import asyncio
import logging
from typing import Callable

from core.config import Settings
from core.repository import Repository
from core.schemas import JobRead
from events.bus import Event, EventBus
from transcoder.pool import QueueFull, WorkerPool

log = logging.getLogger("transcoder.scheduler")

JobFactoryMaker = Callable[[JobRead], Callable[[asyncio.Event], asyncio.Future]]


async def run_scheduler(
    settings: Settings,
    repo: Repository,
    bus: EventBus,
    pool: WorkerPool,
    make_factory: JobFactoryMaker,
    tick_seconds: int = 5,
) -> None:
    """Promote 'scheduled' jobs to 'queued' when their start_at arrives.

    Ticks every `tick_seconds` and submits any due jobs to the worker pool.
    """
    while True:
        try:
            due = await repo.list_due_scheduled()
            for job in due:
                promoted = await repo.mark_queued(job.id)
                if not promoted:
                    continue
                try:
                    await pool.submit(job.id, make_factory(job))
                except QueueFull:
                    log.warning("queue full, re-mark %s as failed", job.id)
                    await repo.mark_failed(job.id, "queue_full_on_schedule")
                    await bus.publish(
                        Event(
                            type="error",
                            job_id=job.id,
                            data={"message": "queue_full_on_schedule"},
                        )
                    )
                    continue
                await bus.publish(
                    Event(
                        type="status_change",
                        job_id=job.id,
                        data={"old": "scheduled", "new": "queued"},
                    )
                )
                log.info("promoted scheduled→queued job_id=%s", job.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(tick_seconds)
