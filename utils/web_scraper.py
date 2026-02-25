"""
网络爬虫模块，用于获取各平台的热门内容
支持基于区域的内容获取：
- 中文区域：B站和微博
- 非中文区域：Reddit和Twitter
同时支持获取活跃窗口标题和搜索功能
"""
import asyncio
import httpx
import random
import re
import platform
from typing import Dict, List, Any, Optional, Union
from urllib.parse import quote
from utils.logger_config import get_module_logger
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from bs4 import BeautifulSoup
import os
from pathlib import Path
import json
import sys

logger = get_module_logger(__name__)


def _fix_bilibili_api_env():
    """
    针对 Nuitka 打包环境的修复函数：
    在程序运行时检测并强制创建 bilibili_api 缺失的 data 目录和关键 JSON 配置文件。
    """
    logger.info("正在检查 Bilibili API 运行环境兼容性...")
    
    # 检查是否处于打包环境 (Nuitka 会定义 __nuitka_binary_dir)
    is_compiled = "__nuitka_binary_dir" in globals() or getattr(sys, 'frozen', False)
    
    try:
        import bilibili_api

        # 1. 定位 bilibili_api 库路径
        try:
            lib_path = os.path.dirname(bilibili_api.__file__)
            base_path = Path(lib_path)
            logger.info(f"检测到 bilibili_api 安装路径: {base_path}")
        except Exception as e:
            logger.warning(f"无法确定 bilibili_api 安装路径，尝试跳过修复: {e}")
            return

        data_dir = base_path / "data"

        # 2. 强制创建 data 目录
        if not data_dir.exists():
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"✅ 已补全缺失的 B站数据目录: {data_dir}")
            except Exception as e:
                logger.warning(f"❌ 无法创建数据目录 (可能是权限问题): {data_dir}, 错误: {e}")
                return
        else:
            logger.debug("B站数据目录已存在，检查配置文件...")

        # 3. 定义必须存在的配置文件及其默认内容
        # video_uploader_lines.json: 核心报错文件，必须是字典格式 {}
        # gevent_patch.json: 部分环境需要的补丁配置，通常是 {}
        missing_files = {
            "video_uploader_lines.json": {},
            "gevent_patch.json": {}
        }

        fixed_count = 0
        for file_name, default_content in missing_files.items():
            file_path = data_dir / file_name
            if not file_path.exists():
                try:
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(default_content, f)
                    logger.info(f"✅ 已强制补全缺失配置文件: {file_name}")
                    fixed_count += 1
                except Exception as e:
                    logger.warning(f"❌ 写入配置文件 {file_name} 失败: {e}")
            else:
                # 检查文件是否为空或损坏 (可选)
                try:
                    if file_path.stat().st_size == 0:
                        with open(file_path, "w", encoding="utf-8") as f:
                            json.dump(default_content, f)
                        logger.info(f"⚠️ 发现空文件 {file_name}，已重置为默认值")
                except Exception as e:
                    logger.warning(f"重置空文件 {file_name} 失败: {e}")

        if is_compiled:
            if fixed_count > 0:
                logger.info(f"打包环境修复完成，共修复 {fixed_count} 个资源文件。")
            else:
                logger.info("打包环境资源完整，无需修复。")

    except ImportError:
        logger.warning("未检测到 bilibili_api 库，跳过环境修复逻辑。")
    except Exception as e:
        # 最后的兜底，确保此函数无论如何不会导致主程序崩溃
        logger.warning(f"⚠️ 尝试自修复 B站 API 环境时发生非预期异常: {e}")

# 在模块加载时立即执行
_fix_bilibili_api_env()

# ==================================================
# 从 language_utils 导入区域检测功能
# ==================================================

try:
    from utils.language_utils import is_china_region
except ImportError:
    # 如果 language_utils 不可用，使用回退方案
    import locale
    def is_china_region() -> bool:
        """
        区域检测回退方案

        仅对中国大陆地区返回True（zh_cn及其变体）
        港澳台地区（zh_tw, zh_hk）返回False
        Windows 中文系统返回 True
        """
        mainland_china_locales = {'zh_cn', 'chinese_china', 'chinese_simplified_china'}
       
        def normalize_locale(loc: str) -> str:
            """标准化locale字符串：小写、替换连字符、去除编码"""
            if not loc:
                return ''
            loc = loc.lower()
            loc = loc.replace('-', '_')
            if '.' in loc:
                loc = loc.split('.')[0]
            return loc

        def check_locale(loc: str) -> bool:
            """检查标准化后的locale是否为中国大陆"""
            normalized = normalize_locale(loc)
            if not normalized:
                return False
            if normalized in mainland_china_locales:
                return True
            if normalized.startswith('zh_cn'):
                return True
            if 'chinese' in normalized and 'china' in normalized:
                return True
            return False

        try:
            try:
                system_locale = locale.getlocale()[0]
                if system_locale and check_locale(system_locale):
                    return True
            except Exception:
                pass

            try:
                default_locale = locale.getdefaultlocale()[0]
                if default_locale and check_locale(default_locale):
                    return True
            except Exception:
                pass

            return False
        except Exception:
            return False


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


def _get_bilibili_credential() -> Any | None:
    try:
        from bilibili_api import Credential
        cookies = _get_platform_cookies('bilibili')
        if not cookies:
            return None
        
        # 兼容原版逻辑，加入 buvid3 防止被 B站 API 风控拦截
        return Credential(
            sessdata=cookies.get('SESSDATA', ''),
            bili_jct=cookies.get('bili_jct', ''),
            buvid3=cookies.get('buvid3', ''),
            dedeuserid=cookies.get('DedeUserID', '')
        )
    except ImportError:
        logger.debug("bilibili_api 库未安装")
        return None
    except Exception as e:
        logger.debug(f"从文件加载认证信息失败: {e}")
    
    return None

# ==================================================
# 热门内容获取函数
# ==================================================

async def fetch_bilibili_trending(limit: int = 30) -> Dict[str, Any]:
    """
    获取B站首页推荐视频
    使用bilibili-api库获取主页视频推荐
    支持个性化推荐（如果提供了认证信息）
    """
    try:
        from bilibili_api import homepage
        
        # 获取认证信息（如果有）
        credential = _get_bilibili_credential()
        
        # 添加随机延迟，避免请求过快
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        # 使用bilibili-api获取首页推荐
        # 如果有credential，会获取个性化推荐；否则获取通用推荐
        result = await homepage.get_videos(credential=credential)
        
        videos = []
        # 安全地访问嵌套字典，避免 KeyError
        if result:
            # bilibili-api 返回的数据结构可能是 {'data': {'item': [...]}} 或直接 {'item': [...]}
            # 先尝试从 data 中获取，如果没有则直接获取
            data = result.get('data', result)
            items = data.get('item', [])
            
            for item in items:
                # 提取视频信息
                bvid = item.get('bvid', '')
                # 有些项目可能是广告或其他类型，跳过没有bvid的
                if not bvid:
                    continue
                
                # 提取推荐理由（如果有）
                rcmd_reason = item.get('rcmd_reason', {})
                rcmd_reason_text = rcmd_reason.get('content', '') if isinstance(rcmd_reason, dict) else ''
                    
                videos.append({
                    'title': item.get('title', ''),
                    'desc': item.get('desc', ''),
                    'author': item.get('owner', {}).get('name', ''),
                    'view': item.get('stat', {}).get('view', 0),
                    'like': item.get('stat', {}).get('like', 0),
                    'bvid': bvid,
                    'url': f'https://www.bilibili.com/video/{bvid}',
                    'id': item.get('id', 0),  # 视频ID
                    'goto': item.get('goto', ''),  # 跳转类型
                    'rcmd_reason': rcmd_reason_text,  # 推荐理由
                })
                
                # 如果已经获取到足够的视频，停止
                if len(videos) >= limit:
                    break
        
        if credential:
            logger.info(f"✅ 使用个性化推荐获取到 {len(videos)} 个B站视频")
        else:
            logger.info(f"✅ 使用默认推荐获取到 {len(videos)} 个B站视频")
        
        return {
            'success': True,
            'videos': videos
        }
        
    except ImportError:
        logger.error("bilibili_api 库未安装，请运行: pip install bilibili-api-python")
        return {
            'success': False,
            'error': 'bilibili_api 库未安装'
        }
    except Exception as e:
        logger.error(f"获取B站推荐失败: {e}")
        import traceback
        logger.debug(f"详细错误: {traceback.format_exc()}")
        return {
            'success': False,
            'error': str(e)
        }




async def fetch_reddit_popular(limit: int = 10) -> Dict[str, Any]:
    """
    获取Reddit热门帖子
    使用Reddit的JSON API获取r/popular的热门帖子
    
    Args:
        limit: 返回帖子的最大数量
    
    Returns:
        包含成功状态和帖子列表的字典
    """
    try:
        # Reddit的JSON API端点
        url = f"https://www.reddit.com/r/popular/hot.json?limit={limit}"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'application/json',
        }
        
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            posts = []
            children = data.get('data', {}).get('children', [])
            
            for item in children[:limit]:
                post_data = item.get('data', {})
                
                # 跳过NSFW内容
                if post_data.get('over_18'):
                    continue
                
                subreddit = post_data.get('subreddit', '')
                title = post_data.get('title', '')
                score = post_data.get('score', 0)
                num_comments = post_data.get('num_comments', 0)
                permalink = post_data.get('permalink', '')
                
                posts.append({
                    'title': title,
                    'subreddit': f"r/{subreddit}",
                    'score': _format_score(score),
                    'comments': _format_score(num_comments),
                    'url': f"https://www.reddit.com{permalink}" if permalink else ''
                })
            
            if posts:
                logger.info(f"从Reddit获取到{len(posts)}条热门帖子")
                return {
                    'success': True,
                    'posts': posts
                }
            else:
                return {
                    'success': False,
                    'error': 'Reddit返回空数据',
                    'posts': []
                }
                
    except httpx.TimeoutException:
        logger.exception("获取Reddit热门超时")
        return {
            'success': False,
            'error': '请求超时',
            'posts': []
        }
    except Exception as e:
        logger.exception(f"获取Reddit热门失败: {e}")
        return {
            'success': False,
            'error': str(e),
            'posts': []
        }


def _format_score(count: int) -> str:
    """格式化Reddit分数/评论数"""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    elif count > 0:
        return str(count)
    return "0"


async def fetch_weibo_trending(limit: int = 10) -> Dict[str, Any]:
    """
    获取微博热议话题
    优先使用s.weibo.com热搜榜页面（刷新频率更高），需要Cookie
    如果失败则回退到公开API
    """
    from bs4 import BeautifulSoup
    
    # 微博Cookie配置 - 用于访问热搜页面
    WEIBO_COOKIE = "SUB=_2AkMWJrkXf8NxqwJRmP8SxWjnaY12zwnEieKgekjMJRMxHRl-yj9jqmtbtRB6PaaX-IGp-AjmO6k5cS-OH2X9CayaTzVD"
    
    try:
        # 优先使用s.weibo.com热搜页面（刷新频率更高）
        url = "https://s.weibo.com/top/summary?cate=realtimehot"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Referer': 'https://s.weibo.com/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Cookie': WEIBO_COOKIE,
        }
        
        # 添加随机延迟
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            # 检查是否重定向到登录页面
            if 'passport' in str(response.url):
                logger.warning("微博Cookie可能已过期，回退到公开API")
                return await _fetch_weibo_trending_fallback(limit)
            
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            
            # 解析热搜列表 (td-02 class)
            td_items = soup.find_all('td', class_='td-02')
            
            if not td_items:
                logger.warning("未找到热搜数据，回退到公开API")
                return await _fetch_weibo_trending_fallback(limit)
            
            trending_list = []
            for i, td in enumerate(td_items):
                if len(trending_list) >= limit:
                    break
                    
                a_tag = td.find('a')
                span = td.find('span')
                
                if a_tag:
                    word = a_tag.get_text(strip=True)
                    if not word:
                        continue
                    
                    # 获取链接
                    href = a_tag.get('href', '')
                    # 构建完整URL（相对链接需要加上域名）
                    if href and not href.startswith('http'):
                        href = f"https://s.weibo.com{href}"
                    
                    # 解析热度值
                    hot_text = span.get_text(strip=True) if span else ''
                    # 热度可能包含类型标签如"剧集 336075"，需要提取数字
                    import re
                    hot_match = re.search(r'(\d+)', hot_text)
                    raw_hot = int(hot_match.group(1)) if hot_match else 0
                    
                    # 提取标签（如"剧集"、"晚会"等）
                    note = re.sub(r'\d+', '', hot_text).strip() if hot_text else ''
                    
                    trending_list.append({
                        'word': word,
                        'raw_hot': raw_hot,
                        'note': note,
                        'rank': i + 1,
                        'url': href
                    })
            
            if trending_list:
                logger.info(f"成功从s.weibo.com获取{len(trending_list)}条热搜")
                return {
                    'success': True,
                    'trending': trending_list
                }
            else:
                return await _fetch_weibo_trending_fallback(limit)
                
    except Exception as e:
        logger.warning(f"s.weibo.com热搜获取失败: {e}，回退到公开API")
        return await _fetch_weibo_trending_fallback(limit)


async def _fetch_weibo_trending_fallback(limit: int = 10) -> Dict[str, Any]:
    """
    微博热搜回退方案 - 使用公开的ajax API
    """
    try:
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
        
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if data.get('ok') == 1:
                trending_list = []
                realtime_list = data.get('data', {}).get('realtime', [])
                
                for item in realtime_list[:limit]:
                    if item.get('is_ad'):
                        continue
                    
                    word = item.get('word', '')
                    # 构建搜索URL
                    search_url = f"https://s.weibo.com/weibo?q={quote(word)}" if word else ''
                    
                    trending_list.append({
                        'word': word,
                        'raw_hot': item.get('raw_hot', 0),
                        'note': item.get('note', ''),
                        'rank': item.get('rank', 0),
                        'url': search_url
                    })
                
                return {
                    'success': True,
                    'trending': trending_list[:limit]
                }
            else:
                logger.error("微博公开API返回错误")
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


async def fetch_twitter_trending(limit: int = 10) -> Dict[str, Any]:
    """
    获取Twitter/X热门话题
    使用Twitter的探索页面获取热门话题
    
    Args:
        limit: 返回热门话题的最大数量
    
    Returns:
        包含成功状态和热门列表的字典
    """
    try:
        # Twitter探索/热门页面
        url = "https://twitter.com/explore/tabs/trending"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1',
        }
        
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html_content = response.text
            
            # 从页面解析热门话题
            trending_list = []
            
            # 尝试从页面的JSON数据中提取热门话题
            trend_pattern = r'"trend":\{[^}]*"name":"([^"]+)"'
            tweet_count_pattern = r'"tweetCount":"([^"]+)"'
            
            trends = re.findall(trend_pattern, html_content)
            tweet_counts = re.findall(tweet_count_pattern, html_content)
            
            for i, trend in enumerate(trends[:limit]):
                if trend and not trend.startswith('#'):
                    trend = '#' + trend if not trend.startswith('@') else trend
                
                # 构建搜索URL
                search_url = f"https://twitter.com/search?q={quote(trend)}" if trend else ''
                
                trending_list.append({
                    'word': trend,
                    'tweet_count': tweet_counts[i] if i < len(tweet_counts) else 'N/A',
                    'note': '',
                    'rank': i + 1,
                    'url': search_url
                })
            
            if trending_list:
                return {
                    'success': True,
                    'trending': trending_list
                }
            else:
                return await _fetch_twitter_trending_fallback(limit)
                
    except httpx.TimeoutException:
        logger.exception("获取Twitter热门超时")
        return {
            'success': False,
            'error': '请求超时'
        }
    except Exception as e:
        logger.exception(f"获取Twitter热门失败: {e}")
        return await _fetch_twitter_trending_fallback(limit)


async def _fetch_twitter_trending_fallback(limit: int = 10) -> Dict[str, Any]:
    """
    Twitter热门的回退方案
    使用第三方服务获取热门话题，因为Twitter官方API需要OAuth认证
    """
    
    def _parse_trends24(soup: BeautifulSoup, limit: int) -> List[Dict[str, Any]]:
        """解析Trends24页面"""
        trending_list = []
        trend_cards = soup.select('.trend-card__list li a')
        for i, item in enumerate(trend_cards[:limit]):
            trend_text = item.get_text(strip=True)
            if trend_text:
                search_url = f"https://twitter.com/search?q={quote(trend_text)}"
                trending_list.append({
                    'word': trend_text,
                    'tweet_count': 'N/A',
                    'note': '',
                    'rank': i + 1,
                    'url': search_url
                })
        return trending_list
    
    def _parse_getdaytrends(soup: BeautifulSoup, limit: int) -> List[Dict[str, Any]]:
        """解析GetDayTrends页面"""
        trending_list = []
        trend_items = soup.select('table.table tr td a')
        for i, item in enumerate(trend_items[:limit]):
            trend_text = item.get_text(strip=True)
            if trend_text:
                search_url = f"https://twitter.com/search?q={quote(trend_text)}"
                trending_list.append({
                    'word': trend_text,
                    'tweet_count': 'N/A',
                    'note': '',
                    'rank': i + 1,
                    'url': search_url
                })
        return trending_list
    
    # 第三方热门话题源列表（按优先级排序）
    fallback_sources = [
        {
            'name': 'Trends24',
            'url': 'https://trends24.in/',
            'parser': _parse_trends24
        },
        {
            'name': 'GetDayTrends',
            'url': 'https://getdaytrends.com/',
            'parser': _parse_getdaytrends
        }
    ]
    
    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    # 按优先级遍历所有数据源
    for source in fallback_sources:
        try:
            await asyncio.sleep(random.uniform(0.1, 0.3))
            
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                response = await client.get(source['url'], headers=headers)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    trending_list = source['parser'](soup, limit)
                    
                    if trending_list:
                        logger.info(f"从{source['name']}获取到{len(trending_list)}条Twitter热门")
                        return {
                            'success': True,
                            'trending': trending_list,
                            'source': source['name'].lower().replace(' ', '')
                        }
        except Exception as e:
            logger.warning(f"{source['name']}获取失败: {e}")
            continue
    
    # 所有第三方源都失败，返回提示信息
    logger.warning("所有Twitter热门数据源均不可用")
    return {
        'success': False,
        'error': 'Twitter热门数据暂时无法获取，请稍后重试或访问 twitter.com/explore',
        'trending': []
    }


async def fetch_trending_content(bilibili_limit: int = 10, weibo_limit: int = 10, 
                                  reddit_limit: int = 10, twitter_limit: int = 10) -> Dict[str, Any]:
    """
    根据用户区域获取热门内容
    
    中文区域：获取B站视频和微博热议话题
    非中文区域：获取Reddit热门帖子和Twitter热门话题
    
    Args:
        bilibili_limit: B站视频最大数量（中文区域）
        weibo_limit: 微博话题最大数量（中文区域）
        reddit_limit: Reddit帖子最大数量（非中文区域）
        twitter_limit: Twitter话题最大数量（非中文区域）
    
    Returns:
        包含成功状态和热门内容的字典
        中文区域：'bilibili' 和 'weibo' 键
        非中文区域：'reddit' 和 'twitter' 键
    """
    try:
        # 检测用户区域
        china_region = is_china_region()
        
        if china_region:
            # Chinese region: Use Bilibili and Weibo
            logger.info("检测到中文区域，获取B站和微博热门内容")
            
            bilibili_task = fetch_bilibili_trending(bilibili_limit)
            weibo_task = fetch_weibo_trending(weibo_limit)
            
            
            bilibili_result, weibo_result = await asyncio.gather(
                bilibili_task, 
                weibo_task,
                return_exceptions=True
            )

            # 处理异常
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
                    'region': 'china',
                    'bilibili': bilibili_result,
                    'weibo': weibo_result
                }
            
            return {
                'success': True,
                'region': 'china',
                'bilibili': bilibili_result,
                'weibo': weibo_result
            }
        else:
            # 非中文区域：使用Reddit和Twitter
            logger.info("检测到非中文区域，获取Reddit和Twitter热门内容")
            
            reddit_task = fetch_reddit_popular(reddit_limit)
            twitter_task = fetch_twitter_trending(twitter_limit)
            
            reddit_result, twitter_result = await asyncio.gather(
                reddit_task,
                twitter_task,
                return_exceptions=True
            )
            
            # 处理异常
            if isinstance(reddit_result, Exception):
                logger.error(f"Reddit爬取异常: {reddit_result}")
                reddit_result = {'success': False, 'error': str(reddit_result)}
            
            if isinstance(twitter_result, Exception):
                logger.error(f"Twitter爬取异常: {twitter_result}")
                twitter_result = {'success': False, 'error': str(twitter_result)}
            
            # 检查是否至少有一个成功
            if not reddit_result.get('success') and not twitter_result.get('success'):
                return {
                    'success': False,
                    'error': '无法获取任何热门内容',
                    'region': 'non-china',
                    'reddit': reddit_result,
                    'twitter': twitter_result
                }
            
            return {
                'success': True,
                'region': 'non-china',
                'reddit': reddit_result,
                'twitter': twitter_result
            }
        
    except Exception as e:
        logger.error(f"获取热门内容失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


async def _fetch_content_by_region(
    china_fetch_func,
    non_china_fetch_func,
    limit: int,
    content_key: str,
    china_log_msg: str,
    non_china_log_msg: str
) -> Dict[str, Any]:
    """
    根据用户区域获取内容的通用辅助函数
    
    Args:
        china_fetch_func: 中文区域使用的异步获取函数
        non_china_fetch_func: 非中文区域使用的异步获取函数
        limit: 内容最大数量
        content_key: 返回结果中的内容键名 ('video' 或 'news')
        china_log_msg: 中文区域的日志消息
        non_china_log_msg: 非中文区域的日志消息
    
    Returns:
        包含成功状态和内容的字典
    """
    china_region = is_china_region()
    region = 'china' if china_region else 'non-china'
    
    try:
        if china_region:
            logger.info(china_log_msg)
            result = await china_fetch_func(limit)
            response = {
                'success': result.get('success', False),
                'region': region,
                content_key: result
            }
        else:
            logger.info(non_china_log_msg)
            result = await non_china_fetch_func(limit)
            response = {
                'success': result.get('success', False),
                'region': region,
                content_key: result
            }
        
        if not result.get('success') and result.get('error'):
            response['error'] = result.get('error')
        return response
            
    except Exception as e:
        logger.error(f"获取内容失败: content_key={content_key} region={region} error={e}")
        return {
            'success': False,
            'error': str(e)
        }


async def fetch_video_content(limit: int = 10) -> Dict[str, Any]:
    """
    根据用户区域获取视频内容
    
    中文区域：获取B站首页视频
    非中文区域：获取Reddit热门帖子
    
    Args:
        limit: 内容最大数量
    
    Returns:
        包含成功状态和视频内容的字典
    """
    return await _fetch_content_by_region(
        china_fetch_func=fetch_bilibili_trending,
        non_china_fetch_func=fetch_reddit_popular,
        limit=limit,
        content_key='video',
        china_log_msg="检测到中文区域，获取B站视频内容",
        non_china_log_msg="检测到非中文区域，获取Reddit热门内容"
    )


async def fetch_news_content(limit: int = 10) -> Dict[str, Any]:
    """
    根据用户区域获取新闻/热议话题内容
    
    中文区域：获取微博热议话题
    非中文区域：获取Twitter热门话题
    
    Args:
        limit: 内容最大数量
    
    Returns:
        包含成功状态和新闻内容的字典
    """
    return await _fetch_content_by_region(
        china_fetch_func=fetch_weibo_trending,
        non_china_fetch_func=fetch_twitter_trending,
        limit=limit,
        content_key='news',
        china_log_msg="检测到中文区域，获取微博热议话题",
        non_china_log_msg="检测到非中文区域，获取Twitter热门话题"
    )


def _format_bilibili_videos(videos: List[Dict], limit: int = 5) -> List[str]:
    """格式化B站视频列表"""
    output_lines = ["【B站首页推荐】"]
    for i, video in enumerate(videos[:limit], 1):
        title = video.get('title', '')
        author = video.get('author', '')
        rcmd_reason = video.get('rcmd_reason', '')
        
        output_lines.append(f"{i}. {title}")
        output_lines.append(f"   UP主: {author}")
        if rcmd_reason:
            output_lines.append(f"   推荐理由: {rcmd_reason}")
    output_lines.append("")
    return output_lines


def _format_reddit_posts(posts: List[Dict], limit: int = 5) -> List[str]:
    """格式化Reddit帖子列表"""
    output_lines = ["【Reddit Hot Posts】"]
    for i, post in enumerate(posts[:limit], 1):
        title = post.get('title', '')
        subreddit = post.get('subreddit', '')
        score = post.get('score', '')
        
        output_lines.append(f"{i}. {title}")
        if subreddit:
            output_lines.append(f"   {subreddit} | {score} upvotes")
    output_lines.append("")
    return output_lines


def _format_weibo_trending(trending_list: List[Dict], limit: int = 5) -> List[str]:
    """格式化微博热议话题列表"""
    output_lines = ["【微博热议话题】"]
    for i, item in enumerate(trending_list[:limit], 1):
        word = item.get('word', '')
        note = item.get('note', '')
        
        line = f"{i}. {word}"
        if note:
            line += f" [{note}]"
        output_lines.append(line)
    output_lines.append("")
    return output_lines


def _format_twitter_trending(trending_list: List[Dict], limit: int = 5) -> List[str]:
    """格式化Twitter热门话题列表"""
    output_lines = ["【Twitter Trending Topics】"]
    for i, item in enumerate(trending_list[:limit], 1):
        word = item.get('word', '')
        tweet_count = item.get('tweet_count', '')
        
        line = f"{i}. {word}"
        if tweet_count and tweet_count != 'N/A':
            line += f" ({tweet_count} tweets)"
        output_lines.append(line)
    output_lines.append("")
    return output_lines


def format_trending_content(trending_content: Dict[str, Any]) -> str:
    """
    将热门内容格式化为可读字符串
    
    根据区域自动格式化：
    - 中文区域：B站和微博内容，中文显示
    - 非中文区域：Reddit和Twitter内容，英文显示
    
    Args:
        trending_content: fetch_trending_content返回的结果
    
    Returns:
        格式化后的字符串
    """
    output_lines = []
    region = trending_content.get('region', 'china')
    
    if region == 'china':
        bilibili_data = trending_content.get('bilibili', {})
        if bilibili_data.get('success'):
            videos = bilibili_data.get('videos', [])
            output_lines.extend(_format_bilibili_videos(videos))
        
        weibo_data = trending_content.get('weibo', {})
        if weibo_data.get('success'):
            trending_list = weibo_data.get('trending', [])
            output_lines.extend(_format_weibo_trending(trending_list))
        
        if not output_lines:
            return "暂时无法获取推荐内容"
    else:
        reddit_data = trending_content.get('reddit', {})
        if reddit_data.get('success'):
            posts = reddit_data.get('posts', [])
            output_lines.extend(_format_reddit_posts(posts))
        
        twitter_data = trending_content.get('twitter', {})
        if twitter_data.get('success'):
            trending_list = twitter_data.get('trending', [])
            output_lines.extend(_format_twitter_trending(trending_list))
        
        if not output_lines:
            return "Unable to fetch trending content at the moment"
    
    return "\n".join(output_lines)


def format_video_content(video_content: Dict[str, Any]) -> str:
    """
    将视频内容格式化为可读字符串
    
    根据区域自动格式化：
    - 中文区域：B站视频内容
    - 非中文区域：Reddit帖子内容
    
    Args:
        video_content: fetch_video_content返回的结果
    
    Returns:
        格式化后的字符串
    """
    region = video_content.get('region', 'china')
    video_data = video_content.get('video', {})
    
    if region == 'china':
        if video_data.get('success'):
            videos = video_data.get('videos', [])
            output_lines = _format_bilibili_videos(videos)
            return "\n".join(output_lines)
        return "暂时无法获取视频推荐内容"
    else:
        if video_data.get('success'):
            posts = video_data.get('posts', [])
            output_lines = _format_reddit_posts(posts)
            return "\n".join(output_lines)
        return "Unable to fetch trending posts at the moment"


def format_news_content(news_content: Dict[str, Any]) -> str:
    """
    将新闻内容格式化为可读字符串
    
    根据区域自动格式化：
    - 中文区域：微博热议话题
    - 非中文区域：Twitter热门话题
    
    Args:
        news_content: fetch_news_content返回的结果
    
    Returns:
        格式化后的字符串
    """
    region = news_content.get('region', 'china')
    news_data = news_content.get('news', {})
    
    if region == 'china':
        if news_data.get('success'):
            trending_list = news_data.get('trending', [])
            output_lines = _format_weibo_trending(trending_list)
            return "\n".join(output_lines)
        return "暂时无法获取热议话题"
    else:
        if news_data.get('success'):
            trending_list = news_data.get('trending', [])
            output_lines = _format_twitter_trending(trending_list)
            return "\n".join(output_lines)
        return "Unable to fetch trending topics at the moment"

# =======================================================
# 活跃窗口标题获取函数
# =======================================================
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
    
    根据用户区域自动使用适当的语言：
    - 中文区域：中文提示词，用于百度搜索
    - 非中文区域：英文提示词，用于Google搜索
    
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
        
        # 使用summary模型配置
        summary_config = config_manager.get_model_api_config('summary')
        
        llm = ChatOpenAI(
            model=summary_config['model'],
            base_url=summary_config['base_url'],
            api_key=summary_config['api_key'],
            temperature=1.0,
            timeout=10.0,
            max_retries=0,
        )
        
        # 清理/脱敏窗口标题用于日志显示
        sanitized_title = window_title[:30] + '...' if len(window_title) > 30 else window_title
        
        # 检测区域并使用适当的提示词
        china_region = is_china_region()
        
        if china_region:
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
        else:
            prompt = f"""Based on the following window title, generate 3 different search keywords for Google search.

Window title: {window_title}

Requirements:
1. Generate 3 keywords from different angles
2. Keywords should be concise (2-6 words each)
3. Keywords should be diverse, covering different aspects
4. Output only 3 keywords, one per line, without any numbers, punctuation, or other content

Example output format:
keyword one
keyword two
keyword three"""

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
        if china_region:
            logger.info(f"为窗口标题「{sanitized_title}」生成的查询关键词: {queries}")
        else:
            logger.info(f"为窗口标题「{sanitized_title}」生成的查询关键词: {queries}")
        return queries[:3]
        
    except Exception as e:
        # 异常日志中也使用脱敏标题
        sanitized_title = window_title[:30] + '...' if len(window_title) > 30 else window_title
        if is_china_region():
            logger.warning(f"为窗口标题「{sanitized_title}」生成多样化查询失败，使用默认清理方法: {e}")
        else:
            logger.warning(f"为窗口标题「{sanitized_title}」生成多样化查询失败，使用默认清理方法: {e}")
        # 回退到原始清理方法
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

# =======================================================
# 搜索函数
# =======================================================

async def search_google(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    使用Google搜索关键词并获取搜索结果（用于非中文区域）
    
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
        
        # Google搜索URL
        url = f"https://www.google.com/search?q={encoded_query}&hl=en"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Cache-Control': 'no-cache',
        }
        
        # 添加随机延迟
        await asyncio.sleep(random.uniform(0.2, 0.5))
        
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html_content = response.text
            
            # 解析搜索结果
            results = parse_google_results(html_content, limit)
            
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
        logger.exception("Google搜索超时")
        return {
            'success': False,
            'error': '搜索超时'
        }
    except Exception as e:
        logger.exception(f"Google搜索失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def parse_google_results(html_content: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    解析Google搜索结果页面
    
    Args:
        html_content: HTML页面内容
        limit: 结果数量限制
    
    Returns:
        搜索结果列表，每个结果包含 title, abstract, url
    """
    results = []
    
    try:
        from urllib.parse import urljoin, urlparse, parse_qs
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 查找搜索结果容器
        # Google使用各种类名，尝试多个选择器
        result_divs = soup.find_all('div', class_='g')
        
        for div in result_divs[:limit * 2]:
            # 提取标题和链接
            link = div.find('a')
            if link:
                # 获取h3标签作为标题
                h3 = div.find('h3')
                if h3:
                    title = h3.get_text(strip=True)
                else:
                    title = link.get_text(strip=True)
                
                if title and 3 < len(title) < 200:
                    # 提取URL
                    href = link.get('href', '')
                    if href:
                        # Google有时会包装URL
                        if href.startswith('/url?'):
                            parsed = urlparse(href)
                            qs = parse_qs(parsed.query)
                            url = qs.get('q', [href])[0]
                        elif href.startswith('http'):
                            url = href
                        else:
                            url = urljoin('https://www.google.com', href)
                    else:
                        url = ''
                    
                    # 提取摘要/片段
                    abstract = ""
                    # 查找片段文本
                    snippet_div = div.find('div', class_=lambda x: x and ('VwiC3b' in x if x else False))
                    if snippet_div:
                        abstract = snippet_div.get_text(strip=True)[:200]
                    else:
                        # 尝试其他常见的片段选择器
                        spans = div.find_all('span')
                        for span in spans:
                            text = span.get_text(strip=True)
                            if len(text) > 50:
                                abstract = text[:200]
                                break
                    
                    # 跳过广告和不需要的结果
                    if not any(skip in title.lower() for skip in ['ad', 'sponsored', 'javascript']):
                        results.append({
                            'title': title,
                            'abstract': abstract,
                            'url': url
                        })
                        if len(results) >= limit:
                            break
        
        logger.info(f"解析到 {len(results)} 条Google搜索结果")
        return results[:limit]
        
    except Exception as e:
        logger.exception(f"解析Google搜索结果失败: {e}")
        return []


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
        
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
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


def format_search_results(search_result: Dict[str, Any]) -> str:
    """
    将搜索结果格式化为可读字符串
    根据区域自动使用适当的语言
    
    Args:
        search_result: search_baidu或search_google返回的结果
    
    Returns:
        格式化后的字符串
    """
    china_region = is_china_region()
    
    if not search_result.get('success'):
        if china_region:
            return f"搜索失败: {search_result.get('error', '未知错误')}"
        else:
            return f"Search failed: {search_result.get('error', 'Unknown error')}"
    
    output_lines = []
    query = search_result.get('query', '')
    results = search_result.get('results', [])
    
    if china_region:
        output_lines.append(f"【关于「{query}」的搜索结果】")
    else:
        output_lines.append(f"【Search results for「{query}」】")
    output_lines.append("")
    
    for i, result in enumerate(results, 1):
        title = result.get('title', '')
        abstract = result.get('abstract', '')
        
        output_lines.append(f"{i}. {title}")
        if abstract:
            abstract = abstract[:150] + '...' if len(abstract) > 150 else abstract
            output_lines.append(f"   {abstract}")
        output_lines.append("")
    
    if not results:
        if china_region:
            output_lines.append("未找到相关结果")
        else:
            output_lines.append("No results found")
    
    return "\n".join(output_lines)


async def fetch_window_context_content(limit: int = 5) -> Dict[str, Any]:
    """
    获取当前活跃窗口标题并进行搜索
    
    使用区域检测来决定搜索引擎：
    - 中文区域：百度搜索
    - 非中文区域：Google搜索
    
    Args:
        limit: 搜索结果数量限制
    
    Returns:
        包含窗口标题和搜索结果的字典
        注意：window_title是脱敏后的版本以保护隐私
    """
    try:
        # 检测区域
        china_region = is_china_region()
        
        # 获取活跃窗口标题（同时获取原始和脱敏版本）
        title_result = get_active_window_title(include_raw=True)
        
        if not title_result:
            if china_region:
                return {
                    'success': False,
                    'error': '无法获取当前活跃窗口标题'
                }
            else:
                return {
                    'success': False,
                    'error': '无法获取当前活跃窗口标题'
                }
        
        sanitized_title = title_result['sanitized']
        raw_title = title_result['raw']
        
        # 清理窗口标题以移除敏感信息，避免发送给LLM
        cleaned_title = clean_window_title(raw_title)
        
        # 使用清理后的标题生成多样化搜索查询（保护隐私）
        search_queries = await generate_diverse_queries(cleaned_title)
        
        if not search_queries or all(not q or len(q) < 2 for q in search_queries):
            if china_region:
                return {
                    'success': False,
                    'error': '窗口标题无法提取有效的搜索关键词',
                    'window_title': sanitized_title
                }
            else:
                return {
                    'success': False,
                    'error': '窗口标题无法提取有效的搜索关键词',
                    'window_title': sanitized_title
                }
        
        # 日志中使用脱敏后的标题
        if china_region:
            logger.info(f"从窗口标题「{sanitized_title}」生成多样化查询: {search_queries}")
        else:
            logger.info(f"从窗口标题「{sanitized_title}」生成多样化查询: {search_queries}")
        
        # 执行搜索并合并结果
        all_results = []
        successful_queries = []
        
        # 根据区域选择搜索函数
        search_func = search_baidu if china_region else search_google
        
        for query in search_queries:
            if not query or len(query) < 2:
                continue
            
            if china_region:
                logger.info(f"使用查询关键词: {query}")
            else:
                logger.info(f"使用查询关键词: {query}")
            
            search_result = await search_func(query, limit)
            
            if search_result.get('success') and search_result.get('results'):
                all_results.extend(search_result['results'])
                successful_queries.append(query)
        
        # 去重结果（优先使用URL，如果URL缺失则使用title）
        seen_keys = set()
        unique_results = []
        for result in all_results:
            url = result.get('url', '')
            title = result.get('title', '')
            
            # 优先使用URL进行去重，回退到title
            dedup_key = url if url else title
            
            if dedup_key and dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                unique_results.append(result)
        
        # 限制总结果数量
        unique_results = unique_results[:limit * 2]
        
        if not unique_results:
            if china_region:
                return {
                    'success': False,
                    'error': '所有查询均未获得搜索结果',
                    'window_title': sanitized_title,
                    'search_queries': search_queries
                }
            else:
                return {
                    'success': False,
                    'error': '所有查询均未获得搜索结果',
                    'window_title': sanitized_title,
                    'search_queries': search_queries
                }
        
        return {
            'success': True,
            'window_title': sanitized_title,
            'search_queries': successful_queries,
            'search_results': unique_results,
            'region': 'china' if china_region else 'non-china'
        }
        
    except Exception as e:
        if is_china_region():
            logger.exception(f"获取窗口上下文内容失败: {e}")
        else:
            logger.exception(f"获取窗口上下文内容失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def format_window_context_content(content: Dict[str, Any]) -> str:
    """
    将窗口上下文内容格式化为可读字符串
    
    根据区域自动使用适当的语言
    
    Args:
        content: fetch_window_context_content返回的结果
    
    Returns:
        格式化后的字符串
    """
    china_region = is_china_region()
    
    if not content.get('success'):
        if china_region:
            return f"获取窗口上下文失败: {content.get('error', '未知错误')}"
        else:
            return f"Failed to fetch window context: {content.get('error', 'Unknown error')}"
    
    output_lines = []
    window_title = content.get('window_title', '')
    search_queries = content.get('search_queries', [])
    results = content.get('search_results', [])
    
    if china_region:
        output_lines.append(f"【当前活跃窗口】{window_title}")
        
        if search_queries:
            if len(search_queries) == 1:
                output_lines.append(f"【搜索关键词】{search_queries[0]}")
            else:
                output_lines.append(f"【搜索关键词】{', '.join(search_queries)}")
        
        output_lines.append("")
        output_lines.append("【相关信息】")
    else:
        output_lines.append(f"【Active Window】{window_title}")
        
        if search_queries:
            if len(search_queries) == 1:
                output_lines.append(f"【Search Keywords】{search_queries[0]}")
            else:
                output_lines.append(f"【Search Keywords】{', '.join(search_queries)}")
        
        output_lines.append("")
        output_lines.append("【Related Information】")
    
    for i, result in enumerate(results, 1):
        title = result.get('title', '')
        abstract = result.get('abstract', '')
        url = result.get('url', '')
        
        output_lines.append(f"{i}. {title}")
        if abstract:
            abstract = abstract[:150] + '...' if len(abstract) > 150 else abstract
            output_lines.append(f"   {abstract}")
        if url:
            if china_region:
                output_lines.append(f"   链接: {url}")
            else:
                output_lines.append(f"   Link: {url}")
    
    if not results:
        if china_region:
            output_lines.append("未找到相关信息")
        else:
            output_lines.append("No related information found")
    
    return "\n".join(output_lines)

# =======================================================
# 自动搜索歌曲，弹出嵌入式浏览器播放
# =======================================================
async def search_and_play_song(song_name: str) -> Dict[str, Any]:
    """
    搜索歌曲并在嵌入式浏览器中播放
    
    根据区域自动选择平台：
    - 中文区域：优先使用B站搜索并播放
    - 非中文区域：优先使用YouTube搜索并播放
    
    Args:
        song_name: 歌曲名称
    
    Returns:
        包含播放结果的字典，结构：
        {
            'success': bool,
            'error': str (可选),
            'url': str (嵌入式播放URL),
            'platform': str ('bilibili'/'youtube'),
            'region': str ('china'/'non-china')
        }
    """
    if not song_name or not song_name.strip():
        logger.error("歌曲名称不能为空")
        return {
            'success': False,
            'error': '歌曲名称不能为空',
            'platform': '',
            'region': ''
        }
    
    # 检测用户区域
    china_region = is_china_region()
    region = 'china' if china_region else 'non-china'
    platform = 'bilibili' if china_region else 'youtube'
    
    # 编码歌曲名称用于URL
    encoded_song_name = quote(song_name.strip())
    
    headers = {
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8' if china_region else 'en-US,en;q=0.9',
    }
    
    try:
        # 添加随机延迟，避免请求过快被限制
        await asyncio.sleep(random.uniform(0.2, 0.8))
        
        if china_region:
            # 中文区域：B站搜索歌曲
            search_url = f"https://search.bilibili.com/all?keyword={encoded_song_name}&order=click&duration=0&tids_1=0"
            logger.info(f"B站搜索歌曲: {song_name}, URL: {search_url}")
            
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(search_url, headers=headers)
                response.raise_for_status()
                
                # 解析搜索结果，提取第一个视频的播放URL
                soup = BeautifulSoup(response.text, 'html.parser')
                # 查找第一个视频链接（B站搜索结果的视频项）
                video_item = soup.find('a', class_='video-item__title') or soup.find('a', attrs={'href': re.compile(r'/video/')})
                
                if not video_item:
                    logger.warning(f"B站未找到歌曲相关视频: {song_name}")
                    return {
                        'success': False,
                        'error': f'B站未找到"{song_name}"相关视频',
                        'platform': platform,
                        'region': region
                    }
                
                # 提取视频链接并构造嵌入式播放URL
                video_href = video_item.get('href', '')
                if not video_href.startswith('http'):
                    video_href = f"https:{video_href}" if video_href.startswith('//') else f"https://www.bilibili.com{video_href}"
                
                # B站嵌入式播放URL（支持iframe嵌入）
                # 提取BV号用于构造embed URL
                bv_match = re.search(r'(BV\w+)', video_href)
                if bv_match:
                    embed_url = f"https://player.bilibili.com/player.html?bvid={bv_match.group(1)}&page=1"
                else:
                    embed_url = video_href
                
                logger.info(f"成功找到B站歌曲播放链接: {embed_url}")
                return {
                    'success': True,
                    'url': embed_url,
                    'platform': platform,
                    'region': region
                }
        
        else:
            # 非中文区域：YouTube搜索歌曲
            search_url = f"https://www.youtube.com/results?search_query={encoded_song_name}"
            logger.info(f"YouTube搜索歌曲: {song_name}, URL: {search_url}")
            
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(search_url, headers=headers)
                response.raise_for_status()
                
                # 解析YouTube搜索结果，提取第一个视频的ID
                soup = BeautifulSoup(response.text, 'html.parser')
                # YouTube搜索结果的视频项匹配（适配不同页面结构）
                video_script = soup.find('script', string=re.compile(r'"videoId":"[\w-]+"'))
                video_id = None
                
                if video_script:
                    # 从JSON数据中提取第一个视频ID
                    vid_match = re.search(r'"videoId":"([\w-]+)"', video_script.string)
                    if vid_match:
                        video_id = vid_match.group(1)
                else:
                    # 备用解析方式：查找视频链接的a标签
                    video_link = soup.find('a', attrs={'href': re.compile(r'/watch\?v=')})
                    if video_link:
                        vid_match = re.search(r'v=([\w-]+)', video_link.get('href', ''))
                        if vid_match:
                            video_id = vid_match.group(1)
                
                if not video_id:
                    logger.warning(f"YouTube未找到歌曲相关视频: {song_name}")
                    return {
                        'success': False,
                        'error': f'YouTube未找到"{song_name}"相关视频',
                        'platform': platform,
                        'region': region
                    }
                
                # YouTube嵌入式播放URL
                embed_url = f"https://www.youtube.com/embed/{video_id}"
                logger.info(f"成功找到YouTube歌曲播放链接: {embed_url}")
                return {
                    'success': True,
                    'url': embed_url,
                    'platform': platform,
                    'region': region
                }
    
    except httpx.TimeoutException:
        logger.error(f"{platform}搜索歌曲超时: {song_name}")
        return {
            'success': False,
            'error': f'{platform}请求超时，请稍后重试',
            'platform': platform,
            'region': region
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"{platform}搜索歌曲HTTP错误: {song_name}, 状态码: {e.response.status_code}")
        return {
            'success': False,
            'error': f'{platform}请求失败（状态码：{e.response.status_code}）',
            'platform': platform,
            'region': region
        }
    except Exception as e:
        logger.error(f"搜索并播放歌曲失败: {song_name}, 错误: {e}", exc_info=True)
        return {
            'success': False,
            'error': f'播放失败：{str(e)}',
            'platform': platform,
            'region': region
        }

# =======================================================
# 个人动态（基于用户兴趣和区域）
# =======================================================

def _get_platform_cookies(platform_name: str) -> dict[str, str]:
    """
    通用平台 Cookie 读取器 (接入系统底层的加密/明文统一读取逻辑)
    """
    try:
        # 优先调用系统底层的解密读取逻辑
        from utils.cookies_login import load_cookies_from_file
        cookies = load_cookies_from_file(platform_name)
        if cookies:
            logger.debug(f"✅ 成功通过底层接口加载 {platform_name} 凭证")
            return cookies
    except Exception as e:
        logger.debug(f"底层接口加载 {platform_name} 凭证失败: {e}，尝试使用明文回退...")

    # 下面是作为回退的明文读取逻辑（兜底处理旧文件）
    possible_paths = [
        Path(os.path.expanduser('~')) / f'{platform_name}_cookies.json',
        Path('config') / f'{platform_name}_cookies.json',
        Path('.') / f'{platform_name}_cookies.json',
    ]
    
    for cookie_file in possible_paths:
        if not cookie_file.exists():
            continue
            
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookie_data = json.load(f)

            cookies = {}
            if isinstance(cookie_data, list):
                for cookie in cookie_data:
                    name, value = cookie.get('name'), cookie.get('value')
                    if name and value: 
                        cookies[name] = value
            elif isinstance(cookie_data, dict):
                cookies = cookie_data
            
            if cookies:
                return cookies
        except Exception:
            continue

    return {}

# 获取个人关注动态内容

async def fetch_bilibili_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    获取B站推送的动态消息
    """
    import re

    try:
        credential = _get_bilibili_credential()
        if not credential: 
            return {'success': False, 'error': '未提供Bilibili认证信息'}

        url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all"
        headers = {"User-Agent": get_random_user_agent(), "Referer": "https://t.bilibili.com/"}
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, cookies=credential.get_cookies(), timeout=10.0)
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, dict) or data.get("code") != 0:
            logger.error(f"获取B站动态失败，API返回: {data}")
            return {'success': False, 'error': "API请求失败"}

        def safe_dict(d: Any, key: str) -> dict:
            if not isinstance(d, dict):
                return {}
            v = d.get(key)
            return v if isinstance(v, dict) else {}

        dynamic_list = []
        items = data.get("data")
        items = items.get("items", []) if isinstance(items, dict) else []

        for item in items:
            if not isinstance(item, dict):
                continue
                
            try:
                dynamic_id = str(item.get("id_str", ""))
                dynamic_type = str(item.get("type", ""))
                if dynamic_type in {"DYNAMIC_TYPE_AD", "DYNAMIC_TYPE_APPLET", "DYNAMIC_TYPE_NONE"}: 
                    continue
                
                modules = safe_dict(item, "modules")
                module_author = safe_dict(modules, "module_author")
                
                # 获取到了作者名
                author = module_author.get("name") or "未知UP主"
                pub_time = module_author.get("pub_time") or "刚刚"
                
                module_dynamic = safe_dict(modules, "module_dynamic")
                major = safe_dict(module_dynamic, "major")
                desc = safe_dict(module_dynamic, "desc")
                
                major_type = major.get("type")
                raw_text = desc.get("text") or ""
                
                content = ""
                specific_url = f"https://t.bilibili.com/{dynamic_id}"  # 默认动态页面URL
                
                match major_type:
                    case "MAJOR_TYPE_ARCHIVE": 
                        # 视频动态：添加视频链接
                        archive = safe_dict(major, "archive")
                        bvid = archive.get("bvid", "")
                        if bvid:
                            specific_url = f"https://www.bilibili.com/video/{bvid}"
                        content = f"[发布了新视频] {archive.get('title', '')}"
                        
                    case "MAJOR_TYPE_DRAW": 
                        # 图文动态：保持动态页面链接
                        content = f"[图文动态] {raw_text}" if raw_text else "[分享了图片]"
                        
                    case "MAJOR_TYPE_ARTICLE":
                        # 专栏文章：添加文章链接
                        article = safe_dict(major, "article")
                        article_id = article.get("id", "")
                        if article_id:
                            specific_url = f"https://www.bilibili.com/read/cv{article_id}"
                        content = f"[发布了专栏文章] {article.get('title', '')}"
                        
                    case "MAJOR_TYPE_LIVE_RCMD":
                        # 直播动态：添加直播间链接
                        live_title = raw_text
                        try:
                            live_rcmd = major.get("live_rcmd") or major.get("live")
                            if isinstance(live_rcmd, dict):
                                content_str = live_rcmd.get("content")
                                if isinstance(content_str, str) and content_str.startswith("{"):
                                    play_info = json.loads(content_str).get("live_play_info")
                                    if isinstance(play_info, dict):
                                        live_title = play_info.get("title", live_title)
                                        room_id = play_info.get("room_id")
                                        if room_id:
                                            specific_url = f"https://live.bilibili.com/{room_id}"
                                elif isinstance(live_rcmd.get("live_play_info"), dict):
                                    live_title = live_rcmd["live_play_info"].get("title", live_title)
                                    room_id = live_rcmd["live_play_info"].get("room_id")
                                    if room_id:
                                        specific_url = f"https://live.bilibili.com/{room_id}"
                        except Exception:
                            pass
                        content = f"[正在直播] {live_title or '快来我的直播间看看吧！'}"
                        
                    case _:
                        if dynamic_type == "DYNAMIC_TYPE_LIVE_RCMD":
                            # 直播开播推送：添加直播间链接
                            content = f"[正在直播] {raw_text or '快来我的直播间看看吧！'}"
                            # 尝试从描述中提取直播间ID
                            import re
                            room_match = re.search(r'直播间：(\d+)', raw_text)
                            if room_match:
                                specific_url = f"https://live.bilibili.com/{room_match.group(1)}"
                                
                        elif dynamic_type == "DYNAMIC_TYPE_FORWARD":
                            content = f"[转发动态] {raw_text}" if raw_text else "[转发了动态]"
                        else:
                            content = raw_text or "发布了新动态"

                content = re.sub(r'\s+', ' ', content).strip()
                if not content:
                    content = "分享了新动态"

                final_content = f"UP主【{author}】: {content}"

                dynamic_list.append({
                    'dynamic_id': dynamic_id, 'type': dynamic_type, 'timestamp': pub_time,
                    'author': author, 'content': final_content,  # 存入拼接好的完整字符串
                    'url': specific_url,  # 使用具体类型的URL
                    'base_url': f"https://t.bilibili.com/{dynamic_id}"  # 保留原始动态页面链接
                })
                if len(dynamic_list) >= limit:
                    break
            except Exception as item_e:
                logger.warning(f"解析单条动态失败, 跳过, 动态ID: {item.get('id_str', '未知')}, 错误类型: {type(item_e).__name__}")

        if dynamic_list:
            logger.info(f"✅ 成功获取到 {len(dynamic_list)} 条你关注的UP主动态消息")
        return {'success': True, 'dynamics': dynamic_list}

    except Exception as e:
        logger.error(f"获取B站动态消息失败: {e}")
        return {'success': False, 'error': str(e)}
        
async def fetch_douyin_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    pass

async def fetch_kuaishou_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """获取快手个人关注动态 (GraphQL 接口 + 严格 Cookie)"""
    pass

async def fetch_weibo_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    获取微博动态
    设计原则：
    - 切换至 Mobile 移动版 API，彻底绕过 PC 端所有风控
    - 仅需核心登录凭证 SUB，其他 Cookie 全部失效
    - 目标变更为：移动端首页关注流的固定 Container ID
    - 必须伪装成手机浏览器的 User-Agent
    """
    import re
    import random
    import asyncio
    import httpx
    
    try:
        weibo_cookies = _get_platform_cookies('weibo')
        if not weibo_cookies:
            return {'success': False, 'error': '未找到 config/weibo_cookies.json'}
        
        # 1. 只需要最核心的 SUB，其他全都不需要！
        sub = weibo_cookies.get('SUB') or weibo_cookies.get('sub')
        if not sub:
            logger.error("❌ 缺少核心登录凭证 SUB。")
            return {'success': False, 'error': '缺少核心登录凭证 SUB'}

        # 2. 目标变更为：移动端首页关注流的固定 Container ID
        url = "https://m.weibo.cn/api/container/getIndex?containerid=102803"
        
        # 3. 必须伪装成手机浏览器的 User-Agent
        mobile_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        
        headers = {
            'User-Agent': mobile_ua,
            'Referer': 'https://m.weibo.cn/',
            'Accept': 'application/json, text/plain, */*',
            'X-Requested-With': 'XMLHttpRequest',
            'MWeibo-Pwa': '1'
        }
        
        # 仅携带最纯净的 SUB 即可
        req_cookies = {'SUB': sub}
        
        await asyncio.sleep(random.uniform(0.1, 0.5))

        # 4. 移动端 API 非常宽容，直接用普通的 httpx 即可稳定发包
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, cookies=req_cookies)
            
            if response.status_code != 200:
                logger.error(f"❌ 移动端微博接口异常，状态码: {response.status_code}")
                return {'success': False, 'error': f"API请求失败，状态码: {response.status_code}"}
                
            data = response.json()
            
            # 移动端如果未登录，通常会返回 ok: 0 或者重定向
            if data.get('ok') != 1:
                logger.error("❌ 微博拦截：返回 ok=0，说明你的 SUB 凭证已过期！")
                return {'success': False, 'error': "微博凭证已过期，请去浏览器重新获取"}
            
            cards = data.get('data', {}).get('cards', [])
            weibo_list = []
            
            for card in cards:
                # card_type == 9 代表这是一条正常的微博博文卡片
                if card.get('card_type') != 9:
                    continue
                    
                mblog = card.get('mblog')
                if not mblog:
                    continue
                    
                user = mblog.get('user', {})
                author = user.get('screen_name') or '未知博主'
                
                # 提取正文并清理 HTML 标签
                text = str(mblog.get('text') or '')
                clean_text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text)).strip()
                
                # 兼容并缝合转发内容
                if mblog.get('retweeted_status'):
                    retweet = mblog['retweeted_status']
                    rt_author = retweet.get('user', {}).get('screen_name') or '原博主'
                    rt_text = str(retweet.get('text') or '')
                    rt_clean_text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', rt_text)).strip()
                    clean_text = f"{clean_text} // [转发动态] @{rt_author}: {rt_clean_text}"
                
                display_text = clean_text if clean_text else "[分享了图片/动态]"
                final_content = f"博主【{author}】: {display_text}"
                mid = mblog.get('mid') or mblog.get('id', '')
                
                weibo_list.append({
                    'author': author,
                    'content': final_content,
                    'timestamp': mblog.get('created_at') or '',
                    'url': f"https://m.weibo.cn/detail/{mid}" # 使用移动端 URL
                })
                
                if len(weibo_list) >= limit:
                    break

            if weibo_list: 
                logger.info(f"✅ 成功通过移动端接口获取到 {len(weibo_list)} 条微博个人动态")
                logger.info("微博动态:")  # 统一对齐 B站 的提示词
                for i, weibo in enumerate(weibo_list, 1):
                    content = weibo.get('content', '')
                    # 稍微放宽一点截断长度，保证显示效果更好
                    if len(content) > 50:
                        content = content[:50] + "..."
                    # 去掉冗余的时间和作者，直接干干净净地打印 content
                    logger.info(f"  - {content}")
                
                return {'success': True, 'statuses': weibo_list}
            else:
                return {'success': False, 'error': '未解析到微博内容'}
                
    except Exception as e: 
        logger.error(f"微博动态解析发生错误: {e}")
        return {'success': False, 'error': str(e)}

async def fetch_reddit_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    获取Reddit推送的动态帖子
    """
    try:
        reddit_cookies = _get_platform_cookies('reddit')
        if not reddit_cookies: 
            return {'success': False, 'error': '未配置 config/reddit_cookies.json'}
        url = f"https://www.reddit.com/hot.json?limit={limit}"
        headers = {'User-Agent': get_random_user_agent(), 'Accept': 'application/json'}
        await asyncio.sleep(random.uniform(0.1, 0.5))

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, cookies=reddit_cookies)
            data = response.json()
            posts = [
                {
                    'title': pd.get('title', ''), 'subreddit': f"r/{pd.get('subreddit', '')}",
                    'score': _format_score(pd.get('score', 0)), 
                    'url': f"https://www.reddit.com{pd.get('permalink', '')}"
                }
                for item in data.get('data', {}).get('children', [])[:limit]
                if not (pd := item.get('data', {})).get('over_18')
            ]
            if posts:
                logger.info(f"✅ 成功获取到 {len(posts)} 条Reddit订阅帖子")
            return {'success': True, 'posts': posts}
    except Exception as e: 
        return {'success': False, 'error': str(e)}


async def _fetch_twitter_personal_web_scraping(limit: int = 10, cookies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Twitter 网页抓取 fallback
    """
    try:
        url = "https://twitter.com/home"
        headers = {'User-Agent': get_random_user_agent()}
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            res = await client.get(url, headers=headers, cookies=cookies)
            
            # 如果被重定向到了登录页，说明 Cookie 彻底失效了
            if "login" in str(res.url) or "logout" in str(res.url):
                return {'success': False, 'error': 'Twitter Cookie 已过期，网页端拒绝访问'}
                
            tweets = []
            tweet_texts = re.findall(r'"tweet":\{[^}]*"full_text":"([^"]+)"', res.text)
            screen_names = re.findall(r'"screen_name":"([^"]+)"', res.text)
            
            for i, text in enumerate(tweet_texts[:limit]):
                clean_text = re.sub(r'https://t\.co/\w+', '', text).strip()
                tweets.append({
                    'author': f"@{screen_names[i] if i<len(screen_names) else 'Unknown'}", 
                    'content': clean_text,
                    'timestamp': '刚刚'  # 保持与主 API 数据字典格式的统一
                })
                
            return {'success': True, 'tweets': tweets} if tweets else {'success': False, 'error': '网页正则抓取失败，页面结构可能已变更'}
    except Exception as e: 
        logger.error(f"Twitter 网页抓取 fallback 失败: {e}")
        return {'success': False, 'error': str(e)}

async def fetch_twitter_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    获取 Twitter 个人时间线
    """
    
    try:
        twitter_cookies = _get_platform_cookies('twitter')
        if not twitter_cookies:
             return {'success': False, 'error': '未配置 config/twitter_cookies.json'}
             
        # 提取防伪 CSRF Token。Twitter 必须，否则哪怕有合法 Cookie 也会立刻 401/403
        ct0 = twitter_cookies.get('ct0') or twitter_cookies.get('CT0', '')
        if not ct0:
            logger.warning("Twitter Cookie 中缺少核心字段 ct0，极大可能触发风控拦截")
        
        # 官方 Web 客户端通用固化的 Bearer Token
        bearer_token = os.environ.get("TWITTER_BEARER_TOKEN", "")
        if not bearer_token:
            logger.warning("Falling back to hardcoded Web client Bearer Token, consider configuring TWITTER_BEARER_TOKEN")
            bearer_token = 'AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIyU2%2FGoa3FmBNYDPz%2FzGz%2F2Rnc%2F2bGBDH%2Fc'
        
        # 切换到更稳定、包含完整推文文本的 v1.1 接口
        url = f"https://api.twitter.com/1.1/statuses/home_timeline.json?tweet_mode=extended&count={limit}"
        
        # 补全极其严格的 Twitter 风控协议头
        headers = {
            'User-Agent': get_random_user_agent(), 
            'Accept': 'application/json',
            'Authorization': f'Bearer {bearer_token}',
            'x-twitter-auth-type': 'OAuth2Session' if 'auth_token' in twitter_cookies else '',
            'x-csrf-token': ct0,  # <-- 防火墙放行的关键钥匙
            'x-twitter-active-user': 'yes',
            'x-twitter-client-language': 'zh-cn'
        }
        
        await asyncio.sleep(random.uniform(0.1, 0.5))

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, cookies=twitter_cookies)
            
            # 状态码非 200 时，平滑降级到备用网页刮削方案
            if response.status_code != 200: 
                logger.warning(f"Twitter API 拒绝访问 (状态码: {response.status_code})，回退到网页刮削...")
                return await _fetch_twitter_personal_web_scraping(limit, twitter_cookies)
                
            # 真正去解析返回的推文数据，替换掉之前的占位符
            data = response.json()
            if not isinstance(data, list):
                return {'success': False, 'error': 'API 返回数据格式异常'}
                
            tweets = []
            for tweet in data[:limit]:
                user = tweet.get('user', {})
                author = user.get('screen_name') or 'Unknown'
                # tweet_mode=extended 时，正文在 full_text 里
                text = str(tweet.get('full_text') or tweet.get('text') or '')
                
                # 清理推文末尾自带的分享短链接 (https://t.co/xxx)
                clean_text = re.sub(r'https://t\.co/\w+', '', text).strip()
                
                # 处理转推 (Retweet) 的前缀拼接
                if 'retweeted_status' in tweet:
                    rt_user = tweet['retweeted_status'].get('user', {}).get('screen_name', 'Unknown')
                    rt_text = str(tweet['retweeted_status'].get('full_text') or '')
                    rt_clean_text = re.sub(r'https://t\.co/\w+', '', rt_text).strip()
                    clean_text = f"RT @{rt_user}: {rt_clean_text}"
                
                tweets.append({
                    'author': f"@{author}", 
                    'content': clean_text,
                    'timestamp': tweet.get('created_at', '')
                })
                
            if tweets:
                logger.info(f"✅ 成功获取到 {len(tweets)} 条 Twitter 个人时间线动态")
                return {'success': True, 'tweets': tweets}
            else:
                return {'success': False, 'error': '未解析到推文内容'}
                
    except Exception as e: 
        logger.error(f"Twitter API 获取失败: {e}")
        return {'success': False, 'error': str(e)}

async def fetch_personal_dynamics(limit: int = 10) -> Dict[str, Any]:
    """
    独立获取全平台个人登录态下的订阅/关注动态
    """
    try:
        china_region = is_china_region()
        if china_region:
            logger.info("检测到中文区域，获取B站和微博个人动态")
            b_dyn, w_dyn = await asyncio.gather(
                fetch_bilibili_personal_dynamic(limit),
                fetch_weibo_personal_dynamic(limit),
                return_exceptions=True
            )
            # 异常隔离与安全降级
            b_dyn = {'success': False, 'error': str(b_dyn)} if isinstance(b_dyn, Exception) else b_dyn
            w_dyn = {'success': False, 'error': str(w_dyn)} if isinstance(w_dyn, Exception) else w_dyn

            top_success = b_dyn.get('success', False) or w_dyn.get('success', False)
            return {'success': top_success, 'region': 'china', 
                    'bilibili_dynamic': b_dyn, 'weibo_dynamic': w_dyn}
        else:
            logger.info("检测到非中文区域，获取Reddit和Twitter个人动态")
            r_dyn, t_dyn = await asyncio.gather(
                fetch_reddit_personal_dynamic(limit),
                fetch_twitter_personal_dynamic(limit),
                return_exceptions=True
            )
            r_dyn = {'success': False, 'error': str(r_dyn)} if isinstance(r_dyn, Exception) else r_dyn
            t_dyn = {'success': False, 'error': str(t_dyn)} if isinstance(t_dyn, Exception) else t_dyn
            
            top_success = r_dyn.get('success', False) or t_dyn.get('success', False)
            return {'success': top_success, 'region': 'non-china', 'reddit_dynamic': r_dyn, 'twitter_dynamic': t_dyn}
    except Exception as e:
        logger.error(f"获取个人动态内容失败: {e}")
        return {'success': False, 'error': str(e)}

def format_personal_dynamics(data: Dict[str, Any]) -> str:
    """格式化个人动态 (结构优化版：全配置表驱动 + 层级排版)"""
    output_lines = []
    region = data.get('region', 'china')
    
    if region == 'china':
        # 配置表：(数据字典键名, 展示标题, 列表的键名)
        platforms = [
            ('bilibili_dynamic', 'B站关注UP主动态', 'dynamics'),
            ('weibo_dynamic', '微博个人关注动态', 'statuses')
        ]
        
        for key, title, list_key in platforms:
            dyn_data = data.get(key, {})
            # 海象运算符 := 提取列表，如果为空则直接跳过该平台
            if dyn_data.get('success') and (items := dyn_data.get(list_key, [])):
                output_lines.append(f"【{title}】")
                
                for i, item in enumerate(items[:5], 1):
                    # 统一了排版结构，保证所有平台的缩进严格对齐 (3个空格)
                    author = item.get('author', '未知')
                    timestamp = item.get('timestamp', '')
                    content = item.get('content', '')
                    
                    output_lines.append(f"{i}. {author} ({timestamp})")
                    output_lines.append(f"   内容: {content}")
                    
                output_lines.append("") 
                
        return "\n".join(output_lines).strip() or "暂时无法获取关注动态"
        
    else:
        # 海外平台配置表
        platforms = [
            ('reddit_dynamic', 'Reddit Subscribed Posts', 'posts'),
            ('twitter_dynamic', 'Twitter Timeline', 'tweets')
        ]
        
        for key, title, list_key in platforms:
            dyn_data = data.get(key, {})
            if dyn_data.get('success') and (items := dyn_data.get(list_key, [])):
                output_lines.append(f"【{title}】")
                
                for i, item in enumerate(items[:5], 1):
                    if key == 'reddit_dynamic':
                        output_lines.append(f"{i}. {item.get('title')}")
                        output_lines.append(f"   Subreddit: {item.get('subreddit')} | Score: {item.get('score')} upvotes")
                    else:
                        output_lines.append(f"{i}. {item.get('author')}: {item.get('content')}")
                        
                output_lines.append("")
                
        return "\n".join(output_lines).strip() or "No personal timeline available"

# =======================================================
# 测试用的主函数
# =======================================================

async def main():
    """
    Web爬虫的测试函数
    自动检测区域并获取相应内容
    """
    china_region = is_china_region()
    
    if china_region:
        print("检测到中文区域")
        print("正在获取热门内容（B站、微博）...")
    else:
        print("检测到非中文区域")
        print("正在获取热门内容（Reddit、Twitter）...")
    
    content = await fetch_trending_content(
        bilibili_limit=5, 
        weibo_limit=5,
        reddit_limit=5,
        twitter_limit=5
    )
    
    if content['success']:
        formatted = format_trending_content(content)
        print("\n" + "="*50)
        print(formatted)
        print("="*50)
    else:
        if china_region:
            print(f"获取失败: {content.get('error')}")
        else:
            print(f"获取失败: {content.get('error')}")


if __name__ == "__main__":
    asyncio.run(main())