"""Imports user Markdown/TXT into the tactical document store.

Front matter is parsed by hand rather than with `yaml.safe_load`. The whitelist
only ever admits strings and lists of strings, so a YAML parser would add a
deserialization surface for no benefit -- and "safe front matter" is easier to
guarantee when the parser physically cannot construct anything else.

A rejected document is rejected whole. Silently dropping an unrecognised key
would leave the user believing a tag is active when it is not.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .store import TAG_KINDS, KnowledgeStore
from .tokenize import term_frequencies

SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt")

FRONT_MATTER_FENCE = "---"
MAX_FRONT_MATTER_CHARS = 4096
MAX_TAGS_PER_DOCUMENT = 64
MAX_TAG_CHARS = 64

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_LIST_RE = re.compile(r"^\[(.*)\]$")


class DocumentRejected(Exception):
    """The document cannot be imported; the message is shown to the user."""


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str
    terms: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    tags: dict[str, tuple[str, ...]]
    chunks: tuple[Chunk, ...]
    sha256: str
    size_bytes: int

    @property
    def tag_count(self) -> int:
        return sum(len(values) for values in self.tags.values())


# --- front matter --------------------------------------------------------

def split_front_matter(text: str) -> tuple[str, str]:
    """Returns `(front_matter_body, remaining_text)`; front matter may be empty."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != FRONT_MATTER_FENCE:
        return "", text
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONT_MATTER_FENCE:
            block = "".join(lines[1:index])
            if len(block) > MAX_FRONT_MATTER_CHARS:
                raise DocumentRejected(
                    f"front matter 超过 {MAX_FRONT_MATTER_CHARS} 字符")
            return block, "".join(lines[index + 1:])
    # An unterminated fence is far more likely to be a horizontal rule than a
    # broken header, so treat the whole file as body.
    return "", text


def _clean_tag(value: str) -> str:
    cleaned = unicodedata.normalize("NFKC", str(value)).strip().strip("\"'")
    if len(cleaned) > MAX_TAG_CHARS:
        raise DocumentRejected(f"标签 {cleaned[:20]}... 超过 {MAX_TAG_CHARS} 字符")
    return cleaned


def _parse_values(raw: str) -> list[str]:
    inline = _INLINE_LIST_RE.match(raw.strip())
    body = inline.group(1) if inline is not None else raw
    values = [_clean_tag(part) for part in body.split(",")]
    return [value for value in values if value]


def parse_front_matter(block: str) -> dict[str, tuple[str, ...]]:
    """Parse the whitelisted tag keys, rejecting anything else outright."""
    tags: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        stripped = line.strip()
        if stripped.startswith("- "):
            if current is None:
                raise DocumentRejected("front matter 里出现了不属于任何键的列表项")
            tags.setdefault(current, []).extend(_parse_values(stripped[2:]))
            continue

        if ":" not in line:
            raise DocumentRejected(f"front matter 行无法解析：{stripped[:40]}")
        # A leading space before a key means a nested mapping, which the tag
        # model has no representation for.
        if line[:1].isspace():
            raise DocumentRejected("front matter 不支持嵌套结构")

        key, _, value = line.partition(":")
        key = key.strip()
        if key not in TAG_KINDS:
            raise DocumentRejected(
                f"front matter 只允许 {', '.join(TAG_KINDS)}，收到了 {key!r}")
        current = key
        tags.setdefault(key, [])
        if value.strip():
            tags[key].extend(_parse_values(value))

    resolved: dict[str, tuple[str, ...]] = {}
    total = 0
    for kind, values in tags.items():
        unique = tuple(dict.fromkeys(value for value in values if value))
        if not unique:
            continue
        total += len(unique)
        resolved[kind] = unique
    if total > MAX_TAGS_PER_DOCUMENT:
        raise DocumentRejected(f"标签总数超过 {MAX_TAGS_PER_DOCUMENT}")
    return resolved


# --- chunking ------------------------------------------------------------

def _sections(body: str) -> list[tuple[str, str]]:
    """Split into `(heading_breadcrumb, section_text)` pairs.

    Headings inside fenced code blocks are text, not structure.
    """
    sections: list[tuple[str, str]] = []
    trail: list[str] = []
    buffer: list[str] = []
    heading = ""
    fenced = False

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            sections.append((heading, text))
        buffer.clear()

    for line in body.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            buffer.append(line)
            continue
        match = None if fenced else _HEADING_RE.match(line)
        if match is None:
            buffer.append(line)
            continue
        flush()
        depth = len(match.group(1))
        title = match.group(2).strip()
        del trail[depth - 1:]
        trail.append(title)
        heading = " / ".join(part for part in trail if part)
    flush()
    return sections


def _split_paragraph(text: str, size: int, overlap: int) -> list[str]:
    """Hard-split an oversized paragraph, preferring sentence boundaries."""
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            window = text[start:end]
            boundary = max(
                window.rfind("。"), window.rfind("！"), window.rfind("？"),
                window.rfind(". "), window.rfind("\n"),
            )
            if boundary > size // 2:
                end = start + boundary + 1
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [piece for piece in pieces if piece]


def chunk_body(body: str, *, size: int, overlap: int) -> list[tuple[str, str]]:
    """Returns `(heading, chunk_text)` pairs of at most `size` characters."""
    size = max(120, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    out: list[tuple[str, str]] = []

    for heading, section in _sections(body):
        paragraphs = [
            part.strip() for part in re.split(r"\n\s*\n", section) if part.strip()
        ]
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > size:
                if current:
                    out.append((heading, current))
                    current = ""
                for piece in _split_paragraph(paragraph, size, overlap):
                    out.append((heading, piece))
                continue
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= size:
                current = candidate
                continue
            out.append((heading, current))
            # Carry the tail forward so a fact split across the seam is still
            # retrievable from either side.
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
        if current:
            out.append((heading, current))
    return out


# --- import --------------------------------------------------------------

def parse_document(name: str, raw_text: str, *, size: int, overlap: int) -> ParsedDocument:
    text = str(raw_text or "")
    if not text.strip():
        raise DocumentRejected("文件是空的")

    front_matter, body = split_front_matter(text)
    tags = parse_front_matter(front_matter) if front_matter.strip() else {}
    body = unicodedata.normalize("NFKC", body)

    pairs = chunk_body(body, size=size, overlap=overlap)
    if not pairs:
        raise DocumentRejected("正文没有可用内容")

    chunks = tuple(
        Chunk(heading=heading, text=chunk_text,
              terms=dict(term_frequencies(f"{heading}\n{chunk_text}")))
        for heading, chunk_text in pairs
    )
    return ParsedDocument(
        title=_title_for(name, body),
        tags=tags,
        chunks=chunks,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        size_bytes=len(text.encode("utf-8")),
    )


def _title_for(name: str, body: str) -> str:
    """Prefer the first heading; fall back to the file stem.

    Only the stem is kept -- never the directory -- so importing from a private
    folder does not put that path in the database.
    """
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match is not None and match.group(2).strip():
            return match.group(2).strip()[:120]
    stem = Path(str(name or "document")).stem
    return (stem or "document")[:120]


def _oversize_message(limit_bytes: int) -> str:
    return f"单个文件超过 {limit_bytes // (1024 * 1024)} MiB"


class DocumentImporter:
    """Applies quotas and dedup, then writes through to the store."""

    def __init__(self, store: KnowledgeStore, cfg) -> None:
        self.store = store
        self.cfg = cfg

    def apply_config(self, cfg) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------
    def import_text(self, name: str, raw_text: str) -> dict[str, Any]:
        cfg = self.cfg
        encoded = len(str(raw_text or "").encode("utf-8"))
        if encoded > cfg.tactics_max_file_bytes:
            raise DocumentRejected(_oversize_message(cfg.tactics_max_file_bytes))

        stats = self.store.stats()
        if stats["documents"] >= cfg.tactics_max_documents:
            raise DocumentRejected(f"文档数量已达上限 {cfg.tactics_max_documents}")
        if stats["total_bytes"] + encoded > cfg.tactics_max_total_bytes:
            raise DocumentRejected(
                f"总量会超过 {cfg.tactics_max_total_bytes // (1024 * 1024)} MiB")

        document = parse_document(
            name, raw_text,
            size=cfg.tactics_chunk_chars, overlap=cfg.tactics_chunk_overlap)

        existing = self.store.has_hash(document.sha256)
        if existing is not None:
            return {
                "status": "duplicate",
                "doc_id": existing,
                "title": document.title,
            }

        budget = max(0, cfg.tactics_index_chunk_cap - self.store.index_capacity_used())
        doc_id = self.store.add_document(
            title=document.title,
            sha256=document.sha256,
            size_bytes=document.size_bytes,
            tags=document.tags,
            chunks=[
                {"heading": chunk.heading, "text": chunk.text, "terms": chunk.terms}
                for chunk in document.chunks
            ],
            index_budget=budget,
        )
        indexed = min(budget, len(document.chunks))
        return {
            "status": "imported",
            "doc_id": doc_id,
            "title": document.title,
            "chunks": len(document.chunks),
            "indexed_chunks": indexed,
            "tags": {kind: list(values) for kind, values in document.tags.items()},
            # Surfaced so the panel can say ranking degraded rather than
            # letting it look like the import silently half-worked.
            "index_truncated": indexed < len(document.chunks),
        }

    def import_paths(self, paths: Sequence[str]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for raw_path in paths:
            path = Path(str(raw_path))
            entry: dict[str, Any] = {"name": path.name}
            try:
                if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    raise DocumentRejected(
                        f"只支持 {', '.join(SUPPORTED_SUFFIXES)}")
                # On disk the size is knowable without reading: enforcing the
                # quota here is the difference between refusing a huge file and
                # pulling it into memory first only to refuse it.
                if path.stat().st_size > self.cfg.tactics_max_file_bytes:
                    raise DocumentRejected(
                        _oversize_message(self.cfg.tactics_max_file_bytes))
                text = path.read_text(encoding="utf-8")
            except DocumentRejected as exc:
                entry.update({"status": "rejected", "error": str(exc)})
                results.append(entry)
                continue
            except UnicodeDecodeError:
                entry.update({"status": "rejected", "error": "文件不是 UTF-8 编码"})
                results.append(entry)
                continue
            except OSError as exc:
                entry.update({"status": "rejected", "error": f"读取失败：{exc.strerror}"})
                results.append(entry)
                continue

            try:
                entry.update(self.import_text(path.name, text))
            except DocumentRejected as exc:
                entry.update({"status": "rejected", "error": str(exc)})
            results.append(entry)
        return _summarize(results)


def _summarize(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for entry in results:
        status = str(entry.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {"results": list(results), "counts": counts}


__all__ = [
    "MAX_FRONT_MATTER_CHARS",
    "MAX_TAGS_PER_DOCUMENT",
    "MAX_TAG_CHARS",
    "SUPPORTED_SUFFIXES",
    "Chunk",
    "DocumentImporter",
    "DocumentRejected",
    "ParsedDocument",
    "chunk_body",
    "parse_document",
    "parse_front_matter",
    "split_front_matter",
]
