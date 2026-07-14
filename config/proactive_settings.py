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

"""Mini-game invitation and proactive-source decay settings."""

MINI_GAME_INVITE_ENABLED = True
"""Mini-game 邀请短路通道总开关（默认开）。
- 用途：proactive_chat 在过完 propensity / skip_probability / restricted_screen_only
  这几道门后，按 MINI_GAME_INVITE_TRIGGER_PROBABILITY 概率短路成"邀请玩家来玩
  小游戏"，跳过 Phase 1/2 LLM。关掉此开关 = 永远不触发该分支，proactive_chat
  退化回纯 source-driven。
- 上游：main_routers/system_router._maybe_deliver_mini_game_invite。"""

MINI_GAME_INVITE_TRIGGER_PROBABILITY = 0.12
"""每次 eligible 主动搭话进入 mini-game 邀请短路的概率。
- 取值约定：[0.0, 1.0]，0.0=禁用（等价于 ENABLED=False），1.0=每次都邀请。
- 上游：random.random() < 此值 → 命中 → 走邀请短路。"""

MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS = 2 * 3600
"""accept 后的最小静默秒数（默认 2h）。
- 配合 MINI_GAME_INVITE_COOLDOWN_CHATS：两条件都跨过才允许下次掷骰。
- 上游：_mini_game_invite_in_cooldown 时间侧判定（state.last_response_choice='accept'）。
- 历史：原统一 1h（PR follow-up #1 从 24h 降下来），后再拆成 accept/decline 双
  阈值——accept 体感"刚玩完一局"短一些（2h），decline 表达"不感兴趣"延长到 5h
  避免短期复扰；之间没有 chats 门差异，10 条仍共用。"""

MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS = 5 * 3600
"""decline 后的最小静默秒数（默认 5h）。
- 配合 MINI_GAME_INVITE_COOLDOWN_CHATS：两条件都跨过才允许下次掷骰。
- 上游：_mini_game_invite_in_cooldown 时间侧判定（state.last_response_choice='decline'）。
- 比 accept 长是因为 decline 是明确"不想玩"信号，短期复扰体感差；5h 跨过一般
  的"刚拒绝完几分钟又问"窗口，又不至于一整天彻底沉默。"""

MINI_GAME_INVITE_NEW_USER_FORCE_AT = 4
"""新用户在第 N 次「成功投递的主动搭话」时强制触发 mini-game 邀请。
- 「新用户」= ``state.delivered_at is None``（角色级，从未发过 invite）。
- N 是整数，>=1；当持久化计数 ``proactive_chat_total >= N - 1`` 时，
  本次投递走 force-trigger（绕开 10% 骰子，但仍尊重 propensity / 工作状态 /
  unfinished_thread / cooldown 等其它 gate）。
- 默认 4 = 用户成功收到 3 条普通主动搭话后，第 4 条强制变成游戏邀请；让
  从未玩过的人有一次确定的「被邀请」机会，不靠 10% 骰子赌。
- 上游：_maybe_deliver_mini_game_invite force-first 分支。"""

MINI_GAME_INVITE_AVAILABLE_GAMES: tuple[str, ...] = ("soccer", "badminton")
"""mini-game 邀请可选的 game_type 列表。
- 命中后从该列表 random.choice 选一个，文案从
  config.prompts.prompts_proactive.MINI_GAME_INVITE_LINES_BY_GAME[game_type] 取。
- 当前只有 soccer；badminton 后端与文案在本 PR 预埋，但实际邀请入口需要等
  页面路由和 Electron 窗口注册在后续 PR 落地后再启用。
- 顺序无意义（用 random.choice）；用 tuple 防止运行期被改写。"""

MINI_GAME_INVITE_COOLDOWN_CHATS = 10
"""一次邀请被回应后，需要再经过的"成功投递的主动搭话"次数。
- 与 MINI_GAME_INVITE_COOLDOWN_AFTER_{ACCEPT,DECLINE}_SECONDS 同时满足才解禁；
  任一不满足都继续抑制。chats 门 accept/decline 共用，不按 choice 拆。
- 上游：_mini_game_invite_in_cooldown 计数侧判定。"""

MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS = 5 * 60
"""用户选择「回头再说」后的短期再掷骰抑制秒数（默认 5min）。
- D2 语义：reset state（delivered_at/responded_at/chats_since_response 都清零，
  让 force-first 与普通 10% 掷骰都恢复正常）但加一个 ``suppressed_until`` 软门，
  这段时间内 ``_mini_game_invite_in_cooldown`` 仍返回 True 防止下一次 proactive
  立刻又邀请，体感上像"等等再问我"。过了这个窗口下次 proactive 才重新走骰子。
- 上游：endpoint /api/mini_game/invite/respond 的 'later' action。"""

MINI_GAME_LAUNCH_URL_BY_GAME: dict[str, str] = {
    'soccer': '/soccer_demo',
    'badminton': '/badminton_demo',
}
"""game_type → 实际打开的页面 URL。前端 `window.open(url)` 让 Electron 主进程
``setWindowOpenHandler`` 拦截开独立 BrowserWindow（普通浏览器是新 tab）；URL
会带上 ``?lanlan_name=...&session_id=...`` query。新 mini-game 加新 entry 即可。"""

MINI_GAME_INVITE_FORCE_GAME_TYPE: str | None = None
"""【调试用临时旗标】非 None 时，每次合格的主动搭话都强制走 mini-game 邀请短路，
且使用此值作为 game_type，跳过 activity_snapshot / propensity / away /
unfinished_thread / cooldown / probability / force-first / 用户级 toggle 等所有
gate；仅 ``MINI_GAME_INVITE_ENABLED`` 总开关仍生效作为最后 kill switch。
- 取值约定：None 关闭（生产默认）；'soccer' 等 ``MINI_GAME_INVITE_LINES_BY_GAME``
  里存在的合法 key。非法 key 会在投递时 warn + 跳过。
- 用途：本地手测三 context UI 时，不想等 force-first 凑齐 N-1 次主动搭话、也不
  想反复重启 fixture 调 cooldown。线上不要打开。
- 上游：``main_routers/system_router._maybe_deliver_mini_game_invite``。"""

PROACTIVE_SOURCE_HARD_SKIP_SECONDS = 5 * 3600
"""主动搭话 source 衰减历史的硬窗口（p_skip=1.0）。
- 用途：5h 内同一 URL 必跳，超过后按 kind 半衰期指数衰减。
- 上游：system_router._should_skip_source。"""

PROACTIVE_SOURCE_HALF_LIFE_BY_KIND: dict[str, float] = {
    'web': 3 * 86400.0,
    'image': 3 * 86400.0,
    'music': 1 * 86400.0,
}
"""硬窗口外按 kind 各自的 p_skip 半衰期（秒）。
- web/image：3d（新闻 / 表情包重复成本相对低，慢慢复活）
- music：1d（曲库小，更频繁轮转）
- 用途：system_router._half_life_for 查表。"""

PROACTIVE_SOURCE_HALF_LIFE_DEFAULT = 3 * 86400.0
"""未在 _BY_KIND 命中时的兜底半衰期。"""

PROACTIVE_SOURCE_FORGET_P = 0.05
"""p_skip 跌破此阈值即从衰减历史中遗忘（让文件体积自然有界）。
- 当前参数下：music ≈ 4.5d 后遗忘，web/image ≈ 13d 后遗忘。"""

EMOTION_ANALYSIS_MAX_TOKENS = 40
"""情感分析 LLM 的 max_completion_tokens。
- 用途：返回情感标签 + score 等短输出。
- 上游：LLM 输出（注意：Gemini 可能返回 markdown 包裹，留 40 token 余量）。"""
