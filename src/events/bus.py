import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

# Types fanned out to the global dashboard stream. Progress events are
# high-frequency debug data and intentionally excluded.
GLOBAL_EVENT_TYPES = frozenset(
    {"created", "status_change", "ready", "completed", "error", "deleted"}
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Event:
    type: str
    job_id: UUID | None
    ts: str = field(default_factory=_utcnow_iso)
    data: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "job_id": str(self.job_id) if self.job_id else None,
            "ts": self.ts,
            **self.data,
        }


class EventBus:
    def __init__(self, per_job_queue_size: int = 256, global_queue_size: int = 256):
        self._per_job: dict[UUID, set[asyncio.Queue[Event]]] = {}
        self._global: set[asyncio.Queue[Event]] = set()
        self._per_job_size = per_job_queue_size
        self._global_size = global_queue_size

    async def publish(self, event: Event) -> None:
        # per-job fanout — always deliver, drop oldest on overflow
        if event.job_id is not None:
            for q in list(self._per_job.get(event.job_id, ())):
                self._put_oldest_drop(q, event)

        # global fanout — whitelisted types only, disconnect slow subscribers
        if event.type in GLOBAL_EVENT_TYPES:
            stale: list[asyncio.Queue[Event]] = []
            for q in list(self._global):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    stale.append(q)
            for q in stale:
                self._global.discard(q)

    @staticmethod
    def _put_oldest_drop(q: asyncio.Queue[Event], event: Event) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @asynccontextmanager
    async def subscribe(self, job_id: UUID) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._per_job_size)
        self._per_job.setdefault(job_id, set()).add(q)
        try:
            yield q
        finally:
            subs = self._per_job.get(job_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._per_job.pop(job_id, None)

    @asynccontextmanager
    async def subscribe_global(self) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._global_size)
        self._global.add(q)
        try:
            yield q
        finally:
            self._global.discard(q)
