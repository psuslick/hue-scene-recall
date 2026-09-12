"""Regression tests for dual-condition recovery arming."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "hue_scene_recall"
    / "recovery_logic.py"
)
spec = importlib.util.spec_from_file_location("recovery_logic", MODULE_PATH)
assert spec is not None and spec.loader is not None
recovery_logic = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = recovery_logic
spec.loader.exec_module(recovery_logic)

impairment_reasons = recovery_logic.impairment_reasons
should_schedule_recovery = recovery_logic.should_schedule_recovery
next_connectivity_issue_pending = recovery_logic.next_connectivity_issue_pending


class RecoveryLogicTests(unittest.TestCase):
    def test_healthy_has_no_reason(self) -> None:
        self.assertEqual(
            impairment_reasons(all_available=True, connectivity_issue=False),
            frozenset(),
        )

    def test_unavailable_alone_arms_recovery(self) -> None:
        self.assertEqual(
            impairment_reasons(all_available=False, connectivity_issue=False),
            frozenset({"unavailable"}),
        )

    def test_connectivity_issue_alone_arms_recovery(self) -> None:
        self.assertEqual(
            impairment_reasons(all_available=True, connectivity_issue=True),
            frozenset({"connectivity_issue"}),
        )

    def test_both_conditions_are_retained(self) -> None:
        self.assertEqual(
            impairment_reasons(all_available=False, connectivity_issue=True),
            frozenset({"unavailable", "connectivity_issue"}),
        )

    def test_recall_waits_until_all_conditions_clear(self) -> None:
        self.assertFalse(
            should_schedule_recovery(
                was_impaired=True,
                current_reasons=frozenset({"connectivity_issue"}),
            )
        )
        self.assertFalse(
            should_schedule_recovery(
                was_impaired=True,
                current_reasons=frozenset({"unavailable"}),
            )
        )
        self.assertTrue(
            should_schedule_recovery(
                was_impaired=True,
                current_reasons=frozenset(),
            )
        )

    def test_connectivity_issue_clears_only_on_connected(self) -> None:
        pending = next_connectivity_issue_pending(
            current_pending=False, new_status="connectivity_issue"
        )
        self.assertTrue(pending)
        pending = next_connectivity_issue_pending(
            current_pending=pending, new_status="disconnected"
        )
        self.assertTrue(pending)
        pending = next_connectivity_issue_pending(
            current_pending=pending, new_status="unidirectional_incoming"
        )
        self.assertTrue(pending)
        pending = next_connectivity_issue_pending(
            current_pending=pending, new_status="connected"
        )
        self.assertFalse(pending)

    def test_healthy_to_healthy_does_not_recall(self) -> None:
        self.assertFalse(
            should_schedule_recovery(
                was_impaired=False,
                current_reasons=frozenset(),
            )
        )


if __name__ == "__main__":
    unittest.main()
