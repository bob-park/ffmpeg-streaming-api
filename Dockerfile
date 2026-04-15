# syntax=docker/dockerfile:1.7

# ─────────────────────────────────────────
# Stage 1: builder — python:3.11-slim + uv
# ─────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN pip install --no-cache-dir uv

WORKDIR /app

# 의존성 메타만 먼저 복사 → 소스 변경시에도 레이어 캐시 유지
COPY pyproject.toml uv.lock ./

# uv.lock 기반 재현 가능 설치. 프로젝트 자체는 설치하지 않음(빌드 백엔드 불필요).
RUN uv sync --frozen --no-install-project --no-dev

# ─────────────────────────────────────────
# Stage 2: runtime — linuxserver/ffmpeg:8.0.1 (ffmpeg 내장, Ubuntu 기반)
# ─────────────────────────────────────────
FROM linuxserver/ffmpeg:8.0.1 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    HOME=/app \
    DEBIAN_FRONTEND=noninteractive

# Python 3.11 (deadsnakes PPA 폴백) + mediainfo + curl(HEALTHCHECK) + tini
# 빌더 단계의 venv는 /usr/local/bin/python3.11 을 기대하므로 심볼릭 링크로 맞춘다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tini \
        mediainfo \
    && ( apt-get install -y --no-install-recommends \
            python3.11 python3.11-venv libpython3.11 \
         || ( apt-get install -y --no-install-recommends software-properties-common gnupg \
              && add-apt-repository -y ppa:deadsnakes/ppa \
              && apt-get update \
              && apt-get install -y --no-install-recommends \
                   python3.11 python3.11-venv libpython3.11 ) ) \
    && ln -sf /usr/bin/python3.11 /usr/local/bin/python3.11 \
    && ln -sf /usr/bin/python3.11 /usr/local/bin/python3 \
    && ln -sf /usr/bin/python3.11 /usr/local/bin/python \
    && apt-get purge -y --auto-remove software-properties-common gnupg 2>/dev/null || true \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# builder에서 만든 venv 복사
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# 앱 소스 / 마이그레이션 복사
COPY --chown=app:app src/ ./src/
COPY --chown=app:app migrations/ ./migrations/

# 런타임 쓰기 디렉터리 (storage, pid 등). 실제 경로는 환경변수로 override 가능.
RUN mkdir -p /app/storage /app/run && chown -R app:app /app/storage /app/run

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# linuxserver/ffmpeg 의 기본 ENTRYPOINT(ffmpeg wrapper) 를 tini 로 완전히 덮어쓴다.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
