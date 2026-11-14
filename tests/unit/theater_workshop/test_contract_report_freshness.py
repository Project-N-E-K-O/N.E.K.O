"""Old or incomplete contract coverage must be reassessed before applying repairs."""

from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_generation import _generation_setup, _quality_payload, _quality_story_context, _reviewed_fixture


def _fixture():
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    payload = _quality_payload(weak_dimension="plot", target_node_id="mainline_01")
    payload["issues"][0]["repair_targets"] = [{"node_id": "mainline_01", "field": "/character_state/catgirl"}]
    report = _reviewed_fixture(assessor._validated_assessment(context, payload), context)
    return story, authoring, assessor, report


@pytest.mark.parametrize("case", ["missing", "partial", "duplicate", "wrong_kind", "wrong_path", "foreign", "malformed", "missing_matrix"])
def test_incomplete_contract_report_cannot_reach_repair_model(case):
    story, authoring, assessor, report = _fixture()
    checks = report["contract_check"]["checks"]
    if case == "missing":
        del report["contract_check"]
    elif case == "partial":
        checks.pop()
    elif case == "duplicate":
        checks[-1] = deepcopy(checks[0])
    elif case == "wrong_kind":
        checks[0]["kind"] = "other"
    elif case == "wrong_path":
        checks[0]["definition_path"] = "/mainline/0/summary"
    elif case == "foreign":
        checks[0]["target_id"] = "unreviewed-node:completion:0"
    elif case == "missing_matrix":
        next(check for check in checks if check.get("kind") == "entry_ownership").pop("ownership_checks")
    else:
        checks[0]["kind"] = {"unexpected": "object"}
    before = deepcopy((story, authoring, report))
    assessor.call_llm = lambda *_args, **_kwargs: pytest.fail("Incomplete review reached the repair model")
    with pytest.raises(QualityAssessmentError) as caught:
        assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                               assessment=report, node_id="mainline_01")
    assert caught.value.code == "quality_reassessment_required"
    assert (story, authoring, report) == before


def test_complete_contract_report_allows_existing_single_repair():
    story, authoring, assessor, report = _fixture()
    calls = []

    def reply(_messages, **options):
        calls.append(options["operation"])
        return json.dumps({"node_updates": [{"node_id": "mainline_01", "story_beat": {
            "character_state": {"catgirl_state": "女主双手空着，旧信已经放在柜台上。"},
        }}]}, ensure_ascii=False)

    assessor.call_llm = reply
    result = assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                                    assessment=report, node_id="mainline_01")
    assert calls == ["numeric_v2_quality_single_node_optimization"]
    assert result["nodes"][1:] == story["nodes"][1:]
