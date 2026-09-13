# Hue Scene Recall v0.3.3

Hue Scene Recall restores **saved Hue appearance intent** to an individual Hue bulb after that exact bulb recovers from a physical-power/connectivity failure, without changing whether the bulb is on or off.

v0.3.3 is the next correction after live v0.3.2 validation proved that Hue can report a bulb `connected` several seconds before its Light resource contains the bulb's real post-power-up appearance.

## Live failure v0.3.3 addresses

During the v0.3.2 Basement Bathroom test, both bulbs shared the correct Golden Hours 5 → Nighttime outage episode. A19 02 reconnected first. HueRecall waited one second, read stale Bridge state, and incorrectly returned `already_correct`. About seven seconds after Hue had reported `connected`, that bulb finally reported its real power-up state at about 50% / 2732 K. A19 01 had meanwhile been repaired and verified correctly. The delayed A19 02 report was then misclassified as an unsaved manual appearance change and cleared the room controller journal.

v0.3.3 fixes both failure points.

## Recovery path

For one exact Hue Light RID:

```text
HA unavailable and/or Hue connectivity_issue
        ↓
arm exact-light recovery + shared room outage episode
        ↓
Hue connected + HA available
        ↓
for connectivity-triggered recovery:
wait for this exact Light RID's first material appearance update
        ↓
if none arrives by 12 s from reconnect: bounded fresh-read fallback
        ↓
fresh Bridge controller/Scene resolution
        ↓
fresh exact-light read
        ↓
already correct? → finish
otherwise exact Light PUT containing appearance only
        ↓
exact-light verification event, then exact GET fallback
        ↓
bounded retry with fresh resolution
```

A delayed power-up report remains part of the outage for a 20-second classification guard. It cannot be treated as a manual override during that window.

## Shared Smart Scene outage episode

The first impaired bulb in a room captures runtime-only controller identity for the outage:

- Smart Scene controller RID;
- active timeslot id;
- effective child Scene RID;
- child activation mode when known;
- capture timestamp.

No brightness, color, color temperature, or power is stored in the recovery episode. Each recovering bulb still re-pulls the current saved child Scene definition from the Bridge and extracts only its own exact-light action.

## Diagnostic flight recorder

v0.3.3 adds a passive, bounded diagnostic journal intended to make normal household use useful for bug discovery without staging a special test.

The flight recorder stores up to 1,000 recent events in RAM, including:

- HA/Hue impairment and reconnect events;
- outage episode creation/completion;
- controller changes;
- desired controller/effective Scene resolution;
- observed pre-write appearance;
- desired appearance;
- exact-light write and verification milestones;
- transaction outcomes;
- post-connect appearance-readiness timeouts;
- warning/anomaly events.

Diagnostic appearance observations may include `on`, brightness, color, and color temperature. They are evidence only. **Recovery code never reads flight-recorder appearance as desired state.**

The complete recorder is exposed through Home Assistant config-entry diagnostics. Room diagnostic sensors expose only small status/retention summaries rather than embedding the full event log into Recorder history.

## microSD-aware persistence

This build is deliberately RAM-first for Home Assistant installations using microSD storage.

- No diagnostic event causes an immediate disk write.
- When dirty, the diagnostic Store is checkpointed every **12 hours**.
- A dirty buffer is flushed on a clean Home Assistant stop/restart.
- A dirty buffer is also flushed when Hue Scene Recall is unloaded/reloaded.
- Unexpected power loss can therefore lose diagnostic events since the last checkpoint, but routine operation generates only two diagnostic Store writes per day plus clean lifecycle flushes.

The functional controller journal remains separate and small.

## Automatic-write safety

Automatic recovery:

- never includes `on`;
- never recalls a whole Hue Scene;
- never recalls a Smart Scene;
- never writes a grouped light;
- writes only `/clip/v2/resource/light/{exact_light_rid}`;
- fails closed when desired state cannot be established safely.

Manual use of the Hue Scene Recall select entity can still recall a Scene/Smart Scene because that is an explicit user command rather than automatic fault recovery.

## Upgrade

v0.3.3 keeps the existing integration config entry, Hue `hueRecall` enrollment, entity unique IDs, and functional controller-journal format. The diagnostic flight recorder uses a new separate Store key and needs no migration from v0.3.2.

At build time v0.3.2 is the live installed version and has **failed end-to-end verification** due to the stale post-connect read described above. v0.3.3 is a build artifact until installed and live-tested.
