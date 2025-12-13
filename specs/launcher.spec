# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_all
from PyInstaller.building.build_main import Tree

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
    'langchain_openai'
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
config_json_files = glob.glob('config/*.json')
print(f"[Build] Packing {len(config_json_files)} config files:")
for json_file in config_json_files:
    print(f"  - {json_file}")
    datas.append((json_file, 'config'))

# 添加项目目录和文件
# 受版权保护的 live2d 模型打包到 _internal（用户不可见）
datas += [
    ('static/mao_pro', 'static/mao_pro'),           # 打包到 _internal
    ('static/ziraitikuwa', 'static/ziraitikuwa'),   # 打包到 _internal
    ('static/libs', 'static/libs'),                 # live2d 库
    ('static/icons', 'static/icons'),               # 图标目录（包含所有 UI 图标）
    ('static/locales', 'static/locales'),           # i18n 国际化翻译文件
    ('static/*.js', 'static'),                      # JS 文件
    ('static/*.json', 'static'),                    # manifest 等
    ('static/*.ico', 'static'),                     # favicon
    ('static/*.png', 'static'),                     # 根目录图标
    ('assets', 'assets'),
    ('templates', 'templates'),
    ('steam_appid.txt', '.'),                       # Steam App ID 文件
]

# 添加 Steam 相关的 DLL 和库文件（必须放在根目录）
binaries += [
    ('steam_api64.dll', '.'),                       # Steam API DLL
    ('SteamworksPy64.dll', '.'),                    # SteamworksPy 64位 DLL
]

# 添加 steam_api64.lib（如果存在，供编译时使用）
if os.path.exists('steam_api64.lib'):
    binaries.append(('steam_api64.lib', '.'))

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
    ['launcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['.'],  # 查找当前目录的 hook 文件
    hooksconfig={},
    runtime_hooks=['runtime_hook_inflect.py'],  # 运行时 hook
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
    name='server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 禁用 UPX 压缩以减少杀毒软件误报
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if sys.platform == 'win32' else None,
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

