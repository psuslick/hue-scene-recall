# Hue Scene Recall project-state update — 2026-09-13

## Current live state

- **APPLIED / FAILED VERIFICATION:** Hue Scene Recall v0.3.2 is installed.
- v0.3.2 successfully captured the shared Smart Scene outage episode and performed one exact-light verified recovery, but incorrectly declared the other bulb `already_correct` from stale post-connect state.
- Live evidence showed Hue can report `connected` about seven seconds before the bulb's real power-up appearance reaches the Light resource.
- The delayed report then caused `healthy_unsaved_appearance_change` and cleared the controller journal.

## PROPOSED — v0.3.3

- Add exact-light post-connect appearance readiness before pre-write comparison.
- Wait for a material appearance event, with a 12-second bounded fallback from Hue reconnect.
- Add a 20-second post-connect classification guard and keep the room fault episode alive through the guard.
- Add a 1,000-event RAM-first diagnostic flight recorder.
- Persist diagnostic history only when dirty every 12 hours and on clean HA stop / integration unload.
- Expose full flight-recorder history through config-entry diagnostics.
- Keep diagnostic appearance completely non-authoritative.

## Verification status

- Static/unit/build verification: **33/33 tests PASS; compile PASS**.
- Live v0.3.3 verification: **NOT APPLIED / NOT VERIFIED**.

After live v0.3.3 verification, mark the v0.3.2 reconnect-readiness assumption SUPERSEDED and update HA_CURRENT_STATE accordingly.
