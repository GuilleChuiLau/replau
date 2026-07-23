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


def test_offer_shows_complete_trip_fee_and_maps(monkeypatch):
    module = load_app()

    def fake_get(path):
        if path.startswith("/v_driver_accounts"):
            return [{"id": 1, "status": "ACTIVE", "repartidor_id": 2, "legal_name": "Test Driver"}]
        if path.startswith("/driver_online_sessions"):
            return [{"id": 9, "started_at": "now"}]
        if path.startswith("/v_delivery_offer_candidates"):
            return [{
                "id": 10, "pedido_id": 20, "pedido_num": "PED-20", "pedido_estado": "CONFIRMADO",
                "pickup_codigo": "STORE", "pickup_direccion": "Pickup 123",
                "pickup_latitude": -12.10, "pickup_longitude": -77.03,
                "distance_km": 1.2, "eta_seconds": 240, "status": "OFFERED",
            }]
        if path.startswith("/v_delivery_asignaciones"):
            return []
        if path.startswith("/v_pedidos_logistica"):
            return [{"id": 20, "direccion_confirmada": "Customer 456", "latitud": -12.12, "longitud": -77.01}]
        if path.startswith("/v_order_pickup_points"):
            return []
        raise AssertionError(path)

    monkeypatch.setattr(module, "pg_get", fake_get)
    monkeypatch.setattr(module, "pg_rpc", lambda name, payload: 7)
    client = TestClient(module.app)
    response = client.get("/driver/app/1", headers=basic("driver-test", "correct-horse-test"))
    assert response.status_code == 200
    assert "Driver fee:</strong> S/ 7" in response.text
    assert "Pickup 123" in response.text
    assert "Customer 456" in response.text
    assert "Pickup map" in response.text
    assert "Customer map" in response.text
    assert "Full route" in response.text
