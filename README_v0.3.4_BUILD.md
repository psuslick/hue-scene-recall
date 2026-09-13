# Hue Scene Recall v0.3.4 — brightness-cap build

This is a **strict source patch** for the verified Hue Scene Recall v0.3.3 baseline installed through HACS at Git commit `d60dcee`.

## What v0.3.4 adds

- `number.hue_maximum_brightness`-style Home Assistant Number entity named **Hue Maximum Brightness**.
- Range **1–100%**; **100% means no cap**.
- House-wide targeting comes from actual Hue V2 `light` resources, not the Home Assistant `Hue` label and never `grouped_light`.
- Current bulbs are never brightened and cap enforcement never sends an `on` field.
- Automatic Hue Scene Recall recovery is capped before its exact-light precompare/write path, so recovery and the cap cannot fight.
- Regular/manual light changes above the cap are reduced on the exact physical Hue light.
- Hue Smart Scene child Scenes receive a reversible **brightness-only saved-Scene overlay**, allowing Hue to remain the native scheduler.
- Original child-Scene brightness is stored in a separate, small Home Assistant Store before any Scene mutation, so changing the cap upward or back to 100% can restore the original saved value after a restart.
- If a saved Hue child Scene is edited while capped, v0.3.4 reconciles that edit as the new saved original and reapplies the cap only when necessary.
- If a child Scene is removed from every Smart Scene schedule, its old overlay is restored instead of being left behind.
- A newly lowered cap may reapply the **same currently active Smart Scene** only after a strict proof: exactly one active Smart Scene, same active timeslot after a fresh read, all affected physical lights healthy, power matches, supported non-brightness appearance matches, and v0.3.3 desired-state resolution says the same child is stable (including transition-defer rules). Otherwise it fails closed.
- Cap-generated direct writes and guarded Smart-Scene transition events are excluded from HueRecall's manual/unsaved-change classification.
- Scene and light writes are read-back verified with bounded retry/ambiguous-error handling.

## Architecture retained from v0.3.3

The v0.3.3 Bridge-authoritative recovery design is intentionally not replaced. Automatic outage recovery remains per-light, exact Hue V2 light only, appearance-only, controller-identity based, and post-write verified. The cap coordinator shares Home Assistant's existing Hue/aiohue runtime and does not open a second Hue connection.

## Applying to Git

Place this bundle in a checkout of `psuslick/hue-scene-recall` at commit `d60dcee`, then run:

```bash
python apply_v034.py
python -m unittest discover -s tests -p "test_*.py"
```

`apply_v034.py` aborts if the expected v0.3.3 source markers do not match and, when `.git` is present, aborts unless `HEAD` is exactly `d60dcee`.

To create a distributable component ZIP after applying:

```bash
python build_release.py
```

The builder reruns tests and compile checks and writes the ZIP plus SHA-256 under `dist/`.

## Live-install status

This build bundle does **not** modify Home Assistant. The live installation remains v0.3.3 until you deliberately publish/install v0.3.4 and restart/reload as appropriate.
