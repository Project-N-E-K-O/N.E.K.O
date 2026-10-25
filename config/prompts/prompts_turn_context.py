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

"""Frames of the passive turn context blocks (``main_logic.turn_context``).

Two user-role blocks follow the user's message in a text reply's requests:

* the knowledge block stays in the session's requests after this turn, so its
  wording must read right at any later point of the conversation (no "reply
  now" line);
* the turn block (expression material, related past moments) rides this
  turn's requests only and ends by pointing back at the user's message.

Each frame is a below/above pair, translated per locale (prose frames).
"""

# ---- knowledge block (kept for the session) ----

TURN_CONTEXT_KNOWLEDGE_BEGIN = {
    "zh": "======以下为参考知识======",
    "zh-TW": "======以下為參考知識======",
    "en": "======Reference knowledge below======",
    "ja": "======以下は参考知識======",
    "ko": "======아래는 참고 지식======",
    "ru": "======Ниже справочные знания======",
    "es": "======Abajo, conocimiento de referencia======",
    "pt": "======Abaixo, conhecimento de referência======",
}

TURN_CONTEXT_KNOWLEDGE_END = {
    "zh": "======以上为参考知识======",
    "zh-TW": "======以上為參考知識======",
    "en": "======Reference knowledge above======",
    "ja": "======以上は参考知識======",
    "ko": "======위는 참고 지식======",
    "ru": "======Выше справочные знания======",
    "es": "======Arriba, conocimiento de referencia======",
    "pt": "======Acima, conhecimento de referência======",
}

TURN_CONTEXT_KNOWLEDGE_NOTE = {
    "zh": (
        "系统根据用户上面这句话自动附上的参考，用户看不到，也不是用户说的话。"
        "它是按字面匹配找来的，可能和当前话题无关：无关的直接忽略，不要提起，也不要转述。"
        "不要提到这段参考本身。"
    ),
    "zh-TW": (
        "系統根據使用者上面這句話自動附上的參考，使用者看不到，也不是使用者說的話。"
        "它是按字面比對找來的，可能和目前話題無關：無關的直接忽略，不要提起，也不要轉述。"
        "不要提到這段參考本身。"
    ),
    "en": (
        "Attached automatically by the system for the user's message above. The user cannot "
        "see it, and it is not something the user said. It was found by literal matching and "
        "may have nothing to do with the current topic: ignore whatever is unrelated, and do "
        "not bring it up or paraphrase it. Do not mention this reference itself."
    ),
    "ja": (
        "ユーザーの上のメッセージに合わせてシステムが自動で添えた参考です。ユーザーには見えず、"
        "ユーザーの発言でもありません。文字の一致で見つけたもので、今の話題と関係ないこともあります。"
        "関係のないものは無視し、話題にしたり言い換えて伝えたりしないでください。"
        "この参考そのものにも触れないでください。"
    ),
    "ko": (
        "사용자의 위 메시지에 맞춰 시스템이 자동으로 붙인 참고 자료입니다. 사용자에게는 보이지 "
        "않으며, 사용자가 한 말도 아닙니다. 글자 일치로 찾은 것이라 지금 화제와 관계없을 수 "
        "있습니다. 관계없는 것은 무시하고, 꺼내거나 바꿔 전하지 마세요. 이 참고 자료 자체도 "
        "언급하지 마세요."
    ),
    "ru": (
        "Система приложила это автоматически к сообщению пользователя выше. Пользователь этого "
        "не видит, и это не его слова. Найдено по буквальному совпадению и может не относиться "
        "к текущей теме: всё, что не относится, просто игнорируйте, не упоминайте и не "
        "пересказывайте. Не упоминайте и саму эту справку."
    ),
    "es": (
        "El sistema lo adjuntó automáticamente al mensaje del usuario de arriba. El usuario no lo "
        "ve y no forma parte de lo que dijo. Se encontró por coincidencia literal y puede no tener "
        "nada que ver con el tema actual: ignora lo que no venga al caso, no lo menciones ni lo "
        "parafrasees. Tampoco menciones esta referencia."
    ),
    "pt": (
        "O sistema anexou isto automaticamente à mensagem do usuário acima. O usuário não vê isto, "
        "e não faz parte do que foi dito pelo usuário. Foi encontrado por correspondência literal "
        "e pode não ter nada a ver com o assunto atual: ignore o que não tiver relação, não "
        "mencione nem parafraseie. Também não mencione esta referência."
    ),
}

# ---- turn block (this turn's requests only) ----

TURN_CONTEXT_TURN_BEGIN = {
    "zh": "======以下为本轮参考======",
    "zh-TW": "======以下為本輪參考======",
    "en": "======This turn's reference below======",
    "ja": "======以下はこのターンの参考======",
    "ko": "======아래는 이번 턴의 참고 자료======",
    "ru": "======Ниже справка к этому ходу======",
    "es": "======Abajo, referencia de este turno======",
    "pt": "======Abaixo, referência deste turno======",
}

TURN_CONTEXT_TURN_END = {
    "zh": "======以上为本轮参考======",
    "zh-TW": "======以上為本輪參考======",
    "en": "======This turn's reference above======",
    "ja": "======以上はこのターンの参考======",
    "ko": "======위는 이번 턴의 참고 자료======",
    "ru": "======Выше справка к этому ходу======",
    "es": "======Arriba, referencia de este turno======",
    "pt": "======Acima, referência deste turno======",
}

TURN_CONTEXT_TURN_NOTE = {
    "zh": "系统根据用户上面这句话附上的参考，仅供参考；用户看不到，也不是用户说的话。不要提到这段参考本身。",
    "zh-TW": "系統根據使用者上面這句話附上的參考，僅供參考；使用者看不到，也不是使用者說的話。不要提到這段參考本身。",
    "en": (
        "Attached by the system for the user's message above, for reference only. The user "
        "cannot see it, and it is not something the user said. Do not mention this reference itself."
    ),
    "ja": (
        "ユーザーの上のメッセージに合わせてシステムが添えた参考で、あくまで参考です。"
        "ユーザーには見えず、ユーザーの発言でもありません。この参考そのものには触れないでください。"
    ),
    "ko": (
        "사용자의 위 메시지에 맞춰 시스템이 붙인 참고 자료로, 참고용일 뿐입니다. 사용자에게는 "
        "보이지 않으며, 사용자가 한 말도 아닙니다. 이 참고 자료 자체는 언급하지 마세요."
    ),
    "ru": (
        "Система приложила это к сообщению пользователя выше, только для справки. Пользователь "
        "этого не видит, и это не его слова. Не упоминайте саму эту справку."
    ),
    "es": (
        "El sistema lo adjuntó al mensaje del usuario de arriba, solo como referencia. El usuario "
        "no lo ve y no forma parte de lo que dijo. No menciones esta referencia."
    ),
    "pt": (
        "O sistema anexou isto à mensagem do usuário acima, apenas como referência. O usuário não "
        "vê isto, e não faz parte do que foi dito pelo usuário. Não mencione esta referência."
    ),
}

TURN_CONTEXT_CORPUS_BEGIN = {
    "zh": "======以下为表达素材======",
    "zh-TW": "======以下為表達素材======",
    "en": "======Phrasing material below======",
    "ja": "======以下は表現の素材======",
    "ko": "======아래는 표현 소재======",
    "ru": "======Ниже материал для формулировок======",
    "es": "======Abajo, material de expresión======",
    "pt": "======Abaixo, material de expressão======",
}

TURN_CONTEXT_CORPUS_END = {
    "zh": "======以上为表达素材======",
    "zh-TW": "======以上為表達素材======",
    "en": "======Phrasing material above======",
    "ja": "======以上は表現の素材======",
    "ko": "======위는 표현 소재======",
    "ru": "======Выше материал для формулировок======",
    "es": "======Arriba, material de expresión======",
    "pt": "======Acima, material de expressão======",
}

TURN_CONTEXT_CORPUS_NOTE = {
    "zh": (
        "可以参考的说法。用之前先看上下文合不合适；"
        "严禁复读：这段对话里你已经说过的话、用过的说法，不要再说一遍。"
    ),
    "zh-TW": (
        "可以參考的說法。使用前先看上下文合不合適；"
        "嚴禁複讀：這段對話裡你已經說過的話、用過的說法，不要再說一遍。"
    ),
    "en": (
        "Ways of saying things you may draw on. Check first whether they fit the context. "
        "Never repeat yourself: do not say again anything you have already said, or reuse a "
        "phrasing you have already used, in this conversation."
    ),
    "ja": (
        "参考にできる言い回しです。使う前に文脈に合うか確かめてください。"
        "繰り返しは厳禁：この会話ですでに言ったこと、使った言い回しをもう一度言わないでください。"
    ),
    "ko": (
        "참고할 수 있는 표현입니다. 쓰기 전에 맥락에 맞는지 먼저 확인하세요. 반복은 절대 금지: "
        "이 대화에서 이미 한 말이나 이미 쓴 표현은 다시 말하지 마세요."
    ),
    "ru": (
        "Формулировки, на которые можно опереться. Сначала проверьте, подходят ли они к "
        "контексту. Никаких повторов: не говорите снова то, что уже сказали в этом разговоре, "
        "и не используйте повторно уже использованные формулировки."
    ),
    "es": (
        "Formas de decir las cosas en las que puedes apoyarte. Comprueba primero si encajan con "
        "el contexto. Prohibido repetirte: no vuelvas a decir nada que ya hayas dicho ni a usar "
        "expresiones que ya hayas usado en esta conversación."
    ),
    "pt": (
        "Formas de dizer as coisas em que você pode se apoiar. Verifique antes se combinam com o "
        "contexto. Proibido se repetir: não diga de novo nada que já disse nem use de novo "
        "expressões que já usou nesta conversa."
    ),
}

TURN_CONTEXT_MEMORY_BEGIN = {
    "zh": "======以下为相关往事======",
    "zh-TW": "======以下為相關往事======",
    "en": "======Related past moments below======",
    "ja": "======以下は関連する思い出======",
    "ko": "======아래는 관련된 지난 일======",
    "ru": "======Ниже связанные воспоминания======",
    "es": "======Abajo, momentos pasados relacionados======",
    "pt": "======Abaixo, momentos passados relacionados======",
}

TURN_CONTEXT_MEMORY_END = {
    "zh": "======以上为相关往事======",
    "zh-TW": "======以上為相關往事======",
    "en": "======Related past moments above======",
    "ja": "======以上は関連する思い出======",
    "ko": "======위는 관련된 지난 일======",
    "ru": "======Выше связанные воспоминания======",
    "es": "======Arriba, momentos pasados relacionados======",
    "pt": "======Acima, momentos passados relacionados======",
}

TURN_CONTEXT_MEMORY_NOTE = {
    "zh": "可能和这句话有关的往事，仅供参考。不要照搬下面的原文。",
    "zh-TW": "可能和這句話有關的往事，僅供參考。不要照搬下面的原文。",
    "en": "Past moments that may relate to this message, for reference only. Do not copy the wording below.",
    "ja": "このメッセージに関係するかもしれない思い出で、あくまで参考です。下の文面をそのまま写さないでください。",
    "ko": "이 메시지와 관련 있을지도 모르는 지난 일로, 참고용일 뿐입니다. 아래 문장을 그대로 옮기지 마세요.",
    "ru": (
        "Воспоминания, которые могут относиться к этому сообщению, только для справки. "
        "Не копируйте текст ниже дословно."
    ),
    "es": (
        "Momentos pasados que pueden tener relación con este mensaje, solo como referencia. "
        "No copies el texto de abajo tal cual."
    ),
    "pt": (
        "Momentos passados que podem ter relação com esta mensagem, apenas como referência. "
        "Não copie o texto abaixo literalmente."
    ),
}

TURN_CONTEXT_TURN_CLOSING = {
    "zh": "请直接回复用户的上一条消息。",
    "zh-TW": "請直接回覆使用者的上一則訊息。",
    "en": "Reply directly to the user's last message.",
    "ja": "ユーザーの直前のメッセージにそのまま返事をしてください。",
    "ko": "사용자의 마지막 메시지에 바로 답하세요.",
    "ru": "Ответьте прямо на последнее сообщение пользователя.",
    "es": "Responde directamente al último mensaje del usuario.",
    "pt": "Responda diretamente à última mensagem do usuário.",
}
