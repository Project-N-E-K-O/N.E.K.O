# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Trending-content fetchers and formatters."""

from __future__ import annotations

import asyncio
import httpx
from utils.external_http_client import get_external_http_client
import random
import re
from typing import TYPE_CHECKING, Dict, List, Any
from urllib.parse import quote

# bs4 惰性 import（各解析函数内首用加载，utils.module_warmup 后台预热兜底）：本模块被
# system_router 顶层引用、坐在 main_server 启动 import 链上，顶层 bs4 会拖慢端口就绪。
if TYPE_CHECKING:
    from bs4 import BeautifulSoup

from ._shared import get_random_user_agent, is_china_region, logger
from .platform_helpers import _get_bilibili_credential, _get_platform_cookies

async def fetch_bilibili_trending(limit: int = 30) -> Dict[str, Any]:
    """
    Fetch Bilibili homepage recommended videos
    Uses the bilibili-api library to fetch homepage video recommendations
    Supports personalized recommendations (when credentials are provided)
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
                if isinstance(rcmd_reason, dict):
                    rcmd_reason_text = rcmd_reason.get('content', '')
                else:
                    rcmd_reason_text = ''
                    
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
    Fetch Reddit hot posts
    Uses Reddit's JSON API to fetch hot posts from r/popular
    
    Args:
        limit: maximum number of posts to return
    
    Returns:
        Dict with success status and post list
    """
    try:
        # Reddit的JSON API端点
        url = f"https://www.reddit.com/r/popular/hot.json?limit={limit}"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'application/json',
        }
        
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        client = get_external_http_client()
        response = await client.get(url, headers=headers, timeout=5.0)
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
            })
            if permalink:
                posts[-1]['url'] = f"https://www.reddit.com{permalink}"
            else:
                posts[-1]['url'] = ''

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
    """Format Reddit scores/comment counts"""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    elif count > 0:
        return str(count)
    return "0"

async def fetch_weibo_trending(limit: int = 10) -> Dict[str, Any]:
    """
    Fetch trending Weibo topics
    Prefers the s.weibo.com hot-search page (refreshes more often), which requires cookies
    Falls back to the public API on failure
    """
    try:
        # 动态获取平台 Cookie，拒绝硬编码
        weibo_cookies = await asyncio.to_thread(_get_platform_cookies, 'weibo')
        sub_cookie = weibo_cookies.get('SUB') or weibo_cookies.get('sub', '')
        if sub_cookie:
            cookie_header = f"SUB={sub_cookie}"
        else:
            cookie_header = ""
        
        # 优先使用s.weibo.com热搜页面（刷新频率更高）
        url = "https://s.weibo.com/top/summary?cate=realtimehot"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Referer': 'https://s.weibo.com/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        if cookie_header:
            headers['Cookie'] = cookie_header
        
        # 添加随机延迟
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        client = get_external_http_client()
        response = await client.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()

        # 检查是否重定向到登录页面
        if 'passport' in str(response.url):
            logger.warning("微博Cookie可能已过期，回退到公开API")
            return await _fetch_weibo_trending_fallback(limit)

        html = response.text
        # BS4 解析放线程池（与 Google/Baidu/Twitter 链路一致）
        from bs4 import BeautifulSoup
        soup = await asyncio.to_thread(BeautifulSoup, html, 'lxml')

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
                if span:
                    hot_text = span.get_text(strip=True)
                else:
                    hot_text = ''
                # 热度可能包含类型标签如"剧集 336075"，需要提取数字
                hot_match = re.search(r'(\d+)', hot_text)
                if hot_match:
                    raw_hot = int(hot_match.group(1))
                else:
                    raw_hot = 0

                # 提取标签（如"剧集"、"晚会"等）
                if hot_text:
                    note = re.sub(r'\d+', '', hot_text).strip()
                else:
                    note = ''

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
    Weibo hot-search fallback — uses the public ajax API
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
        
        client = get_external_http_client()
        response = await client.get(url, headers=headers, timeout=5.0)
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
                if word:
                    search_url = f"https://s.weibo.com/weibo?q={quote(word)}"
                else:
                    search_url = ''

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
    Fetch Twitter/X trending topics
    Uses Twitter's explore page to fetch trends
    
    Args:
        limit: maximum number of trends to return
    
    Returns:
        Dict with success status and trend list
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
        
        client = get_external_http_client()
        response = await client.get(url, headers=headers, timeout=5.0)
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
                if not trend.startswith('@'):
                    trend = '#' + trend

            # 构建搜索URL
            if trend:
                search_url = f"https://twitter.com/search?q={quote(trend)}"
            else:
                search_url = ''

            trending_list.append({
                'word': trend,
            })
            if i < len(tweet_counts):
                trending_list[-1]['tweet_count'] = tweet_counts[i]
            else:
                trending_list[-1]['tweet_count'] = 'N/A'
            trending_list[-1]['note'] = ''
            trending_list[-1]['rank'] = i + 1
            trending_list[-1]['url'] = search_url

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
    Twitter trends fallback
    Uses third-party services to fetch trends, since Twitter's official API requires OAuth
    """
    
    def _parse_trends24(soup: BeautifulSoup, limit: int) -> List[Dict[str, Any]]:
        """Parse the Trends24 page"""
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
        """Parse the GetDayTrends page"""
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
            
            client = get_external_http_client()
            response = await client.get(source['url'], headers=headers, timeout=5.0)

            if response.status_code == 200:
                # BS4 解析 + 解析器迭代统一放线程池，避免阻塞 event loop
                def _parse_in_thread(html: str, parser, _limit: int) -> List[Dict[str, Any]]:
                    from bs4 import BeautifulSoup
                    return parser(BeautifulSoup(html, 'lxml'), _limit)
                trending_list = await asyncio.to_thread(
                    _parse_in_thread, response.text, source['parser'], limit
                )

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
    Fetch trending content based on the user's region
    
    Chinese region: Bilibili videos and trending Weibo topics
    non-Chinese region: Reddit hot posts and Twitter trends
    
    Args:
        bilibili_limit: max Bilibili videos (Chinese region)
        weibo_limit: max Weibo topics (Chinese region)
        reddit_limit: max Reddit posts (non-Chinese region)
        twitter_limit: max Twitter trends (non-Chinese region)
    
    Returns:
        Dict with success status and trending content
        Chinese region: 'bilibili' and 'weibo' keys
        non-Chinese region: 'reddit' and 'twitter' keys
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
    Generic helper for fetching content based on the user's region
    
    Args:
        china_fetch_func: async fetch function used in the Chinese region
        non_china_fetch_func: async fetch function used in the non-Chinese region
        limit: maximum amount of content
        content_key: content key in the result ('video' or 'news')
        china_log_msg: log message for the Chinese region
        non_china_log_msg: log message for the non-Chinese region
    
    Returns:
        Dict with success status and content
    """
    china_region = is_china_region()
    if china_region:
        region = 'china'
    else:
        region = 'non-china'
    
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
    Fetch video content based on the user's region
    
    Chinese region: Bilibili homepage videos
    non-Chinese region: Reddit hot posts
    
    Args:
        limit: maximum amount of content
    
    Returns:
        Dict with success status and video content
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
    Fetch news/trending-topic content based on the user's region
    
    Chinese region: trending Weibo topics
    non-Chinese region: Twitter trends
    
    Args:
        limit: maximum amount of content
    
    Returns:
        Dict with success status and news content
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
    """Format the Bilibili video list"""
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
    """Format the Reddit post list"""
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
    """Format the trending Weibo topic list"""
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
    """Format the Twitter trend list"""
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
    Format trending content into a readable string
    
    Formats automatically by region:
    - Chinese region: Bilibili and Weibo content, displayed in Chinese
    - non-Chinese region: Reddit and Twitter content, displayed in English
    
    Args:
        trending_content: result returned by fetch_trending_content
    
    Returns:
        The formatted string
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
    Format video content into a readable string
    
    Formats automatically by region:
    - Chinese region: Bilibili video content
    - non-Chinese region: Reddit post content
    
    Args:
        video_content: result returned by fetch_video_content
    
    Returns:
        The formatted string
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
    Format news content into a readable string
    
    Formats automatically by region:
    - Chinese region: trending Weibo topics
    - non-Chinese region: Twitter trends
    
    Args:
        news_content: result returned by fetch_news_content
    
    Returns:
        The formatted string
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
