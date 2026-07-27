from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.core.errors import PipelineError
from app.providers.asr import (
    AsrRequest,
    AsrResult,
    AsrSegment,
    AudioChunk,
    AudioChunker,
    OpenAICompatibleAsrProvider,
    combine_asr_results,
    normalize_asr_response,
    redact_sensitive,
    save_transcript_artifacts,
    transcription_endpoint,
    validate_segments,
)
from app.storage.workspace import WorkspacePaths
from app.providers.process import process_registry


@pytest.fixture(autouse=True)
def reset_process_registry() -> None:
    process_registry.begin_lifecycle()


def segment(chunk: int, local_start: float | None, local_end: float | None, text: str) -> AsrSegment:
    return AsrSegment(
        segment_id=f"local-{chunk}-{text}",
        start_time=local_start,
        end_time=local_end,
        text=text,
        confidence=None,
        language="zh",
        provider="test",
        model="whisper-test",
        chunk_index=chunk,
        raw_response_reference=f"raw-{chunk}.json",
    )


def test_chunk_offsets_map_local_segments_to_global_timeline_in_stable_order() -> None:
    chunks = [
        AudioChunk(0, 0.0, 60.0, "chunk0.wav", overlap_after_seconds=5.0),
        AudioChunk(1, 55.0, 90.0, "chunk1.wav", overlap_before_seconds=5.0),
    ]
    results = [
        AsrResult("test", "m", "zh", 1, "raw1", (segment(1, 6.5, 8.0, "第二块"),)),
        AsrResult(
            "test",
            "m",
            "zh",
            0,
            "raw0",
            (segment(0, 2.0, 4.0, "第一块"), segment(0, None, None, "无时间戳")),
        ),
    ]
    combined = combine_asr_results(results, chunks)
    assert [item.text for item in combined] == ["第一块", "第二块", "无时间戳"]
    assert combined[0].start_time == 2.0
    assert combined[1].start_time == 61.5
    assert combined[2].start_time is None
    assert [item.segment_id for item in combined] == [
        "segment-000001",
        "segment-000002",
        "segment-000003",
    ]
    validate_segments(combined)


def test_provider_missing_confidence_or_timestamps_remains_null(tmp_path: Path) -> None:
    request = AsrRequest(AudioChunk(0, 0, 10, "chunk.wav"), "model")
    result = normalize_asr_response(
        {"text": "完整文本", "language": "zh"}, request, tmp_path / "raw.json", "provider"
    )
    assert result.segments[0].confidence is None
    assert result.segments[0].start_time is None
    assert result.segments[0].end_time is None


@pytest.mark.asyncio
async def test_audio_chunker_single_chunk_writes_manifest(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    audio = paths.audio / "normalized.wav"
    audio.write_bytes(b"RIFF" + b"0" * 100)
    progress: list[float | None] = []
    chunks = await AudioChunker("unused").split(
        audio,
        12.0,
        paths,
        chunk_seconds=60,
        cancel_check=lambda: False,
        progress=lambda value, _message: progress.append(value),
    )
    assert len(chunks) == 1
    assert Path(chunks[0].path).read_bytes() == audio.read_bytes()
    manifest = json.loads((paths.chunks / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["overlap_seconds"] == 5
    assert manifest["chunks"][0]["source_start_seconds"] == 0
    assert manifest["chunks"][0]["overlap_after_seconds"] == 0
    assert progress == [1.0]


@pytest.mark.asyncio
async def test_openai_compatible_asr_saves_raw_response_and_normalizes(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    chunk_path = paths.chunks / "chunk_0000.wav"
    chunk_path.write_bytes(b"RIFF" + b"0" * 100)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(
            200,
            json={
                "language": "zh",
                "text": "你好",
                "segments": [{"start": 0.25, "end": 1.5, "text": "你好"}],
            },
        )

    provider = OpenAICompatibleAsrProvider(
        base_url="http://oneapi.local/v1",
        api_key="test-key",
        timeout_seconds=5,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.transcribe(
        AsrRequest(AudioChunk(0, 0, 2, str(chunk_path)), "whisper-test"),
        paths,
        cancel_check=lambda: False,
    )
    assert result.segments[0].start_time == 0.25
    assert result.segments[0].confidence is None
    assert Path(result.raw_response_reference).is_file()
    raw = json.loads(Path(result.raw_response_reference).read_text(encoding="utf-8"))
    assert raw["segments"][0]["text"] == "你好"


@pytest.mark.asyncio
async def test_asr_auth_error_is_classified_without_retry(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    chunk_path = paths.chunks / "chunk.wav"
    chunk_path.write_bytes(b"RIFF" + b"0" * 100)
    provider = OpenAICompatibleAsrProvider(
        base_url="http://oneapi.local",
        api_key="bad-key",
        timeout_seconds=5,
        max_retries=2,
        transport=httpx.MockTransport(lambda _request: httpx.Response(401, json={"error": "no"})),
    )
    with pytest.raises(PipelineError) as failure:
        await provider.transcribe(
            AsrRequest(AudioChunk(0, 0, 2, str(chunk_path)), "model"),
            paths,
            cancel_check=lambda: False,
        )
    assert failure.value.code == "ASR_AUTH_FAILED"
    assert "bad-key" not in (failure.value.technical_detail or "")


@pytest.mark.asyncio
async def test_asr_timeout_and_cancel_are_classified(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    chunk_path = paths.chunks / "chunk.wav"
    chunk_path.write_bytes(b"RIFF" + b"0" * 100)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("controlled timeout", request=request)

    timeout_provider = OpenAICompatibleAsrProvider(
        base_url="http://oneapi.local",
        api_key="key",
        timeout_seconds=1,
        max_retries=0,
        transport=httpx.MockTransport(timeout_handler),
    )
    with pytest.raises(PipelineError) as timeout:
        await timeout_provider.transcribe(
            AsrRequest(AudioChunk(0, 0, 2, str(chunk_path)), "model"),
            paths,
            cancel_check=lambda: False,
        )
    assert timeout.value.code == "ASR_TIMEOUT"

    async def slow_handler(_request: httpx.Request) -> httpx.Response:
        import asyncio

        await asyncio.sleep(5)
        return httpx.Response(200, json={"text": "late"})

    cancel_provider = OpenAICompatibleAsrProvider(
        base_url="http://oneapi.local",
        api_key="key",
        timeout_seconds=10,
        max_retries=0,
        transport=httpx.MockTransport(slow_handler),
    )
    called = 0

    def cancel_check() -> bool:
        nonlocal called
        called += 1
        return called >= 1

    with pytest.raises(PipelineError) as cancelled:
        await cancel_provider.transcribe(
            AsrRequest(AudioChunk(0, 0, 2, str(chunk_path)), "model"),
            paths,
            cancel_check=cancel_check,
        )
    assert cancelled.value.code == "PROCESS_CANCELLED"


def test_transcript_artifacts_are_separate_and_versioned(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    segments = [segment(0, 0.0, 1.0, "第一句"), segment(0, 1.0, 2.5, "第二句")]
    artifacts = save_transcript_artifacts(
        paths,
        segments,
        provider="test",
        model="model",
        language="zh",
        chunk_count=1,
    )
    assert Path(artifacts["raw"]).name == "raw.json"
    assert Path(artifacts["normalized"]).name == "normalized.json"
    assert "第一句" in Path(artifacts["text"]).read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,000" in Path(artifacts["srt"]).read_text(
        encoding="utf-8"
    )
    normalized = json.loads(Path(artifacts["normalized"]).read_text(encoding="utf-8"))
    assert normalized["schema_version"] == 1


def test_sensitive_response_fields_are_recursively_redacted() -> None:
    value = {"token": "secret", "nested": [{"Authorization": "Bearer secret"}], "text": "ok"}
    redacted = redact_sensitive(value)
    assert redacted["token"] == "***"
    assert redacted["nested"][0]["Authorization"] == "***"
    assert redacted["text"] == "ok"
    assert transcription_endpoint("https://oneapi.example/v1") == (
        "https://oneapi.example/v1/audio/transcriptions"
    )
