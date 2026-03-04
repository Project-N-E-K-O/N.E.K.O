# 音乐路由
import re
from typing import Dict, Optional
from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

# 导入分离出去的爬虫类
from utils.music_crawlers import fetch_music_content

router = APIRouter()
from utils.logger_config import get_module_logger
logger = get_module_logger(__name__, "Music")

@router.get("/api/music/search")
async def search_music(query: str):
    """
    智能音乐分发路由，统一调用 music_crawlers 中的 fetch_music_content。
    """
    logger.info(f"[音乐API] 收到搜索请求: '{query}'")
    
    # 直接调用重构后的主函数
    results = await fetch_music_content(keyword=query, limit=1)
    
    # fetch_music_content 已经返回了所需的格式
    return results