"""Import a user-provided Geng Guide Markdown export into public knowledge."""

from __future__ import annotations

import re

from ..filters import normalize_search_text, sanitize_external_text
from ..models import KnowledgeEntry


GENG_GUIDE_SOURCE_URL = "local-import://geng-guide-output.md"
GENG_GUIDE_LICENSE = "User-provided 梗指南 export; license not stated"

_BLOCK_RE = re.compile(
    r"(?ms)^\d{1,2}:\d{2}\s*$\n(?P<body>.*?)(?=^\d{1,2}:\d{2}\s*$\n|\Z)"
)
_SUMMARY_HEADINGS = ("Summary", "摘要", "概要")
_HIGHLIGHT_HEADINGS = ("Highlights", "亮点", "精华")
_QUESTION_HEADINGS = ("Questions", "问题")
_TITLE_SUFFIX_RE = re.compile(r"(?:是什么梗|是什么意思|是什么东西|是什么)$")
_GUIDE_LABEL_RE = re.compile(r"[【\[]?梗指南[】\]]?")
_TAG_RE = re.compile(r"#([^\s#]+)")
_GENERIC_TAGS = frozenset({"梗", "网络梗", "网络热词", "聊天", "游戏", "创作", "视频"})


def load_geng_guide_markdown(raw: bytes) -> tuple[KnowledgeEntry, ...]:
    """Parse the trusted local export without importing its question prompts."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("geng guide export must be UTF-8") from exc

    entries: list[KnowledgeEntry] = []
    seen_terms: set[str] = set()
    for block_match in _BLOCK_RE.finditer(text):
        entry = _entry_from_block(block_match.group("body"))
        if entry is None:
            continue
        # The export sometimes repeats a guide card.  Preserve one stable entry
        # per displayed term rather than making duplicate automatic matches.
        normalized_title = normalize_search_text(entry.title)
        if normalized_title in seen_terms:
            continue
        seen_terms.add(normalized_title)
        entries.append(entry)
    if not entries:
        raise ValueError("geng guide export contains no usable entries")
    return tuple(entries)


def _entry_from_block(block: str) -> KnowledgeEntry | None:
    lines = [line.strip() for line in block.splitlines()]
    title_line = next((line for line in lines if line), "")
    title = _normalize_title(title_line)
    if len(normalize_search_text(title)) < 2:
        return None

    summary = _section(block, _SUMMARY_HEADINGS, _HIGHLIGHT_HEADINGS + _QUESTION_HEADINGS)
    highlights = _section(block, _HIGHLIGHT_HEADINGS, _QUESTION_HEADINGS)
    if not summary:
        return None
    content_parts = [f"含义：{summary}"]
    if highlights:
        content_parts.append(f"要点：{highlights}")
    content = "\n\n".join(content_parts)
    return KnowledgeEntry(
        title=title,
        terms={"alias": (), "recognition": ()},
        # The export has no reliable taxonomy.  Do not invent one, because the
        # response posture must stay source-backed rather than stereotype every
        # entry as self-deprecation or a social phenomenon.
        tags=("source:geng-guide", "scope:public", *_guide_tags(block)),
        content=content,
        summary=summary,
    )


def _normalize_title(value: str) -> str:
    title = _GUIDE_LABEL_RE.sub("", value).strip()
    return _TITLE_SUFFIX_RE.sub("", title).strip(" ：:？?。")


def _section(block: str, headings: tuple[str, ...], stop_headings: tuple[str, ...]) -> str:
    lines = block.splitlines()
    start = next((index + 1 for index, line in enumerate(lines) if line.strip() in headings), None)
    if start is None:
        return ""
    selected: list[str] = []
    for line in lines[start:]:
        if line.strip() in stop_headings:
            break
        clean = line.strip()
        if clean:
            selected.append(clean)
    return sanitize_external_text("\n".join(selected), max_chars=2_000)


def _guide_tags(block: str) -> tuple[str, ...]:
    """Topic tags are not aliases and must never trigger automatic cards."""
    tags: list[str] = []
    for match in _TAG_RE.finditer(block):
        tag = sanitize_external_text(match.group(1), max_chars=120)
        normalized = normalize_search_text(tag)
        if len(normalized) < 2 or tag in _GENERIC_TAGS:
            continue
        tags.append(f"topic:{tag}")
    return tuple(dict.fromkeys(tags))
