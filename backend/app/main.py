from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.gateways import seed_default_gateways
from app.workers.queue import queue_worker


settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Starting Video2Doc backend")
    with SessionLocal() as session:
        seed_default_gateways(session)
    await queue_worker.start()
    yield
    await queue_worker.stop()


app = FastAPI(
    title="Video2Doc API",
    version="2.3.3",
    description="Local faithful media-to-Obsidian application API.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix=settings.api_prefix)

frontend_directory = Path(
    os.getenv(
        "VIDEO2DOC_FRONTEND_DIR",
        str(Path(__file__).resolve().parents[2] / "frontend" / "dist"),
    )
)
frontend_assets = frontend_directory / "assets"
if frontend_assets.is_dir():
    app.mount("/assets", StaticFiles(directory=frontend_assets), name="frontend-assets")


@app.exception_handler(AppError)
async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(SQLAlchemyError)
async def handle_database_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("Database error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "database_error", "message": "A database operation failed"}},
    )


@app.get("/", include_in_schema=False, response_model=None)
def root() -> FileResponse | dict[str, str]:
    index = frontend_directory / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"service": "Video2Doc", "docs": "/docs", "health": f"{settings.api_prefix}/health"}
