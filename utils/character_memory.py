# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import stat
import sys
import threading
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.logger_config import get_module_logger
from utils.recent_file import (
    acquire_recent_file_locks,
    activate_recent_paths,
    fence_recent_deletions_and_clear_redirects,
    get_recent_pending_unlocked,
    read_recent_text_unlocked,
    recent_file_lock,
    recent_file_locks,
    redirect_recent_paths,
    release_recent_file_locks,
    restore_recent_deletions,
    restore_recent_redirects,
    restore_recent_registry_state,
    set_recent_pending_unlocked,
    snapshot_recent_redirects,
    snapshot_recent_deletions,
    write_recent_payload_unlocked,
)


logger = get_module_logger(__name__, "Memory")


# Per-character sidecar caches that shadow their own file: each keeps
# ``{name: data}`` in memory and only re-reads from disk on a cache MISS, so
# whenever a character directory is removed or replaced underneath them the
# stale entry stays authoritative for both reads and the next flush — which
# then writes it back over the new contents.
#
# Looked up through ``sys.modules`` rather than imported: eviction must not be
# what drags the memory package into a process that never touched it, and a
# module nobody loaded has no cache to evict.
# (module, evictor, retiring evictor, reviving evictor). Every sidecar writer
# needs all three:
# each one lazily creates ``memory/<name>/`` on write, so a snapshot staged
# while a delete was in flight would recreate the directory the caller had just
# removed. Retiring is for identities that are going away; the plain evictor is
# for live ones whose files changed underneath us, and it LIFTS retirement.
_CHARACTER_RUNTIME_CACHE_EVICTORS = (
    (
        "memory.anti_repeat_effects",
        "evict_cached_anti_repeat_effects",
        "retire_cached_anti_repeat_effects",
        "revive_cached_anti_repeat_effects",
    ),
    (
        "memory.anti_repeat",
        "evict_cached_anti_repeat_corpus",
        "retire_cached_anti_repeat_corpus",
        "revive_cached_anti_repeat_corpus",
    ),
    (
        "memory.startup_greeting_history",
        "evict_cached_startup_greeting_history",
        "retire_cached_startup_greeting_history",
        "revive_cached_startup_greeting_history",
    ),
)


def _run_character_cache_evictors(names: tuple[str, ...], *, mode: str) -> None:
    """Best-effort by construction: one module failing must not abort a delete,
    rename or cloud-save import that has already touched the filesystem.
    """
    for module_name, evictor_name, retiring_name, reviving_name in (
        _CHARACTER_RUNTIME_CACHE_EVICTORS
    ):
        module = sys.modules.get(module_name)
        wanted = evictor_name
        if mode == "retire" and retiring_name:
            wanted = retiring_name
        elif mode == "revive" and reviving_name:
            wanted = reviving_name
        evict = getattr(module, wanted, None)
        if not callable(evict):
            continue
        try:
            evict(*names)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "[CharacterMemory] cache eviction skipped for %s: %s",
                module_name,
                type(exc).__name__,
            )


_WRITE_FENCED: set[str] = set()
_WRITE_FENCE_LOCK = threading.Lock()


def set_character_write_fence(name: str, *, fenced: bool) -> None:
    """Refuse or re-permit sidecar writes for one name, whatever is on disk.

    Retirement is not enough for an operation that CREATES the directory
    partway through. It only refuses to MAKE one, so a rename's merge opens
    the door halfway: from that moment a write belonging to the identity
    that used to own the name is allowed in, and because staging copies the
    whole payload it replaces the history just moved there rather than
    adding to it. This fence never consults the filesystem, so it holds for
    the whole operation.

    Process-wide rather than per store, deliberately. All three sidecars
    have to refuse together or the gap only moves; and a store built DURING
    the fenced window would otherwise start with an empty set of its own --
    the hazard ``_PENDING_RETIREMENTS`` exists to work around for
    retirement, which this shape avoids entirely.

    It lives here rather than in ``memory`` because utils is the lower
    layer: memory may import utils, not the other way round, and the stores
    reach it from there.

    Releasing is a discard, so it cannot raise and cannot leave a character
    permanently unable to persist. Callers still have to release it in a
    ``finally``: a fence left up is worse than the write it prevents.

    A SET and not a counter, deliberately, even though that means one
    release disarms every holder. The failure modes are not symmetric: an
    unbalanced release with a counter leaves the fence up for the life of
    the process, while an extra release with a set only reopens a window.
    It relies on renames being serialised -- ``character_config_mutation_lock``
    wraps the whole operation, and the recent-file locks serialise them
    again -- so a second holder never arises today. A caller that renames
    outside that lock would make the difference live.

    SCOPE, measured rather than assumed: this covers the three sidecar
    stores and nothing else. Every file-producing call in them goes through
    ``_write_file_path`` or the corrupt-file backup, and both ask. Other
    writers under the same ``memory/<name>/`` -- ``user_directives`` and its
    siblings -- call ``ensure_character_dir`` unconditionally and are NOT
    fenced, so the harms described at the rename call site remain reachable
    through them. Closing that means fencing every writer, which is a
    larger change than this one.

    If it ever did leak, the blast radius is bounded: writes are dropped
    while it is up, but the cache keeps them and the first write after the
    release rewrites the whole payload. Persistence stops; data already in
    memory is not lost.
    """
    if not name:
        return
    with _WRITE_FENCE_LOCK:
        if fenced:
            _WRITE_FENCED.add(name)
        else:
            _WRITE_FENCED.discard(name)


def is_character_write_fenced(name: str) -> bool:
    """Whether sidecar writes for this name are currently refused."""
    if not name:
        return False
    with _WRITE_FENCE_LOCK:
        return name in _WRITE_FENCED


def fence_character_runtime_writes(*character_names: str) -> None:
    """Refuse sidecar writes for these names until they are unfenced.

    Retirement is the wrong tool for an operation that creates the
    directory partway through -- see ``set_character_write_fence`` above.
    Every caller must pair this with ``unfence_character_runtime_writes``
    in a ``finally``: a fence left up silences that character's sidecars
    for the life of the process.
    """
    for name in dict.fromkeys(n for n in character_names if n):
        set_character_write_fence(name, fenced=True)


def unfence_character_runtime_writes(*character_names: str) -> None:
    """Re-permit sidecar writes. A discard, so it cannot fail."""
    for name in dict.fromkeys(n for n in character_names if n):
        set_character_write_fence(name, fenced=False)


def evict_character_runtime_caches(*character_names: str) -> None:
    """Drop caches for LIVE identities whose files just changed.

    For a cloud-save import or a rename TARGET: the files on disk were
    replaced, the cache would shadow them, but the identity is still real and
    must keep persisting. Use ``retire_character_runtime_caches`` instead when
    the directory is going away.
    """
    names = tuple(dict.fromkeys(name for name in character_names if name))
    if not names:
        return
    _run_character_cache_evictors(names, mode="evict")


def revive_character_runtime_caches(*character_names: str) -> None:
    """Lift retirement for names an operation made live, and nothing else.

    For a cloud import: it rewrites only the managed memory files, none of
    which are these sidecars, so their caches still match what is on disk.
    Evicting anyway would raise each store's sequence fence and discard a
    snapshot staged but not yet flushed -- the just-delivered reply, statistic
    or greeting. Only the retirement needs lifting, so a name reused after an
    earlier delete can create its directory again.
    """
    names = tuple(dict.fromkeys(name for name in character_names if name))
    if not names:
        return
    _run_character_cache_evictors(names, mode="revive")


def retire_character_runtime_caches(*character_names: str) -> None:
    """Drop caches for identities whose directories are being REMOVED.

    For a delete or the source name of a rename. On top of the invalidation,
    this retires the name in EVERY sidecar store, so a write staged while the
    removal was still in flight cannot ``makedirs`` the directory back into
    existence after the tree is gone.
    """
    names = tuple(dict.fromkeys(name for name in character_names if name))
    if not names:
        return
    _run_character_cache_evictors(names, mode="retire")




LEGACY_CHARACTER_MEMORY_FILE_MAP = {
    "recent_{name}.json": "recent.json",
    ".recent_{name}.json.write-receipts.json": ".recent.json.write-receipts.json",
    "settings_{name}.json": "settings.json",
    "facts_{name}.json": "facts.json",
    "facts_archive_{name}.json": "facts_archive.json",
    "persona_{name}.json": "persona.json",
    "persona_corrections_{name}.json": "persona_corrections.json",
    "reflections_{name}.json": "reflections.json",
    "reflections_archive_{name}.json": "reflections_archive.json",
    "surfaced_{name}.json": "surfaced.json",
    "time_indexed_{name}": "time_indexed.db",
    "time_indexed_{name}.db": "time_indexed.db",
}

LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES = (
    "semantic_memory_{name}",
)

MESSAGE_NAME_FIELDS = ("speaker", "author", "name", "character")


# Language-sidecar and character identity operations share characters.json.
# Reuse one transaction lock for those cooperating mutation paths so their
# load -> mutate -> save snapshots cannot overtake each other.
class _CharacterConfigMutationLock(asyncio.Lock):
    # Even a rolled-back transaction may have temporarily changed the on-disk
    # file. Non-blocking readers must be able to recognize a read that crossed it.
    generation = 0
    _settled: asyncio.Event | None = None

    async def wait_for_release(self, expected_generation: int | None = None):
        # A reader that observed generation N only needs to wait for that
        # mutation. If N has already settled and another writer acquired the
        # lock, do not make the reader join the unrelated writer's lifetime.
        if expected_generation is not None and self.generation != expected_generation:
            return
        if not self.locked():
            return
        if self._settled is None:
            self._settled = asyncio.Event()
        settled = self._settled
        if expected_generation is not None and self.generation != expected_generation:
            return
        await settled.wait()

    def release(self):
        super().release()
        self.generation += 1
        if self._settled is not None:
            self._settled.set()
            self._settled = None


character_config_mutation_lock = _CharacterConfigMutationLock()


def is_legacy_vector_store_dir(root: Path, name: str) -> bool:
    """Whether ``root/name`` is another character's vector store, not a name.

    ``character_memory_exists("semantic_memory_Alice")`` is True, because that
    very directory is one of the paths it checks for a character of that name
    -- it confirms itself. Anything enumerating a memory root and offering
    what it finds therefore offered a bogus identity, and analysing it read
    ``memory/semantic_memory_Alice/time_indexed.db`` rather than Alice's,
    reporting no history for a character who has plenty.

    OWNERSHIP, not the prefix alone. "semantic_memory_x" is a legal character
    name, and excluding on the prefix would hide a real character who happens
    to be called that -- the same defect as a prefix-based exemption hiding a
    real character from deletion. The owner has to actually exist for this to
    be a vector store rather than a name.
    """
    prefix = "semantic_memory_"
    if not name.startswith(prefix):
        return False
    owner = name[len(prefix):]
    if not owner:
        return False
    try:
        if (root / owner).is_dir():
            return True
        return any(
            (root / pattern.format(name=owner)).exists()
            for pattern in LEGACY_CHARACTER_MEMORY_FILE_MAP
        )
    except OSError:
        return False


def iter_character_memory_roots(config_manager) -> list[Path]:
    """Return all runtime root directories holding character memory (deduped, insertion order kept).

    Only currently active runtime paths are returned:
      - ``memory_dir``: the current runtime's ``<app_docs>/memory``.
      - ``project_memory_dir``: the seed/default memory location under the project directory.

    Legacy paths (``Documents\\N.E.K.O\\memory`` and other CFA fallbacks or roots
    written by old versions) are **not** included. That data is handled separately by
    the two paths below, so deletion/cleanup logic never accidentally touches
    non-runtime locations:

      - Startup soft migration: ``ConfigManager.migrate_legacy_documents_memory`` only
        moves directories still present in ``characters.json[猫娘]`` to the runtime.
      - Manual cleanup button: the Workshop page's \"clean up legacy memory\" scan +
        user-checked deletion.
    """  # noqa: DOCSTRING_CJK
    roots: list[Path] = []
    seen: set[str] = set()

    for raw_path in (
        getattr(config_manager, "memory_dir", None),
        getattr(config_manager, "project_memory_dir", None),
    ):
        if not raw_path:
            continue
        try:
            root = Path(raw_path)
        except Exception:
            continue
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)

    return roots


def get_runtime_character_memory_dir(config_manager, character_name: str) -> Path:
    return Path(config_manager.memory_dir) / character_name


def list_character_memory_paths(config_manager, character_name: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()

    entry_names = [character_name]
    entry_names.extend(
        pattern.format(name=character_name)
        for pattern in LEGACY_CHARACTER_MEMORY_FILE_MAP
    )
    entry_names.extend(
        pattern.format(name=character_name)
        for pattern in LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES
    )

    for base_dir in iter_character_memory_roots(config_manager):
        for entry_name in entry_names:
            entry_path = base_dir / entry_name
            normalized_path = str(entry_path)
            if not entry_path.exists() or normalized_path in seen:
                continue
            seen.add(normalized_path)
            paths.append(entry_path)

    return paths


def character_memory_exists(config_manager, character_name: str) -> bool:
    return bool(list_character_memory_paths(config_manager, character_name))


def _paths_refer_to_same_entry(source_path: Path, target_path: Path) -> bool:
    """Return True when two paths resolve to the same existing filesystem entry."""
    try:
        return os.path.samefile(source_path, target_path)
    except OSError:
        return False


def _symlink_chain_reaches_path(
    source_path: Path,
    target_path: Path,
    *,
    max_hops: int = 40,
) -> bool:
    """Return True if following symlinks starting from ``source_path`` reaches ``target_path``."""
    source_path = Path(source_path)
    target_path = Path(target_path)
    try:
        if not source_path.is_symlink():
            return False
        src_lstat = os.lstat(source_path)
    except OSError:
        return False

    try:
        tgt_lstat = os.lstat(target_path)
    except OSError:
        tgt_lstat = None

    if tgt_lstat is not None and os.path.samestat(src_lstat, tgt_lstat):
        return False

    try:
        target_unresolved_norm: str | None = os.path.normcase(
            os.fspath(target_path.parent.resolve(strict=False) / target_path.name)
        )
    except OSError:
        target_unresolved_norm = None

    current = source_path
    visited: set[tuple[int, int]] = set()
    for _ in range(max_hops):
        try:
            cur_lstat = os.lstat(current)
            node_id = (cur_lstat.st_dev, cur_lstat.st_ino)
            if node_id in visited:
                break
            visited.add(node_id)
            raw_target = os.readlink(current)
        except OSError:
            break

        raw_path = Path(raw_target)
        next_path = raw_path if raw_path.is_absolute() else current.parent / raw_path

        if tgt_lstat is not None:
            try:
                if os.path.samestat(os.lstat(next_path), tgt_lstat):
                    return True
            except OSError:
                pass

        if target_unresolved_norm is not None:
            try:
                next_unresolved_norm = os.path.normcase(
                    os.fspath(next_path.parent.resolve(strict=False) / next_path.name)
                )
            except OSError:
                next_unresolved_norm = None
            if next_unresolved_norm == target_unresolved_norm:
                return True

        current = next_path
        try:
            if not current.is_symlink():
                break
        except OSError:
            break

    return False


def _paths_refer_to_same_directory_entry(source_path: Path, target_path: Path) -> bool:
    """Return True when two paths denote the exact same directory entry."""
    source_path = Path(source_path)
    target_path = Path(target_path)
    if not _paths_refer_to_same_entry(source_path.parent, target_path.parent):
        return False
    try:
        src_lstat = os.lstat(source_path)
        tgt_lstat = os.lstat(target_path)
    except OSError:
        return False
    if not os.path.samestat(src_lstat, tgt_lstat):
        return False
    if source_path.name == target_path.name:
        return True
    if stat.S_ISDIR(src_lstat.st_mode):
        return True
    try:
        with os.scandir(source_path.parent) as entries:
            matching_names: list[str] = []
            for entry in entries:
                try:
                    # On Windows Python 3.11, DirEntry.stat(follow_symlinks=False)
                    # reads WIN32_FIND_DATA with zero st_dev/st_ino, breaking samestat
                    # against os.lstat(source_path). Query os.lstat directly.
                    if os.path.samestat(os.lstat(entry.path), src_lstat):
                        matching_names.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return False
    if len(matching_names) == 1:
        return True
    if source_path.name in matching_names and target_path.name in matching_names:
        return False
    src_key = _normalize_collision_key(source_path.name)
    tgt_key = _normalize_collision_key(target_path.name)
    if src_key != tgt_key:
        return False
    return sum(1 for name in matching_names if _normalize_collision_key(name) == src_key) == 1


def _iter_registered_catgirl_names(
    config_manager,
    *,
    require_authoritative: bool = False,
) -> list[str]:
    """Return configured character names if ``config_manager.load_characters`` is available."""
    load_characters = getattr(config_manager, "load_characters", None)
    if not callable(load_characters):
        return []
    if require_authoritative:
        accepts_authoritative = False
        accepts_path = False
        accepts_allow_missing_default = False
        try:
            params = inspect.signature(load_characters).parameters.values()
            accepts_authoritative = any(
                param.name == "require_authoritative"
                or param.kind is inspect.Parameter.VAR_KEYWORD
                for param in params
            )
            accepts_path = any(
                param.name == "character_json_path"
                or param.kind is inspect.Parameter.VAR_KEYWORD
                for param in params
            )
            accepts_allow_missing_default = any(
                param.name == "allow_missing_default"
                or param.kind is inspect.Parameter.VAR_KEYWORD
                for param in params
            )
        except (TypeError, ValueError):
            accepts_authoritative = False
            accepts_path = False
            accepts_allow_missing_default = False

        runtime_path: str | None = None
        get_runtime_path = getattr(config_manager, "get_runtime_config_path", None)
        if callable(get_runtime_path):
            candidate = get_runtime_path("characters.json")
            if isinstance(candidate, (str, os.PathLike)):
                runtime_path = os.fspath(candidate)

        kwargs: dict[str, Any] = {}
        if accepts_authoritative:
            kwargs["require_authoritative"] = True
        else:
            logger.warning(
                "[CharacterMemory] %s.load_characters does not accept require_authoritative; "
                "falling back to plain load_characters()",
                type(config_manager).__name__,
            )
        if accepts_allow_missing_default:
            kwargs["allow_missing_default"] = False
        if runtime_path is not None:
            if accepts_path:
                kwargs["character_json_path"] = runtime_path
            else:
                logger.warning(
                    "[CharacterMemory] %s.load_characters does not accept character_json_path; "
                    "cannot pin authoritative read to runtime characters.json",
                    type(config_manager).__name__,
                )
        characters = load_characters(**kwargs)
        if not isinstance(characters, dict) or not isinstance(characters.get("猫娘"), dict):
            raise ValueError("character_config_not_authoritative: invalid catgirl registry")
        catgirls = characters["猫娘"]
        return [name for name in catgirls.keys() if isinstance(name, str) and name]
    try:
        characters = load_characters()
    except Exception:
        return []
    if not isinstance(characters, dict):
        return []
    catgirls = characters.get("猫娘")
    if not isinstance(catgirls, dict):
        return []
    return [name for name in catgirls.keys() if isinstance(name, str) and name]


def iter_authoritative_catgirl_names(config_manager) -> list[str]:
    """Return configured character names loaded authoritatively from runtime storage."""
    return _iter_registered_catgirl_names(config_manager, require_authoritative=True)


def _normalize_collision_key(name: str) -> str:
    return unicodedata.normalize("NFC", str(name or "").strip()).casefold()


def _find_shared_memory_source(
    config_manager,
    character_name: str,
    *,
    existing_names: Iterable[str] | None = None,
) -> tuple[Path, str] | None:
    """Return ``(shared_path, other_name)`` if ``character_name`` shares source storage."""
    if config_manager is None:
        return None
    names = (
        [str(name) for name in existing_names if isinstance(name, str) and name]
        if existing_names is not None
        else _iter_registered_catgirl_names(config_manager, require_authoritative=True)
    )
    roots = iter_character_memory_roots(config_manager)
    source_paths = list_character_memory_paths(config_manager, character_name)
    for other_name in names:
        if other_name == character_name:
            continue
        for base_dir in roots:
            source_dir = base_dir / character_name
            if _paths_refer_to_same_entry(base_dir / other_name, source_dir):
                return source_dir, other_name
        if not source_paths:
            continue
        other_paths = list_character_memory_paths(config_manager, other_name)
        for source_path in source_paths:
            for other_path in other_paths:
                if _paths_refer_to_same_entry(other_path, source_path):
                    return source_path, other_name
    return None


def find_character_shared_memory_owner(
    config_manager,
    character_name: str,
    *,
    existing_names: Iterable[str] | None = None,
) -> str | None:
    """Return another configured character name sharing source memory storage."""
    match = _find_shared_memory_source(
        config_manager,
        character_name,
        existing_names=existing_names,
    )
    return match[1] if match is not None else None


def find_character_name_collision(
    config_manager,
    candidate_name: str,
    *,
    exclude_names: Iterable[str] = (),
    existing_names: Iterable[str] | None = None,
) -> str | None:
    """Return the first configured character name colliding with ``candidate_name``.

    Detects exact duplicates, case-folded collisions (e.g. ``neko`` vs ``Neko``),
    Unicode NFC/NFD equivalents, and filesystem ``os.path.samefile`` aliases under
    active character memory roots.
    """
    raw_candidate = str(candidate_name or "")
    candidate = raw_candidate.strip()
    if not candidate:
        return None
    excluded = {str(name) for name in exclude_names if name}
    names = (
        [str(name) for name in existing_names if isinstance(name, str) and name]
        if existing_names is not None
        else _iter_registered_catgirl_names(config_manager)
    )
    candidate_key = _normalize_collision_key(candidate)
    roots = iter_character_memory_roots(config_manager) if config_manager is not None else []
    for existing_name in names:
        if existing_name in excluded:
            continue
        if _normalize_collision_key(existing_name) == candidate_key:
            return existing_name
        for base_dir in roots:
            for candidate_part in dict.fromkeys([raw_candidate, candidate]):
                if _paths_refer_to_same_entry(base_dir / existing_name, base_dir / candidate_part):
                    return existing_name
    return None


def _two_step_samefile_rename(source_path: Path, target_path: Path) -> bool:
    """Rename ``source_path`` to ``target_path`` via a sibling temporary path.

    Required on case-insensitive or Unicode-normalizing filesystems (Windows NTFS,
    macOS APFS) when changing only casing or Unicode normalization form.
    """
    parent_dir = source_path.parent
    temp_path = parent_dir / f".neko_rename_{uuid.uuid4().hex}_{source_path.name}"
    while os.path.lexists(temp_path):
        temp_path = parent_dir / f".neko_rename_{uuid.uuid4().hex}_{source_path.name}"

    os.replace(str(source_path), str(temp_path))
    try:
        if os.path.lexists(target_path):
            raise FileExistsError(
                f"Refusing two-step rename {source_path} -> {target_path}: "
                f"target entry still exists independently"
            )
        os.replace(str(temp_path), str(target_path))
    except BaseException:
        try:
            if os.path.lexists(temp_path) and not os.path.lexists(source_path):
                os.replace(str(temp_path), str(source_path))
        except OSError:
            logger.warning(
                "[CharacterMemory] failed to roll back intermediate rename %s -> %s",
                temp_path,
                source_path,
                exc_info=True,
            )
        raise
    return True


paths_refer_to_same_entry = _paths_refer_to_same_entry
paths_refer_to_same_directory_entry = _paths_refer_to_same_directory_entry
symlink_chain_reaches_path = _symlink_chain_reaches_path
two_step_samefile_rename = _two_step_samefile_rename


def _move_path(source_path: Path, target_path: Path) -> bool:
    if _symlink_chain_reaches_path(source_path, target_path):
        raise FileExistsError(
            f"Refusing to move symlink {source_path} onto its own target {target_path}"
        )

    if not source_path.exists():
        return False

    if source_path.is_dir():
        return _merge_directories(source_path, target_path)

    if _paths_refer_to_same_directory_entry(source_path, target_path):
        if source_path.name == target_path.name:
            return False
        return _two_step_samefile_rename(source_path, target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() or target_path.is_symlink():
        raise FileExistsError(
            f"Refusing to overwrite existing memory file while moving "
            f"{source_path} -> {target_path}"
        )

    shutil.move(str(source_path), str(target_path))
    return True


def _merge_directories(source_dir: Path, target_dir: Path) -> bool:
    if _symlink_chain_reaches_path(source_dir, target_dir):
        raise FileExistsError(
            f"Refusing to move symlink {source_dir} onto its own target {target_dir}"
        )

    if not source_dir.exists():
        return False

    if _paths_refer_to_same_directory_entry(source_dir, target_dir):
        if source_dir.name == target_dir.name:
            return False
        return _two_step_samefile_rename(source_dir, target_dir)

    if not target_dir.exists():
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(target_dir))
        return True

    # Pre-flight: check for conflicts before moving anything
    for child in source_dir.iterdir():
        candidate = target_dir / child.name
        if candidate.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing path while merging directories "
                f"{source_dir} -> {target_dir}: conflict at {child.name}"
            )

    changed = False
    for child in sorted(source_dir.iterdir(), key=lambda item: item.name):
        changed = _move_path(child, target_dir / child.name) or changed

    try:
        source_dir.rmdir()
    except OSError:
        pass

    return changed


def _rewrite_recent_message_character_name(item: dict[str, Any], old_name: str, new_name: str) -> bool:
    changed = False

    for field in MESSAGE_NAME_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value == old_name:
            item[field] = new_name
            changed = True

    nested_data = item.get("data")
    if isinstance(nested_data, dict):
        for field in MESSAGE_NAME_FIELDS:
            value = nested_data.get(field)
            if isinstance(value, str) and value == old_name:
                nested_data[field] = new_name
                changed = True

        content, content_changed = _rewrite_recent_content_character_name(
            nested_data.get("content"), old_name, new_name,
        )
        if content_changed:
            nested_data["content"] = content
            changed = True

    return changed


def _rewrite_recent_content_character_name(content: Any, old_name: str, new_name: str) -> tuple[Any, bool]:
    if not isinstance(content, str):
        return content, False
    changed = False
    for pattern in (
        f"{old_name}说：",
        f"{old_name}说:",
        f"{old_name}:",
        f"{old_name}->",
        f"[{old_name}]",
        f"{old_name} | ",
    ):
        if pattern in content:
            content = content.replace(pattern, pattern.replace(old_name, new_name))
            changed = True
    return content, changed


def _rewrite_pending_message_character_name(message: Any, old_name: str, new_name: str) -> Any:
    rewritten = deepcopy(message)
    if isinstance(rewritten, dict):
        _rewrite_recent_message_character_name(rewritten, old_name, new_name)
        return rewritten
    for field in MESSAGE_NAME_FIELDS:
        if getattr(rewritten, field, None) == old_name:
            setattr(rewritten, field, new_name)
    content, changed = _rewrite_recent_content_character_name(
        getattr(rewritten, "content", None), old_name, new_name,
    )
    if changed:
        setattr(rewritten, "content", content)
    return rewritten


def _rewrite_recent_file_character_name_unlocked(
    recent_path: Path, old_name: str, new_name: str,
) -> bool:
    if old_name == new_name or not recent_path.is_file():
        return False
    try:
        payload = json.loads(read_recent_text_unlocked(recent_path))
    except Exception:
        return False
    if not isinstance(payload, list):
        return False
    changed = False
    for item in payload:
        if isinstance(item, dict):
            changed = _rewrite_recent_message_character_name(item, old_name, new_name) or changed
    if changed:
        write_recent_payload_unlocked(recent_path, payload)
    return changed


def rewrite_recent_file_character_name(recent_path: Path, old_name: str, new_name: str) -> bool:
    """Rewrite the old character name inside a recent file. Blocking — worker thread only.

    Read and write live in one critical section so a concurrent memory_server
    writer cannot land between them and lose its own append.
    """
    with recent_file_lock(recent_path):
        return _rewrite_recent_file_character_name_unlocked(recent_path, old_name, new_name)


def list_character_recent_paths(config_manager, character_name: str) -> list[Path]:
    return list(dict.fromkeys(
        candidate
        for base_dir in iter_character_memory_roots(config_manager)
        for candidate in (
            base_dir / character_name / "recent.json",
            base_dir / f"recent_{character_name}.json",
        )
    ))


def begin_character_recent_transaction(
    config_manager, *character_names: str,
) -> dict[str, Any]:
    """Acquire all recent-file locks needed by a character lifecycle transaction."""
    recent_paths = list(dict.fromkeys(
        path
        for character_name in character_names
        for path in list_character_recent_paths(config_manager, character_name)
    ))
    return {
        "recent_paths": recent_paths,
        "held_locks": acquire_recent_file_locks(recent_paths),
    }


def release_character_recent_transaction(transaction: dict[str, Any] | None) -> None:
    """Release a lifecycle transaction's recent locks exactly once."""
    transaction = transaction or {}
    held_locks = transaction.get("held_locks") or []
    if held_locks:
        transaction["held_locks"] = []
        release_recent_file_locks(held_locks)


def rename_character_memory_storage(
    config_manager,
    old_name: str,
    new_name: str,
    *,
    keep_recent_locks: bool = False,
    recent_transaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_target_dir = get_runtime_character_memory_dir(config_manager, new_name)
    roots = iter_character_memory_roots(config_manager)
    if old_name != new_name:
        registered_names = _iter_registered_catgirl_names(
            config_manager,
            require_authoritative=True,
        )
        shared_source = _find_shared_memory_source(
            config_manager,
            old_name,
            existing_names=registered_names,
        )
        if shared_source is not None:
            shared_path, co_owner = shared_source
            raise FileExistsError(
                f"Refusing to rename memory storage {old_name!r} -> {new_name!r}: "
                f"source path {shared_path} is shared with existing character {co_owner!r}"
            )
        colliding_name = find_character_name_collision(
            config_manager,
            new_name,
            exclude_names=(old_name,),
            existing_names=registered_names,
        )
        if colliding_name is not None:
            raise FileExistsError(
                f"Refusing to rename memory storage {old_name!r} -> {new_name!r}: "
                f"target path {runtime_target_dir} collides with existing character {colliding_name!r}"
            )
        for other_name in registered_names:
            if other_name == old_name:
                continue
            for base_dir in dict.fromkeys([runtime_target_dir.parent, *roots]):
                if _paths_refer_to_same_entry(base_dir / other_name, runtime_target_dir):
                    raise FileExistsError(
                        f"Refusing to rename memory storage {old_name!r} -> {new_name!r}: "
                        f"target path {runtime_target_dir} collides with existing character {other_name!r}"
                    )
    pending_sources = list_character_recent_paths(config_manager, old_name)
    target_recent_paths = list_character_recent_paths(config_manager, new_name)
    target_recent = runtime_target_dir / "recent.json"
    transaction = recent_transaction or begin_character_recent_transaction(
        config_manager, old_name, new_name,
    )
    recent_paths = transaction["recent_paths"]
    redirect_snapshot = snapshot_recent_redirects(recent_paths)
    deletion_snapshot = snapshot_recent_deletions(recent_paths)
    activation_scope: set[str] = set()
    generation_snapshot: dict[str, tuple[int, int]] = {}
    pending_snapshot: dict[Path, list[Any]] = {}
    try:
        # BOTH names are held shut for the duration of the move, and the
        # target is only lifted once the rename has actually committed,
        # below. The source loses its directory to the move. The target may
        # have been DELETED earlier and still be retired, and evicting here
        # lifted that retirement before anything had been moved -- so
        # anything still holding the old name (an in-flight proactive turn, a
        # session bound to it before the delete) was free to write for the
        # whole window, and both outcomes were bad. Its flush creates
        # memory/<new_name>/, which trips _merge_directories' colliding-child
        # pre-flight and aborts the rename outright; or it stages first and
        # flushes after the move, and since staging copies the WHOLE payload
        # rather than a delta, the merged history is replaced by that one
        # record -- four merged entries became one. All three stores stage
        # the whole cached collection rather than a delta, so none of them
        # is exempt.
        #
        # The reason this used to give -- that retiring the target would deny
        # it the lazy directory creation every sibling writer gets -- does
        # not survive measurement. It holds only until the move creates the
        # directory, after which a retired name writes into it perfectly
        # well; and the lift below always runs, so the target creates and
        # writes normally afterwards even when there was nothing to merge.
        retire_character_runtime_caches(old_name)
        retire_character_runtime_caches(new_name)
        # BOTH names, and for the same reason on each: retirement refuses to
        # CREATE a directory but permits writing into one that exists.
        #
        # Target: the merge creates its directory partway through, and from
        # that moment a write from the identity that used to own the name
        # lands on the history just moved in.
        #
        # Source: its directory exists for the whole merge. On the
        # child-by-child path a late write can recreate a file after the
        # children are moved and before _merge_directories rmdir()s the
        # source -- and that rmdir swallows its failure, so the rename
        # reports success while memory/<old_name>/ survives with content and
        # the renamed-away identity still looks like it has memory.
        #
        # The fence does not consult the filesystem, so it covers the whole
        # operation; the finally is what guarantees it comes back down.
        #
        # Which makes retiring the TARGET above redundant, measured: with
        # this fence in place, lifting there instead -- or not touching the
        # target at all -- reddens nothing. It stays as the independent
        # cover for the half before the merge creates the directory, for
        # the case where this fence is somehow never set. Retiring the
        # SOURCE is not redundant: its directory is going away.
        fence_character_runtime_writes(old_name, new_name)
        # Target character name may have been renamed away; sever old redirects before reuse, preventing new characters writing to old targets.
        (
            _,
            activation_scope,
            activation_deletion_snapshot,
            generation_snapshot,
        ) = activate_recent_paths(target_recent_paths)
        deletion_snapshot |= activation_deletion_snapshot
        pending_snapshot = {
            path: deepcopy(get_recent_pending_unlocked(path))
            for path in recent_paths
        }
        changed = False
        for base_dir in roots:
            changed = _merge_directories(base_dir / old_name, runtime_target_dir) or changed

            for legacy_name, target_name in LEGACY_CHARACTER_MEMORY_FILE_MAP.items():
                source_path = base_dir / legacy_name.format(name=old_name)
                target_path = runtime_target_dir / target_name
                changed = _move_path(source_path, target_path) or changed

            for legacy_name in LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES:
                source_path = base_dir / legacy_name.format(name=old_name)
                if source_path.exists():
                    target_path = runtime_target_dir / "semantic_memory_legacy"
                    changed = _move_path(source_path, target_path) or changed

        changed = _rewrite_recent_file_character_name_unlocked(
            target_recent, old_name, new_name,
        ) or changed

        target_pending = get_recent_pending_unlocked(target_recent)
        if old_name != new_name:
            target_pending = [
                _rewrite_pending_message_character_name(message, old_name, new_name)
                for message in target_pending
            ]
        target_pending_key = os.path.normcase(os.path.abspath(os.fspath(target_recent)))
        for source_recent in pending_sources:
            if (
                source_recent == target_recent
                or os.path.normcase(os.path.abspath(os.fspath(source_recent))) == target_pending_key
            ):
                continue
            source_pending = get_recent_pending_unlocked(source_recent)
            set_recent_pending_unlocked(source_recent, [])
            target_pending.extend(
                _rewrite_pending_message_character_name(message, old_name, new_name)
                for message in source_pending
            )
        set_recent_pending_unlocked(target_recent, target_pending)
        redirect_recent_paths(pending_sources, target_recent)

        # Publication: the move has happened and this call is about to
        # commit, so the target becomes a live identity again. As late as
        # possible, because everything before it is still window.
        #
        # The lift has to DROP THE CACHE, not merely mark the name live: a
        # write refused during the window still staged into it, and the move
        # has just replaced the directory underneath, so nothing held for
        # this name is still valid. Dropping it makes the next write re-read
        # the merged file instead of flushing a window record over it.
        #
        # revive_character_runtime_caches would behave identically HERE --
        # measured, and its retired branch evicts for this very reason -- but
        # only because the target is retired by the line above. evict does
        # not depend on that, so it is the one that states the intent.
        evict_character_runtime_caches(new_name)

        result = {
            "changed": changed,
            "runtime_dir": runtime_target_dir,
            "exists_after": runtime_target_dir.exists(),
            "_recent_rename_transaction": transaction,
        }
        transaction.update({
            "pending_snapshot": pending_snapshot,
            "redirect_snapshot": redirect_snapshot,
            "deletion_snapshot": deletion_snapshot,
            "activation_scope": activation_scope,
            "generation_snapshot": generation_snapshot,
        })
        if not keep_recent_locks:
            release_character_recent_transaction(transaction)
        return result
    except BaseException:
        # Undo the cache lifecycle too, by rule rather than by snapshot. At the
        # moment this can raise, the config mutation and the "target must be
        # free" check both still sit with the caller, so the SOURCE is
        # unconditionally live and the TARGET unconditionally absent. Leaving
        # them as-is stranded a live source retired -- every later sidecar write
        # dropped, because a retired name never creates its directory -- and
        # left the absent target reactivated, so a late write could recreate an
        # identity that was never committed. The caller cannot fix it either:
        # it fills its rollback name tuples from this function returning.
        evict_character_runtime_caches(old_name)
        retire_character_runtime_caches(new_name)
        restore_recent_registry_state(
            list(set(recent_paths) | activation_scope),
            redirect_snapshot,
            deletion_snapshot,
            generation_snapshot,
        )
        for path, messages in pending_snapshot.items():
            set_recent_pending_unlocked(path, messages)
        if not keep_recent_locks:
            release_character_recent_transaction(transaction)
        raise
    finally:
        # Unconditional, and after the publication lift above: a fence that
        # survived a raise would silence this character for the life of the
        # process, which is worse than the write it exists to stop.
        unfence_character_runtime_writes(old_name, new_name)


def finalize_character_recent_rename(result: dict[str, Any]) -> None:
    """Release recent locks after the surrounding rename transaction commits."""
    release_character_recent_transaction(result.get("_recent_rename_transaction"))


def rollback_character_recent_rename(result: dict[str, Any]) -> None:
    """Restore pending state and redirects after the surrounding rename rolls back."""
    transaction = result.get("_recent_rename_transaction") or {}
    snapshot = transaction.get("pending_snapshot") or {}
    recent_paths = transaction.get("recent_paths") or list(snapshot)
    activation_scope = transaction.get("activation_scope") or set()
    held_locks = transaction.get("held_locks") or []
    if not held_locks:
        held_locks = acquire_recent_file_locks(recent_paths)
    try:
        restore_recent_registry_state(
            list(set(recent_paths) | set(activation_scope)),
            transaction.get("redirect_snapshot") or {},
            transaction.get("deletion_snapshot") or set(),
            transaction.get("generation_snapshot") or {},
        )
        for path, messages in snapshot.items():
            set_recent_pending_unlocked(path, messages)
    finally:
        transaction["held_locks"] = held_locks
        release_character_recent_transaction(transaction)


def clear_character_recent_redirects(config_manager, character_name: str) -> None:
    """Detach obsolete path redirects before a newly created name starts writing."""
    recent_paths = list_character_recent_paths(config_manager, character_name)
    with recent_file_locks(recent_paths):
        activate_recent_paths(recent_paths)


def begin_character_recent_activation(
    config_manager, *character_names: str,
) -> dict[str, Any]:
    """Activate a reused character identity while retaining its recent locks."""
    transaction = begin_character_recent_transaction(config_manager, *character_names)
    recent_paths = transaction["recent_paths"]
    try:
        (
            redirect_snapshot,
            activation_scope,
            deletion_snapshot,
            generation_snapshot,
        ) = activate_recent_paths(recent_paths)
        transaction.update({
            "redirect_snapshot": redirect_snapshot,
            "activation_scope": activation_scope,
            "deletion_snapshot": deletion_snapshot,
            "generation_snapshot": generation_snapshot,
        })
        return transaction
    except BaseException:
        release_character_recent_transaction(transaction)
        raise


def rollback_character_recent_activation(transaction: dict[str, Any]) -> None:
    """Restore a reused character identity when publishing its config fails."""
    recent_paths = transaction.get("recent_paths") or []
    activation_scope = transaction.get("activation_scope") or set()
    held_locks = transaction.get("held_locks") or []
    if not held_locks:
        held_locks = acquire_recent_file_locks(recent_paths)
    try:
        restore_recent_registry_state(
            list(set(recent_paths) | set(activation_scope)),
            transaction.get("redirect_snapshot") or {},
            transaction.get("deletion_snapshot") or set(),
            transaction.get("generation_snapshot") or {},
        )
    finally:
        transaction["held_locks"] = held_locks
        release_character_recent_transaction(transaction)


async def _await_task_to_completion(task: asyncio.Task) -> Any:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def asave_characters_with_recent_activation(
    config_manager, characters: dict[str, Any], *character_names: str,
) -> bool:
    """Publish config and recent identities; return whether cancellation was deferred."""
    activation_task = asyncio.create_task(asyncio.to_thread(
        begin_character_recent_activation, config_manager, *character_names,
    ))
    try:
        transaction = await asyncio.shield(activation_task)
    except asyncio.CancelledError:
        transaction = await _await_task_to_completion(activation_task)
        await _await_task_to_completion(asyncio.create_task(asyncio.to_thread(
            rollback_character_recent_activation, transaction,
        )))
        raise

    save_task = asyncio.create_task(config_manager.asave_characters(characters))
    try:
        await asyncio.shield(save_task)
    except asyncio.CancelledError:
        try:
            await _await_task_to_completion(save_task)
        except BaseException:
            await _await_task_to_completion(asyncio.create_task(asyncio.to_thread(
                rollback_character_recent_activation, transaction,
            )))
        else:
            release_character_recent_transaction(transaction)
            evict_character_runtime_caches(*character_names)
            return True
        raise
    except BaseException:
        await _await_task_to_completion(asyncio.create_task(asyncio.to_thread(
            rollback_character_recent_activation, transaction,
        )))
        raise
    else:
        release_character_recent_transaction(transaction)
        # Publishing an identity is the explicit "this character is live" event.
        # A name deleted earlier in the process is still retired, and a freshly
        # created profile has no memory/<name>/ yet, so without this lift the
        # sidecar writers would drop its startup greeting and early proactive
        # decisions instead of creating the directory. Rollback paths must NOT
        # lift: nothing was published there.
        evict_character_runtime_caches(*character_names)
        return False


def _find_co_owner_for_memory_path(
    config_manager,
    character_name: str,
    entry_path: Path,
    *,
    existing_names: Iterable[str] | None = None,
) -> str | None:
    """Return another configured character name sharing ``entry_path`` on disk."""
    names = (
        [str(name) for name in existing_names if isinstance(name, str) and name]
        if existing_names is not None
        else _iter_registered_catgirl_names(config_manager, require_authoritative=True)
    )
    for other_name in names:
        if other_name == character_name:
            continue
        for other_path in list_character_memory_paths(config_manager, other_name):
            if _paths_refer_to_same_entry(other_path, entry_path):
                return other_name
    return None


find_co_owner_for_memory_path = _find_co_owner_for_memory_path


def _is_recent_registry_key_shared_with_other(
    config_manager,
    character_name: str,
    recent_path: Path,
    *,
    existing_names: Iterable[str] | None = None,
) -> bool:
    """Return True if another configured character maps to the same recent registry key."""
    recent_key = os.path.normcase(os.path.abspath(os.fspath(recent_path)))
    names = (
        [str(name) for name in existing_names if isinstance(name, str) and name]
        if existing_names is not None
        else _iter_registered_catgirl_names(config_manager, require_authoritative=True)
    )
    for other_name in names:
        if other_name == character_name:
            continue
        for other_recent in list_character_recent_paths(config_manager, other_name):
            other_key = os.path.normcase(os.path.abspath(os.fspath(other_recent)))
            if other_key == recent_key:
                return True
    return False


def delete_character_memory_storage(
    config_manager,
    character_name: str,
    *,
    capture_pending: bool = False,
    keep_recent_locks: bool = False,
    recent_transaction: dict[str, Any] | None = None,
    before_remove: Callable[[Path], None] | None = None,
    on_skip_shared: Callable[[Path, str], None] | None = None,
) -> list[Path] | tuple[list[Path], dict[str, Any]]:
    """Remove unshared character memory entries and clear recent state.

    When provided, ``before_remove(entry_path)`` runs immediately before deleting
    each unshared path, and ``on_skip_shared(entry_path, co_owner)`` runs when
    an entry is skipped because another registered character co-owns it.
    """
    transaction = recent_transaction or begin_character_recent_transaction(
        config_manager, character_name,
    )
    recent_candidates = transaction["recent_paths"]
    redirect_snapshot: dict[str, str] = {}
    deletion_scope: list[Path] = []
    deletion_snapshot: set[str] = set()
    fenced_recent = False
    pending_snapshot: dict[Path, list[Any]] = {}
    try:
        retire_character_runtime_caches(character_name)
        registered_names = _iter_registered_catgirl_names(
            config_manager,
            require_authoritative=True,
        )
        unshared_recent_candidates = [
            recent_path
            for recent_path in recent_candidates
            if not _is_recent_registry_key_shared_with_other(
                config_manager,
                character_name,
                recent_path,
                existing_names=registered_names,
            )
        ]
        redirect_snapshot, deletion_scope, deletion_snapshot = (
            fence_recent_deletions_and_clear_redirects(unshared_recent_candidates)
        )
        fenced_recent = True
        pending_snapshot = {
            path: deepcopy(get_recent_pending_unlocked(path))
            for path in unshared_recent_candidates
        }
        removed_paths: list[Path] = []
        for entry_path in list_character_memory_paths(config_manager, character_name):
            co_owner = _find_co_owner_for_memory_path(
                config_manager,
                character_name,
                entry_path,
                existing_names=registered_names,
            )
            if co_owner is not None:
                if on_skip_shared is not None:
                    on_skip_shared(entry_path, co_owner)
                logger.warning(
                    "[CharacterMemory] skipping deletion of %s for %r because it is shared with character %r",
                    entry_path,
                    character_name,
                    co_owner,
                )
                continue
            if before_remove is not None:
                before_remove(entry_path)
            if entry_path.is_dir() and not entry_path.is_symlink():
                shutil.rmtree(entry_path)
            else:
                entry_path.unlink()
            removed_paths.append(entry_path)

        for recent_path in unshared_recent_candidates:
            set_recent_pending_unlocked(recent_path, [])
        transaction.update({
            "pending_snapshot": pending_snapshot,
            "redirect_snapshot": redirect_snapshot,
            "deletion_snapshot": deletion_snapshot,
            "deletion_scope": deletion_scope,
        })
        if not keep_recent_locks:
            release_character_recent_transaction(transaction)
        if capture_pending:
            return removed_paths, transaction
        return removed_paths
    except BaseException:
        if fenced_recent:
            restore_recent_redirects(redirect_snapshot)
            restore_recent_deletions(list(deletion_scope), deletion_snapshot)
        for path, messages in pending_snapshot.items():
            set_recent_pending_unlocked(path, messages)
        if not keep_recent_locks:
            release_character_recent_transaction(transaction)
        raise


def finalize_character_recent_delete(result: dict[str, Any]) -> None:
    """Release recent locks after the surrounding delete transaction commits."""
    release_character_recent_transaction(result)


def rollback_character_recent_delete(result: dict[str, Any]) -> None:
    """Restore pending state and redirects after a delete transaction rolls back."""
    recent_paths = result.get("recent_paths") or []
    deletion_scope = result.get("deletion_scope") or recent_paths
    held_locks = result.get("held_locks") or []
    if not held_locks:
        held_locks = acquire_recent_file_locks(recent_paths)
        result["held_locks"] = held_locks
    try:
        restore_recent_redirects(result.get("redirect_snapshot") or {})
        restore_recent_deletions(
            list(deletion_scope), result.get("deletion_snapshot") or set(),
        )
        for path, messages in (result.get("pending_snapshot") or {}).items():
            set_recent_pending_unlocked(path, messages)
    finally:
        release_character_recent_transaction(result)
