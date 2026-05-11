# -*- coding: utf-8 -*-
"""Unit tests for memory.user_directives — user ban-topic 抽取 + 持久化 +
prompt 注入。

四类合同：

1. ``extract_directives`` 对 7 个 locale 的 ban-topic 模板都能抽到 term，
   且并行匹配（中英混说也能命中），非 ban-topic 文本不误触发。
2. ``UserDirectivesManager.record`` 命中 ``(kind, term.casefold())`` 时刷新
   last_seen + expire_at + hit_count，而不是新加一条。
3. TTL：``USER_DIRECTIVE_TTL_SECONDS`` 之后过期不再出现在 ``get_active``；
   ``purge_expired`` 删除过期条目并落盘。
4. ``render_prompt_block`` 把活跃 term 拼成 system prompt 片段，对应 lang
   的 header 必须出现；空列表返回 ""。
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from config import USER_DIRECTIVE_TTL_SECONDS
from config.prompts.prompts_directives import (
    DIRECTIVE_PATTERNS,
    extract_directives,
    render_directives_block,
)


# ── helpers ──────────────────────────────────────────────────────────


def _build_manager(tmp_path):
    """Construct a UserDirectivesManager bound to ``tmp_path`` so json
    round-trips through real disk I/O."""
    from memory.user_directives import UserDirectivesManager

    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    mgr = UserDirectivesManager()
    mgr._config_manager = cm
    return mgr


def _read_file(tmp_path, name) -> dict:
    p = os.path.join(str(tmp_path), name, "user_directives.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ── 1. extraction: 7 locale 全覆盖 + 并行匹配 ──────────────────────


@pytest.mark.parametrize("text,expected_term", [
    # zh: 别再提 / 不要再说 / 不想聊 / 关于
    ("别再提小明了", "小明"),
    ("不要再说工作的事了！", "工作"),
    # "的事" 是 zh pattern 的可选词尾抑止后缀，被剥掉后留下 "昨天发生"——
    # 语义还原度对 LLM 注入足够（含 token-level "昨天" 与 "发生"）。
    ("我不想聊昨天发生的事", "昨天发生"),
    ("关于股票就别再讲了", "股票"),
    # en: stop / don't / I don't want
    ("stop talking about work please.", "work"),
    ("don't mention my ex again", "my ex"),
    ("i don't want to talk about politics anymore", "politics"),
    # ja: のこと/についてはもう
    ("仕事のことはもう言わないで", "仕事"),
    ("もう天気の話は嫌だ", "天気"),
    # ko: 에 대해 / 그만
    ("그 일에 대해서는 그만 얘기해줘", "그 일"),
    # ru: не говори про / о ... больше не говори
    ("не говори про работу.", "работу"),
    ("не упоминай моей бывшей больше", "моей бывшей"),
    # es: no hables de
    ("no hables de fútbol, por favor.", "fútbol"),
    ("no menciones mi ex jamás", "mi ex"),
    # pt: não fale de
    ("não fale de trabalho hoje.", "trabalho"),
    ("não mencione minha ex nunca mais", "minha ex"),
])
def test_extract_directives_per_locale(text, expected_term):
    """7 个 locale 的核心模板都能抽到 term。"""
    hits = extract_directives(text)
    assert hits, f"no hit for {text!r}"
    terms = {term for _loc, _kind, term in hits}
    assert any(expected_term in t for t in terms), (
        f"expected {expected_term!r} in {terms!r} for {text!r}"
    )


def test_extract_directives_mixed_language():
    """中英混说也能命中（patterns 并行跑，不依赖语言检测）。"""
    text = "拜托 stop saying 加班 already"
    hits = extract_directives(text)
    terms = {t for _loc, _kind, t in hits}
    assert "加班" in terms


def test_extract_directives_negative_no_directive():
    """普通陈述、问候、提问不应触发。"""
    for text in [
        "",
        "今天天气真好。",
        "What time is it?",
        "我喜欢吃西瓜。",  # 偏好陈述，不是显式 ban
        "Hello there.",
        "그냥 안녕하세요",
    ]:
        assert extract_directives(text) == [], f"false positive on {text!r}"


def test_extract_directives_term_length_bounds():
    """term <2 或 >40 字符的命中被丢弃（防御性裁剪）。"""
    # 过短：捕获到单字符 "a"，应丢弃
    assert extract_directives("don't say a please") == []
    # 过长：构造一个 50 字符的伪 term
    long_term = "x" * 50
    text = f"stop talking about {long_term}."
    hits = extract_directives(text)
    # 可能完全丢弃，或抓到截断到 40 字以内的子串——任何 term 都必须 <= 40
    for _loc, _kind, term in hits:
        assert len(term) <= 40


def test_extract_directives_dedup_same_term():
    """一句话里同一 (kind, term) 在不同 locale pattern 都命中，结果去重。"""
    # "stop talking about" en pattern + ja pattern 不会同时命中同一英文 term，
    # 但我们可以用一个中英都覆盖的 setup 验证去重逻辑本身
    text = "别再提小明了，don't mention 小明 again"
    hits = extract_directives(text)
    # "小明" 既被 zh 抓到，又被 en 抓到——结果去重后只剩一条
    term_lower_kind = {(k, t.casefold()) for _l, k, t in hits}
    assert ("ban_topic", "小明") in term_lower_kind
    # 同 (kind, term.casefold()) 只出现一次
    flat = [(k, t.casefold()) for _l, k, t in hits]
    assert len(flat) == len(set(flat)), f"dups in {hits!r}"


def test_directive_patterns_have_capture_group():
    """所有 pattern 都必须有一个 capture group 给 term。"""
    for locale, kind, pat in DIRECTIVE_PATTERNS:
        assert pat.groups >= 1, (
            f"{locale}/{kind} pattern {pat.pattern!r} 缺少 capture group"
        )


# ── 2. record dedup + refresh ────────────────────────────────────


def test_record_dedup_refreshes_existing(tmp_path):
    """同 (kind, term.casefold()) 二次命中 → hit_count++ + last_seen 刷新。"""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    first = mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    assert first["hit_count"] == 1
    assert first["last_seen_at"] == 1000.0
    assert first["expire_at"] == 1000.0 + USER_DIRECTIVE_TTL_SECONDS

    second = mgr.record(name, locale="en", kind="ban_topic", term="加班", now=2000.0)
    assert second["hit_count"] == 2
    assert second["last_seen_at"] == 2000.0
    assert second["expire_at"] == 2000.0 + USER_DIRECTIVE_TTL_SECONDS
    # 首次 locale 不被覆盖（保留诊断信号）
    assert second["locale"] == "zh"

    # 磁盘上只有一条
    data = _read_file(tmp_path, name)
    assert len(data["directives"]) == 1


def test_record_case_insensitive_dedup(tmp_path):
    """大小写不影响 dedup 键（"Work" 与 "work" 合并）。"""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="en", kind="ban_topic", term="Work", now=1000.0)
    mgr.record(name, locale="en", kind="ban_topic", term="work", now=2000.0)
    active = mgr.get_active(name, now=2000.0)
    assert len(active) == 1
    assert active[0]["hit_count"] == 2


def test_record_different_terms_keep_separate(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="股票", now=1001.0)
    active = mgr.get_active(name, now=1001.0)
    assert len(active) == 2
    assert {e["term"] for e in active} == {"加班", "股票"}


def test_record_from_text_end_to_end(tmp_path):
    """record_from_text 跑完整流水线：抽取 → 落盘。"""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    written = mgr.record_from_text(name, "别再提小明了，stop saying work please")
    assert written
    terms = {e["term"] for e in written}
    assert "小明" in terms
    assert "work" in terms


# ── 3. TTL ────────────────────────────────────────────────────────


def test_ttl_expires_after_window(tmp_path):
    """3 天后的 get_active 不再包含该条。"""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=0.0)
    # 边界内
    assert mgr.get_active(name, now=USER_DIRECTIVE_TTL_SECONDS - 1)
    # 边界外
    assert mgr.get_active(name, now=USER_DIRECTIVE_TTL_SECONDS + 1) == []


def test_purge_expired_drops_entries(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=0.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="股票", now=1000.0)
    # 推进时间到 加班 过期、股票 还活的瞬间
    # （加班.expire = USER_DIRECTIVE_TTL_SECONDS；股票.expire = 1000 + USER_DIRECTIVE_TTL_SECONDS）
    purge_at = USER_DIRECTIVE_TTL_SECONDS + 500.0
    removed = mgr.purge_expired(name, now=purge_at)
    assert removed == 1
    data = _read_file(tmp_path, name)
    assert {e["term"] for e in data["directives"]} == {"股票"}


def test_get_active_sorted_by_last_seen(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    # term 必须 ≥ 2 字符（record 边界校验，CodeRabbit Minor）。
    mgr.record(name, locale="zh", kind="ban_topic", term="aa", now=100.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="bbb", now=200.0)
    mgr.record(name, locale="zh", kind="ban_topic", term="cccc", now=150.0)
    active = mgr.get_active(name, now=200.0)
    assert [e["term"] for e in active] == ["bbb", "cccc", "aa"]


def test_record_rejects_invalid_term(tmp_path):
    """``record()`` boundary 拒绝空白 / 过长 / 非 str term。"""
    mgr = _build_manager(tmp_path)
    name = "Neko"
    # 空白
    assert mgr.record(name, locale="zh", kind="ban_topic", term="") == {}
    assert mgr.record(name, locale="zh", kind="ban_topic", term="   ") == {}
    # 单字符
    assert mgr.record(name, locale="zh", kind="ban_topic", term="X") == {}
    # 超长（> 40）
    assert mgr.record(name, locale="zh", kind="ban_topic", term="x" * 41) == {}
    # 非 str
    assert mgr.record(name, locale="zh", kind="ban_topic", term=None) == {}  # type: ignore[arg-type]
    # 合法
    assert mgr.record(name, locale="zh", kind="ban_topic", term="OK")
    # 两端 trim 后落库
    e = mgr.record(name, locale="zh", kind="ban_topic", term="  ban-me  ")
    assert e["term"] == "ban-me"


def test_get_active_respects_limit(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    for i in range(30):
        mgr.record(
            name, locale="zh", kind="ban_topic",
            term=f"term-{i:02d}", now=float(i),
        )
    active = mgr.get_active(name, now=100.0, limit=5)
    assert len(active) == 5
    # 取的是 last_seen 最大的 5 个
    assert [e["term"] for e in active] == [
        "term-29", "term-28", "term-27", "term-26", "term-25"
    ]


# ── 4. prompt render ─────────────────────────────────────────────


@pytest.mark.parametrize("lang,header_substring", [
    ("zh", "用户最近"),
    ("en", "user recently"),
    ("ja", "ユーザー"),
    ("ko", "사용자"),
    ("ru", "пользователь"),
    ("es", "usuario"),
    ("pt", "usuário"),
])
def test_render_prompt_block_per_lang(lang, header_substring):
    text = render_directives_block(["加班", "股票"], lang)
    assert "加班" in text and "股票" in text
    assert header_substring.lower() in text.lower()


def test_render_prompt_block_empty_returns_empty_string():
    assert render_directives_block([], "zh") == ""


def test_manager_render_prompt_block_integration(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    # 空时返回 ""
    assert mgr.render_prompt_block(name, "zh") == ""
    # 有 active 时拼成完整 block
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    block = mgr.render_prompt_block(name, "zh", now=1000.0)
    assert "加班" in block
    assert block.startswith("\n")  # leading newline 方便 concat


def test_manager_render_skips_expired(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=0.0)
    block = mgr.render_prompt_block(
        name, "zh", now=USER_DIRECTIVE_TTL_SECONDS + 1
    )
    assert block == ""


# ── 5. 持久化往返 ────────────────────────────────────────────────


def test_load_round_trip_from_disk(tmp_path):
    """先 record → 重建 manager → load 后字段一致。"""
    name = "Neko"
    mgr1 = _build_manager(tmp_path)
    mgr1.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    mgr1.record(name, locale="en", kind="ban_topic", term="work", now=1500.0)

    mgr2 = _build_manager(tmp_path)
    active = mgr2.get_active(name, now=2000.0)
    terms = sorted(e["term"] for e in active)
    assert terms == ["work", "加班"]


def test_corrupt_file_starts_empty(tmp_path):
    """文件被外部破坏 → 重建后从空开始（非致命）。"""
    name = "Neko"
    char_dir = os.path.join(str(tmp_path), name)
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, "user_directives.json"), "w", encoding="utf-8") as f:
        f.write("this is not json {")
    mgr = _build_manager(tmp_path)
    assert mgr.get_active(name, now=1000.0) == []


# ── 6. clear ─────────────────────────────────────────────────────


def test_clear_removes_all(tmp_path):
    mgr = _build_manager(tmp_path)
    name = "Neko"
    mgr.record(name, locale="zh", kind="ban_topic", term="加班", now=1000.0)
    mgr.clear(name)
    assert mgr.get_active(name, now=1000.0) == []
    data = _read_file(tmp_path, name)
    assert data["directives"] == []


# ── 7. sink integration (end-to-end via dispatch_user_utterance) ──


def test_user_utterance_sink_records(tmp_path, monkeypatch):
    """模拟 dispatch_user_utterance → 入库。验证 sink 已被注册并能正确处理事件。"""
    from memory import user_directives as ud_module

    # 把单例的 _config_manager 临时指到 tmp_path
    mgr = ud_module.get_user_directives_manager()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    monkeypatch.setattr(mgr, "_config_manager", cm)
    # 清空 cache，避免被其他测试污染
    monkeypatch.setattr(mgr, "_cache", {})

    name = "Neko"
    ud_module._on_user_utterance(name, {
        "type": "user_message",
        "content": "别再提小明了",
        "lanlan": name,
        "is_voice": False,
    })
    active = mgr.get_active(name)
    assert {e["term"] for e in active} == {"小明"}


def test_user_utterance_sink_skips_default_bucket(tmp_path, monkeypatch):
    """``"default"`` bucket 在同一次 dispatch 里也会派；当 event["lanlan"]
    指向真角色时，"default" 视为重复，skip。否则（lanlan 为空 / 缺失 /
    literal "default"）必须落地，避免整段消息漏抽（codex P1）。"""
    from memory import user_directives as ud_module
    mgr = ud_module.get_user_directives_manager()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    monkeypatch.setattr(mgr, "_config_manager", cm)
    monkeypatch.setattr(mgr, "_cache", {})

    # 真角色场景：event["lanlan"] = "Neko"，bucket="default" 被 skip
    ud_module._on_user_utterance("default", {
        "type": "user_message",
        "content": "别再提小明了",
        "lanlan": "Neko",
    })
    # 不应该有任何目录被建（Neko bucket 由另一次 dispatch 投递，本测试不触发）
    assert not os.listdir(str(tmp_path))


def test_user_utterance_sink_records_default_when_no_character(tmp_path, monkeypatch):
    """character 未配置 / lanlan_name == "default" / lanlan 字段缺失时，
    dispatch 只会派 "default" 一份；sink 必须处理它，否则用户的 ban-topic
    会整段丢失（codex P1）。"""
    from memory import user_directives as ud_module
    mgr = ud_module.get_user_directives_manager()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    monkeypatch.setattr(mgr, "_config_manager", cm)
    monkeypatch.setattr(mgr, "_cache", {})

    # 场景 1：缺失 lanlan 字段
    ud_module._on_user_utterance("default", {
        "type": "user_message",
        "content": "别再提小明了",
    })
    active = mgr.get_active("default")
    assert {e["term"] for e in active} == {"小明"}

    monkeypatch.setattr(mgr, "_cache", {})

    # 场景 2：lanlan == "default" literal
    ud_module._on_user_utterance("default", {
        "type": "user_message",
        "content": "别再提股票了",
        "lanlan": "default",
    })
    active = mgr.get_active("default")
    assert any("股票" in e["term"] for e in active)


def test_user_utterance_sink_handles_multimodal_content(tmp_path, monkeypatch):
    """content 是 list（multimodal）时拼出 text 后跑抽取。"""
    from memory import user_directives as ud_module
    mgr = ud_module.get_user_directives_manager()
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    monkeypatch.setattr(mgr, "_config_manager", cm)
    monkeypatch.setattr(mgr, "_cache", {})

    name = "Neko"
    ud_module._on_user_utterance(name, {
        "content": [
            {"type": "text", "text": "别再提"},
            {"type": "text", "text": "工作"},
            {"type": "image", "url": "..."},
            "了",
        ],
    })
    active = mgr.get_active(name)
    assert any("工作" in e["term"] for e in active)


def test_sink_registered_after_install_runtime_bindings():
    """``app.runtime_bindings.install_runtime_bindings`` 装载后，sink 应已
    挂到 ``main_logic.agent_event_bus._user_utterance_sinks``。

    注意：``memory.user_directives`` 自身**不**做 self-registration——它在
    ``main_logic`` 之下，向上 import 会触发 LAYER_CYCLE。注册由 L6 app 层
    的 ``runtime_bindings`` 接线（register 函数 dedupe-on-identity，重复
    install 安全）。
    """
    from app.runtime_bindings import install_runtime_bindings
    install_runtime_bindings()

    from memory import user_directives as ud_module
    from main_logic.agent_event_bus import _user_utterance_sinks

    assert ud_module._on_user_utterance in _user_utterance_sinks
