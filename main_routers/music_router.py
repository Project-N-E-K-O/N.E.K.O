# 音乐路由
import re
from typing import Dict, Optional
from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

# 导入分离出去的爬虫类
from utils.music_crawlers import fetch_music_content

router = APIRouter()
from utils.logger_config import get_module_logger
logger = get_module_logger(__name__, "Music")

@router.get("/api/music/search")
async def search_music(query: str = Query(default="", min_length=0, max_length=200)):
    """
    智能音乐分发路由，统一调用 music_crawlers 中的 fetch_music_content。
    """
    query = query.strip()
    
    logger.info(f"[音乐API] 收到搜索请求: '{query}'")
    
    # 空白输入校验
    if not query:
        logger.warning("[音乐API] 搜索关键词为空，返回失败结果")
        return {
            "success": False,  # 【核心修复】标记为失败
            "data": [],
            "error": "搜索关键词不能为空",  # 填入 error 字段方便前端捕获
            "message": "搜索关键词不能为空"
        }
    
    # 异常保护
    try:
        results = await fetch_music_content(keyword=query, limit=1)
        
        if results.get('success'):
            track_count = len(results.get('data', []))
            logger.info(f"[音乐API] 搜索成功，返回 {track_count} 首音乐")
        else:
            error = results.get('error', '未知错误')
            logger.warning(f"[音乐API] 搜索失败: {error}")
            # 统一失败返回结构
            return {
                "success": False,
                "data": [],
                "error": error,
                "message": results.get("message") or error or "音乐搜索失败"
            }
        
        return results
        
    except Exception as e:
        logger.error(f"[音乐API] 搜索异常: {type(e).__name__}: {e}")
        return {
            "success": False,
            "data": [],
            "error": "MUSIC_SEARCH_ERROR",
            "message": "音乐搜索服务异常，请稍后重试"
        }