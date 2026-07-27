from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.errors import PipelineCancelled, PipelineError
from app.providers.asr import (
    _dashscope_connection_error,
    AsrRequest,
    AsrResult,
    AsrSegment,
    AsrWord,
    AudioChunk,
    AudioChunker,
    combine_asr_results,
    normalize_dashscope_response,
    normalize_asr_response,
    save_transcript_artifacts,
    validate_segments,
)
from app.pipeline.real import _find_reusable_asr_response
from app.storage.workspace import WorkspacePaths


class WritingRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, command: list[str], **kwargs) -> object:
        self.calls += 1
        cancel_check = kwargs["cancel_check"]
        if cancel_check():
            raise PipelineCancelled(stage="AUDIO_CHUNK")
        Path(command[-1]).write_bytes(b"RIFF" + b"0" * 100)
        return object()


def make_segment(
    chunk: int,
    start: float | None,
    end: float | None,
    text: str,
    *,
    words: tuple[AsrWord, ...] = (),
) -> AsrSegment:
    return AsrSegment(
        segment_id=f"c{chunk}",
        start_time=start,
        end_time=end,
        text=text,
        confidence=None,
        language="zh",
        provider="test",
        model="test",
        chunk_index=chunk,
        raw_response_reference=f"raw-{chunk}.json",
        words=words,
    )


@pytest.mark.asyncio
async def test_adjacent_chunks_use_five_second_overlap_and_short_final_chunk(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    audio = paths.audio / "normalized.wav"
    audio.write_bytes(b"RIFF" + b"0" * 100)
    chunks = await AudioChunker("ffmpeg", WritingRunner()).split(
        audio,
        125.0,
        paths,
        chunk_seconds=60.0,
        overlap_seconds=5.0,
        provider="test-gateway",
        model="test-model",
        attempt=2,
        cancel_check=lambda: False,
        progress=lambda *_args: None,
    )
    assert [(item.start_time, item.end_time) for item in chunks] == [
        (0.0, 60.0),
        (55.0, 115.0),
        (110.0, 125.0),
    ]
    assert chunks[-1].duration == 15.0
    manifest = json.loads((paths.chunks / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["step_seconds"] == 55.0
    assert manifest["chunks"][1] == {
        "chunk_index": 1,
        "source_start_seconds": 55.0,
        "source_end_seconds": 115.0,
        "overlap_before_seconds": 5.0,
        "overlap_after_seconds": 5.0,
        "audio_path": chunks[1].path,
        "provider": "test-gateway",
        "model": "test-model",
        "attempt": 2,
        "status": "READY",
    }
    assert manifest["chunks"][-1]["overlap_after_seconds"] == 0.0


@pytest.mark.parametrize("duplicate", ["边界处的完整句子", "边界处的完整句子。"])
def test_exact_and_punctuation_only_overlap_is_removed(duplicate: str) -> None:
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5, overlap_after_seconds=5),
    ]
    results = [
        AsrResult("test", "m", "zh", 0, "a", (make_segment(0, 55, 59, "边界处的完整句子"),)),
        AsrResult("test", "m", "zh", 1, "b", (make_segment(1, 0, 4, duplicate),)),
    ]
    diagnostics: list[dict] = []
    combined = combine_asr_results(results, chunks, diagnostics=diagnostics)
    assert [item.text for item in combined] == ["边界处的完整句子"]
    assert diagnostics[0]["type"] == "duplicate_removed"


def test_similar_but_semantically_different_boundary_text_is_retained() -> None:
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5),
    ]
    results = [
        AsrResult("test", "m", "zh", 0, "a", (make_segment(0, 55, 59, "今天讨论模型部署"),)),
        AsrResult("test", "m", "zh", 1, "b", (make_segment(1, 0, 4, "今天讨论模型训练"),)),
    ]
    diagnostics: list[dict] = []
    combined = combine_asr_results(results, chunks, diagnostics=diagnostics)
    assert [item.text for item in combined] == ["今天讨论模型部署", "今天讨论模型训练"]
    assert any(item["type"] == "boundary_conflict_retained" for item in diagnostics)


def test_word_timestamps_remove_only_confirmed_duplicate_words() -> None:
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5),
    ]
    prior_words = (AsrWord(55, 56, "hello"), AsrWord(56, 57, "world"))
    next_words = (
        AsrWord(0, 1, "world"),
        AsrWord(1, 2, "again"),
    )
    results = [
        AsrResult("test", "m", "en", 0, "a", (make_segment(0, 55, 57, "hello world", words=prior_words),)),
        AsrResult("test", "m", "en", 1, "b", (make_segment(1, 1, 3, "world again", words=next_words),)),
    ]
    diagnostics: list[dict] = []
    combined = combine_asr_results(results, chunks, diagnostics=diagnostics)
    assert [item.text for item in combined] == ["hello world", "again"]
    assert combined[1].start_time is None
    assert any(
        item.get("method") == "conflicting_word_timestamps_cleared"
        for item in diagnostics
    )
    validate_segments(combined)


def test_partial_word_overlap_keeps_uncertain_boundary_word_and_reports_conflict() -> None:
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5),
    ]
    prior_words = (AsrWord(55, 56, "known"), AsrWord(56, 57, "phrase"))
    next_words = (AsrWord(0, 1, "phrase"), AsrWord(1, 2, "different"))
    results = [
        AsrResult(
            "test",
            "m",
            "en",
            0,
            "a",
            (make_segment(0, 55, 57, "known phrase", words=prior_words),),
        ),
        AsrResult(
            "test",
            "m",
            "en",
            1,
            "b",
            (make_segment(1, 0, 2, "phrase different", words=next_words),),
        ),
    ]
    diagnostics: list[dict] = []
    combined = combine_asr_results(results, chunks, diagnostics=diagnostics)
    assert [item.text for item in combined] == ["known phrase", "different"]
    assert {
        "type": "boundary_conflict_retained",
        "method": "partial_word_overlap_remaining",
        "chunk_index": 1,
        "retained_words": ["different"],
    } in diagnostics


def test_long_timestamped_overlap_with_asr_spelling_drift_removes_duplicate_prefix() -> None:
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5),
    ]
    prior_words = (
        AsrWord(55, 56, "continue"),
        AsrWord(56, 57, "open"),
        AsrWord(57, 58, "eagle"),
        AsrWord(58, 59, "browser"),
        AsrWord(59, 60, "skill"),
    )
    next_words = (
        AsrWord(0, 1, "continue"),
        AsrWord(1, 2, "open"),
        AsrWord(2, 3, "ego"),
        AsrWord(3, 4, "brother"),
        AsrWord(4, 5, "skill"),
        AsrWord(4.8, 5.8, "next"),
    )
    results = [
        AsrResult(
            "test",
            "m",
            "en",
            0,
            "a",
            (make_segment(0, 55, 60, "continue open eagle browser skill", words=prior_words),),
        ),
        AsrResult(
            "test",
            "m",
            "en",
            1,
            "b",
            (
                make_segment(
                    1,
                    0,
                    6.2,
                    "continue open ego brother skill next",
                    words=next_words,
                ),
            ),
        ),
    ]
    diagnostics: list[dict] = []
    combined = combine_asr_results(results, chunks, diagnostics=diagnostics)
    assert [item.text for item in combined] == [
        "continue open eagle browser skill",
        "next",
    ]
    assert any(
        item.get("method") == "overlap_window_and_normalized_similarity"
        for item in diagnostics
    )


def test_segment_only_and_no_timestamp_duplicates_are_deterministic() -> None:
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5),
    ]
    timed = combine_asr_results(
        [
            AsrResult("p", "m", None, 0, "a", (make_segment(0, 57, 59, "相同"),)),
            AsrResult("p", "m", None, 1, "b", (make_segment(1, 2, 4, "相同。"),)),
        ],
        chunks,
    )
    untimed = combine_asr_results(
        [
            AsrResult("p", "m", None, 0, "a", (make_segment(0, None, None, "相同"),)),
            AsrResult("p", "m", None, 1, "b", (make_segment(1, None, None, "相 同。"),)),
        ],
        chunks,
    )
    assert len(timed) == 1
    assert len(untimed) == 1


def test_untimed_repeated_text_away_from_chunk_edge_is_retained() -> None:
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5),
    ]
    diagnostics: list[dict] = []
    combined = combine_asr_results(
        [
            AsrResult("p", "m", None, 0, "a", (make_segment(0, None, None, "repeat"),)),
            AsrResult(
                "p",
                "m",
                None,
                1,
                "b",
                (
                    make_segment(1, None, None, "new boundary text"),
                    make_segment(1, None, None, "repeat"),
                ),
            ),
        ],
        chunks,
        diagnostics=diagnostics,
    )
    assert [item.text for item in combined] == ["repeat", "new boundary text", "repeat"]
    assert not any(item["type"] == "duplicate_removed" for item in diagnostics)


def test_outputs_have_no_duplicate_and_timestamps_do_not_retreat(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5),
    ]
    combined = combine_asr_results(
        [
            AsrResult("p", "m", None, 1, "b", (make_segment(1, 0, 2, "重复句"), make_segment(1, 5, 7, "后句"))),
            AsrResult("p", "m", None, 0, "a", (make_segment(0, 55, 57, "重复句。"),)),
        ],
        chunks,
    )
    validate_segments(combined)
    artifacts = save_transcript_artifacts(paths, combined, provider="p", model="m", language=None, chunk_count=2)
    assert Path(artifacts["text"]).read_text(encoding="utf-8").count("重复句") == 1
    assert Path(artifacts["srt"]).read_text(encoding="utf-8").count("重复句") == 1
    normalized = json.loads(Path(artifacts["normalized"]).read_text(encoding="utf-8"))
    assert sum("重复句" in item["text"] for item in normalized["segments"]) == 1
    starts = [item["start_time"] for item in normalized["segments"] if item["start_time"] is not None]
    assert starts == sorted(starts)


def test_validation_rejects_word_timestamp_retreat() -> None:
    segments = [
        make_segment(
            0,
            0,
            2,
            "out of order",
            words=(AsrWord(1, 1.5, "first"), AsrWord(0.5, 1, "second")),
        )
    ]
    with pytest.raises(PipelineError, match="词时间戳顺序不稳定"):
        validate_segments(segments)


def test_uncertain_overlap_keeps_text_and_clears_only_retreating_word_times() -> None:
    chunks = [
        AudioChunk(0, 0, 60, "a", overlap_after_seconds=5),
        AudioChunk(1, 55, 115, "b", overlap_before_seconds=5),
    ]
    results = [
        AsrResult(
            "p",
            "m",
            "zh",
            0,
            "a",
            (
                make_segment(
                    0,
                    55,
                    60,
                    "原边界",
                    words=(AsrWord(55, 56, "原"), AsrWord(59, 60, "边界")),
                ),
            ),
        ),
        AsrResult(
            "p",
            "m",
            "zh",
            1,
            "b",
            (
                make_segment(
                    1,
                    1,
                    6,
                    "不同边界后续",
                    words=(AsrWord(1, 2, "不同"), AsrWord(5, 6, "后续")),
                ),
            ),
        ),
    ]
    diagnostics: list[dict] = []
    combined = combine_asr_results(results, chunks, diagnostics=diagnostics)
    assert [item.text for item in combined] == ["原边界", "不同边界后续"]
    assert combined[1].words[0].start_time is None
    assert combined[1].words[1].start_time == 60
    assert combined[1].start_time == 60
    assert any(
        item.get("method") == "conflicting_word_timestamps_cleared"
        for item in diagnostics
    )
    validate_segments(combined)


def test_dashscope_normalization_keeps_final_sentence_and_word_timestamps(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    result = normalize_dashscope_response(
        {
            "final_sentences": [
                {
                    "begin_time": 170,
                    "end_time": 920,
                    "text": "好的。",
                    "sentence_end": True,
                    "words": [{"begin_time": 170, "end_time": 920, "text": "好的", "punctuation": "。"}],
                }
            ]
        },
        request=AsrRequest(
            AudioChunk(0, 0, 1, "audio.wav"), "fun-asr-realtime-2026-02-28", "zh"
        ),
        raw_path=raw_path,
        provider="dashscope-fun-asr",
    )
    assert result.segments[0].start_time == 0.17
    assert result.segments[0].words[0].end_time == 0.92


def test_dashscope_connection_failure_has_distinct_error_code() -> None:
    failure = _dashscope_connection_error(ConnectionRefusedError("controlled refusal"))
    assert failure.code == "DASHSCOPE_CONNECTION_FAILED"
    assert failure.retryable is True


@pytest.mark.asyncio
async def test_cancellation_stops_generation_of_later_chunks(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    audio = paths.audio / "normalized.wav"
    audio.write_bytes(b"RIFF" + b"0" * 100)
    runner = WritingRunner()
    cancelled = False

    def progress(value: float | None, _message: str) -> None:
        nonlocal cancelled
        if value and value >= 0.5:
            cancelled = True

    with pytest.raises(PipelineCancelled):
        await AudioChunker("ffmpeg", runner).split(
            audio,
            115,
            paths,
            chunk_seconds=60,
            overlap_seconds=5,
            cancel_check=lambda: cancelled,
            progress=progress,
        )
    assert runner.calls == 2
    assert not (paths.chunks / "chunk_0001.wav").exists()


def test_retry_reuses_each_successful_chunk_once_without_duplicate_write(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "task")
    paths.create()
    previous = paths.asr_raw / "attempt_0001" / "chunk_0000.json"
    previous.parent.mkdir(parents=True)
    previous.write_text(
        json.dumps(
            {
                "provider": "mock",
                "model": "m",
                "text": "成功分块",
                "segments": [{"start": 0, "end": 1, "text": "成功分块"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reusable = _find_reusable_asr_response(paths.asr_raw, 2, 0)
    assert reusable == previous
    request = AsrRequest(AudioChunk(0, 0, 60, "chunk.wav"), "m")
    result = normalize_asr_response(
        json.loads(reusable.read_text(encoding="utf-8")), request, reusable, "mock"
    )
    combined = combine_asr_results([result], [request.chunk])
    assert [item.text for item in combined] == ["成功分块"]
