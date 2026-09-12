# Changelog

## 0.2.1 — 2026-09-12

### PROPOSED build

Extends the v0.2 Hue-authoritative recovery design with direct Hue Zigbee connectivity recovery and audit instrumentation.

- Added direct subscription to aiohue `zigbee_connectivity` resource events.
- `connectivity_issue` now independently arms automatic scene recovery.
- HA light `unavailable`/`unknown` remains an independent recovery-arming condition.
- Recall is sent only when **all conditions that currently impair the room have cleared**.
- If one outage produces both `connectivity_issue` and HA `unavailable`, only one scene recall is sent after both recover.
- Recovery still performs exactly one fresh `GET /clip/v2/resource` and resolves the scene from Hue Bridge state at recovery time.
- Added one per-room diagnostic sensor intended for Home Assistant Recorder auditing.
- Diagnostic attributes record per-light connectivity-issue/recovery and HA unavailable/available timestamps plus room-level recovery-trigger timestamps.
- Diagnostic data is not used to select a scene and is not persisted by Hue Scene Recall.
- Soft ON/OFF, brightness, color, scene changes, timers, and Smart Scene timeslot changes remain excluded from recovery arming.
- Only Hue `connectivity_issue` arms the Hue-connectivity path in this release. Other Hue connectivity statuses are observable but do not independently trigger recall.

This build has not been installed on the live Home Assistant instance and therefore remains **PROPOSED**, not APPLIED or VERIFIED.

## 0.2.0 — 2026-09-12

### PROPOSED build

Architectural rewrite of automatic recovery.

- Hue Bridge is the sole scene source of truth.
- Automatic recovery triggers only after a real Hue room availability recovery.
- Recovery performs one fresh `GET /clip/v2/resource` through Home Assistant's existing Hue V2 connection.
- Active Smart Scene wins and its parent Smart Scene is recalled, allowing Hue to choose the current timeslot.
- Otherwise the regular Hue scene with the newest `status.last_recall` for the room is recalled.
- Regular scene contents are never copied into Home Assistant.
- `auto_dynamic` is read from Hue and honored on regular-scene recall.
- Removed persisted `resume_scene_id`, `resume_scene_mode`, and `desired_on` recovery state.
- Removed `recall_armed` recovery semantics.
- Removed soft ON/OFF state tracking.
- Removed `hueRecallPower` relay-edge recovery logic. Existing labels are not deleted; they are simply ignored.
- Preserved `hueRecall` enrollment, the automatic-recovery master switch, and room scene select entity.
- Preserved legacy scene-ID attributes as Hue-derived compatibility aliases.
