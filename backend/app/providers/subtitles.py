from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import PipelineError
from app.providers.asr import AsrSegment
from app.providers.process import CancelCheck, ExternalProcessRunner
from app.storage.workspace import WorkspacePaths, read_json, write_json


SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".txt"}
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
        manifest_path = paths.metadata / "subtitle_acquisition.json"
        reusable = _load_subtitle_acquisition(manifest_path, source=source)
        if reusable is not None:
            progress(1.0, f"复用已抓取字幕：{reusable.language or '未知语言'}")
            return reusable

        metadata = read_json(paths.metadata / "ytdlp.json")
        if not isinstance(metadata, dict):
            metadata = await self._load_metadata(
                source,
                paths,
                cancel_check=cancel_check,
            )
        selected_language = _select_available_language(metadata, self.languages)
        if selected_language is None:
            raise PipelineError(
                "SUBTITLE_NOT_FOUND",
                "视频链接没有可用的中文或英文字幕（已同时检查人工字幕和自动字幕）",
            )

        destination = paths.source / "subtitles"
        destination.mkdir(parents=True, exist_ok=True)
        output_template = str(destination / "source.%(ext)s")
        try:
            result = await self.runner.run(
                [
                    self.executable,
                    "--newline",
                    "--no-playlist",
                    "--no-warnings",
                    "--skip-download",
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    selected_language,
                    "--sub-format",
                    "srt/vtt/ass/best",
                    "--output",
                    output_template,
                    source,
                ],
                cwd=paths.root,
                log_dir=paths.logs,
                log_name="ytdlp_subtitles",
                timeout_seconds=get_settings().media_process_timeout_seconds,
                cancel_check=cancel_check,
                stdout_callback=lambda line: progress(None, line[:300]),
                stderr_callback=lambda line: progress(None, line[:300]),
            )
        except PipelineError as exc:
            if exc.code in {"PROCESS_CANCELLED", "PROCESS_TIMEOUT", "backend_shutdown"}:
                raise
            code = "YTDLP_UNAVAILABLE" if exc.code == "PROCESS_UNAVAILABLE" else "SUBTITLE_FETCH_FAILED"
            retryable = exc.retryable or "429" in str(exc.technical_detail or "")
            raise PipelineError(
                code,
                (
                    "找不到 yt-dlp，无法从视频链接抓取字幕"
                    if code == "YTDLP_UNAVAILABLE"
                    else "yt-dlp 从视频链接抓取字幕失败"
                ),
                technical_detail=exc.technical_detail,
                retryable=retryable,
                log_path=exc.log_path,
            ) from exc

        candidates = [
            path
            for path in destination.glob("source.*")
            if path.is_file()
            and path.suffix.casefold() in SUBTITLE_EXTENSIONS
            and 0 < path.stat().st_size <= MAX_SUBTITLE_BYTES
        ]
        if not candidates:
            raise PipelineError(
                "SUBTITLE_NOT_FOUND",
                "视频链接没有可用的中文或英文字幕（已同时尝试人工字幕和自动字幕）",
                log_path=str(result.stderr_log),
            )

        selected = min(candidates, key=lambda path: _subtitle_candidate_rank(path, metadata))
        language = _subtitle_language(selected)
        source_kind = _subtitle_source_kind(language, metadata)
        payload = selected.read_bytes()
        acquisition = SubtitleAcquisition(
            path=str(selected.resolve()),
            language=language,
            source_kind=source_kind,
            subtitle_format=selected.suffix.casefold().removeprefix("."),
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            manifest_path=str(manifest_path),
        )
        write_json(
            manifest_path,
            {
                "schema_version": 1,
                "source_url": source,
                "language_priority": list(self.languages),
                **acquisition.to_dict(),
            },
        )
        progress(
            1.0,
            f"已抓取{_source_kind_label(source_kind)}字幕：{language or '未知语言'}",
        )
        return acquisition

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
                    "--no-playlist",
                    "--no-warnings",
                    source,
                ],
                cwd=paths.root,
                log_dir=paths.logs,
                log_name="ytdlp_subtitle_metadata",
                timeout_seconds=get_settings().media_process_timeout_seconds,
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
        except json.JSONDecodeError as exc:
            raise PipelineError(
                "SUBTITLE_FETCH_FAILED",
                "yt-dlp 返回了无法解析的字幕信息",
                technical_detail=str(exc),
                log_path=str(result.stdout_log),
            ) from exc
        write_json(paths.metadata / "ytdlp.json", metadata)
        return metadata


def import_subtitle(
    path: Path,
    *,
    language: str | None = "zh",
    provider: str = "imported-subtitle",
) -> SubtitleImport:
    try:
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
    payload = read_json(path)
    if not isinstance(payload, dict):
        return None
    if source is not None and payload.get("source_url") != source:
        return None
    subtitle_path = Path(str(payload.get("path") or ""))
    if (
        not subtitle_path.is_file()
        or subtitle_path.suffix.casefold() not in SUBTITLE_EXTENSIONS
        or not 0 < subtitle_path.stat().st_size <= MAX_SUBTITLE_BYTES
    ):
        return None
    try:
        subtitle_bytes = subtitle_path.read_bytes()
        expected_size = int(payload.get("size_bytes") or 0)
        expected_sha256 = str(payload.get("sha256") or "").casefold()
        if (
            expected_size != len(subtitle_bytes)
            or not expected_sha256
            or hashlib.sha256(subtitle_bytes).hexdigest() != expected_sha256
        ):
            return None
        return SubtitleAcquisition(
            path=str(subtitle_path.resolve()),
            language=_optional_text(payload.get("language")),
            source_kind=str(payload.get("source_kind") or "unknown"),
            subtitle_format=str(
                payload.get("subtitle_format")
                or subtitle_path.suffix.casefold().removeprefix(".")
            ),
            sha256=expected_sha256,
            size_bytes=expected_size,
            manifest_path=str(path),
            provider=str(payload.get("provider") or "yt-dlp-subtitle"),
        )
    except (OSError, TypeError, ValueError):
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
        end_token = right.strip().split(maxsplit=1)[0]
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
    return result if result >= 0 else None


def _clean_markup(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value)
    cleaned = html.unescape(without_tags)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    return cleaned.strip()
