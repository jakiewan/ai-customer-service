import hashlib
import re
from pathlib import Path
from typing import Iterable

import yaml
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore

from .constants import Language, Market
from .schemas import PolicyEvidence


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

    def __init__(self, policy_dir: str | Path, backend: str = "memory", database_url: str | None = None) -> None:
        self.policy_dir = Path(policy_dir)
        self.backend = backend
        self.database_url = database_url
        self.documents = self._load_documents()
        self.embeddings = HashEmbeddings()
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
                embedding_length=self.embeddings.dimensions,
                collection_name="globalcare_policy",
                pre_delete_collection=False,
            )
        except Exception:
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
        normalized_market = str(market).upper()
        candidate_docs: list[Document]
        if self.pg_vector_store is not None:
            try:
                candidate_docs = self.pg_vector_store.similarity_search(
                    query, k=max(top_k * 3, top_k), filter={"market": normalized_market}
                )
            except Exception:
                candidate_docs = self._memory_search(query, normalized_market, top_k)
        else:
            candidate_docs = self._memory_search(query, normalized_market, top_k)
        return [self._to_evidence(doc, query) for doc in candidate_docs[:top_k]]

    def _memory_search(self, query: str, market: str, top_k: int) -> list[Document]:
        results = self.vector_store.similarity_search(query, k=max(len(self.documents), top_k * 4))
        filtered = [doc for doc in results if str(doc.metadata.get("market", "")).upper() == market]
        if filtered:
            return filtered[:top_k]
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

