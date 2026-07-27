from __future__ import annotations

from typing import Any

from app.core.errors import PipelineError
from app.storage.workspace import WorkspacePaths, write_json


def build_fusion_timeline(
    asr_payload: dict[str, Any],
    ocr_timeline: dict[str, Any],
    frame_manifest: dict[str, Any],
    *,
    media_duration: float | None,
    association_window_seconds: float = 8.0,
) -> dict[str, Any]:
    if association_window_seconds < 0:
        raise PipelineError("FUSION_CONFIG_INVALID", "融合关联窗口不能小于 0")
    asr_segments = asr_payload.get("segments")
    ocr_events = ocr_timeline.get("events")
    frames = frame_manifest.get("frames")
    if not isinstance(asr_segments, list):
        raise PipelineError("FUSION_INPUT_INVALID", "标准化 ASR 结果缺少 segments")
    if not isinstance(ocr_events, list):
        raise PipelineError("FUSION_INPUT_INVALID", "OCR 时间线缺少 events")
    if not isinstance(frames, list):
        raise PipelineError("FUSION_INPUT_INVALID", "帧清单缺少 frames")

    asr_events: list[dict[str, Any]] = []
    asr_by_id: dict[str, dict[str, Any]] = {}
    for index, segment in enumerate(asr_segments):
        if not isinstance(segment, dict) or not str(segment.get("text") or "").strip():
            continue
        segment_id = str(segment.get("segment_id") or f"segment_{index:06d}")
        event = {
            "event_id": f"asr:{segment_id}",
            "event_type": "asr_segment",
            "start_time": _number_or_none(segment.get("start_time")),
            "end_time": _number_or_none(segment.get("end_time")),
            "source": {
                "kind": "audio",
                "provider": segment.get("provider") or asr_payload.get("provider"),
                "model": segment.get("model") or asr_payload.get("model"),
                "chunk_index": segment.get("chunk_index"),
            },
            "text": str(segment["text"]),
            "original_artifact_references": [
                reference
                for reference in [segment.get("raw_response_reference")]
                if isinstance(reference, str) and reference
            ],
            "quality": {
                "confidence": _number_or_none(segment.get("confidence")),
                "language": segment.get("language"),
                "word_count": len(segment.get("words") or []),
            },
            "related_frame_ids": [],
            "related_asr_segment_ids": [segment_id],
            "uncertain": bool(
                segment.get("start_time") is None or segment.get("end_time") is None
            ),
        }
        asr_events.append(event)
        asr_by_id[segment_id] = event

    unified_ocr_events: list[dict[str, Any]] = []
    ocr_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(ocr_events):
        if not isinstance(raw, dict):
            continue
        event_id = str(raw.get("event_id") or f"ocr_{index:06d}")
        related_asr = _nearby_asr_ids(
            raw, asr_segments, association_window_seconds
        )
        source_frames = [
            str(item) for item in raw.get("source_frame_ids", []) if str(item)
        ]
        event = {
            "event_id": f"ocr:{event_id}",
            "event_type": "ocr_event",
            "start_time": _number_or_none(raw.get("start_time")),
            "end_time": _number_or_none(raw.get("end_time")),
            "source": {
                "kind": "video_frame",
                "provider": raw.get("provider"),
                "model": raw.get("model"),
                "change_type": raw.get("change_type"),
                "duplicate_group": raw.get("duplicate_group"),
            },
            "text": str(raw.get("text") or ""),
            "original_artifact_references": [
                str(item)
                for item in raw.get("raw_artifact_references", [])
                if str(item)
            ],
            "quality": {
                "confidence": _number_or_none(raw.get("confidence")),
                "language": raw.get("language"),
                "block_count": len(raw.get("blocks") or []),
            },
            "related_frame_ids": source_frames,
            "related_asr_segment_ids": related_asr,
            "uncertain": bool(raw.get("uncertain")),
        }
        unified_ocr_events.append(event)
        ocr_by_id[event_id] = event
        for segment_id in related_asr:
            asr_event = asr_by_id.get(segment_id)
            if asr_event is not None:
                asr_event["related_frame_ids"] = sorted(
                    set(asr_event["related_frame_ids"] + source_frames)
                )

    frame_events: list[dict[str, Any]] = []
    for raw in frames:
        if not isinstance(raw, dict) or not raw.get("kept"):
            continue
        frame_id = str(raw.get("frame_id") or "")
        timestamp = _number_or_none(raw.get("media_timestamp"))
        related_ocr = [
            event_id
            for event_id, event in ocr_by_id.items()
            if frame_id in event["related_frame_ids"]
        ]
        related_asr = _nearby_asr_ids(
            {"start_time": timestamp, "end_time": timestamp},
            asr_segments,
            association_window_seconds,
        )
        frame_events.append(
            {
                "event_id": f"frame:{frame_id}",
                "event_type": "frame_event",
                "start_time": timestamp,
                "end_time": timestamp,
                "source": {
                    "kind": "video_frame",
                    "extraction_reason": raw.get("extraction_reason"),
                    "scene_change_score": raw.get("scene_change_score"),
                },
                "text": "",
                "original_artifact_references": [
                    str(raw.get("original_frame_path") or ""),
                    str(raw.get("normalized_image_path") or ""),
                ],
                "quality": {
                    "sha256": raw.get("sha256"),
                    "perceptual_hash": raw.get("perceptual_hash"),
                    "ocr_event_ids": related_ocr,
                },
                "related_frame_ids": [frame_id],
                "related_asr_segment_ids": related_asr,
                "uncertain": False,
            }
        )

    events = sorted(
        [*asr_events, *unified_ocr_events, *frame_events],
        key=lambda item: (
            item["start_time"] is None,
            item["start_time"] if item["start_time"] is not None else float("inf"),
            {"frame_event": 0, "ocr_event": 1, "asr_segment": 2}.get(
                item["event_type"], 9
            ),
            item["event_id"],
        ),
    )
    payload = {
        "schema_version": 1,
        "media_duration": media_duration,
        "association_window_seconds": association_window_seconds,
        "event_count": len(events),
        "counts": {
            "asr_segment": len(asr_events),
            "ocr_event": len(unified_ocr_events),
            "frame_event": len(frame_events),
        },
        "events": events,
    }
    validate_fusion_timeline(payload)
    return payload


def validate_fusion_timeline(payload: dict[str, Any]) -> None:
    events = payload.get("events")
    if not isinstance(events, list):
        raise PipelineError("FUSION_VALIDATE_FAILED", "融合结果缺少事件列表")
    duration = _number_or_none(payload.get("media_duration"))
    seen: set[str] = set()
    previous_time = -1.0
    reached_untimed = False
    for event in events:
        if not isinstance(event, dict):
            raise PipelineError("FUSION_VALIDATE_FAILED", "融合事件格式错误")
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen:
            raise PipelineError("FUSION_VALIDATE_FAILED", "融合事件 ID 为空或重复")
        seen.add(event_id)
        start = _number_or_none(event.get("start_time"))
        end = _number_or_none(event.get("end_time"))
        if start is None:
            reached_untimed = True
            continue
        if reached_untimed or start + 1e-6 < previous_time:
            raise PipelineError("FUSION_VALIDATE_FAILED", "融合事件时间顺序不稳定")
        if start < -1e-6 or (duration is not None and start > duration + 0.1):
            raise PipelineError("FUSION_VALIDATE_FAILED", "融合事件时间超出媒体范围")
        if end is not None and end + 1e-6 < start:
            raise PipelineError("FUSION_VALIDATE_FAILED", "融合事件结束时间早于开始时间")
        if end is not None and duration is not None and end > duration + 0.1:
            raise PipelineError("FUSION_VALIDATE_FAILED", "融合事件结束时间超出媒体范围")
        previous_time = start


def save_fusion_artifacts(
    paths: WorkspacePaths,
    payload: dict[str, Any],
    *,
    asr_path: str,
    ocr_path: str,
    frames_path: str,
) -> dict[str, str]:
    input_path = paths.fusion / "input.json"
    timeline_path = paths.fusion / "timeline.json"
    preview_path = paths.fusion / "preview.json"
    write_json(
        input_path,
        {
            "schema_version": 1,
            "asr_artifact": asr_path,
            "ocr_artifact": ocr_path,
            "frames_artifact": frames_path,
        },
    )
    write_json(timeline_path, payload)
    write_json(
        preview_path,
        {
            "event_count": payload["event_count"],
            "counts": payload["counts"],
            "events": payload["events"][:200],
            "truncated": payload["event_count"] > 200,
        },
    )
    return {
        "input": str(input_path),
        "timeline": str(timeline_path),
        "preview": str(preview_path),
    }


def _nearby_asr_ids(
    event: dict[str, Any],
    segments: list[Any],
    window: float,
) -> list[str]:
    start = _number_or_none(event.get("start_time"))
    end = _number_or_none(event.get("end_time"))
    if start is None:
        return []
    end = start if end is None else end
    related: list[str] = []
    for index, raw in enumerate(segments):
        if not isinstance(raw, dict):
            continue
        asr_start = _number_or_none(raw.get("start_time"))
        asr_end = _number_or_none(raw.get("end_time"))
        if asr_start is None:
            continue
        asr_end = asr_start if asr_end is None else asr_end
        if asr_end >= start - window and asr_start <= end + window:
            related.append(str(raw.get("segment_id") or f"segment_{index:06d}"))
    return related


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
