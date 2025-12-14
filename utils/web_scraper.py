"""
Web scraper for fetching trending content from Bilibili and Weibo
Also supports fetching active window title and Baidu search
"""
import asyncio
import httpx
import random
import re
import platform
from typing import Dict, List, Any, Optional, Union
import logging
from urllib.parse import quote
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)

# User-Agent池，随机选择以避免被识别
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
]

def get_random_user_agent() -> str:
    """随机获取一个User-Agent"""
    return random.choice(USER_AGENTS)


async def fetch_bilibili_trending(limit: int = 10) -> Dict[str, Any]:
    """
    获取B站首页推荐视频
    使用B站的首页推荐API
    """
    try:
        # B站首页推荐API
        url = "https://api.bilibili.com/x/web-interface/index/top/feed/rcmd"
        params = {
            "ps": limit,  # 每页数量
            "fresh_type": 3,  # 刷新类型
        }
        
        # 添加完整的headers来模拟浏览器请求
        headers = {
            'User-Agent': get_random_user_agent(),
            'Referer': 'https://www.bilibili.com',
            'Origin': 'https://www.bilibili.com',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'DNT': '1',
        }
        
        # 添加随机延迟，避免请求过快
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 0:
                videos = []
                items = data.get('data', {}).get('item', [])
                for item in items[:limit]:
                    videos.append({
                        'title': item.get('title', ''),
                        'desc': item.get('desc', ''),
                        'author': item.get('owner', {}).get('name', ''),
                        'view': item.get('stat', {}).get('view', 0),
                        'like': item.get('stat', {}).get('like', 0),
                        'bvid': item.get('bvid', '')
                    })
                
                return {
                    'success': True,
                    'videos': videos
                }
            else:
                logger.error(f"B站API返回错误: {data.get('message', '未知错误')}")
                return {
                    'success': False,
                    'error': data.get('message', '未知错误')
                }
                
    except httpx.TimeoutException:
        logger.exception("获取B站首页推荐超时")
        return {
            'success': False,
            'error': '请求超时'
        }
    except Exception as e:
        logger.exception(f"获取B站首页推荐失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


async def fetch_weibo_trending(limit: int = 10) -> Dict[str, Any]:
    """
    获取微博热议话题
    使用微博热搜榜API（作为首页热议内容的替代）
    """
    try:
        # 微博热搜榜API（公开接口）
        url = "https://weibo.com/ajax/side/hotSearch"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Referer': 'https://weibo.com',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        }
        
        # 添加随机延迟，避免请求过快
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if data.get('ok') == 1:
                trending_list = []
                realtime_list = data.get('data', {}).get('realtime', [])
                
                for item in realtime_list[:limit]:
                    # 跳过广告
                    if item.get('is_ad'):
                        continue
                    
                    trending_list.append({
                        'word': item.get('word', ''),
                        'raw_hot': item.get('raw_hot', 0),
                        'note': item.get('note', ''),
                        'rank': item.get('rank', 0)
                    })
                
                return {
                    'success': True,
                    'trending': trending_list[:limit]
                }
            else:
                logger.error(f"微博API返回错误")
                return {
                    'success': False,
                    'error': '微博API返回错误'
                }
                
    except httpx.TimeoutException:
        logger.exception("获取微博热议话题超时")
        return {
            'success': False,
            'error': '请求超时'
        }
    except Exception as e:
        logger.exception(f"获取微博热议话题失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


async def fetch_trending_content(bilibili_limit: int = 10, weibo_limit: int = 10) -> Dict[str, Any]:
    """
    并发获取B站首页推荐和微博热议话题
    
    Args:
        bilibili_limit: B站视频数量限制
        weibo_limit: 微博热议话题数量限制
    
    Returns:
        包含成功状态、B站首页视频和微博热议话题的字典
    """
    try:
        # 并发请求
        bilibili_task = fetch_bilibili_trending(bilibili_limit)
        weibo_task = fetch_weibo_trending(weibo_limit)
        
        bilibili_result, weibo_result = await asyncio.gather(
            bilibili_task, 
            weibo_task,
            return_exceptions=True
        )
        
        # 处理异常情况
        if isinstance(bilibili_result, Exception):
            logger.error(f"B站爬取异常: {bilibili_result}")
            bilibili_result = {'success': False, 'error': str(bilibili_result)}
        
        if isinstance(weibo_result, Exception):
            logger.error(f"微博爬取异常: {weibo_result}")
            weibo_result = {'success': False, 'error': str(weibo_result)}
        
        # 检查是否至少有一个成功
        if not bilibili_result.get('success') and not weibo_result.get('success'):
            return {
                'success': False,
                'error': '无法获取任何热门内容',
                'bilibili': bilibili_result,
                'weibo': weibo_result
            }
        
        return {
            'success': True,
            'bilibili': bilibili_result,
            'weibo': weibo_result
        }
        
    except Exception as e:
        logger.error(f"获取热门内容失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def format_trending_content(trending_content: Dict[str, Any]) -> str:
    """
    格式化首页推荐内容为可读的字符串
    
    Args:
        trending_content: fetch_trending_content返回的结果
    
    Returns:
        格式化后的字符串
    """
    output_lines = []
    
    # 格式化B站内容
    bilibili_data = trending_content.get('bilibili', {})
    if bilibili_data.get('success'):
        output_lines.append("【B站首页推荐】")
        videos = bilibili_data.get('videos', [])
        
        for i, video in enumerate(videos[:5], 1):  # 只取前5个
            title = video.get('title', '')
            author = video.get('author', '')
            like = video.get('like', 0)
            
            # 格式化点赞数
            like_str = f"{like//10000}万" if like >= 10000 else str(like)
            
            output_lines.append(f"{i}. {title}")
            output_lines.append(f"   UP主: {author} | 点赞: {like_str}")
        
        output_lines.append("")  # 空行
    
    # 格式化微博内容
    weibo_data = trending_content.get('weibo', {})
    if weibo_data.get('success'):
        output_lines.append("【微博热议话题】")
        trending_list = weibo_data.get('trending', [])
        
        for i, item in enumerate(trending_list[:5], 1):  # 只取前5个
            word = item.get('word', '')
            hot = item.get('raw_hot', 0)
            note = item.get('note', '')
            
            # 格式化热度
            hot_str = f"{hot//10000}万" if hot >= 10000 else str(hot)
            
            line = f"{i}. {word} (热度: {hot_str})"
            if note:
                line += f" [{note}]"
            output_lines.append(line)
    
    if not output_lines:
        return "暂时无法获取推荐内容"
    
    return "\n".join(output_lines)


def get_active_window_title(include_raw: bool = False) -> Optional[Union[str, Dict[str, str]]]:
    """
    获取当前活跃窗口的标题（仅支持Windows）
    
    Args:
        include_raw: 是否返回原始标题。默认False，仅返回截断后的安全标题。
                     设为True时返回包含sanitized和raw的字典。
    
    Returns:
        默认情况：截断后的安全标题字符串（前30字符），失败返回None
        include_raw=True时：{'sanitized': '截断标题', 'raw': '完整标题'}，失败返回None
    """
    if platform.system() != 'Windows':
        logger.warning("获取活跃窗口标题仅支持Windows系统")
        return None
    
    try:
        import pygetwindow as gw
    except ImportError:
        logger.error("pygetwindow模块未安装。在Windows系统上请安装: pip install pygetwindow")
        return None
    
    try:
        active_window = gw.getActiveWindow()
        if active_window:
            raw_title = active_window.title
            # 截断标题以避免记录敏感信息
            sanitized_title = raw_title[:30] + '...' if len(raw_title) > 30 else raw_title
            logger.info(f"获取到活跃窗口标题: {sanitized_title}")
            
            if include_raw:
                return {
                    'sanitized': sanitized_title,
                    'raw': raw_title
                }
            else:
                return sanitized_title
        else:
            logger.warning("没有找到活跃窗口")
            return None
    except Exception as e:
        logger.exception(f"获取活跃窗口标题失败: {e}")
        return None


async def generate_diverse_queries(window_title: str) -> List[str]:
    """
    使用LLM基于窗口标题生成3个多样化的搜索关键词
    
    Args:
        window_title: 窗口标题（应该是已清理的标题，不应包含敏感信息）
    
    Returns:
        包含3个搜索关键词的列表
    
    注意：
        为保护隐私，调用此函数前应先使用clean_window_title()清理标题，
        避免将文件路径、账号等敏感信息发送给LLM API
    """
    try:
        # 导入配置管理器
        from utils.config_manager import ConfigManager
        config_manager = ConfigManager()
        
        # 使用correction模型配置（或者使用emotion等轻量级模型）
        correction_config = config_manager.get_model_api_config('correction')
        
        llm = ChatOpenAI(
            model=correction_config['model'],
            base_url=correction_config['base_url'],
            api_key=correction_config['api_key'],
            temperature=0.8,  # 提高temperature以获得更多样化的结果
            timeout=10.0
        )
        
        # 清理/脱敏窗口标题用于日志显示
        sanitized_title = window_title[:30] + '...' if len(window_title) > 30 else window_title
        
        prompt = f"""基于以下窗口标题，生成3个不同的搜索关键词，用于在百度上搜索相关内容。

窗口标题：{window_title}

要求：
1. 生成3个不同角度的搜索关键词
2. 关键词应该简洁（2-8个字）
3. 关键词应该多样化，涵盖不同方面
4. 只输出3个关键词，每行一个，不要添加任何序号、标点或其他内容

示例输出格式：
关键词1
关键词2
关键词3"""

        # 使用异步调用
        response = await llm.ainvoke([SystemMessage(content=prompt)])
        
        # 解析响应，提取3个关键词
        queries = []
        lines = response.content.strip().split('\n')
        for line in lines:
            line = line.strip()
            # 移除可能的序号、标点等
            line = re.sub(r'^[\d\.\-\*\)\]】]+\s*', '', line)
            line = line.strip('.,;:，。；：')
            if line and len(line) >= 2:
                queries.append(line)
                if len(queries) >= 3:
                    break
        
        # 如果生成的查询不足3个，用原始标题填充
        if len(queries) < 3:
            clean_title = clean_window_title(window_title)
            while len(queries) < 3 and clean_title:
                queries.append(clean_title)
        
        # 使用脱敏后的标题记录日志
        logger.info(f"为窗口标题「{sanitized_title}」生成的查询关键词: {queries}")
        return queries[:3]
        
    except Exception as e:
        # 异常日志中也使用脱敏标题
        sanitized_title = window_title[:30] + '...' if len(window_title) > 30 else window_title
        logger.warning(f"为窗口标题「{sanitized_title}」生成多样化查询失败，使用默认清理方法: {e}")
        # 失败时回退到原始清理方法
        clean_title = clean_window_title(window_title)
        return [clean_title, clean_title, clean_title]


def clean_window_title(title: str) -> str:
    """
    清理窗口标题，提取有意义的搜索关键词
    
    Args:
        title: 原始窗口标题
    
    Returns:
        清理后的搜索关键词
    """
    if not title:
        return ""
    
    # 移除常见的应用程序后缀和无意义内容
    patterns_to_remove = [
        r'\s*[-–—]\s*(Google Chrome|Mozilla Firefox|Microsoft Edge|Opera|Safari|Brave).*$',
        r'\s*[-–—]\s*(Visual Studio Code|VS Code|VSCode).*$',
        r'\s*[-–—]\s*(记事本|Notepad\+*|Sublime Text|Atom).*$',
        r'\s*[-–—]\s*(Microsoft Word|Excel|PowerPoint).*$',
        r'\s*[-–—]\s*(QQ音乐|网易云音乐|酷狗音乐|Spotify).*$',
        r'\s*[-–—]\s*(哔哩哔哩|bilibili|YouTube|优酷|爱奇艺|腾讯视频).*$',
        r'\s*[-–—]\s*\d+\s*$',  # 移除末尾的数字（如页码）
        r'^\*\s*',  # 移除开头的星号（未保存标记）
        r'\s*\[.*?\]\s*$',  # 移除方括号内容
        r'\s*\(.*?\)\s*$',  # 移除圆括号内容
        r'https?://\S+',  # 移除URL
        r'www\.\S+',  # 移除www开头的网址
        r'\.py\s*$',  # 移除.py后缀
        r'\.js\s*$',  # 移除.js后缀
        r'\.html?\s*$',  # 移除.html后缀
        r'\.css\s*$',  # 移除.css后缀
        r'\.md\s*$',  # 移除.md后缀
        r'\.txt\s*$',  # 移除.txt后缀
        r'\.json\s*$',  # 移除.json后缀
    ]
    
    cleaned = title
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # 移除多余空格
    cleaned = ' '.join(cleaned.split())
    
    # 如果清理后太短或为空，返回原标题的一部分
    if len(cleaned) < 3:
        # 尝试提取原标题中的第一个有意义的部分
        parts = re.split(r'\s*[-–—|]\s*', title)
        if parts and len(parts[0]) >= 3:
            cleaned = parts[0].strip()
    
    return cleaned[:100]  # 限制长度


async def search_baidu(query: str, limit: int = 5) -> Dict[str, Any]:
    """
    使用百度搜索关键词并获取搜索结果
    
    Args:
        query: 搜索关键词
        limit: 返回结果数量限制
    
    Returns:
        包含搜索结果的字典
    """
    try:
        if not query or len(query.strip()) < 2:
            return {
                'success': False,
                'error': '搜索关键词太短'
            }
        
        # 清理查询词
        query = query.strip()
        encoded_query = quote(query)
        
        # 百度搜索URL
        url = f"https://www.baidu.com/s?wd={encoded_query}"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Referer': 'https://www.baidu.com/',
            'DNT': '1',
            'Cache-Control': 'no-cache',
        }
        
        # 添加随机延迟
        await asyncio.sleep(random.uniform(0.2, 0.5))
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html_content = response.text
            
            # 解析搜索结果
            results = parse_baidu_results(html_content, limit)
            
            if results:
                return {
                    'success': True,
                    'query': query,
                    'results': results
                }
            else:
                return {
                    'success': False,
                    'error': '未能解析到搜索结果',
                    'query': query
                }
                
    except httpx.TimeoutException:
        logger.exception("百度搜索超时")
        return {
            'success': False,
            'error': '搜索超时'
        }
    except Exception as e:
        logger.exception(f"百度搜索失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }

from bs4 import BeautifulSoup

def parse_baidu_results(html_content: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    解析百度搜索结果页面
    
    Args:
        html_content: HTML页面内容
        limit: 结果数量限制
    
    Returns:
        搜索结果列表，每个结果包含 title, abstract, url
    """
    results = []
    
    try:
        from urllib.parse import urljoin
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 提取搜索结果容器
        containers = soup.find_all('div', class_=lambda x: x and 'c-container' in x, limit=limit * 2)
        
        for container in containers:
            # 提取标题和链接
            link = container.find('a')
            if link:
                title = link.get_text(strip=True)
                if title and 5 < len(title) < 200:
                    # 提取 URL（处理相对和绝对 URL）
                    href = link.get('href', '')
                    if href:
                        # 如果是相对 URL，转换为绝对 URL
                        if href.startswith('/'):
                            url = urljoin('https://www.baidu.com', href)
                        elif not href.startswith('http'):
                            url = urljoin('https://www.baidu.com/', href)
                        else:
                            url = href
                    else:
                        url = ''
                    
                    # 提取摘要
                    abstract = ""
                    content_span = container.find('span', class_=lambda x: x and 'content-right' in x)
                    if content_span:
                        abstract = content_span.get_text(strip=True)[:200]
                    
                    if not any(skip in title.lower() for skip in ['百度', '广告', 'javascript']):
                        results.append({
                            'title': title,
                            'abstract': abstract,
                            'url': url
                        })
                        if len(results) >= limit:
                            break
        
        # 如果没找到结果，尝试提取 h3 标题
        if not results:
            h3_links = soup.find_all('h3')
            for h3 in h3_links[:limit]:
                link = h3.find('a')
                if link:
                    title = link.get_text(strip=True)
                    if title and 5 < len(title) < 200:
                        # 提取 URL
                        href = link.get('href', '')
                        if href:
                            if href.startswith('/'):
                                url = urljoin('https://www.baidu.com', href)
                            elif not href.startswith('http'):
                                url = urljoin('https://www.baidu.com/', href)
                            else:
                                url = href
                        else:
                            url = ''
                        
                        results.append({
                            'title': title,
                            'abstract': '',
                            'url': url
                        })
        
        logger.info(f"解析到 {len(results)} 条百度搜索结果")
        return results[:limit]
        
    except Exception as e:
        logger.exception(f"解析百度搜索结果失败: {e}")
        return []


def format_baidu_search_results(search_result: Dict[str, Any]) -> str:
    """
    格式化百度搜索结果为可读字符串
    
    Args:
        search_result: search_baidu返回的结果
    
    Returns:
        格式化后的字符串
    """
    if not search_result.get('success'):
        return f"搜索失败: {search_result.get('error', '未知错误')}"
    
    output_lines = []
    query = search_result.get('query', '')
    results = search_result.get('results', [])
    
    output_lines.append(f"【关于「{query}」的搜索结果】")
    output_lines.append("")
    
    for i, result in enumerate(results, 1):
        title = result.get('title', '')
        abstract = result.get('abstract', '')
        
        output_lines.append(f"{i}. {title}")
        if abstract:
            # 限制摘要长度
            abstract = abstract[:150] + '...' if len(abstract) > 150 else abstract
            output_lines.append(f"   {abstract}")
        output_lines.append("")
    
    if not results:
        output_lines.append("未找到相关结果")
    
    return "\n".join(output_lines)


async def fetch_window_context_content(limit: int = 5) -> Dict[str, Any]:
    """
    获取当前活跃窗口标题并进行百度搜索
    
    Args:
        limit: 搜索结果数量限制
    
    Returns:
        包含窗口标题和搜索结果的字典
        注意：返回的window_title_sanitized是截断版本，window_title_raw仅用于内部搜索
    """
    try:
        # 获取活跃窗口标题（同时获取原始和截断版本）
        title_result = get_active_window_title(include_raw=True)
        
        if not title_result:
            return {
                'success': False,
                'error': '无法获取当前活跃窗口标题'
            }
        
        sanitized_title = title_result['sanitized']
        raw_title = title_result['raw']
        
        # 清理窗口标题以移除敏感信息，避免将原始标题发送给LLM API
        # 这样可以防止文件路径、账号信息等敏感数据泄露
        cleaned_title = clean_window_title(raw_title)
        
        # 使用清理后的标题生成3个多样化的搜索查询（保护隐私）
        search_queries = await generate_diverse_queries(cleaned_title)
        
        if not search_queries or all(not q or len(q) < 2 for q in search_queries):
            return {
                'success': False,
                'error': '窗口标题无法提取有效的搜索关键词',
                'window_title': sanitized_title  # 返回截断版本
            }
        
        # 日志中使用截断版本
        logger.info(f"从窗口标题「{sanitized_title}」生成多样化查询: {search_queries}")
        
        # 依次使用每个查询进行搜索，合并结果
        all_results = []
        successful_queries = []
        
        for query in search_queries:
            if not query or len(query) < 2:
                continue
                
            logger.info(f"使用查询关键词: {query}")
            search_result = await search_baidu(query, limit)
            
            if search_result.get('success') and search_result.get('results'):
                all_results.extend(search_result['results'])
                successful_queries.append(query)
        
        # 去重（基于URL，如果URL缺失则基于title）
        seen_keys = set()
        unique_results = []
        for result in all_results:
            url = result.get('url', '')
            title = result.get('title', '')
            
            # 优先使用 URL 进行去重，如果 URL 不存在则使用 title
            dedup_key = url if url else title
            
            if dedup_key and dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                unique_results.append(result)
        
        # 限制总结果数量
        unique_results = unique_results[:limit * 2]  # 多返回一些结果
        
        if not unique_results:
            return {
                'success': False,
                'error': '所有查询均未获得搜索结果',
                'window_title': sanitized_title,
                'search_queries': search_queries
            }
        
        return {
            'success': True,
            'window_title': sanitized_title,  # 默认返回截断版本
            'search_queries': successful_queries,  # 返回成功的查询列表
            'search_results': unique_results,
        }
        
    except Exception as e:
        logger.exception(f"获取窗口上下文内容失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def format_window_context_content(content: Dict[str, Any]) -> str:
    """
    格式化窗口上下文内容为可读字符串
    
    Args:
        content: fetch_window_context_content返回的结果
    
    Returns:
        格式化后的字符串
    """
    if not content.get('success'):
        return f"获取窗口上下文失败: {content.get('error', '未知错误')}"
    
    output_lines = []
    # window_title 现在是截断后的安全版本（来自fetch_window_context_content），不会泄露敏感信息
    window_title = content.get('window_title', '')
    search_queries = content.get('search_queries', [])
    results = content.get('search_results', [])
    
    output_lines.append(f"【当前活跃窗口】{window_title}")
    
    # 显示所有使用的搜索关键词
    if search_queries:
        if len(search_queries) == 1:
            output_lines.append(f"【搜索关键词】{search_queries[0]}")
        else:
            output_lines.append(f"【搜索关键词】{', '.join(search_queries)}")
    
    output_lines.append("")
    output_lines.append("【相关信息】")
    
    for i, result in enumerate(results, 1):
        title = result.get('title', '')
        abstract = result.get('abstract', '')
        
        output_lines.append(f"{i}. {title}")
        if abstract:
            abstract = abstract[:150] + '...' if len(abstract) > 150 else abstract
            output_lines.append(f"   {abstract}")
    
    if not results:
        output_lines.append("未找到相关信息")
    
    return "\n".join(output_lines)


# 测试用的主函数
async def main():
    """测试函数"""
    print("正在获取热门内容...")
    content = await fetch_trending_content(bilibili_limit=5, weibo_limit=5)
    
    if content['success']:
        formatted = format_trending_content(content)
        print("\n" + "="*50)
        print(formatted)
        print("="*50)
    else:
        print(f"获取失败: {content.get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
