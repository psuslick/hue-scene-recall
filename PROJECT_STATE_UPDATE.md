# Ready-to-paste project state update

## HA_DECISION_LOG

### SUPERSEDED — Hue Scene Recall v0.3.0 active Smart Scene weekday consistency rule

The v0.3.0 rule requiring `active_timeslot.weekday` to equal the current Bridge calendar weekday is SUPERSEDED. Live Basement Bathroom testing on 2026-09-13 showed an ACTIVE Golden Hours 5 controller at about 03:35 Sunday legitimately reporting `active_timeslot_id=4`, `weekday=saturday`, child **Sleepy**. v0.3.0 therefore aborted both recovery transactions before writing.

### PROPOSED — Hue Scene Recall v0.3.1 resolver correction

Preserve the v0.3.0 controller-journal / exact-light / power-neutral architecture. For an ACTIVE Smart Scene, treat Hue's live `active_timeslot.timeslot_id` as authoritative after validating that the original index exists and targets a Scene. Do not compare its weekday or selected child to the local calendar evaluator. For an INACTIVE Smart Scene, continue ignoring stale `active_timeslot` and fail closed during the currently unverified post-midnight carry-forward interval.

Historical newest-`last_recall`, whole-scene automatic recall, grouped-light recovery writes, and local appearance caching remain REJECTED.

## HA_CURRENT_STATE

### APPLIED but not VERIFIED — Hue Scene Recall v0.3.0

The first controlled Basement Bathroom outage test showed per-light impairment/reconnect handling operating correctly, but both recovery transactions ended `aborted_unresolved` with `active_timeslot_weekday_conflicts_with_schedule`. No recovery write occurred. Therefore v0.3.0 end-to-end automatic recovery is not VERIFIED.

## HA_OPEN_ITEMS

- Publish/install Hue Scene Recall v0.3.1.
- Repeat the controlled Basement Bathroom physical power-cycle while Golden Hours 5 is active.
- Verify diagnostics progress through recovery and at least one exact-light transaction reports `verified` or `already_correct` as appropriate.
- Confirm no `on` write and no sibling/room-wide recall occurred.
- Separately validate inactive Smart Scene post-midnight carry-forward semantics before removing that fail-closed guard.
