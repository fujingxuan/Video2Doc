from __future__ import annotations

from app.models import AppSetting, ModelConfig, ProcessingProfile, Task
from app.core.credentials import credential_is_configured, credential_source
from app.schemas.api import (
    ModelRead,
    ProfileRead,
    ProfileModelSelection,
    SettingsRead,
    SnapshotRead,
    StageRead,
    TaskDetail,
    TaskRead,
)
from app.pipeline.model_usage import stage_model_usage
from app.storage.workspace import list_artifacts, read_json
from pathlib import Path


def model_read(item: ModelConfig) -> ModelRead:
    return ModelRead(
        id=item.id,
        name=item.name,
        display_name=_model_label(item.name, [cap.capability for cap in item.capabilities]),
        capabilities=[cap.capability for cap in item.capabilities],
        notes=item.notes,
        enabled=item.enabled,
        provider=item.provider,
        base_url=item.base_url,
        secret_env_name=item.secret_env_name,
        secret_source=credential_source(item),
        secret_configured=credential_is_configured(item),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def profile_read(item: ProcessingProfile) -> ProfileRead:
    values = {
        column.name: getattr(item, column.name) for column in item.__table__.columns
    }
    return ProfileRead.model_validate(
        {
            **values,
            "asr_selection": _profile_selection(
                "ASR", item.asr_gateway_model, item.asr_model
            ),
            "ocr_selection": _profile_selection(
                "OCR", item.ocr_gateway_model, item.ocr_model
            ),
            "text_selection": _profile_selection(
                "TEXT_PROCESSING", None, item.text_model
            ),
        }
    )


def _profile_selection(
    capability: str,
    gateway_model,
    model,
) -> ProfileModelSelection | None:
    if gateway_model is not None:
        return ProfileModelSelection(
            capability=capability,
            source="GATEWAY",
            model_id=gateway_model.id,
            model_name=gateway_model.model_name,
            display_name=gateway_model.display_name,
            gateway_id=gateway_model.gateway_id,
            gateway_name=gateway_model.gateway.name,
            protocol=gateway_model.gateway.protocol,
        )
    if model is not None:
        return ProfileModelSelection(
            capability=capability,
            source="MODEL_CONFIG",
            model_id=model.id,
            model_name=model.name,
            display_name=_model_label(model.name, [capability]),
        )
    return None


def _model_label(name: str, capabilities: list[str]) -> str:
    scenarios = {"TEXT_PROCESSING": "全文校对", "ASR": "语音转写", "OCR": "画面识别"}
    scenario = "·".join(scenarios[cap] for cap in capabilities if cap in scenarios)
    short_name = name.rsplit("/", 1)[-1]
    return f"{short_name}（{scenario or '文本处理'}）"


def settings_read(item: AppSetting) -> SettingsRead:
    return SettingsRead(
        id=item.id,
        oneapi_base_url=item.oneapi_base_url,
        api_key_configured=False,
        obsidian_vault_path=item.obsidian_vault_path,
        inbox_path=item.inbox_path,
        data_path=item.data_path,
        ffmpeg_path=item.ffmpeg_path,
        ytdlp_path=item.ytdlp_path,
        ocr_command=item.ocr_command,
        last_profile_id=item.last_profile_id,
        last_task_config=item.last_task_config,
        default_markdown_template=item.default_markdown_template,
        default_ocr_enabled=item.default_ocr_enabled,
        default_keep_raw_transcript=item.default_keep_raw_transcript,
        default_keep_timestamps=item.default_keep_timestamps,
        default_keep_downloaded_media=item.default_keep_downloaded_media,
        default_keep_frames=item.default_keep_frames,
        default_text_edit_enabled=item.default_text_edit_enabled,
        default_text_model_name=item.default_text_model_name,
        text_prompt_version=item.text_prompt_version,
        faithfulness_min_length_ratio=item.faithfulness_min_length_ratio,
        faithfulness_formula_symbol_ratio=item.faithfulness_formula_symbol_ratio,
        markdown_template_version=item.markdown_template_version,
        image_insertion_policy=item.image_insertion_policy,
        image_min_gap_seconds=item.image_min_gap_seconds,
        export_filename_template=item.export_filename_template,
        export_attachment_directory_template=item.export_attachment_directory_template,
        export_data_directory_template=item.export_data_directory_template,
        export_conflict_policy=item.export_conflict_policy,
        export_keep_json_sidecar=item.export_keep_json_sidecar,
        export_copy_transcript=item.export_copy_transcript,
        export_copy_subtitles=item.export_copy_subtitles,
        auto_clear_task_cache=item.auto_clear_task_cache,
        updated_at=item.updated_at,
    )


def task_read(item: Task) -> TaskRead:
    return TaskRead(**{field: getattr(item, field) for field in TaskRead.model_fields})


def task_detail(item: Task) -> TaskDetail:
    base = task_read(item).model_dump()
    workspace = Path(item.workspace_path) if item.workspace_path else None
    media_info = read_json(Path(item.media_info_path)) if item.media_info_path else None
    preview_path = workspace / "02_asr/preview.json" if workspace else None
    transcript_preview = read_json(preview_path) if preview_path else None
    frame_manifest = (
        read_json(Path(item.frames_manifest_path)) if item.frames_manifest_path else None
    )
    frame_summary = (
        {
            key: frame_manifest.get(key)
            for key in (
                "candidate_count",
                "kept_count",
                "dropped_count",
                "drop_reasons",
                "config",
            )
        }
        if frame_manifest
        else None
    )
    ocr_preview = read_json(workspace / "03_ocr/preview.json") if workspace else None
    fusion_preview = (
        read_json(workspace / "04_fusion/preview.json") if workspace else None
    )
    document = read_json(Path(item.document_path)) if item.document_path else None
    document_preview = (
        {
            "schema_version": document.get("schema_version"),
            "document_id": document.get("document_id"),
            "title": document.get("title"),
            "media_duration_seconds": document.get("media_duration_seconds"),
            "section_count": len(document.get("sections") or []),
            "block_count": sum(
                len(section.get("blocks") or [])
                for section in document.get("sections") or []
                if isinstance(section, dict)
            ),
            "edit_metadata": document.get("edit_metadata") or {},
        }
        if document
        else None
    )
    markdown_preview = None
    if item.markdown_path:
        try:
            markdown_preview = Path(item.markdown_path).read_text(encoding="utf-8")[:200_000]
        except OSError:
            markdown_preview = None
    exports = [
        {
            "id": export.id,
            "attempt": export.attempt,
            "target_type": export.target_type,
            "status": export.status,
            "markdown_path": export.markdown_path,
            "word_path": (export.config_json or {}).get("word_path"),
            "attachment_directory": export.attachment_directory,
            "data_directory": export.data_directory,
            "image_count": export.image_count,
            "sidecar_count": export.sidecar_count,
            "error_message": export.error_message,
            "created_at": export.created_at,
        }
        for export in item.markdown_exports
    ]
    return TaskDetail(
        **base,
        snapshot=SnapshotRead(
            schema_version=item.snapshot.schema_version,
            snapshot_json=item.snapshot.snapshot_json,
            created_at=item.snapshot.created_at,
        ),
        stages=[
            StageRead.model_validate(
                {
                    column.name: getattr(stage, column.name)
                    for column in stage.__table__.columns
                }
                | {
                    "model_usage": stage.model_usage
                    or stage_model_usage(stage.stage, item.snapshot.snapshot_json)
                }
            )
            for stage in item.stages
        ],
        media_info=media_info,
        transcript_preview=transcript_preview,
        frame_summary=frame_summary,
        ocr_preview=ocr_preview,
        fusion_preview=fusion_preview,
        document_preview=document_preview,
        markdown_preview=markdown_preview,
        exports=exports,
        artifacts=list_artifacts(workspace) if workspace else [],
    )
