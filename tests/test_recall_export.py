import hashlib
import json

import pytest

from recall_export import export_recall_cards, export_recall_manifest


class FakeBucketManager:
    def __init__(self, buckets):
        self.buckets = buckets
        self.calls = []

    async def list_all(self, include_archive=False):
        self.calls.append(include_archive)
        return self.buckets


@pytest.mark.asyncio
async def test_manifest_matches_snapshot_without_returning_cards():
    manager = FakeBucketManager([bucket("one"), bucket("two")])
    manifest = json.loads(await export_recall_manifest(manager))
    snapshot = json.loads(await export_recall_cards(manager, cursor="0", page_size=1))
    assert manifest == {"schema_version": 1, "snapshot_hash": snapshot["snapshot_hash"], "total": 2}
    assert "cards" not in manifest


def bucket(bucket_id, content="正文", **metadata):
    base = {
        "name": bucket_id,
        "title": "",
        "type": "dynamic",
        "domain": ["daily"],
        "tags": ["标签"],
        "importance": 5,
        "created": "2026-09-01T10:00:00+08:00",
        "last_active": "2026-09-02T10:00:00+08:00",
    }
    base.update(metadata)
    return {"id": bucket_id, "content": content, "metadata": base}


@pytest.mark.asyncio
async def test_export_is_read_only_compact_and_excludes_plan_letter():
    manager = FakeBucketManager([
        bucket("event-b", "第一段 [[旧链接]]\n第二段", title="一次见面", anchor=True),
        bucket("feel-a", "当时留下的感受", type="feel", triggered_by="event-b"),
        bucket("plan-a", "不应进入召回卡", type="plan"),
        bucket("letter-a", "也不应进入召回卡", type="letter"),
        bucket("archived-plan", "归档后仍是计划", type="archived", source_tool="plan"),
        bucket("archived-letter", "归档后仍是信件", type="archived", tags=["__letter__"]),
    ])

    payload = json.loads(await export_recall_cards(manager, cursor="0"))

    assert manager.calls == [True]
    assert payload["schema_version"] == 1
    assert payload["total"] == 2
    assert [card["bucket_id"] for card in payload["cards"]] == ["event-b", "feel-a"]
    event = payload["cards"][0]
    assert event["recall_text"] == "第一段 旧链接 第二段"
    assert event["full_text"] == "第一段 旧链接\n第二段"
    assert event["anchor"] is True
    assert "第一段" not in json.dumps(event["content_hash"])
    assert len(event["content_hash"]) == 64
    assert "content" not in event
    assert payload["has_more"] is False


@pytest.mark.asyncio
async def test_export_paginates_deterministically_and_reports_deletions_once():
    manager = FakeBucketManager([
        bucket("c"), bucket("a"), bucket("b"),
        bucket("gone", deleted_at="2026-09-03T00:00:00+08:00"),
    ])

    first = json.loads(await export_recall_cards(manager, cursor="0", page_size=2))
    second = json.loads(await export_recall_cards(manager, cursor="2", page_size=2))

    assert [card["bucket_id"] for card in first["cards"]] == ["a", "b"]
    assert first["deleted_ids"] == ["gone"]
    assert first["next_uri"] == "ombre://recall/cards/2"
    assert [card["bucket_id"] for card in second["cards"]] == ["c"]
    assert second["deleted_ids"] == []
    assert second["snapshot_hash"] == first["snapshot_hash"]


@pytest.mark.asyncio
async def test_surface_state_and_hash_follow_source_changes():
    hidden = bucket("hidden", "短卡", resolved=True, digested=True, dont_surface=True)
    manager = FakeBucketManager([hidden])
    before = json.loads(await export_recall_cards(manager))["cards"][0]
    hidden["content"] = "短卡之外的完整正文发生变化"
    after = json.loads(await export_recall_cards(manager))["cards"][0]

    assert before["surface_allowed"] is False
    assert before["content_hash"] != after["content_hash"]


@pytest.mark.asyncio
async def test_invalid_cursor_is_rejected():
    with pytest.raises(ValueError, match="cursor"):
        await export_recall_cards(FakeBucketManager([]), cursor="next")


@pytest.mark.asyncio
async def test_malformed_importance_falls_back_without_breaking_page():
    manager = FakeBucketManager([bucket("hand-edited", importance="很重要")])

    payload = json.loads(await export_recall_cards(manager))

    assert payload["cards"][0]["importance"] == 5


@pytest.mark.asyncio
async def test_recall_export_is_resource_not_model_callable_tool():
    import server

    templates = server.mcp._resource_manager.list_templates()
    recall_templates = [
        template for template in templates
        if str(template.uri_template) == "ombre://recall/cards/{cursor}"
    ]
    tool_names = {tool.name for tool in await server.mcp.list_tools()}

    assert len(recall_templates) == 1
    assert recall_templates[0].mime_type == "application/json"
    assert not any("recall" in name for name in tool_names)
