"""A plugin child must not hold the host's ingest credential.

Plugin hosts are started with a bare ``multiprocessing.Process``
(plugin/core/host.py), so the start method is the platform default: spawn on
Windows, FORK on POSIX. Under fork the child inherits ``plane_bridge`` already
imported, with the host's token in it, and plugin code can simply ``import`` it
and write authenticated deltas straight to ingest -- bypassing the per-host
uplink's identity stamping, which is the reason plugin messages are routed
through the host at all.

Coverage here is layered, and honestly so -- both CI pytest jobs run on
windows-latest, where children are SPAWNED and mint their own token regardless,
so the POSIX behaviour this fix exists for cannot be verified by anything CI
runs:

  re-mint helper   behaviour, runs everywhere
  hook wiring      AST assertion, runs everywhere -- the only layer that bites
                   on Windows if the registration is deleted or mis-wired
  raw fork         behaviour, POSIX only, skipped in this CI

A cross-platform variant that spawned a real ``multiprocessing.Process`` and
read the child's token was tried and removed: inside this suite on Windows the
spawned child cannot start (BrokenPipeError, WinError 109), so it passed alone
and failed deterministically under the full selection. A test that depends on
which files you select is worse than no test.
"""

from __future__ import annotations

import os
import sys

import pytest

import plugin.server.messaging.plane_bridge as plane_bridge

_HAS_FORK = hasattr(os, "fork") and hasattr(os, "register_at_fork")


@pytest.mark.plugin_unit
def test_re_minting_actually_replaces_the_credential() -> None:
    """The helper itself, on every platform.

    Mutation: make ``_remint_ingest_token_in_child`` a no-op.
    """
    before = plane_bridge.ingest_auth_token()
    try:
        plane_bridge._remint_ingest_token_in_child()
        after = plane_bridge.ingest_auth_token()
        assert after != before
        assert len(after) >= 32, "重铸出来的凭证太短，等于削弱了它"
    finally:
        plane_bridge._INGEST_AUTH_TOKEN = before


@pytest.mark.plugin_unit
@pytest.mark.skipif(not _HAS_FORK, reason="POSIX only; the cross-platform test above covers Windows")
def test_a_raw_fork_child_does_not_inherit_the_token() -> None:
    """Fork directly, with no multiprocessing machinery in between.

    This is the narrow case: the hook has to fire on a bare ``os.fork`` too,
    because that is what multiprocessing does underneath on POSIX.
    """
    parent_token = plane_bridge.ingest_auth_token()
    read_fd, write_fd = os.pipe()

    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the child
        try:
            os.close(read_fd)
            os.write(write_fd, plane_bridge.ingest_auth_token().encode("utf-8"))
            os.close(write_fd)
        finally:
            os._exit(0)

    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as handle:
        child_token = handle.read().decode("utf-8")
    os.waitpid(pid, 0)

    assert child_token and child_token != parent_token


@pytest.mark.plugin_unit
def test_the_after_fork_hook_is_registered_wherever_fork_exists() -> None:
    """The half of the fix that CI cannot otherwise see.

    Both pytest jobs in this repo run on windows-latest, where children are
    SPAWNED and therefore mint their own token no matter what -- so removing the
    ``register_at_fork`` call is invisible to every other test here. This asserts
    the registration happened, which is checkable on any platform even though
    the behaviour it buys only exists on POSIX.

    It proves the call ran, not that fork re-mints; ``test_a_raw_fork_child_...``
    proves that, and only runs on POSIX.

    Mutation: drop the ``os.register_at_fork`` registration.
    """
    # 平台判据在 Windows 上是 False，所以 `_FORK_HOOK_REGISTERED == hasattr(...)`
    # 会退化成 False == False 恒真——变异验证抓到了这一点（去掉注册仍然绿）。
    # 唯一能在 Windows 上真正咬住的是**结构断言**：这条接线在源码里存在，且接
    # 的是重铸函数。它弱于行为验证，这就是分工：
    #   这条        —— 到处都跑，证明接线还在
    #   raw fork    —— 只在 POSIX 跑，证明接线真的起作用
    import ast
    import inspect

    source = inspect.getsource(plane_bridge)
    tree = ast.parse(source)

    wired = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "register_at_fork"):
            continue
        for kw in node.keywords:
            if kw.arg == "after_in_child" and isinstance(kw.value, ast.Name):
                wired = kw.value.id == "_remint_ingest_token_in_child"
    assert wired, (
        "plane_bridge 里没有把 _remint_ingest_token_in_child 接到 "
        "os.register_at_fork(after_in_child=...)——forked 插件子进程会继承宿主的 "
        "ingest 凭证，而本仓两个 pytest job 都跑 Windows，行为测试看不见这一点"
    )

    if hasattr(os, "register_at_fork"):
        assert plane_bridge._FORK_HOOK_REGISTERED, "支持 fork 的平台上却没注册"


# ── the token has more than one home in a forked child ─────────────────


@pytest.mark.plugin_unit
def test_the_child_scrub_reaches_the_runner_and_its_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reminting the module global leaves the startup copies intact.

    The same credential is handed to ``build_message_plane_runner`` and lives on
    at ``_service._message_plane_runner._auth_token`` and on the ingest server
    that runner holds — both module-level singletons a forked child inherits.

    Mutation: drop the sweep over the runner's attributes, or the runner blank.
    """
    from types import SimpleNamespace

    from plugin.server.messaging import plane_bridge

    secret = "the-real-ingest-secret"
    ingest = SimpleNamespace(_auth_token=secret)
    rpc = SimpleNamespace(_auth_token=secret)
    runner = SimpleNamespace(_auth_token=secret, _ingest=ingest, _rpc=rpc, _pub=None)
    service = SimpleNamespace(_message_plane_runner=runner)
    monkeypatch.setitem(
        sys.modules, "plugin.server.lifecycle", SimpleNamespace(_service=service)
    )

    plane_bridge._remint_ingest_token_in_child()

    assert runner._auth_token == "", "runner 自己那份没擦"
    assert ingest._auth_token == "", "ingest server 上那份没擦"
    assert rpc._auth_token == "", "只擦了点名的那几个——扫描没覆盖到"
    assert service._message_plane_runner is None
    assert plane_bridge.ingest_auth_token() not in ("", secret)


@pytest.mark.plugin_unit
def test_the_scrub_is_a_no_op_without_an_inherited_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It runs in an ``after_in_child`` hook, where raising is not an option."""
    monkeypatch.delitem(sys.modules, "plugin.server.lifecycle", raising=False)

    plane_bridge_mod = __import__(
        "plugin.server.messaging.plane_bridge", fromlist=["x"]
    )
    plane_bridge_mod._remint_ingest_token_in_child()  # must not raise
