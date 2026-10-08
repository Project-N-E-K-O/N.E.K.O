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

"""``ManagerHost``: the bounded waits on the ordinary session (main-turn interruption, family turn)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from main_routers.visit_router import host_port
from main_routers.visit_router.host_port import ManagerHost


class _Session:
    def __init__(self, *, responding: bool = False, interrupt_s: float = 0.0) -> None:
        self._is_responding = responding
        self._interrupt_s = interrupt_s

    async def handle_interruption(self) -> None:
        await asyncio.sleep(self._interrupt_s)


def _host(session: _Session) -> ManagerHost:
    return ManagerHost("Host", SimpleNamespace(session=session))


async def test_main_turn_interruption_has_one_deadline_for_both_stages(monkeypatch):
    monkeypatch.setattr(host_port, "_TURN_IDLE_POLL_S", 0.02)
    # 打断本身用掉大半期限、turn 一直不结束：总耗时仍以 timeout 为界（不是打断 + 再等一整个 timeout）
    session = _Session(responding=True, interrupt_s=0.3)
    started = time.monotonic()
    assert await _host(session).interrupt_main_turn(0.4) is False
    assert time.monotonic() - started < 0.55


async def test_family_turn_wait_covers_a_reply_that_starts_late(monkeypatch):
    monkeypatch.setattr(host_port, "_TURN_IDLE_POLL_S", 0.02)
    session = _Session(responding=False)

    async def family_turn() -> None:
        await asyncio.sleep(0.15)                    # 输入已到、回复稍后才开始
        session._is_responding = True
        await asyncio.sleep(0.25)
        session._is_responding = False

    turn = asyncio.ensure_future(family_turn())
    started = time.monotonic()
    await _host(session).wait_turn_idle(5.0, start_window=1.0)
    elapsed = time.monotonic() - started
    await asyncio.gather(turn)
    assert 0.35 <= elapsed < 1.0                      # 等到这一轮真正结束


async def test_family_turn_wait_gives_up_when_no_reply_starts(monkeypatch):
    monkeypatch.setattr(host_port, "_TURN_IDLE_POLL_S", 0.02)
    started = time.monotonic()
    await _host(_Session(responding=False)).wait_turn_idle(5.0, start_window=0.2)
    assert 0.15 <= time.monotonic() - started < 1.0
    started = time.monotonic()
    await _host(_Session(responding=False)).wait_turn_idle(5.0)
    assert time.monotonic() - started < 0.1           # 不给开始窗口：空闲就立即返回


async def test_status_writes_are_bounded_like_frames(monkeypatch):
    monkeypatch.setattr(host_port, "_FRAME_TIMEOUT_S", 0.1)
    stuck = asyncio.Event()

    async def send_status(message):
        await stuck.wait()                            # 页面 socket 背压：一直写不出去
        return True

    host = ManagerHost("Host", SimpleNamespace(send_status=send_status))
    started = time.monotonic()
    # 外层 3 s 只为让测试本身不挂死
    assert await asyncio.wait_for(host.send_status("VISIT_E_BUSY"), 3) is False
    assert time.monotonic() - started < 1.0
    stuck.set()


async def test_mirror_and_chat_block_writes_are_bounded(monkeypatch):
    monkeypatch.setattr(host_port, "_FRAME_TIMEOUT_S", 0.1)
    stuck = asyncio.Event()

    async def hang(*args, **kwargs):
        await stuck.wait()                            # 页面 socket 背压：写不出去
        return True

    host = ManagerHost("Host", SimpleNamespace(mirror_assistant_output=hang, render_chat_blocks=hang))
    started = time.monotonic()
    # 外层 3 s 只为让测试本身不挂死
    await asyncio.wait_for(host.mirror_assistant_output("我回来啦。", metadata={}, request_id="r"), 3)
    assert await asyncio.wait_for(host.render_chat_blocks([], request_id="r", source_name="s"), 3) is False
    assert time.monotonic() - started < 1.0
    stuck.set()


async def test_session_start_answers_are_bounded(monkeypatch):
    monkeypatch.setattr(host_port, "_FRAME_TIMEOUT_S", 0.1)
    stuck = asyncio.Event()

    async def hang(*args, **kwargs):
        await stuck.wait()                            # 页面不收

    host = ManagerHost("Host", SimpleNamespace(send_session_started=hang, send_session_failed=hang))
    started = time.monotonic()
    # 外层 3 s 只为让测试本身不挂死
    await asyncio.wait_for(host.ack_text_session("r1"), 3)
    await asyncio.wait_for(host.fail_session("audio", "r2"), 3)
    assert time.monotonic() - started < 1.0
    stuck.set()


def test_family_spoke_reads_only_real_user_input():
    # 回声 / 空转写 / start_session 都会刷新 last_user_activity_time；「亲人先开口」只认真实输入
    mgr = SimpleNamespace(last_user_activity_time=200.0, last_user_message_time=100.0)
    assert ManagerHost("Host", mgr).last_user_input() == 100.0
    assert ManagerHost("Host", SimpleNamespace(last_user_activity_time=200.0)).last_user_input() == 0.0
