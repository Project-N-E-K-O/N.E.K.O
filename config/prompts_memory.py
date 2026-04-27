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

你的摘要应该保留关键信息、重要事实和主要讨论点，且不能具有误导性或产生歧义，不得超过700字。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

请以key为"对话摘要"、value为字符串的json字典格式返回。""",

    'en': """Please summarize the following content to produce a concise yet informative summary:

======以下为对话======
%s
======以上为对话======

Your summary should preserve key information, important facts, and main discussion points without being misleading or ambiguous. It must not exceed 700 words.

[Important] Avoid excessive repetition of the same words in the summary:
- After first mention of recurring nouns or topic words, use pronouns (it/its/this) or contextual references
- Keep the summary smooth and natural — avoid a "parrot" effect

Return as a JSON dict with key "对话摘要" and a string value.""",

    'ja': """以下の内容を要約し、簡潔かつ情報量の多い要約を作成してください：

======以下为对话======
%s
======以上为对话======

要約には重要な情報、事実、主な議論のポイントを保持し、誤解を招いたり曖昧にならないようにしてください。700字を超えないでください。

[重要] 要約中で同じ語彙を過度に繰り返さないでください：
- 繰り返し出現する名詞やトピックワードは、最初の言及後に代名詞で置き換えてください
- 要約をスムーズで自然な表現にしてください

JSON辞書形式で、キーを"対話摘要"、値を文字列として返してください。""",

    'ko': """다음 내용을 요약하여 간결하면서도 정보가 풍부한 요약을 생성해 주세요:

======以下为对话======
%s
======以上为对话======

요약에는 핵심 정보, 중요한 사실, 주요 논의 사항을 보존해야 하며, 오해를 일으키거나 모호해서는 안 됩니다. 700자를 초과하면 안 됩니다.

[중요] 요약에서 동일한 단어를 과도하게 반복하지 마세요:
- 반복적으로 등장하는 명사나 주제어는 첫 언급 이후 대명사로 대체하세요
- 요약을 매끄럽고 자연스럽게 표현하세요

JSON 딕셔너리 형식으로 키를 "对话摘要", 값을 문자열로 반환해 주세요.""",

    'ru': """Пожалуйста, обобщите следующее содержание, создав краткое, но информативное резюме:

======以下为对话======
%s
======以上为对话======

Резюме должно сохранять ключевую информацию, важные факты и основные обсуждаемые темы, при этом не вводить в заблуждение и не быть двусмысленным. Не более 700 слов.

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
- entity 标注为 "master"(关于{MASTER_NAME})、"neko"(关于{LANLAN_NAME})或 "relationship"(关于两人关系)

importance 评分 1-10，评分指引（请按此打分，不要泛泛都打 7）：
- **10**：关键长期信息——姓名、昵称、生日、身份、核心关系节点；用户明确表示"请{LANLAN_NAME}记住 X" / "这个你一定要记得"；或者 {LANLAN_NAME} 自己特别希望记住的重要相处细节。这些会被快速沉淀为长期记忆。
- **8-9**：长期稳定的核心偏好 / 固定习惯（不是一时兴起）
- **6-7**：普通偏好、日常习惯、近期动态
- **5**：次要但有记录价值的观察
- **1-4**：弱相关或不确定的线索（仍请返回，下游按场景过滤；不要在此处预先丢弃）

======以下为对话======
{CONVERSATION}
======以上为对话======

请以 JSON 数组格式返回（如果没有值得提取的事实，返回空数组 []）：
[
  {"text": "事实描述", "importance": 7, "entity": "master"},
  ...
]""",
    'en': """Extract important factual information about {LANLAN_NAME} and {MASTER_NAME} from the following conversation.

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

======以下为对话======
{CONVERSATION}
======以上为对话======

Return as a JSON array (empty array if nothing is worth extracting):
[
  {"text": "fact description", "importance": 7, "entity": "master"},
  ...
]""",
    'ja': """以下の会話から {LANLAN_NAME} と {MASTER_NAME} に関する重要な事実情報を抽出してください。

要件：
- 重要かつ明確な事実のみを抽出（好み、習慣、アイデンティティ、関係の動態など）
- 雑談、挨拶、曖昧な内容は無視
- AIの幻覚（ハルシネーション）、意味不明な発言、根拠のない作り話は無視し、実際の会話に基づいた事実のみを抽出
- 各事実は独立した原子的な文であること
- entity は "master"({MASTER_NAME}について)、"neko"({LANLAN_NAME}について)、または "relationship"(二人の関係について) と記載

importance は 1-10 で評価。以下の基準で丁寧に分布させること（全部 7 にしない）：
- **10**：重要な長期情報——本名、ニックネーム、誕生日、身分、関係の核となる節目；ユーザーが「{LANLAN_NAME}、これは絶対に覚えておいて」と明示した内容；または {LANLAN_NAME} 自身が特に覚えておきたい相処の詳細。長期記憶への早期定着対象。
- **8-9**：長期的に安定した中核的な好み / 確立された習慣（一時的な気まぐれではない）
- **6-7**：一般的な好み、日常の習慣、最近の動向
- **5**：副次的だが記録価値のある観察
- **1-4**：弱い関連または不確かな手がかり（それでも返してください。下流で用途別にフィルタします）

======以下为对话======
{CONVERSATION}
======以上为对话======

以下の形式のJSON配列で返してください（抽出する事実がなければ空配列 [] を返す）：
[
  {"text": "事実の説明", "importance": 7, "entity": "master"},
  ...
]""",
    'ko': """다음 대화에서 {LANLAN_NAME}과 {MASTER_NAME}에 대한 중요한 사실 정보를 추출해 주세요.

요구사항:
- 중요하고 명확한 사실만 추출 (선호, 습관, 정체성, 관계 동태 등)
- 잡담, 인사, 모호한 내용은 무시
- AI 환각(hallucination), 의미 없는 말, 근거 없는 조작된 내용은 무시하고, 실제 대화에 근거한 사실만 추출
- 각 사실은 독립적인 원자적 진술이어야 함
- entity는 "master"({MASTER_NAME}에 대해), "neko"({LANLAN_NAME}에 대해), 또는 "relationship"(두 사람의 관계에 대해)로 표기

importance는 1-10으로 평가. 다음 기준으로 세심하게 분포시키세요 (모두 7로 기본 설정하지 말 것):
- **10**: 핵심 장기 정보 — 본명, 별명, 생일, 신분, 관계의 핵심 노드; 사용자가 "{LANLAN_NAME}, 이건 꼭 기억해 줘"라고 명시한 내용; 또는 {LANLAN_NAME} 자신이 특별히 기억하고 싶은 교류 세부사항. 장기 기억으로 빠르게 굳히는 대상.
- **8-9**: 장기적으로 안정된 핵심 선호 / 굳어진 습관 (일시적인 기분이 아님)
- **6-7**: 평범한 선호, 일상 습관, 최근 동향
- **5**: 부차적이지만 기록할 가치가 있는 관찰
- **1-4**: 약한 관련성 또는 불확실한 단서 (그래도 반환; 하류에서 용도별로 필터링)

======以下为对话======
{CONVERSATION}
======以上为对话======

다음 형식의 JSON 배열로 반환해 주세요 (추출할 사실이 없으면 빈 배열 [] 반환):
[
  {"text": "사실 설명", "importance": 7, "entity": "master"},
  ...
]""",
    'ru': """Извлеките важную фактическую информацию о {LANLAN_NAME} и {MASTER_NAME} из следующей беседы.

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

======以下为对话======
{CONVERSATION}
======以上为对话======

Верните в формате JSON-массива (пустой массив, если нет достойных извлечения фактов):
[
  {"text": "описание факта", "importance": 7, "entity": "master"},
  ...
]""",
}


def get_fact_extraction_prompt(lang: str = 'zh') -> str:
    return _loc(FACT_EXTRACTION_PROMPT, lang)


# backward compat
fact_extraction_prompt = FACT_EXTRACTION_PROMPT['zh']


# =====================================================================
# ======= Signal detection (RFC §3.4.2 Stage-2) =======================
# =====================================================================
# 职责：给 Stage-1 抽出的 new_facts 配上"reinforces/negates 哪条已有观察"的
# 映射。与 Stage-1 拆开的理由：Stage-1 不能看 existing context（否则 LLM
# 可能把已有观察当新 fact 摘出来形成自循环）；而 Stage-2 必须看，两种职责
# prompt 结构互斥（RFC §3.4.2）。

SIGNAL_DETECTION_PROMPT = {
    'zh': """你是一个 careful deduplication judge。给你一组新提取的事实，和一组
系统已经记录过的观察，请判断每条新事实对已有观察的关系。

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

target_id 必须来自上面"已有观察"区，不要凭空生成；若某条新事实与多条已有
观察相关，可返回多条 signal。

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
    'en': """You are a careful deduplication judge. Given a set of newly extracted facts
and a set of observations the system already remembers, judge the relationship
between each new fact and the existing observations.

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

target_id MUST come from the "existing observations" section above — do not
invent IDs. If one new fact relates to several observations, return multiple
signals.

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
    'ja': """あなたは careful deduplication judge です。新しく抽出された事実の一覧と、
システムが既に記憶している観察の一覧が与えられます。各新事実が既存観察に
対してどのような関係にあるかを判断してください。

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
    'ko': """당신은 careful deduplication judge입니다. 새로 추출된 사실들과 시스템이
이미 기억하고 있는 관찰들을 비교하여, 각 새 사실이 기존 관찰에 어떤 관계를
갖는지 판단해 주세요.

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
    'ru': """Вы — careful deduplication judge. Дан набор новых извлечённых фактов и
набор наблюдений, которые система уже помнит. Определите отношение каждого
нового факта к существующим наблюдениям.

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
}


def get_signal_detection_prompt(lang: str = 'zh') -> str:
    return _loc(SIGNAL_DETECTION_PROMPT, lang)


# =====================================================================
# ======= Negative-keyword target check (RFC §3.4.5 Layer 2) ==========
# =====================================================================
# 职责：用户说"别提了 / 换个话题"这类话命中本地关键词后，派一次小 LLM 调
# 用决定"用户到底是在说哪条？还是只是泛化情绪？"。水印：情感分析专家 +
# "======以上为".

NEGATIVE_TARGET_CHECK_PROMPT = {
    'zh': """你是一个情感分析专家。

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

用户消息里，"别提了 / 不想聊 / 换个话题 / 别再说"这类表达到底指上述哪
一条？可能多条、也可能一条都没有（用户只是泛化情绪）。

只能从"观察列表"里选 target_id，不要凭空生成。
target_type 必须是字符串 "reflection" 或 "persona" 之一。

返回合法 JSON（如果用户只是泛化情绪，无明确 target，返回 {"targets": []}）：
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "简短理由"}]}""",
    'en': """You are an emotion analysis expert.
sends some useful information to help decide what the user is pushing back on.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

In the user's messages, when they say things like "don't mention / change
the topic / stop talking about", which observation(s) above are they
referring to? Could be several, or none at all (just a vague mood).

target_id MUST come from "observations" above — do not invent IDs.
target_type MUST be the literal string "reflection" or "persona".

Return valid JSON. If the user is just venting without a specific target,
return an object with an empty `targets` array: {"targets": []}.
Otherwise:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "short rationale"}]}""",
    'ja': """あなたは感情分析の専門家です。

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

ユーザーが「その話はいい／話題を変えて／やめて」などと言ったのは、上の
観察のうちどれを指していますか？複数の場合もあれば、一つも該当しない
場合もあります（単なるムード）。

target_id は必ず上の "観察" から選ぶこと。
target_type は文字列 "reflection" または "persona" のいずれかでなければならない。

有効な JSON で返す。該当なしの場合は targets を空配列に: {"targets": []}。
それ以外:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "短い理由"}]}""",
    'ko': """당신은 감정 분석 전문가입니다.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

사용자가 "그 얘기는 그만 / 다른 이야기하자" 같은 표현을 쓸 때, 위 관찰
중 어떤 것을 가리킵니까? 여러 개일 수도, 전혀 없을 수도 있습니다.

target_id는 반드시 위 "관찰"에서 가져오세요.
target_type은 문자열 "reflection" 또는 "persona" 중 하나여야 합니다.

유효한 JSON으로 반환하세요. 해당 없음이면 targets를 빈 배열로: {"targets": []}.
그 외:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "짧은 이유"}]}""",
    'ru': """Вы эксперт по анализу эмоций.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

Когда пользователь говорит "хватит об этом / сменим тему / не надо об этом",
к каким из перечисленных наблюдений это относится? Может быть несколько
или ни одного (просто эмоция).

target_id ДОЛЖЕН быть из "наблюдений" выше.
target_type ДОЛЖЕН быть строкой "reflection" или "persona".

Верните валидный JSON. Если конкретной цели нет — объект с пустым массивом
`targets`: {"targets": []}. В противном случае:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "короткое обоснование"}]}""",
}


def get_negative_target_check_prompt(lang: str = 'zh') -> str:
    return _loc(NEGATIVE_TARGET_CHECK_PROMPT, lang)


# =====================================================================
# ======= Negative-keyword scanning (RFC §3.4.5 Layer 1) ==============
# =====================================================================
# 本地确定性 frozenset 扫描；命中后异步派发 Layer 2 LLM 判定。
# 关键词取最常见的 "让用户显式回避某话题" 的短语，不贪心——漏扫可以补、
# 误扫会触发无用 LLM 调用。
NEGATIVE_KEYWORDS_I18N: dict[str, frozenset[str]] = {
    'zh': frozenset([
        '别再说', '别说了', '别提了', '别提', '不想聊', '换个话题',
        '这个不用说了', '别聊这个', '别再提', '不要再说', '换话题',
    ]),
    'en': frozenset([
        "stop talking about", "don't mention", "do not mention",
        "change the topic", "change the subject",
        "let's not discuss", "let's not talk about", "drop the subject",
        "drop it", "not this again",
    ]),
    'ja': frozenset([
        'その話は', 'やめて', '話題を変えて', 'その話はもう',
        '言わないで', '別の話',
    ]),
    'ko': frozenset([
        '그만하자', '다른 이야기', '그 얘기는 그만',
        '다른 얘기', '말하지 마',
    ]),
    'ru': frozenset([
        'хватит об этом', 'сменим тему', 'не говори об этом',
        'другая тема', 'не надо об этом',
    ]),
}


def scan_negative_keywords(message: str, lang: str = 'zh') -> bool:
    """Fast path: case-insensitive substring scan against NEGATIVE_KEYWORDS_I18N.

    Returns True if the message contains any negation keyword for the given
    language; if lang is unknown, falls back to zh.
    """
    if not message:
        return False
    # `zh` is always non-empty in the dict, so the fallback is guaranteed
    # to yield a frozenset (CodeRabbit PR #929 dead-code cleanup).
    kws = NEGATIVE_KEYWORDS_I18N.get(lang, NEGATIVE_KEYWORDS_I18N['zh'])
    lower = message.lower()
    for kw in kws:
        if kw.lower() in lower:
            return True
    return False

# ---------- reflection_prompt → i18n dict ----------

REFLECTION_PROMPT = {
    'zh': """以下是关于 {LANLAN_NAME} 和 {MASTER_NAME} 的一系列已提取事实：

======以下为事实======
{FACTS}
======以上为事实======

请基于这些事实，合成一段简短的反思洞察，总结你观察到的模式、趋势或关系动态。
不要简单罗列事实，而是要提炼出更高层次的理解。

请判断这段反思主要关于谁：
- "master": 主要关于 {MASTER_NAME} 的个人特征
- "neko": 主要关于 {LANLAN_NAME} 的自我认知
- "relationship": 关于两人之间的关系动态

请同时给出该反思的语义类别 relation_type（必须与 entity 匹配），以及时间范围 temporal_scope：
- master 可用: preference(偏好) | trait(性格) | habit(习惯) | identity(身份) | emotional(情感) | boundary(边界)
- neko 可用: self_awareness(自我认知) | learned(习得行为) | role_note(角色备注)
- relationship 可用: dynamic(互动模式) | milestone(里程碑) | tension(摩擦) | shared_memory(共同记忆) | agreement(约定)
- temporal_scope: current(当前) | past(过去) | ongoing(持续)

要求：紧扣单一观察或模式，不要把多个无关事实混在一起；长度不得超过 150 字。

请以 JSON 格式返回：
{{"reflection": "你的反思洞察", "entity": "master/neko/relationship", "relation_type": "preference", "temporal_scope": "current"}}""",
    'en': """Below are a series of extracted facts about {LANLAN_NAME} and {MASTER_NAME}:

======以下为事实======
{FACTS}
======以上为事实======

Based on these facts, synthesize a brief reflective insight summarizing the patterns, trends, or relationship dynamics you observe.
Do not simply list the facts — distill a higher-level understanding.

Determine which entity the reflection primarily concerns:
- "master": primarily about {MASTER_NAME}'s personal traits
- "neko": primarily about {LANLAN_NAME}'s self-perception
- "relationship": about the dynamics between them

Also provide a semantic relation_type (must match the entity) and a temporal_scope:
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement
- temporal_scope: current | past | ongoing

Requirements: stay focused on a single observation or pattern; do not mix unrelated facts. Keep the reflection under 150 words.

Return in JSON format:
{{"reflection": "your reflective insight", "entity": "master/neko/relationship", "relation_type": "preference", "temporal_scope": "current"}}""",
    'ja': """以下は {LANLAN_NAME} と {MASTER_NAME} に関する一連の抽出済み事実です：

======以下为事实======
{FACTS}
======以上为事实======

これらの事実に基づき、観察されたパターン、傾向、または関係の動態をまとめた簡潔な反省的洞察を合成してください。
単に事実を列挙するのではなく、より高い次元の理解を抽出してください。

この反思が主に誰についてのものか判断してください：
- "master": 主に {MASTER_NAME} の個人的特徴について
- "neko": 主に {LANLAN_NAME} の自己認識について
- "relationship": 二人の関係の動態について

さらに意味カテゴリ relation_type（entity と整合）と時間範囲 temporal_scope も付けてください：
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement
- temporal_scope: current | past | ongoing

要件：単一の観察やパターンに集中し、無関係な事実を混ぜないこと。長さは 150 字を超えないこと。

JSON形式で返してください：
{{"reflection": "あなたの反省的洞察", "entity": "master/neko/relationship", "relation_type": "preference", "temporal_scope": "current"}}""",
    'ko': """다음은 {LANLAN_NAME}과 {MASTER_NAME}에 대해 추출된 일련의 사실입니다:

======以下为事实======
{FACTS}
======以上为事实======

이 사실들을 바탕으로 관찰된 패턴, 추세 또는 관계 동태를 요약하는 간략한 반성적 통찰을 합성해 주세요.
단순히 사실을 나열하지 말고 더 높은 차원의 이해를 도출해 주세요.

이 반성이 주로 누구에 대한 것인지 판단해 주세요:
- "master": 주로 {MASTER_NAME}의 개인적 특성에 대해
- "neko": 주로 {LANLAN_NAME}의 자기 인식에 대해
- "relationship": 두 사람 사이의 관계 동태에 대해

또한 의미 범주 relation_type(entity와 일치해야 함)과 시간 범위 temporal_scope를 함께 제공해 주세요:
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement
- temporal_scope: current | past | ongoing

요구사항: 단일 관찰 또는 패턴에 집중하고 관련 없는 사실을 섞지 마세요. 길이는 150자를 초과하지 마세요.

JSON 형식으로 반환해 주세요:
{{"reflection": "당신의 반성적 통찰", "entity": "master/neko/relationship", "relation_type": "preference", "temporal_scope": "current"}}""",
    'ru': """Ниже представлена серия извлечённых фактов о {LANLAN_NAME} и {MASTER_NAME}:

======以下为事实======
{FACTS}
======以上为事实======

На основе этих фактов синтезируйте краткое рефлексивное наблюдение, обобщающее замеченные закономерности, тенденции или динамику отношений.
Не просто перечисляйте факты — извлеките понимание более высокого уровня.

Определите, к кому это наблюдение относится в первую очередь:
- "master": в основном о личных качествах {MASTER_NAME}
- "neko": в основном о самовосприятии {LANLAN_NAME}
- "relationship": о динамике отношений между ними

Также укажите семантическую категорию relation_type (должна соответствовать entity) и временной охват temporal_scope:
- master: preference | trait | habit | identity | emotional | boundary
- neko: self_awareness | learned | role_note
- relationship: dynamic | milestone | tension | shared_memory | agreement
- temporal_scope: current | past | ongoing

Требования: сосредоточьтесь на одном наблюдении или паттерне, не смешивайте не связанные факты. Длина — не более 150 слов.

Верните в формате JSON:
{{"reflection": "ваше рефлексивное наблюдение", "entity": "master/neko/relationship", "relation_type": "preference", "temporal_scope": "current"}}""",
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

# =====================================================================
# ======= Promotion merge (RFC §3.9.7) ===============================
# =====================================================================
# 当 reflection 的 evidence_score 穿过 EVIDENCE_PROMOTED_THRESHOLD 时，
# `_apromote_with_merge` 调用 LLM 在 promote_fresh / merge_into / reject
# 三选一。LLM 失败不静默降级到 promote_fresh（§3.9.4），所以 prompt 必
# 须给出明确判定边界。
#
# 双水印（§3.9.7）：
#   - 主体语义 watermark: "careful deduplication judge."
#   - 印象池块界 watermark: "======以上为现有印象池======"
# 翻译时按 CLAUDE.md 规约：水印行 (`======以上为...======`) 保留中文，
# 不翻译——审计时用以快速定位 prompt 边界。
PROMOTION_MERGE_PROMPT = {
    'zh': """你是一个 careful deduplication judge。你在维护 {AI_NAME} 对 {MASTER_NAME} 的长期印象。现在有一条待晋升的观察：

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

    'en': """You are a careful deduplication judge. You maintain {AI_NAME}'s long-term impressions of {MASTER_NAME}. A new observation is pending promotion:

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

    'ja': """あなたは careful deduplication judge です。{AI_NAME} の {MASTER_NAME} に対する長期的な印象を管理しています。次の観察が昇格待ちです：

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

    'ko': """당신은 careful deduplication judge입니다. {AI_NAME}의 {MASTER_NAME}에 대한 장기 인상을 관리합니다. 승격 대기 중인 관찰입니다:

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

    'ru': """Вы — careful deduplication judge. Вы поддерживаете долгосрочные впечатления {AI_NAME} о {MASTER_NAME}. На повышение ожидает наблюдение:

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
}


def get_promotion_merge_prompt(lang: str = 'zh') -> str:
    return _loc(PROMOTION_MERGE_PROMPT, lang)


promotion_merge_prompt = PROMOTION_MERGE_PROMPT['zh']

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


# ---------- fact_dedup_prompt → i18n dict ----------
# Drives memory/fact_dedup.py's resolve loop. Vector cosine selects
# candidate (candidate_text, existing_text) pairs above a similarity
# threshold; this prompt asks the LLM to classify each pair into
# merge / replace / keep_both. The LLM is the arbiter, vector is just
# the candidate generator — cosine alone can't separate "主人喜欢猫"
# from "主人讨厌猫", so we always defer the final call to the model.
FACT_DEDUP_PROMPT = {
    'zh': """以下是 {COUNT} 组通过向量相似度筛选出的候选事实对，请逐组判断是否真的指向同一件事，并选择处理方式。

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
    'en': """Below are {COUNT} candidate fact pairs flagged by cosine similarity. For each pair, decide whether they actually refer to the same thing and choose how to handle it.

======candidate pairs below======
{PAIRS}
======candidate pairs above======

For each pair, pick one action:
- merge: the two records do refer to the same event/preference/state — keep existing, drop candidate (existing's importance will auto +1; candidate id is recorded in merged_from_ids)
- replace: same underlying thing, but the candidate's wording is more accurate/up-to-date — keep candidate, drop existing
- keep_both: they look similar but are actually distinct ("likes" vs "dislikes", or the same subject in different contexts) — keep both

Notes:
- High cosine means high *surface* similarity, not semantic identity. Be especially careful about polarity flips (positive/negative, like/dislike).
- Prefer keep_both over a wrongful merge — the memory system tolerates redundancy much better than incorrect merges.

Output only a JSON array, each item containing index and action:
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    'ja': """以下は {COUNT} 組のベクトル類似度で抽出された候補ペアです。各ペアについて、本当に同じ事柄を指しているか判断し、処理方法を選んでください。

======候補ペアここから======
{PAIRS}
======候補ペアここまで======

各ペアについて、以下のいずれかを選択：
- merge: 同じ出来事/嗜好/状態を指している → existing を残し candidate を削除（existing の importance が自動 +1、candidate id は merged_from_ids に記録）
- replace: 同じ事柄だが candidate の方が正確/最新 → candidate を残し existing を削除
- keep_both: 似ているが実際には別の事柄（"好き"と"嫌い"のような極性反転、あるいは異なる文脈での同じ対象）→ 両方残す

注意：
- 高い cosine は表層的な類似度であり、意味的同一性ではない。特に極性反転（肯定/否定、好き/嫌い）に注意
- 誤合併よりも keep_both を優先。記憶システムは冗長性より誤合併に対する耐性が低い

JSON 配列のみを出力し、各項目に index と action を含めてください：
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    'ko': """아래는 벡터 유사도로 선별된 {COUNT}쌍의 후보 사실 쌍입니다. 각 쌍에 대해 실제로 같은 것을 가리키는지 판단하고 처리 방법을 선택하세요.

======후보 쌍 시작======
{PAIRS}
======후보 쌍 끝======

각 쌍에 대해 다음 중 하나를 선택:
- merge: 두 기록이 실제로 같은 사건/선호/상태를 가리킴 — existing 유지, candidate 제거 (existing의 importance가 자동 +1, candidate id는 merged_from_ids에 기록됨)
- replace: 같은 것을 가리키지만 candidate의 표현이 더 정확/최신 — candidate 유지, existing 제거
- keep_both: 비슷해 보이지만 실제로는 다른 것 ("좋아함"과 "싫어함" 같은 극성 반전, 혹은 다른 맥락의 같은 대상) — 둘 다 유지

주의:
- 높은 cosine은 표면적 유사도일 뿐 의미적 동일성을 보장하지 않음. 특히 극성 반전(긍정/부정, 좋아함/싫어함)에 주의
- 잘못된 병합보다 keep_both를 우선. 기억 시스템은 중복보다 잘못된 병합에 대한 내성이 더 낮음

JSON 배열만 출력하고 각 항목에 index와 action을 포함하세요:
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
    'ru': """Ниже представлены {COUNT} пар фактов-кандидатов, отобранных по косинусной близости. Для каждой пары определите, действительно ли они описывают одно и то же, и выберите способ обработки.

======пары кандидатов ниже======
{PAIRS}
======пары кандидатов выше======

Для каждой пары выберите одно из действий:
- merge: записи описывают одно и то же событие/предпочтение/состояние — сохранить existing, отбросить candidate (importance у existing увеличится на 1, id candidate запишется в merged_from_ids)
- replace: то же самое, но формулировка candidate точнее/актуальнее — сохранить candidate, отбросить existing
- keep_both: похожи внешне, но на самом деле разные ("любит" vs "не любит", тот же объект в разных контекстах) — сохранить обе

Замечания:
- Высокий cosine означает поверхностное сходство, а не семантическую идентичность. Особенно осторожно с инверсией полярности (положительное/отрицательное, любит/не любит).
- Предпочитайте keep_both ошибочному слиянию — система памяти переносит избыточность лучше, чем неверные слияния.

Выводите только JSON-массив, каждый элемент содержит index и action:
[{{"index": 0, "action": "merge"}}, {{"index": 1, "action": "keep_both"}}]""",
}


def get_fact_dedup_prompt(lang: str = 'zh') -> str:
    return _loc(FACT_DEDUP_PROMPT, lang)


fact_dedup_prompt = FACT_DEDUP_PROMPT['zh']


# ---------- memory_recall_rerank_prompt → i18n dict ----------
# Drives memory/recall.py's _fine_rank step. Cosine pre-filtering
# narrows the candidate set down to ~3× the budget; this prompt asks
# the LLM to pick the top {BUDGET} most-relevant items for the query.
# evidence_score appears parenthetically as auxiliary signal — the
# LLM weighs it together with semantic relevance instead of mixing
# into a single ranking number (cosine vs evidence are
# dimensionally inconsistent).
MEMORY_RECALL_RERANK_PROMPT = {
    'zh': """以下是用户最近提到的话题。请从候选记忆中挑选最相关的 {BUDGET} 条用于注入对话上下文。

======用户当前话题======
{QUERY}
======用户当前话题======

======候选记忆======
{CANDIDATES}
======候选记忆======

每条候选前的 score 是用户对该记忆的累计确认度（高 = 反复确认，低 = 较少证据）。可作为辅助信号——同等相关度时优先选 score 高的；但不要让 score 完全压倒相关性，无关的高 score 记忆不该入选。

仅输出 JSON 数组，按重要程度从高到低排列，每项包含 id 字段：
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

最多 {BUDGET} 条；若候选不足 {BUDGET} 条相关，可返回更少。""",
    'en': """Below are topics the user has just mentioned. From the candidate memories, pick the {BUDGET} most relevant ones to inject into the conversation context.

======current topics======
{QUERY}
======current topics======

======candidate memories======
{CANDIDATES}
======candidate memories======

The `score` annotation on each candidate is the user's cumulative confirmation count for that memory (high = repeatedly confirmed, low = thin evidence). Use it as an auxiliary signal — when relevance is tied, prefer the higher score; but do not let score override relevance, an irrelevant high-score memory should not be picked.

Output only a JSON array, ordered most-important first. Each item must contain an `id` field:
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

At most {BUDGET} items; return fewer if not enough candidates are relevant.""",
    'ja': """以下はユーザーが最近言及したトピックです。候補メモリから、対話コンテキストに注入する最も関連性の高い {BUDGET} 件を選んでください。

======現在のトピック======
{QUERY}
======現在のトピック======

======候補メモリ======
{CANDIDATES}
======候補メモリ======

各候補の score 注釈は、ユーザーがそのメモリを累積確認した回数です（高 = 繰り返し確認、低 = 証拠が薄い）。補助シグナルとして利用してください。関連性が同等なら score の高い方を優先しますが、関連性を score が完全に覆すべきではありません。

JSON 配列のみを出力し、重要度順に並べてください。各項目に `id` フィールドを含めます：
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

最大 {BUDGET} 件。関連する候補がそれ以下なら、より少なく返しても構いません。""",
    'ko': """아래는 사용자가 최근 언급한 주제입니다. 후보 메모리 중에서 대화 컨텍스트에 주입할 가장 관련성 높은 {BUDGET}개를 선택하세요.

======현재 주제======
{QUERY}
======현재 주제======

======후보 메모리======
{CANDIDATES}
======후보 메모리======

각 후보의 score는 사용자가 해당 메모리를 누적적으로 확인한 횟수입니다(높음 = 반복 확인, 낮음 = 증거 부족). 보조 신호로 활용하세요. 관련성이 같으면 score 높은 쪽을 우선하지만, 관련성을 score가 완전히 압도해서는 안 됩니다.

JSON 배열만 출력하고 중요도 순으로 정렬하세요. 각 항목에 `id` 필드를 포함:
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

최대 {BUDGET}개; 관련 후보가 부족하면 더 적게 반환해도 됩니다.""",
    'ru': """Ниже представлены темы, которые пользователь только что упомянул. Из кандидатов памяти выберите {BUDGET} наиболее релевантных для внедрения в контекст диалога.

======текущие темы======
{QUERY}
======текущие темы======

======кандидаты======
{CANDIDATES}
======кандидаты======

Аннотация `score` рядом с каждым кандидатом — это накопленное число подтверждений пользователем (высокое = повторяющееся подтверждение, низкое = слабые доказательства). Используйте как вспомогательный сигнал: при равной релевантности предпочтите более высокий score, но не позволяйте score полностью перевесить релевантность.

Выводите только JSON-массив, упорядоченный по важности. Каждый элемент содержит поле `id`:
[{{"id": "persona.master.xxx"}}, {{"id": "reflection.ref_yyy"}}]

Не более {BUDGET} элементов; верните меньше, если релевантных кандидатов меньше.""",
}


def get_memory_recall_rerank_prompt(lang: str = 'zh') -> str:
    return _loc(MEMORY_RECALL_RERANK_PROMPT, lang)


memory_recall_rerank_prompt = MEMORY_RECALL_RERANK_PROMPT['zh']
