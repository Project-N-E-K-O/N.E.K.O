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

"""Choose built-in tool groups worth showing on the first model request.

This is a cheap hint about the commission text, independent of permission and
availability. Unrecognised requests can still discover either group normally.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

from config.nekoclaw_settings import BUILTIN_GROUP_BROWSER, BUILTIN_GROUP_KEYBOARD_MOUSE, BUILTIN_GROUPS

# Protect URLs before splitting on punctuation, including those inside a quoted
# navigation target. Other quoted instructions are examples or button labels.
# After NFKC a Chinese comma looks like an ASCII comma. Keep punctuation
# inside URL paths/queries (e.g. ?q=click), but stop before a Chinese clause.
_DOMAIN = r"(?a:(?<![\w./\\-])(?:[a-z0-9-]+\.)+(?:com|org|net|cn|io|dev)\b)"
_URL = re.compile(
    rf"(?:https?://|www\.|{_DOMAIN}(?=[/?#]))"
    r"(?:[^\s。,;!?<>\"'“”‘’「」『』`]|[,;!?](?=[a-z0-9%_~./&=+\-]))+", re.I
)
_QUOTED = re.compile(
    r"```[\s\S]*?(?:```|$)|`[^`\n]*`|\"[^\"\n]*\"|(?<!\w)'[^'\n]+'(?!\w)|"
    r"“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』"
)
_CLAUSE = re.compile(
    r"[，,。；;！？!?\n]|\.(?:\s+|$)|\b(?:but|then|instead|however)\b|(?=\bwithout\b)|"
    r"\band\s+(?=(?:describe|translate|explain)\b)|[并並](?=描述|翻译|翻譯|解释|解釋)|"
    r"但是|不过|不過|然后|然後|接着|接著|改为|改為|改用|再(?=打开|打開|点击|點擊|截|按|用)"
)
# A mention in prose, code, help or a prohibition is a poor reason to load
# the group. A subsequent affirmative instruction gets its own clause.
_NON_ACTION = re.compile(
    r"不要|不必|不用|无需|無需|禁止|别(?:用|打|点|開|开|截|按)|別(?:用|打|點|開|截|按)|"
    r"如何|怎么|怎麼|怎样|怎樣|教程|文案|翻译|翻譯|解释|解釋|示例|示範|例句|写.{0,4}(?:代码|脚本)|"
    r"\b(?:don['’]t|do not|never|avoid|without|explain|describe|translate|tutorial|documentation|"
    r"how (?:to|do|can|would)|write (?:a |an |the )?(?:script|guide|test|code|instructions))\b|"
    r"하지\s*마|하지\s*않|ないで|しない|方法|\b(?:no|não|не)\s+"
)
_EXPLICIT_WEB = re.compile(
    r"(?:浏览器|瀏覽器|\b(?:browser|chrome|edge(?!\s+cases?\b)|firefox)\b)(?!\s*(?a:apis?\b))|https?://|"
    + _DOMAIN
)
_WEB_OBJECT = re.compile(
    r"网页|網頁|页面|頁面|网站|網站|网址|網址|链接|連結|鏈接|标签页|分頁|"
    r"\b(?:web\s?page|page|website|site|urls?|link|tabs?)\b"
)
_CODE_FILE = re.compile(r"(?<![\w./\\-])[\w./\\-]+\.(?:tsx?|jsx?|py|vue|md|json|ya?ml|html?|css)\b", re.ASCII)
_CODE_CONTEXT = re.compile(
    r"代码|代碼|组件|組件|配置|函数|函數|源文件|包名|"
    r"\b(?:apis?|bugs?|code|packages?|config(?:uration)?|components?|functions?|"
    r"indentation|tabs? to spaces?)\b"
)
_FILE_OBJECT = re.compile(r"\b(?:files?|folders?)\b|文件夹|資料夾")
# Only the object being opened can establish a local folder target. A folder
# mentioned later as the destination of an attachment does not locate a click.
_OPEN_FOLDER = re.compile(
    r"^\s*(?:(?:(?:请|請|帮我|幫我|先|再)\s*)*"
    r"(?:在(?:本地|本机|本機|桌面|文件(?:资源)?管理器|檔案(?:總管|管理器)|访达|訪達)[上中里裡]?\s*)?"
    r"(?:打开|打開|进入|進入)|"
    r"(?:please\s+)?(?:open|enter|go\s+into)\s+)(?P<target>.*?)(?=\band\b|[并並把将將]|保存|另存|$)"
)
_FOLDER_NAME = re.compile(r"文件[夹夾]|資料夾|\bfolder\b")
_LOCAL_FOLDER = re.compile(
    r"本地|本机|本機|文件管理器|檔案管理器|\b(?:local|file manager|this pc|my computer)\b|"
    r"(?:(?<![a-z0-9_-])downloads|下载|下載)\s*(?:文件[夹夾]|資料夾|\bfolder\b)|"
    r"(?:文件[夹夾]|資料夾|\bfolder\b)\s*downloads\b"
)
_LOCAL_PATH = re.compile(r"(?:[a-z]:[\\/]|\\\\[^\\\s]+\\|(?:~|\.{1,2})[\\/]|/(?:home|users|mnt|volumes)/)")
_UPLOAD = re.compile(r"上[传傳]|\bupload(?:ing)?\b")
_WEB_ACTION = re.compile(
    r"打开|打開|访问|訪問|浏览(?!器)|瀏覽(?!器)|刷新|切换|切換|关闭|關閉|查看|检查|檢查|阅读|閱讀|"
    r"填写|填寫|搜索|搜尋|"
    r"列出(?:当前|目前|當前|所有|全部|浏览器|瀏覽器|打开|打開|的|中)*(?:标签页|標籤頁|分頁)|"
    r"\b(?:list|show)\s+(?:(?:the|my|all|current|open|browser)\s+)*tabs?\b|"
    r"\b(?:open|visit|browse|navigate|go to|load|reload|refresh|switch|close|read|inspect|fill)\b"
)
_WEB_USE = re.compile(r"用.{0,5}(?:浏览器|瀏覽器)|\buse\b")
_OTHER_WEB = re.compile(
    r"(?:ブラウザ|ウェブページ|リンク).{0,12}(?:開い|開く|クリック)|"
    r"(?:브라우저|웹\s?페이지|링크).{0,12}(?:열어|열기|클릭)|"
    r"\b(?:abre|abra|abrir|visita|visite|открой(?:те)?)\s+(?:(?:el|la|o|a)\s+)?"
    r"(?:navegador|p[áa]gina|sitio|site|сайт|браузер|ссылку|https?://)"
)
_DESKTOP_ACTION = re.compile(
    r"(?:用|移动|移動|操作|操控|控制|使用).{0,5}(?:键鼠|鍵鼠|鼠标|滑鼠|键盘|鍵盤)|"
    r"(?:查看|看看|看一下|捕捉).{0,3}(?:屏幕|螢幕|桌面)|"
    r"按(?:下|住|一下)?\s*(?:ctrl|alt|shift|win\b|enter|esc\b|回车|回車)|"
    r"\b(?:use|move|control)\s+(?:(?:the|my|your)\s+)?(?:mouse|keyboard)\b|"
    r"\bpress\s+(?:ctrl|alt|shift|enter|escape|win)\b|"
    r"\b(?:capture|inspect)\s+(?:the |my )?(?:screen|desktop)\b|"
    r"スクリーンショットを撮|화면을?\s*캡처|\b(?:captura|capture)\s+(?:la |a )?(?:pantalla|tela)\b|сделай(?:те)? скриншот"
)
_CLICK = re.compile(
    r"(?:点击|點擊|双击|雙擊|右击|右鍵點)(?!率|量|事件|处理|處理|监听|監聽|回调|回調)|"
    r"\b(?:click|double-click|right-click)\b(?!-through|[-\s]+(?:handling|handlers?|listeners?|callbacks?|events?)\b)"
)
_SCREENSHOT_ACTION = re.compile(
    r"截(?:一?[个個張张]|一下).{0,10}[图圖]|(?:^\s*|请|請|帮我|幫我|给我|給我)(?:截屏|截[图圖]|截桌面)|"
    r"^\s*(?:给|給|为|為|对|對).{0,10}(?:网页|網頁|屏幕|桌面)(?:截屏|截[图圖])|"
    r"\b(?:take|capture)\s+(?:(?:a|the|my|desktop|screen)\s+){0,3}screenshot\b|^\s*(?:please\s+)?screenshot\b"
)
_DESKTOP_TARGET = re.compile(
    r"桌面|屏幕|螢幕|任务栏|工作列|文件资源管理器|檔案總管|访达|訪達|"
    r"\b(?:desktop|taskbar|start menu|system tray|file explorer|finder)\b"
)
_SWITCH_TARGET = re.compile(r"切[换換][到至]|[转轉]到|回到|返回|\b(?:switch(?:\s+over)?|go\s+back|return)\s+to\b")
_JOINED_SWITCH = re.compile(r"\band\s+(?=switch(?:\s+over)?\s+to\b)|[并並](?:且)?(?=切[换換][到至])")
_SWITCH_ACTION = re.compile(
    r"\band\s+(?=(?:(?:double-|right-)?click|type|press|open|use)\b)|"
    r"[并並](?:且)?(?=[点點双雙][击擊]|[输輸]入|打[开開]|按|截)"
)
_SWITCH_CONTEXT = re.compile(
    r"\s+(?P<prep>in|inside|within|on|with|from|using)\s+(?=(?:the|this|that|my|current|browser|chrome|edge|firefox)\b)|"
    r"(?:在|位[于於])(?=(?:当前|當前|这|這|该|該)|[^\s，,。()]+(?:[里裡内內]|之中))|[（(]"
)
# Match the destination of the switch. Settings and language names can be
# capitalized just like an application, so a title-cased word is not evidence.
_APP_TARGET = re.compile(
    r"(?:应用|應用|程序|程式|窗口|視窗|\b(?:app|application|window))(?:\s+now)?\s*$|"
    r"^\s*(?:the\s+)?window\s+showing\b|"
    r"^\s*(?:the\s+)?(?:记事本|記事本|微信|notepad|wechat|(?:microsoft\s+)?(?:excel|word)|vs\s+code)\s*$"
)
_JOINED_TYPING = re.compile(r"[并並](?:且)?[输輸]入|\band\s+type\b")


def _action_clauses(text: str) -> Iterator[str]:
    for clause in _CLAUSE.split(text):
        if not _NON_ACTION.search(clause):
            # Evaluate "click ... and switch to ..." in that order. A
            # prohibition on the combined instruction applies to both actions.
            yield from _JOINED_SWITCH.split(clause)


def select_device_groups(text: str, *, browser_context: bool = False) -> tuple[str, ...]:
    """Classify this instruction; a previous browser target can locate an unqualified click."""
    groups, _ = classify_device_intent(text, browser_context=browser_context)
    return groups


def classify_device_intent(
    text: str, *, browser_context: bool | None = False, inherit_target: bool = True,
) -> tuple[tuple[str, ...], bool | None]:
    """Return device groups and the click target for the next instruction.

    When the initial surface is still unknown, unrelated input keeps it unknown.
    With inherit_target=False, unrelated input returns no target; the initial
    surface still locates an unqualified click.
    A switch away from the browser can change the target without needing a tool.
    """
    next_browser_context = browser_context if inherit_target else None
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _URL.sub("https:// ", text)
    text = _QUOTED.sub(lambda m: "https:// " if m[0][1:-1].strip() == "https://" else " ", text)
    groups: set[str] = set()
    web_context = browser_context
    for clause in _action_clauses(text):
        # Words like page and tabs also name source files, modules and indentation.
        # In development tasks require an actual browser or address for web tools.
        without_files = _CODE_FILE.sub(" ", clause)
        switch = _SWITCH_TARGET.search(clause)
        destination = None
        surface = without_files
        device_actions = clause
        desktop_target = bool(_DESKTOP_TARGET.search(clause))
        if switch:
            # A location describes where the change happens. A coordinated
            # action has its own object, e.g. "and click the desktop icon".
            parts = _SWITCH_ACTION.split(clause[switch.end():], maxsplit=1)
            location = _SWITCH_CONTEXT.search(parts[0])
            target = parts[0][:location.start()].rstrip() if location else parts[0].rstrip()
            destination = _APP_TARGET.search(target)
            desktop = _DESKTOP_TARGET.search(target)
            desktop_target = bool(desktop and desktop.end() == len(target)) or (
                len(parts) > 1 and bool(_DESKTOP_TARGET.search(parts[1]))
            )
            remaining = " ".join([target, *parts[1:]])
            device_actions = clause[:switch.start()] + " " + remaining
            # A setting can still be changed "in the browser"; a named
            # destination instead establishes where the user is going.
            if destination or desktop_target:
                # A web app can run in a browser; moving from the browser
                # instead names the old surface, not where this app runs.
                web_object_text = parts[0] if location and location["prep"] != "from" else target
                surface = _CODE_FILE.sub(" ", " ".join([web_object_text, *parts[1:]]))
        # "Code" in the app name is not a request to work on source code.
        code_text = clause
        if destination:
            code_text = clause[:switch.end()] + clause[switch.end() + len(target):]
        code = without_files != clause or bool(_CODE_CONTEXT.search(code_text))
        explicit_web = bool(_EXPLICIT_WEB.search(surface))
        web_object = bool(_WEB_OBJECT.search(surface))
        file_object = bool(_FILE_OBJECT.search(clause))
        web_target = explicit_web or (web_object and not code and not file_object)
        screenshot = bool(_SCREENSHOT_ACTION.search(clause))
        click = bool(_CLICK.search(without_files))
        web_action = switch or _WEB_ACTION.search(without_files) or (not code and _WEB_USE.search(without_files))
        browser = bool(web_target and (screenshot or click or web_action)) or bool(
            _OTHER_WEB.search(clause)
        )
        desktop_action = bool(_DESKTOP_ACTION.search(device_actions))
        switches_away = switch and not web_target and not code and (desktop_target or destination)
        if switches_away:
            next_browser_context = False
            if _JOINED_TYPING.search(clause[switch.end():]):
                desktop_action = True
        if not (explicit_web or web_object or code) and (opened := _OPEN_FOLDER.match(clause)) and not _UPLOAD.search(clause):
            target = opened["target"]
            path = _LOCAL_PATH.search(target)
            if path or _FOLDER_NAME.search(target):
                if not web_context or path or _LOCAL_FOLDER.search(opened[0]) or _DESKTOP_TARGET.search(opened[0]):
                    desktop_action = True
                else:
                    browser = True
        # File mentions (such as a page upload) do not change where a later
        # click lands. Clicking a file itself is handled in its own clause.
        if desktop_target or desktop_action or (not web_target and screenshot) or switches_away:
            web_context = False
        web_context = web_context or explicit_web or browser
        if click and web_context and not desktop_target and not file_object and not code:
            browser = True
        if browser:
            groups.add(BUILTIN_GROUP_BROWSER)
            next_browser_context = True
        # An ambiguous web object can fall back to discovery. Do not turn a
        # page click or screenshot into input or capture on the real desktop.
        desktop_fallback = desktop_target or not (browser or web_object)
        desktop_click = click and desktop_fallback and (desktop_target or not code)
        if desktop_action or (screenshot and desktop_fallback) or desktop_click:
            groups.add(BUILTIN_GROUP_KEYBOARD_MOUSE)
            next_browser_context = False
            web_context = False
    selected = tuple(group for group in BUILTIN_GROUPS if group in groups)
    return selected, next_browser_context
