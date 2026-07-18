from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.schemas.api import (
    Capability,
    HealthRead,
    ModelCreate,
    ModelRead,
    ModelUpdate,
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    QueueStatus,
    SettingsRead,
    SettingsUpdate,
    TaskBatchCreate,
    TaskDetail,
    TaskRead,
)
from app.services.catalog import CatalogService
from app.services.settings import SettingsService
from app.services.tasks import TaskService
from app.workers.queue import queue_worker


router = APIRouter()


@router.get("/health", response_model=HealthRead, tags=["system"])
def health(session: Session = Depends(get_db)) -> HealthRead:
    session.execute(text("SELECT 1"))
    return HealthRead(
        status="ok",
        service="video2doc-backend",
        version="0.2.0",
        database="ok",
        queue_worker="running" if queue_worker.running else "stopped",
    )


@router.get("/models/capabilities", response_model=list[Capability], tags=["models"])
def list_capabilities() -> list[Capability]:
    return list(Capability)


@router.get("/models", response_model=list[ModelRead], tags=["models"])
def list_models(
    capability: Capability | None = Query(default=None), session: Session = Depends(get_db)
) -> list[ModelRead]:
    return CatalogService(session).list_models(capability.value if capability else None)


@router.post("/models", response_model=ModelRead, status_code=status.HTTP_201_CREATED, tags=["models"])
def create_model(payload: ModelCreate, session: Session = Depends(get_db)) -> ModelRead:
    return CatalogService(session).create_model(payload)


@router.get("/models/{model_id}", response_model=ModelRead, tags=["models"])
def get_model(model_id: int, session: Session = Depends(get_db)) -> ModelRead:
    return CatalogService(session).get_model(model_id)


@router.put("/models/{model_id}", response_model=ModelRead, tags=["models"])
def update_model(model_id: int, payload: ModelUpdate, session: Session = Depends(get_db)) -> ModelRead:
    return CatalogService(session).update_model(model_id, payload)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["models"])
def delete_model(model_id: int, session: Session = Depends(get_db)) -> Response:
    CatalogService(session).delete_model(model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/profiles", response_model=list[ProfileRead], tags=["profiles"])
def list_profiles(session: Session = Depends(get_db)) -> list[ProfileRead]:
    return CatalogService(session).list_profiles()


@router.post(
    "/profiles", response_model=ProfileRead, status_code=status.HTTP_201_CREATED, tags=["profiles"]
)
def create_profile(payload: ProfileCreate, session: Session = Depends(get_db)) -> ProfileRead:
    return CatalogService(session).create_profile(payload)


@router.get("/profiles/{profile_id}", response_model=ProfileRead, tags=["profiles"])
def get_profile(profile_id: int, session: Session = Depends(get_db)) -> ProfileRead:
    return CatalogService(session).get_profile(profile_id)


@router.put("/profiles/{profile_id}", response_model=ProfileRead, tags=["profiles"])
def update_profile(
    profile_id: int, payload: ProfileUpdate, session: Session = Depends(get_db)
) -> ProfileRead:
    return CatalogService(session).update_profile(profile_id, payload)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["profiles"])
def delete_profile(profile_id: int, session: Session = Depends(get_db)) -> Response:
    CatalogService(session).delete_profile(profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/settings", response_model=SettingsRead, tags=["settings"])
def get_settings(session: Session = Depends(get_db)) -> SettingsRead:
    return SettingsService(session).get()


@router.put("/settings", response_model=SettingsRead, tags=["settings"])
def update_settings(payload: SettingsUpdate, session: Session = Depends(get_db)) -> SettingsRead:
    return SettingsService(session).update(payload)


@router.post("/tasks", response_model=list[TaskRead], status_code=status.HTTP_201_CREATED, tags=["tasks"])
def create_tasks(payload: TaskBatchCreate, session: Session = Depends(get_db)) -> list[TaskRead]:
    return TaskService(session, queue_worker).create_batch(payload)


@router.get("/tasks", response_model=list[TaskRead], tags=["tasks"])
def list_tasks(status_filter: str | None = Query(default=None, alias="status"), session: Session = Depends(get_db)) -> list[TaskRead]:
    return TaskService(session).list(status_filter)


@router.get("/tasks/{task_id}", response_model=TaskDetail, tags=["tasks"])
def get_task(task_id: int, session: Session = Depends(get_db)) -> TaskDetail:
    return TaskService(session).get(task_id)


@router.get("/tasks/{task_id}/snapshot", response_model=dict, tags=["tasks"])
def get_task_snapshot(task_id: int, session: Session = Depends(get_db)) -> dict:
    return TaskService(session).snapshot(task_id)


@router.get("/tasks/{task_id}/media", response_model=dict, tags=["tasks"])
def get_task_media(task_id: int, session: Session = Depends(get_db)) -> dict:
    return TaskService(session).media(task_id)


@router.get("/tasks/{task_id}/transcript", response_model=dict, tags=["tasks"])
def get_task_transcript(task_id: int, session: Session = Depends(get_db)) -> dict:
    return TaskService(session).transcript(task_id)


@router.get("/tasks/{task_id}/artifacts", response_model=list[dict], tags=["tasks"])
def get_task_artifacts(task_id: int, session: Session = Depends(get_db)) -> list[dict]:
    return TaskService(session).artifacts(task_id)


@router.post("/tasks/{task_id}/cancel", response_model=TaskRead, tags=["tasks"])
def cancel_task(task_id: int, session: Session = Depends(get_db)) -> TaskRead:
    return TaskService(session, queue_worker).cancel(task_id)


@router.post("/tasks/{task_id}/retry", response_model=TaskRead, tags=["tasks"])
def retry_task(task_id: int, session: Session = Depends(get_db)) -> TaskRead:
    return TaskService(session, queue_worker).retry(task_id)


@router.get("/queue", response_model=QueueStatus, tags=["queue"])
def queue_status(session: Session = Depends(get_db)) -> QueueStatus:
    return TaskService(session).queue_status()


async def event_stream() -> AsyncIterator[str]:
    previous = ""
    while True:
        with SessionLocal() as session:
            payload = {
                "queue": TaskService(session).queue_status().model_dump(),
                "tasks": [item.model_dump(mode="json") for item in TaskService(session).list()],
            }
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        if serialized != previous:
            yield f"event: state\ndata: {serialized}\n\n"
            previous = serialized
        else:
            yield ": keep-alive\n\n"
        await asyncio.sleep(1.0)


@router.get("/events", tags=["queue"])
def events() -> StreamingResponse:
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
