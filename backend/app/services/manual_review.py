from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.documents.editor import build_full_article_document
from app.documents.faithfulness import validate_document_faithfulness
from app.documents.ir import Document
from app.models import Task
from app.services.serializers import task_read
from app.services.tasks import TaskService, WorkerNotifier
from app.storage.workspace import write_json

MANUAL_REVIEW_PROMPT = (
    "请忠实校对以下完整语音转写稿，修正错别字、标点和断句，并合理分段。"
    "保留全部有效内容、数字、公式、代码及专有名词；不要摘要、扩写或添加未经原文支持的事实。"
    "只返回校对后的完整 Markdown 正文，不要添加说明或包裹整篇正文的代码围栏。"
)


class ManualDraftInput(BaseModel):
    attempt: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    edited_text: str = Field(default="", max_length=2_000_000)

    @field_validator("edited_text")
    @classmethod
    def normalize_line_endings(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("修改稿不能包含二进制 NUL 字符")
        return value.replace("\r\n", "\n").replace("\r", "\n")


class ManualSubmitInput(ManualDraftInput):
    action: Literal["original", "edited"]
    acknowledge_warnings: bool = False


def review_seed_path(workspace: Path, attempt: int) -> Path:
    return workspace / "05_output" / "manual_review" / f"attempt_{attempt:04d}.json"


class ManualReviewService:
    def __init__(self, session: Session, worker: WorkerNotifier | None = None) -> None:
        self.session = session
        self.tasks = TaskService(session, worker)
        self.worker = worker

    def _read(self, task_id: int):
        task = self.tasks.repo.get(task_id)
        if task is None or task.status != "WAITING_REVIEW":
            raise ConflictError("任务当前不在待人工校对状态，请刷新任务详情")
        path = self.tasks.artifact_path(
            task_id, f"05_output/manual_review/attempt_{task.attempt:04d}.json"
        )
        try:
            content = path.read_bytes()
            seed = json.loads(content)
            document = Document.model_validate(seed["document"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConflictError("初稿记录损坏，请从工作区检查原始转写文件") from exc
        return task, path, seed, document, hashlib.sha256(content).hexdigest()

    def get(self, task_id: int) -> dict:
        task, path, _seed, document, digest = self._read(task_id)
        original = "\n\n".join(
            block.display_text
            for block in document.all_blocks()
            if block.block_type != "image" and block.display_text
        )
        draft = ""
        draft_path = path.with_suffix(".draft.json")
        if draft_path.is_file():
            try:
                draft = str(json.loads(draft_path.read_text(encoding="utf-8"))["edited_text"])
                draft = draft.replace("\r\n", "\n").replace("\r", "\n")
            except (OSError, ValueError, KeyError, TypeError):
                raise ConflictError("已保存的修改稿无法读取，请从工作区恢复后重试")
        return {
            "task_id": task.id,
            "title": task.title,
            "attempt": task.attempt,
            "source_sha256": digest,
            "original_text": original,
            "edited_text": draft,
            "source_count": len(set(document.source_ids())),
            "prompt": MANUAL_REVIEW_PROMPT,
        }

    def _lock(self, task: Task, payload: ManualDraftInput, digest: str) -> None:
        if payload.attempt != task.attempt or payload.source_sha256 != digest:
            raise ConflictError("初稿版本已变化，请刷新页面后重新核对")
        result = self.session.execute(
            update(Task)
            .where(
                Task.id == task.id,
                Task.status == "WAITING_REVIEW",
                Task.attempt == payload.attempt,
                Task.deleted_at.is_(None),
            )
            .values(current_stage="MANUAL_REVIEW")
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise ConflictError("任务状态已变化，请刷新页面；不会重复提交")

    def save(self, task_id: int, payload: ManualDraftInput) -> dict:
        task, path, _seed, _document, digest = self._read(task_id)
        self._lock(task, payload, digest)
        write_json(path.with_suffix(".draft.json"), payload.model_dump())
        self.session.commit()
        return {"saved": True}

    def submit(self, task_id: int, payload: ManualSubmitInput) -> dict:
        task, path, seed, document, digest = self._read(task_id)
        if payload.action == "edited" and not payload.edited_text.strip():
            raise ConflictError("请先粘贴或导入修改后的完整正文")
        candidate = build_full_article_document(
            document,
            article_markdown=payload.edited_text if payload.action == "edited" else None,
        )
        report = validate_document_faithfulness(candidate)
        warnings = [asdict(issue) for issue in report.issues]
        self._lock(task, payload, digest)
        if warnings and not payload.acknowledge_warnings:
            self.session.rollback()
            return {"requires_confirmation": True, "warnings": warnings, "task": None}
        candidate.edit_metadata = {
            **document.edit_metadata,
            "provider": "user-manual",
            "model": None,
            "manual_review": True,
            "accepted": report.accepted,
            "fallback_used": False,
            "reason": f"user_confirmed_{payload.action}",
            "user_confirmed_warnings": bool(warnings),
            "faithfulness": asdict(report),
        }
        submission_path = path.parent / f"submission_{uuid4().hex}.json"
        write_json(
            submission_path,
            {
                **seed,
                "document": candidate.model_dump(mode="json"),
                "decision": payload.model_dump(),
                "faithfulness": asdict(report),
            },
        )
        task.review_submission_path = str(submission_path)
        task.status = "QUEUED"
        task.current_stage = "QUEUED"
        task.cancel_requested = False
        task.completed_at = None
        self.session.commit()
        if self.worker:
            self.worker.notify()
        return {"requires_confirmation": False, "warnings": warnings, "task": task_read(task)}
