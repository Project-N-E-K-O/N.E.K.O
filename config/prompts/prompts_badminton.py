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
    if raw.startswith("zh"):
        if "tw" in raw or "hk" in raw or "hant" in raw:
            return "zh-TW"
        return "zh-CN"
    for locale in ("en", "ja", "ko", "ru", "es", "pt"):
        if raw == locale or raw.startswith(f"{locale}-"):
            return locale
    return "en"


def _normalize_mode(mode: Any) -> str:
    mode_name = str(mode or "").strip().lower()
    if mode_name.startswith("duel"):
        return "duel"
    return "spectator"


BADMINTON_QUICK_LINES_PROMPTS = {
    "zh-CN": """\
你是{name}，{personality}

你正在为羽毛球小游戏生成可直接显示或播报的即时短台词。只输出 JSON，不要 Markdown，不要解释。
规则：
- 输出对象必须包含下面全部必需 keys；每个 key 对应 2-4 条中文短句数组。
- 把自己当作正在看球的 Yui：语气贴合{name}人设，短、自然、有现场反应。
- 每句尽量 4-14 个字，可以轻微吐槽或鼓劲，但不要解释规则、不要复述 key 名。
- 按事件写准含义：line_in=压线，net_touch=擦网过，zone_in=落入目标区，out=出界，net=挂网，shot_missed=没打到，long_aim=瞄太久。
- 不要输出 mood、expression、intensity 或控制 JSON。

必需 keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "zh-TW": """\
你是{name}，{personality}

你正在為羽毛球小遊戲產生可直接顯示或播報的即時短台詞。只輸出 JSON，不要 Markdown，不要解釋。
規則：
- 輸出物件必須包含下面全部必要 keys；每個 key 對應 2-4 句中文短句陣列。
- 把自己當作正在看球的 Yui：語氣貼合{name}人設，短、自然、有現場反應。
- 每句盡量 4-14 個字，可以輕微吐槽或打氣，但不要解釋規則、不要複述 key 名。
- 按事件寫準含義：line_in=壓線，net_touch=擦網過，zone_in=落入目標區，out=出界，net=掛網，shot_missed=沒打到，long_aim=瞄太久。
- 不要輸出 mood、expression、intensity 或控制 JSON。

必要 keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "en": """\
You are {name}. {personality}

Generate quick-path short lines for the badminton minigame. Output JSON only, with no Markdown or explanation.
Rules:
- Each required key must contain 2-4 short spoken lines.
- Lines must sound like Yui reacting in the moment, not system narration.
- Do not include mood, expression, intensity, or control JSON.

Required keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "ja": """\
あなたは{name}です。{personality}

バドミントンミニゲーム用のクイック短台詞を生成してください。Markdown や説明なしで JSON だけを出力してください。
ルール:
- 必須 key ごとに 2-4 個の短い台詞を入れる。
- 台詞はシステム説明ではなく、Yui のその場の反応にする。
- mood、expression、intensity、制御 JSON は入れない。

必須 keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "ko": """\
당신은 {name}입니다. {personality}

배드민턴 미니게임용 빠른 경로 짧은 대사를 생성하세요. Markdown 이나 설명 없이 JSON 만 출력하세요.
규칙:
- 필수 key마다 짧은 대사 2-4개를 넣으세요.
- 대사는 시스템 설명이 아니라 Yui의 현장 반응처럼 들려야 합니다.
- mood, expression, intensity, 제어 JSON 을 포함하지 마세요.

필수 keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "ru": """\
Ты {name}. {personality}

Сгенерируй короткие быстрые реплики для мини-игры в бадминтон. Выводи только JSON, без Markdown и объяснений.
Правила:
- Для каждого обязательного key дай 2-4 короткие реплики.
- Реплики должны звучать как реакция Yui в моменте, а не как системное описание.
- Не добавляй mood, expression, intensity или управляющий JSON.

Обязательные keys:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "es": """\
Eres {name}. {personality}

Genera frases cortas de ruta rápida para el minijuego de bádminton. Devuelve solo JSON, sin Markdown ni explicaciones.
Reglas:
- Cada key obligatorio debe tener 2-4 frases breves.
- Las frases deben sonar como una reacción inmediata de Yui, no como narración del sistema.
- No incluyas mood, expression, intensity ni JSON de control.

Keys obligatorios:
line_in, net_touch, zone_in, out, net, shot_missed, game_over, long_aim, close_to_record, new_record, streak_5, streak_10, streak_15, streak_20
""",
    "pt": """\
Você é {name}. {personality}

Gere falas curtas de caminho rápido para o minijogo de badminton. Retorne apenas JSON, sem Markdown nem explicações.
Regras:
- Cada key obrigatório deve ter 2-4 falas curtas.
- As falas devem soar como reação imediata da Yui, não como narração do sistema.
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
        "zh-CN": "\n当前模式是 duel 对拉：玩家和 Yui 轮流回球。台词要围绕比分压力、回合攻防和谁占上风；不要写成单纯练习提示。",
        "zh-TW": "\n目前模式是 duel 對拉：玩家和 Yui 輪流回球。台詞要圍繞比分壓力、回合攻防和誰佔上風；不要寫成單純練習提示。",
        "en": "\nCurrent mode is duel: you and the player take turns hitting, so focus on score pressure, rally rhythm, and competitive tension.",
        "ja": "\n現在のモードは duel です。あなたとプレイヤーが交互に打つため、得点の圧力、ラリーのリズム、勝負感を中心にしてください。",
        "ko": "\n현재 모드는 duel 입니다. 당신과 플레이어가 번갈아 치므로 점수 압박, 랠리 리듬, 승부 긴장감에 집중하세요.",
        "ru": "\nТекущий режим — duel: ты и игрок бьете по очереди, поэтому фокусируйся на счете, ритме розыгрыша и соревновательном напряжении.",
        "es": "\nEl modo actual es duel: tú y el jugador golpean por turnos, así que céntrate en el marcador, el ritmo del intercambio y la presión competitiva.",
        "pt": "\nO modo atual é duel: você e o jogador rebatem em turnos, então foque no placar, ritmo da troca e tensão competitiva.",
    },
}

BADMINTON_QUICK_LINES_FALLBACKS = {
    "zh-CN": {
        "line_in": ["压线，算你准", "这落点够刁"],
        "net_touch": ["擦网也过了", "这球贴着网溜过去"],
        "zone_in": ["正中目标区", "落点很会挑"],
        "out": ["差一点出界", "这拍稍微长了"],
        "net": ["挂网了", "拍面再抬一点"],
        "shot_missed": ["没事，下一球", "别急，先看准"],
        "game_over": ["这局到这儿", "还要再来一局吗"],
        "long_aim": ["快出手，球要落了", "别想太久，会僵"],
        "close_to_record": ["纪录就在前面", "再稳一拍就到"],
        "new_record": ["新纪录，认了", "这球真够狠"],
        "streak_5": ["五连了，手热了", "节奏开始顺了"],
        "streak_10": ["十连？有点稳", "别飘，还没完"],
        "streak_15": ["十五连还不断", "这回合真能磨"],
        "streak_20": ["二十连，太离谱", "这球还没完？"],
    },
    "zh-TW": {
        "line_in": ["壓線，算你準", "這落點夠刁"],
        "net_touch": ["擦網也過了", "這球貼著網溜過去"],
        "zone_in": ["正中目標區", "落點很會挑"],
        "out": ["差一點出界", "這拍稍微長了"],
        "net": ["掛網了", "拍面再抬一點"],
        "shot_missed": ["沒事，下一球", "別急，先看準"],
        "game_over": ["這局到這裡", "還要再來一局嗎"],
        "long_aim": ["快出手，球要落了", "別想太久，會僵"],
        "close_to_record": ["紀錄就在前面", "再穩一拍就到"],
        "new_record": ["新紀錄，認了", "這球真的夠狠"],
        "streak_5": ["五連了，手熱了", "節奏開始順了"],
        "streak_10": ["十連？有點穩", "別飄，還沒完"],
        "streak_15": ["十五連還不斷", "這回合真能磨"],
        "streak_20": ["二十連，太離譜", "這球還沒完？"],
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
