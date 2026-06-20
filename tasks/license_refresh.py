"""Background task that periodically refreshes the offline license token.

Runs only when a license server is configured. Each iteration calls
licensing.refresh_once() in a worker thread (it does blocking HTTP + file IO);
failures are swallowed so the loop never dies and never breaks an already
licensed user (offline grace handles connectivity gaps).
"""
import asyncio
import logging

from config import system_config
from core import licensing

logger = logging.getLogger(__name__)


async def run_refresh_loop(stop_event: asyncio.Event) -> None:
    interval = max(3600, system_config.LICENSE_REFRESH_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(licensing.refresh_once)
        except Exception as exc:  # never let the loop die
            logger.warning("license refresh loop iteration failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
