"""The routes of the console that show what the task agents do and set how they are run.

What happened among them and who waits for whom, the model of each preset,
what was said in a session, what was used and how much may be, the
commissions, the MCP tools, and putting a project away
(``app/agent_server/api_console.py``), over a real host.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import config.nekopower_settings as settings
from app.agent_server.api_console import TOKEN_HEADER, build_console_router
from nekopower.host.paths import DSH_SESSION_NAME, TRANSCRIPT_NAME
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekopower.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    scripted_runtime,
    scripts_by_commission,
    wait_status,
)
from tests.unit.nekopower.plugin_server_fake import FakePluginServer, entry, plugin
from tests.unit.nekopower.plugin_server_fake import finish as plugin_finish

TOKEN = "token-of-this-launch"
HEADERS = {TOKEN_HEADER: TOKEN}


def _report(status: str = "ready", summary: str = "done", call_id: str = "call-1") -> list[Any]:
    return [call("report", {"status": status, "summary": summary}, call_id), finish("tool_calls")]


def _used(prompt: int, completion: int) -> Any:
    from utils.llm_client import LLMStreamChunk

    data = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    return LLMStreamChunk(content="", usage_metadata=data)


class _Console:
    """A client of the routes over a host of its own; closed with the test."""

    def __init__(self, tmp_path: Path, llm: Any = None, server: Any = None, **kwargs: Any) -> None:
        if llm is not None:
            pick = llm if callable(llm) else (lambda spec: llm)
            kwargs.setdefault("runtime_factory", scripted_runtime(pick))
        kwargs.setdefault("task_agent_model", fake_model)
        kwargs.setdefault("decision_llm", FakeDecisionLLM())
        self.host = make_host(tmp_path, server, **kwargs)

        async def get_host() -> Any:
            return self.host

        async def character() -> str | None:
            return "Lan"

        app = FastAPI()
        app.include_router(build_console_router(get_host, lambda: TOKEN, character=character))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test")

    async def get(self, path: str, **params: Any) -> httpx.Response:
        return await self.client.get(f"/console{path}", headers=HEADERS, params=params or None)

    async def post(self, path: str, payload: Any = None) -> httpx.Response:
        return await self.client.post(f"/console{path}", headers=HEADERS, json=payload)

    async def dispatch(self, text: str) -> Any:
        return (await self.host.dispatch(text, lanlan_name="Lan", lang="en")).commissions[0]

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.host.shutdown()


@pytest.fixture
async def consoles() -> Any:
    made: list[_Console] = []

    def make(*args: Any, **kwargs: Any) -> _Console:
        made.append(_Console(*args, **kwargs))
        return made[-1]

    yield make
    for console in made:
        await console.aclose()


async def _at_rest(host: Any, session_id: str) -> None:
    """Wait until nothing is at work in a session any more."""
    for _ in range(500):
        if (await host.ledger.get_session(session_id)).status not in ("running", "waiting", "compacting"):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the session stayed at work")


async def _over(console: Any, commission_id: str) -> Any:
    """Wait until a commission is done and nothing more is written for it; return it.

    It is done when its report is taken. Its session still ends its turn
    after that: it writes the last of what was said and says so in events.
    What is read before that is over is not yet all there is.
    """
    done = await wait_status(console.host, commission_id, "done")
    await _at_rest(console.host, done.session_id)
    await console.host.task_agents.settle()
    return done


def _code(response: httpx.Response) -> tuple[int, str]:
    return response.status_code, response.json()["detail"]["code"]


# ---------------------------------------------------------------------------
# Who may ask
# ---------------------------------------------------------------------------


async def test_none_of_these_routes_answers_without_the_token_of_this_launch(consoles, tmp_path):
    console = consoles(tmp_path)
    reads = (
        "/timeline", "/timeline/start", "/waits", "/role-models", "/familiars/s/transcript", "/familiars/s/usage",
        "/commissions", "/commissions/c/usage", "/mcp", "/usage-limit",
    )
    for path in reads:
        assert (await console.client.get(f"/console{path}")).status_code == 403, path
    writes = {
        "/role-models": {"preset": "general", "value": None},
        "/mcp/permission": {"mode": "deny"},
        "/usage-limit": {"tokens": 600_000},
        "/space/projects/archive": {"project": "Garden"},
    }
    for path, payload in writes.items():
        refused = await console.client.post(f"/console{path}", json=payload, headers={TOKEN_HEADER: "another"})
        assert _code(refused) == (403, "forbidden"), path
    # Nothing was done on the way to the refusal.
    assert await console.host.permission_store.mode("mcp_server") == "first_ask"
    assert (await console.host.usage_limit())["set"] is None


# ---------------------------------------------------------------------------
# What happened, and who waits for whom
# ---------------------------------------------------------------------------


async def test_the_timeline_and_the_waits_tell_a_task_agent_that_asks_the_user_and_goes_on(consoles, tmp_path):
    llm = ScriptedLLM([_report("waiting", "Which folder?", "call-w"), _report("ready", "sorted", "call-2")])
    console = consoles(tmp_path, llm)
    first = await console.dispatch("sort the photos")
    await wait_status(console.host, first.id, "waiting_user")

    page = (await console.get("/timeline")).json()
    assert page["success"] and page["more"] is False
    kinds = [item["kind"] for item in page["entries"]]
    assert kinds[0] == "filed" and "started" in kinds and kinds[-1] == "asked_user"
    asked = page["entries"][-1]
    assert (asked["text"], asked["commission_id"], asked["peer"]["type"]) == ("Which folder?", first.id, "user")
    waits = (await console.get("/waits")).json()
    assert [(item["kind"], item["waiter"]["commission_id"], item["target"]["type"]) for item in waits["waits"]] == [
        ("user", first.id, "user")
    ]

    # A page of one, then on from where it stopped; and only what is new from the latest on.
    one = (await console.get("/timeline", limit=1)).json()
    assert [item["kind"] for item in one["entries"]] == ["filed"] and one["more"] is True
    rest = (await console.get("/timeline", after=one["next"])).json()
    assert [item["cursor"] for item in rest["entries"]] == [item["cursor"] for item in page["entries"][1:]]
    assert (await console.get("/timeline", after=page["latest"])).json()["entries"] == []

    answered = await console.post(f"/commissions/{first.id}/answer", {"text": "the one on the desktop"})
    assert answered.status_code == 200
    await _over(console, first.id)
    new = (await console.get("/timeline", after=page["latest"])).json()
    assert [item["kind"] for item in new["entries"]][-1] == "ended"
    assert (await console.get("/waits")).json()["waits"] == []

    # Where a page that opens begins reading: so little is on record that it is the beginning.
    start = (await console.get("/timeline/start")).json()
    assert start["after"] == "" and start["latest"] == new["latest"]

    # Kept to one project: nothing of a project nothing was filed under.
    assert (await console.get("/timeline", project="Garden")).json()["entries"] == []
    assert (await console.get("/waits", project="Garden")).json()["waits"] == []


async def test_a_page_that_opens_begins_reading_a_stretch_before_the_end_and_misses_nothing_after_it(
    consoles, tmp_path, monkeypatch
):
    scripts = {f"job {n}": ScriptedLLM([_report("ready", f"done {n}")]) for n in range(4)}
    console = consoles(tmp_path, scripts_by_commission(scripts))
    for n in range(4):
        await _over(console, (await console.dispatch(f"job {n}")).id)
    whole = (await console.get("/timeline")).json()
    assert len(whole["entries"]) >= 8

    from nekopower.host.timeline import parse_cursor

    rows, _messages = parse_cursor(whole["latest"])
    back = 6
    monkeypatch.setattr(settings, "CONSOLE_TIMELINE_RECENT_EVENT_ROWS", back)
    monkeypatch.setattr(settings, "CONSOLE_TIMELINE_RECENT_MESSAGE_ROWS", 0)
    start = (await console.get("/timeline/start")).json()
    assert start["latest"] == whole["latest"]
    assert parse_cursor(start["after"]) == (rows - back, 0)
    recent = (await console.get("/timeline", after=start["after"])).json()
    # The end of the whole of it, and nothing of it twice or left out from there on.
    assert recent["entries"] and len(recent["entries"]) < len(whole["entries"])
    assert [item["cursor"] for item in recent["entries"]] == [item["cursor"] for item in whole["entries"]][-len(recent["entries"]) :]
    # More rows back than there are: from the beginning.
    monkeypatch.setattr(settings, "CONSOLE_TIMELINE_RECENT_EVENT_ROWS", rows + 50)
    assert (await console.get("/timeline/start")).json()["after"] == ""


async def test_where_a_page_begins_it_is_told_how_many_came_before_and_who_was_at_what(consoles, tmp_path, monkeypatch):
    llm = ScriptedLLM([_report("waiting", "Which folder?", "call-w")])
    console = consoles(tmp_path, llm)
    first = await console.dispatch("sort the photos")
    waiting = await wait_status(console.host, first.id, "waiting_user")
    whole = (await console.get("/timeline")).json()
    # Read from where nothing is left to read: all of it came before.
    monkeypatch.setattr(settings, "CONSOLE_TIMELINE_RECENT_EVENT_ROWS", 0)
    monkeypatch.setattr(settings, "CONSOLE_TIMELINE_RECENT_MESSAGE_ROWS", 0)
    start = (await console.get("/timeline/start")).json()
    assert start["after"] == whole["latest"] and start["before"] == len(whole["entries"])
    assert [(item["session_id"], item["commission_id"], item["look"]) for item in start["scene"]] == [
        (waiting.session_id, first.id, "waiting_user")
    ]
    monkeypatch.setattr(settings, "CONSOLE_TIMELINE_SCENE_MAX_ROWS", 0)
    assert (await console.get("/timeline/start")).json()["scene"] == []
    monkeypatch.setattr(settings, "CONSOLE_TIMELINE_BEFORE_COUNT_MAX", 2)
    assert (await console.get("/timeline/start")).json()["before"] == 2
    # From the beginning: nothing before it.
    monkeypatch.setattr(settings, "CONSOLE_TIMELINE_RECENT_EVENT_ROWS", 10_000)
    assert (await console.get("/timeline/start")).json() | {"latest": ""} == {
        "success": True, "after": "", "latest": "", "before": 0, "scene": [],
    }


async def test_what_the_user_answered_a_tool_is_on_the_timeline_before_it_goes_on(consoles, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TASK_AGENT_USAGE_LIMIT_MIN_TOKENS", 500)
    llm = ScriptedLLM(
        [
            [call("glob", {"pattern": "*.none"}), _used(900, 200), finish("tool_calls")],
            [*_report("ready", "nothing to sort", "call-2")[:1], _used(950, 100), finish("tool_calls")],
        ]
    )
    console = consoles(tmp_path, llm)
    assert (await console.post("/usage-limit", {"tokens": 1_000})).status_code == 200
    first = await console.dispatch("sort the photos")
    await wait_status(console.host, first.id, "waiting_user")
    assert (await console.post("/usage-limit", {"tokens": None})).status_code == 200
    assert (await console.post(f"/commissions/{first.id}/answer", {"text": "go on", "choice": "once"})).status_code == 200
    await _over(console, first.id)
    entries = (await console.get("/timeline")).json()["entries"]
    kinds = [item["kind"] for item in entries]
    asked = kinds.index("asked_user")
    # Asked, answered with a choice and nothing of what was typed, then at work again.
    assert kinds[asked + 1 : asked + 3] == ["answered", "started"]
    answer = entries[asked + 1]
    assert (answer["text"], answer["detail"]) == ("", {"asked_by_tool": True, "question_kind": "permission", "choice": "once"})
    assert answer["commission_id"] == first.id and answer["peer"]["type"] == "user"


async def test_what_the_user_answered_a_question_put_with_a_tool_is_on_the_timeline_with_its_words(consoles, tmp_path):
    questions = [{"id": "q1", "question": "Which folder?", "options": [{"label": "Desktop"}, {"label": "Downloads"}]}]
    llm = ScriptedLLM(
        [
            [call("ask_user_question", {"questions": questions}, "call-q"), finish("tool_calls")],
            _report("ready", "sorted", "call-2"),
        ]
    )
    console = consoles(tmp_path, llm)
    first = await console.dispatch("sort the photos")
    await wait_status(console.host, first.id, "waiting_user")
    assert (await console.post(f"/commissions/{first.id}/answer", {"text": "Downloads"})).status_code == 200
    await _over(console, first.id)
    answers = [item for item in (await console.get("/timeline")).json()["entries"] if item["kind"] == "answered"]
    assert [(item["text"], item["detail"]["question_kind"]) for item in answers] == [("Downloads", "questions")]


async def test_a_note_of_an_answer_that_could_not_be_written_does_not_keep_the_tool_from_its_answer(consoles, tmp_path, monkeypatch):
    questions = [{"id": "q1", "question": "Which folder?", "options": [{"label": "Desktop"}]}]
    llm = ScriptedLLM(
        [
            [call("ask_user_question", {"questions": questions}, "call-q"), finish("tool_calls")],
            _report("ready", "sorted", "call-2"),
        ]
    )
    console = consoles(tmp_path, llm)
    append = console.host.ledger.append_event

    async def failing(commission_id: str, kind: str, payload: Any = None) -> int:
        if kind == "tool_answered":
            raise RuntimeError("the disk is full")
        return await append(commission_id, kind, payload)

    monkeypatch.setattr(console.host.ledger, "append_event", failing)
    first = await console.dispatch("sort the photos")
    await wait_status(console.host, first.id, "waiting_user")
    assert (await console.post(f"/commissions/{first.id}/answer", {"text": "Desktop"})).status_code == 200
    done = await _over(console, first.id)
    assert done.result_summary == "sorted"
    # The tool had its answer: no error came back from it.
    [asked] = [event.payload for event in await console.host.ledger.events(first.id) if event.type == "tool_end"
               and event.payload.get("tool_name") == "ask_user_question"]
    assert asked["is_error"] is False


@pytest.mark.parametrize(
    ("path", "params", "code"),
    [
        ("/timeline", {"after": "not-a-cursor"}, "bad_cursor"),
        ("/timeline", {"after": "e1.m" + "9" * 30}, "bad_cursor"),
        ("/timeline", {"limit": "many"}, "bad_limit"),
        ("/timeline", {"limit": "0"}, "bad_limit"),
        ("/timeline", {"limit": "-3"}, "bad_limit"),
        ("/timeline", {"project": " "}, "bad_name"),
        ("/waits", {"project": " "}, "bad_name"),
    ],
)
async def test_what_is_no_cursor_and_no_number_is_refused_with_a_code(consoles, tmp_path, path, params, code):
    console = consoles(tmp_path)
    assert _code(await console.get(path, **params)) == (400, code)


# ---------------------------------------------------------------------------
# The model of each preset
# ---------------------------------------------------------------------------


async def test_the_model_of_a_preset_is_shown_set_and_put_back_from_the_page(consoles, tmp_path):
    from nekopower.host.role_models import PluginModelSlots
    from tests.unit.nekopower.test_host_role_models import (
        AGENT_SLOT, OWN_KEY, OWN_SLOT, PLAIN_SLOT, _default_slots, _manager,
    )

    manager = _manager(tmp_path, _default_slots())
    console = consoles(tmp_path, model_slots=PluginModelSlots(manager))

    view = (await console.get("/role-models")).json()
    presets = {item["preset"]: item for item in view["presets"]}
    assert list(presets)[:6] == ["boss", "worker", "scout", "inspector", "planner", "general"] or set(presets) >= {
        "boss", "worker", "scout", "inspector", "planner", "general",
    }
    assert (presets["boss"]["setting"], presets["boss"]["default"], presets["boss"]["can_inherit"]) == (None, "agent", False)
    assert (presets["scout"]["default"], presets["scout"]["can_inherit"]) == ("inherit", True)
    assert presets["scout"]["in_effect"]["follows"] == "starter"
    # The slots to choose from are those that can call tools, whatever they are named: none is written in the page.
    offered = [item["slot_id"] for item in view["slots"]]
    assert AGENT_SLOT in offered and OWN_SLOT in offered and PLAIN_SLOT not in offered
    assert view["slots_error"] is None
    # Names and codes only: no address and no key.
    assert OWN_KEY not in json.dumps(view) and "own.test" not in json.dumps(view)

    changed = (await console.post("/role-models", {"preset": "scout", "value": OWN_SLOT})).json()
    scout = next(item for item in changed["presets"] if item["preset"] == "scout")
    assert (scout["setting"], scout["in_effect"]["slot"], scout["in_effect"]["model"]) == (OWN_SLOT, OWN_SLOT, "own-model")
    assert await console.host.task_agents.role_models.store.get("scout") == OWN_SLOT

    for payload, code in (
        ({"preset": "nobody", "value": OWN_SLOT}, "unknown_preset"),
        ({"value": OWN_SLOT}, "unknown_preset"),
        ({"preset": "boss", "value": "inherit"}, "cannot_inherit"),
        ({"preset": 7, "value": OWN_SLOT}, "unknown_preset"),
        ({"preset": "scout", "value": 3}, "bad_value"),
        ({"preset": "scout", "value": " "}, "bad_value"),
        ({"preset": "scout", "value": "x" * 5000}, "slot_not_found"),
        ({"preset": "scout", "value": "slot_" + "f" * 32}, "slot_not_found"),
        ({"preset": "scout", "value": PLAIN_SLOT}, "capability_missing"),
    ):
        assert _code(await console.post("/role-models", payload)) == (400, code), payload
    # None of the refusals wrote anything.
    assert await console.host.task_agents.role_models.store.get("scout") == OWN_SLOT

    back = (await console.post("/role-models", {"preset": "scout", "value": None})).json()
    assert next(item for item in back["presets"] if item["preset"] == "scout")["setting"] is None
    followed = (await console.post("/role-models", {"preset": "scout", "value": "inherit"})).json()
    assert next(item for item in followed["presets"] if item["preset"] == "scout")["setting"] == "inherit"


async def test_without_slots_no_preset_is_set_to_one_and_an_unreadable_file_is_not_written_over(consoles, tmp_path):
    console = consoles(tmp_path)
    view = (await console.get("/role-models")).json()
    assert view["slots"] == [] and {item["preset"] for item in view["presets"]} >= {"boss", "general"}
    assert _code(await console.post("/role-models", {"preset": "general", "value": "slot_" + "a" * 32})) == (400, "no_slots")

    kept = console.host.paths.role_models_json()
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("{not json", encoding="utf-8")
    assert _code(await console.post("/role-models", {"preset": "scout", "value": "inherit"})) == (409, "role_models_unreadable")
    assert kept.read_text(encoding="utf-8") == "{not json"


# ---------------------------------------------------------------------------
# One task agent: what was said in it, what it used
# ---------------------------------------------------------------------------


async def test_what_was_said_in_a_task_agent_is_read_a_page_at_a_time_and_one_entry_whole(consoles, tmp_path):
    llm = ScriptedLLM(
        [
            [say("Looking for the photos."), call("glob", {"pattern": "*.jpg"}), _used(1_000, 50), finish("tool_calls")],
            [*_report("ready", "sorted", "call-2")[:1], _used(1_200, 20), finish("tool_calls")],
        ]
    )
    console = consoles(tmp_path, llm)
    first = await console.dispatch("sort the photos")
    done = await _over(console, first.id)

    page = (await console.get(f"/familiars/{done.session_id}/transcript")).json()
    assert page["success"] and page["readable"] is True and page["before"] is None
    roles = [item["role"] for item in page["entries"]]
    assert "user" in roles and "assistant" in roles and "tool_result" in roles
    said = next(item for item in page["entries"] if item["role"] == "assistant")
    assert said["text"] == "Looking for the photos."
    assert [(item["name"], item["arguments"]) for item in said["calls"]] == [("glob", '{"pattern":"*.jpg"}')]
    assert next(item for item in page["entries"] if item["role"] == "tool_result")["tool"] == "glob"

    # The last one alone, then those before it; the two pages are the whole of it.
    last = (await console.get(f"/familiars/{done.session_id}/transcript", limit=1)).json()
    assert [item["at"] for item in last["entries"]] == [page["entries"][-1]["at"]] and last["before"] == last["entries"][0]["at"]
    before = (await console.get(f"/familiars/{done.session_id}/transcript", before=last["before"])).json()
    assert [item["at"] for item in before["entries"]] == [item["at"] for item in page["entries"][:-1]]
    # One entry whole, by its place.
    whole = (await console.get(f"/familiars/{done.session_id}/transcript", at=said["at"])).json()
    assert [(item["at"], item["text"]) for item in whole["entries"]] == [(said["at"], "Looking for the photos.")]

    assert _code(await console.get(f"/familiars/{done.session_id}/transcript", before=said["at"] + 1)) == (400, "bad_place")
    assert _code(await console.get(f"/familiars/{done.session_id}/transcript", at=said["at"] + 1)) == (400, "bad_place")
    assert _code(await console.get(f"/familiars/{done.session_id}/transcript", before="x")) == (400, "bad_place")
    assert _code(await console.get(f"/familiars/{done.session_id}/transcript", limit="0")) == (400, "bad_limit")
    assert _code(await console.get("/familiars/nobody/transcript")) == (404, "no_familiar")


async def test_a_record_that_cannot_be_opened_now_is_said_and_not_shown_as_nothing_said(consoles, tmp_path, monkeypatch):
    from nekopower.host import records

    console = consoles(tmp_path, ScriptedLLM([_report("ready", "sorted")]))
    done = await _over(console, (await console.dispatch("sort the photos")).id)
    assert (await console.get(f"/familiars/{done.session_id}/transcript")).json()["readable"] is True

    def held(*args: Any, **kwargs: Any) -> Any:
        raise records.RecordUnreadable("the record of this session cannot be read now (PermissionError)")

    monkeypatch.setattr(records, "read_page", held)
    monkeypatch.setattr(records, "read_whole_entry", held)
    assert _code(await console.get(f"/familiars/{done.session_id}/transcript")) == (409, "record_unreadable")
    assert _code(await console.get(f"/familiars/{done.session_id}/transcript", at=0)) == (409, "record_unreadable")

    # Gone between the look and the read: nothing to read, as before.
    def gone(*args: Any, **kwargs: Any) -> Any:
        raise records.RecordError("this session has no record that can be read")

    monkeypatch.setattr(records, "read_page", gone)
    page = (await console.get(f"/familiars/{done.session_id}/transcript")).json()
    assert (page["readable"], page["reason"]) == (False, "empty")

    # It cannot even be looked at whether it is there (not permitted): said as that as well.
    from pathlib import Path

    from nekopower.host.paths import TRANSCRIPT_NAME

    stat = Path.stat

    def not_permitted(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self.name == TRANSCRIPT_NAME:
            raise PermissionError("not permitted")
        return stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", not_permitted)
    assert _code(await console.get(f"/familiars/{done.session_id}/transcript")) == (409, "record_unreadable")
    monkeypatch.setattr(Path, "stat", stat)


async def test_an_entry_too_large_to_show_whole_is_said_to_be(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path, ScriptedLLM([_report("ready", "sorted")]))
    done = await _over(console, (await console.dispatch("sort the photos")).id)
    page = (await console.get(f"/familiars/{done.session_id}/transcript")).json()
    monkeypatch.setattr(settings, "CONSOLE_TRANSCRIPT_WHOLE_ENTRY_MAX_BYTES", 10)
    refused = await console.get(f"/familiars/{done.session_id}/transcript", at=page["entries"][0]["at"])
    assert _code(refused) == (413, "too_large")


async def test_a_session_dsh_holds_or_nothing_was_said_in_says_why_there_is_nothing_to_read(consoles, tmp_path):
    console = consoles(tmp_path)
    made = (await console.post("/familiars", {"name": "Garden", "preset": "boss"})).json()
    session_id = made["session_id"]
    empty = (await console.get(f"/familiars/{session_id}/transcript")).json()
    assert (empty["readable"], empty["reason"], empty["entries"]) == (False, "empty", [])

    kept = await console.host.ledger.get_session(session_id)
    session_dir = Path(console.host.task_agents.session_dir_of(kept))
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / DSH_SESSION_NAME).write_text("{}", encoding="utf-8")
    in_dsh = (await console.get(f"/familiars/{session_id}/transcript")).json()
    assert (in_dsh["readable"], in_dsh["reason"]) == (False, "dsh")
    # Once the built-in runtime wrote something there, that is what is read.
    line = {"type": "message", "message": {"role": "user", "content": "plant the beans"}}
    (session_dir / TRANSCRIPT_NAME).write_text(json.dumps(line) + "\n", encoding="utf-8")
    read = (await console.get(f"/familiars/{session_id}/transcript")).json()
    assert read["readable"] is True and [item["text"] for item in read["entries"]] == ["plant the beans"]


async def test_what_a_task_agent_and_a_commission_used_is_read_with_what_may_be_used(consoles, tmp_path):
    llm = ScriptedLLM(
        [
            [call("glob", {"pattern": "*.jpg"}), _used(1_000, 50), finish("tool_calls")],
            [*_report("ready", "sorted", "call-2")[:1], _used(1_200, 20), finish("tool_calls")],
        ]
    )
    console = consoles(tmp_path, llm)
    done = await _over(console, (await console.dispatch("sort the photos")).id)

    of_session = (await console.get(f"/familiars/{done.session_id}/usage")).json()
    assert (of_session["session"]["requests"], of_session["session"]["tokens"]) == (2, 2_270)
    assert of_session["session"]["context_tokens"] == 1_220
    assert of_session["window"]["window"] == settings.TASK_AGENT_CONTEXT_WINDOW
    # What it finished is not in its hands any more: nothing is held against the limit.
    assert of_session["commission"] is None
    of_commission = (await console.get(f"/commissions/{done.id}/usage")).json()
    assert (of_commission["used"], of_commission["limit"], of_commission["commission"]["requests"]) == (
        2_270, settings.TASK_AGENT_USAGE_LIMIT_TOKENS, 2,
    )
    assert _code(await console.get("/familiars/nobody/usage")) == (404, "no_familiar")
    assert _code(await console.get("/commissions/nobody/usage")) == (404, "no_commission")

    # A task agent that never had a commission used nothing, and has none in hand.
    made = (await console.post("/familiars", {"name": "Garden", "preset": "boss"})).json()
    unused = (await console.get(f"/familiars/{made['session_id']}/usage")).json()
    assert (unused["session"]["tokens"], unused["commission"]) == (0, None)


# ---------------------------------------------------------------------------
# The commissions
# ---------------------------------------------------------------------------


async def test_the_commissions_are_listed_newest_first_with_what_each_asks_and_kept_to_what_is_asked_for(consoles, tmp_path):
    scripts = {
        "sort the photos": ScriptedLLM([_report("ready", "sorted")]),
        "plan the garden project": ScriptedLLM([_report("waiting", "Beans or peas?", "call-w")]),
    }
    console = consoles(tmp_path, scripts_by_commission(scripts))
    await console.host.space.touch_project("Garden")
    first = await console.dispatch("sort the photos")
    await wait_status(console.host, first.id, "done")
    second = await console.dispatch("plan the garden project")
    await wait_status(console.host, second.id, "waiting_user")

    listed = (await console.get("/commissions")).json()
    assert listed["success"] and listed["more"] is False
    rows = {item["id"]: item for item in listed["commissions"]}
    assert [item["id"] for item in listed["commissions"]] == [second.id, first.id]
    done, waiting = rows[first.id], rows[second.id]
    assert (done["status"], done["result"], done["project"], done["ask"]) == ("done", "sorted", None, None)
    assert (done["session"]["kind"], done["session"]["preset"]) == ("single", "general")
    # (In a member of its project, which does the work: a project has a leader only when there is work to hand out.)
    assert (waiting["status"], waiting["project"], waiting["session"]["kind"]) == ("waiting_user", "Garden", "line")
    assert waiting["ask"]["question"] == "Beans or peas?"
    # The one that waits has its commission in hand, with what that may use.
    in_hand = (await console.get(f"/familiars/{waiting['session_id']}/usage")).json()["commission"]
    assert (in_hand["id"], in_hand["status"], in_hand["limit"]) == (second.id, "waiting_user", settings.TASK_AGENT_USAGE_LIMIT_TOKENS)
    assert set(done["usage"]) == {"requests", "tokens"}

    async def ids(**params: Any) -> list[str]:
        return [item["id"] for item in (await console.get("/commissions", **params)).json()["commissions"]]

    assert await ids(status="done") == [first.id]
    assert await ids(status="waiting_user,queued") == [second.id]
    assert await ids(project="Garden") == [second.id]
    assert await ids(loose="1") == [first.id]
    assert await ids(project="Nowhere") == []
    # The limit is for those that are over: the one under way is listed besides them.
    one = (await console.get("/commissions", limit=1)).json()
    assert ([item["id"] for item in one["commissions"]], one["more"]) == ([second.id, first.id], False)
    # Of one project and of none are not asked for together; what is no yes or no is refused.
    assert await ids(project="Garden", loose="0") == [second.id]
    assert await ids(loose="true") == [first.id]
    assert _code(await console.get("/commissions", project="Garden", loose="1")) == (400, "bad_name")
    assert _code(await console.get("/commissions", loose="maybe")) == (400, "bad_name")

    # One that ended long ago is left out of what changed lately, and listed when asked however long ago.
    def long_ago(conn: Any) -> None:
        conn.execute("UPDATE commissions SET updated_at = updated_at - 30 * 86400 WHERE id = ?", (first.id,))

    await console.host.ledger.run(long_ago)
    assert await ids() == [second.id]
    assert await ids(days="0") == [second.id, first.id]
    assert await ids(days="60") == [second.id, first.id]
    # One under way is listed however long it has been.
    def waiting_long(conn: Any) -> None:
        conn.execute("UPDATE commissions SET updated_at = updated_at - 30 * 86400 WHERE id = ?", (second.id,))

    await console.host.ledger.run(waiting_long)
    assert await ids() == [second.id]

    for params, code in (
        ({"status": "sleeping"}, "bad_status"),
        ({"status": ","}, "bad_status"),
        ({"days": "soon"}, "bad_days"),
        ({"limit": "0"}, "bad_limit"),
        ({"project": " "}, "bad_name"),
    ):
        assert _code(await console.get("/commissions", **params)) == (400, code), params


async def test_one_that_ended_between_the_two_reads_of_the_listing_is_listed_as_it_stands(consoles, tmp_path, monkeypatch):
    llm = ScriptedLLM([_report("waiting", "Beans or peas?", "call-w"), _report("ready", "planned", "call-2")])
    console = consoles(tmp_path, llm)
    first = await console.dispatch("plan the garden")
    await wait_status(console.host, first.id, "waiting_user")
    ledger = console.host.ledger
    select = ledger.select
    reads = []

    async def answered_between(**criteria: Any) -> Any:
        found = await select(**criteria)
        reads.append(criteria)
        if len(reads) == 1:
            # Read as waiting; answered and done before the listing reads the rest.
            assert [item.status for item in found if item.id == first.id] == ["waiting_user"]
            assert (await console.post(f"/commissions/{first.id}/answer", {"text": "beans"})).status_code == 200
            await _over(console, first.id)
        return found

    monkeypatch.setattr(ledger, "select", answered_between)
    listed = (await console.get("/commissions")).json()["commissions"]
    assert [(item["id"], item["status"], item["ask"]) for item in listed] == [(first.id, "done", None)]


async def test_what_is_kept_to_is_looked_for_before_the_listing_is_cut(consoles, tmp_path, monkeypatch):
    from tests.unit.nekopower.test_host_timeline import _file, _session

    console = consoles(tmp_path)
    ledger = console.host.ledger

    async def ended(project: str | None, title: str, outcome: str = "done") -> str:
        session = await _session(ledger, title, project=project, kind="single")
        commission = await _file(ledger, session, title)
        assert (await ledger.transition(commission.id, "working")).applied
        assert (await ledger.transition(commission.id, outcome)).applied
        return commission.id

    # One that still waits for the user, older than all the rest; two of one project that are
    # over, then many of another filed after them.
    asking = await _file(ledger, await _session(ledger, "an old one that asks", project="Garden", kind="single"), "an old one that asks")
    assert (await ledger.transition(asking.id, "working")).applied
    assert (await ledger.transition(asking.id, "waiting_user")).applied

    def long_before(conn: Any) -> None:
        conn.execute("UPDATE commissions SET created_at = created_at - 1000 WHERE id = ?", (asking.id,))

    await ledger.run(long_before)
    waiting = asking.id
    old = [await ended("Garden", f"garden {n}") for n in range(2)]
    failed = await ended(None, "a loose one that failed", "failed")
    newer = [await ended("Blog", f"blog {n}") for n in range(8)]
    monkeypatch.setattr(settings, "CONSOLE_COMMISSIONS_ROWS", 3)

    async def listing(**params: Any) -> tuple[list[str], bool]:
        answer = (await console.get("/commissions", **params)).json()
        return [item["id"] for item in answer["commissions"]], answer["more"]

    # Everything: the three newest of those that are over, and more of them are left out. The one
    # that waits is listed however many came after it: it is answered from this list.
    assert await listing() == ([*newer[:-4:-1], waiting], True)
    assert await listing(status="waiting_user") == ([waiting], False)
    # Kept to the project whose commissions are the oldest: all are found, and that is all of them.
    assert await listing(project="Garden") == ([*old[::-1], waiting], False)
    assert await listing(loose="1") == ([failed], False)
    assert await listing(status="failed") == ([failed], False)
    # Kept to the one with many: the newest of it, and more of it are left out.
    assert await listing(project="Blog") == (newer[:-4:-1], True)
    assert await listing(project="Blog", limit=8) == (newer[::-1], False)
    # Nothing is at work or resting among them.
    assert await listing(status="working,resting") == ([], False)
    # A look that goes as far as it may says that there may be more beyond it.
    monkeypatch.setattr(settings, "CONSOLE_COMMISSIONS_SCAN_ROWS", 4)
    assert await listing(project="Garden") == ([waiting], True)


async def test_one_that_rests_is_not_listed_as_at_work_and_one_stuck_long_ago_is_not_listed_as_under_way(consoles, tmp_path):
    from tests.unit.nekopower.test_host_timeline import _file, _session

    console = consoles(tmp_path)
    ledger = console.host.ledger

    async def at_work(title: str) -> str:
        commission = await _file(ledger, await _session(ledger, title, project=None, kind="single"), title)
        assert (await ledger.transition(commission.id, "working")).applied
        return commission.id

    working, resting, stuck = await at_work("at work"), await at_work("resting"), await at_work("stuck")

    # Filed a while apart, so that the newest first is one order and not a matter of the clock.
    def apart(conn: Any) -> None:
        for age, commission_id in enumerate((stuck, resting, working)):
            conn.execute("UPDATE commissions SET created_at = created_at - ? WHERE id = ?", (age * 10, commission_id))

    await ledger.run(apart)
    # It rests while something it asked for is still to wake it.
    _wake, refusal = await ledger.wake_add(
        session_id=(await ledger.get(resting)).session_id, commission_id=resting, note="look at the build again",
        due_at=time.time() + 3600, pending_max=5, daily_max=5,
    )
    assert refusal == "" and await ledger.rest(resting) is not None
    assert (await ledger.transition(stuck, "blocked")).applied

    async def ids(**params: Any) -> list[str]:
        return [item["id"] for item in (await console.get("/commissions", **params)).json()["commissions"]]

    assert await ids() == [stuck, resting, working]
    # By what each is shown as: the one that rests is not at work, and the other way round.
    assert await ids(status="working") == [working]
    assert await ids(status="resting") == [resting]
    assert await ids(status="working,resting") == [resting, working]
    assert await ids(status="blocked") == [stuck]

    # Long ago, all three. Those under way are listed however long it has been; the one that
    # is stuck is over, and listed only when asked however long ago.
    def long_ago(conn: Any) -> None:
        conn.execute("UPDATE commissions SET updated_at = updated_at - 30 * 86400")

    await ledger.run(long_ago)
    assert await ids() == [resting, working]
    assert await ids(days="0") == [stuck, resting, working]


# ---------------------------------------------------------------------------
# The MCP tools
# ---------------------------------------------------------------------------


def _mcp(server: FakePluginServer) -> None:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return plugin_finish({"summary": "ok"})

    server.add_plugin(
        plugin(
            "mcp_adapter",
            "MCP Adapter",
            [
                entry("mcp_notes_search", "Search the notes", name="[notes] search", llm_result_fields=["summary"]),
                entry("mcp_notes_create", "Create a note", name="[notes] create", llm_result_fields=["summary"]),
                entry("mcp_calendar_today", "Events of today", name="[calendar] today", llm_result_fields=["summary"]),
                entry("connect_server", "Connect an MCP server", name="Connect"),
            ],
        ),
        mcp_notes_search=handler, mcp_notes_create=handler, mcp_calendar_today=handler, connect_server=handler,
    )


async def test_the_mcp_tools_are_listed_by_server_and_each_server_is_set_on_its_own(consoles, tmp_path, monkeypatch):
    server = FakePluginServer.install(monkeypatch)
    _mcp(server)
    console = consoles(tmp_path, server=server)

    view = (await console.get("/mcp")).json()
    assert (view["adapter"], view["plugins_on"], view["unreadable"]) == ("running", True, False)
    assert view["category"] == {"mode": "auto", "auto": "first_ask"}
    by_server = {item["server"]: item for item in view["servers"]}
    assert list(by_server) == ["calendar", "notes"]
    assert [tool["name"] for tool in by_server["notes"]["tools"]] == ["search", "create"]
    assert by_server["notes"]["tools"][0]["description"] == "Search the notes"
    assert (by_server["notes"]["mode"], by_server["notes"]["effective"]) == ("auto", "first_ask")
    # What can reach any server is no tool of one.
    assert "connect_server" not in json.dumps(view)

    denied = (await console.post("/mcp/permission", {"server": "notes", "mode": "deny", "expected_version": view["version"]})).json()
    by_server = {item["server"]: item for item in denied["servers"]}
    assert (by_server["notes"]["mode"], by_server["notes"]["effective"]) == ("deny", "deny")
    assert (by_server["calendar"]["mode"], by_server["calendar"]["effective"]) == ("auto", "first_ask")
    assert await console.host.permission_store.mode("mcp_server", "notes") == "deny"
    # A change made on an older view is refused, and changes nothing.
    assert _code(await console.post("/mcp/permission", {"server": "calendar", "mode": "allow", "expected_version": view["version"]})) == (409, "stale")
    assert await console.host.permission_store.mode("mcp_server", "calendar") == "first_ask"

    # All of them at once; a server with a setting of its own keeps it, one without goes by the shared one.
    shared = (await console.post("/mcp/permission", {"mode": "always_ask"})).json()
    by_server = {item["server"]: item for item in shared["servers"]}
    assert shared["category"]["mode"] == "always_ask"
    assert (by_server["notes"]["effective"], by_server["calendar"]["effective"]) == ("deny", "always_ask")
    # Back to no setting of its own.
    back = (await console.post("/mcp/permission", {"server": "notes", "mode": "auto"})).json()
    assert {item["server"]: item["effective"] for item in back["servers"]}["notes"] == "always_ask"
    # Whatever the shared one is: allowed, it is allowed as the one that was never set is.
    allowed = (await console.post("/mcp/permission", {"mode": "allow"})).json()
    assert {item["server"]: (item["mode"], item["effective"]) for item in allowed["servers"]} == {
        "calendar": ("auto", "allow"), "notes": ("auto", "allow"),
    }
    assert (await console.host.permission_store.snapshot())["mcp_servers"] == {}
    # Taken away on an older view: refused, and it keeps what it has.
    await console.post("/mcp/permission", {"server": "notes", "mode": "deny"})
    assert _code(await console.post("/mcp/permission", {"server": "notes", "mode": "auto", "expected_version": allowed["version"]})) == (409, "stale")
    assert await console.host.permission_store.mode("mcp_server", "notes") == "deny"

    for payload, code in (
        ({"server": "notes", "mode": "sometimes"}, "bad_mode"),
        ({"server": "notes"}, "bad_mode"),
        ({"server": " ", "mode": "deny"}, "bad_server"),
        ({"server": "*", "mode": "deny"}, "bad_server"),
        ({"server": 3, "mode": "deny"}, "bad_server"),
        ({"server": "notes", "mode": "deny", "expected_version": "1"}, "bad_version"),
        ({"server": "notes", "mode": "deny", "expected_version": True}, "bad_version"),
    ):
        assert _code(await console.post("/mcp/permission", payload)) == (400, code), payload


async def test_a_setting_of_the_servers_that_could_not_be_written_is_said_with_a_code(consoles, tmp_path, monkeypatch):
    server = FakePluginServer.install(monkeypatch)
    _mcp(server)
    console = consoles(tmp_path, server=server)
    assert (await console.post("/mcp/permission", {"server": "notes", "mode": "deny"})).status_code == 200

    def full(*args: Any) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr(console.host.permission_store, "_write", full)
    # Taken away, set, and the one all share: each says that it was not saved.
    for payload in ({"server": "notes", "mode": "auto"}, {"server": "calendar", "mode": "allow"}, {"mode": "always_ask"}):
        assert _code(await console.post("/mcp/permission", payload)) == (409, "permissions_not_saved"), payload
    # What was to be taken away is still there, in both places.
    assert await console.host.permission_store.mode("mcp_server", "notes") == "deny"


async def test_taking_the_setting_of_a_server_away_ends_what_was_approved_for_those_that_share_the_setting(tmp_path):
    from nekopower.host.permissions import PermissionStore

    store = PermissionStore(tmp_path / "permissions.json")
    await store.set_mode("mcp_server", "always_ask")
    await store.set_mode("mcp_server", "allow", "notes")
    shared, own = store.revision("mcp_server"), store.revision("mcp_server", "notes")
    version = store.version
    # One that has none of its own: nothing changes, nothing is written.
    assert await store.clear_mcp_server("calendar") and store.version == version
    # From allowed to asked every time: what was approved under the looser rule counts no more.
    assert await store.clear_mcp_server("notes")
    assert store.version == version + 1
    assert await store.mode("mcp_server", "notes") == "always_ask"
    assert store.revision("mcp_server", "notes") == store.revision("mcp_server") == store.version
    assert store.revision("mcp_server") not in (shared, own)
    # Kept on the disk as it is now.
    assert (await PermissionStore(tmp_path / "permissions.json").snapshot())["mcp_servers"] == {}

    # Approved while it went by the shared setting, then forbidden, then back to the shared
    # setting: the approval carries the generation it was given in (an approval counts while
    # the generation is the one it carries), and that must not be the generation again.
    approved_in = store.revision("mcp_server", "notes")
    await store.set_mode("mcp_server", "deny", "notes")
    assert store.revision("mcp_server", "notes") != approved_in
    assert await store.clear_mcp_server("notes")
    assert store.revision("mcp_server", "notes") == store.version != approved_in


async def test_an_approval_taken_back_by_forbidding_a_server_is_not_given_back_when_its_setting_is_taken_away(tmp_path):
    from nekopower.host.permissions import GrantScope, PermissionGate, PermissionStore

    async def once(commission_id: str, ask: Any) -> str:
        return "once"

    store = PermissionStore(tmp_path / "permissions.json")
    gate = PermissionGate(store, once)
    # Approved while the server goes by the shared setting, which asks the first time.
    approved = await gate.check("a", "mcp_server", key="notes", scope=GrantScope("notes"), hold_once=True)
    assert approved.allow and gate.grant_active(approved.grant_id)
    await store.set_mode("mcp_server", "deny", "notes")
    assert not gate.grant_active(approved.grant_id)
    # Back to the shared setting: it is asked about again, the old approval does not count.
    assert await store.clear_mcp_server("notes")
    assert await store.mode("mcp_server", "notes") == "first_ask"
    assert not gate.grant_active(approved.grant_id)


async def test_an_approval_for_the_session_is_asked_for_again_after_the_server_was_forbidden_and_set_back(tmp_path):
    from nekopower.host.permissions import GrantScope, PermissionGate, PermissionStore

    asked: list[Any] = []

    async def for_the_session(commission_id: str, ask: Any) -> str:
        asked.append(ask)
        return "session"

    store = PermissionStore(tmp_path / "permissions.json")
    gate = PermissionGate(store, for_the_session)
    scope = GrantScope("notes")

    async def use() -> bool:
        return (await gate.check("a", "mcp_server", key="notes", scope=scope)).allow

    # Asked the first time and allowed for the session: not asked the second time.
    assert await use() and await use()
    assert len(asked) == 1
    await store.set_mode("mcp_server", "deny", "notes")
    assert not await use()
    assert len(asked) == 1
    # Set back to go by the shared setting: what was allowed for the session before it was
    # forbidden does not count, the user is asked again.
    assert await store.clear_mcp_server("notes")
    assert await use()
    assert len(asked) == 2
    # And that answer holds for the session, as any does.
    assert await use()
    assert len(asked) == 2


async def test_a_setting_that_could_not_be_taken_away_on_the_disk_is_still_there_to_take_away(tmp_path, monkeypatch):
    from nekopower.host.permissions import PermissionStore

    path = tmp_path / "permissions.json"
    store = PermissionStore(path)
    await store.set_mode("mcp_server", "deny")
    await store.set_mode("mcp_server", "allow", "notes")
    version, generation = store.version, store.revision("mcp_server")
    write = store._write
    failures = [OSError("the disk is full")]

    def failing_once(*args: Any) -> None:
        if failures:
            raise failures.pop()
        write(*args)

    monkeypatch.setattr(store, "_write", failing_once)
    with pytest.raises(OSError):
        await store.clear_mcp_server("notes")
    # Nothing changed, here or on the disk: it is allowed as before, and nothing approved ends.
    assert await store.mode("mcp_server", "notes") == "allow"
    assert (store.version, store.revision("mcp_server")) == (version, generation)
    assert (await PermissionStore(path).snapshot())["mcp_servers"] == {"notes": "allow"}
    # Asked again once the disk can be written: taken away in both places.
    assert await store.clear_mcp_server("notes")
    assert await store.mode("mcp_server", "notes") == "deny"
    assert (await PermissionStore(path).snapshot())["mcp_servers"] == {}


async def test_a_server_that_is_set_and_has_no_tool_now_is_still_listed_and_a_missing_adapter_is_said(consoles, tmp_path, monkeypatch):
    server = FakePluginServer.install(monkeypatch)
    console = consoles(tmp_path, server=server)
    await console.host.permission_store.set_mode("mcp_server", "deny", "notes")
    view = (await console.get("/mcp")).json()
    assert view["adapter"] == "missing"
    assert view["servers"] == [{"server": "notes", "mode": "deny", "effective": "deny", "tools": []}]


async def test_the_setting_for_calls_that_name_no_server_is_not_listed_as_a_server(consoles, tmp_path):
    console = consoles(tmp_path)
    kept = console.host.paths.permissions_json()
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps({"version": 1, "categories": {}, "mcp_servers": {"*": "deny", "notes": "allow"}}), encoding="utf-8")
    view = (await console.get("/mcp")).json()
    assert [(item["server"], item["mode"]) for item in view["servers"]] == [("notes", "allow")]


async def test_a_permission_file_that_cannot_be_read_is_said_as_such_and_not_written_over(consoles, tmp_path):
    console = consoles(tmp_path)
    kept = console.host.paths.permissions_json()
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("{not json", encoding="utf-8")
    assert (await console.get("/mcp")).json()["unreadable"] is True
    assert _code(await console.post("/mcp/permission", {"server": "notes", "mode": "allow"})) == (409, "permissions_unreadable")
    assert _code(await console.post("/mcp/permission", {"mode": "allow"})) == (409, "permissions_unreadable")
    assert kept.read_text(encoding="utf-8") == "{not json"


async def test_without_a_plugin_server_the_settings_of_the_servers_are_shown_all_the_same(consoles, tmp_path):
    console = consoles(tmp_path)
    view = (await console.get("/mcp")).json()
    assert (view["adapter"], view["servers"], view["category"]["mode"]) == ("missing", [], "auto")


# ---------------------------------------------------------------------------
# How much one commission may use
# ---------------------------------------------------------------------------


async def test_the_usage_limit_is_shown_set_within_its_bounds_and_put_back(consoles, tmp_path):
    console = consoles(tmp_path)
    least, most = settings.TASK_AGENT_USAGE_LIMIT_MIN_TOKENS, settings.TASK_AGENT_USAGE_LIMIT_MAX_TOKENS
    default = settings.TASK_AGENT_USAGE_LIMIT_TOKENS
    assert (await console.get("/usage-limit")).json() == {
        "success": True, "tokens": default, "set": None, "default": default, "min": least, "max": most,
    }
    changed = (await console.post("/usage-limit", {"tokens": 800_000})).json()
    assert (changed["tokens"], changed["set"], changed["default"]) == (800_000, 800_000, default)
    assert json.loads(console.host.paths.usage_limit_json().read_text(encoding="utf-8"))["tokens"] == 800_000
    for payload, code in (
        ({"tokens": least - 1}, "out_of_range"),
        ({"tokens": most + 1}, "out_of_range"),
        ({"tokens": 0}, "out_of_range"),
        ({"tokens": -5}, "out_of_range"),
        ({"tokens": "800000"}, "not_a_number"),
        ({"tokens": 800_000.5}, "not_a_number"),
        ({"tokens": True}, "not_a_number"),
    ):
        assert _code(await console.post("/usage-limit", payload)) == (400, code), payload
    assert (await console.get("/usage-limit")).json()["set"] == 800_000
    for bound in (least, most):
        assert (await console.post("/usage-limit", {"tokens": bound})).json()["set"] == bound
    back = (await console.post("/usage-limit", {"tokens": None})).json()
    assert (back["tokens"], back["set"]) == (default, None)
    assert not console.host.paths.usage_limit_json().exists()


async def test_a_limit_that_could_not_be_written_is_said_with_a_code_and_stays_what_it_was(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path)
    assert (await console.post("/usage-limit", {"tokens": 800_000})).json()["set"] == 800_000
    # A payload that names no limit asks for nothing: it does not put the default back.
    for payload in ({}, {"token": 900_000}):
        assert _code(await console.post("/usage-limit", payload)) == (400, "not_a_number"), payload
    assert (await console.get("/usage-limit")).json()["set"] == 800_000

    def full(*args: Any) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr(console.host.usage_limit_store, "_write", full)
    assert _code(await console.post("/usage-limit", {"tokens": 900_000})) == (409, "limit_not_saved")
    assert _code(await console.post("/usage-limit", {"tokens": None})) == (409, "limit_not_saved")
    assert (await console.get("/usage-limit")).json()["set"] == 800_000


async def test_a_limit_file_that_holds_no_such_number_is_read_as_nothing_set(consoles, tmp_path):
    console = consoles(tmp_path)
    kept = console.host.paths.usage_limit_json()
    kept.parent.mkdir(parents=True, exist_ok=True)
    from nekopower.host.usage_limit import UsageLimitStore

    for content in ("{not json", '{"tokens": 12}', '{"tokens": "many"}', '{"tokens": true}', "[]"):
        kept.write_text(content, encoding="utf-8")
        # A store reads its file once: one that begins with this file each time.
        assert await UsageLimitStore(kept).get() is None, content
    view = (await console.get("/usage-limit")).json()
    assert (view["set"], view["tokens"]) == (None, settings.TASK_AGENT_USAGE_LIMIT_TOKENS)


async def test_the_limit_is_read_from_its_file_once_and_what_is_set_since_holds(tmp_path, monkeypatch):
    from nekopower.host.usage_limit import UsageLimitStore

    kept = tmp_path / "usage_limit.json"
    kept.write_text('{"version": 1, "tokens": 2000000}', encoding="utf-8")
    store = UsageLimitStore(kept)
    reads = []
    read = store._read
    monkeypatch.setattr(store, "_read", lambda *args: (reads.append(1), read(*args))[1])
    assert [await store.get() for _ in range(3)] == [2_000_000] * 3
    assert len(reads) == 1
    await store.put(3_000_000)
    assert await store.get() == 3_000_000
    await store.put(None)
    assert await store.get() is None and not kept.exists()
    assert len(reads) == 1
    # What another store begins with is what was written.
    await store.put(4_000_000)
    assert await UsageLimitStore(kept).get() == 4_000_000


async def test_the_limit_the_user_set_is_where_a_commission_stops_to_ask(consoles, tmp_path, monkeypatch):
    # Bounds small enough for a commission of two requests to reach.
    monkeypatch.setattr(settings, "TASK_AGENT_USAGE_LIMIT_MIN_TOKENS", 500)
    llm = ScriptedLLM(
        [
            [call("glob", {"pattern": "*.none"}), _used(900, 200), finish("tool_calls")],
            [*_report("ready", "nothing to sort", "call-2")[:1], _used(950, 100), finish("tool_calls")],
        ]
    )
    console = consoles(tmp_path, llm)
    assert (await console.post("/usage-limit", {"tokens": 1_000})).status_code == 200

    first = await console.dispatch("sort the photos")
    waiting = await wait_status(console.host, first.id, "waiting_user")
    question = waiting.question()
    # It used 1.1k of the 1k the user set: asked, like a permission, for as much again.
    assert (question["category"], question["detail"]) == ("usage_limit", {"used": "1k", "more": "1k"})
    assert (await console.get(f"/commissions/{first.id}/usage")).json()["limit"] == 1_000
    await console.host.task_agents.settle()
    assert len(llm.requests) == 1

    # Put back to the default, far away: allowed once, it goes on and is not asked again.
    assert (await console.post("/usage-limit", {"tokens": None})).status_code == 200
    assert (await console.post(f"/commissions/{first.id}/answer", {"text": "go on", "choice": "once"})).status_code == 200
    done = await wait_status(console.host, first.id, "done")
    assert done.result_summary == "nothing to sort" and len(llm.requests) == 2


async def test_with_nothing_set_a_commission_goes_by_the_setting_of_the_build(consoles, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TASK_AGENT_USAGE_LIMIT_TOKENS", 1_000)
    llm = ScriptedLLM(
        [
            [call("glob", {"pattern": "*.none"}), _used(900, 200), finish("tool_calls")],
            [*_report("ready", "nothing to sort", "call-2")[:1], _used(950, 100), finish("tool_calls")],
        ]
    )
    console = consoles(tmp_path, llm)
    first = await console.dispatch("sort the photos")
    waiting = await wait_status(console.host, first.id, "waiting_user")
    assert waiting.question()["category"] == "usage_limit"
    view = (await console.get("/usage-limit")).json()
    assert (view["tokens"], view["set"], view["default"]) == (1_000, None, 1_000)
    await console.host.abort(first.id)


# ---------------------------------------------------------------------------
# Putting a project away
# ---------------------------------------------------------------------------


async def test_a_project_is_put_away_from_the_page_and_stays_while_something_is_at_work_in_it(consoles, tmp_path):
    llm = ScriptedLLM([_report("waiting", "Beans or peas?", "call-w"), _report("ready", "planned", "call-2")])
    console = consoles(tmp_path, llm)
    made = (await console.post("/familiars", {"name": "Garden", "preset": "boss", "rules": "Water in the morning."})).json()
    assert made["project"]["name"] == "Garden"

    first = await console.dispatch("plan the garden project")
    await wait_status(console.host, first.id, "waiting_user")
    busy = await console.post("/space/projects/archive", {"project": "Garden"})
    assert (busy.status_code, busy.json()["detail"]) == (409, {"code": "project_busy", "reason": "at_work"})
    assert (await console.host.space.get_project("Garden")).status != "archived"

    assert (await console.post(f"/commissions/{first.id}/answer", {"text": "beans"})).status_code == 200
    await wait_status(console.host, first.id, "done")
    await console.host.task_agents.settle()
    await _at_rest(console.host, made["session_id"])
    archived = await console.post("/space/projects/archive", {"project": "Garden"})
    assert archived.status_code == 200, archived.json()
    project = archived.json()["project"]
    assert (project["name"], project["status"]) == ("Garden", "archived")
    listed = (await console.get("/space/projects")).json()["projects"]
    assert [(item["name"], item["status"]) for item in listed if item["name"] == "Garden"] == [("Garden", "archived")]
    # Nothing was removed: its rules are read where it was put away, and its session is still on record.
    year = project["archive_year"]
    assert (await console.get("/space/rules", project=project["directory"], archive_year=year)).json()["text"].strip() == "Water in the morning."
    assert await console.host.ledger.get_session(made["session_id"]) is not None

    # Put away already, never there, the one for commissions of no project, no name at all.
    assert _code(await console.post("/space/projects/archive", {"project": "Garden"})) == (404, "no_project")
    assert _code(await console.post("/space/projects/archive", {"project": "Nowhere"})) == (404, "no_project")
    assert _code(await console.post("/space/projects/archive", {"project": "_default"})) == (404, "no_project")
    assert _code(await console.post("/space/projects/archive", {})) == (400, "bad_name")
    assert _code(await console.post("/space/projects/archive", {"project": " "})) == (400, "bad_name")


async def test_a_project_taken_up_again_while_it_is_being_put_away_stays(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path)
    (await console.post("/familiars", {"name": "Garden", "preset": "boss"})).json()
    looked = console.host._project_busy

    async def taken_up_after_the_look(project: str) -> Any:
        why = await looked(project)
        # A commission of it that had failed is taken up again right after the look. It was
        # counted for the project before: all it leaves is that the project is in use again.
        monkeypatch.setattr(console.host.space, "_clock", lambda: time.time() + 60)
        await console.host.space.use_project(project)
        return why

    monkeypatch.setattr(console.host, "_project_busy", taken_up_after_the_look)
    refused = await console.post("/space/projects/archive", {"project": "Garden"})
    assert (refused.status_code, refused.json()["detail"]) == (409, {"code": "project_busy", "reason": "at_work"})
    assert (await console.host.space.get_project("Garden")).status != "archived"


async def test_what_a_task_agent_has_in_hand_is_what_it_is_at_and_not_what_waits_for_its_turn(consoles, tmp_path):
    from tests.unit.nekopower.test_host_timeline import _file, _session

    console = consoles(tmp_path)
    ledger = console.host.ledger
    session = await _session(ledger, "Blog", project=None, kind="single")
    at_work = await _file(ledger, session, "move the blog")
    assert (await ledger.transition(at_work.id, "working")).applied
    waiting = await _file(ledger, session, "then the photos")

    def apart(conn: Any) -> None:
        conn.execute("UPDATE commissions SET created_at = created_at + 10 WHERE id = ?", (waiting.id,))

    await ledger.run(apart)

    async def in_hand() -> Any:
        return (await console.get(f"/familiars/{session.id}/usage")).json()["commission"]

    # The newer one waits for its turn: the one it is at is in its hands.
    assert ((await in_hand())["id"], (await in_hand())["status"]) == (at_work.id, "working")
    # The newer one is called off: still the one it is at.
    assert (await ledger.transition(waiting.id, "cancelled")).applied
    assert (await in_hand())["id"] == at_work.id
    # Only one that waits for its turn: that one, and none once nothing is under way.
    assert (await ledger.transition(at_work.id, "done")).applied
    next_one = await _file(ledger, session, "and the notes")
    assert ((await in_hand())["id"], (await in_hand())["status"]) == (next_one.id, "queued")
    # Two that wait for their turn: the one that came first is next.
    after_it = await _file(ledger, session, "and the letters")

    def later(conn: Any) -> None:
        conn.execute("UPDATE commissions SET created_at = created_at + 20 WHERE id = ?", (after_it.id,))

    await ledger.run(later)
    assert (await in_hand())["id"] == next_one.id
    assert (await ledger.transition(next_one.id, "cancelled")).applied
    assert (await in_hand())["id"] == after_it.id
    assert (await ledger.transition(after_it.id, "cancelled")).applied
    assert await in_hand() is None


async def test_a_project_whose_directory_cannot_be_moved_stays_and_the_page_is_told(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path)
    (await console.post("/familiars", {"name": "Garden", "preset": "boss"})).json()

    async def not_moved(name: str, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(console.host.space, "archive_project", not_moved)
    assert _code(await console.post("/space/projects/archive", {"project": "Garden"})) == (409, "archive_failed")

    # Not moved because the host put it away by itself meanwhile: it is not there to put away.
    read = console.host.space.read_project
    looks = []

    async def gone_at_the_second_look(name: str) -> Any:
        looks.append(name)
        return (None, True) if len(looks) > 1 else await read(name)

    monkeypatch.setattr(console.host.space, "read_project", gone_at_the_second_look)
    assert _code(await console.post("/space/projects/archive", {"project": "Garden"})) == (404, "no_project")

    # Not moved, and its record cannot be read at the second look: said as that, not as a file held open.
    looks.clear()

    async def unreadable_at_the_second_look(name: str) -> Any:
        looks.append(name)
        project, readable = await read(name)
        return (project, False) if len(looks) > 1 else (project, readable)

    monkeypatch.setattr(console.host.space, "read_project", unreadable_at_the_second_look)
    refused = await console.post("/space/projects/archive", {"project": "Garden"})
    assert refused.status_code != 200 and refused.json()["detail"]["code"] not in ("archive_failed", "no_project")


# ---------------------------------------------------------------------------
# Reading at once what was read one at a time
# ---------------------------------------------------------------------------


async def test_the_sessions_of_a_page_of_commissions_are_read_at_once(consoles, tmp_path, monkeypatch):
    from tests.unit.nekopower.test_host_timeline import _file, _session

    console = consoles(tmp_path)
    ledger = console.host.ledger
    sessions = [await _session(ledger, f"s{n}", project=None, kind="single") for n in range(3)]
    for session in sessions:
        await _file(ledger, session, f"job of {session.name}")

    async def one_at_a_time(session_id: Any) -> Any:
        raise AssertionError("a session read one at a time")

    monkeypatch.setattr(ledger, "get_session", one_at_a_time)
    listed = (await console.get("/commissions")).json()["commissions"]
    assert sorted(item["session"]["name"] for item in listed) == ["s0", "s1", "s2"]


async def test_sessions_are_read_by_their_ids_those_not_on_record_left_out(consoles, tmp_path):
    from tests.unit.nekopower.test_host_timeline import _session

    console = consoles(tmp_path)
    ledger = console.host.ledger
    one, two = await _session(ledger, "one"), await _session(ledger, "two")
    # More than one statement takes at once: read in parts, each once.
    # Those not on record sort before them: the two are in the second part.
    wanted = [one.id, "!nobody", two.id, one.id, *(f"!missing-{n:03}" for n in range(600))]
    found = await ledger.get_sessions(wanted)
    assert sorted(found) == sorted([one.id, two.id]) and found[two.id].name == "two"
    assert await ledger.get_sessions([]) == {}
    assert await ledger.get_sessions(["", None]) == {}


async def test_what_a_task_agent_has_in_hand_and_what_it_used_are_read_at_one_moment(consoles, tmp_path, monkeypatch):
    from tests.unit.nekopower.test_host_timeline import _file, _session

    console = consoles(tmp_path)
    ledger = console.host.ledger
    session = await _session(ledger, "Blog", project=None, kind="single")
    at_work = await _file(ledger, session, "move the blog")
    assert (await ledger.transition(at_work.id, "working")).applied

    async def apart(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("read apart from what it has in hand")

    # Neither what is under way nor what it used is read by itself.
    monkeypatch.setattr(ledger, "select", apart)
    monkeypatch.setattr(ledger, "usage_standing", apart)
    monkeypatch.setattr(ledger, "get_session", apart)
    usage = (await console.get(f"/familiars/{session.id}/usage")).json()
    assert (usage["commission"]["id"], usage["commission"]["status"]) == (at_work.id, "working")
    assert usage["session"]["tokens"] == 0
    # One filed before it that waits for its turn is not what it has in hand.
    other = await _session(ledger, "Notes", project=None, kind="single")
    waiting = await _file(ledger, other, "first, and waiting")
    later = await _file(ledger, other, "later, and at work")
    assert (await ledger.transition(later.id, "working")).applied
    found = await ledger.usage_in_hand(other.id)
    assert (found[1].id, found[2].id) == (later.id, later.id) and waiting.id != later.id
    # What the user allowed the work it has in hand is what it may use.
    await ledger.allow_usage(later.id, 700)
    assert (await console.get(f"/familiars/{other.id}/usage")).json()["commission"]["limit"] == 700
    assert await ledger.usage_in_hand("nobody") is None
    assert _code(await console.get("/familiars/nobody/usage")) == (404, "no_familiar")


async def test_those_waiting_their_turn_are_all_listed_unless_a_most_is_set(consoles, tmp_path, monkeypatch):
    from tests.unit.nekopower.test_host_timeline import _file, _session

    console = consoles(tmp_path)
    ledger = console.host.ledger
    session = await _session(ledger, "Blog", project=None, kind="single")
    at_work = await _file(ledger, session, "move the blog")
    assert (await ledger.transition(at_work.id, "working")).applied
    queued = [await _file(ledger, session, f"then {n}") for n in range(3)]

    def apart(conn: Any) -> None:
        for n, item in enumerate(queued):
            conn.execute("UPDATE commissions SET created_at = created_at + ? WHERE id = ?", (10 + n, item.id))

    await ledger.run(apart)
    every = (await console.get("/commissions")).json()
    assert len(every["commissions"]) == 4 and every["more"] is False
    monkeypatch.setattr(settings, "CONSOLE_COMMISSIONS_QUEUED_MAX", 1)
    cut = (await console.get("/commissions")).json()
    # The newest of those waiting, and the one at work whatever the most.
    assert [item["id"] for item in cut["commissions"]] == [queued[2].id, at_work.id] and cut["more"] is True
    monkeypatch.setattr(settings, "CONSOLE_COMMISSIONS_QUEUED_MAX", 3)
    assert (await console.get("/commissions")).json()["more"] is False
