from __future__ import annotations

import json
import os
import shutil
import string
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.errors import PipelineCancelled, PipelineError
from app.documents.ir import Document
from app.documents.markdown import render_obsidian_markdown
from app.storage.workspace import safe_filename


ExportCancelCheck = Callable[[], bool]


class ExportConfig(BaseModel):
    vault_path: str = Field(min_length=1)
    inbox_relative_path: str = "Inbox"
    filename_template: str = "{title}"
    attachment_directory_template: str = "{stem}.assets"
    data_directory_template: str = "{stem}.data"
    conflict_policy: Literal["rename", "fail", "overwrite"] = "rename"
    keep_json_sidecar: bool = True
    copy_transcript: bool = False
    copy_subtitles: bool = False

    @field_validator("inbox_relative_path")
    @classmethod
    def inbox_must_be_relative(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Inbox path must be a safe relative path")
        return value or "."

    @field_validator("filename_template")
    @classmethod
    def filename_template_fields(cls, value: str) -> str:
        _validate_template(value, {"title", "document_id"})
        return value

    @field_validator(
        "attachment_directory_template",
        "data_directory_template",
    )
    @classmethod
    def directory_template_fields(cls, value: str) -> str:
        _validate_template(value, {"title", "stem"})
        return value


@dataclass(frozen=True, slots=True)
class ExportResult:
    markdown_path: str
    attachment_directory: str | None
    data_directory: str | None
    image_count: int
    sidecar_count: int
    conflict_resolution: str


def export_document_bundle(
    document: Document,
    config: ExportConfig,
    *,
    sidecars: dict[str, Any] | None = None,
    additional_files: dict[str, Path] | None = None,
    cancel_check: ExportCancelCheck | None = None,
) -> ExportResult:
    vault = Path(config.vault_path).expanduser()
    if not vault.is_absolute():
        raise PipelineError("EXPORT_PATH_INVALID", "Obsidian Vault 必须使用绝对路径")
    if not vault.exists() or not vault.is_dir():
        raise PipelineError("EXPORT_VAULT_NOT_FOUND", "Obsidian Vault 路径不存在或不是目录")
    try:
        vault = vault.resolve(strict=True)
    except OSError as exc:
        raise PipelineError(
            "EXPORT_PATH_INVALID",
            "无法解析 Obsidian Vault 路径",
            technical_detail=str(exc),
        ) from exc
    inbox = (vault / config.inbox_relative_path).resolve(strict=False)
    if not inbox.is_relative_to(vault):
        raise PipelineError("EXPORT_PATH_INVALID", "Inbox 路径超出 Obsidian Vault")
    inbox.mkdir(parents=True, exist_ok=True)
    _ensure_active(cancel_check)

    requested_name = config.filename_template.format(
        title=document.title,
        document_id=document.document_id,
    )
    base_stem = safe_filename(requested_name, fallback="video2doc-note")
    stem, conflict_resolution = _resolve_stem(
        inbox,
        base_stem,
        config,
        title=document.title,
    )
    attachments_name = safe_filename(
        config.attachment_directory_template.format(stem=stem, title=document.title),
        fallback=f"{stem}.assets",
    )
    data_name = safe_filename(
        config.data_directory_template.format(stem=stem, title=document.title),
        fallback=f"{stem}.data",
    )
    final_markdown = inbox / f"{stem}.md"
    final_attachments = inbox / attachments_name
    final_data = inbox / data_name
    stage = inbox / f".video2doc-export-{uuid.uuid4().hex}.tmp"
    backup = inbox / f".video2doc-backup-{uuid.uuid4().hex}.tmp"
    committed: list[Path] = []
    backed_up: list[tuple[Path, Path]] = []
    image_count = 0
    sidecar_count = 0
    staged_document = document.model_copy(deep=True)
    try:
        stage.mkdir()
        stage_attachments = stage / attachments_name
        for index, block in enumerate(staged_document.all_blocks(), 1):
            if block.block_type != "image" or not block.image_source_path:
                continue
            _ensure_active(cancel_check)
            source_image = Path(block.image_source_path)
            if not source_image.is_file():
                raise PipelineError(
                    "EXPORT_IMAGE_MISSING",
                    f"待导出的画面不存在：{block.block_id}",
                    technical_detail=str(source_image),
                )
            stage_attachments.mkdir(parents=True, exist_ok=True)
            frame_id = next(
                (
                    reference.frame_id
                    for reference in block.source_references
                    if reference.frame_id
                ),
                f"frame-{index:04d}",
            )
            timestamp = _filename_timestamp(block.timestamp.start_seconds)
            filename = safe_filename(
                f"{frame_id}_{timestamp}{source_image.suffix.lower() or '.jpg'}",
                fallback=f"frame_{index:04d}_{timestamp}.jpg",
            )
            destination = _unique_child(stage_attachments / filename)
            shutil.copy2(source_image, destination)
            relative = f"{attachments_name}/{destination.name}"
            block.image_relative_path = relative
            block.image_source_path = None
            image_count += 1

        stage_data = stage / data_name
        if config.keep_json_sidecar:
            stage_data.mkdir(parents=True, exist_ok=True)
            _write_text_atomic(
                stage_data / "document.json",
                json.dumps(
                    staged_document.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
            sidecar_count += 1
            for name, payload in (sidecars or {}).items():
                _ensure_active(cancel_check)
                filename = safe_filename(name, fallback="sidecar.json")
                if not filename.lower().endswith(".json"):
                    filename += ".json"
                _write_text_atomic(
                    _unique_child(stage_data / filename),
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                )
                sidecar_count += 1
        for name, source_file in (additional_files or {}).items():
            _ensure_active(cancel_check)
            if not source_file.is_file():
                continue
            stage_data.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, _unique_child(stage_data / safe_filename(name)))
            sidecar_count += 1

        markdown = render_obsidian_markdown(staged_document)
        _write_text_atomic(stage / final_markdown.name, markdown)
        _ensure_active(cancel_check)

        targets = [
            (stage / attachments_name, final_attachments),
            (stage / data_name, final_data),
            (stage / final_markdown.name, final_markdown),
        ]
        if config.conflict_policy == "overwrite":
            backup.mkdir()
            for _staged, target in targets:
                if target.exists():
                    backup_target = backup / target.name
                    target.replace(backup_target)
                    backed_up.append((backup_target, target))
        for staged, target in targets:
            if not staged.exists():
                continue
            _ensure_active(cancel_check)
            staged.replace(target)
            committed.append(target)
        shutil.rmtree(stage, ignore_errors=True)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        for target in reversed(committed):
            _remove_path(target)
        for backup_source, original_target in reversed(backed_up):
            if backup_source.exists():
                backup_source.replace(original_target)
        _remove_path(stage)
        _remove_path(backup)
        raise
    return ExportResult(
        markdown_path=str(final_markdown),
        attachment_directory=str(final_attachments) if image_count else None,
        data_directory=str(final_data) if sidecar_count else None,
        image_count=image_count,
        sidecar_count=sidecar_count,
        conflict_resolution=conflict_resolution,
    )


def _resolve_stem(
    inbox: Path,
    stem: str,
    config: ExportConfig,
    *,
    title: str,
) -> tuple[str, str]:
    def targets_exist(candidate: str) -> bool:
        attachments = safe_filename(
            config.attachment_directory_template.format(
                stem=candidate, title=title
            ),
            fallback=f"{candidate}.assets",
        )
        data = safe_filename(
            config.data_directory_template.format(stem=candidate, title=title),
            fallback=f"{candidate}.data",
        )
        return any(
            path.exists()
            for path in (inbox / f"{candidate}.md", inbox / attachments, inbox / data)
        )

    if not targets_exist(stem):
        return stem, "none"
    if config.conflict_policy == "fail":
        raise PipelineError("EXPORT_CONFLICT", "目标笔记或附件目录已经存在")
    if config.conflict_policy == "overwrite":
        return stem, "overwrite"
    index = 2
    while targets_exist(f"{stem} ({index})"):
        index += 1
    return f"{stem} ({index})", "renamed"


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _unique_child(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _ensure_active(cancel_check: ExportCancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise PipelineCancelled(stage="EXPORT_OBSIDIAN")


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except OSError:
            pass


def _filename_timestamp(value: float | None) -> str:
    if value is None:
        return "untimed"
    total = max(0, int(value))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}-{minutes:02d}-{seconds:02d}"


def _validate_template(value: str, allowed_fields: set[str]) -> None:
    if "\x00" in value or not value.strip():
        raise ValueError("Export template cannot be empty or contain NUL")
    try:
        fields = [
            field_name
            for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(
                value
            )
            if field_name is not None
        ]
    except ValueError as exc:
        raise ValueError("Export template contains unmatched braces") from exc
    if any(field not in allowed_fields for field in fields):
        raise ValueError(
            f"Export template fields must be one of: {', '.join(sorted(allowed_fields))}"
        )
