import pytest
from plugin.server.application.conversations import ConversationService
from plugin.sdk.shared.conversations.client import SdkConversationsV2Bus
from plugin.sdk.shared.contracts.conversations import message_from_event


def raw_message(
    mid, *, input_id=None, reply_id=None, text="message", role="assistant_message"
):
    return {
        "index": {"id": mid, "timestamp": 1.0},
        "payload": {
            "content": text,
            "input_id": input_id,
            "reply_id": reply_id,
            "metadata": {"turn_type": role, "lanlan_name": "YUI"},
        },
    }


def test_reply_and_input_facts_are_independent_and_linked_by_id():
    current = {}
    svc = ConversationService(lambda mid: current.get(mid))
    assert svc.get_reply("missing") is None
    assert svc.accept_fact(
        "input.submitted",
        {
            "input_id": "i",
            "character": "YUI",
            "text": "question",
            "input_source": "text",
            "observed_at": 1.0,
        },
    )
    assert (
        svc.accept_fact(
            "reply.started",
            {
                "reply_id": "r",
                "input_id": "i",
                "character": "YUI",
                "text": "draft",
                "observed_at": 2.0,
            },
        )[1]["reply"]["text"]
        == "draft"
    )
    closed = svc.accept_fact(
        "reply.closed",
        {
            "reply_id": "r",
            "input_id": "i",
            "character": "YUI",
            "text": "complete",
            "close_reason": "completed",
            "observed_at": 3.0,
        },
    )
    assert closed[1]["reply"]["text_state"] == "closed"
    assert svc.accept_fact("reply.started", {"reply_id": "r", "text": "stale"}) is None
    assert svc.get_reply("r")["text"] == "complete"
    assert svc.accept_fact(
        "playback.started",
        {"reply_id": "r", "presentation_id": "p", "presenter_id": "window"},
    )
    assert svc.get_reply("r")["playbacks"][0]["state"] == "playing"
    assert svc.get_interaction("i")["replies"][0]["id"] == "r"
    msg = raw_message("m", input_id="i", reply_id="r", text="stored")
    current["m"] = message_from_event(msg)
    svc.accept_message(msg)
    assert svc.get_reply("r")["text"] == "stored"
    current["m"]["text"] = "corrected"
    assert svc.get_reply("r")["text"] == "corrected"
    svc.accept_message(raw_message("u", input_id="i", role="user_message"))
    assert (
        svc.get_interaction("i")["input"]["observed_outcome"] == "entered_conversation"
    )
    svc.close()
    assert svc.get_interaction("i") is None


def test_late_linkage_and_malformed_facts():
    msg = raw_message("m", input_id="i", reply_id="r")
    svc = ConversationService(
        lambda mid: message_from_event(msg) if mid == "m" else None
    )
    svc.accept_message(msg)
    svc.accept_message(raw_message("u", input_id="i", role="user_message"))
    svc.accept_fact(
        "input.submitted",
        {
            "input_id": "i",
            "character": "YUI",
            "text": "hi",
            "input_source": "text",
            "observed_at": 1.0,
        },
    )
    svc.accept_fact(
        "reply.started", {"reply_id": "r", "character": "YUI", "text": "draft"}
    )
    assert svc.get_reply("r")["text"] == "message"
    assert (
        svc.get_interaction("i")["input"]["observed_outcome"] == "entered_conversation"
    )
    assert len(svc.get_interaction("i")["replies"]) == 1
    assert (
        svc.accept_fact(
            "reply.updated", {"reply_id": "r", "character": "OTHER", "text": "wrong"}
        )
        is None
    )
    with pytest.raises(Exception) as too_large:
        svc.accept_fact("reply.updated", {"reply_id": "r", "text": "x" * 100000})
    assert too_large.value.code == "CONVERSATION_DETAIL_TOO_LARGE"
    with pytest.raises(Exception):
        svc.get_reply("r")
    assert (
        message_from_event(
            {
                "index": {"id": "u", "timestamp": 1},
                "payload": {
                    "content": "x",
                    "metadata": {"turn_type": "user_message", "input_id": "i"},
                },
            }
        )["input_id"]
        == "i"
    )


@pytest.mark.asyncio
async def test_sdk_detail_decodes_and_uses_rpc():
    svc = ConversationService(lambda mid: None)
    svc.accept_fact(
        "input.submitted",
        {
            "input_id": "i",
            "character": "YUI",
            "text": "hi",
            "input_source": "text",
            "observed_at": 1.0,
        },
    )
    svc.accept_fact(
        "reply.started",
        {"reply_id": "r", "input_id": "i", "character": "YUI", "text": "answer"},
    )

    class Host:
        async def _send_request_and_wait_async(
            self, *, request_type, request_data, **kwargs
        ):
            return (
                svc.get_reply(request_data["reply_id"])
                if request_type == "CONVERSATION_REPLY_GET"
                else svc.get_interaction(request_data["input_id"])
            )

    bus = SdkConversationsV2Bus(object(), host_ctx=Host())
    assert (await bus.get_reply("r")).text == "answer"
    assert (await bus.get_interaction("i")).replies[0].id == "r"
    assert await bus.get_reply("unknown") is None


def test_ttl_and_total_budget_evict_without_partial_reply(monkeypatch):
    from plugin.server.application import conversations as module

    clock = [1000.0]
    from types import SimpleNamespace

    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000.0)
    )
    monkeypatch.setattr(module, "_MAX_CACHE_BYTES", 450)
    svc = ConversationService(lambda mid: None)
    svc.accept_fact("reply.started", {"reply_id": "old", "text": "old body"})
    for i in range(8):
        svc.accept_fact(
            "input.submitted",
            {
                "input_id": f"i{i}",
                "character": "YUI",
                "text": "x" * 50,
                "input_source": "text",
                "observed_at": 1.0,
            },
        )
    assert svc._retained_bytes <= 450
    assert svc.get_reply("old") is None
    clock[0] += 601
    assert svc.get_interaction("i7") is None
    assert svc._retained_bytes == 0


def test_early_playback_and_reply_correction_use_store():
    current = {"m": {"text": "stored", "origin": None}}
    changes = []

    def update(mid, fields):
        changes.append((mid, fields))
        current[mid].update(fields)
        return current[mid]

    svc = ConversationService(current.get, update)
    svc.accept_fact(
        "playback.started",
        {"reply_id": "r", "presentation_id": "p", "presenter_id": "window"},
    )
    svc.accept_message(raw_message("m", input_id="i", reply_id="r"))
    svc.accept_fact(
        "reply.started", {"reply_id": "r", "character": "YUI", "text": "old"}
    )
    assert svc.get_reply("r")["playbacks"][0]["state"] == "playing"
    event = svc.accept_fact(
        "reply.updated", {"reply_id": "r", "character": "YUI", "text": "corrected"}
    )
    assert changes == [("m", {"text": "corrected"})]
    assert event[1]["reply"]["text"] == svc.get_reply("r")["text"] == "corrected"


def test_playback_overflow_marks_detail_unavailable():
    svc = ConversationService(lambda mid: None)
    svc.accept_fact("reply.started", {"reply_id": "r", "input_id": "i", "text": "hi"})
    for i in range(32):
        svc.accept_fact(
            "playback.started", {"reply_id": "r", "presentation_id": str(i)}
        )
    with pytest.raises(Exception) as error:
        svc.accept_fact(
            "playback.started", {"reply_id": "r", "presentation_id": "more"}
        )
    assert error.value.code == "CONVERSATION_DETAIL_TOO_LARGE"
    with pytest.raises(Exception) as error:
        svc.get_reply("r")
    assert error.value.code == "CONVERSATION_DETAIL_TOO_LARGE"


def test_turn_alias_resolves_only_same_explicit_character_and_turn():
    svc = ConversationService(lambda mid: None)
    unknown = svc.accept_fact(
        "playback.started",
        {"turn_id": "turn", "character": "YUI", "presentation_id": "early"},
    )
    assert unknown[1]["reply_id"] is None
    svc.accept_fact(
        "reply.started",
        {"reply_id": "r1", "turn_id": "turn", "character": "YUI", "text": "hello"},
    )
    assert (
        svc.get_reply("r1")["playbacks"] == []
    )  # Earlier unlinked reports are not guessed retroactively.
    event = svc.accept_fact(
        "playback.started",
        {
            "turn_id": "turn",
            "character": "YUI",
            "presentation_id": "p1",
            "presenter_id": "w",
        },
    )
    assert event[1]["reply_id"] == "r1"
    svc.accept_fact(
        "playback.stopped",
        {
            "turn_id": "turn",
            "character": "YUI",
            "presentation_id": "p1",
            "presenter_id": "w",
            "stop_reason": "completed",
        },
    )
    svc.accept_fact(
        "playback.started",
        {
            "turn_id": "turn",
            "character": "YUI",
            "presentation_id": "p2",
            "presenter_id": "w",
        },
    )
    assert [
        (p["presentation_id"], p["state"]) for p in svc.get_reply("r1")["playbacks"]
    ] == [("p1", "stopped"), ("p2", "playing")]
    assert (
        svc.accept_fact("playback.started", {"turn_id": "turn", "character": "OTHER"})[
            1
        ]["reply_id"]
        is None
    )
    assert (
        svc.accept_fact("playback.started", {"turn_id": "turn"})[1]["reply_id"] is None
    )
    svc.accept_fact(
        "reply.started",
        {"reply_id": "r2", "turn_id": "turn", "character": "YUI", "text": "other"},
    )
    assert (
        svc.accept_fact("playback.started", {"turn_id": "turn", "character": "YUI"})[1][
            "reply_id"
        ]
        is None
    )
    # Explicit identity remains usable, but conflict is not resolved by a later repeated fact.
    assert (
        svc.accept_fact(
            "playback.started",
            {
                "reply_id": "r2",
                "turn_id": "turn",
                "character": "YUI",
                "presentation_id": "p3",
            },
        )[1]["reply_id"]
        == "r2"
    )
    svc.accept_fact(
        "reply.closed", {"reply_id": "r1", "turn_id": "turn", "character": "YUI"}
    )
    assert (
        svc.accept_fact("playback.started", {"turn_id": "turn", "character": "YUI"})[1][
            "reply_id"
        ]
        is None
    )


def test_record_conversation_id_is_not_a_playback_alias():
    msg = raw_message("m", reply_id="r", text="stored")
    msg["payload"]["metadata"]["conversation_id"] = "turn"
    msg["payload"]["source"] = "main_logic.core"
    svc = ConversationService(
        lambda mid: message_from_event(msg) if mid == "m" else None
    )
    svc.accept_message(msg)
    event = svc.accept_fact(
        "playback.started",
        {"turn_id": "turn", "character": "YUI", "presentation_id": "p"},
    )
    assert event[1]["reply_id"] is None
    svc.accept_fact(
        "reply.started", {"reply_id": "r", "character": "YUI", "text": "draft"}
    )
    assert svc.get_reply("r")["playbacks"] == []


def test_proactive_bus_conversation_id_is_not_a_playback_alias():
    msg = raw_message("m", reply_id="r")
    msg["payload"]["source"] = "proactive"
    msg["payload"]["metadata"]["conversation_id"] = "bus-turn"
    svc = ConversationService(lambda mid: None)
    svc.accept_message(msg)
    event = svc.accept_fact(
        "playback.started", {"turn_id": "bus-turn", "character": "YUI"}
    )
    assert event[1]["reply_id"] is None


def test_phase_times_preserve_producer_evidence_and_zero_boundary():
    svc = ConversationService(lambda mid: None)
    svc.accept_fact(
        "reply.started",
        {
            "reply_id": "r",
            "character": "YUI",
            "text": "first",
            "occurred_at": 0.0,
            "observed_at": 100.0,
        },
    )
    svc.accept_fact(
        "reply.started",
        {
            "reply_id": "r",
            "character": "YUI",
            "text": "again",
            "occurred_at": 5.0,
            "observed_at": 101.0,
        },
    )
    svc.accept_fact(
        "reply.closed",
        {
            "reply_id": "r",
            "character": "YUI",
            "text": "final",
            "observed_at": 102.0,
            "close_reason": "unknown",
        },
    )
    detail = svc.get_reply("r")
    assert detail["started_at"] == 0.0
    assert detail["closed_at"] is None


def test_formal_message_without_phase_provides_unknown_reply_and_interaction():
    current = {}
    svc = ConversationService(current.get)
    svc.accept_fact(
        "input.submitted",
        {
            "input_id": "i",
            "character": "YUI",
            "text": "original submission",
            "input_source": "text",
            "observed_at": 1.0,
        },
    )
    msg = raw_message("m", input_id="i", reply_id="r", text="formal")
    current["m"] = message_from_event(msg)
    svc.accept_message(msg)
    svc.accept_fact("playback.started", {"reply_id": "r", "presentation_id": "p"})
    reply = svc.get_reply("r")
    assert (
        reply["text"] == "formal"
        and reply["character"] == "YUI"
        and reply["input_id"] == "i"
    )
    assert reply["text_state"] == "unknown" and reply["close_reason"] is None
    assert (
        reply["started_at"] is None
        and reply["closed_at"] is None
        and reply["audio_expected"] is None
    )
    assert reply["playbacks"][0]["presentation_id"] == "p"
    interaction = svc.get_interaction("i")
    assert interaction["input"]["text"] == "original submission"
    assert [item["id"] for item in interaction["replies"]] == ["r"]
    current["m"]["text"] = "updated current"
    assert svc.get_interaction("i")["replies"][0]["text"] == "updated current"
    del current["m"]
    assert svc.get_reply("r") is None and svc.get_interaction("i")["replies"] == []


def test_formal_transfer_after_temporary_text_overflow_and_actual_large_formal():
    current = {}
    svc = ConversationService(current.get)
    with pytest.raises(Exception):
        svc.accept_fact("reply.started", {"reply_id": "r", "text": "x" * 100000})
    with pytest.raises(Exception):
        svc.get_reply("r")
    msg = raw_message("m", input_id="i", reply_id="r", text="formal small")
    current["m"] = message_from_event(msg)
    svc.accept_message(msg)
    result = svc.get_reply("r")
    assert result["text"] == "formal small" and result["text_state"] == "unknown"
    current["m"]["text"] = "x" * (1024 * 1024 + 1)
    with pytest.raises(Exception) as error:
        svc.get_reply("r")
    assert error.value.code == "CONVERSATION_DETAIL_TOO_LARGE"


def test_formal_user_record_does_not_invent_submission():
    current = {}
    svc = ConversationService(current.get)
    svc.accept_message(raw_message("u", input_id="i", role="user_message"))
    msg = raw_message("m", input_id="i", reply_id="r")
    current["m"] = message_from_event(msg)
    svc.accept_message(msg)
    assert svc.get_interaction("i") is None


def test_formal_does_not_clear_playback_overflow():
    current = {}
    svc = ConversationService(current.get)
    svc.accept_fact("reply.started", {"reply_id": "r", "input_id": "i", "text": "hi"})
    for i in range(32):
        svc.accept_fact(
            "playback.started", {"reply_id": "r", "presentation_id": str(i)}
        )
    with pytest.raises(Exception):
        svc.accept_fact(
            "playback.started", {"reply_id": "r", "presentation_id": "overflow"}
        )
    msg = raw_message("m", input_id="i", reply_id="r")
    current["m"] = message_from_event(msg)
    svc.accept_message(msg)
    with pytest.raises(Exception) as error:
        svc.get_reply("r")
    assert error.value.code == "CONVERSATION_DETAIL_TOO_LARGE"


def test_formal_oversize_interaction_reports_error():
    current = {}
    svc = ConversationService(current.get)
    svc.accept_fact(
        "input.submitted",
        {
            "input_id": "i",
            "character": "YUI",
            "text": "hi",
            "input_source": "text",
            "observed_at": 1.0,
        },
    )
    msg = raw_message("m", input_id="i", reply_id="r")
    current["m"] = message_from_event(msg)
    svc.accept_message(msg)
    current["m"]["text"] = "x" * (1024 * 1024 + 1)
    with pytest.raises(Exception) as error:
        svc.get_interaction("i")
    assert error.value.code == "CONVERSATION_DETAIL_TOO_LARGE"
