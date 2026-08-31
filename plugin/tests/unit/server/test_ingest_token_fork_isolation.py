"""A plugin child must not hold the host's ingest credential.

Plugin hosts are started with a bare ``multiprocessing.Process``
(plugin/core/host.py), so the start method is the platform default: spawn on
Windows, FORK on POSIX. Under fork the child inherits ``plane_bridge`` already
imported, with the host's token in it, and plugin code can simply ``import`` it
and write authenticated deltas straight to ingest -- bypassing the per-host
uplink's identity stamping, which is the reason plugin messages are routed
through the host at all.

The load-bearing test below uses the SAME start mechanism the product uses, so
it exercises whichever path this platform takes:

  spawn  -> the child re-imports and mints its own; nothing to undo
  fork   -> the child inherits, and the registered after-fork hook re-mints

Both CI pytest jobs run on windows-latest, so a POSIX-only test here would never
execute anywhere -- not locally, not in CI. That is why the cross-platform test
is the one that carries the property, and the fork-specific one only adds
detail where it can run.
"""

from __future__ import annotations

import multiprocessing
import os

import pytest

import plugin.server.messaging.plane_bridge as plane_bridge

_HAS_FORK = hasattr(os, "fork") and hasattr(os, "register_at_fork")


def _report_child_token(conn) -> None:
    """Runs in the child. Module-level so spawn can pickle it by reference."""
    import plugin.server.messaging.plane_bridge as child_bridge

    conn.send(child_bridge.ingest_auth_token())
    conn.close()


@pytest.mark.plugin_unit
def test_a_plugin_child_never_holds_the_hosts_ingest_token() -> None:
    """The property, over whichever start method this platform uses.

    Mutation: drop the ``os.register_at_fork`` registration (fails on POSIX);
    make ``_remint_ingest_token_in_child`` a no-op (same).
    """
    parent_token = plane_bridge.ingest_auth_token()

    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    proc = multiprocessing.Process(target=_report_child_token, args=(child_conn,))
    proc.start()
    child_conn.close()
    try:
        assert parent_conn.poll(30), "子进程没在 30s 内回报 token"
        child_token = parent_conn.recv()
    finally:
        proc.join(timeout=30)
        parent_conn.close()

    assert child_token, "前提没成立：子进程回报了空 token"
    assert child_token != parent_token, (
        f"插件子进程（start method={multiprocessing.get_start_method()}）拿到了"
        "宿主的 ingest 凭证——插件代码 import 一下就能绕过每主机上行的身份盖章"
    )


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
