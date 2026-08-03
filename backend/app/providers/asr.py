from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import unicodedata
import uuid
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets

from app.core.config import get_settings
from app.core.credentials import resolve_gateway_credential
from app.core.secrets import SecretStorageError
from app.core.errors import PipelineCancelled, PipelineError
from app.providers.process import CancelCheck, ExternalProcessRunner, process_registry
from app.providers.gateways import gateway_endpoint, trust_environment_proxy
from app.storage.workspace import WorkspacePaths, write_json


ProgressCallback = Callable[[float | None, str], None]


@dataclass(frozen=True, slots=True)
class AudioChunk:
    index: int
    start_time: float
    end_time: float
    path: str
    overlap_before_seconds: float = 0.0
    overlap_after_seconds: float = 0.0
    provider: str | None = None
    model: str | None = None
    attempt: int = 1
    status: str = "READY"

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.index,
            "source_start_seconds": self.start_time,
            "source_end_seconds": self.end_time,
            "overlap_before_seconds": self.overlap_before_seconds,
            "overlap_after_seconds": self.overlap_after_seconds,
            "audio_path": self.path,
            "provider": self.provider,
            "model": self.model,
            "attempt": self.attempt,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class AsrRequest:
    chunk: AudioChunk
    model: str
    language: str | None = None
    timestamp_granularity: str = "word"
    prompt: str | None = None
    task: str = "transcribe"
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AsrWord:
    start_time: float | None
    end_time: float | None
    text: str
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    words: tuple[AsrWord, ...] = ()

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
        overlap_seconds: float = 5.0,
        provider: str | None = None,
        model: str | None = None,
        attempt: int = 1,
        cancel_check: CancelCheck,
        progress: ProgressCallback,
    ) -> list[AudioChunk]:
        if duration is None or duration <= 0:
            raise PipelineError("AUDIO_EXTRACTION_FAILED", "无法确定音频时长，不能安全分块")
        paths.chunks.mkdir(parents=True, exist_ok=True)
        # Retry always rebuilds the deterministic chunk set; raw ASR responses remain attempt-scoped.
        for stale in paths.chunks.glob("chunk_*.wav"):
            stale.unlink(missing_ok=True)
        if chunk_seconds <= 0:
            raise PipelineError("ASR_CONFIG_INVALID", "ASR 分块长度必须大于 0")
        overlap_seconds = max(0.0, min(float(overlap_seconds), chunk_seconds - 0.001))
        step_seconds = chunk_seconds - overlap_seconds
        starts = [0.0]
        while starts[-1] + chunk_seconds < duration:
            starts.append(starts[-1] + step_seconds)
        count = len(starts)
        chunks: list[AudioChunk] = []
        for index, start in enumerate(starts):
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
            chunks.append(
                AudioChunk(
                    index,
                    start,
                    end,
                    str(destination),
                    overlap_before_seconds=min(overlap_seconds, start) if index else 0.0,
                    overlap_after_seconds=overlap_seconds if end < duration else 0.0,
                    provider=provider,
                    model=model,
                    attempt=attempt,
                )
            )
            progress((index + 1) / count, f"已生成 {index + 1}/{count} 个音频分块")
        write_json(
            paths.chunks / "manifest.json",
            {
                "schema_version": 2,
                "strategy": "capability_driven_overlapping_pcm_wav",
                "chunk_seconds": chunk_seconds,
                "step_seconds": step_seconds,
                "overlap_seconds": overlap_seconds,
                "chunks": [chunk.to_manifest_dict() for chunk in chunks],
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
        self.trust_env = trust_environment_proxy(self.endpoint)

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
            "timestamp_granularities[]": request.timestamp_granularity,
            "task": request.task,
        }
        if request.language:
            data["language"] = request.language
        if request.prompt:
            data["prompt"] = request.prompt
        attempt = 0
        while True:
            try:
                with Path(request.chunk.path).open("rb") as stream:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(self.timeout_seconds),
                        transport=self.transport,
                        trust_env=self.trust_env,
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
        cancel_endpoint: str | None = None,
        request_id: str | None = None,
    ) -> httpx.Response:
        task = asyncio.create_task(
            client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
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
                if cancel_endpoint and request_id:
                    try:
                        await asyncio.wait_for(
                            client.post(f"{cancel_endpoint}/{request_id}"), timeout=2.0
                        )
                    except (Exception, asyncio.CancelledError):
                        # Closing the upload already prevents accepting a late result. The
                        # explicit cancellation call is best effort for local inference workers.
                        pass
                raise PipelineCancelled(stage="ASR_TRANSCRIBE")
            await asyncio.sleep(get_settings().process_poll_seconds)
        return await task


class UnifiedAsrGatewayProvider(OpenAICompatibleAsrProvider):
    name = "video2doc-asr-http-v1"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            transport=transport,
        )
        self.base_url = base_url
        self.endpoint = gateway_endpoint(base_url, "/v1/asr/transcriptions")

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
            "timestamp_granularity": request.timestamp_granularity,
            "task": request.task,
        }
        if request.language:
            data["language"] = request.language
        if request.prompt:
            data["prompt"] = request.prompt
        if request.request_id:
            data["request_id"] = request.request_id
        attempt = 0
        while True:
            try:
                with Path(request.chunk.path).open("rb") as stream:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(self.timeout_seconds),
                        transport=self.transport,
                        trust_env=self.trust_env,
                    ) as client:
                        response = await self._post_cancellable(
                            client,
                            data=data,
                            files={"file": (Path(request.chunk.path).name, stream, "audio/wav")},
                            cancel_check=cancel_check,
                            cancel_endpoint=gateway_endpoint(
                                self.base_url, "/v1/asr/cancellations"
                            ),
                            request_id=request.request_id,
                        )
                if response.status_code in {401, 403}:
                    raise PipelineError("ASR_GATEWAY_AUTH_FAILED", "ASR Gateway 认证失败")
                if response.status_code == 429:
                    raise _RetryableHttpError("ASR_GATEWAY_RATE_LIMITED", response)
                if response.status_code >= 500:
                    raise _RetryableHttpError("ASR_GATEWAY_UNAVAILABLE", response)
                if response.status_code >= 400:
                    raise PipelineError(
                        "ASR_GATEWAY_FAILED",
                        f"ASR Gateway 请求失败（HTTP {response.status_code}）",
                        technical_detail=safe_response_detail(response),
                    )
                payload = response.json()
                if not isinstance(payload, dict):
                    raise PipelineError("ASR_GATEWAY_INVALID_RESPONSE", "ASR Gateway 返回格式错误")
                write_json(raw_path, redact_sensitive(payload))
                provider = optional_text(payload.get("provider")) or self.name
                return normalize_asr_response(payload, request, raw_path, provider)
            except PipelineCancelled:
                raise
            except (httpx.TimeoutException, TimeoutError) as exc:
                failure: PipelineError | _RetryableHttpError = _RetryableHttpError(
                    "ASR_GATEWAY_TIMEOUT", None, detail=str(exc)
                )
            except (httpx.NetworkError, json.JSONDecodeError) as exc:
                failure = _RetryableHttpError(
                    "ASR_GATEWAY_INVALID_RESPONSE"
                    if isinstance(exc, json.JSONDecodeError)
                    else "ASR_GATEWAY_UNAVAILABLE",
                    None,
                    detail=str(exc),
                )
            except _RetryableHttpError as exc:
                failure = exc
            if attempt >= self.max_retries:
                raise PipelineError(
                    failure.code,
                    "ASR Gateway 请求超时"
                    if failure.code == "ASR_GATEWAY_TIMEOUT"
                    else "ASR Gateway 暂时不可用",
                    technical_detail=failure.detail,
                    retryable=True,
                )
            await cancellable_backoff(2**attempt, cancel_check)
            attempt += 1


class DashScopeFunAsrProvider:
    name = "dashscope-fun-asr"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        workspace_id: str | None,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.workspace_id = workspace_id
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def transcribe_stream(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
        paths: WorkspacePaths,
        cancel_check: CancelCheck,
    ) -> AsrResult:
        raw_path = paths.asr_raw / "chunk_0000.json"
        attempt = 0
        while True:
            try:
                sentences, events = await self._run_session(
                    audio_path,
                    model=model,
                    language=language,
                    cancel_check=cancel_check,
                )
                payload = {
                    "events": redact_sensitive(events),
                    "final_sentences": redact_sensitive(sentences),
                }
                write_json(raw_path, payload)
                return normalize_dashscope_response(
                    payload,
                    AsrRequest(AudioChunk(0, 0.0, 0.0, str(audio_path)), model, language),
                    raw_path,
                    self.name,
                )
            except PipelineCancelled:
                raise
            except PipelineError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                failure = exc
            except Exception as exc:
                failure = _dashscope_connection_error(exc)
                if attempt >= self.max_retries:
                    raise failure from exc
            await cancellable_backoff(2**attempt, cancel_check)
            attempt += 1

    async def _run_session(
        self,
        audio_path: Path,
        *,
        model: str,
        language: str | None,
        cancel_check: CancelCheck,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with wave.open(str(audio_path), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                raise PipelineError("DASHSCOPE_INVALID_RESPONSE", "Fun-ASR 输入必须是 PCM S16LE 16kHz 单声道 WAV")
        headers = {"Authorization": f"Bearer {self.api_key}", "user-agent": "Video2Doc/0.3"}
        if self.workspace_id:
            headers["X-DashScope-WorkSpace"] = self.workspace_id
        task_id = str(uuid.uuid4())
        parameters: dict[str, Any] = {
            "format": "pcm",
            "sample_rate": 16000,
            "semantic_punctuation_enabled": False,
            "heartbeat": True,
        }
        if language:
            parameters["language_hints"] = [language]
        run_task = {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": model,
                "parameters": parameters,
                "input": {},
            },
        }
        events: list[dict[str, Any]] = []
        sentences: list[dict[str, Any]] = []
        task_started = False
        try:
            async with websockets.connect(
                self.base_url,
                additional_headers=headers,
                open_timeout=self.timeout_seconds,
                close_timeout=2,
                max_size=8 * 1024 * 1024,
            ) as socket:
                await socket.send(json.dumps(run_task, ensure_ascii=False))
                await self._wait_for_event(socket, "task-started", events, sentences)
                task_started = True
                receiver = asyncio.create_task(
                    self._wait_for_event(socket, "task-finished", events, sentences)
                )
                try:
                    with wave.open(str(audio_path), "rb") as wav:
                        frame_count = 1600
                        while True:
                            if process_registry.shutting_down:
                                from app.core.errors import PipelineInterrupted

                                raise PipelineInterrupted(stage="ASR_TRANSCRIBE")
                            if await check_cancelled(cancel_check):
                                raise PipelineCancelled(stage="ASR_TRANSCRIBE")
                            frame = wav.readframes(frame_count)
                            if not frame:
                                break
                            await socket.send(frame)
                            await asyncio.sleep(len(frame) / (16000 * 2))
                    await socket.send(
                        json.dumps(
                            {
                                "header": {
                                    "action": "finish-task",
                                    "task_id": task_id,
                                    "streaming": "duplex",
                                },
                                "payload": {"input": {}},
                            }
                        )
                    )
                    await asyncio.wait_for(receiver, timeout=self.timeout_seconds)
                except BaseException:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                    raise
        except PipelineCancelled:
            raise
        except PipelineError:
            raise
        except asyncio.TimeoutError as exc:
            raise PipelineError(
                "DASHSCOPE_TIMEOUT", "Fun-ASR 等待服务响应超时", technical_detail=str(exc), retryable=True
            ) from exc
        except Exception as exc:
            failure = _dashscope_connection_error(exc)
            if task_started and failure.code == "DASHSCOPE_CONNECTION_FAILED":
                failure = PipelineError(
                    "DASHSCOPE_STREAM_INTERRUPTED",
                    "Fun-ASR 音频流连接中断",
                    technical_detail=failure.technical_detail,
                    retryable=True,
                )
            raise failure from exc
        return sentences, events

    async def _wait_for_event(
        self,
        socket: Any,
        target_event: str,
        events: list[dict[str, Any]],
        sentences: list[dict[str, Any]],
    ) -> None:
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=self.timeout_seconds)
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise PipelineError(
                    "DASHSCOPE_INVALID_RESPONSE", "Fun-ASR 返回了无效 JSON", technical_detail=str(exc)
                ) from exc
            if not isinstance(payload, dict):
                continue
            events.append(payload)
            header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
            event = header.get("event")
            if event == "task-failed":
                raise _dashscope_service_error(header)
            if event == "result-generated":
                output = payload.get("payload", {}).get("output", {})
                sentence = output.get("sentence") if isinstance(output, dict) else None
                if (
                    isinstance(sentence, dict)
                    and sentence.get("sentence_end") is True
                    and sentence.get("heartbeat") is not True
                ):
                    sentences.append(sentence)
            if event == target_event:
                return


def normalize_dashscope_response(
    payload: dict[str, Any], request: AsrRequest, raw_path: Path, provider: str
) -> AsrResult:
    raw_sentences = payload.get("final_sentences")
    if not isinstance(raw_sentences, list):
        raise PipelineError("DASHSCOPE_INVALID_RESPONSE", "Fun-ASR 原始响应缺少最终句子")
    segments: list[AsrSegment] = []
    for index, sentence in enumerate(raw_sentences):
        if not isinstance(sentence, dict):
            continue
        text = optional_text(sentence.get("text"))
        if not text:
            continue
        words = tuple(
            AsrWord(
                start_time=_milliseconds_to_seconds(word.get("begin_time")),
                end_time=_milliseconds_to_seconds(word.get("end_time")),
                text=f"{optional_text(word.get('text')) or ''}{optional_text(word.get('punctuation')) or ''}",
                confidence=float_or_none(word.get("confidence")),
            )
            for word in sentence.get("words", [])
            if isinstance(word, dict) and optional_text(word.get("text"))
        )
        segments.append(
            AsrSegment(
                segment_id=f"c{request.chunk.index:04d}-s{index:06d}",
                start_time=_milliseconds_to_seconds(sentence.get("begin_time")),
                end_time=_milliseconds_to_seconds(sentence.get("end_time")),
                text=text,
                confidence=float_or_none(sentence.get("confidence")),
                language=optional_text(sentence.get("language")) or request.language,
                provider=provider,
                model=request.model,
                chunk_index=request.chunk.index,
                raw_response_reference=str(raw_path),
                words=words,
            )
        )
    if not segments:
        raise PipelineError("DASHSCOPE_INVALID_RESPONSE", "Fun-ASR 未返回最终句子")
    return AsrResult(
        provider=provider,
        model=request.model,
        language=next((item.language for item in segments if item.language), request.language),
        chunk_index=request.chunk.index,
        raw_response_reference=str(raw_path),
        segments=tuple(segments),
    )


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
                    words=tuple(_normalize_words(raw.get("words"))),
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
                words=tuple(_normalize_words(payload.get("words"))),
            )
        )
    explicit_no_speech = (
        payload.get("no_speech") is True
        and not optional_text(payload.get("text"))
        and raw_segments == []
    )
    if not segments and not explicit_no_speech:
        raise PipelineError("ASR_PROVIDER_FAILED", "ASR 未返回任何可用转写文本")
    return AsrResult(
        provider=provider,
        model=request.model,
        language=language,
        chunk_index=request.chunk.index,
        raw_response_reference=str(raw_path),
        segments=tuple(segments),
    )


def _normalize_words(value: Any) -> list[AsrWord]:
    if not isinstance(value, list):
        return []
    words: list[AsrWord] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = optional_text(item.get("word") or item.get("text"))
        if not text:
            continue
        punctuation = optional_text(item.get("punctuation")) or ""
        words.append(
            AsrWord(
                start_time=float_or_none(item.get("start", item.get("begin_time"))),
                end_time=float_or_none(item.get("end", item.get("end_time"))),
                text=f"{text}{punctuation}",
                confidence=float_or_none(item.get("confidence", item.get("probability"))),
            )
        )
    return words


def combine_asr_results(
    results: list[AsrResult],
    chunks: list[AudioChunk],
    *,
    diagnostics: list[dict[str, Any]] | None = None,
) -> list[AsrSegment]:
    chunk_map = {chunk.index: chunk for chunk in chunks}
    diagnostics = diagnostics if diagnostics is not None else []
    combined: list[AsrSegment] = []
    for result in sorted(results, key=lambda item: item.chunk_index):
        chunk = chunk_map[result.chunk_index]
        if not result.segments:
            diagnostics.append(
                {
                    "type": "no_speech_chunk",
                    "chunk_index": result.chunk_index,
                    "source_start_seconds": chunk.start_time,
                    "source_end_seconds": chunk.end_time,
                }
            )
            continue
        for segment_index, segment in enumerate(result.segments):
            candidate = _map_segment_to_global(segment, chunk.start_time)
            candidate = _deduplicate_boundary_segment(
                candidate,
                combined,
                chunk,
                diagnostics,
                first_segment_in_chunk=segment_index == 0,
            )
            if candidate is not None:
                combined.append(candidate)

    # Timestamped outputs are globally ordered after overlap removal. Untimed outputs retain
    # deterministic chunk/provider order and are placed after the known timeline.
    ordered = [
        item
        for _position, item in sorted(
            enumerate(combined),
            key=lambda pair: (
                pair[1].start_time is None,
                pair[1].start_time if pair[1].start_time is not None else float("inf"),
                pair[0],
            ),
        )
    ]
    ordered = _stabilize_word_timeline(ordered, diagnostics)
    return [replace(item, segment_id=f"segment-{index:06d}") for index, item in enumerate(ordered, 1)]


def _stabilize_word_timeline(
    segments: list[AsrSegment], diagnostics: list[dict[str, Any]]
) -> list[AsrSegment]:
    """Keep uncertain overlap text while refusing contradictory word timestamps.

    Two providers can align different words to the same overlap window. Reordering
    their text would change meaning, while keeping both timestamp sequences would
    make normalized JSON retreat. The conservative representation retains the
    words, clears only the conflicting timestamps, and records the boundary for
    inspection instead of inventing new times or deleting uncertain content.
    """
    previous_word_start = -1.0
    previous_segment_end = -1.0
    stabilized: list[AsrSegment] = []
    for segment in segments:
        words: list[AsrWord] = []
        cleared: list[str] = []
        for word in segment.words:
            if word.start_time is not None and word.start_time < max(
                previous_word_start, previous_segment_end
            ):
                cleared.append(word.text)
                words.append(replace(word, start_time=None, end_time=None))
                continue
            words.append(word)
            if word.start_time is not None:
                previous_word_start = word.start_time
        if cleared:
            diagnostics.append(
                {
                    "type": "boundary_conflict_retained",
                    "method": "conflicting_word_timestamps_cleared",
                    "chunk_index": segment.chunk_index,
                    "retained_words": cleared,
                }
            )
            timed_words = [word for word in words if word.start_time is not None]
            segment = replace(
                segment,
                start_time=timed_words[0].start_time if timed_words else None,
                end_time=timed_words[-1].end_time if timed_words else None,
            )
        stabilized.append(replace(segment, words=tuple(words)))
        if segment.end_time is not None:
            previous_segment_end = max(previous_segment_end, segment.end_time)
    return stabilized


def _map_segment_to_global(segment: AsrSegment, offset: float) -> AsrSegment:
    return replace(
        segment,
        start_time=(segment.start_time + offset) if segment.start_time is not None else None,
        end_time=(segment.end_time + offset) if segment.end_time is not None else None,
        words=tuple(
            replace(
                word,
                start_time=(word.start_time + offset) if word.start_time is not None else None,
                end_time=(word.end_time + offset) if word.end_time is not None else None,
            )
            for word in segment.words
        ),
    )


def _deduplicate_boundary_segment(
    candidate: AsrSegment,
    combined: list[AsrSegment],
    chunk: AudioChunk,
    diagnostics: list[dict[str, Any]],
    *,
    first_segment_in_chunk: bool,
) -> AsrSegment | None:
    if chunk.overlap_before_seconds <= 0 or not combined:
        return candidate
    boundary_start = chunk.start_time
    boundary_end = chunk.start_time + chunk.overlap_before_seconds
    previous = [item for item in combined if item.chunk_index < chunk.index][-12:]
    if not previous:
        return candidate

    candidate_in_boundary = (
        first_segment_in_chunk
        if candidate.start_time is None
        else candidate.start_time <= boundary_end + 0.25
    )
    if not candidate_in_boundary:
        return candidate
    normalized_candidate = normalize_text_for_overlap(candidate.text)
    for prior in reversed(previous):
        same_text = normalized_candidate == normalize_text_for_overlap(prior.text)
        if not same_text:
            continue
        if candidate.start_time is None or prior.start_time is None:
            if (
                candidate.start_time is None
                and prior.start_time is None
                and first_segment_in_chunk
                and prior is previous[-1]
            ):
                diagnostics.append(
                    {
                        "type": "duplicate_removed",
                        "method": "adjacent_edge_text_without_timestamps",
                        "chunk_index": chunk.index,
                        "kept_segment_id": prior.segment_id,
                        "removed_text": candidate.text,
                    }
                )
                return None
            diagnostics.append(
                {
                    "type": "boundary_conflict_retained",
                    "method": "insufficient_timestamps",
                    "chunk_index": chunk.index,
                    "previous_text": prior.text,
                    "current_text": candidate.text,
                }
            )
            return candidate
        if _time_ranges_overlap(candidate, prior, boundary_start, boundary_end):
            diagnostics.append(
                {
                    "type": "duplicate_removed",
                    "method": "timestamp_and_normalized_text",
                    "chunk_index": chunk.index,
                    "kept_segment_id": prior.segment_id,
                    "removed_text": candidate.text,
                }
            )
            return None

    if candidate.words:
        prior_words = [
            word
            for item in previous
            for word in item.words
            if (
                word.start_time is not None
                and (word.end_time if word.end_time is not None else word.start_time)
                >= boundary_start - 0.25
                and word.start_time <= boundary_end + 0.25
            )
        ]
        approximate_prefix_count, approximate_similarity = _matching_overlap_prefix(
            list(candidate.words), prior_words, boundary_end
        )
        if approximate_prefix_count:
            removed_prefix = list(candidate.words[:approximate_prefix_count])
            retained_prefix_tail = list(candidate.words[approximate_prefix_count:])
            diagnostics.append(
                {
                    "type": "word_overlap_removed",
                    "method": "overlap_window_and_normalized_similarity",
                    "chunk_index": chunk.index,
                    "normalized_similarity": round(approximate_similarity, 4),
                    "removed_words": [word.text for word in removed_prefix],
                }
            )
            if not retained_prefix_tail:
                return None
            candidate = replace(
                candidate,
                text=_join_words(retained_prefix_tail),
                start_time=retained_prefix_tail[0].start_time,
                end_time=retained_prefix_tail[-1].end_time,
                words=tuple(retained_prefix_tail),
            )

        retained_words: list[AsrWord] = []
        removed_words: list[AsrWord] = []
        for word in candidate.words:
            duplicate = any(_same_timed_word(word, prior_word) for prior_word in prior_words)
            (removed_words if duplicate else retained_words).append(word)
        if removed_words:
            diagnostics.append(
                {
                    "type": "word_overlap_removed",
                    "method": "word_timestamp_and_normalized_text",
                    "chunk_index": chunk.index,
                    "removed_words": [word.text for word in removed_words],
                }
            )
            if not retained_words:
                return None
            candidate = replace(
                candidate,
                text=_join_words(retained_words),
                start_time=retained_words[0].start_time,
                end_time=retained_words[-1].end_time,
                words=tuple(retained_words),
            )
            retained_boundary_words = [
                word.text
                for word in retained_words
                if word.start_time is None or word.start_time <= boundary_end + 0.25
            ]
            if retained_boundary_words:
                diagnostics.append(
                    {
                        "type": "boundary_conflict_retained",
                        "method": "partial_word_overlap_remaining",
                        "chunk_index": chunk.index,
                        "retained_words": retained_boundary_words,
                    }
                )

    for prior in reversed(previous[-4:]):
        if not _time_ranges_overlap(candidate, prior, boundary_start, boundary_end):
            continue
        similarity = SequenceMatcher(
            None,
            normalize_text_for_overlap(prior.text),
            normalize_text_for_overlap(candidate.text),
        ).ratio()
        if similarity >= 0.45:
            diagnostics.append(
                {
                    "type": "boundary_conflict_retained",
                    "chunk_index": chunk.index,
                    "previous_text": prior.text,
                    "current_text": candidate.text,
                    "normalized_similarity": round(similarity, 4),
                }
            )
            break
    return candidate


def _matching_overlap_prefix(
    candidate_words: list[AsrWord], prior_words: list[AsrWord], boundary_end: float
) -> tuple[int, float]:
    """Find a long, timestamp-backed duplicate prefix with minor ASR spelling drift.

    The compared words all describe the same source-audio overlap window. A fairly
    long normalized string and a conservative similarity floor are required so a
    short phrase such as "model deployment" / "model training" is never discarded.
    """
    prefix: list[AsrWord] = []
    for word in candidate_words:
        if word.start_time is None or word.start_time >= boundary_end - 1e-6:
            break
        prefix.append(word)
    if not prefix or not prior_words:
        return 0, 0.0

    prior_text = normalize_text_for_overlap(_join_words(prior_words))
    prefix_text = normalize_text_for_overlap(_join_words(prefix))
    if min(len(prior_text), len(prefix_text)) < 12:
        return 0, 0.0

    prefix_start = prefix[0].start_time
    prefix_end = prefix[-1].end_time or prefix[-1].start_time
    prior_start = prior_words[0].start_time
    prior_end = prior_words[-1].end_time or prior_words[-1].start_time
    if None in {prefix_start, prefix_end, prior_start, prior_end}:
        return 0, 0.0
    time_overlap = min(prefix_end, prior_end) - max(prefix_start, prior_start)
    if time_overlap < 1.0:
        return 0, 0.0

    matcher = SequenceMatcher(None, prior_text, prefix_text)
    similarity = matcher.ratio()
    if similarity < 0.65:
        return 0, similarity

    # Preserve an unmatched tail at the exact edge. It may be the first word that
    # the previous chunk failed to emit, even though its timestamp begins just
    # before the nominal boundary. Internal spelling drift is still removed when
    # a later matching block anchors it to the same source-audio passage.
    last_match_end = max(
        (block.b + block.size for block in matcher.get_matching_blocks() if block.size),
        default=0,
    )
    removable_count = 0
    normalized_end = 0
    for word in prefix:
        normalized_end += len(normalize_text_for_overlap(word.text))
        if normalized_end > last_match_end:
            break
        removable_count += 1
    if removable_count == 0:
        return 0, similarity
    return removable_count, similarity


def normalize_text_for_overlap(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and not unicodedata.category(character).startswith(("P", "S"))
    )


def _time_ranges_overlap(
    left: AsrSegment, right: AsrSegment, boundary_start: float, boundary_end: float
) -> bool:
    if left.start_time is None or right.start_time is None:
        return False
    left_end = left.end_time if left.end_time is not None else left.start_time
    right_end = right.end_time if right.end_time is not None else right.start_time
    overlap = min(left_end, right_end, boundary_end + 0.25) - max(
        left.start_time, right.start_time, boundary_start - 0.25
    )
    return overlap >= -0.15


def _same_timed_word(left: AsrWord, right: AsrWord) -> bool:
    if normalize_text_for_overlap(left.text) != normalize_text_for_overlap(right.text):
        return False
    if left.start_time is None or right.start_time is None:
        return False
    left_end = left.end_time if left.end_time is not None else left.start_time
    right_end = right.end_time if right.end_time is not None else right.start_time
    return min(left_end, right_end) - max(left.start_time, right.start_time) >= -0.08


def _join_words(words: list[AsrWord]) -> str:
    result = ""
    for word in words:
        text = word.text.strip()
        if not text:
            continue
        if result and _needs_word_space(result[-1], text[0]):
            result += " "
        result += text
    return result.strip()


def _needs_word_space(left: str, right: str) -> bool:
    return left.isascii() and right.isascii() and left.isalnum() and right.isalnum()


def validate_segments(segments: list[AsrSegment]) -> None:
    if not segments:
        raise PipelineError("ASR_PARTIAL_FAILURE", "没有可保存的 ASR 分段")
    previous_start = -1.0
    previous_word_start = -1.0
    for segment in segments:
        if not segment.text.strip():
            raise PipelineError("ASR_PARTIAL_FAILURE", "ASR 结果包含空文本分段")
        if segment.start_time is not None and segment.end_time is not None:
            if segment.start_time < 0 or segment.end_time < segment.start_time:
                raise PipelineError("ASR_PARTIAL_FAILURE", "ASR 返回了无效时间戳")
            if segment.start_time < previous_start:
                raise PipelineError("ASR_PARTIAL_FAILURE", "ASR 分段顺序不稳定")
            previous_start = segment.start_time
        for word in segment.words:
            if word.start_time is None:
                continue
            if word.start_time < 0 or (
                word.end_time is not None and word.end_time < word.start_time
            ):
                raise PipelineError("ASR_PARTIAL_FAILURE", "ASR 返回了无效词时间戳")
            if word.start_time < previous_word_start:
                raise PipelineError("ASR_PARTIAL_FAILURE", "ASR 词时间戳顺序不稳定")
            previous_word_start = word.start_time


def save_transcript_artifacts(
    paths: WorkspacePaths,
    segments: list[AsrSegment],
    *,
    provider: str,
    model: str,
    language: str | None,
    chunk_count: int,
    merge_diagnostics: list[dict[str, Any]] | None = None,
    provider_responses: list[str] | None = None,
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
        "merge_diagnostics": merge_diagnostics or [],
        "segments": [segment.to_dict() for segment in segments],
    }
    write_json(normalized_path, payload)
    write_json(
        raw_index_path,
        {
            **payload,
            "provider_responses": sorted(
                provider_responses
                if provider_responses is not None
                else {segment.raw_response_reference for segment in segments}
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


def load_asr_gateway_credentials(snapshot: dict[str, Any]) -> tuple[dict[str, Any], str]:
    config = snapshot.get("asr")
    if not isinstance(config, dict):
        raise PipelineError("ASR_CONFIG_INVALID", "任务快照中没有 ASR Gateway 配置")
    base_url = str(config.get("base_url") or "").strip()
    model = str(config.get("model") or "").strip()
    protocol = str(config.get("provider") or "").strip()
    secret_env_name = str(config.get("secret_env_name") or "").strip()
    gateway_id = config.get("gateway_id")
    try:
        secret = (
            resolve_gateway_credential(int(gateway_id))
            if gateway_id is not None
            else (os.getenv(secret_env_name, "").strip() if secret_env_name else "")
        )
    except (SecretStorageError, TypeError, ValueError) as exc:
        raise PipelineError(
            "ASR_SECRET_MISSING", "无法读取 ASR Gateway 密钥", technical_detail=str(exc)
        ) from exc
    if not base_url or not model or not protocol:
        raise PipelineError("ASR_CONFIG_INVALID", "任务快照中的 ASR Gateway 配置不完整")
    if (secret_env_name or config.get("secret_source") == "DIRECT") and not secret:
        code = "DASHSCOPE_KEY_MISSING" if protocol == "dashscope-fun-asr-websocket" else "ASR_SECRET_MISSING"
        raise PipelineError(code, f"缺少 ASR Gateway 密钥环境变量 {secret_env_name}")
    return config, secret


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
    if isinstance(value, dict):
        return {
            key: "***" if _is_sensitive_response_key(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def _is_sensitive_response_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if normalized in {
        "authorization",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "bearer_token",
        "secret",
        "password",
        "credential",
        "credentials",
    }:
        return True
    return normalized.startswith(("secret_", "password_", "credential_")) or normalized.endswith(
        ("_api_key", "_access_token", "_bearer_token", "_password", "_secret")
    )


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


def _milliseconds_to_seconds(value: Any) -> float | None:
    milliseconds = float_or_none(value)
    return milliseconds / 1000.0 if milliseconds is not None else None


def _dashscope_service_error(header: dict[str, Any]) -> PipelineError:
    code = str(header.get("error_code") or "").upper()
    message = str(header.get("error_message") or "Fun-ASR 服务返回失败")
    combined = f"{code} {message}".lower()
    if "workspace" in combined:
        mapped = "DASHSCOPE_WORKSPACE_REQUIRED"
        user_message = "Fun-ASR 需要有效的 DASHSCOPE_WORKSPACE_ID"
    elif "region" in combined:
        mapped = "DASHSCOPE_REGION_MISMATCH"
        user_message = "Fun-ASR API Key 与服务地域不匹配"
    elif "rate" in combined or "thrott" in combined or "limit" in combined:
        mapped = "DASHSCOPE_RATE_LIMITED"
        user_message = "Fun-ASR 请求受到限流"
    elif "timeout" in combined:
        mapped = "DASHSCOPE_TIMEOUT"
        user_message = "Fun-ASR 请求超时"
    elif "auth" in combined or "token" in combined or "unauthor" in combined:
        mapped = "DASHSCOPE_AUTH_FAILED"
        user_message = "Fun-ASR 认证失败"
    else:
        mapped = "DASHSCOPE_INVALID_RESPONSE"
        user_message = "Fun-ASR 返回任务失败"
    return PipelineError(
        mapped,
        user_message,
        technical_detail=f"{code}: {message}"[:1000],
        retryable=mapped in {"DASHSCOPE_RATE_LIMITED", "DASHSCOPE_TIMEOUT"},
    )


def _dashscope_connection_error(exc: Exception) -> PipelineError:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    detail = f"{type(exc).__name__}: {exc}"[:1000]
    lowered = detail.lower()
    if status_code in {401, 403}:
        return PipelineError("DASHSCOPE_AUTH_FAILED", "Fun-ASR 认证失败", technical_detail=detail)
    if status_code == 429:
        return PipelineError(
            "DASHSCOPE_RATE_LIMITED", "Fun-ASR 请求受到限流", technical_detail=detail, retryable=True
        )
    if "workspace" in lowered:
        return PipelineError(
            "DASHSCOPE_WORKSPACE_REQUIRED",
            "Fun-ASR 需要有效的 DASHSCOPE_WORKSPACE_ID",
            technical_detail=detail,
        )
    if "region" in lowered:
        return PipelineError(
            "DASHSCOPE_REGION_MISMATCH", "Fun-ASR API Key 与服务地域不匹配", technical_detail=detail
        )
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return PipelineError(
            "DASHSCOPE_TIMEOUT", "Fun-ASR 连接超时", technical_detail=detail, retryable=True
        )
    return PipelineError(
        "DASHSCOPE_CONNECTION_FAILED",
        "Fun-ASR 无法建立连接",
        technical_detail=detail,
        retryable=True,
    )
