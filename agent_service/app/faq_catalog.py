"""FAQ 知识目录：问题身份（intent/group/item）与答案变体（item × goodsid × channel × version）。

seed 脚本与 memory 后端共用本模块，保证 Milvus/Redis 与离线数据同源。
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .constants import CATEGORY_NAMES, SALES_CHANNEL_NAMES, Market, SalesChannel

KEY_PREFIX = "gc:faq"
META_KEY = f"{KEY_PREFIX}:meta"
_PUNCT = re.compile(r"[\s\u3000-\u303f\uff00-\uffef!-/:-@\[-`{-~]+")
_FILLERS = ("请问", "你好", "我想知道", "想问一下")


@dataclass(frozen=True)
class IntentMeta:
    intent_id: str
    route_target: str  # SOP / FAQ
    faq_group_id: str | None = None


@dataclass(frozen=True)
class FaqRef:
    intent_id: str
    faq_group_id: str
    faq_item_id: str


@dataclass
class KnowledgeBundle:
    intents: dict[str, IntentMeta]
    items: dict[str, FaqRef] = field(default_factory=dict)
    standard_questions: dict[str, str] = field(default_factory=dict)
    intent_rows: dict[str, dict] = field(default_factory=dict)
    faq_rows: list[dict] = field(default_factory=list)
    exact: dict[str, FaqRef] = field(default_factory=dict)
    answers: dict[str, dict] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)


def normalize_question(text: str) -> str:
    """精确匹配归一化：小写、去标点空白，并去掉品类/渠道词（它们是适用维度，不是问题身份）。"""

    normalized = text.lower()
    for term in (*CATEGORY_NAMES.values(), *SALES_CHANNEL_NAMES.values(), *_FILLERS):
        normalized = normalized.replace(term, "")
    return _PUNCT.sub("", normalized)


def exact_key(text: str) -> str:
    digest = hashlib.sha256(normalize_question(text).encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}:exact:{digest}"


def version_key(item_id: str, goodsid: str, sales_channel: str) -> str:
    return f"{KEY_PREFIX}:version:{item_id}:{goodsid}:{sales_channel}"


def answer_key(item_id: str, goodsid: str, sales_channel: str, version: str) -> str:
    return f"{KEY_PREFIX}:answer:{item_id}:{goodsid}:{sales_channel}:{version}"


def _row_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _load_policy(path: Path) -> dict:
    content = path.read_text(encoding="utf-8")
    metadata: dict = {}
    body = content
    if content.startswith("---"):
        _, raw_metadata, body = content.split("---", 2)
        metadata = yaml.safe_load(raw_metadata) or {}
    for key in ("market", "sales_channel"):
        metadata[key] = str(metadata.get(key, "")).strip().upper()
    metadata["goodsid"] = str(metadata.get("goodsid", "")).strip()
    clauses: dict[str, str] = {}
    for line in body.splitlines():
        if line.startswith("- "):
            text = line[2:].strip()
            name = re.split(r"[：:]", text, maxsplit=1)[0].strip()
            clauses[name] = text
    return {**metadata, "source": path.name, "body": body.strip(), "clauses": clauses}


def _valid_policy(policy: dict, require_approved: bool) -> bool:
    return (
        0 < len(policy["body"]) <= 4000
        and policy["market"] == Market.CN
        and policy["goodsid"] in CATEGORY_NAMES
        and policy["sales_channel"] in set(SalesChannel)
        and all(
            isinstance(policy.get(key), str) and policy[key].strip()
            for key in ("policy_id", "title", "version", "language")
        )
        and (
            not require_approved
            or (policy.get("approved") is True and policy.get("simulated") is not True)
        )
    )


def load_policies(
    policy_dir: str | Path, *, strict: bool, require_approved: bool
) -> list[dict]:
    directory = Path(policy_dir)
    paths = sorted(directory.glob("*.md")) if directory.exists() else []
    if strict and not paths:
        raise ValueError("严格模式要求非空政策目录")
    policies = [_load_policy(path) for path in paths]
    if not all(_valid_policy(policy, require_approved) for policy in policies):
        raise ValueError(
            "政策必须有中国大陆市场、三级品类 goodsid、销售渠道、版本、来源标识、语言和内容"
        )
    scopes = [(policy["goodsid"], policy["sales_channel"]) for policy in policies]
    if len(scopes) != len(set(scopes)):
        raise ValueError("同一国家、三级品类 goodsid 和销售渠道只能配置一份政策")
    return policies


def build_bundle(
    catalog_path: str | Path,
    policy_dir: str | Path,
    *,
    strict: bool = False,
    require_approved: bool = False,
) -> KnowledgeBundle:
    """把 FAQ 目录与政策 Markdown 合成为 Milvus 行、Redis key/value 的统一视图。"""

    catalog = yaml.safe_load(Path(catalog_path).read_text(encoding="utf-8")) or {}
    policies = load_policies(
        policy_dir, strict=strict, require_approved=require_approved
    )
    bundle = KnowledgeBundle(intents={})
    for raw in catalog.get("intents", []):
        meta = IntentMeta(
            raw["intent_id"], raw["route_target"], raw.get("faq_group_id")
        )
        bundle.intents[meta.intent_id] = meta
        for text in raw.get("corpus", []):
            bundle.intent_rows[_row_id("intent", meta.intent_id, text)] = {
                "intent_id": meta.intent_id,
                "text": text,
            }
    for raw in catalog.get("faq_items", []):
        ref = FaqRef(raw["intent_id"], raw["faq_group_id"], raw["faq_item_id"])
        bundle.items[ref.faq_item_id] = ref
        bundle.standard_questions[ref.faq_item_id] = raw["standard_question"]
        scopes = [
            policy for policy in policies if raw["answer_clause"] in policy["clauses"]
        ]
        goodsids = sorted({policy["goodsid"] for policy in scopes})
        channels = sorted({policy["sales_channel"] for policy in scopes})
        questions = [raw["standard_question"], *raw.get("similar_questions", [])]
        for text in questions:
            bundle.exact[exact_key(text)] = ref
            # 相似问同时作为意图语料：L2 第 1 步按 intent_id 聚合
            bundle.intent_rows[_row_id("intent", ref.intent_id, text)] = {
                "intent_id": ref.intent_id,
                "text": text,
            }
            if goodsids:
                bundle.faq_rows.append(
                    {
                        "id": _row_id("faq", ref.faq_item_id, text),
                        "faq_item_id": ref.faq_item_id,
                        "faq_group_id": ref.faq_group_id,
                        "intent_id": ref.intent_id,
                        "goodsids": goodsids,
                        "sales_channels": channels,
                        "text": text,
                    }
                )
        for policy in scopes:
            version = str(policy["version"])
            bundle.versions[
                version_key(ref.faq_item_id, policy["goodsid"], policy["sales_channel"])
            ] = version
            bundle.answers[
                answer_key(
                    ref.faq_item_id, policy["goodsid"], policy["sales_channel"], version
                )
            ] = {
                "faq_item_id": ref.faq_item_id,
                "policy_id": policy["policy_id"],
                "title": policy["title"],
                "version": version,
                "market": policy["market"],
                "language": policy["language"],
                "goodsid": policy["goodsid"],
                "sales_channel": policy["sales_channel"],
                "answer": policy["clauses"][raw["answer_clause"]],
                "simulated": policy.get("simulated") is True,
            }
    if strict and not bundle.answers:
        raise ValueError("严格模式要求至少一条可用 FAQ 答案")
    return bundle


def redis_payload(bundle: KnowledgeBundle) -> dict[str, str]:
    """Redis 写入视图：exact -> 三元组，version 指针，answer 变体。"""

    payload = {
        key: json.dumps(ref.__dict__, ensure_ascii=False)
        for key, ref in bundle.exact.items()
    }
    payload.update(bundle.versions)
    payload.update(
        {
            key: json.dumps(value, ensure_ascii=False)
            for key, value in bundle.answers.items()
        }
    )
    return payload
