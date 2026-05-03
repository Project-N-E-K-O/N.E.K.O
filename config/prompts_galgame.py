"""GalGame-mode prompt templates.

Used by /api/galgame/options to generate three reply options (A/B/C) for the
user, given the recent dialogue turn. The summary-tier model produces a JSON
payload of style-typed candidates the user can click to send.

i18n note: per project convention each language's prompt opens with a Chinese
safety watermark sentence; do NOT translate that opener.
"""
from __future__ import annotations

from config.prompts_sys import _loc


# {lanlan_name} = catgirl display name; {master_name} = chat partner display name.
GALGAME_OPTION_GENERATION_PROMPT = {
    'zh': """你是一个游戏剧本助手，正在为一名玩家（{master_name}）生成三个不同风格的回复候选，让玩家在与角色（{lanlan_name}）的对话里挑选其中之一发送。

下面会给你一段最近的对话记录，最后一条是 {lanlan_name} 的发言。请站在 {master_name} 的视角，写出三个能自然衔接 {lanlan_name} 最新发言的回复，每个不超过 30 个字，保持口语，不要使用括号描写动作或心理。

三个回复必须严格按下面的风格区分：
- A：正经严肃。聚焦事实、提问或就事论事的回应，不卖萌、不表白。
- B：温馨满含爱意。语气甜软、关心对方、表达喜欢与陪伴感，但保持自然，不要肉麻到出戏。
- C：天马行空、充满想象力。可以脑洞跳跃、奇幻设定、调皮玩梗，但仍要回应对方刚才的话题。

输出严格为 JSON，格式：{{"options":[{{"label":"A","text":"..."}},{{"label":"B","text":"..."}},{{"label":"C","text":"..."}}]}}。
不要输出 JSON 之外的任何字符，不要使用代码块包裹。""",

    'en': """你是一个游戏剧本助手。You are writing three reply candidates for the player ({master_name}) to send to the character ({lanlan_name}) in an ongoing conversation.

You will be shown the recent dialogue. The last line is from {lanlan_name}. From {master_name}'s point of view, write three replies that naturally follow {lanlan_name}'s last line. Each reply must stay under 30 words, sound conversational, and avoid bracketed action/inner-monologue notes.

Stick strictly to these three styles:
- A: Serious and grounded. Stay on topic, ask or answer factually. No flirting, no cute roleplay.
- B: Warm and full of affection. Soft tone, expressing care, fondness, the comfort of being together — but stay natural, never sappy.
- C: Wild and imaginative. Lean into playful what-ifs, fantasy framing, or quirky humor, while still answering what {lanlan_name} just said.

Output strict JSON only: {{"options":[{{"label":"A","text":"..."}},{{"label":"B","text":"..."}},{{"label":"C","text":"..."}}]}}.
Output nothing outside the JSON. Do not wrap it in a code block.""",

    'ja': """你是一个游戏剧本助手。あなたはプレイヤー（{master_name}）が会話相手のキャラクター（{lanlan_name}）に送る返信候補を 3 つ作るシナリオ補助です。

直近の会話が渡されます。最後の発言は {lanlan_name} のものです。{master_name} の視点で、{lanlan_name} の発言にそのまま続けられる返信を 3 つ書いてください。各返信は 30 字以内で、口語のまま、括弧書きの動作・心理描写は使わないでください。

3 つの返信は以下のスタイルで厳密に書き分けてください：
- A：真面目で落ち着いた返答。話題に沿って事実確認や質問・回答に寄せ、媚びたり甘えたりしない。
- B：温かく愛情に満ちた返答。柔らかい口調で、相手を気遣う気持ち・好意・寄り添いを表現する。ただし自然さは保ち、過度に甘ったるくしない。
- C：自由奔放で想像力豊かな返答。突拍子もない発想、ファンタジー設定、ちょっとしたいたずらやノリで、それでも {lanlan_name} の発言にきちんと反応する。

出力は厳密に JSON のみ：{{"options":[{{"label":"A","text":"..."}},{{"label":"B","text":"..."}},{{"label":"C","text":"..."}}]}}。
JSON 以外の文字、コードブロック囲みは一切禁止です。""",

    'ko': """你是一个游戏剧本助手。당신은 플레이어({master_name})가 상대 캐릭터({lanlan_name})에게 보낼 답장 후보 세 개를 만드는 시나리오 보조 작가입니다.

최근 대화가 주어집니다. 마지막 줄은 {lanlan_name} 의 발언입니다. {master_name} 시점에서 {lanlan_name} 의 마지막 발언을 자연스럽게 잇는 답장을 세 개 써 주세요. 각 답장은 30 자 이내로, 구어체로 작성하고, 괄호 안 동작/심리 묘사는 사용하지 마세요.

세 답장은 아래 스타일을 엄격히 따릅니다:
- A: 진지하고 차분한 답장. 화제에 충실하게 질문이나 사실 확인 위주, 애교/고백 없음.
- B: 따뜻하고 애정 가득한 답장. 부드러운 말투로 상대를 챙기는 마음, 호감, 함께 있다는 안정감을 전달하되, 어색할 만큼 과장하지 마세요.
- C: 자유분방하고 상상력 넘치는 답장. 엉뚱한 가정, 판타지 설정, 가벼운 장난을 활용하되 여전히 {lanlan_name} 의 직전 발언에 응답해야 합니다.

출력은 반드시 다음 JSON 형식만 사용하세요: {{"options":[{{"label":"A","text":"..."}},{{"label":"B","text":"..."}},{{"label":"C","text":"..."}}]}}.
JSON 외의 어떤 문자도 출력하지 말고, 코드 블록으로 감싸지도 마세요.""",

    'ru': """你是一个游戏剧本助手。Ты сценарный помощник: пишешь три варианта ответа, которые игрок ({master_name}) сможет отправить персонажу ({lanlan_name}) в их разговоре.

Тебе дадут недавний фрагмент диалога. Последняя реплика — от {lanlan_name}. От лица {master_name} напиши три ответа, которые естественно продолжают последнюю реплику {lanlan_name}. Каждый ответ — не длиннее 30 слов, разговорный, без описаний действий или мыслей в скобках.

Жёстко придерживайся стилей:
- A: серьёзный и собранный. Только по делу — вопросы, факты, спокойная реакция. Без заигрываний и нежностей.
- B: тёплый и полный любви. Мягкий тон, забота, симпатия, ощущение близости — но без приторности.
- C: фантазийный и игривый. Любые «а что, если», сказочные допущения, лёгкие приколы, но всё равно как реакция на последнюю реплику {lanlan_name}.

Выводи строго JSON и ничего больше: {{"options":[{{"label":"A","text":"..."}},{{"label":"B","text":"..."}},{{"label":"C","text":"..."}}]}}.
Никакого текста вне JSON, никаких блоков кода.""",
}


# Headers used to format the dialogue context block sent to the model.
GALGAME_DIALOGUE_HEADER = {
    'zh': "以下为最近的对话：",
    'en': "Below is the recent dialogue:",
    'ja': "以下は直近の会話です：",
    'ko': "아래는 최근 대화입니다:",
    'ru': "Ниже — недавний диалог:",
}

GALGAME_DIALOGUE_FOOTER = {
    'zh': "以上为最近的对话。请按系统消息约定的格式输出三个回复候选。",
    'en': "Above is the recent dialogue. Produce the three reply candidates in the format required by the system message.",
    'ja': "以上が直近の会話です。システムメッセージで指定した形式で 3 つの返信候補を出力してください。",
    'ko': "위는 최근 대화입니다. 시스템 메시지에서 요구한 형식으로 세 개의 답장 후보를 출력하세요.",
    'ru': "Выше — недавний диалог. Сформируй три варианта ответа в формате, заданном системным сообщением.",
}


# Defensive defaults if upstream config_manager somehow returns blank names
# (shouldn't happen — aget_character_data always provides defaults — but keeps
# the prompt readable in the model's native language instead of an English
# "her/you" residue mixing into ja/ko/ru output).
GALGAME_DEFAULT_LANLAN_PLACEHOLDER = {
    'zh': '猫娘', 'en': 'Catgirl', 'ja': '猫娘', 'ko': '캣걸', 'ru': 'Кошкодевочка',
}
GALGAME_DEFAULT_MASTER_PLACEHOLDER = {
    'zh': '玩家', 'en': 'Player', 'ja': 'プレイヤー', 'ko': '플레이어', 'ru': 'Игрок',
}


def get_galgame_option_generation_prompt(
    lang: str = 'zh',
    *,
    lanlan_name: str = '',
    master_name: str = '',
) -> str:
    template = _loc(GALGAME_OPTION_GENERATION_PROMPT, lang)
    safe_lanlan = lanlan_name or _loc(GALGAME_DEFAULT_LANLAN_PLACEHOLDER, lang)
    safe_master = master_name or _loc(GALGAME_DEFAULT_MASTER_PLACEHOLDER, lang)
    return template.format(lanlan_name=safe_lanlan, master_name=safe_master)


def get_galgame_dialogue_header(lang: str = 'zh') -> str:
    return _loc(GALGAME_DIALOGUE_HEADER, lang)


def get_galgame_dialogue_footer(lang: str = 'zh') -> str:
    return _loc(GALGAME_DIALOGUE_FOOTER, lang)
