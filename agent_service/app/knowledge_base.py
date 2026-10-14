"""知识检索端口：L1 Redis 精确匹配 + L2 Milvus 语义检索 + 答案变体适用性审查。

backend=milvus 时读 Milvus(`intent_corpus`/`faq_question`) 与 Redis；backend=memory
或允许降级时，用同源 FAQ 目录在进程内模拟，保证离线测试与线上语义一致。
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.embeddings import Embeddings

from .constants import CATEGORY_NAMES, Market, SalesChannel
from .faq_catalog import (
    META_KEY,
    FaqRef,
    KnowledgeBundle,
    answer_key,
    build_bundle,
    exact_key,
    version_key,
)
from .schemas import PolicyEvidence

logger = logging.getLogger(__name__)

INTENT_COLLECTION = "intent_corpus"
FAQ_COLLECTION = "faq_question"
DEFAULT_CATALOG = (
    Path(__file__).resolve().parents[1] / "data" / "faq" / "faq_catalog.yaml"
)
UNSUPPORTED_POLICY_TERMS = (
    "退款",
    "退货",
    "换机",
    "赔偿",
    "赔付",
    "支付",
    "refund",
    "replacement",
    "compensation",
)
UNSUPPORTED_PRODUCT_TERMS = (
    "智能手表",
    "手表",
    "耳机",
    "笔记本",
    "路由器",
    "平板",
    "laptop",
    "watch",
    "earbuds",
    "router",
)


def _tokens(text: str) -> list[str]:
    """对中英文做轻量 token 化，避免离线模式依赖外部分词服务。"""

    words = re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]", text.lower())
    return words + [
        "".join(words[index : index + 2]) for index in range(len(words) - 1)
    ]


class HashEmbeddings(Embeddings):
    """确定性离线 Embeddings，仅供 CI/离线；没有真实语义能力。"""

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        norm = sum(value * value for value in vector) ** 0.5
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def build_embeddings(
    mode: str, model_name: str, dimensions: int, allow_fallback: bool = True
) -> Embeddings:
    """huggingface 模式加载本地 BGE-M3（dense 1024 维）；失败时按严格度决定是否降级到 hash。"""

    if mode.lower() == "huggingface":
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            return HuggingFaceEmbeddings(
                model_name=model_name,
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True},
            )
        except Exception as exc:  # noqa: BLE001 - 本地模型加载失败时按严格度处理。
            logger.error("Embedding 初始化失败: type=%s", type(exc).__name__)
            if not allow_fallback:
                raise RuntimeError("Embedding 不可用，严格模式禁止降级") from None
    return HashEmbeddings(dimensions=dimensions)


@dataclass(frozen=True)
class Thresholds:
    intent_min_score: float
    intent_min_margin: float
    faq_min_score: float
    faq_min_margin: float


def default_thresholds(embedding_mode: str) -> Thresholds:
    # BGE-M3 与 hash 的余弦分布不同，阈值分开；正式口径见 SDD缺口清单 GAP-005。
    if embedding_mode.lower() == "huggingface":
        # 0.72 依据真实 BGE-M3 联调：越界问法“开发票”误命中 AFTERSALES_LINK 得分 0.68。
        return Thresholds(
            intent_min_score=0.72,
            intent_min_margin=0.03,
            faq_min_score=0.62,
            faq_min_margin=0.02,
        )
    return Thresholds(
        intent_min_score=0.45,
        intent_min_margin=0.05,
        faq_min_score=0.35,
        faq_min_margin=0.02,
    )


@dataclass(frozen=True)
class IntentHit:
    intent_id: str
    score: float
    margin: float


@dataclass(frozen=True)
class FaqMatch:
    item: FaqRef | None
    score: float = 0.0
    candidates: tuple[str, ...] = ()  # 分差过小时交给用户选择的 faq_item_id


def _cosine(left: list[float], right: list[float]) -> float:
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def _ranked(hits: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """按 ID 聚合，取每个 ID 的最高分后降序。"""

    best: dict[str, float] = {}
    for key, score in hits:
        best[key] = max(best.get(key, 0.0), score)
    return sorted(best.items(), key=lambda item: item[1], reverse=True)


def _field(hit: dict, name: str):
    entity = hit.get("entity") or {}
    return entity.get(name, hit.get(name))


class _MemoryIndex:
    """离线/降级用的进程内索引，过滤语义与 Milvus 标量过滤一致。"""

    def __init__(self, embeddings: Embeddings, bundle: KnowledgeBundle) -> None:
        self.intent_rows = list(bundle.intent_rows.values())
        self.faq_rows = bundle.faq_rows
        self.intent_vectors = (
            embeddings.embed_documents([row["text"] for row in self.intent_rows])
            if self.intent_rows
            else []
        )
        self.faq_vectors = (
            embeddings.embed_documents([row["text"] for row in self.faq_rows])
            if self.faq_rows
            else []
        )

    def search_intents(self, vector: list[float]) -> list[tuple[str, float]]:
        return [
            (row["intent_id"], _cosine(vector, vec))
            for row, vec in zip(self.intent_rows, self.intent_vectors)
        ]

    def search_faq(
        self, vector: list[float], group: str | None, goodsid: str, channel: str
    ) -> list[tuple[str, float]]:
        return [
            (row["faq_item_id"], _cosine(vector, vec))
            for row, vec in zip(self.faq_rows, self.faq_vectors)
            if (group is None or row["faq_group_id"] == group)
            and goodsid in row["goodsids"]
            and channel in row["sales_channels"]
        ]


def embedding_signature(mode: str, model_name: str, dimensions: int) -> str:
    """seed 与运行时必须一致，否则向量空间不同，语义检索结果无意义。"""

    return (
        f"huggingface:{model_name}:{dimensions}"
        if mode.lower() == "huggingface"
        else f"hash:{dimensions}"
    )


class KnowledgeStore:
    """四层漏斗的知识端口：L1 exact_match、L2 classify_intent/match_faq、答案适用性审查 answer。"""

    def __init__(
        self,
        policy_dir: str | Path,
        *,
        backend: str = "memory",
        catalog_path: str | Path = DEFAULT_CATALOG,
        embedding_mode: str = "hash",
        embedding_model: str = "BAAI/bge-m3",
        embedding_dimensions: int = 1024,
        milvus_uri: str = "http://127.0.0.1:19530",
        milvus_db: str = "ai_customer_service",
        redis_url: str = "redis://127.0.0.1:56379/0",
        allow_fallback: bool = True,
        require_approved: bool = False,
        thresholds: Thresholds | None = None,
    ) -> None:
        self.backend = backend
        self.allow_fallback = allow_fallback
        self.require_approved = require_approved
        self.bundle = build_bundle(
            catalog_path,
            policy_dir,
            strict=not allow_fallback,
            require_approved=require_approved,
        )
        self.embeddings = build_embeddings(
            embedding_mode, embedding_model, embedding_dimensions, allow_fallback
        )
        self.signature = embedding_signature(
            embedding_mode, embedding_model, embedding_dimensions
        )
        self.thresholds = thresholds or default_thresholds(embedding_mode)
        self.milvus = None
        self.redis = None
        self.degraded = False
        self._memory: _MemoryIndex | None = None
        if backend == "milvus":
            try:
                self._connect(milvus_uri, milvus_db, redis_url)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - 由严格度决定是否允许降级到同源内存索引。
                logger.error("Milvus/Redis 初始化失败: type=%s", type(exc).__name__)
                self.milvus = self.redis = None
                self.degraded = True
                if not allow_fallback:
                    raise RuntimeError(
                        "Milvus/Redis 不可用，严格模式禁止降级"
                    ) from None
        if self.milvus is None:
            self._memory = _MemoryIndex(self.embeddings, self.bundle)

    def _connect(self, milvus_uri: str, milvus_db: str, redis_url: str) -> None:
        import redis
        from pymilvus import MilvusClient

        redis_client = redis.Redis.from_url(
            redis_url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True
        )
        meta = json.loads(redis_client.get(META_KEY) or "{}")
        if meta.get("embedding") != self.signature:
            raise RuntimeError(
                "Redis/Milvus 数据缺失或与当前 Embedding 不一致，请重新执行 seed"
            )
        client = MilvusClient(uri=milvus_uri, db_name=milvus_db, timeout=3)
        for name in (INTENT_COLLECTION, FAQ_COLLECTION):
            if not client.has_collection(name):
                raise RuntimeError(f"缺少 Milvus collection {name}")
            client.load_collection(name)
        self.milvus, self.redis = client, redis_client

    @property
    def active_backend(self) -> str:
        return "milvus" if self.milvus is not None else "memory"

    @staticmethod
    def is_unsupported(query: str) -> bool:
        lowered = query.lower()
        return any(
            term.lower() in lowered
            for term in (*UNSUPPORTED_POLICY_TERMS, *UNSUPPORTED_PRODUCT_TERMS)
        )

    def intent_meta(self, intent_id: str | None):
        return self.bundle.intents.get(str(intent_id)) if intent_id else None

    def item(self, faq_item_id: str | None) -> FaqRef | None:
        return self.bundle.items.get(str(faq_item_id)) if faq_item_id else None

    def standard_question(self, faq_item_id: str) -> str:
        return self.bundle.standard_questions.get(faq_item_id, faq_item_id)

    def _guard(self, remote, local):
        """远端失败时按严格度决定是否降级到同源内存索引。"""

        if self.milvus is None:
            return local()
        try:
            result = remote()
            self.degraded = False
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("知识检索不可用: type=%s", type(exc).__name__)
            self.degraded = True
            if not self.allow_fallback:
                raise RuntimeError("知识检索不可用，严格模式禁止降级") from None
            if self._memory is None:
                self._memory = _MemoryIndex(self.embeddings, self.bundle)
            return local()

    # ---- L1：Redis 精确匹配 ----
    def exact_match(self, query: str) -> FaqRef | None:
        key = exact_key(query)

        def remote():
            raw = self.redis.get(key)
            return FaqRef(**json.loads(raw)) if raw else None

        ref = self._guard(remote, lambda: self.bundle.exact.get(key))
        return ref if ref is not None and ref.faq_item_id in self.bundle.items else None

    # ---- L2 第 1 步：意图语料检索 + 按 intent_id 聚合 ----
    def classify_intent(self, query: str) -> IntentHit | None:
        vector = self.embeddings.embed_query(query)
        hits = self._guard(
            lambda: self._milvus_search(INTENT_COLLECTION, vector, "", "intent_id"),
            lambda: self._memory.search_intents(vector),
        )
        ranked = [
            (key, score) for key, score in _ranked(hits) if key in self.bundle.intents
        ]
        if not ranked or ranked[0][1] < self.thresholds.intent_min_score:
            return None
        margin = ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0.0)
        if margin < self.thresholds.intent_min_margin:
            return None
        return IntentHit(ranked[0][0], round(ranked[0][1], 4), round(margin, 4))

    # ---- L2 第 4 步：FAQ 组内检索 + 按 faq_item_id 聚合 + 分差 ----
    def match_faq(
        self,
        query: str,
        faq_group_id: str | None,
        goodsid: str | None,
        sales_channel: str | None,
    ) -> FaqMatch:
        groups = {ref.faq_group_id for ref in self.bundle.items.values()}
        if (
            goodsid not in CATEGORY_NAMES
            or sales_channel not in set(SalesChannel)
            or (faq_group_id and faq_group_id not in groups)
        ):
            return FaqMatch(None)
        # 所有过滤值均已白名单校验，表达式不拼接用户原文。
        expr = f'ARRAY_CONTAINS(goodsids, "{goodsid}") and ARRAY_CONTAINS(sales_channels, "{sales_channel}")'
        if faq_group_id:
            expr = f'faq_group_id == "{faq_group_id}" and {expr}'
        vector = self.embeddings.embed_query(query)
        hits = self._guard(
            lambda: self._milvus_search(FAQ_COLLECTION, vector, expr, "faq_item_id"),
            lambda: self._memory.search_faq(
                vector, faq_group_id, goodsid, sales_channel
            ),
        )
        ranked = [
            (key, score) for key, score in _ranked(hits) if key in self.bundle.items
        ]
        if not ranked or ranked[0][1] < self.thresholds.faq_min_score:
            return FaqMatch(None)
        if (
            len(ranked) > 1
            and ranked[0][1] - ranked[1][1] < self.thresholds.faq_min_margin
        ):
            return FaqMatch(None, ranked[0][1], (ranked[0][0], ranked[1][0]))
        return FaqMatch(self.bundle.items[ranked[0][0]], round(ranked[0][1], 4))

    def _milvus_search(
        self, collection: str, vector: list[float], expr: str, key_field: str
    ) -> list[tuple[str, float]]:
        results = self.milvus.search(
            collection_name=collection,
            data=[vector],
            limit=10,
            filter=expr,
            output_fields=[key_field],
            search_params={"metric_type": "COSINE"},
        )
        return [
            (str(_field(hit, key_field)), float(hit.get("distance", 0.0)))
            for hit in (results[0] if results else [])
        ]

    # ---- L2 第 5/6 步：Redis 按 faq_item_id + version 取答案，并做适用性审查 ----
    def answer(
        self,
        faq_item_id: str,
        goodsid: str | None,
        sales_channel: str | None,
        score: float = 1.0,
    ) -> PolicyEvidence | None:
        if faq_item_id not in self.bundle.items or not goodsid or not sales_channel:
            return None
        pointer = version_key(faq_item_id, goodsid, sales_channel)

        def lookup(get):
            version = get(pointer)
            if not version:
                return None
            raw = get(answer_key(faq_item_id, goodsid, sales_channel, version))
            return json.loads(raw) if isinstance(raw, str) else raw

        def local_get(key):
            value = self.bundle.versions.get(key) or self.bundle.answers.get(key)
            return value

        payload = self._guard(lambda: lookup(self.redis.get), lambda: lookup(local_get))
        if not self._applicable(payload, faq_item_id, goodsid, sales_channel):
            return None
        return PolicyEvidence(
            policy_id=payload["policy_id"],
            title=payload["title"],
            version=payload["version"],
            market=Market.CN,
            language=payload["language"],
            goodsid=payload["goodsid"],
            sales_channel=payload["sales_channel"],
            excerpt=payload["answer"],
            score=round(max(0.0, min(1.0, score)), 3),
            faq_item_id=faq_item_id,
            retrieval_backend=self.active_backend,
            degraded=self.degraded,
        )

    def _applicable(
        self, payload: dict | None, faq_item_id: str, goodsid: str, sales_channel: str
    ) -> bool:
        """后端返回的任何答案都要复核市场、品类、渠道和审批状态，不信任存储层过滤。"""

        return bool(payload) and (
            payload.get("faq_item_id") == faq_item_id
            and payload.get("market") == Market.CN
            and payload.get("goodsid") == goodsid
            and payload.get("sales_channel") == sales_channel
            and bool(str(payload.get("answer", "")).strip())
            and (not self.require_approved or payload.get("simulated") is not True)
        )

    def probe(self) -> dict[str, str]:
        """readyz 用：真实探测 Milvus/Redis，不以初始化成功代替连通性。"""

        if self.backend != "milvus":
            return {"milvus": "degraded", "redis": "degraded"}
        if self.milvus is None:
            return {"milvus": "down", "redis": "down"}
        state = {}
        try:
            self.redis.ping()
            state["redis"] = "up"
        except Exception:  # noqa: BLE001
            state["redis"] = "down"
        try:
            self.milvus.get_collection_stats(FAQ_COLLECTION)
            state["milvus"] = "up"
        except Exception:  # noqa: BLE001
            state["milvus"] = "down"
        return state
