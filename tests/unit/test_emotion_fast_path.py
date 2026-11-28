# -*- coding: utf-8 -*-
"""Outward emotion: decision question, Doubao first-token reading, emoji after the avatar (D20-D22)."""

from __future__ import annotations

import asyncio
import importlib
import math
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

DOUBAO = {"model": "doubao-seed-2-0-mini-260428", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
          "api_key": "ak", "provider_type": "openai_compatible"}
OTHER = {"model": "glm-4.7-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": "k"}


class _Request:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture
def em(monkeypatch):
    module = importlib.import_module("main_routers.system_router.emotion")
    ed = importlib.import_module("main_routers.system_router.emotion_decision")
    monkeypatch.setattr(module, "_validate_local_mutation_request", lambda request: None)
    pushed: list = []
    monkeypatch.setattr(module, "_push_emotion_update", lambda *args: pushed.append(args))
    monkeypatch.setattr(module, "_infer_emotion_from_text", lambda text: (None, 0))
    ed.clear_pending_reactions()
    state = SimpleNamespace(chat_config=dict(OTHER), chat_calls=0, pushed=pushed, ed=ed)

    class Config:
        async def aget_model_api_config(self, tier):
            return dict(state.chat_config)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages):
            state.chat_calls += 1
            return SimpleNamespace(content='{"emotion": "sad", "confidence": 0.9, "emoji": "😢"}')

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(module, "get_config_manager", lambda: Config())
    monkeypatch.setattr(module, "create_chat_llm_async", factory)
    state.module = module
    yield state
    ed.clear_pending_reactions()


def _analyze(state, text="I am so happy today!"):
    return asyncio.run(_analyze_async(state, text))


async def _analyze_async(state, text):
    return await state.module.emotion_analysis(_Request({"text": text, "lanlan_name": "NEKO"}))


# ── reading ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("probs", "expected"), [
    ({"happy": 0.8, "neutral": 0.2}, ("happy", 0.8)),
    ({"happy": 0.6, "neutral": 0.4}, ("neutral", 0.4)),  # 0.6 < tau 0.7
    ({"sad": 0.3, "angry": 0.1, "neutral": 0.1}, ("sad", 0.75)),  # ratio over neutral only
    ({"neutral": 0.9}, ("neutral", 0.9)),
])
def test_read_against_neutral(em, probs, expected):
    label, conf = em.ed.read_against_neutral(probs)
    assert label == expected[0] and conf == pytest.approx(expected[1])


def test_first_token_candidates_merge_by_label(em):
    probs = em.ed.label_probabilities([
        ("happy", math.log(0.5)), ("hap", math.log(0.1)), ("sur", math.log(0.2)),
        ("s", math.log(0.1)),  # sad or surprised: unreadable
        ("neutral", math.log(0.05)), ("{", math.log(0.05)),
    ])
    assert probs == pytest.approx({"happy": 0.6, "surprised": 0.2, "neutral": 0.05})


def test_first_token_reading_is_doubao_chat_only(em):
    assert em.ed.first_token_reading_usable(DOUBAO)
    assert not em.ed.first_token_reading_usable(OTHER)
    assert not em.ed.first_token_reading_usable({**DOUBAO, "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3"})
    assert not em.ed.first_token_reading_usable({**DOUBAO, "base_url": "https://www.lanlan.tech/text/v1"})


# ── decision question (D21) ──────────────────────────────────────────


def test_decision_answers_at_once_and_the_emoji_follows(em, monkeypatch):
    async def _decide(text, *, chat_config, config_manager):
        return "happy", 0.9

    emoji_calls: list = []

    async def _pick(text, *, chat_config):
        emoji_calls.append(text)
        return "🫠"

    monkeypatch.setattr(em.ed, "decide_emotion", _decide)
    monkeypatch.setattr(em.ed, "pick_emoji", _pick)

    async def _run():
        resp = await _analyze_async(em, "I am so happy today!")
        assert resp["emotion"] == "happy" and resp["confidence"] == 0.9
        assert resp["reaction"] is None and resp["reaction_pending"]
        later = await em.module.emotion_reaction(_Request({"token": resp["reaction_pending"]}))
        again = await em.module.emotion_reaction(_Request({"token": resp["reaction_pending"]}))
        return resp, later, again

    resp, later, again = asyncio.run(_run())
    assert em.chat_calls == 0  # the long chat prompt is not asked
    assert em.pushed == [("NEKO", "happy", 0.9)]
    assert later == {"reaction": {"emoji": "🫠", "author": "NEKO"}}
    assert again == {"reaction": None}  # one collection per token
    assert emoji_calls == ["I am so happy today!"]


def test_decision_without_reaction_skips_the_emoji_chat(em, monkeypatch):
    async def _decide(text, *, chat_config, config_manager):
        return "neutral", 0.8

    async def _pick(text, *, chat_config):
        raise AssertionError("no reaction, no emoji chat")

    monkeypatch.setattr(em.ed, "decide_emotion", _decide)
    monkeypatch.setattr(em.ed, "pick_emoji", _pick)
    resp = _analyze(em)
    assert resp["emotion"] == "neutral" and resp["reaction"] is None and "reaction_pending" not in resp


def test_emoji_chat_junk_picks_by_emotion(em, monkeypatch):
    async def _decide(text, *, chat_config, config_manager):
        return "sad", 0.95

    async def _pick(text, *, chat_config):
        return "none"

    monkeypatch.setattr(em.ed, "decide_emotion", _decide)
    monkeypatch.setattr(em.ed, "pick_emoji", _pick)
    monkeypatch.setattr(em.module.random, "choice", lambda items: items[0])

    async def _run():
        resp = await _analyze_async(em, "I miss you")
        return await em.module.emotion_reaction(_Request({"token": resp["reaction_pending"]}))

    got = asyncio.run(_run())
    assert got["reaction"]["emoji"] == em.module.MESSAGE_REACTION_EMOJIS_BY_EMOTION["sad"][0]


def test_decision_miss_uses_the_chat_prompt(em, monkeypatch):
    async def _decide(text, *, chat_config, config_manager):
        return None

    monkeypatch.setattr(em.ed, "decide_emotion", _decide)
    resp = _analyze(em)
    assert em.chat_calls == 1 and resp["emotion"] == "sad" and "reaction_pending" not in resp


def test_request_with_its_own_model_keeps_the_chat_prompt(em, monkeypatch):
    decided: list = []

    async def _decide(*a, **k):
        # Recorded, not raised: the fast path would swallow an error and fall back.
        decided.append(a)
        return "happy", 0.9

    monkeypatch.setattr(em.ed, "decide_emotion", _decide)
    resp = asyncio.run(em.module.emotion_analysis(_Request({"text": "hi", "lanlan_name": "NEKO", "model": "m"})))
    assert decided == []
    assert em.chat_calls == 1 and resp["emotion"] == "sad"


# ── Doubao first-token reading (D22) ────────────────────────────────


def _chunk(content, cands=None):
    return SimpleNamespace(content=content, top_logprobs=[cands] if cands is not None else None)


class _StreamLLM:
    def __init__(self, chunks, gate=None):
        self.chunks = chunks
        self.gate = gate
        self.kwargs = None
        self.messages = None
        self.closed_early = False

    def astream(self, messages, **kw):
        self.messages = messages
        self.kwargs = kw
        outer = self

        async def _gen():
            try:
                for i, c in enumerate(outer.chunks):
                    if i == 1 and outer.gate is not None:
                        await outer.gate.wait()
                    yield c
            except (asyncio.CancelledError, GeneratorExit):
                outer.closed_early = True
                raise

        return _gen()


def _use_doubao(em, monkeypatch, llm):
    em.chat_config = dict(DOUBAO)

    async def _decide(text, *, chat_config, config_manager):
        return None  # Doubao has no native decision for this site

    async def _warm(*a, **k):
        return llm

    monkeypatch.setattr(em.ed, "decide_emotion", _decide)
    monkeypatch.setattr("utils.llm_client.warm_pool.get_warm_chat_llm", _warm)


def test_doubao_emotion_comes_from_the_first_token_before_the_reply_ends(em, monkeypatch):
    async def _run():
        gate = asyncio.Event()
        llm = _StreamLLM([
            _chunk("happy", [("happy", math.log(0.85)), ("neutral", math.log(0.15))]),
            _chunk('", "confidence": 0.9, "emoji": "🥳"}'),
        ], gate=gate)
        _use_doubao(em, monkeypatch, llm)
        resp = await _analyze_async(em, "Yay, we won!")
        # The answer came back while the stream still waits on its second chunk.
        assert resp["emotion"] == "happy" and resp["confidence"] == pytest.approx(0.85)
        assert resp["reaction"] is None and resp["reaction_pending"]
        gate.set()
        got = await em.module.emotion_reaction(_Request({"token": resp["reaction_pending"]}))
        return llm, got

    llm, got = asyncio.run(_run())
    assert em.chat_calls == 0
    assert llm.messages[-1] == {"role": "assistant", "content": '{"emotion": "'}
    assert llm.kwargs["logprobs"] is True and llm.kwargs["top_logprobs"] == 10
    assert got == {"reaction": {"emoji": "🥳", "author": "NEKO"}}


def test_doubao_emoji_is_dropped_when_the_model_wrote_another_emotion(em, monkeypatch):
    llm = _StreamLLM([
        _chunk("sur", [("sur", math.log(0.5)), ("happy", math.log(0.45)), ("neutral", math.log(0.05))]),
        _chunk('prised", "confidence": 0.6, "emoji": "😲"}'),
    ])
    _use_doubao(em, monkeypatch, llm)
    # surprised 0.5 vs neutral 0.05 -> surprised; the model wrote surprised too: emoji kept.
    monkeypatch.setattr(em.module.random, "choice", lambda items: items[-1])

    async def _run(chunks):
        llm.chunks = chunks
        resp = await _analyze_async(em, "Whoa!")
        return resp, await em.module.emotion_reaction(_Request({"token": resp.get("reaction_pending")}))

    resp, got = asyncio.run(_run(llm.chunks))
    assert resp["emotion"] == "surprised" and got["reaction"]["emoji"] == "😲"
    resp, got = asyncio.run(_run([
        _chunk("happy", [("happy", math.log(0.1)), ("angry", math.log(0.85)), ("neutral", math.log(0.05))]),
        _chunk('", "confidence": 0.6, "emoji": "😄"}'),
    ]))
    # The probabilities say angry, the model wrote happy: its emoji is not used.
    assert resp["emotion"] == "angry"
    assert got["reaction"]["emoji"] == em.module.MESSAGE_REACTION_EMOJIS_BY_EMOTION["angry"][-1]


def test_doubao_without_first_token_probabilities_reads_the_text(em, monkeypatch):
    llm = _StreamLLM([_chunk("sad"), _chunk('", "confidence": 0.9, "emoji": "😢"}')])
    _use_doubao(em, monkeypatch, llm)
    resp = _analyze(em, "I'm sad")
    assert em.chat_calls == 0
    assert resp["emotion"] == "sad" and resp["reaction"] == {"emoji": "😢", "author": "NEKO"}


def test_doubao_neutral_closes_the_stream_early(em, monkeypatch):
    async def _run():
        gate = asyncio.Event()
        llm = _StreamLLM([
            _chunk("neutral", [("neutral", math.log(0.9)), ("happy", math.log(0.1))]),
            _chunk('", "confidence": 0.9, "emoji": null}'),
        ], gate=gate)
        _use_doubao(em, monkeypatch, llm)
        resp = await _analyze_async(em, "ok")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return llm, resp

    llm, resp = asyncio.run(_run())
    assert resp["emotion"] == "neutral" and "reaction_pending" not in resp
    assert llm.closed_early


# ── pending reactions ────────────────────────────────────────────────


def test_unknown_or_missing_token_collects_nothing(em):
    assert asyncio.run(em.module.emotion_reaction(_Request({"token": "nope"}))) == {"reaction": None}
    assert asyncio.run(em.module.emotion_reaction(_Request({}))) == {"reaction": None}


def test_old_pending_reactions_expire_without_being_made(em, monkeypatch):
    made, dropped = [], []

    async def _run():
        async def _make():
            made.append(1)

        token = em.ed.register_pending_reaction(_make, on_drop=lambda: dropped.append(1))
        later = em.ed._pending[token][1] + em.ed.PENDING_REACTION_TTL_SECONDS + 1
        from tests.fake_clock import patch_module_clock

        patch_module_clock(monkeypatch, em.ed, monotonic=lambda: later)
        em.ed.register_pending_reaction(_make)
        await asyncio.sleep(0)
        return token

    token = asyncio.run(_run())
    assert token not in em.ed._pending and dropped == [1] and made == []


def test_decide_emotion_asks_one_english_question_without_rehost(em, monkeypatch):
    import utils.decision_client as dc
    from config.prompts.prompts_emotion import OUTWARD_EMOTION_DECISION_INSTRUCTIONS

    seen = {}

    async def _ask(site, *, state, questions, **kw):
        seen.update(site=site, state=state, questions=questions, kw=kw)
        return {"emotion": SimpleNamespace(value="angry", probabilities={"angry": 0.6, "neutral": 0.2, "sad": 0.2})}

    monkeypatch.setattr(dc, "ask_site_questions", _ask)
    got = asyncio.run(em.ed.decide_emotion("You never listen!", chat_config=OTHER, config_manager=None))
    assert got == ("angry", pytest.approx(0.75))
    assert seen["site"] == "emotion_display" and seen["state"] == "You never listen!"
    assert seen["kw"]["rehost"] is False and seen["kw"]["timeout"] == em.ed.DECISION_TIMEOUT_SECONDS
    assert seen["questions"]["emotion"].instructions == OUTWARD_EMOTION_DECISION_INSTRUCTIONS
    assert set(seen["questions"]["emotion"].criteria) == set(em.ed.LABELS)


def test_pick_emoji_asks_for_one_emoji_in_16_tokens(em, monkeypatch):
    calls = {}

    class _LLM:
        async def ainvoke(self, messages, **kw):
            calls.update(messages=messages, kw=kw)
            return SimpleNamespace(content=" 🫶 ")

    async def _warm(*a, **k):
        return _LLM()

    monkeypatch.setattr("utils.llm_client.warm_pool.get_warm_chat_llm", _warm)
    got = asyncio.run(em.ed.pick_emoji("thank you so much", chat_config=OTHER))
    assert got == "🫶"
    assert calls["kw"]["max_completion_tokens"] == 16
    system = calls["messages"][0]["content"]
    assert "{pool}" not in system and "😊" in system and "only suggestions" in system
    assert calls["messages"][1] == {"role": "user", "content": "thank you so much"}


def test_a_pending_reaction_past_its_time_is_not_collected(em, monkeypatch):
    # Expiry is checked when it is collected too, not only when another is registered.
    async def _run():
        async def _ready():
            return {"emoji": "😊", "author": "NEKO"}

        token = em.ed.register_pending_reaction(_ready, on_drop=lambda: dropped.append(1))
        later = em.ed._pending[token][1] + em.ed.PENDING_REACTION_TTL_SECONDS + 1
        from tests.fake_clock import patch_module_clock

        patch_module_clock(monkeypatch, em.ed, monotonic=lambda: later)
        return await em.ed.collect_pending_reaction(token)

    dropped: list = []
    assert asyncio.run(_run()) is None and dropped == [1]


def test_a_decision_without_probabilities_uses_the_chat_prompt(em, monkeypatch):
    import utils.decision_client as dc

    async def _ask(site, **kw):
        return {"emotion": SimpleNamespace(value="happy", probabilities={})}

    monkeypatch.setattr(dc, "ask_site_questions", _ask)
    assert asyncio.run(em.ed.decide_emotion("hi", chat_config=OTHER, config_manager=None)) is None


def test_nobody_collecting_means_no_emoji_chat(em, monkeypatch):
    # Proactive turns, agent callbacks and games have no message to react to:
    # the frontend never collects, and the small chat must not run.
    async def _decide(text, *, chat_config, config_manager):
        return "happy", 0.95

    calls: list = []

    async def _pick(text, *, chat_config):
        calls.append(text)
        return "😊"

    monkeypatch.setattr(em.ed, "decide_emotion", _decide)
    monkeypatch.setattr(em.ed, "pick_emoji", _pick)

    async def _run():
        resp = await _analyze_async(em, "Yay!")
        await asyncio.sleep(0.05)
        return resp

    resp = asyncio.run(_run())
    assert resp["reaction_pending"] and calls == []


def test_a_slow_doubao_first_token_is_read_as_the_chat_answer_without_a_second_request(em, monkeypatch):
    monkeypatch.setattr(em.ed, "FIRST_TOKEN_TIMEOUT_SECONDS", 0.05)

    class _Slow(_StreamLLM):
        def astream(self, messages, **kw):
            self.calls = getattr(self, "calls", 0) + 1
            outer = self

            async def _gen():
                await asyncio.sleep(0.2)  # past the first-token wait
                for c in outer.chunks:
                    yield c

            return _gen()

    llm = _Slow([_chunk("sad", [("sad", -0.1)]), _chunk('", "confidence": 0.9, "emoji": "😢"}')])
    _use_doubao(em, monkeypatch, llm)
    resp = _analyze(em, "I'm sad")
    assert llm.calls == 1 and em.chat_calls == 0  # the same stream, nothing sent again
    assert resp["emotion"] == "sad" and resp["reaction"] == {"emoji": "😢", "author": "NEKO"}


def test_all_zero_probabilities_mean_the_chat_prompt(em, monkeypatch):
    import utils.decision_client as dc

    async def _ask(site, **kw):
        return {"emotion": SimpleNamespace(value="happy", probabilities={"happy": 0.0, "neutral": 0.0})}

    monkeypatch.setattr(dc, "ask_site_questions", _ask)
    assert asyncio.run(em.ed.decide_emotion("hi", chat_config=OTHER, config_manager=None)) is None


def test_a_whole_answer_written_over_the_prefill_is_not_prefixed_twice(em):
    async def _run():
        async def _whole():
            return ' {"emotion": "sad", "confidence": 0.9}'

        reading = em.ed.FirstTokenReading({}, "", asyncio.ensure_future(_whole()))
        return await reading.full_text()

    assert asyncio.run(_run()) == ' {"emotion": "sad", "confidence": 0.9}'



def test_a_slow_model_emoji_still_leaves_one_picked_for_the_emotion(em, monkeypatch):
    async def _decide(text, *, chat_config, config_manager):
        return "sad", 0.95

    async def _pick(text, *, chat_config):
        await asyncio.sleep(10)
        return "🫠"

    monkeypatch.setattr(em.ed, "decide_emotion", _decide)
    monkeypatch.setattr(em.ed, "pick_emoji", _pick)
    monkeypatch.setattr(em.ed, "REACTION_EMOJI_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(em.ed, "PENDING_REACTION_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(em.module.random, "choice", lambda items: items[0])

    async def _run():
        resp = await _analyze_async(em, "I miss you")
        return await em.module.emotion_reaction(_Request({"token": resp["reaction_pending"]}))

    got = asyncio.run(_run())
    assert got["reaction"] == {"emoji": em.module.MESSAGE_REACTION_EMOJIS_BY_EMOTION["sad"][0], "author": "NEKO"}
    assert em.ed.REACTION_EMOJI_WAIT_SECONDS < em.ed.PENDING_REACTION_WAIT_SECONDS


def test_an_uncollected_reaction_is_dropped_on_time(em, monkeypatch):
    monkeypatch.setattr(em.ed, "PENDING_REACTION_TTL_SECONDS", 0.05)
    dropped: list = []

    async def _make():
        raise AssertionError("never made")

    async def _run():
        token = em.ed.register_pending_reaction(_make, on_drop=lambda: dropped.append(1))
        await asyncio.sleep(0.2)
        return token

    token = asyncio.run(_run())
    # Dropped by its own timer, with nothing else registered or collected.
    assert dropped == [1] and token not in em.ed._pending and token not in em.ed._expiry


def test_a_collected_reaction_cancels_its_timer(em, monkeypatch):
    async def _make():
        return {"emoji": "🫠", "author": "NEKO"}

    async def _run():
        token = em.ed.register_pending_reaction(_make)
        assert token in em.ed._expiry
        got = await em.ed.collect_pending_reaction(token)
        return token, got

    token, got = asyncio.run(_run())
    assert got == {"emoji": "🫠", "author": "NEKO"} and token not in em.ed._expiry
