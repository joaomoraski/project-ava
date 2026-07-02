"""Procrastinate background worker entry point.

Run:
    python worker.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Ensure backend/ is on sys.path when invoked from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.jobs.app import procrastinate_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("worker")


async def main() -> None:
    logger.info("Worker starting; concurrency=2")
    async with procrastinate_app.open_async():
        await procrastinate_app.run_worker_async(concurrency=2, queues=None)


if __name__ == "__main__":
    asyncio.run(main())
