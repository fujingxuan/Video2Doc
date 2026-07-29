from __future__ import annotations

from pathlib import Path

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
