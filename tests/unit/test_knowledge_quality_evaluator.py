from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from knowledge.service import KnowledgeTurnContext
from scripts import evaluate_knowledge_response_quality as evaluator


@pytest.mark.parametrize(
    "script_name",
    ("import_geng_guide.py", "evaluate_knowledge_response_quality.py"),
)
def test_direct_knowledge_cli_bootstraps_repository_before_import(script_name):
    source = (evaluator.ROOT / "scripts" / script_name).read_text(encoding="utf-8")

    assert source.index("sys.path.insert") < source.index("from knowledge")


@pytest.mark.asyncio
async def test_route_preflight_uses_production_context_builder(monkeypatch, tmp_path):
    service = object()
    builder = AsyncMock(
        return_value=KnowledgeTurnContext(
            text="Knowledge term: fixture",
            hit_count=1,
            match_mode="automatic_hybrid",
            entry_title="Fixture",
            source_tag="source:fixture",
        )
    )
    monkeypatch.setattr(
        evaluator.KnowledgeService,
        "for_database",
        lambda _database: service,
    )
    monkeypatch.setattr(evaluator, "_build_production_context", builder)

    results = await evaluator._route_preflight(
        [{
            "message": "semantic fixture",
            "expected_mode": "strong",
            "expected_source_tag": "source:fixture",
            "expected_title": "fixture",
        }],
        tmp_path / "knowledge.db",
    )

    builder.assert_awaited_once_with(service, "semantic fixture")
    assert results[0]["route_pass"] is True
    assert results[0]["production_match_mode"] == "automatic_hybrid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_title", "source_tag"),
    (("Unrelated", "source:fixture"), ("Fixture", "source:other")),
)
async def test_route_preflight_rejects_unrelated_strong_hit(
    monkeypatch,
    tmp_path,
    entry_title,
    source_tag,
):
    monkeypatch.setattr(
        evaluator.KnowledgeService,
        "for_database",
        lambda _database: object(),
    )
    monkeypatch.setattr(
        evaluator,
        "_build_production_context",
        AsyncMock(
            return_value=KnowledgeTurnContext(
                text=f"Knowledge term: {entry_title}",
                hit_count=1,
                match_mode="automatic_hybrid",
                entry_title=entry_title,
                source_tag=source_tag,
            )
        ),
    )

    results = await evaluator._route_preflight(
        [{
            "message": "semantic fixture",
            "expected_mode": "strong",
            "expected_source_tag": "source:fixture",
            "expected_title": "Fixture",
        }],
        tmp_path / "knowledge.db",
    )

    assert results[0]["actual_mode"] == "strong"
    assert results[0]["route_pass"] is False


def test_quality_fixture_binds_every_strong_case_to_one_entry():
    cases = evaluator._load_cases(evaluator.DEFAULT_CASES)

    strong = [case for case in cases if case["expected_mode"] == "strong"]
    assert len(strong) == 7
    assert {case["expected_source_tag"] for case in strong} == {"source:chime"}
    assert all(case["expected_title"] for case in strong)


@pytest.mark.parametrize("missing_field", ("id", "expected_mode"))
def test_quality_fixture_missing_required_field_uses_schema_error(
    tmp_path,
    missing_field,
):
    cases = json.loads(evaluator.DEFAULT_CASES.read_text(encoding="utf-8"))
    cases[0].pop(missing_field)
    fixture = tmp_path / "cases.json"
    fixture.write_text(json.dumps(cases), encoding="utf-8")

    with pytest.raises(ValueError, match="documented fields"):
        evaluator._load_cases(fixture)


@pytest.mark.asyncio
async def test_live_receiver_waits_for_explicit_turn_end():
    class SlowWebSocket:
        def __init__(self):
            self.messages = iter(
                (
                    {"type": "gemini_response", "text": "first"},
                    {"type": "system", "data": "working"},
                    {"type": "gemini_response", "text": " second"},
                    {"type": "system", "data": "turn end"},
                )
            )

        async def recv(self):
            await asyncio.sleep(0.01)
            return json.dumps(next(self.messages))

    outcome = await evaluator._receive_until_complete(SlowWebSocket())

    assert outcome["completed"] is True
    assert outcome["reply"] == "first second"


@pytest.mark.asyncio
async def test_live_receiver_requires_exact_request_id_for_content_and_completion():
    class InterleavedWebSocket:
        def __init__(self):
            self.messages = iter(
                (
                    {"type": "gemini_response", "text": "proactive"},
                    {"type": "system", "data": "turn end agent_callback"},
                    {
                        "type": "gemini_response",
                        "text": "wrong",
                        "request_id": "other-request",
                    },
                    {
                        "type": "system",
                        "data": "turn end",
                        "request_id": "other-request",
                    },
                    {
                        "type": "gemini_response",
                        "text": "target",
                        "request_id": "target-request",
                    },
                    {
                        "type": "system",
                        "data": "turn end",
                        "request_id": "target-request",
                    },
                )
            )

        async def recv(self):
            return json.dumps(next(self.messages))

    outcome = await evaluator._receive_until_complete(
        InterleavedWebSocket(),
        expected_request_id="target-request",
    )

    assert outcome["completed"] is True
    assert outcome["reply"] == "target"


@pytest.mark.asyncio
async def test_live_runner_uses_a_fresh_session_for_every_case(monkeypatch):
    connections = []

    class FakeWebSocket:
        def __init__(self, index):
            self.index = index
            self.sent = []
            self.received = iter(
                (
                    {"type": "session_started"},
                    {
                        "type": "gemini_response",
                        "text": f"reply-{index}",
                        "request_id": f"knowledge-quality-{index}",
                    },
                    {
                        "type": "system",
                        "data": "turn end",
                        "request_id": f"knowledge-quality-{index}",
                    },
                )
            )

        async def send(self, payload):
            self.sent.append(json.loads(payload))

        async def recv(self):
            return json.dumps(next(self.received))

    class FakeConnection:
        def __init__(self):
            self.websocket = FakeWebSocket(len(connections) + 1)
            connections.append(self.websocket)

        async def __aenter__(self):
            return self.websocket

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        evaluator.websockets,
        "connect",
        lambda *_args, **_kwargs: FakeConnection(),
    )

    results = await evaluator._run_live(
        [{"message": "first"}, {"message": "second"}],
        websocket_url="ws://fixture",
        language="zh",
    )

    assert [result["reply"] for result in results] == ["reply-1", "reply-2"]
    assert len(connections) == 2
    for index, websocket in enumerate(connections, start=1):
        assert websocket.sent == [
            {
                "action": "start_session",
                "input_type": "text",
                "new_session": True,
                "language": "zh",
            },
            {
                "action": "stream_data",
                "input_type": "text",
                "data": "first" if index == 1 else "second",
                "request_id": f"knowledge-quality-{index}",
                "language": "zh",
            },
            {
                "action": "end_session",
                "reason": "knowledge_quality_case_complete",
            },
        ]
