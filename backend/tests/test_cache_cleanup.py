from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.models import AppSetting, Task
from app.services.cache import TaskCacheService
from app.storage.workspace import create_workspace
from app.workers.queue import SerialQueueWorker


def _task_with_cache(tmp_path: Path, *, status: str, title: str) -> tuple[int, Path]:
    snapshot = {
        "pipeline": "SIMULATED",
        "processing": {
            "ocr_enabled": False,
            "embedded_subtitle_ocr_fallback": False,
        },
    }
    with SessionLocal() as session:
        task = Task(
            source=title,
            source_type="LOCAL_FILE",
            title=title,
            status=status,
            current_stage=status,
        )
        session.add(task)
        session.flush()
        workspace = create_workspace(
            tmp_path,
            task_id=task.id,
            title=title,
            snapshot=snapshot,
        )
        task.workspace_path = str(workspace)
        session.commit()
        return task.id, workspace


def _populate_cache(workspace: Path) -> None:
    (workspace / "00_source/media/source.mp4").write_bytes(b"media-cache")
    (workspace / "01_extract/audio/normalized.wav").write_bytes(b"audio-cache")
    (workspace / "01_extract/chunks/chunk_0000.wav").write_bytes(b"chunk-cache")
    (workspace / "01_extract/frames/candidates/frame.jpg").write_bytes(b"frame-cache")
    (workspace / "logs/ffmpeg.stderr.log").write_text("temporary", encoding="utf-8")
    (workspace / "02_asr/normalized.json").write_text("{}", encoding="utf-8")
    (workspace / "05_output/final.md").write_text("# preserved", encoding="utf-8")


def test_cache_cleanup_is_scoped_to_terminal_selected_tasks(
    clean_database: None,
    tmp_path: Path,
) -> None:
    completed_id, completed_workspace = _task_with_cache(
        tmp_path, status="COMPLETED", title="completed-cache"
    )
    processing_id, processing_workspace = _task_with_cache(
        tmp_path, status="PROCESSING", title="processing-cache"
    )
    _populate_cache(completed_workspace)
    _populate_cache(processing_workspace)

    with SessionLocal() as session:
        result = TaskCacheService(session).clear(
            [completed_id, processing_id, 999_999]
        )

    assert result["tasks_cleared"] == 1
    assert result["files_removed"] == 5
    assert result["bytes_freed"] > 0
    assert len(result["skipped"]) == 2
    assert not (completed_workspace / "00_source/media/source.mp4").exists()
    assert not (completed_workspace / "01_extract/audio/normalized.wav").exists()
    assert (completed_workspace / "02_asr/normalized.json").is_file()
    assert (completed_workspace / "05_output/final.md").read_text(encoding="utf-8") == (
        "# preserved"
    )
    assert (processing_workspace / "00_source/media/source.mp4").is_file()
    assert (completed_workspace / "artifacts.json").is_file()


def test_worker_automatically_clears_completed_task_cache_when_enabled(
    clean_database: None,
    tmp_path: Path,
) -> None:
    task_id, workspace = _task_with_cache(
        tmp_path, status="COMPLETED", title="automatic-cache"
    )
    _populate_cache(workspace)
    with SessionLocal() as session:
        settings = session.get(AppSetting, 1) or AppSetting(id=1)
        settings.auto_clear_task_cache = True
        session.add(settings)
        session.commit()

    SerialQueueWorker()._auto_clear_completed_task_cache(task_id)

    assert not (workspace / "01_extract/chunks/chunk_0000.wav").exists()
    assert (workspace / "05_output/final.md").is_file()


def test_cache_api_clears_selected_task(
    client: TestClient,
    tmp_path: Path,
) -> None:
    task_id, workspace = _task_with_cache(
        tmp_path, status="FAILED", title="api-selected-cache"
    )
    _populate_cache(workspace)

    response = client.post(
        "/api/v1/system/cache/clear",
        json={"task_ids": [task_id]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["tasks_cleared"] == 1
    assert not (workspace / "00_source/media/source.mp4").exists()
    assert (workspace / "05_output/final.md").is_file()
