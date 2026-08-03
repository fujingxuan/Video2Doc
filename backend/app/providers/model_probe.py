from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.core.credentials import resolve_credential
from app.core.secrets import SecretStorageError
from app.models import GatewayModel, ModelConfig
from app.providers.gateways import (
    discover_gateway_models,
    openai_chat_completions_endpoint,
    probe_gateway,
    trust_environment_proxy,
)


@dataclass(frozen=True, slots=True)
class ModelProbeResult:
    status: str
    detail: str
    checked_at: datetime
    latency_ms: int


async def probe_text_model(
    model: ModelConfig,
    *,
    timeout_seconds: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelProbeResult:
    checked_at = datetime.now(timezone.utc)
    started = time.monotonic()
    if not model.base_url:
        return _result("INVALID_CONFIG", "未配置 Base URL", checked_at, started)
    try:
        endpoint = openai_chat_completions_endpoint(model.base_url)
        secret = resolve_credential(model)
    except (SecretStorageError, ValueError) as exc:
        return _result("INVALID_CONFIG", str(exc), checked_at, started)
    if (model.secret_env_name or model.secret_ciphertext) and not secret:
        detail = (
            f"缺少环境变量 {model.secret_env_name}"
            if model.secret_env_name
            else "无法读取已保存密钥"
        )
        return _result("MISSING_SECRET", detail, checked_at, started)

    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    payload = {
        "model": model.name,
        "messages": [
            {
                "role": "user",
                "content": "这是连通性测试。请只回复 OK。",
            }
        ],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=trust_environment_proxy(endpoint),
        ) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
    except httpx.TimeoutException:
        return _result("TIMEOUT", "模型实调用超时", checked_at, started)
    except (OSError, httpx.HTTPError) as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:300]}"
        return _result("UNREACHABLE", detail, checked_at, started)

    failure = _http_failure(response)
    if failure is not None:
        status, detail = failure
        return _result(status, detail, checked_at, started)
    try:
        body = response.json()
    except ValueError:
        return _result("INVALID_RESPONSE", "接口返回了非 JSON 响应", checked_at, started)
    if not isinstance(body, dict) or not isinstance(body.get("choices"), list):
        return _result(
            "INVALID_RESPONSE", "接口未返回 Chat Completions choices", checked_at, started
        )
    return _result("HEALTHY", "Chat Completions 最小实调用成功", checked_at, started)


async def probe_gateway_model(
    model: GatewayModel,
    *,
    timeout_seconds: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelProbeResult:
    checked_at = datetime.now(timezone.utc)
    started = time.monotonic()
    gateway_result = await probe_gateway(
        model.gateway,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
    if gateway_result.status != "HEALTHY":
        return _result(
            gateway_result.status,
            gateway_result.detail or "Gateway 连接测试失败",
            checked_at,
            started,
        )

    protocol = model.gateway.protocol
    if protocol == "dashscope-fun-asr-websocket":
        return _result(
            "HEALTHY",
            "WebSocket 认证握手成功；模型 ID 已按当前配置校验",
            checked_at,
            started,
        )
    if protocol == "paddlex-layout-parsing-v1":
        expected = str((model.gateway.settings or {}).get("model_name") or "PaddleOCR-VL-1.6-0.9B")
        status = "HEALTHY" if model.model_name == expected else "MODEL_NOT_FOUND"
        detail = (
            "Gateway 健康检查成功；PaddleX 模型配置匹配"
            if status == "HEALTHY"
            else f"Gateway 配置的模型为 {expected}"
        )
        return _result(status, detail, checked_at, started)

    try:
        available = await discover_gateway_models(
            model.gateway,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
    except httpx.TimeoutException:
        return _result("TIMEOUT", "读取 Gateway 模型目录超时", checked_at, started)
    except httpx.HTTPStatusError as exc:
        failure = _http_failure(exc.response)
        status, detail = failure or ("UNHEALTHY", "读取 Gateway 模型目录失败")
        return _result(status, detail, checked_at, started)
    except (OSError, httpx.HTTPError, ValueError) as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:300]}"
        return _result("UNREACHABLE", detail, checked_at, started)

    if any(item.get("model_name") == model.model_name for item in available):
        return _result(
            "HEALTHY",
            "Gateway 健康检查及模型目录校验均通过",
            checked_at,
            started,
        )
    return _result(
        "MODEL_NOT_FOUND",
        f"Gateway 模型目录中未找到 {model.model_name}",
        checked_at,
        started,
    )


def _http_failure(response: httpx.Response) -> tuple[str, str] | None:
    if response.status_code < 400:
        return None
    if response.status_code in {401, 403}:
        return "AUTH_FAILED", f"认证失败（HTTP {response.status_code}）"
    if response.status_code == 404:
        return "MODEL_NOT_FOUND", "模型或兼容接口不存在（HTTP 404）"
    if response.status_code == 408:
        return "TIMEOUT", "模型服务请求超时（HTTP 408）"
    if response.status_code == 429:
        return "RATE_LIMITED", "模型服务限流或额度不足（HTTP 429）"
    if response.status_code >= 500:
        return "UNHEALTHY", f"模型服务暂时不可用（HTTP {response.status_code}）"
    return "UNHEALTHY", f"模型实调用失败（HTTP {response.status_code}）"


def _result(
    status: str,
    detail: str,
    checked_at: datetime,
    started: float,
) -> ModelProbeResult:
    return ModelProbeResult(
        status=status,
        detail=detail,
        checked_at=checked_at,
        latency_ms=max(0, round((time.monotonic() - started) * 1000)),
    )
