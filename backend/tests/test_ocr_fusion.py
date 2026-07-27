from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, ImageDraw

from app.core.errors import PipelineCancelled
from app.providers.frames import (
    FrameExtractionConfig,
    FrameExtractionResult,
    FrameRecord,
    VideoFrameExtractor,
)
from app.providers.fusion import build_fusion_timeline
from app.providers.ocr import OcrBlock, OcrResult, UnifiedOcrGatewayClient
from app.providers.ocr_timeline import build_ocr_timeline, recognize_retained_frames
from app.providers.process import process_registry
from app.storage.workspace import WorkspacePaths, write_json


@pytest.fixture(autouse=True)
def reset_process_registry() -> None:
    process_registry.begin_lifecycle()


class FakeFrameRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, args, **kwargs):
        self.calls += 1
        pattern = Path(args[-1])
        stderr_callback = kwargs["stderr_callback"]
        for index, (timestamp, shade, score) in enumerate(
            [(0.0, 24, 0.0), (5.0, 24, 0.01), (20.0, 220, 0.9)],
            start=1,
        ):
            path = Path(str(pattern).replace("%06d", f"{index:06d}"))
            image = Image.new("RGB", (640, 360), (shade, shade, shade))
            ImageDraw.Draw(image).text(
                (40, 40), f"slide-{shade}", fill=(255 - shade,) * 3
            )
            image.save(path, format="JPEG", quality=95)
            stderr_callback(f"frame:{index - 1} pts:{index} pts_time:{timestamp}")
            stderr_callback(f"lavfi.scene_score={score}")
        return SimpleNamespace(returncode=0)


class FailingFrameRunner:
    async def run(self, *_args, **_kwargs):
        raise AssertionError("valid frame artifacts should have been reused")


class CancelledFrameRunner:
    async def run(self, args, **_kwargs):
        pattern = Path(args[-1])
        partial = Path(str(pattern).replace("%06d", "000001"))
        Image.new("RGB", (64, 64), "white").save(partial, format="JPEG")
        raise PipelineCancelled(stage="FRAME_EXTRACT")


@pytest.mark.asyncio
async def test_real_frame_extraction_uses_media_timestamps_and_perceptual_dedup(
    tmp_path: Path,
) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"immutable-source")
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    paths.create()
    runner = FakeFrameRunner()
    config = FrameExtractionConfig(
        interval_seconds=10,
        scene_change_threshold=0.35,
        max_candidate_frames=10,
    )
    result = await VideoFrameExtractor("ffmpeg", runner).extract(
        media,
        duration=30,
        paths=paths,
        config=config,
        cancel_check=lambda: False,
        progress=lambda _fraction, _message: None,
    )
    assert runner.calls == 1
    assert [item.media_timestamp for item in result.records] == [0.0, 5.0, 20.0]
    assert all(Path(item.original_frame_path).is_file() for item in result.records)
    assert all(Path(item.normalized_image_path).is_file() for item in result.records)
    assert len(result.kept) == 2
    assert result.records[1].drop_reason in {"exact_duplicate", "perceptual_duplicate"}
    assert result.records[1].duplicate_of == result.records[0].frame_id
    assert result.records[2].scene_change_score == 0.9

    reused = await VideoFrameExtractor("ffmpeg", FailingFrameRunner()).extract(
        media,
        duration=30,
        paths=paths,
        config=config,
        cancel_check=lambda: False,
        progress=lambda _fraction, _message: None,
    )
    assert reused.reused is True
    assert [item.sha256 for item in reused.records] == [
        item.sha256 for item in result.records
    ]


@pytest.mark.asyncio
async def test_frame_extraction_cancellation_cleans_partial_images(
    tmp_path: Path,
) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"immutable-source")
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    paths.create()

    with pytest.raises(PipelineCancelled):
        await VideoFrameExtractor("ffmpeg", CancelledFrameRunner()).extract(
            media,
            duration=30,
            paths=paths,
            config=FrameExtractionConfig(),
            cancel_check=lambda: True,
            progress=lambda _fraction, _message: None,
        )

    assert list(paths.frame_candidates.glob("*.part.jpg")) == []
    assert media.read_bytes() == b"immutable-source"


@pytest.mark.asyncio
async def test_ocr_http_request_is_cancelled_without_saving_late_result(
    tmp_path: Path,
) -> None:
    image = tmp_path / "frame.jpg"
    Image.new("RGB", (32, 32), "white").save(image)

    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json={"text": "late"})

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    client = UnifiedOcrGatewayClient(
        base_url="http://ocr.test",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    raw_path = tmp_path / "raw.json"
    with pytest.raises(PipelineCancelled):
        await client.recognize(
            image,
            model="PP-OCRv5_server",
            raw_path=raw_path,
            frame_id="frame_000001",
            media_timestamp=1.5,
            cancel_check=cancelled,
        )
    assert not raw_path.exists()


def test_ocr_timeline_merges_stable_page_but_preserves_incremental_update(
    tmp_path: Path,
) -> None:
    records = tuple(
        _frame_record(tmp_path, index, timestamp, hash_value)
        for index, (timestamp, hash_value) in enumerate(
            [
                (0.0, "0000000000000000"),
                (5.0, "0000000000000001"),
                (10.0, "0000000000000003"),
            ]
        )
    )
    frame_result = FrameExtractionResult(
        records, str(tmp_path / "manifest.json"), "fp"
    )
    raw_paths = [tmp_path / f"raw-{index}.json" for index in range(3)]
    for path in raw_paths:
        write_json(path, {"provider": "test"})
    results = (
        _ocr_result(records[0], raw_paths[0], "标题\n第一点"),
        _ocr_result(records[1], raw_paths[1], "标题\n第一点"),
        _ocr_result(records[2], raw_paths[2], "标题\n第一点\n新增公式 x=1"),
    )
    timeline = build_ocr_timeline(
        results,
        frame_result,
        text_similarity_threshold=0.85,
        incremental_similarity_threshold=0.4,
        max_event_gap_seconds=20,
    )
    assert timeline["event_count"] == 2
    assert timeline["merged_observation_count"] == 1
    assert timeline["events"][0]["source_frame_ids"] == [
        "frame_000000",
        "frame_000001",
    ]
    assert timeline["events"][1]["change_type"] == "incremental_update"
    assert "新增公式" in timeline["events"][1]["text"]


@pytest.mark.asyncio
async def test_ocr_retry_reuses_valid_per_frame_raw_and_normalized_results(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    paths.create()
    frame = _frame_record(tmp_path, 0, 2.0, "0000000000000000")
    Image.new("RGB", (64, 64), "white").save(frame.normalized_image_path)
    frame.sha256 = "frame-sha"
    frame_result = FrameExtractionResult(
        (frame,), str(paths.frames / "manifest.json"), "fp"
    )

    class FakeClient:
        calls = 0

        async def recognize(self, _image, *, raw_path, **_kwargs):
            self.calls += 1
            write_json(raw_path, {"text": "可追溯文字"})
            return _ocr_result(frame, raw_path, "可追溯文字")

    client = FakeClient()
    config = {
        "provider": "video2doc-ocr-http-v1",
        "base_url": "http://ocr.test",
        "model": "PP-OCRv5_server",
        "language": "ch",
        "timeout_seconds": 120,
        "max_retries": 1,
    }
    first = await recognize_retained_frames(
        frame_result,
        ocr_config=config,
        attempt=1,
        paths=paths,
        cancel_check=lambda: False,
        progress=lambda _fraction, _message: None,
        client=client,
    )
    second = await recognize_retained_frames(
        frame_result,
        ocr_config=config,
        attempt=2,
        paths=paths,
        cancel_check=lambda: False,
        progress=lambda _fraction, _message: None,
        client=client,
    )
    assert client.calls == 1
    assert first.reused_count == 0
    assert second.reused_count == 1
    assert second.results[0].text == "可追溯文字"


@pytest.mark.asyncio
async def test_ocr_configuration_change_does_not_reuse_old_frame_result(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    paths.create()
    frame = _frame_record(tmp_path, 0, 2.0, "0000000000000000")
    Image.new("RGB", (64, 64), "white").save(frame.normalized_image_path)
    frame.sha256 = "frame-sha"
    frame_result = FrameExtractionResult(
        (frame,), str(paths.frames / "manifest.json"), "fp"
    )

    class FakeClient:
        calls = 0

        async def recognize(self, _image, *, raw_path, model, **_kwargs):
            self.calls += 1
            write_json(raw_path, {"text": model})
            return _ocr_result(frame, raw_path, model)

    client = FakeClient()
    base_config = {
        "provider": "video2doc-ocr-http-v1",
        "base_url": "http://ocr.test",
        "model": "model-a",
        "language": "ch",
        "timeout_seconds": 120,
        "max_retries": 1,
    }
    await recognize_retained_frames(
        frame_result,
        ocr_config=base_config,
        attempt=1,
        paths=paths,
        cancel_check=lambda: False,
        progress=lambda _fraction, _message: None,
        client=client,
    )
    changed = {**base_config, "model": "model-b"}
    second = await recognize_retained_frames(
        frame_result,
        ocr_config=changed,
        attempt=2,
        paths=paths,
        cancel_check=lambda: False,
        progress=lambda _fraction, _message: None,
        client=client,
    )

    assert client.calls == 2
    assert second.reused_count == 0
    assert second.results[0].text == "model-b"


def test_fusion_keeps_similar_asr_and_ocr_as_distinct_sources(
    tmp_path: Path,
) -> None:
    asr_raw = tmp_path / "asr.json"
    ocr_raw = tmp_path / "ocr.json"
    write_json(asr_raw, {"text": "Attention 公式"})
    write_json(ocr_raw, {"text": "Attention 公式"})
    asr = {
        "provider": "asr-provider",
        "model": "asr-model",
        "segments": [
            {
                "segment_id": "s1",
                "start_time": 10.0,
                "end_time": 14.0,
                "text": "Attention 公式",
                "confidence": 0.9,
                "provider": "asr-provider",
                "model": "asr-model",
                "chunk_index": 0,
                "raw_response_reference": str(asr_raw),
                "words": [],
            }
        ],
    }
    ocr = {
        "events": [
            {
                "event_id": "o1",
                "start_time": 11.0,
                "end_time": 11.0,
                "provider": "ocr-provider",
                "model": "ocr-model",
                "text": "Attention 公式",
                "confidence": 0.8,
                "blocks": [],
                "source_frame_ids": ["frame_000000"],
                "source_images": ["frame.jpg"],
                "raw_artifact_references": [str(ocr_raw)],
                "change_type": "appeared",
                "duplicate_group": "g1",
                "uncertain": False,
            }
        ]
    }
    frames = {
        "frames": [
            {
                "frame_id": "frame_000000",
                "media_timestamp": 11.0,
                "kept": True,
                "extraction_reason": "scene_change",
                "scene_change_score": 0.8,
                "original_frame_path": "original.jpg",
                "normalized_image_path": "frame.jpg",
                "sha256": "sha",
                "perceptual_hash": "0",
            }
        ]
    }
    timeline = build_fusion_timeline(
        asr,
        ocr,
        frames,
        media_duration=30,
        association_window_seconds=5,
    )
    assert timeline["counts"] == {
        "asr_segment": 1,
        "ocr_event": 1,
        "frame_event": 1,
    }
    typed = {item["event_type"]: item for item in timeline["events"]}
    assert typed["asr_segment"]["text"] == typed["ocr_event"]["text"]
    assert typed["asr_segment"]["source"]["kind"] == "audio"
    assert typed["ocr_event"]["source"]["kind"] == "video_frame"
    assert typed["ocr_event"]["related_asr_segment_ids"] == ["s1"]


def _frame_record(
    root: Path, index: int, timestamp: float, perceptual_hash: str
) -> FrameRecord:
    original = root / f"original-{index}.jpg"
    normalized = root / f"normalized-{index}.jpg"
    return FrameRecord(
        frame_id=f"frame_{index:06d}",
        media_timestamp=timestamp,
        extraction_reason="scene_change",
        original_frame_path=str(original),
        normalized_image_path=str(normalized),
        width=640,
        height=360,
        file_size=100,
        sha256=f"sha-{index}",
        perceptual_hash=perceptual_hash,
        scene_change_score=0.8,
    )


def _ocr_result(frame: FrameRecord, raw_path: Path, text: str) -> OcrResult:
    blocks = tuple(
        OcrBlock(
            text=line,
            bounding_box=(10.0, float(index * 20), 300.0, float(index * 20 + 15)),
            confidence=0.95,
            reading_order=index,
        )
        for index, line in enumerate(text.splitlines())
    )
    return OcrResult(
        provider="paddleocr",
        model="PP-OCRv5_server",
        text=text,
        blocks=blocks,
        raw_response_reference=str(raw_path),
        frame_id=frame.frame_id,
        media_timestamp=frame.media_timestamp,
        language="ch",
        elapsed_seconds=0.1,
    )
