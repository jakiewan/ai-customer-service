"""初始化 GlobalCare 独立 PostgreSQL schema（会话/审计/checkpoint），不触碰本机 5432 实例。"""

from pathlib import Path

import psycopg

from agent_service.app.config import get_settings


def to_psycopg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> int:
    settings = get_settings()
    migration_dir = Path(__file__).resolve().parents[1] / "agent_service" / "migrations"
    with psycopg.connect(
        to_psycopg_dsn(settings.database_url), autocommit=True
    ) as connection:
        for migration in sorted(migration_dir.glob("*.sql")):
            connection.execute(migration.read_text(encoding="utf-8"))
    print("POSTGRES_SCHEMA_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
