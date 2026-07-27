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
from app.models import Task, TaskStageRun
from app.models.entities import utcnow
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
    "INPUT_VALIDATE": (0, 3),
    "MEDIA_ACQUIRE": (3, 12),
    "MEDIA_PROBE": (12, 18),
    "AUDIO_EXTRACT": (18, 30),
    "AUDIO_VALIDATE": (30, 33),
    "AUDIO_CHUNK": (33, 38),
    "ASR_TRANSCRIBE": (38, 58),
    "ASR_COMBINE": (58, 63),
    "ASR_VALIDATE": (63, 66),
    "FRAME_EXTRACT": (66, 75),
    "FRAME_DEDUP": (75, 79),
    "OCR_RECOGNIZE": (79, 91),
    "OCR_TIMELINE": (91, 95),
    "FUSION_BUILD": (95, 99),
    "FUSION_VALIDATE": (99, 100),
}


class StageTracker:
    def __init__(self, task_id: int, workspace: Path) -> None:
        self.task_id = task_id
        self.workspace = workspace
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
    tracker = StageTracker(task_id, workspace)

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
        tracker.start(
            "AUDIO_CHUNK",
            message=("Fun-ASR 将使用单个连续音频流" if continuous_stream else "正在为长音频生成稳定分块"),
            input_artifacts=[str(audio_path)],
        )
        overlap_seconds = 0.0 if continuous_stream else float(asr_config.get("overlap_seconds", 5.0))
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
            output_artifacts=[chunk.path for chunk in chunks] + [str(paths.chunks / "manifest.json")],
        )

        attempt_raw = paths.asr_raw / f"attempt_{attempt:04d}"
        attempt_paths = replace(paths, asr_raw=attempt_raw)
        attempt_paths.asr_raw.mkdir(parents=True, exist_ok=True)
        provider: OpenAICompatibleAsrProvider | UnifiedAsrGatewayProvider | DashScopeFunAsrProvider
        provider_options = {
            "base_url": base_url,
            "api_key": api_key,
            "timeout_seconds": float(
                asr_config.get("timeout_seconds", get_settings().asr_request_timeout_seconds)
            ),
            "max_retries": int(asr_config.get("max_retries", get_settings().asr_max_retries)),
        }
        if protocol == "dashscope-fun-asr-websocket":
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
        tracker.start(
            "ASR_TRANSCRIBE",
            message="正在提交 ASR 分块",
            input_artifacts=[chunk.path for chunk in chunks],
        )
        results: list[AsrResult] = []
        language = asr_config.get("language")
        timestamp_granularity = (
            snapshot.get("gateways", {}).get("asr", {}).get("timestamp_strategy") or "segment"
        )
        if continuous_stream:
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
            if continuous_stream:
                break
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
            message=f"ASR 分块识别完成，共 {len(results)} 块",
            output_artifacts=[result.raw_response_reference for result in results],
        )

        tracker.start("ASR_COMBINE", message="正在映射全局时间轴并合并结果")
        merge_diagnostics: list[dict] = []
        segments = combine_asr_results(results, chunks, diagnostics=merge_diagnostics)
        artifacts = save_transcript_artifacts(
            paths,
            segments,
            provider=provider.name,
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
            "ASR_VALIDATE",
            message="正在验证标准化转写结果",
            input_artifacts=[artifacts["normalized"]],
        )
        validate_segments(segments)
        ocr_enabled = bool(snapshot.get("processing", {}).get("ocr_enabled"))
        if not ocr_enabled:
            artifact_index = write_artifact_index(workspace)
            tracker.complete(
                message="真实媒体与 ASR 核心链路完成；任务快照未启用 OCR",
                output_artifacts=[
                    artifacts["normalized"],
                    artifacts["preview"],
                    str(artifact_index),
                ],
            )
            _complete_task(
                task_id,
                artifacts["preview"],
                artifacts["normalized"],
                artifact_index,
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

        processing = snapshot.get("processing", {})
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
        artifact_index = write_artifact_index(workspace)
        with SessionLocal() as session:
            db_task = session.get(Task, task_id)
            if db_task:
                db_task.fusion_timeline_path = fusion_artifacts["timeline"]
                db_task.artifacts_index_path = str(artifact_index)
                session.commit()
        tracker.complete(
            message="OCR 与 ASR/OCR 结构化融合链路完成",
            output_artifacts=[
                fusion_artifacts["timeline"],
                fusion_artifacts["preview"],
                str(artifact_index),
            ],
        )
        _complete_task(
            task_id,
            fusion_artifacts["preview"],
            artifacts["normalized"],
            artifact_index,
            frames_manifest_path=frame_result.manifest_path,
            ocr_timeline_path=ocr_timeline_path,
            fusion_timeline_path=fusion_artifacts["timeline"],
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
    preview_path: str,
    transcript_path: str,
    index_path: Path,
    *,
    frames_manifest_path: str | None = None,
    ocr_timeline_path: str | None = None,
    fusion_timeline_path: str | None = None,
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
                output_path=preview_path,
                transcript_path=transcript_path,
                frames_manifest_path=frames_manifest_path,
                ocr_timeline_path=ocr_timeline_path,
                fusion_timeline_path=fusion_timeline_path,
                artifacts_index_path=str(index_path),
                error_code=None,
                error_message=None,
            )
        )
        session.commit()
        if result.rowcount != 1:
            raise PipelineCancelled(stage="ASR_VALIDATE")


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
