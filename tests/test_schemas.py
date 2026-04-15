from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from core.schemas import JobCreate, JobStatus, StreamMode


def _future(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def test_minimum_payload_defaults_to_vod_no_loop():
    j = JobCreate(source_url="https://example.com/a.mp4")
    assert j.mode == StreamMode.VOD
    assert j.loop is False
    assert j.start_at is None
    assert j.end_at is None


def test_loop_requires_live_mode():
    with pytest.raises(ValidationError):
        JobCreate(
            source_url="https://example.com/a.mp4",
            mode=StreamMode.VOD,
            loop=True,
        )


def test_loop_live_ok():
    j = JobCreate(
        source_url="https://example.com/a.mp4",
        mode=StreamMode.LIVE,
        loop=True,
    )
    assert j.loop is True


def test_start_end_without_loop_rejected():
    with pytest.raises(ValidationError):
        JobCreate(
            source_url="https://example.com/a.mp4",
            mode=StreamMode.LIVE,
            loop=False,
            start_at=_future(60),
            end_at=_future(3600),
        )


def test_end_before_start_rejected():
    with pytest.raises(ValidationError):
        JobCreate(
            source_url="https://example.com/a.mp4",
            mode=StreamMode.LIVE,
            loop=True,
            start_at=_future(3600),
            end_at=_future(60),
        )


def test_end_in_past_rejected():
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    with pytest.raises(ValidationError):
        JobCreate(
            source_url="https://example.com/a.mp4",
            mode=StreamMode.LIVE,
            loop=True,
            end_at=past,
        )


def test_start_in_past_ok_end_in_future():
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    j = JobCreate(
        source_url="https://example.com/a.mp4",
        mode=StreamMode.LIVE,
        loop=True,
        start_at=past,
        end_at=_future(3600),
    )
    assert j.loop is True


def test_scheduled_status_in_enum():
    assert JobStatus.SCHEDULED.value == "scheduled"
