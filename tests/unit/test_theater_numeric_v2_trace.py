"""Text tracing observes real request boundaries without changing calls or stored prose."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_trace as trace
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2TransitionOfferReview
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_usage import invoke_with_usage, numeric_v2_usage_scope
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from utils.llm_client import HumanMessage, SystemMessage


class Client:
    model = "trace-test-model"
    max_completion_tokens = 300
    temperature = 0.5
    api_key = "SECRET_AUTH"

    def __init__(self, content="原始回复", error=None):
        self.calls = []
        self.error = error
        self.response = SimpleNamespace(content=content, response_metadata={
            "finish_reason": "stop", "headers": {"authorization": self.api_key},
            "token_usage": {"prompt_tokens": 12, "completion_tokens": 4, "debug": self.api_key},
        })

    async def ainvoke(self, messages):
        self.calls.append(messages)
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return self.response


def read_traces(root):
    return [list(map(json.loads, path.read_text(encoding="utf-8").splitlines())) for path in sorted(root.glob("*.jsonl"))]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_trace_preserves_requests_responses_usage_and_excludes_credentials(tmp_path, monkeypatch, enabled):
    if enabled:
        monkeypatch.setenv("NEKO_THEATER_TRACE_DIR", str(tmp_path))
    else:
        monkeypatch.delenv("NEKO_THEATER_TRACE_DIR", raising=False)

        def no_serialization(*args, **kwargs):
            raise AssertionError("disabled tracing must not serialize")

        monkeypatch.setattr(trace, "_json_default", no_serialization)
    client = Client()
    messages = [SystemMessage(content="演员指令"), HumanMessage(content="玩家原话"),
                {"role": "assistant", "content": "历史正文", "metadata": {"secret": client.api_key}}]
    with numeric_v2_usage_scope() as calls, trace.text_trace_scope("turn", turn=TurnRequestV2("one", 0, "玩家原话")):
        response = await invoke_with_usage(client, messages, stage="actor")
    assert client.calls == [messages] and client.calls[0] is messages
    assert response is client.response
    assert calls == [{"stage": "actor", "input_tokens": 12, "output_tokens": 4}]
    assert trace._current_trace.get() is None
    logs = read_traces(tmp_path)
    if not enabled:
        assert logs == []
        return
    rows, = logs
    assert len({r["trace_id"] for r in rows}) == 1
    assert [r["sequence"] for r in rows] == list(range(1, len(rows) + 1))
    request = next(r["data"] for r in rows if r["event"] == "model.request")
    reply = next(r["data"] for r in rows if r["event"] == "model.response")
    assert request["messages"] == [{"role": "system", "content": "演员指令"},
                                   {"role": "user", "content": "玩家原话"},
                                   {"role": "assistant", "content": "历史正文"}]
    assert request["call_id"] == reply["call_id"] and request["stage"] == reply["stage"] == "actor"
    assert reply["content"] == client.response.content
    assert client.api_key not in json.dumps(rows)
    assert rows[-1]["data"]["status"] == "returned"


@pytest.mark.asyncio
async def test_concurrent_turns_and_child_calls_are_isolated_and_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_THEATER_TRACE_DIR", str(tmp_path))
    clients = {name: Client(name) for name in ("first", "second")}

    async def run(name):
        with trace.text_trace_scope("turn", session_id=name):
            await asyncio.gather(*(trace.invoke_with_trace(clients[name], [HumanMessage(content=name)], stage="history_lookup")
                                   for _ in range(3)))

    await asyncio.gather(run("first"), run("second"))
    for rows in read_traces(tmp_path):
        name = rows[0]["data"]["session_id"]
        requests = [r["data"] for r in rows if r["event"] == "model.request"]
        replies = [r["data"] for r in rows if r["event"] == "model.response"]
        assert {r["call_id"] for r in requests} == {r["call_id"] for r in replies} == {1, 2, 3}
        assert all(r["messages"][0]["content"] == name for r in requests)
        assert all(r["content"] == name for r in replies)
    assert trace._current_trace.get() is None
    await trace.invoke_with_trace(Client(), [], stage="actor")
    assert len(read_traces(tmp_path)) == 2

    released = asyncio.Event()

    async def late_child():
        await released.wait()
        trace.trace_event("late_child")

    with trace.text_trace_scope("old"):
        pending = asyncio.create_task(late_child())
    with trace.text_trace_scope("new"):
        released.set()
        await pending
    assert all(r["event"] != "late_child" for rows in read_traces(tmp_path) for r in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("SECRET_AUTH"), asyncio.CancelledError("SECRET_AUTH")])
async def test_model_errors_and_cancellation_preserve_exception_and_close_trace(tmp_path, monkeypatch, error):
    monkeypatch.setenv("NEKO_THEATER_TRACE_DIR", str(tmp_path))
    with pytest.raises(type(error)) as caught:
        with trace.text_trace_scope("turn"):
            await trace.invoke_with_trace(Client(error=error), [], stage="review")
    assert caught.value is error
    rows, = read_traces(tmp_path)
    assert [r["event"] for r in rows] == ["trace.started", "model.request", "model.failed", "trace.closed"]
    assert rows[-1]["data"]["status"] == ("cancelled" if isinstance(error, asyncio.CancelledError) else "error")
    assert "SECRET_AUTH" not in json.dumps(rows)
    assert trace._current_trace.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["directory", "serialize", "write", "metadata", "request"])
async def test_trace_failures_never_abort_or_repeat_model_call(tmp_path, monkeypatch, caplog, failure):
    path = tmp_path / "traces"
    if failure == "directory":
        path.write_text("not a directory")
    monkeypatch.setenv("NEKO_THEATER_TRACE_DIR", str(path))
    client = Client()
    messages = [HumanMessage(content="私人正文")]
    if failure == "metadata":
        client.response.response_metadata = object()
    if failure == "request":
        messages = [object()]
    with trace.text_trace_scope("turn"):
        if failure == "serialize":
            trace.trace_event("invalid", unsupported=object())
        if failure == "write":
            stream = trace._current_trace.get().file
            stream.close()
        result = await trace.invoke_with_trace(client, messages, stage="actor")
    assert result is client.response and client.calls == [messages]
    assert trace._current_trace.get() is None
    assert "text trace disabled" in caplog.text
    assert "私人正文" not in caplog.text and "SECRET_AUTH" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_fails", [False, True])
async def test_workflow_traces_retries_reviews_history_and_atomic_commit(tmp_path, monkeypatch, commit_fails):
    """The same fixed replies produce identical saves with tracing on/off, including rollback."""
    from services.theater import numeric_v2_history as history

    async def config(*args):
        return {"model": "trace-test-model", "base_url": "https://unused.test"}

    class HistoryClient(Client):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def history_client(*args, **kwargs):
        return HistoryClient('{"evidence_ids":[0]}')

    monkeypatch.setattr(history, "_model_config", config)
    monkeypatch.setattr(history, "create_chat_llm_async", history_client)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    outcomes = []
    call_counts = []
    for enabled in (False, True):
        if enabled:
            monkeypatch.setenv("NEKO_THEATER_TRACE_DIR", str(tmp_path / "traces"))
        else:
            monkeypatch.delenv("NEKO_THEATER_TRACE_DIR", raising=False)
        runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path / str(enabled))
        current = await runtime.start_session(session_id="same", catgirl_binding=_binding(), opening_performance=_opening())
        counts = {"actor": 0, "evaluator": 0, "review": 0}

        async def evaluate(self, **kwargs):
            counts["evaluator"] += 1
            await invoke_with_usage(Client(), [HumanMessage(content=kwargs["message"])], stage="evaluator")
            return NumericV2EvaluationResult((), False, history_query="开场说了什么？")

        async def generate(self, **kwargs):
            counts["actor"] += 1
            await invoke_with_usage(Client("格式错误" if counts["actor"] == 1 else "候选正文"), [], stage="actor")
            if counts["actor"] == 1:
                raise NumericV2ActorOutputError("numeric_v2_actor_output_invalid_json")
            return {"performance": "拒稿正文" if counts["actor"] == 2 else "最终正文", "suggested_inputs": ["坏按钮", "安全按钮"]}

        async def review(self, **kwargs):
            counts["review"] += 1
            await invoke_with_usage(Client(), [], stage="dispute" if kwargs.get("dispute_review") else "review")
            rejected = kwargs["actor_performance"]["performance"] == "拒稿正文"
            return NumericV2TransitionOfferReview(False, False, ("author_boundary",) if rejected else (),
                                                 (0,) if not rejected else (), "越过作者边界" if rejected else "")

        async def before_commit():
            if commit_fails:
                raise ValueError("commit_fenced")

        monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
        monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
        monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
        kwargs = dict(config_manager=object(), runtime=runtime, current=current, turn=TurnRequestV2("one", 0, "我听着。"),
                      ensure_current_binding=lambda _: _binding(), before_commit=before_commit)
        if commit_fails:
            with pytest.raises(ValueError, match="commit_fenced"):
                await workflow.execute_numeric_v2_turn(**kwargs)
            stored = await runtime.restore_session("same")
            assert stored == current
        else:
            result = await workflow.execute_numeric_v2_turn(**kwargs)
            stored = result.stored
            assert await runtime.restore_session("same") == stored
            assert result.performance["performance"] == "最终正文"
            assert result.performance["suggested_inputs"] == ["安全按钮"]
        outcomes.append(stored)
        call_counts.append(counts)
    assert outcomes[0] == outcomes[1]
    assert call_counts == [{"actor": 3, "evaluator": 1, "review": 3}] * 2
    rows, = read_traces(tmp_path / "traces")
    assert rows[0]["data"]["state_before"]["revision"] == 0
    assert rows[0]["data"]["state_before"]["session_id"] == "same"
    assert rows[0]["data"]["state_before"]["story_package_hash"] == current.session.story_package_hash
    events = [r["event"] for r in rows]
    assert events.count("actor.rejected") == 1
    assert events.count("actor.candidate") == 2 and events.count("review.result") == 3
    evidence = next(r["data"]["result"] for r in rows if r["event"] == "history.result")
    assert evidence["status"] == "found" and evidence["evidence"]
    assert "suggestions.filtered" in events and "turn.finalized" in events
    assert ("turn.committed" in events) is not commit_fails
    assert rows[-1]["data"]["status"] == ("error" if commit_fails else "returned")
    if not commit_fails:
        committed = next(r["data"] for r in rows if r["event"] == "turn.committed")
        assert committed["state_after"]["revision"] == 1
        assert committed["stored_performance"] == outcomes[1].session.performance_history[-1]
        assert committed["ledger_event"] == outcomes[1].ledger_events[-1]


@pytest.mark.parametrize("fails", [False, True])
def test_http_opening_trace_reports_commit_or_mapped_failure(tmp_path, monkeypatch, fails):
    from tests.unit.test_theater_numeric_v2_router import _client

    monkeypatch.setenv("NEKO_THEATER_TRACE_DIR", str(tmp_path / "traces"))
    client = _client(tmp_path, monkeypatch)
    if fails:
        async def fail(*args, **kwargs):
            raise NumericV2ActorOutputError("numeric_v2_actor_output_invalid_json")
        monkeypatch.setattr(workflow.NumericV2Actor, "generate_opening", fail)
    with client:
        response = client.post("/api/theater-numeric/session/start",
                               json={"story_id": "numeric_v2_contract", "session_id": "opening_trace"})
    assert response.status_code == (502 if fails else 200)
    rows, = read_traces(tmp_path / "traces")
    events = [r["event"] for r in rows]
    assert "opening.context" in events
    assert ("opening.committed" in events) is not fails
    reply = next(r["data"] for r in rows if r["event"] == "opening.response")
    assert reply["status_code"] == response.status_code
    if not fails:
        committed = next(r["data"] for r in rows if r["event"] == "opening.committed")
        assert committed["state"]["session_id"] == "opening_trace"
        assert committed["performance"]["performance"] == "你回来了。"
