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

from config import proactive_settings
from config.prompts.prompts_sys import _loc


OUTWARD_EMOTION_ANALYSIS_PROMPT = {
    'zh': """你是一个情感分析专家。请判断输入的角色回复中说话者自身最主导、最外显的一种情绪，并只返回 JSON：{"emotion": "情感类型", "confidence": 置信度, "emoji": null}。

可选情感只有这五种：
- happy：开心、兴奋、满足、轻快、宠溺、可爱、调皮、得意、热情
- sad：失落、难过、委屈、沮丧、低落、遗憾、脆弱
- angry：生气、不满、烦躁、攻击性、强烈指责、炸毛
- surprised：惊讶、震惊、意外、被逗到、夸张感叹、强烈新奇感
- neutral：平静、陈述事实、情绪很弱、难以判断

判断规则：
0. 分析目标是这段角色回复中说话者/角色自身表达的情绪。先确定情绪归属，再应用下面的主情绪规则：他人情绪、故事台词、转述、举例和情绪词解释不能单独作为角色自身情绪的证据。若只有这些内容而没有自身情绪表达，返回 neutral；若角色同时明确表达自己的反应，或明确用引语表达、认同自己的感受，按其自身情绪判断，不能仅因有引号就返回 neutral。confidence 表示对判断的把握，明确的 neutral 也可以有高置信度。
1. 必须优先选择说话者自身表达的“最强主情绪”，不要因为语气里带一点克制就轻易返回 neutral。
2. 说话者自身表达的情绪很弱、平静，或只是客观转述他人情绪时，返回 neutral。
3. 只有在文本明确表达开心、喜欢、得意、轻快、被逗乐、享受互动时，才判为 happy，不要把单纯可爱说法、卖萌语气、口头禅误判成 happy。
4. 如果文本主轴是委屈、想哭、脆弱、受伤、被欺负、害怕、求安慰、低落，即使语气可爱或撒娇，也应优先判为 sad。
5. 当文本主轴是指责、敌意、抱怨、烦躁、警告、拒绝、炸毛、不耐烦时判为 angry；偶尔的吐槽、嫌弃如果整体语气仍偏轻松、玩笑或可爱，可以酌情考虑。
6. surprised 只用于明显的突发惊讶、意外、震惊、夸张反应；不要只因为有感叹号、语气词就判为 surprised。
7. 语气助词、口癖、拟声词、宠物叫声这类风格词本身不代表情绪，不能单独作为判断依据。
8. confidence 取 0 到 1 之间的小数；情绪很明确时应给出较高置信度。
9. 完成情绪判断后，从以下完整候选中选一个最贴合输入的角色回复所流露反应的 emoji：{reaction_emojis}。emoji 不限于所选情感的分类候选；neutral 或拿不准时返回 null，表示没有建议，不改变 emotion 或 confidence。

只返回 JSON，不要附加任何解释文本。""",

    # Opening '你是一个情感分析专家。' remains literal Simplified Chinese in every language; en/ja/ko
    # and all templates share it, so Traditional Chinese follows without conversion.
    'zh-TW': """你是一个情感分析专家。請判斷輸入的角色回覆中說話者自身最主導、最外顯的一種情緒，並只回傳 JSON：{"emotion": "情感類型", "confidence": 信賴度, "emoji": null}。

可選情感只有這五種：
- happy：開心、興奮、滿足、輕快、寵溺、可愛、調皮、得意、熱情
- sad：失落、難過、委屈、沮喪、低落、遺憾、脆弱
- angry：生氣、不滿、煩躁、攻擊性、強烈指責、炸毛
- surprised：驚訝、震驚、意外、被逗到、誇張感嘆、強烈新奇感
- neutral：平靜、陳述事實、情緒很弱、難以判斷

判斷規則：
0. 分析目標是這段角色回覆中說話者/角色自身表達的情緒。先確定情緒歸屬，再套用下面的主情緒規則：他人情緒、故事臺詞、轉述、舉例和情緒詞解釋不能單獨作為角色自身情緒的證據。若只有這些內容而沒有自身情緒表達，回傳 neutral；若角色同時明確表達自己的反應，或明確用引語表達、認同自己的感受，按其自身情緒判斷，不能僅因有引號就回傳 neutral。confidence 表示對判斷的把握，明確的 neutral 也可以有高信賴度。
1. 必須優先選擇說話者自身表達的「最強主情緒」，不要因為語氣裡帶一點克制就輕易回傳 neutral。
2. 說話者自身表達的情緒很弱、平靜，或只是客觀轉述他人情緒時，回傳 neutral。
3. 只有在文字明確表達開心、喜歡、得意、輕快、被逗樂、享受互動時，才判為 happy，不要把單純可愛說法、賣萌語氣、口頭禪誤判成 happy。
4. 如果文字主軸是委屈、想哭、脆弱、受傷、被欺負、害怕、求安慰、低落，即使語氣可愛或撒嬌，也應優先判為 sad。
5. 當文字主軸是指責、敵意、抱怨、煩躁、警告、拒絕、炸毛、不耐煩時判為 angry；偶爾的吐槽、嫌棄如果整體語氣仍偏輕鬆、玩笑或可愛，可以酌情考量。
6. surprised 只用於明顯的突發驚訝、意外、震驚、誇張反應；不要只因為有驚嘆號、語氣詞就判為 surprised。
7. 語氣助詞、口癖、擬聲詞、寵物叫聲這類風格詞本身不代表情緒，不能單獨作為判斷依據。
8. confidence 取 0 到 1 之間的小數；情緒很明確時應給出較高信賴度。
9. 完成情緒判斷後，從以下完整候選中選一個最貼合輸入的角色回覆所流露反應的 emoji：{reaction_emojis}。emoji 不限於所選情感的分類候選；neutral 或拿不準時回傳 null，表示沒有建議，不改變 emotion 或 confidence。

只回傳 JSON，不要附加任何解釋文字。""",

    'en': """你是一个情感分析专家。Identify the speaker's own single most dominant and outward emotion expressed in the input companion reply and return JSON only: {"emotion": "emotion_type", "confidence": confidence, "emoji": null}.

Allowed emotions only:
- happy: joyful, excited, affectionate, playful, cute, delighted, warm
- sad: upset, hurt, disappointed, low, regretful, vulnerable
- angry: angry, annoyed, irritated, hostile, complaining, explosive
- surprised: surprised, shocked, startled, unexpected, exaggerated reaction
- neutral: calm, factual, weak emotion, hard to judge

Rules:
0. Analyze the speaker/companion's own expressed emotion in this reply. Determine whose emotion it is before applying the main-emotion rules below: other people's feelings, story dialogue, reported speech, examples, and definitions of emotion words alone are not evidence of the companion's own emotion. Return neutral when only such content is present without an expression of their own feelings. If the companion also clearly expresses their own reaction, or explicitly uses or endorses a quotation to express their own feelings, judge that emotion; quotation marks alone do not imply neutral. confidence measures certainty in the classification, so a clearly neutral reply may have high confidence.
1. Choose the strongest main emotion expressed by the speaker themselves, not the safest one.
2. Return neutral when the speaker's own expressed emotion is weak or calm, or they only factually report another person's emotion.
3. Use happy only when the text clearly expresses positive enjoyment, affection, delight, playful pleasure, or being genuinely amused; do not treat cute phrasing or verbal tics alone as happy.
4. If the core emotion is hurt, vulnerability, wanting to cry, feeling bullied, fear, pleading, or seeking comfort, prefer sad even if the wording sounds cute or clingy.
5. Use angry when the core emotion is blame, hostility, complaint, irritation, warning, rejection, a meltdown, or impatience. For occasional griping or contempt, if the overall tone is still light, joking, or cute, use your judgment.
6. Use surprised only for clear shock, sudden surprise, or exaggerated astonishment; do not label something surprised just because it has exclamation marks or filler particles.
7. Catchphrases, sound effects, pet-like speech, and filler words are style markers, not emotions by themselves.
8. confidence must be a number between 0 and 1.
9. After judging the emotion, choose one emoji that best reflects the companion reply's reaction from this full list: {reaction_emojis}. The emoji is not restricted to the chosen emotion's category; return null for neutral or when unsure, meaning no suggestion, without changing emotion or confidence.

Return JSON only, with no explanation.""",

    'ja': """你是一个情感分析专家。入力されたキャラクターの返答で話し手自身が表す、最も支配的で外に出ている感情を1つだけ選び、JSONのみで返してください：{"emotion": "emotion_type", "confidence": confidence, "emoji": null}。

使用できる感情は次の5つのみです：
- happy：喜ぶ、嬉しい、楽しい、わくわく、幸せ、かわいい、甘える
- sad：悲しい、落ち込む、つらい、しょんぼり、寂しい、悔しい
- angry：怒っている、腹が立つ、イライラ、不満、ムカつく、きつく責める
- surprised：驚いた、びっくり、意外、衝撃、思わず叫ぶ、大げさな反応
- neutral：無表情、平坦、落ち着いている、事実を述べるだけ、感情が弱い

判断ルール：
0. 分析対象は、このキャラクターの返答で話し手自身が表している感情です。まず誰の感情かを確認し、その後で以下の主感情のルールを適用してください。他人の感情、物語の台詞、伝聞、例文、感情語の説明だけを話し手自身の感情の根拠にしないでください。自身の感情表現がなく、それらだけなら neutral にしてください。自身の反応も明確に表している場合や、引用を明確に自身の気持ちとして使う・肯定する場合は、その感情を判断してください。引用符があるだけで neutral にしないでください。confidence は判断への確信度なので、明確な neutral にも高い値を付けられます。
1. 話し手自身が表すもっとも強い主感情を選び、無難だからという理由で neutral を選ばない。
2. 話し手自身の感情が弱い・落ち着いている場合や、他人の感情を客観的に伝えるだけの場合は neutral にする。
3. happy は、嬉しさ・好意・楽しさ・はしゃぎ・本当に喜んでいる反応が明確なときだけ使い、かわいい言い回しや口ぐせだけで happy にしない。
4. 文の中心が、傷つき・しんどさ・泣きたさ・いじけ・甘えを含む弱さ・慰めを求める気持ちなら、言い方がかわいくても sad を優先する。
5. angry は、文の中心が責め・敵意・不満・苛立ち・警告・拒絶・激怒・苛々であるときに使う。軽い愚痴・嫌気でも、全体の雰囲気が軽い・ふざけている・かわいい場合は状況に応じて判断する。
6. surprised は、はっきりした驚き・意外さ・衝撃・大げさな驚愕にだけ使い、感嘆符や語気だけで surprised にしない。
7. 口ぐせ、擬音、語尾、キャラっぽい言い回しは、それ自体では感情根拠にならない。
8. confidence は 0〜1 の数値にする。
9. 感情を判断した後、入力されたキャラクターの返答に表れた反応に最も合う emoji を次の全候補から1つ選ぶ：{reaction_emojis}。選んだ感情のカテゴリに限定しない。neutral または迷う場合は提案なしを表す null を返し、emotion や confidence は変更しない。

JSONのみを返し、説明文は付けないでください。""",

    'ko': """你是一个情感分析专家。입력된 캐릭터 답변에서 화자 자신이 표현하는 가장 지배적이고 겉으로 드러나는 감정 하나만 고르고 JSON만 반환하세요: {"emotion": "emotion_type", "confidence": confidence, "emoji": null}.

허용되는 감정은 다음 다섯 가지뿐입니다:
- happy: 행복, 즐거움, 기쁨, 신남, 설렘, 애정, 귀여움
- sad: 슬픔, 우울함, 속상함, 서운함, 실망, 풀이 죽음
- angry: 화남, 분노, 짜증, 불만, 열받음, 공격적인 반응
- surprised: 놀람, 깜짝 놀람, 당황, 의외, 충격, 과장된 감탄
- neutral: 무표정, 담담함, 차분함, 사실 전달, 감정이 약함

판단 규칙:
0. 분석 대상은 이 캐릭터 답변에서 화자 자신이 표현하는 감정입니다. 먼저 누구의 감정인지 확인한 뒤 아래 주감정 규칙을 적용하세요. 다른 사람의 감정, 이야기 속 대사, 전달한 말, 예문, 감정 단어의 설명만으로 화자 자신의 감정을 판단하지 마세요. 자신의 감정 표현 없이 이런 내용만 있으면 neutral 을 반환하세요. 자신의 반응도 명확히 표현하거나 인용을 자신의 감정으로 명확히 사용하거나 인정하면 그 감정을 판단하세요. 따옴표가 있다는 이유만으로 neutral 로 판단하지 마세요. confidence 는 분류에 대한 확신이므로 명확한 neutral 에도 높은 값을 줄 수 있습니다.
1. 화자 자신이 표현하는 가장 강한 주감정을 고르고, 안전해 보여서 neutral 을 고르지 마세요.
2. 화자 자신의 감정이 약하거나 차분하거나 다른 사람의 감정을 객관적으로 전달하기만 하면 neutral 을 사용하세요.
3. happy 는 실제로 즐거움, 애정, 들뜸, 만족, 장난스러운 즐거움이 분명할 때만 사용하고, 단순히 귀여운 말투나 말버릇만으로 happy 로 판단하지 마세요.
4. 문장의 핵심이 속상함, 상처, 울고 싶음, 서러움, 괴롭힘당하는 느낌, 두려움, 위로를 바라는 마음이라면 말투가 귀여워도 sad 를 우선하세요.
5. angry 는 문장의 핵심이 비난, 적의, 불만, 짜증, 경고, 거절, 폭발, 조급함일 때 사용하세요. 가벼운 투정이나 싫어함이라도 전체 분위기가 가볍거나 장난스럽거나 귀엽다면 상황에 따라 판단하세요.
6. surprised 는 분명한 놀람, 충격, 뜻밖의 상황, 과장된 경악에만 사용하고, 느낌표나 말끝 표현만으로 surprised 로 판단하지 마세요.
7. 말버릇, 의성어, 캐릭터 말투, 동물 흉내 같은 표현은 그 자체로 감정을 뜻하지 않습니다.
8. confidence 는 0~1 사이 숫자여야 합니다.
9. 감정을 판단한 뒤, 입력된 캐릭터 답변의 반응에 가장 잘 맞는 emoji 하나를 다음 전체 후보에서 고르세요: {reaction_emojis}. 선택한 감정의 범주로 제한하지 마세요. neutral 이거나 확신이 없으면 제안이 없다는 뜻의 null 을 반환하고 emotion 이나 confidence 는 바꾸지 마세요.

설명 없이 JSON만 반환하세요.""",

    'ru': """你是一个情感分析专家。Определите одну наиболее доминирующую и внешне выраженную собственную эмоцию говорящего в ответе персонажа и верните только JSON: {"emotion": "emotion_type", "confidence": confidence, "emoji": null}.

Допустимы только 5 эмоций:
- happy: радость, счастье, веселье, восторг, тёплое чувство, игривость, умиление
- sad: грусть, печаль, подавленность, обида, сожаление, разочарование
- angry: злость, раздражение, гнев, недовольство, резкость, вспышка
- surprised: удивление, шок, неожиданность, изумление, вскрик, сильная реакция
- neutral: безэмоционально, ровно, спокойно, констатация факта, эмоция слабо выражена

Правила:
0. Анализируйте собственную выраженную эмоцию говорящего персонажа в этом ответе. Сначала определите, кому принадлежит эмоция, затем применяйте правила основной эмоции ниже. Чужие чувства, реплики из рассказа, пересказ, примеры и определения эмоциональных слов сами по себе не являются свидетельством эмоции говорящего. Если присутствует только такое содержание без выражения собственных чувств, верните neutral. Если персонаж явно выражает и свою реакцию или явно использует либо поддерживает цитату как выражение собственных чувств, определяйте эту эмоцию; одни кавычки не означают neutral. confidence отражает уверенность в классификации, поэтому явно нейтральный ответ тоже может иметь высокую уверенность.
1. Выбирайте самую сильную основную эмоцию самого говорящего, а не самую безопасную.
2. Возвращайте neutral, если собственная выраженная эмоция говорящего слабая или спокойная, либо он лишь объективно сообщает о чужой эмоции.
3. Используйте happy только когда в тексте явно есть радость, удовольствие, тёплая привязанность, игривое удовольствие или искреннее веселье; милый стиль речи или словечки сами по себе не означают happy.
4. Если в центре текста обида, уязвимость, желание заплакать, ощущение, что обижают, страх, мольба или поиск утешения, выбирайте sad, даже если формулировка звучит мило.
5. Используйте angry, когда центр текста — упрёки, враждебность, жалоба, раздражение, предупреждение, отказ, вспышка гнева или нетерпение. При случайном ворчании или неприязни, если общий тон всё ещё лёгкий, шутливый или милый, действуйте по обстоятельствам.
6. surprised используйте только для явного шока, внезапного удивления или преувеличенного изумления; одних восклицаний или частиц для этого недостаточно.
7. Слова-паразиты, звукоподражания, повторяющиеся словечки и «персонажная» манера речи сами по себе не являются признаком эмоции.
8. confidence должно быть числом от 0 до 1.
9. После определения эмоции выберите один emoji, лучше всего передающий реакцию в ответе персонажа, из полного списка: {reaction_emojis}. Не ограничивайте выбор категорией выбранной эмоции; для neutral или при неуверенности верните null (нет предложения), не меняя emotion или confidence.

Верните только JSON без пояснений.""",

    'es': """你是一个情感分析专家。Identifica la única emoción propia más dominante y más visible que expresa el hablante en la respuesta del personaje y devuelve solo JSON: {"emotion": "emotion_type", "confidence": confidence, "emoji": null}.

Emociones permitidas:
- happy: alegría, entusiasmo, afecto, juego, ternura, deleite, calidez
- sad: tristeza, dolor, decepción, bajón, arrepentimiento, vulnerabilidad
- angry: enojo, molestia, irritación, hostilidad, queja, explosión
- surprised: sorpresa, shock, sobresalto, algo inesperado, reacción exagerada
- neutral: calma, hechos, emoción débil, difícil de juzgar

Reglas:
0. Analiza la emoción propia que expresa el hablante/personaje en esta respuesta. Determina primero de quién es la emoción y después aplica las reglas de emoción principal siguientes. Los sentimientos ajenos, los diálogos de cuentos, el discurso referido, los ejemplos y las definiciones de palabras emocionales por sí solos no demuestran la emoción propia del hablante. Devuelve neutral si solo aparece ese contenido sin expresión de sentimientos propios. Si el personaje también expresa claramente su propia reacción, o usa o adopta explícitamente una cita para expresar sus propios sentimientos, juzga esa emoción; las comillas por sí solas no implican neutral. confidence mide la certeza de la clasificación, por lo que una respuesta claramente neutral también puede tener confianza alta.
1. Elige la emoción propia más fuerte que expresa el hablante, no la opción más segura.
2. Devuelve neutral cuando la emoción propia del hablante sea débil o tranquila, o cuando solo informe objetivamente de la emoción de otra persona.
3. Usa happy solo cuando el texto exprese claramente disfrute positivo, afecto, alegría, placer juguetón o auténtica diversión; no trates una formulación tierna o muletillas como happy por sí solas.
4. Si la emoción central es dolor, vulnerabilidad, ganas de llorar, sentirse maltratado, miedo, súplica o búsqueda de consuelo, prefiere sad aunque la redacción suene tierna o dependiente.
5. Usa angry cuando la emoción central sea culpa, hostilidad, queja, irritación, advertencia, rechazo, colapso o impaciencia. Para quejas o desprecio ocasionales, si el tono general sigue siendo ligero, bromista o tierno, usa tu criterio.
6. Usa surprised solo para shock claro, sorpresa repentina o asombro exagerado; no etiquetes como surprised solo por signos de exclamación o partículas.
7. Muletillas, efectos de sonido, habla tipo mascota y palabras de relleno son marcadores de estilo, no emociones por sí mismas.
8. confidence debe ser un número entre 0 y 1.
9. Tras determinar la emoción, elige un emoji que refleje mejor la reacción de la respuesta del personaje entre todos estos candidatos: {reaction_emojis}. No limites la elección a la categoría de la emoción; devuelve null para neutral o si tienes dudas, indicando que no hay sugerencia, sin cambiar emotion ni confidence.

Devuelve solo JSON, sin explicación.""",

    'pt': """你是一个情感分析专家。Identifique a única emoção própria mais dominante e mais externa expressa pelo falante na resposta do personagem e retorne apenas JSON: {"emotion": "emotion_type", "confidence": confidence, "emoji": null}.

Emoções permitidas:
- happy: alegria, empolgação, afeto, brincadeira, fofura, deleite, calor
- sad: tristeza, mágoa, decepção, baixo astral, arrependimento, vulnerabilidade
- angry: raiva, incômodo, irritação, hostilidade, reclamação, explosão
- surprised: surpresa, choque, susto, inesperado, reação exagerada
- neutral: calma, factual, emoção fraca, difícil de julgar

Regras:
0. Analise a emoção própria expressa pelo falante/personagem nesta resposta. Determine primeiro de quem é a emoção e depois aplique as regras de emoção principal abaixo. Sentimentos alheios, falas de histórias, discurso relatado, exemplos e definições de palavras emocionais, por si só, não demonstram a emoção própria do falante. Retorne neutral se houver apenas esse conteúdo sem expressão de sentimentos próprios. Se o personagem também expressar claramente sua própria reação, ou usar ou adotar explicitamente uma citação para expressar seus próprios sentimentos, julgue essa emoção; aspas por si só não implicam neutral. confidence mede a certeza da classificação, portanto uma resposta claramente neutral também pode ter confiança alta.
1. Escolha a emoção própria mais forte expressa pelo falante, não a mais segura.
2. Retorne neutral quando a emoção própria do falante for fraca ou calma, ou quando ele apenas relatar objetivamente a emoção de outra pessoa.
3. Use happy apenas quando o texto expressar claramente prazer positivo, afeto, deleite, prazer brincalhão ou diversão genuína; não trate uma formulação fofa ou tiques verbais sozinhos como happy.
4. Se a emoção central for mágoa, vulnerabilidade, vontade de chorar, sensação de estar sendo maltratado, medo, súplica ou busca de consolo, prefira sad mesmo que a redação soe fofa ou carente.
5. Use angry quando a emoção central for culpa, hostilidade, reclamação, irritação, aviso, rejeição, explosão ou impaciência. Para reclamações ou desprezo ocasionais, se o tom geral ainda for leve, brincalhão ou fofo, use seu julgamento.
6. Use surprised apenas para choque claro, surpresa repentina ou espanto exagerado; não rotule como surprised só por pontos de exclamação ou partículas.
7. Bordões, efeitos sonoros, fala de bichinho e palavras de preenchimento são marcadores de estilo, não emoções por si só.
8. confidence deve ser um número entre 0 e 1.
9. Após determinar a emoção, escolha um emoji que melhor represente a reação na resposta do personagem entre todos estes candidatos: {reaction_emojis}. Não limite a escolha à categoria da emoção; retorne null para neutral ou em caso de dúvida, indicando nenhuma sugestão, sem mudar emotion ou confidence.

Retorne apenas JSON, sem explicação.""",
}


def get_outward_emotion_analysis_prompt(lang: str = 'zh') -> str:
    emojis = dict.fromkeys(
        emoji
        for candidates in proactive_settings.MESSAGE_REACTION_EMOJIS_BY_EMOTION.values()
        for emoji in candidates
    )
    return _loc(OUTWARD_EMOTION_ANALYSIS_PROMPT, lang).replace(
        '{reaction_emojis}', ' '.join(emojis)
    )


outward_emotion_analysis_prompt = get_outward_emotion_analysis_prompt('zh')


# ============================================================================
# Master (user) emotion profile: two-dimensional valence-arousal analysis prompt.
# ============================================================================
# Strictly separate from OUTWARD above (character-avatar expression): analyze the conversation speaker's
# own (user) emotion, producing continuous valence / arousal coordinates
# for backend infrastructure such as focused cognition. Module-agnostic, tied to no character/scenario.
MASTER_EMOTION_VA_PROMPT = {
    'zh': """你是一个情感分析专家。请分析下面这段对话中说话者流露出的情绪状态，并只返回 JSON：{"valence": 效价, "arousal": 唤醒度, "confidence": 置信度, "complexity": 认知复杂度, "external_intent": 外部意图}。

维度定义：
- valence（效价）：-1 到 1 之间的小数。-1 = 强烈负面（痛苦、难过、愤怒、绝望），0 = 中性，+1 = 强烈正面（开心、满足、兴奋、温暖）。
- arousal（唤醒度）：0 到 1 之间的小数。0 = 平静、低能量、放松，1 = 高度激动、强烈、能量很高（无论正负）。
- confidence（置信度）：0 到 1 之间的小数，表示你对本次判断的把握。
- complexity（认知复杂度）：0 到 1 之间的小数。表示说话者正在提出一个**复杂的、客观的问题**（数学题、逻辑题、推理题、需要多步推导的客观问题等）的程度。1 = 明确在问这类烧脑客观题，0 = 没有在问、或只是闲聊／情绪倾诉／简单问题。与情绪独立判断。
- external_intent（外部意图）：0 到 1 之间的小数。表示这一轮在多大程度上**需要外部能力**——要么明确要求一个对外操作（打开、搜索、控制、运行某个外部东西，操作某 app 或设备，改动外部状态），要么需要外部、实时、或超出你已知范围的信息才能回答（天气、价格、新闻、实时状态等；这类外部/实时信息的疑问句也算）。1 = 显然需要，0 = 只是闲聊、倾诉、表达观点、或凭对话和常识就能回答。与情绪和复杂度都独立判断：纯靠推理就能解的难题（数学、逻辑）归 complexity，这里仍取低。

判断规则：
1. 只依据这段文本本身流露的情绪，不要脑补未给出的背景。
2. valence 与 arousal 相互独立：愤怒是负效价＋高唤醒；平静的难过是负效价＋低唤醒；满足是正效价＋低唤醒；兴奋是正效价＋高唤醒。
3. 语气助词、口癖、拟声词这类风格词本身不代表情绪，不能单独作为判断依据。
4. 文本平铺直叙、情绪很弱时，valence 取接近 0，arousal 取较低值。

只返回 JSON，不要附加任何解释文本。""",

    'zh-TW': """你是一个情感分析专家。請分析下面這段對話中說話者流露出的情緒狀態，並只回傳 JSON：{"valence": 效價, "arousal": 喚醒度, "confidence": 信賴度, "complexity": 認知複雜度, "external_intent": 外部意圖}。

維度定義：
- valence（效價）：-1 到 1 之間的小數。-1 = 強烈負面（痛苦、難過、憤怒、絕望），0 = 中性，+1 = 強烈正面（開心、滿足、興奮、溫暖）。
- arousal（喚醒度）：0 到 1 之間的小數。0 = 平靜、低能量、放鬆，1 = 高度激動、強烈、能量很高（無論正負）。
- confidence（信賴度）：0 到 1 之間的小數，表示你對本次判斷的把握。
- complexity（認知複雜度）：0 到 1 之間的小數。表示說話者正在提出一個**複雜的、客觀的問題**（數學題、邏輯題、推理題、需要多步推導的客觀問題等）的程度。1 = 明確在問這類燒腦客觀題，0 = 沒有在問、或只是閒聊／情緒傾訴／簡單問題。與情緒獨立判斷。
- external_intent（外部意圖）：0 到 1 之間的小數。表示這一輪在多大程度上**需要外部能力**——要麼明確要求一個對外操作（開啟、搜尋、控制、執行某個外部東西，操作某 app 或裝置，改動外部狀態），要麼需要外部、即時、或超出你已知範圍的資訊才能回答（天氣、價格、新聞、即時狀態等；這類外部/即時資訊的疑問句也算）。1 = 顯然需要，0 = 只是閒聊、傾訴、表達觀點、或憑對話和常識就能回答。與情緒和複雜度都獨立判斷：純靠推理就能解的難題（數學、邏輯）歸 complexity，這裡仍取低。

判斷規則：
1. 只依據這段文字本身流露的情緒，不要腦補未給出的背景。
2. valence 與 arousal 相互獨立：憤怒是負效價＋高喚醒；平靜的難過是負效價＋低喚醒；滿足是正效價＋低喚醒；興奮是正效價＋高喚醒。
3. 語氣助詞、口癖、擬聲詞這類風格詞本身不代表情緒，不能單獨作為判斷依據。
4. 文字平鋪直敘、情緒很弱時，valence 取接近 0，arousal 取較低值。

只回傳 JSON，不要附加任何解釋文字。""",

    'en': """你是一个情感分析专家。Analyze the emotional state the speaker reveals in the conversation text below and return JSON only: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "external_intent": external_intent}.

Dimensions:
- valence: a number between -1 and 1. -1 = strongly negative (distress, sadness, anger, despair), 0 = neutral, +1 = strongly positive (joy, contentment, excitement, warmth).
- arousal: a number between 0 and 1. 0 = calm, low energy, relaxed; 1 = highly activated, intense, high energy (regardless of sign).
- confidence: a number between 0 and 1 indicating your certainty.
- complexity: a number between 0 and 1 — how much the speaker is posing a COMPLEX, OBJECTIVE question (math, logic, reasoning, multi-step analytical problems). 1 = clearly asking such a hard objective question; 0 = not asking, or just chatting / venting / a simple question. Judge independently of emotion.
- external_intent: a number between 0 and 1 — how much this turn needs an external capability: either it EXPLICITLY asks to perform an external action (open, search, control, run something, operate an app or device, change external state), OR it needs external, real-time, or beyond-what-you-know information to answer (weather, prices, news, live status, etc.; questions about those external/live facts count too). 1 = clearly needed; 0 = just chatting, venting, giving an opinion, or answerable from the conversation and common sense. Judge independently of both emotion and complexity: a hard problem solvable by pure reasoning (math, logic) belongs to complexity and stays low here.

Rules:
1. Judge only from the emotion this text reveals; do not invent unstated context.
2. valence and arousal are independent: anger is negative valence + high arousal; quiet sadness is negative valence + low arousal; contentment is positive valence + low arousal; excitement is positive valence + high arousal.
3. Catchphrases, verbal tics, and sound effects are style markers, not emotions by themselves.
4. When the text is flat with weak emotion, set valence near 0 and arousal low.

Return JSON only, with no explanation.""",

    'ja': """你是一个情感分析专家。次の会話文で話し手がにじませている感情の状態を JSONのみで返してください：{"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "external_intent": external_intent}。

各次元の定義：
- valence（感情価）：-1〜1 の数値。-1 = 強い負（つらさ・悲しみ・怒り・絶望）、0 = 中立、+1 = 強い正（喜び・満足・高揚・あたたかさ）。
- arousal（覚醒度）：0〜1 の数値。0 = 落ち着き・低エネルギー・リラックス、1 = 強い興奮・激しさ・高エネルギー（正負を問わず）。
- confidence（確信度）：0〜1 の数値で、今回の判断の確かさ。
- complexity（認知的複雑さ）：0〜1 の数値。話し手が**複雑で客観的な問い**（数学・論理・推論、多段階の分析が要る客観的な問題など）をどれだけ投げかけているか。1 = 明確にそうした難しい客観的問題を問うている、0 = 問うていない、または雑談／感情の吐露／単純な質問。感情とは独立に判断する。
- external_intent（外部意図）：0〜1 の数値。このターンが**外部の能力をどれだけ必要としているか**——外部の動作を明確に要求している（何かを開く・検索・制御・実行する、アプリや機器を操作する、外部の状態を変える）か、または外部・リアルタイム・あなたの既知を超える情報がないと答えられない（天気・価格・ニュース・リアルタイムの状態など。こうした外部・リアルタイム情報を尋ねる疑問文も含む）。1 = 明らかに必要、0 = 雑談・吐露・意見、または会話と常識だけで答えられる。感情とも複雑さとも独立に判断する：純粋な推論だけで解ける難問（数学・論理）は complexity に属し、ここでは低いまま。

判断ルール：
1. この文章がにじませる感情だけで判断し、書かれていない背景を補わない。
2. valence と arousal は独立：怒りは負の感情価＋高い覚醒、静かな悲しみは負の感情価＋低い覚醒、満足は正の感情価＋低い覚醒、高揚は正の感情価＋高い覚醒。
3. 語尾、口ぐせ、擬音などの言い回しは、それ自体では感情の根拠にならない。
4. 平坦で感情が弱い文章では、valence は 0 付近、arousal は低めにする。

JSONのみを返し、説明文は付けないでください。""",

    'ko': """你是一个情感分析专家。아래 대화문에서 말하는 사람이 드러내는 감정 상태를 JSON만 반환하세요: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "external_intent": external_intent}.

차원 정의:
- valence(정서가): -1~1 사이 숫자. -1 = 강한 부정(괴로움, 슬픔, 분노, 절망), 0 = 중립, +1 = 강한 긍정(기쁨, 만족, 들뜸, 따뜻함).
- arousal(각성도): 0~1 사이 숫자. 0 = 차분함, 낮은 에너지, 이완; 1 = 강한 흥분, 격렬함, 높은 에너지(긍·부정 무관).
- confidence(확신도): 0~1 사이 숫자로 이번 판단에 대한 확신.
- complexity(인지적 복잡도): 0~1 사이 숫자. 말하는 사람이 **복잡하고 객관적인 질문**(수학·논리·추론, 다단계 분석이 필요한 객관적 문제 등)을 얼마나 던지고 있는지. 1 = 그런 어려운 객관적 문제를 분명히 묻는 중, 0 = 묻지 않음, 또는 잡담／감정 토로／단순한 질문. 감정과 독립적으로 판단.
- external_intent(외부 의도): 0~1 사이 숫자. 이 턴이 **외부 능력을 얼마나 필요로 하는지**——외부 동작을 명시적으로 요청하거나(무언가를 열기·검색·제어·실행, 앱이나 기기 조작, 외부 상태 변경), 또는 외부·실시간·당신이 아는 범위를 넘는 정보가 있어야 답할 수 있는 경우(날씨·가격·뉴스·실시간 상태 등. 이런 외부·실시간 정보를 묻는 의문문도 포함). 1 = 분명히 필요, 0 = 잡담·토로·의견, 또는 대화와 상식만으로 답할 수 있음. 감정 및 복잡도와 독립적으로 판단: 순수한 추론만으로 풀리는 어려운 문제(수학·논리)는 complexity 에 속하며 여기서는 낮게 유지.

판단 규칙:
1. 이 문장이 드러내는 감정만으로 판단하고, 주어지지 않은 배경을 지어내지 마세요.
2. valence 와 arousal 은 서로 독립적입니다: 분노는 부정 정서가＋높은 각성, 조용한 슬픔은 부정 정서가＋낮은 각성, 만족은 긍정 정서가＋낮은 각성, 들뜸은 긍정 정서가＋높은 각성.
3. 말버릇, 어미, 의성어 같은 표현은 그 자체로 감정 근거가 되지 않습니다.
4. 문장이 밋밋하고 감정이 약하면 valence 는 0 근처, arousal 은 낮게 설정하세요.

설명 없이 JSON만 반환하세요.""",

    'ru': """你是一个情感分析专家。Проанализируйте эмоциональное состояние, которое говорящий выражает в приведённом ниже тексте разговора, и верните только JSON: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "external_intent": external_intent}.

Измерения:
- valence (валентность): число от -1 до 1. -1 = сильно негативное (боль, грусть, гнев, отчаяние), 0 = нейтрально, +1 = сильно позитивное (радость, удовлетворение, воодушевление, теплота).
- arousal (возбуждение): число от 0 до 1. 0 = спокойствие, низкая энергия, расслабленность; 1 = сильное возбуждение, интенсивность, высокая энергия (независимо от знака).
- confidence (уверенность): число от 0 до 1, отражающее вашу уверенность.
- complexity (когнитивная сложность): число от 0 до 1 — насколько говорящий задаёт СЛОЖНЫЙ ОБЪЕКТИВНЫЙ вопрос (математика, логика, рассуждение, многошаговые аналитические задачи). 1 = явно задаёт такой трудный объективный вопрос; 0 = не задаёт, либо просто болтает / делится чувствами / простой вопрос. Оценивайте независимо от эмоции.
- external_intent (внешнее намерение): число от 0 до 1 — насколько этот ход требует внешней возможности: либо ЯВНО просит выполнить внешнее действие (открыть, найти, управлять, запустить что-то, работать с приложением или устройством, изменить внешнее состояние), либо требует внешней, реального времени или выходящей за пределы известного вам информации для ответа (погода, цены, новости, текущий статус и т. п.; вопросы о таких внешних/актуальных данных тоже считаются). 1 = явно нужно; 0 = болтовня, излияние чувств, мнение или ответ из разговора и здравого смысла. Оценивайте независимо и от эмоции, и от сложности: трудная задача, решаемая чистым рассуждением (математика, логика), относится к complexity и здесь остаётся низкой.

Правила:
1. Судите только по эмоции, выраженной в этом тексте; не домысливайте неуказанный контекст.
2. valence и arousal независимы: гнев — негативная валентность + высокое возбуждение; тихая грусть — негативная валентность + низкое возбуждение; удовлетворение — позитивная валентность + низкое возбуждение; воодушевление — позитивная валентность + высокое возбуждение.
3. Слова-паразиты, повторяющиеся словечки и звукоподражания сами по себе не являются признаком эмоции.
4. Если текст ровный и эмоция слабая, ставьте valence около 0 и низкий arousal.

Верните только JSON без пояснений.""",

    'es': """你是一个情感分析专家。Analiza el estado emocional que revela quien habla en el texto de conversación siguiente y devuelve solo JSON: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "external_intent": external_intent}.

Dimensiones:
- valence (valencia): un número entre -1 y 1. -1 = fuertemente negativo (dolor, tristeza, ira, desesperación), 0 = neutral, +1 = fuertemente positivo (alegría, satisfacción, entusiasmo, calidez).
- arousal (activación): un número entre 0 y 1. 0 = calma, baja energía, relajación; 1 = muy activado, intenso, alta energía (sin importar el signo).
- confidence (confianza): un número entre 0 y 1 que indica tu seguridad.
- complexity (complejidad cognitiva): un número entre 0 y 1 — cuánto está planteando quien habla una PREGUNTA COMPLEJA y OBJETIVA (matemáticas, lógica, razonamiento, problemas analíticos de varios pasos). 1 = claramente hace una de esas preguntas objetivas difíciles; 0 = no pregunta, o solo charla / se desahoga / pregunta simple. Júzgalo independientemente de la emoción.
- external_intent (intención externa): un número entre 0 y 1 — cuánto necesita este turno una capacidad externa: o bien pide EXPLÍCITAMENTE realizar una acción externa (abrir, buscar, controlar, ejecutar algo, operar una app o dispositivo, cambiar estado externo), o bien necesita información externa, en tiempo real o más allá de lo que sabes para responder (clima, precios, noticias, estado en vivo, etc.; las preguntas sobre esos datos externos/en vivo también cuentan). 1 = claramente necesario; 0 = charla, desahogo, opinión, o se responde con la conversación y el sentido común. Júzgalo independientemente de la emoción y de la complejidad: un problema difícil resoluble por puro razonamiento (matemáticas, lógica) pertenece a complexity y aquí permanece bajo.

Reglas:
1. Juzga solo por la emoción que revela este texto; no inventes contexto no dado.
2. valence y arousal son independientes: la ira es valencia negativa + activación alta; la tristeza tranquila es valencia negativa + activación baja; la satisfacción es valencia positiva + activación baja; el entusiasmo es valencia positiva + activación alta.
3. Las muletillas, los tics verbales y los efectos de sonido son marcadores de estilo, no emociones por sí mismos.
4. Cuando el texto sea plano y con emoción débil, pon valence cerca de 0 y arousal bajo.

Devuelve solo JSON, sin explicación.""",

    'pt': """你是一个情感分析专家。Analise o estado emocional que o falante revela no texto de conversa abaixo e retorne apenas JSON: {"valence": valence, "arousal": arousal, "confidence": confidence, "complexity": complexity, "external_intent": external_intent}.

Dimensões:
- valence (valência): um número entre -1 e 1. -1 = fortemente negativo (sofrimento, tristeza, raiva, desespero), 0 = neutro, +1 = fortemente positivo (alegria, satisfação, empolgação, calor).
- arousal (ativação): um número entre 0 e 1. 0 = calmo, baixa energia, relaxado; 1 = muito ativado, intenso, alta energia (independente do sinal).
- confidence (confiança): um número entre 0 e 1 indicando sua certeza.
- complexity (complexidade cognitiva): um número entre 0 e 1 — o quanto o falante está fazendo uma PERGUNTA COMPLEXA e OBJETIVA (matemática, lógica, raciocínio, problemas analíticos de várias etapas). 1 = claramente faz uma dessas perguntas objetivas difíceis; 0 = não pergunta, ou apenas conversa / desabafa / pergunta simples. Julgue independentemente da emoção.
- external_intent (intenção externa): um número entre 0 e 1 — o quanto este turno precisa de uma capacidade externa: ou pede EXPLICITAMENTE para realizar uma ação externa (abrir, buscar, controlar, executar algo, operar um app ou dispositivo, mudar estado externo), ou precisa de informação externa, em tempo real ou além do que você sabe para responder (clima, preços, notícias, status ao vivo, etc.; perguntas sobre esses dados externos/ao vivo também contam). 1 = claramente necessário; 0 = conversa, desabafo, opinião, ou respondível pela conversa e bom senso. Julgue independentemente da emoção e da complexidade: um problema difícil solúvel por puro raciocínio (matemática, lógica) pertence a complexity e aqui permanece baixo.

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
# i18n keyword tables for heuristic emotion classification.
# ============================================================================
# Dictionaries below are organized by language; system_router._infer_emotion_from_text uses
# helpers below to flatten them for substring matching. Add/change a language's words in
# its block without editing system_router.
# Data follows.

# Keywords per language and emotion (each match adds 1).
#
# zh and zh-TW are independent language blocks, not two spellings of the same table. Flatten
# into one cross-language table before substring matching (see *_flat helpers at file end), matching
# actual user text regardless of UI language. Traditional users typing '好開心' score only if Traditional spelling
# exists in table; `开心` matches none of those characters.
#
# Pair blocks entry by entry, including identical spellings such as `哈哈`, so changes on one side clearly show
# what to change on the other; dict.fromkeys removes duplicate entries during flattening, preventing duplicate scoring.
EMOTION_KEYWORDS_BY_LANG = {
    'zh': {
        'happy': ('哈哈', '嘿嘿', '嘻嘻', '开心', '高兴', '喜欢', '太棒', '可爱', '好耶', '真好', '好开心', '爱你'),
        'sad': ('难过', '伤心', '委屈', '想哭', '要哭', '哭了', '呜呜', '遗憾', '失落', '沮丧', '低落', '心疼', '欺负', '最怕'),
        'angry': ('气死', '生气', '烦死', '烦人', '真烦', '心烦', '恼火', '可恶', '炸毛', '火大',
                  # The `气得直跺脚` family was previously absent, so `他气得直跺脚` scored nothing on main.
                  # It previously seemed correct only because `学生气得` accidentally contains `生气` across the boundary.
                  '气炸', '气哭', '气得', '气坏', '气疯', '气愤'),
        'surprised': ('哇', '居然', '竟然', '不会吧', '啊这', '天哪', '真的假的', '怎么会'),
    },
    'zh-TW': {
        'happy': ('哈哈', '嘿嘿', '嘻嘻', '開心', '高興', '喜歡', '太棒', '可愛', '好耶', '真好', '好開心', '愛你'),
        'sad': ('難過', '傷心', '委屈', '想哭', '要哭', '哭了', '嗚嗚', '遺憾', '失落', '沮喪', '低落', '心疼', '欺負', '最怕'),
        'angry': ('氣死', '生氣', '煩死', '煩人', '真煩', '心煩', '惱火', '可惡', '炸毛', '火大',
                     '氣炸', '氣哭', '氣得', '氣壞', '氣瘋', '氣憤'),
        'surprised': ('哇', '居然', '竟然', '不會吧', '啊這', '天哪', '真的假的', '怎麼會'),
    },
    'en': {
        # English keywords use \b word boundaries in _count_keyword_hits, so bare
        # `happy/sad/surprised` do not match opposite-emotion embeddings such as `unhappy/unsurprised`.
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
        # `ого` (3 characters) falsely matches all `-ого` genitive endings (`мирового/другого/много`),
        # so remove it; remaining `ничего себе/внезапно/удив` cover genuine surprise.
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

# Strong aggressive/hostile expressions: heuristic angry score ×2 on match.
ANGRY_ATTACK_PATTERNS_BY_LANG = {
    'zh': ('气死', '真生气', '烦死了', '恼火', '可恶', '火大', '别烦我', '受不了', '闭嘴', '炸毛了', '气炸了'),
    'zh-TW': ('氣死', '真生氣', '煩死了', '惱火', '可惡', '火大', '別煩我', '受不了', '閉嘴', '炸毛了', '氣炸了'),
    'en': ('shut up', 'fuck off', 'go away', 'leave me alone', 'back off', 'knock it off'),
    'ja': ('うるさい', '黙れ', 'あっち行け', 'ふざけるな', 'ふざけんな'),
    'ko': ('닥쳐', '꺼져', '저리 가', '그만해'),
    'ru': ('заткнись', 'отвали', 'уйди', 'хватит уже'),
    'es': ('cállate', 'callate', 'vete', 'déjame en paz', 'dejame en paz', 'basta ya', 'aléjate'),
    'pt': ('cala a boca', 'vai embora', 'me deixa em paz', 'chega', 'para com isso'),
}

# Vulnerable/hurt expressions: heuristic sad score ×2 on match.
SAD_VULNERABLE_PATTERNS_BY_LANG = {
    'zh': ('委屈', '想哭', '要哭', '哭了', '呜呜', '别欺负', '不要欺负', '欺负我',
           '不要这样对我', '别这样对我', '最怕', '怕你这样说', '心里难受', '好难过', '可怜'),
    # `心裡` is not a mechanical conversion of `心里`: Taiwan uses `裡` for 'inside'; `心里` reads
    # differently. A Simplified/Traditional lookup table cannot infer such Taiwan orthography.
    'zh-TW': ('委屈', '想哭', '要哭', '哭了', '嗚嗚', '別欺負', '不要欺負', '欺負我',
              '不要這樣對我', '別這樣對我', '最怕', '怕你這樣說', '心裡難受', '好難過', '可憐'),
    'en': ('want to cry', 'feel hurt', 'feel awful', 'miss you so', 'broke my heart'),
    'ja': ('泣きたい', 'つらすぎ', '心が痛', '落ち込んで'),
    'ko': ('울고 싶', '너무 속상', '마음이 아프', '서러워'),
    'ru': ('хочется плакать', 'так больно', 'разбил сердце', 'очень обидно'),
    'es': ('quiero llorar', 'me duele', 'me siento herido', 'me siento herida', 'me rompió el corazón'),
    'pt': ('quero chorar', 'me machucou', 'me sinto magoado', 'me sinto magoada', 'partiu meu coração'),
}

# Affectionate/playful expressions: heuristic happy score +1 on match (only without sad/angry signals).
HAPPY_PLAYFUL_PATTERNS_BY_LANG = {
    'zh': ('哈哈', '嘿嘿', '嘻嘻', '贴贴', '撒娇', '可爱', '好耶'),
    'zh-TW': ('哈哈', '嘿嘿', '嘻嘻', '貼貼', '撒嬌', '可愛', '好耶'),
    'en': ('lol', 'yay', 'hehe', 'haha'),
    'ja': ('わーい', 'やったー', 'えへへ', 'うふふ'),
    'ko': ('히히', '헤헤', '꺄아', '신난다'),
    'ru': ('ура', 'хихи', 'хаха'),
    'es': ('jaja', 'jeje', 'yay', 'qué bien', 'que bien'),
    'pt': ('haha', 'hehe', 'eba', 'que bom'),
}

# Warning: this table uses **broad lookback** (within 14 characters before match), so include only words negating
# the target regardless of following text. Exclude modal compound negatives (`不会 / 不再 / 不算 / 未必`): they
# may negate **another** predicate in the clause; `我不会唱歌也很开心` would wrongly be classified emotionless.
# Those words belong to label parser's EMOTION_NEGATION_PREFIXES_BY_LANG (adjacency checks rather than broad
# lookback); the two tables intentionally use different inclusion criteria.
#
# Negation-context lookback tokens: appearances within N characters before keyword invalidate that match,
# preventing false emotion detection for '我不生气 / not angry / 화 안 나 / не злюсь'.
HEURISTIC_NEGATION_TOKENS_BY_LANG = {
    # Multi-character negations have low false positives; use broad lookback (_HEURISTIC_NEGATION_LOOKBACK characters before keyword).
    # zh includes common `不/没 + degree adverb` patterns, covering 2–3-character-separated negations missed by tight lookback
    # (such as `不是很 X / 不怎么 X / 没那么 X`).
    # Warning: `并不 / 並不` belongs to the modal table below, negating only the immediately following predicate;
    # a broad 14-character window would also erase later emotion in the same sentence
    # (observed: `我並不討厭而且覺得太棒` classified entirely emotionless).
    'zh': ('不太', '不是很', '不算很', '不那么', '不怎么',
           '没那么', '没怎么', '没什么'),
    'zh-TW': ('不太', '不是很', '不算很', '不那麼', '不怎麼',
              '沒那麼', '沒怎麼', '沒什麼'),
    # Warning: exclude `without`; like `sin / sem`, it is a **preposition** negating only its complement.
    # In `I am without doubt happy`, negation applies to `doubt`, not `happy`. Complements are open-ended,
    # so fixed-phrase exemptions cannot solve it (`without doubt` is only one example).
    'en': ('not ', ' no ', 'never ', 'neither ', 'nor ',
           "don't", "doesn't", "didn't", "won't",
           "isn't", "aren't", "wasn't", "weren't", "can't", "cannot"),
    'ja': ('ない', 'ません', 'なくて'),
    'ko': ('안 ', '안은', '안이', '못 ', '않', '없'),
    'ru': ('не ', 'нет ', 'никогда'),
    # Warning: synchronize additions with EMOTION_NEGATION_WORDS_BY_LANG: each table serves one
    # pipeline (this heuristic reads user text; label parser reads model labels). Updating only one
    # yields opposite answers for one sentence: `ni triste ni feliz` once gave label-parser
    # neutral but heuristic happy.
    # Warning: exclude `sin` / `sem` (=without); these **prepositions** negate only complements,
    # so `sin miedo y feliz` ('fearless and happy') negates `miedo`, not `feliz`.
    # Correct handling needs complement-scope analysis beyond this gate; fixed-phrase exemptions cannot cover
    # open-ended complements. Prefer missing `sin estar feliz` (current main behavior)
    # over interpreting affirmative sentences as negative.
    'es': ('no ', 'nunca ', 'jamás ', 'jamas ',
           'ni ', 'tampoco ', 'ninguno ', 'ningún ', 'ningun '),
    'pt': ('não ', 'nao ', 'nunca ', 'jamais ',
           'nem ', 'tampouco ', 'nenhum '),
}

# Modal compound negations: the whole word negates, and **only immediately before emotion words**.
# Separate from broad-lookback table, whose 14-character search
# wrongly erases unrelated emotion in `我不会唱歌也很开心` (observed and reverted).
# Strip trailing degree adverbs, then require adjacency before emotion word.
HEURISTIC_MODAL_NEGATIONS_BY_LANG = {
    'zh': ('不会', '不算', '不再', '未必', '不至于', '算不上', '谈不上', '说不上',
           '没什么好', '没什么可', '有什么好', '没什么', '有什么', '并不', '并非',
           # Bare `没有`: tight window is only 2 characters, so intervening degree adverbs (`我没有很开心`)
           # hide it. Strip adverbs before checking adjacency here to catch it.
           '没有', '并没有',
           # `难以` in `难以置信令人开心` modifies `置信`, not `开心`; like `并不`,
           # inability negation affects only the next predicate, so broad lookback would erase later emotion.
           '无法', '难以', '没办法', '没法', '并无', '毫无', '绝无',
           '不要', '不觉得', '不认为', '没觉得', '没有觉得', '没有在', '没有到'),
    'zh-TW': ('不會', '不算', '不再', '未必', '不至於', '算不上', '談不上', '說不上',
              '沒什麼好', '沒什麼可', '有什麼好', '沒什麼', '有什麼', '並不', '並非',
              '沒有', '並沒有',
              '無法', '難以', '沒辦法', '沒法', '並無', '毫無', '絕無',
              '不要', '不覺得', '不認為', '沒覺得', '沒有覺得', '沒有在', '沒有到'),
    # `nada / nenhum` as 'not at all' negate degree only for the **immediately following adjective**
    # (`nada feliz`); an intervening word changes the part of speech: in `no hay nada más feliz que...`,
    # `nada` is the noun 'nothing', making a superlative comparison expressing strong happiness.
    'es': ('nada',),
    'pt': ('nada',),
}

# Tight negation tokens count only within a few characters immediately preceding the keyword
# (_HEURISTIC_TIGHT_NEGATION_LOOKBACK). Special handling for single-character zh negations: `不/没/別/未`
# have high false positives (`不错/不思议/不具合/不愧/不仅/不可思议` are not negations), but remain
# essential as negators; accept when adjacent to emotion words (e.g. `不开心 / 不太烦`).
HEURISTIC_TIGHT_NEGATION_TOKENS_BY_LANG = {
    # Remove single-character zh `莫`: `莫名开心 / 莫名生气 / 莫名其妙` are common nonnegative expressions,
    # while actual negation (`莫怪 / 莫管`) is rare in modern Chinese; false positives exceed true positives.
    #
    # This table previously mixed `別 / 沒` into zh, the repository's sole Traditional coverage. Splitting by language
    # merely makes it symmetric with other tables; the flattened set remains identical
    # (`any(token in window)` matching is order-independent).
    'zh': ('不', '别', '没', '未', '勿'),
    'zh-TW': ('不', '別', '沒', '未', '勿'),
    # ko: colloquial Korean commonly joins negatives mid-sentence, such as `안좋아 / 안슬퍼 / 안화나 / 못좋아`.
    # Single `안/못` also appear in nonnegative words `안녕/안내/안전/안경/못이`, so use tight
    # lookback, counting negation only within a few immediately preceding keyword characters.
    'ko': ('안', '못'),
    'es': ('no',),
    'pt': ('não', 'nao'),
}

# Allowlist nonnegative fixed phrases during negation lookback: replace matching phrases in window with whitespace
# before negation-token matching, avoiding false negation from `not / 不 / не` in affirmative `not only / 不仅 / не только`
# (`not only happy` must remain happy).
HEURISTIC_NEGATION_BLOCKLIST_BY_LANG = {
    'en': ('not only', 'no doubt', 'no wonder'),
    # `别` family: `别` directly precedes emotion in `特别开心`, so tight negation would wrongly negate it,
    # giving '我今天特别开心' no score. These common words contain negator characters but are not negations.
    # `有没有` differs in direction but is similar: `有没有很开心` is rhetorical/exclamatory happiness; bare
    # `没有` is genuinely needed in the modal table (`我没有很开心`), so block false matches here.
    'zh': ('不仅', '不只', '不但', '不光', '有没有',
           '特别', '告别', '分别', '个别', '区别', '差别', '级别', '性别',
           '识别', '辨别', '送别', '离别', '道别', '差不多'),
    'zh-TW': ('不僅', '不只', '不但', '不光', '有沒有',
              '特別', '告別', '分別', '個別', '區別', '差別', '級別', '性別',
              '識別', '辨別', '送別', '離別', '道別', '差不多'),
    'ru': ('не только',),
    'es': ('no solo',),
    'pt': ('não só', 'nao so', 'não apenas', 'nao apenas'),
}

# Concessive/adversative conjunctions: when present in window, only text following them shares the keyword's clause.
# Prevent first-half negation leaking into the second half of contrasts such as 'not X but Y / 不是 X 而是 Y'.
HEURISTIC_CONTRAST_CONJUNCTIONS_BY_LANG = {
    'zh': ('但', '但是', '不过', '然而', '可是', '而是'),
    'zh-TW': ('但', '但是', '不過', '然而', '可是', '而是'),
    # Coordinating conjunctions also mark boundaries: `não cansado e feliz` negates `cansado`. Latin only:
    # Chinese `和/與` often joins objects sharing one negation (`不開心和難過`), so boundaries would misread it.
    'en': (' but ', ' however', ' yet ', ' though', ' instead', ' and '),
    'ja': ('けど', 'けれど', 'でも', 'しかし', 'だが'),
    # ko: `하지만/그러나/근데/대신` are independent conjunctions, but colloquial contrasts more often use bound endings
    # `-지만/-는데` (e.g. `슬프지 않지만 행복해`); include both.
    'ko': ('하지만', '그러나', '근데', '대신', '지만', '는데'),
    'ru': (' но ', ' однако', ' зато', ' а '),
    'es': (' pero ', ' aunque ', ' sin embargo', ' en cambio', ' y '),
    'pt': (' mas ', ' porém', ' porem', ' embora ', ' no entanto', ' em vez disso', ' e '),
}

# Model emotion-label aliases/synonyms normalize to five canonical classes.
# 'common' includes canonical English labels and common English synonyms.
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
    # This table reads **model-output labels**, not user input. Models receiving Traditional prompts answer
    # in Traditional too; without this block, `驚訝` would fall through to neutral.
    'zh-TW': {
        '開心': 'happy', '高興': 'happy', '興奮': 'happy', '快樂': 'happy',
        '難過': 'sad', '傷心': 'sad', '失落': 'sad', '委屈': 'sad',
        '生氣': 'angry', '憤怒': 'angry', '煩躁': 'angry', '惱火': 'angry',
        '驚訝': 'surprised', '震驚': 'surprised', '意外': 'surprised',
        '平靜': 'neutral', '冷靜': 'neutral', '中性': 'neutral', '普通': 'neutral',
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
    # Cross-language deduplication: retain one copy when a term appears in multiple blocks (e.g. `haha` in en/pt),
    # or heuristic per-entry hit accumulation would score the same text repeatedly.
    return {emotion: tuple(dict.fromkeys(words)) for emotion, words in merged.items()}


def _flatten_lang_tuples(by_lang: dict) -> tuple:
    # dict.fromkeys deduplicates preserving order for the same reason: cross-language duplicates must not double-count.
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


def get_heuristic_modal_negations_flat() -> tuple:
    return _flatten_lang_tuples(HEURISTIC_MODAL_NEGATIONS_BY_LANG)


def get_emotion_keyword_false_friends_flat() -> tuple:
    return _flatten_lang_tuples(EMOTION_KEYWORD_FALSE_FRIENDS_BY_LANG)


def get_heuristic_negation_blocklist_flat() -> tuple:
    return _flatten_lang_tuples(HEURISTIC_NEGATION_BLOCKLIST_BY_LANG)


def get_heuristic_contrast_conjunctions_flat() -> tuple:
    return _flatten_lang_tuples(HEURISTIC_CONTRAST_CONJUNCTIONS_BY_LANG)


# ============================================================================
# Negation tables for model-label parser.
# ============================================================================
# EMOTION_LABEL_ALIASES_BY_LANG above defines literals denoting emotions; these three tables handle
# the converse: models sometimes answer with descriptions rather than five canonical labels (`{"emotion":
# "沒有生氣"}`). Aliases match substrings, requiring detection of negation around matches first,
# or the sentence's meaning is reversed.
#
# Previously hardcoded in system_router/emotion.py, these three tables conflicted with this file's stated
# centralized language-organized keyword/alias maintenance; move them together into the same
# per-language blocks + flattening-helper shape as above.

# Prefix negation occurs **before** matches (`不开心` / `not happy` / `не злюсь`).
EMOTION_NEGATION_PREFIXES_BY_LANG = {
    'zh': ('不是', '并不', '并非', '不太', '没那么', '没有', '并没有',
           '不会', '不算', '不再', '未必', '不至于', '算不上', '谈不上', '说不上',
           '不怎么', '没怎么',
           # If intervening text is not degree adverbs (`好 / 可 / 要 / 觉得 / 在 / 到`), stripping cannot reach it,
           # so include whole words. Warning: do not add `好` as a degree adverb: stripping it in `心情不好难过`
           # exposes `心情不`, wrongly treating 'in a bad mood, sad' as negated.
           '没什么好', '没什么可', '有什么好', '不要', '不觉得', '不认为',
           '没觉得', '没有觉得', '没有在', '没有到',
           # Single `无` requires the entire remainder to be an alias, missing `法` in `无法开心`.
           '无法', '难以', '没办法', '没法',
           '不', '没', '无', '非', '别'),
    # Traditional block is necessary, not optional: with Traditional aliases,
    # `沒有生氣` matches `生氣`, but Simplified-only `没有` would not recognize negation,
    # classifying 'not angry' as angry, worse than before adding Traditional aliases.
    'zh-TW': ('不是', '並不', '並非', '不太', '沒那麼', '沒有', '並沒有',
              '不會', '不算', '不再', '未必', '不至於', '算不上', '談不上', '說不上',
              '不怎麼', '沒怎麼',
              '沒什麼好', '沒什麼可', '有什麼好', '不要', '不覺得', '不認為',
              '沒覺得', '沒有覺得', '沒有在', '沒有到',
              '無法', '難以', '沒辦法', '沒法',
              '不', '沒', '無', '非', '別'),
    # Put `nada / nenhum` in prefixes, not standalone negations: **adjacency** requires whole negator
    # as the first word immediately followed by alias, distinguishing `nada feliz` ('not happy at all') from
    # `nada más feliz que` (superlative, very happy), where `más` intervenes.
    'es': ('nada',),
    'pt': ('nada',),
    'ko': ('안', '아니', '못'),
    'ru': ('не', 'нет', 'никогда'),
}

# Keyword false friends: fixed phrases contain emotion keywords but do not express those emotions as whole words.
# Unrelated to negation: these sentences contain no negator; the keyword itself is polysemous.
#
# Traditional `生氣` means both anger and vitality. After adding it to angry keywords,
# `這幅畫充滿生氣` ('this painting is full of life') became angry. Heuristics are the sole
# emotion source without model results, so angry directly changes character reactions.
#
# Warning: deliberately **exclude** `有生氣`: Taiwanese Mandarin `有 + verb` marks perfective (`他有生氣` = he got angry),
# and including it would erase genuine anger. Written `活潑有生氣` still misclassifies, but this direction
# is less harmful, and Traditional users are this PR's audience.
EMOTION_KEYWORD_FALSE_FRIENDS_BY_LANG = {
    # Second false-friend class: a word itself is not emotional, but joining it to the **next word** creates
    # a cross-boundary keyword: `困難` + `過程` contains `難過`. Substring matching has no word boundaries,
    # so remove the complete left word.
    # Warning: **reverse risk** of left-word removal: if that word can precede a **real** keyword, stripping also eats
    # its beginning. Removed `為難`: it also spans `因為` + `難過`,
    # so stripping erases `我因為難過不想說話`; likewise `苦難` (`痛苦難過`).
    # The remaining four have no common words ending in `困/艱/災/劫` before `難過`.
    'zh': ('充满生气', '生气勃勃', '生气蓬勃', '毫无生气', '了无生气', '死气沉沉',
           '困难', '艰难', '灾难', '劫难',
           # `X生` + `气Y`: 学生+气质 / 陌生+气息 / 发生+气爆 / 产生+气泡.
           # `生` and `气` are frequent characters, creating an open cross-product; collect left words only.
           # Warning: reverse-collision checked individually; remove `一生/先生/人生/医生`: in `他一生气就摔东西`,
           # `他先生气再说话`, `那人生气了`, `那医生气坏了`, the left character is independent,
           # so stripping the whole word erases real anger. Remaining entries have no common single-character + 生气 reading.
           '学生', '陌生', '发生', '产生', '天生', '卫生',
           '花生', '养生', '野生', '出生', '终生', '毕生',
           # Newly added `气得/气坏` shifts collisions to the other side: `语气`+`得体`, `口气`+`得罪`,
           # `天气`+`得看`, `空气`+`坏了`. All four passed reverse checks (`语/口/天/空`
           # cannot independently precede `气得`); deliberately exclude `脾气`, since `他脾气坏` really expresses anger.
           '语气', '口气', '天气', '空气'),
    'zh-TW': ('充滿生氣', '生氣勃勃', '生氣蓬勃', '毫無生氣', '了無生氣', '死氣沉沉',
              '困難', '艱難', '災難', '劫難',
              '學生', '陌生', '發生', '產生', '天生', '衛生',
              '花生', '養生', '野生', '出生', '終生', '畢生',
              '語氣', '口氣', '天氣', '空氣'),
}


# Standalone negations: match tokens, not substrings.
EMOTION_NEGATION_WORDS_BY_LANG = {
    # List contractions separately: tokenizer now retains apostrophes, but include apostrophe-free forms such as `isnt` too.
    # `without` is like `sin / sem`; see broad-lookback explanation above.
    'en': ('not', 'no', 'never', 'neither', 'nor',
           "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't", "didn't",
           "won't", "can't", 'cannot', 'isnt', 'arent', 'wasnt', 'werent', 'dont', 'doesnt', 'didnt',
           'wont', 'cant'),
    # Spanish previously worked accidentally via English `no`; Portuguese had none.
    # Warning: this table serves label parser only, while HEURISTIC_NEGATION_TOKENS_BY_LANG serves heuristics only.
    # Keep inclusion criteria synchronized, or the same sentence yields opposite answers across pipelines.
    # As above, exclude `sin` / `sem`: prepositions negate only complements.
    'es': ('no', 'nunca', 'jamás', 'jamas', 'ni', 'tampoco',
           'ninguno', 'ningún', 'ningun'),
    'pt': ('não', 'nao', 'nunca', 'jamais', 'nem',
           'tampouco', 'nenhum'),
    'ko': ('안', '아니', '못', '않', '아니다', '아닌', '아님'),
    'ru': ('не', 'нет', 'никогда'),
}

# Degree adverbs between negation and emotion (e.g. `怎麼` in `不怎麼開心`).
#
# English token-based lookback spans three words, naturally recognizing `not very happy`; Chinese without punctuation
# is one string without word boundaries. A window sees strings such as `不怎麼`, so `endswith('不')`
# is false, incorrectly labeling `不怎麼開心` happy, reversing meaning. Stripping trailing degree adverbs before
# negation checks is the Chinese counterpart.
#
# Desirable side effect: `特別` is itself a degree adverb; adding it strips `特別開心`'s window empty,
# avoiding false negation from single-character `別`.
EMOTION_NEGATION_DEGREE_ADVERBS_BY_LANG = {
    'zh': ('怎么', '那么', '什么', '这么', '那样', '非常', '特别', '十分', '相当',
           '比较', '有点', '有些', '真的', '真正', '超级', '一直', '很', '太', '超',
           '挺', '蛮', '多',
           '更', '最', '真'),
    'zh-TW': ('怎麼', '那麼', '什麼', '這麼', '那樣', '非常', '特別', '十分', '相當',
              '比較', '有點', '有些', '真的', '真正', '超級', '一直', '很', '太', '超',
              '挺', '蠻', '多',
              '更', '最', '真'),
}

# Suffix negation: Korean-specific bound endings (`슬프지 않아`); other languages lack corresponding forms.
# Suffix exceptions: these strings **start** with suffix entries but are not negation overall.
# `ないし` means 'or'; `悲しいないし平穏` coordinates, not negates. Substring matching cannot distinguish,
# so inspect immediately following characters after a match.
EMOTION_NEGATION_SUFFIX_EXCEPTIONS_BY_LANG = {
    # `不了` in `開心不了解你為什麼生氣` belongs to `不了解`, not 'unable to be happy'.
    # Especially important: negating `開心` leaves `生氣` winning in the same sentence, producing the opposite emotion.
    'zh': ('不了解', '不了了之'),
    'zh-TW': ('不了解', '不了了之'),
    'ja': ('ないし', 'ないしは'),
}


EMOTION_NEGATION_SUFFIXES_BY_LANG = {
    # Chinese postposed negation follows **after** emotion (`開心不起來`). Require the entire preceding
    # span to look like an alias, avoiding effects on other components in the clause.
    # `不了` is a verb-complement construction (`开心不了` = unable to feel happy); `才怪 / 个屁 / 个头` are colloquial trailing
    # rebuttals. All require adjacency to emotion, leaving other clause components unaffected.
    'zh': ('不起来', '不下去', '不出来', '不了', '才怪', '个屁', '个头'),
    'zh-TW': ('不起來', '不下去', '不出來', '不了', '才怪', '個屁', '個頭'),
    # Japanese negation is trailing kana. Adding **identical Chinese/Japanese kanji** `興奮 / 憤怒 / 傷心 / 可愛`
    # to Traditional aliases allowed Japanese sentences to match for the first time, exposing this gap.
    # Warning: `ない` is broad; Japanese has nonnegative `切ない / 情けない / もったいない`. Currently
    # aliases omit them, so no conflict, but future inclusion would negate them with their own `ない`.
    # Also `_has_heuristic_negation_after` anchors immediately at keyword end, so
    # `可愛` is followed by `くない`, not `ない`; include both.
    # Warning: per-match suffix must be **adjacent** to alias (startswith from alias end), so polite forms
    # cannot include only final `ません`: after the alias in `興奮していません`, the whole suffix is
    # `していません`; without intervening `してい` in table nothing matches. Include full composite forms.
    # Conversely include only forms that **can follow aliases directly**: no verb te-forms (`ていません`, etc.)
    # appear here because no aliases accept them. Deletion mutations verified each entry is active.
    'ja': ('くない', 'くなかった', 'じゃない', 'ではない', 'じゃなかった',
           'ではありません', 'じゃありません',
           'くありません', 'くありませんでした',
           'ではありませんでした', 'じゃありませんでした', 'ではなかった',
           'しない', 'していない', 'してない',
           'しなかった', 'していなかった', 'してなかった',
           'していません', 'してません', 'しておりません', 'しません',
           'しませんでした', 'していませんでした', 'せず',
           # Te-form continuation and inserted particles: `幸せじゃなくて` / `意外でもない` remain
           # adjacent to aliases, so table additions cover them.
           'じゃなくて', 'くなくて', 'ではなく', 'しなくて',
           'でもない', 'くもない', 'くはない',
           # Formal-noun negation: `嬉しいわけじゃない` / `腹が立つことはない`.
           'わけじゃない', 'わけではない', 'ことはない', 'はずがない',
           'ません', 'ない'),
    'ko': ('지 않', '지않', '지 않아', '지않아', '지 않다', '지않다', '지 않음', '지않음',
           '지 못', '지못', '지 못해', '지못해', '지 못하다', '지못하다',
           '않', '않아', '않다', '않음', '아냐', '아니야', '아니다', '아닌', '아님'),
}


def get_emotion_negation_prefixes_flat() -> tuple:
    return _flatten_lang_tuples(EMOTION_NEGATION_PREFIXES_BY_LANG)


def get_emotion_negation_words_flat() -> tuple:
    return _flatten_lang_tuples(EMOTION_NEGATION_WORDS_BY_LANG)


def get_emotion_negation_suffix_exceptions_flat() -> tuple:
    return _flatten_lang_tuples(EMOTION_NEGATION_SUFFIX_EXCEPTIONS_BY_LANG)


def get_emotion_negation_suffixes_flat() -> tuple:
    return _flatten_lang_tuples(EMOTION_NEGATION_SUFFIXES_BY_LANG)


def get_emotion_negation_degree_adverbs_flat() -> tuple:
    return _flatten_lang_tuples(EMOTION_NEGATION_DEGREE_ADVERBS_BY_LANG)



def get_emotion_label_aliases_flat() -> dict:
    """Merge per-language aliases into dict[alias → canonical], used by _normalize_emotion_label."""
    merged: dict = {}
    for lang_map in EMOTION_LABEL_ALIASES_BY_LANG.values():
        merged.update(lang_map)
    return merged
