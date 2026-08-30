"""Small, deterministic retry primitives shared by remote data providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time
from typing import Callable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """A bounded retry policy; delays are deterministic and cancellation-aware."""

    max_attempts: int = 3
    initial_delay: float = 0.5
    multiplier: float = 2.0
    max_delay: float = 4.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("Retry attempts must be positive")
        if (
            self.initial_delay < 0
            or self.multiplier < 1
            or self.max_delay < self.initial_delay
        ):
            raise ValueError("Retry delays must be non-negative and non-decreasing")

    def delay_after(self, failed_attempt: int, *, retry_after: float | None = None) -> float:
        calculated = self.initial_delay * (self.multiplier ** max(0, failed_attempt - 1))
        requested = max(0.0, float(retry_after or 0.0))
        return min(self.max_delay, max(calculated, requested))


def parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP Retry-After value without allowing an unbounded sleep."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())


def run_with_retry(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy,
    retryable: Callable[[Exception], bool],
    cancel: threading.Event | None = None,
    cancelled: Callable[[], Exception] = lambda: RuntimeError("Operation cancelled"),
    on_retry: Callable[[Exception, int, float], None] | None = None,
) -> T:
    """Run an operation with a strict attempt cap and interruptible backoff."""

    for attempt in range(1, policy.max_attempts + 1):
        if cancel is not None and cancel.is_set():
            raise cancelled()
        try:
            return operation()
        except Exception as exc:
            if attempt >= policy.max_attempts or not retryable(exc):
                raise
            delay = policy.delay_after(
                attempt,
                retry_after=getattr(exc, "retry_after", None),
            )
            if on_retry is not None:
                on_retry(exc, attempt, delay)
            if cancel is not None:
                if cancel.wait(delay):
                    raise cancelled()
            elif delay:
                time.sleep(delay)
    raise AssertionError("Retry loop exhausted without returning or raising")
