# Hue Scene Recall

A Home Assistant custom integration that remembers the last real Philips Hue scene used in each Hue **room** and restores it after labeled Hue bulbs recover from mains power loss.

It is built on `aiohue`'s `SceneActivityTracker` (available in Home Assistant 2026.8+ through `aiohue 4.9.0`) and reuses Home Assistant's already-authenticated local Hue V2 connection. It does **not** create a second Hue Bridge login, poll the cloud, or copy Hue scene definitions into Home Assistant.

## v0.1.3 fixes

- Simplify sticky recall around Hue's own scene model: only selecting a Hue scene changes the remembered scene.
- Temporary per-bulb changes from Hue, Home Assistant, Apple Home, or automations do not disarm or replace scene memory.
- Upgrades ignore the legacy persisted `recall_armed: false` value from v0.1.2 and re-arm any still-valid remembered scene.
- Startup/topology refresh no longer guesses that an already-OFF `hueRecallPower` relay represents a physical outage.
- The manifest declares an explicit `integration_type` for current hassfest validation.
- Local 256/512 px brand icons and dependency-free regression/repository checks are included for CI.

## Why

Hue bulbs behind a relay or wall switch can use a bright, normal-white Hue power-on behavior as the hardware fail-safe. After the bulbs reconnect, Hue Scene Recall can restore the last Hue scene that was actually selected before power was lost.

The integration deliberately stores **scene memory** separately from **power intent**:

- Last scene: e.g. `Arctic Aurora`
- Desired power: `on` or `off`
- Recall armed: whether a valid remembered Hue scene exists and is eligible for recovery

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
- desired power **ON** + a valid remembered scene → Hue Scene Recall recalls the real Hue scene;
- desired power unknown, or no valid remembered scene → it leaves the recovered state alone.

The recovery delay is currently 2.5 seconds after all bulbs become available.

## Temporary adjustments and scene memory

Hue Scene Recall deliberately follows Hue's own distinction between a temporary adjustment and a saved scene:

- **Set once / individual bulb adjustment:** changes the current lighting only. It does not replace or disarm the remembered scene.
- **Select a Hue scene:** that scene becomes the remembered scene.
- **Edit and save an existing Hue scene in Hue:** the remembered scene ID stays the same, and the next recovery uses the newly saved scene definition.
- **Save a new scene and select it:** the new scene becomes the remembered scene.

This rule is intentionally origin-agnostic. Temporary light changes from the Hue app, Home Assistant, Apple Home, or an automation are all treated the same way: they can alter the current room state, but they do not redefine what should be restored after a power recovery.

That avoids trying to infer whether a brightness/color change was a person, a dynamic-scene transition, an automation, or another controller. If a user wants a manual adjustment to survive future power recovery, the durable action is to save/update a Hue scene.

Turning the whole room OFF also keeps the remembered scene. OFF is stored separately as power intent, not as a scene.

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

v0.1.3 intentionally performs automatic reconciliation on Hue **rooms only**. Hue zones can overlap rooms and each other; automatically recalling overlapping zones could create conflicting commands. Zone support can be added later with explicit policy.

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

### Short power cycles behind smart relays

Hue can keep a bulb's last-known HA state for several seconds after mains power is removed. For short wall-switch power cycles that means an availability-only detector may never see `unavailable`.

For any smart switch/relay that physically cuts power to a Hue room, add the **`hueRecallPower`** label to that switch and keep the room's existing room label on it (for example `isaacRoom`). Hue Scene Recall maps the relay to the room using that shared room label. While the relay is off it preserves the saved scene. When the relay returns on it waits for the Hue bulbs to rejoin and recalls the saved scene for the whole room, including continuously powered lamps in the same Hue room.

Only a **physical/manual OFF transition observed while Hue Scene Recall is running** starts a `hueRecallPower` recovery cycle. Relay changes initiated by Home Assistant automations or scripts (for example bedtime or occupancy logic) carry a parent context and are ignored as power-loss signals, so an automation cannot accidentally cause a saved scene to be restored later. If a physical OFF started the cycle, the next ON completes recovery even when that ON was initiated by Home Assistant.

v0.1.3 also deliberately does **not** infer a power cycle merely because a mapped relay is already OFF when Home Assistant starts or the topology refreshes. The integration did not observe the OFF edge or its context, and guessing could turn bedtime into a false outage. This favors not unexpectedly illuminating a room after restart.

This label is optional. Normal/longer power outages are still handled through Hue-light availability recovery.
