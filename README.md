# transcoder-api

FFmpeg `-re` 기반 실시간 HLS 트랜스코딩 REST API. 어떤 비디오 URL(HTTP/HTTPS/S3 presigned)이든 받아서
1배속 페이싱으로 HLS 세그먼트를 만들고, 클라이언트는 트랜스코딩 시작 직후부터 실시간처럼 재생합니다.

## 빠른 시작

```bash
docker compose up --build
```

그 다음 브라우저에서 `http://localhost:8000/` 을 열면 대시보드가 나옵니다.

## API

### POST /jobs
```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"source_url": "https://example.com/video.mp4", "mode": "vod"}'
```

응답 `202 Accepted`:
```json
{
  "id": "...",
  "status": "queued",
  ...
}
```

`mode` 는 `vod` (기본) 또는 `live`:
- **vod**: 모든 세그먼트를 보존. 뒤로 감기 가능.
- **live**: 슬라이딩 윈도우 (기본 6 세그먼트). 뒤로 감기 불가.

### Loop (24/7 가상 채널)

`loop: true` 는 `mode: "live"` 에서만 허용. ffmpeg가 `-stream_loop -1` 로 소스를 무한 반복.
선택적으로 `start_at`/`end_at` (ISO 8601) 로 송출 시간 창을 지정. `start_at` 이 미래면
job은 `scheduled` 상태로 들어가고, 백그라운드 스케줄러가 시간 되면 자동 시작. `end_at` 이 되면
자동 중지.

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "source_url": "https://example.com/loop.mp4",
    "mode": "live",
    "loop": true,
    "start_at": "2026-04-16T20:00:00Z",
    "end_at":   "2026-04-16T23:00:00Z"
  }'
```

**경고**: `-stream_loop -1` 은 입력이 seekable(HTTP range 지원) 해야 동작해요. chunked
transfer, 라이브 원본, Content-Length 없는 스트림은 실패합니다. 또 S3 presigned URL을
쓰면 `end_at` + 버퍼 이상의 만료 시간이 필요해요.

### GET /jobs
전체 목록 (필터링 가능):
```bash
curl "http://localhost:8000/jobs?status=running,ready&limit=50"
```

### GET /jobs/{id}
개별 job 상세.

### POST /jobs/{id}/cancel
실행/대기 중인 job 중지 (SIGTERM → SIGKILL grace). 이미 종료됐으면 `409`.

```bash
curl -X POST http://localhost:8000/jobs/{id}/cancel
```

### DELETE /jobs/{id}
터미널 상태 job(완료/실패/취소/만료) 의 DB 레코드와 HLS 세그먼트 디렉토리를 완전히 삭제.
아직 실행 중이면 `409` — 먼저 cancel 호출하세요.

```bash
curl -X DELETE http://localhost:8000/jobs/{id}
```

### GET /jobs/{id}/events (SSE)
특정 job의 라이프사이클 이벤트 스트림 (snapshot + live).

### GET /events (SSE)
모든 job의 상태 변경 이벤트 글로벌 스트림 (대시보드용).

### GET /streams/{id}/playlist.m3u8
HLS 재생 URL. `ready` 이벤트 발행 후 재생 가능. 클라이언트는 경로를 직접 조립하지 말고
`JobRead.playlist_url` 필드를 사용하세요. `ready` 이전에는 `null` 입니다.

## 설정

모든 설정은 `.env` 에서 오버라이드. `.env.example` 참고.

주요 키:
- `LOG_LEVEL` (`debug|info|warning|error`), `LOG_FORMAT` (`text|json`)
- `MAX_CONCURRENCY`, `MAX_QUEUE_DEPTH`
- `DEFAULT_TTL_SECONDS`, `MAX_RUN_SECONDS`
- `DATABASE_URL`

`LOG_LEVEL=debug` 로 띄우면 ffmpeg 의 실시간 진행(프레임/fps/speed/out_time/bitrate) 이 로그로 흘러요.

## 중요한 제약

- **S3 presigned URL**은 `source 길이 + 버퍼` 이상의 유효기간이 필요합니다. 1시간 비디오라면 presigned
  URL의 만료도 1시간 이상이어야 중간에 깨지지 않습니다.
- **`-re` = 1배속 페이싱**입니다. 1시간 비디오는 ~1시간 걸립니다. 이건 의도된 동작입니다.
- **단일 uvicorn worker 강제**. SSE 이벤트 버스가 프로세스 내부 asyncio 큐라서 다중 워커 불가.
- **인증은 범위 밖**입니다. 리버스 프록시 뒤에서 운영하거나 로컬에서만 노출하세요.

## 개발

이 프로젝트는 [uv](https://docs.astral.sh/uv/) 로 의존성을 관리합니다 (`uv.lock` 커밋됨).

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy src
```

또는 venv를 활성화해서 쓰셔도 됩니다:

```bash
uv sync --extra dev
source .venv/bin/activate
pytest
```
