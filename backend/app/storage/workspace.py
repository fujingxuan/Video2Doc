from __future__ import annotations

import json
import re
import shutil
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    root: Path
    source: Path
    media: Path
    extract: Path
    audio: Path
    chunks: Path
    asr: Path
    asr_raw: Path
    frames: Path
    frame_candidates: Path
    frame_normalized: Path
    ocr: Path
    ocr_raw: Path
    ocr_normalized: Path
    fusion: Path
    metadata: Path
    logs: Path
    intermediate: Path
    output: Path

    @classmethod
    def from_root(cls, root: Path) -> "WorkspacePaths":
        return cls(
            root=root,
            source=root / "00_source",
            media=root / "00_source/media",
            extract=root / "01_extract",
            audio=root / "01_extract/audio",
            chunks=root / "01_extract/chunks",
            asr=root / "02_asr",
            asr_raw=root / "02_asr/provider_raw",
            frames=root / "01_extract/frames",
            frame_candidates=root / "01_extract/frames/candidates",
            frame_normalized=root / "01_extract/frames/normalized",
            ocr=root / "03_ocr",
            ocr_raw=root / "03_ocr/provider_raw",
            ocr_normalized=root / "03_ocr/normalized",
            fusion=root / "04_fusion",
            metadata=root / "00_source/metadata",
            logs=root / "logs",
            intermediate=root / "04_fusion/intermediate",
            output=root / "05_output",
        )

    def create(self) -> None:
        for path in (
            self.source,
            self.media,
            self.extract,
            self.audio,
            self.chunks,
            self.asr,
            self.asr_raw,
            self.frames,
            self.frame_candidates,
            self.frame_normalized,
            self.ocr,
            self.ocr_raw,
            self.ocr_normalized,
            self.fusion,
            self.metadata,
            self.logs,
            self.intermediate,
            self.output,
        ):
            path.mkdir(parents=True, exist_ok=True)


def safe_filename(value: str, *, fallback: str = "untitled", max_length: int = 120) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value).strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(". ") or fallback


def create_workspace(data_root: Path, *, task_id: int, title: str, snapshot: dict[str, Any]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    workspace = data_root / f"{stamp}_{safe_filename(title)}_{task_id:06d}"
    paths = WorkspacePaths.from_root(workspace)
    paths.create()
    ocr_dir = workspace / "03_ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    if not snapshot["processing"]["ocr_enabled"]:
        (ocr_dir / "DISABLED.txt").write_text(
            "OCR is disabled for this task snapshot. No OCR processing was performed.\n",
            encoding="utf-8",
        )
    write_json(workspace / "task_config.json", snapshot)
    write_json(workspace / "task_state.json", {"task_id": task_id, "status": "QUEUED", "progress": 0})
    mode = snapshot.get("pipeline", "SIMULATED")
    (workspace / "logs/task.log").write_text(
        f"{mode} Pipeline task created.\n", encoding="utf-8"
    )
    return workspace


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


def append_task_log(workspace: Path, message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with (workspace / "logs/task.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{timestamp} {message}\n")


def write_task_state(workspace: Path, payload: dict[str, Any]) -> None:
    write_json(workspace / "task_state.json", payload)


def write_simulated_markdown(
    *,
    workspace: Path,
    inbox_root: Path,
    title: str,
    source: str,
    source_type: str,
    snapshot: dict[str, Any],
) -> tuple[Path, Path]:
    display_title = re.sub(r"[\r\n]+", " ", title).strip() or "Untitled"
    safe_title = safe_filename(display_title)
    model_config = snapshot.get("models", {})
    processing = snapshot.get("processing", {})
    source_url = source if source_type == "URL" else ""
    yaml_title = json.dumps(display_title, ensure_ascii=False)
    yaml_source_url = json.dumps(source_url, ensure_ascii=False)
    markdown = f'''---
title: {yaml_title}
source_url: {yaml_source_url}
source_type: {source_type}
platform:
author:
channel:
published_date:
duration:
language:
video2doc_version: v0.1-bootstrap
models:
  asr: "{model_config.get('asr', '') or ''}"
  ocr: "{model_config.get('ocr', '') or ''}"
  text_processing: "{model_config.get('text_processing', '') or ''}"
processing:
  ocr_enabled: {str(bool(processing.get('ocr_enabled'))).lower()}
  remove_fillers: {str(bool(processing.get('remove_fillers', True))).lower()}
  timestamp_enabled: {str(bool(processing.get('keep_timestamps', True))).lower()}
status: generated
tags:
  - video2doc
---

# {display_title}

## 来源信息

输入来源：{source}

## 模拟 Pipeline 说明

此文件由 Video2Doc 第一轮工程初始化的**模拟 Pipeline**生成，仅用于验证任务队列、配置快照、持久化状态与输出路径。

本轮未下载或解析媒体，未调用 FFmpeg、ASR、OCR、OneAPI、Ollama 或任何远程模型，也未生成或伪造媒体正文。

---

## 原始资料

任务工作区：`{workspace}`

任务配置：`{workspace / 'task_config.json'}`
'''
    workspace_output = workspace / "05_output" / f"{safe_title}.md"
    workspace_output.write_text(markdown, encoding="utf-8")
    inbox_root.mkdir(parents=True, exist_ok=True)
    inbox_output = unique_path(inbox_root / f"{safe_title}.md")
    shutil.copy2(workspace_output, inbox_output)
    return workspace_output, inbox_output


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def list_artifacts(workspace: Path) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
        try:
            relative = path.relative_to(workspace).as_posix()
            stat = path.stat()
        except OSError:
            continue
        artifacts.append({"path": relative, "size": stat.st_size})
    return artifacts


def write_artifact_index(workspace: Path) -> Path:
    index_path = workspace / "artifacts.json"
    artifacts = [item for item in list_artifacts(workspace) if item["path"] != "artifacts.json"]
    write_json(index_path, {"schema_version": 1, "artifacts": artifacts})
    return index_path
