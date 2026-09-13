# Hue Scene Recall v0.3.4 — build status

**Build status:** `APPROVED_FOR_LIVE_TEST`, not `VERIFIED_LIVE`  
**Baseline:** HACS-installed `psuslick/hue-scene-recall` commit `d60dcee`, manifest v0.3.3  
**Live Home Assistant modified:** No  
**Live version at build time:** v0.3.3

## Validation completed

- Live/HACS baseline repository and installed commit verified through Home Assistant/HACS.
- Relevant v0.3.3 manager source paths and exact patch markers verified read-only from the installed integration.
- Current live Hue Bridge scene-action shapes sampled read-only: current Smart Scene children use per-light `on` + `dimming` + `color` or `color_temperature`, which the strict Smart reapply guard supports.
- Unit/static/controller regression suite: **69/69 passing**.
- Python `compileall`: passing.
- Strict patch application test: passing.
- Unknown/non-v0.3.3 baseline refusal: passing.
- Exact Git baseline pin when `.git` is available: included.

## Safety invariants covered by tests

- No `grouped_light` write endpoint.
- No regular Scene recall in automatic cap enforcement.
- Direct live cap writes target `/clip/v2/resource/light/<exact RID>` and contain only `dimming.brightness`.
- Automatic recovery payload has `on` structurally removed and brightness can only decrease to the cap.
- 100% never raises a currently lit bulb; it only removes/restores saved-Scene overlay state.
- Smart Scene child overlays persist originals before the first Bridge Scene mutation.
- Removed Smart child Scenes are restored.
- Scene writes and exact-light writes are verified with bounded retry/readback.
- The only automatic Smart Scene recall is the guarded reapplication of the exact same currently active Smart Scene.
- Guard fails closed on power/non-brightness mismatch, unsupported action fields, ambiguity, unhealthy lights, controller/timeslot change, or v0.3.3 transition-defer/unresolved state.
- Smart-reapply intermediate transition events and direct cap-generated events are guarded from HueRecall manual-change classification.

## What remains unverified

No offline suite can prove Hue Bridge firmware behavior end-to-end. The first v0.3.4 installation therefore still needs a controlled live validation before the status can become `VERIFIED`:

1. Install/restart with cap initially 100% and confirm no Scene/light mutation.
2. Lower cap in a room without an active Smart Scene and confirm exact-light dim only, no power change.
3. Lower cap while Golden Hours is active and confirm the same Smart controller remains active and no light power/color changes unexpectedly.
4. Raise cap and confirm current bulbs do not brighten immediately.
5. Return to 100% and confirm saved child Scene brightness definitions are restored.
6. Power-cycle one enrolled Hue bulb and confirm v0.3.3 recovery uses the capped payload and controller journal remains intact.

A failure at any step should stop the live test; do not proceed to broader rollout until diagnosed.
