from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO


DEFAULT_PORT = 8765
FALLBACK_PORT_ATTEMPTS = 100
INSTANCE_MUTEX_NAME = r"Local\Video2Doc.Desktop.SingleInstance"
WINDOWS_ERROR_ALREADY_EXISTS = 183
APP_WINDOW_TITLE = "Video2Doc"
_OUTPUT_STREAM: TextIO | None = None
_INSTANCE_MUTEX_HANDLE: int | None = None


def main() -> int:
    global _INSTANCE_MUTEX_HANDLE

    launch_argv = sys.argv[1:]
    port_is_explicit = _requested_port_is_explicit(launch_argv, os.environ)
    parser = argparse.ArgumentParser(description="Video2Doc Windows launcher")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("VIDEO2DOC_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--app-home")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        return _fatal("端口必须位于 1 到 65535 之间。")

    bundle_root = Path(__file__).resolve().parent
    app_root = Path(
        args.app_home
        or os.getenv(
            "VIDEO2DOC_APP_HOME",
            str(Path(os.getenv("LOCALAPPDATA", Path.home())) / "Video2Doc"),
        )
    ).resolve()
    data_root = app_root / "tasks"
    default_vault = app_root / "default-vault"
    logs = app_root / "logs"
    for path in (app_root, data_root, default_vault / "Inbox", logs):
        path.mkdir(parents=True, exist_ok=True)
    _ensure_output_streams(logs / "launcher.log")
    if not args.check_only:
        try:
            _INSTANCE_MUTEX_HANDLE = _try_acquire_instance_mutex()
        except OSError as exc:
            return _fatal(
                f"无法建立应用单实例锁：{exc}",
                logs / "launcher-error.log",
            )
        if _INSTANCE_MUTEX_HANDLE is None:
            _activate_existing_window()
            print("Video2Doc 已在运行；已阻止重复窗口。")
            return 0

    frontend = _first_existing(
        bundle_root / "frontend_dist",
        bundle_root.parent / "frontend" / "dist",
    )
    migrations = _first_existing(
        bundle_root / "migrations",
        bundle_root / "backend" / "migrations",
    )
    alembic_ini = _first_existing(
        bundle_root / "alembic.ini",
        bundle_root / "backend" / "alembic.ini",
    )
    if not frontend or not (frontend / "index.html").is_file():
        return _fatal("发行包缺少前端资源。", logs / "launcher-error.log")
    if not migrations or not alembic_ini:
        return _fatal("发行包缺少数据库迁移资源。", logs / "launcher-error.log")

    health_url = f"http://127.0.0.1:{args.port}/api/v1/health"
    app_url = f"http://127.0.0.1:{args.port}/"
    if not _port_available(args.port):
        if _is_video2doc(health_url):
            print(f"Video2Doc 已在运行：{app_url}")
            return 0
        if port_is_explicit:
            return _fatal(
                f"端口 {args.port} 已被其他程序占用。"
                "请关闭冲突程序或使用 --port 指定其他端口。",
                logs / "launcher-error.log",
            )
        fallback = _find_fallback_port(args.port + 1)
        if fallback is None:
            return _fatal(
                f"默认端口 {args.port} 已被其他程序占用，"
                f"后续 {FALLBACK_PORT_ATTEMPTS} 个端口也没有可用端口。",
                logs / "launcher-error.log",
            )
        fallback_kind, fallback_port = fallback
        fallback_url = f"http://127.0.0.1:{fallback_port}/"
        if fallback_kind == "video2doc":
            print(f"Video2Doc 已在运行：{fallback_url}")
            return 0
        print(
            f"默认端口 {args.port} 已被其他程序占用，"
            f"Video2Doc 将改用端口 {fallback_port}。"
        )
        args.port = fallback_port

    runtime_paths = {
        "VIDEO2DOC_DATABASE_URL": (
            f"sqlite:///{(app_root / 'video2doc.db').as_posix()}"
        ),
        "VIDEO2DOC_DATA_DIR": str(data_root),
        "VIDEO2DOC_INBOX_DIR": str(default_vault / "Inbox"),
    }
    for name, value in runtime_paths.items():
        if args.app_home:
            os.environ[name] = value
        else:
            os.environ.setdefault(name, value)
    os.environ.setdefault("VIDEO2DOC_FRONTEND_DIR", str(frontend))
    os.environ["VIDEO2DOC_PORT"] = str(args.port)
    os.environ.setdefault(
        "VIDEO2DOC_CORS_ORIGINS",
        f"http://127.0.0.1:{args.port},http://localhost:{args.port}",
    )

    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(alembic_ini))
        config.set_main_option("script_location", str(migrations))
        command.upgrade(config, "head")
    except Exception as exc:
        return _fatal(
            f"数据库迁移失败：{type(exc).__name__}: {exc}",
            logs / "launcher-error.log",
        )

    health_url = f"http://127.0.0.1:{args.port}/api/v1/health"
    app_url = f"http://127.0.0.1:{args.port}/"
    if args.check_only:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "app_home": str(app_root),
                    "frontend": str(frontend),
                    "migrations": str(migrations),
                    "port": args.port,
                },
                ensure_ascii=False,
            )
        )
        return 0

    standalone = not args.no_browser and os.name == "nt"
    if not args.no_browser and not standalone:
        threading.Thread(
            target=_open_when_ready,
            args=(health_url, app_url),
            daemon=True,
        ).start()
    try:
        import uvicorn
        from app.main import app

        print(f"Video2Doc 正在启动：{app_url}")
        print(f"应用数据：{app_root}")
        print("关闭此窗口或按 Ctrl+C 可停止由 Video2Doc 启动的后端进程。")
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            log_level=os.getenv("VIDEO2DOC_LOG_LEVEL", "info").casefold(),
            access_log=False,
            use_colors=False,
        )
        server = uvicorn.Server(config)
        shutdown_requested = threading.Event()

        def request_shutdown() -> None:
            shutdown_requested.set()
            server.should_exit = True

        app.state.request_shutdown = request_shutdown
        if standalone:
            server_thread = threading.Thread(target=server.run, daemon=True)
            server_thread.start()
            if not _wait_until_ready(health_url, server_thread):
                server.should_exit = True
                server_thread.join(timeout=5)
                return _fatal(
                    "应用后端未能在 30 秒内启动。",
                    logs / "launcher-error.log",
                )
            previous_windows = set(_find_application_windows())
            window = _open_standalone_window(app_url, app_root)
            if window is None:
                server.should_exit = True
                server_thread.join(timeout=5)
                return _fatal(
                    "无法启动 Video2Doc 独立窗口；请确认 Microsoft Edge 已安装。",
                    logs / "launcher-error.log",
                )
            app_window_handle = _wait_for_application_window(previous_windows)
            if app_window_handle is None:
                if window.poll() is None:
                    window.terminate()
                server.should_exit = True
                server_thread.join(timeout=5)
                return _fatal(
                    "Video2Doc 独立窗口未能在 10 秒内创建。",
                    logs / "launcher-error.log",
                )
            while (
                _standalone_window_running(window, app_window_handle)
                and server_thread.is_alive()
                and not shutdown_requested.wait(0.25)
            ):
                pass
            if window.poll() is None:
                window.terminate()
                try:
                    window.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    window.kill()
            server.should_exit = True
            server_thread.join(timeout=15)
        else:
            server.run()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        return _fatal(
            f"应用启动失败：{type(exc).__name__}: {exc}",
            logs / "launcher-error.log",
        )
    return 0


def _first_existing(*candidates: Path) -> Path | None:
    return next((path.resolve() for path in candidates if path.exists()), None)


def _ensure_output_streams(log_path: Path) -> TextIO | None:
    global _OUTPUT_STREAM

    if sys.stdout is not None and sys.stderr is not None:
        return _OUTPUT_STREAM
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("a", encoding="utf-8", buffering=1)
    _OUTPUT_STREAM = stream
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return stream


def _try_acquire_instance_mutex(name: str = INSTANCE_MUTEX_NAME) -> int | None:
    if os.name != "nt":
        return -1

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    handle = create_mutex(None, True, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == WINDOWS_ERROR_ALREADY_EXISTS:
        close_handle(handle)
        return None
    return int(handle)


def _close_instance_mutex(handle: int) -> None:
    if os.name != "nt" or handle < 0:
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        return stream.connect_ex(("127.0.0.1", port)) != 0


def _requested_port_is_explicit(
    argv: list[str],
    environ: Mapping[str, str],
) -> bool:
    return "VIDEO2DOC_PORT" in environ or any(
        argument == "--port" or argument.startswith("--port=")
        for argument in argv
    )


def _find_fallback_port(
    start_port: int,
    attempts: int = FALLBACK_PORT_ATTEMPTS,
) -> tuple[str, int] | None:
    end_port = min(65535, start_port + attempts - 1)
    for port in range(start_port, end_port + 1):
        if _port_available(port):
            return "available", port
        if _is_video2doc(f"http://127.0.0.1:{port}/api/v1/health"):
            return "video2doc", port
    return None


def _is_video2doc(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("service") == "video2doc-backend"
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


def _open_when_ready(health_url: str, app_url: str) -> None:
    for _attempt in range(120):
        if _is_video2doc(health_url):
            webbrowser.open(app_url)
            return
        time.sleep(0.25)


def _wait_until_ready(health_url: str, server_thread: threading.Thread) -> bool:
    for _attempt in range(120):
        if _is_video2doc(health_url):
            return True
        if not server_thread.is_alive():
            return False
        time.sleep(0.25)
    return False


def _edge_executable(environ: Mapping[str, str] | None = None) -> Path | None:
    values = environ or os.environ
    configured = str(values.get("VIDEO2DOC_BROWSER_EXECUTABLE") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(values.get("PROGRAMFILES(X86)", ""))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(values.get("PROGRAMFILES", ""))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(values.get("LOCALAPPDATA", ""))
        / "Microsoft/Edge/Application/msedge.exe",
    ]
    discovered = shutil.which("msedge")
    if discovered:
        candidates.append(Path(discovered))
    return next(
        (
            candidate.resolve()
            for candidate in candidates
            if candidate is not None and candidate.is_file()
        ),
        None,
    )


def _open_standalone_window(
    app_url: str,
    app_root: Path,
) -> subprocess.Popen[bytes] | None:
    executable = _edge_executable()
    if executable is None:
        return None
    profile = app_root / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        return subprocess.Popen(
            _standalone_window_arguments(executable, app_url, profile),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
    except OSError:
        return None


def _standalone_window_arguments(
    executable: Path,
    app_url: str,
    profile: Path,
) -> list[str]:
    return [
        str(executable),
        f"--app={app_url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        # Enterprise policy or sync can provision regular Edge extensions into
        # a new profile.  Disable them in this app-only profile so extension
        # onboarding pages never become a second browser window.
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
    ]


def _standalone_window_running(
    process: subprocess.Popen[bytes],
    window_handle: int | None,
) -> bool:
    if window_handle is not None:
        return _is_window_open(window_handle)
    return process.poll() is None


def _wait_for_application_window(
    excluded_handles: set[int] | None = None,
    attempts: int = 40,
) -> int | None:
    if os.name != "nt":
        return None
    excluded = excluded_handles or set()
    for _attempt in range(attempts):
        handles = [
            handle
            for handle in _find_application_windows()
            if handle not in excluded
        ]
        if handles:
            return handles[0]
        time.sleep(0.25)
    return None


def _find_application_windows(title: str = APP_WINDOW_TITLE) -> tuple[int, ...]:
    if os.name != "nt":
        return ()

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found_handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    def inspect_window(window_handle: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(window_handle):
            return True
        length = user32.GetWindowTextLengthW(window_handle)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window_handle, buffer, length + 1)
        if buffer.value == title:
            found_handles.append(int(window_handle))
        return True

    callback = callback_type(inspect_window)
    user32.EnumWindows(callback, 0)
    return tuple(found_handles)


def _activate_existing_window() -> bool:
    if os.name != "nt":
        return False
    handles = _find_application_windows()
    if not handles:
        return False

    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    handle = handles[0]
    user32.ShowWindow(handle, 9)
    return bool(user32.SetForegroundWindow(handle))


def _is_window_open(window_handle: int) -> bool:
    if os.name != "nt":
        return False

    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    return bool(user32.IsWindow(window_handle))


def _fatal(message: str, log_path: Path | None = None) -> int:
    print(message, file=sys.stderr)
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(message + "\n", encoding="utf-8")
        except OSError:
            pass
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "Video2Doc", 0x10)
        except (AttributeError, OSError):
            pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
