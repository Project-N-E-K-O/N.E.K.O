# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Subscriptions & plans: the overlay never rewrites tier settings and follows the agreed precedence.

Precedence: a tier's own per-feature setting (set up in full) > a subscription
that takes the tier > following / the general providers.
"""

import json

import httpx
import pytest

from utils.subscriptions import TEXT_TIERS
from utils.subscriptions.overlay import build_overlay
from utils.subscriptions.plans import get_plans
from utils.subscriptions.store import SubscriptionError, SubscriptionStore, parse

BASE_CORE = {"coreApi": "qwen", "assistApi": "qwen", "assistApiKeyQwen": "sk-qwen-test"}


@pytest.fixture()
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager

    cm = get_config_manager("N.E.K.O")
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    cm.ensure_local_state_directory()
    core = cm.config_dir / "core_config.json"
    subs = cm.local_state_dir / "subscriptions.json"
    saved = {path: (path.read_bytes() if path.exists() else None) for path in (core, subs)}
    yield cm
    for path, data in saved.items():
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(data)


def _write_core(cm, data):
    (cm.config_dir / "core_config.json").write_text(json.dumps(data), encoding="utf-8")


def _store(cm):
    return SubscriptionStore(cm.local_state_dir / "subscriptions.json")


def _all_tiers(cm):
    return {tier: cm.get_model_api_config(tier) for tier in TEXT_TIERS}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_a_tier_belongs_to_one_plan_and_moves_on_request(tmp_path):
    store = SubscriptionStore(tmp_path / "subscriptions.json")
    assert store.set_tier("chatgpt", "conversation", "gpt-x") is None
    assert store.set_tier("mimo_token_plan", "conversation", "mimo-v2.6-pro") == "chatgpt"
    plans = store.read()["plans"]
    assert plans["chatgpt"]["tiers"] == {}
    assert plans["mimo_token_plan"]["tiers"] == {"conversation": "mimo-v2.6-pro"}
    store.set_tier("mimo_token_plan", "conversation", None)
    assert store.read()["plans"]["mimo_token_plan"]["tiers"] == {}
    with pytest.raises(SubscriptionError):
        store.set_tier("chatgpt", "realtime", "x")


def test_a_plan_left_with_no_tier_turns_off_in_the_same_write(tmp_path):
    store = SubscriptionStore(tmp_path / "subscriptions.json")
    store.set_tier("chatgpt", "conversation", "gpt-x")
    store.set_enabled("chatgpt", True)
    store.set_key("mimo_token_plan", "tp-test")
    store.set_tier("mimo_token_plan", "memory", "mimo-v2.6-pro")
    store.set_enabled("mimo_token_plan", True)

    # Moving the only tier away leaves that plan with nothing: it is off.
    assert store.set_tier("mimo_token_plan", "conversation", "mimo-v2.6-pro") == "chatgpt"
    plans = store.read()["plans"]
    assert plans["chatgpt"]["enabled"] is False and plans["mimo_token_plan"]["enabled"] is True

    store.set_tier("mimo_token_plan", "memory", None)
    assert store.read()["plans"]["mimo_token_plan"]["enabled"] is True  # still takes conversation
    store.set_tier("mimo_token_plan", "conversation", None)
    assert store.read()["plans"]["mimo_token_plan"]["enabled"] is False


def test_turning_a_plan_on_is_checked_inside_the_same_write(tmp_path):
    store = SubscriptionStore(tmp_path / "subscriptions.json")
    store.set_tier("chatgpt", "conversation", "gpt-x")
    seen = []

    def refuse(entry):
        seen.append(dict(entry["tiers"]))
        raise SubscriptionError("refused")

    with pytest.raises(SubscriptionError):
        store.set_enabled("chatgpt", True, precheck=refuse)
    assert seen == [{"conversation": "gpt-x"}]
    assert store.read()["plans"]["chatgpt"]["enabled"] is False
    store.set_enabled("chatgpt", True, precheck=lambda entry: None)
    assert store.read()["plans"]["chatgpt"]["enabled"] is True


def test_removing_a_key_turns_the_plan_off_and_keys_are_masked(tmp_path):
    store = SubscriptionStore(tmp_path / "subscriptions.json")
    store.set_key("mimo_token_plan", "tp-secret", "sgp")
    store.set_enabled("mimo_token_plan", True)
    masked = store.masked()["plans"]["mimo_token_plan"]
    assert masked["has_key"] is True and "api_key" not in masked
    assert "tp-secret" not in json.dumps(store.masked())
    store.set_key("mimo_token_plan", "")
    entry = store.read()["plans"]["mimo_token_plan"]
    assert entry["enabled"] is False and entry["api_key"] == "" and entry["region"] == "sgp"


def test_unreadable_documents_mean_no_subscriptions(tmp_path):
    for text in (None, "", "not json", "[]", '{"plans": 3}'):
        assert parse(text) == {"version": 1, "plans": {}}
    junk = parse(json.dumps({"plans": {"chatgpt": {"enabled": "yes", "tiers": {"realtime": "x", "memory": 3, "agent": " m "}}}}))
    assert junk["plans"]["chatgpt"] == {"enabled": False, "tiers": {"agent": "m"}, "api_key": "", "region": ""}
    # A write never replaces a document it cannot read.
    path = tmp_path / "subscriptions.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(SubscriptionError):
        SubscriptionStore(path).set_enabled("chatgpt", True)
    assert path.read_text(encoding="utf-8") == "not json"


# ---------------------------------------------------------------------------
# Overlay resolution
# ---------------------------------------------------------------------------


def test_plan_definitions_load():
    plans = get_plans()
    assert plans["chatgpt"]["auth"] == "chatgpt_oauth" and plans["chatgpt"]["tiers"] == list(TEXT_TIERS)
    mimo = plans["mimo_token_plan"]
    assert mimo["regions"]["cn"].startswith("https://token-plan-cn.") and mimo["key_fallback"]


def test_overlay_skips_plans_that_are_off_and_keeps_keyless_ones_failing():
    plans = get_plans()
    state = {"plans": {
        "chatgpt": {"enabled": True, "tiers": {"conversation": "gpt-x", "realtime": "x"}, "api_key": "", "region": ""},
        "mimo_token_plan": {"enabled": True, "tiers": {"memory": "mimo-v2.6-pro"}, "api_key": "", "region": "sgp"},
    }}
    overlay = build_overlay(state, plans, {})
    assert set(overlay) == {"conversation", "memory"}
    assert overlay["conversation"]["provider_type"] == "chatgpt" and overlay["conversation"]["api_key"] == ""
    # On without a key: the tier stays with the plan and its requests fail on authentication.
    assert overlay["memory"]["subscription"] == "mimo_token_plan" and overlay["memory"]["api_key"] == ""
    # The plan key entered under General Model Provider stands in for a missing own key.
    overlay = build_overlay(state, plans, {"ASSIST_API_KEY_MIMO_TOKEN_PLAN": "tp-general"})
    assert overlay["memory"]["api_key"] == "tp-general"
    assert overlay["memory"]["base_url"] == "https://token-plan-sgp.xiaomimimo.com/v1"
    state["plans"]["chatgpt"]["enabled"] = False
    assert "conversation" not in build_overlay(state, plans, {})


def test_without_subscriptions_every_tier_resolves_exactly_as_before(config_manager):
    cm = config_manager
    _write_core(cm, BASE_CORE)
    baseline = _all_tiers(cm)
    assert not any("subscription" in result for result in baseline.values())

    store = _store(cm)
    store.set_tier("chatgpt", "conversation", "gpt-x")  # chosen but switched off
    assert _all_tiers(cm) == baseline

    (cm.local_state_dir / "subscriptions.json").write_text("{broken", encoding="utf-8")
    assert _all_tiers(cm) == baseline


def test_subscription_takes_its_tiers_and_followers_follow(config_manager):
    cm = config_manager
    _write_core(cm, BASE_CORE)
    baseline = _all_tiers(cm)
    store = _store(cm)
    store.set_tier("chatgpt", "conversation", "gpt-x")
    store.set_enabled("chatgpt", True)

    results = _all_tiers(cm)
    assert results["conversation"] == {
        "model": "gpt-x", "api_key": "", "base_url": "https://api.openai.com/v1", "is_custom": True,
        "provider_type": "chatgpt", "api_type": None, "subscription": "chatgpt",
    }
    # The minigame chat tier follows the chat tier by default.
    assert results["game_main"]["subscription"] == "chatgpt"
    for tier in set(TEXT_TIERS) - {"conversation", "game_main"}:
        assert results[tier] == baseline[tier], tier

    # The small task agent tier follows recognition by default.
    store.set_key("mimo_token_plan", "tp-test")
    store.set_tier("mimo_token_plan", "recognition", "mimo-v2.6-flash")
    store.set_enabled("mimo_token_plan", True)
    results = _all_tiers(cm)
    assert results["recognition"]["subscription"] == "mimo_token_plan"
    assert results["agent_small"]["subscription"] == "mimo_token_plan"
    assert results["agent_small"]["api_key"] == "tp-test"

    # Off again: every tier is back to the user's own settings, untouched.
    store.set_enabled("chatgpt", False)
    store.set_enabled("mimo_token_plan", False)
    assert _all_tiers(cm) == baseline


def test_a_tier_set_up_in_full_keeps_its_own_setting(config_manager):
    cm = config_manager
    core = {
        **BASE_CORE,
        "enableCustomApi": True,
        "assistApiKeyOpenai": "sk-openai-test",
        "conversationModelProvider": "openai",
        "conversationModelUrl": "https://api.openai.com/v1",
        "conversationModelId": "gpt-own",
        "memoryModelProvider": "follow_assist",
    }
    _write_core(cm, core)
    baseline = _all_tiers(cm)
    store = _store(cm)
    store.set_tier("chatgpt", "conversation", "gpt-x")
    store.set_tier("chatgpt", "memory", "gpt-y")
    store.set_enabled("chatgpt", True)

    results = _all_tiers(cm)
    assert results["conversation"] == baseline["conversation"]
    assert results["conversation"]["model"] == "gpt-own"
    assert results["game_main"] == baseline["game_main"]  # follows the chat tier's own setting
    # A tier that only follows the general provider is taken by the subscription.
    assert results["memory"]["subscription"] == "chatgpt"


def test_a_tier_missing_its_own_model_is_not_set_up_in_full(config_manager):
    """An address without a model is incomplete, even though the snapshot fills a fallback model in."""
    cm = config_manager
    _write_core(cm, {
        **BASE_CORE,
        "enableCustomApi": True,
        "conversationModelProvider": "custom",
        "conversationModelUrl": "https://llm.example.test/v1",
        "conversationModelId": "",
    })
    assert cm.get_core_config()["CONVERSATION_MODEL"]  # filled from the general provider
    store = _store(cm)
    store.set_tier("chatgpt", "conversation", "gpt-x")
    store.set_enabled("chatgpt", True)
    assert cm.get_model_api_config("conversation")["subscription"] == "chatgpt"


def test_a_tier_saved_without_a_provider_keeps_the_address_and_model_entered(config_manager):
    """Settings saved before the provider field existed: the resolver uses them, so they win."""
    cm = config_manager
    core = {
        **BASE_CORE,
        "enableCustomApi": True,
        "conversationModelUrl": "https://llm.example.test/v1",
        "conversationModelId": "own-model",
        "memoryModelUrl": "https://llm.example.test/v1",
        "gameMainModelUrl": "https://llm.example.test/v1",
        "gameMainModelId": "own-game-model",
    }
    _write_core(cm, core)
    baseline = _all_tiers(cm)
    assert baseline["conversation"]["model"] == "own-model"
    store = _store(cm)
    for tier in ("conversation", "memory", "game_main"):
        store.set_tier("chatgpt", tier, "gpt-x")
    store.set_enabled("chatgpt", True)

    results = _all_tiers(cm)
    assert results["conversation"] == baseline["conversation"]
    # Only an address was entered: the model comes from defaults, so the subscription wins.
    assert results["memory"]["subscription"] == "chatgpt"
    # An empty provider on the minigame chat tier means following the chat tier.
    assert results["game_main"]["subscription"] == "chatgpt"


def test_minigame_tiers_following_the_other_tier_inherit_its_subscription(config_manager):
    cm = config_manager
    core = {
        **BASE_CORE,
        "enableCustomApi": True,
        "gameMainModelProvider": "follow_memory",
        "gameSummaryModelProvider": "follow_conversation",
    }
    _write_core(cm, core)
    baseline = _all_tiers(cm)
    store = _store(cm)
    store.set_tier("chatgpt", "memory", "gpt-memory")
    store.set_enabled("chatgpt", True)
    results = _all_tiers(cm)
    assert results["game_main"] == results["memory"]
    assert results["game_main"]["model"] == "gpt-memory"
    assert results["game_summary"] == baseline["game_summary"]

    store.set_tier("chatgpt", "conversation", "gpt-chat")
    results = _all_tiers(cm)
    assert results["game_summary"] == results["conversation"]
    assert results["game_summary"]["model"] == "gpt-chat"

    store.set_enabled("chatgpt", False)
    assert _all_tiers(cm) == baseline


def test_a_plan_whose_reused_key_was_removed_does_not_fall_back_to_another_key(config_manager):
    cm = config_manager
    plan = get_plans()["ark_coding_plan"]
    tier = plan["tiers"][0]
    _write_core(cm, {**BASE_CORE, "assistApiKeyDoubao": "sk-doubao-test"})
    store = _store(cm)
    store.set_tier("ark_coding_plan", tier, "doubao-seed-2.1-pro")
    store.set_enabled("ark_coding_plan", True)
    assert cm.get_model_api_config(tier)["api_key"] == "sk-doubao-test"

    _write_core(cm, BASE_CORE)  # the reused key removed under General Model Provider
    result = cm.get_model_api_config(tier)
    assert result["subscription"] == "ark_coding_plan"
    assert result["api_key"] == ""


def test_agent_server_drops_its_read_cache_when_told_the_configuration_changed(monkeypatch):
    import asyncio

    from app.agent_server import api_routes
    from utils.config_manager import read_cache

    dropped = []
    monkeypatch.setattr(read_cache, "invalidate", lambda path=None: dropped.append(path))
    monkeypatch.setattr(api_routes.Modules, "analyzer_enabled", False)
    result = asyncio.run(api_routes.notify_config_changed())
    assert result["success"] is True
    assert dropped == [None]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.fixture()
def client_factory(config_manager, monkeypatch):
    from fastapi import FastAPI

    from main_routers.config_router import subscriptions

    monkeypatch.setattr(subscriptions, "_chatgpt_connected", lambda: True)
    notified = []

    async def notify():
        notified.append(True)

    monkeypatch.setattr(subscriptions, "_notify_agent_server", notify)
    app = FastAPI()
    app.include_router(subscriptions.router)

    def make(host="127.0.0.1"):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(host, 123)),
            base_url="http://127.0.0.1:48999",
        )

    return make, notified


@pytest.mark.parametrize("turned_off", [True, False])
def test_signing_out_tells_the_agent_server_only_when_the_subscription_went_off(monkeypatch, turned_off):
    import asyncio

    from main_routers.config_router import chatgpt, subscriptions

    told = []

    async def notify():
        told.append(True)

    monkeypatch.setattr(chatgpt, "_change_account", lambda action, account_id: True)
    monkeypatch.setattr(chatgpt, "_turn_off_subscription_without_accounts", lambda: turned_off)
    monkeypatch.setattr(subscriptions, "_notify_agent_server", notify)
    result = asyncio.run(chatgpt.chatgpt_logout(chatgpt.AccountRequest(account_id="issued-1")))
    assert result["subscription_turned_off"] is turned_off
    assert told == ([True] if turned_off else [])


def test_turning_chatgpt_off_after_sign_out_never_fails_the_sign_out(monkeypatch):
    from main_routers.config_router import chatgpt
    from utils.chatgpt_auth import ChatGPTAuthError

    class Unreadable:
        def status(self):
            raise ChatGPTAuthError("credentials unreadable")

    monkeypatch.setattr(chatgpt, "get_store", lambda: Unreadable())
    assert chatgpt._turn_off_subscription_without_accounts() is False


@pytest.mark.asyncio
async def test_status_names_the_plan_a_tier_is_ticked_under_even_while_it_is_off(config_manager, client_factory):
    cm = config_manager
    _write_core(cm, BASE_CORE)
    make, _ = client_factory
    prefix = "/api/config/subscriptions"
    async with make() as client:
        await client.post(f"{prefix}/chatgpt/tier", json={"tier": "conversation", "model": "gpt-x"})
        row = (await client.get(prefix)).json()["tiers"]["conversation"]
        assert row["assigned_to"] == "chatgpt" and row["claimed_by"] is None
        assert (await client.get(prefix)).json()["tiers"]["memory"]["assigned_to"] is None


@pytest.mark.asyncio
async def test_routes_name_a_damaged_file_and_a_failed_save(config_manager, client_factory, monkeypatch):
    from utils.subscriptions.store import SubscriptionStore

    cm = config_manager
    _write_core(cm, BASE_CORE)
    make, _ = client_factory
    prefix = "/api/config/subscriptions"
    path = cm.local_state_dir / "subscriptions.json"
    async with make() as client:
        path.write_text("{broken", encoding="utf-8")
        damaged = await client.post(f"{prefix}/chatgpt/tier", json={"tier": "conversation", "model": "gpt-x"})
        assert damaged.status_code == 400 and damaged.json()["detail"]["code"] == "unreadable"
        path.write_bytes(bytes([0xFF, 0xFE]) + b" not utf-8")
        damaged = await client.post(f"{prefix}/chatgpt/tier", json={"tier": "conversation", "model": "gpt-x"})
        assert damaged.status_code == 400 and damaged.json()["detail"]["code"] == "unreadable"
        path.unlink()

        def held(self, value):
            raise PermissionError("held by another program")

        monkeypatch.setattr(SubscriptionStore, "_write", held)
        failed = await client.post(f"{prefix}/chatgpt/tier", json={"tier": "conversation", "model": "gpt-x"})
        assert failed.status_code == 500 and failed.json()["detail"]["code"] == "save_failed"


@pytest.mark.asyncio
async def test_routes_change_one_thing_at_a_time(config_manager, client_factory):
    cm = config_manager
    _write_core(cm, BASE_CORE)
    make, notified = client_factory
    prefix = "/api/config/subscriptions"
    async with make() as client:
        status = (await client.get(prefix)).json()
        assert {plan["id"] for plan in status["plans"]} >= {"chatgpt", "mimo_token_plan"}
        assert all(row["effective"] is None for row in status["tiers"].values())

        refused = await client.post(f"{prefix}/chatgpt/enable", json={"enabled": True})
        assert refused.status_code == 400 and refused.json()["detail"]["code"] == "no_tiers"

        assert (await client.post(f"{prefix}/chatgpt/tier", json={"tier": "conversation", "model": "gpt-x"})).status_code == 200
        on = await client.post(f"{prefix}/chatgpt/enable", json={"enabled": True})
        assert on.status_code == 200
        assert on.json()["tiers"]["conversation"]["effective"] == "chatgpt"
        assert on.json()["tiers"]["game_main"]["effective"] == "chatgpt"
        assert notified

        refused = await client.post(f"{prefix}/mimo_token_plan/enable", json={"enabled": True})
        assert refused.json()["detail"]["code"] == "missing_key"
        await client.post(f"{prefix}/mimo_token_plan/key", json={"api_key": "tp-secret", "region": "ams"})
        moved = await client.post(f"{prefix}/mimo_token_plan/tier", json={"tier": "conversation", "model": "mimo-v2.6-pro"})
        assert moved.json()["moved_from"] == "chatgpt"
        # ChatGPT had nothing left to take over, so it is off now.
        assert moved.json()["state"]["plans"]["chatgpt"]["enabled"] is False
        assert "tp-secret" not in moved.text

        assert (await client.post(f"{prefix}/chatgpt/tier", json={"tier": "realtime", "model": "x"})).status_code == 400
        assert (await client.post(f"{prefix}/nope/enable", json={"enabled": True})).status_code == 404

        # Removing the key turns the plan off.
        await client.post(f"{prefix}/mimo_token_plan/enable", json={"enabled": True})
        off = await client.post(f"{prefix}/mimo_token_plan/key", json={"api_key": ""})
        assert off.json()["state"]["plans"]["mimo_token_plan"]["enabled"] is False

    # core_config.json was never written by any of this.
    assert json.loads((cm.config_dir / "core_config.json").read_text(encoding="utf-8")) == BASE_CORE

    async with make("192.0.2.1") as remote:
        assert (await remote.get(prefix)).status_code == 403


@pytest.mark.asyncio
async def test_a_tier_set_up_in_full_is_reported_as_own_setting(config_manager, client_factory):
    cm = config_manager
    _write_core(cm, {
        **BASE_CORE, "enableCustomApi": True, "assistApiKeyOpenai": "sk-openai-test",
        "conversationModelProvider": "openai", "conversationModelUrl": "https://api.openai.com/v1",
        "conversationModelId": "gpt-own",
    })
    make, _ = client_factory
    async with make() as client:
        await client.post("/api/config/subscriptions/chatgpt/tier", json={"tier": "conversation", "model": "gpt-x"})
        status = (await client.post("/api/config/subscriptions/chatgpt/enable", json={"enabled": True})).json()
    row = status["tiers"]["conversation"]
    assert row == {"assigned_to": "chatgpt", "effective": None, "claimed_by": "chatgpt", "own_setting": True}


# ---------------------------------------------------------------------------
# The plans owner approved (2026-11-25) and what they need from the provider tables
# ---------------------------------------------------------------------------

APPROVED_PLANS = (
    "chatgpt", "mimo_token_plan", "kimi_code", "minimax_m_plan", "bailian_token_plan",
    "bailian_coding_plan", "ark_coding_plan", "glm_coding_plan", "step_plan",
    "qianfan_token_plan", "tencent_token_plan", "opencode_go", "ollama_cloud",
)


def test_all_approved_plans_load_with_consistent_defaults():
    plans = get_plans()
    assert tuple(plans) == APPROVED_PLANS
    for plan_id, plan in plans.items():
        assert plan["tiers"], plan_id
        for tier, model in plan["default_models"].items():
            assert tier in plan["tiers"], (plan_id, tier)
            assert plan["models"] == "account" or model in plan["models"], (plan_id, model)
        if plan["auth"] == "api_key":
            # Every tier a keyed plan offers has a model to start from.
            assert set(plan["default_models"]) == set(plan["tiers"]), plan_id
        for url in [plan["base_url"], *plan["regions"].values()]:
            assert not url or url.startswith("https://"), (plan_id, url)
    # GLM Coding Plan reads images only through a tool: no chat tiers that must see pictures.
    assert {"conversation", "game_main"}.isdisjoint(plans["glm_coding_plan"]["tiers"])
    # Kimi Code reuses the Kimi Code provider: Anthropic protocol and its key-book key.
    assert plans["kimi_code"]["provider_type"] == "anthropic"
    assert plans["kimi_code"]["key_fallback"] == "ASSIST_API_KEY_KIMI_CODE"
    assert plans["ollama_cloud"]["fetch_models"] and plans["opencode_go"]["fetch_models"]


def test_plan_default_models_turn_thinking_down():
    """Every default model of a keyed plan gets a thinking-off body, or is a known exception."""
    from config.providers import get_extra_body
    from utils.subscriptions.plans import plan_base_url

    # Kimi Code models are not registered: K3.1 Pro cannot turn thinking off and the
    # kimi-for-coding default behaves as before this change (unchanged provider).
    known_unregistered = {"kimi-for-coding"}
    for plan_id, plan in get_plans().items():
        if plan["auth"] != "api_key":
            continue
        url = plan_base_url(plan, None)
        for model in set(plan["default_models"].values()) - known_unregistered:
            assert get_extra_body(model, url), (plan_id, model)


def test_gateway_ids_map_onto_registered_models():
    from config.providers import EXTRA_BODY_CLAUDE, get_extra_body

    tencent = "https://tokenhub.tencentmaas.com/plan/v3"
    assert get_extra_body("deepseek-v4-pro-0813", tencent) == EXTRA_BODY_CLAUDE
    assert get_extra_body("minimax-m3", "https://ollama.com/v1") == EXTRA_BODY_CLAUDE
    # Elsewhere the dated or lowercase id stays unknown.
    assert get_extra_body("deepseek-v4-pro-0813", "https://api.example.com/v1") == {}


def test_opencode_go_requests_carry_one_session_header():
    from config.providers import get_cache_kwargs

    first = get_cache_kwargs("https://opencode.ai/inference/go/openai/v1")["default_headers"]
    second = get_cache_kwargs("https://opencode.ai/inference/go/openai/v1")["default_headers"]
    assert first["x-opencode-session"].startswith("ses_")
    assert first == second
    assert "x-opencode-session" not in get_cache_kwargs("https://api.openai.com/v1")["default_headers"]


def test_kimi_code_overseas_host_is_the_same_service():
    from utils.llm_client.anthropic_client import _is_kimi_code_anthropic_base_url

    assert _is_kimi_code_anthropic_base_url("https://api.kimi.ai/coding")
    assert _is_kimi_code_anthropic_base_url("https://api.kimi.com/coding/")
    assert not _is_kimi_code_anthropic_base_url("https://api.kimi.ai/v1")
