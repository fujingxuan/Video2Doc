"""One preparation lane and one inference/export lane, persisted in SQLite."""
from __future__ import annotations

import asyncio

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.core.errors import PipelineCancelled, PipelineInterrupted
from app.db.session import SessionLocal
from app.models import Task
from app.providers.process import process_registry


class ProcessingAdmission:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()

    def reset(self) -> None:
        self.lock = asyncio.Lock()

    async def acquire(self, task_id: int, cancelled) -> None:
        while True:
            if process_registry.shutting_down:
                raise PipelineInterrupted(stage="AUDIO_READY")
            if cancelled():
                raise PipelineCancelled(stage="AUDIO_READY")
            try:
                await asyncio.wait_for(self.lock.acquire(), timeout=0.1)
            except TimeoutError:
                continue
            try:
                with SessionLocal() as session:
                    result = session.execute(update(Task).where(
                        Task.id == task_id, Task.status == "PROCESSING",
                        Task.cancel_requested.is_(False),
                    ).values(processing_lane="main"))
                    if result.rowcount != 1:
                        raise PipelineCancelled(stage="AUDIO_READY")
                    session.commit()
                return
            except IntegrityError:
                self.lock.release()
                await asyncio.sleep(0.1)
            except BaseException:
                self.lock.release()
                raise

    def release(self) -> None:
        self.lock.release()


processing_admission = ProcessingAdmission()
