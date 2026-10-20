from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.errors import PipelineError
from app.db.session import SessionLocal
from app.documents.editor import build_full_article_document
from app.main import app
from app.models import Task, TaskStageRun
from app.pipeline import real
from app.services.manual_review import review_seed_path
from app.services.tasks import TaskService
from app.storage.workspace import read_json, write_json
from tests.test_documents import sample_document
from tests.test_task_management import _create_task


def test_copy_audio_requires_current_validation_and_existing_workspace_file(tmp_path, monkeypatch):
    task_id, workspace, _seed = seed_review(tmp_path)
    client = TestClient(app)
    route = f"/api/v1/tasks/{task_id}/audio/copy"
    copied = []
    monkeypatch.setattr("app.services.clipboard.copy_file", copied.append)
    assert client.post(route).status_code == 409
    audio = workspace / "01_extract/audio/normalized.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"RIFF" + b"0" * 100)
    with SessionLocal() as session:
        session.add(TaskStageRun(task_id=task_id, attempt=1, stage="AUDIO_VALIDATE",
                                 status="COMPLETED", progress=100))
        session.commit()
    assert client.post(route).status_code == 200
    assert copied == [audio.resolve()]
    audio.unlink()
    assert client.post(route).status_code == 404
    assert len(copied) == 1


def seed_review(tmp_path: Path):
    (tmp_path / "exports").mkdir(exist_ok=True)
    task_id, workspace = _create_task(
        data_root=tmp_path / "tasks",
        output_root=tmp_path / "exports",
        status="WAITING_REVIEW",
        title="人工校对测试",
    )
    document = build_full_article_document(sample_document(tmp_path))
    transcript = workspace / "02_asr" / "normalized.json"
    timeline = workspace / "04_fusion" / "timeline.json"
    write_json(transcript, {"text": "原始转写 42"})
    write_json(timeline, {"events": []})
    seed = review_seed_path(workspace, 1)
    write_json(
        seed,
        {
            "document": document.model_dump(mode="json"),
            "context": {
                "transcript_artifacts": {"normalized": str(transcript)},
                "fusion_payload": {"events": []},
                "fusion_artifacts": {"timeline": str(timeline)},
            },
        },
    )
    write_json(workspace / "05_output" / "document.json", document.model_dump(mode="json"))
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        task.document_path = str(workspace / "05_output" / "document.json")
        session.commit()
    return task_id, workspace, seed


def test_audio_multipart_upload_copies_content_and_purges_owned_copy(clean_database, tmp_path):
    source = tmp_path / "会议录音.wav"
    source.write_bytes(b"RIFF" + b"audio-bytes" * 50)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    # No lifespan: the worker stays stopped so uploaded bytes can be inspected first.
    client = TestClient(app)
    with source.open("rb") as stream:
        response = client.post(
            "/api/v1/tasks/upload-audio",
            files={
                "files": ("../../会议录音.wav", stream, "audio/wav"),
            },
            data={"configuration": json.dumps({"pipeline_mode": "SIMULATED"})},
        )
    assert response.status_code == 201, response.text
    task = response.json()[0]
    copied = Path(task["source"])
    assert copied.is_relative_to(Path(task["workspace_path"]) / "00_source" / "upload")
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == original_hash
    snapshot = client.get(f"/api/v1/tasks/{task['id']}/snapshot").json()
    assert snapshot["input"]["upload"]["original_name"] == "会议录音.wav"
    assert snapshot["input"]["upload"]["sha256"] == original_hash
    assert snapshot["input"]["source"] == str(copied)
    assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 200
    purged = client.delete(f"/api/v1/tasks/{task['id']}/permanent")
    assert purged.status_code == 200, purged.text
    assert not copied.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize(
    "filename,content,configuration,expected",
    [
        ("empty.wav", b"", "{}", 422),
        ("script.exe", b"audio", "{}", 422),
        ("audio.mp3", b"audio", "not-json", 422),
    ],
)
def test_upload_rejects_invalid_inputs(clean_database, filename, content, configuration, expected):
    response = TestClient(app).post(
        "/api/v1/tasks/upload-audio",
        files={"files": (filename, content)},
        data={"configuration": configuration},
    )
    assert response.status_code == expected


@pytest.mark.asyncio
async def test_manual_draft_persists_validates_and_resumes_export_without_asr(
    clean_database, tmp_path, monkeypatch
):
    task_id, workspace, seed = seed_review(tmp_path)
    original_seed = seed.read_bytes()
    original_transcript = (workspace / "02_asr" / "normalized.json").read_bytes()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        route = f"/api/v1/tasks/{task_id}/manual-review"
        draft = (await client.get(route)).json()
        download = await client.get(route + "/draft")
        assert download.status_code == 200 and download.text == draft["original_text"]
        assert "attachment" in download.headers["Content-Disposition"]
        edited = draft["original_text"].replace("42", "142")
        payload = {
            "attempt": draft["attempt"],
            "source_sha256": draft["source_sha256"],
            "edited_text": edited.replace("\n", "\r\n"),
        }
        assert (await client.put(route, json=payload)).status_code == 200
        assert (await client.get(route)).json()["edited_text"] == edited
        stale = await client.post(
            route + "/submit", json={**payload, "attempt": 2, "action": "edited"}
        )
        assert stale.status_code == 409
        warning = await client.post(route + "/submit", json={**payload, "action": "edited"})
        assert warning.json()["requires_confirmation"] is True
        assert any(item["code"] == "NUMBERS_LOST" for item in warning.json()["warnings"])
        assert (await client.get(f"/api/v1/tasks/{task_id}")).json()["status"] == "WAITING_REVIEW"
        response = await client.post(
            route + "/submit", json={**payload, "action": "edited", "acknowledge_warnings": True}
        )
        assert response.status_code == 200 and response.json()["task"]["status"] == "QUEUED"
        assert (
            await client.post(route + "/submit", json={**payload, "action": "original"})
        ).status_code == 409

    def unexpected(*args, **kwargs):
        raise AssertionError("Manual resume must not acquire media or rerun inference")

    monkeypatch.setattr(real.MediaSourceResolver, "acquire", unexpected)
    monkeypatch.setattr(real.UnifiedAsrGatewayProvider, "transcribe", unexpected)
    export = real.export_document_bundle
    monkeypatch.setattr(
        real,
        "export_document_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PipelineError("EXPORT_FAILED", "controlled export failure")
        ),
    )
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        task.status = "PROCESSING"
        session.commit()
    await real.run_real_pipeline(task_id)
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task.status == "FAILED" and task.review_submission_path
        retried = TaskService(session).retry(task_id)
        assert retried.attempt == 2
        task.status = "PROCESSING"
        session.commit()
    monkeypatch.setattr(real, "export_document_bundle", export)
    await real.run_real_pipeline(task_id)
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        assert task.status == "COMPLETED", task.error_message
        assert edited in Path(task.export_path).read_text(encoding="utf-8")
        document = read_json(Path(task.document_path))
        assert document["edit_metadata"]["user_confirmed_warnings"] is True
    assert seed.read_bytes() == original_seed
    assert (workspace / "02_asr" / "normalized.json").read_bytes() == original_transcript


@pytest.mark.asyncio
async def test_waiting_review_can_be_trashed_restored_and_cancelled(clean_database, tmp_path):
    task_id, _workspace, seed = seed_review(tmp_path)
    digest = hashlib.sha256(seed.read_bytes()).hexdigest()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/queue")).json()["waiting_review"] >= 1
        assert (await client.delete(f"/api/v1/tasks/{task_id}")).status_code == 200
        response = await client.post(
            f"/api/v1/tasks/{task_id}/manual-review/submit",
            json={
                "attempt": 1,
                "source_sha256": digest,
                "action": "original",
            },
        )
        assert response.status_code == 409
        assert (await client.post(f"/api/v1/tasks/{task_id}/restore")).json()[
            "status"
        ] == "WAITING_REVIEW"
        assert (await client.post(f"/api/v1/tasks/{task_id}/cancel")).json()[
            "status"
        ] == "CANCELLED"
