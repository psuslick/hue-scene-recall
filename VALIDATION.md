# v0.3.2 validation record

Status of this artifact: **PROPOSED build; not APPLIED to live Home Assistant.**

## Live v0.3.1 evidence that triggered this build

During the first controlled Basement Bathroom power cycle after v0.3.1 installation:

### Basement Bath A19 01

- impairment detected by HA unavailable + Hue connectivity issue;
- reconnect detected;
- controller: Golden Hours 5;
- effective Scene: Sleepy;
- exact-light recovery result: **verified**.

### Basement Bath A19 02

- same physical outage, independent reconnect about one second later;
- Smart parent had become inactive before this sibling resolved;
- transaction ended `aborted_unresolved` with `inactive_smart_post_midnight_semantics_unverified`.

Conclusion: v0.3.1's exact-light actuator/verification path works, but independent fresh controller resolution could diverge between sibling bulbs in one outage when Hue activity status changed between their recovery transactions.

## Live dynamic-capability clarification

After Golden Hours 5 was explicitly reselected, HA exposed the active child as Nighttime and its Scene entity as dynamic-capable. Raw Hue Bridge state showed the actual Basement Bathroom Nighttime resource was:

- `auto_dynamic = false`;
- `status.active = static`;
- exact saved actions around 39.52% with per-bulb XY values matching the live bulbs.

Therefore multi-color palette capability is not treated as evidence of current dynamic playback.

## v0.3.2 correction

The first impairment in a room captures a runtime-only `SmartRecoveryEpisode` containing:

- Smart controller RID;
- active timeslot id;
- child Scene RID;
- captured child activation mode when known;
- timestamp.

The object contains no brightness/color/color-temperature/power and is not serialized.

A sibling recovery may use that child identity after the Smart parent has passively become inactive, but the saved child Scene and exact-light action are still fetched fresh at execution time.

Episode use fails or falls back safely if the controller/timeslot mapping changed, a transition boundary was crossed, capture happened in a transition, or a date boundary invalidated the episode.

## Build checks

- dependency-light resolver/comparison/static-invariant tests: **26 passed**;
- full Python `compileall`: PASS;
- manifest/component version: `0.3.2`;
- automatic recovery function contains no Scene recall, Smart Scene recall, or grouped-light actuator;
- automatic exact-light writer still targets `/clip/v2/resource/light/{rid}`;
- automatic payload allowlist remains exactly `dimming`, `color`, `color_temperature`;
- persistent Store serialization contains controller identity only; recovery episode/context is absent;
- tests cover the exact sibling-race pattern, post-midnight shared episode, transition expiry/fallback, date-boundary handling, palette-rich static Scene support, and actual dynamic-palette fail-closed behavior.

## Live verification still required after installation

Repeat a controlled Basement Bathroom outage while Golden Hours 5 is active and inspect the diagnostics. Success criteria:

- one room `recovery_episode` is shared during the outage;
- both bulbs independently arm/reconnect;
- both resolve the same effective saved child Scene unless a real schedule transition invalidates the episode;
- both end `verified` or `already_correct` as appropriate;
- no automatic `on`, Scene recall, Smart Scene recall, grouped-light write, or sibling power change occurs.
