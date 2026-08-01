from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from app.core.errors import PipelineCancelled, PipelineError
from app.documents.builder import build_document
from app.documents.editor import FaithfulTextEditor
from app.documents.exporter import ExportConfig, export_document_bundle
from app.documents.faithfulness import validate_block_edit, validate_document_faithfulness
from app.documents.markdown import render_obsidian_markdown


def sample_fusion(image_path: Path) -> dict:
    return {
        "schema_version": 1,
        "media_duration": 80.0,
        "event_count": 4,
        "events": [
            {
                "event_id": "asr:segment-000001",
                "event_type": "asr_segment",
                "start_time": 10.0,
                "end_time": 20.0,
                "source": {"kind": "audio", "provider": "whisper", "model": "large-v3"},
                "text": "第一段完整论述包含数字 42 和 APIName。",
                "original_artifact_references": ["02_asr/provider_raw/chunk.json"],
                "quality": {"language": "zh", "word_count": 8},
                "related_frame_ids": ["frame_000001"],
                "related_asr_segment_ids": ["segment-000001"],
                "uncertain": False,
            },
            {
                "event_id": "frame:frame_000001",
                "event_type": "frame_event",
                "start_time": 12.0,
                "end_time": 12.0,
                "source": {"kind": "video_frame", "extraction_reason": "scene_change"},
                "text": "",
                "original_artifact_references": [str(image_path), str(image_path)],
                "quality": {"sha256": "abc"},
                "related_frame_ids": ["frame_000001"],
                "related_asr_segment_ids": ["segment-000001"],
                "uncertain": False,
            },
            {
                "event_id": "ocr:ocr_000001",
                "event_type": "ocr_event",
                "start_time": 12.0,
                "end_time": 12.0,
                "source": {"kind": "video_frame", "provider": "paddleocr"},
                "text": "公式：E=mc^2，PaddleOCR 版本 3.3.0",
                "original_artifact_references": ["03_ocr/provider_raw/frame.json"],
                "quality": {"language": "zh", "block_count": 1},
                "related_frame_ids": ["frame_000001"],
                "related_asr_segment_ids": ["segment-000001"],
                "uncertain": False,
            },
            {
                "event_id": "asr:segment-000002",
                "event_type": "asr_segment",
                "start_time": 30.0,
                "end_time": 40.0,
                "source": {"kind": "audio", "provider": "whisper", "model": "large-v3"},
                "text": "第二段继续原始论证，不生成总结。",
                "original_artifact_references": ["02_asr/provider_raw/chunk.json"],
                "quality": {"language": "zh", "word_count": 6},
                "related_frame_ids": [],
                "related_asr_segment_ids": ["segment-000002"],
                "uncertain": False,
            },
        ],
    }


def sample_document(tmp_path: Path, *, source_type: str = "URL"):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    source = (
        "https://www.youtube.com/watch?v=abc"
        if source_type == "URL"
        else r"D:\Video2Doc-Test\sample.mp4"
    )
    return build_document(
        sample_fusion(image),
        title='课程：第 1 讲 / "忠实转换"',
        source=source,
        source_type=source_type,
        media_info={"duration": 80.0},
        models={"asr": "large-v3", "ocr": "PP-OCRv5", "text_processing": None},
        processing={
            "ocr_enabled": True,
            "text_edit_enabled": False,
            "keep_timestamps": True,
            "keep_json_sidecar": True,
        },
        artifact_references={"fusion": "04_fusion/timeline.json"},
        document_id="task-000001",
    )


def test_document_ir_preserves_asr_ocr_frame_sources_and_order(tmp_path: Path) -> None:
    document = sample_document(tmp_path)
    blocks = document.all_blocks()
    assert [block.timestamp.start_seconds for block in blocks] == [10.0, 12.0, 12.0, 30.0]
    assert {block.block_type for block in blocks} >= {
        "transcript",
        "visual_text",
        "image",
    }
    assert set(document.source_ids()) == {
        "frame:frame_000001",
        "ocr:ocr_000001",
        "asr:segment-000001",
        "asr:segment-000002",
    }


def test_markdown_frontmatter_and_seekable_timestamp_are_valid(tmp_path: Path) -> None:
    document = sample_document(tmp_path)
    for block in document.all_blocks():
        if block.block_type == "image":
            block.image_relative_path = "课程.assets/frame_000001_00-00-12.jpg"
    markdown = render_obsidian_markdown(document)
    frontmatter = markdown.split("---", 2)[1]
    parsed = yaml.safe_load(frontmatter)
    assert parsed["title"] == document.title
    assert parsed["models"]["asr"] == "large-v3"
    assert "[00:00:10](https://www.youtube.com/watch?v=abc&t=10s)" in markdown
    assert "第一段完整论述包含数字 42 和 APIName。" in markdown
    assert "画面文字（OCR）" in markdown
    assert "课程.assets/frame_000001_00-00-12.jpg" in markdown
    assert "核心观点" not in markdown


def test_markdown_omits_all_presentation_timestamps_when_disabled(tmp_path: Path) -> None:
    document = sample_document(tmp_path)
    document.processing["keep_timestamps"] = False
    for block in document.all_blocks():
        if block.block_type == "image":
            block.image_relative_path = "课程.assets/frame_000001.jpg"
    markdown = render_obsidian_markdown(document)
    frontmatter = yaml.safe_load(markdown.split("---", 2)[1])
    assert "created_at" not in frontmatter
    assert "[00:00:" not in markdown
    assert "00:00:10" not in markdown
    assert "| 文档块 | 类型 | 时间 | 来源 ID |" not in markdown
    assert "frame_000001.jpg" in markdown


def test_faithfulness_rejects_number_identifier_and_formula_loss(tmp_path: Path) -> None:
    document = sample_document(tmp_path)
    visual = next(block for block in document.all_blocks() if block.block_type == "visual_text")
    visual.edited_text = "公式：文字已大幅删减"
    report = validate_block_edit(
        visual,
        returned_source_ids=[item.source_id for item in visual.source_references],
    )
    assert not report.accepted
    assert {item.code for item in report.issues} >= {
        "NUMBERS_LOST",
        "IDENTIFIERS_LOST",
        "FORMULA_SYMBOLS_LOST",
    }


@pytest.mark.asyncio
async def test_text_editor_accepts_structured_faithful_response(tmp_path: Path) -> None:
    document = sample_document(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        request_json = json.loads(request.content)
        input_json = json.loads(request_json["messages"][1]["content"])
        blocks = [
            {
                "block_id": item["block_id"],
                "edited_text": item["text"].replace("，", "，\n"),
                "source_ids": item["source_ids"],
            }
            for item in input_json["blocks"]
        ]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"blocks": blocks})}}]},
        )

    editor = FaithfulTextEditor(
        base_url="http://127.0.0.1:8800/v1",
        api_key="not-recorded",
        model="faithful-model",
        transport=httpx.MockTransport(handler),
    )
    raw_path = tmp_path / "edit_raw.json"
    edited, run = await editor.edit(document, raw_path=raw_path)
    assert run["accepted"] is True
    assert edited.edit_metadata["prompt_version"] == "faithful-edit-v1"
    assert raw_path.is_file()
    assert "not-recorded" not in raw_path.read_text(encoding="utf-8")
    assert validate_document_faithfulness(edited).accepted


@pytest.mark.asyncio
async def test_text_editor_rejects_shortened_response_and_falls_back(tmp_path: Path) -> None:
    document = sample_document(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        input_json = json.loads(json.loads(request.content)["messages"][1]["content"])
        blocks = [
            {
                "block_id": item["block_id"],
                "edited_text": "摘要",
                "source_ids": item["source_ids"],
            }
            for item in input_json["blocks"]
        ]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"blocks": blocks})}}]},
        )

    editor = FaithfulTextEditor(
        base_url="http://127.0.0.1:8800",
        api_key="secret",
        model="faithful-model",
        transport=httpx.MockTransport(handler),
    )
    edited, run = await editor.edit(document, raw_path=tmp_path / "rejected.json")
    assert run["accepted"] is False
    assert run["fallback_used"] is True
    assert all(block.edited_text is None for block in edited.all_blocks())


@pytest.mark.asyncio
async def test_text_editor_reports_missing_packaged_prompt_as_resource_error(
    tmp_path: Path,
) -> None:
    editor = FaithfulTextEditor(
        base_url="http://127.0.0.1:8800",
        api_key="secret",
        model="faithful-model",
        prompt_path=tmp_path / "missing-prompt.txt",
    )
    with pytest.raises(PipelineError) as missing:
        await editor.edit(sample_document(tmp_path), raw_path=tmp_path / "unused.json")
    assert missing.value.code == "TEXT_PROMPT_MISSING"
    assert "提示词资源缺失" in missing.value.user_message


def test_obsidian_export_is_atomic_relative_and_renames_conflicts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    document = sample_document(tmp_path)
    config = ExportConfig(vault_path=str(vault), inbox_relative_path="Inbox")
    first = export_document_bundle(
        document,
        config,
        sidecars={"timeline.json": sample_fusion(tmp_path / "frame.jpg")},
    )
    second = export_document_bundle(document, config)
    first_markdown = Path(first.markdown_path)
    second_markdown = Path(second.markdown_path)
    assert first_markdown.is_file()
    assert second_markdown.is_file()
    assert second.conflict_resolution == "renamed"
    markdown = first_markdown.read_text(encoding="utf-8")
    assert str(tmp_path) not in markdown
    image_links = [
        line.split("](", 1)[1].removesuffix(")")
        for line in markdown.splitlines()
        if line.startswith("![")
    ]
    assert image_links
    assert all((first_markdown.parent / link).is_file() for link in image_links)
    assert first.data_directory and (Path(first.data_directory) / "document.json").is_file()
    assert not list((vault / "Inbox").glob(".video2doc-*.tmp"))


def test_obsidian_export_cancel_rolls_back_without_partial_files(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    document = sample_document(tmp_path)
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    with pytest.raises(PipelineCancelled):
        export_document_bundle(
            document,
            ExportConfig(vault_path=str(vault), inbox_relative_path="Inbox"),
            cancel_check=cancelled,
        )
    inbox = vault / "Inbox"
    assert not list(inbox.glob("*.md"))
    assert not list(inbox.glob("*.assets"))
    assert not list(inbox.glob(".video2doc-*.tmp"))


def test_obsidian_export_rejects_unsafe_inbox_and_existing_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ExportConfig(vault_path=str(tmp_path), inbox_relative_path="../outside")
    with pytest.raises(ValueError):
        ExportConfig(vault_path=str(tmp_path), filename_template="{title.__class__}")
    vault = tmp_path / "vault"
    inbox = vault / "Inbox"
    inbox.mkdir(parents=True)
    document = sample_document(tmp_path)
    config = ExportConfig(
        vault_path=str(vault),
        inbox_relative_path="Inbox",
        conflict_policy="fail",
    )
    export_document_bundle(document, config)
    with pytest.raises(PipelineError, match="已经存在"):
        export_document_bundle(document, config)
