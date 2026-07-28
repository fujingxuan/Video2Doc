from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BlockType = Literal["transcript", "visual_text", "image", "code", "quote"]
SourceType = Literal["asr_segment", "ocr_event", "frame", "media"]


class TimestampAnchor(BaseModel):
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def end_not_before_start(self) -> TimestampAnchor:
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds + 1e-6 < self.start_seconds
        ):
            raise ValueError("timestamp end cannot precede start")
        return self


class SourceReference(BaseModel):
    source_id: str = Field(min_length=1, max_length=300)
    source_type: SourceType
    timestamp: TimestampAnchor
    asr_segment_id: str | None = None
    ocr_event_id: str | None = None
    frame_id: str | None = None
    artifact_reference: str | None = None
    raw_response_references: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentBlock(BaseModel):
    block_id: str = Field(min_length=1, max_length=300)
    block_type: BlockType
    timestamp: TimestampAnchor
    original_text: str = ""
    edited_text: str | None = None
    source_references: list[SourceReference] = Field(min_length=1)
    image_source_path: str | None = None
    image_relative_path: str | None = None
    image_alt: str | None = None
    uncertain: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def display_text(self) -> str:
        return self.edited_text if self.edited_text is not None else self.original_text

    @field_validator("source_references")
    @classmethod
    def unique_sources(cls, value: list[SourceReference]) -> list[SourceReference]:
        identifiers = [item.source_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("source references must be unique within a block")
        return value


class DocumentSection(BaseModel):
    section_id: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=200)
    timestamp: TimestampAnchor
    blocks: list[DocumentBlock] = Field(default_factory=list)


class MediaSource(BaseModel):
    source: str = Field(min_length=1)
    source_type: Literal["URL", "LOCAL_FILE"]
    local_media_reference: str | None = None
    platform: str | None = None


class Document(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    schema_version: int = 1
    document_id: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    source: MediaSource
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    media_duration_seconds: float | None = Field(default=None, ge=0)
    language: str | None = None
    models: dict[str, str | None] = Field(default_factory=dict)
    processing: dict[str, Any] = Field(default_factory=dict)
    sections: list[DocumentSection] = Field(default_factory=list)
    artifact_references: dict[str, str] = Field(default_factory=dict)
    edit_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity_and_order(self) -> Document:
        block_ids: list[str] = []
        previous = -1.0
        for section in self.sections:
            for block in section.blocks:
                block_ids.append(block.block_id)
                start = block.timestamp.start_seconds
                if start is not None:
                    if start + 1e-6 < previous:
                        raise ValueError("document blocks must be ordered by media time")
                    previous = start
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("document block identifiers must be unique")
        return self

    def all_blocks(self) -> list[DocumentBlock]:
        return [block for section in self.sections for block in section.blocks]

    def source_ids(self) -> list[str]:
        return [
            reference.source_id
            for block in self.all_blocks()
            for reference in block.source_references
        ]
