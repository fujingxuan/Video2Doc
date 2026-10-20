from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.core.errors import PipelineCancelled, PipelineError
from app.db.session import SessionLocal
from app.documents.exporter import ExportConfig, export_document_bundle
from app.documents.faithfulness import validate_block_edit
from app.models import Task, TaskStageRun
from app.providers.asr import (
    AsrRequest,
    AudioChunk,
    OpenAICompatibleAsrProvider,
    UnifiedAsrGatewayProvider,
)
from app.providers.process import ExternalProcessRunner, process_registry
from app.storage.workspace import WorkspacePaths
from app.workers.queue import SerialQueueWorker
from tests.test_documents import sample_document


@pytest.mark.asyncio
async def test_process_accepts_metadata_line_larger_than_streamreader_limit(tmp_path: Path) -> None:
    script = "import json; print(json.dumps({'description': 'x' * 70000})); print('finished')"
    result = await ExternalProcessRunner().run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        log_dir=tmp_path / "logs",
        log_name="long-json",
        timeout_seconds=3,
        cancel_check=lambda: False,
    )
    assert json.loads(result.stdout_lines[0])["description"] == "x" * 70000
    assert result.stdout_lines[1] == "finished"


@pytest.mark.asyncio
async def test_cancelling_runner_coroutine_reaps_child(tmp_path: Path, monkeypatch) -> None:
    started = asyncio.Event()
    children = []
    original_register = process_registry.register

    def register(process, terminator):
        children.append(process)
        original_register(process, terminator)

    monkeypatch.setattr(process_registry, "register", register)
    task = asyncio.create_task(
        ExternalProcessRunner().run(
            [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)"],
            cwd=tmp_path,
            log_dir=tmp_path / "logs",
            log_name="cancel-coroutine",
            timeout_seconds=40,
            cancel_check=lambda: False,
            stdout_callback=lambda _: started.set(),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        assert children[0].pid not in process_registry._active
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


@pytest.mark.asyncio
async def test_process_callback_error_stops_child_promptly(tmp_path: Path) -> None:
    def failed_callback(_line: str) -> None:
        raise PipelineCancelled()

    with pytest.raises(PipelineCancelled):
        await asyncio.wait_for(
            ExternalProcessRunner().run(
                [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)"],
                cwd=tmp_path,
                log_dir=tmp_path / "logs",
                log_name="callback-error",
                timeout_seconds=0.5,
                cancel_check=lambda: False,
                stdout_callback=failed_callback,
            ),
            timeout=3,
        )
    assert not process_registry._active


@pytest.mark.asyncio
async def test_process_keeps_utf8_and_final_unterminated_lines(tmp_path: Path) -> None:
    script = (
        "import sys; "
        "sys.stdout.buffer.write(('\\u8bfe' * 50000 + '\\nlast').encode('utf-8')); "
        "sys.stderr.buffer.write('\\u9519\\u8bef'.encode('utf-8'))"
    )
    result = await ExternalProcessRunner().run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        log_dir=tmp_path / "logs",
        log_name="utf8",
        timeout_seconds=3,
        cancel_check=lambda: False,
    )
    assert result.stdout_lines == ("课" * 50000, "last")
    assert result.stderr_lines == ("错误",)


@pytest.mark.asyncio
async def test_windows_terminator_falls_back_when_taskkill_cannot_start(monkeypatch) -> None:
    from app.providers.process import PlatformProcessTreeTerminator

    if sys.platform != "win32":
        pytest.skip("Windows taskkill fallback")

    class ControlledProcess:
        pid = 123
        returncode = None

        async def wait(self):
            if self.returncode is None:
                raise TimeoutError()
            return self.returncode

        def kill(self):
            self.returncode = 1

    async def missing_taskkill(*_args, **_kwargs):
        raise FileNotFoundError("taskkill")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing_taskkill)
    process = ControlledProcess()
    await PlatformProcessTreeTerminator().terminate(process)
    assert process.returncode == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", [OpenAICompatibleAsrProvider, UnifiedAsrGatewayProvider])
@pytest.mark.parametrize("stop_kind", ["cancel", "shutdown"])
async def test_asr_rejects_response_when_stop_arrives_with_result(
    tmp_path: Path,
    provider_class,
    stop_kind: str,
) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    chunk = paths.chunks / "chunk.wav"
    chunk.write_bytes(b"RIFF" + b"0" * 100)
    cancelled = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal cancelled
        if stop_kind == "cancel":
            cancelled = True
        else:
            process_registry.shutting_down = True
        return httpx.Response(200, json={"text": "late result"})

    provider = provider_class(
        base_url="http://gateway.local",
        api_key="",
        timeout_seconds=2,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(PipelineError) as failure:
        await provider.transcribe(
            AsrRequest(AudioChunk(0, 0, 2, str(chunk)), "model"),
            paths,
            cancel_check=lambda: cancelled,
        )
    assert failure.value.code == (
        "PROCESS_CANCELLED" if stop_kind == "cancel" else "backend_shutdown"
    )
    assert not list(paths.asr_raw.glob("*.json"))


@pytest.mark.asyncio
async def test_cancelling_asr_coroutine_closes_inflight_request(tmp_path: Path) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    chunk = paths.chunks / "chunk.wav"
    chunk.write_bytes(b"RIFF" + b"0" * 100)

    async def handler(_request):
        started.set()
        try:
            await asyncio.sleep(30)
            return httpx.Response(200, json={"text": "late"})
        finally:
            stopped.set()

    provider = UnifiedAsrGatewayProvider(
        base_url="http://gateway.local",
        api_key="",
        timeout_seconds=40,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    task = asyncio.create_task(
        provider.transcribe(
            AsrRequest(AudioChunk(0, 0, 2, str(chunk)), "model"),
            paths,
            cancel_check=lambda: False,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert not list(paths.asr_raw.glob("*.json"))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("field", ["attachment_directory_template", "data_directory_template"])
def test_export_static_directory_conflict_fails_without_looping(
    tmp_path: Path,
    monkeypatch,
    field: str,
) -> None:
    document = sample_document(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    config = ExportConfig(vault_path=str(vault), **{field: "shared"})
    first = export_document_bundle(document, config)
    original = Path(first.markdown_path).read_bytes()
    original_exists = Path.exists
    checks = 0

    def bounded_exists(path):
        nonlocal checks
        checks += 1
        if checks > 100:
            pytest.fail(
                "Export keeps searching although renaming cannot change the conflicting directory"
            )
        return original_exists(path)

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "exists", bounded_exists)
        with pytest.raises(PipelineError) as failure:
            export_document_bundle(document, config)
    assert failure.value.code == "EXPORT_CONFLICT"
    assert Path(first.markdown_path).read_bytes() == original
    assert not list(vault.rglob("*.tmp"))


@pytest.mark.parametrize("attachments,data", [("same", "SAME"), ("{stem}.md", "{stem}.data")])
def test_export_rejects_bundle_paths_that_alias_on_windows(
    tmp_path: Path,
    attachments: str,
    data: str,
) -> None:
    document = sample_document(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    config = ExportConfig(
        vault_path=str(vault),
        attachment_directory_template=attachments,
        data_directory_template=data,
    )
    with pytest.raises(PipelineError) as failure:
        export_document_bundle(document, config)
    assert failure.value.code == "EXPORT_PATH_INVALID"
    assert not list(vault.rglob("*.tmp"))


@pytest.mark.parametrize("template", ["{title:{document_id}}", "{title:.1}", "{title!r}"])
def test_export_rejects_unsupported_format_specs(template: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ExportConfig(vault_path=str(tmp_path), filename_template=template)


def test_export_overwrite_restores_previous_bundle_after_commit_failure(
    tmp_path: Path, monkeypatch
) -> None:
    document = sample_document(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    config = ExportConfig(vault_path=str(vault), conflict_policy="overwrite")
    first = export_document_bundle(document, config)
    original = {
        path.relative_to(vault): path.read_bytes() for path in vault.rglob("*") if path.is_file()
    }
    replace = Path.replace

    def fail_markdown_commit(path, target):
        if path.parent.name.startswith(".video2doc-export-") and Path(target) == Path(
            first.markdown_path
        ):
            raise PermissionError("controlled commit failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_markdown_commit)
    with pytest.raises(PermissionError):
        export_document_bundle(document, config)
    actual = {
        path.relative_to(vault): path.read_bytes() for path in vault.rglob("*") if path.is_file()
    }
    assert actual == original
    assert not list(vault.rglob("*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["pipeline", "cache"])
async def test_worker_continues_after_unhandled_task_or_cache_error(
    monkeypatch, failure_kind
) -> None:
    worker = SerialQueueWorker()
    completed = asyncio.Event()
    with SessionLocal() as session:
        tasks = [
            Task(
                source="controlled",
                source_type="LOCAL_FILE",
                title=f"failure-{i}",
                status="QUEUED",
                current_stage="QUEUED",
            )
            for i in range(2)
        ]
        session.add_all(tasks)
        session.commit()
        task_ids = [task.id for task in tasks]

    # Isolate selection to these tasks while retaining the database status gate.
    def claim():
        with SessionLocal() as session:
            tasks = [session.get(Task, task_id) for task_id in task_ids]
            if any(task.status == "PROCESSING" for task in tasks):
                return None
            for task in tasks:
                if task.status == "QUEUED":
                    task.status = "PROCESSING"
                    session.commit()
                    return task.id
        return None

    async def pipeline(task_id):
        if task_id == task_ids[0] and failure_kind == "pipeline":
            with SessionLocal() as session:
                session.add(
                    TaskStageRun(
                        task_id=task_id, attempt=1, stage="MEDIA_PROBE", status="PROCESSING"
                    )
                )
                session.commit()
            raise OSError("controlled workspace failure")
        with SessionLocal() as session:
            session.get(Task, task_id).status = "COMPLETED"
            session.commit()
        if task_id == task_ids[1]:
            completed.set()

    def clear_cache(task_id):
        if task_id == task_ids[0] and failure_kind == "cache":
            raise PermissionError("controlled locked cache")

    monkeypatch.setattr(worker, "_claim_next_task", claim)
    monkeypatch.setattr(worker, "_auto_clear_completed_task_cache", clear_cache)
    monkeypatch.setattr("app.workers.queue.run_pipeline", pipeline)
    runner = asyncio.create_task(worker._run())
    try:
        await asyncio.wait_for(completed.wait(), timeout=2)
        with SessionLocal() as session:
            first = session.get(Task, task_ids[0])
            assert first.status == ("FAILED" if failure_kind == "pipeline" else "COMPLETED")
            if failure_kind == "pipeline":
                assert first.error_code == "worker_pipeline_error"
                assert first.completed_at is not None
                assert first.stages[0].status == "FAILED"
                assert first.stages[0].finished_at is not None
            assert session.get(Task, task_ids[1]).status == "COMPLETED"
    finally:
        worker._stop_event.set()
        worker.notify()
        await asyncio.gather(runner, return_exceptions=True)
        with SessionLocal() as session:
            for task_id in task_ids:
                session.delete(session.get(Task, task_id))
            session.commit()


@pytest.mark.parametrize("relative", ["logs", "01_extract/audio"])
def test_cache_refuses_directory_links_before_deleting_any_files(
    tmp_path: Path, relative: str
) -> None:
    from app.services.cache import clear_workspace_cache

    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    outside = tmp_path / "preserved"
    outside.mkdir()
    evidence = outside / "original.stdout.log"
    evidence.write_bytes(b"original media or log")
    cache = paths.media / "source.mp4"
    cache.write_bytes(b"generated media")
    linked = paths.root / relative
    linked.rmdir()  # Only the empty directory created by this test.
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(linked))
    else:
        linked.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(OSError):
            clear_workspace_cache(paths.root)
        assert evidence.read_bytes() == b"original media or log"
        assert cache.read_bytes() == b"generated media"
        assert not (outside / "task.log").exists()
    finally:
        if sys.platform == "win32":
            linked.rmdir()  # Remove only this test's junction, never its target.
        else:
            linked.unlink()


@pytest.mark.parametrize(
    "original,edited,code",
    [
        ("结果为 42 次。", "结果为 142 次。", "NUMBERS_LOST"),
        ("结果为42次。", "结果为43次。", "NUMBERS_LOST"),
        ("取 4 和 42。", "取 42 和 42。", "NUMBERS_LOST"),
        ("调用 APIName。", "调用 APIName2。", "IDENTIFIERS_LOST"),
    ],
)
def test_faithfulness_requires_whole_number_and_identifier_tokens(
    tmp_path: Path,
    original: str,
    edited: str,
    code: str,
) -> None:
    block = sample_document(tmp_path).all_blocks()[0]
    block.original_text = original
    block.edited_text = edited
    report = validate_block_edit(
        block, returned_source_ids=[r.source_id for r in block.source_references]
    )
    assert not report.accepted
    assert code in {issue.code for issue in report.issues}


def test_faithfulness_accepts_punctuation_and_markdown_only_edits(tmp_path: Path) -> None:
    block = sample_document(tmp_path).all_blocks()[0]
    block.original_text = "调用 APIName. 结果为42次。"
    block.edited_text = "调用 **APIName**。结果为 **42** 次。"
    report = validate_block_edit(
        block, returned_source_ids=[r.source_id for r in block.source_references]
    )
    assert report.accepted


def test_task_list_does_not_load_unrequested_history() -> None:
    from sqlalchemy import event
    from app.db.session import engine
    from app.services.tasks import TaskService

    with SessionLocal() as session:
        task = Task(source="list", source_type="LOCAL_FILE", title="List only", status="COMPLETED")
        session.add(task)
        session.commit()
        task_id = task.id
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with SessionLocal() as session:
            tasks = TaskService(session).list("COMPLETED")
            assert any(task.id == task_id for task in tasks)
        assert len(statements) == 1, (
            "List/SSE must query task columns without loading history tables"
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
