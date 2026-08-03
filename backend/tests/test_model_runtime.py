from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

from fastapi.testclient import TestClient


def load_runtime(monkeypatch) -> ModuleType:
    monkeypatch.setenv("VIDEO2DOC_ASR_PROVIDER", "mock")
    path = Path(__file__).resolve().parents[2] / "scripts" / "model_runtime" / "asr_gateway_service.py"
    spec = importlib.util.spec_from_file_location("video2doc_test_model_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_ocr_runtime() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "model_runtime"
        / "ocr_gateway_service.py"
    )
    spec = importlib.util.spec_from_file_location("video2doc_test_ocr_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ocr_runtime_startup_removes_only_its_stale_uploads(tmp_path: Path) -> None:
    module = load_ocr_runtime()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    stale = temp_dir / "video2doc-ocr-stale.jpg"
    unrelated = temp_dir / "unrelated.jpg"
    stale.write_bytes(b"partial")
    unrelated.write_bytes(b"keep")

    assert module.cleanup_stale_uploads(tmp_path) == 1
    assert not stale.exists()
    assert unrelated.read_bytes() == b"keep"


def test_local_gateway_health_transcription_and_cancel(monkeypatch) -> None:
    module = load_runtime(monkeypatch)
    with TestClient(module.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["provider"] == "mock"
        result = client.post(
            "/v1/asr/transcriptions",
            data={"model": "mock-asr", "request_id": "request-ok"},
            files={"file": ("audio.wav", b"RIFF" + b"0" * 100, "audio/wav")},
        )
        assert result.status_code == 200
        assert result.json()["text"] == "mock transcription"

        cancelled = client.post("/v1/asr/cancellations/request-cancelled")
        assert cancelled.status_code == 200
        rejected = client.post(
            "/v1/asr/transcriptions",
            data={"model": "mock-asr", "request_id": "request-cancelled"},
            files={"file": ("audio.wav", b"RIFF" + b"0" * 100, "audio/wav")},
        )
        assert rejected.status_code == 409


def test_model_switch_unloads_previous_model(monkeypatch) -> None:
    module = load_runtime(monkeypatch)
    first = module.runtime.load("first-model")
    second = module.runtime.load("second-model")
    assert first is not second
    assert module.runtime.loaded_name == "second-model"
    module.runtime.unload()
    assert module.runtime.loaded_name is None
    assert module.runtime.loaded_model is None


def test_whisper_response_normalization(monkeypatch, tmp_path: Path) -> None:
    module = load_runtime(monkeypatch)
    word = SimpleNamespace(start=0.1, end=0.4, word=" hello", probability=0.91)
    segment = SimpleNamespace(
        start=0.1,
        end=0.4,
        text=" hello",
        words=[word],
        avg_logprob=-0.2,
        no_speech_prob=0.01,
        temperature=0.0,
    )
    fake_model = SimpleNamespace(
        transcribe=lambda *_args, **_kwargs: (
            iter([segment]),
            SimpleNamespace(language="en", duration=0.5),
        )
    )
    monkeypatch.setattr(module.runtime, "load", lambda _name: fake_model)
    response = module._transcribe_whisper(tmp_path / "audio.wav", "large-v3", None, None)
    assert response["provider"] == "faster-whisper"
    assert response["language"] == "en"
    assert response["text"] == "hello"
    assert response["segments"][0]["start"] == 0.1
    assert response["words"] == [
        {"start": 0.1, "end": 0.4, "text": " hello", "confidence": 0.91}
    ]


def test_qwen_response_normalization_uses_all_forced_alignment_items(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_runtime(monkeypatch)
    timestamps = [
        SimpleNamespace(start_time=0.1, end_time=0.3, text="你"),
        SimpleNamespace(start_time=0.3, end_time=0.6, text="好"),
    ]
    fake_result = SimpleNamespace(language="Chinese", text="你好", time_stamps=timestamps)
    fake_model = SimpleNamespace(transcribe=lambda **_kwargs: [fake_result])
    monkeypatch.setattr(module.runtime, "load", lambda _name: fake_model)
    response = module._transcribe_qwen(
        tmp_path / "audio.wav", "Qwen/Qwen3-ASR-1.7B", "zh"
    )
    assert response["provider"] == "qwen-asr-transformers"
    assert response["language"] == "Chinese"
    assert response["duration"] == 0.6
    assert [item["text"] for item in response["words"]] == ["你", "好"]


def test_firered_response_normalization_keeps_words_and_confidence(
    monkeypatch, tmp_path: Path
) -> None:
    module = load_runtime(monkeypatch)
    records = [
        {
            "text": "hello speech",
            "confidence": 0.943,
            "dur_s": 2.24,
            "timestamp": [("hello", 0.34, 0.98), ("speech", 0.98, 1.766)],
        }
    ]
    fake_model = SimpleNamespace(transcribe=lambda *_args: records)
    monkeypatch.setattr(module.runtime, "load", lambda _name: fake_model)
    response = module._transcribe_firered(
        tmp_path / "audio.wav", "FireRedTeam/FireRedASR2-AED"
    )
    assert response["provider"] == "fireredasr2s-official-python"
    assert response["duration"] == 2.24
    assert response["segments"][0]["confidence"] == 0.943
    assert response["segments"][0]["start"] == 0.34
    assert [item["text"] for item in response["words"]] == ["hello", "speech"]


def test_firered_alignment_casts_bfloat16_logits_to_float32(monkeypatch) -> None:
    module = load_runtime(monkeypatch)
    bfloat16 = object()
    float32_logits = object()

    class FakeLogits:
        dtype = bfloat16

        def float(self):  # type: ignore[no-untyped-def]
            return float32_logits

    fake_torch = SimpleNamespace(bfloat16=bfloat16)

    assert module._firered_alignment_log_probs(FakeLogits(), fake_torch) is float32_logits


def test_firered_alignment_leaves_supported_logits_unchanged(monkeypatch) -> None:
    module = load_runtime(monkeypatch)
    logits = SimpleNamespace(dtype="float32")
    fake_torch = SimpleNamespace(bfloat16="bfloat16")

    assert module._firered_alignment_log_probs(logits, fake_torch) is logits
