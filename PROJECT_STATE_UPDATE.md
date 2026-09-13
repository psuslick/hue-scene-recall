# Ready-to-paste project state update

## HA_DECISION_LOG

### SUPERSEDED — v0.3.1 independent sibling Smart Scene re-resolution during one outage

Live Basement Bathroom testing showed v0.3.1 could produce different results for two bulbs on the same physical outage: A19 01 successfully verified Golden Hours 5 → Sleepy, while A19 02 reconnected roughly a second later after the Smart parent had become inactive and aborted in the inactive post-midnight guard. Independent per-light actuation remains correct, but controller/timeslot identity must be shared across one outage episode.

### PROPOSED — v0.3.2 volatile Smart Scene recovery episode

When the first bulb in a Hue room becomes impaired, capture runtime-only Smart controller/timeslot/child identity. All siblings recovering from that same outage may use that shared identity while it remains schedule-valid. Re-pull the current child Scene definition and exact Light action for every write. Never persist appearance, power, actions, or the episode itself. Invalidate the episode on positive controller replacement, transition crossing, date-boundary ambiguity, or changed timeslot target. Actual dynamic-palette playback remains fail-closed because exact-light PUT cannot rejoin it without violating the no-whole-scene invariant.

Historical newest-`last_recall`, automatic whole-Scene recall, grouped-light writes, and local appearance caching remain REJECTED.

## HA_CURRENT_STATE

### APPLIED but not fully VERIFIED — Hue Scene Recall v0.3.1

v0.3.1 is loaded. Its first controlled Basement Bathroom outage produced one successful verified exact-light recovery and one sibling abort caused by Smart Scene activity-state race. Therefore its active-Smart resolver and exact-light actuator are partially VERIFIED, but complete multi-bulb outage recovery is not.

## HA_OPEN_ITEMS

- Install/reload v0.3.2.
- Ensure Golden Hours 5 is positively active before the test.
- Repeat one Basement Bathroom physical power-cycle and leave it on.
- Verify diagnostics show one shared recovery episode and both bulbs complete consistently.
- Confirm no automatic power write, Scene recall, or grouped-light write occurred.
- Continue to fail closed for actual dynamic-palette playback until an exact-light, power-neutral Hue mechanism exists.
