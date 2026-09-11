"""Numeric v2 的确定性状态引擎与 Runtime 入口。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
import re
from pathlib import Path
from typing import Any, Mapping

from utils.tokenize import truncate_to_tokens

from .numeric_v2_context import pending_transition_record

from .numeric_v2 import (
    CompiledNumericV2Package,
    NumericV2Compiler,
)
from .numeric_v2_performance import (
    transition_source_dialogue_policy,
    valid_mixed_performance_policy,
    valid_ordered_content,
    valid_scene_narration,
)
from .numeric_v2_budget import (
    NUMERIC_V2_ACTOR_BUDGET_PROFILES,
    NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
)
from .numeric_v2_store import (
    NumericV2SessionStore,
    NumericV2StoredSession,
)


SESSION_SCHEMA = "neko.script.session.numeric.v2"
LEDGER_EVENT_SCHEMA = "neko.script.ledger_event.numeric.v2"
PERFORMANCE_RECORD_SCHEMA = "neko.script.performance_record.numeric.v2"
NUMERIC_V2_PLAYER_INPUT_MAX_TOKENS = 140
NUMERIC_V2_INPUT_SOURCES = frozenset({"freeform", "suggestion"})
# 当前 Session 只保存正文、数值和转场状态；旧证据链 Session 不再可恢复。
_DIALOGUE_POLICIES = frozenset({"required", "optional", "forbidden"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMPARATORS = {
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    ">": lambda left, right: left > right,
    "<": lambda left, right: left < right,
    ">=": lambda left, right: left >= right,
    "<=": lambda left, right: left <= right,
}


class NumericV2RuntimeError(ValueError):
    """Numeric v2 回合无法在当前确定性状态上结算。"""  # noqa: DOCSTRING_CJK


class NumericV2RevisionConflictError(NumericV2RuntimeError):
    """客户端基于过期 revision 提交。"""  # noqa: DOCSTRING_CJK


class NumericV2DuplicateTurnError(NumericV2RuntimeError):
    """同一个 client_turn_id 已经成功提交。"""  # noqa: DOCSTRING_CJK


def _stable_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise NumericV2RuntimeError(f"{field}_invalid")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NumericV2RuntimeError(f"{field}_invalid")
    return value


def _player_address_disclosed(message: str, configured_address: str) -> bool:
    """只接受包含完整昵称的明确自我介绍或称呼请求。"""  # noqa: DOCSTRING_CJK

    text = str(message or "").strip()
    address = str(configured_address or "").strip()
    if not text or not address or address in {"你", "男主"}:
        return False
    escaped = re.escape(address)
    left = r"(?:^|[\s,，。.!！;；:：])"
    right = r"(?=$|[\s,，。.!！;；:：])"
    quoted_address = rf"[\"'“‘「『]?{escaped}[\"'”’」』]?"
    patterns = (
        # 中文：限定为第一人称身份陈述或明确的称呼指令，排除“你认识小明吗”。
        rf"{left}(?:我(?:的名字)?(?:是|叫)|请?(?:叫|称呼)我(?:为)?|你可以叫我)\s*{quoted_address}(?:吧|就好|即可)?{right}",
        rf"{left}{quoted_address}\s*(?:就是我|是我){right}",
        # 其他已支持界面的常见自我介绍形式；昵称本身始终按完整精确字符串匹配。
        rf"{left}(?:i\s+am|i['’]m|my\s+name\s+is|call\s+me|you\s+can\s+call\s+me)\s+{quoted_address}{right}",
        rf"{left}(?:me\s+llamo|ll[aá]mame|me\s+chamo|pode\s+me\s+chamar\s+de|меня\s+зовут)\s+{quoted_address}{right}",
        rf"{left}(?:私は|僕は|俺は|名前は)\s*{quoted_address}\s*(?:です|だ){right}",
        rf"{left}{quoted_address}\s*と呼んで{right}",
        rf"{left}(?:저는|나는|제\s*이름은|내\s*이름은)\s*{quoted_address}\s*(?:입니다|예요|이에요){right}",
        rf"{left}{quoted_address}\s*(?:라고|이라고)\s*불러{right}",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _player_address_known_after_turn(
    session: "ScriptSessionV2",
    message: str,
) -> bool:
    """仅在玩家明确披露完整配置昵称后推进称呼知情状态。"""  # noqa: DOCSTRING_CJK

    if session.player_address_known:
        return True
    configured_address = str(session.catgirl_binding.get("player_address") or "").strip()
    return _player_address_disclosed(message, configured_address)


def _transition_offered(value: Mapping[str, Any]) -> bool:
    """读取上一轮 Actor 是否交付了可见的具体转场提议。"""  # noqa: DOCSTRING_CJK

    raw = value.get("transition_offered", False)
    if not isinstance(raw, bool):
        raise NumericV2RuntimeError("session_transition_offered_invalid")
    return raw


def _dialogue_policy(value: Any, *, default: str = "required") -> str:
    policy = str(value if value is not None else default)
    if policy not in _DIALOGUE_POLICIES:
        raise NumericV2RuntimeError("session_dialogue_policy_invalid")
    return policy


@dataclass(frozen=True, slots=True)
class MetricChangeV2:
    """判定模型提出、确定性引擎复验后的单项数值变化。"""  # noqa: DOCSTRING_CJK

    metric_id: str
    delta: int
    criterion: str
    evidence: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        metric_schema: Mapping[str, Any],
    ) -> "MetricChangeV2":
        if set(value) != {"metric_id", "delta", "criterion", "evidence"}:
            raise NumericV2RuntimeError("metric_change_fields_invalid")
        metric_id = _stable_id(value.get("metric_id"), "metric_id")
        if metric_id not in metric_schema:
            raise NumericV2RuntimeError("metric_change_unknown_metric")
        delta = _integer(value.get("delta"), "metric_delta")
        if delta == 0:
            raise NumericV2RuntimeError("metric_delta_zero")
        definition = metric_schema[metric_id]
        direction = "increase" if delta > 0 else "decrease"
        limit = int(definition["per_turn_limit"][direction])
        if abs(delta) > limit:
            raise NumericV2RuntimeError("metric_delta_limit_exceeded")
        criterion = str(value.get("criterion") or "").strip()
        evidence = str(value.get("evidence") or "").strip()
        if not criterion or not evidence:
            raise NumericV2RuntimeError("metric_change_reason_required")
        # 判定器只能命中作者已声明的依据，不能借自由文本扩展数值规则。
        allowed_criteria = definition[f"{direction}_criteria"]
        if criterion not in allowed_criteria:
            raise NumericV2RuntimeError("metric_change_criterion_invalid")
        return cls(metric_id, delta, criterion, evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "delta": self.delta,
            "criterion": self.criterion,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class TurnRequestV2:
    client_turn_id: str
    base_revision: int
    message: str
    # 只描述本轮 UI 输入来源，不参与数值、路线或 Session 状态机。
    input_source: str = "freeform"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TurnRequestV2":
        request = cls(
            client_turn_id=_stable_id(value.get("client_turn_id"), "client_turn_id"),
            base_revision=_integer(value.get("base_revision"), "base_revision"),
            message=str(value.get("message") or "").strip(),
            input_source=str(value.get("input_source") or "freeform").strip(),
        )
        if (
            request.base_revision < 0
            or not request.message
            or request.input_source not in NUMERIC_V2_INPUT_SOURCES
        ):
            raise NumericV2RuntimeError("numeric_turn_request_invalid")
        if truncate_to_tokens(request.message, NUMERIC_V2_PLAYER_INPUT_MAX_TOKENS) != request.message:
            raise NumericV2RuntimeError("numeric_turn_input_too_long")
        return request


@dataclass(frozen=True, slots=True)
class ScriptSessionV2:
    session_id: str
    story_package_id: str
    story_package_revision: str
    story_package_hash: str
    catgirl_binding: dict[str, str]
    current_node_id: str
    metrics: dict[str, int]
    node_turn_count: int
    revision: int
    status: str
    processed_client_turn_ids: tuple[str, ...]
    opening_performance: dict[str, Any]
    performance_history: tuple[dict[str, Any], ...]
    # 预算档位属于 Session 快照；继续演绎必须沿用原档位，重新开始才允许重选。
    actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE
    # 演绎 revision 只表示正式回合；结束与继续使用独立版本，避免延迟请求互相覆盖。
    lifecycle_revision: int = 0
    player_address_known: bool = False
    ended_reason: str | None = None
    # 显式遗忘只切断后续记忆与冷档案投影，不删除继续演绎所需的 Session 历史。
    forgotten_through_revision: int = -1
    # 发声能力是确定性 Session 状态；沉睡、失声或关机不能靠自由文本临时猜测。
    dialogue_policy: str = "required"
    # 只有 Actor 在已提交正文中明确提出具体下一步时才锁存为真。
    transition_offered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SESSION_SCHEMA,
            "session_id": self.session_id,
            "story_package_id": self.story_package_id,
            "story_package_revision": self.story_package_revision,
            "story_package_hash": self.story_package_hash,
            "catgirl_binding": deepcopy(self.catgirl_binding),
            "current_node_id": self.current_node_id,
            "metrics": dict(self.metrics),
            "node_turn_count": self.node_turn_count,
            "revision": self.revision,
            "status": self.status,
            "processed_client_turn_ids": list(self.processed_client_turn_ids),
            "opening_performance": deepcopy(self.opening_performance),
            "performance_history": deepcopy(list(self.performance_history)),
            "actor_budget_profile": self.actor_budget_profile,
            "lifecycle_revision": self.lifecycle_revision,
            "player_address_known": self.player_address_known,
            "ended_reason": self.ended_reason,
            "forgotten_through_revision": self.forgotten_through_revision,
            "dialogue_policy": self.dialogue_policy,
            "transition_offered": self.transition_offered,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScriptSessionV2":
        if value.get("schema") != SESSION_SCHEMA:
            raise NumericV2RuntimeError("numeric_session_schema_invalid")
        if any(
            key in value
            for key in (
                "scene_completion_ready",
                "scene_goal_evidence",
                "in_progress_goal_evidence",
                "continuity_goal_evidence",
                "evidence_chain_version",
            )
        ):
            # 旧证据链存档已删除；重新导入必须先由作者升级为 v2.2，而不是隐式迁移。
            raise NumericV2RuntimeError("numeric_v2_legacy_session_unsupported")
        raw_player_address_known = value.get("player_address_known", False)
        if not isinstance(raw_player_address_known, bool):
            raise NumericV2RuntimeError("session_player_address_known_invalid")
        actor_budget_profile = str(
            value.get(
                "actor_budget_profile",
                NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
            )
            or ""
        )
        if actor_budget_profile not in NUMERIC_V2_ACTOR_BUDGET_PROFILES:
            raise NumericV2RuntimeError("numeric_actor_budget_profile_invalid")
        return cls(
            session_id=_stable_id(value.get("session_id"), "session_id"),
            story_package_id=_stable_id(value.get("story_package_id"), "story_package_id"),
            story_package_revision=str(value.get("story_package_revision") or ""),
            story_package_hash=str(value.get("story_package_hash") or ""),
            catgirl_binding=deepcopy(dict(value.get("catgirl_binding") or {})),
            current_node_id=_stable_id(value.get("current_node_id"), "current_node_id"),
            metrics={str(key): _integer(item, "session_metric") for key, item in dict(value.get("metrics") or {}).items()},
            node_turn_count=_integer(value.get("node_turn_count"), "node_turn_count"),
            revision=_integer(value.get("revision"), "revision"),
            status=str(value.get("status") or ""),
            processed_client_turn_ids=tuple(str(item) for item in value.get("processed_client_turn_ids") or []),
            opening_performance=deepcopy(dict(value.get("opening_performance") or {})),
            performance_history=tuple(deepcopy(list(value.get("performance_history") or []))),
            actor_budget_profile=actor_budget_profile,
            lifecycle_revision=_integer(value.get("lifecycle_revision", 0), "lifecycle_revision"),
            player_address_known=raw_player_address_known,
            ended_reason=(str(value.get("ended_reason")) if value.get("ended_reason") is not None else None),
            forgotten_through_revision=_integer(
                value.get("forgotten_through_revision", -1),
                "forgotten_through_revision",
            ),
            dialogue_policy=_dialogue_policy(value.get("dialogue_policy")),
            transition_offered=_transition_offered(value),
        )


@dataclass(frozen=True, slots=True)
class TurnOutcomeV2:
    session: ScriptSessionV2
    ledger_event: dict[str, Any]
    metric_changes: tuple[MetricChangeV2, ...]
    route: dict[str, Any] | None
    route_status: str
    transition_contract: dict[str, Any] | None


class NumericV2Engine:
    """只在候选 Session 上应用 v2.2 的数值、作者路线和转场规则。"""  # noqa: DOCSTRING_CJK

    def __init__(self, compiled: CompiledNumericV2Package):
        # 防止调用方绕过 from_mapping/注册表，直接把旧合同编译结果注入运行时。
        meta = compiled.story.get("meta")
        if not isinstance(meta, Mapping) or meta.get("contract_version") != "v2.2":
            raise NumericV2RuntimeError("numeric_v2_upgrade_required")
        self.compiled = compiled
        self.story = compiled.story
        self.nodes = {str(node["id"]): node for node in self.story["nodes"]}
        self.metric_schema = self.story["metric_schema"]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NumericV2Engine":
        compiler = NumericV2Compiler()
        meta = value.get("meta")
        # 运行时只接受 v2.2；旧包必须先由作者升级，不能绕过注册表直接加载。
        contract_version = meta.get("contract_version") if isinstance(meta, Mapping) else None
        if contract_version != "v2.2":
            raise NumericV2RuntimeError("numeric_v2_upgrade_required")
        compiled = compiler.compile_v2_2(value)
        return cls(compiled)

    @property
    def story_id(self) -> str:
        return self.compiled.story_id

    @staticmethod
    def _node_dialogue_policy(node: Mapping[str, Any], fallback: str) -> str:
        beat = node.get("story_beat")
        contract = beat.get("acting_contract") if isinstance(beat, Mapping) else None
        value = contract.get("dialogue_policy") if isinstance(contract, Mapping) else None
        return _dialogue_policy(value, default=fallback)

    def create_session(
        self,
        *,
        session_id: str,
        catgirl_binding: Mapping[str, Any],
        opening_performance: Mapping[str, Any],
        actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
    ) -> ScriptSessionV2:
        if actor_budget_profile not in NUMERIC_V2_ACTOR_BUDGET_PROFILES:
            raise NumericV2RuntimeError("numeric_actor_budget_profile_invalid")
        return ScriptSessionV2(
            session_id=_stable_id(session_id, "session_id"),
            story_package_id=self.story_id,
            story_package_revision=str(self.story["meta"]["revision"]),
            story_package_hash=self.compiled.package_hash,
            catgirl_binding={str(key): str(item) for key, item in catgirl_binding.items()},
            current_node_id=str(self.story["start_node_id"]),
            metrics={str(key): int(item) for key, item in self.story["initial_state"]["metrics"].items()},
            node_turn_count=0,
            revision=0,
            status="active",
            processed_client_turn_ids=(),
            opening_performance=deepcopy(dict(opening_performance)),
            performance_history=(),
            actor_budget_profile=actor_budget_profile,
            player_address_known=bool(self.story["initial_state"]["player_address_known"]),
            dialogue_policy=self._node_dialogue_policy(
                self.nodes[str(self.story["start_node_id"])],
                "required",
            ),
        )

    def validate_session(self, session: ScriptSessionV2) -> None:
        if session.story_package_id != self.story_id:
            raise NumericV2RuntimeError("story_package_id_mismatch")
        if session.story_package_revision != str(self.story["meta"]["revision"]):
            raise NumericV2RuntimeError("story_package_revision_mismatch")
        if session.story_package_hash not in {
            self.compiled.package_hash,
            *self.compiled.compatible_package_hashes,
        }:
            raise NumericV2RuntimeError("story_package_hash_mismatch")
        if session.current_node_id not in self.nodes:
            raise NumericV2RuntimeError("session_current_node_missing")
        if session.status not in {"active", "ended"}:
            raise NumericV2RuntimeError("session_status_invalid")
        if session.actor_budget_profile not in NUMERIC_V2_ACTOR_BUDGET_PROFILES:
            raise NumericV2RuntimeError("numeric_actor_budget_profile_invalid")
        if set(session.metrics) != set(self.metric_schema):
            raise NumericV2RuntimeError("session_metrics_mismatch")
        for metric_id, value in session.metrics.items():
            definition = self.metric_schema[metric_id]
            if not definition["min"] <= value <= definition["max"]:
                raise NumericV2RuntimeError("session_metric_out_of_range")
        if session.node_turn_count < 0 or session.revision < 0 or session.lifecycle_revision < 0:
            raise NumericV2RuntimeError("session_counter_invalid")
        if not -1 <= session.forgotten_through_revision <= session.revision:
            raise NumericV2RuntimeError("session_forgotten_revision_invalid")
        if not isinstance(session.player_address_known, bool):
            raise NumericV2RuntimeError("session_player_address_known_invalid")
        if not isinstance(session.transition_offered, bool):
            raise NumericV2RuntimeError("session_transition_offered_invalid")
        _dialogue_policy(session.dialogue_policy)

    def resolve_turn(
        self,
        session: ScriptSessionV2,
        request: TurnRequestV2,
        changes: tuple[MetricChangeV2, ...],
        *,
        scene_complete: bool = False,
        transition_intent: str = "unclear",
        natural_ending_ready: bool = False,
    ) -> TurnOutcomeV2:
        """结算 v2.2 回合；目标、证据和完成锁存不再进入状态机。"""  # noqa: DOCSTRING_CJK

        self.validate_session(session)
        if session.status == "ended":
            raise NumericV2RuntimeError("session_already_ended")
        if request.base_revision != session.revision:
            raise NumericV2RevisionConflictError("base_revision_mismatch")
        if request.client_turn_id in session.processed_client_turn_ids:
            raise NumericV2DuplicateTurnError("duplicate_client_turn_id")
        if len({change.metric_id for change in changes}) != len(changes):
            raise NumericV2RuntimeError("metric_change_duplicate")
        if transition_intent not in {"accept", "initiate", "reject", "unclear"}:
            raise NumericV2RuntimeError("transition_intent_invalid")
        # 重新接受只能依据本次场景访问中已经公开的邀请；不新增状态，冷恢复仍从同一历史判定。
        can_accept_offer = session.transition_offered or (
            transition_intent == "accept"
            and pending_transition_record(session, include_withdrawn=True) is not None
        )
        effective_transition_intent = transition_intent
        if (
            transition_intent in {"accept", "reject"}
            and not (can_accept_offer if transition_intent == "accept" else session.transition_offered)
        ):
            effective_transition_intent = "unclear"

        before = dict(session.metrics)
        after = dict(before)
        applied: list[dict[str, Any]] = []
        for change in changes:
            definition = self.metric_schema[change.metric_id]
            next_value = max(definition["min"], min(definition["max"], after[change.metric_id] + change.delta))
            applied.append({**change.to_dict(), "before": after[change.metric_id], "after": next_value})
            after[change.metric_id] = next_value

        source = self.nodes[session.current_node_id]
        next_turn_count = session.node_turn_count + 1
        route = None
        route_status = "playing"
        if effective_transition_intent == "initiate":
            # 玩家可主动要求进入已公开的下一阶段；不伪造邀请，也不绕过本轮数值选路。
            # Evaluator 识别明确请求，正式转场复核再检查公开去向和授权；不合格正文仍不提交。
            route, route_status = self._select_route(source, after)
        elif effective_transition_intent == "accept" and can_accept_offer:
            # 活跃或明确重新接受的历史邀请共用选路条件；不能凭作者目标或模型空口 accept 换幕。
            route, route_status = self._select_route(source, after)
            if route is None:
                # 提议对应的路线当前仍不可达时，留在当前幕而不伪造 advanced。
                route_status = "transition_offered"
        elif session.transition_offered and effective_transition_intent == "unclear":
            # unclear 保留提议，下一轮只回应玩家，不重复催促。
            route_status = "transition_offered"
        elif effective_transition_intent == "reject":
            # reject 清除当前提议；Actor 可在出现新因果后重新提出。
            route_status = "playing"

        # 自然结束只放行本轮已经可收束的结局，不借用 scene_complete 自动推进普通幕。
        # 保持原路线优先级；若胜出的路线是普通幕，不跳过它另找一个结局。
        if route is None and scene_complete and natural_ending_ready is True and effective_transition_intent != "reject":
            ending_route, _ = self._select_route(source, after)
            # 判定看到的是结算前的候选结局；数值变化若改选另一出口，不能挪用前者的授权。
            preview_route, _ = self._select_route(source, before)
            if ending_route is not None and preview_route is not None and ending_route["id"] == preview_route["id"]:
                ending_target = self.nodes[str(ending_route["target_node_id"])]
                if ending_target.get("type") == "ending" or ending_target.get("terminal") is True:
                    route = ending_route

        target_node_id = session.current_node_id
        next_status = "active"
        transition = None
        if route is not None:
            target_node_id = str(route["target_node_id"])
            target = self.nodes[target_node_id]
            next_turn_count = 0
            next_status = "ended" if target.get("type") == "ending" or target.get("terminal") is True else "active"
            transition = deepcopy(dict(route["transition_contract"]))
            route_status = "advanced"

        player_address_known = _player_address_known_after_turn(session, request.message)
        dialogue_policy = session.dialogue_policy
        if route is not None:
            dialogue_policy = self._node_dialogue_policy(
                self.nodes[target_node_id],
                dialogue_policy,
            )

        revision = session.revision + 1
        next_session = replace(
            session,
            current_node_id=target_node_id,
            metrics=after,
            node_turn_count=next_turn_count,
            revision=revision,
            status=next_status,
            processed_client_turn_ids=(*session.processed_client_turn_ids, request.client_turn_id),
            player_address_known=player_address_known,
            dialogue_policy=dialogue_policy,
            # 当前回合的 Actor 正文尚未生成；接受、拒绝或正式换场都会先清除旧提议，
            # unclear 才保留它等待下一轮继续回应。
            transition_offered=(
                session.transition_offered
                if route is None and effective_transition_intent == "unclear"
                else False
            ),
        )
        event = {
            "schema": LEDGER_EVENT_SCHEMA,
            "event_id": f"event_{session.session_id}_{revision}",
            "session_id": session.session_id,
            "client_turn_id": request.client_turn_id,
            "base_revision": session.revision,
            "result_revision": revision,
            "input_text": request.message,
            "metric_changes": applied,
            "before_metrics": before,
            "after_metrics": after,
            "from_node_id": session.current_node_id,
            "to_node_id": target_node_id,
            "route_id": route.get("id") if route else None,
            "route_status": route_status,
            "scene_complete": scene_complete,
            "node_turn_count": next_turn_count,
            "status": next_status,
            "player_address_known": player_address_known,
            "before_dialogue_policy": session.dialogue_policy,
            # Evaluator 与节点覆盖先于 Actor 生效；Actor exact 交付产生的状态效果只影响下一回合。
            # 单独锁存正文生成时看到的发声合同，避免提交时用更新后的状态反向否定本轮合法对白。
            "performance_dialogue_policy": dialogue_policy,
            "dialogue_policy": dialogue_policy,
            # 正文通过校验后由本引擎的 finalize_transition_offer_state 统一锁存新提议；
            # 这里先记录 Evaluator 结算后的旧提议保留或清除结果。
            "transition_offered": (
                session.transition_offered
                if route is None and effective_transition_intent == "unclear"
                else False
            ),
            # 版本 2 表示称呼状态由“完整昵称 + 明确披露句式”确定，旧事件按已提交事实兼容重放。
            "player_address_disclosure_version": 2,
        }
        event["transition_intent"] = effective_transition_intent
        # 只记录新信号的阳性值；旧 Ledger 缺省为 false，分叉重放不会替旧历史提前结束。
        if natural_ending_ready is True:
            event["natural_ending_ready"] = True
        return TurnOutcomeV2(next_session, event, changes, route, route_status, transition)

    def finalize_transition_offer_state(
        self,
        outcome: TurnOutcomeV2,
        performance: Mapping[str, Any],
        *,
        new_offer: bool,
        invalidate_previous_offer: bool = False,
    ) -> tuple[TurnOutcomeV2, dict[str, Any]]:
        """由 Runtime 唯一锁存本轮公开提议，并同步 Session、Ledger 与正文。"""  # noqa: DOCSTRING_CJK

        if not isinstance(new_offer, bool) or not isinstance(invalidate_previous_offer, bool):
            raise NumericV2RuntimeError("transition_offered_invalid")
        # 改稿前与提交前可能各调用一次；同一候选里已经确认的撤下边界不能被后一次丢掉。
        invalidate_previous_offer = invalidate_previous_offer or outcome.ledger_event.get("transition_offer_invalidated") is True
        transition_offered = new_offer or (outcome.session.transition_offered and not invalidate_previous_offer)
        finalized_performance = {
            **performance,
            "transition_offered": transition_offered,
        }
        # 只有 Workflow 的明确复核结论能撤下旧邀请，Actor 不能注入历史边界。
        finalized_performance.pop("transition_offer_invalidated", None)
        ledger_event = {**outcome.ledger_event, "transition_offered": transition_offered}
        if invalidate_previous_offer:
            finalized_performance["transition_offer_invalidated"] = True
            ledger_event["transition_offer_invalidated"] = True
        finalized_outcome = replace(
            outcome,
            session=replace(
                outcome.session,
                transition_offered=transition_offered,
            ),
            ledger_event=ledger_event,
        )
        return finalized_outcome, finalized_performance

    def finalize_transition_performance(
        self,
        outcome: TurnOutcomeV2,
        performance: Mapping[str, Any],
        *,
        target_opening: str,
        bridge_required: bool = False,
        bridge_scene_narration: str = "",
        source_dialogue_policy: str = "required",
        target_dialogue_policy: str = "required",
    ) -> dict[str, Any]:
        """固定三段提交结构；新旁白承接历史，旧数组调用仍按原协议组装。"""  # noqa: DOCSTRING_CJK

        target_node_id = str(outcome.ledger_event["to_node_id"])
        if outcome.ledger_event["from_node_id"] == target_node_id:
            return deepcopy(dict(performance))
        result = deepcopy(dict(performance))
        segments = result.get("segments")
        authored_bridge = str(bridge_scene_narration or "").strip()
        if {
            "source_performance",
            "target_performance",
        }.issubset(result):
            # 两段旁白必须来自同一次生成；作者原文是事实约束，不再覆盖已适配历史的文本。
            # 紧凑主路径与下方三段验证共用桥段许可；目标旁白始终必须非空。
            if (
                not valid_scene_narration(
                    {"scene_narration": result.get("bridge_scene_narration")},
                    allow_empty=not bridge_required,
                )
                or not valid_scene_narration({"scene_narration": result.get("target_scene_narration")})
            ):
                raise NumericV2RuntimeError("numeric_transition_performance_invalid")
            authored_bridge = result.pop("bridge_scene_narration")
            target_opening = result.pop("target_scene_narration")
            segments = [
                {
                    "phase": "source_response",
                    "performance": str(result.pop("source_performance") or "").strip(),
                },
                {
                    "phase": "transition_bridge",
                    "scene_narration": authored_bridge,
                },
                {
                    "phase": "target_opening",
                    "performance": str(result.pop("target_performance") or "").strip(),
                },
            ]
            # 可选来源旁白仍属于第一段，同次提交；不挪到换幕后的桥段或猫娘对白。
            if "source_scene_narration" in result:
                segments[0]["scene_narration"] = result.pop("source_scene_narration")
            result["segments"] = segments
        if (
            authored_bridge
            and isinstance(segments, list)
            and len(segments) == 3
            and isinstance(segments[1], Mapping)
        ):
            # 紧凑输出在上方已选用生成旁白；此处也保留旧三段数组调用的原有组装行为。
            segments[1] = {
                "phase": "transition_bridge",
                "scene_narration": authored_bridge,
            }
        if (
            not valid_scene_narration({"scene_narration": target_opening})
            or not isinstance(segments, list)
            or len(segments) != 3
            or not all(isinstance(item, Mapping) for item in segments)
            or [item.get("phase") for item in segments]
            != ["source_response", "transition_bridge", "target_opening"]
            or set(segments[0]) not in ({"phase", "performance"}, {"phase", "performance", "scene_narration"})
            or ("scene_narration" in segments[0] and not valid_scene_narration(segments[0]))
            or set(segments[1]) != {"phase", "scene_narration"}
            or set(segments[2]) != {"phase", "performance"}
            or not valid_mixed_performance_policy(
                segments[0], source_dialogue_policy
            )
            # 合同仍有独立时间、地点或环境事实时桥段必须可见；仅同场连续且无独立事实时可为空。
            or not valid_scene_narration(segments[1], allow_empty=not bridge_required)
            or not valid_mixed_performance_policy(
                segments[2], target_dialogue_policy
            )
        ):
            raise NumericV2RuntimeError("numeric_transition_performance_invalid")
        result["segments"] = [
            deepcopy(dict(segments[0])),
            deepcopy(dict(segments[1])),
            {
                "phase": "target_opening",
                "scene_narration": target_opening.strip(),
                "performance": str(segments[2]["performance"]).strip(),
            },
        ]
        result["transition_delivered"] = True
        result["visible_node_id"] = target_node_id
        return result

    def _select_route(self, node: Mapping[str, Any], metrics: Mapping[str, int]) -> tuple[dict[str, Any] | None, str]:
        eligible = [route for route in node.get("route_gates", []) if self._conditions_match(route["conditions"], metrics)]
        if not eligible:
            return None, "conditions_blocked"
        highest = max(int(route["priority"]) for route in eligible)
        winners = [route for route in eligible if int(route["priority"]) == highest]
        if len(winners) != 1:
            raise NumericV2RuntimeError("route_priority_tie")
        return deepcopy(dict(winners[0])), "eligible"

    def preview_route(
        self,
        node_id: str,
        metrics: Mapping[str, int],
    ) -> dict[str, Any] | None:
        """只读返回按当前数值会被 Runtime 选中的路线，不改变 Session。"""  # noqa: DOCSTRING_CJK

        node = self.nodes.get(str(node_id))
        if node is None:
            raise NumericV2RuntimeError("node_not_found")
        route, _status = self._select_route(node, metrics)
        return route

    @staticmethod
    def _conditions_match(conditions: Mapping[str, Any], metrics: Mapping[str, int]) -> bool:
        mode = "any" if "any" in conditions else "all"
        rows = list(conditions.get(mode) or [])
        checks = [
            _COMPARATORS[str(row["op"])](metrics[str(row["metric"])], int(row["value"]))
            for row in rows
        ]
        return any(checks) if mode == "any" else all(checks)


class NumericV2Runtime:
    """组合 v2 Engine 和独立持久化目录。"""  # noqa: DOCSTRING_CJK

    def __init__(self, engine: NumericV2Engine, root: Path, *, write_transaction=None):
        self.engine = engine
        self.store = NumericV2SessionStore(Path(root), engine)
        if write_transaction is not None:
            self.store.write_transaction = write_transaction

    async def start_session(
        self,
        *,
        session_id: str,
        catgirl_binding: Mapping[str, Any],
        opening_performance: Mapping[str, Any],
        actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
    ) -> NumericV2StoredSession:
        session = self.engine.create_session(
            session_id=session_id,
            catgirl_binding=catgirl_binding,
            opening_performance=opening_performance,
            actor_budget_profile=actor_budget_profile,
        )
        return await self.store.create_story_session(session)

    async def restore_story_session(
        self,
        catgirl_binding: Mapping[str, Any],
    ) -> NumericV2StoredSession | None:
        stored = await self.store.restore_story_session(
            self.engine.story_id,
            str(catgirl_binding.get("character_id") or ""),
            str(catgirl_binding.get("catgirl_name") or ""),
        )
        return stored

    @asynccontextmanager
    async def story_session_guard(self):
        async with self.store.story_session_guard(self.engine.story_id):
            yield

    async def restore_story_session_unlocked(
        self,
        catgirl_binding: Mapping[str, Any],
    ) -> NumericV2StoredSession | None:
        stored = await self.store._restore_story_session_unlocked(
            self.engine.story_id,
            str(catgirl_binding.get("character_id") or ""),
            str(catgirl_binding.get("catgirl_name") or ""),
        )
        return stored

    async def replace_active_session(
        self,
        *,
        previous_session_id: str,
        session_id: str,
        catgirl_binding: Mapping[str, Any],
        opening_performance: Mapping[str, Any],
        actor_budget_profile: str = NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
    ) -> NumericV2StoredSession:
        session = self.engine.create_session(
            session_id=session_id,
            catgirl_binding=catgirl_binding,
            opening_performance=opening_performance,
            actor_budget_profile=actor_budget_profile,
        )
        stored = await self.store.replace_active(previous_session_id, session)
        return stored

    async def restore_session(self, session_id: str) -> NumericV2StoredSession | None:
        return await self.store.load(session_id)

    async def restore_session_for_lifecycle(
        self,
        session_id: str,
    ) -> NumericV2StoredSession | None:
        """只为结束旧演绎读取存档；调用方不得据此继续生成剧情。"""  # noqa: DOCSTRING_CJK

        return await self.store.load_for_lifecycle(session_id)

    async def fork_session_for_test(
        self,
        source_session_id: str,
        *,
        session_id: str,
        through_revision: int,
    ) -> NumericV2StoredSession:
        """从指定 revision 建立隔离压测分叉，不覆盖正式剧本的继续演绎槽位。"""  # noqa: DOCSTRING_CJK

        source = await self.store.load(source_session_id)
        if source is None:
            raise NumericV2RuntimeError("numeric_source_session_not_found")
        if (
            isinstance(through_revision, bool)
            or not isinstance(through_revision, int)
            or not 0 <= through_revision <= source.session.revision
        ):
            raise NumericV2RuntimeError("numeric_fork_revision_invalid")

        replay_session = self.engine.create_session(
            session_id=session_id,
            catgirl_binding=source.session.catgirl_binding,
            opening_performance=source.session.opening_performance,
            actor_budget_profile=source.session.actor_budget_profile,
        )
        replay_events: list[dict[str, Any]] = []
        for index, source_event in enumerate(source.ledger_events[:through_revision]):
            changes = tuple(
                MetricChangeV2.from_mapping(
                    {
                        key: change.get(key)
                        for key in ("metric_id", "delta", "criterion", "evidence")
                    },
                    self.engine.metric_schema,
                )
                for change in source_event.get("metric_changes") or []
                if isinstance(change, Mapping)
            )
            request = TurnRequestV2.from_mapping({
                "client_turn_id": source_event.get("client_turn_id"),
                "base_revision": replay_session.revision,
                "message": source_event.get("input_text"),
            })
            outcome = self.engine.resolve_turn(
                replay_session,
                request,
                changes,
                scene_complete=bool(source_event.get("scene_complete")),
                transition_intent=str(source_event.get("transition_intent") or "unclear"),
                natural_ending_ready=source_event.get("natural_ending_ready") is True,
            )
            source_performance = deepcopy(
                dict(source.session.performance_history[index])
            )
            replayed_event = deepcopy(outcome.ledger_event)
            if source_event.get("transition_offer_invalidated") is True:
                replayed_event["transition_offer_invalidated"] = True
            replayed_session_after_turn = outcome.session
            if "transition_offered" in source_event:
                # 分叉重放沿用原回合已经提交的提议状态；不能把 Actor 结果重新猜一遍。
                committed_transition_offered = source_event.get("transition_offered")
                if not isinstance(committed_transition_offered, bool):
                    raise NumericV2RuntimeError("session_transition_offered_invalid")
                replayed_event["transition_offered"] = committed_transition_offered
                replayed_session_after_turn = replace(
                    outcome.session,
                    transition_offered=committed_transition_offered,
                )
            if (
                source_event.get("player_address_disclosure_version") is None
                and source_event.get("player_address_known") is True
                and outcome.session.player_address_known is False
            ):
                # 旧 Ledger 曾按“昵称出现即知情”提交。来源 Session 已通过兼容审计，
                # 测试分叉必须保持该既成状态，不能用新规则悄悄改写历史。
                replayed_event.pop("player_address_disclosure_version", None)
                replayed_event["player_address_known"] = True
                replayed_session_after_turn = replace(
                    outcome.session,
                    player_address_known=True,
                )
            # 分叉只更换 Session 身份；剧情正文和每轮正式输入保持逐字一致。
            source_performance.update({
                "schema": PERFORMANCE_RECORD_SCHEMA,
                "client_turn_id": request.client_turn_id,
                "revision": outcome.session.revision,
                "input_text": request.message,
                "from_node_id": outcome.ledger_event["from_node_id"],
                "to_node_id": outcome.ledger_event["to_node_id"],
            })
            replay_session = replace(
                replayed_session_after_turn,
                performance_history=(
                    *replayed_session_after_turn.performance_history,
                    source_performance,
                ),
            )
            replay_events.append(replayed_event)

        snapshot = NumericV2StoredSession(
            replay_session,
            tuple(replay_events),
        )
        return await self.store.create_isolated_snapshot(snapshot)

    def prepare_turn(
        self,
        current: NumericV2StoredSession,
        request: TurnRequestV2,
        changes: tuple[MetricChangeV2, ...],
        *,
        scene_complete: bool = False,
        transition_intent: str = "unclear",
        natural_ending_ready: bool = False,
    ) -> TurnOutcomeV2:
        return self.engine.resolve_turn(
            current.session,
            request,
            changes,
            scene_complete=scene_complete,
            transition_intent=transition_intent,
            natural_ending_ready=natural_ending_ready,
        )

    async def commit_turn(
        self,
        outcome: TurnOutcomeV2,
        performance: Mapping[str, Any],
    ) -> NumericV2StoredSession:
        route_changed = outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]
        if route_changed:
            segments = performance.get("segments")
            new_contract = (
                isinstance(segments, list)
                and bool(segments)
                and isinstance(segments[0], Mapping)
                and "performance" in segments[0]
            )
            if (
                performance.get("transition_delivered") is not True
                or performance.get("visible_node_id") != outcome.ledger_event["to_node_id"]
                or not isinstance(segments, list)
                or [item.get("phase") for item in segments if isinstance(item, Mapping)]
                != ["source_response", "transition_bridge", "target_opening"]
                or (
                    new_contract
                    and (
                        set(segments[0]) not in ({"phase", "performance"}, {"phase", "performance", "scene_narration"})
                        or ("scene_narration" in segments[0] and not valid_scene_narration(segments[0]))
                        or set(segments[1]) != {"phase", "scene_narration"}
                        or set(segments[2]) != {"phase", "scene_narration", "performance"}
                        or not valid_mixed_performance_policy(
                            segments[0],
                            transition_source_dialogue_policy(
                                str(
                                    outcome.ledger_event.get("before_dialogue_policy")
                                    or "required"
                                )
                            ),
                        )
                        or not valid_scene_narration(segments[1], allow_empty=True)
                        or not valid_scene_narration(segments[2])
                        or not valid_mixed_performance_policy(
                            segments[2], outcome.session.dialogue_policy
                        )
                    )
                )
                or (
                    not new_contract
                    and (
                        not valid_ordered_content(segments[0], require_dialogue=True)
                        or not valid_ordered_content(segments[1], require_narration=True)
                        or not valid_ordered_content(segments[2], require_narration=True)
                    )
                )
            ):
                raise NumericV2RuntimeError("numeric_transition_performance_invalid")
        elif (
            "performance" in performance
            and (
                not valid_mixed_performance_policy(
                    performance,
                    str(
                        outcome.ledger_event.get("performance_dialogue_policy")
                        or outcome.session.dialogue_policy
                    ),
                )
                or (
                    "scene_narration" in performance
                    and not valid_scene_narration(performance)
                )
            )
        ):
            raise NumericV2RuntimeError("numeric_performance_invalid")
        new_contract = "performance" in performance or (
            route_changed
            and isinstance(performance.get("segments"), list)
            and any(
                isinstance(segment, Mapping)
                and ("performance" in segment or "scene_narration" in segment)
                for segment in performance["segments"]
            )
        )
        record = {
            "schema": PERFORMANCE_RECORD_SCHEMA,
            "client_turn_id": outcome.ledger_event["client_turn_id"],
            "revision": outcome.session.revision,
            "input_text": outcome.ledger_event["input_text"],
            "from_node_id": outcome.ledger_event["from_node_id"],
            "to_node_id": outcome.ledger_event["to_node_id"],
            **deepcopy(dict(performance)),
            # 旧记录没有该标记；版本 3 才强制混合正文合同，保证历史 Session 可恢复。
            "performance_contract_version": (
                3
                if new_contract
                else (2 if "content" in performance or route_changed else 1)
            ),
        }
        session = replace(
            outcome.session,
            performance_history=(*outcome.session.performance_history, record),
        )
        return await self.store.commit(session, outcome.ledger_event)

    async def end_session(
        self,
        session_id: str,
        *,
        base_revision: int,
        base_lifecycle_revision: int,
        reason: str,
    ) -> NumericV2StoredSession:
        return await self.store.end_session(
            session_id,
            base_revision=base_revision,
            base_lifecycle_revision=base_lifecycle_revision,
            reason=reason,
        )

    async def resume_session(
        self,
        session_id: str,
        *,
        base_revision: int,
        base_lifecycle_revision: int,
    ) -> NumericV2StoredSession:
        return await self.store.resume_session(
            session_id,
            base_revision=base_revision,
            base_lifecycle_revision=base_lifecycle_revision,
        )

    async def forget_history_through_current_revision(
        self,
        session_id: str,
    ) -> NumericV2StoredSession:
        """持久化遗忘水位，继续演绎时仍保留 Runtime 上下文。"""  # noqa: DOCSTRING_CJK

        return await self.store.forget_history_through_current_revision(session_id)


__all__ = [
    "LEDGER_EVENT_SCHEMA",
    "MetricChangeV2",
    "NumericV2DuplicateTurnError",
    "NumericV2Engine",
    "NumericV2RevisionConflictError",
    "NumericV2Runtime",
    "NumericV2RuntimeError",
    "PERFORMANCE_RECORD_SCHEMA",
    "SESSION_SCHEMA",
    "ScriptSessionV2",
    "TurnOutcomeV2",
    "TurnRequestV2",
]
