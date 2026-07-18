from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.errors import PipelineCancelled, PipelineError
from app.providers.process import CancelCheck, ExternalProcessRunner, process_registry
from app.storage.workspace import WorkspacePaths, write_json


ProgressCallback = Callable[[float | None, str], None]


@dataclass(frozen=True, slots=True)
class AudioChunk:
    index: int
    start_time: float
    end_time: float
    path: str

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


@dataclass(frozen=True, slots=True)
class AsrRequest:
    chunk: AudioChunk
    model: str
    language: str | None = None


@dataclass(frozen=True, slots=True)
class AsrSegment:
    segment_id: str
    start_time: float | None
    end_time: float | None
    text: str
    confidence: float | None
    language: str | None
    provider: str
    model: str
    chunk_index: int
    raw_response_reference: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AsrResult:
    provider: str
    model: str
    language: str | None
    chunk_index: int
    raw_response_reference: str
    segments: tuple[AsrSegment, ...]


class AudioChunker:
    def __init__(
        self,
        ffmpeg_executable: str,
        runner: ExternalProcessRunner | None = None,
    ) -> None:
        self.ffmpeg_executable = ffmpeg_executable
        self.runner = runner or ExternalProcessRunner()

    async def split(
        self,
        audio_path: Path,
        duration: float | None,
        paths: WorkspacePaths,
        *,
        chunk_seconds: float,
        cancel_check: CancelCheck,
        progress: ProgressCallback,
    ) -> list[AudioChunk]:
        if duration is None or duration <= 0:
            raise PipelineError("AUDIO_EXTRACTION_FAILED", "无法确定音频时长，不能安全分块")
        paths.chunks.mkdir(parents=True, exist_ok=True)
        # Retry always rebuilds the deterministic chunk set; raw ASR responses remain attempt-scoped.
        for stale in paths.chunks.glob("chunk_*.wav"):
            stale.unlink(missing_ok=True)
        count = max(1, math.ceil(duration / chunk_seconds))
        chunks: list[AudioChunk] = []
        for index in range(count):
            start = index * chunk_seconds
            end = min(duration, start + chunk_seconds)
            destination = paths.chunks / f"chunk_{index:04d}.wav"
            partial = paths.chunks / f"chunk_{index:04d}.part.wav"
            if count == 1:
                shutil.copy2(audio_path, partial)
            else:
                try:
                    await self.runner.run(
                        [
                            self.ffmpeg_executable,
                            "-y",
                            "-ss",
                            f"{start:.6f}",
                            "-t",
                            f"{end - start:.6f}",
                            "-i",
                            str(audio_path),
                            "-vn",
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            "-c:a",
                            "pcm_s16le",
                            str(partial),
                        ],
                        cwd=paths.root,
                        log_dir=paths.logs,
                        log_name=f"ffmpeg_chunk_{index:04d}",
                        timeout_seconds=get_settings().media_process_timeout_seconds,
                        cancel_check=cancel_check,
                    )
                except PipelineError as exc:
                    partial.unlink(missing_ok=True)
                    if exc.code == "PROCESS_UNAVAILABLE":
                        raise PipelineError(
                            "FFMPEG_UNAVAILABLE", "找不到 FFmpeg，无法分割音频"
                        ) from exc
                    raise
            if not partial.is_file() or partial.stat().st_size <= 44:
                partial.unlink(missing_ok=True)
                raise PipelineError("AUDIO_EXTRACTION_FAILED", f"音频分块 {index} 不完整")
            partial.replace(destination)
            chunks.append(AudioChunk(index, start, end, str(destination)))
            progress((index + 1) / count, f"已生成 {index + 1}/{count} 个音频分块")
        write_json(
            paths.chunks / "manifest.json",
            {
                "schema_version": 1,
                "strategy": "fixed_duration_pcm_wav",
                "chunk_seconds": chunk_seconds,
                "overlap_seconds": 0,
                "chunks": [asdict(chunk) for chunk in chunks],
            },
        )
        return chunks


class OpenAICompatibleAsrProvider:
    name = "oneapi-openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = transcription_endpoint(base_url)
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.transport = transport

    async def transcribe(
        self,
        request: AsrRequest,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
    ) -> AsrResult:
        raw_path = paths.asr_raw / f"chunk_{request.chunk.index:04d}.json"
        data: dict[str, str] = {
            "model": request.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        if request.language:
            data["language"] = request.language
        attempt = 0
        while True:
            try:
                with Path(request.chunk.path).open("rb") as stream:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(self.timeout_seconds), transport=self.transport
                    ) as client:
                        response = await self._post_cancellable(
                            client,
                            data=data,
                            files={"file": (Path(request.chunk.path).name, stream, "audio/wav")},
                            cancel_check=cancel_check,
                        )
                if response.status_code in {401, 403}:
                    raise PipelineError("ASR_AUTH_FAILED", "ASR 认证失败，请检查 OneAPI 密钥")
                if response.status_code == 429:
                    raise _RetryableHttpError("ASR_RATE_LIMITED", response)
                if response.status_code >= 500:
                    raise _RetryableHttpError("ASR_PROVIDER_FAILED", response)
                if response.status_code >= 400:
                    raise PipelineError(
                        "ASR_PROVIDER_FAILED",
                        f"ASR 请求失败（HTTP {response.status_code}）",
                        technical_detail=safe_response_detail(response),
                    )
                try:
                    payload = response.json()
                except json.JSONDecodeError as exc:
                    raise PipelineError(
                        "ASR_PROVIDER_FAILED",
                        "ASR 返回了无法解析的 JSON",
                        technical_detail=str(exc),
                    ) from exc
                if not isinstance(payload, dict):
                    raise PipelineError("ASR_PROVIDER_FAILED", "ASR 返回格式不是 JSON 对象")
                write_json(raw_path, redact_sensitive(payload))
                return normalize_asr_response(payload, request, raw_path, self.name)
            except PipelineCancelled:
                raise
            except httpx.TimeoutException as exc:
                failure: PipelineError | _RetryableHttpError = _RetryableHttpError(
                    "ASR_TIMEOUT", None, detail=str(exc)
                )
            except httpx.NetworkError as exc:
                failure = _RetryableHttpError("ASR_PROVIDER_FAILED", None, detail=str(exc))
            except _RetryableHttpError as exc:
                failure = exc
            if attempt >= self.max_retries:
                raise PipelineError(
                    failure.code,
                    "ASR 请求超时" if failure.code == "ASR_TIMEOUT" else "ASR 服务暂时不可用",
                    technical_detail=failure.detail,
                    retryable=True,
                )
            await cancellable_backoff(2**attempt, cancel_check)
            attempt += 1

    async def _post_cancellable(
        self,
        client: httpx.AsyncClient,
        *,
        data: dict[str, str],
        files: dict[str, tuple[str, Any, str]],
        cancel_check: CancelCheck,
    ) -> httpx.Response:
        task = asyncio.create_task(
            client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files=files,
            )
        )
        while not task.done():
            if process_registry.shutting_down:
                from app.core.errors import PipelineInterrupted

                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise PipelineInterrupted(stage="ASR_TRANSCRIBE")
            if await check_cancelled(cancel_check):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise PipelineCancelled(stage="ASR_TRANSCRIBE")
            await asyncio.sleep(get_settings().process_poll_seconds)
        return await task


class _RetryableHttpError(Exception):
    def __init__(
        self, code: str, response: httpx.Response | None, *, detail: str | None = None
    ) -> None:
        self.code = code
        self.detail = detail or (safe_response_detail(response) if response else None)


def normalize_asr_response(
    payload: dict[str, Any], request: AsrRequest, raw_path: Path, provider: str
) -> AsrResult:
    language = optional_text(payload.get("language")) or request.language
    raw_segments = payload.get("segments")
    segments: list[AsrSegment] = []
    if isinstance(raw_segments, list):
        for index, raw in enumerate(raw_segments):
            if not isinstance(raw, dict):
                continue
            text = optional_text(raw.get("text")) or ""
            if not text.strip():
                continue
            segments.append(
                AsrSegment(
                    segment_id=f"c{request.chunk.index:04d}-s{index:06d}",
                    start_time=float_or_none(raw.get("start")),
                    end_time=float_or_none(raw.get("end")),
                    text=text.strip(),
                    confidence=float_or_none(raw.get("confidence")),
                    language=optional_text(raw.get("language")) or language,
                    provider=provider,
                    model=request.model,
                    chunk_index=request.chunk.index,
                    raw_response_reference=str(raw_path),
                )
            )
    elif optional_text(payload.get("text")):
        segments.append(
            AsrSegment(
                segment_id=f"c{request.chunk.index:04d}-s000000",
                start_time=None,
                end_time=None,
                text=str(payload["text"]).strip(),
                confidence=None,
                language=language,
                provider=provider,
                model=request.model,
                chunk_index=request.chunk.index,
                raw_response_reference=str(raw_path),
            )
        )
    if not segments:
        raise PipelineError("ASR_PROVIDER_FAILED", "ASR 未返回任何可用转写文本")
    return AsrResult(
        provider=provider,
        model=request.model,
        language=language,
        chunk_index=request.chunk.index,
        raw_response_reference=str(raw_path),
        segments=tuple(segments),
    )


def combine_asr_results(
    results: list[AsrResult], chunks: list[AudioChunk]
) -> list[AsrSegment]:
    offsets = {chunk.index: chunk.start_time for chunk in chunks}
    combined: list[AsrSegment] = []
    for result in sorted(results, key=lambda item: item.chunk_index):
        offset = offsets[result.chunk_index]
        for segment in result.segments:
            combined.append(
                AsrSegment(
                    segment_id=f"segment-{len(combined) + 1:06d}",
                    start_time=(segment.start_time + offset) if segment.start_time is not None else None,
                    end_time=(segment.end_time + offset) if segment.end_time is not None else None,
                    text=segment.text,
                    confidence=segment.confidence,
                    language=segment.language,
                    provider=segment.provider,
                    model=segment.model,
                    chunk_index=segment.chunk_index,
                    raw_response_reference=segment.raw_response_reference,
                )
            )
    return combined


def validate_segments(segments: list[AsrSegment]) -> None:
    if not segments:
        raise PipelineError("ASR_PARTIAL_FAILURE", "没有可保存的 ASR 分段")
    previous_start = -1.0
    for segment in segments:
        if not segment.text.strip():
            raise PipelineError("ASR_PARTIAL_FAILURE", "ASR 结果包含空文本分段")
        if segment.start_time is None or segment.end_time is None:
            continue
        if segment.start_time < 0 or segment.end_time < segment.start_time:
            raise PipelineError("ASR_PARTIAL_FAILURE", "ASR 返回了无效时间戳")
        if segment.start_time < previous_start:
            raise PipelineError("ASR_PARTIAL_FAILURE", "ASR 分段顺序不稳定")
        previous_start = segment.start_time


def save_transcript_artifacts(
    paths: WorkspacePaths,
    segments: list[AsrSegment],
    *,
    provider: str,
    model: str,
    language: str | None,
    chunk_count: int,
) -> dict[str, str]:
    normalized_path = paths.asr / "normalized.json"
    raw_index_path = paths.asr / "raw.json"
    text_path = paths.asr / "raw.txt"
    srt_path = paths.asr / "raw.srt"
    preview_path = paths.asr / "preview.json"
    payload = {
        "schema_version": 1,
        "provider": provider,
        "model": model,
        "language": language,
        "chunk_count": chunk_count,
        "segments": [segment.to_dict() for segment in segments],
    }
    write_json(normalized_path, payload)
    write_json(
        raw_index_path,
        {
            **payload,
            "provider_responses": sorted(
                {segment.raw_response_reference for segment in segments}
            ),
        },
    )
    text_path.write_text("\n".join(segment.text for segment in segments) + "\n", encoding="utf-8")
    srt_blocks: list[str] = []
    for segment in segments:
        if segment.start_time is None or segment.end_time is None:
            continue
        srt_blocks.append(
            f"{len(srt_blocks) + 1}\n{format_srt_time(segment.start_time)} --> "
            f"{format_srt_time(segment.end_time)}\n{segment.text}\n"
        )
    srt_path.write_text("\n".join(srt_blocks), encoding="utf-8")
    preview_text = "\n".join(segment.text for segment in segments)
    write_json(
        preview_path,
        {
            "provider": provider,
            "model": model,
            "language": language,
            "chunk_count": chunk_count,
            "completed_chunks": chunk_count,
            "segment_count": len(segments),
            "text": preview_text[:20_000],
            "truncated": len(preview_text) > 20_000,
        },
    )
    return {
        "normalized": str(normalized_path),
        "raw": str(raw_index_path),
        "text": str(text_path),
        "srt": str(srt_path),
        "preview": str(preview_path),
    }


def transcription_endpoint(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        raise PipelineError("ASR_CONFIG_INVALID", "未配置 OneAPI Base URL")
    if not re.match(r"^https?://", value, re.IGNORECASE):
        raise PipelineError("ASR_CONFIG_INVALID", "OneAPI Base URL 必须使用 http 或 https")
    parsed = urlparse(value)
    if parsed.username or parsed.password:
        raise PipelineError("ASR_CONFIG_INVALID", "OneAPI Base URL 中不能包含用户名或密码")
    if value.endswith("/audio/transcriptions"):
        return value
    return f"{value}/audio/transcriptions" if value.endswith("/v1") else f"{value}/v1/audio/transcriptions"


def load_asr_credentials(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    config = snapshot.get("asr", {})
    base_url = str(config.get("base_url") or os.getenv("ONEAPI_BASE_URL", "")).strip()
    model = str(config.get("model") or "").strip()
    api_key = os.getenv("ONEAPI_API_KEY", "").strip()
    if not base_url or not model or not api_key:
        missing = [
            name
            for name, value in (
                ("OneAPI Base URL", base_url),
                ("ASR 模型", model),
                ("ONEAPI_API_KEY", api_key),
            )
            if not value
        ]
        raise PipelineError("ASR_CONFIG_INVALID", f"ASR 配置不完整：{', '.join(missing)}")
    return base_url, api_key, model


async def cancellable_backoff(seconds: float, cancel_check: CancelCheck) -> None:
    elapsed = 0.0
    while elapsed < seconds:
        if process_registry.shutting_down:
            from app.core.errors import PipelineInterrupted

            raise PipelineInterrupted(stage="ASR_TRANSCRIBE")
        if await check_cancelled(cancel_check):
            raise PipelineCancelled(stage="ASR_TRANSCRIBE")
        step = min(get_settings().process_poll_seconds, seconds - elapsed)
        await asyncio.sleep(step)
        elapsed += step


async def check_cancelled(cancel_check: CancelCheck) -> bool:
    import inspect

    result = cancel_check()
    return bool(await result) if inspect.isawaitable(result) else bool(result)


def safe_response_detail(response: httpx.Response | None) -> str | None:
    if response is None:
        return None
    text = response.text[:1000]
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]?\s*[^\s,\"]+", r"\1:***", text)
    return f"HTTP {response.status_code}: {text}"


def redact_sensitive(value: Any) -> Any:
    sensitive = {"authorization", "api_key", "apikey", "token", "access_token", "secret"}
    if isinstance(value, dict):
        return {
            key: "***" if str(key).lower() in sensitive else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def format_srt_time(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def optional_text(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None
