# Hue Scene Recall v0.2.1 — Build / Validation Report

**Status:** PROPOSED build artifact  
**Built:** 2026-09-12  
**Live Home Assistant modified:** No

## Requested change

Add Hue Bridge `zigbee_connectivity.status == connectivity_issue` as a second automatic-recovery condition alongside Home Assistant Hue-light `unavailable`/`unknown`, while retaining the v0.2 Hue-authoritative scene lookup architecture and adding timestamp diagnostics suitable for later auditing.

## Implemented recovery semantics

The two independent recovery conditions are:

1. **HA availability condition:** any Hue light in the Hue room is `unavailable`, `unknown`, or missing from the HA state machine.
2. **Hue connectivity condition:** a mapped Hue light's Zigbee connectivity resource reports `connectivity_issue`.

Either condition **arms** recovery. Scene recall is intentionally not attempted while the device is impaired. A recall is scheduled only after every active condition clears.

For the Hue connectivity path, `connectivity_issue` remains pending until Hue explicitly reports `connected`. A transition from `connectivity_issue` to `disconnected`, `unidirectional_incoming`, or another non-connected status is not treated as recovery.

If the same outage generates both Hue `connectivity_issue` and HA `unavailable`, the room receives one recovery after both have cleared.

## Scene-source architecture

Unchanged from v0.2.0:

- Hue Bridge is the sole scene source of truth.
- The recovery path makes one fresh `GET /clip/v2/resource`.
- An active parent Smart Scene wins.
- Otherwise the regular room scene with newest Hue `status.last_recall` is recalled.
- No per-light brightness, color, ON/OFF state, or locally cached recovery scene is used.

## Connectivity implementation

The build uses the already-loaded Home Assistant Hue V2/aiohue connection. It does not poll and does not create another Hue session.

The implementation subscribes directly to:

`api.sensors.zigbee_connectivity.subscribe(...)`

and maps each Hue light to its owning Hue device's Zigbee connectivity resource through the aiohue device/light controllers.

### Version compatibility checked

Home Assistant 2026.8's changelog records the Hue dependency bump to **aiohue 4.9.0**. aiohue 4.9.0 source confirms:

- `ConnectivityServiceStatus.CONNECTIVITY_ISSUE = "connectivity_issue"`;
- `ConnectivityServiceStatus.CONNECTED = "connected"`;
- `ZigbeeConnectivity.status` uses that enum;
- the grouped sensor controller exposes `zigbee_connectivity`;
- resource controllers support event subscriptions;
- device controller exposes `get_zigbee_connectivity(device_id)`;
- the generic resource controller exposes `get_device(resource_id)`.

## Audit instrumentation

Added one diagnostic sensor per Hue room:

`<room> Hue Recall Recovery Diagnostics`

It is enabled by default and marked as `EntityCategory.DIAGNOSTIC`.

Current sensor states:

- `healthy`
- `connectivity_issue`
- `unavailable`
- `connectivity_issue+unavailable`
- `recovering`

Per-light attributes include:

- current HA availability;
- current raw Hue Zigbee connectivity status;
- whether a `connectivity_issue` recovery is pending;
- connectivity resource ID;
- `last_connectivity_issue_at`;
- `last_connectivity_recovered_at`;
- `last_ha_unavailable_at`;
- `last_ha_available_at`;
- event counts since integration load.

Room attributes include the pending recovery reasons and last impairment/recovery trigger timestamps.

These values are diagnostics only. They do not participate in scene selection. The integration persists only the master enable switch; Recorder provides the historical audit trail.

## Files changed from v0.2.0

- `custom_components/hue_scene_recall/manager.py`
  - direct Hue connectivity subscription;
  - dual-condition recovery gate;
  - per-light runtime audit state;
  - room diagnostics data.
- `custom_components/hue_scene_recall/recovery_logic.py`
  - pure dual-condition gate logic;
  - connectivity-issue pending state that clears only on `connected`.
- `custom_components/hue_scene_recall/sensor.py`
  - Recorder-friendly per-room diagnostics entity.
- `custom_components/hue_scene_recall/const.py`
  - version `0.2.1`;
  - adds Sensor platform.
- `custom_components/hue_scene_recall/select.py`
  - exposes connectivity/recovery trigger diagnostics.
- `custom_components/hue_scene_recall/switch.py`
  - updated recovery-trigger description.
- `custom_components/hue_scene_recall/manifest.json`
  - version `0.2.1`.
- `README.md`, `CHANGELOG.md`
  - architecture and audit documentation.
- tests updated/added.

## Validation performed

### Python compilation

All integration and test Python files compile successfully with `compileall`.

### Regression tests

`python -m unittest discover -s tests -v`

**20 tests passed.** Coverage includes:

- both recovery conditions independently arm recovery;
- both can remain active together;
- recall waits until all conditions clear;
- Hue connectivity issue clears only on explicit `connected`;
- healthy→healthy does not trigger recovery;
- direct Hue connectivity subscription exists;
- diagnostic Sensor platform exists;
- soft ON/OFF remains excluded;
- local power/scene memory remains removed;
- exactly one fresh Hue full-resource GET remains in the recovery path;
- Smart Scene parent priority;
- regular scene `last_recall` ordering;
- dynamic-scene behavior;
- fail-safe handling of ambiguous active Smart Scenes.

## Not yet verified

Because this package has **not** been installed on the live Home Assistant system, the following remain unverified:

- the exact observed delay from Isaac's physical switch OFF to Hue `connectivity_issue`;
- whether every real physical cut reports `connectivity_issue` before HA `unavailable`;
- the false-positive rate of `connectivity_issue` when mains power remains present;
- live Recorder history population for the new diagnostic sensors;
- live Smart Scene recovery through the new dual-condition path.

Those are precisely the observations the new diagnostic sensor is intended to make measurable after deployment.
