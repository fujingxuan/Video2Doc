from __future__ import annotations

from typing import Any


ASR_MODEL_STAGES = {"ASR_TRANSCRIBE", "ASR_PROCESSING"}
OCR_MODEL_STAGES = {"OCR_RECOGNIZE", "OCR_PROCESSING"}
TEXT_MODEL_STAGES = {"TEXT_EDIT", "TEXT_PROCESSING"}
SUBTITLE_STAGES = {"SUBTITLE_IMPORT", "SUBTITLE_NORMALIZE", "SUBTITLE_VALIDATE"}


def stage_model_usage(stage: str, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    if stage in SUBTITLE_STAGES:
        subtitle = snapshot.get("subtitle")
        protocol = (
            subtitle.get("provider")
            if isinstance(subtitle, dict)
            else "yt-dlp-subtitle"
        )
        return [
            {
                "capability": "TEXT_SOURCE",
                "mode": "URL_SUBTITLE",
                "gateway_name": None,
                "protocol": protocol,
                "model_name": None,
                "display_name": "从视频链接抓取字幕（未调用 ASR 模型）",
            }
        ]
    if stage in ASR_MODEL_STAGES:
        return _gateway_usage("ASR", snapshot, "asr")
    if stage in OCR_MODEL_STAGES:
        return _gateway_usage("OCR", snapshot, "ocr")
    if stage in TEXT_MODEL_STAGES:
        config = snapshot.get("text_processing")
        if not isinstance(config, dict) or not config.get("enabled"):
            return []
        model_name = str(config.get("model") or "").strip()
        if not model_name:
            return []
        return [
            {
                "capability": "TEXT_PROCESSING",
                "mode": "GATEWAY_MODEL",
                "gateway_name": "OneAPI",
                "protocol": config.get("provider"),
                "model_name": model_name,
                "display_name": model_name,
            }
        ]
    return []


def _gateway_usage(
    capability: str,
    snapshot: dict[str, Any],
    key: str,
) -> list[dict[str, Any]]:
    gateways = snapshot.get("gateways")
    gateway = gateways.get(key) if isinstance(gateways, dict) else None
    if not isinstance(gateway, dict):
        return []
    model_name = str(gateway.get("model_name") or "").strip()
    if not model_name:
        return []
    return [
        {
            "capability": capability,
            "mode": "GATEWAY_MODEL",
            "gateway_name": gateway.get("gateway_name"),
            "protocol": gateway.get("protocol"),
            "model_name": model_name,
            "display_name": gateway.get("display_name") or model_name,
        }
    ]
