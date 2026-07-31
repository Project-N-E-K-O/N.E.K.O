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
_MEMORY_LANGS = _LANGS + ("zh-TW",)


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
async def test_qq_group_core_memory_closing_line_is_group_shaped():
    """The QQ group core-memory section must not end by announcing a
    voice conversation with the private-chat counterpart."""
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


def test_qq_instruction_service_asks_for_the_group_shaped_closing_line():
    """The call site itself: QQ always passes input_mode='text' and
    forwards is_group.

    Testing only the selection logic of get_context_summary_ready cannot
    catch a call site that forgets to state the shape — which is precisely
    what this bug was.
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


def test_qq_recall_render_has_no_internal_enum_left():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    bridge = QQMemoryBridge(SimpleNamespace(logger=MagicMock()))
    with patch("utils.language_utils.get_global_language_full", return_value="zh"):
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
    """The required-placeholder list has to match the template.

    kira_unified carries no placeholder at all yet declared three, so the
    guard ruled "override is missing required placeholders" on every turn
    and swapped each non-Chinese user's section back to the Chinese
    default.
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


def _discovered_layer_templates() -> dict[str, str]:
    """Pair every ``i18n_key`` with the default template its call site
    actually hands to ``_resolve_static_layer``.

    Read off the AST instead of copied into a table here. The previous
    hand-kept dict silently skipped any key it did not list, and it was
    already two keys short of ``_PROMPT_LAYERS`` — so the guard was
    passing on layers it had never looked at. Defaults arrive both as
    module constants (``SCENE_DIRECTED_GROUP``) and as inline literals
    (the two naming layers), so both forms are resolved.
    """
    import importlib

    module = importlib.import_module(
        "plugin.plugins.qq_auto_reply.session_instruction_service"
    )
    source = Path(module.__file__).read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute)
                and func.attr == "_resolve_static_layer"):
            continue
        if len(node.args) < 2:
            continue
        key_node, default_node = node.args[0], node.args[1]
        if not (isinstance(key_node, ast.Constant)
                and isinstance(key_node.value, str)):
            continue
        if isinstance(default_node, ast.Constant) and isinstance(
            default_node.value, str
        ):
            found[key_node.value] = default_node.value
        elif isinstance(default_node, ast.Name):
            resolved = getattr(module, default_node.id, None)
            if isinstance(resolved, str):
                found[key_node.value] = resolved
    return found


def test_every_guarded_layer_has_a_discoverable_default_template():
    """Every keyed layer must be reachable by the discovery above.

    This is what the old hand-kept table got wrong: an unlisted key fell
    through a bare ``continue``, so its declared placeholders were never
    compared with anything. Failing here — instead of skipping — is what
    makes the next test's coverage real.
    """
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    discovered = _discovered_layer_templates()
    guarded = [
        layer["i18n_key"]
        for layer in QQSessionInstructionService._PROMPT_LAYERS
        if layer.get("i18n_key") and layer["i18n_key"] != "__runtime__"
    ]
    assert guarded, "夹具失效：一个受护栏管辖的层都没找到"
    missing = [key for key in guarded if key not in discovered]
    assert missing == [], (
        f"这些层受必需占位符护栏管辖，却找不到对应的默认模板，"
        f"它们声明的占位符等于没人校验：{missing}"
    )


def test_declared_required_placeholders_exist_in_their_own_templates():
    """Every declared placeholder must exist in its own default template.

    A declaration the template cannot satisfy is an always-failing
    condition: the guard judges every i18n bundle "missing placeholders"
    and swaps the whole layer back to the Chinese constant for every
    non-Chinese user, once per turn, with a warning each time.
    """
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    discovered = _discovered_layer_templates()
    mismatched = []
    for layer in QQSessionInstructionService._PROMPT_LAYERS:
        key = layer.get("i18n_key")
        if not key or key == "__runtime__":
            continue
        template = discovered.get(key)
        if template is None:
            # Reported by the test above; skipping here keeps one failure
            # per defect instead of two.
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
    """In the collective scene group_facing is always true, so
    build_prompt_message returns the message verbatim and never reaches
    _build_group_turn_message. Dropping that branch changes nothing."""
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
    """Dropping the branch rests on the pipeline forcing group_facing
    for a collective scene.

    Change that derivation and the dead branch comes back to life — as a
    turn message missing its group-facing instruction — so pin it here.
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
    # 这一条走 AST 而不是正则：上一版把参数顺序和"每个参数各占一行"一起
    # 钉死了，压成一行、换顺序、中间插一个参数都会误红，而行为零变化。
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_build_prompt_message"
    ]
    assert len(calls) == 1, (
        f"reply_context_node 里 _build_prompt_message 的调用点数量变了"
        f"（{len(calls)} 处），这条护栏只覆盖单一调用点"
    )
    passed = {
        kw.arg: kw.value for kw in calls[0].keywords if kw.arg is not None
    }
    group_facing_arg = passed.get("group_facing")
    assert (
        isinstance(group_facing_arg, ast.Name)
        and group_facing_arg.id == "effective_group_facing"
    ), "prompt_message 不再用 effective_group_facing 构建"
