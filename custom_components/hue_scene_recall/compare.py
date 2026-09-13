"""Appearance-only comparison helpers."""

from __future__ import annotations

from typing import Any

from .const import BRIGHTNESS_TOLERANCE, MIREK_TOLERANCE, XY_TOLERANCE


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def appearance_matches(light: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Compare only fields HueRecall intends to write."""
    if "on" in payload:
        return False

    dimming = payload.get("dimming")
    if isinstance(dimming, dict) and "brightness" in dimming:
        want = _number(dimming.get("brightness"))
        have_obj = light.get("dimming")
        have = _number(have_obj.get("brightness")) if isinstance(have_obj, dict) else None
        if want is None or have is None or abs(want - have) > BRIGHTNESS_TOLERANCE:
            return False

    color = payload.get("color")
    if isinstance(color, dict) and "xy" in color:
        want_xy = color.get("xy")
        have_color = light.get("color")
        have_xy = have_color.get("xy") if isinstance(have_color, dict) else None
        if not isinstance(want_xy, dict) or not isinstance(have_xy, dict):
            return False
        for axis in ("x", "y"):
            want = _number(want_xy.get(axis))
            have = _number(have_xy.get(axis))
            if want is None or have is None or abs(want - have) > XY_TOLERANCE:
                return False

    color_temperature = payload.get("color_temperature")
    if isinstance(color_temperature, dict) and "mirek" in color_temperature:
        want = _number(color_temperature.get("mirek"))
        have_obj = light.get("color_temperature")
        have = _number(have_obj.get("mirek")) if isinstance(have_obj, dict) else None
        if want is None or have is None or abs(want - have) > MIREK_TOLERANCE:
            return False

    return True


def appearance_fingerprint(light: dict[str, Any]) -> tuple[Any, ...]:
    """Fingerprint material appearance while deliberately excluding power."""
    dimming = light.get("dimming") if isinstance(light.get("dimming"), dict) else {}
    color = light.get("color") if isinstance(light.get("color"), dict) else {}
    xy = color.get("xy") if isinstance(color.get("xy"), dict) else {}
    ct = (
        light.get("color_temperature")
        if isinstance(light.get("color_temperature"), dict)
        else {}
    )
    return (
        round(float(dimming.get("brightness")), 3)
        if isinstance(dimming.get("brightness"), (int, float))
        else None,
        round(float(xy.get("x")), 5) if isinstance(xy.get("x"), (int, float)) else None,
        round(float(xy.get("y")), 5) if isinstance(xy.get("y"), (int, float)) else None,
        int(ct.get("mirek")) if isinstance(ct.get("mirek"), int) else None,
        bool(ct.get("mirek_valid")) if "mirek_valid" in ct else None,
    )
