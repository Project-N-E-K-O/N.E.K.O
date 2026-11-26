"""What a page hands over when the user answers a pending question with a button.

A page that shows a question as a card answers it with more than a sentence:
which options of ``ask_user_question`` were picked, yes or no to a plugin's
request for confirmation, a sentence added to an approval. These are the
host's side of that, and what such a page is given to draw the card with.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.agent_server.api_console import TOKEN_HEADER, build_console_router
from nekopower.host.gateway import CONFIRMATION_ANSWERS, CapabilityGateway
from nekopower.host.reports import page_question
from nekopower.host.task_agents import answer_words, build_answers

TOKEN = "token-of-this-launch"
HEADERS = {TOKEN_HEADER: TOKEN}

QUESTIONS = [
    {"id": "q", "question": "Deploy where?", "options": [{"label": "Vercel"}, {"label": "Not now"}]},
    # The model gave both the same id, and both offer "Not now".
    {"id": "q", "question": "Add analytics?", "options": [{"label": "Yes"}, {"label": "Not now"}]},
    {"id": "tags", "question": "Which tags?", "options": ["a", "b", "c"], "multi_select": True},
]


def _selected(answers: dict[str, Any]) -> list[list[str]]:
    return [answer["selected"] for answer in answers["answers"]]


def test_what_was_picked_with_a_button_is_taken_by_where_each_question_stands():
    answers = build_answers(QUESTIONS, "", {"0": ["Vercel"], "1": ["Not now"], "2": ["c", "a"]})

    # Each question gets its own, though two of them share an id and a label; in the question's order.
    assert _selected(answers) == [["Vercel"], ["Not now"], ["a", "c"]]
    assert [answer["id"] for answer in answers["answers"]] == ["q", "q", "tags"]


def test_words_added_to_a_pick_select_nothing_more():
    answers = build_answers(QUESTIONS, "Not now, and not Vercel either: a, b", {"1": ["Yes"]})

    # Only what was picked: the words hold labels of options that were not.
    assert _selected(answers) == [[], ["Yes"], []]
    assert {answer["custom"] for answer in answers["answers"]} == {"Not now, and not Vercel either: a, b"}


def test_a_pick_is_one_of_the_questions_options_and_one_only_where_one_is_asked_for():
    answers = build_answers(QUESTIONS, "", {"0": ["Vercel", "Not now", "Netlify"], "2": "a", "7": ["x"]})

    assert _selected(answers) == [["Vercel"], [], []]


def test_a_label_is_the_same_with_or_without_the_blanks_around_it():
    # The pages show a label trimmed and hand it back so; the model wrote it with a blank after it.
    questions = [{"id": "go", "question": "Go on?", "options": [{"label": "Yes "}, {"label": " No"}]}]

    answers = build_answers(questions, "", {"0": ["Yes"]})

    # Given to the task agent as the question has it.
    assert _selected(answers) == [["Yes "]]
    assert _selected(build_answers(questions, "", {"0": [" No  "]})) == [[" No"]]
    assert _selected(build_answers(questions, "", {"0": ["Ye"]})) == [[]]


def test_an_answer_in_words_alone_is_read_as_it_always_was():
    answers = build_answers(QUESTIONS, "vercel please, tags a and b")

    # Looked for in the words, with all that brings: the "c" of "vercel" picks the tag "c".
    assert _selected(answers) == [["Vercel"], [], ["a", "b", "c"]]


@pytest.mark.parametrize(
    "answer, words",
    [
        ({"text": " by month ", "selected": {"0": ["Vercel"], "2": ["a", "c"]}}, "Vercel; a; c; by month"),
        ({"text": "", "selected": {"1": ["Yes"]}}, "Yes"),
        ({"text": "only words", "selected": None}, "only words"),
        ({"text": "", "selected": {"0": "Vercel", "1": [3, ""]}}, ""),
    ],
)
def test_an_answer_is_put_on_record_as_what_was_picked_and_then_the_words(answer, words):
    assert answer_words(answer) == words


# ---------------------------------------------------------------------------
# What a card is drawn with
# ---------------------------------------------------------------------------


def test_a_request_for_approval_is_said_in_one_line_of_the_commissions_language():
    question = {"kind": "permission", "id": "q1", "category": "run_command", "detail": {"command": "npm run build"},
                "options": ["always", "session", "once", "deny"]}

    card = page_question(question, "zh-CN")

    assert card is not None and card is not question
    assert "npm run build" in card["line"] and "运行命令" in card["line"]
    # The answers on offer are the card's buttons, not part of the line.
    assert "始终" not in card["line"]
    assert card["line_cut"] is False
    assert {key: card[key] for key in question} == question
    assert "line" not in question


def test_a_command_too_long_for_the_line_is_said_to_be_cut():
    long = "echo " + " ".join(f"part-{number}" for number in range(4000))

    card = page_question({"kind": "permission", "category": "run_command", "detail": {"command": long}, "options": ["once", "deny"]}, "en")

    assert card["line_cut"] is True
    assert len(card["line"]) < len(long)
    # All of it is still there for the card to show whole.
    assert card["detail"]["command"] == long


@pytest.mark.parametrize(
    "question",
    [
        {"kind": "confirmation", "id": "q2", "question": "Send it to the group?"},
        {"kind": "questions", "id": "q3", "questions": QUESTIONS},
        {"kind": "report", "id": "q4", "question": "Which file?", "details": "three have that name"},
    ],
)
def test_any_other_question_is_handed_over_as_it_is(question):
    assert page_question(question, "en") == question


@pytest.mark.parametrize("nothing", [None, "", [], "not a question"])
def test_no_question_no_card(nothing):
    assert page_question(nothing, "en") is None


# ---------------------------------------------------------------------------
# A plugin's request for confirmation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer, told",
    [
        ({"text": "", "choice": "yes"}, CONFIRMATION_ANSWERS["yes"]),
        ({"text": "  but only the first two  ", "choice": "yes"}, CONFIRMATION_ANSWERS["yes"] + " but only the first two"),
        ({"text": "", "choice": "no"}, CONFIRMATION_ANSWERS["no"]),
        # In words, as it is answered in the main dialogue: the words and nothing put before them.
        ({"text": " go on ", "choice": None}, "go on"),
        # A choice of another kind of question says nothing here.
        ({"text": "fine", "choice": "once"}, "fine"),
    ],
)
async def test_yes_or_no_pressed_on_a_page_is_told_to_the_task_agent_in_words(answer, told):
    asked: list[dict[str, Any]] = []

    async def ask_user(commission_id: str, question: dict[str, Any]) -> dict[str, Any]:
        asked.append(question)
        return answer

    gateway = SimpleNamespace(_ask_user=ask_user)
    capability = SimpleNamespace(id="plugin:chat:send", title="Send a message")

    said = await CapabilityGateway._confirm_with_user(gateway, SimpleNamespace(id="c1"), capability, "Send it to the group?")

    assert said == told
    assert asked[0]["kind"] == "confirmation"
    assert CONFIRMATION_ANSWERS["yes"] != CONFIRMATION_ANSWERS["no"]


# ---------------------------------------------------------------------------
# The route that takes an answer, and the one that lists what is asked
# ---------------------------------------------------------------------------


class _Host:
    """Stands in for the host: takes answers and sentences, and says what waits."""

    def __init__(
        self,
        *,
        takes_answer: bool = True,
        takes_note: "bool | Exception | asyncio.Event" = True,
        waiting: list[Any] | None = None,
        asks: str | None = "questions",
    ) -> None:
        self.takes_answer, self.takes_note = takes_answer, takes_note
        # The kind of question the commission waits with; None for no such commission.
        self.asks = asks
        self.answers: list[tuple[str, str, dict[str, Any]]] = []
        self.told: list[tuple[str, str]] = []
        self.ledger = SimpleNamespace(select=self._select, get=self._get)
        self._waiting = waiting or []
        self.selected_with: dict[str, Any] = {}
        self.passed_on: list[str] = []

    async def answer(self, commission_id: str, text: str, **given: Any) -> bool:
        self.answers.append((commission_id, text, given))
        return self.takes_answer

    async def tell(self, commission_id: str, text: str) -> bool:
        self.told.append((commission_id, text))
        if isinstance(self.takes_note, Exception):
            raise self.takes_note
        if isinstance(self.takes_note, asyncio.Event):
            # Slow to pass on: until the test lets it through.
            await self.takes_note.wait()
            self.passed_on.append(text)
            return True
        return self.takes_note

    async def _get(self, commission_id: str) -> Any:
        if self.asks is None:
            return None
        if self.asks == "nothing":
            # There, and not asking anything any more.
            return SimpleNamespace(id=commission_id, question=lambda: None)
        return SimpleNamespace(id=commission_id, question=lambda: {"kind": self.asks, "id": "q1"})

    async def _select(self, **criteria: Any) -> list[Any]:
        self.selected_with = criteria
        return list(self._waiting)


def _client(host: _Host) -> httpx.AsyncClient:
    async def get_host() -> Any:
        return host

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: TOKEN))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test")


async def test_a_sentence_added_to_an_approval_is_passed_on_once_the_answer_is_in():
    host = _Host()
    async with _client(host) as client:
        answer = await client.post(
            "/console/commissions/c1/answer", headers=HEADERS,
            json={"choice": "deny", "question_id": "q1", "note": "  use pnpm instead  "},
        )

    assert answer.json() == {"success": True, "note_taken": True}
    assert host.answers == [("c1", "", {"choice": "deny", "question_id": "q1", "selected": None})]
    assert host.told == [("c1", "use pnpm instead")]


async def test_a_note_nothing_took_is_said_and_is_no_error():
    host = _Host(takes_note=False)
    async with _client(host) as client:
        answer = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"choice": "once", "note": "careful"})

    assert answer.status_code == 200
    assert answer.json() == {"success": True, "note_taken": False}


async def test_a_note_that_could_not_be_passed_on_leaves_the_answer_taken():
    # The answer is in by then: an error here would have the page send it again, to be turned away.
    host = _Host(takes_note=OSError("the ledger could not be written"))
    async with _client(host) as client:
        answer = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"choice": "once", "note": "careful"})

    assert answer.status_code == 200
    assert answer.json() == {"success": True, "note_taken": False}
    assert host.told == [("c1", "careful")]


async def test_a_note_slow_to_pass_on_does_not_keep_the_answer_waiting(monkeypatch):
    from app.agent_server import api_console

    # The page's server gives up on a request after a while and would take the answer for lost.
    monkeypatch.setattr(api_console, "_NOTE_WAIT_SECONDS", 0.05)
    release = asyncio.Event()
    host = _Host(takes_note=release)
    async with _client(host) as client:
        answer = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"choice": "once", "note": "careful"})

    # The answer is in; whether the sentence was taken is not known yet, and is not said to be lost.
    assert answer.json() == {"success": True, "note_taken": None}
    assert host.passed_on == []
    # Not given up: it is passed on all the same.
    release.set()
    for _ in range(50):
        if host.passed_on:
            break
        await asyncio.sleep(0.01)
    assert host.passed_on == ["careful"]
    assert not api_console._notes_on_their_way


async def test_no_note_is_passed_on_for_an_answer_that_was_not_taken_nor_without_one():
    late = _Host(takes_answer=False)
    async with _client(late) as client:
        refused = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"choice": "once", "note": "careful"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "not_taken"
    assert late.told == []

    plain = _Host()
    async with _client(plain) as client:
        answer = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"choice": "once", "note": "   "})
    assert answer.json() == {"success": True}
    assert plain.told == []


async def test_what_was_picked_is_an_answer_of_its_own_and_reaches_the_host():
    host = _Host()
    async with _client(host) as client:
        answer = await client.post(
            "/console/commissions/c1/answer", headers=HEADERS, json={"selected": {"0": ["Vercel"], "1": []}, "question_id": "q3"},
        )
        nothing = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"selected": {"0": []}})

    assert answer.json() == {"success": True}
    assert host.answers == [("c1", "", {"choice": None, "question_id": "q3", "selected": {"0": ["Vercel"], "1": []}})]
    # Nothing picked, nothing said: there is no answer in it.
    assert nothing.status_code == 400 and nothing.json()["detail"]["code"] == "bad_text"


@pytest.mark.parametrize("selected", [{"0": [""]}, {"0": ["  "], "1": []}])
async def test_labels_that_say_nothing_are_no_answer(selected):
    host = _Host()
    async with _client(host) as client:
        nothing = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"selected": selected})

    assert nothing.status_code == 400 and nothing.json()["detail"] == {"code": "bad_text", "field": "text"}
    assert host.answers == []


@pytest.mark.parametrize("kind", ["permission", "confirmation", "report"])
async def test_a_pick_alone_answers_only_questions_that_offer_something_to_pick(kind):
    # Any other question would be handed an answer with nothing in it (a report's session, an empty prompt).
    host = _Host(asks=kind)
    async with _client(host) as client:
        alone = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"selected": {"0": ["Yes"]}})
        named = await client.post(
            "/console/commissions/c1/answer", headers=HEADERS, json={"selected": {"0": ["Yes"]}, "question_id": "q1"}
        )
        assert named.status_code == 400
        with_words = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"selected": {"0": ["Yes"]}, "text": "yes"})

    assert alone.status_code == 400 and alone.json()["detail"] == {"code": "bad_text", "field": "text"}
    assert with_words.status_code == 200
    assert [given[1] for given in host.answers] == ["yes"]


@pytest.mark.parametrize(
    "asks, question_id",
    [
        # It asks another question by now (a request for approval, whose id is q1), or nothing at all.
        ("permission", "q0"),
        ("nothing", "q0"),
        ("nothing", None),
    ],
)
async def test_a_pick_that_comes_too_late_is_turned_away_as_late_and_not_as_wrong(asks, question_id):
    # The pages say of 409 that the question was answered or replaced; 400 would read as an error of theirs.
    host = _Host(asks=asks, takes_answer=False)
    payload: dict[str, Any] = {"selected": {"0": ["Yes"]}}
    if question_id is not None:
        payload["question_id"] = question_id
    async with _client(host) as client:
        late = await client.post("/console/commissions/c1/answer", headers=HEADERS, json=payload)

    assert late.status_code == 409 and late.json()["detail"]["code"] == "not_taken"
    assert len(host.answers) == 1


async def test_a_pick_for_a_commission_that_is_not_there_is_turned_away_as_any_answer_to_it():
    host = _Host(asks=None, takes_answer=False)
    async with _client(host) as client:
        gone = await client.post("/console/commissions/c1/answer", headers=HEADERS, json={"selected": {"0": ["Yes"]}})

    assert gone.status_code == 409 and gone.json()["detail"]["code"] == "not_taken"


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"selected": ["Vercel"]}, "selected"),
        ({"selected": {"0": "Vercel"}}, "selected"),
        ({"selected": {"0": [3]}}, "selected"),
        ({"selected": {str(number): ["a"] for number in range(65)}}, "selected"),
        ({"selected": {"0": ["a"] * 65}}, "selected"),
        ({"choice": "once", "note": 3}, "note"),
    ],
)
async def test_a_pick_or_a_note_that_is_not_what_it_should_be_is_refused(payload, field):
    host = _Host()
    async with _client(host) as client:
        bad = await client.post("/console/commissions/c1/answer", headers=HEADERS, json=payload)

    assert bad.status_code == 400
    assert bad.json()["detail"] == {"code": "bad_text", "field": field}
    assert host.answers == [] and host.told == []


async def test_what_is_asked_right_now_is_listed_as_cards_show_it():
    def commission(**values: Any) -> Any:
        asked = values.pop("asked")
        return SimpleNamespace(lanlan_name="Lan", lang="en", route="task_agent", **values, question=lambda: asked)

    permission = {"kind": "permission", "id": "q1", "category": "run_command", "detail": {"command": "npm test"}, "options": ["once", "deny"]}
    host = _Host(waiting=[
        commission(id="c1", title="add tests", asked=permission),
        # A task agent's own task agent asks the user too.
        commission(id="c2", title="look it up", asked={"kind": "report", "id": "q2", "question": "Which one?"}, parent_id="c1"),
        # Waiting, with nothing that can be shown: left out.
        commission(id="c3", title="odd", asked=None),
    ])
    async with _client(host) as client:
        refused = await client.get("/console/questions")
        listed = (await client.get("/console/questions", headers=HEADERS)).json()

    assert refused.status_code == 403
    assert host.selected_with == {"statuses": ("waiting_user",), "oldest_first": True}
    assert [(row["task_id"], row["title"], row["route"], row["lanlan_name"]) for row in listed["questions"]] == [
        ("c1", "add tests", "task_agent", "Lan"), ("c2", "look it up", "task_agent", "Lan"),
    ]
    first, second = (row["question"] for row in listed["questions"])
    assert first["id"] == "q1" and "npm test" in first["line"] and first["line_cut"] is False
    assert second == {"kind": "report", "id": "q2", "question": "Which one?"}
