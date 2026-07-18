"""为小剧场叙事回归提供零模型调用的结构化评测器。"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DATA_POLICY = "synthetic_only_no_user_data"
CALIBRATION_MODE = "calibration"
CANDIDATE_MODE = "candidate"
EVALUATION_MODES = frozenset({CALIBRATION_MODE, CANDIDATE_MODE})
DIMENSIONS = frozenset(
    {"route", "fact_memory", "choice_alignment", "persona", "convergence"}
)
FAILURE_CODES = frozenset(
    {
        "context_missing",
        "technical_degraded",
        "semantic_mismatch",
        "contract_violation",
    }
)
HUMAN_REVIEW_FAILURE_CODES = frozenset({"human_review_failed", "human_review_pending"})

_CASE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_ROUTE_KEYS = frozenset({"kind", "target_id", "reason_code"})
_FACT_KEYS = frozenset({"key", "value"})
_CHOICE_KEYS = frozenset({"id", "input_kind", "author_core"})
_CONVERGENCE_KEYS = frozenset({"status", "target_node_id", "resolved_fact_keys"})
_HUMAN_CRITERIA = {
    "route": (),
    "fact_memory": (),
    "choice_alignment": ("dialogue_choice_naturalness",),
    "persona": ("persona_consistency",),
    "convergence": ("convergence_naturalness",),
}


class NarrativeEvalSchemaError(ValueError):
    """表示评测集的人工标注或固定评分配置不完整。"""


def load_dataset(path: str | Path) -> dict[str, Any]:
    """读取并校验一份合成叙事评测集。"""
    with Path(path).open(encoding="utf-8") as file:
        dataset = json.load(file)
    validate_dataset(dataset)
    return dataset


def apply_observations(
    dataset: dict[str, Any],
    observation_set: Any,
) -> dict[str, Any]:
    """生成外部候选评测副本，并彻底移除只属于校准样本的人工标签。"""
    validate_dataset(dataset)
    if _evaluation_mode(dataset) != CALIBRATION_MODE:
        raise NarrativeEvalSchemaError("只能向原始校准集应用外部候选观测")
    if not isinstance(observation_set, dict):
        raise NarrativeEvalSchemaError("候选观测集根节点必须是对象")
    if observation_set.get("schema_version") != SCHEMA_VERSION:
        raise NarrativeEvalSchemaError("候选观测集 schema_version 不受支持")
    if observation_set.get("dataset_id") != dataset["dataset_id"]:
        raise NarrativeEvalSchemaError("候选观测集与评测集编号不一致")
    if observation_set.get("data_policy") != DATA_POLICY:
        raise NarrativeEvalSchemaError("候选观测集必须声明仅使用合成数据")

    observations = observation_set.get("cases")
    if not isinstance(observations, list):
        raise NarrativeEvalSchemaError("候选观测集 cases 必须是数组")
    observed_by_id: dict[str, dict[str, Any]] = {}
    human_review_by_id: dict[str, dict[str, Any]] = {}
    for item in observations:
        required_keys = {"case_id", "observed"}
        allowed_keys = required_keys | {"human_review_result"}
        if (
            not isinstance(item, dict)
            or not required_keys.issubset(item)
            or not set(item).issubset(allowed_keys)
        ):
            raise NarrativeEvalSchemaError(
                "候选观测项只能包含 case_id、observed 与 human_review_result"
            )
        case_id = item.get("case_id")
        observed = item.get("observed")
        if not isinstance(case_id, str) or not _CASE_ID_PATTERN.fullmatch(case_id):
            raise NarrativeEvalSchemaError("候选观测项缺少稳定 case_id")
        if case_id in observed_by_id:
            raise NarrativeEvalSchemaError(f"候选观测编号重复：{case_id}")
        if not isinstance(observed, dict):
            raise NarrativeEvalSchemaError(f"候选观测 {case_id} 必须是对象")
        observed_by_id[case_id] = copy.deepcopy(observed)
        if "human_review_result" in item:
            human_review_by_id[case_id] = copy.deepcopy(item["human_review_result"])

    expected_ids = {case["id"] for case in dataset["cases"]}
    if set(observed_by_id) != expected_ids:
        missing = sorted(expected_ids - set(observed_by_id))
        unknown = sorted(set(observed_by_id) - expected_ids)
        detail = f"缺少={missing}，未知={unknown}"
        raise NarrativeEvalSchemaError(f"候选观测案例集合不完整：{detail}")

    evaluated = copy.deepcopy(dataset)
    evaluated["evaluation_mode"] = CANDIDATE_MODE
    for case in evaluated["cases"]:
        case_id = case["id"]
        case["observed"] = observed_by_id[case_id]
        # manual_label 只描述固定集内置候选，外部候选副本不得继续携带它。
        case.pop("manual_label", None)
        if case_id in human_review_by_id:
            result = human_review_by_id[case_id]
            _validate_human_review_result(
                case_id,
                case["human_review"]["criteria"],
                result,
            )
            case["human_review_result"] = result
    validate_dataset(evaluated)
    return evaluated


def validate_dataset(dataset: Any) -> None:
    """校验评测集配置；候选输出本身的错误留给评分器归类。"""
    if not isinstance(dataset, dict):
        raise NarrativeEvalSchemaError("评测集根节点必须是对象")
    if dataset.get("schema_version") != SCHEMA_VERSION:
        raise NarrativeEvalSchemaError("评测集 schema_version 不受支持")
    if dataset.get("data_policy") != DATA_POLICY:
        raise NarrativeEvalSchemaError("评测集必须声明仅使用合成数据")
    evaluation_mode = _evaluation_mode(dataset)

    dataset_id = dataset.get("dataset_id")
    if not isinstance(dataset_id, str) or not _CASE_ID_PATTERN.fullmatch(dataset_id):
        raise NarrativeEvalSchemaError("dataset_id 必须是稳定的 ASCII snake_case")

    topics = dataset.get("topics")
    if not isinstance(topics, list) or len(topics) < 3:
        raise NarrativeEvalSchemaError("评测集至少需要三个互不依赖的合成题材")
    topic_ids = _validate_topics(topics)

    cases = dataset.get("cases")
    if not isinstance(cases, list) or not 12 <= len(cases) <= 16:
        raise NarrativeEvalSchemaError("评测集案例数必须在 12 到 16 之间")

    case_ids: set[str] = set()
    covered_dimensions: set[str] = set()
    topics_by_dimension = {dimension: set() for dimension in DIMENSIONS}
    for case in cases:
        case_id, dimension, topic_id = _validate_case_configuration(
            case,
            topic_ids,
            evaluation_mode,
        )
        if case_id in case_ids:
            raise NarrativeEvalSchemaError(f"案例编号重复：{case_id}")
        case_ids.add(case_id)
        covered_dimensions.add(dimension)
        topics_by_dimension[dimension].add(topic_id)

    if covered_dimensions != DIMENSIONS:
        missing = ", ".join(sorted(DIMENSIONS - covered_dimensions))
        raise NarrativeEvalSchemaError(f"评测维度不完整：{missing}")
    for dimension, covered_topics in topics_by_dimension.items():
        if len(covered_topics) < 3:
            raise NarrativeEvalSchemaError(
                f"评测维度 {dimension} 必须覆盖至少三个合成题材"
            )


def score_case(case: dict[str, Any]) -> dict[str, Any]:
    """评分单个案例，只返回脱敏标识、机械结果和显式人工复核状态。"""
    dimension = case["dimension"]
    human_review = _human_review_result(case)
    observed = case.get("observed")

    control_result = _score_control_envelope(observed)
    if control_result is not None:
        verdict, failure_code = control_result
    elif dimension == "route":
        verdict, failure_code = _score_route(case["expected"], observed)
    elif dimension == "fact_memory":
        verdict, failure_code = _score_fact_memory(case["expected"], observed)
    elif dimension == "choice_alignment":
        verdict, failure_code = _score_choice_alignment(case["expected"], observed)
    elif dimension == "persona":
        # 人设一致性属于叙事判断，结构化评分器不得用关键词冒充人工结论。
        verdict, failure_code = None, None
    else:
        verdict, failure_code = _score_convergence(case["expected"], observed)

    result = {
        "case_id": case["id"],
        "dimension": dimension,
        "verdict": verdict,
        "failure_code": failure_code,
        "human_review": human_review,
    }
    manual_label = case.get("manual_label")
    if manual_label is not None:
        human_pending = human_review is not None and human_review["verdict"] is None
        result["manual_label_match"] = (
            verdict == manual_label["verdict"]
            and failure_code == manual_label["failure_code"]
            and human_pending == manual_label["human_review_pending"]
        )
    return result


def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """按数据集模式生成脱敏报告，候选模式永不读取校准标签。"""
    validate_dataset(dataset)
    evaluation_mode = _evaluation_mode(dataset)
    results = [score_case(case) for case in dataset["cases"]]
    passed = sum(result["verdict"] is True for result in results)
    failed = sum(result["verdict"] is False for result in results)
    pending = sum(result["verdict"] is None for result in results)
    failure_counts = Counter(
        result["failure_code"]
        for result in results
        if result["failure_code"] is not None
    )
    human_results = [
        result["human_review"]
        for result in results
        if result["human_review"] is not None
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset["dataset_id"],
        "data_policy": DATA_POLICY,
        "evaluation_mode": evaluation_mode,
        "case_count": len(results),
        "automated_summary": {
            "eligible": passed + failed,
            "passed": passed,
            "failed": failed,
            "pending": pending,
            "by_failure_code": {
                code: failure_counts.get(code, 0) for code in sorted(FAILURE_CODES)
            },
        },
        "human_review_summary": {
            "required": len(human_results),
            "passed": sum(result["verdict"] is True for result in human_results),
            "failed": sum(result["verdict"] is False for result in human_results),
            "pending": sum(result["verdict"] is None for result in human_results),
        },
        "cases": results,
    }
    if evaluation_mode == CALIBRATION_MODE:
        report["manual_label_summary"] = {
            "matched": sum(result["manual_label_match"] for result in results),
            "total": len(results),
        }
    return report


def _evaluation_mode(dataset: dict[str, Any]) -> str:
    """读取评测模式；原始固定集为兼容现有 JSON，缺省视为校准模式。"""
    mode = dataset.get("evaluation_mode", CALIBRATION_MODE)
    if mode not in EVALUATION_MODES:
        raise NarrativeEvalSchemaError(
            "evaluation_mode 只能是 calibration 或 candidate"
        )
    return mode


def _validate_topics(topics: list[Any]) -> set[str]:
    """校验题材元数据并返回稳定题材编号。"""
    topic_ids: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict):
            raise NarrativeEvalSchemaError("题材项必须是对象")
        topic_id = topic.get("id")
        label = topic.get("label")
        if not isinstance(topic_id, str) or not _CASE_ID_PATTERN.fullmatch(topic_id):
            raise NarrativeEvalSchemaError("题材编号必须是稳定的 ASCII snake_case")
        if topic_id in topic_ids:
            raise NarrativeEvalSchemaError(f"题材编号重复：{topic_id}")
        if not isinstance(label, str) or not label.strip():
            raise NarrativeEvalSchemaError(f"题材 {topic_id} 缺少合成题材说明")
        topic_ids.add(topic_id)
    return topic_ids


def _validate_case_configuration(
    case: Any,
    topic_ids: set[str],
    evaluation_mode: str,
) -> tuple[str, str, str]:
    """校验人工金标和评分规则，不把有意构造的坏输出判成数据集错误。"""
    if not isinstance(case, dict):
        raise NarrativeEvalSchemaError("案例必须是对象")
    case_id = case.get("id")
    if not isinstance(case_id, str) or not _CASE_ID_PATTERN.fullmatch(case_id):
        raise NarrativeEvalSchemaError("案例编号必须是稳定的 ASCII snake_case")
    if case.get("source") != "synthetic":
        raise NarrativeEvalSchemaError(f"案例 {case_id} 必须明确标记为 synthetic")
    topic_id = case.get("topic_id")
    if topic_id not in topic_ids:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 引用了未知题材")
    dimension = case.get("dimension")
    if dimension not in DIMENSIONS:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的评测维度无效")
    if not isinstance(case.get("context"), dict):
        raise NarrativeEvalSchemaError(f"案例 {case_id} 缺少合成上下文")
    if not isinstance(case.get("observed"), dict):
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的候选输出必须是对象")

    _validate_expected(case_id, dimension, case.get("expected"))
    _validate_human_review(case_id, dimension, case.get("human_review"))
    if evaluation_mode == CALIBRATION_MODE:
        if "human_review_result" in case:
            raise NarrativeEvalSchemaError(
                f"校准案例 {case_id} 不能携带外部人工复核结果"
            )
        _validate_manual_label(case_id, dimension, case.get("manual_label"))
    else:
        if "manual_label" in case:
            raise NarrativeEvalSchemaError(
                f"候选案例 {case_id} 不能携带校准 manual_label"
            )
        if "human_review_result" in case:
            _validate_human_review_result(
                case_id,
                case["human_review"]["criteria"],
                case["human_review_result"],
            )
    return case_id, dimension, topic_id


def _validate_expected(case_id: str, dimension: str, expected: Any) -> None:
    """校验各维度的人工期望结构。"""
    if not isinstance(expected, dict):
        raise NarrativeEvalSchemaError(f"案例 {case_id} 缺少 expected")
    if dimension == "route":
        route = expected.get("route")
        if not _is_exact_string_object(route, _ROUTE_KEYS):
            raise NarrativeEvalSchemaError(f"案例 {case_id} 的路线金标结构无效")
    elif dimension == "fact_memory":
        fact_memory = expected.get("fact_memory")
        if not isinstance(fact_memory, dict):
            raise NarrativeEvalSchemaError(f"案例 {case_id} 的事实金标结构无效")
        for field in ("required_anchors", "forbidden_contradictions"):
            if not _is_unique_record_list(fact_memory.get(field), _FACT_KEYS, "key"):
                raise NarrativeEvalSchemaError(f"案例 {case_id} 的 {field} 无效")
    elif dimension == "choice_alignment":
        choice_alignment = expected.get("choice_alignment")
        choices = (
            choice_alignment.get("choices")
            if isinstance(choice_alignment, dict)
            else None
        )
        if not _is_unique_record_list(choices, _CHOICE_KEYS, "id", exact_keys=True):
            raise NarrativeEvalSchemaError(f"案例 {case_id} 的按钮金标结构无效")
        if any(choice["input_kind"] != "choice" for choice in choices):
            raise NarrativeEvalSchemaError(
                f"案例 {case_id} 的按钮金标类型必须是 choice"
            )
    elif dimension == "persona":
        if expected != {"evaluation": "human_only"}:
            raise NarrativeEvalSchemaError(
                f"案例 {case_id} 的人设评测必须明确为人工判断"
            )
    else:
        convergence = expected.get("convergence")
        if not isinstance(convergence, dict):
            raise NarrativeEvalSchemaError(f"案例 {case_id} 的收束金标结构无效")
        if set(convergence) != {"status", "target_node_id", "required_fact_keys"}:
            raise NarrativeEvalSchemaError(f"案例 {case_id} 的收束金标字段无效")
        if not _all_nonempty_strings(
            [convergence["status"], convergence["target_node_id"]]
        ):
            raise NarrativeEvalSchemaError(f"案例 {case_id} 的收束金标值无效")
        if not _is_unique_string_list(convergence["required_fact_keys"]):
            raise NarrativeEvalSchemaError(f"案例 {case_id} 的收束事实键无效")


def _validate_human_review(case_id: str, dimension: str, human_review: Any) -> None:
    """强制人工维度显式配置，禁止缺配置时静默通过。"""
    if not isinstance(human_review, dict):
        raise NarrativeEvalSchemaError(f"案例 {case_id} 缺少 human_review")
    criteria = human_review.get("criteria")
    if criteria != list(_HUMAN_CRITERIA[dimension]):
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的人工评测项不完整")


def _validate_manual_label(case_id: str, dimension: str, label: Any) -> None:
    """校验每个合成案例都具备可复核的人工金标。"""
    if not isinstance(label, dict) or set(label) != {
        "verdict",
        "failure_code",
        "human_review_pending",
    }:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的人工金标结构无效")
    verdict = label["verdict"]
    if verdict is not None and not isinstance(verdict, bool):
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的 verdict 只能是布尔值或 null")
    failure_code = label["failure_code"]
    if failure_code is not None and failure_code not in FAILURE_CODES:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的失败码无效")
    if not isinstance(label["human_review_pending"], bool):
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的人工待审标记无效")
    if verdict is True and failure_code is not None:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的通过金标不能带失败码")
    if verdict is False and failure_code not in FAILURE_CODES:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的失败金标缺少客观原因码")
    if verdict is None and failure_code is not None:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的机械待审金标不能携带失败码")
    expected_human_pending = bool(_HUMAN_CRITERIA[dimension])
    if label["human_review_pending"] is not expected_human_pending:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的人工待审标记与维度不一致")
    if dimension == "persona" and verdict is not None:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的人设金标不能自动判定")
    if dimension != "persona" and verdict is None:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的结构化维度必须给出布尔金标")


def _human_review_result(case: dict[str, Any]) -> dict[str, Any] | None:
    """读取显式人工结论；没有人工输入时固定保持待审，绝不自动判定。"""
    criteria = case["human_review"]["criteria"]
    if not criteria:
        return None
    explicit_result = case.get("human_review_result")
    if explicit_result is not None:
        return copy.deepcopy(explicit_result)
    return {
        "criteria": list(criteria),
        "verdict": None,
        "failure_code": "human_review_pending",
    }


def _validate_human_review_result(
    case_id: str,
    expected_criteria: list[str],
    result: Any,
) -> None:
    """校验人工复核结论，确保机械评分器不能伪造主观判断。"""
    if not expected_criteria:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 没有可提交的人工评测项")
    if not isinstance(result, dict) or set(result) != {
        "criteria",
        "verdict",
        "failure_code",
    }:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的人工复核结果结构无效")
    if result["criteria"] != expected_criteria:
        raise NarrativeEvalSchemaError(f"案例 {case_id} 的人工复核项目不匹配")

    verdict = result["verdict"]
    failure_code = result["failure_code"]
    if verdict is not None and not isinstance(verdict, bool):
        raise NarrativeEvalSchemaError(
            f"案例 {case_id} 的人工复核 verdict 只能是布尔值或 null"
        )
    expected_failure_code = (
        None
        if verdict is True
        else "human_review_failed"
        if verdict is False
        else "human_review_pending"
    )
    if failure_code != expected_failure_code:
        allowed = ", ".join(sorted(HUMAN_REVIEW_FAILURE_CODES))
        raise NarrativeEvalSchemaError(
            f"案例 {case_id} 的人工复核失败码无效，可用值：{allowed}"
        )


def _score_control_envelope(observed: Any) -> tuple[bool, str] | None:
    """先区分技术降级、上下文缺失和候选信封违规。"""
    if not isinstance(observed, dict):
        return False, "contract_violation"
    context_status = observed.get("context_status")
    delivery = observed.get("delivery")
    if context_status not in {"complete", "missing"} or delivery not in {
        "normal",
        "technical_degraded",
    }:
        return False, "contract_violation"
    if delivery == "technical_degraded":
        return False, "technical_degraded"
    if context_status == "missing":
        return False, "context_missing"
    return None


def _score_route(
    expected: dict[str, Any], observed: dict[str, Any]
) -> tuple[bool, str | None]:
    """只按路线对象的精确字段和值评分。"""
    actual = observed.get("route")
    if not _is_exact_string_object(actual, _ROUTE_KEYS):
        return False, "contract_violation"
    if actual != expected["route"]:
        return False, "semantic_mismatch"
    return True, None


def _score_fact_memory(
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> tuple[bool, str | None]:
    """按结构化事实锚点和明确矛盾对评分，不扫描对白关键词。"""
    actual = observed.get("fact_memory")
    recalled = actual.get("recalled_facts") if isinstance(actual, dict) else None
    if not _is_unique_record_list(recalled, _FACT_KEYS, "key", exact_keys=True):
        return False, "contract_violation"

    actual_pairs = {(item["key"], item["value"]) for item in recalled}
    rules = expected["fact_memory"]
    required_pairs = {
        (item["key"], item["value"]) for item in rules["required_anchors"]
    }
    forbidden_pairs = {
        (item["key"], item["value"]) for item in rules["forbidden_contradictions"]
    }
    if not required_pairs.issubset(actual_pairs) or actual_pairs.intersection(
        forbidden_pairs
    ):
        return False, "semantic_mismatch"
    return True, None


def _score_choice_alignment(
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> tuple[bool, str | None]:
    """只校验按钮稳定编号、输入类型和作者核心；显示文案留给人工。"""
    actual = observed.get("choice_alignment")
    choices = actual.get("choices") if isinstance(actual, dict) else None
    if not _is_unique_record_list(choices, _CHOICE_KEYS, "id"):
        return False, "contract_violation"

    expected_by_id = {
        item["id"]: item for item in expected["choice_alignment"]["choices"]
    }
    actual_by_id = {item["id"]: item for item in choices}
    if set(actual_by_id) != set(expected_by_id):
        return False, "contract_violation"
    if any(
        actual_by_id[choice_id]["input_kind"] != expected_by_id[choice_id]["input_kind"]
        for choice_id in expected_by_id
    ):
        return False, "contract_violation"
    if any(
        actual_by_id[choice_id]["author_core"]
        != expected_by_id[choice_id]["author_core"]
        for choice_id in expected_by_id
    ):
        return False, "semantic_mismatch"
    return True, None


def _score_convergence(
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> tuple[bool, str | None]:
    """只判断收束状态、目标节点与必要事实是否形成结构闭环。"""
    actual = observed.get("convergence")
    if not isinstance(actual, dict) or set(actual) != _CONVERGENCE_KEYS:
        return False, "contract_violation"
    if not _all_nonempty_strings([actual["status"], actual["target_node_id"]]):
        return False, "contract_violation"
    if not _is_unique_string_list(actual["resolved_fact_keys"]):
        return False, "contract_violation"

    rules = expected["convergence"]
    if (
        actual["status"] != rules["status"]
        or actual["target_node_id"] != rules["target_node_id"]
    ):
        return False, "semantic_mismatch"
    if not set(rules["required_fact_keys"]).issubset(actual["resolved_fact_keys"]):
        return False, "semantic_mismatch"
    return True, None


def _is_exact_string_object(value: Any, keys: frozenset[str]) -> bool:
    """判断对象是否只有指定字段，且所有值都是非空字符串。"""
    return (
        isinstance(value, dict)
        and set(value) == keys
        and _all_nonempty_strings(value.values())
    )


def _is_unique_record_list(
    value: Any,
    required_keys: frozenset[str],
    identity_key: str,
    *,
    exact_keys: bool = False,
) -> bool:
    """判断记录列表字段完整、值非空且稳定编号不重复。"""
    if not isinstance(value, list) or not value:
        return False
    identities: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            return False
        item_keys = set(item)
        if (exact_keys and item_keys != required_keys) or not required_keys.issubset(
            item_keys
        ):
            return False
        if not _all_nonempty_strings(item[key] for key in required_keys):
            return False
        identity = item[identity_key]
        if identity in identities:
            return False
        identities.add(identity)
    return True


def _is_unique_string_list(value: Any) -> bool:
    """判断值是非空且无重复项的字符串列表。"""
    return (
        isinstance(value, list)
        and bool(value)
        and _all_nonempty_strings(value)
        and len(value) == len(set(value))
    )


def _all_nonempty_strings(values: Any) -> bool:
    """判断迭代值全部是非空字符串。"""
    return all(isinstance(value, str) and bool(value.strip()) for value in values)
