"""Unit tests for `SteamWorkshop.GetQueryUGCResult` (`R1111-P3-10`)."""

from __future__ import annotations

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
