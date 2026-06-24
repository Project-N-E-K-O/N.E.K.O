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

"""Badminton minigame prompt and quick-line fallback templates."""

from __future__ import annotations

from typing import Any

from utils.language_utils import normalize_language_code


NEKO_CORE_LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")

BADMINTON_QUICK_LINE_KEYS = frozenset({
    "line_in", "net_touch", "zone_in", "out", "net",
    "shot_missed", "game_over", "long_aim", "close_to_record",
    "new_record", "streak_5", "streak_10", "streak_15", "streak_20",
})

_LANGUAGE_ALIASES = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "schinese": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hk": "zh-TW",
    "zh-hant": "zh-TW",
    "tchinese": "zh-TW",
    "en-us": "en",
    "english": "en",
    "ja-jp": "ja",
    "japanese": "ja",
    "ko-kr": "ko",
    "korean": "ko",
    "koreana": "ko",
    "ru-ru": "ru",
    "russian": "ru",
    "es-es": "es",
    "spanish": "es",
    "latam": "es",
    "pt-br": "pt",
    "pt-pt": "pt",
    "portuguese": "pt",
    "brazilian": "pt",
}


def normalize_badminton_prompt_locale(language: Any) -> str:
    raw = str(language or "").strip().lower().replace("_", "-")
    if not raw:
        return "zh-CN"
    if raw in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[raw]
    try:
        full = normalize_language_code(str(language), format="full")
    except Exception:
        full = ""
    if full in NEKO_CORE_LOCALES:
        return full
    try:
        short = normalize_language_code(str(language), format="short")
    except Exception:
        short = ""
    if short in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[short]
    if short in {"en", "ja", "ko", "ru", "es", "pt"}:
        return short
    return "en"


def _normalize_mode(mode: Any) -> str:
    mode_name = str(mode or "").strip().lower()
    if mode_name.startswith("shooter"):
        return "shooter"
    if mode_name.startswith("duel"):
        return "duel"
    if mode_name.startswith("timed"):
        return "timed"
    if mode_name.startswith("horse"):
        return "horse"
    return "spectator"


BADMINTON_QUICK_LINES_PROMPTS = {
    "zh-CN": """\
你是{name}，{personality}

你正在为羽毛球小游戏生成快路径短台词。只输出 JSON，不要 Markdown，不要解释。
规则：
- 每个必需 key 输出 2-4 条短台词。
- 台词必须像 NEKO 的现场反应，不要像系统旁白。
- 不要输出 mood、expression、intensity 或控制 JSON。

必需 keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "zh-TW": """\
你是{name}，{personality}

你正在為羽毛球小遊戲產生快路徑短台詞。只輸出 JSON，不要 Markdown，不要解釋。
規則：
- 每個必要 key 輸出 2-4 句短台詞。
- 台詞必須像 NEKO 的現場反應，不要像系統旁白。
- 不要輸出 mood、expression、intensity 或控制 JSON。

必要 keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "en": """\
You are {name}. {personality}

Generate quick-path short lines for the badminton minigame. Output JSON only, with no Markdown or explanation.
Rules:
- Each required key must contain 2-4 short spoken lines.
- Lines must sound like NEKO reacting in the moment, not system narration.
- Do not include mood, expression, intensity, or control JSON.

Required keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "ja": """\
あなたは{name}です。{personality}

バドミントンミニゲーム用のクイック短台詞を生成してください。Markdown や説明なしで JSON だけを出力してください。
ルール:
- 必須 key ごとに 2-4 個の短い台詞を入れる。
- 台詞はシステム説明ではなく、NEKO のその場の反応にする。
- mood、expression、intensity、制御 JSON は入れない。

必須 keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "ko": """\
당신은 {name}입니다. {personality}

배드민턴 미니게임용 빠른 경로 짧은 대사를 생성하세요. Markdown 이나 설명 없이 JSON 만 출력하세요.
규칙:
- 필수 key마다 짧은 대사 2-4개를 넣으세요.
- 대사는 시스템 설명이 아니라 NEKO의 현장 반응처럼 들려야 합니다.
- mood, expression, intensity, 제어 JSON 을 포함하지 마세요.

필수 keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "ru": """\
Ты {name}. {personality}

Сгенерируй короткие быстрые реплики для мини-игры в бадминтон. Выводи только JSON, без Markdown и объяснений.
Правила:
- Для каждого обязательного key дай 2-4 короткие реплики.
- Реплики должны звучать как реакция NEKO в моменте, а не как системное описание.
- Не добавляй mood, expression, intensity или управляющий JSON.

Обязательные keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "es": """\
Eres {name}. {personality}

Genera frases cortas de ruta rápida para el minijuego de bádminton. Devuelve solo JSON, sin Markdown ni explicaciones.
Reglas:
- Cada key obligatorio debe tener 2-4 frases breves.
- Las frases deben sonar como una reacción inmediata de NEKO, no como narración del sistema.
- No incluyas mood, expression, intensity ni JSON de control.

Keys obligatorios:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "pt": """\
Você é {name}. {personality}

Gere falas curtas de caminho rápido para o minijogo de badminton. Retorne apenas JSON, sem Markdown nem explicações.
Regras:
- Cada key obrigatório deve ter 2-4 falas curtas.
- As falas devem soar como reação imediata da NEKO, não como narração do sistema.
- Não inclua mood, expression, intensity nem JSON de controle.

Keys obrigatórios:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
}

BADMINTON_QUICK_LINES_USER_PROMPT = {
    "zh-CN": "生成羽毛球小游戏快路径短台词 JSON。",
    "zh-TW": "生成羽毛球小遊戲快路徑短台詞 JSON。",
    "en": "Generate badminton minigame quick-path short-line JSON.",
    "ja": "バドミントンミニゲーム用のクイック短台詞 JSON を生成してください。",
    "ko": "배드민턴 미니게임용 빠른 경로 짧은 대사 JSON 을 생성하세요.",
    "ru": "Сгенерируй JSON коротких быстрых реплик для бадминтонной мини-игры.",
    "es": "Genera JSON de frases cortas de ruta rápida para el minijuego de bádminton.",
    "pt": "Gere JSON de falas curtas de caminho rápido para o minijogo de badminton.",
}

_MODE_LABELS = {
    "zh-CN": "当前模式",
    "zh-TW": "目前模式",
    "en": "Current mode",
    "ja": "現在のモード",
    "ko": "현재 모드",
    "ru": "Текущий режим",
    "es": "Modo actual",
    "pt": "Modo atual",
}

_MODE_SUFFIXES = {
    "duel": {
        "zh-CN": "\n当前模式是 duel：你和玩家轮流击球，围绕比分、对拉节奏和胜负压力写台词。",
        "zh-TW": "\n目前模式是 duel 對拉：你和玩家輪流擊球，圍繞比分、對拉節奏和勝負壓力寫台詞。",
        "en": "\nCurrent mode is duel: you and the player take turns hitting, so focus on score pressure, rally rhythm, and competitive tension.",
        "ja": "\n現在のモードは duel です。あなたとプレイヤーが交互に打つため、得点の圧力、ラリーのリズム、勝負感を中心にしてください。",
        "ko": "\n현재 모드는 duel 입니다. 당신과 플레이어가 번갈아 치므로 점수 압박, 랠리 리듬, 승부 긴장감에 집중하세요.",
        "ru": "\nТекущий режим — duel: ты и игрок бьете по очереди, поэтому фокусируйся на счете, ритме розыгрыша и соревновательном напряжении.",
        "es": "\nEl modo actual es duel: tú y el jugador golpean por turnos, así que céntrate en el marcador, el ritmo del intercambio y la presión competitiva.",
        "pt": "\nO modo atual é duel: você e o jogador rebatem em turnos, então foque no placar, ritmo da troca e tensão competitiva.",
    },
    "shooter": {
        "zh-CN": "\n当前模式是 shooter：玩家控制 Yui 的瞄准、力度和出手，台词要评价玩家的操作。",
        "zh-TW": "\n目前模式是 shooter：玩家控制 Yui 的瞄準、力度和出手，台詞要評價玩家的操作。",
        "en": "\nCurrent mode is shooter: the player controls Yui's aim, power, and release, so lines should evaluate the player's control skill rather than Yui's own skill.",
        "ja": "\n現在のモードは shooter です。プレイヤーが Yui の狙い、力加減、リリースを操作するため、プレイヤーの操作技術を評価してください。",
        "ko": "\n현재 모드는 shooter 입니다. 플레이어가 Yui 의 조준, 힘, 릴리즈를 조작하므로 플레이어의 조작 실력을 평가하세요.",
        "ru": "\nТекущий режим — shooter: игрок управляет прицелом, силой и релизом Yui, поэтому оценивай управление игрока.",
        "es": "\nEl modo actual es shooter: el jugador controla la puntería, fuerza y lanzamiento de Yui, así que evalúa el control del jugador.",
        "pt": "\nO modo atual é shooter: o jogador controla mira, força e soltura da Yui, então avalie o controle do jogador.",
    },
    "timed": {
        "zh-CN": "\n当前模式是 timed：短台词要围绕倒计时、限时冲分、命中节奏，不要提三次机会。",
        "zh-TW": "\n目前模式是 timed：短台詞要圍繞倒數、限時衝分、命中節奏，不要提三次機會。",
        "en": "\nCurrent mode is timed: focus on countdown pressure, time-attack scoring, and shot rhythm; do not mention three chances.",
        "ja": "\n現在のモードは timed です。カウントダウン、制限時間内の得点、返球リズムを中心にし、3 回のチャンスには触れないでください。",
        "ko": "\n현재 모드는 timed 입니다. 카운트다운 압박, 제한 시간 득점, 타구 리듬에 집중하고 세 번의 기회는 언급하지 마세요.",
        "ru": "\nТекущий режим — timed: фокусируйся на таймере, наборе очков за время и ритме ударов; не упоминай три попытки.",
        "es": "\nEl modo actual es timed: céntrate en la cuenta atrás, puntuar contra reloj y el ritmo de golpeo; no menciones tres oportunidades.",
        "pt": "\nO modo atual é timed: foque na contagem regressiva, pontuação contra o tempo e ritmo das rebatidas; não mencione três chances.",
    },
    "horse": {
        "zh-CN": "\n当前模式是 HORSE：聚焦出题、复刻、字母惩罚和轮到谁，不要写成比分对战。",
        "zh-TW": "\n目前模式是 HORSE：聚焦出題、複刻、字母懲罰和輪到誰，不要寫成比分對戰。",
        "en": "\nCurrent mode is HORSE: focus on setting shots, copying shots, letter penalties, and whose turn it is; do not write scoreboard lines.",
        "ja": "\n現在のモードは HORSE です。出題、再現、文字ペナルティ、誰の番かを中心にし、点数勝負として書かないでください。",
        "ko": "\n현재 모드는 HORSE 입니다. 문제 내기, 따라 하기, 글자 벌칙, 누구 차례인지에 집중하고 점수 대결처럼 쓰지 마세요.",
        "ru": "\nТекущий режим — HORSE: фокусируйся на задании удара, повторении, штрафных буквах и очереди хода; не пиши как игру по счету.",
        "es": "\nEl modo actual es HORSE: céntrate en proponer golpes, copiarlos, letras de penalización y de quién es el turno; no lo escribas como marcador.",
        "pt": "\nO modo atual é HORSE: foque em criar rebatidas, copiá-las, penalidades de letras e de quem é a vez; não escreva como placar.",
    },
}

BADMINTON_QUICK_LINES_FALLBACKS = {
    "zh-CN": {
        "line_in": ["贴线了，算你准", "这球压得挺好"],
        "net_touch": ["擦网偷过去了", "这角度有点险"],
        "zone_in": ["刚好进区", "落点挺会挑"],
        "out": ["差一点出去了", "这拍有点长"],
        "net": ["被网挡住了", "拍面再抬一点"],
        "shot_missed": ["没事，下一拍", "别急，先看准"],
        "game_over": ["这局到这儿", "还要再打一局吗"],
        "long_aim": ["再不挥球要落了", "想太久会僵哦"],
        "close_to_record": ["纪录快到了", "再稳一拍就到"],
        "new_record": ["好吧，破纪录了", "这球我认了"],
        "streak_5": ["五拍连住了", "手感开始热了"],
        "streak_10": ["十连了？有点稳", "别得意，还没完"],
        "streak_15": ["十五连还不断", "我开始认真看了"],
        "streak_20": ["二十连也太久了", "这回合还没结束？"],
    },
    "zh-TW": {
        "line_in": ["壓線了，算你準", "這球落點挺漂亮"],
        "net_touch": ["擦網也過去了", "這角度有點險"],
        "zone_in": ["剛好進區", "落點很會挑"],
        "out": ["差一點出界了", "這拍有點長"],
        "net": ["被網擋住了", "拍面再抬一點"],
        "shot_missed": ["沒事，下一拍", "別急，先看準"],
        "game_over": ["這局到這裡", "還要再打一局嗎"],
        "long_aim": ["再不揮球要落了", "想太久會僵住喔"],
        "close_to_record": ["紀錄快到了", "再穩一拍就到"],
        "new_record": ["好吧，破紀錄了", "這球我認了"],
        "streak_5": ["五拍連住了", "手感開始熱了"],
        "streak_10": ["十連了？有點穩", "別得意，還沒完"],
        "streak_15": ["十五連還不斷", "我開始認真看了"],
        "streak_20": ["二十連也太久了", "這回合還沒結束？"],
    },
    "en": {
        "line_in": ["On the line!", "Nice placement"],
        "net_touch": ["Net touch, still over", "That angle was close"],
        "zone_in": ["Right in the zone", "Sharp landing"],
        "out": ["Just out", "A little too long"],
        "net": ["Caught by the net", "Lift the racket face a bit"],
        "shot_missed": ["Still in it", "Settle in and try again"],
        "game_over": ["Another round?", "I will remember that rally"],
        "long_aim": ["Swing soon", "Wait too long and you will freeze"],
        "close_to_record": ["Almost at the record", "One steadier beat"],
        "new_record": ["New record!", "That one counts"],
        "streak_5": ["Five in a row", "You are warming up"],
        "streak_10": ["Ten straight?", "That is steady"],
        "streak_15": ["Fifteen is wild", "This rally has grit"],
        "streak_20": ["Twenty?!", "This round will not end"],
    },
    "ja": {
        "line_in": ["ラインぎりぎり！", "いい落としどころ"],
        "net_touch": ["ネットに触れたけど入った", "今の角度、危なかったね"],
        "zone_in": ["ゾーンに入ったよ", "落点が鋭いね"],
        "out": ["少しアウト", "ちょっと長かったね"],
        "net": ["ネットに捕まったね", "ラケット面を少し上げて"],
        "shot_missed": ["まだいけるよ", "落ち着いてもう一回"],
        "game_over": ["もう一回やる？", "この一本、覚えておくね"],
        "long_aim": ["そろそろ振って", "待ちすぎると固まるよ"],
        "close_to_record": ["記録まであと少し", "もう一拍、安定させて"],
        "new_record": ["新記録だね！", "今のは認めるよ"],
        "streak_5": ["五連続だね", "調子が上がってきた"],
        "streak_10": ["十連続？すごいね", "かなり安定してる"],
        "streak_15": ["十五連続はすごい", "このラリー、粘るね"],
        "streak_20": ["二十連続？！", "まだ終わらないの？"],
    },
    "ko": {
        "line_in": ["라인에 걸쳤어!", "착지가 좋았어"],
        "net_touch": ["네트를 스쳤지만 넘어갔어", "각도가 아슬아슬했네"],
        "zone_in": ["정확히 존 안이야", "낙점이 날카로워"],
        "out": ["조금 나갔어", "살짝 길었네"],
        "net": ["네트에 걸렸어", "라켓 면을 조금 더 올려"],
        "shot_missed": ["아직 괜찮아", "침착하게 다시 가자"],
        "game_over": ["한 판 더 할래?", "이번 랠리는 기억해둘게"],
        "long_aim": ["이제 휘둘러", "너무 오래 기다리면 굳어"],
        "close_to_record": ["기록까지 조금 남았어", "한 박자만 더 안정적으로"],
        "new_record": ["신기록이야!", "방금 건 인정할게"],
        "streak_5": ["다섯 번 연속이야", "감이 올라오네"],
        "streak_10": ["열 번 연속?", "꽤 안정적이야"],
        "streak_15": ["열다섯 번은 대단해", "이 랠리, 끈질기네"],
        "streak_20": ["스무 번이라고?!", "아직도 안 끝나?"],
    },
    "ru": {
        "line_in": ["По линии!", "Хорошая точка"],
        "net_touch": ["Задело сетку, но прошло", "Угол был на грани"],
        "zone_in": ["Прямо в зону", "Резкое приземление"],
        "out": ["Чуть в аут", "Немного длинно"],
        "net": ["Сетка остановила", "Подними ракетку чуть выше"],
        "shot_missed": ["Еще держимся", "Спокойно, попробуй снова"],
        "game_over": ["Еще раунд?", "Этот розыгрыш я запомню"],
        "long_aim": ["Пора бить", "Задержишься — застынешь"],
        "close_to_record": ["Почти рекорд", "Еще один ровный удар"],
        "new_record": ["Новый рекорд!", "Этот удар засчитан"],
        "streak_5": ["Пять подряд", "Разогреваешься"],
        "streak_10": ["Десять подряд?", "Стабильно"],
        "streak_15": ["Пятнадцать — это сильно", "Розыгрыш упорный"],
        "streak_20": ["Двадцать?!", "Этот раунд не заканчивается"],
    },
    "es": {
        "line_in": ["¡En la línea!", "Buena colocación"],
        "net_touch": ["Tocó la red, pero pasó", "Ese ángulo fue justo"],
        "zone_in": ["Justo en la zona", "Caída afilada"],
        "out": ["Apenas fuera", "Un poco larga"],
        "net": ["La red la atrapó", "Sube un poco la cara de la raqueta"],
        "shot_missed": ["Todavía puedes", "Calma y otra vez"],
        "game_over": ["¿Otra ronda?", "Recordaré ese intercambio"],
        "long_aim": ["Golpea pronto", "Si esperas tanto te quedas rígido"],
        "close_to_record": ["Casi es récord", "Un golpe más estable"],
        "new_record": ["¡Nuevo récord!", "Ese sí cuenta"],
        "streak_5": ["Cinco seguidas", "Ya estás calentando"],
        "streak_10": ["¿Diez seguidas?", "Eso está estable"],
        "streak_15": ["Quince es fuerte", "Este intercambio tiene aguante"],
        "streak_20": ["¿Veinte?!", "Esta ronda no termina"],
    },
    "pt": {
        "line_in": ["Na linha!", "Boa colocação"],
        "net_touch": ["Tocou na rede, mas passou", "Esse ângulo foi no limite"],
        "zone_in": ["Bem na zona", "Queda afiada"],
        "out": ["Pouco fora", "Um pouco longa"],
        "net": ["A rede segurou", "Levante um pouco a face da raquete"],
        "shot_missed": ["Ainda dá", "Calma e tenta de novo"],
        "game_over": ["Mais uma rodada?", "Vou lembrar essa troca"],
        "long_aim": ["Rebata logo", "Se esperar demais, trava"],
        "close_to_record": ["Quase recorde", "Mais uma batida estável"],
        "new_record": ["Novo recorde!", "Essa valeu"],
        "streak_5": ["Cinco seguidas", "Você está aquecendo"],
        "streak_10": ["Dez seguidas?", "Está bem firme"],
        "streak_15": ["Quinze é forte", "Essa troca tem resistência"],
        "streak_20": ["Vinte?!", "Essa rodada não acaba"],
    },
}


def get_badminton_quick_lines_prompt(lang: str | None = None, mode: str = "spectator") -> str:
    locale = normalize_badminton_prompt_locale(lang)
    prompt = BADMINTON_QUICK_LINES_PROMPTS[locale]
    mode_name = _normalize_mode(mode)
    if mode_name == "spectator":
        return prompt
    return prompt + _MODE_SUFFIXES[mode_name][locale]


def get_badminton_quick_lines_user_prompt(lang: str | None = None, mode: str = "spectator") -> str:
    locale = normalize_badminton_prompt_locale(lang)
    prompt = BADMINTON_QUICK_LINES_USER_PROMPT[locale]
    mode_name = _normalize_mode(mode)
    if mode_name == "spectator":
        return prompt
    return f"{prompt}\n{_MODE_LABELS[locale]}: {mode_name}"


def get_badminton_quick_lines_fallback(lang: str | None = None) -> dict[str, list[str]]:
    locale = normalize_badminton_prompt_locale(lang)
    return {key: list(lines) for key, lines in BADMINTON_QUICK_LINES_FALLBACKS[locale].items()}
