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

"""Model-facing text for the tool-image channel.

Every string a tool's picture drags into the conversation lives here: the
caption that rides alongside the image part, the stand-in when the tool named
no vision prompt, the budget-omission warning, and the placeholder that
replaces the image turn once the tool loop lets go of it.

All of it is read by the model, not by the user, so it follows the same rule as
the rest of ``config/prompts``: one row per runtime locale, resolved through
``prompts_sys._loc``. Before this module the four strings were inline literals
in ``main_logic/omni_offline_client/_tools.py``, and split down the middle --
the caption and the omission warning in English, the placeholder and its recall
suffix in Chinese -- so one tool call handed the model two languages at once.

The single call site is ``_ToolingMixin._append_tool_result_images``. It
resolves the locale once per injected turn via ``normalize_tool_image_locale``
and formats every row against it, which is what keeps the four strings in one
language.
"""

from config._runtime import resolve_global_language
from config.prompts._locale import normalize_prompt_locale


def normalize_tool_image_locale(language: str | None) -> str:
    """Normalize a session locale to a key of this module's tables.

    Same shape as ``prompts_avatar_interaction._avatar_interaction_locale``:
    an empty / missing session locale falls back to the app's global language
    before normalizing, because the offline client can inject a tool image
    before ``LLMSessionManager.user_language`` has been seeded, and English
    captions in a Chinese session would be a worse guess than the UI language.
    ``resolve_global_language`` returns ``"en"`` while unbound, so a bare import
    still resolves deterministically.

    Whitespace is stripped *before* the fallback rather than left to
    ``normalize_prompt_locale``. A locale of ``"  "`` means the session never
    set one, and the neighbouring ``or``-only spellings send it straight past
    the global language into the English default.

    Returns one of ``zh`` / ``zh-TW`` / ``en`` / ``ja`` / ``ko`` / ``ru`` /
    ``es`` / ``pt`` -- the key scheme every table below uses.
    """
    return normalize_prompt_locale(
        str(language or "").strip() or resolve_global_language(),
        default="en",
        simplified="zh",
        keep_traditional=True,
    )


# Stand-in caption for an image whose tool supplied no vision prompt. Several
# providers reject a bare image part, so there is always a text part.
TOOL_IMAGE_DEFAULT_CAPTION = {
    "zh": "（工具返回的画面）",
    "zh-TW": "（工具傳回的畫面）",
    "en": "(image returned by the tool)",
    "ja": "（ツールが返した画像）",
    "ko": "(도구가 반환한 이미지)",
    "ru": "(изображение, возвращённое инструментом)",
    "es": "(imagen devuelta por la herramienta)",
    "pt": "(imagem retornada pela ferramenta)",
}

# The text part that sits next to each injected image. Names the tool and the
# call so the model can tie the picture back to the call that produced it.
TOOL_IMAGE_CAPTION = {
    "zh": "工具 {tool_name} 返回的图片（call_id={call_id}）：{instruction}",
    "zh-TW": "工具 {tool_name} 傳回的圖片（call_id={call_id}）：{instruction}",
    "en": "Tool image from {tool_name} (call_id={call_id}): {instruction}",
    "ja": "ツール {tool_name} の画像（call_id={call_id}）：{instruction}",
    "ko": "도구 {tool_name}의 이미지(call_id={call_id}): {instruction}",
    "ru": "Изображение от инструмента {tool_name} (call_id={call_id}): {instruction}",
    "es": "Imagen de la herramienta {tool_name} (call_id={call_id}): {instruction}",
    "pt": "Imagem da ferramenta {tool_name} (call_id={call_id}): {instruction}",
}

# Told to the model through ``_image_warnings`` when the shared per-turn image
# budget dropped pictures a tool did return. Saying nothing would let her
# answer as though she had looked at them.
TOOL_IMAGE_OMITTED_WARNING = {
    "zh": "因本轮共享图片预算已用尽，已省略 {count} 张工具图片",
    "zh-TW": "因本輪共用圖片預算已用盡，已省略 {count} 張工具圖片",
    "en": "{count} tool image(s) omitted because the shared turn image budget was exhausted",
    "ja": "このターンの共有画像上限に達したため、ツール画像 {count} 枚を省略しました",
    "ko": "이번 턴의 공유 이미지 한도를 모두 사용하여 도구 이미지 {count}개를 생략했습니다",
    "ru": "Пропущено изображений инструмента: {count}; общий лимит изображений для этого хода исчерпан",
    "es": "Se omitieron {count} imágenes de herramientas porque se agotó el presupuesto compartido de imágenes del turno",
    "pt": "{count} imagens de ferramentas foram omitidas porque o limite compartilhado de imagens do turno foi esgotado",
}

# Appended to the eviction placeholder when the tool handed back a recall
# handle, so the model can ask for the same frame again instead of guessing.
TOOL_IMAGE_RECALL_HANDLE = {
    "zh": "；句柄 {shot_id}",
    "zh-TW": "；控制代碼 {shot_id}",
    "en": "; handle {shot_id}",
    "ja": "；ハンドル {shot_id}",
    "ko": "; 핸들 {shot_id}",
    "ru": "; идентификатор {shot_id}",
    "es": "; identificador {shot_id}",
    "pt": "; identificador {shot_id}",
}

# Separator for the tool's own recall hint, which follows the handle. The hint
# text itself is the tool's, and is passed through untranslated.
TOOL_IMAGE_RECALL_HINT = {
    "zh": "；{recall_hint}",
    "zh-TW": "；{recall_hint}",
    "en": "; {recall_hint}",
    "ja": "；{recall_hint}",
    "ko": "; {recall_hint}",
    "ru": "; {recall_hint}",
    "es": "; {recall_hint}",
    "pt": "; {recall_hint}",
}

# What the injected image turn becomes once the tool loop exits. The history
# has no image eviction of its own, so this is the only thing standing between
# a base64 frame and every later request.
TOOL_IMAGE_HISTORY_PLACEHOLDER = {
    "zh": "[工具 {tool_name} 返回的画面已从上下文移除；图片只在产生它的那一轮可见{recall_suffix}]",
    "zh-TW": "[工具 {tool_name} 傳回的畫面已從上下文移除；圖片僅在產生它的該輪可見{recall_suffix}]",
    "en": "[Image returned by tool {tool_name} was removed from context; the image was visible only in the turn that produced it{recall_suffix}]",
    "ja": "[ツール {tool_name} が返した画像はコンテキストから削除されました。この画像は生成されたターンでのみ表示されます{recall_suffix}]",
    "ko": "[도구 {tool_name}가 반환한 이미지는 컨텍스트에서 제거되었습니다. 이미지는 생성된 턴에서만 표시됩니다{recall_suffix}]",
    "ru": "[Изображение от инструмента {tool_name} удалено из контекста; оно было доступно только в том ходе, в котором было создано{recall_suffix}]",
    "es": "[La imagen devuelta por la herramienta {tool_name} se eliminó del contexto; solo estuvo visible en el turno en que se generó{recall_suffix}]",
    "pt": "[A imagem retornada pela ferramenta {tool_name} foi removida do contexto; ela ficou visível apenas no turno em que foi gerada{recall_suffix}]",
}
