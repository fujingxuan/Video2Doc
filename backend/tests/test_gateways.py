from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.models import GatewayConfig, GatewayModel
from app.core.errors import PipelineCancelled
from app.providers.asr import AsrRequest, AudioChunk, UnifiedAsrGatewayProvider
from app.providers.gateways import discover_gateway_models, probe_gateway
from app.providers.ocr import UnifiedOcrGatewayClient
from app.providers.process import process_registry
from app.services.gateways import _apply_discovered_capabilities
from app.services.tasks import _gateway_model_snapshot
from app.schemas.api import GatewayCreate, GatewayModelCreate
from app.storage.workspace import WorkspacePaths


@pytest.fixture(autouse=True)
def reset_process_registry() -> None:
    process_registry.begin_lifecycle()


def gateway(**overrides) -> GatewayConfig:
    values = {
        "id": 1,
        "name": "mock-gateway",
        "gateway_type": "ASR",
        "protocol": "video2doc-asr-http-v1",
        "base_url": "http://gateway.test",
        "secret_env_name": None,
        "enabled": True,
        "health_status": "UNKNOWN",
        "settings": {},
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return GatewayConfig(**values)


def test_gateway_config_rejects_embedded_credentials_and_secret_values() -> None:
    with pytest.raises(ValidationError):
        GatewayCreate(
            name="unsafe",
            gateway_type="ASR",
            protocol="video2doc-asr-http-v1",
            base_url="https://user:password@example.test",
        )
    with pytest.raises(ValidationError):
        GatewayCreate(
            name="unsafe-secret",
            gateway_type="ASR",
            protocol="video2doc-asr-http-v1",
            base_url="https://example.test",
            secret_env_name="not an env name",
        )
    with pytest.raises(ValidationError):
        GatewayCreate(
            name="unsafe-settings",
            gateway_type="ASR",
            protocol="video2doc-asr-http-v1",
            base_url="https://example.test",
            settings={"nested": {"api_key": "super-secret-value"}},
        )
    with pytest.raises(ValidationError):
        GatewayModelCreate(
            gateway_id=1,
            model_name="unsafe-model",
            display_name="Unsafe model",
            capabilities={"access_token": "super-secret-value"},
        )


@pytest.mark.asyncio
async def test_gateway_health_and_model_discovery_use_independent_protocol() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "model-a", "capabilities": {"word_timestamps": True}}]},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    result = await probe_gateway(gateway(), transport=transport)
    models = await discover_gateway_models(gateway(), transport=transport)
    assert result.status == "HEALTHY"
    assert models == [
        {
            "model_name": "model-a",
            "display_name": "model-a",
            "capabilities": {"word_timestamps": True},
        }
    ]


def test_discovered_capabilities_populate_standard_model_fields() -> None:
    model = GatewayModel(
        gateway_id=1,
        model_name="model-a",
        display_name="Model A",
        capabilities={},
    )
    _apply_discovered_capabilities(
        model,
        {
            "word_timestamps": True,
            "segment_timestamps": True,
            "confidence": True,
            "max_chunk_seconds": 55,
            "max_alignment_seconds": 300,
            "languages": ["zh", "en"],
            "max_tokens": 2048,
            "nested": {"token": "must-not-be-stored", "safe": "value"},
        },
    )
    assert model.supports_word_timestamps is True
    assert model.supports_segment_timestamps is True
    assert model.supports_confidence is True
    assert model.max_chunk_seconds == 55
    assert model.max_alignment_seconds == 300
    assert model.languages == ["zh", "en"]
    assert model.capabilities["max_tokens"] == 2048
    assert model.capabilities["nested"] == {"safe": "value"}


def test_unknown_offline_model_does_not_fall_back_to_six_hundred_seconds() -> None:
    parent = gateway()
    model = GatewayModel(
        id=1,
        gateway=parent,
        gateway_id=parent.id,
        model_name="unknown-offline",
        display_name="Unknown Offline",
        capabilities={"offline": True},
        supports_native_long_audio=False,
        supports_streaming=False,
    )
    snapshot = _gateway_model_snapshot(
        model,
        timeout_seconds=120,
        max_retries=1,
        requested_chunk_seconds=600,
        overlap_seconds=5,
    )
    assert snapshot is not None
    assert snapshot["chunking"] == {
        "mode": "offline_overlap",
        "chunk_seconds": 60.0,
        "overlap_seconds": 5,
        "step_seconds": 55.0,
    }


def test_snapshot_removes_sensitive_legacy_capability_values() -> None:
    parent = gateway()
    model = GatewayModel(
        id=1,
        gateway=parent,
        gateway_id=parent.id,
        model_name="legacy-unsafe",
        display_name="Legacy unsafe",
        capabilities={"offline": True, "nested": {"api_key": "super-secret-value"}},
    )
    snapshot = _gateway_model_snapshot(
        model,
        timeout_seconds=120,
        max_retries=1,
        requested_chunk_seconds=60,
        overlap_seconds=5,
    )
    assert snapshot is not None
    assert snapshot["capabilities"]["nested"] == {}
    assert "super-secret-value" not in json.dumps(snapshot)


@pytest.mark.asyncio
async def test_gateway_secret_is_read_by_env_name_and_never_returned(monkeypatch) -> None:
    monkeypatch.setenv("TEST_GATEWAY_KEY", "super-secret-value")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer super-secret-value"
        return httpx.Response(200, json={"status": "ok"})

    item = gateway(secret_env_name="TEST_GATEWAY_KEY")
    result = await probe_gateway(item, transport=httpx.MockTransport(handler))
    assert result.status == "HEALTHY"
    assert "super-secret-value" not in (result.detail or "")


@pytest.mark.asyncio
async def test_unified_asr_gateway_normalizes_segment_and_word_timestamps(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    audio = paths.chunks / "chunk.wav"
    audio.write_bytes(b"RIFF" + b"0" * 100)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/asr/transcriptions"
        return httpx.Response(
            200,
            json={
                "provider": "mock-local",
                "model": "model-a",
                "language": "zh",
                "text": "你好",
                "segments": [
                    {
                        "start": 0.1,
                        "end": 0.8,
                        "text": "你好",
                        "words": [{"start": 0.1, "end": 0.8, "text": "你好"}],
                    }
                ],
                "words": [],
            },
        )

    provider = UnifiedAsrGatewayProvider(
        base_url="http://gateway.test",
        api_key="",
        timeout_seconds=5,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.transcribe(
        AsrRequest(AudioChunk(0, 0, 1, str(audio)), "model-a", "zh", "word"),
        paths,
        cancel_check=lambda: False,
    )
    assert result.provider == "mock-local"
    assert result.segments[0].words[0].start_time == 0.1


@pytest.mark.asyncio
async def test_ocr_gateway_mock_preserves_blocks_coordinates_and_nulls(tmp_path: Path) -> None:
    image = tmp_path / "frame.png"
    image.write_bytes(b"fake-image")
    raw_path = tmp_path / "ocr.json"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/ocr/recognitions"
        return httpx.Response(
            200,
            json={
                "provider": "mock-ocr",
                "model": "ocr-a",
                "text": "标题",
                "blocks": [{"text": "标题", "bbox": [1, 2, 3, 4], "confidence": None}],
            },
        )

    client = UnifiedOcrGatewayClient(
        base_url="http://ocr.test", transport=httpx.MockTransport(handler)
    )
    result = await client.recognize(image, model="ocr-a", raw_path=raw_path)
    assert result.blocks[0].bounding_box == (1.0, 2.0, 3.0, 4.0)
    assert result.blocks[0].confidence is None
    assert json.loads(raw_path.read_text(encoding="utf-8"))["text"] == "标题"


@pytest.mark.asyncio
async def test_unified_gateway_notifies_worker_when_upload_is_cancelled(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    audio = paths.chunks / "chunk.wav"
    audio.write_bytes(b"RIFF" + b"0" * 100)
    seen_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.startswith("/v1/asr/cancellations/"):
            return httpx.Response(200, json={"status": "cancellation_requested"})
        await asyncio.sleep(1)
        return httpx.Response(200, json={"text": "late result"})

    provider = UnifiedAsrGatewayProvider(
        base_url="http://gateway.test",
        api_key="",
        timeout_seconds=5,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(PipelineCancelled):
        await provider.transcribe(
            AsrRequest(
                AudioChunk(0, 0, 1, str(audio)),
                "model-a",
                request_id="task-1-attempt-1-chunk-0",
            ),
            paths,
            cancel_check=lambda: True,
        )
    assert "/v1/asr/cancellations/task-1-attempt-1-chunk-0" in seen_paths
