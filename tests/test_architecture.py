"""Static regression checks for the v0.2.1 architecture."""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "hue_scene_recall"
MANAGER = (ROOT / "manager.py").read_text(encoding="utf-8")
CONST = (ROOT / "const.py").read_text(encoding="utf-8")
SENSOR = (ROOT / "sensor.py").read_text(encoding="utf-8")


class ArchitectureTests(unittest.TestCase):
    def test_removed_local_power_and_scene_memory(self) -> None:
        self.assertNotIn("desired_on", MANAGER)
        self.assertNotIn("resume_scene_id", MANAGER)
        self.assertNotIn("resume_scene_mode", MANAGER)
        self.assertNotIn("recall_armed", MANAGER)
        self.assertNotIn("hueRecallPower", MANAGER)

    def test_recovery_uses_one_fresh_full_state_get(self) -> None:
        self.assertEqual(MANAGER.count('"clip/v2/resource"'), 1)
        self.assertIn("resolve_authoritative_scene(resources, room_id)", MANAGER)

    def test_soft_state_changes_are_not_recovery_inputs(self) -> None:
        self.assertNotIn("STATE_ON", MANAGER)
        self.assertNotIn("STATE_OFF", MANAGER)
        self.assertIn("old_unavailable == new_unavailable", MANAGER)

    def test_both_recovery_conditions_are_present(self) -> None:
        self.assertIn("ConnectivityServiceStatus.CONNECTIVITY_ISSUE", MANAGER)
        self.assertIn("connectivity_issue=self._has_connectivity_issue(room)", MANAGER)
        self.assertIn("all_available=self._all_available(room)", MANAGER)

    def test_direct_hue_connectivity_subscription_is_used(self) -> None:
        self.assertIn("self.api.sensors.zigbee_connectivity.subscribe", MANAGER)
        self.assertIn("_on_connectivity_resource_event", MANAGER)

    def test_diagnostics_are_recorder_friendly_sensor_entities(self) -> None:
        self.assertIn("Platform.SENSOR", CONST)
        self.assertIn("EntityCategory.DIAGNOSTIC", SENSOR)
        self.assertIn("last_connectivity_issue_at", MANAGER)
        self.assertIn("last_ha_unavailable_at", MANAGER)
        self.assertIn("last_recovery_trigger", MANAGER)

    def test_connectivity_diagnostics_do_not_select_scene(self) -> None:
        # Scene selection remains isolated in scene_resolver and the fresh GET path.
        self.assertNotIn("last_connectivity_issue_at = authoritative", MANAGER)
        self.assertNotIn("connectivity_status = authoritative", MANAGER)


if __name__ == "__main__":
    unittest.main()
