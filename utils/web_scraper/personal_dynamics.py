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
"""Personal social-feed fetchers and formatting."""

from __future__ import annotations

import asyncio
import httpx
import random
import re
from typing import TYPE_CHECKING, Dict, Any, Optional
import os

# bs4 惰性 import（各解析函数内首用加载，utils.module_warmup 后台预热兜底）：本模块被
# system_router 顶层引用、坐在 main_server 启动 import 链上，顶层 bs4 会拖慢端口就绪。
if TYPE_CHECKING:
    pass
import json

from ._shared import get_random_user_agent, is_china_region, logger
from .platform_helpers import _get_bilibili_credential, _get_platform_cookies
from .trending_content import _format_score

async def fetch_bilibili_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    Fetch Bilibili push feed updates
    """
    try:
        credential = _get_bilibili_credential()
        if not credential: 
            return {'success': False, 'error': '未提供Bilibili认证信息'}

        url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all"
        headers = {"User-Agent": get_random_user_agent(), "Referer": "https://t.bilibili.com/"}
        await asyncio.sleep(random.uniform(0.1, 0.5))

        # per-call AsyncClient: 带用户鉴权 cookie —— 不能走共享 client，否则
        # 响应的 Set-Cookie 会自动提取进共享 jar，跨请求污染（httpx 默认行为）
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
            if isinstance(v, dict):
                return v
            else:
                return {}

        dynamic_list = []
        items = data.get("data")
        if isinstance(items, dict):
            items = items.get("items", [])
        else:
            items = []

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
                        if raw_text:
                            content = f"[图文动态] {raw_text}"
                        else:
                            content = "[分享了图片]"
                        
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
                            # Optional live metadata must not discard the dynamic item.
                            pass
                        content = f"[正在直播] {live_title or '快来我的直播间看看吧！'}"
                        
                    case _:
                        if dynamic_type == "DYNAMIC_TYPE_LIVE_RCMD":
                            # 直播开播推送：添加直播间链接
                            content = f"[正在直播] {raw_text or '快来我的直播间看看吧！'}"
                            # 尝试从描述中提取直播间ID
                            room_match = re.search(r'直播间：(\d+)', raw_text)
                            if room_match:
                                specific_url = f"https://live.bilibili.com/{room_match.group(1)}"
                                
                        elif dynamic_type == "DYNAMIC_TYPE_FORWARD":
                            if raw_text:
                                content = f"[转发动态] {raw_text}"
                            else:
                                content = "[转发了动态]"
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
    """
    Fetch the Douyin personal following feed
    Requires: cookies with a real, valid session in the config (douyin_cookies.json)
    Note: Douyin endpoints usually require signature params like X-Bogus; this mainly relies on valid cookies and basic params
    """
    try:
        from utils.cookies_login import validate_cookies
        
        cookies = await asyncio.to_thread(_get_platform_cookies, 'douyin')
        if not cookies:
            return {'success': False, 'error': '未找到抖音 Cookie 配置'}
        
        if not validate_cookies('douyin', cookies):
            return {'success': False, 'error': '抖音 Cookie 核心字段缺失，请检查配置'}

        # 抖音 Web 端关注流接口
        url = "https://www.douyin.com/aweme/v1/web/aweme/following/request/"
        headers = {
            "User-Agent": get_random_user_agent(),
            "Referer": "https://www.douyin.com/",
            "Accept": "application/json, text/plain, */*"
        }

        # 基础参数，实际环境中如果触发风控，可能需要在 URL 中追加抓包获取的 X-Bogus 和 a_bogus
        params = {
            "count": limit,
            "device_platform": "webapp",
            "aid": "6383"
        }

        await asyncio.sleep(random.uniform(0.1, 0.5))

        # per-call AsyncClient: 带用户鉴权 cookie，见 bilibili 同款理由
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=headers, cookies=cookies, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        if data.get("status_code") != 0:
            logger.error(f"抖音API返回异常，可能触发风控: {data}")
            return {'success': False, 'error': "API请求失败，可能需要更新 Cookie 或补全 X-Bogus 签名"}

        dynamic_list = []
        # 兼容不同的数据返回结构，归一化为 list
        raw_data = data.get("data")
        if isinstance(raw_data, list):
            aweme_list = raw_data
        elif isinstance(raw_data, dict):
            aweme_list = (
                raw_data.get("list")
                or raw_data.get("aweme_list")
                or raw_data.get("items")
                or data.get("aweme_list")
                or []
            )
        else:
            aweme_list = data.get("aweme_list") or []

        for item in aweme_list[:limit]:
            try:
                if not isinstance(item, dict):
                    logger.warning(f"抖音动态数据项类型异常: {type(item).__name__}，跳过")
                    continue
                author = item.get("author", {}).get("nickname", "未知博主")
                desc = item.get("desc") or "[分享了视频]"
                aweme_id = item.get("aweme_id", "")

                clean_desc = desc.replace('\n', ' ').strip()
                final_content = f"博主【{author}】: {clean_desc}"

                dynamic_list.append({
                    'author': author,
                    'content': final_content,
                    'timestamp': item.get("create_time", "刚刚"),
                })
                if aweme_id:
                    dynamic_list[-1]['url'] = f"https://www.douyin.com/video/{aweme_id}"
                else:
                    dynamic_list[-1]['url'] = "https://www.douyin.com/"
            except Exception as item_err:
                logger.warning(f"解析抖音动态项失败，跳过: {item_err}")
                continue

        if dynamic_list:
            logger.info(f"✅ 成功获取到 {len(dynamic_list)} 条抖音关注动态")
            return {'success': True, 'dynamics': dynamic_list}
        return {'success': False, 'error': '未解析到抖音动态数据'}

    except Exception as e:
        logger.error(f"获取抖音动态失败: {e}")
        return {'success': False, 'error': str(e)}

async def fetch_kuaishou_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    Fetch the Kuaishou personal following feed (GraphQL endpoint + strict cookies)
    Requires: cookies with a real, valid session in the config (kuaishou_cookies.json)
    """
    try:
        from utils.cookies_login import validate_cookies
        
        cookies = await asyncio.to_thread(_get_platform_cookies, 'kuaishou')
        if not cookies:
            return {'success': False, 'error': '未找到快手 Cookie 配置'}
        
        if not validate_cookies('kuaishou', cookies):
            return {'success': False, 'error': '快手 Cookie 核心字段缺失，请检查配置'}

        url = "https://www.kuaishou.com/graphql"
        headers = {
            "User-Agent": get_random_user_agent(),
            "Referer": "https://www.kuaishou.com/",
            "Content-Type": "application/json",
            "Accept": "*/*"
        }

        # 快手 GraphQL 查询 Payload: visionFollowFeed (关注流)
        payload = {
            "operationName": "visionFollowFeed",
            "variables": {
                "limit": limit
            },
            "query": "fragment photoContent on PhotoEntity {\n  id\n  caption\n  timestamp\n  __typename\n}\n\nfragment feedContent on Feed {\n  type\n  author {\n    id\n    name\n    __typename\n  }\n  photo {\n    ...photoContent\n    __typename\n  }\n  __typename\n}\n\nquery visionFollowFeed($pcursor: String, $limit: Int) {\n  visionFollowFeed(pcursor: $pcursor, limit: $limit) {\n    pcursor\n    feeds {\n      ...feedContent\n      __typename\n    }\n    __typename\n  }\n}\n"
        }

        await asyncio.sleep(random.uniform(0.1, 0.5))

        # per-call AsyncClient: 带用户鉴权 cookie，见 bilibili 同款理由
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.post(url, headers=headers, json=payload, cookies=cookies, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        if data.get("errors"):
            logger.error(f"快手GraphQL返回异常: {data['errors']}")
            return {'success': False, 'error': "GraphQL查询报错，可能是 Cookie 失效"}

        feeds = data.get("data", {}).get("visionFollowFeed", {}).get("feeds", [])
        dynamic_list = []

        for item in feeds[:limit]:
            try:
                if not isinstance(item, dict):
                    logger.warning(f"快手动态数据项类型异常: {type(item).__name__}，跳过")
                    continue
                author = item.get("author", {}).get("name", "未知老铁")
                photo = item.get("photo", {})
                caption = photo.get("caption") or "[分享了作品]"
                photo_id = photo.get("id", "")

                clean_caption = caption.replace('\n', ' ').strip()
                final_content = f"老铁【{author}】: {clean_caption}"

                dynamic_list.append({
                    'author': author,
                    'content': final_content,
                    'timestamp': photo.get("timestamp", "刚刚"),
                })
                if photo_id:
                    dynamic_list[-1]['url'] = f"https://www.kuaishou.com/short-video/{photo_id}"
                else:
                    dynamic_list[-1]['url'] = "https://www.kuaishou.com/"
            except Exception as item_err:
                logger.warning(f"解析快手动态项失败，跳过: {item_err}")
                continue

        if dynamic_list:
            logger.info(f"✅ 成功获取到 {len(dynamic_list)} 条快手关注动态")
            return {'success': True, 'dynamics': dynamic_list}
        return {'success': False, 'error': '未解析到快手动态数据'}

    except Exception as e:
        logger.error(f"获取快手动态失败: {e}")
        return {'success': False, 'error': str(e)}

async def fetch_weibo_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    Fetch the Weibo feed
    Design principles:
    - switch to the Mobile API, bypassing all PC-side risk control entirely
    - only the core login credential SUB is needed; all other cookies are obsolete
    - target changed to: the fixed Container ID of the mobile home following feed
    - must disguise as a mobile browser User-Agent
    """
    try:
        from utils.cookies_login import validate_cookies
        
        weibo_cookies = await asyncio.to_thread(_get_platform_cookies, 'weibo')
        if not weibo_cookies:
            return {'success': False, 'error': '未找到 config/weibo_cookies.json'}
        
        if not validate_cookies('weibo', weibo_cookies):
            return {'success': False, 'error': '微博 Cookie 核心字段缺失，请检查配置'}
        
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
        # per-call AsyncClient: 带用户鉴权 cookie，见 bilibili 同款理由
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, cookies=req_cookies, timeout=10.0)

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

            if clean_text:
                display_text = clean_text
            else:
                display_text = "[分享了图片/动态]"
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
                if len(content) > 50:
                    content = content[:50] + "..."
                # 微博正文是用户面对的内容，不写 logger
                print(f"  - {content}")

            return {'success': True, 'statuses': weibo_list}
        else:
            return {'success': False, 'error': '未解析到微博内容'}

    except Exception as e: 
        logger.error(f"微博动态解析发生错误: {e}")
        return {'success': False, 'error': str(e)}

async def fetch_reddit_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    Fetch Reddit pushed feed posts
    """
    try:
        reddit_cookies = await asyncio.to_thread(_get_platform_cookies, 'reddit')
        if not reddit_cookies: 
            return {'success': False, 'error': '未配置 config/reddit_cookies.json'}
        url = f"https://www.reddit.com/hot.json?limit={limit}"
        headers = {'User-Agent': get_random_user_agent(), 'Accept': 'application/json'}
        await asyncio.sleep(random.uniform(0.1, 0.5))

        # per-call AsyncClient: 带用户鉴权 cookie，见 bilibili 同款理由
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, cookies=reddit_cookies, timeout=10.0)
            response.raise_for_status()
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                return {'success': False, 'error': f'Reddit 响应 JSON 解析失败: {e}'}
        if not isinstance(data, dict):
            return {'success': False, 'error': 'Reddit API 返回格式异常（非 dict）'}
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
    Twitter web-scraping fallback
    """
    try:
        url = "https://twitter.com/home"
        headers = {'User-Agent': get_random_user_agent()}
        # per-call AsyncClient: 带用户鉴权 cookie，见 bilibili 同款理由
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            res = await client.get(url, headers=headers, cookies=cookies, timeout=10.0)

        # 如果被重定向到了登录页，说明 Cookie 彻底失效了
        if "login" in str(res.url) or "logout" in str(res.url):
            return {'success': False, 'error': 'Twitter Cookie 已过期，网页端拒绝访问'}

        tweets = []
        tweet_texts = re.findall(r'"tweet":\{[^}]*"full_text":"([^"]+)"', res.text)
        screen_names = re.findall(r'"screen_name":"([^"]+)"', res.text)

        for i, text in enumerate(tweet_texts[:limit]):
            clean_text = re.sub(r'https://t\.co/\w+', '', text).strip()
            if i < len(screen_names):
                author_str = screen_names[i]
            else:
                author_str = 'Unknown'
            tweets.append({
                'author': f"@{author_str}", 
                'content': clean_text,
                'timestamp': '刚刚'  # 保持与主 API 数据字典格式的统一
            })

        if tweets:
            return {'success': True, 'tweets': tweets}
        else:
            return {'success': False, 'error': '网页正则抓取失败，页面结构可能已变更'}
    except Exception as e: 
        logger.error(f"Twitter 网页抓取 fallback 失败: {e}")
        return {'success': False, 'error': str(e)}

async def fetch_twitter_personal_dynamic(limit: int = 10) -> Dict[str, Any]:
    """
    Fetch the personal Twitter timeline
    """
    
    try:
        from utils.cookies_login import validate_cookies
        
        twitter_cookies = await asyncio.to_thread(_get_platform_cookies, 'twitter')
        if not twitter_cookies:
             return {'success': False, 'error': '未配置 config/twitter_cookies.json'}
        
        if not validate_cookies('twitter', twitter_cookies):
            return {'success': False, 'error': 'Twitter Cookie 核心字段缺失，请检查配置'}
             
        # 提取防伪 CSRF Token。Twitter 必须，否则哪怕有合法 Cookie 也会立刻 401/403
        ct0 = twitter_cookies.get('ct0') or twitter_cookies.get('CT0', '')
        if not ct0:
            logger.warning("Twitter Cookie 中缺少核心字段 ct0，极大可能触发风控拦截")
        
        # Official Web client Bearer Token — read from env, no hardcoded fallback
        bearer_token = os.environ.get("TWITTER_BEARER_TOKEN")
        if not bearer_token:
            logger.warning("TWITTER_BEARER_TOKEN not configured, falling back to web scraping")
            return await _fetch_twitter_personal_web_scraping(limit=limit, cookies=twitter_cookies)
        
        # 切换到更稳定、包含完整推文文本的 v1.1 接口
        url = f"https://api.twitter.com/1.1/statuses/home_timeline.json?tweet_mode=extended&count={limit}"
        
        # 补全极其严格的 Twitter 风控协议头
        headers = {
            'User-Agent': get_random_user_agent(), 
            'Accept': 'application/json',
            'Authorization': f'Bearer {bearer_token}',
            'x-twitter-active-user': 'yes',
            'x-twitter-client-language': 'zh-cn'
        }
        if 'auth_token' in twitter_cookies:
            headers['x-twitter-auth-type'] = 'OAuth2Session'
        else:
            headers['x-twitter-auth-type'] = ''
        headers['x-csrf-token'] = ct0
        
        await asyncio.sleep(random.uniform(0.1, 0.5))

        # per-call AsyncClient: 带用户鉴权 cookie，见 bilibili 同款理由
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, cookies=twitter_cookies, timeout=10.0)

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
    Independently fetch logged-in subscription/following feeds across all platforms
    """
    try:
        china_region = is_china_region()
        if china_region:
            logger.info("检测到中文区域，获取B站、微博、抖音和快手个人动态")
            
            # 1. 将抖音和快手加入并发任务列表
            b_dyn, w_dyn, d_dyn, k_dyn = await asyncio.gather(
                fetch_bilibili_personal_dynamic(limit),
                fetch_weibo_personal_dynamic(limit),
                fetch_douyin_personal_dynamic(limit),
                fetch_kuaishou_personal_dynamic(limit),
                return_exceptions=True
            )
            
            # 2. 增加对抖音和快手的异常隔离与安全降级
            if isinstance(b_dyn, Exception):
                b_dyn = {'success': False, 'error': str(b_dyn)}
            if isinstance(w_dyn, Exception):
                w_dyn = {'success': False, 'error': str(w_dyn)}
            if isinstance(d_dyn, Exception):
                d_dyn = {'success': False, 'error': str(d_dyn)}
            if isinstance(k_dyn, Exception):
                k_dyn = {'success': False, 'error': str(k_dyn)}

            # 3. 只要有一个平台成功，就判定为总体成功
            top_success = any([
                b_dyn.get('success', False), 
                w_dyn.get('success', False),
                d_dyn.get('success', False),
                k_dyn.get('success', False)
            ])
            
            # 4. 封装返回字典
            result = {
                'success': top_success, 
                'region': 'china', 
                'bilibili_dynamic': b_dyn, 
                'weibo_dynamic': w_dyn,
                'douyin_dynamic': d_dyn,
                'kuaishou_dynamic': k_dyn
            }
            
            # 【新增】汇总全平台失败的错误信息给顶层
            if not top_success:
                errors = []
                if b_dyn.get('error'):
                    errors.append(f"B站: {b_dyn.get('error')}")
                if w_dyn.get('error'):
                    errors.append(f"微博: {w_dyn.get('error')}")
                if d_dyn.get('error'):
                    errors.append(f"抖音: {d_dyn.get('error')}")
                if k_dyn.get('error'):
                    errors.append(f"快手: {k_dyn.get('error')}")
                
                if errors:
                    result['error'] = " | ".join(errors)
                else:
                    result['error'] = "所有中文平台均获取失败"
                
            return result
            
        else:
            logger.info("检测到非中文区域，获取Reddit和Twitter个人动态")
            r_dyn, t_dyn = await asyncio.gather(
                fetch_reddit_personal_dynamic(limit),
                fetch_twitter_personal_dynamic(limit),
                return_exceptions=True
            )
            if isinstance(r_dyn, Exception):
                r_dyn = {'success': False, 'error': str(r_dyn)}
            if isinstance(t_dyn, Exception):
                t_dyn = {'success': False, 'error': str(t_dyn)}
            
            top_success = r_dyn.get('success', False) or t_dyn.get('success', False)
            
            result = {
                'success': top_success, 
                'region': 'non-china', 
                'reddit_dynamic': r_dyn, 
                'twitter_dynamic': t_dyn
            }
            
            # 【新增】汇总海外平台失败的错误信息给顶层
            # 【新增】汇总海外平台失败的错误信息给顶层
            if not top_success:
                errors = []
                if r_dyn.get('error'):
                    errors.append(f"Reddit: {r_dyn.get('error')}")
                if t_dyn.get('error'):
                    errors.append(f"Twitter: {t_dyn.get('error')}")
                if errors:
                    result['error'] = " | ".join(errors)
                else:
                    result['error'] = "所有海外平台均获取失败"
                
            return result
            
    except Exception as e:
        logger.error(f"获取个人动态内容失败: {e}")
        return {'success': False, 'error': str(e)}

def format_personal_dynamics(data: Dict[str, Any]) -> str:
    """
    Format personal feeds (structure-optimized: fully config-table driven + hierarchical layout)
    """
    output_lines = []
    region = data.get('region', 'china')
    
    if region == 'china':
        # 配置表：(数据字典键名, 展示标题, 列表的键名)
        platforms = [
            ('bilibili_dynamic', 'B站关注UP主动态', 'dynamics'),
            ('weibo_dynamic', '微博个人关注动态', 'statuses'),
            ('douyin_dynamic', '抖音关注动态', 'dynamics'),
            ('kuaishou_dynamic', '快手关注动态', 'dynamics')
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
