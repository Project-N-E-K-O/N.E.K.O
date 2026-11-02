# -*- coding: utf-8 -*-
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

"""Import Molili AI Friends user-data exports into the external-import candidate model.

Same contract as ``memory.external_markdown_import``: the archive is *data*.
Nothing in it is executed, no LLM runs here, and the mapping is deterministic
and reviewable. The caller (``main_routers/memory_router.py``) feeds the
produced candidates into the shared preview/commit pipeline, with
``source_format="molili"``.

Mapping (v1, archive layout ``molili-user-data-export`` schema 1):

* ``manifest.json`` -- format/schema sniffing; counts feed the skip report.
* ``personality/<agent>/role_prompt.md`` -- not a memory candidate. It is the
  character's *base* setting: split on markdown headings into per-section
  card fields (``character_card.sections``, emoji-cleaned, tag = heading
  title) so the card shows tagged areas instead of one blob. The full
  cleaned text rides along as ``character_card.system_prompt`` for
  fallback/compat.
* ``personality/<agent>/{trait_profile,interaction_profile}.md`` and the
  numbered lines of ``cognitive.md`` -> persona candidates, ``entity=neko``:
  evolved self-view content that belongs in the persona pool.
* ``memory/long_term.jsonl`` ``profile_v1`` -> persona candidate,
  ``entity=master`` (the portrait of the *user*), minus its basic-info
  identity fields (see ``_BASIC_INFO_KEY``: the Molili key is homoglyph
  adjacent to a generic label -- pinned by constant). Identity fields are
  returned separately in ``master_profile`` for the caller to route (per
  -character card prompt, or the global master card); stripping them also
  changes the fusion fingerprint, so a previously rejected portrait genuinely
  re-fuses on re-import. When the v1 basic-info is null, identity falls back
  to the structured ``profile_cache`` record.
* ``memory/long_term.jsonl`` ``event_v1`` (already-extracted
  ``summary`` + ``time_iso``) and ``diaries/*.jsonl`` -> daily candidates
  grouped per local day into one ``molili/memory/YYYY-MM-DD.md``
  ``source_file`` so the commit side runs one per-day extraction with the
  day's date as the event anchor.
* ``profile_cache`` / ``event_cache`` (superseded by the v1 records),
  ``conversations/history.jsonl`` (raw chats already distilled into
  ``event_v1.original_messages``), ``attachments/`` and ``owned_items`` are
  skipped and reported, never imported.

Layering: parsing is stdlib-only. Reuse from
``memory.external_markdown_import`` (limits, injection scan, long-fragment
splitting) is same-package imports, so this module stays importable in unit
tests the same way its sibling is."""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from config import EXTERNAL_IMPORT_MAX_ARCHIVE_BYTES
from config.character_fields import (
    CHARACTER_LEGACY_FLAT_RESERVED_FIELDS,
    CHARACTER_RESERVED_FIELDS,
)
from memory.external_markdown_import import (
    ExternalMemoryImportError,
    MAX_ENTRIES,
    MAX_ENTRY_CHARS,
    MAX_FILE_BYTES,
    MAX_SECTION_CHARS,
    MAX_TOTAL_BYTES,
    _normalised_text,
    _split_long_fragment,
    _suspicious_patterns,
)

#: Molili exports carry an ``attachments/`` tree (user photos + AI drawings)
#: that dwarfs the text payload — the sample archive is ~9 MB with ~8.6 MB of
#: images. The 8 MiB budget therefore applies to *parsed text* only; the zip
#: itself gets this larger cap so users can upload the export as-is.
MAX_ZIP_BYTES = EXTERNAL_IMPORT_MAX_ARCHIVE_BYTES
MAX_ARCHIVE_MEMBERS = 2048
#: Central-directory byte cap: each entry is a 46-byte fixed header + name / extra fields; allow an average of
#: 256 bytes per entry (Molili export paths are short; 2048 entries are ~300 KB). This bounds
#: how many entries zipfile materializes on construction (worst case ~11k tiny entries), not the exact count;
#: the exact 2048 cap is re-checked by raw_member_count after construction.
MAX_CENTRAL_DIRECTORY_BYTES = MAX_ARCHIVE_MEMBERS * 256
MAX_SAMPLED_SKIPPED = 20
#: Cap on card fields split from role_prompt headings; beyond it the whole prompt falls back to a single "设定" field.
MAX_CARD_SECTIONS = 64

# Reserved card fields (silently filtered by the generic edit API) + legacy flat compat fields (such as pngtuber_idle_image,
# which migration folds into _reserved and deletes) + core name fields (would overwrite the name the user chose)
_RESERVED_TAG_NAMES = frozenset((
    *CHARACTER_RESERVED_FIELDS, *CHARACTER_LEGACY_FLAT_RESERVED_FIELDS, "档案名", "昵称",
))

MOLILI_FORMAT = "molili-user-data-export"
MOLILI_SCHEMA_VERSION = 1

_PERSONA_FILE_SECTIONS = {
    "trait_profile.md": "特质人格",
    "interaction_profile.md": "交互习惯",
}
# An archive may hold several personality/<agent>/ directories (multi-character saves). v1 imports one:
# mixing different agents' personas into one neko pool contaminates both, and the commit pipeline has only one
# character name to land on. Extra directories go into the skipped report until a real need appears.
_COGNITIVE_LINE_RE = re.compile(r"^\s*\d+\.\s*(.+?)\s*\(@(\d{4}-\d{2}-\d{2})[\d\s:.]*\)\s*$")
_ROLE_NAME_RE = re.compile(r"^\s*你是(.+?)(?=\s*[（(【\s，,、]|$)", re.MULTILINE)
_SECTION_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
# Emoji / decorative symbol cleanup range: pictographs, misc symbols and dingbats (☀-➿), extended symbols
# (⬀-⯿), regional indicators, variation selectors, ZWJ and keycap. Arrows are excluded (→ etc. carry meaning
# in body text). CJK is unaffected. Range boundary code points were verified with probes (2600/27bf/2b00/2bff).
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "☀-➿"
    "⬀-⯿"
    "\U0001F1E6-\U0001F1FF"
    "️‍⃣"
    "]+"
)


def _strip_emoji(text: str) -> str:
    """Drop emoji/decorative symbol codepoints; CJK and ASCII pass through."""
    return _EMOJI_RE.sub("", text).replace("  ", " ").strip()


def _declared_central_directory(archive_bytes: bytes) -> tuple[int, int] | None:
    """Bounded read of the declared ``(member count, central-directory bytes)``.

    Mirrors how ``zipfile`` locates them (EOCD, overridden by a zip64 end
    record directly before its locator); ``None`` when there is no EOCD."""
    # ``zipfile.ZipFile`` materializes the central directory into ZipInfo objects on construction, so the cap must be
    # prechecked before constructing; otherwise an archive with tens of thousands of members eats CPU/memory before rejection. The member
    # count can be forged (lowered while the directory stays large), but zipfile only reads the directory bytes the EOCD declares,
    # so capping those bytes is what actually bounds the entries materialized on construction; the count is still checked too, and
    # ``raw_member_count`` re-checks the real entries after construction.
    # The EOCD is at most 65535 bytes of ZIP comment + the 22-byte structure; a smaller window would miss the EOCD on
    # legitimate long-comment archives, and the precheck would fall back to finding out after construction.
    tail = archive_bytes[-(65535 + 22):]
    # Same order as zipfile._EndRecData: accept a comment-less EOCD in the trailing 22 bytes first, and only
    # fall back to rfind otherwise. With rfind alone, signature bytes that happen to appear in the EOCD's own offset/size
    # fields would hit that later fake position and return None, skipping the precheck.
    last = archive_bytes[-22:]
    if len(last) == 22 and last[:4] == b"PK\x05\x06" and last[-2:] == b"\x00\x00":
        pos = len(tail) - 22
    else:
        pos = tail.rfind(b"PK\x05\x06")
        if pos < 0 or pos + 22 > len(tail):
            return None
    count = int.from_bytes(tail[pos + 10:pos + 12], "little")
    directory_bytes = int.from_bytes(tail[pos + 12:pos + 16], "little")
    # zip64: the locator (20 bytes) sits right before the EOCD and the zip64 end record (56 bytes) right before the locator;
    # when zipfile sees a locator it uses the record's 8-byte count and directory size instead.
    locator = len(archive_bytes) - len(tail) + pos - 20
    if locator >= 0 and archive_bytes[locator:locator + 4] == b"PK\x06\x07":
        # The project's pinned Python 3.11 assumes the zip64 end record sits right before the locator (it ignores the locator's
        # offset); newer zipfile versions may look it up by offset (the two differ when there is an extensible data sector).
        # Read both and take the larger count / directory size, prechecking the worst case.
        candidates = {locator - 56, int.from_bytes(archive_bytes[locator + 8:locator + 16], "little")}
        for record in candidates:
            if 0 <= record and archive_bytes[record:record + 4] == b"PK\x06\x06":
                count = max(count, int.from_bytes(archive_bytes[record + 32:record + 40], "little"))
                directory_bytes = max(
                    directory_bytes,
                    int.from_bytes(archive_bytes[record + 40:record + 48], "little"),
                )
    return count, directory_bytes


def _central_directory_over_limit(archive_bytes: bytes) -> bool:
    """Whether the declared member count or central-directory size exceeds the caps."""
    declared = _declared_central_directory(archive_bytes)
    if declared is None:
        return False
    count, directory_bytes = declared
    return count > MAX_ARCHIVE_MEMBERS or directory_bytes > MAX_CENTRAL_DIRECTORY_BYTES


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into ``(title, body)`` sections on ATX headings.

    Title is emoji-cleaned; body keeps interior newlines. A preamble before
    the first heading comes back with an empty title (caller assigns the
    fallback tag); empty sections are dropped.
    """
    sections: list[tuple[str, str]] = []
    title = ""
    lines: list[str] = []
    for line in text.splitlines():
        heading = _SECTION_HEADING_RE.match(line)
        if heading:
            if title or any(part.strip() for part in lines):
                sections.append((title, "\n".join(lines).strip()))
            title = _strip_emoji(heading.group(1))
            lines = []
        else:
            lines.append(line)
    if title or any(part.strip() for part in lines):
        sections.append((title, "\n".join(lines).strip()))
    return sections


class _MemberReader:
    """Uniform read view over a zip archive or an inline ``files`` list."""

    def __init__(
        self,
        archive_bytes: bytes | None,
        files: list[dict[str, Any]] | None,
    ) -> None:
        if archive_bytes is not None:
            # Before constructing ZipFile, reject archives whose EOCD-declared members exceed the caps (see
            # _central_directory_over_limit); raw_member_count still re-checks after construction.
            if _central_directory_over_limit(archive_bytes):
                raise ExternalMemoryImportError("Molili archive contains too many files")
            self._archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
        else:
            self._archive = None
        self._files = list(files or [])
        # The cap counts raw members (before path filtering): ZipFile has already materialized every ZipInfo on construction,
        # and filtered-out traversal-shaped members cost memory/CPU just the same.
        self.raw_member_count = (
            len(self._archive.infolist()) if self._archive is not None else len(self._files)
        )
        self.skipped_names: list[str] = []
        # Normalized path -> original ZipInfo: member names may use backslashes such as ``a\b.md``,
        # so reads must use the original entry rather than passing the normalized path back to getinfo().
        self._infos: dict[str, zipfile.ZipInfo] = {}

    def __enter__(self) -> "_MemberReader":
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._archive is not None:
            self._archive.close()

    def names(self) -> list[tuple[str, int]]:
        """Safe (path, byte-size) pairs; unsafe paths are skipped, not fatal."""
        entries: list[tuple[str, int]] = []
        if self._archive is not None:
            for info in self._archive.infolist():
                if info.is_dir():
                    continue
                path = _safe_member_path(info.filename)
                if path is None:
                    self.skipped_names.append(str(info.filename))
                    continue
                self._infos.setdefault(path, info)
                entries.append((path, info.file_size))
        else:
            entries.extend(
                (str(item.get("path") or item.get("name") or ""), 0)
                for item in self._files
            )
        return entries

    def read_text(self, path: str) -> str:
        """Decode one UTF-8 text member; enforce the per-file cap."""
        if self._archive is not None:
            info = self._infos.get(path) or self._archive.getinfo(path)
            if info.file_size > MAX_FILE_BYTES:
                raise ExternalMemoryImportError(f"Molili archive file is too large: {path}")
            with self._archive.open(info, "r") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        else:
            content = next(
                (item.get("content") for item in self._files
                 if str(item.get("path") or item.get("name") or "") == path),
                None,
            )
            if not isinstance(content, str):
                raise ExternalMemoryImportError(f"Molili archive content must be text: {path}")
            data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ExternalMemoryImportError(f"Molili archive file is too large: {path}")
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ExternalMemoryImportError(
                f"Molili archive file is not valid UTF-8: {path}"
            ) from exc


def _safe_member_path(raw: object) -> str | None:
    """Normalise a zip member name; ``None`` for anything path-traversal shaped."""
    path = str(raw or "").replace("\\", "/").strip()
    if path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
        return None
    parts = [part for part in PurePosixPath(path).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _decode_jsonl(text: str, path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExternalMemoryImportError(
                f"Molili archive has a malformed JSONL record: {path} line {line_no}"
            ) from exc
        if not isinstance(record, dict):
            raise ExternalMemoryImportError(
                f"Molili archive JSONL record is not an object: {path} line {line_no}"
            )
        records.append(record)
    return records


def _validate_event_date(raw: object, path: str) -> str | None:
    if not raw:
        return None
    value = str(raw)[:10]
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ExternalMemoryImportError(
            f"Molili archive has an invalid date in {path}: {raw!r}"
        ) from exc
    return value


def _flatten_profile(value: object, prefix: str = "") -> list[str]:
    """Flatten the profile_v1 category tree into reviewable bullet lines."""
    lines: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            lines.extend(_flatten_profile(child, f"{prefix}{key}："))
    elif isinstance(value, list):
        for item in value:
            lines.extend(_flatten_profile(item, prefix))
    elif value is None:
        pass
    else:
        text = str(value).strip()
        if text:
            lines.append(f"- {prefix}{text}" if prefix else f"- {text}")
    return lines


# The identity category key for the portrait in Molili archives is "基础信息" ("础" U+7840), not
# "基本信息" ("本" U+672C). The two differ by one character and are nearly indistinguishable once escaped in a console
# (础 vs 本), which once made the whole identity split silently miss; this constant is the source of truth for the key.
_BASIC_INFO_KEY = "基础信息"


def _identity_fields(identity: object) -> dict[str, str]:
    """Normalize a basic-info payload into flat identity fields.

    Accepts the structured dict form; scalar/list forms carry no usable field
    names and yield an empty dict. Empty values are dropped so a null entry
    never pretends to be identity data."""
    if not isinstance(identity, dict):
        return {}
    return {
        str(field): str(value).strip()
        for field, value in identity.items()
        if isinstance(value, (str, int, float)) and str(value).strip()
    }


def _manifest_count(counts: dict[str, Any], key: str) -> int:
    """Read one display-only manifest count; garbage values count as 0.

    These numbers only feed the skip report, so a malformed count must not
    reject an otherwise valid archive (nor escape as a 500)."""
    value = counts.get(key)
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _candidate_text(section: str, text: str) -> str:
    """Prepend the breadcrumb like the markdown importer does, bounded."""
    section = section[:MAX_SECTION_CHARS]
    if section and section.casefold() not in text.casefold():
        prefix = f"{section}： "
        prefix = prefix[: max(0, MAX_ENTRY_CHARS - len(text))]
        return f"{prefix}{text}"
    return text


def build_molili_candidates(
    archive_bytes: bytes | None = None,
    *,
    files: list[dict[str, Any]] | None = None,
    source_format: str = "auto",
) -> dict[str, Any]:
    """Validate a Molili export and map it onto the shared candidate model.

    ``archive_bytes`` is the raw zip (the normal path); ``files`` mirrors the
    markdown importer's inline mode for tests and tooling. Returns the same
    analysis shape as ``build_import_candidates`` plus Molili-only keys
    (``character_card`` / ``skipped`` / ``snapshot_time``) that preview passes
    through and commit ignores.
    """
    requested = str(source_format or "auto").strip().lower()
    if requested not in {"auto", "molili"}:
        raise ExternalMemoryImportError(
            "Molili import requested with a non-Molili source_format"
        )
    if archive_bytes is not None and len(archive_bytes) > MAX_ZIP_BYTES:
        raise ExternalMemoryImportError("Molili archive upload is too large")

    candidates: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    consumed_files: list[str] = []
    skipped: dict[str, Any] = {
        "conversation_messages": 0,
        "cache_memory_records": 0,
        "owned_items": 0,
        "attachments": {"count": 0, "samples": []},
        "extra_personality_agents": 0,
    }
    seen: set[tuple[str, str, str]] = set()
    snapshot_time = ""

    def add_candidate(
        *, target: str, entity: str, kind: str, section: str,
        text: str, source_file: str, event_date: str | None,
    ) -> None:
        text = text.strip()
        if not text:
            return
        key = (target, entity, _normalised_text(text), event_date or "")
        if key in seen:
            return
        seen.add(key)
        for piece in _split_long_fragment(_candidate_text(section, text)):
            if not piece:
                continue
            hits = _suspicious_patterns(piece)
            if hits:
                warnings.append({"source_file": source_file, "patterns": hits, "text": piece[:160]})
            candidates.append({
                "target": target,
                "entity": entity,
                "kind": kind,
                "text": piece,
                "source_file": source_file,
                "source_section": section[:MAX_SECTION_CHARS],
                "event_date": event_date,
                "warning_patterns": hits,
            })
            if len(candidates) > MAX_ENTRIES:
                raise ExternalMemoryImportError("Molili archive contains too many entries")

    with _MemberReader(archive_bytes, files) as reader:
        if reader.raw_member_count > MAX_ARCHIVE_MEMBERS:
            raise ExternalMemoryImportError("Molili archive contains too many files")
        names = reader.names()
        paths = {path for path, _size in names}

        manifest_path = next((p for p in paths if p.lower() == "manifest.json"), None)
        if manifest_path is None:
            raise ExternalMemoryImportError(
                "Not a Molili archive: no manifest.json found"
            )
        try:
            manifest = json.loads(reader.read_text(manifest_path))
        except json.JSONDecodeError as exc:
            raise ExternalMemoryImportError(
                f"Molili archive has a malformed manifest.json: {exc}"
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("format") != MOLILI_FORMAT:
            raise ExternalMemoryImportError(
                "Not a Molili archive: manifest format mismatch"
            )
        if manifest.get("schema_version") != MOLILI_SCHEMA_VERSION:
            raise ExternalMemoryImportError(
                "Unsupported Molili archive schema_version"
            )
        snapshot_time = str(manifest.get("snapshot_time") or "")
        consumed_files.append(manifest_path)
        # The real skip counts come from the manifest (we count files/members, not messages)
        manifest_counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
        skipped["conversation_messages"] = _manifest_count(manifest_counts, "conversation_messages")
        skipped["cache_memory_records"] = _manifest_count(manifest_counts, "cached_memory_records")
        skipped["owned_items"] = _manifest_count(manifest_counts, "owned_items")

        # ── Classify members: attachments and conversations never enter the pool, only the report ──
        agent_dirs = sorted({
            path.split("/")[1] for path in paths
            if path.startswith("personality/") and len(path.split("/")) > 2
        })
        if not agent_dirs:
            raise ExternalMemoryImportError("Molili archive has no personality directory")

        text_budget = 0

        def read_budgeted(path: str) -> str:
            nonlocal text_budget
            text = reader.read_text(path)
            text_budget += len(text.encode("utf-8"))
            if text_budget > MAX_TOTAL_BYTES:
                raise ExternalMemoryImportError("Molili archive payload is too large")
            consumed_files.append(path)
            return text

        for path, size in names:
            if path.startswith("attachments/"):
                skipped["attachments"]["count"] += 1
                if len(skipped["attachments"]["samples"]) < MAX_SAMPLED_SKIPPED:
                    skipped["attachments"]["samples"].append(path)

        # ── Persona: role_prompt goes to the character card, the rest into the neko persona ──
        agent = agent_dirs[0]
        skipped["extra_personality_agents"] = max(0, len(agent_dirs) - 1)
        personality_files = sorted(
            p for p in paths
            if p.startswith(f"personality/{agent}/") and p.endswith(".md")
        )
        role_prompt_path = next(
            (p for p in personality_files if p.endswith("/role_prompt.md")), None
        )
        system_prompt = ""
        if role_prompt_path is not None:
            system_prompt = _strip_emoji(read_budgeted(role_prompt_path).strip())
        suggested_name = None
        name_match = _ROLE_NAME_RE.search(system_prompt)
        if name_match:
            suggested_name = name_match.group(1).strip() or None

        # Split on markdown headings: one custom card field per section (tag = heading), instead of
        # stuffing everything into "设定". Bodies and headings are emoji-cleaned alike. Degenerate input without headings falls back to
        # a single "设定" field, keeping the old behavior usable.
        # Section headings avoid reserved card field names (every reserved field is silently filtered by POST/PUT,
        # and 档案名/昵称 would overwrite the name the user chose). Collisions get a "Molili " prefix.
        card_sections: list[dict[str, str]] = []
        master_profile: dict[str, str] = {}
        used_tags: set[str] = set()
        next_suffix: dict[str, int] = {}
        sections = _split_markdown_sections(system_prompt)
        if len(sections) > MAX_CARD_SECTIONS:
            # So many headings that it does not look like a normal setting (each section becomes a card field): fall back to a single
            # "设定" field; the full text is still kept intact.
            sections = [("", system_prompt)] if system_prompt else []
        for sec_title, sec_body in sections:
            tag = (sec_title or "设定").strip()[:60]
            if tag in _RESERVED_TAG_NAMES:
                tag = f"Molili {tag}"
            base_tag = tag
            # Remember the next free suffix per base name, so repeated headings no longer probe one by one from 2
            counter = next_suffix.get(base_tag, 1)
            while tag in used_tags:
                counter += 1
                tag = f"{base_tag} {counter}"
            next_suffix[base_tag] = counter
            used_tags.add(tag)
            card_sections.append({"tag": tag, "text": sec_body})

        for path in personality_files:
            if path == role_prompt_path:
                continue
            basename = path.rsplit("/", 1)[-1]
            text = read_budgeted(path)
            if basename in _PERSONA_FILE_SECTIONS:
                add_candidate(
                    target="persona", entity="neko", kind="persona",
                    section=_PERSONA_FILE_SECTIONS[basename], text=text,
                    source_file=path, event_date=None,
                )
            elif basename == "cognitive.md":
                for line in text.splitlines():
                    match = _COGNITIVE_LINE_RE.match(line)
                    body = match.group(1) if match else line.strip()
                    event_date = None
                    if match:
                        try:
                            event_date = date.fromisoformat(match.group(2)).isoformat()
                        except ValueError:
                            event_date = None
                    elif not line.strip():
                        continue
                    add_candidate(
                        target="persona", entity="neko", kind="persona",
                        section="重要设定", text=body,
                        source_file=path, event_date=event_date,
                    )

        # ── Long-term memory: profile_v1 -> master persona; event_v1 -> per-day daily ──
        long_term_path = next((p for p in paths if p == "memory/long_term.jsonl"), None)
        daily_order: list[str] = []
        daily_fragments: dict[str, list[tuple[str, str]]] = {}

        def add_daily(date_value: str, section: str, text: str) -> None:
            source_file = f"molili/memory/{date_value}.md"
            if source_file not in daily_fragments:
                daily_fragments[source_file] = []
                daily_order.append(source_file)
            daily_fragments[source_file].append((section, text))

        if long_term_path is not None:
            for record in _decode_jsonl(read_budgeted(long_term_path), long_term_path):
                memory_type = str(record.get("memory_type") or "")
                if memory_type in {"profile_cache", "event_cache"}:
                    # Counts were already taken from manifest.counts; this only skips. The one exception:
                    # the volc profile_v1 often has a null basic-info section, and the identity fields survive only in
                    # profile_cache (structured {"昵称": ...}), so recover them from there.
                    if memory_type == "profile_cache" and not master_profile:
                        cached_content = record.get("content")
                        try:
                            cached = (
                                json.loads(str(cached_content)) if cached_content else None
                            )
                        except json.JSONDecodeError:
                            cached = None
                        if isinstance(cached, dict):
                            master_profile = _identity_fields(
                                cached.get(_BASIC_INFO_KEY)
                            )
                    continue
                if memory_type == "profile_v1":
                    # In real archives the portrait hangs off memory_info.user_profile (a JSON string);
                    # the older cache variant keeps the same structure in top-level content, so accept both locations
                    info = record.get("memory_info")
                    raw_profile: object = None
                    if isinstance(info, dict) and info.get("user_profile"):
                        raw_profile = info["user_profile"]
                    elif record.get("content"):
                        raw_profile = record["content"]
                    if isinstance(raw_profile, str):
                        try:
                            profile = json.loads(raw_profile)
                        except json.JSONDecodeError as exc:
                            raise ExternalMemoryImportError(
                                "Molili archive has a malformed profile_v1 payload"
                            ) from exc
                    else:
                        profile = raw_profile
                    if not isinstance(profile, dict):
                        raise ExternalMemoryImportError(
                            "Molili archive has a malformed profile_v1 payload"
                        )
                    # Identity fields (basic info such as the nickname) are not memory candidates: N.E.K.O's master card is
                    # global, and identity claims among memory candidates would conflict with the global card and be rejected wholesale
                    # by the persona fusion guard. Split them out and let the caller decide where they go (private setting or global card);
                    # the changed candidate set also yields a new fusion fingerprint, so a previously rejected portrait really retries on re-import.
                    # Note: in real volc archives the profile_v1 basic info is null; identity comes from
                    # profile_cache's structured fields (see the cache branch above),
                    # and v1 only overrides when it carries the fields itself.
                    identity = profile.get(_BASIC_INFO_KEY)
                    master_profile = _identity_fields(identity) or master_profile
                    portrait = {
                        key: value for key, value in profile.items()
                        if key != _BASIC_INFO_KEY
                    }
                    if portrait:
                        add_candidate(
                            target="persona", entity="master", kind="profile",
                            section="主人画像", text="\n".join(_flatten_profile(portrait)),
                            source_file=long_term_path, event_date=None,
                        )
                elif memory_type == "event_v1":
                    event_date = _validate_event_date(
                        record.get("time_iso"), long_term_path
                    )
                    info = record.get("memory_info")
                    summary = str(info.get("summary") or "").strip() if isinstance(info, dict) else ""
                    if event_date and summary:
                        add_daily(event_date, "记忆", summary)

        # ── Diaries: merged with same-day events into one daily file, saving an extraction call ──
        for path in sorted(p for p in paths if p.startswith("diaries/") and p.endswith(".jsonl")):
            for record in _decode_jsonl(read_budgeted(path), path):
                event_date = _validate_event_date(record.get("date"), path)
                content = str(record.get("content") or "").strip()
                if event_date and content:
                    add_daily(event_date, "日记", content)

        for source_file in daily_order:
            for section, text in daily_fragments[source_file]:
                add_candidate(
                    target="facts", entity="master", kind="daily",
                    section=section, text=text,
                    source_file=source_file, event_date=source_file.split("/")[-1][:10],
                )

        for name in reader.skipped_names:
            skipped["attachments"]["count"] += 1
            if len(skipped["attachments"]["samples"]) < MAX_SAMPLED_SKIPPED:
                skipped["attachments"]["samples"].append(name)

    return {
        "source_format": "molili",
        "files": consumed_files,
        "candidates": candidates,
        "warnings": warnings,
        "character_card": {
            "suggested_name": suggested_name,
            "system_prompt": system_prompt,
            "sections": card_sections,
        },
        "master_profile": master_profile,
        "skipped": skipped,
        "snapshot_time": snapshot_time,
    }


def detect_molili_archive(archive_bytes: bytes) -> bool:
    """Sniff whether a zip is a Molili user-data export (manifest format tag)."""
    try:
        # Constructing ZipFile materializes the whole central directory: archives clearly over the member caps are judged
        # not Molili and never constructed (explicit molili mode gets a clear error message instead).
        if _central_directory_over_limit(archive_bytes):
            return False
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            info = next(
                (i for i in archive.infolist()
                 if not i.is_dir() and str(i.filename).lower() == "manifest.json"),
                None,
            )
            if info is None or info.file_size > MAX_FILE_BYTES:
                return False
            # Same approach as _MemberReader.read_text: a bounded stream read that does not trust the central directory's declared size
            with archive.open(info, "r") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                return False
            manifest = json.loads(data.decode("utf-8-sig"))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, UnicodeDecodeError, ValueError, OSError):
        return False
    return isinstance(manifest, dict) and manifest.get("format") == MOLILI_FORMAT
