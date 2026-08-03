from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.errors import PipelineError
from app.providers.process import CancelCheck, ExternalProcessRunner
from app.storage.workspace import WorkspacePaths, write_json


ProgressCallback = Callable[[float | None, str], None]


@dataclass(frozen=True, slots=True)
class MediaStream:
    index: int
    type: str
    codec: str | None
    duration: float | None
    sample_rate: int | None = None
    channels: int | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None


@dataclass(frozen=True, slots=True)
class MediaInfo:
    container: str | None
    duration: float | None
    has_audio: bool
    has_video: bool
    selected_audio_stream: int | None
    audio_streams: tuple[MediaStream, ...]
    video_streams: tuple[MediaStream, ...]
    raw_probe_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MediaAcquisitionResult:
    source_type: str
    original_input: str
    local_media_path: str
    title: str | None
    duration: float | None
    container: str | None
    has_audio: bool | None
    has_video: bool | None
    metadata_path: str
    provider: str
    media_available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_input(source: str, declared_type: str) -> tuple[str, str]:
    source = source.strip()
    parsed = urlparse(source)
    is_url = parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
    if declared_type == "URL":
        if not is_url:
            raise PipelineError("INVALID_INPUT", "URL 输入必须使用 http 或 https 协议")
        if parsed.username or parsed.password:
            raise PipelineError("INVALID_INPUT", "URL 中不能包含用户名或密码")
        return "URL", source
    if is_url:
        raise PipelineError("INVALID_INPUT", "本地文件输入不能填写 URL")
    if Path(source).anchor == "" and not Path(source).is_absolute():
        raise PipelineError("INVALID_INPUT", "本地媒体必须使用绝对路径")
    try:
        resolved = Path(source).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PipelineError(
            "INPUT_NOT_FOUND", "找不到指定的本地媒体文件", technical_detail=str(exc)
        ) from exc
    if not resolved.is_file():
        raise PipelineError("INVALID_INPUT", "本地媒体输入必须是文件，不能是目录")
    return "LOCAL_FILE", str(resolved)


class LocalFileSource:
    async def acquire(
        self,
        source: str,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
        progress: ProgressCallback,
    ) -> MediaAcquisitionResult:
        _kind, resolved = classify_input(source, "LOCAL_FILE")
        if await _cancelled(cancel_check):
            from app.core.errors import PipelineCancelled

            raise PipelineCancelled(stage="MEDIA_ACQUIRE")
        source_path = Path(resolved)
        metadata_path = paths.metadata / "source.json"
        payload = {
            "source_type": "LOCAL_FILE",
            "original_input": source,
            "normalized_path": resolved,
            "storage_strategy": "reference_original_read_only",
            "file_name": source_path.name,
            "file_size": source_path.stat().st_size,
            "provider": "local-file",
        }
        write_json(metadata_path, payload)
        progress(1.0, "已引用本地媒体文件")
        return MediaAcquisitionResult(
            source_type="LOCAL_FILE",
            original_input=source,
            local_media_path=resolved,
            title=source_path.stem,
            duration=None,
            container=None,
            has_audio=None,
            has_video=None,
            metadata_path=str(metadata_path),
            provider="local-file",
        )


class YtDlpSource:
    def __init__(self, executable: str, runner: ExternalProcessRunner | None = None) -> None:
        self.executable = executable
        self.runner = runner or ExternalProcessRunner()

    async def acquire(
        self,
        source: str,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
        progress: ProgressCallback,
    ) -> MediaAcquisitionResult:
        classify_input(source, "URL")
        raw_metadata_path = paths.metadata / "ytdlp.json"
        try:
            metadata_result = await self.runner.run(
                [self.executable, "--dump-single-json", "--no-playlist", "--no-warnings", source],
                cwd=paths.root,
                log_dir=paths.logs,
                log_name="ytdlp_metadata",
                timeout_seconds=get_settings().media_process_timeout_seconds,
                cancel_check=cancel_check,
            )
        except PipelineError as exc:
            raise _translate_tool_error(exc, "YTDLP_UNAVAILABLE", "YTDLP_FAILED", "yt-dlp") from exc
        try:
            metadata = json.loads("\n".join(metadata_result.stdout_lines))
        except json.JSONDecodeError as exc:
            raise PipelineError(
                "YTDLP_FAILED",
                "yt-dlp 返回了无法解析的媒体信息",
                technical_detail=str(exc),
                log_path=str(metadata_result.stdout_log),
            ) from exc
        write_json(raw_metadata_path, metadata)
        output_template = str(paths.media / "source.%(ext)s")
        downloaded_path: str | None = None

        def parse_line(line: str) -> None:
            nonlocal downloaded_path
            parsed = parse_ytdlp_progress(line)
            if parsed is not None:
                progress(parsed, line[:300])
            elif any(
                marker in line.casefold()
                for marker in ("retry", "timed out", "timeout", "error")
            ):
                progress(None, line[:300])
            if line.startswith("VIDEO2DOC_FILE:"):
                downloaded_path = line.removeprefix("VIDEO2DOC_FILE:").strip()

        format_selectors: tuple[str | None, ...] = (
            None,
            "bestvideo[height<=1080]+bestaudio[abr<=128]/best[height<=1080]/best",
        )
        final_error: PipelineError | None = None
        for attempt, format_selector in enumerate(format_selectors, start=1):
            downloaded_path = None
            command = [
                self.executable,
                "--newline",
                "--progress",
                "--progress-delta",
                "1",
                "--no-playlist",
                "--socket-timeout",
                "15",
                "--retries",
                "3",
                "--fragment-retries",
                "3",
                "--print",
                "after_move:VIDEO2DOC_FILE:%(filepath)s",
                "--output",
                output_template,
            ]
            if format_selector:
                command.extend(["--format", format_selector])
            command.append(source)
            try:
                await self.runner.run(
                    command,
                    cwd=paths.root,
                    log_dir=paths.logs,
                    log_name=(
                        "ytdlp_download"
                        if attempt == 1
                        else "ytdlp_download_fallback"
                    ),
                    timeout_seconds=get_settings().media_process_timeout_seconds,
                    cancel_check=cancel_check,
                    stdout_callback=parse_line,
                    stderr_callback=parse_line,
                )
                final_error = None
                break
            except PipelineError as exc:
                final_error = exc
                if attempt == 1 and _retryable_ytdlp_download_error(exc):
                    progress(
                        None,
                        "首选 B 站媒体节点下载失败，正在刷新地址并改用备用音频格式",
                    )
                    continue
                break
        if final_error is not None:
            _cleanup_ytdlp_downloads(paths.media)
            raise _translate_tool_error(
                final_error, "YTDLP_UNAVAILABLE", "MEDIA_DOWNLOAD_FAILED", "yt-dlp"
            ) from final_error
        if not downloaded_path:
            candidates = sorted(path for path in paths.media.glob("source.*") if path.is_file())
            downloaded_path = str(candidates[0]) if candidates else None
        local_path = Path(downloaded_path) if downloaded_path else None
        if local_path is None or not local_path.is_file() or local_path.stat().st_size == 0:
            _cleanup_ytdlp_downloads(paths.media)
            raise PipelineError("MEDIA_DOWNLOAD_FAILED", "yt-dlp 未生成有效的媒体文件")
        _cleanup_ytdlp_downloads(paths.media, keep={local_path.resolve()})
        progress(1.0, "媒体下载完成")
        return MediaAcquisitionResult(
            source_type="URL",
            original_input=source,
            local_media_path=str(local_path.resolve()),
            title=_optional_text(metadata.get("title")),
            duration=_float_or_none(metadata.get("duration")),
            container=_optional_text(metadata.get("ext")),
            has_audio=None,
            has_video=None,
            metadata_path=str(raw_metadata_path),
            provider="yt-dlp",
        )


def media_acquisition_from_ytdlp_metadata(
    source: str,
    metadata: dict[str, Any],
    metadata_path: Path,
) -> MediaAcquisitionResult:
    """Build a traceable URL acquisition without downloading media bytes."""
    return MediaAcquisitionResult(
        source_type="URL",
        original_input=source,
        local_media_path="",
        title=_optional_text(metadata.get("title")),
        duration=_float_or_none(metadata.get("duration")),
        container=_optional_text(metadata.get("ext")),
        has_audio=_ytdlp_has_stream(metadata, "audio"),
        has_video=_ytdlp_has_stream(metadata, "video"),
        metadata_path=str(metadata_path),
        provider="yt-dlp-metadata",
        media_available=False,
    )


def media_info_from_ytdlp_metadata(
    metadata: dict[str, Any],
    metadata_path: Path,
) -> MediaInfo:
    """Normalize yt-dlp metadata for subtitle-only tasks that skip media download."""
    duration = _float_or_none(metadata.get("duration"))
    requested = metadata.get("requested_formats")
    raw_streams = requested if isinstance(requested, list) and requested else [metadata]
    audio: list[MediaStream] = []
    video: list[MediaStream] = []
    for raw in raw_streams:
        if not isinstance(raw, dict):
            continue
        stream_duration = _float_or_none(raw.get("duration")) or duration
        if _codec_present(raw.get("acodec")):
            audio.append(
                MediaStream(
                    index=len(audio),
                    type="audio",
                    codec=_optional_text(raw.get("acodec")),
                    duration=stream_duration,
                    sample_rate=_int_or_none(raw.get("asr")),
                    channels=_int_or_none(raw.get("audio_channels")),
                )
            )
        if _codec_present(raw.get("vcodec")):
            video.append(
                MediaStream(
                    index=len(video),
                    type="video",
                    codec=_optional_text(raw.get("vcodec")),
                    duration=stream_duration,
                    width=_int_or_none(raw.get("width")),
                    height=_int_or_none(raw.get("height")),
                    frame_rate=_float_or_none(raw.get("fps")),
                )
            )
    return MediaInfo(
        container=_optional_text(metadata.get("ext")),
        duration=duration,
        has_audio=bool(audio),
        has_video=bool(video),
        selected_audio_stream=audio[0].index if audio else None,
        audio_streams=tuple(audio),
        video_streams=tuple(video),
        raw_probe_path=str(metadata_path),
    )


def _ytdlp_has_stream(metadata: dict[str, Any], kind: str) -> bool:
    requested = metadata.get("requested_formats")
    raw_streams = requested if isinstance(requested, list) and requested else [metadata]
    key = "acodec" if kind == "audio" else "vcodec"
    return any(isinstance(item, dict) and _codec_present(item.get(key)) for item in raw_streams)


def _codec_present(value: Any) -> bool:
    return bool(value and str(value).casefold() != "none")


def _retryable_ytdlp_download_error(exc: PipelineError) -> bool:
    detail = str(exc.technical_detail or "").casefold()
    return any(
        marker in detail
        for marker in (
            "timed out",
            "timeout",
            "connection reset",
            "connection aborted",
            "remote end closed",
            "temporary failure",
        )
    )


def _cleanup_ytdlp_downloads(media_dir: Path, *, keep: set[Path] | None = None) -> None:
    retained = keep or set()
    for path in media_dir.iterdir():
        if path.is_file() and path.resolve() not in retained:
            path.unlink(missing_ok=True)


class MediaSourceResolver:
    def __init__(self, ytdlp_executable: str, runner: ExternalProcessRunner | None = None) -> None:
        self.local = LocalFileSource()
        self.remote = YtDlpSource(ytdlp_executable, runner)

    async def acquire(
        self,
        source: str,
        source_type: str,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
        progress: ProgressCallback,
    ) -> MediaAcquisitionResult:
        kind, normalized = classify_input(source, source_type)
        provider = self.remote if kind == "URL" else self.local
        return await provider.acquire(
            normalized, paths, cancel_check=cancel_check, progress=progress
        )


class FfprobeProvider:
    def __init__(self, executable: str, runner: ExternalProcessRunner | None = None) -> None:
        self.executable = executable
        self.runner = runner or ExternalProcessRunner()

    async def probe(
        self,
        media_path: Path,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
        artifact_name: str = "ffprobe",
    ) -> MediaInfo:
        raw_path = paths.metadata / f"{artifact_name}.json"
        try:
            result = await self.runner.run(
                [
                    self.executable,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(media_path),
                ],
                cwd=paths.root,
                log_dir=paths.logs,
                log_name=artifact_name,
                timeout_seconds=get_settings().media_process_timeout_seconds,
                cancel_check=cancel_check,
            )
        except PipelineError as exc:
            raise _translate_tool_error(
                exc, "FFPROBE_UNAVAILABLE", "MEDIA_PROBE_FAILED", "ffprobe"
            ) from exc
        raw = "\n".join(result.stdout_lines)
        raw_path.write_text(raw, encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PipelineError(
                "MEDIA_PROBE_FAILED",
                "ffprobe 返回了无法解析的媒体信息",
                technical_detail=str(exc),
                log_path=str(result.stderr_log),
            ) from exc
        return normalize_ffprobe(payload, raw_path)


class FfmpegProvider:
    def __init__(
        self,
        executable: str,
        ffprobe: FfprobeProvider,
        runner: ExternalProcessRunner | None = None,
    ) -> None:
        self.executable = executable
        self.ffprobe = ffprobe
        self.runner = runner or ExternalProcessRunner()

    async def normalize_audio(
        self,
        media_path: Path,
        media_info: MediaInfo,
        paths: WorkspacePaths,
        *,
        cancel_check: CancelCheck,
        progress: ProgressCallback,
    ) -> tuple[Path, MediaInfo]:
        if not media_info.has_audio or media_info.selected_audio_stream is None:
            raise PipelineError("NO_AUDIO_STREAM", "媒体中没有可用的音频流")
        destination = paths.audio / "normalized.wav"
        partial = paths.audio / "normalized.part.wav"
        partial.unlink(missing_ok=True)

        def parse_line(line: str) -> None:
            parsed = parse_ffmpeg_progress(line, media_info.duration)
            if parsed is not None:
                progress(parsed, f"已处理媒体时间 {parsed:.1%}")

        try:
            await self.runner.run(
                [
                    self.executable,
                    "-y",
                    "-i",
                    str(media_path),
                    "-map",
                    f"0:{media_info.selected_audio_stream}",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    str(partial),
                ],
                cwd=paths.root,
                log_dir=paths.logs,
                log_name="ffmpeg_audio",
                timeout_seconds=get_settings().media_process_timeout_seconds,
                cancel_check=cancel_check,
                stdout_callback=parse_line,
            )
        except PipelineError as exc:
            partial.unlink(missing_ok=True)
            raise _translate_tool_error(
                exc, "FFMPEG_UNAVAILABLE", "AUDIO_EXTRACTION_FAILED", "FFmpeg"
            ) from exc
        if not partial.is_file() or partial.stat().st_size <= 44:
            partial.unlink(missing_ok=True)
            raise PipelineError("AUDIO_EXTRACTION_FAILED", "FFmpeg 未生成完整的标准化音频")
        partial.replace(destination)
        normalized_info = await self.ffprobe.probe(
            destination,
            paths,
            cancel_check=cancel_check,
            artifact_name="ffprobe_normalized_audio",
        )
        if not normalized_info.has_audio:
            destination.unlink(missing_ok=True)
            raise PipelineError("AUDIO_EXTRACTION_FAILED", "标准化音频无法通过完整性检查")
        progress(1.0, "音频标准化完成：PCM 16kHz 单声道 WAV")
        return destination, normalized_info


def normalize_ffprobe(payload: dict[str, Any], raw_path: Path) -> MediaInfo:
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise PipelineError("MEDIA_PROBE_FAILED", "ffprobe 结果缺少媒体流信息")
    audio: list[MediaStream] = []
    video: list[MediaStream] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        kind = stream.get("codec_type")
        if kind not in {"audio", "video"}:
            continue
        item = MediaStream(
            index=int(stream.get("index", 0)),
            type=kind,
            codec=_optional_text(stream.get("codec_name")),
            duration=_float_or_none(stream.get("duration")),
            sample_rate=_int_or_none(stream.get("sample_rate")) if kind == "audio" else None,
            channels=_int_or_none(stream.get("channels")) if kind == "audio" else None,
            width=_int_or_none(stream.get("width")) if kind == "video" else None,
            height=_int_or_none(stream.get("height")) if kind == "video" else None,
            frame_rate=parse_fraction(stream.get("avg_frame_rate")) if kind == "video" else None,
        )
        (audio if kind == "audio" else video).append(item)
    format_data = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    duration = _float_or_none(format_data.get("duration"))
    if duration is None:
        durations = [item.duration for item in (*audio, *video) if item.duration is not None]
        duration = max(durations) if durations else None
    if not audio and not video:
        raise PipelineError("UNSUPPORTED_MEDIA", "文件中没有可识别的音频或视频流")
    return MediaInfo(
        container=_optional_text(format_data.get("format_name")),
        duration=duration,
        has_audio=bool(audio),
        has_video=bool(video),
        selected_audio_stream=audio[0].index if audio else None,
        audio_streams=tuple(audio),
        video_streams=tuple(video),
        raw_probe_path=str(raw_path),
    )


YTDLP_PROGRESS = re.compile(r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%")


def parse_ytdlp_progress(line: str) -> float | None:
    match = YTDLP_PROGRESS.search(line)
    return min(1.0, max(0.0, float(match.group("percent")) / 100)) if match else None


def parse_ffmpeg_progress(line: str, duration: float | None) -> float | None:
    if not duration or duration <= 0 or "=" not in line:
        return 1.0 if line.strip() == "progress=end" else None
    key, value = line.strip().split("=", 1)
    try:
        if key == "out_time_us":
            seconds = float(value) / 1_000_000
        elif key == "out_time_ms":
            # FFmpeg documents this historical field in microseconds.
            seconds = float(value) / 1_000_000
        elif key == "out_time":
            hours, minutes, seconds_text = value.split(":")
            seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds_text)
        else:
            return None
    except (ValueError, TypeError):
        return None
    return min(1.0, max(0.0, seconds / duration))


def parse_fraction(value: Any) -> float | None:
    try:
        numerator, denominator = str(value).split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else None
    except (ValueError, TypeError):
        return None


async def _cancelled(cancel_check: CancelCheck) -> bool:
    import inspect

    result = cancel_check()
    return bool(await result) if inspect.isawaitable(result) else bool(result)


def _translate_tool_error(
    exc: PipelineError, unavailable_code: str, failed_code: str, tool_name: str
) -> PipelineError:
    if exc.code in {"PROCESS_CANCELLED", "PROCESS_TIMEOUT", "backend_shutdown"}:
        return PipelineError(
            exc.code,
            exc.user_message,
            technical_detail=exc.technical_detail,
            retryable=exc.retryable,
            log_path=exc.log_path,
        )
    unavailable = exc.code == "PROCESS_UNAVAILABLE"
    return PipelineError(
        unavailable_code if unavailable else failed_code,
        f"找不到 {tool_name}，请在设置或 PATH 中配置" if unavailable else exc.user_message,
        technical_detail=exc.technical_detail,
        retryable=exc.retryable,
        log_path=exc.log_path,
    )


def _optional_text(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value not in {None, "", "N/A"} else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in {None, "", "N/A"} else None
    except (TypeError, ValueError):
        return None
