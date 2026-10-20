"""Bounded re-recognition of decoder loops; never delete suspected speech."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from app.core.errors import PipelineError
from app.providers.asr import (
    AsrRequest, AsrResult, AudioChunker, combine_asr_results, normalize_asr_response,
    normalize_text_for_overlap,
)
from app.storage.workspace import read_json, write_json


def repetition_spans(text: str) -> list[dict]:
    normalized = normalize_text_for_overlap(text)
    # Deliberately conservative: short emphasis and distant quotations are not loops.
    return [
        {"phrase": match.group(1), "repetitions": len(match.group()) // len(match.group(1))}
        for match in re.finditer(r"(.{2,80}?)\1{4,}", normalized)
        if len(match.group()) >= 24
    ]


async def recover_repetition(result: AsrResult, request: AsrRequest, paths, provider,
                             ffmpeg: str, cancelled, progress) -> tuple[AsrResult, list[dict]]:
    suspicious = repetition_spans("".join(segment.text for segment in result.segments))
    if not suspicious:
        return result, []
    report = {
        "type": "asr_repetition_detected", "chunk_index": request.chunk.index,
        "source_start_seconds": request.chunk.start_time,
        "source_end_seconds": request.chunk.end_time,
        "original_response": result.raw_response_reference, "patterns": suspicious,
        "resolved": False,
    }
    root = paths.asr_raw / f"quality_chunk_{request.chunk.index:04d}"
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "quality.json"
    report["report_path"] = str(report_path)
    try:
        # Small chunks cannot benefit from further splitting; expose the doubt instead.
        if request.chunk.duration > 15:
            progress(None, f"第 {request.chunk.index + 1} 块出现异常重复，正在用短音频重新识别")
            retry_paths = replace(paths, chunks=root / "audio", asr_raw=root / "responses")
            retry_paths.asr_raw.mkdir(parents=True, exist_ok=True)
            chunks = await AudioChunker(ffmpeg).split(
                Path(request.chunk.path), request.chunk.duration, retry_paths,
                chunk_seconds=15, overlap_seconds=2, model=request.model,
                cancel_check=cancelled, progress=lambda *_: None,
            )
            retried = []
            for chunk in chunks:
                retry_request = replace(request, chunk=chunk,
                                        request_id=f"{request.request_id}-quality-{chunk.index}")
                raw = retry_paths.asr_raw / f"chunk_{chunk.index:04d}.json"
                if raw.is_file():
                    item = normalize_asr_response(read_json(raw) or {}, retry_request, raw,
                                                  result.provider)
                else:
                    item = await provider.transcribe(retry_request, retry_paths,
                                                     cancel_check=cancelled)
                retried.append(item)
            combined = combine_asr_results(retried, chunks)
            report["retry_responses"] = [item.raw_response_reference for item in retried]
            # Never silently replace speech with an empty response, including a single
            # empty subchunk. A remaining loop requires human listening, not deletion.
            if all(item.segments for item in retried) and not repetition_spans(
                "".join(segment.text for segment in combined)
            ):
                result = replace(result, segments=tuple(
                    replace(segment, chunk_index=request.chunk.index) for segment in combined
                ))
                report["resolved"] = True
    except PipelineError as exc:
        if exc.code in {"PROCESS_CANCELLED", "backend_shutdown"}:
            raise
        report["retry_error"] = exc.code
    write_json(report_path, report)
    return result, [report]
