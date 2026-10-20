from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.errors import PipelineCancelled, PipelineInterrupted
from app.db.base import Base
from app.models import Task, TaskConfigSnapshot
from app.pipeline.admission import processing_admission
from app.providers.process import process_registry
from app.workers.queue import SerialQueueWorker


@pytest.fixture
def lane_db(monkeypatch):
    from app.workers import queue
    from app.pipeline import admission

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(queue, "SessionLocal", factory)
    monkeypatch.setattr(admission, "SessionLocal", factory)
    processing_admission.reset()
    yield factory
    engine.dispose()


@pytest.mark.asyncio
async def test_prefetch_overlaps_but_inference_is_exclusive(lane_db):
    with lane_db() as session:
        tasks = [Task(source=str(i), source_type="LOCAL_FILE", title=str(i),
                      snapshot=TaskConfigSnapshot(snapshot_json={"pipeline": "REAL"}))
                 for i in range(3)]
        session.add_all(tasks)
        session.commit()
        ids = [task.id for task in tasks]
    worker = SerialQueueWorker()
    assert worker._claim_next_task() == ids[0]
    assert worker._claim_next_task() is None
    await processing_admission.acquire(ids[0], lambda: False)
    assert worker._claim_next_task() == ids[1]
    assert worker._claim_next_task() is None
    waiter = asyncio.create_task(processing_admission.acquire(ids[1], lambda: False))
    try:
        await asyncio.sleep(0.15)
        assert not waiter.done()
        with lane_db() as session:
            lanes = list(session.scalars(select(Task.processing_lane).where(Task.status == "PROCESSING")))
            assert sorted(lanes) == ["audio", "main"]
            session.get(Task, ids[0]).status = "WAITING_REVIEW"
            session.commit()
        await asyncio.sleep(0.15)
        assert not waiter.done()  # Gateway cleanup must finish before next inference.
        processing_admission.release()
        await asyncio.wait_for(waiter, 1)
        assert worker._claim_next_task() == ids[2]
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        if processing_admission.lock.locked():
            processing_admission.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown", [False, True])
async def test_waiting_for_inference_can_cancel_or_shutdown(lane_db, shutdown):
    await processing_admission.lock.acquire()
    try:
        if shutdown:
            await process_registry.shutdown_all()
        with pytest.raises(PipelineInterrupted if shutdown else PipelineCancelled):
            await processing_admission.acquire(-1, lambda: not shutdown)
    finally:
        processing_admission.release()
        process_registry.begin_lifecycle()


def test_database_allows_two_lanes_but_not_two_transcribers(lane_db):
    with lane_db() as session:
        tasks = [Task(source="lane-db", source_type="LOCAL_FILE", title="lane-db",
                      status="PROCESSING", processing_lane=lane) for lane in ("main", "audio")]
        session.add_all(tasks)
        session.commit()
        tasks[1].processing_lane = "main"
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert sorted(session.scalars(select(Task.processing_lane))) == ["audio", "main"]


@pytest.mark.asyncio
async def test_worker_runs_next_download_during_previous_inference(lane_db, monkeypatch):
    from app.workers import queue
    with lane_db() as session:
        tasks = [Task(source=str(i), source_type="LOCAL_FILE", title=str(i),
                      snapshot=TaskConfigSnapshot(snapshot_json={"pipeline": "REAL"}))
                 for i in range(3)]
        session.add_all(tasks)
        session.commit()
        ids = [task.id for task in tasks]
    events = []
    inference_count = 0

    async def pipeline(task_id):
        nonlocal inference_count
        events.append((task_id, "download_start"))
        await asyncio.sleep(0.05)
        events.append((task_id, "download_end"))
        await processing_admission.acquire(task_id, lambda: False)
        try:
            inference_count += 1
            assert inference_count == 1
            events.append((task_id, "asr_start"))
            await asyncio.sleep(0.3)
            events.append((task_id, "asr_end"))
            with lane_db() as session:
                session.get(Task, task_id).status = "WAITING_REVIEW"
                session.commit()
        finally:
            inference_count -= 1
            processing_admission.release()

    monkeypatch.setattr(queue, "run_pipeline", pipeline)
    worker = SerialQueueWorker()
    await worker.start()
    try:
        async with asyncio.timeout(5):
            while (ids[-1], "asr_end") not in events:
                await asyncio.sleep(0.02)
    finally:
        await worker.stop()
    assert events.index((ids[0], "asr_start")) < events.index((ids[1], "download_start"))
    assert events.index((ids[1], "download_end")) < events.index((ids[0], "asr_end"))
    assert events.index((ids[0], "asr_end")) < events.index((ids[1], "asr_start"))
