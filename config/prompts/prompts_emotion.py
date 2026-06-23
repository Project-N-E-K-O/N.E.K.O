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

"""
Emotion-analysis prompt templates used by runtime expression / reaction systems.
"""
from __future__ import annotations

from config.prompts.prompts_sys import _loc


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

    'es': """你是一个情感分析专家。Identifica la única emoción más dominante y más visible en el texto de entrada y devuelve solo JSON: {"emotion": "emotion_type", "confidence": confidence}.

Emociones permitidas:
- happy: alegría, entusiasmo, afecto, juego, ternura, deleite, calidez
- sad: tristeza, dolor, decepción, bajón, arrepentimiento, vulnerabilidad
- angry: enojo, molestia, irritación, hostilidad, queja, explosión
- surprised: sorpresa, shock, sobresalto, algo inesperado, reacción exagerada
- neutral: calma, hechos, emoción débil, difícil de juzgar

Reglas:
1. Elige la emoción principal más fuerte, no la opción más segura.
2. No devuelvas neutral salvo que el texto sea realmente débil o plano emocionalmente.
3. Usa happy solo cuando el texto exprese claramente disfrute positivo, afecto, alegría, placer juguetón o auténtica diversión; no trates una formulación tierna o muletillas como happy por sí solas.
4. Si la emoción central es dolor, vulnerabilidad, ganas de llorar, sentirse maltratado, miedo, súplica o búsqueda de consuelo, prefiere sad aunque la redacción suene tierna o dependiente.
5. Usa angry cuando la emoción central sea culpa, hostilidad, queja, irritación, advertencia, rechazo, colapso o impaciencia. Para quejas o desprecio ocasionales, si el tono general sigue siendo ligero, bromista o tierno, usa tu criterio.
6. Usa surprised solo para shock claro, sorpresa repentina o asombro exagerado; no etiquetes como surprised solo por signos de exclamación o partículas.
7. Muletillas, efectos de sonido, habla tipo mascota y palabras de relleno son marcadores de estilo, no emociones por sí mismas.
8. confidence debe ser un número entre 0 y 1.

Devuelve solo JSON, sin explicación.""",

    'pt': """你是一个情感分析专家。Identifique a única emoção mais dominante e mais externa no texto de entrada e retorne apenas JSON: {"emotion": "emotion_type", "confidence": confidence}.

Emoções permitidas:
- happy: alegria, empolgação, afeto, brincadeira, fofura, deleite, calor
- sad: tristeza, mágoa, decepção, baixo astral, arrependimento, vulnerabilidade
- angry: raiva, incômodo, irritação, hostilidade, reclamação, explosão
- surprised: surpresa, choque, susto, inesperado, reação exagerada
- neutral: calma, factual, emoção fraca, difícil de julgar

Regras:
1. Escolha a emoção principal mais forte, não a mais segura.
2. Não retorne neutral a menos que o texto seja realmente fraco ou plano emocionalmente.
3. Use happy apenas quando o texto expressar claramente prazer positivo, afeto, deleite, prazer brincalhão ou diversão genuína; não trate uma formulação fofa ou tiques verbais sozinhos como happy.
4. Se a emoção central for mágoa, vulnerabilidade, vontade de chorar, sensação de estar sendo maltratado, medo, súplica ou busca de consolo, prefira sad mesmo que a redação soe fofa ou carente.
5. Use angry quando a emoção central for culpa, hostilidade, reclamação, irritação, aviso, rejeição, explosão ou impaciência. Para reclamações ou desprezo ocasionais, se o tom geral ainda for leve, brincalhão ou fofo, use seu julgamento.
6. Use surprised apenas para choque claro, surpresa repentina ou espanto exagerado; não rotule como surprised só por pontos de exclamação ou partículas.
7. Bordões, efeitos sonoros, fala de bichinho e palavras de preenchimento são marcadores de estilo, não emoções por si só.
8. confidence deve ser um número entre 0 e 1.

Retorne apenas JSON, sem explicação.""",
}


def get_outward_emotion_analysis_prompt(lang: str = 'zh') -> str:
    return _loc(OUTWARD_EMOTION_ANALYSIS_PROMPT, lang)


outward_emotion_analysis_prompt = OUTWARD_EMOTION_ANALYSIS_PROMPT['zh']


# ============================================================================
# Master（用户）情绪画像：二维 valence-arousal 分析 prompt
# ============================================================================
# 与上面的 OUTWARD（驱动角色头像表情）严格分开：这里分析的是「对话里说话者
# （用户）自己」的情绪，产出连续的 valence（效价）/ arousal（唤醒度）二维读数，
# 供凝神等后端基建消费。module-agnostic，不绑定任何具体角色 / 场景。
MASTER_EMOTION_VA_PROMPT = {
    'zh': """你是一个情感分析专家。请分析下面这段对话中说话者流露出的情绪状态，并只返回 JSON：{"valence": 效价, "arousal": 唤醒度, "confidence": 置信度, "complexity": 认知复杂度, "action_intent": 操作意图}。

维度定义：
- valence（效价）：-1 到 1 之间的小数。-1 = 强烈负面（痛苦、难过、愤怒、绝望），0 = 中性，+1 = 强烈正面（开心、满足、兴奋、温暖）。
- arousal（唤醒度）：0 到 1 之间的小数。0 = 平静、低能量、放松，1 = 高度激动、强烈、能量很高（无论正负）。
- confidence（置信度）：0 到 1 之间的小数，表示你对本次判断的把握。
- complexity（认知复杂度）：0 到 1 之间的小数。表示说话者正在提出一个**复杂的、客观的问题**（数学题、逻辑题、推理题、需要多步推导的客观问题等）的程度。1 = 明确在问这类烧脑客观题，0 = 没有在问、或只是闲聊／情绪倾诉／简单问题。与情绪独立判断。
- action_intent（操作意图）：0 到 1 之间的小数。表示说话者在多大程度上**明确要求执行一个对外的动作/操作**（打开、搜索、控制、运行某个外部东西，操作某 app 或设备，获取或改动外部状态）。1 = 明确就是一个操作请求，0 = 只是闲聊、倾诉、表达观点、或单纯提问而没有要求做任何事。默认取 0——只有用户明确要求"去做某件事"时才调高。与情绪和复杂度都独立判断。

判断规则：
1. 只依据这段文本本身流露的情绪，不要脑补未给出的背景。
2. valence 与 arousal 相互独立：愤怒是负效价＋高唤醒；平静的难过是负效价＋低唤醒；满足是正效价＋低唤醒；兴奋是正效价＋高唤醒。
3. 语气助词、口癖、拟声词这类风格词本身不代表情绪，不能单独作为判断依据。
4. 文本平铺直叙、情绪很弱时，valence 取接近 0，arousal 取较低值。

只返回 JSON，不要附加任何解释文本。""",

    'en': """你是一个情感分析专家。Analyze the emotional state the speaker reveals in the conversation text below and return JSON only: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "action_intent": action_intent}.

Dimensions:
- valence: a number between -1 and 1. -1 = strongly negative (distress, sadness, anger, despair), 0 = neutral, +1 = strongly positive (joy, contentment, excitement, warmth).
- arousal: a number between 0 and 1. 0 = calm, low energy, relaxed; 1 = highly activated, intense, high energy (regardless of sign).
- confidence: a number between 0 and 1 indicating your certainty.
- complexity: a number between 0 and 1 — how much the speaker is posing a COMPLEX, OBJECTIVE question (math, logic, reasoning, multi-step analytical problems). 1 = clearly asking such a hard objective question; 0 = not asking, or just chatting / venting / a simple question. Judge independently of emotion.
- action_intent: a number between 0 and 1 — how strongly the speaker is EXPLICITLY asking to perform an external action / operation (open, search, control, run something, operate an app or device, fetch or change external state). 1 = clearly an explicit action request; 0 = just chatting, venting, giving an opinion, or asking a question without asking to do anything. Default to 0 — raise it only on an explicit request to DO something. Judge independently of both emotion and complexity.

Rules:
1. Judge only from the emotion this text reveals; do not invent unstated context.
2. valence and arousal are independent: anger is negative valence + high arousal; quiet sadness is negative valence + low arousal; contentment is positive valence + low arousal; excitement is positive valence + high arousal.
3. Catchphrases, verbal tics, and sound effects are style markers, not emotions by themselves.
4. When the text is flat with weak emotion, set valence near 0 and arousal low.

Return JSON only, with no explanation.""",

    'ja': """你是一个情感分析专家。次の会話文で話し手がにじませている感情の状態を JSONのみで返してください：{"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "action_intent": action_intent}。

各次元の定義：
- valence（感情価）：-1〜1 の数値。-1 = 強い負（つらさ・悲しみ・怒り・絶望）、0 = 中立、+1 = 強い正（喜び・満足・高揚・あたたかさ）。
- arousal（覚醒度）：0〜1 の数値。0 = 落ち着き・低エネルギー・リラックス、1 = 強い興奮・激しさ・高エネルギー（正負を問わず）。
- confidence（確信度）：0〜1 の数値で、今回の判断の確かさ。
- complexity（認知的複雑さ）：0〜1 の数値。話し手が**複雑で客観的な問い**（数学・論理・推論、多段階の分析が要る客観的な問題など）をどれだけ投げかけているか。1 = 明確にそうした難しい客観的問題を問うている、0 = 問うていない、または雑談／感情の吐露／単純な質問。感情とは独立に判断する。
- action_intent（操作意図）：0〜1 の数値。話し手が**外部の動作/操作を明確に要求している**度合い（何かを開く・検索・制御・実行する、アプリや機器を操作する、外部の状態を取得/変更する）。1 = 明確に操作の要求、0 = 雑談・吐露・意見・単なる質問で何かを依頼していない。既定は 0——「何かをして」と明確に頼んだときだけ上げる。感情とも複雑さとも独立に判断する。

判断ルール：
1. この文章がにじませる感情だけで判断し、書かれていない背景を補わない。
2. valence と arousal は独立：怒りは負の感情価＋高い覚醒、静かな悲しみは負の感情価＋低い覚醒、満足は正の感情価＋低い覚醒、高揚は正の感情価＋高い覚醒。
3. 語尾、口ぐせ、擬音などの言い回しは、それ自体では感情の根拠にならない。
4. 平坦で感情が弱い文章では、valence は 0 付近、arousal は低めにする。

JSONのみを返し、説明文は付けないでください。""",

    'ko': """你是一个情感分析专家。아래 대화문에서 말하는 사람이 드러내는 감정 상태를 JSON만 반환하세요: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "action_intent": action_intent}.

차원 정의:
- valence(정서가): -1~1 사이 숫자. -1 = 강한 부정(괴로움, 슬픔, 분노, 절망), 0 = 중립, +1 = 강한 긍정(기쁨, 만족, 들뜸, 따뜻함).
- arousal(각성도): 0~1 사이 숫자. 0 = 차분함, 낮은 에너지, 이완; 1 = 강한 흥분, 격렬함, 높은 에너지(긍·부정 무관).
- confidence(확신도): 0~1 사이 숫자로 이번 판단에 대한 확신.
- complexity(인지적 복잡도): 0~1 사이 숫자. 말하는 사람이 **복잡하고 객관적인 질문**(수학·논리·추론, 다단계 분석이 필요한 객관적 문제 등)을 얼마나 던지고 있는지. 1 = 그런 어려운 객관적 문제를 분명히 묻는 중, 0 = 묻지 않음, 또는 잡담／감정 토로／단순한 질문. 감정과 독립적으로 판단.
- action_intent(조작 의도): 0~1 사이 숫자. 말하는 사람이 **외부 동작/조작을 명시적으로 요청하는** 정도(무언가를 열기·검색·제어·실행, 앱이나 기기 조작, 외부 상태 조회/변경). 1 = 분명한 조작 요청, 0 = 잡담·토로·의견·단순 질문으로 아무 것도 시키지 않음. 기본값 0 — "무엇을 해 달라"고 분명히 요청할 때만 올리세요. 감정 및 복잡도와 독립적으로 판단.

판단 규칙:
1. 이 문장이 드러내는 감정만으로 판단하고, 주어지지 않은 배경을 지어내지 마세요.
2. valence 와 arousal 은 서로 독립적입니다: 분노는 부정 정서가＋높은 각성, 조용한 슬픔은 부정 정서가＋낮은 각성, 만족은 긍정 정서가＋낮은 각성, 들뜸은 긍정 정서가＋높은 각성.
3. 말버릇, 어미, 의성어 같은 표현은 그 자체로 감정 근거가 되지 않습니다.
4. 문장이 밋밋하고 감정이 약하면 valence 는 0 근처, arousal 은 낮게 설정하세요.

설명 없이 JSON만 반환하세요.""",

    'ru': """你是一个情感分析专家。Проанализируйте эмоциональное состояние, которое говорящий выражает в приведённом ниже тексте разговора, и верните только JSON: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "action_intent": action_intent}.

Измерения:
- valence (валентность): число от -1 до 1. -1 = сильно негативное (боль, грусть, гнев, отчаяние), 0 = нейтрально, +1 = сильно позитивное (радость, удовлетворение, воодушевление, теплота).
- arousal (возбуждение): число от 0 до 1. 0 = спокойствие, низкая энергия, расслабленность; 1 = сильное возбуждение, интенсивность, высокая энергия (независимо от знака).
- confidence (уверенность): число от 0 до 1, отражающее вашу уверенность.
- complexity (когнитивная сложность): число от 0 до 1 — насколько говорящий задаёт СЛОЖНЫЙ ОБЪЕКТИВНЫЙ вопрос (математика, логика, рассуждение, многошаговые аналитические задачи). 1 = явно задаёт такой трудный объективный вопрос; 0 = не задаёт, либо просто болтает / делится чувствами / простой вопрос. Оценивайте независимо от эмоции.
- action_intent (намерение к действию): число от 0 до 1 — насколько говорящий ЯВНО просит выполнить внешнее действие/операцию (открыть, найти, управлять, запустить что-то, работать с приложением или устройством, получить/изменить внешнее состояние). 1 = явная просьба о действии; 0 = просто болтает, делится, высказывает мнение или задаёт вопрос, ничего не прося сделать. По умолчанию 0 — повышайте только при явной просьбе что-то СДЕЛАТЬ. Оценивайте независимо и от эмоции, и от сложности.

Правила:
1. Судите только по эмоции, выраженной в этом тексте; не домысливайте неуказанный контекст.
2. valence и arousal независимы: гнев — негативная валентность + высокое возбуждение; тихая грусть — негативная валентность + низкое возбуждение; удовлетворение — позитивная валентность + низкое возбуждение; воодушевление — позитивная валентность + высокое возбуждение.
3. Слова-паразиты, повторяющиеся словечки и звукоподражания сами по себе не являются признаком эмоции.
4. Если текст ровный и эмоция слабая, ставьте valence около 0 и низкий arousal.

Верните только JSON без пояснений.""",

    'es': """你是一个情感分析专家。Analiza el estado emocional que revela quien habla en el texto de conversación siguiente y devuelve solo JSON: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "action_intent": action_intent}.

Dimensiones:
- valence (valencia): un número entre -1 y 1. -1 = fuertemente negativo (dolor, tristeza, ira, desesperación), 0 = neutral, +1 = fuertemente positivo (alegría, satisfacción, entusiasmo, calidez).
- arousal (activación): un número entre 0 y 1. 0 = calma, baja energía, relajación; 1 = muy activado, intenso, alta energía (sin importar el signo).
- confidence (confianza): un número entre 0 y 1 que indica tu seguridad.
- complexity (complejidad cognitiva): un número entre 0 y 1 — cuánto está planteando quien habla una PREGUNTA COMPLEJA y OBJETIVA (matemáticas, lógica, razonamiento, problemas analíticos de varios pasos). 1 = claramente hace una de esas preguntas objetivas difíciles; 0 = no pregunta, o solo charla / se desahoga / pregunta simple. Júzgalo independientemente de la emoción.
- action_intent (intención de acción): un número entre 0 y 1 — cuánto pide EXPLÍCITAMENTE quien habla realizar una acción/operación externa (abrir, buscar, controlar, ejecutar algo, operar una app o dispositivo, obtener/cambiar estado externo). 1 = claramente una petición de acción; 0 = solo conversa, se desahoga, opina o pregunta sin pedir hacer nada. Por defecto 0 — súbelo solo ante una petición explícita de HACER algo. Júzgalo independientemente de la emoción y de la complejidad.

Reglas:
1. Juzga solo por la emoción que revela este texto; no inventes contexto no dado.
2. valence y arousal son independientes: la ira es valencia negativa + activación alta; la tristeza tranquila es valencia negativa + activación baja; la satisfacción es valencia positiva + activación baja; el entusiasmo es valencia positiva + activación alta.
3. Las muletillas, los tics verbales y los efectos de sonido son marcadores de estilo, no emociones por sí mismos.
4. Cuando el texto sea plano y con emoción débil, pon valence cerca de 0 y arousal bajo.

Devuelve solo JSON, sin explicación.""",

    'pt': """你是一个情感分析专家。Analise o estado emocional que o falante revela no texto de conversa abaixo e retorne apenas JSON: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "action_intent": action_intent}.

Dimensões:
- valence (valência): um número entre -1 e 1. -1 = fortemente negativo (sofrimento, tristeza, raiva, desespero), 0 = neutro, +1 = fortemente positivo (alegria, satisfação, empolgação, calor).
- arousal (ativação): um número entre 0 e 1. 0 = calmo, baixa energia, relaxado; 1 = muito ativado, intenso, alta energia (independente do sinal).
- confidence (confiança): um número entre 0 e 1 indicando sua certeza.
- complexity (complexidade cognitiva): um número entre 0 e 1 — o quanto o falante está fazendo uma PERGUNTA COMPLEXA e OBJETIVA (matemática, lógica, raciocínio, problemas analíticos de várias etapas). 1 = claramente faz uma dessas perguntas objetivas difíceis; 0 = não pergunta, ou apenas conversa / desabafa / pergunta simples. Julgue independentemente da emoção.
- action_intent (intenção de ação): um número entre 0 e 1 — o quanto o falante pede EXPLICITAMENTE para realizar uma ação/operação externa (abrir, buscar, controlar, executar algo, operar um app ou dispositivo, obter/alterar estado externo). 1 = claramente um pedido de ação; 0 = apenas conversa, desabafa, opina ou pergunta sem pedir para fazer nada. Padrão 0 — aumente apenas diante de um pedido explícito de FAZER algo. Julgue independentemente da emoção e da complexidade.

Regras:
1. Julgue apenas pela emoção que este texto revela; não invente contexto não fornecido.
2. valence e arousal são independentes: raiva é valência negativa + ativação alta; tristeza quieta é valência negativa + ativação baixa; satisfação é valência positiva + ativação baixa; empolgação é valência positiva + ativação alta.
3. Bordões, tiques verbais e efeitos sonoros são marcadores de estilo, não emoções por si só.
4. Quando o texto for plano e com emoção fraca, defina valence perto de 0 e arousal baixo.

Retorne apenas JSON, sem explicação.""",
}


def get_master_emotion_va_prompt(lang: str = 'zh') -> str:
    return _loc(MASTER_EMOTION_VA_PROMPT, lang)


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
        # 英文 keyword 在 _count_keyword_hits 里走 \b 词边界匹配，所以裸词
        # `happy/sad/surprised` 不会被 `unhappy/unsurprised` 等反向情绪嵌入命中。
        'happy': ('haha', 'hehe', 'happy', 'glad', 'lovely', 'yay', 'awesome'),
        'sad': ('sad', 'upset', 'depressed', 'regret', 'heartbroken'),
        'angry': ('angry', 'furious', 'annoyed', 'irritated', 'infuriating', 'outraged'),
        'surprised': ('wow', 'whoa', 'omg', 'unexpected', 'surprised'),
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
        'happy': ('счастлив', 'рада', 'весело', 'люблю', 'милый'),
        'sad': ('грустно', 'печально', 'обидно', 'жаль', 'тоск', 'плак'),
        'angry': ('злюсь', 'бесит', 'раздраж', 'ненавиж', 'разозли'),
        # `ого` (3 字符) 作为子串在所有 `-ого` 属格结尾词（`мирового/другого/много`）
        # 里假阳，去掉。剩余 `ничего себе/внезапно/удив` 已能覆盖真惊讶表达。
        'surprised': ('ничего себе', 'внезапно', 'удив'),
    },
    'es': {
        'happy': ('feliz', 'alegre', 'contento', 'contenta', 'me encanta', 'genial', 'jaja'),
        'sad': ('triste', 'dolido', 'dolida', 'deprimido', 'deprimida', 'llorar', 'me duele'),
        'angry': ('enojado', 'enojada', 'furioso', 'furiosa', 'molesto', 'molesta', 'irritado', 'irritada'),
        'surprised': ('wow', 'vaya', 'no puede ser', 'en serio', 'sorprendido', 'sorprendida'),
    },
    'pt': {
        'happy': ('feliz', 'alegre', 'contente', 'adorei', 'amei', 'legal', 'haha'),
        'sad': ('triste', 'magoado', 'magoada', 'deprimido', 'deprimida', 'chorar', 'dói'),
        'angry': ('irritado', 'irritada', 'bravo', 'brava', 'zangado', 'zangada', 'furioso', 'furiosa'),
        'surprised': ('uau', 'nossa', 'não acredito', 'nao acredito', 'sério', 'serio', 'surpreso', 'surpresa'),
    },
}

# 强烈攻击/敌意表达：命中后启发式给 angry 分数 ×2
ANGRY_ATTACK_PATTERNS_BY_LANG = {
    'zh': ('气死', '真生气', '烦死了', '恼火', '可恶', '火大', '别烦我', '受不了', '闭嘴', '炸毛了', '气炸了'),
    'en': ('shut up', 'fuck off', 'go away', 'leave me alone', 'back off', 'knock it off'),
    'ja': ('うるさい', '黙れ', 'あっち行け', 'ふざけるな', 'ふざけんな'),
    'ko': ('닥쳐', '꺼져', '저리 가', '그만해'),
    'ru': ('заткнись', 'отвали', 'уйди', 'хватит уже'),
    'es': ('cállate', 'callate', 'vete', 'déjame en paz', 'dejame en paz', 'basta ya', 'aléjate'),
    'pt': ('cala a boca', 'vai embora', 'me deixa em paz', 'chega', 'para com isso'),
}

# 脆弱/受伤表达：命中后启发式给 sad 分数 ×2
SAD_VULNERABLE_PATTERNS_BY_LANG = {
    'zh': ('委屈', '想哭', '要哭', '哭了', '呜呜', '别欺负', '不要欺负', '欺负我',
           '不要这样对我', '别这样对我', '最怕', '怕你这样说', '心里难受', '好难过', '可怜'),
    'en': ('want to cry', 'feel hurt', 'feel awful', 'miss you so', 'broke my heart'),
    'ja': ('泣きたい', 'つらすぎ', '心が痛', '落ち込んで'),
    'ko': ('울고 싶', '너무 속상', '마음이 아프', '서러워'),
    'ru': ('хочется плакать', 'так больно', 'разбил сердце', 'очень обидно'),
    'es': ('quiero llorar', 'me duele', 'me siento herido', 'me siento herida', 'me rompió el corazón'),
    'pt': ('quero chorar', 'me machucou', 'me sinto magoado', 'me sinto magoada', 'partiu meu coração'),
}

# 撒娇/玩闹表达：命中后启发式给 happy 分数 +1（仅在没有 sad/angry 信号时）
HAPPY_PLAYFUL_PATTERNS_BY_LANG = {
    'zh': ('哈哈', '嘿嘿', '嘻嘻', '贴贴', '撒娇', '可爱', '好耶'),
    'en': ('lol', 'yay', 'hehe', 'haha'),
    'ja': ('わーい', 'やったー', 'えへへ', 'うふふ'),
    'ko': ('히히', '헤헤', '꺄아', '신난다'),
    'ru': ('ура', 'хихи', 'хаха'),
    'es': ('jaja', 'jeje', 'yay', 'qué bien', 'que bien'),
    'pt': ('haha', 'hehe', 'eba', 'que bom'),
}

# 否定上下文回看 token：关键词命中前 N 字符内若出现这些 token，本次命中作废，
# 避免 "我不生气 / not angry / 화 안 나 / не злюсь" 被误判为对应情绪。
HEURISTIC_NEGATION_TOKENS_BY_LANG = {
    # 多字否定 token：假阳率低，启用宽 lookback（关键词前 _HEURISTIC_NEGATION_LOOKBACK 字符内）。
    # zh 这里收常见的 `不/没 + 程度副词` 模式，覆盖紧凑 lookback 抓不到的 2-3 字符间隔
    # 否定（如 `不是很 X / 不怎么 X / 没那么 X`）。
    'zh': ('并不', '并非', '不太', '不是很', '不算很', '不那么', '不怎么',
           '没那么', '没怎么', '没什么'),
    'en': ('not ', ' no ', 'never ', "don't", "doesn't", "didn't", "won't",
           "isn't", "aren't", "wasn't", "weren't", "can't", "cannot"),
    'ja': ('ない', 'ません', 'なくて'),
    'ko': ('안 ', '안은', '안이', '못 ', '않', '없'),
    'ru': ('не ', 'нет ', 'никогда'),
    'es': ('no ', 'nunca ', 'jamás ', 'jamas '),
    'pt': ('não ', 'nao ', 'nunca ', 'jamais '),
}

# 紧凑否定 token：仅在命中关键词紧邻前若干字符（_HEURISTIC_TIGHT_NEGATION_LOOKBACK）
# 内出现才算真否定。这是 zh 单字否定的特殊处理——`不/没/別/未` 等单字在中文里
# 假阳率极高（`不错/不思议/不具合/不愧/不仅/不可思议` 等都不是否定），但作为否定
# 又不可或缺；只要它紧邻情绪词（如 `不开心 / 不太烦`）就识别为真否定。
HEURISTIC_TIGHT_NEGATION_TOKENS_BY_LANG = {
    # zh 删除单字 `莫`：`莫名开心 / 莫名生气 / 莫名其妙` 等是常用非否定表达，
    # 而 `莫` 单字作真否定（`莫怪 / 莫管`）在现代汉语极罕见，留之假阳大于真阳。
    'zh': ('不', '别', '別', '没', '沒', '未', '勿'),
    # ko: 韩语口语里 `안좋아 / 안슬퍼 / 안화나 / 못좋아` 这种句中连写否定常见。
    # 单字 `안/못` 也会出现在 `안녕/안내/안전/안경/못이` 等非否定词组里，所以走紧凑
    # lookback：仅在命中关键词紧邻前若干字符内才算否定。
    'ko': ('안', '못'),
    'es': ('no',),
    'pt': ('não', 'nao'),
}

# 否定回看的非否定固定搭配白名单：window 中含这些短语时，把它们替换成空白后
# 再做否定 token 匹配，避免 `not only / 不仅 / не только` 这类肯定结构里的 `not / 不 / не`
# 被错误识别为真否定（`not only happy` → 应是 happy）。
HEURISTIC_NEGATION_BLOCKLIST_BY_LANG = {
    'en': ('not only', 'no doubt', 'no wonder'),
    'zh': ('不仅', '不只', '不但', '不光'),
    'ru': ('не только',),
    'es': ('no solo',),
    'pt': ('não só', 'nao so', 'não apenas', 'nao apenas'),
}

# 让步/转折连词：window 内出现这些词时，词后才算与命中关键词同小句的前文。
# 用于阻断 "not X but Y / 不是 X 而是 Y" 这种对比句把前半的否定带到后半。
HEURISTIC_CONTRAST_CONJUNCTIONS_BY_LANG = {
    'zh': ('但', '但是', '不过', '然而', '可是', '而是'),
    'en': (' but ', ' however', ' yet ', ' though', ' instead'),
    'ja': ('けど', 'けれど', 'でも', 'しかし', 'だが'),
    # ko: `하지만/그러나/근데/대신` 是独立连接词，但口语里更常见的对比是绑定词尾
    # `-지만/-는데`（如 `슬프지 않지만 행복해`），这两个也加进来
    'ko': ('하지만', '그러나', '근데', '대신', '지만', '는데'),
    'ru': (' но ', ' однако', ' зато', ' а '),
    'es': (' pero ', ' aunque ', ' sin embargo', ' en cambio'),
    'pt': (' mas ', ' porém', ' porem', ' embora ', ' no entanto', ' em vez disso'),
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
    'es': {
        'feliz': 'happy', 'alegre': 'happy', 'contento': 'happy', 'contenta': 'happy',
        'triste': 'sad', 'tristeza': 'sad', 'deprimido': 'sad', 'deprimida': 'sad',
        'enojado': 'angry', 'enojada': 'angry', 'enfadado': 'angry', 'enfadada': 'angry',
        'molesto': 'angry', 'molesta': 'angry',
        'sorprendido': 'surprised', 'sorprendida': 'surprised', 'sorpresa': 'surprised',
        'neutral': 'neutral', 'tranquilo': 'neutral', 'tranquila': 'neutral', 'calmado': 'neutral',
    },
    'pt': {
        'feliz': 'happy', 'alegre': 'happy', 'contente': 'happy', 'animado': 'happy', 'animada': 'happy',
        'triste': 'sad', 'tristeza': 'sad', 'deprimido': 'sad', 'deprimida': 'sad',
        'irritado': 'angry', 'irritada': 'angry', 'bravo': 'angry', 'brava': 'angry',
        'zangado': 'angry', 'zangada': 'angry',
        'surpreso': 'surprised', 'surpresa': 'surprised', 'chocado': 'surprised', 'chocada': 'surprised',
        'neutro': 'neutral', 'neutra': 'neutral', 'calmo': 'neutral', 'calma': 'neutral',
    },
}


def get_emotion_keywords_flat() -> dict:
    """Merge per-language emotion keywords into dict[emotion → tuple[keyword]] for flat heuristic matching."""
    merged: dict = {}
    for lang_map in EMOTION_KEYWORDS_BY_LANG.values():
        for emotion, words in lang_map.items():
            merged[emotion] = merged.get(emotion, ()) + tuple(words)
    # 跨语种去重：同一词条出现在多个语种块（如 en/pt 都收了 `haha`）时只保留一份，
    # 否则启发式按词条逐一累加命中数会对同一段文本重复计分。
    return {emotion: tuple(dict.fromkeys(words)) for emotion, words in merged.items()}


def _flatten_lang_tuples(by_lang: dict) -> tuple:
    # dict.fromkeys 保序去重，理由同上：跨语种重复词条不能导致重复计分。
    return tuple(dict.fromkeys(item for words in by_lang.values() for item in words))


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


def get_heuristic_negation_blocklist_flat() -> tuple:
    return _flatten_lang_tuples(HEURISTIC_NEGATION_BLOCKLIST_BY_LANG)


def get_heuristic_contrast_conjunctions_flat() -> tuple:
    return _flatten_lang_tuples(HEURISTIC_CONTRAST_CONJUNCTIONS_BY_LANG)


def get_emotion_label_aliases_flat() -> dict:
    """Merge per-language aliases into dict[alias → canonical], used by _normalize_emotion_label."""
    merged: dict = {}
    for lang_map in EMOTION_LABEL_ALIASES_BY_LANG.values():
        merged.update(lang_map)
    return merged
