from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.providers.gateways import GatewayProbeResult
from app.providers.model_probe import ModelProbeResult
from app.services.gateways import seed_default_gateways
from tests.helpers import wait_for_status


def test_direct_credentials_are_encrypted_and_never_returned(client: TestClient) -> None:
    secret = "direct-secret-value-for-test"
    model_response = client.post(
        "/api/v1/models",
        json={
            "name": "direct-credential-model",
            "provider": "custom",
            "base_url": "http://127.0.0.1:13000/v1",
            "capabilities": ["TEXT_PROCESSING"],
            "secret_value": secret,
        },
    )
    assert model_response.status_code == 201, model_response.text
    model = model_response.json()
    assert model["secret_source"] == "DIRECT"
    assert model["secret_configured"] is True
    assert secret not in model_response.text
    assert secret not in client.get("/api/v1/models").text

    gateway_response = client.post(
        "/api/v1/gateways",
        json={
            "name": "direct-credential-asr",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:18109",
            "secret_value": secret,
        },
    )
    assert gateway_response.status_code == 201, gateway_response.text
    gateway = gateway_response.json()
    assert gateway["secret_source"] == "DIRECT"
    assert gateway["secret_configured"] is True
    assert secret not in gateway_response.text
    assert secret not in client.get("/api/v1/gateways").text


def test_each_model_has_an_independent_connectivity_endpoint(
    client: TestClient,
    monkeypatch,
) -> None:
    checked_at = datetime.now(timezone.utc)

    async def text_probe(model):
        assert model.name == "connectivity-llm"
        return ModelProbeResult("HEALTHY", "LLM probe ok", checked_at, 12)

    monkeypatch.setattr("app.services.catalog.probe_text_model", text_probe)
    llm = client.post(
        "/api/v1/models",
        json={
            "name": "connectivity-llm",
            "provider": "custom",
            "base_url": "http://127.0.0.1:13001/v1",
            "capabilities": ["TEXT_PROCESSING"],
        },
    ).json()
    llm_health = client.post(f"/api/v1/models/{llm['id']}/health")
    assert llm_health.status_code == 200, llm_health.text
    assert llm_health.json() == {
        "model_id": llm["id"],
        "status": "HEALTHY",
        "detail": "LLM probe ok",
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "latency_ms": 12,
    }

    gateway = client.post(
        "/api/v1/gateways",
        json={
            "name": "connectivity-asr-gateway",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:18131",
        },
    ).json()
    model = client.post(
        "/api/v1/gateway-models",
        json={
            "gateway_id": gateway["id"],
            "model_name": "connectivity-asr",
            "display_name": "Connectivity ASR",
        },
    ).json()

    async def gateway_model_probe(item, **_kwargs):
        assert item.model_name == "connectivity-asr"
        return ModelProbeResult("MODEL_NOT_FOUND", "not in model directory", checked_at, 8)

    monkeypatch.setattr("app.services.gateways.probe_gateway_model", gateway_model_probe)
    model_health = client.post(f"/api/v1/gateway-models/{model['id']}/health")
    assert model_health.status_code == 200, model_health.text
    assert model_health.json()["status"] == "MODEL_NOT_FOUND"
    persisted = client.get("/api/v1/gateway-models?gateway_type=ASR").json()
    persisted_model = next(item for item in persisted if item["id"] == model["id"])
    assert persisted_model["health_status"] == "MODEL_NOT_FOUND"

    async def gateway_probe(_gateway, **_kwargs):
        return GatewayProbeResult("HEALTHY", "gateway only", checked_at)

    monkeypatch.setattr("app.services.gateways.probe_gateway", gateway_probe)
    gateway_health = client.post(f"/api/v1/gateways/{gateway['id']}/health")
    assert gateway_health.status_code == 200, gateway_health.text
    persisted = client.get("/api/v1/gateway-models?gateway_type=ASR").json()
    persisted_model = next(item for item in persisted if item["id"] == model["id"])
    assert persisted_model["health_status"] == "MODEL_NOT_FOUND"


def test_health_models_profiles_settings_and_immutable_snapshot(
    client: TestClient,
    monkeypatch,
) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    assert health.json()["queue_worker"] == "running"
    root = client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert "Video2Doc" in root.text
    monkeypatch.setattr(
        "app.api.router.media_tool_status",
        lambda _settings: {
            "platform": "nt",
            "tools": {"ffmpeg": {"available": True}},
            "data_directory": "D:\\Video2Doc",
            "log_directory": "D:\\Video2Doc\\logs",
            "api_prefix": "/api/v1",
            "port": 8000,
        },
    )
    system = client.get("/api/v1/system/status")
    assert system.status_code == 200
    assert system.json()["tools"]["ffmpeg"]["available"] is True
    monkeypatch.setattr(
        "app.api.router.select_media_files",
        lambda: [r"D:\Video2Doc-Test\sample.mp4"],
    )
    selected = client.post("/api/v1/system/select-media")
    assert selected.json()["paths"] == [r"D:\Video2Doc-Test\sample.mp4"]
    monkeypatch.setattr(
        "app.api.router.select_subtitle_file",
        lambda: r"D:\Video2Doc-Test\sample.zh-CN.srt",
    )
    subtitle = client.post("/api/v1/system/select-subtitle")
    assert subtitle.json()["path"].endswith("sample.zh-CN.srt")
    monkeypatch.setattr(
        "app.api.router.select_output_directory",
        lambda: r"D:\Video2Doc-Output",
    )
    output = client.post("/api/v1/system/select-output-directory")
    assert output.json()["path"] == r"D:\Video2Doc-Output"
    shutdown_called: list[bool] = []
    client.app.state.request_shutdown = lambda: shutdown_called.append(True)
    shutdown = client.post("/api/v1/system/shutdown")
    assert shutdown.json()["status"] == "shutting_down"
    assert shutdown_called == [True]

    model_specs = [
        ("whisper-large-v3", ["ASR"]),
        ("paddleocr", ["OCR"]),
        ("gpt-5-mini", ["TEXT_PROCESSING"]),
    ]
    models = {}
    for name, capabilities in model_specs:
        response = client.post(
            "/api/v1/models",
            json={"name": name, "capabilities": capabilities, "enabled": True},
        )
        assert response.status_code == 201, response.text
        models[name] = response.json()

    assert [item["name"] for item in client.get("/api/v1/models?capability=ASR").json()] == [
        "whisper-large-v3"
    ]

    asr_gateway = client.post(
        "/api/v1/gateways",
        json={
            "name": "test-asr-gateway",
            "gateway_type": "ASR",
            "protocol": "video2doc-asr-http-v1",
            "base_url": "http://127.0.0.1:18101",
            "secret_env_name": "TEST_ASR_SECRET",
            "settings": {"timeout_seconds": 45, "max_retries": 3},
        },
    ).json()
    ocr_gateway = client.post(
        "/api/v1/gateways",
        json={
            "name": "test-ocr-gateway",
            "gateway_type": "OCR",
            "protocol": "video2doc-ocr-http-v1",
            "base_url": "http://127.0.0.1:18201",
        },
    ).json()
    asr_gateway_model = client.post(
        "/api/v1/gateway-models",
        json={
            "gateway_id": asr_gateway["id"],
            "model_name": "test-asr",
            "display_name": "Test ASR",
            "max_chunk_seconds": 60,
            "supports_segment_timestamps": True,
        },
    ).json()
    assert asr_gateway_model["gateway_base_url"] == "http://127.0.0.1:18101"
    ocr_gateway_model = client.post(
        "/api/v1/gateway-models",
        json={
            "gateway_id": ocr_gateway["id"],
            "model_name": "test-ocr",
            "display_name": "Test OCR",
        },
    ).json()
    assert asr_gateway["secret_env_name"] == "TEST_ASR_SECRET"
    assert "secret-value" not in str(asr_gateway)

    profile_payload = {
        "name": "High quality simulation",
        "description": "Configuration-only preset used by the simulated Pipeline",
        "is_default": True,
        "asr_gateway_model_id": asr_gateway_model["id"],
        "ocr_gateway_model_id": ocr_gateway_model["id"],
        "text_model_id": models["gpt-5-mini"]["id"],
        "ocr_enabled": True,
    }
    profile_response = client.post("/api/v1/profiles", json=profile_payload)
    assert profile_response.status_code == 201, profile_response.text
    profile = profile_response.json()
    assert profile["asr_selection"] == {
        "capability": "ASR",
        "source": "GATEWAY",
        "model_id": asr_gateway_model["id"],
        "model_name": "test-asr",
        "display_name": "Test ASR",
        "gateway_id": asr_gateway["id"],
        "gateway_name": "test-asr-gateway",
        "protocol": "video2doc-asr-http-v1",
    }
    assert profile["ocr_selection"]["display_name"] == "Test OCR"
    assert profile["text_selection"]["display_name"] == "gpt-5-mini"

    invalid = client.post(
        "/api/v1/profiles",
        json={
            "name": "Invalid profile",
            "asr_gateway_model_id": ocr_gateway_model["id"],
        },
    )
    assert invalid.status_code == 409

    settings_response = client.put(
        "/api/v1/settings",
        json={"last_profile_id": profile["id"], "default_keep_timestamps": True},
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["last_profile_id"] == profile["id"]
    assert "secret-value" not in settings_response.text
    # Environment-variable names are configuration references, not secret values.
    # They must remain visible so snapshots can be reproduced without persisting
    # the referenced credential itself.
    assert "oneapi_api_key" not in settings_response.json()
    assert "oneapi_api_key_ref" not in settings_response.json()
    unsafe_inbox = client.put(
        "/api/v1/settings",
        json={
            "obsidian_vault_path": "D:\\Video2Doc-Test\\Obsidian-Test-Vault",
            "inbox_path": "..\\outside",
        },
    )
    assert unsafe_inbox.status_code == 409

    created = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "SIMULATED",
            "inputs": [{"source": "https://example.invalid/lecture", "source_type": "URL"}],
            "profile_id": profile["id"],
            "config_overrides": {"ocr_enabled": False},
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()[0]["id"]
    snapshot_before = client.get(f"/api/v1/tasks/{task_id}/snapshot").json()
    assert snapshot_before["schema_version"] == 6
    assert snapshot_before["processing"]["prompt_version"] == "faithful-article-v1"
    assert snapshot_before["text_processing"]["timeout_seconds"] == 3600.0
    assert snapshot_before["processing"]["markdown_template_version"] == "obsidian-v1"
    assert snapshot_before["export"]["conflict_policy"] == "rename"
    assert snapshot_before["export"]["keep_json_sidecar"] is True
    assert snapshot_before["processing"]["frame_sampling_strategy"] == "hybrid"
    assert snapshot_before["processing"]["fusion_window_seconds"] == 8.0
    assert snapshot_before["models"]["asr"] == "test-asr"
    assert snapshot_before["gateways"]["asr"]["secret_env_name"] == "TEST_ASR_SECRET"
    assert snapshot_before["gateways"]["asr"]["chunking"]["overlap_seconds"] == 5.0
    assert snapshot_before["gateways"]["asr"]["timeout_seconds"] == 45.0
    assert snapshot_before["gateways"]["asr"]["max_retries"] == 3
    assert snapshot_before["gateways"]["ocr"]["gateway_type"] == "OCR"
    assert snapshot_before["gateways"]["ocr"]["protocol"] == "video2doc-ocr-http-v1"
    assert snapshot_before["ocr"]["language"] == "ch"
    assert snapshot_before["ocr"]["batch_size"] == 1
    assert snapshot_before["ocr"]["capabilities"]["supports_confidence"] is False
    assert snapshot_before["ocr"]["capabilities"]["languages"] == []
    assert snapshot_before["processing"]["ocr_enabled"] is False

    updated = client.put(
        f"/api/v1/profiles/{profile['id']}",
        json={"remove_fillers": False, "ocr_enabled": True},
    )
    assert updated.status_code == 200, updated.text
    snapshot_after = client.get(f"/api/v1/tasks/{task_id}/snapshot").json()
    assert snapshot_after == snapshot_before
    assert snapshot_after["processing"]["remove_fillers"] is True

    completed = wait_for_status(client, task_id, {"COMPLETED"})
    assert completed["output_path"]
    assert completed["progress"] == 100
    assert all(stage["stage"] != "OCR_PROCESSING" for stage in completed["stages"])


def test_dashscope_task_snapshot_uses_one_continuous_stream_without_overlap(
    client: TestClient,
) -> None:
    models = client.get("/api/v1/gateway-models?gateway_type=ASR").json()
    fun_asr = next(item for item in models if item["model_name"] == "fun-asr-realtime-2026-02-28")
    response = client.post(
        "/api/v1/tasks",
        json={
            "pipeline_mode": "SIMULATED",
            "inputs": [{"source": "https://example.invalid/fun", "source_type": "URL"}],
            "asr_gateway_model_id": fun_asr["id"],
        },
    )
    assert response.status_code == 201, response.text
    snapshot = client.get(f"/api/v1/tasks/{response.json()[0]['id']}/snapshot").json()
    chunking = snapshot["gateways"]["asr"]["chunking"]
    assert chunking == {
        "mode": "continuous_stream",
        "chunk_seconds": None,
        "overlap_seconds": 0.0,
        "step_seconds": None,
    }
    assert snapshot["asr"]["streaming"] is True


def test_startup_seed_preserves_existing_gateway_model_customization(
    client: TestClient,
) -> None:
    models = client.get("/api/v1/gateway-models?gateway_type=ASR").json()
    whisper = next(item for item in models if item["model_name"] == "large-v3-turbo")
    changed = client.put(
        f"/api/v1/gateway-models/{whisper['id']}",
        json={"display_name": "Locally tuned turbo", "max_chunk_seconds": 28},
    )
    assert changed.status_code == 200, changed.text

    try:
        with SessionLocal() as session:
            seed_default_gateways(session)
        refreshed = next(
            item
            for item in client.get("/api/v1/gateway-models?gateway_type=ASR").json()
            if item["id"] == whisper["id"]
        )
        assert refreshed["display_name"] == "Locally tuned turbo"
        assert refreshed["max_chunk_seconds"] == 28
    finally:
        restored = client.put(
            f"/api/v1/gateway-models/{whisper['id']}",
            json={
                "display_name": "Whisper large-v3-turbo (speed)",
                "max_chunk_seconds": 30,
            },
        )
        assert restored.status_code == 200, restored.text
