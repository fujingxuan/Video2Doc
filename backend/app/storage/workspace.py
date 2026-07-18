from __future__ import annotations

import json
import re
import shutil
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
    for relative in ("00_source", "01_extract", "02_asr", "04_fusion", "05_output", "logs"):
        (workspace / relative).mkdir(parents=True, exist_ok=True)
    ocr_dir = workspace / "03_ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    if not snapshot["processing"]["ocr_enabled"]:
        (ocr_dir / "DISABLED.txt").write_text(
            "OCR is disabled for this task snapshot. No OCR processing was performed.\n",
            encoding="utf-8",
        )
    write_json(workspace / "task_config.json", snapshot)
    write_json(workspace / "task_state.json", {"task_id": task_id, "status": "QUEUED", "progress": 0})
    (workspace / "logs/task.log").write_text("Simulated Pipeline task created.\n", encoding="utf-8")
    return workspace


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


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
    created_at = datetime.now(timezone.utc).isoformat()
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
created_at: {created_at}
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
