"""Unit tests for `SteamWorkshop` (`R1111-P3-10` & `B27-004`)."""

from __future__ import annotations

import pytest

from steamworks.interfaces.workshop import SteamWorkshop
from steamworks.structs import SteamUGCDetails_t


class _FakeSteam:
    def __init__(self, *, query_ok: bool, published_file_id: int = 0, title: bytes = b""):
        self._query_ok = query_ok
        self._published_file_id = published_file_id
        self._title = title

    def loaded(self) -> bool:
        return True

    def Workshop_GetNumSubscribedItems(self) -> int:
        return 0

    def Workshop_GetQueryUGCResult(self, _handle: int, _index: int, details_ptr) -> bool:
        details_ptr._obj.publishedFileId = self._published_file_id
        if self._title:
            details_ptr._obj.title = self._title
        return self._query_ok


class _FakeUpdateSteam:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, bytes]] = []

    @staticmethod
    def loaded() -> bool:
        return True

    @staticmethod
    def Workshop_GetNumSubscribedItems() -> int:
        return 0

    def Workshop_SetItemTitle(self, update_handle: int, title_bytes: bytes) -> bool:
        self.calls.append(("title", update_handle, title_bytes))
        return True

    def Workshop_SetItemDescription(self, update_handle: int, desc_bytes: bytes) -> bool:
        self.calls.append(("description", update_handle, desc_bytes))
        return True


def test_get_query_ugc_result_returns_details_when_native_call_succeeds():
    """Return populated `SteamUGCDetails_t` when `Workshop_GetQueryUGCResult` returns `True`."""
    workshop = SteamWorkshop(_FakeSteam(query_ok=True, published_file_id=987654321, title=b"Test Card"))

    result = workshop.GetQueryUGCResult(42, 0)

    assert isinstance(result, SteamUGCDetails_t)
    assert result.publishedFileId == 987654321
    assert result.title == b"Test Card"


def test_get_query_ugc_result_returns_empty_dict_when_native_call_fails():
    """Return `{}` when `Workshop_GetQueryUGCResult` returns `False`, matching sibling methods."""
    # Populate a nonzero publishedFileId in the buffer to prove the boolean check gates the return.
    workshop = SteamWorkshop(_FakeSteam(query_ok=False, published_file_id=999999999, title=b"Stale"))

    result = workshop.GetQueryUGCResult(42, 0)

    assert result == {}


@pytest.mark.unit
def test_set_item_title_enforces_utf8_byte_limit():
    steam = _FakeUpdateSteam()
    ws = SteamWorkshop(steam)

    ascii_ok = "a" * 128
    assert ws.SetItemTitle(1, ascii_ok) is True
    assert steam.calls[-1] == ("title", 1, ascii_ok.encode("utf-8"))

    with pytest.raises(AttributeError, match="128 UTF-8 bytes"):
        ws.SetItemTitle(1, "a" * 129)

    # 42 CJK chars (126 bytes) + 2 ASCII (2 bytes) = 128 UTF-8 bytes -> valid
    cjk_ok = ("猫" * 42) + "ab"
    assert len(cjk_ok.encode("utf-8")) == 128
    assert ws.SetItemTitle(2, cjk_ok) is True
    assert steam.calls[-1] == ("title", 2, cjk_ok.encode("utf-8"))

    # 43 CJK chars = 43 characters (< 128) but 129 UTF-8 bytes -> must reject
    cjk_over = "猫" * 43
    assert len(cjk_over) == 43
    assert len(cjk_over.encode("utf-8")) == 129
    with pytest.raises(AttributeError, match="128 UTF-8 bytes"):
        ws.SetItemTitle(2, cjk_over)

    # 33 4-byte emoji = 132 UTF-8 bytes -> must reject
    with pytest.raises(AttributeError, match="128 UTF-8 bytes"):
        ws.SetItemTitle(2, "🐱" * 33)


@pytest.mark.unit
def test_set_item_description_enforces_utf8_byte_limit():
    steam = _FakeUpdateSteam()
    ws = SteamWorkshop(steam)

    ascii_ok = "d" * 8000
    assert ws.SetItemDescription(10, ascii_ok) is True
    assert steam.calls[-1] == ("description", 10, ascii_ok.encode("utf-8"))

    with pytest.raises(AttributeError, match="8000 UTF-8 bytes"):
        ws.SetItemDescription(10, "d" * 8001)

    # 2666 CJK chars (7998 bytes) + "xy" (2 bytes) = 8000 UTF-8 bytes -> valid
    cjk_ok = ("文" * 2666) + "xy"
    assert len(cjk_ok.encode("utf-8")) == 8000
    assert ws.SetItemDescription(11, cjk_ok) is True
    assert steam.calls[-1] == ("description", 11, cjk_ok.encode("utf-8"))

    # 2667 CJK chars = 2667 characters (< 8000) but 8001 UTF-8 bytes -> must reject
    cjk_over = "文" * 2667
    assert len(cjk_over) == 2667
    assert len(cjk_over.encode("utf-8")) == 8001
    with pytest.raises(AttributeError, match="8000 UTF-8 bytes"):
        ws.SetItemDescription(11, cjk_over)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publish_to_workshop_rejects_oversized_utf8_before_create_item(
    tmp_path, monkeypatch
):
    """Over-limit CJK title/description must return HTTP 400 before CreateItem allocates a remote item."""
    import json
    from main_routers.workshop_router import publish as wr_publish

    content_dir = tmp_path / "card_pkg"
    content_dir.mkdir()
    (content_dir / "card.json").write_text("{}", encoding="utf-8")

    create_calls: list[tuple[object, ...]] = []

    class _FailIfCreateWorkshop:
        def SetItemCreatedCallback(self, _cb):
            create_calls.append(("SetItemCreatedCallback",))

        def CreateItem(self, *args):
            create_calls.append(("CreateItem", *args))

    class _FakeSteamworks:
        app_id = 480
        Workshop = _FailIfCreateWorkshop()

        @staticmethod
        def IsSteamRunning() -> bool:
            return True

    async def _fake_workshop_path() -> str:
        return str(tmp_path)

    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: _FakeSteamworks())
    monkeypatch.setattr(wr_publish, "get_workshop_path_async", _fake_workshop_path)

    class _Req:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        async def json(self) -> dict[str, object]:
            return self._payload

    title_resp = await wr_publish.publish_to_workshop(
        _Req(
            {
                "title": "猫" * 43,  # 129 UTF-8 bytes > 128
                "description": "正常简介",
                "content_folder": str(content_dir),
                "visibility": 0,
            }
        )
    )
    assert title_resp.status_code == 400
    assert json.loads(title_resp.body)["success"] is False
    assert create_calls == []

    desc_resp = await wr_publish.publish_to_workshop(
        _Req(
            {
                "title": "正常标题",
                "description": "文" * 2667,  # 8001 UTF-8 bytes > 8000
                "content_folder": str(content_dir),
                "visibility": 0,
            }
        )
    )
    assert desc_resp.status_code == 400
    assert json.loads(desc_resp.body)["success"] is False
    assert create_calls == []

    with pytest.raises(AttributeError, match="128 UTF-8 bytes"):
        wr_publish._publish_workshop_item(
            _FakeSteamworks(), "猫" * 43, "ok", str(content_dir), "", 0, [], "note"
        )
    with pytest.raises(AttributeError, match="8000 UTF-8 bytes"):
        wr_publish._publish_workshop_item(
            _FakeSteamworks(), "ok", "文" * 2667, str(content_dir), "", 0, [], "note"
        )
    assert create_calls == []
