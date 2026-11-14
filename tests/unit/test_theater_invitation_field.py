"""intro.invitation: the author's spoken fallback for a theater invite."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from services.theater.numeric_v2 import (
    INVITATION_META_TERMS,
    MAX_INVITATION_CHARS,
    NumericV2CompileError,
    NumericV2Compiler,
    _Collector,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED = sorted((REPO_ROOT / "assets" / "theater").glob("story_*.json"))


def _codes(story):
    try:
        NumericV2Compiler().compile_v2_2(story)
    except NumericV2CompileError as error:
        return [issue.code for issue in error.issues]
    return []


def _story():
    return json.loads(BUNDLED[0].read_text(encoding="utf-8"))


def test_older_packages_without_an_invitation_still_compile():
    story = _story()
    del story["intro"]["invitation"]
    assert _codes(story) == []


@pytest.mark.parametrize("invitation,code", [
    ("", "required_text"),
    ("长" * (MAX_INVITATION_CHARS + 1), "intro_invitation_too_long"),
    *[(f"要不要和我一起{term}？", "intro_invitation_meta_term") for term in INVITATION_META_TERMS],
])
def test_invitation_must_work_as_a_spoken_line(invitation, code):
    story = _story()
    story["intro"]["invitation"] = invitation
    assert code in _codes(story)


def test_invitation_uses_you_and_me_rather_than_author_names():
    story = deepcopy(_story())
    story["intro"]["player_name"] = "林舟"
    story["intro"]["catgirl_name"] = "小葵"
    story["intro"]["player_identity"] = "林舟，" + story["intro"]["player_identity"].split("，", 1)[1]
    story["intro"]["catgirl_identity"] = "小葵，" + story["intro"]["catgirl_identity"].split("，", 1)[1]
    story["intro"]["invitation"] = "林舟，旧车站的灯还亮着，要不要进来看看？"
    assert "intro_invitation_author_name" in _codes(story)
    story["intro"]["invitation"] = "旧车站的灯还亮着，你要不要进来看看？"
    assert "intro_invitation_author_name" not in _codes(story)


@pytest.mark.parametrize("explicit", [False, True])
def test_single_character_name_allows_pronouns_in_invitation(explicit):
    story = _story()
    intro = story["intro"]
    intro["player_identity"] = "我，归乡人"
    intro["catgirl_identity"] = "小葵，守灯人"
    intro["invitation"] = "要不要和我一起去看看？"
    if explicit:
        intro.update(player_name="我", catgirl_name="小葵")
    else:
        intro.pop("player_name", None)
        intro.pop("catgirl_name", None)
    collector = _Collector()
    NumericV2Compiler._validate_intro(collector, intro)
    assert collector.issues == []
    intro["invitation"] = "小葵，要不要和我一起去看看？"
    NumericV2Compiler._validate_intro(collector, intro)
    assert [issue.code for issue in collector.issues] == ["intro_invitation_author_name"]


@pytest.mark.parametrize("path", BUNDLED, ids=lambda path: path.stem)
def test_bundled_stories_carry_a_valid_invitation(path):
    story = json.loads(path.read_text(encoding="utf-8"))
    assert story["intro"]["invitation"].strip()
    assert _codes(story) == []


def test_workshop_requires_an_invitation_and_exports_it():
    from services.theater_workshop.sdk.generation import numeric_v2 as generation
    from tests.unit.theater_workshop.test_numeric_v2_generation import _idea_outline

    assert "invitation" in json.loads(generation._MAINLINE_OUTPUT_CONTRACT)["world"]
    candidate = _idea_outline()
    assert not any(issue["path"] == "world.invitation"
                   for issue in generation._validate_idea_outline(candidate, minimum=4, maximum=6))
    del candidate["world"]["invitation"]
    assert any(issue["path"] == "world.invitation"
               for issue in generation._validate_idea_outline(candidate, minimum=4, maximum=6))
