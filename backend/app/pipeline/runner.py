from __future__ import annotations

from app.db.session import SessionLocal
from app.models import Task
from app.pipeline.real import run_real_pipeline
from app.pipeline.simulated import run_simulated_pipeline


async def run_pipeline(task_id: int) -> None:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        mode = task.snapshot.snapshot_json.get("pipeline", "SIMULATED") if task else None
    if mode == "REAL":
        await run_real_pipeline(task_id)
    else:
        await run_simulated_pipeline(task_id)
