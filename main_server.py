# -*- coding: utf-8 -*-
import sys, os
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
    
import mimetypes
mimetypes.add_type("application/javascript", ".js")
import asyncio
import logging


from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from main_logic import core as core, cross_server as cross_server
from fastapi.templating import Jinja2Templates
from threading import Thread, Event as ThreadEvent
from queue import Queue
import atexit
import httpx
from config import MAIN_SERVER_PORT, MONITOR_SERVER_PORT
from utils.config_manager import get_config_manager
# 导入创意工坊工具模块
from utils.workshop_utils import (
    get_workshop_root,
    get_workshop_path
)

# 导入创意工坊路由中的函数
from main_routers.workshop_router import get_subscribed_workshop_items

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

# 初始化Steamworks，但即使失败也继续启动服务
# 只在主进程中初始化，防止子进程重复初始化
if _IS_MAIN_PROCESS:
    steamworks = initialize_steamworks()
    # 尝试获取Steam信息，如果失败也不会阻止服务启动
    get_default_steam_info()
else:
    steamworks = None


# 使用真实的截图库，函数已从utils.screenshot_utils导入


# Configure logging (子进程静默初始化，避免重复打印初始化消息)
from utils.logger_config import setup_logging

logger, log_config = setup_logging(service_name="Main", log_level=logging.INFO, silent=not _IS_MAIN_PROCESS)

_config_manager = get_config_manager()

def cleanup():
    logger.info("Starting cleanup process")
    for k in sync_message_queue:
        # 清空队列（queue.Queue 没有 close/join_thread 方法）
        try:
            while sync_message_queue[k] and not sync_message_queue[k].empty():
                sync_message_queue[k].get_nowait()
        except:
            pass
    logger.info("Cleanup completed")

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
            except:
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
            except:
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

@app.on_event("startup")
async def startup_event():
    """FastAPI应用启动事件处理函数"""
    await _init_and_mount_workshop()

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
    _config_manager.ensure_chara_directory()
    user_live2d_path = str(_config_manager.live2d_dir)
    if os.path.exists(user_live2d_path):
        app.mount("/user_live2d", CustomStaticFiles(directory=user_live2d_path), name="user_live2d")
        logger.info(f"已挂载用户Live2D目录: {user_live2d_path}")

    # 挂载用户mod路径
    user_mod_path = _config_manager.get_workshop_path()
    if os.path.exists(user_mod_path) and os.path.isdir(user_mod_path):
        app.mount("/user_mods", CustomStaticFiles(directory=user_mod_path), name="user_mods")
        logger.info(f"已挂载用户mod路径: {user_mod_path}")

# --- Initialize Shared State and Mount Routers ---
# Import and mount routers from main_routers package
from main_routers import (
    config_router,
    characters_router,
    live2d_router,
    workshop_router,
    memory_router,
    pages_router,
    websocket_router,
    agent_router,
    system_router,
)
from main_routers.shared_state import init_shared_state

# Initialize shared state for routers to access
if _IS_MAIN_PROCESS:
    init_shared_state(
        sync_message_queue=sync_message_queue,
        sync_shutdown_event=sync_shutdown_event,
        session_manager=session_manager,
        session_id=session_id,
        sync_process=sync_process,
        websocket_locks=websocket_locks,
        steamworks=steamworks,
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
app.include_router(workshop_router)
app.include_router(memory_router)
# Note: pages_router should be mounted last due to catch-all route /{lanlan_name}
app.include_router(websocket_router)
app.include_router(agent_router)
app.include_router(system_router)
app.include_router(pages_router)  # Mount last for catch-all routes

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

@app.get("/", response_class=HTMLResponse)
async def get_default_index(request: Request):
    return templates.TemplateResponse("templates/index.html", {
        "request": request
    })


@app.get("/api/preferences")
async def get_preferences():
    """获取用户偏好设置"""
    preferences = load_user_preferences()
    return preferences

@app.post("/api/preferences")
async def save_preferences(request: Request):
    """保存用户偏好设置"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        # 验证偏好数据
        if not validate_model_preferences(data):
            return {"success": False, "error": "偏好数据格式无效"}
        
        # 获取参数（可选）
        parameters = data.get('parameters')
        
        # 更新偏好
        if update_model_preferences(data['model_path'], data['position'], data['scale'], parameters):
            return {"success": True, "message": "偏好设置已保存"}
        else:
            return {"success": False, "error": "保存失败"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/steam_language")
async def get_steam_language():
    """获取 Steam 客户端的语言设置，用于前端 i18n 初始化"""
    global steamworks
    
    # Steam 语言代码到 i18n 语言代码的映射
    # 参考: https://partner.steamgames.com/doc/store/localization/languages
    STEAM_TO_I18N_MAP = {
        'schinese': 'zh-CN',      # 简体中文
        'tchinese': 'zh-CN',      # 繁体中文（映射到简体中文，因为目前只支持 zh-CN）
        'english': 'en',          # 英文
        # 其他语言默认映射到英文
    }
    
    try:
        if steamworks is None:
            return {
                "success": False,
                "error": "Steamworks 未初始化",
                "steam_language": None,
                "i18n_language": None
            }
        
        # 获取 Steam 当前游戏语言
        steam_language = steamworks.Apps.GetCurrentGameLanguage()
        # Steam API 可能返回 bytes，需要解码为字符串
        if isinstance(steam_language, bytes):
            steam_language = steam_language.decode('utf-8')
        
        # 映射到 i18n 语言代码
        i18n_language = STEAM_TO_I18N_MAP.get(steam_language, 'en')  # 默认英文
        logger.info(f"[i18n] Steam 语言映射: '{steam_language}' -> '{i18n_language}'")
        
        return {
            "success": True,
            "steam_language": steam_language,
            "i18n_language": i18n_language
        }
        
    except Exception as e:
        logger.error(f"获取 Steam 语言设置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "steam_language": None,
            "i18n_language": None
        }


@app.get("/api/live2d/models")
async def get_live2d_models(simple: bool = False):
    """
    获取Live2D模型列表
    Args:
        simple: 如果为True，只返回模型名称列表；如果为False，返回完整的模型信息
    """
    try:
        # 先获取本地模型
        models = find_models()
        
        # 再获取Steam创意工坊模型
        try:
            workshop_items_result = await get_subscribed_workshop_items()
            
            # 处理响应结果
            if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                items = workshop_items_result.get('items', [])
                logger.info(f"获取到{len(items)}个订阅的创意工坊物品")
                
                # 遍历所有物品，提取已安装的模型
                for item in items:
                    # 直接使用get_subscribed_workshop_items返回的installedFolder
                    installed_folder = item.get('installedFolder')
                    # 从publishedFileId字段获取物品ID，而不是item_id
                    item_id = item.get('publishedFileId')
                    # 获取物品标题用于显示
                    item_title = item.get('title', '')
                    
                    if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and item_id:
                        logger.debug(f"检查创意工坊物品: id={item_id}, title={item_title}, folder={installed_folder}")
                        
                        # 检查安装目录下是否有.model3.json文件
                        for filename in os.listdir(installed_folder):
                            if filename.endswith('.model3.json'):
                                model_name = os.path.splitext(os.path.splitext(filename)[0])[0]
                                
                                # 跳过纯数字模型名（这通常是错误的配置，使用了Steam ID而不是真正的模型名）
                                if model_name.isdigit():
                                    logger.warning(f"跳过纯数字模型名: {model_name} (可能是Steam ID)，请检查创意工坊物品内容")
                                    continue
                                
                                # 避免重复添加
                                if model_name not in [m['name'] for m in models]:
                                    # 构建正确的/workshop URL路径，确保没有多余的引号
                                    path_value = f'/workshop/{item_id}/{filename}'
                                    logger.debug(f"添加模型路径: {path_value!r}, item_id类型: {type(item_id)}, filename类型: {type(filename)}")
                                    # 移除可能的额外引号
                                    path_value = path_value.strip('"')
                                    # 确定display_name：优先使用物品标题
                                    display_name = item_title if item_title and not item_title.startswith('未知物品_') else model_name
                                    logger.info(f"添加创意工坊模型: name={model_name}, display_name={display_name}, item_title={item_title}")
                                    models.append({
                                        'name': model_name,
                                        'display_name': display_name,
                                        'path': path_value,
                                        'source': 'steam_workshop',
                                        'item_id': item_id
                                    })
                            
                        # 检查安装目录下的子目录
                        for subdir in os.listdir(installed_folder):
                            subdir_path = os.path.join(installed_folder, subdir)
                            if os.path.isdir(subdir_path):
                                model_name = subdir
                                
                                # 跳过纯数字子目录名
                                if model_name.isdigit():
                                    logger.debug(f"跳过纯数字子目录: {model_name}")
                                    continue
                                    
                                json_file = os.path.join(subdir_path, f'{model_name}.model3.json')
                                if os.path.exists(json_file):
                                    # 避免重复添加
                                    if model_name not in [m['name'] for m in models]:
                                        # 构建正确的/workshop URL路径，确保没有多余的引号
                                        path_value = f'/workshop/{item_id}/{model_name}/{model_name}.model3.json'
                                        logger.debug(f"添加子目录模型路径: {path_value!r}, item_id类型: {type(item_id)}, model_name类型: {type(model_name)}")
                                        # 移除可能的额外引号
                                        path_value = path_value.strip('"')
                                        models.append({
                                            'name': model_name,
                                            'display_name': item_title if item_title and not item_title.startswith('未知物品_') else model_name,
                                            'path': path_value,
                                            'source': 'steam_workshop',
                                            'item_id': item_id
                                        })
        except Exception as e:
            logger.error(f"获取创意工坊模型时出错: {e}")
        
        if simple:
            # 只返回模型名称列表
            model_names = [model["name"] for model in models]
            return {"success": True, "models": model_names}
        else:
            # 返回完整的模型信息（保持向后兼容）
            return models
    except Exception as e:
        logger.error(f"获取Live2D模型列表失败: {e}")
        if simple:
            return {"success": False, "error": str(e)}
        else:
            return []


@app.get("/api/models")
async def get_models_legacy():
    """
    向后兼容的API端点，重定向到新的 /api/live2d/models
    """
    return await get_live2d_models(simple=False)

@app.post("/api/preferences/set-preferred")
async def set_preferred_model(request: Request):
    """设置首选模型"""
    try:
        data = await request.json()
        if not data or 'model_path' not in data:
            return {"success": False, "error": "无效的数据"}
        
        if move_model_to_top(data['model_path']):
            return {"success": True, "message": "首选模型已更新"}
        else:
            return {"success": False, "error": "模型不存在或更新失败"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/config/page_config")
async def get_page_config(lanlan_name: str = ""):
    """获取页面配置（lanlan_name 和 model_path）"""
    try:
        # 获取角色数据
        _, her_name, _, lanlan_basic_config, _, _, _, _, _, _ = _config_manager.get_character_data()
        
        # 如果提供了 lanlan_name 参数，使用它；否则使用当前角色
        target_name = lanlan_name if lanlan_name else her_name
        
        # 获取 live2d 和 live2d_item_id 字段
        live2d = lanlan_basic_config.get(target_name, {}).get('live2d', 'mao_pro')
        live2d_item_id = lanlan_basic_config.get(target_name, {}).get('live2d_item_id', '')
        
        logger.debug(f"获取页面配置 - 角色: {target_name}, 模型: {live2d}, item_id: {live2d_item_id}")
        
        # 使用 get_current_live2d_model 函数获取正确的模型信息
        # 第一个参数是角色名称，第二个参数是item_id
        model_response = await get_current_live2d_model(target_name, live2d_item_id)
        # 提取JSONResponse中的内容
        model_data = model_response.body.decode('utf-8')
        import json
        model_json = json.loads(model_data)
        model_info = model_json.get('model_info', {})
        model_path = model_info.get('path', '')
        
        return {
            "success": True,
            "lanlan_name": target_name,
            "model_path": model_path
        }
    except Exception as e:
        logger.error(f"获取页面配置失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "lanlan_name": "",
            "model_path": ""
        }

@app.get("/api/config/core_api")
async def get_core_config_api():
    """获取核心配置（API Key）"""
    try:
        # 尝试从core_config.json读取
        try:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
            core_config_path = str(config_manager.get_config_path('core_config.json'))
            with open(core_config_path, 'r', encoding='utf-8') as f:
                core_cfg = json.load(f)
                api_key = core_cfg.get('coreApiKey', '')
        except FileNotFoundError:
            # 如果文件不存在，返回当前配置中的CORE_API_KEY
            core_config = _config_manager.get_core_config()
            api_key = core_config['CORE_API_KEY']
            # 创建空的配置对象用于返回默认值
            core_cfg = {}
        
        return {
            "api_key": api_key,
            "coreApi": core_cfg.get('coreApi', 'qwen'),
            "assistApi": core_cfg.get('assistApi', 'qwen'),
            "assistApiKeyQwen": core_cfg.get('assistApiKeyQwen', ''),
            "assistApiKeyOpenai": core_cfg.get('assistApiKeyOpenai', ''),
            "assistApiKeyGlm": core_cfg.get('assistApiKeyGlm', ''),
            "assistApiKeyStep": core_cfg.get('assistApiKeyStep', ''),
            "assistApiKeySilicon": core_cfg.get('assistApiKeySilicon', ''),
            "mcpToken": core_cfg.get('mcpToken', ''),  # 添加mcpToken字段
            "enableCustomApi": core_cfg.get('enableCustomApi', False),  # 添加enableCustomApi字段
            # 自定义API相关字段
            "summaryModelProvider": core_cfg.get('summaryModelProvider', ''),
            "summaryModelUrl": core_cfg.get('summaryModelUrl', ''),
            "summaryModelId": core_cfg.get('summaryModelId', ''),
            "summaryModelApiKey": core_cfg.get('summaryModelApiKey', ''),
            "correctionModelProvider": core_cfg.get('correctionModelProvider', ''),
            "correctionModelUrl": core_cfg.get('correctionModelUrl', ''),
            "correctionModelId": core_cfg.get('correctionModelId', ''),
            "correctionModelApiKey": core_cfg.get('correctionModelApiKey', ''),
            "emotionModelProvider": core_cfg.get('emotionModelProvider', ''),
            "emotionModelUrl": core_cfg.get('emotionModelUrl', ''),
            "emotionModelId": core_cfg.get('emotionModelId', ''),
            "emotionModelApiKey": core_cfg.get('emotionModelApiKey', ''),
            "visionModelProvider": core_cfg.get('visionModelProvider', ''),
            "visionModelUrl": core_cfg.get('visionModelUrl', ''),
            "visionModelId": core_cfg.get('visionModelId', ''),
            "visionModelApiKey": core_cfg.get('visionModelApiKey', ''),
            "omniModelProvider": core_cfg.get('omniModelProvider', ''),
            "omniModelUrl": core_cfg.get('omniModelUrl', ''),
            "omniModelId": core_cfg.get('omniModelId', ''),
            "omniModelApiKey": core_cfg.get('omniModelApiKey', ''),
            "ttsModelProvider": core_cfg.get('ttsModelProvider', ''),
            "ttsModelUrl": core_cfg.get('ttsModelUrl', ''),
            "ttsModelId": core_cfg.get('ttsModelId', ''),
            "ttsModelApiKey": core_cfg.get('ttsModelApiKey', ''),
            "success": True
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/config/api_providers")
async def get_api_providers_config():
    """获取API服务商配置（供前端使用）"""
    try:
        from utils.api_config_loader import (
            get_core_api_providers_for_frontend,
            get_assist_api_providers_for_frontend,
        )
        
        # 使用缓存加载配置（性能更好，配置更新后需要重启服务）
        core_providers = get_core_api_providers_for_frontend()
        assist_providers = get_assist_api_providers_for_frontend()
        
        return {
            "success": True,
            "core_api_providers": core_providers,
            "assist_api_providers": assist_providers,
        }
    except Exception as e:
        logger.error(f"获取API服务商配置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "core_api_providers": [],
            "assist_api_providers": [],
        }


@app.get("/api/config/info")
async def get_config_info():
    """获取配置目录信息（供前端使用）"""
    try:
        return _config_manager.get_config_info()
    except Exception as e:
        logger.error(f"获取配置目录信息失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "live2d_dir": ""
        }

@app.post("/api/steam/upload-preview-image")
async def upload_preview_image(file: UploadFile = File(...)):
    """上传预览图片到临时位置"""
    try:
        # 验证文件类型
        if not file.filename.lower().endswith(('.jpg', '.jpeg', '.png')):
            return JSONResponse(status_code=400, content={"success": False, "error": "只支持 JPG 和 PNG 格式"})
        
        # 验证文件大小（小于1MB）
        content = await file.read()
        if len(content) > 1024 * 1024:
            return {"success": False, "error": "文件大小必须小于1MB"}
        
        # 保存到临时目录
        temp_dir = _config_manager.app_docs_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
        temp_path = temp_dir / file.filename
        
        with open(temp_path, 'wb') as f:
            f.write(content)
        
        logger.info(f"预览图片已上传到: {temp_path}")
        return {"success": True, "path": str(temp_path)}
    except Exception as e:
        logger.error(f"上传预览图片失败: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/config/core_api")
async def update_core_config(request: Request):
    """更新核心配置（API Key）"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        # 检查是否启用了自定义API
        enable_custom_api = data.get('enableCustomApi', False)
        
        # 如果启用了自定义API，不需要强制检查核心API key
        if not enable_custom_api:
            # 检查是否为免费版配置
            is_free_version = data.get('coreApi') == 'free' or data.get('assistApi') == 'free'
            
            if 'coreApiKey' not in data:
                return {"success": False, "error": "缺少coreApiKey字段"}
            
            api_key = data['coreApiKey']
            if api_key is None:
                return {"success": False, "error": "API Key不能为null"}
            
            if not isinstance(api_key, str):
                return {"success": False, "error": "API Key必须是字符串类型"}
            
            api_key = api_key.strip()
            
            # 免费版允许使用 'free-access' 作为API key，不进行空值检查
            if not is_free_version and not api_key:
                return {"success": False, "error": "API Key不能为空"}
        
        # 保存到core_config.json
        from pathlib import Path
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        core_config_path = str(config_manager.get_config_path('core_config.json'))
        # 确保配置目录存在
        Path(core_config_path).parent.mkdir(parents=True, exist_ok=True)
        
        # 构建配置对象
        core_cfg = {}
        
        # 只有在启用自定义API时，才允许不设置coreApiKey
        if enable_custom_api:
            # 启用自定义API时，coreApiKey是可选的
            if 'coreApiKey' in data:
                api_key = data['coreApiKey']
                if api_key is not None and isinstance(api_key, str):
                    core_cfg['coreApiKey'] = api_key.strip()
        else:
            # 未启用自定义API时，必须设置coreApiKey
            api_key = data.get('coreApiKey', '')
            if api_key is not None and isinstance(api_key, str):
                core_cfg['coreApiKey'] = api_key.strip()
        if 'coreApi' in data:
            core_cfg['coreApi'] = data['coreApi']
        if 'assistApi' in data:
            core_cfg['assistApi'] = data['assistApi']
        if 'assistApiKeyQwen' in data:
            core_cfg['assistApiKeyQwen'] = data['assistApiKeyQwen']
        if 'assistApiKeyOpenai' in data:
            core_cfg['assistApiKeyOpenai'] = data['assistApiKeyOpenai']
        if 'assistApiKeyGlm' in data:
            core_cfg['assistApiKeyGlm'] = data['assistApiKeyGlm']
        if 'assistApiKeyStep' in data:
            core_cfg['assistApiKeyStep'] = data['assistApiKeyStep']
        if 'assistApiKeySilicon' in data:
            core_cfg['assistApiKeySilicon'] = data['assistApiKeySilicon']
        if 'mcpToken' in data:
            core_cfg['mcpToken'] = data['mcpToken']
        if 'enableCustomApi' in data:
            core_cfg['enableCustomApi'] = data['enableCustomApi']
        
        # 添加用户自定义API配置
        if 'summaryModelProvider' in data:
            core_cfg['summaryModelProvider'] = data['summaryModelProvider']
        if 'summaryModelUrl' in data:
            core_cfg['summaryModelUrl'] = data['summaryModelUrl']
        if 'summaryModelId' in data:
            core_cfg['summaryModelId'] = data['summaryModelId']
        if 'summaryModelApiKey' in data:
            core_cfg['summaryModelApiKey'] = data['summaryModelApiKey']
        if 'correctionModelProvider' in data:
            core_cfg['correctionModelProvider'] = data['correctionModelProvider']
        if 'correctionModelUrl' in data:
            core_cfg['correctionModelUrl'] = data['correctionModelUrl']
        if 'correctionModelId' in data:
            core_cfg['correctionModelId'] = data['correctionModelId']
        if 'correctionModelApiKey' in data:
            core_cfg['correctionModelApiKey'] = data['correctionModelApiKey']
        if 'emotionModelProvider' in data:
            core_cfg['emotionModelProvider'] = data['emotionModelProvider']
        if 'emotionModelUrl' in data:
            core_cfg['emotionModelUrl'] = data['emotionModelUrl']
        if 'emotionModelId' in data:
            core_cfg['emotionModelId'] = data['emotionModelId']
        if 'emotionModelApiKey' in data:
            core_cfg['emotionModelApiKey'] = data['emotionModelApiKey']
        if 'visionModelProvider' in data:
            core_cfg['visionModelProvider'] = data['visionModelProvider']
        if 'visionModelUrl' in data:
            core_cfg['visionModelUrl'] = data['visionModelUrl']
        if 'visionModelId' in data:
            core_cfg['visionModelId'] = data['visionModelId']
        if 'visionModelApiKey' in data:
            core_cfg['visionModelApiKey'] = data['visionModelApiKey']
        if 'omniModelProvider' in data:
            core_cfg['omniModelProvider'] = data['omniModelProvider']
        if 'omniModelUrl' in data:
            core_cfg['omniModelUrl'] = data['omniModelUrl']
        if 'omniModelId' in data:
            core_cfg['omniModelId'] = data['omniModelId']
        if 'omniModelApiKey' in data:
            core_cfg['omniModelApiKey'] = data['omniModelApiKey']
        if 'ttsModelProvider' in data:
            core_cfg['ttsModelProvider'] = data['ttsModelProvider']
        if 'ttsModelUrl' in data:
            core_cfg['ttsModelUrl'] = data['ttsModelUrl']
        if 'ttsModelId' in data:
            core_cfg['ttsModelId'] = data['ttsModelId']
        if 'ttsModelApiKey' in data:
            core_cfg['ttsModelApiKey'] = data['ttsModelApiKey']
        
        with open(core_config_path, 'w', encoding='utf-8') as f:
            json.dump(core_cfg, f, indent=2, ensure_ascii=False)
        
        # API配置更新后，需要先通知所有客户端，再关闭session，最后重新加载配置
        logger.info("API配置已更新，准备通知客户端并重置所有session...")
        
        # 1. 先通知所有连接的客户端即将刷新（WebSocket还连着）
        notification_count = 0
        for lanlan_name, mgr in session_manager.items():
            if mgr.is_active and mgr.websocket:
                try:
                    await mgr.websocket.send_text(json.dumps({
                        "type": "reload_page",
                        "message": "API配置已更新，页面即将刷新"
                    }))
                    notification_count += 1
                    logger.info(f"已通知 {lanlan_name} 的前端刷新页面")
                except Exception as e:
                    logger.warning(f"通知 {lanlan_name} 的WebSocket失败: {e}")
        
        logger.info(f"已通知 {notification_count} 个客户端")
        
        # 2. 立刻关闭所有活跃的session（这会断开所有WebSocket）
        sessions_ended = []
        for lanlan_name, mgr in session_manager.items():
            if mgr.is_active:
                try:
                    await mgr.end_session(by_server=True)
                    sessions_ended.append(lanlan_name)
                    logger.info(f"{lanlan_name} 的session已结束")
                except Exception as e:
                    logger.error(f"结束 {lanlan_name} 的session时出错: {e}")
        
        # 3. 重新加载配置并重建session manager
        logger.info("正在重新加载配置...")
        try:
            await initialize_character_data()
            logger.info("配置重新加载完成，新的API配置已生效")
        except Exception as reload_error:
            logger.error(f"重新加载配置失败: {reload_error}")
            return {"success": False, "error": f"配置已保存但重新加载失败: {str(reload_error)}"}
        
        logger.info(f"已通知 {notification_count} 个连接的客户端API配置已更新")
        return {"success": True, "message": "API Key已保存并重新加载配置", "sessions_ended": len(sessions_ended)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.on_event("startup")
async def startup_event():
    global sync_process
    logger.info("Starting main server...")
    
    # ========== 初始化创意工坊目录 ==========
    # 依赖方向: main_server → utils → config (单向)
    # main 层只负责调用 utils，不维护任何 workshop 状态
    # 路径由 utils 层管理并持久化到 config 层
    await _init_and_mount_workshop()
    
    # ========== 启动同步连接器线程 ==========
    logger.info("Starting sync connector threads")
    # 启动同步连接器线程（确保所有角色都有线程）
    for k in list(sync_message_queue.keys()):
        if k not in sync_process or sync_process[k] is None or (hasattr(sync_process.get(k), 'is_alive') and not sync_process[k].is_alive()):
            if k in sync_process and sync_process[k] is not None:
                # 清理已停止的线程
                try:
                    sync_process[k].join(timeout=0.1)
                except:
                    pass
            try:
                sync_process[k] = Thread(
                    target=cross_server.sync_connector_process,
                    args=(sync_message_queue[k], sync_shutdown_event[k], k, f"ws://localhost:{MONITOR_SERVER_PORT}", {'bullet': False, 'monitor': True}),
                    daemon=True,
                    name=f"SyncConnector-{k}"
                )
                sync_process[k].start()
                logger.info(f"✅ 同步连接器线程已启动 ({sync_process[k].name}) for {k}")
                # 检查线程是否成功启动
                await asyncio.sleep(0.1)  # 线程启动更快
                if not sync_process[k].is_alive():
                    logger.error(f"❌ 同步连接器线程 {k} ({sync_process[k].name}) 启动后立即退出！")
                else:
                    logger.info(f"✅ 同步连接器线程 {k} ({sync_process[k].name}) 正在运行")
            except Exception as e:
                logger.error(f"❌ 启动角色 {k} 的同步连接器线程失败: {e}", exc_info=True)
    
    # 如果启用了浏览器模式，在服务器启动完成后打开浏览器
    current_config = get_start_config()
    print(f"启动配置: {current_config}")
    if current_config['browser_mode_enabled']:
        import threading
        
        def launch_browser_delayed():
            # 等待一小段时间确保服务器完全启动
            import time
            time.sleep(1)
            # 从 app.state 获取配置
            config = get_start_config()
            url = f"http://127.0.0.1:{MAIN_SERVER_PORT}/{config['browser_page']}"
            try:
                webbrowser.open(url)
                logger.info(f"服务器启动完成，已打开浏览器访问: {url}")
            except Exception as e:
                logger.error(f"打开浏览器失败: {e}")
        
        # 在独立线程中启动浏览器
        t = threading.Thread(target=launch_browser_delayed, daemon=True)
        t.start()


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    logger.info("Shutting down sync connector threads")
    # 关闭同步服务器连接（线程只能协作式终止）
    for k in sync_process:
        if sync_process[k] is not None:
            sync_shutdown_event[k].set()
            sync_process[k].join(timeout=3)  # 等待线程正常结束
            if sync_process[k].is_alive():
                logger.warning(f"⚠️ 同步连接器线程 {k} 未能在超时内停止，将作为daemon线程随主进程退出")
    logger.info("同步连接器线程已停止")
    
    # 向memory_server发送关闭信号
    try:
        from config import MEMORY_SERVER_PORT
        shutdown_url = f"http://localhost:{MEMORY_SERVER_PORT}/shutdown"
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.post(shutdown_url)
            if response.status_code == 200:
                logger.info("已向memory_server发送关闭信号")
            else:
                logger.warning(f"向memory_server发送关闭信号失败，状态码: {response.status_code}")
    except Exception as e:
        logger.warning(f"向memory_server发送关闭信号时出错: {e}")


@app.websocket("/ws/{lanlan_name}")
async def websocket_endpoint(websocket: WebSocket, lanlan_name: str):
    await websocket.accept()
    
    # 检查角色是否存在，如果不存在则通知前端并关闭连接
    if lanlan_name not in session_manager:
        logger.warning(f"❌ 角色 {lanlan_name} 不存在，当前可用角色: {list(session_manager.keys())}")
        # 获取当前正确的角色名
        current_catgirl = None
        if session_manager:
            current_catgirl = list(session_manager.keys())[0]
        # 通知前端切换到正确的角色
        if current_catgirl:
            try:
                await websocket.send_text(json.dumps({
                    "type": "catgirl_switched",
                    "new_catgirl": current_catgirl,
                    "old_catgirl": lanlan_name
                }))
                logger.info(f"已通知前端切换到正确的角色: {current_catgirl}")
                # 等待一下让客户端有时间处理消息，避免 onclose 在 onmessage 之前触发
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"通知前端失败: {e}")
        await websocket.close()
        return
    
    this_session_id = uuid.uuid4()
    async with lock:
        global session_id
        session_id[lanlan_name] = this_session_id
    logger.info(f"⭐websocketWebSocket accepted: {websocket.client}, new session id: {session_id[lanlan_name]}, lanlan_name: {lanlan_name}")
    
    # 立即设置websocket到session manager，以支持主动搭话
    # 注意：这里设置后，即使cleanup()被调用，websocket也会在start_session时重新设置
    session_manager[lanlan_name].websocket = websocket
    logger.info(f"✅ 已设置 {lanlan_name} 的WebSocket连接")

    try:
        while True:
            data = await websocket.receive_text()
            # 安全检查：如果角色已被重命名或删除，lanlan_name 可能不再存在
            if lanlan_name not in session_id or lanlan_name not in session_manager:
                logger.info(f"角色 {lanlan_name} 已被重命名或删除，关闭旧连接")
                await websocket.close()
                break
            if session_id[lanlan_name] != this_session_id:
                await session_manager[lanlan_name].send_status(f"切换至另一个终端...")
                await websocket.close()
                break
            message = json.loads(data)
            action = message.get("action")
            # logger.debug(f"WebSocket received action: {action}") # Optional debug log

            if action == "start_session":
                session_manager[lanlan_name].active_session_is_idle = False
                input_type = message.get("input_type", "audio")
                if input_type in ['audio', 'screen', 'camera', 'text']:
                    # 传递input_mode参数，告知session manager使用何种模式
                    mode = 'text' if input_type == 'text' else 'audio'
                    asyncio.create_task(session_manager[lanlan_name].start_session(websocket, message.get("new_session", False), mode))
                else:
                    await session_manager[lanlan_name].send_status(f"Invalid input type: {input_type}")

            elif action == "stream_data":
                asyncio.create_task(session_manager[lanlan_name].stream_data(message))

            elif action == "end_session":
                session_manager[lanlan_name].active_session_is_idle = False
                asyncio.create_task(session_manager[lanlan_name].end_session())

            elif action == "pause_session":
                session_manager[lanlan_name].active_session_is_idle = True
                asyncio.create_task(session_manager[lanlan_name].end_session())

            elif action == "ping":
                # 心跳保活消息，回复pong
                await websocket.send_text(json.dumps({"type": "pong"}))
                # logger.debug(f"收到心跳ping，已回复pong")

            else:
                logger.warning(f"Unknown action received: {action}")
                await session_manager[lanlan_name].send_status(f"Unknown action: {action}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        error_message = f"WebSocket handler error: {e}"
        logger.error(f"💥 {error_message}")
        try:
            if lanlan_name in session_manager:
                await session_manager[lanlan_name].send_status(f"Server error: {e}")
        except:
            pass
    finally:
        logger.info(f"Cleaning up WebSocket resources: {websocket.client}")
        # 安全检查：如果角色已被重命名或删除，lanlan_name 可能不再存在
        if lanlan_name in session_manager:
            await session_manager[lanlan_name].cleanup()
            # 注意：cleanup() 会清空 websocket，但只在连接真正断开时调用
            # 如果连接还在，websocket应该保持设置
            if session_manager[lanlan_name].websocket == websocket:
                session_manager[lanlan_name].websocket = None

@app.post('/api/notify_task_result')
async def notify_task_result(request: Request):
    """供工具/任务服务回调：在下一次正常回复之后，插入一条任务完成提示。"""
    try:
        data = await request.json()
        # 如果未显式提供，则使用当前默认角色
        _, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        lanlan = data.get('lanlan_name') or her_name_current
        text = (data.get('text') or '').strip()
        if not text:
            return JSONResponse({"success": False, "error": "text required"}, status_code=400)
        mgr = session_manager.get(lanlan)
        if not mgr:
            return JSONResponse({"success": False, "error": "lanlan not found"}, status_code=404)
        # 将提示加入待插入队列
        mgr.pending_extra_replies.append(text)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post('/api/proactive_chat')
async def proactive_chat(request: Request):
    """主动搭话：爬取热门内容，让AI决定是否主动发起对话"""
    try:
        from utils.web_scraper import fetch_trending_content, format_trending_content
        
        # 获取当前角色数据
        master_name_current, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        
        data = await request.json()
        lanlan_name = data.get('lanlan_name') or her_name_current
        
        # 获取session manager
        mgr = session_manager.get(lanlan_name)
        if not mgr:
            return JSONResponse({"success": False, "error": f"角色 {lanlan_name} 不存在"}, status_code=404)
        
        # 检查是否正在响应中（如果正在说话，不打断）
        if mgr.is_active and hasattr(mgr.session, '_is_responding') and mgr.session._is_responding:
            return JSONResponse({
                "success": False, 
                "error": "AI正在响应中，无法主动搭话",
                "message": "请等待当前响应完成"
            }, status_code=409)
        
        logger.info(f"[{lanlan_name}] 开始主动搭话流程...")
        
        # 1. 爬取热门内容
        try:
            trending_content = await fetch_trending_content(bilibili_limit=10, weibo_limit=10)
            
            if not trending_content['success']:
                return JSONResponse({
                    "success": False,
                    "error": "无法获取热门内容",
                    "detail": trending_content.get('error', '未知错误')
                }, status_code=500)
            
            formatted_content = format_trending_content(trending_content)
            logger.info(f"[{lanlan_name}] 成功获取热门内容")
            
        except Exception as e:
            logger.error(f"[{lanlan_name}] 获取热门内容失败: {e}")
            return JSONResponse({
                "success": False,
                "error": "爬取热门内容时出错",
                "detail": str(e)
            }, status_code=500)
        
        # 2. 获取new_dialogue prompt
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{MEMORY_SERVER_PORT}/new_dialog/{lanlan_name}", timeout=5.0)
                memory_context = resp.text
        except Exception as e:
            logger.warning(f"[{lanlan_name}] 获取记忆上下文失败，使用空上下文: {e}")
            memory_context = ""
        
        # 3. 构造提示词（使用prompts_sys中的模板）
        system_prompt = proactive_chat_prompt.format(
            lanlan_name=lanlan_name,
            master_name=master_name_current,
            trending_content=formatted_content,
            memory_context=memory_context
        )

        # 4. 直接使用langchain ChatOpenAI获取AI回复（不创建临时session）
        try:
            core_config = _config_manager.get_core_config()
            
            # 直接使用langchain ChatOpenAI发送请求
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage
            from openai import APIConnectionError, InternalServerError, RateLimitError
            
            llm = ChatOpenAI(
                model=core_config['CORRECTION_MODEL'],
                base_url=core_config['OPENROUTER_URL'],
                api_key=core_config['OPENROUTER_API_KEY'],
                temperature=1.1,
                streaming=False  # 不需要流式，直接获取完整响应
            )
            
            # 发送请求获取AI决策 - Retry策略：重试2次，间隔1秒、2秒
            print(system_prompt)
            max_retries = 3
            retry_delays = [1, 2]
            response_text = ""
            
            for attempt in range(max_retries):
                try:
                    response = await asyncio.wait_for(
                        llm.ainvoke([SystemMessage(content=system_prompt)]),
                        timeout=10.0
                    )
                    response_text = response.content.strip()
                    break  # 成功则退出重试循环
                except (APIConnectionError, InternalServerError, RateLimitError) as e:
                    if attempt < max_retries - 1:
                        wait_time = retry_delays[attempt]
                        logger.warning(f"[{lanlan_name}] 主动搭话LLM调用失败 (尝试 {attempt + 1}/{max_retries})，{wait_time}秒后重试: {e}")
                        # 向前端发送状态提示
                        if mgr.websocket:
                            try:
                                await mgr.send_status(f"正在重试中...（第{attempt + 1}次）")
                            except:
                                pass
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"[{lanlan_name}] 主动搭话LLM调用失败，已达到最大重试次数: {e}")
                        return JSONResponse({
                            "success": False,
                            "error": f"AI调用失败，已重试{max_retries}次",
                            "detail": str(e)
                        }, status_code=503)
            
            logger.info(f"[{lanlan_name}] AI决策结果: {response_text[:100]}...")
            
            # 5. 判断AI是否选择搭话
            if "[PASS]" in response_text or not response_text:
                return JSONResponse({
                    "success": True,
                    "action": "pass",
                    "message": "AI选择暂时不搭话"
                })
            
            # 6. AI选择搭话，需要通过session manager处理
            # 首先检查是否有真实的websocket连接
            if not mgr.websocket:
                return JSONResponse({
                    "success": False,
                    "error": "没有活跃的WebSocket连接，无法主动搭话。请先打开前端页面。"
                }, status_code=400)
            
            # 检查websocket是否连接
            try:
                from starlette.websockets import WebSocketState
                if hasattr(mgr.websocket, 'client_state'):
                    if mgr.websocket.client_state != WebSocketState.CONNECTED:
                        return JSONResponse({
                            "success": False,
                            "error": "WebSocket未连接，无法主动搭话"
                        }, status_code=400)
            except Exception as e:
                logger.warning(f"检查WebSocket状态失败: {e}")
            
            # 检查是否有现有的session，如果没有则创建一个文本session
            session_created = False
            if not mgr.session or not hasattr(mgr.session, '_conversation_history'):
                logger.info(f"[{lanlan_name}] 没有活跃session，创建文本session用于主动搭话")
                # 使用现有的真实websocket启动session
                await mgr.start_session(mgr.websocket, new=True, input_mode='text')
                session_created = True
                logger.info(f"[{lanlan_name}] 文本session已创建")
            
            # 如果是新创建的session，等待TTS准备好
            if session_created and mgr.use_tts:
                logger.info(f"[{lanlan_name}] 等待TTS准备...")
                max_wait = 5  # 最多等待5秒
                wait_step = 0.1
                waited = 0
                while waited < max_wait:
                    async with mgr.tts_cache_lock:
                        if mgr.tts_ready:
                            logger.info(f"[{lanlan_name}] TTS已准备好")
                            break
                    await asyncio.sleep(wait_step)
                    waited += wait_step
                
                if waited >= max_wait:
                    logger.warning(f"[{lanlan_name}] TTS准备超时，继续发送（可能没有语音）")
            
            # 现在可以将AI的话添加到对话历史中
            from langchain_core.messages import AIMessage
            mgr.session._conversation_history.append(AIMessage(content=response_text))
            logger.info(f"[{lanlan_name}] 已将主动搭话添加到对话历史")
            
            # 生成新的speech_id（用于TTS）
            from uuid import uuid4
            async with mgr.lock:
                mgr.current_speech_id = str(uuid4())
            
            # 通过handle_text_data处理这段话（触发TTS和前端显示）
            # 分chunk发送以模拟流式效果
            chunks = [response_text[i:i+10] for i in range(0, len(response_text), 10)]
            for i, chunk in enumerate(chunks):
                await mgr.handle_text_data(chunk, is_first_chunk=(i == 0))
                await asyncio.sleep(0.05)  # 小延迟模拟流式
            
            # 调用response完成回调
            if hasattr(mgr, 'handle_response_complete'):
                await mgr.handle_response_complete()
            
            return JSONResponse({
                "success": True,
                "action": "chat",
                "message": "主动搭话已发送",
                "lanlan_name": lanlan_name
            })
            
        except asyncio.TimeoutError:
            logger.error(f"[{lanlan_name}] AI回复超时")
            return JSONResponse({
                "success": False,
                "error": "AI处理超时"
            }, status_code=504)
        except Exception as e:
            logger.error(f"[{lanlan_name}] AI处理失败: {e}")
            return JSONResponse({
                "success": False,
                "error": "AI处理失败",
                "detail": str(e)
            }, status_code=500)
        
    except Exception as e:
        logger.error(f"主动搭话接口异常: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "detail": str(e)
        }, status_code=500)

@app.get("/l2d", response_class=HTMLResponse)
async def get_l2d_manager(request: Request):
    """渲染Live2D模型管理器页面"""
    return templates.TemplateResponse("templates/l2d_manager.html", {
        "request": request
    })

@app.get("/live2d_parameter_editor", response_class=HTMLResponse)
async def live2d_parameter_editor(request: Request):
    """Live2D参数编辑器页面"""
    return templates.TemplateResponse("templates/live2d_parameter_editor.html", {
        "request": request
    })

@app.get('/api/characters/current_live2d_model')
async def get_current_live2d_model(catgirl_name: str = "", item_id: str = ""):
    """获取指定角色或当前角色的Live2D模型信息
    
    Args:
        catgirl_name: 角色名称
        item_id: 可选的物品ID，用于直接指定模型
    """
    try:
        characters = _config_manager.load_characters()
        
        # 如果没有指定角色名称，使用当前猫娘
        if not catgirl_name:
            catgirl_name = characters.get('当前猫娘', '')
        
        # 查找指定角色的Live2D模型
        live2d_model_name = None
        model_info = None
        
        # 首先尝试通过item_id查找模型
        if item_id:
            try:
                logger.debug(f"尝试通过item_id {item_id} 查找模型")
                # 获取所有模型
                all_models = find_models()
                # 查找匹配item_id的模型
                matching_model = next((m for m in all_models if m.get('item_id') == item_id), None)
                
                if matching_model:
                    logger.debug(f"通过item_id找到模型: {matching_model['name']}")
                    # 复制模型信息
                    model_info = matching_model.copy()
                    live2d_model_name = model_info['name']
            except Exception as e:
                logger.warning(f"通过item_id查找模型失败: {e}")
        
        # 如果没有通过item_id找到模型，再通过角色名称查找
        if not model_info and catgirl_name:
            # 在猫娘列表中查找
            if '猫娘' in characters and catgirl_name in characters['猫娘']:
                catgirl_data = characters['猫娘'][catgirl_name]
                live2d_model_name = catgirl_data.get('live2d')
                
                # 检查是否有保存的item_id
                saved_item_id = catgirl_data.get('live2d_item_id')
                if saved_item_id:
                    logger.debug(f"发现角色 {catgirl_name} 保存的item_id: {saved_item_id}")
                    try:
                        # 尝试通过保存的item_id查找模型
                        all_models = find_models()
                        matching_model = next((m for m in all_models if m.get('item_id') == saved_item_id), None)
                        if matching_model:
                            logger.debug(f"通过保存的item_id找到模型: {matching_model['name']}")
                            model_info = matching_model.copy()
                            live2d_model_name = model_info['name']
                    except Exception as e:
                        logger.warning(f"通过保存的item_id查找模型失败: {e}")
        
        # 如果找到了模型名称，获取模型信息
        if live2d_model_name:
            try:
                # 先从完整的模型列表中查找，这样可以获取到item_id等完整信息
                all_models = find_models()
                # 查找匹配的模型
                matching_model = next((m for m in all_models if m['name'] == live2d_model_name), None)
                
                if matching_model:
                    # 使用完整的模型信息，包含item_id
                    model_info = matching_model.copy()
                    logger.debug(f"从完整模型列表获取模型信息: {model_info}")
                else:
                    # 如果在完整列表中找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory(live2d_model_name)
                    if os.path.exists(model_dir):
                        # 查找模型配置文件
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            
                            # 使用保存的item_id构建model_path
                            # 从之前的逻辑中获取saved_item_id
                            saved_item_id = catgirl_data.get('live2d_item_id', '') if 'catgirl_data' in locals() else ''
                            
                            # 如果有保存的item_id，使用它构建路径
                            if saved_item_id:
                                model_path = f'{url_prefix}/{saved_item_id}/{model_file}'
                                logger.debug(f"使用保存的item_id构建模型路径: {model_path}")
                            else:
                                # 原始路径构建逻辑
                                model_path = f'{url_prefix}/{live2d_model_name}/{model_file}'
                                logger.debug(f"使用模型名称构建路径: {model_path}")
                            
                            model_info = {
                                'name': live2d_model_name,
                                'item_id': saved_item_id,
                                'path': model_path
                            }
            except Exception as e:
                logger.warning(f"获取模型信息失败: {e}")
        
        # 回退机制：如果没有找到模型，使用默认的mao_pro
        if not live2d_model_name or not model_info:
            logger.info(f"猫娘 {catgirl_name} 未设置Live2D模型，回退到默认模型 mao_pro")
            live2d_model_name = 'mao_pro'
            try:
                # 先从完整的模型列表中查找mao_pro
                all_models = find_models()
                matching_model = next((m for m in all_models if m['name'] == 'mao_pro'), None)
                
                if matching_model:
                    model_info = matching_model.copy()
                    model_info['is_fallback'] = True
                else:
                    # 如果找不到，回退到原来的逻辑
                    model_dir, url_prefix = find_model_directory('mao_pro')
                    if os.path.exists(model_dir):
                        model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                        if model_files:
                            model_file = model_files[0]
                            model_path = f'{url_prefix}/mao_pro/{model_file}'
                            model_info = {
                                'name': 'mao_pro',
                                'path': model_path,
                                'is_fallback': True  # 标记这是回退模型
                            }
            except Exception as e:
                logger.error(f"获取默认模型mao_pro失败: {e}")
        
        return JSONResponse(content={
            'success': True,
            'catgirl_name': catgirl_name,
            'model_name': live2d_model_name,
            'model_info': model_info
        })
        
    except Exception as e:
        logger.error(f"获取角色Live2D模型失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })

@app.get('/chara_manager', response_class=HTMLResponse)
async def chara_manager(request: Request):
    """渲染主控制页面"""
    return templates.TemplateResponse('templates/chara_manager.html', {"request": request})

@app.get('/voice_clone', response_class=HTMLResponse)
async def voice_clone_page(request: Request):
    return templates.TemplateResponse("templates/voice_clone.html", {"request": request})

@app.get("/api_key", response_class=HTMLResponse)
async def api_key_settings(request: Request):
    """API Key 设置页面"""
    return templates.TemplateResponse("templates/api_key_settings.html", {
        "request": request
    })

@app.get('/api/characters')
async def get_characters():
    return JSONResponse(content=_config_manager.load_characters())

@app.get('/steam_workshop_manager', response_class=HTMLResponse)
async def steam_workshop_manager_page(request: Request, lanlan_name: str = ""):
    return templates.TemplateResponse("templates/steam_workshop_manager.html", {"request": request, "lanlan_name": lanlan_name})

@app.get('/api/steam/workshop/subscribed-items')
async def get_subscribed_workshop_items():
    """
    获取用户订阅的Steam创意工坊物品列表
    返回包含物品ID、基本信息和状态的JSON数据
    """
    global steamworks
    
    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        # 获取订阅物品数量
        num_subscribed_items = steamworks.Workshop.GetNumSubscribedItems()
        logger.info(f"获取到 {num_subscribed_items} 个订阅的创意工坊物品")
        
        # 如果没有订阅物品，返回空列表
        if num_subscribed_items == 0:
            return {
                "success": True,
                "items": [],
                "total": 0
            }
        
        # 获取订阅物品ID列表
        subscribed_items = steamworks.Workshop.GetSubscribedItems()
        logger.info(f'获取到 {len(subscribed_items)} 个订阅的创意工坊物品')
        
        # 存储处理后的物品信息
        items_info = []
        
        # 为每个物品获取基本信息和状态
        for item_id in subscribed_items:
            try:
                # 确保item_id是整数类型
                if isinstance(item_id, str):
                    try:
                        item_id = int(item_id)
                    except ValueError:
                        logger.error(f"无效的物品ID: {item_id}")
                        continue
                
                logger.info(f'正在处理物品ID: {item_id}')
                
                # 获取物品状态
                item_state = steamworks.Workshop.GetItemState(item_id)
                logger.debug(f'物品 {item_id} 状态: {item_state}')
                
                # 初始化基本物品信息（确保所有字段都有默认值）
                # 确保publishedFileId始终为字符串类型，避免前端toString()错误
                item_info = {
                    "publishedFileId": str(item_id),
                    "title": f"未知物品_{item_id}",
                    "description": "无法获取详细描述",
                    "tags": [],
                    "state": {
                        "subscribed": bool(item_state & 1),  # EItemState.SUBSCRIBED
                        "legacyItem": bool(item_state & 2),
                        "installed": False,
                        "needsUpdate": bool(item_state & 8),  # EItemState.NEEDS_UPDATE
                        "downloading": False,
                        "downloadPending": bool(item_state & 32),  # EItemState.DOWNLOAD_PENDING
                        "isWorkshopItem": bool(item_state & 128)  # EItemState.IS_WORKSHOP_ITEM
                    },
                    "installedFolder": None,
                    "fileSizeOnDisk": 0,
                    "downloadProgress": {
                        "bytesDownloaded": 0,
                        "bytesTotal": 0,
                        "percentage": 0
                    },
                    # 添加额外的时间戳信息 - 使用datetime替代time模块避免命名冲突
                    "timeAdded": int(datetime.now().timestamp()),
                    "timeUpdated": int(datetime.now().timestamp())
                }
                
                # 尝试获取物品安装信息（如果已安装）
                try:
                    logger.debug(f'获取物品 {item_id} 的安装信息')
                    result = steamworks.Workshop.GetItemInstallInfo(item_id)
                    
                    # 检查返回值的结构 - 支持字典格式（根据日志显示）
                    if isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 安装信息字典: {result}')
                        
                        # 从字典中提取信息
                        item_info["state"]["installed"] = True  # 如果返回字典，假设已安装
                        # 获取安装路径 - workshop.py中已经将folder解码为字符串
                        folder_path = result.get('folder', '')
                        item_info["installedFolder"] = str(folder_path) if folder_path else None
                        logger.debug(f'物品 {item_id} 的安装路径: {item_info["installedFolder"]}')
                        
                        # 处理磁盘大小 - GetItemInstallInfo返回的disk_size是普通整数
                        disk_size = result.get('disk_size', 0)
                        item_info["fileSizeOnDisk"] = int(disk_size) if isinstance(disk_size, (int, float)) else 0
                    # 也支持元组格式作为备选
                    elif isinstance(result, tuple) and len(result) >= 3:
                        installed, folder, size = result
                        logger.debug(f'物品 {item_id} 安装状态: 已安装={installed}, 路径={folder}, 大小={size}')
                        
                        # 安全的类型转换
                        item_info["state"]["installed"] = bool(installed)
                        item_info["installedFolder"] = str(folder) if folder and isinstance(folder, (str, bytes)) else None
                        
                        # 处理大小值
                        if isinstance(size, (int, float)):
                            item_info["fileSizeOnDisk"] = int(size)
                        else:
                            item_info["fileSizeOnDisk"] = 0
                    else:
                        logger.warning(f'物品 {item_id} 的安装信息返回格式未知: {type(result)} - {result}')
                        item_info["state"]["installed"] = False
                except Exception as e:
                    logger.warning(f'获取物品 {item_id} 安装信息失败: {e}')
                    item_info["state"]["installed"] = False
                
                # 尝试获取物品下载信息（如果正在下载）
                try:
                    logger.debug(f'获取物品 {item_id} 的下载信息')
                    result = steamworks.Workshop.GetItemDownloadInfo(item_id)
                    
                    # 检查返回值的结构 - 支持字典格式（与安装信息保持一致）
                    if isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 下载信息字典: {result}')
                        
                        # 使用正确的键名获取下载信息
                        downloaded = result.get('downloaded', 0)
                        total = result.get('total', 0)
                        progress = result.get('progress', 0.0)
                        
                        # 根据total和downloaded确定是否正在下载
                        item_info["state"]["downloading"] = total > 0 and downloaded < total
                        
                        # 设置下载进度信息
                        if downloaded > 0 or total > 0:
                            item_info["downloadProgress"] = {
                                "bytesDownloaded": int(downloaded),
                                "bytesTotal": int(total),
                                "percentage": progress * 100 if isinstance(progress, (int, float)) else 0
                            }
                    # 也支持元组格式作为备选
                    elif isinstance(result, tuple) and len(result) >= 3:
                        # 元组中应该包含下载状态、已下载字节数和总字节数
                        downloaded, total, progress = result if len(result) >= 3 else (0, 0, 0.0)
                        logger.debug(f'物品 {item_id} 下载状态: 已下载={downloaded}, 总计={total}, 进度={progress}')
                        
                        # 根据total和downloaded确定是否正在下载
                        item_info["state"]["downloading"] = total > 0 and downloaded < total
                        
                        # 设置下载进度信息
                        if downloaded > 0 or total > 0:
                            # 处理可能的类型转换
                            try:
                                downloaded_value = int(downloaded.value) if hasattr(downloaded, 'value') else int(downloaded)
                                total_value = int(total.value) if hasattr(total, 'value') else int(total)
                                progress_value = float(progress.value) if hasattr(progress, 'value') else float(progress)
                            except:
                                downloaded_value, total_value, progress_value = 0, 0, 0.0
                                
                            item_info["downloadProgress"] = {
                                "bytesDownloaded": downloaded_value,
                                "bytesTotal": total_value,
                                "percentage": progress_value * 100
                            }
                    else:
                        logger.warning(f'物品 {item_id} 的下载信息返回格式未知: {type(result)} - {result}')
                        item_info["state"]["downloading"] = False
                except Exception as e:
                    logger.warning(f'获取物品 {item_id} 下载信息失败: {e}')
                    item_info["state"]["downloading"] = False
                
                # 尝试获取物品详细信息（标题、描述等）- 使用官方推荐的方式
                try:
                    # 使用官方推荐的CreateQueryUGCDetailsRequest和SendQueryUGCRequest方法
                    logger.debug(f'使用官方推荐方法获取物品 {item_id} 的详细信息')
                    
                    # 创建UGC详情查询请求
                    query_handle = steamworks.Workshop.CreateQueryUGCDetailsRequest([item_id])
                    
                    if query_handle:
                        # 设置回调函数
                        details_received = False
                        
                        def query_completed_callback(result):
                            nonlocal details_received
                            details_received = True
                            # 回调结果会在主线程中通过GetQueryUGCResult获取
                            pass
                        
                        # 设置回调
                        steamworks.Workshop.SetQueryUGCRequestCallback(query_completed_callback)
                        
                        # 发送查询请求
                        steamworks.Workshop.SendQueryUGCRequest(query_handle)
                        
                        # 等待查询完成（简单的轮询方式）
                        import time
                        timeout = 2  # 2秒超时
                        start_time = time.time()
                        
                        # 由于这是异步回调，我们简单地等待一小段时间让查询有机会完成
                        time.sleep(0.5)  # 等待0.5秒
                        
                        try:
                            # 尝试获取查询结果
                            result = steamworks.Workshop.GetQueryUGCResult(query_handle, 0)
                            if result:
                                # 从结果中提取信息
                                if hasattr(result, 'title') and result.title:
                                    item_info['title'] = result.title.decode('utf-8', errors='replace')
                                if hasattr(result, 'description') and result.description:
                                    item_info['description'] = result.description.decode('utf-8', errors='replace')
                                # 获取创建和更新时间
                                if hasattr(result, 'timeCreated'):
                                    item_info['timeAdded'] = int(result.timeCreated)
                                if hasattr(result, 'timeUpdated'):
                                    item_info['timeUpdated'] = int(result.timeUpdated)
                                # 获取作者信息
                                if hasattr(result, 'steamIDOwner'):
                                    item_info['steamIDOwner'] = str(result.steamIDOwner)
                                # 获取文件大小信息
                                if hasattr(result, 'fileSize'):
                                    item_info['fileSizeOnDisk'] = int(result.fileSize)
                                
                                logger.info(f"成功获取物品 {item_id} 的详情信息")
                        except Exception as query_error:
                            logger.warning(f"获取查询结果时出错: {query_error}")
                except Exception as api_error:
                    logger.warning(f"使用官方API获取物品 {item_id} 详情时出错: {api_error}")
                
                # 作为备选方案，如果本地有安装路径，尝试从本地文件获取信息
                if item_info['title'].startswith('未知物品_') or not item_info['description']:
                    install_folder = item_info.get('installedFolder')
                    if install_folder and os.path.exists(install_folder):
                        logger.debug(f'尝试从安装文件夹获取物品信息: {install_folder}')
                        # 查找可能的配置文件来获取更多信息
                        config_files = [
                            os.path.join(install_folder, "config.json"),
                            os.path.join(install_folder, "package.json"),
                            os.path.join(install_folder, "info.json"),
                            os.path.join(install_folder, "manifest.json"),
                            os.path.join(install_folder, "README.md"),
                            os.path.join(install_folder, "README.txt")
                        ]
                        
                        for config_path in config_files:
                            if os.path.exists(config_path):
                                try:
                                    with open(config_path, 'r', encoding='utf-8') as f:
                                        if config_path.endswith('.json'):
                                            config_data = json.load(f)
                                            # 尝试从配置文件中提取标题和描述
                                            if "title" in config_data and config_data["title"]:
                                                item_info["title"] = config_data["title"]
                                            elif "name" in config_data and config_data["name"]:
                                                item_info["title"] = config_data["name"]
                                            
                                            if "description" in config_data and config_data["description"]:
                                                item_info["description"] = config_data["description"]
                                        else:
                                            # 对于文本文件，将第一行作为标题
                                            first_line = f.readline().strip()
                                            if first_line and item_info['title'].startswith('未知物品_'):
                                                item_info['title'] = first_line[:100]  # 限制长度
                                    logger.info(f"从本地文件 {os.path.basename(config_path)} 成功获取物品 {item_id} 的信息")
                                    break
                                except Exception as file_error:
                                    logger.warning(f"读取配置文件 {config_path} 时出错: {file_error}")
                # 移除了没有对应try块的except语句
                
                # 确保publishedFileId是字符串类型
                item_info['publishedFileId'] = str(item_info['publishedFileId'])
                
                # 尝试获取预览图信息 - 优先从本地文件夹查找
                preview_url = None
                install_folder = item_info.get('installedFolder')
                logger.info(f'物品 {item_id} 安装文件夹: {install_folder}')
                if install_folder and os.path.exists(install_folder):
                    try:
                        # 使用辅助函数查找预览图
                        preview_image_path = find_preview_image_in_folder(install_folder)
                        logger.info(f'物品 {item_id} 查找到的预览图路径: {preview_image_path}')
                        if preview_image_path:
                            # 为前端提供代理访问的路径格式
                            # 需要将路径标准化，确保可以通过proxy-image API访问
                            if os.name == 'nt':
                                # Windows路径处理
                                proxy_path = preview_image_path.replace('\\', '/')
                            else:
                                proxy_path = preview_image_path
                            preview_url = f"/api/proxy-image?image_path={quote(proxy_path)}"
                            logger.info(f'为物品 {item_id} 生成预览图URL: {preview_url}')
                        else:
                            logger.warning(f'物品 {item_id} 在文件夹 {install_folder} 中未找到预览图')
                    except Exception as preview_error:
                        logger.warning(f'查找物品 {item_id} 预览图时出错: {preview_error}')
                else:
                    logger.warning(f'物品 {item_id} 没有安装文件夹或文件夹不存在: {install_folder}')
                
                # 添加预览图URL到物品信息
                if preview_url:
                    item_info['previewUrl'] = preview_url
                    logger.info(f'物品 {item_id} 已设置 previewUrl: {preview_url}')
                else:
                    logger.warning(f'物品 {item_id} 没有找到预览图，previewUrl 未设置')
                
                # 添加物品信息到结果列表
                items_info.append(item_info)
                logger.info(f'物品 {item_id} 信息已添加到结果列表: {item_info["title"]}, previewUrl={item_info.get("previewUrl", "未设置")}')
                
            except Exception as item_error:
                logger.error(f"获取物品 {item_id} 信息时出错: {item_error}")
                # 即使出错，也添加一个最基本的物品信息到列表中
                try:
                    basic_item_info = {
                        "publishedFileId": str(item_id),  # 确保是字符串类型
                        "title": f"未知物品_{item_id}",
                        "description": "无法获取详细信息",
                        "state": {
                            "subscribed": True,
                            "installed": False,
                            "downloading": False,
                            "needsUpdate": False,
                            "error": True
                        },
                        "error_message": str(item_error)
                    }
                    items_info.append(basic_item_info)
                    logger.info(f'已添加物品 {item_id} 的基本信息到结果列表')
                except Exception as basic_error:
                    logger.error(f"添加基本物品信息也失败了: {basic_error}")
                # 继续处理下一个物品
                continue
        
        return {
            "success": True,
            "items": items_info,
            "total": len(items_info)
        }
        
    except Exception as e:
        logger.error(f"获取订阅物品列表时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": f"获取订阅物品失败: {str(e)}"
        }, status_code=500)

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
        workshop_items_result = get_subscribed_workshop_items()
        
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
@app.post('/api/steam/workshop/local-items/scan')
async def scan_local_workshop_items(request: Request):
    try:
        logger.info('接收到扫描本地创意工坊物品的API请求')
        
        # 始终使用 live2d_dir 作为扫描目录（忽略workshop配置）
        live2d_folder = str(_config_manager.live2d_dir)
        logger.info(f'使用 live2d 目录进行扫描: {live2d_folder}')
        
        # 确保目录存在
        if not os.path.exists(live2d_folder):
            os.makedirs(live2d_folder, exist_ok=True)
            logger.info(f'创建 live2d 目录: {live2d_folder}')
        
        if not os.path.isdir(live2d_folder):
            logger.warning(f'指定的路径不是文件夹: {live2d_folder}')
            return JSONResponse(content={"success": False, "error": f"指定的路径不是文件夹: {live2d_folder}"}, status_code=400)
        
        # 扫描本地创意工坊物品
        local_items = []
        published_items = []
        item_id = 1
        
        # 遍历文件夹，扫描所有子文件夹
        for item_folder in os.listdir(live2d_folder):
            item_path = os.path.join(live2d_folder, item_folder)
            if os.path.isdir(item_path):
                stat_info = os.stat(item_path)
                
                # 处理预览图路径（如果有）
                preview_image = find_preview_image_in_folder(item_path)
                
                local_items.append({
                    "id": f"local_{item_id}",
                    "name": item_folder,
                    "path": item_path,  # 返回绝对路径
                    "lastModified": stat_info.st_mtime,
                    "size": get_folder_size(item_path),
                    "tags": ["本地文件"],
                    "previewImage": preview_image  # 返回绝对路径
                })
                item_id += 1
        
        logger.info(f"扫描完成，找到 {len(local_items)} 个本地创意工坊物品")
        
        return JSONResponse(content={
            "success": True,
            "local_items": local_items,
            "published_items": published_items,
            "folder_path": live2d_folder
        })
        
    except Exception as e:
        logger.error(f"扫描本地创意工坊物品失败: {e}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

# 获取创意工坊配置
@app.get('/api/steam/workshop/config')
async def get_workshop_config():
    try:
        from utils.workshop_utils import load_workshop_config
        workshop_config_data = load_workshop_config()
        return {"success": True, "config": workshop_config_data}
    except Exception as e:
        logger.error(f"获取创意工坊配置失败: {str(e)}")
        return {"success": False, "error": str(e)}

# 保存创意工坊配置
@app.post('/api/steam/workshop/config')
async def save_workshop_config_api(config_data: dict):
    try:
        # 导入与get_workshop_config相同路径的函数，保持一致性
        from utils.workshop_utils import load_workshop_config, save_workshop_config, ensure_workshop_folder_exists
        
        # 先加载现有配置，避免使用全局变量导致的不一致问题
        workshop_config_data = load_workshop_config() or {}
        
        # 更新配置
        if 'default_workshop_folder' in config_data:
            workshop_config_data['default_workshop_folder'] = config_data['default_workshop_folder']
        if 'auto_create_folder' in config_data:
            workshop_config_data['auto_create_folder'] = config_data['auto_create_folder']
        # 支持用户mod路径配置
        if 'user_mod_folder' in config_data:
            workshop_config_data['user_mod_folder'] = config_data['user_mod_folder']
        
        # 保存配置到文件，传递完整的配置数据作为参数
        save_workshop_config(workshop_config_data)
        
        # 如果启用了自动创建文件夹且提供了路径，则确保文件夹存在
        if workshop_config_data.get('auto_create_folder', True):
            # 优先使用user_mod_folder，如果没有则使用default_workshop_folder
            folder_path = workshop_config_data.get('user_mod_folder') or workshop_config_data.get('default_workshop_folder')
            if folder_path:
                ensure_workshop_folder_exists(folder_path)
        
        return {"success": True, "config": workshop_config_data}
    except Exception as e:
        logger.error(f"保存创意工坊配置失败: {str(e)}")
        return {"success": False, "error": str(e)}

@app.get('/api/proxy-image')
async def proxy_image(image_path: str):
    """代理访问本地图片文件，支持绝对路径和相对路径，特别是Steam创意工坊目录"""

    try:
        logger.info(f"代理图片请求，原始路径: {image_path}")
        
        # 解码URL编码的路径（处理双重编码情况）
        decoded_path = unquote(image_path)
        # 再次解码以处理可能的双重编码
        decoded_path = unquote(decoded_path)
        
        logger.info(f"解码后的路径: {decoded_path}")
        
        # 检查是否是远程URL，如果是则直接返回错误（目前只支持本地文件）
        if decoded_path.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "暂不支持远程图片URL"}, status_code=400)   

        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 替换可能的斜杠为反斜杠，确保Windows路径格式正确
            decoded_path = decoded_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_path.startswith('\\\\'):
                decoded_path = decoded_path[2:]  # 移除多余的反斜杠前缀
        
        # 尝试解析路径
        final_path = None
        
        # 尝试作为绝对路径
        if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
            final_path = os.path.realpath(decoded_path)
        
        # 尝试备选路径格式
        if final_path is None:
            alt_path = decoded_path.replace('\\', '/')
            if os.path.exists(alt_path) and os.path.isfile(alt_path):
                final_path = os.path.realpath(alt_path)
            
        # 尝试相对于默认创意工坊目录的路径处理
        if final_path is None:
            try:
                workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
                
                # 尝试将解码路径作为相对于创意工坊目录的路径
                rel_workshop_path = os.path.join(workshop_base_dir, decoded_path)
                rel_workshop_path = os.path.normpath(rel_workshop_path)
                
                logger.info(f"尝试相对于创意工坊目录的路径: {rel_workshop_path}")
                
                if os.path.exists(rel_workshop_path) and os.path.isfile(rel_workshop_path):
                    real_path = os.path.realpath(rel_workshop_path)
                    # 确保路径在允许的目录内
                    if real_path.startswith(workshop_base_dir):
                        final_path = real_path
                        logger.info(f"找到相对于创意工坊目录的图片: {final_path}")
            except Exception as e:
                logger.warning(f"处理相对于创意工坊目录的路径失败: {str(e)}")
        
        # 如果仍未找到有效路径，返回错误
        if final_path is None:
            return JSONResponse(content={"success": False, "error": f"文件不存在或无访问权限: {decoded_path}"}, status_code=404)
        
        # 检查文件扩展名是否为图片
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.ico']
        if os.path.splitext(final_path)[1].lower() not in image_extensions:
            return JSONResponse(content={"success": False, "error": "不是有效的图片文件"}, status_code=400)
        
        # 读取图片文件
        with open(final_path, 'rb') as f:
            image_data = f.read()
        
        # 根据文件扩展名设置MIME类型
        ext = os.path.splitext(final_path)[1].lower()
        mime_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
            '.ico': 'image/x-icon'
        }.get(ext, 'application/octet-stream')
        
        # 返回图片数据
        return Response(content=image_data, media_type=mime_type)
    except Exception as e:
        logger.error(f"代理图片访问失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"访问图片失败: {str(e)}"}, status_code=500)


@app.get('/api/steam/workshop/local-items/{item_id}')
async def get_local_workshop_item(item_id: str, folder_path: str = None):
    try:
        # 这个接口需要从缓存或临时存储中获取物品信息
        # 这里简化实现，实际应用中应该有更完善的缓存机制
        # folder_path 已经通过函数参数获取
        
        if not folder_path:
            return JSONResponse(content={"success": False, "error": "未提供文件夹路径"}, status_code=400)
        
        # 安全检查：始终使用get_workshop_path()作为基础目录
        base_workshop_folder = os.path.abspath(os.path.normpath(get_workshop_path()))
        
        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 解码并处理Windows路径
            decoded_folder_path = unquote(folder_path)
            # 替换斜杠为反斜杠，确保Windows路径格式正确
            decoded_folder_path = decoded_folder_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_folder_path.startswith('\\\\'):
                decoded_folder_path = decoded_folder_path[2:]  # 移除多余的反斜杠前缀
        else:
            decoded_folder_path = unquote(folder_path)
                    
        folder_path = decoded_folder_path
        logger.info(f'处理后的完整路径: {folder_path}')
        
        # 解析本地ID
        if item_id.startswith('local_'):
            index = int(item_id.split('_')[1])
            
            try:
                # 检查folder_path是否已经是项目文件夹路径
                if os.path.isdir(folder_path):
                    # 情况1：folder_path直接指向项目文件夹
                    stat_info = os.stat(folder_path)
                    item_name = os.path.basename(folder_path)
                    
                    item = {
                        "id": item_id,
                        "name": item_name,
                        "path": folder_path,
                        "lastModified": stat_info.st_mtime,
                        "size": get_folder_size(folder_path),
                        "tags": ["模组"],
                        "previewImage": find_preview_image_in_folder(folder_path)
                    }
                    
                    return JSONResponse(content={"success": True, "item": item})
                else:
                    # 情况2：尝试原始逻辑，从folder_path中查找第index个子文件夹
                    items = []
                    for i, item_folder in enumerate(os.listdir(folder_path)):
                        item_path = os.path.join(folder_path, item_folder)
                        if os.path.isdir(item_path) and i + 1 == index:
                            stat_info = os.stat(item_path)
                            items.append({
                                "id": f"local_{i + 1}",
                                "name": item_folder,
                                "path": item_path,
                                "lastModified": stat_info.st_mtime,
                                "size": get_folder_size(item_path),
                                "tags": ["模组"],
                                "previewImage": find_preview_image_in_folder(item_path)
                            })
                            break
                    
                    if items:
                        return JSONResponse(content={"success": True, "item": items[0]})
                    else:
                        return JSONResponse(content={"success": False, "error": "物品不存在"}, status_code=404)
            except Exception as e:
                logger.error(f"处理本地物品路径时出错: {e}")
                return JSONResponse(content={"success": False, "error": f"路径处理错误: {str(e)}"}, status_code=500)
        
        return JSONResponse(content={"success": False, "error": "无效的物品ID格式"}, status_code=400)
        
    except Exception as e:
        logger.error(f"获取本地创意工坊物品失败: {e}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

@app.get('/api/steam/workshop/check-upload-status')
async def check_upload_status(item_path: str = None):
    try:
        # 验证路径参数
        if not item_path:
            return JSONResponse(content={
                "success": False,
                "error": "未提供物品文件夹路径"
            }, status_code=400)
        
        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 解码并处理Windows路径
            decoded_item_path = unquote(item_path)
            # 替换斜杠为反斜杠，确保Windows路径格式正确
            decoded_item_path = decoded_item_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_item_path.startswith('\\\\'):
                decoded_item_path = decoded_item_path[2:]  # 移除多余的反斜杠前缀
        else:
            decoded_item_path = unquote(item_path)
        
        # 验证路径存在性
        if not os.path.exists(decoded_item_path) or not os.path.isdir(decoded_item_path):
            return JSONResponse(content={
                "success": False,
                "error": "无效的物品文件夹路径"
            }, status_code=400)
        
        # 搜索以steam_workshop_id_开头的txt文件
        import glob
        import re
        
        upload_files = glob.glob(os.path.join(decoded_item_path, "steam_workshop_id_*.txt"))
        
        # 提取第一个找到的物品ID
        published_file_id = None
        if upload_files:
            # 获取第一个文件
            first_file = upload_files[0]
            
            # 从文件名提取ID
            match = re.search(r'steam_workshop_id_(\d+)\.txt', os.path.basename(first_file))
            if match:
                published_file_id = match.group(1)
        
        # 返回检查结果
        return JSONResponse(content={
            "success": True,
            "is_published": published_file_id is not None,
            "published_file_id": published_file_id
        })
        
    except Exception as e:
        logger.error(f"检查上传状态失败: {e}")
        return JSONResponse(content={
            "success": False,
            "error": str(e),
            "message": "检查上传状态时发生错误"
        }, status_code=500)

@app.post('/api/steam/workshop/publish')
async def publish_to_workshop(request: Request):
    global steamworks
    
    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse(content={
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        data = await request.json()
        
        # 验证必要的字段
        required_fields = ['title', 'content_folder', 'visibility']
        for field in required_fields:
            if field not in data:
                return JSONResponse(content={"success": False, "error": f"缺少必要字段: {field}"}, status_code=400)
        
        # 提取数据
        title = data['title']
        content_folder = data['content_folder']
        visibility = int(data['visibility'])
        preview_image = data.get('preview_image', '')
        description = data.get('description', '')
        tags = data.get('tags', [])
        change_note = data.get('change_note', '初始发布')
        
        # 规范化路径处理 - 改进版，确保在所有情况下都能正确处理路径
        content_folder = unquote(content_folder)
        # 处理Windows路径，确保使用正确的路径分隔符
        if os.name == 'nt':
            # 将所有路径分隔符统一为反斜杠
            content_folder = content_folder.replace('/', '\\')
            # 清理可能的错误前缀
            if content_folder.startswith('\\\\'):
                content_folder = content_folder[2:]
        else:
            # 非Windows系统使用正斜杠
            content_folder = content_folder.replace('\\', '/')
        
        # 验证内容文件夹存在并是一个目录
        if not os.path.exists(content_folder):
            return JSONResponse(content={
                "success": False,
                "error": "内容文件夹不存在",
                "message": f"指定的内容文件夹不存在: {content_folder}"
            }, status_code=404)
        
        if not os.path.isdir(content_folder):
            return JSONResponse(content={
                "success": False,
                "error": "不是有效的文件夹",
                "message": f"指定的路径不是有效的文件夹: {content_folder}"
            }, status_code=400)
        
        # 增加内容文件夹检查：确保文件夹中至少有文件，验证文件夹是否包含内容
        if not any(os.scandir(content_folder)):
            return JSONResponse(content={
                "success": False,
                "error": "内容文件夹为空",
                "message": f"内容文件夹为空，请确保包含要上传的文件: {content_folder}"
            }, status_code=400)
        
        # 检查文件夹权限
        if not os.access(content_folder, os.R_OK):
            return JSONResponse(content={
                "success": False,
                "error": "没有文件夹访问权限",
                "message": f"没有读取内容文件夹的权限: {content_folder}"
            }, status_code=403)
        
        # 处理预览图片路径
        if preview_image:
            preview_image = unquote(preview_image)
            if os.name == 'nt':
                preview_image = preview_image.replace('/', '\\')
                if preview_image.startswith('\\\\'):
                    preview_image = preview_image[2:]
            else:
                preview_image = preview_image.replace('\\', '/')
            
            # 验证预览图片存在
            if not os.path.exists(preview_image):
                # 如果指定的预览图不存在，尝试在内容文件夹中查找默认预览图
                logger.warning(f'指定的预览图片不存在，尝试在内容文件夹中查找: {preview_image}')
                auto_preview = find_preview_image_in_folder(content_folder)
                if auto_preview:
                    logger.info(f'找到自动预览图片: {auto_preview}')
                    preview_image = auto_preview
                else:
                    logger.warning(f'无法找到预览图片')
                    preview_image = ''
            
            if preview_image and not os.path.isfile(preview_image):
                return JSONResponse(content={
                    "success": False,
                    "error": "预览图片无效",
                    "message": f"预览图片路径不是有效的文件: {preview_image}"
                }, status_code=400)
        else:
            # 如果未指定预览图片，尝试自动查找
            auto_preview = find_preview_image_in_folder(content_folder)
            if auto_preview:
                logger.info(f'自动找到预览图片: {auto_preview}')
                preview_image = auto_preview
        
        # 记录将要上传的内容信息
        logger.info(f"准备发布创意工坊物品: {title}")
        logger.info(f"内容文件夹: {content_folder}")
        logger.info(f"预览图片: {preview_image or '无'}")
        logger.info(f"可见性: {visibility}")
        logger.info(f"标签: {tags}")
        logger.info(f"内容文件夹包含文件数量: {len([f for f in os.listdir(content_folder) if os.path.isfile(os.path.join(content_folder, f))])}")
        logger.info(f"内容文件夹包含子文件夹数量: {len([f for f in os.listdir(content_folder) if os.path.isdir(os.path.join(content_folder, f))])}")
        
        # 使用线程池执行Steamworks API调用（因为这些是阻塞操作）
        loop = asyncio.get_event_loop()
        published_file_id = await loop.run_in_executor(
            None, 
            lambda: _publish_workshop_item(
                steamworks, title, description, content_folder, 
                preview_image, visibility, tags, change_note
            )
        )
        
        logger.info(f"成功发布创意工坊物品，ID: {published_file_id}")
        return JSONResponse(content={
            "success": True,
            "published_file_id": published_file_id,
            "message": "发布成功"
        })
        
    except ValueError as ve:
        logger.error(f"参数错误: {ve}")
        return JSONResponse(content={"success": False, "error": str(ve)}, status_code=400)
    except SteamNotLoadedException as se:
        logger.error(f"Steamworks API错误: {se}")
        return JSONResponse(content={
            "success": False,
            "error": "Steamworks API错误",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    except Exception as e:
        logger.error(f"发布到创意工坊失败: {e}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


def _format_size(size_bytes):
    """
    将字节大小格式化为人类可读的格式
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

def _publish_workshop_item(steamworks, title, description, content_folder, preview_image, visibility, tags, change_note):
    """
    在单独的线程中执行Steam创意工坊发布操作
    """
    # 在函数内部添加导入语句，确保枚举在函数作用域内可用
    from steamworks.enums import EItemUpdateStatus
    
    # 检查是否存在现有的上传标记文件，避免重复上传
    try:
        if os.path.exists(content_folder) and os.path.isdir(content_folder):
            # 查找以steam_workshop_id_开头的txt文件
            import glob
            marker_files = glob.glob(os.path.join(content_folder, "steam_workshop_id_*.txt"))
            
            if marker_files:
                # 使用第一个找到的标记文件
                marker_file = marker_files[0]
                
                # 从文件名中提取物品ID
                import re
                match = re.search(r'steam_workshop_id_([0-9]+)\.txt', marker_file)
                if match:
                    existing_item_id = int(match.group(1))
                    logger.info(f"检测到物品已上传，找到标记文件: {marker_file}，物品ID: {existing_item_id}")
                    return existing_item_id
    except Exception as e:
        logger.error(f"检查上传标记文件时出错: {e}")
        # 即使检查失败，也继续尝试上传，不阻止功能
    try:
        # 再次验证内容文件夹，确保在多线程环境中仍然有效
        if not os.path.exists(content_folder) or not os.path.isdir(content_folder):
            raise Exception(f"内容文件夹不存在或无效: {content_folder}")
        
        # 统计文件夹内容，确保有文件可上传
        file_count = 0
        for root, dirs, files in os.walk(content_folder):
            file_count += len(files)
        
        if file_count == 0:
            raise Exception(f"内容文件夹中没有找到可上传的文件: {content_folder}")
        
        logger.info(f"内容文件夹验证通过，包含 {file_count} 个文件")
        
        # 获取当前应用ID
        app_id = steamworks.app_id
        logger.info(f"使用应用ID: {app_id} 进行创意工坊上传")
        
        # 增强的Steam连接状态验证
        try:
            # 基础连接状态检查
            is_steam_running = steamworks.IsSteamRunning()
            is_overlay_enabled = steamworks.IsOverlayEnabled()
            is_logged_on = steamworks.Users.LoggedOn()
            steam_id = steamworks.Users.GetSteamID()
            
            # 应用相关权限检查
            app_owned = steamworks.Apps.IsAppInstalled(app_id)
            app_owned_license = steamworks.Apps.IsSubscribedApp(app_id)
            app_subscribed = steamworks.Apps.IsSubscribed()
            
            # 记录详细的连接状态
            logger.info(f"Steam客户端运行状态: {is_steam_running}")
            logger.info(f"Steam覆盖层启用状态: {is_overlay_enabled}")
            logger.info(f"用户登录状态: {is_logged_on}")
            logger.info(f"用户SteamID: {steam_id}")
            logger.info(f"应用ID {app_id} 安装状态: {app_owned}")
            logger.info(f"应用ID {app_id} 订阅许可状态: {app_owned_license}")
            logger.info(f"当前应用订阅状态: {app_subscribed}")
            
            # 预检查连接状态，如果存在问题则提前报错
            if not is_steam_running:
                raise Exception("Steam客户端未运行，请先启动Steam客户端")
            if not is_logged_on:
                raise Exception("用户未登录Steam，请确保已登录Steam客户端")
            
        except Exception as e:
            logger.error(f"Steam连接状态验证失败: {e}")
            # 即使验证失败也继续执行，但提供警告
            logger.warning(f"继续尝试创意工坊上传，但可能会因为Steam连接问题而失败")
        
        # 错误映射表，根据错误码提供更具体的错误信息
        error_codes = {
            1: "成功",
            10: "权限不足 - 可能需要登录Steam客户端或缺少创意工坊上传权限",
            111: "网络连接错误 - 无法连接到Steam网络",
            100: "服务不可用 - Steam创意工坊服务暂时不可用",
            8: "文件已存在 - 相同内容的物品已存在",
            34: "服务器忙 - Steam服务器暂时无法处理请求",
            116: "请求超时 - 与Steam服务器通信超时"
        }
        
        # 对于新物品，先创建一个空物品
        # 使用回调来处理创建结果
        created_item_id = [None]
        created_event = threading.Event()
        create_result = [None]  # 用于存储创建结果
        
        def onCreateItem(result):
            nonlocal created_item_id, create_result
            create_result[0] = result.result
            # 直接从结构体读取字段而不是字典
            if result.result == 1:  # k_EResultOK
                created_item_id[0] = result.publishedFileId
                logger.info(f"成功创建创意工坊物品，ID: {created_item_id[0]}")
                created_event.set()
            else:
                error_msg = error_codes.get(result.result, f"未知错误码: {result.result}")
                logger.error(f"创建创意工坊物品失败，错误码: {result.result} ({error_msg})")
                created_event.set()
        
        # 设置创建物品回调
        steamworks.Workshop.SetItemCreatedCallback(onCreateItem)
        
        # 创建新的创意工坊物品（使用文件类型枚举表示UGC）
        logger.info(f"开始创建创意工坊物品: {title}")
        logger.info(f"调用SteamWorkshop.CreateItem({app_id}, {EWorkshopFileType.COMMUNITY})")
        steamworks.Workshop.CreateItem(app_id, EWorkshopFileType.COMMUNITY)
        
        # 等待创建完成或超时，增加超时时间并添加调试信息
        logger.info("等待创意工坊物品创建完成...")
        # 使用循环等待，定期调用run_callbacks处理回调
        start_time = time.time()
        timeout = 60  # 超时时间60秒
        while time.time() - start_time < timeout:
            if created_event.is_set():
                break
            # 定期调用run_callbacks处理Steam API回调
            try:
                steamworks.run_callbacks()
            except Exception as e:
                logger.error(f"执行Steam回调时出错: {str(e)}")
            time.sleep(0.1)  # 每100毫秒检查一次
        
        if not created_event.is_set():
            logger.error("创建创意工坊物品超时，可能是网络问题或Steam服务暂时不可用")
            raise TimeoutError("创建创意工坊物品超时")
        
        if created_item_id[0] is None:
            # 提供更具体的错误信息
            error_msg = error_codes.get(create_result[0], f"未知错误码: {create_result[0]}")
            logger.error(f"创建创意工坊物品失败: {error_msg}")
            
            # 针对错误码10（权限不足）提供更详细的错误信息和解决方案
            if create_result[0] == 10:
                detailed_error = f"""权限不足 - 请确保:
1. Steam客户端已启动并登录
2. 您的Steam账号拥有应用ID {app_id} 的访问权限
3. Steam创意工坊功能未被禁用
4. 尝试以管理员权限运行应用程序
5. 检查防火墙设置是否阻止了应用程序访问Steam网络
6. 确保steam_appid.txt文件中的应用ID正确
7. 您的Steam账号有权限上传到该应用的创意工坊"""
                logger.error(f"创意工坊上传失败 - 详细诊断信息:")
                logger.error(f"- 应用ID: {app_id}")
                logger.error(f"- Steam运行状态: {steamworks.IsSteamRunning()}")
                logger.error(f"- 用户登录状态: {steamworks.Users.LoggedOn()}")
                logger.error(f"- 应用订阅状态: {steamworks.Apps.IsSubscribedApp(app_id)}")
                raise Exception(f"创建创意工坊物品失败: {detailed_error} (错误码: {create_result[0]})")
            else:
                raise Exception(f"创建创意工坊物品失败: {error_msg} (错误码: {create_result[0]})")
        
        # 开始更新物品
        logger.info(f"开始更新物品内容: {title}")
        update_handle = steamworks.Workshop.StartItemUpdate(app_id, created_item_id[0])
        
        # 设置物品属性
        logger.info("设置物品基本属性...")
        steamworks.Workshop.SetItemTitle(update_handle, title)
        if description:
            steamworks.Workshop.SetItemDescription(update_handle, description)
        
        # 设置物品内容 - 这是文件上传的核心步骤
        logger.info(f"设置物品内容文件夹: {content_folder}")
        content_set_result = steamworks.Workshop.SetItemContent(update_handle, content_folder)
        logger.info(f"内容设置结果: {content_set_result}")
        
        # 设置预览图片（如果提供）
        if preview_image:
            logger.info(f"设置预览图片: {preview_image}")
            preview_set_result = steamworks.Workshop.SetItemPreview(update_handle, preview_image)
            logger.info(f"预览图片设置结果: {preview_set_result}")
        
        # 导入枚举类型并将整数值转换为枚举对象
        from steamworks.enums import ERemoteStoragePublishedFileVisibility
        if visibility == 0:
            visibility_enum = ERemoteStoragePublishedFileVisibility.PUBLIC
        elif visibility == 1:
            visibility_enum = ERemoteStoragePublishedFileVisibility.FRIENDS_ONLY
        elif visibility == 2:
            visibility_enum = ERemoteStoragePublishedFileVisibility.PRIVATE
        else:
            # 默认设为公开
            visibility_enum = ERemoteStoragePublishedFileVisibility.PUBLIC
            
        # 设置物品可见性
        logger.info(f"设置物品可见性: {visibility_enum}")
        steamworks.Workshop.SetItemVisibility(update_handle, visibility_enum)
        
        # 设置标签（如果有）
        if tags:
            logger.info(f"设置物品标签: {tags}")
            steamworks.Workshop.SetItemTags(update_handle, tags)
        
        # 提交更新，使用回调来处理结果
        updated = [False]
        error_code = [0]
        update_event = threading.Event()
        
        def onSubmitItemUpdate(result):
            nonlocal updated, error_code
            # 直接从结构体读取字段而不是字典
            error_code[0] = result.result
            if result.result == 1:  # k_EResultOK
                updated[0] = True
                logger.info(f"物品更新提交成功，结果代码: {result.result}")
            else:
                logger.error(f"提交创意工坊物品更新失败，错误码: {result.result}")
            update_event.set()
        
        # 设置更新物品回调
        steamworks.Workshop.SetItemUpdatedCallback(onSubmitItemUpdate)
        
        # 提交更新
        logger.info(f"开始提交物品更新，更新说明: {change_note}")
        steamworks.Workshop.SubmitItemUpdate(update_handle, change_note)
        
        # 等待更新完成或超时，增加超时时间并添加调试信息
        logger.info("等待创意工坊物品更新完成...")
        # 使用循环等待，定期调用run_callbacks处理回调
        start_time = time.time()
        timeout = 180  # 超时时间180秒
        last_progress = -1
        
        while time.time() - start_time < timeout:
            if update_event.is_set():
                break
            # 定期调用run_callbacks处理Steam API回调
            try:
                steamworks.run_callbacks()
                # 记录上传进度（更详细的进度报告）
                if update_handle:
                    progress = steamworks.Workshop.GetItemUpdateProgress(update_handle)
                    if 'status' in progress:
                        status_text = "未知"
                        if progress['status'] == EItemUpdateStatus.UPLOADING_CONTENT:
                            status_text = "上传内容"
                        elif progress['status'] == EItemUpdateStatus.UPLOADING_PREVIEW_FILE:
                            status_text = "上传预览图"
                        elif progress['status'] == EItemUpdateStatus.COMMITTING_CHANGES:
                            status_text = "提交更改"
                        
                        if 'progress' in progress:
                            current_progress = int(progress['progress'] * 100)
                            # 只有进度有明显变化时才记录日志
                            if current_progress != last_progress:
                                logger.info(f"上传状态: {status_text}, 进度: {current_progress}%")
                                last_progress = current_progress
            except Exception as e:
                logger.error(f"执行Steam回调时出错: {str(e)}")
            time.sleep(0.5)  # 每500毫秒检查一次，减少日志量
        
        if not update_event.is_set():
            logger.error("提交创意工坊物品更新超时，可能是网络问题或Steam服务暂时不可用")
            raise TimeoutError("提交创意工坊物品更新超时")
        
        if not updated[0]:
            # 根据错误码提供更详细的错误信息
            if error_code[0] == 25:  # LIMIT_EXCEEDED
                error_msg = "提交创意工坊物品更新失败：内容超过Steam限制（错误码25）。请检查内容大小、文件数量或其他限制。"
            else:
                error_msg = f"提交创意工坊物品更新失败，错误码: {error_code[0]}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        logger.info(f"创意工坊物品上传成功完成！物品ID: {created_item_id[0]}")
        
        # 在原文件夹创建带物品ID的txt文件，标记为已上传
        try:
            marker_file_path = os.path.join(content_folder, f"steam_workshop_id_{created_item_id[0]}.txt")
            with open(marker_file_path, 'w', encoding='utf-8') as f:
                f.write(f"Steam创意工坊物品ID: {created_item_id[0]}\n")
                f.write(f"上传时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n")
                f.write(f"物品标题: {title}\n")
            logger.info(f"已在原文件夹创建上传标记文件: {marker_file_path}")
        except Exception as e:
            logger.error(f"创建上传标记文件失败: {e}")
            # 即使创建标记文件失败，也不影响物品上传的成功返回
        
        return created_item_id[0]
        
    except Exception as e:
        logger.error(f"发布创意工坊物品时出错: {e}")
        raise

@app.post('/api/steam/set-achievement-status/{name}')
async def set_achievement_status(name: str):
    if steamworks is not None:
        try:
            # 先请求统计数据并运行回调，确保数据已加载
            steamworks.UserStats.RequestCurrentStats()
            # 运行回调等待数据加载（多次运行以确保接收到响应）
            for _ in range(10):
                steamworks.run_callbacks()
                await asyncio.sleep(0.1)
            
            achievement_status = steamworks.UserStats.GetAchievement(name)
            logger.info(f"Achievement status: {achievement_status}")
            if not achievement_status:
                result = steamworks.UserStats.SetAchievement(name)
                if result:
                    logger.info(f"成功设置成就: {name}")
                    steamworks.UserStats.StoreStats()
                    steamworks.run_callbacks()
                else:
                    # 第一次失败，等待后重试一次
                    logger.warning(f"设置成就首次尝试失败，正在重试: {name}")
                    await asyncio.sleep(0.5)
                    steamworks.run_callbacks()
                    result = steamworks.UserStats.SetAchievement(name)
                    if result:
                        logger.info(f"成功设置成就（重试后）: {name}")
                        steamworks.UserStats.StoreStats()
                        steamworks.run_callbacks()
                    else:
                        logger.error(f"设置成就失败: {name}，请确认成就ID在Steam后台已配置")
            else:
                logger.info(f"成就已解锁，无需重复设置: {name}")
        except Exception as e:
            logger.error(f"设置成就失败: {e}")

@app.get('/api/steam/list-achievements')
async def list_achievements():
    """列出Steam后台已配置的所有成就（调试用）"""
    if steamworks is not None:
        try:
            steamworks.UserStats.RequestCurrentStats()
            for _ in range(10):
                steamworks.run_callbacks()
                await asyncio.sleep(0.1)
            
            num_achievements = steamworks.UserStats.GetNumAchievements()
            achievements = []
            for i in range(num_achievements):
                name = steamworks.UserStats.GetAchievementName(i)
                if name:
                    # 如果是bytes类型，解码为字符串
                    if isinstance(name, bytes):
                        name = name.decode('utf-8')
                    status = steamworks.UserStats.GetAchievement(name)
                    achievements.append({"name": name, "unlocked": status})
            
            logger.info(f"Steam后台已配置 {num_achievements} 个成就: {achievements}")
            return JSONResponse(content={"count": num_achievements, "achievements": achievements})
        except Exception as e:
            logger.error(f"获取成就列表失败: {e}")
            return JSONResponse(content={"error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"error": "Steamworks未初始化"}, status_code=500)

@app.get('/api/file-exists')
async def check_file_exists(path: str = None):
    try:
        if not path:
            return JSONResponse(content={"exists": False}, status_code=400)
        
        # 解码URL编码的路径
        decoded_path = unquote(path)
        
        # Windows路径处理
        if os.name == 'nt':
            decoded_path = decoded_path.replace('/', '\\')
        
        # 安全检查：拒绝包含路径遍历字符的请求
        if '..' in decoded_path:
            logger.warning(f"检测到潜在的路径遍历攻击: {decoded_path}")
            return JSONResponse(content={"exists": False}, status_code=403)
        
        # 规范化路径
        real_path = os.path.realpath(decoded_path)
        
        # 检查文件是否存在
        exists = os.path.exists(real_path) and os.path.isfile(real_path)
        
        return JSONResponse(content={"exists": exists})
        
    except Exception as e:
        logger.error(f"检查文件存在失败: {e}")
        return JSONResponse(content={"exists": False}, status_code=500)

@app.get('/api/find-first-image')
async def find_first_image(folder: str = None):
    """
    查找指定文件夹中的预览图片
    
    安全注意事项：
    1. 防止路径遍历攻击
    2. 只返回小于 1MB 的图片（Steam创意工坊预览图大小限制）
    """
    MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1MB
    
    try:
        # 检查参数有效性
        if not folder:
            logger.warning("收到空的文件夹路径请求")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 解码URL编码的路径
        decoded_folder = unquote(folder)
        
        # Windows路径处理
        if os.name == 'nt':
            decoded_folder = decoded_folder.replace('/', '\\')
        
        # 安全检查：拒绝包含路径遍历字符的请求
        if '..' in decoded_folder or '//' in decoded_folder:
            logger.warning(f"检测到潜在的路径遍历攻击: {decoded_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # 规范化路径
        try:
            real_folder = os.path.realpath(decoded_folder)
        except Exception as e:
            logger.error(f"路径规范化失败: {e}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 检查文件夹是否存在
        if not os.path.exists(real_folder) or not os.path.isdir(real_folder):
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 只查找指定的8个预览图片名称，按优先级顺序
        preview_image_names = [
            'preview.jpg', 'preview.png',
            'thumbnail.jpg', 'thumbnail.png',
            'icon.jpg', 'icon.png',
            'header.jpg', 'header.png'
        ]
        
        for image_name in preview_image_names:
            image_path = os.path.join(real_folder, image_name)
            try:
                # 检查文件是否存在
                if os.path.exists(image_path) and os.path.isfile(image_path):
                    # 检查文件大小是否小于 1MB
                    file_size = os.path.getsize(image_path)
                    if file_size >= MAX_IMAGE_SIZE:
                        logger.info(f"跳过大于1MB的图片: {image_name} ({file_size / 1024 / 1024:.2f}MB)")
                        continue
                    
                    # 返回图片的绝对路径
                    return JSONResponse(content={"success": True, "imagePath": image_path})
            except Exception as e:
                logger.error(f"检查图片文件 {image_name} 失败: {e}")
                continue
        
        return JSONResponse(content={"success": False, "error": "未找到小于1MB的预览图片文件"})
        
    except Exception as e:
        logger.error(f"查找预览图片文件失败: {e}")
        return JSONResponse(content={"success": False, "error": "服务器内部错误"}, status_code=500)


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
    """在文件夹中查找预览图片，只查找指定的图片名称"""
    # 按优先级顺序查找指定的图片文件列表（包含更多扩展名格式）
    preview_image_names = [
        'preview.jpg', 'preview.jpeg', 'preview.png', 
        'thumbnail.jpg', 'thumbnail.jpeg', 'thumbnail.png', 
        'icon.jpg', 'icon.jpeg', 'icon.png', 'icon.ico',
        'header.jpg', 'header.jpeg', 'header.png',
        'cover.jpg', 'cover.jpeg', 'cover.png'
    ]
    
    logger.debug(f'在文件夹 {folder_path} 中查找预览图')
    
    for image_name in preview_image_names:
        image_path = os.path.join(folder_path, image_name)
        if os.path.exists(image_path) and os.path.isfile(image_path):
            logger.debug(f'找到预览图: {image_path}')
            return image_path
    
    # 如果找不到指定的图片名称，尝试查找任何图片文件
    try:
        for file in os.listdir(folder_path):
            if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.ico')):
                image_path = os.path.join(folder_path, file)
                if os.path.isfile(image_path):
                    logger.debug(f'使用备选预览图: {image_path}')
                    return image_path
    except Exception as e:
        logger.warning(f'遍历文件夹 {folder_path} 时出错: {e}')
    
    logger.debug(f'在文件夹 {folder_path} 中未找到任何预览图')
    return None

@app.get('/live2d_emotion_manager', response_class=HTMLResponse)
async def live2d_emotion_manager(request: Request):
    """Live2D情感映射管理器页面"""
    try:
        template_path = os.path.join(_get_app_root(), 'templates', 'live2d_emotion_manager.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return HTMLResponse(content=content)
    except Exception as e:
        logger.error(f"加载Live2D情感映射管理器页面失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get('/api/live2d/emotion_mapping/{model_name}')
async def get_emotion_mapping(model_name: str):
    """获取情绪映射配置"""
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 优先使用 EmotionMapping；若不存在则从 FileReferences 推导
        emotion_mapping = config_data.get('EmotionMapping')
        if not emotion_mapping:
            derived_mapping = {"motions": {}, "expressions": {}}
            file_refs = config_data.get('FileReferences', {}) or {}

            # 从标准 Motions 结构推导
            motions = file_refs.get('Motions', {}) or {}
            for group_name, items in motions.items():
                files = []
                for item in items or []:
                    try:
                        file_path = item.get('File') if isinstance(item, dict) else None
                        if file_path:
                            files.append(file_path.replace('\\', '/'))
                    except Exception:
                        continue
                derived_mapping["motions"][group_name] = files

            # 从标准 Expressions 结构推导（按 Name 的前缀进行分组，如 happy_xxx）
            expressions = file_refs.get('Expressions', []) or []
            for item in expressions:
                if not isinstance(item, dict):
                    continue
                name = item.get('Name') or ''
                file_path = item.get('File') or ''
                if not file_path:
                    continue
                file_path = file_path.replace('\\', '/')
                # 根据第一个下划线拆分分组
                if '_' in name:
                    group = name.split('_', 1)[0]
                else:
                    # 无前缀的归入 neutral 组，避免丢失
                    group = 'neutral'
                derived_mapping["expressions"].setdefault(group, []).append(file_path)

            emotion_mapping = derived_mapping
        
        return {"success": True, "config": emotion_mapping}
    except Exception as e:
        logger.error(f"获取情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post('/api/live2d/upload_model')
async def upload_live2d_model(files: list[UploadFile] = File(...)):
    """上传Live2D模型到用户文档目录"""
    import shutil
    import tempfile
    import zipfile
    
    try:
        if not files:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # 创建临时目录来处理上传的文件
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            
            # 保存所有上传的文件到临时目录，保持目录结构
            for file in files:
                # 从文件的相对路径中提取目录结构
                file_path = file.filename
                # 确保路径安全，移除可能的危险路径字符
                file_path = file_path.replace('\\', '/').lstrip('/')
                
                target_file_path = temp_path / file_path
                target_file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 保存文件
                with open(target_file_path, 'wb') as f:
                    content = await file.read()
                    f.write(content)
            
            # 在临时目录中递归查找.model3.json文件
            model_json_files = list(temp_path.rglob('*.model3.json'))
            
            if not model_json_files:
                return JSONResponse(status_code=400, content={"success": False, "error": "未找到.model3.json文件"})
            
            if len(model_json_files) > 1:
                return JSONResponse(status_code=400, content={"success": False, "error": "上传的文件中包含多个.model3.json文件"})
            
            model_json_file = model_json_files[0]
            
            # 确定模型根目录（.model3.json文件的父目录）
            model_root_dir = model_json_file.parent
            model_name = model_root_dir.name
            
            # 获取用户文档的live2d目录
            config_mgr = get_config_manager()
            config_mgr.ensure_live2d_directory()
            user_live2d_dir = config_mgr.live2d_dir
            
            # 目标目录
            target_model_dir = user_live2d_dir / model_name
            
            # 如果目标目录已存在，返回错误或覆盖（这里选择返回错误）
            if target_model_dir.exists():
                return JSONResponse(status_code=400, content={
                    "success": False, 
                    "error": f"模型 {model_name} 已存在，请先删除或重命名现有模型"
                })
            
            # 复制模型根目录到用户文档的live2d目录
            shutil.copytree(model_root_dir, target_model_dir)

            # 上传后：遍历模型目录中的所有动作文件（*.motion3.json），
            # 将官方白名单参数及模型自身在 .model3.json 中声明为 LipSync 的参数的 Segments 清空为 []。
            # 这样可以兼顾官方参数与模型声明的口型参数，同时忽略未声明的作者自定义命名（避免误伤）。
            try:
                import json as _json

                # 官方口型参数白名单（尽量全面列出常见和官方命名的嘴部/口型相关参数）
                # 仅包含与嘴巴形状、发音帧（A/I/U/E/O）、下颚/唇动作直接相关的参数，
                # 明确排除头部/身体/表情等其它参数（例如 ParamAngleZ、ParamAngleX 等不应在此）。
                official_mouth_params = {
                    # 五个基本发音帧（A/I/U/E/O）
                    'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO',
                    # 常见嘴部上下/开合/形状参数
                    'ParamMouthUp', 'ParamMouthDown', 'ParamMouthOpen', 'ParamMouthOpenY',
                    'ParamMouthForm', 'ParamMouthX', 'ParamMouthY', 'ParamMouthSmile', 'ParamMouthPucker',
                    'ParamMouthStretch', 'ParamMouthShrug', 'ParamMouthLeft', 'ParamMouthRight',
                    'ParamMouthCornerUpLeft', 'ParamMouthCornerUpRight',
                    'ParamMouthCornerDownLeft', 'ParamMouthCornerDownRight',
                    # 唇相关（部分模型/官方扩展中可能出现）
                    'ParamLipA', 'ParamLipI', 'ParamLipU', 'ParamLipE', 'ParamLipO', 'ParamLipThickness',
                    # 下颚（部分模型以下颚控制口型）
                    'ParamJawOpen', 'ParamJawForward', 'ParamJawLeft', 'ParamJawRight',
                    # 其它口型相关（保守列入）
                    'ParamMouthAngry', 'ParamMouthAngryLine'
                }

                # 尝试读取模型的 .model3.json，提取 Groups -> Name == "LipSync" && Target == "Parameter" 的 Ids
                model_declared_mouth_params = set()
                try:
                    local_model_json = target_model_dir / model_json_file.name
                    if local_model_json.exists():
                        with open(local_model_json, 'r', encoding='utf-8') as mf:
                            try:
                                model_cfg = _json.load(mf)
                                groups = model_cfg.get('Groups') if isinstance(model_cfg, dict) else None
                                if isinstance(groups, list):
                                    for grp in groups:
                                        try:
                                            if not isinstance(grp, dict):
                                                continue
                                            # 仅考虑官方 Group Name 为 LipSync 且 Target 为 Parameter 的条目
                                            if grp.get('Name') == 'LipSync' and grp.get('Target') == 'Parameter':
                                                ids = grp.get('Ids') or []
                                                for pid in ids:
                                                    if isinstance(pid, str) and pid:
                                                        model_declared_mouth_params.add(pid)
                                        except Exception:
                                            continue
                            except Exception:
                                # 解析失败则视为未找到 groups，继续使用官方白名单
                                pass
                except Exception:
                    pass

                # 合并白名单（官方 + 模型声明）
                mouth_param_whitelist = set(official_mouth_params)
                mouth_param_whitelist.update(model_declared_mouth_params)

                for motion_path in target_model_dir.rglob('*.motion3.json'):
                    try:
                        with open(motion_path, 'r', encoding='utf-8') as mf:
                            try:
                                motion_data = _json.load(mf)
                            except Exception:
                                # 非 JSON 或解析失败则跳过
                                continue

                        modified = False
                        curves = motion_data.get('Curves') if isinstance(motion_data, dict) else None
                        if isinstance(curves, list):
                            for curve in curves:
                                try:
                                    if not isinstance(curve, dict):
                                        continue
                                    cid = curve.get('Id')
                                    if not cid:
                                        continue
                                    # 严格按白名单匹配（避免模糊匹配误伤）
                                    if cid in mouth_param_whitelist:
                                        # 清空 Segments（若存在）
                                        if 'Segments' in curve and curve['Segments']:
                                            curve['Segments'] = []
                                            modified = True
                                except Exception:
                                    continue

                        if modified:
                            try:
                                with open(motion_path, 'w', encoding='utf-8') as mf:
                                    _json.dump(motion_data, mf, ensure_ascii=False, indent=4)
                                logger.info(f"已清除口型参数：{motion_path}")
                            except Exception:
                                # 写入失败则记录但不阻止上传
                                logger.exception(f"写入 motion 文件失败: {motion_path}")
                    except Exception:
                        continue
            except Exception:
                logger.exception("处理 motion 文件时发生错误")
            
            logger.info(f"成功上传Live2D模型: {model_name} -> {target_model_dir}")
            
            return JSONResponse(content={
                "success": True,
                "message": f"模型 {model_name} 上传成功",
                "model_name": model_name,
                "model_path": str(target_model_dir)
            })
            
    except Exception as e:
        logger.error(f"上传Live2D模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post('/api/live2d/emotion_mapping/{model_name}')
async def update_emotion_mapping(model_name: str, request: Request):
    """更新情绪映射配置"""
    try:
        data = await request.json()
        
        if not data:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的数据"})

        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})

        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 统一写入到标准 Cubism 结构（FileReferences.Motions / FileReferences.Expressions）
        file_refs = config_data.setdefault('FileReferences', {})

        # 处理 motions: data 结构为 { motions: { emotion: ["motions/xxx.motion3.json", ...] }, expressions: {...} }
        motions_input = (data.get('motions') if isinstance(data, dict) else None) or {}
        motions_output = {}
        for group_name, files in motions_input.items():
            # 禁止在"常驻"组配置任何motion
            if group_name == '常驻':
                logger.info("忽略常驻组中的motion配置（只允许expression）")
                continue
            items = []
            for file_path in files or []:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.replace('\\', '/').lstrip('./')
                items.append({"File": normalized})
            motions_output[group_name] = items
        file_refs['Motions'] = motions_output

        # 处理 expressions: 将按 emotion 前缀生成扁平列表，Name 采用 "{emotion}_{basename}" 的约定
        expressions_input = (data.get('expressions') if isinstance(data, dict) else None) or {}

        # 先保留不属于我们情感前缀的原始表达（避免覆盖用户自定义）
        existing_expressions = file_refs.get('Expressions', []) or []
        emotion_prefixes = set(expressions_input.keys())
        preserved_expressions = []
        for item in existing_expressions:
            try:
                name = (item.get('Name') or '') if isinstance(item, dict) else ''
                prefix = name.split('_', 1)[0] if '_' in name else None
                if not prefix or prefix not in emotion_prefixes:
                    preserved_expressions.append(item)
            except Exception:
                preserved_expressions.append(item)

        new_expressions = []
        for emotion, files in expressions_input.items():
            for file_path in files or []:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.replace('\\', '/').lstrip('./')
                base = os.path.basename(normalized)
                base_no_ext = base.replace('.exp3.json', '')
                name = f"{emotion}_{base_no_ext}"
                new_expressions.append({"Name": name, "File": normalized})

        file_refs['Expressions'] = preserved_expressions + new_expressions

        # 同时保留一份 EmotionMapping（供管理器读取与向后兼容）
        config_data['EmotionMapping'] = data

        # 保存配置到文件
        with open(model_json_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"模型 {model_name} 的情绪映射配置已更新（已同步到 FileReferences）")
        return {"success": True, "message": "情绪映射配置已保存"}
    except Exception as e:
        logger.error(f"更新情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post('/api/memory/recent_file/save')
async def save_recent_file(request: Request):
    import os, json
    data = await request.json()
    filename = data.get('filename')
    chat = data.get('chat')
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    file_path = str(cm.memory_dir / filename)
    if not (filename and filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    arr = []
    for msg in chat:
        t = msg.get('role')
        text = msg.get('text', '')
        arr.append({
            "type": t,
            "data": {
                "content": text,
                "additional_kwargs": {},
                "response_metadata": {},
                "type": t,
                "name": None,
                "id": None,
                "example": False,
                **({"tool_calls": [], "invalid_tool_calls": [], "usage_metadata": None} if t == "ai" else {})
            }
        })
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(arr, f, ensure_ascii=False, indent=2)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post('/api/memory/update_catgirl_name')
async def update_catgirl_name(request: Request):
    """
    更新记忆文件中的猫娘名称
    1. 重命名记忆文件
    2. 更新文件内容中的猫娘名称引用
    """
    import os, json
    data = await request.json()
    old_name = data.get('old_name')
    new_name = data.get('new_name')
    
    if not old_name or not new_name:
        return JSONResponse({"success": False, "error": "缺少必要参数"}, status_code=400)
    
    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        
        # 1. 重命名记忆文件
        old_filename = f'recent_{old_name}.json'
        new_filename = f'recent_{new_name}.json'
        old_file_path = str(cm.memory_dir / old_filename)
        new_file_path = str(cm.memory_dir / new_filename)
        
        # 检查旧文件是否存在
        if not os.path.exists(old_file_path):
            logger.warning(f"记忆文件不存在: {old_file_path}")
            return JSONResponse({"success": False, "error": f"记忆文件不存在: {old_filename}"}, status_code=404)
        
        # 如果新文件已存在，先删除
        if os.path.exists(new_file_path):
            os.remove(new_file_path)
        
        # 重命名文件
        os.rename(old_file_path, new_file_path)
        
        # 2. 更新文件内容中的猫娘名称引用
        with open(new_file_path, 'r', encoding='utf-8') as f:
            file_content = json.load(f)
        
        # 遍历所有消息，仅在特定字段中更新猫娘名称
        for item in file_content:
            if isinstance(item, dict):
                # 安全的方式：只在特定的字段中替换猫娘名称
                # 避免在整个content中进行字符串替换
                
                # 检查角色名称相关字段
                name_fields = ['speaker', 'author', 'name', 'character', 'role']
                for field in name_fields:
                    if field in item and isinstance(item[field], str) and old_name in item[field]:
                        if item[field] == old_name:  # 完全匹配才替换
                            item[field] = new_name
                            logger.debug(f"更新角色名称字段 {field}: {old_name} -> {new_name}")
                
                # 如果item有data嵌套结构，也检查其中的name字段
                if 'data' in item and isinstance(item['data'], dict):
                    data = item['data']
                    for field in name_fields:
                        if field in data and isinstance(data[field], str) and old_name in data[field]:
                            if data[field] == old_name:  # 完全匹配才替换
                                data[field] = new_name
                                logger.debug(f"更新data中角色名称字段 {field}: {old_name} -> {new_name}")
                    
                    # 对于content字段，使用更保守的方法 - 仅在明确标识为角色名称的地方替换
                    if 'content' in data and isinstance(data['content'], str):
                        content = data['content']
                        # 检查是否是明确的角色发言格式，如"小白说："或"小白: "
                        # 这种格式通常表示后面的内容是角色发言
                        patterns = [
                            f"{old_name}说：",  # 中文冒号
                            f"{old_name}说:",   # 英文冒号  
                            f"{old_name}:",     # 纯冒号
                            f"{old_name}->",    # 箭头
                            f"[{old_name}]",    # 方括号
                        ]
                        
                        for pattern in patterns:
                            if pattern in content:
                                new_pattern = pattern.replace(old_name, new_name)
                                content = content.replace(pattern, new_pattern)
                                logger.debug(f"在消息内容中发现角色标识，更新: {pattern} -> {new_pattern}")
                        
                        data['content'] = content
        
        # 保存更新后的内容
        with open(new_file_path, 'w', encoding='utf-8') as f:
            json.dump(file_content, f, ensure_ascii=False, indent=2)
        
        logger.info(f"已更新猫娘名称从 '{old_name}' 到 '{new_name}' 的记忆文件")
        return {"success": True}
    except Exception as e:
        logger.exception("更新猫娘名称失败")
        return {"success": False, "error": str(e)}

@app.post('/api/emotion/analysis')
async def emotion_analysis(request: Request):
    try:
        data = await request.json()
        if not data or 'text' not in data:
            return {"error": "请求体中必须包含text字段"}
        
        text = data['text']
        api_key = data.get('api_key')
        model = data.get('model')
        
        # 使用参数或默认配置
        core_config = _config_manager.get_core_config()
        api_key = api_key or core_config['OPENROUTER_API_KEY']
        model = model or core_config['EMOTION_MODEL']
        
        if not api_key:
            return {"error": "API密钥未提供且配置中未设置默认密钥"}
        
        if not model:
            return {"error": "模型名称未提供且配置中未设置默认模型"}
        
        # 创建异步客户端
        client = AsyncOpenAI(api_key=api_key, base_url=core_config['OPENROUTER_URL'])
        
        # 构建请求消息
        messages = [
            {
                "role": "system", 
                "content": emotion_analysis_prompt
            },
            {
                "role": "user", 
                "content": text
            }
        ]
        
        # 异步调用模型
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 100
        }
        
        # 只有在需要时才添加 extra_body
        if model in MODELS_WITH_EXTRA_BODY:
            request_params["extra_body"] = {"enable_thinking": False}
        
        response = await client.chat.completions.create(**request_params)
        
        # 解析响应
        result_text = response.choices[0].message.content.strip()
        
        # 尝试解析JSON响应
        try:
            import json
            result = json.loads(result_text)
            # 获取emotion和confidence
            emotion = result.get("emotion", "neutral")
            confidence = result.get("confidence", 0.5)
            
            # 当confidence小于0.3时，自动将emotion设置为neutral
            if confidence < 0.3:
                emotion = "neutral"
            
            # 获取 lanlan_name 并推送到 monitor
            lanlan_name = data.get('lanlan_name')
            if lanlan_name and lanlan_name in sync_message_queue:
                sync_message_queue[lanlan_name].put({
                    "type": "json",
                    "data": {
                        "type": "emotion",
                        "emotion": emotion,
                        "confidence": confidence
                    }
                })
            
            return {
                "emotion": emotion,
                "confidence": confidence
            }
        except json.JSONDecodeError:
            # 如果JSON解析失败，返回简单的情感判断
            return {
                "emotion": "neutral",
                "confidence": 0.5
            }
            
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {
            "error": f"情感分析失败: {str(e)}",
            "emotion": "neutral",
            "confidence": 0.0
        }

@app.get('/memory_browser', response_class=HTMLResponse)
async def memory_browser(request: Request):
    return templates.TemplateResponse('templates/memory_browser.html', {"request": request})


@app.get("/{lanlan_name}", response_class=HTMLResponse)
async def get_index(request: Request, lanlan_name: str):
    # lanlan_name 将从 URL 中提取，前端会通过 API 获取配置
    return templates.TemplateResponse("templates/index.html", {
        "request": request
    })

@app.post('/api/agent/flags')
async def update_agent_flags(request: Request):
    """来自前端的Agent开关更新，级联到各自的session manager。"""
    try:
        data = await request.json()
        _, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        lanlan = data.get('lanlan_name') or her_name_current
        flags = data.get('flags') or {}
        mgr = session_manager.get(lanlan)
        if not mgr:
            return JSONResponse({"success": False, "error": "lanlan not found"}, status_code=404)
        # Update core flags first
        mgr.update_agent_flags(flags)
        # Forward to tool server for MCP/Computer-Use flags
        try:
            forward_payload = {}
            if 'mcp_enabled' in flags:
                forward_payload['mcp_enabled'] = bool(flags['mcp_enabled'])
            if 'computer_use_enabled' in flags:
                forward_payload['computer_use_enabled'] = bool(flags['computer_use_enabled'])
            if forward_payload:
                async with httpx.AsyncClient(timeout=0.7) as client:
                    r = await client.post(f"http://localhost:{TOOL_SERVER_PORT}/agent/flags", json=forward_payload)
                    if not r.is_success:
                        raise Exception(f"tool_server responded {r.status_code}")
        except Exception as e:
            # On failure, reset flags in core to safe state
            mgr.update_agent_flags({'agent_enabled': False, 'computer_use_enabled': False, 'mcp_enabled': False})
            return JSONResponse({"success": False, "error": f"tool_server forward failed: {e}"}, status_code=502)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get('/api/agent/flags')
async def get_agent_flags():
    """获取当前 agent flags 状态（供前端同步）"""
    try:
        async with httpx.AsyncClient(timeout=0.7) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/agent/flags")
            if not r.is_success:
                return JSONResponse({"success": False, "error": "tool_server down"}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=502)


@app.get('/api/agent/health')
async def agent_health():
    """Check tool_server health via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=0.7) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/health")
            if not r.is_success:
                return JSONResponse({"status": "down"}, status_code=502)
            data = {}
            try:
                data = r.json()
            except Exception:
                pass
            return {"status": "ok", **({"tool": data} if isinstance(data, dict) else {})}
    except Exception:
        return JSONResponse({"status": "down"}, status_code=502)


@app.get('/api/agent/computer_use/availability')
async def proxy_cu_availability():
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/computer_use/availability")
            if not r.is_success:
                return JSONResponse({"ready": False, "reasons": [f"tool_server responded {r.status_code}"]}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"ready": False, "reasons": [f"proxy error: {e}"]}, status_code=502)


@app.get('/api/agent/mcp/availability')
async def proxy_mcp_availability():
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/mcp/availability")
            if not r.is_success:
                return JSONResponse({"ready": False, "reasons": [f"tool_server responded {r.status_code}"]}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"ready": False, "reasons": [f"proxy error: {e}"]}, status_code=502)


@app.get('/api/agent/tasks')
async def proxy_tasks():
    """Get all tasks from tool server via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/tasks")
            if not r.is_success:
                return JSONResponse({"tasks": [], "error": f"tool_server responded {r.status_code}"}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"tasks": [], "error": f"proxy error: {e}"}, status_code=502)


@app.get('/api/agent/tasks/{task_id}')
async def proxy_task_detail(task_id: str):
    """Get specific task details from tool server via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/tasks/{task_id}")
            if not r.is_success:
                return JSONResponse({"error": f"tool_server responded {r.status_code}"}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"error": f"proxy error: {e}"}, status_code=502)


# Task status polling endpoint for frontend
@app.get('/api/agent/task_status')
async def get_task_status():
    """Get current task status for frontend polling - returns all tasks with their current status."""
    try:
        # Get tasks from tool server using async client with increased timeout
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/tasks")
            if not r.is_success:
                return JSONResponse({"tasks": [], "error": f"tool_server responded {r.status_code}"}, status_code=502)
            
            tasks_data = r.json()
            tasks = tasks_data.get("tasks", [])
            debug_info = tasks_data.get("debug", {})
            
            # Enhance task data with additional information if needed
            enhanced_tasks = []
            for task in tasks:
                enhanced_task = {
                    "id": task.get("id"),
                    "status": task.get("status", "unknown"),
                    "type": task.get("type", "unknown"),
                    "lanlan_name": task.get("lanlan_name"),
                    "start_time": task.get("start_time"),
                    "end_time": task.get("end_time"),
                    "params": task.get("params", {}),
                    "result": task.get("result"),
                    "error": task.get("error"),
                    "source": task.get("source", "unknown")  # 添加来源信息
                }
                enhanced_tasks.append(enhanced_task)
            
            return {
                "success": True,
                "tasks": enhanced_tasks,
                "total_count": len(enhanced_tasks),
                "running_count": len([t for t in enhanced_tasks if t.get("status") == "running"]),
                "queued_count": len([t for t in enhanced_tasks if t.get("status") == "queued"]),
                "completed_count": len([t for t in enhanced_tasks if t.get("status") == "completed"]),
                "failed_count": len([t for t in enhanced_tasks if t.get("status") == "failed"]),
                "timestamp": datetime.now().isoformat(),
                "debug": debug_info  # 传递调试信息到前端
            }
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "tasks": [],
            "error": f"Failed to fetch task status: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }, status_code=500)


@app.post('/api/agent/admin/control')
async def proxy_admin_control(payload: dict = Body(...)):
    """Proxy admin control commands to tool server."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"http://localhost:{TOOL_SERVER_PORT}/admin/control", json=payload)
            if not r.is_success:
                return JSONResponse({"success": False, "error": f"tool_server responded {r.status_code}"}, status_code=502)
            
            result = r.json()
            logger.info(f"Admin control result: {result}")
            return result
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": f"Failed to execute admin control: {str(e)}"
        }, status_code=500)


# --- Run the Server ---
if __name__ == "__main__":
    import uvicorn
    import argparse
    import os
    import signal
    
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

    # 2) 定义服务器关闭回调
    def shutdown_server():
        logger.info("收到浏览器关闭信号，正在关闭服务器...")
        os.kill(os.getpid(), signal.SIGTERM)

    # 4) 启动服务器（阻塞，直到 server.should_exit=True）
    logger.info("--- Starting FastAPI Server ---")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT}/{args.page}")
    
    try:
        server.run()
    finally:
        logger.info("服务器已关闭")
