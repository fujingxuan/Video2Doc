from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Task, TaskStageRun
from app.pipeline.simulated import run_simulated_pipeline


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
        self._stop_event.clear()
        self.recover_interrupted()
        self._runner = asyncio.create_task(self._run(), name="video2doc-serial-worker")
        self.notify()
        logger.info("Serial queue worker started with max_concurrency=1")

    async def stop(self) -> None:
        self._stop_event.set()
        self.notify()
        if self._runner:
            await self._runner
        self._runner = None
        logger.info("Serial queue worker stopped")

    def notify(self) -> None:
        self._wake_event.set()

    def recover_interrupted(self) -> int:
        with SessionLocal() as session:
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
                    await run_simulated_pipeline(task_id)
                except Exception:
                    logger.exception("Unexpected failure escaped simulated Pipeline for task %s", task_id)
                continue
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=0.5)
            except TimeoutError:
                pass

    def _claim_next_task(self) -> int | None:
        with SessionLocal() as session:
            running = session.scalar(select(Task.id).where(Task.status == "PROCESSING").limit(1))
            if running is not None:
                return None
            item = session.scalar(
                select(Task)
                .where(Task.status == "QUEUED")
                .order_by(Task.created_at.asc(), Task.id.asc())
                .limit(1)
            )
            if item is None:
                return None
            item.status = "PROCESSING"
            item.current_stage = "PREPARING"
            item.started_at = datetime.now(timezone.utc)
            item.completed_at = None
            session.commit()
            return item.id


queue_worker = SerialQueueWorker()
