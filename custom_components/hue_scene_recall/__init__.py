"""Hue Scene Recall integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .brightness_cap import HueBrightnessCapController
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

    # Reuse Home Assistant's already-authenticated aiohue client/cache/SSE
    # stream rather than opening a second Hue connection.
    manager = HueSceneRecallManager(hass, entry, hue_entry, bridge)
    await manager.async_setup()

    # The brightness cap is intentionally a separate coordinator. Recovery
    # remains Bridge-authoritative and exact-light; the cap overlays saved Hue
    # Scene brightness reversibly and uses direct light writes only when doing so
    # cannot disrupt an active Smart Scene.
    cap = HueBrightnessCapController(hass, entry, manager)
    manager.brightness_cap = cap
    try:
        await cap.async_setup()
    except Exception:
        await manager.async_shutdown()
        raise
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HueSceneRecallConfigEntry
) -> bool:
    """Unload Hue Scene Recall."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        cap = getattr(entry.runtime_data, "brightness_cap", None)
        if cap is not None:
            await cap.async_shutdown()
        await entry.runtime_data.async_shutdown()
    return unload_ok
