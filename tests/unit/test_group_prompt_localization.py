"""群聊 prompt 的四处措辞缺陷护栏 + 一处死分支删除。

覆盖：
- scoped 渲染（/scoped_context）的"较久前的记忆"段不得点名私聊对象。
- 前情概要收尾句按会话形态选（语音一对一 / 文字一对一 / 群聊），桌面
  文本模式和 QQ 群都不再念"即将开始用语音对话"。
- 召回条目的 ``[层级/归属]`` 标签本地化，不再把 ``[fact/group_chat]``
  这种内部枚举塞进中文 prompt（插件侧与本体侧同一张表）。
- ``prompts.group.kira_unified`` 的必需占位符清单要以模板实际内容为准，
  否则每个非中文用户的这一段都被护栏换回中文常量。
- ``_build_group_turn_message`` 的 group_collective 分支不可达，删除后
  collective 场景的 prompt_message 与改前逐字相同。
"""

from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.prompts.prompts_sys import (
    CONTEXT_SUMMARY_READY,
    CONTEXT_SUMMARY_READY_GROUP,
    CONTEXT_SUMMARY_READY_TEXT,
    get_context_summary_ready,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LANGS = ("zh", "en", "ja", "ko", "ru", "es", "pt")


# ── scoped 渲染不得泄漏私聊对象的名字 ────────────────────────────────


class _ScopedRenderHarness:
    """跑真正的 RenderingMixin，只补它依赖的几个外部件。"""

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
    """一条超 TTL 的 confirmed 反思 —— past block 只在有它时才渲染。"""
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
    """群 bootstrap 里出现"除非{私聊对象}先主动提起"是双重错误：名字泄漏
    进群 prompt，指令对象也根本不是群里的人。

    条件触发 —— 只有存在超 TTL 的 confirmed 反思时才渲染这一段。
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
            "utils.language_utils.get_global_language", return_value=lang,
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
    """"这次渲染只允许出现 scoped 内容"必须和过滤器同一个判据，否则渲染
    出来的散文会和真正放行的内容对不上。"""
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

    assert set(PAST_MEMORY_BLOCK_SCOPED) == set(PAST_MEMORY_BLOCK) == set(_LANGS)
    for lang in _LANGS:
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
        # 文字变体不能还说"语音"。
        assert "语音" not in CONTEXT_SUMMARY_READY_TEXT[lang]
        assert "语音" not in CONTEXT_SUMMARY_READY_GROUP[lang]
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
    """收尾句的形态判断必须走 get_context_summary_ready。

    自动发现而不是维护清单：任何新调用点（新插件、新会话形态）只要直接
    抓 CONTEXT_SUMMARY_READY，就会在这里现形——桌面文本模式当年正是这样
    一路念着"即将用语音对话"的。
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
async def test_qq_group_core_memory_closing_line_is_group_shaped():
    """QQ 群的 core memory 段结尾不能出现"用语音与{私聊对象}对话"。"""
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    bridge = MagicMock()
    bridge.group_subject.return_value = {
        "subject_kind": "group_chat", "subject_id": "qq:7788",
    }
    bridge.fetch_scoped_bootstrap_memory = AsyncMock(return_value="群聊长期记忆")
    bridge.fetch_bootstrap_memory = AsyncMock(return_value="私人长期记忆")
    plugin = SimpleNamespace(
        memory_bridge=bridge,
        logger=MagicMock(),
        i18n=SimpleNamespace(t=lambda key, default="", **kw: default),
        _qq_settings={"group_memory_enabled": True},
    )
    service = QQSessionInstructionService(plugin)

    group_line = get_context_summary_ready("zh", input_mode="text", is_group=True)
    rendered = await service._build_core_memory_section(
        should_use_memory_context=True,
        her_name="小天",
        master_name="老张",
        context_ready_template=group_line,
        is_group=True,
        group_id="7788",
        sender_id="2046",
    )
    assert "群聊长期记忆" in rendered
    assert "老张" not in rendered
    assert "语音" not in rendered
    assert "群聊里用文字继续对话" in rendered


def test_qq_instruction_service_asks_for_the_group_shaped_closing_line():
    """调用点本身：QQ 永远传 input_mode='text'，并把 is_group 传下去。

    只测 get_context_summary_ready 的选择逻辑挡不住"调用点忘了传形态"，
    而那正是这个 bug 的原样。
    """
    source = (
        _REPO_ROOT / "plugin" / "plugins" / "qq_auto_reply"
        / "session_instruction_service.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == "get_context_summary_ready")
    ]
    assert len(calls) == 1
    keywords = {kw.arg: kw.value for kw in calls[0].keywords}
    assert isinstance(keywords["input_mode"], ast.Constant)
    assert keywords["input_mode"].value == "text"
    assert isinstance(keywords["is_group"], ast.Name)
    assert keywords["is_group"].id == "is_group"


# ── 召回条目的 [层级/归属] 标签 ────────────────────────────────────


def test_recall_entry_tag_is_localized_and_covers_the_scoped_kinds():
    from config.prompts.prompts_memory import (
        RECALL_ENTRY_ENTITY_LABEL,
        RECALL_ENTRY_TIER_LABEL,
        render_recall_entry_tag,
    )

    for table in (RECALL_ENTRY_TIER_LABEL, RECALL_ENTRY_ENTITY_LABEL):
        for key, entry in table.items():
            assert set(entry) == set(_LANGS), f"{key} 缺语言：{set(_LANGS) - set(entry)}"

    # scoped 写入把 entity 强制成 subject.kind，这几个必须在表里。
    for kind in ("group_chat", "participant", "group_participant"):
        assert kind in RECALL_ENTRY_ENTITY_LABEL

    assert render_recall_entry_tag("fact", "group_chat", "zh") == "[事实/群聊]"
    assert render_recall_entry_tag("fact", "group_chat", "en") == "[fact/group chat]"
    assert render_recall_entry_tag("reflection", "master", "zh") == "[印象/关于用户]"
    # 未知枚举原样透出，别静默变成空串。
    assert render_recall_entry_tag("brand_new_tier", "", "zh") == "[brand_new_tier/-]"


def test_qq_recall_render_has_no_internal_enum_left():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    bridge = QQMemoryBridge(SimpleNamespace(logger=MagicMock()))
    with patch("utils.language_utils.get_global_language", return_value="zh"):
        rendered = bridge.render_relevant_memory([
            {
                "text": "群里在聊露营",
                "tier": "fact",
                "entity": "group_chat",
                "created_at": "2026-05-01T10:00:00",
            },
            {
                "text": "阿离喜欢辣条",
                "tier": "reflection",
                "entity": "group_participant",
            },
        ])

    assert "[事实/群聊]" in rendered
    assert "[印象/群成员]" in rendered
    assert "fact" not in rendered and "group_chat" not in rendered
    assert "reflection" not in rendered and "group_participant" not in rendered
    assert "(2026-05-01)" in rendered


@pytest.mark.asyncio
async def test_recall_memory_tool_render_matches_the_plugin_twin():
    """本体侧 recall_memory 工具结果与插件侧共用同一张标签表。"""
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


# ── kira_unified 的必需占位符 ──────────────────────────────────────


def _bundle_text(locale: str, key: str) -> str:
    bundle = json.loads(
        (
            _REPO_ROOT / "plugin" / "plugins" / "qq_auto_reply" / "i18n"
            / f"{locale}.json"
        ).read_text(encoding="utf-8")
    )
    return bundle[key]


def test_english_user_actually_gets_the_english_group_reply_guidelines():
    """必需占位符清单必须以模板实际内容为准。

    kira_unified 一个占位符都没有，却被声明成需要三个 —— 护栏于是每轮都
    判"覆盖缺必需占位符"，把每个非中文用户的这一段换回中文默认常量。
    """
    from plugin.plugins.qq_auto_reply.scene_prompt_templates import (
        SCENE_KIRA_UNIFIED_GROUP,
    )
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    english = _bundle_text("en", "prompts.group.kira_unified")
    plugin = SimpleNamespace(
        i18n=SimpleNamespace(t=lambda key, default="", **kw: english),
        _qq_settings={},
        _strategy_mode="neko_dynamic",
        qq_client=None,
        logger=MagicMock(),
    )
    service = QQSessionInstructionService(plugin)

    rendered = service._build_group_scene_section(
        her_name="Neko", master_title="Master", permission_level="normal",
        sender_id="2046", user_title="Ali", group_id="7788",
        address_user_by_name=False, group_facing=False,
        shared_group_session=True, group_scene_mode="shared_context",
    )

    assert "Group Chat Reply Guidelines" in rendered
    assert "In group chats, you don't need to reply to every message" in rendered
    assert "群聊回复意愿" not in rendered
    assert SCENE_KIRA_UNIFIED_GROUP not in rendered
    # 而且不再每轮打一条"缺必需占位符"的 warning。
    assert not [
        call for call in plugin.logger.warning.call_args_list
        if "必需占位符" in str(call)
    ]


def test_declared_required_placeholders_exist_in_their_own_templates():
    """自动发现：每一层声明的必需占位符都得真的出现在默认模板里。

    列清单会漏；这里直接把 _PROMPT_LAYERS 和它对应的默认模板对起来。
    """
    from plugin.plugins.qq_auto_reply import prompt_fragment_templates as frag
    from plugin.plugins.qq_auto_reply import scene_prompt_templates as scenes
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    defaults = {
        "prompts.group.kira_unified": scenes.SCENE_KIRA_UNIFIED_GROUP,
        "prompts.group.collective": scenes.SCENE_COLLECTIVE_GROUP,
        "prompts.group.shared_session": scenes.SCENE_SHARED_GROUP,
        "prompts.group.directed": scenes.SCENE_DIRECTED_GROUP,
        "prompts.private.body": scenes.SCENE_PRIVATE_CHAT,
        "role_prompt_section": frag.ROLE_PROMPT_SECTION,
        "attention_prompt_section": frag.ATTENTION_PROMPT_SECTION,
        "format_prompt_section": frag.FORMAT_PROMPT_SECTION,
        "format_prompt_section_neko_dynamic": frag.FORMAT_PROMPT_SECTION_NEKO_DYNAMIC,
        "format_prompt_section_open_platform": frag.FORMAT_PROMPT_SECTION_OPEN_PLATFORM,
        "character_prompt_section": frag.CHARACTER_PROMPT_SECTION,
        "time_prompt_section": frag.TIME_PROMPT_SECTION,
        "detail_constraints_section": frag.DETAIL_CONSTRAINTS_SECTION,
        "output_prompt_section": frag.OUTPUT_PROMPT_SECTION,
        "core_memory_section": frag.CORE_MEMORY_SECTION,
    }
    mismatched = []
    for layer in QQSessionInstructionService._PROMPT_LAYERS:
        key = layer.get("i18n_key")
        template = defaults.get(key)
        if template is None:
            continue
        for placeholder in layer.get("required_placeholders") or ():
            if placeholder not in template:
                mismatched.append((key, placeholder))
    assert mismatched == [], (
        f"这些层声明了默认模板里根本没有的必需占位符，护栏会把每份 i18n "
        f"bundle 都判成缺占位符并回退中文：{mismatched}"
    )


# ── group_collective 死分支 ────────────────────────────────────────


def test_collective_prompt_message_is_unchanged_after_dropping_dead_branch():
    """collective 场景下 group_facing 恒为真 → build_prompt_message 原样
    返回消息，从来走不到 _build_group_turn_message。删掉那条分支是零行为
    变更。"""
    from plugin.plugins.qq_auto_reply.prompt_builder import QQPromptBuilder

    builder = QQPromptBuilder(SimpleNamespace())
    message = "群里在聊露营，你怎么看"
    assert builder.build_prompt_message(
        is_group=True,
        group_facing=True,
        group_scene_mode="group_collective",
        user_title="阿离",
        sender_id="2046",
        group_id="7788",
        message=message,
        current_message_id="m-1",
    ) == message


def test_pipeline_still_forces_group_facing_for_collective_scene():
    """删除那条分支的前提：pipeline 保证 collective ⇒ group_facing。

    这个推导一旦被改掉，死分支就复活了（而且是以"少了群体面向指令"的
    形式），所以把它钉在这里。
    """
    source = (
        _REPO_ROOT / "plugin" / "plugins" / "qq_auto_reply"
        / "reply_context_node.py"
    ).read_text(encoding="utf-8")
    assert re.search(
        r"effective_group_facing\s*=\s*group_facing\s+or\s+"
        r"effective_group_scene_mode\s*==\s*[\"']group_collective[\"']",
        source,
    ), (
        "reply_context_node 不再保证 group_collective ⇒ group_facing："
        "prompting._build_group_turn_message 的 collective 分支被删掉了，"
        "这条推导是它可以被删的唯一理由"
    )
    assert re.search(
        r"_build_prompt_message\(\s*\n\s*is_group=is_group,"
        r"\s*\n\s*group_facing=effective_group_facing,",
        source,
    ), "prompt_message 不再用 effective_group_facing 构建"
