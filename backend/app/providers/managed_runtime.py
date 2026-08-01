from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import REPOSITORY_DIR
from app.core.errors import PipelineError
from app.providers.gateways import gateway_endpoint


_runtime_lock = asyncio.Lock()
_started_by_app: set[tuple[str, int]] = set()


async def ensure_managed_gateway(
    gateway: dict[str, Any] | None,
    *,
    log_dir: Path,
) -> bool:
    """Start a built-in local Gateway on demand; return True if this call started it."""
    if not isinstance(gateway, dict):
        return False
    settings = gateway.get("gateway_settings")
    if not isinstance(settings, dict) or not settings.get("managed_lifecycle"):
        return False
    base_url = str(gateway.get("base_url") or "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise PipelineError(
            "GATEWAY_RUNTIME_INVALID",
            "仅本机内置 Gateway 可以由 Video2Doc 自动启动",
        )
    port = parsed.port
    if port is None:
        raise PipelineError("GATEWAY_RUNTIME_INVALID", "本地 Gateway Base URL 必须包含端口")
    if await _healthy(base_url):
        return False

    runtime_kind = str(settings.get("runtime_kind") or "").strip()
    runtime_provider = str(settings.get("runtime_provider") or "").strip()
    if runtime_kind not in {"asr", "ocr"} or not runtime_provider:
        raise PipelineError(
            "GATEWAY_RUNTIME_UNAVAILABLE",
            "该 Gateway 未声明可自动启动的本地运行时",
        )

    async with _runtime_lock:
        if await _healthy(base_url):
            return False
        script = _runtime_script(f"start_{runtime_kind}_gateway.ps1")
        if script is None:
            raise PipelineError(
                "GATEWAY_RUNTIME_UNAVAILABLE",
                "发行包缺少本地 Gateway 启动组件，请重新安装完整便携包",
            )
        powershell = _powershell_executable()
        if powershell is None:
            raise PipelineError(
                "GATEWAY_RUNTIME_UNAVAILABLE",
                "找不到 Windows PowerShell，无法启动本地 Gateway",
            )
        model_root = str(
            settings.get("model_root")
            or os.getenv("VIDEO2DOC_MODEL_ROOT")
            or r"D:\Models\Video2Doc"
        )
        arguments = [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
        if runtime_kind == "asr":
            arguments.extend(["-Provider", runtime_provider, "-Port", str(port)])
            environment_root = str(
                settings.get("environment_root")
                or os.getenv("VIDEO2DOC_RUNTIME_ENVIRONMENT_ROOT")
                or ""
            ).strip()
            if not environment_root:
                model_root_environment = Path(model_root) / "envs" / runtime_provider
                if model_root_environment.is_dir():
                    environment_root = model_root
            if environment_root:
                arguments.extend(["-EnvironmentRoot", environment_root])
        else:
            arguments.extend(["-Port", str(port)])
        arguments.extend(["-ModelRoot", model_root])
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / f"managed_{runtime_kind}_{runtime_provider}.stdout.log"
        stderr_path = log_dir / f"managed_{runtime_kind}_{runtime_provider}.stderr.log"
        timeout = float(
            settings.get("startup_timeout_seconds")
            or (600 if runtime_kind == "asr" else 120)
        )
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
                process = await asyncio.create_subprocess_exec(
                    *arguments,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=creationflags,
                )
                return_code = await asyncio.wait_for(
                    process.wait(), timeout=max(5.0, timeout)
                )
        except asyncio.TimeoutError as exc:
            raise PipelineError(
                "GATEWAY_RUNTIME_START_TIMEOUT",
                f"{runtime_provider} 本地 Gateway 启动超时",
                technical_detail=str(stderr_path),
                retryable=True,
            ) from exc
        except OSError as exc:
            raise PipelineError(
                "GATEWAY_RUNTIME_START_FAILED",
                f"无法启动 {runtime_provider} 本地 Gateway",
                technical_detail=str(exc),
                retryable=True,
            ) from exc
        if return_code != 0 or not await _healthy(base_url):
            detail = _tail(stderr_path) or _tail(stdout_path) or f"exit={return_code}"
            raise PipelineError(
                "GATEWAY_RUNTIME_START_FAILED",
                f"{runtime_provider} 本地 Gateway 启动失败；请检查模型运行时与日志",
                technical_detail=detail,
                retryable=True,
            )
        _started_by_app.add((runtime_kind, port))
        return True


async def release_managed_gateway(
    gateway: dict[str, Any] | None,
    *,
    log_dir: Path,
) -> bool:
    """Stop a local Gateway started by this app and release its model memory."""
    if not isinstance(gateway, dict):
        return False
    settings = gateway.get("gateway_settings")
    if not isinstance(settings, dict) or not settings.get("managed_lifecycle"):
        return False
    base_url = str(gateway.get("base_url") or "").strip()
    parsed = urlparse(base_url)
    port = parsed.port
    runtime_kind = str(settings.get("runtime_kind") or "").strip()
    runtime_provider = str(settings.get("runtime_provider") or "").strip()
    if port is None or runtime_kind not in {"asr", "ocr"} or not runtime_provider:
        return False
    runtime_key = (runtime_kind, port)
    if runtime_key not in _started_by_app:
        return False

    async with _runtime_lock:
        if runtime_key not in _started_by_app:
            return False
        script = _runtime_script(f"stop_{runtime_kind}_gateway.ps1")
        powershell = _powershell_executable()
        if script is None or powershell is None:
            raise PipelineError(
                "GATEWAY_RUNTIME_STOP_FAILED",
                f"无法停止 {runtime_provider} 本地 Gateway",
                technical_detail="停止脚本或 Windows PowerShell 不可用",
            )
        model_root = str(
            settings.get("model_root")
            or os.getenv("VIDEO2DOC_MODEL_ROOT")
            or r"D:\Models\Video2Doc"
        )
        arguments = [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
        if runtime_kind == "asr":
            arguments.extend(["-Provider", runtime_provider, "-Port", str(port)])
        else:
            arguments.extend(["-Port", str(port)])
        arguments.extend(["-ModelRoot", model_root])
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / f"release_{runtime_kind}_{runtime_provider}.stdout.log"
        stderr_path = log_dir / f"release_{runtime_kind}_{runtime_provider}.stderr.log"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
                process = await asyncio.create_subprocess_exec(
                    *arguments,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=creationflags,
                )
                return_code = await asyncio.wait_for(process.wait(), timeout=30.0)
        except (OSError, asyncio.TimeoutError) as exc:
            raise PipelineError(
                "GATEWAY_RUNTIME_STOP_FAILED",
                f"停止 {runtime_provider} 本地 Gateway 失败",
                technical_detail=str(exc),
            ) from exc
        if return_code != 0:
            raise PipelineError(
                "GATEWAY_RUNTIME_STOP_FAILED",
                f"停止 {runtime_provider} 本地 Gateway 失败",
                technical_detail=_tail(stderr_path) or _tail(stdout_path),
            )
        _started_by_app.discard(runtime_key)
        return True


async def _healthy(base_url: str) -> bool:
    try:
        endpoint = gateway_endpoint(base_url, "/health")
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            response = await client.get(endpoint)
        return response.status_code < 400
    except (httpx.HTTPError, ValueError):
        return False


def _runtime_script(name: str) -> Path | None:
    candidates = [REPOSITORY_DIR / "scripts" / "model_runtime" / name]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.insert(0, Path(bundle_root) / "model_runtime" / name)
    executable_root = Path(sys.executable).resolve().parent
    candidates.extend(
        [
            executable_root / "_internal" / "model_runtime" / name,
            executable_root / "model_runtime" / name,
        ]
    )
    return next((item.resolve() for item in candidates if item.is_file()), None)


def _powershell_executable() -> str | None:
    system_root = Path(os.getenv("SystemRoot", r"C:\Windows"))
    candidate = (
        system_root
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if candidate.is_file():
        return str(candidate)
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _tail(path: Path, limit: int = 4_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""
