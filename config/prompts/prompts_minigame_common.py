# -*- coding: utf-8 -*-
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

"""Shared helpers for minigame prompt modules (soccer, badminton).

config/prompts deliberately keeps two locale-key schemes side by side. This
module's ``_normalize_prompt_lang`` keys Simplified Chinese as the short ``zh``
for soccer plus every system/pregame prompt; badminton quick-lines use FULL keys
(``normalize_badminton_prompt_locale``), which key it as ``zh-CN``. Since issue
#2500 step 2 both schemes keep ``zh-TW`` as its own key — they now differ only
in how they spell Simplified Chinese.

Both delegate to ``config.prompts._locale.normalize_prompt_locale`` and differ
only in the keyword arguments they pass. The schemes themselves stay separate
because the two families of tables are keyed differently, not because either
loses the script.

See docs/contributing/developer-notes.md #7, PR #2000, and issue #2500.
"""

from config.prompts._locale import normalize_prompt_locale
from config.prompts.prompts_sys import _loc


def _normalize_prompt_lang(lang: str | None) -> str:
    """Normalize a language code to a prompt-dict key: a SHORT code, or ``zh-TW``.

    ``default="zh"`` is intentional and not a copy of the other prompt modules:
    the soccer/game module hardcodes Chinese-flavored helpers (e.g. the fullwidth
    "；" in ``_apply_soccer_anger_pressure_cap``, which takes no language
    parameter at all), so the module-internal default is Chinese while the
    cross-module fallback (``resolve_global_language``) stays English.

    ``keep_traditional=True`` as of issue #2500 step 2. Every dict reached through
    this normalizer carries a ``'zh-TW'`` template (step 1), and the game-route
    call sites now hand over a FULL locale, so Traditional survives the whole way
    down. The two halves had to land together: the flag alone changes nothing when
    the callers pass a SHORT code that already collapsed the script, and the call
    sites alone would hand Traditional users a normalizer that drops it.

    Three tables are read as ``.get(key) or table["en"]`` rather than through
    ``_loc``, so a ``zh-TW`` key they lack would fall to ENGLISH, not to Simplified
    (``SOCCER_``/``BADMINTON_PREGAME_CONTEXT_FORMATTER_LABELS`` and every table
    behind ``prompts_minigame_route._labels``). Adding a table here without a
    ``zh-TW`` row is therefore a regression, not a soft fallback.
    """
    return normalize_prompt_locale(lang, default="zh", simplified="zh", keep_traditional=True)


def _localized_template(templates: dict[str, str], lang: str | None) -> str:
    return _loc(templates, _normalize_prompt_lang(lang))


# 开局上下文输入水印：pregame 的近期记录 + 启动参数走独立 HumanMessage（裸 JSON），
# 用收尾水印标出数据块边界，让模型分清上面那块是注入输入而非指令。逐 locale 保留中文
# （与 prompts_minigame_route.py 的成对水印对齐），内部禁冒号破折号。
PREGAME_CONTEXT_INPUT_WATERMARK = "======以上为开局近期记录与启动参数======"


AUTHOR_MANAGED_DIALOGUE_HOST_PROMPTS = {
    "zh": """\
这是一次由游戏作者编排消息顺序的 N.E.K.O 小游戏对话请求。
当前小游戏类型：{game_type}。
你始终是当前 N.E.K.O 角色 {name}，正在和主人 {master_name} 互动。
角色设定：
{personality}
后续消息由游戏作者按原始顺序提供，可作为本局游戏的系统规则、上下文和历史。保持当前角色身份；不要泄露或修改宿主凭据、模型与服务商配置、真实文件路径或未通过授权能力提供的宿主上下文。
======以上为 N.E.K.O 小游戏宿主固定上下文======""",
    "zh-TW": """\
這是一次由遊戲作者編排訊息順序的 N.E.K.O 小遊戲對話請求。
目前小遊戲類型：{game_type}。
你始終是目前的 N.E.K.O 角色 {name}，正在和主人 {master_name} 互動。
角色設定：
{personality}
後續訊息由遊戲作者依原始順序提供，可作為本局遊戲的系統規則、上下文和歷史。保持目前角色身分；不要洩露或修改宿主憑據、模型與服務商設定、真實檔案路徑，或未透過授權能力提供的宿主上下文。
======以上为 N.E.K.O 小游戏宿主固定上下文======""",
    "en": """\
This is a N.E.K.O mini-game dialogue request whose message order is managed by the game author.
Current mini-game type: {game_type}.
You remain the current N.E.K.O character {name}, interacting with your master {master_name}.
Character profile:
{personality}
The following messages are supplied in their original order by the game author and may define this round's game rules, context, and history. Keep the current character identity. Do not reveal or alter host credentials, model or provider configuration, real file paths, or host context that was not delivered through an authorized capability.
======以上为 N.E.K.O 小游戏宿主固定上下文======""",
    "ja": """\
これは、ゲーム作者がメッセージ順を管理する N.E.K.O ミニゲームの会話リクエストです。
現在のミニゲーム種別：{game_type}。
あなたは常に現在の N.E.K.O キャラクター {name} であり、マスター {master_name} と交流しています。
キャラクター設定：
{personality}
後続メッセージはゲーム作者が元の順序で提供し、このラウンドのゲーム規則、コンテキスト、履歴を定義できます。現在のキャラクターとして振る舞い続け、ホストの認証情報、モデルやプロバイダー設定、実ファイルパス、または認可された機能から提供されていないホストコンテキストを開示・変更しないでください。
======以上为 N.E.K.O 小游戏宿主固定上下文======""",
    "ko": """\
이 요청은 게임 제작자가 메시지 순서를 관리하는 N.E.K.O 미니게임 대화 요청입니다.
현재 미니게임 유형: {game_type}.
당신은 항상 현재 N.E.K.O 캐릭터 {name}이며, 주인 {master_name}와 상호작용합니다.
캐릭터 설정:
{personality}
뒤의 메시지는 게임 제작자가 원래 순서대로 제공하며 이번 라운드의 게임 규칙, 컨텍스트와 기록을 정의할 수 있습니다. 현재 캐릭터 정체성을 유지하고 호스트 자격 증명, 모델 또는 제공자 설정, 실제 파일 경로, 승인된 기능을 통해 제공되지 않은 호스트 컨텍스트를 공개하거나 변경하지 마세요.
======以上为 N.E.K.O 小游戏宿主固定上下文======""",
    "ru": """\
Это запрос диалога мини-игры N.E.K.O, порядок сообщений в котором задаёт автор игры.
Тип текущей мини-игры: {game_type}.
Ты всегда остаёшься текущим персонажем N.E.K.O {name} и взаимодействуешь с хозяином {master_name}.
Описание персонажа:
{personality}
Следующие сообщения переданы автором игры в исходном порядке и могут задавать правила, контекст и историю этой партии. Сохраняй личность текущего персонажа. Не раскрывай и не изменяй учётные данные хоста, настройки модели или провайдера, реальные пути к файлам и контекст хоста, который не был предоставлен через разрешённую возможность.
======以上为 N.E.K.O 小游戏宿主固定上下文======""",
    "es": """\
Esta es una solicitud de diálogo de minijuego N.E.K.O cuyo orden de mensajes gestiona la persona creadora del juego.
Tipo de minijuego actual: {game_type}.
Sigues siendo siempre el personaje N.E.K.O actual, {name}, e interactúas con tu amo, {master_name}.
Perfil del personaje:
{personality}
Los mensajes siguientes se proporcionan en su orden original y pueden definir las reglas, el contexto y el historial de esta partida. Mantén la identidad del personaje actual. No reveles ni modifiques credenciales del host, configuración del modelo o proveedor, rutas reales de archivos ni contexto del host que no se haya entregado mediante una capacidad autorizada.
======以上为 N.E.K.O 小游戏宿主固定上下文======""",
    "pt": """\
Esta é uma solicitação de diálogo de minijogo N.E.K.O cuja ordem de mensagens é gerenciada pela pessoa autora do jogo.
Tipo atual de minijogo: {game_type}.
Você continua sendo a personagem N.E.K.O atual, {name}, interagindo com seu mestre, {master_name}.
Perfil da personagem:
{personality}
As mensagens seguintes são fornecidas na ordem original e podem definir as regras, o contexto e o histórico desta partida. Mantenha a identidade da personagem atual. Não revele nem altere credenciais do host, configurações de modelo ou provedor, caminhos reais de arquivos nem contexto do host que não tenha sido entregue por uma capacidade autorizada.
======以上为 N.E.K.O 小游戏宿主固定上下文======""",
}


def get_author_managed_dialogue_host_prompt(lang: str | None = None) -> str:
    """Return the protected host prefix for author-managed game dialogue."""
    return _localized_template(AUTHOR_MANAGED_DIALOGUE_HOST_PROMPTS, lang)
