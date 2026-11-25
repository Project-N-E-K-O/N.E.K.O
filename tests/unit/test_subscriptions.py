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


def test_overlay_skips_plans_that_are_off_or_have_no_key():
    plans = get_plans()
    state = {"plans": {
        "chatgpt": {"enabled": True, "tiers": {"conversation": "gpt-x", "realtime": "x"}, "api_key": "", "region": ""},
        "mimo_token_plan": {"enabled": True, "tiers": {"memory": "mimo-v2.6-pro"}, "api_key": "", "region": "sgp"},
    }}
    overlay = build_overlay(state, plans, {})
    assert set(overlay) == {"conversation"}
    assert overlay["conversation"]["provider_type"] == "chatgpt" and overlay["conversation"]["api_key"] == ""
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
    assert row == {"effective": None, "claimed_by": "chatgpt", "own_setting": True}
