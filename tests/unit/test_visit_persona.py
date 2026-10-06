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
    assert _file(_tmp)["scan_complete"] is False          # 截掉的尾巴没被扫描过


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


@pytest.mark.parametrize("card,text,hit", [
    ("家里的网站 private-family.example 只给亲戚看。", "她提过 private-family.example 这个网站。", "private-family.example"),
    ("Our page is https://private-family.example/path.", "See https://private-family.example/path for more.",
     "https://private-family.example/path"),
    ("Our page is https://private-family.example/path.", "Her family runs private-family.example.",
     "private-family.example"),
])
def test_urls_match_without_sentence_punctuation_and_by_host(card, text, hit):
    assert hit in persona.sensitive_token_hits(card, text, ())


def test_lowercase_street_names_after_a_house_number_are_caught():
    card = "Her address: 12 main street, second floor."
    assert persona.sensitive_token_hits(card, "She often walks down main street.", ()) == ["main street"]
    pets = "She lives in a flat with 2 cats and a dog."
    assert persona.sensitive_token_hits(pets, "She loves cats and dogs.", ()) == []
    playing = "She lives in a flat with 2 cats playing outside."
    assert persona.sensitive_token_hits(playing, "Her cats playing outside is a sight.", ()) == []
    grove = "address: 12 maple grove, near the river"           # 没有街道类词尾，靠开头的门牌号
    assert persona.sensitive_token_hits(grove, "She loves walking in maple grove.", ()) == ["maple grove"]
    mid = "address: the house is at 42 main street"
    assert persona.sensitive_token_hits(mid, "She grew up near main street.", ()) == ["main street"]


def test_capitalised_dotted_words_are_not_host_names():
    assert persona.sensitive_token_hits("She calls him Mr.Smith at home.", "Mr.Smith is her teacher.", ()) == []


def test_multi_word_addresses_are_caught():
    card = "She lives with her family. Her address: 12 Main Street."
    assert persona.sensitive_token_hits(card, "She often walks down Main Street.", ()) == ["Main Street"]
    assert persona.sensitive_token_hits(card, "She lives on a quiet street.", ()) == []


def test_phone_numbers_match_whatever_the_separators():
    card = "联系电话 138 0013 8000，随时找她。"
    assert persona.sensitive_token_hits(card, "她的号码是138-0013-8000。", ()) == ["13800138000"]
    assert persona.sensitive_token_hits(card, "她出生在 2001 年。", ()) == []


@pytest.mark.parametrize("card,text", [
    ("联系电话 138 0013 8000 2024年登记", "她的号码是138-0013-8000。"),     # 相邻年份被并进匹配
    ("phone: +1 (555) 010-0199", "Call her at 555-010-0199."),          # 一边省略国家码
    ("电话 5550100199", "号码 +1 555 010 0199"),
])
def test_phone_numbers_match_across_country_codes_and_adjacent_digits(card, text):
    assert persona.sensitive_token_hits(card, text, ())


def test_capped_private_section_list_counts_as_an_incomplete_scan(env, monkeypatch):
    client, _tmp, state, _llm, scan = env
    monkeypatch.setattr(persona, "_PRIVATE_SECTIONS_MAX", 2)
    view = _generate(client)
    assert len(view["private_sections"]) == 2 and view["scan_complete"] is False


def test_generic_road_words_are_not_tokens():
    assert persona.sensitive_token_hits("她喜欢走路，也爱在马路边看车。", "她喜欢走路。", ()) == []


def test_rule_sections_are_the_private_sentences():
    sections = persona.rule_private_sections(CARD, FAMILY)
    assert set(PRIVATE) <= set(sections)
    assert PUBLIC[1] not in sections



def test_put_copying_a_private_passage_is_refused(env):
    client, tmp_path, *_ = env
    _generate(client)
    resp = client.put("/api/visit/persona?catgirl=A", headers=GOOD,
                      json={"text": "你是{LANLAN_NAME}，每周三都要加班到很晚，你会在门口等他回家。", "reviewed": True})
    assert resp.status_code == 400 and resp.json()["code"] == "persona_sensitive_overlap"


def test_a_regeneration_never_overwrites_an_edit_made_meanwhile(env):
    client, tmp_path, *_ = env
    _generate(client)
    gate = client.portal.call(_make_event)

    async def slow(prompt):
        await gate.wait()
        return GOOD_PERSONA

    persona.configure_persona(llm=slow)
    assert client.post("/api/visit/persona/regenerate?catgirl=A", headers=GOOD, json={}).status_code == 202
    edited = {**_file(tmp_path), "text": "你是{LANLAN_NAME}，手写的人设。", "edited": True, "reviewed": True}
    client.portal.call(persona.store().save, UID_A, edited)     # 另一个窗口抢在生成结束前确认了手写
    persona._note_write(UID_A)
    client.portal.call(gate.set)
    _settle(client)
    assert _file(tmp_path)["text"] == "你是{LANLAN_NAME}，手写的人设。"


async def _make_event():
    return asyncio.Event()


def test_a_regeneration_started_while_an_edit_holds_the_lock_does_not_overwrite_it(env, monkeypatch):
    client, tmp_path, *_ = env
    _generate(client)
    gate = client.portal.call(_make_event)

    async def slow(prompt):
        await gate.wait()
        return GOOD_PERSONA

    persona.configure_persona(llm=slow)
    real_save = persona.VisitPersonaStore.save
    clicked = []

    async def save_with_a_click(self, uid, doc):
        if not clicked:                                    # PUT 已拿着锁、正在写盘
            clicked.append(persona.start_regeneration("A", uid))   # 另一个窗口此刻点了重新生成
        await real_save(self, uid, doc)

    monkeypatch.setattr(persona.VisitPersonaStore, "save", save_with_a_click)
    hand = "你是{LANLAN_NAME}，抢先手写的人设。"
    assert client.put("/api/visit/persona?catgirl=A", headers=GOOD,
                      json={"text": hand, "reviewed": True}).status_code == 200
    assert clicked and persona.is_generating(UID_A)
    client.portal.call(gate.set)
    _settle(client)
    assert _file(tmp_path)["text"] == hand



def test_gate_refuses_when_regeneration_starts_while_it_reads(env, monkeypatch):
    client, *_ = env
    _generate(client)
    client.put("/api/visit/persona?catgirl=A", headers=GOOD, json={"reviewed": True})
    real = persona._hooks.load_context

    async def read_then_click():
        ctx = await real()
        persona.start_regeneration("A", UID_A)          # 另一个窗口此刻点了重新生成
        return ctx

    monkeypatch.setattr(persona._hooks, "load_context", read_then_click)
    gate = _gate(client)
    assert gate.ok is False and gate.state == "generating"
    _settle(client)


def test_gate_rereads_a_persona_written_while_it_reads(env, monkeypatch):
    client, tmp_path, *_ = env
    _generate(client)
    client.put("/api/visit/persona?catgirl=A", headers=GOOD, json={"reviewed": True})
    real = persona._hooks.load_context
    written = []

    async def read_then_write():
        ctx = await real()
        if not written:                                  # 重生成刚落了一份未审核的
            written.append(True)
            await persona.store().save(UID_A, {**_file(tmp_path), "reviewed": False})
            persona._note_write(UID_A)
        return ctx

    monkeypatch.setattr(persona._hooks, "load_context", read_then_write)
    gate = _gate(client)
    assert gate.ok is False and gate.state == "unreviewed"



def test_the_token_cap_holds_after_redaction():
    from utils.tokenize import count_tokens

    raw = "Al " * (visit_settings.VISIT_PERSONA_MAX_TOKENS * 2)           # 短名字反复出现，替换成更长的中性称呼
    text = persona._clean_persona_text(raw, ["Al"], "en")
    assert "Al " not in text and count_tokens(text) <= visit_settings.VISIT_PERSONA_MAX_TOKENS
