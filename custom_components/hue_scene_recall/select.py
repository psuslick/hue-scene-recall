"""Hue room scene select entities."""

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
    """Set up a Hue-authoritative scene select for every Hue room."""
    manager = entry.runtime_data
    added_room_ids: set[str] = set()

    @callback
    def _add_missing_room_entities() -> None:
        new_room_ids = [
            room_id for room_id in manager.room_ids() if room_id not in added_room_ids
        ]
        if not new_room_ids:
            return
        added_room_ids.update(new_room_ids)
        async_add_entities(
            HueRecallSceneSelect(manager, room_id) for room_id in new_room_ids
        )

    _add_missing_room_entities()
    entry.async_on_unload(manager.subscribe(_add_missing_room_entities))


class HueRecallSceneSelect(SelectEntity):
    """Select exposing Hue's authoritative room scene identity."""

    _attr_should_poll = False
    _attr_icon = "mdi:palette"

    def __init__(self, manager: HueSceneRecallManager, room_id: str) -> None:
        self.manager = manager
        self.room_id = room_id
        self._attr_unique_id = f"{manager.hue_entry.entry_id}:{room_id}:recall_scene"

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager changes."""
        self.async_on_remove(self.manager.subscribe(self._handle_manager_update))

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
        if not self.available:
            return []
        return self.manager.scene_options(self.room_id)

    @property
    def current_option(self) -> str | None:
        if not self.available:
            return None
        return self.manager.selected_option(self.room_id)

    async def async_select_option(self, option: str) -> None:
        scene_id = self.manager.scene_id_for_option(self.room_id, option)
        await self.manager.async_select_scene(self.room_id, scene_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.available:
            return {}
        room = self.manager.rooms[self.room_id]
        authoritative = self.manager.authoritative_scene_from_cache(self.room_id)
        authoritative_id = authoritative[0] if authoritative else None
        authoritative_name = authoritative[1] if authoritative else None
        authoritative_type = authoritative[2] if authoritative else None
        return {
            # Keep these legacy attribute names as compatibility aliases, but they
            # are now bridge-derived rather than locally persisted recovery state.
            "active_scene": authoritative_name,
            "active_scene_id": authoritative_id,
            "recall_scene_id": authoritative_id,
            "authoritative_scene": authoritative_name,
            "authoritative_scene_id": authoritative_id,
            "authoritative_scene_type": authoritative_type,
            "recovery_source": "fresh_hue_bridge_query",
            "recall_enrolled": room.enrolled,
            "master_enabled": self.manager.master_enabled,
            "recovering": room.recovering,
            "all_available": room.all_available,
            "connectivity_issue": room.connectivity_issue,
            "recovery_impaired": room.impaired,
            "pending_recovery_reasons": sorted(room.pending_recovery_reasons),
            "last_recovery_trigger": room.last_recovery_trigger,
            "last_recovery_trigger_at": (
                room.last_recovery_trigger_at.isoformat()
                if room.last_recovery_trigger_at
                else None
            ),
            "last_recovery_scene": room.last_recovery_scene_name,
            "last_recovery_scene_id": room.last_recovery_scene_id,
            "last_recovery_scene_type": room.last_recovery_scene_type,
            "last_recovery_at": (
                room.last_recovery_at.isoformat() if room.last_recovery_at else None
            ),
            "last_recovery_result": room.last_recovery_result,
            "labeled_light_count": self.manager.labeled_light_count(self.room_id),
            "total_hue_lights": room.total_hue_lights,
            "hue_room_id": room.room_id,
        }
