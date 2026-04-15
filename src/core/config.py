from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Logging
    log_level: str = "info"
    log_format: str = "text"
    log_ffmpeg_stderr_level: str = "info"
    log_ffmpeg_progress_level: str = "debug"
    log_ffmpeg_progress_every: int = Field(default=1, ge=1)

    # Database
    database_url: str = "postgresql://transcoder:transcoder@localhost:5432/transcoder"
    db_pool_min_size: int = 5
    db_pool_max_size: int = 20

    # Storage
    storage_dir: str = "/var/lib/transcoder"
    pidfile_subdir: str = ".pids"

    # Concurrency
    max_concurrency: int = 6
    max_queue_depth: int = 50

    # Job lifecycle
    default_ttl_seconds: int = 300
    max_run_seconds: int = 21600
    grace_janitor_seconds: int = 60
    last_access_debounce_seconds: int = 30

    # Shutdown
    shutdown_grace_seconds: int = 15

    # Janitor
    janitor_interval_seconds: int = 30

    # FFmpeg / HLS
    hls_segment_seconds: int = 4
    hls_live_list_size: int = 6
    x264_preset: str = "veryfast"
    x264_profile: str = "main"
    audio_bitrate: str = "128k"
    audio_channels: int = 2
    audio_sample_rate: int = 48000

    # Security
    cors_allow_origins: list[str] = ["*"]
    url_deny_hostnames: list[str] = [
        "localhost",
        "metadata",
        "metadata.google.internal",
    ]
    url_allow_schemes: list[str] = ["http", "https"]
    # LAN/dev 환경에서 사설 IP 대역(10/8, 172.16/12, 192.168/16 등) 허용.
    # loopback/link-local/multicast/reserved는 여전히 차단됨.
    url_allow_private_ips: bool = False

    # SSE
    sse_ping_interval_seconds: int = 15

    @property
    def pidfile_dir(self) -> str:
        return f"{self.storage_dir}/{self.pidfile_subdir}"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
