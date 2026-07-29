from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Task, TaskStageRun
from app.models.entities import utcnow
from app.pipeline.model_usage import stage_model_usage
from app.storage.workspace import (
    append_task_log,
    write_simulated_markdown,
    write_task_state,
)


class TaskCancelled(Exception):
    pass


class SimulatedPipelineFailure(Exception):
    pass


BASE_STAGES = [
    ("PREPARING", 8),
    ("EXTRACTING", 22),
    ("ASR_PROCESSING", 45),
    ("TEXT_PROCESSING", 78),
    ("GENERATING_OUTPUT", 96),
]


async def run_simulated_pipeline(task_id: int) -> None:
    try:
        stages = _stages_for_task(task_id)
        previous_progress = 0
        for stage, target_progress in stages:
            await _run_stage(task_id, stage, previous_progress, target_progress)
            previous_progress = target_progress
        _complete_task(task_id)
    except TaskCancelled:
        _finish_with_status(task_id, "CANCELLED", "user_cancelled", "Task cancelled by user")
    except SimulatedPipelineFailure as exc:
        _finish_with_status(task_id, "FAILED", "simulated_failure", str(exc))
    except Exception as exc:
        _finish_with_status(task_id, "FAILED", "pipeline_error", f"Unexpected Pipeline error: {exc}")
        raise


def _stages_for_task(task_id: int) -> list[tuple[str, int]]:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            return []
        stages = list(BASE_STAGES)
        if task.snapshot.snapshot_json["processing"].get("text_source") == "SUBTITLE":
            stages = [
                ("SUBTITLE_IMPORT", target) if name == "ASR_PROCESSING" else (name, target)
                for name, target in stages
            ]
        if task.snapshot.snapshot_json["processing"]["ocr_enabled"]:
            stages.insert(3, ("OCR_PROCESSING", 62))
        return stages


async def _run_stage(task_id: int, stage_name: str, start: int, target: int) -> None:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            raise SimulatedPipelineFailure("Task disappeared while processing")
        if task.cancel_requested:
            raise TaskCancelled
        stage_run = TaskStageRun(
            task_id=task.id,
            attempt=task.attempt,
            stage=stage_name,
            status="PROCESSING",
            progress=start,
            started_at=utcnow(),
            model_usage=stage_model_usage(stage_name, task.snapshot.snapshot_json),
        )
        session.add(stage_run)
        task.current_stage = stage_name
        session.commit()
        append_task_log(Path(task.workspace_path), f"Started simulated stage {stage_name}")

    increments = 5
    for index in range(1, increments + 1):
        await asyncio.sleep(get_settings().simulation_step_seconds)
        progress = start + round((target - start) * index / increments)
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if not task or task.cancel_requested:
                raise TaskCancelled
            task.progress = progress
            stage_run = session.scalar(
                select(TaskStageRun).where(
                    TaskStageRun.task_id == task_id,
                    TaskStageRun.attempt == task.attempt,
                    TaskStageRun.stage == stage_name,
                )
            )
            if stage_run:
                stage_run.progress = progress
            session.commit()
            write_task_state(
                Path(task.workspace_path),
                {"task_id": task.id, "status": task.status, "stage": stage_name, "progress": progress},
            )

    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            raise SimulatedPipelineFailure("Task disappeared while processing")
        failure_mode = task.snapshot.snapshot_json["simulation"]["failure_mode"]
        if stage_name == "TEXT_PROCESSING" and (
            failure_mode == "always" or (failure_mode == "once" and task.attempt == 1)
        ):
            stage_run = session.scalar(
                select(TaskStageRun).where(
                    TaskStageRun.task_id == task_id,
                    TaskStageRun.attempt == task.attempt,
                    TaskStageRun.stage == stage_name,
                )
            )
            if stage_run:
                stage_run.status = "FAILED"
                stage_run.error_message = "Intentional simulated failure for queue verification"
                stage_run.finished_at = utcnow()
            session.commit()
            raise SimulatedPipelineFailure("Intentional simulated failure for queue verification")
        stage_run = session.scalar(
            select(TaskStageRun).where(
                TaskStageRun.task_id == task_id,
                TaskStageRun.attempt == task.attempt,
                TaskStageRun.stage == stage_name,
            )
        )
        if stage_run:
            stage_run.status = "COMPLETED"
            stage_run.progress = target
            stage_run.finished_at = utcnow()
        session.commit()
        append_task_log(Path(task.workspace_path), f"Completed simulated stage {stage_name}")


def _complete_task(task_id: int) -> None:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            return
        snapshot = task.snapshot.snapshot_json
        workspace = Path(task.workspace_path)
        title = task.title
        source = task.source
        source_type = task.source_type
        inbox_root = Path(snapshot["paths"]["inbox"])
        completed_at = utcnow()
        result = session.execute(
            update(Task)
            .where(
                Task.id == task_id,
                Task.status == "PROCESSING",
                Task.cancel_requested.is_(False),
            )
            .values(
                status="COMPLETED",
                current_stage="COMPLETED",
                progress=100,
                completed_at=completed_at,
            )
        )
        session.commit()
        if result.rowcount != 1:
            _finish_with_status(task_id, "CANCELLED", "user_cancelled", "Task cancelled by user")
            return
        _workspace_output, inbox_output = write_simulated_markdown(
            workspace=workspace,
            inbox_root=inbox_root,
            title=title,
            source=source,
            source_type=source_type,
            snapshot=snapshot,
        )
        task = session.get(Task, task_id)
        if not task:
            return
        task.output_path = str(inbox_output)
        session.commit()
        append_task_log(workspace, f"Simulated Pipeline completed: {inbox_output}")
        write_task_state(
            workspace,
            {"task_id": task.id, "status": "COMPLETED", "stage": "COMPLETED", "progress": 100},
        )


def _finish_with_status(task_id: int, status: str, code: str, message: str) -> None:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if not task:
            return
        current_stage = session.scalar(
            select(TaskStageRun).where(
                TaskStageRun.task_id == task_id,
                TaskStageRun.attempt == task.attempt,
                TaskStageRun.status == "PROCESSING",
            )
        )
        if current_stage:
            current_stage.status = status
            current_stage.error_message = message
            current_stage.finished_at = utcnow()
        task.status = status
        task.current_stage = status
        task.error_code = code
        task.error_message = message
        task.completed_at = utcnow()
        task.cancel_requested = False
        session.commit()
        append_task_log(Path(task.workspace_path), f"Simulated Pipeline {status.lower()}: {message}")
        write_task_state(
            Path(task.workspace_path),
            {"task_id": task.id, "status": status, "stage": status, "progress": task.progress},
        )
