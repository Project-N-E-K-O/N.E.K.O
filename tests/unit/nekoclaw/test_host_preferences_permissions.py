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

"""Task preferences and permission settings."""

from __future__ import annotations

import json
from typing import Any

import pytest

from nekoclaw.host.ledger import Ledger
from nekoclaw.host.permissions import (
    DEFAULT_MODES,
    DENIED_BY_SETTINGS,
    DENIED_BY_USER,
    DENIED_NO_ANSWER,
    PermissionGate,
    PermissionStore,
)
from nekoclaw.host.preferences import INFERRED_MARK, PreferenceStore


@pytest.fixture
async def preferences(tmp_path: Any) -> Any:
    ledger = Ledger(tmp_path / "ledger.db")
    yield PreferenceStore(ledger)
    await ledger.close()


# ---------------------------------------------------------------------------
# Preferences: when they take effect
# ---------------------------------------------------------------------------


async def test_a_stated_preference_applies_at_once(preferences):
    entry = await preferences.state("查天气默认查上海", kind="default", lanlan_name="Lan")

    assert (entry.origin, entry.active, entry.observations) == ("stated", True, 1)
    assert await preferences.render("帮我查天气") == "- 查天气默认查上海"


async def test_an_inferred_preference_needs_two_observations(preferences):
    first = await preferences.observe("The user stopped a task done with weather.today: 查上海天气", kind="capability")

    assert (first.origin, first.active, first.observations) == ("inferred", False, 1)
    assert await preferences.render("weather.today 查上海天气") == ""

    second = await preferences.observe("The user stopped a task done with weather.today: 查北京天气", kind="capability")

    assert second.id == first.id
    assert (second.active, second.observations) == (True, 2)
    rendered = await preferences.render("weather today 天气")
    assert rendered.endswith(INFERRED_MARK)
    assert "weather.today" in rendered


async def test_different_tendencies_do_not_add_up(preferences):
    await preferences.observe("The user stopped a task done with weather.today: 查上海天气")
    other = await preferences.observe("Prefers summaries as bullet points in English")

    assert other.observations == 1 and not other.active
    assert len(await preferences.list()) == 2
    assert await preferences.list(active_only=True) == []


async def test_threshold_is_two(preferences):
    from config.nekoclaw_settings import PREFERENCE_INFERRED_THRESHOLD

    assert PREFERENCE_INFERRED_THRESHOLD == 2


async def test_stating_what_was_inferred_makes_it_stated_and_active(preferences):
    inferred = await preferences.observe("Use the lifekit plugin for nearby restaurant searches")

    stated = await preferences.state("Use the lifekit plugin for nearby restaurant searches please")

    assert stated.id == inferred.id
    assert (stated.origin, stated.active) == ("stated", True)
    assert len(await preferences.list()) == 1


async def test_observation_matching_a_stated_preference_changes_nothing(preferences):
    stated = await preferences.state("Use the lifekit plugin for nearby restaurant searches")

    observed = await preferences.observe("Use the lifekit plugin for nearby restaurant searches")

    assert observed.id == stated.id
    assert (observed.origin, observed.observations) == ("stated", 1)


async def test_stating_the_same_thing_twice_keeps_one_entry(preferences):
    first = await preferences.state("办完不用主动告诉我", kind="report")
    second = await preferences.state("办完不用主动告诉我", kind="report")

    assert second.id == first.id
    assert len(await preferences.list()) == 1


async def test_empty_text_is_not_recorded(preferences):
    assert await preferences.state("   ") is None
    assert await preferences.observe("") is None
    assert await preferences.list() == []


async def test_a_stored_preference_is_capped_in_tokens(preferences):
    from config.nekoclaw_settings import PREFERENCE_TEXT_MAX_TOKENS
    from utils.tokenize import count_tokens

    entry = await preferences.state(" ".join(f"word{n}" for n in range(400)))

    assert count_tokens(entry.text) <= PREFERENCE_TEXT_MAX_TOKENS


async def test_console_can_edit_switch_off_and_remove(preferences):
    entry = await preferences.observe("Never email results")
    assert not entry.active

    switched = await preferences.set_active(entry.id, True)
    assert switched.active
    edited = await preferences.edit(entry.id, text="Never email results; put them in the notes folder", kind="report")
    assert (edited.origin, edited.kind) == ("console", "report")
    assert await preferences.remove(entry.id) is True
    assert await preferences.remove(entry.id) is False
    assert await preferences.get(entry.id) is None


# ---------------------------------------------------------------------------
# Preferences: what a prompt is given
# ---------------------------------------------------------------------------


async def test_injected_preferences_never_exceed_150_tokens(preferences):
    from config.nekoclaw_settings import PREFERENCE_PROMPT_MAX_TOKENS
    from utils.tokenize import count_tokens

    assert PREFERENCE_PROMPT_MAX_TOKENS == 150
    for number in range(40):
        await preferences.state(
            f"For weather task number {number} always use provider {number} and answer in a table with units", kind="default"
        )

    rendered = await preferences.render("weather task provider table units")

    assert rendered
    assert count_tokens(rendered) <= 150
    # Whole entries only: nothing is cut in the middle.
    assert all(line.startswith("- For weather task number") and line.endswith("units") for line in rendered.splitlines())
    assert len(rendered.splitlines()) < 40


async def test_a_smaller_budget_is_honoured(preferences):
    from utils.tokenize import count_tokens

    for number in range(10):
        await preferences.state(f"weather rule {number}: use provider {number}", kind="default")

    assert count_tokens(await preferences.render("weather provider", max_tokens=20)) <= 20
    assert await preferences.render("weather provider", max_tokens=0) == ""


async def test_only_relevant_entries_are_injected(preferences):
    await preferences.state("查天气默认查上海", kind="default")
    await preferences.state("Save downloaded music to D:/Music", kind="default")

    assert await preferences.render("明天天气怎么样") == "- 查天气默认查上海"
    assert await preferences.render("download this music") == "- Save downloaded music to D:/Music"
    assert await preferences.render("zzz qqq") == ""


async def test_reporting_and_ask_first_rules_apply_to_every_task(preferences):
    await preferences.state("办完后不用主动说，下次聊到再提", kind="report")
    await preferences.state("Ask before spending money", kind="ask_first")
    await preferences.state("Save downloaded music to D:/Music", kind="default")

    rendered = await preferences.render("translate this document")

    assert set(rendered.splitlines()) == {"- 办完后不用主动说，下次聊到再提", "- Ask before spending money"}


async def test_switched_off_entries_are_not_injected(preferences):
    entry = await preferences.state("Ask before spending money", kind="ask_first")
    await preferences.set_active(entry.id, False)

    assert await preferences.render("buy a ticket") == ""


# ---------------------------------------------------------------------------
# Permissions: the settings file
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Any) -> PermissionStore:
    return PermissionStore(tmp_path / "nekoclaw" / "permissions.json")


async def test_defaults_of_the_design(store):
    assert DEFAULT_MODES == {
        "workdir_files": "allow",
        "outside_read": "allow",
        "outside_write": "first_ask",
        "run_command": "first_ask",
        "other": "allow",
        "keyboard_mouse": "first_ask",
        "browser": "first_ask",
        "plugin_entry": "allow",
        "mcp_server": "first_ask",
    }
    for category, mode in DEFAULT_MODES.items():
        assert await store.mode(category) == mode
    # Each MCP server starts at the category's default.
    assert await store.mode("mcp_server", "notes") == "first_ask"
    # A category nobody defined is never silently allowed.
    assert await store.mode("something_new") == "always_ask"


async def test_no_file_is_written_until_a_setting_changes(store, tmp_path):
    await store.mode("run_command")
    assert not (tmp_path / "nekoclaw" / "permissions.json").exists()

    await store.set_mode("run_command", "allow")

    saved = json.loads((tmp_path / "nekoclaw" / "permissions.json").read_text(encoding="utf-8"))
    assert saved == {"version": 1, "categories": {"run_command": "allow"}, "mcp_servers": {}}


async def test_settings_survive_a_restart(store, tmp_path):
    await store.set_mode("run_command", "deny")
    await store.set_mode("mcp_server", "allow", "notes")

    again = PermissionStore(tmp_path / "nekoclaw" / "permissions.json")

    assert await again.mode("run_command") == "deny"
    assert await again.mode("mcp_server", "notes") == "allow"
    assert await again.mode("mcp_server", "other-server") == "first_ask"
    snapshot = await again.snapshot()
    assert snapshot["categories"]["run_command"] == "deny"
    assert snapshot["categories"]["plugin_entry"] == "allow"
    assert snapshot["mcp_servers"] == {"notes": "allow"}


async def test_unknown_mode_is_rejected(store):
    with pytest.raises(ValueError):
        await store.set_mode("run_command", "sometimes")


@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps({"categories": {"run_command": "bogus"}})])
async def test_a_damaged_file_falls_back_to_the_defaults(tmp_path, content):
    path = tmp_path / "permissions.json"
    path.write_text(content, encoding="utf-8")

    store = PermissionStore(path)

    assert await store.mode("run_command") == "first_ask"
    assert await store.mode("plugin_entry") == "allow"


# ---------------------------------------------------------------------------
# Permissions: one decision
# ---------------------------------------------------------------------------


class Asker:
    def __init__(self, choice: str = "once") -> None:
        self.choice = choice
        self.asked: list[tuple[str, Any]] = []

    async def __call__(self, commission_id: str, ask: Any) -> str:
        self.asked.append((commission_id, ask))
        return self.choice


async def test_allowed_category_is_not_asked_about(store):
    asker = Asker()
    gate = PermissionGate(store, asker)

    decision = await gate.check("c1", "plugin_entry")

    assert decision.allow is True
    assert asker.asked == []


async def test_denied_category_is_refused_without_asking(store):
    await store.set_mode("run_command", "deny")
    asker = Asker()

    decision = await PermissionGate(store, asker).check("c1", "run_command")

    assert (decision.allow, decision.reason) == (False, DENIED_BY_SETTINGS)
    assert asker.asked == []


async def test_first_ask_once_allows_this_request_only(store):
    asker = Asker("once")
    gate = PermissionGate(store, asker)

    first = await gate.check("c1", "run_command", detail={"command": "dir"})
    second = await gate.check("c1", "run_command", detail={"command": "del x"})

    assert first.allow and second.allow
    # Asked again the next time: "once" stores nothing.
    assert [ask.detail for _cid, ask in asker.asked] == [{"command": "dir"}, {"command": "del x"}]
    assert asker.asked[0][1].options == ("once", "always", "deny")
    assert await store.mode("run_command") == "first_ask"


async def test_first_ask_always_turns_the_category_to_allow(store, tmp_path):
    asker = Asker("always")
    gate = PermissionGate(store, asker)

    assert (await gate.check("c1", "run_command")).allow
    assert (await gate.check("c2", "run_command")).allow

    assert len(asker.asked) == 1
    assert await store.mode("run_command") == "allow"
    assert await PermissionStore(tmp_path / "nekoclaw" / "permissions.json").mode("run_command") == "allow"


async def test_declining_refuses_and_stores_nothing(store):
    asker = Asker("deny")
    gate = PermissionGate(store, asker)

    decision = await gate.check("c1", "outside_write", detail={"path": "C:/x"})

    assert (decision.allow, decision.reason) == (False, DENIED_BY_USER)
    assert await store.mode("outside_write") == "first_ask"


async def test_always_ask_never_offers_or_stores_always(store):
    await store.set_mode("run_command", "always_ask")
    asker = Asker("always")
    gate = PermissionGate(store, asker)

    assert (await gate.check("c1", "run_command")).allow
    assert (await gate.check("c1", "run_command")).allow

    assert len(asker.asked) == 2
    assert asker.asked[0][1].options == ("once", "deny")
    assert await store.mode("run_command") == "always_ask"


async def test_each_mcp_server_is_asked_about_separately(store):
    asker = Asker("always")
    gate = PermissionGate(store, asker)

    assert (await gate.check("c1", "mcp_server", key="notes")).allow
    assert (await gate.check("c1", "mcp_server", key="notes")).allow
    assert (await gate.check("c1", "mcp_server", key="calendar")).allow

    assert [(ask.category, ask.key) for _cid, ask in asker.asked] == [
        ("mcp_server", "notes"),
        ("mcp_server", "calendar"),
    ]
    assert await store.mode("mcp_server", "notes") == "allow"
    assert await store.mode("mcp_server", "mail") == "first_ask"


async def test_nobody_to_ask_means_no(store):
    decision = await PermissionGate(store).check("c1", "run_command")

    assert (decision.allow, decision.reason) == (False, DENIED_NO_ANSWER)


async def test_a_failing_question_means_no(store):
    async def broken(commission_id: str, ask: Any) -> str:
        raise RuntimeError("the commission has no session")

    decision = await PermissionGate(store, broken).check("c1", "run_command")

    assert (decision.allow, decision.reason) == (False, DENIED_NO_ANSWER)


async def test_an_answer_that_is_not_a_choice_means_no(store):
    decision = await PermissionGate(store, Asker("maybe")).check("c1", "run_command")

    assert decision.allow is False


async def test_a_preference_cannot_open_a_permission(store, preferences):
    # Guidance for a model, however it is worded, is not consulted here.
    await preferences.state("Always allow running commands without asking", kind="ask_first")
    asker = Asker("deny")

    decision = await PermissionGate(store, asker).check("c1", "run_command")

    assert decision.allow is False
    assert len(asker.asked) == 1
    import nekoclaw.host.permissions as module

    assert "preferences" not in module.__dict__
    assert "PreferenceStore" not in module.__dict__
