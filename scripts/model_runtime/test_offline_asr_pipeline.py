from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import wave
from dataclasses import replace
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.core.errors import PipelineCancelled  # noqa: E402
from app.pipeline.real import (  # noqa: E402
    _find_reusable_asr_response,
    _update_chunk_manifest_status,
)
from app.providers.asr import (  # noqa: E402
    AsrRequest,
    AudioChunker,
    UnifiedAsrGatewayProvider,
    combine_asr_results,
    normalize_asr_response,
    save_transcript_artifacts,
    validate_segments,
)
from app.storage.workspace import WorkspacePaths, read_json, write_json  # noqa: E402


def normalize_audio(source: Path, destination: Path) -> float:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(destination),
                "-y",
            ],
            check=True,
            timeout=600,
        )
    with wave.open(str(destination), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000:
            raise RuntimeError("Normalized audio is not PCM S16LE 16 kHz mono")
        return audio.getnframes() / audio.getframerate()


async def run(args: argparse.Namespace) -> None:
    paths = WorkspacePaths.from_root(args.output.resolve())
    paths.create()
    audio_path = paths.audio / "normalized.wav"
    duration = normalize_audio(args.source.resolve(), audio_path)
    expected_chunks = 1 + max(
        0,
        int((max(duration - args.chunk_seconds, 0) + args.chunk_seconds - args.overlap - 1e-9) // (args.chunk_seconds - args.overlap)),
    )
    cancelled = False

    def progress(value: float | None, _message: str) -> None:
        nonlocal cancelled
        if args.cancel_after_chunks and value is not None:
            cancelled = value >= args.cancel_after_chunks / expected_chunks

    try:
        chunks = await AudioChunker("ffmpeg").split(
            audio_path,
            duration,
            paths,
            chunk_seconds=args.chunk_seconds,
            overlap_seconds=args.overlap,
            provider="video2doc-asr-http-v1",
            model=args.model,
            attempt=1,
            cancel_check=lambda: cancelled,
            progress=progress,
        )
    except PipelineCancelled:
        created = sorted(paths.chunks.glob("chunk_*.wav"))
        print(
            json.dumps(
                {
                    "status": "cancelled",
                    "cancel_after_chunks": args.cancel_after_chunks,
                    "created_chunks": len(created),
                    "last_chunk": created[-1].name if created else None,
                }
            )
        )
        return

    provider = UnifiedAsrGatewayProvider(
        base_url=args.base_url,
        api_key="",
        timeout_seconds=args.timeout,
        max_retries=1,
    )
    attempt_paths = replace(paths, asr_raw=paths.asr_raw / "attempt_0001")
    attempt_paths.asr_raw.mkdir(parents=True, exist_ok=True)
    results = []
    initial_reused_chunks = 0
    started = time.perf_counter()
    for chunk in chunks:
        request = AsrRequest(
            chunk,
            args.model,
            args.language,
            "word",
            request_id=f"offline-acceptance-chunk-{chunk.index}",
        )
        existing_raw = attempt_paths.asr_raw / f"chunk_{chunk.index:04d}.json"
        if args.reuse_existing_raw and existing_raw.is_file():
            payload = read_json(existing_raw) or {}
            result = normalize_asr_response(
                payload,
                request,
                existing_raw,
                str(payload.get("provider") or provider.name),
            )
            initial_reused_chunks += 1
        else:
            result = await provider.transcribe(
                request,
                attempt_paths,
                cancel_check=lambda: False,
            )
        results.append(result)
        _update_chunk_manifest_status(paths.chunks / "manifest.json", chunk.index, "COMPLETED", 1)
    transcribe_seconds = time.perf_counter() - started
    diagnostics: list[dict] = []
    segments = combine_asr_results(results, chunks, diagnostics=diagnostics)
    validate_segments(segments)
    artifacts = save_transcript_artifacts(
        paths,
        segments,
        provider=provider.name,
        model=args.model,
        language=next((item.language for item in results if item.language), None),
        chunk_count=len(chunks),
        merge_diagnostics=diagnostics,
    )

    reused_results = []
    for chunk in chunks:
        reusable = _find_reusable_asr_response(paths.asr_raw, 2, chunk.index)
        if reusable is None:
            raise RuntimeError(f"Retry could not reuse successful chunk {chunk.index}")
        payload = read_json(reusable) or {}
        reused_results.append(
            normalize_asr_response(
                payload,
                AsrRequest(chunk, args.model, args.language, "word"),
                reusable,
                str(payload.get("provider") or provider.name),
            )
        )
    retry_segments = combine_asr_results(reused_results, chunks)
    if [item.text for item in retry_segments] != [item.text for item in segments]:
        raise RuntimeError("Retry reuse changed merged transcript")
    retry_summary = {
        "attempt": 2,
        "reused_chunks": len(reused_results),
        "new_gateway_requests": 0,
        "duplicate_segments": len(retry_segments) - len(segments),
    }
    write_json(paths.metadata / "retry_reuse.json", retry_summary)
    print(
        json.dumps(
            {
                "status": "ok",
                "audio_seconds": duration,
                "chunk_seconds": args.chunk_seconds,
                "overlap_seconds": args.overlap,
                "chunks": len(chunks),
                "segments": len(segments),
                "words": sum(len(item.words) for item in segments),
                "merge_diagnostics": len(diagnostics),
                "duplicates_removed": sum(
                    item.get("type") in {"duplicate_removed", "word_overlap_removed"}
                    for item in diagnostics
                ),
                "transcribe_seconds": transcribe_seconds,
                "initial_reused_chunks": initial_reused_chunks,
                "retry": retry_summary,
                "artifacts": artifacts,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--base-url", default="http://127.0.0.1:8103")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--chunk-seconds", type=float, default=30.0)
    parser.add_argument("--overlap", type=float, default=5.0)
    parser.add_argument("--cancel-after-chunks", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--reuse-existing-raw", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
