from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.core.errors import PipelineError
from app.providers.media import (
    FfmpegProvider,
    MediaInfo,
    MediaStream,
    YtDlpSource,
    classify_input,
    media_acquisition_from_ytdlp_metadata,
    media_info_from_ytdlp_metadata,
    normalize_ffprobe,
    parse_ffmpeg_progress,
    parse_ytdlp_progress,
)
from app.providers.process import (
    ExternalProcessRunner,
    ProcessResult,
    _windows_creation_flags,
    process_registry,
    redact_command,
)
from app.storage.workspace import WorkspacePaths


class FakeProcessRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def run(self, args, **kwargs) -> ProcessResult:  # type: ignore[no-untyped-def]
        normalized = tuple(str(item) for item in args)
        self.calls.append(normalized)
        log_dir = Path(kwargs["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / f"{kwargs['log_name']}.stdout.log"
        stderr_log = log_dir / f"{kwargs['log_name']}.stderr.log"
        stderr_log.write_text("", encoding="utf-8")
        lines: tuple[str, ...]
        if "--dump-single-json" in normalized:
            lines = (json.dumps({"title": "Remote lesson", "duration": 3.0, "ext": "mp4"}),)
        elif normalized[0] == "fake-ytdlp":
            destination = WorkspacePaths.from_root(Path(kwargs["cwd"])).media / "source.mp4"
            destination.write_bytes(b"media")
            lines = ("[download] 100.0% of 5B", f"VIDEO2DOC_FILE:{destination}")
        elif normalized[0] == "fake-ffmpeg":
            destination = Path(normalized[-1])
            destination.write_bytes(b"RIFF" + b"0" * 100)
            lines = ("out_time_us=3000000", "progress=end")
        else:
            lines = ()
        stdout_log.write_text("\n".join(lines), encoding="utf-8")
        callback = kwargs.get("stdout_callback")
        if callback:
            for line in lines:
                callback(line)
        return ProcessResult(normalized, 0, lines, (), 0.01, stdout_log, stderr_log)


class RetryingYtDlpRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.download_attempts = 0

    async def run(self, args, **kwargs) -> ProcessResult:  # type: ignore[no-untyped-def]
        normalized = tuple(str(item) for item in args)
        self.calls.append(normalized)
        log_dir = Path(kwargs["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / f"{kwargs['log_name']}.stdout.log"
        stderr_log = log_dir / f"{kwargs['log_name']}.stderr.log"
        stdout_log.write_text("", encoding="utf-8")
        stderr_log.write_text("", encoding="utf-8")
        if "--dump-single-json" in normalized:
            payload = {
                "title": "Retry lesson",
                "duration": 12.5,
                "ext": "mp4",
                "requested_formats": [
                    {
                        "vcodec": "avc1.640028",
                        "acodec": "none",
                        "width": 1920,
                        "height": 1080,
                        "fps": 30,
                    },
                    {
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                        "asr": 48000,
                        "audio_channels": 2,
                    },
                ],
            }
            lines = (json.dumps(payload),)
        else:
            self.download_attempts += 1
            media_dir = WorkspacePaths.from_root(Path(kwargs["cwd"])).media
            if self.download_attempts == 1:
                (media_dir / "source.f100026.mp4").write_bytes(b"orphan-video")
                raise PipelineError(
                    "PROCESS_FAILED",
                    "外部程序执行失败",
                    technical_detail="Read timed out after 3 retries",
                )
            destination = media_dir / "source.mp4"
            destination.write_bytes(b"fallback-media")
            lines = (
                "[download] 100.0% of 14B",
                f"VIDEO2DOC_FILE:{destination}",
            )
        stdout_log.write_text("\n".join(lines), encoding="utf-8")
        callback = kwargs.get("stdout_callback")
        if callback:
            for line in lines:
                callback(line)
        return ProcessResult(normalized, 0, lines, (), 0.01, stdout_log, stderr_log)


class StubProbe:
    async def probe(self, media_path, paths, *, cancel_check, **_kwargs):  # type: ignore[no-untyped-def]
        return MediaInfo(
            container="wav",
            duration=3.0,
            has_audio=True,
            has_video=False,
            selected_audio_stream=0,
            audio_streams=(MediaStream(0, "audio", "pcm_s16le", 3.0, 16000, 1),),
            video_streams=(),
            raw_probe_path=str(paths.metadata / "ffprobe.json"),
        )


@pytest.fixture(autouse=True)
def reset_process_registry() -> None:
    process_registry.begin_lifecycle()


def test_classify_input_distinguishes_url_and_absolute_local_file(tmp_path: Path) -> None:
    media = tmp_path / "课程 音频.wav"
    media.write_bytes(b"RIFFtest")
    kind, normalized = classify_input(str(media), "LOCAL_FILE")
    assert kind == "LOCAL_FILE"
    assert Path(normalized) == media.resolve()
    assert classify_input("https://example.com/video?id=1", "URL")[0] == "URL"
    with pytest.raises(PipelineError, match="http"):
        classify_input("ftp://example.com/video", "URL")
    with pytest.raises(PipelineError) as missing:
        classify_input(str(tmp_path / "missing.mp4"), "LOCAL_FILE")
    assert missing.value.code == "INPUT_NOT_FOUND"


def test_ffprobe_json_is_normalized_without_inventing_missing_values(tmp_path: Path) -> None:
    payload = {
        "format": {"format_name": "matroska,webm", "duration": "12.5"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
            },
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            },
            {
                "index": 3,
                "codec_type": "audio",
                "codec_name": "opus",
                "sample_rate": "48000",
                "channels": 6,
            },
        ],
    }
    result = normalize_ffprobe(payload, tmp_path / "ffprobe.json")
    assert result.duration == 12.5
    assert result.has_audio and result.has_video
    assert result.selected_audio_stream == 2
    assert len(result.audio_streams) == 2
    assert result.audio_streams[0].duration is None
    assert result.video_streams[0].frame_rate == pytest.approx(29.970, rel=1e-3)


def test_real_progress_parsers() -> None:
    assert parse_ytdlp_progress("[download]  42.5% of 10.00MiB") == 0.425
    assert parse_ytdlp_progress("[download] Destination: x") is None
    assert parse_ffmpeg_progress("out_time_us=2500000", 10.0) == 0.25
    assert parse_ffmpeg_progress("out_time=00:00:07.500000", 10.0) == 0.75
    assert parse_ffmpeg_progress("progress=end", None) == 1.0


@pytest.mark.asyncio
async def test_ytdlp_and_ffmpeg_providers_create_traceable_artifacts(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    fake = FakeProcessRunner()
    progress: list[float | None] = []
    acquired = await YtDlpSource("fake-ytdlp", fake).acquire(
        "https://example.com/watch?v=1",
        paths,
        cancel_check=lambda: False,
        progress=lambda value, _message: progress.append(value),
    )
    assert Path(acquired.local_media_path).read_bytes() == b"media"
    assert Path(acquired.metadata_path).is_file()
    assert progress[-1] == 1.0

    media_info = MediaInfo(
        container="mp4",
        duration=3.0,
        has_audio=True,
        has_video=True,
        selected_audio_stream=1,
        audio_streams=(MediaStream(1, "audio", "aac", 3.0, 48000, 2),),
        video_streams=(MediaStream(0, "video", "h264", 3.0),),
        raw_probe_path=str(paths.metadata / "ffprobe.json"),
    )
    audio_path, normalized = await FfmpegProvider(
        "fake-ffmpeg",
        StubProbe(),
        fake,  # type: ignore[arg-type]
    ).normalize_audio(
        Path(acquired.local_media_path),
        media_info,
        paths,
        cancel_check=lambda: False,
        progress=lambda value, _message: progress.append(value),
    )
    assert audio_path.name == "normalized.wav"
    assert audio_path.stat().st_size > 44
    assert normalized.audio_streams[0].sample_rate == 16000
    assert not (paths.audio / "normalized.part.wav").exists()


@pytest.mark.asyncio
async def test_ytdlp_recovers_final_file_when_printed_chinese_path_is_garbled(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "中文任务")
    paths.create()

    class GarbledPathRunner(FakeProcessRunner):
        async def run(self, args, **kwargs):  # type: ignore[no-untyped-def]
            result = await super().run(args, **kwargs)
            if "--dump-single-json" not in args:
                (paths.media / "source.mp4.part").write_bytes(b"partial")
                (paths.media / "source.f100026.mp4").write_bytes(b"unmerged")
                kwargs["stdout_callback"]("VIDEO2DOC_FILE:" + str(paths.media / "missing.mp4"))
            return result

    runner = GarbledPathRunner()
    acquired = await YtDlpSource("fake-ytdlp", runner).acquire(
        "https://example.com/watch?v=chinese",
        paths,
        cancel_check=lambda: False,
        progress=lambda *_args: None,
    )
    assert Path(acquired.local_media_path) == (paths.media / "source.mp4").resolve()
    assert Path(acquired.local_media_path).read_bytes() == b"media"
    assert all(call[call.index("--encoding") + 1] == "utf-8" for call in runner.calls)


@pytest.mark.asyncio
async def test_ytdlp_retries_with_bounded_fallback_and_removes_orphaned_stream(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "retry-task")
    paths.create()
    runner = RetryingYtDlpRunner()
    updates: list[tuple[float | None, str]] = []

    acquired = await YtDlpSource("fake-ytdlp", runner).acquire(
        "https://example.com/watch?v=retry",
        paths,
        cancel_check=lambda: False,
        progress=lambda fraction, message: updates.append((fraction, message)),
    )

    download_calls = [call for call in runner.calls if "--dump-single-json" not in call]
    assert runner.download_attempts == 2
    assert download_calls[0][download_calls[0].index("--socket-timeout") + 1] == "15"
    assert "--format" not in download_calls[0]
    assert "--format" in download_calls[1]
    assert "bestaudio[abr<=128]" in download_calls[1][download_calls[1].index("--format") + 1]
    assert Path(acquired.local_media_path).read_bytes() == b"fallback-media"
    assert not (paths.media / "source.f100026.mp4").exists()
    assert any("备用音频格式" in message for _fraction, message in updates)
    assert updates[-1] == (1.0, "媒体下载完成")


def test_ytdlp_metadata_supports_subtitle_only_media_probe(tmp_path: Path) -> None:
    metadata_path = tmp_path / "ytdlp.json"
    metadata = {
        "title": "Subtitle only lesson",
        "duration": 18.25,
        "ext": "mp4",
        "requested_formats": [
            {
                "vcodec": "avc1.640028",
                "acodec": "none",
                "width": 1920,
                "height": 1080,
                "fps": 30,
            },
            {
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "asr": 48000,
                "audio_channels": 2,
            },
        ],
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    acquisition = media_acquisition_from_ytdlp_metadata(
        "https://example.com/watch?v=subtitle",
        metadata,
        metadata_path,
    )
    media_info = media_info_from_ytdlp_metadata(metadata, metadata_path)

    assert acquisition.media_available is False
    assert acquisition.local_media_path == ""
    assert acquisition.has_audio is True
    assert acquisition.has_video is True
    assert media_info.duration == 18.25
    assert media_info.selected_audio_stream == 0
    assert media_info.audio_streams[0].sample_rate == 48000
    assert media_info.video_streams[0].height == 1080


@pytest.mark.asyncio
async def test_external_process_streams_output_and_checks_return_code(tmp_path: Path) -> None:
    runner = ExternalProcessRunner()
    result = await runner.run(
        [sys.executable, "-c", "print('out'); import sys; print('err', file=sys.stderr)"],
        cwd=tmp_path,
        log_dir=tmp_path / "logs",
        log_name="success",
        timeout_seconds=5,
        cancel_check=lambda: False,
    )
    assert result.returncode == 0
    assert result.stdout_lines == ("out",)
    assert result.stderr_lines == ("err",)
    assert result.stdout_log.read_text(encoding="utf-8").strip() == "out"

    with pytest.raises(PipelineError) as failed:
        await runner.run(
            [
                sys.executable,
                "-c",
                "import sys; print('safe detail', file=sys.stderr); sys.exit(7)",
            ],
            cwd=tmp_path,
            log_dir=tmp_path / "logs",
            log_name="failure",
            timeout_seconds=5,
            cancel_check=lambda: False,
        )
    assert failed.value.code == "PROCESS_FAILED"
    assert "safe detail" in (failed.value.technical_detail or "")


@pytest.mark.asyncio
async def test_external_process_timeout_and_cancel_terminate_process_tree(tmp_path: Path) -> None:
    runner = ExternalProcessRunner()
    with pytest.raises(PipelineError) as timed_out:
        await runner.run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            log_dir=tmp_path / "logs",
            log_name="timeout",
            timeout_seconds=0.05,
            cancel_check=lambda: False,
        )
    assert timed_out.value.code == "PROCESS_TIMEOUT"

    with pytest.raises(PipelineError) as cancelled:
        await runner.run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            log_dir=tmp_path / "logs",
            log_name="cancel",
            timeout_seconds=5,
            cancel_check=lambda: True,
        )
    assert cancelled.value.code == "PROCESS_CANCELLED"


def test_sensitive_command_values_are_redacted() -> None:
    rendered = redact_command(
        ["yt-dlp", "--password", "secret", "https://user:pass@example.com/watch?v=token"]
    )
    assert "secret" not in rendered
    assert "user:pass" not in rendered
    assert "token" not in rendered
    assert "***" in rendered


def test_windows_external_processes_are_created_without_console_windows() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows creation flags are only available on Windows")
    import subprocess

    regular = _windows_creation_flags()
    grouped = _windows_creation_flags(new_process_group=True)
    assert regular & subprocess.CREATE_NO_WINDOW
    assert grouped & subprocess.CREATE_NO_WINDOW
    assert grouped & subprocess.CREATE_NEW_PROCESS_GROUP
