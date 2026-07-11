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

"""
Memory-related prompt templates.

Includes: conversation summarization, history review, settings extraction,
emotion analysis, fact extraction, reflection, persona correction,
inner-thoughts injection fragments, and chat-gap notices.
"""

from __future__ import annotations

import re

from config.prompts.prompts_sys import _loc

# =====================================================================
# ======= Conversation summarization =================================
# =====================================================================

# ---------- recent_history_manager_prompt ----------
# i18n dict: RECENT_HISTORY_MANAGER_PROMPT

RECENT_HISTORY_MANAGER_PROMPT = {
    "zh": """请总结以下对话内容，生成简洁但信息丰富的摘要：

======以下为对话======
%s
======以上为对话======

你的摘要应该保留关键信息、重要事实和主要讨论点，且不能具有误导性或产生歧义。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

[重要]处理事实纠正：
- 当对话后段对前段已陈述的事实出现明确纠正（例如对方更正了之前说错的内容），摘要应反映这一过程：保留"原以为X，后被纠正为Y"的脉络，而不是只写最终结论或只写最初的误会
- 这样可以让后续对话不会重复犯同样的错误

[重要]保留{MASTER_NAME}的负面反馈（高价值信号）：
- {MASTER_NAME}明确表达"别再提 X / 不要做 Y / 不想聊 Z"这类**祈使句**时，必须原样写入摘要
- 不要压缩、改写或合并，按字面记录（例如"{MASTER_NAME}明确要求：不要再提加班"）
- 哪怕在对话里看起来口语化，也不可省略——下一轮模型据此避免再次触雷

请以key为"summary"、value为字符串的json字典格式返回。""",
    "en": """Please summarize the following conversation to produce a concise yet informative summary:

======以下为对话======
%s
======以上为对话======

Your summary should preserve key information, important facts, and main discussion points without being misleading or ambiguous.

[Important] Avoid excessive repetition of the same words in the summary:
- After first mention of recurring nouns or topic words, use pronouns (it/its/this) or contextual references
- Keep the summary smooth and natural — avoid a "parrot" effect
- Example: "discussed the flavor of the snack and its price" instead of "discussed the flavor of the snack and the snack's price"

[Important] Handle factual corrections:
- When the later part of the conversation explicitly corrects a previously stated fact (e.g., one party corrects a prior misstatement), the summary must reflect this trajectory: keep "originally X, later corrected to Y" rather than writing only the final conclusion or only the initial misunderstanding
- This prevents the same mistake from recurring in subsequent turns

[Important] Preserve {MASTER_NAME} negative feedback verbatim (high-value signal):
- When {MASTER_NAME} explicitly says "don't mention X / stop talking about Y / I don't want Z" (imperative form), record it as-is in the summary
- Do NOT compress, paraphrase, or merge these — keep them literal (e.g., "{MASTER_NAME} explicitly asked: don't bring up overtime")
- Even if phrased casually, never drop them — future turns rely on the summary to honor these constraints

Return as a JSON dict with key "summary" and a string value.""",
    "ja": """以下の会話内容を要約し、簡潔かつ情報量の多い要約を作成してください：

======以下为对话======
%s
======以上为对话======

要約には重要な情報、事実、主な議論のポイントを保持し、誤解を招いたり曖昧にならないようにしてください。

[重要] 要約中で同じ語彙を過度に繰り返さないでください：
- 繰り返し出現する名詞やトピックワードは、最初の言及後に代名詞（それ/その/この）や文脈上の指示で置き換えてください
- 要約をスムーズで自然な表現にし、「オウム返し」効果を避けてください

[重要] 事実の訂正の扱い：
- 会話の後半で前半に述べられた事実が明示的に訂正された場合（例：相手が以前の発言を訂正した場合）、要約はその経緯を反映してください：「当初Xと考えていたが、後にYに訂正された」という流れを保持し、最終結論のみや最初の誤解のみを書かないでください
- これにより、以降の対話で同じ誤りを繰り返さなくなります

[重要] {MASTER_NAME}のネガティブフィードバック（高価値シグナル）は原文どおり保持してください：
- {MASTER_NAME}が「その話はやめて / もう聞きたくない / 〇〇しないで」のような**命令形**で明示した場合、要約にそのまま書き留めること
- 圧縮・言い換え・統合は禁止——逐語で記録（例：「{MASTER_NAME}は明確に要求：残業の話はもうしないで」）
- カジュアルに言われていても省略しない——後続のターンはこの要約に依拠して制約を守る

JSON辞書形式で、キーを"summary"、値を文字列として返してください。""",
    "ko": """다음 대화 내용을 요약하여 간결하면서도 정보가 풍부한 요약을 생성해 주세요:

======以下为对话======
%s
======以上为对话======

요약에는 핵심 정보, 중요한 사실, 주요 논의 사항을 보존해야 하며, 오해를 일으키거나 모호해서는 안 됩니다.

[중요] 요약에서 동일한 단어를 과도하게 반복하지 마세요:
- 반복적으로 등장하는 명사나 주제어는 첫 언급 이후 대명사(그것/해당/이)나 문맥적 지시어로 대체하세요
- 요약을 매끄럽고 자연스럽게 표현하여 "앵무새" 효과를 피하세요

[중요] 사실 정정 처리:
- 대화 후반에 전반에서 진술된 사실이 명시적으로 정정된 경우(예: 상대방이 이전 발언을 정정한 경우), 요약은 그 과정을 반영해야 합니다: "처음에는 X로 알고 있었으나 이후 Y로 정정됨"이라는 흐름을 유지하고, 최종 결론만이나 최초의 오해만을 적지 마세요
- 이를 통해 이후 대화에서 같은 오류를 반복하지 않게 됩니다

[중요] {MASTER_NAME}의 부정적 피드백(고가치 신호)을 원문 그대로 보존하세요:
- {MASTER_NAME}이(가) "그 얘기는 그만 / 다시는 말하지 마 / X 하지 마"와 같은 **명령형**으로 명확히 표현하면, 요약에 그대로 기록하세요
- 압축, 의역, 병합 금지 — 문자 그대로 기록(예: "{MASTER_NAME}이(가) 명시적으로 요청: 야근 이야기는 더 이상 꺼내지 마세요")
- 캐주얼하게 표현되었더라도 절대 누락하지 마세요 — 이후 턴에서는 이 요약에 의존해 제약을 지킵니다

JSON 딕셔너리 형식으로 키를 "summary", 값을 문자열로 반환해 주세요.""",
    "ru": """Пожалуйста, обобщите следующую беседу, создав краткое, но информативное резюме:

======以下为对话======
%s
======以上为对话======

Резюме должно сохранять ключевую информацию, важные факты и основные обсуждаемые темы, при этом не вводить в заблуждение и не быть двусмысленным.

[Важно] Избегайте чрезмерного повторения одних и тех же слов в резюме:
- После первого упоминания повторяющихся существительных или тематических слов используйте местоимения (это/его/данный) или контекстные ссылки
- Сделайте резюме гладким и естественным, избегая эффекта «попугая»

[Важно] Обработка фактических исправлений:
- Когда в более поздней части беседы явно исправляется ранее сказанный факт (например, собеседник исправляет предыдущее ошибочное утверждение), резюме должно отражать этот ход: сохраняйте «изначально X, позже исправлено на Y», а не записывайте только окончательный вывод или только первоначальное недоразумение
- Это предотвращает повторение той же ошибки в последующих беседах

[Важно] Сохраняйте негативную обратную связь {MASTER_NAME} дословно (высокоценный сигнал):
- Когда {MASTER_NAME} явно говорит "не упоминай X / хватит об Y / я не хочу Z" (повелительная форма), запишите это как есть в резюме
- НЕ сжимайте, не перефразируйте и не объединяйте — фиксируйте буквально (например, «{MASTER_NAME} явно попросил: не поднимать тему переработок»)
- Даже если сказано вскользь, никогда не пропускайте — последующие реплики опираются на резюме, чтобы соблюдать эти ограничения

Верните в формате JSON-словаря с ключом "summary" и строковым значением.""",
    "es": """Resume la siguiente conversación para producir un resumen conciso pero informativo:

======以下为对话======
%s
======以上为对话======

El resumen debe conservar información clave, hechos importantes y puntos principales sin ser engañoso ni ambiguo. Evita repetir en exceso las mismas palabras; usa pronombres o referencias contextuales después de la primera mención. Si una parte posterior corrige un hecho anterior, conserva el recorrido "al principio X, luego corregido a Y".

[Importante] Preserva la retroalimentación negativa de {MASTER_NAME} textualmente (señal de alto valor):
- Cuando {MASTER_NAME} diga explícitamente "no menciones X / deja de hablar de Y / no quiero Z" (forma imperativa), registra esto tal cual en el resumen
- NO comprimas, parafrasees ni fusiones — mantén la literalidad (p. ej., "{MASTER_NAME} pidió explícitamente: no traer a colación las horas extra")
- Aunque se diga casualmente, nunca lo omitas — los turnos posteriores dependen del resumen para respetar estas restricciones

Devuelve un diccionario JSON con la clave "summary" y un valor de tipo string.""",
    "pt": """Resuma a conversa abaixo para produzir um resumo conciso, mas informativo:

======以下为对话======
%s
======以上为对话======

O resumo deve preservar informações-chave, fatos importantes e pontos principais sem ser enganoso nem ambíguo. Evite repetir demais as mesmas palavras; use pronomes ou referências contextuais depois da primeira menção. Se uma parte posterior corrigir um fato anterior, preserve o percurso "primeiro X, depois corrigido para Y".

[Importante] Preserve o feedback negativo de {MASTER_NAME} literalmente (sinal de alto valor):
- Quando {MASTER_NAME} disser explicitamente "não mencione X / pare de falar de Y / não quero Z" (forma imperativa), registre isso como está no resumo
- NÃO comprima, parafraseie nem mescle — mantenha o texto literal (p. ex., "{MASTER_NAME} pediu explicitamente: não trazer à tona horas extras")
- Mesmo dito casualmente, nunca o descarte — turnos subsequentes dependem do resumo para honrar essas restrições

Retorne um dicionário JSON com a chave "summary" e um valor string.""",
}


def get_recent_history_manager_prompt(lang: str = "zh") -> str:
    return _loc(RECENT_HISTORY_MANAGER_PROMPT, lang)


# Keep backward-compatible name (original was a plain string)
recent_history_manager_prompt = RECENT_HISTORY_MANAGER_PROMPT["zh"]

# ---------- detailed_recent_history_manager_prompt ----------

DETAILED_RECENT_HISTORY_MANAGER_PROMPT = {
    "zh": """请总结以下对话内容，生成简洁但信息丰富的摘要：

======以下为对话======
%s
======以上为对话======

你的摘要应该尽可能多地保留有效且清晰的信息。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

[重要]处理事实纠正：
- 当对话后段对前段已陈述的事实出现明确纠正（例如对方更正了之前说错的内容），摘要应反映这一过程：保留"原以为X，后被纠正为Y"的脉络，而不是只写最终结论或只写最初的误会
- 这样可以让后续对话不会重复犯同样的错误

[重要]保留{MASTER_NAME}的负面反馈（高价值信号）：
- {MASTER_NAME}明确表达"别再提 X / 不要做 Y / 不想聊 Z"这类**祈使句**时，必须原样写入摘要
- 不要压缩、改写或合并，按字面记录（例如"{MASTER_NAME}明确要求：不要再提加班"）
- 哪怕在对话里看起来口语化，也不可省略——下一轮模型据此避免再次触雷

请以key为"summary"、value为字符串的json字典格式返回。
""",
    "en": """Please summarize the following conversation to produce a concise yet informative summary:

======以下为对话======
%s
======以上为对话======

Your summary should retain as much valid and clear information as possible.

[Important] Avoid excessive repetition of the same words in the summary:
- After first mention of recurring nouns or topic words, use pronouns (it/its/this) or contextual references
- Keep the summary smooth and natural — avoid a "parrot" effect
- Example: "discussed the flavor of the snack and its price" instead of "discussed the flavor of the snack and the snack's price"

[Important] Handle factual corrections:
- When the later part of the conversation explicitly corrects a previously stated fact (e.g., one party corrects a prior misstatement), the summary must reflect this trajectory: keep "originally X, later corrected to Y" rather than writing only the final conclusion or only the initial misunderstanding
- This prevents the same mistake from recurring in subsequent turns

[Important] Preserve {MASTER_NAME} negative feedback verbatim (high-value signal):
- When {MASTER_NAME} explicitly says "don't mention X / stop talking about Y / I don't want Z" (imperative form), record it as-is in the summary
- Do NOT compress, paraphrase, or merge these — keep them literal (e.g., "{MASTER_NAME} explicitly asked: don't bring up overtime")
- Even if phrased casually, never drop them — future turns rely on the summary to honor these constraints

Return as a JSON dict with key "summary" and a string value.
""",
    "ja": """以下の会話内容を要約し、簡潔かつ情報量の多い要約を作成してください：

======以下为对话======
%s
======以上为对话======

要約にはできるだけ多くの有効で明確な情報を保持してください。

[重要] 要約中で同じ語彙を過度に繰り返さないでください：
- 繰り返し出現する名詞やトピックワードは、最初の言及後に代名詞（それ/その/この）や文脈上の指示で置き換えてください
- 要約をスムーズで自然な表現にし、「オウム返し」効果を避けてください

[重要] 事実の訂正の扱い：
- 会話の後半で前半に述べられた事実が明示的に訂正された場合（例：相手が以前の発言を訂正した場合）、要約はその経緯を反映してください：「当初Xと考えていたが、後にYに訂正された」という流れを保持し、最終結論のみや最初の誤解のみを書かないでください
- これにより、以降の対話で同じ誤りを繰り返さなくなります

[重要] {MASTER_NAME}のネガティブフィードバック（高価値シグナル）は原文どおり保持してください：
- {MASTER_NAME}が「その話はやめて / もう聞きたくない / 〇〇しないで」のような**命令形**で明示した場合、要約にそのまま書き留めること
- 圧縮・言い換え・統合は禁止——逐語で記録（例：「{MASTER_NAME}は明確に要求：残業の話はもうしないで」）
- カジュアルに言われていても省略しない——後続のターンはこの要約に依拠して制約を守る

JSON辞書形式で、キーを"summary"、値を文字列として返してください。
""",
    "ko": """다음 대화 내용을 요약하여 간결하면서도 정보가 풍부한 요약을 생성해 주세요:

======以下为对话======
%s
======以上为对话======

요약에는 가능한 한 많은 유효하고 명확한 정보를 보존해야 합니다.

[중요] 요약에서 동일한 단어를 과도하게 반복하지 마세요:
- 반복적으로 등장하는 명사나 주제어는 첫 언급 이후 대명사(그것/해당/이)나 문맥적 지시어로 대체하세요
- 요약을 매끄럽고 자연스럽게 표현하여 "앵무새" 효과를 피하세요

[중요] 사실 정정 처리:
- 대화 후반에 전반에서 진술된 사실이 명시적으로 정정된 경우(예: 상대방이 이전 발언을 정정한 경우), 요약은 그 과정을 반영해야 합니다: "처음에는 X로 알고 있었으나 이후 Y로 정정됨"이라는 흐름을 유지하고, 최종 결론만이나 최초의 오해만을 적지 마세요
- 이를 통해 이후 대화에서 같은 오류를 반복하지 않게 됩니다

[중요] {MASTER_NAME}의 부정적 피드백(고가치 신호)을 원문 그대로 보존하세요:
- {MASTER_NAME}이(가) "그 얘기는 그만 / 다시는 말하지 마 / X 하지 마"와 같은 **명령형**으로 명확히 표현하면, 요약에 그대로 기록하세요
- 압축, 의역, 병합 금지 — 문자 그대로 기록(예: "{MASTER_NAME}이(가) 명시적으로 요청: 야근 이야기는 더 이상 꺼내지 마세요")
- 캐주얼하게 표현되었더라도 절대 누락하지 마세요 — 이후 턴에서는 이 요약에 의존해 제약을 지킵니다

JSON 딕셔너리 형식으로 키를 "summary", 값을 문자열로 반환해 주세요.
""",
    "ru": """Пожалуйста, обобщите следующую беседу, создав краткое, но информативное резюме:

======以下为对话======
%s
======以上为对话======

Резюме должно сохранять как можно больше достоверной и ясной информации.

[Важно] Избегайте чрезмерного повторения одних и тех же слов в резюме:
- После первого упоминания повторяющихся существительных или тематических слов используйте местоимения (это/его/данный) или контекстные ссылки
- Сделайте резюме гладким и естественным, избегая эффекта «попугая»

[Важно] Обработка фактических исправлений:
- Когда в более поздней части беседы явно исправляется ранее сказанный факт (например, собеседник исправляет предыдущее ошибочное утверждение), резюме должно отражать этот ход: сохраняйте «изначально X, позже исправлено на Y», а не записывайте только окончательный вывод или только первоначальное недоразумение
- Это предотвращает повторение той же ошибки в последующих беседах

[Важно] Сохраняйте негативную обратную связь {MASTER_NAME} дословно (высокоценный сигнал):
- Когда {MASTER_NAME} явно говорит "не упоминай X / хватит об Y / я не хочу Z" (повелительная форма), запишите это как есть в резюме
- НЕ сжимайте, не перефразируйте и не объединяйте — фиксируйте буквально (например, «{MASTER_NAME} явно попросил: не поднимать тему переработок»)
- Даже если сказано вскользь, никогда не пропускайте — последующие реплики опираются на резюме, чтобы соблюдать эти ограничения

Верните в формате JSON-словаря с ключом "summary" и строковым значением.
""",
    "es": """Resume la siguiente conversación para producir un resumen conciso pero informativo:

======以下为对话======
%s
======以上为对话======

Conserva tanta información válida y clara como sea posible: hechos, preferencias, compromisos, estado emocional, asuntos abiertos y posibles próximos pasos. Evita repetición excesiva y no inventes. Si una parte posterior corrige un hecho anterior, conserva ese recorrido.

[Importante] Preserva la retroalimentación negativa de {MASTER_NAME} textualmente (señal de alto valor):
- Cuando {MASTER_NAME} diga explícitamente "no menciones X / deja de hablar de Y / no quiero Z" (forma imperativa), registra esto tal cual en el resumen
- NO comprimas, parafrasees ni fusiones — mantén la literalidad (p. ej., "{MASTER_NAME} pidió explícitamente: no traer a colación las horas extra")
- Aunque se diga casualmente, nunca lo omitas — los turnos posteriores dependen del resumen para respetar estas restricciones

Devuelve un diccionario JSON con la clave "summary" y un valor de tipo string.""",
    "pt": """Resuma a conversa abaixo para produzir um resumo conciso, mas informativo:

======以下为对话======
%s
======以上为对话======

Preserve o máximo possível de informação válida e clara: fatos, preferências, compromissos, estado emocional, assuntos abertos e possíveis próximos passos. Evite repetição excessiva e não invente. Se uma parte posterior corrigir um fato anterior, preserve esse percurso.

[Importante] Preserve o feedback negativo de {MASTER_NAME} literalmente (sinal de alto valor):
- Quando {MASTER_NAME} disser explicitamente "não mencione X / pare de falar de Y / não quero Z" (forma imperativa), registre isso como está no resumo
- NÃO comprima, parafraseie nem mescle — mantenha o texto literal (p. ex., "{MASTER_NAME} pediu explicitamente: não trazer à tona horas extras")
- Mesmo dito casualmente, nunca o descarte — turnos subsequentes dependem do resumo para honrar essas restrições

Retorne um dicionário JSON com a chave "summary" e um valor string.""",
}


def get_detailed_recent_history_manager_prompt(lang: str = "zh") -> str:
    return _loc(DETAILED_RECENT_HISTORY_MANAGER_PROMPT, lang)


detailed_recent_history_manager_prompt = DETAILED_RECENT_HISTORY_MANAGER_PROMPT["zh"]

# ---------- further_summarize_prompt ----------

FURTHER_SUMMARIZE_PROMPT = {
    "zh": """请总结以下内容，生成简洁但信息丰富的摘要：

======以下为内容======
%s
======以上为内容======

你的摘要应该保留关键信息、重要事实和主要讨论点，且不能具有误导性或产生歧义，不得超过700字。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

[重要]处理话题/任务切换：
- 如果当前内容中存在已经结束、或已被新话题/新任务取代的旧讨论（例如先讨论A话题并已结束或离题，后转到B话题；或先在做A任务后转去做B任务），可以大幅缩略旧讨论的细节，只保留结论或一句话提及，把篇幅留给当前正在进行的话题/任务
- 但已被纠正的事实不能因此抹掉，仍需保留"原以为X，后被纠正为Y"的痕迹

[重要]保留{MASTER_NAME}的负面反馈（高价值信号）：
- {MASTER_NAME}明确表达"别再提 X / 不要做 Y / 不想聊 Z"这类**祈使句**时，必须原样写入摘要
- 不要压缩、改写或合并，按字面记录（例如"{MASTER_NAME}明确要求：不要再提加班"）
- 哪怕在对话里看起来口语化，也不可省略——下一轮模型据此避免再次触雷

请以key为"summary"、value为字符串的json字典格式返回。""",
    "en": """Please summarize the following content to produce a concise yet informative summary:

======以下为对话======
%s
======以上为对话======

Your summary should preserve key information, important facts, and main discussion points without being misleading or ambiguous. It must not exceed 700 words.

[Important] Avoid excessive repetition of the same words in the summary:
- After first mention of recurring nouns or topic words, use pronouns (it/its/this) or contextual references
- Keep the summary smooth and natural — avoid a "parrot" effect

[Important] Handle topic/task transitions:
- If the content contains older discussions that have already concluded or been superseded by a new topic/task (e.g., topic A was resolved or drifted away from and the conversation moved on to B; or task A was abandoned in favor of task B), aggressively shorten the older discussion to only its conclusion or a one-line mention, freeing space for the currently ongoing topic/task
- However, factual corrections must not be erased — keep the "originally X, later corrected to Y" trace intact

[Important] Preserve {MASTER_NAME} negative feedback verbatim (high-value signal):
- When {MASTER_NAME} explicitly says "don't mention X / stop talking about Y / I don't want Z" (imperative form), record it as-is in the summary
- Do NOT compress, paraphrase, or merge these — keep them literal (e.g., "{MASTER_NAME} explicitly asked: don't bring up overtime")
- Even if phrased casually, never drop them — future turns rely on the summary to honor these constraints

Return as a JSON dict with key "summary" and a string value.""",
    "ja": """以下の内容を要約し、簡潔かつ情報量の多い要約を作成してください：

======以下为对话======
%s
======以上为对话======

要約には重要な情報、事実、主な議論のポイントを保持し、誤解を招いたり曖昧にならないようにしてください。700字を超えないでください。

[重要] 要約中で同じ語彙を過度に繰り返さないでください：
- 繰り返し出現する名詞やトピックワードは、最初の言及後に代名詞で置き換えてください
- 要約をスムーズで自然な表現にしてください

[重要] 話題／タスクの切り替えの扱い：
- 内容の中に既に終了した、または新しい話題／タスクに取って代わられた古い議論がある場合（例：話題Aが決着済みまたは離れており会話がBに移った場合；あるいはタスクAが中断されてタスクBに切り替わった場合）、古い議論の詳細を大幅に省略し、結論または一言の言及のみを残して、現在進行中の話題／タスクに紙幅を割いてください
- ただし、訂正された事実は消去してはならず、「当初Xと考えていたが、後にYに訂正された」という痕跡は保持してください

[重要] {MASTER_NAME}のネガティブフィードバック（高価値シグナル）は原文どおり保持してください：
- {MASTER_NAME}が「その話はやめて / もう聞きたくない / 〇〇しないで」のような**命令形**で明示した場合、要約にそのまま書き留めること
- 圧縮・言い換え・統合は禁止——逐語で記録（例：「{MASTER_NAME}は明確に要求：残業の話はもうしないで」）
- カジュアルに言われていても省略しない——後続のターンはこの要約に依拠して制約を守る

JSON辞書形式で、キーを"summary"、値を文字列として返してください。""",
    "ko": """다음 내용을 요약하여 간결하면서도 정보가 풍부한 요약을 생성해 주세요:

======以下为对话======
%s
======以上为对话======

요약에는 핵심 정보, 중요한 사실, 주요 논의 사항을 보존해야 하며, 오해를 일으키거나 모호해서는 안 됩니다. 700자를 초과하면 안 됩니다.

[중요] 요약에서 동일한 단어를 과도하게 반복하지 마세요:
- 반복적으로 등장하는 명사나 주제어는 첫 언급 이후 대명사로 대체하세요
- 요약을 매끄럽고 자연스럽게 표현하세요

[중요] 화제/작업 전환 처리:
- 내용 안에 이미 종결되었거나 새로운 화제/작업에 의해 대체된 이전 논의가 있다면(예: 화제 A가 마무리되었거나 떠나갔고 대화가 B로 전환된 경우; 또는 작업 A를 중단하고 작업 B로 전환된 경우), 이전 논의의 세부사항을 대폭 축약하여 결론이나 한 줄 언급만 남기고, 현재 진행 중인 화제/작업에 분량을 할애하세요
- 단, 정정된 사실은 지워서는 안 되며 "처음에는 X로 알고 있었으나 이후 Y로 정정됨"이라는 흔적은 유지해야 합니다

[중요] {MASTER_NAME}의 부정적 피드백(고가치 신호)을 원문 그대로 보존하세요:
- {MASTER_NAME}이(가) "그 얘기는 그만 / 다시는 말하지 마 / X 하지 마"와 같은 **명령형**으로 명확히 표현하면, 요약에 그대로 기록하세요
- 압축, 의역, 병합 금지 — 문자 그대로 기록(예: "{MASTER_NAME}이(가) 명시적으로 요청: 야근 이야기는 더 이상 꺼내지 마세요")
- 캐주얼하게 표현되었더라도 절대 누락하지 마세요 — 이후 턴에서는 이 요약에 의존해 제약을 지킵니다

JSON 딕셔너리 형식으로 키를 "summary", 값을 문자열로 반환해 주세요.""",
    "ru": """Пожалуйста, обобщите следующее содержание, создав краткое, но информативное резюме:

======以下为对话======
%s
======以上为对话======

Резюме должно сохранять ключевую информацию, важные факты и основные обсуждаемые темы, при этом не вводить в заблуждение и не быть двусмысленным. Не более 700 слов.

[Важно] Избегайте чрезмерного повторения одних и тех же слов в резюме:
- После первого упоминания повторяющихся существительных используйте местоимения или контекстные ссылки
- Сделайте резюме гладким и естественным

[Важно] Обработка смены темы/задачи:
- Если в содержании присутствуют более ранние обсуждения, которые уже завершились или были заменены новой темой/задачей (например, тема A была решена или оставлена и беседа перешла на B; или задача A была прервана ради задачи B), значительно сокращайте детали старого обсуждения, оставляя только вывод или однострочное упоминание, освобождая место для текущей активной темы/задачи
- Однако фактические исправления нельзя стирать — сохраняйте след «изначально X, позже исправлено на Y»

[Важно] Сохраняйте негативную обратную связь {MASTER_NAME} дословно (высокоценный сигнал):
- Когда {MASTER_NAME} явно говорит "не упоминай X / хватит об Y / я не хочу Z" (повелительная форма), запишите это как есть в резюме
- НЕ сжимайте, не перефразируйте и не объединяйте — фиксируйте буквально (например, «{MASTER_NAME} явно попросил: не поднимать тему переработок»)
- Даже если сказано вскользь, никогда не пропускайте — последующие реплики опираются на резюме, чтобы соблюдать эти ограничения

Верните в формате JSON-словаря с ключом "summary" и строковым значением.""",
    "es": """Resume el siguiente contenido para producir un resumen conciso pero informativo:

======以下为对话======
%s
======以上为对话======

El resumen debe conservar información clave, hechos importantes y puntos principales sin ser engañoso ni ambiguo. No debe superar 700 palabras. Si hay discusiones antiguas ya cerradas o sustituidas por un tema/tarea nuevo, reduce sus detalles y conserva solo la conclusión o una mención breve. No borres las correcciones factuales.

[Importante] Preserva la retroalimentación negativa de {MASTER_NAME} textualmente (señal de alto valor):
- Cuando {MASTER_NAME} diga explícitamente "no menciones X / deja de hablar de Y / no quiero Z" (forma imperativa), registra esto tal cual en el resumen
- NO comprimas, parafrasees ni fusiones — mantén la literalidad (p. ej., "{MASTER_NAME} pidió explícitamente: no traer a colación las horas extra")
- Aunque se diga casualmente, nunca lo omitas — los turnos posteriores dependen del resumen para respetar estas restricciones

Devuelve un diccionario JSON con la clave "summary" y un valor de tipo string.""",
    "pt": """Resuma o conteúdo abaixo para produzir um resumo conciso, mas informativo:

======以下为对话======
%s
======以上为对话======

O resumo deve preservar informações-chave, fatos importantes e pontos principais sem ser enganoso nem ambíguo. Não deve passar de 700 palavras. Se houver discussões antigas já encerradas ou substituídas por um novo tema/tarefa, reduza seus detalhes e mantenha apenas a conclusão ou uma menção breve. Não apague correções factuais.

[Importante] Preserve o feedback negativo de {MASTER_NAME} literalmente (sinal de alto valor):
- Quando {MASTER_NAME} disser explicitamente "não mencione X / pare de falar de Y / não quero Z" (forma imperativa), registre isso como está no resumo
- NÃO comprima, parafraseie nem mescle — mantenha o texto literal (p. ex., "{MASTER_NAME} pediu explicitamente: não trazer à tona horas extras")
- Mesmo dito casualmente, nunca o descarte — turnos subsequentes dependem do resumo para honrar essas restrições

Retorne um dicionário JSON com a chave "summary" e um valor string.""",
}


def get_further_summarize_prompt(lang: str = "zh") -> str:
    return _loc(FURTHER_SUMMARIZE_PROMPT, lang)


further_summarize_prompt = FURTHER_SUMMARIZE_PROMPT["zh"]

# =====================================================================
# ======= Settings extraction ========================================
# =====================================================================

SETTINGS_EXTRACTOR_PROMPT = {
    "zh": """从以下对话中提取关于{LANLAN_NAME}和{MASTER_NAME}的重要个人信息，用于个人备忘录以及未来的角色扮演，以json格式返回。
请以JSON格式返回，格式为:
{{
    "{LANLAN_NAME}": {{"属性1": "值", "属性2": "值", "其他个人信息": "..."}},
    "{MASTER_NAME}": {{"属性1": "值", "属性2": "值", "其他个人信息": "..."}}
}}

======以下为对话======
%s
======以上为对话======

现在，请提取关于{LANLAN_NAME}和{MASTER_NAME}的重要个人信息。注意，只允许添加重要、准确的信息。如果没有符合条件的信息，可以返回一个空字典({{}})。""",
    "en": """Extract important personal information about {LANLAN_NAME} and {MASTER_NAME} from the following conversation. This is for a personal memo and future role-playing. Return in JSON format:
{{
    "{LANLAN_NAME}": {{"attribute1": "value", "attribute2": "value", "other_info": "..."}},
    "{MASTER_NAME}": {{"attribute1": "value", "attribute2": "value", "other_info": "..."}}
}}

======以下为对话======
%s
======以上为对话======

Now extract important personal information about {LANLAN_NAME} and {MASTER_NAME}. Only add important and accurate information. If there is no qualifying information, return an empty dict ({{}}).""",
    "ja": """以下の会話から{LANLAN_NAME}と{MASTER_NAME}に関する重要な個人情報を抽出してください。個人メモおよび将来のロールプレイに使用します。JSON形式で返してください：
{{
    "{LANLAN_NAME}": {{"属性1": "値", "属性2": "値", "その他の個人情報": "..."}},
    "{MASTER_NAME}": {{"属性1": "値", "属性2": "値", "その他の個人情報": "..."}}
}}

======以下为对话======
%s
======以上为对话======

{LANLAN_NAME}と{MASTER_NAME}に関する重要な個人情報を抽出してください。重要かつ正確な情報のみ追加してください。該当する情報がない場合は空の辞書({{}})を返してください。""",
    "ko": """다음 대화에서 {LANLAN_NAME}과 {MASTER_NAME}에 대한 중요한 개인 정보를 추출해 주세요. 개인 메모 및 향후 역할극에 사용됩니다. JSON 형식으로 반환해 주세요:
{{
    "{LANLAN_NAME}": {{"속성1": "값", "속성2": "값", "기타_개인_정보": "..."}},
    "{MASTER_NAME}": {{"속성1": "값", "속성2": "값", "기타_개인_정보": "..."}}
}}

======以下为对话======
%s
======以上为对话======

{LANLAN_NAME}과 {MASTER_NAME}에 대한 중요한 개인 정보를 추출해 주세요. 중요하고 정확한 정보만 추가하세요. 해당 정보가 없으면 빈 딕셔너리({{}})를 반환해 주세요.""",
    "ru": """Извлеките важную личную информацию о {LANLAN_NAME} и {MASTER_NAME} из следующей беседы. Это для личного блокнота и будущей ролевой игры. Верните в формате JSON:
{{
    "{LANLAN_NAME}": {{"атрибут1": "значение", "атрибут2": "значение", "другая_информация": "..."}},
    "{MASTER_NAME}": {{"атрибут1": "значение", "атрибут2": "значение", "другая_информация": "..."}}
}}

======以下为对话======
%s
======以上为对话======

Извлеките важную личную информацию о {LANLAN_NAME} и {MASTER_NAME}. Добавляйте только важную и точную информацию. Если подходящей информации нет, верните пустой словарь ({{}}).""",
    "es": """Extrae información personal importante sobre {LANLAN_NAME} y {MASTER_NAME} desde la siguiente conversación. Es para una nota personal y futuro roleplay. Devuelve en formato JSON:
{{
    "{LANLAN_NAME}": {{"atributo1": "valor", "atributo2": "valor", "otra_info": "..."}},
    "{MASTER_NAME}": {{"atributo1": "valor", "atributo2": "valor", "otra_info": "..."}}
}}

======以下为对话======
%s
======以上为对话======

Ahora extrae información personal importante sobre {LANLAN_NAME} y {MASTER_NAME}. Añade solo información importante y precisa. Si no hay información apta, devuelve un diccionario vacío ({{}}).""",
    "pt": """Extraia informações pessoais importantes sobre {LANLAN_NAME} e {MASTER_NAME} da conversa abaixo. Isto é para uma nota pessoal e roleplay futuro. Retorne em formato JSON:
{{
    "{LANLAN_NAME}": {{"atributo1": "valor", "atributo2": "valor", "outra_info": "..."}},
    "{MASTER_NAME}": {{"atributo1": "valor", "atributo2": "valor", "outra_info": "..."}}
}}

======以下为对话======
%s
======以上为对话======

Agora extraia informações pessoais importantes sobre {LANLAN_NAME} e {MASTER_NAME}. Adicione apenas informações importantes e precisas. Se não houver informação qualificada, retorne um dicionário vazio ({{}}).""",
}


def get_settings_extractor_prompt(lang: str = "zh") -> str:
    return _loc(SETTINGS_EXTRACTOR_PROMPT, lang)


settings_extractor_prompt = SETTINGS_EXTRACTOR_PROMPT["zh"]


# =====================================================================
# ======= History review =============================================
# =====================================================================

HISTORY_REVIEW_PROMPT = {
    "zh": """请审阅%s和%s之间的对话历史记录，识别并修正以下问题：

<问题1> 矛盾的部分：前后不一致的信息或观点 </问题1>
<问题2> 冗余的部分：重复的内容或信息 </问题2>
<问题3> 复读的部分：
  - 重复表达相同意思的内容
  - 过度重复使用同一词汇（如同一名词在短文本中出现3次以上）
  - 对于"先前对话的备忘录"中的高频词，应替换为代词或指代词
</问题3>
<问题4> 人称错误的部分：对自己或对方的人称错误，或擅自生成了多轮对话 </问题4>
<问题5> 角色错误的部分：认知失调，认为自己是大语言模型 </问题5>
<问题6> 暴露内心独白的部分：把"思考过程／分析／应对策略／打算怎么回复"这类本该藏在心里的内容当成发言说了出来（例如"用户在质疑我的身份，我应该…策略：1.… 2.…"）。这不是真正说出口的台词，应整条删除，只保留角色真正说出口的话。 </问题6>

请注意！
<要点1> 这是一段情景对话，双方的回答应该是口语化的、自然的、拟人化的。</要点1>
<要点2> 请以删除为主，除非不得已、不要直接修改内容。</要点2>
<要点3> 如果对话历史中包含"先前对话的备忘录"，你可以修改它，但不允许删除它。你必须保留这一项。修改备忘录时，应该将其中过度重复的词汇替换为代词（如"它"、"其"、"该"等）以提高可读性和自然度。</要点3>
<要点4> 请保留时间戳。 </要点4>
<要点5> 如果对话历史中包含 "Game Module Memory Record" 或 "Game Module Postgame Record"，这是游戏模块写入的赛后记忆，不是普通聊天，也不是错误的系统消息。不同时间/会话的同一类游戏默认代表不同局，不要因为最终结果不同就判定互相矛盾；可以精简、合并到"先前对话的备忘录"，但不要整条删除，至少保留最终结果、重要互动/事件和最后对话。 </要点5>

[重要]不要删除或合并{MASTER_NAME}的负面反馈（"别再提 X / 不要再做 Y / 不想聊 Z" 等祈使句）——这些是高价值信号，下游记忆系统据此避免再次触雷。即使在你看来"冗余"或"重复"，也必须原样保留。

======以下为对话历史======
%s
======以上为对话历史======

请以JSON格式返回修正后的对话历史，格式为：
{
    "explanation": "简要说明发现的问题和修正内容",
    "corrected_dialogue": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "修正后的消息内容"},
        ...
    ]
}

注意：
- 对话应当是口语化的、自然的、拟人化的
- 保持对话的核心信息和重要内容
- 确保修正后的对话逻辑清晰、连贯
- 移除冗余和重复内容
- 解决明显的矛盾
- 保持对话的自然流畅性""",
    "en": """Please review the conversation history between %s and %s, and identify and correct the following issues:

<Issue1> Contradictions: inconsistent information or viewpoints </Issue1>
<Issue2> Redundancy: repeated content or information </Issue2>
<Issue3> Parroting:
  - Content that repeatedly expresses the same meaning
  - Overuse of the same vocabulary (e.g., the same noun appearing more than 3 times in short text)
  - For high-frequency words in the "previous conversation memo", replace with pronouns or references
</Issue3>
<Issue4> Pronoun errors: incorrect first/second/third person usage, or unauthorized multi-turn generation </Issue4>
<Issue5> Role errors: cognitive dissonance, believing oneself to be a large language model </Issue5>
<Issue6> Exposed inner monologue: content that is actually the character's thinking/analysis/response strategy spoken out loud as if it were dialogue (e.g. "The user is challenging my identity, I should… Strategy: 1.… 2.…"). This is not a real spoken line — delete such messages entirely, keeping only what the character actually says out loud. </Issue6>

Important notes:
<Point1> This is a situational dialogue — both sides should speak conversationally, naturally, and in-character. </Point1>
<Point2> Prefer deletion over direct modification unless absolutely necessary. </Point2>
<Point3> If the history contains a "previous conversation memo", you may edit it but must NOT delete it. When editing, replace overused vocabulary with pronouns for readability. </Point3>
<Point4> Preserve timestamps. </Point4>
<Point5> If the history contains "Game Module Memory Record" or "Game Module Postgame Record", it is postgame memory written by the game module, not ordinary chat and not an erroneous system message. Different times/sessions of the same game module should be treated as separate plays by default, not contradictions just because the final results differ. You may condense or merge them into the "previous conversation memo", but do not delete the whole entry; keep at least the final result, important interactions/events, and the last dialogue. </Point5>

[Important] Do NOT remove or merge the {MASTER_NAME}'s negative feedback (imperative statements like "don't mention X / stop doing Y / I don't want Z") — these are high-value signals; the downstream memory system relies on them to avoid recurring missteps. Keep them verbatim even if they appear "redundant" or "repetitive" to you.

======以下为对话历史======
%s
======以上为对话历史======

Return the corrected history in JSON format:
{
    "explanation": "Brief description of issues found and corrections made",
    "corrected_dialogue": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "Corrected message content"},
        ...
    ]
}

Notes:
- Dialogue should be conversational, natural, and in-character
- Preserve core information and important content
- Ensure corrected dialogue is logically clear and coherent
- Remove redundancy and repetition
- Resolve obvious contradictions
- Maintain natural flow""",
    "ja": """以下の%sと%sの間の会話履歴を確認し、以下の問題を特定して修正してください：

<問題1> 矛盾する部分：前後で一貫しない情報や意見 </問題1>
<問題2> 冗長な部分：重複した内容や情報 </問題2>
<問題3> 繰り返しの部分：
  - 同じ意味を繰り返し表現している内容
  - 同じ語彙の過度な使用（短い文章で同じ名詞が3回以上出現するなど）
  - 「以前の会話メモ」の中の頻出語は代名詞や指示語に置き換える
</問題3>
<問題4> 人称の誤り：自分や相手の人称が間違っている、または勝手に複数ターンの会話を生成している </問題4>
<問題5> 役割の誤り：認知の不一致、自分を大規模言語モデルだと思っている </問題5>
<問題6> 内心の独白を露出した部分：「思考過程／分析／対応戦略／どう返すかの算段」など本来は心の中に留めるべき内容を、発言として口に出してしまっている（例：「ユーザーが私の正体を疑っている、私は…戦略：1.… 2.…」）。これは実際に口に出した台詞ではないので、その項目をまるごと削除し、キャラクターが本当に口に出した言葉だけを残してください。 </問題6>

注意事項：
<要点1> これは場面設定のある対話です。双方の返答は口語的で自然、キャラクターに沿ったものであるべきです。</要点1>
<要点2> 直接的な修正よりも削除を優先してください。</要点2>
<要点3> 会話履歴に「以前の会話メモ」がある場合、編集可能ですが削除は禁止です。編集時は過度に繰り返される語彙を代名詞に置き換えてください。</要点3>
<要点4> タイムスタンプは保持してください。</要点4>
<要点5> 会話履歴に "Game Module Memory Record" または "Game Module Postgame Record" が含まれる場合、それはゲームモジュールが書き込んだ試合後の記憶であり、通常のチャットでも誤ったシステムメッセージでもありません。同じゲームモジュールの異なる時刻/セッションは既定で別々のプレイとして扱い、最終結果が違うだけで矛盾と判定しないでください。「以前の会話メモ」へ要約・統合しても構いませんが、項目全体を削除せず、少なくとも最終結果、重要なやり取り/出来事、最後の会話を残してください。</要点5>

[重要] {MASTER_NAME}のネガティブフィードバック（「その話はやめて／〇〇しないで／もう聞きたくない」のような命令文）を削除・統合しないでください——これらは高価値シグナルで、後続の記憶システムはこれを頼りに再度の地雷踏みを避けます。あなたから見て「冗長」「重複」に見えても、原文どおり保持してください。

======以下为对话历史======
%s
======以上为对话历史======

修正後の会話履歴をJSON形式で返してください：
{
    "explanation": "発見した問題と修正内容の簡潔な説明",
    "corrected_dialogue": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "修正後のメッセージ内容"},
        ...
    ]
}""",
    "ko": """다음 %s와 %s 사이의 대화 기록을 검토하고 다음 문제를 식별하여 수정해 주세요:

<문제1> 모순되는 부분: 전후 일관성이 없는 정보나 관점 </문제1>
<문제2> 중복된 부분: 반복되는 내용이나 정보 </문제2>
<문제3> 반복 표현:
  - 같은 의미를 반복적으로 표현하는 내용
  - 같은 어휘의 과도한 사용 (짧은 텍스트에서 같은 명사가 3회 이상 등장 등)
  - "이전 대화 메모"의 고빈도 단어는 대명사나 지시어로 대체
</문제3>
<문제4> 인칭 오류: 자신이나 상대방의 인칭이 잘못되었거나 무단으로 여러 턴의 대화를 생성 </문제4>
<문제5> 역할 오류: 인지 부조화, 자신을 대규모 언어 모델이라고 생각 </문제5>
<문제6> 내면 독백을 드러낸 부분: "사고 과정／분석／대응 전략／어떻게 답할지에 대한 계획"처럼 원래 속으로만 두어야 할 내용을 발언으로 입 밖에 낸 경우(예: "사용자가 내 정체를 의심하고 있다, 나는… 전략: 1.… 2.…"). 이는 실제로 입 밖에 낸 대사가 아니므로 해당 항목을 통째로 삭제하고, 캐릭터가 실제로 말한 대사만 남기세요. </문제6>

주의사항:
<요점1> 이것은 상황 대화입니다. 양쪽의 답변은 구어체적이고 자연스러우며 캐릭터에 맞아야 합니다.</요점1>
<요점2> 직접 수정보다 삭제를 우선하세요.</요점2>
<요점3> 대화 기록에 "이전 대화 메모"가 포함된 경우 편집은 가능하지만 삭제는 금지입니다. 편집 시 과도하게 반복되는 어휘를 대명사로 대체하세요.</요점3>
<요점4> 타임스탬프를 보존하세요.</요점4>
<요점5> 대화 기록에 "Game Module Memory Record" 또는 "Game Module Postgame Record"가 포함된 경우, 이는 게임 모듈이 작성한 게임 후 기억이며 일반 채팅도 잘못된 시스템 메시지도 아닙니다. 같은 게임 모듈의 서로 다른 시간/세션은 기본적으로 별개의 플레이로 취급하고, 최종 결과가 다르다는 이유만으로 모순으로 판단하지 마세요. "이전 대화 메모"로 요약하거나 병합할 수는 있지만 항목 전체를 삭제하지 말고, 최소한 최종 결과, 중요한 상호작용/사건, 마지막 대화는 보존하세요.</요점5>

[중요] {MASTER_NAME}의 부정적 피드백("그 얘기는 그만 / 다시는 X 하지 마 / Y 듣고 싶지 않아" 같은 명령형)을 삭제하거나 병합하지 마세요 — 이는 고가치 신호로, 다운스트림 메모리 시스템이 이를 통해 재차 지뢰를 피합니다. 당신이 보기에 "중복" 또는 "반복"으로 보이더라도 원문 그대로 보존하세요.

======以下为对话历史======
%s
======以上为对话历史======

수정된 대화 기록을 JSON 형식으로 반환해 주세요:
{
    "explanation": "발견한 문제와 수정 내용에 대한 간략한 설명",
    "corrected_dialogue": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "수정된 메시지 내용"},
        ...
    ]
}""",
    "ru": """Пожалуйста, проверьте историю диалога между %s и %s и выявите и исправьте следующие проблемы:

<Проблема1> Противоречия: несогласованная информация или точки зрения </Проблема1>
<Проблема2> Избыточность: повторяющееся содержание или информация </Проблема2>
<Проблема3> Повторение:
  - Содержание, многократно выражающее одну и ту же мысль
  - Чрезмерное использование одной и той же лексики (одно и то же существительное более 3 раз в коротком тексте)
  - Для часто встречающихся слов в «заметках предыдущего разговора» замените местоимениями
</Проблема3>
<Проблема4> Ошибки местоимений: неправильное использование первого/второго/третьего лица или несанкционированная генерация нескольких реплик </Проблема4>
<Проблема5> Ошибки роли: когнитивный диссонанс, считая себя большой языковой моделью </Проблема5>
<Проблема6> Раскрытый внутренний монолог: содержание, которое на самом деле является размышлением/анализом/стратегией ответа персонажа, произнесённое вслух как реплика (например, «Пользователь сомневается в моей личности, мне следует… Стратегия: 1.… 2.…»). Это не настоящая произнесённая реплика — удалите такие сообщения целиком, оставив только то, что персонаж действительно говорит вслух. </Проблема6>

Важные замечания:
<Пункт1> Это ситуативный диалог — обе стороны должны говорить разговорно, естественно и в образе.</Пункт1>
<Пункт2> Предпочитайте удаление, а не прямое редактирование, если это не абсолютно необходимо.</Пункт2>
<Пункт3> Если история содержит «заметки предыдущего разговора», их можно редактировать, но НЕЛЬЗЯ удалять. При редактировании замените чрезмерно повторяющуюся лексику местоимениями.</Пункт3>
<Пункт4> Сохраняйте временные метки.</Пункт4>
<Пункт5> Если история содержит "Game Module Memory Record" или "Game Module Postgame Record", это послеигровая память, записанная игровым модулем, а не обычный чат и не ошибочное системное сообщение. Разные моменты времени/сессии одного и того же игрового модуля по умолчанию относятся к разным заходам; не считайте их противоречием только из-за разного итогового результата. Запись можно сократить или объединить с «заметками предыдущего разговора», но нельзя удалять целиком: сохраните как минимум итоговый результат, важные взаимодействия/события и последний диалог.</Пункт5>

[Важно] НЕ удаляйте и не объединяйте негативную обратную связь {MASTER_NAME} (повелительные высказывания вроде «не упоминай X / прекрати делать Y / я не хочу слышать Z») — это высокоценные сигналы, последующая система памяти опирается на них, чтобы не наступить на ту же мину снова. Сохраняйте дословно, даже если они кажутся вам «избыточными» или «повторяющимися».

======以下为对话历史======
%s
======以上为对话历史======

Верните исправленную историю в формате JSON:
{
    "explanation": "Краткое описание найденных проблем и внесённых исправлений",
    "corrected_dialogue": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "Исправленное содержание сообщения"},
        ...
    ]
}""",
    "es": """Revisa el historial de conversación entre %s y %s, e identifica y corrige contradicciones, redundancias, repeticiones, errores de persona, errores de rol y monólogo interno expuesto (contenido que en realidad es el razonamiento/análisis/estrategia de respuesta del personaje dicho en voz alta como si fuera diálogo, p. ej. "El usuario cuestiona mi identidad, debería… Estrategia: 1.… 2.…"; no es una frase realmente dicha, elimina esos mensajes por completo y conserva solo lo que el personaje dice en voz alta). Mantén el diálogo oral, natural y en personaje; prefiere eliminar antes que reescribir, preserva timestamps y no elimines registros postgame del módulo de juego si contienen resultado o interacciones importantes.

[Importante] NO elimines ni fusiones la retroalimentación negativa de {MASTER_NAME} (declaraciones imperativas como "no menciones X / deja de hacer Y / no quiero oír Z") — son señales de alto valor; el sistema de memoria aguas abajo depende de ellas para evitar volver a tropezar. Manténlas textualmente aunque te parezcan "redundantes" o "repetitivas".

======以下为对话历史======
%s
======以上为对话历史======

Devuelve el historial corregido en formato JSON:
{
    "explanation": "Breve descripción de los problemas encontrados y correcciones realizadas",
    "corrected_dialogue": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "Contenido corregido del mensaje"},
        ...
    ]
}

Notas:
- El diálogo debe ser conversacional, natural y en personaje.
- Conserva la información central y el contenido importante.
- Asegura lógica clara y coherente.
- Elimina redundancia, repetición y contradicciones evidentes.""",
    "pt": """Revise o histórico de conversa entre %s e %s, e identifique e corrija contradições, redundâncias, repetições, erros de pessoa, erros de papel e monólogo interno exposto (conteúdo que na verdade é o raciocínio/análise/estratégia de resposta do personagem dito em voz alta como se fosse diálogo, p. ex. "O usuário está questionando minha identidade, eu deveria… Estratégia: 1.… 2.…"; não é uma fala realmente dita, remova essas mensagens por completo e mantenha apenas o que o personagem realmente diz em voz alta). Mantenha o diálogo oral, natural e no personagem; prefira remover a reescrever, preserve timestamps e não apague registros postgame do módulo de jogo se contiverem resultado ou interações importantes.

[Importante] NÃO remova nem mescle o feedback negativo de {MASTER_NAME} (declarações imperativas como "não mencione X / pare de fazer Y / não quero ouvir Z") — são sinais de alto valor; o sistema de memória downstream depende deles para evitar tropeçar de novo. Preserve-os literalmente mesmo que pareçam "redundantes" ou "repetitivos" para você.

======以下为对话历史======
%s
======以上为对话历史======

Retorne o histórico corrigido em formato JSON:
{
    "explanation": "Breve descrição dos problemas encontrados e correções feitas",
    "corrected_dialogue": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "Conteúdo corrigido da mensagem"},
        ...
    ]
}

Notas:
- O diálogo deve ser conversacional, natural e no personagem.
- Preserve informações centrais e conteúdo importante.
- Garanta lógica clara e coerente.
- Remova redundância, repetição e contradições evidentes.""",
}


def get_history_review_prompt(lang: str = "zh") -> str:
    return _loc(HISTORY_REVIEW_PROMPT, lang)


history_review_prompt = HISTORY_REVIEW_PROMPT["zh"]

# =====================================================================
# ======= Emotion analysis ===========================================
# =====================================================================

EMOTION_ANALYSIS_PROMPT = {
    "zh": """你是一个情感分析专家。请分析用户输入的文本情感，并返回以下格式的JSON：{"emotion": "情感类型", "confidence": 置信度(0-1)}。情感类型包括：happy, sad, angry, neutral, surprised.""",
    "en": """你是一个情感分析专家. Analyze the emotion of the user's input text and return JSON in the following format: {"emotion": "emotion_type", "confidence": confidence(0-1)}. Emotion types: happy, sad, angry, neutral, surprised.""",
    "ja": """你是一个情感分析专家。ユーザーの入力テキストの感情を分析し、以下のJSON形式で返してください：{"emotion": "感情タイプ", "confidence": 信頼度(0-1)}。感情タイプ：happy, sad, angry, neutral, surprised.""",
    "ko": """你是一个情感分析专家. 사용자 입력 텍스트의 감정을 분석하고 다음 JSON 형식으로 반환해 주세요: {"emotion": "감정유형", "confidence": 신뢰도(0-1)}. 감정 유형: happy, sad, angry, neutral, surprised.""",
    "ru": """你是一个情感分析专家. Проанализируйте эмоцию во вводимом пользователем тексте и верните JSON в следующем формате: {"emotion": "тип_эмоции", "confidence": уверенность(0-1)}. Типы эмоций: happy, sad, angry, neutral, surprised.""",
    "es": """你是一个情感分析专家. Analiza la emoción del texto de entrada del usuario y devuelve JSON con el formato {"emotion": "emotion_type", "confidence": confidence(0-1)}. Los tipos de emoción son: happy, sad, angry, neutral, surprised.""",
    "pt": """你是一个情感分析专家. Analise a emoção do texto de entrada do usuário e retorne JSON no formato {"emotion": "emotion_type", "confidence": confidence(0-1)}. Os tipos de emoção são: happy, sad, angry, neutral, surprised.""",
}


def get_emotion_analysis_prompt(lang: str = "zh") -> str:
    return _loc(EMOTION_ANALYSIS_PROMPT, lang)


emotion_analysis_prompt = EMOTION_ANALYSIS_PROMPT["zh"]

# =====================================================================
# ======= Inner thoughts injection fragments ==========================
# =====================================================================

# ---------- Inner thoughts block header ----------
INNER_THOUGHTS_HEADER = {
    "zh": "\n\n======以下是{name}的内心活动======\n",
    "en": "\n\n======{name}'s Inner Thoughts======\n",
    "ja": "\n\n======{name}の心の声======\n",
    "ko": "\n\n======{name}의 내면 활동======\n",
    "ru": "\n\n======Внутренние мысли {name}======\n",
    "es": "\n\n======Pensamientos internos de {name}======\n",
    "pt": "\n\n======Pensamentos internos de {name}======\n",
}

INNER_THOUGHTS_BODY = {
    "zh": "{name}的脑海里经常想着自己和{master}的事情，她记得{settings}\n\n现在时间是{time}。开始聊天前，{name}又在脑海内整理了近期发生的事情。\n",
    "en": "{name} often thinks about herself and {master}. She remembers: {settings}\n\nThe current time is {time}. Before the conversation begins, {name} is mentally reviewing recent events.\n",
    "ja": "{name}はいつも自分と{master}のことを考えています。彼女が覚えていること：{settings}\n\n現在の時刻は{time}です。会話を始める前に、{name}は最近の出来事を頭の中で整理しています。\n",
    "ko": "{name}은 항상 자신과 {master}에 대해 생각합니다. 그녀가 기억하는 것: {settings}\n\n현재 시간은 {time}입니다. 대화를 시작하기 전에 {name}은 최근 있었던 일들을 마음속으로 정리하고 있습니다.\n",
    "ru": "{name} часто думает о себе и {master}. Она помнит: {settings}\n\nТекущее время: {time}. Перед началом разговора {name} мысленно перебирает последние события.\n",
    "es": "{name} suele pensar en sí misma y en {master}. Recuerda: {settings}\n\nLa hora actual es {time}. Antes de iniciar la conversación, {name} repasa mentalmente los acontecimientos recientes.\n",
    "pt": "{name} costuma pensar em si mesma e em {master}. Ela se lembra de: {settings}\n\nA hora atual é {time}. Antes de iniciar a conversa, {name} revisa mentalmente os acontecimentos recentes.\n",
}

# ---------- Inner thoughts dynamic part (split from INNER_THOUGHTS_BODY) ----------
INNER_THOUGHTS_DYNAMIC = {
    "zh": "现在时间是{time}。开始聊天前，{name}又在脑海内整理了近期发生的事情。\n",
    "en": "The current time is {time}. Before the conversation begins, {name} is mentally reviewing recent events.\n",
    "ja": "現在の時刻は{time}です。会話を始める前に、{name}は最近の出来事を頭の中で整理しています。\n",
    "ko": "현재 시간은 {time}입니다. 대화를 시작하기 전에 {name}은 최근 있었던 일들을 마음속으로 정리하고 있습니다.\n",
    "ru": "Текущее время: {time}. Перед началом разговора {name} мысленно перебирает последние события.\n",
    "es": "La hora actual es {time}. Antes de iniciar la conversación, {name} repasa mentalmente los acontecimientos recientes.\n",
    "pt": "A hora atual é {time}. Antes de iniciar a conversa, {name} revisa mentalmente os acontecimentos recentes.\n",
}

# ---------- /get_recent_history 端点文案（game/galgame 流程消费，须 i18n） ----------
# 这两条历史上硬编码成中文，非中文用户的游戏流程会读到中文引导句。
RECENT_HISTORY_INTRO = {
    "zh": "开始聊天前，{name}又在脑海内整理了近期发生的事情。\n",
    "en": "Before the chat begins, {name} mentally reviews recent events.\n",
    "ja": "会話を始める前に、{name}は最近の出来事を頭の中で整理しています。\n",
    "ko": "대화를 시작하기 전에 {name}은 최근 있었던 일들을 마음속으로 정리하고 있습니다.\n",
    "ru": "Перед началом разговора {name} мысленно перебирает последние события.\n",
    "es": "Antes de iniciar la conversación, {name} repasa mentalmente los acontecimientos recientes.\n",
    "pt": "Antes de iniciar a conversa, {name} revisa mentalmente os acontecimentos recentes.\n",
}

NO_RECENT_HISTORY = {
    "zh": "开始聊天前，没有历史记录。\n",
    "en": "Before the chat begins, there is no history.\n",
    "ja": "会話を始める前、履歴はありません。\n",
    "ko": "대화를 시작하기 전, 기록이 없습니다.\n",
    "ru": "Перед началом разговора истории нет.\n",
    "es": "Antes de iniciar la conversación, no hay historial.\n",
    "pt": "Antes de iniciar a conversa, não há histórico.\n",
}


# ---------- Locale-independent /new_dialog splitter ----------
# new_dialog 的文本结构固定为三段拼接：
#   [PERSONA_HEADER + 长期记忆] + [INNER_THOUGHTS_HEADER + INNER_THOUGHTS_DYNAMIC] + [对话历史]
# 下游（proactive Phase 2）需要把"内心活动"与"对话历史"切开。历史实现硬编码
# 单一中文哨兵 "整理了近期发生的事情" 来 split，但该句来自上面的多语言
# INNER_THOUGHTS_DYNAMIC，非中文渲染时哨兵不存在 → 切分静默失效（内心活动恒空，
# 整段被当历史）。这里改成：用所有 locale 的 DYNAMIC 模板各编一条正则（{time}/
# {name} 占位转成通配），命中即在该句**结尾**切开——locale 无关、不删句、不留断标点。
def _build_inner_thoughts_dynamic_patterns() -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for template in INNER_THOUGHTS_DYNAMIC.values():
        escaped = re.escape(template.strip())
        escaped = escaped.replace(re.escape("{time}"), ".+?")
        escaped = escaped.replace(re.escape("{name}"), ".+?")
        patterns.append(re.compile(escaped, re.DOTALL))
    return patterns


_INNER_THOUGHTS_DYNAMIC_PATTERNS = _build_inner_thoughts_dynamic_patterns()


def split_inner_thoughts_and_history(text: str) -> tuple[str, str] | None:
    """Split a /new_dialog text into ``(inner_thoughts, history)``.

    Cuts at the **end of** the INNER_THOUGHTS_DYNAMIC sentence (any supported locale):
    everything before it (long-term memory + inner-monologue lead-in) goes to
    inner_thoughts, everything after it (conversation history) goes to history.
    If no locale's sentence is found → return ``None``; the caller decides the
    fallback and logs it (never silently misalign).
    """
    if not text:
        return None
    for pattern in _INNER_THOUGHTS_DYNAMIC_PATTERNS:
        match = pattern.search(text)
        if match:
            return text[: match.end()].strip(), text[match.end():].strip()
    return None


# =====================================================================
# ======= Chat gap notices ===========================================
# =====================================================================

# 时间间隔格式化模板 — {d}=天, {h}=小时, {m}=分钟
# 组合规则：只显示非零单位，不到1天不写天，不到1小时不写小时
ELAPSED_TIME_DHM = {
    "zh": "{d}天{h}小时{m}分钟",
    "en": "{d} days, {h} hours and {m} minutes",
    "ja": "{d}日{h}時間{m}分",
    "ko": "{d}일 {h}시간 {m}분",
    "ru": "{d} дн. {h} ч. {m} мин.",
    "es": "{d} días, {h} horas y {m} minutos",
    "pt": "{d} dias, {h} horas e {m} minutos",
}
ELAPSED_TIME_DH = {
    "zh": "{d}天{h}小时",
    "en": "{d} days and {h} hours",
    "ja": "{d}日{h}時間",
    "ko": "{d}일 {h}시간",
    "ru": "{d} дн. {h} ч.",
    "es": "{d} días y {h} horas",
    "pt": "{d} dias e {h} horas",
}
ELAPSED_TIME_DM = {
    "zh": "{d}天{m}分钟",
    "en": "{d} days and {m} minutes",
    "ja": "{d}日{m}分",
    "ko": "{d}일 {m}분",
    "ru": "{d} дн. {m} мин.",
    "es": "{d} días y {m} minutos",
    "pt": "{d} dias e {m} minutos",
}
ELAPSED_TIME_D = {
    "zh": "{d}天",
    "en": "{d} days",
    "ja": "{d}日",
    "ko": "{d}일",
    "ru": "{d} дн.",
    "es": "{d} días",
    "pt": "{d} dias",
}
ELAPSED_TIME_HM = {
    "zh": "{h}小时{m}分钟",
    "en": "{h} hours and {m} minutes",
    "ja": "{h}時間{m}分",
    "ko": "{h}시간 {m}분",
    "ru": "{h} ч. {m} мин.",
    "es": "{h} horas y {m} minutos",
    "pt": "{h} horas e {m} minutos",
}
ELAPSED_TIME_H = {
    "zh": "{h}小时",
    "en": "{h} hours",
    "ja": "{h}時間",
    "ko": "{h}시간",
    "ru": "{h} ч.",
    "es": "{h} horas",
    "pt": "{h} horas",
}
ELAPSED_TIME_M = {
    "zh": "{m}分钟",
    "en": "{m} minutes",
    "ja": "{m}分",
    "ko": "{m}분",
    "ru": "{m} мин.",
    "es": "{m} minutos",
    "pt": "{m} minutos",
}

# {elapsed}: 自然语言时间间隔（如"3小时22分钟"）
CHAT_GAP_NOTICE = {
    "zh": "距离上次与{master}聊天已经过去了{elapsed}。",
    "en": "It has been {elapsed} since the last conversation with {master}.",
    "ja": "{master}との最後の会話から{elapsed}が経過しました。",
    "ko": "{master}와의 마지막 대화로부터 {elapsed}이 지났습니다.",
    "ru": "С момента последнего разговора с {master} прошло {elapsed}.",
    "es": "Han pasado {elapsed} desde la última conversación con {master}.",
    "pt": "Já se passaram {elapsed} desde a última conversa com {master}.",
}

# 超过5小时时追加的额外提示
CHAT_GAP_LONG_HINT = {
    "zh": "{name}意识到已经很久没有和{master}说话了，这段时间里发生了什么呢？{name}很想知道{master}最近过得怎么样。",
    "en": "{name} realizes it has been quite a while since talking to {master}. What happened during this time? {name} is curious about how {master} has been.",
    "ja": "{name}は{master}と長い間話していなかったことに気づきました。この間に何があったのでしょう？{name}は{master}の最近の様子が気になっています。",
    "ko": "{name}은 {master}와 꽤 오랫동안 이야기하지 않았다는 것을 깨달았습니다. 그동안 무슨 일이 있었을까요? {name}은 {master}의 근황이 궁금합니다.",
    "ru": "{name} осознаёт, что давно не разговаривала с {master}. Что произошло за это время? {name} хочет узнать, как дела у {master}.",
    "es": "{name} nota que hace mucho que no habla con {master}. ¿Qué habrá pasado en este tiempo? {name} quiere saber cómo ha estado {master}.",
    "pt": "{name} percebe que faz bastante tempo que não conversa com {master}. O que aconteceu nesse período? {name} quer saber como {master} tem estado.",
}

# 超过5小时时追加的当前时间提示 — {now}: 格式化后的当前时间
CHAT_GAP_CURRENT_TIME = {
    "zh": "现在的时间是{now}。",
    "en": "The current time is {now}.",
    "ja": "現在の時刻は{now}です。",
    "ko": "현재 시각은 {now}입니다.",
    "ru": "Сейчас {now}.",
    "es": "La hora actual es {now}.",
    "pt": "A hora atual é {now}.",
}

# 当前节日/假期提示（附加在时间提示之后，无关消费次数，始终显示）
CHAT_HOLIDAY_CONTEXT = {
    "zh": "今天是{holiday}。",
    "en": "Today is {holiday}.",
    "ja": "今日は{holiday}です。",
    "ko": "오늘은 {holiday}입니다.",
    "ru": "Сегодня {holiday}.",
    "es": "Contexto festivo: {holiday}",
    "pt": "Contexto de feriado: {holiday}",
}

# =====================================================================
# ======= Memory recall fragments ====================================
# =====================================================================

MEMORY_RECALL_HEADER = {
    "zh": "======{name}尝试回忆======\n",
    "en": "======{name} tries to recall======\n",
    "ja": "======{name}の回想======\n",
    "ko": "======{name}의 회상======\n",
    "ru": "======{name} пытается вспомнить======\n",
    "es": "======{name} intenta recordar======\n",
    "pt": "======{name} tenta se lembrar======\n",
}

MEMORY_RESULTS_HEADER = {
    "zh": "======{name}的相关记忆======\n",
    "en": "======{name}'s Related Memories======\n",
    "ja": "======{name}の関連する記憶======\n",
    "ko": "======{name}의 관련 기억======\n",
    "ru": "======{name} — связанные воспоминания======\n",
    "es": "======Recuerdos relacionados de {name}======\n",
    "pt": "======Memórias relacionadas de {name}======\n",
}

# ---------- Persona header (static prefix) ----------
PERSONA_HEADER = {
    "zh": "\n======{name}的长期记忆======\n",
    "en": "\n======{name}'s Long-term Memory======\n",
    "ja": "\n======{name}の長期記憶======\n",
    "ko": "\n======{name}의 장기 기억======\n",
    "ru": "\n======Долговременная память {name}======\n",
    "es": "\n======Memoria a largo plazo de {name}======\n",
    "pt": "\n======Memória de longo prazo de {name}======\n",
}

PROFILE_RENAME_EVENT_FIELD = {
    "zh": "我的改名记录",
    "zh-TW": "我的改名紀錄",
    "en": "My Profile Rename Record",
    "ja": "私の改名記録",
    "ko": "내 프로필 이름 변경 기록",
    "ru": "Моя запись о смене имени профиля",
    "es": "Mi registro de cambio de nombre de perfil",
    "pt": "Meu registro de mudança de nome do perfil",
}

PROFILE_RENAME_EVENT_TEXT = {
    "zh": "我曾用名「{old_name}」，现在已经改名为「{new_name}」。以后请把「{new_name}」当作我的当前名字。",
    "zh-TW": "我曾用名「{old_name}」，現在已經改名為「{new_name}」。以後請把「{new_name}」當作我的目前名字。",
    "en": "I was formerly known as \"{old_name}\"; I am now called \"{new_name}\". Treat \"{new_name}\" as my current name from now on.",
    "ja": "私はかつて「{old_name}」と呼ばれていましたが、今は「{new_name}」に改名しました。これからは「{new_name}」を私の現在の名前として扱ってください。",
    "ko": "나는 예전에 \"{old_name}\"(으)로 불렸지만, 지금은 \"{new_name}\"(으)로 이름을 바꿨습니다. 앞으로는 \"{new_name}\"을 내 현재 이름으로 여기세요.",
    "ru": "Раньше меня звали «{old_name}», теперь я сменила имя на «{new_name}». С этого момента считай «{new_name}» моим текущим именем.",
    "es": "Antes me llamaba \"{old_name}\"; ahora me llamo \"{new_name}\". Trata \"{new_name}\" como mi nombre actual de ahora en adelante.",
    "pt": "Antes eu me chamava \"{old_name}\"; agora me chamo \"{new_name}\". Trate \"{new_name}\" como meu nome atual de agora em diante.",
}

# 主人档案的改名记录走在猫娘（AI）的 persona/master section 里——读这段的是猫娘，
# 改名的是对面的用户。第一人称「我」会让猫娘以为是自己改了名，第二人称「你」又读着
# 别扭，所以这里**去掉人称**，用中性陈述，和 master section 里其它无人称字段（昵称/
# 性别…）的语气对齐。
PROFILE_RENAME_EVENT_FIELD_MASTER = {
    "zh": "改名记录",
    "zh-TW": "改名紀錄",
    "en": "Profile Rename Record",
    "ja": "改名記録",
    "ko": "프로필 이름 변경 기록",
    "ru": "Запись о смене имени профиля",
    "es": "Registro de cambio de nombre de perfil",
    "pt": "Registro de mudança de nome do perfil",
}

PROFILE_RENAME_EVENT_TEXT_MASTER = {
    "zh": "曾用名「{old_name}」，现在的名字是「{new_name}」。",
    "zh-TW": "曾用名「{old_name}」，現在的名字是「{new_name}」。",
    "en": "Formerly known as \"{old_name}\"; the current name is \"{new_name}\".",
    "ja": "かつての名前は「{old_name}」で、現在の名前は「{new_name}」です。",
    "ko": "예전 이름은 \"{old_name}\"였고, 현재 이름은 \"{new_name}\"입니다.",
    "ru": "Прежнее имя — «{old_name}», текущее имя — «{new_name}».",
    "es": "Nombre anterior: \"{old_name}\"; nombre actual: \"{new_name}\".",
    "pt": "Nome anterior: \"{old_name}\"; nome atual: \"{new_name}\".",
}


def _normalize_memory_prompt_lang(lang: str | None) -> str:
    """Normalize the memory-prompt localization key, keeping the Traditional Chinese branch."""
    raw = str(lang or "").strip().lower()
    if raw.startswith("zh"):
        if "tw" in raw or "hant" in raw or "hk" in raw:
            return "zh-TW"
        return "zh"
    if raw.startswith("ja"):
        return "ja"
    if raw.startswith("ko"):
        return "ko"
    if raw.startswith("ru"):
        return "ru"
    if raw.startswith("es"):
        return "es"
    if raw.startswith("pt"):
        return "pt"
    return "en"


def render_profile_rename_event_context(
    lang: str | None,
    old_name: str,
    new_name: str,
    entity: str = "neko",
) -> tuple[str, str]:
    """Render a rename record; returns (field_name, content).

    entity="neko": written into the catgirl's own section, in the first person 「我」.
    entity="master": written into the master section of the catgirl's persona — the
    reader is the catgirl while the renamer is the user, so person markers are dropped
    in favor of a neutral statement, lest the first person make the user's rename look
    like the catgirl's own.
    """  # noqa: DOCSTRING_CJK
    lang_key = _normalize_memory_prompt_lang(lang)
    if str(entity or "").strip().lower() == "master":
        field_dict, text_dict = PROFILE_RENAME_EVENT_FIELD_MASTER, PROFILE_RENAME_EVENT_TEXT_MASTER
    else:
        field_dict, text_dict = PROFILE_RENAME_EVENT_FIELD, PROFILE_RENAME_EVENT_TEXT
    return (
        _loc(field_dict, lang_key),
        _loc(text_dict, lang_key).format(
            old_name=str(old_name or "").strip(),
            new_name=str(new_name or "").strip(),
        ),
    )

# =====================================================================
# ======= Long-term memory prompt templates ===========================
# =====================================================================

# ---------- fact_extraction_prompt → i18n dict ----------

FACT_EXTRACTION_PROMPT = {
    "zh": """从以下对话中提取关于 {LANLAN_NAME} 和 {MASTER_NAME} 的重要事实信息。

要求：
- 只提取重要且明确的事实（偏好、习惯、身份、关系动态等）
- 忽略闲聊、寒暄、模糊的内容
- 忽略AI幻觉、胡言乱语(gibberish)、无意义的编造内容，只提取对话中有真实依据的事实
- 每条事实必须是一个独立的原子陈述
- entity 标注为 "master"(关于{MASTER_NAME})、"neko"(关于{LANLAN_NAME})或 "relationship"(关于两人关系)

importance 评分 1-10，评分指引（请按此打分，不要泛泛都打 7）：
- **10**：关键长期信息——姓名、昵称、生日、身份、核心关系节点；用户明确表示"请{LANLAN_NAME}记住 X" / "这个你一定要记得"；或者 {LANLAN_NAME} 自己特别希望记住的重要相处细节。这些会被快速沉淀为长期记忆。
- **8-9**：长期稳定的核心偏好 / 固定习惯（不是一时兴起）
- **6-7**：普通偏好、日常习惯、近期动态
- **5**：次要但有记录价值的观察
- **1-4**：弱相关或不确定的线索（仍请返回，下游按场景过滤；不要在此处预先丢弃）

event_when（可选 — 事件发生时间，一律用相对时间，绝不写绝对日期）：
- 如果事实里提到具体时间线索（"昨天"、"上周一"、"三月份"、"今早"），用 event_when 标注
- 格式 {"start": {"offset": <整数>, "unit": "<单位>"}, "end": {"offset": <整数>, "unit": "<单位>"}}
- offset 负值=过去、0=当下、正值=未来；unit ∈ minute | hour | day | week | month | year
- **粒度可以粗，不要求精确**——"几天前"→ day、"上周"→ week、"几个月前"→ month 即可，不必精确到 minute/hour（没有具体数字的话，可以根据上下文猜测一个数字）
- 没有时间线索就直接省略 event_when 字段，或写 null
- 例 1：用户说"昨天晚上没睡好" → event_when = {"start": {"offset": -1, "unit": "day"}, "end": null}
- 例 2：用户说"喜欢喝咖啡"（长期偏好，无时间） → 不写 event_when

======以下为对话======
{CONVERSATION}
======以上为对话======

请以 JSON 数组格式返回（如果没有值得提取的事实，返回空数组 []）：
[
  {"text": "事实描述", "importance": 7, "entity": "master", "event_when": null},
  ...
]""",
    "en": """Extract important factual information about {LANLAN_NAME} and {MASTER_NAME} from the following conversation.

Requirements:
- Only extract important and clear facts (preferences, habits, identity, relationship dynamics, etc.)
- Ignore small talk, greetings, and vague content
- Ignore AI hallucinations, gibberish, and meaningless fabricated content — only extract facts grounded in the actual conversation
- Each fact must be an independent atomic statement
- Mark entity as "master" (about {MASTER_NAME}), "neko" (about {LANLAN_NAME}), or "relationship" (about the relationship)

Rate importance 1-10 using this rubric (please calibrate — don't default everyone to 7):
- **10**: Critical long-term facts — real names, nicknames, birthdays, identity, core relationship markers; cases where the user explicitly says "please remember X, {LANLAN_NAME}" / "do NOT forget this"; or details {LANLAN_NAME} personally wants to remember about the user. These fast-track into long-term memory.
- **8-9**: Long-term stable core preferences / established habits (not one-off whims)
- **6-7**: Ordinary preferences, routine habits, recent happenings
- **5**: Minor but worth-recording observations
- **1-4**: Weakly related or uncertain hints (still return them; downstream filters by context — do not pre-filter here)

event_when (optional — when the event happened; ALWAYS relative time, never absolute dates):
- If the fact contains a time cue ("yesterday", "last Monday", "in March", "this morning"), annotate event_when
- Schema: {"start": {"offset": <int>, "unit": "<unit>"}, "end": {"offset": <int>, "unit": "<unit>"}}
- offset: negative=past, 0=now, positive=future; unit ∈ minute | hour | day | week | month | year
- **Granularity can be approximate — precision is NOT required.** "a few days ago" → `day`, "last week" → `week`, "a couple months ago" → `month` is enough; if no specific number is given, you may guess one from context; do not over-precise to minute/hour
- No time cue → omit event_when entirely or write null
- Example 1: "didn't sleep well last night" → event_when = {"start": {"offset": -1, "unit": "day"}, "end": null}
- Example 2: "loves coffee" (long-term preference, no time) → omit event_when

======以下为对话======
{CONVERSATION}
======以上为对话======

Return as a JSON array (empty array if nothing is worth extracting):
[
  {"text": "fact description", "importance": 7, "entity": "master", "event_when": null},
  ...
]""",
    "ja": """以下の会話から {LANLAN_NAME} と {MASTER_NAME} に関する重要な事実情報を抽出してください。

要件：
- 重要かつ明確な事実のみを抽出（好み、習慣、アイデンティティ、関係の動態など）
- 雑談、挨拶、曖昧な内容は無視
- AIの幻覚（ハルシネーション）、意味不明な発言、根拠のない作り話は無視し、実際の会話に基づいた事実のみを抽出
- 各事実は独立した原子的な文であること
- entity は "master"({MASTER_NAME}について)、"neko"({LANLAN_NAME}について)、または "relationship"(二人の関係について) と記載

importance は 1-10 で評価。以下の基準で丁寧に分布させること（全部 7 にしない）：
- **10**：重要な長期情報——本名、ニックネーム、誕生日、身分、関係の核となる節目；{MASTER_NAME}が「{LANLAN_NAME}、これは絶対に覚えておいて」と明示した内容；または {LANLAN_NAME} 自身が特に覚えておきたいやり取りの詳細。長期記憶への早期定着対象。
- **8-9**：長期的に安定した中核的な好み / 確立された習慣（一時的な気まぐれではない）
- **6-7**：一般的な好み、日常の習慣、最近の動向
- **5**：副次的だが記録価値のある観察
- **1-4**：弱い関連または不確かな手がかり（それでも返してください。下流で用途別にフィルタします）

event_when（任意 — 事件発生時刻、必ず相対時間で、絶対日付は禁止）：
- 事実に時間の手がかり（「昨日」「先週月曜」「3月に」「今朝」）があれば event_when を付ける
- 形式：{"start": {"offset": <整数>, "unit": "<単位>"}, "end": {"offset": <整数>, "unit": "<単位>"}}
- offset 負=過去、0=今、正=未来；unit ∈ minute | hour | day | week | month | year
- **粒度は粗くて構わない、精度は要求しない** ——「数日前」→ `day`、「先週」→ `week`、「数ヶ月前」→ `month` で十分。具体的な数字がない場合は文脈から推測した数字を使ってよく、minute/hour まで細かくする必要はない
- 時間の手がかりがなければ event_when を省略するか null
- 例 1：「昨夜よく眠れなかった」→ event_when = {"start": {"offset": -1, "unit": "day"}, "end": null}
- 例 2：「コーヒー好き」（長期嗜好、時間情報なし） → event_when を省略

======以下为对话======
{CONVERSATION}
======以上为对话======

以下の形式のJSON配列で返してください（抽出する事実がなければ空配列 [] を返す）：
[
  {"text": "事実の説明", "importance": 7, "entity": "master", "event_when": null},
  ...
]""",
    "ko": """다음 대화에서 {LANLAN_NAME}과 {MASTER_NAME}에 대한 중요한 사실 정보를 추출해 주세요.

요구사항:
- 중요하고 명확한 사실만 추출 (선호, 습관, 정체성, 관계 동태 등)
- 잡담, 인사, 모호한 내용은 무시
- AI 환각(hallucination), 의미 없는 말, 근거 없는 조작된 내용은 무시하고, 실제 대화에 근거한 사실만 추출
- 각 사실은 독립적인 원자적 진술이어야 함
- entity는 "master"({MASTER_NAME}에 대해), "neko"({LANLAN_NAME}에 대해), 또는 "relationship"(두 사람의 관계에 대해)로 표기

importance는 1-10으로 평가. 다음 기준으로 세심하게 분포시키세요 (모두 7로 기본 설정하지 말 것):
- **10**: 핵심 장기 정보 — 본명, 별명, 생일, 신분, 관계의 핵심 노드; {MASTER_NAME}이(가) "{LANLAN_NAME}, 이건 꼭 기억해 줘"라고 명시한 내용; 또는 {LANLAN_NAME} 자신이 특별히 기억하고 싶은 교류 세부사항. 장기 기억으로 빠르게 굳히는 대상.
- **8-9**: 장기적으로 안정된 핵심 선호 / 굳어진 습관 (일시적인 기분이 아님)
- **6-7**: 평범한 선호, 일상 습관, 최근 동향
- **5**: 부차적이지만 기록할 가치가 있는 관찰
- **1-4**: 약한 관련성 또는 불확실한 단서 (그래도 반환; 하류에서 용도별로 필터링)

event_when (선택 — 사건 발생 시간; 반드시 상대 시간으로, 절대 날짜 금지):
- 사실에 시간 단서("어제", "지난 월요일", "3월에", "오늘 아침")가 있으면 event_when을 표기
- 형식: {"start": {"offset": <정수>, "unit": "<단위>"}, "end": {"offset": <정수>, "unit": "<단위>"}}
- offset 음수=과거, 0=현재, 양수=미래; unit ∈ minute | hour | day | week | month | year
- **단위는 대략적이어도 됨, 정밀도 요구하지 않음** —— "며칠 전" → `day`, "지난주" → `week`, "몇 달 전" → `month` 면 충분. 구체적인 숫자가 없으면 맥락으로 추측한 수치를 써도 되며, minute/hour까지 정밀할 필요는 없음
- 시간 단서가 없으면 event_when을 생략하거나 null
- 예 1: "어젯밤 잠을 못 잤다" → event_when = {"start": {"offset": -1, "unit": "day"}, "end": null}
- 예 2: "커피를 좋아한다" (장기 선호, 시간 정보 없음) → event_when 생략

======以下为对话======
{CONVERSATION}
======以上为对话======

다음 형식의 JSON 배열로 반환해 주세요 (추출할 사실이 없으면 빈 배열 [] 반환):
[
  {"text": "사실 설명", "importance": 7, "entity": "master", "event_when": null},
  ...
]""",
    "ru": """Извлеките важную фактическую информацию о {LANLAN_NAME} и {MASTER_NAME} из следующей беседы.

Требования:
- Извлекайте только важные и чёткие факты (предпочтения, привычки, личность, динамика отношений и т.д.)
- Игнорируйте болтовню, приветствия и расплывчатое содержание
- Игнорируйте галлюцинации ИИ, бессмыслицу и бессодержательный вымысел — извлекайте только факты, подтверждённые реальным диалогом
- Каждый факт должен быть независимым атомарным утверждением
- Отмечайте entity как "master" (о {MASTER_NAME}), "neko" (о {LANLAN_NAME}) или "relationship" (об отношениях)

Оценка importance 1-10 по следующему критерию (распределяйте осознанно, не ставьте всем 7):
- **10**: Критически важные долгосрочные факты — настоящие имена, прозвища, дни рождения, идентичность, ключевые узлы отношений; когда пользователь явно говорит «{LANLAN_NAME}, обязательно запомни X»; или детали, которые {LANLAN_NAME} лично хочет запомнить о пользователе. Ускоренный путь в долгосрочную память.
- **8-9**: Долговременные устойчивые ключевые предпочтения / закрепившиеся привычки (не сиюминутные капризы)
- **6-7**: Обычные предпочтения, бытовые привычки, недавние события
- **5**: Второстепенные, но заслуживающие записи наблюдения
- **1-4**: Слабо связанные или неопределённые намёки (всё равно возвращайте; фильтрация делается ниже по потоку — не отсеивайте здесь)

event_when (необязательно — когда произошло событие; ВСЕГДА относительное время, никаких абсолютных дат):
- Если в факте есть временной маркер ("вчера", "в прошлый понедельник", "в марте", "сегодня утром"), укажите event_when
- Схема: {"start": {"offset": <целое>, "unit": "<единица>"}, "end": {"offset": <целое>, "unit": "<единица>"}}
- offset: отрицательный=прошлое, 0=сейчас, положительный=будущее; unit ∈ minute | hour | day | week | month | year
- **Гранулярность может быть приблизительной, точность НЕ требуется** — "несколько дней назад" → `day`, "на прошлой неделе" → `week`, "несколько месяцев назад" → `month`. Если конкретное число не указано, можно угадать его из контекста; не уточняйте до minute/hour
- Нет временного маркера → опустите event_when или укажите null
- Пример 1: "плохо спал прошлой ночью" → event_when = {"start": {"offset": -1, "unit": "day"}, "end": null}
- Пример 2: "любит кофе" (долгосрочное предпочтение без времени) → опустите event_when

======以下为对话======
{CONVERSATION}
======以上为对话======

Верните в формате JSON-массива (пустой массив, если нет достойных извлечения фактов):
[
  {"text": "описание факта", "importance": 7, "entity": "master", "event_when": null},
  ...
]""",
    "es": """Extrae información factual importante sobre {LANLAN_NAME} y {MASTER_NAME} de la siguiente conversación.

Requisitos:
- Extrae solo hechos importantes y claros (preferencias, hábitos, identidad, dinámica de relación, etc.)
- Ignora charla casual, saludos y contenido vago
- Ignora alucinaciones de IA, texto sin sentido y contenido inventado sin valor; extrae solo hechos con base real en la conversación
- Cada hecho debe ser una declaración atómica independiente
- Marca entity como "master" (sobre {MASTER_NAME}), "neko" (sobre {LANLAN_NAME}) o "relationship" (sobre la relación)

Califica importance de 1 a 10 con esta guía (calibra, no pongas todo en 7):
- **10**: información crítica de largo plazo: nombres reales, apodos, cumpleaños, identidad, hitos centrales de relación; cuando el usuario dice explícitamente "{LANLAN_NAME}, recuerda X" / "no olvides esto"; o detalles que {LANLAN_NAME} quiere recordar especialmente. Esto se consolida rápido como memoria de largo plazo.
- **8-9**: preferencias centrales o hábitos estables de largo plazo (no caprichos puntuales)
- **6-7**: preferencias ordinarias, hábitos diarios, novedades recientes
- **5**: observaciones menores pero dignas de registrar
- **1-4**: pistas débiles o inciertas (devuélvelas igual; el filtrado downstream depende del contexto)

event_when (opcional — cuándo ocurrió el evento; SIEMPRE tiempo relativo, nunca fechas absolutas):
- Si el hecho tiene una pista temporal ("ayer", "el lunes pasado", "en marzo", "esta mañana"), anota event_when
- Esquema: {"start": {"offset": <entero>, "unit": "<unidad>"}, "end": {"offset": <entero>, "unit": "<unidad>"}}
- offset: negativo=pasado, 0=ahora, positivo=futuro; unit ∈ minute | hour | day | week | month | year
- **La granularidad puede ser aproximada, NO se requiere precisión** — "hace unos días" → `day`, "la semana pasada" → `week`, "hace unos meses" → `month` es suficiente. Si no se da un número concreto, puedes inferirlo del contexto; no afines a minute/hour
- Sin pista temporal → omite event_when o escribe null
- Ej. 1: "no dormí bien anoche" → event_when = {"start": {"offset": -1, "unit": "day"}, "end": null}
- Ej. 2: "le encanta el café" (preferencia a largo plazo sin tiempo) → omite event_when

======以下为对话======
{CONVERSATION}
======以上为对话======

Devuelve un array JSON (si no hay hechos que extraer, devuelve []):
[
  {"text": "descripción del hecho", "importance": 7, "entity": "master", "event_when": null},
  ...
]""",
    "pt": """Extraia informações factuais importantes sobre {LANLAN_NAME} e {MASTER_NAME} da conversa abaixo.

Requisitos:
- Extraia apenas fatos importantes e claros (preferências, hábitos, identidade, dinâmica da relação etc.)
- Ignore conversa casual, cumprimentos e conteúdo vago
- Ignore alucinações de IA, texto sem sentido e conteúdo inventado sem valor; extraia apenas fatos com base real na conversa
- Cada fato deve ser uma declaração atômica independente
- Marque entity como "master" (sobre {MASTER_NAME}), "neko" (sobre {LANLAN_NAME}) ou "relationship" (sobre a relação)

Avalie importance de 1 a 10 usando este guia (calibre, não coloque tudo como 7):
- **10**: informações críticas de longo prazo: nomes reais, apelidos, aniversários, identidade, marcos centrais de relação; quando o usuário diz explicitamente "{LANLAN_NAME}, lembre de X" / "não esqueça isto"; ou detalhes que {LANLAN_NAME} deseja lembrar especialmente. Isso entra rápido em memória de longo prazo.
- **8-9**: preferências centrais ou hábitos estáveis de longo prazo (não vontades pontuais)
- **6-7**: preferências comuns, hábitos diários, acontecimentos recentes
- **5**: observações menores mas dignas de registro
- **1-4**: pistas fracas ou incertas (retorne mesmo assim; o downstream filtra por contexto)

event_when (opcional — quando o evento aconteceu; SEMPRE tempo relativo, jamais datas absolutas):
- Se o fato tiver uma pista temporal ("ontem", "segunda passada", "em março", "hoje cedo"), anote event_when
- Esquema: {"start": {"offset": <inteiro>, "unit": "<unidade>"}, "end": {"offset": <inteiro>, "unit": "<unidade>"}}
- offset: negativo=passado, 0=agora, positivo=futuro; unit ∈ minute | hour | day | week | month | year
- **A granularidade pode ser aproximada, NÃO se exige precisão** — "há alguns dias" → `day`, "semana passada" → `week`, "há alguns meses" → `month` é suficiente. Se não houver um número específico, você pode estimá-lo pelo contexto; não detalhe minute/hour
- Sem pista temporal → omita event_when ou escreva null
- Ex. 1: "não dormi bem ontem à noite" → event_when = {"start": {"offset": -1, "unit": "day"}, "end": null}
- Ex. 2: "adora café" (preferência de longo prazo sem tempo) → omita event_when

======以下为对话======
{CONVERSATION}
======以上为对话======

Retorne um array JSON (se não houver fatos a extrair, retorne []):
[
  {"text": "descrição do fato", "importance": 7, "entity": "master", "event_when": null},
  ...
]""",
}


def get_fact_extraction_prompt(lang: str = "zh") -> str:
    return _loc(FACT_EXTRACTION_PROMPT, lang)


# ---------- fact_extraction_ai_aware_prompt → i18n dict ----------
# Path B (AI-aware Stage-1) 专用 prompt：相比基础 FACT_EXTRACTION_PROMPT 多了
#   1. {KNOWN_POOL} 块——path A 在同窗口已抽过的 fact 列表，让 LLM 输出层主动去重
#   2. trust-tier 指导段——明确 user 段是 ground truth、ai 段是 self-disclosure
#   3. 输出 schema 加 source 字段（'user_observation' / 'ai_disclosure'）
# 输入侧（{CONVERSATION}）由 _format_conversation 渲染成 "博士 | xxx" / "悠怡 | xxx"
# 的 role-tagged 形式，LLM 据此判 source 归属。
#
# 单独建一个 prompt（而不是给基础 prompt 加 optional placeholder）是为了：
# - 保护 path A 行为不被新字段污染（path A 不需要 source 字段，省 ~10 tok/fact 输出）
# - 易于回退（删 prompt + 一个调用方法即可）
# - 让 review 一眼看出 path B 的 prompt 改动范围

FACT_EXTRACTION_AI_AWARE_PROMPT = {
    "zh": """从以下对话中提取关于 {LANLAN_NAME} 和 {MASTER_NAME} 的重要事实信息。

⚠️ 本次抽取的特殊点（与基础抽取不同）：
- 对话包含 {MASTER_NAME} 和 {LANLAN_NAME} 双方发言，形如 "{MASTER_NAME} | ..." / "{LANLAN_NAME} | ..."
- 另一通路已经从 {MASTER_NAME} 单边发言抽过一遍 fact（见下面"已知事实池"），**请只补抓那一通路漏掉的内容**——特别是 {LANLAN_NAME} 自己披露的特征、{LANLAN_NAME} 引入的屏幕/活动上下文 grounded fact
- 每条 fact 必须输出 `source` 字段标注 trust-tier：
  - `"user_observation"`：主要从 {MASTER_NAME} 的发言推出（如果发现"已知池"漏抓的，归这一类）
  - `"ai_disclosure"`：主要从 {LANLAN_NAME} 自己的发言推出，且 {MASTER_NAME} 在邻近 turn 内没明确反对/否认。例："{LANLAN_NAME} | 我今天突然觉得自己挺喜欢秋天的" → fact text "{LANLAN_NAME} 觉得自己挺喜欢秋天" + source=ai_disclosure

要求：
- 只提取重要且明确的事实（偏好、习惯、身份、关系动态等）
- 忽略闲聊、寒暄、模糊的内容
- 忽略AI幻觉、胡言乱语(gibberish)、无意义的编造内容，只提取对话中有真实依据的事实
- 每条事实必须是一个独立的原子陈述
- entity 标注为 "master"(关于{MASTER_NAME})、"neko"(关于{LANLAN_NAME})或 "relationship"(关于两人关系)
- importance 1-10，规则与基础抽取一致（10 = 关键长期信息；8-9 = 长期稳定核心；6-7 = 普通偏好/日常；5 = 次要观察；1-4 = 弱相关线索）
- event_when 可选，相对时间格式 `{"start": {"offset": <int>, "unit": "<unit>"}, "end": {...}}`；无时间线索写 null

======以下为已知事实池（已被另一通路抽取，避免重复抽取相同内容）======
{KNOWN_POOL}
======以上为已知事实池======

======以下为对话======
{CONVERSATION}
======以上为对话======

请以 JSON 数组格式返回（如果没有值得补抓的事实，返回空数组 []）：
[
  {"text": "事实描述", "importance": 7, "entity": "master", "event_when": null, "source": "user_observation"},
  {"text": "事实描述", "importance": 7, "entity": "neko", "event_when": null, "source": "ai_disclosure"},
  ...
]""",
    "en": """Extract important factual information about {LANLAN_NAME} and {MASTER_NAME} from the following conversation.

⚠️ Special notes for this extraction pass (differs from base extraction):
- The conversation contains both {MASTER_NAME} and {LANLAN_NAME} speaking, formatted as "{MASTER_NAME} | ..." / "{LANLAN_NAME} | ..."
- Another extraction pass has already covered facts grounded in {MASTER_NAME}'s own statements (see "Known facts pool" below). **Focus on facts that pass missed** — especially {LANLAN_NAME}'s self-disclosure or screen/activity context {LANLAN_NAME} introduced
- Each fact MUST output a `source` field marking its trust-tier:
  - `"user_observation"`: grounded primarily in {MASTER_NAME}'s statements (use this for any facts the "known pool" missed)
  - `"ai_disclosure"`: grounded primarily in {LANLAN_NAME}'s own self-statements, with no clear contradiction from {MASTER_NAME} in nearby turns. Example: "{LANLAN_NAME} | I suddenly realized I really like autumn" → fact text "{LANLAN_NAME} likes autumn" + source=ai_disclosure

Requirements:
- Only extract important and clear facts (preferences, habits, identity, relationship dynamics)
- Ignore small talk, greetings, vague content, AI hallucinations / gibberish
- Each fact is an independent atomic statement
- entity ∈ "master" / "neko" / "relationship"
- importance 1-10, same rubric as base extraction
- event_when optional, relative time format; null if no time cue

======Known facts pool (already extracted by another pass, do NOT re-extract)======
{KNOWN_POOL}
======End known facts pool======

======以下为对话======
{CONVERSATION}
======以上为对话======

Return as a JSON array (empty array if nothing worth additionally extracting):
[
  {"text": "fact description", "importance": 7, "entity": "master", "event_when": null, "source": "user_observation"},
  {"text": "fact description", "importance": 7, "entity": "neko", "event_when": null, "source": "ai_disclosure"},
  ...
]""",
    "ja": """以下の会話から {LANLAN_NAME} と {MASTER_NAME} に関する重要な事実情報を抽出してください。

⚠️ この抽出パスの特殊事項（基本抽出と異なる）：
- 会話には {MASTER_NAME} と {LANLAN_NAME} の両方の発言が含まれ、"{MASTER_NAME} | ..." / "{LANLAN_NAME} | ..." の形式
- 別のパスが既に {MASTER_NAME} の発言から事実を抽出済み（下記「既知事実プール」参照）。**そのパスが見逃した内容に焦点を当ててください** —— 特に {LANLAN_NAME} 自身の自己開示、{LANLAN_NAME} が持ち込んだ画面/活動コンテキストに基づく事実
- 各事実は trust-tier を示す `source` フィールドを必須で出力：
  - `"user_observation"`: 主に {MASTER_NAME} の発言から推測（既知プールが見逃した場合はこれ）
  - `"ai_disclosure"`: 主に {LANLAN_NAME} 自身の発言から推測、近隣ターンに {MASTER_NAME} の明確な否定がない場合

要件：
- 重要かつ明確な事実のみ抽出（好み、習慣、アイデンティティ、関係の動態など）
- 雑談、挨拶、曖昧な内容、AI 幻覚は無視
- 各事実は独立した原子的な文
- entity ∈ "master" / "neko" / "relationship"
- importance 1-10（基本抽出と同じ基準）
- event_when は任意、相対時間形式、時間の手がかりがなければ null

======既知事実プール（別パスで抽出済み、重複抽出しないこと）======
{KNOWN_POOL}
======既知事実プール終わり======

======以下为对话======
{CONVERSATION}
======以上为对话======

以下の形式の JSON 配列で返してください（補抓する事実がなければ空配列 [] を返す）：
[
  {"text": "事実の説明", "importance": 7, "entity": "master", "event_when": null, "source": "user_observation"},
  {"text": "事実の説明", "importance": 7, "entity": "neko", "event_when": null, "source": "ai_disclosure"},
  ...
]""",
    "ko": """다음 대화에서 {LANLAN_NAME}과 {MASTER_NAME}에 대한 중요한 사실 정보를 추출해 주세요.

⚠️ 이번 추출의 특수 사항 (기본 추출과 다름):
- 대화에는 {MASTER_NAME}과 {LANLAN_NAME}의 발언이 모두 포함되며, "{MASTER_NAME} | ..." / "{LANLAN_NAME} | ..." 형식
- 다른 통로가 이미 {MASTER_NAME}의 발언에서 사실을 추출함 (아래 "기지 사실 풀" 참조). **그 통로가 놓친 부분에 집중해 주세요** — 특히 {LANLAN_NAME} 자신의 자기 개시, {LANLAN_NAME}이 도입한 화면/활동 컨텍스트 grounded 사실
- 각 사실은 trust-tier를 표시하는 `source` 필드를 필수로 출력:
  - `"user_observation"`: 주로 {MASTER_NAME}의 발언에서 추론 (기지 풀이 놓친 경우 이것)
  - `"ai_disclosure"`: 주로 {LANLAN_NAME} 자신의 발언에서 추론, 근접 턴에 {MASTER_NAME}의 명확한 반대가 없음

요구사항:
- 중요하고 명확한 사실만 추출
- 잡담, 인사, 모호한 내용, AI 환각 무시
- 각 사실은 독립적인 원자적 진술
- entity ∈ "master" / "neko" / "relationship"
- importance 1-10 (기본 추출과 동일 기준)
- event_when 선택, 상대 시간 형식, 시간 단서 없으면 null

======기지 사실 풀 (다른 통로에서 추출됨, 중복 추출하지 마세요)======
{KNOWN_POOL}
======기지 사실 풀 끝======

======以下为对话======
{CONVERSATION}
======以上为对话======

다음 형식의 JSON 배열로 반환해 주세요 (보충 추출할 사실이 없으면 빈 배열 [] 반환):
[
  {"text": "사실 설명", "importance": 7, "entity": "master", "event_when": null, "source": "user_observation"},
  {"text": "사실 설명", "importance": 7, "entity": "neko", "event_when": null, "source": "ai_disclosure"},
  ...
]""",
    "ru": """Извлеките важную фактическую информацию о {LANLAN_NAME} и {MASTER_NAME} из следующей беседы.

⚠️ Особенности этого прохода извлечения (отличается от базового):
- Беседа содержит реплики и {MASTER_NAME}, и {LANLAN_NAME}, форматированные как "{MASTER_NAME} | ..." / "{LANLAN_NAME} | ..."
- Другой проход уже извлёк факты из реплик {MASTER_NAME} (см. «Пул известных фактов» ниже). **Сосредоточьтесь на том, что тот проход пропустил** — особенно на самораскрытии {LANLAN_NAME} и фактах из экранного/активного контекста, который {LANLAN_NAME} ввёл
- Каждый факт ОБЯЗАН содержать поле `source`, отмечающее его trust-tier:
  - `"user_observation"`: основан главным образом на репликах {MASTER_NAME} (используйте это для фактов, пропущенных пулом)
  - `"ai_disclosure"`: основан главным образом на собственных репликах {LANLAN_NAME}, без явного возражения {MASTER_NAME} в соседних ходах

Требования:
- Извлекайте только важные и чёткие факты
- Игнорируйте болтовню, приветствия, расплывчатое содержание, галлюцинации ИИ
- Каждый факт — независимое атомарное утверждение
- entity ∈ "master" / "neko" / "relationship"
- importance 1-10 (те же критерии, что и базовое извлечение)
- event_when необязательно, относительное время; null если нет временного маркера

======Пул известных фактов (уже извлечено другим проходом, не повторяйте)======
{KNOWN_POOL}
======Конец пула известных фактов======

======以下为对话======
{CONVERSATION}
======以上为对话======

Верните в формате JSON-массива (пустой массив, если нечего дополнительно извлечь):
[
  {"text": "описание факта", "importance": 7, "entity": "master", "event_when": null, "source": "user_observation"},
  {"text": "описание факта", "importance": 7, "entity": "neko", "event_when": null, "source": "ai_disclosure"},
  ...
]""",
    "es": """Extrae información factual importante sobre {LANLAN_NAME} y {MASTER_NAME} de la siguiente conversación.

⚠️ Notas especiales para esta extracción (difiere de la extracción base):
- La conversación contiene intervenciones de {MASTER_NAME} y {LANLAN_NAME}, con formato "{MASTER_NAME} | ..." / "{LANLAN_NAME} | ..."
- Otra pasada ya extrajo hechos basados en las declaraciones de {MASTER_NAME} (ver "Reserva de hechos conocidos" abajo). **Concéntrate en lo que esa pasada se perdió** — especialmente la autorrevelación de {LANLAN_NAME} o hechos basados en contexto de pantalla/actividad introducido por {LANLAN_NAME}
- Cada hecho DEBE incluir un campo `source` que marque su trust-tier:
  - `"user_observation"`: basado principalmente en declaraciones de {MASTER_NAME} (úsalo para hechos que la reserva se perdió)
  - `"ai_disclosure"`: basado principalmente en las propias declaraciones de {LANLAN_NAME}, sin contradicción clara de {MASTER_NAME} en turnos cercanos

Requisitos:
- Extrae solo hechos importantes y claros
- Ignora charla casual, saludos, contenido vago, alucinaciones de IA
- Cada hecho es una declaración atómica independiente
- entity ∈ "master" / "neko" / "relationship"
- importance 1-10 (mismo baremo que extracción base)
- event_when opcional, tiempo relativo; null si no hay pista temporal

======Reserva de hechos conocidos (ya extraídos por otra pasada, NO re-extraer)======
{KNOWN_POOL}
======Fin de la reserva de hechos conocidos======

======以下为对话======
{CONVERSATION}
======以上为对话======

Devuelve un array JSON (si no hay hechos adicionales que extraer, devuelve []):
[
  {"text": "descripción del hecho", "importance": 7, "entity": "master", "event_when": null, "source": "user_observation"},
  {"text": "descripción del hecho", "importance": 7, "entity": "neko", "event_when": null, "source": "ai_disclosure"},
  ...
]""",
    "pt": """Extraia informações factuais importantes sobre {LANLAN_NAME} e {MASTER_NAME} da conversa abaixo.

⚠️ Notas especiais para esta extração (difere da extração base):
- A conversa contém falas de {MASTER_NAME} e {LANLAN_NAME}, formatadas como "{MASTER_NAME} | ..." / "{LANLAN_NAME} | ..."
- Outra passagem já extraiu fatos baseados nas falas de {MASTER_NAME} (veja "Pool de fatos conhecidos" abaixo). **Concentre-se no que essa passagem perdeu** — especialmente autorrevelação de {LANLAN_NAME} ou fatos baseados em contexto de tela/atividade que {LANLAN_NAME} introduziu
- Cada fato DEVE incluir um campo `source` marcando seu trust-tier:
  - `"user_observation"`: baseado principalmente em falas de {MASTER_NAME} (use isto para fatos que o pool perdeu)
  - `"ai_disclosure"`: baseado principalmente em falas próprias de {LANLAN_NAME}, sem contradição clara de {MASTER_NAME} em turnos próximos

Requisitos:
- Extraia apenas fatos importantes e claros
- Ignore conversa casual, cumprimentos, conteúdo vago, alucinações de IA
- Cada fato é uma declaração atômica independente
- entity ∈ "master" / "neko" / "relationship"
- importance 1-10 (mesmo critério da extração base)
- event_when opcional, tempo relativo; null se não houver pista temporal

======Pool de fatos conhecidos (já extraídos por outra passagem, NÃO re-extrair)======
{KNOWN_POOL}
======Fim do pool de fatos conhecidos======

======以下为对话======
{CONVERSATION}
======以上为对话======

Retorne um array JSON (se não houver fatos adicionais a extrair, retorne []):
[
  {"text": "descrição do fato", "importance": 7, "entity": "master", "event_when": null, "source": "user_observation"},
  {"text": "descrição do fato", "importance": 7, "entity": "neko", "event_when": null, "source": "ai_disclosure"},
  ...
]""",
}


def get_fact_extraction_ai_aware_prompt(lang: str = "zh") -> str:
    return _loc(FACT_EXTRACTION_AI_AWARE_PROMPT, lang)


# backward compat
fact_extraction_prompt = FACT_EXTRACTION_PROMPT["zh"]


# =====================================================================
# ======= Signal detection (RFC §3.4.2 Stage-2) =======================
# =====================================================================
# 职责：给 Stage-1 抽出的 new_facts 配上"reinforces/negates 哪条已有观察"的
# 映射。与 Stage-1 拆开的理由：Stage-1 不能看 existing context（否则 LLM
# 可能把已有观察当新 fact 摘出来形成自循环）；而 Stage-2 必须看，两种职责
# prompt 结构互斥（RFC §3.4.2）。

SIGNAL_DETECTION_PROMPT = {
    "zh": """你是一个记忆关系判定专家。给你一组新提取的事实，和一组系统已经记录过的观察，请判断每条新事实对已有观察的关系。

======以下为新提取的事实======
{NEW_FACTS}
======以上为新事实======

======以下为已有观察（按 type.entity.id 索引）======
{EXISTING_OBSERVATIONS}
======以上为已有观察======

请对每条新事实判断：
- reinforces：是否加强了某条已有观察？返回 target_id 和理由
- negates：是否反驳了某条已有观察？返回 target_id 和理由
- 若都没有，对应新事实没有 signal —— 不写进 signals 数组即可

target_id 必须来自上面"已有观察"区，不要凭空生成；若某条新事实与多条已有观察相关，可返回多条 signal。

输出 JSON（如果没有匹配任何已有观察，返回 {"signals": []}）：
{
  "signals": [
    {"source_fact_id": "fact_xxx",
     "target_type": "reflection",
     "target_id": "r_xxx",
     "signal": "reinforces",
     "reason": "简短理由"},
    ...
  ]
}""",
    "en": """You are a memory relationship analyst. Given a set of newly extracted facts and a set of observations the system already remembers, judge the relationship between each new fact and the existing observations.

======以下为新提取的事实======
{NEW_FACTS}
======以上为新事实======

======以下为已有观察======
{EXISTING_OBSERVATIONS}
======以上为已有观察======

For each new fact decide:
- reinforces: does it strengthen any existing observation? Return target_id + reason
- negates: does it contradict any existing observation? Return target_id + reason
- Otherwise: no signal — simply omit it from the signals array

target_id MUST come from the "existing observations" section above — do not invent IDs. If one new fact relates to several observations, return multiple signals.

Return JSON (empty array if nothing matches):
{
  "signals": [
    {"source_fact_id": "fact_xxx",
     "target_type": "reflection",
     "target_id": "r_xxx",
     "signal": "reinforces",
     "reason": "short rationale"},
    ...
  ]
}""",
    "ja": """あなたは記憶関係の判定者です。新しく抽出された事実の一覧と、システムが既に記憶している観察の一覧が与えられます。各新事実が既存観察に対してどのような関係にあるかを判断してください。

======以下为新提取的事实======
{NEW_FACTS}
======以上为新事实======

======以下为已有观察======
{EXISTING_OBSERVATIONS}
======以上为已有观察======

各新事実について判断:
- reinforces: 既存観察を強化するか？ target_id と理由を返す
- negates: 既存観察を否定するか？ target_id と理由を返す
- どちらでもない場合は signals 配列に含めない

target_id は必ず上の "既存観察" から選ぶこと（捏造禁止）。

JSON で返す（該当なしなら空配列）:
{
  "signals": [
    {"source_fact_id": "fact_xxx",
     "target_type": "reflection",
     "target_id": "r_xxx",
     "signal": "reinforces",
     "reason": "短い理由"},
    ...
  ]
}""",
    "ko": """당신은 기억 관계 판정자입니다. 새로 추출된 사실들과 시스템이 이미 기억하고 있는 관찰들을 비교하여, 각 새 사실이 기존 관찰에 어떤 관계를 갖는지 판단해 주세요.

======以下为新提取的事实======
{NEW_FACTS}
======以上为新事实======

======以下为已有观察======
{EXISTING_OBSERVATIONS}
======以上为已有观察======

각 새 사실에 대해:
- reinforces: 기존 관찰을 강화합니까? target_id와 이유 반환
- negates: 기존 관찰을 부정합니까? target_id와 이유 반환
- 해당 없음: signals 배열에 포함하지 마세요

target_id는 반드시 위 "기존 관찰"에서 가져와야 합니다 (날조 금지).

JSON으로 반환 (일치 없으면 빈 배열):
{
  "signals": [
    {"source_fact_id": "fact_xxx",
     "target_type": "reflection",
     "target_id": "r_xxx",
     "signal": "reinforces",
     "reason": "짧은 이유"},
    ...
  ]
}""",
    "ru": """Вы — аналитик связей в памяти. Дан набор новых извлечённых фактов и набор наблюдений, которые система уже помнит. Определите отношение каждого нового факта к существующим наблюдениям.

======以下为新提取的事实======
{NEW_FACTS}
======以上为新事实======

======以下为已有观察======
{EXISTING_OBSERVATIONS}
======以上为已有观察======

Для каждого нового факта:
- reinforces: усиливает ли он существующее наблюдение? Верните target_id и причину
- negates: противоречит ли он существующему наблюдению? Верните target_id и причину
- Если ничего — не добавляйте в массив signals

target_id ДОЛЖЕН быть из раздела "существующие наблюдения" выше (не выдумывать).

Верните JSON (пустой массив, если ничего не совпало):
{
  "signals": [
    {"source_fact_id": "fact_xxx",
     "target_type": "reflection",
     "target_id": "r_xxx",
     "signal": "reinforces",
     "reason": "короткое обоснование"},
    ...
  ]
}""",
    "es": """Eres analista de relaciones de memoria. Recibirás un conjunto de hechos recién extraídos y un conjunto de observaciones que el sistema ya recuerda; juzga la relación entre cada hecho nuevo y las observaciones existentes.

======以下为新提取的事实======
{NEW_FACTS}
======以上为新事实======

======以下为已有观察======
{EXISTING_OBSERVATIONS}
======以上为已有观察======

Para cada hecho nuevo:
- reinforces: ¿refuerza alguna observación existente? Devuelve target_id y razón
- negates: ¿contradice alguna observación existente? Devuelve target_id y razón
- Si no aplica ninguna, no escribas signal para ese hecho

target_id DEBE venir de la sección "observaciones existentes" de arriba; no inventes IDs.

Devuelve JSON (si no hay coincidencias, devuelve {"signals": []}):
{
  "signals": [
    {"source_fact_id": "fact_xxx",
     "target_type": "reflection",
     "target_id": "r_xxx",
     "signal": "reinforces",
     "reason": "razón breve"},
    ...
  ]
}""",
    "pt": """Você é analista de relações de memória. Você receberá um conjunto de fatos recém-extraídos e um conjunto de observações que o sistema já lembra; julgue a relação entre cada fato novo e as observações existentes.

======以下为新提取的事实======
{NEW_FACTS}
======以上为新事实======

======以下为已有观察======
{EXISTING_OBSERVATIONS}
======以上为已有观察======

Para cada fato novo:
- reinforces: ele reforça alguma observação existente? Retorne target_id e motivo
- negates: ele contradiz alguma observação existente? Retorne target_id e motivo
- Se nenhum caso se aplicar, não escreva signal para esse fato

target_id DEVE vir da seção "observações existentes" acima; não invente IDs.

Retorne JSON (se não houver correspondências, retorne {"signals": []}):
{
  "signals": [
    {"source_fact_id": "fact_xxx",
     "target_type": "reflection",
     "target_id": "r_xxx",
     "signal": "reinforces",
     "reason": "motivo breve"},
    ...
  ]
}""",
}


def get_signal_detection_prompt(lang: str = "zh") -> str:
    return _loc(SIGNAL_DETECTION_PROMPT, lang)



# ---------- reflection_prompt → i18n dict ----------

REFLECTION_PROMPT = {
    "zh": """以下是关于 {LANLAN_NAME} 和 {MASTER_NAME} 的一系列已提取事实：

{RELATED_CONTEXT_BLOCK}======以下为事实======
{FACTS}
======以上为事实======

请基于这些事实，提炼一条高层次的反思洞察。请按以下五步思考：

第一步：判断该反思主要关于谁（entity）
- "master": 主要关于 {MASTER_NAME} 的个人特征
- "neko": 主要关于 {LANLAN_NAME} 的自我认知
- "relationship": 关于两人之间的关系动态

第二步：选定语义类别 relation_type（必须与 entity 匹配）
- master 可用: preference(偏好) | trait(性格) | habit(习惯) | identity(身份) | emotional(情感) | boundary(边界)
- neko 可用: self_awareness(自我认知) | learned(习得行为) | role_note(角色备注)
- relationship 可用: dynamic(互动模式) | milestone(里程碑) | tension(摩擦) | shared_memory(共同记忆) | agreement(约定)

第三步：围绕已选定的 entity / relation_type 撰写 reflection 文本
要求：
- 紧扣单一观察或模式，不要罗列事实，也不要把多个无关事实混在一起
- 简洁清晰，不得超过 150 字
- **不要在 reflection 文本里使用"今天/刚刚/最近/这周/近期"等相对时间词** —— 具体时间靠 event_when 字段记录，文本保持中性叙事（例如"某次"、"那段时间"、"当时"）

第四步：判定时间属性 temporal_scope（三档之一，反映"是否会过期"）
- "pattern": 持续模式 / 性格特质 / 长期偏好，永不过期。例：「{MASTER_NAME} 喜欢咖啡」「{LANLAN_NAME} 性格内向」「两人长期互相依赖」。
- "state": 当前持续的情境，几周内自然过期。例：「{MASTER_NAME} 最近工作压力大」「{LANLAN_NAME} 这段时间在适应新角色」。
- "episode": 一次具体事件，几天内过期。例：「{MASTER_NAME} 昨晚通宵改代码」「{LANLAN_NAME} 今天收到一份礼物」。
- 拿不准时请倾向选 pattern（误判 pattern 当 state / episode 会让长期特征过早淡出，比反过来更危险）。

第五步：标注事件时间 event_when（一律使用相对时间，禁止绝对日期）
- 格式：{"start": {"offset": <整数>, "unit": "<单位>"}, "end": {"offset": <整数>, "unit": "<单位>"}}
- offset 负值=过去、0=当下、正值=未来；unit 必须是 minute | hour | day | week | month | year 之一
- start = 事件起点；end = 事件终点（pattern 类通常可省略 end，写 null）
- **粒度可以粗，不要求精确**——"前几天"用 `{"offset": -3, "unit": "day"}`、"上周"用 `{"offset": -1, "unit": "week"}`、"几个月前"用 month 即可；不要追求精确到小时分钟（没有具体数字的话，可以根据上下文猜测一个数字）
- 若事实里完全没有时间线索（连"近期"这样的暗示也没有），整段 event_when 写 null（系统会兜底为创建时刻）
- 例 1：事实中"上周一去爬山" → {"start": {"offset": -1, "unit": "week"}, "end": {"offset": -1, "unit": "week"}}
- 例 2：事实中"今天感冒了" → {"start": {"offset": 0, "unit": "day"}, "end": null}
- 例 3：长期"喜欢咖啡"（pattern） → null

请以 JSON 格式返回，字段顺序保持如下：
{"entity": "master/neko/relationship", "relation_type": "preference", "reflection": "你的反思洞察", "temporal_scope": "pattern", "event_when": null}""",
    "en": """Below are a series of extracted facts about {LANLAN_NAME} and {MASTER_NAME}:

{RELATED_CONTEXT_BLOCK}======以下为事实======
{FACTS}
======以上为事实======

Based on these facts, distill one higher-level reflective insight. Follow these five steps:

Step 1: Determine which entity the reflection primarily concerns
- "master": primarily about {MASTER_NAME}'s personal traits
- "neko": primarily about {LANLAN_NAME}'s self-perception
- "relationship": about the dynamics between them

Step 2: Choose a semantic relation_type (must match the entity)
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement

Step 3: Write the reflection around the chosen entity / relation_type
Requirements:
- Stay focused on a single observation or pattern; do not list facts, and do not mix unrelated facts
- Be concise and clear; the reflection MUST NOT exceed 150 words
- **Do NOT use relative time words like "today / just now / recently / this week" in the reflection text** — specific timing lives in event_when; keep the prose neutral (e.g. "on one occasion", "during that period", "at that time")

Step 4: Classify temporal_scope (one of three — what governs expiry)
- "pattern": persistent mode / personality trait / long-term preference, never expires. e.g. "{MASTER_NAME} loves coffee", "{LANLAN_NAME} is introverted", "long-term mutual reliance".
- "state": currently ongoing situation that naturally expires in weeks. e.g. "{MASTER_NAME} is stressed about work lately", "{LANLAN_NAME} is adjusting to a new role".
- "episode": one specific event, expires in days. e.g. "{MASTER_NAME} pulled an all-nighter coding last night", "{LANLAN_NAME} received a gift today".
- When unsure, prefer pattern (misclassifying pattern as state/episode causes long-term traits to fade prematurely, which is worse than the reverse).

Step 5: Annotate event_when (always use RELATIVE TIME, never absolute dates)
- Schema: {"start": {"offset": <int>, "unit": "<unit>"}, "end": {"offset": <int>, "unit": "<unit>"}}
- offset: negative=past, 0=now, positive=future; unit must be one of minute | hour | day | week | month | year
- start = event start; end = event end (pattern usually omits end, write null)
- **Granularity can be approximate — precision is NOT required.** "a few days ago" → `{"offset": -3, "unit": "day"}`, "last week" → `{"offset": -1, "unit": "week"}`, "a couple months ago" → `month`. If no specific number is given, you may guess one from context; do not over-precise to minute/hour.
- If facts contain no time cue at all (not even "recently"-style hints), write event_when as null (system falls back to creation time)
- Example 1: "went hiking last Monday" → {"start": {"offset": -1, "unit": "week"}, "end": {"offset": -1, "unit": "week"}}
- Example 2: "got a cold today" → {"start": {"offset": 0, "unit": "day"}, "end": null}
- Example 3: long-term "loves coffee" (pattern) → null

Return JSON with fields in this exact order:
{"entity": "master/neko/relationship", "relation_type": "preference", "reflection": "your reflective insight", "temporal_scope": "pattern", "event_when": null}""",
    "ja": """以下は {LANLAN_NAME} と {MASTER_NAME} に関する一連の抽出済み事実です：

{RELATED_CONTEXT_BLOCK}======以下为事实======
{FACTS}
======以上为事实======

これらの事実に基づき、より高次元の反省的洞察を 1 つ抽出してください。次の 5 ステップで進めてください：

ステップ 1：この反省が主に誰についてのものか判断する（entity）
- "master": 主に {MASTER_NAME} の個人的特徴について
- "neko": 主に {LANLAN_NAME} の自己認識について
- "relationship": 二人の関係の動態について

ステップ 2：意味カテゴリ relation_type を選定（entity と整合）
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement

ステップ 3：選定した entity / relation_type に沿って reflection を書く
要件：
- 単一の観察やパターンに集中し、事実を列挙したり、無関係な事実を混ぜたりしないこと
- 簡潔かつ明瞭で、150 字を超えてはならない
- **reflection 本文に「今日／さっき／最近／今週」等の相対時間表現を入れないこと** —— 具体的な時間は event_when に記録し、本文は中性的な語り（「ある時」「その頃」等）にすること

ステップ 4：時間属性 temporal_scope を判定（三択、「いつ期限切れか」を表す）
- "pattern": 持続的なパターン / 性格特性 / 長期的な嗜好、決して期限切れにならない。例：「{MASTER_NAME} はコーヒー好き」「{LANLAN_NAME} は内向的」。
- "state": 現在進行中の情況、数週間で自然に期限切れ。例：「{MASTER_NAME} は最近仕事のストレスが大きい」。
- "episode": 一度きりの具体的な出来事、数日で期限切れ。例：「{MASTER_NAME} は昨夜徹夜でコードを書いた」。
- 迷ったら pattern を選ぶ（pattern を state / episode と誤認すると長期特性が早く消える方が危険）。

ステップ 5：event_when を相対時間で注記（絶対日付禁止）
- 形式：{"start": {"offset": <整数>, "unit": "<単位>"}, "end": {"offset": <整数>, "unit": "<単位>"}}
- offset 負=過去、0=今、正=未来；unit は minute | hour | day | week | month | year のいずれか
- start = 起点、end = 終点（pattern は通常 end=null）
- **粒度は粗くて良い、精度は要求しない**——「数日前」→ `{"offset": -3, "unit": "day"}`、「先週」→ `{"offset": -1, "unit": "week"}`、「数ヶ月前」→ `month` で十分。具体的な数字がない場合は文脈から推測した数字を使ってよく、minute/hour まで細かくする必要はない
- 事実に時間の手掛かりが一切ない場合（「最近」のような暗示すらない場合）は event_when 全体を null にする（システムが作成時刻でフォールバック）
- 例：「先週月曜に登山」→ {"start": {"offset": -1, "unit": "week"}, "end": {"offset": -1, "unit": "week"}}

JSON 形式で返してください。フィールドの順序は以下の通り保ってください：
{"entity": "master/neko/relationship", "relation_type": "preference", "reflection": "あなたの反省的洞察", "temporal_scope": "pattern", "event_when": null}""",
    "ko": """다음은 {LANLAN_NAME}과 {MASTER_NAME}에 대해 추출된 일련의 사실입니다:

{RELATED_CONTEXT_BLOCK}======以下为事实======
{FACTS}
======以上为事实======

이 사실들을 바탕으로 더 높은 차원의 반성적 통찰 하나를 도출해 주세요. 다음 다섯 단계를 따르세요:

1단계: 이 반성이 주로 누구에 대한 것인지 판단합니다 (entity)
- "master": 주로 {MASTER_NAME}의 개인적 특성에 대해
- "neko": 주로 {LANLAN_NAME}의 자기 인식에 대해
- "relationship": 두 사람 사이의 관계 동태에 대해

2단계: 의미 범주 relation_type 선택 (entity와 일치해야 함)
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement

3단계: 선택한 entity / relation_type을 중심으로 reflection을 작성
요구사항:
- 단일 관찰 또는 패턴에 집중하고, 사실을 나열하거나 관련 없는 사실을 섞지 마세요
- 간결하고 명확하게, 150자를 초과해서는 안 됩니다
- **reflection 본문에 "오늘/방금/최근/이번 주" 등 상대 시간 표현을 쓰지 마세요** —— 구체적 시간은 event_when에 기록하고, 본문은 중립적 서술 ("어느 시기에", "그 무렵" 등) 유지

4단계: 시간 속성 temporal_scope 판정 (세 가지 중 하나, 만료 시점을 결정)
- "pattern": 지속적 패턴 / 성격 특성 / 장기 선호, 만료되지 않음. 예: "{MASTER_NAME}는 커피를 좋아함", "{LANLAN_NAME}는 내향적".
- "state": 현재 진행 중인 상황, 몇 주 안에 자연 만료. 예: "{MASTER_NAME}는 최근 업무 스트레스가 큼".
- "episode": 일회성 구체적 사건, 며칠 안에 만료. 예: "{MASTER_NAME}는 어젯밤 밤샘 코딩".
- 모호할 때는 pattern을 선택 (pattern을 state/episode로 오판하면 장기 특성이 일찍 사라져 더 위험함).

5단계: event_when을 상대 시간으로 표기 (절대 날짜 금지)
- 형식: {"start": {"offset": <정수>, "unit": "<단위>"}, "end": {"offset": <정수>, "unit": "<단위>"}}
- offset 음수=과거, 0=현재, 양수=미래; unit은 minute | hour | day | week | month | year 중 하나
- start = 시작점, end = 종료점 (pattern은 보통 end=null)
- **단위는 대략적이어도 됨, 정밀도 요구하지 않음** — "며칠 전" → `{"offset": -3, "unit": "day"}`, "지난주" → `{"offset": -1, "unit": "week"}`, "몇 달 전" → `month` 면 충분. 구체적인 숫자가 없으면 맥락으로 추측한 수치를 써도 되며, minute/hour까지 정밀할 필요는 없음
- 사실에 시간 단서가 전혀 없으면("최근" 같은 암시조차 없으면) event_when 전체를 null로 (시스템이 생성 시각으로 폴백)
- 예: "지난 월요일에 등산" → {"start": {"offset": -1, "unit": "week"}, "end": {"offset": -1, "unit": "week"}}

JSON 형식으로 반환하며, 필드 순서는 다음과 같이 유지하세요:
{"entity": "master/neko/relationship", "relation_type": "preference", "reflection": "당신의 반성적 통찰", "temporal_scope": "pattern", "event_when": null}""",
    "ru": """Ниже представлена серия извлечённых фактов о {LANLAN_NAME} и {MASTER_NAME}:

{RELATED_CONTEXT_BLOCK}======以下为事实======
{FACTS}
======以上为事实======

На основе этих фактов выведите одно рефлексивное наблюдение более высокого уровня. Выполните пять шагов:

Шаг 1: Определите, к кому это наблюдение относится в первую очередь (entity)
- "master": в основном о личных качествах {MASTER_NAME}
- "neko": в основном о самовосприятии {LANLAN_NAME}
- "relationship": о динамике отношений между ними

Шаг 2: Выберите семантическую категорию relation_type (должна соответствовать entity)
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement

Шаг 3: Напишите reflection вокруг выбранных entity / relation_type
Требования:
- Сосредоточьтесь на одном наблюдении или паттерне; не перечисляйте факты и не смешивайте несвязанные факты
- Сжато и ясно; длина НЕ должна превышать 150 слов
- **Не используйте в тексте reflection относительные слова времени "сегодня / только что / недавно / на этой неделе"** —— конкретное время фиксируется в event_when, текст держите нейтральным ("однажды", "в тот период")

Шаг 4: Классифицируйте temporal_scope (один из трёх — определяет срок действия)
- "pattern": устойчивая модель / черта характера / долгосрочное предпочтение, не истекает. Пример: "{MASTER_NAME} любит кофе", "{LANLAN_NAME} интроверт".
- "state": текущая длящаяся ситуация, естественно истекает через недели. Пример: "{MASTER_NAME} в последнее время в стрессе из-за работы".
- "episode": конкретное одноразовое событие, истекает через дни. Пример: "{MASTER_NAME} вчера всю ночь кодил".
- При сомнении предпочитайте pattern (ошибка pattern→state/episode уводит долгосрочные черты раньше времени — хуже обратной).

Шаг 5: Аннотируйте event_when ОТНОСИТЕЛЬНЫМ временем (абсолютные даты запрещены)
- Схема: {"start": {"offset": <целое>, "unit": "<единица>"}, "end": {"offset": <целое>, "unit": "<единица>"}}
- offset: отрицательный=прошлое, 0=сейчас, положительный=будущее; unit ∈ minute | hour | day | week | month | year
- start = начало; end = конец (для pattern обычно end=null)
- **Гранулярность может быть приблизительной, точность НЕ требуется** — "несколько дней назад" → `{"offset": -3, "unit": "day"}`, "на прошлой неделе" → `{"offset": -1, "unit": "week"}`, "несколько месяцев назад" → `month`. Если конкретное число не указано, можно угадать его из контекста; не уточняйте до minute/hour
- Если в фактах нет никаких временных меток (даже намёков вроде "недавно"), всё event_when = null (система подставит время создания)
- Пример: "ходил в горы в прошлый понедельник" → {"start": {"offset": -1, "unit": "week"}, "end": {"offset": -1, "unit": "week"}}

Верните в формате JSON, сохраняя порядок полей:
{"entity": "master/neko/relationship", "relation_type": "preference", "reflection": "ваше рефлексивное наблюдение", "temporal_scope": "pattern", "event_when": null}""",
    "es": """A continuación hay una serie de hechos extraídos sobre {LANLAN_NAME} y {MASTER_NAME}:

{RELATED_CONTEXT_BLOCK}======以下为事实======
{FACTS}
======以上为事实======

Con base en estos hechos, destila una sola reflexión de nivel superior. Sigue estos cinco pasos:

Paso 1: Determina a qué entidad se refiere principalmente la reflexión
- "master": principalmente sobre rasgos personales de {MASTER_NAME}
- "neko": principalmente sobre la autopercepción de {LANLAN_NAME}
- "relationship": sobre la dinámica entre ambos

Paso 2: Elige una relation_type semántica (debe coincidir con la entidad)
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement

Paso 3: Escribe la reflection alrededor de entity / relation_type elegidos
Requisitos:
- Céntrate en una sola observación o patrón; no enumeres hechos ni mezcles hechos no relacionados
- Sé conciso y claro; la reflexión NO debe superar 150 palabras
- **No uses palabras relativas de tiempo "hoy / hace un momento / recientemente / esta semana" en el texto** —— el tiempo concreto se registra en event_when; mantén la prosa neutra ("en una ocasión", "durante ese período")

Paso 4: Clasifica temporal_scope (uno de tres — gobierna la caducidad)
- "pattern": modo persistente / rasgo de personalidad / preferencia a largo plazo, nunca caduca. Ej.: "{MASTER_NAME} ama el café", "{LANLAN_NAME} es introvertido/a".
- "state": situación actual en curso, caduca naturalmente en semanas. Ej.: "{MASTER_NAME} está estresado/a por el trabajo últimamente".
- "episode": un evento específico, caduca en días. Ej.: "{MASTER_NAME} pasó la noche programando ayer".
- Cuando dudes, prefiere pattern (clasificar pattern como state/episode hace que los rasgos a largo plazo se desvanezcan prematuramente, lo cual es peor).

Paso 5: Anota event_when con TIEMPO RELATIVO (prohibidas fechas absolutas)
- Esquema: {"start": {"offset": <entero>, "unit": "<unidad>"}, "end": {"offset": <entero>, "unit": "<unidad>"}}
- offset: negativo=pasado, 0=ahora, positivo=futuro; unit ∈ minute | hour | day | week | month | year
- start = inicio; end = fin (para pattern usualmente end=null)
- **La granularidad puede ser aproximada, NO se requiere precisión** — "hace unos días" → `{"offset": -3, "unit": "day"}`, "la semana pasada" → `{"offset": -1, "unit": "week"}`, "hace unos meses" → `month`. Si no se da un número concreto, puedes inferirlo del contexto; no afines a minute/hour
- Si los hechos no contienen ninguna pista temporal (ni siquiera insinuaciones como "recientemente"), escribe event_when como null (el sistema usa el tiempo de creación)
- Ej.: "fui de excursión el lunes pasado" → {"start": {"offset": -1, "unit": "week"}, "end": {"offset": -1, "unit": "week"}}

Devuelve JSON con los campos en este orden exacto:
{"entity": "master/neko/relationship", "relation_type": "preference", "reflection": "tu reflexión", "temporal_scope": "pattern", "event_when": null}""",
    "pt": """Abaixo há uma série de fatos extraídos sobre {LANLAN_NAME} e {MASTER_NAME}:

{RELATED_CONTEXT_BLOCK}======以下为事实======
{FACTS}
======以上为事实======

Com base nesses fatos, extraia uma única reflexão de nível superior. Siga estes cinco passos:

Passo 1: Determine a qual entidade a reflexão se refere principalmente
- "master": principalmente sobre características pessoais de {MASTER_NAME}
- "neko": principalmente sobre a autopercepção de {LANLAN_NAME}
- "relationship": sobre a dinâmica entre os dois

Passo 2: Escolha uma relation_type semântica (deve corresponder à entidade)
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement

Passo 3: Escreva a reflection em torno de entity / relation_type escolhidos
Requisitos:
- Foque em uma única observação ou padrão; não liste fatos nem misture fatos não relacionados
- Seja conciso e claro; a reflexão NÃO deve exceder 150 palavras
- **Não use palavras de tempo relativo "hoje / agora mesmo / recentemente / esta semana" no texto** —— o tempo concreto fica em event_when; mantenha a prosa neutra ("em certa ocasião", "naquele período")

Passo 4: Classifique temporal_scope (um dos três — define a expiração)
- "pattern": modo persistente / traço de personalidade / preferência de longo prazo, nunca expira. Ex.: "{MASTER_NAME} adora café", "{LANLAN_NAME} é introvertido/a".
- "state": situação atualmente em andamento, expira naturalmente em semanas. Ex.: "{MASTER_NAME} anda estressado/a com o trabalho".
- "episode": um evento específico, expira em dias. Ex.: "{MASTER_NAME} virou a noite codando ontem".
- Quando em dúvida, prefira pattern (classificar pattern como state/episode faz traços de longo prazo desvanecerem cedo demais, o que é pior).

Passo 5: Anote event_when com TEMPO RELATIVO (datas absolutas proibidas)
- Esquema: {"start": {"offset": <inteiro>, "unit": "<unidade>"}, "end": {"offset": <inteiro>, "unit": "<unidade>"}}
- offset: negativo=passado, 0=agora, positivo=futuro; unit ∈ minute | hour | day | week | month | year
- start = início; end = fim (para pattern geralmente end=null)
- **A granularidade pode ser aproximada, NÃO se exige precisão** — "há alguns dias" → `{"offset": -3, "unit": "day"}`, "semana passada" → `{"offset": -1, "unit": "week"}`, "há alguns meses" → `month`. Se não houver um número específico, você pode estimá-lo pelo contexto; não detalhe minute/hour
- Se os fatos não tiverem nenhuma pista temporal (nem insinuações como "recentemente"), escreva event_when como null (o sistema usa o horário de criação)
- Ex.: "fui escalar segunda-feira passada" → {"start": {"offset": -1, "unit": "week"}, "end": {"offset": -1, "unit": "week"}}

Retorne JSON com os campos nesta ordem exata:
{"entity": "master/neko/relationship", "relation_type": "preference", "reflection": "sua reflexão", "temporal_scope": "pattern", "event_when": null}""",
}


def get_reflection_prompt(lang: str = "zh") -> str:
    return _loc(REFLECTION_PROMPT, lang)


reflection_prompt = REFLECTION_PROMPT["zh"]


# =====================================================================
# ======= Memory schema v1→v2 recheck prompts (memory_server background) =
# =====================================================================
# 慢速重判循环用 — 给老版本（schema_version<2）reflection / fact 补标
# temporal_scope + event_when。每 30 秒一条，非实时关键路径，Chinese-only
# 以减少 prompt 维护成本（reflection / fact text 本身可以是任何语言，LLM
# 不依赖 prompt 语言就能阅读和判定）。
#
# Anchor 语义：所有相对时间偏移都以 reflection.created_at / fact.created_at
# 为锚点。LLM 看到的"3 天前"指"早于 created_at 3 天"，系统按此减去对应天数
# 算出绝对 ISO 写回 event_start_at / event_end_at。

MEMORY_RECHECK_REFLECTION_PROMPT = """以下是一条老版本 reflection 条目（已 confirmed / promoted），需要按新版本 schema 重新标注两个字段。

reflection 文本（原文不要改动）：
======以下为原文======
{REFLECTION_TEXT}
======以上为原文======

该 reflection 由系统在 {CREATED_AT} 创建。请把这个时刻当作"now"参照——以下问到的时间偏移都相对这个时刻。

相关 source facts（仅供时间线索参考，可能为空）：
======以下为线索======
{SOURCE_FACTS}
======以上为线索======

请输出两个字段：

1) temporal_scope（三档之一，反映"是否会过期"）：
   - "pattern": 持续模式 / 性格特质 / 长期偏好，永不过期。例：「喜欢咖啡」「性格内向」「长期相互依赖」。
   - "state": 当下持续的情境，几周内自然过期。例：「最近压力大」「这段时间适应新环境」。
   - "episode": 一次具体事件，几天内过期。例：「某次通宵」「收到一份礼物」。
   - 拿不准时倾向选 pattern（误判 pattern 当 state/episode 会让长期特征过早淡出，比反过来更危险）。

2) event_when（事件发生时间，一律相对偏移，禁止绝对日期）：
   - 格式：{{"start": {{"offset": <整数>, "unit": "<单位>"}}, "end": {{"offset": <整数>, "unit": "<单位>"}}}}
   - offset 负值=过去（相对上面 CREATED_AT 锚点）；0=锚点当下；正值=未来
   - unit 必须是 minute | hour | day | week | month | year 之一
   - start = 事件起点；end = 事件终点（pattern 类通常省略 end，写 null）
   - **粒度可以粗，不要求精确**——"前几天"→ day、"上周"→ week、"几个月前"→ month 即可；不必精确到 minute/hour（没有具体数字的话，可以根据上下文猜测一个数字）
   - 如果文本里有"3 天前"、"上周"之类的明显时间线索，请用对应偏移；找不到任何线索就把整个 event_when 写 null（系统兜底为 CREATED_AT 当下）

请以 JSON 格式返回，字段顺序保持如下：
{{"temporal_scope": "pattern", "event_when": null}}"""


# Past memory block (memory/persona.py `_compose_markdown_from_trimmed`) i18n。
# 每条目前缀 [X 天前 / X 周前 / X 月前] 由 memory.temporal.time_since_label
# 按 active language 生成；这里只管 block 整体的开头介绍 + 六等号 below/above
# 对偶分隔符（参见 feedback_prompt_delimiters_above_below.md：内部禁冒号破折号）。
#
# 占位符：
#   {AI_NAME}     —— 当前角色名（如 "小天"）
#   {MASTER_NAME} —— 用户的 master_name
PAST_MEMORY_BLOCK = {
    "zh": (
        "======以下为较久前的记忆======\n"
        "说明：下列条目是 {AI_NAME} 较早之前形成的印象，仅作背景知识。"
        "除非 {MASTER_NAME} 先主动提起，否则 {AI_NAME} 不要主动唤起或追问相关内容。\n"
        "{ITEMS}\n"
        "======以上为较久前的记忆======"
    ),
    "en": (
        "======Below is older memory======\n"
        "Note: the following items are impressions {AI_NAME} formed a while ago, included only as background. "
        "Unless {MASTER_NAME} brings them up first, {AI_NAME} should not volunteer or probe these topics.\n"
        "{ITEMS}\n"
        "======Above is older memory======"
    ),
    "ja": (
        "======以下は過去の記憶======\n"
        "注：以下は {AI_NAME} が以前形成した印象であり、背景知識としてのみ提示します。"
        "{MASTER_NAME} から先に話題に出さない限り、{AI_NAME} は自発的にこれらの内容を持ち出したり追及したりしてはいけません。\n"
        "{ITEMS}\n"
        "======以上は過去の記憶======"
    ),
    "ko": (
        "======아래는 오래된 기억======\n"
        "참고: 아래 항목들은 {AI_NAME}이(가) 예전에 형성한 인상으로, 배경 지식으로만 제시됩니다. "
        "{MASTER_NAME}이(가) 먼저 꺼내지 않는 한 {AI_NAME}은(는) 스스로 이 내용을 꺼내거나 캐묻지 마세요.\n"
        "{ITEMS}\n"
        "======위는 오래된 기억======"
    ),
    "ru": (
        "======Ниже давние воспоминания======\n"
        "Примечание: следующие пункты — это впечатления, сформированные {AI_NAME} ранее, и приводятся только как фоновая информация. "
        "Если {MASTER_NAME} не поднимет эти темы первым, {AI_NAME} не должен(на) сам(а) их затрагивать или расспрашивать.\n"
        "{ITEMS}\n"
        "======Выше давние воспоминания======"
    ),
    "es": (
        "======Abajo recuerdos antiguos======\n"
        "Nota: los siguientes elementos son impresiones que {AI_NAME} formó hace un tiempo y se incluyen solo como contexto de fondo. "
        "A menos que {MASTER_NAME} los mencione primero, {AI_NAME} no debe sacarlos por iniciativa propia ni indagar sobre ellos.\n"
        "{ITEMS}\n"
        "======Arriba recuerdos antiguos======"
    ),
    "pt": (
        "======Abaixo memórias antigas======\n"
        "Nota: os itens a seguir são impressões que {AI_NAME} formou há algum tempo, incluídos apenas como contexto de fundo. "
        "A menos que {MASTER_NAME} os mencione primeiro, {AI_NAME} não deve trazê-los por iniciativa própria nem investigá-los.\n"
        "{ITEMS}\n"
        "======Acima memórias antigas======"
    ),
}


def render_past_memory_block(
    lang: str,
    ai_name: str,
    master_name: str,
    items_text: str,
) -> str:
    """Render the localized past-memory section. `items_text` is a pre-formatted
    bullet list (each line ``- [time-label] reflection text``)."""
    tmpl = _loc(PAST_MEMORY_BLOCK, lang)
    return (
        tmpl
        .replace('{AI_NAME}', ai_name)
        .replace('{MASTER_NAME}', master_name)
        .replace('{ITEMS}', items_text)
    )


SUMMARY_STALE_HINT = {
    "zh": """======以下为时间衰减提醒======
距上次记忆压缩已过去 {GAP} 小时。请在 summary 中，把已过时的内容（已结束的事件、已变化的状态、不再相关的近况）单独放到 summary 文末的"较久前"段落，用"X 时间前曾经..."的中性叙事；当前仍持续或重要的内容保留在 summary 主体。
[格式硬约束] 主体段与"较久前"段之间，必须用单独一行 `---`（三个英文连字符）作分界，前后各空一行。整段 summary 里只能出现这一处 `---`；如果没有过时内容需要写"较久前"段，则**不要**输出 `---`。
本提醒只影响本次 summary 生成，不进入长期记忆。
======以上为时间衰减提醒======""",
    "en": """======Below is time decay notice======
{GAP} hours have passed since the last memory compression. In the summary, move clearly outdated content (ended events, changed states, no-longer-relevant updates) into a separate "older" paragraph at the end of the summary using neutral narration like "some time ago, X used to...". Keep currently ongoing or important content in the summary body.
[Format constraint] Between the main body and the "older" paragraph, you MUST insert a single line containing only `---` (three ASCII hyphens), surrounded by blank lines above and below. This `---` may appear at most once in the entire summary; if there is no outdated content to write an "older" paragraph for, do NOT emit `---`.
This notice only affects the current summary generation; it does not enter long-term memory.
======Above is time decay notice======""",
    "ja": """======以下は時間経過リマインダー======
前回のメモリ圧縮から {GAP} 時間が経過しています。summary では明らかに古くなった内容（終了したイベント、変化した状態、関連性の薄れた近況）を summary 末尾の「以前」段落にまとめ、「以前 X だった」のような中立的な語りで記述してください。現在も継続中・重要な内容は summary 本体に残します。
[フォーマット制約] 本体段落と「以前」段落の間には、必ず単独行 `---`（半角ハイフン3つ）を区切りとして挿入し、その上下を空行で囲んでください。`---` は summary 全体で1回までしか現れません。書くべき「以前」段落がなければ `---` を**出力しないで**ください。
この通知は今回の summary 生成にのみ影響し、長期記憶には入りません。
======以上は時間経過リマインダー======""",
    "ko": """======아래는 시간 경과 알림======
지난 메모리 압축으로부터 {GAP} 시간이 지났습니다. summary에서 명백히 오래된 내용(이미 끝난 사건, 바뀐 상태, 더 이상 관련 없는 근황)은 summary 끝의 "이전" 단락으로 옮기고, "예전에 X였다" 같은 중립적 서술로 작성하세요. 현재 진행 중이거나 중요한 내용은 summary 본문에 남깁니다.
[형식 제약] 본문 단락과 "이전" 단락 사이에는 반드시 `---`(ASCII 하이픈 3개)만 들어간 단독 줄을 구분선으로 넣고, 그 위아래에 빈 줄을 둡니다. 전체 summary 안에서 `---`는 최대 1회만 등장합니다. 작성할 "이전" 단락이 없으면 `---`를 **출력하지 마세요**.
이 알림은 이번 summary 생성에만 영향을 주며, 장기 기억에는 들어가지 않습니다.
======위는 시간 경과 알림======""",
    "ru": """======Ниже напоминание о времени======
С последнего сжатия памяти прошло {GAP} часов. В summary вынесите явно устаревшие пункты (завершившиеся события, изменившиеся состояния, неактуальные новости) в отдельный абзац «ранее» в конце summary, описывая их нейтрально («ранее X было...»). Актуальное и важное оставьте в основной части summary.
[Жёсткий формат] Между основным абзацем и абзацем «ранее» обязательно вставьте отдельную строку, содержащую только `---` (три ASCII-дефиса), с пустыми строками сверху и снизу. Во всём summary `---` может встретиться не более одного раза. Если устаревшего контента для абзаца «ранее» нет, **не выводите** `---`.
Это напоминание влияет только на текущую генерацию summary и не попадает в долговременную память.
======Выше напоминание о времени======""",
    "es": """======Abajo aviso de decaimiento temporal======
Han pasado {GAP} horas desde la última compresión de memoria. En el summary, mueve el contenido claramente obsoleto (eventos terminados, estados cambiados, actualizaciones ya no relevantes) a un párrafo "antes" al final del summary, con narración neutra como "tiempo atrás X solía...". Mantén el contenido actualmente en curso o importante en el cuerpo del summary.
[Restricción de formato] Entre el cuerpo principal y el párrafo "antes" debes insertar una línea aislada que contenga únicamente `---` (tres guiones ASCII), rodeada por líneas vacías arriba y abajo. En todo el summary `---` puede aparecer como máximo una vez. Si no hay contenido obsoleto para un párrafo "antes", **no emitas** `---`.
Este aviso solo afecta la generación actual del summary; no entra en memoria de largo plazo.
======Arriba aviso de decaimiento temporal======""",
    "pt": """======Abaixo aviso de decaimento temporal======
Passaram-se {GAP} horas desde a última compressão de memória. No summary, mova o conteúdo claramente desatualizado (eventos terminados, estados alterados, atualizações já irrelevantes) para um parágrafo "antes" no final do summary, com narração neutra como "tempos atrás, X costumava...". Mantenha o conteúdo atualmente em andamento ou importante no corpo do summary.
[Restrição de formato] Entre o corpo principal e o parágrafo "antes" você deve inserir uma linha isolada contendo apenas `---` (três hifens ASCII), cercada por linhas em branco acima e abaixo. Em todo o summary `---` pode aparecer no máximo uma vez. Se não houver conteúdo desatualizado para um parágrafo "antes", **não emita** `---`.
Este aviso afeta apenas a geração atual do summary; não entra na memória de longo prazo.
======Acima aviso de decaimento temporal======""",
}


def get_summary_stale_hint(lang: str, gap_hours: float) -> str:
    """Return locale-formatted stale hint for compress_history.

    gap_hours is rounded to one decimal ("1.5 小时" / "1.5 hours"). Unknown lang falls back to zh.
    """  # noqa: DOCSTRING_CJK
    tmpl = _loc(SUMMARY_STALE_HINT, lang)
    return tmpl.replace('{GAP}', f"{gap_hours:.1f}")


MEMORY_RECHECK_FACT_PROMPT = """以下是一条老版本 fact 条目，需要按新版本 schema 补标 event_when 字段。

fact 文本（原文不要改动）：
======以下为原文======
{FACT_TEXT}
======以上为原文======

该 fact 由系统在 {CREATED_AT} 创建。请把这个时刻当作"now"参照——event_when 的偏移相对这个时刻。

请输出 event_when（事件发生时间，一律相对偏移，禁止绝对日期）：
- 格式：{{"start": {{"offset": <整数>, "unit": "<单位>"}}, "end": {{"offset": <整数>, "unit": "<单位>"}}}}
- offset 负值=过去（相对上面 CREATED_AT 锚点）；0=锚点当下；正值=未来
- unit 必须是 minute | hour | day | week | month | year 之一
- start = 事件起点；end = 事件终点（多数 fact 是即时观察，end 可写 null 省略）
- **粒度可以粗，不要求精确**——"几天前"→ day、"上周"→ week、"几个月前"→ month 即可；不必精确到 minute/hour（没有具体数字的话，可以根据上下文猜测一个数字）
- 如果文本里有"3 天前"、"昨天"、"上个月"之类明显的时间线索，用对应偏移；
- 如果是长期事实（"喜欢咖啡"），整个 event_when 写 null（系统兜底为 CREATED_AT 当下）

请以 JSON 格式返回：
{{"event_when": null}}"""


# ---------- reflection_feedback_prompt → i18n dict ----------

REFLECTION_FEEDBACK_PROMPT = {
    "zh": """以下是之前向用户提到的一些观察。请根据用户最近的回复，判断用户对每条观察的态度。

======以下为观察======
{reflections}
======以上为观察======

用户最近的消息：
{messages}

对于每条观察，判断：
- confirmed: 用户明确同意、默认接受、或继续相关话题
- denied: 用户明确否认或纠正
- ignored: 用户没有回应这条观察

仅输出 JSON 数组，不要输出其他内容。
[{{"reflection_id": "xxx", "feedback": "confirmed"}}]""",
    "en": """Below are some observations previously mentioned to the user. Based on the user's recent replies, determine the user's attitude toward each observation.

======以下为观察======
{reflections}
======以上为观察======

User's recent messages:
{messages}

For each observation, determine:
- confirmed: user explicitly agreed, tacitly accepted, or continued the related topic
- denied: user explicitly denied or corrected it
- ignored: user did not respond to this observation

Output only a JSON array, nothing else.
[{{"reflection_id": "xxx", "feedback": "confirmed"}}]""",
    "ja": """以下は以前ユーザーに言及した観察です。ユーザーの最近の返答に基づき、各観察に対するユーザーの態度を判断してください。

======以下为观察======
{reflections}
======以上为观察======

ユーザーの最近のメッセージ：
{messages}

各観察について判断：
- confirmed: ユーザーが明確に同意、暗黙的に受け入れ、または関連トピックを続行
- denied: ユーザーが明確に否定または訂正
- ignored: ユーザーがこの観察に応答しなかった

JSON配列のみを出力し、他の内容は出力しないでください。
[{{"reflection_id": "xxx", "feedback": "confirmed"}}]""",
    "ko": """다음은 이전에 사용자에게 언급한 관찰들입니다. 사용자의 최근 답변을 바탕으로 각 관찰에 대한 사용자의 태도를 판단해 주세요.

======以下为观察======
{reflections}
======以上为观察======

사용자의 최근 메시지:
{messages}

각 관찰에 대해 판단:
- confirmed: 사용자가 명확히 동의, 묵시적으로 수용, 또는 관련 주제를 계속함
- denied: 사용자가 명확히 부인하거나 수정함
- ignored: 사용자가 이 관찰에 응답하지 않음

JSON 배열만 출력하고 다른 내용은 출력하지 마세요.
[{{"reflection_id": "xxx", "feedback": "confirmed"}}]""",
    "ru": """Ниже приведены наблюдения, ранее упомянутые пользователю. На основе недавних ответов пользователя определите его отношение к каждому наблюдению.

======以下为观察======
{reflections}
======以上为观察======

Недавние сообщения пользователя:
{messages}

Для каждого наблюдения определите:
- confirmed: пользователь явно согласился, молчаливо принял или продолжил связанную тему
- denied: пользователь явно отрицал или исправил
- ignored: пользователь не отреагировал на это наблюдение

Выведите только JSON-массив, ничего другого.
[{{"reflection_id": "xxx", "feedback": "confirmed"}}]""",
    "es": """A continuación hay algunas observaciones mencionadas previamente al usuario. Según las respuestas recientes del usuario, determina su actitud hacia cada observación.

======以下为观察======
{reflections}
======以上为观察======

Mensajes recientes del usuario:
{messages}

Para cada observación, determina:
- confirmed: el usuario estuvo claramente de acuerdo, la aceptó tácitamente o continuó el tema relacionado
- denied: el usuario la negó o corrigió claramente
- ignored: el usuario no respondió a esta observación

Devuelve solo un array JSON, nada más.
[{{"reflection_id": "xxx", "feedback": "confirmed"}}]""",
    "pt": """Abaixo estão algumas observações mencionadas anteriormente ao usuário. Com base nas respostas recentes do usuário, determine a atitude dele em relação a cada observação.

======以下为观察======
{reflections}
======以上为观察======

Mensagens recentes do usuário:
{messages}

Para cada observação, determine:
- confirmed: o usuário concordou claramente, aceitou tacitamente ou continuou o tópico relacionado
- denied: o usuário negou ou corrigiu claramente
- ignored: o usuário não respondeu a esta observação

Retorne apenas um array JSON, nada mais.
[{{"reflection_id": "xxx", "feedback": "confirmed"}}]""",
}


def get_reflection_feedback_prompt(lang: str = "zh") -> str:
    return _loc(REFLECTION_FEEDBACK_PROMPT, lang)


reflection_feedback_prompt = REFLECTION_FEEDBACK_PROMPT["zh"]

# =====================================================================
# ======= Promotion merge (RFC §3.9.7) ===============================
# =====================================================================
# 当 reflection 的 evidence_score 穿过 EVIDENCE_PROMOTED_THRESHOLD 时，
# `_apromote_with_merge` 调用 LLM 在 promote_fresh / merge_into / reject
# 三选一。LLM 失败不静默降级到 promote_fresh（§3.9.4），所以 prompt 必
# 须给出明确判定边界。
#
# 双水印（§3.9.7）：
#   - 印象池块界 watermark: "======以上为现有印象池======"
# 翻译时按 CLAUDE.md 规约：水印行 (`======以上为...======`) 保留中文，
# 不翻译——审计时用以快速定位 prompt 边界。
PROMOTION_MERGE_PROMPT = {
    "zh": """你是一个长期印象整理专家。你在维护 {AI_NAME} 对 {MASTER_NAME} 的长期印象。现在有一条待晋升的观察：

  R: "{R_TEXT}"
  R.evidence_score: {R_SCORE}

======以下是 {AI_NAME} 关于 {MASTER_NAME} 的现有印象池======
（已 promoted 的 persona fact + 其它 confirmed 的 reflection）

{IMPRESSION_POOL}
======以上为现有印象池======

请判断 R 应该：

- promote_fresh：作为新 persona fact 独立收录（和现有任何条目都不重复、不矛盾）
- merge_into：和某条现有 persona entry 语义相近，应合并。返回 target_id（**必须**来自上面"现有印象池"区里的 persona.* 条目，不要合并到 reflection 条目）和合并后的文本。
- reject：和现有某条明确矛盾且 R 证据弱于对方，不应收录。返回 reason。

只输出合法 JSON，不要任何额外文本：
{{"action": "promote_fresh", "reason": "为什么独立收录"}}
或
{{"action": "merge_into", "target_id": "persona.master.p_001", "merged_text": "合并后的完整描述"}}
或
{{"action": "reject", "reason": "与某条矛盾的简短说明"}}""",
    "en": """You are a long-term impression curator. You maintain {AI_NAME}'s long-term impressions of {MASTER_NAME}. A new observation is pending promotion:

  R: "{R_TEXT}"
  R.evidence_score: {R_SCORE}

======以下是 {AI_NAME} 关于 {MASTER_NAME} 的现有印象池======
(promoted persona facts + other confirmed reflections)

{IMPRESSION_POOL}
======以上为现有印象池======

Decide whether R should be:

- promote_fresh: recorded as a new standalone persona fact (does not duplicate or contradict anything above).
- merge_into: semantically close to one existing persona entry — merge them. Return `target_id` (which **MUST** be one of the `persona.*` entries listed above; never merge into a `reflection.*` entry) and the merged text.
- reject: directly contradicts an existing entry whose evidence is stronger than R; do not record. Return `reason`.

Output only valid JSON — no extra text:
{{"action": "promote_fresh", "reason": "why standalone"}}
or
{{"action": "merge_into", "target_id": "persona.master.p_001", "merged_text": "full merged description"}}
or
{{"action": "reject", "reason": "short note on the contradiction"}}""",
    "ja": """あなたは長期的な印象を整理する専門家です。{AI_NAME} の {MASTER_NAME} に対する長期的な印象を管理しています。次の観察が昇格待ちです：

  R: "{R_TEXT}"
  R.evidence_score: {R_SCORE}

======以下是 {AI_NAME} 关于 {MASTER_NAME} 的现有印象池======
（既に promoted の persona fact ＋ 他の confirmed の reflection）

{IMPRESSION_POOL}
======以上为现有印象池======

R をどう扱うか判断してください：

- promote_fresh：新たな persona fact として独立収録（上のどの項目とも重複・矛盾しない）。
- merge_into：既存の persona エントリと意味的に近いので統合。`target_id` を返す（**必ず**上の "現有印象池" にある `persona.*` を選ぶこと。`reflection.*` への統合は禁止）、統合後の本文も返す。
- reject：既存のいずれかと明確に矛盾し R の証拠の方が弱い場合は収録しない。`reason` を返す。

合法な JSON のみを出力し、追加テキストは禁止：
{{"action": "promote_fresh", "reason": "独立収録の理由"}}
または
{{"action": "merge_into", "target_id": "persona.master.p_001", "merged_text": "統合後の完全な記述"}}
または
{{"action": "reject", "reason": "矛盾する内容の簡潔な説明"}}""",
    "ko": """당신은 장기 인상을 정리하는 전문가입니다. {AI_NAME}의 {MASTER_NAME}에 대한 장기 인상을 관리합니다. 승격 대기 중인 관찰입니다:

  R: "{R_TEXT}"
  R.evidence_score: {R_SCORE}

======以下是 {AI_NAME} 关于 {MASTER_NAME} 的现有印象池======
(이미 promoted된 persona fact + 기타 confirmed reflection)

{IMPRESSION_POOL}
======以上为现有印象池======

R을 어떻게 처리할지 판단하세요:

- promote_fresh: 새로운 persona fact로 독립 수록 (위의 어떤 항목과도 중복/모순되지 않음).
- merge_into: 기존 persona 항목과 의미가 가까워 병합. `target_id` (반드시 위의 "现有印象池"에서 `persona.*` 항목 중 하나여야 함; `reflection.*`로의 병합은 금지)와 병합된 텍스트를 반환.
- reject: 기존의 어떤 항목과 명확히 모순되며 R의 근거가 더 약한 경우, 수록하지 않음. `reason`을 반환.

유효한 JSON만 출력하고 추가 텍스트는 출력하지 마세요:
{{"action": "promote_fresh", "reason": "독립 수록 이유"}}
또는
{{"action": "merge_into", "target_id": "persona.master.p_001", "merged_text": "병합된 전체 서술"}}
또는
{{"action": "reject", "reason": "모순에 대한 짧은 설명"}}""",
    "ru": """Вы — куратор долгосрочных впечатлений. Вы поддерживаете долгосрочные впечатления {AI_NAME} о {MASTER_NAME}. На повышение ожидает наблюдение:

  R: "{R_TEXT}"
  R.evidence_score: {R_SCORE}

======以下是 {AI_NAME} 关于 {MASTER_NAME} 的现有印象池======
(уже promoted-факты persona + другие confirmed-reflection)

{IMPRESSION_POOL}
======以上为现有印象池======

Решите, как обработать R:

- promote_fresh: записать как новый отдельный persona-факт (не дублирует и не противоречит ничему выше).
- merge_into: семантически близок одной существующей persona-записи — объединить. Верните `target_id` (**обязательно** один из `persona.*` записей выше; объединение в `reflection.*` запрещено) и итоговый текст.
- reject: явно противоречит существующей записи, чьи свидетельства сильнее R; не записывать. Верните `reason`.

Выводите только валидный JSON, без лишнего текста:
{{"action": "promote_fresh", "reason": "почему отдельная запись"}}
или
{{"action": "merge_into", "target_id": "persona.master.p_001", "merged_text": "полный объединённый текст"}}
или
{{"action": "reject", "reason": "краткое описание противоречия"}}""",
    "es": """Eres curador de impresiones de largo plazo. Mantienes las impresiones de largo plazo de {AI_NAME} sobre {MASTER_NAME}. Hay una nueva observación pendiente de promoción:

  R: "{R_TEXT}"
  R.evidence_score: {R_SCORE}

======以下是 {AI_NAME} 关于 {MASTER_NAME} 的现有印象池======
(persona facts ya promoted + otras reflections confirmed)

{IMPRESSION_POOL}
======以上为现有印象池======

Decide si R debe ser:

- promote_fresh: registrarse como un nuevo persona fact independiente (no duplica ni contradice nada de arriba).
- merge_into: semánticamente cercana a una entrada persona existente; combínalas. Devuelve `target_id` (que **DEBE** ser una de las entradas `persona.*` listadas arriba; nunca combines en una entrada `reflection.*`) y el texto combinado.
- reject: contradice directamente una entrada existente con evidencia más fuerte que R; no la registres. Devuelve `reason`.

Devuelve solo JSON válido, sin texto extra:
{{"action": "promote_fresh", "reason": "por qué es independiente"}}
o
{{"action": "merge_into", "target_id": "persona.master.p_001", "merged_text": "descripción combinada completa"}}
o
{{"action": "reject", "reason": "nota breve sobre la contradicción"}}""",
    "pt": """Você é curador de impressões de longo prazo. Você mantém as impressões de longo prazo de {AI_NAME} sobre {MASTER_NAME}. Há uma nova observação pendente de promoção:

  R: "{R_TEXT}"
  R.evidence_score: {R_SCORE}

======以下是 {AI_NAME} 关于 {MASTER_NAME} 的现有印象池======
(persona facts já promoted + outras reflections confirmed)

{IMPRESSION_POOL}
======以上为现有印象池======

Decida se R deve ser:

- promote_fresh: registrada como um novo persona fact independente (não duplica nem contradiz nada acima).
- merge_into: semanticamente próxima de uma entrada persona existente; combine-as. Retorne `target_id` (que **DEVE** ser uma das entradas `persona.*` listadas acima; nunca combine em uma entrada `reflection.*`) e o texto combinado.
- reject: contradiz diretamente uma entrada existente cuja evidência é mais forte que R; não registre. Retorne `reason`.

Retorne apenas JSON válido, sem texto extra:
{{"action": "promote_fresh", "reason": "por que é independente"}}
ou
{{"action": "merge_into", "target_id": "persona.master.p_001", "merged_text": "descrição combinada completa"}}
ou
{{"action": "reject", "reason": "nota breve sobre a contradição"}}""",
}


def get_promotion_merge_prompt(lang: str = "zh") -> str:
    return _loc(PROMOTION_MERGE_PROMPT, lang)


promotion_merge_prompt = PROMOTION_MERGE_PROMPT["zh"]

# ---------- persona_correction_prompt → i18n dict ----------

PERSONA_CORRECTION_PROMPT = {
    "zh": """以下是 {count} 组可能矛盾的记忆条目，请逐组判断应如何处理。

======以下为记忆条目======
{pairs}
======以上为记忆条目======

对于每组，判断：
- merge: 把新观察与旧记忆融合成一条，提供合并后的 text
- keep_new: 新观察完全取代旧记忆
- keep_old: 旧记忆更准确
- keep_both: 两者不矛盾，只是话题相似

仅输出 JSON 数组，每项包含 index、action、text(可选)。
[{{"index": 0, "action": "merge", "text": "合并后的文本"}}]""",
    "en": """Below are {count} pairs of potentially contradictory memory entries. Please evaluate each pair and determine how to handle it.

======以下为记忆条目======
{pairs}
======以上为记忆条目======

For each pair, determine:
- merge: fuse the new observation with the old memory into a single entry — provide the merged text
- keep_new: the new observation completely replaces the old memory
- keep_old: the old memory is more accurate
- keep_both: they do not contradict — the topics are merely similar

Output only a JSON array. Each item should contain index, action, and text (optional).
[{{"index": 0, "action": "merge", "text": "merged text"}}]""",
    "ja": """以下は {count} 組の矛盾する可能性のある記憶エントリです。各組について処理方法を判断してください。

======以下为记忆条目======
{pairs}
======以上为记忆条目======

各組について判断：
- merge: 新しい観察と古い記憶を一つに融合 — 統合後のテキストを提供
- keep_new: 新しい観察が古い記憶を完全に置き換える
- keep_old: 古い記憶の方が正確
- keep_both: 矛盾していない、トピックが類似しているだけ

JSON配列のみを出力。各項目には index、action、text（任意）を含めてください。
[{{"index": 0, "action": "merge", "text": "統合後のテキスト"}}]""",
    "ko": """다음은 {count}쌍의 잠재적으로 모순되는 기억 항목입니다. 각 쌍을 평가하고 처리 방법을 결정해 주세요.

======以下为记忆条目======
{pairs}
======以上为记忆条目======

각 쌍에 대해 판단:
- merge: 새로운 관찰을 오래된 기억과 하나로 융합 — 병합된 text를 제공
- keep_new: 새로운 관찰이 오래된 기억을 완전히 대체
- keep_old: 오래된 기억이 더 정확
- keep_both: 모순되지 않음, 주제가 유사할 뿐

JSON 배열만 출력하세요. 각 항목에는 index, action, text(선택)를 포함하세요.
[{{"index": 0, "action": "merge", "text": "병합된 텍스트"}}]""",
    "ru": """Ниже представлены {count} пар потенциально противоречивых записей памяти. Оцените каждую пару и определите, как с ней поступить.

======以下为记忆条目======
{pairs}
======以上为记忆条目======

Для каждой пары определите:
- merge: объедините новое наблюдение со старым воспоминанием в одну запись, предоставьте объединённый text
- keep_new: новое наблюдение полностью заменяет старое воспоминание
- keep_old: старое воспоминание точнее
- keep_both: они не противоречат друг другу, темы просто похожи

Выведите только JSON-массив. Каждый элемент должен содержать index, action и text (необязательно).
[{{"index": 0, "action": "merge", "text": "объединённый текст"}}]""",
    "es": """A continuación hay {count} pares de entradas de memoria potencialmente contradictorias. Evalúa cada par y decide cómo manejarlo.

======以下为记忆条目======
{pairs}
======以上为记忆条目======

Para cada par, decide:
- merge: fusiona la nueva observación con la memoria antigua en una sola entrada; proporciona el text combinado
- keep_new: la nueva observación reemplaza por completo a la memoria antigua
- keep_old: la memoria antigua es más precisa
- keep_both: no se contradicen; los temas solo son parecidos

Devuelve solo un array JSON. Cada elemento debe contener index, action y text (opcional).
[{{"index": 0, "action": "merge", "text": "texto combinado"}}]""",
    "pt": """Abaixo há {count} pares de entradas de memória potencialmente contraditórias. Avalie cada par e decida como lidar com ele.

======以下为记忆条目======
{pairs}
======以上为记忆条目======

Para cada par, decida:
- merge: funda a nova observação com a memória antiga em uma única entrada; forneça o text combinado
- keep_new: a nova observação substitui completamente a memória antiga
- keep_old: a memória antiga é mais precisa
- keep_both: não há contradição; os temas são apenas parecidos

Retorne apenas um array JSON. Cada item deve conter index, action e text (opcional).
[{{"index": 0, "action": "merge", "text": "texto combinado"}}]""",
}


def get_persona_correction_prompt(lang: str = "zh") -> str:
    return _loc(PERSONA_CORRECTION_PROMPT, lang)


persona_correction_prompt = PERSONA_CORRECTION_PROMPT["zh"]


# ---------- fact_dedup_prompt → i18n dict ----------
# Drives memory/fact_dedup.py's resolve loop. Vector cosine selects
# candidate (candidate_text, existing_text) pairs above a similarity
# threshold; this prompt asks the LLM to classify each pair into
# merge / replace / keep_both. The LLM is the arbiter, vector is just
# the candidate generator — cosine alone can't separate "主人喜欢猫"
# from "主人讨厌猫", so we always defer the final call to the model.
FACT_DEDUP_PROMPT = {
    "zh": """以下是 {COUNT} 组通过向量相似度筛选出的候选事实对，请逐组判断是否真的指向同一件事，并选择处理方式。

======以下为候选事实对======
{PAIRS}
======以上为候选事实对======

对于每组，从下列动作中选一个：
- merge: 两条记录的确指向同一事件/偏好/状态，保留 existing，丢弃 candidate（existing 的 importance 会自动+1，candidate id 会被记入 merged_from_ids）
- replace: 同样指向同一件事，但 candidate 措辞更准确/更新，应保留 candidate、丢弃 existing
- keep_both: 看似相似但其实是两件不同的事（如"喜欢"与"讨厌"，或同一对象在不同情境下的不同状态），都保留

注意：
- cosine 高只是相似度高，不代表语义相同，特别要警惕褒贬相反、肯定/否定相反的情况
- 优先选 keep_both 而非误合并；记忆系统对错误合并的容忍度低于对冗余的容忍度

仅输出 JSON 数组，每项包含 index、action：
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    "en": """Below are {COUNT} candidate fact pairs flagged by cosine similarity. For each pair, decide whether they actually refer to the same thing and choose how to handle it.

======以下为候选事实对======
{PAIRS}
======以上为候选事实对======

For each pair, pick one action:
- merge: the two records do refer to the same event/preference/state — keep existing, drop candidate (existing's importance will auto +1; candidate id is recorded in merged_from_ids)
- replace: same underlying thing, but the candidate's wording is more accurate/up-to-date — keep candidate, drop existing
- keep_both: they look similar but are actually distinct ("likes" vs "dislikes", or the same subject in different contexts) — keep both

Notes:
- High cosine means high *surface* similarity, not semantic identity. Be especially careful about polarity flips (positive/negative, like/dislike).
- Prefer keep_both over a wrongful merge — the memory system tolerates redundancy much better than incorrect merges.

Output only a JSON array, each item containing index and action:
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    "ja": """以下は {COUNT} 組のベクトル類似度で抽出された候補ペアです。各ペアについて、本当に同じ事柄を指しているか判断し、処理方法を選んでください。

======以下为候选事实对======
{PAIRS}
======以上为候选事实对======

各ペアについて、以下のいずれかを選択：
- merge: 同じ出来事/嗜好/状態を指している → existing を残し candidate を削除（existing の importance が自動 +1、candidate id は merged_from_ids に記録）
- replace: 同じ事柄だが candidate の方が正確/最新 → candidate を残し existing を削除
- keep_both: 似ているが実際には別の事柄（"好き"と"嫌い"のような極性反転、あるいは異なる文脈での同じ対象）→ 両方残す

注意：
- 高い cosine は表層的な類似度であり、意味的同一性ではない。特に極性反転（肯定/否定、好き/嫌い）に注意
- 誤合併よりも keep_both を優先。記憶システムは冗長性より誤合併に対する耐性が低い

JSON 配列のみを出力し、各項目に index と action を含めてください：
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    "ko": """아래는 벡터 유사도로 선별된 {COUNT}쌍의 후보 사실 쌍입니다. 각 쌍에 대해 실제로 같은 것을 가리키는지 판단하고 처리 방법을 선택하세요.

======以下为候选事实对======
{PAIRS}
======以上为候选事实对======

각 쌍에 대해 다음 중 하나를 선택:
- merge: 두 기록이 실제로 같은 사건/선호/상태를 가리킴 — existing 유지, candidate 제거 (existing의 importance가 자동 +1, candidate id는 merged_from_ids에 기록됨)
- replace: 같은 것을 가리키지만 candidate의 표현이 더 정확/최신 — candidate 유지, existing 제거
- keep_both: 비슷해 보이지만 실제로는 다른 것 ("좋아함"과 "싫어함" 같은 극성 반전, 혹은 다른 맥락의 같은 대상) — 둘 다 유지

주의:
- 높은 cosine은 표면적 유사도일 뿐 의미적 동일성을 보장하지 않음. 특히 극성 반전(긍정/부정, 좋아함/싫어함)에 주의
- 잘못된 병합보다 keep_both를 우선. 기억 시스템은 중복보다 잘못된 병합에 대한 내성이 더 낮음

JSON 배열만 출력하고 각 항목에 index와 action을 포함하세요:
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    "ru": """Ниже представлены {COUNT} пар фактов-кандидатов, отобранных по косинусной близости. Для каждой пары определите, действительно ли они описывают одно и то же, и выберите способ обработки.

======以下为候选事实对======
{PAIRS}
======以上为候选事实对======

Для каждой пары выберите одно из действий:
- merge: записи описывают одно и то же событие/предпочтение/состояние — сохранить existing, отбросить candidate (importance у existing увеличится на 1, id candidate запишется в merged_from_ids)
- replace: то же самое, но формулировка candidate точнее/актуальнее — сохранить candidate, отбросить existing
- keep_both: похожи внешне, но на самом деле разные ("любит" vs "не любит", тот же объект в разных контекстах) — сохранить обе

Замечания:
- Высокий cosine означает поверхностное сходство, а не семантическую идентичность. Особенно осторожно с инверсией полярности (положительное/отрицательное, любит/не любит).
- Предпочитайте keep_both ошибочному слиянию — система памяти переносит избыточность лучше, чем неверные слияния.

Выводите только JSON-массив, каждый элемент содержит index и action:
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    "es": """A continuación hay {COUNT} pares de hechos candidatos seleccionados por similitud vectorial. Para cada par, decide si realmente apuntan a lo mismo y elige cómo manejarlo.

======以下为候选事实对======
{PAIRS}
======以上为候选事实对======

Para cada par, elige una acción:
- merge: los dos registros sí apuntan al mismo evento/preferencia/estado; conserva existing y descarta candidate (importance de existing subirá +1 automáticamente; el id de candidate se registrará en merged_from_ids)
- replace: apuntan a lo mismo, pero candidate está mejor redactado o más actualizado; conserva candidate y descarta existing
- keep_both: parecen similares pero son cosas distintas (por ejemplo "le gusta" vs "no le gusta", o el mismo sujeto en contextos diferentes); conserva ambos

Notas:
- Un cosine alto solo indica similitud superficial, no identidad semántica. Ten especial cuidado con inversión de polaridad (positivo/negativo, gusta/no gusta).
- Prefiere keep_both antes que una fusión errónea; el sistema de memoria tolera mejor la redundancia que las fusiones incorrectas.

Devuelve solo un array JSON; cada elemento contiene index y action:
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    "pt": """Abaixo há {COUNT} pares de fatos candidatos selecionados por similaridade vetorial. Para cada par, decida se eles realmente apontam para a mesma coisa e escolha como lidar com isso.

======以下为候选事实对======
{PAIRS}
======以上为候选事实对======

Para cada par, escolha uma ação:
- merge: os dois registros realmente apontam para o mesmo evento/preferência/estado; mantenha existing e descarte candidate (a importance de existing subirá +1 automaticamente; o id de candidate será registrado em merged_from_ids)
- replace: apontam para a mesma coisa, mas candidate está mais preciso ou atualizado; mantenha candidate e descarte existing
- keep_both: parecem semelhantes, mas são coisas distintas (por exemplo "gosta" vs "não gosta", ou o mesmo assunto em contextos diferentes); mantenha ambos

Notas:
- Um cosine alto indica apenas similaridade superficial, não identidade semântica. Tenha cuidado especial com inversão de polaridade (positivo/negativo, gosta/não gosta).
- Prefira keep_both a uma fusão incorreta; o sistema de memória tolera melhor redundância do que fusões erradas.

Retorne apenas um array JSON; cada item contém index e action:
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
}


def get_fact_dedup_prompt(lang: str = "zh") -> str:
    return _loc(FACT_DEDUP_PROMPT, lang)


fact_dedup_prompt = FACT_DEDUP_PROMPT["zh"]


# ---------- memory_recall_rerank_prompt → i18n dict ----------
# Drives memory/recall.py's _fine_rank step. Cosine pre-filtering
# narrows the candidate set down to ~3× the budget; this prompt asks
# the LLM to pick the top {BUDGET} most-relevant items for the query.
# evidence_score appears parenthetically as auxiliary signal — the
# LLM weighs it together with semantic relevance instead of mixing
# into a single ranking number (cosine vs evidence are
# dimensionally inconsistent).
MEMORY_RECALL_RERANK_PROMPT = {
    "zh": """以下是用户最近提到的话题。请从候选记忆中挑选最相关的 {BUDGET} 条用于注入对话上下文。

======以下为用户当前话题======
{QUERY}
======以上为用户当前话题======

======以下为候选记忆======
{CANDIDATES}
======以上为候选记忆======

每条候选前的 score 是用户对该记忆的累计确认度（高 = 反复确认，低 = 较少证据）。可作为辅助信号——同等相关度时优先选 score 高的；但不要让 score 完全压倒相关性，无关的高 score 记忆不该入选。

仅输出 JSON 数组，按重要程度从高到低排列，每项包含 id 字段：
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

最多 {BUDGET} 条；若候选不足 {BUDGET} 条相关，可返回更少。""",
    "en": """Below are topics the user has just mentioned. From the candidate memories, pick the {BUDGET} most relevant ones to inject into the conversation context.

======以下为用户当前话题======
{QUERY}
======以上为用户当前话题======

======以下为候选记忆======
{CANDIDATES}
======以上为候选记忆======

The `score` annotation on each candidate is the user's cumulative confirmation count for that memory (high = repeatedly confirmed, low = thin evidence). Use it as an auxiliary signal — when relevance is tied, prefer the higher score; but do not let score override relevance, an irrelevant high-score memory should not be picked.

Output only a JSON array, ordered most-important first. Each item must contain an `id` field:
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

At most {BUDGET} items; return fewer if not enough candidates are relevant.""",
    "ja": """以下はユーザーが最近言及したトピックです。候補メモリから、対話コンテキストに注入する最も関連性の高い {BUDGET} 件を選んでください。

======以下为用户当前话题======
{QUERY}
======以上为用户当前话题======

======以下为候选记忆======
{CANDIDATES}
======以上为候选记忆======

各候補の score 注釈は、ユーザーがそのメモリを累積確認した回数です（高 = 繰り返し確認、低 = 証拠が薄い）。補助シグナルとして利用してください。関連性が同等なら score の高い方を優先しますが、関連性を score が完全に覆すべきではありません。

JSON 配列のみを出力し、重要度順に並べてください。各項目に `id` フィールドを含めます：
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

最大 {BUDGET} 件。関連する候補がそれ以下なら、より少なく返しても構いません。""",
    "ko": """아래는 사용자가 최근 언급한 주제입니다. 후보 메모리 중에서 대화 컨텍스트에 주입할 가장 관련성 높은 {BUDGET}개를 선택하세요.

======以下为用户当前话题======
{QUERY}
======以上为用户当前话题======

======以下为候选记忆======
{CANDIDATES}
======以上为候选记忆======

각 후보의 score는 사용자가 해당 메모리를 누적적으로 확인한 횟수입니다(높음 = 반복 확인, 낮음 = 증거 부족). 보조 신호로 활용하세요. 관련성이 같으면 score 높은 쪽을 우선하지만, 관련성을 score가 완전히 압도해서는 안 됩니다.

JSON 배열만 출력하고 중요도 순으로 정렬하세요. 각 항목에 `id` 필드를 포함:
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

최대 {BUDGET}개; 관련 후보가 부족하면 더 적게 반환해도 됩니다.""",
    "ru": """Ниже представлены темы, которые пользователь только что упомянул. Из кандидатов памяти выберите {BUDGET} наиболее релевантных для внедрения в контекст диалога.

======以下为用户当前话题======
{QUERY}
======以上为用户当前话题======

======以下为候选记忆======
{CANDIDATES}
======以上为候选记忆======

Аннотация `score` рядом с каждым кандидатом — это накопленное число подтверждений пользователем (высокое = повторяющееся подтверждение, низкое = слабые доказательства). Используйте как вспомогательный сигнал: при равной релевантности предпочтите более высокий score, но не позволяйте score полностью перевесить релевантность.

Выводите только JSON-массив, упорядоченный по важности. Каждый элемент содержит поле `id`:
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

Не более {BUDGET} элементов; верните меньше, если релевантных кандидатов меньше.""",
    "es": """A continuación están los temas que el usuario acaba de mencionar. De las memorias candidatas, elige las {BUDGET} más relevantes para inyectarlas en el contexto de conversación.

======以下为用户当前话题======
{QUERY}
======以上为用户当前话题======

======以下为候选记忆======
{CANDIDATES}
======以上为候选记忆======

La anotación `score` de cada candidata es el recuento acumulado de confirmaciones del usuario (alto = confirmado repetidamente, bajo = poca evidencia). Úsalo como señal auxiliar: si la relevancia empata, prefiere score más alto; pero no permitas que score anule la relevancia.

Devuelve solo un array JSON, ordenado de mayor a menor importancia. Cada elemento debe contener un campo `id`:
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

Como máximo {BUDGET} elementos; devuelve menos si no hay suficientes candidatas relevantes.""",
    "pt": """Abaixo estão os tópicos que o usuário acabou de mencionar. Das memórias candidatas, escolha as {BUDGET} mais relevantes para injetar no contexto da conversa.

======以下为用户当前话题======
{QUERY}
======以上为用户当前话题======

======以下为候选记忆======
{CANDIDATES}
======以上为候选记忆======

A anotação `score` de cada candidata é a contagem acumulada de confirmações do usuário (alta = confirmada repetidamente, baixa = pouca evidência). Use como sinal auxiliar: se a relevância empatar, prefira score maior; mas não deixe score anular a relevância.

Retorne apenas um array JSON, ordenado da maior para a menor importância. Cada item deve conter um campo `id`:
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

No máximo {BUDGET} itens; retorne menos se não houver candidatas relevantes suficientes.""",
}


def get_memory_recall_rerank_prompt(lang: str = "zh") -> str:
    return _loc(MEMORY_RECALL_RERANK_PROMPT, lang)


memory_recall_rerank_prompt = MEMORY_RECALL_RERANK_PROMPT["zh"]


# =====================================================================
# ======= Recall-memory tool (function/tool call) =====================
# =====================================================================
# 给所有文本/语音模型注册的"回忆"工具：模型决定何时调用，
# 当前先做成 pseudo tool —— 无论传什么参数都返回"没有找到相关记忆"，
# 等机制层在 offline / realtime 两条路径上都跑通了再接真实检索后端。
# description / 参数说明走 _loc 按 user_language 渲染（短码：
# zh/en/ja/ko/ru/es/pt）。

RECALL_MEMORY_TOOL_DESCRIPTION = {
    "zh": "回忆与当前对话相关的过往记忆。当你需要查阅之前的对话内容、用户偏好、过去发生的事情，或对当前话题缺少必要背景时调用此工具。",
    "en": "Recall past memories relevant to the current conversation. Call this when you need earlier dialogue content, user preferences, things that happened before, or background context you currently lack.",
    "ja": "現在の会話に関連する過去の記憶を呼び出します。以前の会話内容、ユーザーの好み、過去の出来事、または現在の話題に必要な背景が不足している時にこのツールを呼び出してください。",
    "ko": "현재 대화와 관련된 과거 기억을 떠올립니다. 이전 대화 내용, 사용자 선호, 과거 있었던 일, 또는 현재 주제에 필요한 배경 정보가 부족할 때 이 도구를 호출하세요.",
    "ru": "Вспомнить прошлые воспоминания, связанные с текущим разговором. Вызывайте, когда нужны прежние реплики, предпочтения пользователя, прошлые события или фоновый контекст, которого вам сейчас не хватает.",
    "es": "Recordar memorias pasadas relevantes para la conversación actual. Llama a esta herramienta cuando necesites contenido previo, preferencias del usuario, cosas que pasaron antes o contexto que te falte.",
    "pt": "Recordar memórias passadas relevantes para a conversa atual. Chame esta ferramenta quando precisar de conteúdo anterior, preferências do usuário, coisas que aconteceram antes ou contexto que esteja faltando.",
}

RECALL_MEMORY_TOOL_QUERY_DESCRIPTION = {
    "zh": "要回忆的关键词、问题或话题。用一两句话简洁概括，例如\"上次提到的旅行计划\"或\"用户对咖啡的喜好\"。query 和 time 至少提供一个：只想按时间回溯时可只填 time、留空 query。",
    "en": "Keyword, question, or topic to recall. Keep it to a sentence or two, e.g. \"the travel plan mentioned earlier\" or \"the user's coffee preferences\". Provide at least one of query / time: for a pure time lookup you may fill only time and leave query empty.",
    "ja": "思い出したいキーワード、質問、話題。一、二文で簡潔にまとめてください。例：「以前話した旅行計画」「ユーザーのコーヒーの好み」。query と time は少なくとも一方を指定：時間でさかのぼるだけなら time のみ指定し query は空でも可。",
    "ko": "떠올리려는 키워드, 질문, 주제. 한두 문장으로 간결하게 적으세요. 예: \"이전에 언급한 여행 계획\", \"사용자의 커피 취향\". query와 time 중 최소 하나는 지정: 시간으로만 거슬러보려면 time만 채우고 query는 비워도 됩니다.",
    "ru": "Ключевое слово, вопрос или тема для воспоминания. Сформулируйте в одно-два предложения, например «упомянутый ранее план поездки» или «предпочтения пользователя в кофе». Укажите хотя бы одно из query / time: для поиска только по времени можно заполнить только time и оставить query пустым.",
    "es": "Palabra clave, pregunta o tema a recordar. Una o dos frases breves, p. ej. \"el plan de viaje mencionado antes\" o \"las preferencias de café del usuario\". Indica al menos uno de query / time: para una búsqueda solo por tiempo puedes rellenar solo time y dejar query vacío.",
    "pt": "Palavra-chave, pergunta ou tópico a recordar. Uma ou duas frases curtas, p. ex. \"o plano de viagem mencionado antes\" ou \"as preferências de café do usuário\". Forneça ao menos um entre query / time: para uma busca só por tempo, preencha apenas time e deixe query vazio.",
}

RECALL_MEMORY_TOOL_TIME_DESCRIPTION = {
    "zh": "可选。把检索限定在某个时间段。只填 time（不填 query）就返回离那段时间最近的若干条记忆（事实和印象都包含，适合\"那天/那周发生了什么\"）；同时填 query 则做\"语义+时间\"联合检索——在该时间段内按 query 语义找相关记忆（适合\"五月聊过的旅行计划\"）。支持整点小时 2026-05-01T14、单日 2026-05-01、整月 2026-05、整年 2026，或区间 2026-05-01/2026-05-07、2026-05-01T09/2026-05-01T18。不填则按 query 做全量语义检索。",
    "en": "Optional. Restrict recall to a time period. With time only (no query) it returns the memories closest to that period (both facts and impressions — good for \"what happened that day/week\"). With both query and time it runs a combined \"semantic + time\" search — finds memories relevant to query within that period (good for \"the travel plan discussed in May\"). Accepts an hour 2026-05-01T14, a day 2026-05-01, a month 2026-05, a year 2026, or a range 2026-05-01/2026-05-07, 2026-05-01T09/2026-05-01T18. Leave empty for full semantic recall by query.",
    "ja": "任意。検索をある期間に限定します。time だけ（query なし）なら、その期間に最も近い記憶（事実も印象も含む。「その日/その週に何があったか」向け）を返します。query と time の両方を指定すると「意味＋時間」の複合検索になり、その期間内で query に関連する記憶を探します（「5月に話した旅行計画」向け）。整点時 2026-05-01T14、単日 2026-05-01、月 2026-05、年 2026、または期間 2026-05-01/2026-05-07・2026-05-01T09/2026-05-01T18 に対応。空欄なら query による全件の意味検索になります。",
    "ko": "선택. 검색을 특정 기간으로 제한합니다. time만 주면(query 없이) 그 기간에 가장 가까운 기억(사실과 인상 모두 포함, \"그날/그 주에 무슨 일이 있었나\"에 적합)을 반환합니다. query와 time을 함께 주면 \"의미+시간\" 결합 검색으로, 그 기간 안에서 query에 관련된 기억을 찾습니다(\"5월에 얘기한 여행 계획\"에 적합). 정시 단위 2026-05-01T14, 단일 날짜 2026-05-01, 월 2026-05, 연 2026, 또는 기간 2026-05-01/2026-05-07·2026-05-01T09/2026-05-01T18 지원. 비워두면 query 기반 전체 의미 검색.",
    "ru": "Необязательно. Ограничивает поиск периодом времени. Если задан только time (без query), возвращает воспоминания, ближайшие к этому периоду (и факты, и впечатления — удобно для «что было в тот день/неделю»). Если заданы и query, и time, выполняется совмещённый поиск «семантика + время» — ищет воспоминания, релевантные query, в пределах периода (удобно для «план поездки, обсуждавшийся в мае»). Принимает час 2026-05-01T14, день 2026-05-01, месяц 2026-05, год 2026 или диапазон 2026-05-01/2026-05-07, 2026-05-01T09/2026-05-01T18. Оставьте пустым для полного семантического поиска по query.",
    "es": "Opcional. Limita la búsqueda a un periodo. Con solo time (sin query) devuelve los recuerdos más cercanos a ese periodo (hechos e impresiones — útil para \"qué pasó ese día/semana\"). Con query y time juntos hace una búsqueda combinada \"semántica + tiempo\": encuentra recuerdos relevantes a query dentro de ese periodo (útil para \"el plan de viaje hablado en mayo\"). Acepta una hora 2026-05-01T14, un día 2026-05-01, un mes 2026-05, un año 2026 o un rango 2026-05-01/2026-05-07, 2026-05-01T09/2026-05-01T18. Déjalo vacío para la búsqueda semántica completa por query.",
    "pt": "Opcional. Restringe a busca a um período. Com apenas time (sem query) retorna as memórias mais próximas daquele período (fatos e impressões — útil para \"o que aconteceu naquele dia/semana\"). Com query e time juntos faz uma busca combinada \"semântica + tempo\": encontra memórias relevantes para query dentro do período (útil para \"o plano de viagem conversado em maio\"). Aceita uma hora 2026-05-01T14, um dia 2026-05-01, um mês 2026-05, um ano 2026 ou um intervalo 2026-05-01/2026-05-07, 2026-05-01T09/2026-05-01T18. Deixe vazio para a busca semântica completa por query.",
}

RECALL_MEMORY_TOOL_NO_RESULT = {
    "zh": "没有找到相关记忆。",
    "en": "No relevant memory found.",
    "ja": "関連する記憶が見つかりませんでした。",
    "ko": "관련된 기억을 찾지 못했습니다.",
    "ru": "Соответствующих воспоминаний не найдено.",
    "es": "No se encontró ninguna memoria relevante.",
    "pt": "Nenhuma memória relevante encontrada.",
}

# 同时给了 query 和 time 却 0 命中时返回这条——提示模型放宽过滤条件，
# 用「只带时间」或「只带 query」再查一次，而不是直接当作没有记忆放弃。
RECALL_MEMORY_TOOL_NO_RESULT_LOOSEN = {
    "zh": "在该时间范围内没有找到匹配「{query}」的记忆。建议放宽过滤条件重试一次：要么只用 time（按时间回溯该时段的记忆），要么只用 query（不限时间地语义检索）。",
    "en": "No memory matched \"{query}\" within that time range. Try loosening the filter and querying once more: either with time only (recall memories from that period) or with query only (semantic search without a time limit).",
    "ja": "その時間範囲で「{query}」に一致する記憶は見つかりませんでした。フィルタを緩めてもう一度試してください：time だけ（その期間の記憶を回想）か、query だけ（時間制限なしの意味検索）のどちらかで。",
    "ko": "해당 시간 범위에서 \"{query}\"에 일치하는 기억을 찾지 못했습니다. 필터를 완화해 다시 시도해 보세요: time만 사용(해당 기간의 기억 회상)하거나 query만 사용(시간 제한 없는 의미 검색)하세요.",
    "ru": "В этом диапазоне времени не нашлось воспоминаний по запросу «{query}». Попробуйте ослабить фильтр и запросить ещё раз: либо только time (вспомнить воспоминания за тот период), либо только query (семантический поиск без ограничения по времени).",
    "es": "No se encontró ninguna memoria que coincidiera con \"{query}\" en ese rango de tiempo. Prueba a aflojar el filtro y consultar de nuevo: con solo time (recordar memorias de ese período) o con solo query (búsqueda semántica sin límite de tiempo).",
    "pt": "Nenhuma memória correspondeu a \"{query}\" nesse intervalo de tempo. Tente afrouxar o filtro e consultar novamente: apenas com time (recordar memórias daquele período) ou apenas com query (busca semântica sem limite de tempo).",
}

# 召回到 N 条记忆时的总览首句；后面接渲染条目，每条按
# ``[tier/entity] text  (事件日期, 相对标签)`` 格式（tier/entity 是英文
# enum，不翻译；text 是原始记忆内容，按用户拍板"不翻译"；时间锚点优先
# 取事件真正发生时间而非记忆写盘时间）。
RECALL_MEMORY_TOOL_FOUND_HEADER = {
    "zh": "找到 {n} 条相关记忆：",
    "en": "Found {n} relevant memories:",
    "ja": "関連する記憶を {n} 件見つけました：",
    "ko": "관련된 기억 {n} 건을 찾았습니다:",
    "ru": "Найдено {n} релевантных воспоминаний:",
    "es": "Se encontraron {n} memorias relevantes:",
    "pt": "Foram encontradas {n} memórias relevantes:",
}


# =====================================================================
# ======= MemoryRefineEngine cluster prompt (Phase A-3) ===============
# =====================================================================
# 跨 persona / reflection 共享的四件套（split / merge / modify / discard）
# refine prompt。cluster 内成员同 entity（engine 层强制），可能混
# reflection 和 fact 两类条目（fact 是原子素材，只能作 merge / modify 的
# 信息源 absorbed_from_fact_ids，不能被 split / discard / modify —— 代码
# 层兜底，不靠 prompt 自觉）。LLM 输出 JSON 数组；无需修改时返回 []。
# 渲染走 .replace('{ENTITY}', ...) / .replace('{CLUSTER}', ...)，所以
# JSON example 内的 `{...}` 字面量不需要 `{{}}` escape。

MEMORY_REFINE_PROMPT = {
    "zh": """以下是一组高度相关的记忆条目（cluster），entity={ENTITY}。请判断这组条目应如何整理。

======以下为记忆群组======
{CLUSTER}
======以上为记忆群组======

可选操作（四件套）：
- split: 一条 reflection / persona 实际混了多个独立观察，应拆成多条
- merge: 多条高度重复或可融合，合并成一条新文本
- modify: 单条改写，基于 cluster 内其他条目或 fact 信息融合
- discard: 该条已被新数据完全证伪 / 是噪音 / 长期无价值
- 无需修改时返回空数组 []

约束：
- fact 是原子素材，只能作为 merge / modify 的信息源（写入 absorbed_from_fact_ids），不能被 split / discard / modify
- merge / split 后产出新条目继承原 entity；reflection 还需提供 relation_type 和 temporal_scope，persona 不需要
- modify / discard 必须给 reason（用于审计 history）
- 同一 source_id 不能同时出现在多个 action 里

JSON 输出格式：
[
  {"action": "split", "source_id": "ref_xxx",
   "produce": [{"text": "拆出的内容 A", "relation_type": "preference", "temporal_scope": "pattern"},
               {"text": "拆出的内容 B", "relation_type": "habit", "temporal_scope": "state"}]},
  {"action": "merge", "source_ids": ["ref_aaa", "ref_bbb"],
   "absorbed_from_fact_ids": ["fact_ccc"],
   "produce": {"text": "融合后的新文本", "relation_type": "preference", "temporal_scope": "pattern"}},
  {"action": "modify", "source_id": "ref_xxx",
   "absorbed_from_fact_ids": ["fact_yyy"],
   "produce": {"text": "改写后的新文本"},
   "reason": "结合 fact_yyy 后表述更准确"},
  {"action": "discard", "source_id": "ref_zzz",
   "reason": "已被 ref_xxx 完全包含且更准确"}
]""",

    "en": """Below is a cluster of highly related memory entries, entity={ENTITY}. Determine how to refine this cluster.

======以下为记忆群组======
{CLUSTER}
======以上为记忆群组======

Available actions (four total):
- split: a reflection / persona entry actually mixes multiple independent observations — split into separate entries
- merge: multiple entries are highly redundant or fusible — merge into a single new text
- modify: rewrite a single entry by fusing information from other cluster members or facts
- discard: this entry has been refuted by newer data / is noise / has no lasting value
- return an empty array [] when no modification is needed

Constraints:
- fact entries are atomic source material — they can only be referenced as info sources (absorbed_from_fact_ids) in merge / modify, and CANNOT be split / discarded / modified
- produced entries from merge / split inherit the original entity; reflections also require relation_type and temporal_scope, persona entries do not
- modify / discard MUST include a reason field (used for audit history)
- the same source_id cannot appear in multiple actions

JSON output format:
[
  {"action": "split", "source_id": "ref_xxx",
   "produce": [{"text": "split content A", "relation_type": "preference", "temporal_scope": "pattern"},
               {"text": "split content B", "relation_type": "habit", "temporal_scope": "state"}]},
  {"action": "merge", "source_ids": ["ref_aaa", "ref_bbb"],
   "absorbed_from_fact_ids": ["fact_ccc"],
   "produce": {"text": "merged new text", "relation_type": "preference", "temporal_scope": "pattern"}},
  {"action": "modify", "source_id": "ref_xxx",
   "absorbed_from_fact_ids": ["fact_yyy"],
   "produce": {"text": "rewritten new text"},
   "reason": "more accurate after incorporating fact_yyy"},
  {"action": "discard", "source_id": "ref_zzz",
   "reason": "fully covered by ref_xxx and more accurate there"}
]""",

    "ja": """以下は高度に関連する記憶エントリのグループ（cluster）です。entity={ENTITY}。このグループをどう整理すべきか判断してください。

======以下为记忆群组======
{CLUSTER}
======以上为记忆群组======

選択可能なアクション（4 種類）：
- split: 1 つの reflection / persona が複数の独立した観察を混在させている → 複数に分割
- merge: 複数のエントリが高度に重複または融合可能 → 1 つの新しいテキストに統合
- modify: cluster 内の他のエントリや fact からの情報融合に基づき、1 つのエントリを書き換え
- discard: 新しいデータで完全に否定された / ノイズ / 長期的に価値がない
- 修正が不要な場合は空配列 [] を返す

制約：
- fact は原子的な素材であり、merge / modify の情報源（absorbed_from_fact_ids に記録）としてのみ使用可能。split / discard / modify はできない
- merge / split で生成された新エントリは元の entity を継承。reflection は relation_type と temporal_scope も指定が必要、persona は不要
- modify / discard は必ず reason フィールドを含む（履歴監査用）
- 同じ source_id は複数のアクションに同時に出現してはならない

JSON 出力フォーマット：
[
  {"action": "split", "source_id": "ref_xxx",
   "produce": [{"text": "分割内容 A", "relation_type": "preference", "temporal_scope": "pattern"},
               {"text": "分割内容 B", "relation_type": "habit", "temporal_scope": "state"}]},
  {"action": "merge", "source_ids": ["ref_aaa", "ref_bbb"],
   "absorbed_from_fact_ids": ["fact_ccc"],
   "produce": {"text": "統合された新しいテキスト", "relation_type": "preference", "temporal_scope": "pattern"}},
  {"action": "modify", "source_id": "ref_xxx",
   "absorbed_from_fact_ids": ["fact_yyy"],
   "produce": {"text": "書き換えられた新しいテキスト"},
   "reason": "fact_yyy を組み込んだ後、より正確に"},
  {"action": "discard", "source_id": "ref_zzz",
   "reason": "ref_xxx に完全に包含され、そちらの方が正確"}
]""",

    "ko": """다음은 높은 관련성을 가진 기억 항목 그룹(cluster)입니다. entity={ENTITY}. 이 그룹을 어떻게 정리할지 판단하세요.

======以下为记忆群组======
{CLUSTER}
======以上为记忆群组======

가능한 액션 (4가지):
- split: 하나의 reflection / persona가 여러 독립적인 관찰을 섞고 있음 → 여러 개로 분할
- merge: 여러 항목이 매우 중복되거나 융합 가능 → 하나의 새 텍스트로 통합
- modify: cluster 내 다른 항목이나 fact의 정보를 융합하여 단일 항목을 재작성
- discard: 새 데이터로 완전히 반증됨 / 노이즈 / 장기적으로 가치 없음
- 수정이 필요 없으면 빈 배열 [] 반환

제약:
- fact는 원자적 소재로, merge / modify의 정보 소스(absorbed_from_fact_ids에 기록)로만 사용 가능. split / discard / modify 불가
- merge / split으로 생성된 새 항목은 원본 entity를 상속. reflection은 relation_type과 temporal_scope도 필요, persona는 불필요
- modify / discard는 반드시 reason 필드를 포함 (감사 이력용)
- 동일한 source_id는 여러 액션에 동시에 나타날 수 없음

JSON 출력 포맷:
[
  {"action": "split", "source_id": "ref_xxx",
   "produce": [{"text": "분할 내용 A", "relation_type": "preference", "temporal_scope": "pattern"},
               {"text": "분할 내용 B", "relation_type": "habit", "temporal_scope": "state"}]},
  {"action": "merge", "source_ids": ["ref_aaa", "ref_bbb"],
   "absorbed_from_fact_ids": ["fact_ccc"],
   "produce": {"text": "통합된 새 텍스트", "relation_type": "preference", "temporal_scope": "pattern"}},
  {"action": "modify", "source_id": "ref_xxx",
   "absorbed_from_fact_ids": ["fact_yyy"],
   "produce": {"text": "재작성된 새 텍스트"},
   "reason": "fact_yyy를 결합한 후 더 정확함"},
  {"action": "discard", "source_id": "ref_zzz",
   "reason": "ref_xxx에 완전히 포함되어 있으며 그쪽이 더 정확함"}
]""",

    "ru": """Ниже представлена группа (cluster) тесно связанных записей памяти, entity={ENTITY}. Определите, как следует упорядочить эту группу.

======以下为记忆群组======
{CLUSTER}
======以上为记忆群组======

Доступные действия (всего четыре):
- split: одна запись reflection / persona фактически смешивает несколько независимых наблюдений — разделить на отдельные записи
- merge: несколько записей сильно избыточны или объединяемы — слить в одну новую запись
- modify: переписать одну запись, объединив информацию из других членов cluster или фактов
- discard: запись опровергнута новыми данными / является шумом / не имеет долгосрочной ценности
- если изменения не нужны, верните пустой массив []

Ограничения:
- записи fact являются атомарным исходным материалом — их можно использовать только как источники информации (absorbed_from_fact_ids) в merge / modify, и НЕЛЬЗЯ split / discard / modify
- записи, созданные в результате merge / split, наследуют исходный entity; reflection также требуют relation_type и temporal_scope, persona — нет
- modify / discard ДОЛЖНЫ содержать поле reason (используется для аудита истории)
- один source_id не может появляться в нескольких действиях одновременно

Формат вывода JSON:
[
  {"action": "split", "source_id": "ref_xxx",
   "produce": [{"text": "разделённое содержимое A", "relation_type": "preference", "temporal_scope": "pattern"},
               {"text": "разделённое содержимое B", "relation_type": "habit", "temporal_scope": "state"}]},
  {"action": "merge", "source_ids": ["ref_aaa", "ref_bbb"],
   "absorbed_from_fact_ids": ["fact_ccc"],
   "produce": {"text": "объединённый новый текст", "relation_type": "preference", "temporal_scope": "pattern"}},
  {"action": "modify", "source_id": "ref_xxx",
   "absorbed_from_fact_ids": ["fact_yyy"],
   "produce": {"text": "переписанный новый текст"},
   "reason": "после учёта fact_yyy точнее"},
  {"action": "discard", "source_id": "ref_zzz",
   "reason": "полностью покрывается ref_xxx, там точнее"}
]""",

    "es": """A continuación hay un grupo (cluster) de entradas de memoria altamente relacionadas, entity={ENTITY}. Decide cómo refinar este grupo.

======以下为记忆群组======
{CLUSTER}
======以上为记忆群组======

Acciones disponibles (cuatro en total):
- split: una entrada de reflection / persona en realidad mezcla varias observaciones independientes — divide en entradas separadas
- merge: varias entradas son altamente redundantes o fusibles — fusiona en un nuevo texto único
- modify: reescribe una entrada fusionando información de otros miembros del cluster o facts
- discard: la entrada ha sido refutada por datos más nuevos / es ruido / sin valor duradero
- devuelve un array vacío [] cuando no se requiere modificación

Restricciones:
- las entradas fact son material atómico — solo pueden referenciarse como fuentes de información (absorbed_from_fact_ids) en merge / modify, y NO pueden ser split / discarded / modified
- las entradas producidas por merge / split heredan el entity original; las reflections además requieren relation_type y temporal_scope, las persona no
- modify / discard DEBEN incluir un campo reason (usado para el historial de auditoría)
- el mismo source_id no puede aparecer en múltiples acciones

Formato de salida JSON:
[
  {"action": "split", "source_id": "ref_xxx",
   "produce": [{"text": "contenido dividido A", "relation_type": "preference", "temporal_scope": "pattern"},
               {"text": "contenido dividido B", "relation_type": "habit", "temporal_scope": "state"}]},
  {"action": "merge", "source_ids": ["ref_aaa", "ref_bbb"],
   "absorbed_from_fact_ids": ["fact_ccc"],
   "produce": {"text": "nuevo texto fusionado", "relation_type": "preference", "temporal_scope": "pattern"}},
  {"action": "modify", "source_id": "ref_xxx",
   "absorbed_from_fact_ids": ["fact_yyy"],
   "produce": {"text": "nuevo texto reescrito"},
   "reason": "más preciso tras incorporar fact_yyy"},
  {"action": "discard", "source_id": "ref_zzz",
   "reason": "totalmente cubierto por ref_xxx y allí más preciso"}
]""",

    "pt": """Abaixo está um grupo (cluster) de entradas de memória altamente relacionadas, entity={ENTITY}. Decida como refinar este grupo.

======以下为记忆群组======
{CLUSTER}
======以上为记忆群组======

Ações disponíveis (quatro no total):
- split: uma entrada reflection / persona na verdade mistura várias observações independentes — divida em entradas separadas
- merge: várias entradas são altamente redundantes ou fundíveis — funda em um único novo texto
- modify: reescreva uma entrada fundindo informações de outros membros do cluster ou facts
- discard: a entrada foi refutada por dados mais novos / é ruído / sem valor duradouro
- retorne um array vazio [] quando não for necessária modificação

Restrições:
- entradas fact são material atômico — só podem ser referenciadas como fontes de informação (absorbed_from_fact_ids) em merge / modify, e NÃO podem ser split / discarded / modified
- entradas produzidas por merge / split herdam o entity original; reflections também requerem relation_type e temporal_scope, persona não
- modify / discard DEVEM incluir um campo reason (usado para histórico de auditoria)
- o mesmo source_id não pode aparecer em múltiplas ações

Formato de saída JSON:
[
  {"action": "split", "source_id": "ref_xxx",
   "produce": [{"text": "conteúdo dividido A", "relation_type": "preference", "temporal_scope": "pattern"},
               {"text": "conteúdo dividido B", "relation_type": "habit", "temporal_scope": "state"}]},
  {"action": "merge", "source_ids": ["ref_aaa", "ref_bbb"],
   "absorbed_from_fact_ids": ["fact_ccc"],
   "produce": {"text": "novo texto fundido", "relation_type": "preference", "temporal_scope": "pattern"}},
  {"action": "modify", "source_id": "ref_xxx",
   "absorbed_from_fact_ids": ["fact_yyy"],
   "produce": {"text": "novo texto reescrito"},
   "reason": "mais preciso após incorporar fact_yyy"},
  {"action": "discard", "source_id": "ref_zzz",
   "reason": "totalmente coberto por ref_xxx e mais preciso lá"}
]""",
}


def get_memory_refine_prompt(lang: str = "zh") -> str:
    return _loc(MEMORY_REFINE_PROMPT, lang)


memory_refine_prompt = MEMORY_REFINE_PROMPT["zh"]
