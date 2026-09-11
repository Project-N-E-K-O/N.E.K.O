"""Numeric v2 Story Package 的确定性编译边界。

本模块只负责作者包合同、规范化字节和静态可达性，不实现 Actor、Session、
Ledger 或回合数值判定。InkAI 发布前会调用这里进行第二次独立复验。
"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import string
from typing import Any, Mapping

from utils.tokenize import count_tokens


STORY_SCHEMA = "neko.story.numeric.v2"
_ID_FIRST_CHARACTERS = frozenset(string.ascii_letters + string.digits)
_ID_CHARACTERS = _ID_FIRST_CHARACTERS | frozenset("._-")
_COMPARATORS = frozenset({"==", "!=", ">", "<", ">=", "<="})
_VISIBILITIES = frozenset({"hidden"})
_NODE_TYPES = frozenset({"start", "scene", "ending"})
MAX_RECOMMENDED_TURNS = 40
# Actor 的固定输入预算是 4800 tokens；单个作者字段必须有硬上限，避免导入后才发现剧本永远无法开场。
MAX_ACTOR_PROMPT_FIELD_TOKENS = 384
_ACTING_CONTRACT_COGNITION_STATES = frozenset({"fresh_boot", "limited", "normal"})
_ACTING_CONTRACT_MEMORY_STATES = frozenset({"empty", "partial", "available"})
_ACTING_CONTRACT_SELF_REFERENCE_MODES = frozenset({"system_neutral", "persona_allowed"})
_ACTING_CONTRACT_PERSONA_SCOPES = frozenset({"style_only", "full"})
# Story beat 使用有限枚举表达关系和可选创作素材。
_RELATIONSHIP_CEILINGS = frozenset({"stranger", "guarded", "cooperative", "trusted", "intimate"})
_GOAL_OWNERS = frozenset({"catgirl", "player", "shared", "environment"})
_GOAL_EVIDENCE_MODES = frozenset({"semantic", "exact"})
# 运行时只接受新合同；旧包必须在作者侧升级后才能导入或运行。
_NUMERIC_CONTRACT_VERSIONS = frozenset({"v2.2"})
_GOAL_DELIVERY_TIMINGS = frozenset({"opening", "turn"})
_DIALOGUE_POLICIES = frozenset({"required", "optional", "forbidden"})
# v2.2 只允许有限交付类型，避免 Runtime 再从目标描述中猜“谁在什么位置完成了什么”。
_GOAL_DELIVERY_OUTPUTS = {
    "catgirl_dialogue": "performance_dialogue",
    "catgirl_action": "performance_action",
    "environment_fact": "scene_update",
    "player_action": "player_input",
    "shared_agreement": "shared",
    "semantic_state": "evaluator",
}
_GOAL_DELIVERY_OWNERS = {
    "catgirl_dialogue": frozenset({"catgirl"}),
    "catgirl_action": frozenset({"catgirl"}),
    "environment_fact": frozenset({"environment"}),
    "player_action": frozenset({"player"}),
    "shared_agreement": frozenset({"shared"}),
    "semantic_state": frozenset({"catgirl", "player", "shared"}),
}
MAX_STORY_GOALS = 8
_FORBIDDEN_LEGACY_FIELDS = frozenset(
    {"interaction_rules", "available_interaction_ids", "choices", "state_schema"}
)
_IDENTITY_SEPARATORS = frozenset("，,；;：:\n（(")
_CHARACTER_STATE_SUBJECTS = {
    "catgirl_state": "女主",
    "player_state": "男主",
    "environment_state": "环境",
}
_NEGATIVE_STATE_BOUNDARY_PREFIXES = ("不得", "禁止", "不能")

@dataclass(frozen=True)
class NumericV2Issue:
    """一次返回给作者的稳定合同问题。"""  # noqa: DOCSTRING_CJK

    code: str
    path: str
    message: str


@dataclass(frozen=True)
class NumericV2Warning:
    """不阻止包复验的静态质量提示。"""  # noqa: DOCSTRING_CJK

    code: str
    path: str
    message: str


class NumericV2CompileError(ValueError):
    """Numeric v2 包含一个或多个硬合同问题。"""  # noqa: DOCSTRING_CJK

    def __init__(self, issues: list[NumericV2Issue]):
        super().__init__("numeric_v2_compile_failed")
        self.issues = tuple(issues)


@dataclass(frozen=True)
class CompiledNumericV2Package:
    """已通过 N.E.K.O 复验的 canonical Story Package。"""  # noqa: DOCSTRING_CJK

    story: dict[str, Any]
    json_bytes: bytes
    package_hash: str
    warnings: tuple[NumericV2Warning, ...]
    compatible_package_hashes: tuple[str, ...] = ()

    @property
    def story_id(self) -> str:
        return str(self.story["meta"]["story_id"])


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _valid_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and value[0] in _ID_FIRST_CHARACTERS
        and all(character in _ID_CHARACTERS for character in value)
    )


def _identity_source_name(value: Any) -> str:
    text = str(value or "").strip()
    separator_indexes = [
        index
        for index, character in enumerate(text)
        if character in _IDENTITY_SEPARATORS
    ]
    if not separator_indexes:
        return ""
    name = text[:separator_indexes[0]].strip()
    return name if 0 < len(name) <= 24 else ""


def _condition_branches(conditions: Mapping[str, Any]) -> list[list[Mapping[str, Any]]]:
    if not isinstance(conditions, Mapping):
        return []
    mode = "any" if "any" in conditions else "all"
    rows = [row for row in conditions.get(mode) or [] if isinstance(row, Mapping)]
    return [rows] if mode == "all" else [[row] for row in rows]


def _conditions_overlap(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    metric_ranges: Mapping[str, tuple[int | None, int | None]],
) -> bool:
    """Return whether two route predicates can be true for one metric state."""

    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    for left_branch in _condition_branches(left):
        for right_branch in _condition_branches(right):
            rows = [*left_branch, *right_branch]
            by_metric: dict[str, list[Mapping[str, Any]]] = {}
            for row in rows:
                metric = str(row.get("metric") or "")
                if metric not in metric_ranges or row.get("op") not in _COMPARATORS or not _is_int(row.get("value")):
                    # Other validation reports the malformed condition; do not
                    # add a secondary overlap error for the same malformed row.
                    by_metric = {}
                    break
                by_metric.setdefault(metric, []).append(row)
            if not by_metric and rows:
                continue
            possible = True
            for metric, metric_rows in by_metric.items():
                minimum, maximum = metric_ranges[metric]
                if minimum is None or maximum is None:
                    possible = False
                    break
                candidates = {minimum, maximum}
                for row in metric_rows:
                    threshold = int(row["value"])
                    candidates.update({threshold - 1, threshold, threshold + 1})
                if not any(
                    minimum <= candidate <= maximum
                    and all(
                        {
                            "==": candidate == int(row["value"]),
                            "!=": candidate != int(row["value"]),
                            ">": candidate > int(row["value"]),
                            "<": candidate < int(row["value"]),
                            ">=": candidate >= int(row["value"]),
                            "<=": candidate <= int(row["value"]),
                        }[str(row["op"])]
                        for row in metric_rows
                    )
                    for candidate in candidates
                ):
                    possible = False
                    break
            if possible:
                return True
    return False


class _Collector:
    """集中收集问题，避免遇到第一个错误就中断作者定位。"""  # noqa: DOCSTRING_CJK

    def __init__(self) -> None:
        self.issues: list[NumericV2Issue] = []

    def add(self, code: str, path: str, message: str) -> None:
        self.issues.append(NumericV2Issue(code, path, message))

    def obj(self, value: Any, path: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            self.add("expected_object", path, "必须是对象。")
            return {}
        return dict(value)

    def array(self, value: Any, path: str) -> list[Any]:
        if not isinstance(value, list):
            self.add("expected_array", path, "必须是数组。")
            return []
        return value

    def require_text(self, value: Any, path: str) -> str:
        if not _text(value):
            self.add("required_text", path, "必须填写非空文本。")
            return ""
        text = str(value)
        if count_tokens(text) > MAX_ACTOR_PROMPT_FIELD_TOKENS:
            self.add(
                "actor_prompt_field_too_large",
                path,
                f"单个文本字段不能超过 {MAX_ACTOR_PROMPT_FIELD_TOKENS} tokens。",
            )
        return text

    def require_id(self, value: Any, path: str) -> str:
        if not _valid_id(value):
            self.add("invalid_id", path, "必须是安全且稳定的 ID。")
            return ""
        return value

    def require_int(self, value: Any, path: str) -> int | None:
        if not _is_int(value):
            self.add("expected_integer", path, "必须是整数。")
            return None
        return int(value)

    def require_text_list(self, value: Any, path: str, *, allow_empty: bool) -> list[str]:
        items = self.array(value, path)
        if not allow_empty and not items:
            self.add("required_items", path, "至少需要填写一项。")
        result: list[str] = []
        for index, item in enumerate(items):
            text = self.require_text(item, f"{path}[{index}]")
            if text:
                result.append(text)
        return result


class NumericV2Compiler:
    """只编译 Numeric v2 作者包，不提供旧协议兼容或迁移。"""  # noqa: DOCSTRING_CJK

    def compile(self, payload: Mapping[str, Any]) -> CompiledNumericV2Package:
        collector = _Collector()
        story = collector.obj(payload, "story")
        if story.get("schema") != STORY_SCHEMA:
            collector.add("invalid_schema", "schema", f"schema 必须是 {STORY_SCHEMA}。")
        for field in sorted(_FORBIDDEN_LEGACY_FIELDS.intersection(story)):
            collector.add("legacy_field_forbidden", field, "Numeric v2 不允许包含旧协议字段。")

        self._validate_meta(collector, story.get("meta"))
        self._validate_intro(collector, story.get("intro"))
        collector.obj(story.get("characters"), "characters")
        self._validate_binding(collector, story.get("catgirl_binding"))
        metric_ranges = self._validate_metrics(collector, story.get("metric_schema"), story.get("initial_state"))
        intro = story.get("intro")
        cast_names = intro if isinstance(intro, Mapping) else {}
        nodes, route_targets = self._validate_nodes(collector, story.get("nodes"), metric_ranges, cast_names=cast_names)
        ending_ids = self._validate_endings(collector, story.get("endings"))
        self._validate_graph(
            collector,
            start_node_id=story.get("start_node_id"),
            nodes=nodes,
            route_targets=route_targets,
            ending_ids=ending_ids,
        )
        if collector.issues:
            raise NumericV2CompileError(collector.issues)

        canonical_story = deepcopy(story)
        canonical_bytes = json.dumps(
            canonical_story,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        warnings = self._warnings(canonical_story)
        return CompiledNumericV2Package(
            story=canonical_story,
            json_bytes=canonical_bytes,
            package_hash=f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}",
            warnings=tuple(warnings),
            # v2.2 不再接受旧包哈希；升级后的包必须使用新的规范字节重新生成哈希。
            compatible_package_hashes=(),
        )

    def compile_v2_1(self, payload: Mapping[str, Any]) -> CompiledNumericV2Package:
        """旧版编译入口已停用；作者必须重新导出 v2.2 包。"""  # noqa: DOCSTRING_CJK

        raise NumericV2CompileError([
            NumericV2Issue(
                "numeric_v2_upgrade_required",
                "meta.contract_version",
                "旧版剧本包必须升级到 v2.2 后才能编译或运行。",
            )
        ])

    def compile_v2_2(self, payload: Mapping[str, Any]) -> CompiledNumericV2Package:
        """只接受显式声明 v2.2 的新包，并执行不依赖旧证据链的数值限幅门禁。"""  # noqa: DOCSTRING_CJK

        # 先做版本门禁，确保 CLI、注册表和直接编译入口对旧包返回同一个升级提示。
        meta = payload.get("meta") if isinstance(payload, Mapping) else None
        if not isinstance(meta, Mapping) or meta.get("contract_version") != "v2.2":
            raise NumericV2CompileError([
                NumericV2Issue(
                    "numeric_v2_upgrade_required",
                    "meta.contract_version",
                    "旧版剧本包必须升级到 v2.2 后才能编译或运行。",
                )
            ])
        compiled = self.compile(payload)
        issues: list[NumericV2Issue] = []
        # v2.2 的目标字段只作为创作素材；数值限幅与版本门禁独立校验。
        for metric_id, metric in (compiled.story.get("metric_schema") or {}).items():
            if not isinstance(metric, Mapping):
                continue
            limits = metric.get("per_turn_limit")
            if not isinstance(limits, Mapping):
                continue
            for direction in ("increase", "decrease"):
                value = limits.get(direction)
                if _is_int(value) and not 1 <= value <= 5:
                    issues.append(NumericV2Issue(
                        "v2_2_turn_limit_out_of_range",
                        f"metric_schema.{metric_id}.per_turn_limit.{direction}",
                        "v2.2 每个方向的单回合限幅必须是 1—5 的正整数。",
                    ))
        if issues:
            raise NumericV2CompileError(issues)
        return compiled

    @staticmethod
    def _validate_meta(c: _Collector, value: Any) -> None:
        meta = c.obj(value, "meta")
        c.require_id(meta.get("story_id"), "meta.story_id")
        for field in ("title", "author", "revision", "language"):
            c.require_text(meta.get(field), f"meta.{field}")
        if (
            "contract_version" in meta
            and meta.get("contract_version") not in _NUMERIC_CONTRACT_VERSIONS
        ):
            c.add(
                "invalid_numeric_contract_version",
                "meta.contract_version",
                f"演绎合同版本必须是 {', '.join(sorted(_NUMERIC_CONTRACT_VERSIONS))}。",
            )

    @staticmethod
    def _validate_intro(c: _Collector, value: Any) -> None:
        intro = c.obj(value, "intro")
        for field in ("background", "player_identity", "catgirl_identity"):
            c.require_text(intro.get(field), f"intro.{field}")
        # 真实昵称可以含逗号或超过旧首段长度；新工坊声明成对姓名，避免运行时猜截点。
        if "player_name" in intro or "catgirl_name" in intro:
            for role in ("player", "catgirl"):
                name = intro.get(f"{role}_name")
                identity = intro.get(f"{role}_identity")
                c.require_text(name, f"intro.{role}_name")
                if _text(name) and _text(identity) and not identity.startswith(name + "，"):
                    c.add("intro_identity_name_mismatch", f"intro.{role}_identity",
                          "剧情身份必须以对应的完整姓名和中文逗号开头。")
            if _text(intro.get("player_name")) and intro.get("player_name") == intro.get("catgirl_name"):
                c.add("intro_identity_names_conflict", "intro", "玩家与猫娘的作者姓名不能相同。")
            return
        for field in ("player_identity", "catgirl_identity"):
            identity = intro.get(field)
            if _text(identity) and not _identity_source_name(identity):
                c.add(
                    "intro_identity_name_segment_required",
                    f"intro.{field}",
                    "必须采用“角色名，剧情身份”格式，且角色名不超过 24 个字符。",
                )
        player_name = _identity_source_name(intro.get("player_identity"))
        catgirl_name = _identity_source_name(intro.get("catgirl_identity"))
        if player_name and player_name == catgirl_name:
            c.add(
                "intro_identity_names_conflict",
                "intro",
                "玩家与猫娘身份的首段名称不能相同。",
            )

    @staticmethod
    def _validate_binding(c: _Collector, value: Any) -> None:
        binding = c.obj(value, "catgirl_binding")
        if binding.get("source") != "runtime.current_catgirl":
            c.add("invalid_catgirl_binding", "catgirl_binding.source", "必须绑定当前猫娘配置。")
        c.require_text(binding.get("role_overlay"), "catgirl_binding.role_overlay")

    @staticmethod
    def _validate_metrics(c: _Collector, value: Any, initial_value: Any) -> dict[str, tuple[int | None, int | None]]:
        metrics = c.obj(value, "metric_schema")
        if len(metrics) > 4:
            c.add("metric_limit_exceeded", "metric_schema", "第一版最多启用四个 metric。")
        metric_ranges: dict[str, tuple[int | None, int | None]] = {}
        for metric_id, raw in metrics.items():
            path = f"metric_schema.{metric_id}"
            stable_id = c.require_id(metric_id, path)
            metric = c.obj(raw, path)
            c.require_text(metric.get("name"), f"{path}.name")
            c.require_text(metric.get("description"), f"{path}.description")
            minimum = c.require_int(metric.get("min"), f"{path}.min")
            maximum = c.require_int(metric.get("max"), f"{path}.max")
            initial = c.require_int(metric.get("initial"), f"{path}.initial")
            if stable_id:
                metric_ranges[stable_id] = (minimum, maximum)
            if minimum is not None and maximum is not None and minimum >= maximum:
                c.add("invalid_metric_range", path, "metric 的最小值必须小于最大值。")
            if None not in (minimum, maximum, initial) and not minimum <= initial <= maximum:
                c.add("metric_initial_out_of_range", f"{path}.initial", "初始值超出 metric 范围。")
            if metric.get("visibility") not in _VISIBILITIES:
                c.add("invalid_metric_visibility", f"{path}.visibility", "Numeric v2 数值只能对玩家隐藏。")
            if metric.get("relationship_effect", "none") not in {"positive", "negative", "none"}:
                c.add(
                    "invalid_metric_relationship_effect",
                    f"{path}.relationship_effect",
                    "关系演绎作用只能是 positive、negative 或 none。",
                )
            limits = c.obj(metric.get("per_turn_limit"), f"{path}.per_turn_limit")
            for direction in ("increase", "decrease"):
                limit = c.require_int(limits.get(direction), f"{path}.per_turn_limit.{direction}")
                if limit is not None and limit <= 0:
                    c.add("invalid_turn_limit", f"{path}.per_turn_limit.{direction}", "单回合限幅必须为正整数。")
            increase = c.require_text_list(metric.get("increase_criteria"), f"{path}.increase_criteria", allow_empty=False)
            decrease = c.require_text_list(metric.get("decrease_criteria"), f"{path}.decrease_criteria", allow_empty=False)
            if increase and decrease and set(increase) == set(decrease):
                c.add("metric_criteria_overlap", path, "增加依据和减少依据不能完全相同。")
            NumericV2Compiler._validate_bands(c, metric.get("bands"), path, minimum, maximum)

        initial = c.obj(initial_value, "initial_state")
        initial_metrics = c.obj(initial.get("metrics"), "initial_state.metrics")
        if not isinstance(initial.get("player_address_known"), bool):
            c.add(
                "invalid_initial_player_address_known",
                "initial_state.player_address_known",
                "初始称呼状态必须是布尔值。",
            )
        if set(initial_metrics) != set(metric_ranges):
            c.add("initial_metrics_mismatch", "initial_state.metrics", "初始状态必须精确覆盖全部 metric。")
        for metric_id, value in initial_metrics.items():
            numeric = c.require_int(value, f"initial_state.metrics.{metric_id}")
            definition = metrics.get(metric_id)
            if numeric is not None and isinstance(definition, Mapping):
                minimum = definition.get("min")
                maximum = definition.get("max")
                if _is_int(minimum) and _is_int(maximum) and not minimum <= numeric <= maximum:
                    c.add("initial_metric_out_of_range", f"initial_state.metrics.{metric_id}", "初始状态数值越界。")
                declared_initial = definition.get("initial")
                if _is_int(declared_initial) and numeric != declared_initial:
                    c.add(
                        "initial_metric_value_mismatch",
                        f"initial_state.metrics.{metric_id}",
                        "初始状态数值必须与 metric_schema 的 initial 一致。",
                    )
        return metric_ranges

    @staticmethod
    def _validate_bands(c: _Collector, value: Any, path: str, minimum: int | None, maximum: int | None) -> None:
        bands = c.array(value, f"{path}.bands")
        parsed: list[tuple[int, int]] = []
        for index, raw in enumerate(bands):
            band_path = f"{path}.bands[{index}]"
            band = c.obj(raw, band_path)
            start = c.require_int(band.get("min"), f"{band_path}.min")
            end = c.require_int(band.get("max"), f"{band_path}.max")
            c.require_text(band.get("label"), f"{band_path}.label")
            if start is not None and end is not None:
                if start > end:
                    c.add("invalid_metric_band", band_path, "区间起点不能大于终点。")
                parsed.append((start, end))
        if minimum is None or maximum is None or not parsed:
            if not parsed:
                c.add("metric_bands_required", f"{path}.bands", "bands 必须完整覆盖 metric 范围。")
            return
        parsed.sort()
        cursor = minimum
        for start, end in parsed:
            if start != cursor:
                c.add("metric_bands_not_contiguous", f"{path}.bands", "bands 存在重叠、断档或越界。")
                return
            cursor = end + 1
        if cursor != maximum + 1:
            c.add("metric_bands_not_contiguous", f"{path}.bands", "bands 必须完整覆盖 metric 范围。")

    @staticmethod
    def _validate_story_beat(
        c: _Collector, value: Any, path: str, *, cast_names: Mapping[str, Any] | None = None,
    ) -> None:
        beat = c.obj(value, path)
        summary = c.require_text(beat.get("summary"), f"{path}.summary")
        # opening_scene 是显式可见开场；缺失时仅使用当前摘要首句作为作者输入。
        opening_path = f"{path}.opening_scene" if "opening_scene" in beat else f"{path}.summary"
        opening_scene = (
            c.require_text(beat.get("opening_scene"), opening_path)
            if "opening_scene" in beat
            else summary
        )
        if "relationship_ceiling" in beat and beat.get("relationship_ceiling") not in _RELATIONSHIP_CEILINGS:
            c.add(
                "invalid_relationship_ceiling",
                f"{path}.relationship_ceiling",
                f"关系上限必须是 {', '.join(sorted(_RELATIONSHIP_CEILINGS))} 之一。",
            )
        if "goals" in beat:
            if "must_happen" in beat:
                c.add(
                    "conflicting_story_goal_contracts",
                    path,
                    "结构化 goals 与旧 must_happen 只能保留一份，避免目标事实分叉。",
                )
            NumericV2Compiler._validate_structured_goals(c, beat.get("goals"), f"{path}.goals")
        else:
            c.require_text_list(
                beat.get("must_happen"),
                f"{path}.must_happen",
                allow_empty=False,
            )
        c.require_text_list(beat.get("must_not_happen"), f"{path}.must_not_happen", allow_empty=True)
        if "opening_only_boundaries" in beat:
            opening_boundaries = c.require_text_list(
                beat.get("opening_only_boundaries"),
                f"{path}.opening_only_boundaries",
                allow_empty=True,
            )
            if len(opening_boundaries) > 4:
                c.add(
                    "too_many_opening_only_boundaries",
                    f"{path}.opening_only_boundaries",
                    "每幕最多保留四条仅公开开场生效的边界。",
                )
            for index, boundary in enumerate(opening_boundaries):
                if not boundary.startswith(_NEGATIVE_STATE_BOUNDARY_PREFIXES):
                    c.add(
                        "opening_only_boundary_polarity_invalid",
                        f"{path}.opening_only_boundaries[{index}]",
                        "开场边界必须以“不得”“禁止”或“不能”开头。",
                    )
        c.require_text(beat.get("catgirl_situation"), f"{path}.catgirl_situation")
        c.require_text(beat.get("transition_goal"), f"{path}.transition_goal")
        if "character_state" in beat:
            NumericV2Compiler._validate_character_state(
                c,
                beat.get("character_state"),
                f"{path}.character_state",
                cast_names=cast_names,
            )
        if "acting_contract" in beat:
            NumericV2Compiler._validate_acting_contract(c, beat.get("acting_contract"), f"{path}.acting_contract")

    @staticmethod
    def _validate_character_state(
        c: _Collector, value: Any, path: str, *, cast_names: Mapping[str, Any] | None = None,
    ) -> None:
        """校验作者写定的三方入幕状态；它只约束演绎，不进入 Session 数值。"""  # noqa: DOCSTRING_CJK

        state = c.obj(value, path)
        for field, subject in _CHARACTER_STATE_SUBJECTS.items():
            # 旧包保留固定主体前缀；新包用 intro 中已验证的姓名明确状态归属。
            name = (cast_names or {}).get(field.replace("_state", "_name"))
            if field != "environment_state" and _text(name):
                subject = name
            text = c.require_text(state.get(field), f"{path}.{field}")
            if text and not text.startswith(subject):
                c.add(
                    "character_state_subject_invalid",
                    f"{path}.{field}",
                    f"{field} 必须以“{subject}”开头，避免角色状态和行为职责倒置。",
                )
        continuity = c.require_text_list(
            state.get("continuity_from_previous"),
            f"{path}.continuity_from_previous",
            allow_empty=True,
        )
        if len(continuity) > 4:
            c.add(
                "too_many_character_state_continuity_items",
                f"{path}.continuity_from_previous",
                "每幕最多保留四条跨幕连续事实。",
            )
        boundaries = c.require_text_list(
            state.get("scene_boundaries"),
            f"{path}.scene_boundaries",
            allow_empty=True,
        )
        if len(boundaries) > 4:
            c.add(
                "too_many_character_state_boundaries",
                f"{path}.scene_boundaries",
                "每幕最多保留四条状态边界。",
            )
        for index, boundary in enumerate(boundaries):
            if not boundary.startswith(_NEGATIVE_STATE_BOUNDARY_PREFIXES):
                c.add(
                    "character_state_boundary_polarity_invalid",
                    f"{path}.scene_boundaries[{index}]",
                    "状态边界必须以“不得”“禁止”或“不能”开头；正向状态应写入对应角色状态。",
                )

    @staticmethod
    def _validate_structured_goals(c: _Collector, value: Any, path: str) -> None:
        """校验作者显式目标，避免 Runtime、Evaluator 和 Actor 再从描述中猜职责。"""  # noqa: DOCSTRING_CJK

        goals = c.array(value, path)
        if not goals:
            c.add("required_items", path, "至少需要填写一项目标。")
        if len(goals) > MAX_STORY_GOALS:
            c.add("too_many_story_goals", path, f"每幕最多允许 {MAX_STORY_GOALS} 项结构化目标。")
        seen_ids: set[str] = set()
        for index, raw in enumerate(goals):
            goal_path = f"{path}[{index}]"
            goal = c.obj(raw, goal_path)
            goal_id = c.require_id(goal.get("id"), f"{goal_path}.id")
            if goal_id in seen_ids:
                c.add("duplicate_story_goal_id", f"{goal_path}.id", "同一幕的目标 ID 不能重复。")
            if goal_id:
                seen_ids.add(goal_id)
            if goal.get("owner") not in _GOAL_OWNERS:
                c.add(
                    "invalid_story_goal_owner",
                    f"{goal_path}.owner",
                    f"目标主体必须是 {', '.join(sorted(_GOAL_OWNERS))} 之一。",
                )
            c.require_text(goal.get("description"), f"{goal_path}.description")
            evidence_path = f"{goal_path}.evidence"
            evidence = c.obj(goal.get("evidence"), evidence_path)
            mode = evidence.get("mode")
            if mode not in _GOAL_EVIDENCE_MODES:
                c.add(
                    "invalid_goal_evidence_mode",
                    f"{evidence_path}.mode",
                    f"证据模式必须是 {', '.join(sorted(_GOAL_EVIDENCE_MODES))} 之一。",
                )
            anchors = c.require_text_list(
                evidence.get("anchors"),
                f"{evidence_path}.anchors",
                allow_empty=True,
            )
            if len(anchors) > 8:
                c.add("too_many_goal_evidence_anchors", f"{evidence_path}.anchors", "每项目标最多允许八个精确证据锚点。")
            if mode == "exact" and not anchors:
                c.add(
                    "goal_evidence_anchors_required",
                    f"{evidence_path}.anchors",
                    "exact 证据模式至少需要一个完整字面锚点。",
                )
            if mode == "semantic" and anchors:
                c.add(
                    "semantic_goal_evidence_anchors_forbidden",
                    f"{evidence_path}.anchors",
                    "semantic 模式不读取字面锚点；需要硬核验时请改用 exact。",
                )
            if "delivery" in goal:
                NumericV2Compiler._validate_goal_delivery(
                    c,
                    goal.get("delivery"),
                    f"{goal_path}.delivery",
                    owner=str(goal.get("owner") or ""),
                    evidence_mode=str(mode or ""),
                    evidence_anchors=anchors,
                    evidence_path=evidence_path,
                )

    @staticmethod
    def _validate_goal_delivery(
        c: _Collector,
        value: Any,
        path: str,
        *,
        owner: str,
        evidence_mode: str,
        evidence_anchors: list[str],
        evidence_path: str,
    ) -> None:
        """校验 v2.1 原子交付；旧目标未声明 delivery 时继续走兼容投影。"""  # noqa: DOCSTRING_CJK

        delivery = c.obj(value, path)
        delivery_type = delivery.get("type")
        if delivery_type not in _GOAL_DELIVERY_OUTPUTS:
            c.add(
                "invalid_goal_delivery_type",
                f"{path}.type",
                f"交付类型必须是 {', '.join(sorted(_GOAL_DELIVERY_OUTPUTS))} 之一。",
            )
            return
        allowed_owners = _GOAL_DELIVERY_OWNERS[delivery_type]
        if owner not in allowed_owners:
            c.add(
                "goal_delivery_owner_mismatch",
                f"{path}.type",
                "交付类型与目标 owner 不一致，不能把角色或环境职责交给另一方。",
            )
        expected_output = _GOAL_DELIVERY_OUTPUTS[delivery_type]
        output_field = delivery.get("output_field")
        if output_field != expected_output:
            c.add(
                "goal_delivery_output_mismatch",
                f"{path}.output_field",
                f"{delivery_type} 必须写入 {expected_output}。",
            )
        if delivery_type == "semantic_state" and evidence_mode != "semantic":
            c.add(
                "semantic_delivery_requires_semantic_evidence",
                f"{path}.type",
                "semantic_state 只能交给 Evaluator 做语义判断。",
            )
        # typed delivery 只声明“谁在什么输出位置完成目标”，不等同于要求逐字复述。
        # 自然对白、动作和环境变化可以交给 Evaluator 做 semantic 取证；只有作者确实
        # 需要保留口令、编号或界面原文时才使用 exact anchors。
        if delivery_type == "catgirl_action":
            for index, anchor in enumerate(evidence_anchors):
                if anchor.strip().startswith(("她", "女主", "猫娘")):
                    # 动作锚点会原样写进括号微动作；第三人称主语属于旁白，进入该字段后会破坏
                    # “括号内不朗读”的表现协议，也会诱导模型在括号外复述同一句。
                    c.add(
                        "catgirl_action_anchor_third_person",
                        f"{evidence_path}.anchors[{index}]",
                        "猫娘动作锚点必须直接写动作，不得以‘她/女主/猫娘’等第三人称主语开头。",
                    )
        source_ids = c.array(delivery.get("source_ids", []), f"{path}.source_ids")
        if len(source_ids) > 8:
            c.add(
                "too_many_goal_delivery_sources",
                f"{path}.source_ids",
                "每项目标最多引用八个正式事实来源。",
            )
        seen_sources: set[str] = set()
        for index, source_id in enumerate(source_ids):
            source_path = f"{path}.source_ids[{index}]"
            parsed = c.require_id(source_id, source_path)
            if parsed and parsed in seen_sources:
                c.add(
                    "duplicate_goal_delivery_source",
                    source_path,
                    "同一事实来源不能重复声明。",
                )
            if parsed:
                seen_sources.add(parsed)
        timing = delivery.get("timing", "turn")
        if timing not in _GOAL_DELIVERY_TIMINGS:
            c.add(
                "invalid_goal_delivery_timing",
                f"{path}.timing",
                f"交付时机必须是 {', '.join(sorted(_GOAL_DELIVERY_TIMINGS))} 之一。",
            )
        state_effects = delivery.get("state_effects")
        if state_effects is not None:
            effects = c.obj(state_effects, f"{path}.state_effects")
            unexpected = set(effects).difference({"dialogue_policy"})
            for field in sorted(unexpected):
                c.add(
                    "unexpected_goal_state_effect",
                    f"{path}.state_effects.{field}",
                    "目标状态效果只能修改已声明的发声政策。",
                )
            if (
                "dialogue_policy" in effects
                and effects.get("dialogue_policy") not in _DIALOGUE_POLICIES
            ):
                c.add(
                    "invalid_dialogue_policy",
                    f"{path}.state_effects.dialogue_policy",
                    f"发声政策必须是 {', '.join(sorted(_DIALOGUE_POLICIES))} 之一。",
                )
        fallback_inputs = delivery.get("fallback_player_inputs")
        if fallback_inputs is not None:
            rows = c.require_text_list(
                fallback_inputs,
                f"{path}.fallback_player_inputs",
                allow_empty=False,
            )
            if owner not in {"player", "shared"}:
                c.add(
                    "fallback_player_inputs_owner_mismatch",
                    f"{path}.fallback_player_inputs",
                    "只有 player 或 shared 目标可以声明玩家推荐兜底。",
                )
            if len(rows) > 3:
                c.add(
                    "too_many_fallback_player_inputs",
                    f"{path}.fallback_player_inputs",
                    "每项目标最多允许三条玩家推荐兜底。",
                )
            for index, row in enumerate(rows):
                if count_tokens(row) > 80:
                    c.add(
                        "fallback_player_input_too_long",
                        f"{path}.fallback_player_inputs[{index}]",
                        "玩家推荐兜底必须保持为可直接发送的短句。",
                    )

    @staticmethod
    def _validate_acting_contract(c: _Collector, value: Any, path: str) -> None:
        contract = c.obj(value, path)
        enum_fields = (
            ("cognition_state", _ACTING_CONTRACT_COGNITION_STATES, "认知状态"),
            ("memory_state", _ACTING_CONTRACT_MEMORY_STATES, "记忆状态"),
            ("self_reference_mode", _ACTING_CONTRACT_SELF_REFERENCE_MODES, "自称模式"),
            ("persona_scope", _ACTING_CONTRACT_PERSONA_SCOPES, "人格权限"),
        )
        for field, allowed, label in enum_fields:
            value = contract.get(field)
            if value not in allowed:
                c.add(
                    "invalid_acting_contract_value",
                    f"{path}.{field}",
                    f"{label}必须是 {', '.join(sorted(allowed))} 之一。",
                )
        if (
            "dialogue_policy" in contract
            and contract.get("dialogue_policy") not in _DIALOGUE_POLICIES
        ):
            c.add(
                "invalid_dialogue_policy",
                f"{path}.dialogue_policy",
                f"发声政策必须是 {', '.join(sorted(_DIALOGUE_POLICIES))} 之一。",
            )
        c.require_text_list(
            contract.get("allowed_behaviors"),
            f"{path}.allowed_behaviors",
            allow_empty=True,
        )
        c.require_text_list(
            contract.get("forbidden_behaviors"),
            f"{path}.forbidden_behaviors",
            allow_empty=True,
        )
        if "assertable_self_facts" in contract:
            facts = c.require_text_list(
                contract.get("assertable_self_facts"),
                f"{path}.assertable_self_facts",
                allow_empty=False,
            )
            if len(facts) > 8:
                c.add(
                    "too_many_assertable_self_facts",
                    f"{path}.assertable_self_facts",
                    "每个演绎合同最多允许八条可确认自身事实。",
                )

    @staticmethod
    def _validate_nodes(
        c: _Collector, value: Any, metric_ranges: Mapping[str, tuple[int | None, int | None]],
        *, cast_names: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
        nodes: dict[str, dict[str, Any]] = {}
        route_targets: dict[str, list[str]] = {}
        route_ids: set[str] = set()
        for index, raw in enumerate(c.array(value, "nodes")):
            path = f"nodes[{index}]"
            node = c.obj(raw, path)
            node_id = c.require_id(node.get("id"), f"{path}.id")
            if node_id in nodes:
                c.add("duplicate_node_id", f"{path}.id", "节点 ID 重复。")
            if node_id:
                nodes[node_id] = node
            if node.get("type") not in _NODE_TYPES:
                c.add("invalid_node_type", f"{path}.type", "节点类型必须是 start、scene 或 ending。")
            c.require_text(node.get("chapter"), f"{path}.chapter")
            if node.get("type") != "ending":
                min_turns = c.require_int(node.get("min_turns"), f"{path}.min_turns")
                if min_turns is not None and not 1 <= min_turns <= 20:
                    c.add("invalid_node_min_turns", f"{path}.min_turns", "最少演绎回合数必须位于 1..20。")
                if "recommended_turns" in node:
                    recommended_turns = c.require_int(
                        node.get("recommended_turns"),
                        f"{path}.recommended_turns",
                    )
                    if (
                        recommended_turns is not None
                        and (
                            min_turns is None
                            or recommended_turns < min_turns
                            or recommended_turns > MAX_RECOMMENDED_TURNS
                        )
                    ):
                        c.add(
                            "invalid_node_recommended_turns",
                            f"{path}.recommended_turns",
                            f"建议收束回合数必须不小于 min_turns，且不能超过 {MAX_RECOMMENDED_TURNS}。",
                        )
            NumericV2Compiler._validate_story_beat(
                c, node.get("story_beat"), f"{path}.story_beat", cast_names=cast_names,
            )
            if any(field in node for field in ("choices", "available_interaction_ids", "edges")):
                c.add("legacy_node_field_forbidden", path, "Numeric v2 节点不能包含旧 Choice、interaction 或 Edge 字段。")
            routes = c.array(node.get("route_gates"), f"{path}.route_gates")
            # 单出口表示确定性的顺序推进，不要求作者为了主线编译虚构数值条件；
            # 同一节点出现多个出口时，所有路线仍必须用作者配置的数值明确区分。
            allow_empty_conditions = len(routes) == 1
            targets: list[str] = []
            priorities: dict[int, list[tuple[str, dict[str, Any], str]]] = {}
            for route_index, route_raw in enumerate(routes):
                route_path = f"{path}.route_gates[{route_index}]"
                route = c.obj(route_raw, route_path)
                route_id = c.require_id(route.get("id"), f"{route_path}.id")
                if route_id in route_ids:
                    c.add("duplicate_route_id", f"{route_path}.id", "路线 ID 重复。")
                if route_id:
                    route_ids.add(route_id)
                target = c.require_id(route.get("target_node_id"), f"{route_path}.target_node_id")
                if target:
                    targets.append(target)
                    if node_id and target == node_id:
                        c.add(
                            "route_self_loop_forbidden",
                            f"{route_path}.target_node_id",
                            "路线不能直接回到当前节点。",
                        )
                priority = c.require_int(route.get("priority"), f"{route_path}.priority")
                signature = NumericV2Compiler._validate_conditions(
                    c,
                    route.get("conditions"),
                    route_path,
                    metric_ranges,
                    allow_empty=allow_empty_conditions,
                )
                if priority is not None:
                    same_priority = priorities.setdefault(priority, [])
                    raw_conditions = route.get("conditions")
                    conditions = raw_conditions if isinstance(raw_conditions, Mapping) else {}
                    if any(existing_signature == signature for existing_signature, _conditions, _path in same_priority):
                        c.add("duplicate_route_priority", route_path, "同一来源节点存在同优先级且相同条件的路线。")
                    elif any(
                        _conditions_overlap(existing_conditions, conditions, metric_ranges)
                        for existing_signature, existing_conditions, _path in same_priority
                        if existing_signature != "invalid" and signature != "invalid"
                    ):
                        c.add("overlapping_route_priority", route_path, "同一来源节点存在同优先级且条件重叠的路线。")
                    same_priority.append((signature, dict(conditions), route_path))
                NumericV2Compiler._validate_transition(c, route.get("transition_contract"), f"{route_path}.transition_contract")
            if node_id:
                route_targets[node_id] = targets
            terminal = node.get("type") == "ending" or node.get("terminal") is True
            if terminal:
                if node.get("type") == "start":
                    c.add(
                        "terminal_start_forbidden",
                        f"{path}.terminal",
                        "开场节点不能同时作为结局。",
                    )
                if routes:
                    c.add("terminal_route_forbidden", f"{path}.route_gates", "结局节点不能创建出边。")
                c.require_id(node.get("ending_id"), f"{path}.ending_id")
            elif not routes:
                c.add("node_route_required", f"{path}.route_gates", "幕节点不能作为路线终点，至少需要一条通往后续幕或结局的路线。")
        return nodes, route_targets

    @staticmethod
    def _validate_conditions(
        c: _Collector,
        value: Any,
        route_path: str,
        metric_ranges: Mapping[str, tuple[int | None, int | None]],
        *,
        allow_empty: bool,
    ) -> str:
        conditions = c.obj(value, f"{route_path}.conditions")
        modes = [mode for mode in ("all", "any") if mode in conditions]
        if len(modes) != 1:
            c.add("invalid_condition_mode", f"{route_path}.conditions", "条件必须且只能使用 all 或 any。")
            return "invalid"
        mode = modes[0]
        rows = c.array(conditions.get(mode), f"{route_path}.conditions.{mode}")
        if not rows and (not allow_empty or mode == "any"):
            c.add("route_condition_required", f"{route_path}.conditions.{mode}", "同一幕存在多个出口时，每条路线至少需要一个 metric 条件。")
        signature_rows: list[str] = []
        can_check_compound = True
        for index, raw in enumerate(rows):
            path = f"{route_path}.conditions.{mode}[{index}]"
            condition = c.obj(raw, path)
            if condition.get("type") != "metric_compare":
                c.add("invalid_condition_type", f"{path}.type", "第一版只支持 metric_compare。")
                can_check_compound = False
            metric = c.require_id(condition.get("metric"), f"{path}.metric")
            if not metric or metric not in metric_ranges:
                can_check_compound = False
            if metric and metric not in metric_ranges:
                c.add("unknown_metric", f"{path}.metric", "路线引用了未知 metric。")
            operator = condition.get("op")
            if operator not in _COMPARATORS:
                c.add("invalid_metric_comparator", f"{path}.op", "不支持该比较符。")
                can_check_compound = False
            number = c.require_int(condition.get("value"), f"{path}.value")
            if number is None:
                can_check_compound = False
            if metric in metric_ranges and number is not None:
                minimum, maximum = metric_ranges[metric]
                if minimum is not None and maximum is not None and not minimum <= number <= maximum:
                    c.add("route_threshold_out_of_range", f"{path}.value", "路线阈值超出 metric 范围。")
                    can_check_compound = False
                elif (
                    minimum is not None
                    and maximum is not None
                    and operator in _COMPARATORS
                    and not {
                        "==": minimum <= number <= maximum,
                        "!=": minimum < maximum,
                        ">": maximum > number,
                        "<": minimum < number,
                        ">=": maximum >= number,
                        "<=": minimum <= number,
                    }[operator]
                ):
                    c.add(
                        "route_condition_impossible",
                        f"{path}.value",
                        "路线条件在 metric 的声明范围内永远无法成立。",
                    )
            signature_rows.append(f"{metric}:{operator}:{number}")
        if len(rows) > 1 and can_check_compound:
            if not _conditions_overlap(conditions, {"all": []}, metric_ranges):
                c.add(
                    "route_condition_impossible",
                    f"{route_path}.conditions",
                    "路线条件组合在 metric 的声明范围内永远无法同时成立。",
                )
        return f"{mode}|{'|'.join(sorted(signature_rows))}"

    @staticmethod
    def _validate_transition(c: _Collector, value: Any, path: str) -> None:
        contract = c.obj(value, path)
        c.require_text(contract.get("reason"), f"{path}.reason")
        c.require_text_list(
            contract.get("must_deliver"),
            f"{path}.must_deliver",
            allow_empty=False,
        )
        if "bridge_scene_narration" in contract:
            c.require_text(
                contract.get("bridge_scene_narration"),
                f"{path}.bridge_scene_narration",
            )
        c.require_text_list(contract.get("must_preserve"), f"{path}.must_preserve", allow_empty=True)
        c.require_text(contract.get("tone"), f"{path}.tone")

    @staticmethod
    def _validate_endings(c: _Collector, value: Any) -> set[str]:
        ids: set[str] = set()
        for index, raw in enumerate(c.array(value, "endings")):
            path = f"endings[{index}]"
            ending = c.obj(raw, path)
            ending_id = c.require_id(ending.get("id"), f"{path}.id")
            if ending_id in ids:
                c.add("duplicate_ending_id", f"{path}.id", "结局 ID 重复。")
            if ending_id:
                ids.add(ending_id)
            c.require_text(ending.get("title"), f"{path}.title")
            c.require_text(ending.get("summary"), f"{path}.summary")
            if ending.get("terminal") is not True:
                c.add("ending_not_terminal", f"{path}.terminal", "结局必须标记 terminal=true。")
        if not ids:
            c.add("ending_required", "endings", "至少需要一个结局。")
        return ids

    @staticmethod
    def _validate_graph(c: _Collector, *, start_node_id: Any, nodes: dict[str, dict[str, Any]], route_targets: dict[str, list[str]], ending_ids: set[str]) -> None:
        start = c.require_id(start_node_id, "start_node_id")
        if start and start not in nodes:
            c.add("unknown_start_node", "start_node_id", "开场节点不存在。")
        elif start and nodes[start].get("type") != "start":
            c.add("invalid_start_node_type", "start_node_id", "start_node_id 必须指向 start 节点。")
        for source, targets in route_targets.items():
            for target in targets:
                if target not in nodes:
                    c.add("unknown_target_node", f"nodes.{source}.route_gates", f"目标节点 {target} 不存在。")
        terminal_nodes = {
            node_id: node for node_id, node in nodes.items()
            if node.get("type") == "ending" or node.get("terminal") is True
        }
        for node_id, node in terminal_nodes.items():
            if node.get("ending_id") not in ending_ids:
                c.add("unknown_ending", f"nodes.{node_id}.ending_id", "结局节点引用了未知 ending。")
        if not start or start not in nodes:
            return
        reachable: set[str] = set()
        stack = [start]
        while stack:
            node_id = stack.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            stack.extend(target for target in route_targets.get(node_id, []) if target in nodes)
        for node_id in sorted(set(nodes).difference(reachable)):
            c.add("unreachable_node", f"nodes.{node_id}", "节点从开场不可达。")
        reached_endings = {
            node.get("ending_id") for node_id, node in terminal_nodes.items() if node_id in reachable
        }
        for ending_id in sorted(ending_ids.difference(reached_endings)):
            c.add("unreachable_ending", f"endings.{ending_id}", "结局从开场不可达。")

        # 从所有结局反向遍历，保证每条可达支线都能收束到结局，而不是停在幕节点或循环中。
        reverse_routes: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        for source, targets in route_targets.items():
            for target in targets:
                if target in nodes:
                    reverse_routes[target].add(source)
        can_reach_ending = set(terminal_nodes)
        stack = list(terminal_nodes)
        while stack:
            node_id = stack.pop()
            for source in reverse_routes.get(node_id, set()):
                if source not in can_reach_ending:
                    can_reach_ending.add(source)
                    stack.append(source)
        for node_id in sorted(reachable.difference(can_reach_ending)):
            if node_id not in terminal_nodes:
                c.add("node_cannot_reach_ending", f"nodes.{node_id}", "该幕所在路线无法最终到达结局节点。")

    @staticmethod
    def _warnings(story: Mapping[str, Any]) -> list[NumericV2Warning]:
        used: set[str] = set()
        for node in story.get("nodes", []):
            for route in node.get("route_gates", []):
                conditions = route.get("conditions", {})
                rows = conditions.get("all") or conditions.get("any") or []
                used.update(row.get("metric") for row in rows if isinstance(row, Mapping))
        warnings = [
            NumericV2Warning(
                "unused_metric",
                f"metric_schema.{metric_id}",
                "该 metric 尚未被任何路线使用。",
            )
            for metric_id in story.get("metric_schema", {})
            if metric_id not in used
        ]
        return warnings

__all__ = [
    "CompiledNumericV2Package",
    "NumericV2CompileError",
    "NumericV2Compiler",
    "NumericV2Issue",
    "NumericV2Warning",
    "MAX_RECOMMENDED_TURNS",
    "MAX_STORY_GOALS",
    "STORY_SCHEMA",
]
