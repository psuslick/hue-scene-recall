"""Regression tests for Hue-authoritative scene resolution."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "hue_scene_recall"
    / "scene_resolver.py"
)
spec = importlib.util.spec_from_file_location("scene_resolver", MODULE_PATH)
assert spec is not None and spec.loader is not None
scene_resolver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scene_resolver
spec.loader.exec_module(scene_resolver)

AmbiguousHueSceneError = scene_resolver.AmbiguousHueSceneError
resolve_authoritative_scene = scene_resolver.resolve_authoritative_scene


class SceneResolverTests(unittest.TestCase):
    def test_latest_regular_scene_wins(self) -> None:
        resources = [
            {
                "id": "older",
                "type": "scene",
                "group": {"rid": "room-1"},
                "metadata": {"name": "Relax"},
                "status": {"active": "inactive", "last_recall": "2026-09-11T22:00:00Z"},
            },
            {
                "id": "newer",
                "type": "scene",
                "group": {"rid": "room-1"},
                "metadata": {"name": "Shine"},
                "status": {"active": "inactive", "last_recall": "2026-09-12T01:00:00Z"},
            },
        ]
        result = resolve_authoritative_scene(resources, "room-1")
        self.assertIsNotNone(result)
        self.assertEqual(result.scene_id, "newer")
        self.assertEqual(result.name, "Shine")
        self.assertEqual(result.kind, "scene")

    def test_active_smart_scene_beats_newer_child_scene(self) -> None:
        resources = [
            {
                "id": "child",
                "type": "scene",
                "group": {"rid": "room-1"},
                "metadata": {"name": "Golden Hours child"},
                "status": {"active": "static", "last_recall": "2026-09-12T02:55:00Z"},
            },
            {
                "id": "golden",
                "type": "smart_scene",
                "group": {"rid": "room-1"},
                "metadata": {"name": "Golden hours"},
                "state": "active",
                "active_timeslot": {"timeslot_id": 4, "weekday": "saturday"},
            },
        ]
        result = resolve_authoritative_scene(resources, "room-1")
        self.assertIsNotNone(result)
        self.assertEqual(result.scene_id, "golden")
        self.assertEqual(result.kind, "smart_scene")

    def test_auto_dynamic_is_hue_owned_recall_mode(self) -> None:
        resources = [
            {
                "id": "dynamic",
                "type": "scene",
                "group": {"rid": "room-1"},
                "metadata": {"name": "Amber robin"},
                "auto_dynamic": True,
                "status": {"active": "inactive", "last_recall": "2026-09-12T02:00:00Z"},
            }
        ]
        result = resolve_authoritative_scene(resources, "room-1")
        self.assertIsNotNone(result)
        self.assertTrue(result.dynamic)

    def test_other_room_is_ignored(self) -> None:
        resources = [
            {
                "id": "other",
                "type": "scene",
                "group": {"rid": "room-2"},
                "metadata": {"name": "Other"},
                "status": {"active": "static", "last_recall": "2026-09-12T03:00:00Z"},
            }
        ]
        self.assertIsNone(resolve_authoritative_scene(resources, "room-1"))

    def test_never_recalled_regular_scene_is_not_authoritative(self) -> None:
        resources = [
            {
                "id": "never",
                "type": "scene",
                "group": {"rid": "room-1"},
                "metadata": {"name": "Never"},
                "status": {"active": "inactive"},
            }
        ]
        self.assertIsNone(resolve_authoritative_scene(resources, "room-1"))

    def test_multiple_active_smart_scenes_fail_safe(self) -> None:
        resources = [
            {
                "id": "smart-a",
                "type": "smart_scene",
                "group": {"rid": "room-1"},
                "metadata": {"name": "A"},
                "state": "active",
            },
            {
                "id": "smart-b",
                "type": "smart_scene",
                "group": {"rid": "room-1"},
                "metadata": {"name": "B"},
                "state": "active",
            },
        ]
        with self.assertRaises(AmbiguousHueSceneError):
            resolve_authoritative_scene(resources, "room-1")


if __name__ == "__main__":
    unittest.main()
