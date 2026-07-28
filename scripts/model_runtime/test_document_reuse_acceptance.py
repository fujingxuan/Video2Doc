from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
SOURCE_DATABASE = BACKEND_ROOT / "data" / "video2doc.db"
SOURCE_TASK_ID = 2
SOURCE_WORKSPACE = (
    BACKEND_ROOT
    / "data"
    / "tasks"
    / "20260727_034706_sample OCR retry reuse acceptance_000002"
)
SOURCE_MEDIA = Path(r"D:\Video2Doc-Test\sample.mp4")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume real sample ASR/OCR artifacts through document export"
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"Acceptance output directory is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    runtime_workspace = output_root / "task-000002"
    runtime_database = output_root / "video2doc.db"
    vault_path = output_root / "vault"

    source_hash_before = _sha256(SOURCE_MEDIA)
    _copy_reusable_artifacts(SOURCE_WORKSPACE, runtime_workspace)
    _prepare_database(
        SOURCE_DATABASE,
        runtime_database,
        source_workspace=SOURCE_WORKSPACE,
        runtime_workspace=runtime_workspace,
        vault_path=vault_path,
    )

    os.environ["VIDEO2DOC_DATABASE_URL"] = (
        f"sqlite:///{runtime_database.as_posix()}"
    )
    os.environ["VIDEO2DOC_DATA_DIR"] = str(output_root / "tasks")
    os.environ["VIDEO2DOC_INBOX_DIR"] = str(vault_path / "Inbox")
    os.environ["VIDEO2DOC_LOG_LEVEL"] = "WARNING"

    sys.path.insert(0, str(BACKEND_ROOT))
    from app.main import app

    transitions: list[dict[str, Any]] = []
    started = time.monotonic()
    with TestClient(app) as client:
        response = client.post(f"/api/v1/tasks/{SOURCE_TASK_ID}/retry")
        response.raise_for_status()
        last_state: tuple[str, str | None, int] | None = None
        deadline = time.monotonic() + args.timeout_seconds
        while time.monotonic() < deadline:
            task_response = client.get(f"/api/v1/tasks/{SOURCE_TASK_ID}")
            task_response.raise_for_status()
            task = task_response.json()
            state = (
                str(task["status"]),
                task.get("current_stage"),
                int(task["progress"]),
            )
            if state != last_state:
                transitions.append(
                    {
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "status": state[0],
                        "stage": state[1],
                        "progress": state[2],
                    }
                )
                last_state = state
            if state[0] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Resumed task did not reach a terminal state")

        if task["status"] != "COMPLETED":
            raise RuntimeError(
                f"Resumed task failed: {task.get('error_code')}: "
                f"{task.get('error_message')}"
            )
        document_response = client.get(
            f"/api/v1/tasks/{SOURCE_TASK_ID}/document"
        )
        markdown_response = client.get(
            f"/api/v1/tasks/{SOURCE_TASK_ID}/markdown"
        )
        artifacts_response = client.get(
            f"/api/v1/tasks/{SOURCE_TASK_ID}/artifacts"
        )
        document_response.raise_for_status()
        markdown_response.raise_for_status()
        artifacts_response.raise_for_status()

    document = document_response.json()
    markdown = markdown_response.text
    export_path = Path(task["export_path"]).resolve()
    markdown_path = Path(task["markdown_path"]).resolve()
    audit = _audit_output(
        document=document,
        markdown=markdown,
        markdown_path=markdown_path,
        export_path=export_path,
    )
    source_hash_after = _sha256(SOURCE_MEDIA)
    if source_hash_after != source_hash_before:
        raise RuntimeError("Source sample hash changed during acceptance")

    with sqlite3.connect(runtime_database) as connection:
        connection.row_factory = sqlite3.Row
        attempt = int(
            connection.execute(
                "SELECT attempt FROM tasks WHERE id = ?", (SOURCE_TASK_ID,)
            ).fetchone()["attempt"]
        )
        stage_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT stage, status, message, progress, started_at, finished_at
                FROM task_stage_runs
                WHERE task_id = ? AND attempt = ?
                ORDER BY id
                """,
                (SOURCE_TASK_ID, attempt),
            )
        ]
        export_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT target_type, status, markdown_path,
                       image_count, sidecar_count, error_message
                FROM markdown_exports
                WHERE task_id = ? AND attempt = ?
                ORDER BY id
                """,
                (SOURCE_TASK_ID, attempt),
            )
        ]

    report = {
        "status": "passed",
        "acceptance_kind": "real_upstream_artifact_retry_reuse",
        "new_model_inference_performed": False,
        "source_media": str(SOURCE_MEDIA),
        "source_media_bytes": SOURCE_MEDIA.stat().st_size,
        "source_sha256_before": source_hash_before,
        "source_sha256_after": source_hash_after,
        "source_task_id": SOURCE_TASK_ID,
        "source_workspace": str(SOURCE_WORKSPACE),
        "runtime_root": str(output_root),
        "attempt": attempt,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "transitions": transitions,
        "stage_runs": stage_rows,
        "exports": export_rows,
        "document": {
            "document_id": document.get("document_id"),
            "section_count": len(document.get("sections") or []),
            "block_count": audit["block_count"],
            "source_reference_count": audit["source_reference_count"],
            "asr_reference_count": audit["asr_reference_count"],
            "ocr_reference_count": audit["ocr_reference_count"],
        },
        "markdown": {
            "workspace_path": str(markdown_path),
            "export_path": str(export_path),
            "character_count": len(markdown),
            "timestamp_count": audit["timestamp_count"],
            "image_count": audit["image_count"],
            "frontmatter_keys": audit["frontmatter_keys"],
        },
        "artifact_index_count": len(artifacts_response.json()),
    }
    report_path = output_root / "acceptance-summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"ACCEPTANCE_REPORT={report_path}")
    return 0


def _copy_reusable_artifacts(source: Path, destination: Path) -> None:
    required_files = (
        "00_source/metadata/media_acquisition.json",
        "00_source/metadata/media_info.json",
        "00_source/metadata/audio_media_info.json",
        "01_extract/audio/normalized.wav",
    )
    required_directories = (
        "01_extract/chunks",
        "01_extract/frames",
        "02_asr/provider_raw",
        "03_ocr/provider_raw",
        "03_ocr/normalized/frames",
    )
    destination.mkdir(parents=True, exist_ok=True)
    for relative in required_files:
        source_path = source / relative
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination_path)
    for relative in required_directories:
        source_directory = source / relative
        if not source_directory.is_dir():
            raise FileNotFoundError(source_directory)
        for source_path in source_directory.rglob("*"):
            if source_path.is_file():
                destination_path = destination / source_path.relative_to(source)
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, destination_path)


def _prepare_database(
    source_database: Path,
    runtime_database: Path,
    *,
    source_workspace: Path,
    runtime_workspace: Path,
    vault_path: Path,
) -> None:
    source = sqlite3.connect(
        f"file:{source_database.as_posix()}?mode=ro",
        uri=True,
    )
    target = sqlite3.connect(runtime_database)
    source.backup(target)
    source.close()
    target.row_factory = sqlite3.Row
    task = dict(
        target.execute(
            "SELECT * FROM tasks WHERE id = ?", (SOURCE_TASK_ID,)
        ).fetchone()
    )
    path_columns = [
        row[1]
        for row in target.execute("PRAGMA table_info(tasks)")
        if row[1] == "workspace_path" or row[1].endswith("_path")
    ]
    updates = {
        column: task[column].replace(
            str(source_workspace), str(runtime_workspace)
        )
        for column in path_columns
        if isinstance(task.get(column), str)
    }
    assignments = ", ".join(f"{column} = ?" for column in updates)
    target.execute(
        f"UPDATE tasks SET {assignments} WHERE id = ?",
        [*updates.values(), SOURCE_TASK_ID],
    )
    # Work only on the isolated database copy. Mark the copied completed task
    # as an interrupted handoff so the public retry API exercises the same
    # recovery path used after a real shutdown.
    target.execute(
        """
        UPDATE tasks
        SET status = 'INTERRUPTED',
            current_stage = 'INTERRUPTED',
            error_code = 'acceptance_resume',
            error_message = 'Isolated acceptance copy prepared for retry'
        WHERE id = ?
        """,
        (SOURCE_TASK_ID,),
    )
    snapshot = target.execute(
        """
        SELECT id, snapshot_json
        FROM task_config_snapshots
        WHERE task_id = ?
        """,
        (SOURCE_TASK_ID,),
    ).fetchone()
    payload = json.loads(snapshot["snapshot_json"])
    payload.setdefault("paths", {})["inbox"] = str(vault_path / "Inbox")
    payload["paths"]["data"] = str(runtime_workspace.parent / "tasks")
    target.execute(
        "UPDATE task_config_snapshots SET snapshot_json = ? WHERE id = ?",
        (json.dumps(payload, ensure_ascii=False), snapshot["id"]),
    )
    target.commit()
    target.close()


def _audit_output(
    *,
    document: dict[str, Any],
    markdown: str,
    markdown_path: Path,
    export_path: Path,
) -> dict[str, Any]:
    if not markdown.strip() or not markdown.startswith("---\n"):
        raise RuntimeError("Markdown is empty or lacks YAML frontmatter")
    frontmatter_end = markdown.find("\n---\n", 4)
    if frontmatter_end < 0:
        raise RuntimeError("Markdown frontmatter is not terminated")
    frontmatter = yaml.safe_load(markdown[4:frontmatter_end])
    if not isinstance(frontmatter, dict):
        raise RuntimeError("Markdown frontmatter is not a mapping")
    if str(BACKEND_ROOT) in markdown:
        raise RuntimeError("Markdown leaked a backend workspace absolute path")
    if not markdown_path.is_file() or not export_path.is_file():
        raise RuntimeError("Workspace or exported Markdown is missing")

    blocks = [
        block
        for section in document.get("sections") or []
        for block in section.get("blocks") or []
    ]
    references = [
        reference
        for block in blocks
        for reference in block.get("source_references") or []
    ]
    asr_references = [
        reference
        for reference in references
        if str(reference.get("source_type", "")).casefold() == "asr_segment"
    ]
    ocr_references = [
        reference
        for reference in references
        if str(reference.get("source_type", "")).casefold()
        in {"ocr_event", "ocr_block"}
    ]
    if not blocks or not asr_references or not ocr_references:
        raise RuntimeError("Document did not retain both ASR and OCR sources")

    timestamps = [
        int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        for hours, minutes, seconds in re.findall(
            r"\[(\d{2}):(\d{2}):(\d{2})\]",
            markdown,
        )
    ]
    duration = float(document.get("media_duration_seconds") or 0)
    if not timestamps or any(timestamp > duration + 1 for timestamp in timestamps):
        raise RuntimeError("Markdown timestamps are missing or out of range")
    image_paths = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    for raw_path in image_paths:
        if Path(raw_path).is_absolute():
            raise RuntimeError(f"Markdown image path is absolute: {raw_path}")
        resolved = (export_path.parent / raw_path).resolve()
        if not resolved.is_file():
            raise RuntimeError(f"Markdown image is missing: {resolved}")
    if not image_paths:
        raise RuntimeError("Markdown did not include related frames")

    return {
        "block_count": len(blocks),
        "source_reference_count": len(references),
        "asr_reference_count": len(asr_references),
        "ocr_reference_count": len(ocr_references),
        "timestamp_count": len(timestamps),
        "image_count": len(image_paths),
        "frontmatter_keys": sorted(frontmatter),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


if __name__ == "__main__":
    raise SystemExit(main())
