"""Inspect and edit a plugin's Python requirements without importing its code.

Only project-local distribution metadata is read. Requirement writes preserve
the surrounding TOML text and refuse layouts that cannot be edited losslessly.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from importlib import metadata
from pathlib import Path

import portalocker
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from plugin.core.python_dependencies import (
    canonicalize_distribution_name,
)

from .python_package_graph import build_dependency_graph, requirement_applies, requirement_status
from .toml_utils import tomllib
from .vendor_sync import _lock_identity, dependency_lock, pyproject_revision, read_pyproject_bytes


class PythonPackagesError(ValueError):
    """A user-actionable project requirement error with a stable API code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _read_project(plugin_dir: Path) -> tuple[bytes | None, dict, list[str]]:
    path = plugin_dir / "pyproject.toml"
    try:
        raw = read_pyproject_bytes(path)
        data = tomllib.loads(raw.decode("utf-8")) if raw is not None else {}
    except (OSError, ValueError) as exc:
        raise PythonPackagesError("PYTHON_PACKAGES_PROJECT_INVALID", str(exc)) from exc
    project = data.get("project", {})
    if not isinstance(project, dict):
        raise PythonPackagesError("PYTHON_PACKAGES_PROJECT_INVALID", "[project] must be a table")
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(not isinstance(item, str) for item in dynamic):
        raise PythonPackagesError("PYTHON_PACKAGES_PROJECT_INVALID", "project.dynamic must be a list of strings")
    requirements = project.get("dependencies", [])
    if not isinstance(requirements, list) or any(not isinstance(item, str) for item in requirements):
        raise PythonPackagesError("PYTHON_PACKAGES_REQUIREMENTS_INVALID", "project.dependencies must be a list of strings")
    return raw, data, requirements


def _installed(plugin_dir: Path) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    issues: list[dict] = []
    vendor = plugin_dir / "vendor"
    if not vendor.is_dir():
        return rows, issues
    try:
        for dist in metadata.distributions(path=[str(vendor)]):
            try:
                name = dist.metadata.get("Name")
                version = dist.metadata.get("Version")
                if not name or not version:
                    raise ValueError("distribution metadata must contain Name and Version")
                if Requirement(name).name != name:
                    raise ValueError("distribution metadata Name must be a package name")
                Version(version)
                rows.append({
                    "name": name,
                    "version": version,
                    "direct": False,
                    "requires_dist": list(dist.requires or []),
                    "requires_python": dist.metadata.get("Requires-Python"),
                    "summary": dist.metadata.get("Summary"),
                })
            except (ValueError, TypeError, KeyError) as exc:
                issues.append({"code": "invalid_metadata", "message": str(exc)})
    except OSError as exc:
        raise PythonPackagesError("PYTHON_PACKAGES_VENDOR_UNREADABLE", str(exc)) from exc
    rows.sort(key=lambda row: canonicalize_distribution_name(row["name"]))
    names: set[str] = set()
    for row in rows:
        name = canonicalize_distribution_name(row["name"])
        if name in names:
            issues.append({"code": "duplicate_distribution", "package": row["name"],
                           "message": f"Multiple installed distributions have the name {row['name']}"})
        names.add(name)
    return rows, issues


def inspect_python_packages(plugin_dir: Path) -> dict:
    """Read declarations and vendor inventory; perform no network or imports."""
    plugin_dir = Path(plugin_dir)
    raw, _data, requirements = _read_project(plugin_dir)
    installed, issues = _installed(plugin_dir)
    by_name = {canonicalize_distribution_name(row["name"]): row for row in installed}
    declarations: list[dict] = []
    for text in requirements:
        try:
            parsed = Requirement(text)
            status, version = requirement_status(parsed, by_name)
        except ValueError as exc:
            issues.append({"code": "invalid_requirement", "requirement": text, "message": str(exc)})
            declarations.append({"requirement": text, "name": text, "specifier": "", "extras": [],
                                 "marker": None, "editable": False, "status": "invalid_metadata",
                                 "installed_version": None})
            continue
        declarations.append({"requirement": text, "name": parsed.name, "specifier": str(parsed.specifier),
                             "extras": sorted(parsed.extras), "marker": str(parsed.marker) if parsed.marker else None,
                             "editable": parsed.url is None, "status": status, "installed_version": version})
        installed_row = by_name.get(canonicalize_distribution_name(parsed.name))
        if installed_row is not None:
            installed_row["direct"] = True
        if status in {"missing", "version_mismatch"}:
            issues.append({"code": status, "package": parsed.name, "requirement": text,
                           "message": f"{text}: {status.replace('_', ' ')}"})
    direct_names = {canonicalize_distribution_name(row["name"]) for row in declarations}
    for row in installed:
        row["direct"] = canonicalize_distribution_name(row["name"]) in direct_names
    graph, graph_issues = build_dependency_graph(installed, declarations)
    issues.extend(graph_issues)
    return {
        "revision": pyproject_revision(raw), "requirements": declarations, "installed": installed,
        "issues": issues, "vendor_path": str(plugin_dir / "vendor"),
        "pyproject_path": str(plugin_dir / "pyproject.toml"), "dependency_graph": graph,
    }


def check_python_packages(plugin_dir: Path) -> dict:
    """Check active requirements of all installed packages."""
    plugin_dir = Path(plugin_dir)
    snapshot = inspect_python_packages(plugin_dir)
    installed = {canonicalize_distribution_name(row["name"]): row for row in snapshot["installed"]}
    for node in snapshot["dependency_graph"]["nodes"]:
        row = installed.get(node["id"])
        if row is None:
            continue
        requires_python = row["requires_python"]
        if requires_python:
            try:
                if not SpecifierSet(requires_python).contains(default_environment()["python_full_version"], prereleases=True):
                    snapshot["issues"].append({"code": "python_version_mismatch", "package": row["name"],
                                               "message": f"{row['name']} requires Python {requires_python}"})
            except InvalidSpecifier as exc:
                snapshot["issues"].append({"code": "invalid_metadata", "package": row["name"], "message": str(exc)})
        for edge in node["dependencies"]:
            for text in edge["requirements"]:
                child = Requirement(text)
                child.marker = None
                status, _version = requirement_status(child, installed)
                if status in {"missing", "version_mismatch"}:
                    snapshot["issues"].append({"code": f"dependency_{status}", "package": row["name"],
                                               "requirement": text, "message": f"{row['name']} requires {text}: {status.replace('_', ' ')}"})
    return snapshot


_PROJECT_HEADER = re.compile(r"(?m)^[ \t]*\[[ \t]*(?:project|\"project\"|'project')[ \t]*\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)")
_DEPENDENCIES_KEY = re.compile(r"(?m)^[ \t]*(?:dependencies|\"dependencies\"|'dependencies')[ \t]*=[ \t]*")


def _array_end(text: str, start: int) -> int:
    """Locate an already-validated TOML array without interpreting its strings."""
    if start >= len(text) or text[start] != "[":
        raise PythonPackagesError("PYTHON_PACKAGES_FORMAT_UNSUPPORTED", "dependencies must use an array assignment")
    pos = start + 1
    while pos < len(text):
        char = text[pos]
        if char == "#":
            end = text.find("\n", pos)
            pos = len(text) if end == -1 else end + 1
        elif char in "\"'":
            quote = char * (3 if text.startswith(char * 3, pos) else 1)
            pos += len(quote)
            while pos < len(text):
                if char == '"' and text[pos] == "\\":
                    pos += 2
                elif text.startswith(quote, pos):
                    pos += len(quote)
                    break
                else:
                    pos += 1
        elif char == "]":
            return pos + 1
        else:
            pos += 1
    raise PythonPackagesError("PYTHON_PACKAGES_FORMAT_UNSUPPORTED", "Cannot locate the dependencies array")


def _edit_requirements(text: str, data: dict, requirements: list[str]) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    value = "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in requirements) + "]"
    header = _PROJECT_HEADER.search(text)
    if header:
        # Header-looking text inside multiline strings is harmless: the
        # semantic comparison below rejects edits outside the actual field.
        next_table = re.search(r"(?m)^[ \t]*\[", text[header.end():])
        # The first array line may itself start with '['; locate the key first.
        match = _DEPENDENCIES_KEY.search(text, header.end())
        if match and (next_table is None or match.start() < header.end() + next_table.start()):
            start = match.end()
            end = _array_end(text, start)
            # Keep comments from within a multiline array as standalone comments.
            # Requirement strings cannot contain a newline, but may contain '#'.
            comments = _array_comments(text[start:end])
            replacement = value
            if comments:
                replacement += newline + newline.join(comments)
            return text[:start] + replacement + text[end:]
        if "dependencies" not in data.get("project", {}):
            separator = "" if text[:header.end()].endswith("\n") else newline
            return text[:header.end()] + separator + f"dependencies = {value}{newline}" + text[header.end():]
    elif "project" not in data:
        separator = "" if not text or text.endswith("\n") else newline
        return text + separator + f"{newline}[project]{newline}dependencies = {value}{newline}"
    raise PythonPackagesError("PYTHON_PACKAGES_FORMAT_UNSUPPORTED", "Edit project.dependencies in your editor first; this TOML layout cannot be changed losslessly")


def _array_comments(text: str) -> list[str]:
    """Keep comments while replacing requirement strings, including quoted '#'."""
    comments: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos] == "#":
            end = text.find("\n", pos)
            end = len(text) if end < 0 else end
            comments.append(text[pos:end].rstrip("\r"))
            pos = end
        elif text[pos] in "\"'":
            char = text[pos]
            quote = char * (3 if text.startswith(char * 3, pos) else 1)
            pos += len(quote)
            while pos < len(text):
                if char == '"' and text[pos] == "\\":
                    pos += 2
                elif text.startswith(quote, pos):
                    pos += len(quote)
                    break
                else:
                    pos += 1
        else:
            pos += 1
    return comments


def save_python_requirements(plugin_dir: Path, requirements: list[str], revision: str) -> dict:
    """Serialize workbench writes and reject revisions stale at the final read.

    External editors do not take this lock; their writes after that final read
    cannot be made atomic with our replacement without their cooperation.
    """
    plugin_dir = Path(plugin_dir)
    path = plugin_dir / "pyproject.toml"
    try:
        project_identity = _lock_identity(plugin_dir)
        with dependency_lock(plugin_dir):
            if _lock_identity(plugin_dir) != project_identity:
                raise PythonPackagesError("PYTHON_PACKAGES_REVISION_CONFLICT", "Project directory changed; reload before saving")
            if path.is_symlink():
                raise PythonPackagesError("PYTHON_PACKAGES_FORMAT_UNSUPPORTED", "pyproject.toml is a symbolic link; edit its target in your editor")
            raw, data, old_requirements = _read_project(plugin_dir)
            if pyproject_revision(raw) != revision:
                raise PythonPackagesError("PYTHON_PACKAGES_REVISION_CONFLICT", "pyproject.toml changed; reload before saving")
            if "dependencies" in data.get("project", {}).get("dynamic", []):
                raise PythonPackagesError("PYTHON_PACKAGES_FORMAT_UNSUPPORTED", "Dependencies are supplied dynamically; edit their source in your editor")
            if not isinstance(requirements, list) or any(not isinstance(item, str) for item in requirements):
                raise PythonPackagesError("PYTHON_PACKAGES_REQUIREMENTS_INVALID", "requirements must be a list of strings")
            seen: set[str] = set()
            for item in requirements:
                try:
                    parsed = Requirement(item)
                    requirement_applies(parsed, parsed.extras)
                except ValueError as exc:
                    raise PythonPackagesError("PYTHON_PACKAGES_REQUIREMENTS_INVALID", str(exc)) from exc
                if parsed.url and item not in old_requirements:
                    raise PythonPackagesError("PYTHON_PACKAGES_REQUIREMENT_SOURCE_UNSUPPORTED", "Add URL, Git or local dependencies in your editor")
                parsed.name = canonicalize_distribution_name(parsed.name)
                parsed.extras = {canonicalize_distribution_name(extra) for extra in parsed.extras}
                identity = str(parsed)
                if identity in seen:
                    raise PythonPackagesError("PYTHON_PACKAGES_REQUIREMENTS_INVALID", f"Duplicate requirement: {item}")
                seen.add(identity)
            updated = _edit_requirements(raw.decode("utf-8") if raw is not None else "", data, requirements)
            try:
                parsed_updated = tomllib.loads(updated)
            except ValueError as exc:
                raise PythonPackagesError("PYTHON_PACKAGES_FORMAT_UNSUPPORTED", str(exc)) from exc
            expected = copy.deepcopy(data)
            expected.setdefault("project", {})["dependencies"] = requirements
            if parsed_updated != expected:
                raise PythonPackagesError("PYTHON_PACKAGES_FORMAT_UNSUPPORTED", "Cannot preserve the other TOML fields in this layout")
            descriptor, name = tempfile.mkstemp(prefix=".python-packages-", suffix=".toml", dir=plugin_dir)
            staged = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as file_obj:
                    file_obj.write(updated.encode("utf-8"))
                    file_obj.flush()
                    os.fsync(file_obj.fileno())
                if path.exists():
                    os.chmod(staged, path.stat().st_mode & 0o777)
                current = read_pyproject_bytes(path)
                if pyproject_revision(current) != revision:
                    raise PythonPackagesError("PYTHON_PACKAGES_REVISION_CONFLICT", "pyproject.toml changed; reload before saving")
                if path.is_symlink():
                    raise PythonPackagesError("PYTHON_PACKAGES_FORMAT_UNSUPPORTED", "pyproject.toml is a symbolic link; edit its target in your editor")
                os.replace(staged, path)
            finally:
                staged.unlink(missing_ok=True)
    except portalocker.exceptions.LockException as exc:
        raise PythonPackagesError("PYTHON_PACKAGES_BUSY", "A dependency operation is already running for this project") from exc
    except OSError as exc:
        raise PythonPackagesError("PYTHON_PACKAGES_WRITE_FAILED", str(exc)) from exc
    return inspect_python_packages(plugin_dir)
