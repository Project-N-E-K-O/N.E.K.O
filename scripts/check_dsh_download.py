#!/usr/bin/env python3
"""Fetch the dsh release file from every download host and check it, installing nothing.

What the "download" button of the runtime page does goes through
``nekopower.dsh.install``: the file of ``DSH_VERSION`` for this platform is
fetched from the hosts in ``DSH_DOWNLOAD_HOSTS``, in order, and its size and
SHA-256 are compared with what is recorded. This script does the fetching and
the comparing for each host on its own, so that one run on a network tells
which of them deliver the file there and how fast. The file goes to the
system's temporary directory and is removed; nothing is unpacked, installed
or written below the application's data.

It reads the version, the files and the hosts out of
``config/nekopower_settings.py`` as text, without importing the application
(importing it writes to the user's data), and uses the standard library only:

    python scripts/check_dsh_download.py
    python scripts/check_dsh_download.py --platform linux-x64 --hosts 2
    python scripts/check_dsh_download.py --use-system-proxy

By default no proxy is used, whatever the environment and the system
settings say: the question is whether a user without one gets the file. A
proxy that works below that (a TUN device, a router) still applies; switch
it off for the run. Exits 0 when at least one host delivered the right file.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import http.client
import os
import platform
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SETTINGS = Path(__file__).resolve().parents[1] / "config" / "nekopower_settings.py"
WANTED = ("DSH_VERSION", "DSH_WHEELS", "DSH_DOWNLOAD_HOSTS", "DSH_DOWNLOAD_CONNECT_SECONDS", "DSH_DOWNLOAD_READ_SECONDS")
# The registries dsh installs its plugins from: only asked whether they answer.
NPM_PINGS = ("https://registry.npmjs.org/-/ping", "https://registry.npmmirror.com/-/ping")
CHUNK = 1 << 16


def read_settings() -> dict[str, Any]:
    """Return the dsh download settings, read from the settings file as literals."""
    tree = ast.parse(SETTINGS.read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
        if isinstance(target, ast.Name) and target.id in WANTED:
            found[target.id] = ast.literal_eval(node.value)
    missing = [name for name in WANTED if name not in found]
    if missing:
        raise SystemExit(f"{SETTINGS} 里找不到 {', '.join(missing)}")
    return found


def platform_tag() -> str | None:
    """Return dsh's tag for this computer, as ``nekopower.dsh.install.platform_tag`` names them."""
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("amd64", "x86_64", "x64") else None
    system = {"Windows": "win", "Darwin": "macos", "Linux": "linux"}.get(platform.system())
    if arch is None or system is None:
        return None
    return f"{system}-{arch}"


def opener(use_system_proxy: bool) -> urllib.request.OpenerDirector:
    """Return an opener that goes out directly unless the system's proxy is asked for."""
    if use_system_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(director: urllib.request.OpenerDirector, url: str, wheel: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Fetch one file to a temporary file, remove it, and return what came of it."""
    size = int(wheel["size"])
    digest = hashlib.sha256()
    fetched = 0
    started = time.monotonic()
    first_byte = None
    handle, name = tempfile.mkstemp(prefix="dsh-download-check-", suffix=".whl")
    try:
        with os.fdopen(handle, "wb") as target:
            request = urllib.request.Request(url, headers={"User-Agent": "dsh-download-check"})
            with director.open(request, timeout=timeout) as answer:
                status = getattr(answer, "status", 200)
                if status != 200:
                    return {"ok": False, "reason": f"HTTP {status}"}
                while True:
                    chunk = answer.read(CHUNK)
                    if not chunk:
                        break
                    if first_byte is None:
                        first_byte = time.monotonic() - started
                    fetched += len(chunk)
                    if fetched > size:
                        break
                    digest.update(chunk)
                    target.write(chunk)
                    if sys.stderr.isatty():
                        sys.stderr.write(f"\r    {fetched / 1e6:6.1f} / {size / 1e6:.1f} MB")
                        sys.stderr.flush()
    except urllib.error.HTTPError as exc:
        return {"ok": False, "reason": f"HTTP {exc.code}"}
    except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as exc:
        # ``HTTPException``: an answer cut off in the middle (``IncompleteRead``).
        reason = getattr(exc, "reason", exc)
        return {"ok": False, "reason": f"{type(reason).__name__}: {reason}", "fetched": fetched}
    finally:
        if sys.stderr.isatty():
            sys.stderr.write("\r" + " " * 40 + "\r")
        try:
            os.unlink(name)
        except OSError:
            pass
    seconds = time.monotonic() - started
    result = {"fetched": fetched, "seconds": seconds, "first_byte": first_byte}
    if fetched != size:
        return {**result, "ok": False, "reason": f"大小不对：收到 {fetched} 字节，应为 {size}"}
    if digest.hexdigest() != wheel["sha256"]:
        return {**result, "ok": False, "reason": "SHA-256 不对：不是记录的那个文件"}
    return {**result, "ok": True}


def ping(director: urllib.request.OpenerDirector, url: str, timeout: float) -> str:
    """Return a line saying whether ``url`` answered, and how fast."""
    started = time.monotonic()
    try:
        with director.open(urllib.request.Request(url, headers={"User-Agent": "dsh-download-check"}), timeout=timeout) as answer:
            answer.read(1024)
            return f"通，{(time.monotonic() - started) * 1000:.0f} 毫秒"
    except urllib.error.HTTPError as exc:
        return f"有回应但 HTTP {exc.code}"
    except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return f"不通（{type(reason).__name__}: {reason}）"


def main() -> int:
    settings = read_settings()
    wheels: dict[str, dict[str, Any]] = settings["DSH_WHEELS"]
    hosts: tuple[str, ...] = tuple(settings["DSH_DOWNLOAD_HOSTS"])
    parser = argparse.ArgumentParser(description="逐个下载地址实测 dsh 的程序文件能不能下、对不对，不安装。")
    parser.add_argument("--platform", choices=sorted(wheels), default=None, help="测哪个平台的文件，默认是这台电脑的")
    parser.add_argument("--hosts", default=None, help="只测第几个地址，逗号分隔，从 1 起；默认全测")
    parser.add_argument("--use-system-proxy", action="store_true", help="走系统和环境变量里的代理（默认不走）")
    parser.add_argument("--skip-npm", action="store_true", help="不探 dsh 插件用的 npm 源")
    options = parser.parse_args()

    tag = options.platform or platform_tag()
    if tag is None or tag not in wheels:
        print(f"这台电脑（{platform.system()} {platform.machine()}）没有对应的 dsh 文件；用 --platform 指定一个：{', '.join(sorted(wheels))}")
        return 2
    wheel = wheels[tag]
    picked = range(1, len(hosts) + 1)
    if options.hosts:
        picked = [int(part) for part in options.hosts.split(",") if part.strip()]
    director = opener(options.use_system_proxy)
    timeout = float(settings["DSH_DOWNLOAD_READ_SECONDS"])

    proxies = sorted(name for name in os.environ if name.lower() in ("http_proxy", "https_proxy", "all_proxy"))
    print(f"dsh {settings['DSH_VERSION']}，平台 {tag}，文件 {wheel['file']}（{int(wheel['size']) / 1e6:.1f} MB）")
    print(f"代理：{'走系统代理' if options.use_system_proxy else '不走代理（直连）'}"
          + (f"；环境里设了 {', '.join(proxies)}，{'会用到' if options.use_system_proxy else '这次不用'}" if proxies else ""))
    print("只下载和核对，不安装；文件下到系统临时目录，核对完就删。\n")

    good = 0
    tried = 0
    lines: list[str] = []
    for number in picked:
        if not 1 <= number <= len(hosts):
            print(f"没有第 {number} 个地址（一共 {len(hosts)} 个）")
            continue
        host = hosts[number - 1]
        tried += 1
        url = f"{host.rstrip('/')}/{wheel['path']}/{wheel['file']}"
        print(f"[{number}/{len(hosts)}] {host}")
        result = fetch(director, url, wheel, timeout)
        if result["ok"]:
            good += 1
            speed = result["fetched"] / 1e6 / max(result["seconds"], 1e-6)
            line = f"成功：{result['seconds']:.1f} 秒，{speed:.2f} MB/秒，首字节 {result['first_byte']:.2f} 秒，大小和 SHA-256 都对"
        else:
            got = result.get("fetched")
            line = f"失败：{result['reason']}" + (f"（已收到 {got / 1e6:.1f} MB）" if got else "")
        print(f"    {line}\n")
        lines.append(f"{number}. {host}：{line}")

    if not options.skip_npm:
        print("dsh 插件用的 npm 源（只看有没有回应，不下载）：")
        for url in NPM_PINGS:
            line = ping(director, url, float(settings["DSH_DOWNLOAD_CONNECT_SECONDS"]))
            print(f"    {url}：{line}")
            lines.append(f"npm {url}：{line}")
        print()

    tried_all = set(range(1, len(hosts) + 1)) <= set(picked)
    print("=== 结果（整段贴回去即可）===")
    print(f"dsh {settings['DSH_VERSION']} / {tag} / {'系统代理' if options.use_system_proxy else '直连'} / {time.strftime('%Y-%m-%d %H:%M')}")
    for line in lines:
        print(line)
    # Only of the addresses that were tried: the application goes through all of them.
    print(f"结论：试了 {tried} 个地址，{good} 个能下到正确的文件。" + (
        "" if good else (
            # The application goes by the environment's proxy; this run may not have.
            f"这次{'走系统代理' if options.use_system_proxy else '直连'}试的全部地址都没下成；应用按系统代理下载，"
            f"{'它的下载按钮在这个网络上多半也会失败（它读代理设置的办法和这个脚本不完全一样，不能说一定）。' if options.use_system_proxy else '它走代理时可能仍然下得成，加 --use-system-proxy 再试一次。'}"
            if tried_all else "这次选的地址都没下成；没试的地址不在结论里。"
        )
    ))
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
