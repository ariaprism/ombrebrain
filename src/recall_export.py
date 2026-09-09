"""Bubble 使用的只读召回卡导出。

这个模块只把 BucketManager 已读出的桶投影成轻量卡片；它不修改桶、
不刷新活跃时间、不触发向量或模型，也不把完整正文交给同步方。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from tools.plan.core import is_letter_bucket
from utils import parse_bool, strip_wikilinks


SCHEMA_VERSION = 1
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500
_EXCLUDED_TYPES = {"plan", "letter"}


def _safe_importance(value: Any) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError, OverflowError):
        parsed = 5
    return max(1, min(10, parsed))


def _is_plan_bucket(bucket: dict[str, Any]) -> bool:
    """生命周期改写 type 后，仍识别逻辑上的计划桶。"""
    meta = bucket.get("metadata") if isinstance(bucket.get("metadata"), dict) else {}
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",")]
    return (
        str(meta.get("type") or "").strip().casefold() == "plan"
        or str(meta.get("source_tool") or "").strip().casefold() == "plan"
        or (isinstance(tags, (list, tuple, set)) and "__plan__" in tags)
    )


def _clean_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value] if value else []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen or (text.startswith("__") and text.endswith("__")):
            continue
        seen.add(text)
        result.append(text[:80])
    return result[:40]


def _short_text(value: Any, limit: int = 320) -> str:
    text = strip_wikilinks(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    shortened = text[:limit].rstrip()
    return shortened.rstrip("，,；;：:") + "…"


def _card_from_bucket(bucket: dict[str, Any]) -> dict[str, Any] | None:
    meta = bucket.get("metadata") if isinstance(bucket.get("metadata"), dict) else {}
    bucket_id = str(bucket.get("id") or "").strip()
    memory_type = str(meta.get("type") or "dynamic").strip().lower()
    if (
        not bucket_id
        or memory_type in _EXCLUDED_TYPES
        or is_letter_bucket(bucket)
        or _is_plan_bucket(bucket)
    ):
        return None

    title = str(meta.get("title") or meta.get("name") or "").strip()
    if title == bucket_id:
        title = ""
    content = str(bucket.get("content") or "")
    recall_source = meta.get("summary") or meta.get("recall_text") or content
    card = {
        "bucket_id": bucket_id,
        "title": _short_text(title, 160),
        "type": memory_type,
        "domains": _clean_list(meta.get("domain")),
        "tags": _clean_list(meta.get("tags")),
        "importance": _safe_importance(meta.get("importance")),
        "created_at": str(meta.get("created") or "").strip()[:80],
        "last_active_at": str(meta.get("last_active") or "").strip()[:80],
        "recall_text": _short_text(recall_source),
        "full_text": strip_wikilinks(content),
        "triggered_by": str(meta.get("triggered_by") or "").strip()[:160] or None,
        "resolved": parse_bool(meta.get("resolved"), default=False),
        "digested": parse_bool(meta.get("digested"), default=False),
        "pinned": parse_bool(meta.get("pinned"), default=False),
        "protected": parse_bool(meta.get("protected"), default=False),
        "anchor": parse_bool(meta.get("anchor"), default=False),
        "dont_surface": parse_bool(meta.get("dont_surface"), default=False),
    }
    card["surface_allowed"] = not (
        card["resolved"] or card["digested"] or card["dont_surface"]
        or memory_type == "archived"
    )
    fingerprint = {**card, "source_content": content}
    card["content_hash"] = hashlib.sha256(
        json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return card


def _parse_cursor(cursor: str) -> int:
    raw = str(cursor or "0").strip()
    if not raw.isdigit():
        raise ValueError("cursor 必须是非负整数")
    return int(raw)


async def export_recall_cards(
    bucket_manager: Any,
    *,
    cursor: str = "0",
    page_size: int = DEFAULT_PAGE_SIZE,
) -> str:
    """返回一页稳定 JSON；调用期间只执行 ``list_all``。"""
    offset = _parse_cursor(cursor)
    size = max(1, min(MAX_PAGE_SIZE, int(page_size)))
    buckets = await bucket_manager.list_all(include_archive=True)
    cards: list[dict[str, Any]] = []
    deleted_ids: list[str] = []
    for bucket in buckets:
        meta = bucket.get("metadata") if isinstance(bucket.get("metadata"), dict) else {}
        bucket_id = str(bucket.get("id") or "").strip()
        if bucket_id and meta.get("deleted_at"):
            deleted_ids.append(bucket_id)
            continue
        card = _card_from_bucket(bucket)
        if card is not None:
            cards.append(card)

    cards.sort(key=lambda item: item["bucket_id"])
    deleted_ids = sorted(set(deleted_ids))
    snapshot_hash = hashlib.sha256(
        json.dumps(
            [(card["bucket_id"], card["content_hash"]) for card in cards] + [(item, "deleted") for item in deleted_ids],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    page = cards[offset:offset + size]
    next_offset = offset + len(page)
    has_more = next_offset < len(cards)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_hash": snapshot_hash,
        "cursor": str(offset),
        "cards": page,
        "deleted_ids": deleted_ids if offset == 0 else [],
        "next_uri": f"ombre://recall/cards/{next_offset}" if has_more else None,
        "has_more": has_more,
        "total": len(cards),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def export_recall_manifest(bucket_manager: Any) -> str:
    """Return only the current snapshot identity for cheap trusted polling."""
    raw = await export_recall_cards(bucket_manager, cursor="0", page_size=1)
    payload = json.loads(raw)
    return json.dumps({
        "schema_version": payload["schema_version"],
        "snapshot_hash": payload["snapshot_hash"],
        "total": payload["total"],
    }, ensure_ascii=False, separators=(",", ":"))
