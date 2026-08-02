from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.core.errors import PipelineError
from app.providers.asr import AsrResult, AsrSegment
from app.providers.frames import FrameExtractionResult, FrameRecord
from app.providers.media import MediaAcquisitionResult, MediaInfo, MediaStream
from app.providers.ocr import OcrBlock, OcrResult
from app.providers.ocr_timeline import OcrRunResult
from app.providers.subtitles import SubtitleAcquisition
from app.storage.workspace import write_json
from tests.helpers import wait_for_status


def media_info(path: Path, *, video: bool) -> MediaInfo:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return MediaInfo(
        container="wav" if not video else "mp4",
        duration=4.0,
        has_audio=True,
        has_video=video,
        selected_audio_stream=1 if video else 0,
        audio_streams=(
            MediaStream(
                1 if video else 0,
                "audio",
                "aac" if video else "pcm_s16le",
                4.0,
                48000 if video else 16000,
                2 if video else 1,
            ),
        ),
        video_streams=(MediaStream(0, "video", "h264", 4.0),) if video else (),
        raw_probe_path=str(path),
    )


def test_database_worker_runs_real_pipeline_and_exposes_results(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "真实 输入.mp4"
    source.write_bytes(b"not-a-real-video-but-provider-is-controlled")
    gateway = client.post(
        "/api/v1/gateways",
        json={
            "name": "integration-asr-gateway",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:18111",
        },
    ).json()
    model = client.post(
        "/api/v1/gateway-models",
        json={
            "gateway_id": gateway["id"],
            "model_name": "integration-whisper",
            "display_name": "Integration Whisper",
            "max_chunk_seconds": 30,
            "supports_segment_timestamps": True,
        },
    ).json()
    profile = client.post(
        "/api/v1/profiles",
        json={
            "name": "integration-real",
            "asr_gateway_model_id": model["id"],
            "ocr_enabled": False,
        },
    ).json()

    async def fake_probe(self, media_path, paths, *, cancel_check, artifact_name="ffprobe"):
        return media_info(
            paths.metadata / f"{artifact_name}.json", video=Path(media_path) == source
        )

    async def fake_normalize(self, media_path, probed, paths, *, cancel_check, progress):
        output = paths.audio / "normalized.wav"
        output.write_bytes(b"RIFF" + b"0" * 100)
        progress(1.0, "controlled audio complete")
        return output, media_info(paths.metadata / "ffprobe_normalized_audio.json", video=False)

    async def fake_transcribe(self, request, paths, *, cancel_check):
        raw_path = paths.asr_raw / f"chunk_{request.chunk.index:04d}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            '{"language":"zh","segments":[{"start":0.5,"end":2.0,"text":"受控转写"}]}',
            encoding="utf-8",
        )
        return AsrResult(
            provider=self.name,
            model=request.model,
            language="zh",
            chunk_index=request.chunk.index,
            raw_response_reference=str(raw_path),
            segments=(
                AsrSegment(
                    segment_id="local",
                    start_time=0.5,
                    end_time=2.0,
                    text="受控转写",
                    confidence=None,
                    language="zh",
                    provider=self.name,
                    model=request.model,
                    chunk_index=request.chunk.index,
                    raw_response_reference=str(raw_path),
                ),
            ),
        )

    monkeypatch.setattr("app.pipeline.real.FfprobeProvider.probe", fake_probe)
    monkeypatch.setattr("app.pipeline.real.FfmpegProvider.normalize_audio", fake_normalize)
    monkeypatch.setattr("app.pipeline.real.UnifiedAsrGatewayProvider.transcribe", fake_transcribe)

    created = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "REAL",
            "inputs": [{"source": str(source), "source_type": "LOCAL_FILE"}],
            "profile_id": profile["id"],
            "config_overrides": {"audio_chunk_seconds": 600},
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()[0]["id"]
    completed = wait_for_status(client, task_id, {"COMPLETED"})
    assert completed["progress"] == 100
    assert completed["media_info"]["has_video"] is True
    assert completed["transcript_preview"]["text"] == "受控转写"
    assert [stage["stage"] for stage in completed["stages"]] == [
        "INPUT_VALIDATE",
        "MEDIA_ACQUIRE",
        "MEDIA_PROBE",
        "AUDIO_EXTRACT",
        "AUDIO_VALIDATE",
        "AUDIO_CHUNK",
        "ASR_TRANSCRIBE",
        "ASR_COMBINE",
        "ASR_VALIDATE",
        "FUSION_BUILD",
        "FUSION_VALIDATE",
        "DOCUMENT_BUILD",
        "TEXT_EDIT",
        "MARKDOWN_RENDER",
        "OBSIDIAN_EXPORT",
        "FINALIZE",
    ]
    assert completed["document_path"] and Path(completed["document_path"]).is_file()
    assert completed["markdown_path"] and Path(completed["markdown_path"]).is_file()
    assert completed["export_path"] and Path(completed["export_path"]).is_file()
    markdown = Path(completed["export_path"]).read_text(encoding="utf-8")
    assert "受控转写" in markdown
    assert "未启用（忠实原文回退）" in markdown
    assert {item["target_type"] for item in completed["exports"]} == {
        "WORKSPACE",
        "OBSIDIAN",
    }
    assert client.get(f"/api/v1/tasks/{task_id}/document").status_code == 200
    markdown_response = client.get(f"/api/v1/tasks/{task_id}/markdown")
    assert markdown_response.status_code == 200
    assert "受控转写" in markdown_response.text
    opened: list[str] = []
    monkeypatch.setattr("app.services.tasks.os.startfile", opened.append)
    opened_response = client.post(f"/api/v1/tasks/{task_id}/open/markdown")
    assert opened_response.status_code == 200
    assert opened == [str(Path(completed["export_path"]).resolve())]
    transcript = client.get(f"/api/v1/tasks/{task_id}/transcript")
    assert transcript.status_code == 200
    assert transcript.json()["segments"][0]["start_time"] == 0.5
    media = client.get(f"/api/v1/tasks/{task_id}/media")
    assert media.status_code == 200 and media.json()["has_audio"] is True
    artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts").json()
    assert any(item["path"] == "02_asr/normalized.json" for item in artifacts)
    snapshot_text = str(client.get(f"/api/v1/tasks/{task_id}/snapshot").json())
    assert "integration-secret" not in snapshot_text


def test_url_chinese_subtitle_skips_asr_and_exports_timestamp_free_markdown(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "subtitle-video.mp4"
    source.write_bytes(b"controlled-video-source")
    subtitle = tmp_path / "subtitle-video.zh-CN.srt"
    subtitle.write_text(
        "1\n00:00:00,500 --> 00:00:01,800\n第一段中文字幕。\n\n"
        "2\n00:00:02,000 --> 00:00:03,500\n第二段保留完整内容。\n",
        encoding="utf-8",
    )
    export_directory = tmp_path / "export"
    export_directory.mkdir()
    gateway = client.post(
        "/api/v1/gateways",
        json={
            "name": "subtitle-fallback-asr-gateway",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:18121",
        },
    ).json()
    model = client.post(
        "/api/v1/gateway-models",
        json={
            "gateway_id": gateway["id"],
            "model_name": "subtitle-fallback-whisper",
            "display_name": "Subtitle Fallback Whisper",
            "max_chunk_seconds": 30,
            "supports_segment_timestamps": True,
        },
    ).json()
    profile = client.post(
        "/api/v1/profiles",
        json={
            "name": "subtitle-with-asr-fallback",
            "asr_gateway_model_id": model["id"],
            "ocr_enabled": False,
        },
    ).json()

    async def fake_probe(self, media_path, paths, *, cancel_check, artifact_name="ffprobe"):
        return media_info(paths.metadata / f"{artifact_name}.json", video=True)

    async def fake_media_acquire(
        self,
        source_url,
        source_type,
        paths,
        *,
        cancel_check,
        progress,
    ):
        metadata_path = paths.metadata / "ytdlp.json"
        write_json(
            metadata_path,
            {"subtitles": {"zh-CN": [{"ext": "srt"}]}, "title": "字幕视频"},
        )
        progress(1.0, "受控媒体获取完成")
        return MediaAcquisitionResult(
            source_type=source_type,
            original_input=source_url,
            local_media_path=str(source),
            title="字幕视频",
            duration=4.0,
            container="mp4",
            has_audio=True,
            has_video=True,
            metadata_path=str(metadata_path),
            provider="controlled-yt-dlp",
        )

    async def fake_subtitle_acquire(
        self,
        source_url,
        paths,
        *,
        cancel_check,
        progress,
    ):
        progress(1.0, "受控链接字幕抓取完成")
        return SubtitleAcquisition(
            path=str(subtitle),
            language="zh-CN",
            source_kind="manual",
            subtitle_format="srt",
            sha256="controlled-subtitle-sha256",
            size_bytes=subtitle.stat().st_size,
            manifest_path=str(paths.metadata / "subtitle_acquisition.json"),
        )

    async def forbidden_audio(*args, **kwargs):
        raise AssertionError("subtitle mode must not normalize audio")

    monkeypatch.setattr(
        "app.pipeline.real.MediaSourceResolver.acquire",
        fake_media_acquire,
    )
    monkeypatch.setattr(
        "app.pipeline.real.YtDlpSubtitleSource.acquire",
        fake_subtitle_acquire,
    )
    monkeypatch.setattr("app.pipeline.real.FfprobeProvider.probe", fake_probe)
    monkeypatch.setattr(
        "app.pipeline.real.FfmpegProvider.normalize_audio",
        forbidden_audio,
    )

    created = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "REAL",
            "inputs": [
                {
                    "source": "https://example.com/watch?v=subtitle",
                    "source_type": "URL",
                    "text_source": "SUBTITLE",
                }
            ],
            "export_directory": str(export_directory),
            "profile_id": profile["id"],
            "config_overrides": {
                "ocr_enabled": False,
                "keep_timestamps": False,
            },
        },
    )
    assert created.status_code == 201, created.text
    completed = wait_for_status(client, created.json()[0]["id"], {"COMPLETED"})
    stage_names = [stage["stage"] for stage in completed["stages"]]
    assert "SUBTITLE_IMPORT" in stage_names
    assert "SUBTITLE_NORMALIZE" in stage_names
    assert "SUBTITLE_VALIDATE" in stage_names
    assert "ASR_TRANSCRIBE" not in stage_names
    assert "AUDIO_EXTRACT" not in stage_names
    subtitle_stage = next(
        stage for stage in completed["stages"] if stage["stage"] == "SUBTITLE_IMPORT"
    )
    assert subtitle_stage["model_usage"][0]["mode"] == "URL_SUBTITLE"
    assert subtitle_stage["model_usage"][0]["model_name"] is None
    assert completed["transcript_preview"]["provider"] == "yt-dlp-subtitle"
    assert completed["transcript_preview"]["text"] == ("第一段中文字幕。\n第二段保留完整内容。")
    markdown = Path(completed["export_path"]).read_text(encoding="utf-8")
    assert "第一段中文字幕。" in markdown
    assert "[00:00:" not in markdown
    assert "created_at:" not in markdown
    snapshot = completed["snapshot"]["snapshot_json"]
    assert snapshot["models"]["asr"] == "subtitle-fallback-whisper"
    assert snapshot["asr"]["model"] == "subtitle-fallback-whisper"
    assert snapshot["subtitle"]["mode"] == "URL_SUBTITLE_FETCH"
    assert snapshot["subtitle"]["provider"] == "yt-dlp-subtitle"
    assert snapshot["subtitle"]["source_url"].startswith("https://")


def test_missing_url_subtitle_automatically_falls_back_to_profile_asr(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "subtitle-missing-video.mp4"
    source.write_bytes(b"controlled-video-source")
    export_directory = tmp_path / "fallback-export"
    export_directory.mkdir()
    gateway = client.post(
        "/api/v1/gateways",
        json={
            "name": "automatic-fallback-asr-gateway",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:18122",
        },
    ).json()
    model = client.post(
        "/api/v1/gateway-models",
        json={
            "gateway_id": gateway["id"],
            "model_name": "automatic-fallback-whisper",
            "display_name": "Automatic Fallback Whisper",
            "max_chunk_seconds": 30,
            "supports_segment_timestamps": True,
        },
    ).json()
    profile = client.post(
        "/api/v1/profiles",
        json={
            "name": "automatic-subtitle-fallback",
            "asr_gateway_model_id": model["id"],
            "ocr_enabled": False,
        },
    ).json()

    async def fake_media_acquire(self, source_url, source_type, paths, *, cancel_check, progress):
        metadata_path = paths.metadata / "ytdlp.json"
        write_json(metadata_path, {"subtitles": {}, "title": "无字幕视频"})
        progress(1.0, "受控媒体获取完成")
        return MediaAcquisitionResult(
            source_type=source_type,
            original_input=source_url,
            local_media_path=str(source),
            title="无字幕视频",
            duration=4.0,
            container="mp4",
            has_audio=True,
            has_video=True,
            metadata_path=str(metadata_path),
            provider="controlled-yt-dlp",
        )

    async def fake_probe(self, media_path, paths, *, cancel_check, artifact_name="ffprobe"):
        return media_info(
            paths.metadata / f"{artifact_name}.json",
            video=Path(media_path) == source,
        )

    async def missing_subtitle(self, source_url, paths, *, cancel_check, progress):
        raise PipelineError("SUBTITLE_NOT_FOUND", "视频链接没有可用字幕")

    async def fake_normalize(self, media_path, probed, paths, *, cancel_check, progress):
        output = paths.audio / "normalized.wav"
        output.write_bytes(b"RIFF" + b"0" * 100)
        progress(1.0, "受控音频完成")
        return output, media_info(paths.metadata / "ffprobe_normalized_audio.json", video=False)

    async def fake_transcribe(self, request, paths, *, cancel_check):
        raw_path = paths.asr_raw / f"chunk_{request.chunk.index:04d}.json"
        write_json(
            raw_path,
            {
                "language": "zh",
                "segments": [{"start": 0.5, "end": 2.0, "text": "字幕缺失后自动识别"}],
            },
        )
        return AsrResult(
            provider=self.name,
            model=request.model,
            language="zh",
            chunk_index=request.chunk.index,
            raw_response_reference=str(raw_path),
            segments=(
                AsrSegment(
                    segment_id="fallback-segment",
                    start_time=0.5,
                    end_time=2.0,
                    text="字幕缺失后自动识别",
                    confidence=0.95,
                    language="zh",
                    provider=self.name,
                    model=request.model,
                    chunk_index=request.chunk.index,
                    raw_response_reference=str(raw_path),
                ),
            ),
        )

    monkeypatch.setattr("app.pipeline.real.MediaSourceResolver.acquire", fake_media_acquire)
    monkeypatch.setattr("app.pipeline.real.FfprobeProvider.probe", fake_probe)
    monkeypatch.setattr("app.pipeline.real.YtDlpSubtitleSource.acquire", missing_subtitle)
    monkeypatch.setattr("app.pipeline.real.FfmpegProvider.normalize_audio", fake_normalize)
    monkeypatch.setattr("app.pipeline.real.UnifiedAsrGatewayProvider.transcribe", fake_transcribe)

    created = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "REAL",
            "inputs": [
                {
                    "source": "https://example.com/watch?v=without-subtitle",
                    "source_type": "URL",
                    "text_source": "SUBTITLE",
                }
            ],
            "profile_id": profile["id"],
            "export_directory": str(export_directory),
            "config_overrides": {
                "ocr_enabled": False,
                "embedded_subtitle_ocr_fallback": False,
                "audio_chunk_seconds": 600,
            },
        },
    )
    assert created.status_code == 201, created.text
    completed = wait_for_status(client, created.json()[0]["id"], {"COMPLETED"})
    stages = [stage["stage"] for stage in completed["stages"]]
    assert "AUDIO_EXTRACT" in stages
    assert "ASR_TRANSCRIBE" in stages
    assert "ASR_COMBINE" in stages
    assert "SUBTITLE_NORMALIZE" not in stages
    assert completed["transcript_preview"]["text"] == "字幕缺失后自动识别"
    assert completed["snapshot"]["snapshot_json"]["asr"]["model"] == ("automatic-fallback-whisper")
    task_log = Path(completed["workspace_path"]) / "logs" / "task.log"
    assert "已自动切换到处理方案中的 ASR 模型" in task_log.read_text(encoding="utf-8")


def test_database_worker_runs_ocr_fusion_stages_and_serves_traceable_artifacts(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "ocr-fusion.mp4"
    source.write_bytes(b"controlled-video-source")
    asr_gateway = client.post(
        "/api/v1/gateways",
        json={
            "name": "integration-fusion-asr",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:18112",
        },
    ).json()
    asr_model = client.post(
        "/api/v1/gateway-models",
        json={
            "gateway_id": asr_gateway["id"],
            "model_name": "integration-fusion-asr",
            "display_name": "Integration Fusion ASR",
            "max_chunk_seconds": 30,
            "supports_segment_timestamps": True,
        },
    ).json()
    ocr_gateway = client.post(
        "/api/v1/gateways",
        json={
            "name": "integration-fusion-ocr",
            "gateway_type": "OCR",
            "protocol": "video2doc-ocr-http-v1",
            "base_url": "http://127.0.0.1:18212",
        },
    ).json()
    ocr_model = client.post(
        "/api/v1/gateway-models",
        json={
            "gateway_id": ocr_gateway["id"],
            "model_name": "PP-OCRv5_server",
            "display_name": "Integration PP-OCRv5",
            "supports_confidence": True,
            "languages": ["ch", "en"],
        },
    ).json()
    profile = client.post(
        "/api/v1/profiles",
        json={
            "name": "integration-ocr-fusion",
            "asr_gateway_model_id": asr_model["id"],
            "ocr_gateway_model_id": ocr_model["id"],
            "ocr_enabled": True,
        },
    ).json()

    async def fake_probe(self, media_path, paths, *, cancel_check, artifact_name="ffprobe"):
        return media_info(
            paths.metadata / f"{artifact_name}.json", video=Path(media_path) == source
        )

    async def fake_normalize(self, media_path, probed, paths, *, cancel_check, progress):
        output = paths.audio / "normalized.wav"
        output.write_bytes(b"RIFF" + b"0" * 100)
        progress(1.0, "controlled audio complete")
        return output, media_info(paths.metadata / "ffprobe_normalized_audio.json", video=False)

    async def fake_transcribe(self, request, paths, *, cancel_check):
        raw_path = paths.asr_raw / f"chunk_{request.chunk.index:04d}.json"
        write_json(
            raw_path,
            {
                "language": "zh",
                "segments": [{"start": 0.5, "end": 2.0, "text": "画面标题"}],
            },
        )
        return AsrResult(
            provider=self.name,
            model=request.model,
            language="zh",
            chunk_index=request.chunk.index,
            raw_response_reference=str(raw_path),
            segments=(
                AsrSegment(
                    segment_id="local",
                    start_time=0.5,
                    end_time=2.0,
                    text="画面标题",
                    confidence=0.91,
                    language="zh",
                    provider=self.name,
                    model=request.model,
                    chunk_index=request.chunk.index,
                    raw_response_reference=str(raw_path),
                ),
            ),
        )

    async def fake_extract(self, media_path, *, duration, paths, config, cancel_check, progress):
        original = paths.frame_candidates / "frame_000000.jpg"
        normalized = paths.frame_normalized / "frame_000000.jpg"
        Image.new("RGB", (640, 360), "white").save(original)
        Image.new("RGB", (640, 360), "white").save(normalized)
        record = FrameRecord(
            frame_id="frame_000000",
            media_timestamp=1.0,
            extraction_reason="scene_change",
            original_frame_path=str(original),
            normalized_image_path=str(normalized),
            width=640,
            height=360,
            file_size=normalized.stat().st_size,
            sha256="controlled-frame-sha",
            perceptual_hash="0000000000000000",
            scene_change_score=0.8,
        )
        manifest = paths.frames / "manifest.json"
        write_json(
            manifest,
            {
                "schema_version": 1,
                "candidate_count": 1,
                "kept_count": 1,
                "dropped_count": 0,
                "drop_reasons": {},
                "frames": [record.to_dict()],
            },
        )
        progress(1.0, "controlled frame complete")
        return FrameExtractionResult((record,), str(manifest), "controlled")

    async def fake_ocr(
        frames,
        *,
        ocr_config,
        attempt,
        paths,
        cancel_check,
        progress,
    ):
        frame = frames.kept[0]
        raw = paths.ocr_raw / f"attempt_{attempt:04d}" / f"{frame.frame_id}.json"
        write_json(raw, {"provider": "controlled-ocr", "text": "画面标题"})
        result = OcrResult(
            provider="controlled-ocr",
            model=ocr_config["model"],
            text="画面标题",
            blocks=(
                OcrBlock(
                    text="画面标题",
                    bounding_box=(10.0, 10.0, 300.0, 60.0),
                    confidence=0.98,
                    reading_order=0,
                ),
            ),
            raw_response_reference=str(raw),
            frame_id=frame.frame_id,
            media_timestamp=frame.media_timestamp,
            language="ch",
            elapsed_seconds=0.01,
        )
        normalized = paths.ocr / "normalized.json"
        raw_index = paths.ocr / "raw.json"
        preview = paths.ocr / "preview.json"
        write_json(normalized, {"schema_version": 1, "frames": [result.to_dict()]})
        write_json(raw_index, {"schema_version": 1, "provider_responses": [str(raw)]})
        write_json(preview, {"frame_count": 1, "block_count": 1, "text": "画面标题"})
        progress(1.0, "controlled OCR complete")
        return OcrRunResult(
            (result,),
            str(normalized),
            str(raw_index),
            str(preview),
            1,
            0,
            0.01,
        )

    monkeypatch.setattr("app.pipeline.real.FfprobeProvider.probe", fake_probe)
    monkeypatch.setattr("app.pipeline.real.FfmpegProvider.normalize_audio", fake_normalize)
    monkeypatch.setattr("app.pipeline.real.UnifiedAsrGatewayProvider.transcribe", fake_transcribe)
    monkeypatch.setattr("app.pipeline.real.VideoFrameExtractor.extract", fake_extract)
    monkeypatch.setattr("app.pipeline.real.recognize_retained_frames", fake_ocr)

    created = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "REAL",
            "inputs": [{"source": str(source), "source_type": "LOCAL_FILE"}],
            "profile_id": profile["id"],
            "config_overrides": {"audio_chunk_seconds": 600},
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()[0]["id"]
    completed = wait_for_status(client, task_id, {"COMPLETED"})
    assert completed["frame_summary"]["kept_count"] == 1
    assert completed["ocr_preview"]["block_count"] == 1
    assert completed["fusion_preview"]["counts"] == {
        "asr_segment": 1,
        "ocr_event": 1,
        "frame_event": 1,
    }
    assert [stage["stage"] for stage in completed["stages"]][-11:] == [
        "FRAME_EXTRACT",
        "FRAME_DEDUP",
        "OCR_RECOGNIZE",
        "OCR_TIMELINE",
        "FUSION_BUILD",
        "FUSION_VALIDATE",
        "DOCUMENT_BUILD",
        "TEXT_EDIT",
        "MARKDOWN_RENDER",
        "OBSIDIAN_EXPORT",
        "FINALIZE",
    ]
    assert completed["document_path"] and Path(completed["document_path"]).is_file()
    assert completed["export_path"] and Path(completed["export_path"]).is_file()
    markdown = Path(completed["export_path"]).read_text(encoding="utf-8")
    assert "画面标题" in markdown
    assert "画面文字（OCR）" not in markdown
    assert "**讲者内容**" not in markdown
    assert "frame_000000" in markdown
    assert client.get(f"/api/v1/tasks/{task_id}/document").status_code == 200
    assert client.get(f"/api/v1/tasks/{task_id}/markdown").status_code == 200
    assert client.get(f"/api/v1/tasks/{task_id}/frames").status_code == 200
    assert client.get(f"/api/v1/tasks/{task_id}/ocr").status_code == 200
    timeline = client.get(f"/api/v1/tasks/{task_id}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["counts"]["ocr_event"] == 1
    artifact = client.get(
        f"/api/v1/tasks/{task_id}/artifacts/01_extract/frames/normalized/frame_000000.jpg"
    )
    assert artifact.status_code == 200
    assert artifact.content.startswith(b"\xff\xd8")
    traversal = client.get(f"/api/v1/tasks/{task_id}/artifacts/%2E%2E/%2E%2E/task_config.json")
    assert traversal.status_code == 404
