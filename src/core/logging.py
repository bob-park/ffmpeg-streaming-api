import logging
import sys
from contextvars import ContextVar

_job_id_ctx: ContextVar[str | None] = ContextVar("job_id", default=None)


class JobIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.job_id = _job_id_ctx.get() or "-"
        return True


def configure_logging(level: str, fmt: str) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(JobIdFilter())

    if fmt == "json":
        from pythonjsonlogger import jsonlogger

        formatter: logging.Formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(job_id)s %(message)s",
            rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(job_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level.upper())

    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("asyncio").setLevel("WARNING")


def bind_job_id(job_id: str) -> None:
    _job_id_ctx.set(job_id)


def current_job_id() -> str | None:
    return _job_id_ctx.get()
