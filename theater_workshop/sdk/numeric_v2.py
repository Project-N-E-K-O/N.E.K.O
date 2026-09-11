"""Numeric v2 作者 DTO、metric 预设和 N.E.K.O 合同适配。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import re
from typing import Any, Mapping

from .packages import PackageGateway, PackageError as NekoV2BridgeError, PackageWarning as NekoV2Warning
from .numeric_v2_analysis import analyze_numeric_v2_story


NUMERIC_V2_SCHEMA = "neko.story.numeric.v2"
NUMERIC_V2_CONTRACT_VERSION = "v2.2"
NUMERIC_V2_MIN_PER_TURN_LIMIT = 1
NUMERIC_V2_MAX_PER_TURN_LIMIT = 5
# 短篇剧场把每幕的硬底线和软收束点统一压到三回合，避免目标数量把演绎拖成长篇。
DEFAULT_SCENE_MIN_TURNS = 3
DEFAULT_SCENE_RECOMMENDED_TURNS = 3
DEFAULT_METRIC_MIN = 0
DEFAULT_METRIC_MAX = 50
DEFAULT_METRIC_INITIAL = 20
_METRIC_ID_RE = re.compile(r"[^a-z0-9]+")
_RELATIONSHIP_EFFECTS = {"positive", "negative", "none"}
_NON_ACTIONABLE_PLAYER_EXIT_EXACT = {"无", "无需", "不需要", "不必", "不用", "无须"}
_NON_ACTIONABLE_PLAYER_EXIT_PHRASES = (
    "仅作为观察者",
    "只是观察者",
    "无需玩家决定",
    "不需要玩家决定",
    "由环境决定",
    "自动发生",
    "被动等待",
)


@dataclass(frozen=True)
class PresetMetric:
    id: str
    name: str
    description: str
    increase_criteria: tuple[str, ...]
    decrease_criteria: tuple[str, ...]
    relationship_effect: str = "none"

    def to_draft(self) -> dict[str, Any]:
        """预设只负责起始文案，作者保存后仍可完整修改。"""

        low_label, middle_label, high_label = _PRESET_BAND_LABELS[self.id]
        # 关系数值需要跨多轮建立，默认限幅低于线索、压力等剧情数值；
        # 作者仍可在高级设置中显式修改，不把该默认值变成隐藏硬限制。
        default_limit = 3 if self.relationship_effect != "none" else 5
        return {
            "id": self.id,
            "preset": self.id,
            "name": self.name,
            "description": self.description,
            "relationship_effect": self.relationship_effect,
            "min": DEFAULT_METRIC_MIN,
            "max": DEFAULT_METRIC_MAX,
            "initial": DEFAULT_METRIC_INITIAL,
            "increase_limit": default_limit,
            "decrease_limit": default_limit,
            "increase_criteria": list(self.increase_criteria),
            "decrease_criteria": list(self.decrease_criteria),
            "visibility": "hidden",
            "bands": [
                # 中段从 20 起、最高段从 35 起，让常用分支阈值随短篇范围一起下移。
                {"min": 0, "max": 19, "label": low_label},
                {"min": 20, "max": 34, "label": middle_label},
                {"min": 35, "max": 50, "label": high_label},
            ],
        }


_PRESET_BAND_LABELS = {
    "affection": ("礼貌疏离", "愿意靠近", "彼此倾心"),
    "trust": ("保持戒备", "愿意试探", "愿意托付秘密"),
    "disgust": ("基本接纳", "明显排斥", "拒绝靠近"),
    "guard": ("放下防备", "谨慎观察", "高度戒备"),
    "intimacy": ("保持距离", "愿意分享", "允许亲密靠近"),
    "pressure": ("从容应对", "负担加重", "濒临失控"),
    "fear": ("尚能镇定", "明显不安", "强烈恐惧"),
    "courage": ("犹豫退缩", "愿意尝试", "主动承担风险"),
    "suspicion": ("基本相信", "保留怀疑", "认定存在隐瞒"),
    "clue_progress": ("线索零散", "逐渐串联", "接近真相"),
}


_PRESETS = (
    PresetMetric("affection", "好感度", "猫娘对玩家产生积极情感和亲近意愿的程度。", ("玩家真诚关心她的感受", "玩家尊重她的个人选择"), ("玩家轻视她的感受", "玩家只在需要帮助时接近她"), "positive"),
    PresetMetric("trust", "信任度", "猫娘愿意相信玩家承诺并交付真实信息的程度。", ("玩家坦诚说明目的", "玩家兑现已经作出的承诺"), ("玩家说谎或隐瞒关键目的", "玩家违背已经作出的承诺"), "positive"),
    PresetMetric("disgust", "厌恶度", "猫娘对玩家行为产生排斥和拒绝接近的程度。", ("玩家反复越过她明确表达的边界", "玩家利用她的弱点伤害她"), ("玩家停止冒犯并承担后果", "玩家持续尊重她的边界"), "negative"),
    PresetMetric("guard", "戒备度", "猫娘认为当前互动可能带来风险并保持防备的程度。", ("玩家施压或隐瞒关键动机", "环境出现新的危险信号"), ("玩家提供可验证的信息", "玩家允许她保留选择空间"), "negative"),
    PresetMetric("intimacy", "亲密度", "双方愿意分享私人经历并允许彼此靠近的程度。", ("玩家分享真实而具体的经历", "玩家接住她主动表达的脆弱"), ("玩家嘲弄她的私人经历", "玩家把亲密当作交换条件"), "positive"),
    PresetMetric("pressure", "压力值", "猫娘当前承受的外部压力和情绪负担。", ("时间限制进一步逼近", "玩家提出高风险要求"), ("玩家帮助拆解当前困难", "外部威胁得到缓解")),
    PresetMetric("fear", "恐惧度", "猫娘对即将发生的危险或失去控制的担忧程度。", ("危险得到新的证实", "玩家制造不可预测的威胁"), ("玩家提供可靠保护", "危险来源被明确排除")),
    PresetMetric("courage", "勇气值", "猫娘愿意面对风险并主动采取行动的程度。", ("玩家给予具体支持", "她获得成功处理困难的证据"), ("行动失败且后果扩大", "玩家否定她的判断能力")),
    PresetMetric("suspicion", "怀疑度", "猫娘认为玩家陈述或当前事实存在隐瞒的程度。", ("玩家前后说法矛盾", "出现与承诺不符的证据"), ("玩家给出可核验解释", "关键误会被事实澄清"), "negative"),
    PresetMetric("clue_progress", "线索进度", "玩家已经获得并正确串联关键线索的程度。", ("玩家发现新的有效证据", "玩家正确关联已有线索"), ("关键证据被证伪", "玩家沿错误假设排除有效线索")),
)


def preset_metric_catalog() -> list[dict[str, Any]]:
    return [preset.to_draft() for preset in _PRESETS]


def allocate_metric_id(name: str, existing_ids: set[str]) -> str:
    """服务端只在首次保存自定义 metric 时分配稳定 ID。"""

    ascii_hint = _METRIC_ID_RE.sub("_", name.lower()).strip("_")
    base = ascii_hint or f"metric_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"
    candidate = base
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def normalize_metric_drafts(metrics: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """应用已确认默认值并为自定义 metric 分配稳定 ID。"""

    if len(metrics) > 4:
        raise ValueError("metric_limit_exceeded")
    normalized: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for raw in metrics:
        metric = deepcopy(dict(raw))
        name = str(metric.get("name") or "").strip()
        metric_id = str(metric.get("id") or "").strip()
        if not metric_id and name:
            metric_id = allocate_metric_id(name, existing_ids)
        metric["id"] = metric_id
        metric.setdefault("preset", None)
        preset = next((item for item in _PRESETS if item.id == metric.get("preset")), None)
        metric.setdefault("relationship_effect", preset.relationship_effect if preset else "none")
        if metric["relationship_effect"] not in _RELATIONSHIP_EFFECTS:
            raise ValueError("invalid_metric_relationship_effect")
        metric.setdefault("min", DEFAULT_METRIC_MIN)
        metric.setdefault("max", DEFAULT_METRIC_MAX)
        metric.setdefault("initial", DEFAULT_METRIC_INITIAL)
        default_limit = 3 if metric["relationship_effect"] != "none" else 5
        metric.setdefault("increase_limit", default_limit)
        metric.setdefault("decrease_limit", default_limit)
        for direction in ("increase", "decrease"):
            limit = metric[f"{direction}_limit"]
            # v2.2 在作者保存阶段就拒绝越界限幅，避免到导出或运行时才暴露错误。
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not NUMERIC_V2_MIN_PER_TURN_LIMIT <= limit <= NUMERIC_V2_MAX_PER_TURN_LIMIT
            ):
                raise ValueError("v2_2_turn_limit_out_of_range")
        metric.setdefault("increase_criteria", [])
        metric.setdefault("decrease_criteria", [])
        # Numeric v2 的数值只服务剧场内部结算，不向玩家暴露。
        metric["visibility"] = "hidden"
        metric.setdefault("bands", [
            {"min": metric["min"], "max": metric["max"], "label": "当前状态"}
        ])
        existing_ids.add(metric_id)
        normalized.append(metric)
    return normalized


def metrics_to_package(metrics: list[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, int]]:
    schema: dict[str, Any] = {}
    initial: dict[str, int] = {}
    for metric in metrics:
        metric_id = str(metric["id"])
        schema[metric_id] = {
            "name": metric.get("name"),
            "description": metric.get("description"),
            "relationship_effect": metric.get("relationship_effect", "none"),
            "min": metric.get("min"),
            "max": metric.get("max"),
            "initial": metric.get("initial"),
            "visibility": "hidden",
            "per_turn_limit": {
                "increase": metric.get("increase_limit"),
                "decrease": metric.get("decrease_limit"),
            },
            "increase_criteria": list(metric.get("increase_criteria") or []),
            "decrease_criteria": list(metric.get("decrease_criteria") or []),
            "bands": deepcopy(list(metric.get("bands") or [])),
        }
        initial[metric_id] = metric.get("initial")
    return schema, initial


def scene_turn_budget(
    goals: list[Mapping[str, Any]],
    expected_turns: int | None = None,
) -> tuple[int, int]:
    """返回短篇节点固定的三回合预算；作者估算只用于诊断。"""

    # 两个参数只保留在内部计算签名中，不能把目标数量或 expected_turns
    # 投影到 Story Package；Runtime 的每幕推荐回合始终固定为 3。
    _ = goals
    _ = expected_turns
    return DEFAULT_SCENE_MIN_TURNS, DEFAULT_SCENE_RECOMMENDED_TURNS


def _is_actionable_player_exit(value: Any) -> bool:
    """判断离幕说明是否真的留下了玩家可亲自执行的动作。"""

    text = re.sub(r"\s+", "", str(value or "")).strip()
    if not text:
        return False
    if text in _NON_ACTIONABLE_PLAYER_EXIT_EXACT:
        return False
    return not any(phrase in text for phrase in _NON_ACTIONABLE_PLAYER_EXIT_PHRASES)


def acting_contract_to_package(value: Mapping[str, Any]) -> dict[str, Any]:
    """把作者状态线投影为现有 Runtime 合同；空的可确认事实不写入严格字段。"""

    contract = {
        "cognition_state": str(value.get("cognition_state") or ""),
        "memory_state": str(value.get("memory_state") or ""),
        "self_reference_mode": str(value.get("self_reference_mode") or ""),
        "persona_scope": str(value.get("persona_scope") or ""),
        "dialogue_policy": str(value.get("dialogue_policy") or ""),
        "allowed_behaviors": [
            str(item).strip()
            for item in value.get("allowed_behaviors") or []
            if str(item).strip()
        ],
        "forbidden_behaviors": [
            str(item).strip()
            for item in value.get("forbidden_behaviors") or []
            if str(item).strip()
        ],
    }
    facts = [
        str(item).strip()
        for item in value.get("assertable_self_facts") or []
        if str(item).strip()
    ]
    if facts:
        contract["assertable_self_facts"] = facts[:8]
    return contract


def character_state_to_package(value: Mapping[str, Any]) -> dict[str, Any]:
    """把作者状态线投影为 N.E.K.O 可直接读取的入幕状态，不复制演绎合同。"""

    return {
        "catgirl_state": str(value.get("catgirl_state") or "").strip(),
        "player_state": str(value.get("player_state") or "").strip(),
        "environment_state": str(value.get("environment_state") or "").strip(),
        "continuity_from_previous": [
            str(item).strip()
            for item in value.get("continuity_from_previous") or []
            if str(item).strip()
        ],
        "scene_boundaries": [
            str(item).strip()
            for item in value.get("scene_boundaries") or []
            if str(item).strip()
        ],
    }


class NumericV2Compiler:
    """NEKO_Numeric_drama 工作台使用的统一 Numeric v2 合同入口。"""

    def __init__(self, bridge: PackageGateway):
        self.bridge = bridge

    def compile(self, story: Mapping[str, Any]) -> Any:
        candidate = deepcopy(dict(story))
        metric_schema = candidate.get("metric_schema")
        if isinstance(metric_schema, dict):
            for definition in metric_schema.values():
                if isinstance(definition, dict):
                    definition["visibility"] = "hidden"
        compiled = self.bridge.compile(candidate)
        # N.E.K.O 负责合同硬错误；生成器只追加作者侧静态诊断，软预算和文本可辨识性都不阻断编译。
        author_warnings = tuple(
            NekoV2Warning(
                code=warning.code,
                path=warning.path,
                message=warning.message,
            )
            for warning in analyze_numeric_v2_story(compiled.story)
        )
        return replace(
            compiled,
            warnings=tuple(compiled.warnings) + author_warnings,
        )


__all__ = [
    "DEFAULT_METRIC_INITIAL",
    "DEFAULT_METRIC_MAX",
    "DEFAULT_METRIC_MIN",
    "DEFAULT_SCENE_MIN_TURNS",
    "DEFAULT_SCENE_RECOMMENDED_TURNS",
    "NUMERIC_V2_MAX_PER_TURN_LIMIT",
    "NUMERIC_V2_MIN_PER_TURN_LIMIT",
    "NUMERIC_V2_CONTRACT_VERSION",
    "NUMERIC_V2_SCHEMA",
    "NekoV2BridgeError",
    "NumericV2Compiler",
    "acting_contract_to_package",
    "allocate_metric_id",
    "metrics_to_package",
    "normalize_metric_drafts",
    "preset_metric_catalog",
    "scene_turn_budget",
]
