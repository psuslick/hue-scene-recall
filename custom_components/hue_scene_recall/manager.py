"""Hue-authoritative, per-light scene recovery for Hue Scene Recall."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from aiohue.util import dataclass_to_dict
from aiohue.v2.controllers.events import EventType
from aiohue.v2.models.scene import Scene as HueScene
from aiohue.v2.models.smart_scene import SmartScene as HueSmartScene, SmartSceneState
from aiohue.v2.models.zigbee_connectivity import (
    ConnectivityServiceStatus,
    ZigbeeConnectivity,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .compare import appearance_fingerprint, appearance_matches
from .const import (
    CONTROLLER_RECONCILE_SECONDS,
    MAX_RECOVERY_ATTEMPTS,
    RECALL_LABEL_NAME,
    RECOVERY_RETRY_DELAY_SECONDS,
    RECOVERY_SETTLE_SECONDS,
    STATE_UNAVAILABLE_VALUES,
    STORAGE_KEY_PREFIX,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
    VERIFY_EVENT_TIMEOUT_SECONDS,
)
from .controller_tracker import ControllerRef, make_controller
from .desired_state import DesiredState, resolve_desired_state
from .recovery_logic import (
    RecoveryReason,
    impairment_reasons,
    next_connectivity_issue_pending,
)

_LOGGER = logging.getLogger(__name__)
Listener = Callable[[], None]


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _connectivity_value(status: Any) -> str:
    value = _enum_value(status)
    return str(value) if value is not None else "unknown"


def _scene_active_value(scene: Any) -> str:
    status = getattr(scene, "status", None)
    return str(_enum_value(getattr(status, "active", "inactive")))


def _scene_last_recall(scene: Any) -> datetime | None:
    status = getattr(scene, "status", None)
    value = getattr(status, "last_recall", None)
    return value if isinstance(value, datetime) else None


@dataclass(slots=True)
class LightRecoveryState:
    """Runtime recovery state for one exact Hue Light RID."""

    entity_id: str
    hue_light_id: str
    room_id: str
    connectivity_resource_id: str | None = None
    connectivity_status: str = "unknown"
    connectivity_issue_pending: bool = False
    ha_available: bool = False
    armed: bool = False
    trigger_reasons: set[RecoveryReason] = field(default_factory=set)
    generation: int = 0
    settle_handle: asyncio.TimerHandle | None = field(default=None, repr=False)
    defer_handle: asyncio.TimerHandle | None = field(default=None, repr=False)
    task: asyncio.Task[Any] | None = field(default=None, repr=False)
    internal_expected_payload: dict[str, Any] | None = None
    internal_expected_until: datetime | None = None
    status: str = "healthy"

    last_connectivity_issue_at: datetime | None = None
    last_connectivity_recovered_at: datetime | None = None
    last_ha_unavailable_at: datetime | None = None
    last_ha_available_at: datetime | None = None
    last_recovery_trigger_at: datetime | None = None
    last_recovery_at: datetime | None = None
    last_result: str | None = None
    last_reason: str | None = None
    last_controller_kind: str | None = None
    last_controller_rid: str | None = None
    last_controller_name: str | None = None
    last_effective_scene_rid: str | None = None
    last_effective_scene_name: str | None = None
    connectivity_issue_count: int = 0
    ha_unavailable_count: int = 0


@dataclass(slots=True)
class RoomRecallState:
    room_id: str
    room_name: str
    light_entity_ids: tuple[str, ...] = ()
    hue_light_ids: tuple[str, ...] = ()
    total_hue_lights: int = 0
    enrolled: bool = False
    controller: ControllerRef | None = None
    controller_reconcile_handle: asyncio.TimerHandle | None = field(
        default=None, repr=False
    )
    fresh_regular_candidates: set[str] = field(default_factory=set)
    material_change_pending: bool = False
    last_controller_change_at: datetime | None = None
    last_controller_change_reason: str | None = None


class HueSceneRecallManager:
    """Coordinate controller identity and exact-light fault recovery."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        hue_entry: ConfigEntry,
        bridge: Any,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.hue_entry = hue_entry
        self.bridge = bridge
        self.api = bridge.api

        self.master_enabled = True
        self.label_id: str | None = None
        self.rooms: dict[str, RoomRecallState] = {}
        self.lights: dict[str, LightRecoveryState] = {}
        self._stored_controllers: dict[str, ControllerRef] = {}

        self._entity_to_light: dict[str, str] = {}
        self._connectivity_to_light: dict[str, str] = {}
        self._light_to_room: dict[str, str] = {}
        self._light_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._regular_last_recall: dict[str, datetime | None] = {}

        self._listeners: list[Listener] = []
        self._unsubs: list[Callable[[], None]] = []
        self._state_unsub: Callable[[], None] | None = None

        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry.entry_id}",
            atomic_writes=True,
        )

    async def async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self.master_enabled = bool(stored.get("master_enabled", True))
        controllers = stored.get("controllers", {})
        if isinstance(controllers, dict):
            for room_id, raw in controllers.items():
                if isinstance(room_id, str) and (ref := ControllerRef.from_dict(raw)):
                    self._stored_controllers[room_id] = ref

        label_registry = lr.async_get(self.hass)
        label = label_registry.async_get_label_by_name(RECALL_LABEL_NAME)
        if label is None:
            label = label_registry.async_create(
                RECALL_LABEL_NAME,
                icon="mdi:lightbulb-auto",
                description=(
                    "Hue light entities enrolled for power-neutral, exact-light "
                    "appearance recovery after Hue connectivity_issue or Home "
                    "Assistant unavailable clears."
                ),
            )
        self.label_id = label.label_id

        await self.async_refresh_topology(initial=True)
        self._bootstrap_controller_journal()
        self._seed_scene_edges_and_light_fingerprints()

        self._unsubs.append(
            self.api.scenes.subscribe(
                self._on_scene_resource_event,
                event_filter=(
                    EventType.RESOURCE_ADDED,
                    EventType.RESOURCE_UPDATED,
                    EventType.RESOURCE_DELETED,
                ),
            )
        )
        self._unsubs.append(
            self.api.lights.subscribe(
                self._on_light_resource_event,
                event_filter=(
                    EventType.RESOURCE_ADDED,
                    EventType.RESOURCE_UPDATED,
                    EventType.RESOURCE_DELETED,
                ),
            )
        )
        self._unsubs.append(
            self.api.groups.room.subscribe(
                self._on_room_resource_event,
                event_filter=(
                    EventType.RESOURCE_ADDED,
                    EventType.RESOURCE_UPDATED,
                    EventType.RESOURCE_DELETED,
                ),
            )
        )
        self._unsubs.append(
            self.api.sensors.zigbee_connectivity.subscribe(
                self._on_connectivity_resource_event,
                event_filter=(
                    EventType.RESOURCE_ADDED,
                    EventType.RESOURCE_UPDATED,
                    EventType.RESOURCE_DELETED,
                ),
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_entity_registry_update
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                lr.EVENT_LABEL_REGISTRY_UPDATED, self._on_label_registry_update
            )
        )
        self._schedule_save()
        self._notify()

    async def async_shutdown(self) -> None:
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for room in self.rooms.values():
            self._cancel_room_reconcile(room)
        for light in self.lights.values():
            self._cancel_light_work(light)

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

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._serialize, STORAGE_SAVE_DELAY)

    @callback
    def _serialize(self) -> dict[str, Any]:
        return {
            "master_enabled": self.master_enabled,
            "controllers": {
                room_id: room.controller.as_dict()
                for room_id, room in self.rooms.items()
                if room.controller is not None
            },
        }

    async def async_set_master_enabled(self, enabled: bool) -> None:
        if self.master_enabled == enabled:
            return
        self.master_enabled = enabled
        if not enabled:
            for light in self.lights.values():
                if light.armed and not self._current_reasons(light):
                    self._finish_light_transaction(light, "master_disabled", "disabled")
        self._schedule_save()
        self._notify()

    async def async_refresh_topology(self, *, initial: bool = False) -> None:
        ent_reg = er.async_get(self.hass)
        label_registry = lr.async_get(self.hass)
        label = label_registry.async_get_label_by_name(RECALL_LABEL_NAME)
        if label is not None:
            self.label_id = label.label_id
        elif self.label_id and label_registry.async_get_label(self.label_id) is None:
            self.label_id = None

        old_lights = self.lights
        new_lights: dict[str, LightRecoveryState] = {}
        entity_to_light: dict[str, str] = {}
        connectivity_to_light: dict[str, str] = {}
        light_to_room: dict[str, str] = {}
        current_room_ids: set[str] = set()

        for hue_room in self.api.groups.room:
            room_id = hue_room.id
            current_room_ids.add(room_id)
            room = self.rooms.get(room_id)
            if room is None:
                room = RoomRecallState(
                    room_id=room_id,
                    room_name=hue_room.metadata.name,
                    controller=self._stored_controllers.get(room_id),
                )
                self.rooms[room_id] = room
            else:
                room.room_name = hue_room.metadata.name

            hue_lights = self.api.groups.room.get_lights(room_id)
            entity_ids: list[str] = []
            light_ids: list[str] = []
            all_resolved = True

            for hue_light in hue_lights:
                entity_id = ent_reg.async_get_entity_id("light", "hue", hue_light.id)
                if entity_id is None:
                    all_resolved = False
                    continue

                connectivity = self._connectivity_for_hue_light(hue_light.id)
                connectivity_id = connectivity.id if connectivity is not None else None
                connectivity_status = (
                    _connectivity_value(connectivity.status)
                    if connectivity is not None
                    else "unknown"
                )
                state = old_lights.get(hue_light.id)
                if state is None:
                    state = LightRecoveryState(
                        entity_id=entity_id,
                        hue_light_id=hue_light.id,
                        room_id=room_id,
                        connectivity_resource_id=connectivity_id,
                        connectivity_status=connectivity_status,
                        connectivity_issue_pending=(
                            connectivity_status
                            == ConnectivityServiceStatus.CONNECTIVITY_ISSUE.value
                        ),
                        ha_available=self._entity_available(entity_id),
                    )
                    initial_reasons = self._reasons_for_values(
                        state.ha_available, state.connectivity_issue_pending
                    )
                    if initial_reasons:
                        state.armed = True
                        state.trigger_reasons.update(initial_reasons)
                        state.status = "impaired"
                else:
                    state.entity_id = entity_id
                    state.room_id = room_id
                    state.connectivity_resource_id = connectivity_id
                    state.connectivity_status = connectivity_status
                    state.connectivity_issue_pending = next_connectivity_issue_pending(
                        current_pending=state.connectivity_issue_pending,
                        new_status=connectivity_status,
                    )
                    state.ha_available = self._entity_available(entity_id)

                new_lights[hue_light.id] = state
                entity_ids.append(entity_id)
                light_ids.append(hue_light.id)
                entity_to_light[entity_id] = hue_light.id
                light_to_room[hue_light.id] = room_id
                if connectivity_id is not None:
                    connectivity_to_light[connectivity_id] = hue_light.id

            room.light_entity_ids = tuple(sorted(entity_ids))
            room.hue_light_ids = tuple(sorted(light_ids))
            room.total_hue_lights = len(hue_lights)
            room.enrolled = (
                bool(hue_lights)
                and all_resolved
                and self.label_id is not None
                and all(
                    (entry := ent_reg.async_get(entity_id)) is not None
                    and self.label_id in entry.labels
                    for entity_id in room.light_entity_ids
                )
            )

        for light_id, state in old_lights.items():
            if light_id not in new_lights:
                self._cancel_light_work(state)
        for room_id in set(self.rooms) - current_room_ids:
            room = self.rooms.pop(room_id)
            self._cancel_room_reconcile(room)

        self.lights = new_lights
        self._entity_to_light = entity_to_light
        self._connectivity_to_light = connectivity_to_light
        self._light_to_room = light_to_room
        self._reset_state_listener()
        self._validate_controllers()
        for state in self.lights.values():
            self._process_light_condition(state)
        self._notify()
        if not initial:
            _LOGGER.debug("Refreshed Hue Scene Recall topology")

    def _connectivity_for_hue_light(self, hue_light_id: str) -> ZigbeeConnectivity | None:
        try:
            device = self.api.lights.get_device(hue_light_id)
            if device is None:
                return None
            return self.api.devices.get_zigbee_connectivity(device.id)
        except (KeyError, TypeError):
            return None

    def _bootstrap_controller_journal(self) -> None:
        self._validate_controllers()
        for room in self.rooms.values():
            active_smart = self._active_smart_scenes(room.room_id)
            if len(active_smart) == 1:
                self._set_controller(
                    room,
                    make_controller("smart_scene", active_smart[0].id, reason="startup_active_smart_scene"),
                )
                continue
            if active_smart:
                continue
            if room.controller is None:
                regular_active = self._active_regular_scenes(room.room_id)
                if len(regular_active) == 1:
                    self._set_controller(
                        room,
                        make_controller("scene", regular_active[0].id, reason="startup_unique_active_scene"),
                    )

    def _validate_controllers(self) -> None:
        for room in self.rooms.values():
            ref = room.controller
            if ref is None:
                continue
            resource = self._get_scene(ref.rid)
            valid = (
                resource is not None
                and getattr(getattr(resource, "group", None), "rid", None) == room.room_id
                and ((ref.kind == "smart_scene") == isinstance(resource, HueSmartScene))
            )
            if not valid:
                self._set_controller(room, None, reason="stored_controller_invalid")

    def _seed_scene_edges_and_light_fingerprints(self) -> None:
        for scene in self.api.scenes:
            if isinstance(scene, HueSmartScene):
                continue
            self._regular_last_recall[scene.id] = _scene_last_recall(scene)
        for light in self.api.lights:
            try:
                raw = dataclass_to_dict(light, skip_none=True)
            except (TypeError, ValueError):
                continue
            self._light_fingerprints[light.id] = appearance_fingerprint(raw)

    def _active_smart_scenes(self, room_id: str) -> list[HueSmartScene]:
        return [
            scene
            for scene in self.api.scenes
            if isinstance(scene, HueSmartScene)
            and getattr(scene.group, "rid", None) == room_id
            and scene.state == SmartSceneState.ACTIVE
        ]

    def _active_regular_scenes(self, room_id: str) -> list[HueScene]:
        return [
            scene
            for scene in self.api.scenes
            if not isinstance(scene, HueSmartScene)
            and getattr(scene.group, "rid", None) == room_id
            and _scene_active_value(scene) != "inactive"
        ]

    def _set_controller(
        self,
        room: RoomRecallState,
        controller: ControllerRef | None,
        *,
        reason: str | None = None,
    ) -> None:
        if (
            room.controller is None
            and controller is None
        ) or (
            room.controller is not None
            and controller is not None
            and room.controller.kind == controller.kind
            and room.controller.rid == controller.rid
        ):
            return
        room.controller = controller
        if controller is None:
            self._stored_controllers.pop(room.room_id, None)
        else:
            self._stored_controllers[room.room_id] = controller
        room.last_controller_change_at = _now()
        room.last_controller_change_reason = reason or (
            controller.reason if controller is not None else "cleared"
        )
        self._schedule_save()
        self._notify()

    @callback
    def _schedule_controller_reconcile(self, room_id: str) -> None:
        room = self.rooms.get(room_id)
        if room is None:
            return
        self._cancel_room_reconcile(room)
        room.controller_reconcile_handle = self.hass.loop.call_later(
            CONTROLLER_RECONCILE_SECONDS,
            lambda: self.hass.async_create_task(
                self._async_reconcile_controller(room_id),
                name=f"{self.entry.domain}_controller_reconcile_{room_id}",
            ),
        )

    async def _async_reconcile_controller(self, room_id: str) -> None:
        room = self.rooms.get(room_id)
        if room is None:
            return
        room.controller_reconcile_handle = None

        active_smart = self._active_smart_scenes(room_id)
        if len(active_smart) == 1:
            self._set_controller(
                room,
                make_controller("smart_scene", active_smart[0].id, reason="active_smart_scene"),
            )
            room.fresh_regular_candidates.clear()
            room.material_change_pending = False
            return
        if len(active_smart) > 1:
            room.last_controller_change_reason = "ambiguous_multiple_active_smart_scenes"
            room.fresh_regular_candidates.clear()
            room.material_change_pending = False
            self._notify()
            return

        active_candidates = [
            scene
            for scene_id in room.fresh_regular_candidates
            if (scene := self._get_scene(scene_id)) is not None
            and not isinstance(scene, HueSmartScene)
            and getattr(scene.group, "rid", None) == room_id
            and _scene_active_value(scene) != "inactive"
        ]
        if len(active_candidates) == 1:
            self._set_controller(
                room,
                make_controller("scene", active_candidates[0].id, reason="fresh_saved_scene_recall"),
            )
            room.fresh_regular_candidates.clear()
            room.material_change_pending = False
            return
        if len(active_candidates) > 1:
            self._set_controller(room, None, reason="ambiguous_fresh_regular_scene_recall")
            room.fresh_regular_candidates.clear()
            room.material_change_pending = False
            return

        if room.controller is not None and room.controller.kind == "scene":
            current = self._get_scene(room.controller.rid)
            if (
                current is not None
                and not isinstance(current, HueSmartScene)
                and _scene_active_value(current) != "inactive"
            ):
                room.fresh_regular_candidates.clear()
                room.material_change_pending = False
                return

        if room.material_change_pending and self._room_healthy(room):
            self._set_controller(room, None, reason="healthy_unsaved_appearance_change")

        room.fresh_regular_candidates.clear()
        room.material_change_pending = False
        self._notify()

    @callback
    def _reset_state_listener(self) -> None:
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        if self._entity_to_light:
            self._state_unsub = async_track_state_change_event(
                self.hass, tuple(self._entity_to_light), self._on_state_change
            )

    @callback
    def _on_state_change(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        light_id = self._entity_to_light.get(entity_id)
        if light_id is None or (light := self.lights.get(light_id)) is None:
            return
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        old_unavailable = self._state_unavailable(old_state)
        new_unavailable = self._state_unavailable(new_state)
        if old_unavailable == new_unavailable:
            return

        now = _now()
        light.ha_available = not new_unavailable
        if new_unavailable:
            light.last_ha_unavailable_at = now
            light.ha_unavailable_count += 1
        else:
            light.last_ha_available_at = now
        self._process_light_condition(light)

    @callback
    def _on_connectivity_resource_event(
        self, event_type: EventType, connectivity: ZigbeeConnectivity
    ) -> None:
        connectivity_id = getattr(connectivity, "id", None)
        if connectivity_id is None:
            return
        if event_type == EventType.RESOURCE_DELETED:
            self.hass.async_create_task(
                self.async_refresh_topology(),
                name=f"{self.entry.domain}_refresh_connectivity",
            )
            return
        light_id = self._connectivity_to_light.get(connectivity_id)
        if light_id is None:
            if event_type == EventType.RESOURCE_ADDED:
                self.hass.async_create_task(
                    self.async_refresh_topology(),
                    name=f"{self.entry.domain}_refresh_connectivity",
                )
            return
        light = self.lights.get(light_id)
        if light is None:
            return

        new_status = _connectivity_value(connectivity.status)
        old_status = light.connectivity_status
        if old_status == new_status:
            return
        light.connectivity_status = new_status
        was_pending = light.connectivity_issue_pending
        light.connectivity_issue_pending = next_connectivity_issue_pending(
            current_pending=was_pending, new_status=new_status
        )
        now = _now()
        if new_status == ConnectivityServiceStatus.CONNECTIVITY_ISSUE.value:
            light.last_connectivity_issue_at = now
            light.connectivity_issue_count += 1
        elif was_pending and not light.connectivity_issue_pending:
            light.last_connectivity_recovered_at = now
        self._process_light_condition(light)

    @callback
    def _process_light_condition(self, light: LightRecoveryState) -> None:
        reasons = self._current_reasons(light)
        if reasons:
            light.trigger_reasons.update(reasons)
            if not light.armed:
                light.armed = True
            if light.status != "impaired":
                # Invalidate any settle/defer/write transaction immediately when
                # this exact light becomes impaired again.
                light.generation += 1
            light.status = "impaired"
            light.last_reason = "+".join(sorted(reasons))
            self._cancel_light_work(light, keep_running_task=True)
            self._notify()
            return

        if not light.armed:
            light.status = "healthy"
            self._notify()
            return
        if light.settle_handle is not None or light.defer_handle is not None:
            return
        if light.task is not None and not light.task.done():
            return

        light.generation += 1
        generation = light.generation
        light.status = "settling"
        light.last_recovery_trigger_at = _now()
        light.settle_handle = self.hass.loop.call_later(
            RECOVERY_SETTLE_SECONDS,
            lambda: self._start_recovery_task(light.hue_light_id, generation),
        )
        self._notify()

    @callback
    def _start_recovery_task(self, light_id: str, generation: int) -> None:
        light = self.lights.get(light_id)
        if light is None:
            return
        light.settle_handle = None
        light.task = self.hass.async_create_task(
            self._async_recover_light(light_id, generation),
            name=f"{self.entry.domain}_recover_{light_id}",
        )

    async def _async_recover_light(self, light_id: str, generation: int) -> None:
        light = self.lights.get(light_id)
        if light is None or generation != light.generation:
            return
        room = self.rooms.get(light.room_id)
        if room is None:
            return
        if not self.master_enabled:
            self._finish_light_transaction(light, "master_disabled", "disabled")
            return
        if not room.enrolled:
            self._finish_light_transaction(light, "room_not_enrolled", "aborted_unresolved")
            return
        if self._current_reasons(light):
            light.status = "impaired"
            self._notify()
            return

        for attempt in range(1, MAX_RECOVERY_ATTEMPTS + 1):
            if generation != light.generation or self._current_reasons(light):
                light.status = "impaired"
                self._notify()
                return

            light.status = "resolving"
            self._notify()
            try:
                resources = await self._fresh_resources()
            except Exception as err:  # noqa: BLE001 - Hue transport errors are runtime data
                light.last_reason = f"bridge_snapshot_failed:{type(err).__name__}"
                if attempt < MAX_RECOVERY_ATTEMPTS:
                    await asyncio.sleep(RECOVERY_RETRY_DELAY_SECONDS)
                    continue
                self._finish_light_transaction(light, light.last_reason, "failed_unverified")
                return

            desired = resolve_desired_state(
                resources,
                room_id=room.room_id,
                light_id=light.hue_light_id,
                controller=room.controller,
                now=_now(),
            )
            self._record_desired(light, desired)

            if desired.status == "no_controller":
                self._finish_light_transaction(light, desired.reason, "no_recoverable_controller")
                return
            if desired.status == "unresolved":
                self._finish_light_transaction(light, desired.reason, "aborted_unresolved")
                return
            if desired.status == "deferred":
                self._schedule_deferred_recovery(light, generation, desired)
                return
            assert desired.payload is not None
            assert "on" not in desired.payload

            try:
                current = await self._fresh_light(light.hue_light_id)
            except Exception as err:  # noqa: BLE001
                light.last_reason = f"prewrite_light_read_failed:{type(err).__name__}"
                if attempt < MAX_RECOVERY_ATTEMPTS:
                    await asyncio.sleep(RECOVERY_RETRY_DELAY_SECONDS)
                    continue
                self._finish_light_transaction(light, light.last_reason, "failed_unverified")
                return

            if not self._controller_matches_desired(room.controller, desired):
                if attempt < MAX_RECOVERY_ATTEMPTS:
                    continue
                self._finish_light_transaction(light, "controller_changed_during_recovery", "aborted_unresolved")
                return

            if appearance_matches(current, desired.payload):
                self._finish_light_transaction(light, "desired_appearance_already_present", "already_correct")
                return

            if self._current_reasons(light) or generation != light.generation:
                light.status = "impaired"
                self._notify()
                return

            light.status = "writing"
            light.internal_expected_payload = desired.payload
            light.internal_expected_until = _now() + timedelta(seconds=3)
            self._notify()
            verified, write_error = await self._async_write_and_verify(
                light, desired.payload, generation
            )
            if verified:
                if not self._controller_matches_desired(room.controller, desired):
                    if attempt < MAX_RECOVERY_ATTEMPTS:
                        continue
                    self._finish_light_transaction(light, "controller_changed_after_write", "aborted_unresolved")
                    return
                reason = "verified_after_ambiguous_write" if write_error else "verified"
                self._finish_light_transaction(light, reason, "verified")
                return

            if self._current_reasons(light) or generation != light.generation:
                light.status = "impaired"
                self._notify()
                return
            if attempt < MAX_RECOVERY_ATTEMPTS:
                light.status = "retry_pending"
                self._notify()
                await asyncio.sleep(RECOVERY_RETRY_DELAY_SECONDS)
                continue

            reason = (
                f"write_error_unverified:{type(write_error).__name__}"
                if write_error
                else "postwrite_state_unverified"
            )
            self._finish_light_transaction(light, reason, "failed_unverified")
            return

    def _schedule_deferred_recovery(
        self, light: LightRecoveryState, generation: int, desired: DesiredState
    ) -> None:
        if desired.defer_until is None:
            self._finish_light_transaction(light, "invalid_defer_without_deadline", "aborted_unresolved")
            return
        seconds = max(0.1, (desired.defer_until - _now()).total_seconds())
        light.status = "deferred"
        light.last_reason = desired.reason
        if light.defer_handle:
            light.defer_handle.cancel()
        light.defer_handle = self.hass.loop.call_later(
            seconds,
            lambda: self._start_deferred_task(light.hue_light_id, generation),
        )
        self._notify()

    @callback
    def _start_deferred_task(self, light_id: str, generation: int) -> None:
        light = self.lights.get(light_id)
        if light is None:
            return
        light.defer_handle = None
        if generation != light.generation:
            return
        if self._current_reasons(light):
            light.status = "impaired"
            self._notify()
            return
        light.task = self.hass.async_create_task(
            self._async_recover_light(light_id, generation),
            name=f"{self.entry.domain}_deferred_recover_{light_id}",
        )

    async def _async_write_and_verify(
        self,
        light: LightRecoveryState,
        payload: dict[str, Any],
        generation: int,
    ) -> tuple[bool, Exception | None]:
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
            id_filter=(light.hue_light_id,),
            event_filter=(EventType.RESOURCE_UPDATED,),
        )
        write_error: Exception | None = None
        try:
            if self._current_reasons(light) or generation != light.generation:
                return False, None
            try:
                await self.api.request(
                    "put",
                    f"clip/v2/resource/light/{light.hue_light_id}",
                    json=payload,
                )
            except Exception as err:  # noqa: BLE001 - verify ambiguous transport/207 outcomes
                write_error = err

            light.status = "verifying"
            self._notify()
            try:
                async with asyncio.timeout(VERIFY_EVENT_TIMEOUT_SECONDS):
                    await event.wait()
                return True, write_error
            except TimeoutError:
                pass

            if self._current_reasons(light) or generation != light.generation:
                return False, write_error
            try:
                current = await self._fresh_light(light.hue_light_id)
            except Exception:  # noqa: BLE001 - caller owns bounded retry policy
                return False, write_error
            return appearance_matches(current, payload), write_error
        finally:
            unsub()

    async def _fresh_resources(self) -> list[dict[str, Any]]:
        resources = await self.api.request("get", "clip/v2/resource")
        if not isinstance(resources, list):
            raise TypeError("Hue full-state query did not return a resource list")
        return [item for item in resources if isinstance(item, dict)]

    async def _fresh_light(self, light_id: str) -> dict[str, Any]:
        resources = await self.api.request("get", f"clip/v2/resource/light/{light_id}")
        if not isinstance(resources, list) or len(resources) != 1 or not isinstance(resources[0], dict):
            raise TypeError("Hue exact-light query did not return one resource")
        return resources[0]

    @staticmethod
    def _controller_matches_desired(
        controller: ControllerRef | None, desired: DesiredState
    ) -> bool:
        return (
            controller is not None
            and controller.kind == desired.controller_kind
            and controller.rid == desired.controller_rid
        )

    def _record_desired(self, light: LightRecoveryState, desired: DesiredState) -> None:
        light.last_reason = desired.reason
        light.last_controller_kind = desired.controller_kind
        light.last_controller_rid = desired.controller_rid
        light.last_controller_name = desired.controller_name
        light.last_effective_scene_rid = desired.effective_scene_rid
        light.last_effective_scene_name = desired.effective_scene_name

    def _finish_light_transaction(
        self, light: LightRecoveryState, reason: str, result: str
    ) -> None:
        light.last_reason = reason
        light.last_result = result
        light.last_recovery_at = _now()
        light.armed = False
        light.trigger_reasons.clear()
        light.status = "healthy" if not self._current_reasons(light) else "impaired"
        self._notify()

    def _current_reasons(self, light: LightRecoveryState) -> frozenset[RecoveryReason]:
        return self._reasons_for_values(light.ha_available, light.connectivity_issue_pending)

    @staticmethod
    def _reasons_for_values(
        ha_available: bool, connectivity_issue_pending: bool
    ) -> frozenset[RecoveryReason]:
        return impairment_reasons(
            ha_available=ha_available,
            connectivity_issue_pending=connectivity_issue_pending,
        )

    def _room_healthy(self, room: RoomRecallState) -> bool:
        return bool(room.hue_light_ids) and all(
            (light := self.lights.get(light_id)) is not None
            and not self._current_reasons(light)
            for light_id in room.hue_light_ids
        )

    def _light_guarded_from_manual_classification(self, light_id: str) -> bool:
        light = self.lights.get(light_id)
        if light is None:
            return True
        if self._current_reasons(light):
            return True
        if light.status in {"impaired", "settling", "resolving", "retry_pending"}:
            return True
        return False

    @callback
    def _on_light_resource_event(self, event_type: EventType, hue_light: Any) -> None:
        light_id = getattr(hue_light, "id", None)
        if not isinstance(light_id, str):
            return
        if event_type == EventType.RESOURCE_DELETED:
            self.hass.async_create_task(
                self.async_refresh_topology(), name=f"{self.entry.domain}_refresh_lights"
            )
            return
        if light_id not in self._light_to_room:
            if event_type == EventType.RESOURCE_ADDED:
                self.hass.async_create_task(
                    self.async_refresh_topology(), name=f"{self.entry.domain}_refresh_lights"
                )
            return
        try:
            raw = dataclass_to_dict(hue_light, skip_none=True)
        except (TypeError, ValueError):
            return
        new_fingerprint = appearance_fingerprint(raw)
        old_fingerprint = self._light_fingerprints.get(light_id)
        self._light_fingerprints[light_id] = new_fingerprint
        if old_fingerprint is None or old_fingerprint == new_fingerprint:
            return

        recovery = self.lights.get(light_id)
        if recovery is not None and recovery.internal_expected_payload is not None:
            if (
                recovery.internal_expected_until is not None
                and _now() <= recovery.internal_expected_until
                and appearance_matches(raw, recovery.internal_expected_payload)
            ):
                # This is the exact appearance HueRecall just requested. It is
                # verification evidence, not a user override.
                recovery.internal_expected_payload = None
                recovery.internal_expected_until = None
                return
            if recovery.internal_expected_until is not None and _now() > recovery.internal_expected_until:
                recovery.internal_expected_payload = None
                recovery.internal_expected_until = None

        if self._light_guarded_from_manual_classification(light_id):
            return
        room_id = self._light_to_room[light_id]
        room = self.rooms.get(room_id)
        if room is None or not self._room_healthy(room):
            return
        room.material_change_pending = True
        self._schedule_controller_reconcile(room_id)

    @callback
    def _on_scene_resource_event(
        self, event_type: EventType, scene: HueScene | HueSmartScene
    ) -> None:
        scene_id = getattr(scene, "id", None)
        group = getattr(scene, "group", None)
        room_id = getattr(group, "rid", None)
        if not isinstance(scene_id, str) or not isinstance(room_id, str):
            return
        room = self.rooms.get(room_id)
        if room is None:
            return

        if event_type == EventType.RESOURCE_DELETED:
            self._regular_last_recall.pop(scene_id, None)
            if room.controller is not None and room.controller.rid == scene_id:
                self._set_controller(room, None, reason="controller_deleted")
            self._schedule_controller_reconcile(room_id)
            return

        if isinstance(scene, HueSmartScene):
            if scene.state == SmartSceneState.ACTIVE:
                self._set_controller(
                    room,
                    make_controller("smart_scene", scene.id, reason="smart_scene_became_active"),
                )
                room.fresh_regular_candidates.clear()
                room.material_change_pending = False
            else:
                # Passive Smart Scene inactivity is not replacement evidence.
                self._schedule_controller_reconcile(room_id)
            return

        new_last = _scene_last_recall(scene)
        old_last = self._regular_last_recall.get(scene_id)
        self._regular_last_recall[scene_id] = new_last
        if new_last is not None and new_last != old_last:
            room.fresh_regular_candidates.add(scene_id)
        self._schedule_controller_reconcile(room_id)

    async def async_select_scene(self, room_id: str, scene_id: str) -> None:
        scene = self._get_scene(scene_id)
        if scene is None or getattr(scene.group, "rid", None) != room_id:
            raise ValueError("Scene does not belong to this Hue room")
        if isinstance(scene, HueSmartScene):
            await self.bridge.async_request_call(self.api.scenes.smart_scene.recall, scene_id)
            controller = make_controller("smart_scene", scene_id, reason="hue_recall_select")
        else:
            await self.bridge.async_request_call(
                self.api.scenes.scene.recall,
                scene_id,
                dynamic=bool(scene.auto_dynamic),
            )
            controller = make_controller("scene", scene_id, reason="hue_recall_select")
        room = self.rooms[room_id]
        self._set_controller(room, controller)
        room.fresh_regular_candidates.clear()
        room.material_change_pending = False
        self._notify()

    def controller_from_cache(self, room_id: str) -> tuple[str, str, str] | None:
        room = self.rooms.get(room_id)
        if room is None:
            return None
        active_smart = self._active_smart_scenes(room_id)
        if len(active_smart) == 1:
            scene = active_smart[0]
            return scene.id, scene.metadata.name, "smart_scene"
        ref = room.controller
        if ref is None:
            return None
        scene = self._get_scene(ref.rid)
        if scene is None or getattr(scene.group, "rid", None) != room_id:
            return None
        return scene.id, scene.metadata.name, ref.kind

    # Compatibility alias for v0.2.1 entity code/users.
    authoritative_scene_from_cache = controller_from_cache

    def room_ids(self) -> tuple[str, ...]:
        return tuple(self.rooms)

    def room_name(self, room_id: str) -> str:
        return self.rooms[room_id].room_name

    def scene_options(self, room_id: str) -> list[str]:
        option_to_id, _ = self._scene_option_maps(room_id)
        return list(option_to_id)

    def selected_option(self, room_id: str) -> str | None:
        controller = self.controller_from_cache(room_id)
        if controller is None:
            return None
        scene_id, _, _ = controller
        _, id_to_option = self._scene_option_maps(room_id)
        return id_to_option.get(scene_id)

    def scene_id_for_option(self, room_id: str, option: str) -> str:
        option_to_id, _ = self._scene_option_maps(room_id)
        return option_to_id[option]

    def labeled_light_count(self, room_id: str) -> int:
        if self.label_id is None:
            return 0
        ent_reg = er.async_get(self.hass)
        return sum(
            1
            for entity_id in self.rooms[room_id].light_entity_ids
            if (entry := ent_reg.async_get(entity_id)) is not None
            and self.label_id in entry.labels
        )

    def room_diagnostic_state(self, room_id: str) -> str:
        room = self.rooms[room_id]
        states = [self.lights.get(light_id) for light_id in room.hue_light_ids]
        if any(state and state.status in {"writing", "verifying", "resolving", "retry_pending"} for state in states):
            return "recovering"
        if any(state and state.status == "deferred" for state in states):
            return "deferred"
        has_connectivity = any(state and state.connectivity_issue_pending for state in states)
        has_unavailable = any(state and not state.ha_available for state in states)
        if has_connectivity and has_unavailable:
            return "connectivity_issue+unavailable"
        if has_connectivity:
            return "connectivity_issue"
        if has_unavailable:
            return "unavailable"
        return "healthy"

    def room_diagnostic_attributes(self, room_id: str) -> dict[str, Any]:
        room = self.rooms[room_id]
        controller = room.controller
        controller_name = None
        if controller and (resource := self._get_scene(controller.rid)) is not None:
            controller_name = resource.metadata.name
        lights: dict[str, dict[str, Any]] = {}
        for light_id in room.hue_light_ids:
            state = self.lights.get(light_id)
            if state is None:
                continue
            ha_state = self.hass.states.get(state.entity_id)
            friendly_name = ha_state.attributes.get("friendly_name", state.entity_id) if ha_state else state.entity_id
            lights[state.entity_id] = {
                "name": friendly_name,
                "hue_light_id": light_id,
                "ha_available": state.ha_available,
                "zigbee_connectivity": state.connectivity_status,
                "connectivity_issue_pending": state.connectivity_issue_pending,
                "connectivity_resource_id": state.connectivity_resource_id,
                "recovery_status": state.status,
                "recovery_armed": state.armed,
                "trigger_reasons": sorted(state.trigger_reasons),
                "last_connectivity_issue_at": _iso(state.last_connectivity_issue_at),
                "last_connectivity_recovered_at": _iso(state.last_connectivity_recovered_at),
                "last_ha_unavailable_at": _iso(state.last_ha_unavailable_at),
                "last_ha_available_at": _iso(state.last_ha_available_at),
                "last_recovery_trigger_at": _iso(state.last_recovery_trigger_at),
                "last_recovery_at": _iso(state.last_recovery_at),
                "last_recovery_result": state.last_result,
                "last_recovery_reason": state.last_reason,
                "last_controller_kind": state.last_controller_kind,
                "last_controller_rid": state.last_controller_rid,
                "last_controller_name": state.last_controller_name,
                "last_effective_scene_rid": state.last_effective_scene_rid,
                "last_effective_scene_name": state.last_effective_scene_name,
                "connectivity_issue_count_since_load": state.connectivity_issue_count,
                "ha_unavailable_count_since_load": state.ha_unavailable_count,
            }
        return {
            "hue_room_id": room.room_id,
            "recall_enrolled": room.enrolled,
            "master_enabled": self.master_enabled,
            "controller_state": controller.kind if controller else "no_recoverable_controller",
            "controller_rid": controller.rid if controller else None,
            "controller_name": controller_name,
            "controller_updated_at": controller.updated_at if controller else None,
            "controller_reason": controller.reason if controller else None,
            "last_controller_change_at": _iso(room.last_controller_change_at),
            "last_controller_change_reason": room.last_controller_change_reason,
            "lights": lights,
        }

    def _scene_option_maps(self, room_id: str) -> tuple[dict[str, str], dict[str, str]]:
        scenes = sorted(
            (scene for scene in self.api.scenes if getattr(scene.group, "rid", None) == room_id),
            key=lambda scene: (scene.metadata.name.casefold(), scene.id),
        )
        option_to_id: dict[str, str] = {}
        id_to_option: dict[str, str] = {}
        for scene in scenes:
            option = scene.metadata.name
            repeat = 1
            while option in option_to_id:
                repeat += 1
                option = f"{scene.metadata.name} ({repeat})"
            option_to_id[option] = scene.id
            id_to_option[scene.id] = option
        return option_to_id, id_to_option

    def _get_scene(self, scene_id: str | None) -> HueScene | HueSmartScene | None:
        if not scene_id:
            return None
        try:
            return self.api.scenes[scene_id]
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _state_unavailable(state: State | None) -> bool:
        return state is None or state.state in STATE_UNAVAILABLE_VALUES

    def _entity_available(self, entity_id: str) -> bool:
        return not self._state_unavailable(self.hass.states.get(entity_id))

    @callback
    def _on_room_resource_event(self, event_type: EventType, room: Any) -> None:
        self.hass.async_create_task(
            self.async_refresh_topology(), name=f"{self.entry.domain}_refresh_hue_rooms"
        )

    @callback
    def _on_entity_registry_update(self, event: Event) -> None:
        entity_id = event.data.get("entity_id", "")
        if not entity_id.startswith("light."):
            return
        entity_entry = er.async_get(self.hass).async_get(entity_id)
        was_tracked = entity_id in self._entity_to_light
        is_our_hue_light = (
            entity_entry is not None
            and entity_entry.platform == "hue"
            and entity_entry.config_entry_id == self.hue_entry.entry_id
        )
        if not was_tracked and not is_our_hue_light:
            return
        self.hass.async_create_task(
            self.async_refresh_topology(), name=f"{self.entry.domain}_refresh_labels"
        )

    @callback
    def _on_label_registry_update(self, event: Event) -> None:
        self.hass.async_create_task(
            self.async_refresh_topology(), name=f"{self.entry.domain}_refresh_recall_label"
        )

    @staticmethod
    @callback
    def _cancel_room_reconcile(room: RoomRecallState) -> None:
        if room.controller_reconcile_handle:
            room.controller_reconcile_handle.cancel()
            room.controller_reconcile_handle = None

    @staticmethod
    @callback
    def _cancel_light_work(
        light: LightRecoveryState, *, keep_running_task: bool = False
    ) -> None:
        if light.settle_handle:
            light.settle_handle.cancel()
            light.settle_handle = None
        if light.defer_handle:
            light.defer_handle.cancel()
            light.defer_handle = None
        if not keep_running_task and light.task and not light.task.done():
            light.task.cancel()
            light.task = None
