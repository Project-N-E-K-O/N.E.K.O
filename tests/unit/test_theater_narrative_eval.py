"""验证小剧场叙事评测集的零模型评分、人工边界与脱敏报告。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import ast
import copy
import json
import os
import sys
from pathlib import Path

import pytest

from scripts import run_theater_narrative_eval
from tests.utils import theater_narrative_eval
from tests.utils.theater_narrative_eval import (
    NarrativeEvalSchemaError,
    apply_observations,
    evaluate_dataset,
    load_dataset,
    score_case,
    validate_dataset,
)
from utils import file_utils


FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "theater" / "narrative_eval_v1.json"
)


@pytest.fixture
def dataset() -> dict:
    """每个测试读取一份独立合成数据，避免用例之间共享修改。"""  # noqa: DOCSTRING_CJK
    return load_dataset(FIXTURE_PATH)


def _case(dataset: dict, case_id: str) -> dict:
    """按稳定编号取得合成案例。"""  # noqa: DOCSTRING_CJK
    return next(case for case in dataset["cases"] if case["id"] == case_id)


def _observation_set(
    dataset: dict,
    human_reviews: dict[str, dict] | None = None,
) -> dict:
    """从固定集候选构造完整外部观测，按需附带显式人工复核结论。"""  # noqa: DOCSTRING_CJK
    human_reviews = human_reviews or {}
    cases = []
    for case in dataset["cases"]:
        item = {
            "case_id": case["id"],
            "observed": copy.deepcopy(case["observed"]),
        }
        if case["id"] in human_reviews:
            item["human_review_result"] = copy.deepcopy(human_reviews[case["id"]])
        cases.append(item)
    return {
        "schema_version": 1,
        "dataset_id": dataset["dataset_id"],
        "data_policy": "synthetic_only_no_user_data",
        "cases": cases,
    }


def test_dataset_schema_covers_five_dimensions_and_three_topics(dataset):
    """固定集应含 16 个人工标注案例，并覆盖五维和三个无关题材。"""  # noqa: DOCSTRING_CJK
    assert dataset["data_policy"] == "synthetic_only_no_user_data"
    assert len(dataset["cases"]) == 16
    assert {case["dimension"] for case in dataset["cases"]} == {
        "route",
        "fact_memory",
        "choice_alignment",
        "persona",
        "convergence",
    }
    assert len({case["topic_id"] for case in dataset["cases"]}) == 3
    for dimension in {case["dimension"] for case in dataset["cases"]}:
        assert (
            len(
                {
                    case["topic_id"]
                    for case in dataset["cases"]
                    if case["dimension"] == dimension
                }
            )
            >= 3
        )
    assert {case["source"] for case in dataset["cases"]} == {"synthetic"}


def test_each_dimension_must_keep_three_topic_coverage(dataset):
    """任何单一维度退化为不足三题材时，schema 都必须立即拒绝。"""  # noqa: DOCSTRING_CJK
    narrowed = copy.deepcopy(dataset)
    for case in narrowed["cases"]:
        if case["dimension"] == "persona":
            case["topic_id"] = "orbital_garden"

    with pytest.raises(NarrativeEvalSchemaError, match="persona.*三个合成题材"):
        validate_dataset(narrowed)


def test_report_matches_manual_labels_and_keeps_failure_codes_distinct(dataset):
    """校准报告应复现全部金标，并严格分开机械失败与人工待审。"""  # noqa: DOCSTRING_CJK
    report = evaluate_dataset(dataset)

    assert report["evaluation_mode"] == "calibration"
    assert report["case_count"] == 16
    assert report["automated_summary"] == {
        "eligible": 13,
        "passed": 4,
        "failed": 9,
        "pending": 3,
        "by_failure_code": {
            "context_missing": 1,
            "contract_violation": 2,
            "semantic_mismatch": 5,
            "technical_degraded": 1,
        },
    }
    assert report["human_review_summary"] == {
        "required": 9,
        "passed": 0,
        "failed": 0,
        "pending": 9,
    }
    assert report["manual_label_summary"] == {"matched": 16, "total": 16}


def test_report_excludes_synthetic_context_and_candidate_text(dataset):
    """报告只导出固定编号与原因码，连合成候选全文也不能进入报告。"""  # noqa: DOCSTRING_CJK
    marker = "synthetic_private_marker_7e9a"
    dataset = copy.deepcopy(dataset)
    persona = _case(dataset, "persona_observatory_pending")
    persona["context"]["player_input"] = marker
    persona["observed"]["dialogue"] = marker

    report_text = json.dumps(evaluate_dataset(dataset), ensure_ascii=False)
    assert marker not in report_text
    assert "player_input" not in report_text
    assert '"dialogue":' not in report_text
    assert '"narration":' not in report_text


def test_route_requires_exact_structure_before_semantic_comparison(dataset):
    """路线多字段属于合同违规，字段完整但目标错误才属于语义不符。"""  # noqa: DOCSTRING_CJK
    exact_case = copy.deepcopy(_case(dataset, "route_orbital_exact"))
    exact_case["observed"]["route"]["debug_hint"] = "synthetic"
    assert score_case(exact_case)["failure_code"] == "contract_violation"

    wrong_target = score_case(_case(dataset, "route_puppet_wrong_target"))
    assert wrong_target["verdict"] is False
    assert wrong_target["failure_code"] == "semantic_mismatch"


def test_fact_memory_uses_structured_anchors_and_contradictions(dataset):
    """事实评分应识别缺锚点和明确矛盾，不从对白中猜测事实。"""  # noqa: DOCSTRING_CJK
    missing = score_case(_case(dataset, "fact_observatory_anchor_missing"))
    contradiction = score_case(_case(dataset, "fact_orbital_contradiction"))
    assert missing["failure_code"] == "semantic_mismatch"
    assert contradiction["failure_code"] == "semantic_mismatch"

    malformed = copy.deepcopy(_case(dataset, "fact_puppet_anchor_exact"))
    malformed["observed"]["fact_memory"]["recalled_facts"][0].pop("value")
    assert score_case(malformed)["failure_code"] == "contract_violation"


def test_choice_text_is_not_auto_scored_and_naturalness_stays_pending(dataset):
    """按钮显示文案变化不能伪造自然度结论，结构通过后仍需人工复核。"""  # noqa: DOCSTRING_CJK
    case = copy.deepcopy(_case(dataset, "choice_observatory_contract_pass"))
    case["observed"]["narration"] = "完全不自然的合成占位对白"
    for choice in case["observed"]["choice_alignment"]["choices"]:
        choice["text"] = "完全不相关的合成按钮文案"

    result = score_case(case)
    assert result["verdict"] is True
    assert result["failure_code"] is None
    assert result["human_review"] == {
        "criteria": ["dialogue_choice_naturalness"],
        "verdict": None,
        "failure_code": "human_review_pending",
    }


def test_choice_contract_and_author_core_have_different_failure_codes(dataset):
    """按钮类型错误是合同违规，稳定按钮下的作者核心偏移是语义不符。"""  # noqa: DOCSTRING_CJK
    wrong_type = score_case(_case(dataset, "choice_puppet_wrong_input_type"))
    wrong_core = score_case(_case(dataset, "choice_orbital_author_core_mismatch"))
    assert wrong_type["failure_code"] == "contract_violation"
    assert wrong_core["failure_code"] == "semantic_mismatch"
    assert wrong_type["human_review"]["verdict"] is None
    assert wrong_core["human_review"]["verdict"] is None


def test_persona_always_returns_null_and_human_review_pending(dataset):
    """不论候选对白看似好坏，人设一致性都不能被关键词规则自动判通过。"""  # noqa: DOCSTRING_CJK
    for case_id in (
        "persona_observatory_pending",
        "persona_puppet_pending",
        "persona_orbital_pending",
    ):
        case = copy.deepcopy(_case(dataset, case_id))
        case["observed"]["dialogue"] = "好、坏、符合、违背都只是合成关键词"
        result = score_case(case)
        assert result["verdict"] is None
        assert result["failure_code"] is None
        assert result["human_review"]["verdict"] is None


def test_convergence_scores_structure_but_not_naturalness(dataset):
    """收束闭环可自动校验，情节是否自然仍固定输出人工待审。"""  # noqa: DOCSTRING_CJK
    passed = score_case(_case(dataset, "convergence_puppet_structure_pass"))
    wrong_target = score_case(_case(dataset, "convergence_observatory_wrong_target"))
    malformed = score_case(_case(dataset, "convergence_orbital_contract_violation"))

    assert passed["verdict"] is True
    assert passed["human_review"]["failure_code"] == "human_review_pending"
    assert wrong_target["failure_code"] == "semantic_mismatch"
    assert malformed["failure_code"] == "contract_violation"
    assert all(
        result["human_review"]["verdict"] is None
        for result in (passed, wrong_target, malformed)
    )


def test_context_missing_and_technical_degraded_are_not_semantic_failures(dataset):
    """上下文缺失与技术降级应保留独立原因码，便于后续定位真实退化来源。"""  # noqa: DOCSTRING_CJK
    context_missing = score_case(_case(dataset, "route_observatory_context_missing"))
    technical = score_case(_case(dataset, "route_orbital_technical_degraded"))
    assert context_missing["failure_code"] == "context_missing"
    assert technical["failure_code"] == "technical_degraded"


def test_missing_human_config_or_auto_pass_persona_label_is_rejected(dataset):
    """主观项缺少人工配置或人设被标成自动通过时，数据集必须直接拒绝。"""  # noqa: DOCSTRING_CJK
    missing_config = copy.deepcopy(dataset)
    _case(missing_config, "choice_observatory_contract_pass").pop("human_review")
    with pytest.raises(NarrativeEvalSchemaError, match="human_review"):
        validate_dataset(missing_config)

    auto_pass = copy.deepcopy(dataset)
    label = _case(auto_pass, "persona_observatory_pending")["manual_label"]
    label.update(verdict=True, failure_code=None)
    with pytest.raises(NarrativeEvalSchemaError, match="人设金标"):
        validate_dataset(auto_pass)


def test_default_scorer_has_no_model_or_network_call_path():
    """默认 CI 评分模块只能依赖标准库，也不能包含常见模型或网络调用入口。"""  # noqa: DOCSTRING_CJK
    source_path = Path(theater_narrative_eval.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    called_attributes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attributes.add(node.func.attr)

    assert imported_roots <= {
        "__future__",
        "collections",
        "copy",
        "json",
        "pathlib",
        "re",
        "typing",
    }
    assert called_attributes.isdisjoint(
        {"invoke", "ainvoke", "request", "post", "generate"}
    )


def test_external_observations_replace_only_candidates(dataset):
    """外部候选只能替换 observed，并从副本中彻底移除校准标签。"""  # noqa: DOCSTRING_CJK
    observation_set = _observation_set(dataset)
    original = copy.deepcopy(dataset)

    evaluated = apply_observations(dataset, observation_set)

    assert evaluated["evaluation_mode"] == "candidate"
    assert all("manual_label" not in case for case in evaluated["cases"])
    assert all(
        candidate["observed"] == source["observed"]
        for candidate, source in zip(evaluated["cases"], original["cases"], strict=True)
    )
    assert dataset == original
    assert evaluated is not dataset


def test_external_observations_require_exact_case_set(dataset):
    """候选缺案例或混入未知编号时必须拒绝，避免部分样本被静默漏评。"""  # noqa: DOCSTRING_CJK
    observation_set = _observation_set(dataset)
    observation_set["cases"] = observation_set["cases"][1:]

    with pytest.raises(NarrativeEvalSchemaError, match="案例集合不完整"):
        apply_observations(dataset, observation_set)


def test_external_report_does_not_reuse_calibration_labels(dataset):
    """候选副本应在默认 API 路径下自动隔离固定集校准标签。"""  # noqa: DOCSTRING_CJK
    evaluated = apply_observations(dataset, _observation_set(dataset))
    report = evaluate_dataset(evaluated)

    assert report["evaluation_mode"] == "candidate"
    assert "manual_label_summary" not in report
    assert all("manual_label_match" not in case for case in report["cases"])

    poisoned = copy.deepcopy(evaluated)
    poisoned["cases"][0]["manual_label"] = copy.deepcopy(
        dataset["cases"][0]["manual_label"]
    )
    with pytest.raises(NarrativeEvalSchemaError, match="不能携带校准 manual_label"):
        validate_dataset(poisoned)


def test_external_human_reviews_can_pass_fail_or_stay_pending(dataset):
    """三类主观维度只接受显式人工结论，并分别保留通过、失败和待审。"""  # noqa: DOCSTRING_CJK
    human_reviews = {
        "persona_observatory_pending": {
            "criteria": ["persona_consistency"],
            "verdict": True,
            "failure_code": None,
        },
        "choice_observatory_contract_pass": {
            "criteria": ["dialogue_choice_naturalness"],
            "verdict": False,
            "failure_code": "human_review_failed",
        },
        "convergence_puppet_structure_pass": {
            "criteria": ["convergence_naturalness"],
            "verdict": None,
            "failure_code": "human_review_pending",
        },
    }
    evaluated = apply_observations(
        dataset,
        _observation_set(dataset, human_reviews),
    )
    report = evaluate_dataset(evaluated)
    by_id = {case["case_id"]: case for case in report["cases"]}

    assert by_id["persona_observatory_pending"]["human_review"]["verdict"] is True
    assert (
        by_id["choice_observatory_contract_pass"]["human_review"]["failure_code"]
        == "human_review_failed"
    )
    assert by_id["convergence_puppet_structure_pass"]["human_review"]["verdict"] is None
    assert report["human_review_summary"] == {
        "required": 9,
        "passed": 1,
        "failed": 1,
        "pending": 7,
    }


def test_external_human_review_requires_matching_criterion_and_failure_code(dataset):
    """人工结果的评测项或失败码不匹配时，不得静默接纳。"""  # noqa: DOCSTRING_CJK
    invalid_review = {
        "persona_observatory_pending": {
            "criteria": ["persona_consistency"],
            "verdict": False,
            "failure_code": None,
        }
    }

    with pytest.raises(NarrativeEvalSchemaError, match="人工复核失败码无效"):
        apply_observations(dataset, _observation_set(dataset, invalid_review))

    wrong_criterion = copy.deepcopy(invalid_review)
    review = wrong_criterion["persona_observatory_pending"]
    review.update(
        criteria=["convergence_naturalness"],
        verdict=True,
        failure_code=None,
    )
    with pytest.raises(NarrativeEvalSchemaError, match="人工复核项目不匹配"):
        apply_observations(dataset, _observation_set(dataset, wrong_criterion))


def test_cli_calibration_mode_ignores_intentional_bad_examples(
    dataset,
    tmp_path,
    monkeypatch,
    capsys,
):
    """校准模式只核对评分器与标签，固定集里的坏例不能导致命令失败。"""  # noqa: DOCSTRING_CJK
    output_path = tmp_path / "calibration-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(FIXTURE_PATH),
            "--output",
            str(output_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 0
    stdout = capsys.readouterr().out
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert "evaluation_mode=calibration" in stdout
    assert "automated_failed=9" in stdout
    assert report["manual_label_summary"] == {"matched": 16, "total": 16}


def test_cli_candidate_mode_fails_on_automated_failure_and_stays_redacted(
    dataset,
    tmp_path,
    monkeypatch,
    capsys,
):
    """候选模式默认启用机械门禁，且控制台与报告都不得泄漏候选正文。"""  # noqa: DOCSTRING_CJK
    marker = "synthetic_cli_private_marker_5f2b"
    observation_set = _observation_set(dataset)
    persona_item = next(
        item
        for item in observation_set["cases"]
        if item["case_id"] == "persona_observatory_pending"
    )
    persona_item["observed"]["dialogue"] = marker
    observation_path = tmp_path / "observations.json"
    observation_path.write_text(
        json.dumps(observation_set, ensure_ascii=False),
        encoding="utf-8",
    )
    output_path = tmp_path / "candidate-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(FIXTURE_PATH),
            "--observations",
            str(observation_path),
            "--output",
            str(output_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 1
    stdout = capsys.readouterr().out
    report_text = output_path.read_text(encoding="utf-8")
    assert "evaluation_mode=candidate" in stdout
    assert "automated_failed=9" in stdout
    assert marker not in stdout
    assert marker not in report_text
    assert "manual_label_summary" not in json.loads(report_text)


@pytest.mark.parametrize("protected_input", ["dataset", "observations"])
@pytest.mark.parametrize("alias_kind", ["same_path", "symlink", "hardlink"])
def test_cli_rejects_output_aliasing_any_input_without_touching_it(
    dataset,
    tmp_path,
    monkeypatch,
    capsys,
    protected_input,
    alias_kind,
):
    """输出的直接路径、符号链接和硬链接都不能覆盖任一评测输入。"""  # noqa: DOCSTRING_CJK
    dataset_path = tmp_path / "dataset.json"
    observation_path = tmp_path / "observations.json"
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False),
        encoding="utf-8",
    )
    observation_path.write_text(
        json.dumps(_observation_set(dataset), ensure_ascii=False),
        encoding="utf-8",
    )
    protected_path = dataset_path if protected_input == "dataset" else observation_path
    output_path = protected_path
    if alias_kind != "same_path":
        output_path = tmp_path / f"{protected_input}-{alias_kind}-report.json"
        if alias_kind == "symlink":
            output_path.symlink_to(protected_path)
        else:
            os.link(protected_path, output_path)
    original_dataset = dataset_path.read_bytes()
    original_observations = observation_path.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(dataset_path),
            "--observations",
            str(observation_path),
            "--output",
            str(output_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "theater_narrative_eval_error=output_conflicts_input\n"
    assert dataset_path.read_bytes() == original_dataset
    assert observation_path.read_bytes() == original_observations
    assert str(dataset_path) not in captured.err
    assert str(observation_path) not in captured.err
    if alias_kind == "symlink":
        assert output_path.is_symlink()
    elif alias_kind == "hardlink":
        assert output_path.samefile(protected_path)


def test_cli_rejects_lexically_different_output_resolving_to_dataset(
    dataset,
    tmp_path,
    monkeypatch,
    capsys,
):
    """含父目录跳转的不同写法解析到数据集时仍必须在读取前拒绝。"""  # noqa: DOCSTRING_CJK
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False),
        encoding="utf-8",
    )
    original_dataset = dataset_path.read_bytes()
    nested = tmp_path / "nested"
    nested.mkdir()
    output_path = nested / ".." / dataset_path.name
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(dataset_path),
            "--output",
            str(output_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "theater_narrative_eval_error=output_conflicts_input\n"
    assert dataset_path.read_bytes() == original_dataset


@pytest.mark.parametrize(
    ("invalid_input", "raw", "expected_code"),
    [
        ("dataset", '{"SECRET_DATASET_JSON":', "dataset_json_invalid"),
        (
            "dataset",
            '{"SECRET_DATASET_SCHEMA":"invalid"}',
            "dataset_schema_invalid",
        ),
        (
            "observations",
            '{"SECRET_OBSERVATIONS_JSON":',
            "observations_json_invalid",
        ),
        (
            "observations",
            '{"SECRET_OBSERVATIONS_SCHEMA":"invalid"}',
            "observations_schema_invalid",
        ),
    ],
)
def test_cli_input_failures_use_stable_redacted_codes_and_preserve_files(
    dataset,
    tmp_path,
    monkeypatch,
    capsys,
    invalid_input,
    raw,
    expected_code,
):
    """坏 JSON 与坏 schema 只输出稳定码，不能改写输入或留下报告。"""  # noqa: DOCSTRING_CJK
    dataset_path = tmp_path / "SECRET_DATASET_PATH.json"
    observation_path = tmp_path / "SECRET_OBSERVATIONS_PATH.json"
    dataset_path.write_text(
        raw if invalid_input == "dataset" else json.dumps(dataset, ensure_ascii=False),
        encoding="utf-8",
    )
    observation_path.write_text(
        raw
        if invalid_input == "observations"
        else json.dumps(_observation_set(dataset), ensure_ascii=False),
        encoding="utf-8",
    )
    output_path = tmp_path / "report.json"
    original_dataset = dataset_path.read_bytes()
    original_observations = observation_path.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(dataset_path),
            "--observations",
            str(observation_path),
            "--output",
            str(output_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == f"theater_narrative_eval_error={expected_code}\n"
    assert "SECRET_" not in captured.err
    assert str(tmp_path) not in captured.err
    assert dataset_path.read_bytes() == original_dataset
    assert observation_path.read_bytes() == original_observations
    assert not output_path.exists()


def test_cli_atomic_write_failure_preserves_inputs_and_existing_report(
    dataset,
    tmp_path,
    monkeypatch,
    capsys,
):
    """原子替换失败时旧报告和两份输入都必须保持逐字不变。"""  # noqa: DOCSTRING_CJK
    dataset_path = tmp_path / "dataset.json"
    observation_path = tmp_path / "observations.json"
    output_path = tmp_path / "report.json"
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False),
        encoding="utf-8",
    )
    observation_path.write_text(
        json.dumps(_observation_set(dataset), ensure_ascii=False),
        encoding="utf-8",
    )
    output_path.write_bytes(b"ORIGINAL_REPORT_SENTINEL\n")
    original_dataset = dataset_path.read_bytes()
    original_observations = observation_path.read_bytes()
    original_report = output_path.read_bytes()

    def _replace_failed(_source, _target):
        """模拟提交点失败，异常正文不得进入命令输出。"""  # noqa: DOCSTRING_CJK
        raise OSError("SECRET_ATOMIC_REPLACE_FAILURE")

    monkeypatch.setattr(file_utils.os, "replace", _replace_failed)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(dataset_path),
            "--observations",
            str(observation_path),
            "--output",
            str(output_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "theater_narrative_eval_error=output_write_failed\n"
    assert "SECRET_" not in captured.err
    assert str(tmp_path) not in captured.err
    assert dataset_path.read_bytes() == original_dataset
    assert observation_path.read_bytes() == original_observations
    assert output_path.read_bytes() == original_report
    assert list(tmp_path.glob(f".{output_path.name}.*.tmp")) == []


def test_cli_missing_dataset_output_conflict_does_not_create_input(
    tmp_path,
    monkeypatch,
    capsys,
):
    """缺失数据集与输出同路径时也必须先拒绝，不能创建伪输入文件。"""  # noqa: DOCSTRING_CJK
    missing_path = tmp_path / "missing-dataset.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(missing_path),
            "--output",
            str(missing_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "theater_narrative_eval_error=output_conflicts_input\n"
    assert not missing_path.exists()


@pytest.mark.parametrize(
    ("invalid_input", "expected_code"),
    [
        ("dataset", "dataset_read_failed"),
        ("observations", "observations_read_failed"),
    ],
)
def test_cli_unicode_failures_are_redacted_and_keep_existing_report(
    dataset,
    tmp_path,
    monkeypatch,
    capsys,
    invalid_input,
    expected_code,
):
    """非 UTF-8 输入只返回角色明确的读取错误，旧报告不得被截断。"""  # noqa: DOCSTRING_CJK
    dataset_path = tmp_path / "dataset.json"
    observation_path = tmp_path / "observations.json"
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False),
        encoding="utf-8",
    )
    observation_path.write_text(
        json.dumps(_observation_set(dataset), ensure_ascii=False),
        encoding="utf-8",
    )
    invalid_path = dataset_path if invalid_input == "dataset" else observation_path
    invalid_path.write_bytes(b"\xffSECRET_INVALID_UTF8")
    output_path = tmp_path / "report.json"
    output_path.write_bytes(b"ORIGINAL_REPORT_SENTINEL\n")
    original_report = output_path.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(dataset_path),
            "--observations",
            str(observation_path),
            "--output",
            str(output_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == f"theater_narrative_eval_error={expected_code}\n"
    assert "SECRET_" not in captured.err
    assert str(tmp_path) not in captured.err
    assert output_path.read_bytes() == original_report


def test_cli_internal_error_is_redacted_and_keeps_existing_report(
    dataset,
    tmp_path,
    monkeypatch,
    capsys,
):
    """未预期异常不能产生 traceback、泄漏异常正文或覆盖既有报告。"""  # noqa: DOCSTRING_CJK
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False),
        encoding="utf-8",
    )
    output_path = tmp_path / "report.json"
    output_path.write_bytes(b"ORIGINAL_REPORT_SENTINEL\n")
    original_report = output_path.read_bytes()

    def _malformed_internal_report(_dataset):
        """模拟评分器返回坏内部结构，报告不能在摘要失败前先提交。"""  # noqa: DOCSTRING_CJK
        return {
            "evaluation_mode": "calibration",
            "SECRET_INTERNAL_REPORT": "must_not_be_written",
        }

    monkeypatch.setattr(
        run_theater_narrative_eval,
        "evaluate_dataset",
        _malformed_internal_report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_theater_narrative_eval.py",
            "--dataset",
            str(dataset_path),
            "--output",
            str(output_path),
        ],
    )

    assert run_theater_narrative_eval.main() == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "theater_narrative_eval_error=internal_error\n"
    assert "SECRET_" not in captured.err
    assert str(tmp_path) not in captured.err
    assert output_path.read_bytes() == original_report
