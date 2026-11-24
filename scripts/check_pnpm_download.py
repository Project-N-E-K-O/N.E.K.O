#!/usr/bin/env python3
"""Fetch the pnpm that adds dsh plugins from every registry and check it, installing nothing.

``neko-dsh-plugin`` (``nekopower/dsh/plugin_command.py``) fetches, the first
time a dsh plugin is added, the two files of ``DSH_PNPM_PACKAGES`` for this
platform (pnpm's program text and the executable that runs it) from the first
registry of ``DSH_PNPM_REGISTRIES`` that answers, and compares their sizes
and SHA-256 with what is recorded; then pnpm fetches the plugin from that
registry. This script does the fetching and the comparing for each registry
on its own, and fetches one plugin's metadata and file as pnpm would, so that
one run on a network tells which registries work there and how fast. The
files go to the system's temporary directory and are removed; nothing is
unpacked, installed or run.

It reads the settings out of ``config/nekopower_settings.py`` as text,
without importing the application, and uses the standard library only:

    python scripts/check_pnpm_download.py
    python scripts/check_pnpm_download.py --platform linux-x64 --registries 2
    python scripts/check_pnpm_download.py --use-system-proxy

By default no proxy is used, whatever the environment and the system
settings say: the question is whether a user without one gets the files. A
proxy that works below that (a TUN device, a router) still applies; switch
it off for the run. Exits 0 when at least one registry delivered both files
and the plugin.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_dsh_download as shared  # noqa: E402 - the scripts directory is put on the path above

WANTED = ("DSH_PNPM_VERSION", "DSH_PNPM_PACKAGES", "DSH_PNPM_REGISTRIES", "DSH_DOWNLOAD_CONNECT_SECONDS",
          "DSH_DOWNLOAD_READ_SECONDS")
# A plugin with no dependencies and no install script, which adds tools a task agent sees.
PLUGIN = "dsh-free-search"


def plugin_check(director: urllib.request.OpenerDirector, registry: str, timeout: float) -> tuple[bool, str]:
    """Fetch a plugin's metadata as pnpm asks for it, then its file, checked against the metadata."""
    import base64
    import hashlib

    started = time.monotonic()
    try:
        request = urllib.request.Request(
            f"{registry}/{PLUGIN}",
            headers={"User-Agent": "pnpm-download-check", "Accept": "application/vnd.npm.install-v1+json"},
        )
        with director.open(request, timeout=timeout) as answer:
            metadata = json.loads(answer.read())
        latest = metadata["dist-tags"]["latest"]
        dist = metadata["versions"][latest]["dist"]
        with director.open(urllib.request.Request(dist["tarball"], headers={"User-Agent": "pnpm-download-check"}),
                           timeout=timeout) as answer:
            data = answer.read()
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError, ValueError, KeyError) as exc:
        # ``HTTPException``: an answer cut off in the middle (``IncompleteRead``); the next registry is still tried.
        reason = getattr(exc, "reason", exc)
        return False, f"{type(reason).__name__}: {reason}"
    algorithm, _, expected = str(dist.get("integrity", "")).partition("-")
    if algorithm != "sha512" or base64.b64encode(hashlib.sha512(data).digest()).decode() != expected:
        return False, "插件包的校验值和元数据对不上"
    host = urllib.parse.urlparse(dist["tarball"]).netloc
    return True, f"{PLUGIN}@{latest}，{len(data) / 1e6:.1f} MB，从 {host} 下，校验对，{time.monotonic() - started:.1f} 秒"


def main() -> int:
    settings = read_settings()
    packages: dict[str, dict[str, Any]] = settings["DSH_PNPM_PACKAGES"]
    registries: tuple[str, ...] = tuple(settings["DSH_PNPM_REGISTRIES"])
    platforms = sorted(tag for tag in packages if tag != "main")
    parser = argparse.ArgumentParser(description="逐个 npm 源实测 dsh 插件用的 pnpm 能不能下、对不对，不安装。")
    parser.add_argument("--platform", choices=platforms, default=None, help="测哪个平台的文件，默认是这台电脑的")
    parser.add_argument("--registries", default=None, help="只测第几个源，逗号分隔，从 1 起；默认全测")
    parser.add_argument("--use-system-proxy", action="store_true", help="走系统和环境变量里的代理（默认不走）")
    options = parser.parse_args()

    tag = options.platform or shared.platform_tag()
    if tag not in platforms:
        print(f"这台电脑（{tag or '未知平台'}）没有对应的 pnpm 文件；用 --platform 指定一个：{', '.join(platforms)}")
        return 2
    files = [packages["main"], packages[tag]]
    picked = range(1, len(registries) + 1)
    if options.registries:
        try:
            picked = [int(part) for part in options.registries.split(",") if part.strip()]
        except ValueError:
            parser.error(f"--registries 要写源的序号，用逗号分隔，比如 1,2（一共 {len(registries)} 个源）")
    director = shared.opener(options.use_system_proxy)
    timeout = float(settings["DSH_DOWNLOAD_READ_SECONDS"])

    total = sum(int(item["size"]) for item in files) / 1e6
    print(f"pnpm {settings['DSH_PNPM_VERSION']}，平台 {tag}，两个文件共 {total:.1f} MB")
    print(f"代理：{'走系统代理' if options.use_system_proxy else '不走代理（直连）'}")
    print("只下载和核对，不安装、不运行；文件下到系统临时目录，核对完就删。\n")

    good = 0
    tried = 0
    lines: list[str] = []
    for number in picked:
        if not 1 <= number <= len(registries):
            print(f"没有第 {number} 个源（一共 {len(registries)} 个）")
            continue
        registry = registries[number - 1]
        tried += 1
        print(f"[{number}/{len(registries)}] {registry}")
        line = shared.ping(director, f"{registry}/-/ping", float(settings["DSH_DOWNLOAD_CONNECT_SECONDS"]))
        print(f"    探测：{line}")
        lines.append(f"{number}. {registry} 探测：{line}")
        whole = True
        for item in files:
            result = shared.fetch(director, f"{registry}/{item['name']}/-/{item['file']}", item, timeout)
            if result["ok"]:
                speed = result["fetched"] / 1e6 / max(result["seconds"], 1e-6)
                line = f"成功：{result['seconds']:.1f} 秒，{speed:.2f} MB/秒，大小和 SHA-256 都对"
            else:
                whole = False
                got = result.get("fetched")
                line = f"失败：{result['reason']}" + (f"（已收到 {got / 1e6:.1f} MB）" if got else "")
            print(f"    {item['file']}：{line}")
            lines.append(f"   {item['file']}：{line}")
        ok, line = plugin_check(director, registry, timeout)
        print(f"    插件：{line}\n")
        lines.append(f"   插件：{'成功：' if ok else '失败：'}{line}")
        good += whole and ok

    print("=== 结果（整段贴回去即可）===")
    print(f"pnpm {settings['DSH_PNPM_VERSION']} / {tag} / {'系统代理' if options.use_system_proxy else '直连'} / "
          f"{time.strftime('%Y-%m-%d %H:%M')}")
    for line in lines:
        print(line)
    print(f"结论：试了 {tried} 个源，{good} 个能下到正确的 pnpm 和插件。")
    return 0 if good else 1


def read_settings() -> dict[str, Any]:
    """Return the pnpm download settings, read from the settings file as literals."""
    import ast

    tree = ast.parse(shared.SETTINGS.read_text(encoding="utf-8"))
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
        raise SystemExit(f"{shared.SETTINGS} 里找不到 {', '.join(missing)}")
    return found


if __name__ == "__main__":
    raise SystemExit(main())
