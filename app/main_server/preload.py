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

"""Preload heavy runtime modules without delaying the import-time app facade."""

import asyncio

from ._shared import runtime

logger = runtime.logger


async def _background_preload():
    """Preload audio processing modules in the background

    Note: no Event-based synchronization is needed, because Python's import lock
    automatically waits for the first import to finish. If the user clicks voice
    before preloading completes, the second import simply blocks until ready.
    """
    try:
        logger.info("🔄 后台预加载音频处理模块...")
        # 在线程池中执行同步导入（避免阻塞事件循环）
        import concurrent.futures

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            await loop.run_in_executor(pool, _sync_preload_modules)
    except Exception as e:
        logger.warning(f"⚠️ 音频处理模块预加载失败（不影响使用）: {e}")


def _sync_preload_modules():
    """Synchronously preload lazily imported modules (runs in a thread pool)

    Note: the following modules are already loaded at startup via the import chain and need no preloading:
    - numpy, soxr: via core.py / audio_processor.py
    - websockets: via omni_realtime_client.py
    - langchain_openai/langchain_core: via omni_offline_client.py
    - httpx: via core.py
    - aiohttp: via tts_client.py

    Lazily imported modules that genuinely need preloading:
    - dashscope: imported only inside the cosyvoice_vc_tts_worker function in tts_client.py
    - googletrans/translatepy: translation libraries lazily imported in language_utils.py
    - translation_service: the translation service (TranslationService) in language_utils.py
    """
    import time

    start = time.time()

    # 1. 翻译服务相关模块（避免首轮对话延迟）
    try:
        # 预加载翻译库（googletrans, translatepy 等）
        from utils import language_utils

        # 触发翻译库的导入（如果可用）
        _ = language_utils.GOOGLETRANS_AVAILABLE
        _ = language_utils.TRANSLATEPY_AVAILABLE
        logger.debug("✅ 翻译库预加载完成")
    except Exception as e:
        logger.debug(f"⚠️ 翻译库预加载失败（不影响使用）: {e}")

    # 2. 翻译服务实例（需要 config_manager）
    try:
        # 提前初始化翻译服务（如果在初始化过程中需要翻译数据）
        from utils.language_utils import get_translation_service
        from utils.config_manager import get_config_manager

        # 此处仅调用以触发单例初始化，后续使用时通过 get_translation_service 获取即可
        config_manager = get_config_manager()
        # 预初始化翻译服务实例（触发 LLM 客户端创建等）
        _ = get_translation_service(config_manager)
        logger.debug("✅ 翻译服务预加载完成")
    except Exception as e:
        logger.debug(f"⚠️ 翻译服务预加载失败（不影响使用）: {e}")

    # 3. dashscope (阿里云 CosyVoice TTS SDK - 仅在使用自定义音色时需要)
    try:
        import dashscope  # noqa: F401

        logger.debug("  ✓ dashscope loaded")
    except Exception as e:
        logger.debug(f"  ✗ dashscope: {e}")

    # 4. httpx SSL 上下文预热（首次创建 AsyncClient 会初始化 SSL）
    try:
        import httpx
        import asyncio

        async def _warmup_httpx():
            # per-call AsyncClient: 这就是 SSL warmup 本身，改共享 client 反而没意义
            async with httpx.AsyncClient(
                timeout=1.0, proxy=None, trust_env=False
            ) as client:
                # 发送一个简单请求预热 SSL 上下文
                try:
                    await client.get("http://127.0.0.1:1", timeout=0.01)
                except:  # noqa: E722
                    pass  # 预期会失败，只是为了初始化 SSL

        # 在当前线程的事件循环中运行（如果没有则创建临时循环）
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果已有运行中的循环，使用线程池
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_warmup_httpx())).result(
                        timeout=2.0
                    )
            else:
                loop.run_until_complete(_warmup_httpx())
        except RuntimeError:
            asyncio.run(_warmup_httpx())
        logger.debug("  ✓ httpx SSL context warmed up")
    except Exception as e:
        logger.debug(f"  ✗ httpx warmup: {e}")

    elapsed = time.time() - start
    logger.info(f"📦 模块预加载完成，耗时 {elapsed:.2f}s")
