"""
Result 类型模块

提供类似 Rust 的 Result<T, E> 类型，用于显式错误处理。
支持 Python 3.10+ 的 pattern matching。

Usage:
    from plugin._types import Ok, Err, Result, safe, async_safe
    
    @async_safe
    async def fetch_user(user_id: int) -> dict:
        response = await http_client.get(f"/users/{user_id}")
        return response.json()
    
    # Pattern matching
    match await fetch_user(1):
        case Ok(user):
            return user["name"]
        case Err(e):
            logger.error(f"Failed: {e}")
            return "Unknown"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, NoReturn, Optional, TypeVar, Union
from functools import wraps

from .errors import ErrorCode

T = TypeVar('T')
U = TypeVar('U')
E = TypeVar('E', bound=Exception)


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """成功结果
    
    包装成功的返回值，支持链式操作和 pattern matching。
    
    Attributes:
        value: 成功的值
    
    Example:
        >>> result = Ok(42)
        >>> result.unwrap()
        42
        >>> result.map(lambda x: x * 2).unwrap()
        84
    """
    value: T
    
    def __bool__(self) -> bool:
        """Ok 总是 truthy"""
        return True
    
    def is_ok(self) -> bool:
        """检查是否成功"""
        return True
    
    def is_err(self) -> bool:
        """检查是否失败"""
        return False
    
    def unwrap(self) -> T:
        """获取值，如果是 Err 则抛出异常"""
        return self.value
    
    def unwrap_or(self, default: T) -> T:
        """获取值，如果是 Err 则返回默认值"""
        return self.value
    
    def unwrap_or_else(self, fn: Callable[[], T]) -> T:
        """获取值，如果是 Err 则调用函数获取默认值"""
        return self.value
    
    def map(self, fn: Callable[[T], U]) -> "Ok[U]":
        """对值应用函数"""
        return Ok(fn(self.value))
    
    def map_err(self, fn: Callable[[Any], Any]) -> "Ok[T]":
        """对错误应用函数（Ok 不变）"""
        return self
    
    def and_then(self, fn: Callable[[T], "Result[U, Any]"]) -> "Result[U, Any]":
        """链式调用，如果成功则应用函数"""
        return fn(self.value)
    
    def or_else(self, fn: Callable[[Any], "Result[T, Any]"]) -> "Ok[T]":
        """链式调用，如果失败则应用函数（Ok 不变）"""
        return self
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "success": True,
            "code": int(ErrorCode.SUCCESS),
            "data": self.value,
        }


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    """错误结果
    
    包装错误信息，支持链式操作和 pattern matching。
    
    Attributes:
        error: 错误对象（通常是 Exception）
        code: 错误码
        message: 可选的错误消息
    
    Example:
        >>> result = Err(ValueError("invalid input"), ErrorCode.INVALID_PARAMS)
        >>> result.unwrap_or("default")
        'default'
    """
    error: E
    code: ErrorCode = ErrorCode.INTERNAL
    message: Optional[str] = None
    
    def __bool__(self) -> bool:
        """Err 总是 falsy"""
        return False
    
    def is_ok(self) -> bool:
        """检查是否成功"""
        return False
    
    def is_err(self) -> bool:
        """检查是否失败"""
        return True
    
    def unwrap(self) -> NoReturn:
        """获取值，Err 总是抛出异常"""
        raise ResultError(self.code, self.message or str(self.error), self.error) from self.error
    
    def unwrap_or(self, default: T) -> T:
        """获取值，Err 返回默认值"""
        return default
    
    def unwrap_or_else(self, fn: Callable[[], T]) -> T:
        """获取值，Err 调用函数获取默认值"""
        return fn()
    
    def map(self, fn: Callable[[Any], Any]) -> "Err[E]":
        """对值应用函数（Err 不变）"""
        return self
    
    def map_err(self, fn: Callable[[E], U]) -> "Err[U]":
        """对错误应用函数"""
        return Err(fn(self.error), self.code, self.message)
    
    def and_then(self, fn: Callable[[Any], "Result[Any, E]"]) -> "Err[E]":
        """链式调用（Err 不变）"""
        return self
    
    def or_else(self, fn: Callable[[E], "Result[T, Any]"]) -> "Result[T, Any]":
        """链式调用，如果失败则应用函数"""
        return fn(self.error)
    
    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "success": False,
            "code": int(self.code),
            "error": str(self.error),
        }
        if self.message:
            result["message"] = self.message
        return result


# 类型别名
Result = Union[Ok[T], Err[E]]


class ResultError(Exception):
    """Result.unwrap() 失败时抛出的异常"""
    
    def __init__(self, code: ErrorCode, message: str, original: Optional[Exception] = None):
        self.code = code
        self.message = message
        self.original = original
        super().__init__(f"[{code.name}] {message}")


# ========== 装饰器 ==========

def safe(fn: Callable[..., T]) -> Callable[..., Result[T, Exception]]:
    """将可能抛异常的函数转为返回 Result 的函数
    
    Usage:
        @safe
        def divide(a: int, b: int) -> float:
            return a / b
        
        result = divide(10, 2)  # Ok(5.0)
        result = divide(10, 0)  # Err(ZeroDivisionError)
    """
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Result[T, Exception]:
        try:
            return Ok(fn(*args, **kwargs))
        except Exception as e:
            return Err(e)
    
    return wrapper


def async_safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """将可能抛异常的异步函数转为返回 Result 的函数
    
    Usage:
        @async_safe
        async def fetch_user(user_id: int) -> dict:
            response = await http_client.get(f"/users/{user_id}")
            return response.json()
        
        result = await fetch_user(1)  # Ok({"name": "John"}) or Err(...)
    """
    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Result[Any, Exception]:
        try:
            return Ok(await fn(*args, **kwargs))
        except Exception as e:
            return Err(e)
    
    return wrapper


# ========== 工具函数 ==========

def try_call(fn: Callable[..., T], *args, **kwargs) -> Result[T, Exception]:
    """尝试调用函数，返回 Result"""
    try:
        return Ok(fn(*args, **kwargs))
    except Exception as e:
        return Err(e)


async def try_call_async(fn: Callable[..., T], *args, **kwargs) -> Result[T, Exception]:
    """尝试调用异步函数，返回 Result"""
    try:
        return Ok(await fn(*args, **kwargs))
    except Exception as e:
        return Err(e)


def from_optional(value: Optional[T], error: E) -> Result[T, E]:
    """将 Optional 转换为 Result"""
    if value is None:
        return Err(error)
    return Ok(value)


def collect_results(results: list[Result[T, E]]) -> Result[list[T], E]:
    """收集多个 Result，如果有任何 Err 则返回第一个 Err"""
    values = []
    for r in results:
        match r:
            case Ok(v):
                values.append(v)
            case Err() as err:
                return err
    return Ok(values)


# ========== 导出 ==========

__all__ = [
    # 类型
    "Ok",
    "Err", 
    "Result",
    "ErrorCode",
    "ResultError",
    # 装饰器
    "safe",
    "async_safe",
    # 工具函数
    "try_call",
    "try_call_async",
    "from_optional",
    "collect_results",
]
