# -*- mode: python ; coding: utf-8 -*-
"""
Monitor 独立打包配置文件
只包含必要的依赖，去除后端功能
"""

import os
import sys
import platform
from PyInstaller.utils.hooks import collect_data_files

# 获取 spec 文件所在目录和项目根目录
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
PROJECT_ROOT = os.path.dirname(SPEC_DIR)

# 切换到项目根目录
original_dir = os.getcwd()
os.chdir(PROJECT_ROOT)

print(f"[Build] SPEC_DIR: {SPEC_DIR}")
print(f"[Build] PROJECT_ROOT: {PROJECT_ROOT}")
print(f"[Build] Working from: {os.getcwd()}")

block_cipher = None

# 添加必要的二进制文件（DLLs）
binaries = []
if sys.platform == 'win32':
    # 从 Python 安装目录添加必要的 DLLs
    python_dir = os.path.dirname(sys.executable)
    dll_dir = os.path.join(python_dir, 'DLLs')
    library_bin = os.path.join(python_dir, 'Library', 'bin')
    
    # 添加 XML 解析相关的 DLLs
    xml_dlls = ['pyexpat.pyd', '_elementtree.pyd']
    for dll_name in xml_dlls:
        dll_path = os.path.join(dll_dir, dll_name)
        if os.path.exists(dll_path):
            binaries.append((dll_path, '.'))
    
    # 添加 Library/bin 中可能需要的 DLLs（expat 相关）
    if os.path.exists(library_bin):
        for dll_name in ['libexpat.dll']:
            dll_path = os.path.join(library_bin, dll_name)
            if os.path.exists(dll_path):
                binaries.append((dll_path, '.'))

# 收集必要的数据文件
datas = []

# 添加 templates 目录（只包含 monitor 需要的模板）
monitor_templates = [
    ('templates/viewer.html', 'templates'),
    ('templates/subtitle.html', 'templates'),
]

# 检查可选模板文件（如果存在则添加）
optional_templates = ['templates/streamer.html']
for template in optional_templates:
    if os.path.exists(template):
        monitor_templates.append((template, 'templates'))

datas.extend(monitor_templates)

# 添加 static 目录（Live2D 模型和静态资源）
datas += [
    ('static', 'static'),
]

# 添加 config 目录的必要文件
datas += [
    ('config/characters.json', 'config'),
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'monitor.py')],  # 使用绝对路径
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'uvicorn',
        'fastapi',
        'websockets',
        'jinja2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的后端模块
        'brain',
        'memory',
        'main_logic',
        'agent_server',
        'memory_server',
        'main_server',
        'main_routers',
        'plugin',
        # 排除大型科学计算库（如果不需要）
        'numpy',
        'scipy',
        'pandas',
        'matplotlib',
        'torch',
        'tensorflow',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Monitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=True if sys.platform == 'darwin' else False,  # macOS 需要开启
    target_arch=platform.machine() if sys.platform == 'darwin' else None,  # 自动检测 macOS 架构 (arm64/x86_64)
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if sys.platform == 'win32' else None,  # macOS 暂不使用图标
)

