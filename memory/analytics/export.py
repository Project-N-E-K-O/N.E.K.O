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

"""Memory archive ZIP exporter (user keepsake & redacted developer diagnostics).

Two explicit export modes:
- ``keepsake``: Warm human-readable ``OUR_MEMORIES.md`` plus raw JSON copies of
  long-term memory files for personal backup.
- ``diagnostics``: Redacted technical bundle (``overview.json``, ``star_map.json``,
  ``lineage_graph.json``, ``manifest.json``) with regex PII/secret scrubbing and
  zero raw conversation transcripts (never includes ``recent.json`` or
  ``time_indexed.db``).
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager

from .imprint import build_memory_overview
from .lineage import build_full_lineage_graph
from .star_map import build_star_map

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # OpenAI / Anthropic / Google / generic bearer/Basic auth & API keys
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\b((?:authorization\s*:\s*)?(?:bearer|basic)\s+)[^\s,\"']+"), r"\1[REDACTED_TOKEN]"),
    # Quoted or unquoted simple/compound credential keys (e.g. "token": "...", access_token=..., openai_api_key: ...)
    (
        re.compile(
            r"""(?i)(?<![A-Za-z0-9_])(["']?[A-Za-z0-9_-]*?(?:api[_-]?key|secret|token|password|passwd|pwd)["']?)\s*([:=])\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;\])}'"]+)"""
        ),
        r"\1\2[REDACTED]",
    ),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # Phone numbers (CN 11-digit mobile & international formats)
    (re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), "[REDACTED_PHONE]"),
    (re.compile(r"(?<!\d)\+\d{1,3}[- ]\d{2,4}[- ]\d{3,4}[- ]\d{3,4}(?!\d)"), "[REDACTED_PHONE]"),
    # Windows/POSIX home user paths
    (re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s\"']+"), r"C:\\Users\\[REDACTED_USER]"),
    (re.compile(r"/(?:Users|home)/[^/\s\"']+"), "/home/[REDACTED_USER]"),
)

_SENSITIVE_DICT_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|secret|access[_-]?token|refresh[_-]?token|csrf[_-]?token|auth[_-]?token|token|password|passwd|pwd|authorization|cookie|private[_-]?key)(?:$|[_-])"
)
_NON_SENSITIVE_TOKEN_METRIC_RE = re.compile(
    r"(?i)(?:token[_-]?(?:count|limit|budget|max|min|usage|used|len|length)|(?:max|min|total|input|output|prompt|completion)[_-]?tokens?)$"
)

_KEEPSAKE_RAW_FILES: tuple[str, ...] = (
    "persona.json",
    "reflections.json",
    "facts.json",
    "facts_archive.json",
    "reflections_archive.json",
    "persona_corrections.json",
    "user_directives.json",
    "imprint_notes_cache.json",
)


def _is_sensitive_dict_key(key: str) -> bool:
    if _NON_SENSITIVE_TOKEN_METRIC_RE.search(key):
        return False
    return bool(_SENSITIVE_DICT_KEY_RE.search(key))


def redact_text(text: str) -> str:
    """Scrub likely secrets, emails, phone numbers, and local user paths."""
    out = str(text)
    for pattern, repl in _REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_obj(obj: Any) -> Any:
    """Recursively apply ``redact_text`` and sensitive-key masking across JSON-serializable structures."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if _is_sensitive_dict_key(ks) and isinstance(v, str) and v.strip():
                out[ks] = "[REDACTED]"
            else:
                out[ks] = redact_obj(v)
        return out
    return obj


def _render_keepsake_markdown(character_name: str, overview: dict[str, Any]) -> str:
    imprint = overview.get("imprint") or {}
    stats = imprint.get("stats") or {}
    warm = imprint.get("warm_summary") or {}
    cached_note = warm.get("cached_note") if isinstance(warm, dict) else None
    note = cached_note if isinstance(cached_note, dict) else (warm if isinstance(warm, dict) else {})
    milestones = imprint.get("milestones") or []
    evolution = imprint.get("evolution") or []
    groups = (overview.get("memories") or {}).get("groups") or {}

    lines: list[str] = [
        f"# {character_name} — Our Memories",
        "",
        f"- Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Companion days: {stats.get('companion_days', 0)}",
        f"- Conversations remembered: {stats.get('total_messages', 0)}",
        f"- Shared facts: {stats.get('facts_total', 0)}",
        f"- Deep impressions left by you: {stats.get('imprint_persona_count', 0)}",
        "",
    ]

    if note.get("summary") or note.get("commentary") or note.get("blessing"):
        lines.append("## Relationship Note")
        lines.append("")
        if note.get("summary"):
            lines.append(f"> {note['summary']}")
            lines.append("")
        if note.get("commentary"):
            lines.append(str(note["commentary"]))
            lines.append("")
        if note.get("blessing"):
            lines.append(f"*{note['blessing']}*")
            lines.append("")

    section_titles = (
        ("about_you", "## What They Remember About You"),
        ("relationship", "## Our Bond & Inside Jokes"),
        ("growth", "## Growth Because of You"),
        ("other_people", "## People & Things Around You"),
    )
    for key, heading in section_titles:
        items = groups.get(key) or []
        if not items:
            continue
        lines.append(heading)
        lines.append("")
        for item in items:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            pin = " [Pinned]" if item.get("user_pinned") else ""
            lines.append(f"- {text}{pin}")
        lines.append("")

    if milestones:
        lines.append("## Milestones")
        lines.append("")
        for m in milestones:
            dt = str(m.get("created_at") or "")[:10]
            prefix = f"[{dt}] " if dt else ""
            lines.append(f"- {prefix}{m.get('text', '')}")
        lines.append("")

    if evolution:
        lines.append("## How Understanding Evolved")
        lines.append("")
        for ev in evolution:
            lines.append(f"- Before: {ev.get('previous_text', '')}")
            lines.append(f"  Now: {ev.get('current_text', '')}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def build_memory_export_zip(
    character_name: str,
    *,
    mode: str = "keepsake",
    memory_dir: Path | None = None,
) -> tuple[bytes, str]:
    """Synchronously assemble a keepsake or redacted diagnostics ZIP archive."""
    norm_mode = "diagnostics" if str(mode or "").strip().lower() == "diagnostics" else "keepsake"
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    char_dir = base / character_name

    overview = build_memory_overview(character_name, memory_dir=base)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_char = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff-]+", "_", str(character_name)).strip("_") or "memory"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if norm_mode == "keepsake":
            filename = f"{safe_char}_keepsake_{stamp}.zip"
            md = _render_keepsake_markdown(character_name, overview)
            zf.writestr("OUR_MEMORIES.md", md.encode("utf-8"))
            zf.writestr(
                "overview.json",
                json.dumps(overview, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for fname in _KEEPSAKE_RAW_FILES:
                fpath = char_dir / fname
                if fpath.is_file():
                    zf.writestr(f"raw/{fname}", fpath.read_bytes())
            # Include cold archive shards verbatim in user-requested keepsake backups
            # via cold_store.shard_relatives() (file export is an allowed cold-store use).
            from memory import cold_store
            for rel in sorted(cold_store.shard_relatives(str(char_dir))):
                if cold_store.is_managed_relative(rel):
                    shard_path = char_dir / rel
                    if shard_path.is_file():
                        zf.writestr(f"raw/{rel}", shard_path.read_bytes())
        else:
            # Diagnostics filename uses a fixed non-PII prefix so raw character names
            # (which may contain sensitive identifiers) never leak into download headers.
            filename = f"neko_memory_diagnostics_redacted_{stamp}.zip"
            star_map = build_star_map(character_name, memory_dir=base)
            lineage_graph = build_full_lineage_graph(
                character_name,
                memory_dir=base,
                include_conversations=False,
            )
            manifest = {
                "schema_version": 1,
                "mode": "diagnostics_redacted",
                "exported_at": datetime.now().isoformat(),
                "character_name": character_name,
                "includes_raw_conversations": False,
                "redaction_applied": True,
                "files": ["manifest.json", "overview.json", "star_map.json", "lineage_graph.json"],
            }
            zf.writestr(
                "manifest.json",
                json.dumps(redact_obj(manifest), ensure_ascii=False, indent=2).encode("utf-8"),
            )
            zf.writestr(
                "overview.json",
                json.dumps(redact_obj(overview), ensure_ascii=False, indent=2).encode("utf-8"),
            )
            zf.writestr(
                "star_map.json",
                json.dumps(redact_obj(star_map), ensure_ascii=False, indent=2).encode("utf-8"),
            )
            zf.writestr(
                "lineage_graph.json",
                json.dumps(redact_obj(lineage_graph), ensure_ascii=False, indent=2).encode("utf-8"),
            )

    return buf.getvalue(), filename


async def abuild_memory_export_zip(
    character_name: str,
    *,
    mode: str = "keepsake",
    memory_dir: Path | None = None,
) -> tuple[bytes, str]:
    """Non-blocking async wrapper around ``build_memory_export_zip``."""
    return await asyncio.to_thread(
        build_memory_export_zip,
        character_name,
        mode=mode,
        memory_dir=memory_dir,
    )
