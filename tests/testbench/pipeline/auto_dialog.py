"""Auto-Dialog runner — 双 AI 自动对话 pipeline (P13).

PLAN §P13 约定: Composer 的第四种对话模式, 给定配置 (total_turns / SimUser
style + persona_hint / step_mode=fixed|off / step_seconds) 后让 SimUser 与
Target AI **交替生成** N 轮, 每轮落盘 ``session.messages``, SSE 向 UI 推进度,
支持 **Pause / Resume / Stop** 的 graceful 控制.

设计要点
--------
* **"一轮" 的定义 = "一次 target AI 回复"**: ``completed_turns`` 在每条 assistant
  消息完成后 ``+1``, 进度条 ``N/M`` 的分子就是它. 首步由 ``_decide_first_kind``
  自适应决定:
    - ``session.messages[-1].role == 'user'`` → 先跑 target (补齐欠下的回复),
      该轮**没有配对的 sim step**, 但仍计一轮.
    - 其它 (末尾是 assistant / system / 空) → 先跑 SimUser 开局, 后接 target.
* **SimUser 调用不落盘**: ``simulated_user.generate_simuser_message`` 只生成
  草稿 (SimUserDraft); 我们用它的 ``content`` 作为 ``stream_send(user_content=
  draft.content, source=SOURCE_AUTO)`` 的输入, 由 ``stream_send`` 统一追加
  user 消息 + yield ``{event:'user'}``, 避免两处各 append 一次.
* **adaptive 首步是 target 时跳过 user append**: 调 ``stream_send(user_content
  =None)`` (需要 chat_runner 支持这条路径, P13 补了) 让推理直接基于现有
  messages 跑, 不再生成新的 user. 这是对应 "先补一条欠下的 assistant 回复"
  的干净实现.
* **fixed step mode 的时钟推进**: 每条 target 回复开始前调
  ``session.clock.stage_next_turn(delta=timedelta(seconds=step_seconds))``;
  ``stream_send`` 会在内部 consume pending, 让这轮的 assistant 时间戳落在
  "user_ts + step_seconds" 附近 (实际是在 append user 之前 advance). 
  off mode 则完全不碰 clock.
* **graceful pause gate**: ``running_event: asyncio.Event`` 默认 set; pause
  端点 ``clear()``, 循环头 ``await running_event.wait()`` 阻塞. 关键属性 —
  一条 step (SimUser 生成 or target stream) **不会被 pause 打断**, 因为我们
  只在两步之间检查 gate. 代价: 从点 Pause 到真暂停 = 当前 step 剩余耗时;
  好处: 不会留下半截 assistant 消息 + 不触发 httpx aclose 边界.
* **stop = set stop_event**: 循环头判断; 可能卡在 pause gate 的 generator
  需要 ``running_event.set()`` + ``stop_event.set()`` 的顺序把它唤醒后走到
  stop 分支.
* **外部控制端点不持 session lock**: Auto-Dialog 全程持 BUSY, pause/resume/
  stop 如果也想拿锁会死锁. 它们只读 ``session.auto_state`` 里的 event 引用
  调 ``.set()/.clear()`` — 单次调用在 GIL 下原子, 不会与 runner 写 auto_state
  其它字段的地方竞态.
* **auto_state 生死期**: 只在 ``run_auto_dialog`` 的 try/finally 内存活. 生成
  器完成 / 异常 / stop 时 finally 清成 ``None``, 让 ``GET /state`` 回报 "空闲".
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, AsyncIterator, Final, Literal

from tests.testbench.chat_messages import (
    ROLE_USER,
    SOURCE_AUTO,
)
from tests.testbench.logger import python_logger
from tests.testbench.pipeline import diagnostics_store
from tests.testbench.pipeline.chat_runner import get_chat_backend
from tests.testbench.pipeline.messages_writer import append_message
from tests.testbench.pipeline.simulated_user import (
    STYLE_PRESETS,
    SimUserError,
    generate_simuser_message,
)
from tests.testbench.session_store import Session


# ── errors ──────────────────────────────────────────────────────────


class AutoDialogError(RuntimeError):
    """Raised when auto-dialog start / pause / stop can't proceed.

    Code → HTTP status 在 router 层 ``_auto_error_to_http`` 统一映射. 前端
    按 ``code`` 选 toast 文案.

    P24 Day 7 (§12.3.F) 新加 ``errors: list[str] | None`` 字段: 用于
    ``from_request`` 类**批量校验**场景 — 一次收集多条失败, 让前端
    可以用折叠面板分条展示而不是把所有错误挤进一个大 string 让 toast
    截断或不可读 (toast 超 280 字符会被 ``_truncate`` 砍掉). 该字段
    纯展示用, 不影响 HTTP status 分派.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 500,
        *,
        errors: list[str] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.errors = list(errors) if errors else None
        super().__init__(f"{code}: {message}")


# ── config ──────────────────────────────────────────────────────────


STEP_MODES: Final[tuple[str, ...]] = ("fixed", "off")
#: ``fixed`` = 每轮 target 前 stage ``step_seconds`` 秒; ``off`` = 不动时钟.
#: PLAN 原文列了 fixed / linear / random / off 四档, 本期只先落地 fixed / off
#: (足够覆盖 "等长 N 轮压测" + "完全不动时钟看对话连续性" 两种主流需求);
#: linear / random 等有评分场景具体需求时再补, 协议与 UI 布局都为之预留.

MAX_TOTAL_TURNS: Final[int] = 50
#: 单次 Auto-Dialog 最多 50 轮 — 防止误填 999 把 api_key 额度一次烧光. 真有
#: 超长压测需要可以分多次跑 (每次 N=50 手动接力).

MIN_STEP_SECONDS: Final[int] = 1
MAX_STEP_SECONDS: Final[int] = 7 * 24 * 3600  # 一周, 跨月/季度压测也够用


@dataclass
class AutoDialogConfig:
    """Auto-Dialog 一次 start 的完整配置.

    不存盘 — 只在 ``session.auto_state['config']`` 里做运行期快照. 下次 start
    时前端可以把上次的 config 回填到 UI (通过 ``/state`` 读), 但后端不自动
    记住 (避免 "上次跑挂了, 这次默认值还是那个挂的 config" 的迷思).
    """

    total_turns: int
    simuser_style: str
    step_mode: Literal["fixed", "off"]
    step_seconds: int | None = None
    simuser_persona_hint: str = ""
    simuser_extra_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_request(cls, body: dict[str, Any]) -> "AutoDialogConfig":
        """校验并构造 config; 失败 → :class:`AutoDialogError`.

        Router 把 JSON body 直接喂进来, 这里一口气校验完所有字段, 好让前端
        一次看到所有错误. P24 Day 7 (§12.3.F) 起**把多条错误整体传给前端**:
        合成的 message (兼容老前端 toast 显示) + ``errors: list[str]``
        分条列表 (新前端用折叠面板展示).
        """
        errors: list[str] = []

        total_turns = body.get("total_turns")
        if not isinstance(total_turns, int) or total_turns < 1:
            errors.append("total_turns 必须是 ≥1 的整数")
        elif total_turns > MAX_TOTAL_TURNS:
            errors.append(f"total_turns 超过上限 {MAX_TOTAL_TURNS}")

        style = body.get("simuser_style") or ""
        if not isinstance(style, str) or style not in STYLE_PRESETS:
            errors.append(
                f"simuser_style 必须是预设之一 ({', '.join(STYLE_PRESETS)})",
            )

        step_mode = body.get("step_mode") or "off"
        if step_mode not in STEP_MODES:
            errors.append(
                f"step_mode 必须是 {'/'.join(STEP_MODES)}, got {step_mode!r}",
            )

        step_seconds = body.get("step_seconds")
        if step_mode == "fixed":
            if not isinstance(step_seconds, int):
                errors.append("step_mode=fixed 时 step_seconds 必须是整数秒")
            elif not (MIN_STEP_SECONDS <= step_seconds <= MAX_STEP_SECONDS):
                errors.append(
                    f"step_seconds 超出范围 [{MIN_STEP_SECONDS},"
                    f" {MAX_STEP_SECONDS}]"
                )
        else:
            # off 模式下传进来也忽略, 统一归零
            step_seconds = None

        persona_hint = body.get("simuser_persona_hint") or ""
        extra_hint = body.get("simuser_extra_hint") or ""
        if not isinstance(persona_hint, str):
            errors.append("simuser_persona_hint 必须是字符串")
        if not isinstance(extra_hint, str):
            errors.append("simuser_extra_hint 必须是字符串")

        if errors:
            raise AutoDialogError(
                "InvalidConfig",
                "Auto-Dialog 配置无效: " + "; ".join(errors),
                status=400,
                errors=errors,
            )

        # 此处所有字段都已过校验
        return cls(
            total_turns=total_turns,  # type: ignore[arg-type]
            simuser_style=style,
            step_mode=step_mode,  # type: ignore[arg-type]
            step_seconds=step_seconds,
            simuser_persona_hint=persona_hint,
            simuser_extra_hint=extra_hint,
        )


# ── helpers ─────────────────────────────────────────────────────────


def _decide_first_kind(session: Session) -> Literal["simuser", "target"]:
    """首步由谁跑.

    末尾是 user → 先补一条 target 回复 (欠账, 不消耗 SimUser 一次生成);
    其它 (assistant / system / 空) → 先让 SimUser 开局.

    注意: 只看 ``role`` 不看 ``content`` — 即使是空串的 placeholder user
    (不应该发生, chat_runner 只在成功 append 后才 yield, 失败会 pop)
    也按 "user 存在" 对待, 防御性地补 target.
    """
    if not session.messages:
        return "simuser"
    last = session.messages[-1]
    if last.get("role") == ROLE_USER:
        return "target"
    return "simuser"


def _public_auto_state(session: Session) -> dict[str, Any] | None:
    """``/state`` 端点用的外部快照 (滤掉 asyncio.Event 等不可 JSON 的东西).

    同时保留 ``paused`` 字段 (``running_event.is_set() == False``) 让前端
    能区分 "跑着" / "暂停" 两个运行态, 不用自己维护状态机.
    """
    st = session.auto_state
    if st is None:
        return None
    running: asyncio.Event | None = st.get("running_event")
    stopping: asyncio.Event | None = st.get("stop_event")
    return {
        "total_turns": st.get("total_turns"),
        "completed_turns": st.get("completed_turns", 0),
        "next_kind": st.get("next_kind"),
        "config": st.get("config"),
        "paused": bool(running is not None and not running.is_set()),
        "stopping": bool(stopping is not None and stopping.is_set()),
        "started_at_real": st.get("started_at_real"),
    }


# ── public API ──────────────────────────────────────────────────────


async def run_auto_dialog(
    session: Session,
    config: AutoDialogConfig,
) -> AsyncIterator[dict[str, Any]]:
    """Auto-Dialog 主循环 async generator.

    调用前提: router 已用 ``session_operation("chat.auto_dialog.start",
    state=BUSY)`` 持锁并校验 no-session / already-running. 本 generator
    生死期间对 ``session.messages`` / ``session.clock`` / ``session.
    auto_state`` 有独占写权.

    Yields
    ------
    dict
        SSE 事件 (router 负责 json.dumps + "data: " 前缀):

        * ``{event:'start', total_turns, config, first_kind, auto_state}``
        * ``{event:'turn_begin', index, kind, step_seconds?}``
        * ``{event:'simuser_done', message, warnings}`` — SimUser 落盘
        * ``{event:'user', message}`` — 透传 stream_send 的 user 事件
          (sim 模式下同 simuser_done 指向的那条)
        * ``{event:'wire_built', ...}`` / ``{event:'assistant_start', ...}``
          / ``{event:'delta', ...}`` / ``{event:'usage', ...}`` / ``{event:
          'assistant', message}`` — 透传 stream_send 的 target 事件
        * ``{event:'turn_done', completed_turns, total_turns}`` — 一轮成功
        * ``{event:'paused'}`` / ``{event:'resumed'}`` — 控制状态变迁
        * ``{event:'stopped', reason: 'completed'|'user_stop'|'error',
          completed_turns, total_turns}`` — 终止 (最后一条事件)
        * ``{event:'error', error:{type,message}}`` — 紧跟 stopped(reason=
          error). SimUser 或 target 任一失败都走这条.
    """
    if session.auto_state is not None:
        raise AutoDialogError(
            "AutoAlreadyRunning",
            "当前会话已有 Auto-Dialog 在跑, 先 stop 再 start",
            status=409,
        )

    running_event = asyncio.Event()
    running_event.set()  # 默认 running; pause 时 clear
    stop_event = asyncio.Event()

    first_kind = _decide_first_kind(session)

    session.auto_state = {
        "total_turns": config.total_turns,
        "completed_turns": 0,
        "next_kind": first_kind,
        "config": config.to_dict(),
        "started_at_real": time.monotonic(),
        "running_event": running_event,
        "stop_event": stop_event,
    }

    session.logger.log_sync(
        "auto_dialog.start",
        payload={
            "config": config.to_dict(),
            "first_kind": first_kind,
            "starting_message_count": len(session.messages),
        },
    )
    python_logger().info(
        "auto_dialog: session=%s N=%d style=%s step=%s first=%s",
        session.id, config.total_turns, config.simuser_style,
        f"{config.step_mode}/{config.step_seconds or 0}s",
        first_kind,
    )

    yield {
        "event": "start",
        "total_turns": config.total_turns,
        "config": config.to_dict(),
        "first_kind": first_kind,
        "auto_state": _public_auto_state(session),
    }

    reason: str = "completed"
    error_payload: dict[str, Any] | None = None
    # 终局完成数独立快照, 不依赖 session.auto_state —  finally 块会把
    # auto_state 清成 None 以释放运行态, 但我们**还需要**在之后的 error /
    # stopped yield 里报真实的 completed_turns. 曾经直接读 auto_state 导致
    # 10/10 跑完了 stopped 事件里永远是 0/10 的 UI bug.
    final_completed: int = 0

    try:
        while True:
            # 1) Pause gate — 当前 step 结束后才检查, 不打断进行中的 stream.
            if not running_event.is_set():
                yield {"event": "paused"}
                # 等待 resume 或 stop. stop 会同时 set running_event 让它解封.
                await running_event.wait()
                if not stop_event.is_set():
                    yield {"event": "resumed"}

            # 2) Stop gate — 比 pause 优先级高, 可能是 pause 后再 stop.
            #    语义: "完成当前轮再停". 若 next_kind=target 且 messages 末尾
            #    是 user (SimUser 刚 append 还没 target 回复), 先补跑一轮
            #    target 把 AI 回复跑完再 break, 避免留下 "UI 里 user 消息有
            #    但 AI 不回" 的半轮悬空状态 (用户直觉上会误解为 "消息没发到
            #    AI"). 补轮过程不再受 stop_event 影响 — 此时 stop 已经确认,
            #    只是在干净收尾. 如果 next_kind=simuser 或 messages 为空,
            #    直接 break — 没有欠账需要补.
            if stop_event.is_set():
                needs_final_target = (
                    session.auto_state["next_kind"] == "target"
                    and bool(session.messages)
                    and session.messages[-1].get("role") == ROLE_USER
                )
                if needs_final_target:
                    try:
                        async for ev in _run_target_turn(
                            session, config,
                            final_before_stop=True,
                        ):
                            yield ev
                    except SimUserError as exc:
                        reason = "error"
                        error_payload = {
                            "type": exc.code,
                            "message": exc.message,
                        }
                        break
                    except AutoDialogError as exc:
                        reason = "error"
                        error_payload = {
                            "type": exc.code,
                            "message": exc.message,
                        }
                        break
                    except Exception as exc:  # noqa: BLE001 — 防御兜底
                        reason = "error"
                        error_payload = {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        }
                        python_logger().exception(
                            "auto_dialog stop-final-turn error"
                            " (session=%s): %s",
                            session.id, exc,
                        )
                        break
                reason = "user_stop"
                break

            # 3) 已跑完?
            completed = session.auto_state["completed_turns"]
            if completed >= config.total_turns:
                reason = "completed"
                break

            kind = session.auto_state["next_kind"]
            # NB: 进度分子 = 已完成 target 数. 当下一步是 target 时, 它补完
            # 完成数 +1 回到一致; 当下一步是 simuser 时, 下一个 target 完成
            # 后分子才 +1, 所以 simuser 阶段 UI 显示 "第 completed+1 轮生成中".

            try:
                if kind == "simuser":
                    async for ev in _run_simuser_step(session, config):
                        yield ev
                    # sim 成功 → 下一步必是 target
                    session.auto_state["next_kind"] = "target"
                    # 若 SimUser 刚跑完用户就点 stop, 下一圈循环头的 stop
                    # gate 会走 "needs_final_target" 分支补完 target 再 break,
                    # 不会留下半轮悬空.
                else:
                    async for ev in _run_target_turn(session, config):
                        yield ev
            except SimUserError as exc:
                reason = "error"
                error_payload = {
                    "type": exc.code,
                    "message": exc.message,
                }
                break
            except AutoDialogError as exc:
                reason = "error"
                error_payload = {
                    "type": exc.code,
                    "message": exc.message,
                }
                break
            except Exception as exc:  # noqa: BLE001 — 防御性兜底
                reason = "error"
                error_payload = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                python_logger().exception(
                    "auto_dialog unexpected error (session=%s): %s",
                    session.id, exc,
                )
                break
    finally:
        # 先快照终局 completed_turns, 后面 stopped/error yield 还要用; 然后
        # 再 log + 清 auto_state. 顺序敏感: log_sync 之后 auto_state 会立刻
        # 变 None 让 GET /state 回报空闲, 所以任何"清空后还想读"的字段都
        # 必须提前转成局部变量.
        if session.auto_state is not None:
            final_completed = session.auto_state.get("completed_turns", 0)
        session.logger.log_sync(
            "auto_dialog.end",
            payload={
                "reason": reason,
                "completed_turns": final_completed,
                "total_turns": config.total_turns,
                "error": error_payload,
            },
        )
        # 2026-04-22 Day 8 验收反馈: RateLimitError (429) / SimUser LlmFailed
        # / 其他 runtime error 之前**只走 SSE** 到前端, 不进 diagnostics
        # ring buffer, 顶栏 Err 徽章 + Diagnostics → Errors 页都看不见.
        # 集中在 finally 里兜, 覆盖三处 except 分支, 未来新增 except 也
        # 自动受益. level="error" 让徽章计数 +1 (warning 是轻量 advisory).
        if error_payload is not None:
            try:
                diagnostics_store.record_internal(
                    op="auto_dialog_error",
                    message=(
                        f"[auto_dialog] {error_payload.get('type', 'Error')}: "
                        f"{error_payload.get('message', '')}"
                    ),
                    level="error",
                    session_id=session.id,
                    detail={
                        "type": error_payload.get("type"),
                        "completed_turns": final_completed,
                        "total_turns": config.total_turns,
                    },
                )
            except Exception as rec_exc:  # noqa: BLE001 — 不因日志写入失败阻塞
                python_logger().warning(
                    "diagnostics_store.record_internal failed"
                    " (session=%s): %s", session.id, rec_exc,
                )
        session.auto_state = None

    if error_payload is not None:
        yield {"event": "error", "error": error_payload}

    yield {
        "event": "stopped",
        "reason": reason,
        "completed_turns": final_completed,
        "total_turns": config.total_turns,
    }


# ── step runners ────────────────────────────────────────────────────


async def _run_simuser_step(
    session: Session,
    config: AutoDialogConfig,
) -> AsyncIterator[dict[str, Any]]:
    """SimUser 生成一条 user 消息, 通过 stream_send 继续跑 target 回复.

    注意: 这里**只跑 SimUser**, 生成的 user 落盘 + yield 给上层是后续
    target step 里 ``stream_send`` 做的事. 单独拎出 SimUser 是为了让
    failure semantics 清晰 — sim 失败时 target 还没开始, 不会留半条
    messages.

    实际上我们把 sim 生成 + target stream **合并**在一个复合步: sim 拿到
    content 后立刻 stage clock + 调 stream_send. 但这样 pause gate 就失去
    了"在 sim 和 target 之间再歇一下"的能力. 权衡下来: 保持 sim / target
    两步独立调 — sim step 只 yield ``simuser_done`` 不调 stream_send, 由
    外层循环下一次走 target 分支把它 append 到 messages.

    ⚠️ 这会导致一个"半成品"窗口: SimUser 产出的 content 还没进 messages,
    下次循环去跑 target 的 ``user_content=None`` 路径会发现末尾不是 user
    而是 assistant (或空), 拒执行. 解决方案: **SimUser step 自己就把 user
    消息 append 到 messages** 并 yield ``{event:'simuser_done', message}``,
    之后 target step 走 ``user_content=None`` 路径 — messages 末尾确实是
    user 了. 这样两步完全解耦, 中间允许 pause/stop 介入, 而语义清晰.
    """
    draft = await generate_simuser_message(
        session,
        style=config.simuser_style,
        user_persona_prompt=config.simuser_persona_hint,
        extra_hint=config.simuser_extra_hint,
    )
    # P25 r7: SimUser LLM 不再 stamp ``session.last_llm_wire`` —
    # 见 ``simulated_user.py::generate_simuser_message`` 文档. Target
    # 那一侧 (``stream_send`` with ``source=SOURCE_AUTO``) 仍会 stamp
    # ``auto_dialog_target``, 这是测试人员真正关心的 wire.

    # r5 T8: prompt-injection audit — Auto-Dialog 走的是
    # ``stream_send(user_content=None)`` 复跑路径, 所以 chat_runner 内的
    # 扫描看不到 SimUser 生成的 user 文本, 也看不到 tester 在
    # ``simuser_persona_hint`` / ``simuser_extra_hint`` 里埋的自定义提示.
    # 这是 Subagent C r5 审计中 5 个 P0 缺口之一. 我们分三个字段扫,
    # 每个字段独立 record — 让 tester 在 Diagnostics → Errors 能分清
    # 是 "SimUser 自己写 out" 还是 "我 tester 写的 persona_hint" 命中了.
    try:
        from tests.testbench.pipeline import injection_audit as _ia
        _ia.scan_many(
            {
                "draft_content": draft.content or "",
                "persona_hint": config.simuser_persona_hint or "",
                "extra_hint": config.simuser_extra_hint or "",
            },
            source_prefix="auto_dialog.simuser",
            session_id=getattr(session, "id", None),
            extra={"style": config.simuser_style},
        )
    except Exception as exc:  # noqa: BLE001
        from tests.testbench.logger import python_logger
        python_logger().warning(
            "auto_dialog simuser injection_audit skipped: %s: %s",
            type(exc).__name__, exc,
        )

    # 手工追加 user 消息到 session.messages — 复制 chat_runner stream_send
    # 里相同的 make_message + append 流程, 但 source=SOURCE_AUTO 区分 "自动
    # 模式下 SimUser 生成的" 与 "用户在 SimUser 模式下生成到 textarea 后
    # 手动 Send 的" (后者是 SOURCE_SIMUSER).
    from tests.testbench.chat_messages import make_message  # lazy, 避免循环

    # Auto 模式下的 user 消息时间戳: 不单独 advance clock, 跟目前游标对齐.
    # target step 里 stream_send 会自动 consume pending + 可能 advance, 之
    # 后的 assistant 时间戳会落在更晚处 — 即 "用户在 t0 说话, 模型在 t0 +
    # step_seconds 时回复", 语义对得上.
    user_msg = make_message(
        role=ROLE_USER,
        content=draft.content,
        timestamp=session.clock.now(),
        source=SOURCE_AUTO,
    )
    # P24 §12.5: Auto-Dialog uses coerce like SSE chat path. Dialog can't
    # fail mid-stream; if clock was rewound, monotonicity is preserved by
    # bumping ts forward. For Auto-Dialog the user-visible surfacing is
    # the op=timestamp_coerced diagnostics entry; auto_dialog's own SSE
    # frames are per-turn progress events and don't carry per-message
    # warnings, so users find this via Diagnostics → Errors.
    _auto_result = append_message(session, user_msg, on_violation="coerce")
    user_msg = _auto_result.msg

    session.logger.log_sync(
        "auto_dialog.simuser.done",
        payload={
            "message_id": user_msg["id"],
            "content_chars": len(draft.content),
            "style": draft.style,
            "elapsed_ms": draft.elapsed_ms,
            "token_usage": draft.token_usage,
            "warnings": draft.warnings,
        },
    )

    yield {
        "event": "simuser_done",
        "message": user_msg,
        "warnings": draft.warnings,
    }


async def _run_target_turn(
    session: Session,
    config: AutoDialogConfig,
    *,
    final_before_stop: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """跑完整一"轮" target: stage clock → turn_begin → _run_target_step
    (含 stream_send 全流) → completed_turns +=1 + next_kind='simuser'
    → turn_done.

    抽出来供主循环 "正常 kind=='target' 分支" 和 stop gate 的 "补完
    当前轮" 分支共用, 避免两份同构代码漂移 (上一版就在主循环写了一份,
    stop 里想补就得再复制一份, 注定失同步).

    ``final_before_stop=True`` 仅作为 SSE ``turn_begin`` 的一个字段透传
    给前端, 让 banner 可选地显示 "最后一轮补完中" 之类的 UI hint. 后端
    行为与 ``False`` 完全相同 — stop gate 的"补轮"一旦启动就必须跑完,
    不再受 stop_event 影响 (stop_event 已经是 True 了).
    """
    completed_before = session.auto_state["completed_turns"]
    step_seconds: int | None = None
    if config.step_mode == "fixed":
        step_seconds = config.step_seconds
        session.clock.stage_next_turn(
            delta=timedelta(seconds=step_seconds),
        )
    yield {
        "event": "turn_begin",
        "index": completed_before + 1,
        "kind": "target",
        "step_seconds": step_seconds,
        "final_before_stop": final_before_stop,
    }
    # 首步是 target 时, messages 末尾已有 user, 不追加新 user;
    # 其它情况 (sim 刚追加 user), 也不能再追加 → 同样走 user_content=None
    # 路径. 两条路径统一 (见 chat_runner.stream_send 的 None 分支 guard).
    async for ev in _run_target_step(session):
        yield ev
    session.auto_state["completed_turns"] += 1
    session.auto_state["next_kind"] = "simuser"
    yield {
        "event": "turn_done",
        "completed_turns": session.auto_state["completed_turns"],
        "total_turns": config.total_turns,
    }


async def _run_target_step(session: Session) -> AsyncIterator[dict[str, Any]]:
    """跑 target AI 回复当前 messages 末尾的 user 消息.

    前提: messages 末尾必须是 role=user 的消息 (_run_simuser_step 刚 append
    过, 或 adaptive 首步的 "欠账 user"). 否则 chat_runner 会在 user_content
    =None 的 guard 里 ValueError.

    SSE 事件完全透传 stream_send — 前端的 message_stream.js 已经能解 wire_
    built / assistant_start / delta / assistant / done / error 六种, 无需
    额外适配. 这也意味着 Auto-Dialog 期间 Target AI 的流式渲染与手动 Send
    完全一致.

    stream_send 内部失败时 yield ``{event:'error'}`` 而不抛异常; 本函数把
    这条透传上去, 上层循环检测到后通过 ``sentinel`` 机制终止.  为了不把
    "stream_send 成功但 assistant 空串" 当成错误, 我们只在看到 ``event=
    error`` 时设 failed 标记.
    """
    backend = get_chat_backend()
    failed_error: dict[str, Any] | None = None

    async for ev in backend.stream_send(
        session,
        user_content=None,
        source=SOURCE_AUTO,
    ):
        yield ev
        if ev.get("event") == "error":
            failed_error = ev.get("error") or {
                "type": "ChatStreamError",
                "message": "target stream failed without detail",
            }
            # 不 break — stream_send 里 error 后就 return 了, 这里也就没有
            # 更多事件可迭代. 为了安全起见 break 一下.
            break

    if failed_error is not None:
        raise AutoDialogError(
            failed_error.get("type") or "ChatStreamError",
            failed_error.get("message") or "Target AI 回复失败",
            status=502,
        )


# ── control (pause / resume / stop) ─────────────────────────────────


def request_pause(session: Session) -> dict[str, Any]:
    """把 running_event clear 掉, 让下一次循环头阻塞.

    无运行中 Auto-Dialog → ``AutoDialogError('AutoNotRunning', ...)``.
    已经是 paused → 幂等 (clear 两次等价于 clear 一次).
    """
    if session.auto_state is None:
        raise AutoDialogError(
            "AutoNotRunning",
            "当前没有正在运行的 Auto-Dialog",
            status=409,
        )
    event: asyncio.Event | None = session.auto_state.get("running_event")
    if event is None:  # 理论不可达: start 必设
        raise AutoDialogError(
            "AutoStateCorrupted",
            "auto_state 缺 running_event, 请 stop 后重启",
            status=500,
        )
    event.clear()
    session.logger.log_sync("auto_dialog.pause.request", payload={})
    return {"status": "paused"}


def request_resume(session: Session) -> dict[str, Any]:
    """set running_event 解封 pause gate. 幂等."""
    if session.auto_state is None:
        raise AutoDialogError(
            "AutoNotRunning",
            "当前没有正在运行的 Auto-Dialog",
            status=409,
        )
    event: asyncio.Event | None = session.auto_state.get("running_event")
    if event is None:
        raise AutoDialogError(
            "AutoStateCorrupted",
            "auto_state 缺 running_event, 请 stop 后重启",
            status=500,
        )
    event.set()
    session.logger.log_sync("auto_dialog.resume.request", payload={})
    return {"status": "running"}


def request_stop(session: Session) -> dict[str, Any]:
    """set stop_event + set running_event 把可能卡在 pause 的 generator 唤醒.

    幂等. stop 后真实终止发生在下一次循环头 (graceful): 当前 step 跑完 + 下
    一次循环头的 stop 判断会 break. 期间 yield 完成的事件都保留不丢.
    """
    if session.auto_state is None:
        raise AutoDialogError(
            "AutoNotRunning",
            "当前没有正在运行的 Auto-Dialog",
            status=409,
        )
    running: asyncio.Event | None = session.auto_state.get("running_event")
    stop: asyncio.Event | None = session.auto_state.get("stop_event")
    if running is None or stop is None:
        raise AutoDialogError(
            "AutoStateCorrupted",
            "auto_state 缺 event, 请杀服务重启",
            status=500,
        )
    stop.set()
    running.set()  # 解封可能卡在 pause wait 的 generator
    session.logger.log_sync("auto_dialog.stop.request", payload={})
    return {"status": "stopping"}


# ── observation ─────────────────────────────────────────────────────


def describe_auto_state(session: Session) -> dict[str, Any]:
    """``GET /api/chat/auto_dialog/state`` 的 payload.

    ``is_running`` 便于前端一眼看出要不要挂 SSE 重连 / 显示 banner.
    """
    snap = _public_auto_state(session)
    return {
        "is_running": snap is not None,
        "auto_state": snap,
    }


__all__ = [
    "AutoDialogConfig",
    "AutoDialogError",
    "MAX_STEP_SECONDS",
    "MAX_TOTAL_TURNS",
    "MIN_STEP_SECONDS",
    "STEP_MODES",
    "describe_auto_state",
    "request_pause",
    "request_resume",
    "request_stop",
    "run_auto_dialog",
]
