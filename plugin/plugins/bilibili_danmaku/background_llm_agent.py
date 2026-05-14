"""
弹幕情报处理系统 v1.0 — 背景 LLM Agent

没有人设，没有情绪，纯信息处理。
对标 Galgame 的 GameLLMAgent 架构。

职责：
1. 独立运行时循环（2秒 tick），非回调驱动
2. 分析流水线编排（topic/rhythm）
3. 推送决策引擎（内容价值 + 冷却 + 优先级）
4. 猫娘双向通信通道
5. 多级降级 + 待机自恢复

架构：
┌─────────────────────────────────────────────┐
│              Runtime Loop (2s tick)          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ 收集批次   │→ │ 分析流水线 │→ │ 推送决策  │   │
│  └──────────┘  └──────────┘  └──────────┘   │
│        ↓              ↓              ↓       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ 更新记忆   │  │ 生成情报   │  │ 推送给猫娘 │   │
│  └──────────┘  └──────────┘  └──────────┘   │
│        ↓                                     │
│  ┌──────────┐                                │
│  │ 处理猫娘信 │                                │
│  └──────────┘                                │
└─────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Optional

from .aggregator import BatchedDanmaku, DanmakuEntry
from .danmaku_memory import DanmakuMemory
from .danmaku_analyzer import DanmakuAnalyzer

from .llm_client import LLMClient
from .plugin_utils import extract_json
from .user_profile import UserRecordManager
from .danmaku_analyzer import _POSITIVE_WORDS, _NEGATIVE_WORDS
from .intelligence_card import (
    IntelligenceCard,
    CARD_TYPE_TOPIC_REPORT,
    CARD_TYPE_SILENT_PERIOD,
    CARD_TYPE_SYSTEM_STANDBY,
    CARD_TYPE_SYSTEM_RECOVERY,
    CARD_TYPE_QUERY_RESPONSE,
    CARD_TYPE_IMPORTANT_EVENT,
    CARD_TYPE_INTERACTION_SUGGESTION,
    make_silent_card,
    make_standby_card,
    make_recovery_card,
    IntelligenceCard as Card,
)

logger = logging.getLogger(__name__)

# ── 意图关键词和回复模板（本地降级用） ──────────────────────────

_INTENT_PATTERNS: dict[str, tuple[list[str], str]] = {
    "感谢":       (["感谢", "谢谢", "多谢", "感恩", "有心了"],   "感谢支持"),
    "求教程":     (["求教程", "想学", "怎么做的", "怎么弄的", "求教"], "可以询问具体想学什么"),
    "推荐":       (["推荐", "安利", "有什么好"], "可以询问需求后推荐"),
    "请教问题":   (["请问", "请教", "想问", "求助", "怎么办"], "耐心解答观众问题"),
    "打招呼":     (["你好", "嗨", "hi", "hello", "晚上好", "下午好", "早上好"], "欢迎互动"),
    "抱怨":       (["无聊", "没意思", "不行", "不好", "难看", "难听", "退了", "走了"], "了解不满并安抚"),
    "提问":       ([], ""),  # 通用疑问句检测，见 _is_question()
}

_REPLY_TEMPLATES: dict[str, str] = {
    "感谢":     "感谢「{text}」的观众~",
    "求教程":   "有观众问「{text}」，可以聊一下这个",
    "推荐":     "观众求推荐「{text}」，可以互动一下",
    "请教问题": "观众请教「{text}」，一起聊聊吧",
    "打招呼":   "「{text}」欢迎新来的朋友~",
    "抱怨":     "观众说「{text}」，可以回应一下",
    "提问":     "观众问到「{text}」，这是个好问题",
}

# ── 默认配置 ──────────────────────────────────────────────────

_DEFAULT_TICK_INTERVAL = 2.0          # tick 间隔（秒）
_DEFAULT_PUSH_COOLDOWN = 12.0         # 最小推送间隔（秒）
_DEFAULT_SILENT_PUSH_AFTER = 300.0    # 静默 N 秒后推送静默提醒
_DEFAULT_MAX_BATCHES_PER_TICK = 10    # 每 tick 最多处理批次
_DEFAULT_STANDBY_THRESHOLD = 5        # 连续 N 次异常进入待机
_DEFAULT_RECOVERY_INTERVAL = 30.0     # 待机后每 N 秒尝试恢复


class DanmakuBackgroundAgent:
    """
    弹幕情报处理系统 v1.0

    这是一个在后台独立运行的 LLM Agent。
    它没有性格、没有角色、没有人设——只做信息处理。
    """

    def __init__(
        self,
        *,
        memory: DanmakuMemory,
        user_records: Optional[UserRecordManager] = None,
        analyzer: Optional[DanmakuAnalyzer] = None,
        push_func: Optional[Callable[[IntelligenceCard], None]] = None,
        push_text_func: Optional[Callable[[str, str, int], None]] = None,
        room_id: int = 0,
        tick_interval: float = _DEFAULT_TICK_INTERVAL,
        push_cooldown: float = _DEFAULT_PUSH_COOLDOWN,
        silent_push_after: float = _DEFAULT_SILENT_PUSH_AFTER,
        standby_threshold: int = _DEFAULT_STANDBY_THRESHOLD,
        recovery_interval: float = _DEFAULT_RECOVERY_INTERVAL,
        llm_client: Optional[LLMClient] = None,
        knowledge_context: str = "",
        llm_pool_threshold: int = 20,
        llm_pool_max: int = 500,
    ):
        """
        Args:
            memory: 弹幕房间级记忆系统（话题/密度/节奏）
            user_records: 统一用户记录管理器（观众画像/分类/备注）
            analyzer: 弹幕分析器
            push_func: 推送回调（IntelligenceCard → None）
            push_text_func: 直接文本推送回调，用于本地筛选结果直推（respond）
            room_id: 直播间 ID
            tick_interval: 运行循环间隔
            push_cooldown: 同类型推送最小冷却
            silent_push_after: 静默多少秒后推送提醒
            standby_threshold: 连续异常次数阈值
            recovery_interval: 待机恢复重试间隔
            llm_client: LLM 调用客户端（用于弹幕筛选）
            knowledge_context: 知识库上下文（来自 config.json）
            llm_pool_threshold: LLM 池子触发条数（攒够 N 条才调 LLM）
            llm_pool_max: LLM 池子最大容量，超出丢弃最旧条目
        """
        self._memory = memory
        self._user_records = user_records
        self._analyzer = analyzer
        self._push_func = push_func
        self._push_text_func = push_text_func
        self._room_id = room_id
        self._tick_interval = tick_interval
        self._push_cooldown = push_cooldown
        self._silent_push_after = silent_push_after
        self._standby_threshold = standby_threshold
        self._recovery_interval = recovery_interval
        self._llm_client = llm_client
        self._knowledge_context = knowledge_context
        self._host_recent_messages: str = ""      # 主播近期发言（供 LLM 筛选参考）
        self._llm_pool: list = []                 # LLM 池子（累计弹幕条目）
        self._llm_pool_threshold = llm_pool_threshold
        self._llm_pool_max = llm_pool_max

        # ── 运行状态 ──
        self._running = False
        self._started = False

        # ── 弹幕批次缓冲（生产者→消费者队列） ──
        self._batch_queue: asyncio.Queue[BatchedDanmaku] = asyncio.Queue()

        # ── 猫娘 inbox（catgirl → agent） ──
        self._catgirl_inbox: asyncio.Queue[dict] = asyncio.Queue()

        # ── 推送状态 ──
        self._last_push_time: float = 0.0
        self._last_push_type: str = ""
        self._consecutive_failures: int = 0
        self._total_pushes: int = 0

        # ── 待机模式 ──
        self._standby: bool = False
        self._standby_reason: str = ""
        self._standby_entered_at: float = 0.0
        self._last_recovery_attempt: float = 0.0

        # ── 硬错误 ──
        self._hard_error: str = ""
        self._hard_error_retryable: bool = False

        # ── 静默推送跟踪 ──
        self._last_silent_push_time: float = 0.0

        # ── 统计 ──
        self._ticks: int = 0
        self._batches_processed: int = 0
        self._cards_generated: int = 0
        self._local_fallback_count: int = 0

        # ── 全链路监控 ──


    # ══════════════════════════════════════════════════════════
    # 生命周期
    # ══════════════════════════════════════════════════════════

    async def start(self):
        """启动 Agent（不再创建独立任务，由插件 @timer_interval 驱动）"""
        if self._started:
            return
        self._started = True
        self._running = True
        logger.info("弹幕情报系统 Agent 已就绪")

    async def stop(self):
        """停止 Agent"""
        if not self._started:
            return
        self._running = False
        self._started = False
        await self._memory.save()
        logger.info("弹幕情报系统 Agent 已停止")

    @property
    def is_running(self) -> bool:
        return self._started and self._running

    @property
    def is_standby(self) -> bool:
        return self._standby

    # ══════════════════════════════════════════════════════════
    # 数据入口
    # ══════════════════════════════════════════════════════════

    async def feed_batch(self, batch: BatchedDanmaku):
        """向 Agent 喂入一个弹幕聚合批次（由聚合器回调调用）"""
        if self._standby:
            logger.info(f"[Agent] feed_batch 丢弃(待机): {batch.total_count}条弹幕")
            return  # 待机模式丢弃分析数据（但仍可恢复时处理新数据）
        await self._batch_queue.put(batch)
        logger.info(f"[Agent] feed_batch 入队: {batch.total_count}条弹幕, entries={batch.count}, 队列深度={self._batch_queue.qsize()}")

    def feed_host_message(self, text: str):
        """喂入主播发言，供弹幕筛选时避免推荐已回应话题"""
        if not text:
            return
        self._host_recent_messages = (self._host_recent_messages + "\n" + text)[-500:]

    async def send_from_catgirl(self, message: dict):
        """
        猫娘向 Agent 发送消息。
        会打断 Agent 当前处理，Agent 处理完当前 tick 后会优先处理此消息。
        """
        await self._catgirl_inbox.put(message)

    # ══════════════════════════════════════════════════════════
    # 猫娘查询 API（直接从插件层调用，不经过 inbox 队列）
    # ══════════════════════════════════════════════════════════

    async def query(self, question: str) -> IntelligenceCard:
        """
        猫娘主动查询：返回情报卡片
        不经过 inbox，直接返回结果（同步等待）
        """
        return await self._handle_query(question)

    async def get_snapshot(self) -> dict:
        """获取当前情报快照（给猫娘看的全貌）"""
        active_topics = self._memory.get_active_topics()
        density = self._memory.get_density_status()
        silent_seconds = self._memory.get_silent_seconds()
        audience = self._user_records.get_audience_summary() if self._user_records else ""

        return {
            "room_id": self._room_id,
            "agent_status": "standby" if self._standby else "active",
            "silent_seconds": int(silent_seconds),
            "density": density,
            "active_topics": [
                {
                    "keywords": list(t.keywords),
                    "mention_count": t.mention_count,
                    "sentiment": round(t.avg_sentiment, 2),
                }
                for t in active_topics[:5]
            ],
            "active_audience": audience,
            "total_pushes": self._total_pushes,
            "stats": self.get_stats(),
        }

    # ══════════════════════════════════════════════════════════
    # 运行循环
    # ══════════════════════════════════════════════════════════

    async def tick(self):
        """
        外部驱动 tick（由插件的 @timer_interval 每 2 秒调用）
        返回 True 表示正常处理，False 表示待机跳过
        """
        if not self._running:
            return False

        try:
            if self._standby:
                await self._standby_tick()
            else:
                await self._normal_tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._handle_tick_error(e)
            return False
        return True

    async def _normal_tick(self):
        """正常 tick：收集 → 分析 → 推送 → 处理猫娘消息 → 清理"""
        self._ticks += 1
        # 1. 处理猫娘发来的消息（优先）
        await self._process_catgirl_mail()

        # 2. 收集待处理批次
        batches = self._drain_batches()
        if not batches:
            logger.info(f"[Agent] _normal_tick: 无批次, tick#{self._ticks}")
            await self._check_silence()
            return

        self._batches_processed += len(batches)
        logger.info(f"[Agent] _normal_tick: 处理 {len(batches)} 个批次, 累计={self._batches_processed}")

        # 3. 更新房间级指标（用户画像已在 _process_danmaku_event 中记录）
        for batch in batches:
            for entry in batch.entries:
                # 房间级指标（DanmakuMemory: 静默计时、提问检测）
                self._memory.record_danmaku(
                    uid=entry.uid,
                    uname=entry.uname,
                    text=entry.text,
                )

        # 3.5 运行分析流水线（节奏检测、话题分析），产出 topic/rhythm 卡片
        analyzer_cards: list[IntelligenceCard] = []
        if self._analyzer:
            for batch in batches:
                try:
                    pipeline_cards = await self._analyzer.run_pipeline(batch)
                    analyzer_cards.extend(pipeline_cards)
                except Exception as e:
                    logger.warning(f"[Agent] 分析流水线异常: {e}")

        # 4. 弹幕筛选：
        #    LLM 路径 → 数量池子（攒够 N 条才调 LLM）
        #    降级路径 → 按时间批次（聚合器窗口触发，走本地评分）
        cards = []
        llm_attempted = False

        if self._llm_client:
            # 累加入池
            for batch in batches:
                self._llm_pool.extend(batch.entries)
                # 限制池子大小，超出时丢弃最旧条目
                if len(self._llm_pool) > self._llm_pool_max:
                    excess = len(self._llm_pool) - self._llm_pool_max
                    self._llm_pool = self._llm_pool[excess:]

            pool_size = len(self._llm_pool)
            if pool_size >= self._llm_pool_threshold:
                llm_attempted = True
                logger.info(f"[Agent] LLM池子触发: {pool_size}/{self._llm_pool_threshold}")
                try:
                    audience_summary = ""
                    if self._user_records:
                        audience_summary = self._user_records.get_audience_summary(max_count=5)
                    topic_summary = self._memory.get_topic_summary()
                    # 合并池子为单个批次调 LLM
                    merged = BatchedDanmaku(
                        entries=self._llm_pool,
                        total_count=pool_size,
                        window_start=self._llm_pool[0].timestamp,
                        window_end=time.time(),
                        sampled=False,
                    )
                    selected = await self._llm_select_danmaku(merged, audience_summary, topic_summary)
                    if selected:
                        cards.append(selected)
                except Exception as e:
                    logger.warning(f"[Agent] LLM 筛选失败，降级本地: {e}")
                finally:
                    self._llm_pool.clear()
            else:
                logger.info(f"[Agent] LLM池子 {pool_size}/{self._llm_pool_threshold}, 等待积累")

        # 降级路径（按时间批次）：
        # - LLM 未配置：总是走本地评分
        # - LLM 已配置且池子触发但 LLM 调用失败（llm_attempted=True 但 cards 为空）：降级本地
        # - LLM 已配置但池子积累中：跳过本地评分，避免同一批弹幕被推两次
        if not cards and (not self._llm_client or llm_attempted):
            for batch in batches:
                selected = self._select_interesting_danmaku(batch)
                if selected:
                    cards.append(selected)

        self._cards_generated += len(cards)

        # 分析流水线卡不计入 _cards_generated（另有独立统计），追加至推送列表
        cards.extend(analyzer_cards)

        mode = "LLM" if cards and llm_attempted else ("池积累" if self._llm_client and not llm_attempted else "本地降级")
        logger.info(f"[Agent] {mode}筛选完成: 生成 {len(cards)} 张推送卡片, 累计={self._cards_generated}")
        for card in cards:
            logger.info(f"[Agent]   卡片: type={card.card_type}, priority={card.priority}, summary={card.summary[:80]}")

        # 5. 推送决策：独立高优通道 + 普通卡片合并
        pushed_cards = [c for c in cards if self._should_push(c)]
        # 5a. 互动建议单独推送（priority=7-8），不与其他卡片合并
        suggestion_cards = [c for c in pushed_cards
                            if c.card_type == CARD_TYPE_INTERACTION_SUGGESTION]
        other_cards = [c for c in pushed_cards
                       if c.card_type != CARD_TYPE_INTERACTION_SUGGESTION]

        for sc in suggestion_cards:
            # 提升优先级至 7-8，确保主播/LLM 优先看到
            sc.priority = max(sc.priority, 7)
            formatted = self._format_interaction_suggestion(sc)
            if self._push_text_func:
                self._push_text_with_tracking(formatted, "建议回复", sc.priority, sc.card_type)
            else:
                await self._do_push(sc)

        if other_cards:
            merged = "\n".join(c.summary for c in other_cards[:3])
            if self._push_text_func:
                self._push_text_with_tracking(merged, "弹幕情报", 5, other_cards[0].card_type)
            else:
                await self._do_push(other_cards[0])

        # 6. 清理过期记忆
        self._memory.cleanup()

        # 7. 重置连续失败计数
        self._consecutive_failures = 0

    async def _standby_tick(self):
        """待机 tick：只处理猫娘消息 + 尝试恢复"""
        # 处理猫娘消息
        await self._process_catgirl_mail()

        # 尝试恢复
        if time.time() - self._last_recovery_attempt > self._recovery_interval:
            self._last_recovery_attempt = time.time()
            logger.info(f"弹幕情报系统尝试退出待机 (reason: {self._standby_reason})")
            # 恢复：清除待机状态，发恢复通知
            self._standby = False
            self._consecutive_failures = 0
            self._hard_error = ""
            self._clear_standby()
            await self._do_push(make_recovery_card())

    # ══════════════════════════════════════════════════════════
    # 推送决策引擎
    # ══════════════════════════════════════════════════════════

    def _should_push(self, card: IntelligenceCard) -> bool:
        """
        判断是否应该推送这张情报卡片

        规则：
        - 紧急事件（priority >= 8）：总是推送，忽略冷却
        - 系统通知（priority <= 1）：总是推送
        - 普通卡片：必须满足冷却时间
        - 静默卡片：每 N 秒最多一次
        - 过期卡片：不推送
        """
        if card.is_expired:
            return False

        now = time.time()

        # 紧急事件：立即推送
        if card.is_urgent:
            return True

        # 系统通知：立即推送
        if card.priority <= 1:
            return True

        # 静默卡片：有额外冷却
        if card.card_type == CARD_TYPE_SILENT_PERIOD:
            if now - self._last_silent_push_time < self._push_cooldown * 2:
                return False
            return True

        # 普通卡片：冷却检查
        if now - self._last_push_time < self._push_cooldown:
            return False

        return True

    def _push_text_with_tracking(self, text: str, label: str, priority: int, card_type: str = "") -> None:
        """文本推送回调封装，带统计和待机保护（与 _do_push 行为一致）"""
        if not self._push_text_func:
            return
        try:
            self._push_text_func(text, label, priority)
            self._total_pushes += 1
            self._last_push_time = time.time()
            self._last_push_type = card_type
            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            logger.warning(f"文本推送失败 ({self._consecutive_failures}/{self._standby_threshold}): {e}")
            if self._consecutive_failures >= self._standby_threshold:
                self._enter_standby(f"连续{self._standby_threshold}次文本推送失败: {e}")

    async def _do_push(self, card: IntelligenceCard):
        """执行推送"""
        if not self._push_func:
            logger.warning("[Agent] _do_push: push_func 为空，无法推送")
            return

        try:
            logger.info(f"[Agent] _do_push: type={card.card_type}, priority={card.priority}, 距上次推送={time.time()-self._last_push_time:.1f}s")

            self._push_func(card)
            self._last_push_time = time.time()
            self._last_push_type = card.card_type
            self._total_pushes += 1

            if card.card_type == CARD_TYPE_SILENT_PERIOD:
                self._last_silent_push_time = time.time()

            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            logger.warning(f"推送情报卡片失败 ({self._consecutive_failures}/{self._standby_threshold}): {e}")

            if self._consecutive_failures >= self._standby_threshold:
                self._enter_standby(f"连续{self._standby_threshold}次推送失败: {e}")

    # ══════════════════════════════════════════════════════════
    # LLM 弹幕筛选
    # ══════════════════════════════════════════════════════════

    _LLM_SELECT_PROMPT = """你是一个虚拟主播直播间的弹幕筛选助手。
你的任务：从弹幕列表中选出最值得推送给主播的 1-3 条弹幕。

选弹幕标准（按加权优先级）：
1. 互动型（权重最高）→ 直接 @主播、提问、疑问句 — 主播必须优先回应
2. 情绪型（优先安抚）→ 包含负面情绪、抱怨、不满 — 需尽快安抚
3. 价值型 → 高等级用户、付费用户（舰长/提督/总督）的发言
4. 情报型 → 提到游戏/新闻/事件/知识 — 主播可以延展话题
5. 趣味型 → 有趣吐槽、夸夸、造梗 — 活跃氛围
6. 代表型 → 多条同类弹幕选一条最具代表性的

避免原则：
- 避免推荐主播已经回应过的话题（参考主播近期发言）
- 避免推荐纯表情、纯语气词、无实质内容的弹幕

对每条选中的弹幕：
- 必须补充背景信息（黑话/梗/游戏术语/事件），让主播看了能接上话
- 如果弹幕是提问，给出建议的回应方向
- 如果弹幕是负面情绪，标注「需安抚」
- 在 reply_hint 字段给出 5-15 字的具体回复建议

输出格式（严格 JSON）：
{"selected": [
  {"text": "弹幕原文", "username": "发送者", "context": "补充说明", "reply_hint": "回复建议"}
]}

如果没有值得选的弹幕，输出 {"selected": []}
不要分析趋势，只筛选弹幕并补充信息。"""

    async def _llm_select_danmaku(
        self,
        batch: BatchedDanmaku,
        audience_summary: str,
        topic_summary: str,
    ) -> Optional[IntelligenceCard]:
        """调用 LLM 筛选弹幕，附带观众画像和知识库，再联网补充"""
        if not batch.entries:
            return None

        # 带等级信息的弹幕列表（让 LLM 知道用户价值）
        texts = []
        for e in batch.entries[:60]:
            level_tag = ""
            if e.level >= 25:
                level_tag = " [高等级]"
            elif e.guard > 0:
                level_tag = " [付费用户]"
            texts.append(f"{e.uname}{level_tag}: {e.text}")

        # 构造 user prompt（观众画像 + 话题 + 知识库 + 主播近期发言）
        parts = ["弹幕列表："]
        parts.extend(f"- {t}" for t in texts)
        if audience_summary:
            parts.append(f"\n观众画像：\n{audience_summary}")
        if topic_summary:
            parts.append(f"\n当前话题：\n{topic_summary}")
        if self._knowledge_context:
            parts.append(f"\n知识库参考：\n{self._knowledge_context}")
        if self._host_recent_messages:
            parts.append(f"\n主播近期发言（避免推荐已回应话题）：\n{self._host_recent_messages}")

        messages = [
            {"role": "system", "content": self._LLM_SELECT_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]

        raw = await self._llm_client.call(messages)
        if not raw:
            return None

        # 解析 JSON 响应
        try:
            data = extract_json(raw)
            if not data:
                return None
            selected = data.get("selected", [])
            if not selected:
                return None
        except Exception:
            return None

        # 构建推送文本（含 reply_hint）
        lines = []
        suggestions = []
        for s in selected[:4]:
            uname = s.get("username", "观众")
            text = s.get("text", "")
            ctx = s.get("context", "").strip()
            reply_hint = s.get("reply_hint", "").strip()
            prefix = f"· {uname}说：{text}"
            if ctx:
                prefix += f"（{ctx}）"
            lines.append(prefix)
            if reply_hint:
                suggestions.append(reply_hint)

        if not lines:
            return None

        summary = "\n".join(lines)

        card = IntelligenceCard(
            card_type=CARD_TYPE_INTERACTION_SUGGESTION,
            priority=7,
            summary=summary,
            suggestion="",
            diagnostic="",
        )
        if suggestions:
            card.suggestion = "；".join(suggestions[:2])
        else:
            card.suggestion = "自然地回应一下观众"

        return card

    @staticmethod
    def _format_interaction_suggestion(card: IntelligenceCard) -> str:
        """将互动建议格式化为高优推送格式"""
        parts = ["🎯 建议回复这条弹幕："]
        parts.append(card.summary)
        if card.suggestion:
            parts.append(f"💡 {card.suggestion}")
        return "\n".join(parts)

    # 本地弹幕筛选（不调 LLM）
    # ══════════════════════════════════════════════════════════
    # 情绪词见 danmaku_analyzer._POSITIVE_WORDS / _NEGATIVE_WORDS

    @staticmethod
    def _is_question(text: str) -> bool:
        """判断文本是否为疑问句"""
        q_markers = ("吗", "？", "?", "什么", "怎么", "为啥", "为什么",
                      "啥", "哪", "谁能", "有没有人", "大家有没有",
                      "能不能", "可不可以", "会不会", "是不是", "有没",
                      "怎", "如何", "怎样", "咋")
        return any(q in text for q in q_markers)

    @staticmethod
    def _match_intent(text: str) -> tuple[Optional[str], Optional[str]]:
        """匹配意图，返回 (intent_name, reply_template)"""
        text_lower = text.lower()
        for intent, (keywords, reply) in _INTENT_PATTERNS.items():
            if any(k in text_lower for k in keywords):
                return intent, reply
        # 通用疑问句检测
        if DanmakuBackgroundAgent._is_question(text):
            return "提问", _REPLY_TEMPLATES["提问"]
        return None, None

    @staticmethod
    def _is_at_host(text: str) -> bool:
        """检测是否 @主播（提到主播相关称呼）"""
        at_keywords = ("主播", "up", "UP", "up主", "UP主", "up猪",
                        "@", "小姐姐", "小哥哥", "大大", "楼主")
        return any(k in text for k in at_keywords)

    def _select_interesting_danmaku(self, batch: BatchedDanmaku) -> Optional[IntelligenceCard]:
        """
        从弹幕批次中挑选有意思的弹幕直接推送（纯本地评分，不调 LLM）

        评分维度：
        - 意图匹配（感谢/求教程/提问等）→ +4
        - @主播 → +3
        - 情绪词（正面/负面）→ +3/+2
        - 礼物/SC → +4 并特殊合并
        - 管理员/高等级 → +1
        - 长度 > 8 → +1
        - 长度 > 15 → +1

        产出：生成带 reply_hint 的建议卡片，和 LLM 路径共用同一高优通道
        """
        if not batch.entries:
            return None

        scored: list[tuple[int, str, str, str]] = []  # (score, text, username, reply_hint)
        gifts: list[str] = []   # 礼物消息合并用
        scs: list[str] = []     # SC 合并用

        for entry in batch.entries:
            t = entry.text.strip()
            if not t:
                continue
            t_lower = t.lower()
            score = 0
            reply_hint = ""

            # 礼物/SC → 加分并收集合并
            if entry.msg_type == 2:
                gifts.append(f"{entry.uname}: {t}")
                continue  # 不参与弹幕评分，统一合并
            if entry.msg_type == 13:
                scs.append(f"{entry.uname}: {t}")
                continue  # 同上

            # ── 意图匹配（权重最高） ──
            intent, intent_reply = self._match_intent(t)
            if intent:
                score += 4
                reply_hint = intent_reply

            # ── @主播 ──
            if self._is_at_host(t):
                score += 3
                if not reply_hint:
                    reply_hint = "观众在 @你，可以直接回应"

            # ── 情绪词 ──
            if any(w in t_lower for w in _POSITIVE_WORDS):
                score += 3
            if any(w in t_lower for w in _NEGATIVE_WORDS):
                score += 2
                if not reply_hint:
                    reply_hint = "了解观众的不满并安抚"

            # ── 付费用户 → +1 ──
            if entry.msg_source == 1 or entry.guard > 0:
                score += 1

            # ── 长度加分 ──
            if len(t) > 8:
                score += 1
            if len(t) > 15:
                score += 1

            scored.append((score, t, entry.uname, reply_hint))

        # ── 合并礼物和 SC ──
        merged_gifts_or_scs = ""
        if scs:
            # SC 单独推送（高价值）
            sc_lines = []
            for s in scs[:2]:
                sc_lines.append(f"SC {s}")
            merged_gifts_or_scs += "\n".join(sc_lines) + "\n"
        if gifts:
            # 礼物合并：同类型合并摘要
            if len(gifts) <= 3:
                merged_gifts_or_scs += "\n".join(f"礼物 {g}" for g in gifts)
            else:
                merged_gifts_or_scs += f"礼物 {len(gifts)} 人送礼"
            merged_gifts_or_scs = merged_gifts_or_scs.strip()

        # ── 排序筛选 ──
        scored.sort(key=lambda x: -x[0])
        selected = scored[:3]

        # 如果既没选出弹幕也没礼物/SC，放弃
        if not selected and not merged_gifts_or_scs:
            return None

        # ── 构造推送内容 ──
        parts: list[str] = []
        suggestions: list[str] = []

        for s in selected:
            if s[0] < 2:
                continue
            parts.append(f"· {s[2]}说：{s[1]}")
            if s[3]:
                suggestions.append(s[3])

        if merged_gifts_or_scs:
            parts.append(merged_gifts_or_scs)

        if not parts:
            return None

        summary = "\n".join(parts)

        suggestion = ""
        if suggestions:
            # 去重
            seen = set()
            unique = []
            for s in suggestions:
                if s not in seen:
                    seen.add(s)
                    unique.append(s)
            suggestion = "；".join(unique[:2])
        else:
            suggestion = "自然地回应一下观众"

        return IntelligenceCard(
            card_type=CARD_TYPE_INTERACTION_SUGGESTION,
            priority=7,  # 和 LLM 路径同一优先级，走高优通道
            summary=summary,
            suggestion=suggestion,
            diagnostic="local_fallback",
        )

    # ══════════════════════════════════════════════════════════
    # 静默检测
    # ══════════════════════════════════════════════════════════

    async def _check_silence(self):
        """检测直播间是否长时间无人发言"""
        silent_seconds = self._memory.get_silent_seconds()
        if silent_seconds < self._silent_push_after:
            return

        # 避免重复推送静默提醒
        if time.time() - self._last_silent_push_time < self._silent_push_after:
            return

        card = make_silent_card(
            silent_seconds=silent_seconds,
            suggestion="可以随便聊点别的，或者问问观众在不在",
        )
        if self._should_push(card):
            await self._do_push(card)

    # ══════════════════════════════════════════════════════════
    # 猫娘消息处理
    # ══════════════════════════════════════════════════════════

    async def _process_catgirl_mail(self):
        """处理猫娘发来的 inbox 消息"""
        while not self._catgirl_inbox.empty():
            try:
                message = self._catgirl_inbox.get_nowait()
            except asyncio.QueueEmpty:
                break

            msg_type = message.get("type", "")
            if msg_type == "query":
                question = message.get("text", "")
                card = await self._handle_query(question)
                await self._do_push(card)
            elif msg_type == "command":
                command = message.get("command", "")
                await self._handle_command(command, message.get("args", {}))

    async def _handle_query(self, question: str) -> IntelligenceCard:
        """处理猫娘查询"""
        snapshot = await self.get_snapshot()

        # 如果有 LLM，可以调用 LLM 回答
        # 暂时返回结构化快照
        density = snapshot.get("density", {})
        topics = snapshot.get("active_topics", [])
        silent = snapshot.get("silent_seconds", 0)

        if "谁" in question or "观众" in question or "人在" in question:
            summary = (self._user_records.get_audience_summary() if self._user_records else None) or "目前没有活跃观众"
        elif "话题" in question or "在聊" in question or "讨论" in question:
            summary = self._memory.get_topic_summary() or "目前没有明显的话题"
        elif "弹幕" in question or "节奏" in question or "热闹" in question:
            trend = density.get("trend", "stable")
            if trend == "rising":
                summary = f"弹幕在增多，当前密度 {density.get('current', 0)} 条/窗口"
            elif trend == "falling":
                summary = f"弹幕在减少，当前密度 {density.get('current', 0)} 条/窗口"
            else:
                summary = f"弹幕平稳，密度 {density.get('current', 0)} 条/窗口"
        elif "沉默" in question or "安静" in question or "没人" in question:
            if silent > 0:
                minutes = int(silent // 60)
                summary = f"已经{minutes}分钟没人发弹幕了" if minutes > 0 else "直播间目前没人说话"
            else:
                summary = "直播间有人在说话"
        else:
            # 默认：返回全貌
            topic_lines = "\n".join(
                f"- {'、'.join(t.get('keywords', []))}（{t.get('mention_count', 0)}次）"
                for t in topics[:3]
            )
            summary = f"当前有 {len(topics)} 个话题在讨论\n{topic_lines}" if topics else "当前没有活跃话题"

        return IntelligenceCard(
            card_type=CARD_TYPE_QUERY_RESPONSE,
            priority=5,
            summary=summary,
            details=snapshot,
            batch_info={"query": question},
        )

    async def _handle_command(self, command: str, args: dict):
        """处理猫娘命令"""
        if command == "reset":
            self._clear_standby()
            self._consecutive_failures = 0
            self._hard_error = ""
        elif command == "standby":
            self._enter_standby(args.get("reason", "猫娘要求待机"))
        elif command == "resume":
            self._clear_standby()

    # ══════════════════════════════════════════════════════════
    # 待机模式
    # ══════════════════════════════════════════════════════════

    def _enter_standby(self, reason: str):
        """进入待机模式"""
        if self._standby:
            return
        self._standby = True
        self._standby_reason = reason
        self._standby_entered_at = time.time()
        self._hard_error = ""
        logger.warning(f"弹幕情报系统进入待机模式: {reason}")

        # 清空缓存队列（不限量，全部清空）
        self._drain_batches(limit=None)

    def _clear_standby(self):
        """退出待机模式"""
        self._standby = False
        self._standby_reason = ""
        self._last_recovery_attempt = 0
        logger.info("弹幕情报系统退出待机模式")

    # ══════════════════════════════════════════════════════════
    # 错误处理
    # ══════════════════════════════════════════════════════════

    def _handle_tick_error(self, error: Exception):
        """处理 tick 中的异常"""
        self._consecutive_failures += 1
        logger.error(
            f"Agent tick 异常 ({self._consecutive_failures}/{self._standby_threshold}): {error}"
        )

        if self._consecutive_failures >= self._standby_threshold:
            self._enter_standby(f"连续{self._standby_threshold}次 tick 异常: {error}")

    # ══════════════════════════════════════════════════════════
    # 工具方法
    # ══════════════════════════════════════════════════════════

    def _drain_batches(self, limit: Optional[int] = _DEFAULT_MAX_BATCHES_PER_TICK) -> list[BatchedDanmaku]:
        """清空批次队列

        Args:
            limit: 最多取出条数，None 表示不限制
        """
        batches = []
        while not self._batch_queue.empty():
            if limit is not None and len(batches) >= limit:
                break
            try:
                batch = self._batch_queue.get_nowait()
                batches.append(batch)
            except asyncio.QueueEmpty:
                break
        return batches

    def get_stats(self) -> dict:
        """获取运行统计"""
        return {
            "ticks": self._ticks,
            "batches_processed": self._batches_processed,
            "cards_generated": self._cards_generated,
            "total_pushes": self._total_pushes,
            "consecutive_failures": self._consecutive_failures,
            "standby": self._standby,
            "standby_reason": self._standby_reason if self._standby else "",
            "memory": self._memory.get_stats(),
            "user_records": self._user_records.get_stats() if self._user_records else {},
            "hard_error": self._hard_error,
        }
