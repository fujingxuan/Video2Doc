from __future__ import annotations

import copy
import hashlib
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError, NotFoundError
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Task, MarkdownExport
from app.models.entities import utcnow
from app.repositories.tasks import TaskRepository
from app.services import events
from app.services.cache import TaskCacheService
from app.services.events import page_delta
from app.services.review import TaskReviewService
from app.services.tasks import TaskService
from app.storage.workspace import write_json
from app.workers import queue
from tests.test_documents import sample_document
from tests.test_task_management import _create_task


def make_task(tmp_path: Path, title: str = "history-feature", status: str = "COMPLETED"):
    return _create_task(
        data_root=tmp_path / "tasks", output_root=tmp_path / "exports", status=status, title=title
    )


def test_page_stable_order_literal_search_and_bounded_queries(clean_database):
    with SessionLocal() as session:
        tasks = [
            Task(
                title=f"page-fixture-{i:03d}",
                source="https://invalid/100%_literal",
                source_type="URL",
                status="COMPLETED" if i % 2 else "FAILED",
                created_at=utcnow().replace(microsecond=0),
            )
            for i in range(251)
        ]
        session.add_all(tasks)
        session.commit()
        ids = sorted([task.id for task in tasks], reverse=True)
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with SessionLocal() as session:
            page = TaskService(session).page(search="page-fixture-", limit=100)
            assert page.total == 251 and [task.id for task in page.items] == ids[:100]
            assert len(statements) == 2  # Count + bounded rows; no task relationships.
            last = TaskService(session).page(search="page-fixture-", offset=900, limit=100)
            assert last.offset == 200 and [task.id for task in last.items] == ids[200:]
            filtered = TaskService(session).page(search="page-fixture-", status="completed")
            assert filtered.total == 125 and all(
                task.status == "COMPLETED" for task in filtered.items
            )
            assert TaskService(session).page(search="100%_literal").total == 251
            assert TaskService(session).page(search="100X_literal").total == 0
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert all("task_stage_runs" not in statement for statement in statements)


@pytest.mark.asyncio
async def test_page_api_limits_and_trash_visibility(clean_database, tmp_path):
    task_id, workspace = make_task(tmp_path, "api-trash-roundtrip")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for query in ("limit=0", "limit=201", "offset=-1", "search=" + "x" * 201):
            assert (await client.get("/api/v1/tasks/page?" + query)).status_code == 422
        assert (await client.delete(f"/api/v1/tasks/{task_id}/permanent")).status_code == 409
        response = await client.delete(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200 and response.json()["deleted_at"]
        assert workspace.is_dir()
        assert (await client.get(f"/api/v1/tasks/{task_id}")).status_code == 404
        active = (await client.get("/api/v1/tasks/page?search=api-trash-roundtrip")).json()
        trash = (
            await client.get("/api/v1/tasks/page?search=api-trash-roundtrip&trashed=true")
        ).json()
        assert active["total"] == 0 and trash["total"] == 1
        assert (await client.post(f"/api/v1/tasks/{task_id}/restore")).status_code == 200
        assert (await client.get(f"/api/v1/tasks/{task_id}")).status_code == 200


def test_trash_restore_preserve_files_snapshot_and_cache(clean_database, tmp_path):
    task_id, workspace = make_task(tmp_path)
    cache_file = workspace / "02_asr/retained.txt"
    cache_file.write_text("retain original output", encoding="utf-8")
    before = {
        path.relative_to(workspace): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    with SessionLocal() as session:
        service = TaskService(session)
        snapshot = service.snapshot(task_id)
        trashed = service.delete(task_id)
        assert trashed.trash_expires_at - trashed.deleted_at == timedelta(days=30)
        assert service.delete(task_id).trash_expires_at == trashed.trash_expires_at
        assert TaskRepository(session).get(task_id) is None
        assert TaskCacheService(session).clear([task_id])["tasks_cleared"] == 0
        restored = service.restore(task_id)
        assert restored.status == "COMPLETED" and restored.deleted_at is None
        assert service.snapshot(task_id) == snapshot
    after = {
        path.relative_to(workspace): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_worker_skips_trash_and_restore_resumes_queue(tmp_path, monkeypatch):
    isolated = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    Base.metadata.create_all(isolated)
    sessions = sessionmaker(isolated, expire_on_commit=False)
    monkeypatch.setattr(queue, "SessionLocal", sessions)
    with sessions() as session:
        task = Task(title="queued", source="source", source_type="URL", status="QUEUED")
        session.add(task)
        session.commit()
        task_id = task.id
        TaskService(session).delete(task_id)
        assert TaskService(session).queue_status().queued == 0
    worker = queue.SerialQueueWorker()
    assert worker._claim_next_task() is None
    with sessions() as session:
        TaskService(session).restore(task_id)
    assert worker._claim_next_task() == task_id
    with sessions() as session:
        with pytest.raises(ConflictError):
            TaskService(session).delete(task_id)
        session.execute(update(Task).where(Task.id == task_id).values(status="QUEUED"))
        session.commit()

    # Inject a completed trash update immediately before the worker's claim.
    def race(conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("UPDATE tasks SET status="):
            conn.exec_driver_sql(
                "UPDATE tasks SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,)
            )

    event.listen(isolated, "before_cursor_execute", race)
    try:
        assert worker._claim_next_task() is None
    finally:
        event.remove(isolated, "before_cursor_execute", race)
        isolated.dispose()


def test_expiry_is_manual_and_rechecks_current_expiry(clean_database, tmp_path, monkeypatch):
    expired_id, expired_workspace = make_task(tmp_path, "expired")
    fresh_id, fresh_workspace = make_task(tmp_path, "fresh")
    race_id, race_workspace = make_task(tmp_path, "renewed")
    with SessionLocal() as session:
        service = TaskService(session)
        for task_id in (expired_id, fresh_id, race_id):
            service.delete(task_id)
        session.execute(
            update(Task)
            .where(Task.id.in_([expired_id, race_id]))
            .values(trash_expires_at=utcnow() - timedelta(days=1))
        )
        session.commit()
        original_purge = service.purge

        def purge_with_restore(task_id, **kwargs):
            if task_id == race_id:
                service.restore(task_id)
                service.delete(task_id)
            return original_purge(task_id, **kwargs)

        monkeypatch.setattr(service, "purge", purge_with_restore)
        assert expired_workspace.exists()  # Expiry alone has no destructive side effect.
        result = service.purge_expired()
        assert expired_id in [item["task_id"] for item in result["deleted"]]
        assert race_id in [item["task_id"] for item in result["skipped"]]
        assert session.get(Task, fresh_id).deleted_at is not None
    assert not expired_workspace.exists()
    assert fresh_workspace.exists() and race_workspace.exists()


@pytest.mark.parametrize("nested_input", [False, True])
def test_purge_protects_shared_outputs_and_nested_inputs(clean_database, tmp_path, nested_input):
    task_id, workspace = make_task(tmp_path, "protected-output")
    retained = workspace / "05_output/retained.md"
    retained.write_text("shared or original", encoding="utf-8")
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if nested_input:
            task.source, task.source_type = str(retained), "LOCAL_FILE"
        else:
            session.add(
                Task(
                    title="other-ref",
                    source=str(retained),
                    source_type="LOCAL_FILE",
                    status="FAILED",
                )
            )
        session.commit()
        service = TaskService(session)
        service.delete(task_id)
        with pytest.raises(ConflictError):
            service.purge(task_id)
    assert retained.read_text(encoding="utf-8") == "shared or original"
    assert (workspace / "task_config.json").exists()


def test_purge_link_preflight_does_not_delete_other_files(clean_database, tmp_path, monkeypatch):
    from app.services import cache

    task_id, workspace = make_task(tmp_path, "unsafe-link")
    link = workspace / "05_output/link"
    link.mkdir()
    original = cache._is_link
    monkeypatch.setattr(cache, "_is_link", lambda path: path == link or original(path))
    with SessionLocal() as session:
        service = TaskService(session)
        service.delete(task_id)
        with pytest.raises(ConflictError):
            service.purge(task_id)
    assert (workspace / "task_config.json").exists()


def test_purge_protects_word_referenced_by_another_task(clean_database, tmp_path):
    task_id, workspace = make_task(tmp_path, "shared-word")
    retained = workspace / "05_output/shared.docx"
    retained.write_bytes(b"shared Word")
    with SessionLocal() as session:
        other = Task(title="other-word-export", source="old-source", source_type="URL",
                     status="COMPLETED")
        session.add(other)
        session.flush()
        other.markdown_exports.append(MarkdownExport(
            attempt=1, target_type="OBSIDIAN", status="COMPLETED",
            config_json={"word_path": str(retained)},
        ))
        session.commit()
        service = TaskService(session)
        service.delete(task_id)
        with pytest.raises(ConflictError):
            service.purge(task_id)
    assert retained.read_bytes() == b"shared Word"


def test_restart_marks_interrupted_purge_without_deleting_files(clean_database, tmp_path):
    task_id, workspace = make_task(tmp_path, "interrupted-purge")
    with SessionLocal() as session:
        TaskService(session).delete(task_id)
        session.execute(update(Task).where(Task.id == task_id).values(status="DELETING"))
        session.commit()
        with pytest.raises(ConflictError):
            TaskService(session).restore(task_id)
    queue.SerialQueueWorker().recover_interrupted()
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task.status == "FAILED" and task.error_code == "delete_interrupted"
        assert task.deleted_at is not None
    assert workspace.is_dir()


def test_failed_file_removal_keeps_a_visible_retryable_tombstone(
    clean_database, tmp_path, monkeypatch
):
    from app.services import tasks as task_services

    task_id, workspace = make_task(tmp_path, "partial-purge")
    partial = workspace / "05_output/partial.md"
    partial.write_text("fixture", encoding="utf-8")

    def fail_after_one_file(_task, **_kwargs):
        partial.unlink()
        raise PermissionError("fixture: second file is locked")

    monkeypatch.setattr(task_services, "_delete_task_files", fail_after_one_file)
    with SessionLocal() as session:
        service = TaskService(session)
        service.delete(task_id)
        with pytest.raises(ConflictError):
            service.purge(task_id)
        task = session.get(Task, task_id)
        assert task.deleted_at is not None and task.status == "FAILED"
        assert task.error_code == "delete_failed"
        restored = service.restore(task_id)
        assert restored.status == "FAILED" and restored.error_code == "delete_failed"
    assert not partial.exists() and (workspace / "task_config.json").exists()


def feed(items, total=None):
    return {
        "page": {
            "items": items,
            "total": len(items) if total is None else total,
            "offset": 0,
            "limit": 100,
        },
        "queue": {"counts": {"COMPLETED": 3}},
    }


def test_delta_only_changed_rows_with_order_and_membership():
    previous = feed([{"id": 3, "progress": 0}, {"id": 2, "progress": 10}, {"id": 1}])
    assert page_delta(previous, copy.deepcopy(previous)) is None
    current = feed([{"id": 4}, {"id": 3, "progress": 0}, {"id": 2, "progress": 90}])
    delta = page_delta(previous, current)
    assert delta["upserts"] == [{"id": 4}, {"id": 2, "progress": 90}]
    assert delta["removed_ids"] == [1] and delta["order"] == [4, 3, 2]
    reordered = copy.deepcopy(previous)
    reordered["page"]["items"].reverse()
    assert page_delta(previous, reordered)["upserts"] == []


@pytest.mark.asyncio
async def test_sse_snapshot_delta_keepalive_and_reconnect(monkeypatch):
    initial, changed = feed([{"id": 1, "progress": 0}]), feed([{"id": 1, "progress": 42}])
    snapshots = iter([initial, initial, changed, changed])
    monkeypatch.setattr(events, "_snapshot", lambda query: next(snapshots))

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(events.asyncio, "sleep", no_sleep)
    stream = events.paged_event_stream(limit=100)
    assert (await anext(stream)).startswith("event: page\n")
    assert await anext(stream) == ": keep-alive\n\n"
    assert (await anext(stream)).startswith("event: delta\n")
    await stream.aclose()
    reconnected = events.paged_event_stream(limit=100)
    assert (await anext(reconnected)).startswith("event: page\n")
    await reconnected.aclose()


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.asyncio
async def test_review_complete_text_saved_decision_and_download(clean_database, tmp_path, fallback):
    task_id, workspace = make_task(tmp_path)
    document = sample_document(tmp_path)
    block = document.all_blocks()[0]
    block.original_text = "<script>alert(1)</script> 原文 42\n" + "一整段不截断。\n" * 1500
    block.edited_text = None if fallback else block.original_text + "\n段落排版。"
    document.edit_metadata = {
        "accepted": not fallback,
        "fallback_used": fallback,
        "reason": "保留原文" if fallback else None,
        "model": "saved-model",
    }
    path = workspace / "05_output/document.json"
    write_json(path, document.model_dump(mode="json"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with SessionLocal() as session:
        session.execute(update(Task).where(Task.id == task_id).values(document_path=str(path)))
        session.commit()
        result = TaskReviewService(session).get(task_id)
        assert block.original_text in result["original_text"]
        assert result["original_characters"] == len(result["original_text"])
        assert result["fallback_used"] == fallback and result["changed"] != fallback
        assert result["source_count"] == len(set(document.source_ids()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/tasks/{task_id}/review/diff")
        assert response.status_code == 200 and response.text == result["diff"]
        assert "attachment;" in response.headers["content-disposition"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "kind", ["missing", "invalid", "encoding", "old_attempt", "outside", "running"]
)
def test_review_rejects_missing_invalid_or_stale_artifacts(clean_database, tmp_path, kind):
    task_id, workspace = make_task(tmp_path)
    path = workspace / "05_output/document.json"
    path.write_bytes(b"\xff" if kind == "encoding" else b"{}")
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        task.document_path = str(path)
        if kind == "missing":
            path.unlink()
        if kind == "old_attempt":
            task.document_path = None
        if kind == "outside":
            task.document_path = str(tmp_path / "elsewhere.json")
        if kind == "running":
            task.status = "QUEUED"
        session.commit()
        with pytest.raises((ConflictError, NotFoundError)):
            TaskReviewService(session).get(task_id)
        task.status = "FAILED"
        session.commit()
