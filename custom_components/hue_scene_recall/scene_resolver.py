"""Resolve the Hue Bridge's authoritative scene for a Hue room."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

SceneKind = Literal["scene", "smart_scene"]


class AmbiguousHueSceneError(ValueError):
    """Raised when Hue reports more than one active Smart Scene for one room."""


@dataclass(frozen=True, slots=True)
class AuthoritativeScene:
    """A Hue-owned scene selected from a fresh bridge resource query."""

    scene_id: str
    name: str
    kind: SceneKind
    dynamic: bool = False
    last_recall: datetime | None = None


def _parse_hue_timestamp(value: Any) -> datetime | None:
    """Parse a Hue ISO 8601 timestamp."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_authoritative_scene(
    resources: list[dict[str, Any]], room_id: str
) -> AuthoritativeScene | None:
    """Resolve the Hue-owned scene for one room from one fresh full-state query.

    Priority is intentionally simple:
    1. An active Hue Smart Scene is authoritative.
    2. Otherwise the regular Hue scene with the newest bridge-maintained
       status.last_recall is authoritative.

    No Home Assistant light state, brightness, color, or power intent participates
    in this decision.
    """
    active_smart: list[dict[str, Any]] = []
    regular: list[tuple[datetime, dict[str, Any]]] = []

    for resource in resources:
        group = resource.get("group")
        if not isinstance(group, dict) or group.get("rid") != room_id:
            continue

        resource_type = resource.get("type")
        if resource_type == "smart_scene":
            if resource.get("state") == "active":
                active_smart.append(resource)
            continue

        if resource_type != "scene":
            continue

        status = resource.get("status")
        if not isinstance(status, dict):
            continue
        last_recall = _parse_hue_timestamp(status.get("last_recall"))
        if last_recall is not None:
            regular.append((last_recall, resource))

    if len(active_smart) > 1:
        names = [
            str(item.get("metadata", {}).get("name", item.get("id", "unknown")))
            for item in active_smart
        ]
        raise AmbiguousHueSceneError(
            f"Hue reports multiple active Smart Scenes for room {room_id}: {names}"
        )

    if active_smart:
        scene = active_smart[0]
        return AuthoritativeScene(
            scene_id=str(scene["id"]),
            name=str(scene.get("metadata", {}).get("name", scene["id"])),
            kind="smart_scene",
        )

    if not regular:
        return None

    last_recall, scene = max(regular, key=lambda item: item[0])
    return AuthoritativeScene(
        scene_id=str(scene["id"]),
        name=str(scene.get("metadata", {}).get("name", scene["id"])),
        kind="scene",
        dynamic=bool(scene.get("auto_dynamic", False)),
        last_recall=last_recall,
    )
