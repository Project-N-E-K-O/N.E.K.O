"""市场包下载：写盘不得落在事件循环上，且总时长必须有兜底。

两个被修的缺陷（都在 ``_download_package_once``）：

1. **每 64KiB 一次同步 ``handle.write()``**。200MB 的包要和 loop 交织 3200 次阻塞写，
   而 Windows 上 Defender 扫一个刚创建的 ``.neko-plugin`` 会让每次写都可能卡顿——这
   期间插件服务器的**所有**路由（``/plugins``、``/plugin_cli``、``/runs``、
   ``/websocket``、插件 UI 流）都停摆。同一个函数里的哈希早就是 ``to_thread`` 的
   （``_verify_sha256_file`` 的调用点），写循环只是漏了。现在攒够
   ``_DOWNLOAD_FLUSH_BYTES`` 再交给线程，迭代粒度仍是 64KiB，所以取消检查与进度上报
   的密度不变。
2. **``httpx.Timeout(120.0)`` 是每阶段的**，一个"每次读都及时返回一点点字节"的服务器
   永远不会触发它。本文件里 ``_fetch_market_release`` 已经为同一个理由用了
   ``asyncio.timeout``（注释原文："HTTPX phase timeouts alone do not bound total
   response time"），下载这边没有。而且总时长到期抛的是内建 ``TimeoutError``，
   与 ``httpx.TimeoutException`` **不是同一个类**（实测 ``issubclass(...) is False``），
   不单独接住就会落到最后的 ``except Exception`` 裸抛——既拿不到 GitHub 直连的回退
   重试，用户看到的也不是"下载超时"。

变异清单（每条都应有测试变红）：
* 把 ``await _flush_pending()`` 换回 ``handle.write(chunk)`` → 2 红
* 去掉 ``async with asyncio.timeout(_DOWNLOAD_TOTAL_TIMEOUT)`` → 3 红
* 去掉 ``except TimeoutError`` 分支 → 4 红
"""

from __future__ import annotations

import asyncio
import hashlib
import http.server
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.routes import market_bridge as module

pytestmark = pytest.mark.plugin_unit


class _PackageHandler(http.server.BaseHTTPRequestHandler):
    """按 ``self.server.chunk`` 大小、``self.server.delay`` 间隔吐 ``self.server.payload``。"""

    def do_GET(self) -> None:  # noqa: N802 - stdlib 命名
        payload: bytes = self.server.payload  # type: ignore[attr-defined]
        chunk: int = self.server.chunk  # type: ignore[attr-defined]
        delay: float = self.server.delay  # type: ignore[attr-defined]
        limit: int = self.server.limit  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload) if limit <= 0 else limit))
        self.end_headers()
        sent = 0
        try:
            while sent < len(payload):
                piece = payload[sent : sent + chunk]
                self.wfile.write(piece)
                self.wfile.flush()
                sent += len(piece)
                if delay:
                    # 滴流式：每一片都及时返回，所以 httpx 的每阶段读超时永不触发。
                    import time as _time

                    _time.sleep(delay)
        except (BrokenPipeError, ConnectionResetError):  # pragma: no cover - 客户端提前断开
            pass

    def log_message(self, *args) -> None:  # 静音
        pass


@pytest.fixture
def http_server():
    def _start(payload: bytes, *, chunk: int = 65536, delay: float = 0.0, limit: int = -1):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _PackageHandler)
        srv.payload = payload  # type: ignore[attr-defined]
        srv.chunk = chunk  # type: ignore[attr-defined]
        srv.delay = delay  # type: ignore[attr-defined]
        srv.limit = limit  # type: ignore[attr-defined]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    started = []

    def factory(*args, **kwargs):
        srv = _start(*args, **kwargs)
        started.append(srv)
        return srv

    yield factory
    for srv in started:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def isolated_download_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把 artifacts root 指到 tmp_path，别碰真实用户目录。"""
    monkeypatch.setattr(
        module,
        "PluginCliPathPolicy",
        SimpleNamespace(from_settings=lambda: SimpleNamespace(package_artifacts_root=tmp_path)),
    )
    return tmp_path


def _url(srv) -> str:
    host, port = srv.server_address[0], srv.server_address[1]
    return f"http://{host}:{port}/pkg.neko-plugin"


@pytest.mark.asyncio
async def test_downloaded_bytes_are_exact_and_progress_is_reported(
    http_server, isolated_download_root
) -> None:
    """攒批写不能改变落盘字节，也不能丢进度上报。"""
    payload = bytes((i * 7 + 13) % 256 for i in range(3 * 1024 * 1024))  # 3 MB > flush 阈值
    srv = http_server(payload, chunk=65536)
    task: dict = {"progress": 0.0, "message": ""}

    path = await module._download_package_once(_url(srv), task)

    try:
        data = path.read_bytes()
        assert len(data) == len(payload), f"落盘 {len(data)} 字节，应为 {len(payload)}"
        assert hashlib.sha256(data).hexdigest() == hashlib.sha256(payload).hexdigest(), (
            "攒批写改变了文件内容"
        )
        assert task["downloaded_bytes"] == len(payload)
        assert task["total_bytes"] == len(payload)
        # 0.1 + 1.0*0.6 = 0.7 是下满时的进度
        assert task["progress"] == pytest.approx(0.7, abs=1e-9)
        assert "正在下载" in task["message"]
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_every_write_happens_off_the_event_loop(
    http_server, isolated_download_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """变异：把 ``await _flush_pending()`` 换回 ``handle.write(chunk)``。

    钉的是"写盘在别的线程上"，不是"下载能成功"——后者换回同步写也照样过。
    """
    payload = bytes(range(256)) * (12 * 1024)  # 3 MB
    srv = http_server(payload, chunk=65536)

    loop_thread = threading.current_thread().name
    write_threads: list[str] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(fn, *args, **kwargs):
        def _wrapped(*a, **kw):
            write_threads.append(threading.current_thread().name)
            return fn(*a, **kw)

        return await real_to_thread(_wrapped, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy_to_thread)

    path = await module._download_package_once(_url(srv), {"progress": 0.0})
    try:
        assert write_threads, "一次 to_thread 都没走——写盘又落回事件循环了"
        assert loop_thread not in write_threads, (
            f"有 {write_threads.count(loop_thread)} 次写发生在事件循环线程 {loop_thread} 上"
        )
        # 3MB / 1MB flush = 3 次；攒批的意义就是次数远小于 3200
        assert len(write_threads) <= 8, (
            f"线程往返 {len(write_threads)} 次，攒批没生效（每块都跨一次线程）"
        )
        assert path.read_bytes() == payload
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_a_drip_feed_download_hits_the_total_budget(
    http_server, isolated_download_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """变异：去掉 ``async with asyncio.timeout(_DOWNLOAD_TOTAL_TIMEOUT)``。

    每片 50ms 返回 1 字节：httpx 的每阶段读超时（120s）永远不触发，只有总时长兜底
    能停下来。载荷故意只有 64 字节 —— 去掉兜底后它会在 3.2s 内**正常下完**（于是
    ``pytest.raises`` 失败），而不是把测试挂住几百秒。
    """
    srv = http_server(b"x" * 64, chunk=1, delay=0.05)
    monkeypatch.setattr(module, "_DOWNLOAD_TOTAL_TIMEOUT", 0.6)

    with pytest.raises(module._DownloadAttemptError) as excinfo:
        await module._download_package_once(_url(srv), {"progress": 0.0})

    assert "超时" in str(excinfo.value), f"用户看到的不是超时：{excinfo.value}"
    downloads = list((isolated_download_root / ".downloads").glob("*")) if (
        isolated_download_root / ".downloads"
    ).is_dir() else []
    assert downloads == [], f"超时后临时文件没清掉：{downloads}"


@pytest.mark.asyncio
async def test_total_timeout_is_not_confused_with_the_httpx_one(
    http_server, isolated_download_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个超时必须是两条独立的路：内建 TimeoutError 不是 httpx.TimeoutException。

    变异：删掉 ``except TimeoutError`` 分支 —— 那它会落到最后的 ``except Exception``
    裸抛，既拿不到 GitHub 直连回退，也不是 ``_DownloadAttemptError``。
    """
    assert not issubclass(module.httpx.TimeoutException, TimeoutError), (
        "前提变了：httpx 的超时类成了内建 TimeoutError 的子类，两条 except 会互相遮蔽"
    )
    assert asyncio.TimeoutError is TimeoutError, "asyncio.timeout 抛的不再是内建 TimeoutError"

    srv = http_server(b"y" * 64, chunk=1, delay=0.05)
    monkeypatch.setattr(module, "_DOWNLOAD_TOTAL_TIMEOUT", 0.5)

    raised: BaseException | None = None
    try:
        await module._download_package_once(_url(srv), {"progress": 0.0})
    except BaseException as exc:  # noqa: BLE001 - 要看清究竟是哪个类型
        raised = exc
    assert isinstance(raised, module._DownloadAttemptError), (
        f"总时长超时没有被转成 _DownloadAttemptError，实际是 {type(raised).__name__}: {raised}"
    )


@pytest.mark.asyncio
async def test_the_size_cap_is_still_enforced(
    http_server, isolated_download_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """攒批写不能把大小上限的检查推后到超过上限之后。"""
    payload = b"z" * 4096
    srv = http_server(payload, chunk=512)
    monkeypatch.setattr(module, "_DOWNLOAD_MAX_BYTES", 1024)

    with pytest.raises(module._DownloadAttemptError) as excinfo:
        await module._download_package_once(_url(srv), {"progress": 0.0})
    assert "过大" in str(excinfo.value) or "限制" in str(excinfo.value)
