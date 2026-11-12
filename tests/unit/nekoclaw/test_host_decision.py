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

"""The decision model: what each path costs in model calls, and what it decides.

The owner's rule is that no path may cost more than the analyzer it replaces.
The counts asserted here are the ones in the module docstring of
``nekoclaw/host/decision.py`` and in the pull request's comparison table.
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any

import pytest

from nekoclaw.host.decision import (
    DecisionRequest,
    LexicalDecisionModel,
    Triage,
    find_duplicate,
    is_capability_question,
    parse_json_object,
    parse_permission_choice,
)
from nekoclaw.host.ledger import Commission
from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, pick_plugin
from tests.unit.nekoclaw.plugin_server_fake import entry, plugin


LANGUAGES = ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]


def commission(commission_id: str, dispatch_text: str, **values: Any) -> Commission:
    base: dict[str, Any] = {item.name: None for item in fields(Commission)}
    base.update(
        id=commission_id,
        lanlan_name="Lan",
        title=dispatch_text,
        dispatch_text=dispatch_text,
        user_text="",
        source="dispatch",
        status="working",
        status_reason="",
        created_at=0.0,
        updated_at=0.0,
    )
    base.update(values)
    return Commission(**base)


def weather_plugins() -> list[dict[str, Any]]:
    return [
        plugin(
            "weather",
            "Weather lookups for any city",
            [entry("today", "Today's weather"), entry("week", "Seven day forecast")],
            keywords=["天气", "weather"],
        ),
        plugin("lamp", "Controls the desk lamp", [entry("switch", "Turn the lamp on or off")]),
    ]


def _filler(index: int) -> str:
    return " ".join(f"module{index}word{n}" for n in range(60))


def large_catalog(count: int = 40) -> list[dict[str, Any]]:
    return [plugin(f"bulk{n:02d}", f"Bulk plugin {n}. {_filler(n)}", [entry("run", "Run it")]) for n in range(count)]


def request(text: str, **values: Any) -> DecisionRequest:
    values.setdefault("plugins", weather_plugins())
    return DecisionRequest(dispatch_text=text, **values)


# ---------------------------------------------------------------------------
# First round: local, no model call
# ---------------------------------------------------------------------------


async def test_first_round_of_a_new_task_makes_no_model_call():
    llm = FakeDecisionLLM()

    triage = await LexicalDecisionModel(llm).triage(request("查一下上海明天的天气", user_text="明天上海冷不冷"))

    assert llm.calls == []
    assert (triage.kind, triage.model_calls) == ("new", 0)
    assert triage.keyword_hit_ids == ["weather"]
    assert "weather" in triage.candidate_ids


async def test_first_round_with_no_plugin_match_but_a_task_agent_makes_no_model_call():
    llm = FakeDecisionLLM()

    triage = await LexicalDecisionModel(llm).triage(request("zzz qqq", task_agent_available=True))

    assert llm.calls == []
    assert (triage.kind, triage.candidate_ids, triage.model_calls) == ("new", [], 0)


async def test_first_round_with_no_plugins_at_all_makes_no_model_call():
    llm = FakeDecisionLLM()

    triage = await LexicalDecisionModel(llm).triage(request("zzz qqq", plugins=[]))

    assert llm.calls == []
    assert (triage.kind, triage.reason) == ("none", "no_match")


@pytest.mark.parametrize("coarse, kind", [(["lamp"], "new"), ([], "none"), (["not-installed"], "none")])
async def test_the_one_exception_is_a_single_small_model_call(coarse, kind):
    # No local candidate and no task agent to fall back on: one coarse screen,
    # on the small tier, before saying that nothing can do this.
    llm = FakeDecisionLLM(coarse=coarse)

    triage = await LexicalDecisionModel(llm).triage(request("zzz qqq", task_agent_available=False))

    assert llm.tiers() == ["coarse"]
    assert (triage.kind, triage.model_calls) == (kind, 1)
    assert triage.coarse_ids == coarse


@pytest.mark.parametrize(
    "text",
    ["别查了", "算了，不用了", "stop that", "cancel it please", "やめて", "그만해", "хватит", "ya no, déjalo"],
)
async def test_stop_request_is_read_locally(text):
    llm = FakeDecisionLLM()
    active = [commission("c1", "查一下上海明天的天气", route="plugin")]

    triage = await LexicalDecisionModel(llm).triage(request(text, active=active))

    assert llm.calls == []
    assert (triage.kind, triage.targets) == ("stop", ["c1"])


async def test_stop_names_the_commission_it_means():
    active = [commission("c1", "查一下上海明天的天气"), commission("c2", "整理下载文件夹里的照片")]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(request("照片先别整理了", active=active))

    assert (triage.kind, triage.targets) == ("stop", ["c2"])


async def test_stop_without_saying_which_of_several_asks_back():
    active = [commission("c1", "查一下上海明天的天气"), commission("c2", "整理下载文件夹里的照片")]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(request("算了", active=active))

    assert triage.kind == "ambiguous"
    assert sorted(triage.targets) == ["c1", "c2"]


async def test_stop_words_about_something_else_are_a_task_of_their_own():
    # "Cancel tomorrow's alarm" is work for a plugin, not a stop of the weather lookup.
    llm = FakeDecisionLLM()
    active = [commission("c1", "查一下上海的气温")]

    triage = await LexicalDecisionModel(llm).triage(request("取消明早七点的闹钟", active=active, task_agent_available=True))

    assert triage.kind == "new"
    assert llm.calls == []


@pytest.mark.parametrize("text", ["取消明天的闹钟", "cancel tomorrow's alarm"])
async def test_a_stop_sharing_only_a_common_word_is_not_about_that_commission(text):
    # "Tomorrow" says nothing about which commission is meant.
    llm = FakeDecisionLLM()
    active = [commission("c1", "查一下上海明天的天气 check tomorrow's weather")]

    triage = await LexicalDecisionModel(llm).triage(request(text, active=active, task_agent_available=True))

    assert triage.kind == "new"
    assert llm.calls == []


async def test_a_stop_naming_only_common_words_among_several_is_not_a_question_back():
    # It names something, just nothing under way: not "which one do you mean".
    active = [commission("c1", "查一下上海明天的天气"), commission("c2", "明天整理下载文件夹里的照片")]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(
        request("取消明天的", active=active, task_agent_available=True)
    )

    assert triage.kind == "new"


async def test_a_stop_still_finds_its_commission_next_to_common_words():
    active = [commission("c1", "查一下上海明天的天气"), commission("c2", "明天整理下载文件夹里的照片")]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(request("明天的照片别整理了", active=active))

    assert (triage.kind, triage.targets) == ("stop", ["c2"])


async def test_stop_with_nothing_under_way():
    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(request("算了", active=[]))

    assert triage.kind == "nothing_active"


async def test_repeat_of_something_under_way_is_read_locally():
    llm = FakeDecisionLLM()
    active = [commission("c1", "查一下上海明天的天气", user_text="明天上海冷不冷")]

    triage = await LexicalDecisionModel(llm).triage(request("查一下上海明天的天气", user_text="明天上海冷不冷", active=active))

    assert llm.calls == []
    assert (triage.kind, triage.targets) == ("duplicate", ["c1"])


async def test_same_user_words_lower_the_bar_for_a_repeat():
    active = [commission("c1", "查上海明天的天气情况", user_text="明天上海冷不冷")]
    model = LexicalDecisionModel(FakeDecisionLLM())

    same_words = await model.triage(request("帮他查上海明天天气", user_text="明天上海冷不冷", active=active))
    other_words = await model.triage(request("帮他查上海明天天气", user_text="后天呢", active=active))

    assert same_words.kind == "duplicate"
    assert other_words.kind == "new"


async def test_a_plugin_commission_left_blocked_is_not_repeated():
    # A plugin asked for confirmation; the commission has no way out of
    # "blocked" but a stop, so the confirmed request must run again.
    llm = FakeDecisionLLM()
    blocked = commission(
        "c1", "把明早7点的闹钟定上", user_text="明早7点叫我", status="blocked", route="plugin", plugin_id="alarm"
    )

    triage = await LexicalDecisionModel(llm).triage(
        request("把明早7点的闹钟定上", user_text="明早7点叫我", active=[blocked], task_agent_available=True)
    )

    assert triage.kind == "new"
    assert find_duplicate("把明早7点的闹钟定上", "明早7点叫我", [blocked]) is None


async def test_a_task_agent_commission_left_blocked_is_still_a_repeat():
    blocked = commission("c1", "整理下载文件夹里的照片", status="blocked", route="task_agent")

    assert find_duplicate("整理下载文件夹里的照片", "", [blocked]) is blocked


async def test_a_child_task_agents_commission_is_not_repeated():
    child = commission("c2", "整理下载文件夹里的照片", user_text="帮我整理照片", route="task_agent", parent_id="c1")

    assert find_duplicate("整理下载文件夹里的照片", "帮我整理照片", [child]) is None


async def test_a_child_task_agents_question_can_still_be_answered():
    question = json.dumps({"kind": "questions", "questions": [{"id": "q1", "question": "照片按月份还是按地点分？"}]})
    parent = commission("c1", "整理下载文件夹里的照片", route="task_agent")
    child = commission(
        "c2", "整理照片", route="task_agent", parent_id="c1", status="waiting_user", pending_question=question
    )

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(
        request("回答：按月份分", active=[parent], answerable=[child])
    )

    assert (triage.kind, triage.targets) == ("answer", ["c2"])


@pytest.mark.parametrize(
    "text",
    [
        "Поставь будильник на семь утра",
        "아침 일곱 시에 알람 맞춰 줘",
        "あしたのアラームをセットして",
        "アラームをセットしてください",
        "Pon la alarma mañana a las siete",
    ],
)
async def test_repeats_are_recognised_in_every_script(text):
    active = [commission("c1", text)]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(request(text, active=active))

    assert (triage.kind, triage.targets) == ("duplicate", ["c1"])


@pytest.mark.parametrize(
    "text, named",
    [("Отмени будильник", "будильник"), ("알람 취소해", "알람"), ("アラームをキャンセル", "アラーム")],
)
async def test_a_stop_naming_one_of_several_works_in_every_script(text, named):
    active = [commission("c1", f"{named} 7:00"), commission("c2", "整理下载文件夹里的照片")]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(request(text, active=active))

    assert (triage.kind, triage.targets) == ("stop", ["c1"])


async def test_a_different_task_is_not_a_repeat():
    active = [commission("c1", "查一下上海明天的天气")]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(
        request("把台灯关掉", active=active, task_agent_available=True)
    )

    assert triage.kind == "new"


async def test_addition_goes_to_the_task_agent_it_is_about():
    active = [
        commission("c1", "整理下载文件夹里的照片", route="task_agent"),
        commission("c2", "查一下上海明天的天气", route="plugin"),
    ]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(request("另外照片按月份分文件夹", active=active))

    assert (triage.kind, triage.targets) == ("tell", ["c1"])


async def test_addition_cannot_go_to_a_plugin_run():
    active = [commission("c2", "查一下上海明天的天气", route="plugin")]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(
        request("另外天气结果发我邮箱", active=active, task_agent_available=True)
    )

    assert triage.kind == "new"


async def test_answer_to_a_waiting_commission_is_read_locally():
    question = json.dumps({"kind": "questions", "questions": [{"id": "q1", "question": "照片按月份还是按地点分？"}]})
    active = [commission("c1", "整理下载文件夹里的照片", route="task_agent", status="waiting_user", pending_question=question)]
    llm = FakeDecisionLLM()

    triage = await LexicalDecisionModel(llm).triage(request("回答：按月份分", active=active))

    assert llm.calls == []
    assert (triage.kind, triage.targets, triage.choice) == ("answer", ["c1"], None)


@pytest.mark.parametrize(
    "text, choice",
    [
        ("可以", "once"),
        ("就这一次", "once"),
        ("以后都行", "always"),
        ("这次任务里都可以", "session"),
        ("不行", "deny"),
        ("yes go ahead", "once"),
        ("always", "always"),
        ("fine for this session", "session"),
        ("no", "deny"),
    ],
)
async def test_answer_to_a_permission_prompt_carries_the_choice(text, choice):
    question = json.dumps({"kind": "permission", "category": "run_command", "detail": {"command": "dir"}})
    active = [commission("c1", "整理下载文件夹里的照片", route="task_agent", status="waiting_user", pending_question=question)]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(request(text, active=active))

    assert (triage.kind, triage.targets, triage.choice) == ("answer", ["c1"], choice)


async def test_unrelated_sentence_is_not_taken_as_an_answer():
    question = json.dumps({"kind": "questions", "questions": [{"id": "q1", "question": "照片按月份还是按地点分？"}]})
    active = [commission("c1", "整理下载文件夹里的照片", route="task_agent", status="waiting_user", pending_question=question)]

    triage = await LexicalDecisionModel(FakeDecisionLLM()).triage(
        request("把台灯关掉", active=active, task_agent_available=True)
    )

    assert triage.kind == "new"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("不行", "deny"),
        ("以后都不行", "deny"),
        ("可以，以后都行", "always"),
        ("好的", "once"),
        ("don't", "deny"),
        ("sure, from now on", "always"),
        ("今回だけ", "once"),
        ("안 돼", "deny"),
        ("всегда", "always"),
        ("sí", "once"),
        ("weather in Paris", None),
        # A negated approval is a refusal, in every language.
        ("not allowed", "deny"),
        ("not approved", "deny"),
        ("I can't allow that", "deny"),
        ("不同意", "deny"),
        ("不批准", "deny"),
        ("許可しません", "deny"),
        ("허락 안 해", "deny"),
        ("не разрешаю", "deny"),
        ("no lo permito", "deny"),
        ("não permito", "deny"),
    ],
)
def test_permission_answers(text, expected):
    assert parse_permission_choice(text) == expected


@pytest.mark.parametrize("lang", LANGUAGES)
def test_each_named_answer_to_a_permission_prompt_reads_back_as_itself(lang):
    # The four answers are named to her in the question; said back word for
    # word, each has to be read as the choice it names.
    from config.prompts.prompts_nekoclaw_brain import PERMISSION_OPTION_NAMES
    from nekoclaw.host.permissions import CHOICES

    names = PERMISSION_OPTION_NAMES[lang]

    assert set(names) == set(CHOICES) == {"always", "session", "once", "deny"}
    assert {choice: parse_permission_choice(name) for choice, name in names.items()} == {
        choice: choice for choice in names
    }


@pytest.mark.parametrize(
    "text, expected",
    [
        # The scope has to be said outright to count as "for this session".
        ("本会话都可以", "session"),
        ("这个任务里都行", "session"),
        ("for this task, always fine", "session"),
        ("sure, during this task", "session"),
        ("このセッション中は許可", "session"),
        ("이번 세션 동안 허용", "session"),
        ("разрешаю на эту сессию", "session"),
        ("durante esta tarea, sí", "session"),
        ("durante esta tarefa, sim", "session"),
        # "This task is fine" names no scope: it is a one-time approval.
        ("这个任务可以", "once"),
        ("this one is fine", "once"),
        # A refusal wins over a scope named in the same sentence.
        ("这个会话里也不行", "deny"),
        ("not for this session", "deny"),
        ("この件の間もだめ", "deny"),
    ],
)
def test_session_scope_in_a_permission_answer(text, expected):
    assert parse_permission_choice(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("你能不能帮我订机票", True),
        ("可以控制台灯吗", True),
        ("can you book a flight", True),
        ("is it possible to print this", True),
        ("印刷できますか", True),
        ("천천히 할 수 있어?", True),
        ("можешь заказать такси", True),
        ("se puede reservar un vuelo", True),
        ("天气查得怎么样了", False),
        ("what are you working on", False),
        ("", False),
    ],
)
def test_capability_questions(text, expected):
    assert is_capability_question(text) is expected


# ---------------------------------------------------------------------------
# Second round: how many model calls each path costs
# ---------------------------------------------------------------------------


async def test_direct_plugin_costs_one_fine_selection():
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today", {"city": "Shanghai"})])
    model = LexicalDecisionModel(llm)
    req = request("查一下上海明天的天气")

    selection = await model.select(req, await model.triage(req))

    assert llm.tiers() == ["fine"]
    assert selection.model_calls == 1
    assert (selection.route, selection.plugin_id, selection.entry_id, selection.plugin_args) == (
        "plugin",
        "weather",
        "today",
        {"city": "Shanghai"},
    )
    assert selection.shown_plugin_ids == ["weather", "lamp"]


async def test_handing_over_to_a_task_agent_is_decided_by_the_same_call():
    llm = FakeDecisionLLM(fine=[{"route": "task_agent", "reason": "needs several steps"}])
    model = LexicalDecisionModel(llm)
    req = request("查上海明天的天气，再写成一份出行建议存到桌面", task_agent_available=True)

    selection = await model.select(req, await model.triage(req))

    # One request in total: no second one asks "should a task agent take this".
    assert llm.tiers() == ["fine"]
    assert (selection.route, selection.model_calls) == ("task_agent", 1)


async def test_large_catalog_costs_coarse_screen_plus_fine_selection():
    plugins = large_catalog()
    plugins[3]["description"] = "Looks up train timetables. " + _filler(3)
    llm = FakeDecisionLLM(coarse=["bulk11"], fine=[pick_plugin("bulk03", "run")])
    model = LexicalDecisionModel(llm)
    req = request("train timetables", plugins=plugins)

    triage = await model.triage(req)
    selection = await model.select(req, triage)

    assert triage.model_calls == 0
    assert llm.tiers() == ["coarse", "fine"]
    assert selection.model_calls == 2
    assert (selection.route, selection.plugin_id) == ("plugin", "bulk03")
    assert selection.shown_plugin_ids == ["bulk03", "bulk11"]


async def test_coarse_screen_of_the_first_round_is_not_paid_for_twice():
    # Large catalog, nothing matches locally, no task agent: the first round
    # runs the coarse screen (the exception) and the second round reuses it.
    plugins = large_catalog()
    llm = FakeDecisionLLM(coarse=["bulk07"], fine=[pick_plugin("bulk07", "run")])
    model = LexicalDecisionModel(llm)
    req = request("qqqq", plugins=plugins, task_agent_available=False)

    triage = await model.triage(req)
    selection = await model.select(req, triage)

    assert llm.tiers() == ["coarse", "fine"]
    assert (triage.model_calls, selection.model_calls) == (1, 1)
    assert selection.shown_plugin_ids == ["bulk07"]
    assert selection.route == "plugin"


async def test_no_plugins_means_no_model_call_at_all():
    llm = FakeDecisionLLM()
    model = LexicalDecisionModel(llm)
    req = request("整理一下桌面", plugins=[], task_agent_available=True)

    selection = await model.select(req, await model.triage(req))

    assert llm.calls == []
    assert (selection.route, selection.model_calls) == ("task_agent", 0)


async def test_large_catalog_with_no_candidate_skips_the_fine_selection():
    llm = FakeDecisionLLM(coarse=[])
    model = LexicalDecisionModel(llm)
    req = request("qqqq", plugins=large_catalog(), task_agent_available=True)

    selection = await model.select(req, await model.triage(req))

    # The analyzer asked the fine selection even with nothing to choose from.
    assert llm.tiers() == ["coarse"]
    assert (selection.route, selection.model_calls, selection.shown_plugin_ids) == ("task_agent", 1, [])


@pytest.mark.parametrize(
    ("wrong", "problem"),
    [
        (pick_plugin("weather", "now"), "entry_id 'now' does not exist in plugin 'weather'"),
        (pick_plugin("wetter", "today"), "plugin_id 'wetter' is not available"),
        ({"route": "plugin", "plugin_id": "weather"}, "entry_id is required for plugin 'weather'"),
        ({"route": "plugin", "entry_id": "today"}, "plugin_id is required"),
    ],
    ids=["unknown-entry", "unknown-plugin", "no-entry", "no-plugin"],
)
async def test_an_answer_naming_what_does_not_exist_is_not_asked_about_again(wrong, problem):
    # The second answer would be right; it is never asked for.
    llm = FakeDecisionLLM(fine=[wrong, pick_plugin("weather", "today")])
    model = LexicalDecisionModel(llm)

    selection = await model.select(request("查一下上海明天的天气", task_agent_available=True), Triage(kind="new"))

    assert llm.tiers() == ["fine"]
    assert selection.model_calls == 1
    # No plugin is called on a guess; a task agent takes it, with what was shown.
    assert (selection.route, selection.plugin_id, selection.entry_id) == ("task_agent", None, None)
    assert problem in selection.reason
    assert selection.shown_plugin_ids


async def test_an_answer_naming_what_does_not_exist_cannot_be_done_without_a_task_agent():
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "now"), pick_plugin("weather", "today")])
    model = LexicalDecisionModel(llm)

    selection = await model.select(request("查一下上海明天的天气", task_agent_available=False), Triage(kind="new"))

    assert llm.tiers() == ["fine"]
    assert (selection.route, selection.plugin_id, selection.model_calls) == ("none", None, 1)


@pytest.mark.parametrize("task_agent_available", [True, False])
async def test_the_model_can_say_that_nothing_can_do_it(task_agent_available):
    llm = FakeDecisionLLM(
        fine=[{"route": "none", "task_description": "修水龙头", "preference": "以后别催", "reason": "needs hands"}]
    )
    model = LexicalDecisionModel(llm)

    selection = await model.select(
        request("帮我把厨房的水龙头修好", task_agent_available=task_agent_available), Triage(kind="new")
    )

    # Nothing is started, whether or not a task agent could have been.
    assert (selection.route, selection.plugin_id, selection.model_calls) == ("none", None, 1)
    assert (selection.task_description, selection.reason) == ("修水龙头", "needs hands")
    # What she stated for good is kept even when the task itself cannot be done.
    assert selection.preference == "以后别催"


async def test_an_answer_with_no_route_still_goes_to_a_task_agent():
    # "none" has to be said; an answer that names no route is not read as it.
    llm = FakeDecisionLLM(fine=[{"task_description": "整理桌面", "reason": ""}])
    model = LexicalDecisionModel(llm)

    selection = await model.select(request("整理一下桌面", task_agent_available=True), Triage(kind="new"))

    assert (selection.route, selection.model_calls) == ("task_agent", 1)


@pytest.mark.parametrize("answer", ["", "I cannot decide", RuntimeError("provider down")])
async def test_an_unusable_answer_costs_one_call_and_falls_back(answer):
    llm = FakeDecisionLLM(fine=[answer])
    model = LexicalDecisionModel(llm)

    with_task_agent = await model.select(request("查天气", task_agent_available=True), Triage(kind="new"))
    without = await model.select(request("查天气", task_agent_available=False), Triage(kind="new"))

    assert (with_task_agent.route, with_task_agent.model_calls) == ("task_agent", 1)
    assert (without.route, without.model_calls) == ("none", 1)
    assert llm.count() == 2


# ---------------------------------------------------------------------------
# Second round: what the fine selection is shown
# ---------------------------------------------------------------------------


async def test_fine_selection_sees_task_user_words_ledger_and_preferences():
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])
    active = [commission("c1", "整理下载文件夹里的照片", route="task_agent")]
    req = request("查一下上海明天的天气", user_text="明天上海冷不冷", active=active, preferences="- 查天气默认查上海", lang="zh")

    await LexicalDecisionModel(llm).select(req, Triage(kind="new"))

    system, user = (message["content"] for message in llm.last("fine"))
    assert "- weather [KEYWORD MATCH]: Weather lookups for any city" in system
    assert user.splitlines() == [
        "TASK: 查一下上海明天的天气",
        "USER_SAID: 明天上海冷不冷",
        "ACTIVE_TASKS:",
        "1. [working] 整理下载文件夹里的照片 (task_agent)",
        "PREFERENCES:",
        "- 查天气默认查上海",
    ]


async def test_fine_selection_gets_no_conversation_history():
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])

    await LexicalDecisionModel(llm).select(request("查天气", user_text="查天气"), Triage(kind="new"))

    messages = llm.last("fine")
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[1]["content"] == "TASK: 查天气\nUSER_SAID: 查天气"


async def test_ledger_view_of_the_fine_selection_has_at_most_three_rows():
    from config.nekoclaw_settings import DECISION_LEDGER_ROW_MAX_TOKENS, DECISION_LEDGER_ROWS
    from utils.tokenize import count_tokens

    assert DECISION_LEDGER_ROWS == 3
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])
    active = [commission(f"c{n}", f"任务 {n} " + "很长的描述 " * 80, route="task_agent") for n in range(9)]
    active.append(commission("near", "查一下北京明天的天气", route="plugin", plugin_id="weather", entry_id="today"))

    await LexicalDecisionModel(llm).select(request("查一下上海明天的天气", active=active), Triage(kind="new"))

    user = llm.last("fine")[1]["content"]
    rows = [line for line in user.splitlines() if line[:2] in ("1.", "2.", "3.", "4.")]
    assert len(rows) == 3
    # The most related commission comes first.
    assert rows[0] == "1. [working] 查一下北京明天的天气 (weather.today)"
    assert all(count_tokens(row[3:]) <= DECISION_LEDGER_ROW_MAX_TOKENS for row in rows)


async def test_dispatch_text_user_text_and_preferences_are_capped_in_tokens():
    from config.nekoclaw_settings import (
        DISPATCH_TEXT_MAX_TOKENS,
        DISPATCH_USER_TEXT_MAX_TOKENS,
        PREFERENCE_PROMPT_MAX_TOKENS,
    )
    from utils.tokenize import count_tokens

    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])
    long = "天气 " + " ".join(f"词{n}" for n in range(3000))
    req = request(long, user_text=long, preferences="\n".join(f"- 规则{n}" for n in range(500)))

    await LexicalDecisionModel(llm).select(req, Triage(kind="new"))

    lines = llm.last("fine")[1]["content"].split("\n")
    task = next(line for line in lines if line.startswith("TASK: "))
    said = next(line for line in lines if line.startswith("USER_SAID: "))
    preferences = "\n".join(lines[lines.index("PREFERENCES:") + 1 :])
    assert count_tokens(task[len("TASK: ") :]) <= DISPATCH_TEXT_MAX_TOKENS
    assert count_tokens(said[len("USER_SAID: ") :]) <= DISPATCH_USER_TEXT_MAX_TOKENS
    assert count_tokens(preferences) <= PREFERENCE_PROMPT_MAX_TOKENS == 150


# What the fixed part of the analyzer's selection prompt cost, per language,
# measured on the text as it was when the analyzer was removed.
ANALYZER_SELECTION_FIXED_TOKENS = {
    "zh": 636, "zh-TW": 788, "en": 614, "ja": 938, "ko": 800, "ru": 830, "es": 690, "pt": 700,
}


@pytest.mark.parametrize("lang", LANGUAGES)
def test_fixed_part_of_the_fine_selection_is_no_longer_than_the_analyzers_was(lang):
    from config.prompts.prompts_nekoclaw_brain import TASK_BRAIN_SELECT_PROMPT
    from utils.tokenize import count_tokens

    new = count_tokens(TASK_BRAIN_SELECT_PROMPT[lang].format(plugins_desc=""))

    assert new <= ANALYZER_SELECTION_FIXED_TOKENS[lang], f"{lang}: {new} tokens"


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize(
    "table", ["TASK_BRAIN_SELECT_PROMPT", "TASK_BRAIN_COARSE_SCREEN_PROMPT", "TURN_CHECK_PROMPT"]
)
def test_every_system_prompt_of_the_task_brain_opens_as_the_analyzers_did(table, lang):
    from config.prompts import prompts_nekoclaw_brain

    # The phrase the analyzer's prompts opened with in every language, untranslated;
    # the paired block markers of the other prompts would do as well.
    phrase = "automation assessment agent"
    prompts = getattr(prompts_nekoclaw_brain, table)

    assert set(prompts) == set(LANGUAGES)
    text = prompts[lang]
    assert phrase in text.splitlines()[0] or ("======以下为" in text and "======以上为" in text)


def test_every_prompt_table_sent_to_a_model_is_under_that_check():
    # A system prompt added to this file later has to be added to the test
    # above; this fails until it is.
    from config.prompts import prompts_nekoclaw_brain

    sent_to_a_model = {
        name for name in dir(prompts_nekoclaw_brain) if name.endswith("_PROMPT") and name.isupper()
    }

    assert sent_to_a_model == {"TASK_BRAIN_SELECT_PROMPT", "TASK_BRAIN_COARSE_SCREEN_PROMPT", "TURN_CHECK_PROMPT"}


def test_every_language_of_the_fine_selection_keeps_the_identifiers_it_parses():
    from config.prompts.prompts_nekoclaw_brain import TASK_BRAIN_SELECT_PROMPT

    assert set(TASK_BRAIN_SELECT_PROMPT) == {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    for lang, text in TASK_BRAIN_SELECT_PROMPT.items():
        rendered = text.format(plugins_desc="- demo: Demo | entries: [run]")
        for needle in (
            "- demo: Demo | entries: [run]",
            'route="plugin"',
            'route="task_agent"',
            'route="duplicate"',
            'route="none"',
            '"route": "plugin" | "task_agent" | "duplicate" | "none"',
            '"plugin_id"',
            '"entry_id"',
            '"plugin_args"',
            '"duplicate_of"',
            '"preference"',
            "TASK",
            "USER_SAID",
            "ACTIVE_TASKS",
            "PREFERENCES",
            "[KEYWORD MATCH]",
        ):
            assert needle in rendered, f"{lang} lacks {needle}"
        # The rule for "none" ends by sending a doubtful case to a task agent,
        # and the rule before it says what a task agent can do.
        none_rule = next(line for line in rendered.splitlines() if 'route="none"' in line)
        assert none_rule.startswith("4. ") and none_rule.count("task_agent") >= 2, lang
        agent_rule = next(line for line in rendered.splitlines() if line.startswith("3. "))
        assert 'route="task_agent"' in agent_rule and agent_rule.count("task_agent") == 2, lang


# ---------------------------------------------------------------------------
# Second round: reading the answer
# ---------------------------------------------------------------------------


async def test_duplicate_points_at_a_listed_commission():
    llm = FakeDecisionLLM(fine=[{"route": "duplicate", "duplicate_of": 1}])
    active = [commission("c1", "查一下北京明天的天气", route="plugin")]

    selection = await LexicalDecisionModel(llm).select(request("看看北京明天天气如何", active=active), Triage(kind="new"))

    assert (selection.route, selection.duplicate_of) == ("duplicate", "c1")


@pytest.mark.parametrize("number", [0, 2, None, "x", True])
async def test_duplicate_of_an_unlisted_line_is_not_acted_on(number):
    llm = FakeDecisionLLM(fine=[{"route": "duplicate", "duplicate_of": number}])
    active = [commission("c1", "查一下北京明天的天气", route="plugin")]

    selection = await LexicalDecisionModel(llm).select(
        request("看看北京明天天气如何", active=active, task_agent_available=True), Triage(kind="new")
    )

    assert (selection.route, selection.duplicate_of) == ("task_agent", None)


async def test_a_stated_preference_is_returned():
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today", preference="  以后查天气都查上海  ")])

    selection = await LexicalDecisionModel(llm).select(request("查天气，以后都默认查上海"), Triage(kind="new"))

    assert selection.preference == "以后查天气都查上海"


async def test_answer_in_the_analyzers_older_shape_is_understood():
    old_shape = {"has_task": True, "can_execute": True, "plugin_id": "weather", "entry_id": "today", "plugin_args": {}}
    llm = FakeDecisionLLM(fine=[old_shape])

    selection = await LexicalDecisionModel(llm).select(request("查天气"), Triage(kind="new"))

    assert (selection.route, selection.plugin_id) == ("plugin", "weather")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"route": "plugin"}', {"route": "plugin"}),
        ('```json\n{"route": "plugin",}\n```', {"route": "plugin"}),
        ('Sure: {"route": "task_agent", "x": {"y": 1}} done', {"route": "task_agent", "x": {"y": 1}}),
        ("[]", None),
        ("", None),
        ("no json here", None),
        # Commas and fences inside strings are content and stay as they are.
        ('{"plugin_args":{"text":"a,}b"}}', {"plugin_args": {"text": "a,}b"}}),
        ('{"text": "a,]b", "n": [1, 2,],}', {"text": "a,]b", "n": [1, 2]}),
        ('{"text": "say \\",}\\" twice",}', {"text": 'say ",}" twice'}),
        (
            '```json\n{"plugin_args": {"text": "```python\\nprint(1)\\n```"}}\n```',
            {"plugin_args": {"text": "```python\nprint(1)\n```"}},
        ),
        ('```\n{"route": "plugin"}\n```', {"route": "plugin"}),
    ],
)
def test_parsing_a_model_answer(raw, expected):
    assert parse_json_object(raw) == expected


# ---------------------------------------------------------------------------
# The configured model tiers
# ---------------------------------------------------------------------------


class _FakeConfig:
    def __init__(self, slots: dict[str, dict[str, Any]]) -> None:
        self.slots = slots
        self.asked: list[str] = []

    def get_model_api_config(self, slot: str) -> dict[str, Any]:
        self.asked.append(slot)
        return self.slots[slot]


class _FakeClient:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.messages: list[Any] = []
        self.closed = False

    async def ainvoke(self, messages: list[dict[str, Any]]) -> Any:
        self.messages.append(messages)

        class Response:
            content = '["weather"]'

        return Response()

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def configured(monkeypatch: Any) -> Any:
    import utils.config_manager as config_manager
    import utils.llm_client as llm_client
    import utils.token_tracker as token_tracker

    config = _FakeConfig(
        {
            "emotion": {"model": "small", "base_url": "http://192.168.1.20:8000/v1", "api_key": "k1", "provider_type": "openai_compatible"},
            "summary": {"model": "large", "base_url": "http://localhost:11434/v1", "api_key": "k2"},
            "agent": {"model": "worker", "base_url": "http://10.0.0.5:1234/v1", "api_key": "k3", "provider_type": "anthropic"},
        }
    )
    created: list[_FakeClient] = []
    call_types: list[str] = []

    async def create(**kwargs: Any) -> _FakeClient:
        created.append(_FakeClient(kwargs))
        return created[-1]

    def get_config_manager(*args: Any, **kwargs: Any) -> _FakeConfig:
        # Asking for the configuration never triggers a migration from here.
        assert kwargs.get("migrate") is False
        return config

    monkeypatch.setattr(config_manager, "get_config_manager", get_config_manager)
    monkeypatch.setattr(llm_client, "create_chat_llm_async", create)
    monkeypatch.setattr(token_tracker, "set_call_type", call_types.append)
    return config, created, call_types


async def test_coarse_and_fine_use_the_analyzers_models_and_budgets(configured):
    from config import AGENT_PLUGIN_COARSE_MAX_TOKENS, AGENT_PLUGIN_FULL_MAX_TOKENS
    from nekoclaw.host.decision import ConfiguredDecisionLLM

    config, created, call_types = configured
    llm = ConfiguredDecisionLLM()
    messages = [{"role": "user", "content": "hi"}]

    assert await llm.complete("coarse", messages) == '["weather"]'
    await llm.complete("fine", messages)
    await llm.complete("fine", messages)

    assert config.asked == ["emotion", "summary", "summary"]
    # One client per tier, reused.
    coarse, fine = created
    assert (coarse.kwargs["model"], coarse.kwargs["max_completion_tokens"]) == ("small", AGENT_PLUGIN_COARSE_MAX_TOKENS)
    assert (fine.kwargs["model"], fine.kwargs["max_completion_tokens"]) == ("large", AGENT_PLUGIN_FULL_MAX_TOKENS)
    assert (AGENT_PLUGIN_COARSE_MAX_TOKENS, AGENT_PLUGIN_FULL_MAX_TOKENS) == (300, 500)
    # Addresses are passed on as configured; plain http is allowed.
    assert coarse.kwargs["base_url"] == "http://192.168.1.20:8000/v1"
    assert fine.kwargs["base_url"] == "http://localhost:11434/v1"
    for client in created:
        assert client.kwargs["timeout"] == 120.0
        assert client.kwargs["max_retries"] == 0
        assert "temperature" not in client.kwargs
    # Counted in the same usage bucket as the analyzer.
    assert call_types == ["agent", "agent", "agent"]
    await llm.aclose()
    assert all(client.closed for client in created)


async def test_task_agents_use_the_agent_model_slot_with_its_address_as_configured(configured):
    from nekoclaw.host.service import configured_task_agent_model
    from nekoclaw.runtime.protocol import ModelConfig

    config, _created, _call_types = configured

    model = await configured_task_agent_model()

    assert config.asked == ["agent"]
    assert model == {
        "model": "worker",
        "base_url": "http://10.0.0.5:1234/v1",
        "api_key": "k3",
        "provider_type": "anthropic",
    }
    assert ModelConfig(**model).base_url == "http://10.0.0.5:1234/v1"
    config.slots["agent"] = {"model": "", "base_url": "", "api_key": ""}
    assert await configured_task_agent_model() is None


# ---------------------------------------------------------------------------
# A query's probe never asks a model
# ---------------------------------------------------------------------------


async def test_probe_for_a_query_leaves_open_what_only_a_model_could_tell():
    llm = FakeDecisionLLM(coarse=["lamp"])
    model = LexicalDecisionModel(llm)
    nothing_matches = request("zzz qqq", task_agent_available=False)

    for_query = await model.probe(nothing_matches, ask_model=False)
    for_dispatch = await model.probe(nothing_matches)

    # The dispatch pays for one coarse screen; the query does not and says so.
    assert (for_query.kind, for_query.model_calls) == ("unsure", 0)
    assert (for_dispatch.kind, for_dispatch.model_calls) == ("new", 1)
    assert llm.tiers() == ["coarse"]


@pytest.mark.parametrize(
    "values, kind",
    [
        ({"dispatch_text": "查一下上海明天的天气"}, "new"),
        ({"dispatch_text": "zzz qqq", "task_agent_available": True}, "new"),
        ({"dispatch_text": "zzz qqq", "plugins": []}, "none"),
    ],
)
async def test_probe_for_a_query_agrees_with_a_dispatch_where_no_model_is_needed(values, kind):
    llm = FakeDecisionLLM()
    model = LexicalDecisionModel(llm)
    text = values.pop("dispatch_text")

    for_query = await model.probe(request(text, **values), ask_model=False)
    for_dispatch = await model.probe(request(text, **values))

    assert for_query.kind == for_dispatch.kind == kind
    assert llm.calls == []



# ---------------------------------------------------------------------------
# Turn check: the gate and the one model call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score, text, expected",
    [
        # No score, or one at the threshold or above: the turn goes through.
        (None, "今天好累啊", True),
        (0.2, "今天好累啊", True),
        (0.9, "帮我订一张机票", True),
        # A low score, but a plugin keyword in the user's words.
        (0.0, "明天天气怎么样", True),
        # A low score and nothing local that speaks for a task.
        (0.0, "今天好累啊", False),
        (0.19, "讲个笑话", False),
    ],
)
def test_gate_before_the_turn_check(score, text, expected):
    from nekoclaw.host.decision import looks_like_a_task

    plugins = [plugin("weather", "Weather lookups", [entry("today", "Today")], keywords=["天气", "weather"])]

    assert looks_like_a_task(score, text, plugins) is expected


def test_gate_lets_a_turn_through_when_a_plugin_has_no_keywords_to_match_by():
    from nekoclaw.host.decision import looks_like_a_task

    # Only a model can tell whether such a plugin fits, so its presence
    # never holds a turn back; a passive or fully hidden one does not count.
    without_keywords = [plugin("lamp", "Controls the desk lamp", [entry("switch", "Turn the lamp on or off")])]
    hidden = [plugin("lamp", "Controls the desk lamp", [entry("switch", "Switch", metadata={"agent_hidden": True})])]

    assert looks_like_a_task(0.0, "今天好累啊", without_keywords) is True
    assert looks_like_a_task(0.0, "今天好累啊", hidden) is False


@pytest.mark.parametrize(
    "answer, kind, task",
    [
        ({"verdict": "promised", "task": "查上海明天的天气"}, "promised", "查上海明天的天气"),
        ({"verdict": "unable", "task": "开灯"}, "unable", "开灯"),
        ({"verdict": "claimed_done", "task": "订机票"}, "claimed_done", "订机票"),
        ({"verdict": "none", "task": "不该留下的话"}, "none", ""),
        ('```json\n{"verdict": "PROMISED", "task": " 查  天气 "}\n```', "promised", "查 天气"),
        # Anything unreadable changes nothing.
        ({"verdict": "maybe"}, "none", ""),
        ("no json here", "none", ""),
        (RuntimeError("boom"), "none", ""),
    ],
)
async def test_turn_check_is_one_call_of_the_small_model(answer, kind, task):
    llm = FakeDecisionLLM(turn=[answer])

    verdict = await LexicalDecisionModel(llm).judge_turn("帮我查一下上海明天的天气", "好的，我这就去查", "zh")

    assert llm.tiers() == ["turn_check"]
    assert (verdict.kind, verdict.task, verdict.model_calls) == (kind, task, 1)


@pytest.mark.parametrize("lang", LANGUAGES)
async def test_turn_check_request_is_the_prompt_and_the_two_sentences(lang):
    from config.prompts.prompts_nekoclaw_brain import TURN_CHECK_PROMPT, TURN_CHECK_VERDICTS

    llm = FakeDecisionLLM(turn=[{"verdict": "none"}])

    await LexicalDecisionModel(llm).judge_turn("USER-SENTENCE", "REPLY-SENTENCE", lang)

    system, user = llm.last("turn_check")
    assert system == {"role": "system", "content": TURN_CHECK_PROMPT[lang]}
    assert user == {"role": "user", "content": "USER: USER-SENTENCE\nREPLY: REPLY-SENTENCE"}
    # The identifiers the answer is parsed by are in the prompt of every language.
    for needle in (*TURN_CHECK_VERDICTS, '"verdict"', '"task"'):
        assert needle in system["content"], f"{lang}: {needle} missing"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_fixed_part_of_the_turn_check_stays_small(lang):
    from config.prompts.prompts_nekoclaw_brain import TURN_CHECK_PROMPT
    from utils.tokenize import count_tokens

    # With both sentences capped at 300 tokens each, one check stays below
    # 950 tokens of input; a usual one is around 0.4k.
    assert count_tokens(TURN_CHECK_PROMPT[lang]) <= 340
