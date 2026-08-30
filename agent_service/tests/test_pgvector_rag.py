"""真实 PostgreSQL + pgvector 回归；要求项目独立容器已启动并已 seed 政策。"""

import os

import pytest

from agent_service.app.constants import Market
from agent_service.app.policy_store import PolicyStore

PG_RAG_CASES = [
    ("中国手机保修期", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机无法开机", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机维修申请", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("中国手机维修进度", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("China phone warranty", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("China phone power on issue", Market.CN, "CN-MOBILE-AFTERSALES-2026"),
    ("Malaysia phone warranty", Market.MY, "MY-MOBILE-AFTERSALES-2026"),
    ("Malaysia phone cannot power on", Market.MY, "MY-MOBILE-AFTERSALES-2026"),
    ("Malaysia phone repair request", Market.MY, "MY-MOBILE-AFTERSALES-2026"),
    ("Malaysia repair status", Market.MY, "MY-MOBILE-AFTERSALES-2026"),
    ("马来西亚手机保修", Market.MY, "MY-MOBILE-AFTERSALES-2026"),
    ("马来西亚手机无法开机", Market.MY, "MY-MOBILE-AFTERSALES-2026"),
    ("退款规则", Market.CN, None),
    ("换机资格", Market.MY, None),
    ("赔偿政策", Market.CN, None),
    ("智能手表维修", Market.MY, None),
    ("耳机维修", Market.CN, None),
    ("天气预报", Market.MY, None),
    ("游戏账号密码", Market.CN, None),
    ("路由器配置", Market.MY, None),
]


@pytest.fixture(scope="session")
def pg_store() -> PolicyStore:
    database_url = os.getenv(
        "GLOBALCARE_TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@127.0.0.1:55432/globalcare_agent",
    )
    store = PolicyStore(
        "agent_service/data/policies",
        backend="pgvector",
        database_url=database_url,
        embedding_dimensions=256,
    )
    if store.pg_vector_store is None:
        pytest.fail("PGVECTOR_TEST_UNAVAILABLE: 请先启动项目独立 pgvector 容器并执行 seed")
    store.seed_pgvector()
    return store


@pytest.mark.parametrize("query,market,expected_policy", PG_RAG_CASES, ids=[f"pg-rag-{index:02d}" for index in range(1, 21)])
def test_real_pgvector_rag_market_and_guard(pg_store: PolicyStore, query: str, market: Market, expected_policy: str | None) -> None:
    evidence = pg_store.retrieve(query, market, top_k=2)
    if expected_policy is None:
        assert evidence == []
        return
    assert evidence
    assert evidence[0].policy_id == expected_policy
    assert all(item.market == market for item in evidence)
    assert all(item.version == "2026.01" and item.excerpt for item in evidence)

