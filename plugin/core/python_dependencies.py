from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
import re

try:
    import tomllib  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

try:
    from importlib import metadata as importlib_metadata
except ImportError:  # pragma: no cover
    import importlib_metadata  # type: ignore[no-redef]

try:
    from packaging.requirements import Requirement
    from packaging.version import Version
except ImportError:  # pragma: no cover
    Requirement = None  # type: ignore
    Version = None  # type: ignore


HOST_PROVIDED_REQUIREMENT_NAMES = frozenset({"n-e-k-o"})

_NAME_NORMALIZE_RE = re.compile(r"[-_.]+")
_REQ_NAME_SPLIT_RE = re.compile(r"[<>=!~;\[\s]")


def canonicalize_distribution_name(name: str) -> str:
    """Canonicalize a Python distribution name using PEP 503 rules."""

    return _NAME_NORMALIZE_RE.sub("-", str(name or "")).lower().strip()


def parse_requirement_name(requirement: str) -> Optional[str]:
    text = str(requirement or "").strip()
    if not text:
        return None
    if Requirement is not None:
        try:
            parsed = Requirement(text)
            return str(parsed.name).strip() or None
        except Exception:
            pass
    head = _REQ_NAME_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    return head or None


def requirement_applies_to_current_environment(requirement: str) -> bool:
    if Requirement is None:
        return True
    try:
        parsed = Requirement(str(requirement or "").strip())
    except Exception:
        return True
    marker = getattr(parsed, "marker", None)
    if marker is None:
        return True
    try:
        return bool(marker.evaluate())
    except Exception:
        return True


def is_host_provided_requirement(requirement: str) -> bool:
    name = parse_requirement_name(requirement)
    if not name:
        return False
    return canonicalize_distribution_name(name) in HOST_PROVIDED_REQUIREMENT_NAMES


def split_host_provided_requirements(requirements: Iterable[str]) -> tuple[list[str], list[str]]:
    external: list[str] = []
    host_provided: list[str] = []
    for requirement in requirements:
        if is_host_provided_requirement(requirement):
            host_provided.append(requirement)
        else:
            external.append(requirement)
    return external, host_provided


def load_pyproject_toml(plugin_dir: Path) -> dict[str, object] | None:
    pyproject_path = plugin_dir / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    with pyproject_path.open("rb") as file_obj:
        data = tomllib.load(file_obj)
    if not isinstance(data, dict):
        raise ValueError(f"pyproject.toml root must be a table: {pyproject_path}")
    return data


def collect_project_python_requirements(pyproject_toml: dict[str, object] | None) -> list[str]:
    """Collect third-party runtime requirements from ``[project].dependencies``."""

    if not isinstance(pyproject_toml, dict):
        return []
    project_table = pyproject_toml.get("project")
    if not isinstance(project_table, dict):
        return []
    raw_dependencies = project_table.get("dependencies")
    if not isinstance(raw_dependencies, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_dependencies:
        if not isinstance(item, str):
            continue
        requirement = item.strip()
        if not requirement:
            continue
        key = requirement.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(requirement)
    return result


def find_missing_python_requirements(
    requirements: Iterable[str],
    *,
    search_paths: Iterable[Path | str] | None = None,
) -> list[str]:
    """Return unsatisfied requirement specs.

    When *search_paths* is provided, only distributions installed under those
    paths are considered. Passing an empty iterable intentionally means "nothing
    is installed" and never falls back to the shared interpreter environment.
    """

    requirement_list = [str(item or "").strip() for item in requirements if str(item or "").strip()]
    if not requirement_list:
        return []

    try:
        if search_paths is None:
            distributions = importlib_metadata.distributions()
        else:
            distributions = importlib_metadata.distributions(
                path=[str(Path(item)) for item in search_paths]
            )
    except Exception:
        return find_missing_python_requirements_from_versions(requirement_list, {})

    installed: dict[str, Optional[str]] = {}
    for dist in distributions:
        version_text: Optional[str] = None
        try:
            dist_version = getattr(dist, "version", None)
            if isinstance(dist_version, str) and dist_version.strip():
                version_text = dist_version.strip()
            else:
                meta_version = dist.metadata.get("Version")
                if isinstance(meta_version, str) and meta_version.strip():
                    version_text = meta_version.strip()

            dist_name = dist.metadata.get("Name")
            if isinstance(dist_name, str) and dist_name.strip():
                installed[canonicalize_distribution_name(dist_name)] = version_text

            dist_attr_name = getattr(dist, "name", None)
            if isinstance(dist_attr_name, str) and dist_attr_name.strip():
                installed.setdefault(canonicalize_distribution_name(dist_attr_name), version_text)
        except Exception:
            continue

    return find_missing_python_requirements_from_versions(requirement_list, installed)


def find_missing_python_requirements_from_versions(
    requirements: Iterable[str],
    installed_versions: Mapping[str, str | None],
) -> list[str]:
    requirement_list = [str(item or "").strip() for item in requirements if str(item or "").strip()]
    if not requirement_list:
        return []

    installed = {
        canonicalize_distribution_name(name): (version.strip() if isinstance(version, str) and version.strip() else None)
        for name, version in installed_versions.items()
        if str(name or "").strip()
    }

    missing: list[str] = []
    seen_missing: set[str] = set()
    for req in requirement_list:
        if not requirement_applies_to_current_environment(req):
            continue

        parsed_requirement = None
        req_name = None
        if Requirement is not None:
            try:
                parsed_requirement = Requirement(req)
                req_name = str(parsed_requirement.name).strip() or None
            except Exception:
                parsed_requirement = None

        if req_name is None:
            req_name = parse_requirement_name(req)
        if not req_name:
            continue

        canon = canonicalize_distribution_name(req_name)
        installed_version = installed.get(canon)
        satisfied = canon in installed
        if satisfied and parsed_requirement is not None and Version is not None:
            specifier = getattr(parsed_requirement, "specifier", None)
            if specifier:
                satisfied = False
                if installed_version is not None:
                    try:
                        satisfied = Version(installed_version) in specifier
                    except Exception:
                        satisfied = False

        if satisfied:
            continue

        key = req.lower()
        if key in seen_missing:
            continue
        seen_missing.add(key)
        missing.append(req)
    return missing
