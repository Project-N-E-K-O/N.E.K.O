"""回归：``install_hooks()`` 必须幂等，不能叠加 monkey-patch。

合并单进程模式（打包 / Steam 版，见 launcher 的 _run_merged）把 main / memory /
agent 三个 uvicorn app 跑在同一进程，三个 app 的 startup 各调一次
``install_hooks()``，打在同一个进程级 ``Completions.create`` 上。没有幂等守卫时
wrapper 会逐层叠加 —— 每个 chat.completions 调用被 record 多次，conversation /
emotion / proactive / galgame_options 等走 hook 的 call_type 在遥测里精确翻 N 倍
（线上 Steam 版三 app 实测 ×3）。

本测试钉住：连调多次 ``install_hooks()`` 后，一次 create 调用只 record 一次。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import utils.token_tracker as tt
import utils.token_tracker._shared as tracker_shared


@pytest.fixture
def restore_openai_create():
    """隔离全局副作用：测试结束后把被 patch 的 SDK 方法还原。"""
    from openai.resources.chat.completions import AsyncCompletions, Completions

    orig_sync = Completions.create
    orig_async = AsyncCompletions.create
    try:
        yield Completions, AsyncCompletions
    finally:
        Completions.create = orig_sync
        AsyncCompletions.create = orig_async


def _fake_usage_response():
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        prompt_tokens_details=None,
    )
    return SimpleNamespace(usage=usage, model="fake-model")


def test_install_hooks_idempotent_no_stacking(restore_openai_create):
    Completions, _AsyncCompletions = restore_openai_create

    # 用不打网络的假 create 当"原始" SDK 方法（未带我们的 hook 标记）
    def fake_create(self, *args, **kwargs):
        return _fake_usage_response()

    Completions.create = fake_create

    rec = MagicMock()
    fake_tracker = SimpleNamespace(record=rec)

    with patch.object(tt.TokenTracker, "get_instance", return_value=fake_tracker):
        # 模拟合并模式：main / memory / agent 三个 startup 各装一次
        tt.install_hooks()
        installed_once = Completions.create
        tt.install_hooks()
        tt.install_hooks()

        # 守卫生效：第 2、3 次安装是 no-op，函数对象不变（没有重新包一层）
        assert Completions.create is installed_once
        assert getattr(Completions.create, "_neko_token_tracker_hooked", False) is True

        # 一次非流式调用
        Completions.create(SimpleNamespace(), model="fake-model", stream=False)

    # 关键不变量：record 恰好一次，而不是三次（叠层会是 3）
    assert rec.call_count == 1, f"expected 1 record, got {rec.call_count}（wrapper 叠层）"


def test_install_hooks_records_once_after_single_install(restore_openai_create):
    """单次安装的基线：一次调用记一次（与叠层场景形成对照）。"""
    Completions, _AsyncCompletions = restore_openai_create

    def fake_create(self, *args, **kwargs):
        return _fake_usage_response()

    Completions.create = fake_create
    rec = MagicMock()

    with patch.object(
        tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)
    ):
        tt.install_hooks()
        Completions.create(SimpleNamespace(), model="fake-model", stream=False)

    assert rec.call_count == 1


@pytest.mark.asyncio
async def test_install_hooks_idempotent_async_path(restore_openai_create):
    """异步路径同样不叠层。

    生产里 conversation 走 LangChain astream → AsyncCompletions.create，异步分支
    才是主路径；它与同步分支共用 _neko_token_tracker_hooked 守卫，但单独钉一遍，
    避免将来有人只改其中一条分支时静默漏掉。
    """
    _Completions, AsyncCompletions = restore_openai_create

    async def fake_async_create(self, *args, **kwargs):
        return _fake_usage_response()

    AsyncCompletions.create = fake_async_create
    rec = MagicMock()

    with patch.object(
        tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)
    ):
        # 合并模式：main / memory / agent 三个 startup 各装一次
        tt.install_hooks()
        installed_once = AsyncCompletions.create
        tt.install_hooks()
        tt.install_hooks()

        # 与同步测试对称：第 2、3 次安装是 no-op，异步函数对象不变（没有重新包一层），
        # 且守卫标记已设。缺这两个断言时，若将来异步分支的守卫被误删而 wrapper 恰好
        # 还能跑一次，仅 record 计数无法发现。
        assert AsyncCompletions.create is installed_once
        assert getattr(AsyncCompletions.create, "_neko_token_tracker_hooked", False) is True

        await AsyncCompletions.create(SimpleNamespace(), model="fake-model", stream=False)

    assert rec.call_count == 1, f"expected 1 record, got {rec.call_count}（async wrapper 叠层）"


def test_record_anthropic_usage_maps_messages_usage_fields():
    rec = MagicMock()

    with patch.object(
        tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)
    ), tt.llm_call_context("conversation"):
        tt.record_anthropic_usage(
            "claude-test",
            {
                "input_tokens": 100,
                "output_tokens": 25,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 80,
            },
        )

    rec.assert_called_once_with(
        model="claude-test",
        prompt_tokens=200,
        completion_tokens=25,
        total_tokens=225,
        cached_tokens=80,
        call_type="conversation",
    )


def test_file_lock_closes_descriptor_after_initial_write_failure(monkeypatch, tmp_path):
    """A failed lock metadata write must not leak its opened descriptor."""
    real_write = tracker_shared.os.write
    real_close = tracker_shared.os.close
    write_attempts = 0
    closed_descriptors = []

    def flaky_write(fd, data):
        nonlocal write_attempts
        write_attempts += 1
        if write_attempts == 1:
            raise OSError("simulated lock metadata write failure")
        return real_write(fd, data)

    def tracked_close(fd):
        closed_descriptors.append(fd)
        return real_close(fd)

    monkeypatch.setattr(tracker_shared.os, "write", flaky_write)
    monkeypatch.setattr(tracker_shared.os, "close", tracked_close)

    with tracker_shared._file_lock(tmp_path / "token-tracker.lock"):
        pass

    assert write_attempts == 2
    assert len(closed_descriptors) == 2
