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
from tests.unit.nekopower.host_fakes import (
    FakeDecisionLLM,
    let_go,
    make_host,
    scripted_runtime,
    wait_status,
    wait_until,
)
from utils.file_utils import atomic_write_json

AGENT_SLOT = "slot_" + "a" * 32
SMALL_SLOT = "slot_" + "b" * 32
OWN_SLOT = "slot_" + "c" * 32
PLAIN_SLOT = "slot_" + "d" * 32
OTHER_SLOT = "slot_" + "e" * 32
ADVANCED_SLOT = "slot_" + "f" * 32
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
    """Return requests as text with what differs from one host to another left out.

    That is its files, its ids and markers, and the minute its sessions read
    off the wall clock: the two hosts run one after the other, so a minute
    boundary between them would otherwise show up as a difference.
    """
    import re

    text = json.dumps(requests, ensure_ascii=False)
    for form in (str(root), root.as_posix()):
        text = text.replace(json.dumps(form)[1:-1], "<root>")
    text = re.sub(r"[0-9a-f]{32}|[0-9a-f]{16}", "<id>", text)
    # The runtime context notice: "2026-11-23 21:23 +0000 (Monday)".
    text, stamps = re.subn(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} [+-][0-9]{4} [(][^)]+[)]", "<now>", text
    )
    assert stamps, "no runtime context time in the requests; the pattern above no longer matches"
    return text


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
    await host.derive_leader("Budget app", "Lan")

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
    await host.derive_leader("Garden", "Lan")
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
    from nekopower.host import task_agents as task_agents_module

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
    await host.derive_leader("Budget app", "Lan")
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
    await host.derive_leader("Budget app", "Lan")
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
    # A commission is "done" on the board before its session has written its
    # last entries. Shut the host down before reading what it stored: then all
    # of it is on disk, and no session still holds a transcript lock (reading
    # a locked file fails on Windows).
    await host.shutdown()
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
        "planner": (None, "advanced", True),
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
    await host.derive_leader("Budget app", "Lan")
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
    update = host.ledger.keep_model
    failed: list[str] = []

    async def failing_once(session_id: str, preset: str, **values: Any) -> Any:
        if "model_ident" in values and not failed:
            failed.append(session_id)
            raise RuntimeError("the ledger is busy")
        return await update(session_id, preset, **values)

    monkeypatch.setattr(host.ledger, "keep_model", failing_once)
    made = await host.open_session("Lan", "Garden", preset="boss")
    assert failed == [made["session_id"]]
    assert (await host.ledger.get_session(made["session_id"])).model_ident == ""

    done = await wait_status(host, (await _dispatch(host, "plan the garden project")).commission_id, "done")

    assert done.session_id == made["session_id"]
    assert specs[0].model.model == "own-model"
    assert (await _session_of(host, done.id)).model_slot == OWN_SLOT


async def test_a_session_whose_model_cannot_be_written_down_is_not_opened(hosts, tmp_path, monkeypatch):
    manager = _manager(tmp_path, _default_slots())
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    await host.set_role_model("general", OWN_SLOT)
    update = host.ledger.keep_model

    async def failing(session_id: str, preset: str, **values: Any) -> Any:
        if "model_ident" in values:
            raise RuntimeError("the ledger is busy")
        return await update(session_id, preset, **values)

    monkeypatch.setattr(host.ledger, "keep_model", failing)
    failed = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "failed")

    # Opened now and on no record, it could be opened by another model the next time, conversation and all.
    assert specs == []
    assert "the ledger is busy" in failed.status_reason
    assert (await _session_of(host, failed.id)).model_ident == ""


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

    # No task agent model: one sent to it cannot start, one sent to its own slot can; and she may go on
    # with that one, so task agents are there for her.
    assert await host.task_agents.available() == (True, "")
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
    await let_go(host)
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


async def test_a_wake_is_held_or_not_by_the_model_of_the_session_it_wakes(hosts, tmp_path):
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
    manager.tiers["agent"] = {}

    # A session with a model of its own is woken, whatever a new one would have.
    assert await tasks._may_start(own.id, session=await host.ledger.get_session(own.id)) is None
    # One whose slot is gone, and whose default has no model, is not, though another session has one.
    slots = _default_slots()
    del slots[OWN_SLOT]
    _write_slots(manager, slots)
    assert await tasks.available() == (True, "")
    held = await tasks._may_start(own.id, session=await host.ledger.get_session(own.id))
    assert held is not None and held.outcome == "held"


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
    await _session_of_hers(host, "plain", "plain")
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
    # To one with a model of its own: the note is written down for it; a question gets as far as asking
    # whether the other session is at work on something (it is not).
    await host.ledger.transition(writer.id, "working")
    said = await refusal(tasks._cb_session_send(writer.id, {"to": "reader", "message": "hi"}))
    asked = await refusal(tasks._cb_session_ask(writer.id, {"session": "reader", "question": "Which photos?"}))
    assert said == ""
    assert [wake.session_id for wake in await host.ledger.wakes_pending(session_id=reader.id)] == [reader.id]
    # (Past the gates: refused only as no copy of a session with nothing in it can be made.)
    assert asked.startswith('Not asked: no copy of "reader" could be made')


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


async def test_a_slot_that_cannot_be_read_just_now_is_tried_again_the_next_time(hosts, tmp_path):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    tasks = host.task_agents
    await host.set_role_model("general", SMALL_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    await tasks.settle_model(session)
    await host.set_role_model("general", None)

    # The small tier cannot be read for a moment: the default this time, and nothing written over.
    manager.tiers["agent_small"] = RuntimeError("being saved")
    choice = await tasks.session_model(await host.ledger.get_session(session.id))
    assert (choice.name, choice.fallback) == ("agent-model", "host_api_not_configured")
    row = await host.ledger.get_session(session.id)
    assert (row.model_slot, row.model_name, row.model_fallback) == (SMALL_SLOT, "small-model", "")

    # Readable again: where it was settled.
    manager.tiers["agent_small"] = small
    choice = await tasks.session_model(await host.ledger.get_session(session.id))
    assert (choice.slot, choice.name) == (SMALL_SLOT, "small-model")


async def test_a_session_settled_before_goes_on_when_its_model_cannot_be_written_again(hosts, tmp_path, monkeypatch):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    specs: list[Any] = []
    host = _host(hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([_report()]), manager)
    await host.set_role_model("boss", SMALL_SLOT)
    await host.space.touch_project("Budget app")
    await host.derive_leader("Budget app", "Lan")
    await wait_status(host, (await _dispatch(host, "add a chart to the budget app")).commission_id, "done")
    manager.tiers["agent_small"] = {**small, "model": "small-model-2"}
    update = host.ledger.keep_model

    async def failing(session_id: str, preset: str, **values: Any) -> Any:
        if "model_ident" in values:
            raise RuntimeError("the ledger is busy")
        return await update(session_id, preset, **values)

    monkeypatch.setattr(host.ledger, "keep_model", failing)
    await wait_status(host, (await _dispatch(host, "add a legend to the budget app")).commission_id, "done")

    assert [spec.model.model for spec in specs] == ["small-model", "small-model-2"]


async def test_a_commission_that_waits_for_a_place_has_its_session_settled_as_it_waits(hosts, tmp_path):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    specs: list[Any] = []
    scripts = [ScriptedLLM([[wait, *_report()]]), ScriptedLLM([_report()])]
    host = _host(
        hosts, tmp_path, lambda spec: specs.append(spec) or scripts[len(specs) - 1], manager, max_task_agents=1
    )
    await host.set_role_model("general", OWN_SLOT)
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    second = (await _dispatch(host, "sort the music")).commission_id
    # In the queue, which is after it was settled (a commission is "queued" on record from when it is filed).
    await wait_until(lambda: second in host.task_agents._queue, what="the second commission being queued")
    # The user changes the setting while it waits: it was settled as it was queued.
    await host.set_role_model("general", None)
    release.set()
    await wait_status(host, second, "done")

    assert [spec.model.model for spec in specs] == ["own-model", "own-model"]


async def test_what_a_commission_at_work_used_is_told_against_the_window_of_the_model_it_runs_on(hosts, tmp_path):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait, *_report()]]), manager)
    await host.model_capabilities.set_context_window("http://own.test/v1", "own-model", 40_000)
    await host.set_role_model("general", OWN_SLOT)
    commission = (await _dispatch(host, "sort the photos")).commission_id
    await wait_until(lambda: commission in host.task_agents._sessions and host.task_agents._sessions[commission].model)
    own_window = await host.model_capabilities.describe_window("http://own.test/v1", "own-model")
    slots = _default_slots()
    del slots[OWN_SLOT]
    _write_slots(manager, slots)

    # Its slot is gone, and it would be opened with another model next time; what it used now is the own model's.
    assert (await host.usage(commission))["window"] == own_window
    release.set()
    await wait_status(host, commission, "done")


async def test_an_open_session_is_woken_by_the_key_it_is_open_with(hosts, tmp_path):
    from nekopower.host.task_agents import _Session

    manager = _manager(tmp_path, _default_slots())
    counted: list[str] = []

    async def meter(key: str) -> bool:
        counted.append(key)
        return False

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager, task_agent_meter=meter)
    await host.set_role_model("general", OWN_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    await host.task_agents.settle_model(session)
    kept = await host.ledger.get_session(session.id)
    # The slot was given the free key since the session was opened with the user's own.
    slots = _default_slots()
    slots[OWN_SLOT] = _own(api_key=FREE_KEY)
    _write_slots(manager, slots)

    host.task_agents._sessions["c1"] = _Session("c1", free=False, idle=True)
    host.task_agents._sessions["c2"] = _Session("c2", free=True, idle=True)
    try:
        assert await host.task_agents._may_start("c1", session=kept) is None
        assert counted == []
        held = await host.task_agents._may_start("c2", session=kept)
        assert held is not None and counted == ["c2"]
        # One at work in its run is not taken for open and idle: where it would go now counts (the free
        # key, with the quota known to be used up).
        host.task_agents._sessions["c1"].idle = False
        held = await host.task_agents._may_start("c1", session=kept)
        assert held is not None
    finally:
        host.task_agents._sessions.clear()


async def test_a_session_whose_model_moved_since_it_last_ran_gets_no_idle_timer(hosts, tmp_path):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.set_role_model("general", SMALL_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    await host.task_agents.settle_model(session)
    assert await host.task_agents._task_model_key(session.id) is not None

    # The small tier moves to another provider: nothing of the session goes there until it runs again.
    manager.tiers["agent_small"] = {**small, "base_url": "http://elsewhere.test/v1"}
    assert await host.task_agents._task_model_key(session.id) is None


async def test_an_idle_session_is_not_summed_up_by_a_model_that_has_it_switched_off(hosts, tmp_path, monkeypatch):
    from config import nekopower_settings as settings

    monkeypatch.setattr(settings, "TASK_AGENT_IDLE_COMPACT_ENABLED", True)
    monkeypatch.setattr(settings, "TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS", 3600.0)
    # Any conversation is worth summing up here: only the switch stands in the way.
    monkeypatch.setattr(settings, "TASK_AGENT_IDLE_COMPACT_MIN_TOKENS", 0)
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report()]), manager)
    tasks = host.task_agents
    done = await wait_status(host, (await _dispatch(host, "sort the photos")).commission_id, "done")
    await tasks.settle()
    await let_go(host)
    tasks._drop_idle_timer(done.session_id)
    key = await tasks._task_model_key(done.session_id)
    looked: list[str] = []

    async def on_until_looked_at(session_id: str) -> bool:
        looked.append(session_id)
        return True

    # Switched on as the timer comes due, and off for the model by the time it would be sent there.
    monkeypatch.setattr(tasks, "_idle_compact_wanted", on_until_looked_at)
    await host.model_capabilities.set_idle_compact_on("http://agent.test/v1", "agent-model", False)
    begun: list[str] = []
    begin = host.ledger.begin_compacting

    async def beginning(session_id: str) -> Any:
        begun.append(session_id)
        return await begin(session_id)

    monkeypatch.setattr(host.ledger, "begin_compacting", beginning)
    assert await tasks._idle_compact(done.session_id, after=0.0, model=key) is False
    assert looked == [done.session_id] and begun == []


async def test_a_slot_made_to_follow_a_tier_at_the_same_address_is_not_followed_when_the_tier_moves(hosts, tmp_path):
    same = {"model": "own-model", "base_url": "http://own.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=same)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    tasks = host.task_agents
    await host.set_role_model("general", OWN_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    await tasks.settle_model(session)
    await host.set_role_model("general", None)

    # The own slot is made to follow the small tier, which is the same model for now: not taken as chosen.
    slots = _default_slots()
    slots[OWN_SLOT] = _follow("agent_small")
    _write_slots(manager, slots)
    choice = await tasks.session_model(await host.ledger.get_session(session.id))
    assert (choice.slot, choice.fallback) == (AGENT_SLOT, "slot_changed")
    row = await host.ledger.get_session(session.id)
    assert (row.model_slot, row.model_tier) == (AGENT_SLOT, "agent")


async def test_a_setting_is_written_to_the_file_it_was_read_from_when_the_data_moves_meanwhile(tmp_path):
    first, second = tmp_path / "a" / "role_models.json", tmp_path / "b" / "role_models.json"
    asked: list[Path] = []

    def where() -> Path:
        # Where the data is, asked once per look: it moves after the first.
        asked.append(first if not asked else second)
        return asked[-1]

    store = role_models.RoleModelStore(where)
    await store.put("scout", "inherit")

    assert json.loads(first.read_text(encoding="utf-8"))["roles"] == {"scout": "inherit"}
    assert not second.exists()


class _TierGoesAway(Manager):
    """The small tier is there as the slot is resolved, and gone when it is read again."""

    def __init__(self, root: Path, tiers: dict[str, Any]) -> None:
        super().__init__(root, tiers)
        self.reads = 0

    def get_model_api_config(self, model_type: str) -> dict[str, Any]:
        if model_type == "agent_small":
            self.reads += 1
            if self.reads > 1:
                return {}
        return super().get_model_api_config(model_type)


async def test_a_tier_that_is_gone_by_the_time_it_is_read_again_leaves_the_preset_with_its_default(hosts, tmp_path):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _TierGoesAway(tmp_path / "app", {"agent": dict(AGENT_TIER), "agent_small": small})
    _write_slots(manager, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.task_agents.role_models.store.put("general", SMALL_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")

    choice = await host.task_agents.session_model(session)

    assert (choice.name, choice.fallback) == ("agent-model", "host_api_not_configured")


async def test_the_settings_are_read_once_for_all_the_presets_shown(hosts, tmp_path, monkeypatch):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    store = host.task_agents.role_models.store
    await host.set_role_model("scout", OWN_SLOT)
    reads: list[int] = []
    read_all = store.all

    async def counting() -> dict[str, str]:
        reads.append(1)
        return await read_all()

    async def no_one_by_one(preset: str) -> str | None:
        raise AssertionError("read again for one preset")

    monkeypatch.setattr(store, "all", counting)
    monkeypatch.setattr(store, "get", no_one_by_one)
    view = await host.role_models()

    assert reads == [1]
    assert next(item for item in view["presets"] if item["preset"] == "scout")["in_effect"]["slot"] == OWN_SLOT


async def test_an_open_session_is_not_woken_while_task_agents_are_switched_off(hosts, tmp_path):
    from nekopower.host.task_agents import _Session

    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    host.task_agents._enabled = lambda: False

    host.task_agents._sessions["c1"] = _Session("c1", idle=True)
    try:
        held = await host.task_agents._may_start("c1", session=session)
    finally:
        host.task_agents._sessions.clear()

    assert held is not None and held.outcome == "held"


async def test_two_settlings_of_one_session_at_once_settle_it_on_one_model(hosts, tmp_path):
    import asyncio

    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _manager(tmp_path, _default_slots(), agent_small=small)
    slots = PluginModelSlots(manager)
    resolve = slots.resolve
    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def held_first(slot_id: str) -> Any:
        calls.append(slot_id)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return await resolve(slot_id)

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager, model_slots=slots)
    tasks = host.task_agents
    await host.set_role_model("general", OWN_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    # From here on the first slot to be resolved waits until it is released.
    slots.resolve = held_first

    # One settling reads the setting and waits in the slot; the user changes the setting; another begins.
    first = asyncio.ensure_future(tasks.session_model(session))
    await asyncio.wait_for(entered.wait(), 10)
    await tasks.role_models.store.put("general", SMALL_SLOT)
    second = asyncio.ensure_future(tasks.session_model(session))
    await asyncio.sleep(0.05)
    release.set()
    one, other = await asyncio.gather(first, second)

    assert one.slot == other.slot == OWN_SLOT
    assert (await host.ledger.get_session(session.id)).model_slot == OWN_SLOT
    assert tasks._settle_locks == {}


async def test_a_usable_slot_a_session_was_settled_on_is_found_behind_any_number_of_gone_ones(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    manager.tiers["agent"] = {}
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    usable = await host.ledger.create_session(lanlan_name="Lan", name="usable", kind="single")
    await host.ledger.update_session(
        usable.id,
        model_slot=OWN_SLOT,
        model_name="own-model",
        model_ident=role_models.model_ident("http://own.test/v1", "own-model"),
        model_tier="",
    )
    for number in range(25):
        gone = await host.ledger.create_session(lanlan_name="Lan", name=f"gone {number}", kind="single")
        await host.ledger.update_session(
            gone.id, active=True, model_slot=f"slot_{number:032x}", model_name="m", model_ident="y"
        )

    assert await host.task_agents.available() == (True, "")


async def test_a_commission_is_settled_before_it_is_queued_whatever_is_set_while_that_is_written(
    hosts, tmp_path, monkeypatch
):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first, writing, release_note = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    specs: list[Any] = []
    scripts = [ScriptedLLM([[wait_first, *_report()]]), ScriptedLLM([_report()])]
    host = _host(
        hosts, tmp_path, lambda spec: specs.append(spec) or scripts[len(specs) - 1], manager, max_task_agents=1
    )
    await host.set_role_model("general", OWN_SLOT)
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    append = host.ledger.append_event

    async def held_note(commission_id: str, kind: str, payload: Any = None, *args: Any, **kwargs: Any) -> Any:
        if kind == "queued":
            writing.set()
            await release_note.wait()
        return await append(commission_id, kind, payload, *args, **kwargs)

    monkeypatch.setattr(host.ledger, "append_event", held_note)
    queued = asyncio.ensure_future(_dispatch(host, "sort the music"))
    await asyncio.wait_for(writing.wait(), 10)
    # The setting changes while it is being noted that the commission is queued.
    await host.task_agents.role_models.store.put("general", SMALL_SLOT)
    release_note.set()
    second = (await queued).commission_id
    release_first.set()
    await wait_status(host, second, "done")

    assert [spec.model.model for spec in specs] == ["own-model", "own-model"]


async def test_a_place_that_comes_free_while_a_commission_is_settled_for_the_queue_is_taken(hosts, tmp_path):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first, settling, release_settling = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    specs: list[Any] = []
    scripts = [ScriptedLLM([[wait_first, *_report()]]), ScriptedLLM([_report()])]
    host = _host(
        hosts, tmp_path, lambda spec: specs.append(spec) or scripts[len(specs) - 1], manager, max_task_agents=1
    )
    tasks = host.task_agents
    await host.set_role_model("general", OWN_SLOT)
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    settle = tasks.settle_model

    async def held_settling(session: Any) -> None:
        # Only the settling of a commission that found no place comes here.
        settling.set()
        await release_settling.wait()
        await settle(session)

    tasks.settle_model = held_settling
    queued = asyncio.ensure_future(_dispatch(host, "sort the music"))
    await asyncio.wait_for(settling.wait(), 10)
    # The first one finishes while the second is being settled, not yet queued: nobody is queued to be started.
    release_first.set()
    await wait_status(host, first, "done")
    await wait_until(lambda: not tasks._sessions and not tasks._held_by, what="the first place coming free")
    await tasks.settle()
    release_settling.set()
    second = (await queued).commission_id

    await wait_status(host, second, "done")
    assert [spec.model.model for spec in specs] == ["own-model", "own-model"]

async def test_a_commission_stopped_while_it_is_settled_for_the_queue_is_neither_started_nor_queued(hosts, tmp_path):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first, settling, release_settling = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    specs: list[Any] = []
    host = _host(
        hosts, tmp_path, lambda spec: specs.append(spec) or ScriptedLLM([[wait_first, *_report()]]), manager,
        max_task_agents=1,
    )
    tasks = host.task_agents
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    later, _created = await host.ledger.create(
        lanlan_name="Lan", title="sort the music", dispatch_text="sort the music", source="dispatch", route="task_agent"
    )
    settle = tasks.settle_model

    async def held_settling(session: Any) -> None:
        settling.set()
        await release_settling.wait()
        await settle(session)

    tasks.settle_model = held_settling
    wanted = {"yes": True}
    handing = asyncio.ensure_future(tasks.submit(later, still_wanted=lambda: wanted["yes"]))
    await asyncio.wait_for(settling.wait(), 10)
    # Everything is stopped meanwhile, and a place comes free.
    wanted["yes"] = False
    release_first.set()
    await wait_status(host, first, "done")
    await wait_until(lambda: not tasks._sessions and not tasks._held_by, what="the first place coming free")
    release_settling.set()

    assert await asyncio.wait_for(handing, 10) is False
    assert later.id not in tasks._queue and later.id not in tasks._sessions
    assert len(specs) == 1


async def test_the_default_slot_rewritten_between_its_listing_and_its_reading_is_not_followed(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    slots = PluginModelSlots(manager)
    listed = slots.list

    async def rewritten_after_listing() -> list[Any]:
        found = await listed()
        # Between the two reads, the slot that followed the task agent model is pointed somewhere else.
        rewritten = _default_slots()
        rewritten[AGENT_SLOT] = _own(name="Familiar model", base_url="http://somewhere-else.test/v1")
        _write_slots(manager, rewritten)
        return found

    slots.list = rewritten_after_listing
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager, model_slots=slots)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")

    choice = await host.task_agents.session_model(session)

    assert (choice.slot, choice.config["base_url"], choice.fallback) == ("", "http://agent.test/v1", "slot_changed")


async def test_a_commission_being_settled_for_the_queue_as_the_host_shuts_down_fails_and_is_not_queued(
    hosts, tmp_path
):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first, settling, release_settling = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait_first, *_report()]]), manager, max_task_agents=1)
    tasks = host.task_agents
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    later, _created = await host.ledger.create(
        lanlan_name="Lan", title="sort the music", dispatch_text="sort the music", source="dispatch", route="task_agent"
    )
    settle = tasks.settle_model

    async def held_settling(session: Any) -> None:
        settling.set()
        await release_settling.wait()
        await settle(session)

    tasks.settle_model = held_settling
    handing = asyncio.ensure_future(tasks.submit(later))
    await asyncio.wait_for(settling.wait(), 10)
    closing = asyncio.ensure_future(tasks.shutdown())
    await wait_until(lambda: tasks._closed, what="the manager closing")
    release_settling.set()
    release_first.set()

    assert await asyncio.wait_for(handing, 10) is False
    await asyncio.wait_for(closing, 15)
    assert later.id not in tasks._queue
    assert (await host.ledger.get(later.id)).status == "failed"


async def test_the_settling_lock_stays_while_another_waits_for_it_though_the_first_was_cancelled(hosts, tmp_path):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    slots = PluginModelSlots(manager)
    resolve = slots.resolve
    entered, never = asyncio.Event(), asyncio.Event()
    first_call = {"yes": True}

    async def first_held(slot_id: str) -> Any:
        if first_call["yes"]:
            first_call["yes"] = False
            entered.set()
            await never.wait()
        return await resolve(slot_id)

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager, model_slots=slots)
    tasks = host.task_agents
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    slots.resolve = first_held
    first = asyncio.ensure_future(tasks.session_model(session))
    await asyncio.wait_for(entered.wait(), 10)
    second = asyncio.ensure_future(tasks.session_model(session))
    await asyncio.sleep(0.05)

    first.cancel()
    await asyncio.gather(first, return_exceptions=True)

    # The second waited for it, and has not come back: one that came now would wait behind it.
    assert session.id in tasks._settle_locks
    choice = await asyncio.wait_for(second, 10)
    assert choice.slot == AGENT_SLOT
    assert tasks._settle_locks == {}


async def test_a_session_whose_slot_was_pointed_elsewhere_does_not_count_as_one_she_may_go_on_with(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    await host.set_role_model("general", OWN_SLOT)
    session = await host.ledger.create_session(lanlan_name="Lan", name="s", kind="single")
    await host.task_agents.settle_model(session)
    await host.set_role_model("general", None)
    manager.tiers["agent"] = {}
    assert await host.task_agents.available() == (True, "")

    slots = _default_slots()
    slots[OWN_SLOT] = _own(base_url="http://somewhere-else.test/v1")
    _write_slots(manager, slots)

    assert await host.task_agents.available() == (False, "not_enabled")


async def test_one_model_that_cannot_be_read_does_not_keep_the_others_from_being_asked(hosts, tmp_path):
    async def unreadable() -> dict[str, Any]:
        raise OSError("the configuration is being written")

    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager, task_agent_model=unreadable)
    assert await host.task_agents.available() == (False, "not_enabled")

    # The task agent model cannot be read; the leads of projects go to a slot of their own.
    await host.task_agents.role_models.store.put("boss", OWN_SLOT)

    assert await host.task_agents.available() == (True, "")


async def test_a_commission_cancelled_while_it_is_settled_for_the_queue_is_not_queued(hosts, tmp_path):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first, settling, release_settling = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait_first, *_report()]]), manager, max_task_agents=1)
    tasks = host.task_agents
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    later, _created = await host.ledger.create(
        lanlan_name="Lan", title="sort the music", dispatch_text="sort the music", source="dispatch", route="task_agent"
    )
    settle = tasks.settle_model

    async def held_settling(session: Any) -> None:
        settling.set()
        await release_settling.wait()
        await settle(session)

    tasks.settle_model = held_settling
    handing = asyncio.ensure_future(tasks.submit(later))
    await asyncio.wait_for(settling.wait(), 10)
    # Cancelled while nothing that stops commissions can find it in the queue.
    await host.ledger.transition(later.id, "cancelled", reason="everything was stopped")
    release_settling.set()

    assert await asyncio.wait_for(handing, 10) is False
    assert later.id not in tasks._queue
    release_first.set()
    await wait_status(host, first, "done")


async def test_a_commission_whose_session_cannot_be_read_to_settle_it_is_queued_all_the_same(
    hosts, tmp_path, monkeypatch
):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first = asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    specs: list[Any] = []
    scripts = [ScriptedLLM([[wait_first, *_report()]]), ScriptedLLM([_report()])]
    host = _host(
        hosts, tmp_path, lambda spec: specs.append(spec) or scripts[len(specs) - 1], manager, max_task_agents=1
    )
    tasks = host.task_agents
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    later, _created = await host.ledger.create(
        lanlan_name="Lan", title="sort the music", dispatch_text="sort the music", source="dispatch", route="task_agent"
    )
    get_session = host.ledger.get_session
    failed: list[str] = []

    async def unreadable_once(session_id: Any) -> Any:
        if not failed:
            failed.append(str(session_id))
            raise OSError("the disk is busy")
        return await get_session(session_id)

    monkeypatch.setattr(host.ledger, "get_session", unreadable_once)

    assert await tasks.submit(later) is True
    assert failed and later.id in tasks._queue
    monkeypatch.setattr(host.ledger, "get_session", get_session)
    release_first.set()
    await wait_status(host, later.id, "done")
    assert len(specs) == 2


async def test_a_commission_whose_record_cannot_be_read_after_it_is_settled_is_queued_all_the_same(
    hosts, tmp_path, monkeypatch
):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first = asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait_first, *_report()]]), manager, max_task_agents=1)
    tasks = host.task_agents
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    later, _created = await host.ledger.create(
        lanlan_name="Lan", title="sort the music", dispatch_text="sort the music", source="dispatch", route="task_agent"
    )
    settle = tasks.settle_model
    get = host.ledger.get

    async def unreadable(commission_id: str) -> Any:
        if commission_id == later.id:
            raise OSError("the disk is busy")
        return await get(commission_id)

    async def settling_then_unreadable(session: Any) -> None:
        await settle(session)
        monkeypatch.setattr(host.ledger, "get", unreadable)

    tasks.settle_model = settling_then_unreadable

    assert await tasks.submit(later) is True
    assert later.id in tasks._queue
    monkeypatch.setattr(host.ledger, "get", get)
    release_first.set()
    await wait_status(host, first, "done")


async def test_settings_of_another_shape_are_not_written_over(tmp_path):
    path = tmp_path / "role_models.json"
    store = role_models.RoleModelStore(path)
    path.write_text(json.dumps({"version": 1, "roles": ["boss"]}), encoding="utf-8")

    with pytest.raises(OSError):
        await store.put("scout", "inherit")

    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1, "roles": ["boss"]}


async def test_a_write_of_the_settings_that_fails_after_whoever_asked_gave_up_is_logged(tmp_path, caplog):
    import asyncio

    caplog.set_level(logging.WARNING, logger="nekopower.host.role_models")
    path = tmp_path / "role_models.json"
    path.write_text("{half written", encoding="utf-8")
    store = role_models.RoleModelStore(path)
    asking = asyncio.ensure_future(store.put("scout", "inherit"))
    await asyncio.sleep(0)
    asking.cancel()
    await asyncio.gather(asking, return_exceptions=True)
    for _ in range(50):
        if "writing a preset model setting failed" in caplog.text:
            break
        await asyncio.sleep(0.02)

    assert "writing a preset model setting failed: OSError" in caplog.text


class _TierUnreadableAfterResolving(Manager):
    """The small tier reads as configured when a slot is resolved, and cannot be read when it is read again."""

    def __init__(self, root: Path, tiers: dict[str, Any]) -> None:
        super().__init__(root, tiers)
        self.fail_second = False
        self.reads = 0

    def get_model_api_config(self, model_type: str) -> dict[str, Any]:
        if model_type == "agent_small" and self.fail_second:
            self.reads += 1
            if self.reads % 2 == 0:
                raise OSError("the configuration is being written")
        return super().get_model_api_config(model_type)


async def test_a_tier_that_cannot_be_read_does_not_stop_the_look_through_settled_sessions(hosts, tmp_path):
    small = {"model": "small-model", "base_url": "http://small.test/v1", "api_key": "sk-small"}
    manager = _TierUnreadableAfterResolving(tmp_path / "app", {"agent": dict(AGENT_TIER), "agent_small": small})
    _write_slots(manager, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    tasks = host.task_agents
    await host.set_role_model("general", OWN_SLOT)
    own = await host.ledger.create_session(lanlan_name="Lan", name="own", kind="single")
    await tasks.settle_model(own)
    await host.set_role_model("general", SMALL_SLOT)
    small_one = await host.ledger.create_session(lanlan_name="Lan", name="small", kind="single")
    await tasks.settle_model(small_one)
    await host.ledger.update_session(small_one.id, active=True)
    await host.set_role_model("general", None)
    manager.tiers["agent"] = {}
    manager.fail_second = True

    # The small one is looked at first and cannot be read; the own one is looked at all the same.
    assert await tasks.available() == (True, "")


async def test_a_commission_stopped_while_it_is_settled_for_the_queue_is_not_queued_when_its_record_cannot_be_read(
    hosts, tmp_path, monkeypatch
):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first, settling, release_settling = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait_first, *_report()]]), manager, max_task_agents=1)
    tasks = host.task_agents
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    later, _created = await host.ledger.create(
        lanlan_name="Lan", title="sort the music", dispatch_text="sort the music", source="dispatch", route="task_agent"
    )
    settle = tasks.settle_model

    async def held_settling(session: Any) -> None:
        settling.set()
        await release_settling.wait()
        await settle(session)

    tasks.settle_model = held_settling
    handing = asyncio.ensure_future(tasks.submit(later))
    await asyncio.wait_for(settling.wait(), 10)
    await tasks.abort(later.id)
    get = host.ledger.get

    async def unreadable(commission_id: str) -> Any:
        if commission_id == later.id:
            raise OSError("the disk is busy")
        return await get(commission_id)

    monkeypatch.setattr(host.ledger, "get", unreadable)
    release_settling.set()

    assert await asyncio.wait_for(handing, 10) is False
    assert later.id not in tasks._queue
    assert tasks._settling_for_queue == set() and tasks._stopped_while_settling == set()
    monkeypatch.setattr(host.ledger, "get", get)
    release_first.set()
    await wait_status(host, first, "done")


async def test_a_commission_stopped_as_its_record_is_read_on_its_way_into_the_queue_is_not_queued(
    hosts, tmp_path, monkeypatch
):
    import asyncio

    manager = _manager(tmp_path, _default_slots())
    release_first = asyncio.Event()

    async def wait_first() -> None:
        await release_first.wait()

    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([[wait_first, *_report()]]), manager, max_task_agents=1)
    tasks = host.task_agents
    first = (await _dispatch(host, "sort the photos")).commission_id
    await wait_status(host, first, "working")
    later, _created = await host.ledger.create(
        lanlan_name="Lan", title="sort the music", dispatch_text="sort the music", source="dispatch", route="task_agent"
    )
    get = host.ledger.get
    stopped: list[str] = []

    async def stopped_as_it_is_read(commission_id: str) -> Any:
        if commission_id == later.id and later.id in tasks._settling_for_queue and not stopped:
            # Stopped this very moment, and what the ledger has of it cannot be read.
            stopped.append(commission_id)
            await tasks.abort(commission_id)
            raise OSError("the disk is busy")
        return await get(commission_id)

    monkeypatch.setattr(host.ledger, "get", stopped_as_it_is_read)

    assert await tasks.submit(later) is False
    assert stopped == [later.id] and later.id not in tasks._queue
    monkeypatch.setattr(host.ledger, "get", get)
    release_first.set()
    await wait_status(host, first, "done")


# ---------------------------------------------------------------------------
# The planner goes by the advanced task agent model of the main app
# ---------------------------------------------------------------------------

ADVANCED_TIER = {
    "model": "advanced-model",
    "base_url": "http://advanced.test/v1",
    "api_key": "sk-advanced-secret-0003",
    "provider_type": "openai_compatible",
}


def _with_advanced_slot(**more: Any) -> dict[str, ModelSlot]:
    return {**_default_slots(), ADVANCED_SLOT: _follow("agent_advanced", **more)}


async def _planner(host: Any, name: str = "plan") -> Any:
    return await host.ledger.create_session(lanlan_name="Lan", name=name, kind="single", preset="planner")


async def test_out_of_the_box_a_planner_is_sent_to_the_slot_that_follows_the_advanced_model(hosts, tmp_path):
    manager = _manager(tmp_path, _with_advanced_slot(), agent_advanced=dict(ADVANCED_TIER))
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    session = await _planner(host)

    choice = await host.task_agents.session_model(session)

    assert (choice.slot, choice.name, choice.agent, choice.tier, choice.fallback) == (
        ADVANCED_SLOT, "advanced-model", False, "agent_advanced", ""
    )
    assert choice.config["api_key"] == ADVANCED_TIER["api_key"]
    row = await host.ledger.get_session(session.id)
    assert (row.model_slot, row.model_name, row.model_tier, row.model_fallback) == (
        ADVANCED_SLOT, "advanced-model", "agent_advanced", ""
    )
    by = {item["preset"]: item for item in (await host.role_models())["presets"]}
    assert by["planner"]["in_effect"] == {
        "follows": None, "slot": ADVANCED_SLOT, "model": "advanced-model", "agent": False, "free": False,
        "fallback": None,
    }


async def test_the_other_presets_keep_their_defaults_beside_an_advanced_planner(hosts, tmp_path):
    manager = _manager(tmp_path, _with_advanced_slot(), agent_advanced=dict(ADVANCED_TIER))
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)

    by = {item["preset"]: item for item in (await host.role_models())["presets"]}

    assert {name: item["default"] for name, item in by.items()} == {
        "boss": "agent", "worker": "inherit", "scout": "inherit", "inspector": "inherit", "planner": "advanced",
        "general": "agent",
    }
    for name in ("boss", "general"):
        assert (by[name]["in_effect"]["slot"], by[name]["in_effect"]["model"]) == (AGENT_SLOT, "agent-model")
    for name in ("worker", "scout", "inspector"):
        assert by[name]["in_effect"] == {"follows": "starter", "fallback": None}


async def test_a_planner_started_by_a_lead_goes_by_the_advanced_model_and_not_the_leads(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots(), agent_advanced=dict(ADVANCED_TIER))
    team = _Team({"preset": "planner"})
    host = _host(hosts, tmp_path, team.pick, manager)
    # The lead goes by a slot of its own: its planner no longer follows it there.
    await host.set_role_model("general", OWN_SLOT)

    lead = await wait_status(host, (await _dispatch(host, "plan the tests")).commission_id, "done")
    (sub,) = await host.ledger.select(parent_id=lead.id)

    assert team.lead_spec.model.model == "own-model"
    assert team.child_spec.model.model == "advanced-model"
    row = await _session_of(host, sub.id)
    assert (row.preset, row.model_slot, row.model_tier, row.model_fallback) == ("planner", "", "agent_advanced", "")


@pytest.mark.parametrize("slots", ["none", "without tool calling"])
async def test_without_a_slot_that_follows_it_a_planner_is_sent_to_the_advanced_model_itself(hosts, tmp_path, slots):
    chosen = _default_slots() if slots == "none" else _with_advanced_slot(capabilities=["text", "streaming"])
    manager = _manager(tmp_path, chosen, agent_advanced=dict(ADVANCED_TIER))
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    session = await _planner(host)

    choice = await host.task_agents.session_model(session)

    assert (choice.slot, choice.name, choice.agent, choice.tier, choice.fallback) == (
        "", "advanced-model", False, "agent_advanced", ""
    )
    row = await host.ledger.get_session(session.id)
    assert (row.model_slot, row.model_name, row.model_tier) == ("", "advanced-model", "agent_advanced")
    # Shown as a model of its own: not the task agent model, and not the model of whoever starts it.
    by = {item["preset"]: item for item in (await host.role_models())["presets"]}
    assert by["planner"]["in_effect"] == {
        "follows": None, "slot": None, "model": "advanced-model", "agent": False, "free": False, "fallback": None,
    }


async def test_a_planner_settled_on_the_advanced_model_itself_stays_on_it_and_follows_it(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots(), agent_advanced=dict(ADVANCED_TIER))
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    tasks = host.task_agents
    session = await _planner(host)
    await tasks.settle_model(session)

    # Opened again: the advanced model it was settled on, and not the task agent model.
    choice = await tasks.session_model(await host.ledger.get_session(session.id))
    assert (choice.slot, choice.name, choice.tier, choice.fallback) == ("", "advanced-model", "agent_advanced", "")

    # The user sets the advanced model of the main app to another one: followed, as a tier always is.
    manager.tiers["agent_advanced"] = {**ADVANCED_TIER, "model": "advanced-model-2"}
    choice = await tasks.session_model(await host.ledger.get_session(session.id))
    assert (choice.slot, choice.name, choice.tier, choice.fallback) == ("", "advanced-model-2", "agent_advanced", "")


async def test_a_planner_on_the_slot_that_follows_the_advanced_model_follows_that_tier_and_no_other(hosts, tmp_path):
    manager = _manager(tmp_path, _with_advanced_slot(), agent_advanced=dict(ADVANCED_TIER))
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    tasks = host.task_agents
    session = await _planner(host)
    await tasks.settle_model(session)

    # The tier moves: followed, and not taken for a slot that was changed.
    manager.tiers["agent_advanced"] = {**ADVANCED_TIER, "base_url": "http://elsewhere.test/v1"}
    choice = await tasks.session_model(await host.ledger.get_session(session.id))
    assert (choice.slot, choice.config["base_url"], choice.fallback) == (ADVANCED_SLOT, "http://elsewhere.test/v1", "")

    # The slot is made to follow the task agent model instead: not followed there; the default again,
    # which with no slot that follows the advanced model is that model itself, and why.
    slots = _with_advanced_slot()
    slots[ADVANCED_SLOT] = _follow("agent")
    _write_slots(manager, slots)
    choice = await tasks.session_model(await host.ledger.get_session(session.id))
    assert (choice.slot, choice.tier, choice.fallback) == ("", "agent_advanced", "slot_changed")
    row = await host.ledger.get_session(session.id)
    assert (row.model_slot, row.model_tier, row.model_fallback) == ("", "agent_advanced", "slot_changed")


@pytest.mark.parametrize(
    ("how", "code"),
    [
        ("not configured", "host_api_not_configured"),
        ("unreadable", "model_config_unreadable"),
        ("slot of a tier not configured", "host_api_not_configured"),
        ("not an HTTP address", "host_api_unsupported"),
        ("slot of a tier that is not an HTTP address", "host_api_unsupported"),
    ],
)
async def test_an_advanced_model_that_cannot_be_used_leaves_a_planner_with_the_task_agent_model_and_says_why(
    hosts, tmp_path, how, code
):
    tiers: dict[str, Any] = {}
    if how == "unreadable":
        tiers["agent_advanced"] = OSError("cannot be read")
    if how.endswith("not an HTTP address"):
        tiers["agent_advanced"] = {**ADVANCED_TIER, "base_url": "ws://advanced.test/v1"}
    slots = _with_advanced_slot() if how.startswith("slot") else _default_slots()
    manager = _manager(tmp_path, slots, **tiers)
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    session = await _planner(host)

    choice = await host.task_agents.session_model(session)

    assert (choice.slot, choice.name, choice.agent, choice.tier, choice.fallback) == (
        AGENT_SLOT, "agent-model", True, "agent", code
    )
    row = await host.ledger.get_session(session.id)
    assert (row.model_slot, row.model_fallback) == (AGENT_SLOT, code)


class _AdvancedTierMovesOffHttp(Manager):
    """The advanced tier has an HTTP address as the slots are listed and one is resolved, and a WebSocket one when it is read again."""

    def __init__(self, root: Path, tiers: dict[str, Any]) -> None:
        super().__init__(root, tiers)
        self.reads = 0

    def get_model_api_config(self, model_type: str) -> dict[str, Any]:
        if model_type == "agent_advanced":
            self.reads += 1
            # Read once as the slots are listed, once as the slot is resolved.
            if self.reads > 2:
                return {**ADVANCED_TIER, "base_url": "ws://advanced.test/v1"}
        return super().get_model_api_config(model_type)


async def test_an_advanced_model_whose_address_is_no_http_one_when_read_again_leaves_a_planner_with_the_task_agent_model(
    hosts, tmp_path
):
    manager = _AdvancedTierMovesOffHttp(
        tmp_path / "app", {"agent": dict(AGENT_TIER), "agent_advanced": dict(ADVANCED_TIER)}
    )
    _write_slots(manager, _with_advanced_slot())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    session = await _planner(host)

    choice = await host.task_agents.session_model(session)

    # Resolved through the slot with its HTTP address, read again with the WebSocket one: refused
    # there too, as the advanced model itself is.
    assert manager.reads >= 3
    assert (choice.slot, choice.agent, choice.tier, choice.fallback) == (AGENT_SLOT, True, "agent", "host_api_unsupported")
    row = await host.ledger.get_session(session.id)
    assert (row.model_slot, row.model_fallback) == (AGENT_SLOT, "host_api_unsupported")


async def test_without_slots_a_planner_is_sent_to_the_task_agent_model_as_every_preset_is(hosts, tmp_path):
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), None)
    session = await _planner(host)

    choice = await host.task_agents.session_model(session)

    assert (choice.slot, choice.name, choice.agent, choice.fallback) == ("", "agent-model", True, "")


@pytest.mark.parametrize("setting", ["slot", "inherit"])
async def test_a_planner_set_by_the_user_goes_by_the_setting_and_not_the_advanced_model(hosts, tmp_path, setting):
    manager = _manager(tmp_path, _with_advanced_slot(), agent_advanced=dict(ADVANCED_TIER))
    team = _Team({"preset": "planner"})
    host = _host(hosts, tmp_path, team.pick, manager)
    await host.set_role_model("planner", OWN_SLOT if setting == "slot" else "inherit")

    by = {item["preset"]: item for item in (await host.role_models())["presets"]}
    lead = await wait_status(host, (await _dispatch(host, "plan the tests")).commission_id, "done")
    (sub,) = await host.ledger.select(parent_id=lead.id)
    row = await _session_of(host, sub.id)

    if setting == "slot":
        assert by["planner"]["in_effect"]["slot"] == OWN_SLOT
        assert team.child_spec.model.model == "own-model"
        assert (row.model_slot, row.model_fallback) == (OWN_SLOT, "")
    else:
        assert by["planner"]["in_effect"] == {"follows": "starter", "fallback": None}
        # The lead is a session of no project's: the task agent model, by the slot that follows it.
        assert team.child_spec.model.model == "agent-model"
        assert (row.model_slot, row.model_fallback) == (AGENT_SLOT, "")


async def test_a_planner_counts_against_the_free_quota_only_where_the_advanced_model_goes_with_the_free_key(
    hosts, tmp_path
):
    manager = _manager(tmp_path, _default_slots(), agent_advanced={**ADVANCED_TIER, "api_key": FREE_KEY})
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM(), manager)
    free = await host.task_agents.session_model(await _planner(host, "free"))
    manager.tiers["agent_advanced"] = dict(ADVANCED_TIER)
    paid = await host.task_agents.session_model(await _planner(host, "paid"))

    assert (free.free, paid.free) == (True, False)


async def test_a_session_given_another_preset_is_sent_to_that_presets_model_from_its_next_start(hosts, tmp_path):
    manager = _manager(tmp_path, _default_slots())
    llm = ScriptedLLM([_report(), _report(), _report()])
    specs: list[Any] = []

    def pick(spec: Any) -> Any:
        specs.append(spec)
        return llm

    host = _host(hosts, tmp_path, pick, manager)
    await host.set_role_model("scout", OWN_SLOT)
    member = (await host.open_session("Lan", "Shop site", preset="general"))["session_id"]

    async def sent_to() -> tuple[str, str]:
        kept = await host.ledger.get_session(member)
        return kept.model_slot, kept.model_name

    await wait_status(host, (await _dispatch(host, "fix the header of the shop site")).commission_id, "done")
    await let_go(host)
    assert await sent_to() == (AGENT_SLOT, "agent-model")

    # Given another preset: settled anew by that one's setting, as one made of it would be.
    await host.change_duty(member, "scout")
    assert await sent_to() == (OWN_SLOT, "own-model")
    (card,) = (await host.board())["projects"][0]["members"]
    assert card["model"] == "own-model"
    await wait_status(host, (await _dispatch(host, "look at the footer of the shop site")).commission_id, "done")
    await let_go(host)

    # And back: the preset it has says where it is sent, not the one it was made with.
    await host.change_duty(member, "general")
    assert await sent_to() == (AGENT_SLOT, "agent-model")
    await wait_status(host, (await _dispatch(host, "fix the footer of the shop site")).commission_id, "done")

    assert [spec.model.model for spec in specs] == ["agent-model", "own-model", "agent-model"]


async def test_a_session_whose_new_presets_model_cannot_be_told_just_now_stays_where_it_was(hosts, tmp_path, monkeypatch):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]), manager)
    member = (await host.open_session("Lan", "Shop site", preset="general"))["session_id"]
    before = await host.ledger.get_session(member)
    assert (before.model_slot, before.model_name) == (AGENT_SLOT, "agent-model")

    async def unreadable(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("the settings cannot be read")

    monkeypatch.setattr(host.task_agents.role_models, "choose", unreadable)
    changed = await host.change_duty(member, "scout")

    # Its preset is changed all the same; where it is sent is as it was.
    kept = await host.ledger.get_session(member)
    assert changed["changed"] and kept.preset == "scout"
    assert (kept.model_slot, kept.model_name, kept.model_ident) == (before.model_slot, before.model_name, before.model_ident)


async def test_a_session_begun_in_dsh_goes_on_there_also_as_a_preset_that_withholds_tools(hosts, tmp_path, monkeypatch):
    from nekopower.host import task_agents as task_agents_module

    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report()]), manager)
    await host.set_role_model("scout", OWN_SLOT)
    # A session that was begun in dsh stays there whatever preset it has by now (given another
    # since, say): with what was said in it, and with the model dsh was started by. It is held to
    # its preset there by what it is told and by the host's own tools it is opened without.
    await host.open_session("Lan", "Shop site", preset="scout")
    seen: list[tuple[Any, bool]] = []

    async def choosing(commission: Any, model: Any, begun_in: Any, *, builtin_only: bool = False) -> Any:
        seen.append((model.model, builtin_only))
        raise RuntimeError("stop here")

    monkeypatch.setattr(task_agents_module, "_begun_in", lambda place, session_dir: task_agents_module.RUNTIME_DSH)
    host.task_agents._choose_runtime = choosing
    await wait_status(host, (await _dispatch(host, "look at the header of the shop site")).commission_id, "failed")

    assert seen == [("agent-model", False)]


async def test_a_session_whose_new_preset_has_no_model_configured_just_now_stays_where_it_was(hosts, tmp_path, monkeypatch):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]), manager)
    member = (await host.open_session("Lan", "Shop site", preset="general"))["session_id"]
    before = await host.ledger.get_session(member)

    async def nothing_there(*_args: Any, **_kwargs: Any) -> Any:
        # Where it would go has no model configured this moment.
        return role_models.Choice(slot=OWN_SLOT, agent=False, name="", ident="", free=False)

    monkeypatch.setattr(host.task_agents.role_models, "choose", nothing_there)
    await host.change_duty(member, "scout")

    kept = await host.ledger.get_session(member)
    assert (kept.model_slot, kept.model_name, kept.model_ident) == (before.model_slot, before.model_name, before.model_ident)


async def test_a_model_chosen_for_a_preset_the_session_has_no_longer_is_not_written_over_the_new_ones(hosts, tmp_path, monkeypatch):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, ScriptedLLM([_report()]), manager)
    await host.set_role_model("scout", OWN_SLOT)
    agents = host.task_agents
    session = await host.ledger.create_session(lanlan_name="Lan", name="it", kind="line", project="Shop site")
    choose = agents.role_models.choose
    meanwhile: list[str] = []

    async def choosing(preset: str, *args: Any, **kwargs: Any) -> Any:
        chosen = await choose(preset, *args, **kwargs)
        if preset == "general" and not meanwhile:
            # While its model is being settled for the preset it was made with, it is given another,
            # and its model is settled anew for that one.
            meanwhile.append(preset)
            await host.change_duty(session.id, "scout")
        return chosen

    monkeypatch.setattr(agents.role_models, "choose", choosing)
    await agents.settle_model(session)

    kept = await host.ledger.get_session(session.id)
    assert meanwhile == ["general"]
    assert (kept.preset, kept.model_slot, kept.model_name) == ("scout", OWN_SLOT, "own-model")
    # By the ledger itself: only for the preset the session has.
    assert await host.ledger.keep_model(session.id, "general", model_name="agent-model") is False
    assert (await host.ledger.get_session(session.id)).model_name == "own-model"
    assert await host.ledger.keep_model(session.id, "scout", model_name="another") is True
    with pytest.raises(ValueError, match="not the columns"):
        await host.ledger.keep_model(session.id, "scout", seat="tree")


CAPPED_TEMPLATE = '---\nname: capped\ndescription: When a few requests are enough.\nmax_requests: 5\n---\nYour role: quick.\n'


@pytest.mark.parametrize(
    ("preset", "only_built_in"), [("scout", False), ("general", False), ("boss", True), ("capped", True)]
)
async def test_which_new_sessions_are_opened_in_the_built_in_runtime_whatever_else_would_be_chosen(
    hosts, tmp_path, preset, only_built_in
):
    manager = _manager(tmp_path, _default_slots())
    host = _host(hosts, tmp_path, lambda spec: ScriptedLLM([_report(), _report()]), manager)
    templates = tmp_path / "nekopower" / "task_agent_space" / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "capped.md").write_text(CAPPED_TEMPLATE, encoding="utf-8")
    await host.open_session("Lan", "Shop site", preset=preset)
    chosen: list[bool] = []
    choose = host.task_agents._choose_runtime

    async def choosing(commission: Any, model: Any, begun_in: Any, *, builtin_only: bool = False) -> Any:
        chosen.append(builtin_only)
        return await choose(commission, model, begun_in, builtin_only=builtin_only)

    host.task_agents._choose_runtime = choosing
    await wait_status(host, (await _dispatch(host, "look at the header of the shop site")).commission_id, "done")

    # A preset that withholds tools is held to in dsh by what its session is told, and is opened
    # wherever another would be. The leader of a project is not, nor one whose requests are capped
    # (the host cannot count them in dsh).
    assert chosen and chosen[0] is only_built_in
