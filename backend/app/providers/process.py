from __future__ import annotations

import asyncio
import inspect
import os
import re
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.core.errors import PipelineCancelled, PipelineError, PipelineInterrupted


LineCallback = Callable[[str], None]
CancelCheck = Callable[[], bool | Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    args: tuple[str, ...]
    returncode: int
    stdout_lines: tuple[str, ...]
    stderr_lines: tuple[str, ...]
    elapsed_seconds: float
    stdout_log: Path
    stderr_log: Path


class ProcessTreeTerminator(Protocol):
    async def terminate(self, process: asyncio.subprocess.Process) -> None: ...


class PlatformProcessTreeTerminator:
    async def terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=_windows_creation_flags(),
                )
                try:
                    await asyncio.wait_for(killer.wait(), timeout=5)
                except TimeoutError:
                    killer.kill()
                    await killer.wait()
            except OSError:
                # The parent-process fallback must also work when taskkill
                # cannot be started (missing executable or denied access).
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
                return
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except TimeoutError:
            process.kill()
            await process.wait()


class ProcessRegistry:
    def __init__(self) -> None:
        self._active: dict[int, tuple[asyncio.subprocess.Process, ProcessTreeTerminator]] = {}
        self.shutting_down = False

    def begin_lifecycle(self) -> None:
        self.shutting_down = False

    def register(
        self, process: asyncio.subprocess.Process, terminator: ProcessTreeTerminator
    ) -> None:
        self._active[process.pid] = (process, terminator)

    def unregister(self, process: asyncio.subprocess.Process) -> None:
        self._active.pop(process.pid, None)

    async def shutdown_all(self) -> None:
        self.shutting_down = True
        await asyncio.gather(
            *(terminator.terminate(process) for process, terminator in list(self._active.values())),
            return_exceptions=True,
        )


process_registry = ProcessRegistry()


class ExternalProcessRunner:
    def __init__(self, terminator: ProcessTreeTerminator | None = None) -> None:
        self.terminator = terminator or PlatformProcessTreeTerminator()

    async def run(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path,
        log_dir: Path,
        log_name: str,
        timeout_seconds: float,
        cancel_check: CancelCheck,
        stdout_callback: LineCallback | None = None,
        stderr_callback: LineCallback | None = None,
        expected_returncodes: set[int] | None = None,
    ) -> ProcessResult:
        normalized = tuple(str(item) for item in args)
        if not normalized or not normalized[0]:
            raise ValueError("An executable is required")
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / f"{log_name}.stdout.log"
        stderr_log = log_dir / f"{log_name}.stderr.log"
        creationflags = _windows_creation_flags(new_process_group=True)
        start = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *normalized,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except FileNotFoundError as exc:
            raise PipelineError(
                "PROCESS_UNAVAILABLE",
                f"找不到外部程序：{Path(normalized[0]).name}",
                technical_detail=str(exc),
                log_path=str(stderr_log),
            ) from exc
        process_registry.register(process, self.terminator)
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        async def consume(
            stream: asyncio.StreamReader | None,
            destination: Path,
            collected: list[str],
            callback: LineCallback | None,
        ) -> None:
            if stream is None:
                return
            with destination.open("w", encoding="utf-8", errors="replace") as handle:
                def emit(raw: bytes) -> None:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    collected.append(line)
                    handle.write(line + "\n")
                    handle.flush()
                    if callback:
                        callback(line)

                # yt-dlp emits a complete metadata object on one line, often
                # exceeding StreamReader.readline's default 64 KiB limit.
                pending: list[bytes] = []
                while chunk := await stream.read(64 * 1024):
                    parts = chunk.split(b"\n")
                    pending.append(parts[0])
                    if len(parts) > 1:
                        emit(b"".join(pending))
                        for part in parts[1:-1]:
                            emit(part)
                        pending = [parts[-1]]
                if raw := b"".join(pending):
                    emit(raw)

        stdout_task = asyncio.create_task(
            consume(process.stdout, stdout_log, stdout_lines, stdout_callback)
        )
        stderr_task = asyncio.create_task(
            consume(process.stderr, stderr_log, stderr_lines, stderr_callback)
        )
        wait_task = asyncio.create_task(process.wait())
        terminal_error: PipelineError | None = None
        try:
            tasks = {wait_task, stdout_task, stderr_task}
            while not all(task.done() for task in tasks):
                # A failed log reader or progress callback must stop the
                # process promptly instead of leaving a blocked output pipe.
                for reader in (stdout_task, stderr_task):
                    if reader.done():
                        reader.result()
                if process_registry.shutting_down:
                    terminal_error = PipelineInterrupted()
                    await self.terminator.terminate(process)
                    break
                cancelled = cancel_check()
                if inspect.isawaitable(cancelled):
                    cancelled = await cancelled
                if cancelled:
                    terminal_error = PipelineCancelled(log_path=str(stderr_log))
                    await self.terminator.terminate(process)
                    break
                if time.monotonic() - start >= timeout_seconds:
                    terminal_error = PipelineError(
                        "PROCESS_TIMEOUT",
                        f"外部程序运行超过 {timeout_seconds:g} 秒，已终止",
                        technical_detail=f"Timed out: {redact_command(normalized)}",
                        retryable=True,
                        log_path=str(stderr_log),
                    )
                    await self.terminator.terminate(process)
                    break
                pending_tasks = {task for task in tasks if not task.done()}
                if pending_tasks:
                    await asyncio.wait(
                        pending_tasks,
                        timeout=get_settings().process_poll_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
            await wait_task
            await asyncio.gather(stdout_task, stderr_task)
        finally:
            try:
                if process.returncode is None:
                    await self.terminator.terminate(process)
            finally:
                for task in (stdout_task, stderr_task, wait_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)
                process_registry.unregister(process)
        if terminal_error:
            raise terminal_error
        result = ProcessResult(
            args=normalized,
            returncode=process.returncode or 0,
            stdout_lines=tuple(stdout_lines),
            stderr_lines=tuple(stderr_lines),
            elapsed_seconds=time.monotonic() - start,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )
        allowed = expected_returncodes if expected_returncodes is not None else {0}
        if result.returncode not in allowed:
            tail = redact_text("\n".join(stderr_lines[-20:]))
            raise PipelineError(
                "PROCESS_FAILED",
                f"外部程序执行失败（退出码 {result.returncode}）",
                technical_detail=tail or f"Command failed: {redact_command(normalized)}",
                retryable=False,
                log_path=str(stderr_log),
            )
        return result


def redact_command(args: Sequence[str]) -> str:
    secret_flags = {"--password", "--username", "--cookies", "--cookies-from-browser"}
    redacted: list[str] = []
    hide_next = False
    for value in args:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        lowered = value.lower()
        if lowered in secret_flags:
            redacted.append(value)
            hide_next = True
            continue
        if re.match(r"https?://", value, re.IGNORECASE):
            value = re.sub(r"(?<=://)[^/@\s]+@", "***@", value)
            value = value.split("?", 1)[0] + ("?***" if "?" in value else "")
        redacted.append(value)
    return subprocess.list2cmdline(redacted)


def redact_text(value: str) -> str:
    value = re.sub(
        r"(?i)(authorization|api[_-]?key|bearer|token|secret)\s*[:=]?\s*[^\s,;\"]+",
        r"\1:***",
        value,
    )
    value = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", value, flags=re.IGNORECASE)
    value = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?***", value, flags=re.IGNORECASE)
    return value


def _windows_creation_flags(*, new_process_group: bool = False) -> int:
    if os.name != "nt":
        return 0
    flags = subprocess.CREATE_NO_WINDOW
    if new_process_group:
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    return flags
