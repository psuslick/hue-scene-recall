"""Tests for power-source Context classification used by Hue Scene Recall."""

import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "hue_scene_recall"
    / "context.py"
)
spec = spec_from_file_location("hue_scene_recall_context", MODULE_PATH)
assert spec is not None and spec.loader is not None
context_module = module_from_spec(spec)
spec.loader.exec_module(context_module)

is_unparented_context = context_module.is_unparented_context


def _context(*, parent_id=None, user_id=None):
    return SimpleNamespace(parent_id=parent_id, user_id=user_id)


class ContextClassificationTests(unittest.TestCase):
    """Regression tests for smart-relay power-source classification."""

    def test_physical_device_event_is_unparented(self) -> None:
        self.assertTrue(is_unparented_context(_context()))

    def test_direct_ha_user_action_is_unparented(self) -> None:
        self.assertTrue(is_unparented_context(_context(user_id="user-1")))

    def test_automation_action_is_parented(self) -> None:
        self.assertFalse(is_unparented_context(_context(parent_id="automation-run")))

    def test_user_initiated_automation_child_is_parented(self) -> None:
        self.assertFalse(
            is_unparented_context(
                _context(parent_id="automation-run", user_id="user-1")
            )
        )


if __name__ == "__main__":
    unittest.main()
