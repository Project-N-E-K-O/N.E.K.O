# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Public visit persona (OD-10 v3; visit design §4.6 persona, §5 PR-09a ``persona.py``)."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config.visit_settings as visit_settings
from config.prompts.prompts_visit import build_visit_instructions, get_family_neutral_term
from main_logic.visit.sanitize import ngram_units
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from main_routers.visit_router import persona
from main_routers.visit_router import router as visit_router
from main_routers.visit_router.local_context import CharacterContext

ORIGIN = "http://testserver"
GOOD = {"Origin": ORIGIN, "X-CSRF-Token": AUTOSTART_CSRF_TOKEN}
UID_A = "a" * 32
UID_B = "b" * 32

PUBLIC = ["你是{LANLAN_NAME}，一只活泼好动的橘色猫娘，说话尾巴总爱带一个喵字。",
          "喜欢晒太阳、追毛线球和吃小鱼干，讨厌洗澡和打雷的夜晚。"]
PRIVATE = ["你的亲人叫小明，你们住在桂花路的老房子里。",
           "小明每周三都要加班到很晚，你会在门口等他回家。",
           "小明的微信号是 xiaoming_1990，QQ 123456。"]
CARD = "\n".join(PUBLIC + PRIVATE)
FAMILY = ("小明",)
GOOD_PERSONA = "你是{LANLAN_NAME}，一只活泼好动的橘色猫娘，口头禅是喵。喜欢晒太阳和吃小鱼干，讨厌洗澡。"


class FakeLLM:
    """Generation call: returns queued replies in order (the last one repeats)."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or [GOOD_PERSONA]
        self.prompts: list[str] = []
        self.fail = False

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("boom")
        return self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]


class FakeScan:
    def __init__(self, sections=None) -> None:
        self.sections = list(PRIVATE[:2]) if sections is None else sections
        self.calls = 0
        self.fail = False

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("scan down")
        return json.dumps(self.sections, ensure_ascii=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    persona._reset_for_tests()
    state = {"cards": {"A": CARD, "B": "你是{LANLAN_NAME}，一只安静的白猫。"}, "uids": {"A": UID_A, "B": UID_B}}
    llm, scan = FakeLLM(), FakeScan()

    async def load_context():
        return CharacterContext(family_names=FAMILY, cards=dict(state["cards"]))

    async def resolve(name):
        return state["uids"].get(name)

    saved = persona._hooks.__dict__.copy()
    persona.configure_persona(config_dir=lambda: tmp_path, load_context=load_context, resolve_char_uid=resolve,
                              llm=llm, scan_llm=scan, lang=lambda: "zh")
    monkeypatch.setattr(visit_settings, "VISIT_ENABLED", True)
    monkeypatch.setattr(visit_settings, "NEKO_VISIT_ALLOW_NONLOCAL", False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    app = FastAPI()
    app.include_router(visit_router)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client, tmp_path, state, llm, scan
    persona._reset_for_tests()
    persona._hooks.__dict__.update(saved)


def _settle(client) -> None:
    async def wait():
        jobs = list(persona._jobs.values())
        if jobs:
            await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 5)

    client.portal.call(wait)


def _generate(client, name="A"):
    resp = client.post(f"/api/visit/persona/regenerate?catgirl={name}", headers=GOOD, json={})
    assert resp.status_code == 202, resp.text
    _settle(client)
    return client.get(f"/api/visit/persona?catgirl={name}", headers=GOOD).json()


def _file(tmp_path, uid=UID_A) -> dict:
    return json.loads((tmp_path / "visit_persona" / f"{uid}.json").read_text(encoding="utf-8"))


def _shares_ngram(text: str, source: str, n: int = 8) -> bool:
    a, b = ngram_units(text), ngram_units(source)
    grams = {tuple(b[i:i + n]) for i in range(len(b) - n + 1)}
    return any(tuple(a[i:i + n]) in grams for i in range(len(a) - n + 1))


# ── 生成与隐私检查 ─────────────────────────────────────────────────────


def test_instructions_carry_only_the_public_persona(env):
    client, tmp_path, *_ = env
    view = _generate(client)
    assert view["state"] == "unreviewed" and view["text"]
    instructions = build_visit_instructions("A", "guest", "zh", persona_text=view["text"],
                                            memory_block="", peer_display="")
    for section in PRIVATE:
        assert not _shares_ngram(instructions, section)
    assert "小明" not in instructions and "桂花路" not in instructions


def test_private_overlap_regenerates_once_then_refuses(env):
    client, tmp_path, _state, llm, _scan = env
    leaking = GOOD_PERSONA + "小明每周三都要加班到很晚，你会在门口等他。"
    llm.replies = [leaking, leaking]
    view = _generate(client)
    assert len(llm.prompts) == 2
    assert view["state"] == "missing" and view["error"] == "persona_sensitive_overlap"
    assert not (tmp_path / "visit_persona").exists() or not any((tmp_path / "visit_persona").iterdir())


def test_overlap_then_clean_regeneration_is_saved(env):
    client, _tmp, _state, llm, _scan = env
    llm.replies = [GOOD_PERSONA + "小明每周三都要加班到很晚，你会在门口等他。", GOOD_PERSONA]
    view = _generate(client)
    assert len(llm.prompts) == 2 and view["state"] == "unreviewed" and "error" not in view


def test_rule_sections_catch_a_private_sentence_the_scan_missed(env):
    client, _tmp, _state, llm, scan = env
    scan.sections = []
    # 「小明」会被脱敏、句子里也没有规则敏感词：只有规则段落的 8-gram 对照能挡住
    leaking = GOOD_PERSONA + "小明每周三都要加班到很晚，你会在门口等他。"
    llm.replies = [leaking, leaking]
    view = _generate(client)
    assert view["error"] == "persona_sensitive_overlap"


@pytest.mark.parametrize("leak", ["QQ 123456", "桂花路"])
def test_short_sensitive_tokens_are_caught_even_when_the_scan_misses_them(env, leak):
    client, _tmp, _state, llm, scan = env
    scan.sections = []
    llm.replies = [GOOD_PERSONA + f"她常提起{leak}。"]
    view = _generate(client)
    assert view["error"] == "persona_sensitive_overlap"


def test_family_names_are_redacted_from_generated_text(env):
    client, _tmp, _state, llm, _scan = env
    llm.replies = ["你是{LANLAN_NAME}，喜欢和小明一起晒太阳。"]
    view = _generate(client)
    assert "小明" not in view["text"] and get_family_neutral_term("zh") in view["text"]


def test_private_sections_persist_and_get_never_rescans(env):
    client, tmp_path, _state, _llm, scan = env
    view = _generate(client)
    assert scan.calls == 1
    sections = view["private_sections"]
    assert set(PRIVATE[:2]) <= set(sections) and view["scan_complete"] is True
    # 「重启」：清掉进程内状态后再读
    persona._reset_for_tests()
    again = client.get("/api/visit/persona?catgirl=A", headers=GOOD).json()
    assert again["private_sections"] == sections and scan.calls == 1
    client.put("/api/visit/persona?catgirl=A", headers=GOOD, json={"text": GOOD_PERSONA, "reviewed": True})
    assert client.get("/api/visit/persona?catgirl=A", headers=GOOD).json()["private_sections"] == sections
    scan.sections = [PRIVATE[2]]
    regenerated = _generate(client)
    assert scan.calls == 2 and PRIVATE[2] in regenerated["private_sections"]
    assert regenerated["reviewed"] is False and regenerated["edited"] is False


def test_failed_scan_is_persisted_as_incomplete(env):
    client, tmp_path, _state, _llm, scan = env
    scan.fail = True
    view = _generate(client)
    assert view["scan_complete"] is False and _file(tmp_path)["scan_complete"] is False
    # 规则段落仍在清单里（亲人名 / 账号 / 地址所在的句子）
    assert set(PRIVATE) <= set(view["private_sections"])
    persona._reset_for_tests()
    assert client.get("/api/visit/persona?catgirl=A", headers=GOOD).json()["scan_complete"] is False
    scan.fail = False
    assert _generate(client)["scan_complete"] is True


def test_generation_failure_reports_llm_unavailable_and_writes_nothing(env):
    client, tmp_path, _state, llm, _scan = env
    llm.fail = True
    view = _generate(client)
    assert view["state"] == "missing" and view["error"] == "llm_unavailable"


def test_card_is_cut_to_its_input_budget(env, monkeypatch):
    client, _tmp, state, llm, _scan = env
    monkeypatch.setattr(persona, "PERSONA_CARD_MAX_TOKENS", 20)
    state["cards"]["A"] = CARD + "\n" + "很长的设定" * 2000
    _generate(client)
    assert all(len(p) < 4000 for p in llm.prompts)


# ── 闸门 ───────────────────────────────────────────────────────────────


def _gate(client, name="A"):
    return client.portal.call(persona.persona_gate, name)


def test_gate_refuses_missing_and_unreviewed(env):
    client, tmp_path, *_ = env
    assert _gate(client).ok is False and _gate(client).state == "missing"
    _generate(client)
    gate = _gate(client)
    assert gate.ok is False and gate.state == "unreviewed"
    client.put("/api/visit/persona?catgirl=A", headers=GOOD, json={"reviewed": True})
    gate = _gate(client)
    assert gate.ok and gate.state == "ready" and gate.text == _file(tmp_path)["text"]


def test_card_change_without_edit_regenerates_and_requires_review(env):
    client, tmp_path, state, llm, _scan = env
    _generate(client)
    client.put("/api/visit/persona?catgirl=A", headers=GOOD, json={"reviewed": True})
    calls = len(llm.prompts)
    state["cards"]["A"] = CARD + "\n新加了一句：喜欢看雨。"
    gate = _gate(client)
    assert gate.ok is False and gate.state == "generating"
    _settle(client)
    assert len(llm.prompts) == calls + 1
    doc = _file(tmp_path)
    assert doc["source_card_hash"] == persona.card_hash(state["cards"]["A"]) and doc["reviewed"] is False
    assert _gate(client).ok is False
    client.put("/api/visit/persona?catgirl=A", headers=GOOD, json={"reviewed": True})
    assert _gate(client).ok


def test_edited_persona_survives_a_card_change(env):
    client, tmp_path, state, llm, _scan = env
    _generate(client)
    hand = "你是{LANLAN_NAME}，一只爱睡觉的猫。"
    client.put("/api/visit/persona?catgirl=A", headers=GOOD, json={"text": hand, "reviewed": True})
    before = (tmp_path / "visit_persona" / f"{UID_A}.json").read_bytes()
    calls = len(llm.prompts)
    state["cards"]["A"] = CARD + "\n又改了卡。"
    gate = _gate(client)
    assert gate.ok and gate.text == hand
    assert (tmp_path / "visit_persona" / f"{UID_A}.json").read_bytes() == before and len(llm.prompts) == calls
    view = client.get("/api/visit/persona?catgirl=A", headers=GOOD).json()
    assert view["card_changed"] is True and view["edited"] is True
    regenerated = _generate(client)
    assert regenerated["edited"] is False and regenerated["reviewed"] is False


def test_persona_is_keyed_by_character_uid(env):
    client, tmp_path, state, *_ = env
    _generate(client)
    client.put("/api/visit/persona?catgirl=A", headers=GOOD, json={"reviewed": True})
    # 改名：同一个 character_uid 换了名字，读到的还是同一份文件
    state["cards"]["A2"] = state["cards"].pop("A")
    state["uids"]["A2"] = state["uids"].pop("A")
    view = client.get("/api/visit/persona?catgirl=A2", headers=GOOD).json()
    assert view["state"] == "ready" and view["character_uid"] == UID_A
    if os.name != "nt":
        mode = stat.S_IMODE((tmp_path / "visit_persona" / f"{UID_A}.json").stat().st_mode)
        assert mode == 0o600


# ── PUT / 端点 ─────────────────────────────────────────────────────────


def test_put_redacts_family_names(env):
    client, tmp_path, *_ = env
    resp = client.put("/api/visit/persona?catgirl=A", headers=GOOD,
                      json={"text": "你是{LANLAN_NAME}，最喜欢小明了。", "reviewed": True})
    assert resp.status_code == 200
    text = _file(tmp_path)["text"]
    assert "小明" not in text and get_family_neutral_term("zh") in text


def test_put_with_a_sensitive_token_is_refused_and_keeps_the_file(env):
    client, tmp_path, *_ = env
    _generate(client)
    before = (tmp_path / "visit_persona" / f"{UID_A}.json").read_bytes()
    resp = client.put("/api/visit/persona?catgirl=A", headers=GOOD,
                      json={"text": "你是{LANLAN_NAME}，号码 123456。", "reviewed": True})
    assert resp.status_code == 400
    assert resp.json()["code"] == "persona_sensitive_overlap" and "123456" in resp.json()["hits"]
    assert (tmp_path / "visit_persona" / f"{UID_A}.json").read_bytes() == before


def test_put_over_the_token_budget_is_refused(env):
    client, *_ = env
    resp = client.put("/api/visit/persona?catgirl=A", headers=GOOD,
                      json={"text": "猫" * 5000, "reviewed": True})
    assert resp.status_code == 400 and resp.json()["code"] == "persona_too_long"


def test_put_and_regenerate_while_generating_answer_409(env):
    client, _tmp, _state, llm, _scan = env
    gate = asyncio.Event()

    async def slow(prompt):
        await gate.wait()
        return GOOD_PERSONA

    persona.configure_persona(llm=slow)
    assert client.post("/api/visit/persona/regenerate?catgirl=A", headers=GOOD, json={}).status_code == 202
    assert client.get("/api/visit/persona?catgirl=A", headers=GOOD).json()["state"] == "generating"
    assert client.post("/api/visit/persona/regenerate?catgirl=A", headers=GOOD, json={}).status_code == 409
    assert client.put("/api/visit/persona?catgirl=A", headers=GOOD,
                      json={"reviewed": True}).status_code == 409
    client.portal.call(gate.set)
    _settle(client)


def test_unknown_character_is_404(env):
    client, *_ = env
    assert client.get("/api/visit/persona?catgirl=Z", headers=GOOD).status_code == 404


@pytest.mark.parametrize("method,path", [
    ("get", "/api/visit/persona?catgirl=A"),
    ("put", "/api/visit/persona?catgirl=A"),
    ("post", "/api/visit/persona/regenerate?catgirl=A"),
])
def test_persona_endpoints_need_csrf_and_loopback(env, method, path):
    client, *_ = env
    body = {"reviewed": True} if method != "get" else None
    kwargs = {"json": body} if body is not None else {}
    assert getattr(client, method)(path, headers={"Origin": ORIGIN}, **kwargs).status_code == 403
    lan = TestClient(client.app, client=("192.168.1.20", 5000))
    assert getattr(lan, method)(path, headers=GOOD, **kwargs).status_code == 403


def test_persona_endpoints_are_behind_the_release_switch(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(visit_settings, "VISIT_ENABLED", False)
    assert client.get("/api/visit/persona?catgirl=A", headers=GOOD).status_code == 404
    assert client.post("/api/visit/persona/regenerate?catgirl=A", headers=GOOD, json={}).status_code == 404


# ── 纯函数 ─────────────────────────────────────────────────────────────


def test_sensitive_tokens_cover_contacts_addresses_and_family():
    tokens = persona.extract_sensitive_tokens(
        CARD + "\n邮箱 cat@example.com，主页 https://example.com/me，家在幸福小区3栋502室。", FAMILY,
    )
    for expected in ("小明", "123456", "xiaoming_1990", "桂花路", "cat@example.com", "幸福小区", "502室"):
        assert any(expected in t for t in tokens), expected


def test_generic_road_words_are_not_tokens():
    assert persona.sensitive_token_hits("她喜欢走路，也爱在马路边看车。", "她喜欢走路。", ()) == []


def test_rule_sections_are_the_private_sentences():
    sections = persona.rule_private_sections(CARD, FAMILY)
    assert set(PRIVATE) <= set(sections)
    assert PUBLIC[1] not in sections
