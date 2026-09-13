# Changelog

## 0.3.0 — controller-journal / exact-light recovery rewrite

### Changed

- Replaced room-wide recovery with per-Light-RID recovery.
- Replaced whole Scene recall during automatic recovery with exact-light appearance-only PUTs.
- Added durable per-room controller identity (`scene` or `smart_scene` RID) while continuing to prohibit local appearance/power caching.
- Removed historical newest-`last_recall` recovery selection.
- Added saved-Scene versus unsaved-appearance controller reconciliation.
- Added supported inactive Smart Scene schedule resolution for the currently validated all-days fixed-time + sunset Golden Hours shape.
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
