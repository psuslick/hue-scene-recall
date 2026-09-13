"""Pure brightness-cap planning and Smart Scene safety helpers.

This module deliberately has no Home Assistant or aiohue imports so the safety
rules can be regression-tested without constructing a Home Assistant runtime.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

BRIGHTNESS_EPSILON = 0.2
XY_EPSILON = 0.002
MIREK_EPSILON = 1.0


def clamp_cap(value: float) -> float:
    """Clamp a user brightness cap to Hue's 1..100 percentage range."""
    return max(1.0, min(100.0, float(value)))


def cap_payload_brightness(payload: Mapping[str, Any], cap: float) -> dict[str, Any]:
    """Return a copy with brightness reduced to cap, never increased.

    Power is never added. If a caller accidentally supplies an ``on`` field it
    is removed, preserving Hue Scene Recall's automatic-write invariant.
    """
    result = deepcopy(dict(payload))
    result.pop("on", None)
    dimming = result.get("dimming")
    if not isinstance(dimming, dict):
        return result
    brightness = dimming.get("brightness")
    if isinstance(brightness, bool) or not isinstance(brightness, (int, float)):
        return result
    dimming["brightness"] = min(float(brightness), clamp_cap(cap))
    return result



def smart_child_scene_ids(resources: Iterable[Mapping[str, Any]]) -> set[str]:
    """Return regular Scene RIDs referenced by Hue Smart Scene schedules."""
    result: set[str] = set()
    for item in resources:
        if item.get("type") != "smart_scene":
            continue
        week = item.get("week_timeslots")
        if not isinstance(week, list):
            continue
        for day in week:
            timeslots = day.get("timeslots") if isinstance(day, Mapping) else None
            if not isinstance(timeslots, list):
                continue
            for slot in timeslots:
                target = slot.get("target") if isinstance(slot, Mapping) else None
                if not isinstance(target, Mapping) or target.get("rtype") != "scene":
                    continue
                rid = target.get("rid")
                if isinstance(rid, str) and rid:
                    result.add(rid)
    return result


def target_light_id(action: Mapping[str, Any]) -> str | None:
    """Return the physical Hue Light RID targeted by a scene action."""
    target = action.get("target")
    if not isinstance(target, Mapping) or target.get("rtype") != "light":
        return None
    rid = target.get("rid")
    return rid if isinstance(rid, str) and rid else None


def action_brightness(action: Mapping[str, Any]) -> float | None:
    """Return a Scene action's brightness percentage, if present."""
    body = action.get("action")
    if not isinstance(body, Mapping):
        return None
    dimming = body.get("dimming")
    if not isinstance(dimming, Mapping):
        return None
    value = dimming.get("brightness")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _brightness_index(actions: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    values: dict[str, float] = {}
    for action in actions:
        rid = target_light_id(action)
        brightness = action_brightness(action)
        if rid is not None and brightness is not None:
            values[rid] = brightness
    return values


def reconcile_originals(
    actions: Iterable[Mapping[str, Any]],
    originals: Mapping[str, float],
    old_cap: float,
) -> tuple[dict[str, float], bool]:
    """Reconcile persisted originals against the current Bridge Scene.

    While a cap is active, ``min(original, cap)`` is the value HueRecall expects
    to see. A different Bridge value is treated as a user/external saved-scene
    edit and becomes the new original. Missing actions are removed from the
    journal. This is deliberately conservative when the observed value exactly
    equals the cap: that case is indistinguishable from our own overlay, so the
    prior original is retained rather than destroyed.
    """
    observed = _brightness_index(actions)
    cap = clamp_cap(old_cap)
    result = dict(originals)
    changed = False
    for light_id, original in tuple(result.items()):
        current = observed.get(light_id)
        if current is None:
            result.pop(light_id, None)
            changed = True
            continue
        expected = min(float(original), cap)
        if abs(current - expected) > BRIGHTNESS_EPSILON:
            result[light_id] = current
            changed = True
    return result, changed


@dataclass(frozen=True, slots=True)
class SceneOverlayPlan:
    """A reversible brightness-only mutation for one Hue regular Scene."""

    actions: list[dict[str, Any]]
    originals: dict[str, float]
    changed_targets: dict[str, float]
    removable_after_verify: frozenset[str]

    @property
    def changed(self) -> bool:
        return bool(self.changed_targets)


def plan_scene_overlay(
    actions: list[dict[str, Any]],
    *,
    physical_light_ids: set[str],
    cap: float,
    originals: Mapping[str, float],
) -> SceneOverlayPlan:
    """Plan a brightness-only Scene overlay for physical Hue lights.

    Only existing per-light dimming values can be changed. No action is added,
    no power field is touched, no grouped_light target is accepted, and no
    brightness is ever raised above the saved original.
    """
    cap_value = clamp_cap(cap)
    new_actions = deepcopy(actions)
    journal = dict(originals)
    changed_targets: dict[str, float] = {}
    removable: set[str] = set()

    for action in new_actions:
        light_id = target_light_id(action)
        if light_id is None or light_id not in physical_light_ids:
            continue
        current = action_brightness(action)
        if current is None:
            continue

        original = journal.get(light_id)
        if original is None:
            if cap_value >= 100.0 or current <= cap_value + BRIGHTNESS_EPSILON:
                continue
            original = current
            journal[light_id] = original

        desired = min(float(original), cap_value)
        if abs(current - desired) > BRIGHTNESS_EPSILON:
            body = action.get("action")
            if not isinstance(body, dict):
                continue
            dimming = body.get("dimming")
            if not isinstance(dimming, dict):
                continue
            dimming["brightness"] = desired
            changed_targets[light_id] = desired

        # Once the original no longer exceeds the cap, there is no active
        # overlay to reverse. Removal happens only after the Bridge verifies the
        # desired value, so a failed restore never loses the original.
        if float(original) <= cap_value + BRIGHTNESS_EPSILON:
            removable.add(light_id)

    return SceneOverlayPlan(
        actions=new_actions,
        originals=journal,
        changed_targets=changed_targets,
        removable_after_verify=frozenset(removable),
    )


def _xy_matches(current: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    current_xy = current.get("xy")
    expected_xy = expected.get("xy")
    if not isinstance(current_xy, Mapping) or not isinstance(expected_xy, Mapping):
        return False
    for axis in ("x", "y"):
        have = current_xy.get(axis)
        want = expected_xy.get(axis)
        if not isinstance(have, (int, float)) or not isinstance(want, (int, float)):
            return False
        if abs(float(have) - float(want)) > XY_EPSILON:
            return False
    return True


def non_brightness_scene_state_matches(
    light: Mapping[str, Any], scene_action: Mapping[str, Any]
) -> bool:
    """Prove a light already matches a Scene except for brightness.

    This is intentionally fail-closed. It is used only before the narrowly
    allowed operation of reapplying the *same currently active* Smart Scene.
    Unsupported appearance instructions make the proof fail rather than being
    guessed around.
    """
    body = scene_action.get("action")
    if not isinstance(body, Mapping):
        return False

    supported = {
        "on",
        "dimming",
        "color",
        "color_temperature",
        "gradient",
        "effects",
        "effects_v2",
    }
    if any(key not in supported for key in body):
        return False

    if "on" in body:
        expected_on = body.get("on")
        current_on = light.get("on")
        if isinstance(expected_on, Mapping):
            expected_on = expected_on.get("on")
        if isinstance(current_on, Mapping):
            current_on = current_on.get("on")
        if not isinstance(expected_on, bool) or current_on is not expected_on:
            return False

    if "color" in body:
        expected = body.get("color")
        current = light.get("color")
        if not isinstance(expected, Mapping) or not isinstance(current, Mapping):
            return False
        if not _xy_matches(current, expected):
            return False

    if "color_temperature" in body:
        expected = body.get("color_temperature")
        current = light.get("color_temperature")
        if not isinstance(expected, Mapping) or not isinstance(current, Mapping):
            return False
        want = expected.get("mirek")
        have = current.get("mirek")
        if not isinstance(want, (int, float)) or not isinstance(have, (int, float)):
            return False
        if abs(float(have) - float(want)) > MIREK_EPSILON:
            return False
        if current.get("mirek_valid") is False:
            return False

    # Gradient/effect structures are not safe to approximate. Require exact
    # equality for the specific fields the Scene carries.
    for key in ("gradient", "effects", "effects_v2"):
        if key in body and light.get(key) != body.get(key):
            return False

    return True


def brightness_above_cap(light: Mapping[str, Any], cap: float) -> bool:
    """Return whether a current physical-light brightness exceeds the cap."""
    dimming = light.get("dimming")
    if not isinstance(dimming, Mapping):
        return False
    value = dimming.get("brightness")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return float(value) > clamp_cap(cap) + BRIGHTNESS_EPSILON
