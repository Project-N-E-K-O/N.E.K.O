from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from plugin.core.image_store import ImageStore, get_image_store
from plugin.server.routes.media import router


pytestmark = pytest.mark.plugin_unit


def test_image_store_and_http_route_share_the_same_temporary_image() -> None:
    store = get_image_store()
    store.clear()
    image_bytes = b"synthetic-jpeg-bytes"

    image_id = store.put(image_bytes, mime="image/jpeg")

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(f"/media/{image_id}")

    assert response.status_code == 200
    assert response.content == image_bytes
    assert response.headers["content-type"] == "image/jpeg"


def test_image_store_evicts_least_recently_used_data_by_byte_budget() -> None:
    store = ImageStore(max_bytes=6)
    first_id = store.put(b"111", mime="image/jpeg")
    second_id = store.put(b"22", mime="image/jpeg")
    assert store.get(first_id) is not None  # first is now the most recently used

    third_id = store.put(b"333", mime="image/jpeg")

    assert store.get(first_id) is not None
    assert store.get(second_id) is None
    assert store.get(third_id) is not None
