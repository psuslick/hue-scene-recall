"""Diagnostics support for Hue Scene Recall."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HueSceneRecallConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HueSceneRecallConfigEntry
) -> dict[str, Any]:
    """Return runtime diagnostics including the non-authoritative flight recorder."""
    return entry.runtime_data.diagnostic_snapshot()
