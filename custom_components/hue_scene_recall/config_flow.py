"""Config flow for Hue Scene Recall."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_API_VERSION

from .const import CONF_HUE_ENTRY_ID, DOMAIN, HUE_DOMAIN


class HueSceneRecallConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hue Scene Recall."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the Home Assistant Hue bridge to augment."""
        configured = {
            entry.data.get(CONF_HUE_ENTRY_ID) for entry in self._async_current_entries()
        }
        hue_entries = [
            entry
            for entry in self.hass.config_entries.async_entries(HUE_DOMAIN)
            if entry.entry_id not in configured
            and entry.data.get(CONF_API_VERSION, 1) == 2
        ]

        if not hue_entries:
            return self.async_abort(reason="no_available_hue_v2_bridge")

        if user_input is not None:
            hue_entry_id = user_input[CONF_HUE_ENTRY_ID]
            hue_entry = self.hass.config_entries.async_get_entry(hue_entry_id)
            if hue_entry is None or hue_entry.domain != HUE_DOMAIN:
                return self.async_abort(reason="hue_bridge_missing")

            await self.async_set_unique_id(f"hue:{hue_entry_id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Hue Scene Recall — {hue_entry.title}",
                data={CONF_HUE_ENTRY_ID: hue_entry_id},
            )

        if len(hue_entries) == 1:
            hue_entry = hue_entries[0]
            await self.async_set_unique_id(f"hue:{hue_entry.entry_id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Hue Scene Recall — {hue_entry.title}",
                data={CONF_HUE_ENTRY_ID: hue_entry.entry_id},
            )

        choices = {entry.entry_id: entry.title for entry in hue_entries}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HUE_ENTRY_ID): vol.In(choices)}),
        )
