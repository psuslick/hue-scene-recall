"""Dependency-light regression tests for Hue Scene Recall v0.3 logic."""

from __future__ import annotations

from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hue_scene_recall"
PKG = "hue_scene_recall_test"

# Load the logic modules without installing Home Assistant. const.py only needs
# Platform to define its platform list, so a tiny test stub is sufficient.
ha = types.ModuleType("homeassistant")
ha_const = types.ModuleType("homeassistant.const")
class _Platform:
    SELECT = "select"
    SWITCH = "switch"
    SENSOR = "sensor"
ha_const.Platform = _Platform
sys.modules.setdefault("homeassistant", ha)
sys.modules.setdefault("homeassistant.const", ha_const)

pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
sys.modules[PKG] = pkg


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


const = _load("const")
controller = _load("controller_tracker")
desired = _load("desired_state")
compare = _load("compare")

ControllerRef = controller.ControllerRef
resolve_desired_state = desired.resolve_desired_state
appearance_matches = compare.appearance_matches

ROOM = "room-1"
LIGHT_A = "light-a"
LIGHT_B = "light-b"
SCENE_OLD = "scene-old"
SCENE_NEW = "scene-new"
SMART = "smart-1"
TZ = ZoneInfo("America/New_York")


def _scene(rid: str, brightness: float, light_id: str = LIGHT_A, **extra):
    action = {"on": {"on": True}, "dimming": {"brightness": brightness}}
    action.update(extra.pop("action_extra", {}))
    return {
        "id": rid,
        "type": "scene",
        "group": {"rid": ROOM, "rtype": "room"},
        "metadata": {"name": rid},
        "actions": [{"target": {"rid": light_id, "rtype": "light"}, "action": action}],
        "auto_dynamic": extra.pop("auto_dynamic", False),
        **extra,
    }


def _smart(state="inactive", active_id=0, timeslots=None):
    if timeslots is None:
        timeslots = [
            (7, 0, SCENE_OLD),
            (16, 0, SCENE_OLD),
            ("sunset", 0, SCENE_OLD),
            (20, 0, SCENE_OLD),
            (22, 0, SCENE_NEW),
            (0, 0, SCENE_OLD),
        ]
    raw = []
    for hour, minute, target in timeslots:
        if hour == "sunset":
            start = {"kind": "sunset", "time": {"hour": 0, "minute": 0, "second": 0}}
        elif hour == "sunrise":
            start = {"kind": "sunrise", "time": {"hour": 0, "minute": 0, "second": 0}}
        else:
            start = {"kind": "time", "time": {"hour": hour, "minute": minute, "second": 0}}
        raw.append({"start_time": start, "target": {"rid": target, "rtype": "scene"}})
    return {
        "id": SMART,
        "type": "smart_scene",
        "group": {"rid": ROOM, "rtype": "room"},
        "metadata": {"name": "Golden hours"},
        "state": state,
        "active_timeslot": {"timeslot_id": active_id, "weekday": "saturday"},
        "transition_duration": 60000,
        "week_timeslots": [{
            "timeslots": raw,
            "recurrence": ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"],
        }],
    }


def _base_resources(*items):
    return [
        {"id": "bridge", "type": "bridge", "time_zone": {"time_zone": "America/New_York"}},
        {"id": "geo", "type": "geolocation", "is_configured": True,
         "sun_today": {"sunset_time": "19:22:00", "day_type": "normal_day"}},
        *items,
    ]


def test_regular_controller_uses_exact_saved_scene_and_strips_power():
    resources = _base_resources(
        _scene(SCENE_OLD, 10),
        _scene(SCENE_NEW, 42),
    )
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("scene", SCENE_OLD),
        now=datetime(2026, 9, 12, 21, 0, tzinfo=TZ),
    )
    assert out.status == "resolved"
    assert out.effective_scene_rid == SCENE_OLD
    assert out.payload == {"dimming": {"brightness": 10}}
    assert "on" not in out.payload


def test_regular_scene_missing_exact_light_fails_closed():
    resources = _base_resources(_scene(SCENE_OLD, 10, light_id=LIGHT_B))
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("scene", SCENE_OLD),
        now=datetime(2026, 9, 12, 21, 0, tzinfo=TZ),
    )
    assert out.status == "unresolved"
    assert "exact_light" in out.reason


def test_dynamic_regular_scene_fails_closed_instead_of_whole_scene_recall():
    resources = _base_resources(_scene(SCENE_OLD, 10, auto_dynamic=True))
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("scene", SCENE_OLD),
        now=datetime(2026, 9, 12, 21, 0, tzinfo=TZ),
    )
    assert out.status == "unresolved"
    assert "dynamic_regular_scene" in out.reason


def test_inactive_smart_ignores_stale_active_timeslot_and_uses_schedule():
    resources = _base_resources(
        _smart(state="inactive", active_id=0),
        _scene(SCENE_OLD, 44),
        _scene(SCENE_NEW, 25),
    )
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("smart_scene", SMART),
        now=datetime(2026, 9, 12, 22, 5, tzinfo=TZ),
    )
    assert out.status == "resolved"
    assert out.effective_scene_rid == SCENE_NEW
    assert out.payload == {"dimming": {"brightness": 25}}


def test_active_smart_requires_timeslot_consistency():
    resources = _base_resources(
        _smart(state="active", active_id=3),
        _scene(SCENE_OLD, 44),
        _scene(SCENE_NEW, 25),
    )
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("smart_scene", SMART),
        now=datetime(2026, 9, 12, 22, 5, tzinfo=TZ),
    )
    assert out.status == "unresolved"
    assert "active_timeslot" in out.reason


def test_smart_transition_defers_from_boundary_minus_duration_through_settle():
    resources = _base_resources(
        _smart(state="active", active_id=3),
        _scene(SCENE_OLD, 44),
        _scene(SCENE_NEW, 25),
    )
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("smart_scene", SMART),
        now=datetime(2026, 9, 12, 21, 59, 30, tzinfo=TZ),
    )
    assert out.status == "deferred"
    assert out.defer_until == datetime(2026, 9, 12, 22, 1, tzinfo=TZ)


def test_next_day_midnight_transition_is_not_missed_at_2359():
    resources = _base_resources(
        _smart(state="active", active_id=4),
        _scene(SCENE_OLD, 44),
        _scene(SCENE_NEW, 25),
    )
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("smart_scene", SMART),
        now=datetime(2026, 9, 12, 23, 59, 30, tzinfo=TZ),
    )
    assert out.status == "deferred"
    assert out.defer_until == datetime(2026, 9, 13, 0, 1, tzinfo=TZ)


def test_sunrise_schedule_fails_closed():
    smart = _smart(timeslots=[
        (0, 0, SCENE_OLD),
        ("sunrise", 0, SCENE_NEW),
        (22, 0, SCENE_OLD),
    ])
    resources = _base_resources(smart, _scene(SCENE_OLD, 44), _scene(SCENE_NEW, 25))
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("smart_scene", SMART),
        now=datetime(2026, 9, 12, 12, 0, tzinfo=TZ),
    )
    assert out.status == "unresolved"
    assert out.reason == "sunrise_not_supported"


def test_unknown_scene_action_field_fails_closed():
    resources = _base_resources(
        _scene(SCENE_OLD, 10, action_extra={"signaling": {"signal": "on_off"}})
    )
    out = resolve_desired_state(
        resources,
        room_id=ROOM,
        light_id=LIGHT_A,
        controller=ControllerRef("scene", SCENE_OLD),
        now=datetime(2026, 9, 12, 21, 0, tzinfo=TZ),
    )
    assert out.status == "unresolved"
    assert "unsupported_scene_action_fields" in out.reason


def test_appearance_compare_uses_native_tolerances_and_ignores_power():
    light = {
        "on": {"on": True},
        "dimming": {"brightness": 25.35},
        "color": {"xy": {"x": 0.5608, "y": 0.3869}},
        "color_temperature": {"mirek": 367},
    }
    payload = {
        "dimming": {"brightness": 25.29},
        "color": {"xy": {"x": 0.5609, "y": 0.3868}},
        "color_temperature": {"mirek": 367},
    }
    assert appearance_matches(light, payload)
    assert not appearance_matches(light, {**payload, "on": {"on": False}})


def test_controller_storage_contains_identity_not_appearance():
    raw = ControllerRef("smart_scene", SMART, "time", "reason").as_dict()
    assert set(raw) == {"kind", "rid", "updated_at", "reason"}
    assert "brightness" not in raw and "color" not in raw and "on" not in raw


def test_recovery_function_contains_no_scene_recall_or_grouped_light_actuator():
    import ast
    source = (ROOT / "manager.py").read_text()
    tree = ast.parse(source)
    recover = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_async_recover_light"
    )
    text = ast.get_source_segment(source, recover) or ""
    assert ".scene.recall" not in text
    assert ".smart_scene.recall" not in text
    assert "grouped_light" not in text


def test_safe_automatic_payload_allowlist_structurally_excludes_power():
    assert "on" not in desired.SAFE_APPEARANCE_FIELDS
    assert desired.SAFE_APPEARANCE_FIELDS == {"dimming", "color", "color_temperature"}
