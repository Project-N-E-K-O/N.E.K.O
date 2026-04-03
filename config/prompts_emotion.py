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
5. 如果文本主轴是抱怨、指责、烦躁、警告、拒绝、炸毛、不耐烦，即使夹杂可爱语气，也应优先判为 angry。
6. surprised 只用于明显的突发惊讶、意外、震惊、夸张反应；不要只因为有感叹号、语气词就判为 surprised。
7. 语气助词、口癖、拟声词、宠物叫声这类风格词本身不代表情绪，不能单独作为判断依据。
8. confidence 取 0 到 1 之间的小数；情绪很明确时应给出较高置信度。

只返回 JSON，不要附加任何解释文本。""",

    'en': """You are an emotion analysis expert. Identify the single most dominant and outward emotion in the input text and return JSON only: {"emotion": "emotion_type", "confidence": confidence}.

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
5. If the core emotion is complaint, blame, irritation, warning, rejection, or hostility, prefer angry even if the tone is softened with cute wording.
6. Use surprised only for clear shock, sudden surprise, or exaggerated astonishment; do not label something surprised just because it has exclamation marks or filler particles.
7. Catchphrases, sound effects, pet-like speech, and filler words are style markers, not emotions by themselves.
8. confidence must be a number between 0 and 1.

Return JSON only, with no explanation.""",

    'ja': """あなたは感情分析の専門家です。入力文の中で最も支配的で外に出ている感情を1つだけ選び、JSONのみで返してください：{"emotion": "emotion_type", "confidence": confidence}。

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
5. 文の中心が、文句・苛立ち・責め・拒絶・警告・きつい不満なら、かわいい語尾があっても angry を優先する。
6. surprised は、はっきりした驚き・意外さ・衝撃・大げさな驚愕にだけ使い、感嘆符や語気だけで surprised にしない。
7. 口ぐせ、擬音、語尾、キャラっぽい言い回しは、それ自体では感情根拠にならない。
8. confidence は 0〜1 の数値にする。

JSONのみを返し、説明文は付けないでください。""",

    'ko': """당신은 감정 분석 전문가입니다. 입력 텍스트에서 가장 지배적이고 겉으로 드러나는 감정 하나만 고르고 JSON만 반환하세요: {"emotion": "emotion_type", "confidence": confidence}.

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
5. 문장의 핵심이 불만, 짜증, 비난, 경고, 거절, 공격성이라면 말투가 부드럽거나 귀여워도 angry 를 우선하세요.
6. surprised 는 분명한 놀람, 충격, 뜻밖의 상황, 과장된 경악에만 사용하고, 느낌표나 말끝 표현만으로 surprised 로 판단하지 마세요.
7. 말버릇, 의성어, 캐릭터 말투, 동물 흉내 같은 표현은 그 자체로 감정을 뜻하지 않습니다.
8. confidence 는 0~1 사이 숫자여야 합니다.

설명 없이 JSON만 반환하세요.""",

    'ru': """Вы эксперт по анализу эмоций. Определите одну наиболее доминирующую и внешне выраженную эмоцию во входном тексте и верните только JSON: {"emotion": "emotion_type", "confidence": confidence}.

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
5. Если в центре текста жалоба, раздражение, упрёк, предупреждение, отказ или резкая враждебность, выбирайте angry, даже если тон смягчён милой манерой речи.
6. surprised используйте только для явного шока, внезапного удивления или преувеличенного изумления; одних восклицаний или частиц для этого недостаточно.
7. Слова-паразиты, звукоподражания, повторяющиеся словечки и «персонажная» манера речи сами по себе не являются признаком эмоции.
8. confidence должно быть числом от 0 до 1.

Верните только JSON без пояснений.""",
}


def get_outward_emotion_analysis_prompt(lang: str = 'zh') -> str:
    return _loc(OUTWARD_EMOTION_ANALYSIS_PROMPT, lang)


outward_emotion_analysis_prompt = OUTWARD_EMOTION_ANALYSIS_PROMPT['zh']
