"""Small, dependency-free helpers for Home Assistant event classification."""

from __future__ import annotations

from typing import Any


def is_unparented_context(context: Any) -> bool:
    """Return whether a state change has no parent action/context."""
    return context.parent_id is None
