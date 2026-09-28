"""Guards for four group-prompt wording defects and one dead branch.

- The stale-memory block of a scoped render (/scoped_context) must not
  name the private-chat counterpart.
- The context-summary closing line follows the session's actual shape
  (voice one-to-one / text one-to-one / group chat), so neither the
  desktop text mode nor a QQ group announces a voice conversation.
- Recalled entries carry a localized ``[tier/entity]`` tag instead of
  pushing internal enums such as ``[fact/group_chat]`` into a Chinese
  prompt; the plugin and the main program share one table.
- The required-placeholder list of ``prompts.group.kira_unified`` has to
  match what the template actually contains, or the guard swaps every
  non-Chinese user's section back to the Chinese constant.
- The group_collective branch of ``_build_group_turn_message`` is
  unreachable; dropping it leaves the collective prompt_message identical.

The cases that drive the qq_auto_reply plugin (its instruction service,
recall renderer, i18n bundles and prompt builder) left this repository with
the plugin; what remains covers the main program's side of these defects.
"""

from __future__ import annotations

import ast
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config.prompts.prompts_sys import (
    CONTEXT_SUMMARY_READY,
    CONTEXT_SUMMARY_READY_GROUP,
    CONTEXT_SUMMARY_READY_TEXT,
    get_context_summary_ready,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
# 'zh-TW' joined the prompts_sys tables with the issue #2500 backfill, so the two
# lists coincide again; _MEMORY_LANGS stays as its own name because the memory
# tables have carried Traditional since long before that.
_LANGS = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
_MEMORY_LANGS = _LANGS


# ── scoped 渲染不得泄漏私聊对象的名字 ────────────────────────────────


class _ScopedRenderHarness:
    """Runs the real RenderingMixin, stubbing only what it depends on."""

    def __init__(self, persona: dict, name_mapping: dict):
        from memory.persona.mentions import MentionsMixin
        from memory.persona.rendering import RenderingMixin

        # 本类在前：ensure_persona / update_suppressions 用桩，其余（含
        # _collect_all_entries）都跑真实实现。
        self.__class__ = type(
            "_Harness",
            (_ScopedRenderHarness, RenderingMixin, MentionsMixin),
            {},
        )
        self._persona = persona
        character_data = (
            name_mapping.get("human", ""), "小天", {}, {},
            name_mapping, {}, {}, {}, {},
        )
        self._config_manager = SimpleNamespace(
            get_character_data=lambda: character_data,
            aget_character_data=AsyncMock(return_value=character_data),
        )

    def ensure_persona(self, name):
        return self._persona

    async def aensure_persona(self, name):
        return self._persona

    def update_suppressions(self, name):
        return None

    async def aupdate_suppressions(self, name):
        return None


def _stale_reflection(text: str, **subject_fields) -> dict:
    """A confirmed reflection past its TTL — the block only renders
    when at least one exists."""
    old_iso = (datetime.now() - timedelta(days=120)).isoformat()
    return {
        "id": f"r-{text[:8]}",
        "text": text,
        "entity": "master",
        "status": "confirmed",
        "temporal_scope": "state",
        "event_end_at": old_iso,
        "created_at": old_iso,
        **subject_fields,
    }


@pytest.mark.asyncio
async def test_scoped_past_memory_block_never_names_the_private_counterpart():
    """Naming the private-chat counterpart in a group bootstrap is wrong
    twice over: the name leaks into the group prompt, and the instruction
    addresses someone who is not in the group at all.

    Conditional — the section only renders when a confirmed reflection has
    outlived its TTL.
    """
    from memory.scopes import MemorySubject

    subject = MemorySubject.group_chat("qq", "7788")
    harness = _ScopedRenderHarness({}, {"human": "老张"})
    scoped = _stale_reflection("群里聊过露营", **subject.as_entry_fields())

    # 两种 locale 都要过：这一段整段按 active language 渲染。
    for lang, scoped_phrase, legacy_phrase in (
        ("zh", "除非有人先主动提起", "除非 老张 先主动提起"),
        ("en", "Unless someone brings them up first", "Unless 老张 brings them up first"),
    ):
        with patch(
            "utils.language_utils.get_global_language_full", return_value=lang,
        ):
            rendered_sync = harness.render_persona_markdown(
                "小天", None, [scoped],
                subjects=[subject], include_legacy_private=False,
            )
            rendered_async = await harness.arender_persona_markdown(
                "小天", None, [scoped],
                subjects=[subject], include_legacy_private=False,
            )
            # legacy 私聊渲染（无 subjects）照旧点名对话对象——群变体不是
            # 把这条指令删掉了，只是换成不指认任何人的说法。
            legacy = _ScopedRenderHarness(
                {}, {"human": "老张"},
            ).render_persona_markdown(
                "小天", None, [_stale_reflection("一起看过电影")],
            )

        for rendered in (rendered_sync, rendered_async):
            assert "群里聊过露营" in rendered
            assert "老张" not in rendered
            assert scoped_phrase in rendered
        assert "一起看过电影" in legacy
        assert legacy_phrase in legacy


def test_scoped_past_block_signal_matches_the_scope_filter():
    """"This render may only show scoped content" has to use the same
    derivation as the filters, or the rendered prose disagrees with what
    was actually let through."""
    from memory.persona.rendering import RenderingMixin
    from memory.scopes import MemorySubject

    group = MemorySubject.group_chat("qq", "7788")
    assert RenderingMixin._renders_scoped_only(None, None) is False
    assert RenderingMixin._renders_scoped_only([], None) is False
    assert RenderingMixin._renders_scoped_only([group], None) is True
    assert RenderingMixin._renders_scoped_only([group], False) is True
    # 显式带上 legacy 私聊内容时，点名对话对象仍然是对的。
    assert RenderingMixin._renders_scoped_only([group], True) is False


def test_scoped_past_memory_block_is_localized_everywhere():
    from config.prompts.prompts_memory import (
        PAST_MEMORY_BLOCK,
        PAST_MEMORY_BLOCK_SCOPED,
        render_past_memory_block,
    )

    assert (
        set(PAST_MEMORY_BLOCK_SCOPED)
        == set(PAST_MEMORY_BLOCK)
        == set(_MEMORY_LANGS)
    )
    for lang in _MEMORY_LANGS:
        assert "{MASTER_NAME}" not in PAST_MEMORY_BLOCK_SCOPED[lang]
        assert "{AI_NAME}" in PAST_MEMORY_BLOCK_SCOPED[lang]
        assert "{ITEMS}" in PAST_MEMORY_BLOCK_SCOPED[lang]
        rendered = render_past_memory_block(
            lang=lang, ai_name="小天", master_name="老张",
            items_text="- 条目", scoped_only=True,
        )
        assert "老张" not in rendered
        assert "小天" in rendered and "- 条目" in rendered


# ── 前情概要收尾句：语音 / 文字 / 群聊 ──────────────────────────────


def test_context_summary_ready_variants_match_the_session_shape():
    for lang in _LANGS:
        assert get_context_summary_ready(lang) == CONTEXT_SUMMARY_READY[lang]
        assert (
            get_context_summary_ready(lang, input_mode="audio")
            == CONTEXT_SUMMARY_READY[lang]
        )
        assert (
            get_context_summary_ready(lang, input_mode="text")
            == CONTEXT_SUMMARY_READY_TEXT[lang]
        )
        # 群聊压过模态：群里就没有那个固定的一对一对象。
        assert (
            get_context_summary_ready(lang, input_mode="text", is_group=True)
            == CONTEXT_SUMMARY_READY_GROUP[lang]
        )
        assert (
            get_context_summary_ready(lang, input_mode="audio", is_group=True)
            == CONTEXT_SUMMARY_READY_GROUP[lang]
        )


def test_context_summary_ready_group_variant_has_no_counterpart_slot():
    assert (
        set(CONTEXT_SUMMARY_READY_GROUP)
        == set(CONTEXT_SUMMARY_READY_TEXT)
        == set(CONTEXT_SUMMARY_READY)
        == set(_LANGS)
    )
    for lang in _LANGS:
        assert "{master}" not in CONTEXT_SUMMARY_READY_GROUP[lang]
        assert "{name}" in CONTEXT_SUMMARY_READY_GROUP[lang]
        assert "{master}" in CONTEXT_SUMMARY_READY_TEXT[lang]
        # 文字变体不能还说"语音"。简繁两种写法都要挡：只查简体的话，繁中那行
        # 写成「語音」会从这条断言底下溜过去。
        for spelling in ("语音", "語音"):
            assert spelling not in CONTEXT_SUMMARY_READY_TEXT[lang]
            assert spelling not in CONTEXT_SUMMARY_READY_GROUP[lang]
        assert "voice" not in CONTEXT_SUMMARY_READY_TEXT[lang].lower()
        assert "voice" not in CONTEXT_SUMMARY_READY_GROUP[lang].lower()


_SKIPPED_DIRS = {
    ".claude", ".git", ".venv", "__pycache__", "build", "deps", "dist",
    "frontend", "node_modules", "tests", "venv",
}


def _python_sources_outside_prompts_and_tests():
    for directory, subdirs, files in os.walk(_REPO_ROOT):
        subdirs[:] = [name for name in subdirs if name not in _SKIPPED_DIRS]
        current = Path(directory)
        if current.relative_to(_REPO_ROOT).parts[:2] == ("config", "prompts"):
            continue
        for name in files:
            if name.endswith(".py"):
                yield current / name


def test_no_module_picks_the_voice_template_directly():
    """Choosing the closing line has to go through
    get_context_summary_ready.

    Discovered rather than listed: any new call site (a new plugin, a new
    session shape) that grabs CONTEXT_SUMMARY_READY directly shows up
    here — which is exactly how the desktop text mode kept announcing a
    voice conversation.
    """
    offenders = []
    call_sites = []
    for path in _python_sources_outside_prompts_and_tests():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "CONTEXT_SUMMARY_READY" not in source and (
            "get_context_summary_ready" not in source
        ):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        relative = str(path.relative_to(_REPO_ROOT))
        # 走 AST 而不是子串：注释里提一句模板名不算调用点。
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute, ast.alias)):
                referenced = (
                    getattr(node, "id", None)
                    or getattr(node, "attr", None)
                    or getattr(node, "name", None)
                )
                if referenced == "CONTEXT_SUMMARY_READY":
                    offenders.append(f"{relative}:{getattr(node, 'lineno', '?')}")
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "get_context_summary_ready":
                continue
            call_sites.append((
                relative,
                node.lineno,
                {kw.arg for kw in node.keywords},
            ))

    assert offenders == [], (
        f"这些模块直接引用了只适用于语音一对一的 CONTEXT_SUMMARY_READY："
        f"{offenders}；请改用 get_context_summary_ready(...)"
    )
    assert call_sites, "没有找到任何 get_context_summary_ready 调用点"
    missing = [
        site for site in call_sites
        if not ({"input_mode", "is_group"} & site[2])
    ]
    assert missing == [], (
        f"这些调用点没有交代本次会话的形态（input_mode / is_group）：{missing}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input_mode, banned, expected",
    [("text", "语音", "用文字"), ("audio", "用文字", "语音")],
)
async def test_hot_swap_prime_closing_line_follows_the_session_mode(
    input_mode, banned, expected,
):
    """The hot-swap prime is the second lifecycle call site.

    It reads ``self.input_mode`` rather than a parameter, so the AST guard
    that every call states its shape cannot tell whether the value is even
    reachable — drive the real sequence and read the primed text.
    """
    from tests.unit.test_hot_swap_cancellation import (
        _FakeSession,
        _drain_task,
        _make_swap_manager,
    )

    manager = _make_swap_manager()
    manager.input_mode = input_mode
    old_session = _FakeSession("old")
    new_session = _FakeSession("pending")
    manager.session = old_session
    manager.pending_session = new_session
    manager.is_hot_swap_imminent = True
    manager.is_active = True
    manager.message_handler_task = None

    try:
        await manager._perform_final_swap_sequence()
        assert manager.session is new_session
        primed_text, skipped = new_session.prime_calls[0]
        assert skipped is True
        assert expected in primed_text
        assert banned not in primed_text
    finally:
        await _drain_task(manager.message_handler_task)


# ── 召回条目的 [层级/归属] 标签 ────────────────────────────────────


def test_recall_entry_tag_is_localized_and_covers_the_scoped_kinds():
    from config.prompts.prompts_memory import (
        RECALL_ENTRY_ENTITY_LABEL,
        RECALL_ENTRY_TIER_LABEL,
        render_recall_entry_tag,
    )

    for table in (RECALL_ENTRY_TIER_LABEL, RECALL_ENTRY_ENTITY_LABEL):
        for key, entry in table.items():
            assert set(entry) == set(_MEMORY_LANGS), (
                f"{key} 缺语言：{set(_MEMORY_LANGS) - set(entry)}"
            )

    # scoped 写入把 entity 强制成 subject.kind，这几个必须在表里。
    for kind in ("group_chat", "participant", "group_participant"):
        assert kind in RECALL_ENTRY_ENTITY_LABEL

    assert render_recall_entry_tag("fact", "group_chat", "zh") == "[事实/群聊]"
    assert render_recall_entry_tag("fact", "group_chat", "en") == "[fact/group chat]"
    assert render_recall_entry_tag("reflection", "master", "zh") == "[印象/关于用户]"
    # 未知枚举原样透出，别静默变成空串。
    assert render_recall_entry_tag("brand_new_tier", "", "zh") == "[brand_new_tier/-]"


@pytest.mark.asyncio
async def test_recall_memory_tool_render_matches_the_plugin_twin():
    """The main program's recall_memory tool result shares one label
    table with the plugin."""
    from main_logic.core.tool_calling import ToolCallingMixin

    class _Harness(ToolCallingMixin):
        def __init__(self):
            self.user_language = "zh"
            self.lanlan_name = "小天"
            self.input_mode = "text"
            self.session = None
            self.memory_server_port = 12345

    payload = {
        "results": [
            {
                "text": "群里在聊露营",
                "tier": "fact",
                "entity": "group_chat",
                "created_at": "2026-05-01T10:00:00",
            },
        ],
        "elapsed_ms": 3.0,
    }
    response = SimpleNamespace(
        is_success=True, status_code=200, text="",
        json=lambda: payload,
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    with patch(
        "utils.internal_http_client.get_internal_http_client",
        return_value=client,
    ):
        rendered = await _Harness()._handle_recall_memory_call({"query": "露营"})

    assert "[事实/群聊]" in rendered
    assert "[fact/group_chat]" not in rendered
    assert "群里在聊露营" in rendered
