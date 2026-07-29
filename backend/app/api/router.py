from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.schemas.api import (
    Capability,
    GatewayCreate,
    GatewayHealthRead,
    GatewayModelCreate,
    GatewayModelRead,
    GatewayModelUpdate,
    GatewayRead,
    GatewayType,
    GatewayUpdate,
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
from app.services.gateways import GatewayService
from app.services.settings import SettingsService
from app.services.system import (
    media_tool_status,
    select_media_files,
    select_output_directory,
    select_subtitle_file,
)
from app.services.tasks import TaskService
from app.workers.queue import queue_worker


router = APIRouter()


@router.get("/gateways", response_model=list[GatewayRead], tags=["gateways"])
def list_gateways(
    gateway_type: GatewayType | None = Query(default=None), session: Session = Depends(get_db)
) -> list[GatewayRead]:
    return GatewayService(session).list_gateways(gateway_type.value if gateway_type else None)


@router.post(
    "/gateways", response_model=GatewayRead, status_code=status.HTTP_201_CREATED, tags=["gateways"]
)
def create_gateway(payload: GatewayCreate, session: Session = Depends(get_db)) -> GatewayRead:
    return GatewayService(session).create_gateway(payload)


@router.get("/gateways/{gateway_id}", response_model=GatewayRead, tags=["gateways"])
def get_gateway(gateway_id: int, session: Session = Depends(get_db)) -> GatewayRead:
    return GatewayService(session).get_gateway(gateway_id)


@router.put("/gateways/{gateway_id}", response_model=GatewayRead, tags=["gateways"])
def update_gateway(
    gateway_id: int, payload: GatewayUpdate, session: Session = Depends(get_db)
) -> GatewayRead:
    return GatewayService(session).update_gateway(gateway_id, payload)


@router.delete("/gateways/{gateway_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["gateways"])
def delete_gateway(gateway_id: int, session: Session = Depends(get_db)) -> Response:
    GatewayService(session).delete_gateway(gateway_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/gateways/{gateway_id}/health", response_model=GatewayHealthRead, tags=["gateways"]
)
async def check_gateway_health(
    gateway_id: int, session: Session = Depends(get_db)
) -> GatewayHealthRead:
    return await GatewayService(session).health(gateway_id)


@router.post(
    "/gateways/{gateway_id}/discover", response_model=list[GatewayModelRead], tags=["gateways"]
)
async def discover_models(
    gateway_id: int, session: Session = Depends(get_db)
) -> list[GatewayModelRead]:
    return await GatewayService(session).discover(gateway_id)


@router.get("/gateway-models", response_model=list[GatewayModelRead], tags=["gateways"])
def list_gateway_models(
    gateway_id: int | None = Query(default=None),
    gateway_type: GatewayType | None = Query(default=None),
    session: Session = Depends(get_db),
) -> list[GatewayModelRead]:
    return GatewayService(session).list_models(
        gateway_id=gateway_id, gateway_type=gateway_type.value if gateway_type else None
    )


@router.post(
    "/gateway-models",
    response_model=GatewayModelRead,
    status_code=status.HTTP_201_CREATED,
    tags=["gateways"],
)
def create_gateway_model(
    payload: GatewayModelCreate, session: Session = Depends(get_db)
) -> GatewayModelRead:
    return GatewayService(session).create_model(payload)


@router.put("/gateway-models/{model_id}", response_model=GatewayModelRead, tags=["gateways"])
def update_gateway_model(
    model_id: int, payload: GatewayModelUpdate, session: Session = Depends(get_db)
) -> GatewayModelRead:
    return GatewayService(session).update_model(model_id, payload)


@router.delete(
    "/gateway-models/{model_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["gateways"]
)
def delete_gateway_model(model_id: int, session: Session = Depends(get_db)) -> Response:
    GatewayService(session).delete_model(model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/health", response_model=HealthRead, tags=["system"])
def health(session: Session = Depends(get_db)) -> HealthRead:
    session.execute(text("SELECT 1"))
    return HealthRead(
        status="ok",
        service="video2doc-backend",
        version="2.0.0",
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


@router.get("/system/status", response_model=dict, tags=["system"])
def system_status(session: Session = Depends(get_db)) -> dict:
    settings = SettingsService(session).entity()
    return media_tool_status(settings)


@router.post("/system/select-media", response_model=dict, tags=["system"])
def select_media() -> dict[str, list[str]]:
    return {"paths": select_media_files()}


@router.post("/system/select-subtitle", response_model=dict, tags=["system"])
def select_subtitle() -> dict[str, str | None]:
    return {"path": select_subtitle_file()}


@router.post("/system/select-output-directory", response_model=dict, tags=["system"])
def select_export_directory() -> dict[str, str | None]:
    return {"path": select_output_directory()}


@router.post("/system/shutdown", response_model=dict, tags=["system"])
def shutdown_application(request: Request) -> dict[str, str]:
    callback = getattr(request.app.state, "request_shutdown", None)
    if not callable(callback):
        from app.core.errors import ConflictError

        raise ConflictError("This development server is not managed by the Windows launcher")
    callback()
    return {"status": "shutting_down"}


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


@router.get("/tasks/{task_id}/frames", response_model=dict, tags=["tasks"])
def get_task_frames(task_id: int, session: Session = Depends(get_db)) -> dict:
    return TaskService(session).frames(task_id)


@router.get("/tasks/{task_id}/ocr", response_model=dict, tags=["tasks"])
def get_task_ocr(task_id: int, session: Session = Depends(get_db)) -> dict:
    return TaskService(session).ocr(task_id)


@router.get("/tasks/{task_id}/timeline", response_model=dict, tags=["tasks"])
def get_task_timeline(task_id: int, session: Session = Depends(get_db)) -> dict:
    return TaskService(session).fusion(task_id)


@router.get("/tasks/{task_id}/document", response_model=dict, tags=["tasks"])
def get_task_document(task_id: int, session: Session = Depends(get_db)) -> dict:
    return TaskService(session).document(task_id)


@router.get("/tasks/{task_id}/markdown", tags=["tasks"])
def get_task_markdown(
    task_id: int,
    session: Session = Depends(get_db),
) -> PlainTextResponse:
    return PlainTextResponse(
        TaskService(session).markdown(task_id),
        media_type="text/markdown; charset=utf-8",
    )


@router.post("/tasks/{task_id}/open/{target}", response_model=dict, tags=["tasks"])
def open_task_target(
    task_id: int,
    target: str,
    session: Session = Depends(get_db),
) -> dict[str, str]:
    return TaskService(session).open_target(task_id, target)


@router.get("/tasks/{task_id}/artifacts/{artifact_path:path}", tags=["tasks"])
def get_task_artifact(
    task_id: int, artifact_path: str, session: Session = Depends(get_db)
) -> FileResponse:
    path = TaskService(session).artifact_path(task_id, artifact_path)
    return FileResponse(path, filename=path.name)


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
