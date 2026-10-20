from __future__ import annotations

from pathlib import Path

import pytest

from app.providers import managed_runtime


class CompletedProcess:
    async def wait(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_release_stops_only_app_started_managed_gateway(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    stop_script = tmp_path / "stop_asr_gateway.ps1"
    stop_script.write_text("# controlled test", encoding="utf-8")

    async def fake_create(*args, **_kwargs):
        calls.append(tuple(str(value) for value in args))
        return CompletedProcess()

    monkeypatch.setattr(managed_runtime, "_runtime_script", lambda _name: stop_script)
    monkeypatch.setattr(managed_runtime, "_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(
        managed_runtime.asyncio,
        "create_subprocess_exec",
        fake_create,
    )
    gateway = {
        "base_url": "http://127.0.0.1:18103",
        "gateway_settings": {
            "managed_lifecycle": True,
            "runtime_kind": "asr",
            "runtime_provider": "whisper",
            "model_root": str(tmp_path / "models"),
        },
    }
    managed_runtime._started_by_app.clear()
    assert await managed_runtime.release_managed_gateway(gateway, log_dir=tmp_path) is False
    managed_runtime._started_by_app.add(("asr", 18103))

    assert await managed_runtime.release_managed_gateway(gateway, log_dir=tmp_path) is True
    assert len(calls) == 1
    assert "-Provider" in calls[0] and "whisper" in calls[0]
    assert "-Port" in calls[0] and "18103" in calls[0]
    assert ("asr", 18103) not in managed_runtime._started_by_app
