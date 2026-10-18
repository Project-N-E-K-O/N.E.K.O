"""Independent secure registry for Numeric v2 Story Packages."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

import portalocker

from .numeric_v2 import NumericV2CompileError, NumericV2Compiler
from .numeric_v2_storage_transaction import discard_temporary_file


_STORY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DEFAULT_PACKAGE_ROOT = Path(__file__).with_name("default_numeric_v2_packages")
_DEFAULT_PACKAGES_INITIALIZED_MARKER = ".defaults_initialized"
_DEFAULT_PACKAGES_MARKER_SCHEMA = "neko.theater.numeric.v2.default-packages"
# Real v2.2 packages are tens of KB (each prompt field is capped at 384 tokens);
# 8 MiB leaves ample headroom while bounding what an import may make us parse.
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
logger = logging.getLogger(__name__)


def _read_default_package_ids(marker: Path) -> set[str]:
    """Read handled builtin story IDs, supporting empty markers created by older versions."""  # noqa: DOCSTRING_CJK

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
    """Atomically record handled builtin stories so future versions install only newly added ones."""  # noqa: DOCSTRING_CJK

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
        discard_temporary_file(temporary_path)


class NumericV2PackageError(ValueError):
    """Numeric v2 package cannot be revalidated or written."""  # noqa: DOCSTRING_CJK


class NumericV2PackageUpgradeRequiredError(NumericV2PackageError):
    """Legacy story packages must upgrade to v2.2 before import or execution."""  # noqa: DOCSTRING_CJK


class NumericV2PackageExistsError(NumericV2PackageError):
    """The target story_id exists; overwriting is disallowed by default."""  # noqa: DOCSTRING_CJK


class NumericV2PackageNotFoundError(NumericV2PackageError):
    """The specified Numeric v2 package does not exist."""  # noqa: DOCSTRING_CJK


class NumericV2PackageRegistry:
    """Manage only ``numeric_v2/packages``, without reading v1 packages or Sessions."""  # noqa: DOCSTRING_CJK

    def __init__(self, root: Path, compiler: NumericV2Compiler | None = None):
        self.root = Path(root)
        self.compiler = compiler or NumericV2Compiler()

    @staticmethod
    def _declares_v2_2(payload: Mapping[str, Any]) -> bool:
        """Identify newly generated packages, preventing import fallback to older permissive clamping."""  # noqa: DOCSTRING_CJK

        meta = payload.get("meta") if isinstance(payload, Mapping) else None
        return isinstance(meta, Mapping) and meta.get("contract_version") == "v2.2"

    def compile_for_import(
        self,
        payload: Mapping[str, Any],
    ):
        """Import accepts only v2.2; all legacy packages receive actionable upgrade errors."""  # noqa: DOCSTRING_CJK

        if not self._declares_v2_2(payload):
            # Keep old package files on disk for authors to upgrade, but exclude them from runtime and install slots.
            raise NumericV2PackageUpgradeRequiredError(
                "numeric_v2_upgrade_required"
            )
        return self.compiler.compile_v2_2(payload)

    def ensure_default_packages(self) -> None:
        """Install repository builtin stories on first Numeric v2 use, never overwriting user stories."""  # noqa: DOCSTRING_CJK

        marker = self.root / _DEFAULT_PACKAGES_INITIALIZED_MARKER
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            bundled_sources = (
                sorted(_DEFAULT_PACKAGE_ROOT.glob("*.json"))
                if _DEFAULT_PACKAGE_ROOT.is_dir()
                else []
            )
            # Do not write a completion marker when no builtin package ships; later versions can still install newly added defaults.
            if not bundled_sources:
                return
            handled_story_ids = _read_default_package_ids(marker)
            for source in bundled_sources:
                payload = json.loads(source.read_text(encoding="utf-8"))
                # Builtin packages share the import version gate: skip old packages, never bypass upgrade requirements.
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
                        # Concurrent first starts may install the same default package; another process having written it counts as success.
                        pass
                handled_story_ids.add(compiled.story_id)
            _write_default_package_ids(marker, handled_story_ids)
        except (OSError, UnicodeError, json.JSONDecodeError, NumericV2CompileError) as exc:
            raise NumericV2PackageError("numeric_v2_default_package_invalid") from exc

    def delete_package(self, story_id: str) -> None:
        """Delete one installed story; the caller cascades Session cleanup in the same business action."""  # noqa: DOCSTRING_CJK

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

    def package_file_exists(self, story_id: str) -> bool:
        """True only when the package file name matches ``story_id`` literally.

        On case-insensitive filesystems ``foo.json`` also opens ``Foo.json``;
        the directory listing keeps the exact spelling, so a request for
        another casing cannot load, delete or cascade into a different story.
        """

        path = self.package_path(story_id)
        try:
            return path.name in os.listdir(self.root) and path.is_file()
        except FileNotFoundError:
            return False

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
        """List only packages that still pass the current v2 contract."""  # noqa: DOCSTRING_CJK

        if not self.root.is_dir():
            return []
        result = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                result.append(self.validate_package(payload))
            except NumericV2PackageUpgradeRequiredError:
                # Retain old files for author upgrades, but omit them from runnable story lists.
                continue
            except Exception as exc:
                # One unreadable or malformed file must not hide every other story.
                logger.warning(
                    "Skipping unusable numeric v2 package %s: %s",
                    path.name,
                    type(exc).__name__,
                )
                continue
        return result

    def load_engine(self, story_id: str):
        """Load the deterministic Engine from the v2 private directory, without legacy-package compatibility."""  # noqa: DOCSTRING_CJK

        from .numeric_v2_runtime import NumericV2Engine

        path = self.package_path(story_id)
        try:
            if not self.package_file_exists(story_id):
                raise NumericV2PackageNotFoundError("numeric_story_not_found")
            payload = json.loads(path.read_text(encoding="utf-8"))
            # Runtime loading and import share one version gate; old packages must first undergo the author's upgrade flow.
            compiled = self.compile_for_import(payload)
            if compiled.story_id != story_id:
                # A copied or renamed file would run under another story's sessions.
                raise NumericV2PackageError("numeric_story_id_mismatch")
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
            discard_temporary_file(temporary_path)
        return self.validate_package(compiled.story)


__all__ = [
    "MAX_PACKAGE_BYTES",
    "NumericV2PackageError",
    "NumericV2PackageExistsError",
    "NumericV2PackageNotFoundError",
    "NumericV2PackageUpgradeRequiredError",
    "NumericV2PackageRegistry",
]
