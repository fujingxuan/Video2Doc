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
    / "test_document_reuse_acceptance.py"
)


def test_real_sample_artifacts_resume_through_obsidian_export(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    if not request.config.getoption("--run-real-reuse"):
        pytest.skip("real sample artifact reuse acceptance is opt-in")
    configured_output = str(
        request.config.getoption("--real-reuse-output-root") or ""
    ).strip()
    output_root = (
        Path(configured_output)
        if configured_output
        else tmp_path / "real-document-reuse"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ACCEPTANCE_SCRIPT),
            "--output-root",
            str(output_root),
        ],
        cwd=REPOSITORY_ROOT / "backend",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=360,
        check=False,
    )
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    assert (output_root / "acceptance-summary.json").is_file()
