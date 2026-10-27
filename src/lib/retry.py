"""Retry only errors that mean the project is not ready yet."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

NOT_READY_MARKERS = (
    "not ready",
    "being created",
    "failed precondition",
    "try again",
    "backend error",
    "not been used in project",
    "is not enabled",
    "service is disabled",
    "not found",
    "does not exist",
)


def is_not_ready(error: BaseException) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in NOT_READY_MARKERS)


def is_capacity_exhausted(error: BaseException) -> bool:
    text = str(error)
    return "ZONE_RESOURCE_POOL_EXHAUSTED" in text or "stockout" in text.lower()


def call_until_ready(
    action: Callable[[], T],
    *,
    attempts: int = 12,
    sleep_seconds: float = 0,
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    """Call action. Retry a not-ready error. Any other error stops at once."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as error:
            last = error
            if not is_not_ready(error) or attempt == attempts:
                raise
            if sleep_seconds:
                sleeper(sleep_seconds)
    assert last is not None
    raise last
