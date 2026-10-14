"""把 FAQ 目录与政策 Markdown 写入 Milvus(intent_corpus / faq_question) 与 Redis。

用法（需先启动 milvus-standalone 与项目 Redis 容器）:
    GLOBALCARE_EMBEDDING_MODE=huggingface PYTHONPATH=. python scripts/seed_milvus_redis.py

重复执行是幂等的：两个 collection 会被重建，本项目 Redis 前缀 gc:faq:* 会被清空后重写。
"""

import json
import sys
from pathlib import Path

from agent_service.app.config import get_settings
from agent_service.app.faq_catalog import KEY_PREFIX, META_KEY, build_bundle, redis_payload
from agent_service.app.knowledge_base import (
    DEFAULT_CATALOG,
    FAQ_COLLECTION,
    INTENT_COLLECTION,
    build_embeddings,
    embedding_signature,
)

POLICY_DIR = Path(__file__).resolve().parents[1] / "agent_service" / "data" / "policies"


def _create_collection(client, name: str, dimensions: int, faq: bool) -> None:
    from pymilvus import DataType, MilvusClient

    if client.has_collection(name):
        client.drop_collection(name)
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("intent_id", DataType.VARCHAR, max_length=64)
    if faq:
        schema.add_field("faq_group_id", DataType.VARCHAR, max_length=64)
        schema.add_field("faq_item_id", DataType.VARCHAR, max_length=64)
        # 适用维度：标量过滤 ARRAY_CONTAINS(goodsids, ...) / ARRAY_CONTAINS(sales_channels, ...)
        schema.add_field("goodsids", DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=64, max_length=16)
        schema.add_field("sales_channels", DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=8, max_length=16)
    schema.add_field("text", DataType.VARCHAR, max_length=512)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dimensions)
    index = client.prepare_index_params()
    index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(name, schema=schema, index_params=index)


def main() -> int:
    settings = get_settings()
    bundle = build_bundle(DEFAULT_CATALOG, POLICY_DIR, strict=True)
    if settings.embedding_mode != "huggingface":
        print("WARNING: 当前 embedding_mode=hash，没有语义能力，仅用于联调。", file=sys.stderr)
    embeddings = build_embeddings(settings.embedding_mode, settings.embedding_model,
                                  settings.embedding_dimensions, allow_fallback=False)
    intent_rows = [{"id": key, **row} for key, row in bundle.intent_rows.items()]
    intent_vectors = embeddings.embed_documents([row["text"] for row in intent_rows])
    faq_vectors = embeddings.embed_documents([row["text"] for row in bundle.faq_rows])
    dimensions = len(intent_vectors[0])
    if dimensions != settings.embedding_dimensions:
        print(f"EMBEDDING_DIM_MISMATCH model={dimensions} config={settings.embedding_dimensions}", file=sys.stderr)
        return 2

    import redis
    from pymilvus import MilvusClient

    root = MilvusClient(uri=settings.milvus_uri, timeout=10)
    if settings.milvus_db not in root.list_databases():
        root.create_database(settings.milvus_db)
    client = MilvusClient(uri=settings.milvus_uri, db_name=settings.milvus_db, timeout=10)
    _create_collection(client, INTENT_COLLECTION, dimensions, faq=False)
    _create_collection(client, FAQ_COLLECTION, dimensions, faq=True)
    client.insert(INTENT_COLLECTION, [{**row, "vector": vec} for row, vec in zip(intent_rows, intent_vectors)])
    client.insert(FAQ_COLLECTION, [{**row, "vector": vec} for row, vec in zip(bundle.faq_rows, faq_vectors)])
    for name in (INTENT_COLLECTION, FAQ_COLLECTION):
        client.flush(name)
        client.load_collection(name)

    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    stale = list(redis_client.scan_iter(f"{KEY_PREFIX}:*"))
    pipe = redis_client.pipeline(transaction=True)
    if stale:
        pipe.delete(*stale)
    pipe.mset(redis_payload(bundle))
    # meta 最后写入：运行时据此确认数据完整且与当前 Embedding 一致。
    pipe.set(META_KEY, json.dumps({
        "embedding": embedding_signature(settings.embedding_mode, settings.embedding_model, dimensions),
        "faq_items": len(bundle.items), "answers": len(bundle.answers),
    }, ensure_ascii=False))
    pipe.execute()
    print(f"SEEDED intent_corpus={len(intent_rows)} faq_question={len(bundle.faq_rows)} "
          f"redis_exact={len(bundle.exact)} redis_answers={len(bundle.answers)} dim={dimensions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
