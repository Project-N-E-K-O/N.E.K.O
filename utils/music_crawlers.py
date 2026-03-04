"""
音乐爬虫模块，用于从不同平台搜索和抓取音乐。

-   **功能**: 
    -   根据用户所在区域（中国/非中国）选择合适的音乐源。
    -   支持从网易云音乐、Musopen（古典乐）、FMA（免版权音乐）等平台抓取。
    -   所有爬虫都返回 APlayer 兼容的音频格式。
-   **设计**: 
    -   采用统一的 `BaseMusicCrawler` 基类，封装了通用的 `httpx` 请求逻辑、日志记录和 User-Agent 管理。
    -   每个平台实现为 `BaseMusicCrawler` 的子类，只需重写 `search` 方法即可。
    -   主函数 `fetch_music_content` 通过 `asyncio.gather` 并发执行多个爬虫，并根据区域和关键词有无进行智能调度。
"""

import asyncio
import httpx
import random
import re
import json
from urllib.parse import unquote
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional

from utils.logger_config import get_module_logger

# ==================================================
# 1. 模块级设置
# ==================================================

logger = get_module_logger(__name__)

# User-Agent 池，模仿 web_scraper.py 的做法，以避免被识别
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

def get_random_user_agent() -> str:
    """随机获取一个User-Agent"""
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
        """关闭 httpx 客户端"""
        await self.client.aclose()

# =======================================================
# 3. 各平台爬虫实现
# =======================================================

class NeteaseCrawler(BaseMusicCrawler):
    """网易云音乐爬虫，支持搜索并过滤 VIP/付费歌曲。"""
    def __init__(self):
        super().__init__("网易云音乐")
        self.client.headers.update({
            'Referer': 'http://music.163.com/',
            'Content-Type': 'application/x-www-form-urlencoded'
        })

    async def search(self, keyword: str, limit: int = 1) -> List[Dict[str, Any]]:
        if not keyword:
            logger.debug(f"[{self.platform_name}] 因关键词为空而跳过")
            return []

        logger.info(f"[{self.platform_name}] 正在搜索: {keyword}")
        search_url = "http://music.163.com/api/search/get/web"
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
                audio_url = f"http://music.163.com/song/media/outer/url?id={song_id}.mp3"
                final_results.append(self._format_item(name=song_name, url=audio_url, artist=artist_name, cover=cover_url))
            
            return final_results

        except httpx.TimeoutException:
            logger.warning(f"[{self.platform_name}] 搜索 '{keyword}' 超时")
        except Exception as e:
            logger.error(f"[{self.platform_name}] 搜索 '{keyword}' 失败: {e}", exc_info=True)
        
        return []

class MusopenCrawler(BaseMusicCrawler):
    """Musopen 古典音乐爬虫，用于在无明确关键词时提供背景音乐。"""
    def __init__(self):
        super().__init__("Musopen")
        self.client.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Referer': 'https://www.google.com/',
        })

    async def search(self, keyword: str = "", limit: int = 1) -> List[Dict[str, Any]]:
        logger.info(f"[{self.platform_name}] 正在获取免版权古典音乐...")
        # 随机选择一个古典乐页面
        music_pages = [
            'https://musopen.org/music/43-nocturnes-op-9/', # 肖邦夜曲
            'https://musopen.org/music/801-claire-de-lune/', # 德彪西月光
            'https://musopen.org/music/449-the-four-seasons/' # 维瓦尔第四季
        ]
        url = random.choice(music_pages)

        try:
            response = await self.client.get(url)
            response.raise_for_status()
            
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
                    real_name = unquote(filename_part).replace('.mp3', '').replace('.m4a', '')
                except Exception:
                    real_name = "古典曲目"
                
                results.append(self._format_item(name=real_name, url=link, artist="古典音乐"))
            return results

        except httpx.TimeoutException:
            logger.warning(f"[{self.platform_name}] 访问 {url} 超时")
        except Exception as e:
            logger.error(f"[{self.platform_name}] 抓取失败: {e}", exc_info=True)
        
        return []

class FMACrawler(BaseMusicCrawler):
    """FMA (Free Music Archive) 爬虫，用于搜索免版权音乐。"""
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
                
                if audio_url:
                    results.append(self._format_item(name=title, url=audio_url, artist=artist))
            return results

        except httpx.TimeoutException:
            logger.warning(f"[{self.platform_name}] 搜索 '{keyword}' 超时")
        except Exception as e:
            logger.error(f"[{self.platform_name}] 搜索 '{keyword}' 失败: {e}", exc_info=True)
        
        return []

# =======================================================
# 4. 主调度函数
# =======================================================

async def fetch_music_content(keyword: str, limit: int = 1) -> Dict[str, Any]:
    """
    主音乐获取函数，根据关键词和区域智能调度爬虫。

    - 如果有关键词，主要使用网易云音乐搜索。
    - 如果没有关键词，从 Musopen 和 FMA 获取推荐的背景音乐。
    - 在非中国区域，优先使用 FMA。
    """
    china = is_china_region()
    logger.info(f"音乐搜索请求: keyword='{keyword}', limit={limit}, is_china_region={china}")

    tasks = []
    all_crawlers = {
        'netease': NeteaseCrawler(),
        'fma': FMACrawler(),
        'musopen': MusopenCrawler()
    }

    # 根据场景选择要运行的爬虫
    if keyword: # 用户指定了想听什么
        if china:
            # 国内优先使用网易云
            tasks.append(all_crawlers['netease'].search(keyword, limit))
        else:
            # 国外使用 FMA
            tasks.append(all_crawlers['fma'].search(keyword, limit))
        # 备选方案
        tasks.append(all_crawlers['netease'].search(keyword, limit)) 

    else: # 用户没说听什么，推荐背景音乐
        if china:
            # 国内优先来点古典乐
            tasks.append(all_crawlers['musopen'].search(limit=limit))
            tasks.append(all_crawlers['fma'].search(keyword="lofi", limit=limit)) # lofi 作为备选
        else:
            # 国外优先来点 lofi
            tasks.append(all_crawlers['fma'].search(keyword="lofi", limit=limit))
            tasks.append(all_crawlers['musopen'].search(limit=limit))

    all_results = []
    try:
        # 并发执行所有任务
        crawler_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 清理并收集结果
        for result in crawler_results:
            if isinstance(result, Exception):
                logger.error(f"一个音乐爬虫在 gather 中失败: {result}")
            elif result:
                all_results.extend(result)
    finally:
        # 确保所有爬虫的客户端都被关闭
        await asyncio.gather(*[crawler.close() for crawler in all_crawlers.values()])

    if not all_results:
        logger.warning("所有音乐源均未返回任何结果")
        return {
            'success': False,
            'error': '未能找到任何相关音乐',
            'data': []
        }

    # 去重（防止不同源返回同一首歌）
    seen_urls = set()
    unique_results = []
    for item in all_results:
        if item['url'] not in seen_urls:
            unique_results.append(item)
            seen_urls.add(item['url'])
    
    logger.info(f"成功获取到 {len(unique_results)} 首音乐")
    return {
        'success': True,
        'data': unique_results[:limit]
    }

# =======================================================
# 5. 用于独立测试的入口
# =======================================================

async def main():
    """测试函数"""
    print("--- 正在测试有关键词的搜索 (周杰伦) ---")
    results_with_keyword = await fetch_music_content(keyword="周杰伦", limit=2)
    print(json.dumps(results_with_keyword, indent=2, ensure_ascii=False))
    
    print("\n" + "="*50 + "\n")

    print("--- 正在测试无关键词的推荐 ---")
    results_no_keyword = await fetch_music_content(keyword="", limit=1)
    print(json.dumps(results_no_keyword, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    asyncio.run(main())
