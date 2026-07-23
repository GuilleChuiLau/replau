import base64
import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient


APP_PATH = Path(__file__).with_name("replau_driver_app.py")


def load_app():
    os.environ["REQUIRE_DRIVER_AUTH"] = "true"
    os.environ["DRIVER_AUTH_USERNAME"] = "driver-test"
    os.environ["DRIVER_AUTH_PASSWORD"] = "correct-horse-test"
    spec = importlib.util.spec_from_file_location("replau_driver_app_test", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def basic(username: str, password: str) -> dict[str, str]:
    value = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def test_driver_routes_require_authentication():
    module = load_app()
    client = TestClient(module.app)

    response = client.get("/driver")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")

    response = client.get("/driver", headers=basic("driver-test", "wrong"))
    assert response.status_code == 401


def test_valid_auth_reaches_driver_route(monkeypatch):
    module = load_app()
    client = TestClient(module.app)

    response = client.get("/driver", headers=basic("driver-test", "correct-horse-test"))
    assert response.status_code == 200
    assert "Replau Driver" in response.text
    assert "useCurrentLocation" in response.text


def test_health_stays_available_without_driver_credentials(monkeypatch):
    module = load_app()
    monkeypatch.setattr(module, "pg_get", lambda path: [])
    client = TestClient(module.app)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
