"""Pure per-light recovery-condition logic for Hue Scene Recall."""

from __future__ import annotations

from typing import Literal

RecoveryReason = Literal["unavailable", "connectivity_issue"]


def impairment_reasons(
    *, ha_available: bool, connectivity_issue_pending: bool
) -> frozenset[RecoveryReason]:
    """Return independent impairment reasons for one exact Hue light."""
    reasons: set[RecoveryReason] = set()
    if not ha_available:
        reasons.add("unavailable")
    if connectivity_issue_pending:
        reasons.add("connectivity_issue")
    return frozenset(reasons)


def next_connectivity_issue_pending(
    *, current_pending: bool, new_status: str
) -> bool:
    """Keep connectivity_issue armed until Hue explicitly reports connected."""
    if new_status == "connectivity_issue":
        return True
    if current_pending and new_status == "connected":
        return False
    return current_pending
