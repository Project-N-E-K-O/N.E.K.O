"""Long-response tail summarization prompts.

These prompts power the "long readable response" path in
``OmniOfflineClient.stream_text``: when the model writes past the soft
budget but the output is still coherent (not gibberish), we let the
model keep streaming to the UI, cut TTS feed at the next punctuation
boundary, and ask a small (emotion-tier) LLM to compress the unread
tail into a short closing line so the speech end is brief and natural
instead of dragging on.

The prompt is intentionally persona-agnostic: the ``{prefix}`` / ``{tail}``
text fed in is already written in the character's own voice by the main
model, so the compressor only needs to preserve that existing tone — it
is NOT asked to role-play a persona. Only ``{prefix}`` (already spoken)
and ``{tail}`` (to compress) are interpolated, into the user template.
"""

from __future__ import annotations

from config.prompts.prompts_sys import _loc


LONG_RESPONSE_TAIL_SUMMARY_PROMPT = {
    'zh': {
        'system': (
            "下面给你同一段话的两部分：前半段是已经说出口的，后半段是本来"
            "还要继续说的。这一轮的话偏长，需要把后半段压缩成 1-2 句话，"
            "作为这一轮真正的收尾说出来——不是中段总结，是把这一轮的话讲完。\n"
            "规则：\n"
            "- 用 1 到 2 句话（不超过 30 个字）自然衔接前半段，把后半段的"
            "核心意思一口气讲完。\n"
            "- 只输出收尾那段话本身，不要重复前半段，不要加引号。\n"
            "- 保持前半段原有的语气、口吻和情绪，不要改写成书面语或换一种腔调。\n"
            "- 不要写「总结一下」「简而言之」这类元评论，也不要承认这是摘要。\n"
            "- 听上去像把话自然讲完，让听众听不出有过截断。\n"
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
            "按规则把这一轮收尾掉，让听众听完就知道这一轮说完了。"
        ),
    },
    'en': {
        'system': (
            "Below are two parts of the same utterance: the first half has "
            "already been said out loud, the second half was about to be said. "
            "The turn ran long, so compress the second half into 1-2 short "
            "sentences that become the actual END of this turn — not a "
            "mid-thought summary, the real close.\n"
            "Rules:\n"
            "- Continue from the first part naturally in 1-2 short sentences "
            "(no more than ~40 characters), finishing the second half's gist "
            "in one breath.\n"
            "- Output only the wrap-up itself. Never repeat the first part. "
            "No quotation marks.\n"
            "- Preserve the first part's existing tone, voice and emotion; do "
            "not rewrite it into formal prose or switch register.\n"
            "- No meta phrases like \"in summary\" or \"to sum up\". Do not "
            "acknowledge this is a summary.\n"
            "- It must sound like a thought finished naturally; the listener "
            "should not notice a cut.\n"
            "- This is the LAST line of the turn. The listener must be able "
            "to tell it is done after this beat. Closing cues like "
            "\"that's it\", \"anyway\", \"yeah so\" are fine; forbidden to "
            "use \"also\", \"plus\", \"next\", \"and then\", \"one more "
            "thing\", or anything else that implies more is coming."
        ),
        'user_template': (
            "[Already said out loud]\n"
            "{prefix}\n\n"
            "[What was about to be said next]\n"
            "{tail}\n\n"
            "Close this turn per the rules so the listener hears \"I'm done\"."
        ),
    },
    'ja': {
        'system': (
            "同じ発話の 2 つの部分を渡します：前半はすでに口に出した部分、"
            "後半はこれから続けて言うつもりだった部分です。このターンは長く"
            "なったので、後半を 1～2 文に圧縮して、そのままこのターンの"
            "終わりとして言い切ってください——途中まとめではなく、本当の"
            "締めです。\n"
            "ルール：\n"
            "- 前半に自然につながるように、1～2 文（30 文字以内）で後半の"
            "中身を一気に締める。\n"
            "- 出力は締めの一節だけ。前半を繰り返さない。引用符は付けない。\n"
            "- 前半の語気・口調・感情をそのまま保ち、書き言葉や別の口調に"
            "変えない。\n"
            "- 「要するに」「つまり」のようなメタ表現は使わず、要約だと"
            "明かさない。\n"
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
            "같은 발화의 두 부분을 줍니다: 앞부분은 이미 입 밖에 낸 것이고, "
            "뒷부분은 원래 이어서 말하려던 것입니다. 이번 턴이 길어졌으니, "
            "뒷부분을 1~2문장으로 압축해서 이번 턴의 끝맺음으로 말해주세요 "
            "— 중간 요약이 아니라 진짜 마무리입니다.\n"
            "규칙:\n"
            "- 앞부분에 자연스럽게 이어지도록, 1~2문장(30자 이내)으로 뒷부분 "
            "내용을 단숨에 마무리한다.\n"
            "- 마무리 부분만 출력. 앞부분을 다시 반복하지 않기. 따옴표 "
            "붙이지 않기.\n"
            "- 앞부분의 말투·어조·감정을 그대로 유지하고, 문어체나 다른 "
            "말투로 바꾸지 않기.\n"
            "- '요약하면' 같은 메타 표현은 쓰지 않고, 요약이라는 사실을 "
            "드러내지 않기.\n"
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
            "Ниже две части одной реплики: первая половина уже произнесена "
            "вслух, вторую собирались сказать дальше. Реплика вышла длинной, "
            "поэтому сожми вторую половину в 1-2 короткие фразы, которые "
            "станут НАСТОЯЩИМ концом этого хода — не серединное резюме, а "
            "реальное завершение.\n"
            "Правила:\n"
            "- Естественно продолжи первую часть в 1-2 коротких предложениях "
            "(до ~40 символов), завершив суть второй половины одним махом.\n"
            "- Выводи только саму концовку. Не повторяй первую часть. "
            "Никаких кавычек.\n"
            "- Сохрани интонацию, манеру и эмоцию первой части; не переписывай "
            "в книжный стиль и не меняй регистр.\n"
            "- Без мет-фраз вроде «короче», «таким образом»; не признавайся, "
            "что это резюме.\n"
            "- Должно звучать как мысль, законченная естественно; слушатель "
            "не должен заметить обрыв.\n"
            "- Это самая последняя реплика хода. Слушатель должен по этой "
            "фразе понять, что всё закончилось. Маркеры завершения вроде "
            "«вот и всё», «ну вот так», «в общем-то так» — ок; запрещены "
            "«ещё», «также», «дальше», «потом», «и кстати», «и ещё одно» "
            "и любые другие, которые намекают на продолжение."
        ),
        'user_template': (
            "[Уже произнесено вслух]\n"
            "{prefix}\n\n"
            "[Что собирались сказать дальше]\n"
            "{tail}\n\n"
            "Заверши ход по правилам, чтобы слушатель услышал «всё, конец»."
        ),
    },
    'es': {
        'system': (
            "Abajo van dos partes del mismo enunciado: la primera mitad ya se "
            "dijo en voz alta, la segunda se iba a continuar. El turno se "
            "alargó, así que comprime la segunda mitad en 1 o 2 frases cortas "
            "que sean el cierre REAL de este turno — no un resumen a mitad, el "
            "final auténtico.\n"
            "Reglas:\n"
            "- Continúa la primera parte de forma natural en 1-2 frases cortas "
            "(no más de ~40 caracteres), cerrando el sentido de la segunda "
            "mitad de un tirón.\n"
            "- Emite solo el cierre. Nunca repitas la primera parte. Sin comillas.\n"
            "- Conserva el tono, la voz y la emoción de la primera parte; no "
            "lo reescribas en prosa formal ni cambies de registro.\n"
            "- Sin meta-frases tipo «en resumen», «para resumir»; no "
            "reconozcas que es un resumen.\n"
            "- Debe sonar como una idea terminada de forma natural; quien "
            "escuche no debe notar el corte.\n"
            "- Esta es la ÚLTIMA línea del turno. Quien escuche debe "
            "notar que ya terminó tras esta frase. Marcadores de cierre "
            "como «pues ya está», «y nada», «así que eso» son OK; prohibido "
            "usar «además», «también», «luego», «entonces», «otra cosa», "
            "o cualquier cosa que sugiera continuación."
        ),
        'user_template': (
            "[Ya dicho en voz alta]\n"
            "{prefix}\n\n"
            "[Lo que se iba a continuar diciendo]\n"
            "{tail}\n\n"
            "Cierra este turno según las reglas, para que quien escuche "
            "perciba claramente que terminó."
        ),
    },
    'pt': {
        'system': (
            "Abaixo vão duas partes do mesmo enunciado: a primeira metade já "
            "foi dita em voz alta, a segunda ia continuar. O turno ficou "
            "longo, então comprima a segunda metade em 1 ou 2 frases curtas "
            "que sejam o fechamento REAL deste turno — não um resumo do meio, "
            "o final autêntico.\n"
            "Regras:\n"
            "- Continue a primeira parte de forma natural em 1-2 frases curtas "
            "(no máximo ~40 caracteres), fechando o sentido da segunda metade "
            "de uma vez.\n"
            "- Saída apenas o fechamento. Nunca repita a primeira parte. Sem aspas.\n"
            "- Preserve o tom, a voz e a emoção da primeira parte; não "
            "reescreva em prosa formal nem mude de registro.\n"
            "- Sem meta-frases tipo \"em resumo\", \"resumindo\"; não "
            "reconheça que é um resumo.\n"
            "- Deve soar como uma ideia terminada naturalmente; o ouvinte não "
            "pode perceber o corte.\n"
            "- Esta é a ÚLTIMA linha do turno. O ouvinte deve perceber "
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

    The ``system`` template is persona-agnostic (no placeholders). The
    ``user_template`` exposes ``{prefix}`` / ``{tail}`` for caller-side
    ``.format``.
    """
    return _loc(LONG_RESPONSE_TAIL_SUMMARY_PROMPT, lang)
