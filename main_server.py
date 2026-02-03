# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows multiprocessing 支持：确保子进程不会重复执行模块级初始化
from multiprocessing import freeze_support
freeze_support()

# 检查是否需要执行初始化（用于防止 Windows spawn 方式创建的子进程重复初始化）
# 方案：首次导入时设置环境变量标记，子进程会继承这个标记从而跳过初始化
_INIT_MARKER = '_NEKO_MAIN_SERVER_INITIALIZED'
_IS_MAIN_PROCESS = _INIT_MARKER not in os.environ

if _IS_MAIN_PROCESS:
    # 立即设置标记，这样任何从此进程 spawn 的子进程都会继承此标记
    os.environ[_INIT_MARKER] = '1'

# 获取应用程序根目录（与 config_manager 保持一致）
def _get_app_root():
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()

# Only adjust DLL search path on Windows
if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(_get_app_root())
    
import mimetypes # noqa
mimetypes.add_type("application/javascript", ".js")
import asyncio # noqa
import logging # noqa
from fastapi import FastAPI # noqa
from fastapi.staticfiles import StaticFiles # noqa
from main_logic import core as core, cross_server as cross_server # noqa
from fastapi.templating import Jinja2Templates # noqa
from threading import Thread, Event as ThreadEvent # noqa
from queue import Queue # noqa
import atexit # noqa
import httpx # noqa
from config import MAIN_SERVER_PORT, MONITOR_SERVER_PORT # noqa
from utils.config_manager import get_config_manager # noqa
# 导入创意工坊工具模块
from utils.workshop_utils import ( # noqa
    get_workshop_root,
    get_workshop_path
)
# 导入创意工坊路由中的函数
from main_routers.workshop_router import get_subscribed_workshop_items # noqa

# 确定 templates 目录位置（使用 _get_app_root）
template_dir = _get_app_root()

templates = Jinja2Templates(directory=template_dir)

def initialize_steamworks():
    try:
        # 明确读取steam_appid.txt文件以获取应用ID
        app_id = None
        app_id_file = os.path.join(_get_app_root(), 'steam_appid.txt')
        if os.path.exists(app_id_file):
            with open(app_id_file, 'r') as f:
                app_id = f.read().strip()
            print(f"从steam_appid.txt读取到应用ID: {app_id}")
        
        # 创建并初始化Steamworks实例
        from steamworks import STEAMWORKS
        steamworks = STEAMWORKS()
        # 显示Steamworks初始化过程的详细日志
        print("正在初始化Steamworks...")
        steamworks.initialize()
        steamworks.UserStats.RequestCurrentStats()
        # 初始化后再次获取应用ID以确认
        actual_app_id = steamworks.app_id
        print(f"Steamworks初始化完成，实际使用的应用ID: {actual_app_id}")
        
        # 检查全局logger是否已初始化，如果已初始化则记录成功信息
        if 'logger' in globals():
            logger.info(f"Steamworks初始化成功，应用ID: {actual_app_id}")
            logger.info(f"Steam客户端运行状态: {steamworks.IsSteamRunning()}")
            logger.info(f"Steam覆盖层启用状态: {steamworks.IsOverlayEnabled()}")
        
        return steamworks
    except Exception as e:
        # 检查全局logger是否已初始化，如果已初始化则记录错误，否则使用print
        error_msg = f"初始化Steamworks失败: {e}"
        if 'logger' in globals():
            logger.error(error_msg)
        else:
            print(error_msg)
        return None

def get_default_steam_info():
    global steamworks
    # 检查steamworks是否初始化成功
    if steamworks is None:
        print("Steamworks not initialized. Skipping Steam functionality.")
        if 'logger' in globals():
            logger.info("Steamworks not initialized. Skipping Steam functionality.")
        return
    
    try:
        my_steam64 = steamworks.Users.GetSteamID()
        my_steam_level = steamworks.Users.GetPlayerSteamLevel()
        subscribed_apps = steamworks.Workshop.GetNumSubscribedItems()
        print(f'Subscribed apps: {subscribed_apps}')

        print(f'Logged on as {my_steam64}, level: {my_steam_level}')
        print('Is subscribed to current app?', steamworks.Apps.IsSubscribed())
    except Exception as e:
        print(f"Error accessing Steamworks API: {e}")
        if 'logger' in globals():
            logger.error(f"Error accessing Steamworks API: {e}")

# Steamworks 初始化将在 @app.on_event("startup") 中延迟执行
# 这样可以避免在模块导入时就执行 DLL 加载等操作
steamworks = None

# Configure logging (子进程静默初始化，避免重复打印初始化消息)
from utils.logger_config import setup_logging # noqa: E402

logger, log_config = setup_logging(service_name="Main", log_level=logging.INFO, silent=not _IS_MAIN_PROCESS)

_config_manager = get_config_manager()

def _log_session_state(name: str, mgr: core.LLMSessionManager, context: str = ""):
    """输出session/TTS关键状态，用于排查打断后TTS异常."""
    try:
        logger.debug(
            f"[SessionState][{name}] {context} "
            f"use_tts={getattr(mgr, 'use_tts', None)}, "
            f"tts_ready={getattr(mgr, 'tts_ready', None)}, "
            f"tts_thread_alive={getattr(mgr, 'tts_thread', None) and getattr(mgr.tts_thread, 'is_alive', lambda: False)()}, "
            f"current_speech_id={getattr(mgr, 'current_speech_id', None)}, "
            f"input_mode={getattr(mgr, 'input_mode', None)}"
        )
    except Exception as e:  # 安全兜底，避免日志本身影响主流程
        logger.warning(f"[SessionState][{name}] 状态日志失败: {e}")

def cleanup():
    """通知所有同步线程停止"""
    logger.info("正在关闭同步线程...")
    for k in sync_shutdown_event:
        try:
            sync_shutdown_event[k].set()
        except Exception:
            pass

# 只在主进程中注册 cleanup 函数，防止子进程退出时执行清理
if _IS_MAIN_PROCESS:
    atexit.register(cleanup)

sync_message_queue = {}
sync_shutdown_event = {}
session_manager = {}
session_id = {}
sync_process = {}
# 每个角色的websocket操作锁，用于防止preserve/restore与cleanup()之间的竞争
websocket_locks = {}
# Global variables for character data (will be updated on reload)
master_name = None
her_name = None
master_basic_config = None
lanlan_basic_config = None
name_mapping = None
lanlan_prompt = None
semantic_store = None
time_store = None
setting_store = None
recent_log = None
catgirl_names = []

async def initialize_character_data():
    """初始化或重新加载角色配置数据"""
    global master_name, her_name, master_basic_config, lanlan_basic_config
    global name_mapping, lanlan_prompt, semantic_store, time_store, setting_store, recent_log
    global catgirl_names, sync_message_queue, sync_shutdown_event, session_manager, session_id, sync_process, websocket_locks
    
    logger.info("正在加载角色配置...")
    
    # 清理无效的voice_id引用
    _config_manager.cleanup_invalid_voice_ids()
    
    # 加载最新的角色数据
    master_name, her_name, master_basic_config, lanlan_basic_config, name_mapping, lanlan_prompt, semantic_store, time_store, setting_store, recent_log = _config_manager.get_character_data()
    catgirl_names = list(lanlan_prompt.keys())
    
    # 为新增的角色初始化资源
    for k in catgirl_names:
        is_new_character = False
        if k not in sync_message_queue:
            sync_message_queue[k] = Queue()
            sync_shutdown_event[k] = ThreadEvent()
            session_id[k] = None
            sync_process[k] = None
            logger.info(f"为角色 {k} 初始化新资源")
            is_new_character = True
        
        # 确保该角色有websocket锁
        if k not in websocket_locks:
            websocket_locks[k] = asyncio.Lock()
        
        # 更新或创建session manager（使用最新的prompt）
        # 使用锁保护websocket的preserve/restore操作，防止与cleanup()竞争
        async with websocket_locks[k]:
            # 如果已存在且已有websocket连接，保留websocket引用
            old_websocket = None
            if k in session_manager and session_manager[k].websocket:
                old_websocket = session_manager[k].websocket
                logger.info(f"保留 {k} 的现有WebSocket连接")
            
            # 注意：不在这里清理旧session，因为：
            # 1. 切换当前角色音色时，已在API层面关闭了session
            # 2. 切换其他角色音色时，已跳过重新加载
            # 3. 其他场景不应该影响正在使用的session
            # 如果旧session_manager有活跃session，保留它，只更新配置相关的字段
            
            # 先检查会话状态（在锁内检查避免竞态条件）
            has_active_session = k in session_manager and session_manager[k].is_active
            
            if has_active_session:
                # 有活跃session，不重新创建session_manager，只更新配置
                # 这是为了防止重新创建session_manager时破坏正在运行的session
                try:
                    old_mgr = session_manager[k]
                    # 更新prompt
                    old_mgr.lanlan_prompt = lanlan_prompt[k].replace('{LANLAN_NAME}', k).replace('{MASTER_NAME}', master_name)
                    # 重新读取角色配置以更新voice_id等字段
                    (
                        _,
                        _,
                        _,
                        lanlan_basic_config_updated,
                        _,
                        _,
                        _,
                        _,
                        _,
                        _
                    ) = _config_manager.get_character_data()
                    # 更新voice_id（这是切换音色时需要的）
                    old_mgr.voice_id = lanlan_basic_config_updated[k].get('voice_id', '')
                    logger.info(f"{k} 有活跃session，只更新配置，不重新创建session_manager")
                    _log_session_state(k, old_mgr, context="active-session-reload")
                except Exception as e:
                    logger.error(f"更新 {k} 的活跃session配置失败: {e}", exc_info=True)
                    # 配置更新失败，但为了不影响正在运行的session，继续使用旧配置
                    # 如果确实需要更新配置，可以考虑在下次session重启时再应用
            else:
                # 没有活跃session，可以安全地重新创建session_manager
                session_manager[k] = core.LLMSessionManager(
                    sync_message_queue[k],
                    k,
                    lanlan_prompt[k].replace('{LANLAN_NAME}', k).replace('{MASTER_NAME}', master_name)
                )
                
                # 将websocket锁存储到session manager中，供cleanup()使用
                session_manager[k].websocket_lock = websocket_locks[k]
                _log_session_state(k, session_manager[k], context="new-session-manager-created")
                
                # 恢复websocket引用（如果存在）
                if old_websocket:
                    session_manager[k].websocket = old_websocket
                    logger.info(f"已恢复 {k} 的WebSocket连接")
        
        # 检查并启动同步连接器线程
        # 如果是新角色，或者线程不存在/已停止，需要启动线程
        if k not in sync_process:
            sync_process[k] = None
        
        need_start_thread = False
        if is_new_character:
            # 新角色，需要启动线程
            need_start_thread = True
        elif sync_process[k] is None:
            # 线程为None，需要启动
            need_start_thread = True
        elif hasattr(sync_process[k], 'is_alive') and not sync_process[k].is_alive():
            # 线程已停止，需要重启
            need_start_thread = True
            try:
                sync_process[k].join(timeout=0.1)
            except: # noqa: E722
                pass
        
        if need_start_thread:
            try:
                sync_process[k] = Thread(
                    target=cross_server.sync_connector_process,
                    args=(sync_message_queue[k], sync_shutdown_event[k], k, f"ws://localhost:{MONITOR_SERVER_PORT}", {'bullet': False, 'monitor': True}),
                    daemon=True,
                    name=f"SyncConnector-{k}"
                )
                sync_process[k].start()
                logger.info(f"✅ 已为角色 {k} 启动同步连接器线程 ({sync_process[k].name})")
                await asyncio.sleep(0.1)  # 线程启动更快，减少等待时间
                if not sync_process[k].is_alive():
                    logger.error(f"❌ 同步连接器线程 {k} ({sync_process[k].name}) 启动后立即退出！")
                else:
                    logger.info(f"✅ 同步连接器线程 {k} ({sync_process[k].name}) 正在运行")
            except Exception as e:
                logger.error(f"❌ 启动角色 {k} 的同步连接器线程失败: {e}", exc_info=True)
    
    # 清理已删除角色的资源
    removed_names = [k for k in session_manager.keys() if k not in catgirl_names]
    for k in removed_names:
        logger.info(f"清理已删除角色 {k} 的资源")
        
        # 先停止同步连接器线程（线程只能协作式终止，不能强制kill）
        if k in sync_process and sync_process[k] is not None:
            try:
                logger.info(f"正在停止已删除角色 {k} 的同步连接器线程...")
                if k in sync_shutdown_event:
                    sync_shutdown_event[k].set()
                sync_process[k].join(timeout=3)  # 等待线程正常结束
                if sync_process[k].is_alive():
                    logger.warning(f"⚠️ 同步连接器线程 {k} 未能在超时内停止，将作为daemon线程自动清理")
                else:
                    logger.info(f"✅ 已停止角色 {k} 的同步连接器线程")
            except Exception as e:
                logger.warning(f"停止角色 {k} 的同步连接器线程时出错: {e}")
        
        # 清理队列（queue.Queue 没有 close/join_thread 方法）
        if k in sync_message_queue:
            try:
                while not sync_message_queue[k].empty():
                    sync_message_queue[k].get_nowait()
            except: # noqa
                pass
            del sync_message_queue[k]
        
        # 清理其他资源
        if k in sync_shutdown_event:
            del sync_shutdown_event[k]
        if k in session_manager:
            del session_manager[k]
        if k in session_id:
            del session_id[k]
        if k in sync_process:
            del sync_process[k]
    
    logger.info(f"角色配置加载完成，当前角色: {catgirl_names}，主人: {master_name}")

# 初始化角色数据（使用asyncio.run在模块级别执行async函数）
# 只在主进程中执行，防止 Windows 上子进程重复导入时再次启动子进程
if _IS_MAIN_PROCESS:
    import asyncio as _init_asyncio
    try:
        _init_asyncio.get_event_loop()
    except RuntimeError:
        _init_asyncio.set_event_loop(_init_asyncio.new_event_loop())
    _init_asyncio.get_event_loop().run_until_complete(initialize_character_data())
lock = asyncio.Lock()

# --- FastAPI App Setup ---
app = FastAPI()



class CustomStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith('.js'):
            response.headers['Content-Type'] = 'application/javascript'
        return response

# 确定 static 目录位置（使用 _get_app_root）
static_dir = os.path.join(_get_app_root(), 'static')

app.mount("/static", CustomStaticFiles(directory=static_dir), name="static")

# 挂载用户文档下的live2d目录（只在主进程中执行，子进程不提供HTTP服务）
if _IS_MAIN_PROCESS:
    _config_manager.ensure_live2d_directory()
    _config_manager.ensure_vrm_directory()
    _config_manager.ensure_chara_directory()
    user_live2d_path = str(_config_manager.live2d_dir)
    if os.path.exists(user_live2d_path):
        app.mount("/user_live2d", CustomStaticFiles(directory=user_live2d_path), name="user_live2d")
        logger.info(f"已挂载用户Live2D目录: {user_live2d_path}")

    # 挂载VRM动画目录（static/vrm/animation） 必须第一个挂载
    vrm_animation_path = str(_config_manager.vrm_animation_dir)
    if os.path.exists(vrm_animation_path):
        app.mount("/user_vrm/animation", CustomStaticFiles(directory=vrm_animation_path), name="user_vrm_animation")
        logger.info(f"已挂载VRM动画目录: {vrm_animation_path}")

    # 挂载VRM模型目录（用户文档目录）
    user_vrm_path = str(_config_manager.vrm_dir)
    if os.path.exists(user_vrm_path):
        app.mount("/user_vrm", CustomStaticFiles(directory=user_vrm_path), name="user_vrm")
        logger.info(f"已挂载VRM目录: {user_vrm_path}")
    
    # 挂载项目目录下的static/vrm（作为备用，如果文件在项目目录中）
    project_vrm_path = os.path.join(static_dir, 'vrm')
    if os.path.exists(project_vrm_path) and os.path.isdir(project_vrm_path):
        logger.info(f"项目VRM目录存在: {project_vrm_path} (可通过 /static/vrm/ 访问)")
    

    # 挂载用户mod路径
    user_mod_path = _config_manager.get_workshop_path()
    if os.path.exists(user_mod_path) and os.path.isdir(user_mod_path):
        app.mount("/user_mods", CustomStaticFiles(directory=user_mod_path), name="user_mods")
        logger.info(f"已挂载用户mod路径: {user_mod_path}")

# --- Initialize Shared State and Mount Routers ---
# Import and mount routers from main_routers package
from main_routers import ( # noqa
    config_router,
    characters_router,
    live2d_router,
    vrm_router,
    workshop_router,
    memory_router,
    pages_router,
    websocket_router,
    agent_router,
    system_router,
)
from main_routers.shared_state import init_shared_state # noqa

# Initialize shared state for routers to access
# 注意：steamworks 会在 startup 事件中初始化后更新
if _IS_MAIN_PROCESS:
    init_shared_state(
        sync_message_queue=sync_message_queue,
        sync_shutdown_event=sync_shutdown_event,
        session_manager=session_manager,
        session_id=session_id,
        sync_process=sync_process,
        websocket_locks=websocket_locks,
        steamworks=None,  # 延迟初始化，会在 startup 事件中设置
        templates=templates,
        config_manager=_config_manager,
        logger=logger,
        initialize_character_data=initialize_character_data,
    )

@app.post('/api/beacon/shutdown')
async def beacon_shutdown():
    """Beacon API for graceful server shutdown"""
    try:
        # 从 app.state 获取配置
        current_config = get_start_config()
        # Only respond to beacon if server was started with --open-browser
        if current_config['browser_mode_enabled']:
            logger.info("收到beacon信号，准备关闭服务器...")
            # Schedule server shutdown
            asyncio.create_task(shutdown_server_async())
            return {"success": True, "message": "服务器关闭信号已接收"}
    except Exception as e:
        logger.error(f"Beacon处理错误: {e}")
        return {"success": False, "error": str(e)}

# Mount all routers
app.include_router(config_router)
app.include_router(characters_router)
app.include_router(live2d_router)
app.include_router(vrm_router)
app.include_router(workshop_router)
app.include_router(memory_router)
# Note: pages_router should be mounted last due to catch-all route /{lanlan_name}
app.include_router(websocket_router)
app.include_router(agent_router)
app.include_router(system_router)
app.include_router(pages_router)  # Mount last for catch-all routes

# 后台预加载任务
_preload_task: asyncio.Task = None


async def _background_preload():
    """后台预加载音频处理模块
    
    注意：不需要 Event 同步机制，因为 Python 的 import lock 会自动等待首次导入完成。
    如果用户在预加载完成前点击语音，再次 import 会自动阻塞等待。
    """
    try:
        logger.info("🔄 后台预加载音频处理模块...")
        # 在线程池中执行同步导入（避免阻塞事件循环）
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            await loop.run_in_executor(pool, _sync_preload_modules)
    except Exception as e:
        logger.warning(f"⚠️ 音频处理模块预加载失败（不影响使用）: {e}")


def _sync_preload_modules():
    """同步预加载延迟导入的模块（在线程池中执行）
    
    注意：以下模块已通过导入链在启动时加载，无需预加载：
    - numpy, soxr: 通过 core.py / audio_processor.py
    - websockets: 通过 omni_realtime_client.py
    - langchain_openai/langchain_core: 通过 omni_offline_client.py
    - httpx: 通过 core.py
    - aiohttp: 通过 tts_client.py
    
    真正需要预加载的延迟导入模块：
    - pyrnnoise/audiolab: audio_processor.py 中通过 _get_rnnoise() 延迟加载
    - dashscope: tts_client.py 中仅在 cosyvoice_vc_tts_worker 函数内部导入
    - googletrans/translatepy: language_utils.py 中延迟导入的翻译库
    - translation_service: main_logic/core.py 中延迟初始化的翻译服务
    """
    import time
    start = time.time()
    
    # 1. 翻译服务相关模块（避免首轮对话延迟）
    try:
        # 预加载翻译库（googletrans, translatepy 等）
        from utils import language_utils
        # 触发翻译库的导入（如果可用）
        _ = language_utils.GOOGLETRANS_AVAILABLE
        _ = language_utils.TRANSLATEPY_AVAILABLE
        logger.debug("✅ 翻译库预加载完成")
    except Exception as e:
        logger.debug(f"⚠️ 翻译库预加载失败（不影响使用）: {e}")
    
    # 2. 翻译服务实例（需要 config_manager）
    try:
        from utils.translation_service import get_translation_service
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        # 预初始化翻译服务实例（触发 LLM 客户端创建等）
        _ = get_translation_service(config_manager)
        logger.debug("✅ 翻译服务预加载完成")
    except Exception as e:
        logger.debug(f"⚠️ 翻译服务预加载失败（不影响使用）: {e}")
    
    # 3. pyrnnoise/audiolab (音频降噪 - 延迟加载，可能较慢)
    try:
        from utils.audio_processor import _get_rnnoise
        RNNoise = _get_rnnoise()
        if RNNoise:
            # 创建临时实例以预热神经网络权重加载
            _warmup_instance = RNNoise(sample_rate=48000)
            del _warmup_instance
            logger.debug("  ✓ pyrnnoise loaded and warmed up")
        else:
            logger.debug("  ✗ pyrnnoise not available")
    except Exception as e:
        logger.debug(f"  ✗ pyrnnoise: {e}")
    
    # 4. dashscope (阿里云 CosyVoice TTS SDK - 仅在使用自定义音色时需要)
    try:
        import dashscope  # noqa: F401
        logger.debug("  ✓ dashscope loaded")
    except Exception as e:
        logger.debug(f"  ✗ dashscope: {e}")
    
    # 5. AudioProcessor 预热（numpy buffer + soxr resampler 初始化）
    try:
        from utils.audio_processor import AudioProcessor
        import numpy as np
        # 创建临时实例预热 numpy/soxr
        _warmup_processor = AudioProcessor(
            input_sample_rate=48000,
            output_sample_rate=16000,
            noise_reduce_enabled=False  # 不需要 RNNoise，前面已预热
        )
        # 模拟处理一小块音频，预热 numpy 和 soxr 的 JIT
        _dummy_audio = np.zeros(480, dtype=np.int16).tobytes()
        _ = _warmup_processor.process_chunk(_dummy_audio)
        del _warmup_processor, _dummy_audio
        logger.debug("  ✓ AudioProcessor warmed up")
    except Exception as e:
        logger.debug(f"  ✗ AudioProcessor warmup: {e}")
    
    # 6. httpx SSL 上下文预热（首次创建 AsyncClient 会初始化 SSL）
    try:
        import httpx
        import asyncio
        
        async def _warmup_httpx():
            async with httpx.AsyncClient(timeout=1.0) as client:
                # 发送一个简单请求预热 SSL 上下文
                try:
                    await client.get("http://127.0.0.1:1", timeout=0.01)
                except:  # noqa: E722
                    pass  # 预期会失败，只是为了初始化 SSL
        
        # 在当前线程的事件循环中运行（如果没有则创建临时循环）
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果已有运行中的循环，使用线程池
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_warmup_httpx())).result(timeout=2.0)
            else:
                loop.run_until_complete(_warmup_httpx())
        except RuntimeError:
            asyncio.run(_warmup_httpx())
        logger.debug("  ✓ httpx SSL context warmed up")
    except Exception as e:
        logger.debug(f"  ✗ httpx warmup: {e}")
    
    elapsed = time.time() - start
    logger.info(f"📦 模块预加载完成，耗时 {elapsed:.2f}s")


# Startup 事件：延迟初始化 Steamworks 和全局语言
@app.on_event("startup")
async def on_startup():
    """服务器启动时执行的初始化操作"""
    if _IS_MAIN_PROCESS:
        global steamworks, _preload_task
        logger.info("正在初始化 Steamworks...")
        steamworks = initialize_steamworks()
        
        # 更新 shared_state 中的 steamworks 引用
        from main_routers.shared_state import set_steamworks
        set_steamworks(steamworks)
        
        # 尝试获取 Steam 信息
        get_default_steam_info()
        
        # 在后台异步预加载音频模块（不阻塞服务器启动）
        # 注意：不需要等待机制，Python import lock 会自动处理并发
        _preload_task = asyncio.create_task(_background_preload())
        await _init_and_mount_workshop()
        logger.info("Startup 初始化完成，后台正在预加载音频模块...")

        # 初始化全局语言变量（优先级：Steam设置 > 系统设置）
        try:
            from utils.language_utils import initialize_global_language
            global_lang = initialize_global_language()
            logger.info(f"全局语言初始化完成: {global_lang}")
        except Exception as e:
            logger.warning(f"全局语言初始化失败: {e}，将使用默认值")

# 使用 FastAPI 的 app.state 来管理启动配置
def get_start_config():
    """从 app.state 获取启动配置"""
    if hasattr(app.state, 'start_config'):
        return app.state.start_config
    return {
        "browser_mode_enabled": False,
        "browser_page": "chara_manager",
        'server': None
    }

def set_start_config(config):
    """设置启动配置到 app.state"""
    app.state.start_config = config


async def _init_and_mount_workshop():
    """
    初始化并挂载创意工坊目录
    
    设计原则：
    - main 层只负责调用，不维护状态
    - 路径由 utils 层计算并持久化到 config 层
    - 其他代码需要路径时调用 get_workshop_path() 获取
    """
    try:
        # 1. 获取订阅的创意工坊物品列表
        workshop_items_result = await get_subscribed_workshop_items()
        
        # 2. 提取物品列表传给 utils 层
        subscribed_items = []
        if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
            subscribed_items = workshop_items_result.get('items', [])
        
        # 3. 调用 utils 层函数获取/计算路径（路径会被持久化到 config）
        workshop_path = get_workshop_root(subscribed_items)
        
        # 4. 挂载静态文件目录
        if workshop_path and os.path.exists(workshop_path) and os.path.isdir(workshop_path):
            try:
                app.mount("/workshop", StaticFiles(directory=workshop_path), name="workshop")
                logger.info(f"✅ 成功挂载创意工坊目录: {workshop_path}")
            except Exception as e:
                logger.error(f"挂载创意工坊目录失败: {e}")
        else:
            logger.warning(f"创意工坊目录不存在或不是有效的目录: {workshop_path}，跳过挂载")
    except Exception as e:
        logger.error(f"初始化创意工坊目录时出错: {e}")
        # 降级：确保至少有一个默认路径可用
        workshop_path = get_workshop_path()
        logger.info(f"使用配置中的默认路径: {workshop_path}")
        if workshop_path and os.path.exists(workshop_path) and os.path.isdir(workshop_path):
            try:
                app.mount("/workshop", StaticFiles(directory=workshop_path), name="workshop")
                logger.info(f"✅ 降级模式下成功挂载创意工坊目录: {workshop_path}")
            except Exception as mount_err:
                logger.error(f"降级模式挂载创意工坊目录仍然失败: {mount_err}")


async def shutdown_server_async():
    """异步关闭服务器"""
    try:
        # Give a small delay to allow the beacon response to be sent
        await asyncio.sleep(0.5)
        logger.info("正在关闭服务器...")
        
        # 向memory_server发送关闭信号
        try:
            from config import MEMORY_SERVER_PORT
            shutdown_url = f"http://localhost:{MEMORY_SERVER_PORT}/shutdown"
            async with httpx.AsyncClient(timeout=1) as client:
                response = await client.post(shutdown_url)
                if response.status_code == 200:
                    logger.info("已向memory_server发送关闭信号")
                else:
                    logger.warning(f"向memory_server发送关闭信号失败，状态码: {response.status_code}")
        except Exception as e:
            logger.warning(f"向memory_server发送关闭信号时出错: {e}")
        
        # Signal the server to stop
        current_config = get_start_config()
        if current_config['server'] is not None:
            current_config['server'].should_exit = True
    except Exception as e:
        logger.error(f"关闭服务器时出错: {e}")


# Steam 创意工坊管理相关API路由
# 确保这个路由被正确注册
if _IS_MAIN_PROCESS:
    logger.info('注册Steam创意工坊扫描API路由')


def _format_size(size_bytes):
    """
    将字节大小格式化为人类可读的格式
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"



# 辅助函数
def get_folder_size(folder_path):
    """获取文件夹大小（字节）"""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                total_size += os.path.getsize(filepath)
            except (OSError, FileNotFoundError):
                continue
    return total_size

def find_preview_image_in_folder(folder_path):
    """在文件夹中查找预览图片，只查找指定的8个图片名称"""
    # 按优先级顺序查找指定的图片文件列表
    preview_image_names = ['preview.jpg', 'preview.png', 'thumbnail.jpg', 'thumbnail.png', 
                         'icon.jpg', 'icon.png', 'header.jpg', 'header.png']
    
    for image_name in preview_image_names:
        image_path = os.path.join(folder_path, image_name)
        if os.path.exists(image_path) and os.path.isfile(image_path):
            return image_path
    
    # 如果找不到指定的图片名称，返回None
    return None

# --- Run the Server ---
if __name__ == "__main__":
    import uvicorn
    import argparse
    import signal
    import threading
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-browser",   action="store_true",
                        help="启动后是否打开浏览器并监控它")
    parser.add_argument("--page",           type=str, default="",
                        choices=["index", "chara_manager", "api_key", ""],
                        help="要打开的页面路由（不含域名和端口）")
    args = parser.parse_args()

    logger.info("--- Starting FastAPI Server ---")
    # Use os.path.abspath to show full path clearly
    logger.info(f"Serving static files from: {os.path.abspath('static')}")
    logger.info(f"Serving index.html from: {os.path.abspath('templates/index.html')}")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT} (or your network IP:{MAIN_SERVER_PORT})")
    logger.info("-----------------------------")

    # 使用统一的速率限制日志过滤器
    from utils.logger_config import create_main_server_filter, create_httpx_filter
    
    # Add filter to uvicorn access logger
    logging.getLogger("uvicorn.access").addFilter(create_main_server_filter())
    
    # Add filter to httpx logger for availability check requests
    logging.getLogger("httpx").addFilter(create_httpx_filter())

    # 1) 配置 UVicorn
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=MAIN_SERVER_PORT,
        log_level="info",
        loop="asyncio",
        reload=False,
    )
    server = uvicorn.Server(config)
    
    # Set browser mode flag if --open-browser is used
    if args.open_browser:
        # 使用 FastAPI 的 app.state 来管理配置
        start_config = {
            "browser_mode_enabled": True,
            "browser_page": args.page if args.page!='index' else '',
            'server': server
        }
        set_start_config(start_config)
    else:
        # 设置默认配置
        start_config = {
            "browser_mode_enabled": False,
            "browser_page": "",
            'server': server
        }
        set_start_config(start_config)

    print(f"启动配置: {get_start_config()}")

    # 2) 信号处理：Ctrl+C 时快速关闭
    def _signal_handler(signum, frame):
        logger.info("正在关闭服务器...")
        cleanup()
        server.should_exit = True
        threading.Timer(3.0, lambda: os._exit(0)).start()
    
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 4) 启动服务器（阻塞，直到 server.should_exit=True）
    logger.info("--- Starting FastAPI Server ---")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT}/{args.page}")
    
    try:
        server.run()
    except KeyboardInterrupt:
        # 信号处理器已经处理了，这里只是捕获异常防止 traceback
        pass
    finally:
        logger.info("服务器已关闭")