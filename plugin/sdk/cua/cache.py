"""CUA (Computer-Use Agent) path cache helper.

从 ``ComputerUseAdapter.cots`` 中提取 pyautogui 关键坐标，
存到插件 store，下次同 ``cache_key`` 的调用自动注入缓存提示前缀，
让 VLM 优先尝试已知坐标，减少截图→推理轮次（典型 15 步 → 3-5 步）。

设计原则：
- **duck typing**：不 import ``brain.computer_use``，只要求传入的对象有
  ``.cots`` 属性和 ``.run_instruction()`` 方法
- **graceful degradation**：store 不可用 / cots 为空 / 坐标提取失败 →
  静默跳过缓存，完整走 CUA 流程
- **单一职责**：只做路径缓存，不包 CUA 错误处理（由调用方负责）

用法 ::

    from plugin.sdk.cua.cache import CuaPathCache

    cache = CuaPathCache(store=self.store, key="weibo_post")

    # 等价于直接调用 cua.run_instruction(instruction)，
    # 但自动注入/记录路径缓存
    result = await cache.run(cua, instruction)

    # 也可以手动分步
    hint = await cache.build_hint()           # 读缓存 → 构建提示前缀
    full_instruction = hint + instruction
    result = cua.run_instruction(full_instruction)
    await cache.record(cua, result)           # 执行后 → 记录新缓存
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

# pyautogui 坐标提取正则——匹配 click/doubleClick/rightClick/moveTo 调用
# 坐标范围 [0, 999]（CUA 内部坐标系，不是屏幕像素）
_COORD_RE = re.compile(
    r"pyautogui\.(?:click|doubleClick|rightClick|moveTo)\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})"
)

# 缓存 prompt 模板——注入到 instruction 开头
_CACHE_HINT_TEMPLATE = """\
【路径缓存提示】
上次此操作已成功 {success_count} 次。请尽量复用已知坐标。
已知关键坐标（如果截图发现位置不匹配请重新定位）：
{coords_text}

"""

# 缓存 prompt 模板——首次（无历史）
_CACHE_HINT_FIRST_TEMPLATE = """\
【路径缓存提示】
上次此操作已成功 {success_count} 次。

"""


@dataclass(frozen=True)
class CuaCacheEntry:
    """单个坐标点的缓存条目。"""

    x: int
    y: int


@dataclass
class CuaCache:
    """完整的路径缓存记录（store 中存的 JSON 结构）。"""

    platform: str
    task_type: str
    key_coords: List[CuaCacheEntry]
    last_success_at: str  # ISO date string
    success_count: int
    last_steps: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "task_type": self.task_type,
            "key_coords": [{"x": c.x, "y": c.y} for c in self.key_coords],
            "last_success_at": self.last_success_at,
            "success_count": self.success_count,
            "last_steps": self.last_steps,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CuaCache":
        coords: List[CuaCacheEntry] = []
        for c in data.get("key_coords", []):
            if isinstance(c, dict):
                try:
                    coords.append(CuaCacheEntry(x=int(c["x"]), y=int(c["y"])))
                except (KeyError, ValueError, TypeError):
                    continue
        return cls(
            platform=str(data.get("platform", "")),
            task_type=str(data.get("task_type", "")),
            key_coords=coords,
            last_success_at=str(data.get("last_success_at", "")),
            success_count=int(data.get("success_count", 0)),
            last_steps=int(data.get("last_steps", 0)),
        )


class CuaPathCache:
    """CUA 路径缓存 helper。

    Parameters
    ----------
    store:
        插件的 ``self.store`` 对象。duck typing：需要异步的
        ``get(key)`` 和 ``set(key, value)`` 方法。
        兼容两种返回模式：
        - 官方 SDK ``PluginStore``：返回 ``Result[T, E]``（Ok/Err 包装）
        - 直接返回值：返回原始值
    key:
        缓存标识，格式建议 ``platform:task_type``（如 ``weibo_web:post``）。
        作为 store key 的一部分。
    platform / task_type:
        可选元数据，用于缓存 JSON 记录。如果不传，从 ``key`` 拆分。
    expiry_days:
        缓存过期天数，默认 7。超过此天数视为过期，重新走完整 VLM 流程。
    max_coords:
        最多缓存多少个坐标点，默认 12（太多会让 VLM 提示过长）。
    """

    def __init__(
        self,
        store: Any,
        key: str,
        *,
        platform: str = "",
        task_type: str = "",
        expiry_days: int = 7,
        max_coords: int = 12,
    ):
        self._store = store
        self._key = key
        parts = key.split(":", 1)
        self._platform = platform or (parts[0] if len(parts) > 1 else "")
        self._task_type = task_type or (parts[1] if len(parts) > 1 else key)
        self._expiry_days = max(1, int(expiry_days))
        self._max_coords = max(1, int(max_coords))

    # ── store 读写（兼容 Result[T, E] 和直接返回值两种模式） ──────────

    _STORE_KEY_PREFIX = "cua_path_cache"

    @property
    def _store_key(self) -> str:
        return f"{self._STORE_KEY_PREFIX}:{self._key}"

    async def _store_get(self, key: str) -> Any:
        """读 store，兼容 Result 包装和直接值。"""
        if self._store is None:
            return None
        try:
            result = await self._store.get(key)
        except Exception:
            return None
        # 官方 SDK 返回 Result[T, E]
        if hasattr(result, "is_ok") and hasattr(result, "value"):
            if result.is_ok():
                return result.value
            return None
        # 直接值（或 None）
        return result

    async def _store_set(self, key: str, value: Any) -> bool:
        """写 store，兼容两种模式。返回是否成功。"""
        if self._store is None:
            return False
        try:
            await self._store.set(key, value)
        except Exception:
            return False
        return True

    # ── 坐标提取 ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_coords_from_cots(
        cots: List[Dict[str, str]], *, max_coords: int = 12
    ) -> List[CuaCacheEntry]:
        """从 CUA step history（cots）中提取 pyautogui 坐标。

        每个 cot 是 ``{"thought": ..., "action": ..., "code": "..."}``。
        code 里的 pyautogui.click(x, y) 坐标就是路径缓存。
        """
        coords: List[CuaCacheEntry] = []
        seen: set[tuple[int, int]] = set()
        for cot in cots:
            code = cot.get("code", "") if isinstance(cot, dict) else ""
            for m in _COORD_RE.finditer(code):
                try:
                    x, y = int(m.group(1)), int(m.group(2))
                except ValueError:
                    continue
                if (x, y) not in seen:
                    seen.add((x, y))
                    coords.append(CuaCacheEntry(x=x, y=y))
                    if len(coords) >= max_coords:
                        return coords
        return coords

    # ── 缓存读取 ──────────────────────────────────────────────────────

    async def load(self) -> Optional[CuaCache]:
        """从 store 读取缓存，自动过期检查。过期或不存在返回 None。"""
        raw = await self._store_get(self._store_key)
        if not raw:
            return None
        try:
            data = raw
            if isinstance(raw, str):
                data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            cache = CuaCache.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

        # 过期检查
        if cache.last_success_at:
            try:
                last = datetime.fromisoformat(cache.last_success_at).date()
                if (date.today() - last) > timedelta(days=self._expiry_days):
                    return None
            except ValueError:
                pass
        return cache

    # ── 缓存提示构建 ───────────────────────────────────────────────────

    @staticmethod
    def build_hint_text(cache: Optional[CuaCache]) -> str:
        """把缓存转成 instruction 前缀文本。

        VLM 看到已知坐标后会优先尝试，减少截图→推理的轮次。
        如果坐标不匹配（窗口位置变了），VLM 会自行重新定位。
        """
        if cache is None:
            return ""

        coords_text_lines: List[str] = []
        for i, c in enumerate(cache.key_coords, 1):
            coords_text_lines.append(f"  {i}. ({c.x}, {c.y})")

        if coords_text_lines:
            return _CACHE_HINT_TEMPLATE.format(
                success_count=cache.success_count,
                coords_text="\n".join(coords_text_lines),
            )
        # 有缓存记录但坐标为空——仍提示"之前成功过"，让 VLM 知道路径曾有效
        return _CACHE_HINT_FIRST_TEMPLATE.format(success_count=cache.success_count)

    async def build_hint(self) -> str:
        """读缓存并构建提示前缀（便捷方法）。"""
        cache = await self.load()
        return self.build_hint_text(cache)

    # ── 缓存记录 ───────────────────────────────────────────────────────

    async def record(
        self,
        cua: Any,
        result: Dict[str, Any],
    ) -> Optional[CuaCache]:
        """从 CUA 结果和 cots 中提取坐标，更新缓存。

        仅当 result["success"]==True 时记录。
        """
        if not result or not bool(result.get("success")):
            return None

        cots = getattr(cua, "cots", None)
        if not isinstance(cots, list):
            return None

        coords = self._extract_coords_from_cots(cots, max_coords=self._max_coords)
        if not coords:
            return None

        # 读取旧缓存累加 success_count
        old = await self.load()
        old_count = old.success_count if old else 0

        cache = CuaCache(
            platform=self._platform,
            task_type=self._task_type,
            key_coords=coords,
            last_success_at=date.today().isoformat(),
            success_count=old_count + 1,
            last_steps=int(result.get("steps", 0)),
        )

        ok = await self._store_set(self._store_key, cache.to_dict())
        return cache if ok else None

    # ── 一键执行（读缓存 → 注入 → 跑 CUA → 写缓存） ────────────────────

    async def run(self, cua: Any, instruction: str) -> Dict[str, Any]:
        """完整跑一遍 CUA + 路径缓存。

        等价于：
        1. ``build_hint()`` → 注入缓存提示
        2. ``cua.run_instruction(hint + instruction)`` → 执行
        3. ``record(cua, result)`` → 更新缓存

        如果 store 不可用或 cots 为空，静默退化到直接 run_instruction。
        """
        hint = await self.build_hint()
        full_instruction = hint + instruction if hint else instruction

        # duck typing：cua.run_instruction(instruction) 同步阻塞
        result = cua.run_instruction(full_instruction)

        # 异步记录缓存（不阻塞返回）
        try:
            await self.record(cua, result)
        except Exception:
            pass

        return result


# ── 便捷导出 ────────────────────────────────────────────────────────

__all__ = [
    "CuaPathCache",
    "CuaCache",
    "CuaCacheEntry",
]
