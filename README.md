# Hue Scene Recall

A Home Assistant custom integration that remembers the last real Philips Hue scene used in each Hue **room** and restores it after labeled Hue bulbs recover from mains power loss.

It is built on `aiohue`'s `SceneActivityTracker` (available in Home Assistant 2026.8+ through `aiohue 4.9.0`) and reuses Home Assistant's already-authenticated local Hue V2 connection. It does **not** create a second Hue Bridge login, poll the cloud, or copy Hue scene definitions into Home Assistant.

## Why

Hue bulbs behind a relay or wall switch can use a bright, normal-white Hue power-on behavior as the hardware fail-safe. After the bulbs reconnect, Hue Scene Recall can restore the last Hue scene that was actually selected before power was lost.

The integration deliberately stores **scene memory** separately from **power intent**:

- Last scene: e.g. `Arctic Aurora`
- Desired power: `on` or `off`
- Recall armed: whether the room still represents that Hue scene or has been manually changed away from it

`off` is never treated as a scene. If a room was intentionally off when power failed, recovery reasserts OFF instead of turning the room on to an old scene.

## Enrollment: `hueRecall`

On first setup, the integration creates a Home Assistant entity label named **`hueRecall`** if it does not already exist.

Add `hueRecall` to the **individual Hue light entities** you want protected.

For safety, automatic recovery is enabled for a Hue room only when **every Hue light in that Hue room**:

1. resolves to a Home Assistant Hue `light.*` entity, and
2. has the `hueRecall` label.

Partially labeled rooms are tracked but are **not** automatically changed after recovery. This prevents recalling a room scene from unexpectedly changing unlabeled bulbs.

## Entities

### `switch.hue_recall_automatic_recovery`

Master automatic-recovery switch for the configured Hue bridge.

- **ON:** enrolled rooms can automatically reconcile after light availability recovers.
- **OFF:** scene activity and power intent continue to be tracked, but automatic recovery makes no changes.
- Turning it back ON does **not** immediately change any light; it only rearms future recovery.

This is intended to be useful on an Actions/Admin dashboard.

### `<room> Hue Recall Scene` select

One `select.*` entity is created for every Hue room. Its options are the live Hue scenes belonging to that room, including scenes later added in the Hue app.

The selected option is the **sticky scene to recall**, not merely a snapshot of bulb colors. Selecting an option recalls the real scene on the Hue Bridge. Direct scene selection in the Hue app is observed through `SceneActivityTracker` and updates the sticky recall scene.

Useful attributes include:

- `active_scene`
- `desired_power`
- `recall_armed`
- `recall_enrolled`
- `master_enabled`
- `total_hue_lights`

Because this is a standard Home Assistant `select`, an automation can use `select.select_next` with cycling enabled for a wall-button "next scene" action.

## Recovery behavior

When any enrolled room becomes unavailable because one or more Hue bulbs lose power, the integration preserves the room's pre-loss intent. Once **all** Hue bulbs in that room are available again and remain stable briefly:

- desired power **OFF** → Hue Scene Recall turns the Hue room back OFF;
- desired power **ON** + recall armed + remembered scene → Hue Scene Recall recalls the real Hue scene;
- desired power unknown, or recall disarmed → it leaves the recovered state alone.

The recovery delay is currently 2.5 seconds after all bulbs become available.

## Manual divergence

If Hue reports that a scene is no longer active while all room bulbs remain reachable and at least one remains on, Hue Scene Recall treats that as a deliberate manual change after a short settling delay and disarms automatic scene recall for that room.

Selecting another Hue scene—through the Hue app, the Home Assistant select, or an automation—arms recall again and updates the remembered scene.

Turning the room off does **not** erase or disarm its last scene.

## Dynamic scenes

When `SceneActivityTracker` reports that the remembered regular Hue scene was running in `dynamic_palette` mode, automatic recovery recalls it dynamically. If a scene is edited later in the Hue app, Hue Scene Recall recalls the current scene definition from the bridge rather than replaying stale copied brightness/color values.

Hue Smart Scenes are retained and recalled as Smart Scenes.

## Bedtime / temporary overrides

The intended pattern is that a temporary mode such as bedtime changes actual lighting without replacing the remembered Hue scene.

For a room where a relay physically removes power from ceiling bulbs during bedtime:

1. the remembered scene remains sticky;
2. scene selections made in the Hue app can update the remembered scene;
3. the powered-off bulbs cannot illuminate;
4. when the bulbs are intentionally powered again, normal recovery recalls the newest remembered scene.

A direct Hue-app command can still affect any Hue lamp that remains continuously powered. A local integration cannot prevent the Hue Bridge from executing a direct Hue-app command before Home Assistant observes it.

## Hue rooms, not Hue zones

v0.1.0 intentionally performs automatic reconciliation on Hue **rooms only**. Hue zones can overlap rooms and each other; automatically recalling overlapping zones could create conflicting commands. Zone support can be added later with explicit policy.

## Installation with HACS

1. Put this repository on GitHub.
2. In HACS, add the repository as a custom repository of type **Integration**.
3. Install **Hue Scene Recall**.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration** and add **Hue Scene Recall**.
6. Label every Hue bulb in a room with `hueRecall` to enroll that room.

Requires Home Assistant **2026.8.0 or newer** and the built-in Philips Hue integration using the Hue V2 API.

## Compatibility note

This integration intentionally reuses the built-in Hue integration's `ConfigEntry.runtime_data` bridge object. That avoids duplicate Hue connections and credentials, but it is an internal Home Assistant implementation detail. The access is isolated so it can be adapted if Home Assistant later exposes `SceneActivityTracker` officially.

## Upstream work

The architecture follows the direction of Home Assistant PR #151883 (active Hue scene per group) and the `aiohue` `SceneActivityTracker` added in 4.9.0. Hue Scene Recall is separate so it can provide sticky scene/power-loss behavior today without replacing or forking Home Assistant's built-in Hue integration.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
