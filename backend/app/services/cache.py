from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Task
from app.storage.workspace import append_task_log, write_artifact_index


TERMINAL_TASK_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
CACHE_DIRECTORIES = (
    "00_source/media",
    "01_extract/audio",
    "01_extract/chunks",
    "01_extract/frames/candidates",
    "04_fusion/intermediate",
)
CACHE_LOG_PATTERNS = (
    "*.stdout.log",
    "*.stderr.log",
)
TEMP_PATTERNS = ("*.part", ".*.tmp")


class TaskCacheService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def clear(self, task_ids: list[int] | None = None) -> dict[str, Any]:
        statement = select(Task).order_by(Task.id)
        if task_ids is not None:
            statement = statement.where(Task.id.in_(set(task_ids)))
        tasks = list(self.session.scalars(statement))
        found_ids = {task.id for task in tasks}
        missing_ids = sorted(set(task_ids or []) - found_ids)
        cleared: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = [
            {"task_id": task_id, "reason": "任务不存在"} for task_id in missing_ids
        ]
        total_files = 0
        total_bytes = 0
        for task in tasks:
            if task.status not in TERMINAL_TASK_STATUSES:
                skipped.append({"task_id": task.id, "reason": "任务仍在队列或处理中"})
                continue
            if not task.workspace_path:
                skipped.append({"task_id": task.id, "reason": "任务没有工作区"})
                continue
            result = clear_workspace_cache(Path(task.workspace_path))
            result["task_id"] = task.id
            cleared.append(result)
            total_files += int(result["files_removed"])
            total_bytes += int(result["bytes_freed"])
        return {
            "tasks_cleared": len(cleared),
            "files_removed": total_files,
            "bytes_freed": total_bytes,
            "cleared": cleared,
            "skipped": skipped,
        }


def clear_workspace_cache(workspace: Path) -> dict[str, int]:
    try:
        root = workspace.resolve(strict=True)
    except OSError:
        return {"files_removed": 0, "bytes_freed": 0}
    if not root.is_dir():
        return {"files_removed": 0, "bytes_freed": 0}

    files_removed = 0
    bytes_freed = 0
    for relative in CACHE_DIRECTORIES:
        target = (root / relative).resolve(strict=False)
        if not target.is_relative_to(root) or not target.exists():
            continue
        count, size = _tree_metrics(target)
        if target.is_symlink():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        files_removed += count
        bytes_freed += size

    logs = root / "logs"
    for pattern in CACHE_LOG_PATTERNS:
        for path in logs.glob(pattern) if logs.is_dir() else ():
            count, size = _tree_metrics(path)
            path.unlink(missing_ok=True)
            files_removed += count
            bytes_freed += size
    for pattern in TEMP_PATTERNS:
        for path in root.rglob(pattern):
            if not path.is_file() or not path.resolve().is_relative_to(root):
                continue
            count, size = _tree_metrics(path)
            path.unlink(missing_ok=True)
            files_removed += count
            bytes_freed += size

    append_task_log(
        root,
        f"Cleared regenerable task cache: files={files_removed}, bytes={bytes_freed}",
    )
    write_artifact_index(root)
    return {"files_removed": files_removed, "bytes_freed": bytes_freed}


def _tree_metrics(path: Path) -> tuple[int, int]:
    if path.is_file() or path.is_symlink():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 0, 0
    count = 0
    size = 0
    for directory, _subdirectories, files in os.walk(path, followlinks=False):
        for name in files:
            candidate = Path(directory) / name
            try:
                size += candidate.stat().st_size
                count += 1
            except OSError:
                continue
    return count, size
