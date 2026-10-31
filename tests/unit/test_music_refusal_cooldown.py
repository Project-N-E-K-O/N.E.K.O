# -*- coding: utf-8 -*-
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

"""Refusing music, on a card or in words, pauses music recommendations.

The player-side cooldown only fires when a started song is closed quickly.
Recommendation cards are skipped before anything plays, and a verbal "stop
playing songs" never touches the player, so before this change both left
music recommendations running.
"""

from collections import deque

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config.prompts.prompts_music_refusal import detect_music_refusal
from main_logic.proactive_chat import music_recommendation
from main_logic.proactive_chat import music_refusal
from main_logic.proactive_chat import state
from main_routers.system_router import proactive_chat_flow

LL = "Tian"


@pytest.fixture(autouse=True)
def _isolate_refusal_state(monkeypatch):
    monkeypatch.setattr(music_refusal, "_music_card_skip_counts", {})
    monkeypatch.setattr(music_refusal, "_music_refusal_cooldown_until", {})


@pytest.mark.parametrize("text", [
    # Taken from the 2026-10-31 report that motivated this change.
    "别再给我放歌啦 我在听自己的。",
    "别搞 我听自己的就行了",
    "别放歌了",
    "不要再推荐音乐了",
    "可以不要放歌吗",
    "歌就别放了",
    "不想听歌",
    "別再給我放歌了",
    "Stop playing music please",
    "don't recommend me any more songs",
    "no more music",
    "I'm listening to my own playlist",
    "曲はもういい",
    "音楽を流さないで",
    "노래 그만 틀어",
    "음악 추천하지 마",
    "не включай музыку",
    "хватит ставить песни",
    "no me pongas más música",
    "deja de recomendar canciones",
    "não coloque música",
    "pare de recomendar músicas",
])
def test_detects_music_refusal(text):
    assert detect_music_refusal(text)


@pytest.mark.parametrize("text", [
    "放首歌吧",
    "我想听歌",
    "要不要放歌？",
    "个别推荐歌曲还不错",
    "这首歌真好听",
    "别闹了",
    "don't stop playing music",
    "play some music",
    "I love this song",
    "歌がいいね",
    "",
])
def test_ignores_non_refusals(text):
    assert not detect_music_refusal(text)


def test_scan_is_capped_for_long_input():
    # The cap keeps a pasted wall of text from becoming a per-message cost.
    assert not detect_music_refusal("x" * 5000 + "别放歌了")


def test_two_consecutive_card_skips_start_the_cooldown():
    assert not music_refusal.record_music_card_skipped(LL, now=1000.0)
    assert not music_refusal.is_music_refusal_cooldown_active(LL, now=1001.0)
    assert music_refusal.record_music_card_skipped(LL, now=1002.0)
    assert music_refusal.is_music_refusal_cooldown_active(LL, now=1003.0)
    expiry = 1002.0 + music_refusal.MUSIC_REFUSAL_COOLDOWN_SECONDS
    assert music_refusal.is_music_refusal_cooldown_active(LL, now=expiry - 1)
    assert not music_refusal.is_music_refusal_cooldown_active(LL, now=expiry)


def test_cooldown_restarts_the_skip_streak():
    music_refusal.record_music_card_skipped(LL, now=0.0)
    music_refusal.record_music_card_skipped(LL, now=1.0)
    later = 1.0 + music_refusal.MUSIC_REFUSAL_COOLDOWN_SECONDS + 1
    assert not music_refusal.is_music_refusal_cooldown_active(LL, now=later)
    assert not music_refusal.record_music_card_skipped(LL, now=later)


def test_skip_streak_is_per_character():
    music_refusal.record_music_card_skipped(LL, now=0.0)
    music_refusal.record_music_card_skipped("Other", now=1.0)
    assert not music_refusal.is_music_refusal_cooldown_active(LL, now=2.0)
    assert not music_refusal.is_music_refusal_cooldown_active("Other", now=2.0)


def test_manual_accept_breaks_the_skip_streak():
    music_refusal.record_music_card_skipped(LL, now=0.0)
    music_refusal.reset_music_card_skips(LL)
    assert not music_refusal.record_music_card_skipped(LL, now=1.0)


def test_played_through_breaks_the_skip_streak_but_not_an_active_cooldown(monkeypatch):
    monkeypatch.setattr(state, "_proactive_chat_history", {LL: deque()})
    music_refusal.record_music_card_skipped(LL)
    music_recommendation._record_music_played_through(LL)
    assert not music_refusal.record_music_card_skipped(LL)

    music_refusal.record_music_card_skipped(LL)
    music_recommendation._record_music_played_through(LL)
    assert music_refusal.is_music_refusal_cooldown_active(LL)


def _utterance(content, lanlan=LL):
    return {"type": "user_message", "content": content, "lanlan": lanlan}


def test_verbal_refusal_starts_the_cooldown_once_per_message():
    event = _utterance("别再给我放歌啦")
    # turn.py dispatches one message to the "default" bucket and the character bucket.
    music_refusal.on_user_utterance("default", event)
    assert music_refusal._music_refusal_cooldown_until == {}
    music_refusal.on_user_utterance(LL, event)
    assert music_refusal.is_music_refusal_cooldown_active(LL)
    assert "default" not in music_refusal._music_refusal_cooldown_until


def test_verbal_refusal_reads_multimodal_text_parts():
    music_refusal.on_user_utterance(LL, _utterance([{"type": "text", "text": "不要再推荐音乐了"}, {"type": "image"}]))
    assert music_refusal.is_music_refusal_cooldown_active(LL)


def test_ordinary_message_leaves_music_alone():
    music_refusal.on_user_utterance(LL, _utterance("这首歌真好听"))
    assert not music_refusal.is_music_refusal_cooldown_active(LL)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(proactive_chat_flow, "_validate_local_mutation_request", lambda request: None)

    class _ConfigManager:
        async def aget_character_data(self):
            return ("", LL, None, None, None, None, None, None, None)

    monkeypatch.setattr(proactive_chat_flow, "get_config_manager", lambda: _ConfigManager())
    app = FastAPI()
    app.include_router(proactive_chat_flow.router)
    with TestClient(app) as test_client:
        yield test_client


def test_card_decision_endpoint_counts_skips_and_accepts(client):
    url = "/api/proactive/music_card_decision"
    assert client.post(url, json={"lanlan_name": LL, "decision": "skipped"}).json()["cooldown"] is False
    assert client.post(url, json={"lanlan_name": LL, "decision": "accepted"}).json()["cooldown"] is False
    assert client.post(url, json={"lanlan_name": LL, "decision": "skipped"}).json()["cooldown"] is False
    # Falls back to the current character when the page did not send one.
    response = client.post(url, json={"decision": "skipped"})
    assert response.json() == {"success": True, "cooldown": True, "lanlan_name": LL}
    assert music_refusal.is_music_refusal_cooldown_active(LL)


def test_card_decision_endpoint_rejects_unknown_decisions(client):
    response = client.post("/api/proactive/music_card_decision", json={"lanlan_name": LL, "decision": "shown"})
    assert response.status_code == 400
    assert music_refusal._music_card_skip_counts == {}


def test_proactive_request_honours_the_backend_cooldown():
    # handle_proactive_chat already drops music whenever ``music_cooldown`` is
    # set; the backend cooldown must feed that same flag, not a parallel one.
    import inspect

    from main_logic.proactive_chat import service

    source = inspect.getsource(service.handle_proactive_chat)
    assert (
        "music_cooldown = command.music_cooldown or is_music_refusal_cooldown_active(lanlan_name)"
        in source
    )
