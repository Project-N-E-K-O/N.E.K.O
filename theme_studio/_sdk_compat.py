# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""主题工坊 —— 插件 SDK 的最小等价替身。

主题工坊原先是一个 N.E.K.O 插件：业务代码写成 ``NekoPluginBase`` 的子类，方法上挂
``@plugin_entry``，由插件宿主（48916 端口）负责路由与结果封装。移植进本体以后不再
有宿主，但业务代码（``theme_studio/service.py``，约 5000 行）本身与 SDK 的耦合极浅
——只用到 ``Ok`` / ``Err`` / ``SdkError`` 这层返回值包装、几个装饰器，以及 ``logger``。

本模块提供**行为等价的最小子集**，让业务代码保持原样可读：

* ``Ok`` / ``Err`` —— 结果容器，``theme_studio.api`` 负责拆包成 HTTP 响应。
* ``SdkError`` —— 业务错误（消息会原样回给面板，是给用户看的中文提示）。
* ``plugin_entry`` / ``lifecycle`` / ``ui`` / ``neko_plugin`` / ``tr`` —— 只做注册与
  元数据收集的装饰器，不再参与运行时调度。

这里刻意不复刻宿主的进度上报 / 事件 / 权限体系：主题工坊从未使用它们。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

__all__ = [
    "ENTRY_REGISTRY",
    "Err",
    "NekoPluginBase",
    "Ok",
    "SdkError",
    "lifecycle",
    "neko_plugin",
    "plugin_entry",
    "tr",
    "ui",
]


class SdkError(Exception):
    """业务错误。``str(err)`` 就是要展示给用户的中文原因。"""

    def __init__(self, message: str = "", *, code: str = "sdk_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:  # pragma: no cover - 直接继承也可以，仅为可读性
        return self.message or self.code


class _Result:
    """``Ok`` / ``Err`` 的共同基类。"""

    __slots__ = ("value", "error")

    def __init__(self, value: Any = None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        if self.ok:
            return f"Ok({self.value!r})"
        return f"Err({self.error!r})"


class Ok(_Result):
    """成功结果。"""

    def __init__(self, value: Any = None) -> None:
        super().__init__(value=value, error=None)


class Err(_Result):
    """失败结果。参数既可以是 ``SdkError``，也可以是普通异常。"""

    def __init__(self, error: Exception | str | None = None) -> None:
        if isinstance(error, str):
            error = SdkError(error)
        elif error is None:
            error = SdkError("未知错误")
        super().__init__(value=None, error=error)


#: entry_id -> 处理函数。``theme_studio.api`` 用它做通用调度。
ENTRY_REGISTRY: dict[str, Callable[..., Any]] = {}


def _noop_decorator(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
    def wrap(obj: Any) -> Any:
        return obj

    return wrap


def tr(key: str, default: str | None = None, **_kwargs: Any) -> str:
    """翻译占位。主题工坊只有中文文案，直接返回 ``default``（缺省回退 key）。"""
    if default:
        return default
    return key


def neko_plugin(cls: Any) -> Any:
    """类装饰器占位。本体不再需要「把这个类交给宿主」。"""
    return cls


def plugin_entry(*_args: Any, **kwargs: Any) -> Callable[[Any], Any]:
    """方法装饰器：把 ``id=`` 与函数登记进 :data:`ENTRY_REGISTRY`。"""

    entry_id = str(kwargs.get("id") or "")

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        if entry_id:
            ENTRY_REGISTRY[entry_id] = fn
            setattr(fn, "__entry_id__", entry_id)
        return fn

    return wrap


def lifecycle(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
    return _noop_decorator()


class _UiNamespace:
    """``ui.context`` / ``ui.panel`` 等装饰器的占位命名空间。"""

    @staticmethod
    def context(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
        return _noop_decorator()

    @staticmethod
    def panel(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
        return _noop_decorator()


ui = _UiNamespace()


class NekoPluginBase:
    """最小基类：提供 ``logger`` 与文件日志能力。"""

    plugin_id: str = "neko_theme_studio"

    def __init__(self, ctx: Any = None) -> None:
        self.ctx = ctx
        self.logger: logging.Logger = getattr(ctx, "logger", None) or logging.getLogger(
            "neko.theme_studio"
        )

    def enable_file_logging(self, log_level: str = "INFO") -> logging.Logger:
        """等价替身：返回标准库 logger，级别随参数调整。"""
        logger = logging.getLogger("neko.theme_studio")
        level = getattr(logging, str(log_level).upper(), logging.INFO)
        try:
            logger.setLevel(level)
        except Exception:  # noqa: BLE001 - 日志级别异常不该影响功能
            pass
        return logger