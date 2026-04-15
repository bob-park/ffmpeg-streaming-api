import logging
import os
import signal
from pathlib import Path

log = logging.getLogger("transcoder.orphans")


def _pid_file(pidfile_dir: Path, job_id: str) -> Path:
    return pidfile_dir / job_id


def write_pidfile(pidfile_dir: str | Path, job_id: str, pgid: int) -> None:
    d = Path(pidfile_dir)
    d.mkdir(parents=True, exist_ok=True)
    _pid_file(d, job_id).write_text(str(pgid))


def remove_pidfile(pidfile_dir: str | Path, job_id: str) -> None:
    try:
        _pid_file(Path(pidfile_dir), job_id).unlink()
    except FileNotFoundError:
        pass


def reap_orphans(pidfile_dir: str | Path) -> int:
    """Kill any ffmpeg process groups left over from a previous instance.

    Each pidfile holds a pgid. We SIGKILL the group and remove the file.
    Returns the number of orphaned groups killed.
    """
    d = Path(pidfile_dir)
    if not d.exists():
        return 0

    killed = 0
    for f in d.iterdir():
        if not f.is_file():
            continue
        try:
            pgid = int(f.read_text().strip())
        except (ValueError, OSError):
            f.unlink(missing_ok=True)
            continue
        try:
            os.killpg(pgid, signal.SIGKILL)
            killed += 1
            log.info("reaped orphan ffmpeg pgid=%d", pgid)
        except ProcessLookupError:
            pass
        except PermissionError:
            log.warning("cannot killpg %d (permission)", pgid)
        f.unlink(missing_ok=True)
    return killed
