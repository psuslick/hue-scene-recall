"""Constants for Hue Scene Recall."""

from homeassistant.const import Platform

DOMAIN = "hue_scene_recall"
NAME = "Hue Scene Recall"
VERSION = "0.1.3"

CONF_HUE_ENTRY_ID = "hue_entry_id"
HUE_DOMAIN = "hue"
RECALL_LABEL_NAME = "hueRecall"
RECALL_POWER_LABEL_NAME = "hueRecallPower"

PLATFORMS = [Platform.SELECT, Platform.SWITCH]

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = DOMAIN
STORAGE_SAVE_DELAY = 30
RECOVERY_SETTLE_SECONDS = 2.5
POWER_RECOVERY_SETTLE_SECONDS = 6.0
POWER_RECOVERY_RETRY_SECONDS = 3.0
POWER_RECOVERY_MAX_ATTEMPTS = 5

STATE_UNAVAILABLE_VALUES = {"unavailable", "unknown"}
