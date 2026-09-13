# Ready-to-paste project state update

## HA_DECISION_LOG

### APPROVED — Hue Scene Recall v0.3.0 architecture/build

Hue Scene Recall vNext architecture is approved for implementation based on source review and live Bridge validation. The v0.3.0 build uses persistent Hue controller identity only, fresh Bridge resolution at recovery time, exact-light power-neutral appearance writes, per-light impairment/recovery, Smart Scene transition deferral, and post-write verification. Historical newest-`last_recall`, whole-scene automatic recall, grouped-light recovery writes, and local appearance caching remain REJECTED.

Build artifact exists but is not yet APPLIED to live Home Assistant.

## HA_OPEN_ITEMS

- Install/publish Hue Scene Recall v0.3.0 through the existing HACS repository workflow.
- Verify config-entry setup and controller-journal migration on live HA.
- Perform one controlled enrolled-light outage to verify exact-light appearance-only recovery and diagnostics.
- After successful live verification, update HA_CURRENT_STATE and mark v0.2.1 behavior SUPERSEDED.
