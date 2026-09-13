"""Constants for Hue Scene Recall."""

from homeassistant.const import Platform

DOMAIN = "hue_scene_recall"
NAME = "Hue Scene Recall"
VERSION = "0.3.4"

CONF_HUE_ENTRY_ID = "hue_entry_id"
HUE_DOMAIN = "hue"
RECALL_LABEL_NAME = "hueRecall"

PLATFORMS = [Platform.SELECT, Platform.SWITCH, Platform.SENSOR, Platform.NUMBER]

# Keep the existing Store version so v0.2.1's master_enabled value can be read.
# v0.3 adds a backwards-compatible "controllers" mapping to the same payload.
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = DOMAIN
STORAGE_SAVE_DELAY = 2

# Brightness-cap persistence is deliberately separate from controller identity.
# It stores only the user's cap and the original brightness values needed to
# reverse a temporary Hue Scene overlay. It never stores power, color, schedule,
# or recovery desired-state payloads.
BRIGHTNESS_CAP_STORAGE_VERSION = 1
BRIGHTNESS_CAP_STORAGE_KEY_PREFIX = f"{DOMAIN}.brightness_cap"
BRIGHTNESS_CAP_MIN = 1.0
BRIGHTNESS_CAP_MAX = 100.0
BRIGHTNESS_CAP_DEFAULT = 100.0
BRIGHTNESS_CAP_SCENE_SETTLE_SECONDS = 0.75
BRIGHTNESS_CAP_LIGHT_SETTLE_SECONDS = 0.75
BRIGHTNESS_CAP_INTERNAL_WRITE_SECONDS = 4.0
# Same-active-Smart reapplication can emit intermediate transition events for
# the Smart Scene transition duration. Keep those cap-generated events out of
# HueRecall manual-controller classification, with a bounded settling margin.
BRIGHTNESS_CAP_SMART_REAPPLY_GUARD_MARGIN_SECONDS = 15.0
BRIGHTNESS_CAP_VERIFY_TIMEOUT_SECONDS = 1.5
BRIGHTNESS_CAP_MAX_WRITE_ATTEMPTS = 2

# Detailed diagnostics are RAM-first to minimize microSD writes. The separate
# flight-recorder Store is checkpointed at most twice per day during normal
# operation, and flushed on clean integration unload / Home Assistant stop.
DIAGNOSTIC_STORAGE_KEY_PREFIX = f"{DOMAIN}.diagnostics"
DIAGNOSTIC_STORAGE_VERSION = 1
DIAGNOSTIC_CHECKPOINT_SECONDS = 12 * 60 * 60
DIAGNOSTIC_MAX_EVENTS = 1000

STATE_UNAVAILABLE_VALUES = {"unavailable", "unknown"}

# Base settle for non-connectivity recovery paths. Physical-power recoveries
# have an additional exact-light post-connect appearance-readiness gate below.
RECOVERY_SETTLE_SECONDS = 1.0

# Live physical-power validation proved Hue can report zigbee_connectivity=
# connected several seconds before the Light resource carries its real power-up
# appearance. For connectivity_issue recoveries, wait for an exact-light
# appearance event; if none arrives, perform a bounded fallback read only after
# this window.
POST_CONNECT_APPEARANCE_TIMEOUT_SECONDS = 12.0

# Delayed power-up reports must not be classified as manual overrides immediately
# after a recovery. This is classification suppression only; it is never used as
# desired-state authority.
POST_CONNECT_MANUAL_GUARD_SECONDS = 20.0

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
