"""Bounded, non-authoritative diagnostic flight recorder for Hue Scene Recall."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class FlightRecorder:
    """RAM-first rolling diagnostics; never consulted for recovery decisions."""

    def __init__(self, *, max_events: int = 1000) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._next_seq = 1
        self.dirty = False
        self.last_persisted_at: str | None = None

    def load(self, raw: dict[str, Any] | None) -> None:
        if not isinstance(raw, dict):
            return
        events = raw.get("events", [])
        if isinstance(events, list):
            for item in events[-self._events.maxlen :]:
                if isinstance(item, dict):
                    self._events.append(deepcopy(item))
        seqs = [item.get("seq") for item in self._events if isinstance(item.get("seq"), int)]
        self._next_seq = (max(seqs) + 1) if seqs else 1
        persisted = raw.get("last_persisted_at")
        self.last_persisted_at = persisted if isinstance(persisted, str) else None
        self.dirty = False

    def record(
        self,
        event: str,
        *,
        category: str,
        severity: str = "info",
        room_id: str | None = None,
        light_id: str | None = None,
        episode_id: str | None = None,
        transaction_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "seq": self._next_seq,
            "at": _now_iso(),
            "category": category,
            "event": event,
            "severity": severity,
        }
        self._next_seq += 1
        if room_id is not None:
            item["room_id"] = room_id
        if light_id is not None:
            item["light_id"] = light_id
        if episode_id is not None:
            item["episode_id"] = episode_id
        if transaction_id is not None:
            item["transaction_id"] = transaction_id
        if data:
            item["data"] = deepcopy(data)
        self._events.append(item)
        self.dirty = True
        return item

    def mark_persisted(self, when: str | None = None) -> None:
        self.last_persisted_at = when or _now_iso()
        self.dirty = False

    def serialize(self) -> dict[str, Any]:
        return {
            "last_persisted_at": self.last_persisted_at,
            "events": [deepcopy(item) for item in self._events],
        }

    def snapshot(self) -> dict[str, Any]:
        events = [deepcopy(item) for item in self._events]
        anomalies = [item for item in events if item.get("severity") in {"warning", "error"}]
        return {
            "dirty": self.dirty,
            "event_count": len(events),
            "max_events": self._events.maxlen,
            "last_persisted_at": self.last_persisted_at,
            "last_event": events[-1] if events else None,
            "last_anomaly": anomalies[-1] if anomalies else None,
            "events": events,
        }

    def summary(self) -> dict[str, Any]:
        last_event = deepcopy(self._events[-1]) if self._events else None
        last_anomaly = next(
            (
                deepcopy(item)
                for item in reversed(self._events)
                if item.get("severity") in {"warning", "error"}
            ),
            None,
        )
        return {
            "dirty": self.dirty,
            "event_count": len(self._events),
            "max_events": self._events.maxlen,
            "last_persisted_at": self.last_persisted_at,
            "last_event": last_event,
            "last_anomaly": last_anomaly,
        }
