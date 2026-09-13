"""Hue room controller select entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HueSceneRecallConfigEntry
from .manager import HueSceneRecallManager


async def async_setup_entry(
    hass, entry: HueSceneRecallConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    manager = entry.runtime_data
    added_room_ids: set[str] = set()

    @callback
    def _add_missing_room_entities() -> None:
        new_room_ids = [room_id for room_id in manager.room_ids() if room_id not in added_room_ids]
        if not new_room_ids:
            return
        added_room_ids.update(new_room_ids)
        async_add_entities(HueRecallSceneSelect(manager, room_id) for room_id in new_room_ids)

    _add_missing_room_entities()
    entry.async_on_unload(manager.subscribe(_add_missing_room_entities))


class HueRecallSceneSelect(SelectEntity):
    """Select a Hue controller and expose v0.3's durable recovery identity."""

    _attr_should_poll = False
    _attr_icon = "mdi:palette"

    def __init__(self, manager: HueSceneRecallManager, room_id: str) -> None:
        self.manager = manager
        self.room_id = room_id
        # Preserve v0.2.x unique IDs so upgrades keep the same entity.
        self._attr_unique_id = f"{manager.hue_entry.entry_id}:{room_id}:recall_scene"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.subscribe(self._handle_manager_update))

    @callback
    def _handle_manager_update(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def name(self) -> str:
        if self.room_id not in self.manager.rooms:
            return "Hue Recall Scene"
        return f"{self.manager.room_name(self.room_id)} Hue Recall Scene"

    @property
    def available(self) -> bool:
        return self.room_id in self.manager.rooms

    @property
    def options(self) -> list[str]:
        return self.manager.scene_options(self.room_id) if self.available else []

    @property
    def current_option(self) -> str | None:
        return self.manager.selected_option(self.room_id) if self.available else None

    async def async_select_option(self, option: str) -> None:
        scene_id = self.manager.scene_id_for_option(self.room_id, option)
        await self.manager.async_select_scene(self.room_id, scene_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.available:
            return {}
        room = self.manager.rooms[self.room_id]
        controller = self.manager.controller_from_cache(self.room_id)
        controller_id = controller[0] if controller else None
        controller_name = controller[1] if controller else None
        controller_type = controller[2] if controller else None
        # Keep legacy aliases but redefine them as controller identity, never as
        # "newest historical last_recall".
        return {
            "active_scene": controller_name,
            "active_scene_id": controller_id,
            "recall_scene_id": controller_id,
            "authoritative_scene": controller_name,
            "authoritative_scene_id": controller_id,
            "authoritative_scene_type": controller_type,
            "recovery_source": "persisted_controller_identity_plus_fresh_hue_bridge_pull",
            "controller_state": controller_type or "no_recoverable_controller",
            "recall_enrolled": room.enrolled,
            "master_enabled": self.manager.master_enabled,
            "labeled_light_count": self.manager.labeled_light_count(self.room_id),
            "total_hue_lights": room.total_hue_lights,
            "hue_room_id": room.room_id,
        }
