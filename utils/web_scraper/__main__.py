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
"""Command-line smoke entry point for the web-scraper package."""

import asyncio

from ._shared import is_china_region
from .trending_content import fetch_trending_content, format_trending_content


async def main():
    """
    Web crawler test function
    Auto-detects the region and fetches matching content
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
