"""Thin retry wrapper for Telegram sends that hit transient network errors.

python-telegram-bot v21 handles RetryAfter (429) internally but does not
automatically retry NetworkError or TimedOut. This wrapper fills that gap.
"""
import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import telegram


async def send_with_retry(
    coro_factory: Callable[[], Coroutine[Any, Any, Any]],
    *,
    retries: int = 3,
    base_delay: float = 1.0,
) -> Any:
    """
    Call coro_factory() to get a fresh coroutine on each attempt.
    Retries on NetworkError / TimedOut with exponential backoff (1s, 2s, 4s).
    Raises on the final failure.
    """
    for attempt in range(retries):
        try:
            return await coro_factory()
        except (telegram.error.NetworkError, telegram.error.TimedOut):
            if attempt == retries - 1:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt))
