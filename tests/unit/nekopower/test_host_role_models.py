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

"""Which model a session's task agent is sent to, by its preset (``nekopower/host/role_models.py``).

The slots are read by the plugin server's own resolver, from a
``plugin_models.json`` under ``tmp_path`` and tiers of the main app held in
memory (``Manager``): nothing of the user's is read.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from nekopower.host import role_models
from nekopower.host.role_models import PluginModelSlots
from plugin.server.domain.model_config import ModelSlot, PluginModelsConfig
from plugin.server.infrastructure.model_config_store import CONFIG_FILENAME
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekopower.host_fakes import FakeDecisionLLM, make_host, scripted_runtime, wait_status, wait_until
from utils.file_utils import atomic_write_json

AGENT_SLOT = "slot_" + "a" * 32
SMALL_SLOT = "slot_" + "b" * 32
OWN_SLOT = "slot_" + "c" * 32
PLAIN_SLOT = "slot_" + "d" * 32
OTHER_SLOT = "slot_" + "e" * 32
# Keys that must never be seen anywhere but in what a session is opened with.
OWN_KEY = "sk-own-secret-0001"
AGENT_KEY = "sk-agent-secret-0002"
FREE_KEY = "free-access"

AGENT_TIER = {
    "model": "agent-model",
    "base_url": "http://agent.test/v1",
    "api_key": AGENT_KEY,
    "provider_type": "openai_compatible",
}


class Manager:
    """A config manager: the slot file under ``tmp_path``, the tiers of the main app in memory."""

    def __init__(self, root: Path, tiers: dict[str, Any]) -> None:
        self.config_dir = root / "config"
        self.tiers = tiers

    def get_runtime_config_path(self, filename: str) -> Path:
        return self.config_dir / filename

    def save_json_config(self, filename: str, data: Any) -> None:
        raise AssertionError("nothing writes the slots here")

    def get_model_api_config(self, model_type: str) -> dict[str, Any]:
        value = self.tiers.get(model_type, {})
        if isinstance(value, Exception):
            raise value
        return dict(value)


def _follow(host_api: str, **more: Any) -> ModelSlot:
    fields = {
        "name": f"Follows {host_api}",
        "source": "host_follow",
        "host_api": host_api,
        "capabilities": ["text", "streaming", "tool_calling", "image_input"],
    }
    fields.update(more)
    return ModelSlot(**fields)


def _own(**more: Any) -> ModelSlot:
    fields = {
        "name": "Cheap one",
        "protocol": "openai_chat",
        "base_url": "http://own.test/v1",
        "model": "own-model",
        "api_key": OWN_KEY,
        "capabilities": ["text", "tool_calling"],
    }
    fields.update(more)
    return ModelSlot(**fields)


def _write_slots(manager: Manager, slots: dict[str, ModelSlot]) -> None:
    manager.config_dir.mkdir(parents=True, exist_ok=True)
    document = PluginModelsConfig(slots=slots).model_dump(mode="json")
    atomic_write_json(manager.get_runtime_config_path(CONFIG_FILENAME), document)


def _default_slots() -> dict[str, ModelSlot]:
    """What a plugin server seeds (the slot that follows the task agent model), and two more."""
    return {
        AGENT_SLOT: _follow("agent"),
        SMALL_SLOT: _follow("agent_small"),
        OWN_SLOT: _own(),
        PLAIN_SLOT: _own(name="Talks only", capabilities=["text"]),
    }


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _host(hosts: list[Any], tmp_path: Path, pick: Any, manager: Manager | None, **kwargs: Any) -> Any:
    """A host whose task agent model is the agent tier, and whose slots are ``manager``'s (none: no slots)."""
    tier = manager.tiers if manager is not None else {"agent": AGENT_TIER}

    async def agent_model() -> dict[str, Any] | None:
        return role_models.host_model_config(dict(tier.get("agent") or {}))

    kwargs.setdefault("task_agent_model", agent_model)
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    if manager is not None:
        kwargs.setdefault("model_slots", PluginModelSlots(manager))
    host = make_host(tmp_path, None, runtime_factory=scripted_runtime(pick), **kwargs)
    hosts.append(host)
    return host


def _manager(tmp_path: Path, slots: dict[str, ModelSlot] | None = None, **tiers: Any) -> Manager:
    manager = Manager(tmp_path / "app", {"agent": dict(AGENT_TIER), **tiers})
    if slots is not None:
        _write_slots(manager, slots)
    return manager


async def _dispatch(host: Any, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "en")
    return await host.dispatch(text, **kwargs)


def _report(summary: str = "done") -> list[Any]:
    return [call("report", {"status": "ready", "summary": summary}), finish("tool_calls")]


class _Team:
    """A lead that starts one task agent (with ``start``), waits for it and reports."""

    def __init__(self, start: dict[str, Any] | None = None) -> None:
        self.lead = ScriptedLLM(
            [
                [
                    call("subagent", {"description": "look", "prompt": "Look at the tests.", **(start or {})}),
                    finish("tool_calls"),
                ],
                [call("wait_agent", {"timeout_ms": 600_000}, "c2"), finish("tool_calls")],
                _report("looked at"),
            ]
        )
        self.child = ScriptedLLM([[say("They pass."), finish()]])
        self.specs: list[Any] = []

    def pick(self, spec: Any) -> ScriptedLLM:
        self.specs.append(spec)
        return self.lead if spec.top_level else self.child

    @property
    def lead_spec(self) -> Any:
        return next(spec for spec in self.specs if spec.top_level)

    @property
    def child_spec(self) -> Any:
        return next(spec for spec in self.specs if not spec.top_level)


async def _session_of(host: Any, commission_id: str) -> Any:
    commission = await host.ledger.get(commission_id)
    return await host.ledger.get_session(commission.session_id)


def _model(spec: Any) -> tuple[Any, ...]:
    model = spec.model
    return (model.model, model.base_url, model.api_key, model.provider_type)


async def _card(host: Any, session_id: str) -> dict[str, Any]:
    board = await host.board()
    cards = [card for project in board["projects"] for card in project["members"]] + board["loose"]
    return next(card for card in cards if card["id"] == session_id)


# ---------------------------------------------------------------------------
# Out of the box: what every session was sent to before, and the same request
# ---------------------------------------------------------------------------


async def _run_all_kinds(host: Any, team: _Team, single: ScriptedLLM) -> list[Any]:
    """A commission of no project, one of a project whose lead starts a task agent, and a copy of that lead."""
    await host.space.touch_project("Budget app")
    loose = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    lead = await wait_status(host, (await _dispatch(host, "add a chart to the budget app")).commission_id, "done")
    await host.task_agents.settle()
    (sub,) = await host.ledger.select(parent_id=lead.id)
    copy = await host.fork_session(lead.session_id)
    return [
        await host.ledger.get_session(loose.session_id),
        await host.ledger.get_session(lead.session_id),
        await host.ledger.get_session(sub.session_id),
        await host.ledger.get_session(copy.id),
    ]


def _same(requests: Any, root: Path) -> str:
    """Return requests as text with what differs from one host to another left out: its files, ids and markers."""
    import re

    text = json.dumps(requests, ensure_ascii=False)
    for form in (str(root), root.as_posix()):
        text = text.replace(json.dumps(form)[1:-1], "<root>")
    return re.sub(r"[0-9a-f]{32}|[0-9a-f]{16}", "<id>", text)


@pytest.mark.parametrize("slots", ["seeded", "removed", "never"])
async def test_out_of_the_box_every_session_is_sent_where_it_was_with_the_same_requests(hosts, tmp_path, slots):
    """With nothing set, each kind of session gets the model and the requests it got before there were slots.

    ``seeded``: the slot that follows the task agent model is there (a
    plugin server ran). ``removed``: the user removed it. ``never``: no
    plugin server ever wrote the file.
    """

    def scripts() -> tuple[_Team, ScriptedLLM, Any]:
        team = _Team()
        single = ScriptedLLM([_report("sorted")])

        seen: list[Any] = []

        def pick(spec: Any) -> ScriptedLLM:
            # The commission of no project is the first one opened.
            seen.append(spec)
            return single if len(seen) == 1 else team.pick(spec)

        return team, single, pick

    before_team, before_single, before_pick = scripts()
    before = _host(hosts, tmp_path / "before", before_pick, None)
    before_rows = await _run_all_kinds(before, before_team, before_single)

    chosen = {"seeded": _default_slots(), "removed": {OWN_SLOT: _own()}, "never": None}[slots]
    manager = _manager(tmp_path / "now", chosen)
    team, single, pick = scripts()
    now = _host(hosts, tmp_path / "now", pick, manager)
    rows = await _run_all_kinds(now, team, single)

    # The same model, address, key and provider type, for the lead, the task agent it started and the
    # commission of no project; and the same requests, word for word.
    assert _model(team.lead_spec) == _model(before_team.lead_spec) == (
        "agent-model", "http://agent.test/v1", AGENT_KEY, "openai_compatible"
    )
    assert team.child_spec.model == before_team.child_spec.model
    assert team.lead_spec.model == before_team.lead_spec.model
    for now_llm, before_llm in ((team.lead, before_team.lead), (team.child, before_team.child), (single, before_single)):
        assert len(now_llm.requests) == len(before_llm.requests) > 0
        assert _same(now_llm.requests, tmp_path / "now") == _same(before_llm.requests, tmp_path / "before")
        assert now_llm.overrides == before_llm.overrides
    # Each says so on its row: the slot it goes by (none where there is no such slot), the model's
    # name, and that nothing fell back. Without slots, the task agent model itself.
    slot = AGENT_SLOT if slots == "seeded" else ""
    for row in rows:
        assert (row.model_slot, row.model_name, row.model_fallback, row.model_free) == (slot, "agent-model", "", 0)
        assert row.model_ident == role_models.model_ident("http://agent.test/v1", "agent-model")
    for row in before_rows:
        assert (row.model_slot, row.model_name, row.model_fallback) == ("", "agent-model", "")
    # The board names the model as it did.
    assert (await _card(now, rows[1].id))["model"] == (await _card(before, before_rows[1].id))["model"] == "agent-model"
    assert (await _card(now, rows[1].id))["model_fallback"] is None


# ---------------------------------------------------------------------------
# A preset set to a slot
# ---------------------------------------------------------------------------


async def test_a_preset_set_to_a_slot_sends_its_new_sessions_there_and_leaves_the_others_alone(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    specs: list[Any] = []
    llms = [ScriptedLLM([_report("first")]), ScriptedLLM([_report("again")]), ScriptedLLM([_report("second")])]
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or llms[len(specs) - 1], manager)
    await host.space.touch_project("Budget app")

    first = await wait_status(host, (await _dispatch(host, "add a chart to the budget app")).commission_id, "done")
    view = await host.set_role_model("boss", OWN_SLOT)
    assert next(item for item in view["presets"] if item["preset"] == "boss")["setting"] == OWN_SLOT
    # The project's session there is keeps its model; a session of no project made now is not a lead.
    again = await wait_status(host, (await _dispatch(host, "add a legend to the budget app")).commission_id, "done")
    loose = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    assert again.session_id == first.session_id
    assert [spec.model.model for spec in specs] == ["agent-model", "agent-model", "agent-model"]

    # A new project's lead is sent to the slot: its address, its key, the provider type of its protocol.
    await host.space.touch_project("Garden")
    specs.clear()
    llms[:] = [ScriptedLLM([_report("planted")])]
    garden = await wait_status(host, (await _dispatch(host, "plan the garden project")).commission_id, "done")
    assert _model(specs[0]) == ("own-model", "http://own.test/v1", OWN_KEY, "openai_compatible")
    row = await _session_of(host, garden.id)
    assert (row.model_slot, row.model_name, row.model_free, row.model_fallback) == (OWN_SLOT, "own-model", 0, "")
    assert (await _card(host, row.id))["model"] == "own-model"
    # The sessions there were still say what they were settled on.
    assert (await _session_of(host, first.id)).model_name == "agent-model"
    assert (await _session_of(host, loose.id)).model_slot == AGENT_SLOT


async def test_a_session_of_a_preset_set_to_a_slot_runs_in_the_built_in_runtime(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, lambda spec: llm, manager)
    chosen: list[bool] = []
    choose = host.task_agents._choose_runtime

    async def choosing(commission: Any, model: Any, begun_in: Any, *, builtin_only: bool = False) -> Any:
        chosen.append(builtin_only)
        return await choose(commission, model, begun_in, builtin_only=builtin_only)

    host.task_agents._choose_runtime = choosing
    await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    await host.set_role_model("general", OWN_SLOT)
    llm.add(*_report())
    await wait_status(host, (await _dispatch(host, "sort the music")).commission_id, "done")

    # The task agent model may go to dsh as before; a model set for the preset is for the built-in runtime only.
    assert chosen == [False, True]


async def test_a_session_begun_in_dsh_goes_on_there_with_the_task_agent_model(hosts, tmp_path, monkeypatch):
    import nekopower.host.task_agents as task_agents_module

    manager = _manager(tmp_path, _default_slots())
    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, lambda spec: llm, manager)
    await host.set_role_model("general", OWN_SLOT)
    seen: list[tuple[Any, bool]] = []

    async def choosing(commission: Any, model: Any, begun_in: Any, *, builtin_only: bool = False) -> Any:
        seen.append((model.model, builtin_only))
        raise RuntimeError("stop here")

    monkeypatch.setattr(task_agents_module, "_begun_in", lambda place, session_dir: task_agents_module.RUNTIME_DSH)
    host.task_agents._choose_runtime = choosing
    failed = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "failed")

    assert seen == [("agent-model", False)]
    # And on record as what it is sent to: the board and the wake read the same.
    row = await _session_of(host, failed.id)
    assert (row.model_slot, row.model_name) == ("", "agent-model")


async def test_a_task_agent_follows_the_session_that_started_it_unless_its_preset_is_set(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    current = {"team": _Team({"preset": "scout"})}
    host = _host(hosts, tmp_path, lambda spec: current["team"].pick(spec), manager)
    await host.set_role_model("general", OWN_SLOT)

    lead = await wait_status(host, (await _dispatch(host, "look at the tests")).commission_id, "done")

    # The lead is sent to the slot set for it, and the scout it started there too: it follows.
    team = current["team"]
    assert team.lead_spec.model.model == team.child_spec.model.model == "own-model"
    (sub,) = await host.ledger.select(parent_id=lead.id)
    assert (await _session_of(host, sub.id)).model_slot == OWN_SLOT

    # A scout of its own: what is set for scouts, whoever starts it.
    await host.set_role_model("scout", AGENT_SLOT)
    current["team"] = _Team({"preset": "scout"})
    await wait_status(host, (await _dispatch(host, "look at the docs")).commission_id, "done")
    team = current["team"]
    assert (team.lead_spec.model.model, team.child_spec.model.model) == ("own-model", "agent-model")


async def test_a_task_agent_no_session_started_follows_the_lead_of_its_project(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    llm = ScriptedLLM([_report()])
    host = _host(hosts, tmp_path, lambda spec: llm, manager)
    await host.set_role_model("boss", OWN_SLOT)
    await host.space.touch_project("Budget app")
    await wait_status(host, (await _dispatch(host, "add a chart to the budget app")).commission_id, "done")

    # A worker the host opens itself in the project (as a branch will be): the lead's model.
    worker = await host.ledger.create_session(lanlan_name="Lan", name="branch", kind="single", project="Budget app", preset="worker")
    choice = await host.task_agents.session_model(worker)
    assert (choice.slot, choice.name) == (OWN_SLOT, "own-model")
    # One of no project has no lead: the task agent model.
    loose = await host.ledger.create_session(lanlan_name="Lan", name="loose", kind="single", preset="worker")
    assert (await host.task_agents.session_model(loose)).name == "agent-model"


async def test_a_copy_is_sent_where_the_session_it_was_copied_from_is_whatever_is_set_since(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    await host.set_role_model("general", OWN_SLOT)
    done = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    await host.task_agents.settle()
    await host.set_role_model("general", None)

    copy = await host.fork_session(done.session_id)
    again = await host.fork_session(copy.id)

    for row in (copy, again):
        assert (row.model_slot, row.model_name, row.model_tier) == (OWN_SLOT, "own-model", "")
        choice = await host.task_agents.session_model(row)
        assert (choice.slot, choice.name, choice.config["api_key"]) == (OWN_SLOT, "own-model", OWN_KEY)
    # One copied from a session not on record any more: the default of a session of no project.
    orphan = await host.ledger.create_session(lanlan_name="Lan", name="left", kind="fork", forked_from="gone", preset="shadow")
    assert (await host.task_agents.session_model(orphan)).slot == AGENT_SLOT


# ---------------------------------------------------------------------------
# The five ways a slot can fail: the preset's default, with why on the card
# ---------------------------------------------------------------------------


def _break(manager: Manager, how: str) -> None:
    if how == "slot_not_found":
        slots = _default_slots()
        del slots[OWN_SLOT]
        _write_slots(manager, slots)
    elif how == "model_config_unreadable":
        manager.get_runtime_config_path(CONFIG_FILENAME).write_text("{not json", encoding="utf-8")
    elif how == "host_api_not_configured":
        manager.tiers["agent_small"] = {}
    elif how == "host_api_unsupported":
        manager.tiers["agent_small"] = {"model": "small", "base_url": "wss://small.test/realtime", "api_key": "k"}
    elif how == "capability_missing":
        slots = _default_slots()
        slots[OWN_SLOT] = _own(capabilities=["text"])
        _write_slots(manager, slots)


@pytest.mark.parametrize("how", [code for code in role_models.FALLBACK_REASONS if code != "slot_changed"])
async def test_a_slot_that_cannot_be_used_leaves_the_preset_with_its_default_and_says_why(hosts, tmp_path, how):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    llm = ScriptedLLM([_report(), _report()])
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or llm, manager)
    # The small tier for the two ways only a slot that follows a tier fails; the own slot otherwise.
    chosen = SMALL_SLOT if how.startswith("host_api") else OWN_SLOT
    await host.set_role_model("general", chosen)
    _break(manager, how)

    done = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")

    # Worked on all the same, with the default of the preset: the task agent model.
    assert specs[0].model.model == "agent-model"
    row = await _session_of(host, done.id)
    assert (row.model_name, row.model_fallback) == ("agent-model", how)
    card = await _card(host, row.id)
    assert (card["model"], card["model_fallback"]) == ("agent-model", how)
    view = await host.role_models()
    general = next(item for item in view["presets"] if item["preset"] == "general")
    assert (general["setting"], general["in_effect"]["model"], general["in_effect"]["fallback"]) == (chosen, "agent-model", how)


async def test_a_session_whose_slot_cannot_be_used_any_more_goes_on_with_its_presets_default(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    await host.set_role_model("boss", OWN_SLOT)
    await host.space.touch_project("Budget app")
    first = await wait_status(host, (await _dispatch(host, "add a chart to the budget app")).commission_id, "done")
    slots = _default_slots()
    del slots[OWN_SLOT]
    _write_slots(manager, slots)

    again = await wait_status(host, (await _dispatch(host, "add a legend to the budget app")).commission_id, "done")

    assert again.session_id == first.session_id
    assert [spec.model.model for spec in specs] == ["own-model", "agent-model"]
    row = await _session_of(host, again.id)
    assert (row.model_slot, row.model_fallback) == (AGENT_SLOT, "slot_not_found")


async def test_what_was_settled_stays_while_no_model_is_configured_where_it_goes(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report()]), manager)
    await host.set_role_model("general", OWN_SLOT)
    done = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    row = await _session_of(host, done.id)
    await host.set_role_model("general", None)
    manager.tiers["agent"] = {}
    slots = _default_slots()
    del slots[OWN_SLOT]
    _write_slots(manager, slots)

    # Its slot is gone and its default has no model: nothing is written over what it was settled on.
    choice = await host.task_agents.session_model(row)
    assert choice.config is None
    assert (await host.ledger.get_session(row.id)).model_slot == OWN_SLOT


# ---------------------------------------------------------------------------
# What is kept per model is looked up by the session's own
# ---------------------------------------------------------------------------


async def test_the_window_pictures_and_idle_time_are_those_of_the_sessions_own_model(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    store = host.model_capabilities
    await store.set_context_window("http://own.test/v1", "own-model", 40_000)
    await store.set_idle_compact_after("http://own.test/v1", "own-model", 900)
    await store.note_images_refused("http://own.test/v1", "own-model", "no pictures")
    own_window = await store.describe_window("http://own.test/v1", "own-model")
    agent_window = await store.describe_window("http://agent.test/v1", "agent-model")
    assert own_window != agent_window

    agent = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    await host.set_role_model("general", OWN_SLOT)
    own = await wait_status(host, (await _dispatch(host, "sort the music")).commission_id, "done")

    assert (specs[0].model.context_window, specs[0].model.supports_images) != (40_000, False)
    assert (specs[1].model.context_window, specs[1].model.supports_images) == (40_000, False)
    assert (await host.usage(own.id))["window"] == own_window
    assert (await host.usage(agent.id))["window"] == agent_window
    assert await host.task_agents._idle_after(own.session_id) == 900.0
    assert await host.task_agents._idle_after(agent.session_id) != 900.0
    await store.set_idle_compact_on("http://own.test/v1", "own-model", False)
    assert await host.task_agents._idle_compact_wanted(own.session_id) is False
    assert await host.task_agents._idle_compact_wanted(agent.session_id) is True


async def test_an_idle_session_is_compared_with_its_own_model_when_the_time_comes(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report()]), manager)
    await host.set_role_model("general", OWN_SLOT)
    own = await wait_status(host, (await _dispatch(host, "sort the music")).commission_id, "done")
    await host.set_role_model("general", None)
    agent = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    manager_ = host.task_agents
    own_key = await manager_._task_model_key(own.session_id)
    agent_key = await manager_._task_model_key(agent.session_id)
    assert own_key != agent_key

    # The task agent model moves to another provider: the session sent to it is not summed up there;
    # the one sent to its own slot is as it was.
    manager.tiers["agent"] = {**AGENT_TIER, "model": "another-model"}
    assert await manager_._task_model_key(own.session_id) == own_key
    assert await manager_._task_model_key(agent.session_id) != agent_key
    assert await manager_._idle_compact(agent.session_id, model=agent_key) is False


# ---------------------------------------------------------------------------
# The free daily quota: only requests that go with the free key
# ---------------------------------------------------------------------------


async def test_only_sessions_that_go_with_the_free_key_count_against_the_free_quota(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    manager.tiers["agent"] = {**AGENT_TIER, "model": "free-agent-model", "api_key": FREE_KEY}
    counted: list[str] = []

    async def meter(key: str) -> bool:
        counted.append(key)
        return True

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report()]), manager, task_agent_meter=meter)
    free = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    assert counted and set(counted) == {free.id}
    assert (await _session_of(host, free.id)).model_free == 1

    counted.clear()
    await host.set_role_model("general", OWN_SLOT)
    paid = await wait_status(host, (await _dispatch(host, "sort the music")).commission_id, "done")
    assert counted == []
    assert (await _session_of(host, paid.id)).model_free == 0

    # A slot the user filled in with the free key is counted too: it is the key that is free.
    slots = _default_slots()
    slots[OWN_SLOT] = _own(api_key=FREE_KEY)
    _write_slots(manager, slots)
    copied = await wait_status(host, (await _dispatch(host, "sort the videos")).commission_id, "done")
    assert set(counted) == {copied.id}


async def test_a_wake_is_held_for_the_quota_only_for_a_session_that_goes_with_the_free_key(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    manager.tiers["agent"] = {**AGENT_TIER, "api_key": FREE_KEY}
    counted: list[str] = []

    async def meter(key: str) -> bool:
        counted.append(key)
        return False

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report()]), manager, task_agent_meter=meter)
    await host.set_role_model("general", OWN_SLOT)
    paid = await host.ledger.create_session(lanlan_name="Lan", name="paid", kind="single")
    await host.task_agents.settle_model(paid)
    await host.set_role_model("general", None)
    free = await host.ledger.create_session(lanlan_name="Lan", name="free", kind="single")
    await host.task_agents.settle_model(free)

    assert await host.task_agents._may_start(paid.id, session=await host.ledger.get_session(paid.id)) is None
    assert counted == []
    held = await host.task_agents._may_start(free.id, session=await host.ledger.get_session(free.id))
    assert held is not None and held.outcome == "held"
    assert counted == [free.id]


# ---------------------------------------------------------------------------
# Keys stay in memory
# ---------------------------------------------------------------------------


async def test_no_key_is_kept_on_record_shown_logged_or_told_in_an_exception(hosts, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report()]), manager)
    await host.set_role_model("general", OWN_SLOT)
    done = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    await host.task_agents.settle()
    slots = _default_slots()
    slots[OWN_SLOT] = _own(capabilities=["text"])
    _write_slots(manager, slots)
    raised: list[str] = []
    try:
        await host.set_role_model("general", OWN_SLOT)
    except ValueError as exc:
        raised.append(repr(exc))
    try:
        await PluginModelSlots(manager).resolve(OWN_SLOT)
    except role_models.SlotUnavailable as exc:
        raised.append(repr(exc) + str(exc))
    await wait_status(host, (await _dispatch(host, "sort the music")).commission_id, "done")
    choice = await host.task_agents.session_model(await _session_of(host, done.id), record=False)

    shown = json.dumps([await host.board(), await host.role_models()], ensure_ascii=False)
    stored = b"".join(path.read_bytes() for path in (tmp_path / "nekopower").rglob("*") if path.is_file())
    for key in (OWN_KEY, AGENT_KEY):
        assert key not in shown
        assert key.encode() not in stored
        assert key not in caplog.text
        assert all(key not in text for text in raised)
        assert key not in repr(choice)
    assert len(raised) == 2


# ---------------------------------------------------------------------------
# Reading and writing the settings
# ---------------------------------------------------------------------------


async def test_the_settings_are_read_whole_and_written_one_preset_at_a_time(hosts, tmp_path):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": FREE_KEY}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)

    view = await host.role_models()
    by = {item["preset"]: item for item in view["presets"]}
    assert list(by) == ["boss", "worker", "scout", "inspector", "planner", "general"]
    assert {name: (item["setting"], item["default"], item["can_inherit"]) for name, item in by.items()} == {
        "boss": (None, "agent", False),
        "worker": (None, "inherit", True),
        "scout": (None, "inherit", True),
        "inspector": (None, "inherit", True),
        "planner": (None, "inherit", True),
        "general": (None, "agent", False),
    }
    assert by["boss"]["in_effect"] == {
        "follows": None, "slot": AGENT_SLOT, "model": "agent-model", "agent": True, "free": False, "fallback": None,
    }
    assert by["scout"]["in_effect"] == {"follows": "starter", "fallback": None}
    # Only the slots that declare tool calling, in panel order, with no address and no key.
    assert [item["slot_id"] for item in view["slots"]] == [AGENT_SLOT, SMALL_SLOT, OWN_SLOT]
    assert view["slots"][1] == {
        "slot_id": SMALL_SLOT, "name": "Follows agent_small", "source": "host_follow", "host_api": "agent_small",
        "model": "small-model", "configured": True, "free": True, "error_code": None,
    }
    assert view["slots_error"] is None

    by = {item["preset"]: item for item in (await host.set_role_model("scout", SMALL_SLOT))["presets"]}
    assert by["scout"]["setting"] == SMALL_SLOT
    assert by["scout"]["in_effect"] == {
        "follows": None, "slot": SMALL_SLOT, "model": "small-model", "agent": False, "free": True, "fallback": None,
    }
    by = {item["preset"]: item for item in (await host.set_role_model("boss", AGENT_SLOT))["presets"]}
    assert by["boss"]["setting"] == AGENT_SLOT
    by = {item["preset"]: item for item in (await host.set_role_model("scout", "inherit"))["presets"]}
    assert by["scout"]["setting"] == "inherit"
    by = {item["preset"]: item for item in (await host.set_role_model("scout", None))["presets"]}
    assert by["scout"]["setting"] is None
    # Kept in the host's own file, by preset.
    kept = json.loads((tmp_path / "nekopower" / "role_models.json").read_text(encoding="utf-8"))
    assert kept == {"version": 1, "roles": {"boss": AGENT_SLOT}}


@pytest.mark.parametrize(
    ("preset", "value", "code"),
    [
        ("shadow", None, "unknown_preset"),
        ("nobody", OWN_SLOT, "unknown_preset"),
        ("boss", "inherit", "cannot_inherit"),
        ("general", "inherit", "cannot_inherit"),
        ("scout", "", "bad_value"),
        ("scout", 3, "bad_value"),
        ("scout", OTHER_SLOT, "slot_not_found"),
        ("scout", PLAIN_SLOT, "capability_missing"),
    ],
)
async def test_a_setting_that_will_not_do_is_refused_and_nothing_is_written(hosts, tmp_path, preset, value, code):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)

    with pytest.raises(ValueError) as raised:
        await host.set_role_model(preset, value)

    assert str(raised.value) == code
    assert not (tmp_path / "nekopower" / "role_models.json").exists()


async def test_slots_that_cannot_be_read_are_said_so_and_none_can_be_set(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    manager.get_runtime_config_path(CONFIG_FILENAME).write_text("{not json", encoding="utf-8")
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)

    view = await host.role_models()
    assert (view["slots"], view["slots_error"]) == ([], "model_config_unreadable")
    # Every preset is sent to the task agent model meanwhile, and says why.
    boss = next(item for item in view["presets"] if item["preset"] == "boss")
    assert (boss["in_effect"]["model"], boss["in_effect"]["fallback"]) == ("agent-model", "model_config_unreadable")
    with pytest.raises(ValueError, match="model_config_unreadable"):
        await host.set_role_model("scout", OWN_SLOT)


async def test_without_slots_every_preset_is_sent_to_the_task_agent_model_and_none_can_be_set(hosts, tmp_path):
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), None)

    view = await host.role_models()
    assert (view["slots"], view["slots_error"]) == ([], None)
    assert next(item for item in view["presets"] if item["preset"] == "general")["in_effect"]["model"] == "agent-model"
    with pytest.raises(ValueError, match="no_slots"):
        await host.set_role_model("scout", OWN_SLOT)
    # Following the session that starts it needs no slot.
    await host.set_role_model("scout", "inherit")


async def test_a_template_of_the_users_has_a_setting_and_goes_by_its_model_by_default(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    templates = tmp_path / "nekopower" / "task_agent_space" / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "tester.md").write_text(
        "---\nname: tester\ndescription: Runs the tests.\nmodel: agent\n---\nRun the tests.\n", encoding="utf-8"
    )

    by = {item["preset"]: item for item in (await host.role_models())["presets"]}
    assert (by["tester"]["default"], by["tester"]["can_inherit"]) == ("agent", True)
    assert by["tester"]["in_effect"]["slot"] == AGENT_SLOT
    await host.set_role_model("tester", OWN_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="t", kind="single", preset="tester")
    assert (await host.task_agents.session_model(session)).slot == OWN_SLOT


async def test_settings_that_cannot_be_read_are_read_as_none(tmp_path):
    path = tmp_path / "role_models.json"
    store = role_models.RoleModelStore(path)
    path.write_text("{not json", encoding="utf-8")
    assert await store.all() == {}
    path.write_text(json.dumps({"roles": {"boss": "https://x", "scout": "inherit", "worker": 3, "planner": OWN_SLOT}}))
    assert await store.all() == {"scout": "inherit", "planner": OWN_SLOT}


# ---------------------------------------------------------------------------
# Settled once, looked at many times
# ---------------------------------------------------------------------------


async def test_a_session_whose_slot_breaks_goes_to_the_default_and_not_to_what_is_set_since(hosts, tmp_path):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    await host.set_role_model("boss", OWN_SLOT)
    await host.space.touch_project("Budget app")
    first = await wait_status(host, (await _dispatch(host, "add a chart to the budget app")).commission_id, "done")
    await host.set_role_model("boss", SMALL_SLOT)
    slots = _default_slots()
    del slots[OWN_SLOT]
    _write_slots(manager, slots)

    await wait_status(host, (await _dispatch(host, "add a legend to the budget app")).commission_id, "done")
    await wait_status(host, (await _dispatch(host, "add a title to the budget app")).commission_id, "done")

    # The default of a lead, not the small model set for leads made from now on; and why stays said.
    assert [spec.model.model for spec in specs] == ["own-model", "agent-model", "agent-model"]
    row = await _session_of(host, first.id)
    assert (row.model_slot, row.model_fallback) == (AGENT_SLOT, "slot_not_found")
    assert (await _card(host, row.id))["model_fallback"] == "slot_not_found"


async def test_looking_at_a_sessions_model_writes_nothing_and_settling_it_twice_reads_once(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    slots = PluginModelSlots(manager)
    resolved: list[str] = []
    resolve = slots.resolve

    async def counting(slot_id: str) -> Any:
        resolved.append(slot_id)
        return await resolve(slot_id)

    slots.resolve = counting
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager, model_slots=slots)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")

    looked = await host.task_agents.session_model(session, record=False)
    assert looked.slot == AGENT_SLOT
    assert (await host.ledger.get_session(session.id)).model_ident == ""
    assert await host.task_agents.available(session) == (True, "")
    assert (await host.ledger.get_session(session.id)).model_ident == ""

    await host.task_agents.settle_model(session)
    settled = await host.ledger.get_session(session.id)
    assert settled.model_slot == AGENT_SLOT
    resolved.clear()
    await host.task_agents.settle_model(settled)
    assert resolved == []


async def test_a_session_that_cannot_be_settled_as_it_is_made_is_settled_when_it_is_opened(
    hosts, tmp_path, monkeypatch
):
    manager = _manager(tmp_path, _default_slots())
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    await host.set_role_model("boss", OWN_SLOT)
    update = host.ledger.update_session
    failed: list[str] = []

    async def failing_once(session_id: str, **values: Any) -> Any:
        if "model_ident" in values and not failed:
            failed.append(session_id)
            raise RuntimeError("the ledger is busy")
        return await update(session_id, **values)

    monkeypatch.setattr(host.ledger, "update_session", failing_once)
    made = await host.open_session("Lan", "Garden", preset="boss")
    assert failed == [made["session_id"]]
    assert (await host.ledger.get_session(made["session_id"])).model_ident == ""

    done = await wait_status(host, (await _dispatch(host, "plan the garden project")).commission_id, "done")

    assert done.session_id == made["session_id"]
    assert specs[0].model.model == "own-model"
    assert (await _session_of(host, done.id)).model_slot == OWN_SLOT


async def test_a_model_that_cannot_be_written_down_does_not_keep_a_session_from_starting(
    hosts, tmp_path, monkeypatch
):
    manager = _manager(tmp_path, _default_slots())
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    await host.set_role_model("general", OWN_SLOT)
    update = host.ledger.update_session

    async def failing(session_id: str, **values: Any) -> Any:
        if "model_ident" in values:
            raise RuntimeError("the ledger is busy")
        return await update(session_id, **values)

    monkeypatch.setattr(host.ledger, "update_session", failing)
    done = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")

    assert specs[0].model.model == "own-model"
    assert (await _session_of(host, done.id)).model_ident == ""


async def test_a_project_the_user_opens_has_its_lead_settled_as_it_is_made(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.set_role_model("boss", OWN_SLOT)

    made = await host.open_session("Lan", "Garden", preset="boss")

    row = await host.ledger.get_session(made["session_id"])
    assert (row.model_slot, row.model_name) == (OWN_SLOT, "own-model")


async def test_sessions_that_start_each_other_round_and_round_end_at_the_task_agent_model(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    one = await host.ledger.create_session(lanlan_name="Lan", name="one", kind="child", preset="worker", parent_id="two")
    await host.ledger.create_session(id="two", lanlan_name="Lan", name="two", kind="child", preset="worker", parent_id=one.id)

    choice = await host.task_agents.session_model(one, record=False)

    assert (choice.slot, choice.name) == (AGENT_SLOT, "agent-model")


# ---------------------------------------------------------------------------
# Whether a task agent can be started at all
# ---------------------------------------------------------------------------


async def test_whether_a_task_agent_can_be_started_is_told_by_the_model_it_would_be_sent_to(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.set_role_model("general", OWN_SLOT)
    own = await host.ledger.create_session(lanlan_name="Lan", name="own", kind="single")
    await host.task_agents.settle_model(own)
    await host.set_role_model("general", None)
    agent = await host.ledger.create_session(lanlan_name="Lan", name="agent", kind="single")
    await host.task_agents.settle_model(agent)
    manager.tiers["agent"] = {}

    # No task agent model: a new session of hers cannot start, nor can one sent to it; one sent to its own slot can.
    assert await host.task_agents.available() == (False, "not_enabled")
    assert await host.task_agents.available(await host.ledger.get_session(agent.id)) == (False, "not_enabled")
    assert await host.task_agents.available(await host.ledger.get_session(own.id)) == (True, "")
    # A new one of hers can once her preset is set to a slot that has a model.
    await host.set_role_model("general", OWN_SLOT)
    assert await host.task_agents.available() == (True, "")
    assert (await host.task_agents.model_of_commission("no such commission"))["model"] == "own-model"


async def _yes(session: Any = None) -> tuple[bool, str]:
    return True, ""


async def test_whether_a_wake_is_counted_is_told_for_a_session_not_settled_yet_and_when_that_cannot_be_told(
    hosts, tmp_path, monkeypatch
):
    manager = _manager(tmp_path, _default_slots())
    manager.tiers["agent"] = {**AGENT_TIER, "api_key": FREE_KEY}
    counted: list[str] = []

    async def meter(key: str) -> bool:
        counted.append(key)
        return True

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager, task_agent_meter=meter)
    loose = await host.ledger.create_session(lanlan_name="Lan", name="loose", kind="single")
    await host.set_role_model("general", OWN_SLOT)
    paid = await host.ledger.create_session(lanlan_name="Lan", name="paid", kind="single")

    # Not settled yet: by what it would be settled on.
    assert await host.task_agents._may_start(paid.id, session=paid) is None
    assert counted == []
    await host.set_role_model("general", None)
    assert await host.task_agents._may_start(loose.id, session=loose) is None
    assert counted == [loose.id]

    # Cannot be told: counted, as before there were settings.
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("cannot tell")

    other = await host.ledger.create_session(lanlan_name="Lan", name="other", kind="single")
    monkeypatch.setattr(host.task_agents, "available", _yes)
    monkeypatch.setattr(host.task_agents, "session_model", broken)
    assert await host.task_agents._may_start("c-other", session=other) is None
    assert counted == [loose.id, "c-other"]


async def test_an_idle_session_waits_under_its_own_model_and_not_the_task_agent_models(
    hosts, tmp_path, caplog, monkeypatch
):
    from config import nekopower_settings as settings

    # Switched on, with a wait long enough that no timer set as a commission ends comes due here.
    monkeypatch.setattr(settings, "TASK_AGENT_IDLE_COMPACT_ENABLED", True)
    monkeypatch.setattr(settings, "TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS", 3600.0)
    caplog.set_level(logging.INFO, logger="nekopower.host.task_agents")
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report()]), manager)
    await host.set_role_model("general", OWN_SLOT)
    own = await wait_status(host, (await _dispatch(host, "sort the music")).commission_id, "done")
    await host.set_role_model("general", None)
    agent = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    await host.task_agents.settle()
    tasks = host.task_agents
    await wait_until(lambda: tasks._held_by == {}, what="every session being let go")
    own_key = await tasks._task_model_key(own.session_id)
    agent_key = await tasks._task_model_key(agent.session_id)
    manager.tiers["agent"] = {**AGENT_TIER, "model": "another-model"}
    tasks._drop_idle_timer(own.session_id)
    tasks._drop_idle_timer(agent.session_id)

    caplog.clear()
    await tasks._idle_compact(own.session_id, after=3600.0, model=own_key)
    assert "changed while it waited" not in caplog.text
    await tasks._idle_compact(agent.session_id, after=3600.0, model=agent_key)
    assert f"the model of session {agent.session_id} changed while it waited" in caplog.text


async def test_a_slot_that_speaks_the_messages_api_is_opened_as_such(hosts, tmp_path):
    slots = _default_slots()
    slots[OWN_SLOT] = _own(protocol="anthropic_messages", base_url="http://claude.test")
    manager = _manager(tmp_path, slots)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.set_role_model("scout", OWN_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single", preset="scout")

    choice = await host.task_agents.session_model(session)

    assert choice.config["provider_type"] == "anthropic"
    assert (await host.task_agents.session_model(await host.ledger.get_session(session.id))).config == choice.config


# ---------------------------------------------------------------------------
# Defaults and fallbacks in the corners
# ---------------------------------------------------------------------------


def test_a_tier_of_the_main_app_without_an_address_names_no_model():
    assert role_models.host_model_config({"model": "m", "api_key": "k"}) is None
    assert role_models.host_model_config({"base_url": "http://x.test/v1", "api_key": "k"}) is None
    assert role_models.host_model_config(None) is None
    assert role_models.host_model_config({"model": "m", "base_url": "http://x.test/v1"}) == {
        "model": "m", "base_url": "http://x.test/v1", "api_key": None, "provider_type": None,
    }


async def test_a_slot_that_follows_the_small_model_is_not_taken_for_the_one_that_follows_the_agent_model(
    hosts, tmp_path
):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, {SMALL_SLOT: _follow("agent_small"), OWN_SLOT: _own()}, agent_small=small)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")

    choice = await host.task_agents.session_model(session)

    # No slot follows the task agent model: that model itself, and nothing fell back.
    assert (choice.slot, choice.name, choice.fallback) == ("", "agent-model", "")


async def test_a_slot_that_follows_the_agent_model_and_cannot_be_used_leaves_the_model_itself_and_says_why(
    hosts, tmp_path
):
    slots = _default_slots()
    slots[AGENT_SLOT] = _follow("agent", capabilities=["text", "streaming"])
    manager = _manager(tmp_path, slots)
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)

    done = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")

    assert specs[0].model.model == "agent-model"
    row = await _session_of(host, done.id)
    assert (row.model_slot, row.model_fallback) == ("", "capability_missing")


async def test_why_a_task_agent_fell_back_is_its_own_and_not_the_one_of_the_session_that_started_it(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    current = {"team": _Team({"preset": "scout"})}
    host = _host(hosts, tmp_path, lambda spec: current["team"].pick(spec), manager)
    # The lead's slot is gone: it falls back. Its scout follows it, and nothing of its own fell back.
    await host.set_role_model("general", OWN_SLOT)
    slots = _default_slots()
    del slots[OWN_SLOT]
    _write_slots(manager, slots)
    lead = await wait_status(host, (await _dispatch(host, "look at the tests")).commission_id, "done")
    (sub,) = await host.ledger.select(parent_id=lead.id)
    assert (await _session_of(host, lead.id)).model_fallback == "slot_not_found"
    assert (await _session_of(host, sub.id)).model_fallback == ""

    # A scout set to a slot that is gone: it follows its lead, and says why.
    _write_slots(manager, _default_slots())
    # (Refused if it is set now: as if the slot lost the capability after it was set.)
    await host.task_agents.role_models.store.put("scout", PLAIN_SLOT)
    current["team"] = _Team({"preset": "scout"})
    lead = await wait_status(host, (await _dispatch(host, "look at the docs")).commission_id, "done")
    (sub,) = await host.ledger.select(parent_id=lead.id)
    row = await _session_of(host, sub.id)
    assert (row.model_slot, row.model_fallback) == (OWN_SLOT, "capability_missing")


async def test_a_slot_set_by_hand_where_there_are_no_slots_leaves_the_preset_with_its_default(hosts, tmp_path):
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), None)
    (tmp_path / "nekopower").mkdir(parents=True, exist_ok=True)
    (tmp_path / "nekopower" / "role_models.json").write_text(
        json.dumps({"version": 1, "roles": {"general": OWN_SLOT}}), encoding="utf-8"
    )
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")

    choice = await host.task_agents.session_model(session)

    assert (choice.slot, choice.name, choice.fallback) == ("", "agent-model", "model_config_unreadable")


async def test_a_copy_not_settled_yet_is_sent_where_the_session_it_was_copied_from_is(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.set_role_model("general", OWN_SLOT)
    source = await host.ledger.create_session(lanlan_name="Lan", name="source", kind="single")
    await host.task_agents.settle_model(source)
    await host.set_role_model("general", None)
    copy = await host.ledger.create_session(
        lanlan_name="Lan", name="copy", kind="fork", forked_from=source.id, preset="shadow"
    )

    choice = await host.task_agents.session_model(copy)

    assert (choice.slot, choice.name) == (OWN_SLOT, "own-model")
    assert (await host.ledger.get_session(copy.id)).model_slot == OWN_SLOT


async def test_a_project_is_opened_when_its_leads_model_cannot_be_read(hosts, tmp_path):
    async def unreadable() -> dict[str, Any]:
        raise OSError("the configuration is being written")

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), None, task_agent_model=unreadable)

    made = await host.open_session("Lan", "Garden", preset="boss")

    assert (await host.ledger.get_session(made["session_id"])).model_ident == ""


async def test_a_wake_of_a_session_with_a_model_of_its_own_is_not_held_for_the_task_agent_model(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.set_role_model("general", OWN_SLOT)
    own = await host.ledger.create_session(lanlan_name="Lan", name="own", kind="single")
    await host.task_agents.settle_model(own)
    await host.set_role_model("general", None)
    manager.tiers["agent"] = {}

    # A new session would have no model; this one has its own.
    assert await host.task_agents.available() == (False, "not_enabled")
    assert await host.task_agents._may_start(own.id, session=await host.ledger.get_session(own.id)) is None


async def test_only_a_request_that_goes_with_the_free_key_is_charged(hosts, tmp_path):
    counted: list[str] = []

    async def meter(key: str) -> bool:
        counted.append(key)
        return False

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), None, task_agent_meter=meter)

    assert await host.task_agents._charge(False, "paid") is True
    assert await host.task_agents._charge(True, "free") is False
    assert counted == ["free"]


async def test_a_preset_whose_template_cannot_be_used_says_its_default_as_the_built_in_one_does(hosts, tmp_path):
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), None)
    templates = tmp_path / "nekopower" / "task_agent_space" / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "boss.md").write_text("---\nname: boss\nnope: 1\n---\nWrong.\n", encoding="utf-8")
    (templates / "helper.md").write_text("---\nname: helper\nnope: 1\n---\nWrong.\n", encoding="utf-8")

    assert await host.task_agents.model_default_of("boss", None) == "agent"
    assert await host.task_agents.model_default_of("helper", None) == "inherit"


# ---------------------------------------------------------------------------
# Review round 1: the gates that ask about a session ask about its own model
# ---------------------------------------------------------------------------


async def _session_of_hers(host: Any, title: str, name: str) -> Any:
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", route="task_agent"
    )
    _commission, session = await host.ledger.give_session(commission.id, kind="single", name=name)
    await host.task_agents.settle_model(session)
    return await host.ledger.get_session(session.id)


async def test_a_lead_with_a_model_of_its_own_can_be_handed_work_without_the_task_agent_model(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    manager.tiers["agent"] = {}
    assert await host.task_agents.available() == (False, "not_enabled")

    # Leads of projects go to a slot of their own: what she hands over can be worked on.
    await host.set_role_model("boss", OWN_SLOT)

    assert await host.task_agents.available() == (True, "")


async def test_a_note_or_a_question_goes_to_a_session_only_when_that_session_has_a_model(hosts, tmp_path):
    from config.prompts import prompts_nekopower_sessions as words

    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.set_role_model("general", OWN_SLOT)
    writer = await _session_of_hers(host, "write", "writer")
    reader = await _session_of_hers(host, "read", "reader")
    await host.set_role_model("general", None)
    plain = await _session_of_hers(host, "plain", "plain")
    manager.tiers["agent"] = {}
    tasks = host.task_agents

    async def refusal(call: Any) -> str:
        try:
            await call
        except RuntimeError as exc:
            return str(exc)
        return ""

    # To one sent to the task agent model, which has none now: nobody would work on it.
    said = await refusal(tasks._cb_session_send(writer.id, {"to": "plain", "message": "hi"}))
    assert said == words.MESSAGE_REFUSALS["switched_off"]
    asked = await refusal(tasks._cb_session_ask(writer.id, {"session": "plain", "question": "Which photos?"}))
    assert asked == words.ASK_REFUSALS["switched_off"]
    # To one with a model of its own: not refused for that.
    said = await refusal(tasks._cb_session_send(writer.id, {"to": "reader", "message": "hi"}))
    assert said != words.MESSAGE_REFUSALS["switched_off"]
    asked = await refusal(tasks._cb_session_ask(writer.id, {"session": "reader", "question": "Which photos?"}))
    assert asked != words.ASK_REFUSALS["switched_off"]


async def test_a_wake_is_counted_by_the_key_its_session_goes_with_now(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    manager.tiers["agent"] = {**AGENT_TIER, "api_key": FREE_KEY}
    counted: list[str] = []

    async def meter(key: str) -> bool:
        counted.append(key)
        return False

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager, task_agent_meter=meter)
    await host.set_role_model("general", OWN_SLOT)
    slots = _default_slots()
    slots[OWN_SLOT] = _own(api_key=FREE_KEY)
    _write_slots(manager, slots)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    await host.task_agents.settle_model(session)
    assert (await host.ledger.get_session(session.id)).model_free == 1
    # The user puts a key of their own in the slot after it last ran: the wake waits for no quota.
    _write_slots(manager, _default_slots())

    assert await host.task_agents._may_start(session.id, session=await host.ledger.get_session(session.id)) is None
    assert counted == []


async def test_a_slot_that_does_not_declare_pictures_is_opened_without_them(hosts, tmp_path):
    slots = _default_slots()
    slots[OTHER_SLOT] = _own(name="Sees", capabilities=["text", "tool_calling", "image_input"])
    manager = _manager(tmp_path, slots)
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    await host.set_role_model("general", OWN_SLOT)
    await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    await host.set_role_model("general", OTHER_SLOT)
    await wait_status(host, (await _dispatch(host, "sort the music")).commission_id, "done")

    assert [spec.model.supports_images for spec in specs] == [False, True]


async def test_a_settled_session_does_not_follow_its_slot_to_another_address_unless_it_follows_the_main_app(
    hosts, tmp_path
):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    tasks = host.task_agents
    await host.set_role_model("general", OWN_SLOT)
    own = await host.ledger.create_session(lanlan_name="Lan", name="own", kind="single")
    await tasks.settle_model(own)
    await host.set_role_model("general", SMALL_SLOT)
    small_one = await host.ledger.create_session(lanlan_name="Lan", name="small", kind="single")
    await tasks.settle_model(small_one)
    await host.set_role_model("general", None)

    # A new key at the same address and name: taken.
    slots = _default_slots()
    slots[OWN_SLOT] = _own(api_key="sk-own-rotated")
    _write_slots(manager, slots)
    choice = await tasks.session_model(await host.ledger.get_session(own.id))
    assert (choice.slot, choice.config["api_key"], choice.fallback) == (OWN_SLOT, "sk-own-rotated", "")

    # The small tier of the main app moves to another provider: followed, as the main app always was.
    manager.tiers["agent_small"] = {**small, "base_url": "http://elsewhere.test/v1"}
    choice = await tasks.session_model(await host.ledger.get_session(small_one.id))
    assert (choice.slot, choice.config["base_url"], choice.fallback) == (SMALL_SLOT, "http://elsewhere.test/v1", "")

    # The slot itself is pointed somewhere else: what was said in the session does not go there.
    slots[OWN_SLOT] = _own(base_url="http://somewhere-else.test/v1")
    _write_slots(manager, slots)
    choice = await tasks.session_model(await host.ledger.get_session(own.id))
    assert (choice.slot, choice.name, choice.fallback) == (AGENT_SLOT, "agent-model", "slot_changed")
    row = await host.ledger.get_session(own.id)
    assert (row.model_slot, row.model_fallback) == (AGENT_SLOT, "slot_changed")
    assert (await _card(host, own.id))["model_fallback"] == "slot_changed"


async def test_settings_that_cannot_be_read_are_not_written_over(tmp_path):
    path = tmp_path / "role_models.json"
    store = role_models.RoleModelStore(path)
    path.write_text("{half written", encoding="utf-8")

    with pytest.raises(OSError):
        await store.put("scout", OWN_SLOT)

    # What was there stays, for the user to see to; meanwhile every preset goes by its default.
    assert path.read_text(encoding="utf-8") == "{half written"
    assert await store.all() == {}


async def test_a_setting_being_written_is_written_whole_when_whoever_asked_gives_up(tmp_path):
    import asyncio

    path = tmp_path / "role_models.json"
    store = role_models.RoleModelStore(path)
    first = asyncio.ensure_future(store.put("boss", OWN_SLOT))
    await asyncio.sleep(0)
    first.cancel()
    await store.put("scout", "inherit")

    assert await store.all() == {"boss": OWN_SLOT, "scout": "inherit"}


@pytest.mark.parametrize("rewritten", ["own slot made to follow a tier", "agent slot made to follow another tier"])
async def test_a_slot_made_to_follow_another_tier_since_is_not_followed_there(hosts, tmp_path, rewritten):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    tasks = host.task_agents
    chosen = OWN_SLOT if rewritten.startswith("own") else AGENT_SLOT
    await host.set_role_model("general", chosen)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    await tasks.settle_model(session)
    settled = await host.ledger.get_session(session.id)
    assert settled.model_tier == ("" if chosen == OWN_SLOT else "agent")
    await host.set_role_model("general", None)

    slots = _default_slots()
    slots[chosen] = _follow("agent_small")
    _write_slots(manager, slots)
    choice = await tasks.session_model(settled)

    # The conversation stays away from a tier nobody chose for it: the preset's default, and why.
    assert choice.name != "small-model"
    assert choice.fallback == "slot_changed"
