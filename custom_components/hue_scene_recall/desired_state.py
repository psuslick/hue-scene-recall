"""Fresh Bridge desired-state resolution for Hue Scene Recall v0.3.2."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import SMART_SCENE_POST_BOUNDARY_SETTLE_SECONDS
from .controller_tracker import ControllerRef

ResolutionStatus = Literal["resolved", "deferred", "unresolved", "no_controller"]

# Only static appearance fields are eligible for automatic recovery. "on" is
# intentionally absent. Unsupported action fields fail closed rather than being
# silently approximated.
SAFE_APPEARANCE_FIELDS = frozenset({"dimming", "color", "color_temperature"})
IGNORED_SCENE_ACTION_FIELDS = frozenset({"on"})


@dataclass(frozen=True, slots=True)
class SmartRecoveryEpisode:
    """Volatile pre-impairment Smart Scene identity shared by sibling lights.

    This object intentionally contains controller/child identity only. It never
    stores brightness, color, color temperature, or power. At recovery time the
    saved child Scene is fetched again from the Bridge and its exact-light action
    is projected fresh.
    """

    controller_rid: str
    timeslot_id: int
    child_scene_rid: str
    captured_at: datetime
    child_activation_mode: str | None = None


@dataclass(frozen=True, slots=True)
class DesiredState:
    status: ResolutionStatus
    reason: str
    controller_kind: str | None = None
    controller_rid: str | None = None
    controller_name: str | None = None
    effective_scene_rid: str | None = None
    effective_scene_name: str | None = None
    payload: dict[str, Any] | None = None
    defer_until: datetime | None = None


def _resource_by_id(
    resources: list[dict[str, Any]], rid: str, resource_type: str | None = None
) -> dict[str, Any] | None:
    for resource in resources:
        if resource.get("id") != rid:
            continue
        if resource_type is not None and resource.get("type") != resource_type:
            continue
        return resource
    return None


def _metadata_name(resource: dict[str, Any]) -> str:
    metadata = resource.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
        return metadata["name"]
    return str(resource.get("id", "unknown"))


def _same_group(resource: dict[str, Any], room_id: str) -> bool:
    group = resource.get("group")
    return isinstance(group, dict) and group.get("rid") == room_id


def _scene_active_mode(scene: dict[str, Any]) -> str | None:
    status = scene.get("status")
    if not isinstance(status, dict):
        return None
    active = status.get("active")
    return active if isinstance(active, str) else None


def _parse_clock(value: str) -> time | None:
    try:
        parts = [int(part) for part in value.split(":")]
    except (AttributeError, TypeError, ValueError):
        return None
    if len(parts) != 3:
        return None
    try:
        return time(parts[0], parts[1], parts[2])
    except ValueError:
        return None


def _bridge_timezone(resources: list[dict[str, Any]]) -> ZoneInfo | None:
    for resource in resources:
        if resource.get("type") != "bridge":
            continue
        time_zone = resource.get("time_zone")
        if not isinstance(time_zone, dict):
            continue
        name = time_zone.get("time_zone")
        if not isinstance(name, str):
            continue
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            return None
    return None


def _today_sunset(resources: list[dict[str, Any]]) -> time | None:
    for resource in resources:
        if resource.get("type") != "geolocation":
            continue
        if resource.get("is_configured") is not True:
            continue
        sun_today = resource.get("sun_today")
        if not isinstance(sun_today, dict) or sun_today.get("day_type") != "normal_day":
            return None
        return _parse_clock(sun_today.get("sunset_time"))
    return None


def _transition_duration(smart: dict[str, Any]) -> tuple[timedelta | None, str | None]:
    duration_ms = smart.get("transition_duration", 60000)
    try:
        return timedelta(milliseconds=max(0, int(duration_ms))), None
    except (TypeError, ValueError):
        return None, "invalid_transition_duration"


def _smart_schedule(
    smart: dict[str, Any], resources: list[dict[str, Any]], now: datetime
) -> tuple[list[tuple[datetime, int, str]], datetime | None, str | None]:
    """Resolve the supported current-day Smart Scene schedule.

    Returns (boundaries, defer_until, error). Each boundary retains the original
    Hue timeslot_id/index even though the array itself is not chronological.
    """
    timezone = _bridge_timezone(resources)
    if timezone is None:
        return [], None, "bridge_timezone_unavailable"
    local_now = now.astimezone(timezone)

    week_timeslots = smart.get("week_timeslots")
    if not isinstance(week_timeslots, list) or len(week_timeslots) != 1:
        return [], None, "unsupported_week_timeslots_shape"
    day_slots = week_timeslots[0]
    if not isinstance(day_slots, dict):
        return [], None, "unsupported_week_timeslots_shape"
    recurrence = day_slots.get("recurrence")
    expected = {
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
    }
    if not isinstance(recurrence, list) or set(recurrence) != expected:
        return [], None, "unsupported_sparse_recurrence"

    raw_timeslots = day_slots.get("timeslots")
    if not isinstance(raw_timeslots, list) or not raw_timeslots:
        return [], None, "missing_timeslots"

    sunset = _today_sunset(resources)
    boundaries: list[tuple[datetime, int, str]] = []
    has_midnight = False
    for index, slot in enumerate(raw_timeslots):
        if not isinstance(slot, dict):
            return [], None, "invalid_timeslot"
        start = slot.get("start_time")
        target = slot.get("target")
        if not isinstance(start, dict) or not isinstance(target, dict):
            return [], None, "invalid_timeslot"
        target_rid = target.get("rid")
        if target.get("rtype") != "scene" or not isinstance(target_rid, str):
            return [], None, "invalid_timeslot_target"

        kind = start.get("kind")
        boundary_time: time | None = None
        if kind == "time":
            clock = start.get("time")
            if not isinstance(clock, dict):
                return [], None, "invalid_fixed_time"
            try:
                boundary_time = time(
                    int(clock.get("hour", 0)),
                    int(clock.get("minute", 0)),
                    int(clock.get("second", 0)),
                )
            except (TypeError, ValueError):
                return [], None, "invalid_fixed_time"
            if boundary_time == time(0, 0, 0):
                has_midnight = True
        elif kind == "sunset":
            if sunset is None:
                return [], None, "sunset_unavailable_or_non_normal_day"
            boundary_time = sunset
        elif kind == "sunrise":
            return [], None, "sunrise_not_supported"
        else:
            return [], None, f"unsupported_timeslot_kind:{kind}"

        boundary = datetime.combine(local_now.date(), boundary_time, tzinfo=timezone)
        boundaries.append((boundary, index, target_rid))

    if not has_midnight:
        return [], None, "explicit_midnight_timeslot_required"

    boundaries.sort(key=lambda item: item[0])
    duration, duration_error = _transition_duration(smart)
    if duration_error or duration is None:
        return [], None, duration_error
    post_settle = timedelta(seconds=SMART_SCENE_POST_BOUNDARY_SETTLE_SECONDS)

    transition_boundaries = [item[0] for item in boundaries]
    # The all-days schedule repeats at midnight. At 23:59, the upcoming
    # next-day 00:00 boundary is not part of today's sorted list, but its
    # transition has already started. Include that one rollover boundary.
    midnight_boundaries = [
        boundary
        for boundary, _, _ in boundaries
        if boundary.timetz().replace(tzinfo=None) == time(0, 0, 0)
    ]
    transition_boundaries.extend(
        boundary + timedelta(days=1) for boundary in midnight_boundaries
    )

    for boundary in transition_boundaries:
        start = boundary - duration
        end = boundary + post_settle
        if start <= local_now <= end:
            return boundaries, end, None

    return boundaries, None, None


def capture_active_smart_episode(
    smart: dict[str, Any],
    *,
    room_id: str,
    now: datetime,
    child_activation_mode: str | None = None,
) -> tuple[SmartRecoveryEpisode | None, str | None]:
    """Capture only the live active Smart controller/timeslot/child identity."""
    if smart.get("type") != "smart_scene" or not _same_group(smart, room_id):
        return None, "active_smart_wrong_group_or_type"
    if smart.get("state") != "active":
        return None, "smart_scene_not_active"
    active = smart.get("active_timeslot")
    if not isinstance(active, dict):
        return None, "active_smart_scene_missing_active_timeslot"
    try:
        active_id = int(active.get("timeslot_id"))
    except (TypeError, ValueError):
        return None, "invalid_active_timeslot_id"

    week_timeslots = smart.get("week_timeslots")
    if not isinstance(week_timeslots, list) or len(week_timeslots) != 1:
        return None, "unsupported_week_timeslots_shape"
    day_slots = week_timeslots[0]
    raw_timeslots = day_slots.get("timeslots") if isinstance(day_slots, dict) else None
    if not isinstance(raw_timeslots, list) or active_id < 0 or active_id >= len(raw_timeslots):
        return None, "active_timeslot_id_missing_from_schedule"
    slot = raw_timeslots[active_id]
    target = slot.get("target") if isinstance(slot, dict) else None
    child_rid = target.get("rid") if isinstance(target, dict) else None
    if not isinstance(target, dict) or target.get("rtype") != "scene" or not isinstance(child_rid, str):
        return None, "invalid_timeslot_target"

    return (
        SmartRecoveryEpisode(
            controller_rid=str(smart.get("id")),
            timeslot_id=active_id,
            child_scene_rid=child_rid,
            captured_at=now,
            child_activation_mode=child_activation_mode,
        ),
        None,
    )


def _episode_child_if_still_valid(
    smart: dict[str, Any],
    resources: list[dict[str, Any]],
    boundaries: list[tuple[datetime, int, str]],
    episode: SmartRecoveryEpisode,
    now: datetime,
) -> tuple[str | None, str | None]:
    """Validate a volatile pre-outage child without guessing across boundaries."""
    if episode.controller_rid != smart.get("id"):
        return None, "smart_episode_controller_mismatch"

    timezone = _bridge_timezone(resources)
    if timezone is None:
        return None, "bridge_timezone_unavailable"
    captured_local = episode.captured_at.astimezone(timezone)
    local_now = now.astimezone(timezone)
    if local_now < captured_local:
        return None, "smart_episode_clock_reversed"
    # Fail closed across midnight/day changes. Hue's post-midnight carry-forward
    # semantics are known to differ from naive calendar schedule selection.
    if captured_local.date() != local_now.date():
        return None, "smart_episode_crossed_day_boundary"

    # If impairment began during a Hue transition, there is no source-faithful
    # single static child to restore later.
    _, captured_defer, captured_error = _smart_schedule(
        smart, resources, episode.captured_at
    )
    if captured_error:
        return None, captured_error
    if captured_defer is not None:
        return None, "smart_episode_captured_during_transition"

    targets_by_id = {index: rid for _, index, rid in boundaries}
    current_target = targets_by_id.get(episode.timeslot_id)
    if current_target is None:
        return None, "smart_episode_timeslot_missing_from_current_schedule"
    if current_target != episode.child_scene_rid:
        return None, "smart_episode_timeslot_target_changed"

    duration, duration_error = _transition_duration(smart)
    if duration_error or duration is None:
        return None, duration_error
    # A child identity captured before an outage is only valid until the next
    # transition begins (live validation: B - transition_duration). Never reuse
    # it across a schedule transition merely because the Smart parent became
    # inactive while bulbs were unpowered.
    for boundary, _, _ in boundaries:
        transition_start = boundary - duration
        if captured_local < transition_start <= local_now:
            return None, "smart_episode_crossed_transition_boundary"

    return episode.child_scene_rid, None


def _project_scene_action(
    scene: dict[str, Any],
    light_id: str,
    *,
    activation_mode: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    # HA's scene `is_dynamic` UI property only means a palette is available;
    # that does not prove the Scene is currently playing dynamically. The Hue
    # Bridge activation mode is what matters. Exact-light recovery cannot join
    # a live dynamic_palette playback without a whole-Scene recall, which is
    # intentionally forbidden.
    mode = activation_mode or _scene_active_mode(scene)
    if mode == "dynamic_palette":
        return None, "dynamic_scene_exact_light_recovery_unsupported"
    if scene.get("auto_dynamic") is True and mode != "static":
        return None, "dynamic_regular_scene_not_supported_for_exact_light_recovery"

    actions = scene.get("actions")
    if not isinstance(actions, list):
        return None, "scene_actions_missing"
    matches: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        target = item.get("target")
        action = item.get("action")
        if (
            isinstance(target, dict)
            and target.get("rtype") == "light"
            and target.get("rid") == light_id
            and isinstance(action, dict)
        ):
            matches.append(action)
    if len(matches) != 1:
        return None, "exact_light_scene_action_missing_or_ambiguous"

    action = matches[0]
    unsupported = set(action) - SAFE_APPEARANCE_FIELDS - IGNORED_SCENE_ACTION_FIELDS
    if unsupported:
        return None, "unsupported_scene_action_fields:" + ",".join(sorted(unsupported))

    payload = {
        key: deepcopy(action[key])
        for key in SAFE_APPEARANCE_FIELDS
        if key in action and action[key] is not None
    }
    if not payload:
        return None, "scene_has_no_supported_appearance_fields"
    # Structural invariant: no caller can accidentally pass Scene power intent.
    assert "on" not in payload
    return payload, None


def resolve_desired_state(
    resources: list[dict[str, Any]],
    *,
    room_id: str,
    light_id: str,
    controller: ControllerRef | None,
    now: datetime,
    smart_episode: SmartRecoveryEpisode | None = None,
) -> DesiredState:
    """Resolve a recovery payload entirely from one fresh Bridge snapshot."""
    if controller is None:
        return DesiredState(status="no_controller", reason="no_recoverable_controller")

    if controller.kind == "scene":
        scene = _resource_by_id(resources, controller.rid, "scene")
        if scene is None or not _same_group(scene, room_id):
            return DesiredState(status="unresolved", reason="stored_scene_missing_or_wrong_group")
        payload, error = _project_scene_action(scene, light_id)
        if error:
            return DesiredState(
                status="unresolved",
                reason=error,
                controller_kind="scene",
                controller_rid=controller.rid,
                controller_name=_metadata_name(scene),
            )
        return DesiredState(
            status="resolved",
            reason="regular_scene",
            controller_kind="scene",
            controller_rid=controller.rid,
            controller_name=_metadata_name(scene),
            effective_scene_rid=scene["id"],
            effective_scene_name=_metadata_name(scene),
            payload=payload,
        )

    smart = _resource_by_id(resources, controller.rid, "smart_scene")
    if smart is None or not _same_group(smart, room_id):
        return DesiredState(status="unresolved", reason="stored_smart_scene_missing_or_wrong_group")

    boundaries, defer_until, error = _smart_schedule(smart, resources, now)
    if error:
        return DesiredState(
            status="unresolved",
            reason=error,
            controller_kind="smart_scene",
            controller_rid=controller.rid,
            controller_name=_metadata_name(smart),
        )
    if defer_until is not None:
        return DesiredState(
            status="deferred",
            reason="smart_scene_transition_window",
            controller_kind="smart_scene",
            controller_rid=controller.rid,
            controller_name=_metadata_name(smart),
            defer_until=defer_until,
        )

    timezone = _bridge_timezone(resources)
    assert timezone is not None
    local_now = now.astimezone(timezone)
    activation_mode: str | None = None
    resolution_reason = "smart_scene_current_child"

    if smart.get("state") == "active":
        active = smart.get("active_timeslot")
        if not isinstance(active, dict):
            return DesiredState(status="unresolved", reason="active_smart_scene_missing_active_timeslot")
        try:
            active_id = int(active.get("timeslot_id"))
        except (TypeError, ValueError):
            return DesiredState(status="unresolved", reason="invalid_active_timeslot_id")

        # Live Bridge validation on 2026-09-13 proved that an ACTIVE Smart
        # Scene can legitimately report the previous weekday after midnight.
        # Hue's live active_timeslot is therefore authoritative while active.
        targets_by_id = {index: rid for _, index, rid in boundaries}
        target_rid = targets_by_id.get(active_id)
        if target_rid is None:
            return DesiredState(status="unresolved", reason="active_timeslot_id_missing_from_schedule")
    else:
        episode_error: str | None = None
        if smart_episode is not None and smart_episode.controller_rid == controller.rid:
            episode_target, episode_error = _episode_child_if_still_valid(
                smart, resources, boundaries, smart_episode, now
            )
            if episode_target is not None and episode_error is None:
                target_rid = episode_target
                activation_mode = smart_episode.child_activation_mode
                resolution_reason = "smart_scene_recovery_episode_child"
            else:
                target_rid = None
        else:
            target_rid = None

        if target_rid is None:
            # The shared outage episode is deliberately not a stale-state cache.
            # If a schedule transition/day boundary invalidated it, re-resolve
            # from current Bridge schedule data. Only fail closed where current
            # inactive Smart semantics themselves remain unverified.
            eligible = [item for item in boundaries if item[0] <= local_now]
            if not eligible:
                return DesiredState(status="unresolved", reason="no_current_timeslot")
            selected_boundary, _, target_rid = max(eligible, key=lambda item: item[0])

            # Live overnight testing showed that active Hue carry-forward
            # semantics do not match a naive 00:00 calendar rollover. Without
            # a current active timeslot or a still-valid pre-outage episode, do
            # not guess during that interval.
            if selected_boundary.timetz().replace(tzinfo=None) == time(0, 0, 0):
                return DesiredState(
                    status="unresolved",
                    reason="inactive_smart_post_midnight_semantics_unverified",
                    controller_kind="smart_scene",
                    controller_rid=controller.rid,
                    controller_name=_metadata_name(smart),
                )
            resolution_reason = (
                f"smart_scene_inactive_schedule_after_episode:{episode_error}"
                if episode_error
                else "smart_scene_inactive_schedule"
            )

    child = _resource_by_id(resources, target_rid, "scene")
    if child is None or not _same_group(child, room_id):
        return DesiredState(status="unresolved", reason="smart_scene_child_missing_or_wrong_group")
    payload, action_error = _project_scene_action(
        child, light_id, activation_mode=activation_mode
    )
    if action_error:
        return DesiredState(
            status="unresolved",
            reason=action_error,
            controller_kind="smart_scene",
            controller_rid=controller.rid,
            controller_name=_metadata_name(smart),
            effective_scene_rid=target_rid,
            effective_scene_name=_metadata_name(child),
        )
    return DesiredState(
        status="resolved",
        reason=resolution_reason,
        controller_kind="smart_scene",
        controller_rid=controller.rid,
        controller_name=_metadata_name(smart),
        effective_scene_rid=target_rid,
        effective_scene_name=_metadata_name(child),
        payload=payload,
    )
