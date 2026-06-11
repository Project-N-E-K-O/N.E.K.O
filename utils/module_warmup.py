# -*- coding: utf-8 -*-
"""启动后台模块预热。

启动链只 import greeting 真正需要的东西；重型 SDK（google-genai + 它捎带的
mcp、translatepy 等）改成首次使用时才 lazy import。本模块在服务 ready 之后起
一个 daemon thread，把这些"首次使用"的 import 提前在后台跑掉，让用户真正用到
时不必在交互中途承担 import 延迟。

GIL 说明：纯 Python 模块在解析期间持 GIL，但这里的大头是 C 扩展 dlopen（会释放
GIL）和文件 IO，而事件循环大多在 await IO，所以低优先级 daemon thread 能见缝插针
推进、不至于卡住 loop。这是 best-effort 预热而非正确性路径——任何失败都吞掉，
首次使用时的 lazy import 才是唯一真相来源。
"""
from __future__ import annotations

import importlib
import os
import threading
import time

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# main_server 进程 ready 后要预热的重模块。genai 会在 import 时捎带 mcp，
# 所以列了 genai 就不必单列 mcp。translatepy 的子翻译器各自有数据表，逐个列出
# 让它们都进 sys.modules 缓存。
MAIN_SERVER_WARMUP: tuple[str, ...] = (
    "google.genai",
    "google.genai.types",
    "translatepy",
    "translatepy.translators.microsoft",
    "translatepy.translators.bing",
    "translatepy.translators.reverso",
    "translatepy.translators.libre",
    "translatepy.translators.mymemory",
    "translatepy.translators.translatecom",
    "googletrans",
    # 功能路由的重依赖（声音克隆 TTS / 网易云 / 网页抓取 / B 站），从各 router /
    # util 模块顶层下放到 handler 后，在这里预热，保证首次点功能时不等 import。
    "dashscope",
    "dashscope.audio.tts_v2",
    "pyncm_async",
    "bs4",
    "bilibili_api",
)

_warmup_lock = threading.Lock()
_warmup_started = False


def start_background_warmup(modules, *, label: str = "server") -> bool:
    """起一个 daemon thread 预热 ``modules``，进程级只跑一次。

    返回是否真正启动了线程（已经跑过则返回 ``False``）。
    """
    # 测试环境下不预热：daemon 线程跑真实重 import 既拖慢测试、又会在测试 logging
    # 拆除后回写日志报错，且预热是纯优化无行为契约，跳过完全安全。
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False

    global _warmup_started
    with _warmup_lock:
        if _warmup_started:
            return False
        _warmup_started = True

    module_list = tuple(modules)

    def _run() -> None:
        t0 = time.monotonic()
        loaded = 0
        for name in module_list:
            start = time.monotonic()
            try:
                importlib.import_module(name)
                loaded += 1
                logger.debug(
                    "[warmup:%s] %s (%.0f ms)",
                    label, name, (time.monotonic() - start) * 1000,
                )
            except Exception as exc:
                logger.debug("[warmup:%s] skip %s: %s", label, name, exc)
            # 每个模块之间主动让出，给正在跑的事件循环一个抢回 GIL 的机会。
            time.sleep(0)
        logger.info(
            "[warmup:%s] done: %d/%d modules in %.0f ms",
            label, loaded, len(module_list), (time.monotonic() - t0) * 1000,
        )

    threading.Thread(target=_run, name=f"module-warmup-{label}", daemon=True).start()
    return True
