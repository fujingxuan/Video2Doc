from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO

import httpx
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_EXE = REPOSITORY_ROOT / "dist" / "Video2Doc-Portable" / "Video2Doc.exe"
OCR_PYTHON = Path(
    r"D:\Models\Video2Doc\envs\paddleocr\Scripts\python.exe"
)
OCR_SERVICE = (
    REPOSITORY_ROOT / "scripts" / "model_runtime" / "ocr_gateway_service.py"
)
ASR_SERVICE = (
    REPOSITORY_ROOT / "scripts" / "model_runtime" / "asr_gateway_service.py"
)
ASR_LAUNCHER = (
    REPOSITORY_ROOT / "scripts" / "model_runtime" / "run_asr_gateway.sh"
)
MODEL_ROOT = Path(r"D:\Models\Video2Doc")
FFMPEG = Path(
    r"C:\Users\Aaron\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"
)
SOURCE_MEDIA = Path(r"D:\Video2Doc-Test\sample.mp4")
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a fresh sample.mp4 task through the packaged application"
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--app-port", type=int, default=8891)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--resume-existing", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    if (
        not args.resume_existing
        and output_root.exists()
        and any(output_root.iterdir())
    ):
        raise RuntimeError(f"Acceptance output directory is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    if args.resume_existing and not (
        output_root / "app-home" / "video2doc.db"
    ).is_file():
        raise RuntimeError("Resume requested but packaged application database is missing")
    logs = output_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (output_root / "vault").mkdir(parents=True, exist_ok=True)

    _require_file(PACKAGE_EXE)
    _require_file(OCR_PYTHON)
    _require_file(OCR_SERVICE)
    _require_file(ASR_SERVICE)
    _require_file(ASR_LAUNCHER)
    _require_file(FFMPEG)
    _require_file(SOURCE_MEDIA)
    if _port_listening(args.app_port):
        raise RuntimeError(f"Application acceptance port is already in use: {args.app_port}")

    source_hash_before = _sha256(SOURCE_MEDIA)
    processes: list[tuple[str, subprocess.Popen[bytes], BinaryIO, BinaryIO]] = []
    started_services: set[str] = set()
    asr_gateway_name = "local-whisper"
    asr_model_name = "large-v3-turbo"
    asr_runtime = "local-whisper"
    base_url = f"http://127.0.0.1:{args.app_port}/api/v1"
    started = time.monotonic()
    try:
        if not _healthy("http://127.0.0.1:8201/health"):
            ocr_stdout = (logs / "ocr.stdout.log").open("ab")
            ocr_stderr = (logs / "ocr.stderr.log").open("ab")
            ocr_env = _normalized_environment()
            ocr_env.update(
                {
                    "VIDEO2DOC_OCR_RUNTIME_ROOT": str(output_root / "ocr-runtime"),
                    "PADDLE_PDX_CACHE_HOME": str(MODEL_ROOT / "ocr" / "cache"),
                    "PADDLE_PDX_MODEL_SOURCE": "BOS",
                    "FLAGS_use_mkldnn": "0",
                }
            )
            ocr_process = _start_process(
                [
                    str(OCR_PYTHON),
                    str(OCR_SERVICE),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8201",
                ],
                stdout=ocr_stdout,
                stderr=ocr_stderr,
                cwd=REPOSITORY_ROOT,
                env=ocr_env,
            )
            processes.append(("ocr", ocr_process, ocr_stdout, ocr_stderr))
            started_services.add("ocr")
            _wait_health("http://127.0.0.1:8201/health", 120, ocr_process)

        if not _healthy("http://127.0.0.1:8103/health"):
            asr_stdout = (logs / "asr.stdout.log").open("ab")
            asr_stderr = (logs / "asr.stderr.log").open("ab")
            service_path = _wsl_path(ASR_SERVICE)
            launcher_path = _wsl_path(ASR_LAUNCHER)
            model_root = _wsl_path(MODEL_ROOT)
            environment_root = subprocess.check_output(
                [
                    "wsl.exe",
                    "-d",
                    "Ubuntu",
                    "--",
                    "bash",
                    "-lc",
                    'printf %s "${XDG_DATA_HOME:-$HOME/.local/share}/video2doc-model-runtime"',
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            ).strip()
            candidates = [
                f"{environment_root}/envs/whisper/bin/python",
                "/root/.local/share/video2doc-model-runtime/envs/whisper/bin/python",
            ]
            discovery = subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    "Ubuntu",
                    "--",
                    "bash",
                    "-lc",
                    "find /home /root -path '*/envs/whisper/bin/python' "
                    "-print -quit 2>/dev/null",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=60,
            )
            discovered = discovery.stdout.strip()
            if discovered:
                candidates.append(discovered)
            whisper_python = next(
                (
                    candidate
                    for candidate in candidates
                    if subprocess.run(
                        [
                            "wsl.exe",
                            "-d",
                            "Ubuntu",
                            "--",
                            "bash",
                            "-lc",
                            f"test -x '{candidate}'",
                        ],
                        check=False,
                        timeout=30,
                    ).returncode
                    == 0
                ),
                None,
            )
            if whisper_python is not None:
                asr_process = _start_process(
                    [
                        "wsl.exe",
                        "-d",
                        "Ubuntu",
                        "--",
                        "bash",
                        launcher_path,
                        "whisper",
                        model_root,
                        whisper_python,
                        service_path,
                        "8103",
                    ],
                    stdout=asr_stdout,
                    stderr=asr_stderr,
                    cwd=REPOSITORY_ROOT,
                )
                processes.append(("asr", asr_process, asr_stdout, asr_stderr))
                started_services.add("asr")
                _wait_health(
                    "http://127.0.0.1:8103/health",
                    180,
                    asr_process,
                )
            else:
                asr_stdout.close()
                asr_stderr.close()
                if not os.getenv("DASHSCOPE_API_KEY", "").strip():
                    raise RuntimeError(
                        "Whisper environment is unavailable and "
                        "DASHSCOPE_API_KEY is not configured; checked "
                        + ", ".join(candidates)
                    )
                asr_gateway_name = "dashscope-fun-asr"
                asr_model_name = "fun-asr-realtime-2026-02-28"
                asr_runtime = "dashscope-fun-asr"

        app_stdout = (logs / "app.stdout.log").open("ab")
        app_stderr = (logs / "app.stderr.log").open("ab")
        app_process = _start_process(
            [
                str(PACKAGE_EXE),
                "--no-browser",
                "--port",
                str(args.app_port),
                "--app-home",
                str(output_root / "app-home"),
            ],
            stdout=app_stdout,
            stderr=app_stderr,
            cwd=PACKAGE_EXE.parent,
            env=_normalized_environment(),
        )
        processes.append(("app", app_process, app_stdout, app_stderr))
        started_services.add("app")
        _wait_health(
            f"http://127.0.0.1:{args.app_port}/api/v1/health",
            120,
            app_process,
        )

        task, cancel_task, details = _run_application_acceptance(
            base_url,
            output_root=output_root,
            timeout_seconds=args.timeout_seconds,
            asr_gateway_name=asr_gateway_name,
            asr_model_name=asr_model_name,
            resume_existing=args.resume_existing,
        )
        source_hash_after = _sha256(SOURCE_MEDIA)
        if source_hash_after != source_hash_before:
            raise RuntimeError("Source sample hash changed during fresh acceptance")

        report = _build_report(
            task=task,
            cancel_task=cancel_task,
            details=details,
            output_root=output_root,
            source_hash=source_hash_before,
            elapsed_seconds=time.monotonic() - started,
            started_services=started_services,
            asr_runtime=asr_runtime,
        )
        report_path = output_root / "fresh-acceptance-summary.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"ACCEPTANCE_REPORT={report_path}")
        return 0
    finally:
        if "app" in started_services:
            try:
                with httpx.Client(base_url=base_url, timeout=5, trust_env=False) as client:
                    client.post("/system/shutdown")
            except httpx.HTTPError:
                pass
        for name, process, stdout, stderr in reversed(processes):
            try:
                process.wait(timeout=30 if name == "app" else 5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            stdout.close()
            stderr.close()
        if "asr" in started_services:
            subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    "Ubuntu",
                    "--",
                    "bash",
                    "-lc",
                    "pkill -f 'asr_gateway_service.py --host 127.0.0.1 --port 8103' || true",
                ],
                check=False,
                timeout=30,
            )


def _run_application_acceptance(
    base_url: str,
    *,
    output_root: Path,
    timeout_seconds: float,
    asr_gateway_name: str,
    asr_model_name: str,
    resume_existing: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    with httpx.Client(base_url=base_url, timeout=60, trust_env=False) as client:
        settings_response = client.put(
            "/settings",
            json={
                "ffmpeg_path": str(FFMPEG),
                "obsidian_vault_path": str(output_root / "vault"),
                "inbox_path": "Inbox",
                "default_text_edit_enabled": False,
                "export_conflict_policy": "rename",
                "export_keep_json_sidecar": True,
            },
        )
        settings_response.raise_for_status()
        system = client.get("/system/status")
        system.raise_for_status()
        tool_status = system.json()["tools"]
        if not tool_status["ffmpeg"]["available"]:
            raise RuntimeError(f"Packaged application cannot run FFmpeg: {tool_status}")

        gateway_models = client.get("/gateway-models")
        gateway_models.raise_for_status()
        models = gateway_models.json()
        asr_model = next(
            (
                item
                for item in models
                if item.get("gateway_name") == asr_gateway_name
                and item.get("model_name") == asr_model_name
                and item.get("enabled")
            ),
            None,
        )
        ocr_model = next(
            (
                item
                for item in models
                if item.get("gateway_name") == "local-paddleocr"
                and item.get("model_name") == "PP-OCRv5_server"
                and item.get("enabled")
            ),
            None,
        )
        if not asr_model or not ocr_model:
            raise RuntimeError(
                "Packaged application did not seed the selected ASR/OCR models"
            )
        if resume_existing:
            existing_tasks = client.get("/tasks")
            existing_tasks.raise_for_status()
            failed = next(
                (
                    item
                    for item in existing_tasks.json()
                    if item["status"] in {"FAILED", "INTERRUPTED"}
                ),
                None,
            )
            completed = next(
                (
                    item
                    for item in existing_tasks.json()
                    if item["status"] == "COMPLETED" and item.get("export_path")
                ),
                None,
            )
            resumable = failed or completed
            if not resumable:
                raise RuntimeError(
                    "No failed or completed packaged task is available to inspect"
                )
            snapshot_before = client.get(
                f"/tasks/{resumable['id']}/snapshot"
            )
            snapshot_before.raise_for_status()
            profile_id = int(
                snapshot_before.json()["profile"]["id"]
            )
        else:
            profile_response = client.post(
                "/profiles",
                json={
                    "name": f"Packaged fresh acceptance {int(time.time())}",
                    "description": "Fresh real inference through packaged Video2Doc",
                    "asr_gateway_model_id": asr_model["id"],
                    "ocr_gateway_model_id": ocr_model["id"],
                    "ocr_enabled": True,
                    "keep_timestamps": True,
                    "keep_raw_transcript": True,
                },
            )
            profile_response.raise_for_status()
            profile_id = profile_response.json()["id"]
        create_payload = {
            "pipeline_mode": "REAL",
            "inputs": [
                {
                    "source": str(SOURCE_MEDIA),
                    "source_type": "LOCAL_FILE",
                    "title": "sample packaged fresh real acceptance",
                }
            ],
            "profile_id": profile_id,
            "config_overrides": {
                "frame_sampling_strategy": "hybrid",
                "ocr_frame_interval_seconds": 180.0,
                "scene_change_threshold": 0.70,
                "scene_min_gap_seconds": 2.0,
                "max_candidate_frames": 3,
                "frame_normalized_max_width": 1280,
                "ocr_language": "ch",
                "ocr_batch_size": 1,
                "fusion_window_seconds": 8.0,
                "text_edit_enabled": False,
            },
            "export_vault_path": str(output_root / "vault"),
            "export_inbox_relative_path": "Inbox",
        }
        if resume_existing and failed:
            retry_response = client.post(f"/tasks/{failed['id']}/retry")
            retry_response.raise_for_status()
            task_id = int(failed["id"])
        elif resume_existing:
            task_id = int(completed["id"])
        else:
            created = client.post("/tasks", json=create_payload)
            created.raise_for_status()
            task_id = int(created.json()[0]["id"])
        task = _wait_task(client, task_id, timeout_seconds)
        if task["status"] != "COMPLETED":
            raise RuntimeError(
                f"Fresh task failed: {task.get('error_code')}: "
                f"{task.get('error_message')}"
            )

        cancel_created = client.post(
            "/tasks",
            json={
                **create_payload,
                "inputs": [
                    {
                        "source": str(SOURCE_MEDIA),
                        "source_type": "LOCAL_FILE",
                        "title": "sample packaged cancellation acceptance",
                    }
                ],
            },
        )
        cancel_created.raise_for_status()
        cancel_task_id = int(cancel_created.json()[0]["id"])
        cancel_response = client.post(f"/tasks/{cancel_task_id}/cancel")
        cancel_response.raise_for_status()
        cancel_task = _wait_task(client, cancel_task_id, 120)
        if cancel_task["status"] != "CANCELLED":
            raise RuntimeError(
                f"Cancellation task ended as {cancel_task['status']}"
            )

        document_response = client.get(f"/tasks/{task_id}/document")
        markdown_response = client.get(f"/tasks/{task_id}/markdown")
        frames_response = client.get(f"/tasks/{task_id}/frames")
        ocr_response = client.get(f"/tasks/{task_id}/ocr")
        timeline_response = client.get(f"/tasks/{task_id}/timeline")
        snapshot_response = client.get(f"/tasks/{task_id}/snapshot")
        for response in (
            document_response,
            markdown_response,
            frames_response,
            ocr_response,
            timeline_response,
            snapshot_response,
        ):
            response.raise_for_status()
        details = {
            "settings": settings_response.json(),
            "system": system.json(),
            "document": document_response.json(),
            "markdown": markdown_response.text,
            "frames": frames_response.json(),
            "ocr": ocr_response.json(),
            "timeline": timeline_response.json(),
            "snapshot": snapshot_response.json(),
        }
        return task, cancel_task, details


def _build_report(
    *,
    task: dict[str, Any],
    cancel_task: dict[str, Any],
    details: dict[str, Any],
    output_root: Path,
    source_hash: str,
    elapsed_seconds: float,
    started_services: set[str],
    asr_runtime: str,
) -> dict[str, Any]:
    document = details["document"]
    markdown = details["markdown"]
    export_path = Path(task["export_path"]).resolve()
    if not export_path.is_file() or not export_path.is_relative_to(output_root):
        raise RuntimeError("Fresh task export is missing or outside acceptance root")
    if not markdown.startswith("---\n"):
        raise RuntimeError("Fresh Markdown lacks YAML frontmatter")
    frontmatter_end = markdown.find("\n---\n", 4)
    frontmatter = yaml.safe_load(markdown[4:frontmatter_end])
    if not isinstance(frontmatter, dict):
        raise RuntimeError("Fresh Markdown frontmatter is invalid")
    if str(REPOSITORY_ROOT) in markdown:
        raise RuntimeError("Fresh Markdown leaked a repository workspace path")

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
    asr_count = sum(
        str(item.get("source_type", "")).casefold() == "asr_segment"
        for item in references
    )
    ocr_count = sum(
        str(item.get("source_type", "")).casefold()
        in {"ocr_event", "ocr_block"}
        for item in references
    )
    if not blocks or not asr_count or not ocr_count:
        raise RuntimeError("Fresh document did not retain ASR and OCR references")
    timestamps = [
        int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        for hours, minutes, seconds in re.findall(
            r"\[(\d{2}):(\d{2}):(\d{2})\]",
            markdown,
        )
    ]
    duration = float(document.get("media_duration_seconds") or 0)
    if not timestamps or any(value > duration + 1 for value in timestamps):
        raise RuntimeError("Fresh Markdown timestamps are invalid")
    image_paths = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    for raw_path in image_paths:
        if Path(raw_path).is_absolute():
            raise RuntimeError(f"Fresh Markdown contains absolute image: {raw_path}")
        if not (export_path.parent / raw_path).resolve().is_file():
            raise RuntimeError(f"Fresh Markdown image is missing: {raw_path}")
    if not image_paths:
        raise RuntimeError("Fresh Markdown contains no related frames")

    database = output_root / "app-home" / "video2doc.db"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        edit_row = connection.execute(
            """
            SELECT provider, model, prompt_version, accepted, fallback_used
            FROM text_edit_runs
            WHERE task_id = ? AND attempt = ?
            ORDER BY id DESC LIMIT 1
            """,
            (task["id"], task["attempt"]),
        ).fetchone()
        attempt_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT attempt,
                       MIN(started_at) AS started_at,
                       MAX(finished_at) AS finished_at,
                       SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END)
                           AS completed_stage_count,
                       SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END)
                           AS failed_stage_count,
                       SUM(CASE WHEN message LIKE '%复用%' THEN 1 ELSE 0 END)
                           AS reuse_message_count,
                       ROUND(
                           (julianday(MAX(finished_at))
                            - julianday(MIN(started_at))) * 86400,
                           3
                       ) AS elapsed_seconds,
                       MAX(CASE WHEN stage = 'ASR_TRANSCRIBE'
                           THEN message END) AS asr_message,
                       MAX(CASE WHEN stage = 'FRAME_EXTRACT'
                           THEN message END) AS frame_message,
                       MAX(CASE WHEN stage = 'OCR_RECOGNIZE'
                           THEN message END) AS ocr_message,
                       MAX(CASE WHEN stage = 'OBSIDIAN_EXPORT'
                           THEN message END) AS export_message
                FROM task_stage_runs
                WHERE task_id = ?
                GROUP BY attempt
                ORDER BY attempt
                """,
                (task["id"],),
            )
        ]

    return {
        "status": "passed",
        "acceptance_kind": "packaged_fresh_real_inference",
        "new_model_inference_performed": True,
        "source_media": str(SOURCE_MEDIA),
        "source_media_bytes": SOURCE_MEDIA.stat().st_size,
        "source_sha256_before": source_hash,
        "source_sha256_after": _sha256(SOURCE_MEDIA),
        "report_run_elapsed_seconds": round(elapsed_seconds, 3),
        "started_services": sorted(started_services),
        "asr_runtime": asr_runtime,
        "task": {
            "id": task["id"],
            "status": task["status"],
            "attempt": task["attempt"],
            "workspace": task["workspace_path"],
            "export_path": task["export_path"],
            "completed_stages": [
                row["stage"]
                for row in task.get("stages") or []
                if row["status"] == "COMPLETED"
            ],
            "attempts": attempt_rows,
        },
        "cancellation": {
            "task_id": cancel_task["id"],
            "status": cancel_task["status"],
            "stage": cancel_task["current_stage"],
        },
        "snapshot_schema": details["snapshot"]["schema_version"],
        "models": details["snapshot"].get("models"),
        "frames": {
            "candidate_count": details["frames"].get("candidate_count"),
            "kept_count": details["frames"].get("kept_count"),
            "dropped_count": details["frames"].get("dropped_count"),
        },
        "ocr": {
            "event_count": details["ocr"].get("event_count"),
            "merged_observation_count": details["ocr"].get(
                "merged_observation_count"
            ),
            "frame_count": (task.get("ocr_preview") or {}).get("frame_count"),
            "block_count": (task.get("ocr_preview") or {}).get("block_count"),
            "reused_count": (task.get("ocr_preview") or {}).get("reused_count"),
        },
        "fusion": {
            "event_count": details["timeline"].get("event_count"),
            "counts": details["timeline"].get("counts"),
        },
        "document": {
            "section_count": len(document.get("sections") or []),
            "block_count": len(blocks),
            "source_reference_count": len(references),
            "asr_reference_count": asr_count,
            "ocr_reference_count": ocr_count,
        },
        "text_edit": dict(edit_row) if edit_row else None,
        "markdown": {
            "character_count": len(markdown),
            "timestamp_count": len(timestamps),
            "image_count": len(image_paths),
            "frontmatter_keys": sorted(frontmatter),
        },
        "tool_status": details["system"]["tools"],
    }


def _wait_task(
    client: httpx.Client,
    task_id: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    previous: tuple[str, str | None, int] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/tasks/{task_id}")
        response.raise_for_status()
        task = response.json()
        state = (
            str(task["status"]),
            task.get("current_stage"),
            int(task["progress"]),
        )
        if state != previous:
            print(
                json.dumps(
                    {
                        "task_id": task_id,
                        "status": state[0],
                        "stage": state[1],
                        "progress": state[2],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            previous = state
        if state[0] in TERMINAL_STATES:
            return task
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for task {task_id}")


def _start_process(
    command: list[str],
    *,
    stdout: BinaryIO,
    stderr: BinaryIO,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        creationflags=creation_flags,
    )


def _normalized_environment() -> dict[str, str]:
    environment = dict(os.environ)
    path_keys = [key for key in environment if key.casefold() == "path"]
    canonical = next((key for key in path_keys if key == "Path"), None)
    canonical = canonical or (path_keys[0] if path_keys else None)
    path_value = environment.get(canonical, "") if canonical else ""
    for key in path_keys:
        environment.pop(key, None)
    environment["Path"] = path_value
    return environment


def _wsl_path(path: Path) -> str:
    return subprocess.check_output(
        [
            "wsl.exe",
            "-d",
            "Ubuntu",
            "--",
            "wslpath",
            "-a",
            str(path).replace("\\", "/"),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    ).strip()


def _wait_health(
    url: str,
    timeout_seconds: float,
    process: subprocess.Popen[bytes],
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Process exited before health check: {process.returncode}, {url}"
            )
        if _healthy(url):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for health endpoint: {url}")


def _healthy(url: str) -> bool:
    try:
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get(url)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        return stream.connect_ex(("127.0.0.1", port)) == 0


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"PACKAGED_REAL_ACCEPTANCE_FAILED: {error}", file=sys.stderr)
        raise
