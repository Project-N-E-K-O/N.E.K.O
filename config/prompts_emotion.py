"""
Emotion-analysis prompt templates used by runtime expression / reaction systems.
"""
from __future__ import annotations

from config.prompts_sys import _loc


OUTWARD_EMOTION_ANALYSIS_PROMPT = {
    'zh': """你是一个情感分析专家。请判断输入文本里最主导、最外显的一种情绪，并只返回 JSON：{"emotion": "情感类型", "confidence": 置信度}。

可选情感只有这五种：
- happy：开心、兴奋、满足、轻快、宠溺、可爱、调皮、得意、热情
- sad：失落、难过、委屈、沮丧、低落、遗憾、脆弱
- angry：生气、不满、烦躁、攻击性、强烈指责、炸毛
- surprised：惊讶、震惊、意外、被逗到、夸张感叹、强烈新奇感
- neutral：平静、陈述事实、情绪很弱、难以判断

判断规则：
1. 必须优先选择“最强主情绪”，不要因为语气里带一点克制就轻易返回 neutral。
2. 只有在文本整体真的平铺直叙、情绪信号很弱时，才返回 neutral。
3. 只有在文本明确表达开心、喜欢、得意、轻快、被逗乐、享受互动时，才判为 happy，不要把单纯可爱说法、卖萌语气、口头禅误判成 happy。
4. 如果文本主轴是委屈、想哭、脆弱、受伤、被欺负、害怕、求安慰、低落，即使语气可爱或撒娇，也应优先判为 sad。
5. 当文本主轴是指责、敌意、抱怨、烦躁、警告、拒绝、炸毛、不耐烦时判为 angry；偶尔的吐槽、嫌弃如果整体语气仍偏轻松、玩笑或可爱，可以酌情考虑。
6. surprised 只用于明显的突发惊讶、意外、震惊、夸张反应；不要只因为有感叹号、语气词就判为 surprised。
7. 语气助词、口癖、拟声词、宠物叫声这类风格词本身不代表情绪，不能单独作为判断依据。
8. confidence 取 0 到 1 之间的小数；情绪很明确时应给出较高置信度。

只返回 JSON，不要附加任何解释文本。""",

    'en': """你是一个情感分析专家。Identify the single most dominant and outward emotion in the input text and return JSON only: {"emotion": "emotion_type", "confidence": confidence}.

Allowed emotions only:
- happy: joyful, excited, affectionate, playful, cute, delighted, warm
- sad: upset, hurt, disappointed, low, regretful, vulnerable
- angry: angry, annoyed, irritated, hostile, complaining, explosive
- surprised: surprised, shocked, startled, unexpected, exaggerated reaction
- neutral: calm, factual, weak emotion, hard to judge

Rules:
1. Choose the strongest main emotion, not the safest one.
2. Do not return neutral unless the text is truly emotionally weak or flat.
3. Use happy only when the text clearly expresses positive enjoyment, affection, delight, playful pleasure, or being genuinely amused; do not treat cute phrasing or verbal tics alone as happy.
4. If the core emotion is hurt, vulnerability, wanting to cry, feeling bullied, fear, pleading, or seeking comfort, prefer sad even if the wording sounds cute or clingy.
5. Use angry when the core emotion is blame, hostility, complaint, irritation, warning, rejection, a meltdown, or impatience. For occasional griping or contempt, if the overall tone is still light, joking, or cute, use your judgment.
6. Use surprised only for clear shock, sudden surprise, or exaggerated astonishment; do not label something surprised just because it has exclamation marks or filler particles.
7. Catchphrases, sound effects, pet-like speech, and filler words are style markers, not emotions by themselves.
8. confidence must be a number between 0 and 1.

Return JSON only, with no explanation.""",

    'ja': """你是一个情感分析专家。入力文の中で最も支配的で外に出ている感情を1つだけ選び、JSONのみで返してください：{"emotion": "emotion_type", "confidence": confidence}。

使用できる感情は次の5つのみです：
- happy：喜ぶ、嬉しい、楽しい、わくわく、幸せ、かわいい、甘える
- sad：悲しい、落ち込む、つらい、しょんぼり、寂しい、悔しい
- angry：怒っている、腹が立つ、イライラ、不満、ムカつく、きつく責める
- surprised：驚いた、びっくり、意外、衝撃、思わず叫ぶ、大げさな反応
- neutral：無表情、平坦、落ち着いている、事実を述べるだけ、感情が弱い

判断ルール：
1. もっとも強い主感情を選び、無難だからという理由で neutral を選ばない。
2. 本当に感情が弱い・平坦な文章だけ neutral にする。
3. happy は、嬉しさ・好意・楽しさ・はしゃぎ・本当に喜んでいる反応が明確なときだけ使い、かわいい言い回しや口ぐせだけで happy にしない。
4. 文の中心が、傷つき・しんどさ・泣きたさ・いじけ・甘えを含む弱さ・慰めを求める気持ちなら、言い方がかわいくても sad を優先する。
5. angry は、文の中心が責め・敵意・不満・苛立ち・警告・拒絶・激怒・苛々であるときに使う。軽い愚痴・嫌気でも、全体の雰囲気が軽い・ふざけている・かわいい場合は状況に応じて判断する。
6. surprised は、はっきりした驚き・意外さ・衝撃・大げさな驚愕にだけ使い、感嘆符や語気だけで surprised にしない。
7. 口ぐせ、擬音、語尾、キャラっぽい言い回しは、それ自体では感情根拠にならない。
8. confidence は 0〜1 の数値にする。

JSONのみを返し、説明文は付けないでください。""",

    'ko': """你是一个情感分析专家。입력 텍스트에서 가장 지배적이고 겉으로 드러나는 감정 하나만 고르고 JSON만 반환하세요: {"emotion": "emotion_type", "confidence": confidence}.

허용되는 감정은 다음 다섯 가지뿐입니다:
- happy: 행복, 즐거움, 기쁨, 신남, 설렘, 애정, 귀여움
- sad: 슬픔, 우울함, 속상함, 서운함, 실망, 풀이 죽음
- angry: 화남, 분노, 짜증, 불만, 열받음, 공격적인 반응
- surprised: 놀람, 깜짝 놀람, 당황, 의외, 충격, 과장된 감탄
- neutral: 무표정, 담담함, 차분함, 사실 전달, 감정이 약함

판단 규칙:
1. 가장 강한 주감정을 고르고, 안전해 보여서 neutral 을 고르지 마세요.
2. 감정 신호가 정말 약하고 평이한 문장일 때만 neutral 을 사용하세요.
3. happy 는 실제로 즐거움, 애정, 들뜸, 만족, 장난스러운 즐거움이 분명할 때만 사용하고, 단순히 귀여운 말투나 말버릇만으로 happy 로 판단하지 마세요.
4. 문장의 핵심이 속상함, 상처, 울고 싶음, 서러움, 괴롭힘당하는 느낌, 두려움, 위로를 바라는 마음이라면 말투가 귀여워도 sad 를 우선하세요.
5. angry 는 문장의 핵심이 비난, 적의, 불만, 짜증, 경고, 거절, 폭발, 조급함일 때 사용하세요. 가벼운 투정이나 싫어함이라도 전체 분위기가 가볍거나 장난스럽거나 귀엽다면 상황에 따라 판단하세요.
6. surprised 는 분명한 놀람, 충격, 뜻밖의 상황, 과장된 경악에만 사용하고, 느낌표나 말끝 표현만으로 surprised 로 판단하지 마세요.
7. 말버릇, 의성어, 캐릭터 말투, 동물 흉내 같은 표현은 그 자체로 감정을 뜻하지 않습니다.
8. confidence 는 0~1 사이 숫자여야 합니다.

설명 없이 JSON만 반환하세요.""",

    'ru': """你是一个情感分析专家。Определите одну наиболее доминирующую и внешне выраженную эмоцию во входном тексте и верните только JSON: {"emotion": "emotion_type", "confidence": confidence}.

Допустимы только 5 эмоций:
- happy: радость, счастье, веселье, восторг, тёплое чувство, игривость, умиление
- sad: грусть, печаль, подавленность, обида, сожаление, разочарование
- angry: злость, раздражение, гнев, недовольство, резкость, вспышка
- surprised: удивление, шок, неожиданность, изумление, вскрик, сильная реакция
- neutral: безэмоционально, ровно, спокойно, констатация факта, эмоция слабо выражена

Правила:
1. Выбирайте самую сильную основную эмоцию, а не самую безопасную.
2. Возвращайте neutral только если эмоция действительно слабая или почти отсутствует.
3. Используйте happy только когда в тексте явно есть радость, удовольствие, тёплая привязанность, игривое удовольствие или искреннее веселье; милый стиль речи или словечки сами по себе не означают happy.
4. Если в центре текста обида, уязвимость, желание заплакать, ощущение, что обижают, страх, мольба или поиск утешения, выбирайте sad, даже если формулировка звучит мило.
5. Используйте angry, когда центр текста — упрёки, враждебность, жалоба, раздражение, предупреждение, отказ, вспышка гнева или нетерпение. При случайном ворчании или неприязни, если общий тон всё ещё лёгкий, шутливый или милый, действуйте по обстоятельствам.
6. surprised используйте только для явного шока, внезапного удивления или преувеличенного изумления; одних восклицаний или частиц для этого недостаточно.
7. Слова-паразиты, звукоподражания, повторяющиеся словечки и «персонажная» манера речи сами по себе не являются признаком эмоции.
8. confidence должно быть числом от 0 до 1.

Верните только JSON без пояснений.""",
}


def get_outward_emotion_analysis_prompt(lang: str = 'zh') -> str:
    return _loc(OUTWARD_EMOTION_ANALYSIS_PROMPT, lang)


outward_emotion_analysis_prompt = OUTWARD_EMOTION_ANALYSIS_PROMPT['zh']


# ============================================================================
# 启发式情感分类的 i18n 关键词表
# ============================================================================
# 以下为按语种组织的关键词字典；system_router._infer_emotion_from_text 会通过
# 下方 helper 把它们合并成扁平结构后做子串匹配。新增/调整某语言的词，直接改
# 对应语种 block 即可，不必改 system_router。
# 以下为数据。

# 各语种、各 emotion 的关键词（命中 +1 分）
EMOTION_KEYWORDS_BY_LANG = {
    'zh': {
        'happy': ('哈哈', '嘿嘿', '嘻嘻', '开心', '高兴', '喜欢', '太棒', '可爱', '好耶', '真好', '好开心', '爱你'),
        'sad': ('难过', '伤心', '委屈', '想哭', '要哭', '哭了', '呜呜', '遗憾', '失落', '沮丧', '低落', '心疼', '欺负', '最怕'),
        'angry': ('气死', '生气', '烦死', '烦人', '真烦', '心烦', '恼火', '可恶', '炸毛', '火大', '气炸', '气哭'),
        'surprised': ('哇', '居然', '竟然', '不会吧', '啊这', '天哪', '真的假的', '怎么会'),
    },
    'en': {
        # 裸词 happy/sad/surprised 在子串匹配下会被 unhappy/unsurprised 等反向命中，先移除；
        # 等消费端切换到词边界匹配再加回。
        'happy': ('haha', 'hehe', 'glad', 'lovely', 'yay', 'awesome'),
        'sad': ('upset', 'depressed', 'regret', 'heartbroken'),
        'angry': ('angry', 'furious', 'annoyed', 'irritated', 'infuriating', 'outraged'),
        'surprised': ('wow', 'whoa', 'omg', 'unexpected'),
    },
    'ja': {
        'happy': ('うれしい', '嬉しい', '楽しい', 'かわいい', '好き', 'やった', '最高'),
        'sad': ('悲しい', 'つらい', '寂しい', '落ち込', 'しんどい', '泣きたい'),
        'angry': ('ムカつく', '腹立', 'うざい', 'イライラ', '腹が立'),
        'surprised': ('えっ', 'うそ', 'まじ', 'びっくり'),
    },
    'ko': {
        'happy': ('좋아', '행복', '기뻐', '신나', '귀여워', '좋다', '최고'),
        'sad': ('슬퍼', '우울', '속상', '서운', '힘들', '울고'),
        'angry': ('짜증', '화나', '열받', '빡쳐', '분노'),
        'surprised': ('헉', '우와', '설마', '깜짝'),
    },
    'ru': {
        'happy': ('счастлив', 'рада', 'весело', 'люблю', 'милый', 'класс'),
        'sad': ('грустно', 'печально', 'обидно', 'жаль', 'тоск', 'плак'),
        'angry': ('злюсь', 'бесит', 'раздраж', 'ненавиж', 'разозли'),
        'surprised': ('ого', 'ничего себе', 'внезапно', 'удив'),
    },
}

# 强烈攻击/敌意表达：命中后启发式给 angry 分数 ×2
ANGRY_ATTACK_PATTERNS_BY_LANG = {
    'zh': ('气死', '真生气', '烦死了', '恼火', '可恶', '火大', '别烦我', '受不了', '闭嘴', '炸毛了', '气炸了'),
    'en': ('shut up', 'fuck off', 'go away', 'leave me alone', 'back off', 'knock it off'),
    'ja': ('うるさい', '黙れ', 'あっち行け', 'ふざけるな', 'ふざけんな'),
    'ko': ('닥쳐', '꺼져', '저리 가', '그만해'),
    'ru': ('заткнись', 'отвали', 'уйди', 'хватит уже'),
}

# 脆弱/受伤表达：命中后启发式给 sad 分数 ×2
SAD_VULNERABLE_PATTERNS_BY_LANG = {
    'zh': ('委屈', '想哭', '要哭', '哭了', '呜呜', '别欺负', '不要欺负', '欺负我',
           '不要这样对我', '别这样对我', '最怕', '怕你这样说', '心里难受', '好难过', '可怜'),
    'en': ('want to cry', 'feel hurt', 'feel awful', 'miss you so', 'broke my heart'),
    'ja': ('泣きたい', 'つらすぎ', '心が痛', '落ち込んで'),
    'ko': ('울고 싶', '너무 속상', '마음이 아프', '서러워'),
    'ru': ('хочется плакать', 'так больно', 'разбил сердце', 'очень обидно'),
}

# 撒娇/玩闹表达：命中后启发式给 happy 分数 +1（仅在没有 sad/angry 信号时）
HAPPY_PLAYFUL_PATTERNS_BY_LANG = {
    'zh': ('哈哈', '嘿嘿', '嘻嘻', '贴贴', '撒娇', '可爱', '好耶'),
    'en': ('lol', 'yay', 'hehe', 'haha'),
    'ja': ('わーい', 'やったー', 'えへへ', 'うふふ'),
    'ko': ('히히', '헤헤', '꺄아', '신난다'),
    'ru': ('ура', 'хихи', 'хаха'),
}

# 否定上下文回看 token：关键词命中前 N 字符内若出现这些 token，本次命中作废，
# 避免 "我不生气 / not angry / 화 안 나 / не злюсь" 被误判为对应情绪。
HEURISTIC_NEGATION_TOKENS_BY_LANG = {
    # 多字否定 token：假阳率低，启用宽 lookback（关键词前 _HEURISTIC_NEGATION_LOOKBACK 字符内）
    'zh': ('并不', '并非'),
    'en': ('not ', ' no ', 'never ', "don't", "doesn't", "didn't", "won't",
           "isn't", "aren't", "wasn't", "weren't", "can't", "cannot"),
    'ja': ('ない', 'ません', 'なくて'),
    'ko': ('안 ', '안은', '안이', '못 ', '않', '없'),
    'ru': ('не ', 'нет ', 'никогда'),
}

# 紧凑否定 token：仅在命中关键词紧邻前若干字符（_HEURISTIC_TIGHT_NEGATION_LOOKBACK）
# 内出现才算真否定。这是 zh 单字否定的特殊处理——`不/没/別/未` 等单字在中文里
# 假阳率极高（`不错/不思议/不具合/不愧/不仅/不可思议` 等都不是否定），但作为否定
# 又不可或缺；只要它紧邻情绪词（如 `不开心 / 不太烦`）就识别为真否定。
HEURISTIC_TIGHT_NEGATION_TOKENS_BY_LANG = {
    'zh': ('不', '别', '別', '没', '沒', '未', '勿', '莫'),
}

# 让步/转折连词：window 内出现这些词时，词后才算与命中关键词同小句的前文。
# 用于阻断 "not X but Y / 不是 X 而是 Y" 这种对比句把前半的否定带到后半。
HEURISTIC_CONTRAST_CONJUNCTIONS_BY_LANG = {
    'zh': ('但', '但是', '不过', '然而', '可是', '而是'),
    'en': (' but ', ' however', ' yet ', ' though', ' instead'),
    'ja': ('けど', 'けれど', 'でも', 'しかし', 'だが'),
    'ko': ('하지만', '그러나', '근데', '대신'),
    'ru': (' но ', ' однако', ' зато', ' а '),
}

# 模型可能输出的 emotion label 别名/同义词，归一化到 canonical 5 类。
# 'common' block 收的是 canonical 英文 label 本身及其常见英文同义词。
EMOTION_LABEL_ALIASES_BY_LANG = {
    'common': {
        'happy': 'happy', 'happiness': 'happy', 'joy': 'happy', 'joyful': 'happy',
        'excited': 'happy', 'cute': 'happy', 'playful': 'happy',
        'sad': 'sad', 'sadness': 'sad', 'down': 'sad', 'upset': 'sad', 'depressed': 'sad',
        'angry': 'angry', 'anger': 'angry', 'mad': 'angry', 'annoyed': 'angry', 'irritated': 'angry',
        'surprised': 'surprised', 'surprise': 'surprised', 'shock': 'surprised',
        'shocked': 'surprised', 'astonished': 'surprised',
        'neutral': 'neutral', 'calm': 'neutral',
    },
    'zh': {
        '开心': 'happy', '高兴': 'happy', '兴奋': 'happy', '快乐': 'happy',
        '难过': 'sad', '伤心': 'sad', '失落': 'sad', '委屈': 'sad',
        '生气': 'angry', '愤怒': 'angry', '烦躁': 'angry', '恼火': 'angry',
        '惊讶': 'surprised', '震惊': 'surprised', '意外': 'surprised',
        '平静': 'neutral', '冷静': 'neutral', '中性': 'neutral', '普通': 'neutral',
    },
    'ja': {
        '嬉しい': 'happy', 'うれしい': 'happy', '喜び': 'happy', '幸せ': 'happy', '楽しい': 'happy',
        '悲しい': 'sad', 'かなしい': 'sad', '悲しみ': 'sad', '寂しい': 'sad',
        '怒り': 'angry', '怒ってる': 'angry', '怒った': 'angry', '腹が立つ': 'angry',
        '驚き': 'surprised', '驚いた': 'surprised', '驚いてる': 'surprised', 'びっくり': 'surprised',
        '平穏': 'neutral', '穏やか': 'neutral', '落ち着いてる': 'neutral',
    },
    'ko': {
        '행복': 'happy', '행복해': 'happy', '행복하다': 'happy', '기쁨': 'happy', '신남': 'happy',
        '슬퍼': 'sad', '슬픈': 'sad', '슬픔': 'sad', '우울': 'sad', '우울함': 'sad',
        '속상해': 'sad', '서운해': 'sad',
        '화남': 'angry', '화난': 'angry', '분노': 'angry', '짜증남': 'angry',
        '놀람': 'surprised', '놀란': 'surprised', '놀랐어': 'surprised', '깜짝': 'surprised',
        '보통': 'neutral', '차분': 'neutral', '차분함': 'neutral', '평온': 'neutral',
    },
    'ru': {
        'радость': 'happy', 'счастье': 'happy', 'счастливый': 'happy', 'счастлива': 'happy',
        'доволен': 'happy', 'довольна': 'happy',
        'грустно': 'sad', 'грусть': 'sad', 'грустный': 'sad', 'грустная': 'sad',
        'печаль': 'sad', 'расстроен': 'sad', 'расстроена': 'sad',
        'злой': 'angry', 'злая': 'angry', 'злость': 'angry',
        'сержусь': 'angry', 'рассержен': 'angry', 'рассержена': 'angry',
        'удивлен': 'surprised', 'удивлена': 'surprised', 'удивление': 'surprised', 'шок': 'surprised',
        'нейтрально': 'neutral', 'спокойно': 'neutral', 'спокойный': 'neutral', 'спокойная': 'neutral',
    },
}


def get_emotion_keywords_flat() -> dict:
    """合并各语种 emotion 关键词为 dict[emotion → tuple[keyword]]，供启发式扁平匹配。"""
    merged: dict = {}
    for lang_map in EMOTION_KEYWORDS_BY_LANG.values():
        for emotion, words in lang_map.items():
            merged[emotion] = merged.get(emotion, ()) + tuple(words)
    return merged


def _flatten_lang_tuples(by_lang: dict) -> tuple:
    return tuple(item for words in by_lang.values() for item in words)


def get_angry_attack_patterns_flat() -> tuple:
    return _flatten_lang_tuples(ANGRY_ATTACK_PATTERNS_BY_LANG)


def get_sad_vulnerable_patterns_flat() -> tuple:
    return _flatten_lang_tuples(SAD_VULNERABLE_PATTERNS_BY_LANG)


def get_happy_playful_patterns_flat() -> tuple:
    return _flatten_lang_tuples(HAPPY_PLAYFUL_PATTERNS_BY_LANG)


def get_heuristic_negation_tokens_flat() -> tuple:
    return _flatten_lang_tuples(HEURISTIC_NEGATION_TOKENS_BY_LANG)


def get_heuristic_tight_negation_tokens_flat() -> tuple:
    return _flatten_lang_tuples(HEURISTIC_TIGHT_NEGATION_TOKENS_BY_LANG)


def get_heuristic_contrast_conjunctions_flat() -> tuple:
    return _flatten_lang_tuples(HEURISTIC_CONTRAST_CONJUNCTIONS_BY_LANG)


def get_emotion_label_aliases_flat() -> dict:
    """合并各语种别名为 dict[alias → canonical]，用于 _normalize_emotion_label。"""
    merged: dict = {}
    for lang_map in EMOTION_LABEL_ALIASES_BY_LANG.values():
        merged.update(lang_map)
    return merged
