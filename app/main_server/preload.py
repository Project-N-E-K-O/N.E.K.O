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
    """Preload translation libraries, dashscope, and httpx in the background.

    Note: no Event-based synchronization is needed, because Python's import lock
    automatically waits for the first import to finish. A concurrent first use
    simply blocks until the corresponding import is ready.
    """
    try:
        logger.info("🔄 后台预加载翻译与网络模块...")
        # Run synchronous imports in a thread pool to avoid blocking the event loop
        import concurrent.futures

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            await loop.run_in_executor(pool, _sync_preload_modules)
    except Exception as e:
        logger.warning(f"⚠️ 翻译与网络模块预加载失败（不影响使用）: {e}")


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

    # 1. Translation-service modules (avoid first-conversation latency)
    try:
        # Preload translation libraries (googletrans, translatepy, etc.)
        from utils import language_utils

        # Trigger translation-library imports if available
        _ = language_utils.GOOGLETRANS_AVAILABLE
        _ = language_utils.TRANSLATEPY_AVAILABLE
        logger.debug("✅ 翻译库预加载完成")
    except Exception as e:
        logger.debug(f"⚠️ 翻译库预加载失败（不影响使用）: {e}")

    # 2. Translation-service instance (requires config_manager)
    try:
        # Initialize the translation service early if initialization needs translation data
        from utils.language_utils import get_translation_service
        from utils.config_manager import get_config_manager

        # Call only to trigger singleton initialization here; later retrieve it through get_translation_service
        config_manager = get_config_manager()
        # Preinitialize the translation service (creating the LLM client, etc.)
        _ = get_translation_service(config_manager)
        logger.debug("✅ 翻译服务预加载完成")
    except Exception as e:
        logger.debug(f"⚠️ 翻译服务预加载失败（不影响使用）: {e}")

    # 3. dashscope (Alibaba Cloud CosyVoice TTS SDK, needed only for custom voices)
    try:
        import dashscope  # noqa: F401

        logger.debug("  ✓ dashscope loaded")
    except Exception as e:
        logger.debug(f"  ✗ dashscope: {e}")

    # 4. Warm the httpx SSL context (the first AsyncClient creation initializes SSL)
    try:
        import httpx
        import asyncio

        async def _warmup_httpx():
            # per-call AsyncClient: this is the SSL warmup itself; using a shared client would defeat its purpose
            async with httpx.AsyncClient(
                timeout=1.0, proxy=None, trust_env=False
            ) as client:
                # Send a simple request to warm the SSL context
                try:
                    await client.get("http://127.0.0.1:1", timeout=0.01)
                except:  # noqa: E722
                    pass  # Failure is expected; this only initializes SSL

        # Run on the current thread's event loop, creating a temporary loop if needed
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Use a thread pool if an event loop is already running
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
