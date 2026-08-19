#!/usr/bin/env python
"""调试脚本：代入实际 QQReplyBufferService，验证多条缓冲汇总只用原消息。

背景（bug）：``_deliver_after_wait`` 的多条分支原先拿 ``pending.buffered_texts``
拼总结 prompt，而 ``buffered_texts[0]`` 已被 ``schedule_reply`` 覆盖成 bot 的草稿
回复——猫娘自己的回复被当成「对方发的消息」喂进 LLM。修复后改用
``buffered_user_texts``（真实入站文本）。

代入**实际生产环境**：
- 真实 ``QQReplyBufferService / PendingReply``，走真实
  ``pre_buffer → schedule_reply → _deliver_after_wait`` 全链路；
- 生产配置（若提供 ``--data-dir``）或内置默认；
- 唯一拦截点（不真正调 LLM / 不发 QQ）：
    · ``reply_pipeline.run`` —— 捕获构造好的 ``QQReplyRequest``，检查总结 prompt；
    · ``reply_delivery_node.deliver`` —— 单条路径检查投递的是 bot 回复。

隔离与确定性：
- 会话历史写入内存里的 ``_user_sessions``，绝不落盘；
- 自动等待任务在跑完前会被显式取消、``wait_until`` 拨到过去，再直接调用真实
  ``_deliver_after_wait``，让等待时长不影响结果。

用法：
    python plugin/plugins/qq_auto_reply/debug_reply_buffer_summary.py [--data-dir <目录>]
退出码：0 = 全部断言通过；2 = 有失败。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# Windows 控制台默认 GBK，强制 UTF-8 输出避免中文乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 让脚本在仓库根目录下可导入 plugin.*（仓库根 = 本文件的 4 层父目录：
# qq_auto_reply → plugins → plugin → 仓库根）
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore
from plugin.plugins.qq_auto_reply.group_permission import GroupPermissionManager
from plugin.plugins.qq_auto_reply.permission import PermissionManager
from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
from plugin.plugins.qq_auto_reply.reply_buffer_service import QQReplyBufferService
from plugin.plugins.qq_auto_reply.session import QQAutoReplySessionMixin


GROUP_ID = "1048307485"
SENDER_ID = "1219199629"


def log(msg: str) -> None:
    print(f"[debug] {msg}")


def build_facade(config: dict) -> QQAutoReplyPlugin:
    """构造插件门面：真实 buffer 服务 + 真实权限管理器，数据留在内存。"""
    facade = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    facade._qq_settings = dict(config)
    facade.logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        exception=lambda *a, **k: None,
        debug=lambda *a, **k: None,
    )
    facade._emit_log = lambda level, msg: log(f"[{level}] {msg}")

    # 真实的权限管理器（buffer 的授权撤销检查会读它）
    facade.permission_mgr = PermissionManager(list(config.get("trusted_users") or []))
    facade.group_permission_mgr = GroupPermissionManager(
        list(config.get("trusted_groups") or [])
    )

    # 真实 buffer 服务（被测试的节点）
    facade.reply_buffer_service = QQReplyBufferService(facade)
    # 会话历史走内存；mark_latest_draft_undelivered 只读空历史即可
    facade._user_sessions = {}

    facade._build_session_key = QQAutoReplySessionMixin._build_session_key

    # 拦截点：LLM / 真实发送之前
    captured: list[tuple[str, object]] = []

    async def fake_pipeline_run(request):
        captured.append(("pipeline", request))
        # 只观察请求，不真正生成回复
        return SimpleNamespace(action="reply", reply_text="")

    async def fake_with_lock(key, coro):
        return await coro()

    facade.reply_pipeline = SimpleNamespace(run=fake_pipeline_run)
    facade._run_with_session_lock = fake_with_lock
    facade.session_memory_service = SimpleNamespace(
        session_history_len=lambda key: 0,
        record_synthetic_prompt_rows=lambda key, before, **kwargs: 0,
    )
    facade.reply_generation_service = SimpleNamespace(
        append_fallback_ai_row=lambda *a, **k: None,
        record_scoped_mentions_on_delivery=lambda *a, **k: None,
    )
    facade._spawn_memory_sync_task = lambda coro, session_key=None: coro
    facade.captured = captured
    return facade


async def _cancel_auto_task(pending) -> None:
    """取消 buffer 自动建的等待任务，并等它真正收场（避免泄漏）。"""
    task = getattr(pending, "task", None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await asyncio.sleep(0)  # 让被 _supersede 取消的上一代任务也收场


def _session_data(facade: QQAutoReplyPlugin, session_key: str) -> dict:
    data = facade._user_sessions.setdefault(session_key, {})
    data.setdefault(
        "session", SimpleNamespace(_conversation_history=[]),
    )
    data.setdefault("undelivered_draft_rows", [])
    data.setdefault("provisional_draft_rows", [])
    return data


async def scenario_multi_summary(facade: QQAutoReplyPlugin) -> list[str]:
    """多条缓冲总结：prompt 必须用原消息，不能带 bot 草稿。"""
    problems: list[str] = []
    service = facade.reply_buffer_service
    session_key = facade._build_session_key(
        sender_id=SENDER_ID, is_group=True, group_id=GROUP_ID,
    )
    _session_data(facade, session_key)

    log("场景 1：多条缓冲汇总（修复点）")

    # 1. 第一条原消息：pre_buffer 建占位，返回 False → 走 pipeline
    first = service.pre_buffer(
        session_key, "第一条原消息", SENDER_ID, True, GROUP_ID,
    )
    assert first is False, "首条消息应走 pipeline（返回 False）"

    # 2. pipeline 生成 bot 草稿 → schedule_reply 覆盖 buffered_texts[0]
    await service.schedule_reply(
        session_key=session_key,
        reply_text="<msg>（猫娘的第一版草稿回复）</msg>",
        raw_text="（猫娘的第一版草稿回复）",
        blocks=[],
        wait_seconds=30.0,  # 够长，模拟跑完前不会自动触发
        sender_id=SENDER_ID,
        is_group=True,
        group_id=GROUP_ID,
    )

    # 3. 第二条原消息在等待期间到达：pre_buffer 追加并跳过 pipeline
    second = service.pre_buffer(
        session_key, "第二条原消息", SENDER_ID, True, GROUP_ID,
    )
    assert second is True, "追加消息应跳过 pipeline（返回 True）"

    pending = service._pending[session_key]
    log(f"   buffered_texts      = {pending.buffered_texts}")
    log(f"   buffered_user_texts = {pending.buffered_user_texts}")
    log(f"   message_count       = {pending.message_count}")

    # 4. 手动接管投递时机：取消自动等待任务，把 wait_until 拨到过去再跑真实方法
    await _cancel_auto_task(pending)
    pending.wait_until = time.time() - 1
    await service._deliver_after_wait(session_key, pending, pending.generation)

    reqs = [r for kind, r in facade.captured if kind == "pipeline"]
    if not reqs:
        problems.append("未捕获到多条缓冲的总结请求")
        return problems
    prompt = str(getattr(reqs[0], "message_text", "") or "")
    print("\n" + "=" * 70)
    print("多条缓冲总结请求 message_text：")
    print("=" * 70)
    print(prompt)
    print("=" * 70)
    if "第一条原消息" not in prompt or "第二条原消息" not in prompt:
        problems.append("总结 prompt 缺少原消息（应包含两条原消息）")
    if "猫娘的第一版草稿回复" in prompt:
        problems.append("总结 prompt 混入了 bot 自己的草稿回复（bug 复现）")
    if f"对方连续发了 2 条消息" not in prompt:
        problems.append("总结 prompt 的消息条数不是 2")
    return problems


async def scenario_single_delivery(facade: QQAutoReplyPlugin) -> list[str]:
    """单条缓冲：投递的是 bot 回复，不是把原消息回发回去。"""
    problems: list[str] = []
    service = facade.reply_buffer_service
    session_key = facade._build_session_key(
        sender_id=SENDER_ID, is_group=True, group_id=GROUP_ID,
    )
    _session_data(facade, session_key)

    log("\n场景 2：单条缓冲投递（回归确认不受影响）")

    delivered_plans: list = []

    async def fake_deliver(plan, consent_gate=None):
        delivered_plans.append(plan)
        return SimpleNamespace(delivered=True)

    facade.reply_delivery_node = SimpleNamespace(deliver=fake_deliver)

    assert service.pre_buffer(
        session_key, "一条原消息", SENDER_ID, True, GROUP_ID,
    ) is False
    await service.schedule_reply(
        session_key=session_key,
        reply_text="<msg>（猫娘的最终回复）</msg>",
        raw_text="（猫娘的最终回复）",
        blocks=[QQMessageBlock(text="（猫娘的最终回复）")],
        wait_seconds=30.0,
        sender_id=SENDER_ID,
        is_group=True,
        group_id=GROUP_ID,
    )
    pending = service._pending[session_key]
    await _cancel_auto_task(pending)
    pending.wait_until = time.time() - 1
    await service._deliver_after_wait(session_key, pending, pending.generation)

    if not delivered_plans:
        problems.append("单条路径未调用投递")
    else:
        text = str(delivered_plans[0].blocks[0].text or "")
        log(f"   投递的 blocks[0].text = {text!r}")
        if "猫娘的最终回复" not in text:
            problems.append("单条路径没有投递 bot 回复")
        if text == "一条原消息":
            problems.append("单条路径把原消息回发了回去")
    return problems


async def scenario_all_bot_drafts(facade: QQAutoReplyPlugin) -> list[str]:
    """复刻用户真实日志：两条缓冲全是 bot 草稿，prompt 只能用原消息。

    真实时序（group:1048307485）：
        11:22:20  真实入站「可爱捏」→ pre_buffer 建占位
        11:22:22  pipeline 回复「什么东西可爱呀？」→ schedule_reply 填 buffered_texts[0]
        11:22:23  群摘要( retroactive_review, 不走 pre_buffer )也生成回复
        11:22:24  「你们俩是在玩什么吗？」→ schedule_reply 追加 → buffered_texts 两条全是 bot 草稿
        11:22:33  总结 prompt 读 buffered_texts → 两条都是猫娘自己的回复
    修复后读 buffered_user_texts = [可爱捏]。
    """
    problems: list[str] = []
    service = facade.reply_buffer_service
    session_key = facade._build_session_key(
        sender_id=SENDER_ID, is_group=True, group_id=GROUP_ID,
    )
    _session_data(facade, session_key)

    log("\n场景 3：复刻你真实日志（两条缓冲全是 bot 草稿）")

    # 11:22:20 真实入站「可爱捏」→ pre_buffer 建占位
    assert service.pre_buffer(
        session_key, "可爱捏", SENDER_ID, True, GROUP_ID,
    ) is False
    # 11:22:22 pipeline 回复 → schedule_reply 填 buffered_texts[0]
    await service.schedule_reply(
        session_key=session_key,
        reply_text="<msg>什么东西可爱呀？</msg>",
        raw_text="什么东西可爱呀？",
        blocks=[],
        wait_seconds=30.0,
        sender_id=SENDER_ID,
        is_group=True,
        group_id=GROUP_ID,
    )
    # 11:22:24 群摘要(不走 pre_buffer)的回复 → schedule_reply 追加路径再堆一条 bot 草稿
    await service.schedule_reply(
        session_key=session_key,
        reply_text="<msg>你们俩是在玩什么吗？</msg>",
        raw_text="你们俩是在玩什么吗？",
        blocks=[],
        wait_seconds=30.0,
        sender_id=SENDER_ID,
        is_group=True,
        group_id=GROUP_ID,
    )

    pending = service._pending[session_key]
    log(f"   buffered_texts      = {pending.buffered_texts}")
    log(f"   buffered_user_texts = {pending.buffered_user_texts}")
    log(f"   message_count       = {pending.message_count}")

    baseline = len(facade.captured)
    await _cancel_auto_task(pending)
    pending.wait_until = time.time() - 1
    await service._deliver_after_wait(session_key, pending, pending.generation)

    reqs = [r for kind, r in facade.captured[baseline:] if kind == "pipeline"]
    if not reqs:
        problems.append("场景 3：未捕获到总结请求")
        return problems
    prompt = str(getattr(reqs[0], "message_text", "") or "")
    print("\n" + "=" * 70)
    print("场景 3 总结请求 message_text（修复后）：")
    print("=" * 70)
    print(prompt)
    print("=" * 70)
    if "可爱捏" not in prompt:
        problems.append("场景 3：prompt 缺真实入站消息「可爱捏」")
    if "什么东西可爱呀？" in prompt or "你们俩是在玩什么吗？" in prompt:
        problems.append("场景 3：prompt 仍混入 bot 草稿（bug 复现）")
    return problems


async def run_all(facade: QQAutoReplyPlugin) -> list[str]:
    problems: list[str] = []
    problems += await scenario_multi_summary(facade)
    problems += await scenario_single_delivery(facade)
    problems += await scenario_all_bot_drafts(facade)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="调试：验证多条缓冲汇总用原消息而非 bot 草稿",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="qq_auto_reply 插件数据目录（可选，有则加载生产配置）",
    )
    args = parser.parse_args()

    config: dict = {}
    if args.data_dir:
        data_dir = Path(args.data_dir)
        if (data_dir / "business_config.json").is_file():
            store = QQAutoReplyConfigStore(data_dir)
            config = asyncio.run(store.load())
            log(f"生产配置加载: {store.path}")
        else:
            log(f"WARN: {data_dir} 下没有 business_config.json，使用内置默认配置")

    facade = build_facade(config)
    problems = asyncio.run(run_all(facade))

    if problems:
        print(f"\n⚠ 诊断未完全通过（{len(problems)} 项）：")
        for p in problems:
            print("  - " + p)
        return 2
    print("\n✅ 全部断言通过：多条汇总用原消息，单条投递 bot 回复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
