from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Mapping
from pathlib import Path


DEFAULT_PORT = 8765
FALLBACK_PORT_ATTEMPTS = 100


def main() -> int:
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
            if not args.no_browser:
                webbrowser.open(app_url)
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
            if not args.no_browser:
                webbrowser.open(fallback_url)
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

    if not args.no_browser:
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
        )
        server = uvicorn.Server(config)
        app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
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
