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

"""The tool she uses, with NekoPower switched off, to ask the user to let her switch it on.

The definition rides on every request of a session in the default state (the
master switch off), so it is kept short; ``tests/unit/test_task_tools_budget.py``
holds it to a budget together with the opening sentence. The user answers in a
NekoPower question card in the chat (``main_logic/nekopower_enable.py``); she
hears the outcome later, as a report. What the tool says back at once is in
``ENABLE_*``; the card in ``ENABLE_CARD_*``; the reports in ``ENABLE_REPORT_*``.
"""

ENABLE_NEKOPOWER_TOOL_NAME = "enable_nekopower"

ENABLE_NEKOPOWER_TOOL_DESCRIPTION = {
    "zh": "请用户授权你打开喵能力：聊天里会出现确认卡片，结果稍后告诉你。",
    "zh-TW": "請使用者授權你打開喵能力：聊天裡會出現確認卡片，結果稍後告訴你。",
    "en": "Ask the user to authorize you to switch NekoPower on: a confirmation card appears in the chat; you are told the outcome later.",
    "ja": "にゃんパワーをオンにする許可をユーザーに求めます。チャットに確認カードが出て、結果はあとで知らせます。",
    "ko": "냥파워를 켜도록 사용자에게 허락을 구합니다. 채팅에 확인 카드가 뜨고, 결과는 나중에 알려 줍니다.",
    "ru": "Попросить пользователя разрешить вам включить NekoPower: в чате появится карточка подтверждения, результат сообщат позже.",
    "es": "Pide al usuario que te autorice a activar NekoPower: aparece una tarjeta de confirmación en el chat y se te dirá el resultado después.",
    "pt": "Pede ao usuário que autorize você a ativar o NekoPower: aparece um cartão de confirmação no chat e você saberá o resultado depois.",
}

# What the tool says back at once.
ENABLE_ASKED = {
    "zh": "已经在聊天里请用户确认了。结果出来前别说喵能力已经打开。",
    "zh-TW": "已經在聊天裡請使用者確認了。結果出來前別說喵能力已經打開。",
    "en": "The user has been asked in the chat. Until the outcome comes, do not say NekoPower is on.",
    "ja": "チャットでユーザーに確認を求めました。結果が出るまで、にゃんパワーがオンになったと言わないでください。",
    "ko": "채팅에서 사용자에게 확인을 요청했습니다. 결과가 나오기 전에는 냥파워가 켜졌다고 말하지 마세요.",
    "ru": "Пользователя уже спросили в чате. Пока нет результата, не говорите, что NekoPower включён.",
    "es": "Ya se le ha preguntado al usuario en el chat. Hasta que llegue el resultado, no digas que NekoPower está activado.",
    "pt": "O usuário já foi consultado no chat. Até sair o resultado, não diga que o NekoPower está ativado.",
}
ENABLE_WAITING = {
    "zh": "确认卡片还在等用户点，别再问一次。",
    "zh-TW": "確認卡片還在等使用者點，別再問一次。",
    "en": "The confirmation card is still waiting for the user; do not ask again.",
    "ja": "確認カードはまだユーザーの操作を待っています。もう一度頼まないでください。",
    "ko": "확인 카드가 아직 사용자를 기다리고 있습니다. 다시 묻지 마세요.",
    "ru": "Карточка подтверждения ещё ждёт пользователя; не спрашивайте снова.",
    "es": "La tarjeta de confirmación sigue esperando al usuario; no vuelvas a preguntar.",
    "pt": "O cartão de confirmação ainda espera o usuário; não pergunte de novo.",
}
ENABLE_COOLDOWN = {
    "zh": "用户刚连续拒绝了两次，还要等大约 {minutes} 分钟才能再问；照实说现在办不到。",
    "zh-TW": "使用者剛連續拒絕了兩次，還要等大約 {minutes} 分鐘才能再問；照實說現在辦不到。",
    "en": "The user has just refused twice in a row; you can ask again in about {minutes} min. Say plainly you cannot do it now.",
    "ja": "ユーザーは続けて2回断ったばかりなので、あと約{minutes}分は頼めません。いまはできないと正直に伝えてください。",
    "ko": "사용자가 방금 연달아 두 번 거절해서 약 {minutes}분 뒤에야 다시 물을 수 있습니다. 지금은 할 수 없다고 솔직하게 말하세요.",
    "ru": "Пользователь только что отказал дважды подряд; спросить снова можно примерно через {minutes} мин. Честно скажите, что сейчас это невозможно.",
    "es": "El usuario acaba de negarse dos veces seguidas; podrás volver a preguntar en unos {minutes} min. Di con franqueza que ahora no puedes.",
    "pt": "O usuário acabou de recusar duas vezes seguidas; você poderá perguntar de novo em cerca de {minutes} min. Diga com franqueza que agora não dá.",
}
ENABLE_REMOTE = {
    "zh": "N.E.K.O 的后台不在用户自己的电脑上，喵能力在这里打不开；照实说现在办不到。",
    "zh-TW": "N.E.K.O 的後台不在使用者自己的電腦上，喵能力在這裡打不開；照實說現在辦不到。",
    "en": "N.E.K.O's backend is not on the user's own computer, so NekoPower cannot be switched on here; say plainly you cannot do it now.",
    "ja": "N.E.K.O のバックエンドがユーザー自身のパソコン上にないため、ここではにゃんパワーをオンにできません。いまはできないと正直に伝えてください。",
    "ko": "N.E.K.O 백엔드가 사용자 본인의 컴퓨터에 있지 않아 여기서는 냥파워를 켤 수 없습니다. 지금은 할 수 없다고 솔직하게 말하세요.",
    "ru": "Бэкенд N.E.K.O работает не на компьютере пользователя, поэтому здесь NekoPower включить нельзя; честно скажите, что сейчас это невозможно.",
    "es": "El backend de N.E.K.O no está en la computadora del usuario, así que aquí no se puede activar NekoPower; di con franqueza que ahora no puedes.",
    "pt": "O backend do N.E.K.O não está no computador do usuário, então aqui não dá para ativar o NekoPower; diga com franqueza que agora não dá.",
}
ENABLE_UNAVAILABLE = {
    "zh": "现在没法请用户确认（聊天界面没连上）；照实说现在办不到。",
    "zh-TW": "現在沒辦法請使用者確認（聊天介面沒連上）；照實說現在辦不到。",
    "en": "The user cannot be asked right now (the chat is not connected); say plainly you cannot do it now.",
    "ja": "いまはユーザーに確認を求められません（チャットがつながっていません）。いまはできないと正直に伝えてください。",
    "ko": "지금은 사용자에게 확인을 요청할 수 없습니다(채팅이 연결되어 있지 않음). 지금은 할 수 없다고 솔직하게 말하세요.",
    "ru": "Сейчас спросить пользователя нельзя (чат не подключён); честно скажите, что сейчас это невозможно.",
    "es": "Ahora no se puede preguntar al usuario (el chat no está conectado); di con franqueza que ahora no puedes.",
    "pt": "Agora não dá para perguntar ao usuário (o chat não está conectado); diga com franqueza que agora não dá.",
}
ENABLE_ALREADY_ON = {
    "zh": "喵能力已经开着。",
    "zh-TW": "喵能力已經開著。",
    "en": "NekoPower is already on.",
    "ja": "にゃんパワーはもうオンです。",
    "ko": "냥파워는 이미 켜져 있습니다.",
    "ru": "NekoPower уже включён.",
    "es": "NekoPower ya está activado.",
    "pt": "O NekoPower já está ativado.",
}

# The name the reports come under (the panel's ``settings.toggles.agentMaster``).
NEKOPOWER_NAME = {
    "zh": "喵能力",
    "zh-TW": "喵能力",
    "en": "NekoPower",
    "ja": "にゃんパワー",
    "ko": "냥파워",
    "ru": "NekoPower",
    "es": "NekoPower",
    "pt": "NekoPower",
}

# The card the user answers ({name}: her name).
ENABLE_CARD_TITLE = {
    "zh": "打开喵能力",
    "zh-TW": "打開喵能力",
    "en": "Switch NekoPower on",
    "ja": "にゃんパワーをオンにする",
    "ko": "냥파워 켜기",
    "ru": "Включить NekoPower",
    "es": "Activar NekoPower",
    "pt": "Ativar o NekoPower",
}
ENABLE_CARD_QUESTION = {
    "zh": "{name}想打开喵能力，替你在电脑上办事。同意吗？",
    "zh-TW": "{name}想打開喵能力，替你在電腦上辦事。同意嗎？",
    "en": "{name} wants to switch NekoPower on to do things on the computer for you. Do you agree?",
    "ja": "{name}がにゃんパワーをオンにして、パソコンで代わりに作業したいそうです。許可しますか？",
    "ko": "{name}이(가) 냥파워를 켜서 컴퓨터에서 대신 일을 하고 싶어 합니다. 허락할까요?",
    "ru": "{name} хочет включить NekoPower, чтобы делать за вас дела на компьютере. Согласны?",
    "es": "{name} quiere activar NekoPower para hacer cosas en la computadora por ti. ¿Estás de acuerdo?",
    "pt": "{name} quer ativar o NekoPower para fazer coisas no computador por você. Você concorda?",
}

# The reports she gets later ({text}: what the user typed instead of clicking).
ENABLE_REPORT_ACCEPTED = {
    "zh": "用户同意了，喵能力已经打开，你现在有任务工具了：接着办刚才用户要的事。",
    "zh-TW": "使用者同意了，喵能力已經打開，你現在有任務工具了：接著辦剛才使用者要的事。",
    "en": "The user agreed and NekoPower is now on; you have the task tools: carry on with what the user asked for.",
    "ja": "ユーザーが許可し、にゃんパワーがオンになりました。タスクツールが使えます。さっきユーザーに頼まれたことを続けてください。",
    "ko": "사용자가 허락해서 냥파워가 켜졌고, 이제 작업 도구가 있습니다. 방금 사용자가 부탁한 일을 이어서 하세요.",
    "ru": "Пользователь согласился, NekoPower включён, у вас есть инструменты задач: продолжайте то, о чём просил пользователь.",
    "es": "El usuario aceptó y NekoPower está activado; ya tienes las herramientas de tareas: sigue con lo que pidió el usuario.",
    "pt": "O usuário concordou e o NekoPower está ativado; você já tem as ferramentas de tarefas: continue com o que o usuário pediu.",
}
# The session could not be renewed in time (renew_session_now): the task tools may be
# there (they are synced to the live session) or not yet (Gemini Live takes them at connect).
ENABLE_REPORT_ACCEPTED_NOT_RENEWED = {
    "zh": "用户同意了，喵能力已经打开，但会话还没来得及换新：手上有任务工具就接着办刚才的事；没有的话告诉对方喵能力已经打开，稍等一下再办。",
    "zh-TW": "使用者同意了，喵能力已經打開，但會話還沒來得及換新：手上有任務工具就接著辦剛才的事；沒有的話告訴對方喵能力已經打開，稍等一下再辦。",
    "en": "The user agreed and NekoPower is on, but the session has not been renewed yet: if you have the task tools, carry on with what the user asked; if not, tell them NekoPower is on and it takes a moment before you can do it.",
    "ja": "ユーザーが許可し、にゃんパワーはオンになりましたが、セッションの切り替えがまだです。タスクツールがあればさっきの用件を続けてください。なければ、にゃんパワーはオンになったので少し待ってほしいと伝えてください。",
    "ko": "사용자가 허락해서 냥파워가 켜졌지만 세션이 아직 새로 바뀌지 않았습니다. 작업 도구가 있으면 방금 부탁받은 일을 이어서 하고, 없으면 냥파워는 켜졌으니 조금만 기다려 달라고 말하세요.",
    "ru": "Пользователь согласился, NekoPower включён, но сессия ещё не обновилась: если у вас есть инструменты задач, продолжайте то, о чём просил пользователь; если нет, скажите, что NekoPower включён и нужно немного подождать.",
    "es": "El usuario aceptó y NekoPower está activado, pero la sesión aún no se ha renovado: si tienes las herramientas de tareas, sigue con lo que pidió el usuario; si no, dile que NekoPower ya está activado y que hace falta esperar un momento.",
    "pt": "O usuário concordou e o NekoPower está ativado, mas a sessão ainda não foi renovada: se você tem as ferramentas de tarefas, continue com o que o usuário pediu; se não, diga que o NekoPower já está ativado e que é preciso esperar um pouco.",
}
# What the user typed beside a click on "yes" or "no" ({text}), added to the report.
ENABLE_REPORT_NOTE = {
    "zh": "用户还补了一句：「{text}」。",
    "zh-TW": "使用者還補了一句：「{text}」。",
    "en": "The user also wrote: \"{text}\".",
    "ja": "ユーザーは「{text}」とも書きました。",
    "ko": "사용자가 \"{text}\"라고도 썼습니다.",
    "ru": "Пользователь также написал: «{text}».",
    "es": "El usuario también escribió: «{text}».",
    "pt": "O usuário também escreveu: \"{text}\".",
}
ENABLE_REPORT_DECLINED = {
    "zh": "用户没同意打开喵能力；照实告诉对方现在办不到。",
    "zh-TW": "使用者沒同意打開喵能力；照實告訴對方現在辦不到。",
    "en": "The user did not agree to switch NekoPower on; tell them plainly you cannot do it now.",
    "ja": "ユーザーはにゃんパワーをオンにすることを許可しませんでした。いまはできないと正直に伝えてください。",
    "ko": "사용자가 냥파워를 켜는 데 동의하지 않았습니다. 지금은 할 수 없다고 솔직하게 말하세요.",
    "ru": "Пользователь не согласился включить NekoPower; честно скажите, что сейчас это невозможно.",
    "es": "El usuario no aceptó activar NekoPower; dile con franqueza que ahora no puedes.",
    "pt": "O usuário não concordou em ativar o NekoPower; diga com franqueza que agora não dá.",
}
ENABLE_REPORT_EXPIRED = {
    "zh": "确认卡片两分钟没人点，已经撤了；喵能力还没打开。",
    "zh-TW": "確認卡片兩分鐘沒人點，已經撤了；喵能力還沒打開。",
    "en": "Nobody answered the confirmation card for two minutes, so it was withdrawn; NekoPower is still off.",
    "ja": "確認カードは2分間だれも押さなかったので取り下げました。にゃんパワーはまだオフです。",
    "ko": "확인 카드를 2분 동안 아무도 누르지 않아 거두었습니다. 냥파워는 아직 꺼져 있습니다.",
    "ru": "Карточку подтверждения две минуты никто не нажал, её убрали; NekoPower всё ещё выключен.",
    "es": "Nadie respondió la tarjeta de confirmación en dos minutos y se retiró; NekoPower sigue desactivado.",
    "pt": "Ninguém respondeu ao cartão de confirmação em dois minutos, então ele foi retirado; o NekoPower continua desativado.",
}
ENABLE_REPORT_WORDS = {
    "zh": "用户没点按钮，回了一句：「{text}」。喵能力还没打开。",
    "zh-TW": "使用者沒點按鈕，回了一句：「{text}」。喵能力還沒打開。",
    "en": "The user did not click; they wrote: \"{text}\". NekoPower is still off.",
    "ja": "ユーザーはボタンを押さず、「{text}」と書きました。にゃんパワーはまだオフです。",
    "ko": "사용자가 버튼을 누르지 않고 \"{text}\"라고 썼습니다. 냥파워는 아직 꺼져 있습니다.",
    "ru": "Пользователь не нажал кнопку, а написал: «{text}». NekoPower всё ещё выключен.",
    "es": "El usuario no pulsó ningún botón; escribió: «{text}». NekoPower sigue desactivado.",
    "pt": "O usuário não clicou; escreveu: \"{text}\". O NekoPower continua desativado.",
}
ENABLE_REPORT_DECLINED_COOLDOWN = {
    "zh": "用户又没同意，已经连续拒绝两次：5 分钟内不能再请用户授权；照实告诉对方现在办不到。",
    "zh-TW": "使用者又沒同意，已經連續拒絕兩次：5 分鐘內不能再請使用者授權；照實告訴對方現在辦不到。",
    "en": "The user said no again, twice in a row: you cannot ask for authorization for 5 minutes; tell them plainly you cannot do it now.",
    "ja": "ユーザーはまた断り、続けて2回になりました。5分間は許可を求められません。いまはできないと正直に伝えてください。",
    "ko": "사용자가 또 거절해 연달아 두 번이 되었습니다. 5분 동안은 허락을 구할 수 없습니다. 지금은 할 수 없다고 솔직하게 말하세요.",
    "ru": "Пользователь снова отказал, уже дважды подряд: 5 минут просить разрешения нельзя; честно скажите, что сейчас это невозможно.",
    "es": "El usuario volvió a negarse, dos veces seguidas: no puedes pedir autorización durante 5 minutos; dile con franqueza que ahora no puedes.",
    "pt": "O usuário recusou de novo, duas vezes seguidas: você não pode pedir autorização por 5 minutos; diga com franqueza que agora não dá.",
}
# {reason}: one of ENABLE_FAILURE_REASONS.
ENABLE_REPORT_FAILED = {
    "zh": "用户同意了，但喵能力没能打开：{reason}。照实告诉对方现在办不到。",
    "zh-TW": "使用者同意了，但喵能力沒能打開：{reason}。照實告訴對方現在辦不到。",
    "en": "The user agreed, but NekoPower could not be switched on: {reason}. Tell them plainly you cannot do it now.",
    "ja": "ユーザーは許可しましたが、にゃんパワーをオンにできませんでした：{reason}。いまはできないと正直に伝えてください。",
    "ko": "사용자가 허락했지만 냥파워를 켜지 못했습니다: {reason}. 지금은 할 수 없다고 솔직하게 말하세요.",
    "ru": "Пользователь согласился, но включить NekoPower не удалось: {reason}. Честно скажите, что сейчас это невозможно.",
    "es": "El usuario aceptó, pero NekoPower no se pudo activar: {reason}. Dile con franqueza que ahora no puedes.",
    "pt": "O usuário concordou, mas o NekoPower não pôde ser ativado: {reason}. Diga com franqueza que agora não dá.",
}
# Why switching it on failed (main_routers/agent_router.py::_switch_nekopower_on).
ENABLE_FAILURE_REASONS = {
    "unreachable": {
        "zh": "喵能力的后台没连上",
        "zh-TW": "喵能力的後台沒連上",
        "en": "the NekoPower backend could not be reached",
        "ja": "にゃんパワーのバックエンドにつながりませんでした",
        "ko": "냥파워 백엔드에 연결되지 않았습니다",
        "ru": "не удалось связаться с бэкендом NekoPower",
        "es": "no se pudo contactar con el backend de NekoPower",
        "pt": "não foi possível falar com o backend do NekoPower",
    },
    "refused": {
        "zh": "喵能力的后台没有接受",
        "zh-TW": "喵能力的後台沒有接受",
        "en": "the NekoPower backend did not accept it",
        "ja": "にゃんパワーのバックエンドが受け付けませんでした",
        "ko": "냥파워 백엔드가 받아들이지 않았습니다",
        "ru": "бэкенд NekoPower не принял команду",
        "es": "el backend de NekoPower no lo aceptó",
        "pt": "o backend do NekoPower não aceitou",
    },
    "unknown": {
        "zh": "后台没有回话，不确定打开了没有",
        "zh-TW": "後台沒有回話，不確定打開了沒有",
        "en": "the backend did not answer, so it is not known whether it came on",
        "ja": "バックエンドから返事がなく、オンになったかどうか分かりません",
        "ko": "백엔드가 응답하지 않아 켜졌는지 알 수 없습니다",
        "ru": "бэкенд не ответил, и неизвестно, включился ли он",
        "es": "el backend no respondió y no se sabe si se activó",
        "pt": "o backend não respondeu e não se sabe se foi ativado",
    },
}
