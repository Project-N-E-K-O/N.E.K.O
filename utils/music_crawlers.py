"""
音乐爬虫模块，用于从不同平台搜索和抓取音乐。

-   **功能**: 
    -   根据用户所在区域（中国/非中国）选择合适的音乐源。
    -   支持从网易云音乐、Musopen（古典乐）、FMA（免版权音乐）等平台抓取。
    -   所有爬虫都返回 APlayer 兼容的音频格式。
-   **设计**: 
    -   采用统一的 `BaseMusicCrawler` 基类，封装了通用的 `httpx` 请求逻辑、日志记录和 User-Agent 管理。
    -   每个平台实现为 `BaseMusicCrawler` 的子类，只需重写 `search` 方法即可。
    -   主函数 `fetch_music_content` 通过 `asyncio.gather` 并发执行多个爬虫，并根据区域、关键词和多样性策略进行智能调度。
    -   实现了短期去重机制，避免同一首歌曲在短时间内被重复爬取。
"""

import asyncio
import httpx
import random
import re
import json
import time
import urllib.parse
from urllib.parse import unquote
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from collections import Counter

from utils.logger_config import get_module_logger

# ==================================================
# 1. 模块级设置
# ==================================================

logger = get_module_logger(__name__)

# User-Agent 池
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

# ==================================================
# 去重与多样性管理
# ==================================================

class MusicCache:
    """
    歌曲缓存管理器，实现短期去重和多样性评估
    """
    
    def __init__(self, expire_seconds: int = 300):
        self.cache = []
        self.expire_seconds = expire_seconds
        self.last_cleanup = time.time()
    
    def _cleanup(self):
        """
        清理过期缓存（按歌曲 TTL 删除过期项）
        """
        current_time = time.time()
        # 移除已过期的项
        self.cache = [
            item for item in self.cache
            if current_time - item.get('timestamp', 0) < self.expire_seconds
        ]
        self.last_cleanup = current_time
    
    def is_duplicate(self, url: str, name: str, artist: str) -> bool:
        """
        检查是否重复
        """
        self._cleanup()
        for item in self.cache:
            if item['url'] == url:
                return True
            if item['name'] == name and item['artist'] == artist:
                return True
        return False
    
    def add(self, track: Dict[str, Any]):
        """
        添加歌曲到缓存
        """
        self._cleanup()
        self.cache.append({
            'url': track.get('url', ''),
            'name': track.get('name', ''),
            'artist': track.get('artist', ''),
            'timestamp': time.time()
        })
    
    def filter_duplicates(self, tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        过滤重复歌曲（只过滤，不写入缓存）
        """
        self._cleanup()
        filtered = []
        for track in tracks:
            if not self.is_duplicate(track.get('url', ''), track.get('name', ''), track.get('artist', '')):
                filtered.append(track)
        return filtered
    
    def mark_as_played(self, tracks: List[Dict[str, Any]]):
        """
        将实际返回的歌曲标记为已播放（写入缓存）
        """
        for track in tracks:
            self.add(track)
    
    def get_diversity_score(self, tracks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        评估歌曲多样性
        """
        if not tracks:
            return {'score': 0, 'artist_diversity': 0, 'style_notes': []}
        
        artists = [t.get('artist', '未知') for t in tracks]
        artist_counter = Counter(artists)
        unique_artists = len(artist_counter)
        
        artist_diversity = unique_artists / len(tracks) if tracks else 0
        
        # 风格多样性评估（基于关键词）
        style_notes = []
        if anykw(tracks, ['lofi', 'chill', 'relax', 'ambient']):
            style_notes.append('放松氛围')
        if anykw(tracks, ['pop', '流行']):
            style_notes.append('流行')
        if anykw(tracks, ['rock', '摇滚']):
            style_notes.append('摇滚')
        if anykw(tracks, ['电子', 'electronic', 'edm']):
            style_notes.append('电子')
        if anykw(tracks, ['hiphop', 'rap', '说唱']):
            style_notes.append('说唱')
        if anykw(tracks, ['古典', '钢琴', 'classical', 'piano']):
            style_notes.append('古典')
        
        # 计算多样性分数
        style_score = min(len(style_notes) / 6.0, 1.0)  # 最多6种风格
        overall_score = (artist_diversity * 0.6 + style_score * 0.4) * 100
        
        return {
            'score': round(overall_score, 1),
            'artist_diversity': round(artist_diversity * 100, 1),
            'unique_artists': unique_artists,
            'style_notes': style_notes
        }

def anykw(tracks: List[Dict[str, Any]], keywords: List[str]) -> bool:
    """
    检查 tracks 中是否包含任意关键词
    """
    for track in tracks:
        text = f"{track.get('name', '')} {track.get('artist', '')}".lower()
        if any(kw.lower() in text for kw in keywords):
            return True
    return False

# 全局缓存实例
music_cache = MusicCache(expire_seconds=300)

def get_random_user_agent() -> str:
    """
    随机获取一个User-Agent
    """
    return random.choice(USER_AGENTS)

# 区域检测，与 web_scraper.py 保持一致
try:
    from utils.language_utils import is_china_region
except ImportError:
    import locale
    def is_china_region() -> bool:
        try:
            loc = locale.getdefaultlocale()[0]
            return loc and 'zh' in loc.lower() and 'cn' in loc.lower()
        except Exception:
            return False

# =======================================================
# 2. 爬虫基类
# =======================================================

class BaseMusicCrawler:
    """
    音乐爬虫的基类，封装了通用的请求逻辑和格式化方法。
    """
    def __init__(self, platform_name: str):
        self.platform_name = platform_name
        self.client = httpx.AsyncClient(
            headers={'User-Agent': get_random_user_agent()},
            timeout=10.0,
            follow_redirects=True
        )

    async def search(self, keyword: str = "", limit: int = 1) -> List[Dict[str, Any]]:
        """
        每个子类必须实现的核心搜索方法。
        
        Args:
            keyword: 搜索关键词。
            limit: 希望返回的结果数量。

        Returns:
            一个包含 APlayer 格式字典的列表。
        """
        raise NotImplementedError

    def _format_item(self, name: str, url: str, artist: str = "未知艺术家", cover: str = "") -> Dict[str, Any]:
        """
        将抓取到的数据统一为 APlayer 兼容的格式。
        """
        return {
            'name': name,
            'artist': artist,
            'url': url,
            'cover': cover or f'https://dummyimage.com/150x150/44b7fe/fff&text={self.platform_name}',
            'theme': '#44b7fe'  # 统一使用蓝色主题
        }

    async def close(self):
        """
        关闭 httpx 客户端
        """
        await self.client.aclose()

# =======================================================
# 3. 各平台爬虫实现
# =======================================================

class NeteaseCrawler(BaseMusicCrawler):
    """
    网易云音乐爬虫，支持搜索并过滤 VIP/付费歌曲。
    """
    def __init__(self):
        super().__init__("网易云音乐")
        self.client.headers.update({
            'Referer': 'https://music.163.com/',
            'Content-Type': 'application/x-www-form-urlencoded'
        })

    async def search(self, keyword: str, limit: int = 1) -> List[Dict[str, Any]]:
        if not keyword:
            logger.debug(f"[{self.platform_name}] 因关键词为空而跳过")
            return []

        logger.info(f"[{self.platform_name}] 正在搜索: {keyword}")
        search_url = "https://music.163.com/api/search/get/web"
        data = {'s': keyword, 'type': 1, 'offset': 0, 'limit': 20} # 多获取一些用于筛选
        
        try:
            response = await self.client.post(search_url, data=data)
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200 or not result.get("result", {}).get("songs"):
                logger.warning(f"[{self.platform_name}] API 未返回有效歌曲: {result}")
                return []

            songs = result["result"]["songs"]
            found_songs = []

            # 优先选择免费或会员可播的歌曲
            for song in songs:
                # `fee` == 0 (免费), `fee` == 8 (会员免费)
                if song.get("fee", 1) in [0, 8]:
                    found_songs.append(song)
            
            # 如果没有免费的，就使用第一首作为备选
            if not found_songs and songs:
                found_songs.append(songs[0])

            if not found_songs:
                return []

            # 格式化输出
            final_results = []
            for song in found_songs[:limit]:
                song_id = song.get("id")
                song_name = song.get("name", "未知曲目")
                artists = song.get("artists", [])
                artist_name = artists[0].get("name", "未知") if artists else "未知"
                cover_url = song.get("album", {}).get("picUrl", "")
                # 使用外链地址，无需付费即可播放
                audio_url = f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
                final_results.append(self._format_item(name=song_name, url=audio_url, artist=artist_name, cover=cover_url))
            
            return final_results

        except httpx.TimeoutException:
            logger.warning(f"[{self.platform_name}] 搜索 '{keyword}' 超时")
        except Exception as e:
            logger.error(f"[{self.platform_name}] 搜索 '{keyword}' 失败: {e}", exc_info=True)
        
        return []


class SoundCloudCrawler(BaseMusicCrawler):
    """
    SoundCloud 爬虫，自动动态获取鉴权 Token
    """
    def __init__(self):
        super().__init__("SoundCloud")
        self.client_id = None

    async def _get_dynamic_client_id(self):
        """
        动态去 SoundCloud 的 JS 脚本里提取最新的 client_id
        """
        if self.client_id:
            return self.client_id
        
        try:
            res = await self.client.get("https://soundcloud.com/")
            # 找到主页挂载的所有的 JS 脚本
            scripts = re.findall(r'<script crossorigin src="([^"]+)"></script>', res.text)
            # Token 通常在最后几个 JS 文件里，倒序查找
            for js_url in reversed(scripts):
                js_res = await self.client.get(js_url)
                # 正则匹配 32 位的 client_id
                match = re.search(r'client_id:"([^"]{32})"', js_res.text)
                if match:
                    self.client_id = match.group(1)
                    logger.info(f"[{self.platform_name}] 成功动态获取 Client ID")
                    return self.client_id
        except Exception as e:
            logger.warning(f"[{self.platform_name}] 动态获取 Client ID 失败: {e}")
        
        return None

    async def search(self, keyword: str = "lofi", limit: int = 1) -> List[Dict[str, Any]]:
        logger.info(f"[{self.platform_name}] 正在搜索: {keyword}")
        
        # 加入最多 2 次的重试机制，防 Token 突然过期
        for attempt in range(2):
            client_id = await self._get_dynamic_client_id()
            
            if not client_id:
                logger.warning(f"[{self.platform_name}] 无法获取有效的 Client ID，跳过搜索")
                return []
            
            try:
                search_url = "https://api-v2.soundcloud.com/search/tracks"
                params = {
                    'q': keyword,
                    'limit': min(limit * 3, 50),
                    'client_id': client_id,
                }
                
                response = await self.client.get(search_url, params=params)
                
                if response.status_code in [401, 403]:
                    logger.warning(f"[{self.platform_name}] API 认证失败 (尝试 {attempt+1}/2)，清空 Token 准备重试")
                    self.client_id = None  # 核心机制：清空失效的 Token
                    continue               # 立即进入下一次循环，重新去首页偷新 Token
                
                if response.status_code != 200:
                    return []
                
                data = response.json()
                collection = data.get('collection', [])
                
                if not collection:
                    return []

                async def fetch_stream_url(track, client_id):
                    try:
                        title = track.get('title', '未知曲目')
                        artist = track.get('user', {}).get('username', '未知艺术家')
                        
                        transcodings = track.get('media', {}).get('transcodings', [])
                        if not transcodings:
                            return None
                        
                        stream_api = transcodings[0].get('url')
                        if not stream_api:
                            return None
                        
                        stream_res = await self.client.get(f"{stream_api}?client_id={client_id}")
                        real_audio_url = stream_res.json().get('url')
                        
                        if not real_audio_url:
                            return None
                        
                        cover_url = track.get('artwork_url') or ''
                        if cover_url:
                            cover_url = cover_url.replace('-large', '-t500x500')
                        
                        return self._format_item(name=title, url=real_audio_url, artist=artist, cover=cover_url)
                    except Exception:
                        return None

                stream_tasks = [fetch_stream_url(track, client_id) for track in collection[:limit * 3]]
                stream_results = await asyncio.gather(*stream_tasks, return_exceptions=True)
                
                # 过滤出有效结果，取前 limit 个
                valid_results = [r for r in stream_results if r]
                results = valid_results[:limit]
                return results # 成功则直接返回，退出重试循环

            except Exception as e:
                logger.error(f"[{self.platform_name}] 搜索失败: {e}")
                break # 网络或解析报错（非权限问题）没必要重试，直接退出
        
        return []


class iTunesCrawler(BaseMusicCrawler):
    """
    iTunes/Apple Music 爬虫，用于搜索热门音乐。
    """
    def __init__(self):
        super().__init__("iTunes")
        self.api_base = "https://itunes.apple.com"

    async def search(self, keyword: str = "lofi", limit: int = 1) -> List[Dict[str, Any]]:
        logger.info(f"[{self.platform_name}] 正在搜索: {keyword}")
        
        try:
            search_url = f"{self.api_base}/search"
            params = {
                'term': keyword,
                'media': 'music',
                'entity': 'song',
                'limit': min(limit * 3, 50)
            }
            
            response = await self.client.get(search_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if not data or not data.get('results'):
                logger.warning(f"[{self.platform_name}] 未找到与 '{keyword}' 相关的曲目")
                return []

            results = []
            # 使用扩大的候选窗口来提高成功率
            for track in data['results'][:limit * 3]:
                title = track.get('trackName', '未知曲目')
                artist = track.get('artistName', '未知艺术家')
                preview_url = track.get('previewUrl')
                cover_url = track.get('artworkUrl100', '').replace('100x100', '600x600')
                
                if preview_url:
                    results.append(self._format_item(
                        name=title,
                        url=preview_url,
                        artist=artist,
                        cover=cover_url
                    ))
                    if len(results) >= limit:
                        break
            
            return results

        except httpx.TimeoutException:
            logger.warning(f"[{self.platform_name}] 搜索 '{keyword}' 超时")
        except Exception as e:
            logger.error(f"[{self.platform_name}] 搜索 '{keyword}' 失败: {e}", exc_info=True)
        
        return []

class MusopenCrawler(BaseMusicCrawler):
    """
    Musopen 古典音乐爬虫，用于在无明确关键词时提供背景音乐。
    """
    def __init__(self):
        super().__init__("Musopen")
        # --- 恢复豪华版浏览器伪装头，突破 403 盾 ---
        self.client.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.google.com/',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Upgrade-Insecure-Requests': '1'
        })

    async def search(self, keyword: str = "", limit: int = 1) -> List[Dict[str, Any]]:
        logger.info(f"[{self.platform_name}] 正在获取免版权古典音乐... 关键词: {keyword}")
        
        # 关键词到页面的映射
        raw_keyword_map = {
            'chopin': 'https://musopen.org/music/43-nocturnes-op-9/',
            'nocturne': 'https://musopen.org/music/43-nocturnes-op-9/',
            '夜曲': 'https://musopen.org/music/43-nocturnes-op-9/',
            '肖邦': 'https://musopen.org/music/43-nocturnes-op-9/',
            'debussy': 'https://musopen.org/music/801-claire-de-lune/',
            'claire de lune': 'https://musopen.org/music/801-claire-de-lune/',
            '月光': 'https://musopen.org/music/801-claire-de-lune/',
            '德彪西': 'https://musopen.org/music/801-claire-de-lune/',
            'vivaldi': 'https://musopen.org/music/449-the-four-seasons/',
            'four seasons': 'https://musopen.org/music/449-the-four-seasons/',
            '四季': 'https://musopen.org/music/449-the-four-seasons/',
            '维瓦尔第': 'https://musopen.org/music/449-the-four-seasons/',
            'beethoven': 'https://musopen.org/music/707-symphony-no-5-in-c-minor-op-67/',
            'symphony no.5': 'https://musopen.org/music/707-symphony-no-5-in-c-minor-op-67/',
            '第五交响曲': 'https://musopen.org/music/707-symphony-no-5-in-c-minor-op-67/',
            '贝多芬': 'https://musopen.org/music/707-symphony-no-5-in-c-minor-op-67/',
            'mozart': 'https://musopen.org/music/466-eine-kleine-nachtmusik/',
            'Eine Kleine Nachtmusik': 'https://musopen.org/music/466-eine-kleine-nachtmusik/',
            '小夜曲': 'https://musopen.org/music/466-eine-kleine-nachtmusik/',
            '莫扎特': 'https://musopen.org/music/466-eine-kleine-nachtmusik/',
            'bach': 'https://musopen.org/music/25172-cello-suite-no-1-in-g-major-bwv-1007/',
            'cello suite': 'https://musopen.org/music/25172-cello-suite-no-1-in-g-major-bwv-1007/',
            '巴赫': 'https://musopen.org/music/25172-cello-suite-no-1-in-g-major-bwv-1007/',
            'classical': 'https://musopen.org/music/43-nocturnes-op-9/',
            '古典': 'https://musopen.org/music/43-nocturnes-op-9/',
            'piano': 'https://musopen.org/music/43-nocturnes-op-9/',
            '钢琴': 'https://musopen.org/music/43-nocturnes-op-9/',
        }
        keyword_map = {k.lower(): v for k, v in raw_keyword_map.items()}
        
        # 随机备用页面列表
        music_pages = [
            'https://musopen.org/music/43-nocturnes-op-9/',
            'https://musopen.org/music/801-claire-de-lune/',
            'https://musopen.org/music/449-the-four-seasons/'
        ]
        
        # 根据关键词选择页面
        if keyword:
            keyword_lower = keyword.lower().strip()
            url = keyword_map.get(keyword_lower)
            if not url:
                # 尝试模糊匹配
                for key, page in keyword_map.items():
                    if key in keyword_lower or keyword_lower in key:
                        url = page
                        break
            if url:
                logger.info(f"[{self.platform_name}] 匹配到关键词 '{keyword}' -> {url}")
            else:
                logger.info(f"[{self.platform_name}] 关键词 '{keyword}' 未匹配，随机选择")
                url = random.choice(music_pages)
        else:
            url = random.choice(music_pages)
            logger.info(f"[{self.platform_name}] 无关键词，随机选择: {url}")

        try:
            response = await self.client.get(url)
            response.raise_for_status()
            # === Musopen 封面抓取 ===
            soup = BeautifulSoup(response.text, 'html.parser')
            cover_url = ""
            
            # 1. 提取网页头部的 Open Graph 图片 (最清晰的原版封面或肖像)
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                cover_url = og_image['content']
                
            # 2. 备用兜底：寻找页面中的专辑缩略图
            if not cover_url:
                main_img = soup.find('img', class_='composer-illustration') or soup.find('img', class_='work-illustration')
                if main_img and main_img.get('src'):
                    cover_url = main_img['src']
            # ===================================
            # 使用正则从页面源码中提取所有 mp3/m4a 链接
            audio_links = re.findall(r'https?://[^\s"\'<>\[\]]+\.(?:mp3|m4a)', response.text)
            unique_links = list(set(audio_links))
            
            if not unique_links:
                logger.warning(f"[{self.platform_name}] 在页面 {url} 未找到音频链接")
                return []

            random.shuffle(unique_links)
            results = []
            for link in unique_links[:limit]:
                # 尝试从链接中解析文件名作为曲目名
                try:
                    filename_part = link.split('filename=')[-1]
                    # 使用 unquote_plus 完美处理 URL 中的加号(+)和百分号乱码
                    real_name = urllib.parse.unquote_plus(filename_part).replace('.mp3', '').replace('.m4a', '')
                except Exception:
                    real_name = "古典曲目"
                # 传入 cover 参数
                results.append(self._format_item(name=real_name, url=link, artist="古典音乐", cover=cover_url))
            return results

        except httpx.TimeoutException:
            logger.warning(f"[{self.platform_name}] 访问 {url} 超时")
        except Exception as e:
            logger.error(f"[{self.platform_name}] 抓取失败: {e}", exc_info=True)
        
        return []

class FMACrawler(BaseMusicCrawler):
    """
    FMA (Free Music Archive) 爬虫，用于搜索免版权音乐。
    """
    def __init__(self):
        super().__init__("FMA")

    async def search(self, keyword: str = "piano", limit: int = 1) -> List[Dict[str, Any]]:
        logger.info(f"[{self.platform_name}] 正在搜索: {keyword}")
        search_url = f'https://freemusicarchive.org/search/?adv=1&quicksearch={keyword}'
        
        try:
            response = await self.client.get(search_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # FMA 将音轨信息存在 `data-track-info` 属性中
            play_items = soup.find_all(attrs={"data-track-info": True})
            
            if not play_items:
                logger.warning(f"[{self.platform_name}] 未找到与 '{keyword}' 相关的曲目")
                return []

            results = []
            for item in play_items[:limit]:
                track_info = json.loads(item['data-track-info'])
                title = track_info.get('title', '未知FMA曲目')
                artist = track_info.get('artistName', '未知FMA艺术家')
                audio_url = track_info.get('playbackUrl')

                # === FMA 封面抓取 ===
                cover_url = ""
                # 1. 尝试从隐藏的 JSON 信息中提取
                if track_info.get('imageFileUrl'):
                    cover_url = track_info['imageFileUrl']
                elif track_info.get('image'):
                    cover_url = track_info['image']
                
                # 2. 如果 JSON 里没存图，沿 DOM 树向上攀爬寻找缩略图
                if not cover_url:
                    # 向上找包含这首歌的卡片父级容器
                    card = item.find_parent('div', class_=re.compile(r'play-item|row|col'))
                    if card:
                        img = card.find('img')
                        if img:
                            # 兼容现代前端框架的 lazyload 懒加载机制
                            cover_url = img.get('src') or img.get('data-src', '')
                
                # 3. 过滤净化：排除无用的 SVG 装饰图标，确保是真实专辑图
                if cover_url and (cover_url.endswith('.svg') or 'icon' in cover_url.lower()):
                    cover_url = ""
                # =============================
                if audio_url:
                    results.append(self._format_item(name=title, url=audio_url, artist=artist, cover=cover_url))
            return results

        except httpx.TimeoutException:
            logger.warning(f"[{self.platform_name}] 搜索 '{keyword}' 超时")
        except Exception as e:
            logger.error(f"[{self.platform_name}] 搜索 '{keyword}' 失败: {e}", exc_info=True)
        
        return []

class BandcampCrawler(BaseMusicCrawler):
    """
    Bandcamp 独立音乐爬虫，极度适合抓取 lofi、环境音和游戏同人OST。
    """
    def __init__(self):
        super().__init__("Bandcamp")

    async def search(self, keyword: str = "lofi", limit: int = 1) -> List[Dict[str, Any]]:
        logger.info(f"[{self.platform_name}] 正在搜索: {keyword}")
        results = []
        try:
            # 改用 Bandcamp 官方搜索页，并限定搜索类型为单曲 (item_type=t)
            url = 'https://bandcamp.com/search'
            params = {'q': keyword, 'item_type': 't'}
            
            response = await self.client.get(url, params=params) # httpx 会自动编码
            if response.status_code != 200:
                return []
                
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 搜索页的链接藏在 .heading a 里面
            items = soup.select('.heading a')
            if not items:
                logger.warning(f"[{self.platform_name}] 未找到与 '{keyword}' 相关的曲目")
                return []
                
            random.shuffle(items)
            
            async def fetch_track(item):
                target_url = item.get('href', '')
                if target_url.startswith('http'):
                    target_url = target_url.split('?')[0]
                else:
                    return None
                
                try:
                    track_res = await self.client.get(target_url)
                    track_soup = BeautifulSoup(track_res.text, 'html.parser')
                    
                    script_data = track_soup.find('script', attrs={'data-tralbum': True})
                    if not script_data:
                        return None
                        
                    tralbum = json.loads(script_data['data-tralbum'])
                    tracks = tralbum.get('trackinfo', [])
                    
                    if not tracks or not tracks[0].get('file') or 'mp3-128' not in tracks[0]['file']:
                        return None
                    
                    audio_url = tracks[0]['file']['mp3-128']
                    title = tracks[0].get('title', '独立曲目')
                    artist = tralbum.get('artist', 'Bandcamp 艺术家')
                    
                    cover_art = track_soup.find('a', class_='popupImage')
                    cover_url = cover_art['href'] if cover_art else ""
                    
                    return self._format_item(
                        name=title,
                        url=audio_url,
                        artist=artist,
                        cover=cover_url
                    )
                except Exception as e:
                    logger.debug(f"[{self.platform_name}] 获取曲目失败: {e}")
                    return None
            
            track_tasks = [fetch_track(item) for item in items[:limit * 3]]
            track_results = await asyncio.gather(*track_tasks, return_exceptions=True)
            
            for track in track_results:
                if track and len(results) < limit:
                    results.append(track)
        except httpx.TimeoutException:
            logger.warning(f"[{self.platform_name}] 搜索 '{keyword}' 超时")
        except Exception as e:
            logger.error(f"[{self.platform_name}] 抓取失败: {e}", exc_info=True)
            
        return results

# =======================================================
# 全局爬虫实例 (利用 httpx 连接池复用提升 30% 速度)
# 懒加载：在首次访问时才实例化，避免模块导入时创建 AsyncClient
# =======================================================
_crawlers_cache = None

def get_crawlers() -> Dict[str, BaseMusicCrawler]:
    global _crawlers_cache
    if _crawlers_cache is None:
        _crawlers_cache = {
            'netease': NeteaseCrawler(),
            'fma': FMACrawler(),
            'musopen': MusopenCrawler(),
            'soundcloud': SoundCloudCrawler(),
            'itunes': iTunesCrawler(),
            'bandcamp': BandcampCrawler(),
        }
    return _crawlers_cache

def get_music_crawlers() -> Dict[str, BaseMusicCrawler]:
    """获取音乐爬虫实例的懒加载访问器"""
    return get_crawlers()

async def close_all_crawlers():
    """
    统一关闭所有全局爬虫实例，释放连接池资源。
    建议在服务关闭时调用（如 main_server.py 的 on_shutdown）。
    """
    global _crawlers_cache
    if _crawlers_cache is None:
        logger.info("音乐爬虫未初始化，无需关闭")
        return
    
    logger.info("正在关闭所有音乐爬虫实例...")
    crawlers = _crawlers_cache
    if crawlers:
        await asyncio.gather(*[crawler.close() for crawler in crawlers.values()])
    _crawlers_cache = None
    logger.info("所有音乐爬虫实例已关闭")

# =======================================================
# 4. 主调度函数
# =======================================================

async def fetch_music_content(keyword: str, limit: int = 1) -> Dict[str, Any]:
    """
    主音乐获取函数，带有“分段截断”的智能并发调度。
    """
    china = is_china_region()
    logger.info(f"音乐搜索请求: keyword='{keyword}', limit={limit}, is_china_region={china}")

    all_results = []
    
    # 使用懒加载访问器获取爬虫实例
    all_crawlers = get_music_crawlers() 

    if keyword: 
        # 场景 A: 用户指定了明确关键词 -> 开启"梯队降级"机制
        kw_lower = keyword.lower()
        classical_keywords = [
            # zh
            "古典", "钢琴", "肖邦", "贝多芬", "莫扎特", "交响", "夜曲",
            # en
            "classical", "piano", "chopin", "beethoven", "mozart", "symphony", "nocturne",
            # ja
            "クラシック", "ピアノ", "ショパン", "ベートーヴェン", "モーツァルト", "交響", "夜想曲",
            # ko
            "클래식", "피아노", "쇼팽", "베토벤", "모차르트", "교향곡", "야상곡",
            # ru
            "классическая", "фортепиано", "шопен", "бетховен", "моцарт", "симфония", "ноктюрн",
        ]
        indie_keywords = [
            "独立",  "电音", "小众", "环境音", 
            "electronic", "chill", "lofi",
            "インディーズ", "電子音楽",
             "인디", "전자음악",
            "инди", "электронная", "лоуфай",
        ]
        chinese_keywords = [
            # zh
            "华语", "中文", "国语", "华语流行", "中文歌",
            # en
            "mandarin", "c-pop", "chinese pop",
            # ja
            "中国語", "中文", "華語", "j-pop", "日本流行",
            # ko
            "한글", "가요", "한국 대중음악", "k-pop", "kpop",
            # ru
            "китайская музыка", "китайский поп",
            # 华语歌手 (常见中文歌手名)
            "周杰伦", "jay chou", "蔡依林", "jolin tsai", "林俊杰", "jj lin",
            "王心凌", "cyndi wang", "五月天", "mayday", "告五人",
            "邓紫棋", "g.e.m.", "陈奕迅", "eason chan", "张学友", "jacky cheung",
            "刘德华", "andy lau", "王菲", "faye wong", "梁静茹", "fish leong",
            "李荣浩", "毛不易", "薛之谦", "赵雷", "许嵩", "徐佳莹",
            # 台流
            "台式", "台客", "闽南语", "台语",
        ]
        
        primary_tasks = []
        
        # --- 组建第一梯队（最优解竞速） ---
        if any(kw in kw_lower for kw in classical_keywords):
            primary_tasks.append(all_crawlers['musopen'].search(keyword, limit))
        elif any(kw in kw_lower for kw in chinese_keywords):
            primary_tasks.append(all_crawlers['netease'].search(keyword, limit))
        elif any(kw in kw_lower for kw in indie_keywords):
            primary_tasks.append(all_crawlers['bandcamp'].search(keyword, limit))
            primary_tasks.append(all_crawlers['soundcloud'].search(keyword, limit))
        else:
            if china:
                primary_tasks.append(all_crawlers['netease'].search(keyword, limit))
            else:
                # 非中文区但检测到华语关键词时，也加入网易云
                if any(kw in kw_lower for kw in chinese_keywords):
                    primary_tasks.append(all_crawlers['netease'].search(keyword, limit))
                primary_tasks.append(all_crawlers['soundcloud'].search(keyword, limit))
                primary_tasks.append(all_crawlers['itunes'].search(keyword, limit))
        
        # 执行第一梯队 - 竞速模式：任一源返回结果即停止等待
        if primary_tasks:
            # 创建任务以便后续取消
            primary_task_objs = [asyncio.create_task(coro) for coro in primary_tasks]
            
            for coro in asyncio.as_completed(primary_task_objs):
                try:
                    res = await coro
                    if isinstance(res, list) and res:
                        all_results.extend(res)
                        logger.info(f"[智能调度] 第一梯队某源命中，取消其他任务")
                        # 取消剩余任务
                        for task in primary_task_objs:
                            if not task.done():
                                task.cancel()
                        # 等待取消完成
                        await asyncio.gather(*primary_task_objs, return_exceptions=True)
                        break
                except asyncio.CancelledError:
                    # 任务被取消，忽略
                    pass
                except Exception as e:
                    logger.warning(f"[智能调度] 第一梯队某源异常: {e}")
                
        # --- 组建第二梯队（兜底截断逻辑） ---
        if not all_results:
            logger.info("[智能调度] 第一梯队未命中，触发第二级兜底引擎...")
            fallback_tasks = []
            # 支持多语言古典乐关键词
            fallback_fma = "piano" if any(kw in kw_lower for kw in [
                "钢琴", "piano", "ピアノ", "피아노", "фортепиано"
            ]) else "relax"
            
            fallback_tasks.append(all_crawlers['netease'].search(keyword, limit))
            fallback_tasks.append(all_crawlers['fma'].search(fallback_fma, limit))
            
            # 兜底梯队也使用竞速模式
            fallback_task_objs = [asyncio.create_task(coro) for coro in fallback_tasks]
            for coro in asyncio.as_completed(fallback_task_objs):
                try:
                    res = await coro
                    if isinstance(res, list) and res:
                        all_results.extend(res)
                        logger.info(f"[智能调度] 兜底源命中，取消其他任务")
                        # 取消剩余任务
                        for task in fallback_task_objs:
                            if not task.done():
                                task.cancel()
                        # 等待取消完成
                        await asyncio.gather(*fallback_task_objs, return_exceptions=True)
                        break
                except asyncio.CancelledError:
                    # 任务被取消，忽略
                    pass
                except Exception as e:
                    logger.warning(f"[智能调度] 兜底源异常: {e}")

    else: 
        # 场景 B: 纯背景音乐推荐 -> 并发盲抽
        tasks = []
        if china:
            china_styles = [
                ('netease', '华语'), ('netease', '流行'), ('netease', '电子'), 
                ('netease', '说唱'), ('musopen', None), ('fma', 'lofi'), 
                ('fma', 'chill'), ('fma', 'electronic'), ('fma', 'hiphop')
            ]
            selected_styles = random.sample(china_styles, min(3, len(china_styles)))
        else:
            global_styles = [
                ('itunes', 'lofi'), ('itunes', 'chill'), ('fma', 'ambient'), 
                ('fma', 'electronic'), ('musopen', None), ('bandcamp', 'indie'), 
                ('bandcamp', 'vgm'), ('bandcamp', 'lofi')
            ]
            selected_styles = random.sample(global_styles, min(3, len(global_styles)))
        
        for source, kw in selected_styles:
            if source == 'musopen':
                tasks.append(all_crawlers['musopen'].search(limit=limit))
            else:
                tasks.append(all_crawlers[source].search(kw, limit))
                
        crawler_results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in crawler_results:
            if isinstance(res, list) and res:
                all_results.extend(res)

    # 统一的去重与返回逻辑
    if not all_results:
        logger.warning("所有音乐源（含兜底）均未返回任何结果")
        return {'success': False, 'error': '未能找到任何相关音乐', 'data': []}

    # URL级别去重
    seen_urls = set()
    unique_results = []
    for item in all_results:
        if item['url'] not in seen_urls:
            unique_results.append(item)
            seen_urls.add(item['url'])
    
    # 使用缓存进行短期去重（只过滤，不写入缓存）
    unique_results = music_cache.filter_duplicates(unique_results)
    
    # 去重后可能为空，需要修正返回语义
    if not unique_results:
        logger.warning("去重后无可用音乐")
        return {'success': False, 'error': '去重后无可用音乐', 'data': []}
    
    # 评估多样性
    diversity_info = music_cache.get_diversity_score(unique_results)
    logger.info(f"成功获取到 {len(unique_results)} 首音乐，多样性评分: {diversity_info['score']}% (风格: {diversity_info['style_notes']}, 独立艺术家: {diversity_info['unique_artists']})")
    
    # 日志只显示前5首
    display_tracks = unique_results[:5]
    log_items = [f"{t.get('name', '未知')[:15]}-{t.get('artist', '未知')[:10]}" for t in display_tracks]
    logger.info(f"[音乐日志] 代表性歌曲: {log_items}")
    
    # 标记实际返回的歌曲为已播放（写入缓存）
    music_cache.mark_as_played(unique_results[:limit])
    
    return {'success': True, 'data': unique_results[:limit], 'diversity': diversity_info}

# =======================================================
# 5. 用于独立测试的入口
# =======================================================

async def main():
    """
    全方位测试函数：测试独立爬虫及智能调度器
    """
    print("==================================================")
    print(" 🚀 阶段一：测试独立爬虫模块")
    print("==================================================\n")

    # 测试新加的 Bandcamp 爬虫
    print("--- 1. 测试 Bandcamp 搜索 (关键词: lofi) ---")
    bandcamp_crawler = BandcampCrawler()
    bc_results = await bandcamp_crawler.search("lofi", limit=2)
    print(f"✅ Bandcamp 找到 {len(bc_results)} 首音乐:")
    for i, r in enumerate(bc_results, 1):
        print(f"  {i}. {r['name']} - {r['artist']}\n     🎵 直链: {r['url'][:70]}...")
    await bandcamp_crawler.close()
    print("\n")

    # 测试 SoundCloud 爬虫
    print("--- 2. 测试 SoundCloud 搜索 (关键词: electronic) ---")
    sc_crawler = SoundCloudCrawler()
    sc_results = await sc_crawler.search("electronic", limit=2)
    print(f"✅ SoundCloud 找到 {len(sc_results)} 首音乐:")
    for i, r in enumerate(sc_results, 1):
        print(f"  {i}. {r['name']} - {r['artist']}\n     🎵 直链: {r['url'][:70]}...")
    await sc_crawler.close()
    print("\n")

    print("==================================================")
    print(" 🧠 阶段二：测试并发智能调度引擎 (fetch_music_content)")
    print("==================================================\n")

    # 测试 1: 古典乐分发
    print("--- 3. 智能调度测试: [肖邦夜曲] -> 预期命中 Musopen 或 网易云兜底 ---")
    results_classical = await fetch_music_content(keyword="肖邦夜曲", limit=1)
    print(json.dumps(results_classical, indent=2, ensure_ascii=False))
    print("\n")

    # 测试 2: 流行乐分发
    print("--- 4. 智能调度测试: [周杰伦] -> 预期命中 网易云 ---")
    results_pop = await fetch_music_content(keyword="周杰伦", limit=1)
    print(json.dumps(results_pop, indent=2, ensure_ascii=False))
    print("\n")

    # 测试 3: 无关键词随机推荐
    print("--- 5. 智能调度测试: [无关键词] -> 预期触发多平台随机并发抽选 ---")
    results_random = await fetch_music_content(keyword="", limit=2)
    print(json.dumps(results_random, indent=2, ensure_ascii=False))
    print("\n==================================================")
    print(" 🎉 全链路测试完毕！")
    await close_all_crawlers()

if __name__ == '__main__':
    # 针对 Windows 环境的 asyncio 报错防范
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())