#!/usr/bin/env python3
"""
构建时预下载 browser-use 扩展，避免用户首次启动时的网络延迟。

运行方式:
    python scripts/prepare_extensions.py

此脚本会在构建前预下载 browser-use 所需的 Chrome 扩展到 data/browser_use_extensions/，
这样打包后的软件首次启动时无需从网络下载扩展。
"""

import os
import sys
import json
import shutil
import zipfile
import urllib.request
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 扩展目标目录
EXTENSIONS_DIR = PROJECT_ROOT / "data" / "browser_use_extensions"

# browser-use 默认扩展列表 (从 browser_use.config 或源代码获取)
# 这些是 browser-use 在首次启动时会尝试下载的扩展
DEFAULT_EXTENSIONS = {
    "browser_debugger": {
        "url": "https://github.com/browser-use/browser-use/raw/main/extension/browser_debugger",
        "fallback_crx": None,  # 如有 CRX 直链可填入
    },
    "recorder": {
        "url": "https://github.com/browser-use/browser-use/raw/main/extension/recorder",
        "fallback_crx": None,
    },
}


def download_file(url: str, dest: Path, timeout: int = 30) -> bool:
    """下载文件到指定路径。"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"[PrepareExtensions] 下载失败 {url}: {e}")
        return False


def extract_crx(crx_path: Path, dest_dir: Path) -> bool:
    """解压 CRX 扩展文件。"""
    try:
        # CRX 文件本质是 ZIP，但头部有额外元数据
        # 简单处理：尝试作为 ZIP 解压
        with zipfile.ZipFile(crx_path, "r") as zf:
            zf.extractall(dest_dir)
        return True
    except Exception as e:
        print(f"[PrepareExtensions] 解压失败 {crx_path}: {e}")
        return False


def prepare_from_browser_use_config():
    """
    尝试从 browser-use 配置中获取扩展信息并预下载。
    这是首选方法，能确保与当前安装的 browser-use 版本兼容。
    """
    try:
        from browser_use.config import CONFIG

        ext_dir = getattr(CONFIG, "BROWSER_USE_EXTENSIONS_DIR", None)
        if not ext_dir:
            print("[PrepareExtensions] CONFIG.BROWSER_USE_EXTENSIONS_DIR 未设置")
            return False

        # 获取扩展下载 URL 配置
        urls = getattr(CONFIG, "EXTENSION_DOWNLOAD_URLS", {})
        if not urls:
            print("[PrepareExtensions] CONFIG.EXTENSION_DOWNLOAD_URLS 为空，尝试备用方法")
            return False

        EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)

        for ext_name, url in urls.items():
            dest = EXTENSIONS_DIR / f"{ext_name}.crx"
            if dest.exists():
                print(f"[PrepareExtensions] {ext_name} 已存在，跳过")
                continue

            print(f"[PrepareExtensions] 下载 {ext_name} from {url}...")
            if download_file(url, dest):
                # 解压 CRX
                ext_extract_dir = EXTENSIONS_DIR / ext_name
                ext_extract_dir.mkdir(exist_ok=True)
                if extract_crx(dest, ext_extract_dir):
                    print(f"[PrepareExtensions] {ext_name} 准备完成")
                    # 删除 CRX 保留解压后的内容
                    dest.unlink()
                else:
                    print(f"[PrepareExtensions] {ext_name} 解压失败，保留 CRX")
            else:
                print(f"[PrepareExtensions] {ext_name} 下载失败")

        return True

    except ImportError:
        print("[PrepareExtensions] browser-use 未安装，无法从 CONFIG 获取")
        return False
    except Exception as e:
        print(f"[PrepareExtensions] 从 CONFIG 获取失败: {e}")
        return False


def prepare_from_source_clone():
    """
    备用方法：从 browser-use GitHub 仓库克隆扩展源代码。
    适用于开发环境或无法直接下载 CRX 的情况。
    """
    import tempfile
    import subprocess

    print("[PrepareExtensions] 尝试从 GitHub 克隆扩展源码...")

    EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "browser-use"

        # 浅克隆扩展目录
        cmd = [
            "git",
            "clone",
            "--depth", "1",
            "--filter", "blob:none",
            "--sparse",
            "https://github.com/browser-use/browser-use.git",
            str(repo_dir),
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            # 检出扩展目录
            sparse_cmd = ["git", "sparse-checkout", "set", "extension"]
            subprocess.run(
                sparse_cmd, cwd=repo_dir, check=True, capture_output=True, text=True
            )

            src_ext_dir = repo_dir / "extension"
            if not src_ext_dir.exists():
                print("[PrepareExtensions] 仓库中没有 extension 目录")
                return False

            # 复制扩展
            for ext_path in src_ext_dir.iterdir():
                if ext_path.is_dir():
                    dest_path = EXTENSIONS_DIR / ext_path.name
                    if dest_path.exists():
                        shutil.rmtree(dest_path)
                    shutil.copytree(ext_path, dest_path)
                    print(f"[PrepareExtensions] 已复制 {ext_path.name}")

            return True

        except subprocess.CalledProcessError as e:
            print(f"[PrepareExtensions] Git 克隆失败: {e}")
            return False
        except Exception as e:
            print(f"[PrepareExtensions] 克隆过程出错: {e}")
            return False


def create_manifest_stub():
    """
    创建占位扩展清单，用于测试构建流程。
    实际发布构建时应替换为真实扩展。
    """
    EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)

    stub_manifest = {
        "manifest_version": 3,
        "name": "Browser Use Stub Extension",
        "version": "1.0.0",
        "description": "This is a placeholder. Replace with real extensions before release build.",
    }

    stub_dir = EXTENSIONS_DIR / "_stub"
    stub_dir.mkdir(exist_ok=True)

    manifest_file = stub_dir / "manifest.json"
    manifest_file.write_text(json.dumps(stub_manifest, indent=2))

    print(f"[PrepareExtensions] 已创建占位扩展: {stub_dir}")
    print("[PrepareExtensions] ⚠️  注意：发布构建前请替换为真实扩展文件")


def main():
    print("=" * 60)
    print("Browser Use 扩展预下载脚本")
    print("=" * 60)

    # 方法1: 从 browser-use CONFIG 获取
    if prepare_from_browser_use_config():
        print("\n[PrepareExtensions] ✓ 扩展准备完成（从 CONFIG）")
        return 0

    # 方法2: 从 GitHub 克隆
    if prepare_from_source_clone():
        print("\n[PrepareExtensions] ✓ 扩展准备完成（从 GitHub）")
        return 0

    # 方法3: 创建占位（保底）
    print("\n[PrepareExtensions] 无法获取真实扩展，创建占位文件...")
    create_manifest_stub()

    print("\n[PrepareExtensions] ⚠️  占位扩展已创建")
    print("    请在发布构建前手动放置真实扩展到:")
    print(f"    {EXTENSIONS_DIR}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
