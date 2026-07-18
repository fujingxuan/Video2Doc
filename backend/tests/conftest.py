from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATA = BACKEND_DIR / f"data/test-runtime-{uuid.uuid4().hex}"
TEST_DATABASE = TEST_DATA / "test.db"
TEST_TASKS = TEST_DATA / "tasks"
TEST_INBOX = TEST_DATA / "inbox"

os.environ["VIDEO2DOC_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["VIDEO2DOC_DATA_DIR"] = str(TEST_TASKS)
os.environ["VIDEO2DOC_INBOX_DIR"] = str(TEST_INBOX)
os.environ["VIDEO2DOC_SIMULATION_STEP_SECONDS"] = "0.03"
os.environ["VIDEO2DOC_LOG_LEVEL"] = "WARNING"

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def clean_database() -> None:
    TEST_DATA.mkdir(parents=True)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()
    shutil.rmtree(TEST_DATA, ignore_errors=True)


@pytest.fixture()
def client(clean_database: None) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
