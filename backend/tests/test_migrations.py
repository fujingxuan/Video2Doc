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

    assert revision == ("0014_pipeline_lanes",)
    assert {
        "frames_manifest_path",
        "ocr_timeline_path",
        "fusion_timeline_path",
        "document_path",
        "markdown_path",
        "export_path",
        "deleted_at",
        "trash_expires_at",
        "review_submission_path",
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
    assert {"document_path", "markdown_path", "export_path", "deleted_at", "trash_expires_at"}.isdisjoint(task_columns)
    assert {"document_artifacts", "text_edit_runs", "markdown_exports"}.isdisjoint(
        tables
    )


def test_trash_migration_preserves_existing_task_and_snapshot(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / 'existing.db'
    environment = os.environ.copy()
    environment['VIDEO2DOC_DATABASE_URL'] = f'sqlite:///{database_path.as_posix()}'
    def migrate(direction: str, revision: str) -> None:
        result = subprocess.run([sys.executable, '-m', 'alembic', direction, revision],
                                cwd=backend_dir, env=environment, capture_output=True,
                                text=True, encoding='utf-8', timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    migrate('upgrade', '0011_full_article_text_edit')
    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO tasks (id, source, source_type, title, status, current_stage, progress, cancel_requested, attempt, created_at, updated_at) VALUES (1, 'original.mp4', 'LOCAL_FILE', 'Existing history', 'COMPLETED', 'COMPLETED', 100, 0, 2, '2026-09-01', '2026-09-01')")
        connection.execute("INSERT INTO task_config_snapshots (task_id, schema_version, snapshot_json, created_at) VALUES (1, 6, ?, '2026-09-01')", ('{"unchanged": true}',))
        before = connection.execute('SELECT * FROM tasks').fetchone()
    migrate('upgrade', 'head')
    with sqlite3.connect(database_path) as connection:
        after = connection.execute('SELECT * FROM tasks').fetchone()
        assert after[:len(before)] == before and after[len(before):] == (None, None, None, "main")
        assert connection.execute('SELECT snapshot_json FROM task_config_snapshots').fetchone() == ('{"unchanged": true}',)
        assert 'ix_tasks_history_page' in {row[1] for row in connection.execute('PRAGMA index_list(tasks)')}
    migrate('downgrade', '0011_full_article_text_edit')
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT * FROM tasks').fetchone() == before
        assert connection.execute('SELECT snapshot_json FROM task_config_snapshots').fetchone() == ('{"unchanged": true}',)
