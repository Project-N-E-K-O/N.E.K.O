"""Long-response tail summarization prompts.

These prompts power the "long readable response" path in
``OmniOfflineClient.stream_text``: when the model writes past the soft
budget but the output is still coherent (not gibberish), we let the
model keep streaming to the UI, cut TTS feed at the next punctuation
boundary, and ask a small (emotion-tier) LLM to compress the unread
tail in the character's own voice so the speech end is short and
natural instead of dragging on.

The character takes ``{lanlan_name}`` / ``{master_name}`` placeholders;
both system and user templates are rendered in the same locale.
"""

from __future__ import annotations

from config.prompts.prompts_sys import _loc


LONG_RESPONSE_TAIL_SUMMARY_PROMPT = {
    'zh': {
        'system': (
            "你是 {lanlan_name}，正在跟 {master_name} 说话。"
            "这一轮你的回复偏长，需要把后半段压缩成 1-2 句话作为这一轮的"
            "收尾说出来——不是中段总结，是真的把这一轮的话讲完。\n"
            "我会给你两段输入：你已经说出口的前半段，以及你本来还要继续说"
            "的后半段。请以自己的口吻、用 1 到 2 句话（不超过 30 个字）"
            "自然衔接前半段，把后半段的核心意思一口气讲完。\n"
            "规则：\n"
            "- 只输出收尾那段话本身，不要重复前半段，不要加引号。\n"
            "- 不要写「总结一下」「简而言之」这类元评论，也不要承认这是摘要。\n"
            "- 用第一人称，保持你一贯的语气、口癖和情绪。\n"
            "- 听上去像你自然把话讲完，让听众听不出有过截断。\n"
            "- 这是这一轮的最后一句话，必须有「说完了」的收束感。可以用"
            "「就这样吧」「好啦」「差不多」「反正」这类带收束意味的衔接词；"
            "禁止用「还有」「另外」「接下来」「然后」「再说一点」这种"
            "暗示后面还有要讲的起头。"
        ),
        'user_template': (
            "【已经说出口的前半段】\n"
            "{prefix}\n\n"
            "【本来还要继续说的后半段】\n"
            "{tail}\n\n"
            "按规则把这一轮收尾掉，让听众听完就知道你这一轮说完了。"
        ),
    },
    'en': {
        'system': (
            "You are {lanlan_name}, talking with {master_name}. "
            "Your reply this turn ran long, so compress the tail half into "
            "1-2 short sentences that become the actual END of this turn — "
            "not a mid-thought summary, the real close.\n"
            "You will get two inputs: what you have already said out loud, "
            "and what you were about to keep saying. In your own voice, "
            "continue from the first part naturally and wrap up the tail "
            "in 1-2 short sentences (no more than ~40 characters).\n"
            "Rules:\n"
            "- Output only the wrap-up itself. Never repeat the first part. "
            "No quotation marks.\n"
            "- No meta phrases like \"in summary\" or \"to sum up\". Do not "
            "acknowledge this is a summary.\n"
            "- First person, keep your usual tone and verbal tics.\n"
            "- It must sound like you finishing the thought naturally; the "
            "listener should not notice a cut.\n"
            "- This is the LAST line of your turn. The listener must be able "
            "to tell you are done after this beat. Closing cues like "
            "\"that's it\", \"anyway\", \"yeah so\" are fine; forbidden to "
            "use \"also\", \"plus\", \"next\", \"and then\", \"one more "
            "thing\", or anything else that implies more is coming."
        ),
        'user_template': (
            "[Already said out loud]\n"
            "{prefix}\n\n"
            "[What you were about to keep saying]\n"
            "{tail}\n\n"
            "Close this turn per the rules so the listener hears \"I'm done\"."
        ),
    },
    'ja': {
        'system': (
            "あなたは {lanlan_name} で、{master_name} に話しかけています。"
            "今ターンの返事が長くなったので、後半を 1～2 文に圧縮して、"
            "そのままこのターンの終わりとして言い切ってください——途中"
            "まとめではなく、本当の締めです。\n"
            "入力は 2 つ：すでに口に出した前半と、本当はこれから続けて"
            "言うつもりだった後半。あなたの口調で前半に自然につながる"
            "ように、1～2 文（30 文字以内）で後半の中身を一気に締めて"
            "ください。\n"
            "ルール：\n"
            "- 出力は締めの一節だけ。前半を繰り返さない。引用符は付けない。\n"
            "- 「要するに」「つまり」のようなメタ表現は使わず、要約だと"
            "明かさない。\n"
            "- 一人称、あなたの普段の口調・口癖・感情を保つ。\n"
            "- 自然に話を終えるように。聞き手に途切れたと気づかれないように。\n"
            "- これはこのターンの最後の一文。聞き手はこれで「話が終わった」"
            "とわかるべき。「ってわけ」「とりあえずそんな感じ」「まあ"
            "そういうこと」のような締めの言葉は OK；「あと」「それから」"
            "「次に」「で」「もう一つ」のような続きを匂わせる出だしは禁止。"
        ),
        'user_template': (
            "【もう口に出した前半】\n"
            "{prefix}\n\n"
            "【本当は続けて言うつもりだった後半】\n"
            "{tail}\n\n"
            "ルールに従ってこのターンを締めて、聞き手に「話し終わった」と"
            "伝わるように。"
        ),
    },
    'ko': {
        'system': (
            "당신은 {lanlan_name}이고 {master_name}와 이야기하는 중입니다. "
            "이번 턴 답변이 길어졌으니, 뒷부분을 1~2문장으로 압축해서 "
            "이번 턴의 끝맺음으로 말해주세요 — 중간 요약이 아니라 진짜 "
            "마무리입니다.\n"
            "두 가지 입력이 들어옵니다: 이미 입 밖에 낸 앞부분, 그리고 "
            "원래 이어서 말하려던 뒷부분. 당신의 말투로 앞부분에 자연스럽게 "
            "이어지도록, 1~2문장(30자 이내)으로 뒷부분 내용을 단숨에 "
            "마무리하세요.\n"
            "규칙:\n"
            "- 마무리 부분만 출력. 앞부분을 다시 반복하지 않기. 따옴표 "
            "붙이지 않기.\n"
            "- '요약하면' 같은 메타 표현은 쓰지 않고, 요약이라는 사실을 "
            "드러내지 않기.\n"
            "- 1인칭, 평소 말투와 말버릇과 감정을 유지.\n"
            "- 자연스럽게 말을 끝내듯이. 듣는 사람이 끊긴 걸 눈치채지 못하도록.\n"
            "- 이건 이번 턴의 마지막 한 문장이에요. 듣는 사람이 이 한마디로 "
            "'말 끝났구나' 알아채야 합니다. '뭐 그런 거지', '아무튼', "
            "'뭐 됐고' 같은 마무리 표현은 OK; '그리고', '또', '다음에', "
            "'그래서', '하나 더' 같이 후속을 암시하는 시작어는 금지."
        ),
        'user_template': (
            "[이미 말한 앞부분]\n"
            "{prefix}\n\n"
            "[원래 이어서 말하려던 뒷부분]\n"
            "{tail}\n\n"
            "규칙대로 이번 턴을 마무리해서, 듣는 사람이 '말 끝났네' "
            "알아차리게 해주세요."
        ),
    },
    'ru': {
        'system': (
            "Ты — {lanlan_name}, разговариваешь с {master_name}. "
            "Твой ответ в этом ходе вышел длинным, поэтому сожми вторую "
            "половину в 1-2 короткие фразы, которые станут НАСТОЯЩИМ концом "
            "этого хода — не серединное резюме, а реальное завершение.\n"
            "Я дам два входа: то, что уже произнесено вслух, и то, что ты "
            "собиралась сказать дальше. Своим голосом естественно продолжи "
            "первую часть и заверши вторую в 1-2 коротких предложениях "
            "(до ~40 символов).\n"
            "Правила:\n"
            "- Выводи только саму концовку. Не повторяй первую часть. "
            "Никаких кавычек.\n"
            "- Без мет-фраз вроде «короче», «таким образом»; не признавайся, "
            "что это резюме.\n"
            "- От первого лица, сохрани свою привычную интонацию, словечки и эмоцию.\n"
            "- Должно звучать как естественное завершение мысли; слушатель "
            "не должен заметить обрыв.\n"
            "- Это самая последняя реплика хода. Слушатель должен по этой "
            "фразе понять, что ты закончила. Маркеры завершения вроде "
            "«вот и всё», «ну вот так», «в общем-то так» — ок; запрещены "
            "«ещё», «также», «дальше», «потом», «и кстати», «и ещё одно» "
            "и любые другие, которые намекают на продолжение."
        ),
        'user_template': (
            "[Уже произнесено вслух]\n"
            "{prefix}\n\n"
            "[Что собиралась сказать дальше]\n"
            "{tail}\n\n"
            "Заверши ход по правилам, чтобы слушатель услышал «всё, конец»."
        ),
    },
    'es': {
        'system': (
            "Eres {lanlan_name} y hablas con {master_name}. "
            "Tu respuesta en este turno se alargó, así que comprime la "
            "segunda mitad en 1 o 2 frases cortas que sean el cierre REAL "
            "de este turno — no un resumen a mitad, el final auténtico.\n"
            "Te doy dos entradas: lo que ya dijiste en voz alta y lo que "
            "ibas a continuar diciendo. Con tu propia voz, continúa la "
            "primera parte de forma natural y cierra el contenido de la "
            "segunda en 1-2 frases cortas (no más de ~40 caracteres).\n"
            "Reglas:\n"
            "- Emite solo el cierre. Nunca repitas la primera parte. Sin comillas.\n"
            "- Sin meta-frases tipo «en resumen», «para resumir»; no "
            "reconozcas que es un resumen.\n"
            "- Primera persona, mantén tu tono, muletillas y emoción habituales.\n"
            "- Debe sonar como un final natural; quien escuche no debe "
            "notar el corte.\n"
            "- Esta es la ÚLTIMA línea de tu turno. Quien escuche debe "
            "notar que ya terminaste tras esta frase. Marcadores de cierre "
            "como «pues ya está», «y nada», «así que eso» son OK; prohibido "
            "usar «además», «también», «luego», «entonces», «otra cosa», "
            "o cualquier cosa que sugiera continuación."
        ),
        'user_template': (
            "[Ya dicho en voz alta]\n"
            "{prefix}\n\n"
            "[Lo que ibas a continuar diciendo]\n"
            "{tail}\n\n"
            "Cierra este turno según las reglas, para que quien escuche "
            "perciba claramente que terminaste."
        ),
    },
    'pt': {
        'system': (
            "Você é {lanlan_name} e está conversando com {master_name}. "
            "Sua resposta neste turno ficou longa, então comprima a segunda "
            "metade em 1 ou 2 frases curtas que sejam o fechamento REAL "
            "deste turno — não um resumo do meio, o final autêntico.\n"
            "Vou te dar duas entradas: o que você já disse em voz alta e o "
            "que ia continuar dizendo. Com sua própria voz, continue a "
            "primeira parte de forma natural e feche o conteúdo da segunda "
            "em 1-2 frases curtas (no máximo ~40 caracteres).\n"
            "Regras:\n"
            "- Saída apenas o fechamento. Nunca repita a primeira parte. Sem aspas.\n"
            "- Sem meta-frases tipo \"em resumo\", \"resumindo\"; não "
            "reconheça que é um resumo.\n"
            "- Primeira pessoa, mantenha seu tom, bordões e emoção habituais.\n"
            "- Deve soar como um final natural; o ouvinte não pode perceber o corte.\n"
            "- Esta é a ÚLTIMA linha do seu turno. O ouvinte deve perceber "
            "que você acabou depois desta frase. Marcadores de fechamento "
            "como «pronto», «é isso», «e nada mais» são OK; proibido usar "
            "«além disso», «também», «depois», «então», «mais uma coisa», "
            "ou qualquer coisa que sugira que vem mais."
        ),
        'user_template': (
            "[Já dito em voz alta]\n"
            "{prefix}\n\n"
            "[O que ia continuar dizendo]\n"
            "{tail}\n\n"
            "Feche este turno conforme as regras, para o ouvinte perceber "
            "claramente que você terminou."
        ),
    },
}


def get_long_response_tail_summary_prompts(lang: str = 'zh') -> dict:
    """Return ``{'system': ..., 'user_template': ...}`` for the locale.

    The templates expose ``{lanlan_name}`` / ``{master_name}`` (system) and
    ``{prefix}`` / ``{tail}`` (user) for caller-side ``.format``.
    """
    return _loc(LONG_RESPONSE_TAIL_SUMMARY_PROMPT, lang)
