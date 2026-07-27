from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx


TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--max-candidate-frames", type=int, default=10)
    args = parser.parse_args()
    if not args.video.is_file():
        raise SystemExit(f"Video is missing: {args.video}")
    if not args.ffmpeg.is_file():
        raise SystemExit(f"FFmpeg is missing: {args.ffmpeg}")

    video_hash_before = _sha256(args.video)
    started = time.monotonic()
    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=60, trust_env=False
    ) as client:
        health = _get(client, "/health")
        if health.get("status") != "ok":
            raise SystemExit(f"Backend is unhealthy: {health}")
        settings = _put(client, "/settings", {"ffmpeg_path": str(args.ffmpeg)})
        gateway_models = _get(client, "/gateway-models")
        asr_model = next(
            (
                item
                for item in gateway_models
                if item.get("gateway_name") == "local-whisper"
                and item.get("model_name") == "large-v3-turbo"
                and item.get("enabled")
            ),
            None,
        )
        ocr_model = next(
            (
                item
                for item in gateway_models
                if item.get("gateway_name") == "local-paddleocr"
                and item.get("model_name") == "PP-OCRv5_server"
                and item.get("enabled")
            ),
            None,
        )
        if not asr_model or not ocr_model:
            raise SystemExit(
                "Required local-whisper/large-v3-turbo or "
                "local-paddleocr/PP-OCRv5_server model is not enabled"
            )
        if args.task_id is None:
            profile = _post(
                client,
                "/profiles",
                {
                    "name": f"OCR fusion real acceptance {int(time.time())}",
                    "description": "Automated real local video OCR/fusion acceptance",
                    "asr_gateway_model_id": asr_model["id"],
                    "ocr_gateway_model_id": ocr_model["id"],
                    "ocr_enabled": True,
                    "keep_timestamps": True,
                },
            )
            created = _post(
                client,
                "/tasks",
                {
                    "pipeline_mode": "REAL",
                    "inputs": [
                        {
                            "source": str(args.video.resolve()),
                            "source_type": "LOCAL_FILE",
                            "title": "sample OCR fusion real acceptance",
                        }
                    ],
                    "profile_id": profile["id"],
                    "config_overrides": {
                        "frame_sampling_strategy": "hybrid",
                        "ocr_frame_interval_seconds": 60.0,
                        "scene_change_threshold": 0.50,
                        "scene_min_gap_seconds": 2.0,
                        "max_candidate_frames": args.max_candidate_frames,
                        "frame_normalized_max_width": 1600,
                        "ocr_language": "ch",
                        "ocr_batch_size": 1,
                        "fusion_window_seconds": 8.0,
                    },
                },
            )
            task_id = created[0]["id"]
            detail = _wait_for_task(client, task_id, args.timeout_seconds)
        else:
            task_id = args.task_id
            detail = _get(client, f"/tasks/{task_id}")
        if detail["status"] != "COMPLETED":
            raise SystemExit(
                json.dumps(
                    {
                        "task_id": task_id,
                        "status": detail["status"],
                        "stage": detail["current_stage"],
                        "error_code": detail.get("error_code"),
                        "error_message": detail.get("error_message"),
                    },
                    ensure_ascii=False,
                )
            )
        frames = _get(client, f"/tasks/{task_id}/frames")
        ocr = _get(client, f"/tasks/{task_id}/ocr")
        timeline = _get(client, f"/tasks/{task_id}/timeline")
        artifacts = _get(client, f"/tasks/{task_id}/artifacts")

    workspace = Path(detail["workspace_path"])
    frame_records = frames.get("frames")
    ocr_events = ocr.get("events")
    timeline_events = timeline.get("events")
    if not isinstance(frame_records, list) or not frame_records:
        raise SystemExit("Frame manifest contains no frames")
    if not isinstance(ocr_events, list) or not ocr_events:
        raise SystemExit("OCR timeline contains no events")
    if not isinstance(timeline_events, list) or not timeline_events:
        raise SystemExit("Fusion timeline contains no events")
    timestamps = [float(item["media_timestamp"]) for item in frame_records]
    if timestamps != sorted(timestamps):
        raise SystemExit("Frame timestamps are not monotonic")
    duration = float(frames["media_duration"])
    if not all(0 <= item <= duration + 0.1 for item in timestamps):
        raise SystemExit("Frame timestamps fall outside the media duration")
    raw_references = {
        reference
        for event in ocr_events
        for reference in event.get("raw_artifact_references", [])
    }
    if not raw_references or not all(Path(item).is_file() for item in raw_references):
        raise SystemExit("OCR raw response references are missing")
    leftovers = [
        str(item.relative_to(workspace))
        for item in workspace.rglob("*")
        if item.is_file() and (".part" in item.name or item.name.endswith(".tmp"))
    ]
    if leftovers:
        raise SystemExit(f"Temporary artifacts remain: {leftovers}")
    video_hash_after = _sha256(args.video)
    if video_hash_before != video_hash_after:
        raise SystemExit("Input video changed during the Pipeline run")

    elapsed = time.monotonic() - started
    summary: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "status": detail["status"],
        "workspace": str(workspace),
        "elapsed_seconds": elapsed,
        "source_sha256": video_hash_before,
        "source_unchanged": True,
        "media_duration": duration,
        "frame_candidate_count": frames.get("candidate_count"),
        "frame_kept_count": frames.get("kept_count"),
        "frame_dropped_count": frames.get("dropped_count"),
        "frame_timestamp_min": min(timestamps),
        "frame_timestamp_max": max(timestamps),
        "frame_timestamps_monotonic": True,
        "ocr_event_count": ocr.get("event_count"),
        "ocr_merged_observation_count": ocr.get("merged_observation_count"),
        "ocr_raw_response_count": len(raw_references),
        "ocr_frame_count": (detail.get("ocr_preview") or {}).get("frame_count"),
        "ocr_block_count": (detail.get("ocr_preview") or {}).get("block_count"),
        "ocr_reused_count": (detail.get("ocr_preview") or {}).get("reused_count"),
        "fusion_event_count": timeline.get("event_count"),
        "fusion_counts": timeline.get("counts"),
        "artifact_count": len(artifacts),
        "temporary_leftover_count": 0,
        "asr_model": asr_model["model_name"],
        "ocr_model": ocr_model["model_name"],
        "ffmpeg_path": settings["ffmpeg_path"],
        "completed_stages": [
            item["stage"] for item in detail.get("stages", []) if item["status"] == "COMPLETED"
        ],
        "attempts": sorted(
            {int(item["attempt"]) for item in detail.get("stages", [])}
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _wait_for_task(
    client: httpx.Client, task_id: int, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    previous: tuple[str, int] | None = None
    while time.monotonic() < deadline:
        detail = _get(client, f"/tasks/{task_id}")
        state = (str(detail["current_stage"]), int(detail["progress"]))
        if state != previous:
            print(
                json.dumps(
                    {
                        "task_id": task_id,
                        "status": detail["status"],
                        "stage": state[0],
                        "progress": state[1],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            previous = state
        if detail["status"] in TERMINAL_STATES:
            return detail
        time.sleep(1)
    raise SystemExit(f"Timed out waiting for task {task_id}")


def _get(client: httpx.Client, path: str) -> Any:
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> Any:
    response = client.post(path, json=payload)
    response.raise_for_status()
    return response.json()


def _put(client: httpx.Client, path: str, payload: dict[str, Any]) -> Any:
    response = client.put(path, json=payload)
    response.raise_for_status()
    return response.json()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
