# Hue Scene Recall v0.3.1

Hue Scene Recall restores **saved Hue appearance intent** to an individual Hue bulb after that bulb recovers from a physical-power/connectivity failure, without changing whether the bulb is on or off.

v0.3.1 is a targeted correction to the v0.3.0 controller-journal / exact-light architecture. The first live v0.3.0 outage test verified per-light impairment and reconnect handling but exposed an overnight Smart Scene resolver bug: at 03:35 Sunday the Hue Bridge legitimately reported the active Golden Hours timeslot as Saturday's 22:00 child. v0.3.0 rejected that valid Hue state before any recovery write.

## Core rule

```text
exact Hue light becomes impaired
        ↓
that exact light becomes connected/available again
        ↓
short settle
        ↓
pull current controller + schedule + saved Scene actions from Hue Bridge
        ↓
resolve the exact saved action for this Light RID
        ↓
strip power (`on`) structurally
        ↓
pre-compare current Bridge light state
        ↓
PUT appearance only to /clip/v2/resource/light/{rid}
        ↓
verify by exact-light SSE, then exact GET fallback
```

Hue Scene Recall never uses a whole Scene recall as its automatic-recovery actuator and never writes a grouped light during recovery.

## What is persisted

Only:

- the master automatic-recovery switch; and
- per Hue room, a Hue controller identity (`scene` RID or `smart_scene` RID).

It does **not** persist brightness, color, color temperature, power state, Smart Scene child identity, Scene actions, or schedule values.

This means edits to an existing saved Hue Scene automatically affect the next recovery because the current Scene definition is pulled from the Bridge at recovery time.

## Controller identity

Hue Scene Recall recognizes three recovery-controller states:

- `smart_scene` + RID
- `scene` + RID
- `no_recoverable_controller`

A currently active Smart Scene is positive controller evidence.

A freshly recalled saved regular Scene can replace a Smart Scene controller when Hue reports the regular Scene active and advances its `last_recall`. `last_recall` is used only as an **edge detector**. Historical "newest last_recall wins" logic has been removed.

A healthy unsaved appearance change (for example Hue **Set once**) with no identifiable saved replacement clears the recovery controller. The transient appearance is not cached locally.

Ordinary soft OFF/ON does not clear the controller because power is excluded from appearance-change classification.

## Per-light impairment and recovery

Each Hue Light RID has its own recovery transaction. One bulb does not wait for siblings in the same room.

Either condition arms that exact light:

- Home Assistant light state becomes `unavailable` or `unknown`;
- Hue `zigbee_connectivity.status` becomes `connectivity_issue`.

A Hue `connectivity_issue` remains armed until Hue explicitly reports `connected`.

If both conditions occur, recovery begins only after both have cleared for that exact light.

## Power-neutral writes

Automatic recovery has a strict allowlist:

- `dimming`
- `color`
- `color_temperature`

`on` is not an allowed output field and is asserted absent before every recovery write.

Unsupported Scene action fields fail closed instead of being approximated.

## Smart Scenes / Golden Hours

For the currently validated Golden Hours shape, v0.3.1 uses two deliberately different resolver paths.

### Active Smart Scene

When Hue reports the stored Smart Scene `state = active`, the Bridge's live `active_timeslot.timeslot_id` is authoritative after validating that the original timeslot index still exists and points to a Scene in the current Smart Scene definition.

The reported `active_timeslot.weekday` is **not** required to equal the current calendar weekday. Live Bridge validation on 2026-09-13 showed Golden Hours 5 active at about 03:35 Sunday while Hue correctly reported `timeslot_id = 4`, `weekday = saturday`, targeting the saved **Sleepy** Scene. v0.3.0's weekday-equality check was therefore invalid and has been removed.

### Inactive Smart Scene

Inactive Smart Scene `active_timeslot` remains deliberately ignored because live testing proved it can be stale by hours or days. Outside the unverified overnight carry-forward window, the current child can be calculated from fresh Bridge schedule data for the supported schedule shape:

- recurrence explicitly covers all seven weekdays;
- an explicit `00:00` fixed timeslot exists;
- fixed `time` timeslots are supported;
- `sunset` is supported when the Bridge reports `sun_today.day_type = normal_day` and a valid `sunset_time`;
- the Bridge timezone is used;
- timeslots are evaluated by resolved wall-clock time, not array order.

The live v0.3.0 failure also revealed that Hue's active overnight carry-forward semantics do **not** match the previously assumed simple `00:00` rollover rule. Therefore, while a stored Smart Scene is inactive, v0.3.1 fails closed from the explicit midnight boundary until the next non-midnight boundary rather than guessing which saved child should govern.

### Transition safety

Live testing showed Hue recalls the next child at approximately:

```text
boundary - transition_duration
```

For a 60-second Golden Hours transition at 22:00, the child Scene recall occurred at about 21:59:00.

Hue Scene Recall therefore defers recovery from `boundary - transition_duration` through a conservative 60-second post-boundary settling window. After the window it resolves everything again from fresh Bridge data.

It does not attempt to interpolate a native Hue Smart Scene transition.

### Fail-closed Smart Scene cases

This release intentionally does not guess when it encounters:

- `sunrise` timeslots;
- sparse weekday recurrence requiring unverified carry-forward semantics;
- missing explicit midnight rollover;
- unsupported/unknown timeslot kinds;
- non-normal-day sunset data;
- an active Smart Scene whose reported timeslot index no longer exists in its current definition;
- an inactive Smart Scene during the unverified post-midnight carry-forward window.

Those recoveries are reported as `aborted_unresolved` and make no write.

## Regular Scenes

The stored Scene RID is fetched fresh at recovery time. The exact Scene action targeting the recovered Light RID is projected into an appearance-only payload.

Regular Scenes with `auto_dynamic = true` currently fail closed for automatic exact-light recovery because recalling the whole dynamic Scene would violate the per-light/no-sibling-write invariant.

Manual selection from the Hue Recall Scene select entity still performs Hue's normal Scene or Smart Scene recall because that is an explicit user action, not automatic fault recovery.

## Verification and retry

For a required write, v0.3.1:

1. subscribes to updates for the exact Light RID;
2. re-checks connectivity/availability;
3. issues an appearance-only PUT to that exact Light RID;
4. accepts a matching exact-light SSE update as verification;
5. falls back to a fresh exact-light GET if no matching event arrives;
6. performs at most one fully re-resolved retry if still connected and unverified.

A PUT response by itself is never considered `verified`.

## Enrollment

The existing `hueRecall` entity label remains the enrollment mechanism. A Hue room is automatically recoverable only when all Hue lights in that Hue room resolve to Home Assistant Hue light entities and all carry `hueRecall`.

The existing `hueRecallPower` label is left untouched and is not required by the integration.

## Existing entities retained on upgrade

v0.3.1 preserves the v0.2.x/v0.3.0 unique IDs for:

- `switch.hue_recall_automatic_recovery`
- each `<room> Hue Recall Scene` select
- each `<room> Hue Recall Recovery Diagnostics` sensor

The diagnostics sensor reports persisted controller identity and per-light recovery state/results.

## Upgrade / storage compatibility

The existing storage key/version remains unchanged. v0.3.1 needs no journal migration from v0.3.0 and continues to migrate the pre-v0.3 master switch value without caching appearance.

Historical `last_recall` timestamps are seeded only as edge baselines and are never used to pick a recovery winner.

## Installation status

This archive is a **build artifact only**. Creating it does not modify the live Home Assistant installation.

At build time, v0.3.0 is APPLIED on the live instance, but its first controlled Basement Bathroom outage test ended `aborted_unresolved` for both bulbs because of the now-removed weekday consistency check. v0.3.1 is not APPLIED until installed/reloaded.

For the repository/HACS workflow, replace/publish the repository source with this build, refresh HACS repository information, update Hue Scene Recall, and restart/reload Home Assistant as appropriate. Keep the prior package available for rollback until v0.3.1 is live-tested.

## Minimum environment

- Home Assistant 2026.8.0 or newer
- built-in Philips Hue integration using Hue V2
- the aiohue version supplied by that Home Assistant release

The implementation intentionally reuses Home Assistant's existing Hue runtime client/cache/event stream rather than creating a second Hue Bridge connection.
