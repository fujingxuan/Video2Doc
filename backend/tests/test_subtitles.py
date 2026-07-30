from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import PipelineError
from app.providers.subtitles import YtDlpSubtitleSource, import_subtitle
from app.storage.workspace import WorkspacePaths, write_json


class SubtitleRunner:
    def __init__(self, *, create_subtitle: bool = True) -> None:
        self.create_subtitle = create_subtitle
        self.calls: list[tuple[str, ...]] = []

    async def run(self, args, **kwargs):
        normalized = tuple(str(item) for item in args)
        self.calls.append(normalized)
        if self.create_subtitle:
            output = Path(normalized[normalized.index("--output") + 1])
            subtitle = Path(str(output).replace("%(ext)s", "zh-CN.vtt"))
            subtitle.parent.mkdir(parents=True, exist_ok=True)
            subtitle.write_text(
                "WEBVTT\n\n00:00:00.500 --> 00:00:01.800\n从链接抓取的中文字幕。\n",
                encoding="utf-8",
            )
        callback = kwargs.get("stdout_callback")
        if callback:
            callback("[download] Writing video subtitles")
        return SimpleNamespace(stderr_log=kwargs["log_dir"] / "ytdlp_subtitles.stderr.log")


@pytest.mark.asyncio
async def test_ytdlp_subtitle_source_fetches_chinese_caption_and_reuses_it(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    write_json(
        paths.metadata / "ytdlp.json",
        {
            "subtitles": {"zh-CN": [{"ext": "vtt"}]},
            "automatic_captions": {"en": [{"ext": "vtt"}]},
        },
    )
    runner = SubtitleRunner()
    progress: list[tuple[float | None, str]] = []
    provider = YtDlpSubtitleSource("yt-dlp", runner)

    acquired = await provider.acquire(
        "https://example.com/watch?v=123",
        paths,
        cancel_check=lambda: False,
        progress=lambda value, message: progress.append((value, message)),
    )
    reused = await provider.acquire(
        "https://example.com/watch?v=123",
        paths,
        cancel_check=lambda: False,
        progress=lambda value, message: progress.append((value, message)),
    )

    assert len(runner.calls) == 1
    command = runner.calls[0]
    assert "--skip-download" in command
    assert "--write-subs" in command
    assert "--write-auto-subs" in command
    assert command[command.index("--sub-langs") + 1] == "zh-CN"
    assert acquired.path == reused.path
    assert acquired.language == "zh-CN"
    assert acquired.source_kind == "manual"
    assert acquired.sha256
    imported = import_subtitle(
        Path(acquired.path),
        language=acquired.language,
        provider=acquired.provider,
    )
    assert imported.segments[0].text == "从链接抓取的中文字幕。"
    assert imported.segments[0].language == "zh-CN"
    assert imported.segments[0].provider == "yt-dlp-subtitle"

    Path(acquired.path).write_text("damaged", encoding="utf-8")
    repaired = await provider.acquire(
        "https://example.com/watch?v=123",
        paths,
        cancel_check=lambda: False,
        progress=lambda value, message: progress.append((value, message)),
    )
    assert len(runner.calls) == 2
    assert repaired.sha256 == acquired.sha256


@pytest.mark.asyncio
async def test_ytdlp_subtitle_source_reports_missing_captions(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    write_json(paths.metadata / "ytdlp.json", {"subtitles": {}, "automatic_captions": {}})
    runner = SubtitleRunner(create_subtitle=False)

    with pytest.raises(PipelineError) as captured:
        await YtDlpSubtitleSource("yt-dlp", runner).acquire(
            "https://example.com/watch?v=missing",
            paths,
            cancel_check=lambda: False,
            progress=lambda _value, _message: None,
        )

    assert captured.value.code == "SUBTITLE_NOT_FOUND"
    assert "人工字幕和自动字幕" in captured.value.user_message
