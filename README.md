# Hue Scene Recall v0.3.2

Hue Scene Recall restores **saved Hue appearance intent** to an individual Hue bulb after that bulb recovers from a physical-power/connectivity failure, without changing whether the bulb is on or off.

v0.3.2 is a targeted correction to the v0.3.1 controller-journal / exact-light architecture after the first live v0.3.1 Basement Bathroom recovery exposed a sibling race.

## What v0.3.1 proved live

During one Basement Bathroom physical power cycle:

- A19 01 recovered and was **VERIFIED** against Golden Hours 5 → Sleepy.
- A19 02 reconnected about a second later, after Hue had already marked the Smart Scene inactive, and aborted with `inactive_smart_post_midnight_semantics_unverified`.

The first exact-light recovery did not need to be rolled back; it proved the active-Smart resolver and exact-light write/verification path worked. The problem was that the second sibling independently re-resolved after the parent Smart Scene had passively gone inactive.

## v0.3.2 recovery episode

When the first bulb in a Hue room becomes impaired, v0.3.2 freezes one **volatile room recovery episode** from the last positively observed active Smart Scene:

- Smart Scene controller RID;
- Hue `active_timeslot` id;
- effective child Scene RID;
- observed child activation mode (`static` / `dynamic_palette` when known);
- capture timestamp.

It does **not** store:

- brightness;
- color;
- color temperature;
- power;
- Scene actions.

The episode is runtime-only and is never written to Home Assistant storage.

Every returning bulb in that outage uses the same controller/child identity if it is still valid, but **re-pulls the current saved child Scene definition** and projects only that exact Light RID's action. This prevents sibling divergence without turning the episode into a local appearance cache.

Once all affected bulbs are healthy and their transactions have completed, the episode is cleared and ordinary controller reconciliation resumes.

## Boundary safety

A captured episode is not allowed to become stale scheduling authority.

Before using it, v0.3.2 verifies that:

- it belongs to the currently journaled Smart controller;
- the captured timeslot still exists;
- that timeslot still targets the same saved child Scene;
- capture and recovery are on the same Bridge-local date;
- the outage did not begin inside a Smart Scene transition window;
- no Smart Scene transition start (`B - transition_duration`) was crossed while the episode was held.

If the episode is no longer valid, Hue Scene Recall discards it as desired-state evidence and re-resolves from current Bridge schedule data. If current inactive-Smart semantics are themselves unverified (notably the known post-midnight carry-forward interval), recovery still fails closed.

## Dynamic Scene clarification

Home Assistant's Hue Scene `is_dynamic` property is not treated as proof that the Scene is currently playing dynamically. A multi-color Hue palette can make that UI property true even when the Bridge reports a normal static Scene activation.

For automatic exact-light recovery v0.3.2 uses Bridge runtime evidence:

- `status.active = static` → exact-light static appearance projection is allowed;
- `status.active = dynamic_palette` → fail closed;
- `auto_dynamic = true` with no positive static activation evidence → fail closed.

This matters for the Basement Bathroom Nighttime child observed during live testing: the raw Bridge resource reported `auto_dynamic = false`, `status.active = static`, and exact saved per-bulb actions matching the room. It is therefore a supported static recovery target even though HA exposes the Scene as dynamic-capable.

Hue Recall still never performs a whole Scene recall automatically merely to rejoin dynamic playback.

## Core automatic recovery path

```text
exact Hue light becomes impaired
        ↓
room outage episode captures controller/child identity (Smart Scene only)
        ↓
that exact light becomes connected/available again
        ↓
short settle
        ↓
pull fresh Hue Bridge resources
        ↓
resolve live Smart child OR valid shared episode OR supported inactive schedule
        ↓
pull current saved child Scene action for this exact Light RID
        ↓
strip power (`on`) structurally
        ↓
pre-compare current exact-light Bridge state
        ↓
PUT appearance only to /clip/v2/resource/light/{rid}
        ↓
verify by exact-light SSE, then exact GET fallback
```

## Persistent state

Only these values are persisted:

- master automatic-recovery switch;
- per-room controller identity (`scene` RID or `smart_scene` RID).

The v0.3.2 recovery episode and last active Smart child context are explicitly runtime-only.

## Power-neutral actuator

Automatic recovery can output only:

- `dimming`
- `color`
- `color_temperature`

`on` is structurally excluded. Automatic recovery never calls Scene recall, Smart Scene recall, or `grouped_light`.

## Controller rules retained

- Active Smart Scene → Smart Scene RID is controller.
- Active Smart parent + freshly recalled regular child → keep Smart parent.
- Saved regular Scene selected while healthy → durable regular Scene controller.
- Healthy unsaved `Set once` appearance change with no saved replacement → `NO_RECOVERABLE_CONTROLLER`.
- Plain Smart Scene inactivity from room OFF/power/connectivity does not replace controller identity.
- Historical newest `last_recall` is never used as recovery authority.

During a live recovery episode, regular-child `last_recall` and exact-light appearance changes generated by the outage/recovery are suppressed as controller-replacement evidence until the episode is complete.

## Smart Scene timing retained

For the currently validated Golden Hours schedule shape:

- active Smart Scene `active_timeslot.timeslot_id` is authoritative;
- `active_timeslot.weekday` need not equal the current calendar weekday;
- inactive `active_timeslot` is ignored because it can be stale;
- Hue transition begins at approximately `boundary - transition_duration`;
- automatic recovery defers through the transition and a bounded post-boundary settling window;
- unsupported sunrise/sparse/ambiguous schedule semantics fail closed.

## Verification

A PUT response alone is never success. A required write is verified by:

1. subscribing to the exact Light RID;
2. issuing the exact-light appearance-only PUT;
3. accepting matching exact-light SSE state as verification; or
4. falling back to a fresh exact-light GET;
5. making at most one fully re-resolved retry while still connected.

## Upgrade compatibility

v0.3.2 keeps the existing config entry, storage version, entity unique IDs, label enrollment, and controller journal format. No migration is required from v0.3.1.

Existing entities retained include the master recovery switch, each room Scene select, and each room Recovery Diagnostics sensor.

The diagnostics sensor now also exposes the active runtime `recovery_episode` when one exists, so sibling-race behavior can be inspected directly during live validation.

## Installation status

This archive is a **build artifact only**. Creating it does not change the installed Home Assistant integration.

At build time v0.3.1 is APPLIED. Its first live outage produced one verified exact-light recovery and one sibling abort, which is the race v0.3.2 addresses. v0.3.2 becomes APPLIED only after you install/reload it.

## Minimum environment

- Home Assistant 2026.8.0 or newer
- built-in Philips Hue integration using Hue V2
- aiohue supplied by that Home Assistant release
