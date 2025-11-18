# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import CompressedRecentHistoryManager, SemanticMemory, ImportantSettingsManager, TimeIndexedMemory
from fastapi import FastAPI
import json
import uvicorn
from langchain_core.messages import convert_to_messages
from uuid import uuid4
from config import MEMORY_SERVER_PORT
from utils.config_manager import get_config_manager
from pydantic import BaseModel
import re
import asyncio
import logging
import argparse

# Setup logger
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)

class HistoryRequest(BaseModel):
    input_history: str

app = FastAPI()

# 初始化组件
_config_manager = get_config_manager()
recent_history_manager = CompressedRecentHistoryManager()
semantic_manager = SemanticMemory(recent_history_manager)
settings_manager = ImportantSettingsManager()
time_manager = TimeIndexedMemory(recent_history_manager)

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

@app.post("/process/{lanlan_name}")
async def process_conversation(request: HistoryRequest, lanlan_name: str):
    global correction_tasks
    try:
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        await recent_history_manager.update_history(input_history, lanlan_name)
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
    global correction_tasks
    try:
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
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
    return await semantic_manager.query(query, lanlan_name)

@app.get("/get_settings/{lanlan_name}")
def get_settings(lanlan_name: str):
    result = f"{lanlan_name}记得{json.dumps(settings_manager.get_settings(lanlan_name), ensure_ascii=False)}"
    return result

@app.get("/new_dialog/{lanlan_name}")
async def new_dialog(lanlan_name: str):
    global correction_tasks, correction_cancel_flags
    
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
    result = f"\n========{lanlan_name}的内心活动========\n{lanlan_name}的脑海里经常想着自己和{master_name}的事情，她记得{json.dumps(settings_manager.get_settings(lanlan_name), ensure_ascii=False)}\n\n"
    result += f"开始聊天前，{lanlan_name}又在脑海内整理了近期发生的事情。\n"
    for i in recent_history_manager.get_recent_history(lanlan_name):
        if type(i.content) == str:
            cleaned_content = brackets_pattern.sub('', i.content).strip()
            result += f"{name_mapping[i.type]} | {cleaned_content}\n"
        else:
            texts = [brackets_pattern.sub('', j['text']).strip() for j in i.content if j['type'] == 'text']
            result += f"{name_mapping[i.type]} | " + "\n".join(texts) + "\n"
    return result

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
    uvicorn.run(app, host="0.0.0.0", port=MEMORY_SERVER_PORT)