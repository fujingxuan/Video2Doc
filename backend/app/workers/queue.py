from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models import AppSetting, Task
from app.pipeline.runner import run_pipeline
from app.providers.process import process_registry
from app.services.cache import TaskCacheService
from app.storage.workspace import write_task_state


logger = logging.getLogger(__name__)


class SerialQueueWorker:
    max_concurrency = 1

    def __init__(self) -> None:
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._runner is not None and not self._runner.done()

    async def start(self) -> None:
        if self.running:
            return
        # A FastAPI app can be started again in the same process (tests, local
        # reloads, or an embedded host). asyncio primitives are loop-bound once
        # awaited, so every lifecycle needs fresh events.
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self.recover_interrupted()
        process_registry.begin_lifecycle()
        self._runner = asyncio.create_task(self._run(), name="video2doc-serial-worker")
        self.notify()
        logger.info("Serial queue worker started with max_concurrency=1")

    async def stop(self) -> None:
        self._stop_event.set()
        self.notify()
        await process_registry.shutdown_all()
        if self._runner:
            await self._runner
        self._runner = None
        logger.info("Serial queue worker stopped")

    def notify(self) -> None:
        self._wake_event.set()

    def recover_interrupted(self) -> int:
        with SessionLocal() as session:
            # A crash during an explicitly requested permanent deletion may
            # leave some files already removed. Keep the tombstone retryable,
            # never silently restore or continue a destructive operation.
            deleting = list(session.scalars(select(Task).where(
                Task.status == "DELETING", Task.deleted_at.is_not(None),
            )))
            for task in deleting:
                task.status = "FAILED"
                task.current_stage = "FAILED"
                task.error_code = "delete_interrupted"
                task.error_message = "永久删除被中断，部分文件可能已删除；请检查回收站后重试"
            tasks = list(session.scalars(select(Task).where(Task.status == "PROCESSING")))
            for task in tasks:
                task.status = "INTERRUPTED"
                task.current_stage = "INTERRUPTED"
                task.error_code = "backend_restart"
                task.error_message = "Task was interrupted by a backend stop or restart; retry is available"
                task.cancel_requested = False
                task.completed_at = datetime.now(timezone.utc)
                for stage in task.stages:
                    if stage.status == "PROCESSING":
                        stage.status = "INTERRUPTED"
                        stage.error_message = task.error_message
                        stage.finished_at = task.completed_at
            session.commit()
            if tasks:
                logger.warning("Marked %d previously processing task(s) as interrupted", len(tasks))
            return len(tasks)

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            task_id = self._claim_next_task()
            if task_id is not None:
                try:
                    await run_pipeline(task_id)
                except Exception:
                    logger.exception("Unexpected failure escaped Pipeline for task %s", task_id)
                    self._fail_escaped_task(task_id)
                try:
                    self._auto_clear_completed_task_cache(task_id)
                except Exception:
                    # Cache cleanup is optional and must never stop the queue
                    # or change an already completed task into a failed task.
                    logger.exception("Automatic cache cleanup failed for task %s", task_id)
                continue
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=0.5)
            except TimeoutError:
                pass

    def _fail_escaped_task(self, task_id: int) -> None:
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if task is None or task.status != "PROCESSING":
                return
            task.status = "FAILED"
            task.current_stage = "FAILED"
            task.error_code = "worker_pipeline_error"
            task.error_message = "任务执行异常，已保留已生成的产物；可重试此任务"
            task.cancel_requested = False
            task.completed_at = datetime.now(timezone.utc)
            for stage in task.stages:
                if stage.attempt == task.attempt and stage.status == "PROCESSING":
                    stage.status = "FAILED"
                    stage.error_message = task.error_message
                    stage.finished_at = task.completed_at
            session.commit()
            workspace = task.workspace_path
            state = {
                "task_id": task.id, "status": task.status, "stage": task.current_stage,
                "progress": task.progress, "attempt": task.attempt,
                "error_code": task.error_code, "message": task.error_message,
            }
        if workspace:
            try:
                write_task_state(Path(workspace), state)
            except OSError:
                # In particular, a full/unwritable workspace may have caused
                # the original failure. SQLite remains the queue's fact source.
                logger.warning("Could not write failed task state for task %s", task_id)

    def _claim_next_task(self) -> int | None:
        with SessionLocal() as session:
            running = session.scalar(select(Task.id).where(Task.status == "PROCESSING").limit(1))
            if running is not None:
                return None
            task_id = session.scalar(
                select(Task.id)
                .where(Task.status == "QUEUED", Task.deleted_at.is_(None))
                .order_by(Task.created_at.asc(), Task.id.asc())
                .limit(1)
            )
            if task_id is None:
                return None
            try:
                claimed = session.execute(
                    update(Task).where(Task.id == task_id, Task.status == "QUEUED",
                                       Task.deleted_at.is_(None))
                    .values(status="PROCESSING", current_stage="PREPARING",
                            started_at=datetime.now(timezone.utc), completed_at=None)
                )
                if claimed.rowcount != 1:
                    session.rollback()
                    return None
                session.commit()
            except IntegrityError:
                session.rollback()
                logger.info("Another worker claimed a task before this worker committed")
                return None
            return task_id

    def _auto_clear_completed_task_cache(self, task_id: int) -> None:
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            settings = session.get(AppSetting, 1)
            if (
                task is None
                or task.status != "COMPLETED"
                or settings is None
                or not settings.auto_clear_task_cache
            ):
                return
            result = TaskCacheService(session).clear([task_id])
            logger.info(
                "Automatically cleared task %s cache: %s files, %s bytes",
                task_id,
                result["files_removed"],
                result["bytes_freed"],
            )


queue_worker = SerialQueueWorker()
