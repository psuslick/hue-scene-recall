"""Constants for Hue Scene Recall."""

from homeassistant.const import Platform

DOMAIN = "hue_scene_recall"
NAME = "Hue Scene Recall"
VERSION = "0.1.0"

CONF_HUE_ENTRY_ID = "hue_entry_id"
HUE_DOMAIN = "hue"
RECALL_LABEL_NAME = "hueRecall"

PLATFORMS = [Platform.SELECT, Platform.SWITCH]

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = DOMAIN
STORAGE_SAVE_DELAY = 30
RECOVERY_SETTLE_SECONDS = 2.5
MANUAL_DIVERGENCE_DELAY = 5.0

STATE_UNAVAILABLE_VALUES = {"unavailable", "unknown"}
