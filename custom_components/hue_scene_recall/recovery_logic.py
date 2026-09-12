"""Pure recovery-condition logic for Hue Scene Recall."""

from __future__ import annotations

from typing import Literal

RecoveryReason = Literal["unavailable", "connectivity_issue"]


def impairment_reasons(
    *, all_available: bool, connectivity_issue: bool
) -> frozenset[RecoveryReason]:
    """Return the independent conditions that currently impair a Hue room."""
    reasons: set[RecoveryReason] = set()
    if not all_available:
        reasons.add("unavailable")
    if connectivity_issue:
        reasons.add("connectivity_issue")
    return frozenset(reasons)


def should_schedule_recovery(
    *, was_impaired: bool, current_reasons: frozenset[RecoveryReason]
) -> bool:
    """Return True only when an armed room has recovered from all conditions."""
    return was_impaired and not current_reasons


def next_connectivity_issue_pending(
    *, current_pending: bool, new_status: str
) -> bool:
    """Track a connectivity_issue until Hue explicitly reports connected.

    A transition from connectivity_issue to disconnected/unidirectional/etc. is
    not treated as recovery. This prevents an early recall while the device is
    still not fully connected to the Hue Bridge.
    """
    if new_status == "connectivity_issue":
        return True
    if current_pending and new_status == "connected":
        return False
    return current_pending
