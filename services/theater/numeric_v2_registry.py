"""Numeric v2 Story Package 的独立安全注册表。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

import portalocker

from .numeric_v2 import NumericV2CompileError, NumericV2Compiler


_STORY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DEFAULT_PACKAGE_ROOT = Path(__file__).with_name("default_numeric_v2_packages")
_DEFAULT_PACKAGES_INITIALIZED_MARKER = ".defaults_initialized"
_DEFAULT_PACKAGES_MARKER_SCHEMA = "neko.theater.numeric.v2.default-packages"


def _read_default_package_ids(marker: Path) -> set[str]:
    """读取已处理的内置剧本 ID；兼容旧版本创建的空标记。"""  # noqa: DOCSTRING_CJK

    if not marker.is_file():
        return set()
    raw = marker.read_text(encoding="utf-8").strip()
    if not raw:
        return set()
    payload = json.loads(raw)
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != _DEFAULT_PACKAGES_MARKER_SCHEMA
        or not isinstance(payload.get("story_ids"), list)
    ):
        return set()
    return {
        story_id
        for story_id in payload["story_ids"]
        if isinstance(story_id, str) and _STORY_ID_RE.fullmatch(story_id)
    }


def _write_default_package_ids(marker: Path, story_ids: set[str]) -> None:
    """原子记录已处理的内置剧本，供后续版本只补装新增项。"""  # noqa: DOCSTRING_CJK

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=marker.parent,
            prefix=".defaults-",
            suffix=".tmp",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            json.dump(
                {
                    "schema": _DEFAULT_PACKAGES_MARKER_SCHEMA,
                    "story_ids": sorted(story_ids),
                },
                temporary,
                ensure_ascii=False,
                sort_keys=True,
            )
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, marker)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class NumericV2PackageError(ValueError):
    """Numeric v2 包无法复验或写入。"""  # noqa: DOCSTRING_CJK


class NumericV2PackageUpgradeRequiredError(NumericV2PackageError):
    """旧版剧本包必须先升级到 v2.2 才能导入或运行。"""  # noqa: DOCSTRING_CJK


class NumericV2PackageExistsError(NumericV2PackageError):
    """目标 story_id 已存在，默认不允许覆盖。"""  # noqa: DOCSTRING_CJK


class NumericV2PackageNotFoundError(NumericV2PackageError):
    """指定 Numeric v2 包不存在。"""  # noqa: DOCSTRING_CJK


class NumericV2PackageRegistry:
    """只管理 ``numeric_v2/packages``，不读取 v1 包或 Session。"""  # noqa: DOCSTRING_CJK

    def __init__(self, root: Path, compiler: NumericV2Compiler | None = None):
        self.root = Path(root)
        self.compiler = compiler or NumericV2Compiler()

    @staticmethod
    def _declares_v2_2(payload: Mapping[str, Any]) -> bool:
        """识别新生成包，确保导入时不会退回旧版宽松限幅。"""  # noqa: DOCSTRING_CJK

        meta = payload.get("meta")
        return isinstance(meta, Mapping) and meta.get("contract_version") == "v2.2"

    def compile_for_import(
        self,
        payload: Mapping[str, Any],
    ):
        """导入入口只接受 v2.2，旧包统一返回可操作的升级错误。"""  # noqa: DOCSTRING_CJK

        if not self._declares_v2_2(payload):
            # 旧包文件保留在磁盘上供作者升级，但不能再进入运行时或安装槽位。
            raise NumericV2PackageUpgradeRequiredError(
                "numeric_v2_upgrade_required"
            )
        return self.compiler.compile_v2_2(payload)

    def ensure_default_packages(self) -> None:
        """首次使用 Numeric v2 时安装仓库内置剧本，绝不覆盖用户剧本。"""  # noqa: DOCSTRING_CJK

        marker = self.root / _DEFAULT_PACKAGES_INITIALIZED_MARKER
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            bundled_sources = (
                sorted(_DEFAULT_PACKAGE_ROOT.glob("*.json"))
                if _DEFAULT_PACKAGE_ROOT.is_dir()
                else []
            )
            # 当前发行物没有内置包时不写完成标记，后续版本加入默认剧本后仍能自动补装。
            if not bundled_sources:
                return
            handled_story_ids = _read_default_package_ids(marker)
            for source in bundled_sources:
                payload = json.loads(source.read_text(encoding="utf-8"))
                # 内置包也必须沿用导入入口的版本门禁；旧内置包跳过安装，不能绕过升级要求。
                try:
                    compiled = self.compile_for_import(payload)
                except NumericV2PackageUpgradeRequiredError:
                    continue
                if compiled.story_id in handled_story_ids:
                    continue
                target = self.package_path(compiled.story_id)
                if not target.exists():
                    try:
                        self.import_package(compiled.story)
                    except NumericV2PackageExistsError:
                        # 多进程首次启动可能同时安装同一默认包；另一进程已写入即视为初始化成功。
                        pass
                handled_story_ids.add(compiled.story_id)
            _write_default_package_ids(marker, handled_story_ids)
        except (OSError, UnicodeError, json.JSONDecodeError, NumericV2CompileError) as exc:
            raise NumericV2PackageError("numeric_v2_default_package_invalid") from exc

    def delete_package(self, story_id: str) -> None:
        """删除一个已安装剧本；Session 由调用方在同一业务动作中级联清理。"""  # noqa: DOCSTRING_CJK

        target = self.package_path(story_id)
        try:
            target.unlink()
        except FileNotFoundError as exc:
            raise NumericV2PackageNotFoundError("numeric_story_not_found") from exc
        except OSError as exc:
            raise NumericV2PackageError("numeric_story_delete_failed") from exc

    def package_path(self, story_id: str) -> Path:
        if not isinstance(story_id, str) or not _STORY_ID_RE.fullmatch(story_id):
            raise NumericV2PackageError("invalid_numeric_v2_story_id")
        return self.root / f"{story_id}.json"

    def validate_package(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        compiled = self.compile_for_import(payload)
        meta = compiled.story["meta"]
        return {
            "story_id": meta["story_id"],
            "title": meta["title"],
            "author": meta["author"],
            "revision": meta["revision"],
            "language": meta["language"],
            "contract_version": meta.get("contract_version", "v2"),
            "schema": compiled.story["schema"],
            "package_hash": compiled.package_hash,
            "warnings": [warning.__dict__ for warning in compiled.warnings],
            "intro": dict(compiled.story["intro"]),
            "metric_count": len(compiled.story["metric_schema"]),
        }

    def list_packages(self) -> list[dict[str, Any]]:
        """只列出能够重新通过当前 v2 合同的包。"""  # noqa: DOCSTRING_CJK

        if not self.root.is_dir():
            return []
        result = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                result.append(self.validate_package(payload))
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                NumericV2CompileError,
                NumericV2PackageUpgradeRequiredError,
            ):
                # 旧包文件保留给作者升级，但不出现在可运行剧本列表中。
                continue
        return result

    def load_engine(self, story_id: str):
        """从 v2 私有目录加载确定性 Engine，不兼容旧包。"""  # noqa: DOCSTRING_CJK

        from .numeric_v2_runtime import NumericV2Engine

        path = self.package_path(story_id)
        if not path.is_file():
            raise NumericV2PackageNotFoundError("numeric_story_not_found")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            # 运行时加载与导入使用同一版本门禁，旧包只能先经过作者升级流程。
            compiled = self.compile_for_import(payload)
            return NumericV2Engine(compiled)
        except NumericV2CompileError as exc:
            raise NumericV2PackageError("numeric_v2_contract_invalid") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NumericV2PackageError("numeric_v2_package_read_failed") from exc

    def import_package(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            compiled = self.compile_for_import(payload)
        except NumericV2CompileError as exc:
            raise NumericV2PackageError("numeric_v2_contract_invalid") from exc
        target = self.package_path(compiled.story_id)
        temporary_path: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(target):
                raise NumericV2PackageExistsError("numeric_v2_story_exists")
            with tempfile.NamedTemporaryFile(
                dir=self.root,
                prefix=f".{target.stem}-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(compiled.json_bytes)
                temporary.flush()
                os.fsync(temporary.fileno())
            # Serialize the existence check and publication across registry
            # instances/processes. Keep the lock file: unlinking it could let
            # waiters lock different inodes. Readers only see complete JSON,
            # including on user-selected filesystems without hard links.
            with portalocker.Lock(str(self.root / ".imports.lock"), mode="a", timeout=10):
                if os.path.lexists(target):
                    raise NumericV2PackageExistsError("numeric_v2_story_exists")
                os.replace(temporary_path, target)
        except NumericV2PackageError:
            raise
        except (OSError, portalocker.exceptions.LockException) as exc:
            raise NumericV2PackageError("numeric_v2_import_failed") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return self.validate_package(compiled.story)


__all__ = [
    "NumericV2PackageError",
    "NumericV2PackageExistsError",
    "NumericV2PackageNotFoundError",
    "NumericV2PackageUpgradeRequiredError",
    "NumericV2PackageRegistry",
]
