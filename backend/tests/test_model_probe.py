from __future__ import annotations

import json

import httpx
import pytest

from app.models import GatewayConfig, GatewayModel, ModelConfig
from app.providers.model_probe import probe_gateway_model, probe_text_model


@pytest.mark.asyncio
async def test_text_model_probe_performs_minimal_chat_completion(monkeypatch) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "probe-secret")
    model = ModelConfig(
        id=11,
        name="glm-4.7",
        provider="zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        secret_env_name="TEST_LLM_KEY",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/paas/v4/chat/completions"
        assert request.headers["authorization"] == "Bearer probe-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "glm-4.7"
        assert payload["stream"] is False
        assert "source_text" not in request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    result = await probe_text_model(model, transport=httpx.MockTransport(handler))

    assert result.status == "HEALTHY"
    assert "最小实调用成功" in result.detail
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_text_model_probe_reports_missing_secret_without_network(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_LLM_KEY", raising=False)
    model = ModelConfig(
        id=12,
        name="missing-secret-model",
        provider="custom",
        base_url="https://llm.example/v1",
        secret_env_name="MISSING_LLM_KEY",
    )

    result = await probe_text_model(model)

    assert result.status == "MISSING_SECRET"
    assert result.detail == "缺少环境变量 MISSING_LLM_KEY"


@pytest.mark.asyncio
async def test_text_model_probe_classifies_auth_failure_without_exposing_secret(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BAD_LLM_KEY", "must-not-be-returned")
    model = ModelConfig(
        id=13,
        name="auth-model",
        provider="custom",
        base_url="https://llm.example/v1",
        secret_env_name="BAD_LLM_KEY",
    )

    result = await probe_text_model(
        model,
        transport=httpx.MockTransport(lambda _request: httpx.Response(401)),
    )

    assert result.status == "AUTH_FAILED"
    assert "must-not-be-returned" not in result.detail


def gateway_model() -> GatewayModel:
    gateway = GatewayConfig(
        id=21,
        name="ASR Gateway",
        gateway_type="ASR",
        protocol="video2doc-asr-http-v1",
        base_url="http://gateway.test",
        enabled=True,
        settings={},
    )
    return GatewayModel(
        id=22,
        gateway=gateway,
        gateway_id=gateway.id,
        model_name="whisper-large-v3",
        display_name="Whisper large-v3",
    )


@pytest.mark.asyncio
async def test_gateway_model_probe_checks_gateway_and_specific_model_directory() -> None:
    seen_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "whisper-large-v3"}]})
        return httpx.Response(404)

    result = await probe_gateway_model(
        gateway_model(),
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "HEALTHY"
    assert seen_paths == ["/health", "/v1/models"]


@pytest.mark.asyncio
async def test_gateway_model_probe_does_not_treat_gateway_health_as_model_health() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"data": [{"id": "different-model"}]})

    result = await probe_gateway_model(
        gateway_model(),
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "MODEL_NOT_FOUND"
    assert "whisper-large-v3" in result.detail
