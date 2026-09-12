# Hue Scene Recall v0.2.1

Hue Scene Recall restores the **Hue Bridge's scene intent** after a labeled Hue room recovers from either of the two failure signals requested for recovery:

1. Home Assistant reports one of the room's Hue lights as `unavailable`/`unknown`.
2. The Hue Bridge reports that light's `zigbee_connectivity.status` as `connectivity_issue`.

These are independent recovery signals. Neither changes the scene source of truth: **Hue Bridge remains the only scene authority.**

## v0.2.1 recovery path

```text
HA light unavailable/unknown ──────┐
                                  ├─> arm room recovery
Hue connectivity_issue ───────────┘

wait until every armed condition has cleared
        ↓
2.5 second settle
        ↓
one fresh Hue Bridge GET: /clip/v2/resource
        ↓
active Smart Scene? → recall the parent Smart Scene
otherwise → recall the room's regular scene with newest Hue status.last_recall
        ↓
exit
```

A `connectivity_issue` does **not** send a recall while the light is impaired. It arms recovery. The recall is sent when Hue reports the issue cleared and the HA light is also available. Likewise, `unavailable` arms recovery and the recall waits for HA availability to return.

If both signals occur during the same physical outage, v0.2.1 produces **one recovery**, after both have cleared. It does not recall twice.

## Why add Hue connectivity as a trigger?

Recorder history for Isaac's ceiling lights showed that HA typically does not mark the bulbs `unavailable` until roughly 1–2.5 minutes after their upstream physical switch removes power. Hue's `zigbee_connectivity` event stream can potentially expose the loss sooner.

v0.2.1 therefore listens directly to aiohue's `zigbee_connectivity` resource updates rather than polling. `connectivity_issue` is the only Hue connectivity status that arms recovery in this build, matching the requested behavior. Other Hue statuses remain visible in diagnostics but do not independently trigger recall.

## Recovery diagnostics / auditing

v0.2.1 adds one enabled diagnostic sensor per Hue room:

`<room> Hue Recall Recovery Diagnostics`

Its state is one of:

- `healthy`
- `connectivity_issue`
- `unavailable`
- `connectivity_issue+unavailable`
- `recovering`

The entity is a normal Home Assistant diagnostic sensor, so Recorder can retain its state/attribute transitions. Its per-light attributes include:

- current HA availability;
- current Hue Zigbee connectivity status;
- Hue connectivity resource ID;
- `last_connectivity_issue_at`;
- `last_connectivity_recovered_at`;
- `last_ha_unavailable_at`;
- `last_ha_available_at`;
- counts of connectivity issues and HA-unavailable edges since the integration loaded.

Room-level attributes also expose:

- current pending recovery reasons;
- last impairment timestamp/reason;
- last recovery trigger (`connectivity_issue`, `unavailable`, or both);
- last recovery-trigger timestamp;
- last recovery result and scene.

These diagnostics are **observational only**. They never determine which scene is recalled. The integration does not persist these timestamps into its own state store; Recorder is the audit trail.

## Scene architecture remains unchanged from v0.2.0

Hue remains authoritative for:

- regular scene definitions;
- regular scene `status.last_recall` metadata;
- Smart Scene identity and active state;
- Smart Scene schedules and current timeslot;
- whether a regular scene is configured `auto_dynamic`.

Hue Scene Recall does not cache brightness, color, soft ON/OFF state, or a local recovery scene.

The following remain normal Hue operation and do not arm recovery:

- soft `on → off`;
- soft `off → on`;
- brightness changes;
- color changes;
- ordinary scene changes;
- Hue timers;
- Smart Scene timeslot changes.

## Smart Scenes / Golden Hours

If Hue reports an active Smart Scene when the room recovers, the integration recalls the **parent Smart Scene**. Hue then chooses the correct current timeslot. It does not recall a cached child scene.

## Regular scenes

If no Smart Scene is active, the integration chooses the regular scene in that Hue room with the newest bridge-maintained `status.last_recall` timestamp and recalls Hue's current saved definition.

## Enrollment

The existing `hueRecall` entity label remains the enrollment mechanism. Automatic recovery is enabled for a Hue room only when every Hue light in that Hue room resolves to a Home Assistant Hue light entity and carries `hueRecall`.

The legacy `hueRecallPower` label remains untouched but is not used by this integration.

## Entities

### `switch.hue_recall_automatic_recovery`

Master automatic-recovery switch.

### `<room> Hue Recall Scene`

Manual Hue scene selector. The selected scene remains derived from Hue bridge metadata, not locally persisted recovery memory.

### `<room> Hue Recall Recovery Diagnostics`

Recorder-friendly audit sensor described above.

## Storage

The integration persists only the master automatic-recovery switch. It does not persist scene state, light state, ON/OFF intent, or audit timestamps.

## Requirements

- Home Assistant 2026.8.0 or newer
- Built-in Philips Hue integration using Hue V2
- aiohue supplied by that Home Assistant release

Home Assistant 2026.8 includes aiohue 4.9.0. That release exposes Hue V2 `zigbee_connectivity` resources through an event-driven controller, and the connectivity model includes `connected`, `disconnected`, `connectivity_issue`, `unidirectional_incoming`, and `pending_discovery` statuses.

## Installation

This ZIP is a **PROPOSED build artifact**. It has not been installed on the live Home Assistant instance.

For HACS/repository deployment, replace the repository contents with this build, publish/tag `v0.2.1`, refresh HACS repository information, update Hue Scene Recall, then restart Home Assistant.

## Validation included

The package includes regression checks for:

- active Smart Scene priority and parent recall;
- newest regular Hue `last_recall` selection;
- Hue `auto_dynamic` preservation;
- no local scene/power recovery memory;
- direct Hue `zigbee_connectivity` event subscription;
- `connectivity_issue` as an independent recovery-arming condition;
- HA `unavailable` as an independent recovery-arming condition;
- waiting until **all** active recovery conditions clear before recall;
- one fresh full-resource Hue GET per recovery;
- Recorder-friendly diagnostic entities/timestamps;
- soft ON/OFF changes remaining excluded from recovery logic.
