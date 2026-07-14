"""战雷 HUD 事件（击杀/受损）解析与战绩统计。

把 /hudmsg 的 damage 文本解析成结构化击杀事件，并累积本局战绩(K/D)。

数据特点（来自实测，中文客户端）：
    "-RINKO- tl0sr2 (歼-15T) 击毁了 AI米格-15bis"
    - 文本语言随游戏客户端（这里是中文），故动作词需中英文都支持。
    - 字符间插有零宽字符(\\u200b 等，战雷反爬)，解析前必须清洗。
    - 格式：<[战队] 玩家名> (载具) <动作> <被击杀者[ (载具)]>
    - AI 单位名以 "AI" 开头且通常无载具括号。

注意：localhost API 不直接告诉你“谁是自己”。可在创建 KillTracker 时传入 player_name
（玩家名，不含战队标签）来统计“我的”战绩；不传则只产出全局击杀榜，由前端自行高亮。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

# 需清洗的字符：
#   - 零宽/不可见空白（战雷反爬会在每个字之间插零宽字符）
#   - 控制图符区 U+2400–U+243F（游戏在载具名前插的占位字形，如 “␗三式战一型”）
_INVISIBLE = dict.fromkeys(
    list(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00a0"))
    + list(range(0x2400, 0x2440)),
    None,
)


def clean_text(s: str | None) -> str:
    """去除零宽字符并压缩空白。"""
    if not s:
        return ""
    text = s.translate(_INVISIBLE)
    return re.sub(r"\s+", " ", text).strip()


# 系统/会话噪声：玩家掉线、连接、加入等，不是战斗事件，需从击杀流剔除，
# 否则会以 parsed=False 的 "other" 形式污染 combat.feed / by_action / total_events。
# 实测两种掉线措辞：
#   "[PPFW] 404ErrorPage 已掉线。"
#   "404ErrorPagetd! kd?NET_PLAYER_DISCONNECT_FROM_GAME"
_SYSTEM_NOISE_MARKERS = (
    "掉线", "disconnect", "net_player", "net_team",
    "已连接", "connected", "已加入", "joined the game",
    # 战斗播报/嘉奖（无施动者→受动者结构，非击杀事件）：
    #   先拔头筹=First Blood；获得嘉奖"双杀!/三杀!/连续无伤歼敌×3!/同仇敌忾"等
    "先拔头筹", "first blood", "获得嘉奖", "earned the award", "earned an award",
)


def is_system_noise(text: str | None) -> bool:
    """是否为系统/会话噪声（掉线/连接等），应从击杀流忽略。"""
    raw = clean_text(text).lower()
    if not raw:
        return False
    return any(m in raw for m in _SYSTEM_NOISE_MARKERS)


# 双方事件：<击杀者> 动作 <被击杀者>。短语按出现概率排列，先匹配到先用。
# 注意区分两类 damage 流文本（均 kind=='damage'，实测同一局并存）：
#   ① 击杀榜（权威致命事件）：击落了 / 击毁了 / 炸毁了 / 已坠毁 / 已被摧毁 —— 计入 K/D。
#   ② 伤害日志（细粒度，常打在 AI bot 上）：致命攻击 / 重创 / 引燃 / 击伤 —— **不计 K/D**，
#      否则会与击杀榜对同一次击杀重复计数。其中“致命攻击”虽语义为致命一击，仍按非致命处理，
#      真正的击杀以击杀榜动词为准（is_kill=False）。
_DUAL_ACTIONS: list[tuple[tuple[str, ...], str]] = [
    (("击落了", "shot down", "gunned down"), "shot_down"),
    (("击毁了", "摧毁了", "destroyed"), "destroyed"),
    (("炸毁了", "炸沉了"), "destroyed"),
    # —— 以下为伤害日志动词，非致命（不进 _FATAL_ACTIONS）——
    (("致命攻击", "dealt the final blow", "lethal hit"), "lethal_hit"),
    (("重创", "严重损坏了", "严重损毁了", "severely damaged"), "severely_damaged"),
    # 实测整句为 "X 的攻击引燃了 Y"，须用完整连接短语切分，否则 "的攻击"/"了" 会残留进双方名。
    (("的攻击引燃了", "引燃了", "点燃了", "set afire", "set on fire"), "set_afire"),
    (("击伤了", "击伤", "damaged"), "damaged"),
]

# 单方事件：<对象> 动作（无施动者，如坠毁/坠机）。
# 中文客户端实测为 "<名> (载具) 已坠毁。"——动作词前夹了 "已"，故必须把
# "已坠毁" 整体作为短语优先匹配，否则只匹配 "坠毁" 会把 "已" 残留进对象名、
# 并破坏载具括号的提取（实测得到 "tl0sr2 (三式战一型) 已"）。长短语排前面先匹配。
_SOLO_ACTIONS: list[tuple[tuple[str, ...], str]] = [
    (("已坠毁", "坠毁", "has crashed", "crashed"), "crashed"),
    # 地面/海军载具被摧毁的单方措辞（无施动者）：实测 "<名> (PGZ09) 已被摧毁"。
    # 与双方动作 "摧毁了" 不冲突（前者含 "被摧毁"，后者含 "摧毁了"）。算阵亡。
    (("已被摧毁", "被摧毁", "已被击毁", "被击毁", "has been destroyed"), "destroyed"),
    (("已失控", "失控", "has been wrecked", "wrecked"), "wrecked"),
]

_ACTION_LABEL = {
    "shot_down": "击落",
    "destroyed": "击毁",
    "lethal_hit": "致命一击",
    "set_afire": "点燃",
    "severely_damaged": "重创",
    "damaged": "击伤",
    "crashed": "坠毁",
    "wrecked": "损毁",
    "other": "事件",
}


@dataclass
class KillEvent:
    """一条结构化击杀/受损事件。"""

    id: int = 0
    time: int | None = None
    action: str = "other"          # 归一化动作代码
    action_label: str = "事件"      # 中文动作标签
    killer: str = ""               # 施动者玩家名（不含战队/载具）
    killer_squad: str = ""
    killer_vehicle: str = ""
    killer_is_ai: bool = False
    victim: str = ""
    victim_squad: str = ""
    victim_vehicle: str = ""
    victim_is_ai: bool = False
    is_kill: bool = False          # 是否致命(击落/击毁/坠毁/损毁)
    parsed: bool = True            # 是否成功解析
    raw: str = ""                  # 清洗后的原文

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FATAL_ACTIONS = {"shot_down", "destroyed", "crashed", "wrecked"}


def _split_trailing_vehicle(part: str) -> tuple[str, str]:
    """从参与者文本末尾剥离 (载具)，返回 (剩余, 载具)。

    载具名本身可能含括号变体，如 `台风 (AESA)` / `◄EF-2000 (AESA)`，故不能用
    非贪婪正则（会只截到内层 `AESA`）。改为从右向左做括号配平，取最外层那对括号。
    """
    part = part.rstrip()
    if not part.endswith(")"):
        return part.strip(), ""
    depth = 0
    i = len(part) - 1
    while i >= 0:
        c = part[i]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    if i < 0:  # 括号不配平，退回原样
        return part.strip(), ""
    vehicle = part[i + 1:-1].strip()
    name = part[:i].strip()
    return name, vehicle


def _parse_actor(part: str) -> tuple[str, str, str, bool]:
    """解析一个参与者，返回 (玩家名, 战队, 载具, 是否AI)。"""
    part = part.strip()
    part, vehicle = _split_trailing_vehicle(part)

    is_ai = part.startswith("AI")
    if is_ai:
        part = part[2:].strip()

    squad = ""
    # 战队标签：-TAG- name 或 [TAG] name
    m2 = re.match(r"^[\-\[]([^\-\]]{1,12})[\-\]]\s+(.+)$", part)
    if m2:
        squad = m2.group(1).strip()
        name = m2.group(2).strip()
    else:
        name = part

    # AI 通常名字即载具
    if is_ai and not vehicle:
        vehicle = name
    return name, squad, vehicle, is_ai


def parse_event(text: str, event_id: int = 0, time: int | None = None) -> KillEvent:
    """把一条 hudmsg 文本解析成 KillEvent；解析失败时 parsed=False 但保留原文。"""
    raw = clean_text(text)

    for phrases, norm in _DUAL_ACTIONS:
        for ph in phrases:
            idx = raw.find(ph)
            if idx == -1:
                continue
            killer_part = raw[:idx].strip()
            victim_part = raw[idx + len(ph):].strip()
            if not killer_part or not victim_part:
                continue
            k_name, k_squad, k_veh, k_ai = _parse_actor(killer_part)
            v_name, v_squad, v_veh, v_ai = _parse_actor(victim_part)
            return KillEvent(
                id=event_id, time=time, action=norm,
                action_label=_ACTION_LABEL.get(norm, "事件"),
                killer=k_name, killer_squad=k_squad,
                killer_vehicle=k_veh, killer_is_ai=k_ai,
                victim=v_name, victim_squad=v_squad,
                victim_vehicle=v_veh, victim_is_ai=v_ai,
                is_kill=norm in _FATAL_ACTIONS, parsed=True, raw=raw,
            )

    for phrases, norm in _SOLO_ACTIONS:
        for ph in phrases:
            idx = raw.find(ph)
            if idx == -1:
                continue
            victim_part = raw[:idx].strip()
            v_name, v_squad, v_veh, v_ai = _parse_actor(victim_part)
            return KillEvent(
                id=event_id, time=time, action=norm,
                action_label=_ACTION_LABEL.get(norm, "事件"),
                victim=v_name, victim_squad=v_squad,
                victim_vehicle=v_veh, victim_is_ai=v_ai,
                is_kill=norm in _FATAL_ACTIONS, parsed=True, raw=raw,
            )

    return KillEvent(id=event_id, time=time, parsed=False, raw=raw)


# 自动识别“自己”的置信度下限：低于此值则不把自动猜测用于战绩统计（仅作候选展示）。
# 实测真人混战（数十名玩家）里，活跃度榜首常是“死得最多”的敌方而非自己；
# 0.5 时会以 ~0.57 的低置信“自信认错人”。提到 0.6，拥挤对局宁可返回 None（交前端手动设）。
_AUTO_SELF_CONFIDENCE_MIN = 0.6


class KillTracker:
    """累积本局击杀事件与战绩统计，并（在无手动昵称时）尝试自动识别“自己”。

    识别策略（见 set_player_name / resolve_self）：
        - 手动昵称（前端/启动参数传入）为**权威来源**，置信度恒为 1.0；
        - 无手动昵称时，从击杀流按“活跃度（击杀+阵亡）”给真人玩家投票，
          取最高者为候选并给出置信度——这是 **best-effort**：单人刷 AI 时几乎必中，
          但真人混战里敌方王牌也可能高活跃度，故置信度不足时不用于统计、仅作候选展示。
        - 游戏不在遥测里暴露玩家昵称、也无可靠“阵亡”信号（实测 indicators.valid 全程为真），
          因此无法做到 100% 自动；这也是保留手动昵称接口的原因。
    """

    def __init__(self, player_name: str | None = None, feed_size: int = 100) -> None:
        # 手动昵称：权威来源，跨换局保留（玩家昵称不会因换局而变）
        self._manual_name = (player_name or "").strip()
        self._feed: deque[KillEvent] = deque(maxlen=feed_size)
        self._seen_ids: set[int] = set()
        self.reset()

    def reset(self) -> None:
        """清空本局统计（换局时调用）；手动昵称不在此清除。"""
        self._feed.clear()
        self._seen_ids.clear()
        self._players: dict[str, dict[str, int]] = {}
        self._vehicles: dict[str, dict[str, int]] = {}  # name -> {本地化载具名: 次数}
        self._by_action: dict[str, int] = {}

    def set_player_name(self, name: str | None) -> None:
        """设置/清除手动昵称（权威）。传 None/空串则回退自动识别。"""
        self._manual_name = (name or "").strip()

    def feed(self, hud_messages: list[Any]) -> list[KillEvent]:
        """喂入新的 HudMessage（kind=='damage' 的会被解析），返回本次新增的事件。"""
        added: list[KillEvent] = []
        for hm in hud_messages:
            kind = getattr(hm, "kind", None)
            if kind != "damage":
                continue
            eid = int(getattr(hm, "id", 0) or 0)
            if eid in self._seen_ids:
                continue
            msg = getattr(hm, "msg", "")
            # 自机技术通知（油温/襟翼/过热等）由 NoticeTracker 处理；这里跳过，
            # 否则像“襟翼非对称…可能导致失控”会被 _SOLO_ACTIONS(失控) 误判成击杀事件、
            # 污染击杀流与排行榜。不计入 _seen_ids，交给 NoticeTracker 自行去重。
            if parse_notice(msg) is not None:
                continue
            # 战斗嘉奖（先拔头筹/双杀/完成了最后一击 等）由 AwardTracker 独立处理；这里跳过，
            # 否则像“X 完成了最后一击！”既非击杀也非已知动作，会以 action='other'(parsed=False)
            # 漏进击杀流、污染 by_action。嘉奖绝不计入 K/D。不记 _seen_ids，交给 AwardTracker 去重。
            if parse_award(msg) is not None:
                continue
            # 系统噪声（掉线/连接/加入）不是战斗事件，直接忽略，不计入战绩与 feed。
            # 不记 _seen_ids：成本极低，重复投递时再次跳过即可。
            if is_system_noise(msg):
                continue
            self._seen_ids.add(eid)
            ev = parse_event(msg, eid, getattr(hm, "time", None))
            self._feed.append(ev)
            self._accumulate(ev)
            added.append(ev)
        return added

    @staticmethod
    def _is_player(name: str, squad: str, vehicle: str, is_ai: bool) -> bool:
        """是否为真人玩家（计入战绩/自我识别）。

        真人在击杀流里必带 `(载具)` 括号（或带战队标签）；AI 空军以 "AI" 开头；
        AI 地面单位（卡车/防空车等）多为裸名、无括号无战队（实测 "M19A1"/"史蒂倍克 US6"），
        故无载具且无战队者一律视为非真人，避免把 AI 地面目标当玩家计入榜单。
        """
        return bool(name) and not is_ai and (bool(vehicle) or bool(squad))

    def _accumulate(self, ev: KillEvent) -> None:
        self._by_action[ev.action] = self._by_action.get(ev.action, 0) + 1
        if ev.is_kill and self._is_player(ev.killer, ev.killer_squad, ev.killer_vehicle, ev.killer_is_ai):
            self._players.setdefault(ev.killer, {"kills": 0, "deaths": 0})["kills"] += 1
            self._note_vehicle(ev.killer, ev.killer_vehicle)
        if ev.is_kill and self._is_player(ev.victim, ev.victim_squad, ev.victim_vehicle, ev.victim_is_ai):
            self._players.setdefault(ev.victim, {"kills": 0, "deaths": 0})["deaths"] += 1
            self._note_vehicle(ev.victim, ev.victim_vehicle)

    def _note_vehicle(self, name: str, vehicle: str) -> None:
        if not vehicle:
            return
        vm = self._vehicles.setdefault(name, {})
        vm[vehicle] = vm.get(vehicle, 0) + 1

    def _candidates(self) -> list[dict[str, Any]]:
        """真人玩家按活跃度（击杀+阵亡）降序的候选列表，含其最常用载具。"""
        out: list[dict[str, Any]] = []
        for name, s in self._players.items():
            score = s["kills"] + s["deaths"]
            if score <= 0:
                continue
            vmap = self._vehicles.get(name)
            vehicle = max(vmap.items(), key=lambda kv: kv[1])[0] if vmap else None
            out.append({
                "name": name, "kills": s["kills"], "deaths": s["deaths"],
                "score": score, "vehicle": vehicle,
            })
        out.sort(key=lambda c: (c["score"], c["kills"]), reverse=True)
        return out

    def resolve_self(self, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """解析“谁是自己”。返回 {name, source, confidence}。

        source: "manual"（手动昵称，权威）/ "auto"（自动投票，best-effort）。
        name 为 None 表示尚无法可靠确定（自动且置信度不足或无任何真人事件）。
        注：敌我识别不在此做——只暴露活跃玩家列表(active_players)由前端取用。
        """
        if self._manual_name:
            return {"name": self._manual_name, "source": "manual", "confidence": 1.0}
        cands = candidates if candidates is not None else self._candidates()
        if not cands:
            return {"name": None, "source": "auto", "confidence": 0.0}
        leader = cands[0]
        second = cands[1]["score"] if len(cands) > 1 else 0
        # 置信度：领先者占比，叠加 +1 平滑；自动识别永不宣称 100%（上限 0.85）
        conf = round(min(leader["score"] / (leader["score"] + second + 1), 0.85), 3)
        name = leader["name"] if conf >= _AUTO_SELF_CONFIDENCE_MIN else None
        return {"name": name, "source": "auto", "confidence": conf}

    def get_summary(self) -> dict[str, Any]:
        """返回战绩快照：活跃玩家列表 + 自我识别 + 击杀流(带涉我标记) + 动作计数 + 我的K/D。"""
        # 高活跃度玩家（按 击杀+阵亡 降序，含最常用载具）——直接上报前端，不区分敌我
        active_players = self._candidates()
        leaderboard = sorted(
            ({"name": n, **s} for n, s in self._players.items()),
            key=lambda x: (x["kills"], -x["deaths"]),
            reverse=True,
        )
        ident = self.resolve_self(active_players)
        me = ident["name"]
        my = dict(self._players.get(me, {"kills": 0, "deaths": 0})) if me else None

        feed: list[dict[str, Any]] = []
        for e in reversed(self._feed):
            d = e.to_dict()
            d["involves_me"] = bool(me) and me in (e.killer, e.victim)
            d["is_my_kill"] = bool(me) and e.killer == me and e.is_kill
            d["is_my_death"] = bool(me) and e.victim == me and e.is_kill
            feed.append(d)

        return {
            "player_name": me,
            "self": ident,
            "active_players": active_players[:30],
            "total_events": len(self._seen_ids),
            "by_action": dict(self._by_action),
            "leaderboard": leaderboard[:20],
            "my": my,
            "feed": feed,
        }


# ---------------------------------------------------------------------------
# 自机技术通知（HUD 文本里关于“自己载具”的状态警告，无施动者结构）
# ---------------------------------------------------------------------------
#
# 这类消息同样出现在 /hudmsg 的 damage（偶尔 events）流里，但不是
# “击杀者→被击杀者”结构，而是描述自己载具状态的技术警告，例如油温过高、
# 左右襟翼非对称展开、发动机过热等。它们天然只关于“自己”——游戏不会告诉你
# 敌人的油温/襟翼，因此不会与击杀流的双方动词冲突。
#
# 游戏文本随客户端语言变化、措辞各版本略有差异，故采用“关键词组”宽松匹配：
# 一条消息只要命中任一 keyword_set（该组内关键词全部为子串，大小写不敏感）
# 即归类成功。关键词表为数据驱动，便于按真机抓包结果增补 / 校准。


@dataclass(frozen=True)
class _NoticeRule:
    """一条自机通知的匹配规则。"""

    code: str          # 机器可读代码
    level: str         # info / warning / critical
    label: str         # 中文标签
    # OR(每个 keyword_set) of AND(组内每个关键词)
    keyword_sets: tuple[tuple[str, ...], ...]


# 按出现概率排列；同一条消息只取第一个命中的规则。
_SELF_NOTICES: list[_NoticeRule] = [
    _NoticeRule(
        "oil_overheat", "warning", "油温过高",
        (("油温", "过高"), ("油温", "过热"), ("机油", "过热"),
         ("oil", "overheat"), ("oil", "too hot")),
    ),
    _NoticeRule(
        "flap_asymmetric", "warning", "襟翼非对称展开",
        (("襟翼", "非对称"), ("襟翼", "不对称"), ("襟翼", "不一致"),
         ("flap", "asymmetr")),
    ),
    _NoticeRule(
        "engine_overheat", "warning", "发动机过热",
        (("发动机", "过热"), ("水温", "过高"),
         ("engine", "overheat"), ("water", "overheat")),
    ),
    _NoticeRule(
        # 实测文本：“动力系统故障：螺旋桨损坏”（发动机/螺旋桨被打坏 -> 失去动力，濒危）
        "powertrain_failure", "critical", "动力系统故障",
        (("动力系统故障",), ("螺旋桨损坏",), ("发动机损坏",),
         ("engine", "disabled"), ("propeller", "broken")),
    ),
]


@dataclass
class HudNotice:
    """一条结构化的自机技术通知。"""

    id: int = 0
    time: int | None = None
    code: str = "other"
    level: str = "info"      # info / warning / critical
    label: str = ""          # 中文标签
    message: str = ""        # 清洗后的原文
    raw: str = ""            # 清洗后的原文（同 message，保留以便对照）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_notice(text: str, event_id: int = 0, time: int | None = None) -> HudNotice | None:
    """把一条 HUD 文本尝试解析成自机技术通知；无任何规则命中时返回 None。"""
    raw = clean_text(text)
    if not raw:
        return None
    low = raw.lower()
    for rule in _SELF_NOTICES:
        for kset in rule.keyword_sets:
            if all(kw in low for kw in kset):
                return HudNotice(
                    id=event_id, time=time, code=rule.code,
                    level=rule.level, label=rule.label,
                    message=raw, raw=raw,
                )
    return None


class NoticeTracker:
    """从 HUD 文本流中提取自机技术通知，去重累积成可推送的事件流。"""

    def __init__(self, feed_size: int = 100) -> None:
        self._feed: deque[HudNotice] = deque(maxlen=feed_size)
        # 同一条消息可能被多次投递；按 (kind, id) 去重（event 与 damage 的 id 各自独立）
        self._seen: set[tuple[str, int]] = set()
        self.reset()

    def reset(self) -> None:
        """清空（换局时调用）。"""
        self._feed.clear()
        self._seen.clear()
        self._by_code: dict[str, int] = {}
        self._total = 0

    def feed(self, hud_messages: list[Any]) -> list[HudNotice]:
        """喂入新的 HudMessage（event/damage 都会尝试），返回本次新增的通知。"""
        added: list[HudNotice] = []
        for hm in hud_messages:
            kind = getattr(hm, "kind", "") or ""
            eid = int(getattr(hm, "id", 0) or 0)
            key = (kind, eid)
            if key in self._seen:
                continue
            self._seen.add(key)
            notice = parse_notice(getattr(hm, "msg", ""), eid, getattr(hm, "time", None))
            if notice is None:
                continue
            self._feed.append(notice)
            self._by_code[notice.code] = self._by_code.get(notice.code, 0) + 1
            self._total += 1
            added.append(notice)
        return added

    def get_summary(self) -> dict[str, Any]:
        """返回通知快照：累计数 + 各代码计数 + 通知流（最新在前）。"""
        return {
            "total": self._total,
            "by_code": dict(self._by_code),
            "feed": [n.to_dict() for n in reversed(self._feed)],
        }


# ---------------------------------------------------------------------------
# 战斗嘉奖（HUD 里对某玩家的成就/连杀播报）
# ---------------------------------------------------------------------------
#
# 实测格式（中文客户端，kind=='damage'）：
#   "<[战队] 玩家名> (载具) 获得嘉奖“双杀！”"
#   "<[战队] 玩家名> (载具) 获得嘉奖“连续无伤歼敌×3！”"
#   "<玩家名> (载具) 先拔头筹！"            ← 一血是独立句式，无“获得嘉奖”包裹
# 与击杀流无关（无施动者→受动者结构），KillTracker 已把它们当系统噪声跳过；
# 这里独立解析，按“是否罕见而重要”给 notable 标记，供前端做高光/情报推送。
# 嘉奖天然带玩家名+战队+载具，可用于识别敌方高威胁玩家（连杀王）。


@dataclass(frozen=True)
class _AwardRule:
    """嘉奖归类规则：嘉奖名命中关键词即归此类。"""

    keyword: str       # 命中关键词（子串）
    code: str          # 机器可读代码
    tier: str          # minor / notable / major
    notable: bool      # 是否“罕见而重要”（默认推送/高光）


# 按重要性/出现顺序排列；嘉奖名命中第一个关键词即归类。
# tier: major(高光级，连杀/一血) > notable(值得一提) > minor(常见，默认不推)。
# 独立句式成就（无“获得嘉奖”包裹，直接 "<玩家>(载具) XXX！"）。
_STANDALONE_AWARDS: tuple[str, ...] = ("先拔头筹", "完成了最后一击")


_AWARD_RULES: list[_AwardRule] = [
    _AwardRule("先拔头筹", "first_blood", "major", True),
    _AwardRule("最后一击", "final_blow", "major", True),  # “完成了最后一击！”绝杀
    _AwardRule("连续无伤歼敌", "killing_spree", "major", True),
    _AwardRule("五杀", "penta_kill", "major", True),
    _AwardRule("四杀", "quad_kill", "major", True),
    _AwardRule("三杀", "triple_kill", "major", True),
    _AwardRule("双杀", "double_kill", "notable", True),
    _AwardRule("多杀", "multi_kill", "major", True),
    _AwardRule("终结者", "terminator", "major", True),
    _AwardRule("战场英雄", "battle_hero", "major", True),
    _AwardRule("战斗英雄", "battle_hero", "major", True),
    _AwardRule("制空", "air_dominance", "notable", True),
    _AwardRule("空中之王", "sky_king", "major", True),
    _AwardRule("空中杀手", "air_killer", "notable", True),   # 空战多杀
    _AwardRule("战斗机救星", "fighter_savior", "notable", True),  # 击落正在攻击队友的敌机
    _AwardRule("同仇敌忾", "comrade", "minor", False),
    _AwardRule("毫发无伤", "unscathed", "minor", False),       # 无伤（常见，默认不推）
]


@dataclass
class Award:
    """一条结构化战斗嘉奖。"""

    id: int = 0
    time: int | None = None
    player: str = ""
    squad: str = ""
    vehicle: str = ""
    is_ai: bool = False
    code: str = "award_other"
    label: str = ""          # 嘉奖原始名称（如 “连续无伤歼敌×3”）
    tier: str = "minor"
    notable: bool = False
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_award(text: str, event_id: int = 0, time: int | None = None) -> Award | None:
    """把一条 HUD 文本尝试解析成战斗嘉奖；非嘉奖返回 None。"""
    raw = clean_text(text)
    if not raw:
        return None

    actor_part = ""
    name = ""
    if "获得嘉奖" in raw:
        actor_part = raw.split("获得嘉奖", 1)[0]
        tail = raw.split("获得嘉奖", 1)[1]
        # 取引号内的嘉奖名；无引号则取整段尾巴
        m = re.search(r"[“\"']\s*(.+?)\s*[”\"']", tail)
        name = (m.group(1) if m else tail).strip(" “”\"'！!：:")
    else:
        # 独立句式成就（无“获得嘉奖”包裹）：实测 先拔头筹 / 完成了最后一击
        for kw in _STANDALONE_AWARDS:
            if kw in raw:
                actor_part = raw.split(kw, 1)[0]
                name = kw
                break
        if not name:
            return None

    if not name:
        return None
    player, squad, vehicle, is_ai = _parse_actor(actor_part)
    code, tier, notable = "award_other", "minor", False
    low = name
    for rule in _AWARD_RULES:
        if rule.keyword in low:
            code, tier, notable = rule.code, rule.tier, rule.notable
            break
    return Award(
        id=event_id, time=time, player=player, squad=squad, vehicle=vehicle,
        is_ai=is_ai, code=code, label=name, tier=tier, notable=notable, raw=raw,
    )


class AwardTracker:
    """从 HUD 文本流中提取战斗嘉奖，去重累积；标记是否“我的”、是否值得高光。"""

    def __init__(self, feed_size: int = 60) -> None:
        self._feed: deque[Award] = deque(maxlen=feed_size)
        self._seen: set[tuple[str, int]] = set()
        self.reset()

    def reset(self) -> None:
        """清空（换局/进局排空时调用）。"""
        self._feed.clear()
        self._seen.clear()
        self._by_code: dict[str, int] = {}
        self._total = 0

    def feed(self, hud_messages: list[Any]) -> list[Award]:
        """喂入新的 HudMessage，返回本次新增的嘉奖。"""
        added: list[Award] = []
        for hm in hud_messages:
            kind = getattr(hm, "kind", "") or ""
            eid = int(getattr(hm, "id", 0) or 0)
            key = (kind, eid)
            if key in self._seen:
                continue
            self._seen.add(key)
            aw = parse_award(getattr(hm, "msg", ""), eid, getattr(hm, "time", None))
            if aw is None:
                continue
            self._feed.append(aw)
            self._by_code[aw.code] = self._by_code.get(aw.code, 0) + 1
            self._total += 1
            added.append(aw)
        return added

    def get_summary(self, self_name: str | None = None) -> dict[str, Any]:
        """返回嘉奖快照。

        - `feed`：全部已识别嘉奖（最新在前，含 `is_mine`）。
        - `notable`：仅“罕见而重要”的子集（一血/连杀/连续无伤歼敌等），供前端高光/情报推送。
        - `by_code` / `total`：累计统计。
        `self_name` 为当前识别出的“自己”（来自 KillTracker），用于标记 `is_mine`。
        """
        feed: list[dict[str, Any]] = []
        for a in reversed(self._feed):
            d = a.to_dict()
            d["is_mine"] = bool(self_name) and a.player == self_name
            feed.append(d)
        notable = [d for d in feed if d.get("notable")]
        return {
            "total": self._total,
            "by_code": dict(self._by_code),
            "feed": feed,
            "notable": notable,
        }
