from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional

from .client import STS2ApiClient, STS2ClientError
from .combat import CombatAnalyzer
from .context import GameContextAnalyzer
from .llm_strategy import LLMStrategy
from .models import normalize_snapshot
from .parser import StrategyParser
from .strategy import HeuristicSelector
from .action_execution import ActionExecutionMixin
from .autoplay_loop import AutoplayLoopMixin
from .context_flow import ContextFlowMixin
from .decisioning import DecisioningMixin
from .neko_commanding import NekoCommandingMixin
from .neko_reporting import NekoReportingMixin


class STS2AutoplayService(
    NekoCommandingMixin,
    NekoReportingMixin,
    ContextFlowMixin,
    AutoplayLoopMixin,
    DecisioningMixin,
    ActionExecutionMixin,
):
    def __init__(self, logger, status_reporter: Callable[[dict[str, Any]], None], frontend_notifier: Optional[Callable[..., Any]] = None) -> None:
        self.logger = logger
        self._report_status = status_reporter
        self._frontend_notifier = frontend_notifier
        self._client: Optional[STS2ApiClient] = None
        self._cfg: Dict[str, Any] = {}
        self._snapshot: Dict[str, Any] = {}
        self._history: Deque[Dict[str, Any]] = deque(maxlen=100)
        self._poll_task: Optional[asyncio.Task] = None
        self._autoplay_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self._paused = False
        self._server_state = "disconnected"
        self._autoplay_state = "disabled"
        self._last_error = ""
        self._last_action = ""
        self._last_poll_at = 0.0
        self._last_action_at = 0.0
        self._consecutive_errors = 0
        self._step_lock = asyncio.Lock()
        self._parser = StrategyParser(logger)
        self._combat_analyzer = CombatAnalyzer(logger)
        self._context_analyzer = GameContextAnalyzer(logger)
        self._heuristic_selector = HeuristicSelector(logger)
        self._llm_strategy = LLMStrategy(logger)
        self._neko_guidance_queue: Deque[Dict[str, Any]] = deque(maxlen=50)
        self._recent_snapshot_log: Deque[Dict[str, Any]] = deque(maxlen=60)
        self._step_count = 0
        self._last_llm_reasoning: Optional[Dict[str, Any]] = None
        self._last_neko_guidance_used = ""
        self._last_neko_guidance_count = 0
        self._semi_auto_task: Optional[Dict[str, Any]] = None
        self._last_task_report_step = -1
        self._last_neko_commentary_at = 0.0
        self._last_neko_commentary_scene = ""
        self._last_neko_event_scene = ""
        self._last_neko_event_floor = -1

    _MODE_ALIASES = {
        "full-program": "full-program",
        "full_program": "full-program",
        "program": "full-program",
        "全程序": "full-program",
        "全程序模式": "full-program",
        "half-program": "half-program",
        "half_program": "half-program",
        "半程序": "half-program",
        "半程序模式": "half-program",
        "full-model": "full-model",
        "full_model": "full-model",
        "model": "full-model",
        "模型": "full-model",
        "全模型": "full-model",
        "全模型模式": "full-model",
    }
    _MODE_LABELS = {
        "full-program": "全程序",
        "half-program": "半程序",
        "full-model": "全模型",
    }

    _DEFAULT_CHARACTER_STRATEGY = "defect"

    _COMMENTARY_STYLES = {
        "defect": {"tone": "理性", "prefix": "数据看起来", "suffix": "喵"},
        "ironclad": {"tone": "稳健", "prefix": "稳住节奏", "suffix": "喵"},
        "silent_hunter": {"tone": "灵巧", "prefix": "节奏很关键", "suffix": "喵"},
        "necrobinder": {"tone": "冷静", "prefix": "资源要算清楚", "suffix": "喵"},
        "regent": {"tone": "从容", "prefix": "局势还在掌控中", "suffix": "喵"},
    }

    _COMMENTARY_TEMPLATES = {
        "critical_hp": [
            "{prefix}，血量只剩 {hp}/{max_hp} 了，先别慌，我会优先保命和叠甲{suffix}。",
            "{prefix}，现在是危险血线 {hp}/{max_hp}，我们先把生存放第一位{suffix}。",
        ],
        "low_hp": [
            "{prefix}，现在血量偏低，{hp}/{max_hp}，这回合要稳一点{suffix}。",
            "{prefix}，血量不太健康了，先少掉血最重要{suffix}。",
        ],
        "lethal": [
            "{prefix}，看到斩杀机会了！这波可以优先找输出，把敌人收掉{suffix}。",
            "{prefix}，敌人血线已经露出来了，我们可以尝试收尾{suffix}。",
        ],
        "incoming_attack": [
            "{prefix}，敌人这回合大概要打 {incoming_attack} 点，还差 {remaining_block} 点防御，先叠甲会更稳{suffix}。",
            "{prefix}，来袭伤害有 {incoming_attack} 点，当前防御缺口是 {remaining_block}，别硬吃{suffix}。",
        ],
        "defense": [
            "{prefix}，这回合防守收益更高，我会尽量减少掉血{suffix}。",
            "{prefix}，先把防线架起来，后面再找输出窗口{suffix}。",
        ],
        "combat": [
            "{prefix}，我在看这手牌：{hand_text}。下一步倾向于{action_hint}{reason_text}{suffix}。",
            "{prefix}，手里有 {hand_text}，我会按当前局面选择{action_hint}{suffix}。",
        ],
        "reward": [
            "{prefix}，奖励界面到了，我们看看有没有适合当前构筑的好牌或资源{suffix}。",
            "{prefix}，战利品来了，先挑最能补强构筑的选项{suffix}。",
        ],
        "shop": [
            "{prefix}，商店到了，先看关键遗物，再考虑删牌和补强{suffix}。",
            "{prefix}，商店资源要精打细算，别急着花光金币{suffix}。",
        ],
        "rest": [
            "{prefix}，休息点到了，先评估血量，再决定休息还是强化{suffix}。",
            "{prefix}，这里可以喘口气，我们按血量决定最稳选择{suffix}。",
        ],
        "event": [
            "{prefix}，事件选项到了，我会优先避开高风险损血{suffix}。",
            "{prefix}，事件要看收益和代价，咱们稳一点判断{suffix}。",
        ],
        "map": [
            "{prefix}，路线选择到了，我会优先考虑安全节点和关键资源{suffix}。",
            "{prefix}，接下来挑路线，尽量平衡风险和成长{suffix}。",
        ],
        "combat_end": [
            "{prefix}，战斗结束啦，打得不错，接下来看看奖励怎么拿{suffix}。",
            "{prefix}，这一场收下了，先整理资源再继续前进{suffix}。",
        ],
        "key_relic": [
            "{prefix}，看到关键遗物了，这可能会改变后续构筑方向{suffix}。",
            "{prefix}，这个遗物值得认真考虑，可能是本局节奏点{suffix}。",
        ],
        "route_chosen": [
            "{prefix}，路线已经推进了，我们按这个节奏继续走{suffix}。",
            "{prefix}，路线选择完成，接下来注意资源和血量管理{suffix}。",
        ],
        "general": [
            "{prefix}，我在旁边看着战况，有危险会及时提醒你{suffix}。",
            "{prefix}，当前局面还可以，我会继续陪你盯着{suffix}。",
        ],
    }

    @property
    def _strategies_dir(self) -> Path:
        return Path(__file__).with_name("strategies")

    async def startup(self, cfg: Dict[str, Any]) -> None:
        self._cfg = dict(cfg)
        self._cfg["mode"] = self._configured_mode()
        self._cfg["character_strategy"] = self._resolve_startup_character_strategy(self._cfg.get("character_strategy"))
        self._shutdown = False
        self._paused = False
        self._autoplay_state = "idle"
        self._client = STS2ApiClient(
            base_url=str(self._cfg.get("base_url") or "http://127.0.0.1:8080"),
            connect_timeout=float(self._cfg.get("connect_timeout_seconds", 5) or 5),
            request_timeout=float(self._cfg.get("request_timeout_seconds", 15) or 15),
        )
        try:
            await self.health_check()
        except Exception:
            pass
        self._poll_task = asyncio.create_task(self._poll_loop())
        if bool(self._cfg.get("autoplay_on_start", False)):
            await self.start_autoplay()

    async def shutdown(self) -> None:
        self._shutdown = True
        await self.stop_autoplay()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._poll_task = None
        if self._client is not None:
            try:
                await self._client.close()
            except RuntimeError as exc:
                if "Event loop is closed" not in str(exc):
                    raise
                self.logger.warning("[sts2_autoplay] shutdown skipped client close because event loop is already closed")
            self._client = None
        self._server_state = "disconnected"
        self._emit_status()

    async def health_check(self) -> Dict[str, Any]:
        client = self._require_client()
        data = await client.health()
        self._server_state = "connected"
        self._last_error = ""
        self._emit_status()
        message = f"STS2-Agent 已连接: {client.base_url}"
        return {"status": "connected", "message": message, "summary": message, "health": data}

    async def refresh_state(self) -> Dict[str, Any]:
        context = await self._fetch_step_context(publish=True, record_history=True)
        message = f"已刷新状态，screen={self._snapshot.get('screen')}"
        return {"status": "ok", "message": message, "summary": message, "snapshot": context["snapshot"]}

    async def get_status(self) -> Dict[str, Any]:
        current_mode = self._configured_mode()
        current_character_strategy = self._configured_character_strategy()
        summary = (
            f"尖塔服务={self._server_state}，自动游玩={self._autoplay_state}，"
            f"screen={self._snapshot.get('screen', 'unknown')}，floor={self._snapshot.get('floor', 0)}"
        )
        return {
            "summary": summary,
            "message": summary,
            "server": {"state": self._server_state, "base_url": self._cfg.get("base_url", "http://127.0.0.1:8080")},
            "autoplay": {
                "state": self._autoplay_state,
                "mode": current_mode,
                "mode_label": self._display_mode_name(current_mode),
                "character_strategy": current_character_strategy,
                "strategy": current_mode,
                "strategy_label": self._display_mode_name(current_mode),
                "paused": self._paused,
                "task": self._semi_auto_task,
            },
            "run": {
                "screen": self._snapshot.get("screen", "unknown"),
                "floor": self._snapshot.get("floor", 0),
                "act": self._snapshot.get("act", 0),
                "in_combat": self._snapshot.get("in_combat", False),
                "available_action_count": self._snapshot.get("available_action_count", 0),
            },
            "decision": {"last_action": self._last_action, "last_error": self._last_error},
            "timestamps": {"last_poll_at": self._last_poll_at, "last_action_at": self._last_action_at},
        }

    async def get_snapshot(self) -> Dict[str, Any]:
        if not self._snapshot:
            await self.refresh_state()
        summary = (
            f"当前快照：screen={self._snapshot.get('screen', 'unknown')}，"
            f"floor={self._snapshot.get('floor', 0)}，"
            f"可用动作={self._snapshot.get('available_action_count', 0)}"
        )
        return {"status": "ok", "message": summary, "summary": summary, "snapshot": self._snapshot}

    async def step_once(self) -> Dict[str, Any]:
        async with self._step_lock:
            return await self._step_once_locked()


    async def _step_once_locked(self) -> Dict[str, Any]:
        context = await self._await_stable_step_context()
        actions = context["actions"]
        if not actions:
            snapshot = context["snapshot"]
            return {"status": "idle", "message": "当前没有可执行动作", "snapshot": snapshot}
        action, llm_reasoning = await self._select_action_with_reasoning(context)
        self._last_llm_reasoning = llm_reasoning
        prepared = self._prepare_action_request(action, context)
        revalidated = await self._revalidate_prepared_action(prepared, context)
        if revalidated is None:
            context = await self._await_stable_step_context()
            actions = context["actions"]
            if not actions:
                snapshot = context["snapshot"]
                return {"status": "idle", "message": "当前没有可执行动作", "snapshot": snapshot}
            action, llm_reasoning = await self._select_action_with_reasoning(context)
            self._last_llm_reasoning = llm_reasoning
            prepared = self._prepare_action_request(action, context)
        result = await self._execute_action(prepared)
        await self._maybe_emit_frontend_message(
            event_type="action",
            action=prepared.get("action_type"),
            snapshot=context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {},
            detail=result.get("message") or "",
        )
        await self._await_action_interval()
        settled_context = await self._await_post_action_settle(context, prepared)
        self._publish_snapshot(settled_context["snapshot"], record_history=True)
        return {**result, "snapshot": settled_context["snapshot"]}


    async def set_mode(self, mode: str) -> Dict[str, Any]:
        normalized_mode = self._normalize_mode_name(mode)
        if normalized_mode not in {"full-program", "half-program", "full-model"}:
            raise RuntimeError(f"暂不支持尖塔模式: {mode}")
        self._cfg["mode"] = normalized_mode
        self._emit_status()
        return {
            "status": "ok",
            "message": f"尖塔模式已切换为 {self._display_mode_name(normalized_mode)}",
            "mode": normalized_mode,
            "mode_label": self._display_mode_name(normalized_mode),
        }

    async def set_character_strategy(self, character_strategy: str) -> Dict[str, Any]:
        normalized_strategy = self._normalize_character_strategy_name(character_strategy)
        self._ensure_character_strategy_exists(normalized_strategy)
        self._cfg["character_strategy"] = normalized_strategy
        self._emit_status()
        return {
            "status": "ok",
            "message": f"角色策略已切换为 {normalized_strategy}",
            "character_strategy": normalized_strategy,
        }

    async def set_speed(self, *, action_interval_seconds: Optional[float] = None, post_action_delay_seconds: Optional[float] = None, poll_interval_active_seconds: Optional[float] = None) -> Dict[str, Any]:
        if action_interval_seconds is not None:
            self._cfg["action_interval_seconds"] = max(0.0, float(action_interval_seconds))
        if post_action_delay_seconds is not None:
            self._cfg["post_action_delay_seconds"] = max(0.0, float(post_action_delay_seconds))
        if poll_interval_active_seconds is not None:
            self._cfg["poll_interval_active_seconds"] = max(0.1, float(poll_interval_active_seconds))
        return {
            "status": "ok",
            "message": "速度设置已更新",
            "action_interval_seconds": self._cfg.get("action_interval_seconds"),
            "post_action_delay_seconds": self._cfg.get("post_action_delay_seconds"),
            "poll_interval_active_seconds": self._cfg.get("poll_interval_active_seconds"),
        }

    def _configured_mode(self) -> str:
        return self._normalize_mode_name(self._cfg.get("mode", self._cfg.get("strategy", "half-program")))

    def _configured_character_strategy(self) -> str:
        return self._normalize_character_strategy_name(self._cfg.get("character_strategy", self._DEFAULT_CHARACTER_STRATEGY))

    def _resolve_startup_character_strategy(self, strategy_name: Any) -> str:
        raw = "" if strategy_name is None else str(strategy_name).strip()
        if not raw:
            default_strategy = self._DEFAULT_CHARACTER_STRATEGY
            self._ensure_character_strategy_exists(default_strategy)
            self.logger.warning(f"[sts2_autoplay] character_strategy 为空，使用安全默认策略: {default_strategy}")
            return default_strategy
        normalized_strategy = self._normalize_character_strategy_name(raw)
        try:
            self._ensure_character_strategy_exists(normalized_strategy)
        except RuntimeError as exc:
            available = ", ".join(self._parser._available_character_strategies()) or "无"
            raise RuntimeError(
                "角色策略配置无效: "
                f"原始值={strategy_name!r}，归一化={normalized_strategy!r}；"
                f"可用策略: {available}；详情: {exc}"
            ) from exc
        return normalized_strategy

    def _normalize_mode_name(self, mode: Any) -> str:
        raw = str(mode or "half-program").strip().lower()
        return self._MODE_ALIASES.get(raw, raw)

    def _display_mode_name(self, mode: Any) -> str:
        normalized = self._normalize_mode_name(mode)
        return self._MODE_LABELS.get(normalized, normalized)

    def _normalize_character_strategy_name(self, strategy_name: Any) -> str:
        return self._parser._normalize_character_strategy_name(strategy_name)

    def _ensure_character_strategy_exists(self, strategy_name: str) -> Path:
        return self._parser._ensure_character_strategy_exists(strategy_name)


    def _require_client(self) -> STS2ApiClient:
        if self._client is None:
            raise RuntimeError("STS2 客户端未初始化")
        return self._client

    def _emit_status(self) -> None:
        try:
            current_mode = self._configured_mode()
            current_character_strategy = self._configured_character_strategy()
            self._report_status({
                "server": {"state": self._server_state, "base_url": self._cfg.get("base_url", "http://127.0.0.1:8080")},
                "autoplay": {
                    "state": self._autoplay_state,
                    "mode": current_mode,
                    "mode_label": self._display_mode_name(current_mode),
                    "character_strategy": current_character_strategy,
                    "strategy": current_mode,
                    "strategy_label": self._display_mode_name(current_mode),
                    "task": self._semi_auto_task,
                },
                "run": {"screen": self._snapshot.get("screen", "unknown"), "floor": self._snapshot.get("floor", 0), "available_action_count": self._snapshot.get("available_action_count", 0)},
                "decision": {"last_action": self._last_action, "last_error": self._last_error},
            })
        except Exception:
            pass
