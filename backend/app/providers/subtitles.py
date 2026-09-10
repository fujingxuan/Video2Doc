from __future__ import annotations

import asyncio
import hashlib
import html
import inspect
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from PIL import Image, ImageOps

from app.core.config import get_settings
from app.core.errors import PipelineError
from app.providers.asr import AsrSegment, format_srt_time
from app.providers.process import CancelCheck, ExternalProcessRunner, process_registry
from app.providers.gateways import trust_environment_proxy
from app.providers.frames import (
    FrameExtractionConfig,
    FrameExtractionResult,
    VideoFrameExtractor,
)
from app.providers.ocr_timeline import recognize_retained_frames
from app.providers.ocr import OcrResult
from app.storage.workspace import WorkspacePaths, read_json, write_json


SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".txt", ".json3"}
DEFAULT_SUBTITLE_LANGUAGES = (
    "zh-Hans",
    "zh-CN",
    "zh-Hant",
    "zh-TW",
    "zh-HK",
    "zh",
    "en",
    "en-orig",
)
MAX_SUBTITLE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SubtitleImport:
    segments: list[AsrSegment]
    encoding: str
    subtitle_format: str


@dataclass(frozen=True, slots=True)
class SubtitleAcquisition:
    path: str
    language: str | None
    source_kind: str
    subtitle_format: str
    sha256: str
    size_bytes: int
    manifest_path: str
    provider: str = "yt-dlp-subtitle"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "source_kind": self.source_kind,
            "subtitle_format": self.subtitle_format,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "manifest_path": self.manifest_path,
            "provider": self.provider,
        }


class YtDlpSubtitleSource:
    def __init__(
        self,
        executable: str,
        runner: ExternalProcessRunner | None = None,
        *,
        languages: tuple[str, ...] = DEFAULT_SUBTITLE_LANGUAGES,
    ) -> None:
        self.executable = executable
        self.runner = runner or ExternalProcessRunner()
        self.languages = languages

    async def acquire(
        self,
        source: str,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
        progress: Callable[[float | None, str], None],
    ) -> SubtitleAcquisition:
        await _ensure_subtitle_active(cancel_check)
        manifest_path = paths.metadata / "subtitle_acquisition.json"
        reusable = _load_subtitle_acquisition(manifest_path, source=source)
        if reusable is not None:
            progress(1.0, f"复用已抓取字幕：{reusable.language or '未知语言'}")
            return reusable
        metadata = read_json(paths.metadata / "ytdlp.json")
        errors: list[str] = []
        if not isinstance(metadata, dict) or metadata.get("_video2doc_source_url", source) != source:
            try:
                metadata = await self._load_metadata(source, paths, cancel_check=cancel_check)
            except PipelineError as exc:
                if exc.code in {"PROCESS_CANCELLED", "backend_shutdown"}:
                    raise
                errors.append(exc.technical_detail or exc.user_message)
                metadata = {}
        for index, (language, source_kind) in enumerate(_subtitle_tracks(metadata, self.languages)[:6]):
            await _ensure_subtitle_active(cancel_check)
            # Each attempt owns a fresh directory: incomplete or stale tracks cannot win selection.
            destination = paths.source / "subtitles" / uuid4().hex
            destination.mkdir(parents=True, exist_ok=True)
            try:
                await self.runner.run([
                    self.executable, "--newline", "--no-playlist", "--no-warnings",
                    "--ignore-no-formats-error", "--skip-download",
                    *( ["--write-subs", "--no-write-auto-subs"] if source_kind == "manual"
                       else ["--no-write-subs", "--write-auto-subs"] ),
                    "--sub-langs", re.escape(language).replace(r"\-", "-"),
                    "--sub-format", "srt/vtt/ass/json3/best",
                    "--retries", "3", "--extractor-retries", "3", "--socket-timeout", "20",
                    "--retry-sleep", "http:exp=1:4", "--retry-sleep", "extractor:exp=1:4",
                    "--output", str(destination / "source.%(ext)s"), source,
                ], cwd=paths.root, log_dir=paths.logs, log_name=f"ytdlp_subtitles_{index}",
                    timeout_seconds=min(120, get_settings().media_process_timeout_seconds),
                    cancel_check=cancel_check,
                    stdout_callback=lambda line: progress(None, line[:300]),
                    stderr_callback=lambda line: progress(None, line[:300]))
                for selected in sorted(destination.glob("source.*")):
                    if (not selected.is_file() or selected.suffix.lower() not in SUBTITLE_EXTENSIONS
                            or not 0 < selected.stat().st_size <= MAX_SUBTITLE_BYTES):
                        continue
                    try:
                        imported = import_subtitle(selected, language=language)
                        if any(segment.start_time is None or segment.end_time is None
                               for segment in imported.segments):
                            continue
                    except PipelineError:
                        continue
                    await _ensure_subtitle_active(cancel_check)
                    payload = selected.read_bytes()
                    acquisition = SubtitleAcquisition(
                        path=str(selected.resolve()), language=language, source_kind=source_kind,
                        subtitle_format=selected.suffix.lower()[1:],
                        sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload),
                        manifest_path=str(manifest_path),
                    )
                    write_json(manifest_path, {
                        "schema_version": 2, "source_url": source,
                        "language_priority": list(self.languages), **acquisition.to_dict(),
                    })
                    progress(1.0, f"已抓取{_source_kind_label(source_kind)}字幕：{language}")
                    return acquisition
                errors.append(f"{language} ({source_kind}): 没有有效的完整字幕文件")
            except PipelineError as exc:
                if exc.code in {"PROCESS_CANCELLED", "backend_shutdown"}:
                    raise
                errors.append(f"{language}: {exc.technical_detail or exc.user_message}")
                progress(None, f"{language} 字幕暂不可用，继续尝试备用字幕")
        bilibili = await BilibiliSubtitleSource(self.languages).acquire_if_available(
            source, paths, cancel_check=cancel_check, progress=progress,
        )
        if bilibili is not None:
            return bilibili
        raise PipelineError(
            "SUBTITLE_FETCH_FAILED" if errors else "SUBTITLE_NOT_FOUND",
            "视频链接没有可用的中文或英文字幕（已检查人工字幕和自动字幕，并尝试备用来源）",
            technical_detail="; ".join(errors)[-4000:] or None, retryable=bool(errors),
        )

    async def _load_metadata(
        self,
        source: str,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
    ) -> dict[str, Any]:
        try:
            result = await self.runner.run(
                [
                    self.executable,
                    "--dump-single-json",
                    "--ignore-no-formats-error",
                    "--extractor-retries", "3", "--socket-timeout", "20",
                    "--retry-sleep", "extractor:exp=1:4",
                    "--no-playlist",
                    "--no-warnings",
                    source,
                ],
                cwd=paths.root,
                log_dir=paths.logs,
                log_name="ytdlp_subtitle_metadata",
                timeout_seconds=min(90, get_settings().media_process_timeout_seconds),
                cancel_check=cancel_check,
            )
        except PipelineError as exc:
            if exc.code in {"PROCESS_CANCELLED", "PROCESS_TIMEOUT", "backend_shutdown"}:
                raise
            code = "YTDLP_UNAVAILABLE" if exc.code == "PROCESS_UNAVAILABLE" else "SUBTITLE_FETCH_FAILED"
            raise PipelineError(
                code,
                (
                    "找不到 yt-dlp，无法检查视频链接字幕"
                    if code == "YTDLP_UNAVAILABLE"
                    else "yt-dlp 无法读取视频链接的字幕信息"
                ),
                technical_detail=exc.technical_detail,
                retryable=exc.retryable,
                log_path=exc.log_path,
            ) from exc
        try:
            metadata = json.loads("\n".join(result.stdout_lines))
            if not isinstance(metadata, dict):
                raise ValueError("Expected a metadata object")
            metadata["_video2doc_source_url"] = source
        except (ValueError, TypeError) as exc:
            raise PipelineError(
                "SUBTITLE_FETCH_FAILED",
                "yt-dlp 返回了无法解析的字幕信息",
                technical_detail=str(exc),
                log_path=str(result.stdout_log),
            ) from exc
        write_json(paths.metadata / "ytdlp.json", metadata)
        return metadata


class BilibiliSubtitleSource:
    """Independent fallback for Bilibili player subtitle tracks omitted by yt-dlp."""

    _BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b", re.IGNORECASE)

    def __init__(
        self,
        languages: tuple[str, ...] = DEFAULT_SUBTITLE_LANGUAGES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.languages = languages
        self.transport = transport

    async def acquire_if_available(
        self,
        source: str,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
        progress: Callable[[float | None, str], None],
    ) -> SubtitleAcquisition | None:
        hosts = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
        parsed = urlparse(source)
        if parsed.hostname not in hosts | {"b23.tv", "www.b23.tv"}:
            return None
        await _ensure_subtitle_active(cancel_check)
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True, max_redirects=5,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
                trust_env=trust_environment_proxy("https://api.bilibili.com"),
                transport=self.transport,
            ) as client:
                if parsed.hostname in {"b23.tv", "www.b23.tv"}:
                    redirected = await _subtitle_get(client, source, cancel_check)
                    parsed = urlparse(str(redirected.url))
                    if parsed.hostname not in hosts:
                        return None
                match = self._BVID_RE.search(parsed.path)
                aid = re.search(r"/av(\d+)", parsed.path, re.IGNORECASE)
                if not match and not aid:
                    return None
                identity = {"bvid": match.group(1)} if match else {"aid": aid.group(1)}
                response = await _subtitle_get(client, "https://api.bilibili.com/x/web-interface/view",
                                                cancel_check, params=identity)
                view = response.json()
                if not isinstance(view, dict) or view.get("code") != 0 or not isinstance(view.get("data"), dict):
                    return None
                data = view["data"]
                bvid = str(data.get("bvid") or identity.get("bvid") or "")
                page_index = int((parse_qs(parsed.query).get("p") or ["1"])[0]) - 1
                pages = data.get("pages")
                if page_index < 0 or (isinstance(pages, list) and pages and page_index >= len(pages)):
                    return None
                selected_page = pages[page_index] if isinstance(pages, list) and pages else data
                if not isinstance(selected_page, dict) or not selected_page.get("cid"):
                    return None
                cid = selected_page["cid"]
                for endpoint in ("/x/player/wbi/v2", "/x/player/v2"):
                    try:
                        response = await _subtitle_get(client, "https://api.bilibili.com" + endpoint,
                                                       cancel_check, params={**identity, "cid": cid})
                        payload = response.json()
                        info = payload.get("data") if isinstance(payload, dict) else None
                        subtitle = info.get("subtitle") if isinstance(info, dict) else None
                        raw_items = (subtitle.get("subtitles") or subtitle.get("list")) if isinstance(subtitle, dict) else None
                        remaining = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
                    except (httpx.HTTPError, ValueError, TypeError):
                        continue
                    for _ in range(min(6, len(remaining))):
                        selected = _select_bilibili_subtitle(remaining, self.languages)
                        if selected is None:
                            break
                        remaining.remove(selected)
                        url = str(selected.get("subtitle_url") or selected.get("url") or "").strip()
                        if url.startswith("//"):
                            url = "https:" + url
                        if not url.startswith("https://"):
                            continue
                        try:
                            body_response = await _subtitle_get(client, url, cancel_check)
                            body_payload = body_response.json()
                            body = body_payload.get("body") if isinstance(body_payload, dict) else None
                            cues = _bilibili_cues(body)
                            if not cues:
                                continue
                        except (httpx.HTTPError, ValueError, TypeError):
                            continue
                        await _ensure_subtitle_active(cancel_check)
                        destination = paths.source / "subtitles" / f"bilibili_{uuid4().hex}.srt"
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_text(_render_srt(cues), encoding="utf-8")
                        content = destination.read_bytes()
                        language = str(selected.get("lan") or selected.get("lang") or "zh")
                        manifest_path = paths.metadata / "subtitle_acquisition.json"
                        acquisition = SubtitleAcquisition(
                            path=str(destination.resolve()), language=language,
                            source_kind="automatic" if (selected.get("ai_type") or language.startswith("ai-")) else "manual",
                            subtitle_format="srt", sha256=hashlib.sha256(content).hexdigest(),
                            size_bytes=len(content), manifest_path=str(manifest_path), provider="bilibili-player-api",
                        )
                        write_json(paths.metadata / "bilibili_subtitle.json", body_payload)
                        write_json(paths.metadata / "ytdlp.json", {
                            "_video2doc_source_url": source, "id": bvid or str(identity.get("aid")),
                            "title": data.get("title"), "duration": selected_page.get("duration") or data.get("duration"),
                            "webpage_url": source, "extractor": "bilibili-player-api",
                        })
                        write_json(manifest_path, {
                            "schema_version": 2, "source_url": source, "bvid": bvid, "cid": cid,
                            "language_priority": list(self.languages), **acquisition.to_dict(),
                        })
                        progress(1.0, f"已从 B 站播放器接口抓取字幕：{language}")
                        return acquisition
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            progress(None, f"B 站播放器字幕接口暂不可用：{type(exc).__name__}")
        return None


def _bilibili_cues(body) -> list[tuple[float, float, str]]:
    cues = []
    for item in body if isinstance(body, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item.get("from")), float(item.get("to"))
        except (TypeError, ValueError):
            continue
        content = html.unescape(str(item.get("content") or "")).strip()
        if content and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end:
            cues.append((start, end, content))
    return sorted(cues, key=lambda cue: cue[0])


async def _subtitle_get(client: httpx.AsyncClient, url: str, cancel_check: CancelCheck, **kwargs):
    for attempt in range(2):
        await _ensure_subtitle_active(cancel_check)
        request = asyncio.create_task(client.get(url, **kwargs))
        try:
            while not request.done():
                await asyncio.wait({request}, timeout=0.2)
                await _ensure_subtitle_active(cancel_check)
            response = await request
            response.raise_for_status()
            if len(response.content) > MAX_SUBTITLE_BYTES:
                raise ValueError("Subtitle response exceeds size limit")
            return response
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if attempt or status is not None and status not in {408, 429, 500, 502, 503, 504}:
                raise
            for _ in range(5):
                await asyncio.sleep(0.1)
                await _ensure_subtitle_active(cancel_check)
        finally:
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
    raise RuntimeError("Unreachable subtitle retry state")


class EmbeddedCaptionOcrSource:
    """Recognize burned-in captions from a dense crop of the lower video area."""

    async def acquire(
        self,
        media_path: Path,
        *,
        source: str,
        duration: float,
        ffmpeg_executable: str,
        ocr_config: dict[str, Any],
        attempt: int,
        paths: WorkspacePaths,
        cancel_check: CancelCheck,
        progress: Callable[[float | None, str], None],
        interval_seconds: float = 1.0,
        crop_top_ratio: float = 0.55,
        crop_bottom_ratio: float = 0.98,
    ) -> SubtitleAcquisition:
        if duration <= 0:
            raise PipelineError(
                "EMBEDDED_SUBTITLE_FAILED",
                "无法确定视频时长，不能识别画面内嵌字幕",
            )
        if not isinstance(ocr_config, dict) or not ocr_config.get("model"):
            raise PipelineError(
                "EMBEDDED_SUBTITLE_OCR_REQUIRED",
                "视频没有平台字幕；识别画面下方内嵌字幕需要选择 OCR 模型",
            )
        effective_interval = max(
            0.5,
            float(interval_seconds),
            duration / 9_999,
        )
        caption_root = paths.extract / "embedded_subtitles"
        caption_paths = replace(
            paths,
            frames=caption_root / "frames",
            frame_candidates=caption_root / "frames" / "candidates",
            frame_normalized=caption_root / "frames" / "normalized",
            ocr=caption_root / "ocr",
            ocr_raw=caption_root / "ocr" / "provider_raw",
            ocr_normalized=caption_root / "ocr" / "normalized",
        )
        for directory in (
            caption_paths.frames,
            caption_paths.frame_candidates,
            caption_paths.frame_normalized,
            caption_paths.ocr,
            caption_paths.ocr_raw,
            caption_paths.ocr_normalized,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        frame_result = await VideoFrameExtractor(ffmpeg_executable).extract(
            media_path,
            duration=duration,
            paths=caption_paths,
            config=FrameExtractionConfig(
                strategy="fixed_interval",
                interval_seconds=effective_interval,
                max_candidate_frames=min(10_000, math.ceil(duration / effective_interval) + 2),
                normalized_max_width=1600,
            ),
            cancel_check=cancel_check,
            progress=lambda value, message: progress(
                None if value is None else value * 0.35,
                "内嵌字幕抽帧：" + message,
            ),
        )
        crop_directory = caption_root / "caption_crops"
        crop_directory.mkdir(parents=True, exist_ok=True)
        caption_records = []
        for index, record in enumerate(frame_result.records):
            await _ensure_subtitle_active(cancel_check)
            destination = crop_directory / f"{record.frame_id}.png"
            width, height = _crop_caption_region(
                Path(record.normalized_image_path),
                destination,
                crop_top_ratio,
                crop_bottom_ratio,
            )
            payload = destination.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            caption_records.append(
                replace(
                    record,
                    normalized_image_path=str(destination),
                    width=width,
                    height=height,
                    file_size=len(payload),
                    sha256=digest,
                    perceptual_hash=digest[:16],
                    kept=True,
                    drop_reason=None,
                    duplicate_of=None,
                )
            )
            progress(
                0.35 + 0.05 * (index + 1) / len(frame_result.records),
                f"正在裁剪视频下方字幕区域 {index + 1}/{len(frame_result.records)}",
            )
        caption_frame_result = FrameExtractionResult(
            tuple(caption_records),
            frame_result.manifest_path,
            frame_result.config_fingerprint + ":caption-crop-v1",
            frame_result.reused,
        )
        ocr_run = await recognize_retained_frames(
            caption_frame_result,
            ocr_config=ocr_config,
            attempt=attempt,
            paths=caption_paths,
            cancel_check=cancel_check,
            progress=lambda value, message: progress(
                None if value is None else 0.4 + value * 0.55,
                "内嵌字幕 OCR：" + message,
            ),
        )
        cues = _caption_cues(
            ocr_run.results,
            duration=duration,
            interval_seconds=effective_interval,
        )
        if not cues:
            raise PipelineError(
                "EMBEDDED_SUBTITLE_NOT_FOUND",
                "平台没有字幕轨道，OCR 也未在视频下方识别出稳定内嵌字幕",
            )
        destination = paths.source / "subtitles" / "source.embedded-ocr.srt"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_render_srt(cues), encoding="utf-8")
        payload = destination.read_bytes()
        manifest_path = paths.metadata / "subtitle_acquisition.json"
        acquisition = SubtitleAcquisition(
            path=str(destination.resolve()),
            language=str(ocr_config.get("language") or "zh"),
            source_kind="embedded-ocr",
            subtitle_format="srt",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            manifest_path=str(manifest_path),
            provider="embedded-caption-ocr",
        )
        write_json(
            manifest_path,
            {
                "schema_version": 2,
                "source_url": source,
                "method": "lower-frame-region-ocr",
                "interval_seconds": effective_interval,
                "crop_top_ratio": crop_top_ratio,
                "crop_bottom_ratio": crop_bottom_ratio,
                "frame_manifest": frame_result.manifest_path,
                "ocr_artifacts": {
                    "normalized": ocr_run.normalized_path,
                    "raw_index": ocr_run.raw_index_path,
                    "preview": ocr_run.preview_path,
                },
                "cue_count": len(cues),
                **acquisition.to_dict(),
            },
        )
        progress(1.0, f"已从视频画面下方识别 {len(cues)} 条内嵌字幕")
        return acquisition


def _select_bilibili_subtitle(
    items: list[dict[str, Any]],
    priorities: tuple[str, ...],
) -> dict[str, Any] | None:
    if not items:
        return None
    priority_map = {value.casefold(): index for index, value in enumerate(priorities)}

    def rank(item: dict[str, Any]) -> tuple[int, int, str]:
        language = str(item.get("lan") or item.get("lang") or "").casefold().removeprefix("ai-")
        exact = priority_map.get(language)
        if exact is None:
            exact = min(
                (
                    index
                    for value, index in priority_map.items()
                    if language.startswith(value + "-") or value.startswith(language + "-")
                ),
                default=len(priority_map) + 10,
            )
        automatic = int(bool(item.get("ai_type") or item.get("ai_status") or str(item.get("lan", "")).startswith("ai-")))
        # Manual Chinese first, then automatic Chinese, then English.
        return (0 if language.startswith("zh") else 2) + automatic, exact, language

    selected = min(items, key=rank)
    return selected if rank(selected)[1] < len(priority_map) + 10 else None


def _render_srt(cues: list[tuple[float, float, str]]) -> str:
    blocks = []
    for index, (start, end, text) in enumerate(cues, start=1):
        normalized = re.sub(r"[\r\n]+", " ", text).strip()
        if normalized:
            blocks.append(
                f"{index}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{normalized}"
            )
    return "\n\n".join(blocks) + "\n"


def _crop_caption_region(
    source: Path,
    destination: Path,
    top_ratio: float,
    bottom_ratio: float,
) -> tuple[int, int]:
    if not 0 <= top_ratio < bottom_ratio <= 1:
        raise PipelineError("EMBEDDED_SUBTITLE_CONFIG_INVALID", "内嵌字幕裁剪区域无效")
    partial = destination.with_name(f"{destination.stem}.part{destination.suffix}")
    with Image.open(source) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGB")
        top = max(0, min(rgb.height - 1, round(rgb.height * top_ratio)))
        bottom = max(top + 1, min(rgb.height, round(rgb.height * bottom_ratio)))
        cropped = rgb.crop((0, top, rgb.width, bottom))
        if cropped.height < 360:
            scale = min(3.0, 360 / max(1, cropped.height))
            cropped = cropped.resize(
                (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale))),
                Image.Resampling.LANCZOS,
            )
        width, height = cropped.size
        cropped.save(partial, format="PNG", optimize=True)
    partial.replace(destination)
    return width, height


def _caption_cues(
    results: tuple[OcrResult, ...],
    *,
    duration: float,
    interval_seconds: float,
) -> list[tuple[float, float, str]]:
    ordered = sorted(
        (item for item in results if item.media_timestamp is not None),
        key=lambda item: float(item.media_timestamp or 0.0),
    )
    half_interval = interval_seconds / 2
    cues: list[tuple[float, float, str]] = []
    current_text = ""
    current_start = 0.0
    last_seen = 0.0

    def close(end: float) -> None:
        nonlocal current_text
        if not current_text:
            return
        bounded_end = min(duration, max(current_start + 0.1, end))
        cues.append((current_start, bounded_end, current_text))
        current_text = ""

    for item in ordered:
        timestamp = max(0.0, float(item.media_timestamp or 0.0))
        text = _clean_caption_candidate(item)
        if not text:
            close(max(current_start + 0.1, timestamp - half_interval))
            continue
        if not current_text:
            current_text = text
            current_start = max(0.0, timestamp - half_interval)
            last_seen = timestamp
            continue
        if _caption_text_similar(current_text, text):
            if (
                len(_caption_key(text)) > len(_caption_key(current_text))
                or (
                    _caption_key(text) == _caption_key(current_text)
                    and len(text) > len(current_text)
                )
            ):
                current_text = text
            last_seen = timestamp
            continue
        close(max(current_start + 0.1, timestamp - half_interval))
        current_text = text
        current_start = max(0.0, timestamp - half_interval)
        last_seen = timestamp
    close(min(duration, last_seen + half_interval))

    merged: list[tuple[float, float, str]] = []
    for cue in cues:
        if (
            merged
            and _caption_key(merged[-1][2]) == _caption_key(cue[2])
            and cue[0] - merged[-1][1] <= interval_seconds
        ):
            previous = merged[-1]
            merged[-1] = (previous[0], cue[1], previous[2])
        else:
            merged.append(cue)
    return merged


def _clean_caption_candidate(result: OcrResult) -> str:
    if result.blocks:
        candidates: list[tuple[float, float, float, float, str]] = []
        for block in result.blocks:
            box = block.bounding_box
            value = html.unescape(block.text).strip()
            key = _caption_key(value)
            if not box or len(box) < 4 or len(key) < 2:
                continue
            left, top, right, bottom = (float(item) for item in box[:4])
            height = abs(bottom - top)
            center_y = (top + bottom) / 2
            confidence = float(block.confidence or 0.0)
            if center_y < 120 or height < 20 or confidence < 0.55:
                continue
            score = (
                center_y
                + min(height, 140.0) * 1.25
                + min(len(key), 30) * 1.5
                + confidence * 20
            )
            candidates.append((score, center_y, height, left, value))
        if not candidates:
            return ""
        winner = max(candidates, key=lambda item: item[0])
        vertical_window = max(35.0, winner[2] * 1.5)
        selected = [
            item
            for item in candidates
            if abs(item[1] - winner[1]) <= vertical_window
            and item[2] >= winner[2] * 0.5
        ]
        selected.sort(key=lambda item: (item[1], item[3]))
        return " ".join(dict.fromkeys(item[4] for item in selected)).strip()

    source = result.text
    lines = []
    for raw in re.split(r"[\r\n]+", source):
        value = html.unescape(raw).strip()
        value = re.sub(r"\s+", " ", value)
        if len(_caption_key(value)) >= 2:
            lines.append(value)
    return " ".join(dict.fromkeys(lines)).strip()


def _caption_key(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value.casefold())


def _caption_text_similar(left: str, right: str) -> bool:
    left_key = _caption_key(left)
    right_key = _caption_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    if shorter in longer and len(shorter) / len(longer) >= 0.72:
        return True
    return SequenceMatcher(None, left_key, right_key, autojunk=False).ratio() >= 0.84


async def _ensure_subtitle_active(cancel_check: CancelCheck) -> None:
    if process_registry.shutting_down:
        raise PipelineError("backend_shutdown", "应用正在关闭，字幕抓取已中断")
    value = cancel_check()
    if inspect.isawaitable(value):
        value = await value
    if value:
        raise PipelineError(
            "PROCESS_CANCELLED",
            "字幕抓取已取消",
            stage="SUBTITLE_IMPORT",
        )


def import_subtitle(
    path: Path,
    *,
    language: str | None = "zh",
    provider: str = "imported-subtitle",
) -> SubtitleImport:
    try:
        if path.stat().st_size > MAX_SUBTITLE_BYTES:
            raise PipelineError("SUBTITLE_TOO_LARGE", "字幕文件超过 64 MiB")
        payload = path.read_bytes()
    except OSError as exc:
        raise PipelineError(
            "SUBTITLE_READ_FAILED",
            "无法读取已导入的字幕文件",
            technical_detail=str(exc),
        ) from exc
    text, encoding = _decode_subtitle(payload)
    suffix = path.suffix.casefold()
    if suffix in {".srt", ".vtt"}:
        cues = _parse_timed_text(text)
        subtitle_format = suffix.removeprefix(".")
    elif suffix in {".ass", ".ssa"}:
        cues = _parse_ass(text)
        subtitle_format = suffix.removeprefix(".")
    elif suffix == ".json3":
        cues = _parse_json3(text)
        subtitle_format = "json3"
    elif suffix == ".txt":
        cues = _parse_plain_text(text)
        subtitle_format = "txt"
    else:
        raise PipelineError(
            "SUBTITLE_FORMAT_UNSUPPORTED",
            "字幕格式不受支持；请选择 SRT、VTT、ASS、SSA 或 TXT",
        )
    segments = [
        AsrSegment(
            segment_id=f"subtitle-{index:06d}",
            start_time=start,
            end_time=end,
            text=content,
            confidence=None,
            language=language,
            provider=provider,
            model="not-applicable",
            chunk_index=0,
            raw_response_reference=str(path),
        )
        for index, (start, end, content) in enumerate(cues, 1)
        if content.strip()
    ]
    if not segments:
        raise PipelineError(
            "SUBTITLE_EMPTY",
            "字幕文件中没有可导入的正文",
        )
    return SubtitleImport(
        segments=segments,
        encoding=encoding,
        subtitle_format=subtitle_format,
    )


def _load_subtitle_acquisition(
    path: Path,
    *,
    source: str | None = None,
) -> SubtitleAcquisition | None:
    try:
        payload = read_json(path)
        if not isinstance(payload, dict) or (source is not None and payload.get("source_url") != source):
            return None
        subtitle_path = Path(str(payload.get("path") or ""))
        if (not subtitle_path.resolve(strict=False).is_relative_to(path.parent.parent.parent.resolve())
                or not subtitle_path.is_file() or subtitle_path.suffix.casefold() not in SUBTITLE_EXTENSIONS
                or not 0 < subtitle_path.stat().st_size <= MAX_SUBTITLE_BYTES):
            return None
        subtitle_bytes = subtitle_path.read_bytes()
        expected_size = int(payload.get("size_bytes") or 0)
        expected_sha256 = str(payload.get("sha256") or "").casefold()
        if expected_size != len(subtitle_bytes) or hashlib.sha256(subtitle_bytes).hexdigest() != expected_sha256:
            return None
        import_subtitle(subtitle_path)
        return SubtitleAcquisition(
            path=str(subtitle_path.resolve()), language=_optional_text(payload.get("language")),
            source_kind=str(payload.get("source_kind") or "unknown"),
            subtitle_format=str(payload.get("subtitle_format") or subtitle_path.suffix.casefold()[1:]),
            sha256=expected_sha256, size_bytes=expected_size, manifest_path=str(path),
            provider=str(payload.get("provider") or "yt-dlp-subtitle"),
        )
    except (OSError, TypeError, ValueError, PipelineError):
        return None


def _subtitle_candidate_rank(path: Path, metadata: dict[str, Any]) -> tuple[int, int, str]:
    language = (_subtitle_language(path) or "").casefold()
    priorities = {
        "zh-hans": 0,
        "zh-cn": 1,
        "zh-hant": 2,
        "zh-tw": 3,
        "zh-hk": 4,
        "zh": 5,
        "en": 20,
    }
    language_rank = priorities.get(language)
    if language_rank is None:
        language_rank = 10 if language.startswith("zh") else 21 if language.startswith("en") else 99
    source_kind = _subtitle_source_kind(language, metadata)
    source_rank = {"manual": 0, "automatic": 1, "unknown": 2}.get(source_kind, 2)
    return language_rank, source_rank, path.name.casefold()


def _subtitle_tracks(metadata: dict, priorities: tuple[str, ...]) -> list[tuple[str, str]]:
    tracks = []
    order = {language.casefold(): index for index, language in enumerate(priorities)}
    for prefix in ("zh", "en"):
        for key, kind in (("subtitles", "manual"), ("automatic_captions", "automatic")):
            source = metadata.get(key)
            if isinstance(source, dict):
                candidates = [lang for lang, entries in source.items()
                              if isinstance(lang, str) and lang.casefold().startswith(prefix) and entries]
                tracks.extend((lang, kind) for lang in sorted(
                    candidates, key=lambda lang: (order.get(lang.casefold(), 99), lang.casefold())))
    return tracks


def _select_available_language(
    metadata: dict[str, Any],
    priorities: tuple[str, ...] = DEFAULT_SUBTITLE_LANGUAGES,
) -> str | None:
    manual = metadata.get("subtitles")
    automatic = metadata.get("automatic_captions")
    manual_languages = set(manual) if isinstance(manual, dict) else set()
    automatic_languages = set(automatic) if isinstance(automatic, dict) else set()
    for candidates, prefix in (
        (manual_languages, "zh"),
        (automatic_languages, "zh"),
        (manual_languages, "en"),
        (automatic_languages, "en"),
    ):
        exact = next(
            (
                language
                for preferred in priorities
                for language in candidates
                if language.casefold() == preferred.casefold()
                and language.casefold().startswith(prefix)
            ),
            None,
        )
        if exact is not None:
            return exact
        fallback = next(
            (
                language
                for language in sorted(candidates, key=str.casefold)
                if language.casefold().startswith(prefix)
            ),
            None,
        )
        if fallback is not None:
            return fallback
    return None


def _subtitle_language(path: Path) -> str | None:
    prefix = "source."
    stem = path.stem
    return stem[len(prefix) :] if stem.casefold().startswith(prefix) else None


def _subtitle_source_kind(language: str | None, metadata: dict[str, Any]) -> str:
    if not language:
        return "unknown"
    subtitles = metadata.get("subtitles")
    automatic = metadata.get("automatic_captions")
    if isinstance(subtitles, dict) and language in subtitles:
        return "manual"
    if isinstance(automatic, dict) and language in automatic:
        return "automatic"
    return "unknown"


def _source_kind_label(value: str) -> str:
    return {"manual": "人工", "automatic": "自动", "unknown": ""}.get(value, "")


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _decode_subtitle(payload: bytes) -> tuple[str, str]:
    candidates = (
        ("utf-8-sig", payload.startswith(b"\xef\xbb\xbf")),
        ("utf-16", payload.startswith((b"\xff\xfe", b"\xfe\xff"))),
        ("utf-8", True),
        ("gb18030", True),
    )
    for encoding, enabled in candidates:
        if not enabled:
            continue
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise PipelineError(
        "SUBTITLE_ENCODING_UNSUPPORTED",
        "字幕编码无法识别；请使用 UTF-8、UTF-16 或 GB18030",
    )


def _parse_timed_text(text: str) -> list[tuple[float | None, float | None, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[tuple[float | None, float | None, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = line.split("-->", 1)
        start = _parse_timestamp(left.strip())
        end_token = (right.strip().split(maxsplit=1) or [""])[0]
        end = _parse_timestamp(end_token)
        index += 1
        content_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            content_lines.append(lines[index].strip())
            index += 1
        content = _clean_markup("\n".join(content_lines))
        if start is not None and end is not None and end + 1e-6 < start:
            raise PipelineError("SUBTITLE_TIMESTAMP_INVALID", "字幕结束时间早于开始时间")
        if content:
            cues.append((start, end, content))
        index += 1
    return cues


def _parse_ass(text: str) -> list[tuple[float | None, float | None, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    in_events = False
    fields = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
    cues: list[tuple[float | None, float | None, str]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("["):
            in_events = line.casefold() == "[events]"
            continue
        if not in_events:
            continue
        if line.casefold().startswith("format:"):
            fields = [item.strip().casefold() for item in line.split(":", 1)[1].split(",")]
            continue
        if not line.casefold().startswith("dialogue:"):
            continue
        values = line.split(":", 1)[1].split(",", max(0, len(fields) - 1))
        if len(values) != len(fields):
            continue
        event = dict(zip(fields, values, strict=True))
        start = _parse_timestamp(event.get("start", "").strip())
        end = _parse_timestamp(event.get("end", "").strip())
        content = _clean_markup(
            re.sub(r"\{[^{}]*\}", "", event.get("text", "")).replace(r"\N", "\n").replace(r"\n", "\n")
        )
        if start is not None and end is not None and end + 1e-6 < start:
            raise PipelineError("SUBTITLE_TIMESTAMP_INVALID", "字幕结束时间早于开始时间")
        if content:
            cues.append((start, end, content))
    return cues


def _parse_json3(text: str) -> list[tuple[float, float, str]]:
    try:
        payload = json.loads(text)
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            raise ValueError("missing events")
        cues = []
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("segs"), list):
                continue
            start = float(event.get("tStartMs", 0)) / 1000
            end = start + float(event.get("dDurationMs", 0)) / 1000
            content = _clean_markup("".join(str(seg.get("utf8") or "")
                                           for seg in event["segs"] if isinstance(seg, dict)))
            if content and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end:
                cues.append((start, end, content))
        return cues
    except (ValueError, TypeError) as exc:
        raise PipelineError("SUBTITLE_FORMAT_INVALID", "JSON3 字幕格式无效") from exc


def _parse_plain_text(text: str) -> list[tuple[float | None, float | None, str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [
        _clean_markup(block)
        for block in re.split(r"\n\s*\n|\n", normalized)
        if block.strip()
    ]
    return [(None, None, block) for block in blocks if block]


def _parse_timestamp(value: str) -> float | None:
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours = "0"
            minutes, seconds = parts
        else:
            return None
        result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _clean_markup(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value)
    cleaned = html.unescape(without_tags)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    return cleaned.strip()
