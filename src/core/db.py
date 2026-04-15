import asyncpg

from core.config import Settings


async def create_pool(settings: Settings) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=30,
    )
    assert pool is not None
    return pool


async def run_migrations(pool: asyncpg.Pool, migration_sql: str) -> None:
    async with pool.acquire() as conn:
        # Postgres forbids using a newly-added enum value in the same
        # transaction that added it. Create/extend the job_status enum in
        # standalone statements (auto-committed) before running the main
        # migration, which references 'scheduled' in a partial index.
        await conn.execute(
            """
            DO $$ BEGIN
              CREATE TYPE job_status AS ENUM
                ('scheduled','queued','running','ready','completed','failed','cancelled','expired');
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
            """
        )
        await conn.execute(
            "ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'scheduled' BEFORE 'queued'"
        )
        await conn.execute(migration_sql)
