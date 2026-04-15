import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path
from uuid import UUID

from core.config import Settings
from core.logging import bind_job_id
from core.repository import Repository
from core.schemas import StreamMode
from events.bus import Event, EventBus
from transcoder.ffmpeg_cmd import build_ffmpeg_argv
from transcoder.orphans import remove_pidfile, write_pidfile

log = logging.getLogger("transcoder.runner")


async def _drain_stderr(
    proc: asyncio.subprocess.Process,
    buf: list[bytes],
    settings: Settings,
) -> None:
    assert proc.stderr is not None
    line_log = logging.getLogger("transcoder.ffmpeg.stderr")
    level = getattr(logging, settings.log_ffmpeg_stderr_level.upper())
    async for raw in proc.stderr:
        buf.append(raw)
        total = sum(len(b) for b in buf)
        while total > 4096 and len(buf) > 1:
            total -= len(buf.pop(0))
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            line_log.log(level, line)


async def _drain_progress(
    proc: asyncio.subprocess.Process,
    settings: Settings,
) -> None:
    assert proc.stdout is not None
    p_log = logging.getLogger("transcoder.ffmpeg.progress")
    level = getattr(logging, settings.log_ffmpeg_progress_level.upper())
    every = max(1, settings.log_ffmpeg_progress_every)
    block: dict[str, str] = {}
    counter = 0
    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        block[key] = val
        if key == "progress":
            counter += 1
            if counter % every == 0:
                try:
                    frame = int(block.get("frame", "0") or "0")
                except ValueError:
                    frame = 0
                try:
                    fps = float(block.get("fps", "0") or "0")
                except ValueError:
                    fps = 0.0
                speed = block.get("speed", "?")
                out_time = block.get("out_time", "?")
                bitrate = block.get("bitrate", "?")
                try:
                    drop_frames = int(block.get("drop_frames", "0") or "0")
                except ValueError:
                    drop_frames = 0
                msg = (
                    f"progress frame={frame} fps={fps:.1f} speed={speed} "
                    f"t={out_time} br={bitrate} drop={drop_frames}"
                )
                p_log.log(
                    level,
                    msg,
                    extra={
                        "progress": {
                            "frame": frame,
                            "fps": fps,
                            "speed": speed,
                            "out_time": out_time,
                            "bitrate": bitrate,
                            "drop_frames": drop_frames,
                            "state": val,
                        }
                    },
                )
            block.clear()


async def _watch_ready(
    output_dir: Path,
    job_id: UUID,
    repo: Repository,
    bus: EventBus,
    poll_interval: float = 0.5,
) -> None:
    playlist = output_dir / "playlist.m3u8"
    while True:
        try:
            if playlist.exists():
                text = playlist.read_text(errors="ignore")
                if "#EXTINF" in text:
                    rel = f"{job_id}/playlist.m3u8"
                    await repo.mark_ready(job_id, rel)
                    await bus.publish(
                        Event(
                            type="ready",
                            job_id=job_id,
                            data={"playlist_url": f"/streams/{rel}"},
                        )
                    )
                    return
        except OSError:
            pass
        await asyncio.sleep(poll_interval)


async def _watchdog(
    proc: asyncio.subprocess.Process,
    pgid: int,
    max_run_seconds: int,
) -> None:
    await asyncio.sleep(max_run_seconds)
    if proc.returncode is None:
        log.warning("watchdog timeout — killing pgid %d", pgid)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
        await asyncio.sleep(5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)


async def _cancel_watcher(
    cancel_event: asyncio.Event,
    pgid: int,
    grace_seconds: int,
) -> None:
    await cancel_event.wait()
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    await asyncio.sleep(grace_seconds)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)


async def _end_time_watcher(
    cancel_event: asyncio.Event,
    end_at,
) -> None:
    """Trip the cancel_event when wall-clock time reaches end_at."""
    from datetime import datetime, timezone

    if end_at is None:
        return
    end_utc = end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)
    while True:
        delta = (end_utc - datetime.now(timezone.utc)).total_seconds()
        if delta <= 0:
            log.info("end_at reached — signalling cancel")
            cancel_event.set()
            return
        # Sleep in chunks to respond quickly to external cancels too.
        await asyncio.sleep(min(delta, 30))


async def run_job(
    job_id: UUID,
    source_url: str,
    mode: StreamMode,
    settings: Settings,
    repo: Repository,
    bus: EventBus,
    cancel_event: asyncio.Event,
    loop: bool = False,
    realtime: bool = True,
    video_bitrate: str | None = None,
    video_height: int | None = None,
    end_at=None,
) -> None:
    """Run a single transcoding job. Exceptions are caught and recorded as failures."""
    bind_job_id(str(job_id))
    output_dir = Path(settings.storage_dir) / str(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    argv = build_ffmpeg_argv(
        source_url,
        str(output_dir),
        mode,
        settings,
        loop=loop,
        realtime=realtime,
        video_bitrate=video_bitrate,
        video_height=video_height,
    )
    log.info(
        "starting ffmpeg argv_len=%d mode=%s loop=%s realtime=%s "
        "v_br=%s v_h=%s end_at=%s",
        len(argv),
        mode.value,
        loop,
        realtime,
        video_bitrate,
        video_height,
        end_at,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        log.exception("failed to spawn ffmpeg")
        await repo.mark_failed(job_id, f"spawn_error:{e}")
        await bus.publish(
            Event(type="error", job_id=job_id, data={"message": f"spawn_error:{e}"})
        )
        return

    pgid = os.getpgid(proc.pid)
    write_pidfile(settings.pidfile_dir, str(job_id), pgid)

    await repo.mark_running(job_id)
    await bus.publish(
        Event(
            type="status_change",
            job_id=job_id,
            data={"old": "queued", "new": "running"},
        )
    )

    stderr_buf: list[bytes] = []
    stderr_task = asyncio.create_task(_drain_stderr(proc, stderr_buf, settings))
    progress_task = asyncio.create_task(_drain_progress(proc, settings))
    ready_task = asyncio.create_task(_watch_ready(output_dir, job_id, repo, bus))
    watchdog_task = asyncio.create_task(_watchdog(proc, pgid, settings.max_run_seconds))
    cancel_task = asyncio.create_task(
        _cancel_watcher(cancel_event, pgid, settings.shutdown_grace_seconds)
    )
    end_time_task = asyncio.create_task(_end_time_watcher(cancel_event, end_at))

    try:
        returncode = await proc.wait()
    finally:
        for t in (ready_task, watchdog_task, cancel_task, end_time_task):
            t.cancel()
        await asyncio.gather(
            ready_task, watchdog_task, cancel_task, end_time_task,
            return_exceptions=True,
        )
        for drain in (stderr_task, progress_task):
            try:
                await asyncio.wait_for(asyncio.shield(drain), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                drain.cancel()
                with contextlib.suppress(BaseException):
                    await drain
        remove_pidfile(settings.pidfile_dir, str(job_id))

    tail = b"".join(stderr_buf)[-4000:].decode("utf-8", errors="replace")

    if cancel_event.is_set():
        await repo.mark_cancelled(job_id)
        await bus.publish(
            Event(
                type="status_change",
                job_id=job_id,
                data={"old": "running", "new": "cancelled"},
            )
        )
        return

    if returncode == 0:
        await repo.mark_completed(job_id)
        await bus.publish(Event(type="completed", job_id=job_id))
    else:
        err_msg = f"ffmpeg exit={returncode}\n{tail}"
        await repo.mark_failed(job_id, err_msg)
        await bus.publish(
            Event(type="error", job_id=job_id, data={"message": err_msg[-500:]})
        )
