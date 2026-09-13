# Changelog

## 0.3.1 — active Smart Scene overnight resolver correction

### Fixed

- Removed the invalid requirement that an active Smart Scene's `active_timeslot.weekday` equal the current calendar weekday.
- Removed the active-timeslot-versus-locally-calculated-child equality check. When the Smart Scene is active, Hue's live `active_timeslot.timeslot_id` is authoritative after index/target validation.
- Added the live regression case observed on 2026-09-13: Sunday ~03:35 with Golden Hours 5 active, `active_timeslot_id=4`, `weekday=saturday` resolves the id-4 **Sleepy** child instead of aborting.
- Added a fail-closed guard for inactive Smart Scene recovery after midnight until the next non-midnight boundary because Hue's overnight carry-forward semantics are not yet verified for inactive reconstruction.

### Preserved safety invariants

- Automatic recovery never writes `on`.
- Automatic recovery never recalls a Scene or Smart Scene.
- Automatic recovery never writes a grouped light.
- Recovery targets only the exact recovered Light RID.
- Every recovery/retry resolves from fresh Bridge data.
- Unsupported or ambiguous desired-state cases make no write.

## 0.3.0 — controller-journal / exact-light recovery rewrite

### Changed

- Replaced room-wide recovery with per-Light-RID recovery.
- Replaced whole Scene recall during automatic recovery with exact-light appearance-only PUTs.
- Added durable per-room controller identity (`scene` or `smart_scene` RID) while continuing to prohibit local appearance/power caching.
- Removed historical newest-`last_recall` recovery selection.
- Added saved-Scene versus unsaved-appearance controller reconciliation.
- Added supported inactive Smart Scene schedule resolution for the then-validated all-days fixed-time + sunset Golden Hours shape.
- Added transition deferral using live-validated `boundary - transition_duration` semantics.
- Added exact-light SSE verification with exact GET fallback.
- Added one bounded, fully re-resolved retry.
- Expanded diagnostics to expose controller identity and per-light transaction state.

### Safety invariants

- Automatic recovery never writes `on`.
- Automatic recovery never recalls a Scene or Smart Scene.
- Automatic recovery never writes a grouped light.
- A sibling bulb is never touched merely because another bulb recovered.
- Unsupported desired-state cases fail closed.
- Every retry and deferred continuation re-pulls current Bridge state.

### Compatibility

- Existing Hue Scene Recall config entry remains version 1.
- Existing master switch, room select, and diagnostics entity unique IDs are retained.
- Existing `master_enabled` storage value is retained.
