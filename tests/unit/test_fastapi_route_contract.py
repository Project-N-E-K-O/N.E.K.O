from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

from tests.fastapi_routes import iter_routes


pytestmark = pytest.mark.unit


def test_route_inspection_preserves_nested_prefixes_methods_and_endpoints():
    leaf = APIRouter()

    @leaf.get("/probe")
    def probe():
        return {"ok": True}

    parent = APIRouter()
    parent.include_router(leaf, prefix="/first")
    parent.include_router(leaf, prefix="/second")
    app = FastAPI()
    app.include_router(parent, prefix="/api")

    matching = [route for route in iter_routes(app.routes) if route.endpoint is probe]
    assert [route.path for route in matching] == ["/api/first/probe", "/api/second/probe"]
    assert all("GET" in route.methods for route in matching)
    with TestClient(app) as client:
        for route in matching:
            response = client.get(route.path)
            assert response.status_code == 200
            assert response.json() == {"ok": True}


def test_route_inspection_does_not_hide_duplicate_registrations():
    router = APIRouter()

    @router.get("/probe")
    def probe():
        return {"ok": True}

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(router, prefix="/api")

    assert sum(route.path == "/api/probe" for route in iter_routes(app.routes)) == 2
