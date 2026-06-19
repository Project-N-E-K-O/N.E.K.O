from __future__ import annotations

import os
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy, PluginCliRootId


@dataclass(frozen=True, slots=True)
class PluginCliPluginRef:
    root_id: PluginCliRootId
    directory_name: str


@dataclass(frozen=True, slots=True)
class ResolvedPluginSource:
    root_id: PluginCliRootId
    directory_name: str
    plugin_id: str
    plugin_dir: Path

    @property
    def label(self) -> str:
        return self.plugin_id or self.directory_name


def _compare_key(path: Path) -> str:
    return os.path.normcase(unicodedata.normalize("NFC", str(path.resolve(strict=False))))


def _path_is_within(path: Path, root: Path) -> bool:
    path_key = _compare_key(path)
    root_key = _compare_key(root)
    sep = os.sep
    prefix = root_key if root_key.endswith(sep) else root_key + sep
    return path_key.startswith(prefix)


def _require_safe_directory_name(value: str) -> str:
    directory_name = value.strip()
    posix_path = PurePosixPath(directory_name)
    windows_path = PureWindowsPath(directory_name)
    if (
        not directory_name
        or directory_name in {".", ".."}
        or len(posix_path.parts) != 1
        or len(windows_path.parts) != 1
        or windows_path.drive
        or windows_path.root
    ):
        raise ValueError(f"directory_name must be a safe plugin directory name, got {value!r}")
    return directory_name


class PluginSourceResolver:
    """Resolve plugin build sources from explicit builtin/user roots."""

    def __init__(self, policy: PluginCliPathPolicy) -> None:
        self.policy = policy

    def list_plugins(self) -> list[ResolvedPluginSource]:
        return self._scan_sources()

    def resolve_plugin_ref(self, ref: PluginCliPluginRef | dict[str, Any]) -> ResolvedPluginSource:
        if isinstance(ref, PluginCliPluginRef):
            root_id = ref.root_id
            directory_name = ref.directory_name
        else:
            root_id = ref.get("root_id")
            directory_name = ref.get("directory_name")

        if root_id not in {"builtin", "user"}:
            raise ValueError(f"plugin_ref.root_id must be 'builtin' or 'user', got {root_id!r}")
        if not isinstance(directory_name, str):
            raise ValueError("plugin_ref.directory_name is required")
        safe_directory_name = _require_safe_directory_name(directory_name)
        root = self.policy.plugin_root(root_id)  # type: ignore[arg-type]
        candidate = root / safe_directory_name
        if candidate.is_symlink():
            raise ValueError(
                f"plugin_ref must point to a real plugin directory under {root_id}, "
                f"symlinks are not allowed: {root_id}/{safe_directory_name}"
            )
        resolved_candidate = candidate.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
        if not _path_is_within(resolved_candidate, resolved_root):
            raise ValueError(
                f"plugin_ref resolved outside {root_id} plugin root: "
                f"{root_id}/{safe_directory_name}"
            )
        return self._source_from_dir(
            root_id=root_id,  # type: ignore[arg-type]
            directory_name=safe_directory_name,
            plugin_dir=resolved_candidate,
        )

    def resolve_string(self, raw: str) -> ResolvedPluginSource:
        value = str(raw).strip()
        if not value:
            raise ValueError("plugin specifier must not be empty")

        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return self.resolve_absolute_path(candidate)

        matches: dict[tuple[str, str], ResolvedPluginSource] = {}
        for source in self._scan_sources():
            if source.directory_name == value or source.plugin_id == value:
                matches[(source.root_id, source.directory_name)] = source

        if not matches:
            raise FileNotFoundError(
                f"plugin '{value}' was not found under builtin or user plugin roots"
            )
        if len(matches) > 1:
            choices = ", ".join(
                f"{item.root_id}/{item.directory_name}"
                + (f" ([plugin].id={item.plugin_id})" if item.plugin_id else "")
                for item in sorted(matches.values(), key=self._source_sort_key)
            )
            raise ValueError(f"plugin '{value}' is ambiguous: {choices}")
        return next(iter(matches.values()))

    def resolve_absolute_path(self, raw: Path) -> ResolvedPluginSource:
        resolved = raw.expanduser().resolve(strict=False)
        for root_id, root in self.policy.build_source_roots:
            root_resolved = root.resolve(strict=False)
            if not _path_is_within(resolved, root_resolved):
                continue
            try:
                relative = resolved.relative_to(root_resolved)
            except ValueError:
                # The case-insensitive comparison matched; recover the first
                # path component by using the resolved path parts.
                root_part_count = len(root_resolved.parts)
                relative_parts = resolved.parts[root_part_count:]
            else:
                relative_parts = relative.parts

            if len(relative_parts) != 1:
                raise ValueError(
                    f"absolute plugin path must point to a top-level plugin directory under {root_id}: {raw}"
                )
            directory_name = _require_safe_directory_name(relative_parts[0])
            return self._source_from_dir(
                root_id=root_id,
                directory_name=directory_name,
                plugin_dir=(root_resolved / directory_name).resolve(strict=False),
            )

        roots = ", ".join(f"{root_id}={root}" for root_id, root in self.policy.build_source_roots)
        raise ValueError(f"absolute plugin path must be inside builtin or user plugin roots ({roots}): {raw}")

    def resolve_many(
        self,
        *,
        refs: list[PluginCliPluginRef | dict[str, Any]] | None = None,
        specifiers: list[str] | None = None,
    ) -> list[ResolvedPluginSource]:
        out: list[ResolvedPluginSource] = []
        for ref in refs or []:
            out.append(self.resolve_plugin_ref(ref))
        for specifier in specifiers or []:
            out.append(self.resolve_string(specifier))
        return out

    def _scan_sources(self) -> list[ResolvedPluginSource]:
        sources: list[ResolvedPluginSource] = []
        for root_id, root in self.policy.build_source_roots:
            if not root.exists() or not root.is_dir():
                continue
            try:
                children = sorted(root.iterdir(), key=lambda item: item.name)
            except OSError:
                continue
            for child in children:
                name = child.name
                if name.startswith(".") or name.startswith("_") or child.is_symlink():
                    continue
                try:
                    if not child.is_dir():
                        continue
                except OSError:
                    continue
                plugin_toml = child / "plugin.toml"
                if not plugin_toml.is_file():
                    continue
                sources.append(
                    ResolvedPluginSource(
                        root_id=root_id,
                        directory_name=name,
                        plugin_id=self._load_plugin_id(plugin_toml),
                        plugin_dir=child.resolve(strict=False),
                    )
                )
        return sorted(sources, key=self._source_sort_key)

    def _source_from_dir(
        self,
        *,
        root_id: PluginCliRootId,
        directory_name: str,
        plugin_dir: Path,
    ) -> ResolvedPluginSource:
        plugin_toml = plugin_dir / "plugin.toml"
        if not plugin_toml.is_file():
            raise FileNotFoundError(
                f"plugin.toml not found for plugin '{root_id}/{directory_name}': {plugin_toml}"
            )
        return ResolvedPluginSource(
            root_id=root_id,
            directory_name=directory_name,
            plugin_id=self._load_plugin_id(plugin_toml),
            plugin_dir=plugin_dir.resolve(strict=False),
        )

    @staticmethod
    def _load_plugin_id(plugin_toml: Path) -> str:
        try:
            data = tomllib.loads(plugin_toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return ""
        plugin_table = data.get("plugin")
        if isinstance(plugin_table, dict):
            plugin_id = plugin_table.get("id")
            if isinstance(plugin_id, str):
                return plugin_id.strip()
        return ""

    @staticmethod
    def _source_sort_key(source: ResolvedPluginSource) -> tuple[int, str]:
        root_order = 0 if source.root_id == "builtin" else 1
        return (root_order, source.directory_name)
