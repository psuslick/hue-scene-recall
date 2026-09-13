"""House-wide, power-neutral Hue maximum-brightness controller.

The cap is intentionally implemented beside the recovery state machine rather
than as a Home Assistant light automation. Physical Hue V2 ``light`` resources
are the only live-light targets. Hue regular Scene brightness is overlaid
reversibly so Hue-native Smart Scenes remain the scheduler instead of being
cancelled by routine per-bulb dimming.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
import logging
from typing import Any

from aiohue.util import dataclass_to_dict
from aiohue.v2.controllers.events import EventType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .brightness_cap_logic import (
    BRIGHTNESS_EPSILON,
    action_brightness,
    brightness_above_cap,
    cap_payload_brightness,
    clamp_cap,
    non_brightness_scene_state_matches,
    plan_scene_overlay,
    reconcile_originals,
    smart_child_scene_ids,
    target_light_id,
)
from .compare import appearance_matches
from .const import (
    BRIGHTNESS_CAP_DEFAULT,
    BRIGHTNESS_CAP_INTERNAL_WRITE_SECONDS,
    BRIGHTNESS_CAP_LIGHT_SETTLE_SECONDS,
    BRIGHTNESS_CAP_MAX_WRITE_ATTEMPTS,
    BRIGHTNESS_CAP_SCENE_SETTLE_SECONDS,
    BRIGHTNESS_CAP_SMART_REAPPLY_GUARD_MARGIN_SECONDS,
    BRIGHTNESS_CAP_STORAGE_KEY_PREFIX,
    BRIGHTNESS_CAP_STORAGE_VERSION,
    BRIGHTNESS_CAP_VERIFY_TIMEOUT_SECONDS,
)
from .controller_tracker import make_controller
from .desired_state import capture_active_smart_episode, resolve_desired_state

_LOGGER = logging.getLogger(__name__)
Listener = Callable[[], None]


def _now() -> datetime:
    return datetime.now().astimezone()


def _resource_by_id(
    resources: list[dict[str, Any]], rid: str, resource_type: str
) -> dict[str, Any] | None:
    for item in resources:
        if item.get("id") == rid and item.get("type") == resource_type:
            return item
    return None


def _scene_brightness_map(scene: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    actions = scene.get("actions")
    if not isinstance(actions, list):
        return result
    for action in actions:
        if not isinstance(action, dict):
            continue
        rid = target_light_id(action)
        brightness = action_brightness(action)
        if rid is not None and brightness is not None:
            result[rid] = brightness
    return result


class HueBrightnessCapController:
    """Coordinate reversible Scene overlays and safe exact-light capping."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, manager: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.manager = manager
        self.api = manager.api
        self.bridge = manager.bridge

        self.maximum_brightness = BRIGHTNESS_CAP_DEFAULT
        self._originals: dict[str, dict[str, float]] = {}
        self._listeners: list[Listener] = []
        self._unsubs: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._scene_handle: asyncio.TimerHandle | None = None
        self._light_handles: dict[str, asyncio.TimerHandle] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass,
            BRIGHTNESS_CAP_STORAGE_VERSION,
            f"{BRIGHTNESS_CAP_STORAGE_KEY_PREFIX}.{entry.entry_id}",
            atomic_writes=True,
        )

        self.last_result = "not_run"
        self.last_error: str | None = None
        self.last_applied_at: str | None = None
        self.last_smart_reapply_at: str | None = None
        self.scene_overlay_failures = 0
        self.direct_light_failures = 0
        self._store_dirty = False

    async def async_setup(self) -> None:
        """Restore the cap journal, reconcile Bridge Scenes, then subscribe."""
        stored = await self._store.async_load() or {}
        raw_cap = stored.get("maximum_brightness", BRIGHTNESS_CAP_DEFAULT)
        try:
            self.maximum_brightness = clamp_cap(float(raw_cap))
        except (TypeError, ValueError):
            self.maximum_brightness = BRIGHTNESS_CAP_DEFAULT

        raw_originals = stored.get("scene_original_brightness", {})
        if isinstance(raw_originals, dict):
            for scene_id, values in raw_originals.items():
                if not isinstance(scene_id, str) or not isinstance(values, dict):
                    continue
                clean: dict[str, float] = {}
                for light_id, brightness in values.items():
                    if (
                        isinstance(light_id, str)
                        and not isinstance(brightness, bool)
                        and isinstance(brightness, (int, float))
                    ):
                        clean[light_id] = float(brightness)
                if clean:
                    self._originals[scene_id] = clean

        # Reconcile before subscribing so our startup repair cannot be mistaken
        # for a user Scene edit by this coordinator. A transient Bridge read
        # failure must not take the core HueRecall recovery integration down; a
        # bounded retry is scheduled after subscriptions are established.
        startup_failed = False
        async with self._lock:
            try:
                await self._async_reconcile_scene_overlays(
                    old_cap=self.maximum_brightness,
                    reason="startup",
                )
                if self.maximum_brightness < 100.0:
                    await self._async_enforce_current_lights(reason="startup")
            except Exception as err:  # noqa: BLE001 - transient Hue availability
                startup_failed = True
                self.last_error = f"startup_reconcile: {err}"
                self.last_result = "startup_reconcile_deferred"
                _LOGGER.warning("Hue brightness-cap startup reconciliation deferred: %s", err)

        self._unsubs.append(
            self.api.scenes.subscribe(
                self._on_scene_event,
                event_filter=(
                    EventType.RESOURCE_ADDED,
                    EventType.RESOURCE_UPDATED,
                    EventType.RESOURCE_DELETED,
                ),
            )
        )
        self._unsubs.append(
            self.api.lights.subscribe(
                self._on_light_event,
                event_filter=(EventType.RESOURCE_UPDATED, EventType.RESOURCE_ADDED),
            )
        )
        if startup_failed:
            self._scene_handle = self.hass.loop.call_later(5.0, self._start_scene_reconcile)
        self._notify()

    async def async_shutdown(self) -> None:
        """Cancel pending work and persist the small reversible-overlay journal."""
        if self._scene_handle is not None:
            self._scene_handle.cancel()
            self._scene_handle = None
        for handle in self._light_handles.values():
            handle.cancel()
        self._light_handles.clear()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._persist()

    @callback
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def _serialize(self) -> dict[str, Any]:
        return {
            "maximum_brightness": self.maximum_brightness,
            "scene_original_brightness": self._originals,
        }

    async def _persist(self) -> None:
        # This Store changes only when the user changes the cap, a saved Hue
        # Scene is edited while an overlay is active, or an overlay is restored.
        # It is not part of the high-frequency light-event path.
        if not self._store_dirty:
            return
        await self._store.async_save(self._serialize())
        self._store_dirty = False

    async def async_set_maximum_brightness(self, value: float) -> None:
        """Apply a new cap without ever increasing current bulb brightness."""
        new_cap = clamp_cap(value)
        async with self._lock:
            old_cap = self.maximum_brightness
            if abs(new_cap - old_cap) <= BRIGHTNESS_EPSILON:
                return

            lowering = new_cap < old_cap
            self.maximum_brightness = new_cap
            self._store_dirty = True
            try:
                # Scene reconciliation captures/persists any new originals and
                # the new cap together, before the first Bridge mutation.
                await self._async_reconcile_scene_overlays(
                    old_cap=old_cap,
                    reason="cap_changed",
                    persist_intent=True,
                )
            except Exception:
                # No Scene write occurs before the persist point inside the
                # reconciler, so a preflight failure can safely restore the old
                # in-memory cap and report the failed slider change.
                self.maximum_brightness = old_cap
                self._store_dirty = False
                raise
            if lowering and new_cap < 100.0:
                # The cap/Scene-overlay Store write above is the commit point.
                # A later live-light enforcement problem must not make the
                # caller think the cap rolled back while saved Scenes are
                # already overlaid. Keep the committed cap, surface the partial
                # result diagnostically, and let later Hue events retry.
                try:
                    await self._async_enforce_current_lights(reason="cap_lowered")
                except Exception as err:  # noqa: BLE001 - committed cap remains authoritative
                    self.last_error = f"live_enforcement_after_commit: {err}"
                    self.last_result = "cap_committed_live_enforcement_deferred"
                    _LOGGER.warning(
                        "Hue brightness cap committed, but immediate live enforcement deferred: %s",
                        err,
                    )

            self.last_applied_at = _now().isoformat()
            self._notify()

    def cap_desired_state(self, desired: Any) -> Any:
        """Clamp a resolved recovery payload without changing its controller metadata."""
        if desired.payload is None or self.maximum_brightness >= 100.0:
            return desired
        payload = cap_payload_brightness(desired.payload, self.maximum_brightness)
        if payload == desired.payload:
            return desired
        return replace(desired, payload=payload)

    def diagnostic_attributes(self) -> dict[str, Any]:
        tracked = sum(len(values) for values in self._originals.values())
        return {
            "scope": "physical_hue_v2_lights_house_wide",
            "100_percent": "no_cap",
            "power_writes": "never",
            "grouped_light_writes": "never",
            "smart_scene_policy": "reversible_scene_overlay_guarded_same_controller_reapply",
            "tracked_scene_originals": tracked,
            "tracked_scenes": len(self._originals),
            "last_result": self.last_result,
            "last_error": self.last_error,
            "last_applied_at": self.last_applied_at,
            "last_smart_reapply_at": self.last_smart_reapply_at,
            "scene_overlay_failures": self.scene_overlay_failures,
            "direct_light_failures": self.direct_light_failures,
        }

    async def _fresh_resources(self) -> list[dict[str, Any]]:
        resources = await self.api.request("get", "clip/v2/resource")
        if not isinstance(resources, list):
            raise TypeError("Hue full-state query did not return a resource list")
        return [item for item in resources if isinstance(item, dict)]

    async def _fresh_resource(self, resource_type: str, rid: str) -> dict[str, Any]:
        items = await self.api.request("get", f"clip/v2/resource/{resource_type}/{rid}")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise TypeError(f"Hue {resource_type} query did not return one resource")
        return items[0]

    @callback
    def _on_scene_event(self, event_type: EventType, scene: Any) -> None:
        if self._scene_handle is not None:
            self._scene_handle.cancel()
        self._scene_handle = self.hass.loop.call_later(
            BRIGHTNESS_CAP_SCENE_SETTLE_SECONDS,
    BRIGHTNESS_CAP_SMART_REAPPLY_GUARD_MARGIN_SECONDS,
            self._start_scene_reconcile,
        )

    @callback
    def _start_scene_reconcile(self) -> None:
        self._scene_handle = None
        self.hass.async_create_task(
            self._async_scene_reconcile_task(),
            name=f"{self.entry.domain}_brightness_cap_scene_reconcile",
        )

    async def _async_scene_reconcile_task(self) -> None:
        async with self._lock:
            try:
                await self._async_reconcile_scene_overlays(
                    old_cap=self.maximum_brightness,
                    reason="scene_changed",
                )
                if self.maximum_brightness < 100.0:
                    await self._async_enforce_current_lights(reason="scene_changed")
            except Exception as err:  # noqa: BLE001 - diagnostics; next event retries
                self.last_error = f"scene_reconcile: {err}"
                self.last_result = "scene_reconcile_failed"
                _LOGGER.warning("Hue brightness-cap Scene reconciliation failed: %s", err)
            self._notify()

    @callback
    def _on_light_event(self, event_type: EventType, hue_light: Any) -> None:
        if self.maximum_brightness >= 100.0:
            return
        light_id = getattr(hue_light, "id", None)
        if not isinstance(light_id, str):
            return
        try:
            raw = dataclass_to_dict(hue_light, skip_none=True)
        except (TypeError, ValueError):
            return
        if not brightness_above_cap(raw, self.maximum_brightness):
            return

        old = self._light_handles.pop(light_id, None)
        if old is not None:
            old.cancel()
        self._light_handles[light_id] = self.hass.loop.call_later(
            BRIGHTNESS_CAP_LIGHT_SETTLE_SECONDS,
            self._start_light_enforcement,
            light_id,
        )

    @callback
    def _start_light_enforcement(self, light_id: str) -> None:
        self._light_handles.pop(light_id, None)
        self.hass.async_create_task(
            self._async_light_enforcement_task(light_id),
            name=f"{self.entry.domain}_brightness_cap_{light_id}",
        )

    async def _async_light_enforcement_task(self, light_id: str) -> None:
        async with self._lock:
            if self.maximum_brightness >= 100.0:
                return
            try:
                resources = await self._fresh_resources()
                light = _resource_by_id(resources, light_id, "light")
                if light is None or not brightness_above_cap(light, self.maximum_brightness):
                    return
                room_id = self._room_id_for_light(light_id)
                active_smart = self._active_smart(resources, room_id) if room_id else []
                if active_smart:
                    if len(active_smart) == 1:
                        await self._async_guarded_reapply_active_smart(room_id, resources)
                    else:
                        self.last_result = "smart_scene_ambiguous_fail_closed"
                    return
                await self._async_cap_exact_light(light_id)
            except Exception as err:  # noqa: BLE001 - bounded event-driven retry occurs later
                self.last_error = f"light_enforcement: {err}"
                self.last_result = "light_enforcement_failed"
                _LOGGER.warning("Hue brightness-cap light enforcement failed: %s", err)
            finally:
                self._notify()

    async def _async_reconcile_scene_overlays(
        self, *, old_cap: float, reason: str, persist_intent: bool = False
    ) -> None:
        """Reconcile Hue Smart Scene child overlays, then apply or restore them."""
        resources = await self._fresh_resources()
        physical_light_ids = {
            item["id"]
            for item in resources
            if item.get("type") == "light" and isinstance(item.get("id"), str)
        }
        scenes = {
            item["id"]: item
            for item in resources
            if item.get("type") == "scene" and isinstance(item.get("id"), str)
        }
        child_scene_ids = smart_child_scene_ids(resources)

        staged: dict[str, dict[str, float]] = {
            scene_id: dict(values) for scene_id, values in self._originals.items()
        }
        plans: dict[str, Any] = {}
        journal_changed = False

        # Deleted Scenes cannot be restored and must not leave stale journal
        # records forever. Previously-overlaid Scenes removed from all Smart
        # schedules are still processed below with an effective cap of 100 so
        # their original brightness is restored before the journal is removed.
        for scene_id in tuple(staged):
            if scene_id not in scenes:
                staged.pop(scene_id, None)
                journal_changed = True

        process_ids = child_scene_ids | set(staged)
        for scene_id in process_ids:
            scene = scenes.get(scene_id)
            if scene is None:
                continue
            actions = scene.get("actions")
            if not isinstance(actions, list):
                continue
            clean_actions = [item for item in actions if isinstance(item, dict)]
            existing = staged.get(scene_id, {})
            reconciled, changed = reconcile_originals(clean_actions, existing, old_cap)
            journal_changed = journal_changed or changed

            # Only current Smart Scene children stay overlaid. If Hue edits a
            # Smart Scene schedule and removes a child, restore that old child's
            # saved brightness immediately instead of leaving hidden mutations.
            scene_cap = self.maximum_brightness if scene_id in child_scene_ids else 100.0
            plan = plan_scene_overlay(
                clean_actions,
                physical_light_ids=physical_light_ids,
                cap=scene_cap,
                originals=reconciled,
            )
            if plan.originals != existing:
                journal_changed = True
            if plan.originals:
                staged[scene_id] = dict(plan.originals)
            else:
                staged.pop(scene_id, None)
            plans[scene_id] = plan

        # Persist originals BEFORE the first Scene mutation. This is the key
        # crash-safety property that lets 100% restore true saved Hue values.
        if staged != self._originals or journal_changed or persist_intent:
            self._originals = staged
            self._store_dirty = True
            await self._persist()

        verified_prunes: dict[str, set[str]] = {}
        failures = 0
        for scene_id, plan in plans.items():
            if not plan.changed:
                if plan.removable_after_verify:
                    verified_prunes[scene_id] = set(plan.removable_after_verify)
                continue

            ok = await self._async_write_scene_actions(
                scene_id,
                plan.actions,
                plan.changed_targets,
            )
            if ok:
                if plan.removable_after_verify:
                    verified_prunes[scene_id] = set(plan.removable_after_verify)
            else:
                failures += 1

        pruned = False
        for scene_id, light_ids in verified_prunes.items():
            values = self._originals.get(scene_id)
            if values is None:
                continue
            for light_id in light_ids:
                if light_id in values:
                    values.pop(light_id, None)
                    pruned = True
            if not values:
                self._originals.pop(scene_id, None)

        if pruned:
            self._store_dirty = True
            try:
                await self._persist()
            except Exception as err:  # noqa: BLE001 - persisted pre-write journal remains safer
                # The prior durable journal intentionally still contains the
                # restored originals. Retaining extra reversible state is safer
                # than reporting a rollback after verified Bridge restoration.
                self.last_error = f"overlay_prune_persist: {err}"
                _LOGGER.warning(
                    "Hue brightness-cap restored Scene values but could not prune the durable journal: %s",
                    err,
                )

        self.scene_overlay_failures += failures
        self.last_error = None if failures == 0 else f"{failures} scene overlay write(s) unverified"
        self.last_result = (
            f"scene_overlay_ok:{reason}" if failures == 0 else f"scene_overlay_partial:{reason}"
        )

    async def _async_write_scene_actions(
        self,
        scene_id: str,
        actions: list[dict[str, Any]],
        expected: dict[str, float],
    ) -> bool:
        """Write one regular Scene and verify exactly the changed brightnesses."""
        last_error: Exception | None = None
        for attempt in range(BRIGHTNESS_CAP_MAX_WRITE_ATTEMPTS):
            write_error: Exception | None = None
            try:
                await self.api.request(
                    "put",
                    f"clip/v2/resource/scene/{scene_id}",
                    json={"actions": actions},
                )
            except Exception as err:  # noqa: BLE001 - Hue 207 can be ambiguous
                write_error = err
                last_error = err

            try:
                current = await self._fresh_resource("scene", scene_id)
                observed = _scene_brightness_map(current)
                verified = all(
                    light_id in observed
                    and abs(observed[light_id] - brightness) <= BRIGHTNESS_EPSILON
                    for light_id, brightness in expected.items()
                )
            except Exception as err:  # noqa: BLE001 - bounded retry below
                if write_error is None:
                    last_error = err
                verified = False

            if verified:
                return True
            if attempt + 1 < BRIGHTNESS_CAP_MAX_WRITE_ATTEMPTS:
                await asyncio.sleep(0.5)

        if last_error is not None:
            _LOGGER.warning(
                "Hue brightness-cap Scene %s write was not verified: %s",
                scene_id,
                last_error,
            )
        return False

    async def _async_enforce_current_lights(self, *, reason: str) -> None:
        resources = await self._fresh_resources()
        smart_rooms: set[str] = set()
        for item in resources:
            if item.get("type") != "light" or not brightness_above_cap(
                item, self.maximum_brightness
            ):
                continue
            light_id = item.get("id")
            if not isinstance(light_id, str):
                continue
            room_id = self._room_id_for_light(light_id)
            active_smart = self._active_smart(resources, room_id) if room_id else []
            if active_smart:
                if len(active_smart) == 1 and room_id is not None:
                    smart_rooms.add(room_id)
                continue
            await self._async_cap_exact_light(light_id)

        for room_id in sorted(smart_rooms):
            fresh = await self._fresh_resources()
            await self._async_guarded_reapply_active_smart(room_id, fresh)
        self.last_result = f"live_enforcement_complete:{reason}"

    def _room_id_for_light(self, light_id: str) -> str | None:
        state = self.manager.lights.get(light_id)
        return state.room_id if state is not None else None

    @staticmethod
    def _active_smart(
        resources: list[dict[str, Any]], room_id: str | None
    ) -> list[dict[str, Any]]:
        if room_id is None:
            return []
        result: list[dict[str, Any]] = []
        for item in resources:
            if item.get("type") != "smart_scene" or item.get("state") != "active":
                continue
            group = item.get("group")
            if isinstance(group, dict) and group.get("rid") == room_id:
                result.append(item)
        return result

    async def _async_cap_exact_light(self, light_id: str) -> bool:
        """Reduce one exact physical Hue light; never turn it on or brighten it."""
        for attempt in range(BRIGHTNESS_CAP_MAX_WRITE_ATTEMPTS):
            current = await self._fresh_resource("light", light_id)
            if not brightness_above_cap(current, self.maximum_brightness):
                return True

            # A Smart Scene may have become active after the triggering event.
            # Direct dimming can deactivate it, so re-check immediately before
            # the exact-light PUT and fail closed if one now owns the room.
            room_id = self._room_id_for_light(light_id)
            if room_id is not None:
                resources = await self._fresh_resources()
                if self._active_smart(resources, room_id):
                    return False

            payload = {"dimming": {"brightness": self.maximum_brightness}}
            recovery = self.manager.lights.get(light_id)
            if recovery is not None:
                guard_until = _now() + timedelta(
                    seconds=BRIGHTNESS_CAP_INTERNAL_WRITE_SECONDS
                )
                recovery.internal_expected_payload = payload
                recovery.internal_expected_until = guard_until
                recovery.manual_classification_guard_until = guard_until

            event = asyncio.Event()

            @callback
            def _on_exact_light(event_type: EventType, resource: Any) -> None:
                if event_type != EventType.RESOURCE_UPDATED:
                    return
                try:
                    raw = dataclass_to_dict(resource, skip_none=True)
                except (TypeError, ValueError):
                    return
                if appearance_matches(raw, payload):
                    event.set()

            unsub = self.api.lights.subscribe(
                _on_exact_light,
                id_filter=(light_id,),
                event_filter=(EventType.RESOURCE_UPDATED,),
            )
            write_error: Exception | None = None
            try:
                try:
                    await self.api.request(
                        "put",
                        f"clip/v2/resource/light/{light_id}",
                        json=payload,
                    )
                except Exception as err:  # noqa: BLE001 - verify ambiguous Hue result
                    write_error = err

                try:
                    async with asyncio.timeout(BRIGHTNESS_CAP_VERIFY_TIMEOUT_SECONDS):
                        await event.wait()
                    return True
                except TimeoutError:
                    pass

                fresh = await self._fresh_resource("light", light_id)
                if appearance_matches(fresh, payload):
                    return True
            finally:
                unsub()

            if attempt + 1 < BRIGHTNESS_CAP_MAX_WRITE_ATTEMPTS:
                await asyncio.sleep(0.5)
                continue
            self.direct_light_failures += 1
            if write_error is not None:
                self.last_error = f"exact_light_{light_id}: {write_error}"

            if recovery is not None and recovery.internal_expected_payload == payload:
                recovery.internal_expected_payload = None
                recovery.internal_expected_until = None
        return False

    async def _async_guarded_reapply_active_smart(
        self, room_id: str, resources: list[dict[str, Any]]
    ) -> bool:
        """Reapply only the same active Smart Scene after a strict safety proof.

        This is intentionally the sole automatic Smart Scene recall in the cap
        subsystem. It is never used by fault recovery. The proof requires the
        room to be healthy and every affected light's power/non-brightness
        appearance to match the current child Scene. Brightness may differ.
        """
        active = self._active_smart(resources, room_id)
        if len(active) != 1:
            return False
        smart = active[0]
        smart_id = smart.get("id")
        if not isinstance(smart_id, str):
            return False

        episode, error = capture_active_smart_episode(
            smart,
            room_id=room_id,
            now=_now(),
        )
        if episode is None or error is not None:
            return False
        child = _resource_by_id(resources, episode.child_scene_rid, "scene")
        if child is None:
            return False
        actions = child.get("actions")
        if not isinstance(actions, list) or not actions:
            return False

        controller = make_controller(
            "smart_scene",
            smart_id,
            reason="brightness_cap_same_active_smart_guard",
        )
        expected_payloads: dict[str, dict[str, Any]] = {}
        needs_reapply = False

        for action in actions:
            if not isinstance(action, dict):
                return False
            light_id = target_light_id(action)
            if light_id is None:
                return False
            current = _resource_by_id(resources, light_id, "light")
            recovery = self.manager.lights.get(light_id)
            if current is None or recovery is None:
                return False
            if not recovery.ha_available or recovery.connectivity_issue_pending:
                return False
            if not non_brightness_scene_state_matches(current, action):
                return False

            desired = resolve_desired_state(
                resources,
                room_id=room_id,
                light_id=light_id,
                controller=controller,
                now=_now(),
                smart_episode=episode,
            )
            if (
                desired.status != "resolved"
                or desired.effective_scene_rid != episode.child_scene_rid
                or desired.payload is None
            ):
                # Includes the Smart Scene transition-defer window. Do not
                # restart Hue's scheduler while its current state is ambiguous.
                return False
            expected_payloads[light_id] = cap_payload_brightness(
                desired.payload,
                self.maximum_brightness,
            )
            if brightness_above_cap(current, self.maximum_brightness):
                needs_reapply = True

        if not needs_reapply:
            return True

        # One final Bridge read closes most of the active-timeslot race. A
        # controller/timeslot change between proof and recall causes fail-closed.
        fresh_smart = await self._fresh_resource("smart_scene", smart_id)
        if fresh_smart.get("state") != "active":
            return False
        before_slot = smart.get("active_timeslot")
        after_slot = fresh_smart.get("active_timeslot")
        if before_slot != after_slot:
            return False

        # Recalling the same Smart Scene can emit intermediate transition
        # updates for up to its configured transition duration. Those events
        # are generated by this cap operation, not by an unsaved/manual user
        # adjustment, so guard HueRecall controller classification for the
        # bounded transition window. This uses the manager's existing runtime-
        # only manual-classification guard; nothing about appearance is
        # persisted as recovery authority.
        raw_duration = fresh_smart.get("transition_duration", 60000)
        try:
            transition_seconds = max(0.0, float(raw_duration) / 1000.0)
        except (TypeError, ValueError):
            transition_seconds = 60.0
        guard_until = _now() + timedelta(
            seconds=transition_seconds
            + BRIGHTNESS_CAP_SMART_REAPPLY_GUARD_MARGIN_SECONDS
        )

        for light_id, payload in expected_payloads.items():
            recovery = self.manager.lights.get(light_id)
            if recovery is not None:
                recovery.internal_expected_payload = payload
                recovery.internal_expected_until = _now() + timedelta(
                    seconds=BRIGHTNESS_CAP_INTERNAL_WRITE_SECONDS
                )
                recovery.manual_classification_guard_until = guard_until

        try:
            # Deliberately reapply the SAME controller that is already active.
            # No regular Scene, grouped_light, or alternate Smart Scene is
            # recalled here. The strict guard above proves power and all
            # supported non-brightness appearance already match its current
            # child Scene.
            await self.api.scenes.smart_scene.recall(smart_id)
        except Exception as err:  # noqa: BLE001 - overlay remains protective
            self.last_error = f"smart_scene_reapply_{smart_id}: {err}"
            return False

        self.last_smart_reapply_at = _now().isoformat()
        self.last_result = "same_active_smart_scene_reapplied"
        return True
