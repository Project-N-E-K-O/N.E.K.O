"""Push composer for galgame plugin.

Merges the two push pipelines — plot (what is happening) and character profile
(who they are) — into a single self-contained push payload, enforces a token
budget, and renders the wire-format text the catgirl actually sees.

Design notes
------------

* Token counting uses :mod:`tiktoken` (cl100k_base by default) when available
  and falls back to :func:`context_tokens.count_tokens_heuristic` otherwise.
  The encoding name is configurable so the plugin can adapt to non-OpenAI
  tokenizers in the future.
* :meth:`compose` only performs **textual** budget enforcement — when the
  rendered plot block is over budget, earlier key lines are dropped first. Level
  downgrade (L2 → L1 → L0) for the character pipeline is a caller concern; the
  caller picks the level and asks :class:`CharacterProfileManager` to render the
  matching payload.
* The composer is stateless and pure (modulo the cached tokenizer instance) so
  it can be safely shared between agents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .context_tokens import count_tokens_heuristic


try:  # pragma: no cover - import probe
    import tiktoken
except ImportError:  # pragma: no cover - degraded path
    tiktoken = None  # type: ignore[assignment]


MAX_PUSH_TOKENS: int = 1200
"""Hard upper bound on the combined rendered push (plot + character)."""

PLOT_MIN_TOKENS: int = 120
"""Floor on plot block tokens before we stop trimming key_lines."""

DEFAULT_TOKEN_ENCODING: str = "cl100k_base"

_PUSH_FORMAT_VERSION: int = 1


@dataclass(frozen=True, slots=True)
class ComposeResult:
    """Structured composer output preserved alongside the wire payload."""

    push_seq: int
    mode: str
    plot_block: str
    character_block: str
    total_tokens: int
    plot_tokens: int
    character_tokens: int
    truncated_plot: bool


class PushComposer:
    """Combine plot + character pipeline outputs into a single push payload."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        max_tokens: int = MAX_PUSH_TOKENS,
        encoding_name: str = DEFAULT_TOKEN_ENCODING,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._max_tokens = max(0, int(max_tokens))
        self._encoding_name = encoding_name
        self._tokenizer = self._resolve_tokenizer(encoding_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compose(
        self,
        plot_payload: dict[str, Any] | None,
        character_payload: dict[str, Any] | None,
        *,
        push_seq: int,
        mode: str,
    ) -> dict[str, Any]:
        """Return a self-contained push payload dict for the catgirl pipeline.

        ``plot_payload`` and ``character_payload`` may be ``None``; the
        resulting dict will simply omit the corresponding block. ``mode`` is
        echoed for the caller's accounting — common values are ``"incremental"``
        and ``"full"``.
        """
        plot_block = self.build_plot_block(plot_payload) if plot_payload else ""
        character_block = (
            self.build_character_block(character_payload)
            if character_payload
            else ""
        )

        plot_block, character_block, truncated_plot = self._enforce_budget(
            plot_block, character_block, plot_payload
        )
        plot_block, character_block, wrapped_truncated = self._enforce_wrapped_budget(
            plot_block,
            character_block,
        )
        truncated_plot = truncated_plot or wrapped_truncated

        plot_tokens = self._count_tokens(plot_block)
        character_tokens = self._count_tokens(character_block)
        total = (
            self._count_tokens(self.wrap_push_message(plot_block, character_block))
            if plot_block or character_block
            else 0
        )

        return {
            "push_seq": int(push_seq),
            "mode": str(mode or "incremental"),
            "format_version": _PUSH_FORMAT_VERSION,
            "plot": self._extract_plot_metadata(plot_payload, plot_block),
            "characters": self._extract_character_metadata(
                character_payload, character_block
            ),
            "available_queries": self._merge_available_queries(
                plot_payload,
                character_payload,
            ),
            "_metrics": {
                "max_tokens": self._max_tokens,
                "total_tokens": total,
                "plot_tokens": plot_tokens,
                "character_tokens": character_tokens,
                "truncated_plot": truncated_plot,
                "encoding": self._encoding_name,
            },
        }

    def wrap_push_message(
        self,
        plot_text: str,
        character_text: str,
        *,
        header: str = "======[游戏动态]",
        footer: str = "======",
    ) -> str:
        """Render the final wire-format text the catgirl actually sees.

        Empty blocks are dropped so the watermark is consistent regardless of
        which pipelines produced output.
        """
        parts: list[str] = [header]
        if character_text.strip():
            parts.append(character_text.strip())
        if plot_text.strip():
            parts.append(plot_text.strip())
        parts.append(footer)
        return "\n\n".join(parts)

    def build_plot_block(self, payload: dict[str, Any] | None) -> str:
        """Render the ``[剧情]`` block. Self-contained per push."""
        if not payload:
            return ""

        lines: list[str] = []
        anchor = payload.get("scene_anchor") or {}
        location = self._strip(anchor.get("location"))
        characters_present = anchor.get("characters_present")
        if location or characters_present:
            anchor_line = "📍"
            if location:
                anchor_line += f" {location}"
            if isinstance(characters_present, list) and characters_present:
                names = " & ".join(
                    self._strip(name) for name in characters_present if self._strip(name)
                )
                if names:
                    anchor_line += f" — {names}"
            lines.append(anchor_line.strip())

        plot_update = payload.get("plot_update") or {}
        summary = self._strip(plot_update.get("summary"))
        if summary:
            lines.append("")
            lines.append(summary)

        key_lines = plot_update.get("key_lines")
        if isinstance(key_lines, list) and key_lines:
            lines.append("")
            for entry in key_lines:
                if not isinstance(entry, dict):
                    continue
                speaker = self._strip(entry.get("speaker")) or "旁白"
                text = self._strip(entry.get("text"))
                if text:
                    lines.append(f"💬「{speaker}：{text}」")

        new_choices = plot_update.get("new_choices")
        if isinstance(new_choices, list) and new_choices:
            lines.append("")
            lines.append("❓ 新选择：")
            for choice in new_choices:
                if not isinstance(choice, dict):
                    continue
                text = self._strip(choice.get("text"))
                context = self._strip(choice.get("context"))
                if text and context:
                    lines.append(f"  · {text} — {context}")
                elif text:
                    lines.append(f"  · {text}")

        offline_summary = self._strip(payload.get("offline_events_summary"))
        if offline_summary:
            lines.append("")
            lines.append(f"⏱ {offline_summary}")

        return "\n".join(lines).strip()

    def build_character_block(self, payload: dict[str, Any] | None) -> str:
        """Render the ``[角色档案]`` block from a character payload."""
        if not payload:
            return ""
        characters = payload.get("characters")
        if not isinstance(characters, dict) or not characters:
            return ""
        level = str(payload.get("level") or "L1").upper()
        fixed = self._strip(payload.get("fixed_character"))

        lines: list[str] = []
        header = "======[角色身份]"
        if fixed:
            header += f"（锁定：{fixed}）"
        elif level:
            header += f"（{level}）"
        lines.append(header)

        for name, rendered in characters.items():
            text = self._strip(rendered)
            if text:
                lines.append(text)

        return "\n\n".join(lines)

    # ------------------------------------------------------------------
    # Budget enforcement
    # ------------------------------------------------------------------

    def _enforce_budget(
        self,
        plot_block: str,
        character_block: str,
        plot_payload: dict[str, Any] | None,
    ) -> tuple[str, str, bool]:
        """Trim ``plot_block`` (and as a last resort ``character_block``) so the
        combined token count fits ``self._max_tokens``.

        Returns ``(plot_block, character_block, truncated_plot)``.
        """
        if self._max_tokens <= 0:
            return plot_block, character_block, False

        plot_tokens = self._count_tokens(plot_block)
        char_tokens = self._count_tokens(character_block)
        total = plot_tokens + char_tokens
        if total <= self._max_tokens:
            return plot_block, character_block, False

        truncated_plot = False

        # Strategy 1: drop earliest key_lines until plot fits the remaining budget
        if plot_block and plot_payload:
            plot_update = plot_payload.get("plot_update")
            if isinstance(plot_update, dict):
                key_lines = list(plot_update.get("key_lines") or [])
                while (
                    self._count_tokens(plot_block) + char_tokens
                    > self._max_tokens
                    and len(key_lines) > 1
                ):
                    key_lines = key_lines[1:]
                    trimmed_payload = {
                        **plot_payload,
                        "plot_update": {**plot_update, "key_lines": key_lines},
                    }
                    plot_block = self.build_plot_block(trimmed_payload)
                    truncated_plot = True

        # Strategy 2: hard-truncate the plot text if still over (preserve tail)
        plot_tokens = self._count_tokens(plot_block)
        if plot_tokens + char_tokens > self._max_tokens and plot_block:
            allowed = max(PLOT_MIN_TOKENS, self._max_tokens - char_tokens)
            plot_block = self._truncate_to_tokens(plot_block, allowed)
            truncated_plot = True

        # Strategy 3: trim character block if still over
        plot_tokens = self._count_tokens(plot_block)
        if plot_tokens + char_tokens > self._max_tokens and character_block:
            allowed = max(0, self._max_tokens - plot_tokens)
            character_block = self._truncate_to_tokens(character_block, allowed)

        return plot_block, character_block, truncated_plot

    def _enforce_wrapped_budget(
        self,
        plot_block: str,
        character_block: str,
    ) -> tuple[str, str, bool]:
        if self._max_tokens <= 0:
            return plot_block, character_block, False
        truncated_plot = False
        while (
            self._count_tokens(self.wrap_push_message(plot_block, character_block))
            > self._max_tokens
        ):
            if plot_block:
                current = self._count_tokens(plot_block)
                over = (
                    self._count_tokens(self.wrap_push_message(plot_block, character_block))
                    - self._max_tokens
                )
                plot_block = self._truncate_to_tokens(
                    plot_block,
                    max(0, current - over - 1),
                )
                truncated_plot = True
            elif character_block:
                current = self._count_tokens(character_block)
                over = (
                    self._count_tokens(self.wrap_push_message(plot_block, character_block))
                    - self._max_tokens
                )
                character_block = self._truncate_to_tokens(
                    character_block,
                    max(0, current - over - 1),
                )
            else:
                break
        return plot_block, character_block, truncated_plot

    def _truncate_to_tokens(self, text: str, budget: int) -> str:
        if budget <= 0:
            return ""
        if self._count_tokens(text) <= budget:
            return text
        marker = "…[truncated]"
        marker_tokens = self._count_tokens(marker)
        if budget <= marker_tokens:
            return ""
        target = max(0, budget - marker_tokens)
        # Binary search on suffix character length; CJK-heavy strings need fewer chars.
        low, high = 0, len(text)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = text[-mid:].lstrip() if mid else ""
            if not candidate:
                low = mid + 1
                continue
            if self._count_tokens(candidate) <= target:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        if not best:
            return ""
        return marker + best

    # ------------------------------------------------------------------
    # Tokenization
    # ------------------------------------------------------------------

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._tokenizer is not None:
            try:
                return len(self._tokenizer.encode(text))
            except Exception:  # noqa: BLE001 — tokenizer surface varies by version
                self._tokenizer = None
                self._logger.warning(
                    "tokenizer %s failed; falling back to heuristic counter",
                    self._encoding_name,
                )
        return count_tokens_heuristic(text)

    @staticmethod
    def _resolve_tokenizer(encoding_name: str) -> Any | None:
        if tiktoken is None:
            return None
        try:
            return tiktoken.get_encoding(encoding_name)
        except Exception:  # noqa: BLE001 — encoding name may be invalid
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _extract_plot_metadata(
        payload: dict[str, Any] | None, rendered: str
    ) -> dict[str, Any] | None:
        if not payload:
            return None
        anchor = payload.get("scene_anchor") or {}
        return {
            "scene_id": payload.get("scene_id") or "",
            "anchor_location": anchor.get("location") or "",
            "rendered": rendered,
        }

    @staticmethod
    def _extract_character_metadata(
        payload: dict[str, Any] | None, rendered: str
    ) -> dict[str, Any] | None:
        if not payload:
            return None
        return {
            "level": str(payload.get("level") or "L1").upper(),
            "fixed_character": payload.get("fixed_character") or "",
            "names": list((payload.get("characters") or {}).keys()),
            "rendered": rendered,
        }

    @staticmethod
    def _merge_available_queries(
        plot_payload: dict[str, Any] | None,
        character_payload: dict[str, Any] | None,
    ) -> list[Any]:
        merged: list[Any] = []
        seen: set[str] = set()
        for payload in (plot_payload, character_payload):
            queries = payload.get("available_queries", []) if payload else []
            if not isinstance(queries, list):
                continue
            for query in queries:
                if isinstance(query, dict):
                    key = str(
                        query.get("id")
                        or query.get("query")
                        or query.get("name")
                        or sorted(str(item) for item in query.items())
                    )
                else:
                    key = str(query)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(query)
        return merged


__all__ = [
    "PushComposer",
    "ComposeResult",
    "MAX_PUSH_TOKENS",
    "PLOT_MIN_TOKENS",
    "DEFAULT_TOKEN_ENCODING",
]
