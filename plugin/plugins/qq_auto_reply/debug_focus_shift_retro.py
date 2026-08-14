#!/usr/bin/env python
"""调试脚本：模拟两个群聊 → 焦点切换 → 回溯补回生成的摘要文本。

代入**实际生产环境**：
- 加载真实的生产配置 ``business_config.json``（注意力阈值、回溯参数、标签、
  信任列表等），行为与线上一致；
- 走真实的 ``QQAttentionService / QQBacklogStore / QQBacklogService /
  QQAttentionGateService`` 代码路径；
- 唯一拦截点：``reply_pipeline.run``（不真正调 LLM、不发 QQ 消息），改为捕获
  构造好的 ``QQReplyRequest``，打印回溯补回的摘要 prompt。

隔离与确定性：
- 回溯/注意力数据写入临时目录（``backlog_state.json`` 副本），绝不污染生产
  backlog；
- 时间用可控时钟驱动（覆盖 ``attention._current_time``），让焦点切换确定性发生。

用法：
    python plugin/plugins/qq_auto_reply/debug_focus_shift_retro.py --data-dir <qq_auto_reply 数据目录>
    # 数据目录 = 包含 business_config.json 的目录，通常在
    #   <N.E.K.O 数据根>/data/plugins/qq_auto_reply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
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
from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService
from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService
from plugin.plugins.qq_auto_reply.backlog_service import QQBacklogService
from plugin.plugins.qq_auto_reply.backlog_store import QQBacklogStore
from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore
from plugin.plugins.qq_auto_reply.group_permission import GroupPermissionManager
from plugin.plugins.qq_auto_reply.permission import PermissionManager
from plugin.plugins.qq_auto_reply.session import QQAutoReplySessionMixin


def log(msg: str) -> None:
    print(f"[debug] {msg}")


def build_facade(data_dir: Path, tmp_dir: Path, config: dict) -> SimpleNamespace:
    """构造插件门面：真实服务 + 生产配置，数据写入临时目录。"""
    facade = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    facade._qq_settings = dict(config)
    facade.logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        exception=lambda *a, **k: None,
        debug=lambda *a, **k: None,
    )
    facade._emit_log = lambda level, msg: log(f"[{level}] {msg}")

    # 真实的权限管理器：生产信任列表 + 测试群强制入册（否则消息不会被记录）
    trusted_users = list(config.get("trusted_users") or [])
    trusted_groups = list(config.get("trusted_groups") or [])
    seen_groups = {str(g.get("group_id") or "").strip() for g in trusted_groups if isinstance(g, dict)}
    for gid in (GROUP_A, GROUP_B):
        if gid not in seen_groups:
            trusted_groups.append({"group_id": gid, "level": "trusted"})
    facade.permission_mgr = PermissionManager(trusted_users)
    facade.group_permission_mgr = GroupPermissionManager(trusted_groups)

    # 真实的 backlog 存储（临时副本，不碰生产数据）
    facade.backlog_store = QQBacklogStore(tmp_dir, retention_limit=int(config.get("backlog_retention_limit", 200) or 200))

    # 真实服务
    facade.attention_service = QQAttentionService(facade)
    facade.backlog_service = QQBacklogService(facade)
    facade.gate = QQAttentionGateService(facade)

    # 真实的 key 构建 / 清洗方法（均为 @staticmethod，直接用类访问）
    facade._build_session_key = QQAutoReplySessionMixin._build_session_key
    facade._build_backlog_conversation_key = QQAutoReplySessionMixin._build_backlog_conversation_key
    facade._sanitize_message_text = QQAutoReplyPlugin._sanitize_message_text

    # 焦点切换检测 / 回溯需要的门面属性
    facade._admin_qq = str(config.get("admin_qq") or (trusted_users[0].get("qq") if trusted_users else "") or "0")
    facade._group_digest_task = None
    facade._user_sessions = {}
    facade._group_memory_sync_tasks = set()
    facade._prompt_change_discard_tasks = set()

    # 回溯补回流水线：拦在 LLM 之前，捕获构造好的请求
    captured = []

    async def fake_pipeline_run(request):
        captured.append(request)
        # 只观察摘要 prompt，不真正回复
        return SimpleNamespace(action="reply", reply_text="")

    async def fake_with_lock(key, coro):
        return await coro()

    facade.reply_pipeline = SimpleNamespace(run=fake_pipeline_run)
    facade._run_with_session_lock = fake_with_lock
    facade.session_memory_service = SimpleNamespace(
        session_history_len=lambda key: 0,
        record_synthetic_prompt_rows=lambda key, before: None,
    )
    facade.runtime_service = SimpleNamespace(record_pipeline_outcome=lambda *a, **k: None)
    facade.reply_buffer_service = None
    facade.fatigue_service = None
    facade.qq_client = SimpleNamespace(needs_attention=True)
    facade.captured = captured
    return facade


def make_group_message(*, group_id: str, sender_id: str, nickname: str, text: str, message_id: str, ts: int) -> dict:
    return {
        "message_type": "group",
        "user_id": sender_id,
        "user_nickname": nickname,
        "content": text,
        "message_id": message_id,
        "timestamp": ts,
        "group_id": group_id,
        "is_at_bot": False,
        "is_reply_to_bot": False,
    }


async def run(data_dir: Path, messages_b: list[tuple[str, str, str]]) -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="focus_shift_retro_"))
    try:
        # 1. 加载生产配置
        config_store = QQAutoReplyConfigStore(data_dir)
        config = await config_store.load()
        log(f"生产配置加载: {config_store.path}")
        log(f"  焦点线 focus_threshold={config.get('group_attention_focus_threshold', 4.0)}, "
            f"发送门控 focus_send_threshold={config.get('group_attention_focus_send_threshold', 2.0)}, "
            f"回溯上限 retroactive_review_max_messages={config.get('retroactive_review_max_messages', 30)}")

        facade = build_facade(data_dir, tmp_dir, config)
        attention = facade.attention_service
        gate = facade.gate

        # 2. 可控时钟
        clock = {"now": 1_700_000_000}
        attention._current_time = lambda: int(clock["now"])

        # 3. 群 A 先成为焦点（注意力 ≥ 焦点线）
        attention.mark_focus(GROUP_A)
        _a = attention._load_state(GROUP_A)
        _a.attention_score = float(config.get("group_attention_focus_threshold", 4.0))
        attention._write_state(_a)
        log(f"群 {GROUP_A} 成为焦点，score={_a.attention_score:.1f}")

        # 4. 群 A 焦点期间，群 B 的消息被「忽略」→ 落进 backlog（真实记录路径）
        clock["now"] += 60
        ts = clock["now"]
        for i, (sender, nickname, text) in enumerate(messages_b, 1):
            msg = make_group_message(
                group_id=GROUP_B, sender_id=sender, nickname=nickname,
                text=text, message_id=f"b{i}", ts=ts + i,
            )
            await facade.backlog_service.record_message(msg)
            log(f"  注入 B 消息: [{nickname}] {text}")
        log(f"群 {GROUP_B} 已注入 {len(messages_b)} 条消息（backlog 未审核）")

        # 5. 群 B 注意力反超 → 焦点切到 B
        clock["now"] += 60
        _b = attention._load_state(GROUP_B)
        _b.attention_score = float(config.get("group_attention_focus_threshold", 4.0)) + 2.0
        _b.last_message_at = clock["now"]
        attention._write_state(_b)
        gate._last_focus_group = GROUP_A  # 模拟接收时焦点是 A

        shift = await gate.check_focus_shift()
        log(f"check_focus_shift → {shift.previous_focus_group or '无'} → {shift.new_focus_group}"
            if shift else "check_focus_shift → 无切换")

        # 6. 回溯前先取快照（run_retroactive_review 结束后会把消息标记为已审阅）
        max_msgs = int(config.get("retroactive_review_max_messages", 30) or 30)
        unreviewed_before = await facade.backlog_store.get_unreviewed_messages_since(
            GROUP_B, since_timestamp=0, limit=max_msgs,
        )

        # 触发回溯补回（拦截 LLM，捕获摘要 prompt）
        await gate.run_retroactive_review(GROUP_B)

        # 7. 打印结果
        print("\n" + "=" * 70)
        print("回溯补回捕获到的 LLM 请求 message_text：")
        print("=" * 70)
        if facade.captured:
            print(facade.captured[0].message_text)
        else:
            print("（未捕获到请求——回溯可能被跳过）")
        print("=" * 70)

        # 直接验证 _build_ignored_summary（字段名修复后内容应为非空）
        summary = QQAttentionGateService._build_ignored_summary(unreviewed_before)
        print("\n_build_ignored_summary 直接输出（回溯前快照）：")
        print(summary if summary else "（空——没有未审核消息或全为空内容）")

        # 诊断脚本的核心目标：捕获回溯补回请求并确认摘要非空。任一失败都返回非零，
        # 避免「回溯没触发 / 摘要为空」时误报成功（退出码 0）。
        problems: list[str] = []
        if not facade.captured:
            problems.append("未捕获到回溯补回请求（backlog 为空 / 焦点未切换 / 回溯被跳过）")
        else:
            prompt_text = str(getattr(facade.captured[0], "message_text", "") or "")
            if "摘要：" not in prompt_text:
                problems.append("捕获到的请求里没有摘要段落")
        if not summary:
            problems.append("回溯前快照的未审核消息为空")
        empty_rows = [l for l in summary.splitlines() if ":  " in l or l.rstrip().endswith(":")]
        if empty_rows:
            problems.append(f"仍有 {len(empty_rows)} 条空内容行——原消息内容可能为空")
        if problems:
            print(f"\n⚠ 诊断未完全通过（{len(problems)} 项）：")
            for p in problems:
                print("  - " + p)
        return 2 if problems else 0
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def find_data_dir(override: str | None) -> Path:
    if override:
        p = Path(override)
        if (p / "business_config.json").is_file():
            return p
        raise SystemExit(f"--data-dir 下找不到 business_config.json: {p}")
    candidates = [
        Path.cwd() / "data" / "plugins" / "qq_auto_reply",
        Path.cwd() / "plugin" / "plugins" / "qq_auto_reply" / "data",
    ]
    for c in candidates:
        if (c / "business_config.json").is_file():
            return c
    raise SystemExit(
        "未找到生产配置。请用 --data-dir 指定 qq_auto_reply 插件数据目录（含 business_config.json 的目录）。"
    )


GROUP_A = "1048307485"
GROUP_B = "2097152000"


def main() -> int:
    global GROUP_A, GROUP_B
    parser = argparse.ArgumentParser(description="调试焦点切换 → 回溯补回摘要")
    parser.add_argument("--data-dir", default=None, help="qq_auto_reply 插件数据目录（含 business_config.json）")
    parser.add_argument("--group-a", default="1048307485", help="群 A（初始焦点群）")
    parser.add_argument("--group-b", default="2097152000", help="群 B（被忽略后接管焦点的群）")
    args = parser.parse_args()

    GROUP_A, GROUP_B = args.group_a, args.group_b

    data_dir = find_data_dir(args.data_dir)
    # 默认注入：B 群在 A 焦点期间被忽略的几条消息
    messages_b = [
        ("1219199629", "喵酱", "输入框成精这个说法太形象了，那光标就是它的尾巴在得意地甩来甩去啦～"),
        ("3429924750", "小明", "哈哈确实，每次打字都感觉它在摇头晃脑"),
        ("820040531", "老王", "有没有人今晚一起开黑？"),
        ("8765432101", "路人", "等一个王者车队，缺一"),
    ]
    return asyncio.run(run(data_dir, messages_b))


if __name__ == "__main__":
    raise SystemExit(main())
