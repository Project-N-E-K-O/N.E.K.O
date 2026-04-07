from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version, InvalidVersion

from .models import BundleAnalysisResult, BundleSdkAnalysis, PluginSource, SharedDependency
from .plugin_source import load_plugin_source

_NAME_NORMALIZE_RE = re.compile(r"[-_.]+")


def analyze_bundle_plugins(
    plugin_dirs: list[str | Path],
    *,
    current_sdk_version: str | None = None,
) -> BundleAnalysisResult:
    """Analyze bundle candidates before any multi-plugin packaging begins."""

    sources = [load_plugin_source(plugin_dir) for plugin_dir in plugin_dirs]
    sources.sort(key=lambda item: item.plugin_id)

    shared_dependencies, common_dependencies = _analyze_shared_dependencies(sources)
    sdk_supported_analysis = _analyze_sdk_overlap(
        sources,
        kind="supported",
        current_sdk_version=current_sdk_version,
    )
    sdk_recommended_analysis = _analyze_sdk_overlap(
        sources,
        kind="recommended",
        current_sdk_version=current_sdk_version,
    )

    return BundleAnalysisResult(
        plugin_ids=[source.plugin_id for source in sources],
        shared_dependencies=shared_dependencies,
        common_dependencies=common_dependencies,
        sdk_supported_analysis=sdk_supported_analysis,
        sdk_recommended_analysis=sdk_recommended_analysis,
    )


def _analyze_shared_dependencies(
    sources: list[PluginSource],
) -> tuple[list[SharedDependency], list[SharedDependency]]:
    by_name: dict[str, dict[str, str]] = defaultdict(dict)

    for source in sources:
        for requirement_text in _read_project_dependencies(source.pyproject_toml):
            normalized_name = _normalize_requirement_name(requirement_text)
            if not normalized_name:
                continue
            by_name[normalized_name][source.plugin_id] = requirement_text

    shared: list[SharedDependency] = []
    common: list[SharedDependency] = []
    all_plugin_ids = {source.plugin_id for source in sources}

    for name in sorted(by_name):
        mapping = by_name[name]
        if len(mapping) < 2:
            continue
        item = SharedDependency(
            name=name,
            plugin_ids=sorted(mapping),
            requirement_texts=dict(sorted(mapping.items())),
        )
        shared.append(item)
        if set(mapping) == all_plugin_ids:
            common.append(item)

    return shared, common


def _analyze_sdk_overlap(
    sources: list[PluginSource],
    *,
    kind: str,
    current_sdk_version: str | None,
) -> BundleSdkAnalysis:
    plugin_specifiers: dict[str, str] = {}
    specifier_sets: list[SpecifierSet] = []

    for source in sources:
        raw = source.sdk_supported if kind == "supported" else source.sdk_recommended
        plugin_specifiers[source.plugin_id] = raw
        specifier_sets.append(SpecifierSet(raw or ""))

    matching_versions = _probe_matching_versions(
        list(plugin_specifiers.values()),
        current_sdk_version=current_sdk_version,
    )

    current_supported: bool | None = None
    if current_sdk_version:
        try:
            current_version = Version(current_sdk_version)
            current_supported = all(spec.contains(current_version, prereleases=True) for spec in specifier_sets)
        except InvalidVersion:
            current_supported = None

    return BundleSdkAnalysis(
        kind=kind,
        plugin_specifiers=plugin_specifiers,
        has_overlap=bool(matching_versions),
        matching_versions=matching_versions,
        current_sdk_version=current_sdk_version or "",
        current_sdk_supported_by_all=current_supported,
    )


def _probe_matching_versions(specifier_texts: list[str], *, current_sdk_version: str | None) -> list[str]:
    specifiers = [SpecifierSet(text or "") for text in specifier_texts]
    candidates = _candidate_versions(specifier_texts, current_sdk_version=current_sdk_version)

    matches: list[str] = []
    for candidate in candidates:
        try:
            version = Version(candidate)
        except InvalidVersion:
            continue
        if all(spec.contains(version, prereleases=True) for spec in specifiers):
            matches.append(str(version))
    return matches


def _candidate_versions(specifier_texts: list[str], *, current_sdk_version: str | None) -> list[str]:
    candidates: set[str] = set()
    if current_sdk_version:
        candidates.add(current_sdk_version)

    for text in specifier_texts:
        if not text:
            continue
        try:
            specifier_set = SpecifierSet(text)
        except Exception:
            continue

        for item in specifier_set:
            version_text = str(item.version)
            if not version_text:
                continue
            candidates.add(version_text)
            bumped = _bump_patch(version_text)
            if bumped:
                candidates.add(bumped)

    return sorted(candidates, key=_version_sort_key)


def _bump_patch(version_text: str) -> str | None:
    try:
        version = Version(version_text)
    except InvalidVersion:
        return None

    release = list(version.release)
    while len(release) < 3:
        release.append(0)
    release[2] += 1
    return ".".join(str(part) for part in release[:3])


def _version_sort_key(version_text: str) -> Version:
    try:
        return Version(version_text)
    except InvalidVersion:
        return Version("0")


def _read_project_dependencies(pyproject_toml: dict[str, object] | None) -> list[str]:
    if not isinstance(pyproject_toml, dict):
        return []

    project_table = pyproject_toml.get("project")
    if not isinstance(project_table, dict):
        return []

    raw_dependencies = project_table.get("dependencies")
    if not isinstance(raw_dependencies, list):
        return []

    result: list[str] = []
    for item in raw_dependencies:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def _normalize_requirement_name(requirement_text: str) -> str:
    try:
        requirement = Requirement(requirement_text)
    except Exception:
        return ""
    return _NAME_NORMALIZE_RE.sub("-", requirement.name).lower().strip()
