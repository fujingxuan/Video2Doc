from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.errors import PipelineError
from app.documents.ir import (
    Document,
    DocumentBlock,
    DocumentSection,
    MediaSource,
    SourceReference,
    TimestampAnchor,
)


def build_document(
    fusion_timeline: dict[str, Any],
    *,
    title: str,
    source: str,
    source_type: str,
    media_info: dict[str, Any],
    models: dict[str, str | None],
    processing: dict[str, Any],
    artifact_references: dict[str, str] | None = None,
    document_id: str = "video2doc-document",
    section_seconds: float = 300.0,
    image_min_gap_seconds: float = 8.0,
) -> Document:
    events = fusion_timeline.get("events")
    if not isinstance(events, list):
        raise PipelineError("DOCUMENT_INPUT_INVALID", "融合时间线缺少事件列表")
    if source_type not in {"URL", "LOCAL_FILE"}:
        raise PipelineError("DOCUMENT_INPUT_INVALID", "文档来源类型无效")
    duration = _number(media_info.get("duration"))
    sections: list[DocumentSection] = []
    section_by_bucket: dict[int, DocumentSection] = {}
    untimed_bucket = 1_000_000_000
    last_image_time: float | None = None
    block_index = 0

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        text = str(event.get("text") or "").strip()
        start = _number(event.get("start_time"))
        end = _number(event.get("end_time"))
        if start is not None and duration is not None:
            start = min(max(start, 0.0), duration)
        if end is not None and duration is not None:
            end = min(max(end, start or 0.0), duration)
        timestamp = TimestampAnchor(start_seconds=start, end_seconds=end)
        bucket = int(start // section_seconds) if start is not None else untimed_bucket
        section = section_by_bucket.get(bucket)
        if section is None:
            section = DocumentSection(
                section_id=f"section-{len(sections) + 1:04d}",
                title=_section_title(start, text, event_type),
                timestamp=timestamp,
            )
            section_by_bucket[bucket] = section
            sections.append(section)

        event_id = str(event.get("event_id") or f"event-{block_index + 1:06d}")
        block: DocumentBlock | None = None
        if event_type == "asr_segment" and text:
            segment_ids = [
                str(item) for item in event.get("related_asr_segment_ids", []) if str(item)
            ]
            segment_id = segment_ids[0] if segment_ids else event_id.removeprefix("asr:")
            block = DocumentBlock(
                block_id=f"transcript-{block_index + 1:06d}",
                block_type="transcript",
                timestamp=timestamp,
                original_text=text,
                uncertain=bool(event.get("uncertain")),
                source_references=[
                    _source_reference(
                        event,
                        source_id=event_id,
                        source_type="asr_segment",
                        timestamp=timestamp,
                        asr_segment_id=segment_id,
                    )
                ],
                metadata={"quality": event.get("quality") or {}},
            )
        elif event_type == "ocr_event" and text:
            ocr_id = event_id.removeprefix("ocr:")
            block = DocumentBlock(
                block_id=f"visual-{block_index + 1:06d}",
                block_type=_visual_block_type(text, event),
                timestamp=timestamp,
                original_text=text,
                uncertain=bool(event.get("uncertain")),
                source_references=[
                    _source_reference(
                        event,
                        source_id=event_id,
                        source_type="ocr_event",
                        timestamp=timestamp,
                        ocr_event_id=ocr_id,
                    )
                ],
                metadata={"quality": event.get("quality") or {}},
            )
        elif event_type == "frame_event":
            if start is not None and last_image_time is not None:
                if start - last_image_time < image_min_gap_seconds:
                    continue
            image_path = _frame_image_path(event)
            if image_path:
                frame_ids = [
                    str(item) for item in event.get("related_frame_ids", []) if str(item)
                ]
                frame_id = frame_ids[0] if frame_ids else event_id.removeprefix("frame:")
                block = DocumentBlock(
                    block_id=f"image-{block_index + 1:06d}",
                    block_type="image",
                    timestamp=timestamp,
                    original_text="",
                    image_source_path=image_path,
                    image_alt=f"相关画面 {frame_id}",
                    source_references=[
                        _source_reference(
                            event,
                            source_id=event_id,
                            source_type="frame",
                            timestamp=timestamp,
                            frame_id=frame_id,
                            artifact_reference=image_path,
                        )
                    ],
                    metadata={
                        "quality": event.get("quality") or {},
                        "extraction_reason": (event.get("source") or {}).get(
                            "extraction_reason"
                        ),
                    },
                )
                last_image_time = start
        if block is not None:
            section.blocks.append(block)
            block_index += 1

    sections = [section for section in sections if section.blocks]
    if not any(block.block_type == "transcript" for section in sections for block in section.blocks):
        raise PipelineError("DOCUMENT_EMPTY_TRANSCRIPT", "标准化转写没有可生成文档的正文")
    language = _document_language(events)
    return Document(
        document_id=document_id,
        title=_single_line(title)[:300] or "Untitled",
        source=MediaSource(source=source, source_type=source_type),
        media_duration_seconds=duration,
        language=language,
        models=models,
        processing=processing,
        sections=sections,
        artifact_references=artifact_references or {},
    )


def _source_reference(
    event: dict[str, Any],
    *,
    source_id: str,
    source_type: str,
    timestamp: TimestampAnchor,
    asr_segment_id: str | None = None,
    ocr_event_id: str | None = None,
    frame_id: str | None = None,
    artifact_reference: str | None = None,
) -> SourceReference:
    raw_references = [
        str(item)
        for item in event.get("original_artifact_references", [])
        if isinstance(item, str) and item
    ]
    return SourceReference(
        source_id=source_id,
        source_type=source_type,
        timestamp=timestamp,
        asr_segment_id=asr_segment_id,
        ocr_event_id=ocr_event_id,
        frame_id=frame_id,
        artifact_reference=artifact_reference,
        raw_response_references=raw_references,
        metadata={
            "related_frame_ids": event.get("related_frame_ids") or [],
            "related_asr_segment_ids": event.get("related_asr_segment_ids") or [],
        },
    )


def _section_title(start: float | None, text: str, event_type: str) -> str:
    stamp = _format_timestamp(start) if start is not None else "未定时"
    if text:
        cleaned = re.sub(r"[\r\n#*_`]+", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            excerpt = cleaned[:44].rstrip("，。；：,.!?！？ ")
            return f"{stamp} {excerpt}"
    kind = {
        "asr_segment": "讲者内容",
        "ocr_event": "画面文字",
        "frame_event": "相关画面",
    }.get(event_type, "媒体内容")
    return f"{stamp} {kind}"


def _visual_block_type(text: str, event: dict[str, Any]) -> str:
    if "```" in text or re.search(r"(^|\n)\s*(def |class |function |SELECT |#include)", text):
        return "code"
    quality = event.get("quality")
    if isinstance(quality, dict) and quality.get("block_type") == "quote":
        return "quote"
    return "visual_text"


def _frame_image_path(event: dict[str, Any]) -> str | None:
    references = event.get("original_artifact_references")
    if not isinstance(references, list):
        return None
    candidates = [str(item) for item in references if isinstance(item, str) and item]
    if not candidates:
        return None
    normalized = [item for item in candidates if "normalized" in Path(item).parts]
    return (normalized or candidates)[-1]


def _document_language(events: list[Any]) -> str | None:
    for event in events:
        if not isinstance(event, dict):
            continue
        quality = event.get("quality")
        if isinstance(quality, dict) and quality.get("language"):
            return str(quality["language"])
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _format_timestamp(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
