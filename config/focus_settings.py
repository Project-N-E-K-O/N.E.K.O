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

"""Master-emotion analysis and focus-mode tuning settings."""

# ── Master 情绪画像（基建）─────────────────────────────────────────────
# 用 emotion-tier 小模型即时分析「用户自己说的话」的情绪，产出二维 valence-arousal
# 瞬时读数（效价 -1~+1、唤醒 0~1）。这是一条独立基建：单一权威源，凝神（FocusScorer
# 的 emotion 信号）是第一个消费者，后续记忆/UI/主动反应可接同一个 state。绝不复用
# lanlan 头像那条 outward-emotion 管线（那是角色的脸，不是用户的情绪）。privacy-
# independent：输入是对话不是屏幕，不受隐私模式门控（同凝神，见 developer-notes 规则 6）。
MASTER_EMOTION_ENABLED = True
"""Master 情绪画像总开关（默认开）。
- 用途：开 = 每条用户消息（节流后）异步跑一次 VA 情绪分析、更新瞬时读数；关掉则
  不分析、读数恒空，凝神的 emotion 信号自动消失，退回 keyword+cadence。
- 上游：_note_user_turn 的 fire-and-forget 触发；FocusScorer.emotion 信号的可用性。"""

MASTER_EMOTION_MIN_INTERVAL_SEC = 6.0
"""两次 VA 分析的最小间隔（秒），节流防连发消息打爆 emotion tier。
- 用途：MasterEmotionTracker 内部按上次分析时间戳早退。
- 调小 = 更即时但更费 token；调大 = 更省但读数更陈。"""

MASTER_EMOTION_TIMEOUT_SEC = 8.0
"""单次 VA 分析的 emotion-tier 调用超时（秒）。
- 用途：传给 _invoke_emotion_tier 的 timeout；超时则本轮不更新读数、保留上一次。
- 注意：用的是独立的 emotion tier 模型，不是主对话模型，所以不受 Gemini Live 慢拖累。"""

MASTER_EMOTION_MAX_INPUT_CHARS = 500
"""送进 VA 分析的用户文本上限（字符），超出截断。
- 用途：情绪判断只需开头一段；截断防用户粘贴长文时把整段塞进 emotion tier
  （token / 成本 / 输入预算）。
- 上游：MasterEmotionTracker._invoke 拼 prompt 前截断。"""

MASTER_EMOTION_READING_TTL_SEC = 120.0
"""情绪读数的有效期（秒），超期视为过期、latest 返回 None。
- 用途：emotion 信号能单轮独立触发凝神，若读数无限有效，长停顿后一条中性消息
  会读到几分钟前的旧 distress 读数、误重入/维持 Focus。TTL 让陈旧读数失效，
  正常对话（turn 间隔几秒~几十秒）不受影响。
- 设 0 关闭老化。上游：MasterEmotionTracker.latest。"""

# ── Focus mode 凝神 (docs/design/focus-truename-mode.md) ───────────────
# 信号触发、用户无感的「这一轮开思考 + 换强模型」机制，兑现 90/10 产品命题
# 里的 10% 神明降临。以下全是 A/B 可调旋钮，集中在此便于灰度调参；情绪关键词
# 这类多语言词表按 i18n 规约放 config/prompts/prompts_focus.py，不在这里。
FOCUS_MODE_ENABLED = True
"""凝神总开关（默认开）。
- 用途：开 = FocusScorer 正常评分、SM 按累加电荷进入/退出 FOCUS，命中那一轮 inline
  升档开思考、proactive 路径按情节冷却；关掉则两条触发路径都退化回常规
  （proactive 仍 disable_thinking、stream_text 不升档），逐字节零行为变化。
- 历史：曾默认关「先 inert 落地」，因阈值未用真实信号分布调过、且 thinking-on 的
  端到端行为（内联推理文本在流式 content 里的泄露、各 provider 思考开销）未对真模型
  验证过。现转默认开，进入真实信号实测 + 调参阶段。详见 docs/design/focus-truename-mode.md。
- 上游：FocusScorer / SessionStateMachine 入口的早退判定。"""

# ── 累积进入模型（leaky 累加器）─────────────────────────────────────
# 进入不是「单轮分数越线」而是「逐轮累加的电荷值越线」：每轮
#   charge = charge × FOCUS_CHARGE_RETENTION + 本轮score
# charge ≥ ENTER 进入、< EXIT 退出（迟滞带）。这样零散漏出的脆弱信号能攒够进入，
# 而转中性后 charge 每轮按 retention 漏掉、自然退出（替代旧的「连续 K 轮低分」
# streak——streak 会被噪音单轮顶回而卡死，见 PR 实测）。
FOCUS_CHARGE_RETENTION = 0.5
"""电荷每轮的保留率（0~1）。
- 用途：charge = charge × 此值 + 本轮score。0.5 = 每轮留 50%、漏 50%。
- 调高（如 0.7/0.8）= 记性更长、零散信号更易累积进入、进去后更黏；
  调低（如 0.3）= 漏得快、难累积、退得利落。
- 稳态：持续每轮 score=s 时 charge → s/(1-retention)（如 retention=0.5、s=0.5 → 趋近 1.0）。
- 这是「敏感度」主旋钮。仅用于 inline（用户发声）路径。"""

# idle（proactive 主动搭话）冷却——proactive 绝不抬升 charge，只衰减；进入/维持凝神
# 只由 inline（用户自己说的话）驱动。原先分「开口/沉默」两档（开口更耗专注），现统一
# 为同一保留率：无论这一轮 proactive 有没有把话说出来，凝神都按同一速度温和降温，持续
# 长短由 proactive 触发频率主导而非单纯时间流逝。两个旋钮保留以便日后再拆，但须都 > 0、
# < 1，且 replied <= silent。
FOCUS_IDLE_SILENT_RETENTION = 0.8
"""proactive 本轮没把话说出来时的电荷保留率。
- 涵盖：action != chat（被 guard/接管挡下、内容空、[PASS]），以及 Phase 2 思考
  超时 / 流式异常导致 aborted（最终也归 action=pass）——开了思考模式却没能在限时内
  接住，同样按此档降温。
- 用途：charge = charge × 此值。0.8 = 每轮温和降温。当前与 replied 统一为 0.8，
  开口与沉默同速冷却。
- 调低 = 沉默 / 超时更快冷却。"""

FOCUS_IDLE_REPLIED_RETENTION = 0.8
"""proactive 本轮真开口了（action == chat：投递了主动搭话）时的电荷保留率。
- 用途：charge = charge × 此值。0.8 = 每开口一次温和消耗——cap=1.0(满电)起约需 6 次
  主动搭话才漏到 EXIT(0.3) 以下退出，凝神逗留更久。
- 须 <= FOCUS_IDLE_SILENT_RETENTION：开口不比沉默退得更慢。当前两档统一为 0.8。
- 上游：SM.update_focus 的 retention_override（idle 收尾按 action 选这两档之一）。"""

# 调参护栏：把两档冷却的约定变成 fail-fast 的硬校验，避免后续误配把语义反转——
# >= 1.0 会让 idle tick 不降反升（破坏「绝不抬升」），replied > silent 会让「开口」
# 比「沉默」退得更慢（快慢档颠倒）。允许两档相等（当前统一 0.8，开口/沉默同速）。
# 模块加载即校验，配错直接报错而非静默跑坏。
if not (0.0 < FOCUS_IDLE_REPLIED_RETENTION <= FOCUS_IDLE_SILENT_RETENTION < 1.0):
    raise ValueError(
        "Focus idle retentions must satisfy 0 < replied <= silent < 1 "
        f"(got replied={FOCUS_IDLE_REPLIED_RETENTION}, "
        f"silent={FOCUS_IDLE_SILENT_RETENTION})"
    )

FOCUS_CHARGE_ENTER = 0.6
"""进入凝神的电荷阈值，也是「完全激活」点。
- 用途：charge ≥ 此值 → REGULAR→FOCUS，同时前端边缘辉光在此处非线性跃升 + 起呼吸。
- 单个强信号即可单轮秒进：强 distress 情绪读数（emotion 满格 0.7、≥~0.86 时越阈）或
  满格复杂提问（question 1.0×0.6=0.6）。脆弱词单独不足以单轮进（keyword 满格 0.5 < 此阈，
  有意——词表是廉价信号），须叠加 emotion 或跨轮累积；零散信号靠 charge 累积逼近此值后进入。
  注：score 现为各信号加权和（无分母，见 FOCUS_SIGNAL_WEIGHTS）。
- charge 不再 cap 在此值——见 FOCUS_CHARGE_CAP，0.6 以上继续累积到 1.0（更亮更持久）。
- 时间衰减以此为界：charge < ENTER 衰减快（FOCUS_TIME_DECAY_PER_SEC），≥ ENTER（完全激活）
  衰减减半（FOCUS_TIME_DECAY_PER_SEC_ACTIVATED）→ 0.6 以上自然更持久。"""

FOCUS_CHARGE_CAP = 1.0
"""电荷上限（封顶）。
- 用途：charge 累积的天花板。ENTER(0.6) 是进入/完全激活点，0.6→CAP 只是「更深」——
  前端边缘辉光峰值随 charge 继续抬高直到此处封顶。
- 须 ≥ ENTER。"""

FOCUS_TIME_DECAY_PER_SEC = 0.02
"""未完全激活（charge < ENTER）时电荷的每秒时间衰减量。
- 用途：与按轮 retention 叠加的「双重衰减」之时间分量——即便没有新 turn，charge 也随
  wall-clock 真实流逝（惰性在 update_focus 计算、前端按同速率本地外推辉光）。
- 0.02/s ⇒ 从 0.6 漏到 0（如无新证据）约 30s 量级；调高 = 凉得更快。"""

FOCUS_TIME_DECAY_PER_SEC_ACTIVATED = 0.01
"""完全激活（charge ≥ ENTER）后的每秒时间衰减量（减半，更持久）。
- 用途：进入凝神后时间衰减放慢一半，「她降临后多停留一会」；charge 越高离 ENTER 越远、
  停留越久（0.6 以上更持久即源于此）。
- 地板：激活后时间衰减最多只能把 charge 降到 ENTER（0.6）为止，**绝不靠时间降到 0.6 以下**——
  退出激活必须靠一轮对话的 retention（见 _decay_charge_over_time）。0.6 以下才会被时间衰减到 0。
- 须 < FOCUS_TIME_DECAY_PER_SEC（激活后必须比激活前慢）。"""

if not (FOCUS_TIME_DECAY_PER_SEC_ACTIVATED < FOCUS_TIME_DECAY_PER_SEC):
    raise ValueError(
        "FOCUS_TIME_DECAY_PER_SEC_ACTIVATED must be < FOCUS_TIME_DECAY_PER_SEC "
        f"(got activated={FOCUS_TIME_DECAY_PER_SEC_ACTIVATED}, "
        f"base={FOCUS_TIME_DECAY_PER_SEC})"
    )

FOCUS_CHARGE_EXIT = 0.3
"""退出凝神的电荷阈值（迟滞低门，须 < ENTER）。
- 用途：FOCUS 期间 charge < 此值 → 退出。
- 转中性后从 cap 处按 retention 漏：retention=0.5/cap=ENTER(0.6) 时约 1~2 轮漏到 <0.3 退出
  （即「她降临后追一两轮才放下」）。调低 = 更黏、追更久。"""

FOCUS_HARD_CAP_TURNS = 8
"""单次凝神最多持续轮数 M（硬顶 backstop）。
- 用途：即使 charge 一直在 EXIT 以上（用户持续重话），满 M 轮也强制退出收个尾，
  防单个情节无限拖长。
- 上游：SM 的 focus_turn_count 计数器。"""

FOCUS_SIGNAL_WEIGHTS: dict[str, float] = {
    "keyword": 0.5,       # 用户消息命中脆弱情绪词（词表）
    "cadence": 0.2,       # 回复字数相对基线骤跌（仅在有 distress 证据时计入）
    "emotion": 0.7,       # master 情绪画像（主信号，**带符号**）：负效价 distress 为正、正效价 joy 为负，见 MasterEmotionTracker
    "question": 0.6,      # master 模型判定「正在问复杂客观问题（数学/逻辑/推理）」的认知加分项
}
"""FocusScorer 各信号的相对权重（仅 inline 路径——评分只看用户自己说的话）。
- 用途：scorer 对适用信号按权重**直接加权求和（不归一、无分母）** → 该轮 score（喂给
  累加器）。权重即每个信号的绝对贡献：present 信号加 weight×value 进 score，缺席信号贡献 0。
- 信号语义分两类：
  · keyword / emotion / question 是触发信号——缺席返回 None、不计入（贡献 0，不稀释别的）。
    emotion 是 keyword 词表的真模型升级（故词表权重 0.5 < 模型情绪 0.7），且**带符号**：
    负效价 distress 为正、正效价 joy 为负（neutral 返回 None）——开心会把 score/charge
    往下拉。question 是认知轴加分项（问复杂客观题——数学/逻辑/推理——也值得 thinking-on），
    与 distress 正交但并入同一 charge。
  · cadence 是行为信号——只在样本足够且**有 distress 证据**（keyword / question / emotion>0）
    时才计入（否则一句短的开心话会让 cadence 误推 focus）。无分母后它的 0.0（「字数没骤降」）
    贡献 0、等价于缺席，只有字数真的骤降才往 score 加分。
- ⚠️ 无分母 ⇒ score 不再封顶在 1.0：全信号满格 = 各权重之和（当前 0.5+0.2+0.7+0.6=2.0），
  下游由 FOCUS_CHARGE_CAP 截。调权重 = 直接调每信号绝对推力，也间接改相对 ENTER 的触发难度。
- ⚠️ 单信号能否单轮进 ENTER 取决于「权重×满格值 ≥ ENTER」：去分母后 keyword 满格仅 0.5、
  cadence 0.2，单独都越不过 ENTER(0.6)——脆弱词必须叠加 emotion 或跨轮累积才进（有意：词表
  是廉价信号）；只有 emotion（≥~0.86）与满格 question（1.0×0.6）能单信号单轮进。
- emotion 读 master 情绪画像（MasterEmotionTracker）已算好的最近 VA 读数，映射成
  distress = max(0,-valence) × (FOCUS_EMOTION_AROUSAL_FLOOR + (1-floor)×arousal)——
  负效价主导、arousal 带下限放大（见 FOCUS_EMOTION_AROUSAL_FLOOR）。**滞后一拍**
  （画像异步算，inline 拿上一轮读数）；
  MASTER_EMOTION_ENABLED 关或无读数/无 distress 时返回 None、自动退回 keyword+cadence。
- idle（proactive）路径不评分：它只用 FOCUS_IDLE_SILENT/REPLIED_RETENTION 让 charge
  衰减，绝不抬升，故不在此表里（凝神的进入/维持只由 inline 驱动）。
- 上游：各子信号各自归一化到 [0,1]。
- 设计依据：keyword/emotion 是最强的两个情绪信号故权重最高。改这里直接改触发性格，慎调。"""

FOCUS_KEYWORD_SATURATION = 3
"""脆弱情绪关键词命中数的饱和点。
- 用途：scan_vulnerability_keywords 返回的命中数 / 此值后截到 1.0 作为 keyword
  子信号——单个「累」是轻推，「撑不住 + 一个人 + 没意思」叠加才是满格。
- 上游：config/prompts/prompts_focus.scan_vulnerability_keywords 的命中计数。"""

FOCUS_EMOTION_AROUSAL_FLOOR = 0.5
"""emotion 信号里 arousal（唤醒度）作为放大器的下限。
- 映射：distress = max(0,-valence) × (floor + (1-floor) × arousal)。
- 语义：distress 由「负效价」主导触发，arousal 只在 [floor, 1] 区间缩放强度，
  不再当与门。脆弱/倾诉常是「低唤醒 + 强负效价」（默默难过、丧），旧的纯乘积
  distress = arousal × negativity 会被低 arousal 压到接近 0、漏掉这类安静型 distress；
  给 arousal 一个下限后，强负效价即使唤醒度低也能透过大部分分值。
- 取值：=0 退回旧的纯乘积（arousal 仍是与门）；=1 完全忽略 arousal、纯看 valence；
  0.5 折中——低唤醒保底过半、高唤醒满额放大。须 ∈ [0,1]。
- 上游：FocusScorer._signal_emotion。仅作用于 emotion 子信号，keyword/cadence 不受影响。"""

FOCUS_EMOTION_POSITIVE_SCALE = 0.5
"""正效价（用户开心）时 emotion 信号的「反凝神」幅度系数。
- emotion 信号现在是**带符号**的：负效价 → 正 distress（推进凝神，上限 +1）；正效价 → 负值
  （把 charge 往下拉、别打扰好心情），幅度上限为此系数。
- 映射（正效价侧）：emotion = -(positivity × m × 此系数)，其中 positivity=max(0,valence)，
  m = AROUSAL_FLOOR + (1-AROUSAL_FLOOR)×arousal（与 distress 侧同一 arousal 放大器，∈[0.5,1]）。
- 取 0.5 ⇒ 正效价侧最深为 -0.5（valence=+1、arousal=1，m=1）；valence=+1、arousal=0.3 时
  m=0.65 → emotion=-0.5×0.65=-0.325。即正效价拉力天花板只有负效价 distress 的一半。
- 须 ∈ [0,1]；=0 关闭「开心反凝神」、退回「正效价不投票（None）」。"""

FOCUS_CADENCE_BASELINE_WINDOW = 6
"""cadence 信号的基线窗口：取最近 N 条用户消息长度算中位数做基线。
- 用途：FocusScorer 内 per-session 滚动 buffer 的 maxlen。
- 上游：每条真用户消息的字符长度。"""

FOCUS_CADENCE_MIN_SAMPLES = 3
"""cadence 信号生效所需的最小样本数。
- 用途：buffer 内样本不足 N 时 cadence 子信号判为「不适用」（不进归一化），
  避免会话刚开头基线不稳就乱触发。
- 上游：滚动 buffer 当前长度。"""

FOCUS_CADENCE_DROP_RATIO = 0.4
"""cadence 满格所需的「当前长度 / 基线中位数」下跌比。
- 用途：当前消息长度 ≤ 此比 × 基线中位数 → cadence 子信号 = 1.0；≥ 基线 → 0.0；
  中间线性。例：基线 30 字、ratio 0.4，则 ≤12 字（「嗯。」「知道了。」）算满格。
- 上游：当前消息长度与基线中位数之比。"""

# NOTE: silence / open_thread 信号已移除——idle（proactive）路径不再评分，改为只用
# FOCUS_IDLE_SILENT/REPLIED_RETENTION 让 charge 衰减（凝神进入/维持只由 inline 驱动）。
# 故 FOCUS_SILENCE_MIN_SECONDS / FOCUS_SILENCE_FULL_SECONDS 一并退役，避免死配置。

# NOTE: FOCUS_IDLE_THRESHOLD_MULTIPLIER（凝神态下调低 idle 触发阈值「她降临一次后
# 主动追一两轮」）属 Path B 的 idle-threshold-drop 子特性，该特性尚未接线，故旋钮
# 暂不引入，待实现该 feature 时再随它一起加，避免留下死配置。设计见 blueprint。

# NOTE: FOCUS_EPISODE_MEMORY_ENABLED（凝神退出顺便批量整理 reflection/persona/
# facts/ban-list 的开关）同理暂不引入——FOCUS_EXIT → memory 订阅者特性尚未接线，
# 旋钮待该 PR 实现时随它加回，避免死配置。设计见 docs/design/focus-truename-mode.md。
