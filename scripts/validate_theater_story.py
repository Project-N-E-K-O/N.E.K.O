#!/usr/bin/env python3
"""离线校验一份小剧场 Story Package，并输出脱敏结构化报告。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # 直接执行 scripts 下的文件时，只加入本地仓库根目录；工具不会连接模型或网络服务。
    sys.path.insert(0, str(REPO_ROOT))

from services.theater import story_loader  # noqa: E402
from utils.file_utils import atomic_write_text  # noqa: E402


REPORT_SCHEMA_VERSION = "neko_theater_story_validation_v1"
EXIT_VALID = 0
EXIT_STORY_INVALID = 1
EXIT_TOOL_ERROR = 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """只接受作者显式指定的单文件输入和可选报告路径。"""  # noqa: DOCSTRING_CJK
    parser = argparse.ArgumentParser(
        prog="validate_theater_story.py",
        description=__doc__,
    )
    parser.add_argument(
        "--story",
        type=Path,
        required=True,
        help="待校验的单个 Story Package JSON 文件",
    )
    parser.add_argument(
        "--explain-slots",
        action="store_true",
        help="在脱敏报告中列出动态内容槽位的执行级别",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="可选报告路径；未显式提供时不会写入文件",
    )
    return parser.parse_args(argv)


def _report(
    code: str, *, valid: bool, warnings: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """构造不含路径、正文或异常原文的稳定报告骨架。"""  # noqa: DOCSTRING_CJK
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "valid": valid,
        "code": code,
        "warnings": warnings or [],
    }


def _edge_visibility_warnings(story: dict[str, Any]) -> list[dict[str, str]]:
    """提示合法旧边仍依赖 recommended 默认值，不泄漏节点身份或作者正文。"""  # noqa: DOCSTRING_CJK
    edges = story.get("edges")
    if not isinstance(edges, list):
        return []
    uses_legacy_default = any(
        isinstance(edge, dict)
        and not str(edge.get("visibility") or "").strip()
        for edge in edges
    )
    return (
        [{"code": "edge_visibility_legacy_default"}]
        if uses_legacy_default
        else []
    )


def _slot_diagnostics(
    story: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """说明槽位约束是否具备确定性目录证明，并为声明式约束发出警告。"""  # noqa: DOCSTRING_CJK
    contract = story.get("world_contract")
    slots = contract.get("dynamic_content_slots") if isinstance(contract, dict) else []
    explanations: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for slot in slots if isinstance(slots, list) else []:
        if not isinstance(slot, dict):
            continue
        catalog_items = slot.get("catalog_items")
        # 只有经过 Loader 合同校验的非空目录，才能提供不依赖模型自报的确定性证明。
        catalog_verified = isinstance(catalog_items, list) and bool(catalog_items)
        enforcement = "catalog_verified" if catalog_verified else "declarative_only"
        slot_id = str(slot.get("slot_id") or "")
        explanations.append(
            {
                "slot_id": slot_id,
                "allowed_fact_type": str(slot.get("allowed_fact_type") or ""),
                # Loader 已保证这两组是稳定字符串列表；报告沿用作者顺序便于定位合同。
                "required_traits": list(slot.get("allowed_traits") or []),
                "forbidden_traits": list(slot.get("forbidden_traits") or []),
                "enforcement": enforcement,
            }
        )
        if not catalog_verified:
            warnings.append(
                {
                    "code": "slot_traits_declarative_only",
                    "slot_id": slot_id,
                }
            )
    return explanations, warnings


def _paths_conflict(story_path: Path, output_path: Path) -> bool:
    """拒绝同一路径、符号链接或已存在硬链接覆盖作者输入。"""  # noqa: DOCSTRING_CJK
    try:
        if story_path.resolve() == output_path.resolve():
            return True
        return output_path.exists() and story_path.samefile(output_path)
    except (OSError, RuntimeError):
        # 路径可用性由后续单独分类；比较失败本身不能授权覆盖输入。
        return False


def _output_would_pollute_story_directory(
    story_path: Path,
    output_path: Path,
) -> bool:
    """拒绝把报告 JSON 混入作者目录，也不向正式 Story 目录写任何报告。"""  # noqa: DOCSTRING_CJK
    try:
        # 只解析父目录，不能跟随最终文件符号链接；原子替换会覆盖链接本身并在作者目录留下 JSON。
        output_parent = output_path.parent.resolve()
        output_entry = output_parent / output_path.name
        config_story_dir = story_loader.CONFIG_STORY_DIR.resolve()
        if output_entry == config_story_dir or config_story_dir in output_entry.parents:
            return True
        try:
            relative_output = output_entry.relative_to(story_path.parent.resolve())
        except ValueError:
            return False
        # Loader 的直接 `*.json` glob 也会命中同名目录；因此检查相对路径第一层而非只看最终后缀。
        return bool(
            relative_output.parts and relative_output.parts[0].lower().endswith(".json")
        )
    except (OSError, RuntimeError):
        # 路径错误仍由后续 I/O 分类；这里只处理能够确定会污染 Loader 扫描面的路径。
        return False


def _write_report(path: Path, report: dict[str, Any]) -> None:
    """只向作者显式给出的路径原子写入脱敏报告。"""  # noqa: DOCSTRING_CJK
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _print_report(report: dict[str, Any]) -> None:
    """标准输出与落盘报告使用同一份脱敏数据。"""  # noqa: DOCSTRING_CJK
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _emit_report(
    report: dict[str, Any], output_path: Path | None, exit_code: int
) -> int:
    """写入可选报告；写入失败时以稳定工具错误覆盖原结果。"""  # noqa: DOCSTRING_CJK
    if output_path is not None:
        try:
            _write_report(output_path, report)
        except (OSError, UnicodeError):
            failed = _report("output_write_failed", valid=False)
            _print_report(failed)
            return EXIT_TOOL_ERROR
    _print_report(report)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """运行严格单文件校验，不扫描目录、不修复输入、也不调用模型。"""  # noqa: DOCSTRING_CJK
    args = _parse_args(argv)
    story_path: Path = args.story
    output_path: Path | None = args.output

    if output_path is not None and _paths_conflict(story_path, output_path):
        # 冲突报告只能输出到控制台，绝不能为了报告而覆盖或创建待校验 Story。
        _print_report(_report("output_conflicts_input", valid=False))
        return EXIT_TOOL_ERROR
    if output_path is not None and _output_would_pollute_story_directory(
        story_path,
        output_path,
    ):
        _print_report(_report("output_conflicts_story_directory", valid=False))
        return EXIT_TOOL_ERROR
    if not story_path.exists():
        return _emit_report(
            _report("input_missing", valid=False),
            output_path,
            EXIT_TOOL_ERROR,
        )
    if not story_path.is_file():
        return _emit_report(
            _report("input_not_file", valid=False),
            output_path,
            EXIT_TOOL_ERROR,
        )

    try:
        story = asyncio.run(story_loader.validate_story_file(story_path))
    except json.JSONDecodeError as exc:
        report = _report("story_json_invalid", valid=False)
        # JSON 语法错误仅公开定位，不回显异常消息、原文或绝对文件路径。
        report["location"] = {"line": exc.lineno, "column": exc.colno}
        return _emit_report(report, output_path, EXIT_STORY_INVALID)
    except story_loader.StoryRootNotObjectError:
        return _emit_report(
            _report("story_root_not_object", valid=False),
            output_path,
            EXIT_STORY_INVALID,
        )
    except (OSError, UnicodeError):
        return _emit_report(
            _report("input_read_failed", valid=False),
            output_path,
            EXIT_TOOL_ERROR,
        )
    except ValueError:
        return _emit_report(
            _report("story_contract_invalid", valid=False),
            output_path,
            EXIT_STORY_INVALID,
        )
    except Exception:
        return _emit_report(
            _report("internal_error", valid=False),
            output_path,
            EXIT_TOOL_ERROR,
        )

    slots, slot_warnings = _slot_diagnostics(story)
    # 兼容警告按静态图、动态槽位固定排序，保证自动化作者工具获得稳定报告。
    warnings = [*_edge_visibility_warnings(story), *slot_warnings]
    report = _report("valid", valid=True, warnings=warnings)
    if args.explain_slots:
        report["slots"] = slots
    return _emit_report(report, output_path, EXIT_VALID)


if __name__ == "__main__":
    raise SystemExit(main())
