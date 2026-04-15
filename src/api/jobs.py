import asyncio
import json
import logging
import shutil
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from core.config import Settings
from core.repository import Repository
from core.schemas import ErrorResponse, JobCreate, JobRead, JobStatus
from core.security import UrlRejected, validate_source_url
from events.bus import Event, EventBus
from transcoder.pool import QueueFull, WorkerPool
from transcoder.runner import run_job

log = logging.getLogger("transcoder.api")

router = APIRouter()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_repo(request: Request) -> Repository:
    return request.app.state.repo  # type: ignore[no-any-return]


def get_pool(request: Request) -> WorkerPool:
    return request.app.state.pool  # type: ignore[no-any-return]


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus  # type: ignore[no-any-return]


@router.post("/jobs", response_model=JobRead, status_code=202)
async def create_job(
    payload: JobCreate,
    settings: Settings = Depends(get_settings),
    repo: Repository = Depends(get_repo),
    pool: WorkerPool = Depends(get_pool),
    bus: EventBus = Depends(get_bus),
) -> JobRead:
    try:
        validate_source_url(
            payload.source_url,
            settings.url_allow_schemes,
            settings.url_deny_hostnames,
            allow_private_ips=settings.url_allow_private_ips,
        )
    except UrlRejected as e:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                detail=f"source_url rejected: {e.reason}", code="url_rejected"
            ).model_dump(),
        )

    ttl = payload.ttl_seconds or settings.default_ttl_seconds
    job = await repo.insert_job(
        payload.source_url,
        payload.mode,
        ttl,
        loop=payload.loop,
        realtime=payload.realtime,
        video_bitrate=payload.video_bitrate,
        video_height=payload.video_height,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )

    await bus.publish(
        Event(
            type="created",
            job_id=job.id,
            data={
                "source_url": payload.source_url,
                "mode": payload.mode.value,
                "loop": payload.loop,
                "realtime": payload.realtime,
                "video_bitrate": payload.video_bitrate,
                "video_height": payload.video_height,
            },
        )
    )

    # Scheduled jobs sit in 'scheduled' state; the background scheduler submits
    # them to the pool when start_at arrives.
    if job.status == JobStatus.SCHEDULED:
        return job

    async def _make_coro(cancel_event: asyncio.Event) -> None:
        await run_job(
            job_id=job.id,
            source_url=payload.source_url,
            mode=payload.mode,
            settings=settings,
            repo=repo,
            bus=bus,
            cancel_event=cancel_event,
            loop=payload.loop,
            realtime=payload.realtime,
            video_bitrate=payload.video_bitrate,
            video_height=payload.video_height,
            end_at=payload.end_at,
        )

    try:
        await pool.submit(job.id, _make_coro)
    except QueueFull:
        await repo.mark_failed(job.id, "queue_full")
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                detail="worker queue is full", code="queue_full"
            ).model_dump(),
        )

    return job


@router.get("/jobs", response_model=list[JobRead])
async def list_jobs(
    status: str | None = Query(
        None, description="comma-separated JobStatus filter"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: Repository = Depends(get_repo),
) -> list[JobRead]:
    filters: list[JobStatus] | None = None
    if status:
        try:
            filters = [JobStatus(s.strip()) for s in status.split(",") if s.strip()]
        except ValueError as e:
            raise HTTPException(400, f"invalid status: {e}")
    return await repo.list_jobs(statuses=filters, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(
    job_id: UUID,
    repo: Repository = Depends(get_repo),
) -> JobRead:
    job = await repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


_TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.EXPIRED,
}


@router.post("/jobs/{job_id}/cancel", status_code=202)
async def cancel_job(
    job_id: UUID,
    repo: Repository = Depends(get_repo),
    pool: WorkerPool = Depends(get_pool),
) -> dict[str, str]:
    """Cancel a running/queued/ready job. 409 if already terminal."""
    job = await repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                detail=f"job is already {job.status.value}", code="not_cancellable"
            ).model_dump(),
        )
    if pool.cancel(job_id):
        return {"status": "cancelling"}
    # Not in pool (e.g., crashed between DB insert and pool submit).
    # Fall back to direct DB mark.
    await repo.mark_cancelled(job_id)
    return {"status": "cancelled"}


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: UUID,
    settings: Settings = Depends(get_settings),
    repo: Repository = Depends(get_repo),
    bus: EventBus = Depends(get_bus),
) -> Response:
    """Hard-delete a terminal job: remove DB row and segment directory.

    - 404 if job does not exist
    - 409 if job is still active (caller must cancel first)
    - 204 on success
    """
    job = await repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                detail=f"job is still {job.status.value}, cancel first",
                code="still_active",
            ).model_dump(),
        )

    deleted = await repo.delete_terminal_job(job_id)
    if not deleted:
        # Race: something else moved the row out of terminal state.
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                detail="job no longer in a deletable state", code="race"
            ).model_dump(),
        )

    # Best-effort segment cleanup. Never fail the request on filesystem errors.
    job_dir = Path(settings.storage_dir) / str(job_id)
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except OSError:
        log.exception("failed to remove job dir %s", job_dir)

    await bus.publish(Event(type="deleted", job_id=job_id))
    return Response(status_code=204)


async def _sse_stream_per_job(
    request: Request,
    job_id: UUID,
    repo: Repository,
    bus: EventBus,
    ping_interval: int,
):
    # Subscribe BEFORE reading snapshot to avoid the subscribe/snapshot race.
    async with bus.subscribe(job_id) as queue:
        job = await repo.get_job(job_id)
        if job is None:
            yield {"event": "error", "data": json.dumps({"message": "job not found"})}
            return
        yield {
            "event": "snapshot",
            "data": json.dumps({"type": "snapshot", "job": job.model_dump(mode="json")}),
        }
        snapshot_ts = job.created_at.isoformat()

        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=ping_interval)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue
            # dedup: drop events strictly older than snapshot
            if event.ts < snapshot_ts:
                continue
            yield {
                "event": event.type,
                "data": json.dumps(event.to_payload()),
            }
            if event.type in ("completed", "error"):
                return


@router.get("/jobs/{job_id}/events")
async def job_events(
    request: Request,
    job_id: UUID,
    settings: Settings = Depends(get_settings),
    repo: Repository = Depends(get_repo),
    bus: EventBus = Depends(get_bus),
) -> EventSourceResponse:
    return EventSourceResponse(
        _sse_stream_per_job(
            request, job_id, repo, bus, settings.sse_ping_interval_seconds
        )
    )


async def _sse_stream_global(
    request: Request,
    repo: Repository,
    bus: EventBus,
    ping_interval: int,
):
    async with bus.subscribe_global() as queue:
        # Snapshot AFTER subscribe so in-flight events aren't lost.
        jobs = await repo.list_jobs(
            statuses=[
                JobStatus.QUEUED,
                JobStatus.RUNNING,
                JobStatus.READY,
                JobStatus.COMPLETED,
                JobStatus.FAILED,
            ],
            limit=100,
        )
        yield {
            "event": "snapshot",
            "data": json.dumps(
                {
                    "type": "snapshot",
                    "jobs": [j.model_dump(mode="json") for j in jobs],
                }
            ),
        }
        # Drain any events that fired between subscribe and snapshot — client
        # dedups by job_id so safe to re-emit.
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=ping_interval)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue
            yield {
                "event": event.type,
                "data": json.dumps(event.to_payload()),
            }


@router.get("/events")
async def global_events(
    request: Request,
    settings: Settings = Depends(get_settings),
    repo: Repository = Depends(get_repo),
    bus: EventBus = Depends(get_bus),
) -> EventSourceResponse:
    return EventSourceResponse(
        _sse_stream_global(request, repo, bus, settings.sse_ping_interval_seconds)
    )


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
