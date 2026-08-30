"""把模拟政策写入 pgvector；数据库不可用时返回非零并保留原因。"""

from pathlib import Path

from agent_service.app.config import get_settings
from agent_service.app.policy_store import PolicyStore


def main() -> int:
    settings = get_settings()
    policy_dir = Path(__file__).resolve().parents[1] / "agent_service" / "data" / "policies"
    store = PolicyStore(
        policy_dir,
        backend="pgvector",
        database_url=settings.database_url,
        embedding_mode=settings.embedding_mode,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
    )
    if store.pg_vector_store is None:
        print("PGVECTOR_UNAVAILABLE")
        return 2
    count = store.seed_pgvector()
    print(f"SEEDED_POLICIES={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
