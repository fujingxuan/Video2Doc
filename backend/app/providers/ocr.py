from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.core.errors import PipelineError
from app.providers.gateways import gateway_endpoint, trust_environment_proxy
from app.providers.asr import float_or_none, optional_text, redact_sensitive
from app.storage.workspace import write_json


@dataclass(frozen=True, slots=True)
class OcrBlock:
    text: str
    bounding_box: tuple[float, ...] | None
    confidence: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OcrResult:
    provider: str
    model: str
    text: str
    blocks: tuple[OcrBlock, ...]
    raw_response_reference: str


class OcrGatewayClient(Protocol):
    async def recognize(
        self, image_path: Path, *, model: str, raw_path: Path
    ) -> OcrResult: ...


class UnifiedOcrGatewayClient:
    name = "video2doc-ocr-http-v1"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = gateway_endpoint(base_url, "/v1/ocr/recognitions")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.trust_env = trust_environment_proxy(self.endpoint)

    async def recognize(self, image_path: Path, *, model: str, raw_path: Path) -> OcrResult:
        try:
            with image_path.open("rb") as stream:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout_seconds),
                    transport=self.transport,
                    trust_env=self.trust_env,
                ) as client:
                    response = await client.post(
                        self.endpoint,
                        headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                        data={"model": model},
                        files={"file": (image_path.name, stream, "application/octet-stream")},
                    )
        except httpx.TimeoutException as exc:
            raise PipelineError("OCR_GATEWAY_TIMEOUT", "OCR Gateway 请求超时", retryable=True) from exc
        except httpx.NetworkError as exc:
            raise PipelineError(
                "OCR_GATEWAY_UNAVAILABLE", "OCR Gateway 无法连接", technical_detail=str(exc), retryable=True
            ) from exc
        if response.status_code in {401, 403}:
            raise PipelineError("OCR_GATEWAY_AUTH_FAILED", "OCR Gateway 认证失败")
        if response.status_code >= 400:
            raise PipelineError("OCR_GATEWAY_FAILED", f"OCR Gateway 请求失败（HTTP {response.status_code}）")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise PipelineError("OCR_GATEWAY_INVALID_RESPONSE", "OCR Gateway 返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise PipelineError("OCR_GATEWAY_INVALID_RESPONSE", "OCR Gateway 返回格式错误")
        write_json(raw_path, redact_sensitive(payload))
        return normalize_ocr_response(payload, model=model, raw_path=raw_path, provider=self.name)


def normalize_ocr_response(
    payload: dict[str, Any], *, model: str, raw_path: Path, provider: str
) -> OcrResult:
    text = optional_text(payload.get("text")) or ""
    raw_blocks = payload.get("blocks")
    blocks: list[OcrBlock] = []
    if isinstance(raw_blocks, list):
        for item in raw_blocks:
            if not isinstance(item, dict):
                continue
            block_text = optional_text(item.get("text"))
            if not block_text:
                continue
            raw_box = item.get("bounding_box", item.get("bbox"))
            box = (
                tuple(float(value) for value in raw_box)
                if isinstance(raw_box, list) and all(isinstance(value, (int, float)) for value in raw_box)
                else None
            )
            blocks.append(
                OcrBlock(
                    text=block_text,
                    bounding_box=box,
                    confidence=float_or_none(item.get("confidence")),
                )
            )
    if not text and blocks:
        text = "\n".join(item.text for item in blocks)
    if not text:
        raise PipelineError("OCR_GATEWAY_INVALID_RESPONSE", "OCR Gateway 未返回文本")
    return OcrResult(
        provider=optional_text(payload.get("provider")) or provider,
        model=optional_text(payload.get("model")) or model,
        text=text,
        blocks=tuple(blocks),
        raw_response_reference=str(raw_path),
    )
