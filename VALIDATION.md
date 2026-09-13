# v0.3.3 validation record

Status of this artifact: **PROPOSED build; not APPLIED to live Home Assistant.**

## Live v0.3.2 evidence that triggered this build

Basement Bathroom outage at approximately 04:19–04:20 EDT:

- one shared recovery episode was correctly captured as Golden Hours 5 → Sunday timeslot 5 → Nighttime;
- A19 02 Hue connectivity returned at ~04:20:27.394;
- v0.3.2 evaluated A19 02 after the one-second settle and ended `already_correct` at ~04:20:28.947;
- A19 01 connectivity returned at ~04:20:28.403;
- A19 01 received the exact-light Nighttime appearance write and ended `verified` at ~04:20:30.419;
- A19 02's real post-power-up appearance was not reported until ~04:20:34.394, roughly seven seconds after Hue had already said `connected`;
- that delayed state was ~50% / 2732 K rather than the saved Nighttime appearance;
- controller journal cleared at ~04:20:35.899 as `healthy_unsaved_appearance_change`.

Conclusion: the v0.3.2 shared episode fix worked, but `connected + 1 second` was not a trustworthy readiness gate and late power-up state could still be misclassified as a manual change.

## v0.3.3 correction

For recoveries that include Hue `connectivity_issue`:

1. after the exact light returns `connected`, start/continue a per-light readiness wait;
2. a material Hue Light appearance update for that exact RID completes readiness immediately;
3. if none arrives, wait until 12 seconds after reconnect before using a fresh exact-light GET fallback;
4. only then can pre-write comparison return `already_correct`;
5. suppress manual-appearance classification through 20 seconds after reconnect;
6. keep the room fault episode active through that guard before controller reconciliation resumes.

## Diagnostic flight recorder

- bounded to 1,000 events in RAM;
- separate non-authoritative diagnostics Store;
- no per-event Store writes;
- dirty-only checkpoint every 12 hours;
- dirty flush on Home Assistant stop and integration unload/reload;
- complete event history available through config-entry diagnostics;
- full history is not copied into room diagnostic-sensor attributes.

## Build checks

- dependency-light resolver/comparison/flight-recorder/static-invariant tests: **33 passed**;
- full Python `compileall`: PASS;
- manifest/component version: `0.3.3`;
- automatic recovery contains no Scene recall, Smart Scene recall, or grouped-light actuator;
- automatic exact-light writer still targets `/clip/v2/resource/light/{rid}`;
- automatic payload allowlist remains exactly `dimming`, `color`, `color_temperature`;
- detailed diagnostic writes occur only inside the checkpoint/flush method, not the event-record path;
- checkpoint interval test confirms 12 hours;
- clean HA stop and integration unload paths flush dirty diagnostics;
- readiness wait occurs before the recovery pre-write exact-light comparison;
- tests require the post-connect manual-classification guard to exceed the observed ~7-second live delayed-report window;
- flight recorder is bounded and does not appear in `desired_state.py` recovery authority.

## Live verification required after installation

Use normal operation or one controlled Basement Bathroom outage. Success criteria:

- both bulbs share the same outage episode;
- neither transaction evaluates `already_correct` from a stale immediate post-connect read;
- an exact-light post-connect appearance event advances recovery when it arrives;
- if no event arrives, the bounded timeout/fresh-read path is visible in diagnostics;
- both bulbs end `verified` or genuinely `already_correct` against their real post-power-up state;
- delayed Hue reports do not clear the controller journal;
- the flight recorder contains correlated events sufficient to reconstruct the episode afterward;
- no automatic power write, whole-Scene recall, Smart Scene recall, grouped-light write, or sibling power change occurs.
