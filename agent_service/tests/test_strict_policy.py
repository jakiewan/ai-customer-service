"""严格检索与证据回答，不把开发 fallback 当生产成功。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document

from agent_service.app.config import Settings
from agent_service.app.main import create_app
from agent_service.app.policy_store import PolicyStore


def test_strict_pgvector_unavailable_does_not_silently_use_memory():
    with pytest.raises(RuntimeError):
        PolicyStore("agent_service/data/policies", backend="pgvector",
                    database_url="postgresql+psycopg://invalid:invalid@127.0.0.1:1/unavailable", allow_fallback=False)


def test_strict_policy_rejects_documents_without_version(tmp_path):
    (tmp_path / "bad.md").write_text("---\nmarket: CN\ntitle: policy\n---\nWarranty 24 months", encoding="utf-8")
    with pytest.raises(ValueError):
        PolicyStore(tmp_path, allow_fallback=False)


def test_retriever_checks_market_even_when_backend_filter_is_broken():
    store = PolicyStore("agent_service/data/policies")

    class WrongMarketDatabase:
        def similarity_search(self, *_args, **_kwargs):
            return [Document(page_content="保修期 99 个月", metadata={"market": "US", "policy_id": "wrong", "version": "1", "title": "CN", "language": "zh-CN", "goodsid": "65539", "sales_channel": "MALL"})]

    store.pg_vector_store = WrongMarketDatabase()
    assert store.retrieve("手机保修期", "CN", goodsid="65539", sales_channel="MALL") == []


def test_policy_answer_uses_document_duration_not_twelve_month_constant(tmp_path):
    original = Path("agent_service/data/policies/policy_cn.md").read_text(encoding="utf-8")
    (tmp_path / "policy.md").write_text(original.replace("12 个月", "24 个月"), encoding="utf-8")
    settings = Settings(_env_file=None, policy_dir=str(tmp_path))
    with TestClient(create_app(settings)) as client:
        headers = {"X-Demo-Token": settings.demo_token}
        created = client.post("/api/v1/conversations", headers=headers, json={"market": "CN", "goodsid": "65539", "sales_channel": "MALL"})
        response = client.post("/api/v1/conversations/" + created.json()["data"]["conversation_id"] + "/messages/stream",
                               headers=headers, json={"client_message_id": "duration", "message": "手机保修期多久"})
        assert "24 个月" in response.text
        assert "12 个月" not in response.text


def test_production_policy_cannot_approve_simulated_documents(tmp_path):
    content = Path("agent_service/data/policies/policy_cn.md").read_text(encoding="utf-8")
    (tmp_path / "simulated.md").write_text(content.replace("simulated: true", "simulated: true\napproved: true"), encoding="utf-8")
    with pytest.raises(ValueError):
        PolicyStore(tmp_path, require_approved=True)
