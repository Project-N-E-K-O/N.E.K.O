"""Market downloads must bound resources without blocking the event loop.

The historical implementation wrote every 64 KiB synchronously: a 200 MiB
package required 3200 blocking writes. The downloader now batches writes on
workers, drains them before cleanup, and enforces a total deadline in addition
to HTTPX phase timeouts. The market and import paths share a 500 MiB limit.

Regressions cover byte limits, progress, cancellation-safe file ownership and
total timeouts. Only package file operations produce nonretryable write errors;
network timeouts, connection errors and CA configuration errors retain their
transport categories and the existing direct-source fallback policy.
"""

from __future__ import annotations

from plugin.utils.http_imports import load_httpx

import asyncio
import errno
import hashlib
import http.server
import io
import ssl
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.server.routes import market_bridge as module
from plugin.server.infrastructure import package_download as download_io

pytestmark = pytest.mark.plugin_unit


def test_market_and_import_share_the_500_mib_package_limit():
    from plugin.server.application.plugin_cli.service import _UPLOAD_MAX_BYTES

    assert module._DOWNLOAD_MAX_BYTES == _UPLOAD_MAX_BYTES == 500 * 1024 * 1024


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_temp_file_close_failure_attempts_cleanup_without_replacing_the_error(tmp_path, monkeypatch, cleanup_fails):
    failure = OSError(errno.EIO, "close failed")
    close = download_io.os.close

    def fail_close(fd):
        close(fd)
        raise failure

    def fail_unlink(*args, **kwargs):
        raise PermissionError("cleanup denied")

    with monkeypatch.context() as patch:
        patch.setattr(download_io, "os", SimpleNamespace(close=fail_close))
        if cleanup_fails:
            patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(download_io.PackageDownloadWriteError) as raised:
            download_io._create_download_file(tmp_path)
        assert raised.value.error is failure
        assert raised.value.__cause__ is failure
        files = list(tmp_path.glob("neko-market-*.neko-plugin"))
        assert len(files) == (1 if cleanup_fails else 0)
    for path in files:
        path.unlink()


@pytest.mark.parametrize("size", [264366702, 500 * 1024 * 1024])
async def test_valid_large_content_length_reaches_stream_without_large_fixture(
    isolated_download_root, monkeypatch, size,
):
    httpx = load_httpx()
    client = httpx.AsyncClient

    class BodyReached(Exception):
        pass

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise BodyReached
            yield b""  # pragma: no cover - keep this an async byte iterator

    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"content-length": str(size)}, stream=Stream(),
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    task = {}
    with pytest.raises(BodyReached):
        await module._download_package_once("https://example.test/package", task)
    assert task["total_bytes"] == size
    assert not list((isolated_download_root / ".downloads").glob("*.neko-plugin"))


@pytest.mark.parametrize("declared", [True, False])
async def test_size_refusal_preserves_counts_and_removes_partial_file(
    isolated_download_root, monkeypatch, declared,
):
    httpx = load_httpx()
    client = httpx.AsyncClient
    limit = module._DOWNLOAD_MAX_BYTES if declared else 6
    actual = limit + 1
    response = httpx.Response(200, content=b"package")
    if declared:
        response.headers["content-length"] = str(actual)
    else:
        response.headers.pop("content-length")
    transport = httpx.MockTransport(lambda request: response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    monkeypatch.setattr(module, "_DOWNLOAD_MAX_BYTES", limit)
    task = {}
    with pytest.raises(module._DownloadAttemptError) as raised:
        await module._download_package_once("https://example.test/package", task)
    assert raised.value.code == "package_size_exceeded"
    assert raised.value.retryable is False
    assert raised.value.details == {
        "actual_bytes": actual, "maximum_bytes": limit,
        "size_source": "content_length" if declared else "received_bytes",
    }
    assert task["downloaded_bytes"] == (0 if declared else 7)
    assert task["total_bytes"] == (actual if declared else None)
    assert not list((isolated_download_root / ".downloads").glob("*.neko-plugin"))


@pytest.mark.parametrize("declared", [True, False])
async def test_received_bytes_equal_to_limit_are_allowed(isolated_download_root, monkeypatch, declared):
    httpx = load_httpx()
    client = httpx.AsyncClient
    response = httpx.Response(200, content=b"package")
    if not declared:
        response.headers.pop("content-length")
    transport = httpx.MockTransport(lambda request: response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    monkeypatch.setattr(module, "_DOWNLOAD_MAX_BYTES", 7)
    path = await module._download_package_once("https://example.test/package", {})
    assert path.read_bytes() == b"package"
    path.unlink()


@pytest.mark.parametrize("failure,code,retryable", [
    ("timeout", "download_timeout", True),
    ("network", "download_network_error", True),
    ("http", "download_http_error", True),
    ("disk", "insufficient_disk_space", False),
    ("permission", "download_write_failed", False),
])
async def test_download_failure_categories(isolated_download_root, monkeypatch, failure, code, retryable):
    httpx = load_httpx()
    request = httpx.Request("GET", "https://example.test/package")
    errors = {
        "timeout": httpx.ReadTimeout("timeout", request=request),
        "network": httpx.ConnectError("network", request=request),
        "http": httpx.HTTPStatusError("http", request=request, response=httpx.Response(503)),
        "disk": download_io.PackageDownloadWriteError(OSError(errno.ENOSPC, "no space")),
        "permission": download_io.PackageDownloadWriteError(PermissionError(errno.EACCES, "denied")),
    }

    async def fail(*_args, **_kwargs):
        raise errors[failure]

    monkeypatch.setattr(module, "download_package_file", fail)
    with pytest.raises(module._DownloadAttemptError) as raised:
        await module._download_package_once(str(request.url), {})
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    task_error = module._download_task_error(raised.value)
    assert task_error.code == code
    assert task_error.retryable is retryable
    if failure == "http":
        assert task_error.details == {"http_status": 503}


@pytest.mark.parametrize("failure,code", [
    (TimeoutError("read timed out"), "download_timeout"),
    (ConnectionResetError("connection reset"), "download_network_error"),
    (ssl.SSLError("TLS error"), "download_network_error"),
])
async def test_stream_os_errors_remain_retryable(isolated_download_root, monkeypatch, failure, code):
    httpx = load_httpx()
    client = httpx.AsyncClient

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise failure
            yield b""  # pragma: no cover

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    with pytest.raises(module._DownloadAttemptError) as raised:
        await module._download_package_once("https://example.test/package", {})
    assert raised.value.code == code
    assert raised.value.retryable is True
    assert list((isolated_download_root / ".downloads").iterdir()) == []


async def test_missing_ca_file_is_not_a_package_write_error(isolated_download_root, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(isolated_download_root / "missing-ca.pem"))
    with pytest.raises(module._DownloadAttemptError) as raised:
        await module._download_package_once("https://example.test/package", {})
    assert isinstance(raised.value.__cause__, FileNotFoundError)
    assert raised.value.code == "download_network_error"
    assert raised.value.retryable is True
    assert list((isolated_download_root / ".downloads").iterdir()) == []


@pytest.mark.parametrize("phase", ["create", "open", "write", "close"])
async def test_only_file_operations_produce_nonretryable_write_errors(
    isolated_download_root, monkeypatch, phase,
):
    httpx = load_httpx()
    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"package"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))

    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "denied")

    class File(io.BytesIO):
        def writelines(self, chunks):
            if phase == "write":
                raise OSError(errno.ENOSPC, "no space")
            super().writelines(chunks)

        def close(self):
            super().close()
            if phase == "close":
                raise PermissionError(errno.EACCES, "close denied")

    if phase == "create":
        monkeypatch.setattr(download_io.tempfile, "mkstemp", denied)
    elif phase == "open":
        monkeypatch.setattr(Path, "open", denied)
    else:
        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: File())
    with pytest.raises(module._DownloadAttemptError) as raised:
        await module._download_package_once("https://example.test/package", {})
    assert raised.value.code == ("insufficient_disk_space" if phase == "write" else "download_write_failed")
    assert raised.value.retryable is False
    assert list((isolated_download_root / ".downloads").iterdir()) == []


async def test_raw_network_error_still_falls_back_to_direct_github(isolated_download_root, monkeypatch):
    httpx = load_httpx()
    client = httpx.AsyncClient
    direct = "https://github.com/example/demo/releases/download/v1/demo.neko-plugin"
    proxy_base = next(base for source, base in module._GITHUB_PROXY_SOURCES if source != "github-direct")
    urls = []

    def respond(request):
        urls.append(str(request.url))
        if len(urls) == 1:
            raise ConnectionResetError("proxy reset")
        return httpx.Response(200, content=b"package")

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    path, source = await module._download_package(proxy_base + direct, {})
    assert urls == [proxy_base + direct, direct]
    assert source == direct
    assert path.read_bytes() == b"package"
    path.unlink()


@pytest.mark.parametrize("failure", [
    asyncio.CancelledError(), download_io.PackageSizeExceeded(9, 8),
])
async def test_file_close_failure_preserves_the_primary_exception(tmp_path, monkeypatch, failure):
    class File(io.BytesIO):
        def close(self):
            was_closed = self.closed
            super().close()
            if not was_closed:
                raise OSError(errno.EIO, "close failed")

    handle = File()
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: handle)
    with pytest.raises(type(failure)) as raised:
        async with download_io._open_download_file(tmp_path / "package"):
            raise failure
    assert raised.value is failure
    assert handle.closed


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


@pytest.mark.parametrize(
    ("length", "expected"),
    [
        (None, None),
        ("", None),
        ("abc", None),
        ("-1", None),
        ("1.5", None),
        ("0", 0),
        ("7", 7),
    ],
)
async def test_download_length_validation_preserves_bytes_and_progress(
    isolated_download_root,
    monkeypatch,
    length,
    expected,
):
    httpx = load_httpx()
    client = httpx.AsyncClient

    def respond(request):
        response = httpx.Response(200, content=b"package")
        if length is None:
            response.headers.pop("content-length", None)
        else:
            response.headers["content-length"] = length
        return response

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs)
    )
    task = {"progress": 0.0}
    path = await module._download_package_once("https://example.test/package", task)
    assert path.read_bytes() == b"package"
    assert task["downloaded_bytes"] == 7
    assert task["total_bytes"] == expected


@pytest.mark.parametrize("length", ["abc", "-1"])
async def test_invalid_download_length_keeps_the_received_byte_limit(
    isolated_download_root,
    monkeypatch,
    length,
):
    httpx = load_httpx()
    client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-length": length}, content=b"package"
        )
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs)
    )
    monkeypatch.setattr(module, "_DOWNLOAD_MAX_BYTES", 2)
    with pytest.raises(module._DownloadAttemptError, match="过大"):
        await module._download_package_once(
            "https://example.test/package", {"progress": 0.0}
        )
    assert not list((isolated_download_root / ".downloads").glob("*.neko-plugin"))


@pytest.mark.asyncio
async def test_downloaded_bytes_are_exact_and_progress_is_reported(
    http_server, isolated_download_root
) -> None:
    """攒批写不能改变落盘字节，也不能丢进度上报。"""
    payload = bytes((i * 7 + 13) % 256 for i in range(3 * module._DOWNLOAD_FLUSH_BYTES))
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
    payload = bytes(range(256)) * (3 * module._DOWNLOAD_FLUSH_BYTES // 256)
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
        # Three writes plus one open and close, rather than one worker per chunk.
        assert len(write_threads) <= 8, (
            f"线程往返 {len(write_threads)} 次，攒批没生效（每块都跨一次线程）"
        )
        assert path.read_bytes() == payload
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_download_preserves_bytes_with_one_file_handle(
    http_server, isolated_download_root, monkeypatch,
):
    payload = b"x" * (3 * module._DOWNLOAD_FLUSH_BYTES + 19)
    srv = http_server(payload)
    handles = []
    open_threads = []
    real_open = Path.open
    loop_thread = threading.get_ident()

    def open_file(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if mode in {"ab", "wb"} and path.suffix == ".neko-plugin":
            handles.append(handle)
            open_threads.append(threading.get_ident())
        return handle

    monkeypatch.setattr(Path, "open", open_file)
    path = await module._download_package_once(_url(srv), {})
    try:
        assert path.read_bytes() == payload
        assert len(handles) == 1, "Every buffered write reopened the package"
        assert handles[0].closed
        assert loop_thread not in open_threads
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_cancelled_file_open_is_closed_before_download_cleanup(
    http_server, isolated_download_root, monkeypatch,
):
    srv = http_server(b"x")
    loop = asyncio.get_running_loop()
    opened = asyncio.Event()
    release = threading.Event()
    handles = []
    real_open = Path.open

    def open_file(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if mode == "wb" and path.suffix == ".neko-plugin":
            handles.append(handle)
            loop.call_soon_threadsafe(opened.set)
            if not release.wait(2):
                handle.close()
                raise TimeoutError("test did not release open worker")
        return handle

    monkeypatch.setattr(Path, "open", open_file)
    fallback = threading.Timer(1.5, release.set)
    fallback.start()
    operation = asyncio.create_task(module._download_package_once(_url(srv), {}))
    try:
        await asyncio.wait_for(opened.wait(), 1)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0.05)
        assert not operation.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 1)
        assert handles and all(handle.closed for handle in handles)
        assert list((isolated_download_root / ".downloads").iterdir()) == []
    finally:
        release.set()
        fallback.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await asyncio.to_thread(fallback.join)


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
    assert not issubclass(load_httpx().TimeoutException, TimeoutError), (
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


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_stage", ["write", "close"])
@pytest.mark.parametrize("cancellation", ["timeout", "caller"])
async def test_slow_file_io_drains_without_blocking_the_loop(
    isolated_download_root, monkeypatch, blocked_stage, cancellation,
):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    loop_thread = threading.get_ident()
    cleanup_threads = []

    def pause():
        loop.call_soon_threadsafe(started.set)
        if not release.wait(2):
            raise TimeoutError("test did not release the file worker")

    class SlowRaw(io.RawIOBase):
        def writable(self):
            return True

        def write(self, data):
            if blocked_stage == "write":
                pause()
            return len(data)

        def close(self):
            if blocked_stage == "close" and not self.closed:
                pause()
            super().close()

    raw = SlowRaw()
    handle = io.BufferedWriter(raw)
    real_open = Path.open

    def open_file(path, mode="r", *args, **kwargs):
        if mode in {"ab", "wb"} and path.suffix == ".neko-plugin":
            return handle
        return real_open(path, mode, *args, **kwargs)

    class Response:
        headers = {"content-length": str(1024 * 1024)}

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def aiter_bytes(self, chunk_size):
            for _ in range(16):
                yield b"x" * chunk_size

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def stream(self, *_args):
            return Response()

    real_cleanup = download_io.cleanup_download_file

    def cleanup(path):
        assert handle.closed, "Cleanup raced the file worker"
        cleanup_threads.append(threading.get_ident())
        real_cleanup(path)

    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(load_httpx(), "AsyncClient", Client)
    monkeypatch.setattr(download_io, "cleanup_download_file", cleanup)
    monkeypatch.setattr(module, "_DOWNLOAD_TOTAL_TIMEOUT", 0.05 if cancellation == "timeout" else 10)
    # A regressed synchronous close must fail within a bounded time, not hang pytest.
    fallback_release = threading.Timer(1.5, release.set)
    fallback_release.start()
    download = asyncio.create_task(module._download_package_once("https://example.invalid/pkg", {}))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if cancellation == "caller":
            download.cancel()
            await asyncio.sleep(0)
            download.cancel()
        tick_started = time.perf_counter()
        await asyncio.sleep(0.1)
        assert time.perf_counter() - tick_started < 0.5, "File cleanup blocked the event loop"
        assert not download.done()
        assert cleanup_threads == []
        release.set()
        expected = module._DownloadAttemptError if cancellation == "timeout" else asyncio.CancelledError
        with pytest.raises(expected):
            await asyncio.wait_for(download, 1)
        assert cleanup_threads and loop_thread not in cleanup_threads
        assert list((isolated_download_root / ".downloads").iterdir()) == []
    finally:
        release.set()
        fallback_release.cancel()
        await asyncio.gather(download, return_exceptions=True)
        await asyncio.to_thread(fallback_release.join)


@pytest.mark.asyncio
async def test_filesystem_timeout_is_not_classified_as_download_deadline(
    http_server, isolated_download_root, monkeypatch
):
    import errno
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def broken_file(path):
        class File:
            def writelines(self, chunks):
                raise OSError(errno.ETIMEDOUT, "filesystem timed out")
        yield File()

    monkeypatch.setattr(download_io, "_open_download_file", broken_file)
    srv = http_server(b"package")
    with pytest.raises(module._DownloadAttemptError) as excinfo:
        await module._download_package_once(_url(srv), {"progress": 0.0})
    assert excinfo.value.code == "download_write_failed"
    assert excinfo.value.retryable is False
    assert isinstance(excinfo.value.__cause__, download_io.PackageDownloadWriteError)
    assert isinstance(excinfo.value.__cause__.error, TimeoutError)
    assert not isinstance(excinfo.value.__cause__.error, download_io.PackageDownloadDeadline)
    assert list((isolated_download_root / ".downloads").iterdir()) == []
