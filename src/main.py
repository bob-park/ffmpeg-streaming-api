import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

# Allow running this file directly (e.g., from PyCharm) — make `src/` importable.
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from api.jobs import router as jobs_router  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.db import create_pool, run_migrations  # noqa: E402
from core.logging import configure_logging  # noqa: E402
from core.repository import Repository  # noqa: E402
from events.bus import EventBus  # noqa: E402
from janitor import run_janitor  # noqa: E402
from scheduler import run_scheduler  # noqa: E402
from transcoder.orphans import reap_orphans  # noqa: E402
from transcoder.pool import WorkerPool  # noqa: E402

log = logging.getLogger("transcoder.main")

# src/main.py → repo root is parent.parent
MIGRATION_PATH = Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"
UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "media-src 'self'"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log.info("starting transcoder-api")

    # Storage dirs
    storage = Path(settings.storage_dir)
    storage.mkdir(parents=True, exist_ok=True)
    Path(settings.pidfile_dir).mkdir(parents=True, exist_ok=True)

    # Reap leftover ffmpeg processes from previous crash
    killed = reap_orphans(settings.pidfile_dir)
    if killed:
        log.warning("reaped %d orphan ffmpeg processes on startup", killed)

    # DB
    pool = await create_pool(settings)
    if MIGRATION_PATH.exists():
        sql = MIGRATION_PATH.read_text()
        await run_migrations(pool, sql)
    repo = Repository(pool)

    # Mark any leftover running jobs as failed
    n = await repo.mark_running_as_failed_on_restart()
    if n:
        log.warning("marked %d running jobs as failed on restart", n)

    # Worker pool + event bus
    bus = EventBus()
    worker_pool = WorkerPool(
        max_concurrency=settings.max_concurrency,
        max_queue_depth=settings.max_queue_depth,
    )

    # Factory that builds a run_job coroutine for any JobRead. Used both by
    # the re-enqueue-on-startup path and by the scheduler when it promotes a
    # scheduled job to queued.
    from transcoder.runner import run_job

    def make_factory(j):
        async def _factory(cancel_event):
            await run_job(
                job_id=j.id,
                source_url=j.source_url,
                mode=j.mode,
                settings=settings,
                repo=repo,
                bus=bus,
                cancel_event=cancel_event,
                loop=j.loop,
                realtime=j.realtime,
                video_bitrate=j.video_bitrate,
                video_height=j.video_height,
                end_at=j.end_at,
            )
        return _factory

    # Janitor
    janitor_task = asyncio.create_task(run_janitor(settings, repo))

    # Scheduler — promotes scheduled → queued when start_at arrives.
    scheduler_task = asyncio.create_task(
        run_scheduler(settings, repo, bus, worker_pool, make_factory)
    )

    # Re-submit queued jobs left over from previous run
    try:
        queued = await repo.list_queued()
        for j in queued:
            try:
                await worker_pool.submit(j.id, make_factory(j))
            except Exception:
                log.exception("failed to re-submit queued job %s", j.id)
    except Exception:
        log.exception("failed to re-enqueue queued jobs")

    app.state.settings = settings
    app.state.db_pool = pool
    app.state.repo = repo
    app.state.bus = bus
    app.state.pool = worker_pool

    try:
        yield
    finally:
        log.info("shutting down — cancelling active jobs")
        janitor_task.cancel()
        scheduler_task.cancel()
        with _suppress():
            await janitor_task
        with _suppress():
            await scheduler_task
        worker_pool.cancel_all()
        await worker_pool.drain(timeout=settings.shutdown_grace_seconds + 5)
        await pool.close()


class _suppress:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return True


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="transcoder-api", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(jobs_router)

    # Serve HLS segments directly from the storage dir.
    # Directory creation happens inside lifespan; don't require it at import time.
    app.mount(
        "/streams",
        StaticFiles(directory=settings.storage_dir, check_dir=False),
        name="streams",
    )

    @app.get("/", include_in_schema=False)
    async def ui_index() -> Response:
        if UI_PATH.exists():
            resp = FileResponse(str(UI_PATH), media_type="text/html")
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["Content-Security-Policy"] = CSP
            return resp
        return Response("UI not built", status_code=404)

    return app


app = create_app()

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(
        "main:app",
        host=s.host,
        port=s.port,
        reload=True,
        reload_dirs=[str(_SRC_DIR)],
    )
