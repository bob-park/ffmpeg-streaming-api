import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

log = logging.getLogger("transcoder.pool")

JobCoroFactory = Callable[[asyncio.Event], Awaitable[None]]


class QueueFull(Exception):
    pass


class WorkerPool:
    """Async subprocess pool with queue-depth cap and cancel via asyncio.Event."""

    def __init__(self, max_concurrency: int, max_queue_depth: int):
        self._sem = asyncio.Semaphore(max_concurrency)
        self._lock = asyncio.Lock()
        self._max_queue = max_queue_depth
        self._queue_depth = 0
        self._cancels: dict[UUID, asyncio.Event] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    async def submit(self, job_id: UUID, make_coro: JobCoroFactory) -> None:
        async with self._lock:
            if self._queue_depth >= self._max_queue:
                raise QueueFull
            self._queue_depth += 1
            cancel = asyncio.Event()
            self._cancels[job_id] = cancel
        task = asyncio.create_task(self._run(job_id, make_coro, cancel))
        self._tasks[job_id] = task

    async def _run(
        self,
        job_id: UUID,
        make_coro: JobCoroFactory,
        cancel: asyncio.Event,
    ) -> None:
        async with self._sem:
            async with self._lock:
                self._queue_depth -= 1
            try:
                await make_coro(cancel)
            except Exception:
                log.exception("worker task crashed job_id=%s", job_id)
            finally:
                self._tasks.pop(job_id, None)
                self._cancels.pop(job_id, None)

    def cancel(self, job_id: UUID) -> bool:
        ev = self._cancels.get(job_id)
        if ev is None:
            return False
        ev.set()
        return True

    def cancel_all(self) -> int:
        n = 0
        for ev in list(self._cancels.values()):
            ev.set()
            n += 1
        return n

    async def drain(self, timeout: float) -> None:
        tasks = list(self._tasks.values())
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
            )
        except asyncio.TimeoutError:
            log.warning("pool drain timeout — %d tasks still running", len(tasks))
