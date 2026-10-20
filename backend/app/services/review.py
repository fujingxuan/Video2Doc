from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.documents.ir import Document
from app.services.tasks import TaskService
from app.storage.workspace import read_json


class TaskReviewService:
    """Read the saved source and the actual output; never invoke a model."""

    def __init__(self, session: Session) -> None:
        self.tasks = TaskService(session)

    def get(self, task_id: int) -> dict[str, Any]:
        task = self.tasks.repo.get(task_id)
        if task is None:
            raise NotFoundError("Task", task_id)
        if task.status in {"QUEUED", "PROCESSING"}:
            raise ConflictError("任务仍在执行，请在文本整理结束后查看已保存的结果")
        if not task.document_path:
            raise NotFoundError("Current attempt document", task_id)
        path = self.tasks.artifact_path(task_id, "05_output/document.json")
        if Path(task.document_path).resolve(strict=False) != path:
            raise ConflictError("当前文档不在任务记录的标准产物位置，请从工作区检查")
        try:
            payload = read_json(path)
            document = Document.model_validate(payload)
        except (ValidationError, UnicodeError) as exc:
            raise ConflictError("文档产物无法读取，仍可从任务工作区查看原始文件") from exc
        blocks = [block for block in document.all_blocks() if block.block_type != "image"]
        original = "\n\n".join(block.original_text for block in blocks if block.original_text)
        edited = "\n\n".join(block.display_text for block in blocks if block.display_text)
        # A unified line diff stays deterministic, keeps the complete text and
        # avoids an expensive character-by-character matcher on long lectures.
        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                edited.splitlines(),
                fromfile="original",
                tofile="output",
                lineterm="",
            )
        )
        metadata = document.edit_metadata or {}
        return {
            "task_id": task.id,
            "title": task.title,
            "attempt": task.attempt,
            "original_text": original,
            "edited_text": edited,
            "diff": diff,
            "changed": original != edited,
            "original_characters": len(original),
            "edited_characters": len(edited),
            "source_count": len(set(document.source_ids())),
            "accepted": metadata.get("accepted"),
            "manual_review": bool(metadata.get("manual_review")),
            "fallback_used": bool(metadata.get("fallback_used", False)),
            "model": metadata.get("model"),
            "reason": metadata.get("reason"),
            "prompt_version": metadata.get("prompt_version"),
        }
