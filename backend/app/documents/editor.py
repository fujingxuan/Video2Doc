from __future__ import annotations

import asyncio
import difflib
import inspect
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from app.core.errors import PipelineCancelled, PipelineError
from app.documents.faithfulness import validate_block_edit
from app.documents.ir import Document
from app.providers.asr import redact_sensitive
from app.providers.gateways import trust_environment_proxy
from app.storage.workspace import write_json


EditCancelCheck = Callable[[], bool | Awaitable[bool]]
PROMPT_VERSION = "faithful-edit-v1"
FALLBACK_GATEWAY_ERRORS = {
    "TEXT_GATEWAY_TIMEOUT",
    "TEXT_GATEWAY_UNAVAILABLE",
    "TEXT_GATEWAY_AUTH_FAILED",
    "TEXT_GATEWAY_RETRYABLE",
    "TEXT_GATEWAY_FAILED",
}


class FaithfulTextEditor:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        prompt_path: Path | None = None,
        timeout_seconds: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = _chat_completions_endpoint(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.prompt_path = prompt_path or _default_prompt_path()

    async def edit(
        self,
        document: Document,
        *,
        raw_path: Path,
        minimum_length_ratio: float = 0.72,
        formula_symbol_ratio: float = 0.8,
        cancel_check: EditCancelCheck | None = None,
    ) -> tuple[Document, dict[str, Any]]:
        editable = [
            block
            for block in document.all_blocks()
            if block.block_type in {"transcript", "visual_text", "code", "quote"}
            and block.original_text.strip()
        ]
        if not editable:
            return document, {
                "prompt_version": PROMPT_VERSION,
                "model": self.model,
                "accepted": True,
                "reason": "no_editable_blocks",
                "block_runs": [],
            }
        await _ensure_active(cancel_check)
        started = time.monotonic()
        try:
            prompt = self.prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PipelineError(
                "TEXT_PROMPT_MISSING",
                "文本整理提示词资源缺失，请重新安装完整便携包",
                technical_detail=str(exc),
            ) from exc
        request_blocks = [
            {
                "block_id": block.block_id,
                "source_ids": [item.source_id for item in block.source_references],
                "start_time": block.timestamp.start_seconds,
                "end_time": block.timestamp.end_seconds,
                "block_type": block.block_type,
                "text": block.original_text,
            }
            for block in editable
        ]
        request_payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"prompt_version": PROMPT_VERSION, "blocks": request_blocks},
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        try:
            response_payload = await self._request(request_payload, cancel_check)
        except PipelineError as exc:
            if exc.code not in FALLBACK_GATEWAY_ERRORS:
                raise
            run = _rejected_run(
                self.model,
                raw_path,
                f"{exc.code}: {exc.user_message}",
                started,
            )
            run["error_code"] = exc.code
            run["raw_response_reference"] = None
            return document, run
        write_json(
            raw_path,
            {
                "schema_version": 1,
                "prompt_version": PROMPT_VERSION,
                "model": self.model,
                "response": redact_sensitive(response_payload),
            },
        )
        try:
            parsed = _response_json(response_payload)
        except PipelineError as exc:
            return document, _rejected_run(
                self.model,
                raw_path,
                exc.user_message,
                started,
            )
        returned = parsed.get("blocks")
        if not isinstance(returned, list):
            return document, _rejected_run(
                self.model,
                raw_path,
                "响应缺少 blocks 数组",
                started,
            )
        by_id = {
            str(item.get("block_id")): item
            for item in returned
            if isinstance(item, dict) and item.get("block_id")
        }
        if set(by_id) != {block.block_id for block in editable}:
            return document, _rejected_run(
                self.model,
                raw_path,
                "响应块 ID 不完整或包含额外块",
                started,
            )
        replacements: dict[str, str] = {}
        block_runs: list[dict[str, Any]] = []
        for block in editable:
            item = by_id[block.block_id]
            edited_text = item.get("edited_text")
            source_ids = item.get("source_ids")
            if not isinstance(edited_text, str) or not isinstance(source_ids, list):
                return document, _rejected_run(
                    self.model,
                    raw_path,
                    f"块 {block.block_id} 的字段格式无效",
                    started,
                )
            candidate = block.model_copy(update={"edited_text": edited_text})
            report = validate_block_edit(
                candidate,
                returned_source_ids=[str(value) for value in source_ids],
                minimum_length_ratio=minimum_length_ratio,
                formula_symbol_ratio=formula_symbol_ratio,
            )
            if not report.accepted:
                reason = "; ".join(issue.code for issue in report.issues)
                return document, _rejected_run(
                    self.model,
                    raw_path,
                    f"忠实性校验拒绝块 {block.block_id}: {reason}",
                    started,
                )
            replacements[block.block_id] = edited_text
            block_runs.append(
                {
                    "block_id": block.block_id,
                    "source_ids": source_ids,
                    "original_text": block.original_text,
                    "edited_text": edited_text,
                    "change_diff": _change_diff(block.original_text, edited_text),
                    "validation_metrics": report.metrics,
                }
            )
        edited_document = _replace_text(document, replacements)
        run = {
            "schema_version": 1,
            "prompt_version": PROMPT_VERSION,
            "model": self.model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "accepted": True,
            "fallback_used": False,
            "raw_response_reference": str(raw_path),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "block_runs": block_runs,
        }
        edited_document.edit_metadata = run
        return edited_document, run

    async def _request(
        self,
        payload: dict[str, Any],
        cancel_check: EditCancelCheck | None,
    ) -> dict[str, Any]:
        async def send() -> httpx.Response:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout_seconds),
                    transport=self.transport,
                    trust_env=trust_environment_proxy(self.endpoint),
                ) as client:
                    return await client.post(
                        self.endpoint,
                        headers=(
                            {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                        ),
                        json=payload,
                    )
            except httpx.TimeoutException as exc:
                raise PipelineError(
                    "TEXT_GATEWAY_TIMEOUT",
                    "文本整理服务请求超时",
                    retryable=True,
                ) from exc
            except httpx.NetworkError as exc:
                raise PipelineError(
                    "TEXT_GATEWAY_UNAVAILABLE",
                    "文本整理服务无法连接",
                    technical_detail=str(exc),
                    retryable=True,
                ) from exc

        task = asyncio.create_task(send())
        try:
            while not task.done():
                await _ensure_active(cancel_check)
                await asyncio.sleep(0.1)
            response = await task
        except BaseException:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, PipelineError):
                    pass
            raise
        if response.status_code in {401, 403}:
            raise PipelineError("TEXT_GATEWAY_AUTH_FAILED", "文本整理服务认证失败")
        if response.status_code == 429 or response.status_code >= 500:
            raise PipelineError(
                "TEXT_GATEWAY_RETRYABLE",
                f"文本整理服务暂时不可用（HTTP {response.status_code}）",
                retryable=True,
            )
        if response.status_code >= 400:
            raise PipelineError(
                "TEXT_GATEWAY_FAILED",
                f"文本整理服务请求失败（HTTP {response.status_code}）",
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise PipelineError(
                "TEXT_GATEWAY_INVALID_RESPONSE", "文本整理服务返回无效 JSON"
            ) from exc
        if not isinstance(data, dict):
            raise PipelineError("TEXT_GATEWAY_INVALID_RESPONSE", "文本整理服务返回格式错误")
        return data


def _replace_text(document: Document, replacements: dict[str, str]) -> Document:
    sections = []
    for section in document.sections:
        blocks = [
            block.model_copy(
                update={"edited_text": replacements.get(block.block_id, block.edited_text)}
            )
            for block in section.blocks
        ]
        sections.append(section.model_copy(update={"blocks": blocks}))
    return document.model_copy(update={"sections": sections})


def _response_json(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        content = payload["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise PipelineError(
            "TEXT_GATEWAY_INVALID_RESPONSE",
            "文本整理响应不是约定的 JSON 对象",
        ) from exc
    if not isinstance(parsed, dict):
        raise PipelineError("TEXT_GATEWAY_INVALID_RESPONSE", "文本整理响应格式错误")
    return parsed


def _rejected_run(
    model: str,
    raw_path: Path,
    reason: str,
    started: float,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "accepted": False,
        "fallback_used": True,
        "reason": reason,
        "raw_response_reference": str(raw_path),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "block_runs": [],
    }


def _change_diff(original: str, edited: str) -> list[str]:
    return list(
        difflib.unified_diff(
            original.splitlines(),
            edited.splitlines(),
            fromfile="original",
            tofile="edited",
            lineterm="",
        )
    )


async def _ensure_active(cancel_check: EditCancelCheck | None) -> None:
    if cancel_check is None:
        return
    value = cancel_check()
    if inspect.isawaitable(value):
        value = await value
    if value:
        raise PipelineCancelled(stage="EDIT_TEXT_OPTIONAL")


def _chat_completions_endpoint(base_url: str) -> str:
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


def _default_prompt_path() -> Path:
    candidates = [
        Path(__file__).resolve().parents[1] / "prompts" / "faithful_edit_v1.txt",
    ]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.insert(
            0,
            Path(bundle_root) / "app" / "prompts" / "faithful_edit_v1.txt",
        )
    return next((path for path in candidates if path.is_file()), candidates[0])
