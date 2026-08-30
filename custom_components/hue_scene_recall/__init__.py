"""Hue Scene Recall integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_HUE_ENTRY_ID, PLATFORMS
from .manager import HueSceneRecallManager


type HueSceneRecallConfigEntry = ConfigEntry[HueSceneRecallManager]


async def async_setup_entry(
    hass: HomeAssistant, entry: HueSceneRecallConfigEntry
) -> bool:
    """Set up Hue Scene Recall from a config entry."""
    hue_entry_id = entry.data[CONF_HUE_ENTRY_ID]
    hue_entry = hass.config_entries.async_get_entry(hue_entry_id)
    if hue_entry is None:
        raise ConfigEntryNotReady("Configured Hue bridge entry no longer exists")
    if hue_entry.data.get(CONF_API_VERSION, 1) != 2:
        raise ConfigEntryNotReady("Hue Scene Recall requires a Hue V2 bridge")
    if not hasattr(hue_entry, "runtime_data") or hue_entry.runtime_data is None:
        raise ConfigEntryNotReady("Hue bridge is not loaded yet")

    bridge = hue_entry.runtime_data
    if getattr(bridge, "api_version", 1) != 2:
        raise ConfigEntryNotReady("Hue Scene Recall requires a Hue V2 bridge")

    manager = HueSceneRecallManager(hass, entry, hue_entry, bridge)
    await manager.async_setup()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HueSceneRecallConfigEntry
) -> bool:
    """Unload Hue Scene Recall."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok
