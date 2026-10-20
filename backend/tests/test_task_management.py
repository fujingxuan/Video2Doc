from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.errors import ConflictError
from app.db.session import SessionLocal
from app.models import AppSetting, MarkdownExport, Task, TaskConfigSnapshot
from app.schemas.api import TaskUpdate
from app.services.tasks import TaskService
from app.storage.workspace import create_workspace, read_json


def _create_task(
    *,
    data_root: Path,
    output_root: Path,
    status: str,
    title: str,
) -> tuple[int, Path]:
    snapshot = {
        "schema_version": 6,
        "pipeline": "SIMULATED",
        "processing": {
            "ocr_enabled": False,
            "embedded_subtitle_ocr_fallback": False,
            "text_source": "ASR",
        },
        "paths": {"data": str(data_root), "inbox": str(output_root)},
        "export": {"vault_path": str(output_root), "inbox_relative_path": "."},
        "input": {
            "source": "old-source",
            "source_type": "LOCAL_FILE",
            "title": title,
            "text_source": "ASR",
        },
    }
    with SessionLocal() as session:
        task = Task(
            source="old-source",
            source_type="LOCAL_FILE",
            title=title,
            status=status,
            current_stage=status,
        )
        session.add(task)
        session.flush()
        workspace = create_workspace(
            data_root,
            task_id=task.id,
            title=title,
            snapshot=snapshot,
        )
        task.workspace_path = str(workspace)
        task.snapshot = TaskConfigSnapshot(schema_version=6, snapshot_json=snapshot)
        session.commit()
        return task.id, workspace


def test_queued_task_can_be_edited_and_becomes_the_next_default(
    clean_database: None,
) -> None:
    data_root = get_settings().data_dir
    output_root = get_settings().inbox_dir
    task_id, workspace = _create_task(
        data_root=data_root,
        output_root=output_root,
        status="QUEUED",
        title="Before edit",
    )

    payload = TaskUpdate.model_validate(
        {
            "pipeline_mode": "SIMULATED",
            "input": {
                "source": "https://example.invalid/edited",
                "source_type": "URL",
                "title": "After edit",
                "text_source": "SUBTITLE",
            },
            "config_overrides": {
                "ocr_enabled": False,
                "embedded_subtitle_ocr_fallback": False,
                "ocr_frame_interval_seconds": 21,
                "text_edit_enabled": False,
            },
        }
    )
    with SessionLocal() as session:
        result = TaskService(session).update(task_id, payload)

    assert result.title == "After edit"
    assert result.source == "https://example.invalid/edited"
    written = read_json(workspace / "task_config.json")
    assert written is not None
    assert written["input"]["text_source"] == "SUBTITLE"
    assert written["processing"]["ocr_frame_interval_seconds"] == 21
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task is not None
        assert task.snapshot.snapshot_json["input"]["title"] == "After edit"
        assert task.snapshot.snapshot_json["subtitle"]["source_url"].endswith("/edited")
        settings = session.get(AppSetting, 1)
        assert settings is not None
        assert settings.last_task_config["input"]["source_type"] == "URL"


def test_delete_task_removes_database_workspace_and_export_bundle(
    client: TestClient,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "tasks"
    output_root = tmp_path / "exports"
    output_root.mkdir(parents=True)
    task_id, workspace = _create_task(
        data_root=data_root,
        output_root=output_root,
        status="COMPLETED",
        title="Delete me",
    )
    markdown = output_root / "Delete me.md"
    word = output_root / "Delete me.docx"
    attachments = output_root / "Delete me.assets"
    sidecars = output_root / "Delete me.data"
    markdown.write_text("# generated", encoding="utf-8")
    word.write_bytes(b"generated Word")
    attachments.mkdir()
    (attachments / "frame.jpg").write_bytes(b"frame")
    sidecars.mkdir()
    (sidecars / "document.json").write_text("{}", encoding="utf-8")
    (workspace / "05_output/generated.md").write_text("# workspace", encoding="utf-8")
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.output_path = str(markdown)
        task.export_path = str(markdown)
        task.markdown_exports.append(
            MarkdownExport(
                attempt=1,
                target_type="OBSIDIAN",
                status="COMPLETED",
                markdown_path=str(markdown),
                attachment_directory=str(attachments),
                data_directory=str(sidecars),
                config_json={"vault_path": str(output_root), "inbox_relative_path": ".",
                             "word_path": str(word)},
            )
        )
        session.commit()

    response = client.delete(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 200, response.text
    assert response.json()["deleted_at"] is not None
    assert workspace.exists() and markdown.exists() and attachments.exists() and sidecars.exists()
    assert word.exists()
    response = client.delete(f"/api/v1/tasks/{task_id}/permanent")
    assert response.status_code == 200, response.text
    assert response.json()["files_removed"] >= 4
    assert not workspace.exists()
    assert not markdown.exists()
    assert not word.exists()
    assert not attachments.exists()
    assert not sidecars.exists()
    with SessionLocal() as session:
        assert session.get(Task, task_id) is None


def test_processing_task_must_be_cancelled_before_delete(
    clean_database: None,
    tmp_path: Path,
) -> None:
    task_id, workspace = _create_task(
        data_root=tmp_path / "tasks",
        output_root=tmp_path / "exports",
        status="PROCESSING",
        title="Still running",
    )

    with SessionLocal() as session, pytest.raises(ConflictError):
        TaskService(session).delete(task_id)

    assert workspace.is_dir()
    with SessionLocal() as session:
        assert session.get(Task, task_id) is not None


def test_delete_never_removes_the_original_local_input(
    clean_database: None,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "tasks"
    output_root = tmp_path / "exports"
    output_root.mkdir(parents=True)
    original = output_root / "original.mp4"
    original.write_bytes(b"original-media")
    task_id, workspace = _create_task(
        data_root=data_root,
        output_root=output_root,
        status="COMPLETED",
        title="Protected input",
    )
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.source = str(original)
        task.source_type = "LOCAL_FILE"
        task.output_path = str(original)
        session.commit()

    with SessionLocal() as session:
        TaskService(session).delete(task_id)
        with pytest.raises(ConflictError):
            TaskService(session).purge(task_id)

    assert original.read_bytes() == b"original-media"
    assert workspace.is_dir()
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task is not None
        assert task.status == "COMPLETED"
