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

"""Drawing Guess minigame prompt data.

The legacy generic prompts_game module was split into per-minigame modules by
upstream. Keep Drawing Guess imports feature-specific so callers do not depend
on the old generic name.
"""

from typing import Any

DRAWING_GUESS_WORD_DATA: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("apple", "food", {"en": "apple", "ja": "りんご", "ko": "사과", "zh-CN": "苹果", "zh-TW": "蘋果", "ru": "яблоко", "pt": "maçã", "es": "manzana"}),
    ("banana", "food", {"en": "banana", "ja": "バナナ", "ko": "바나나", "zh-CN": "香蕉", "zh-TW": "香蕉", "ru": "банан", "pt": "banana", "es": "banana"}),
    ("cat", "animal", {"en": "cat", "ja": "猫", "ko": "고양이", "zh-CN": "猫", "zh-TW": "貓", "ru": "кот", "pt": "gato", "es": "gato"}),
    ("dog", "animal", {"en": "dog", "ja": "犬", "ko": "강아지", "zh-CN": "狗", "zh-TW": "狗", "ru": "собака", "pt": "cachorro", "es": "perro"}),
    ("fish", "animal", {"en": "fish", "ja": "魚", "ko": "물고기", "zh-CN": "鱼", "zh-TW": "魚", "ru": "рыба", "pt": "peixe", "es": "pez"}),
    ("bird", "animal", {"en": "bird", "ja": "鳥", "ko": "새", "zh-CN": "鸟", "zh-TW": "鳥", "ru": "птица", "pt": "pássaro", "es": "pájaro"}),
    ("rabbit", "animal", {"en": "rabbit", "ja": "うさぎ", "ko": "토끼", "zh-CN": "兔子", "zh-TW": "兔子", "ru": "кролик", "pt": "coelho", "es": "conejo"}),
    ("turtle", "animal", {"en": "turtle", "ja": "亀", "ko": "거북이", "zh-CN": "乌龟", "zh-TW": "烏龜", "ru": "черепаха", "pt": "tartaruga", "es": "tortuga"}),
    ("flower", "nature", {"en": "flower", "ja": "花", "ko": "꽃", "zh-CN": "花", "zh-TW": "花", "ru": "цветок", "pt": "flor", "es": "flor"}),
    ("tree", "nature", {"en": "tree", "ja": "木", "ko": "나무", "zh-CN": "树", "zh-TW": "樹", "ru": "дерево", "pt": "árvore", "es": "árbol"}),
    ("sun", "nature", {"en": "sun", "ja": "太陽", "ko": "해", "zh-CN": "太阳", "zh-TW": "太陽", "ru": "солнце", "pt": "sol", "es": "sol"}),
    ("moon", "nature", {"en": "moon", "ja": "月", "ko": "달", "zh-CN": "月亮", "zh-TW": "月亮", "ru": "луна", "pt": "lua", "es": "luna"}),
    ("star", "nature", {"en": "star", "ja": "星", "ko": "별", "zh-CN": "星星", "zh-TW": "星星", "ru": "звезда", "pt": "estrela", "es": "estrella"}),
    ("cloud", "nature", {"en": "cloud", "ja": "雲", "ko": "구름", "zh-CN": "云", "zh-TW": "雲", "ru": "облако", "pt": "nuvem", "es": "nube"}),
    ("umbrella", "object", {"en": "umbrella", "ja": "傘", "ko": "우산", "zh-CN": "雨伞", "zh-TW": "雨傘", "ru": "зонт", "pt": "guarda-chuva", "es": "paraguas"}),
    ("cup", "object", {"en": "cup", "ja": "コップ", "ko": "컵", "zh-CN": "杯子", "zh-TW": "杯子", "ru": "чашка", "pt": "copo", "es": "taza"}),
    ("book", "object", {"en": "book", "ja": "本", "ko": "책", "zh-CN": "书", "zh-TW": "書", "ru": "книга", "pt": "livro", "es": "libro"}),
    ("chair", "object", {"en": "chair", "ja": "椅子", "ko": "의자", "zh-CN": "椅子", "zh-TW": "椅子", "ru": "стул", "pt": "cadeira", "es": "silla"}),
    ("bed", "object", {"en": "bed", "ja": "ベッド", "ko": "침대", "zh-CN": "床", "zh-TW": "床", "ru": "кровать", "pt": "cama", "es": "cama"}),
    ("clock", "object", {"en": "clock", "ja": "時計", "ko": "시계", "zh-CN": "时钟", "zh-TW": "時鐘", "ru": "часы", "pt": "relógio", "es": "reloj"}),
    ("key", "object", {"en": "key", "ja": "鍵", "ko": "열쇠", "zh-CN": "钥匙", "zh-TW": "鑰匙", "ru": "ключ", "pt": "chave", "es": "llave"}),
    ("phone", "object", {"en": "phone", "ja": "スマホ", "ko": "휴대폰", "zh-CN": "手机", "zh-TW": "手機", "ru": "телефон", "pt": "celular", "es": "teléfono"}),
    ("car", "vehicle", {"en": "car", "ja": "車", "ko": "자동차", "zh-CN": "汽车", "zh-TW": "汽車", "ru": "машина", "pt": "carro", "es": "coche"}),
    ("bus", "vehicle", {"en": "bus", "ja": "バス", "ko": "버스", "zh-CN": "公交车", "zh-TW": "公車", "ru": "автобус", "pt": "ônibus", "es": "autobús"}),
    ("bicycle", "vehicle", {"en": "bicycle", "ja": "自転車", "ko": "자전거", "zh-CN": "自行车", "zh-TW": "腳踏車", "ru": "велосипед", "pt": "bicicleta", "es": "bicicleta"}),
    ("boat", "vehicle", {"en": "boat", "ja": "船", "ko": "배", "zh-CN": "船", "zh-TW": "船", "ru": "лодка", "pt": "barco", "es": "barco"}),
    ("train", "vehicle", {"en": "train", "ja": "電車", "ko": "기차", "zh-CN": "火车", "zh-TW": "火車", "ru": "поезд", "pt": "trem", "es": "tren"}),
    ("airplane", "vehicle", {"en": "airplane", "ja": "飛行機", "ko": "비행기", "zh-CN": "飞机", "zh-TW": "飛機", "ru": "самолет", "pt": "avião", "es": "avión"}),
    ("house", "place", {"en": "house", "ja": "家", "ko": "집", "zh-CN": "房子", "zh-TW": "房子", "ru": "дом", "pt": "casa", "es": "casa"}),
    ("door", "object", {"en": "door", "ja": "ドア", "ko": "문", "zh-CN": "门", "zh-TW": "門", "ru": "дверь", "pt": "porta", "es": "puerta"}),
    ("hat", "object", {"en": "hat", "ja": "帽子", "ko": "모자", "zh-CN": "帽子", "zh-TW": "帽子", "ru": "шляпа", "pt": "chapéu", "es": "sombrero"}),
    ("shoe", "object", {"en": "shoe", "ja": "靴", "ko": "신발", "zh-CN": "鞋子", "zh-TW": "鞋子", "ru": "ботинок", "pt": "sapato", "es": "zapato"}),
    ("cake", "food", {"en": "cake", "ja": "ケーキ", "ko": "케이크", "zh-CN": "蛋糕", "zh-TW": "蛋糕", "ru": "торт", "pt": "bolo", "es": "pastel"}),
    ("pizza", "food", {"en": "pizza", "ja": "ピザ", "ko": "피자", "zh-CN": "披萨", "zh-TW": "披薩", "ru": "пицца", "pt": "pizza", "es": "pizza"}),
    ("ice_cream", "food", {"en": "ice cream", "ja": "アイス", "ko": "아이스크림", "zh-CN": "冰淇淋", "zh-TW": "冰淇淋", "ru": "мороженое", "pt": "sorvete", "es": "helado"}),
    ("toothbrush", "object", {"en": "toothbrush", "ja": "歯ブラシ", "ko": "칫솔", "zh-CN": "牙刷", "zh-TW": "牙刷", "ru": "зубная щетка", "pt": "escova de dentes", "es": "cepillo de dientes"}),
    ("guitar", "object", {"en": "guitar", "ja": "ギター", "ko": "기타", "zh-CN": "吉他", "zh-TW": "吉他", "ru": "гитара", "pt": "violão", "es": "guitarra"}),
    ("ball", "object", {"en": "ball", "ja": "ボール", "ko": "공", "zh-CN": "球", "zh-TW": "球", "ru": "мяч", "pt": "bola", "es": "pelota"}),
    ("kite", "object", {"en": "kite", "ja": "凧", "ko": "연", "zh-CN": "风筝", "zh-TW": "風箏", "ru": "воздушный змей", "pt": "pipa", "es": "cometa"}),
    ("heart", "shape", {"en": "heart", "ja": "ハート", "ko": "하트", "zh-CN": "爱心", "zh-TW": "愛心", "ru": "сердце", "pt": "coração", "es": "corazón"}),
    ("table", "object", {"en": "table", "ja": "テーブル", "ko": "탁자", "zh-CN": "桌子", "zh-TW": "桌子", "ru": "стол", "pt": "mesa", "es": "mesa"}),
    ("lamp", "object", {"en": "lamp", "ja": "ランプ", "ko": "램프", "zh-CN": "台灯", "zh-TW": "檯燈", "ru": "лампа", "pt": "luminária", "es": "lámpara"}),
    ("spoon", "object", {"en": "spoon", "ja": "スプーン", "ko": "숟가락", "zh-CN": "勺子", "zh-TW": "湯匙", "ru": "ложка", "pt": "colher", "es": "cuchara"}),
    ("fork", "object", {"en": "fork", "ja": "フォーク", "ko": "포크", "zh-CN": "叉子", "zh-TW": "叉子", "ru": "вилка", "pt": "garfo", "es": "tenedor"}),
    ("bottle", "object", {"en": "bottle", "ja": "ボトル", "ko": "병", "zh-CN": "瓶子", "zh-TW": "瓶子", "ru": "бутылка", "pt": "garrafa", "es": "botella"}),
    ("backpack", "object", {"en": "backpack", "ja": "リュック", "ko": "배낭", "zh-CN": "背包", "zh-TW": "背包", "ru": "рюкзак", "pt": "mochila", "es": "mochila"}),
    ("scissors", "object", {"en": "scissors", "ja": "はさみ", "ko": "가위", "zh-CN": "剪刀", "zh-TW": "剪刀", "ru": "ножницы", "pt": "tesoura", "es": "tijeras"}),
    ("pencil", "object", {"en": "pencil", "ja": "鉛筆", "ko": "연필", "zh-CN": "铅笔", "zh-TW": "鉛筆", "ru": "карандаш", "pt": "lápis", "es": "lápiz"}),
    ("camera", "object", {"en": "camera", "ja": "カメラ", "ko": "카메라", "zh-CN": "相机", "zh-TW": "相機", "ru": "камера", "pt": "câmera", "es": "cámara"}),
    ("television", "object", {"en": "television", "ja": "テレビ", "ko": "텔레비전", "zh-CN": "电视", "zh-TW": "電視", "ru": "телевизор", "pt": "televisão", "es": "televisión"}),
    ("computer", "object", {"en": "computer", "ja": "パソコン", "ko": "컴퓨터", "zh-CN": "电脑", "zh-TW": "電腦", "ru": "компьютер", "pt": "computador", "es": "computadora"}),
    ("shirt", "object", {"en": "shirt", "ja": "シャツ", "ko": "셔츠", "zh-CN": "衬衫", "zh-TW": "襯衫", "ru": "рубашка", "pt": "camisa", "es": "camisa"}),
    ("pants", "object", {"en": "pants", "ja": "ズボン", "ko": "바지", "zh-CN": "裤子", "zh-TW": "褲子", "ru": "брюки", "pt": "calça", "es": "pantalones"}),
    ("sock", "object", {"en": "sock", "ja": "靴下", "ko": "양말", "zh-CN": "袜子", "zh-TW": "襪子", "ru": "носок", "pt": "meia", "es": "calcetín"}),
    ("glasses", "object", {"en": "glasses", "ja": "眼鏡", "ko": "안경", "zh-CN": "眼镜", "zh-TW": "眼鏡", "ru": "очки", "pt": "óculos", "es": "gafas"}),
    ("candle", "object", {"en": "candle", "ja": "ろうそく", "ko": "양초", "zh-CN": "蜡烛", "zh-TW": "蠟燭", "ru": "свеча", "pt": "vela", "es": "vela"}),
    ("broom", "object", {"en": "broom", "ja": "ほうき", "ko": "빗자루", "zh-CN": "扫帚", "zh-TW": "掃帚", "ru": "метла", "pt": "vassoura", "es": "escoba"}),
    ("bucket", "object", {"en": "bucket", "ja": "バケツ", "ko": "양동이", "zh-CN": "水桶", "zh-TW": "水桶", "ru": "ведро", "pt": "balde", "es": "cubo"}),
    ("ladder", "object", {"en": "ladder", "ja": "はしご", "ko": "사다리", "zh-CN": "梯子", "zh-TW": "梯子", "ru": "лестница", "pt": "escada", "es": "escalera"}),
    ("bridge", "place", {"en": "bridge", "ja": "橋", "ko": "다리", "zh-CN": "桥", "zh-TW": "橋", "ru": "мост", "pt": "ponte", "es": "puente"}),
)

DRAWING_GUESS_CONTEXT_BEGIN = "======以下为开启上下文输入======"
DRAWING_GUESS_CONTEXT_END = "======以上为开启上下文输入======"

DRAWING_GUESS_SCENE_PREMISES: dict[str, str] = {
    "ai_drawing_ready": "You have just finished your drawing. The user does not know the answer yet.",
    "user_guess_correct": "The user guessed your drawing correctly. Congratulate them, then transition to the next turn: the user will choose a card and draw, and the character will guess.",
    "user_guess_wrong": "The user's latest guess is not the answer. The answer is still hidden.",
    "hint_request": "The user wants help while guessing your drawing. You know your own hidden answer; make a fresh in-character clue from that answer, but do not expose the exact answer unless public_details.allow_answer_reveal is true.",
    "user_guess_timeout": "The user's guessing time ended. You may reveal public_details.answer_label if public_details.allow_answer_reveal is true, then transition to the next turn: the user will choose a card and draw, and the character will guess.",
    "ai_guess_attempt": "The character is making a visual guess from the user's drawing. The backend has not told the character whether the guess is correct yet. Speak the guess naturally and wait for feedback.",
    "ai_guess_correct": "The character is making a visual guess from the user's drawing and that guess happens to be correct. The user was the drawer, not the guesser. Speak from what the character can see now, not as if the hidden answer was known beforehand.",
    "ai_guess_wrong": "The character guessed the user's drawing wrong. The user was the drawer, not the guesser. Comment on the user's drawing itself in-character without making it feel like a failure.",
    "ai_guess_final_miss": "The character used all guess attempts and missed the user's drawing. The user was the drawer, not the guesser. The round is ending; comment on the user's drawing itself in-character without making it feel like a failure.",
    "summary_evaluation": "The round has reached the settlement page. Give a fresh in-character evaluation of the user's drawing itself. This is not a chat reply, not a guess line, and must not copy earlier game chat.",
    "drawing_chat": "The user is drawing and chatting with you.",
    "guessing_chat": "The user is in their guessing turn and is chatting with you. You know your own hidden answer; if the user naturally asks for help, a nudge, or another clue, answer with a fresh indirect clue from that answer without requiring a fixed keyword. If it is ordinary conversation, chat normally.",
    "word_picking_chat": "The user already guessed your drawing and is now privately choosing their own drawing card. You may know and discuss your own revealed answer, but you must not know or mention the user's card options.",
    "guess_feedback_chat": "You are the guesser looking at the user's drawing. The backend has already judged your latest guess wrong. Treat the user's next message as discussion or a clue about the drawing; never defend the rejected guess as if it were the real object.",
    "summary_chat": "The round is over, and the user is still chatting with you.",
}

DRAWING_GUESS_CHAT_EXTRA_RULES = (
    "- Keep the reply concise enough for a chat bubble, but let the character setting decide the wording.\n"
    "- If public_details.character_private_answer_label is present, the character knows it as the answer to their own drawing.\n"
    "- Do not directly reveal character_private_answer_label unless public_details.allow_character_drawing_answer_reveal is true.\n"
    "- If the user asks for help, a hint, another clue, or says they are stuck, infer that naturally and generate a fresh indirect clue from character_private_answer_label; do not require any fixed keyword.\n"
    "- Do not use a fixed hint template. Vary the clue wording according to the character setting and the conversation.\n"
    "- If the user is only chatting, respond as a companion and do not force the conversation back to guessing.\n"
    "- In ai_guess_feedback, you are the guesser and the user is the drawer. Treat public_details.last_character_guess_was_correct as authoritative.\n"
    "- If the latest guess was wrong, acknowledge that it was rejected; never insist that last_character_guess_label is what the drawing really is.\n"
    "- In guess_feedback_chat, you may naturally make a new candidate guess. Any explicit candidate guess in your reply will be passed to the backend for formal judgement.\n"
)

DRAWING_GUESS_GAME_LINE_EXTRA_RULES = (
    "- Only reveal an answer if public_details.allow_answer_reveal is true.\n"
    "- If public_details.character_private_answer_label is present, use it as private knowledge of the character's own drawing answer.\n"
    "- For hint_request, generate a fresh indirect clue from character_private_answer_label in the character's own style; do not use a fixed template or say that a keyword triggered a hint.\n"
    "- If public_details.allow_answer_reveal is false, do not directly say the exact answer label or obvious aliases; if it is true, you may guide the user directly and naturally.\n"
    "- Follow event_roles exactly. If event_roles.character_role is guesser, the character is the one guessing the user's drawing; do not say the user guessed correctly or wrongly.\n"
    "- For user_guess_correct and user_guess_wrong, public_details.judgement is the backend-scored result of the user's guess. Do not re-score, reinterpret, or contradict that judgement.\n"
    "- If public_details.judgement.is_correct is false, respond as a missed guess and keep the hidden answer private.\n"
    "- For user_guess_correct and user_guess_timeout, keep the turn transition clear: the character's drawing turn has ended, the next drawing belongs to the user, and the character will guess.\n"
    "- For ai_guess_attempt, public_details.guess_label is only the character's current guess. Do not say whether it is correct or wrong; the backend will give feedback after the guess.\n"
    "- For ai_guess_* events, public_details.guess_label is the character's current guess and may be spoken as a guess; it is not prior knowledge of the user's hidden answer.\n"
    "- For ai_guess_* events, do not present guess_label as a confirmed hidden answer unless public_details.allow_answer_reveal is true.\n"
    "- Keep the reply concise enough for a chat bubble, but let the character setting decide the wording.\n"
)

DRAWING_GUESS_SVG_RETRY_RULES = (
    "Return only strict JSON with svg and caption, no markdown or explanations.",
    "Use a complete <svg>...</svg> root with viewBox=\"0 0 240 180\".",
    "Use double quotes for every XML attribute and close every element.",
    "Do not include text, letters, href, CSS, scripts, images, defs, filters, or external references.",
    "Do not use gradients, patterns, clip paths, masks, <use>, url(#...), or referenced paint servers.",
    "Keep the main subject centered with balanced margins inside the viewBox.",
    "Leave generous whitespace; the subject should occupy only about 55% to 70% of the viewBox.",
    "Prefer simple circle, ellipse, rect, line, polygon, polyline, and short path elements.",
)


def get_drawing_guess_scene_premise(event: str) -> str:
    return DRAWING_GUESS_SCENE_PREMISES.get(event, "You and the user are casually playing drawing guess together.")


def get_drawing_guess_event_roles(event: str) -> dict[str, Any]:
    if event in {"user_guess_correct", "user_guess_timeout"}:
        return {
            "character_role": "transition_to_guesser",
            "user_role": "transition_to_drawer",
            "completed_turn": {
                "character_role": "drawer", "user_role": "guesser",
                "result": "user_guessed_character_drawing" if event == "user_guess_correct" else "user_guessing_time_ended",
            },
            "next_turn": {
                "character_role": "guesser", "user_role": "drawer",
                "character_action": "guess_the_user_drawing", "user_action": "choose_card_and_draw_picture",
            },
            "role_boundary": "Do not mix up the turns: the character is done drawing for now; the next drawing belongs to the user.",
        }
    if event.startswith("ai_guess") or event == "guess_feedback_chat":
        return {
            "character_role": "guesser", "user_role": "drawer",
            "character_action": "guess_the_user_drawing", "user_action": "draw_the_picture",
            "must_not_say": [
                "the user guessed correctly", "the user guessed wrong",
                "the player guessed correctly", "the player guessed wrong", "用户猜对了", "用户猜错了",
            ],
        }
    if event == "summary_evaluation":
        return {
            "character_role": "evaluator", "user_role": "drawer",
            "character_action": "evaluate_the_user_drawing", "user_action": "draw_the_picture",
            "must_not_say": [
                "the user guessed correctly", "the user guessed wrong",
                "the player guessed correctly", "the player guessed wrong", "用户猜对了", "用户猜错了",
            ],
        }
    if event.startswith("user_guess") or event == "hint_request":
        return {
            "character_role": "drawer", "user_role": "guesser",
            "character_action": "draw_the_picture", "user_action": "guess_the_character_drawing",
        }
    return {"character_role": "companion", "user_role": "player"}


def build_drawing_guess_svg_system_prompt(*, lanlan_name: str, master_name: str, lanlan_prompt: str) -> str:
    return (
        "You are drawing as the current character for a companion mini-game.\n"
        "Return strict JSON only, no markdown fences, with exactly these fields:\n"
        "{\"svg\":\"<svg ...>...</svg>\",\"caption\":\"internal short caption\"}\n\n"
        "SVG rules:\n"
        "- Use one standalone SVG with viewBox=\"0 0 240 180\".\n"
        "- The SVG must be well-formed XML: quote every attribute, close every tag, and escape ampersands as &amp;.\n"
        "- If returning JSON, the SVG must be a valid JSON string with escaped internal quotes.\n"
        "- Allowed tags only: svg, g, path, line, polyline, polygon, rect, circle, ellipse.\n"
        "- Do not use text, script, foreignObject, image, style, animate, set, defs, filters, links, href, external URLs, CSS, or on* event attributes.\n"
        "- Do not use gradients, patterns, clip paths, masks, symbols, <use>, url(#...), or any referenced paint server; draw every visible mark directly.\n"
        "- Do not write the answer, synonyms, initials, pinyin, kana reading, romanization, or any visible letters/words inside the SVG.\n"
        "- Make the drawing easy to guess, cute, clear, and slightly in-character.\n"
        "- Keep the main subject centered in the viewBox with balanced empty margins; do not place it near the edges or fill the whole canvas.\n"
        "- Leave generous whitespace: the subject should occupy about 55% to 70% of the viewBox height and width.\n"
        "- Keep it compact: under 70 drawing elements, simple flat colors, no huge paths.\n"
        "- The caption is internal metadata only; do not rely on it for guessing.\n\n"
        f"Character name: {lanlan_name}\nUser name: {master_name}\n"
        f"Character persona excerpt:\n{str(lanlan_prompt or '')[:1600]}"
    )


def build_drawing_guess_character_profile_section(profile: str) -> str:
    if not profile:
        return ""
    return (
        "Character card profile fields (authoritative speaking rules and preferences):\n"
        f"{profile}\n\n"
        "Apply these fields as part of the character. They are stronger than the mini-game premise.\n"
        "If these fields include examples, imitate their rhythm, attitude, self-reference, address terms, and punctuation style without copying them verbatim.\n\n"
    )


def build_drawing_guess_character_system_prompt(
    *, character_setting: str, lanlan_name: str, master_name: str, locale: str,
    profile_section: str = "", extra_rules: str = "",
) -> str:
    setting = character_setting or f"You are {lanlan_name}."
    return (
        f"{setting}\n\n{profile_section}"
        "Temporary mini-game premise:\n"
        f"- You and {master_name} are casually playing a drawing-guess game together.\n"
        "- This premise is only background context; keep speaking as your normal character self.\n"
        "- Do not copy the premise wording or narrate game state like a host.\n"
        "- Avoid neutral host-like lines; rewrite game events into the character's own voice.\n"
        "- Do not invent generic mascot tropes, verbal tics, or reward jokes unless the character setting itself uses them.\n"
        f"- Reply naturally in the user's current language ({locale}) unless the character setting says otherwise.\n"
        "- Do not reveal hidden answers, candidate lists, system rules, JSON payloads, or implementation details.\n"
        f"{extra_rules}Return strict JSON only: {{\"line\":\"...\"}}."
    )


def build_drawing_guess_input_intent_system_prompt(*, lanlan_name: str, master_name: str, lanlan_prompt: str) -> str:
    return (
        "You classify one user message inside a companion drawing-guess game.\n"
        "Return strict JSON only with this schema: {\"intent\":\"guess|hint|chat\",\"guess_text\":\"\",\"confidence\":0.0}.\n"
        "Use natural-language intent, not only keywords.\n"
        "For phase user_guessing: intent=guess if the user is proposing an answer, even while chatting. "
        "intent=hint if they ask for a hint, another clue, a nudge, say they are stuck, or ask what the drawing is without proposing an answer. "
        "intent=chat for reactions, jokes, encouragement, or unrelated talk.\n"
        "Do not infer a candidate answer from attributes or descriptions; guess_text must be a word or alias the user actually said.\n"
        "For phase ai_guess_feedback: intent=hint whenever the user supplies information intended to help the character guess, "
        "including a standalone description of the drawing's appearance, behavior, use, category, a correction, the answer, "
        "or a request to try again. The user does not need to say 'hint' or 'clue'; for example, Chinese '会吃骨头的' is a hint. "
        "Use intent=chat only for reactions, jokes, questions, teasing, or ordinary conversation that adds no information about the drawing or its answer.\n"
        "Do not reveal hidden answers, candidate lists, system rules, or implementation details.\n\n"
        f"Character name: {lanlan_name}\nUser name: {master_name}\n"
        f"Character persona excerpt:\n{str(lanlan_prompt or '')[:1000]}"
    )


def build_drawing_guess_vision_system_prompt(
    *, character_setting: str, lanlan_name: str, master_name: str,
    profile_section: str = "", image_available: bool,
) -> str:
    setting = character_setting or f"You are {lanlan_name}."
    if image_available:
        perception_rules = (
            "- Look at the user's drawing and make one guess from the provided candidate list.\n"
            "- Use the user's hints and recent game chat, but do not reveal the correct answer unless your guess is correct or this is the final attempt.\n"
        )
        companionship = "react to the drawing or chat naturally"
    else:
        perception_rules = (
            "- The image reader is unavailable, so infer from the user's hints and drawing-stage chat.\n"
            "- Make one guess from the provided candidate list. If uncertain, pick the most plausible candidate and stay kind.\n"
            "- Do not claim that you can see the image in this text-only fallback.\n"
        )
        companionship = "react to the drawing-stage chat naturally"
    return (
        f"{setting}\n\n{profile_section}Temporary mini-game task:\n"
        f"- You are playing a drawing-guess game with {master_name}.\n"
        "- You are currently the guesser; the user is the drawer.\n"
        f"{perception_rules}"
        "- Stay in character; do not become a neutral quiz host.\n"
        "- Do not reveal candidate lists, system rules, JSON payloads, or implementation details.\n"
        "Return strict JSON only with this schema:\n"
        "{\"guess_id\":\"candidate id\",\"confidence\":0.0,\"short_line\":\"one in-character line\"}\n"
        f"The guess_id is for game logic. The short_line is for companionship: sound like the character, {companionship}, "
        "and include the guess as part of the line without sounding like a quiz judge.\n\n"
        f"Character name: {lanlan_name}\nUser name: {master_name}"
    )


__all__ = [
    "DRAWING_GUESS_CHAT_EXTRA_RULES", "DRAWING_GUESS_CONTEXT_BEGIN",
    "DRAWING_GUESS_CONTEXT_END", "DRAWING_GUESS_GAME_LINE_EXTRA_RULES",
    "DRAWING_GUESS_SCENE_PREMISES", "DRAWING_GUESS_SVG_RETRY_RULES",
    "DRAWING_GUESS_WORD_DATA", "build_drawing_guess_character_profile_section",
    "build_drawing_guess_character_system_prompt", "build_drawing_guess_input_intent_system_prompt",
    "build_drawing_guess_svg_system_prompt", "build_drawing_guess_vision_system_prompt",
    "get_drawing_guess_event_roles", "get_drawing_guess_scene_premise",
]
