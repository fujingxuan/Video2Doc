from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets

from app.models import GatewayConfig
from app.core.credentials import resolve_credential
from app.core.secrets import SecretStorageError


@dataclass(frozen=True, slots=True)
class GatewayProbeResult:
    status: str
    detail: str | None
    checked_at: datetime
    runtime: dict[str, Any] | None = None


def gateway_endpoint(base_url: str, suffix: str) -> str:
    base = base_url.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Gateway Base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Gateway Base URL cannot contain credentials")
    return f"{base}{suffix}"


def openai_chat_completions_endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Text Gateway Base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Text Gateway Base URL cannot contain credentials")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        return base
    compatible_roots = ("/v1", "/v3", "/v4", "/v1beta/openai")
    suffix = "/chat/completions" if path.endswith(compatible_roots) else "/v1/chat/completions"
    return f"{base}{suffix}"


def trust_environment_proxy(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").casefold()
    return hostname not in {"127.0.0.1", "localhost", "::1"}


async def probe_gateway(
    gateway: GatewayConfig,
    *,
    timeout_seconds: float = 10.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GatewayProbeResult:
    checked_at = datetime.now(timezone.utc)
    try:
        secret = resolve_credential(gateway)
    except SecretStorageError as exc:
        return GatewayProbeResult("MISSING_SECRET", str(exc), checked_at)
    if (gateway.secret_env_name or gateway.secret_ciphertext) and not secret:
        detail = (
            f"缺少环境变量 {gateway.secret_env_name}"
            if gateway.secret_env_name
            else "无法读取已保存密钥"
        )
        return GatewayProbeResult("MISSING_SECRET", detail, checked_at)
    try:
        if gateway.protocol == "dashscope-fun-asr-websocket":
            parsed = urlparse(gateway.base_url)
            if parsed.scheme != "wss" or not parsed.netloc:
                return GatewayProbeResult(
                    "INVALID_CONFIG", "DashScope 地址必须使用 wss://", checked_at
                )
            headers = {"Authorization": f"Bearer {secret}", "user-agent": "Video2Doc/0.3"}
            workspace_id = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
            if workspace_id:
                headers["X-DashScope-WorkSpace"] = workspace_id
            async with websockets.connect(
                gateway.base_url,
                additional_headers=headers,
                open_timeout=timeout_seconds,
                close_timeout=2,
            ):
                return GatewayProbeResult("HEALTHY", "WebSocket 握手成功", checked_at)
        endpoint = gateway_endpoint(gateway.base_url, "/health")
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=trust_environment_proxy(endpoint),
        ) as client:
            response = await client.get(endpoint, headers=headers)
        if response.status_code in {401, 403}:
            return GatewayProbeResult("AUTH_FAILED", f"HTTP {response.status_code}", checked_at)
        if response.status_code >= 400:
            return GatewayProbeResult("UNHEALTHY", f"HTTP {response.status_code}", checked_at)
        runtime: dict[str, Any] | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                runtime = payload
        except ValueError:
            pass
        detail = "健康检查通过"
        if runtime is not None and "loaded_model" in runtime:
            loaded_model = str(runtime.get("loaded_model") or "").strip()
            device = str(runtime.get("device") or "未知设备")
            detail = (
                f"Gateway 在线；已加载 {loaded_model}；设备 {device}"
                if loaded_model
                else f"Gateway 在线；模型尚未加载；预定设备 {device}"
            )
        return GatewayProbeResult("HEALTHY", detail, checked_at, runtime)
    except (OSError, httpx.HTTPError, TimeoutError, ValueError) as exc:
        return GatewayProbeResult("UNREACHABLE", f"{type(exc).__name__}: {exc}", checked_at)
    except Exception as exc:  # WebSocket implementations expose several handshake exception types.
        return GatewayProbeResult("UNREACHABLE", f"{type(exc).__name__}: {exc}", checked_at)


async def discover_gateway_models(
    gateway: GatewayConfig,
    *,
    timeout_seconds: float = 15.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, Any]]:
    if gateway.protocol == "dashscope-fun-asr-websocket":
        return []
    if gateway.protocol == "paddlex-layout-parsing-v1":
        model_name = str((gateway.settings or {}).get("model_name") or "PaddleOCR-VL-1.6-0.9B")
        return [
            {
                "model_name": model_name,
                "display_name": "PaddleOCR-VL 1.6 (Docker/PaddleX)",
                "capabilities": {
                    "local": True,
                    "docker": True,
                    "layout_parsing": True,
                    "text_blocks": True,
                    "bounding_boxes": True,
                    "reading_order": True,
                    "markdown": True,
                    "confidence": False,
                    "managed_lifecycle": bool((gateway.settings or {}).get("managed_lifecycle")),
                },
            }
        ]
    endpoint = gateway_endpoint(gateway.base_url, "/v1/models")
    try:
        secret = resolve_credential(gateway)
    except SecretStorageError as exc:
        raise ValueError(str(exc)) from exc
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        transport=transport,
        trust_env=trust_environment_proxy(endpoint),
    ) as client:
        response = await client.get(endpoint, headers=headers)
    response.raise_for_status()
    payload = response.json()
    raw_models = (
        payload.get("data", payload.get("models", payload))
        if isinstance(payload, dict)
        else payload
    )
    if not isinstance(raw_models, list):
        raise ValueError("Gateway model discovery response must contain a list")
    normalized: list[dict[str, Any]] = []
    for item in raw_models:
        if isinstance(item, str):
            normalized.append({"model_name": item, "display_name": item})
        elif isinstance(item, dict):
            model_name = str(
                item.get("id") or item.get("model_name") or item.get("name") or ""
            ).strip()
            if model_name:
                normalized.append(
                    {
                        "model_name": model_name,
                        "display_name": str(
                            item.get("display_name") or item.get("name") or model_name
                        ),
                        "capabilities": item.get("capabilities") or {},
                    }
                )
    return normalized
