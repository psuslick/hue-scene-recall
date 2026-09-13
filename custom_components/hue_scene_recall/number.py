"""Hue maximum-brightness control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HueSceneRecallConfigEntry
from .const import BRIGHTNESS_CAP_MAX, BRIGHTNESS_CAP_MIN


async def async_setup_entry(
    hass, entry: HueSceneRecallConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the house-wide Hue maximum-brightness number."""
    async_add_entities([HueMaximumBrightnessNumber(entry.runtime_data.brightness_cap)])


class HueMaximumBrightnessNumber(NumberEntity):
    """House-wide maximum brightness for physical Hue V2 lights."""

    _attr_should_poll = False
    _attr_icon = "mdi:brightness-percent"
    _attr_name = "Hue Maximum Brightness"
    _attr_native_min_value = BRIGHTNESS_CAP_MIN
    _attr_native_max_value = BRIGHTNESS_CAP_MAX
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, controller) -> None:
        self.controller = controller
        self._attr_unique_id = f"{controller.manager.hue_entry.entry_id}:maximum_brightness"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.subscribe(self._handle_update))

    def _handle_update(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self.controller.maximum_brightness

    async def async_set_native_value(self, value: float) -> None:
        await self.controller.async_set_maximum_brightness(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.controller.diagnostic_attributes()
