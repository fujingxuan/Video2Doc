from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import wait_for_status


def test_health_models_profiles_settings_and_immutable_snapshot(client: TestClient) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    assert health.json()["queue_worker"] == "running"

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

    profile_payload = {
        "name": "High quality simulation",
        "description": "Configuration-only preset used by the simulated Pipeline",
        "is_default": True,
        "asr_model_id": models["whisper-large-v3"]["id"],
        "ocr_model_id": models["paddleocr"]["id"],
        "text_model_id": models["gpt-5-mini"]["id"],
        "ocr_enabled": True,
    }
    profile_response = client.post("/api/v1/profiles", json=profile_payload)
    assert profile_response.status_code == 201, profile_response.text
    profile = profile_response.json()

    invalid = client.post(
        "/api/v1/profiles",
        json={
            "name": "Invalid profile",
            "asr_model_id": models["paddleocr"]["id"],
        },
    )
    assert invalid.status_code == 409

    settings_response = client.put(
        "/api/v1/settings",
        json={"last_profile_id": profile["id"], "default_keep_timestamps": True},
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["last_profile_id"] == profile["id"]
    assert "oneapi_api_key" not in settings_response.text.lower()

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
    assert snapshot_before["models"]["asr"] == "whisper-large-v3"
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
