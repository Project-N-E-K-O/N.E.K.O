# -*- coding: utf-8 -*-
"""Routing by vendor ability, the chat rehost of decision questions, and their plumbing."""

from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from utils.decision_client import ChoiceQuestion, routing
from utils.decision_client.rehost import (
    REHOST_REPLY_RULE,
    build_rehost_messages,
    letter_probabilities,
    rehost_choice,
    rehost_choices,
)
from utils.decision_client.routing import (
    ROUTE_CHAT,
    ROUTE_NATIVE,
    ROUTE_REHOST,
    SITE_MASTER_EMOTION,
    SITE_MEMORY_RECALL,
    chat_logprobs_usable,
    decide_route,
    native_decision_usable,
)


def _native(url: str, model: str = "decision-model", protocol: str = "systemone") -> dict:
    return {"model": model, "base_url": url, "decision_url": url, "decision_protocol": protocol, "api_key": "k"}


def _chat(url: str, model: str = "m", provider_type: str = "openai_compatible") -> dict:
    return {"model": model, "base_url": url, "api_key": "k", "provider_type": provider_type}


BAILIAN = _native("https://ws.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone")
ARK_NATIVE = _native("https://ark.cn-beijing.volces.com/api/v1/systemone", model="doubao-seed-2-1-lite-260915")
CHAT_FALLBACK = {"model": "glm-4.7-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4", "decision_protocol": "chat_fallback"}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("decision", "chat", "expected"),
    [
        # Class A: a native decision slot wins whatever the chat model is.
        (BAILIAN, _chat("https://api.deepseek.com/v1"), ROUTE_NATIVE),
        (_native("https://api.openai.com/v1/decisions", "gpt-6-luna", "openai_decisions"), _chat("https://api.openai.com/v1"), ROUTE_NATIVE),
        # Class B: chat models with first-token probabilities.
        (CHAT_FALLBACK, _chat("https://ark.cn-beijing.volces.com/api/v3"), ROUTE_REHOST),
        (CHAT_FALLBACK, _chat("https://api.deepseek.com/v1"), ROUTE_REHOST),
        (CHAT_FALLBACK, _chat("https://api.moonshot.cn/v1"), ROUTE_REHOST),
        (CHAT_FALLBACK, _chat("https://api.stepfun.com/v1"), ROUTE_REHOST),
        # Class C: everything else keeps its own chat.
        (CHAT_FALLBACK, _chat("https://open.bigmodel.cn/api/paas/v4"), ROUTE_CHAT),
        (CHAT_FALLBACK, _chat("https://api.x.ai/v1"), ROUTE_CHAT),
        (CHAT_FALLBACK, _chat("https://api.kimi.com/coding", provider_type="anthropic"), ROUTE_CHAT),
        (CHAT_FALLBACK, _chat("https://api.deepseek.com/v1", provider_type="anthropic"), ROUTE_CHAT),
        (None, None, ROUTE_CHAT),
        # Bailian without a workspace host resolves to chat_fallback: class C.
        ({"model": "qwen3.7-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
          "decision_protocol": "chat_fallback"}, _chat("https://dashscope.aliyuncs.com/compatible-mode/v1"), ROUTE_CHAT),
    ],
)
def test_decide_route_by_vendor_ability(decision, chat, expected):
    assert decide_route(SITE_MASTER_EMOTION, decision_config=decision, chat_config=chat) == expected


@pytest.mark.unit
def test_doubao_native_decision_serves_memory_recall_only():
    doubao_chat = _chat("https://ark.cn-beijing.volces.com/api/v3")
    assert native_decision_usable(ARK_NATIVE, SITE_MEMORY_RECALL)
    assert not native_decision_usable(ARK_NATIVE, SITE_MASTER_EMOTION)
    assert decide_route(SITE_MEMORY_RECALL, decision_config=ARK_NATIVE, chat_config=doubao_chat) == ROUTE_NATIVE
    assert decide_route(SITE_MASTER_EMOTION, decision_config=ARK_NATIVE, chat_config=doubao_chat) == ROUTE_REHOST


@pytest.mark.unit
@pytest.mark.parametrize("url", ["https://www.lanlan.tech/text/v1", "https://www.lanlan.app/text/v1"])
def test_free_tier_keeps_its_chat_until_the_gateway_takes_the_new_prompts(url, monkeypatch):
    free_chat = _chat(url, model="free-mini-model")
    free_native = _native(url.replace("/text/v1", "/text/v1/systemone"), model="free-decision")
    assert decide_route(SITE_MASTER_EMOTION, decision_config=free_native, chat_config=free_chat) == ROUTE_CHAT

    monkeypatch.setattr(routing, "FREE_TIER_NEW_PROMPTS_ENABLED", True)
    assert native_decision_usable(free_native, SITE_MASTER_EMOTION)
    # Only the domestic gateway (Doubao behind it) returns token probabilities.
    assert chat_logprobs_usable(free_chat) == ("lanlan.tech" in url)


@pytest.mark.unit
def test_native_decision_needs_a_model_and_a_native_protocol():
    assert not native_decision_usable({**BAILIAN, "model": ""}, SITE_MASTER_EMOTION)
    assert not native_decision_usable(CHAT_FALLBACK, SITE_MASTER_EMOTION)
    # A missing protocol is inferred from the endpoint.
    inferred = {"model": "custom", "base_url": "https://gw.example/v1/systemone"}
    assert native_decision_usable(inferred, SITE_MASTER_EMOTION)


# ---------------------------------------------------------------------------
# Rehost: messages and letter reading
# ---------------------------------------------------------------------------


def _question(n: int = 3) -> ChoiceQuestion:
    return ChoiceQuestion(
        instructions="Which one?",
        criteria={f"opt{i}": f"option number {i}" for i in range(n)},
    )


@pytest.mark.unit
def test_rehost_messages_letter_the_options_and_ask_for_the_letter():
    messages, keys = build_rehost_messages("the state", _question(3))
    assert keys == ["opt0", "opt1", "opt2"]
    assert [m["role"] for m in messages] == ["system", "user"]  # no assistant prefill
    system = messages[0]["content"]
    assert system.startswith("Which one?\n\nOptions:\nA. option number 0\nB. option number 1\nC. option number 2")
    assert system.endswith(REHOST_REPLY_RULE)
    assert messages[1]["content"] == "the state"


@pytest.mark.unit
def test_rehost_messages_take_at_most_26_options_and_dump_non_text_state():
    with pytest.raises(ValueError):
        build_rehost_messages("s", _question(27))
    messages, _keys = build_rehost_messages({"turn": "hi"}, _question(2))
    assert messages[1]["content"] == '{"turn": "hi"}'


@pytest.mark.unit
def test_letter_probabilities_keep_letters_only_and_renormalise():
    keys = ["a", "b", "c"]
    cands = [
        ("B", math.log(0.5)),
        (" A", math.log(0.2)),
        ("A.", math.log(0.1)),  # same letter, trailing full stop
        ("D", math.log(0.1)),  # beyond the options
        ("Sure", math.log(0.05)),  # not a letter
        ("b", math.log(0.05)),  # lower case is not an option letter
    ]
    probs = letter_probabilities(cands, keys)
    assert set(probs) == {"a", "b"}
    assert probs["b"] == pytest.approx(0.5 / 0.8)
    assert probs["a"] == pytest.approx(0.3 / 0.8)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert letter_probabilities([("Hello", -0.1)], keys) == {}


@pytest.mark.unit
def test_letter_probabilities_drop_non_finite_log_probabilities():
    keys = ["a", "b"]
    assert letter_probabilities([("A", float("nan"))], keys) == {}
    probs = letter_probabilities([("A", float("nan")), ("B", math.log(0.4))], keys)
    assert probs == {"b": pytest.approx(1.0)}


# ---------------------------------------------------------------------------
# Rehost: the call
# ---------------------------------------------------------------------------


def _raw_with_first_token(cands):
    top = [SimpleNamespace(token=t, logprob=lp) for t, lp in cands]
    logprobs = SimpleNamespace(content=[SimpleNamespace(token=cands[0][0] if cands else "", top_logprobs=top)])
    return SimpleNamespace(choices=[SimpleNamespace(logprobs=logprobs, message=SimpleNamespace(content="B"))])


class _FakeLLM:
    def __init__(self, raw=None, exc=None):
        self.raw = raw
        self.exc = exc
        self.calls: list[tuple] = []

    async def ainvoke_raw(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.exc:
            raise self.exc
        return self.raw


DOUBAO_CHAT = _chat("https://ark.cn-beijing.volces.com/api/v3", model="doubao-seed-2-0-mini-260428")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rehost_choice_asks_one_token_with_top_20_and_returns_a_choice_answer():
    llm = _FakeLLM(_raw_with_first_token([("B", math.log(0.7)), ("A", math.log(0.3))]))
    with patch("utils.llm_client.warm_pool.get_warm_chat_llm", AsyncMock(return_value=llm)) as get_llm:
        answer = await rehost_choice(state="s", question=_question(2), chat_config=DOUBAO_CHAT, timeout=3.0)
    assert answer.question_type == "choice"
    assert answer.value == "opt1"
    assert answer.probabilities == pytest.approx({"opt1": 0.7, "opt0": 0.3})
    messages, kw = llm.calls[0]
    assert kw["max_completion_tokens"] == 1
    assert kw["logprobs"] is True and kw["top_logprobs"] == 20
    assert kw["timeout"] == 3.0
    assert get_llm.await_args.args[:3] == ("doubao-seed-2-0-mini-260428", DOUBAO_CHAT["base_url"], "k")


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    "llm",
    [
        _FakeLLM(exc=RuntimeError("HTTP 500")),
        _FakeLLM(SimpleNamespace(choices=[SimpleNamespace(logprobs=None)])),  # vendor ignored logprobs
        _FakeLLM(SimpleNamespace(choices=[])),
        _FakeLLM(_raw_with_first_token([("Well", -0.1), ("I", -2.0)])),  # no option letter
    ],
)
async def test_rehost_choice_returns_none_on_any_miss(llm):
    with patch("utils.llm_client.warm_pool.get_warm_chat_llm", AsyncMock(return_value=llm)):
        assert await rehost_choice(state="s", question=_question(2), chat_config=DOUBAO_CHAT) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rehost_choice_gives_up_at_its_timeout():
    class _Slow(_FakeLLM):
        async def ainvoke_raw(self, messages, **kw):
            await asyncio.sleep(5)

    with patch("utils.llm_client.warm_pool.get_warm_chat_llm", AsyncMock(return_value=_Slow())):
        assert await rehost_choice(state="s", question=_question(2), chat_config=DOUBAO_CHAT, timeout=0.05) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rehost_choices_run_together_and_fail_as_a_whole():
    good = _raw_with_first_token([("A", math.log(0.9)), ("B", math.log(0.1))])
    started: list[str] = []
    gate = asyncio.Event()

    class _Parallel(_FakeLLM):
        async def ainvoke_raw(self, messages, **kw):
            started.append(messages[0]["content"])
            if len(started) == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), 1.0)  # both calls must be in flight together
            return self.raw

    qs = {"kind": _question(2), "mood": _question(3)}
    with patch("utils.llm_client.warm_pool.get_warm_chat_llm", AsyncMock(return_value=_Parallel(good))):
        answers = await rehost_choices(state="s", questions=qs, chat_config=DOUBAO_CHAT)
    assert set(answers) == {"kind", "mood"}
    assert answers["kind"].value == "opt0"

    async def _one_misses(*, question, **kw):
        return None if len(question.criteria) == 3 else SimpleNamespace(value="opt0")

    with patch("utils.decision_client.rehost.rehost_choice", _one_misses):
        assert await rehost_choices(state="s", questions=qs, chat_config=DOUBAO_CHAT) is None


# ---------------------------------------------------------------------------
# Warm chat-client pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_warm_pool_reuses_one_client_per_settings_and_keeps_connections_open():
    from utils.llm_client import warm_pool

    made: list[dict] = []

    async def _create(model, base_url, api_key, **kw):
        made.append({"model": model, **kw})
        return SimpleNamespace(model=model, aclose=AsyncMock())

    with patch("utils.llm_client.create_chat_llm_async", _create):
        try:
            a = await warm_pool.get_warm_chat_llm("m1", "https://x/v1", "k", max_retries=0)
            b = await warm_pool.get_warm_chat_llm("m1", "https://x/v1", "k", max_retries=0)
            c = await warm_pool.get_warm_chat_llm("m1", "https://x/v1", "other-key", max_retries=0)
        finally:
            await warm_pool.close_warm_chat_llms()
    assert a is b and a is not c
    assert len(made) == 2
    assert made[0]["keepalive_expiry"] == warm_pool.WARM_POOL_KEEPALIVE_SECONDS


@pytest.mark.asyncio
@pytest.mark.unit
async def test_warm_pool_drops_the_least_recently_used_client_without_closing_it():
    from utils.llm_client import warm_pool

    clients: list[SimpleNamespace] = []

    async def _create(model, base_url, api_key, **kw):
        client = SimpleNamespace(model=model, aclose=AsyncMock())
        clients.append(client)
        return client

    with patch("utils.llm_client.create_chat_llm_async", _create), patch.object(warm_pool, "WARM_POOL_MAX_CLIENTS", 2):
        try:
            await warm_pool.get_warm_chat_llm("m0", "u", "k")
            await warm_pool.get_warm_chat_llm("m1", "u", "k")
            await warm_pool.get_warm_chat_llm("m0", "u", "k")  # m0 is now the most recent
            await warm_pool.get_warm_chat_llm("m2", "u", "k")  # drops m1
            again = await warm_pool.get_warm_chat_llm("m1", "u", "k")
            assert again is not clients[1]
            # A call may still hold the dropped client; it is not closed under it.
            clients[1].aclose.assert_not_awaited()
        finally:
            await warm_pool.close_warm_chat_llms()


# ---------------------------------------------------------------------------
# Plumbing: decision keep-alive, Ark beta header, chat logprobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decision_http_client_keeps_idle_connections_open():
    from utils.decision_client import adapters

    adapters._http_clients.clear()
    try:
        client = adapters._decision_http_client()
        assert client._transport._pool._keepalive_expiry == adapters.DECISION_KEEPALIVE_SECONDS
    finally:
        await client.aclose()
        adapters._http_clients.clear()


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("endpoint", "beta_header"),
    [
        ("https://ark.cn-beijing.volces.com/api/v1/systemone", True),
        ("https://api.siliconflow.cn/v1/systemone", False),
    ],
)
async def test_systemone_adds_the_ark_beta_header_only_for_ark(endpoint, beta_header):
    from utils.decision_client import BoolQuestion, SystemOneAdapter

    sent: list[dict] = []

    async def _post(url, payload, headers, timeout):
        sent.append(dict(headers))
        return SimpleNamespace(status_code=200, json=lambda: {"results": {"t": {"noul": 0.8}}})

    with patch("utils.decision_client.adapters._post_decision_request", _post):
        resp = await SystemOneAdapter("systemone").execute(
            state="s", questions={"t": BoolQuestion(instructions="ok?")}, model="m",
            base_url="", api_key="sk", decision_url=endpoint,
        )
    assert resp.answers["t"].probability == pytest.approx(0.8)
    assert sent[0]["Authorization"] == "Bearer sk"
    assert (sent[0].get("ark-beta-systemone-api") == "true") is beta_header


@pytest.mark.unit
def test_first_token_top_logprobs_reads_sdk_objects_and_dicts():
    from utils.llm_client import first_token_top_logprobs, top_logprobs_of

    raw = _raw_with_first_token([("A", -0.1), ("B", -2.5)])
    assert first_token_top_logprobs(raw) == [("A", -0.1), ("B", -2.5)]
    as_dict = {"content": [{"token": "x", "top_logprobs": [{"token": "x", "logprob": -0.2}, {"token": "y", "logprob": None}]}]}
    assert top_logprobs_of(as_dict) == [[("x", -0.2)]]
    assert first_token_top_logprobs(SimpleNamespace(choices=[SimpleNamespace(logprobs=None)])) == []
    assert top_logprobs_of(None) == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_astream_passes_each_chunks_token_candidates_through():
    from utils.llm_client import ChatOpenAI

    def _chunk(text, cands=None, finish=None):
        logprobs = None
        if cands is not None:
            logprobs = SimpleNamespace(content=[SimpleNamespace(token=text, top_logprobs=[SimpleNamespace(token=t, logprob=lp) for t, lp in cands])])
        choice = SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None), logprobs=logprobs, finish_reason=finish)
        return SimpleNamespace(choices=[choice], usage=None)

    class _Stream:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._chunks:
                raise StopAsyncIteration
            return self._chunks.pop(0)

        async def aclose(self):
            return None

    llm = ChatOpenAI(model="doubao-seed-2-0-mini-260428", base_url="https://ark.cn-beijing.volces.com/api/v3", api_key="k")
    stream = _Stream([_chunk("hap", [("hap", -0.1), ("sad", -2.4)]), _chunk("py"), _chunk("", None, "stop")])
    try:
        with patch.object(llm._aclient.chat.completions, "create", AsyncMock(return_value=stream)):
            got = [c async for c in llm.astream([{"role": "user", "content": "hi"}], logprobs=True, top_logprobs=10)]
    finally:
        await llm.aclose()
    assert got[0].content == "hap"
    assert got[0].top_logprobs == [[("hap", -0.1), ("sad", -2.4)]]
    assert got[1].top_logprobs is None
    assert got[-1].finish_reason == "stop"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chat_client_keepalive_option_reaches_its_connection_pool():
    from utils.llm_client import ChatOpenAI

    warm = ChatOpenAI(model="m", base_url="https://x.example/v1", api_key="k", keepalive_expiry=90.0)
    plain = ChatOpenAI(model="m", base_url="https://x.example/v1", api_key="k")
    try:
        assert warm._aclient._client._transport._pool._keepalive_expiry == 90.0
        assert plain._aclient._client._transport._pool._keepalive_expiry == 5.0
    finally:
        await warm.aclose()
        await plain.aclose()


# ---------------------------------------------------------------------------
# Config (D10): the OpenAI recognition model and its thinking-off form
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_openai_recognition_model_is_gpt_6_luna_with_thinking_off():
    from config.api_profiles import DEFAULT_ASSIST_API_PROFILES
    from config.providers import get_extra_body
    from utils.api_config_loader import get_config

    assert DEFAULT_ASSIST_API_PROFILES["openai"]["RECOGNITION_MODEL"] == "gpt-6-luna"
    assert get_config(force_reload=True)["assist_api_providers"]["openai"]["recognition_model"] == "gpt-6-luna"
    assert get_extra_body("gpt-6-luna", "https://api.openai.com/v1") == {"reasoning_effort": "none"}


# ---------------------------------------------------------------------------
# ask_site_questions: one entry point for the sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_site_questions_native_never_uses_the_clients_chat_fallback():
    from utils.decision_client import DecisionAnswer, DecisionClient, DecisionResponse, ask_site_questions

    qs = {"kind": _question(2)}
    resp = DecisionResponse(
        answers={"kind": DecisionAnswer(question_type="choice", value="opt1", probabilities={"opt1": 0.9, "opt0": 0.1})},
        model="decision-model", protocol="systemone",
    )
    with patch.object(DecisionClient, "decide", AsyncMock(return_value=resp)) as decide:
        got = await ask_site_questions(SITE_MASTER_EMOTION, state="s", questions=qs, chat_config=DOUBAO_CHAT,
                                       decision_config=BAILIAN, timeout=2.0, call_type="x")
    assert got["kind"].value == "opt1"
    kw = decide.await_args.kwargs
    assert kw["allow_fallback"] is False and kw["respect_cooldown"] is True
    assert kw["config"] is BAILIAN and kw["timeout"] == 2.0


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("outcome", ["raise", "partial"])
async def test_ask_site_questions_native_miss_means_the_sites_chat(outcome):
    from utils.decision_client import DecisionAnswer, DecisionClient, DecisionResponse, ask_site_questions

    qs = {"a": _question(2), "b": _question(2)}
    if outcome == "raise":
        mock = AsyncMock(side_effect=RuntimeError("cooldown"))
    else:
        mock = AsyncMock(return_value=DecisionResponse(
            answers={"a": DecisionAnswer(question_type="choice", value="opt0")}, model="m", protocol="systemone"))
    with patch.object(DecisionClient, "decide", mock):
        assert await ask_site_questions(SITE_MASTER_EMOTION, state="s", questions=qs, chat_config=None,
                                        decision_config=BAILIAN) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_site_questions_rehosts_or_returns_none_by_route():
    from utils.decision_client import ask_site_questions

    qs = {"kind": _question(2)}
    sentinel = {"kind": object()}
    with patch("utils.decision_client.rehost.rehost_choices", AsyncMock(return_value=sentinel)) as rh:
        assert await ask_site_questions(SITE_MASTER_EMOTION, state="s", questions=qs, chat_config=DOUBAO_CHAT,
                                        decision_config=CHAT_FALLBACK) is sentinel
        # A site without a rehost reading keeps its chat on Doubao.
        assert await ask_site_questions(SITE_MASTER_EMOTION, state="s", questions=qs, chat_config=DOUBAO_CHAT,
                                        decision_config=CHAT_FALLBACK, rehost=False) is None
        # Class C.
        assert await ask_site_questions(SITE_MASTER_EMOTION, state="s", questions=qs,
                                        chat_config=_chat("https://open.bigmodel.cn/api/paas/v4"),
                                        decision_config=CHAT_FALLBACK) is None
    assert rh.await_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ask_site_questions_reads_the_decision_slot_when_not_given():
    from unittest.mock import MagicMock

    from utils.decision_client import ask_site_questions

    cm = MagicMock()
    cm.aget_model_api_config = AsyncMock(return_value=CHAT_FALLBACK)
    assert await ask_site_questions(SITE_MASTER_EMOTION, state="s", questions={"k": _question(2)},
                                    chat_config=None, config_manager=cm) is None
    cm.aget_model_api_config.assert_awaited_once_with("decision")
