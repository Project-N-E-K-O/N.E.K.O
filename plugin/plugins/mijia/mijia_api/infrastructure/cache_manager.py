"""缓存管理器

支持多层缓存策略：
- L1: 内存缓存（TTLCache/LRUCache）- 最快
- L2: Redis 缓存（可选）- 分布式共享
- L3: 文件缓存 - 持久化

当 Redis 配置存在时，使用三层缓存；否则使用内存+文件两层缓存。
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from cachetools import TTLCache

from ..core.logging import get_logger

logger = get_logger(__name__)


class CacheManager:
    """缓存管理器

    支持多层缓存策略：
    - L1: 内存缓存（TTLCache/LRUCache）- 最快
    - L2: Redis 缓存（可选）- 分布式共享
    - L3: 文件缓存 - 持久化

    当 Redis 配置存在时，使用三层缓存；否则使用内存+文件两层缓存。
    Redis 不可用时自动降级到内存+文件缓存。
    """

    def __init__(
        self, cache_dir: Optional[Path] = None, redis_client: Optional[Any] = None
    ) -> None:
        """初始化缓存管理器

        Args:
            cache_dir: 文件缓存目录，默认为 ~/.mijia/cache
            redis_client: Redis客户端实例（可选），如果提供则启用L2缓存
        """
        # L1: 内存缓存 - 设备列表（5分钟TTL）
        self._device_cache: TTLCache = TTLCache(maxsize=1000, ttl=300)
        # L1: 内存缓存 - 设备状态（30秒TTL）
        self._state_cache: TTLCache = TTLCache(maxsize=5000, ttl=30)

        # L2: Redis 缓存（可选）
        self._redis_client = redis_client

        # L3: 文件缓存目录
        self._cache_dir = cache_dir or Path.home() / ".mijia" / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # 缓存统计
        self._stats: Dict[str, int] = {
            "l1_hits": 0,  # L1 内存缓存命中
            "l2_hits": 0,  # L2 Redis 缓存命中
            "l3_hits": 0,  # L3 文件缓存命中
            "misses": 0,  # 完全未命中
        }

    def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """获取缓存（多层查找）

        查找顺序：L1 内存 -> L2 Redis -> L3 文件

        Args:
            key: 缓存键
            namespace: 命名空间，用于多用户隔离，默认为"default"

        Returns:
            缓存值，不存在返回None
        """
        full_key = f"{namespace}:{key}"

        # L1: 查内存缓存
        l1_value = self._get_from_memory(full_key)
        if l1_value is not None:
            self._stats["l1_hits"] += 1
            return l1_value

        # L2: 查 Redis 缓存（如果配置）
        l2_value = self._get_from_redis(full_key)
        if l2_value is not None:
            self._stats["l2_hits"] += 1
            self._set_memory_cache(full_key, l2_value, ttl=300)
            return l2_value

        # L3: 查文件缓存
        l3_value = self._get_from_file(full_key)
        if l3_value is not None:
            self._stats["l3_hits"] += 1
            self._backfill_cache(full_key, l3_value)
            return l3_value

        self._stats["misses"] += 1
        return None

    def _get_from_memory(self, full_key: str) -> Optional[Any]:
        """从内存缓存获取值

        Args:
            full_key: 完整的缓存键（包含命名空间）

        Returns:
            缓存值，不存在返回None
        """
        if full_key in self._device_cache:
            return self._device_cache[full_key]
        if full_key in self._state_cache:
            return self._state_cache[full_key]
        return None

    def _get_from_redis(self, full_key: str) -> Optional[Any]:
        """从Redis缓存获取值

        Args:
            full_key: 完整的缓存键（包含命名空间）

        Returns:
            缓存值，不存在或失败返回None
        """
        if not self._redis_client:
            return None

        try:
            return self._redis_client.get(full_key)
        except Exception as e:
            logger.warning(f"Redis 读取失败: {e}", extra={"key": full_key})
            return None

    def _get_from_file(self, full_key: str) -> Optional[Any]:
        """从文件缓存获取值

        Args:
            full_key: 完整的缓存键（包含命名空间）

        Returns:
            缓存值，不存在或失败返回None
        """
        return self._load_from_file(full_key)

    def _backfill_cache(self, full_key: str, value: Any) -> None:
        """回填缓存到上层

        Args:
            full_key: 完整的缓存键（包含命名空间）
            value: 缓存值
        """
        # 回填到 L1
        self._set_memory_cache(full_key, value, ttl=300)

        # 回填到 L2（如果配置）
        if self._redis_client:
            try:
                self._redis_client.set(full_key, value, ttl=3600)
            except Exception as e:
                logger.warning(f"Redis 回填失败: {e}", extra={"key": full_key})

    def set(self, key: str, value: Any, ttl: int = 300, namespace: str = "default") -> None:
        """设置缓存（写入所有层）

        Args:
            key: 缓存键
            value: 缓存值
            ttl: 生存时间（秒），默认300秒
            namespace: 命名空间，用于多用户隔离，默认为"default"
        """
        full_key = f"{namespace}:{key}"

        # L1: 写入内存缓存
        self._set_memory_cache(full_key, value, ttl)

        # L2: 写入 Redis（如果配置）
        if self._redis_client:
            try:
                self._redis_client.set(full_key, value, ttl=ttl)
            except Exception as e:
                # Redis 写入失败不影响主流程
                logger.warning(f"Redis 写入失败: {e}", extra={"key": full_key})

        # L3: 长期缓存写入文件
        if ttl > 300:
            self._save_to_file(full_key, value)

    def _set_memory_cache(self, full_key: str, value: Any, ttl: int) -> None:
        """写入内存缓存

        根据TTL选择合适的缓存类型：
        - TTL <= 30秒：使用state_cache（设备状态），固定30秒过期
        - TTL > 30秒：使用device_cache（设备列表），固定300秒过期

        注意：传入的ttl参数仅用于选择缓存类型，实际的缓存过期时间
        由缓存实例初始化时固定（state_cache=30秒, device_cache=300秒）。

        Args:
            full_key: 完整的缓存键（包含命名空间）
            value: 缓存值
            ttl: 生存时间（秒），仅用于选择缓存类型
        """
        if ttl <= 30:
            self._state_cache[full_key] = value
        else:
            self._device_cache[full_key] = value

    def invalidate(self, key: str, namespace: str = "default") -> None:
        """失效单个缓存（所有层）

        Args:
            key: 缓存键
            namespace: 命名空间，默认为"default"
        """
        full_key = f"{namespace}:{key}"

        # L1: 清除内存缓存
        self._device_cache.pop(full_key, None)
        self._state_cache.pop(full_key, None)

        # L2: 清除 Redis 缓存
        if self._redis_client:
            try:
                self._redis_client.delete(full_key)
            except Exception as e:
                logger.warning(f"Redis 删除失败: {e}", extra={"key": full_key})

    def invalidate_pattern(self, pattern: str) -> None:
        """失效匹配模式的所有缓存

        Args:
            pattern: 匹配模式，支持部分匹配
        """
        # L1: 清除内存缓存
        keys_to_remove = [k for k in self._device_cache.keys() if pattern in k]
        for key in keys_to_remove:
            self._device_cache.pop(key, None)

        keys_to_remove = [k for k in self._state_cache.keys() if pattern in k]
        for key in keys_to_remove:
            self._state_cache.pop(key, None)

        # L2: 清除 Redis 缓存（如果配置）
        if self._redis_client:
            try:
                self._redis_client.delete_pattern(pattern)
            except Exception as e:
                logger.warning(f"Redis 批量删除失败: {e}", extra={"pattern": pattern})

    def clear(self, namespace: Optional[str] = None) -> None:
        """清空缓存

        Args:
            namespace: 命名空间，如果指定则只清空该命名空间的缓存，否则清空所有缓存
        """
        if namespace:
            # 清空指定命名空间
            self.invalidate_pattern(f"{namespace}:")
        else:
            # 清空所有缓存
            self._device_cache.clear()
            self._state_cache.clear()

            # 清空 Redis（按前缀删除，避免误删其他应用数据）
            if self._redis_client:
                try:
                    # 使用 SCAN + DEL 按前缀删除，而不是危险的 flushdb
                    # 缓存键格式为 {namespace}:{key}，使用 *:* 模式匹配所有
                    pattern = "*:*"
                    cursor = 0
                    while True:
                        cursor, keys = self._redis_client.scan(cursor, match=pattern, count=100)
                        if keys:
                            self._redis_client.delete(*keys)
                        if cursor == 0:
                            break
                except Exception as e:
                    logger.warning(f"Redis 清空失败: {e}")

            logger.info("所有缓存已清空")

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计

        Returns:
            包含缓存统计信息的字典，包括：
            - l1_hits: L1缓存命中次数
            - l2_hits: L2缓存命中次数
            - l3_hits: L3缓存命中次数
            - misses: 未命中次数
            - total_hits: 总命中次数
            - hit_rate: 命中率（百分比）
            - device_cache_size: 设备缓存大小
            - state_cache_size: 状态缓存大小
            - redis_enabled: Redis是否启用
            - redis_info: Redis信息（如果可用）
        """
        total = (
            self._stats["l1_hits"]
            + self._stats["l2_hits"]
            + self._stats["l3_hits"]
            + self._stats["misses"]
        )
        hit_rate = (
            (self._stats["l1_hits"] + self._stats["l2_hits"] + self._stats["l3_hits"]) / total
            if total > 0
            else 0
        )

        stats: Dict[str, Any] = {
            "l1_hits": self._stats["l1_hits"],
            "l2_hits": self._stats["l2_hits"],
            "l3_hits": self._stats["l3_hits"],
            "misses": self._stats["misses"],
            "total_hits": self._stats["l1_hits"] + self._stats["l2_hits"] + self._stats["l3_hits"],
            "hit_rate": f"{hit_rate:.2%}",
            "device_cache_size": len(self._device_cache),
            "state_cache_size": len(self._state_cache),
            "redis_enabled": self._redis_client is not None,
        }

        # 如果 Redis 可用，添加 Redis 统计
        if self._redis_client:
            try:
                stats["redis_info"] = self._redis_client.get_info()
            except Exception:
                stats["redis_info"] = "不可用"

        return stats

    def _load_from_file(self, key: str) -> Optional[Any]:
        """从文件加载缓存

        Args:
            key: 缓存键

        Returns:
            缓存值，不存在或加载失败返回None
        """
        file_path = self._cache_dir / self._hash_key(key)
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"文件缓存加载失败: {e}", extra={"key": key})
                return None
        return None

    def _save_to_file(self, key: str, value: Any, ttl: int = 3600) -> None:
        """保存缓存到文件

        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒）
        """
        import time

        file_path = self._cache_dir / self._hash_key(key)
        try:
            # 包装值和过期时间
            wrapped = {
                "_value": value,
                "_expires_at": time.time() + ttl,
                "_ttl": ttl,
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(wrapped, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"文件缓存保存失败: {e}", extra={"key": key})

    def _hash_key(self, key: str) -> str:
        """对key进行哈希

        使用MD5哈希将缓存键转换为文件名安全的字符串。

        Args:
            key: 缓存键

        Returns:
            哈希后的字符串
        """
        return hashlib.md5(key.encode()).hexdigest()
