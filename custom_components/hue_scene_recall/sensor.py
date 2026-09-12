"""Recovery diagnostics sensors for Hue Scene Recall."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HueSceneRecallConfigEntry
from .manager import HueSceneRecallManager


async def async_setup_entry(
    hass, entry: HueSceneRecallConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up one Recorder-friendly recovery diagnostics sensor per Hue room."""
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
            HueRecallRecoveryDiagnosticsSensor(manager, room_id)
            for room_id in new_room_ids
        )

    _add_missing_room_entities()
    entry.async_on_unload(manager.subscribe(_add_missing_room_entities))


class HueRecallRecoveryDiagnosticsSensor(SensorEntity):
    """Expose recovery-condition state and exact event timestamps."""

    _attr_should_poll = False
    _attr_icon = "mdi:access-point-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: HueSceneRecallManager, room_id: str) -> None:
        self.manager = manager
        self.room_id = room_id
        self._attr_unique_id = (
            f"{manager.hue_entry.entry_id}:{room_id}:recovery_diagnostics"
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager changes so Recorder receives every audit edge."""
        self.async_on_remove(self.manager.subscribe(self._handle_manager_update))

    @callback
    def _handle_manager_update(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def name(self) -> str:
        if self.room_id not in self.manager.rooms:
            return "Hue Recall Recovery Diagnostics"
        return f"{self.manager.room_name(self.room_id)} Hue Recall Recovery Diagnostics"

    @property
    def available(self) -> bool:
        return self.room_id in self.manager.rooms

    @property
    def native_value(self) -> str | None:
        if not self.available:
            return None
        return self.manager.room_diagnostic_state(self.room_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.available:
            return {}
        return self.manager.room_diagnostic_attributes(self.room_id)
