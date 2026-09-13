# Changelog

## 0.3.2 — shared Smart Scene outage episode / sibling-race fix

### Fixed

- Added a volatile per-room Smart Scene recovery episode so bulbs recovering from the same physical outage use one consistent pre-outage controller/timeslot/child identity.
- Fixed the live v0.3.1 race where Basement Bath A19 01 verified against Golden Hours 5 → Sleepy, then A19 02 re-resolved after Hue passively marked the Smart parent inactive and aborted.
- Suppressed Scene-child `last_recall` and appearance-change controller classification while a recovery episode is active, preventing recovery-generated events from replacing the Smart controller mid-episode.
- Delayed normal controller reconciliation until all affected bulbs in the room are no longer impaired/armed.
- Added episode invalidation on positive controller replacement/clear.

### Schedule safety

- Episode identity is valid only while its Smart controller/timeslot/child mapping remains current and no transition start (`B-D`) has been crossed.
- Episodes never cross a Bridge-local calendar-day boundary as authority.
- When an episode expires, v0.3.2 re-resolves current inactive Smart schedule state where supported rather than blindly reusing the captured child.
- The existing fail-closed post-midnight inactive-Smart guard remains when current Hue semantics cannot be reconstructed safely.

### Dynamic Scene handling

- Clarified that HA's palette-derived `is_dynamic` indication is not runtime dynamic-playback evidence.
- Static Scene activation remains recoverable even when a Scene has a multi-color palette.
- Actual `dynamic_palette` activation fails closed because an exact Light PUT cannot rejoin Hue dynamic Scene playback without a whole-Scene recall.
- `auto_dynamic=true` remains fail-closed unless positive static activation evidence exists.

### Preserved safety invariants

- Automatic recovery never writes `on`.
- Automatic recovery never recalls a Scene or Smart Scene.
- Automatic recovery never writes a grouped light.
- Each actuator write still targets only the exact recovered Light RID.
- Recovery episodes persist no appearance values and are never written to storage.
- Saved Scene definitions are re-pulled from the Bridge at every recovery/retry.

## 0.3.1 — active Smart Scene overnight resolver correction

- Trusted Hue's live active Smart `active_timeslot.timeslot_id` without requiring weekday equality.
- Added fail-closed inactive post-midnight handling.

## 0.3.0 — controller-journal / exact-light recovery rewrite

- Replaced room-wide whole-Scene recovery with per-Light-RID appearance-only recovery.
- Persisted controller identity only.
- Removed historical newest-`last_recall` authority.
- Added Smart Scene schedule/transition handling and exact-light verification.
