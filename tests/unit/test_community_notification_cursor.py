"""Desktop relay pagination must follow the real Verse cursor contract."""

import httpx
import pytest

from main_routers import community_remote_proxy as proxy


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 99, 100, 101])
async def test_cursor_pages_are_complete_and_bounded(count):
    seen = []

    async def upstream(request):
        assert "offset" not in request.url.params
        cursor = request.url.params.get("cursor")
        seen.append(cursor)
        offset = int(cursor or 0)
        items = [{"id": str(i)} for i in range(offset, min(offset + 100, count))]
        return httpx.Response(200, json={
            "items": items,
            "next_cursor": str(offset + 100) if len(items) == 100 else None,
            "total_count": count,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        status, body = await proxy._community_notifications(
            client, {"base_url": "https://synthetic.example", "access": "synthetic"},
        )
    assert status == 200
    assert [x["id"] for x in body["items"]] == [str(i) for i in range(count)]
    assert seen == ([None] if count < 100 else [None, "100"])


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["repeat", "", 123, [], {"items": []}, "empty-page"])
async def test_invalid_cursor_never_becomes_an_authoritative_partial_snapshot(bad):
    calls = 0

    async def upstream(request):
        nonlocal calls
        calls += 1
        if bad == "repeat":
            body = {"items": [{"id": "one"}], "next_cursor": "same"}
        elif bad == "empty-page":
            body = {"items": [], "next_cursor": "more"}
        else:
            body = {"items": [{"id": "one"}], "next_cursor": bad}
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        status, body = await proxy._community_notifications(
            client, {"base_url": "https://synthetic.example", "access": "synthetic"},
        )
    assert status == 502
    assert "items" not in body
    assert calls <= 2


def test_read_decisions_do_not_reappear_as_pending_invitations():
    body = proxy._visit_invitation_snapshot("https://synthetic.example", 200, {"items": [
        {"id": "accepted", "payload": {"type": "visit_invite", "response": "accepted"}},
        {"id": "declined", "payload": {"type": "visit_invite", "response": "declined"}},
        {"id": "pending", "is_read": True, "payload": {"type": "visit_invite"}},
    ]})
    assert body["incoming_invite_ids"] == ["pending"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], {"items": [{}] * 100}, {"items": None}])
async def test_malformed_pages_cannot_claim_completeness(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload),
    )) as client:
        status, body = await proxy._community_notifications(
            client, {"base_url": "https://synthetic.example", "access": "synthetic"},
        )
    assert status == 502
    assert "items" not in body
