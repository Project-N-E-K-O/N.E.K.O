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
    # A leader takes whatever is the project's, a member that was named included.
    assert cases_of.as_destination(orch.settle("project:shop", orch.FOLLOW_UP, standing, bars)) == {"action": "to_leader", "project": "shop"}
    assert cases_of.as_destination(orch.settle("agent:painter", orch.FOLLOW_UP, standing, bars)) == {"action": "to_leader", "project": "shop"}
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
