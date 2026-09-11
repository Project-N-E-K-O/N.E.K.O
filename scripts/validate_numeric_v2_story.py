#!/usr/bin/env python3
"""供 InkAI 调用的 Numeric v2 复验与安全安装 CLI。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler  # noqa: E402
from services.theater.numeric_v2_registry import (  # noqa: E402
    NumericV2PackageError,
    NumericV2PackageExistsError,
    NumericV2PackageRegistry,
)


def _package_root() -> Path:
    """复用 N.E.K.O 自己的存储策略，不在 InkAI 复制平台路径规则。"""  # noqa: DOCSTRING_CJK

    from utils.config_manager import ConfigManager

    return Path(ConfigManager().app_docs_dir) / "theater" / "numeric_v2" / "packages"


def main() -> int:
    # 工坊尚在独立仓库时沿用这条窄桥读取名称；SDK 内置后直接调用同一个函数。
    if sys.argv[1:] == ["--authoring-names"]:
        from services.theater.numeric_v2_identity import numeric_v2_authoring_names
        from utils.config_manager import ConfigManager

        try:
            data = numeric_v2_authoring_names(ConfigManager())
        except (ValueError, OSError):
            print(json.dumps({"success": False, "error": {"code": "numeric_v2_authoring_names_unavailable"}}))
            return 5
        print(json.dumps({"success": True, "data": data}, ensure_ascii=False))
        return 0
    if len(sys.argv) not in {2, 3} or (len(sys.argv) == 3 and sys.argv[2] != "--install"):
        print(json.dumps({"success": False, "error": {"code": "invalid_arguments"}}))
        return 2
    source = Path(sys.argv[1])
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        compiler = NumericV2Compiler()
        # CLI 与服务端安装入口共用 v2.2 严格门禁，旧包只会得到明确的升级提示。
        compiled = compiler.compile_v2_2(payload)
        if len(sys.argv) == 3:
            data = NumericV2PackageRegistry(_package_root(), compiler).import_package(compiled.story)
        else:
            meta = compiled.story["meta"]
            data = {
                "story_id": meta["story_id"],
                "title": meta["title"],
                "schema": compiled.story["schema"],
                "package_hash": compiled.package_hash,
                "warnings": [asdict(item) for item in compiled.warnings],
            }
        print(json.dumps({"success": True, "data": data}, ensure_ascii=False))
        return 0
    except NumericV2CompileError as exc:
        issue_codes = {issue.code for issue in exc.issues}
        # 旧包需要作者升级而非修复单个字段；CLI 顶层错误码与服务端门禁保持一致。
        error_code = (
            "numeric_v2_upgrade_required"
            if "numeric_v2_upgrade_required" in issue_codes
            else "numeric_v2_compile_failed"
        )
        print(json.dumps({
            "success": False,
            "error": {
                "code": error_code,
                "details": {"issues": [asdict(item) for item in exc.issues]},
            },
        }, ensure_ascii=False))
        return 3
    except NumericV2PackageExistsError:
        print(json.dumps({"success": False, "error": {"code": "numeric_v2_story_exists"}}))
        return 4
    except NumericV2PackageError as exc:
        print(json.dumps({
            "success": False,
            "error": {"code": str(exc) or "numeric_v2_validation_failed"},
        }, ensure_ascii=False))
        return 5
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        # Keep the CLI contract stable and do not expose local paths or parser text.
        print(json.dumps({
            "success": False,
            "error": {
                "code": "numeric_v2_source_read_failed",
                "details": {"exception": type(exc).__name__},
            },
        }, ensure_ascii=False))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
