from __future__ import annotations

import time

from fastapi.testclient import TestClient


def wait_for_status(
    client: TestClient,
    task_id: int,
    expected: set[str],
    *,
    timeout: float = 10.0,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in expected:
            return last
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not reach {expected}; last state: {last}")
