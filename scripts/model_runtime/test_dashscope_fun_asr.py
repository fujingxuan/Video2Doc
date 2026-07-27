from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.providers.asr import (  # noqa: E402
    AudioChunk,
    DashScopeFunAsrProvider,
    combine_asr_results,
    save_transcript_artifacts,
    validate_segments,
)
from app.core.errors import PipelineCancelled  # noqa: E402
from app.storage.workspace import WorkspacePaths  # noqa: E402


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000:
            raise SystemExit("Audio must be PCM S16LE, 16 kHz, mono WAV")
        return audio.getnframes() / audio.getframerate()


async def run(args: argparse.Namespace) -> None:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DASHSCOPE_API_KEY is missing")
    audio_path = args.audio.resolve()
    duration = wav_duration(audio_path)
    paths = WorkspacePaths.from_root(args.output.resolve())
    paths.create()
    provider = DashScopeFunAsrProvider(
        base_url=args.endpoint,
        api_key=api_key,
        workspace_id=os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip() or None,
        timeout_seconds=args.timeout,
        max_retries=args.retries,
    )
    started = time.monotonic()
    try:
        result = await provider.transcribe_stream(
            audio_path,
            model=args.model,
            language=args.language,
            paths=paths,
            cancel_check=lambda: bool(
                args.cancel_after_seconds
                and time.monotonic() - started >= args.cancel_after_seconds
            ),
        )
    except PipelineCancelled:
        print(
            json.dumps(
                {
                    "status": "cancelled",
                    "cancel_after_seconds": args.cancel_after_seconds,
                    "wall_seconds": time.monotonic() - started,
                    "normalized_output_exists": (paths.asr / "normalized.json").exists(),
                }
            )
        )
        return
    chunk = AudioChunk(
        0,
        0.0,
        duration,
        str(audio_path),
        provider=provider.name,
        model=args.model,
        status="COMPLETED",
    )
    segments = combine_asr_results([result], [chunk])
    validate_segments(segments)
    artifacts = save_transcript_artifacts(
        paths,
        segments,
        provider=provider.name,
        model=args.model,
        language=result.language,
        chunk_count=1,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "model": args.model,
                "audio_seconds": duration,
                "segments": len(segments),
                "words": sum(len(item.words) for item in segments),
                "first_timestamp": segments[0].start_time,
                "last_timestamp": segments[-1].end_time,
                "artifacts": artifacts,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="fun-asr-realtime-2026-02-28")
    parser.add_argument("--language", default="zh")
    parser.add_argument(
        "--endpoint", default="wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--cancel-after-seconds", type=float, default=0.0)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
