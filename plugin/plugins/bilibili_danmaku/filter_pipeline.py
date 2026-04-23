"""
四象限过滤流水线

功能：
- 四象限智能分级（紧急/重要维度）
- 用户价值权重计算
- 频率限制和刷屏检测
- 与现有过滤器的兼容集成
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any, Set
from collections import defaultdict
import asyncio

from .user_profile import UserProfileStore, get_global_profile_store
from .filter import is_sensitive, is_spam


class Quadrant(Enum):
    """四象限定义"""
    I = "I"      # 紧急+重要：立即推送
    II = "II"    # 不紧急+重要：进入聚合池
    III = "III"  # 紧急+不重要：丢弃
    IV = "IV"    # 不紧急+不重要：丢弃


class Action(Enum):
    """处理动作"""
    PUSH_NOW = "push_now"    # 立即推送
    QUEUE = "queue"          # 进入聚合队列
    DROP = "drop"            # 丢弃
    REPLACE = "replace"      # 替换内容（如敏感词替换）


@dataclass
class FilterResult:
    """过滤结果"""
    action: Action
    quadrant: Quadrant
    priority_score: int = 0
    reason: str = ""
    filtered_content: Optional[str] = None  # 替换后的内容


@dataclass
class Event:
    """标准化事件"""
    type: str  # danmaku, gift, superchat, guard, entry, follow
    uid: int
    name: str
    content: Optional[str] = None
    gift_name: Optional[str] = None
    gift_num: int = 1
    battery: int = 0  # 电池数（1电池=0.1元）
    price: int = 0    # 价格（元，用于SC）
    user_level: int = 0
    medal_level: int = 0
    medal_name: str = ""
    guard_level: int = 0
    is_vip: bool = False
    timestamp: float = field(default_factory=time.time)
    
    @property
    def is_high_value(self) -> bool:
        """判断是否为高价值事件"""
        if self.type == "superchat":
            return self.price >= 30  # 30元以上SC
        elif self.type == "gift":
            return self.battery >= 500  # 50元以上礼物
        elif self.type == "guard":
            return True  # 所有大航海都是高价值
        return False
    
    @property
    def rmb_value(self) -> float:
        """计算人民币价值"""
        if self.type == "superchat":
            return float(self.price)
        elif self.type in ["gift", "guard"]:
            return self.battery / 10.0  # 电池转人民币
        return 0.0


class FilterPipeline:
    """四象限过滤流水线"""
    
    def __init__(
        self,
        profile_store: Optional[UserProfileStore] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        """
        初始化过滤流水线
        
        Args:
            profile_store: 用户画像存储
            config: 过滤配置
        """
        self.profile_store = profile_store or get_global_profile_store()
        self.config = config or {}
        
        # 频率限制器
        self.user_frequency: Dict[int, List[float]] = defaultdict(list)
        self.global_frequency: List[float] = []
        
        # 频率限制配置
        self.freq_config = {
            "user_max_per_minute": 30,      # 单个用户每分钟最多30条
            "global_max_per_second": 50,    # 全局每秒最多50条
            "user_cooldown_seconds": 2,     # 用户冷却时间
        }
        
        # 白名单和黑名单
        self.whitelist_uids: Set[int] = set()
        self.blacklist_uids: Set[int] = set()
        
        # 关键词过滤
        self.keyword_patterns: List[re.Pattern] = []
        
        # 刷屏检测
        self.spam_detection_window = 10  # 10秒窗口
        self.spam_threshold = 5          # 10秒内5条视为刷屏
        
        # 加载配置
        self._load_config()
    
    def _load_config(self) -> None:
        """加载过滤配置"""
        if not self.config:
            return
        
        # 加载白名单和黑名单
        self.whitelist_uids = set(self.config.get("whitelist_uids", []))
        self.blacklist_uids = set(self.config.get("blacklist_uids", []))
        
        # 加载频率限制配置
        freq_config = self.config.get("frequency", {})
        self.freq_config.update(freq_config)
        
        # 加载关键词
        keywords = self.config.get("keywords", [])
        if keywords:
            pattern = re.compile('|'.join(map(re.escape, keywords)), re.IGNORECASE)
            self.keyword_patterns.append(pattern)
    
    async def process(self, raw_event: Dict[str, Any]) -> Optional[Tuple[Event, FilterResult]]:
        """
        处理原始事件
        
        Returns:
            (标准化事件, 过滤结果) 或 None（如果事件被丢弃）
        """
        # 1. 标准化事件
        event = self._standardize_event(raw_event)
        if event is None:
            return None
        
        # 2. 获取用户画像
        profile = await self.profile_store.get_or_create_profile(event.uid, event.name)
        
        # 3. 更新用户画像
        profile.update_from_event(raw_event)
        await self.profile_store.save_profile(profile)
        
        # 4. 执行过滤流水线
        result = await self._filter_pipeline(event, profile)
        
        # 5. 更新频率记录
        self._update_frequency(event.uid)
        
        return (event, result)
    
    def _standardize_event(self, raw_event: Dict[str, Any]) -> Optional[Event]:
        """标准化事件"""
        event_type = raw_event.get("type")
        
        if event_type == "danmaku":
            return Event(
                type="danmaku",
                uid=raw_event.get("user_id", 0),
                name=raw_event.get("user_name", ""),
                content=raw_event.get("content", ""),
                user_level=raw_event.get("user_level", 0),
                medal_level=raw_event.get("medal_level", 0),
                medal_name=raw_event.get("medal_name", ""),
                guard_level=raw_event.get("guard_level", 0),
                is_vip=raw_event.get("is_vip", False)
            )
        
        elif event_type == "gift":
            return Event(
                type="gift",
                uid=raw_event.get("user_id", 0),
                name=raw_event.get("user_name", ""),
                gift_name=raw_event.get("gift_name", ""),
                gift_num=raw_event.get("num", 1),
                battery=raw_event.get("battery", 0),
                user_level=raw_event.get("user_level", 0)
            )
        
        elif event_type == "superchat":
            return Event(
                type="superchat",
                uid=raw_event.get("user_id", 0),
                name=raw_event.get("user_name", ""),
                content=raw_event.get("message", ""),
                price=raw_event.get("price", 0),
                battery=raw_event.get("price", 0) * 10,  # 价格转电池
                user_level=raw_event.get("user_level", 0)
            )
        
        elif event_type == "guard":
            return Event(
                type="guard",
                uid=raw_event.get("user_id", 0),
                name=raw_event.get("user_name", ""),
                guard_level=raw_event.get("guard_level", 0),
                battery=raw_event.get("battery", 0)
            )
        
        return None
    
    async def _filter_pipeline(self, event: Event, profile) -> FilterResult:
        """执行过滤流水线"""
        
        # 1. 白名单检查
        if event.uid in self.whitelist_uids:
            return FilterResult(
                action=Action.PUSH_NOW,
                quadrant=Quadrant.I,
                priority_score=1000,
                reason="白名单用户"
            )
        
        # 2. 黑名单检查
        if event.uid in self.blacklist_uids:
            return FilterResult(
                action=Action.DROP,
                quadrant=Quadrant.III,
                reason="黑名单用户"
            )
        
        # 3. 频率限制检查
        freq_check = self._check_frequency(event.uid)
        if not freq_check[0]:
            return FilterResult(
                action=Action.DROP,
                quadrant=Quadrant.III,
                reason=freq_check[1]
            )
        
        # 4. 敏感词检查
        if event.content:
            if is_sensitive(event.content):
                return FilterResult(
                    action=Action.DROP,
                    quadrant=Quadrant.III,
                    reason="包含敏感词"
                )
            
            if is_spam(event.content):
                return FilterResult(
                    action=Action.DROP,
                    quadrant=Quadrant.III,
                    reason="广告/垃圾信息"
                )
        
        # 5. 关键词过滤
        keyword_check = self._check_keywords(event)
        if keyword_check[0]:
            return FilterResult(
                action=keyword_check[1],
                quadrant=keyword_check[2],
                reason=keyword_check[3]
            )
        
        # 6. 四象限分级
        quadrant = self._classify_quadrant(event, profile)
        
        # 7. 计算优先级分数
        priority_score = self._calculate_priority(event, profile, quadrant)
        
        # 8. 确定处理动作
        action = self._determine_action(quadrant, priority_score)
        
        return FilterResult(
            action=action,
            quadrant=quadrant,
            priority_score=priority_score,
            reason=f"象限{quadrant.value}, 分数{priority_score}"
        )
    
    def _check_frequency(self, uid: int) -> Tuple[bool, str]:
        """检查频率限制"""
        now = time.time()
        
        # 清理过期记录
        one_minute_ago = now - 60
        self.user_frequency[uid] = [t for t in self.user_frequency[uid] if t > one_minute_ago]
        self.global_frequency = [t for t in self.global_frequency if t > now - 1]
        
        # 检查用户频率
        if len(self.user_frequency[uid]) >= self.freq_config["user_max_per_minute"]:
            return False, f"用户频率限制: {len(self.user_frequency[uid])}/{self.freq_config['user_max_per_minute']}条/分钟"
        
        # 检查全局频率
        if len(self.global_frequency) >= self.freq_config["global_max_per_second"]:
            return False, f"全局频率限制: {len(self.global_frequency)}/{self.freq_config['global_max_per_second']}条/秒"
        
        # 检查用户冷却时间
        if self.user_frequency[uid]:
            last_time = self.user_frequency[uid][-1]
            if now - last_time < self.freq_config["user_cooldown_seconds"]:
                return False, f"用户冷却中: 还需{self.freq_config['user_cooldown_seconds'] - (now - last_time):.1f}秒"
        
        return True, "频率检查通过"
    
    def _update_frequency(self, uid: int) -> None:
        """更新频率记录"""
        now = time.time()
        self.user_frequency[uid].append(now)
        self.global_frequency.append(now)
    
    def _check_keywords(self, event: Event) -> Tuple[bool, Action, Quadrant, str]:
        """关键词检查"""
        if not event.content:
            return False, Action.DROP, Quadrant.IV, ""
        
        content_lower = event.content.lower()
        
        for pattern in self.keyword_patterns:
            if pattern.search(content_lower):
                # 根据关键词严重性决定动作
                return True, Action.DROP, Quadrant.III, "命中关键词"
        
        return False, Action.DROP, Quadrant.IV, ""
    
    def _classify_quadrant(self, event: Event, profile) -> Quadrant:
        """四象限分级"""
        
        # 紧急判断标准
        is_urgent = (
            event.type in ["superchat", "guard"] or  # SC和大航海总是紧急
            event.is_high_value or                   # 高价值礼物
            self._is_question(event.content) or      # 提问
            profile.is_core_fan                      # 核心粉丝发言
        )
        
        # 重要判断标准
        is_important = (
            event.type in ["superchat", "guard", "gift"] or  # 消费行为总是重要
            profile.is_high_value or                         # 高价值用户
            self._is_meaningful_content(event.content) or    # 有意义的内容
            profile.value_tier >= 2                          # 粉丝团以上用户
        )
        
        # 四象限判断
        if is_urgent and is_important:
            return Quadrant.I      # 紧急+重要
        elif not is_urgent and is_important:
            return Quadrant.II     # 不紧急+重要
        elif is_urgent and not is_important:
            return Quadrant.III    # 紧急+不重要
        else:
            return Quadrant.IV     # 不紧急+不重要
    
    def _is_question(self, content: Optional[str]) -> bool:
        """判断是否为提问"""
        if not content:
            return False
        
        question_indicators = [
            "吗？", "?", "？", "什么", "怎么", "如何", "为什么", "为啥",
            "请教", "请问", "问一下", "知不知道", "懂不懂", "会不会"
        ]
        
        content_lower = content.lower()
        for indicator in question_indicators:
            if indicator in content_lower:
                return True
        
        return False
    
    def _is_meaningful_content(self, content: Optional[str]) -> bool:
        """判断内容是否有意义"""
        if not content:
            return False
        
        # 过滤纯表情、单字、无意义内容
        meaningless_patterns = [
            r"^[~!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]*$",  # 纯符号
            r"^[a-zA-Z]{1,3}$",                           # 1-3个字母
            r"^[\d]{1,3}$",                               # 1-3个数字
            r"^[哈呵嘿嘻]{2,}$",                           # 重复语气词
        ]
        
        for pattern in meaningless_patterns:
            if re.match(pattern, content):
                return False
        
        # 检查内容长度
        if len(content.strip()) < 2:
            return False
        
        return True
    
    def _calculate_priority(self, event: Event, profile, quadrant: Quadrant) -> int:
        """计算优先级分数"""
        base_score = 0
        
        # 事件类型基础分
        type_scores = {
            "superchat": 100,
            "guard": 80,
            "gift": 50,
            "danmaku": 10,
        }
        base_score += type_scores.get(event.type, 0)
        
        # 价值加成
        if event.type == "superchat":
            base_score += event.price * 2  # 每元加2分
        elif event.type == "gift":
            base_score += event.battery // 10  # 每10电池加1分
        
        # 用户价值加成
        user_multiplier = 1.0 + profile.value_weight / 1000.0
        base_score = int(base_score * user_multiplier)
        
        # 内容质量加成
        if event.content:
            if self._is_question(event.content):
                base_score += 20
            if self._is_meaningful_content(event.content):
                base_score += 10
        
        # 象限加成
        quadrant_multipliers = {
            Quadrant.I: 2.0,
            Quadrant.II: 1.0,
            Quadrant.III: 0.1,
            Quadrant.IV: 0.01
        }
        base_score = int(base_score * quadrant_multipliers.get(quadrant, 1.0))
        
        return max(0, min(1000, base_score))  # 限制在0-1000分
    
    def _determine_action(self, quadrant: Quadrant, priority_score: int) -> Action:
        """根据象限和优先级确定处理动作"""
        if quadrant == Quadrant.I:
            return Action.PUSH_NOW
        elif quadrant == Quadrant.II:
            return Action.QUEUE
        elif quadrant == Quadrant.III:
            # 紧急但不重要：根据优先级决定是否丢弃
            if priority_score > 50:
                return Action.QUEUE
            else:
                return Action.DROP
        else:  # Quadrant.IV
            return Action.DROP
    
    async def add_to_whitelist(self, uid: int) -> None:
        """添加用户到白名单"""
        self.whitelist_uids.add(uid)
    
    async def add_to_blacklist(self, uid: int) -> None:
        """添加用户到黑名单"""
        self.blacklist_uids.add(uid)
    
    async def remove_from_whitelist(self, uid: int) -> None:
        """从白名单移除用户"""
        self.whitelist_uids.discard(uid)
    
    async def remove_from_blacklist(self, uid: int) -> None:
        """从黑名单移除用户"""
        self.blacklist_uids.discard(uid)
    
    def clear_frequency_records(self) -> None:
        """清空频率记录"""
        self.user_frequency.clear()
        self.global_frequency.clear()


# 全局过滤流水线实例
_global_filter_pipeline: Optional[FilterPipeline] = None

def get_global_filter_pipeline(
    profile_store: Optional[UserProfileStore] = None,
    config: Optional[Dict[str, Any]] = None
) -> FilterPipeline:
    """获取全局过滤流水线实例"""
    global _global_filter_pipeline
    
    if _global_filter_pipeline is None:
        _global_filter_pipeline = FilterPipeline(profile_store, config)
    
    return _global_filter_pipeline