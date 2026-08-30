# Changelog

## 0.1.3 — 2026-08-30

- Simplify sticky scene memory: only selecting a Hue scene replaces the remembered scene.
- Treat per-bulb brightness/color/state changes as temporary overrides regardless of whether they originate in Hue, Home Assistant, Apple Home, or an automation.
- Remove manual-divergence timers and HA-user-context disarming logic.
- Ignore v0.1.2's legacy persisted disarmed flag on upgrade and re-arm any still-valid remembered scene.
- Continue storing power intent separately, so turning a room OFF preserves its remembered scene.
- Stop inferring a physical power cycle from a `hueRecallPower` relay that is merely already OFF during startup/topology refresh.
- Preserve v0.1.2 Context handling: unparented/manual power-source OFF starts recovery; automation/script child OFF does not.
- Add explicit `integration_type: service` for current hassfest config-flow validation.
- Add local brand icons, dependency-free Context regression tests, and repository validation CI.
- Remove Core-only `strings.json`; custom integrations ship English config-flow text from `translations/en.json`.
- Move CI to Node-24-compatible `actions/checkout@v6` and `actions/setup-python@v6`.

## 0.1.2 — 2026-08-30

- Ignore automation/script-generated `hueRecallPower` OFF transitions by checking Home Assistant Context `parent_id`.
- Preserve manual/unparented power-cycle recovery.

## 0.1.1 — 2026-08-30

- Add optional `hueRecallPower` smart-relay detection for short mains interruptions that Hue may not expose as unavailable.

## 0.1.0 — 2026-08-30

- Initial release.
