from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.core.errors import PipelineError
from app.core.credentials import resolve_gateway_credential
from app.core.secrets import SecretStorageError
from app.providers.frames import (
    FrameExtractionResult,
    FrameRecord,
    perceptual_hash_distance,
)
from app.providers.ocr import (
    OcrBlock,
    OcrCancelCheck,
    OcrResult,
    PaddleXLayoutParsingOcrClient,
    UnifiedOcrGatewayClient,
)
from app.storage.workspace import WorkspacePaths, read_json, write_json


ProgressCallback = Callable[[float | None, str], None]


@dataclass(frozen=True, slots=True)
class OcrRunResult:
    results: tuple[OcrResult, ...]
    normalized_path: str
    raw_index_path: str
    preview_path: str
    processed_count: int
    reused_count: int
    elapsed_seconds: float


async def recognize_retained_frames(
    frames: FrameExtractionResult,
    *,
    ocr_config: dict[str, Any],
    attempt: int,
    paths: WorkspacePaths,
    cancel_check: OcrCancelCheck,
    progress: ProgressCallback,
    client: UnifiedOcrGatewayClient | PaddleXLayoutParsingOcrClient | None = None,
) -> OcrRunResult:
    kept = list(frames.kept)
    if not kept:
        raise PipelineError("OCR_NO_FRAMES", "没有可提交 OCR 的保留帧")
    model = str(ocr_config.get("model") or "").strip()
    base_url = str(ocr_config.get("base_url") or "").strip()
    language = str(ocr_config.get("language") or "ch").strip() or "ch"
    if not model or not base_url:
        raise PipelineError("OCR_CONFIG_INVALID", "任务快照中的 OCR Gateway 配置不完整")
    secret_env_name = str(ocr_config.get("secret_env_name") or "").strip()
    gateway_id = ocr_config.get("gateway_id")
    try:
        api_key = (
            resolve_gateway_credential(int(gateway_id))
            if gateway_id is not None
            else (os.getenv(secret_env_name, "").strip() if secret_env_name else "")
        )
    except (SecretStorageError, TypeError, ValueError) as exc:
        raise PipelineError(
            "OCR_GATEWAY_SECRET_MISSING",
            "无法读取 OCR Gateway 密钥",
            technical_detail=str(exc),
        ) from exc
    if (secret_env_name or ocr_config.get("secret_source") == "DIRECT") and not api_key:
        raise PipelineError(
            "OCR_GATEWAY_SECRET_MISSING",
            f"缺少 OCR Gateway 密钥环境变量 {secret_env_name}",
        )
    if client is None:
        protocol = str(ocr_config.get("provider") or "video2doc-ocr-http-v1")
        client_class = (
            PaddleXLayoutParsingOcrClient
            if protocol == "paddlex-layout-parsing-v1"
            else UnifiedOcrGatewayClient
        )
        if protocol not in {
            "video2doc-ocr-http-v1",
            "paddlex-layout-parsing-v1",
        }:
            raise PipelineError(
                "OCR_CONFIG_INVALID",
                f"不支持的 OCR Gateway 协议：{protocol}",
            )
        client = client_class(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=float(ocr_config.get("timeout_seconds", 120.0)),
            max_retries=int(ocr_config.get("max_retries", 1)),
        )

    attempt_raw = paths.ocr_raw / f"attempt_{attempt:04d}"
    normalized_frames = paths.ocr_normalized / "frames"
    error_dir = paths.ocr / "errors" / f"attempt_{attempt:04d}"
    attempt_raw.mkdir(parents=True, exist_ok=True)
    normalized_frames.mkdir(parents=True, exist_ok=True)
    error_dir.mkdir(parents=True, exist_ok=True)
    results: list[OcrResult] = []
    reused_count = 0
    raw_references: list[str] = []
    started = time.monotonic()

    for index, frame in enumerate(kept):
        fingerprint = _ocr_request_fingerprint(frame, ocr_config)
        normalized_frame_path = normalized_frames / f"{frame.frame_id}.json"
        reusable = _load_reusable_result(normalized_frame_path, fingerprint)
        if reusable is not None:
            result = reusable
            reused_count += 1
        else:
            raw_path = attempt_raw / f"{frame.frame_id}.json"
            try:
                result = await client.recognize(
                    Path(frame.normalized_image_path),
                    model=model,
                    raw_path=raw_path,
                    frame_id=frame.frame_id,
                    media_timestamp=frame.media_timestamp,
                    language=language,
                    cancel_check=cancel_check,
                )
            except PipelineError as exc:
                write_json(
                    error_dir / f"{frame.frame_id}.json",
                    {
                        "schema_version": 1,
                        "attempt": attempt,
                        "frame_id": frame.frame_id,
                        "media_timestamp": frame.media_timestamp,
                        "image_path": frame.normalized_image_path,
                        "error_code": exc.code,
                        "message": exc.user_message,
                        "technical_detail": exc.technical_detail,
                        "retryable": exc.retryable,
                    },
                )
                raise
            write_json(
                normalized_frame_path,
                {
                    "schema_version": 1,
                    "request_fingerprint": fingerprint,
                    "attempt": attempt,
                    "frame": frame.to_dict(),
                    "result": result.to_dict(),
                },
            )
        results.append(result)
        raw_references.append(result.raw_response_reference)
        progress(
            (index + 1) / len(kept),
            f"OCR 已完成 {index + 1}/{len(kept)} 帧，复用 {reused_count} 帧",
        )

    elapsed = time.monotonic() - started
    normalized_path = paths.ocr / "normalized.json"
    raw_index_path = paths.ocr / "raw.json"
    preview_path = paths.ocr / "preview.json"
    normalized_payload = {
        "schema_version": 1,
        "provider": results[0].provider,
        "model": model,
        "language": language,
        "frame_count": len(results),
        "reused_count": reused_count,
        "elapsed_seconds": elapsed,
        "frames": [item.to_dict() for item in results],
    }
    write_json(normalized_path, normalized_payload)
    write_json(
        raw_index_path,
        {
            **normalized_payload,
            "attempt": attempt,
            "provider_responses": sorted(set(raw_references)),
            "error_records": [
                str(item) for item in sorted(error_dir.glob("*.json")) if item.is_file()
            ],
        },
    )
    preview_text = "\n\n".join(
        f"[{item.media_timestamp:.3f}s] {item.text}"
        for item in results
        if item.media_timestamp is not None
    )
    write_json(
        preview_path,
        {
            "provider": results[0].provider,
            "model": model,
            "language": language,
            "frame_count": len(results),
            "reused_count": reused_count,
            "block_count": sum(len(item.blocks) for item in results),
            "text": preview_text[:20_000],
            "truncated": len(preview_text) > 20_000,
            "elapsed_seconds": elapsed,
        },
    )
    return OcrRunResult(
        tuple(results),
        str(normalized_path),
        str(raw_index_path),
        str(preview_path),
        len(results),
        reused_count,
        elapsed,
    )


def build_ocr_timeline(
    results: tuple[OcrResult, ...],
    frame_result: FrameExtractionResult,
    *,
    text_similarity_threshold: float = 0.88,
    incremental_similarity_threshold: float = 0.55,
    max_event_gap_seconds: float = 20.0,
) -> dict[str, Any]:
    frame_by_id = {item.frame_id: item for item in frame_result.records}
    ordered = sorted(
        (item for item in results if item.media_timestamp is not None),
        key=lambda item: float(item.media_timestamp or 0.0),
    )
    events: list[dict[str, Any]] = []
    for result in ordered:
        frame = frame_by_id.get(result.frame_id or "")
        if frame is None:
            raise PipelineError(
                "OCR_TIMELINE_INVALID",
                "OCR 结果引用了不存在的帧",
                technical_detail=f"frame_id={result.frame_id}",
            )
        blocks = _deduplicate_blocks(result.blocks)
        text = "\n".join(item.text for item in blocks).strip() or result.text
        timestamp = float(result.media_timestamp or 0.0)
        current = _new_event(len(events), result, frame, blocks, text)
        if not events:
            events.append(current)
            continue
        previous = events[-1]
        gap = timestamp - float(previous["last_seen_time"])
        text_similarity = _text_similarity(previous["text"], text)
        image_hamming = perceptual_hash_distance(
            str(previous["last_perceptual_hash"]), frame.perceptual_hash
        )
        spatial_similarity = _spatial_similarity(previous["blocks"], [item.to_dict() for item in blocks])
        stable_duplicate = (
            gap <= max_event_gap_seconds
            and text_similarity >= text_similarity_threshold
            and (spatial_similarity >= 0.45 or image_hamming <= 10)
            and not _meaningful_text_update(previous["text"], text)
        )
        if stable_duplicate:
            previous["end_time"] = timestamp
            previous["last_seen_time"] = timestamp
            previous["source_frame_ids"].append(frame.frame_id)
            previous["source_images"].append(frame.normalized_image_path)
            previous["raw_artifact_references"].append(result.raw_response_reference)
            previous["observations"].append(
                {
                    "frame_id": frame.frame_id,
                    "timestamp": timestamp,
                    "text_similarity": round(text_similarity, 6),
                    "spatial_similarity": round(spatial_similarity, 6),
                    "image_hamming_distance": image_hamming,
                }
            )
            previous["confidence"] = _combined_confidence(
                previous.get("confidence"), _average_confidence(blocks)
            )
            previous["last_perceptual_hash"] = frame.perceptual_hash
            continue
        if (
            gap <= max_event_gap_seconds
            and text_similarity >= incremental_similarity_threshold
            and image_hamming <= 20
        ):
            current["change_type"] = "incremental_update"
            current["previous_event_id"] = previous["event_id"]
            current["comparison"] = {
                "text_similarity": round(text_similarity, 6),
                "spatial_similarity": round(spatial_similarity, 6),
                "image_hamming_distance": image_hamming,
            }
        events.append(current)

    for event in events:
        event.pop("last_perceptual_hash", None)
        event["raw_artifact_references"] = sorted(
            set(event["raw_artifact_references"])
        )
        event["uncertain"] = bool(
            event["confidence"] is not None and event["confidence"] < 0.6
        ) or any(bool(block.get("uncertain")) for block in event["blocks"])
    duplicate_groups = sum(max(0, len(event["source_frame_ids"]) - 1) for event in events)
    return {
        "schema_version": 1,
        "event_count": len(events),
        "merged_observation_count": duplicate_groups,
        "events": events,
    }


def save_ocr_timeline(paths: WorkspacePaths, payload: dict[str, Any]) -> str:
    path = paths.ocr / "timeline.json"
    write_json(path, payload)
    return str(path)


def _ocr_request_fingerprint(frame: FrameRecord, ocr_config: dict[str, Any]) -> str:
    payload = {
        "schema_version": 1,
        "frame_sha256": frame.sha256,
        "provider": ocr_config.get("provider"),
        "base_url": ocr_config.get("base_url"),
        "model": ocr_config.get("model"),
        "language": ocr_config.get("language"),
        "timeout_seconds": ocr_config.get("timeout_seconds"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _load_reusable_result(path: Path, fingerprint: str) -> OcrResult | None:
    payload = read_json(path)
    if not payload or payload.get("request_fingerprint") != fingerprint:
        return None
    raw = payload.get("result")
    if not isinstance(raw, dict):
        return None
    raw_reference = raw.get("raw_response_reference")
    if not isinstance(raw_reference, str) or not Path(raw_reference).is_file():
        return None
    try:
        blocks = tuple(OcrBlock(**item) for item in raw.get("blocks", []))
        return OcrResult(
            provider=str(raw["provider"]),
            model=str(raw["model"]),
            text=str(raw["text"]),
            blocks=blocks,
            raw_response_reference=raw_reference,
            frame_id=raw.get("frame_id"),
            media_timestamp=raw.get("media_timestamp"),
            language=raw.get("language"),
            elapsed_seconds=raw.get("elapsed_seconds"),
            error=raw.get("error"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _deduplicate_blocks(blocks: tuple[OcrBlock, ...]) -> tuple[OcrBlock, ...]:
    retained: list[OcrBlock] = []
    for block in sorted(
        blocks,
        key=lambda item: (
            item.reading_order if item.reading_order is not None else 1_000_000,
            item.bounding_box or (),
        ),
    ):
        duplicate = False
        for existing in retained:
            if _normalized_text(existing.text) != _normalized_text(block.text):
                continue
            if existing.bounding_box is None or block.bounding_box is None:
                duplicate = True
                break
            if _box_iou(existing.bounding_box, block.bounding_box) >= 0.9:
                duplicate = True
                break
        if not duplicate:
            retained.append(block)
    return tuple(retained)


def _new_event(
    index: int,
    result: OcrResult,
    frame: FrameRecord,
    blocks: tuple[OcrBlock, ...],
    text: str,
) -> dict[str, Any]:
    timestamp = float(result.media_timestamp or 0.0)
    return {
        "event_id": f"ocr_{index:06d}",
        "event_type": "ocr_event",
        "start_time": timestamp,
        "end_time": timestamp,
        "first_seen_time": timestamp,
        "last_seen_time": timestamp,
        "provider": result.provider,
        "model": result.model,
        "language": result.language,
        "text": text,
        "blocks": [item.to_dict() for item in blocks],
        "confidence": _average_confidence(blocks),
        "duplicate_group": f"ocr_group_{index:06d}",
        "change_type": "appeared",
        "previous_event_id": None,
        "source_frame_ids": [frame.frame_id],
        "source_images": [frame.normalized_image_path],
        "raw_artifact_references": [result.raw_response_reference],
        "observations": [
            {
                "frame_id": frame.frame_id,
                "timestamp": timestamp,
                "text_similarity": 1.0,
                "spatial_similarity": 1.0,
                "image_hamming_distance": 0,
            }
        ],
        "last_perceptual_hash": frame.perceptual_hash,
        "uncertain": False,
    }


def _normalized_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def _text_similarity(left: str, right: str) -> float:
    left_key = _normalized_text(left)
    right_key = _normalized_text(right)
    if not left_key and not right_key:
        return 1.0
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key, autojunk=False).ratio()


def _meaningful_text_update(previous: str, current: str) -> bool:
    previous_key = _normalized_text(previous)
    current_key = _normalized_text(current)
    if not current_key:
        return False
    length_growth = max(0, len(current_key) - len(previous_key)) / max(1, len(previous_key))
    novel = len(set(current_key) - set(previous_key)) / max(1, len(set(current_key)))
    return length_growth > 0.12 or novel > 0.08


def _average_confidence(blocks: tuple[OcrBlock, ...]) -> float | None:
    values = [item.confidence for item in blocks if item.confidence is not None]
    return sum(values) / len(values) if values else None


def _combined_confidence(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return (left + right) / 2


def _spatial_similarity(
    left_blocks: list[dict[str, Any]], right_blocks: list[dict[str, Any]]
) -> float:
    left_boxes = [
        tuple(item["bounding_box"])
        for item in left_blocks
        if isinstance(item.get("bounding_box"), (list, tuple))
        and len(item["bounding_box"]) >= 4
    ]
    right_boxes = [
        tuple(item["bounding_box"])
        for item in right_blocks
        if isinstance(item.get("bounding_box"), (list, tuple))
        and len(item["bounding_box"]) >= 4
    ]
    if not left_boxes or not right_boxes:
        return 0.0
    scores: list[float] = []
    for left in left_boxes:
        scores.append(max((_box_iou(left, right) for right in right_boxes), default=0.0))
    return sum(scores) / len(scores)


def _box_iou(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) < 4 or len(right) < 4:
        return 0.0
    left_x1, left_y1, left_x2, left_y2 = left[:4]
    right_x1, right_y1, right_x2, right_y2 = right[:4]
    intersection_width = max(0.0, min(left_x2, right_x2) - max(left_x1, right_x1))
    intersection_height = max(0.0, min(left_y2, right_y2) - max(left_y1, right_y1))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, left_x2 - left_x1) * max(0.0, left_y2 - left_y1)
    right_area = max(0.0, right_x2 - right_x1) * max(0.0, right_y2 - right_y1)
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0
