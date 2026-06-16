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
_NOTE_SUMMARY_HEADINGS = {
    "zh": ("标题", "要点", "细节"),
    "zh-cn": ("标题", "要点", "细节"),
    "zh-tw": ("標題", "重點", "細節"),
    "en": ("Title", "Key Points", "Details"),
    "ja": ("タイトル", "要点", "詳細"),
    "ko": ("제목", "핵심 요점", "세부 내용"),
    "es": ("Título", "Puntos clave", "Detalles"),
    "pt": ("Título", "Pontos principais", "Detalhes"),
    "ru": ("Заголовок", "Ключевые моменты", "Подробности"),
}
_EXPAND_FALLBACK_MESSAGES = {
    "zh": "暂时无法连接模型扩写。建议补充定义、例子、易错点和一个自测问题。",
    "zh-cn": "暂时无法连接模型扩写。建议补充定义、例子、易错点和一个自测问题。",
    "zh-tw": "暫時無法連接模型擴寫。建議補充定義、例子、易錯點和一個自測問題。",
    "en": "The model is currently unavailable for expansion. Consider adding definitions, examples, common pitfalls, and one self-check question.",
    "ja": "現在、モデルに接続してノートを拡張できません。定義、例、間違えやすい点、確認問題を1つ追加してみてください。",
    "ko": "현재 모델에 연결해 노트를 확장할 수 없습니다. 정의, 예시, 자주 틀리는 부분, 자가 점검 질문 하나를 추가해 보세요.",
    "es": "El modelo no está disponible para ampliar la nota. Considera añadir definiciones, ejemplos, errores comunes y una pregunta de autoevaluación.",
    "pt": "O modelo não está disponível para expandir a nota. Considere adicionar definições, exemplos, pontos de erro comuns e uma pergunta de autoavaliação.",
    "ru": "Модель сейчас недоступна для расширения заметки. Добавьте определения, примеры, типичные ошибки и один вопрос для самопроверки.",
}


def _localized_note_headings(language: str) -> tuple[str, str, str]:
    normalized = str(language or "").strip().lower().replace("_", "-")
    if normalized in _NOTE_SUMMARY_HEADINGS:
        return _NOTE_SUMMARY_HEADINGS[normalized]
    primary = normalized.split("-", 1)[0]
    return _NOTE_SUMMARY_HEADINGS.get(primary, _NOTE_SUMMARY_HEADINGS["en"])


def _localized_expand_fallback(language: str) -> str:
    normalized = str(language or "").strip().lower().replace("_", "-")
    if normalized in _EXPAND_FALLBACK_MESSAGES:
        return _EXPAND_FALLBACK_MESSAGES[normalized]
    primary = normalized.split("-", 1)[0]
    return _EXPAND_FALLBACK_MESSAGES.get(primary, _EXPAND_FALLBACK_MESSAGES["en"])


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
        )
        markdown = _ensure_expanded_note_preserves_original(
            original,
            str(raw or ""),
            language=getattr(self._config, "language", ""),
        )
        return TutorReply(
            operation=LLM_OPERATION_EXPAND_NOTE,
            input_text=original,
            reply=markdown,
            payload={"content": markdown},
            created_at=utc_now_iso(),
        )
    except Exception as exc:
        markdown = _fallback_expand_note(
            original,
            language=getattr(self._config, "language", ""),
        )
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
    headings = _localized_note_headings(self._config.language)
    messages = [
        {
            "role": "system",
            "content": (
                "You turn study material into a Markdown note. The output must be "
                "Markdown only and use this structure: "
                f"'# {headings[0]}', '## {headings[1]}', '### {headings[2]}'. "
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
        )
        markdown = _ensure_note_summary_structure(str(raw or ""), text, headings=headings)
        title = _extract_markdown_title(markdown) or "Study Note"
        return TutorReply(
            operation=LLM_OPERATION_SUMMARIZE_TO_NOTE,
            input_text=text,
            reply=markdown,
            payload={"title": title, "content": markdown},
            created_at=utc_now_iso(),
        )
    except Exception as exc:
        markdown = _fallback_summary_note(text, headings=headings)
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


def _ensure_expanded_note_preserves_original(
    original: str,
    raw: str,
    *,
    language: str = "",
) -> str:
    generated = str(raw or "").strip()
    if not generated:
        return _fallback_expand_note(original, language=language)
    # Trust the model output as-is only when the COMPLETE original is present;
    # matching just a prefix would let a model that echoes the first lines drop
    # the rest of a longer note (the editor then overwrites + autosaves it).
    original_full = original.strip()
    has_original = bool(original_full and original_full in generated)
    has_ai_callout = "> [!ai]" in generated
    if has_original and has_ai_callout:
        return generated
    addition = generated
    if has_original:
        addition = generated.replace(original, "", 1).strip() or generated
    if not addition.startswith("> [!ai]"):
        addition = "> [!ai]\n> " + addition.replace("\n", "\n> ")
    return f"{original}\n\n{addition}".strip()


def _ensure_note_summary_structure(
    raw: str,
    source_text: str,
    *,
    headings: tuple[str, str, str] | None = None,
) -> str:
    _title_heading, summary_heading, details_heading = (
        headings or _localized_note_headings("")
    )
    markdown = str(raw or "").strip()
    if not markdown:
        return _fallback_summary_note(source_text, headings=headings)
    lines = markdown.splitlines()
    if not any(line.startswith("# ") for line in lines):
        title = _derive_title(source_text)
        markdown = f"# {title}\n\n{markdown}"
    if f"## {summary_heading}" not in markdown:
        markdown += f"\n\n## {summary_heading}\n\n- " + _first_sentence(source_text)
    if f"### {details_heading}" not in markdown:
        markdown += f"\n\n### {details_heading}\n\n" + source_text[:1000].strip()
    return markdown.strip()


def _fallback_expand_note(original: str, *, language: str = "") -> str:
    message = _localized_expand_fallback(language)
    return (
        f"{original.strip()}\n\n"
        "> [!ai]\n"
        f"> {message}"
    ).strip()


def _fallback_summary_note(
    source_text: str,
    *,
    headings: tuple[str, str, str] | None = None,
) -> str:
    _title_heading, summary_heading, details_heading = (
        headings or _localized_note_headings("")
    )
    title = _derive_title(source_text)
    return (
        f"# {title}\n\n"
        f"## {summary_heading}\n\n"
        f"- {_first_sentence(source_text)}\n\n"
        f"### {details_heading}\n\n"
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
