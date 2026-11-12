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

"""The two task tools of the main dialogue model: dispatch and query.

Their definitions are sent with every request of a session that has NekoClaw
switched on, so every word here is paid for on every turn. The opening
sentence of such a session (``SESSION_INIT_PROMPT_AGENT``) plus these two
definitions stay within a fixed budget in Chinese; the test
``tests/unit/test_task_tools_budget.py`` holds them to it.

Both tools take one sentence and no identifier. Which task a sentence is about
is worked out by the task brain (``nekoclaw/host/task_brain.py``), which keeps
the record; the main dialogue's session is replaced every few turns and would
lose any number it was given.
"""

DISPATCH_TOOL_NAME = "dispatch_task"
QUERY_TOOL_NAME = "query_tasks"

DISPATCH_TOOL_DESCRIPTION = {
    'zh': '把对话之外要动手的事交给任务系统去办；补充、改主意、叫停、转达用户的回答也用它。',
    'zh-TW': '把對話以外要動手的事交給任務系統去辦；補充、改主意、叫停、轉達使用者的回答也用它。',
    'en': "Hand what is to be done outside the conversation to the task system; also use it to add to a task, change it, stop it, or pass on the user's answer.",
    'ja': '会話の外で実際に手を動かす用件をタスクシステムに任せます。追加、変更、中止、ユーザーの回答の伝達にも使います。',
    'ko': '대화 밖에서 실제로 해야 하는 일을 작업 시스템에 맡깁니다. 추가, 변경, 중지, 사용자의 답 전달에도 씁니다.',
    'ru': 'Передать системе задач то, что нужно сделать вне диалога; также для дополнения, изменения, остановки задачи и передачи ответа пользователя.',
    'es': 'Encarga al sistema de tareas lo que haya que hacer fuera de la conversación; úsala también para añadir, cambiar, parar una tarea o transmitir la respuesta del usuario.',
    'pt': 'Encarrega o sistema de tarefas do que precisar ser feito fora da conversa; use também para acrescentar, mudar, parar uma tarefa ou repassar a resposta do usuário.',
}

DISPATCH_TOOL_TASK_DESCRIPTION = {
    'zh': '一句话说清要办什么。',
    'zh-TW': '一句話說清楚要辦什麼。',
    'en': 'One sentence saying what to do.',
    'ja': '何をするかを一文で。',
    'ko': '무엇을 할지 한 문장으로.',
    'ru': 'Одно предложение: что сделать.',
    'es': 'Una frase que diga qué hacer.',
    'pt': 'Uma frase dizendo o que fazer.',
}

QUERY_TOOL_DESCRIPTION = {
    'zh': '问任务系统在办什么、办得怎样、某事能不能办。',
    'zh-TW': '問任務系統在辦什麼、辦得怎樣、某事能不能辦。',
    'en': 'Ask the task system what is under way, how a task is going, or whether something can be done.',
    'ja': 'タスクシステムに、いま何を進めているか、進み具合、ある用件ができるかを尋ねます。',
    'ko': '작업 시스템에 지금 무엇을 하는지, 어떻게 되어 가는지, 어떤 일을 할 수 있는지 묻습니다.',
    'ru': 'Спросить систему задач, что выполняется, как идёт задача и можно ли что-то сделать.',
    'es': 'Pregunta al sistema de tareas qué está en marcha, cómo va una tarea o si algo se puede hacer.',
    'pt': 'Pergunta ao sistema de tarefas o que está em andamento, como vai uma tarefa ou se algo pode ser feito.',
}

QUERY_TOOL_QUESTION_DESCRIPTION = {
    'zh': '想问什么；留空列出在办的事。',
    'zh-TW': '想問什麼；留空列出在辦的事。',
    'en': 'What to ask; empty lists what is under way.',
    'ja': '尋ねたいこと。空なら進行中の用件を一覧します。',
    'ko': '묻고 싶은 것. 비우면 진행 중인 일을 나열합니다.',
    'ru': 'Вопрос; пусто — список того, что выполняется.',
    'es': 'Qué preguntar; vacío lista lo que está en marcha.',
    'pt': 'O que perguntar; vazio lista o que está em andamento.',
}

# Tool results the main server produces by itself, when the task system
# could not be asked. They must not read as an acceptance.
TASK_TOOL_UNREACHABLE = {
    'zh': '任务系统没有回应，这件事没有交出去。不要说已经在办。',
    'zh-TW': '任務系統沒有回應，這件事沒有交出去。不要說已經在辦。',
    'en': 'The task system did not respond; this was not handed over. Do not say it is under way.',
    'ja': 'タスクシステムから応答がなく、この用件は渡せていません。進めているとは言わないでください。',
    'ko': '작업 시스템이 응답하지 않아 이 일은 넘기지 못했습니다. 진행 중이라고 말하지 마세요.',
    'ru': 'Система задач не ответила; это не было передано. Не говорите, что это выполняется.',
    'es': 'El sistema de tareas no respondió; esto no se entregó. No digas que está en marcha.',
    'pt': 'O sistema de tarefas não respondeu; isto não foi entregue. Não diga que está em andamento.',
}

TASK_TOOL_QUERY_UNREACHABLE = {
    'zh': '任务系统没有回应，现在查不到。',
    'zh-TW': '任務系統沒有回應，現在查不到。',
    'en': 'The task system did not respond; nothing can be looked up right now.',
    'ja': 'タスクシステムから応答がなく、いまは確認できません。',
    'ko': '작업 시스템이 응답하지 않아 지금은 확인할 수 없습니다.',
    'ru': 'Система задач не ответила; сейчас ничего узнать нельзя.',
    'es': 'El sistema de tareas no respondió; ahora no se puede consultar.',
    'pt': 'O sistema de tarefas não respondeu; agora não dá para consultar.',
}

TASK_TOOL_EMPTY = {
    'zh': '没有说要办什么。',
    'zh-TW': '沒有說要辦什麼。',
    'en': 'Nothing was said about what to do.',
    'ja': '何をするかが書かれていません。',
    'ko': '무엇을 할지 말하지 않았습니다.',
    'ru': 'Не сказано, что нужно сделать.',
    'es': 'No se dijo qué hay que hacer.',
    'pt': 'Não foi dito o que fazer.',
}
