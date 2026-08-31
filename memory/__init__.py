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

"""Memory subsystem.

⚠️ LLM call conventions (project-level hard rules)
================================
**Any call in memory/ and utils/ going through ``utils.llm_client.create_chat_llm`` /
``ChatOpenAI``:**

1. **Do not pass ``temperature=...``**. Both default to ``None`` (not written into the
   request body), letting the model endpoint respond with its own default behavior. The
   same rule applies to any wrapper helper (e.g. ``FactStore._allm_call_with_retries``
   historically accepted ``temperature=``; it has been removed).
   Rationale: (1) compatibility with models that reject the parameter, such as
   o1/o3/gpt-5-thinking/Claude extended-thinking; (2) per-task custom temperatures
   (0.1/0.2/0.3/0.5/1.0) introduce hard-to-reproduce regressions.
   Gatekeeper: ``scripts/check_no_temperature.py`` (CI: ``.github/workflows/analyze.yml``).

2. **Models come from tiers; no hardcoded fallbacks**. Every LLM call goes through
   ``self._config_manager.get_model_api_config('summary'|'correction'|'emotion'|'vision'|...)``
   to fetch the ``api_config['model'] / ['base_url'] / ['api_key']`` triple. Do **not**
   write fallbacks like ``api_config.get('model', SETTING_PROPOSER_MODEL)`` — those are
   retired hardcodes (``SETTING_PROPOSER_MODEL`` / ``SETTING_VERIFIER_MODEL`` were
   decommissioned in 2026-04). If the tier isn't configured, ``api_config['model']`` is
   ``''`` and the request is explicitly rejected by the API; that is a configuration
   error which should surface directly, not be silently masked by a qwen-max fallback.

3. **Tiers used by memory submodules**: all active LLM paths run on the ``summary`` or
   ``correction`` tier (fact extraction / signal detection / reflection synthesis /
   fact dedup / recall rerank → ``summary``; recent.review +
   persona.correction + promotion merge → ``correction``). Do not introduce new
   hardcoded model names.

If you have a very specific reason to bypass this, delete
``scripts/check_no_temperature.py`` first and explain it in the PR description for the
reviewer to judge.
"""
import os
import shutil
import logging

from .recent import CompressedRecentHistoryManager
from .settings import ImportantSettingsManager
from .timeindex import TimeIndexedMemory
from .facts import FactStore
from .persona import PersonaManager
from .reflection import ReflectionEngine

_logger = logging.getLogger(__name__)


def character_dir_is_within_memory_root(memory_dir: str, name: str) -> bool:
    """Whether ``memory_dir/name`` really resolves to a child of the root.

    The public name for the rule the sidecar stores already apply before
    resolving a write. Read paths need it too, and reaching for the
    private one from outside this package would have made the rule a
    convention rather than a contract.
    """
    return _is_within_memory_root(
        str(memory_dir), name, os.path.join(str(memory_dir), name)
    )


def _is_within_memory_root(memory_dir: str, name: str, character_dir: str) -> bool:
    """Whether character_dir is a DIRECT child of the memory root.

    A character name reaches this as a path component, and a historical
    unsafe one resolves somewhere else entirely: "." lands on the root, ".."
    escapes above it, and a name carrying a separator nests. Every sidecar
    store asks this before resolving a write, so the answer lives here
    rather than three times over.
    """
    # Before normalisation, because normalisation is what differs between
    # platforms: POSIX treats a backslash as an ordinary filename character,
    # so "a\b" arrives as a legal DIRECT child and every check below passes
    # it. On Windows the same name is a separator and gets rejected. The
    # backslash half is what makes the answer the same on both; the forward
    # slash is already refused below, because the basename can never equal a
    # name containing one -- it is listed here so the two read as one rule,
    # and so it still holds if that equality is ever relaxed. Measured: only
    # dropping the backslash half reddens the guard.
    if "/" in name or "\\" in name:
        return False
    # realpath, not abspath: abspath is pure string arithmetic and leaves a
    # symlink unresolved, so a memory/<name> pointing anywhere at all still
    # looked like a direct child and the sidecar was written THROUGH the link.
    # Both sides get the same treatment, so a memory root that is itself a
    # link (a tree moved to another drive) keeps working.
    root = os.path.realpath(str(memory_dir))
    resolved = os.path.realpath(character_dir)
    # DIRECT child, and named exactly for the character. "a/b" nests a level
    # deeper and leaves an "a/" behind that facts_sync reads as a character
    # of its own; "./x" lands on the same directory as a character actually
    # called "x" and would share its sidecar.
    return (
        os.path.dirname(resolved) == root
        and os.path.basename(resolved) == name
    )


def ensure_character_dir(memory_dir: str, name: str) -> str:
    """Return the character-specific directory memory_dir/{name}/, creating it if missing."""
    char_dir = os.path.join(str(memory_dir), name)
    os.makedirs(char_dir, exist_ok=True)
    return char_dir


# 旧文件名 → 新文件名的映射（不含 name 后缀）
#
# Borrowed from utils.character_memory rather than copied. The copy that used
# to live here had drifted three entries behind: time_indexed_{name}.db,
# facts_archive_{name}.json and reflections_archive_{name}.json were all
# renameable and selectable but never migrated, so a character whose only
# history was one of those files was offered in the panel and then reported
# as having none -- the startup migration left the file in the memory root
# while every reader looked inside memory/{name}/.
#
# utils.character_memory imports nothing from this package, so the direction
# is safe.
from utils.character_memory import (  # noqa: E402
    LEGACY_CHARACTER_MEMORY_FILE_MAP as _MIGRATION_MAP,
)

# Longest suffix first, so "time_indexed_Carol.db" decodes to "Carol"
# rather than to "Carol.db" via the extension-less pattern that also
# matches it.
_LEGACY_ROOT_ENTRY_PATTERNS = tuple(
    sorted(
        _MIGRATION_MAP,
        key=lambda pattern: (
            len(pattern.partition("{name}")[2]),
            len(pattern.partition("{name}")[0]),
        ),
        reverse=True,
    )
)

# SQLite writes these beside a database and they are transient. The
# extension-less "time_indexed_{name}" pattern matches them, so
# "time_indexed_Carol.db-wal" decodes to a character called
# "Carol.db-wal" -- a name that would then get a directory of its own.
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _legacy_root_entry_owner(entry_name: str) -> str | None:
    """Return the character a FLAT legacy memory FILE belongs to, if any.

    Decoding a legacy filename is a MIGRATION concern and lives here for
    that reason. It used to sit in ``utils.character_memory`` where the
    panel selector called it on every request, which made a second layout
    something the read path had to understand rather than something the
    migration removes.
    """
    for pattern in _LEGACY_ROOT_ENTRY_PATTERNS:
        prefix, _, suffix = pattern.partition("{name}")
        if not entry_name.startswith(prefix) or not entry_name.endswith(suffix):
            continue
        end = len(entry_name) - len(suffix) if suffix else len(entry_name)
        if end <= len(prefix):
            # A bare prefix names nobody.
            continue
        owner = entry_name[len(prefix) : end]
        # A leading dot or a path separator is never a character, and this
        # name goes on to build paths.
        if owner.startswith(".") or "/" in owner or chr(92) in owner:
            continue
        return owner
    return None


def _decoded_owner_is_safe(owner: str) -> bool:
    """Whether a decoded owner is a name this project would accept as one.

    A legacy filename is not a validated identity -- it is whatever the old
    layout happened to write -- and the decoded string goes straight into a
    path. On Windows "Bob." and "Bob " both resolve to "Bob", so
    "time_indexed_Bob..db" migrated an unrelated orphan INTO the real Bob's
    directory as his time_indexed.db. Measured, on the platform this ships
    on.

    ``validate_character_name`` already carries those rules -- trailing dot,
    "..", path separators, reserved device names -- and the trailing DOT is
    the form that actually corrupts today: makedirs creates "Bob", the move
    succeeds, and the orphan becomes the real Bob's time_indexed.db.

    The equality check is defence in depth rather than the thing preventing
    that. Validation STRIPS before it judges, so "Bob " passes as "Bob" --
    but measured on this platform the trailing-SPACE form fails loudly
    instead of mis-attaching: makedirs("Bob ") creates "Bob", while
    shutil.move into "Bob \\..." raises FileNotFoundError, so the migration
    logs and moves on. It is kept because accepting a decoded name that is
    not the name on disk is a latent identity split, and because a
    filesystem that resolves the two silently would turn it into the dot
    case. Its contract is pinned directly rather than through an effect it
    does not currently produce.
    """
    from utils.character_name import validate_character_name

    result = validate_character_name(owner, allow_dots=True)
    return result.ok and result.normalized == owner


def _legacy_root_file_owners(memory_dir: str, known: set) -> list:
    """Owners of flat legacy FILES still sitting in the memory root.

    Files only. A per-character directory and a legacy
    ``semantic_memory_<name>/`` vector store are the same shape on disk,
    so a character legitimately named "semantic_memory_Alice" would be
    decoded to "Alice" and have its directory moved out from under it.
    A vector store holds no assistant history either way, so nothing the
    panel can read is lost by leaving those alone.
    """
    owners: list[str] = []
    # NOT compared case-insensitively, deliberately. A name differing from a
    # configured one only by case is either the SAME directory (Windows, where
    # the per-name loop above already migrates the file through the
    # configured spelling, so skipping here changes nothing) or a genuinely
    # DIFFERENT character (POSIX, where skipping would strand its history in
    # the root forever). Measured both ways: the guard was a no-op on one
    # platform and harmful on the other.
    try:
        entries = sorted(os.listdir(memory_dir))
    except OSError:
        return owners
    for entry in entries:
        if entry.endswith(_SQLITE_SIDECAR_SUFFIXES):
            continue
        entry_path = os.path.join(memory_dir, entry)
        # A LINK is never migrated. shutil.move recreates it at the
        # destination, so memory/<name>/time_indexed.db becomes a link out
        # of the memory root and every reader follows it from then on --
        # the link is inside the character namespace at that point, which
        # is worse than leaving it flat where nothing reads it.
        if os.path.islink(entry_path):
            _logger.warning(
                "[Memory] 跳过符号链接的旧文件: %s", entry,
            )
            continue
        if not os.path.isfile(entry_path):
            continue
        owner = _legacy_root_entry_owner(entry)
        if owner is not None and not _decoded_owner_is_safe(owner):
            _logger.warning(
                "[Memory] 跳过不安全的旧文件名: %s", entry,
            )
            continue
        if owner and owner not in known:
            known.add(owner)
            owners.append(owner)
    return owners


def _roll_back_sidecars(
    old_path: str, new_path: str, moved_sidecars: list[str], old_filename: str
) -> None:
    """Put back sidecars that moved before their database could follow.

    Keeping the database while one of its sidecars has already gone
    leaves a source that no longer carries its own WAL, so anything
    opening it before the retry reads a database missing committed rows.
    All-or-nothing is easier to reason about than an ordering argument
    about who opens what when.

    A function rather than two copies because it has to happen on BOTH
    failures, and only one of them had it.
    """
    for suffix in moved_sidecars:
        try:
            shutil.move(new_path + suffix, old_path + suffix)
        except Exception as e:
            _logger.warning(
                f"[Memory] 回滚失败 {old_filename}{suffix}: {e}"
            )


def migrate_to_character_dirs(memory_dir: str, names: list[str]) -> None:
    """One-time migration: move legacy memory_dir/{type}_{name}.ext into memory_dir/{name}/{type}.ext"""
    memory_dir = str(memory_dir)
    # Configured names PLUS whatever the root still holds under a legacy
    # filename. Migrating only the configured ones left an unconfigured
    # owner flat forever -- and no reader understands that layout, so its
    # history was on disk and unreachable. A character absent from
    # characters.json is exactly the case nothing else was going to fix:
    # delete and rename both handle the flat names, but neither runs for
    # an identity nobody can name any more.
    known = {name for name in names if name}
    for name in list(names) + _legacy_root_file_owners(memory_dir, known):
        # The DESTINATION, before anything is written to it.
        # ensure_character_dir accepts an existing memory/<name> that is
        # a link, and every shutil.move below then writes THROUGH it --
        # so an orphan's authoritative database and its sidecars leave
        # the memory root at startup. Refusing the source link was only
        # half of it; this is the other end of the same move.
        if not character_dir_is_within_memory_root(memory_dir, name):
            _logger.warning(
                "[Memory] 跳过符号链接的角色目录: %s", name,
            )
            continue
        char_dir = ensure_character_dir(memory_dir, name)
        for old_pattern, new_filename in _MIGRATION_MAP.items():
            old_filename = old_pattern.replace('{name}', name)
            old_path = os.path.join(memory_dir, old_filename)
            new_path = os.path.join(char_dir, new_filename)
            if not os.path.exists(old_path) or os.path.exists(new_path):
                continue
            # Here too, not only during owner DISCOVERY. Every name in
            # ``names`` bypasses _legacy_root_file_owners, so the link
            # check added there did nothing for a configured character:
            # a linked time_indexed_Carol.db still satisfied exists()
            # and shutil.move recreated it as memory/Carol/time_indexed.db,
            # inside the namespace, where every reader follows it.
            if os.path.islink(old_path):
                _logger.warning(
                    "[Memory] 跳过符号链接的旧文件: %s",
                    old_filename,
                )
                continue
            # The SIDECARS too, and before any of them moves. shutil.move
            # recreates a link at the destination just the same, so a
            # linked -wal beside an ordinary database installs itself as
            # memory/<name>/time_indexed.db-wal and every later SQLite
            # open follows it out of the namespace -- to read AND to
            # write. Checked up front rather than per sidecar: refusing
            # one of them halfway is the split state the ordering below
            # exists to avoid.
            linked_sidecar = next(
                (
                    suffix
                    for suffix in _SQLITE_SIDECAR_SUFFIXES
                    if os.path.islink(old_path + suffix)
                ),
                None,
            )
            if linked_sidecar is not None:
                _logger.warning(
                    "[Memory] 跳过符号链接的旧文件: %s",
                    old_filename + linked_sidecar,
                )
                continue
            # A destination SIDECAR with no destination database is an
            # ambiguous half-migration, and pairing our database with it
            # is worse than leaving both alone: SQLite will replay a
            # foreign WAL of the same page size into the database it
            # finds beside it, so the stale rows REPLACE the real ones on
            # first open. The per-suffix skip below quietly did exactly
            # that -- it declined to move our sidecar and then moved the
            # database anyway.
            stray_destination = next(
                (
                    suffix
                    for suffix in _SQLITE_SIDECAR_SUFFIXES
                    # lexists: a DANGLING link answers False to exists(),
                    # so a broken -wal at the destination slipped past
                    # this and the database moved in beside it -- the
                    # very pairing the check exists to refuse.
                    if os.path.lexists(new_path + suffix)
                ),
                None,
            )
            if stray_destination is not None:
                _logger.warning(
                    "[Memory] 目标已有旁文件，跳过: %s",
                    name + "/" + new_filename + stray_destination,
                )
                continue
            # Sidecars FIRST, the database LAST. An uncheckpointed WAL holds
            # committed rows, so moving the database without it loses them,
            # and left in the root it is unreadable anyway -- SQLite looks
            # for it beside the db.
            #
            # The ORDER is what makes an interrupted run recoverable. With
            # the database moved first, a process that died before the WAL
            # followed could never retry: the database is gone from the root
            # by then, so the guard above is false on every later run and the
            # WAL is stranded. Moving it last means a crash always leaves the
            # database still flat, and the whole step simply runs again.
            interrupted = False
            moved_sidecars: list[str] = []
            for suffix in _SQLITE_SIDECAR_SUFFIXES:
                sidecar = old_path + suffix
                if not os.path.exists(sidecar) or os.path.exists(
                    new_path + suffix
                ):
                    continue
                try:
                    shutil.move(sidecar, new_path + suffix)
                except Exception as e:
                    _logger.warning(
                        f"[Memory] 迁移失败 {old_filename}{suffix}: {e}"
                    )
                    interrupted = True
                    break
                moved_sidecars.append(suffix)
            if interrupted:
                _roll_back_sidecars(
                    old_path, new_path, moved_sidecars, old_filename
                )
                # Leave the database where it is, so the next run retries
                # the whole set rather than stranding what did not move.
                continue
            try:
                shutil.move(old_path, new_path)
                _logger.info(f"[Memory] 迁移 {old_filename} → {name}/{new_filename}")
            except Exception as e:
                _logger.warning(f"[Memory] 迁移失败 {old_filename}: {e}")
                # And put the sidecars BACK. Unlike a crash this returns
                # and startup carries on, so the runtime would look in the
                # character directory and find sidecars with no database,
                # while the authoritative one sits stranded in the root. A
                # writable open then creates a fresh database beside them.
                # The sidecar branch above already rolled back; this one
                # simply never did.
                _roll_back_sidecars(
                    old_path, new_path, moved_sidecars, old_filename
                )


