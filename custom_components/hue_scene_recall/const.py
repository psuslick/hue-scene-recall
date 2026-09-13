"""Constants for Hue Scene Recall."""

from homeassistant.const import Platform

DOMAIN = "hue_scene_recall"
NAME = "Hue Scene Recall"
VERSION = "0.3.2"

CONF_HUE_ENTRY_ID = "hue_entry_id"
HUE_DOMAIN = "hue"
RECALL_LABEL_NAME = "hueRecall"

PLATFORMS = [Platform.SELECT, Platform.SWITCH, Platform.SENSOR]

# Keep the existing Store version so v0.2.1's master_enabled value can be read.
# v0.3 adds a backwards-compatible "controllers" mapping to the same payload.
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = DOMAIN
STORAGE_SAVE_DELAY = 2

STATE_UNAVAILABLE_VALUES = {"unavailable", "unknown"}

# Recovery is per exact Hue light. This is deliberately short: live validation
# proved that the Bridge's connected event is sufficient to start a bounded
# recovery transaction, while a separate post-connect Light SSE update is not
# guaranteed.
RECOVERY_SETTLE_SECONDS = 1.0

# Coalesce related Hue Scene/Smart Scene/Light events before deciding whether an
# ordinary saved scene replaced a Smart Scene or a healthy unsaved appearance
# change cleared recoverable controller intent.
CONTROLLER_RECONCILE_SECONDS = 1.5

# Live Golden Hours validation showed the next child Scene is recalled at
# boundary - transition_duration, while individual light reports can settle for
# tens of seconds after the nominal boundary. v0.3 therefore defers recovery
# through a bounded post-boundary safety margin rather than interpolating.
SMART_SCENE_POST_BOUNDARY_SETTLE_SECONDS = 60.0

VERIFY_EVENT_TIMEOUT_SECONDS = 1.5
RECOVERY_RETRY_DELAY_SECONDS = 0.75
MAX_RECOVERY_ATTEMPTS = 2

BRIGHTNESS_TOLERANCE = 0.2
XY_TOLERANCE = 0.002
MIREK_TOLERANCE = 1
