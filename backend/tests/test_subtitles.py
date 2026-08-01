from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

from app.core.errors import PipelineError
from app.providers.ocr import OcrBlock, OcrResult
from app.providers.subtitles import (
    BilibiliSubtitleSource,
    YtDlpSubtitleSource,
    _caption_cues,
    import_subtitle,
)
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


@pytest.mark.asyncio
async def test_bilibili_player_api_fallback_downloads_public_caption(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"pages": [{"cid": 12345}]}},
            )
        if request.url.path == "/x/player/wbi/v2":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {
                                    "lan": "zh-CN",
                                    "subtitle_url": "//aisubtitle.example/subtitle.json",
                                    "ai_type": 1,
                                }
                            ]
                        }
                    },
                },
            )
        if request.url.host == "aisubtitle.example":
            return httpx.Response(
                200,
                json={
                    "body": [
                        {"from": 0.5, "to": 1.8, "content": "播放器字幕"},
                        {"from": 2.0, "to": 3.0, "content": "第二句"},
                    ]
                },
            )
        raise AssertionError(str(request.url))

    acquired = await BilibiliSubtitleSource(
        transport=httpx.MockTransport(handler)
    ).acquire_if_available(
        "https://www.bilibili.com/video/BV1bsgx6EEUf?p=1",
        paths,
        cancel_check=lambda: False,
        progress=lambda _value, _message: None,
    )

    assert acquired is not None
    assert acquired.provider == "bilibili-player-api"
    assert acquired.source_kind == "automatic"
    imported = import_subtitle(Path(acquired.path), provider=acquired.provider)
    assert [item.text for item in imported.segments] == ["播放器字幕", "第二句"]


def test_embedded_caption_observations_merge_stable_ocr_text() -> None:
    def result(timestamp: float, text: str) -> OcrResult:
        return OcrResult(
            provider="mock-ocr",
            model="ocr",
            text=text,
            blocks=(),
            raw_response_reference="mock.json",
            frame_id=f"frame-{timestamp}",
            media_timestamp=timestamp,
        )

    cues = _caption_cues(
        (
            result(0.0, "第一句字幕"),
            result(1.0, "第一句字幕。"),
            result(2.0, "第二句字幕"),
            result(3.0, ""),
        ),
        duration=4.0,
        interval_seconds=1.0,
    )

    assert [item[2] for item in cues] == ["第一句字幕。", "第二句字幕"]
    assert cues[0][:2] == (0.0, 1.5)
    assert cues[1][:2] == (1.5, 2.5)


def test_embedded_caption_prefers_large_lower_text_over_page_noise() -> None:
    blocks = (
        OcrBlock("Chinese", (469, 0, 1370, 238), 0.99),
        OcrBlock("71", (267, 40, 309, 69), 0.99),
        OcrBlock("把付费用户的单条字数上限", (328, 213, 1156, 288), 0.999),
        OcrBlock("54", (279, 270, 318, 299), 0.99),
    )
    result = OcrResult(
        provider="mock-ocr",
        model="ocr",
        text="\n".join(block.text for block in blocks),
        blocks=blocks,
        raw_response_reference="mock.json",
        media_timestamp=5.0,
    )

    cues = _caption_cues((result,), duration=6.0, interval_seconds=1.0)

    assert cues == [(4.5, 5.5, "把付费用户的单条字数上限")]
