# -*- mode: python ; coding: utf-8 -*-
import sys
import os
import platform
from PyInstaller.utils.hooks import collect_all
from PyInstaller.building.build_main import Tree

# 获取 spec 文件所在目录和项目根目录
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
PROJECT_ROOT = os.path.dirname(SPEC_DIR)

# 切换到项目根目录，以便所有路径都是相对于根目录
original_dir = os.getcwd()
os.chdir(PROJECT_ROOT)

print(f"[Build] SPEC_DIR: {SPEC_DIR}")
print(f"[Build] PROJECT_ROOT: {PROJECT_ROOT}")
print(f"[Build] Working from: {os.getcwd()}")

# 收集所有必要的依赖
datas = []
binaries = []
hiddenimports = []

# 收集关键包的所有内容（根据实际 import 检查）
critical_packages = [
    'dashscope',         # main_logic 使用
    'openai',            # langchain_openai 需要
    'langchain',         # brain 和 memory 使用
    'langchain_community',
    'langchain_core',
    'langchain_openai',
    'browser_use',       # browser-use agent 需要 .md 模板文件
]

for pkg in critical_packages:
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
    except Exception as e:
        print(f"Warning: Could not collect {pkg}: {e}")

# 添加配置文件（只添加 .json 文件，不包含 .py 代码）
import glob
config_json_files = glob.glob(os.path.join(PROJECT_ROOT, 'config/*.json'))
print(f"[Build] Packing {len(config_json_files)} config files:")
for json_file in config_json_files:
    print(f"  - {json_file}")
    # 使用绝对路径，目标路径为 'config'
    datas.append((json_file, 'config'))

# 添加项目目录和文件（使用绝对路径）
# 受版权保护的 live2d 模型打包到 _internal（用户不可见）
def add_data(src, dest):
    """ 添加数据文件，支持通配符 """
    src_path = os.path.join(PROJECT_ROOT, src)
    if '*' in src:
        # 处理通配符
        files = glob.glob(src_path)
        if files:
            for f in files:
                datas.append((f, dest))
        else:
            print(f"[Build] Warning: No files matched pattern '{src}', skipping")
    elif os.path.exists(src_path):
        datas.append((src_path, dest))
    else:
        print(f"[Build] Warning: {src_path} not found, skipping")

add_data('static/css', 'static/css')
add_data('static/js', 'static/js')
add_data('static/fonts', 'static/fonts')
add_data('static/vrm', 'static/vrm')
add_data('static/mao_pro', 'static/mao_pro')
add_data('static/ziraitikuwa', 'static/ziraitikuwa') 
add_data('static/libs', 'static/libs')
add_data('static/icons', 'static/icons')
add_data('static/locales', 'static/locales')
add_data('static/neko', 'static/neko')
add_data('static/kemomimi', 'static/kemomimi')
add_data('static/default', 'static/default')
add_data('static/*.js', 'static')
add_data('static/*.json', 'static')
add_data('static/*.ico', 'static')
add_data('static/*.png', 'static')
add_data('assets', 'assets')
add_data('templates', 'templates')
add_data('data/browser_use_prompts', 'data/browser_use_prompts')
add_data('steam_appid.txt', '.')

# 添加 Steam 相关的 DLL 和库文件（必须放在根目录）
# macOS 上使用 dylib，Windows 上使用 dll
if sys.platform == 'darwin':
    # macOS (Apple Silicon) 使用 .dylib
    libsteam_api = os.path.join(PROJECT_ROOT, 'libsteam_api.dylib')
    libSteamworksPy = os.path.join(PROJECT_ROOT, 'libSteamworksPy.dylib')
    if os.path.exists(libsteam_api):
        binaries.append((libsteam_api, '.'))
    if os.path.exists(libSteamworksPy):
        binaries.append((libSteamworksPy, '.'))
elif sys.platform == 'win32':
    # Windows 使用 .dll
    steam_api_dll = os.path.join(PROJECT_ROOT, 'steam_api64.dll')
    steamworks_dll = os.path.join(PROJECT_ROOT, 'SteamworksPy64.dll')
    if os.path.exists(steam_api_dll):
        binaries.append((steam_api_dll, '.'))
    if os.path.exists(steamworks_dll):
        binaries.append((steamworks_dll, '.'))
    # 添加 steam_api64.lib（如果存在，供编译时使用）
    steam_lib = os.path.join(PROJECT_ROOT, 'steam_api64.lib')
    if os.path.exists(steam_lib):
        binaries.append((steam_lib, '.'))

# 注意：lanlan_frd.exe 不打包进去，应该和 Xiao8.exe 放在同一目录

# 重要的隐藏导入（只保留实际需要的）
hiddenimports += [
    # Uvicorn 相关
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    
    # FastAPI 相关
    'fastapi',
    'fastapi.responses',
    'fastapi.staticfiles',
    'starlette',
    'starlette.staticfiles',
    'starlette.templating',
    
    # 模板引擎
    'jinja2',
    'jinja2.ext',
    
    # WebSocket
    'websockets',
    'websocket',
    
    # AI 相关
    'openai',
    'dashscope',
    'httpx',
    
    # 自动化相关（brain/computer_use.py）
    'PIL',
    'PIL.Image',
    'pyautogui',
    'gui_agents',
    
    # 音频相关
    'librosa',
    'soundfile',
    'pyaudio',
    'numpy',
    
    # 其他工具
    'inflect',
    'typeguard',
    'typeguard._decorators',
    'requests',
    'cachetools',
    
    # Langchain
    'langchain',
    'langchain_community',
    'langchain_core',
    'langchain_openai',
    
    # 项目主模块
    'main_server',
    'memory_server',
    'agent_server',
    'monitor',
    
    # config 子模块
    'config',
    'config.api',
    'config.prompts_sys',
    'config.prompts_chara',
    
    # brain 子模块
    'brain',
    'brain.processor',
    'brain.planner',
    'brain.analyzer',
    'brain.computer_use',
    'brain.deduper',
    'brain.mcp_client',
    
    # main_logic 子模块
    'main_logic',
    'main_logic.core',
    'main_logic.cross_server',
    'main_logic.omni_offline_client',
    'main_logic.omni_realtime_client',
    'main_logic.tts_client',
    
    # main_routers 子模块
    'main_routers',
    'main_routers.config_router',
    'main_routers.characters_router',
    'main_routers.live2d_router',
    'main_routers.workshop_router',
    'main_routers.memory_router',
    'main_routers.pages_router',
    'main_routers.websocket_router',
    'main_routers.agent_router',
    'main_routers.system_router',
    'main_routers.shared_state',
    
    # memory 子模块
    'memory',
    'memory.recent',
    'memory.router',
    'memory.semantic',
    'memory.settings',
    'memory.timeindex',
    
    # utils 子模块
    'utils',
    'utils.audio',
    'utils.config_manager',
    'utils.frontend_utils',
    'utils.logger_config',
    'utils.preferences',
    'utils.web_scraper',
    
    # Steam 相关模块
    'steamworks',
    'steamworks.enums',
    'steamworks.structs',
    'steamworks.exceptions',
    'steamworks.methods',
    'steamworks.util',
    'steamworks.interfaces',
    'steamworks.interfaces.apps',
    'steamworks.interfaces.friends',
    'steamworks.interfaces.matchmaking',
    'steamworks.interfaces.music',
    'steamworks.interfaces.screenshots',
    'steamworks.interfaces.users',
    'steamworks.interfaces.userstats',
    'steamworks.interfaces.utils',
    'steamworks.interfaces.workshop',
    'steamworks.interfaces.microtxn',
    'steamworks.interfaces.input',
    
    # plugin 子模块
    'plugin',
    'plugin.settings',
    'plugin.user_plugin_server',
    'plugin.api',
    'plugin.api.exceptions',
    'plugin.api.models',
    'plugin.core',
    'plugin.core.context',
    'plugin.core.state',
    'plugin.runtime',
    'plugin.runtime.communication',
    'plugin.runtime.host',
    'plugin.runtime.registry',
    'plugin.runtime.status',
    'plugin.sdk',
    'plugin.sdk.base',
    'plugin.sdk.decorators',
    'plugin.sdk.events',
    'plugin.sdk.logger',
    'plugin.sdk.version',
    'plugin.server',
    'plugin.server.exceptions',
    'plugin.server.lifecycle',
    'plugin.server.services',
    'plugin.server.utils',
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'launcher.py')],  # 使用绝对路径
    pathex=[PROJECT_ROOT],  # 添加项目根目录到路径
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['.'],  # 查找当前目录的 hook 文件
    hooksconfig={},
    runtime_hooks=[],  # 移除不存在的 runtime hook
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],  # 不打包 binaries 到 exe
    exclude_binaries=True,  # 关键：排除二进制文件，使用 onedir 模式
    name='projectneko_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 禁用 UPX 压缩以减少杀毒软件误报
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=True if sys.platform == 'darwin' else False,  # macOS 需要开启
    target_arch=platform.machine() if sys.platform == 'darwin' else None,  # 自动检测 macOS 架构 (arm64/x86_64)
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if sys.platform == 'win32' else None,  # macOS 暂不使用图标
    version='version_info.txt' if sys.platform == 'win32' else None,  # 添加版本信息减少误报
)

# 使用 COLLECT 创建目录模式分发包
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # 禁用 UPX 压缩以减少杀毒软件误报
    upx_exclude=[],
    name='N.E.K.O',
)

# 恢复原始工作目录
os.chdir(original_dir)