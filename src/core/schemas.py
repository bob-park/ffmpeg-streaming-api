from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class StreamMode(str, Enum):
    LIVE = "live"
    VOD = "vod"


class JobStatus(str, Enum):
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class JobCreate(BaseModel):
    source_url: str = Field(..., min_length=1, max_length=8192)
    mode: StreamMode = StreamMode.VOD
    ttl_seconds: int | None = Field(default=None, ge=60, le=86400)
    loop: bool = False
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def _check_loop_window(self) -> "JobCreate":
        if self.loop and self.mode != StreamMode.LIVE:
            raise ValueError("loop requires mode='live'")
        if not self.loop and (self.start_at is not None or self.end_at is not None):
            raise ValueError("start_at/end_at only allowed when loop=true")
        if self.start_at is not None and self.end_at is not None:
            if _as_utc(self.end_at) <= _as_utc(self.start_at):
                raise ValueError("end_at must be after start_at")
        if self.end_at is not None:
            if _as_utc(self.end_at) <= datetime.now(timezone.utc):
                raise ValueError("end_at must be in the future")
        return self


class JobRead(BaseModel):
    id: UUID
    source_url: str
    mode: StreamMode
    status: JobStatus
    playlist_url: str | None = None
    loop: bool = False
    start_at: datetime | None = None
    end_at: datetime | None = None
    created_at: datetime
    started_at: datetime | None = None
    ready_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    error: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    code: str
