from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile


MODEL_ID = "PP-OCRv5_server"
PROVIDER = "paddleocr-3.7.0"
_engine: Any | None = None
_engine_lock = threading.Lock()
_inference_lock = threading.Lock()

app = FastAPI(title="Video2Doc PaddleOCR Gateway", version="1.0")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": PROVIDER,
        "model": MODEL_ID,
        "model_loaded": _engine is not None,
        "device": "cpu",
    }


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {
        "models": [
            {
                "model_name": MODEL_ID,
                "display_name": "OCR v5（中英文画面）",
                "capabilities": {
                    "local": True,
                    "text_blocks": True,
                    "bounding_boxes": True,
                    "polygons": True,
                    "reading_order": True,
                    "confidence": True,
                    "languages": ["ch", "en"],
                    "device": "cpu",
                },
            }
        ]
    }


@app.post("/v1/ocr/recognitions")
async def recognize(
    file: UploadFile = File(...),
    model: str = Form(MODEL_ID),
    frame_id: str = Form(""),
    media_timestamp: str = Form(""),
    language: str = Form("ch"),
) -> dict[str, Any]:
    if model != MODEL_ID:
        raise HTTPException(status_code=400, detail=f"Unsupported OCR model: {model}")
    suffix = Path(file.filename or "frame.jpg").suffix or ".jpg"
    runtime_root = Path(os.getenv("VIDEO2DOC_OCR_RUNTIME_ROOT", tempfile.gettempdir()))
    temp_root = runtime_root / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix="video2doc-ocr-", suffix=suffix, dir=temp_root, delete=False
    )
    image_path = Path(handle.name)
    try:
        while chunk := await file.read(1024 * 1024):
            handle.write(chunk)
        handle.close()
        if image_path.stat().st_size == 0:
            raise HTTPException(status_code=400, detail="Uploaded image is empty")
        started = time.monotonic()
        raw_results = await asyncio.to_thread(_predict, image_path, language)
        elapsed = time.monotonic() - started
        blocks = _blocks_from_results(raw_results)
        if not blocks:
            raise HTTPException(status_code=422, detail="PaddleOCR returned no text blocks")
        timestamp = _float_or_none(media_timestamp)
        return {
            "provider": PROVIDER,
            "model": MODEL_ID,
            "frame_id": frame_id or None,
            "media_timestamp": timestamp,
            "language": language or "ch",
            "elapsed_seconds": elapsed,
            "text": "\n".join(block["text"] for block in blocks),
            "blocks": blocks,
            "raw_response": raw_results,
        }
    finally:
        try:
            handle.close()
        except OSError:
            pass
        image_path.unlink(missing_ok=True)
        await file.close()


def _load_engine(language: str) -> Any:
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        from paddleocr import PaddleOCR

        _engine = PaddleOCR(
            text_detection_model_name="PP-OCRv5_server_det",
            text_recognition_model_name="PP-OCRv5_server_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            enable_mkldnn=False,
        )
    return _engine


def _predict(image_path: Path, language: str) -> list[dict[str, Any]]:
    engine = _load_engine(language)
    with _inference_lock:
        results = list(engine.predict(str(image_path)))
    return [_result_payload(item) for item in results]


def _result_payload(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        payload = item
    else:
        payload = getattr(item, "json", None)
        if callable(payload):
            payload = payload()
        if payload is None:
            try:
                payload = dict(item)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Unsupported PaddleOCR result type: {type(item)!r}") from exc
    converted = _jsonable(payload)
    if not isinstance(converted, dict):
        raise RuntimeError("PaddleOCR result is not an object")
    return converted


def _blocks_from_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for result in results:
        data = result.get("res") if isinstance(result.get("res"), dict) else result
        texts = data.get("rec_texts") or []
        scores = data.get("rec_scores") or []
        polygons = data.get("rec_polys") or []
        boxes = data.get("rec_boxes") or []
        for index, raw_text in enumerate(texts):
            text = str(raw_text).strip()
            if not text:
                continue
            polygon = polygons[index] if index < len(polygons) else None
            box = boxes[index] if index < len(boxes) else None
            confidence = scores[index] if index < len(scores) else None
            blocks.append(
                {
                    "text": text,
                    "confidence": _float_or_none(confidence),
                    "bounding_box": _box_or_none(box),
                    "polygon": _polygon_or_none(polygon),
                    "reading_order": len(blocks),
                    "language": None,
                }
            )
    return blocks


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _box_or_none(value: Any) -> list[float] | None:
    converted = _jsonable(value)
    if (
        isinstance(converted, list)
        and len(converted) >= 4
        and all(isinstance(item, (int, float)) for item in converted[:4])
    ):
        return [float(item) for item in converted[:4]]
    return None


def _polygon_or_none(value: Any) -> list[list[float]] | None:
    converted = _jsonable(value)
    if not isinstance(converted, list):
        return None
    points: list[list[float]] = []
    for point in converted:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or not all(isinstance(item, (int, float)) for item in point)
        ):
            return None
        points.append([float(point[0]), float(point[1])])
    return points if len(points) >= 3 else None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cleanup_stale_uploads(runtime_root: Path | None = None) -> int:
    root = runtime_root or Path(
        os.getenv("VIDEO2DOC_OCR_RUNTIME_ROOT", tempfile.gettempdir())
    )
    temp_root = root / "temp"
    if not temp_root.is_dir():
        return 0
    removed = 0
    for candidate in temp_root.glob("video2doc-ocr-*"):
        if candidate.is_file():
            candidate.unlink(missing_ok=True)
            removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8201)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"} and os.getenv(
        "VIDEO2DOC_ALLOW_PUBLIC_BIND"
    ) != "1":
        raise SystemExit("Public bind is disabled; use 127.0.0.1")
    cleanup_stale_uploads()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
