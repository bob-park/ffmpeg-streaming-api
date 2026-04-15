from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

from core.schemas import JobRead, JobStatus, StreamMode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_job(row: asyncpg.Record, playlist_base: str = "/streams") -> JobRead:
    playlist_rel = row["playlist_rel"]
    playlist_url: str | None = None
    if playlist_rel and row["status"] in ("ready", "completed"):
        playlist_url = f"{playlist_base}/{playlist_rel}"
    return JobRead(
        id=row["id"],
        source_url=row["source_url"],
        mode=StreamMode(row["mode"]),
        status=JobStatus(row["status"]),
        playlist_url=playlist_url,
        loop=bool(row["loop"]),
        realtime=bool(row["realtime"]),
        video_bitrate=row["video_bitrate"],
        video_height=row["video_height"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        ready_at=row["ready_at"],
        completed_at=row["completed_at"],
        expires_at=row["expires_at"],
        error=row["error"],
    )


class Repository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def insert_job(
        self,
        source_url: str,
        mode: StreamMode,
        ttl_seconds: int,
        loop: bool = False,
        realtime: bool = True,
        video_bitrate: str | None = None,
        video_height: int | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> JobRead:
        # If start_at is in the future, start the job in 'scheduled' state so
        # the background scheduler picks it up at the right time.
        initial_status = "queued"
        if start_at is not None and start_at > _utcnow():
            initial_status = "scheduled"

        row = await self._pool.fetchrow(
            """
            INSERT INTO jobs (source_url, mode, ttl_seconds, status,
                              loop, realtime, video_bitrate, video_height,
                              start_at, end_at)
            VALUES ($1, $2::stream_mode, $3, $4::job_status,
                    $5, $6, $7, $8, $9, $10)
            RETURNING *
            """,
            source_url,
            mode.value,
            ttl_seconds,
            initial_status,
            loop,
            realtime,
            video_bitrate,
            video_height,
            start_at,
            end_at,
        )
        assert row is not None
        return _row_to_job(row)

    async def list_due_scheduled(self) -> list[JobRead]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM jobs
            WHERE status = 'scheduled'
              AND (start_at IS NULL OR start_at <= now())
            ORDER BY start_at NULLS FIRST
            """
        )
        return [_row_to_job(r) for r in rows]

    async def mark_queued(self, job_id: UUID) -> bool:
        """Promote a scheduled job to queued. Returns True if a row was updated."""
        result = await self._pool.execute(
            """
            UPDATE jobs SET status = 'queued'
            WHERE id = $1 AND status = 'scheduled'
            """,
            job_id,
        )
        try:
            return int(result.split()[-1]) > 0
        except (ValueError, IndexError):
            return False

    async def get_job(self, job_id: UUID) -> JobRead | None:
        row = await self._pool.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
        return _row_to_job(row) if row else None

    async def list_jobs(
        self,
        statuses: list[JobStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobRead]:
        if statuses:
            rows = await self._pool.fetch(
                """
                SELECT * FROM jobs
                WHERE status = ANY($1::job_status[])
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                [s.value for s in statuses],
                limit,
                offset,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT * FROM jobs
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [_row_to_job(r) for r in rows]

    async def list_expired(self) -> list[asyncpg.Record]:
        return await self._pool.fetch(
            """
            SELECT id, playlist_rel FROM jobs
            WHERE status IN ('completed','failed','cancelled')
              AND expires_at IS NOT NULL
              AND expires_at < now()
              AND (last_access_at IS NULL OR last_access_at < now() - ($1::int || ' seconds')::interval)
            """,
            60,  # grace period in seconds; caller may override via wrapper
        )

    async def list_running_ids(self) -> list[UUID]:
        rows = await self._pool.fetch(
            "SELECT id FROM jobs WHERE status = 'running'"
        )
        return [r["id"] for r in rows]

    async def list_queued(self) -> list[JobRead]:
        rows = await self._pool.fetch(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at"
        )
        return [_row_to_job(r) for r in rows]

    async def mark_running(self, job_id: UUID) -> None:
        await self._pool.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = now()
            WHERE id = $1
            """,
            job_id,
        )

    async def mark_ready(self, job_id: UUID, playlist_rel: str) -> None:
        await self._pool.execute(
            """
            UPDATE jobs
            SET status = 'ready', ready_at = now(), playlist_rel = $2
            WHERE id = $1 AND status = 'running'
            """,
            job_id,
            playlist_rel,
        )

    async def mark_completed(self, job_id: UUID) -> None:
        await self._pool.execute(
            """
            UPDATE jobs
            SET status = 'completed',
                completed_at = now(),
                expires_at = now() + (ttl_seconds || ' seconds')::interval
            WHERE id = $1
            """,
            job_id,
        )

    async def mark_failed(self, job_id: UUID, error: str) -> None:
        await self._pool.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                completed_at = now(),
                expires_at = now() + (ttl_seconds || ' seconds')::interval,
                error = $2
            WHERE id = $1
            """,
            job_id,
            error[-4000:],
        )

    async def mark_cancelled(self, job_id: UUID) -> None:
        await self._pool.execute(
            """
            UPDATE jobs
            SET status = 'cancelled',
                completed_at = now(),
                expires_at = now() + (ttl_seconds || ' seconds')::interval
            WHERE id = $1
            """,
            job_id,
        )

    async def mark_expired(self, job_id: UUID) -> None:
        await self._pool.execute(
            "UPDATE jobs SET status = 'expired', playlist_rel = NULL WHERE id = $1",
            job_id,
        )

    async def delete_terminal_job(self, job_id: UUID) -> bool:
        """Hard-delete a job row, but only if it is in a terminal state.

        Returns True if a row was deleted, False if the job is still active
        (caller should respond 409) or already gone (caller should 404).
        """
        result = await self._pool.execute(
            """
            DELETE FROM jobs
            WHERE id = $1
              AND status IN ('completed','failed','cancelled','expired')
            """,
            job_id,
        )
        # result is like "DELETE N"
        try:
            return int(result.split()[-1]) > 0
        except (ValueError, IndexError):
            return False

    async def mark_running_as_failed_on_restart(self) -> int:
        result = await self._pool.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                completed_at = now(),
                expires_at = now() + (ttl_seconds || ' seconds')::interval,
                error = 'server_restart'
            WHERE status = 'running'
            """
        )
        # result like "UPDATE N"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def touch_last_access(self, job_id: UUID) -> None:
        await self._pool.execute(
            "UPDATE jobs SET last_access_at = now() WHERE id = $1", job_id
        )

    async def list_expired_with_grace(self, grace_seconds: int) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT id, playlist_rel FROM jobs
            WHERE status IN ('completed','failed','cancelled')
              AND expires_at IS NOT NULL
              AND expires_at < now()
              AND (last_access_at IS NULL OR last_access_at < now() - ($1 || ' seconds')::interval)
            """,
            str(grace_seconds),
        )
        return [dict(r) for r in rows]
