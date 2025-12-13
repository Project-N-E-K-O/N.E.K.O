"""
Web scraper for fetching trending content from Bilibili and Weibo
Also supports fetching active window title and Baidu search
"""
import asyncio
import httpx
import random
import re
import platform
from typing import Dict, List, Any, Optional
import logging
from urllib.parse import quote

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
        logger.error("获取B站首页推荐超时")
        return {
            'success': False,
            'error': '请求超时'
        }
    except Exception as e:
        logger.error(f"获取B站首页推荐失败: {e}")
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
        logger.error("获取微博热议话题超时")
        return {
            'success': False,
            'error': '请求超时'
        }
    except Exception as e:
        logger.error(f"获取微博热议话题失败: {e}")
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
            view = video.get('view', 0)
            like = video.get('like', 0)
            
            # 格式化播放量和点赞数
            view_str = f"{view//10000}万" if view >= 10000 else str(view)
            like_str = f"{like//10000}万" if like >= 10000 else str(like)
            
            output_lines.append(f"{i}. {title}")
            output_lines.append(f"   UP主: {author} | 播放: {view_str} | 点赞: {like_str}")
        
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


def get_active_window_title() -> Optional[str]:
    """
    获取当前活跃窗口的标题（仅支持Windows）
    
    Returns:
        窗口标题字符串，如果获取失败则返回None
    """
    if platform.system() != 'Windows':
        logger.warning("获取活跃窗口标题仅支持Windows系统")
        return None
    
    try:
        import pygetwindow as gw
        active_window = gw.getActiveWindow()
        if active_window:
            title = active_window.title
            logger.info(f"获取到活跃窗口标题: {title}")
            return title
        else:
            logger.warning("没有找到活跃窗口")
            return None
    except Exception as e:
        logger.error(f"获取活跃窗口标题失败: {e}")
        return None


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
        logger.error("百度搜索超时")
        return {
            'success': False,
            'error': '搜索超时'
        }
    except Exception as e:
        logger.error(f"百度搜索失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def parse_baidu_results(html_content: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    解析百度搜索结果页面
    
    Args:
        html_content: HTML页面内容
        limit: 结果数量限制
    
    Returns:
        搜索结果列表
    """
    results = []
    
    # 清理HTML标签的辅助函数
    def clean_html(text):
        if not text:
            return ""
        # 移除HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        # 移除多余空白
        text = ' '.join(text.split())
        # 解码HTML实体
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&amp;', '&')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        return text.strip()
    
    try:
        # 方法1: 尝试提取 c-container 中的内容
        # 百度搜索结果的主要容器
        container_pattern = r'<div[^>]*class="[^"]*c-container[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>'
        containers = re.findall(container_pattern, html_content, re.DOTALL | re.IGNORECASE)
        
        for container in containers[:limit * 2]:
            # 从容器中提取标题
            title_match = re.search(r'<a[^>]*>(.*?)</a>', container, re.DOTALL)
            if title_match:
                title = clean_html(title_match.group(1))
                if title and len(title) > 5 and len(title) < 200:
                    # 提取摘要
                    abstract = ""
                    abstract_match = re.search(r'<span[^>]*class="[^"]*content-right[^"]*"[^>]*>(.*?)</span>', container, re.DOTALL)
                    if not abstract_match:
                        abstract_match = re.search(r'<span[^>]*>([\u4e00-\u9fa5].*?)</span>', container, re.DOTALL)
                    if abstract_match:
                        abstract = clean_html(abstract_match.group(1))
                    
                    if not any(skip in title.lower() for skip in ['百度', '广告', 'javascript']):
                        results.append({
                            'title': title,
                            'abstract': abstract[:200] if abstract else ''
                        })
                        if len(results) >= limit:
                            break
        
        # 方法2: 如果方法1没有结果，尝试更通用的模式
        if not results:
            # 提取所有 h3 标题中的链接
            h3_pattern = r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>.*?</h3>'
            titles = re.findall(h3_pattern, html_content, re.DOTALL | re.IGNORECASE)
            
            for title in titles[:limit]:
                cleaned_title = clean_html(title)
                if cleaned_title and len(cleaned_title) > 5 and len(cleaned_title) < 200:
                    if not any(skip in cleaned_title.lower() for skip in ['百度', '广告', '登录', '更多']):
                        results.append({
                            'title': cleaned_title,
                            'abstract': ''
                        })
        
        # 方法3: 提取 result-op 类型的结果（百度特殊结果）
        if not results:
            result_op_pattern = r'<div[^>]*class="[^"]*result-op[^"]*"[^>]*>.*?<h3[^>]*>.*?<a[^>]*>(.*?)</a>'
            op_titles = re.findall(result_op_pattern, html_content, re.DOTALL | re.IGNORECASE)
            
            for title in op_titles[:limit]:
                cleaned_title = clean_html(title)
                if cleaned_title and len(cleaned_title) > 5:
                    results.append({
                        'title': cleaned_title,
                        'abstract': ''
                    })
        
        # 方法4: 最宽松的模式 - 提取所有看起来像标题的链接
        if not results:
            all_links_pattern = r'<a[^>]*href="http[^"]*"[^>]*>([^<]{10,100})</a>'
            all_links = re.findall(all_links_pattern, html_content, re.IGNORECASE)
            
            seen = set()
            for link_text in all_links:
                cleaned = clean_html(link_text)
                if cleaned and cleaned not in seen and len(cleaned) > 10:
                    if not any(skip in cleaned.lower() for skip in ['百度', '广告', '登录', '设置', '更多', 'javascript', 'http', '下载', '安装']):
                        seen.add(cleaned)
                        results.append({
                            'title': cleaned,
                            'abstract': ''
                        })
                        if len(results) >= limit:
                            break
        
        logger.info(f"解析到 {len(results)} 条百度搜索结果")
        return results[:limit]
        
    except Exception as e:
        logger.error(f"解析百度搜索结果失败: {e}")
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
    """
    try:
        # 获取活跃窗口标题
        window_title = get_active_window_title()
        
        if not window_title:
            return {
                'success': False,
                'error': '无法获取当前活跃窗口标题'
            }
        
        # 清理窗口标题以获取搜索关键词
        search_query = clean_window_title(window_title)
        
        if not search_query or len(search_query) < 2:
            return {
                'success': False,
                'error': '窗口标题无法提取有效的搜索关键词',
                'window_title': window_title
            }
        
        logger.info(f"从窗口标题「{window_title}」提取搜索关键词: {search_query}")
        
        # 进行百度搜索
        search_result = await search_baidu(search_query, limit)
        
        return {
            'success': search_result.get('success', False),
            'window_title': window_title,
            'search_query': search_query,
            'search_results': search_result.get('results', []),
            'error': search_result.get('error') if not search_result.get('success') else None
        }
        
    except Exception as e:
        logger.error(f"获取窗口上下文内容失败: {e}")
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
    window_title = content.get('window_title', '')
    search_query = content.get('search_query', '')
    results = content.get('search_results', [])
    
    output_lines.append(f"【当前活跃窗口】{window_title}")
    output_lines.append(f"【搜索关键词】{search_query}")
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
