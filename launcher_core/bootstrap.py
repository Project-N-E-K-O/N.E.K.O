# -*- coding: utf-8 -*-
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

"""
N.E.K.O. unified launcher
Starts all servers, waits until they are ready, then starts the main program and monitors its state
"""
from __future__ import annotations

import sys
import os
import io  # noqa: F401  (re-exported by the root launcher facade)
import signal  # noqa: F401  (re-exported by the root launcher facade)

# Preserve the historical root launcher.py path after moving this code.
_IMPLEMENTATION_FILE = __file__
__file__ = os.path.abspath(os.path.join(os.path.dirname(_IMPLEMENTATION_FILE), '..', 'launcher.py'))

def _configure_stdio_utf8() -> None:
    """Normalize stdio encoding when running the launcher on Windows.

    Prefer stream.reconfigure (keeps the stream object); fall back to swapping in a
    TextIOWrapper on failure. Keeping the original object preserves compatibility
    with pytest capture / IDE consoles / other embedded hosts — replacing sys.stdout
    would break those upstream redirectors.
    """
    if sys.platform != 'win32':
        return

    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            reconfigure = getattr(stream, 'reconfigure', None)
            if callable(reconfigure):
                reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


# 模块级立即 reconfigure 一次：即使 launcher 被作为 module import（比如
# tests/unit/test_cloudsave_startup_flow.py 里 8 处 import launcher），也
# 能保证 Windows 下中文 log 不崩。stream.reconfigure 幂等，
# _bootstrap_launcher_runtime 里再调一次只是 no-op。
_configure_stdio_utf8()


# 检测打包环境（PyInstaller 设 sys.frozen，Nuitka 设 __compiled__）
IS_FROZEN = getattr(sys, 'frozen', False) or '__compiled__' in globals()


def _ensure_utf8_filesystem_encoding() -> None:
    """Restart once with PYTHONUTF8=1 when Linux fs encoding is not UTF-8.

    Embedded Python builds in AppImage or minimal Linux runtimes can fall back
    to ``ascii`` under a C/POSIX locale when C.UTF-8 is unavailable and PEP 538
    locale coercion fails. Any non-ASCII path then raises UnicodeEncodeError
    during calls such as os.makedirs or open, preventing the backend from
    starting even if the host shell has a UTF-8 LANG that was not propagated.

    Filesystem encoding is fixed at interpreter startup, so the only reliable
    repair is setting PYTHONUTF8=1 (PEP 540 UTF-8 Mode) and execv-restarting
    the current process. execv preserves the PID for Electron process tracking,
    and the environment is inherited by later server subprocesses. Windows
    already defaults to a UTF-8 filesystem encoding, so it is skipped.
    """
    if sys.platform == 'win32':
        return
    enc = (sys.getfilesystemencoding() or '').lower().replace('-', '')
    if enc == 'utf8':
        return
    # 已经重启过一次：即便仍非 utf-8（例如 PYTHONUTF8 未被尊重）也放行，
    # 绝不二次 execv，避免无限重启。
    if os.environ.get('_NEKO_FS_UTF8_REEXEC') == '1':
        return
    os.environ['PYTHONUTF8'] = '1'
    os.environ['_NEKO_FS_UTF8_REEXEC'] = '1'
    if IS_FROZEN:
        argv = [sys.executable, *sys.argv[1:]]
    else:
        argv = [sys.executable, os.path.abspath(__file__), *sys.argv[1:]]
    try:
        sys.stderr.write(
            f'[launcher] filesystem encoding is {enc!r}; '
            're-exec with PYTHONUTF8=1 to support non-ASCII paths\n'
        )
    except Exception:
        # stderr may be closed or unwritable in embedded launchers; losing this
        # diagnostic is harmless, and the re-exec attempt below is still useful.
        pass
    try:
        os.execv(argv[0], argv)
    except Exception:
        # execv 失败（极少见）就放行：本进程仍是 ascii，但 PYTHONUTF8 已留在
        # os.environ 里，后续 Popen 的子进程仍能拿到 utf-8。好过完全起不来。
        pass


# 仅在作为入口运行时才可能 re-exec：被 tests 当模块 import 时（__name__ !=
# '__main__'）跳过，否则 ascii-fs 环境下的一次 import 会用 execv 把 pytest
# 进程顶替掉。__name__ 在模块体执行前即确定，放这里能赶在任何中文路径操作之前。
if __name__ == '__main__':
    _ensure_utf8_filesystem_encoding()


# 处理 PyInstaller 和 Nuitka 打包后的路径
if IS_FROZEN:
    # 运行在打包后的环境
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller
        bundle_dir = sys._MEIPASS
    else:
        # Nuitka 或其他
        bundle_dir = os.path.dirname(os.path.abspath(__file__))
    # tiktoken encodings (e.g. o200k_base) load merge tables from TIKTOKEN_CACHE_DIR;
    # build_nuitka.bat pre-fetches into data/tiktoken_cache for offline use.
    _tiktoken_cache = os.path.join(bundle_dir, "data", "tiktoken_cache")
    if os.path.isdir(_tiktoken_cache):
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", _tiktoken_cache)
else:
    # 运行在正常 Python 环境
    bundle_dir = os.path.dirname(os.path.abspath(__file__))


def _configure_ssl_cert_bundle() -> None:
    """Explicitly feed certifi's CA bundle to OpenSSL, only in frozen distributions.

    Nuitka / PyInstaller copy `libssl`, but its compile-time hard-coded OPENSSLDIR
    points at a build-machine path that doesn't exist on user machines; if the
    SSL_CERT_FILE env var isn't set either, `ssl.create_default_context()` gets no
    root certificates and all external TLS fails. build-desktop.yml already packs
    `certifi/cacert.pem` as package data; this merely points OpenSSL at it
    explicitly.

    In source mode we do **not** touch SSL_CERT_FILE: the system Python's OpenSSL
    default trust chain is whatever the OS / venv uses, possibly carrying private
    enterprise CAs (corporate TLS MITM proxies, internal PKI, etc.). The static
    certifi bundle lacks those roots, and a hard override would suddenly break
    previously working intranet HTTPS with `certificate verify failed`. Frozen
    builds don't carry that risk (libssl's OPENSSLDIR points at nothing anyway),
    so the fallback only runs in the IS_FROZEN branch.

    If the user already set either variable explicitly and the file exists, the
    original value is respected whether frozen or not; we only override variables
    that are missing or point at no-longer-existing paths (e.g. stale paths
    inherited from the packaging build machine).
    """
    var_names = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")

    def _existing_is_valid(name: str) -> bool:
        value = os.environ.get(name)
        if not value:
            return False
        # 各变量对"有效"的定义不同：
        # - REQUESTS_CA_BUNDLE: requests 文档明确允许 PEM 文件 *或* c_rehash
        #   过的 CA 目录（capath 模式），把目录当失效值覆盖会破坏企业 PKI 的
        #   capath 配置。
        # - SSL_CERT_FILE / CURL_CA_BUNDLE: OpenSSL / curl 都只接受 PEM 文件，
        #   目录由各自的 SSL_CERT_DIR / CURL_CA_PATH 单独表达。
        if name == "REQUESTS_CA_BUNDLE":
            return os.path.isfile(value) or os.path.isdir(value)
        return os.path.isfile(value)

    # 三个变量都已经指向有效文件 → 完全不动。
    if all(_existing_is_valid(name) for name in var_names):
        return

    # 源码模式：保持系统默认信任链，不强行换 certifi（避免破坏企业 CA 场景）。
    # 即便某个变量目前指向失效路径，源码模式也由用户/上游脚本负责修——我们
    # 没法区分"用户故意指向坏路径调试"和"误继承坏路径"。
    if not IS_FROZEN:
        return

    ca_path: str | None = None
    try:
        import certifi  # noqa: WPS433 — 故意放在函数内，保持模块导入开销可控
        candidate = certifi.where()
        if candidate and os.path.isfile(candidate):
            ca_path = candidate
    except Exception:
        ca_path = None

    if ca_path is None:
        # 冻结环境兜底：build-desktop.yml 把 certifi/cacert.pem 落到 bundle_dir 下；
        # PyInstaller onefile 模式下 bundle_dir == sys._MEIPASS（见文件顶部
        # IS_FROZEN 分支），所以这一份候选覆盖了主流冻结布局。
        candidate = os.path.join(bundle_dir, "certifi", "cacert.pem")
        if os.path.isfile(candidate):
            ca_path = candidate

    if ca_path is None:
        # 冻结环境下找不到任何 CA bundle —— 外网 TLS 注定挂，给运维一个明确的
        # 根因提示，避免下游只看到二手的 "certificate verify failed"。
        print(
            "[Launcher] Warning: failed to locate CA bundle in frozen build "
            f"(certifi.where() unavailable, no certifi/cacert.pem under {bundle_dir}); "
            "external HTTPS / WSS will fail with certificate verify failed.",
            flush=True,
        )
        return

    # 每个失效变量按"自身语义最贴近的 fallback 顺序"挑来源，保持各库自己
    # 的查找语义不变；都没拿到再用 certifi 兜底。
    #
    # 关键场景：用户故意分流 SSL_CERT_FILE=/etc/openssl.pem 给 OpenSSL、
    # CURL_CA_BUNDLE=/etc/curl.pem 给 curl/requests，没设 REQUESTS_CA_BUNDLE
    # 想让 requests 走文档里的 fallback (REQUESTS → CURL → default)。如果
    # 我们对所有失效变量都 break 在第一个找到的有效文件（顺序为 SSL → REQUESTS
    # → CURL），REQUESTS_CA_BUNDLE 会被错填成 SSL 的 PEM，requests 看不到
    # 用户预期的 CURL_CA_BUNDLE，HTTPS 行为偏离文档。
    #
    # 偏好顺序设计依据：
    # - SSL_CERT_FILE: OpenSSL 没 documented fallback，但 REQUESTS / CURL 的
    #   PEM 都是 OpenSSL 兼容文件，任选其一无大差异；REQUESTS 排前因为更可能
    #   是用户业务侧的 trust bundle，CURL 排后留给系统级 curl 配置。
    # - REQUESTS_CA_BUNDLE: requests 文档明确 fallback 到 CURL_CA_BUNDLE，
    #   所以 CURL 必须排第一；SSL 作为最后兜底（仍是有效 PEM）。
    # - CURL_CA_BUNDLE: curl 没 documented fallback，按"系统全局信任 → 业务
    #   信任"的直觉：SSL 排前，REQUESTS 兜底。
    #
    # 只看 file：REQUESTS_CA_BUNDLE 允许的目录（capath）不能喂给 OpenSSL /
    # curl，跨变量传播一律走文件。
    propagation_sources = {
        "SSL_CERT_FILE": ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"),
        "REQUESTS_CA_BUNDLE": ("CURL_CA_BUNDLE", "SSL_CERT_FILE"),
        "CURL_CA_BUNDLE": ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"),
    }

    def _pick_fallback(target: str) -> str:
        for src in propagation_sources[target]:
            value = os.environ.get(src)
            if value and os.path.isfile(value):
                return value
        return ca_path

    # 三个变量统一处理：已存在且有效 → 保留；否则 → 按 propagation_sources
    # 顺序找一个有效 PEM 文件填，找不到才用 certifi。`setdefault` 不够：
    # 继承自打包构建机 / 旧路径的失效值会让 requests / curl 仍然报 verify
    # failed，本函数要避免的恰恰就是这个症状。
    for name in var_names:
        if not _existing_is_valid(name):
            os.environ[name] = _pick_fallback(name)


# 必须在任何会触发 `import ssl` 的模块之前执行；Python 的 ssl 模块在第一次
# import 时就会通过 OpenSSL 把默认 verify paths 锁住，之后再设环境变量
# 对已有 SSLContext 不生效。下面 from utils.* import ... 已经会拉起 httpx /
# openai SDK 链路，所以这里抢在前面跑。
#
# 用显式判断而非 `assert`：`python -O` 会剥离 assert，把检查变成静默通过。
# 这里希望任何在本函数之前 import ssl 的回归都能被运维直接看到。
if "ssl" in sys.modules:
    print(
        "[Launcher] Warning: `ssl` was imported before _configure_ssl_cert_bundle() ran; "
        "SSL_CERT_FILE override won't affect the already-initialized default SSLContext. "
        "Move SSL bootstrap higher in launcher.py.",
        flush=True,
    )
_configure_ssl_cert_bundle()


def _get_project_venv_python(project_dir: str) -> str | None:
    if sys.platform == 'win32':
        candidate = os.path.join(project_dir, '.venv', 'Scripts', 'python.exe')
    else:
        candidate = os.path.join(project_dir, '.venv', 'bin', 'python')

    return candidate if os.path.exists(candidate) else None


def _maybe_reexec_into_project_venv(project_dir: str) -> None:
    """Prefer the repo-local virtualenv when launching from source.

    Users often invoke ``python launcher.py`` with the system interpreter.
    When that interpreter differs from the project's managed ``.venv``,
    imports fail even though the dependency is already installed locally.
    """
    if IS_FROZEN:
        return

    # 获取预期的 .venv 目录和当前环境的根目录
    expected_venv_dir = os.path.abspath(os.path.join(project_dir, ".venv"))
    current_venv_dir = os.path.abspath(sys.prefix)

    # 校验当前环境是否真的是本项目的 .venv（忽略大小写差异）
    # 这样既能兼容 uv run，又能防止在其他无关虚拟环境中误跑此脚本导致报错
    if os.path.normcase(current_venv_dir) == os.path.normcase(expected_venv_dir):
        return

    # 如果根目录不匹配，再进行原有的解释器路径严格校验
    current_executable = os.path.abspath(sys.executable or "")
    if not current_executable:
        return

    candidate = _get_project_venv_python(project_dir)
    if not candidate:
        return

    target_executable = os.path.abspath(candidate)
    if current_executable == target_executable:
        return

    print(f"[Launcher] 当前解释器不是项目虚拟环境，正在切换到: {candidate}")
    os.execv(target_executable, [target_executable] + sys.argv)
