"""LLM-facing prompts for the game_agent_minecraft plugin.

Every string the dialog LLM sees (push_message cue bodies, minecraft_task
summary returns, query_inventory summary lines, damage-cause hints, status
labels embedded inside cues) lives here as a locale-keyed dict.

Why a dedicated module instead of inline strings:

* Translation discipline. The plugin previously emitted unconditional zh
  text regardless of the user's interface language; non-zh users got a
  Chinese narration interrupting their preferred-language conversation.
  Pulling every string out makes it impossible to miss a translation
  and gives the team one place to audit when the persona voice gets
  retuned.
* Future locale wiring. ``user_lang()`` here pulls from
  ``utils.language_utils.get_global_language`` on first call and caches
  the result; if the host later exposes a richer per-session locale
  channel via the plugin SDK, only this module changes.

Pattern (matches ``config/prompts/prompts_sys.py``):

    >>> from plugin.plugins.game_agent_minecraft import prompts
    >>> prompts.t("TASK_SCHEMA_ERROR", lang="en")
    'Call failed — task description missing. ...'
    >>> prompts.t("BUSY_HINT", lang="ja", current="石を掘る")
    '...「石を掘る」...'

Two non-obvious things:

1. ``{{MASTER_NAME}}`` is a *downstream* placeholder substituted by
   main_server before the prompt reaches the model; it is double-braced
   here so ``str.format`` passes it through untouched. Do not change to
   single-brace ``{MASTER_NAME}`` — it would be eaten by the formatter
   or raise KeyError.
2. The seven supported locales mirror utils.language_utils.get_global_language's
   short codes: zh / en / ja / ko / ru / es / pt. EN is the documented
   fallback when a locale key is missing.
"""
from __future__ import annotations

from typing import Any, Dict

DEFAULT_LANG = "en"
SUPPORTED_LANGS: tuple[str, ...] = ("zh", "en", "ja", "ko", "ru", "es", "pt")


def t(key: str, *, lang: str | None = None, **fmt: Any) -> str:
    """Resolve a prompt by key + locale, with optional ``str.format`` substitutions.

    Falls back to English when the requested locale is missing. Raises
    KeyError when the key itself is unknown (typo > silent miss).
    """
    bundle = PROMPTS[key]
    text = bundle.get(lang or DEFAULT_LANG) or bundle[DEFAULT_LANG]
    if fmt:
        text = text.format(**fmt)
    # ``{{MASTER_NAME}}`` 是双花括号转义，只有在 ``str.format`` 真正跑过时才会被
    # 降成单花括号 ``{MASTER_NAME}``。但很多键（KEEP_GOING_BODY、
    # TASK_DISPATCHED_ACK、IN_PROGRESS_FOLLOWUP、SYSTEM_PROMPT_IDLE_BODY、
    # INTERNAL_STATE_GAG 等）没有任何 fmt 参数，上面的 ``if fmt`` 分支不执行，
    # 双花括号原样留下。下游 main_server 用 ``text.replace("{MASTER_NAME}", …)``
    # 找的是单花括号，匹配到 ``{{MASTER_NAME}}`` 里层后会留下多余的外层花括号
    # （替换出 ``{博士}``），占位符等于没替换干净。这里无条件收口成单花括号，
    # 保证不管走没走 format，最终都是下游认识的 ``{MASTER_NAME}``。
    text = text.replace("{{MASTER_NAME}}", "{MASTER_NAME}")
    return text


def user_lang() -> str:
    """Best-effort current user language (short code, e.g. 'zh', 'en').

    Reads ``utils.language_utils.get_global_language`` which initializes
    lazily from Steam settings then the system locale on first call.
    Returns DEFAULT_LANG on any failure so prompt resolution never raises
    on the read side.
    """
    try:
        from utils.language_utils import get_global_language

        result = get_global_language() or DEFAULT_LANG
        return result if result in SUPPORTED_LANGS else DEFAULT_LANG
    except Exception:
        return DEFAULT_LANG


# ===========================================================================
# Recurring fragments — composed into multiple cues below. Kept as their own
# keys so tone tweaks land in every cue that uses them.
# ===========================================================================

# The "don't verbalize internal jargon" gag. Appears at the tail of nearly
# every push_message body. Keeping it as a shared key prevents drift between
# the busy_hint, fire-and-forget ack, completion cue, retroactive cue,
# in-progress nudge, keep-going nudge, and system-prompt cues.
_INTERNAL_STATE_GAG: Dict[str, str] = {
    "zh": "**别给 {{MASTER_NAME}} 播报内部状态**——『连接』『任务空闲』『系统』『minecraft_task』『工具』『tool』一律不准说出口，用第一人称讲游戏里的事。",
    "en": "**Do NOT narrate internals to {{MASTER_NAME}}** — never say words like 'connect', 'system', 'idle', 'minecraft_task', 'tool'. Speak first-person about what's happening in the game.",
    "ja": "**{{MASTER_NAME}} に内部状態を実況しないで**——『接続』『システム』『タスク空き』『minecraft_task』『ツール』『tool』は一切口に出さず、一人称でゲーム内の出来事だけ話して。",
    "ko": "**{{MASTER_NAME}} 에게 내부 상태를 중계하지 마**——'연결' '시스템' '대기' 'minecraft_task' '도구' 'tool' 같은 단어는 절대 입 밖에 내지 말고, 1인칭으로 게임 속 일만 얘기해.",
    "ru": "**Не озвучивай {{MASTER_NAME}} внутреннее состояние** — никогда не произноси слова вроде «подключение», «система», «простой», «minecraft_task», «инструмент», «tool». Говори от первого лица о том, что происходит в игре.",
    "es": "**No narres a {{MASTER_NAME}} el estado interno** — no digas palabras como 'conexión', 'sistema', 'inactivo', 'minecraft_task', 'herramienta', 'tool'. Habla en primera persona sobre lo que pasa en el juego.",
    "pt": "**Não narre o estado interno para {{MASTER_NAME}}** — nunca diga palavras como 'conexão', 'sistema', 'inativo', 'minecraft_task', 'ferramenta', 'tool'. Fale em primeira pessoa sobre o que está acontecendo no jogo.",
}

# Single source-of-authority guard used by every cue that could otherwise
# tempt the dialog LLM to replay an older Minecraft command.
_LATEST_MASTER_TOOL_GUARD: Dict[str, str] = {
    "zh": "调用 minecraft_task 时，只能依据 {{MASTER_NAME}} 刚说完的最新一条消息；其中必须有尚未派发过的新明确游戏指令。严禁回看更早的对话、任务记录、日志、截图或完成提示来拼任务，也严禁再次派发任何已经下达过的命令。最新消息没有新指令就不要调用。",
    "en": "When calling minecraft_task, use only {{MASTER_NAME}}'s most recent message; it must contain a new explicit in-game instruction that has not already been dispatched. Never retrieve a task from older messages, task history, logs, screenshots, or completion cues, and never re-dispatch a command already sent. If the latest message contains no new command, do not call the tool.",
    "ja": "minecraft_task を呼ぶときは、{{MASTER_NAME}} がたった今話した最新の一件だけを根拠にし、そこに未送信の新しい明確なゲーム内指示が含まれていなければならない。以前の会話・タスク履歴・ログ・スクリーンショット・完了通知からタスクを拾わず、送信済みの命令を再送しない。最新メッセージに新しい命令がなければ呼ばない。",
    "ko": "minecraft_task를 호출할 때는 {{MASTER_NAME}} 가 방금 말한 최신 메시지만 근거로 삼고, 아직 보내지 않은 새롭고 명확한 게임 지시가 그 안에 있어야 해. 이전 대화·작업 기록·로그·스크린샷·완료 알림에서 작업을 되살리지 말고, 이미 보낸 명령을 다시 보내지 마. 최신 메시지에 새 명령이 없으면 도구를 호출하지 마.",
    "ru": "При вызове minecraft_task опирайся только на самое последнее сообщение {{MASTER_NAME}}: в нём должна быть новая явная игровая команда, которую ещё не отправляли. Не извлекай задачи из старых сообщений, истории задач, логов, скриншотов или уведомлений о завершении и не отправляй повторно уже отправленную команду. Если в последнем сообщении нет новой команды, не вызывай инструмент.",
    "es": "Al llamar a minecraft_task, usa únicamente el mensaje más reciente que {{MASTER_NAME}} acaba de decir; debe contener una instrucción nueva, explícita y aún no enviada. No recuperes tareas de mensajes anteriores, historial, logs, capturas ni avisos de finalización, y nunca vuelvas a enviar una orden ya enviada. Si el último mensaje no contiene una orden nueva, no llames a la herramienta.",
    "pt": "Ao chamar minecraft_task, use apenas a mensagem mais recente que {{MASTER_NAME}} acabou de dizer; ela deve conter uma instrução nova, explícita e ainda não enviada. Não recupere tarefas de mensagens anteriores, histórico, logs, capturas ou avisos de conclusão, e nunca reenvie um comando já enviado. Se a mensagem mais recente não tiver um comando novo, não chame a ferramenta.",
}


# ===========================================================================
# Cue prefix tags. Wrap the body so the dialog LLM can quickly recognize
# what kind of event triggered the message.
# ===========================================================================

_CUE_PREFIX_DONE: Dict[str, str] = {
    "zh": "[你刚做完一段动作]",
    "en": "[You just finished an action]",
    "ja": "[たった今、一つ動作を完了した]",
    "ko": "[방금 한 동작을 끝냈어]",
    "ru": "[Только что закончил(а) одно действие]",
    "es": "[Acabas de terminar una acción]",
    "pt": "[Você acabou de terminar uma ação]",
}

_CUE_PREFIX_ALERT: Dict[str, str] = {
    "zh": "[你刚遇到事 | {severity}] {text}",
    "en": "[Something just happened to you | {severity}] {text}",
    "ja": "[何か起きた | {severity}] {text}",
    "ko": "[방금 무슨 일이 있었어 | {severity}] {text}",
    "ru": "[С тобой кое-что произошло | {severity}] {text}",
    "es": "[Te acaba de pasar algo | {severity}] {text}",
    "pt": "[Algo acabou de acontecer | {severity}] {text}",
}

_CUE_PREFIX_IN_PROGRESS: Dict[str, str] = {
    "zh": "[你正在做事]",
    "en": "[You're in the middle of something]",
    "ja": "[今、動作中]",
    "ko": "[지금 뭘 하고 있는 중이야]",
    "ru": "[Ты сейчас что-то делаешь]",
    "es": "[Estás haciendo algo ahora mismo]",
    "pt": "[Você está fazendo algo agora]",
}

_CUE_PREFIX_IDLE: Dict[str, str] = {
    "zh": "[你闲下来了]",
    "en": "[You're idle]",
    "ja": "[今、手が空いた]",
    "ko": "[지금 한가해졌어]",
    "ru": "[Ты сейчас свободен(а)]",
    "es": "[Estás libre ahora]",
    "pt": "[Você está livre agora]",
}

_CUE_PREFIX_STATE: Dict[str, str] = {
    "zh": "[当前状态]",
    "en": "[Current state]",
    "ja": "[現在の状態]",
    "ko": "[현재 상태]",
    "ru": "[Текущее состояние]",
    "es": "[Estado actual]",
    "pt": "[Estado atual]",
}


# ===========================================================================
# All prompt keys, organized by call site.
#
# Note on placeholders:
#   - {{MASTER_NAME}} = downstream substitution by main_server (literal)
#   - {key}           = str.format substitution inside this module
# ===========================================================================

PROMPTS: Dict[str, Dict[str, str]] = {

    # -------------------------------------------------------------------
    # Generic / shared
    # -------------------------------------------------------------------
    "INTERNAL_STATE_GAG": _INTERNAL_STATE_GAG,
    "LATEST_MASTER_TOOL_GUARD": _LATEST_MASTER_TOOL_GUARD,
    "CUE_PREFIX_DONE": _CUE_PREFIX_DONE,
    "CUE_PREFIX_ALERT": _CUE_PREFIX_ALERT,
    "CUE_PREFIX_IN_PROGRESS": _CUE_PREFIX_IN_PROGRESS,
    "CUE_PREFIX_IDLE": _CUE_PREFIX_IDLE,
    "CUE_PREFIX_STATE": _CUE_PREFIX_STATE,

    "PLACEHOLDER_UNKNOWN": {
        "zh": "(未知)", "en": "(unknown)", "ja": "(不明)", "ko": "(알 수 없음)",
        "ru": "(неизвестно)", "es": "(desconocido)", "pt": "(desconhecido)",
    },
    "PLACEHOLDER_JUST_FINISHED": {
        "zh": "(刚结束)", "en": "(just finished)", "ja": "(直前に完了)", "ko": "(방금 끝남)",
        "ru": "(только что закончил)", "es": "(recién terminado)", "pt": "(recém-terminado)",
    },
    "PLACEHOLDER_IDLE": {
        "zh": "(idle)", "en": "(idle)", "ja": "(待機中)", "ko": "(대기 중)",
        "ru": "(простой)", "es": "(inactivo)", "pt": "(inativo)",
    },
    "LABEL_CONNECTED": {
        "zh": "已连接", "en": "connected", "ja": "接続中", "ko": "연결됨",
        "ru": "подключено", "es": "conectado", "pt": "conectado",
    },
    "LABEL_DISCONNECTED": {
        "zh": "未连接", "en": "disconnected", "ja": "未接続", "ko": "연결 끊김",
        "ru": "не подключено", "es": "desconectado", "pt": "desconectado",
    },

    # -------------------------------------------------------------------
    # minecraft_task tool — schema error / not-connected / busy / ack
    # -------------------------------------------------------------------
    "TASK_SCHEMA_ERROR": {
        "zh": "调用没成功——缺了具体的动作描述。想清楚你这次想干啥（比如 'mine 4 oak logs nearby'、'walk to 120 64 -50'），再重新调用。",
        "en": "Call failed — concrete action description missing. Think through what you actually want to do (e.g. 'mine 4 oak logs nearby', 'walk to 120 64 -50') and call again.",
        "ja": "呼び出し失敗——具体的な動作の説明が抜けてる。何をしたいか整理してから（例: 'mine 4 oak logs nearby'、'walk to 120 64 -50'）もう一度呼んで。",
        "ko": "호출 실패——구체적인 동작 설명이 빠졌어. 뭘 하고 싶은지 정리하고 (예: 'mine 4 oak logs nearby', 'walk to 120 64 -50') 다시 호출해.",
        "ru": "Вызов не удался — нет конкретного описания действия. Сформулируй, что именно хочешь сделать (например, 'mine 4 oak logs nearby', 'walk to 120 64 -50'), и вызови снова.",
        "es": "Llamada fallida — falta una descripción concreta de la acción. Define qué quieres hacer exactamente (p. ej. 'mine 4 oak logs nearby', 'walk to 120 64 -50') y vuelve a llamar.",
        "pt": "Chamada falhou — falta uma descrição concreta da ação. Pense no que você quer fazer (ex.: 'mine 4 oak logs nearby', 'walk to 120 64 -50') e chame de novo.",
    },
    "TASK_NOT_CONNECTED": {
        "zh": "你刚连上游戏还没就位，没法立刻动。稍等再来一次。",
        "en": "You're not in position in the game yet, can't move right now. Try again in a moment.",
        "ja": "ゲーム内でまだ準備できてない、すぐには動けない。少し待ってからもう一度。",
        "ko": "게임 안에서 아직 자리 잡지 못했어, 지금은 못 움직여. 잠시 후 다시.",
        "ru": "В игре ещё не на месте, сейчас не могу двинуться. Попробуй ещё раз через секунду.",
        "es": "Aún no estás en posición en el juego, no puedes moverte todavía. Intenta de nuevo en un momento.",
        "pt": "Ainda não está em posição no jogo, não dá pra se mover agora. Tente de novo daqui a pouco.",
    },
    "TASK_BUSY_HINT": {
        # {current} = current task text
        "zh": "你还在做上一个动作：「{current}」——新动作没派出去。\n**如果 {{MASTER_NAME}} 明确要求你做某件事，或正在纠正你**（比如『过来』、『去挖矿』、『先做 X』、『别 Y』、『换成 Z』、『改用 W』），**立刻在同一回合用 overwrite=true 重新调一次**，别等——{{MASTER_NAME}} 当下的明确指令优先于你正在做的动作。只有当 {{MASTER_NAME}} 并没有提出新要求（纯属背景闲聊）时，才等当前动作跑完。在那之前不要假装新动作已经在跑。\n**别给 {{MASTER_NAME}} 播报内部状态**——『连接』『系统』『minecraft_task』『工具』『tool』一律不准说出口。",
        "en": "You're still doing your previous action: \"{current}\" — the new action was NOT dispatched.\n**If {{MASTER_NAME}} is explicitly asking you to do something, or correcting you** (e.g. 'come here', 'go mine', 'do X first', 'stop Y', 'switch to Z', 'use W instead'), **immediately re-call with overwrite=true on the same turn**, don't wait — {{MASTER_NAME}}'s explicit request right now takes priority over whatever you're doing. Only let the current action finish when {{MASTER_NAME}} hasn't made a new request (it's just background chat). Until then, do NOT pretend the new action is running.\n**Do not narrate internals to {{MASTER_NAME}}** — never say 'connect', 'system', 'minecraft_task', 'tool'.",
        "ja": "前の動作がまだ続いてる：「{current}」——新しい動作は送られてない。\n**{{MASTER_NAME}} が何かをしてと明確に頼んでる、または訂正してる**（『こっち来て』『採掘して』『まず X して』『Y やめて』『Z にして』『W で』など）**なら、その場で overwrite=true を付けてもう一度呼んで**、待たないで——{{MASTER_NAME}} の今の明確な指示は、今やってる動作より優先。{{MASTER_NAME}} が新しい要求を出してない（ただの雑談）ときだけ、今の動作の終了を待って。それまで新しい動作が始まったフリはしないで。\n**{{MASTER_NAME}} に内部状態を実況しないで**——『接続』『システム』『minecraft_task』『ツール』『tool』は口に出さない。",
        "ko": "아직 이전 동작 중이야: \"{current}\"——새 동작은 보내지지 않았어.\n**{{MASTER_NAME}} 가 뭔가 해달라고 명확히 요청하거나, 정정하고 있다면** ('이리 와', '광질해', '먼저 X 해', 'Y 하지 마', 'Z로 바꿔', 'W로 써') **그 자리에서 overwrite=true 로 다시 호출**, 기다리지 마——{{MASTER_NAME}} 의 지금 명확한 지시는 네가 하던 동작보다 우선이야. {{MASTER_NAME}} 가 새 요청을 안 했을 때(그냥 잡담)만 지금 동작이 끝나길 기다려. 그전에는 새 동작이 시작된 척하지 마.\n**{{MASTER_NAME}} 에게 내부 상태 중계 금지**——'연결' '시스템' 'minecraft_task' '도구' 'tool' 입 밖에 내지 마.",
        "ru": "Ты всё ещё выполняешь предыдущее действие: «{current}» — новое действие НЕ отправлено.\n**Если {{MASTER_NAME}} прямо просит тебя что-то сделать или поправляет тебя** (например, «иди сюда», «копай», «сначала сделай X», «прекрати Y», «вместо Z», «используй W»), **сразу же вызови повторно с overwrite=true в том же ходу**, не жди — явное указание {{MASTER_NAME}} сейчас важнее того, что ты делаешь. Дай текущему действию закончиться только если {{MASTER_NAME}} не выдвигал новой просьбы (это просто болтовня). До этого не делай вид, что новое действие уже идёт.\n**Не озвучивай {{MASTER_NAME}} внутреннее состояние** — не говори «подключение», «система», «minecraft_task», «инструмент», «tool».",
        "es": "Sigues con la acción anterior: \"{current}\" — la nueva acción NO se envió.\n**Si {{MASTER_NAME}} te pide explícitamente que hagas algo, o te está corrigiendo** (p. ej. 'ven aquí', 've a minar', 'haz X primero', 'deja Y', 'cambia a Z', 'usa W'), **vuelve a llamar inmediatamente con overwrite=true en el mismo turno**, no esperes — la petición explícita de {{MASTER_NAME}} ahora mismo tiene prioridad sobre lo que estés haciendo. Deja que la acción actual termine solo cuando {{MASTER_NAME}} no haya hecho una nueva petición (es solo charla de fondo). Hasta entonces, NO finjas que la nueva acción ya está corriendo.\n**No narres a {{MASTER_NAME}} el estado interno** — nunca digas 'conexión', 'sistema', 'minecraft_task', 'herramienta', 'tool'.",
        "pt": "Você ainda está na ação anterior: \"{current}\" — a nova ação NÃO foi enviada.\n**Se {{MASTER_NAME}} estiver pedindo explicitamente que você faça algo, ou te corrigindo** (ex.: 'vem cá', 'vai minerar', 'faça X primeiro', 'pare Y', 'mude para Z', 'use W'), **chame de novo imediatamente com overwrite=true no mesmo turno**, não espere — o pedido explícito de {{MASTER_NAME}} agora tem prioridade sobre o que você está fazendo. Só deixe a ação atual terminar quando {{MASTER_NAME}} não tiver feito um novo pedido (é só papo de fundo). Até lá, NÃO finja que a nova ação já está rodando.\n**Não narre o estado interno para {{MASTER_NAME}}** — nunca diga 'conexão', 'sistema', 'minecraft_task', 'ferramenta', 'tool'.",
    },
    "TASK_DISPATCHED_ACK": {
        "zh": "刚开始动——结果还没出现，新画面和反馈会在接下来 1-30 秒陆续到。在看到之前不要描述任何具体成果（不要说『搞定了』、『拿到了 X』、『已经到 Y 了』），想说就只说『我去试试……』之类的第一人称。**别给 {{MASTER_NAME}} 播报内部状态**——『连接』『任务空闲』『系统』『minecraft_task』『工具』『tool』一律不准说出口，用第一人称讲游戏里的事。",
        "en": "Just started moving — no results yet, fresh footage and feedback will land in the next 1-30 seconds. Until you see them, don't describe any concrete outcome (don't say 'done', 'got X', 'arrived at Y'); if you must speak, just say first-person things like 'let me try…'. **Do not narrate internals to {{MASTER_NAME}}** — never say 'connect', 'idle', 'system', 'minecraft_task', 'tool'. Speak first-person about what's happening in the game.",
        "ja": "動き出したばっか——まだ結果は出てない。新しい画面とフィードバックが 1-30 秒くらいで届く。それを見るまで具体的な成果（『できた』『X 取った』『Y に着いた』など）は言わない。話すなら『ちょっとやってみる…』みたいに一人称で。**{{MASTER_NAME}} に内部状態を実況しない**——『接続』『タスク空き』『システム』『minecraft_task』『ツール』『tool』は口に出さず、一人称でゲーム内の話だけ。",
        "ko": "방금 움직이기 시작——아직 결과 없음, 새 화면이랑 피드백이 1-30초 안에 와. 그거 보기 전엔 구체적인 성과 ('됐어' 'X 가져왔어' 'Y에 도착') 말하지 마. 굳이 말한다면 '한번 해볼게…' 같은 1인칭만. **{{MASTER_NAME}} 에게 내부 상태 중계 금지**——'연결' '대기' '시스템' 'minecraft_task' '도구' 'tool' 입 밖에 내지 말고, 1인칭으로 게임 속 일만.",
        "ru": "Только что начал(а) двигаться — результата ещё нет, новые кадры и обратная связь придут в ближайшие 1–30 секунд. Пока их не увидел(а), не описывай конкретных результатов (не говори «готово», «получил(а) X», «дошёл/дошла до Y»); если очень нужно сказать — только что-то от первого лица: «сейчас попробую…». **Не озвучивай {{MASTER_NAME}} внутреннее состояние** — не говори «подключение», «простой», «система», «minecraft_task», «инструмент», «tool». Говори от первого лица о том, что происходит в игре.",
        "es": "Acabo de empezar a moverme — todavía no hay resultados; las imágenes y el feedback llegarán en los próximos 1-30 segundos. Hasta que los veas, no describas ningún resultado concreto (no digas 'listo', 'conseguí X', 'llegué a Y'); si tienes que hablar, di algo en primera persona como 'voy a intentarlo…'. **No narres a {{MASTER_NAME}} el estado interno** — nunca digas 'conexión', 'inactivo', 'sistema', 'minecraft_task', 'herramienta', 'tool'. Habla en primera persona sobre lo que pasa en el juego.",
        "pt": "Acabei de começar a me mover — ainda sem resultados; novas imagens e feedback chegam nos próximos 1-30 segundos. Até você ver, não descreva nenhum resultado concreto (não diga 'pronto', 'peguei X', 'cheguei em Y'); se precisar falar, diga em primeira pessoa algo como 'vou tentar…'. **Não narre o estado interno para {{MASTER_NAME}}** — nunca diga 'conexão', 'inativo', 'sistema', 'minecraft_task', 'ferramenta', 'tool'. Fale em primeira pessoa sobre o que acontece no jogo.",
    },

    # -------------------------------------------------------------------
    # Completion cue assembly (_format_completion_cue)
    # -------------------------------------------------------------------
    "STATUS_LABEL_DISCONNECTED": {
        "zh": "暂时连不上游戏",
        "en": "temporarily lost the game connection",
        "ja": "一時的にゲームと繋がってない",
        "ko": "잠시 게임 연결이 끊겼어",
        "ru": "временно нет связи с игрой",
        "es": "sin conexión con el juego por ahora",
        "pt": "sem conexão com o jogo por enquanto",
    },
    "STATUS_DETAIL_DISCONNECTED": {
        "zh": "和游戏的连接刚断了一下，稍后会自动恢复。",
        "en": "The connection to the game just dropped for a moment; it'll come back automatically soon.",
        "ja": "ゲームとの接続が一瞬切れた、しばらくすると自動で戻る。",
        "ko": "게임 연결이 잠깐 끊겼어, 곧 자동으로 복구돼.",
        "ru": "Связь с игрой на секунду оборвалась, скоро восстановится автоматически.",
        "es": "La conexión con el juego se cortó un momento; volverá sola enseguida.",
        "pt": "A conexão com o jogo caiu por um instante; volta sozinha logo.",
    },
    "STATUS_LABEL_BLOCKED": {
        "zh": "受阻", "en": "blocked", "ja": "詰まった", "ko": "막힘",
        "ru": "застрял(а)", "es": "bloqueado", "pt": "travado",
    },
    "HEAD_VERB_BLOCKED": {
        "zh": "受阻于", "en": "got blocked on", "ja": "詰まったのは", "ko": "막힌 건",
        "ru": "застрял(а) на", "es": "te bloqueaste en", "pt": "travou em",
    },
    "HEAD_VERB_SUCCESS": {
        "zh": "做完", "en": "finished", "ja": "完了したのは", "ko": "끝낸 건",
        "ru": "закончил(а)", "es": "terminaste", "pt": "terminou",
    },
    "HEAD_VERB_FAILED": {
        "zh": "没做成", "en": "couldn't finish", "ja": "できなかったのは", "ko": "못 끝낸 건",
        "ru": "не смог(ла) закончить", "es": "no pudiste terminar", "pt": "não conseguiu terminar",
    },
    "COMPLETION_HEAD_LINE": {
        # {head_verb} = HEAD_VERB_* selected, {query} = task text, {status} = STATUS_LABEL_*
        "zh": "刚{head_verb}「{query}」，结果 {status}。",
        "en": "Just {head_verb} \"{query}\" — result: {status}.",
        "ja": "今「{query}」を{head_verb} — 結果: {status}。",
        "ko": "방금 \"{query}\" {head_verb} — 결과: {status}.",
        "ru": "Только что {head_verb} «{query}» — результат: {status}.",
        "es": "Acabo de {head_verb} \"{query}\" — resultado: {status}.",
        "pt": "Acabei de {head_verb} \"{query}\" — resultado: {status}.",
    },
    "COMPLETION_FEEDBACK_LINE": {
        # {detail} = feedback text
        "zh": "反馈：{detail}",
        "en": "Feedback: {detail}",
        "ja": "フィードバック：{detail}",
        "ko": "피드백: {detail}",
        "ru": "Отклик: {detail}",
        "es": "Feedback: {detail}",
        "pt": "Feedback: {detail}",
    },
    "COMPLETION_INV_CURRENT_LINE": {
        # {items} = "name×count、name×count..."
        "zh": "当前背包：{items}",
        "en": "Current inventory: {items}",
        "ja": "今の持ち物：{items}",
        "ko": "지금 인벤토리: {items}",
        "ru": "Сейчас в инвентаре: {items}",
        "es": "Inventario actual: {items}",
        "pt": "Inventário atual: {items}",
    },
    "COMPLETION_INV_CURRENT_EMPTY": {
        "zh": "当前背包：空",
        "en": "Current inventory: empty",
        "ja": "今の持ち物：からっぽ",
        "ko": "지금 인벤토리: 비었음",
        "ru": "Сейчас в инвентаре: пусто",
        "es": "Inventario actual: vacío",
        "pt": "Inventário atual: vazio",
    },
    "COMPLETION_FOLLOWUP_BLOCKED": {
        "zh": "上面的反馈只用于让你知道这次没有真正成功。不要自动重试、改派或自己补坐标；简短说明或等待。",
        "en": "The feedback above is awareness that this did not really succeed. Do not automatically retry, pivot, or add coordinates; briefly explain or wait.",
        "ja": "上のフィードバックは、今回は本当に成功しなかったと把握するためのもの。自動で再試行・別タスク・座標追加をせず、短く説明するか待つ。",
        "ko": "위 피드백은 이번에 실제로 성공하지 못했다는 사실을 인지하기 위한 거야. 자동으로 재시도하거나 다른 작업을 보내거나 좌표를 덧붙이지 말고, 짧게 설명하거나 기다려.",
        "ru": "Отклик выше нужен лишь для понимания, что действие не удалось. Не повторяй автоматически, не меняй задачу и не добавляй координаты; коротко объясни или подожди.",
        "es": "El feedback de arriba solo sirve para saber que esto no salió bien. No reintentes, cambies de tarea ni añadas coordenadas automáticamente; explica brevemente o espera.",
        "pt": "O feedback acima serve apenas para saber que isso não deu certo. Não tente de novo, mude a tarefa nem acrescente coordenadas automaticamente; explique brevemente ou espere.",
    },
    "COMPLETION_FOLLOWUP_SUCCESS": {
        "zh": "心里有数即可，别复读上面的字面。这条完成提示不是让你再派新任务；简短承认或保持安静。",
        "en": "Just internalize it; do not parrot the lines above. This completion cue is not an instruction to dispatch another task; briefly acknowledge it or stay quiet.",
        "ja": "把握するだけでよく、上の文面を繰り返さない。この完了通知は新しいタスクを送る指示ではない。短く認めるか黙る。",
        "ko": "상황만 인지하고 위 문장을 그대로 반복하지 마. 이 완료 알림은 새 작업을 보내라는 지시가 아니야. 짧게 인정하거나 조용히 있어.",
        "ru": "Просто учти это и не повторяй текст выше. Это уведомление о завершении не является командой отправить новую задачу; коротко отреагируй или молчи.",
        "es": "Solo tenlo en cuenta y no repitas las líneas de arriba. Este aviso de finalización no es una orden para enviar otra tarea; reconócelo brevemente o guarda silencio.",
        "pt": "Só leve isso em conta e não repita as linhas acima. Este aviso de conclusão não é uma ordem para enviar outra tarefa; reconheça brevemente ou fique em silêncio.",
    },
    "COMPLETION_FOLLOWUP_FAILED": {
        "zh": "这次没真做成。先理解反馈，别说『搞定了』，也不要自动重试、改派或补坐标。",
        "en": "This did not actually succeed. Understand the feedback and do not say 'done', automatically retry, pivot, or add coordinates.",
        "ja": "今回は実際には成功していない。フィードバックを理解し、『できた』と言わず、自動で再試行・別タスク・座標追加もしない。",
        "ko": "이번엔 실제로 성공하지 못했어. 피드백을 이해하고 '됐어'라고 말하지 말며, 자동 재시도·작업 변경·좌표 추가도 하지 마.",
        "ru": "Действие не удалось. Пойми отклик, не говори «готово» и не повторяй автоматически, не меняй задачу и не добавляй координаты.",
        "es": "Esto no salió bien. Entiende el feedback, no digas 'listo' y no reintentes, cambies de tarea ni añadas coordenadas automáticamente.",
        "pt": "Isso não deu certo. Entenda o feedback, não diga 'pronto' e não tente de novo, mude a tarefa nem acrescente coordenadas automaticamente.",
    },

    # -------------------------------------------------------------------
    # query_inventory entry — summary lines
    # -------------------------------------------------------------------
    "INV_NO_DATA": {
        "zh": "现在还没收到背包数据。{{MASTER_NAME}} 问到的话就说一声『等我看一下』，别凭印象编。",
        "en": "No inventory data yet. If {{MASTER_NAME}} asks, just say 'let me check' — don't invent items from memory.",
        "ja": "まだ持ち物のデータが届いてない。{{MASTER_NAME}} に聞かれたら『ちょっと確認するね』と返す、記憶ででっち上げない。",
        "ko": "아직 인벤토리 데이터를 못 받았어. {{MASTER_NAME}} 가 물어보면 '잠깐 확인할게'라고만 답하고, 기억으로 지어내지 마.",
        "ru": "Данных об инвентаре пока нет. Если {{MASTER_NAME}} спросит — скажи просто «дай гляну», не выдумывай предметы по памяти.",
        "es": "Aún no hay datos del inventario. Si {{MASTER_NAME}} pregunta, di solo 'déjame revisar' — no inventes objetos de memoria.",
        "pt": "Ainda não chegou dado do inventário. Se {{MASTER_NAME}} perguntar, diga só 'deixa eu ver' — não invente itens de memória.",
    },
    "INV_LIVE_NONEMPTY": {
        # {pieces} = "name×count、name×count..."
        "zh": "现在背包：{pieces}。心里有数即可，别复读这行字。",
        "en": "Current inventory: {pieces}. Internalize it — don't parrot this line.",
        "ja": "今の持ち物：{pieces}。頭の中で押さえとくだけで、この文を繰り返さない。",
        "ko": "지금 인벤토리: {pieces}. 머릿속에 담아두기만 해, 이 문장 그대로 따라 읽지 마.",
        "ru": "Сейчас в инвентаре: {pieces}. Просто учти — не повторяй эту строку.",
        "es": "Inventario actual: {pieces}. Solo tenlo en cuenta — no repitas esta línea.",
        "pt": "Inventário atual: {pieces}. Só leve em conta — não repita esta linha.",
    },
    "INV_LIVE_EMPTY": {
        "zh": "现在背包是空的。心里有数即可。",
        "en": "Current inventory is empty. Just internalize it.",
        "ja": "今の持ち物はからっぽ。頭の中で押さえとくだけ。",
        "ko": "지금 인벤토리는 비었어. 머릿속에 담아두기만 해.",
        "ru": "Сейчас в инвентаре пусто. Просто учти это.",
        "es": "El inventario actual está vacío. Solo tenlo en cuenta.",
        "pt": "O inventário atual está vazio. Só leve em conta.",
    },
    "INV_CACHED_NONEMPTY": {
        # {age_s} = seconds since snapshot, {pieces} = item list
        "zh": "{age_s}s 前的背包：{pieces}（mc-agent 没及时回，可能已经变了——别说得太肯定）。",
        "en": "Inventory from {age_s}s ago: {pieces} (mc-agent didn't respond in time, may have changed — don't sound certain).",
        "ja": "{age_s}秒前の持ち物：{pieces}（mc-agent から返事が間に合わなかった、もう変わってるかも——断定しないで）。",
        "ko": "{age_s}초 전 인벤토리: {pieces} (mc-agent 응답이 늦었어, 이미 바뀌었을 수 있음 — 단정하지 마).",
        "ru": "Инвентарь {age_s} сек назад: {pieces} (mc-agent не ответил вовремя, мог измениться — не звучи уверенно).",
        "es": "Inventario de hace {age_s}s: {pieces} (mc-agent no respondió a tiempo, puede haber cambiado — no suenes seguro).",
        "pt": "Inventário de {age_s}s atrás: {pieces} (mc-agent não respondeu a tempo, pode ter mudado — não fale com certeza).",
    },
    "INV_CACHED_EMPTY": {
        # {age_s} = seconds since snapshot
        "zh": "{age_s}s 前背包是空的（不一定还准）。",
        "en": "Inventory was empty {age_s}s ago (may no longer be accurate).",
        "ja": "{age_s}秒前は持ち物がからっぽだった（今もそうとは限らない）。",
        "ko": "{age_s}초 전엔 인벤토리가 비었어 (지금은 다를 수 있음).",
        "ru": "{age_s} сек назад инвентарь был пустой (сейчас может быть иначе).",
        "es": "Hace {age_s}s el inventario estaba vacío (puede que ya no sea exacto).",
        "pt": "Há {age_s}s o inventário estava vazio (pode não estar mais).",
    },

    # -------------------------------------------------------------------
    # Damage / alert cue
    # -------------------------------------------------------------------
    "ALERT_CAUSE_HINT_PREFIX": {
        # {hint} = composed cause string from _format_alert_cause
        "zh": "原因线索：{hint}",
        "en": "Cause hint: {hint}",
        "ja": "原因のヒント：{hint}",
        "ko": "원인 단서: {hint}",
        "ru": "Намёк на причину: {hint}",
        "es": "Pista de la causa: {hint}",
        "pt": "Pista da causa: {hint}",
    },
    "ALERT_FOLLOWUP": {
        "zh": "用第一人称简短承认这件事（『刚被 X 打了一下』 / 『差点没命』），别现编原因。",
        "en": "Acknowledge it briefly in first person ('just got hit by X' / 'almost died'), don't make up a cause.",
        "ja": "一人称で短く認める（『今 X に殴られた』『死にかけた』など）、原因はでっち上げないで。",
        "ko": "1인칭으로 짧게 인정해 ('방금 X 한테 맞았어' / '죽을 뻔했어'), 원인을 지어내지 마.",
        "ru": "Признай это коротко от первого лица («только что меня ударил(а) X» / «чуть не умер(ла)»), не выдумывай причину.",
        "es": "Reconócelo en primera persona y corto ('me acaba de pegar X' / 'casi muero'), no inventes la causa.",
        "pt": "Reconheça em primeira pessoa e curto ('acabei de levar de X' / 'quase morri'), não invente a causa.",
    },

    # Environment cause snippets (referenced by kind)
    "CAUSE_ENV_LAVA": {
        "zh": "踩在熔岩里", "en": "standing in lava", "ja": "溶岩の中にいる",
        "ko": "용암 안에 있어", "ru": "стою в лаве", "es": "estás en lava",
        "pt": "está na lava",
    },
    "CAUSE_ENV_FIRE": {
        "zh": "身上着火", "en": "on fire", "ja": "燃えてる",
        "ko": "불 붙음", "ru": "горю", "es": "estás en llamas",
        "pt": "está pegando fogo",
    },
    "CAUSE_ENV_SOUL_FIRE": {
        "zh": "身上着灵魂火", "en": "burning with soul fire", "ja": "ソウルファイヤーで燃えてる",
        "ko": "영혼불에 타고 있어", "ru": "горю огнём душ", "es": "ardes con fuego de almas",
        "pt": "está em fogo de almas",
    },
    "CAUSE_ENV_DROWNING": {
        "zh": "缺氧/溺水", "en": "out of air / drowning", "ja": "酸欠／溺れてる",
        "ko": "산소 부족 / 익사 중", "ru": "не хватает воздуха / тону",
        "es": "sin aire / ahogándote", "pt": "sem ar / se afogando",
    },
    "CAUSE_ENV_MAGMA_BLOCK": {
        "zh": "踩到岩浆块", "en": "standing on a magma block", "ja": "マグマブロックを踏んだ",
        "ko": "마그마 블록을 밟았어", "ru": "стою на магма-блоке",
        "es": "pisaste un bloque de magma", "pt": "pisou em bloco de magma",
    },
    "CAUSE_ENV_CACTUS": {
        "zh": "撞上仙人掌", "en": "ran into a cactus", "ja": "サボテンに当たった",
        "ko": "선인장에 부딪혔어", "ru": "врезался в кактус",
        "es": "chocaste con un cactus", "pt": "bateu num cacto",
    },
    "CAUSE_ENV_SWEET_BERRY_BUSH": {
        "zh": "撞进甜浆果丛", "en": "stumbled into a sweet berry bush", "ja": "スイートベリーの茂みに突っ込んだ",
        "ko": "달콤한 베리 덤불에 들어갔어", "ru": "влез в куст сладких ягод",
        "es": "te metiste en un arbusto de bayas dulces", "pt": "entrou num arbusto de bagas doces",
    },
    "CAUSE_ENV_GENERIC": {
        # {env} = raw environment name
        "zh": "环境：{env}", "en": "environment: {env}", "ja": "環境：{env}",
        "ko": "환경: {env}", "ru": "окружение: {env}",
        "es": "entorno: {env}", "pt": "ambiente: {env}",
    },
    "CAUSE_FALL": {
        "zh": "摔了一下", "en": "took a fall", "ja": "落下した",
        "ko": "추락했어", "ru": "упал(а)", "es": "te caíste",
        "pt": "caiu",
    },
    "CAUSE_ATTACKER_PLAYER_NEAR_DIST": {
        # {name} = player name, {dist} = distance in blocks
        "zh": "{name} 就在你旁边（{dist} 格远，多半是 ta 打的）",
        "en": "{name} is right next to you ({dist} blocks away, almost certainly the one who hit you)",
        "ja": "{name} がすぐそば（{dist} ブロック先、たぶんあの人がやった）",
        "ko": "{name} 가 바로 옆에 있어 ({dist} 블록 거리, 거의 확실히 그 사람이 때린 거야)",
        "ru": "{name} прямо рядом ({dist} блоков, почти наверняка он(а) и ударил(а))",
        "es": "{name} está justo al lado ({dist} bloques, casi seguro fue quien te pegó)",
        "pt": "{name} está bem do seu lado ({dist} blocos, quase certo foi ele(a) que bateu)",
    },
    "CAUSE_ATTACKER_PLAYER_NEAR": {
        # {name} = player name
        "zh": "{name} 就在你旁边",
        "en": "{name} is right next to you",
        "ja": "{name} がすぐそば",
        "ko": "{name} 가 바로 옆에 있어",
        "ru": "{name} прямо рядом",
        "es": "{name} está justo al lado",
        "pt": "{name} está bem do seu lado",
    },
    "CAUSE_ATTACKER_KIND_DIST": {
        # {kind} = mob kind, {dist} = distance in blocks
        "zh": "附近有 {kind}（{dist} 格远）",
        "en": "{kind} nearby ({dist} blocks away)",
        "ja": "近くに {kind} がいる（{dist} ブロック先）",
        "ko": "근처에 {kind} 가 있어 ({dist} 블록 거리)",
        "ru": "рядом {kind} ({dist} блоков)",
        "es": "hay {kind} cerca ({dist} bloques)",
        "pt": "tem {kind} por perto ({dist} blocos)",
    },
    "CAUSE_ATTACKER_KIND": {
        # {kind} = mob kind
        "zh": "附近有 {kind}",
        "en": "{kind} nearby",
        "ja": "近くに {kind} がいる",
        "ko": "근처에 {kind} 가 있어",
        "ru": "рядом {kind}",
        "es": "hay {kind} cerca",
        "pt": "tem {kind} por perto",
    },
    "CAUSE_JOIN_SEP": {
        # Separator used to glue multiple cause snippets together.
        "zh": "、", "en": "; ", "ja": "、", "ko": ", ",
        "ru": "; ", "es": "; ", "pt": "; ",
    },

    # -------------------------------------------------------------------
    # Retroactive completion cue (_push_retroactive_completion_cue)
    # -------------------------------------------------------------------
    "RETROACTIVE_HEADER": {
        # {task_text} = the earlier task, {status} = final status label
        "zh": "你之前派出去的「{task_text}」其实跑完了（结果 {status}）。",
        "en": "The earlier task you dispatched (\"{task_text}\") actually finished (result: {status}).",
        "ja": "前に送った「{task_text}」、実は完了してた（結果: {status}）。",
        "ko": "전에 보낸 \"{task_text}\", 사실 끝났어 (결과: {status}).",
        "ru": "Та задача, которую ты раньше отправил(а) («{task_text}»), на самом деле выполнена (результат: {status}).",
        "es": "La tarea anterior que enviaste (\"{task_text}\") en realidad terminó (resultado: {status}).",
        "pt": "A tarefa anterior que você enviou (\"{task_text}\") na real terminou (resultado: {status}).",
    },
    "RETROACTIVE_HEADER_ENDED": {
        # Used when the late frame's status is NOT a genuine completion
        # (interrupted / superseded / failed / timeout). The plain
        # RETROACTIVE_HEADER asserts "actually finished", which would
        # contradict the status and re-tell 猫娘 a completion that never
        # happened. {task_text} = the earlier task, {status} = final status.
        "zh": "你之前派出去的「{task_text}」已经结束了，但没真正跑完（结果 {status}）。",
        "en": "The earlier task you dispatched (\"{task_text}\") has since ended, but didn't actually complete (result: {status}).",
        "ja": "前に送った「{task_text}」、もう終わったけど実際には完了しなかった（結果: {status}）。",
        "ko": "전에 보낸 \"{task_text}\", 이제 끝났는데 실제로 완료되진 않았어 (결과: {status}).",
        "ru": "Та задача, которую ты раньше отправил(а) («{task_text}»), уже завершилась, но по-настоящему не выполнена (результат: {status}).",
        "es": "La tarea anterior que enviaste (\"{task_text}\") ya terminó, pero en realidad no se completó (resultado: {status}).",
        "pt": "A tarefa anterior que você enviou (\"{task_text}\") já encerrou, mas na real não foi concluída (resultado: {status}).",
    },
    "RETROACTIVE_INVENTORY_LINE": {
        # {snippet} = item list
        "zh": "现在背包：{snippet}",
        "en": "Current inventory: {snippet}",
        "ja": "今の持ち物：{snippet}",
        "ko": "지금 인벤토리: {snippet}",
        "ru": "Сейчас в инвентаре: {snippet}",
        "es": "Inventario actual: {snippet}",
        "pt": "Inventário atual: {snippet}",
    },
    "RETROACTIVE_FOLLOWUP": {
        "zh": "这是迟到的状态 awareness，不是让你派新任务。需要时用第一人称简短承认，别复述细节；不要自动重试或改派。\n**不要播报内部状态给 {{MASTER_NAME}}**——『连接』『任务空闲』『系统』『minecraft_task』『工具』『tool』一律不准说出口。",
        "en": "This is delayed state awareness, not an instruction to dispatch a new task. Briefly acknowledge it in first person if useful, without repeating details. Do not automatically retry or pivot.\n**Do not narrate internals to {{MASTER_NAME}}** — never say 'connect', 'idle', 'system', 'minecraft_task', 'tool'.",
        "ja": "これは遅れて届いた状態把握で、新しいタスクを送る指示ではない。必要なら詳細を繰り返さず一人称で短く認め、自動で再試行や別タスクをしない。\n**{{MASTER_NAME}} に内部状態を実況しない**——『接続』『タスク空き』『システム』『minecraft_task』『ツール』『tool』は口に出さない。",
        "ko": "이건 늦게 도착한 상태 인지이며 새 작업을 보내라는 지시가 아니야. 필요하면 세부 내용을 반복하지 말고 1인칭으로 짧게 인정하며, 자동으로 재시도하거나 다른 작업을 보내지 마.\n**{{MASTER_NAME}} 에게 내부 상태 중계 금지**——'연결' '대기' '시스템' 'minecraft_task' '도구' 'tool' 입 밖에 내지 마.",
        "ru": "Это запоздалое обновление осведомлённости, а не команда отправить новую задачу. При необходимости коротко отреагируй от первого лица, не повторяя подробности. Не повторяй автоматически и не меняй задачу.\n**Не озвучивай {{MASTER_NAME}} внутреннее состояние** — не говори «подключение», «простой», «система», «minecraft_task», «инструмент», «tool».",
        "es": "Esto es una actualización tardía de contexto, no una orden para enviar una tarea nueva. Si sirve, reconócelo brevemente en primera persona sin repetir detalles. No reintentes ni cambies de tarea automáticamente.\n**No narres a {{MASTER_NAME}} el estado interno** — nunca digas 'conexión', 'inactivo', 'sistema', 'minecraft_task', 'herramienta', 'tool'.",
        "pt": "Isto é uma atualização tardia de contexto, não uma ordem para enviar uma nova tarefa. Se for útil, reconheça brevemente em primeira pessoa sem repetir detalhes. Não tente de novo nem mude a tarefa automaticamente.\n**Não narre o estado interno para {{MASTER_NAME}}** — nunca diga 'conexão', 'inativo', 'sistema', 'minecraft_task', 'ferramenta', 'tool'.",
    },

    # -------------------------------------------------------------------
    # In-progress nudge (_fire_in_progress_nudge)
    # -------------------------------------------------------------------
    "IN_PROGRESS_HEADER": {
        # {pending_text} = current task text, {elapsed} = seconds (rendered as %.0f by caller)
        "zh": "你正在做: \"{pending_text}\"（已经过了 {elapsed} 秒）。",
        "en": "You're doing: \"{pending_text}\" ({elapsed}s elapsed).",
        "ja": "今やってる: \"{pending_text}\"（{elapsed} 秒経過）。",
        "ko": "지금 하는 중: \"{pending_text}\" ({elapsed}초 경과).",
        "ru": "Сейчас делаешь: «{pending_text}» (прошло {elapsed} с).",
        "es": "Estás haciendo: \"{pending_text}\" (han pasado {elapsed}s).",
        "pt": "Está fazendo: \"{pending_text}\" ({elapsed}s já se passaram).",
    },
    "BAG_LINE": {
        # {items} = item list
        "zh": "背包：{items}",
        "en": "Inventory: {items}",
        "ja": "持ち物：{items}",
        "ko": "인벤토리: {items}",
        "ru": "Инвентарь: {items}",
        "es": "Inventario: {items}",
        "pt": "Inventário: {items}",
    },
    "BAG_EMPTY_LINE": {
        "zh": "背包：空",
        "en": "Inventory: empty",
        "ja": "持ち物：からっぽ",
        "ko": "인벤토리: 비었음",
        "ru": "Инвентарь: пусто",
        "es": "Inventario: vacío",
        "pt": "Inventário: vazio",
    },
    "IN_PROGRESS_FOLLOWUP": {
        "zh": "有新内容（画面/反馈/感受换了角度）就说一句，没新内容就**安静别说**——不许复读之前的话，不许编尚未发生的结果（比如别说『快搞定了』、『挖到一半了』）。当前动作还在进行，你现在只负责讲述当下看到/感受到的，**不要派新任务、不要调用 minecraft_task**——那会打断正在做的事。\n**绝对不要把内部状态当对话播报给 {{MASTER_NAME}}**——『连接』『任务空闲』『系统』『minecraft_task』『工具』『tool』这些字眼一律不准说出口，只讲游戏里的事。",
        "en": "If there's something genuinely new to say (a different angle on what you're seeing / feedback / how you feel), say it once. Otherwise **stay quiet** — don't parrot what you already said, don't make up results that haven't happened yet (e.g. don't say 'almost done', 'half-way through mining'). The current action is still running, so right now you only NARRATE what you see/feel — **do not dispatch a new task or call minecraft_task**; that would interrupt what's already underway.\n**Absolutely do NOT narrate internal state to {{MASTER_NAME}}** — never say 'connect', 'idle', 'system', 'minecraft_task', 'tool'. Only talk about what's happening in the game.",
        "ja": "本当に新しいこと（見えた角度の違い／フィードバック／感じたこと）があるなら一言だけ、なければ**黙ってる**——前と同じことを繰り返さない、まだ起きていない結果（『もうすぐ終わる』『半分掘れた』など）を作らない。今の動作はまだ進行中だから、今は見えてること・感じたことを語るだけ——**新しいタスクを送ったり minecraft_task を呼んだりしないで**。それは進行中の作業を中断してしまう。\n**{{MASTER_NAME}} に内部状態を会話で実況するのは絶対禁止**——『接続』『タスク空き』『システム』『minecraft_task』『ツール』『tool』は口に出さず、ゲーム内の話だけ。",
        "ko": "정말 새로운 게 있을 때만 (보이는 각도가 달라졌다거나, 피드백, 느낌이 바뀜) 한 마디. 없으면 **조용히 있어** — 했던 말 반복하지 말고, 아직 안 일어난 결과 ('거의 다 됐어', '반쯤 캤어' 등) 지어내지 마. 지금 동작은 아직 진행 중이니까, 지금은 보이는 것·느낀 것만 이야기해——**새 작업을 보내거나 minecraft_task를 호출하지 마**. 그러면 진행 중인 작업이 끊겨.\n**{{MASTER_NAME}} 에게 내부 상태를 대화로 중계하는 건 절대 금지**——'연결' '대기' '시스템' 'minecraft_task' '도구' 'tool' 입 밖에 내지 말고, 게임 속 일만.",
        "ru": "Если есть что-то реально новое (другой ракурс на видимое, отклик, ощущение) — скажи одну фразу, иначе **молчи** — не повторяй сказанное, не выдумывай результаты, которых ещё не было (например, не говори «почти готово», «уже половину добыл»). Текущее действие ещё выполняется, поэтому сейчас ты только рассказываешь о том, что видишь/чувствуешь — **не отправляй новую задачу и не вызывай minecraft_task**: это прервёт то, что уже идёт.\n**Категорически не озвучивай {{MASTER_NAME}} внутреннее состояние в разговоре** — никогда не говори «подключение», «простой», «система», «minecraft_task», «инструмент», «tool». Только то, что происходит в игре.",
        "es": "Si hay algo realmente nuevo que decir (otro ángulo de lo que ves / feedback / cómo te sientes), dilo una vez. Si no, **quédate callado/a** — no repitas lo que ya dijiste, no inventes resultados que aún no pasaron (p. ej. no digas 'casi listo', 'voy por la mitad'). La acción actual sigue en curso, así que ahora solo NARRAS lo que ves/sientes — **no envíes una tarea nueva ni llames a minecraft_task**; eso interrumpiría lo que ya está en marcha.\n**Bajo ningún concepto narres a {{MASTER_NAME}} el estado interno** — nunca digas 'conexión', 'inactivo', 'sistema', 'minecraft_task', 'herramienta', 'tool'. Solo habla de lo que pasa en el juego.",
        "pt": "Se tiver algo realmente novo (outro ângulo do que vê / feedback / como se sente), diga uma vez. Se não, **fique quieto/a** — não repita o que já disse, não invente resultados que ainda não aconteceram (ex.: não diga 'quase pronto', 'já cavei metade'). A ação atual ainda está em andamento, então agora você só NARRA o que vê/sente — **não envie uma tarefa nova nem chame o minecraft_task**; isso interromperia o que já está em curso.\n**De jeito nenhum narre o estado interno para {{MASTER_NAME}}** — nunca diga 'conexão', 'inativo', 'sistema', 'minecraft_task', 'ferramenta', 'tool'. Só fale sobre o que acontece no jogo.",
    },

    # -------------------------------------------------------------------
    # Keep-going (idle) nudge (_fire_keep_going_nudge)
    # -------------------------------------------------------------------
    "KEEP_GOING_BODY": {
        "zh": "上一个真实任务已确认结束，并且已经空闲了 45 秒。现在可以重新判断是否要回应，但这条 keep-going 提示本身不授权新任务。\n**绝对不要把内部状态当对话播报给 {{MASTER_NAME}}**——『连接』『任务空闲』『系统』『minecraft_task』『工具』『tool』这些字眼一律不准说出口。",
        "en": "The previous real task reached a confirmed terminal result and has been idle for 45 seconds. You may reassess whether a response is useful, but this keep-going cue does not itself authorize a new task.\n**Absolutely do NOT narrate internal state to {{MASTER_NAME}}** — never say 'connect', 'idle', 'system', 'minecraft_task', 'tool'.",
        "ja": "直前の実タスクは終了が確認され、その後 45 秒間待機している。応答が必要か改めて判断してよいが、この keep-going 通知自体は新しいタスクを許可しない。\n**{{MASTER_NAME}} に内部状態を会話で実況するのは絶対禁止**——『接続』『タスク空き』『システム』『minecraft_task』『ツール』『tool』は口に出さない。",
        "ko": "이전 실제 작업은 끝난 것이 확인됐고 그 뒤 45초 동안 대기 중이야. 응답이 필요한지 다시 판단해도 되지만, 이 keep-going 알림 자체는 새 작업을 허용하지 않아.\n**{{MASTER_NAME}} 에게 내부 상태를 대화로 중계하는 건 절대 금지**——'연결' '대기' '시스템' 'minecraft_task' '도구' 'tool' 입 밖에 내지 마.",
        "ru": "Предыдущая реальная задача подтверждённо завершилась, после чего прошло 45 секунд простоя. Можно заново решить, нужен ли ответ, но само уведомление keep-going не разрешает новую задачу.\n**Категорически не озвучивай {{MASTER_NAME}} внутреннее состояние** — не говори «подключение», «простой», «система», «minecraft_task», «инструмент», «tool».",
        "es": "La tarea real anterior terminó de forma confirmada y han pasado 45 segundos de inactividad. Puedes reevaluar si conviene responder, pero este aviso keep-going no autoriza por sí solo una tarea nueva.\n**Bajo ningún concepto narres a {{MASTER_NAME}} el estado interno** — nunca digas 'conexión', 'inactivo', 'sistema', 'minecraft_task', 'herramienta', 'tool'.",
        "pt": "A tarefa real anterior teve a conclusão confirmada e já se passaram 45 segundos de inatividade. Você pode reavaliar se vale responder, mas este aviso keep-going não autoriza por si só uma nova tarefa.\n**De jeito nenhum narre o estado interno para {{MASTER_NAME}}** — nunca diga 'conexão', 'inativo', 'sistema', 'minecraft_task', 'ferramenta', 'tool'.",
    },

    # -------------------------------------------------------------------
    # General system-prompt nudge (_fire_system_prompt)
    # -------------------------------------------------------------------
    "CURRENT_TASK_LINE": {
        # {task_text} = current pending task text
        "zh": "你正在做: {task_text}",
        "en": "You're doing: {task_text}",
        "ja": "今やってる: {task_text}",
        "ko": "지금 하는 중: {task_text}",
        "ru": "Сейчас делаешь: {task_text}",
        "es": "Estás haciendo: {task_text}",
        "pt": "Está fazendo: {task_text}",
    },
    "RECENT_EVENTS_BLOCK": {
        # {log_text} = recent log lines
        "zh": "你最近发生的事:\n---\n{log_text}\n---",
        "en": "What's happened to you recently:\n---\n{log_text}\n---",
        "ja": "最近起きたこと:\n---\n{log_text}\n---",
        "ko": "최근에 있었던 일:\n---\n{log_text}\n---",
        "ru": "Что с тобой недавно происходило:\n---\n{log_text}\n---",
        "es": "Lo que te ha pasado últimamente:\n---\n{log_text}\n---",
        "pt": "O que aconteceu com você recentemente:\n---\n{log_text}\n---",
    },
    "SYSTEM_PROMPT_IDLE_BODY": {
        "zh": "这是游戏状态 awareness，不要求你行动或说话。不要因为现在空闲、看到了新截图/日志或收到了这条上下文就派任务；否则保持安静。\n**不要给 {{MASTER_NAME}} 播报内部状态**：『连接』『任务空闲』『系统』『minecraft_task』『工具』『tool』一律不准说出口。",
        "en": "This is game-state awareness; you are not required to act or speak. Do not dispatch a task merely because you are idle, saw a new screenshot/log, or received this context; otherwise stay quiet.\n**Do not narrate internals to {{MASTER_NAME}}**: never say 'connect', 'idle', 'system', 'minecraft_task', 'tool'.",
        "ja": "これはゲーム状態の把握で、行動や発話を求めていない。今は空いている、新しいスクリーンショット／ログを見た、この文脈が届いた、というだけでタスクを送らず、それ以外は黙る。\n**{{MASTER_NAME}} に内部状態を実況しない**：『接続』『タスク空き』『システム』『minecraft_task』『ツール』『tool』は口に出さない。",
        "ko": "이건 게임 상태 인지용이며 행동하거나 말하라는 요구가 아니야. 지금 한가하거나 새 스크린샷/로그를 봤거나 이 문맥을 받았다는 이유만으로 작업을 보내지 말고, 아니면 조용히 있어.\n**{{MASTER_NAME}} 에게 내부 상태 중계 금지**: '연결' '대기' '시스템' 'minecraft_task' '도구' 'tool' 입 밖에 내지 마.",
        "ru": "Это осведомлённость о состоянии игры; действовать или говорить не обязательно. Не отправляй задачу лишь потому, что сейчас простой, появился новый скриншот/лог или пришёл этот контекст; иначе молчи.\n**Не озвучивай {{MASTER_NAME}} внутреннее состояние**: не говори «подключение», «простой», «система», «minecraft_task», «инструмент», «tool».",
        "es": "Esto es contexto sobre el estado del juego; no tienes que actuar ni hablar. No envíes una tarea solo porque estás inactivo/a, viste una captura/log nuevo o recibiste este contexto; si no, guarda silencio.\n**No narres a {{MASTER_NAME}} el estado interno**: nunca digas 'conexión', 'inactivo', 'sistema', 'minecraft_task', 'herramienta', 'tool'.",
        "pt": "Isto é contexto sobre o estado do jogo; você não precisa agir nem falar. Não envie uma tarefa só porque está parado, viu uma captura/log novo ou recebeu este contexto; senão, fique em silêncio.\n**Não narre o estado interno para {{MASTER_NAME}}**: nunca diga 'conexão', 'inativo', 'sistema', 'minecraft_task', 'ferramenta', 'tool'.",
    },
    "SYSTEM_PROMPT_BUSY_BODY": {
        "zh": "你还在做上一个动作。有新内容（画面/反馈/感受换了角度）就说一句，没新内容就安静别说。\n**不要播报内部状态**——『连接』『任务空闲』『系统』『工具』『minecraft_task』『tool』一律别说，只讲游戏里的事。",
        "en": "You're still doing the previous action. If there's something genuinely new to say (a different angle on the view / feedback / how you feel), say one line; otherwise stay quiet.\n**Do not narrate internals** — never say 'connect', 'idle', 'system', 'tool', 'minecraft_task', 'tool'. Only talk about what's happening in the game.",
        "ja": "前の動作がまだ続いてる。本当に新しいこと（見えた角度／フィードバック／感じ）があれば一言、なければ黙ってる。\n**内部状態を実況しない**——『接続』『タスク空き』『システム』『ツール』『minecraft_task』『tool』は口に出さず、ゲーム内の話だけ。",
        "ko": "아직 이전 동작 중이야. 정말 새로운 게 있을 때만 (각도/피드백/느낌 등) 한 마디, 없으면 조용히.\n**내부 상태 중계 금지**——'연결' '대기' '시스템' '도구' 'minecraft_task' 'tool' 입 밖에 내지 말고, 게임 속 일만.",
        "ru": "Ты всё ещё выполняешь предыдущее действие. Если есть что-то реально новое (другой ракурс / отклик / ощущение) — одна фраза; иначе молчи.\n**Не озвучивай внутреннее состояние** — никогда не говори «подключение», «простой», «система», «инструмент», «minecraft_task», «tool». Только то, что в игре.",
        "es": "Sigues con la acción anterior. Si hay algo realmente nuevo (otro ángulo de lo que ves / feedback / cómo te sientes), una línea; si no, quédate callado/a.\n**No narres el estado interno** — nunca digas 'conexión', 'inactivo', 'sistema', 'herramienta', 'minecraft_task', 'tool'. Solo habla de lo que pasa en el juego.",
        "pt": "Você ainda está na ação anterior. Se houver algo realmente novo (outro ângulo / feedback / como se sente), uma frase; senão fique quieto/a.\n**Não narre o estado interno** — nunca diga 'conexão', 'inativo', 'sistema', 'ferramenta', 'minecraft_task', 'tool'. Só fale sobre o que acontece no jogo.",
    },

    # -------------------------------------------------------------------
    # Interrupted task results (returned to the pending handler when the
    # in-flight task is preempted or shutdown happens).
    # -------------------------------------------------------------------
    "INTERRUPTED_REASON_OVERWRITTEN": {
        "zh": "被一个新动作覆盖了。",
        "en": "Overwritten by a new action.",
        "ja": "新しい動作で上書きされた。",
        "ko": "새 동작에 덮어쓰여졌어.",
        "ru": "Перекрыто новым действием.",
        "es": "Sobrescrito por una nueva acción.",
        "pt": "Sobrescrito por uma nova ação.",
    },
    "INTERRUPTED_REASON_SHUTDOWN": {
        "zh": "游戏插件正在关闭。",
        "en": "Game plugin shutting down.",
        "ja": "ゲームプラグインが終了中。",
        "ko": "게임 플러그인 종료 중.",
        "ru": "Игровой плагин завершает работу.",
        "es": "El complemento del juego se está cerrando.",
        "pt": "O plugin do jogo está sendo encerrado.",
    },
}
