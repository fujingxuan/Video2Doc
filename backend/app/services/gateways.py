from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, NotFoundError
from app.models import GatewayConfig, GatewayModel
from app.providers.gateways import discover_gateway_models, probe_gateway
from app.schemas.api import (
    GatewayCreate,
    GatewayHealthRead,
    GatewayModelCreate,
    GatewayModelRead,
    GatewayModelUpdate,
    GatewayRead,
    GatewayUpdate,
    is_sensitive_metadata_key,
)


class GatewayService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_gateways(self, gateway_type: str | None = None) -> list[GatewayRead]:
        query = select(GatewayConfig).order_by(GatewayConfig.gateway_type, GatewayConfig.name)
        if gateway_type:
            query = query.where(GatewayConfig.gateway_type == gateway_type)
        return [_gateway_read(item) for item in self.session.scalars(query)]

    def get_gateway(self, gateway_id: int) -> GatewayRead:
        return _gateway_read(self._gateway(gateway_id))

    def create_gateway(self, payload: GatewayCreate) -> GatewayRead:
        item = GatewayConfig(**payload.model_dump(mode="json"))
        item.name = item.name.strip()
        item.protocol = item.protocol.strip()
        item.base_url = item.base_url.strip().rstrip("/")
        self.session.add(item)
        self._commit_unique("A gateway with that name already exists")
        return _gateway_read(item)

    def update_gateway(self, gateway_id: int, payload: GatewayUpdate) -> GatewayRead:
        item = self._gateway(gateway_id)
        for key, value in payload.model_dump(exclude_unset=True, mode="json").items():
            if isinstance(value, str) and key in {"name", "protocol", "base_url", "secret_env_name"}:
                value = value.strip()
            setattr(item, key, value)
        self._commit_unique("A gateway with that name already exists")
        return _gateway_read(item)

    def delete_gateway(self, gateway_id: int) -> None:
        item = self._gateway(gateway_id)
        self.session.delete(item)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("Gateway or its models are referenced by a processing profile") from exc

    async def health(self, gateway_id: int) -> GatewayHealthRead:
        item = self._gateway(gateway_id)
        result = await probe_gateway(
            item,
            timeout_seconds=_numeric_gateway_setting(item, "timeout_seconds", 10.0, 1.0, 600.0),
        )
        item.health_status = result.status
        item.last_checked_at = result.checked_at
        for model in item.models:
            model.health_status = result.status
        self.session.commit()
        return GatewayHealthRead(
            gateway_id=item.id,
            status=result.status,
            detail=result.detail,
            checked_at=result.checked_at,
        )

    async def discover(self, gateway_id: int) -> list[GatewayModelRead]:
        gateway = self._gateway(gateway_id)
        discovered = await discover_gateway_models(
            gateway,
            timeout_seconds=_numeric_gateway_setting(
                gateway, "timeout_seconds", 15.0, 1.0, 600.0
            ),
        )
        existing = {item.model_name: item for item in gateway.models}
        for raw in discovered:
            model_name = raw["model_name"]
            item = existing.get(model_name)
            if item is None:
                item = GatewayModel(
                    gateway=gateway,
                    model_name=model_name,
                    display_name=raw["display_name"],
                )
                self.session.add(item)
            else:
                item.display_name = raw["display_name"]
            if raw.get("capabilities"):
                _apply_discovered_capabilities(item, raw["capabilities"])
        self.session.commit()
        self.session.refresh(gateway)
        return self.list_models(gateway_id=gateway_id)

    def list_models(
        self, *, gateway_id: int | None = None, gateway_type: str | None = None
    ) -> list[GatewayModelRead]:
        query = (
            select(GatewayModel)
            .join(GatewayConfig)
            .options(selectinload(GatewayModel.gateway))
            .order_by(GatewayConfig.name, GatewayModel.display_name)
        )
        if gateway_id is not None:
            query = query.where(GatewayModel.gateway_id == gateway_id)
        if gateway_type:
            query = query.where(GatewayConfig.gateway_type == gateway_type)
        return [_gateway_model_read(item) for item in self.session.scalars(query)]

    def get_model(self, model_id: int) -> GatewayModelRead:
        return _gateway_model_read(self._model(model_id))

    def create_model(self, payload: GatewayModelCreate) -> GatewayModelRead:
        self._gateway(payload.gateway_id)
        item = GatewayModel(**payload.model_dump())
        item.model_name = item.model_name.strip()
        item.display_name = item.display_name.strip()
        self.session.add(item)
        self._commit_unique("That gateway already contains this model")
        return _gateway_model_read(self._model(item.id))

    def update_model(self, model_id: int, payload: GatewayModelUpdate) -> GatewayModelRead:
        item = self._model(model_id)
        values = payload.model_dump(exclude_unset=True)
        if "gateway_id" in values:
            self._gateway(values["gateway_id"])
        for key, value in values.items():
            if isinstance(value, str) and key in {"model_name", "display_name"}:
                value = value.strip()
            setattr(item, key, value)
        self._commit_unique("That gateway already contains this model")
        return _gateway_model_read(self._model(model_id))

    def delete_model(self, model_id: int) -> None:
        item = self._model(model_id)
        self.session.delete(item)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("Gateway model is referenced by a processing profile") from exc

    def _gateway(self, gateway_id: int) -> GatewayConfig:
        item = self.session.scalar(
            select(GatewayConfig)
            .where(GatewayConfig.id == gateway_id)
            .options(selectinload(GatewayConfig.models))
        )
        if item is None:
            raise NotFoundError("Gateway", gateway_id)
        return item

    def _model(self, model_id: int) -> GatewayModel:
        item = self.session.scalar(
            select(GatewayModel)
            .where(GatewayModel.id == model_id)
            .options(selectinload(GatewayModel.gateway))
        )
        if item is None:
            raise NotFoundError("Gateway model", model_id)
        return item

    def _commit_unique(self, message: str) -> None:
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError(message) from exc


def _gateway_read(item: GatewayConfig) -> GatewayRead:
    return GatewayRead(
        id=item.id,
        name=item.name,
        gateway_type=item.gateway_type,
        protocol=item.protocol,
        base_url=item.base_url,
        secret_env_name=item.secret_env_name,
        secret_configured=bool(item.secret_env_name and os.getenv(item.secret_env_name)),
        enabled=item.enabled,
        health_status=item.health_status,
        last_checked_at=item.last_checked_at,
        settings=sanitize_gateway_metadata(item.settings or {}),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _gateway_model_read(item: GatewayModel) -> GatewayModelRead:
    values = {
        column.name: getattr(item, column.name)
        for column in item.__table__.columns
        if column.name != "gateway_id"
    }
    values["capabilities"] = sanitize_gateway_metadata(values.get("capabilities") or {})
    values["settings"] = sanitize_gateway_metadata(values.get("settings") or {})
    return GatewayModelRead(
        **values,
        gateway_id=item.gateway_id,
        gateway_name=item.gateway.name,
        gateway_type=item.gateway.gateway_type,
        gateway_base_url=item.gateway.base_url,
        protocol=item.gateway.protocol,
    )


def _apply_discovered_capabilities(item: GatewayModel, capabilities: dict[str, Any]) -> None:
    safe_capabilities = sanitize_gateway_metadata(capabilities)
    item.capabilities = safe_capabilities
    aliases = {
        "segment_timestamps": "supports_segment_timestamps",
        "word_timestamps": "supports_word_timestamps",
        "confidence": "supports_confidence",
        "language_detection": "supports_language_detection",
    }
    direct_fields = {
        "max_chunk_seconds",
        "max_alignment_seconds",
        "supports_native_long_audio",
        "supports_streaming",
        "supports_segment_timestamps",
        "supports_word_timestamps",
        "supports_confidence",
        "supports_language_detection",
        "timestamp_granularity",
        "languages",
    }
    boolean_fields = {
        "supports_native_long_audio",
        "supports_streaming",
        "supports_segment_timestamps",
        "supports_word_timestamps",
        "supports_confidence",
        "supports_language_detection",
    }
    numeric_fields = {"max_chunk_seconds", "max_alignment_seconds"}
    list_fields = {"timestamp_granularity", "languages"}
    for key, value in safe_capabilities.items():
        field = aliases.get(key, key)
        if field not in direct_fields:
            continue
        if field in boolean_fields and isinstance(value, bool):
            setattr(item, field, value)
        elif field in numeric_fields and isinstance(value, (int, float)) and value > 0:
            setattr(item, field, float(value))
        elif field in list_fields and isinstance(value, list) and all(
            isinstance(entry, str) for entry in value
        ):
            setattr(item, field, value)


def sanitize_gateway_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_gateway_metadata(item)
            for key, item in value.items()
            if not is_sensitive_metadata_key(str(key))
        }
    if isinstance(value, list):
        return [sanitize_gateway_metadata(item) for item in value]
    return value


def _numeric_gateway_setting(
    gateway: GatewayConfig,
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float((gateway.settings or {}).get(key, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def seed_default_gateways(session: Session) -> None:
    definitions: list[dict[str, Any]] = [
        {
            "name": "dashscope-fun-asr",
            "gateway_type": "ASR",
            "protocol": "dashscope-fun-asr-websocket",
            "base_url": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
            "secret_env_name": "DASHSCOPE_API_KEY",
            "settings": {
                "workspace_env_name": "DASHSCOPE_WORKSPACE_ID",
                "region": "cn-beijing",
                "timeout_seconds": 180,
                "max_retries": 1,
            },
            "models": [
                {
                    "model_name": "fun-asr-realtime-2026-02-28",
                    "display_name": "Fun-ASR Realtime 2026-02-28",
                    "supports_native_long_audio": True,
                    "supports_streaming": True,
                    "supports_segment_timestamps": True,
                    "supports_word_timestamps": True,
                    "supports_language_detection": True,
                    "timestamp_granularity": ["segment", "word"],
                    "languages": ["zh", "en", "ja"],
                    "capabilities": {"continuous_session": True},
                },
                {
                    "model_name": "fun-asr-realtime",
                    "display_name": "Fun-ASR Realtime (stable alias)",
                    "supports_native_long_audio": True,
                    "supports_streaming": True,
                    "supports_segment_timestamps": True,
                    "supports_word_timestamps": True,
                    "supports_language_detection": True,
                    "timestamp_granularity": ["segment", "word"],
                    "capabilities": {"continuous_session": True},
                },
            ],
        },
        {
            "name": "local-firered-asr",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:8101",
            "settings": {"timeout_seconds": 300, "max_retries": 1},
            "models": [
                {
                    "model_name": "FireRedTeam/FireRedASR2-AED",
                    "display_name": "FireRedASR2-AED",
                    "max_chunk_seconds": 55.0,
                    "supports_segment_timestamps": True,
                    "supports_word_timestamps": True,
                    "supports_confidence": True,
                    "timestamp_granularity": ["segment", "word"],
                    "languages": ["zh", "en", "zh-en"],
                    "capabilities": {"offline": True},
                }
            ],
        },
        {
            "name": "local-qwen-asr",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:8102",
            "settings": {"timeout_seconds": 900, "max_retries": 0},
            "models": [
                {
                    "model_name": "Qwen/Qwen3-ASR-1.7B",
                    "display_name": "Qwen3-ASR-1.7B + ForcedAligner",
                    "max_chunk_seconds": 180.0,
                    "max_alignment_seconds": 300.0,
                    "supports_segment_timestamps": True,
                    "supports_word_timestamps": True,
                    "supports_language_detection": True,
                    "timestamp_granularity": ["segment", "word"],
                    "languages": ["zh", "en", "yue", "dialects"],
                    "settings": {"aligner_model": "Qwen/Qwen3-ForcedAligner-0.6B"},
                    "capabilities": {"offline": True, "forced_alignment": True},
                }
            ],
        },
        {
            "name": "local-whisper",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:8103",
            "settings": {"timeout_seconds": 600, "max_retries": 1},
            "models": [
                {
                    "model_name": "large-v3",
                    "display_name": "Whisper large-v3 (quality)",
                    "max_chunk_seconds": 30.0,
                    "supports_segment_timestamps": True,
                    "supports_word_timestamps": True,
                    "supports_confidence": True,
                    "supports_language_detection": True,
                    "timestamp_granularity": ["segment", "word"],
                    "capabilities": {"vad_filter": True, "quality_priority": True},
                },
                {
                    "model_name": "large-v3-turbo",
                    "display_name": "Whisper large-v3-turbo (speed)",
                    "max_chunk_seconds": 30.0,
                    "supports_segment_timestamps": True,
                    "supports_word_timestamps": True,
                    "supports_confidence": True,
                    "supports_language_detection": True,
                    "timestamp_granularity": ["segment", "word"],
                    "capabilities": {"vad_filter": True, "speed_priority": True},
                },
            ],
        },
        {
            "name": "local-ocr-placeholder",
            "gateway_type": "OCR",
            "protocol": "video2doc-ocr-http-v1",
            "base_url": "http://127.0.0.1:8201",
            "enabled": False,
            "settings": {
                "deployment_status": "not_deployed",
                "timeout_seconds": 120,
                "max_retries": 1,
            },
            "models": [],
        },
        {
            "name": "local-paddleocr",
            "gateway_type": "OCR",
            "protocol": "video2doc-ocr-http-v1",
            "base_url": "http://127.0.0.1:8201",
            "enabled": True,
            "settings": {
                "deployment_status": "optional_local_runtime",
                "timeout_seconds": 180,
                "max_retries": 1,
                "runtime": "paddleocr",
            },
            "models": [
                {
                    "model_name": "PP-OCRv5_server",
                    "display_name": "PP-OCRv5 Server (Chinese/English)",
                    "supports_confidence": True,
                    "supports_language_detection": False,
                    "languages": ["ch", "en"],
                    "settings": {
                        "default_language": "ch",
                        "device": "cpu",
                    },
                    "capabilities": {
                        "local": True,
                        "text_blocks": True,
                        "bounding_boxes": True,
                        "polygons": True,
                        "reading_order": True,
                        "raw_response": True,
                    },
                }
            ],
        },
        {
            "name": "docker-paddleocr-vl",
            "gateway_type": "OCR",
            "protocol": "paddlex-layout-parsing-v1",
            "base_url": "http://127.0.0.1:8202",
            "enabled": False,
            "settings": {
                "deployment_status": "discovered_not_running",
                "timeout_seconds": 600,
                "max_retries": 1,
                "runtime": "docker-paddleocr-vl",
                "model_name": "PaddleOCR-VL-1.6-0.9B",
                "managed_lifecycle": False,
                "discovered_compose": r"D:\Tools\paddle ocr\compose.yaml",
                "discovered_vlm_base_url": "http://127.0.0.1:8118/v1",
            },
            "models": [
                {
                    "model_name": "PaddleOCR-VL-1.6-0.9B",
                    "display_name": "PaddleOCR-VL 1.6 (external Docker)",
                    "supports_confidence": False,
                    "supports_language_detection": False,
                    "languages": ["zh", "en"],
                    "settings": {
                        "device": "docker-gpu-vlm",
                        "managed_lifecycle": False,
                    },
                    "capabilities": {
                        "local": True,
                        "docker": True,
                        "layout_parsing": True,
                        "text_blocks": True,
                        "bounding_boxes": True,
                        "reading_order": True,
                        "markdown": True,
                        "raw_response": True,
                    },
                }
            ],
        },
    ]
    existing_gateways = {
        item.name: item
        for item in session.scalars(
            select(GatewayConfig).options(selectinload(GatewayConfig.models))
        )
    }
    now = datetime.now(timezone.utc)
    for definition in definitions:
        model_definitions = definition.pop("models")
        gateway = existing_gateways.get(definition["name"])
        if gateway is None:
            gateway = GatewayConfig(**definition, created_at=now, updated_at=now)
            gateway.models = [
                GatewayModel(**model, created_at=now, updated_at=now)
                for model in model_definitions
            ]
            session.add(gateway)
            continue
        gateway.settings = {
            **(definition.get("settings") or {}),
            **(gateway.settings or {}),
        }
        existing_models = {item.model_name: item for item in gateway.models}
        for model_definition in model_definitions:
            model = existing_models.get(model_definition["model_name"])
            if model is None:
                gateway.models.append(
                    GatewayModel(**model_definition, created_at=now, updated_at=now)
                )
            # Existing rows are administrator configuration, even when they began
            # as built-in defaults.  Startup seeding may add new catalog entries,
            # but must not silently reset edited limits, capabilities, or labels.
    session.commit()
