from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.providers.asr import AsrResult, AsrSegment
from app.providers.media import MediaInfo, MediaStream
from tests.helpers import wait_for_status


def media_info(path: Path, *, video: bool) -> MediaInfo:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return MediaInfo(
        container="wav" if not video else "mp4",
        duration=4.0,
        has_audio=True,
        has_video=video,
        selected_audio_stream=1 if video else 0,
        audio_streams=(
            MediaStream(
                1 if video else 0,
                "audio",
                "aac" if video else "pcm_s16le",
                4.0,
                48000 if video else 16000,
                2 if video else 1,
            ),
        ),
        video_streams=(MediaStream(0, "video", "h264", 4.0),) if video else (),
        raw_probe_path=str(path),
    )


def test_database_worker_runs_real_pipeline_and_exposes_results(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "真实 输入.mp4"
    source.write_bytes(b"not-a-real-video-but-provider-is-controlled")
    monkeypatch.setenv("ONEAPI_API_KEY", "integration-secret")

    model = client.post(
        "/api/v1/models",
        json={"name": "integration-whisper", "capabilities": ["ASR"], "enabled": True},
    ).json()
    profile = client.post(
        "/api/v1/profiles",
        json={"name": "integration-real", "asr_model_id": model["id"], "ocr_enabled": False},
    ).json()
    settings = client.put(
        "/api/v1/settings", json={"oneapi_base_url": "http://oneapi.integration/v1"}
    )
    assert settings.status_code == 200

    async def fake_probe(self, media_path, paths, *, cancel_check, artifact_name="ffprobe"):
        return media_info(paths.metadata / f"{artifact_name}.json", video=Path(media_path) == source)

    async def fake_normalize(
        self, media_path, probed, paths, *, cancel_check, progress
    ):
        output = paths.audio / "normalized.wav"
        output.write_bytes(b"RIFF" + b"0" * 100)
        progress(1.0, "controlled audio complete")
        return output, media_info(paths.metadata / "ffprobe_normalized_audio.json", video=False)

    async def fake_transcribe(self, request, paths, *, cancel_check):
        raw_path = paths.asr_raw / f"chunk_{request.chunk.index:04d}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            '{"language":"zh","segments":[{"start":0.5,"end":2.0,"text":"受控转写"}]}',
            encoding="utf-8",
        )
        return AsrResult(
            provider=self.name,
            model=request.model,
            language="zh",
            chunk_index=request.chunk.index,
            raw_response_reference=str(raw_path),
            segments=(
                AsrSegment(
                    segment_id="local",
                    start_time=0.5,
                    end_time=2.0,
                    text="受控转写",
                    confidence=None,
                    language="zh",
                    provider=self.name,
                    model=request.model,
                    chunk_index=request.chunk.index,
                    raw_response_reference=str(raw_path),
                ),
            ),
        )

    monkeypatch.setattr("app.pipeline.real.FfprobeProvider.probe", fake_probe)
    monkeypatch.setattr("app.pipeline.real.FfmpegProvider.normalize_audio", fake_normalize)
    monkeypatch.setattr(
        "app.pipeline.real.OpenAICompatibleAsrProvider.transcribe", fake_transcribe
    )

    created = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "REAL",
            "inputs": [{"source": str(source), "source_type": "LOCAL_FILE"}],
            "profile_id": profile["id"],
            "config_overrides": {"audio_chunk_seconds": 600},
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()[0]["id"]
    completed = wait_for_status(client, task_id, {"COMPLETED"})
    assert completed["progress"] == 100
    assert completed["media_info"]["has_video"] is True
    assert completed["transcript_preview"]["text"] == "受控转写"
    assert [stage["stage"] for stage in completed["stages"]] == [
        "INPUT_VALIDATE",
        "MEDIA_ACQUIRE",
        "MEDIA_PROBE",
        "AUDIO_EXTRACT",
        "AUDIO_VALIDATE",
        "AUDIO_CHUNK",
        "ASR_TRANSCRIBE",
        "ASR_COMBINE",
        "ASR_VALIDATE",
    ]
    transcript = client.get(f"/api/v1/tasks/{task_id}/transcript")
    assert transcript.status_code == 200
    assert transcript.json()["segments"][0]["start_time"] == 0.5
    media = client.get(f"/api/v1/tasks/{task_id}/media")
    assert media.status_code == 200 and media.json()["has_audio"] is True
    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    assert any(item["path"] == "02_asr/normalized.json" for item in artifacts)
    snapshot_text = str(client.get(f"/api/v1/tasks/{task_id}/snapshot").json())
    assert "integration-secret" not in snapshot_text
