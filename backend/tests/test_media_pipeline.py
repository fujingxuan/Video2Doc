from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.core.errors import PipelineError
from app.providers.media import (
    FfmpegProvider,
    FfprobeProvider,
    MediaInfo,
    MediaStream,
    YtDlpSource,
    classify_input,
    normalize_ffprobe,
    parse_ffmpeg_progress,
    parse_ytdlp_progress,
)
from app.providers.process import (
    ExternalProcessRunner,
    ProcessResult,
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
        "fake-ffmpeg", StubProbe(), fake  # type: ignore[arg-type]
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
            [sys.executable, "-c", "import sys; print('safe detail', file=sys.stderr); sys.exit(7)"],
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
