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


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size,total", [(0, 1), (1, 2), (100, 101)])
async def test_terminal_page_cannot_contradict_a_larger_total(page_size, total):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "items": [{"id": str(i)} for i in range(page_size)],
            "next_cursor": None, "total_count": total,
        }),
    )) as client:
        status, body = await proxy._community_notifications(
            client, {"base_url": "https://synthetic.example", "access": "synthetic"},
        )
    assert status == 502 and "items" not in body
    snapshot = proxy._visit_invitation_snapshot("https://synthetic.example", status, body)
    assert snapshot["notifications_ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("total,expected", [(99, 200), (100, 200), (101, 502), (None, 200)])
async def test_terminal_total_is_checked_against_all_collected_pages(total, expected):
    seen = []

    async def upstream(request):
        seen.append(request.url.params.get("cursor"))
        if len(seen) == 1:
            return httpx.Response(200, json={"items": [{"id": str(i)} for i in range(100)],
                                            "next_cursor": "next", "total_count": 101})
        return httpx.Response(200, json={"items": [], "next_cursor": None, "total_count": total})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        status, body = await proxy._community_notifications(
            client, {"base_url": "https://synthetic.example", "access": "synthetic"},
        )
    assert status == expected and seen == [None, "next"]
    if status == 200:
        assert len(body["items"]) == 100
    else:
        assert "items" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_ignores_filter", [False, True])
async def test_verse_total_contract_with_non_invitation_unread_notifications(legacy_ignores_filter):
    # Verse notifications/service.py uses the same filters for total_count and
    # items. unread_count is a separate, potentially wider account-wide count.
    # Older Verse ignores visit_invitations and returns every unread row.
    invitation = {"id": "incoming", "is_read": False, "payload": {
        "type": "visit_invite", "actor_id": "friend", "visit_id": "A" * 22,
        "expires_at": 9_000_000_000,
    }}
    read_invitation = {**invitation, "id": "read-pending", "is_read": True}
    response = {"id": "reply", "is_read": False, "payload": {
        "type": "visit_invite_response", "visit_id": "B" * 22, "accepted": False,
    }}
    ordinary = [{"id": f"like-{i}", "is_read": False, "payload": {"type": "like"}} for i in range(100)]
    rows = ordinary + [invitation, response] if legacy_ignores_filter else [read_invitation, invitation, response]
    seen = []

    async def upstream(request):
        assert request.url.params["unread"] == "true"
        assert request.url.params["visit_invitations"] == "true"
        cursor = request.url.params.get("cursor")
        seen.append(cursor)
        offset = 100 if cursor == "opaque-next-page" else 0
        page = rows[offset:offset + 100]
        return httpx.Response(200, json={
            "items": page, "total_count": len(rows), "unread_count": 102,
            "next_cursor": "opaque-next-page" if len(page) == 100 else None,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        status, body = await proxy._community_notifications(
            client, {"base_url": "https://synthetic.example", "access": "synthetic"},
        )
    assert status == 200
    assert body["total_count"] == len(body["items"]) == (102 if legacy_ignores_filter else 3)
    assert body["unread_count"] == 102
    snapshot = proxy._visit_invitation_snapshot("https://synthetic.example", status, body)
    assert snapshot["notifications_ok"] is True
    assert set(snapshot["incoming_invite_ids"]) == ({"incoming"} if legacy_ignores_filter else {"incoming", "read-pending"})
    assert [item["id"] for item in snapshot["invite_responses"]] == ["reply"]
    assert seen == ([None, "opaque-next-page"] if legacy_ignores_filter else [None])
