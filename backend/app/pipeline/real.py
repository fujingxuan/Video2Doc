from __future__ import annotations

import copy
import logging
import os
from dataclasses import replace
from pathlib import Path

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.errors import PipelineCancelled, PipelineError, PipelineInterrupted
from app.db.session import SessionLocal
from app.documents.builder import build_document
from app.documents.editor import FaithfulTextEditor, PROMPT_VERSION
from app.documents.exporter import ExportConfig, ExportResult, export_document_bundle
from app.documents.faithfulness import validate_document_faithfulness
from app.documents.ir import Document
from app.models import (
    DocumentArtifact,
    MarkdownExport,
    Task,
    TaskStageRun,
    TextEditRun,
)
from app.models.entities import utcnow
from app.pipeline.model_usage import stage_model_usage
from app.providers.asr import (
    AsrRequest,
    AsrResult,
    AudioChunk,
    AudioChunker,
    DashScopeFunAsrProvider,
    OpenAICompatibleAsrProvider,
    UnifiedAsrGatewayProvider,
    combine_asr_results,
    load_asr_gateway_credentials,
    load_asr_credentials,
    normalize_asr_response,
    normalize_dashscope_response,
    save_transcript_artifacts,
    validate_segments,
)
from app.providers.frames import FrameExtractionConfig, VideoFrameExtractor
from app.providers.fusion import build_fusion_timeline, save_fusion_artifacts
from app.providers.media import (
    FfmpegProvider,
    FfprobeProvider,
    MediaAcquisitionResult,
    MediaInfo,
    MediaSourceResolver,
    MediaStream,
    classify_input,
)
from app.providers.process import process_registry
from app.providers.subtitles import import_subtitle
from app.providers.ocr_timeline import (
    build_ocr_timeline,
    recognize_retained_frames,
    save_ocr_timeline,
)
from app.storage.workspace import (
    WorkspacePaths,
    append_task_log,
    read_json,
    write_artifact_index,
    write_json,
    write_task_state,
)


logger = logging.getLogger(__name__)


STAGE_RANGES: dict[str, tuple[int, int]] = {
    "INPUT_VALIDATE": (0, 2),
    "MEDIA_ACQUIRE": (2, 9),
    "MEDIA_PROBE": (9, 14),
    "AUDIO_EXTRACT": (14, 23),
    "AUDIO_VALIDATE": (23, 25),
    "AUDIO_CHUNK": (25, 29),
    "ASR_TRANSCRIBE": (29, 47),
    "ASR_COMBINE": (47, 51),
    "ASR_VALIDATE": (51, 54),
    "SUBTITLE_IMPORT": (25, 47),
    "SUBTITLE_NORMALIZE": (47, 51),
    "SUBTITLE_VALIDATE": (51, 54),
    "FRAME_EXTRACT": (54, 63),
    "FRAME_DEDUP": (63, 67),
    "OCR_RECOGNIZE": (67, 79),
    "OCR_TIMELINE": (79, 83),
    "FUSION_BUILD": (83, 87),
    "FUSION_VALIDATE": (87, 89),
    "DOCUMENT_BUILD": (89, 92),
    "TEXT_EDIT": (92, 94),
    "MARKDOWN_RENDER": (94, 97),
    "OBSIDIAN_EXPORT": (97, 99),
    "FINALIZE": (99, 100),
}


class StageTracker:
    def __init__(self, task_id: int, workspace: Path, snapshot: dict) -> None:
        self.task_id = task_id
        self.workspace = workspace
        self.snapshot = snapshot
        self.stage: str | None = None
        self.attempt: int | None = None

    def start(
        self,
        stage: str,
        *,
        message: str,
        input_artifacts: list[str] | None = None,
        log_path: str | None = None,
    ) -> None:
        if process_registry.shutting_down:
            raise PipelineInterrupted(stage=stage)
        with SessionLocal() as session:
            task = session.get(Task, self.task_id)
            if not task:
                raise PipelineError("WORKSPACE_ERROR", "任务已不存在")
            if task.cancel_requested:
                raise PipelineCancelled(stage=stage)
            self.stage = stage
            self.attempt = task.attempt
            run = TaskStageRun(
                task_id=task.id,
                attempt=task.attempt,
                stage=stage,
                status="PROCESSING",
                progress=0,
                message=message,
                started_at=utcnow(),
                input_artifacts=input_artifacts,
                model_usage=stage_model_usage(stage, self.snapshot),
                log_path=log_path,
            )
            session.add(run)
            task.current_stage = stage
            task.progress = STAGE_RANGES[stage][0]
            session.commit()
        append_task_log(self.workspace, f"Started real stage {stage}: {message}")
        self._write_state(message)

    def progress(self, fraction: float | None, message: str) -> None:
        if not self.stage or self.attempt is None:
            return
        if process_registry.shutting_down:
            raise PipelineInterrupted(stage=self.stage)
        stage_progress = None if fraction is None else round(max(0.0, min(1.0, fraction)) * 100)
        start, end = STAGE_RANGES[self.stage]
        overall = start if fraction is None else round(start + (end - start) * fraction)
        with SessionLocal() as session:
            task = session.get(Task, self.task_id)
            if not task or task.cancel_requested:
                raise PipelineCancelled(stage=self.stage)
            run = _current_stage(session, self.task_id, self.attempt, self.stage)
            if run:
                if stage_progress is not None:
                    run.progress = stage_progress
                run.message = message[:1000]
            task.progress = overall
            session.commit()
        self._write_state(message)

    def complete(self, *, message: str, output_artifacts: list[str] | None = None) -> None:
        if not self.stage or self.attempt is None:
            return
        _start, end = STAGE_RANGES[self.stage]
        with SessionLocal() as session:
            task = session.get(Task, self.task_id)
            run = _current_stage(session, self.task_id, self.attempt, self.stage)
            if run:
                run.status = "COMPLETED"
                run.progress = 100
                run.message = message
                run.output_artifacts = output_artifacts
                run.finished_at = utcnow()
            if task:
                task.progress = end
            session.commit()
        append_task_log(self.workspace, f"Completed real stage {self.stage}: {message}")
        self._write_state(message)

    def fail(self, error: PipelineError, status: str) -> None:
        if not self.stage or self.attempt is None:
            return
        with SessionLocal() as session:
            run = _current_stage(session, self.task_id, self.attempt, self.stage)
            if run:
                run.status = status
                run.message = error.user_message
                run.error_message = error.user_message
                run.technical_error = error.technical_detail
                run.retryable = error.retryable
                run.log_path = error.log_path or run.log_path
                run.finished_at = utcnow()
            session.commit()

    def _write_state(self, message: str) -> None:
        with SessionLocal() as session:
            task = session.get(Task, self.task_id)
            if task:
                write_task_state(
                    self.workspace,
                    {
                        "task_id": task.id,
                        "status": task.status,
                        "stage": task.current_stage,
                        "progress": task.progress,
                        "message": message,
                        "attempt": task.attempt,
                    },
                )


async def run_real_pipeline(task_id: int) -> None:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            return
        snapshot = copy.deepcopy(task.snapshot.snapshot_json)
        workspace = Path(task.workspace_path)
        source = task.source
        source_type = task.source_type
        attempt = task.attempt
    paths = WorkspacePaths.from_root(workspace)
    paths.create()
    tracker = StageTracker(task_id, workspace, snapshot)

    def cancelled() -> bool:
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            return task is None or task.cancel_requested

    try:
        tracker.start("INPUT_VALIDATE", message="正在验证输入来源")
        kind, normalized_source = classify_input(source, source_type)
        source_record = paths.metadata / "input.json"
        write_json(
            source_record,
            {
                "source_type": kind,
                "original_input": source,
                "normalized_input": normalized_source,
            },
        )
        tracker.complete(message="输入来源有效", output_artifacts=[str(source_record)])

        tools = snapshot.get("tools", {})
        ffmpeg_executable = str(tools.get("ffmpeg") or "ffmpeg")
        ffprobe_executable = str(tools.get("ffprobe") or "ffprobe")
        ytdlp_executable = str(tools.get("ytdlp") or "yt-dlp")
        acquisition_path = paths.metadata / "media_acquisition.json"
        tracker.start(
            "MEDIA_ACQUIRE",
            message="正在获取媒体",
            input_artifacts=[str(source_record)],
        )
        acquisition = _load_acquisition(acquisition_path)
        if acquisition and Path(acquisition.local_media_path).is_file():
            tracker.progress(1.0, "复用当前任务工作区中的已获取媒体")
        else:
            acquisition = await MediaSourceResolver(ytdlp_executable).acquire(
                normalized_source,
                kind,
                paths,
                cancel_check=cancelled,
                progress=tracker.progress,
            )
            write_json(acquisition_path, acquisition.to_dict())
        if acquisition.title:
            with SessionLocal() as session:
                db_task = session.get(Task, task_id)
                if db_task and db_task.title in {source, Path(source).name, Path(source).stem}:
                    db_task.title = acquisition.title[:300]
                    session.commit()
        tracker.complete(
            message="媒体获取完成",
            output_artifacts=[acquisition.local_media_path, acquisition.metadata_path, str(acquisition_path)],
        )

        ffprobe = FfprobeProvider(ffprobe_executable)
        media_info_path = paths.metadata / "media_info.json"
        tracker.start(
            "MEDIA_PROBE",
            message="正在探测音视频流",
            input_artifacts=[acquisition.local_media_path],
            log_path=str(paths.logs / "ffprobe.stderr.log"),
        )
        media_info = _load_media_info(media_info_path)
        if media_info is None:
            media_info = await ffprobe.probe(
                Path(acquisition.local_media_path), paths, cancel_check=cancelled
            )
            media_payload = {**acquisition.to_dict(), **media_info.to_dict()}
            write_json(media_info_path, media_payload)
        else:
            tracker.progress(1.0, "复用已探测媒体信息")
        with SessionLocal() as session:
            db_task = session.get(Task, task_id)
            if db_task:
                db_task.media_info_path = str(media_info_path)
                session.commit()
        tracker.complete(
            message=f"探测完成：音频流 {len(media_info.audio_streams)}，视频流 {len(media_info.video_streams)}",
            output_artifacts=[media_info.raw_probe_path, str(media_info_path)],
        )

        processing = snapshot.get("processing", {})
        subtitle_mode = str(processing.get("text_source") or "ASR") == "SUBTITLE"
        if subtitle_mode:
            audio_path = Path(acquisition.local_media_path)
            audio_info = media_info
        else:
            tracker.start(
                "AUDIO_EXTRACT",
                message="正在提取并标准化音频",
                input_artifacts=[acquisition.local_media_path, str(media_info_path)],
                log_path=str(paths.logs / "ffmpeg_audio.stderr.log"),
            )
            audio_path = paths.audio / "normalized.wav"
            audio_info_path = paths.metadata / "audio_media_info.json"
            audio_info = _load_media_info(audio_info_path)
            if audio_path.is_file() and audio_info is not None:
                tracker.progress(1.0, "复用已完成的标准化音频")
            else:
                audio_path, audio_info = await FfmpegProvider(
                    ffmpeg_executable, ffprobe
                ).normalize_audio(
                    Path(acquisition.local_media_path),
                    media_info,
                    paths,
                    cancel_check=cancelled,
                    progress=tracker.progress,
                )
                write_json(audio_info_path, audio_info.to_dict())
            tracker.complete(
                message="已生成 PCM 16kHz 单声道 WAV",
                output_artifacts=[str(audio_path), str(audio_info_path)],
            )

            tracker.start(
                "AUDIO_VALIDATE",
                message="正在验证标准化音频",
                input_artifacts=[str(audio_path), str(audio_info_path)],
            )
            if (
                not audio_path.is_file()
                or audio_path.stat().st_size <= 44
                or not audio_info.has_audio
                or not audio_info.audio_streams
                or audio_info.audio_streams[0].sample_rate != 16000
                or audio_info.audio_streams[0].channels != 1
            ):
                raise PipelineError("AUDIO_EXTRACTION_FAILED", "标准化音频完整性检查失败")
            tracker.complete(message="标准化音频有效", output_artifacts=[str(audio_path)])

        asr_config = snapshot.get("asr") if isinstance(snapshot.get("asr"), dict) else {}
        if subtitle_mode:
            protocol = "local-subtitle-import"
            base_url = ""
            api_key = ""
            model = "not-applicable"
        else:
            protocol = str(asr_config.get("provider") or "oneapi-openai-compatible")
            if protocol == "oneapi-openai-compatible":
                base_url, api_key, model = load_asr_credentials(snapshot)
            else:
                asr_config, api_key = load_asr_gateway_credentials(snapshot)
                base_url = str(asr_config["base_url"])
                model = str(asr_config["model"])
        continuous_stream = bool(
            protocol == "dashscope-fun-asr-websocket" and asr_config.get("streaming")
        )
        raw_chunk_seconds = snapshot.get("processing", {}).get(
            "audio_chunk_seconds", get_settings().audio_chunk_seconds
        )
        chunk_seconds = None if continuous_stream else float(raw_chunk_seconds)
        overlap_seconds = 0.0 if continuous_stream else float(asr_config.get("overlap_seconds", 5.0))
        if subtitle_mode:
            chunks = [
                AudioChunk(
                    0,
                    0.0,
                    audio_info.duration or 0.0,
                    str(audio_path),
                    provider=protocol,
                    model=model,
                    attempt=attempt,
                )
            ]
        else:
            tracker.start(
                "AUDIO_CHUNK",
                message=(
                    "Fun-ASR 将使用单个连续音频流"
                    if continuous_stream
                    else "正在为长音频生成稳定分块"
                ),
                input_artifacts=[str(audio_path)],
            )
            if continuous_stream:
                if audio_info.duration is None:
                    raise PipelineError("AUDIO_EXTRACTION_FAILED", "无法确定连续音频流时长")
                chunks = [
                    AudioChunk(
                        0,
                        0.0,
                        audio_info.duration,
                        str(audio_path),
                        provider=protocol,
                        model=model,
                        attempt=attempt,
                    )
                ]
                write_json(
                    paths.chunks / "manifest.json",
                    {
                        "schema_version": 2,
                        "strategy": "continuous_stream_no_overlap",
                        "chunk_seconds": None,
                        "step_seconds": None,
                        "overlap_seconds": 0.0,
                        "chunks": [chunks[0].to_manifest_dict()],
                    },
                )
                tracker.progress(1.0, "连续流不重复发送音频，不创建人为重叠分块")
            else:
                chunks = _load_chunks(
                    paths.chunks / "manifest.json",
                    expected_chunk_seconds=chunk_seconds,
                    expected_overlap_seconds=overlap_seconds,
                )
                if not chunks or any(not Path(chunk.path).is_file() for chunk in chunks):
                    chunks = await AudioChunker(ffmpeg_executable).split(
                        audio_path,
                        audio_info.duration,
                        paths,
                        chunk_seconds=chunk_seconds,
                        overlap_seconds=overlap_seconds,
                        provider=protocol,
                        model=model,
                        attempt=attempt,
                        cancel_check=cancelled,
                        progress=tracker.progress,
                    )
                else:
                    tracker.progress(1.0, f"复用 {len(chunks)} 个现有音频分块")
            tracker.complete(
                message=f"音频分块完成，共 {len(chunks)} 块",
                output_artifacts=[chunk.path for chunk in chunks]
                + [str(paths.chunks / "manifest.json")],
            )

        attempt_raw = paths.asr_raw / f"attempt_{attempt:04d}"
        attempt_paths = replace(paths, asr_raw=attempt_raw)
        attempt_paths.asr_raw.mkdir(parents=True, exist_ok=True)
        provider: (
            OpenAICompatibleAsrProvider
            | UnifiedAsrGatewayProvider
            | DashScopeFunAsrProvider
            | None
        ) = None
        provider_options = {
            "base_url": base_url,
            "api_key": api_key,
            "timeout_seconds": float(
                asr_config.get("timeout_seconds", get_settings().asr_request_timeout_seconds)
            ),
            "max_retries": int(asr_config.get("max_retries", get_settings().asr_max_retries)),
        }
        if subtitle_mode:
            provider_name = "imported-subtitle"
        elif protocol == "dashscope-fun-asr-websocket":
            provider = DashScopeFunAsrProvider(
                **provider_options,
                workspace_id=os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip() or None,
            )
        elif protocol == "video2doc-asr-http-v1":
            provider = UnifiedAsrGatewayProvider(**provider_options)
        elif protocol == "oneapi-openai-compatible":
            provider = OpenAICompatibleAsrProvider(**provider_options)
        else:
            raise PipelineError("ASR_CONFIG_INVALID", f"不支持的 ASR Gateway 协议：{protocol}")
        if provider is not None:
            provider_name = provider.name
        subtitle_path = str((snapshot.get("subtitle") or {}).get("workspace_path") or "")
        tracker.start(
            "SUBTITLE_IMPORT" if subtitle_mode else "ASR_TRANSCRIBE",
            message=("正在导入中文字幕并跳过 ASR" if subtitle_mode else "正在提交 ASR 分块"),
            input_artifacts=(
                [subtitle_path] if subtitle_mode else [chunk.path for chunk in chunks]
            ),
        )
        results: list[AsrResult] = []
        language = asr_config.get("language")
        gateway_collection = snapshot.get("gateways")
        asr_gateway = (
            gateway_collection.get("asr")
            if isinstance(gateway_collection, dict)
            else None
        )
        timestamp_granularity = (
            asr_gateway.get("timestamp_strategy")
            if isinstance(asr_gateway, dict)
            else None
        ) or "segment"
        if subtitle_mode:
            if not subtitle_path:
                raise PipelineError("SUBTITLE_CONFIG_INVALID", "任务快照缺少字幕副本路径")
            imported = import_subtitle(Path(subtitle_path))
            results.append(
                AsrResult(
                    provider=provider_name,
                    model=model,
                    language="zh",
                    chunk_index=0,
                    raw_response_reference=subtitle_path,
                    segments=tuple(imported.segments),
                )
            )
            tracker.progress(
                1.0,
                f"已导入 {len(imported.segments)} 条字幕（{imported.subtitle_format.upper()} / "
                f"{imported.encoding}）",
            )
        elif continuous_stream:
            assert provider is not None
            reusable = _find_reusable_asr_response(paths.asr_raw, attempt, 0)
            request = AsrRequest(chunks[0], model, language, timestamp_granularity)
            if reusable:
                result = normalize_dashscope_response(
                    read_json(reusable) or {}, request, reusable, provider.name
                )
            else:
                result = await provider.transcribe_stream(
                    audio_path,
                    model=model,
                    language=language,
                    paths=attempt_paths,
                    cancel_check=cancelled,
                )
            results.append(result)
            _update_chunk_manifest_status(paths.chunks / "manifest.json", 0, "COMPLETED", attempt)
            tracker.progress(1.0, "Fun-ASR 连续音频流识别完成")
        for index, chunk in enumerate(chunks):
            if subtitle_mode or continuous_stream:
                break
            assert provider is not None
            request = AsrRequest(
                chunk=chunk,
                model=model,
                language=language,
                timestamp_granularity=timestamp_granularity,
                request_id=f"task-{task_id}-attempt-{attempt}-chunk-{chunk.index}",
            )
            reusable = _find_reusable_asr_response(paths.asr_raw, attempt, chunk.index)
            if reusable:
                payload = read_json(reusable)
                result = normalize_asr_response(payload or {}, request, reusable, provider.name)
            else:
                result = await provider.transcribe(request, attempt_paths, cancel_check=cancelled)
            results.append(result)
            _update_chunk_manifest_status(
                paths.chunks / "manifest.json", chunk.index, "COMPLETED", attempt
            )
            tracker.progress(
                (index + 1) / len(chunks), f"ASR 已完成 {index + 1}/{len(chunks)} 个分块"
            )
        tracker.complete(
            message=(
                f"中文字幕导入完成，共 {len(results[0].segments)} 条"
                if subtitle_mode
                else f"ASR 分块识别完成，共 {len(results)} 块"
            ),
            output_artifacts=[result.raw_response_reference for result in results],
        )

        tracker.start(
            "SUBTITLE_NORMALIZE" if subtitle_mode else "ASR_COMBINE",
            message=(
                "正在规范化字幕并保留原始时间轴"
                if subtitle_mode
                else "正在映射全局时间轴并合并结果"
            ),
        )
        merge_diagnostics: list[dict] = []
        segments = combine_asr_results(results, chunks, diagnostics=merge_diagnostics)
        artifacts = save_transcript_artifacts(
            paths,
            segments,
            provider=provider_name,
            model=model,
            language=next((item.language for item in results if item.language), None),
            chunk_count=len(chunks),
            merge_diagnostics=merge_diagnostics,
        )
        tracker.complete(
            message=f"已按原媒体时间轴合并 {len(segments)} 个分段",
            output_artifacts=list(artifacts.values()),
        )

        tracker.start(
            "SUBTITLE_VALIDATE" if subtitle_mode else "ASR_VALIDATE",
            message=(
                "正在验证导入字幕的正文与时间顺序"
                if subtitle_mode
                else "正在验证标准化转写结果"
            ),
            input_artifacts=[artifacts["normalized"]],
        )
        validate_segments(segments)
        ocr_enabled = bool(snapshot.get("processing", {}).get("ocr_enabled"))
        if not ocr_enabled:
            tracker.complete(
                message="标准化 ASR 结果有效；任务快照未启用 OCR",
                output_artifacts=[artifacts["normalized"], artifacts["preview"]],
            )
            tracker.start(
                "FUSION_BUILD",
                message="正在构建仅含 ASR 来源的统一时间线",
                input_artifacts=[artifacts["normalized"]],
            )
            asr_payload = read_json(Path(artifacts["normalized"]))
            if asr_payload is None:
                raise PipelineError("FUSION_INPUT_INVALID", "无法读取标准化 ASR 产物")
            disabled_ocr_path = paths.ocr / "disabled_timeline.json"
            disabled_frames_path = paths.frames / "disabled_manifest.json"
            write_json(
                disabled_ocr_path,
                {"schema_version": 1, "event_count": 0, "events": []},
            )
            write_json(
                disabled_frames_path,
                {
                    "schema_version": 1,
                    "candidate_count": 0,
                    "kept_count": 0,
                    "frames": [],
                },
            )
            fusion_payload = build_fusion_timeline(
                asr_payload,
                {"events": []},
                {"frames": []},
                media_duration=media_info.duration,
                association_window_seconds=float(
                    processing.get("fusion_window_seconds", 8.0)
                ),
            )
            fusion_artifacts = save_fusion_artifacts(
                paths,
                fusion_payload,
                asr_path=artifacts["normalized"],
                ocr_path=str(disabled_ocr_path),
                frames_path=str(disabled_frames_path),
            )
            tracker.complete(
                message=f"ASR-only 时间线包含 {fusion_payload['event_count']} 个事件",
                output_artifacts=list(fusion_artifacts.values()),
            )
            tracker.start(
                "FUSION_VALIDATE",
                message="正在验证 ASR-only 时间线与来源引用",
                input_artifacts=[fusion_artifacts["timeline"]],
            )
            with SessionLocal() as session:
                db_task = session.get(Task, task_id)
                if db_task:
                    db_task.fusion_timeline_path = fusion_artifacts["timeline"]
                    session.commit()
            tracker.complete(
                message="ASR-only 结构化融合时间线有效",
                output_artifacts=[fusion_artifacts["timeline"]],
            )
            await _run_document_pipeline(
                task_id,
                attempt=attempt,
                tracker=tracker,
                paths=paths,
                snapshot=snapshot,
                acquisition=acquisition,
                media_info=media_info,
                transcript_artifacts=artifacts,
                fusion_payload=fusion_payload,
                fusion_artifacts=fusion_artifacts,
                cancelled=cancelled,
            )
            return
        tracker.complete(
            message="标准化 ASR 结果有效，继续执行 OCR 分支",
            output_artifacts=[artifacts["normalized"], artifacts["preview"]],
        )
        if not media_info.has_video or media_info.duration is None:
            raise PipelineError(
                "NO_VIDEO_STREAM",
                "任务启用了 OCR，但媒体中没有可用视频流或可靠时长",
            )
        frame_config = FrameExtractionConfig(
            strategy=str(processing.get("frame_sampling_strategy") or "hybrid"),
            interval_seconds=float(
                processing.get("ocr_frame_interval_seconds", 15.0)
            ),
            scene_change_threshold=float(
                processing.get("scene_change_threshold", 0.35)
            ),
            scene_min_gap_seconds=float(processing.get("scene_min_gap_seconds", 1.5)),
            max_candidate_frames=int(processing.get("max_candidate_frames", 240)),
            normalized_max_width=int(
                processing.get("frame_normalized_max_width", 1600)
            ),
            perceptual_hamming_threshold=int(
                processing.get("frame_similarity_hamming_threshold", 4)
            ),
            visual_difference_threshold=float(
                processing.get("frame_similarity_difference_threshold", 0.025)
            ),
            transition_burst_seconds=float(
                processing.get("frame_transition_burst_seconds", 3.0)
            ),
        )
        tracker.start(
            "FRAME_EXTRACT",
            message="正在按媒体时间轴抽取固定间隔与画面变化候选帧",
            input_artifacts=[acquisition.local_media_path],
            log_path=str(paths.logs / "ffmpeg_frames.stderr.log"),
        )
        frame_result = await VideoFrameExtractor(ffmpeg_executable).extract(
            Path(acquisition.local_media_path),
            duration=media_info.duration,
            paths=paths,
            config=frame_config,
            cancel_check=cancelled,
            progress=tracker.progress,
        )
        with SessionLocal() as session:
            db_task = session.get(Task, task_id)
            if db_task:
                db_task.frames_manifest_path = frame_result.manifest_path
                session.commit()
        tracker.complete(
            message=f"抽取候选帧 {len(frame_result.records)} 个",
            output_artifacts=[frame_result.manifest_path],
        )

        tracker.start(
            "FRAME_DEDUP",
            message="正在验证画面变化与感知相似度去重决策",
            input_artifacts=[frame_result.manifest_path],
        )
        if not frame_result.kept:
            raise PipelineError("OCR_NO_FRAMES", "候选帧去重后没有可用于 OCR 的画面")
        timestamps = [item.media_timestamp for item in frame_result.records]
        if timestamps != sorted(timestamps):
            raise PipelineError("FRAME_TIMESTAMP_INVALID", "候选帧时间戳顺序不稳定")
        tracker.progress(
            1.0,
            f"保留 {len(frame_result.kept)} 帧，去重 {frame_result.dropped_count} 帧",
        )
        tracker.complete(
            message=f"画面去重完成：保留 {len(frame_result.kept)}，"
            f"丢弃 {frame_result.dropped_count}",
            output_artifacts=[
                item.normalized_image_path for item in frame_result.kept
            ],
        )

        ocr_config = snapshot.get("ocr")
        if not isinstance(ocr_config, dict):
            raise PipelineError("OCR_CONFIG_INVALID", "任务快照中没有 OCR Gateway 配置")
        tracker.start(
            "OCR_RECOGNIZE",
            message="正在通过独立 OCR Gateway 识别保留帧",
            input_artifacts=[
                item.normalized_image_path for item in frame_result.kept
            ],
        )
        ocr_run = await recognize_retained_frames(
            frame_result,
            ocr_config=ocr_config,
            attempt=attempt,
            paths=paths,
            cancel_check=cancelled,
            progress=tracker.progress,
        )
        tracker.complete(
            message=f"OCR 完成 {ocr_run.processed_count} 帧，"
            f"复用 {ocr_run.reused_count} 帧",
            output_artifacts=[
                ocr_run.raw_index_path,
                ocr_run.normalized_path,
                ocr_run.preview_path,
            ],
        )

        tracker.start(
            "OCR_TIMELINE",
            message="正在按图像、文本、空间位置和时间建立 OCR 稳定事件",
            input_artifacts=[ocr_run.normalized_path, frame_result.manifest_path],
        )
        ocr_timeline = build_ocr_timeline(
            ocr_run.results,
            frame_result,
            text_similarity_threshold=float(
                processing.get("ocr_text_similarity_threshold", 0.88)
            ),
            incremental_similarity_threshold=float(
                processing.get("ocr_incremental_similarity_threshold", 0.55)
            ),
            max_event_gap_seconds=float(
                processing.get("ocr_event_gap_seconds", 20.0)
            ),
        )
        ocr_timeline_path = save_ocr_timeline(paths, ocr_timeline)
        with SessionLocal() as session:
            db_task = session.get(Task, task_id)
            if db_task:
                db_task.ocr_timeline_path = ocr_timeline_path
                session.commit()
        tracker.progress(1.0, f"生成 {ocr_timeline['event_count']} 个 OCR 事件")
        tracker.complete(
            message=f"OCR 时间线完成，合并 "
            f"{ocr_timeline['merged_observation_count']} 个稳定重复观察",
            output_artifacts=[ocr_timeline_path],
        )

        tracker.start(
            "FUSION_BUILD",
            message="正在保留来源差异并关联临近 ASR/OCR/帧事件",
            input_artifacts=[
                artifacts["normalized"],
                ocr_timeline_path,
                frame_result.manifest_path,
            ],
        )
        asr_payload = read_json(Path(artifacts["normalized"]))
        frame_payload = read_json(Path(frame_result.manifest_path))
        if asr_payload is None or frame_payload is None:
            raise PipelineError("FUSION_INPUT_INVALID", "无法读取融合输入产物")
        fusion_payload = build_fusion_timeline(
            asr_payload,
            ocr_timeline,
            frame_payload,
            media_duration=media_info.duration,
            association_window_seconds=float(
                processing.get("fusion_window_seconds", 8.0)
            ),
        )
        fusion_artifacts = save_fusion_artifacts(
            paths,
            fusion_payload,
            asr_path=artifacts["normalized"],
            ocr_path=ocr_timeline_path,
            frames_path=frame_result.manifest_path,
        )
        tracker.progress(1.0, f"统一时间线包含 {fusion_payload['event_count']} 个事件")
        tracker.complete(
            message="ASR、OCR 与帧事件已按媒体时间排序并建立关联",
            output_artifacts=list(fusion_artifacts.values()),
        )

        tracker.start(
            "FUSION_VALIDATE",
            message="正在验证融合时间、来源引用和产物完整性",
            input_artifacts=[fusion_artifacts["timeline"]],
        )
        with SessionLocal() as session:
            db_task = session.get(Task, task_id)
            if db_task:
                db_task.fusion_timeline_path = fusion_artifacts["timeline"]
                session.commit()
        tracker.complete(
            message="OCR 与 ASR/OCR 结构化融合链路完成",
            output_artifacts=[
                fusion_artifacts["timeline"],
                fusion_artifacts["preview"],
            ],
        )
        await _run_document_pipeline(
            task_id,
            attempt=attempt,
            tracker=tracker,
            paths=paths,
            snapshot=snapshot,
            acquisition=acquisition,
            media_info=media_info,
            transcript_artifacts=artifacts,
            fusion_payload=fusion_payload,
            fusion_artifacts=fusion_artifacts,
            cancelled=cancelled,
            frames_manifest_path=frame_result.manifest_path,
            ocr_timeline_path=ocr_timeline_path,
        )
    except PipelineError as exc:
        if not exc.stage:
            exc.stage = tracker.stage
        status = "CANCELLED" if exc.code == "PROCESS_CANCELLED" else (
            "INTERRUPTED" if exc.code == "backend_shutdown" else "FAILED"
        )
        tracker.fail(exc, status)
        _finish_task(task_id, status, exc)
    except OSError as exc:
        failure = PipelineError(
            "WORKSPACE_ERROR",
            "无法读写任务工作区",
            technical_detail=f"{type(exc).__name__}: {exc}",
            stage=tracker.stage,
            retryable=True,
        )
        tracker.fail(failure, "FAILED")
        _finish_task(task_id, "FAILED", failure)
    except Exception as exc:
        logger.exception("Unexpected real Pipeline failure for task %s", task_id)
        failure = PipelineError(
            "pipeline_error",
            "处理过程中发生未预期错误",
            technical_detail=f"{type(exc).__name__}: {exc}",
            stage=tracker.stage,
        )
        tracker.fail(failure, "FAILED")
        _finish_task(task_id, "FAILED", failure)


async def _run_document_pipeline(
    task_id: int,
    *,
    attempt: int,
    tracker: StageTracker,
    paths: WorkspacePaths,
    snapshot: dict,
    acquisition: MediaAcquisitionResult,
    media_info: MediaInfo,
    transcript_artifacts: dict[str, str],
    fusion_payload: dict,
    fusion_artifacts: dict[str, str],
    cancelled,
    frames_manifest_path: str | None = None,
    ocr_timeline_path: str | None = None,
) -> None:
    processing = snapshot.get("processing")
    if not isinstance(processing, dict):
        processing = {}
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            raise PipelineError("WORKSPACE_ERROR", "任务已不存在")
        title = task.title

    original_document_path = paths.output / "document.original.json"
    tracker.start(
        "DOCUMENT_BUILD",
        message="正在从 ASR、OCR、帧和媒体元数据构建结构化文档",
        input_artifacts=[
            transcript_artifacts["normalized"],
            fusion_artifacts["timeline"],
        ],
    )
    document = build_document(
        fusion_payload,
        title=title,
        source=acquisition.original_input,
        source_type=acquisition.source_type,
        media_info=media_info.to_dict(),
        models=snapshot.get("models") or {},
        processing=processing,
        artifact_references={
            "transcript": transcript_artifacts["normalized"],
            "fusion_timeline": fusion_artifacts["timeline"],
            **(
                {"frames_manifest": frames_manifest_path}
                if frames_manifest_path
                else {}
            ),
            **(
                {"ocr_timeline": ocr_timeline_path}
                if ocr_timeline_path
                else {}
            ),
        },
        document_id=f"task-{task_id:06d}-attempt-{attempt:04d}",
        image_min_gap_seconds=float(processing.get("image_min_gap_seconds", 8.0)),
    )
    write_json(original_document_path, document.model_dump(mode="json"))
    tracker.progress(
        1.0,
        f"结构化文档包含 {len(document.sections)} 节、"
        f"{len(document.all_blocks())} 个块",
    )
    tracker.complete(
        message="结构化文档原始版本已生成，所有块均保留来源引用",
        output_artifacts=[str(original_document_path)],
    )

    tracker.start(
        "TEXT_EDIT",
        message="正在执行受约束的忠实文本整理或无模型回退",
        input_artifacts=[str(original_document_path)],
    )
    text_config = snapshot.get("text_processing")
    if not isinstance(text_config, dict):
        text_config = {}
    edit_enabled = bool(text_config.get("enabled", processing.get("text_edit_enabled", False)))
    edit_raw_path: Path | None = None
    if edit_enabled:
        base_url = str(text_config.get("base_url") or "").strip()
        model = str(text_config.get("model") or "").strip()
        secret_env_name = str(
            text_config.get("secret_env_name") or "ONEAPI_API_KEY"
        )
        api_key = os.getenv(secret_env_name, "").strip()
        if not base_url or not model:
            raise PipelineError(
                "TEXT_CONFIG_INVALID",
                "任务快照启用了文本整理，但缺少 Gateway Base URL 或模型",
            )
        if not api_key:
            raise PipelineError(
                "TEXT_GATEWAY_SECRET_MISSING",
                f"缺少文本整理 Gateway 密钥环境变量 {secret_env_name}",
            )
        edit_raw_path = (
            paths.output
            / "text_edit"
            / f"attempt_{attempt:04d}_provider_raw.json"
        )
        document, edit_run = await FaithfulTextEditor(
            base_url=base_url,
            api_key=api_key,
            model=model,
        ).edit(
            document,
            raw_path=edit_raw_path,
            minimum_length_ratio=float(
                text_config.get("minimum_length_ratio", 0.72)
            ),
            formula_symbol_ratio=float(
                text_config.get("formula_symbol_ratio", 0.8)
            ),
            cancel_check=cancelled,
        )
        edit_run["provider"] = str(
            text_config.get("provider") or "oneapi-openai-compatible"
        )
    else:
        edit_run = {
            "schema_version": 1,
            "provider": "none",
            "model": None,
            "prompt_version": str(
                text_config.get("prompt_version")
                or processing.get("prompt_version")
                or PROMPT_VERSION
            ),
            "enabled": False,
            "accepted": True,
            "fallback_used": False,
            "reason": "no_model_mode",
            "elapsed_seconds": 0.0,
            "block_runs": [],
        }
    document.edit_metadata = edit_run
    report = validate_document_faithfulness(
        document,
        minimum_length_ratio=float(
            text_config.get("minimum_length_ratio", 0.72)
        ),
        formula_symbol_ratio=float(
            text_config.get("formula_symbol_ratio", 0.8)
        ),
    )
    if not report.accepted:
        raise PipelineError(
            "DOCUMENT_FAITHFULNESS_FAILED",
            "文档忠实性自动检查失败",
            technical_detail="; ".join(
                f"{item.code}:{item.block_id or '-'}" for item in report.issues
            ),
        )
    document_path = paths.output / "document.json"
    validation_path = paths.output / "text_edit" / (
        f"attempt_{attempt:04d}_validation.json"
    )
    write_json(document_path, document.model_dump(mode="json"))
    write_json(
        validation_path,
        {
            "schema_version": 1,
            "edit_run": edit_run,
            "faithfulness": {
                "accepted": report.accepted,
                "metrics": report.metrics,
                "issues": [
                    {
                        "code": item.code,
                        "message": item.message,
                        "block_id": item.block_id,
                    }
                    for item in report.issues
                ],
            },
        },
    )
    _record_document_and_edit(
        task_id,
        attempt,
        document,
        document_path,
        validation_path,
        edit_raw_path,
        edit_run,
    )
    tracker.complete(
        message=(
            "文本整理已通过忠实性校验"
            if edit_enabled and edit_run.get("accepted")
            else (
                "文本模型响应被拒绝，已忠实回退到未整理文本"
                if edit_enabled
                else "未启用文本模型，已使用确定性忠实整理"
            )
        ),
        output_artifacts=[
            str(document_path),
            str(validation_path),
            *([str(edit_raw_path)] if edit_raw_path else []),
        ],
    )

    source_sidecar = {
        "schema_version": 1,
        "document_id": document.document_id,
        "source_references": [
            reference.model_dump(mode="json")
            for block in document.all_blocks()
            for reference in block.source_references
        ],
    }
    sidecars = {
        "timeline.json": fusion_payload,
        "sources.json": source_sidecar,
    }
    additional_files: dict[str, Path] = {}
    export_snapshot = _export_snapshot(snapshot)
    if export_snapshot.copy_transcript:
        additional_files["transcript.normalized.json"] = Path(
            transcript_artifacts["normalized"]
        )

    tracker.start(
        "MARKDOWN_RENDER",
        message="正在渲染工作区 Markdown 并复制相对路径附件",
        input_artifacts=[str(document_path)],
    )
    workspace_config = export_snapshot.model_copy(
        update={
            "vault_path": str(paths.output),
            "inbox_relative_path": ".",
            "conflict_policy": "overwrite",
            "keep_json_sidecar": True,
        }
    )
    workspace_result = export_document_bundle(
        document,
        workspace_config,
        sidecars=sidecars,
        additional_files=additional_files,
        cancel_check=cancelled,
    )
    _validate_rendered_markdown(Path(workspace_result.markdown_path), document)
    _record_export(
        task_id,
        attempt,
        "WORKSPACE",
        "COMPLETED",
        workspace_config,
        workspace_result,
    )
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if task:
            task.markdown_path = workspace_result.markdown_path
            session.commit()
    tracker.complete(
        message=f"工作区 Markdown 已生成，包含 {workspace_result.image_count} 张图片",
        output_artifacts=[
            workspace_result.markdown_path,
            *(
                [workspace_result.attachment_directory]
                if workspace_result.attachment_directory
                else []
            ),
            *(
                [workspace_result.data_directory]
                if workspace_result.data_directory
                else []
            ),
        ],
    )

    tracker.start(
        "OBSIDIAN_EXPORT",
        message="正在原子导出 Markdown、附件和追溯 sidecar 到 Obsidian Inbox",
        input_artifacts=[workspace_result.markdown_path],
    )
    try:
        export_result = export_document_bundle(
            document,
            export_snapshot,
            sidecars=sidecars,
            additional_files=additional_files,
            cancel_check=cancelled,
        )
    except PipelineError as exc:
        _record_export(
            task_id,
            attempt,
            "OBSIDIAN",
            "FAILED",
            export_snapshot,
            error_message=exc.user_message,
        )
        raise
    _record_export(
        task_id,
        attempt,
        "OBSIDIAN",
        "COMPLETED",
        export_snapshot,
        export_result,
    )
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if task:
            task.export_path = export_result.markdown_path
            session.commit()
    tracker.complete(
        message=(
            f"Obsidian 导出完成：图片 {export_result.image_count}，"
            f"sidecar/原始产物 {export_result.sidecar_count}"
        ),
        output_artifacts=[
            export_result.markdown_path,
            *(
                [export_result.attachment_directory]
                if export_result.attachment_directory
                else []
            ),
            *(
                [export_result.data_directory]
                if export_result.data_directory
                else []
            ),
        ],
    )

    tracker.start(
        "FINALIZE",
        message="正在更新任务索引并完成可恢复状态",
        input_artifacts=[
            document_path.as_posix(),
            workspace_result.markdown_path,
            export_result.markdown_path,
        ],
    )
    artifact_index = write_artifact_index(paths.root)
    tracker.complete(
        message="完整转换与 Obsidian 导出链路完成",
        output_artifacts=[str(artifact_index)],
    )
    _complete_task(
        task_id,
        export_result.markdown_path,
        transcript_artifacts["normalized"],
        artifact_index,
        frames_manifest_path=frames_manifest_path,
        ocr_timeline_path=ocr_timeline_path,
        fusion_timeline_path=fusion_artifacts["timeline"],
        document_path=str(document_path),
        markdown_path=workspace_result.markdown_path,
        export_path=export_result.markdown_path,
    )


def _export_snapshot(snapshot: dict) -> ExportConfig:
    raw = snapshot.get("export")
    if isinstance(raw, dict):
        return ExportConfig.model_validate(raw)
    inbox = Path(str((snapshot.get("paths") or {}).get("inbox") or "")).resolve()
    return ExportConfig(
        vault_path=str(inbox.parent),
        inbox_relative_path=inbox.name,
        filename_template="{title}",
        conflict_policy="rename",
        keep_json_sidecar=True,
    )


def _record_document_and_edit(
    task_id: int,
    attempt: int,
    document: Document,
    document_path: Path,
    validation_path: Path,
    edit_raw_path: Path | None,
    edit_run: dict,
) -> None:
    with SessionLocal() as session:
        session.add(
            DocumentArtifact(
                task_id=task_id,
                attempt=attempt,
                schema_version=document.schema_version,
                document_path=str(document_path),
                block_count=len(document.all_blocks()),
                source_reference_count=len(document.source_ids()),
            )
        )
        session.add(
            TextEditRun(
                task_id=task_id,
                attempt=attempt,
                provider=str(edit_run.get("provider") or "none"),
                model=(
                    str(edit_run["model"])
                    if edit_run.get("model") is not None
                    else None
                ),
                prompt_version=str(edit_run.get("prompt_version") or PROMPT_VERSION),
                accepted=bool(edit_run.get("accepted")),
                fallback_used=bool(edit_run.get("fallback_used")),
                raw_response_path=str(edit_raw_path) if edit_raw_path else None,
                validation_path=str(validation_path),
                elapsed_seconds=float(edit_run.get("elapsed_seconds") or 0.0),
            )
        )
        task = session.get(Task, task_id)
        if task:
            task.document_path = str(document_path)
        session.commit()


def _record_export(
    task_id: int,
    attempt: int,
    target_type: str,
    status: str,
    config: ExportConfig,
    result: ExportResult | None = None,
    *,
    error_message: str | None = None,
) -> None:
    with SessionLocal() as session:
        session.add(
            MarkdownExport(
                task_id=task_id,
                attempt=attempt,
                target_type=target_type,
                status=status,
                markdown_path=result.markdown_path if result else None,
                attachment_directory=(
                    result.attachment_directory if result else None
                ),
                data_directory=result.data_directory if result else None,
                config_json=config.model_dump(mode="json"),
                image_count=result.image_count if result else 0,
                sidecar_count=result.sidecar_count if result else 0,
                error_message=error_message,
            )
        )
        session.commit()


def _validate_rendered_markdown(path: Path, document: Document) -> None:
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PipelineError(
            "MARKDOWN_READ_FAILED",
            "无法读取已渲染 Markdown",
            technical_detail=str(exc),
        ) from exc
    if not markdown.startswith("---\n") or "\n---\n" not in markdown[4:]:
        raise PipelineError("MARKDOWN_INVALID", "Markdown YAML frontmatter 不完整")
    transcript_text = [
        block.display_text
        for block in document.all_blocks()
        if block.block_type == "transcript"
    ]
    if not transcript_text or not all(text in markdown for text in transcript_text):
        raise PipelineError("MARKDOWN_TRANSCRIPT_LOST", "Markdown 未完整保留转写正文")
    for block in document.all_blocks():
        if block.block_type == "image" and block.image_source_path:
            if block.image_alt and block.image_alt not in markdown:
                raise PipelineError("MARKDOWN_IMAGE_LOST", "Markdown 缺少应插入的相关画面")


def _current_stage(session, task_id: int, attempt: int, stage: str) -> TaskStageRun | None:
    return session.scalar(
        select(TaskStageRun).where(
            TaskStageRun.task_id == task_id,
            TaskStageRun.attempt == attempt,
            TaskStageRun.stage == stage,
        )
    )


def _complete_task(
    task_id: int,
    output_path: str,
    transcript_path: str,
    index_path: Path,
    *,
    frames_manifest_path: str | None = None,
    ocr_timeline_path: str | None = None,
    fusion_timeline_path: str | None = None,
    document_path: str | None = None,
    markdown_path: str | None = None,
    export_path: str | None = None,
) -> None:
    with SessionLocal() as session:
        result = session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == "PROCESSING", Task.cancel_requested.is_(False))
            .values(
                status="COMPLETED",
                current_stage="COMPLETED",
                progress=100,
                completed_at=utcnow(),
                output_path=output_path,
                transcript_path=transcript_path,
                frames_manifest_path=frames_manifest_path,
                ocr_timeline_path=ocr_timeline_path,
                fusion_timeline_path=fusion_timeline_path,
                document_path=document_path,
                markdown_path=markdown_path,
                export_path=export_path,
                artifacts_index_path=str(index_path),
                error_code=None,
                error_message=None,
            )
        )
        session.commit()
        if result.rowcount != 1:
            raise PipelineCancelled(stage="FINALIZE")


def _finish_task(task_id: int, status: str, error: PipelineError) -> None:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            return
        task.status = status
        task.current_stage = status
        task.error_code = error.code
        task.error_message = error.user_message
        task.completed_at = utcnow()
        task.cancel_requested = False
        session.commit()
        workspace = Path(task.workspace_path)
        append_task_log(
            workspace,
            f"Real Pipeline {status.lower()} [{error.code}]: {error.user_message}; "
            f"technical={error.technical_detail or '-'}",
        )
        write_task_state(
            workspace,
            {
                "task_id": task.id,
                "status": status,
                "stage": status,
                "progress": task.progress,
                "error_code": error.code,
                "message": error.user_message,
            },
        )


def _load_acquisition(path: Path) -> MediaAcquisitionResult | None:
    payload = read_json(path)
    if not payload:
        return None
    try:
        return MediaAcquisitionResult(**payload)
    except TypeError:
        return None


def _load_media_info(path: Path) -> MediaInfo | None:
    payload = read_json(path)
    if not payload:
        return None
    try:
        audio = tuple(MediaStream(**item) for item in payload.get("audio_streams", []))
        video = tuple(MediaStream(**item) for item in payload.get("video_streams", []))
        return MediaInfo(
            container=payload.get("container"),
            duration=payload.get("duration"),
            has_audio=bool(payload.get("has_audio")),
            has_video=bool(payload.get("has_video")),
            selected_audio_stream=payload.get("selected_audio_stream"),
            audio_streams=audio,
            video_streams=video,
            raw_probe_path=str(payload.get("raw_probe_path", "")),
        )
    except (TypeError, ValueError):
        return None


def _load_chunks(
    path: Path,
    *,
    expected_chunk_seconds: float | None = None,
    expected_overlap_seconds: float | None = None,
) -> list[AudioChunk]:
    payload = read_json(path)
    if not payload or not isinstance(payload.get("chunks"), list):
        return []
    if expected_chunk_seconds is not None and abs(
        float(payload.get("chunk_seconds", -1)) - expected_chunk_seconds
    ) > 0.001:
        return []
    if expected_overlap_seconds is not None and abs(
        float(payload.get("overlap_seconds", -1)) - expected_overlap_seconds
    ) > 0.001:
        return []
    try:
        chunks: list[AudioChunk] = []
        for item in payload["chunks"]:
            if "chunk_index" in item:
                chunks.append(
                    AudioChunk(
                        index=int(item["chunk_index"]),
                        start_time=float(item["source_start_seconds"]),
                        end_time=float(item["source_end_seconds"]),
                        path=str(item["audio_path"]),
                        overlap_before_seconds=float(item.get("overlap_before_seconds", 0)),
                        overlap_after_seconds=float(item.get("overlap_after_seconds", 0)),
                        provider=item.get("provider"),
                        model=item.get("model"),
                        attempt=int(item.get("attempt", 1)),
                        status=str(item.get("status", "READY")),
                    )
                )
            else:
                chunks.append(AudioChunk(**item))
        return chunks
    except (TypeError, ValueError):
        return []


def _update_chunk_manifest_status(path: Path, chunk_index: int, status: str, attempt: int) -> None:
    payload = read_json(path)
    if not payload or not isinstance(payload.get("chunks"), list):
        return
    for item in payload["chunks"]:
        item_index = item.get("chunk_index", item.get("index"))
        if item_index == chunk_index:
            item["status"] = status
            item["attempt"] = attempt
            break
    write_json(path, payload)


def _find_reusable_asr_response(root: Path, attempt: int, chunk_index: int) -> Path | None:
    for previous in range(attempt - 1, 0, -1):
        candidate = root / f"attempt_{previous:04d}" / f"chunk_{chunk_index:04d}.json"
        if candidate.is_file():
            return candidate
    return None
