#!/usr/bin/env python3
"""Apply the Hue Scene Recall v0.3.4 brightness-cap changes to v0.3.3.

Run from the repository root at baseline commit d60dcee. The script is strict:
it aborts rather than guessing if the expected v0.3.3 source markers are absent.
It never touches a Home Assistant installation directly.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "custom_components" / "hue_scene_recall"
PAYLOAD = ROOT / "v034_payload" / "custom_components" / "hue_scene_recall"
EXPECTED_VERSION = 'VERSION = "0.3.3"'
EXPECTED_GIT_COMMIT = "d60dcee"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly one source marker, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    manager_path = TARGET / "manager.py"
    const_path = TARGET / "const.py"
    manifest_path = TARGET / "manifest.json"
    if not manager_path.exists() or not const_path.exists() or not manifest_path.exists():
        fail("run from a Hue Scene Recall repository root containing custom_components/hue_scene_recall")

    current_const = const_path.read_text(encoding="utf-8")
    if EXPECTED_VERSION not in current_const:
        fail("baseline is not v0.3.3; refusing to patch an unknown source tree")

    # When a real Git checkout is available, pin the patch to the exact HACS
    # baseline verified live. Source-marker validation below remains mandatory
    # as a second guard and also supports extracted source trees without .git.
    if (ROOT / ".git").exists():
        try:
            head = subprocess.check_output(
                ["git", "rev-parse", "--short=7", "HEAD"],
                cwd=ROOT,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as err:
            fail(f"could not verify Git baseline: {err}")
        if head != EXPECTED_GIT_COMMIT:
            fail(
                f"Git baseline is {head}, expected {EXPECTED_GIT_COMMIT}; "
                "refusing to patch a different revision"
            )

    manager = manager_path.read_text(encoding="utf-8")
    manager = replace_once(
        manager,
        "    VERIFY_EVENT_TIMEOUT_SECONDS,\n)",
        "    VERIFY_EVENT_TIMEOUT_SECONDS,\n    VERSION,\n)",
        "manager const import",
    )
    manager = replace_once(
        manager,
        '            data={"version": "0.3.3", "restored_events": self.flight_recorder.summary()["event_count"]},',
        '            data={"version": VERSION, "restored_events": self.flight_recorder.summary()["event_count"]},',
        "setup diagnostic version",
    )
    manager = replace_once(
        manager,
        '            "version": "0.3.3",',
        '            "version": VERSION,',
        "diagnostic snapshot version",
    )
    recovery_marker = '''            desired = resolve_desired_state(\n                resources,\n                room_id=room.room_id,\n                light_id=light.hue_light_id,\n                controller=room.controller,\n                now=_now(),\n                smart_episode=room.recovery_episode,\n            )\n            self._record_desired(light, desired)\n'''
    recovery_replacement = '''            desired = resolve_desired_state(\n                resources,\n                room_id=room.room_id,\n                light_id=light.hue_light_id,\n                controller=room.controller,\n                now=_now(),\n                smart_episode=room.recovery_episode,\n            )\n            cap_controller = getattr(self, "brightness_cap", None)\n            if cap_controller is not None:\n                desired = cap_controller.cap_desired_state(desired)\n            self._record_desired(light, desired)\n'''
    manager = replace_once(
        manager,
        recovery_marker,
        recovery_replacement,
        "recovery brightness-cap hook",
    )

    # Copy the reviewed v0.3.4 payload after all strict baseline checks pass.
    for source in PAYLOAD.iterdir():
        if source.name == "manager.py":
            continue
        target = TARGET / source.name
        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    manager_path.write_text(manager, encoding="utf-8")
    print("Applied Hue Scene Recall v0.3.4 patch successfully.")
    print("Next: run the included regression/static checks before committing or packaging.")


if __name__ == "__main__":
    main()
