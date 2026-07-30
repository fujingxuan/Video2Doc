from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import video2doc_launcher as launcher


def test_requested_port_is_explicit_for_cli_or_environment() -> None:
    assert launcher._requested_port_is_explicit(["--port", "9000"], {})
    assert launcher._requested_port_is_explicit(["--port=9000"], {})
    assert launcher._requested_port_is_explicit([], {"VIDEO2DOC_PORT": "9000"})
    assert not launcher._requested_port_is_explicit([], {})


def test_fallback_uses_first_available_port(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher,
        "_port_available",
        lambda port: port == launcher.DEFAULT_PORT + 2,
    )
    monkeypatch.setattr(launcher, "_is_video2doc", lambda _url: False)

    assert launcher._find_fallback_port(launcher.DEFAULT_PORT + 1) == (
        "available",
        launcher.DEFAULT_PORT + 2,
    )


def test_fallback_reuses_existing_video2doc_before_starting_another(
    monkeypatch,
) -> None:
    monkeypatch.setattr(launcher, "_port_available", lambda _port: False)
    monkeypatch.setattr(
        launcher,
        "_is_video2doc",
        lambda url: url.endswith(f":{launcher.DEFAULT_PORT + 1}/api/v1/health"),
    )

    assert launcher._find_fallback_port(launcher.DEFAULT_PORT + 1) == (
        "video2doc",
        launcher.DEFAULT_PORT + 1,
    )


def test_fallback_stops_at_port_range_end(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "_port_available", lambda _port: False)
    monkeypatch.setattr(launcher, "_is_video2doc", lambda _url: False)

    assert launcher._find_fallback_port(65535, attempts=100) is None


def test_edge_executable_prefers_explicit_configuration(tmp_path: Path) -> None:
    executable = tmp_path / "msedge.exe"
    executable.write_bytes(b"edge")

    assert launcher._edge_executable(
        {"VIDEO2DOC_BROWSER_EXECUTABLE": str(executable)}
    ) == executable.resolve()


def test_windowless_launcher_supplies_streams_for_uvicorn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from uvicorn.logging import DefaultFormatter

    monkeypatch.setattr(launcher.sys, "stdout", None)
    monkeypatch.setattr(launcher.sys, "stderr", None)

    stream = launcher._ensure_output_streams(tmp_path / "launcher.log")
    try:
        DefaultFormatter("%(message)s")
        print("windowless-output")
        assert launcher.sys.stdout is stream
        assert launcher.sys.stderr is stream
    finally:
        stream.close()

    assert "windowless-output" in (tmp_path / "launcher.log").read_text(
        encoding="utf-8"
    )


def test_windows_instance_mutex_blocks_duplicate_process() -> None:
    if launcher.os.name != "nt":
        pytest.skip("Windows named mutex is only available on Windows")

    name = rf"Local\Video2Doc.Test.{uuid4().hex}"
    first_handle = launcher._try_acquire_instance_mutex(name)
    assert first_handle is not None
    try:
        assert launcher._try_acquire_instance_mutex(name) is None
    finally:
        launcher._close_instance_mutex(first_handle)


def test_duplicate_launch_exits_before_opening_another_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    activated: list[bool] = []
    monkeypatch.setattr(
        launcher.sys,
        "argv",
        ["Video2Doc.exe", "--app-home", str(tmp_path)],
    )
    monkeypatch.setattr(launcher, "_try_acquire_instance_mutex", lambda: None)
    monkeypatch.setattr(
        launcher,
        "_open_standalone_window",
        lambda *_args: pytest.fail("duplicate launch must not open a window"),
    )
    monkeypatch.setattr(
        launcher,
        "_activate_existing_window",
        lambda: activated.append(True) or True,
    )

    assert launcher.main() == 0
    assert activated == [True]


def test_standalone_window_tracks_real_window_handle(monkeypatch) -> None:
    process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(launcher, "_is_window_open", lambda handle: handle == 100)

    assert launcher._standalone_window_running(process, 100)
    assert not launcher._standalone_window_running(process, 200)
    assert launcher._standalone_window_running(process, None)


def test_application_window_wait_ignores_preexisting_edge_windows(monkeypatch) -> None:
    observed = iter([(11,), (11, 22)])
    monkeypatch.setattr(launcher.os, "name", "nt")
    monkeypatch.setattr(
        launcher,
        "_find_application_windows",
        lambda: next(observed),
    )
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)

    assert launcher._wait_for_application_window({11}, attempts=2) == 22
