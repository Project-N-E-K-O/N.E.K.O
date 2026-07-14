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

"""Backend screenshot + window title endpoints (/screenshot,
/screenshot/interactive, /get_window_title).

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import (
    _is_loopback_request,
    _is_remote_backend_deployment,
    _json_no_store_response,
    _set_no_store_headers,
    _validate_local_mutation_request,
    logger,
    router,
)
import os
import sys
import asyncio
import base64
import shutil
import subprocess
import tempfile
from fastapi import Request
from fastapi.responses import JSONResponse
from PIL import Image
from utils.pyautogui_diagnostics import format_pyautogui_import_error
from utils.screenshot_utils import (
    compress_screenshot,
    COMPRESS_TARGET_HEIGHT,
    COMPRESS_JPEG_QUALITY,
)


def _run_macos_interactive_screenshot(output_path: str) -> tuple[int, str]:
    cmd = shutil.which("screencapture")
    if not cmd:
        raise FileNotFoundError("screencapture not found")
    completed = subprocess.run(
        [cmd, "-i", "-s", "-x", output_path],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, (completed.stderr or "").strip()


def _image_path_to_jpeg_data_url(image_path: str) -> tuple[str, int]:
    with Image.open(image_path) as shot:
        if shot.mode in ("RGBA", "LA", "P"):
            shot = shot.convert("RGB")
        jpg_bytes = compress_screenshot(
            shot,
            target_h=COMPRESS_TARGET_HEIGHT,
            quality=COMPRESS_JPEG_QUALITY,
        )
    b64 = base64.b64encode(jpg_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}", len(jpg_bytes)


def _is_interactive_screenshot_canceled(platform_name: str, returncode: int, stderr: str, file_size: int) -> bool:
    if file_size > 0:
        return False
    normalized_stderr = str(stderr or "").strip()
    if returncode == 0:
        return True
    if platform_name == "darwin":
        return returncode == 1
    return returncode == 1 and not normalized_stderr


def _format_backend_screenshot_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    lower = text.lower()
    if sys.platform.startswith("linux") and "gnome-screenshot" in lower and not shutil.which("gnome-screenshot"):
        return "gnome-screenshot not installed; install it with: sudo apt install gnome-screenshot"
    if "pillow" in lower:
        try:
            Image.new("RGB", (1, 1))
            if "gnome-screenshot" in lower:
                return "gnome-screenshot not installed; install it with: sudo apt install gnome-screenshot"
        except Exception:
            pass
    return text or type(exc).__name__


@router.get('/get_window_title')
async def get_window_title_api():
    """
    Get the title of the currently active window (Windows only).
    """
    try:
        from utils.web_scraper import get_active_window_title
        title = get_active_window_title()
        if title:
            return JSONResponse({"success": True, "window_title": title})
        return JSONResponse({"success": False, "window_title": None})
    except Exception as e:
        logger.error(f"获取窗口标题失败: {e}")
        return JSONResponse({"success": False, "window_title": None})


@router.post('/screenshot')
async def backend_screenshot(request: Request):
    """
    Backend screenshot fallback: when all frontend screen-capture APIs fail, the backend captures the local screen with pyautogui.
    Security restriction: only requests from loopback addresses are allowed. Returns a JPEG base64 DataURL.
    """
    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"success": False},
    )
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error

    if not _is_loopback_request(request):
        return _json_no_store_response({"success": False, "error": "only available from localhost"}, status_code=403)

    if _is_remote_backend_deployment():
        return _json_no_store_response(
            {"success": False, "error": "backend is configured as remote (NEKO_ACTIVITY_TRACKER_REMOTE); local screenshot disabled"},
            status_code=501,
        )

    try:
        import pyautogui
    except Exception as exc:
        error_message = format_pyautogui_import_error(exc, platform_name=sys.platform)
        logger.error(f"后端截图初始化失败: {error_message}")
        return _json_no_store_response({"success": False, "error": error_message}, status_code=501)

    try:
        def _capture_rgb_screenshot():
            shot = pyautogui.screenshot()
            if shot.mode in ('RGBA', 'LA', 'P'):
                shot = shot.convert('RGB')
            return shot

        shot = await asyncio.to_thread(_capture_rgb_screenshot)

        # macOS 黑屏检测：仅在 macOS 上执行——未授权 Screen Recording 时 pyautogui 返回全黑图片
        # 其他平台（Windows/Linux）全黑截图属正常内容，不应拦截
        if sys.platform == "darwin":
            # 低分辨率采样：把图缩到 16×16 后用 PIL extrema 检测，避免全量 numpy 数组的内存开销
            try:
                thumb = shot.resize((16, 16))
                extrema = thumb.getextrema()  # ((min_r, max_r), (min_g, max_g), (min_b, max_b))
                if all(mx <= 1 for _, mx in extrema):
                    logger.warning("后端截图检测到全黑图片，可能缺少 Screen Recording 权限")
                    return _json_no_store_response(
                        {"success": False, "error": "screenshot is blank (Screen Recording permission may be denied)"},
                        status_code=403,
                    )
            except Exception:
                logger.debug("macOS blank-screen detection failed, skipping check", exc_info=True)

        jpg_bytes = await asyncio.to_thread(
            compress_screenshot, shot, target_h=COMPRESS_TARGET_HEIGHT, quality=COMPRESS_JPEG_QUALITY,
        )
        b64 = base64.b64encode(jpg_bytes).decode('utf-8')
        data_url = f"data:image/jpeg;base64,{b64}"
        return _json_no_store_response({"success": True, "data": data_url, "size": len(jpg_bytes)})
    except Exception as e:
        error_message = _format_backend_screenshot_error(e)
        logger.error(f"后端截图失败: {error_message}")
        return _json_no_store_response({"success": False, "error": error_message}, status_code=500)


@router.post('/screenshot/interactive')
async def backend_interactive_screenshot(request: Request):
    """
    System-native interactive screenshot: preferred by the chat screenshot button.
    Current implementation:
      - macOS: `screencapture` system-level region selection
      - Windows: local full-desktop overlay region selection
    Returns a JPEG DataURL of the user's selection.
    Security restrictions:
      - only requests from loopback addresses are allowed;
      - any request carrying `Origin` or `Referer` (i.e. coming from a browser)
        must still pass the local-mutation CSRF/origin checks, preventing
        arbitrary pages from blind-POSTing localhost to pop up the native
        selection UI (a localhost CSRF);
      - pure server-side loopback calls without `Origin`/`Referer` may skip
        CSRF, reserved for curl / local scripts / tests.
    """
    if not _is_loopback_request(request):
        return _json_no_store_response({"success": False, "error": "only available from localhost"}, status_code=403)

    # 用原始 header 是否存在来判断"这是不是浏览器请求"，而不是 _get_request_origin 的归一化结果。
    # 后者会把 `Origin: null`（sandboxed iframe / file:// / data:）和无效 `Referer` 归一成空串，
    # 让恶意页面可以通过 sandboxed iframe 故意送 `Origin: null` 来绕过 CSRF 校验。
    if request.headers.get("origin") is not None or request.headers.get("referer") is not None:
        validation_error = _validate_local_mutation_request(
            request,
            error_defaults={"success": False},
        )
        if validation_error is not None:
            _set_no_store_headers(validation_error)
            return validation_error

    if _is_remote_backend_deployment():
        return _json_no_store_response(
            {"success": False, "error": "backend is configured as remote (NEKO_ACTIVITY_TRACKER_REMOTE); local interactive screenshot disabled"},
            status_code=501,
        )

    if sys.platform == "darwin":
        runner = _run_macos_interactive_screenshot
    else:
        # Windows / Linux 没有可靠的"系统级框选 + 回传"原语，统一交给前端 Electron
        # 的 desktopCapturer 区域选择路径处理；这里直接 501 让 caller 走兜底链。
        return _json_no_store_response(
            {"success": False, "error": "interactive screenshot is only supported on macOS"},
            status_code=501,
        )

    fd, tmp_path = tempfile.mkstemp(prefix="neko-interactive-shot-", suffix=".png")
    os.close(fd)
    try:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

        returncode, stderr = await asyncio.to_thread(runner, tmp_path)
        file_exists = os.path.exists(tmp_path)
        file_size = os.path.getsize(tmp_path) if file_exists else 0

        if _is_interactive_screenshot_canceled(sys.platform, returncode, stderr, file_size):
            logger.info("系统原生交互截图已取消(returncode=%s, stderr=%s)", returncode, stderr)
            return _json_no_store_response({"success": False, "canceled": True}, status_code=200)

        if file_size <= 0:
            error_message = str(stderr or "").strip() or f"interactive screenshot failed with returncode {returncode}"
            logger.warning(
                "系统原生交互截图失败且未生成文件(returncode=%s, stderr=%s)",
                returncode,
                stderr,
            )
            return _json_no_store_response(
                {"success": False, "canceled": False, "error": error_message},
                status_code=500,
            )

        data_url, jpg_size = await asyncio.to_thread(_image_path_to_jpeg_data_url, tmp_path)
        return _json_no_store_response({
            "success": True,
            "data": data_url,
            "size": jpg_size,
            "interactive": True,
        })
    except FileNotFoundError as e:
        logger.warning("系统原生交互截图不可用: %s", e)
        return _json_no_store_response({"success": False, "error": str(e)}, status_code=501)
    except SystemExit as e:
        # Nuitka 等场景下，缺失某些可选依赖会用 SystemExit 当 sentinel 抛出（继承 BaseException
        # 而非 Exception）。如果不在这里截住，会逃出 asyncio worker thread → 拖死整个后端
        # 进程，连带 Electron shell 一起崩。这里转成普通 500，让前端能继续走兜底链。
        logger.error("系统原生交互截图 runner 抛 SystemExit: %s", e)
        return _json_no_store_response(
            {"success": False, "error": f"interactive screenshot runner aborted: {e}"},
            status_code=500,
        )
    except Exception as e:
        logger.error(f"系统原生交互截图失败: {e}")
        return _json_no_store_response({"success": False, "error": str(e)}, status_code=500)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            logger.debug("清理交互截图临时文件失败: %s", tmp_path, exc_info=True)
