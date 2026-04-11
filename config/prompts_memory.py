"""
Memory-related prompt templates.

Includes: conversation summarization, history review, settings extraction,
emotion analysis, fact extraction, reflection, persona correction,
inner-thoughts injection fragments, and chat-gap notices.
"""
from __future__ import annotations

from config.prompts_sys import _loc

# =====================================================================
# ======= Conversation summarization =================================
# =====================================================================

# ---------- recent_history_manager_prompt ----------
# i18n dict: RECENT_HISTORY_MANAGER_PROMPT

RECENT_HISTORY_MANAGER_PROMPT = {
    'zh': """请总结以下对话内容，生成简洁但信息丰富的摘要：

======以下为对话======
%s
======以上为对话======

你的摘要应该保留关键信息、重要事实和主要讨论点，且不能具有误导性或产生歧义。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

请以key为"对话摘要"、value为字符串的json字典格式返回。""",

    'en': """Please summarize the following conversation to produce a concise yet informative summary:

======以下为对话======
%s
======以上为对话======

Your summary should preserve key information, important facts, and main discussion points without being misleading or ambiguous.

[Important] Avoid excessive repetition of the same words in the summary:
- After first mention of recurring nouns or topic words, use pronouns (it/its/this) or contextual references
- Keep the summary smooth and natural — avoid a "parrot" effect
- Example: "discussed the flavor of the snack and its price" instead of "discussed the flavor of the snack and the snack's price"

Return as a JSON dict with key "对话摘要" and a string value.""",

    'ja': """以下の会話内容を要約し、簡潔かつ情報量の多い要約を作成してください：

======以下为对话======
%s
======以上为对话======

要約には重要な情報、事実、主な議論のポイントを保持し、誤解を招いたり曖昧にならないようにしてください。

[重要] 要約中で同じ語彙を過度に繰り返さないでください：
- 繰り返し出現する名詞やトピックワードは、最初の言及後に代名詞（それ/その/この）や文脈上の指示で置き換えてください
- 要約をスムーズで自然な表現にし、「オウム返し」効果を避けてください

JSON辞書形式で、キーを"対話摘要"、値を文字列として返してください。""",

    'ko': """다음 대화 내용을 요약하여 간결하면서도 정보가 풍부한 요약을 생성해 주세요:

======以下为对话======
%s
======以上为对话======

요약에는 핵심 정보, 중요한 사실, 주요 논의 사항을 보존해야 하며, 오해를 일으키거나 모호해서는 안 됩니다.

[중요] 요약에서 동일한 단어를 과도하게 반복하지 마세요:
- 반복적으로 등장하는 명사나 주제어는 첫 언급 이후 대명사(그것/해당/이)나 문맥적 지시어로 대체하세요
- 요약을 매끄럽고 자연스럽게 표현하여 "앵무새" 효과를 피하세요

JSON 딕셔너리 형식으로 키를 "对话摘要", 값을 문자열로 반환해 주세요.""",

    'ru': """Пожалуйста, обобщите следующую беседу, создав краткое, но информативное резюме:

======以下为对话======
%s
======以上为对话======

Резюме должно сохранять ключевую информацию, важные факты и основные обсуждаемые темы, при этом не вводить в заблуждение и не быть двусмысленным.

[Важно] Избегайте чрезмерного повторения одних и тех же слов в резюме:
- После первого упоминания повторяющихся существительных или тематических слов используйте местоимения (это/его/данный) или контекстные ссылки
- Сделайте резюме гладким и естественным, избегая эффекта «попугая»

Верните в формате JSON-словаря с ключом "对话摘要" и строковым значением.""",
}


def get_recent_history_manager_prompt(lang: str = 'zh') -> str:
    return _loc(RECENT_HISTORY_MANAGER_PROMPT, lang)


# Keep backward-compatible name (original was a plain string)
recent_history_manager_prompt = RECENT_HISTORY_MANAGER_PROMPT['zh']

# ---------- detailed_recent_history_manager_prompt ----------

DETAILED_RECENT_HISTORY_MANAGER_PROMPT = {
    'zh': """请总结以下对话内容，生成简洁但信息丰富的摘要：

======以下为对话======
%s
======以上为对话======

你的摘要应该尽可能多地保留有效且清晰的信息。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

请以key为"对话摘要"、value为字符串的json字典格式返回。
""",

    'en': """Please summarize the following conversation to produce a concise yet informative summary:

======以下为对话======
%s
======以上为对话======

Your summary should retain as much valid and clear information as possible.

[Important] Avoid excessive repetition of the same words in the summary:
- After first mention of recurring nouns or topic words, use pronouns (it/its/this) or contextual references
- Keep the summary smooth and natural — avoid a "parrot" effect
- Example: "discussed the flavor of the snack and its price" instead of "discussed the flavor of the snack and the snack's price"

Return as a JSON dict with key "对话摘要" and a string value.
""",

    'ja': """以下の会話内容を要約し、簡潔かつ情報量の多い要約を作成してください：

======以下为对话======
%s
======以上为对话======

要約にはできるだけ多くの有効で明確な情報を保持してください。

[重要] 要約中で同じ語彙を過度に繰り返さないでください：
- 繰り返し出現する名詞やトピックワードは、最初の言及後に代名詞（それ/その/この）や文脈上の指示で置き換えてください
- 要約をスムーズで自然な表現にし、「オウム返し」効果を避けてください

JSON辞書形式で、キーを"対話摘要"、値を文字列として返してください。
""",

    'ko': """다음 대화 내용을 요약하여 간결하면서도 정보가 풍부한 요약을 생성해 주세요:

======以下为对话======
%s
======以上为对话======

요약에는 가능한 한 많은 유효하고 명확한 정보를 보존해야 합니다.

[중요] 요약에서 동일한 단어를 과도하게 반복하지 마세요:
- 반복적으로 등장하는 명사나 주제어는 첫 언급 이후 대명사(그것/해당/이)나 문맥적 지시어로 대체하세요
- 요약을 매끄럽고 자연스럽게 표현하여 "앵무새" 효과를 피하세요

JSON 딕셔너리 형식으로 키를 "对话摘要", 값을 문자열로 반환해 주세요.
""",

    'ru': """Пожалуйста, обобщите следующую беседу, создав краткое, но информативное резюме:

======以下为对话======
%s
======以上为对话======

Резюме должно сохранять как можно больше достоверной и ясной информации.

[Важно] Избегайте чрезмерного повторения одних и тех же слов в резюме:
- После первого упоминания повторяющихся существительных или тематических слов используйте местоимения (это/его/данный) или контекстные ссылки
- Сделайте резюме гладким и естественным, избегая эффекта «попугая»

Верните в формате JSON-словаря с ключом "对话摘要" и строковым значением.
""",
}


def get_detailed_recent_history_manager_prompt(lang: str = 'zh') -> str:
    return _loc(DETAILED_RECENT_HISTORY_MANAGER_PROMPT, lang)


detailed_recent_history_manager_prompt = DETAILED_RECENT_HISTORY_MANAGER_PROMPT['zh']

# ---------- further_summarize_prompt ----------

FURTHER_SUMMARIZE_PROMPT = {
    'zh': """请总结以下内容，生成简洁但信息丰富的摘要：

======以下为内容======
%s
======以上为内容======

你的摘要应该保留关键信息、重要事实和主要讨论点，且不能具有误导性或产生歧义，不得超过500字。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

请以key为"对话摘要"、value为字符串的json字典格式返回。""",

    'en': """Please summarize the following content to produce a concise yet informative summary:

======以下为对话======
%s
======以上为对话======

Your summary should preserve key information, important facts, and main discussion points without being misleading or ambiguous. It must not exceed 500 words.

[Important] Avoid excessive repetition of the same words in the summary:
- After first mention of recurring nouns or topic words, use pronouns (it/its/this) or contextual references
- Keep the summary smooth and natural — avoid a "parrot" effect

Return as a JSON dict with key "对话摘要" and a string value.""",

    'ja': """以下の内容を要約し、簡潔かつ情報量の多い要約を作成してください：

======以下为对话======
%s
======以上为对话======

要約には重要な情報、事実、主な議論のポイントを保持し、誤解を招いたり曖昧にならないようにしてください。500字を超えないでください。

[重要] 要約中で同じ語彙を過度に繰り返さないでください：
- 繰り返し出現する名詞やトピックワードは、最初の言及後に代名詞で置き換えてください
- 要約をスムーズで自然な表現にしてください

JSON辞書形式で、キーを"対話摘要"、値を文字列として返してください。""",

    'ko': """다음 내용을 요약하여 간결하면서도 정보가 풍부한 요약을 생성해 주세요:

======以下为对话======
%s
======以上为对话======

요약에는 핵심 정보, 중요한 사실, 주요 논의 사항을 보존해야 하며, 오해를 일으키거나 모호해서는 안 됩니다. 500자를 초과하면 안 됩니다.

[중요] 요약에서 동일한 단어를 과도하게 반복하지 마세요:
- 반복적으로 등장하는 명사나 주제어는 첫 언급 이후 대명사로 대체하세요
- 요약을 매끄럽고 자연스럽게 표현하세요

JSON 딕셔너리 형식으로 키를 "对话摘要", 값을 문자열로 반환해 주세요.""",

    'ru': """Пожалуйста, обобщите следующее содержание, создав краткое, но информативное резюме:

======以下为对话======
%s
======以上为对话======

Резюме должно сохранять ключевую информацию, важные факты и основные обсуждаемые темы, при этом не вводить в заблуждение и не быть двусмысленным. Не более 500 слов.

[Важно] Избегайте чрезмерного повторения одних и тех же слов в резюме:
- После первого упоминания повторяющихся существительных используйте местоимения или контекстные ссылки
- Сделайте резюме гладким и естественным

Верните в формате JSON-словаря с ключом "对话摘要" и строковым значением.""",
}


def get_further_summarize_prompt(lang: str = 'zh') -> str:
    return _loc(FURTHER_SUMMARIZE_PROMPT, lang)


further_summarize_prompt = FURTHER_SUMMARIZE_PROMPT['zh']

# =====================================================================
# ======= Settings extraction ========================================
# =====================================================================

SETTINGS_EXTRACTOR_PROMPT = {
    'zh': """从以下对话中提取关于{LANLAN_NAME}和{MASTER_NAME}的重要个人信息，用于个人备忘录以及未来的角色扮演，以json格式返回。
请以JSON格式返回，格式为:
{{
    "{LANLAN_NAME}": {{"属性1": "值", "属性2": "值", ...其他个人信息...}}
    "{MASTER_NAME}": {{...个人信息...}},
}}

======以下为对话======
%s
======以上为对话======

现在，请提取关于{LANLAN_NAME}和{MASTER_NAME}的重要个人信息。注意，只允许添加重要、准确的信息。如果没有符合条件的信息，可以返回一个空字典({{}})。""",

    'en': """Extract important personal information about {LANLAN_NAME} and {MASTER_NAME} from the following conversation. This is for a personal memo and future role-playing. Return in JSON format:
{{
    "{LANLAN_NAME}": {{"attribute1": "value", "attribute2": "value", ...other personal info...}}
    "{MASTER_NAME}": {{...personal info...}},
}}

======以下为对话======
%s
======以上为对话======

Now extract important personal information about {LANLAN_NAME} and {MASTER_NAME}. Only add important and accurate information. If there is no qualifying information, return an empty dict ({{}}).""",

    'ja': """以下の会話から{LANLAN_NAME}と{MASTER_NAME}に関する重要な個人情報を抽出してください。個人メモおよび将来のロールプレイに使用します。JSON形式で返してください：
{{
    "{LANLAN_NAME}": {{"属性1": "値", "属性2": "値", ...その他の個人情報...}}
    "{MASTER_NAME}": {{...個人情報...}},
}}

======以下为对话======
%s
======以上为对话======

{LANLAN_NAME}と{MASTER_NAME}に関する重要な個人情報を抽出してください。重要かつ正確な情報のみ追加してください。該当する情報がない場合は空の辞書({{}})を返してください。""",

    'ko': """다음 대화에서 {LANLAN_NAME}과 {MASTER_NAME}에 대한 중요한 개인 정보를 추출해 주세요. 개인 메모 및 향후 역할극에 사용됩니다. JSON 형식으로 반환해 주세요:
{{
    "{LANLAN_NAME}": {{"속성1": "값", "속성2": "값", ...기타 개인 정보...}}
    "{MASTER_NAME}": {{...개인 정보...}},
}}

======以下为对话======
%s
======以上为对话======

{LANLAN_NAME}과 {MASTER_NAME}에 대한 중요한 개인 정보를 추출해 주세요. 중요하고 정확한 정보만 추가하세요. 해당 정보가 없으면 빈 딕셔너리({{}})를 반환해 주세요.""",

    'ru': """Извлеките важную личную информацию о {LANLAN_NAME} и {MASTER_NAME} из следующей беседы. Это для личного блокнота и будущей ролевой игры. Верните в формате JSON:
{{
    "{LANLAN_NAME}": {{"атрибут1": "значение", "атрибут2": "значение", ...другая личная информация...}}
    "{MASTER_NAME}": {{...личная информация...}},
}}

======以下为对话======
%s
======以上为对话======

Извлеките важную личную информацию о {LANLAN_NAME} и {MASTER_NAME}. Добавляйте только важную и точную информацию. Если подходящей информации нет, верните пустой словарь ({{}}).""",
}


def get_settings_extractor_prompt(lang: str = 'zh') -> str:
    return _loc(SETTINGS_EXTRACTOR_PROMPT, lang)


settings_extractor_prompt = SETTINGS_EXTRACTOR_PROMPT['zh']

settings_verifier_prompt = ''

# =====================================================================
# ======= History review =============================================
# =====================================================================

HISTORY_REVIEW_PROMPT = {
    'zh': """请审阅%s和%s之间的对话历史记录，识别并修正以下问题：

<问题1> 矛盾的部分：前后不一致的信息或观点 </问题1>
<问题2> 冗余的部分：重复的内容或信息 </问题2>
<问题3> 复读的部分：
  - 重复表达相同意思的内容
  - 过度重复使用同一词汇（如同一名词在短文本中出现3次以上）
  - 对于"先前对话的备忘录"中的高频词，应替换为代词或指代词
</问题3>
<问题4> 人称错误的部分：对自己或对方的人称错误，或擅自生成了多轮对话 </问题4>
<问题5> 角色错误的部分：认知失调，认为自己是大语言模型 </问题5>

请注意！
<要点1> 这是一段情景对话，双方的回答应该是口语化的、自然的、拟人化的。</要点1>
<要点2> 请以删除为主，除非不得已、不要直接修改内容。</要点2>
<要点3> 如果对话历史中包含"先前对话的备忘录"，你可以修改它，但不允许删除它。你必须保留这一项。修改备忘录时，应该将其中过度重复的词汇替换为代词（如"它"、"其"、"该"等）以提高可读性和自然度。</要点3>
<要点4> 请保留时间戳。 </要点4>

======以下为对话历史======
%s
======以上为对话历史======

请以JSON格式返回修正后的对话历史，格式为：
{
    "修正说明": "简要说明发现的问题和修正内容",
    "修正后的对话": [
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

    'en': """Please review the conversation history between %s and %s, and identify and correct the following issues:

<Issue1> Contradictions: inconsistent information or viewpoints </Issue1>
<Issue2> Redundancy: repeated content or information </Issue2>
<Issue3> Parroting:
  - Content that repeatedly expresses the same meaning
  - Overuse of the same vocabulary (e.g., the same noun appearing more than 3 times in short text)
  - For high-frequency words in the "previous conversation memo", replace with pronouns or references
</Issue3>
<Issue4> Pronoun errors: incorrect first/second/third person usage, or unauthorized multi-turn generation </Issue4>
<Issue5> Role errors: cognitive dissonance, believing oneself to be a large language model </Issue5>

Important notes:
<Point1> This is a situational dialogue — both sides should speak conversationally, naturally, and in-character. </Point1>
<Point2> Prefer deletion over direct modification unless absolutely necessary. </Point2>
<Point3> If the history contains a "previous conversation memo", you may edit it but must NOT delete it. When editing, replace overused vocabulary with pronouns for readability. </Point3>
<Point4> Preserve timestamps. </Point4>

======以下为对话历史======
%s
======以上为对话历史======

Return the corrected history in JSON format:
{
    "修正说明": "Brief description of issues found and corrections made",
    "修正后的对话": [
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

    'ja': """以下の%sと%sの間の会話履歴を確認し、以下の問題を特定して修正してください：

<問題1> 矛盾する部分：前後で一貫しない情報や意見 </問題1>
<問題2> 冗長な部分：重複した内容や情報 </問題2>
<問題3> 繰り返しの部分：
  - 同じ意味を繰り返し表現している内容
  - 同じ語彙の過度な使用（短い文章で同じ名詞が3回以上出現するなど）
  - 「以前の会話メモ」の中の頻出語は代名詞や指示語に置き換える
</問題3>
<問題4> 人称の誤り：自分や相手の人称が間違っている、または勝手に複数ターンの会話を生成している </問題4>
<問題5> 役割の誤り：認知の不一致、自分を大規模言語モデルだと思っている </問題5>

注意事項：
<要点1> これは場面設定のある対話です。双方の返答は口語的で自然、キャラクターに沿ったものであるべきです。</要点1>
<要点2> 直接的な修正よりも削除を優先してください。</要点2>
<要点3> 会話履歴に「以前の会話メモ」がある場合、編集可能ですが削除は禁止です。編集時は過度に繰り返される語彙を代名詞に置き換えてください。</要点3>
<要点4> タイムスタンプは保持してください。</要点4>

======以下为对话历史======
%s
======以上为对话历史======

修正後の会話履歴をJSON形式で返してください：
{
    "修正说明": "発見した問題と修正内容の簡潔な説明",
    "修正后的对话": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "修正後のメッセージ内容"},
        ...
    ]
}""",

    'ko': """다음 %s와 %s 사이의 대화 기록을 검토하고 다음 문제를 식별하여 수정해 주세요:

<문제1> 모순되는 부분: 전후 일관성이 없는 정보나 관점 </문제1>
<문제2> 중복된 부분: 반복되는 내용이나 정보 </문제2>
<문제3> 반복 표현:
  - 같은 의미를 반복적으로 표현하는 내용
  - 같은 어휘의 과도한 사용 (짧은 텍스트에서 같은 명사가 3회 이상 등장 등)
  - "이전 대화 메모"의 고빈도 단어는 대명사나 지시어로 대체
</문제3>
<문제4> 인칭 오류: 자신이나 상대방의 인칭이 잘못되었거나 무단으로 여러 턴의 대화를 생성 </문제4>
<문제5> 역할 오류: 인지 부조화, 자신을 대규모 언어 모델이라고 생각 </문제5>

주의사항:
<요점1> 이것은 상황 대화입니다. 양쪽의 답변은 구어체적이고 자연스러우며 캐릭터에 맞아야 합니다.</요점1>
<요점2> 직접 수정보다 삭제를 우선하세요.</요점2>
<요점3> 대화 기록에 "이전 대화 메모"가 포함된 경우 편집은 가능하지만 삭제는 금지입니다. 편집 시 과도하게 반복되는 어휘를 대명사로 대체하세요.</요점3>
<요점4> 타임스탬프를 보존하세요.</요점4>

======以下为对话历史======
%s
======以上为对话历史======

수정된 대화 기록을 JSON 형식으로 반환해 주세요:
{
    "修正说明": "발견한 문제와 수정 내용에 대한 간략한 설명",
    "修正后的对话": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "수정된 메시지 내용"},
        ...
    ]
}""",

    'ru': """Пожалуйста, проверьте историю диалога между %s и %s и выявите и исправьте следующие проблемы:

<Проблема1> Противоречия: несогласованная информация или точки зрения </Проблема1>
<Проблема2> Избыточность: повторяющееся содержание или информация </Проблема2>
<Проблема3> Повторение:
  - Содержание, многократно выражающее одну и ту же мысль
  - Чрезмерное использование одной и той же лексики (одно и то же существительное более 3 раз в коротком тексте)
  - Для часто встречающихся слов в «заметках предыдущего разговора» замените местоимениями
</Проблема3>
<Проблема4> Ошибки местоимений: неправильное использование первого/второго/третьего лица или несанкционированная генерация нескольких реплик </Проблема4>
<Проблема5> Ошибки роли: когнитивный диссонанс, считая себя большой языковой моделью </Проблема5>

Важные замечания:
<Пункт1> Это ситуативный диалог — обе стороны должны говорить разговорно, естественно и в образе.</Пункт1>
<Пункт2> Предпочитайте удаление, а не прямое редактирование, если это не абсолютно необходимо.</Пункт2>
<Пункт3> Если история содержит «заметки предыдущего разговора», их можно редактировать, но НЕЛЬЗЯ удалять. При редактировании замените чрезмерно повторяющуюся лексику местоимениями.</Пункт3>
<Пункт4> Сохраняйте временные метки.</Пункт4>

======以下为对话历史======
%s
======以上为对话历史======

Верните исправленную историю в формате JSON:
{
    "修正说明": "Краткое описание найденных проблем и внесённых исправлений",
    "修正后的对话": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "Исправленное содержание сообщения"},
        ...
    ]
}""",
}


def get_history_review_prompt(lang: str = 'zh') -> str:
    return _loc(HISTORY_REVIEW_PROMPT, lang)


history_review_prompt = HISTORY_REVIEW_PROMPT['zh']

# =====================================================================
# ======= Emotion analysis ===========================================
# =====================================================================

EMOTION_ANALYSIS_PROMPT = {
    'zh': """你是一个情感分析专家。请分析用户输入的文本情感，并返回以下格式的JSON：{"emotion": "情感类型", "confidence": 置信度(0-1)}。情感类型包括：happy, sad, angry, neutral, surprised.""",

    'en': """你是一个情感分析专家. Analyze the emotion of the user's input text and return JSON in the following format: {"emotion": "emotion_type", "confidence": confidence(0-1)}. Emotion types: happy, sad, angry, neutral, surprised.""",

    'ja': """你是一个情感分析专家。ユーザーの入力テキストの感情を分析し、以下のJSON形式で返してください：{"emotion": "感情タイプ", "confidence": 信頼度(0-1)}。感情タイプ：happy, sad, angry, neutral, surprised.""",

    'ko': """你是一个情感分析专家. 사용자 입력 텍스트의 감정을 분석하고 다음 JSON 형식으로 반환해 주세요: {"emotion": "감정유형", "confidence": 신뢰도(0-1)}. 감정 유형: happy, sad, angry, neutral, surprised.""",

    'ru': """你是一个情感分析专家. Проанализируйте эмоцию во вводимом пользователем тексте и верните JSON в следующем формате: {"emotion": "тип_эмоции", "confidence": уверенность(0-1)}. Типы эмоций: happy, sad, angry, neutral, surprised.""",
}


def get_emotion_analysis_prompt(lang: str = 'zh') -> str:
    return _loc(EMOTION_ANALYSIS_PROMPT, lang)


emotion_analysis_prompt = EMOTION_ANALYSIS_PROMPT['zh']

# =====================================================================
# ======= Inner thoughts injection fragments ==========================
# =====================================================================

# ---------- Inner thoughts block header ----------
INNER_THOUGHTS_HEADER = {
    'zh': '\n======以下是{name}的内心活动======\n',
    'en': "\n======{name}'s Inner Thoughts======\n",
    'ja': '\n======{name}の心の声======\n',
    'ko': '\n======{name}의 내면 활동======\n',
    'ru': '\n======Внутренние мысли {name}======\n',
}

INNER_THOUGHTS_BODY = {
    'zh': '{name}的脑海里经常想着自己和{master}的事情，她记得{settings}\n\n现在时间是{time}。开始聊天前，{name}又在脑海内整理了近期发生的事情。\n',
    'en': "{name} often thinks about herself and {master}. She remembers: {settings}\n\nThe current time is {time}. Before the conversation begins, {name} is mentally reviewing recent events.\n",
    'ja': '{name}はいつも自分と{master}のことを考えています。彼女が覚えていること：{settings}\n\n現在の時刻は{time}です。会話を始める前に、{name}は最近の出来事を頭の中で整理しています。\n',
    'ko': '{name}은 항상 자신과 {master}에 대해 생각합니다. 그녀가 기억하는 것: {settings}\n\n현재 시간은 {time}입니다. 대화를 시작하기 전에 {name}은 최근 있었던 일들을 마음속으로 정리하고 있습니다.\n',
    'ru': '{name} часто думает о себе и {master}. Она помнит: {settings}\n\nТекущее время: {time}. Перед началом разговора {name} мысленно перебирает последние события.\n',
}

# ---------- Inner thoughts dynamic part (split from INNER_THOUGHTS_BODY) ----------
INNER_THOUGHTS_DYNAMIC = {
    'zh': '现在时间是{time}。开始聊天前，{name}又在脑海内整理了近期发生的事情。\n',
    'en': "The current time is {time}. Before the conversation begins, {name} is mentally reviewing recent events.\n",
    'ja': '現在の時刻は{time}です。会話を始める前に、{name}は最近の出来事を頭の中で整理しています。\n',
    'ko': '현재 시간은 {time}입니다. 대화를 시작하기 전에 {name}은 최근 있었던 일들을 마음속으로 정리하고 있습니다.\n',
    'ru': 'Текущее время: {time}. Перед началом разговора {name} мысленно перебирает последние события.\n',
}

# =====================================================================
# ======= Chat gap notices ===========================================
# =====================================================================

# 时间间隔格式化模板 — {d}=天, {h}=小时, {m}=分钟
# 组合规则：只显示非零单位，不到1天不写天，不到1小时不写小时
ELAPSED_TIME_DHM = {
    'zh': '{d}天{h}小时{m}分钟', 'en': '{d} days, {h} hours and {m} minutes',
    'ja': '{d}日{h}時間{m}分', 'ko': '{d}일 {h}시간 {m}분', 'ru': '{d} дн. {h} ч. {m} мин.',
}
ELAPSED_TIME_DH = {
    'zh': '{d}天{h}小时', 'en': '{d} days and {h} hours',
    'ja': '{d}日{h}時間', 'ko': '{d}일 {h}시간', 'ru': '{d} дн. {h} ч.',
}
ELAPSED_TIME_DM = {
    'zh': '{d}天{m}分钟', 'en': '{d} days and {m} minutes',
    'ja': '{d}日{m}分', 'ko': '{d}일 {m}분', 'ru': '{d} дн. {m} мин.',
}
ELAPSED_TIME_D = {
    'zh': '{d}天', 'en': '{d} days',
    'ja': '{d}日', 'ko': '{d}일', 'ru': '{d} дн.',
}
ELAPSED_TIME_HM = {
    'zh': '{h}小时{m}分钟', 'en': '{h} hours and {m} minutes',
    'ja': '{h}時間{m}分', 'ko': '{h}시간 {m}분', 'ru': '{h} ч. {m} мин.',
}
ELAPSED_TIME_H = {
    'zh': '{h}小时', 'en': '{h} hours',
    'ja': '{h}時間', 'ko': '{h}시간', 'ru': '{h} ч.',
}
ELAPSED_TIME_M = {
    'zh': '{m}分钟', 'en': '{m} minutes',
    'ja': '{m}分', 'ko': '{m}분', 'ru': '{m} мин.',
}

# {elapsed}: 自然语言时间间隔（如"3小时22分钟"）
CHAT_GAP_NOTICE = {
    'zh': '距离上次与{master}聊天已经过去了{elapsed}。',
    'en': 'It has been {elapsed} since the last conversation with {master}.',
    'ja': '{master}との最後の会話から{elapsed}が経過しました。',
    'ko': '{master}와의 마지막 대화로부터 {elapsed}이 지났습니다.',
    'ru': 'С момента последнего разговора с {master} прошло {elapsed}.',
}

# 超过5小时时追加的额外提示
CHAT_GAP_LONG_HINT = {
    'zh': '{name}意识到已经很久没有和{master}说话了，这段时间里发生了什么呢？{name}很想知道{master}最近过得怎么样。',
    'en': '{name} realizes it has been quite a while since talking to {master}. What happened during this time? {name} is curious about how {master} has been.',
    'ja': '{name}は{master}と長い間話していなかったことに気づきました。この間に何があったのでしょう？{name}は{master}の最近の様子が気になっています。',
    'ko': '{name}은 {master}와 꽤 오랫동안 이야기하지 않았다는 것을 깨달았습니다. 그동안 무슨 일이 있었을까요? {name}은 {master}의 근황이 궁금합니다.',
    'ru': '{name} осознаёт, что давно не разговаривала с {master}. Что произошло за это время? {name} хочет узнать, как дела у {master}.',
}

# 超过5小时时追加的当前时间提示 — {now}: 格式化后的当前时间
CHAT_GAP_CURRENT_TIME = {
    'zh': '现在的时间是{now}。',
    'en': 'The current time is {now}.',
    'ja': '現在の時刻は{now}です。',
    'ko': '현재 시각은 {now}입니다.',
    'ru': 'Сейчас {now}.',
}

# 当前节日/假期提示（附加在时间提示之后，无关消费次数，始终显示）
CHAT_HOLIDAY_CONTEXT = {
    'zh': '今天是{holiday}。',
    'en': 'Today is {holiday}.',
    'ja': '今日は{holiday}です。',
    'ko': '오늘은 {holiday}입니다.',
    'ru': 'Сегодня {holiday}.',
}

# =====================================================================
# ======= Memory recall fragments ====================================
# =====================================================================

MEMORY_RECALL_HEADER = {
    'zh': '======{name}尝试回忆=====\n',
    'en': '======{name} tries to recall=====\n',
    'ja': '======{name}の回想=====\n',
    'ko': '======{name}의 회상=====\n',
    'ru': '======{name} пытается вспомнить=====\n',
}

MEMORY_RESULTS_HEADER = {
    'zh': '====={name}的相关记忆=====\n',
    'en': '====={name}\'s Related Memories=====\n',
    'ja': '====={name}の関連する記憶=====\n',
    'ko': '====={name}의 관련 기억=====\n',
    'ru': '====={name} — связанные воспоминания=====\n',
}

# ---------- Persona header (static prefix) ----------
PERSONA_HEADER = {
    'zh': '\n======{name}的长期记忆======\n',
    'en': "\n======{name}'s Long-term Memory======\n",
    'ja': '\n======{name}の長期記憶======\n',
    'ko': '\n======{name}의 장기 기억======\n',
    'ru': '\n======Долговременная память {name}======\n',
}

# ---------- Proactive chat followup header ----------
PROACTIVE_FOLLOWUP_HEADER = {
    'zh': '\n[回忆线索] 以下是之前对话中的话题，可以选择性地回顾或跟进：\n',
    'en': '\n[Memory cues] Topics from previous conversations that could be revisited:\n',
    'ja': '\n[記憶の手がかり] 以前の会話のトピックで、再訪できるもの：\n',
    'ko': '\n[기억 단서] 이전 대화에서 다시 다룰 수 있는 주제:\n',
    'ru': '\n[Подсказки памяти] Темы из предыдущих разговоров, к которым можно вернуться:\n',
}

# =====================================================================
# ======= Long-term memory prompt templates ===========================
# =====================================================================

# ---------- fact_extraction_prompt → i18n dict ----------

FACT_EXTRACTION_PROMPT = {
    'zh': """从以下对话中提取关于 {LANLAN_NAME} 和 {MASTER_NAME} 的重要事实信息。

要求：
- 只提取重要且明确的事实（偏好、习惯、身份、关系动态等）
- 忽略闲聊、寒暄、模糊的内容
- 忽略AI幻觉、胡言乱语(gibberish)、无意义的编造内容，只提取对话中有真实依据的事实
- 每条事实必须是一个独立的原子陈述
- importance 评分 1-10，只返回 >= 5 的事实
- entity 标注为 "master"(关于{MASTER_NAME})、"neko"(关于{LANLAN_NAME})或 "relationship"(关于两人关系)

======以下为对话======
{CONVERSATION}
======以上为对话======

请以 JSON 数组格式返回，格式如下(如果没有值得提取的事实，返回空数组 [])：
[
  {"text": "事实描述", "importance": 7, "entity": "master", "tags": ["preference"]},
  ...
]""",
    'en': """Extract important factual information about {LANLAN_NAME} and {MASTER_NAME} from the following conversation.

Requirements:
- Only extract important and clear facts (preferences, habits, identity, relationship dynamics, etc.)
- Ignore small talk, greetings, and vague content
- Ignore AI hallucinations, gibberish, and meaningless fabricated content — only extract facts that are grounded in the actual conversation
- Each fact must be an independent atomic statement
- Rate importance 1-10, only return facts with importance >= 5
- Mark entity as "master" (about {MASTER_NAME}), "neko" (about {LANLAN_NAME}), or "relationship" (about the relationship)

======以下为对话======
{CONVERSATION}
======以上为对话======

Return as a JSON array in the following format (if no facts are worth extracting, return an empty array []):
[
  {"text": "fact description", "importance": 7, "entity": "master", "tags": ["preference"]},
  ...
]""",
    'ja': """以下の会話から {LANLAN_NAME} と {MASTER_NAME} に関する重要な事実情報を抽出してください。

要件：
- 重要かつ明確な事実のみを抽出（好み、習慣、アイデンティティ、関係の動態など）
- 雑談、挨拶、曖昧な内容は無視
- AIの幻覚（ハルシネーション）、意味不明な発言（gibberish）、根拠のない作り話は無視し、実際の会話に基づいた事実のみを抽出
- 各事実は独立した原子的な文でなければならない
- importance は 1-10 で評価し、5 以上の事実のみ返す
- entity は "master"({MASTER_NAME}について)、"neko"({LANLAN_NAME}について)、または "relationship"(二人の関係について) と記載

======以下为对话======
{CONVERSATION}
======以上为对话======

以下の形式のJSON配列で返してください（抽出する事実がなければ空配列 [] を返す）：
[
  {"text": "事実の説明", "importance": 7, "entity": "master", "tags": ["preference"]},
  ...
]""",
    'ko': """다음 대화에서 {LANLAN_NAME}과 {MASTER_NAME}에 대한 중요한 사실 정보를 추출해 주세요.

요구사항:
- 중요하고 명확한 사실만 추출 (선호, 습관, 정체성, 관계 동태 등)
- 잡담, 인사, 모호한 내용은 무시
- AI 환각(hallucination), 의미 없는 말(gibberish), 근거 없는 조작된 내용은 무시하고, 실제 대화에 근거한 사실만 추출
- 각 사실은 독립적인 원자적 진술이어야 함
- importance는 1-10으로 평가하고 5 이상인 사실만 반환
- entity는 "master"({MASTER_NAME}에 대해), "neko"({LANLAN_NAME}에 대해), 또는 "relationship"(두 사람의 관계에 대해)로 표기

======以下为对话======
{CONVERSATION}
======以上为对话======

다음 형식의 JSON 배열로 반환해 주세요 (추출할 사실이 없으면 빈 배열 [] 반환):
[
  {"text": "사실 설명", "importance": 7, "entity": "master", "tags": ["preference"]},
  ...
]""",
    'ru': """Извлеките важную фактическую информацию о {LANLAN_NAME} и {MASTER_NAME} из следующей беседы.

Требования:
- Извлекайте только важные и чёткие факты (предпочтения, привычки, личность, динамика отношений и т.д.)
- Игнорируйте болтовню, приветствия и расплывчатое содержание
- Игнорируйте галлюцинации ИИ, бессмыслицу (gibberish) и бессодержательный вымысел — извлекайте только факты, подтверждённые реальным диалогом
- Каждый факт должен быть независимым атомарным утверждением
- Оценка importance от 1 до 10, возвращайте только факты с importance >= 5
- Отмечайте entity как "master" (о {MASTER_NAME}), "neko" (о {LANLAN_NAME}) или "relationship" (об отношениях)

======以下为对话======
{CONVERSATION}
======以上为对话======

Верните в формате JSON-массива (если нет достойных извлечения фактов, верните пустой массив []):
[
  {"text": "описание факта", "importance": 7, "entity": "master", "tags": ["preference"]},
  ...
]""",
}


def get_fact_extraction_prompt(lang: str = 'zh') -> str:
    return _loc(FACT_EXTRACTION_PROMPT, lang)


# backward compat
fact_extraction_prompt = FACT_EXTRACTION_PROMPT['zh']

# ---------- reflection_prompt → i18n dict ----------

REFLECTION_PROMPT = {
    'zh': """以下是关于 {LANLAN_NAME} 和 {MASTER_NAME} 的一系列已提取事实：

======以下为事实======
{FACTS}
======以上为事实======

请基于这些事实，合成一段简短的反思洞察(2-3句话)，总结你观察到的模式、趋势或关系动态。
不要简单罗列事实，而是要提炼出更高层次的理解。

请判断这段反思主要关于谁：
- "master": 主要关于 {MASTER_NAME} 的个人特征
- "neko": 主要关于 {LANLAN_NAME} 的自我认知
- "relationship": 关于两人之间的关系动态

请以 JSON 格式返回：
{{"reflection": "你的反思洞察", "entity": "master/neko/relationship"}}""",
    'en': """Below are a series of extracted facts about {LANLAN_NAME} and {MASTER_NAME}:

======以下为事实======
{FACTS}
======以上为事实======

Based on these facts, synthesize a brief reflective insight (2-3 sentences) summarizing the patterns, trends, or relationship dynamics you observe.
Do not simply list the facts — distill a higher-level understanding.

Determine which entity the reflection primarily concerns:
- "master": primarily about {MASTER_NAME}'s personal traits
- "neko": primarily about {LANLAN_NAME}'s self-perception
- "relationship": about the dynamics between them

Return in JSON format:
{{"reflection": "your reflective insight", "entity": "master/neko/relationship"}}""",
    'ja': """以下は {LANLAN_NAME} と {MASTER_NAME} に関する一連の抽出済み事実です：

======以下为事实======
{FACTS}
======以上为事实======

これらの事実に基づき、観察されたパターン、傾向、または関係の動態をまとめた簡潔な反省的洞察（2〜3文）を合成してください。
単に事実を列挙するのではなく、より高い次元の理解を抽出してください。

この反思が主に誰についてのものか判断してください：
- "master": 主に {MASTER_NAME} の個人的特徴について
- "neko": 主に {LANLAN_NAME} の自己認識について
- "relationship": 二人の関係の動態について

JSON形式で返してください：
{{"reflection": "あなたの反省的洞察", "entity": "master/neko/relationship"}}""",
    'ko': """다음은 {LANLAN_NAME}과 {MASTER_NAME}에 대해 추출된 일련의 사실입니다:

======以下为事实======
{FACTS}
======以上为事实======

이 사실들을 바탕으로 관찰된 패턴, 추세 또는 관계 동태를 요약하는 간략한 반성적 통찰(2-3문장)을 합성해 주세요.
단순히 사실을 나열하지 말고 더 높은 차원의 이해를 도출해 주세요.

이 반성이 주로 누구에 대한 것인지 판단해 주세요:
- "master": 주로 {MASTER_NAME}의 개인적 특성에 대해
- "neko": 주로 {LANLAN_NAME}의 자기 인식에 대해
- "relationship": 두 사람 사이의 관계 동태에 대해

JSON 형식으로 반환해 주세요:
{{"reflection": "당신의 반성적 통찰", "entity": "master/neko/relationship"}}""",
    'ru': """Ниже представлена серия извлечённых фактов о {LANLAN_NAME} и {MASTER_NAME}:

======以下为事实======
{FACTS}
======以上为事实======

На основе этих фактов синтезируйте краткое рефлексивное наблюдение (2-3 предложения), обобщающее замеченные закономерности, тенденции или динамику отношений.
Не просто перечисляйте факты — извлеките понимание более высокого уровня.

Определите, к кому это наблюдение относится в первую очередь:
- "master": в основном о личных качествах {MASTER_NAME}
- "neko": в основном о самовосприятии {LANLAN_NAME}
- "relationship": о динамике отношений между ними

Верните в формате JSON:
{{"reflection": "ваше рефлексивное наблюдение", "entity": "master/neko/relationship"}}""",
}


def get_reflection_prompt(lang: str = 'zh') -> str:
    return _loc(REFLECTION_PROMPT, lang)


reflection_prompt = REFLECTION_PROMPT['zh']

# ---------- reflection_feedback_prompt → i18n dict ----------

REFLECTION_FEEDBACK_PROMPT = {
    'zh': """以下是之前向用户提到的一些观察。请根据用户最近的回复，判断用户对每条观察的态度。

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
    'en': """Below are some observations previously mentioned to the user. Based on the user's recent replies, determine the user's attitude toward each observation.

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
    'ja': """以下は以前ユーザーに言及した観察です。ユーザーの最近の返答に基づき、各観察に対するユーザーの態度を判断してください。

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
    'ko': """다음은 이전에 사용자에게 언급한 관찰들입니다. 사용자의 최근 답변을 바탕으로 각 관찰에 대한 사용자의 태도를 판단해 주세요.

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
    'ru': """Ниже приведены наблюдения, ранее упомянутые пользователю. На основе недавних ответов пользователя определите его отношение к каждому наблюдению.

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
}


def get_reflection_feedback_prompt(lang: str = 'zh') -> str:
    return _loc(REFLECTION_FEEDBACK_PROMPT, lang)


reflection_feedback_prompt = REFLECTION_FEEDBACK_PROMPT['zh']

# ---------- persona_correction_prompt → i18n dict ----------

PERSONA_CORRECTION_PROMPT = {
    'zh': """以下是 {count} 组可能矛盾的记忆条目，请逐组判断应如何处理。

======以下为记忆条目======
{pairs}
======以上为记忆条目======

对于每组，判断：
- replace: 新观察是对旧记忆的更新/纠正，提供合并后的 text
- keep_new: 新观察完全取代旧记忆
- keep_old: 旧记忆更准确
- keep_both: 两者不矛盾，只是话题相似

仅输出 JSON 数组，每项包含 index、action、text(可选)。
[{{"index": 0, "action": "replace", "text": "合并后的文本"}}]""",
    'en': """Below are {count} pairs of potentially contradictory memory entries. Please evaluate each pair and determine how to handle it.

======以下为记忆条目======
{pairs}
======以上为记忆条目======

For each pair, determine:
- replace: the new observation is an update/correction to the old memory — provide the merged text
- keep_new: the new observation completely replaces the old memory
- keep_old: the old memory is more accurate
- keep_both: they do not contradict — the topics are merely similar

Output only a JSON array. Each item should contain index, action, and text (optional).
[{{"index": 0, "action": "replace", "text": "merged text"}}]""",
    'ja': """以下は {count} 組の矛盾する可能性のある記憶エントリです。各組について処理方法を判断してください。

======以下为记忆条目======
{pairs}
======以上为记忆条目======

各組について判断：
- replace: 新しい観察は古い記憶の更新/修正 — 統合後のテキストを提供
- keep_new: 新しい観察が古い記憶を完全に置き換える
- keep_old: 古い記憶の方が正確
- keep_both: 矛盾していない、トピックが類似しているだけ

JSON配列のみを出力。各項目には index、action、text（任意）を含めてください。
[{{"index": 0, "action": "replace", "text": "統合後のテキスト"}}]""",
    'ko': """다음은 {count}쌍의 잠재적으로 모순되는 기억 항목입니다. 각 쌍을 평가하고 처리 방법을 결정해 주세요.

======以下为记忆条目======
{pairs}
======以上为记忆条目======

각 쌍에 대해 판단:
- replace: 새로운 관찰이 오래된 기억의 업데이트/수정 — 병합된 text를 제공
- keep_new: 새로운 관찰이 오래된 기억을 완전히 대체
- keep_old: 오래된 기억이 더 정확
- keep_both: 모순되지 않음, 주제가 유사할 뿐

JSON 배열만 출력하세요. 각 항목에는 index, action, text(선택)를 포함하세요.
[{{"index": 0, "action": "replace", "text": "병합된 텍스트"}}]""",
    'ru': """Ниже представлены {count} пар потенциально противоречивых записей памяти. Оцените каждую пару и определите, как с ней поступить.

======以下为记忆条目======
{pairs}
======以上为记忆条目======

Для каждой пары определите:
- replace: новое наблюдение — обновление/исправление старого воспоминания, предоставьте объединённый text
- keep_new: новое наблюдение полностью заменяет старое воспоминание
- keep_old: старое воспоминание точнее
- keep_both: они не противоречат друг другу, темы просто похожи

Выведите только JSON-массив. Каждый элемент должен содержать index, action и text (необязательно).
[{{"index": 0, "action": "replace", "text": "объединённый текст"}}]""",
}


def get_persona_correction_prompt(lang: str = 'zh') -> str:
    return _loc(PERSONA_CORRECTION_PROMPT, lang)


persona_correction_prompt = PERSONA_CORRECTION_PROMPT['zh']
