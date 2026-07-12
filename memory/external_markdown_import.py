# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Import OpenClaw/Hermes Markdown memory into N.E.K.O's JSON stores.

The external formats are intentionally treated as *data*.  The importer never
executes instructions found in Markdown and never calls an LLM.  It performs a
deterministic, reviewable mapping:

* ``USER.md`` -> budgeted ``master`` persona entries
* ``SOUL.md`` -> budgeted ``neko`` persona entries
* ``MEMORY.md`` and ``memory/YYYY-MM-DD[-slug].md`` -> atomic facts

Both projects use free-form Markdown, so parsing is deliberately permissive:
headings provide a breadcrumb, list items become individual entries, ordinary
paragraphs stay intact, and Hermes' section-sign delimiter is supported.
"""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Any, Iterable

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2048
MAX_ENTRIES = 1000
MAX_ENTRY_CHARS = 8000

_SUPPORTED_ROOT_NAMES = frozenset({"USER.MD", "SOUL.MD", "MEMORY.MD"})
_DAILY_RE = re.compile(r"^memory/(\d{4}-\d{2}-\d{2})(?:-[^/]+)?\.md$", re.IGNORECASE)
_DAILY_BASENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-[^/]+)?\.md$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_RE = re.compile(r"^\s{0,3}(?:[-+*]|\d+[.)])\s+(.+?)\s*$")
_FIELD_RE = re.compile(r"^\*{0,2}([^:*]{1,80})\*{0,2}\s*:\s*(.+)$")
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("role_marker", re.compile(r"(?im)^\s*(?:system|developer|assistant|user)\s*[:：]")),
    ("chatml_token", re.compile(r"<\|(?:im_start|im_end|endoftext)\|>", re.IGNORECASE)),
    ("ignore_previous", re.compile(r"\b(?:ignore|disregard)\b.{0,40}\b(?:previous|prior|above)\b.{0,30}\b(?:instructions?|prompts?|rules?)\b", re.IGNORECASE)),
    ("ignore_previous_zh", re.compile(r"(?:忽略|无视|不要理会)(?:以上|上述|之前).{0,12}(?:指令|提示|规则|设定)")),
)


class ExternalMemoryImportError(ValueError):
    """Caller-facing invalid external-memory payload."""


@dataclass(frozen=True)
class MarkdownSourceFile:
    path: str
    content: str


def _normalise_path(raw: str) -> str:
    path = str(raw or "").replace("\\", "/").strip()
    if path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
        raise ExternalMemoryImportError(f"Unsafe Markdown path: {raw!r}")
    parts = [part for part in PurePosixPath(path).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ExternalMemoryImportError(f"Unsafe Markdown path: {raw!r}")
    return "/".join(parts)


def _classify_path(path: str) -> tuple[str, str | None] | None:
    """Return ``(kind, event_date)`` for a supported workspace file."""
    normalised = _normalise_path(path)
    parts = normalised.split("/")
    basename = parts[-1]
    upper = basename.upper()
    if upper in _SUPPORTED_ROOT_NAMES:
        return upper.removesuffix(".MD").lower(), None

    # Workspaces/profiles are commonly wrapped in one or more archive folders.
    lower = normalised.lower()
    marker = lower.rfind("/memory/")
    relative = normalised[marker + 1:] if marker >= 0 else normalised
    match = _DAILY_RE.match(relative)
    if match is None and len(parts) == 1:
        match = _DAILY_BASENAME_RE.match(basename)
    if match:
        return "daily", match.group(1)
    return None


def _decode_markdown(data: bytes, path: str) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise ExternalMemoryImportError(f"Markdown file is too large: {path}")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExternalMemoryImportError(f"Markdown file is not valid UTF-8: {path}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def collect_markdown_files(
    files: Iterable[dict[str, Any]] | None = None,
    *,
    archive_bytes: bytes | None = None,
) -> list[MarkdownSourceFile]:
    """Validate direct files or a ZIP and return supported Markdown files."""
    collected: list[MarkdownSourceFile] = []
    total_bytes = 0

    if archive_bytes is not None:
        if len(archive_bytes) > MAX_TOTAL_BYTES:
            raise ExternalMemoryImportError("Archive upload is too large")
        try:
            archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
        except zipfile.BadZipFile as exc:
            raise ExternalMemoryImportError("Invalid ZIP archive") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ExternalMemoryImportError("ZIP archive contains too many files")
            for info in infos:
                if info.is_dir():
                    continue
                path = _normalise_path(info.filename)
                if _classify_path(path) is None:
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    raise ExternalMemoryImportError(f"Markdown file is too large: {path}")
                with archive.open(info, "r") as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                total_bytes += len(data)
                if total_bytes > MAX_TOTAL_BYTES:
                    raise ExternalMemoryImportError("Markdown payload is too large")
                collected.append(MarkdownSourceFile(path, _decode_markdown(data, path)))
    else:
        for item in files or []:
            if not isinstance(item, dict):
                raise ExternalMemoryImportError("Each file must be an object")
            path = _normalise_path(str(item.get("path") or item.get("name") or ""))
            if _classify_path(path) is None:
                continue
            content = item.get("content")
            if not isinstance(content, str):
                raise ExternalMemoryImportError(f"Markdown content must be text: {path}")
            data = content.encode("utf-8")
            total_bytes += len(data)
            if len(data) > MAX_FILE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise ExternalMemoryImportError("Markdown payload is too large")
            collected.append(MarkdownSourceFile(path, content.replace("\r\n", "\n").replace("\r", "\n")))

    if not collected:
        raise ExternalMemoryImportError(
            "No supported USER.md, SOUL.md, MEMORY.md, or memory/YYYY-MM-DD*.md files found"
        )
    return collected


def detect_source_format(files: Iterable[MarkdownSourceFile], requested: str = "auto") -> str:
    requested = str(requested or "auto").strip().lower()
    if requested in {"openclaw", "hermes"}:
        return requested
    if requested != "auto":
        raise ExternalMemoryImportError("source_format must be auto, openclaw, or hermes")
    paths = [item.path.lower() for item in files]
    contents = "\n".join(item.content[:5000] for item in files)
    if any(".hermes" in path or "/memories/" in path for path in paths) or "\n§\n" in contents:
        return "hermes"
    return "openclaw"


def _clean_fragment(text: str) -> str:
    text = text.strip()
    field = _FIELD_RE.match(text)
    if field:
        text = f"{field.group(1).strip()}: {field.group(2).strip()}"
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _split_long_fragment(text: str) -> list[str]:
    if len(text) <= MAX_ENTRY_CHARS:
        return [text]
    out: list[str] = []
    remaining = text
    while remaining:
        cut = min(MAX_ENTRY_CHARS, len(remaining))
        if cut < len(remaining):
            boundary = max(remaining.rfind("\n", 0, cut), remaining.rfind(". ", 0, cut))
            if boundary >= MAX_ENTRY_CHARS // 2:
                cut = boundary + 1
        out.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [item for item in out if item]


def split_markdown_entries(text: str, *, hermes_delimiter: bool = False) -> list[dict[str, str]]:
    """Split free Markdown into ``{section, text}`` entries."""
    if hermes_delimiter and re.search(r"(?m)^\s*§\s*$", text):
        blocks = re.split(r"(?m)^\s*§\s*$", text)
        return [
            {"section": "", "text": piece}
            for block in blocks
            for piece in _split_long_fragment(_clean_fragment(block))
            if piece
        ]

    entries: list[dict[str, str]] = []
    headings: list[str] = []
    paragraph: list[str] = []
    fenced = False

    def flush() -> None:
        if not paragraph:
            return
        raw = _clean_fragment("\n".join(paragraph))
        paragraph.clear()
        for piece in _split_long_fragment(raw):
            if piece:
                entries.append({"section": " / ".join(headings), "text": piece})

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if not fenced:
            heading = _HEADING_RE.match(line)
            if heading:
                flush()
                level = len(heading.group(1))
                headings[:] = headings[: level - 1]
                headings.append(_clean_fragment(heading.group(2)))
                continue
            item = _LIST_RE.match(line)
            if item:
                flush()
                cleaned = _clean_fragment(item.group(1))
                if cleaned:
                    entries.append({"section": " / ".join(headings), "text": cleaned})
                continue
            if not line.strip():
                flush()
                continue
        paragraph.append(line)
    flush()
    return entries


def _candidate_text(section: str, text: str) -> str:
    text = _clean_fragment(text)
    if section and section.casefold() not in text.casefold():
        return f"{section}: {text}"
    return text


def _normalised_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _suspicious_patterns(text: str) -> list[str]:
    return [pattern_id for pattern_id, pattern in _INJECTION_PATTERNS if pattern.search(text)]


def build_import_candidates(
    files: Iterable[MarkdownSourceFile],
    *,
    source_format: str = "auto",
) -> dict[str, Any]:
    source_files = list(files)
    detected = detect_source_format(source_files, source_format)
    candidates: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for source in source_files:
        classified = _classify_path(source.path)
        if classified is None:
            continue
        kind, event_date = classified
        if event_date:
            try:
                date.fromisoformat(event_date)
            except ValueError as exc:
                raise ExternalMemoryImportError(
                    f"Invalid daily-memory date in path: {source.path}"
                ) from exc
        fragments = split_markdown_entries(
            source.content,
            hermes_delimiter=(detected == "hermes" and kind in {"user", "memory"}),
        )
        for fragment in fragments:
            text = _candidate_text(fragment["section"], fragment["text"])
            if not text:
                continue
            if kind == "soul":
                target, entity = "persona", "neko"
            elif kind == "user":
                target, entity = "persona", "master"
            else:
                target, entity = "facts", "master"
            key = (target, entity, _normalised_text(text))
            if key in seen:
                continue
            seen.add(key)
            hits = _suspicious_patterns(text)
            if hits:
                warnings.append({"source_file": source.path, "patterns": hits, "text": text[:160]})
            candidates.append({
                "target": target,
                "entity": entity,
                "text": text,
                "kind": kind,
                "source_file": source.path,
                "source_section": fragment["section"],
                "event_date": event_date,
                "warning_patterns": hits,
            })
            if len(candidates) > MAX_ENTRIES:
                raise ExternalMemoryImportError("External memory contains too many entries")

    if not candidates:
        raise ExternalMemoryImportError("Supported Markdown files did not contain importable text")
    return {
        "source_format": detected,
        "files": [item.path for item in source_files],
        "candidates": candidates,
        "warnings": warnings,
    }
