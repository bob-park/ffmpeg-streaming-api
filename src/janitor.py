import asyncio
import logging
import shutil
from pathlib import Path

from core.config import Settings
from core.repository import Repository

log = logging.getLogger("transcoder.janitor")


async def run_janitor(settings: Settings, repo: Repository) -> None:
    """Background task: periodically delete expired job segment dirs."""
    while True:
        try:
            expired = await repo.list_expired_with_grace(
                settings.grace_janitor_seconds
            )
            for row in expired:
                job_id = row["id"]
                playlist_rel = row["playlist_rel"]
                if playlist_rel:
                    # playlist_rel = "<uuid>/playlist.m3u8"
                    job_dir = Path(settings.storage_dir) / str(job_id)
                    try:
                        shutil.rmtree(job_dir, ignore_errors=True)
                    except OSError:
                        log.exception("failed to remove %s", job_dir)
                await repo.mark_expired(job_id)
                log.info("expired job_id=%s", job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("janitor tick failed")
        await asyncio.sleep(settings.janitor_interval_seconds)
