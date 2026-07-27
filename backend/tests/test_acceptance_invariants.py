from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.errors import ConflictError
from app.db.session import SessionLocal
from app.main import app
from app.models import Task, TaskConfigSnapshot, TaskStageRun
from app.pipeline.simulated import _complete_task
from app.services.tasks import TaskService
from app.storage.workspace import create_workspace
from tests.helpers import wait_for_status


def _processing_task(title: str, *, with_stage: bool = False) -> int:
    snapshot = {
        "schema_version": 1,
        "pipeline": "SIMULATED",
        "models": {"asr": None, "ocr": None, "text_processing": None},
        "processing": {"ocr_enabled": False},
        "simulation": {"failure_mode": "never"},
        "paths": {
            "inbox": str(get_settings().inbox_dir),
            "data": str(get_settings().data_dir),
        },
    }
    with SessionLocal() as session:
        task = Task(
            source=title,
            source_type="LOCAL_FILE",
            title=title,
            status="PROCESSING",
            current_stage="GENERATING_OUTPUT",
            progress=96,
            started_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.flush()
        task.snapshot = TaskConfigSnapshot(snapshot_json=snapshot)
        task.workspace_path = str(
            create_workspace(
                get_settings().data_dir,
                task_id=task.id,
                title=title,
                snapshot=snapshot,
            )
        )
        if with_stage:
            task.stages.append(
                TaskStageRun(
                    attempt=1,
                    stage="GENERATING_OUTPUT",
                    status="PROCESSING",
                    progress=96,
                    started_at=datetime.now(timezone.utc),
                )
            )
        session.commit()
        return task.id


def test_cancel_and_completion_race_has_one_consistent_winner(clean_database: None) -> None:
    cancel_wins_id = _processing_task("race-cancel-wins")
    with SessionLocal() as session:
        TaskService(session).cancel(cancel_wins_id)
    _complete_task(cancel_wins_id)
    with SessionLocal() as session:
        cancelled = session.get(Task, cancel_wins_id)
        assert cancelled is not None
        assert cancelled.status == "CANCELLED"
        assert cancelled.output_path is None

    completion_wins_id = _processing_task("race-completion-wins")
    _complete_task(completion_wins_id)
    with SessionLocal() as session:
        with pytest.raises(ConflictError):
            TaskService(session).cancel(completion_wins_id)
        completed = session.get(Task, completion_wins_id)
        assert completed is not None
        assert completed.status == "COMPLETED"
        assert completed.output_path is not None


def test_backend_startup_recovers_legacy_processing_task(clean_database: None) -> None:
    task_id = _processing_task("startup-recovery", with_stage=True)

    with TestClient(app) as startup_client:
        recovered = startup_client.get(f"/api/v1/tasks/{task_id}")
        assert recovered.status_code == 200
        detail = recovered.json()
        assert detail["status"] == "INTERRUPTED"
        assert detail["error_code"] == "backend_restart"
        assert detail["stages"][0]["status"] == "INTERRUPTED"


def test_retry_preserves_original_snapshot_and_increments_attempt(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "SIMULATED",
            "inputs": [
                {
                    "source": "snapshot-retry",
                    "source_type": "LOCAL_FILE",
                    "simulate_failure": "once",
                }
            ],
            "config_overrides": {"ocr_enabled": False, "keep_timestamps": True},
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()[0]["id"]
    original_snapshot = client.get(f"/api/v1/tasks/{task_id}/snapshot").json()
    wait_for_status(client, task_id, {"FAILED"})

    changed = client.put(
        "/api/v1/settings",
        json={"default_ocr_enabled": True, "default_keep_timestamps": False},
    )
    assert changed.status_code == 200, changed.text
    retried = client.post(f"/api/v1/tasks/{task_id}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["attempt"] == 2
    assert client.get(f"/api/v1/tasks/{task_id}/snapshot").json() == original_snapshot
    completed = wait_for_status(client, task_id, {"COMPLETED"})
    assert completed["attempt"] == 2
    assert {stage["attempt"] for stage in completed["stages"]} == {1, 2}


def test_ocr_disabled_skips_ocr_stage(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "SIMULATED",
            "inputs": [{"source": "no-ocr", "source_type": "LOCAL_FILE"}],
            "config_overrides": {"ocr_enabled": False},
        },
    )
    assert created.status_code == 201, created.text
    completed = wait_for_status(client, created.json()[0]["id"], {"COMPLETED"})
    assert all(stage["stage"] != "OCR_PROCESSING" for stage in completed["stages"])


def test_database_rejects_two_processing_tasks(clean_database: None) -> None:
    with SessionLocal() as session:
        session.add_all(
            [
                Task(
                    source=f"db-invariant-{index}",
                    source_type="LOCAL_FILE",
                    title=f"DB invariant {index}",
                    status="PROCESSING",
                    current_stage="PREPARING",
                )
                for index in range(2)
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        processing_count = session.scalar(
            select(func.count()).select_from(Task).where(Task.status == "PROCESSING")
        )
        assert processing_count == 0
