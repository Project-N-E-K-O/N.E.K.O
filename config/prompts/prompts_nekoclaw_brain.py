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

"""Texts of the NekoClaw task brain (``nekoclaw/host/``).

* ``TASK_BRAIN_SELECT_PROMPT``: the fine selection of the decision model. It
  takes over the plugin rules of ``USER_PLUGIN_SYSTEM_PROMPT``
  (``prompts_agent.py``) and adds the hand-over to a familiar, which the same
  request decides. The coarse screen keeps using
  ``USER_PLUGIN_COARSE_SCREEN_PROMPT`` unchanged.
* Receipts and query answers: short lines returned to the main dialogue model
  as tool results.
* Cue patterns: how a dispatched sentence is recognised, without a model, as
  a stop request, an answer or an addition to something already under way.
* Preference kinds: which kind a stated task preference is, without a model.
"""

# =====================================================================
# ======= Fine selection =======
# =====================================================================
# The system text; ``{plugins_desc}`` is the only placeholder. The user
# message is built by ``build_selection_request`` below.

TASK_BRAIN_SELECT_PROMPT = {
    'zh': """你是任务路由器。可用插件列表：
{plugins_desc}

用户消息里有：TASK（要办的事）、USER_SAID（用户原话），可能还有 ACTIVE_TASKS 和 PREFERENCES。

指令：
1. 判断上面某个插件入口调用一次能否办完 TASK。插件优先。
2. 能：返回 route="plugin"，以及 plugin_id、entry_id 和匹配入口 schema 的 plugin_args。
3. 没有合适的入口，或这件事要多步、要多个工具：返回 route="familiar"。不要勉强选一个只对得上一半的插件。
4. TASK 和 ACTIVE_TASKS 里某一行是同一件事：返回 route="duplicate"，duplicate_of 填那一行的序号。
5. 输出必须只是一个 JSON 对象，不含解释性文字、markdown 或代码块。

输出格式（严格 JSON）：
{{
    "route": "plugin" | "familiar" | "duplicate",
    "task_description": "简要描述",
    "plugin_id": "插件 id 或 null",
    "entry_id": "插件内的入口 id 或 null",
    "plugin_args": {{...}} 或 null,
    "duplicate_of": 序号或 null,
    "preference": "TASK 里交代的、以后同类事都适用的做法；没有就填 null",
    "reason": "原因"
}}

非常重要：
- 严格匹配：plugin_id 和 entry_id 是代码标识符，必须从上面的列表中原样复制（区分大小写、逐字符匹配），不要发明、缩写或改写。找不到完全匹配就用 route="familiar"。
- route="plugin" 时 entry_id 必填。
- 入口有 args(...) 信息时，plugin_args 用那些字段名，只包含 schema 中列出的字段。
- 入口 schema 需要用户文本字段（例如 command/message/query/objective）时，必须复制 USER_SAID 原文；不要翻译、摘要、改写或补全。
- PREFERENCES 适用时照办。
- 标注了 [KEYWORD MATCH] 的插件已通过关键词预筛，优先考虑。
只返回 JSON 对象，不含其他内容。""",

    'zh-TW': """你是任務路由器。可用外掛清單：
{plugins_desc}

使用者訊息裡有：TASK（要辦的事）、USER_SAID（使用者原話），可能還有 ACTIVE_TASKS 和 PREFERENCES。

指令：
1. 判斷上面某個外掛入口呼叫一次能不能辦完 TASK。外掛優先。
2. 能：回傳 route="plugin"，以及 plugin_id、entry_id 和符合入口 schema 的 plugin_args。
3. 沒有合適的入口，或這件事要多步、要多個工具：回傳 route="familiar"。不要勉強選一個只對得上一半的外掛。
4. TASK 和 ACTIVE_TASKS 裡某一行是同一件事：回傳 route="duplicate"，duplicate_of 填那一行的序號。
5. 輸出必須只有一個 JSON 物件，不含解釋性文字、markdown 或程式碼區塊。

輸出格式（嚴格 JSON）：
{{
    "route": "plugin" | "familiar" | "duplicate",
    "task_description": "簡要描述",
    "plugin_id": "外掛 id 或 null",
    "entry_id": "外掛裡的入口 id 或 null",
    "plugin_args": {{...}} 或 null,
    "duplicate_of": 序號或 null,
    "preference": "TASK 裡交代的、以後同類事都適用的做法；沒有就填 null",
    "reason": "原因"
}}

非常重要：
- 嚴格比對：plugin_id 和 entry_id 是程式識別碼，必須從上面的清單原樣複製（區分大小寫、逐字元比對），不要發明、縮寫或改寫。找不到完全相符的就用 route="familiar"。
- route="plugin" 時 entry_id 必填。
- 入口有 args(...) 資訊時，plugin_args 用那些欄位名，只包含 schema 裡列出的欄位。
- 入口 schema 需要使用者文字欄位（例如 command/message/query/objective）時，必須照抄 USER_SAID 原文；不要翻譯、摘要、改寫或補齊。
- PREFERENCES 適用時照辦。
- 標了 [KEYWORD MATCH] 的外掛已經通過關鍵字預篩，優先考慮。
只回傳 JSON 物件，不含其他內容。""",

    'en': """You are a task router. AVAILABLE PLUGINS:
{plugins_desc}

The user message gives TASK (what has to get done) and USER_SAID (the user's own words), and may give ACTIVE_TASKS and PREFERENCES.

INSTRUCTIONS:
1. Decide whether ONE call to ONE plugin entry above gets TASK done. Plugins come first.
2. If yes: return route="plugin" with plugin_id, entry_id and plugin_args matching the entry's schema.
3. If no entry fits, or the task needs several steps or several tools: return route="familiar". Do NOT force a plugin that only half fits.
4. If TASK is the same task as a line of ACTIVE_TASKS: return route="duplicate" and that line's number in duplicate_of.
5. OUTPUT MUST BE ONLY a single JSON object. No explanatory text, markdown or code fences.

OUTPUT FORMAT (strict JSON):
{{
    "route": "plugin" | "familiar" | "duplicate",
    "task_description": "brief description",
    "plugin_id": "plugin id or null",
    "entry_id": "entry id inside that plugin or null",
    "plugin_args": {{...}} or null,
    "duplicate_of": number or null,
    "preference": "a lasting instruction TASK gives for tasks of this kind, else null",
    "reason": "why"
}}

VERY IMPORTANT:
- STRICT MATCHING: plugin_id and entry_id are code identifiers. Copy them EXACTLY (case-sensitive, character-for-character) from the list above. Do NOT invent, abbreviate or rephrase them. If no exact match exists, use route="familiar".
- route="plugin" requires entry_id.
- If the entry has args(...) info, use those field names in plugin_args. Only include fields listed in the schema.
- When the entry schema needs a user-text field (e.g. command/message/query/objective), copy USER_SAID verbatim; do not translate, summarize, rewrite or complete it.
- Follow PREFERENCES where they apply.
- Plugins marked [KEYWORD MATCH] passed a keyword pre-filter; consider them first.
Return only the JSON object, nothing else.""",

    'ja': """あなたはタスクルーターです。利用可能なプラグイン一覧：
{plugins_desc}

ユーザーメッセージには TASK（片付ける用件）と USER_SAID（ユーザー本人の言葉）があり、ACTIVE_TASKS と PREFERENCES が付くこともあります。

指示：
1. 上のプラグインエントリのどれか 1 つを 1 回呼ぶだけで TASK が片付くか判断します。プラグイン優先です。
2. 片付く場合：route="plugin" と、plugin_id、entry_id、エントリの schema に合う plugin_args を返します。
3. 合うエントリがない、または複数の手順や複数のツールが必要な場合：route="familiar" を返します。半分しか合わないプラグインを無理に選ばないでください。
4. TASK が ACTIVE_TASKS のある行と同じ用件の場合：route="duplicate" を返し、duplicate_of にその行の番号を入れます。
5. 出力は JSON オブジェクト 1 つだけにします。説明文、markdown、コードブロックは付けません。

出力形式（厳密な JSON）：
{{
    "route": "plugin" | "familiar" | "duplicate",
    "task_description": "簡潔な説明",
    "plugin_id": "プラグイン id または null",
    "entry_id": "プラグイン内のエントリ id または null",
    "plugin_args": {{...}} または null,
    "duplicate_of": 番号または null,
    "preference": "TASK が示した、今後同じ種類の用件に当てはまるやり方。なければ null",
    "reason": "理由"
}}

非常に重要：
- 厳密一致：plugin_id と entry_id はコード識別子です。上の一覧からそのまま写してください（大文字小文字を区別、1 文字ずつ一致）。作り出したり、省略したり、言い換えたりしないでください。完全一致がなければ route="familiar" にします。
- route="plugin" のとき entry_id は必須です。
- エントリに args(...) があれば、plugin_args にはそのフィールド名を使い、schema にあるフィールドだけを入れます。
- エントリの schema がユーザーのテキスト（command/message/query/objective など）を求めるときは、USER_SAID を原文のまま写します。翻訳、要約、書き換え、補完はしません。
- PREFERENCES が当てはまるときは従います。
- [KEYWORD MATCH] の付いたプラグインはキーワードの事前選別を通っています。優先して検討してください。
JSON オブジェクトだけを返してください。""",

    'ko': """당신은 작업 라우터입니다. 사용 가능한 플러그인 목록:
{plugins_desc}

사용자 메시지에는 TASK(처리할 일)와 USER_SAID(사용자가 한 말 그대로)가 있고, ACTIVE_TASKS와 PREFERENCES가 붙을 수 있습니다.

지침:
1. 위 플러그인 엔트리 하나를 한 번 호출해서 TASK를 끝낼 수 있는지 판단합니다. 플러그인이 우선입니다.
2. 끝낼 수 있으면: route="plugin"과 plugin_id, entry_id, 엔트리 schema에 맞는 plugin_args를 반환합니다.
3. 맞는 엔트리가 없거나 여러 단계 또는 여러 도구가 필요하면: route="familiar"를 반환합니다. 절반만 맞는 플러그인을 억지로 고르지 마세요.
4. TASK가 ACTIVE_TASKS의 어느 줄과 같은 일이면: route="duplicate"를 반환하고 duplicate_of에 그 줄의 번호를 넣습니다.
5. 출력은 JSON 객체 하나뿐이어야 합니다. 설명 문장, markdown, 코드 블록을 넣지 마세요.

출력 형식(엄격한 JSON):
{{
    "route": "plugin" | "familiar" | "duplicate",
    "task_description": "간단한 설명",
    "plugin_id": "플러그인 id 또는 null",
    "entry_id": "플러그인 안의 엔트리 id 또는 null",
    "plugin_args": {{...}} 또는 null,
    "duplicate_of": 번호 또는 null,
    "preference": "TASK가 알려 준, 앞으로 같은 종류의 일에 적용할 방식. 없으면 null",
    "reason": "이유"
}}

매우 중요:
- 엄격한 일치: plugin_id와 entry_id는 코드 식별자입니다. 위 목록에서 그대로 복사하세요(대소문자 구분, 글자 단위 일치). 지어내거나 줄이거나 바꿔 쓰지 마세요. 정확히 일치하는 것이 없으면 route="familiar"로 합니다.
- route="plugin"이면 entry_id가 필수입니다.
- 엔트리에 args(...) 정보가 있으면 plugin_args에 그 필드 이름을 쓰고, schema에 있는 필드만 넣습니다.
- 엔트리 schema가 사용자 텍스트 필드(command/message/query/objective 등)를 요구하면 USER_SAID를 원문 그대로 복사합니다. 번역, 요약, 바꿔 쓰기, 보완을 하지 마세요.
- PREFERENCES가 해당되면 따릅니다.
- [KEYWORD MATCH]가 붙은 플러그인은 키워드 사전 선별을 통과했습니다. 먼저 검토하세요.
JSON 객체만 반환하세요.""",

    'ru': """Вы маршрутизатор задач. ДОСТУПНЫЕ ПЛАГИНЫ:
{plugins_desc}

В сообщении пользователя есть TASK (что нужно сделать) и USER_SAID (слова самого пользователя), могут быть ACTIVE_TASKS и PREFERENCES.

ИНСТРУКЦИИ:
1. Определите, выполнит ли TASK ОДИН вызов ОДНОЙ точки входа плагина из списка. Плагины в приоритете.
2. Если да: верните route="plugin", а также plugin_id, entry_id и plugin_args по схеме точки входа.
3. Если ни одна точка входа не подходит или задаче нужно несколько шагов либо несколько инструментов: верните route="familiar". НЕ подбирайте плагин, который подходит лишь наполовину.
4. Если TASK совпадает с одной из строк ACTIVE_TASKS: верните route="duplicate" и номер этой строки в duplicate_of.
5. ВЫВОД ДОЛЖЕН БЫТЬ ТОЛЬКО одним объектом JSON. Без пояснений, markdown и блоков кода.

ФОРМАТ ВЫВОДА (строгий JSON):
{{
    "route": "plugin" | "familiar" | "duplicate",
    "task_description": "краткое описание",
    "plugin_id": "id плагина или null",
    "entry_id": "id точки входа в этом плагине или null",
    "plugin_args": {{...}} или null,
    "duplicate_of": номер или null,
    "preference": "постоянное указание из TASK для задач такого рода, иначе null",
    "reason": "почему"
}}

ОЧЕНЬ ВАЖНО:
- СТРОГОЕ СОВПАДЕНИЕ: plugin_id и entry_id — идентификаторы в коде. Копируйте их ТОЧНО (с учётом регистра, символ в символ) из списка выше. НЕ выдумывайте, не сокращайте и не перефразируйте. Если точного совпадения нет, используйте route="familiar".
- Для route="plugin" обязателен entry_id.
- Если у точки входа есть args(...), используйте эти имена полей в plugin_args и только поля из схемы.
- Если схеме нужно текстовое поле пользователя (например command/message/query/objective), копируйте USER_SAID дословно; не переводите, не сокращайте, не переписывайте и не дополняйте.
- Следуйте PREFERENCES там, где они применимы.
- Плагины с пометкой [KEYWORD MATCH] прошли отбор по ключевым словам; рассмотрите их первыми.
Верните только объект JSON, больше ничего.""",

    'es': """Eres un enrutador de tareas. PLUGINS DISPONIBLES:
{plugins_desc}

El mensaje del usuario trae TASK (lo que hay que hacer) y USER_SAID (las palabras del propio usuario), y puede traer ACTIVE_TASKS y PREFERENCES.

INSTRUCCIONES:
1. Decide si UNA llamada a UNA entrada de plugin de la lista resuelve TASK. Los plugins van primero.
2. Si es así: devuelve route="plugin" con plugin_id, entry_id y plugin_args según el schema de la entrada.
3. Si ninguna entrada encaja, o la tarea necesita varios pasos o varias herramientas: devuelve route="familiar". NO fuerces un plugin que solo encaja a medias.
4. Si TASK es la misma tarea que una línea de ACTIVE_TASKS: devuelve route="duplicate" y el número de esa línea en duplicate_of.
5. LA SALIDA DEBE SER SOLO un objeto JSON. Sin texto explicativo, markdown ni bloques de código.

FORMATO DE SALIDA (JSON estricto):
{{
    "route": "plugin" | "familiar" | "duplicate",
    "task_description": "descripción breve",
    "plugin_id": "id del plugin o null",
    "entry_id": "id de la entrada dentro del plugin o null",
    "plugin_args": {{...}} o null,
    "duplicate_of": número o null,
    "preference": "una instrucción duradera que TASK da para tareas de este tipo; si no, null",
    "reason": "por qué"
}}

MUY IMPORTANTE:
- COINCIDENCIA ESTRICTA: plugin_id y entry_id son identificadores de código. Cópialos EXACTAMENTE (distinguiendo mayúsculas, carácter por carácter) de la lista. NO los inventes, abrevies ni reformules. Si no hay coincidencia exacta, usa route="familiar".
- route="plugin" exige entry_id.
- Si la entrada tiene args(...), usa esos nombres de campo en plugin_args y solo los campos del schema.
- Cuando el schema pide un campo de texto del usuario (p. ej. command/message/query/objective), copia USER_SAID tal cual; no lo traduzcas, resumas, reescribas ni completes.
- Sigue PREFERENCES cuando correspondan.
- Los plugins marcados con [KEYWORD MATCH] pasaron un prefiltro de palabras clave; considéralos primero.
Devuelve solo el objeto JSON, nada más.""",

    'pt': """Você é um roteador de tarefas. PLUGINS DISPONÍVEIS:
{plugins_desc}

A mensagem do usuário traz TASK (o que precisa ser feito) e USER_SAID (as palavras do próprio usuário), e pode trazer ACTIVE_TASKS e PREFERENCES.

INSTRUÇÕES:
1. Decida se UMA chamada a UMA entrada de plugin da lista resolve TASK. Plugins vêm primeiro.
2. Se sim: retorne route="plugin" com plugin_id, entry_id e plugin_args conforme o schema da entrada.
3. Se nenhuma entrada servir, ou a tarefa precisar de vários passos ou várias ferramentas: retorne route="familiar". NÃO force um plugin que só serve pela metade.
4. Se TASK for a mesma tarefa de uma linha de ACTIVE_TASKS: retorne route="duplicate" e o número dessa linha em duplicate_of.
5. A SAÍDA DEVE SER APENAS um objeto JSON. Sem texto explicativo, markdown ou blocos de código.

FORMATO DE SAÍDA (JSON estrito):
{{
    "route": "plugin" | "familiar" | "duplicate",
    "task_description": "descrição breve",
    "plugin_id": "id do plugin ou null",
    "entry_id": "id da entrada dentro do plugin ou null",
    "plugin_args": {{...}} ou null,
    "duplicate_of": número ou null,
    "preference": "uma instrução duradoura que TASK dá para tarefas deste tipo; senão, null",
    "reason": "por quê"
}}

MUITO IMPORTANTE:
- CORRESPONDÊNCIA ESTRITA: plugin_id e entry_id são identificadores de código. Copie-os EXATAMENTE (diferenciando maiúsculas, caractere por caractere) da lista. NÃO invente, abrevie nem reformule. Se não houver correspondência exata, use route="familiar".
- route="plugin" exige entry_id.
- Se a entrada tiver args(...), use esses nomes de campo em plugin_args e apenas os campos do schema.
- Quando o schema pedir um campo de texto do usuário (ex.: command/message/query/objective), copie USER_SAID literalmente; não traduza, resuma, reescreva nem complete.
- Siga PREFERENCES quando se aplicarem.
- Plugins marcados com [KEYWORD MATCH] passaram por um pré-filtro de palavras-chave; considere-os primeiro.
Retorne apenas o objeto JSON, nada mais.""",
}

# Sent when the fine selection named a plugin or entry that does not exist;
# same wording as the analyzer's retry.
SELECT_CORRECTION_TEMPLATE = "CORRECTION: {hint}. Please fix your response and return a valid JSON."

NO_PLUGINS_DESC = "No plugins available."


def build_selection_request(
    *,
    task: str,
    user_said: str,
    active_tasks: list[str],
    preferences: str,
) -> str:
    """Build the user message of the fine selection.

    The labels are fixed English words the system text refers to. Parts with
    nothing to say are left out.
    """
    lines = [f"TASK: {task}", f"USER_SAID: {user_said or task}"]
    if active_tasks:
        lines.append("ACTIVE_TASKS:")
        lines.extend(f"{number}. {row}" for number, row in enumerate(active_tasks, 1))
    if preferences:
        lines.append("PREFERENCES:")
        lines.append(preferences)
    return "\n".join(lines)


# =====================================================================
# ======= Receipts (tool results for the main dialogue model) =======
# =====================================================================

COMMISSION_STATUS_WORDS = {
    'zh': {"queued": "排队中", "working": "进行中", "waiting_user": "等用户回话", "done": "已完成",
           "blocked": "卡住了", "failed": "没办成", "cancelled": "已叫停"},
    'zh-TW': {"queued": "排隊中", "working": "進行中", "waiting_user": "等使用者回話", "done": "已完成",
              "blocked": "卡住了", "failed": "沒辦成", "cancelled": "已叫停"},
    'en': {"queued": "queued", "working": "in progress", "waiting_user": "waiting for the user", "done": "done",
           "blocked": "blocked", "failed": "failed", "cancelled": "stopped"},
    'ja': {"queued": "順番待ち", "working": "進行中", "waiting_user": "ユーザーの返事待ち", "done": "完了",
           "blocked": "行き詰まり", "failed": "失敗", "cancelled": "中止済み"},
    'ko': {"queued": "대기 중", "working": "진행 중", "waiting_user": "사용자 답변 대기", "done": "완료",
           "blocked": "막힘", "failed": "실패", "cancelled": "중지됨"},
    'ru': {"queued": "в очереди", "working": "выполняется", "waiting_user": "ждёт ответа пользователя", "done": "готово",
           "blocked": "застряло", "failed": "не удалось", "cancelled": "остановлено"},
    'es': {"queued": "en cola", "working": "en curso", "waiting_user": "esperando al usuario", "done": "hecho",
           "blocked": "bloqueado", "failed": "fallido", "cancelled": "detenido"},
    'pt': {"queued": "na fila", "working": "em andamento", "waiting_user": "aguardando o usuário", "done": "concluído",
           "blocked": "travado", "failed": "falhou", "cancelled": "interrompido"},
}

RECEIPT_TEXTS = {
    'zh': {
        "accepted": "已受理：{title}",
        "received": "收下了，正在判断怎么办。",
        "duplicate": "这件事已经在办了：{title}（{status}）",
        "no_capability": "办不了：{reason}",
        "stopped": "已叫停：{title}",
        "told": "已转告：{title}",
        "answered": "已把回答交给：{title}",
        "ambiguous": "对不上是哪一件，先问清楚再说。候选：\n{candidates}",
        "nothing_active": "现在没有在办的事。",
        "unclear_answer": "{title} 在等回话，但这句话看不出是同意还是拒绝。可以回答：就这一次、以后都行、不行。",
    },
    'zh-TW': {
        "accepted": "已受理：{title}",
        "received": "收下了，正在判斷怎麼辦。",
        "duplicate": "這件事已經在辦了：{title}（{status}）",
        "no_capability": "辦不了：{reason}",
        "stopped": "已叫停：{title}",
        "told": "已轉告：{title}",
        "answered": "已把回答交給：{title}",
        "ambiguous": "對不上是哪一件，先問清楚再說。候選：\n{candidates}",
        "nothing_active": "現在沒有在辦的事。",
        "unclear_answer": "{title} 在等回話，但這句話看不出是同意還是拒絕。可以回答：就這一次、以後都行、不行。",
    },
    'en': {
        "accepted": "Accepted: {title}",
        "received": "Received; working out how to handle it.",
        "duplicate": "Already under way: {title} ({status})",
        "no_capability": "Cannot be done: {reason}",
        "stopped": "Stopped: {title}",
        "told": "Passed on to: {title}",
        "answered": "Answer handed to: {title}",
        "ambiguous": "It is unclear which task is meant; ask before going on. Candidates:\n{candidates}",
        "nothing_active": "Nothing is under way right now.",
        "unclear_answer": "{title} is waiting for an answer, but this does not say yes or no. Possible answers: just this once, always, no.",
    },
    'ja': {
        "accepted": "受け付けました：{title}",
        "received": "受け取りました。どう進めるか判断しています。",
        "duplicate": "この用件はすでに進めています：{title}（{status}）",
        "no_capability": "対応できません：{reason}",
        "stopped": "中止しました：{title}",
        "told": "伝えました：{title}",
        "answered": "回答を渡しました：{title}",
        "ambiguous": "どの用件のことか分かりません。先に確認してください。候補：\n{candidates}",
        "nothing_active": "いま進めている用件はありません。",
        "unclear_answer": "{title} は返事を待っていますが、この言葉からは許可か拒否か分かりません。答え方：今回だけ、今後ずっと、だめ。",
    },
    'ko': {
        "accepted": "접수했습니다: {title}",
        "received": "받았습니다. 어떻게 처리할지 판단하는 중입니다.",
        "duplicate": "이 일은 이미 진행 중입니다: {title} ({status})",
        "no_capability": "처리할 수 없습니다: {reason}",
        "stopped": "중지했습니다: {title}",
        "told": "전달했습니다: {title}",
        "answered": "답변을 넘겼습니다: {title}",
        "ambiguous": "어느 일을 말하는지 알 수 없습니다. 먼저 확인하세요. 후보:\n{candidates}",
        "nothing_active": "지금 진행 중인 일이 없습니다.",
        "unclear_answer": "{title}이(가) 답을 기다리고 있지만, 이 말로는 허락인지 거절인지 알 수 없습니다. 가능한 답: 이번만, 앞으로 계속, 안 됨.",
    },
    'ru': {
        "accepted": "Принято: {title}",
        "received": "Получено; решаю, как это сделать.",
        "duplicate": "Это уже выполняется: {title} ({status})",
        "no_capability": "Сделать нельзя: {reason}",
        "stopped": "Остановлено: {title}",
        "told": "Передано: {title}",
        "answered": "Ответ передан: {title}",
        "ambiguous": "Непонятно, о какой задаче речь; сначала уточните. Варианты:\n{candidates}",
        "nothing_active": "Сейчас ничего не выполняется.",
        "unclear_answer": "{title} ждёт ответа, но из этих слов неясно, да или нет. Возможные ответы: только в этот раз, всегда, нет.",
    },
    'es': {
        "accepted": "Aceptado: {title}",
        "received": "Recibido; viendo cómo resolverlo.",
        "duplicate": "Esto ya está en marcha: {title} ({status})",
        "no_capability": "No se puede hacer: {reason}",
        "stopped": "Detenido: {title}",
        "told": "Transmitido a: {title}",
        "answered": "Respuesta entregada a: {title}",
        "ambiguous": "No está claro a qué tarea se refiere; pregunta antes de seguir. Candidatas:\n{candidates}",
        "nothing_active": "Ahora mismo no hay nada en marcha.",
        "unclear_answer": "{title} espera una respuesta, pero esto no dice ni sí ni no. Respuestas posibles: solo esta vez, siempre, no.",
    },
    'pt': {
        "accepted": "Aceito: {title}",
        "received": "Recebido; vendo como resolver.",
        "duplicate": "Isso já está em andamento: {title} ({status})",
        "no_capability": "Não dá para fazer: {reason}",
        "stopped": "Interrompido: {title}",
        "told": "Repassado para: {title}",
        "answered": "Resposta entregue a: {title}",
        "ambiguous": "Não está claro de qual tarefa se trata; pergunte antes de continuar. Candidatas:\n{candidates}",
        "nothing_active": "Não há nada em andamento agora.",
        "unclear_answer": "{title} aguarda uma resposta, mas isto não diz sim nem não. Respostas possíveis: só desta vez, sempre, não.",
    },
}

NO_CAPABILITY_REASONS = {
    'zh': {"disabled": "猫爪没有开", "not_installed": "没有装能办这件事的能力", "not_enabled": "相关的能力装了但没有开",
           "denied": "权限设置里禁止了", "no_match": "没有找到能办这件事的能力"},
    'zh-TW': {"disabled": "貓爪沒有開", "not_installed": "沒有裝能辦這件事的能力", "not_enabled": "相關的能力裝了但沒有開",
              "denied": "權限設定裡禁止了", "no_match": "沒有找到能辦這件事的能力"},
    'en': {"disabled": "NekoClaw is switched off", "not_installed": "nothing that can do this is installed",
           "not_enabled": "the capability is installed but switched off", "denied": "the permission settings forbid it",
           "no_match": "no capability that can do this was found"},
    'ja': {"disabled": "猫爪がオフになっています", "not_installed": "これができる能力が入っていません",
           "not_enabled": "能力は入っていますがオフになっています", "denied": "権限設定で禁止されています",
           "no_match": "これができる能力が見つかりませんでした"},
    'ko': {"disabled": "네코클로가 꺼져 있습니다", "not_installed": "이 일을 할 수 있는 능력이 설치되어 있지 않습니다",
           "not_enabled": "능력이 설치되어 있지만 꺼져 있습니다", "denied": "권한 설정에서 금지되어 있습니다",
           "no_match": "이 일을 할 수 있는 능력을 찾지 못했습니다"},
    'ru': {"disabled": "NekoClaw выключен", "not_installed": "не установлено ничего, что может это сделать",
           "not_enabled": "возможность установлена, но выключена", "denied": "это запрещено настройками разрешений",
           "no_match": "не найдено возможности, которая может это сделать"},
    'es': {"disabled": "NekoClaw está apagado", "not_installed": "no hay instalado nada que pueda hacerlo",
           "not_enabled": "la capacidad está instalada pero apagada", "denied": "los permisos lo prohíben",
           "no_match": "no se encontró ninguna capacidad que pueda hacerlo"},
    'pt': {"disabled": "o NekoClaw está desligado", "not_installed": "não há nada instalado que consiga fazer isso",
           "not_enabled": "a capacidade está instalada, mas desligada", "denied": "as permissões proíbem",
           "no_match": "nenhuma capacidade que consiga fazer isso foi encontrada"},
}

QUERY_TEXTS = {
    'zh': {"active": "在办的事：", "recent": "最近办完的：", "empty": "没有任务记录。",
           "can_plugin": "能办：有对得上的插件（{names}）。", "can_familiar": "能办：可以交给使魔去做。",
           "cannot": "办不了：{reason}", "asked": "在等用户回答：{question}", "result": "结果：{summary}",
           "unsure": "没找到现成的能力，真要办可以派发试试。"},
    'zh-TW': {"active": "在辦的事：", "recent": "最近辦完的：", "empty": "沒有任務記錄。",
              "can_plugin": "能辦：有對得上的外掛（{names}）。", "can_familiar": "能辦：可以交給使魔去做。",
              "cannot": "辦不了：{reason}", "asked": "在等使用者回答：{question}", "result": "結果：{summary}",
              "unsure": "沒找到現成的能力，真要辦可以派發試試。"},
    'en': {"active": "Under way:", "recent": "Recently finished:", "empty": "There is no task on record.",
           "can_plugin": "Can be done: matching plugins exist ({names}).", "can_familiar": "Can be done: a familiar can take it.",
           "cannot": "Cannot be done: {reason}", "asked": "waiting for the user to answer: {question}", "result": "result: {summary}",
           "unsure": "No ready capability was found; to really get it done, dispatch it and see."},
    'ja': {"active": "進めている用件：", "recent": "最近終わった用件：", "empty": "用件の記録はありません。",
           "can_plugin": "対応できます：合うプラグインがあります（{names}）。", "can_familiar": "対応できます：使い魔に任せられます。",
           "cannot": "対応できません：{reason}", "asked": "ユーザーの回答待ち：{question}", "result": "結果：{summary}",
           "unsure": "すぐ使える能力は見つかりませんでした。本当に片付けたいなら任せてみてください。"},
    'ko': {"active": "진행 중인 일:", "recent": "최근 끝난 일:", "empty": "작업 기록이 없습니다.",
           "can_plugin": "처리할 수 있습니다: 맞는 플러그인이 있습니다({names}).", "can_familiar": "처리할 수 있습니다: 사역마에게 맡길 수 있습니다.",
           "cannot": "처리할 수 없습니다: {reason}", "asked": "사용자 답변 대기: {question}", "result": "결과: {summary}",
           "unsure": "바로 쓸 수 있는 능력은 찾지 못했습니다. 정말 처리하려면 맡겨 보세요."},
    'ru': {"active": "Выполняется:", "recent": "Недавно завершено:", "empty": "Записей о задачах нет.",
           "can_plugin": "Сделать можно: есть подходящие плагины ({names}).", "can_familiar": "Сделать можно: это можно поручить фамильяру.",
           "cannot": "Сделать нельзя: {reason}", "asked": "ждёт ответа пользователя: {question}", "result": "результат: {summary}",
           "unsure": "Готовой возможности не нашлось; чтобы действительно сделать это, попробуйте поручить."},
    'es': {"active": "En marcha:", "recent": "Terminadas hace poco:", "empty": "No hay tareas registradas.",
           "can_plugin": "Se puede hacer: hay plugins que encajan ({names}).", "can_familiar": "Se puede hacer: un familiar puede encargarse.",
           "cannot": "No se puede hacer: {reason}", "asked": "esperando la respuesta del usuario: {question}", "result": "resultado: {summary}",
           "unsure": "No se encontró una capacidad lista; para hacerlo de verdad, prueba a encargarlo."},
    'pt': {"active": "Em andamento:", "recent": "Concluídas há pouco:", "empty": "Não há tarefas registradas.",
           "can_plugin": "Dá para fazer: há plugins que servem ({names}).", "can_familiar": "Dá para fazer: um familiar pode cuidar disso.",
           "cannot": "Não dá para fazer: {reason}", "asked": "aguardando a resposta do usuário: {question}", "result": "resultado: {summary}",
           "unsure": "Nenhuma capacidade pronta foi encontrada; para fazer de verdade, tente encarregar."},
}

# =====================================================================
# ======= Cue patterns (matched without a model) =======
# =====================================================================
# Regular expressions, case-insensitive, covering the eight interface
# languages in one list each. They only say what kind of sentence this is;
# which commission it is about is matched against the ledger.

STOP_CUE_PATTERNS = (
    # Chinese, Simplified and Traditional.
    r"叫停|停止|停下|停掉|先停|中止|终止|終止|取消|算了|别.{0,6}了|別.{0,6}了|不用.{0,4}了|不要.{0,4}了|不必.{0,4}了",
    # English.
    r"\b(?:stop|cancel|abort|halt|never\s?mind|forget\s+it|call\s+it\s+off|don'?t\s+bother)\b",
    # Japanese.
    r"止めて|やめて|やめる|中止|キャンセル|取り消",
    # Korean.
    r"그만|취소|중지|멈춰|멈추",
    # Russian.
    r"останов|отмен|прекрат|хватит|\bстоп\b|не\s+надо",
    # Spanish and Portuguese.
    r"\b(?:det[eé]n\w*|cancel\w*|olv[ií]da\w*|d[eé]jalo|ya\s+no|pare|para\s+com|esquece\w*|deixa\s+pra\s+l[aá])\b",
)

# Words that point at something without naming it ("that one", "it", "please").
# They are set aside before a sentence is compared with the commissions, so
# "stop that" reads as a stop request that names nothing.
VAGUE_REFERENCE_PATTERNS = (
    r"那个|那個|这个|這個|那件事|这件事|這件事|那件|这件|這件|任务|任務|事情|全部|所有|都|先|吧|啊|呀|了|的",
    r"\b(?:that|this|it|them|those|these|one|ones|thing|things|task|tasks|everything|all|now|please|the|doing|for\s+me)\b",
    r"それ|これ|あれ|全部|もう|ください|して|を|は",
    r"그거|이거|저거|전부|모두|제발|이제|해|줘",
    r"это|то|всё|все|пожалуйста|уже|сейчас",
    r"\b(?:eso|esto|aquello|todo|por\s+favor|ya|ahora|isso|isto|aquilo|tudo|agora)\b",
)

# Words too common to say which commission a stop request means: days, times
# of day, "help me". "Cancel tomorrow's alarm" shares "tomorrow" with a
# weather lookup for tomorrow and is still not about it. Only the matching of
# a stop request against the commissions leaves them out; a sentence made of
# nothing else still counts as naming something.
GENERIC_WORD_PATTERNS = (
    # Chinese, Simplified and Traditional.
    r"今天|明天|后天|後天|昨天|今晚|明早|明晚|早上|上午|中午|下午|晚上|现在|現在|一会儿|一會兒|待会|待會|等会|等會|马上|馬上|帮我|幫我|给我|給我|一下",
    # English.
    r"\b(?:today|tomorrow|tonight|yesterday|morning|afternoon|evening|night|now|later|soon|again|me|my|for|to|of|a|an|on|at|in)\b|['’]s\b",
    # Japanese.
    r"今日|明日|あした|あす|明後日|昨日|今夜|今晩|今朝|あとで|後で|すぐ",
    # Korean.
    r"오늘|내일|모레|어제|아침|점심|저녁|지금|나중에|이따가|바로",
    # Russian.
    r"\b(?:сегодня|завтра|послезавтра|вчера|утром|днём|днем|вечером|ночью|сейчас|потом|позже|мне|меня|мой|мою)\b",
    # Spanish and Portuguese.
    r"\b(?:hoy|mañana|ayer|noche|tarde|ahora|luego|después|hoje|amanhã|ontem|noite|agora|depois|logo|me|mi|meu|minha|de|do|da|la|el|los|las|o|os|as|a|para)\b",
)

SUPPLEMENT_CUE_PATTERNS = (
    r"补充|補充|另外|还有|還有|顺便|順便|再加|加上|改成|换成|換成|改为|改為|记得|記得|对了|對了",
    r"\b(?:also|additionally|in\s+addition|by\s+the\s+way|instead|change\s+it\s+to|make\s+sure|one\s+more\s+thing|and\s+add)\b",
    r"ついでに|それと|追加で|あと、|変更して",
    r"추가로|그리고|또한|참고로|바꿔",
    r"также|ещё|кстати|вместо\s+этого|добавь",
    r"\b(?:adem[aá]s|tambi[eé]n|por\s+cierto|en\s+vez\s+de|al[eé]m\s+disso|tamb[eé]m|a\s+prop[oó]sito|em\s+vez\s+de)\b",
)

ANSWER_CUE_PATTERNS = (
    r"^\s*(?:回答|回复|回覆|答复|答覆|告诉它|告訴它|告诉使魔|告訴使魔)",
    r"^\s*(?:answer|reply|tell\s+(?:it|the\s+familiar))\b",
    r"^\s*(?:回答[:：]|返事[:：]|答え[:：])",
    r"^\s*(?:답변|대답)\s*[:：]",
    r"^\s*(?:ответ|ответь)\s*[:：]?",
    r"^\s*(?:respuesta|responde|resposta|responda)\s*[:：]?",
)

# Answers to a permission prompt. Checked in this order: deny, always, once.
PERMISSION_DENY_PATTERNS = (
    r"不行|不可以|不能|不允许|不允許|不准|別|别|拒绝|拒絕|禁止|不要",
    r"\b(?:no|nope|deny|denied|don'?t|do\s+not|refuse|never)\b",
    r"だめ|ダメ|駄目|許可しない|いいえ|拒否",
    r"안\s?돼|안\s?됨|거절|아니",
    r"\bнет\b|нельзя|запрет|отказ",
    r"\b(?:no|nunca|den[ií]ega\w*|rechaz\w*|não|nao|negar|recus\w*)\b",
)

PERMISSION_ALWAYS_PATTERNS = (
    r"以后都|以後都|一直|总是|總是|每次都|都可以|都行|永远|永遠",
    r"\b(?:always|every\s+time|from\s+now\s+on|whenever)\b",
    r"今後ずっと|いつも|毎回|ずっと",
    r"앞으로|항상|매번|계속",
    r"всегда|каждый\s+раз|впредь",
    r"\b(?:siempre|cada\s+vez|de\s+ahora\s+en\s+adelante|sempre|toda\s+vez|daqui\s+pra\s+frente)\b",
)

PERMISSION_ONCE_PATTERNS = (
    r"就这一次|就這一次|这一次|這一次|可以|好的|好吧|行|同意|允许|允許|批准|没问题|沒問題|嗯",
    r"\b(?:yes|yeah|yep|ok|okay|sure|allow|allowed|approve|approved|go\s+ahead|fine|just\s+this\s+once)\b",
    r"今回だけ|はい|いいよ|いいです|許可|どうぞ|オーケー",
    r"이번만|네|예|좋아|허락|허용|그래",
    r"\bда\b|можно|разреш|хорошо|только\s+в\s+этот\s+раз|ладно",
    r"\b(?:s[ií]|vale|claro|permit\w*|adelante|solo\s+esta\s+vez|sim|pode|tudo\s+bem|s[oó]\s+desta\s+vez)\b",
)

# A query that asks whether something can be done, rather than how a task is going.
CAPABILITY_QUESTION_PATTERNS = (
    r"能不能|可不可以|会不会|會不會|能否|可否|能.{0,8}吗|能.{0,8}嗎|可以.{0,8}吗|可以.{0,8}嗎|会.{0,6}吗|會.{0,6}嗎|办得到|辦得到|做得到|有没有办法|有沒有辦法",
    r"\b(?:can\s+(?:you|it|she|we)|could\s+(?:you|it)|are\s+you\s+able|is\s+it\s+possible|able\s+to|capable\s+of)\b",
    r"できる|できます|可能です|できますか",
    r"할\s?수\s?있|가능해|가능한가",
    r"можешь|можно\s+ли|умеешь|способн|возможно\s+ли",
    r"\b(?:puedes|se\s+puede|es\s+posible|eres\s+capaz|consegue|d[aá]\s+para|[eé]\s+poss[ií]vel|pode\s+fazer)\b",
)

# What a stated task preference is about, read without a model: the decision
# model returns the preference as a sentence only. Kinds are tried in the
# order ask_first, report, default, capability, each against every language
# (a preference may be worded in another language than the commission); a
# sentence matching none is "other". Reporting, ask-first and default entries
# are given to every task, the others only to tasks sharing words with them.
PREFERENCE_KIND_PATTERNS = {
    'zh': {
        "ask_first": r"先问|问过我|问一下我|问我一下|征求|先确认|确认一下|先跟我说|经过我同意",
        "report": r"汇报|报告|告诉我|通知我|说一声|跟我说|结果|列表|列出|摘要|总结|主动说|主动提",
        "capability": r"插件|工具|别用|不要用|改用|换用|优先用|用.{0,12}(?:来|去)(?:查|搜|找|做|办)",
        "default": r"默认|一律|通常|一般都|每次都|总是|以后.{0,10}都",
    },
    'zh-TW': {
        "ask_first": r"先問|問過我|問一下我|問我一下|徵求|先確認|確認一下|先跟我說|經過我同意",
        "report": r"匯報|報告|告訴我|通知我|說一聲|跟我說|結果|清單|列表|列出|摘要|總結|主動說|主動提",
        "capability": r"外掛|插件|工具|別用|不要用|改用|換用|優先用|用.{0,12}(?:來|去)(?:查|搜|找|做|辦)",
        "default": r"預設|默認|一律|通常|一般都|每次都|總是|以後.{0,10}都",
    },
    'en': {
        "ask_first": r"\bask\b.{0,30}\b(?:first|before)\b|\b(?:check|confirm)\s+with\s+me\b|\bwithout\s+asking\b|\b(?:approval|permission|consent)\b",
        "report": r"\b(?:report\w*|tell\s+me|let\s+me\s+know|notify|results?|summar\w*|bullet\s*points?|lists?)\b",
        "capability": r"\b(?:plugins?|tools?|apps?|don'?t\s+use|never\s+use|instead\s+of)\b",
        "default": r"\b(?:by\s+default|defaults?|always|usually|unless\s+(?:I|told))\b",
    },
    'ja': {
        "ask_first": r"先に(?:聞|確認)|確認してから|聞いてから|前に.{0,6}(?:確認|聞)|許可を(?:取|得|もら)",
        "report": r"報告|知らせ|教えて|結果|まとめ|要約|箇条書き|リスト|一覧",
        "capability": r"プラグイン|ツール|アプリ|を使って|は使わ",
        "default": r"デフォルト|既定|いつも|基本的に|毎回|普段",
    },
    'ko': {
        "ask_first": r"먼저\s?(?:물어|확인|허락)|물어보고|확인하고|허락(?:을|받)",
        "report": r"보고|알려|결과|요약|목록|정리해서",
        "capability": r"플러그인|도구|앱으로|(?:을|를)\s?(?:써|사용)|쓰지\s?마|사용하지\s?마",
        "default": r"기본|항상|매번|보통",
    },
    'ru': {
        "ask_first": r"(?:сначала|прежде|заранее)\s+(?:спро|уточн|соглас|спрашива)|спрашивай|без\s+спроса|разрешени",
        "report": r"отч[её]т|сообща|сообщи|результат|итог|кратко|списк|спис[оа]к",
        "capability": r"плагин|инструмент|приложени|используй",
        "default": r"по\s+умолчанию|всегда|обычно|каждый\s+раз",
    },
    'es': {
        "ask_first": r"\bpreg[uú]nta\w*\b.{0,30}\b(?:antes|primero)\b|\bconsult\w*\b.{0,30}\b(?:antes|primero)\b|\bsin\s+preguntar\b|\bmi\s+(?:permiso|aprobaci[oó]n)\b",
        "report": r"\b(?:inf[oó]rm\w*|av[ií]s\w*|resultados?|resumen\w*|res[uú]menes|listas?)\b",
        "capability": r"\b(?:plugins?|complementos?|herramientas?|aplicaci[oó]n|no\s+uses|usa|utiliza)\b",
        "default": r"\b(?:por\s+defecto|predeterminad\w*|siempre|normalmente)\b",
    },
    'pt': {
        "ask_first": r"\bpergunt\w*\b.{0,30}\b(?:antes|primeiro)\b|\bconsult\w*\b.{0,30}\b(?:antes|primeiro)\b|\bsem\s+perguntar\b|\bminha\s+(?:permiss[aã]o|aprova[cç][aã]o)\b",
        "report": r"\b(?:relat\w*|avis\w*|resultados?|resum\w*|listas?)\b",
        "capability": r"\b(?:plugins?|ferramentas?|aplicativos?|n[aã]o\s+use|use|usa|utilize)\b",
        "default": r"\b(?:por\s+padr[aã]o|padr[aã]o|sempre|normalmente)\b",
    },
}

# =====================================================================
# ======= Familiar brief =======
# =====================================================================
# Appended to a familiar's system prompt through ``FamiliarSpec.instructions``.
# English only, like the rest of the runtime's model-facing text.

FAMILIAR_BRIEF_TEMPLATE = (
    "You are working on one commission for {lanlan_name}, the companion the user talks to. "
    "She handed the commission over and will relay your reports; you do not talk to the user directly "
    "except through ask_user_question and report.\n"
    "Commission: {dispatch_text}\n"
    "What the user said: {user_text}"
)

FAMILIAR_BRIEF_PREFERENCES_HEADER = (
    "How the user wants such things done (guidance only; it does not change what you are permitted to do):"
)

FAMILIAR_BRIEF_GATEWAY_NOTE = (
    "Tools whose names start with plugin__ or mcp__ come from the host. They are the quick way to reach "
    "installed plugins and connected services; prefer them over doing the same thing by hand. "
    "capability_search finds further ones by description, and capability_call runs one by name."
)

FAMILIAR_CHILD_BRIEF_TEMPLATE = (
    "You were started by another familiar to take one part of its work.\n"
    "Your part: {description}"
)

FAMILIAR_CHILD_RESULT_TEMPLATE = "[sub-familiar {name} finished: {status}]\n{text}"
