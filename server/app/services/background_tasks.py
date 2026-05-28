"""
Background Task System for TrendZo.
Provides a scalable task queue architecture using FastAPI BackgroundTasks.

Future migration path:
- Replace with Celery + Redis for horizontal scaling
- Replace with ARQ (async Redis queue) for lightweight async workers
- Replace with Dramatiq for actor-based processing

Current implementation: in-process async task queue with priority support.
"""

import asyncio
import traceback
from typing import Callable, Any
from datetime import datetime, timezone
from collections import deque
from app.config import APP_NAME
from app.services.logger import log


class TaskQueue:
    """
    In-process async task queue with priority levels.
    Tasks run in the background without blocking request handlers.
    """

    def __init__(self, max_concurrent: int = 5):
        self._queue: deque = deque()
        self._running: int = 0
        self._max_concurrent = max_concurrent
        self._completed: int = 0
        self._failed: int = 0
        self._history: list = []  # Last 50 task results

    async def enqueue(
        self,
        task_fn: Callable,
        *args,
        task_name: str = "unnamed",
        priority: str = "normal",
        **kwargs,
    ):
        """Add a task to the queue. Executes immediately if capacity available."""
        task_entry = {
            "fn": task_fn,
            "args": args,
            "kwargs": kwargs,
            "name": task_name,
            "priority": priority,
            "queued_at": datetime.now(timezone.utc),
        }

        if priority == "high":
            self._queue.appendleft(task_entry)
        else:
            self._queue.append(task_entry)

        # Try to process immediately
        asyncio.create_task(self._process_next())

    async def _process_next(self):
        """Process the next task in the queue if capacity allows."""
        if self._running >= self._max_concurrent:
            return
        if not self._queue:
            return

        task = self._queue.popleft()
        self._running += 1

        started_at = datetime.now(timezone.utc)
        try:
            result = await task["fn"](*task["args"], **task["kwargs"])
            self._completed += 1
            duration = (datetime.now(timezone.utc) - started_at).total_seconds()
            self._record_history(task["name"], "completed", duration)
            log.info(f"Task completed: {task['name']} ({duration:.2f}s)")
        except Exception as e:
            self._failed += 1
            duration = (datetime.now(timezone.utc) - started_at).total_seconds()
            self._record_history(task["name"], "failed", duration, str(e))
            log.error(f"Task failed: {task['name']} — {str(e)}")
        finally:
            self._running -= 1
            # Process next in queue
            if self._queue:
                asyncio.create_task(self._process_next())

    def _record_history(self, name: str, status: str, duration: float, error: str = None):
        entry = {
            "name": name,
            "status": status,
            "duration_s": round(duration, 2),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            entry["error"] = error
        self._history.append(entry)
        if len(self._history) > 50:
            self._history.pop(0)

    def get_stats(self) -> dict:
        return {
            "queued": len(self._queue),
            "running": self._running,
            "completed": self._completed,
            "failed": self._failed,
            "max_concurrent": self._max_concurrent,
            "recent_tasks": self._history[-10:],
        }


# Global singleton
task_queue = TaskQueue(max_concurrent=5)
