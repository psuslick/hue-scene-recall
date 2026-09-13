# Changelog

## 0.3.3 — post-connect readiness + RAM-first diagnostic flight recorder

### Fixed from live v0.3.2 validation

- Hue `zigbee_connectivity=connected` is no longer treated as proof that the Light resource already contains the bulb's real power-up appearance.
- Connectivity-triggered recovery now waits for the **exact recovered Light RID** to emit a material appearance update after reconnect.
- If no such appearance update arrives, recovery fails over to a bounded fresh-read path only after **12 seconds** from Hue reconnect.
- `already_correct` therefore cannot be decided from the one-second stale Bridge window that failed live in v0.3.2.
- Delayed power-up appearance reports are protected by a **20-second post-connect manual-classification guard** so they cannot be mistaken for an unsaved user adjustment and erase the controller journal.
- The room outage episode remains active through that guard before controller reconciliation resumes.

### Passive diagnostics / flight recorder

- Added a bounded **1,000-event RAM flight recorder**.
- Events include impairment/reconnect lifecycle, shared outage episode identity, controller changes, desired-state resolution, observed pre-write appearance, exact-light write/verification milestones, transaction outcomes, appearance-readiness timeouts, and anomalies.
- Observed appearance can include power/brightness/color for diagnostics, but the recorder is **strictly non-authoritative** and is never consulted by desired-state resolution.
- Added config-entry `diagnostics.py` support so the complete in-memory/persisted flight recorder can be retrieved later without putting the full log into entity attributes or Home Assistant Recorder.

### microSD write policy

- Detailed diagnostic events stay in RAM during ordinary operation.
- The diagnostic Store writes only when dirty at a **12-hour checkpoint** (twice daily during continuous operation).
- A dirty diagnostic buffer is also flushed on clean Home Assistant stop or integration unload/reload.
- There are no per-event diagnostic disk writes.
- The existing small controller journal remains separate; this release does not turn diagnostic appearance history into recovery state.

### Preserved safety invariants

- Automatic recovery never writes `on`.
- Automatic recovery never recalls a Scene or Smart Scene.
- Automatic recovery never writes a grouped light.
- Each actuator write targets only the exact recovered Hue Light RID.
- Saved Scene actions are still pulled fresh from the Bridge at execution time.
- Recovery episode identity is runtime-only and contains no appearance values.

## 0.3.2 — shared Smart Scene outage episode / sibling-race fix

- Added a volatile shared Smart Scene recovery episode so sibling bulbs use one pre-outage controller/timeslot/child identity.
- Fixed the v0.3.1 sibling race where the first repair could make the Smart parent inactive before the second bulb resolved.
- Corrected palette-capability versus actual `dynamic_palette` playback handling.

## 0.3.1 — active Smart Scene overnight resolver correction

- Trusted Hue's live active Smart `active_timeslot.timeslot_id` without requiring weekday equality.
- Added fail-closed inactive post-midnight handling.

## 0.3.0 — controller-journal / exact-light recovery rewrite

- Replaced room-wide whole-Scene recovery with per-Light-RID appearance-only recovery.
- Persisted controller identity only.
- Removed historical newest-`last_recall` authority.
- Added Smart Scene schedule/transition handling and exact-light verification.
