"""Master automatic-recovery switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HueSceneRecallConfigEntry


async def async_setup_entry(
    hass, entry: HueSceneRecallConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the master automatic-recovery switch."""
    async_add_entities([HueRecallMasterSwitch(entry.runtime_data)])


class HueRecallMasterSwitch(SwitchEntity):
    """Globally enable/disable Hue connectivity/availability scene recovery."""

    _attr_should_poll = False
    _attr_icon = "mdi:restore"
    _attr_name = "Hue Recall Automatic Recovery"

    def __init__(self, manager) -> None:
        self.manager = manager
        self._attr_unique_id = f"{manager.hue_entry.entry_id}:master_recall"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.subscribe(self._handle_manager_update))

    def _handle_manager_update(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.manager.master_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.manager.async_set_master_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.manager.async_set_master_enabled(False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "label": "hueRecall",
            "enrolled_rooms": sum(
                1 for room in self.manager.rooms.values() if room.enrolled
            ),
            "total_rooms": len(self.manager.rooms),
            "recovery_trigger": "hue_connectivity_issue_or_ha_unavailable_recovery",
            "scene_source_of_truth": "hue_bridge",
            "diagnostics": "per_room_recorder_sensor",
            "behavior_when_off": "no_automatic_recovery",
        }
