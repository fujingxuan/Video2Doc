from __future__ import annotations

import argparse
import asyncio
import gc
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
import uvicorn


logging.basicConfig(level=os.getenv("VIDEO2DOC_MODEL_LOG_LEVEL", "INFO"))
logger = logging.getLogger("video2doc.model_runtime")

PROVIDER = os.getenv("VIDEO2DOC_ASR_PROVIDER", "mock").strip().lower()
MODEL_ROOT = Path(os.getenv("VIDEO2DOC_MODEL_ROOT", "/mnt/d/Models/Video2Doc")).resolve()
DEVICE = os.getenv("VIDEO2DOC_ASR_DEVICE", "cuda").strip().casefold()
CUDA_DEVICE = DEVICE == "cuda" or DEVICE.startswith("cuda:")
COMPUTE_TYPE = os.getenv("VIDEO2DOC_WHISPER_COMPUTE_TYPE", "float16")
FIRERED_USE_HALF = os.getenv("VIDEO2DOC_FIRERED_USE_HALF", "1").strip().casefold() not in {
    "0",
    "false",
    "no",
}
QWEN_ASR_MODEL = os.getenv("VIDEO2DOC_QWEN_MODEL", str(MODEL_ROOT / "qwen/Qwen3-ASR-1.7B"))
QWEN_ALIGNER_MODEL = os.getenv(
    "VIDEO2DOC_QWEN_ALIGNER_MODEL", str(MODEL_ROOT / "qwen/Qwen3-ForcedAligner-0.6B")
)
FIRERED_MODEL = Path(
    os.getenv("VIDEO2DOC_FIRERED_MODEL", str(MODEL_ROOT / "firered/FireRedASR2-AED"))
)


class ModelRuntime:
    def __init__(self) -> None:
        self.lock = Lock()
        self.inference_lock = Lock()
        self.loaded_name: str | None = None
        self.loaded_model: Any = None
        self.loaded_at: float | None = None
        self.load_seconds: float | None = None

    def unload(self) -> None:
        self.loaded_model = None
        self.loaded_name = None
        self.loaded_at = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except ImportError:
            pass

    def load(self, model_name: str) -> Any:
        with self.lock:
            if self.loaded_name == model_name and self.loaded_model is not None:
                return self.loaded_model
            self.unload()
            started = time.perf_counter()
            if PROVIDER == "whisper":
                from faster_whisper import WhisperModel

                model_path = MODEL_ROOT / "whisper" / model_name
                source = str(model_path) if model_path.exists() else model_name
                whisper_device = "cuda" if CUDA_DEVICE else DEVICE
                whisper_options: dict[str, Any] = {
                    "device": whisper_device,
                    "compute_type": COMPUTE_TYPE,
                }
                if DEVICE.startswith("cuda:"):
                    whisper_options["device_index"] = int(DEVICE.partition(":")[2])
                self.loaded_model = WhisperModel(source, **whisper_options)
            elif PROVIDER == "qwen":
                import torch
                from qwen_asr import Qwen3ASRModel

                if model_name not in {"Qwen/Qwen3-ASR-1.7B", "Qwen3-ASR-1.7B"}:
                    raise ValueError(f"Unsupported Qwen model: {model_name}")
                self.loaded_model = Qwen3ASRModel.from_pretrained(
                    QWEN_ASR_MODEL,
                    dtype=torch.bfloat16 if CUDA_DEVICE else torch.float32,
                    device_map=(DEVICE if DEVICE.startswith("cuda:") else "cuda:0")
                    if CUDA_DEVICE
                    else DEVICE,
                    max_inference_batch_size=1,
                    max_new_tokens=2048,
                    forced_aligner=QWEN_ALIGNER_MODEL,
                    forced_aligner_kwargs={
                        "dtype": torch.bfloat16 if CUDA_DEVICE else torch.float32,
                        "device_map": (
                            DEVICE if DEVICE.startswith("cuda:") else "cuda:0"
                        )
                        if CUDA_DEVICE
                        else DEVICE,
                    },
                )
            elif PROVIDER == "mock":
                self.loaded_model = object()
            elif PROVIDER == "firered":
                import torch
                from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config

                _install_firered_forced_align_compatibility()
                if DEVICE.startswith("cuda:"):
                    torch.cuda.set_device(int(DEVICE.partition(":")[2]))

                if model_name not in {
                    "FireRedTeam/FireRedASR2-AED",
                    "FireRedASR2-AED",
                }:
                    raise ValueError(f"Unsupported FireRed model: {model_name}")
                config = FireRedAsr2Config(
                    use_gpu=CUDA_DEVICE,
                    use_half=FIRERED_USE_HALF,
                    beam_size=3,
                    nbest=1,
                    decode_max_len=0,
                    softmax_smoothing=1.25,
                    aed_length_penalty=0.6,
                    eos_penalty=1.0,
                    return_timestamp=True,
                )
                self.loaded_model = FireRedAsr2.from_pretrained(
                    "aed", str(FIRERED_MODEL), config
                )
                _release_cpu_loader_memory()
            else:
                raise ValueError(f"Unknown provider: {PROVIDER}")
            self.loaded_name = model_name
            self.loaded_at = time.time()
            self.load_seconds = time.perf_counter() - started
            return self.loaded_model


runtime = ModelRuntime()
app = FastAPI(title="Video2Doc ASR Gateway", version="1.0")
cancelled_requests: set[str] = set()
cancelled_requests_lock = Lock()


def supported_models() -> list[dict[str, Any]]:
    if PROVIDER == "whisper":
        return [
            {"id": "large-v3", "display_name": "Whisper large-v3", "capabilities": _whisper_caps()},
            {"id": "large-v3-turbo", "display_name": "Whisper large-v3-turbo", "capabilities": _whisper_caps()},
        ]
    if PROVIDER == "qwen":
        return [
            {
                "id": "Qwen/Qwen3-ASR-1.7B",
                "display_name": "Qwen3-ASR-1.7B + ForcedAligner",
                "capabilities": {
                    "segment_timestamps": True,
                    "word_timestamps": True,
                    "language_detection": True,
                    "max_chunk_seconds": 180,
                    "max_alignment_seconds": 300,
                },
            }
        ]
    if PROVIDER == "firered":
        return [
            {
                "id": "FireRedTeam/FireRedASR2-AED",
                "display_name": "FireRedASR2-AED",
                "capabilities": {
                    "segment_timestamps": True,
                    "word_timestamps": True,
                    "confidence": True,
                    "max_chunk_seconds": 55,
                },
            }
        ]
    return [{"id": "mock-asr", "display_name": "Mock ASR", "capabilities": {"word_timestamps": True}}]


def _whisper_caps() -> dict[str, Any]:
    return {
        "segment_timestamps": True,
        "word_timestamps": True,
        "language_detection": True,
        "vad_filter": True,
        "max_chunk_seconds": 30,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": PROVIDER,
        "device": DEVICE,
        "cuda_enabled": CUDA_DEVICE,
        "compute_type": (
            COMPUTE_TYPE
            if PROVIDER == "whisper"
            else ("bfloat16" if CUDA_DEVICE and PROVIDER in {"firered", "qwen"} else "float32")
        ),
        "loaded_model": runtime.loaded_name,
        "loaded_at": runtime.loaded_at,
        "load_seconds": runtime.load_seconds,
        "model_root": str(MODEL_ROOT),
    }


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {"data": supported_models()}


@app.post("/v1/models/unload")
def unload() -> dict[str, str]:
    with runtime.inference_lock:
        runtime.unload()
    return {"status": "unloaded"}


@app.post("/v1/asr/cancellations/{request_id}")
def cancel_transcription(request_id: str) -> dict[str, str]:
    with cancelled_requests_lock:
        cancelled_requests.add(request_id)
    return {"status": "cancellation_requested", "request_id": request_id}


def _is_cancelled(request_id: str | None) -> bool:
    if not request_id:
        return False
    with cancelled_requests_lock:
        return request_id in cancelled_requests


def _clear_cancellation(request_id: str | None) -> None:
    if request_id:
        with cancelled_requests_lock:
            cancelled_requests.discard(request_id)


@app.post("/v1/asr/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(default=None),
    timestamp_granularity: str = Form(default="word"),
    prompt: str | None = Form(default=None),
    task: str = Form(default="transcribe"),
    request_id: str | None = Form(default=None),
) -> dict[str, Any]:
    if task != "transcribe":
        raise HTTPException(status_code=400, detail="Only transcribe is supported")
    if model not in {item["id"] for item in supported_models()}:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model}")
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    if _is_cancelled(request_id):
        _clear_cancellation(request_id)
        raise HTTPException(status_code=409, detail="ASR request was cancelled")
    try:
        with tempfile.TemporaryDirectory(prefix="video2doc-asr-") as temp_dir:
            audio_path = Path(temp_dir) / f"input{suffix}"
            with audio_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            started = time.perf_counter()
            try:
                if PROVIDER == "whisper":
                    call = _transcribe_whisper, (audio_path, model, language, prompt)
                elif PROVIDER == "qwen":
                    call = _transcribe_qwen, (audio_path, model, language)
                elif PROVIDER == "firered":
                    call = _transcribe_firered, (audio_path, model)
                else:
                    call = _transcribe_mock, (audio_path, model, language)
                response = await asyncio.to_thread(_run_inference, call[0], call[1])
            except RuntimeError as exc:
                logger.exception("ASR inference failed")
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        if _is_cancelled(request_id):
            raise HTTPException(status_code=409, detail="ASR request was cancelled; result discarded")
    finally:
        _clear_cancellation(request_id)
    response["duration"] = response.get("duration")
    response["raw_response_reference"] = None
    response["inference_seconds"] = time.perf_counter() - started
    response["timestamp_granularity"] = timestamp_granularity
    return response


def _run_inference(call: Any, arguments: tuple[Any, ...]) -> dict[str, Any]:
    # Model switching and unloading must not overlap an active GPU inference.
    with runtime.inference_lock:
        return call(*arguments)


def _firered_alignment_log_probs(log_probs: Any, torch_module: Any) -> Any:
    # FireRed selects BF16 automatically on recent NVIDIA GPUs, while
    # torchaudio.forced_align accepts only FP16/FP32/FP64 logits.
    return log_probs.float() if log_probs.dtype == torch_module.bfloat16 else log_probs


def _install_firered_forced_align_compatibility() -> None:
    import torch
    import torchaudio

    current = torchaudio.functional.forced_align
    if getattr(current, "_video2doc_bfloat16_compatible", False):
        return

    def compatible_forced_align(log_probs: Any, *args: Any, **kwargs: Any) -> Any:
        return current(
            _firered_alignment_log_probs(log_probs, torch),
            *args,
            **kwargs,
        )

    compatible_forced_align._video2doc_bfloat16_compatible = True  # type: ignore[attr-defined]
    torchaudio.functional.forced_align = compatible_forced_align


def _release_cpu_loader_memory() -> None:
    """Return transient checkpoint-loading allocations to the Linux host."""
    import gc

    gc.collect()
    if os.name != "posix":
        return
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (AttributeError, OSError):
        logger.debug("glibc malloc_trim is unavailable", exc_info=True)


def _transcribe_whisper(audio_path: Path, model_name: str, language: str | None, prompt: str | None) -> dict[str, Any]:
    model = runtime.load(model_name)
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        task="transcribe",
        beam_size=5,
        temperature=0.0,
        condition_on_previous_text=True,
        vad_filter=True,
        word_timestamps=True,
        initial_prompt=prompt,
    )
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for item in segments_iter:
        item_words = [
            {
                "start": word.start,
                "end": word.end,
                "text": word.word,
                "confidence": word.probability,
            }
            for word in (item.words or [])
        ]
        words.extend(item_words)
        segments.append(
            {
                "start": item.start,
                "end": item.end,
                "text": item.text,
                "confidence": None,
                "words": item_words,
                "raw": {
                    "avg_logprob": item.avg_logprob,
                    "no_speech_prob": item.no_speech_prob,
                    "temperature": item.temperature,
                },
            }
        )
    text = "".join(item["text"] for item in segments).strip()
    return {
        "model": model_name,
        "provider": "faster-whisper",
        "language": info.language,
        "text": text,
        "segments": segments,
        "words": words,
        "duration": info.duration,
        "no_speech": not bool(text),
    }


def _transcribe_qwen(audio_path: Path, model_name: str, language: str | None) -> dict[str, Any]:
    model = runtime.load(model_name)
    results = model.transcribe(
        audio=str(audio_path),
        language=_qwen_language(language),
        return_time_stamps=True,
    )
    if not results:
        raise RuntimeError("Qwen3-ASR returned no result")
    result = results[0]
    # qwen-asr returns ForcedAlignItem objects directly in ``time_stamps`` for one
    # transcription result.  Indexing once more would keep only the first item and
    # then attempt to iterate that dataclass.
    raw_timestamps = result.time_stamps or []
    words = [
        {"start": item.start_time, "end": item.end_time, "text": item.text, "confidence": None}
        for item in raw_timestamps
    ]
    text = str(result.text or "").strip()
    segment = {
        "start": words[0]["start"] if words else None,
        "end": words[-1]["end"] if words else None,
        "text": text,
        "confidence": None,
        "words": words,
    }
    return {
        "model": model_name,
        "provider": "qwen-asr-transformers",
        "language": result.language,
        "text": text,
        "segments": [segment] if text else [],
        "words": words if text else [],
        "duration": segment["end"],
        "no_speech": not bool(text),
    }


def _qwen_language(value: str | None) -> str | None:
    if value is None:
        return None
    return {"zh": "Chinese", "en": "English", "yue": "Cantonese"}.get(value, value)


def _transcribe_firered(audio_path: Path, model_name: str) -> dict[str, Any]:
    model = runtime.load(model_name)
    records = model.transcribe([audio_path.stem], [str(audio_path)])
    if not records:
        raise RuntimeError("FireRedASR2-AED returned no result")
    record = records[0]
    text = str(record.get("text") or "").strip()
    words = [
        {
            "start": float(item[1]),
            "end": float(item[2]),
            "text": str(item[0]),
            "confidence": None,
        }
        for item in (record.get("timestamp") or [])
        if len(item) >= 3
    ]
    confidence = record.get("confidence")
    segment = {
        "start": words[0]["start"] if words else None,
        "end": words[-1]["end"] if words else None,
        "text": text,
        "confidence": float(confidence) if confidence is not None else None,
        "words": words,
    }
    return {
        "model": model_name,
        "provider": "fireredasr2s-official-python",
        "language": None,
        "text": text,
        "segments": [segment] if text else [],
        "words": words if text else [],
        "duration": record.get("dur_s"),
        "raw_records": records,
        "no_speech": not bool(text),
    }


def _transcribe_mock(audio_path: Path, model_name: str, language: str | None) -> dict[str, Any]:
    runtime.load(model_name)
    return {
        "model": model_name,
        "provider": "mock-asr",
        "language": language,
        "text": "mock transcription",
        "segments": [{"start": 0.0, "end": 1.0, "text": "mock transcription", "confidence": None, "words": []}],
        "words": [],
        "duration": 1.0,
        "no_speech": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and os.getenv("VIDEO2DOC_ALLOW_PUBLIC_BIND") != "1":
        raise SystemExit("Public binding is disabled; set VIDEO2DOC_ALLOW_PUBLIC_BIND=1 to override")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
