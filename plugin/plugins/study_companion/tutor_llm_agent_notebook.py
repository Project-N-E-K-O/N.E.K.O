from __future__ import annotations

from .constants import LLM_OPERATION_EXPAND_NOTE, LLM_OPERATION_SUMMARIZE_TO_NOTE
from .tutor_llm_agent_common import (
    SdkError,
    TutorReply,
    _bounded_prompt_text,
    diagnostic_code_for_exception,
    utc_now_iso,
)


_MAX_EXPAND_NOTE_CHARS = 8000
_MAX_SUMMARIZE_TO_NOTE_CHARS = 12000


async def expand_note(
    self,
    content: str,
    *,
    topic_context: str = "",
    expand_scope: str = "details",
) -> TutorReply:
    original = str(content or "").strip()
    if not original:
        raise SdkError("note content is required")
    bounded = _bounded_prompt_text(original, max_chars=_MAX_EXPAND_NOTE_CHARS)
    scope = str(expand_scope or "details").strip() or "details"
    messages = [
        {
            "role": "system",
            "content": (
                "You are a concise study tutor. Expand the student's Markdown note. "
                "Preserve the original note content and append new material under a "
                "Markdown callout headed exactly '> [!ai]'. Do not overwrite or delete "
                "the student's wording."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language: {self._config.language}\n"
                f"Expansion scope: {scope}\n"
                f"Topic context: {str(topic_context or '').strip() or '-'}\n\n"
                "Original note:\n"
                f"{bounded}"
            ),
        },
    ]
    try:
        raw = await self._call_model(
            messages,
            operation=LLM_OPERATION_EXPAND_NOTE,
            model_group_override="tutor",
        )
        markdown = _ensure_expanded_note_preserves_original(original, str(raw or ""))
        return TutorReply(
            operation=LLM_OPERATION_EXPAND_NOTE,
            input_text=original,
            reply=markdown,
            payload={"content": markdown},
            created_at=utc_now_iso(),
        )
    except Exception as exc:
        markdown = _fallback_expand_note(original)
        return TutorReply(
            operation=LLM_OPERATION_EXPAND_NOTE,
            input_text=original,
            reply=markdown,
            payload={"content": markdown},
            degraded=True,
            diagnostic=diagnostic_code_for_exception(exc),
            created_at=utc_now_iso(),
        )


async def summarize_to_note(
    self,
    source_text: str,
    *,
    source_type: str = "manual",
    source_ref: str = "",
) -> TutorReply:
    text = str(source_text or "").strip()
    if not text:
        raise SdkError("note source text is required")
    bounded = _bounded_prompt_text(text, max_chars=_MAX_SUMMARIZE_TO_NOTE_CHARS)
    messages = [
        {
            "role": "system",
            "content": (
                "You turn study material into a Markdown note. The output must be "
                "Markdown only and use this structure: '# 标题', '## 要点', '### 细节'. "
                "Keep it faithful to the source and do not mention saving."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language: {self._config.language}\n"
                f"Source type: {str(source_type or 'manual')}\n"
                f"Source reference: {str(source_ref or '') or '-'}\n\n"
                "Source material:\n"
                f"{bounded}"
            ),
        },
    ]
    try:
        raw = await self._call_model(
            messages,
            operation=LLM_OPERATION_SUMMARIZE_TO_NOTE,
            model_group_override="summary",
        )
        markdown = _ensure_note_summary_structure(str(raw or ""), text)
        title = _extract_markdown_title(markdown) or "Study Note"
        return TutorReply(
            operation=LLM_OPERATION_SUMMARIZE_TO_NOTE,
            input_text=text,
            reply=markdown,
            payload={"title": title, "content": markdown},
            created_at=utc_now_iso(),
        )
    except Exception as exc:
        markdown = _fallback_summary_note(text)
        title = _extract_markdown_title(markdown) or "Study Note"
        return TutorReply(
            operation=LLM_OPERATION_SUMMARIZE_TO_NOTE,
            input_text=text,
            reply=markdown,
            payload={"title": title, "content": markdown},
            degraded=True,
            diagnostic=diagnostic_code_for_exception(exc),
            created_at=utc_now_iso(),
        )


def _ensure_expanded_note_preserves_original(original: str, raw: str) -> str:
    generated = str(raw or "").strip()
    if not generated:
        return _fallback_expand_note(original)
    original_probe = original[:120].strip()
    has_original = bool(original_probe and original_probe in generated)
    has_ai_callout = "> [!ai]" in generated
    if has_original and has_ai_callout:
        return generated
    addition = generated
    if has_original:
        addition = generated.replace(original, "", 1).strip() or generated
    if not addition.startswith("> [!ai]"):
        addition = "> [!ai]\n> " + addition.replace("\n", "\n> ")
    return f"{original}\n\n{addition}".strip()


def _ensure_note_summary_structure(raw: str, source_text: str) -> str:
    markdown = str(raw or "").strip()
    if not markdown:
        return _fallback_summary_note(source_text)
    lines = markdown.splitlines()
    if not any(line.startswith("# ") for line in lines):
        title = _derive_title(source_text)
        markdown = f"# {title}\n\n{markdown}"
    if "## 要点" not in markdown:
        markdown += "\n\n## 要点\n\n- " + _first_sentence(source_text)
    if "### 细节" not in markdown:
        markdown += "\n\n### 细节\n\n" + source_text[:1000].strip()
    return markdown.strip()


def _fallback_expand_note(original: str) -> str:
    return (
        f"{original.strip()}\n\n"
        "> [!ai]\n"
        "> 暂时无法连接模型扩写。建议补充定义、例子、易错点和一个自测问题。"
    ).strip()


def _fallback_summary_note(source_text: str) -> str:
    title = _derive_title(source_text)
    return (
        f"# {title}\n\n"
        "## 要点\n\n"
        f"- {_first_sentence(source_text)}\n\n"
        "### 细节\n\n"
        f"{source_text[:2000].strip()}"
    ).strip()


def _extract_markdown_title(markdown: str) -> str:
    for line in str(markdown or "").splitlines():
        if line.startswith("# "):
            return line[2:].strip()[:160]
    return ""


def _derive_title(source_text: str) -> str:
    for line in str(source_text or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:80]
    return "Study Note"


def _first_sentence(source_text: str) -> str:
    text = " ".join(str(source_text or "").split())
    for delimiter in ("。", ".", "！", "!", "？", "?"):
        if delimiter in text:
            head, _, _tail = text.partition(delimiter)
            return (head + delimiter).strip()[:240]
    return (text or "No source text available.")[:240]
