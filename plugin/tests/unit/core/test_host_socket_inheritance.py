"""A forked plugin child must not hold the host's HTTP server sockets.

Plugin hosts are started with a bare ``multiprocessing.Process``
(plugin/core/host.py), so the start method is the platform default: spawn on
Windows and on macOS run from source, FORK on Linux -- and FORK on macOS too in
the packaged (merged) topology, where ``app/main_server/__init__.py`` calls
``multiprocessing.set_start_method("fork")``. Under fork the child copies the
host's whole descriptor table, and that table holds the host's HTTP listening
sockets plus every browser connection the host has already accepted.

The damage is not a descriptor count creeping up. A TCP socket dies when its
LAST reference is closed, so while a child holds one, the host's own ``close()``
stops sending FIN: the peer (the browser, and internal httpx pools alike) never
learns the connection is gone, keeps it in its keep-alive pool, and the next
request on it lands in a receive buffer that nothing will ever read. The plugin
manager page then hangs until the tab is reloaded -- and re-poisons on the next
fork.

Which ports that means depends on the topology, and the rule has to cover all of
them: the forking process is agent_server when running from source (agent + plugin
servers only), but the SINGLE merged process in packaged builds (main + memory +
agent + plugin servers). Hence four ports, not two.

Scope is deliberately narrow in the other direction. libzmq's TCP listeners are
ordinary AF_INET listening sockets that also sit in this process's descriptor
table, and they must NOT be touched: they hang off the process-global
``zmq.Context.instance()``, and libzmq's own bookkeeping still lists those fd
numbers after the fork, so closing them behind its back means ``zmq_ctx_term``
can later close an unrelated fd that happens to have been reused. That is why the
rule is a port allow-list and not "every listening socket this process owns".
AF_UNIX is left alone because ``state.plugin_response_map``'s Manager proxies are
inherited on purpose.

Coverage here is layered, and honestly so -- both CI pytest jobs run on
windows-latest, where children are SPAWNED and inherit nothing, so the naive
"fork a process and look" test would skip everywhere it could run:

  classification   behaviour, runs everywhere -- the fd set is injected, so the
                   rule that decides WHAT to close is checked on Windows too
  enumeration      behaviour, needs /proc/self/fd or /dev/fd, skipped in this CI
  guard rails      behaviour, runs everywhere
  hook wiring      AST assertion, runs everywhere -- the only layer that bites
                   on Windows if the registration is deleted
  raw fork         behaviour, POSIX only, skipped in this CI
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from plugin.core import host

_HAS_FORK = hasattr(os, "fork") and hasattr(os, "register_at_fork")
_FD_DIRS = tuple(directory for directory in ("/proc/self/fd", "/dev/fd") if os.path.isdir(directory))
_HAS_FD_DIR = bool(_FD_DIRS)


def _is_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return True
    return False


def _detach_and_close(sock: socket.socket) -> None:
    """Release the raw fd exactly once, whoever still owns it.

    Some of these sockets get closed by the hook under test; letting the Python
    wrapper close the number a second time would target whatever has since been
    handed that fd.
    """
    try:
        os.close(sock.detach())
    except OSError:
        pass


@pytest.mark.plugin_unit
def test_it_closes_sockets_on_every_host_http_port_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two host services, one ZMQ-shaped listener, and a client.

    The listener and the sockets it accepted share one local port. That is what
    makes the rule exact rather than a heuristic: a client socket picks an
    ephemeral local port, so it can never be mistaken for a socket the host
    serves.

    Two ports are in the rule on purpose, because the forking process serves more
    than the plugin server: packaged builds run the merged topology (main + memory
    + agent + plugin in ONE process), so 48911's accepted browser connections are
    inherited exactly like 48916's. Closing only the latter is the P1 regression
    this case pins -- the main UI keeps its poisoned keep-alive.

    Mutation: match the peer port instead of the local port, drop accepted
    connections, or shrink the allow-list back to the plugin server port alone --
    the first two leave the hanging request behind, the third leaves the main UI
    exposed.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host_port = listener.getsockname()[1]

    browser = socket.create_connection(("127.0.0.1", host_port))  # 客户端：临时本地端口
    accepted, _ = listener.accept()  # 服务端：本地端口 == host_port

    # 同进程的第二个 HTTP 服务（merged 拓扑里的 main_server / memory_server，
    # 本条用 48911 同款关系：监听 + 一条已 accept 的浏览器连接）
    other_listener = socket.socket()
    other_listener.bind(("127.0.0.1", 0))
    other_listener.listen(1)
    other_port = other_listener.getsockname()[1]
    other_browser = socket.create_connection(("127.0.0.1", other_port))
    other_accepted, _ = other_listener.accept()

    # 本进程的第三个监听，模拟 libzmq 的 TCP 监听：同样是 AF_INET 监听 socket，
    # 但不在端口允许表里 -> 必须留着（替它关会让 zmq_ctx_term 关错 fd）。
    zmq_like = socket.socket()
    zmq_like.bind(("127.0.0.1", 0))
    zmq_like.listen(1)

    manager = socket.socket(socket.AF_UNIX)  # Manager proxy 那一类：必须留着
    manager.bind(str(tmp_path / "manager.sock"))

    pipe_read, pipe_write = os.pipe()

    monkeypatch.setattr(host, "_HOST_HTTP_PORTS", frozenset({host_port, other_port}))
    # Inject the fd set so the classification is checked on platforms with no
    # fd directory at all -- the enumeration itself is covered by the test below.
    monkeypatch.setattr(
        host,
        "_iter_open_fds",
        lambda: iter(
            [
                listener.fileno(),
                accepted.fileno(),
                browser.fileno(),
                other_listener.fileno(),
                other_accepted.fileno(),
                other_browser.fileno(),
                zmq_like.fileno(),
                manager.fileno(),
                pipe_read,
                pipe_write,
            ]
        ),
    )

    try:
        host._close_inherited_host_sockets()

        assert host.inherited_host_sockets_closed() == 4, (
            "两个 HTTP 服务的监听 + 各自的 accept 连接都要关"
        )
        assert _is_closed(listener.fileno()), "宿主的监听 socket 还留在子进程手里"
        assert _is_closed(accepted.fileno()), (
            "accept 出来的连接没被关——就是它让浏览器那条 keep-alive 永久挂起"
        )
        assert _is_closed(other_listener.fileno()), (
            "同进程另一个 HTTP 服务（merged 拓扑下的 48911/48912）的监听没被关"
        )
        assert _is_closed(other_accepted.fileno()), (
            "同进程另一个服务的 accept 连接没被关——主界面的 keep-alive 就是这么被黑洞化的"
        )
        assert not _is_closed(browser.fileno()), (
            "误伤了客户端 socket（本地端口是临时的）"
        )
        assert not _is_closed(other_browser.fileno()), (
            "误伤了客户端 socket（本地端口是临时的）"
        )
        assert not _is_closed(zmq_like.fileno()), (
            "误伤了不在端口允许表里的监听 socket——libzmq 的 TCP 监听就是这种形状，"
            "替它关掉会让 zmq_ctx_term 去关一个号码被复用的 fd"
        )
        assert not _is_closed(manager.fileno()), (
            "误伤了 AF_UNIX——state.plugin_response_map 的 Manager proxy 靠它跨进程"
        )
        assert not _is_closed(pipe_read), "误伤了管道（fork 的哨兵管道是同类）"
    finally:
        for sock in (
            browser,
            accepted,
            listener,
            other_browser,
            other_accepted,
            other_listener,
            zmq_like,
            manager,
        ):
            _detach_and_close(sock)
        for fd in (pipe_read, pipe_write):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.mark.plugin_unit
@pytest.mark.skipif(
    not _HAS_FD_DIR, reason="needs /proc/self/fd or /dev/fd to enumerate this process"
)
def test_the_enumerated_fd_set_reaches_the_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same rule, but with the real enumeration instead of an injected set.

    The two halves are worth separating: this one fails if ``_iter_open_fds``
    stops yielding what the process actually holds (including on macOS, where the
    fd directory is fdescfs's /dev/fd), and it also pins down that the probe
    survives fds that are not sockets at all.

    Mutation: skip fds above 2, or drop the ``OSError`` guard around the socket
    wrap -- the first misses everything, the second kills plugin startup on the
    child's own pipe.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    pipe_read, pipe_write = os.pipe()

    monkeypatch.setattr(
        host, "_HOST_HTTP_PORTS", frozenset({listener.getsockname()[1]})
    )

    try:
        host._close_inherited_host_sockets()

        assert host.inherited_host_sockets_closed() >= 1, (
            "枚举出来的 fd 一个都没命中，钩子等于没接"
        )
        assert _is_closed(listener.fileno())
        assert not _is_closed(pipe_read), "误伤了管道"
    finally:
        _detach_and_close(listener)
        for fd in (pipe_read, pipe_write):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.mark.plugin_unit
def test_the_hook_is_wired_wherever_fork_exists() -> None:
    """Both pytest jobs run windows-latest, where ``os.fork`` does not exist.

    A fork-based behavioural test therefore skips everywhere it would run, and
    "the hook was never registered" would be an invisible mutation. This asserts
    the wiring from the source instead, and the registration flag on POSIX.

    Mutation: drop the ``os.register_at_fork`` registration.
    """
    import ast

    source = Path(host.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    wired = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "register_at_fork"):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "after_in_child"
                and isinstance(kw.value, ast.Name)
                and kw.value.id == "_close_inherited_host_sockets"
            ):
                wired = True

    assert wired, (
        "host.py 里没有把 _close_inherited_host_sockets 挂到 "
        "register_at_fork(after_in_child=...) 上——POSIX 下（含 macOS 的打包 merged "
        "拓扑）每个插件子进程都会继承宿主各个 HTTP 服务的监听 socket 和全部已 "
        "accept 的浏览器连接，宿主自己 close() 又不再发 FIN，那些连接会变成永远"
        "没人读的黑洞"
    )

    if hasattr(os, "register_at_fork"):
        assert host._FORK_SOCKET_HOOK_REGISTERED, "支持 fork 的平台上却没注册"


@pytest.mark.plugin_unit
@pytest.mark.skipif(
    not _HAS_FORK, reason="POSIX only; the injection test above covers Windows"
)
def test_a_raw_fork_child_releases_the_inherited_host_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fork directly, with no multiprocessing machinery in between.

    This is the narrow case: the hook has to fire on a bare ``os.fork`` too,
    because that is what multiprocessing does underneath on POSIX.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    browser = socket.create_connection(("127.0.0.1", listener.getsockname()[1]))
    accepted, _ = listener.accept()

    monkeypatch.setattr(
        host, "_HOST_HTTP_PORTS", frozenset({listener.getsockname()[1]})
    )

    read_fd, write_fd = os.pipe()

    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the child
        try:
            os.close(read_fd)
            report = ",".join(
                str(value)
                for value in (
                    host.inherited_host_sockets_closed(),
                    int(_is_closed(listener.fileno())),
                    int(_is_closed(accepted.fileno())),
                )
            )
            # 报告能写出来，本身就说明钩子没把这条管道当 socket 关掉。
            os.write(write_fd, report.encode("utf-8"))
            os.close(write_fd)
        finally:
            os._exit(0)

    os.close(write_fd)
    try:
        with os.fdopen(read_fd, "rb") as handle:
            report = handle.read().decode("utf-8")
    finally:
        os.waitpid(pid, 0)

    closed_count, listener_closed, accepted_closed = (
        int(part) for part in report.split(",")
    )

    assert closed_count >= 1, "fork 出来的子进程一个宿主 socket 都没清"
    assert listener_closed == 1, "宿主的监听 socket 还在子进程手里"
    assert accepted_closed == 1, "宿主的已 accept 连接还在子进程手里"
    assert not _is_closed(listener.fileno()), "父进程的监听 socket 不该受影响"

    for sock in (browser, accepted, listener):
        _detach_and_close(sock)
