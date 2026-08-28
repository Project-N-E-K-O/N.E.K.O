"""Traditional Chinese must survive the trip from the global locale to the
prompt table (issue #2500, step 2 — scattered call sites + plugins).

Every test here sets the process locale to Traditional Chinese with the real
``language_context`` and then asserts on the *rendered* prompt, not on a
normalizer in isolation. Each one pins three outcomes apart:

* the Traditional template is the one that came out;
* it is NOT the Simplified template (the old bug — a short-code collapse);
* it is NOT the English template (the failure mode a naive "just pass the
  full code" fix introduces, because the full code for Simplified is
  ``zh-CN`` while these tables key Simplified as ``zh``).

The third assertion is the reason each test also runs the Simplified case:
a fix that drops Simplified users to English would pass a Traditional-only
test.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config.prompts.prompts_sys import SESSION_INIT_PROMPT
from utils.language_utils import language_context


# ---------------------------------------------------------------------------
# main_logic/activity/tracker.py — activity_guess narration locale
# ---------------------------------------------------------------------------


def _drive_activity_guess_once(monkeypatch):
    """Run exactly one iteration of ``_activity_guess_loop`` and return the
    ``lang`` values it handed to its two consumers.

    The loop body is the call site under test, so it is driven for real
    rather than re-derived here; everything it touches on the way there is
    stubbed on the instance.
    """
    from main_logic.activity import tracker as tracker_mod

    seen: dict[str, str] = {}

    monkeypatch.setattr(tracker_mod, "_ACTIVITY_GUESS_TICK_SECONDS", 0)
    monkeypatch.setattr(tracker_mod, "_privacy_mode_active", lambda: False)
    monkeypatch.setattr(tracker_mod, "_proactive_chat_enabled", lambda: True)
    monkeypatch.setattr(
        tracker_mod, "observation_from_system", lambda *a, **k: object(),
    )

    async def _fake_call_activity_guess(**kwargs):
        seen["activity"] = kwargs["lang"]
        # The loop returns on CancelledError, so this both records the value
        # and ends the iteration deterministically.
        raise asyncio.CancelledError

    from main_logic.activity import llm_enrichment
    monkeypatch.setattr(
        llm_enrichment, "call_activity_guess", _fake_call_activity_guess,
    )

    inst = object.__new__(tracker_mod.UserActivityTracker)
    inst.lanlan_name = "Neko"
    inst._conv_seq = 0
    inst._user_msg_buffer = []
    inst._ai_msg_buffer = []
    rule_snap = SimpleNamespace(state="focused_work")
    inst._sm = SimpleNamespace(
        _prefs=None,
        update_system=lambda *a, **k: None,
        update_window=lambda *a, **k: None,
        get_snapshot=lambda **k: rule_snap,
    )
    inst._select_system_snapshot = lambda ts: object()
    inst._tick_break_reminders = lambda snap, **k: None

    async def _drain():
        return None

    inst._drain_context_prompt = _drain
    inst._process_topic_candidates_if_ready = (
        lambda *, lang, now: seen.__setitem__("topic", lang)
    )
    inst._is_narration_suppressed = lambda: False
    inst._coarse_activity_sig = lambda snap: ("focused_work", "x")
    inst._activity_guess_gate = SimpleNamespace(
        should_fire=lambda *a, **k: True,
        record_fired=lambda *a, **k: None,
    )
    inst._snapshot_signals_for_llm = lambda snap, **k: {}

    asyncio.run(inst._activity_guess_loop())
    return seen


@pytest.mark.parametrize(
    ("ui_locale", "expected"),
    [("zh-TW", "zh-TW"), ("zh-CN", "zh-CN"), ("ja", "ja")],
)
def test_activity_guess_loop_passes_full_locale(monkeypatch, ui_locale, expected):
    """The narration locale must reach ``call_activity_guess`` as a full code.

    ``ACTIVITY_GUESS_PROMPTS`` carries a distinct ``zh-TW`` template and
    ``llm_enrichment._normalize_lang`` knows how to select it, so collapsing
    to the short code here is the one step that made Traditional users read
    a Simplified narration.
    """
    with language_context(ui_locale):
        seen = _drive_activity_guess_once(monkeypatch)

    assert seen["activity"] == expected
    # Both consumers ride the same value; the topic pool already needed full.
    assert seen["topic"] == expected


def test_activity_guess_traditional_locale_selects_traditional_template():
    """The value the loop now passes must actually change the prompt text —
    otherwise the flip above would be a no-op rename."""
    from config.prompts.prompts_activity import ACTIVITY_GUESS_PROMPTS
    from main_logic.activity.llm_enrichment import (
        _normalize_lang,
        _select_lang_template,
    )

    traditional = _select_lang_template(
        ACTIVITY_GUESS_PROMPTS, _normalize_lang("zh-TW"),
    )
    simplified = _select_lang_template(
        ACTIVITY_GUESS_PROMPTS, _normalize_lang("zh-CN"),
    )
    assert traditional == ACTIVITY_GUESS_PROMPTS["zh-TW"]
    assert simplified == ACTIVITY_GUESS_PROMPTS["zh"]
    assert traditional != simplified
    assert traditional != ACTIVITY_GUESS_PROMPTS["en"]


# ---------------------------------------------------------------------------
# plugin/plugins/game_agent_minecraft — user_lang() + PROMPTS tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ui_locale", "expected"),
    [("zh-TW", "zh-TW"), ("zh-CN", "zh"), ("zh", "zh"), ("ja", "ja"), ("en", "en")],
)
def test_minecraft_user_lang_resolves_to_table_key(ui_locale, expected):
    """``user_lang`` must land on a key of this module's own tables.

    Traditional keeps its own key; Simplified collapses to ``zh`` rather
    than leaking ``zh-CN`` — which is not a key here, so the
    ``in SUPPORTED_LANGS`` guard would have bounced every Simplified user
    to English.
    """
    from plugin.plugins.game_agent_minecraft import prompts

    with language_context(ui_locale):
        assert prompts.user_lang() == expected
    assert expected in prompts.SUPPORTED_LANGS


def test_minecraft_prompts_render_traditional_not_simplified_or_english():
    from plugin.plugins.game_agent_minecraft import prompts

    with language_context("zh-TW"):
        cue = prompts.t("TASK_NOT_CONNECTED", lang=prompts.user_lang())
    assert cue == prompts.PROMPTS["TASK_NOT_CONNECTED"]["zh-TW"]
    assert cue != prompts.PROMPTS["TASK_NOT_CONNECTED"]["zh"]
    assert cue != prompts.PROMPTS["TASK_NOT_CONNECTED"]["en"]


def test_minecraft_t_degrades_chinese_variants_to_simplified_not_english():
    """A future table that forgets ``zh-TW`` must fall back to Simplified.

    English mid-conversation is a language switch; Simplified is merely the
    wrong script. Same rule as ``prompts_sys._loc``.
    """
    from plugin.plugins.game_agent_minecraft import prompts

    incomplete = {"zh": "简体", "en": "English"}
    original = prompts.PROMPTS.get("__TEST_ONLY__")
    prompts.PROMPTS["__TEST_ONLY__"] = incomplete
    try:
        assert prompts.t("__TEST_ONLY__", lang="zh-TW") == "简体"
        assert prompts.t("__TEST_ONLY__", lang="ko") == "English"
    finally:
        if original is None:
            prompts.PROMPTS.pop("__TEST_ONLY__", None)
        else:  # pragma: no cover - defensive
            prompts.PROMPTS["__TEST_ONLY__"] = original


# ---------------------------------------------------------------------------
# Chat-platform plugins — SESSION_INIT_PROMPT lookups
# ---------------------------------------------------------------------------


def _assert_traditional_session_init(text: str, her_name: str) -> None:
    assert SESSION_INIT_PROMPT["zh-TW"].format(name=her_name) in text
    assert SESSION_INIT_PROMPT["zh"].format(name=her_name) not in text
    assert SESSION_INIT_PROMPT["en"].format(name=her_name) not in text


def _assert_simplified_session_init(text: str, her_name: str) -> None:
    assert SESSION_INIT_PROMPT["zh"].format(name=her_name) in text
    assert SESSION_INIT_PROMPT["zh-TW"].format(name=her_name) not in text
    assert SESSION_INIT_PROMPT["en"].format(name=her_name) not in text


@pytest.mark.parametrize(
    ("ui_locale", "check"),
    [("zh-TW", _assert_traditional_session_init),
     ("zh-CN", _assert_simplified_session_init)],
)
def test_bilibili_danmaku_trusted_write_instructions_locale(
    monkeypatch, ui_locale, check,
):
    import utils.config_manager as config_manager_mod
    from plugin.plugins.bilibili_danmaku import BiliDanmakuPlugin

    monkeypatch.setattr(
        config_manager_mod,
        "get_config_manager",
        lambda: SimpleNamespace(
            get_character_data=lambda: (
                "小明", "喵喵", None, {"喵喵": {}}, None,
                {"喵喵": "角色设定"}, None, None, None,
            ),
        ),
    )

    facade = object.__new__(BiliDanmakuPlugin)
    facade._target_lanlan = ""
    facade._master_bili_uid = 0
    facade._master_bili_name = ""
    facade._logged_in_matches_master = False
    facade._logged_in_bili_uid = 0

    async def _display_name():
        return "小明"

    facade._get_master_display_name = _display_name

    async def _run():
        return await facade._build_bili_trusted_write_instructions(
            action_name="评论",
            content_field="content",
            context="ctx",
            constraints="cons",
        )

    with language_context(ui_locale):
        prompt = asyncio.run(_run())
    check(prompt, "喵喵")


@pytest.mark.parametrize(
    ("ui_locale", "check"),
    [("zh-TW", _assert_traditional_session_init),
     ("zh-CN", _assert_simplified_session_init)],
)
def test_wechat_reply_system_prompt_locale(monkeypatch, ui_locale, check):
    import utils.config_manager as config_manager_mod
    import utils.llm_client as llm_client_mod
    from plugin.plugins.wechat_integration import WechatIntegrationPlugin

    monkeypatch.setattr(
        config_manager_mod,
        "get_config_manager",
        lambda: SimpleNamespace(
            get_character_data=lambda: (
                "小明", "喵喵", None, {"喵喵": {}}, None,
                {"喵喵": "角色设定"}, None, None, None,
            ),
            get_model_api_config=lambda kind: {
                "base_url": "http://localhost", "model": "m", "api_key": "k",
            },
        ),
    )

    captured: dict = {}

    class _StubLLM:
        async def ainvoke(self, messages):
            captured["system"] = messages[0]["content"]
            return SimpleNamespace(content="好的")

    async def _create(**kwargs):
        return _StubLLM()

    monkeypatch.setattr(llm_client_mod, "create_chat_llm_async", _create)

    facade = object.__new__(WechatIntegrationPlugin)
    facade.logger = MagicMock()
    facade._wechat_sessions = {}
    facade._cleanup_wechat_sessions = lambda now: None

    async def _fetch_memory(_her_name):
        return ""

    facade._fetch_memory_context = _fetch_memory

    with language_context(ui_locale):
        asyncio.run(facade._generate_wechat_reply("wxid_1", "在吗"))

    check(captured["system"], "喵喵")


# ---------------------------------------------------------------------------
# qq_auto_reply — init template + prompt-override locale
# ---------------------------------------------------------------------------


def _qq_service(settings=None, bundle=None):
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    messages = bundle or {}

    def _t(key, *, locale=None, default="", **params):
        from plugin.sdk.shared.i18n import locale_candidates
        for candidate in locale_candidates(locale, "zh-CN"):
            if candidate in messages and key in messages[candidate]:
                return messages[candidate][key]
        return default

    plugin = SimpleNamespace(
        i18n=SimpleNamespace(t=lambda key, default="", **kw: _t(
            key, default=default, **kw
        )),
        _qq_settings=settings or {},
        logger=MagicMock(),
    )
    return QQSessionInstructionService(plugin)


@pytest.mark.parametrize(
    ("locale", "expected_key"),
    [("zh-TW", "zh-TW"), ("zh-CN", "zh"), ("zh", "zh"), ("ja", "ja"), ("xx", "en")],
)
def test_qq_init_template_resolves_by_normalized_locale(locale, expected_key):
    """``_resolve_init_template`` is fed the full locale by
    ``build_instructions``; it must map ``zh-CN`` onto this table's ``zh``
    key and keep ``zh-TW`` distinct, without hand-splitting on ``-``."""
    service = _qq_service()
    assert service._resolve_init_template(locale) == (
        SESSION_INIT_PROMPT[expected_key]
    )


def test_qq_static_layer_default_locale_finds_traditional_override():
    """With no explicit locale the layer resolver falls back to the process
    locale. The prompt editor stores overrides under the frontend locale
    (``zh-TW`` for a Traditional user), and the short code ``zh`` never
    reaches that bucket — so a Traditional user's saved override was
    silently ignored.
    """
    service = _qq_service({
        "prompt_overrides": {
            "zh-TW": {"output_prompt_section": "繁體覆蓋"},
            "zh-CN": {"output_prompt_section": "简体覆盖"},
        },
    })

    with language_context("zh-TW"):
        assert service._resolve_static_layer(
            "output_prompt_section", "默认模板",
        ) == "繁體覆蓋"
    with language_context("zh-CN"):
        assert service._resolve_static_layer(
            "output_prompt_section", "默认模板",
        ) == "简体覆盖"


def test_qq_static_layer_default_locale_prefers_traditional_bundle():
    """Same fallback, i18n-bundle side: no override at all, and the
    Traditional bundle must win over the Simplified one — while a locale
    with no bundle still lands on the default template, not on English by
    accident."""
    service = _qq_service(bundle={
        "zh-TW": {"output_prompt_section": "繁體 bundle"},
        "zh-CN": {"output_prompt_section": "简体 bundle"},
    })

    with language_context("zh-TW"):
        assert service._resolve_static_layer(
            "output_prompt_section", "默认模板",
        ) == "繁體 bundle"
    with language_context("zh-CN"):
        assert service._resolve_static_layer(
            "output_prompt_section", "默认模板",
        ) == "简体 bundle"


@pytest.mark.parametrize(
    ("ui_locale", "check"),
    [("zh-TW", _assert_traditional_session_init),
     ("zh-CN", _assert_simplified_session_init)],
)
def test_qq_session_instructions_locale(ui_locale, check):
    """End-to-end through ``build_session_instructions`` itself: the init
    template and the memory closing line both have to come out Traditional.
    ``get_context_summary_ready`` was the second half of the collapse — it
    was fed the short code alongside the init lookup."""
    from unittest.mock import AsyncMock

    from config.prompts.prompts_sys import get_context_summary_ready
    from plugin.plugins.qq_auto_reply import session_instruction_service as module

    plugin = SimpleNamespace(
        logger=MagicMock(),
        _emit_log=lambda *a, **k: None,
        _qq_settings={},
        _user_sessions={},
        i18n=SimpleNamespace(t=lambda key, default="", **kw: default),
        memory_bridge=MagicMock(),
        permission_mgr=SimpleNamespace(
            get_nickname=lambda *a, **k: None,
            get_user_title=lambda *a, **k: "",
        ),
        qq_client=SimpleNamespace(needs_attention=False),
        fatigue_service=None,
        session_runtime_service=SimpleNamespace(),
    )
    service = module.QQSessionInstructionService(plugin)
    service._build_core_memory_section = AsyncMock(return_value="回忆片段")

    async def _run():
        return await service.build_session_instructions(
            her_name="喵喵",
            master_name="小明",
            character_prompt="角色设定",
            character_card_fields={},
            permission_level="admin",
            sender_id="2046",
            user_title="member",
            is_group=False,
            group_id=None,
            use_memory_context=True,
        )

    with language_context(ui_locale):
        bundle = asyncio.run(_run())

    check(bundle.system_prompt, "喵喵")

    # The closing line is rendered deeper in ``_build_core_memory_section``;
    # what this call site owns is the template it hands down.
    expected_key = "zh-TW" if ui_locale == "zh-TW" else "zh"
    other_key = "zh" if expected_key == "zh-TW" else "zh-TW"
    handed_down = service._build_core_memory_section.await_args.kwargs[
        "context_ready_template"
    ]
    assert handed_down == get_context_summary_ready(
        expected_key, input_mode="text",
    )
    assert handed_down != get_context_summary_ready(other_key, input_mode="text")
    assert handed_down != get_context_summary_ready("en", input_mode="text")


def test_qq_blank_override_is_treated_as_unset():
    """``save_prompt_override`` stores an empty string when the editor box is
    cleared, so a blank value means "not set" — the resolver has to keep
    walking the candidate chain rather than serve the blank.

    Without this the cleared layer would render as nothing at all, and a
    legacy bucket further down the chain would be masked by the blank.
    """
    masked = _qq_service({
        "prompt_overrides": {
            "zh-TW": {"output_prompt_section": "   "},
            "zh": {"output_prompt_section": "舊的繁中覆蓋"},
        },
    })
    with language_context("zh-TW"):
        assert masked._resolve_static_layer(
            "output_prompt_section", "默认模板",
        ) == "舊的繁中覆蓋"

    only_blank = _qq_service({
        "prompt_overrides": {"zh-TW": {"output_prompt_section": ""}},
    })
    with language_context("zh-TW"):
        assert only_blank._resolve_static_layer(
            "output_prompt_section", "默认模板",
        ) == "默认模板"


def _prompt_editor_facade(settings):
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    facade = object.__new__(QQAutoReplyPlugin)
    facade._qq_settings = settings
    facade._strategy_mode = "neko_dynamic"
    facade.session_instruction_service = QQSessionInstructionService(facade)
    facade.fatigue_service = None
    facade.attention_gate_service = None
    facade.i18n = SimpleNamespace(t=lambda key, **kw: kw.get("default", ""))
    facade._emit_log = lambda *a, **k: None
    facade.logger = MagicMock()
    return facade


def _layer_state(payload, layer_id):
    return next(
        layer for layer in payload["layers"] if layer["id"] == layer_id
    )


def test_qq_editor_sees_legacy_short_locale_override():
    """A Traditional user whose override predates #2500 has it stored under
    the old short code ``zh``.

    The runtime resolves overrides through ``locale_candidates``, so that
    bucket is still live. If the editor matched the locale key exactly it
    would report the layer as unmodified while the override kept applying —
    visible nowhere, resettable nowhere.
    """
    settings = {
        "qq_connection_mode": "napcat",
        "prompt_overrides": {"zh": {"output_prompt_section": "舊的繁中覆蓋"}},
    }
    facade = _prompt_editor_facade(settings)

    with language_context("zh-TW"):
        payload = getattr(
            asyncio.run(facade.get_prompt_editor_state()), "value", None,
        )
        # What the runtime actually serves, for comparison.
        runtime_text = facade.session_instruction_service._resolve_static_layer(
            "output_prompt_section", "默认模板",
        )

    layer = _layer_state(payload, "output")
    assert layer["has_override"] is True
    assert layer["effective_text"] == "舊的繁中覆蓋"
    assert runtime_text == layer["effective_text"]


@pytest.mark.asyncio
async def test_qq_reset_clears_the_bucket_the_runtime_actually_uses():
    """Reset must land on the same bucket the resolver reads.

    Keyed strictly by the current locale, resetting a legacy ``zh`` override
    returned ``no_override_found`` and left it applying forever.
    """
    from unittest.mock import AsyncMock

    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    settings = {
        "qq_connection_mode": "napcat",
        "prompt_overrides": {"zh": {"output_prompt_section": "舊的繁中覆蓋"}},
    }
    facade = _prompt_editor_facade(settings)
    facade.session_instruction_service._discard_all_sessions_for_prompt_change = (
        lambda: None
    )

    async def _mutate(_self, fn):
        return fn(settings)

    facade._mutate_business_config = AsyncMock(side_effect=None)

    with language_context("zh-TW"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                QQAutoReplyPlugin, "_mutate_business_config", _mutate,
            )
            result = await facade.reset_prompt_override(
                locale="zh-TW", layer_id="output",
            )

    payload = getattr(result, "value", result)
    assert payload.get("reason") != "no_override_found"
    assert settings["prompt_overrides"] == {}

    with language_context("zh-TW"):
        assert facade.session_instruction_service._resolve_static_layer(
            "output_prompt_section", "默认模板",
        ) == "默认模板"


@pytest.mark.parametrize(
    "shadowed_bucket", ["zh", "zh-CN", "en"],
)
@pytest.mark.asyncio
async def test_qq_reset_does_not_resurrect_a_shadowed_override(shadowed_bucket):
    """Reset must leave the default resolving, not the next bucket down.

    Once the editor saves under the full locale, an older bucket for the same
    layer keeps sitting further along the candidate chain. Deleting only the
    exact bucket hands the layer straight back to that older value — "restore
    default" then reports success while a custom prompt is still applied, and
    pressing it again changes nothing.

    ``zh-CN`` is the common case, not an exotic one: ``locale_candidates``
    appends the plugin's default locale to every chain.
    """
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    settings = {
        "qq_connection_mode": "napcat",
        "prompt_overrides": {
            "zh-TW": {"output_prompt_section": "新的繁中覆蓋"},
            shadowed_bucket: {"output_prompt_section": "被遮住的舊覆蓋"},
        },
    }
    facade = _prompt_editor_facade(settings)
    facade.session_instruction_service._discard_all_sessions_for_prompt_change = (
        lambda: None
    )

    async def _mutate(_self, fn):
        return fn(settings)

    with language_context("zh-TW"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(QQAutoReplyPlugin, "_mutate_business_config", _mutate)
            await facade.reset_prompt_override(
                locale="zh-TW", layer_id="output",
            )

        rendered = facade.session_instruction_service._resolve_static_layer(
            "output_prompt_section", "默认模板",
        )
        payload = getattr(
            asyncio.run(facade.get_prompt_editor_state()), "value", None,
        )

    assert rendered == "默认模板"
    assert _layer_state(payload, "output")["has_override"] is False


@pytest.mark.asyncio
async def test_qq_reset_sweeps_the_whole_candidate_chain():
    """Every position on the chain has to go, not just the first shadow.

    With buckets stacked at ``zh-TW`` / ``zh`` / ``zh-CN`` / ``en``, removing
    the exact bucket plus one more still leaves two behind — the layer would
    resolve to a custom prompt again. The sweep has to run to exhaustion.
    """
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    settings = {
        "qq_connection_mode": "napcat",
        "prompt_overrides": {
            "zh-TW": {"output_prompt_section": "繁中"},
            "zh": {"output_prompt_section": "舊短碼"},
            "zh-CN": {"output_prompt_section": "简中"},
            "en": {"output_prompt_section": "english"},
        },
    }
    facade = _prompt_editor_facade(settings)
    facade.session_instruction_service._discard_all_sessions_for_prompt_change = (
        lambda: None
    )

    async def _mutate(_self, fn):
        return fn(settings)

    with language_context("zh-TW"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(QQAutoReplyPlugin, "_mutate_business_config", _mutate)
            await facade.reset_prompt_override(
                locale="zh-TW", layer_id="output",
            )
        rendered = facade.session_instruction_service._resolve_static_layer(
            "output_prompt_section", "默认模板",
        )

    assert settings["prompt_overrides"] == {}
    assert rendered == "默认模板"


@pytest.mark.asyncio
async def test_qq_reset_leaves_other_layers_alone():
    """The sweep is per-layer: other layers' overrides in the same buckets
    must survive, or "restore default" on one layer would wipe the lot."""
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    settings = {
        "qq_connection_mode": "napcat",
        "prompt_overrides": {
            "zh-TW": {
                "output_prompt_section": "要重置的",
                "role_prompt_section": "要留下的",
            },
            "zh-CN": {"role_prompt_section": "也要留下的"},
        },
    }
    facade = _prompt_editor_facade(settings)
    facade.session_instruction_service._discard_all_sessions_for_prompt_change = (
        lambda: None
    )

    async def _mutate(_self, fn):
        return fn(settings)

    with language_context("zh-TW"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(QQAutoReplyPlugin, "_mutate_business_config", _mutate)
            await facade.reset_prompt_override(
                locale="zh-TW", layer_id="output",
            )

    assert settings["prompt_overrides"] == {
        "zh-TW": {"role_prompt_section": "要留下的"},
        "zh-CN": {"role_prompt_section": "也要留下的"},
    }


@pytest.mark.asyncio
async def test_qq_reset_clears_a_blank_placeholder_bucket():
    """A cleared layer stores ``""``, which ``resolve_prompt_override``
    deliberately ignores. Reset still has to remove it, otherwise the
    placeholder lingers forever and reset reports no_override_found."""
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    settings = {
        "qq_connection_mode": "napcat",
        "prompt_overrides": {"zh-TW": {"output_prompt_section": ""}},
    }
    facade = _prompt_editor_facade(settings)
    facade.session_instruction_service._discard_all_sessions_for_prompt_change = (
        lambda: None
    )

    async def _mutate(_self, fn):
        return fn(settings)

    with language_context("zh-TW"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(QQAutoReplyPlugin, "_mutate_business_config", _mutate)
            result = await facade.reset_prompt_override(
                locale="zh-TW", layer_id="output",
            )

    payload = getattr(result, "value", result)
    assert payload.get("reason") != "no_override_found"
    assert settings["prompt_overrides"] == {}


@pytest.mark.parametrize(
    ("ui_locale", "expected"), [("zh-TW", "zh-TW"), ("zh-CN", "zh-CN")],
)
def test_qq_prompt_editor_state_defaults_to_full_locale(ui_locale, expected):
    """The editor's locale is also the key ``save_prompt_override`` writes
    under, so it has to agree with the runtime read side above. A short
    code would make a Traditional user edit the Simplified bucket."""
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    facade = object.__new__(QQAutoReplyPlugin)
    facade._qq_settings = {"qq_connection_mode": "napcat"}
    facade._strategy_mode = "neko_dynamic"
    facade.session_instruction_service = SimpleNamespace(_PROMPT_LAYERS=[])
    facade.fatigue_service = None
    facade.attention_gate_service = None
    facade.i18n = SimpleNamespace(t=lambda key, **kw: kw.get("default", ""))
    facade._emit_log = lambda *a, **k: None
    facade.logger = MagicMock()

    with language_context(ui_locale):
        result = asyncio.run(facade.get_prompt_editor_state())

    payload = getattr(result, "value", result)
    assert payload["locale"] == expected
