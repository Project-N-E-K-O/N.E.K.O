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
import logging
from urllib.parse import quote
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from bs4 import BeautifulSoup
import os
from pathlib import Path
import json

# 从 language_utils 导入区域检测功能
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


def _get_bilibili_credential():
    """
    从文件加载Bilibili认证信息，返回Credential对象
    
    支持从以下位置读取cookies：
    1. ~/bilibili_cookies.json
    2. config/bilibili_cookies.json
    3. ./bilibili_cookies.json
    
    Returns:
        Credential对象，如果加载失败则返回None
    """
    try:
        from bilibili_api import Credential
        
        # 查找可能的cookie文件位置
        possible_paths = [
            Path(os.path.expanduser('~')) / 'bilibili_cookies.json',
            Path('config') / 'bilibili_cookies.json',
            Path('.') / 'bilibili_cookies.json',
        ]
        
        for cookie_file in possible_paths:
            if cookie_file.exists():
                with open(cookie_file, 'r', encoding='utf-8') as f:
                    cookie_data = json.load(f)
                    
                    # 提取必要的认证信息
                    cookies = {}
                    
                    # EditThisCookie/Cookie-Editor格式 (数组)
                    if isinstance(cookie_data, list):
                        for cookie in cookie_data:
                            if cookie.get('domain', '').endswith('bilibili.com'):
                                cookies[cookie['name']] = cookie['value']
                    
                    # 简单的键值对格式
                    elif isinstance(cookie_data, dict):
                        cookies = cookie_data
                    
                    # 创建Credential对象
                    if cookies:
                        sessdata = cookies.get('SESSDATA', '')
                        bili_jct = cookies.get('bili_jct', '')
                        buvid3 = cookies.get('buvid3', '')
                        dedeuserid = cookies.get('DedeUserID', '')
                        
                        if sessdata:
                            credential = Credential(
                                sessdata=sessdata,
                                bili_jct=bili_jct,
                                buvid3=buvid3,
                                dedeuserid=dedeuserid
                            )
                            logger.info(f"✅ 成功从文件加载 Bilibili 认证信息: {cookie_file}")
                            return credential
                        else:
                            logger.warning(f"⚠️ Cookie文件缺少SESSDATA: {cookie_file}")
    except ImportError:
        logger.debug("bilibili_api 库未安装")
    except Exception as e:
        logger.debug(f"从文件加载认证信息失败: {e}")
    
    # 如果没有找到cookie文件，记录提示信息
    logger.info("💡 提示：要使用个性化B站推荐，请导出cookies到以下任一位置：")
    logger.info(f"   1. {Path(os.path.expanduser('~')) / 'bilibili_cookies.json'}")
    logger.info(f"   2. {Path('config') / 'bilibili_cookies.json'}")
    logger.info("   使用浏览器扩展 'EditThisCookie' 或 'Cookie-Editor' 导出为JSON格式")
    
    return None


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
        if result and 'item' in result:
            items = result['item']
            for item in items[:limit]:
                # 提取视频信息
                bvid = item.get('bvid', '')
                # 有些项目可能是广告或其他类型，跳过没有bvid的
                if not bvid:
                    continue
                    
                videos.append({
                    'title': item.get('title', ''),
                    'desc': item.get('desc', ''),
                    'author': item.get('owner', {}).get('name', ''),
                    'view': item.get('stat', {}).get('view', 0),
                    'like': item.get('stat', {}).get('like', 0),
                    'bvid': bvid,
                    'url': f'https://www.bilibili.com/video/{bvid}'
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
        # 格式化B站内容（中文）
        bilibili_data = trending_content.get('bilibili', {})
        if bilibili_data.get('success'):
            output_lines.append("【B站首页推荐】")
            videos = bilibili_data.get('videos', [])
            
            for i, video in enumerate(videos[:5], 1):  # 只显示前5个
                title = video.get('title', '')
                author = video.get('author', '')
                url = video.get('url', '')
                
                output_lines.append(f"{i}. {title}")
                output_lines.append(f"   UP主: {author}")
                if url:
                    output_lines.append(f"   链接: {url}")
            
            output_lines.append("")  # 空行
        
        # 格式化微博内容（中文）
        weibo_data = trending_content.get('weibo', {})
        if weibo_data.get('success'):
            output_lines.append("【微博热议话题】")
            trending_list = weibo_data.get('trending', [])
            
            for i, item in enumerate(trending_list[:5], 1):  # 只显示前5个
                word = item.get('word', '')
                note = item.get('note', '')
                url = item.get('url', '')
                
                line = f"{i}. {word}"
                if note:
                    line += f" [{note}]"
                output_lines.append(line)
                if url:
                    output_lines.append(f"   链接: {url}")
        
        if not output_lines:
            return "暂时无法获取推荐内容"
    else:
        # 格式化Reddit内容（英文）
        reddit_data = trending_content.get('reddit', {})
        if reddit_data.get('success'):
            output_lines.append("【Reddit Hot Posts】")
            posts = reddit_data.get('posts', [])
            
            for i, post in enumerate(posts[:5], 1):  # 只显示前5个
                title = post.get('title', '')
                subreddit = post.get('subreddit', '')
                score = post.get('score', '')
                url = post.get('url', '')
                
                output_lines.append(f"{i}. {title}")
                if subreddit:
                    output_lines.append(f"   {subreddit} | {score} upvotes")
                if url:
                    output_lines.append(f"   Link: {url}")
            
            output_lines.append("")  # 空行
        
        # 格式化Twitter内容（英文）
        twitter_data = trending_content.get('twitter', {})
        if twitter_data.get('success'):
            output_lines.append("【Twitter Trending Topics】")
            trending_list = twitter_data.get('trending', [])
            
            for i, item in enumerate(trending_list[:5], 1):  # 只显示前5个
                word = item.get('word', '')
                tweet_count = item.get('tweet_count', '')
                note = item.get('note', '')
                url = item.get('url', '')
                
                line = f"{i}. {word}"
                if tweet_count and tweet_count != 'N/A':
                    line += f" ({tweet_count} tweets)"
                if note:
                    line += f" - {note}"
                output_lines.append(line)
                if url:
                    output_lines.append(f"   Link: {url}")
        
        if not output_lines:
            return "暂时无法获取热门内容"
    
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
        
        # 使用correction模型配置（轻量级模型，适合此任务）
        correction_config = config_manager.get_model_api_config('correction')
        
        llm = ChatOpenAI(
            model=correction_config['model'],
            base_url=correction_config['base_url'],
            api_key=correction_config['api_key'],
            temperature=1.0,  # 提高temperature以获得更多样化的结果
            timeout=10.0
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


# 测试用的主函数
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
