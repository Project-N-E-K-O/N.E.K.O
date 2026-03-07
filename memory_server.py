# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import CompressedRecentHistoryManager, SemanticMemory, ImportantSettingsManager, TimeIndexedMemory
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
import json
import uvicorn
from langchain_core.messages import convert_to_messages
from uuid import uuid4
from config import MEMORY_SERVER_PORT
from config.prompts_sys import _loc, INNER_THOUGHTS_HEADER, INNER_THOUGHTS_BODY
from utils.language_utils import get_global_language
from utils.config_manager import get_config_manager
from pydantic import BaseModel
import re
import asyncio
import logging
import argparse
from utils.frontend_utils import get_timestamp
from typing import Optional

# 配置日志
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)

class HistoryRequest(BaseModel):
    input_history: str
    # /process 默认仍兼容旧调用方：未传时 recent 更新与完整处理共用同一份历史。
    # cross_server 在 session end 场景下可单独传 recent_input_history，避免把已 /cache 的消息再次写进 recent history。
    recent_input_history: Optional[str] = None

app = FastAPI()


# ── 健康检查 / 指纹端点 ──────────────────────────────────────────
@app.get("/health")
async def health():
    """返回带 N.E.K.O 签名的健康响应，供 launcher/前端识别，
    以区分当前服务与随机占用该端口的其他进程。"""
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response("memory", instance_id=INSTANCE_ID)


def validate_lanlan_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=400, detail="Invalid lanlan_name length")
    if not re.match(r"^[\w\-\s]+$", name):
        raise HTTPException(status_code=400, detail="Invalid characters in lanlan_name")
    return name

# 初始化组件
_config_manager = get_config_manager()
recent_history_manager = CompressedRecentHistoryManager()
semantic_manager = SemanticMemory(recent_history_manager)
settings_manager = ImportantSettingsManager()
time_manager = TimeIndexedMemory(recent_history_manager)

# 用于保护重新加载操作的锁
_reload_lock = asyncio.Lock()

async def reload_memory_components():
    """重新加载记忆组件配置（用于新角色创建后）
    
    使用锁保护重新加载操作，确保原子性交换，避免竞态条件。
    先创建所有新实例，然后原子性地交换引用。
    """
    global recent_history_manager, semantic_manager, settings_manager, time_manager
    async with _reload_lock:
        logger.info("[MemoryServer] 开始重新加载记忆组件配置...")
        try:
            # 先创建所有新实例
            new_recent = CompressedRecentHistoryManager()
            new_semantic = SemanticMemory(new_recent)
            new_settings = ImportantSettingsManager()
            new_time = TimeIndexedMemory(new_recent)
            
            # 然后原子性地交换引用
            recent_history_manager = new_recent
            semantic_manager = new_semantic
            settings_manager = new_settings
            time_manager = new_time
            
            logger.info("[MemoryServer] ✅ 记忆组件配置重新加载完成")
            return True
        except Exception as e:
            logger.error(f"[MemoryServer] ❌ 重新加载记忆组件配置失败: {e}", exc_info=True)
            return False

# 全局变量用于控制服务器关闭
shutdown_event = asyncio.Event()
# 全局变量控制是否响应退出请求
enable_shutdown = False
# 全局变量用于管理correction任务
correction_tasks = {}  # {lanlan_name: asyncio.Task}
correction_cancel_flags = {}  # {lanlan_name: asyncio.Event}

@app.post("/shutdown")
async def shutdown_memory_server():
    """接收来自main_server的关闭信号"""
    global enable_shutdown
    if not enable_shutdown:
        logger.warning("收到关闭信号，但当前模式不允许响应退出请求")
        return {"status": "shutdown_disabled", "message": "当前模式不允许响应退出请求"}
    
    try:
        logger.info("收到来自main_server的关闭信号")
        shutdown_event.set()
        return {"status": "shutdown_signal_received"}
    except Exception as e:
        logger.error(f"处理关闭信号时出错: {e}")
        return {"status": "error", "message": str(e)}

@app.on_event("shutdown")
async def shutdown_event_handler():
    """应用关闭时执行清理工作"""
    logger.info("Memory server正在关闭...")
    # 这里可以添加任何需要的清理工作
    logger.info("Memory server已关闭")


async def _run_review_in_background(lanlan_name: str):
    """在后台运行review_history，支持取消"""
    global correction_tasks, correction_cancel_flags
    
    # 获取该角色的取消标志
    cancel_event = correction_cancel_flags.get(lanlan_name)
    if not cancel_event:
        cancel_event = asyncio.Event()
        correction_cancel_flags[lanlan_name] = cancel_event
    
    try:
        # 直接异步调用review_history方法
        await recent_history_manager.review_history(lanlan_name, cancel_event)
        logger.info(f"✅ {lanlan_name} 的记忆整理任务完成")
    except asyncio.CancelledError:
        logger.info(f"⚠️ {lanlan_name} 的记忆整理任务被取消")
    except Exception as e:
        logger.error(f"❌ {lanlan_name} 的记忆整理任务出错: {e}")
    finally:
        # 清理任务记录
        if lanlan_name in correction_tasks:
            del correction_tasks[lanlan_name]
        # 重置取消标志
        if lanlan_name in correction_cancel_flags:
            correction_cancel_flags[lanlan_name].clear()

@app.post("/cache/{lanlan_name}")
async def cache_conversation(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    """轻量级缓存：仅将新消息追加到 recent history，不触发 time_manager / review 等 LLM 操作。
    供 cross_server 在每轮 turn end 时调用，保持 memory_browser 实时可见。"""
    try:
        input_history = convert_to_messages(json.loads(request.input_history))
        if not input_history:
            return {"status": "cached", "count": 0}
        logger.info(f"[MemoryServer] cache: {lanlan_name} +{len(input_history)} 条消息")
        await recent_history_manager.update_history(input_history, lanlan_name, compress=False)
        return {"status": "cached", "count": len(input_history)}
    except Exception as e:
        logger.error(f"[MemoryServer] cache 失败: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/process/{lanlan_name}")
async def process_conversation(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    global correction_tasks
    try:
        # 检查角色是否存在于配置中，如果不存在则记录信息但继续处理（允许新角色）
        try:
            character_data = _config_manager.load_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            if lanlan_name not in catgirl_names:
                logger.info(f"[MemoryServer] 角色 '{lanlan_name}' 不在配置中，但继续处理（可能是新创建的角色）")
        except Exception as e:
            logger.warning(f"检查角色配置失败: {e}，继续处理")
        
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        # input_history 始终代表 /process 的完整处理范围，供 time index / review 使用。
        # recent_input_history 只代表这次还需要补写到 recent history 的增量；
        # 如果调用方未显式传入，则退回旧行为，仍用完整 history 更新 recent。
        recent_input_history = input_history
        if request.recent_input_history is not None:
            recent_input_history = convert_to_messages(json.loads(request.recent_input_history))
        logger.info(f"[MemoryServer] 收到 {lanlan_name} 的对话历史处理请求，消息数: {len(input_history)}，recent增量: {len(recent_input_history)}")
        if recent_input_history:
            await recent_history_manager.update_history(recent_input_history, lanlan_name)
        """
        下面屏蔽了两个模块，因为这两个模块需要消耗token，但当前版本实用性近乎于0。尤其是，Qwen与GPT等旗舰模型相比性能差距过大。
        """
        # await settings_manager.extract_and_update_settings(input_history, lanlan_name)
        # await semantic_manager.store_conversation(uid, input_history, lanlan_name)
        await time_manager.store_conversation(uid, input_history, lanlan_name)
        
        # 在后台启动review_history任务
        if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
            # 如果已有任务在运行，取消它
            correction_tasks[lanlan_name].cancel()
            try:
                await correction_tasks[lanlan_name]
            except asyncio.CancelledError:
                pass
        
        # 启动新的review任务
        task = asyncio.create_task(_run_review_in_background(lanlan_name))
        correction_tasks[lanlan_name] = task
        
        return {"status": "processed"}
    except Exception as e:
        logger.error(f"处理对话历史失败: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/renew/{lanlan_name}")
async def process_conversation_for_renew(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    global correction_tasks
    try:
        # 检查角色是否存在于配置中，如果不存在则记录信息但继续处理（允许新角色）
        try:
            character_data = _config_manager.load_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            if lanlan_name not in catgirl_names:
                logger.info(f"[MemoryServer] renew: 角色 '{lanlan_name}' 不在配置中，但继续处理（可能是新创建的角色）")
        except Exception as e:
            logger.warning(f"检查角色配置失败: {e}，继续处理")
        
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        logger.info(f"[MemoryServer] renew: 收到 {lanlan_name} 的对话历史处理请求，消息数: {len(input_history)}")
        await recent_history_manager.update_history(input_history, lanlan_name, detailed=True)
        # await settings_manager.extract_and_update_settings(input_history, lanlan_name)
        # await semantic_manager.store_conversation(uid, input_history, lanlan_name)
        await time_manager.store_conversation(uid, input_history, lanlan_name)
        
        # 在后台启动review_history任务
        if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
            # 如果已有任务在运行，取消它
            correction_tasks[lanlan_name].cancel()
            try:
                await correction_tasks[lanlan_name]
            except asyncio.CancelledError:
                pass
        
        # 启动新的review任务
        task = asyncio.create_task(_run_review_in_background(lanlan_name))
        correction_tasks[lanlan_name] = task
        
        return {"status": "processed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/get_recent_history/{lanlan_name}")
def get_recent_history(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    # 检查角色是否存在于配置中
    try:
        character_data = _config_manager.load_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空历史记录")
            return "开始聊天前，没有历史记录。\n"
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return "开始聊天前，没有历史记录。\n"
    
    history = recent_history_manager.get_recent_history(lanlan_name)
    _, _, _, _, name_mapping, _, _, _, _, _ = _config_manager.get_character_data()
    name_mapping['ai'] = lanlan_name
    result = f"开始聊天前，{lanlan_name}又在脑海内整理了近期发生的事情。\n"
    for i in history:
        if i.type == 'system':
            result += i.content + "\n"
        else:
            texts = [j['text'] for j in i.content if j['type']=='text']
            joined = "\n".join(texts)
            result += f"{name_mapping[i.type]} | {joined}\n"
    return result

@app.get("/search_for_memory/{lanlan_name}/{query}")
async def get_memory(query: str, lanlan_name:str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    return await semantic_manager.query(query, lanlan_name)

@app.get("/get_settings/{lanlan_name}")
def get_settings(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    # 检查角色是否存在于配置中
    try:
        character_data = _config_manager.load_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空设置")
            return f"{lanlan_name}记得{{}}"
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return f"{lanlan_name}记得{{}}"
    
    result = f"{lanlan_name}记得{json.dumps(settings_manager.get_settings(lanlan_name), ensure_ascii=False)}"
    return result

@app.post("/reload")
async def reload_config():
    """重新加载记忆服务器配置（用于新角色创建后）"""
    try:
        success = await reload_memory_components()
        if success:
            return {"status": "success", "message": "配置已重新加载"}
        else:
            return {"status": "error", "message": "配置重新加载失败"}
    except Exception as e:
        logger.error(f"重新加载配置时出错: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

@app.post("/cancel_correction/{lanlan_name}")
async def cancel_correction(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    """中断指定角色的记忆整理任务（用于记忆编辑后立即生效）"""
    global correction_tasks, correction_cancel_flags
    
    if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
        logger.info(f"🛑 收到取消请求，中断 {lanlan_name} 的correction任务")
        
        if lanlan_name in correction_cancel_flags:
            correction_cancel_flags[lanlan_name].set()
        
        correction_tasks[lanlan_name].cancel()
        try:
            await correction_tasks[lanlan_name]
        except asyncio.CancelledError:
            logger.info(f"✅ {lanlan_name} 的correction任务已成功中断")
        except Exception as e:
            logger.warning(f"⚠️ 中断 {lanlan_name} 的correction任务时出现异常: {e}")
        
        return {"status": "cancelled"}
    
    return {"status": "no_task"}

@app.get("/new_dialog/{lanlan_name}")
async def new_dialog(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    global correction_tasks, correction_cancel_flags
    
    # 检查角色是否存在于配置中
    try:
        character_data = _config_manager.load_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空上下文")
            return PlainTextResponse("")
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return PlainTextResponse("")
    
    # 中断正在进行的correction任务
    if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
        logger.info(f"🛑 收到new_dialog请求，中断 {lanlan_name} 的correction任务")
        
        # 设置取消标志
        if lanlan_name in correction_cancel_flags:
            correction_cancel_flags[lanlan_name].set()
        
        # 取消任务
        correction_tasks[lanlan_name].cancel()
        try:
            await correction_tasks[lanlan_name]
        except asyncio.CancelledError:
            logger.info(f"✅ {lanlan_name} 的correction任务已成功中断")
        except Exception as e:
            logger.warning(f"⚠️ 中断 {lanlan_name} 的correction任务时出现异常: {e}")
    
    # 正则表达式：删除所有类型括号及其内容（包括[]、()、{}、<>、【】、（）等）
    brackets_pattern = re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
    master_name, _, _, _, name_mapping, _, _, _, _, _ = _config_manager.get_character_data()
    name_mapping['ai'] = lanlan_name
    _lang = get_global_language()
    result = (
        _loc(INNER_THOUGHTS_HEADER, _lang).format(name=lanlan_name)
        + _loc(INNER_THOUGHTS_BODY, _lang).format(
            name=lanlan_name,
            master=master_name,
            settings=json.dumps(settings_manager.get_settings(lanlan_name), ensure_ascii=False),
            time=get_timestamp(),
        )
    )
    for i in recent_history_manager.get_recent_history(lanlan_name):
        if isinstance(i.content, str):
            cleaned_content = brackets_pattern.sub('', i.content).strip()
            result += f"{name_mapping[i.type]} | {cleaned_content}\n"
        else:
            texts = [brackets_pattern.sub('', j['text']).strip() for j in i.content if j['type'] == 'text']
            result += f"{name_mapping[i.type]} | " + "\n".join(texts) + "\n"
    return PlainTextResponse(result)

if __name__ == "__main__":
    import threading
    import time
    import signal
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Memory Server')
    parser.add_argument('--enable-shutdown', action='store_true', 
                       help='启用响应退出请求功能（仅在终端用户环境使用）')
    args = parser.parse_args()
    
    # 设置全局变量
    enable_shutdown = args.enable_shutdown
    
    # 创建一个后台线程来监控关闭信号
    def monitor_shutdown():
        while not shutdown_event.is_set():
            time.sleep(0.1)
        logger.info("检测到关闭信号，正在关闭memory_server...")
        # 发送SIGTERM信号给当前进程
        os.kill(os.getpid(), signal.SIGTERM)
    
    # 只有在启用关闭功能时才启动监控线程
    if enable_shutdown:
        shutdown_monitor = threading.Thread(target=monitor_shutdown, daemon=True)
        shutdown_monitor.start()
    
    # 启动服务器
    uvicorn.run(app, host="127.0.0.1", port=MEMORY_SERVER_PORT)