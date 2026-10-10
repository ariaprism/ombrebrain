from types import SimpleNamespace

import pytest

from deletion_requests import DeletionRequestStore
from web import buckets, import_api, letters


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            for method in methods:
                self.routes[(method, path)] = fn
            return fn
        return decorator


class Request:
    def __init__(self, body, *, path_params=None, confirm=False):
        self.body_value = body
        self.path_params = path_params or {}
        self.query_params = {"confirm": "true"} if confirm else {}


class Manager:
    def __init__(self, records):
        self.records = records
        self.deleted = []
        self.archived = []
        self.embedding_outbox = SimpleNamespace(discard=lambda bucket_id: None)

    async def get(self, bucket_id):
        return self.records.get(bucket_id)

    async def delete(self, bucket_id):
        self.deleted.append(bucket_id)
        self.records.pop(bucket_id, None)
        return True

    async def archive(self, bucket_id):
        self.archived.append(bucket_id)
        self.records.pop(bucket_id, None)
        return True

    def _invalidate_bm25(self):
        pass


async def _exercise_routes(monkeypatch, tmp_path, records):
    manager = Manager(records)
    store = DeletionRequestStore(str(tmp_path), manager)

    async def read_json(request):
        return request.body_value

    for module in (buckets, letters, import_api):
        monkeypatch.setattr(module.sh, "_require_auth", lambda request: None)
        monkeypatch.setattr(module.sh, "_read_json_object", read_json)
        monkeypatch.setattr(module.sh, "bucket_mgr", manager)
        monkeypatch.setattr(module.sh, "deletion_requests", store)

    bucket_mcp, letter_mcp, import_mcp = FakeMCP(), FakeMCP(), FakeMCP()
    buckets.register(bucket_mcp)
    letters.register(letter_mcp)
    import_api.register(import_mcp)
    return manager, store, bucket_mcp, letter_mcp, import_mcp


@pytest.mark.asyncio
async def test_all_human_delete_paths_execute_without_reason_or_ai_approval(monkeypatch, tmp_path):
    records = {
        "delete": {"id": "delete", "metadata": {}},
        "archive": {"id": "archive", "metadata": {}},
        "batch": {"id": "batch", "metadata": {}},
        "letter": {"id": "letter", "metadata": {"type": "letter"}},
        "review": {"id": "review", "metadata": {}},
    }
    manager, store, bmcp, lmcp, imcp = await _exercise_routes(
        monkeypatch, tmp_path, records
    )
    reason = {}

    await bmcp.routes[("DELETE", "/api/bucket/{bucket_id}")](
        Request(reason, path_params={"bucket_id": "delete"}, confirm=True)
    )
    await bmcp.routes[("POST", "/api/bucket/{bucket_id}/archive")](
        Request(reason, path_params={"bucket_id": "archive"})
    )
    await bmcp.routes[("POST", "/api/buckets/batch")](
        Request({"ids": ["batch"], "action": "archive", **reason})
    )
    await lmcp.routes[("DELETE", "/api/letter/{letter_id}")](
        Request(reason, path_params={"letter_id": "letter"}, confirm=True)
    )
    await imcp.routes[("POST", "/api/import/review")](
        Request({"decisions": [{"bucket_id": "review", "action": "delete", **reason}]})
    )

    assert manager.deleted == ["delete", "archive", "batch", "letter", "review"]
    assert manager.archived == []
    assert store.pending_with_buckets() == []


@pytest.mark.asyncio
async def test_web_delete_and_archive_keep_test_buckets_direct(monkeypatch, tmp_path):
    provenance = {"provenance": {"kind": "test", "erasable": True}}
    records = {
        "delete-test": {"id": "delete-test", "metadata": provenance},
        "archive-test": {"id": "archive-test", "metadata": provenance},
        "batch-test": {"id": "batch-test", "metadata": provenance},
    }
    manager, store, bmcp, _, _ = await _exercise_routes(monkeypatch, tmp_path, records)

    await bmcp.routes[("DELETE", "/api/bucket/{bucket_id}")](
        Request({}, path_params={"bucket_id": "delete-test"}, confirm=True)
    )
    await bmcp.routes[("POST", "/api/bucket/{bucket_id}/archive")](
        Request({}, path_params={"bucket_id": "archive-test"})
    )
    await bmcp.routes[("POST", "/api/buckets/batch")](
        Request({"ids": ["batch-test"], "action": "archive"})
    )

    assert manager.deleted == ["delete-test", "archive-test", "batch-test"]
    assert manager.archived == []
    assert store._load()["requests"] == []


@pytest.mark.asyncio
async def test_confirmation_is_still_required_for_delete(monkeypatch, tmp_path):
    manager, _, bmcp, lmcp, _ = await _exercise_routes(
        monkeypatch, tmp_path,
        {"memory": {"id": "memory", "metadata": {}},
         "letter": {"id": "letter", "metadata": {"type": "letter"}}},
    )
    for mcp, path, param, value in [
        (bmcp, "/api/bucket/{bucket_id}", "bucket_id", "memory"),
        (lmcp, "/api/letter/{letter_id}", "letter_id", "letter"),
    ]:
        response = await mcp.routes[("DELETE", path)](
            Request({}, path_params={param: value})
        )
        assert response.status_code == 400
    assert manager.deleted == []


@pytest.mark.asyncio
async def test_confirmed_archive_keeps_markdown_and_removes_vector(bucket_mgr, test_config, fake_embedding_engine):
    from pathlib import Path
    import frontmatter

    bid = await bucket_mgr.create(content="A memory to retain in the archive", name="Memory")
    store = DeletionRequestStore(test_config["buckets_dir"], bucket_mgr, fake_embedding_engine)
    await store.submit(bid, "legacy request")
    result = await store.execute_confirmed(bid)

    assert result["ok"]
    files = list(Path(test_config["buckets_dir"]).joinpath("archive").glob("*.md"))
    assert len(files) == 1
    post = frontmatter.load(files[0])
    assert post.content == "A memory to retain in the archive"
    assert post["deleted_at"]
    assert bid not in fake_embedding_engine._store
    assert store.pending_with_buckets() == []
    assert store.status(bid)["status"] == "superseded"


@pytest.mark.asyncio
async def test_direct_batch_reports_failures_and_deduplicates(monkeypatch, tmp_path):
    manager, store, _, _, _ = await _exercise_routes(
        monkeypatch, tmp_path,
        {"ok": {"id": "ok", "metadata": {}}, "fail": {"id": "fail", "metadata": {}}},
    )
    original = manager.delete

    async def delete(bid):
        return False if bid == "fail" else await original(bid)

    manager.delete = delete
    result = await store.execute_confirmed_batch(["ok", "ok", "fail", "missing"])
    assert result["ok"] is False
    assert result["updated"] == ["ok"]
    assert result["missing"] == ["missing"]
    assert result["errors"] == [{"id": "fail", "error": "bucket deletion failed"}]
    assert store.pending_with_buckets() == []
