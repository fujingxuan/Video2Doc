from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_alembic_upgrades_and_downgrades_document_export_head(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.db"
    environment = os.environ.copy()
    environment["VIDEO2DOC_DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        stage_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(task_stage_runs)"
            ).fetchall()
        }
        model_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(model_configs)").fetchall()
        }
        gateway_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(gateway_configs)").fetchall()
        }
        setting_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(app_settings)"
            ).fetchall()
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert revision == ("0011_full_article_text_edit",)
    assert {
        "frames_manifest_path",
        "ocr_timeline_path",
        "fusion_timeline_path",
        "document_path",
        "markdown_path",
        "export_path",
    }.issubset(task_columns)
    assert {"document_artifacts", "text_edit_runs", "markdown_exports"}.issubset(tables)
    assert "model_usage" in stage_columns
    assert {"provider", "base_url", "secret_env_name", "secret_ciphertext"}.issubset(
        model_columns
    )
    assert "secret_ciphertext" in gateway_columns
    assert "auto_clear_task_cache" in setting_columns

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0006_ocr_fusion_artifacts"],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert revision == ("0006_ocr_fusion_artifacts",)
    assert {"document_path", "markdown_path", "export_path"}.isdisjoint(task_columns)
    assert {"document_artifacts", "text_edit_runs", "markdown_exports"}.isdisjoint(
        tables
    )
