"""The orchestrator's layers that ask no model, pinned by the scenarios; and its pieces one by one."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from nekopower.host import orchestrator as orch
from nekopower.host.orchestrator import Agent, Bars, Done, Match, ProjectView, Standing
from tests.unit.nekopower import orchestrator_cases as cases_of

pytestmark = pytest.mark.unit

WORLDS, CASES = cases_of.load()
HOUR = 3600.0


def _local(case: dict[str, Any], bars: Bars) -> tuple[str, orch.Verdict | None]:
    """What the layers that ask no model make of a case: which of them settled it, and how."""
    standing = WORLDS[case["world"]]
    verdict = orch.by_rules(case["dispatch"], case["user"], standing, bars)
    if verdict is not None:
        return "rules", verdict
    key = cases_of.follows_up(case["dispatch"], standing)
    if key is not None:
        return "rules", orch.settle(key, orch.FOLLOW_UP, standing, bars)
    verdict, _matches = orch.by_retrieval(case["dispatch"], case["user"], standing, bars)
    if verdict is None:
        return "ask", None
    return ("nothing" if verdict.action == orch.SUMMON_STRAY else "attach"), verdict


def test_the_scenarios_cover_what_they_are_to_cover() -> None:
    assert 40 <= len(CASES)
    assert len({case["id"] for case in CASES}) == len(CASES)
    tags = {tag for case in CASES for tag in case["tags"]}
    assert {"named", "paraphrase", "lookalike", "one_off", "recurrence", "second", "third", "leader", "other_language"} <= tags
    actions = {case["expect"]["action"] for case in CASES} | {
        case["expect_family_at_2"]["action"] for case in CASES if "expect_family_at_2" in case
    }
    # A new task agent in a project is what a project gets whose members take no more work; no scenario has one yet.
    assert actions == set(orch.ACTIONS) - {orch.SUMMON_IN_PROJECT}
    assert len({case["lang"] for case in CASES}) >= 5
    for case in CASES:
        standing = WORLDS[case["world"]]
        for where in (case["expect"], case.get("expect_family_at_2")):
            if where is None:
                continue
            for agent in (*[where.get("agent")], *where.get("members", ())):
                assert agent is None or standing.agent(agent) is not None, case["id"]
            assert where.get("project") is None or standing.project(where["project"]) is not None, case["id"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_what_the_layers_without_a_model_make_of_a_scenario(case: dict[str, Any]) -> None:
    """Each scenario says which layer settles it ("local") and whether that is right ("local_right")."""
    layer, verdict = _local(case, Bars())
    assert layer == case["local"]
    if verdict is None:
        return
    grade = cases_of.grade(case["expect"], verdict, WORLDS[case["world"]])
    assert (grade == cases_of.RIGHT) == case.get("local_right", True), (grade, cases_of.as_destination(verdict))


def test_the_layers_without_a_model_never_attach_a_new_matter() -> None:
    """Wrongly attaching does most harm: none of the scenarios is, by rules and retrieval alone."""
    wrongly = []
    for case in CASES:
        _layer, verdict = _local(case, Bars())
        if verdict is not None and cases_of.grade(case["expect"], verdict, WORLDS[case["world"]]) == cases_of.WRONGLY_ATTACHED:
            wrongly.append(case["id"])
    assert wrongly == []


def test_a_one_off_in_an_empty_world_asks_nobody() -> None:
    for case in CASES:
        if case["world"] == "empty":
            layer, verdict = _local(case, Bars())
            assert layer == "nothing" and verdict is not None and verdict.action == orch.SUMMON_STRAY
            assert verdict.preset == orch.WORKING_PRESET and verdict.model_calls == 0


# -- the pieces ---------------------------------------------------------------


def _standing() -> Standing:
    return Standing(
        projects=(
            ProjectView(name="Recipe Box", description="A site of family recipes"),
            ProjectView(name="thesis", description="The master's thesis", in_git=True),
            ProjectView(name="shop", description="The shop's pages", leader="boss-1"),
        ),
        agents=(
            Agent(id="cook", project="Recipe Box", title="Recipe keeper", duty="Adds recipes", idle_seconds=2 * HOUR),
            Agent(id="scout", project="Recipe Box", preset="scout", duty="Looks things up", idle_seconds=HOUR),
            Agent(id="boss-1", project="shop", preset="boss", title="Shop boss"),
            Agent(id="painter", project="shop", preset="worker", title="Page painter", duty="Paints the pages"),
            Agent(
                id="weekly",
                title="Report writer",
                duty="Writes the weekly report",
                idle_seconds=30 * HOUR,
                done=(
                    Done(title="Write the weekly report for week 46", filed=orch.RECURRENCE, age_seconds=30 * HOUR),
                    Done(title="Write the weekly report", filed=orch.NEW, age_seconds=200 * HOUR),
                ),
            ),
            Agent(
                id="desks",
                duty="Compare standing desks",
                idle_seconds=10 * HOUR,
                done=(Done(title="Compare standing desks under 400", result="Three desks in desks.md", age_seconds=10 * HOUR),),
            ),
            Agent(
                id="old",
                duty="Look up the price of a graphics card",
                idle_seconds=400 * HOUR,
                done=(Done(title="Look up the price of the graphics card", age_seconds=400 * HOUR),),
            ),
        ),
    )


def test_a_project_is_named_by_its_name_and_a_task_agent_by_its_title() -> None:
    standing, bars = _standing(), Bars()
    assert orch.named("add a soup to Recipe Box", standing, bars) == "project:Recipe Box"
    assert orch.named("ask the report writer about last week", standing, bars) == "agent:weekly"
    # Not inside another word, and not a name that is too short to mean anything.
    assert orch.named("go shopping for me", standing, bars) is None
    short = replace(standing, projects=(ProjectView(name="ab"),), agents=())
    assert orch.named("the ab test", short, bars) is None
    assert orch.named("the ab test", short, replace(bars, name_min_chars=2)) == "project:ab"
    titled = Standing(agents=(Agent(id="one", title="ab"),))
    assert orch.named("the ab test", titled, bars) is None
    assert orch.named("the ab test", titled, replace(bars, name_min_chars=2)) == "agent:one"


def test_the_longest_name_wins_and_two_task_agents_of_one_title_name_neither() -> None:
    bars = Bars()
    standing = Standing(
        projects=(ProjectView(name="shop"), ProjectView(name="shop pages")),
        agents=(Agent(id="one", title="Helper cat"), Agent(id="two", title="Helper cat")),
    )
    assert orch.named("fix the shop pages header", standing, bars) == "project:shop pages"
    assert orch.named("tell helper cat to stop", standing, bars) is None
    # A project before a task agent whose title is as long.
    same = Standing(projects=(ProjectView(name="notes"),), agents=(Agent(id="one", title="notes"),))
    assert orch.named("tidy the notes", same, bars) == "project:notes"


def test_where_a_project_s_sentence_goes() -> None:
    standing, bars = _standing(), Bars()
    # A leader takes what is the project's; a member that was named takes its own, beside the leader.
    assert cases_of.as_destination(orch.settle("project:shop", orch.FOLLOW_UP, standing, bars)) == {"action": "to_leader", "project": "shop"}
    named = orch.settle("agent:painter", orch.FOLLOW_UP, standing, bars)
    assert (cases_of.as_destination(named), named.direct) == ({"action": "continue", "agent": "painter"}, True)
    # A project in a git repository gets a leader now.
    assert orch.settle("project:thesis", orch.FOLLOW_UP, standing, bars).action == orch.TO_LEADER
    # No leader: the member that works takes it, not the one that only reads.
    found = orch.settle("project:Recipe Box", orch.FOLLOW_UP, standing, bars)
    assert (found.action, found.agent, found.project) == (orch.CONTINUE, "cook", "Recipe Box")
    # A member that is named takes it itself, though another worked later; one that takes no more work does not.
    older = Agent(id="baker", project="Recipe Box", title="Bread baker", duty="Bakes", idle_seconds=50 * HOUR)
    two = replace(standing, agents=(*standing.agents, older))
    found = orch.settle("agent:baker", orch.FOLLOW_UP, two, bars)
    assert (found.action, found.agent, found.project) == (orch.CONTINUE, "baker", "Recipe Box")
    gone = replace(two, agents=(*standing.agents, replace(older, idle_seconds=999 * HOUR)))
    assert orch.settle("agent:baker", orch.FOLLOW_UP, gone, bars).agent == "cook"
    # None that takes work is left: a new one, in the project.
    retired = replace(standing, agents=tuple(replace(agent, idle_seconds=999 * HOUR) for agent in standing.agents))
    found = orch.settle("project:Recipe Box", orch.FOLLOW_UP, retired, bars)
    assert (found.action, found.project, found.preset) == (orch.SUMMON_IN_PROJECT, "Recipe Box", orch.WORKING_PRESET)


def test_where_a_loose_task_agent_s_sentence_goes() -> None:
    standing, bars = _standing(), Bars()
    # Followed up: it goes on, however often its matter came up.
    found = orch.settle("agent:weekly", orch.FOLLOW_UP, standing, bars)
    assert (found.action, found.agent) == (orch.CONTINUE, "weekly")
    # The same job a third time: a family.
    found = orch.settle("agent:weekly", orch.RECURRENCE, standing, bars)
    assert (found.action, found.members, found.agent) == (orch.FORM_FAMILY, ("weekly",), "weekly")
    # A second time is not yet one, unless the bar says so.
    assert orch.settle("agent:desks", orch.RECURRENCE, standing, bars).action == orch.CONTINUE
    assert orch.settle("agent:desks", orch.RECURRENCE, standing, replace(bars, family_min=2)).action == orch.FORM_FAMILY
    # One that takes no more work: a new loose one.
    found = orch.settle("agent:old", orch.FOLLOW_UP, standing, bars)
    assert (found.action, found.preset) == (orch.SUMMON_STRAY, orch.WORKING_PRESET)
    assert orch.settle("agent:old", orch.FOLLOW_UP, standing, replace(bars, retire_seconds=500 * HOUR)).action == orch.CONTINUE
    # Nothing: a new loose one.
    assert orch.settle(None, orch.NEW, standing, bars).action == orch.SUMMON_STRAY


def test_loose_task_agents_that_did_the_same_thing_become_the_family_together() -> None:
    bars = Bars()
    first = Agent(id="first", duty="Write the changelog", idle_seconds=300 * HOUR, done=(Done(title="Write the changelog for 1.2"),))
    second = Agent(id="second", duty="Write the changelog", idle_seconds=20 * HOUR, done=(Done(title="Write the changelog for 1.3", filed=orch.RECURRENCE),))
    other = Agent(id="other", duty="Draw an avatar", done=(Done(title="Draw an avatar"),))
    standing = Standing(agents=(first, second, other))
    matches = orch.rank("write the changelog for 1.4", standing)
    found = orch.settle("agent:second", orch.RECURRENCE, standing, bars, matches=matches)
    assert (found.action, found.members, found.agent) == (orch.FORM_FAMILY, ("first", "second"), "second")
    # The one that takes work takes it, also when the other was the better match.
    found = orch.settle("agent:first", orch.RECURRENCE, standing, bars, matches=matches)
    assert (found.members, found.agent) == (("first", "second"), "second")
    # None of them takes work any more: the family is made all the same, and a new one does it.
    both_old = Standing(agents=(first, replace(second, idle_seconds=400 * HOUR), other))
    found = orch.settle("agent:second", orch.RECURRENCE, both_old, bars, matches=orch.rank("write the changelog for 1.4", both_old))
    assert (found.action, found.members, found.agent, found.preset) == (
        orch.FORM_FAMILY, ("first", "second"), None, orch.WORKING_PRESET
    )
    # One that scores too little of the match is no peer.
    strict = replace(bars, peer=1.5)
    assert orch.settle("agent:second", orch.RECURRENCE, standing, strict, matches=matches).action == orch.CONTINUE
    assert orch.settle("agent:second", orch.RECURRENCE, standing, replace(bars, peer_hits=99), matches=matches).action == orch.CONTINUE


def test_the_user_asking_for_a_new_one_gets_a_new_one() -> None:
    standing, bars = _standing(), Bars()
    found = orch.by_rules("Start a new one to compare standing desks from scratch", "", standing, bars)
    assert found is not None and found.action == orch.SUMMON_STRAY
    found = orch.by_rules("add a soup to Recipe Box", "", standing, bars)
    assert found is not None and (found.action, found.agent) == (orch.CONTINUE, "cook")
    # A project that is named has it, whatever else the sentence says; a task agent that is named does not.
    found = orch.by_rules("start a new one in Recipe Box for the soups", "", standing, bars)
    assert found is not None and (found.action, found.project) == (orch.CONTINUE, "Recipe Box")
    found = orch.by_rules("do not continue with the report writer, start a new one", "", standing, bars)
    assert found is not None and found.action == orch.SUMMON_STRAY
    assert orch.by_rules("compare some standing desks", "", standing, bars) is None


def test_how_a_sentence_relates_to_its_matter_by_its_words() -> None:
    assert orch.relation_by_words("write this week's report") == orch.RECURRENCE
    assert orch.relation_by_words("fix the typo in that one from last time") == orch.FOLLOW_UP
    assert orch.relation_by_words("also add a summary") == orch.FOLLOW_UP
    assert orch.relation_by_words("write this week's report", names_agent=True) == orch.FOLLOW_UP


def test_a_clear_match_that_would_make_a_project_is_left_to_the_small_model() -> None:
    bars = Bars()
    done = (
        Done(title="Fill in the monthly invoice for the freelance client from the timesheet", filed=orch.RECURRENCE),
        Done(title="Fill in the monthly invoice for the freelance client", filed=orch.NEW),
    )
    standing = Standing(agents=(Agent(id="invoices", duty="Fill in the monthly invoice for the freelance client", done=done),))
    sentence = "fill in the monthly invoice for the freelance client from the timesheet"

    # Clear by its words, and the third time: not settled without a model.
    assert orch.clear_match(orch.rank(sentence, standing), bars)[1] is not None
    verdict, matches = orch.by_retrieval(sentence, "", standing, bars)
    assert verdict is None and matches[0].key == "agent:invoices"
    # The second time makes no project: it goes on there, and nobody is asked.
    once = replace(standing, agents=(replace(standing.agents[0], done=done[1:]),))
    verdict, _matches = orch.by_retrieval("fill in the monthly invoice for the freelance client", "", once, bars)
    assert verdict is not None and (verdict.action, verdict.agent, verdict.relation) == (orch.CONTINUE, "invoices", orch.RECURRENCE)
    # (A word that is nowhere there weighs much: with one, the same sentence is clear no longer.)
    verdict, _matches = orch.by_retrieval(sentence, "", once, bars)
    assert verdict is None
    # Nor does one that follows the work up, however often the job came up.
    verdict, _matches = orch.by_retrieval("also " + sentence, "", standing, bars)
    assert verdict is not None and (verdict.action, verdict.relation) == (orch.CONTINUE, orch.FOLLOW_UP)


def test_retrieval_settles_only_what_is_clear() -> None:
    bars = Bars()
    assert orch.clear_match([], bars) == (True, None)
    assert orch.clear_match([Match("agent:a", 0.9, hits=1)], bars) == (True, None)
    assert orch.clear_match([Match("agent:a", 0.3, hits=3)], bars) == (False, None)
    clear = Match("agent:a", bars.attach, hits=bars.attach_hits)
    assert orch.clear_match([clear, Match("agent:b", 0.1, hits=1)], bars) == (True, clear)
    # Not ahead of the next one, not enough words, not enough of the sentence: not clear.
    assert orch.clear_match([clear, Match("agent:b", bars.attach - bars.margin / 2, hits=3)], bars) == (False, None)
    assert orch.clear_match([replace(clear, hits=bars.attach_hits - 1)], bars) == (False, None)
    assert orch.clear_match([replace(clear, score=bars.attach - 0.01)], bars) == (False, None)
    # A lesser match with words enough keeps it from being nothing.
    assert orch.clear_match([Match("agent:a", 0.2, hits=1), Match("agent:b", 0.1, hits=2)], bars) == (False, None)


def test_the_small_model_is_shown_the_few_best_matches_that_share_anything() -> None:
    standing, bars = _standing(), Bars()
    matches = orch.rank("compare the standing desks", standing)

    assert orch.shown_keys(matches, bars) == ["agent:desks"]
    assert orch.shown_keys(matches, replace(bars, shown=0)) == []
    several = [Match(f"agent:{number}", 0.5, hits=2) for number in range(6)]
    assert orch.shown_keys(several, bars) == [f"agent:{number}" for number in range(bars.shown)]
    # A sentence in a script nothing there is written in shares nothing: those that worked last are shown.
    korean = "다운로드 폴더 다시 정리해 줘"
    unseen = orch.rank(korean, standing)
    assert orch.shown_keys(unseen, bars) == [] and orch.unseen(korean, standing)
    assert orch.shown_keys(unseen, bars, korean, standing) == ["project:shop", "project:Recipe Box", "agent:desks", "agent:weekly"]
    assert not orch.unseen("compare the standing desks", standing) and not orch.unseen("", standing)
    assert not orch.unseen(korean, Standing())


def test_ranking_counts_words_that_say_something() -> None:
    standing = _standing()
    matches = orch.rank("compare the standing desks again please", standing)
    assert matches[0].key == "agent:desks" and matches[0].hits == 3
    # Function words and what every request says share nothing with anything.
    assert all(match.hits == 0 for match in orch.rank("please do this for me now", standing))
    assert orch.content_tokens("帮我把那个的了") == frozenset()
    # Members are matched through their project, not on their own.
    assert {match.key for match in matches} == {"project:Recipe Box", "project:thesis", "project:shop", "agent:weekly", "agent:desks", "agent:old"}
    assert orch.rank("anything", Standing()) == []


def test_the_small_model_s_answer_is_read() -> None:
    keys = ["project:shop", "agent:desks"]
    assert orch.read_answer('{"goes_to": 2, "relation": "recurrence"}', keys) == ("agent:desks", orch.RECURRENCE)
    assert orch.read_answer('```json\n{"goes_to": "1", "relation": "follow_up"}\n```', keys) == ("project:shop", orch.FOLLOW_UP)
    assert orch.read_answer('{"goes_to": 0, "relation": "new"}', keys) == (None, orch.NEW)
    # A relation that is none of them, with a candidate chosen: a follow-up, which makes no family.
    assert orch.read_answer('{"goes_to": 1, "relation": "whatever"}', keys) == ("project:shop", orch.FOLLOW_UP)
    # Said to be new and given a candidate all the same: new, which goes on in nothing that was there.
    assert orch.read_answer('{"goes_to": 2, "relation": "new"}', keys) == (None, orch.NEW)
    for unreadable in (
        "", "no idea", '{"goes_to": 3}', '{"goes_to": true}', '{"relation": "new"}', '{"goes_to": "--1"}', '{"goes_to": "1.5"}'
    ):
        assert orch.read_answer(unreadable, keys) is None


def test_the_question_shows_the_candidates_within_their_cap() -> None:
    from utils.tokenize import count_tokens

    standing = _standing()
    messages = orch.question("compare desks", "desks?", ["project:shop", "agent:desks"], standing, line_max_tokens=30)
    assert "automation assessment agent, " in messages[0]["content"]
    user = messages[1]["content"]
    assert user.splitlines()[:3] == ["REQUEST: compare desks", "USER_SAID: desks?", "CANDIDATES:"]
    assert user.splitlines()[3].startswith("1. [project] shop") and user.splitlines()[4].startswith("2. [agent] Compare standing desks")
    long = replace(standing.agent("desks"), duty="word " * 400)
    line = orch.candidate_line("agent:desks", replace(standing, agents=(long,)), 30)
    assert count_tokens(line) <= 30


def test_a_verdict_is_kept_without_the_sentence() -> None:
    import json

    standing, bars = _standing(), Bars()
    matches = orch.rank("compare the standing desks", standing)
    found = orch.settle("agent:desks", orch.FOLLOW_UP, standing, bars, matches=matches, layer=orch.LAYER_MODEL, model_calls=1, reason="model")
    kept = json.loads(orch.to_json(found))
    assert kept["action"] == "continue" and kept["agent"] == "desks" and kept["layer"] == "model" and kept["model_calls"] == 1
    assert kept["candidates"][0][0] == "agent:desks" and "compare" not in orch.to_json(found)


def test_doing_it_over_from_the_start_is_not_asking_for_another_task_agent():
    standing = orch.Standing(projects=(), agents=(orch.Agent(id="writer", title="Report Writer"),))
    bars = orch.Bars()

    again = orch.by_rules("Ask Report Writer to rebuild the report from scratch", "", standing, bars)
    assert again is not None and (again.action, again.agent) == (orch.CONTINUE, "writer")
    for text in ("с нуля перепиши отчёт, Report Writer", "Report Writer: rehaz el informe desde cero"):
        found = orch.by_rules(text, "", standing, bars)
        assert found is not None and (found.action, found.agent) == (orch.CONTINUE, "writer")
    # Nor is a new part of what it did: "start a new" asks for another one only when it names one.
    # (Nor a new thread or chat, which is what is to be done more often than another task agent.)
    for text in (
        "Ask Report Writer to start a new section in the report",
        "Ask Report Writer to start a new thread in the forum about the report",
        "Report Writer should start a new chat with the client",
    ):
        part = orch.by_rules(text, "", standing, bars)
        assert part is not None and (part.action, part.agent) == (orch.CONTINUE, "writer")
    # Nor is beginning it again, in Chinese: said of the thing, as "from scratch" is.
    for text in (
        "让 Report Writer 把那份周报重新开始写一遍", "Report Writer 重新开个头", "請 Report Writer 重新開一下那個本地服務",
        "让 Report Writer 重新开一个页面放图表", "让 Report Writer 重新开一个对话框查看导出设置",
        "請 Report Writer 重新開一個對話框",
    ):
        begun = orch.by_rules(text, "", standing, bars)
        assert begun is not None and (begun.action, begun.agent) == (orch.CONTINUE, "writer"), text
    # (With nobody named, such a sentence is left to the retrieval and is not a new matter by rule.)
    assert orch.by_rules("把那份周报重新开始写一遍", "", standing, bars) is None
    for text in (
        "另开一只来写周报，不要 Report Writer", "重新开一只写周报，不用 Report Writer", "重新開一隻來寫週報",
        "重新开一个会话来写周报，不用 Report Writer", "重新開一個會話來寫週報，不用 Report Writer",
        "重新开一个新的使魔写周报，不用 Report Writer",
        "重新开一个新会话来写周报，不用 Report Writer", "重新开一个对话写周报，不用 Report Writer",
        "重新開一個新會話，不用 Report Writer", "重新再开一只使魔，不用 Report Writer",
        "重新开个新会话写周报，不用 Report Writer",
    ):
        other = orch.by_rules(text, "", standing, bars)
        assert other is not None and (other.action, other.agent) == (orch.SUMMON_STRAY, None), text
    # Asking for another one is still that, whoever is named.
    for text in (
        "start a new agent for what Report Writer did",
        "Report Writer is busy, start a new one for the report",
        "start a new task agent for the report, not Report Writer",
        "start a new subagent beside Report Writer",
    ):
        other = orch.by_rules(text, "", standing, bars)
        assert other is not None and (other.action, other.agent) == (orch.SUMMON_STRAY, None)


def test_a_sentence_in_another_script_is_shown_what_shares_a_word_with_it_first():
    agents = (
        orch.Agent(id="old", title="Redis sentinel failover notes", idle_seconds=5000.0),
        *(orch.Agent(id=f"new{index}", title=title, idle_seconds=float(index)) for index, title in enumerate(
            ("Holiday photo album", "Tax return checklist", "Garden watering plan", "Birthday party invitations"), start=1
        )),
    )
    standing = orch.Standing(projects=(), agents=agents)
    bars = orch.Bars()
    sentence = "레디스 sentinel 장애 조치 문서를 Redis 기준으로 다시 정리해 줘"
    assert orch.unseen(sentence, standing)

    shown = orch.shown_keys(orch.rank(sentence, standing), bars, sentence, standing)

    # The one that shares its words is shown though four worked since, then those that worked last.
    assert len(shown) == bars.shown and shown[0] == orch.AGENT_KEY + "old"
    assert shown[1:] == [orch.AGENT_KEY + f"new{index}" for index in (1, 2, 3)]
    # With nothing shared, those that worked last, as before.
    nothing = "사진 앨범을 정리해 줘"
    assert orch.shown_keys(orch.rank(nothing, standing), bars, nothing, standing) == [
        orch.AGENT_KEY + f"new{index}" for index in (1, 2, 3, 4)
    ]


def test_a_project_named_goes_to_the_member_that_works_else_to_the_member_it_has():
    project = orch.ProjectView(name="thesis", description="The master's thesis")
    scout = orch.Agent(id="scout", project="thesis", preset="scout", idle_seconds=10.0)
    general = orch.Agent(id="general", project="thesis", preset="general", idle_seconds=500.0)
    over = orch.Agent(id="over", project="thesis", preset="scout", idle_seconds=10.0**9)
    bars = orch.Bars()
    key = orch.PROJECT_KEY + "thesis"

    both = orch.settle(key, orch.FOLLOW_UP, orch.Standing(projects=(project,), agents=(scout, general)), bars)
    assert (both.action, both.agent) == (orch.CONTINUE, "general")
    only = orch.settle(key, orch.FOLLOW_UP, orch.Standing(projects=(project,), agents=(scout,)), bars)
    assert (only.action, only.agent) == (orch.CONTINUE, "scout")
    # One that takes no more work is not gone on with: a new member that works.
    none = orch.settle(key, orch.FOLLOW_UP, orch.Standing(projects=(project,), agents=(over,)), bars)
    assert (none.action, none.agent, none.preset) == (orch.SUMMON_IN_PROJECT, None, orch.WORKING_PRESET)


def test_what_plainly_goes_on_with_a_members_work_goes_to_it_beside_the_leader() -> None:
    project = orch.ProjectView(name="shop", description="The pages of the shop", leader="lead")
    lead = orch.Agent(id="lead", project="shop", preset="boss", title="Shop lead", duty="Runs the shop pages; hands work out")
    home = orch.Agent(
        id="home", project="shop", preset="worker", duty="The home page of the shop: layout, banner and carousel",
        done=(orch.Done(title="rebuild the home page in three columns", result="three columns, banner on top"),),
    )
    detail = orch.Agent(id="detail", project="shop", preset="worker", duty="The template of the detail pages")
    over = orch.Agent(id="over", project="shop", preset="worker", duty=home.duty, idle_seconds=10.0**9)
    bars = orch.Bars()
    standing = orch.Standing(projects=(project,), agents=(lead, home, detail, over))
    key = orch.PROJECT_KEY + "shop"

    def goes(sentence: str, **more: Any) -> tuple[str, str | None, bool]:
        found = orch.settle(key, orch.FOLLOW_UP, more.pop("standing", standing), bars, sentence=sentence, **more)
        return found.action, found.agent, found.direct

    # Five words of its own work and more: the member's, with no round through the leader.
    assert goes("swap the banner and the carousel on the home page for the new year layout") == (orch.CONTINUE, "home", True)
    # Too few, or nothing of any member's: the leader says who does it.
    assert goes("change the banner on the home page") == (orch.TO_LEADER, None, False)
    assert goes("add a newsletter sign-up form to the shop") == (orch.TO_LEADER, None, False)
    assert goes("") == (orch.TO_LEADER, None, False)
    # Two members it fits about as well: nobody's plainly, so the leader's.
    twin = orch.Agent(id="twin", project="shop", preset="worker", duty=home.duty)
    both = orch.Standing(projects=(project,), agents=(lead, home, twin))
    assert goes("swap the banner and the carousel on the home page for the new year layout", standing=both) == (
        orch.TO_LEADER, None, False,
    )
    # Not a member that takes no more work, and never the leader itself, whatever its card says.
    gone = orch.Standing(projects=(project,), agents=(lead, over))
    assert goes("swap the banner and the carousel on the home page for the new year layout", standing=gone)[0] == orch.TO_LEADER
    assert goes("who runs the shop pages and hands the work out to the others")[0] == orch.TO_LEADER
    # A member that is named by its title has it, however few words it shares with the member's
    # work: the sentence names the project too, which is what it was matched with.
    painter = orch.Agent(id="painter", project="shop", preset="worker", title="Page painter", duty="The pictures")
    titled = orch.Standing(projects=(project,), agents=(lead, home, painter))
    assert goes("ask Page painter in shop for an update", standing=titled) == (orch.CONTINUE, "painter", True)
    assert orch.by_rules("ask Page painter in shop for an update", "", titled, bars).agent == "painter"
    # Not two of one title, not one that takes no more work, and not a title the project's name has in it.
    again = orch.Agent(id="again", project="shop", preset="worker", title="Page painter", duty="The icons")
    assert goes("ask Page painter in shop for an update", standing=orch.Standing(projects=(project,), agents=(lead, painter, again)))[0] == orch.TO_LEADER
    left = orch.Agent(id="left", project="shop", preset="worker", title="Page painter", duty="The pictures", idle_seconds=10.0**9)
    assert goes("ask Page painter in shop for an update", standing=orch.Standing(projects=(project,), agents=(lead, left)))[0] == orch.TO_LEADER
    keeper = orch.Agent(id="keeper", project="shop", preset="worker", title="shop", duty="The pictures")
    assert goes("ask the shop for an update", standing=orch.Standing(projects=(project,), agents=(lead, keeper)))[0] == orch.TO_LEADER
    # A member that cannot change files (a scout the leader started to look something up) does not
    # get what goes on with its work by the words alone: what is asked next may be to change the
    # thing it looked at. The leader says who does it.
    scout = orch.Agent(
        id="scout", project="shop", preset="scout", title="Colour finder", changes_files=False,
        duty="Which colours the dark mode of the header on the home page uses",
        done=(orch.Done(title="look up which colours the dark mode of the header uses", result="three greys"),),
    )
    looked = orch.Standing(projects=(project,), agents=(lead, scout, detail))
    warmer = "make the colours of the dark mode of the header warmer"
    assert orch.clear_member(warmer, [scout, detail], bars, "shop") is scout
    assert goes(warmer, standing=looked) == (orch.TO_LEADER, None, False)
    # Not by a match the retrieval or the small model found with it either.
    assert goes(warmer, standing=looked, layer=orch.LAYER_RETRIEVAL)[0] == orch.TO_LEADER
    found = orch.settle(orch.AGENT_KEY + "scout", orch.FOLLOW_UP, looked, bars, layer=orch.LAYER_MODEL, sentence=warmer)
    assert (found.action, found.direct) == (orch.TO_LEADER, False)
    # Nor does the member that can change files and fits less well get it in its place.
    writer = orch.Agent(id="writer", project="shop", preset="worker", duty="The dark mode of the header and its colours")
    assert goes(warmer, standing=orch.Standing(projects=(project,), agents=(lead, scout, writer)))[0] == orch.TO_LEADER
    # Named by its title, it has it: the user chose it.
    assert goes("ask Colour finder in shop which colours the footer uses", standing=looked) == (orch.CONTINUE, "scout", True)
    by_title = orch.by_rules("ask Colour finder which colours the footer uses", "", looked, bars)
    assert (by_title.action, by_title.agent, by_title.direct) == (orch.CONTINUE, "scout", True)
    # Named by her in what she dispatched, and not by the user: nobody chose the scout for a change.
    hers = "Ask Colour finder to make the colours of the dark mode of the header warmer"
    users = "make the colours of the dark mode of the header warmer"
    assert goes(orch.sentence_of(hers, users), standing=looked, said=users) == (orch.TO_LEADER, None, False)
    relayed = orch.by_rules(hers, users, looked, bars)
    assert (relayed.action, relayed.agent, relayed.direct) == (orch.TO_LEADER, None, False)
    assert orch.by_rules(hers, "ask Colour finder about the header", looked, bars).agent == "scout"
    # (With no words of the user's beside hers, hers are all there is to go by.)
    assert orch.by_rules(hers, "", looked, bars).agent == "scout"
    # A member that can change files is sent what she names it for all the same.
    assert goes("Ask Page painter in shop for an update\nhow is the shop coming along", standing=titled, said="how is the shop coming along") == (
        orch.CONTINUE, "painter", True,
    )
    # Of two that are named, the one in the user's own words has it, not the longer title she wrote.
    finder = orch.Agent(id="finder", project="shop", preset="scout", title="Colour finder of the pages", changes_files=False)
    two = orch.Standing(projects=(project,), agents=(lead, finder, painter))
    assert goes(
        "Ask Colour finder of the pages what Page painter did in shop\nwhat did Page painter do", standing=two,
        said="what did Page painter do",
    ) == (orch.CONTINUE, "painter", True)
    # A member the user names has it though its title is in the project's name (which the words
    # of a sentence that names only the project would not be held to name).
    daily = orch.ProjectView(name="daily report", leader="lead")
    reader = orch.Agent(id="reader", project="daily report", preset="scout", title="report", changes_files=False)
    reports = orch.Standing(projects=(daily,), agents=(reader,))
    asked = orch.by_rules("ask report for the numbers of yesterday", "ask report for the numbers of yesterday", reports, bars)
    assert (asked.action, asked.agent, asked.direct) == (orch.CONTINUE, "reader", True)
    # To begin a thing again is no call for a new task agent: it is said of the thing.
    again_at = orch.by_rules("商店网站的 Page painter 重新开始做横幅", "", orch.Standing(
        projects=(orch.ProjectView(name="商店网站", leader="lead"),),
        agents=(orch.Agent(id="p", project="商店网站", preset="worker", title="Page painter"),),
    ), bars)
    assert (again_at.action, again_at.agent, again_at.direct) == (orch.CONTINUE, "p", True)
    # A new one that is asked for is the leader's to start, whichever member the words fit.
    anew = "in shop, start a new agent to swap the banner and the carousel on the home page for the new year layout"
    assert goes(anew) == (orch.TO_LEADER, None, False)
    assert goes("start a new agent, not Page painter, for the pictures in shop", standing=titled)[0] == orch.TO_LEADER
    # The words of the project's own name are no sign of a member's work: every sentence that
    # names the project shares them with every member that worked on it.
    budget = orch.ProjectView(name="记账小程序", leader="L")
    wrote = orch.Agent(
        id="w", project="记账小程序", preset="worker", duty="写记账小程序的 budget.py 和 README",
        done=(orch.Done(title="给记账小程序写 budget.py（用命令行参数 add 加一笔支出），再写 README.md 说明用法", result="写好了"),),
    )
    led = orch.Standing(projects=(budget,), agents=(wrote,))
    named = orch.PROJECT_KEY + "记账小程序"
    other = orch.settle(named, orch.FOLLOW_UP, led, bars, sentence="给记账小程序做一个网页版的界面")
    assert len(orch.content_tokens("给记账小程序做一个网页版的界面") & orch.token_set("记账小程序")) >= bars.member_hits
    assert (other.action, other.direct) == (orch.TO_LEADER, False)
    more = orch.settle(named, orch.FOLLOW_UP, led, bars, sentence="给记账小程序的 budget.py 加一个 list 命令，并在 README.md 里补上说明")
    assert (more.action, more.agent, more.direct) == (orch.CONTINUE, "w", True)
    # A project without a leader goes on as it did: the member that works.
    plain = orch.Standing(projects=(orch.ProjectView(name="shop"),), agents=(home, detail))
    found = orch.settle(key, orch.FOLLOW_UP, plain, bars, sentence="swap the banner and the carousel on the home page")
    assert (found.action, found.direct) == (orch.CONTINUE, False)
    assert orch.as_record(found)["direct"] is False
    assert orch.clear_member("", [home], bars) is None and orch.clear_member("the banner", [], bars) is None


def test_the_scenarios_of_projects_with_a_leader_go_to_the_member_or_to_the_leader_as_they_say() -> None:
    standings, listed = cases_of.load()
    bars = orch.Bars()
    seen = {orch.CONTINUE: 0, orch.TO_LEADER: 0}
    for case in listed:
        standing = standings[case["world"]]
        wanted = case["truth"].get("matter") or ""
        project = standing.project(wanted[len(orch.PROJECT_KEY) :]) if wanted.startswith(orch.PROJECT_KEY) else None
        if project is None or project.leader is None:
            continue
        # Whichever layer finds the project (its name, the words, or the small model): what follows is a rule.
        sentence = orch.sentence_of(case["dispatch"], case["user"])
        found = orch.settle(wanted, orch.FOLLOW_UP, standing, bars, sentence=sentence)
        assert cases_of.as_destination(found) == case["expect"], case["id"]
        assert found.direct is (found.action == orch.CONTINUE), case["id"]
        seen[found.action] += 1
    assert seen == {orch.CONTINUE: 3, orch.TO_LEADER: 7}
