# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-dir spec for N.E.K.O. Testbench standalone desktop."""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
DIST_ROOT = SPEC_DIR.parent
PROJECT_ROOT = DIST_ROOT.parents[1]

block_cipher = None

datas = [
    (str(PROJECT_ROOT / "tests" / "testbench" / "static"), "testbench/static"),
    (str(PROJECT_ROOT / "tests" / "testbench" / "templates"), "testbench/templates"),
    (str(PROJECT_ROOT / "tests" / "testbench" / "presets"), "testbench/presets"),
    (str(PROJECT_ROOT / "tests" / "testbench" / "scoring_schemas"), "testbench/scoring_schemas"),
    (str(PROJECT_ROOT / "tests" / "testbench" / "dialog_templates"), "testbench/dialog_templates"),
    (str(PROJECT_ROOT / "tests" / "testbench" / "docs"), "testbench/docs"),
    (str(PROJECT_ROOT / "config"), "config"),
]

# Optional embedding / tiktoken assets (prepared by scripts/prepare_embedding.py).
_emb = PROJECT_ROOT / "data" / "embedding_models"
if _emb.is_dir():
    datas.append((str(_emb), "data/embedding_models"))
_tik = PROJECT_ROOT / "data" / "tiktoken_cache"
if _tik.is_dir():
    datas.append((str(_tik), "data/tiktoken_cache"))

hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "jinja2",
    "multipart",
    "webview",
    "tests.testbench",
    "tests.testbench.server",
    "tests.testbench_dist.src.bootstrap",
    "tests.testbench_dist.src.frozen_runtime",
]
hiddenimports += collect_submodules("tests.testbench")
hiddenimports += collect_submodules("config")
hiddenimports += collect_submodules("memory")
# Keep utils broad but exclude known heavy optional trees via Analysis.excludes.
hiddenimports += collect_submodules("utils")
hiddenimports += collect_submodules("main_logic.topic")
hiddenimports += ["main_logic.core"]

try:
    datas += collect_data_files("tiktoken")
except Exception:
    pass
try:
    datas += collect_data_files("tokenizers")
except Exception:
    pass

a = Analysis(
    [str(DIST_ROOT / "src" / "desktop_main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "playwright",
        "browser_use",
        "torch",
        "tensorflow",
        "brain",
        "app.agent_server",
        "app.memory_server",
        "app.main_server",
        "main_routers",
        "frontend",
        "galgame_plugin",
        "dxcam",
        "pyautogui",
        "pytest",
        "hypothesis",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Testbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Testbench",
)
