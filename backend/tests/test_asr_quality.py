from dataclasses import replace

import pytest

from app.pipeline.asr_quality import recover_repetition, repetition_spans
from app.providers.asr import AsrRequest, AsrResult, AudioChunk, combine_asr_results, normalize_asr_response
from app.storage.workspace import WorkspacePaths, read_json
from tests.test_asr_overlap import make_segment


def test_repetition_detection_does_not_remove_emphasis_or_separate_mentions():
    assert repetition_spans("嗯好的谢谢" * 20)
    assert repetition_spans("大家好。" * 12)
    assert not repetition_spans("不不不，我的意思是要保留原始内容。")
    assert not repetition_spans("谢谢谢谢，谢谢大家。")
    assert not repetition_spans("今天讨论模型。然后做实验。最后再次讨论模型。")


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_text,resolved", [("重新识别的有效内容", True), ("嗯好的谢谢" * 20, False), ("", False)])
async def test_short_retry_keeps_original_responses_and_uncertainty(tmp_path, monkeypatch, retry_text, resolved):
    paths = WorkspacePaths.from_root(tmp_path)
    paths.create()
    original = paths.asr_raw / "chunk_0000.json"
    original.write_text("original response", encoding="utf-8")
    result = AsrResult("test", "test", "zh", 4, str(original), (
        make_segment(4, 0, 30, "嗯好的谢谢" * 20),
    ))
    request = AsrRequest(AudioChunk(4, 200, 230, "audio.wav"), "test")

    async def split(*args, **kwargs):
        return [AudioChunk(0, 0, 15, "first.wav"), AudioChunk(1, 15, 30, "last.wav")]

    class Provider:
        async def transcribe(self, req, retry_paths, **kwargs):
            segment = make_segment(req.chunk.index, 0, 14, retry_text)
            return replace(result, chunk_index=req.chunk.index,
                           segments=(segment,) if retry_text else ())

    monkeypatch.setattr("app.pipeline.asr_quality.AudioChunker.split", split)
    actual, report = await recover_repetition(result, request, paths, Provider(), "ffmpeg",
                                             lambda: False, lambda *_: None)
    assert report[0]["resolved"] is resolved
    assert original.read_text(encoding="utf-8") == "original response"
    assert read_json(paths.asr_raw / "quality_chunk_0004/quality.json")["patterns"]
    if resolved:
        assert [s.chunk_index for s in actual.segments] == [4, 4]
        merged = combine_asr_results([actual], [request.chunk])
        assert [s.start_time for s in merged] == [200, 215]
    else:
        assert actual == result


def test_segment_only_exact_boundary_prefix_is_removed_with_time_evidence():
    phrase = "这是一段需要完整保留的边界原文"
    first = AsrResult("test", "m", "zh", 0, "a", (make_segment(0, 50, 60, "前文" + phrase),))
    second = AsrResult("test", "m", "zh", 1, "b", (make_segment(1, 0, 9, phrase + "后续新内容"),))
    chunks = [AudioChunk(0, 0, 60, "a"), AudioChunk(1, 55, 115, "b", overlap_before_seconds=5)]
    combined = combine_asr_results([first, second], chunks)
    assert [s.text for s in combined] == ["前文" + phrase, "后续新内容"]
    later = replace(second, segments=(make_segment(1, 10, 20, phrase + "后续新内容"),))
    assert combine_asr_results([first, later], chunks)[1].text == phrase + "后续新内容"


def test_word_overlap_keeps_punctuation_supplied_only_in_segment_text(tmp_path):
    chunks = [AudioChunk(0, 0, 10, "a"), AudioChunk(1, 8, 18, "b", overlap_before_seconds=2)]
    first = normalize_asr_response({"segments": [{
        "start": 8, "end": 10, "text": "你好！", "words": [{"start": 8, "end": 10, "text": "你好"}],
    }]}, AsrRequest(chunks[0], "m"), tmp_path / "a.json", "test")
    second = normalize_asr_response({"segments": [{
        "start": 0, "end": 6, "text": "你好！今天，继续。", "words": [
            {"start": 0, "end": 2, "text": "你好"},
            {"start": 2, "end": 4, "text": "今天"},
            {"start": 4, "end": 6, "text": "继续"},
        ],
    }]}, AsrRequest(chunks[1], "m"), tmp_path / "b.json", "test")
    combined = combine_asr_results([first, second], chunks)
    assert [s.text for s in combined] == ["你好！", "今天，继续。"]
