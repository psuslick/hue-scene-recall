#!/usr/bin/env python3
"""Materialize and package Hue Scene Recall v0.3.4 from verified v0.3.3."""
from __future__ import annotations

import compileall
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = "0.3.4"
DIST = ROOT / "dist"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    run(sys.executable, "apply_v034.py")
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")
    target = ROOT / "custom_components" / "hue_scene_recall"
    if not compileall.compile_dir(target, quiet=1):
        raise SystemExit("compileall failed")

    DIST.mkdir(exist_ok=True)
    out = DIST / f"Hue_Scene_Recall_v{VERSION}_HACS.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(target.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            zf.write(path, path.relative_to(ROOT))
        for name in ("README.md", "hacs.json", "LICENSE"):
            path = ROOT / name
            if path.is_file():
                zf.write(path, path.relative_to(ROOT))

    checksum = DIST / f"Hue_Scene_Recall_v{VERSION}_HACS.zip.sha256"
    checksum.write_text(f"{sha256(out)}  {out.name}\n", encoding="utf-8")
    print(out)
    print(checksum)


if __name__ == "__main__":
    main()
