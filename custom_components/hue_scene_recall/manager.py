"""Hue-authoritative scene recovery for Hue Scene Recall."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

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

from .const import (
    RECALL_LABEL_NAME,
    RECOVERY_SETTLE_SECONDS,
    STATE_UNAVAILABLE_VALUES,
    STORAGE_KEY_PREFIX,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)
from .recovery_logic import (
    RecoveryReason,
    impairment_reasons,
    next_connectivity_issue_pending,
    should_schedule_recovery,
)
from .scene_resolver import (
    AmbiguousHueSceneError,
    AuthoritativeScene,
    resolve_authoritative_scene,
)

_LOGGER = logging.getLogger(__name__)

Listener = Callable[[], None]


def _now() -> datetime:
    """Return an aware local timestamp for diagnostics."""
    return datetime.now().astimezone()


def _iso(value: datetime | None) -> str | None:
    """Serialize an optional diagnostic timestamp."""
    return value.isoformat() if value else None


def _connectivity_value(status: Any) -> str:
    """Normalize aiohue connectivity enum/string values."""
    value = getattr(status, "value", status)
    return str(value) if value is not None else "unknown"


@dataclass(slots=True)
class LightAuditState:
    """Runtime-only diagnostic timestamps for one Hue light."""

    entity_id: str
    hue_light_id: str
    connectivity_resource_id: str | None = None
    connectivity_status: str = "unknown"
    connectivity_issue_pending: bool = False
    ha_available: bool = False

    last_connectivity_issue_at: datetime | None = None
    last_connectivity_recovered_at: datetime | None = None
    last_ha_unavailable_at: datetime | None = None
    last_ha_available_at: datetime | None = None

    connectivity_issue_count: int = 0
    ha_unavailable_count: int = 0


@dataclass(slots=True)
class RoomRecallState:
    """Runtime state for one Hue room.

    No scene identity, light state, brightness, color, or power intent is persisted.
    Hue remains the source of truth for scenes. Connectivity/availability timestamps
    are diagnostics only and never select a scene.
    """

    room_id: str
    room_name: str
    light_entity_ids: tuple[str, ...] = ()
    connectivity_resource_ids: tuple[str, ...] = ()
    total_hue_lights: int = 0
    enrolled: bool = False

    all_available: bool = False
    connectivity_issue: bool = False
    impaired: bool = False
    pending_recovery_reasons: set[RecoveryReason] = field(default_factory=set)

    recovering: bool = False
    recovery_handle: asyncio.TimerHandle | None = field(default=None, repr=False)

    # Observability only. These are not recovery inputs and are not persisted.
    last_impairment_at: datetime | None = None
    last_impairment_reason: str | None = None
    last_recovery_trigger: str | None = None
    last_recovery_trigger_at: datetime | None = None
    last_recovery_scene_id: str | None = None
    last_recovery_scene_name: str | None = None
    last_recovery_scene_type: str | None = None
    last_recovery_at: datetime | None = None
    last_recovery_result: str | None = None


class HueSceneRecallManager:
    """Recover Hue scene intent after availability or connectivity recovery."""

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
        self._entity_to_rooms: dict[str, set[str]] = {}
        self._connectivity_to_rooms: dict[str, set[str]] = {}
        self._connectivity_to_entities: dict[str, set[str]] = {}
        self._light_audit: dict[str, LightAuditState] = {}

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
        """Initialize topology and recovery-condition tracking."""
        stored = await self._store.async_load() or {}
        self.master_enabled = bool(stored.get("master_enabled", True))

        label_registry = lr.async_get(self.hass)
        label = label_registry.async_get_label_by_name(RECALL_LABEL_NAME)
        if label is None:
            label = label_registry.async_create(
                RECALL_LABEL_NAME,
                icon="mdi:lightbulb-auto",
                description=(
                    "Hue light entities enrolled for scene recovery after either "
                    "Hue connectivity_issue or Home Assistant unavailable clears. "
                    "Hue Bridge scene state is queried only at recovery time and "
                    "remains authoritative."
                ),
            )
        self.label_id = label.label_id

        await self.async_refresh_topology(initial=True)

        # Scene events refresh UI display only; they never populate recovery memory.
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

        # Legacy v0.1.x room state remains discarded. Diagnostic timestamps are
        # intentionally left to Recorder rather than persisted in integration storage.
        self._schedule_save()
        self._notify()

    async def async_shutdown(self) -> None:
        """Release listeners and timers."""
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for room in self.rooms.values():
            self._cancel_recovery_timer(room)

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
        """Persist only the master enable switch, never Hue scene/audit state."""
        return {"master_enabled": self.master_enabled}

    async def async_set_master_enabled(self, enabled: bool) -> None:
        """Enable or disable automatic recovery globally for this bridge."""
        if self.master_enabled == enabled:
            return
        self.master_enabled = enabled
        self._schedule_save()
        self._notify()

    async def async_refresh_topology(self, *, initial: bool = False) -> None:
        """Refresh Hue room, light, connectivity, and label mappings."""
        ent_reg = er.async_get(self.hass)
        label_registry = lr.async_get(self.hass)
        label = label_registry.async_get_label_by_name(RECALL_LABEL_NAME)
        if label is not None:
            self.label_id = label.label_id
        elif self.label_id and label_registry.async_get_label(self.label_id) is None:
            self.label_id = None

        current_room_ids: set[str] = set()
        entity_to_rooms: dict[str, set[str]] = {}
        connectivity_to_rooms: dict[str, set[str]] = {}
        connectivity_to_entities: dict[str, set[str]] = {}
        current_entities: set[str] = set()

        for hue_room in self.api.groups.room:
            room_id = hue_room.id
            current_room_ids.add(room_id)
            room = self.rooms.get(room_id)
            if room is None:
                room = RoomRecallState(room_id=room_id, room_name=hue_room.metadata.name)
                self.rooms[room_id] = room
            else:
                room.room_name = hue_room.metadata.name

            hue_lights = self.api.groups.room.get_lights(room_id)
            entity_ids: list[str] = []
            connectivity_ids: set[str] = set()
            all_resolved = True

            for hue_light in hue_lights:
                entity_id = ent_reg.async_get_entity_id("light", "hue", hue_light.id)
                if entity_id is None:
                    all_resolved = False
                    continue

                current_entities.add(entity_id)
                entity_ids.append(entity_id)
                entity_to_rooms.setdefault(entity_id, set()).add(room_id)

                connectivity = self._connectivity_for_hue_light(hue_light.id)
                connectivity_id = connectivity.id if connectivity is not None else None
                connectivity_status = (
                    _connectivity_value(connectivity.status)
                    if connectivity is not None
                    else "unknown"
                )

                audit = self._light_audit.get(entity_id)
                if audit is None:
                    audit = LightAuditState(
                        entity_id=entity_id,
                        hue_light_id=hue_light.id,
                    )
                    self._light_audit[entity_id] = audit
                audit.hue_light_id = hue_light.id
                audit.connectivity_resource_id = connectivity_id
                audit.connectivity_status = connectivity_status
                # A topology refresh establishes a fresh baseline. Only an actual
                # connectivity_issue is considered armed at baseline.
                audit.connectivity_issue_pending = (
                    connectivity_status
                    == ConnectivityServiceStatus.CONNECTIVITY_ISSUE.value
                )
                audit.ha_available = self._entity_available(entity_id)

                if connectivity_id is not None:
                    connectivity_ids.add(connectivity_id)
                    connectivity_to_rooms.setdefault(connectivity_id, set()).add(room_id)
                    connectivity_to_entities.setdefault(connectivity_id, set()).add(
                        entity_id
                    )

            room.light_entity_ids = tuple(sorted(entity_ids))
            room.connectivity_resource_ids = tuple(sorted(connectivity_ids))
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

            # Establish the current baseline without manufacturing a recovery edge.
            reasons = self._room_impairment_reasons(room)
            room.all_available = "unavailable" not in reasons
            room.connectivity_issue = "connectivity_issue" in reasons
            room.impaired = bool(reasons)
            room.pending_recovery_reasons = set(reasons)

        for room_id in set(self.rooms) - current_room_ids:
            room = self.rooms.pop(room_id)
            self._cancel_recovery_timer(room)

        for entity_id in set(self._light_audit) - current_entities:
            self._light_audit.pop(entity_id, None)

        self._entity_to_rooms = entity_to_rooms
        self._connectivity_to_rooms = connectivity_to_rooms
        self._connectivity_to_entities = connectivity_to_entities
        self._reset_state_listener()
        self._notify()

        if not initial:
            _LOGGER.debug("Refreshed Hue Scene Recall topology")

    def _connectivity_for_hue_light(self, hue_light_id: str) -> ZigbeeConnectivity | None:
        """Return the Hue Zigbee connectivity resource for one Hue light."""
        try:
            device = self.api.lights.get_device(hue_light_id)
            if device is None:
                return None
            return self.api.devices.get_zigbee_connectivity(device.id)
        except (KeyError, TypeError):
            return None

    @callback
    def _reset_state_listener(self) -> None:
        if self._state_unsub:
            self._state_unsub()
            self._state_unsub = None
        entity_ids = tuple(self._entity_to_rooms)
        if entity_ids:
            self._state_unsub = async_track_state_change_event(
                self.hass, entity_ids, self._on_state_change
            )

    @callback
    def _on_state_change(self, event: Event) -> None:
        """Track only HA availability edges; ignore soft ON/OFF and light changes."""
        entity_id = event.data["entity_id"]
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        old_unavailable = self._state_unavailable(old_state)
        new_unavailable = self._state_unavailable(new_state)

        # Normal Hue state/scene/brightness changes never arm scene recovery.
        if old_unavailable == new_unavailable:
            return

        now = _now()
        audit = self._light_audit.get(entity_id)
        if audit is not None:
            audit.ha_available = not new_unavailable
            if new_unavailable:
                audit.last_ha_unavailable_at = now
                audit.ha_unavailable_count += 1
            else:
                audit.last_ha_available_at = now

        for room_id in tuple(self._entity_to_rooms.get(entity_id, ())):
            self._process_room_recovery_condition(room_id)

    @callback
    def _on_connectivity_resource_event(
        self, event_type: EventType, connectivity: ZigbeeConnectivity
    ) -> None:
        """Track Hue connectivity and arm recovery on connectivity_issue."""
        connectivity_id = getattr(connectivity, "id", None)
        if connectivity_id is None:
            return

        # Resource identity changes are topology changes, not proof of recovery.
        # Re-map them rather than interpreting deletion as issue clearance.
        if event_type == EventType.RESOURCE_DELETED:
            self.hass.async_create_task(
                self.async_refresh_topology(),
                name=f"{self.entry.domain}_refresh_hue_connectivity",
            )
            return

        entity_ids = tuple(self._connectivity_to_entities.get(connectivity_id, ()))
        if not entity_ids:
            if event_type == EventType.RESOURCE_ADDED:
                self.hass.async_create_task(
                    self.async_refresh_topology(),
                    name=f"{self.entry.domain}_refresh_hue_connectivity",
                )
            return

        new_status = _connectivity_value(connectivity.status)
        now = _now()
        changed = False

        for entity_id in entity_ids:
            audit = self._light_audit.get(entity_id)
            if audit is None:
                continue
            old_status = audit.connectivity_status
            if old_status == new_status:
                continue
            audit.connectivity_status = new_status
            changed = True

            was_pending = audit.connectivity_issue_pending
            audit.connectivity_issue_pending = next_connectivity_issue_pending(
                current_pending=was_pending, new_status=new_status
            )
            if new_status == ConnectivityServiceStatus.CONNECTIVITY_ISSUE.value:
                audit.last_connectivity_issue_at = now
                audit.connectivity_issue_count += 1
            elif was_pending and not audit.connectivity_issue_pending:
                audit.last_connectivity_recovered_at = now

        if not changed:
            return

        for room_id in tuple(self._connectivity_to_rooms.get(connectivity_id, ())):
            self._process_room_recovery_condition(room_id)

    @callback
    def _process_room_recovery_condition(self, room_id: str) -> None:
        """Arm on either impairment; recall once every impairment has cleared."""
        room = self.rooms.get(room_id)
        if room is None:
            return

        reasons = self._room_impairment_reasons(room)
        room.all_available = "unavailable" not in reasons
        room.connectivity_issue = "connectivity_issue" in reasons

        if reasons:
            newly_seen = reasons - room.pending_recovery_reasons
            room.pending_recovery_reasons.update(reasons)
            if not room.impaired or newly_seen:
                room.last_impairment_at = _now()
                room.last_impairment_reason = "+".join(
                    sorted(room.pending_recovery_reasons)
                )
                _LOGGER.debug(
                    "Hue room %s recovery armed by %s",
                    room.room_name,
                    room.last_impairment_reason,
                )
            room.impaired = True
            self._cancel_recovery_timer(room)
            self._notify()
            return

        if not should_schedule_recovery(
            was_impaired=room.impaired, current_reasons=reasons
        ):
            return

        trigger_reasons = set(room.pending_recovery_reasons)
        room.impaired = False
        room.pending_recovery_reasons.clear()
        room.last_recovery_trigger = "+".join(sorted(trigger_reasons)) or "unknown"
        room.last_recovery_trigger_at = _now()

        self._cancel_recovery_timer(room)
        room.recovery_handle = self.hass.loop.call_later(
            RECOVERY_SETTLE_SECONDS,
            lambda: self.hass.async_create_task(
                self.async_recover_scene(room_id),
                name=f"{self.entry.domain}_recover_{room_id}",
            ),
        )
        _LOGGER.debug(
            "Hue room %s recovered from %s; scheduled bridge scene query",
            room.room_name,
            room.last_recovery_trigger,
        )
        self._notify()

    async def async_recover_scene(self, room_id: str) -> None:
        """Query Hue once and recall its authoritative scene for a recovered room."""
        room = self.rooms.get(room_id)
        if room is None:
            return
        room.recovery_handle = None

        # Fail closed if either independent recovery condition has returned.
        if (
            not self.master_enabled
            or not room.enrolled
            or self._room_impairment_reasons(room)
        ):
            return

        room.recovering = True
        room.last_recovery_result = "querying_hue_bridge"
        self._notify()

        try:
            # Exactly one fresh GET establishes Hue's scene truth at recovery time.
            # We intentionally do not reuse locally persisted scene/light state.
            resources = await self.bridge.async_request_call(
                self.api.request,
                "get",
                "clip/v2/resource",
            )
            if not isinstance(resources, list):
                raise TypeError("Hue full-state query did not return a resource list")

            authoritative = resolve_authoritative_scene(resources, room_id)
            if authoritative is None:
                room.last_recovery_result = "no_hue_scene_to_recall"
                _LOGGER.debug(
                    "Hue room %s recovered but Hue reports no authoritative scene",
                    room.room_name,
                )
                return

            await self._async_recall_authoritative_scene(authoritative)
            room.last_recovery_scene_id = authoritative.scene_id
            room.last_recovery_scene_name = authoritative.name
            room.last_recovery_scene_type = authoritative.kind
            room.last_recovery_at = _now()
            room.last_recovery_result = "recalled"
            _LOGGER.debug(
                "Recovered Hue room %s by recalling Hue-authoritative %s %s",
                room.room_name,
                authoritative.kind,
                authoritative.name,
            )
        except AmbiguousHueSceneError:
            room.last_recovery_result = "ambiguous_active_smart_scene"
            _LOGGER.exception(
                "Hue reported ambiguous active Smart Scenes for %s; no recall sent",
                room.room_name,
            )
        except Exception:  # noqa: BLE001 - bridge wrapper surfaces Hue/transport errors
            room.last_recovery_result = "failed"
            _LOGGER.exception("Failed to recover Hue room %s", room.room_name)
        finally:
            room.recovering = False
            reasons = self._room_impairment_reasons(room)
            room.all_available = "unavailable" not in reasons
            room.connectivity_issue = "connectivity_issue" in reasons
            room.impaired = bool(reasons)
            if reasons:
                room.pending_recovery_reasons.update(reasons)
            self._notify()

    async def _async_recall_authoritative_scene(
        self, authoritative: AuthoritativeScene
    ) -> None:
        """Recall a scene by identity; Hue owns all scene contents and timing."""
        if authoritative.kind == "smart_scene":
            await self.bridge.async_request_call(
                self.api.scenes.smart_scene.recall,
                authoritative.scene_id,
            )
            return

        await self.bridge.async_request_call(
            self.api.scenes.scene.recall,
            authoritative.scene_id,
            dynamic=authoritative.dynamic,
        )

    async def async_select_scene(self, room_id: str, scene_id: str) -> None:
        """Select a real Hue scene without creating local scene memory."""
        scene = self._get_scene(scene_id)
        if scene is None or scene.group.rid != room_id:
            raise ValueError("Scene does not belong to this Hue room")

        if isinstance(scene, HueSmartScene):
            await self.bridge.async_request_call(
                self.api.scenes.smart_scene.recall,
                scene_id,
            )
        else:
            await self.bridge.async_request_call(
                self.api.scenes.scene.recall,
                scene_id,
                dynamic=bool(scene.auto_dynamic),
            )
        self._notify()

    def authoritative_scene_from_cache(
        self, room_id: str
    ) -> tuple[str, str, str] | None:
        """Return current bridge-cache scene identity for UI display only.

        Recovery never calls this method; recovery always performs one fresh bridge GET.
        """
        active_smart = [
            scene
            for scene in self.api.scenes
            if isinstance(scene, HueSmartScene)
            and scene.group.rid == room_id
            and scene.state == SmartSceneState.ACTIVE
        ]
        if len(active_smart) == 1:
            scene = active_smart[0]
            return scene.id, scene.metadata.name, "smart_scene"
        if len(active_smart) > 1:
            return None

        regular = [
            scene
            for scene in self.api.scenes
            if isinstance(scene, HueScene)
            and not isinstance(scene, HueSmartScene)
            and scene.group.rid == room_id
            and scene.status is not None
            and scene.status.last_recall is not None
        ]
        if not regular:
            return None
        scene = max(regular, key=lambda item: item.status.last_recall)
        return scene.id, scene.metadata.name, "scene"

    def room_ids(self) -> tuple[str, ...]:
        return tuple(self.rooms)

    def room_name(self, room_id: str) -> str:
        return self.rooms[room_id].room_name

    def scene_options(self, room_id: str) -> list[str]:
        option_to_id, _ = self._scene_option_maps(room_id)
        return list(option_to_id)

    def selected_option(self, room_id: str) -> str | None:
        authoritative = self.authoritative_scene_from_cache(room_id)
        if authoritative is None:
            return None
        scene_id, _, _ = authoritative
        _, id_to_option = self._scene_option_maps(room_id)
        return id_to_option.get(scene_id)

    def scene_id_for_option(self, room_id: str, option: str) -> str:
        option_to_id, _ = self._scene_option_maps(room_id)
        return option_to_id[option]

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

    def room_diagnostic_state(self, room_id: str) -> str:
        """Return compact current recovery/audit state for the room sensor."""
        room = self.rooms[room_id]
        if room.recovering:
            return "recovering"
        reasons = self._room_impairment_reasons(room)
        if reasons == {"connectivity_issue", "unavailable"}:
            return "connectivity_issue+unavailable"
        if "connectivity_issue" in reasons:
            return "connectivity_issue"
        if "unavailable" in reasons:
            return "unavailable"
        return "healthy"

    def room_diagnostic_attributes(self, room_id: str) -> dict[str, Any]:
        """Return Recorder-friendly timestamps and per-light recovery evidence."""
        room = self.rooms[room_id]
        lights: dict[str, dict[str, Any]] = {}
        for entity_id in room.light_entity_ids:
            audit = self._light_audit.get(entity_id)
            state = self.hass.states.get(entity_id)
            friendly_name = (
                state.attributes.get("friendly_name", entity_id) if state else entity_id
            )
            if audit is None:
                continue
            lights[entity_id] = {
                "name": friendly_name,
                "hue_light_id": audit.hue_light_id,
                "ha_available": audit.ha_available,
                "zigbee_connectivity": audit.connectivity_status,
                "connectivity_issue_pending": audit.connectivity_issue_pending,
                "connectivity_resource_id": audit.connectivity_resource_id,
                "last_connectivity_issue_at": _iso(audit.last_connectivity_issue_at),
                "last_connectivity_recovered_at": _iso(
                    audit.last_connectivity_recovered_at
                ),
                "last_ha_unavailable_at": _iso(audit.last_ha_unavailable_at),
                "last_ha_available_at": _iso(audit.last_ha_available_at),
                "connectivity_issue_count_since_load": audit.connectivity_issue_count,
                "ha_unavailable_count_since_load": audit.ha_unavailable_count,
            }

        return {
            "hue_room_id": room.room_id,
            "recall_enrolled": room.enrolled,
            "master_enabled": self.master_enabled,
            "all_available": room.all_available,
            "connectivity_issue": room.connectivity_issue,
            "impaired": room.impaired,
            "pending_recovery_reasons": sorted(room.pending_recovery_reasons),
            "last_impairment_at": _iso(room.last_impairment_at),
            "last_impairment_reason": room.last_impairment_reason,
            "last_recovery_trigger": room.last_recovery_trigger,
            "last_recovery_trigger_at": _iso(room.last_recovery_trigger_at),
            "last_recovery_at": _iso(room.last_recovery_at),
            "last_recovery_result": room.last_recovery_result,
            "last_recovery_scene": room.last_recovery_scene_name,
            "lights": lights,
        }

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

    @staticmethod
    def _state_unavailable(state: State | None) -> bool:
        return state is None or state.state in STATE_UNAVAILABLE_VALUES

    def _entity_available(self, entity_id: str) -> bool:
        return not self._state_unavailable(self.hass.states.get(entity_id))

    def _all_available(self, room: RoomRecallState) -> bool:
        states = self._states(room)
        return bool(states) and all(not self._state_unavailable(state) for state in states)

    def _has_connectivity_issue(self, room: RoomRecallState) -> bool:
        for entity_id in room.light_entity_ids:
            audit = self._light_audit.get(entity_id)
            if audit is not None and audit.connectivity_issue_pending:
                return True
        return False

    def _room_impairment_reasons(
        self, room: RoomRecallState
    ) -> frozenset[RecoveryReason]:
        return impairment_reasons(
            all_available=self._all_available(room),
            connectivity_issue=self._has_connectivity_issue(room),
        )

    @callback
    def _on_scene_resource_event(
        self, event_type: EventType, scene: HueScene | HueSmartScene
    ) -> None:
        """Refresh select/entity display only; never save scene state."""
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
        if not entity_id.startswith("light."):
            return
        entity_entry = er.async_get(self.hass).async_get(entity_id)
        was_tracked = entity_id in self._entity_to_rooms
        is_our_hue_light = (
            entity_entry is not None
            and entity_entry.platform == "hue"
            and entity_entry.config_entry_id == self.hue_entry.entry_id
        )
        if not was_tracked and not is_our_hue_light:
            return
        self.hass.async_create_task(
            self.async_refresh_topology(),
            name=f"{self.entry.domain}_refresh_labels",
        )

    @callback
    def _on_label_registry_update(self, event: Event) -> None:
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
