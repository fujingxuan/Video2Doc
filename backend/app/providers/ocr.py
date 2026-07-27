from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import httpx

from app.core.errors import PipelineCancelled, PipelineError, PipelineInterrupted
from app.providers.gateways import gateway_endpoint, trust_environment_proxy
from app.providers.asr import float_or_none, optional_text, redact_sensitive
from app.providers.process import process_registry
from app.storage.workspace import write_json


OcrCancelCheck = Callable[[], bool | Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class OcrBlock:
    text: str
    bounding_box: tuple[float, ...] | None
    confidence: float | None
    polygon: tuple[tuple[float, float], ...] | None = None
    reading_order: int | None = None
    language: str | None = None
    uncertain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OcrResult:
    provider: str
    model: str
    text: str
    blocks: tuple[OcrBlock, ...]
    raw_response_reference: str
    frame_id: str | None = None
    media_timestamp: float | None = None
    language: str | None = None
    elapsed_seconds: float | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "blocks": [item.to_dict() for item in self.blocks],
        }


class OcrGatewayClient(Protocol):
    async def recognize(
        self,
        image_path: Path,
        *,
        model: str,
        raw_path: Path,
        frame_id: str | None = None,
        media_timestamp: float | None = None,
        language: str | None = None,
        cancel_check: OcrCancelCheck | None = None,
    ) -> OcrResult: ...


class UnifiedOcrGatewayClient:
    name = "video2doc-ocr-http-v1"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 120.0,
        max_retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = gateway_endpoint(base_url, "/v1/ocr/recognitions")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, min(max_retries, 10))
        self.transport = transport
        self.trust_env = trust_environment_proxy(self.endpoint)

    async def recognize(
        self,
        image_path: Path,
        *,
        model: str,
        raw_path: Path,
        frame_id: str | None = None,
        media_timestamp: float | None = None,
        language: str | None = None,
        cancel_check: OcrCancelCheck | None = None,
    ) -> OcrResult:
        started = time.monotonic()
        last_error: PipelineError | None = None
        for retry_index in range(self.max_retries + 1):
            await _ensure_ocr_active(cancel_check)
            try:
                payload = await self._request(
                    image_path,
                    model=model,
                    frame_id=frame_id,
                    media_timestamp=media_timestamp,
                    language=language,
                    cancel_check=cancel_check,
                )
                write_json(raw_path, redact_sensitive(payload))
                return normalize_ocr_response(
                    payload,
                    model=model,
                    raw_path=raw_path,
                    provider=self.name,
                    frame_id=frame_id,
                    media_timestamp=media_timestamp,
                    language=language,
                    elapsed_seconds=time.monotonic() - started,
                )
            except PipelineError as exc:
                last_error = exc
                if not exc.retryable or retry_index >= self.max_retries:
                    raise
                await _cancel_aware_delay(min(4.0, 0.5 * (2**retry_index)), cancel_check)
        raise last_error or PipelineError("OCR_GATEWAY_FAILED", "OCR Gateway 请求失败")

    async def _request(
        self,
        image_path: Path,
        *,
        model: str,
        frame_id: str | None,
        media_timestamp: float | None,
        language: str | None,
        cancel_check: OcrCancelCheck | None,
    ) -> dict[str, Any]:
        data = {
            "model": model,
            "frame_id": frame_id or "",
            "media_timestamp": "" if media_timestamp is None else f"{media_timestamp:.6f}",
            "language": language or "",
        }

        async def send() -> httpx.Response:
            try:
                with image_path.open("rb") as stream:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(self.timeout_seconds),
                        transport=self.transport,
                        trust_env=self.trust_env,
                    ) as client:
                        return await client.post(
                            self.endpoint,
                            headers=(
                                {"Authorization": f"Bearer {self.api_key}"}
                                if self.api_key
                                else {}
                            ),
                            data=data,
                            files={
                                "file": (
                                    image_path.name,
                                    stream,
                                    "application/octet-stream",
                                )
                            },
                        )
            except httpx.TimeoutException as exc:
                raise PipelineError(
                    "OCR_GATEWAY_TIMEOUT", "OCR Gateway 请求超时", retryable=True
                ) from exc
            except httpx.NetworkError as exc:
                raise PipelineError(
                    "OCR_GATEWAY_UNAVAILABLE",
                    "OCR Gateway 无法连接",
                    technical_detail=str(exc),
                    retryable=True,
                ) from exc

        response = await _await_ocr_request(send(), cancel_check)
        if response.status_code in {401, 403}:
            raise PipelineError("OCR_GATEWAY_AUTH_FAILED", "OCR Gateway 认证失败")
        if response.status_code == 429 or response.status_code >= 500:
            raise PipelineError(
                "OCR_GATEWAY_RETRYABLE",
                f"OCR Gateway 暂时不可用（HTTP {response.status_code}）",
                retryable=True,
            )
        if response.status_code >= 400:
            raise PipelineError(
                "OCR_GATEWAY_FAILED",
                f"OCR Gateway 请求失败（HTTP {response.status_code}）",
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise PipelineError(
                "OCR_GATEWAY_INVALID_RESPONSE", "OCR Gateway 返回了无效 JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise PipelineError("OCR_GATEWAY_INVALID_RESPONSE", "OCR Gateway 返回格式错误")
        return payload


def normalize_ocr_response(
    payload: dict[str, Any],
    *,
    model: str,
    raw_path: Path,
    provider: str,
    frame_id: str | None = None,
    media_timestamp: float | None = None,
    language: str | None = None,
    elapsed_seconds: float | None = None,
) -> OcrResult:
    text = _clean_ocr_text(optional_text(payload.get("text")) or "")
    raw_blocks = payload.get("blocks")
    blocks: list[OcrBlock] = []
    if isinstance(raw_blocks, list):
        for item in raw_blocks:
            if not isinstance(item, dict):
                continue
            block_text = _clean_ocr_text(optional_text(item.get("text")) or "")
            if not block_text:
                continue
            raw_box = item.get("bounding_box", item.get("bbox"))
            box = (
                tuple(float(value) for value in raw_box)
                if isinstance(raw_box, list) and all(isinstance(value, (int, float)) for value in raw_box)
                else None
            )
            raw_polygon = item.get("polygon", item.get("poly"))
            polygon = _polygon_or_none(raw_polygon)
            confidence = float_or_none(item.get("confidence", item.get("score")))
            raw_order = item.get("reading_order", item.get("order"))
            reading_order = int(raw_order) if isinstance(raw_order, int) else None
            block_language = optional_text(item.get("language"))
            blocks.append(
                OcrBlock(
                    text=block_text,
                    bounding_box=box,
                    confidence=confidence,
                    polygon=polygon,
                    reading_order=reading_order,
                    language=block_language,
                    uncertain=bool(
                        item.get("uncertain")
                        or (confidence is not None and confidence < 0.5)
                    ),
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
        frame_id=optional_text(payload.get("frame_id")) or frame_id,
        media_timestamp=(
            float_or_none(payload.get("media_timestamp"))
            if payload.get("media_timestamp") is not None
            else media_timestamp
        ),
        language=optional_text(payload.get("language")) or language,
        elapsed_seconds=(
            float_or_none(payload.get("elapsed_seconds"))
            if payload.get("elapsed_seconds") is not None
            else elapsed_seconds
        ),
    )


async def _await_ocr_request(
    awaitable: Awaitable[httpx.Response],
    cancel_check: OcrCancelCheck | None,
) -> httpx.Response:
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            await _ensure_ocr_active(cancel_check)
            await asyncio.wait({task}, timeout=0.1)
        return await task
    except BaseException:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        raise


async def _ensure_ocr_active(cancel_check: OcrCancelCheck | None) -> None:
    if process_registry.shutting_down:
        raise PipelineInterrupted(stage="OCR_RECOGNIZE")
    if cancel_check is None:
        return
    cancelled = cancel_check()
    if inspect.isawaitable(cancelled):
        cancelled = await cancelled
    if cancelled:
        raise PipelineCancelled(stage="OCR_RECOGNIZE")


async def _cancel_aware_delay(
    seconds: float, cancel_check: OcrCancelCheck | None
) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await _ensure_ocr_active(cancel_check)
        await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _clean_ocr_text(value: str) -> str:
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _polygon_or_none(value: Any) -> tuple[tuple[float, float], ...] | None:
    if not isinstance(value, list):
        return None
    points: list[tuple[float, float]] = []
    for point in value:
        if (
            not isinstance(point, (list, tuple))
            or len(point) != 2
            or not all(isinstance(coordinate, (int, float)) for coordinate in point)
        ):
            return None
        points.append((float(point[0]), float(point[1])))
    return tuple(points) if len(points) >= 3 else None
