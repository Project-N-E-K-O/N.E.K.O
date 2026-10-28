"""Optional author entry replies share the runtime suggestion format."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
from services.theater.numeric_v2_actor_output import _parse_actor_suggestions
from services.theater_workshop.sdk.generation.facts import build_fact_sources
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor

from .numeric_v2_fixture import numeric_v2_story
from .test_numeric_v2_generation import _quality_story_context
from .test_sdk_lifecycle import opened


REPLIES = [
    "（轻轻点头）我明白，你按自己的想法来。",
    "（抬头看向柜台）你接下来有什么打算？",
    "（收回目光）好，我继续复习。",
]


@pytest.mark.parametrize("entries", [["（点头）"], REPLIES, ["  （点头）好。  "], ["你愿意多说一点吗？"], ["好" * 120], ["你接下来有什么打算？", "（保持安静）", "（点头）好。"]], ids=['entries0', 'entries2', 'entries4', 'entries7', 'entries8', 'entries10'])
def test_entry_inputs_preserve_author_text_and_accept_runtime_format(entries):
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["entry_player_inputs"] = entries
    before = deepcopy(story)
    compiled = NumericV2Compiler().compile_v2_2(story)
    assert compiled.story == before == story
    assert len(_parse_actor_suggestions(entries)) == len(entries)


@pytest.mark.parametrize("entries", [None, "（点头）好。", [], REPLIES + ["（微笑）谢谢。"], [None], ["   "], ["好" * 121], ["（她点头）好。"], ["好。（点头）"], ["玩家对白：我先听听。"], ["（点头）好。（继续写字）"], ["（点头）好。", "  （点头）好。  "]], ids=['None', '（点头）好。', 'entries4', 'entries5', 'entries6', 'entries11', 'entries13', 'entries14', 'entries18', 'entries20', 'entries26', 'entries28'])
def test_invalid_entry_inputs_are_compile_errors_at_the_author_field(entries):
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["entry_player_inputs"] = entries
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any(issue.path.startswith("nodes[0].story_beat.entry_player_inputs")
               for issue in caught.value.issues)


@pytest.mark.parametrize("terminal_kind", ["ending", "terminal"])
def test_terminal_nodes_reject_entry_inputs(terminal_kind):
    story = numeric_v2_story()
    if terminal_kind == "ending":
        node = next(node for node in story["nodes"] if node["type"] == "ending")
    else:
        node = story["nodes"][0]
        node["terminal"] = True
    node["story_beat"]["entry_player_inputs"] = REPLIES
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert "terminal_entry_player_inputs_forbidden" in {issue.code for issue in caught.value.issues}


def test_absent_entry_inputs_do_not_change_legacy_compilation():
    story = numeric_v2_story()
    compiled = NumericV2Compiler().compile_v2_2(story)
    assert compiled.story == story
    assert compiled.json_bytes == json.dumps(
        story, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.parametrize("entries", [
    REPLIES,
    ["你接下来有什么打算？", "（保持安静）", "（点头）好。"],
])
def test_entry_inputs_survive_real_sdk_import_update_compile_validate_export(opened, entries):
    sdk = opened[0].sdk
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["entry_player_inputs"] = REPLIES[:1]
    project = sdk.import_story(story)
    assert project["story"] == story
    changed = deepcopy(project["story"])
    changed["nodes"][0]["story_beat"]["entry_player_inputs"] = entries
    project = sdk.update_project(project["project_id"], base_revision=project["revision"],
                                 changes={"story": changed})
    compiled = sdk.compile(project["project_id"], base_revision=project["revision"])
    sdk.validate(project["project_id"], base_revision=project["revision"])
    exported = sdk.export(project["project_id"], base_revision=project["revision"])
    assert exported.json_bytes == compiled["json_bytes"]
    assert json.loads(exported.json_bytes) == changed
    imported = sdk.import_story(json.loads(exported.json_bytes))
    assert imported["story"] == changed


def test_node_enhancement_preserves_manual_entry_inputs(opened, monkeypatch):
    sdk = opened[0].sdk
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["entry_player_inputs"] = REPLIES
    project = sdk.import_story(story)
    node_id = story["nodes"][0]["id"]
    monkeypatch.setattr(sdk._generator, "enhance_node", lambda **kwargs: {
        "opening_scene": "柜台旁的灯仍亮着。",
        "entry_player_inputs": ["（点头）替换作者回复。"],
    })
    result = sdk.enhance_node(project["project_id"], node_id, base_revision=project["revision"])
    beat = result["project"]["story"]["nodes"][0]["story_beat"]
    assert beat["opening_scene"] == "柜台旁的灯仍亮着。"
    assert beat["entry_player_inputs"] == REPLIES


def test_quality_context_preserves_declared_entry_text_and_citation_paths():
    story, authoring = _quality_story_context()
    story["nodes"][0]["story_beat"]["entry_player_inputs"] = REPLIES
    branch = deepcopy(story["nodes"][0])
    branch.update(id="entry_branch", type="scene", route_gates=[])
    story["nodes"].append(branch)
    before = deepcopy(story)
    context = NumericV2QualityAssessor._assessment_context(story, {}, authoring)
    rows = [*context["mainline"], *context["endings"],
            *(node for group in context["branches"] for node in group["nodes"])]
    declared = [row for row in rows if row["id"] in {story["nodes"][0]["id"], "entry_branch"}]
    assert len(declared) == 2
    for row in declared:
        assert row["entry_player_inputs"] == REPLIES
    assert all("entry_player_inputs" not in row for row in rows if row not in declared)
    marked, sources = build_fact_sources(context)
    ref = marked["mainline"][0]["entry_player_inputs"][1]["ref"]
    assert sources[ref] == {"path": "/mainline/0/entry_player_inputs/1", "quote": REPLIES[1]}
    declared[0]["entry_player_inputs"].clear()
    assert story == before


def test_quality_entry_inputs_are_counted_before_provider_call(monkeypatch):
    from services.theater_workshop.host import NekoWorkshopModel
    from services.theater_workshop.sdk import WorkshopError
    from utils import llm_client
    from utils.tokenize import count_tokens

    story, authoring = _quality_story_context()
    calls = []
    def capture(messages, **options):
        calls.append(messages)
        raise RuntimeError("captured_quality_request")
    with pytest.raises(RuntimeError, match="captured_quality_request"):
        NumericV2QualityAssessor(capture).assess(story=story, setup={}, authoring=authoring)
    budget = count_tokens(json.dumps(calls[0], ensure_ascii=False)) + 1
    story["nodes"][0]["story_beat"]["entry_player_inputs"] = [
        "（点头）" + word * 116 for word in ("听", "看", "想")
    ]
    def no_provider(**options):
        pytest.fail("entry input text must be budgeted before provider access")
    monkeypatch.setattr(llm_client, "create_chat_llm", no_provider)
    model = NekoWorkshopModel({"model": "selected-model", "api_key": "test-key",
                              "base_url": "https://example.invalid/v1", "max_input_tokens": budget})
    with pytest.raises(WorkshopError, match="workshop_model_input_budget_exceeded"):
        NumericV2QualityAssessor(model).assess(story=story, setup={}, authoring=authoring)
