from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx


def gpu_memory_used() -> int | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return max(int(line.strip()) for line in completed.stdout.splitlines() if line.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def run(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    baseline = gpu_memory_used()
    peak = baseline
    stop = threading.Event()

    def sample_gpu() -> None:
        nonlocal peak
        while not stop.wait(0.5):
            value = gpu_memory_used()
            if value is not None:
                peak = value if peak is None else max(peak, value)

    sampler = threading.Thread(target=sample_gpu, daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        with args.audio.open("rb") as audio, httpx.Client(
            timeout=args.timeout, trust_env=False
        ) as client:
            response = client.post(
                f"{args.base_url.rstrip('/')}/v1/asr/transcriptions",
                data={
                    "model": args.model,
                    "language": args.language,
                    "timestamp_granularity": "word",
                    "task": "transcribe",
                    "request_id": f"acceptance-{int(time.time())}",
                },
                files={"file": (args.audio.name, audio, "audio/wav")},
            )
            if response.is_error:
                print(
                    json.dumps(
                        {"status_code": response.status_code, "detail": response.text[:2000]},
                        ensure_ascii=False,
                    )
                )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            health = client.get(f"{args.base_url.rstrip('/')}/health").json()
    finally:
        elapsed = time.perf_counter() - started
        stop.set()
        sampler.join(timeout=10)
    benchmark = {
        "model": args.model,
        "wall_seconds": elapsed,
        "gpu_memory_baseline_mib": baseline,
        "gpu_memory_peak_mib": peak,
        "gpu_memory_delta_mib": (
            peak - baseline if peak is not None and baseline is not None else None
        ),
        "segments": len(payload.get("segments") or []),
        "words": len(payload.get("words") or []),
        "language": payload.get("language"),
        "load_seconds": health.get("load_seconds"),
        "loaded_model": health.get("loaded_model"),
    }
    output.write_text(
        json.dumps({"benchmark": benchmark, "response": payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({**benchmark, "output": str(output)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8103")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--timeout", type=float, default=1800)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
