"""Controller-identity state for Hue Scene Recall.

Only Hue controller identity is durable. Appearance, power, schedule children,
and scene actions are intentionally never persisted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

ControllerKind = Literal["scene", "smart_scene"]


@dataclass(frozen=True, slots=True)
class ControllerRef:
    """Durable identity of a Hue-owned controller for one Hue group."""

    kind: ControllerKind
    rid: str
    updated_at: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind, "rid": self.rid}
        if self.updated_at:
            data["updated_at"] = self.updated_at
        if self.reason:
            data["reason"] = self.reason
        return data

    @classmethod
    def from_dict(cls, value: Any) -> "ControllerRef | None":
        if not isinstance(value, dict):
            return None
        kind = value.get("kind")
        rid = value.get("rid")
        if kind not in ("scene", "smart_scene") or not isinstance(rid, str) or not rid:
            return None
        updated_at = value.get("updated_at")
        reason = value.get("reason")
        return cls(
            kind=kind,
            rid=rid,
            updated_at=updated_at if isinstance(updated_at, str) else None,
            reason=reason if isinstance(reason, str) else None,
        )


def make_controller(kind: ControllerKind, rid: str, *, reason: str) -> ControllerRef:
    """Create a timestamped controller reference for diagnostics/storage."""
    return ControllerRef(
        kind=kind,
        rid=rid,
        updated_at=datetime.now().astimezone().isoformat(),
        reason=reason,
    )
