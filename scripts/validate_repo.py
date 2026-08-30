"""Fast dependency-free repository validation used before HACS/hassfest CI."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "hue_scene_recall"


def load_json(path: Path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


def main() -> int:
    manifest = load_json(COMPONENT / "manifest.json")
    hacs = load_json(ROOT / "hacs.json")
    translation = load_json(COMPONENT / "translations" / "en.json")

    required_manifest = {
        "domain",
        "name",
        "version",
        "config_flow",
        "dependencies",
        "documentation",
        "integration_type",
        "issue_tracker",
        "iot_class",
        "codeowners",
    }
    missing = required_manifest - manifest.keys()
    if missing:
        raise ValueError(f"manifest missing keys: {sorted(missing)}")
    if manifest["domain"] != "hue_scene_recall":
        raise ValueError("manifest domain mismatch")
    if manifest["integration_type"] != "service":
        raise ValueError("integration_type must be service")
    if manifest["version"] != "0.1.3":
        raise ValueError("manifest version mismatch")
    if hacs.get("homeassistant") != "2026.8.0":
        raise ValueError("unexpected minimum Home Assistant version")
    if translation.get("title") != "Hue Scene Recall" or "config" not in translation:
        raise ValueError("translations/en.json is incomplete")
    if (COMPONENT / "strings.json").exists():
        raise ValueError("custom integrations should not ship Core-only strings.json")

    const_text = (COMPONENT / "const.py").read_text(encoding="utf-8")
    if 'VERSION = "0.1.3"' not in const_text:
        raise ValueError("const.py version mismatch")

    manager_text = (COMPONENT / "manager.py").read_text(encoding="utf-8")
    obsolete_divergence_markers = (
        "DIRECT_USER_DIVERGENCE_DELAY",
        "MANUAL_DIVERGENCE_DELAY",
        "_process_direct_user_light_change",
        "_async_evaluate_inactive_scene",
    )
    if any(
        marker in manager_text or marker in const_text
        for marker in obsolete_divergence_markers
    ):
        raise ValueError("obsolete manual-divergence logic is still present")

    if "room.recall_armed = room.resume_scene_id is not None" not in manager_text:
        raise ValueError("legacy disarmed-state migration guard is missing")
    if '"recall_armed": room.recall_armed' in manager_text:
        raise ValueError("recall_armed must not be persisted in v0.1.3")

    icon = COMPONENT / "brand" / "icon.png"
    icon_2x = COMPONENT / "brand" / "icon@2x.png"
    if png_size(icon) != (256, 256):
        raise ValueError("brand/icon.png must be 256x256")
    if png_size(icon_2x) != (512, 512):
        raise ValueError("brand/icon@2x.png must be 512x512")

    forbidden = [
        path
        for path in ROOT.rglob("*")
        if "__pycache__" in path.parts or path.suffix == ".pyc"
    ]
    if forbidden:
        raise ValueError(f"cache artifacts present: {forbidden}")

    print("repository validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
