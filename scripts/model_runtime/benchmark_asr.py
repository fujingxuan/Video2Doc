"""Run a real, repeatable local ASR benchmark without starting a server."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import asr_gateway_service as service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()
    model_name = service.supported_models()[0]["id"]
    call = {"firered": service._transcribe_firered,
            "qwen": lambda p, m: service._transcribe_qwen(p, m, "zh"),
            "whisper": lambda p, m: service._transcribe_whisper(p, m, "zh", None)}[service.PROVIDER]
    records = []
    for index in range(args.runs):
        started = time.perf_counter()
        result = service._run_inference(call, (args.audio, model_name))
        elapsed = time.perf_counter() - started
        record = {"run": index, "wall_seconds": elapsed, "text": result["text"],
                  "word_count": len(result["words"]), "health": service.health()}
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    service.runtime.unload()


if __name__ == "__main__":
    main()
