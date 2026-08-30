"""Scene tracking and power-loss reconciliation for Hue Scene Recall."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
import logging
from typing import Any

from aiohue.v2.controllers.events import EventType
from aiohue.v2.models.scene import Scene as HueScene, SceneActiveStatus
from aiohue.v2.models.smart_scene import SmartScene as HueSmartScene
from aiohue.v2.scene_activity import SceneActivityTracker

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .const import (
    POWER_RECOVERY_MAX_ATTEMPTS,
    POWER_RECOVERY_RETRY_SECONDS,
    POWER_RECOVERY_SETTLE_SECONDS,
    RECALL_LABEL_NAME,
    RECALL_POWER_LABEL_NAME,
    RECOVERY_SETTLE_SECONDS,
    STATE_UNAVAILABLE_VALUES,
    STORAGE_KEY_PREFIX,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)
from .context import is_unparented_context

_LOGGER = logging.getLogger(__name__)

Listener = Callable[[], None]


@dataclass(slots=True)
class RoomRecallState:
    """Runtime and persistent state for one Hue room."""

    room_id: str
    room_name: str
    light_entity_ids: tuple[str, ...] = ()
    power_entity_ids: tuple[str, ...] = ()
    total_hue_lights: int = 0
    enrolled: bool = False
    all_available: bool = False

    resume_scene_id: str | None = None
    resume_scene_mode: str | None = None
    active_scene_id: str | None = None
    desired_on: bool | None = None
    recall_armed: bool = False

    reconciling: bool = False
    power_cycle_active: bool = False
    recovery_attempts: int = 0
    recovery_handle: asyncio.TimerHandle | None = field(default=None, repr=False)


class HueSceneRecallManager:
    """Coordinate Hue scene tracking and room reconciliation."""

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
        self.power_label_id: str | None = None
        self.rooms: dict[str, RoomRecallState] = {}
        self._entity_to_rooms: dict[str, set[str]] = {}
        self._power_entity_to_rooms: dict[str, set[str]] = {}
        self._listeners: list[Listener] = []
        self._unsubs: list[Callable[[], None]] = []
        self._state_unsub: Callable[[], None] | None = None
        self._tracker_unsubs: dict[str, Callable[[], None]] = {}
        self._own_tracker = False
        self._tracker: SceneActivityTracker | None = None

        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry.entry_id}",
            atomic_writes=True,
        )
        self._stored_rooms: dict[str, dict[str, Any]] = {}

    async def async_setup(self) -> None:
        """Initialize tracking using Home Assistant's existing Hue connection."""
        stored = await self._store.async_load() or {}
        self.master_enabled = bool(stored.get("master_enabled", True))
        self._stored_rooms = dict(stored.get("rooms", {}))

        label_registry = lr.async_get(self.hass)
        label = label_registry.async_get_label_by_name(RECALL_LABEL_NAME)
        if label is None:
            label = label_registry.async_create(
                RECALL_LABEL_NAME,
                icon="mdi:lightbulb-auto",
                description=(
                    "Hue lights in a room are automatically scene-recalled after "
                    "power recovery when every Hue light in that room has this label."
                ),
            )
        self.label_id = label.label_id

        power_label = label_registry.async_get_label_by_name(RECALL_POWER_LABEL_NAME)
        if power_label is None:
            power_label = label_registry.async_create(
                RECALL_POWER_LABEL_NAME,
                icon="mdi:power-plug",
                description=(
                    "Optional smart switch/relay whose power cycle should trigger Hue "
                    "scene restoration for the matching hueRecall room. Match it to the "
                    "room by sharing the room's HA label (for example isaacRoom)."
                ),
            )
        self.power_label_id = power_label.label_id

        existing_tracker = getattr(self.bridge, "scene_activity_tracker", None)
        if isinstance(existing_tracker, SceneActivityTracker):
            self._tracker = existing_tracker
        else:
            self._tracker = SceneActivityTracker(self.api.scenes)
            self._own_tracker = True

        await self.async_refresh_topology(initial=True)

        assert self._tracker is not None
        for room_id in self.rooms:
            self._subscribe_tracker(room_id)

        if self._own_tracker:
            self._tracker.start()

        # Seed the sticky scene from the bridge. A currently active Hue scene is
        # authoritative; temporary per-bulb changes never replace scene memory.
        for room_id in tuple(self.rooms):
            self._sync_scene_from_tracker(room_id)

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
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_entity_registry_update
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                lr.EVENT_LABEL_REGISTRY_UPDATED, self._on_label_registry_update
            )
        )
        self._notify()

    async def async_shutdown(self) -> None:
        """Release listeners and tracker resources."""
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        for unsub in self._tracker_unsubs.values():
            unsub()
        self._tracker_unsubs.clear()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._own_tracker and self._tracker is not None:
            self._tracker.stop()
        for room in self.rooms.values():
            self._cancel_room_timers(room)

    @callback
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Subscribe an entity to manager state changes."""
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
            "rooms": {
                room_id: {
                    "resume_scene_id": room.resume_scene_id,
                    "resume_scene_mode": room.resume_scene_mode,
                    "desired_on": room.desired_on,
                }
                for room_id, room in self.rooms.items()
            },
        }

    async def async_set_master_enabled(self, enabled: bool) -> None:
        """Enable or disable automatic recovery globally for this bridge."""
        if self.master_enabled == enabled:
            return
        self.master_enabled = enabled
        self._schedule_save()
        self._notify()
        # Intentionally do not reconcile immediately when re-enabled.

    async def async_refresh_topology(self, *, initial: bool = False) -> None:
        """Refresh Hue room membership, HA entity mapping, and label enrollment."""
        ent_reg = er.async_get(self.hass)
        label_registry = lr.async_get(self.hass)
        label = label_registry.async_get_label_by_name(RECALL_LABEL_NAME)
        if label is not None:
            self.label_id = label.label_id
        elif self.label_id and label_registry.async_get_label(self.label_id) is None:
            self.label_id = None

        power_label = label_registry.async_get_label_by_name(RECALL_POWER_LABEL_NAME)
        if power_label is not None:
            self.power_label_id = power_label.label_id
        elif self.power_label_id and label_registry.async_get_label(self.power_label_id) is None:
            self.power_label_id = None

        current_room_ids: set[str] = set()
        entity_to_rooms: dict[str, set[str]] = {}
        room_common_labels: dict[str, set[str]] = {}

        for hue_room in self.api.groups.room:
            room_id = hue_room.id
            current_room_ids.add(room_id)
            room = self.rooms.get(room_id)
            if room is None:
                room = RoomRecallState(room_id=room_id, room_name=hue_room.metadata.name)
                stored = self._stored_rooms.get(room_id, {})
                room.resume_scene_id = stored.get("resume_scene_id")
                room.resume_scene_mode = stored.get("resume_scene_mode")
                room.desired_on = stored.get("desired_on")
                # v0.1.3 intentionally ignores the legacy persisted disarmed flag.
                # Any still-valid remembered Hue scene is authoritative again.
                room.recall_armed = room.resume_scene_id is not None
                self.rooms[room_id] = room
                if self._tracker is not None:
                    self._subscribe_tracker(room_id)
            else:
                room.room_name = hue_room.metadata.name

            hue_lights = self.api.groups.room.get_lights(room_id)
            entity_ids: list[str] = []
            all_resolved = True
            for hue_light in hue_lights:
                entity_id = ent_reg.async_get_entity_id("light", "hue", hue_light.id)
                if entity_id is None:
                    all_resolved = False
                    continue
                entity_ids.append(entity_id)
                entity_to_rooms.setdefault(entity_id, set()).add(room_id)

            room.light_entity_ids = tuple(sorted(entity_ids))
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

            label_sets = [
                set(entry.labels)
                for entity_id in room.light_entity_ids
                if (entry := ent_reg.async_get(entity_id)) is not None
            ]
            common_labels = set.intersection(*label_sets) if label_sets else set()
            if self.label_id is not None:
                common_labels.discard(self.label_id)
            if self.power_label_id is not None:
                common_labels.discard(self.power_label_id)
            room_common_labels[room_id] = common_labels

            current_all_available = self._all_available(room)
            # A topology/label refresh establishes a fresh baseline. It must never
            # masquerade as an availability recovery and trigger a scene recall.
            room.all_available = current_all_available

            if current_all_available and room.desired_on is None:
                # Learn intent only when no persistent intent exists. A restart during
                # power recovery must not overwrite a remembered OFF state with the
                # bulbs' bright-white hardware fallback state.
                room.desired_on = self._any_on(room)

            # Clear references to scenes that no longer exist.
            if room.resume_scene_id and self._get_scene(room.resume_scene_id) is None:
                room.resume_scene_id = None
                room.resume_scene_mode = None
                room.recall_armed = False

        # Map optional hueRecallPower switches/relays to rooms using labels that
        # are common to every Hue light in that room. Prefer room-unique labels
        # so generic labels such as interiorLight cannot create ambiguous links.
        power_entity_to_rooms: dict[str, set[str]] = {}
        label_room_count: dict[str, int] = {}
        for common_labels in room_common_labels.values():
            for common_label in common_labels:
                label_room_count[common_label] = label_room_count.get(common_label, 0) + 1

        for room in self.rooms.values():
            room.power_entity_ids = ()

        if self.power_label_id is not None:
            for entity_entry in ent_reg.entities.values():
                if (
                    not entity_entry.entity_id.startswith("switch.")
                    or self.power_label_id not in entity_entry.labels
                ):
                    continue
                entry_labels = set(entity_entry.labels)
                candidates: list[tuple[int, str]] = []
                for room_id, common_labels in room_common_labels.items():
                    shared = entry_labels & common_labels
                    if not shared:
                        continue
                    score = sum(100 if label_room_count[label] == 1 else 1 for label in shared)
                    candidates.append((score, room_id))
                if not candidates:
                    _LOGGER.warning(
                        "hueRecallPower entity %s does not share a room label with any Hue room",
                        entity_entry.entity_id,
                    )
                    continue
                best_score = max(score for score, _ in candidates)
                best_rooms = [room_id for score, room_id in candidates if score == best_score]
                if len(best_rooms) != 1:
                    _LOGGER.warning(
                        "hueRecallPower entity %s maps ambiguously to Hue rooms %s",
                        entity_entry.entity_id,
                        best_rooms,
                    )
                    continue
                room_id = best_rooms[0]
                power_entity_to_rooms.setdefault(entity_entry.entity_id, set()).add(room_id)

        for entity_id, room_ids in power_entity_to_rooms.items():
            for room_id in room_ids:
                room = self.rooms.get(room_id)
                if room is not None:
                    room.power_entity_ids = tuple(sorted((*room.power_entity_ids, entity_id)))

        removed_room_ids = set(self.rooms) - current_room_ids
        for room_id in removed_room_ids:
            room = self.rooms.pop(room_id)
            self._cancel_room_timers(room)
            if unsub := self._tracker_unsubs.pop(room_id, None):
                unsub()

        self._entity_to_rooms = entity_to_rooms
        self._power_entity_to_rooms = power_entity_to_rooms
        # Never infer a physical/manual power cycle merely because a mapped relay
        # is already OFF during setup or a topology refresh. We did not observe the
        # OFF edge or its Context, so this could just as easily be bedtime or another
        # intentional automation state. Recovery cycles begin only from an observed,
        # unparented OFF transition while this manager is running.
        self._reset_state_listener()
        self._schedule_save()
        self._notify()

    @callback
    def _reset_state_listener(self) -> None:
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        entity_ids = tuple(set(self._entity_to_rooms) | set(self._power_entity_to_rooms))
        if entity_ids:
            self._state_unsub = async_track_state_change_event(
                self.hass, entity_ids, self._on_state_change
            )

    @callback
    def _subscribe_tracker(self, room_id: str) -> None:
        if self._tracker is None or room_id in self._tracker_unsubs:
            return
        self._tracker_unsubs[room_id] = self._tracker.subscribe(
            room_id, partial(self._on_tracker_update, room_id)
        )

    @callback
    def _on_tracker_update(self, room_id: str, _: str) -> None:
        self._sync_scene_from_tracker(room_id)

    @callback
    def _sync_scene_from_tracker(self, room_id: str) -> None:
        if self._tracker is None or (room := self.rooms.get(room_id)) is None:
            return
        state = self._tracker.get_group_state(room_id)
        active_scene_id = state.scene_id
        room.active_scene_id = active_scene_id

        if active_scene_id:
            # Hue scene selection is the only event that replaces sticky scene
            # memory. Temporary individual-bulb changes are intentionally ignored.
            room.resume_scene_id = active_scene_id
            room.resume_scene_mode = (
                state.scene_mode.value if state.scene_mode is not None else None
            )
            room.desired_on = True
            room.recall_armed = True
            self._schedule_save()
        self._notify()

    @callback
    def _on_state_change(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        new_state = event.data.get("new_state")
        if entity_id in self._power_entity_to_rooms:
            self._process_power_state_change(entity_id, new_state)
        for room_id in tuple(self._entity_to_rooms.get(entity_id, ())):
            self._process_room_light_state(room_id)

    @callback
    def _process_power_state_change(self, entity_id: str, new_state: State | None) -> None:
        if new_state is None or new_state.state not in (STATE_ON, STATE_OFF):
            return
        for room_id in tuple(self._power_entity_to_rooms.get(entity_id, ())):
            room = self.rooms.get(room_id)
            if room is None:
                continue
            if new_state.state == STATE_OFF:
                # Only a physical/manual power cut should begin a recall cycle.
                # Automation-generated relay changes (bedtime, occupancy, scripts)
                # carry a parent context and must not later resurrect the room scene.
                if not is_unparented_context(new_state.context):
                    _LOGGER.debug(
                        "Ignoring automation-generated power OFF from %s for %s",
                        entity_id,
                        room.room_name,
                    )
                    continue
                room.power_cycle_active = True
                room.recovery_attempts = 0
                self._cancel_recovery_timer(room)
                _LOGGER.debug(
                    "Physical power source %s turned OFF for %s; preserving scene %s",
                    entity_id,
                    room.room_name,
                    room.resume_scene_id,
                )
                self._notify()
                continue

            # With multiple mapped power sources, wait until all are back ON.
            if any(
                (state := self.hass.states.get(power_entity_id)) is not None
                and state.state == STATE_OFF
                for power_entity_id in room.power_entity_ids
            ):
                continue
            if not room.power_cycle_active:
                continue
            room.recovery_attempts = 0
            self._schedule_power_recovery(room, POWER_RECOVERY_SETTLE_SECONDS)
            self._notify()

    @callback
    def _schedule_power_recovery(self, room: RoomRecallState, delay: float) -> None:
        self._cancel_recovery_timer(room)
        room.recovery_handle = self.hass.loop.call_later(
            delay,
            lambda: self.hass.async_create_task(
                self.async_reconcile(room.room_id, reason="power_source_recovery")
            ),
        )

    @callback
    def _process_room_light_state(self, room_id: str) -> None:
        room = self.rooms.get(room_id)
        if room is None:
            return

        current_all_available = self._all_available(room)
        previous_all_available = room.all_available

        if room.power_cycle_active:
            room.all_available = current_all_available
            self._notify()
            return

        if not current_all_available:
            room.all_available = False
            self._cancel_recovery_timer(room)
            self._notify()
            return

        if not previous_all_available:
            room.all_available = True
            self._cancel_recovery_timer(room)
            room.recovery_handle = self.hass.loop.call_later(
                RECOVERY_SETTLE_SECONDS,
                lambda: self.hass.async_create_task(
                    self.async_reconcile(room_id, reason="availability_recovery")
                ),
            )
            self._notify()
            return

        if room.reconciling:
            return

        old_desired_on = room.desired_on
        if self._all_off(room):
            room.desired_on = False
        elif self._any_on(room):
            room.desired_on = True
        if room.desired_on != old_desired_on:
            self._schedule_save()
            self._notify()

    async def async_reconcile(self, room_id: str, *, reason: str) -> None:
        """Reconcile one room after availability or a known mains-power recovery."""
        room = self.rooms.get(room_id)
        if room is None:
            return
        room.recovery_handle = None
        power_recovery = reason == "power_source_recovery"

        if not self.master_enabled or not room.enrolled:
            if power_recovery:
                room.power_cycle_active = False
                room.recovery_attempts = 0
                self._notify()
            return

        if not self._all_available(room):
            if power_recovery and room.recovery_attempts < POWER_RECOVERY_MAX_ATTEMPTS:
                room.recovery_attempts += 1
                self._schedule_power_recovery(room, POWER_RECOVERY_RETRY_SECONDS)
            elif power_recovery:
                room.power_cycle_active = False
                room.recovery_attempts = 0
                self._notify()
            return

        intent = room.desired_on
        if intent is None:
            # First observation with no trustworthy pre-outage intent: learn current
            # state and do not change the room.
            room.desired_on = self._any_on(room)
            if power_recovery:
                room.power_cycle_active = False
                room.recovery_attempts = 0
            self._schedule_save()
            self._notify()
            return

        room.reconciling = True
        self._notify()
        failed = False
        try:
            if intent is False:
                await self._async_set_room_power(room_id, False)
                _LOGGER.debug("Reconciled %s OFF after %s", room.room_name, reason)
            elif room.recall_armed and room.resume_scene_id:
                await self._async_recall_saved_scene(room)
                _LOGGER.debug(
                    "Recalled scene %s for %s after %s",
                    room.resume_scene_id,
                    room.room_name,
                    reason,
                )
        except Exception:  # noqa: BLE001 - HA bridge wrapper provides user-facing errors
            failed = True
            _LOGGER.exception("Failed to reconcile Hue room %s", room.room_name)
        finally:
            room.reconciling = False
            room.all_available = self._all_available(room)

        if power_recovery and failed and room.recovery_attempts < POWER_RECOVERY_MAX_ATTEMPTS:
            room.recovery_attempts += 1
            self._schedule_power_recovery(room, POWER_RECOVERY_RETRY_SECONDS)
        elif power_recovery:
            room.power_cycle_active = False
            room.recovery_attempts = 0
        self._notify()

    async def async_select_scene(self, room_id: str, scene_id: str) -> None:
        """Explicitly select a Hue scene; manual selection ignores master recovery OFF."""
        room = self.rooms[room_id]
        scene = self._get_scene(scene_id)
        if scene is None or scene.group.rid != room_id:
            raise ValueError("Scene does not belong to this Hue room")

        room.reconciling = True
        self._notify()
        try:
            if isinstance(scene, HueSmartScene):
                await self.bridge.async_request_call(
                    self.api.scenes.smart_scene.recall, scene_id
                )
                mode: str | None = None
            else:
                await self.bridge.async_request_call(
                    self.api.scenes.scene.recall, scene_id, dynamic=False
                )
                mode = SceneActiveStatus.STATIC.value

            # Update immediately; SceneActivityTracker will confirm/refresh from Hue.
            room.resume_scene_id = scene_id
            room.resume_scene_mode = mode
            room.desired_on = True
            room.recall_armed = True
            self._schedule_save()
        finally:
            room.reconciling = False
            self._notify()

    async def _async_recall_saved_scene(self, room: RoomRecallState) -> None:
        scene = self._get_scene(room.resume_scene_id)
        if scene is None:
            room.resume_scene_id = None
            room.resume_scene_mode = None
            room.recall_armed = False
            self._schedule_save()
            return
        if isinstance(scene, HueSmartScene):
            await self.bridge.async_request_call(
                self.api.scenes.smart_scene.recall, scene.id
            )
            return
        dynamic = room.resume_scene_mode == SceneActiveStatus.DYNAMIC_PALETTE.value
        await self.bridge.async_request_call(
            self.api.scenes.scene.recall,
            scene.id,
            dynamic=dynamic,
        )

    async def _async_set_room_power(self, room_id: str, on: bool) -> None:
        hue_room = self.api.groups.room.get(room_id)
        if hue_room is None or hue_room.grouped_light is None:
            return
        await self.bridge.async_request_call(
            self.api.groups.grouped_light.set_state,
            hue_room.grouped_light,
            on=on,
        )

    def room_ids(self) -> tuple[str, ...]:
        return tuple(self.rooms)

    def room_name(self, room_id: str) -> str:
        return self.rooms[room_id].room_name

    def scene_options(self, room_id: str) -> list[str]:
        option_to_id, _ = self._scene_option_maps(room_id)
        return list(option_to_id)

    def selected_option(self, room_id: str) -> str | None:
        room = self.rooms[room_id]
        _, id_to_option = self._scene_option_maps(room_id)
        return id_to_option.get(room.resume_scene_id or "")

    def scene_id_for_option(self, room_id: str, option: str) -> str:
        option_to_id, _ = self._scene_option_maps(room_id)
        return option_to_id[option]

    def scene_name(self, scene_id: str | None) -> str | None:
        if not scene_id or (scene := self._get_scene(scene_id)) is None:
            return None
        return scene.metadata.name

    def labeled_light_count(self, room_id: str) -> int:
        """Return how many mapped Hue light entities carry the recall label."""
        if self.label_id is None:
            return 0
        ent_reg = er.async_get(self.hass)
        return sum(
            1
            for entity_id in self.rooms[room_id].light_entity_ids
            if (entry := ent_reg.async_get(entity_id)) is not None
            and self.label_id in entry.labels
        )

    def _scene_option_maps(self, room_id: str) -> tuple[dict[str, str], dict[str, str]]:
        scenes = sorted(
            (scene for scene in self.api.scenes if scene.group.rid == room_id),
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

    def _states(self, room: RoomRecallState) -> list[State | None]:
        return [self.hass.states.get(entity_id) for entity_id in room.light_entity_ids]

    def _all_available(self, room: RoomRecallState) -> bool:
        states = self._states(room)
        return bool(states) and all(
            state is not None and state.state not in STATE_UNAVAILABLE_VALUES
            for state in states
        )

    def _any_on(self, room: RoomRecallState) -> bool:
        return any(state is not None and state.state == STATE_ON for state in self._states(room))

    def _all_off(self, room: RoomRecallState) -> bool:
        states = self._states(room)
        return bool(states) and all(
            state is not None and state.state == STATE_OFF for state in states
        )

    @callback
    def _on_scene_resource_event(
        self, event_type: EventType, scene: HueScene | HueSmartScene
    ) -> None:
        room = self.rooms.get(scene.group.rid)
        if room and event_type == EventType.RESOURCE_DELETED and room.resume_scene_id == scene.id:
            room.resume_scene_id = None
            room.resume_scene_mode = None
            room.recall_armed = False
            self._schedule_save()
        self._notify()

    @callback
    def _on_room_resource_event(self, event_type: EventType, room: Any) -> None:
        self.hass.async_create_task(
            self.async_refresh_topology(),
            name=f"{self.entry.domain}_refresh_hue_rooms",
        )

    @callback
    def _on_entity_registry_update(self, event: Event) -> None:
        entity_id = event.data.get("entity_id", "")
        if not (entity_id.startswith("light.") or entity_id.startswith("switch.")):
            return
        entity_entry = er.async_get(self.hass).async_get(entity_id)
        was_tracked = (
            entity_id in self._entity_to_rooms or entity_id in self._power_entity_to_rooms
        )
        is_our_hue_light = (
            entity_entry is not None
            and entity_entry.platform == "hue"
            and entity_entry.config_entry_id == self.hue_entry.entry_id
        )
        is_power_candidate = (
            entity_entry is not None
            and self.power_label_id is not None
            and self.power_label_id in entity_entry.labels
        )
        if not was_tracked and not is_our_hue_light and not is_power_candidate:
            return
        self.hass.async_create_task(
            self.async_refresh_topology(),
            name=f"{self.entry.domain}_refresh_labels",
        )

    @callback
    def _on_label_registry_update(self, event: Event) -> None:
        # Label edits are rare. Re-resolve by name so deletion/recreation or a
        # changed label ID cannot leave enrollment stuck on stale registry data.
        self.hass.async_create_task(
            self.async_refresh_topology(),
            name=f"{self.entry.domain}_refresh_recall_label",
        )

    @staticmethod
    @callback
    def _cancel_recovery_timer(room: RoomRecallState) -> None:
        if room.recovery_handle:
            room.recovery_handle.cancel()
            room.recovery_handle = None

    @classmethod
    @callback
    def _cancel_room_timers(cls, room: RoomRecallState) -> None:
        cls._cancel_recovery_timer(room)
