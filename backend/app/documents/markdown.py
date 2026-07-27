from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.documents.ir import Document, DocumentBlock


MARKDOWN_TEMPLATE_VERSION = "obsidian-v1"


def render_obsidian_markdown(document: Document) -> str:
    frontmatter = {
        "title": document.title,
        "source": document.source.source,
        "source_type": document.source.source_type,
        "platform": document.source.platform,
        "created_at": document.created_at.isoformat(),
        "duration_seconds": document.media_duration_seconds,
        "language": document.language,
        "video2doc_schema": document.schema_version,
        "markdown_template": MARKDOWN_TEMPLATE_VERSION,
        "models": document.models,
        "processing": _processing_summary(document.processing),
        "tags": ["video2doc"],
    }
    lines = ["---"]
    lines.extend(f"{key}: {_yaml_value(value)}" for key, value in frontmatter.items())
    lines.extend(["---", "", f"# {document.title}", "", "## 媒体来源", ""])
    lines.append(f"- 来源类型：{document.source.source_type}")
    lines.append(f"- 来源：{document.source.source}")
    if document.media_duration_seconds is not None:
        lines.append(f"- 时长：{_format_timestamp(document.media_duration_seconds)}")
    lines.extend(["", "## 正文", ""])

    for section in document.sections:
        lines.extend([f"### {section.title}", ""])
        for block in section.blocks:
            lines.extend(_render_block(document, block))
    lines.extend(_traceability_appendix(document))
    return "\n".join(lines).rstrip() + "\n"


def _render_block(document: Document, block: DocumentBlock) -> list[str]:
    anchor = _timestamp_anchor(document, block)
    source_ids = ", ".join(item.source_id for item in block.source_references)
    if block.block_type == "image":
        if not block.image_relative_path:
            return []
        relative = PurePosixPath(block.image_relative_path).as_posix()
        alt = _single_line(block.image_alt or "相关画面")
        frame_ids = [
            item.frame_id for item in block.source_references if item.frame_id
        ]
        detail = f"画面时间 {anchor}"
        if frame_ids:
            detail += f" · frame_id: `{frame_ids[0]}`"
        return [f"![{alt}]({relative})", "", f"*{detail}*", ""]
    text = block.display_text.strip()
    if not text:
        return []
    if block.block_type == "transcript":
        lines = [f"{anchor} **讲者内容**", "", text, ""]
    elif block.block_type == "code":
        lines = [f"{anchor} **画面代码或公式**", "", text, ""]
    elif block.block_type == "quote":
        quoted = "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
        lines = [f"{anchor} **画面引用**", "", quoted, ""]
    else:
        quoted = "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
        lines = [f"{anchor} **画面文字（OCR）**", "", quoted, ""]
    lines.extend([f"<!-- video2doc-sources: {source_ids} -->", ""])
    return lines


def _traceability_appendix(document: Document) -> list[str]:
    lines = [
        "---",
        "",
        "<details>",
        "<summary>处理与追溯信息</summary>",
        "",
        f"- 文档 IR：schema v{document.schema_version}",
        f"- Markdown 模板：{MARKDOWN_TEMPLATE_VERSION}",
        f"- 文本整理：{_edit_status(document)}",
        "",
        "| 文档块 | 类型 | 时间 | 来源 ID |",
        "| --- | --- | --- | --- |",
    ]
    for block in document.all_blocks():
        source_ids = "<br>".join(
            _table_escape(item.source_id) for item in block.source_references
        )
        lines.append(
            f"| `{_table_escape(block.block_id)}` | {block.block_type} | "
            f"{_format_timestamp(block.timestamp.start_seconds)} | {source_ids} |"
        )
    lines.extend(["", "</details>", ""])
    return lines


def _timestamp_anchor(document: Document, block: DocumentBlock) -> str:
    label = _format_timestamp(block.timestamp.start_seconds)
    seconds = block.timestamp.start_seconds
    if seconds is None or document.source.source_type != "URL":
        return f"[{label}]"
    link = _seekable_url(document.source.source, int(seconds))
    return f"[{label}]({link})" if link else f"[{label}]"


def _seekable_url(value: str, seconds: int) -> str | None:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").casefold()
    if hostname in {"youtu.be", "www.youtube.com", "youtube.com", "m.youtube.com"}:
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["t"] = f"{seconds}s"
        return urlunparse(parsed._replace(query=urlencode(query)))
    if hostname.endswith("bilibili.com") or hostname == "b23.tv":
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["t"] = str(seconds)
        return urlunparse(parsed._replace(query=urlencode(query)))
    return None


def _processing_summary(processing: dict[str, object]) -> dict[str, object]:
    keys = (
        "ocr_enabled",
        "text_edit_enabled",
        "prompt_version",
        "keep_timestamps",
        "frame_sampling_strategy",
        "image_insertion_policy",
        "conflict_policy",
        "keep_json_sidecar",
    )
    return {key: processing[key] for key in keys if key in processing}


def _edit_status(document: Document) -> str:
    if (
        not document.edit_metadata
        or document.edit_metadata.get("enabled") is False
        or document.edit_metadata.get("reason") == "no_model_mode"
    ):
        return "未启用（忠实原文回退）"
    if document.edit_metadata.get("accepted"):
        return (
            f"已通过忠实性校验 · {document.edit_metadata.get('model') or 'unknown'} · "
            f"{document.edit_metadata.get('prompt_version') or 'unknown'}"
        )
    return "模型响应被拒绝，已回退到未整理文本"


def _yaml_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _format_timestamp(value: float | None) -> str:
    if value is None:
        return "--:--:--"
    total = max(0, int(value))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _single_line(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).strip()


def _table_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
