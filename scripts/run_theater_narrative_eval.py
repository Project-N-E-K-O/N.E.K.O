#!/usr/bin/env python3
"""生成小剧场叙事评测的脱敏结构化报告。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = (
    REPO_ROOT / "tests" / "fixtures" / "theater" / "narrative_eval_v1.json"
)
if str(REPO_ROOT) not in sys.path:
    # 直接执行 scripts 下的文件时，显式加入仓库根目录以复用测试侧评分器。
    sys.path.insert(0, str(REPO_ROOT))

from tests.utils.theater_narrative_eval import (  # noqa: E402
    CALIBRATION_MODE,
    NarrativeEvalSchemaError,
    apply_observations,
    evaluate_dataset,
    load_dataset,
)
from utils.file_utils import atomic_write_text  # noqa: E402


EXIT_SUCCESS = 0
EXIT_QUALITY_FAILED = 1
EXIT_TOOL_ERROR = 2


def _parse_args() -> argparse.Namespace:
    """读取显式输入和输出路径；报告不得默认写入仓库。"""  # noqa: DOCSTRING_CJK
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="人工金标评测集；默认使用 v1 合成固定集",
    )
    parser.add_argument(
        "--observations",
        type=Path,
        help="可选候选观测集；省略时只校准固定集与评分器",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="脱敏 JSON 报告的显式本地输出路径",
    )
    return parser.parse_args()


def _load_json(path: Path) -> Any:
    """读取候选观测 JSON；只允许调用方显式提供的本地文件。"""  # noqa: DOCSTRING_CJK
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _paths_conflict(input_path: Path, output_path: Path) -> bool:
    """拒绝输出通过直接路径、符号链接或硬链接覆盖任一输入。"""  # noqa: DOCSTRING_CJK
    try:
        if input_path.resolve(strict=False) == output_path.resolve(strict=False):
            return True
        if input_path.exists() and output_path.exists():
            return input_path.samefile(output_path)
    except (OSError, RuntimeError):
        # 保护路径无法可靠比较时必须拒绝写入，不能把比较失败当成覆盖授权。
        return True
    return False


def _write_report(path: Path, report: dict[str, Any]) -> None:
    """在目标目录内原子替换脱敏报告，不留下截断的旧文件。"""  # noqa: DOCSTRING_CJK
    atomic_write_text(
        path,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _emit_error(code: str) -> int:
    """只向标准错误输出稳定原因码，不回显路径、正文或异常消息。"""  # noqa: DOCSTRING_CJK
    print(f"theater_narrative_eval_error={code}", file=sys.stderr)
    return EXIT_TOOL_ERROR


def _console_result(report: dict[str, Any]) -> tuple[list[str], int]:
    """在报告提交前构造全部控制台行和最终退出码。"""  # noqa: DOCSTRING_CJK
    mode = report["evaluation_mode"]
    lines = [f"evaluation_mode={mode}"]
    for case in report["cases"]:
        verdict = "pending" if case["verdict"] is None else str(case["verdict"]).lower()
        reason = case["failure_code"] or "none"
        human_review = case["human_review"]
        if human_review is None:
            human_verdict = "not_required"
            human_reason = "none"
        else:
            human_verdict = (
                "pending"
                if human_review["verdict"] is None
                else str(human_review["verdict"]).lower()
            )
            human_reason = human_review["failure_code"] or "none"
        lines.append(
            f"case_id={case['case_id']} dimension={case['dimension']} "
            f"automated_verdict={verdict} automated_reason={reason} "
            f"human_verdict={human_verdict} human_reason={human_reason}"
        )
    summary = report["automated_summary"]
    human_summary = report["human_review_summary"]
    lines.append(
        f"automated_passed={summary['passed']} automated_failed={summary['failed']} "
        f"human_passed={human_summary['passed']} human_failed={human_summary['failed']} "
        f"human_pending={human_summary['pending']}"
    )
    if mode == CALIBRATION_MODE:
        # 校准模式验证评分器是否复现固定标签，内置坏例本身不能导致失败。
        labels = report["manual_label_summary"]
        exit_code = (
            EXIT_SUCCESS
            if labels["matched"] == labels["total"]
            else EXIT_QUALITY_FAILED
        )
    else:
        # 候选模式默认就是质量门禁；机械或已完成人工复核的失败都返回非零。
        exit_code = (
            EXIT_QUALITY_FAILED
            if summary["failed"] or human_summary["failed"]
            else EXIT_SUCCESS
        )
    return lines, exit_code


def main() -> int:
    """运行零模型评分并只在控制台输出稳定编号和原因码。"""  # noqa: DOCSTRING_CJK
    args = _parse_args()
    protected_inputs = [args.dataset]
    if args.observations is not None:
        protected_inputs.append(args.observations)
    if any(_paths_conflict(path, args.output) for path in protected_inputs):
        # 冲突检查必须发生在任何读取、建目录或报告写入之前。
        return _emit_error("output_conflicts_input")

    try:
        dataset = load_dataset(args.dataset)
    except json.JSONDecodeError:
        return _emit_error("dataset_json_invalid")
    except NarrativeEvalSchemaError:
        return _emit_error("dataset_schema_invalid")
    except (OSError, UnicodeError):
        return _emit_error("dataset_read_failed")
    except Exception:
        return _emit_error("internal_error")

    if args.observations is not None:
        try:
            observations = _load_json(args.observations)
        except json.JSONDecodeError:
            return _emit_error("observations_json_invalid")
        except (OSError, UnicodeError):
            return _emit_error("observations_read_failed")
        except Exception:
            return _emit_error("internal_error")
        try:
            dataset = apply_observations(dataset, observations)
        except NarrativeEvalSchemaError:
            return _emit_error("observations_schema_invalid")
        except Exception:
            return _emit_error("internal_error")

    try:
        report = evaluate_dataset(dataset)
    except Exception:
        return _emit_error("internal_error")
    try:
        console_lines, exit_code = _console_result(report)
    except Exception:
        # 摘要和退出码也属于待提交结果；结构异常时旧报告必须保持不变。
        return _emit_error("internal_error")
    try:
        _write_report(args.output, report)
    except (OSError, UnicodeError):
        return _emit_error("output_write_failed")
    except Exception:
        return _emit_error("internal_error")

    for line in console_lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
