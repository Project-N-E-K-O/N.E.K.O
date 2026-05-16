from types import SimpleNamespace

import pytest

from main_routers import workshop_router


class _FakeWorkshop:
    def __init__(self, download_info=None):
        self._download_info = download_info or {}

    def GetItemState(self, item_id):
        return workshop_router._ITEM_STATE_SUBSCRIBED

    def GetItemInstallInfo(self, item_id):
        return {}

    def GetItemDownloadInfo(self, item_id):
        return self._download_info

    def GetNumSubscribedItems(self):
        return 1

    def GetSubscribedItems(self):
        return [123456]


@pytest.fixture
def unsupported_ugc_steamworks():
    # Intentionally omit Workshop_CreateQueryUGCDetailsRequest and friends.
    # This mirrors Linux wrappers that can enumerate subscriptions but cannot
    # query rich UGC metadata.
    return SimpleNamespace(Workshop=_FakeWorkshop())


@pytest.mark.asyncio
async def test_workshop_item_details_reports_unsupported_ugc_details(monkeypatch, unsupported_ugc_steamworks):
    monkeypatch.setattr(workshop_router, "get_steamworks", lambda: unsupported_ugc_steamworks)

    response = await workshop_router.get_workshop_item_details("123456")

    assert response["success"] is True
    assert response["partial"] is True
    assert response["detailsAvailable"] is False
    assert response["detailsUnavailableReason"] == "ugc_details_query_unsupported"
    assert response["item"]["publishedFileId"] == 123456


@pytest.mark.asyncio
async def test_workshop_item_details_unsupported_uses_download_tuple_order(monkeypatch):
    steamworks = SimpleNamespace(Workshop=_FakeWorkshop(download_info=(25, 100, 0.25)))
    monkeypatch.setattr(workshop_router, "get_steamworks", lambda: steamworks)

    response = await workshop_router.get_workshop_item_details("123456")

    progress = response["item"]["downloadProgress"]
    assert progress["bytesDownloaded"] == 25
    assert progress["bytesTotal"] == 100
    assert progress["percentage"] == 25


@pytest.mark.asyncio
async def test_subscribed_workshop_items_degrades_when_ugc_details_unsupported(
    monkeypatch,
    unsupported_ugc_steamworks,
):
    monkeypatch.setattr(workshop_router, "get_steamworks", lambda: unsupported_ugc_steamworks)
    monkeypatch.setattr(workshop_router, "_request_workshop_item_download", lambda *args, **kwargs: False)

    response = await workshop_router.get_subscribed_workshop_items()

    assert response["success"] is True
    assert response["total"] == 1
    assert response["items"][0]["publishedFileId"] == "123456"
    assert response["items"][0]["title"] == "未知物品_123456"
