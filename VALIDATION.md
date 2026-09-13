# v0.3.1 validation record

Status of this artifact: **PROPOSED build; not APPLIED to live Home Assistant.**

## Live v0.3.0 failure that triggered this patch

The first controlled Basement Bathroom power-cycle test on the APPLIED v0.3.0 build verified that both exact bulbs independently armed on HA `unavailable` + Hue `connectivity_issue`, then independently triggered recovery when HA returned available and Hue returned `connected`.

Both transactions then stopped before any write with:

```text
aborted_unresolved
active_timeslot_weekday_conflicts_with_schedule
```

Live state at the time showed:

- local time: about 03:35 Sunday, America/New_York;
- controller: Golden Hours 5 (`smart_scene` RID `1ab1a166-9067-4182-80fd-f695da1c6665`);
- Smart Scene state: active;
- Bridge active timeslot: id `4`, weekday `saturday`;
- active child exposed by HA Hue: **Sleepy**;
- both bulbs had returned from power-up around 50% rather than the saved Sleepy appearance (~25.29% with per-bulb saved color).

This proves v0.3.0's current-calendar-weekday equality check was invalid. The impairment/reconnect machinery was reached and worked; the desired-state resolver aborted before the actuator.

## v0.3.1 resolver rule

- If the stored Smart Scene is **active**, use Hue's live `active_timeslot.timeslot_id` as authoritative after confirming that original timeslot index still exists and targets a Scene in the current Smart Scene definition. Do not require `weekday` to match today's calendar weekday and do not require the active id to match a locally calculated child.
- If the stored Smart Scene is **inactive**, continue ignoring `active_timeslot` because it can be stale. The schedule resolver remains supported outside the newly identified unverified post-midnight carry-forward interval.
- For inactive Smart Scenes after the explicit 00:00 boundary and before the next non-midnight boundary, fail closed with `inactive_smart_post_midnight_semantics_unverified`.

## Prior live findings still encoded

- Inactive Smart Scene `active_timeslot` can be stale and is not used.
- Active Smart Scene and active regular child can coexist; child `last_recall` cannot replace an active Smart parent.
- Plain Smart Scene inactivity can result from ordinary room OFF and is not controller-replacement evidence.
- Unsaved Hue **Set once** can replace a Smart Scene without creating a replacement Scene RID; the safe controller result is `no_recoverable_controller`.
- A saved Scene recall produces a durable Scene RID / fresh `last_recall` edge and can become the journal controller.
- Golden Hours with `transition_duration=60000` recalled its 22:00 child at ~21:59:00 (`B-D`).
- Individual bulbs on the same switched circuit report `connectivity_issue` and `connected` independently.
- Hue does not guarantee a fresh Light appearance SSE update immediately after `connected`; a fresh resource read is required.

## Build checks

- Resolver/comparison/static-invariant tests: **16 pass**.
- Includes exact overnight active-Smart regression test.
- Includes inactive post-midnight fail-closed regression test.
- Includes invalid active timeslot index fail-closed test.
- Automatic payload allowlist remains `dimming`, `color`, `color_temperature`; `on` excluded.
- Static automatic-recovery inspection continues to prohibit Scene recall and grouped-light actuation.

## Not yet verified

v0.3.1 still requires a new controlled live outage after installation to verify the complete path:

```text
impairment → reconnect → active Smart child resolution → exact-light appearance PUT → verification
```

Until that succeeds, end-to-end automatic recovery remains **not VERIFIED**.
