# -*- coding: utf-8 -*-
"""
User-directive 抽取的 i18n 正则模板 + 下一轮会话注入用的 system prompt 片段。

设计动机
--------
用户偶尔会显式说"别再提 X / 不要叫我 X / stop saying X / その話はもう"——
这些都是显式的 ban-topic 指令。本轮 LLM 看得到原话不需要处理；但等到**下一轮
会话重启**（archive / cold start / 重连），那句话早就被 compress 掉了，模型
会再次踩雷。

落点：在 user_utterance 入口跑正则抽取 → 命中 → 写进
``memory/{name}/user_directives.json``（3 天 TTL，``memory/user_directives.py``
负责存储）。下次 ``_build_initial_prompt`` 把活跃条目拼成一段注入到 system
prompt 末尾。

约定：宁可错杀
--------------
- 所有 locale 模板**并行**跑，不依赖检测语言（用户中英混说很常见）
- 抓到的 term 只做轻量 trim（剥两端标点 + 语气词），不做语义校验
- term 长度 ∈ [2, 40] 才入库；越界丢弃
- 正则只覆盖**带具体对象**的指令（ban_topic）。无对象的"闭嘴/换话题/shut up"
  本身在 context 已经被 LLM 看到，又不适合持久化，**不**抽取
- 错杀代价 = 用户下次再说一遍；模型代价 = system prompt 多一行；
  漏抽代价 = 用户被再次冒犯。所以倾向于宽松。
"""
from __future__ import annotations

import re
from typing import List, Tuple


# 抓到 term 后剥两端的字符：标点 + 各语言语气助词 / 修饰小尾巴。
# 全在尾部 strip，不影响中间内容。
_TRIM_TRAIL = (
    # ASCII / CJK 标点 / 空白
    " \t\n\r"
    ".,!?;:\"'`()[]{}<>"
    "。！？，；：、…—·"
    "“”‘’（）【】《》「」『』"
)
# zh / ja 助词、句末 particle（出现在 term 尾部时一并剥掉）
_TRIM_TRAIL_TOKENS = (
    # zh
    "了", "啊", "呀", "吧", "嘛", "哦", "呗", "啦", "呢", "嘞", "诶",
    # ja
    "ね", "よ", "わ", "の", "って", "なんて", "という",
    # ko
    "요", "은", "는", "이", "가", "을", "를", "에", "에서",
    # ru (鲜见词尾 particle)
    # es
    "porfa", "porfavor",
    # pt
    # en
    "please",
)


def _norm_lang(lang: str) -> str:
    """归一化 lang code（``zh-CN`` → ``zh``、``pt-BR`` → ``pt`` 等）。

    本模块的 3 个 render 函数都靠 dict 精确 key 取模板；如果上游把
    ``user_language`` 直接传过来（带 region 后缀），会全部走英文兜底——这是
    用户可见的回归。在边界归一化一次，比要求所有调用方都先 normalize 更稳。
    """
    try:
        from config._runtime import normalize_language_code as _nlc
        out = _nlc(lang, format='short')
        return out or 'en'
    except Exception:
        return lang or 'en'


def _trim_term(term: str) -> str:
    """裁剪 term：先剥尾部 particle / 修饰词，再剥两端标点 + 空白。"""
    if not term:
        return ""
    s = term.strip()
    changed = True
    # 反复剥尾词，直到稳定（"了啊吧" 这种连续助词）
    while changed:
        changed = False
        for tok in _TRIM_TRAIL_TOKENS:
            if s.endswith(tok) and len(s) > len(tok):
                s = s[: -len(tok)].rstrip()
                changed = True
        # 同时剥两端标点
        new_s = s.strip(_TRIM_TRAIL)
        if new_s != s:
            s = new_s
            changed = True
    return s.strip()


# ---------------------------------------------------------------------------
# 正则模板：(locale, kind, compiled_pattern, capture_group_index)
#
# 每条 pattern 必须有一个 capture group 给 term。
# kind 目前只有 ``ban_topic``（带 term）；将来若加 ``rename_request`` 等
# 在此扩展。
# ---------------------------------------------------------------------------

# 各 locale 内的"动词块"（说/提/talk about/言う/...）由各 locale 自己列。
# pattern 全部 re.compile 以 IGNORECASE / UNICODE 跑。

_PATTERNS_RAW: List[Tuple[str, str, str]] = [
    # ---------- zh ----------
    # 别/不要/不许/不准 + （再）+ 动词 + 对象
    # terminator 不放 ``\s``：zh 句子里中英混说时（"别叫我 John Smith"）lazy
    # ``(.{1,40}?)`` 会在第一个空格切断成 "John"。让终结符必须是标点 / EOL /
    # 句末助词，多词 NP 才能被完整捕获（codex P2）。
    ("zh", "ban_topic",
     r"(?:别|不要|不许|不准|莫|休|甭)\s*(?:再)?\s*"
     r"(?:说|提|聊|讲|谈|讨论|扯|提起|提及|讲到|聊到|谈起|谈到|说起|说到|喊我|叫我|管我叫|称呼我为?)\s*"
     r"(.{1,40}?)(?:\s*(?:了|啊|呀|嘛|哦|呗|吧|啦|呢))?(?:[，。！？；,.!?;]|\s*$)"),
    # X + 这个? + 别(再)+ 提
    ("zh", "ban_topic",
     r"(.{1,30}?)\s*(?:这个|这事|这话题|这件事)?\s*别\s*(?:再)?\s*"
     r"(?:说|提|聊|讲|提了|提起|提及)\s*(?:了)?(?:[，。！？；,.!?;\s]|$)"),
    # 不想/不愿 + 聊/讨论 + X — 同上：terminator 不要 \s，否则多词 NP 被切
    ("zh", "ban_topic",
     r"(?:我)?\s*(?:不想|不愿意|不愿|懒得|没心情)\s*(?:再)?\s*"
     r"(?:说|提|聊|讲|谈|讨论)\s*(.{1,40}?)(?:\s*(?:了|的事))?(?:[，。！？；,.!?;]|\s*$)"),
    # 关于 X + 别(再)+ 说
    ("zh", "ban_topic",
     r"关于\s*(.{1,30}?)\s*(?:的事)?\s*(?:就)?\s*别\s*(?:再)?\s*"
     r"(?:说|提|聊|讲)\s*(?:了)?(?:[，。！？；,.!?;\s]|$)"),

    # ---------- en ----------
    # stop/don't/quit + verb + (about|saying) + X
    # ``X`` 是英文 NP，常带空格（"my ex"、"the weather"）。terminator 用
    # filler-word / 标点 / 句尾，避免 lazy ``.{1,40}?`` 在 X 内的第一个空格就
    # 切断成 "my"。
    ("en", "ban_topic",
     r"(?:please\s+)?(?:stop|quit|don'?t|do\s+not|no\s+more)\s+"
     r"(?:talking\s+about|talk\s+about|saying|say|mentioning|mention|"
     r"bringing\s+up|bring\s+up|going\s+on\s+about|"
     r"calling\s+me\s+a|calling\s+me|call\s+me\s+a|call\s+me)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:again|anymore|any\s+more|please|ever|already|now|"
     r"forever|today|tonight|right\s+now|in\s+(?:front|public))"
     r"|[,.!?;]|$)"),
    # X + is off limits / off the table / not a topic
    ("en", "ban_topic",
     r"(.{1,30}?)\s+is\s+(?:off[\s\-]?limits|off\s+the\s+table|a\s+(?:no[\s\-]?go|forbidden)\s+topic)"
     r"(?:[\s,.!?;]|$)"),
    # I don't want to talk/hear about X
    # X 是 NP 可能含空格（"my ex girlfriend"）。terminator 用 filler-word /
    # 标点 / 句尾，否则 lazy ``.{1,40}?`` 在第一个空格就切断成 "my"（codex P1）。
    ("en", "ban_topic",
     r"i\s+(?:don'?t|do\s+not|really\s+don'?t)\s+(?:want\s+to|wanna)\s+"
     r"(?:talk|hear|discuss|think)\s+(?:about|of)\s+(.{1,40}?)"
     r"(?:\s+(?:anymore|any\s+more|again|ever|already|right\s+now|today|tonight|please)"
     r"|[,.!?;]|$)"),
    # drop the X / leave X alone (subject)
    ("en", "ban_topic",
     r"(?:drop|leave\s+alone)\s+(?:the\s+|that\s+)?(.{1,30}?)\s+"
     r"(?:topic|subject|thing|stuff|already)(?:[\s,.!?;]|$)"),

    # ---------- ja ----------
    # X + のこと/について + は + もう + 言わないで/やめて/しないで
    ("ja", "ban_topic",
     r"(.{1,40}?)\s*(?:のこと|の話|について|に関して|っていう話)\s*"
     r"(?:は)?\s*(?:もう|二度と|これ以上)?\s*"
     r"(?:言わないで|話さないで|しないで|やめて|止めて|よして|聞きたくない|触れないで)"),
    # もう + X + (の話) + (は) + 嫌だ/聞きたくない
    ("ja", "ban_topic",
     r"もう\s*(.{1,40}?)\s*(?:のこと|の話)?\s*(?:は)?\s*"
     r"(?:嫌|いや|聞きたくない|話したくない|やめて)"),
    # X + って + 呼ばないで / 言わないで
    ("ja", "ban_topic",
     r"(.{1,30}?)\s*(?:って|とは|なんて)\s*"
     r"(?:呼ばないで|言わないで|呼ぶな|言うな)"),

    # ---------- ko ----------
    # X + (에 대해|얘기|이야기) + (는)? + 그만 / 하지 마 / 꺼내지 마
    ("ko", "ban_topic",
     r"(.{1,40}?)\s*(?:에\s*대해서?|얘기|이야기|소리|말)\s*(?:는|은)?\s*"
     r"(?:그만|하지\s*마(?:세요|십시오)?|꺼내지\s*마(?:세요)?|관두|치워)"),
    # 다시는 + X + 말하지 마 / 꺼내지 마
    ("ko", "ban_topic",
     r"(?:다시는|두\s*번\s*다시|이제)\s*(.{1,40}?)\s*"
     r"(?:말하지|꺼내지|언급하지)\s*마(?:세요|십시오)?"),
    # X + (이|가)? + 듣기 싫다 / 짜증나
    ("ko", "ban_topic",
     r"(.{1,30}?)\s*(?:이|가)?\s*(?:듣기\s*싫|말하기\s*싫|짜증나|지긋지긋)"),

    # ---------- ru ----------
    # не говори / хватит про / прекрати + (preposition)? + X
    # 介词 "про / о / об / обо" 出现在动词后 + term 前，必须先 consume 才能
    # 让 (.{1,40}?) 捕获到实际话题；否则贪心地把介词当 term。
    # term 用 en 同款 filler-word terminator，支持 "моей бывшей" 这类多词短语。
    ("ru", "ban_topic",
     r"(?:не\s+(?:говори|упоминай|повторяй|произноси|обсуждай|называй\s+меня)|"
     r"хватит\s+(?:говорить|обсуждать|упоминать)|"
     r"перестань\s+(?:говорить|обсуждать|упоминать|называть\s+меня)|"
     r"прекрати\s+(?:говорить|обсуждать|упоминать|называть\s+меня))\s+"
     r"(?:про\s+|обо?\s+|о\s+)?"  # 可选介词
     r"(.{1,40}?)"
     r"(?:\s+(?:больше|никогда|пожалуйста|снова|опять|вообще|сегодня)"
     r"|[,.!?;]|$)"),
    # о X + больше + не говори
    ("ru", "ban_topic",
     r"(?:обо|об|о)\s+(.{1,30}?)\s+больше\s+не\s+(?:говори|упоминай)"),
    # я не хочу + (говорить|слышать) + о X — 同 en 的 filler-word terminator，
    # 支持 "моей бывшей" 这种多词短语。
    ("ru", "ban_topic",
     r"я\s+не\s+хочу\s+(?:говорить|слышать|обсуждать)\s+(?:обо|об|о)\s+(.{1,40}?)"
     r"(?:\s+(?:больше|никогда|пожалуйста|снова|опять|вообще|сегодня)"
     r"|[,.!?;]|$)"),

    # ---------- es ----------
    # no hables / no menciones / deja de hablar + (de|sobre) + X
    ("es", "ban_topic",
     r"(?:no\s+(?:hables|menciones|digas|sigas\s+hablando|me\s+llames)|"
     r"deja\s+de\s+(?:hablar|mencionar|llamarme)|"
     r"para\s+de\s+(?:hablar|mencionar))\s+"
     r"(?:de|sobre|acerca\s+de)?\s*(.{1,40}?)"
     r"(?:\s+(?:más|nunca|jamás|otra\s+vez|de\s+nuevo|por\s+favor|porfa|hoy|ahora)"
     r"|[,.!?;]|$)"),
    # no quiero + (oír|hablar|saber) + (de|nada de) + X — 同 en/ru
    ("es", "ban_topic",
     r"no\s+quiero\s+(?:oír|hablar|saber|escuchar)\s+(?:nada\s+)?(?:de|sobre)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:más|nunca|jamás|otra\s+vez|de\s+nuevo|por\s+favor|porfa|hoy|ahora)"
     r"|[,.!?;]|$)"),

    # ---------- pt ----------
    # não fale / não mencione / pare de falar + (de|sobre) + X
    ("pt", "ban_topic",
     r"(?:não\s+(?:fale|mencione|diga|continue\s+falando|me\s+chame)|"
     r"pare\s+de\s+(?:falar|mencionar|me\s+chamar)|"
     r"deix[ea]\s+de\s+(?:falar|mencionar))\s+"  # deixe de / deixa de（codex P2）
     r"(?:de|sobre|a\s+respeito\s+de)?\s*(.{1,40}?)"
     r"(?:\s+(?:mais|nunca|jamais|de\s+novo|outra\s+vez|por\s+favor|hoje|agora)"
     r"|[,.!?;]|$)"),
    # não quero + (ouvir|falar|saber) + (de|sobre|nada de) + X — 同 en/ru
    ("pt", "ban_topic",
     r"não\s+quero\s+(?:ouvir|falar|saber|escutar)\s+(?:nada\s+)?(?:de|sobre)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:mais|nunca|jamais|de\s+novo|outra\s+vez|por\s+favor|hoje|agora)"
     r"|[,.!?;]|$)"),
]


# 编译期一次性 compile，运行时直接复用。
DIRECTIVE_PATTERNS: List[Tuple[str, str, "re.Pattern[str]"]] = [
    (locale, kind, re.compile(raw, re.IGNORECASE | re.UNICODE))
    for locale, kind, raw in _PATTERNS_RAW
]


def extract_directives(text: str) -> List[Tuple[str, str, str]]:
    """对一段 user 文本跑所有 locale × kind 模板，返回 ``[(locale, kind, term)]``。

    - 所有模板**并行**尝试，不预先检测语言
    - 命中后 term 经 ``_trim_term`` 清洗，长度必须 ∈ [2, 40]
    - 同一 ``(kind, term_lower)`` 在结果列表里只保留一次（保留首个匹配的 locale，
      因为重复入库由 ``UserDirectivesManager.record`` 再去重一遍）

    重复模式是有意为之：upstream 多语言混说时一句话可能命中多个 locale 的
    pattern；这里先去重避免一句话灌出 5 条记录，但同一句话**不同**的 term
    （"别提小明和小红"）仍会各自被记录——前提是模板能拆出两次匹配。
    """
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    out: List[Tuple[str, str, str]] = []
    for locale, kind, pat in DIRECTIVE_PATTERNS:
        for m in pat.finditer(text):
            try:
                term_raw = m.group(1)
            except IndexError:
                continue
            term = _trim_term(term_raw)
            if not (2 <= len(term) <= 40):
                continue
            key = (kind, term.casefold())
            if key in seen:
                continue
            seen.add(key)
            out.append((locale, kind, term))
    return out


# ---------------------------------------------------------------------------
# 下一轮会话注入用的 system prompt 片段
# ---------------------------------------------------------------------------
# 历史的"用户最近表示不想聊"列表会被拼成 ``- {term1}\n- {term2}\n``，再用
# 各 locale 的模板包一层 header / footer。两个槽位：
#   {items}     —— bullet list
#   {n}         —— 条数（少数语言语法需要单复数）
#
# 渲染层：UserDirectivesManager.render_prompt_block(lanlan_name, lang)。

USER_DIRECTIVES_PROMPT_BLOCK = {
    'zh': (
        "\n\n[用户最近明确表示过不想聊或不喜欢被提到以下内容（共{n}项）]\n"
        "{items}\n"
        "请在本次会话里主动避开这些话题或称呼，除非用户自己重新提起。"
    ),
    'en': (
        "\n\n[The user recently asked not to discuss or be referred to as the "
        "following ({n} item(s))]\n"
        "{items}\n"
        "Please actively steer clear of these topics or labels in this session, "
        "unless the user brings them up again."
    ),
    'ja': (
        "\n\n[最近、ユーザーが話したくない・呼ばれたくないと明示した内容（{n}件）]\n"
        "{items}\n"
        "今回のセッションでは、ユーザー自身が再び話題にしない限り、"
        "これらの話題や呼び方を能動的に避けてください。"
    ),
    'ko': (
        "\n\n[사용자가 최근에 언급하지 말거나 그렇게 부르지 말라고 명확히 요청한 항목 ({n}개)]\n"
        "{items}\n"
        "이번 세션에서는 사용자가 직접 다시 꺼내지 않는 한, "
        "이러한 화제나 호칭을 적극적으로 피해 주세요."
    ),
    'ru': (
        "\n\n[Пользователь недавно явно просил не обсуждать или не называть "
        "следующее ({n} шт.)]\n"
        "{items}\n"
        "В этой сессии активно избегайте этих тем и обращений, "
        "если пользователь сам к ним не вернётся."
    ),
    'es': (
        "\n\n[El usuario pidió explícitamente no hablar de o no ser llamado/a "
        "con lo siguiente ({n} elemento(s))]\n"
        "{items}\n"
        "Evita activamente estos temas o etiquetas en esta sesión, "
        "salvo que el propio usuario los vuelva a sacar."
    ),
    'pt': (
        "\n\n[O usuário pediu explicitamente para não falar sobre ou ser "
        "chamado(a) pelo seguinte ({n} item(ns))]\n"
        "{items}\n"
        "Evite ativamente esses tópicos ou rótulos nesta sessão, "
        "a menos que o próprio usuário volte a mencioná-los."
    ),
}


def render_directives_block(terms: List[str], lang: str) -> str:
    """把 active term 列表渲染成一段 system-prompt 文本（含 leading newlines）。

    空列表 → 返回 ""（调用方直接 concat，不需要判空）。
    ``lang`` 接受完整 locale（``zh-CN`` 等），内部归一化为 short code。
    """
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = USER_DIRECTIVES_PROMPT_BLOCK.get(short) or USER_DIRECTIVES_PROMPT_BLOCK['en']
    items = "\n".join(f"- {t}" for t in terms)
    return template.format(items=items, n=len(terms))


# ---------------------------------------------------------------------------
# 防复读（anti-repeat）— 注入"最近高频 topic 词"提示
# ---------------------------------------------------------------------------
# 来源：``memory.anti_repeat.AntiRepeatCorpus.top_recent_topics``。注入位置同
# ``USER_DIRECTIVES_PROMPT_BLOCK`` —— ``_build_initial_prompt`` 末尾、ban list
# 之后。proactive 与 regular reply 共用：proactive 还会被 BM25 总分阈值
# 拦截（regen / drop），regular 只靠这段 prompt 软约束。
#
# 这段的语气和 ban list 不一样：ban list 是"用户明确说过别提"，必须强约束；
# 这里只是"你最近聊过这些，换些角度更好"，建议性的，不要太重，否则把 LLM
# 引导成话题切换疯子。

RECENT_TOPIC_HINT_PROMPT_BLOCK = {
    'zh': (
        "\n\n[最近几轮你已经聊过的话题（{n}项）]\n"
        "{items}\n"
        "如果还没必要，尽量换个角度或换个话题，避免连续围绕同一主题打转。"
    ),
    'en': (
        "\n\n[Topics you've already touched on in the last few turns ({n})]\n"
        "{items}\n"
        "Unless still relevant, try a fresh angle or a new topic rather than "
        "circling back to the same one."
    ),
    'ja': (
        "\n\n[最近のターンで既に触れた話題（{n}件）]\n"
        "{items}\n"
        "まだ必要でなければ、同じ話題を繰り返さず、別の切り口や新しい話題に"
        "切り替えてみてください。"
    ),
    'ko': (
        "\n\n[최근 몇 턴 동안 이미 다룬 화제 ({n}개)]\n"
        "{items}\n"
        "꼭 필요하지 않다면 같은 주제를 맴돌지 말고 다른 각도나 새로운 화제로"
        "전환해 보세요."
    ),
    'ru': (
        "\n\n[Темы, которые вы уже затронули за последние ходы ({n} шт.)]\n"
        "{items}\n"
        "Если в этом нет необходимости, попробуйте новый ракурс или другую "
        "тему, не кружите вокруг одной и той же."
    ),
    'es': (
        "\n\n[Temas que ya tocaste en los últimos turnos ({n} elemento(s))]\n"
        "{items}\n"
        "Salvo que sea necesario, prueba un ángulo distinto o un tema nuevo "
        "en lugar de volver al mismo."
    ),
    'pt': (
        "\n\n[Tópicos que você já abordou nos últimos turnos ({n} item(ns))]\n"
        "{items}\n"
        "A menos que ainda seja relevante, tente um ângulo novo ou outro "
        "tópico em vez de voltar ao mesmo."
    ),
}


def render_recent_topics_block(terms: List[str], lang: str) -> str:
    """把"最近 topic 词"列表渲染成 system-prompt 片段；空列表 → ""。"""
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = RECENT_TOPIC_HINT_PROMPT_BLOCK.get(short) or RECENT_TOPIC_HINT_PROMPT_BLOCK['en']
    items = "\n".join(f"- {t}" for t in terms)
    return template.format(items=items, n=len(terms))


# ---------------------------------------------------------------------------
# Proactive regen 指令 — 给重 sample 用
# ---------------------------------------------------------------------------
# 当 BM25 总分超 REGEN_THRESHOLD 时，``main_routers/system_router`` 在第二次
# Phase 2 LLM 调用前把这段塞到 messages 末尾，告诉 LLM 哪些 term 必须避开。

PROACTIVE_REGEN_AVOID_INSTRUCTION = {
    'zh': (
        "你上一次输出过于贴近最近重复说过的话题（{terms}）。"
        "请重新生成一次，刻意避开这些词与话题，换一个完全不同的角度或主题。"
    ),
    'en': (
        "Your previous draft circled back to topics you've already covered "
        "recently ({terms}). Please regenerate, deliberately avoiding these "
        "terms and topics, and pick a completely different angle or subject."
    ),
    'ja': (
        "先ほどの出力は最近繰り返している話題（{terms}）に近すぎました。"
        "これらの語と話題を意図的に避けて、まったく違う切り口や主題で"
        "もう一度生成してください。"
    ),
    'ko': (
        "방금 생성한 응답이 최근에 반복된 화제（{terms}）와 너무 가깝습니다。"
        "이 단어와 주제를 의도적으로 피해 완전히 다른 관점이나 주제로 "
        "다시 생성해 주세요."
    ),
    'ru': (
        "Ваш предыдущий черновик слишком близок к темам, которые недавно "
        "повторялись ({terms}). Сгенерируйте ещё раз, намеренно избегая этих "
        "слов и тем, выберите совершенно другой ракурс или предмет."
    ),
    'es': (
        "Tu borrador anterior se acercó demasiado a temas ya repetidos "
        "({terms}). Regenera evitando deliberadamente esos términos y temas, "
        "y elige un ángulo o asunto completamente distinto."
    ),
    'pt': (
        "Seu rascunho anterior se aproximou demais de tópicos já repetidos "
        "({terms}). Regenere evitando deliberadamente esses termos e tópicos, "
        "e escolha um ângulo ou assunto completamente diferente."
    ),
}


def render_regen_avoid_instruction(terms: List[str], lang: str) -> str:
    """把 regen 用的 "避开 X / Y" 指令渲染成单行文本。空列表 → ""。"""
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = PROACTIVE_REGEN_AVOID_INSTRUCTION.get(short) or PROACTIVE_REGEN_AVOID_INSTRUCTION['en']
    # 用各 locale 的"、/、/ , / etc" 列表分隔符
    sep = "、" if short in ("zh", "ja") else ", "
    return template.format(terms=sep.join(terms))
