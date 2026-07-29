from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import PipelineError
from app.providers.asr import AsrSegment


@dataclass(frozen=True, slots=True)
class SubtitleImport:
    segments: list[AsrSegment]
    encoding: str
    subtitle_format: str


def import_subtitle(path: Path) -> SubtitleImport:
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
            language="zh",
            provider="imported-subtitle",
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
