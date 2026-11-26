"""The orchestrator's scenarios (``fixtures/orchestrator_scenarios.json``): loading them, and grading a verdict.

Used by the tests that pin the layers that ask no model, and by whoever
measures the layer that does against a real model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nekopower.host import orchestrator
from nekopower.host.orchestrator import Agent, Done, ProjectView, Standing, Verdict
from nekopower.host.templates import builtin_templates

SCENARIOS = Path(__file__).parent / "fixtures" / "orchestrator_scenarios.json"

RIGHT = "right"
# It was attached to work it does not belong with: to something, when it is
# new; or to another matter than its own. The mistake that does most harm:
# the task agent works on it with what it knows of something else.
WRONGLY_ATTACHED = "wrongly_attached"
# It belongs with something that is there and was started as new: one task
# agent too many, and what was known of the matter is not with it.
MISSED = "missed"
# The right matter in the wrong way (gone on with where a project was to be made, say).
WRONG_FORM = "wrong_form"
GRADES = (RIGHT, WRONGLY_ATTACHED, MISSED, WRONG_FORM)


def load() -> tuple[dict[str, Standing], list[dict[str, Any]]]:
    """Return the worlds as what the orchestrator is given, and the cases."""
    data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    worlds: dict[str, Standing] = {}
    for name, world in data["worlds"].items():
        projects = tuple(
            ProjectView(name=item["name"], description=item["description"], in_git=item["git"], leader=item["leader"])
            for item in world["projects"]
        )
        agents = tuple(
            Agent(
                id=item["id"],
                project=item["project"],
                preset=item["preset"],
                title=item["title"],
                duty=item["duty"],
                summary=item["summary"],
                idle_seconds=item["idle_hours"] * 3600.0,
                # As the built-in templates of these presets have it (``Template.changes_files``).
                changes_files=builtin_templates()[item["preset"]].changes_files,
                done=tuple(
                    Done(title=one["title"], result=one["result"], age_seconds=one["hours_ago"] * 3600.0, filed=one["filed"])
                    for one in item["done"]
                ),
            )
            for item in world["agents"]
        )
        worlds[name] = Standing(projects=projects, agents=agents)
    return worlds, data["cases"]


def expected(case: dict[str, Any], family_min: int = 3) -> dict[str, Any]:
    """Return where a case goes when ``family_min`` occurrences make a family (3 as shipped, or 2)."""
    if family_min <= 2 and "expect_family_at_2" in case:
        return case["expect_family_at_2"]
    return case["expect"]


def _matter(where: dict[str, Any], standing: Standing) -> frozenset[str]:
    """What a destination is about: a project, or the loose task agents concerned; empty for a new loose one."""
    action = where.get("action")
    if action == orchestrator.SUMMON_STRAY:
        return frozenset()
    if where.get("project"):
        return frozenset({"project:" + where["project"]})
    if action == orchestrator.FORM_FAMILY:
        return frozenset(where.get("members") or ())
    agent = standing.agent(where.get("agent") or "")
    if agent is not None and agent.project:
        return frozenset({"project:" + agent.project})
    return frozenset({where.get("agent") or ""})


def as_destination(verdict: Verdict) -> dict[str, Any]:
    """Return a verdict in the shape a case's ``expect`` has."""
    where: dict[str, Any] = {"action": verdict.action}
    if verdict.action == orchestrator.CONTINUE:
        where["agent"] = verdict.agent
    elif verdict.action in (orchestrator.TO_LEADER, orchestrator.SUMMON_IN_PROJECT):
        where["project"] = verdict.project
    elif verdict.action == orchestrator.FORM_FAMILY:
        where["members"] = sorted(verdict.members)
        where["agent"] = verdict.agent
    return where


def grade(expect: dict[str, Any], verdict: Verdict, standing: Standing) -> str:
    """Grade a verdict against where the case goes: one of ``GRADES``."""
    got = as_destination(verdict)
    want = dict(expect)
    if "members" in want:
        want["members"] = sorted(want["members"])
    if got == want:
        return RIGHT
    wanted, given = _matter(want, standing), _matter(got, standing)
    if not given:
        return MISSED
    if not wanted or not (wanted & given):
        return WRONGLY_ATTACHED
    return WRONG_FORM
