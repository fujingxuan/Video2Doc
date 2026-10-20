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

import httpx

from app.core.errors import PipelineCancelled, PipelineError
from app.documents.faithfulness import validate_block_edit
from app.documents.ir import (
    Document,
    DocumentBlock,
    DocumentSection,
    SourceReference,
    TimestampAnchor,
)
from app.providers.asr import redact_sensitive
from app.providers.gateways import (
    openai_chat_completions_endpoint,
    openai_text_generation_options,
    trust_environment_proxy,
)
from app.storage.workspace import write_json


EditCancelCheck = Callable[[], bool | Awaitable[bool]]
PROMPT_VERSION = "faithful-article-v1"
ARTICLE_MODE = "full_article"
DEFAULT_TEXT_TIMEOUT_SECONDS = 600.0
FALLBACK_GATEWAY_ERRORS = {
    "TEXT_GATEWAY_TIMEOUT",
    "TEXT_GATEWAY_UNAVAILABLE",
    "TEXT_GATEWAY_AUTH_FAILED",
    "TEXT_GATEWAY_RETRYABLE",
    "TEXT_GATEWAY_FAILED",
    "TEXT_GATEWAY_INVALID_RESPONSE",
}


class FaithfulTextEditor:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        prompt_path: Path | None = None,
        timeout_seconds: float = DEFAULT_TEXT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = openai_chat_completions_endpoint(base_url)
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
                "mode": ARTICLE_MODE,
                "block_runs": [],
            }
        await _ensure_active(cancel_check)
        started = time.monotonic()
        source_text = _full_article_source(editable)
        fallback_document = build_full_article_document(document)
        try:
            prompt = self.prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PipelineError(
                "TEXT_PROMPT_MISSING",
                "文本整理提示词资源缺失，请重新安装完整便携包",
                technical_detail=str(exc),
            ) from exc
        request_payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": _article_max_tokens(source_text),
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "prompt_version": PROMPT_VERSION,
                            "title": document.title,
                            "source_text": source_text,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            **openai_text_generation_options(self.model),
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
            run["mode"] = ARTICLE_MODE
            run["source_block_count"] = len(editable)
            return fallback_document, run
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
            run = _rejected_run(
                self.model,
                raw_path,
                exc.user_message,
                started,
            )
            run["mode"] = ARTICLE_MODE
            run["source_block_count"] = len(editable)
            return fallback_document, run
        article_markdown = parsed.get("article_markdown")
        if not isinstance(article_markdown, str) or not article_markdown.strip():
            run = _rejected_run(
                self.model,
                raw_path,
                "响应缺少非空 article_markdown 字段",
                started,
            )
            run["mode"] = ARTICLE_MODE
            run["source_block_count"] = len(editable)
            return fallback_document, run
        article_markdown = article_markdown.strip()
        edited_document = build_full_article_document(
            document,
            article_markdown=article_markdown,
        )
        article_block = next(
            block for block in edited_document.all_blocks() if block.block_type != "image"
        )
        source_ids = [item.source_id for item in article_block.source_references]
        report = validate_block_edit(
            article_block,
            returned_source_ids=source_ids,
            minimum_length_ratio=minimum_length_ratio,
            formula_symbol_ratio=formula_symbol_ratio,
        )
        if not report.accepted:
            reason = "; ".join(f"{issue.code}（{issue.message}）" for issue in report.issues)
            run = _rejected_run(
                self.model,
                raw_path,
                f"忠实性校验拒绝整篇文章: {reason}",
                started,
            )
            run["mode"] = ARTICLE_MODE
            run["source_block_count"] = len(editable)
            return fallback_document, run
        run = {
            "schema_version": 1,
            "prompt_version": PROMPT_VERSION,
            "mode": ARTICLE_MODE,
            "model": self.model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "accepted": True,
            "fallback_used": False,
            "raw_response_reference": str(raw_path),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "source_block_count": len(editable),
            "article_source_count": len(source_ids),
            "block_runs": [
                {
                    "block_id": article_block.block_id,
                    "source_ids": source_ids,
                    "original_text": source_text,
                    "edited_text": article_markdown,
                    "change_diff": _change_diff(source_text, article_markdown),
                    "validation_metrics": report.metrics,
                }
            ],
        }
        edited_document.edit_metadata = run
        return edited_document, run

    async def _request(
        self,
        payload: dict[str, Any],
        cancel_check: EditCancelCheck | None,
    ) -> dict[str, Any]:
        async def send() -> dict[str, Any]:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        self.timeout_seconds,
                        connect=min(30.0, self.timeout_seconds),
                    ),
                    transport=self.transport,
                    trust_env=trust_environment_proxy(self.endpoint),
                ) as client:
                    result = await _send_chat_completion(
                        client,
                        self.endpoint,
                        payload,
                        api_key=self.api_key,
                        streaming=True,
                    )
                    if result is not None:
                        return result
                    fallback = await _send_chat_completion(
                        client,
                        self.endpoint,
                        payload,
                        api_key=self.api_key,
                        streaming=False,
                    )
                    if fallback is None:  # pragma: no cover - non-stream requests never retry.
                        raise PipelineError(
                            "TEXT_GATEWAY_FAILED",
                            "文本整理服务不支持当前请求模式",
                        )
                    return fallback
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
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while not task.done():
                await _ensure_active(cancel_check)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PipelineError(
                        "TEXT_GATEWAY_TIMEOUT",
                        f"文本整理服务超过 {self.timeout_seconds:g} 秒仍未完成",
                        retryable=True,
                    )
                await asyncio.sleep(min(0.1, remaining))
            response = await task
        except BaseException:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, PipelineError):
                    pass
            raise
        return response


def build_full_article_document(
    document: Document,
    *,
    article_markdown: str | None = None,
) -> Document:
    editable = [
        block
        for block in document.all_blocks()
        if block.block_type in {"transcript", "visual_text", "code", "quote"}
        and block.original_text.strip()
    ]
    if not editable:
        return document.model_copy(deep=True)
    source_text = _full_article_source(editable)
    source_references = _unique_source_references(editable)
    all_blocks = document.all_blocks()
    starts = [
        block.timestamp.start_seconds
        for block in all_blocks
        if block.timestamp.start_seconds is not None
    ]
    ends = [
        block.timestamp.end_seconds
        for block in all_blocks
        if block.timestamp.end_seconds is not None
    ]
    timestamp = TimestampAnchor(
        start_seconds=min(starts) if starts else None,
        end_seconds=max(ends or starts) if (ends or starts) else None,
    )
    article_block = DocumentBlock(
        block_id="article-000001",
        block_type="transcript",
        timestamp=timestamp,
        original_text=source_text,
        edited_text=article_markdown.strip() if article_markdown else None,
        source_references=source_references,
        uncertain=any(block.uncertain for block in editable),
        metadata={
            "article_mode": ARTICLE_MODE,
            "original_block_ids": [block.block_id for block in editable],
            "source_block_count": len(editable),
        },
    )
    images = [block.model_copy(deep=True) for block in all_blocks if block.block_type == "image"]
    article_section = DocumentSection(
        section_id="article-section-0001",
        title="完整正文",
        timestamp=timestamp,
        blocks=[article_block, *images],
    )
    processing = {**document.processing, "article_mode": ARTICLE_MODE}
    return document.model_copy(
        deep=True,
        update={"sections": [article_section], "processing": processing},
    )


def _full_article_source(blocks: list[DocumentBlock]) -> str:
    return "\n".join(block.original_text.strip() for block in blocks).strip()


def _unique_source_references(blocks: list[DocumentBlock]) -> list[SourceReference]:
    references: list[SourceReference] = []
    seen: set[str] = set()
    for block in blocks:
        for reference in block.source_references:
            if reference.source_id in seen:
                continue
            seen.add(reference.source_id)
            references.append(reference.model_copy(deep=True))
    return references


def _response_json(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PipelineError(
            "TEXT_GATEWAY_INVALID_RESPONSE",
            "文本整理响应不是约定的 JSON 对象",
        ) from exc
    content = _message_text(content)
    if not content.strip():
        stream = payload.get("_video2doc_stream")
        reasoning_characters = (
            int(stream.get("reasoning_characters") or 0)
            if isinstance(stream, dict)
            else 0
        )
        detail = (
            "模型只返回了思考过程，没有生成文章正文"
            if reasoning_characters
            else "模型没有生成文章正文"
        )
        raise PipelineError("TEXT_GATEWAY_INVALID_RESPONSE", detail)
    parsed = _parse_json_object(content)
    if parsed is None:
        # Several OpenAI-compatible relays ignore response_format and return
        # the requested Markdown directly. The normal faithfulness gate still
        # rejects shortened or altered articles after this compatibility path.
        return {"article_markdown": content.strip()}
    if not isinstance(parsed, dict):
        raise PipelineError("TEXT_GATEWAY_INVALID_RESPONSE", "文本整理响应格式错误")
    return parsed


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text"))
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return ""


def _parse_json_object(content: str) -> dict[str, Any] | None:
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        opening = candidate.find("{")
        if opening < 0:
            return None
        try:
            parsed, _end = decoder.raw_decode(candidate[opening:])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _article_max_tokens(source_text: str) -> int:
    return max(4096, min(32768, len(source_text) * 2))


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


def _raise_gateway_status(response: httpx.Response) -> None:
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


async def _send_chat_completion(
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    streaming: bool,
) -> dict[str, Any] | None:
    request_payload = {**payload, "stream": True} if streaming else payload
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with client.stream(
        "POST",
        endpoint,
        headers=headers,
        json=request_payload,
    ) as response:
        if streaming and response.status_code in {400, 422}:
            await response.aread()
            if _streaming_not_supported(response):
                return None
        _raise_gateway_status(response)
        content_type = response.headers.get("content-type", "").casefold()
        if "text/event-stream" in content_type:
            return await _consume_chat_completion_stream(response)
        await response.aread()
        return _json_response_payload(response)


def _streaming_not_supported(response: httpx.Response) -> bool:
    detail = response.text.casefold()
    return "stream" in detail and any(
        marker in detail for marker in ("unsupported", "not support", "not allowed", "不支持")
    )


def _json_response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise PipelineError("TEXT_GATEWAY_INVALID_RESPONSE", "文本整理服务返回无效 JSON") from exc
    if not isinstance(data, dict):
        raise PipelineError("TEXT_GATEWAY_INVALID_RESPONSE", "文本整理服务返回格式错误")
    return data


async def _consume_chat_completion_stream(response: httpx.Response) -> dict[str, Any]:
    content_parts: list[str] = []
    event_count = 0
    reasoning_characters = 0
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        event_data = line.removeprefix("data:").strip()
        if not event_data or event_data == "[DONE]":
            continue
        event_count += 1
        try:
            event = json.loads(event_data)
        except json.JSONDecodeError as exc:
            raise PipelineError(
                "TEXT_GATEWAY_INVALID_RESPONSE",
                "文本整理服务返回了无效的流式 JSON",
            ) from exc
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        if event.get("error"):
            raise PipelineError("TEXT_GATEWAY_FAILED", "文本整理服务流式响应报告错误")
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            continue
        choice = choices[0]
        if isinstance(choice.get("finish_reason"), str):
            finish_reason = choice["finish_reason"]
        message = choice.get("delta") or choice.get("message") or {}
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str):
            reasoning_characters += len(reasoning)
        if isinstance(content, str) and content:
            content_parts.append(content)
        elif isinstance(content, list):
            content_parts.extend(
                str(part.get("text"))
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
    if event_count == 0:
        raise PipelineError(
            "TEXT_GATEWAY_INVALID_RESPONSE",
            "文本整理服务未返回有效的流式内容",
        )
    result: dict[str, Any] = {
        "choices": [
            {
                "message": {"content": "".join(content_parts)},
                "finish_reason": finish_reason,
            }
        ],
        "_video2doc_stream": {
            "event_count": event_count,
            "reasoning_characters": reasoning_characters,
        },
    }
    if usage is not None:
        result["usage"] = usage
    return result


def _default_prompt_path() -> Path:
    candidates = [
        Path(__file__).resolve().parents[1] / "prompts" / "faithful_article_v1.txt",
    ]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.insert(
            0,
            Path(bundle_root) / "app" / "prompts" / "faithful_article_v1.txt",
        )
    return next((path for path in candidates if path.is_file()), candidates[0])
