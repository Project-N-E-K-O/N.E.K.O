import json
from types import SimpleNamespace

import pytest

from main_routers import workshop_router
from main_routers.workshop_router import items as wr_items
from main_routers.workshop_router import ugc as wr_ugc


class _FakeWorkshop:
    def __init__(self, download_info=None, item_state=None, subscribed_items=None):
        self._download_info = download_info or {}
        self._item_state = (
            workshop_router._ITEM_STATE_SUBSCRIBED
            if item_state is None
            else item_state
        )
        self._subscribed_items = [123456] if subscribed_items is None else subscribed_items

    def GetItemState(self, item_id):
        return self._item_state

    def GetItemInstallInfo(self, item_id):
        return {}

    def GetItemDownloadInfo(self, item_id):
        return self._download_info

    def GetNumSubscribedItems(self):
        return len(self._subscribed_items)

    def GetSubscribedItems(self):
        return self._subscribed_items


@pytest.fixture
def unsupported_ugc_steamworks():
    # Intentionally omit Workshop_CreateQueryUGCDetailsRequest and friends.
    # This mirrors Linux wrappers that can enumerate subscriptions but cannot
    # query rich UGC metadata.
    return SimpleNamespace(Workshop=_FakeWorkshop())


@pytest.mark.asyncio
async def test_workshop_item_details_reports_unsupported_ugc_details(monkeypatch, unsupported_ugc_steamworks):
    monkeypatch.setattr(wr_items, "get_steamworks", lambda: unsupported_ugc_steamworks)
    monkeypatch.setattr(wr_ugc, "get_steamworks", lambda: unsupported_ugc_steamworks)

    response = await workshop_router.get_workshop_item_details("123456")

    assert response["success"] is True
    assert response["partial"] is True
    assert response["detailsAvailable"] is False
    assert response["detailsUnavailableReason"] == "ugc_details_query_unsupported"
    assert response["item"]["publishedFileId"] == 123456


@pytest.mark.asyncio
async def test_workshop_item_details_unsupported_uses_download_tuple_order(monkeypatch):
    steamworks = SimpleNamespace(Workshop=_FakeWorkshop(download_info=(25, 100, 0.25)))
    monkeypatch.setattr(wr_items, "get_steamworks", lambda: steamworks)
    monkeypatch.setattr(wr_ugc, "get_steamworks", lambda: steamworks)

    response = await workshop_router.get_workshop_item_details("123456")

    progress = response["item"]["downloadProgress"]
    assert progress["bytesDownloaded"] == 25
    assert progress["bytesTotal"] == 100
    assert progress["percentage"] == 25


@pytest.mark.asyncio
async def test_workshop_item_details_unsupported_preserves_not_found_for_unknown_id(monkeypatch):
    steamworks = SimpleNamespace(Workshop=_FakeWorkshop(item_state=0, subscribed_items=[]))
    monkeypatch.setattr(wr_items, "get_steamworks", lambda: steamworks)
    monkeypatch.setattr(wr_ugc, "get_steamworks", lambda: steamworks)

    response = await workshop_router.get_workshop_item_details("999999")

    assert response.status_code == 404
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["success"] is False
    assert payload["detailsUnavailableReason"] == "ugc_details_query_unsupported"


@pytest.mark.asyncio
async def test_workshop_item_details_unsupported_tolerates_dirty_subscribed_items(monkeypatch):
    steamworks = SimpleNamespace(
        Workshop=_FakeWorkshop(
            item_state=0,
            subscribed_items=[None, "not-a-number", "123456"],
        )
    )
    monkeypatch.setattr(wr_items, "get_steamworks", lambda: steamworks)
    monkeypatch.setattr(wr_ugc, "get_steamworks", lambda: steamworks)

    response = await workshop_router.get_workshop_item_details("123456")

    assert response["success"] is True
    assert response["partial"] is True
    assert response["item"]["publishedFileId"] == 123456


@pytest.mark.asyncio
async def test_subscribed_workshop_items_degrades_when_ugc_details_unsupported(
    monkeypatch,
    unsupported_ugc_steamworks,
):
    monkeypatch.setattr(wr_items, "get_steamworks", lambda: unsupported_ugc_steamworks)
    monkeypatch.setattr(wr_ugc, "get_steamworks", lambda: unsupported_ugc_steamworks)
    monkeypatch.setattr(wr_ugc, "_request_workshop_item_download", lambda *args, **kwargs: False)
    monkeypatch.setattr(wr_items, "_request_workshop_item_download", lambda *args, **kwargs: False)

    response = await workshop_router.get_subscribed_workshop_items()

    assert response["success"] is True
    assert response["total"] == 1
    assert response["items"][0]["publishedFileId"] == "123456"
    assert response["items"][0]["title"] == "未知物品_123456"
