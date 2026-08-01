from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import ConflictError, PipelineError
from app.models import AppSetting


def media_tool_status(settings: AppSetting) -> dict[str, Any]:
    configured = {
        "ffmpeg": settings.ffmpeg_path or "ffmpeg",
        "ffprobe": _ffprobe_for(settings.ffmpeg_path),
        "yt_dlp": settings.ytdlp_path or "yt-dlp",
    }
    tools = {
        name: _probe_tool(value, "--version" if name == "yt_dlp" else "-version")
        for name, value in configured.items()
    }
    runtime = get_settings()
    return {
        "platform": os.name,
        "tools": tools,
        "data_directory": str(Path(settings.data_path) if settings.data_path else runtime.data_dir),
        "log_directory": str(
            (Path(settings.data_path) if settings.data_path else runtime.data_dir) / "logs"
        ),
        "api_prefix": runtime.api_prefix,
        "port": int(os.getenv("VIDEO2DOC_PORT", "8000")),
    }


def select_media_files() -> list[str]:
    if os.name != "nt":
        raise ConflictError("Native media selection is supported only on Windows")
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = [System.Windows.Forms.OpenFileDialog]::new()
$dialog.Title = '选择 Video2Doc 输入媒体'
$dialog.Multiselect = $true
$dialog.RestoreDirectory = $true
$dialog.Filter = '视频和音频|*.mp4;*.mkv;*.mov;*.avi;*.webm;*.m4v;*.mp3;*.wav;*.m4a;*.aac;*.flac;*.ogg;*.opus|所有文件|*.*'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    @($dialog.FileNames) | ConvertTo-Json -Compress
}
"""
    payload = _run_native_dialog(script, "Windows 文件选择器")
    if payload is None:
        return []
    selected = [payload] if isinstance(payload, str) else payload
    if not isinstance(selected, list):
        raise PipelineError("FILE_PICKER_INVALID_RESPONSE", "Windows 文件选择结果格式错误")
    return [str(Path(item).resolve(strict=True)) for item in selected if isinstance(item, str)]


def select_subtitle_file() -> str | None:
    if os.name != "nt":
        raise ConflictError("Native subtitle selection is supported only on Windows")
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = [System.Windows.Forms.OpenFileDialog]::new()
$dialog.Title = '选择要导入的中文字幕'
$dialog.Multiselect = $false
$dialog.RestoreDirectory = $true
$dialog.Filter = '字幕文件|*.srt;*.vtt;*.ass;*.ssa;*.txt|所有文件|*.*'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    $dialog.FileName | ConvertTo-Json -Compress
}
"""
    payload = _run_native_dialog(script, "Windows 字幕文件选择器")
    if payload is None:
        return None
    if not isinstance(payload, str):
        raise PipelineError("FILE_PICKER_INVALID_RESPONSE", "字幕文件选择结果格式错误")
    return str(Path(payload).resolve(strict=True))


def select_output_directory() -> str | None:
    if os.name != "nt":
        raise ConflictError("Native folder selection is supported only on Windows")
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = [System.Windows.Forms.FolderBrowserDialog]::new()
$dialog.Description = '选择 Markdown 与附件导出目录'
$dialog.ShowNewFolderButton = $true
$owner = [System.Windows.Forms.Form]::new()
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedToolWindow
$owner.Opacity = 0
try {
    $owner.Show()
    $owner.Activate()
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        $dialog.SelectedPath | ConvertTo-Json -Compress
    }
}
finally {
    $owner.Close()
    $owner.Dispose()
    $dialog.Dispose()
}
"""
    payload = _run_native_dialog(script, "Windows 文件夹选择器")
    if payload is None:
        return None
    if not isinstance(payload, str):
        raise PipelineError("FILE_PICKER_INVALID_RESPONSE", "文件夹选择结果格式错误")
    return str(Path(payload).resolve(strict=True))


def _run_native_dialog(script: str, display_name: str) -> Any:
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            errors="replace",
            timeout=3600,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PipelineError(
            "FILE_PICKER_UNAVAILABLE",
            f"无法打开{display_name}",
            technical_detail=str(exc),
        ) from exc
    if result.returncode != 0:
        raise PipelineError(
            "FILE_PICKER_UNAVAILABLE",
            f"{display_name}异常退出",
            technical_detail=(result.stderr or "").strip(),
        )
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineError(
            "FILE_PICKER_INVALID_RESPONSE",
            f"{display_name}返回了无效结果",
            technical_detail=str(exc),
        ) from exc


def _probe_tool(executable: str, version_argument: str) -> dict[str, Any]:
    resolved = shutil.which(executable) or (
        str(Path(executable).resolve(strict=False))
        if Path(executable).is_absolute() and Path(executable).is_file()
        else None
    )
    if not resolved:
        return {
            "configured": executable,
            "available": False,
            "resolved_path": None,
            "version": None,
            "error": "not found",
        }
    try:
        result = subprocess.run(
            [resolved, version_argument],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "configured": executable,
            "available": False,
            "resolved_path": resolved,
            "version": None,
            "error": str(exc),
        }
    output = (result.stdout or result.stderr).splitlines()
    return {
        "configured": executable,
        "available": result.returncode == 0,
        "resolved_path": resolved,
        "version": output[0] if output else None,
        "error": None if result.returncode == 0 else f"exit code {result.returncode}",
    }


def _ffprobe_for(ffmpeg_path: str | None) -> str:
    if not ffmpeg_path:
        return "ffprobe"
    path = Path(ffmpeg_path)
    if path.parent == Path("."):
        return "ffprobe"
    suffix = path.suffix or (".exe" if os.name == "nt" else "")
    return str(path.with_name(f"ffprobe{suffix}"))
