"""
用户画像存储模块

功能：
- 用户画像数据模型定义
- 用户价值权重计算
- JSON持久化存储
- LRU内存缓存管理
"""

from __future__ import annotations

import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from collections import OrderedDict
import asyncio


@dataclass
class UserProfile:
    """用户画像数据类"""
    
    # 基础信息
    uid: int
    name: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    
    # 身份等级
    ul_level: int = 0                    # 用户等级 (0-60)
    fan_medal_level: int = 0             # 粉丝团等级
    fan_medal_name: str = ""             # 粉丝团名称
    is_vip: bool = False                 # 老爷（大会员）
    guard_level: int = 0                 # 0=无，1=总督，2=提督，3=舰长
    
    # 消费统计
    total_battery_spent: int = 0         # 总电池消费（1电池=0.1元）
    total_gift_count: int = 0            # 总礼物次数
    total_sc_count: int = 0              # 总SC次数
    last_spend_time: float = 0           # 最后消费时间
    
    # 互动统计
    total_danmaku: int = 0               # 总弹幕数
    reply_count: int = 0                 # 被回复次数
    recent_messages: List[str] = field(default_factory=list)  # 最近5条消息
    avg_response_time: float = 0         # 平均回应时间（秒）
    
    # 标记与标签
    is_whitelist: bool = False           # 白名单用户
    is_blacklist: bool = False           # 黑名单用户
    tags: Set[str] = field(default_factory=set)  # 用户标签
    
    # 会话统计（当前直播）
    session_danmaku: int = 0             # 本次直播弹幕数
    session_battery: int = 0             # 本次直播消费电池
    
    @property
    def value_tier(self) -> int:
        """计算用户价值等级（0-6）"""
        if self.guard_level == 1:
            return 6  # 总督
        if self.guard_level == 2:
            return 5  # 提督
        if self.guard_level == 3:
            return 4  # 舰长
        if self.is_vip:
            return 3  # 老爷
        if self.fan_medal_level > 0:
            return 2  # 粉丝团
        if self.ul_level > 0:
            return 1  # 普通用户
        return 0      # 路人
    
    @property
    def value_weight(self) -> int:
        """计算用户价值权重（用于优先级排序）"""
        # 基础价值权重
        tier_weights = {
            0: 0,    # 路人
            1: max(1, self.ul_level // 5),  # 普通用户：每5级+1权重
            2: 20,   # 粉丝团
            3: 30,   # 老爷
            4: 100,  # 舰长
            5: 500,  # 提督
            6: 2000  # 总督
        }
        
        base_weight = tier_weights.get(self.value_tier, 0)
        
        # 消费加成：每1000电池（100元）+1权重
        spend_bonus = self.total_battery_spent // 1000
        
        # 互动加成：每100条弹幕+1权重
        interaction_bonus = self.total_danmaku // 100
        
        # 衰减因子：长时间未活跃的用户权重降低
        days_since_last_seen = (time.time() - self.last_seen) / 86400
        decay_factor = max(0.1, 1.0 - (days_since_last_seen / 90))  # 90天衰减到10%
        
        total_weight = int((base_weight + spend_bonus + interaction_bonus) * decay_factor)
        
        # 白名单用户额外加成
        if self.is_whitelist:
            total_weight *= 2
        
        return max(0, min(10000, total_weight))  # 限制在0-10000范围内
    
    @property
    def is_core_fan(self) -> bool:
        """判断是否为核心粉丝"""
        return self.value_tier >= 4 or self.total_battery_spent >= 10000  # 舰长以上或消费超1000元
    
    @property
    def is_high_value(self) -> bool:
        """判断是否为高价值用户"""
        return self.value_weight >= 100
    
    def update_from_event(self, event: Dict[str, Any]) -> None:
        """根据事件更新用户画像"""
        self.last_seen = time.time()
        
        event_type = event.get("type")
        
        if event_type == "danmaku":
            self.total_danmaku += 1
            self.session_danmaku += 1
            
            # 更新最近消息
            content = event.get("content", "")
            if content:
                self.recent_messages.append(content)
                if len(self.recent_messages) > 5:
                    self.recent_messages.pop(0)
            
            # 更新身份信息
            self.ul_level = max(self.ul_level, event.get("user_level", 0))
            self.fan_medal_level = max(self.fan_medal_level, event.get("medal_level", 0))
            self.fan_medal_name = event.get("medal_name", self.fan_medal_name)
            
        elif event_type == "gift":
            battery = event.get("battery", 0)
            self.total_battery_spent += battery
            self.session_battery += battery
            self.total_gift_count += 1
            self.last_spend_time = time.time()
            
        elif event_type == "superchat":
            battery = event.get("battery", 0)
            self.total_battery_spent += battery
            self.session_battery += battery
            self.total_sc_count += 1
            self.last_spend_time = time.time()
            
        elif event_type == "guard":
            self.guard_level = max(self.guard_level, event.get("guard_level", 0))
            battery = event.get("battery", 0)
            self.total_battery_spent += battery
            self.session_battery += battery
            self.last_spend_time = time.time()
    
    def add_tag(self, tag: str) -> None:
        """添加用户标签"""
        self.tags.add(tag)
    
    def remove_tag(self, tag: str) -> None:
        """移除用户标签"""
        self.tags.discard(tag)
    
    def has_tag(self, tag: str) -> bool:
        """检查是否有指定标签"""
        return tag in self.tags
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于JSON序列化）"""
        data = asdict(self)
        # 转换set为list
        data["tags"] = list(self.tags)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UserProfile:
        """从字典创建用户画像"""
        # 转换list为set
        if "tags" in data and isinstance(data["tags"], list):
            data["tags"] = set(data["tags"])
        return cls(**data)


class UserProfileStore:
    """用户画像存储管理器"""
    
    def __init__(self, data_dir: Path, max_cache_size: int = 10000):
        """
        初始化用户画像存储
        
        Args:
            data_dir: 数据存储目录
            max_cache_size: 内存缓存最大大小
        """
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # LRU内存缓存
        self.cache: OrderedDict[int, UserProfile] = OrderedDict()
        self.max_cache_size = max_cache_size
        
        # 分片存储配置
        self.shard_size = 10000  # 每个分片最多存储10000个用户
        
        # 异步锁
        self._lock = asyncio.Lock()
        
        # 脏数据标记
        self._dirty_profiles: Set[int] = set()
        self._save_task: Optional[asyncio.Task] = None
        
        # 自动保存配置
        self.auto_save_interval = 300  # 5分钟
        self.auto_save_batch_size = 100
        
        # 启动自动保存任务
        self._start_auto_save()
    
    def _get_shard_path(self, uid: int) -> Path:
        """获取用户所在分片文件路径"""
        shard_id = uid // self.shard_size
        return self.data_dir / f"profiles_shard_{shard_id}.json"
    
    def _start_auto_save(self) -> None:
        """启动自动保存任务"""
        async def auto_save_loop():
            while True:
                await asyncio.sleep(self.auto_save_interval)
                await self.save_dirty_profiles()
        
        self._save_task = asyncio.create_task(auto_save_loop())
    
    async def get_profile(self, uid: int) -> Optional[UserProfile]:
        """获取用户画像（优先从缓存，缓存不存在则从文件加载）"""
        async with self._lock:
            # 检查缓存
            if uid in self.cache:
                # 移动到最近使用位置
                profile = self.cache.pop(uid)
                self.cache[uid] = profile
                return profile
            
            # 从文件加载
            shard_path = self._get_shard_path(uid)
            if not shard_path.exists():
                return None
            
            try:
                # 异步读取文件
                def read_file():
                    with open(shard_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                
                data = await asyncio.to_thread(read_file)
                profiles_data = data.get("profiles", {})
                
                if str(uid) in profiles_data:
                    profile_data = profiles_data[str(uid)]
                    profile = UserProfile.from_dict(profile_data)
                    
                    # 加入缓存
                    self._add_to_cache(uid, profile)
                    return profile
            
            except Exception as e:
                print(f"加载用户画像失败 uid={uid}: {e}")
            
            return None
    
    async def get_or_create_profile(self, uid: int, name: str = "") -> UserProfile:
        """获取或创建用户画像"""
        profile = await self.get_profile(uid)
        if profile is None:
            profile = UserProfile(uid=uid, name=name)
            await self.save_profile(profile)
        elif name and profile.name != name:
            profile.name = name
            await self.save_profile(profile)
        
        return profile
    
    async def save_profile(self, profile: UserProfile) -> None:
        """保存用户画像（标记为脏数据，稍后批量保存）"""
        async with self._lock:
            # 更新缓存
            self._add_to_cache(profile.uid, profile)
            
            # 标记为脏数据
            self._dirty_profiles.add(profile.uid)
            
            # 如果脏数据达到批量大小，立即保存
            if len(self._dirty_profiles) >= self.auto_save_batch_size:
                await self._save_dirty_profiles_now()
    
    async def _save_dirty_profiles_now(self) -> None:
        """立即保存所有脏数据"""
        if not self._dirty_profiles:
            return
        
        async with self._lock:
            # 按分片分组
            shard_profiles: Dict[Path, Dict[str, Any]] = {}
            
            for uid in self._dirty_profiles:
                if uid in self.cache:
                    profile = self.cache[uid]
                    shard_path = self._get_shard_path(uid)
                    
                    if shard_path not in shard_profiles:
                        shard_profiles[shard_path] = {}
                    
                    shard_profiles[shard_path][str(uid)] = profile.to_dict()
            
            # 保存每个分片
            for shard_path, profiles_data in shard_profiles.items():
                await self._save_shard(shard_path, profiles_data)
            
            # 清空脏数据标记
            self._dirty_profiles.clear()
    
    async def _save_shard(self, shard_path: Path, new_profiles: Dict[str, Any]) -> None:
        """保存分片数据（合并更新）"""
        try:
            # 读取现有数据
            existing_data = {}
            if shard_path.exists():
                def read_existing():
                    with open(shard_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                existing_data = await asyncio.to_thread(read_existing)
            
            # 合并数据
            if "profiles" not in existing_data:
                existing_data["profiles"] = {}
            
            existing_data["profiles"].update(new_profiles)
            existing_data["_metadata"] = {
                "updated_at": time.time(),
                "profile_count": len(existing_data["profiles"])
            }
            
            # 写入文件
            def write_file():
                with open(shard_path, 'w', encoding='utf-8') as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            await asyncio.to_thread(write_file)
            
        except Exception as e:
            print(f"保存分片数据失败 {shard_path}: {e}")
    
    async def save_dirty_profiles(self) -> None:
        """保存所有脏数据（供外部调用）"""
        await self._save_dirty_profiles_now()

    async def save_all(self) -> None:
        """保存所有缓存的用户画像数据（关闭时调用）"""
        async with self._lock:
            # 将所有缓存用户标记为脏数据
            for uid in self.cache:
                self._dirty_profiles.add(uid)
        await self._save_dirty_profiles_now()
    
    def _add_to_cache(self, uid: int, profile: UserProfile) -> None:
        """添加用户画像到缓存"""
        if uid in self.cache:
            # 更新现有
            self.cache.pop(uid)
        
        self.cache[uid] = profile
        
        # 检查缓存大小
        if len(self.cache) > self.max_cache_size:
            # 移除最久未使用的
            self.cache.popitem(last=False)
    
    async def get_high_value_users(self, limit: int = 50) -> List[UserProfile]:
        """获取高价值用户列表"""
        async with self._lock:
            # 从缓存中获取所有用户
            profiles = list(self.cache.values())
            
            # 按价值权重排序
            profiles.sort(key=lambda p: p.value_weight, reverse=True)
            
            return profiles[:limit]
    
    async def search_users(self, keyword: str, limit: int = 20) -> List[UserProfile]:
        """搜索用户（按UID或名称）"""
        async with self._lock:
            results = []
            
            for profile in self.cache.values():
                if (keyword in str(profile.uid) or 
                    keyword.lower() in profile.name.lower()):
                    results.append(profile)
                
                if len(results) >= limit:
                    break
            
            return results
    
    async def clear_cache(self) -> None:
        """清空缓存"""
        async with self._lock:
            self.cache.clear()
    
    async def shutdown(self) -> None:
        """关闭存储管理器（保存所有数据）"""
        if self._save_task:
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        
        # 保存所有脏数据
        await self.save_dirty_profiles()
    
    @property
    def cache_size(self) -> int:
        """获取缓存大小"""
        return len(self.cache)
    
    @property
    def dirty_count(self) -> int:
        """获取脏数据数量"""
        return len(self._dirty_profiles)


# 全局用户画像存储实例
_global_profile_store: Optional[UserProfileStore] = None

def get_global_profile_store(data_dir: Optional[Path] = None) -> UserProfileStore:
    """获取全局用户画像存储实例"""
    global _global_profile_store
    
    if _global_profile_store is None:
        if data_dir is None:
            # 默认使用插件data目录
            plugin_root = Path(__file__).parent
            data_dir = plugin_root / "data" / "user_profiles"
        
        _global_profile_store = UserProfileStore(data_dir)
    
    return _global_profile_store