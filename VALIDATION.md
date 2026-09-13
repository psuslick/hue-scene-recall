# v0.3.0 validation record

Status of this artifact: **APPROVED build; not APPLIED to live Home Assistant.**

## Source reconciliation

The installed v0.2.1 source was read from `/config/custom_components/hue_scene_recall` before this build. v0.2.1 was confirmed to:

- arm recovery at Hue-room scope;
- wait for all room impairments to clear;
- wait 2.5 seconds;
- GET the full Hue resource tree;
- choose an active Smart Scene, else the regular Scene with newest historical `last_recall`;
- perform a parent Smart Scene or whole regular Scene recall.

v0.3.0 intentionally replaces those automatic-recovery behaviors.

## Live Bridge findings encoded by the implementation

- Inactive Smart Scene `active_timeslot` can be stale and is not used.
- Active Smart Scene and active regular child can coexist; child `last_recall` cannot replace an active Smart parent.
- Plain Smart Scene inactivity can result from ordinary room OFF and is not controller-replacement evidence.
- Unsaved Hue **Set once** can replace a Smart Scene without creating a replacement Scene RID; the safe controller result is `no_recoverable_controller`.
- A saved Scene recall produces a durable Scene RID / fresh `last_recall` edge and can become the journal controller.
- Golden Hours with `transition_duration=60000` recalled its 22:00 child at ~21:59:00 (`B-D`).
- Individual bulbs on the same switched circuit report `connectivity_issue` and `connected` independently.
- Hue does not guarantee a fresh Light appearance SSE update immediately after `connected`; a fresh resource read is required.

## Build checks

- Python compileall: pass.
- JSON parse for `manifest.json` and `hacs.json`: pass.
- Dependency-light resolver/comparison/static-invariant tests: 13 pass.
- Static recovery-actuator inspection: no Scene recall or grouped-light actuator inside automatic recovery.
- Automatic payload allowlist: `dimming`, `color`, `color_temperature`; `on` excluded.

## Not yet verified

Because this artifact has not been installed, the following remain **APPLIED/VERIFIED pending**:

- Home Assistant runtime import/setup of v0.3.0;
- live persistence migration from the existing v0.2.1 Store payload;
- live per-light recovery write/verification against the Bridge;
- live diagnostics entity behavior after upgrade;
- HACS installation/update flow for the published repository commit/tag.
