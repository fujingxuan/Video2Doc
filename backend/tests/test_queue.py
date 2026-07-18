from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.models import Task, TaskConfigSnapshot
from app.core.config import get_settings
from app.storage.workspace import create_workspace
from app.workers.queue import queue_worker
from tests.helpers import wait_for_status


def test_three_tasks_execute_serially_and_persist_outputs(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks",
        json={
            "inputs": [
                {"source": f"https://example.invalid/{index}", "source_type": "URL", "title": f"Task {index}"}
                for index in range(1, 4)
            ],
            "config_overrides": {"ocr_enabled": False},
        },
    )
    assert response.status_code == 201, response.text
    task_ids = [item["id"] for item in response.json()]

    observed_running_counts: list[int] = []
    deadline = time.monotonic() + 10
    states: dict[int, dict] = {}
    while time.monotonic() < deadline:
        queue = client.get("/api/v1/queue").json()
        observed_running_counts.append(queue["running"])
        assert queue["running"] <= 1
        states = {task_id: client.get(f"/api/v1/tasks/{task_id}").json() for task_id in task_ids}
        if all(item["status"] == "COMPLETED" for item in states.values()):
            break
        time.sleep(0.01)
    assert all(item["status"] == "COMPLETED" for item in states.values())
    assert 1 in observed_running_counts
    started = [datetime.fromisoformat(states[task_id]["started_at"]) for task_id in task_ids]
    assert started == sorted(started)
    for task_id in task_ids:
        detail = states[task_id]
        assert Path(detail["workspace_path"], "task_config.json").is_file()
        assert Path(detail["workspace_path"], "task_state.json").is_file()
        assert Path(detail["output_path"]).is_file()


def test_cancel_waiting_and_processing_tasks(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        json={
            "inputs": [
                {"source": "cancel-running", "source_type": "LOCAL_FILE", "title": "Cancel running"},
                {"source": "cancel-waiting", "source_type": "LOCAL_FILE", "title": "Cancel waiting"},
            ]
        },
    ).json()
    running_id, waiting_id = created[0]["id"], created[1]["id"]

    waiting_cancel = client.post(f"/api/v1/tasks/{waiting_id}/cancel")
    assert waiting_cancel.status_code == 200
    assert waiting_cancel.json()["status"] == "CANCELLED"

    wait_for_status(client, running_id, {"PROCESSING"})
    running_cancel = client.post(f"/api/v1/tasks/{running_id}/cancel")
    assert running_cancel.status_code == 200
    cancelled = wait_for_status(client, running_id, {"CANCELLED"})
    assert cancelled["current_stage"] == "CANCELLED"


def test_failure_then_retry_succeeds(client: TestClient) -> None:
    created = client.post(
        "/api/v1/tasks",
        json={
            "inputs": [
                {
                    "source": "intentional-failure",
                    "source_type": "LOCAL_FILE",
                    "title": "Retry demonstration",
                    "simulate_failure": "once",
                }
            ]
        },
    )
    task_id = created.json()[0]["id"]
    failed = wait_for_status(client, task_id, {"FAILED"})
    assert failed["error_code"] == "simulated_failure"
    assert "Intentional simulated failure" in failed["error_message"]

    retry = client.post(f"/api/v1/tasks/{task_id}/retry")
    assert retry.status_code == 200, retry.text
    assert retry.json()["attempt"] == 2
    completed = wait_for_status(client, task_id, {"COMPLETED"})
    assert completed["attempt"] == 2
    assert {stage["attempt"] for stage in completed["stages"]} == {1, 2}


def test_restart_recovery_marks_processing_task_interrupted(client: TestClient) -> None:
    with SessionLocal() as session:
        task = Task(
            source="restart-test",
            source_type="LOCAL_FILE",
            title="Restart recovery",
            status="PROCESSING",
            current_stage="ASR_PROCESSING",
            progress=37,
            started_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.flush()
        snapshot = {
            "pipeline": "SIMULATED",
            "models": {"asr": None, "ocr": None, "text_processing": None},
            "processing": {
                "ocr_enabled": False,
                "remove_fillers": True,
                "keep_timestamps": True,
            },
            "simulation": {"failure_mode": "never"},
            "paths": {
                "inbox": str(get_settings().inbox_dir),
                "data": str(get_settings().data_dir),
            },
        }
        task.snapshot = TaskConfigSnapshot(
            snapshot_json=snapshot
        )
        task.workspace_path = str(
            create_workspace(
                get_settings().data_dir,
                task_id=task.id,
                title=task.title,
                snapshot=snapshot,
            )
        )
        session.commit()
        task_id = task.id

    assert queue_worker.recover_interrupted() >= 1
    recovered = client.get(f"/api/v1/tasks/{task_id}").json()
    assert recovered["status"] == "INTERRUPTED"
    assert recovered["error_code"] == "backend_restart"
    retry = client.post(f"/api/v1/tasks/{task_id}/retry")
    assert retry.status_code == 200
    assert wait_for_status(client, task_id, {"COMPLETED"})["attempt"] == 2
