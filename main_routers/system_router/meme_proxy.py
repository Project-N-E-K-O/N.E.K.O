# -*- coding: utf-8 -*-
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

"""Meme image proxy endpoint (/meme/proxy-image) with TTL cache.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import logger, router
from urllib.parse import unquote
from fastapi.responses import JSONResponse, Response
import ssl
import httpx
from cachetools import TTLCache
from utils.meme_fetcher import MEME_ALLOWED_HOSTS


# 统一的表情包代理缓存，使用 byte-based 限制 (50MB)，防止 OOM
MEME_PROXY_CACHE = TTLCache(
    maxsize=50 * 1024 * 1024,  # 50MB 内存预算
    ttl=1800,
    getsizeof=lambda item: len(item.get('body', b''))
)


@router.get('/meme/proxy-image')
async def proxy_meme_image(url: str):
    return await fetch_meme_image_response(url, write_cache=True)


async def fetch_meme_image_response(url: str, *, write_cache: bool = True):
    """
    Proxy a remote meme image, solving CORS issues, with SSRF protection.
    """
    
    # 检查缓存
    cache_key = url
    if cache_key in MEME_PROXY_CACHE:
        logger.info(f"[Meme Proxy] 命中缓存: {url[:60]}...")
        cached = MEME_PROXY_CACHE[cache_key]
        return Response(
            content=cached['body'],
            media_type=cached['content_type'],
            headers={
                'Cache-Control': 'public, max-age=86400',
                'X-Cache': 'HIT',
                'X-Content-Type-Options': 'nosniff'
            }
        )
    
    try:
        logger.info(f"[Meme Proxy] 收到代理请求, url: {url[:100] if url else 'None'}...")
        
        if not url:
            return JSONResponse(content={"success": False, "error": "缺少URL参数"}, status_code=400)
        
        decoded_url = unquote(url)
        if not decoded_url.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "无效的URL"}, status_code=400)
        
        allowed_hosts = MEME_ALLOWED_HOSTS
        
        from urllib.parse import urlparse, urljoin
        parsed = urlparse(decoded_url)
        hostname = (parsed.hostname or '').lower()
        
        if not any(hostname == host or hostname.endswith('.' + host) for host in allowed_hosts):
            logger.warning(f"[Meme Proxy] 非法域名请求: {hostname}")
            return JSONResponse(content={"success": False, "error": f"不允许代理该域名: {hostname}"}, status_code=403)

        # 构建请求头
        # 【修复】完善所有域名的 Referer 映射，避免被反爬拦截
        referer_map = {
            'img.soutula.com': 'https://fabiaoqing.com/',
            'fabiaoqing.com': 'https://fabiaoqing.com/',
            # 2026-04-16: doutub.com 域名易主挂黑产，停用
            # 'qn.doutub.com': 'https://www.doutub.com/',
            # 'doutub.com': 'https://www.doutub.com/',
            'i.imgflip.com': 'https://imgflip.com/',
            'imgflip.com': 'https://imgflip.com/',
            'soutula.com': 'https://fabiaoqing.com/',
            'img.doutupk.com': 'https://www.doutupk.com/',
            'doutupk.com': 'https://www.doutupk.com/',
        }
        referer = referer_map.get(hostname, f'{parsed.scheme}://{hostname}/')
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Referer': referer,
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'
        }

        # 使用流式下载以严格控制资源大小，防止内存溢出或大文件攻击
        MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB 限制

        # 已知 SSL 证书有问题的 CDN 域名（如七牛 CDN hostname mismatch），
        # 对这些域名首次请求即使用宽松 SSL，避免白白浪费一次超时。
        # 2026-04-16: qn.doutub.com 随 doutub.com 域名易主停用；白名单当前为空，
        # 其它域名仍走 ssl.SSLError 降级分支兜底。
        _SSL_RELAXED_HOSTS: set[str] = set()
        need_relaxed_ssl = hostname in _SSL_RELAXED_HOSTS

        def _make_client(relaxed: bool = False) -> httpx.AsyncClient:
            if relaxed:
                ctx = ssl.create_default_context()
                try:
                    ctx.set_ciphers('DEFAULT@SECLEVEL=1')
                except Exception as e:
                    logger.debug("[Meme Proxy] set_ciphers SECLEVEL=1 不可用，使用默认密码套件: %s", e)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                return httpx.AsyncClient(timeout=15.0, follow_redirects=False, verify=ctx)
            return httpx.AsyncClient(timeout=15.0, follow_redirects=False)

        async with _make_client(relaxed=need_relaxed_ssl) as client:
            current_url = decoded_url
            for _ in range(4):  # 最多跟随 3 次重定向 (4次请求)
                async with client.stream("GET", current_url, headers=headers) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            break
                        
                        new_url = urljoin(current_url, location)
                        new_parsed = urlparse(new_url)
                        new_hostname = (new_parsed.hostname or '').lower()
                        
                        if not any(new_hostname == host or new_hostname.endswith('.' + host) for host in allowed_hosts):
                            logger.warning(f"[Meme Proxy] 重定向到非法域名: {new_hostname}")
                            return JSONResponse(content={"success": False, "error": "非法重定向"}, status_code=403)
                        
                        current_url = new_url
                        continue
                    
                    resp.raise_for_status()
                    
                    # 校验 Content-Type (严格白名单，防 SVG XSS 注入)
                    raw_content_type = resp.headers.get('Content-Type', '').lower()
                    content_type = raw_content_type.split(';', 1)[0].strip()
                    allowed_content_types = {
                        'image/jpeg', 'image/png', 'image/gif', 
                        'image/webp', 'image/avif', 'image/bmp'
                    }
                    if content_type not in allowed_content_types:
                        logger.warning(f"[Meme Proxy] 拒绝非安全图片内容: {raw_content_type}")
                        return JSONResponse(content={"success": False, "error": "格式不支持或含有潜在风险"}, status_code=403)
                    
                    # 校验 Content-Length (如果存在)
                    content_length = resp.headers.get('Content-Length')
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except (ValueError, TypeError):
                            declared_size = None  # 解析失败就当未知长度，靠流式校验兜底
                        if declared_size is not None and declared_size > MAX_IMAGE_SIZE:
                            logger.warning(f"[Meme Proxy] 资源过大 (Content-Length): {content_length}")
                            return JSONResponse(content={"success": False, "error": "资源超过大小限制 (10MB)"}, status_code=413)

                    # 流式读取内容并累加大小校验
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_IMAGE_SIZE:
                            logger.warning(f"[Meme Proxy] 资源过大 (实际读取): {len(body)}")
                            return JSONResponse(content={"success": False, "error": "资源超过大小限制 (10MB)"}, status_code=413)

                    body_bytes = bytes(body)
                    # 存入 TTLCache
                    if write_cache:
                        MEME_PROXY_CACHE[cache_key] = {
                            'body': body_bytes,
                            'content_type': content_type
                        }
                    
                    return Response(
                        content=body_bytes,
                        media_type=content_type,
                        headers={
                            'Cache-Control': 'public, max-age=86400',
                            'X-Cache': 'MISS',
                            'X-Content-Type-Options': 'nosniff'
                        }
                    )
            
            return JSONResponse(content={"success": False, "error": "过多的重定向"}, status_code=400)

    except httpx.TimeoutException:
        return JSONResponse(content={"success": False, "error": "请求超时"}, status_code=504)
    except (ssl.SSLError, httpx.ConnectError) as e:
        # SSL 握手失败：对白名单内的表情包域名降级重试（宽松 SSL）
        is_ssl = isinstance(e, ssl.SSLError) or 'SSL' in str(e) or 'certificate' in str(e).lower()
        if is_ssl and not need_relaxed_ssl:
            logger.warning(f"[Meme Proxy] SSL 失败，降级重试: {hostname} ({e})")
            try:
                async with _make_client(relaxed=True) as fallback_client:
                    async with fallback_client.stream("GET", decoded_url, headers=headers) as resp:
                        resp.raise_for_status()
                        raw_ct = resp.headers.get('Content-Type', '').lower()
                        ct = raw_ct.split(';', 1)[0].strip()
                        allowed_ct = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif', 'image/bmp'}
                        if ct not in allowed_ct:
                            return JSONResponse(content={"success": False, "error": "格式不支持"}, status_code=403)
                        body = bytearray()
                        async for chunk in resp.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_IMAGE_SIZE:
                                return JSONResponse(content={"success": False, "error": "资源超过大小限制"}, status_code=413)
                        body_bytes = bytes(body)
                        if write_cache:
                            MEME_PROXY_CACHE[cache_key] = {'body': body_bytes, 'content_type': ct}
                        return Response(
                            content=body_bytes, media_type=ct,
                            headers={'Cache-Control': 'public, max-age=86400', 'X-Cache': 'MISS-SSL-FALLBACK', 'X-Content-Type-Options': 'nosniff'}
                        )
            except Exception as fallback_e:
                logger.error(f"[Meme Proxy] SSL 降级重试也失败: {fallback_e}")
                return JSONResponse(content={"success": False, "error": str(fallback_e)}, status_code=500)
        logger.error(f"[Meme Proxy] 代理失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)
    except Exception as e:
        logger.error(f"[Meme Proxy] 代理失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)
