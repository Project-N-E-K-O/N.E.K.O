from __future__ import annotations

import pytest

from brain.task_executor import DirectTaskExecutor
from brain.plugin_reply import build_plugin_reply_event
from plugin.reply_contract import (
    choose_reply_candidate,
    export_item_candidate,
    parse_agent_reply_spec,
    resolve_message_reply_text,
    trigger_response_candidate,
)


def test_reply_candidate_prefers_explicit_export_over_default_trigger_response():
    trigger = trigger_response_candidate(
        {
            "data": {"summary": "from return", "hidden": "secret"},
            "message": "",
            "meta": {},
        },
        fallback_fields=["summary"],
        sequence=0,
    )
    export = export_item_candidate(
        {
            "type": "text",
            "text": "from export",
            "metadata": {"agent": {"reply": True}},
        },
        sequence=1,
    )

    chosen = choose_reply_candidate([trigger, export])

    assert chosen is not None
    assert chosen.source.startswith("export:")
    assert chosen.visible_payload == "from export"


def test_reply_candidate_ignores_hidden_export_without_visible_content():
    trigger = trigger_response_candidate(
        {
            "data": {"summary": "from return", "hidden": "secret"},
            "message": "",
            "meta": {},
        },
        fallback_fields=["summary"],
        sequence=0,
    )
    export = export_item_candidate(
        {
            "type": "json",
            "json": {"summary": "hidden"},
            "metadata": {"agent": {"reply": True, "include": False}},
        },
        sequence=1,
    )

    chosen = choose_reply_candidate([trigger, export])

    assert chosen is not None
    assert chosen.source == "trigger_response"
    assert chosen.visible_payload == {"summary": "from return"}


def test_resolve_message_reply_text_supports_summary_override_and_silence():
    spec, content = resolve_message_reply_text(
        content="raw content",
        metadata={"agent": {"reply": True, "summary": "override"}},
        default_reply=True,
    )
    assert spec.reply is True
    assert content == "override"

    silent_spec, silent_content = resolve_message_reply_text(
        content="raw content",
        metadata={"agent": {"reply": False}},
        default_reply=True,
    )
    assert silent_spec.reply is False
    assert silent_content == ""


def test_build_plugin_reply_event_uses_explicit_summary_detail_and_mode():
    event = build_plugin_reply_event(
        plugin_id="demo",
        completion={
            "reply": {
                "reply": True,
                "mode": "proactive",
                "payload_type": "json",
                "data": {"summary": "ignored"},
                "fields": ["summary"],
                "summary": "Summarized",
                "detail": "Detailed",
            },
            "run_data": {"summary": "fallback"},
        },
        success=True,
    )

    assert event.emit is True
    assert event.event_type == "proactive_message"
    assert event.summary == "Summarized"
    assert event.detail == "Detailed"


def test_build_plugin_reply_event_respects_suppressed_completion():
    event = build_plugin_reply_event(
        plugin_id="demo",
        completion={"reply_suppressed": True, "run_data": {"summary": "fallback"}},
        success=True,
    )

    assert event.emit is False


def test_build_plugin_reply_event_does_not_leak_hidden_run_data():
    event = build_plugin_reply_event(
        plugin_id="demo",
        completion={
            "reply": {
                "reply": True,
                "include": False,
                "payload_type": "json",
                "data": None,
                "fields": ["summary"],
            },
            "reply_message": "Completed privately",
            "run_data": {"summary": "SECRET"},
        },
        success=True,
    )

    assert event.emit is True
    assert event.detail == "Completed privately"
    assert "SECRET" not in event.summary
    assert "SECRET" not in event.detail


def test_build_plugin_reply_event_suppresses_hidden_empty_reply_contract():
    event = build_plugin_reply_event(
        plugin_id="demo",
        completion={
            "reply": {
                "reply": True,
                "include": False,
                "payload_type": "json",
                "data": None,
            },
            "run_data": {"summary": "SECRET"},
        },
        success=True,
    )

    assert event.emit is False


def test_parse_agent_reply_spec_preserves_summary_and_detail_overrides():
    spec = parse_agent_reply_spec(
        {"agent": {"reply": True, "summary": "Reminder due", "detail": "raw reminder body"}},
        default_reply=True,
        default_mode="proactive",
    )

    assert spec.reply is True
    assert spec.summary == "Reminder due"
    assert spec.detail == "raw reminder body"


@pytest.mark.asyncio
async def test_execute_user_plugin_uses_resolved_entry_fields_for_case_insensitive_match(monkeypatch):
    executor = DirectTaskExecutor(computer_use=object(), browser_use=None)
    executor.plugin_list = [
        {
            "id": "demo",
            "entries": [
                {"id": "run", "llm_result_fields": ["summary"]},
            ],
        }
    ]

    class _FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"run_id": "run-1", "run_token": "token-1"}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            return _FakeResponse()

    captured: dict[str, object] = {}

    async def _fake_await_run_completion(run_id, **kwargs):
        captured["run_id"] = run_id
        captured["llm_result_fields"] = kwargs.get("llm_result_fields")
        return {"status": "succeeded", "success": True, "data": {"summary": "ok"}}

    monkeypatch.setattr("brain.task_executor.httpx.AsyncClient", lambda *args, **kwargs: _FakeClient())
    monkeypatch.setattr(executor, "_await_run_completion", _fake_await_run_completion)

    result = await executor._execute_user_plugin(
        task_id="task-1",
        plugin_id="demo",
        plugin_args={},
        entry_id="Run",
        task_description="demo",
    )

    assert result.success is True
    assert result.entry_id == "run"
    assert captured["run_id"] == "run-1"
    assert captured["llm_result_fields"] == ["summary"]
