"""Copy a workspace file to Windows Explorer's file clipboard."""
import base64
import os
from pathlib import Path
import subprocess

from app.core.errors import ConflictError


def copy_file(path: Path) -> None:
    if os.name != "nt":
        raise ConflictError("复制音频文件仅支持 Windows，请使用下载音频")
    encoded_path = base64.b64encode(str(path).encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Windows.Forms; "
        f"$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_path}')); "
        "$files=New-Object System.Collections.Specialized.StringCollection; "
        "[void]$files.Add($p); [Windows.Forms.Clipboard]::SetFileDropList($files)"
    )
    command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", command],
            check=True, timeout=10, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConflictError("剪贴板暂时不可用，请重试或打开工作区复制音频") from exc
