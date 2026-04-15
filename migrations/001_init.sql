DO $$ BEGIN
  CREATE TYPE job_status AS ENUM
    ('scheduled','queued','running','ready','completed','failed','cancelled','expired');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE stream_mode AS ENUM ('live','vod');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS jobs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_url       TEXT NOT NULL,
  mode             stream_mode NOT NULL,
  status           job_status NOT NULL DEFAULT 'queued',
  ttl_seconds      INTEGER NOT NULL,
  loop             BOOLEAN NOT NULL DEFAULT FALSE,
  start_at         TIMESTAMPTZ,
  end_at           TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at       TIMESTAMPTZ,
  ready_at         TIMESTAMPTZ,
  completed_at     TIMESTAMPTZ,
  expires_at       TIMESTAMPTZ,
  last_access_at   TIMESTAMPTZ,
  error            TEXT,
  playlist_rel     TEXT
);

-- Idempotent additive migrations for existing installs.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS loop     BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS start_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS end_at   TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_expires_at
  ON jobs(expires_at) WHERE status IN ('completed','failed','cancelled');
CREATE INDEX IF NOT EXISTS idx_jobs_scheduled_start_at
  ON jobs(start_at) WHERE status = 'scheduled';
