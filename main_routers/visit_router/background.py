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

"""Process-level visit tasks wired by ``app/main_server`` (design §5 PR-09b).

* :func:`start_visit_background_tasks` (called once the main server runtime
  is initialized) starts, with ``asyncio.create_task`` and never awaited on
  the startup path, the visit sweep (``runtime.visit_sweep_loop``) and the
  startup recovery of visit files (``recovery.visit_spool_recovery``) with
  the runtime's callbacks injected.
* :func:`stop_visit_background_tasks` is the front of ``on_shutdown``:
  stop the sweep, ``runtime.stop_all('shutdown')`` within
  ``VISIT_SHUTDOWN_BUDGET_S``, then cancel what is left of the recovery.

Both run whatever the ``NEKO_VISIT_ENABLED`` release switch says: the
switch only closes the entry points, recovery and uploads keep working.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from config.visit_settings import VISIT_SHUTDOWN_BUDGET_S
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_sweep_task: Optional[asyncio.Task] = None
_recovery_task: Optional[asyncio.Task] = None


async def _family_names() -> tuple[str, ...]:
    from main_routers.visit_router.local_context import load_character_context

    try:
        return tuple((await load_character_context()).family_names)
    except Exception as exc:  # noqa: BLE001 - 读不出就不替换家人称呼（摘要只是少一道保护名单）
        logger.warning("visit recovery: family names unreadable: %s", type(exc).__name__)
        return ()


async def run_startup_recovery() -> Any:
    """One startup recovery pass with the runtime's callbacks (the body of the recovery task)."""
    from config.visit_settings import VISIT_LAST_SUMMARY_MAX_TOKENS, VISIT_LLM_TIMEOUT_S
    from main_logic.visit.forget_runner import default_void_pending
    from main_logic.visit.recovery import visit_spool_recovery
    from main_routers.visit_router import runtime, transcript_upload
    from main_routers.visit_router.debrief import render_chips
    from main_routers.visit_router.llm import one_shot_llm

    config_dir = runtime.runtime_deps().config_dir()
    report = await visit_spool_recovery(
        render_chips,
        transcript_upload.upload_visit_transcript,
        is_live=runtime.is_visit_live,
        spawn_background=runtime.spawn_visit_background,
        config_dir=config_dir,
        summary_llm=one_shot_llm(max_tokens=VISIT_LAST_SUMMARY_MAX_TOKENS, timeout=VISIT_LLM_TIMEOUT_S),
        family_names=await _family_names(),
        submit_report=transcript_upload.submit_queued_report,
        retry_later=transcript_upload.schedule_visit_retry,
        void_pending=default_void_pending(config_dir),
        # 清除重放期间挡住改名 / 删除的守卫（PR-09b 第一段提供）；没有它时清除照常重放、只是不挡
        lifecycle_guard=getattr(runtime, "hold_character_lifecycle", None),
    )
    logger.info("visit recovery done: crashed=%d chips=%d swept=%d", len(report.crashed), len(report.chips),
                report.swept)
    return report


async def _recover_in_background() -> None:
    try:
        await run_startup_recovery()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 补录失败只留文件给下次启动
        logger.error("visit recovery failed: %r", exc)


def start_visit_background_tasks() -> None:
    """Start the visit sweep and the startup recovery (``create_task``; never awaited by startup)."""
    global _sweep_task, _recovery_task
    from main_routers.visit_router import runtime

    if _sweep_task is None or _sweep_task.done():
        _sweep_task = asyncio.create_task(runtime.visit_sweep_loop(), name="visit-sweep")
    if _recovery_task is None or _recovery_task.done():
        _recovery_task = asyncio.create_task(_recover_in_background(), name="visit-recovery")


def _cancel(task: Optional[asyncio.Task]) -> None:
    if task is not None and not task.done():
        task.cancel()


async def stop_visit_background_tasks() -> None:
    """Shutdown: stop the sweep, ``stop_all`` within ``VISIT_SHUTDOWN_BUDGET_S``, cancel the recovery."""
    global _sweep_task, _recovery_task
    from main_routers.visit_router import runtime

    sweep, recovery = _sweep_task, _recovery_task
    _sweep_task = _recovery_task = None
    _cancel(sweep)  # 计时不再与关机收口并发
    try:
        await asyncio.wait_for(runtime.stop_all("shutdown"), VISIT_SHUTDOWN_BUDGET_S)
    except Exception as exc:  # noqa: BLE001 - 超时 / 出错只记日志：没收口的留给下次启动补录
        logger.warning("visit shutdown: stop_all did not finish: %r", exc)
    # 补录本身（其派生的后台写入已由 stop_all 取消）：没做完的下次启动再来
    _cancel(recovery)


def cancel_visit_background_tasks() -> None:
    """Startup rollback: cancel both tasks without the shutdown flow (no visit can be live yet)."""
    global _sweep_task, _recovery_task
    _cancel(_sweep_task)
    _cancel(_recovery_task)
    _sweep_task = _recovery_task = None
