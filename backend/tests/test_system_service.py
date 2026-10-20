from __future__ import annotations

import os

import pytest

from app.services import system


def test_output_directory_picker_is_owned_by_foreground_app_window(monkeypatch) -> None:
    if os.name != "nt":
        pytest.skip("Native folder picker is available only on Windows")
    captured: dict[str, str] = {}

    def fake_run(script: str, display_name: str):
        captured["script"] = script
        captured["display_name"] = display_name
        return None

    monkeypatch.setattr(system, "_run_native_dialog", fake_run)
    assert system.select_output_directory() is None
    assert "Add-Type @'" not in captured["script"]
    assert "$owner.TopMost = $true" in captured["script"]
    assert "$owner.Opacity = 0" in captured["script"]
    assert "ShowDialog($owner)" in captured["script"]
    assert captured["display_name"] == "Windows 文件夹选择器"
