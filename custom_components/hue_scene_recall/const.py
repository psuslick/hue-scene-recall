"""Constants for Hue Scene Recall."""

from homeassistant.const import Platform

DOMAIN = "hue_scene_recall"
NAME = "Hue Scene Recall"
VERSION = "0.2.1"

CONF_HUE_ENTRY_ID = "hue_entry_id"
HUE_DOMAIN = "hue"
RECALL_LABEL_NAME = "hueRecall"

PLATFORMS = [Platform.SELECT, Platform.SWITCH, Platform.SENSOR]

# Storage now contains only the master enable/disable setting. Legacy v0.1.x
# room scene/power data is ignored and removed on the next delayed save.
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = DOMAIN
STORAGE_SAVE_DELAY = 30

# Either HA unavailability or Hue connectivity_issue arms recovery. The recall
# occurs only after all armed conditions have cleared. This brief settle delay gives
# the Hue device/room time to finish reconnecting before the one fresh bridge query.
RECOVERY_SETTLE_SECONDS = 2.5

STATE_UNAVAILABLE_VALUES = {"unavailable", "unknown"}
