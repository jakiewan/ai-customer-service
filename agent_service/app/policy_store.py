import hashlib
import logging
import re
from pathlib import Path

import yaml
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore

from .constants import Language, Market
from .schemas import PolicyEvidence

logger = logging.getLogger(__name__)


def _tokens(text: str) -> list[str]:
    """对中英文做轻量 token 化，避免 MVP 强依赖外部分词服务。"""

    lowered = text.lower()
    words = re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]", lowered)
    chinese = ["".join(words[index : index + 2]) for index in range(len(words) - 1)]
    return words + chinese


class HashEmbeddings(Embeddings):
    """可离线运行的 LangChain Embeddings 实现，生产可替换为 HuggingFace。"""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _tokens(text)
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = sum(value * value for value in vector) ** 0.5
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def build_embeddings(mode: str, model_name: str, dimensions: int) -> Embeddings:
    """生产可选本地多语言 embedding，离线/CI 默认使用确定性 Hash embedding。"""

    if mode.lower() == "huggingface":
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            return HuggingFaceEmbeddings(
                model_name=model_name,
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True},
            )
        except Exception as exc:  # noqa: BLE001 - 本地模型加载失败时必须保留离线 fallback。
            logger.error("HuggingFace Embedding 初始化失败，回退 Hash Embedding: %s", exc)
    return HashEmbeddings(dimensions=dimensions)


SUPPORTED_POLICY_TOPICS = {
    "warranty": ("保修", "保多久", "保修期", "warranty", "coverage", "eligible"),
    "power": ("无法开机", "不能开机", "开不了机", "黑屏", "不充电", "power on", "not charging"),
    "repair": ("维修", "售后", "报修", "送修", "repair", "after-sales", "service center"),
    "status": ("进度", "状态", "修到哪", "repair status", "repair progress"),
}
UNSUPPORTED_POLICY_TERMS = ("退款", "退货", "换机", "赔偿", "赔付", "支付", "refund", "replacement", "compensation")
UNSUPPORTED_PRODUCT_TERMS = ("智能手表", "手表", "耳机", "笔记本", "路由器", "平板", "laptop", "watch", "earbuds", "router")


def _load_frontmatter(path: Path) -> Document:
    content = path.read_text(encoding="utf-8")
    metadata: dict[str, str] = {}
    body = content
    if content.startswith("---"):
        _, raw_metadata, body = content.split("---", 2)
        metadata = yaml.safe_load(raw_metadata) or {}
    metadata["source"] = path.name
    metadata["market"] = str(metadata.get("market", "")).upper()
    return Document(page_content=body.strip(), metadata=metadata)


class PolicyStore:
    """政策检索端口。默认内存向量库，配置 pgvector 时使用 PostgreSQL。"""

    @staticmethod
    def is_unsupported(query: str) -> bool:
        return any(term.lower() in query.lower() for term in (*UNSUPPORTED_POLICY_TERMS, *UNSUPPORTED_PRODUCT_TERMS))

    def __init__(
        self,
        policy_dir: str | Path,
        backend: str = "memory",
        database_url: str | None = None,
        embedding_mode: str = "hash",
        embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        embedding_dimensions: int = 256,
    ) -> None:
        self.policy_dir = Path(policy_dir)
        self.backend = backend
        self.database_url = database_url
        self.documents = self._load_documents()
        self.embeddings = build_embeddings(embedding_mode, embedding_model, embedding_dimensions)
        self.embedding_dimensions = embedding_dimensions
        self.vector_store = InMemoryVectorStore.from_documents(self.documents, self.embeddings)
        self.pg_vector_store = None
        if backend == "pgvector" and database_url:
            self._try_create_pgvector(database_url)

    def _load_documents(self) -> list[Document]:
        if not self.policy_dir.exists():
            return []
        return [_load_frontmatter(path) for path in sorted(self.policy_dir.glob("*.md"))]

    def _try_create_pgvector(self, database_url: str) -> None:
        """创建 PGVector 适配器；数据库不可用时保留内存 fallback。"""

        try:
            from langchain_postgres import PGVector

            self.pg_vector_store = PGVector(
                embeddings=self.embeddings,
                connection=database_url,
                embedding_length=self.embedding_dimensions,
                collection_name="globalcare_policy",
                pre_delete_collection=False,
            )
        except Exception:  # noqa: BLE001 - pgvector 不可用不能阻断离线 FAQ。
            # readyz 会报告 pgvector 不可用，不能让 FAQ 离线模式整体不可启动。
            self.pg_vector_store = None

    def seed_pgvector(self) -> int:
        if self.pg_vector_store is None:
            return 0
        ids = [str(document.metadata.get("policy_id")) for document in self.documents]
        self.pg_vector_store.add_documents(self.documents, ids=ids)
        return len(self.documents)

    def retrieve(self, query: str, market: Market | str | None, top_k: int = 3) -> list[PolicyEvidence]:
        if not market:
            return []
        lowered_query = query.lower()
        if any(term.lower() in lowered_query for term in (*UNSUPPORTED_POLICY_TERMS, *UNSUPPORTED_PRODUCT_TERMS)):
            return []
        if not any(
            term.lower() in lowered_query
            for terms in SUPPORTED_POLICY_TOPICS.values()
            for term in terms
        ):
            return []
        normalized_market = str(market).upper()
        candidate_docs: list[Document]
        if self.pg_vector_store is not None:
            try:
                candidate_docs = self.pg_vector_store.similarity_search(
                    query, k=max(top_k * 3, top_k), filter={"market": normalized_market}
                )
            except Exception:  # noqa: BLE001 - 向量检索失败时使用确定性 fallback。
                candidate_docs = self._memory_search(query, normalized_market, top_k)
        else:
            candidate_docs = self._memory_search(query, normalized_market, top_k)
        return [self._to_evidence(doc, query) for doc in candidate_docs[:top_k]]

    def _memory_search(self, query: str, market: str, top_k: int) -> list[Document]:
        results = self.vector_store.similarity_search(query, k=max(len(self.documents), top_k * 4))
        filtered = [doc for doc in results if str(doc.metadata.get("market", "")).upper() == market]
        query_lower = query.lower()
        topic_terms = [
            term.lower()
            for terms in SUPPORTED_POLICY_TOPICS.values()
            if any(term.lower() in query_lower for term in terms)
            for term in terms
        ]
        topic_filtered = [
            document
            for document in filtered
            if any(term in document.page_content.lower() for term in topic_terms)
        ]
        if topic_filtered:
            return topic_filtered[:top_k]
        # 向量库为空或模型不擅长中文时，以确定性 token overlap 兜底。
        scored: list[tuple[int, Document]] = []
        query_tokens = set(_tokens(query))
        for document in self.documents:
            if str(document.metadata.get("market", "")).upper() != market:
                continue
            overlap = len(query_tokens.intersection(_tokens(document.page_content)))
            scored.append((overlap, document))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [document for _, document in scored[:top_k]]

    @staticmethod
    def _to_evidence(document: Document, query: str) -> PolicyEvidence:
        query_tokens = set(_tokens(query))
        content_tokens = set(_tokens(document.page_content))
        overlap = len(query_tokens.intersection(content_tokens))
        score = min(0.99, 0.55 + overlap / max(len(query_tokens), 1) * 0.4)
        metadata = document.metadata
        excerpt = " ".join(document.page_content.split())[:360]
        return PolicyEvidence(
            policy_id=str(metadata.get("policy_id", "unknown-policy")),
            title=str(metadata.get("title", "售后政策")),
            version=str(metadata.get("version", "unknown")),
            market=Market(str(metadata.get("market", "CN"))),
            language=str(metadata.get("language", Language.ZH_CN.value)),
            excerpt=excerpt,
            score=round(score, 3),
        )
