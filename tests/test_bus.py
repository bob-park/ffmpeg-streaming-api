import asyncio
from uuid import uuid4

import pytest

from events.bus import Event, EventBus, GLOBAL_EVENT_TYPES


@pytest.mark.asyncio
async def test_per_job_subscriber_receives_events():
    bus = EventBus()
    job = uuid4()
    async with bus.subscribe(job) as q:
        await bus.publish(Event(type="ready", job_id=job))
        ev = await asyncio.wait_for(q.get(), timeout=1)
        assert ev.type == "ready"
        assert ev.job_id == job


@pytest.mark.asyncio
async def test_global_whitelist_filters_progress():
    bus = EventBus()
    job = uuid4()
    async with bus.subscribe_global() as gq:
        await bus.publish(Event(type="progress", job_id=job))
        await bus.publish(Event(type="status_change", job_id=job))
        ev = await asyncio.wait_for(gq.get(), timeout=1)
        assert ev.type == "status_change"
        assert gq.qsize() == 0


@pytest.mark.asyncio
async def test_per_job_receives_progress_events():
    bus = EventBus()
    job = uuid4()
    async with bus.subscribe(job) as q:
        await bus.publish(Event(type="progress", job_id=job))
        ev = await asyncio.wait_for(q.get(), timeout=1)
        assert ev.type == "progress"


@pytest.mark.asyncio
async def test_global_whitelist_set():
    # Sanity: progress must NOT be in whitelist, status_change must be.
    assert "progress" not in GLOBAL_EVENT_TYPES
    for t in ("status_change", "ready", "completed", "error", "created", "deleted"):
        assert t in GLOBAL_EVENT_TYPES


@pytest.mark.asyncio
async def test_deleted_event_fanned_out_globally():
    bus = EventBus()
    job = uuid4()
    async with bus.subscribe_global() as gq:
        await bus.publish(Event(type="deleted", job_id=job))
        ev = await asyncio.wait_for(gq.get(), timeout=1)
        assert ev.type == "deleted"
        assert ev.job_id == job


@pytest.mark.asyncio
async def test_per_job_oldest_drop_on_overflow():
    bus = EventBus(per_job_queue_size=2)
    job = uuid4()
    async with bus.subscribe(job) as q:
        await bus.publish(Event(type="a", job_id=job, data={"n": 1}))
        await bus.publish(Event(type="a", job_id=job, data={"n": 2}))
        await bus.publish(Event(type="a", job_id=job, data={"n": 3}))
        assert q.qsize() == 2
        e1 = q.get_nowait()
        e2 = q.get_nowait()
        # Oldest (n=1) was dropped, so we should see 2 and 3
        assert {e1.data["n"], e2.data["n"]} == {2, 3}


@pytest.mark.asyncio
async def test_slow_global_subscriber_is_dropped():
    bus = EventBus(global_queue_size=2)
    job = uuid4()
    async with bus.subscribe_global() as gq:
        # Fill queue past capacity with whitelisted events; slow subscriber gets dropped.
        for i in range(5):
            await bus.publish(Event(type="created", job_id=job, data={"n": i}))
        # After the drop, further publishes should not enqueue to this queue.
        before = gq.qsize()
        await bus.publish(Event(type="ready", job_id=job))
        assert gq.qsize() == before  # dropped — not receiving new events
