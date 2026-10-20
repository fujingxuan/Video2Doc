from pathlib import Path

import pytest

from app.core.errors import PipelineError
from app.providers.media import YtDlpSource
from app.providers.process import ProcessResult
from app.providers.ytdlp_options import authentication_options, translate_ytdlp_error
from app.storage.workspace import WorkspacePaths
from tests.test_model_runtime import load_runtime


def test_login_redirect_is_actionable_and_cancellation_is_preserved():
    error = PipelineError("PROCESS_FAILED", "exit 1", technical_detail=
                          "ERROR: Unsupported URL: https://www.xiaohongshu.com/login?***")
    translated = translate_ytdlp_error(error)
    assert translated.code == "MEDIA_LOGIN_REQUIRED"
    assert not translated.retryable
    assert "音视频" in translated.user_message
    assert translate_ytdlp_error(PipelineError("PROCESS_CANCELLED", "cancelled")) is None


def test_cookie_file_requires_explicit_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("VIDEO2DOC_YTDLP_COOKIES_FILE", raising=False)
    assert authentication_options() == []
    assert authentication_options("http://127.0.0.1/a.wav") == ["--proxy", ""]
    assert authentication_options("https://example.com/video") == []
    cookie = tmp_path / "cookies.txt"
    monkeypatch.setenv("VIDEO2DOC_YTDLP_COOKIES_FILE", str(cookie))
    with pytest.raises(PipelineError, match="文件不存在"):
        authentication_options()
    cookie.write_text("# Netscape HTTP Cookie File\n")
    assert authentication_options() == ["--cookies", str(cookie.resolve())]


@pytest.mark.asyncio
async def test_audio_only_download_keeps_best_audio(tmp_path):
    calls = []
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()

    class Runner:
        async def run(self, args, **kwargs):
            calls.append(args)
            stdout = paths.logs / "out.log"
            stderr = paths.logs / "err.log"
            if "--dump-single-json" in args:
                lines = ('{"title":"lesson","ext":"m4a"}',)
            else:
                target = paths.media / "source.m4a"
                target.write_bytes(b"audio")
                lines = (f"VIDEO2DOC_FILE:{target}",)
                kwargs["stdout_callback"](lines[0])
            return ProcessResult(tuple(args), 0, lines, (), 0.0, stdout, stderr)

    result = await YtDlpSource("yt-dlp", Runner(), audio_only=True).acquire(
        "https://example.com/video", paths, cancel_check=lambda: False,
        progress=lambda *_: None,
    )
    assert Path(result.local_media_path).suffix == ".m4a"
    assert calls[1][calls[1].index("--format") + 1] == "bestaudio/best"


def test_runtime_reports_cold_and_warm_costs_separately(monkeypatch, tmp_path):
    module = load_runtime(monkeypatch)
    for cold in (True, False):
        result = module._run_inference(module._transcribe_mock, (tmp_path / "a.wav", "mock-asr", None))
        metrics = result["runtime_metrics"]
        assert metrics["cold_start"] is cold
        assert metrics["total_seconds"] >= metrics["inference_seconds"] >= 0
        if not cold:
            assert metrics["load_seconds"] == 0
    module.runtime.unload()
    assert module.health()["actual_device"] is None
    assert module.health()["load_seconds"] is None
