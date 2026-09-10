from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_SCRIPT = (
    REPOSITORY_ROOT
    / "scripts"
    / "model_runtime"
    / "test_packaged_real_pipeline.py"
)


def test_packaged_acceptance_continues_after_manual_review_gate():
    import json
    import runpy

    import httpx

    submitted = []

    def handler(request):
        if request.url.path.endswith("/submit"):
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"requires_confirmation": False})
        if request.url.path.endswith("/manual-review"):
            return httpx.Response(200, json={"attempt": 1, "source_sha256": "a" * 64})
        return httpx.Response(200, json={
            "status": "COMPLETED" if submitted else "WAITING_REVIEW",
            "current_stage": "MANUAL_REVIEW", "progress": 94,
        })

    namespace = runpy.run_path(str(ACCEPTANCE_SCRIPT))
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        task = namespace["_wait_task"](client, 7, 1)
    assert task["status"] == "COMPLETED"
    assert submitted == [{"action": "original", "attempt": 1, "source_sha256": "a" * 64}]


def test_packaged_application_runs_fresh_real_pipeline(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    if not request.config.getoption("--run-packaged-real"):
        pytest.skip("packaged fresh real inference acceptance is opt-in")
    configured_output = str(
        request.config.getoption("--packaged-real-output-root") or ""
    ).strip()
    output_root = (
        Path(configured_output)
        if configured_output
        else tmp_path / "packaged-real"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ACCEPTANCE_SCRIPT),
            "--output-root",
            str(output_root),
            *(
                ["--resume-existing"]
                if request.config.getoption("--packaged-real-resume")
                else []
            ),
        ],
        cwd=REPOSITORY_ROOT / "backend",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=4200,
        check=False,
    )
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert (output_root / "fresh-acceptance-summary.json").is_file()
