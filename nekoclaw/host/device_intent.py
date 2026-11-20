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

from nekoclaw.host.capabilities import BUILTIN_GROUP_BROWSER, BUILTIN_GROUP_KEYBOARD_MOUSE, BUILTIN_GROUPS

# Protect URLs before splitting on punctuation, including those inside a quoted
# navigation target. Other quoted instructions are examples or button labels.
# After NFKC a Chinese comma looks like an ASCII comma. Keep punctuation
# inside URL paths/queries (e.g. ?q=click), but stop before a Chinese clause.
_URL = re.compile(
    r"(?:https?://|www\.)(?:[^\s。,;!?<>\"'“”‘’「」『』`]|[,;!?](?=[a-z0-9%_~./&=+\-]))+", re.I
)
_QUOTED = re.compile(
    r"```[\s\S]*?(?:```|$)|`[^`\n]*`|\"[^\"\n]*\"|(?<!\w)'[^'\n]+'(?!\w)|"
    r"“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』"
)
_CLAUSE = re.compile(
    r"[，,。；;！？!?\n]|\.(?:\s+|$)|\b(?:but|then|instead|however)\b|(?=\bwithout\b)|"
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
_WEB_TARGET = (
    r"(?:网页|網頁|页面|頁面|网站|網站|网址|網址|链接|連結|鏈接|浏览器|瀏覽器|标签页|分頁|"
    r"\b(?:browser|web\s?page|page|website|site|urls?|link|tabs?|chrome|edge(?!\s+cases?\b)|firefox)\b|https?://|"
    r"\b[\w-]+\.(?:com|org|net|cn|io|dev)\b)"
)
_WEB_CONTEXT = re.compile(_WEB_TARGET)
_WEB_ACTION = re.compile(
    r"打开|打開|访问|訪問|浏览(?!器)|瀏覽(?!器)|刷新|切换|切換|关闭|關閉|查看|检查|檢查|阅读|閱讀|"
    r"点击(?!率|量)|點擊(?!率|量)|填写|填寫|搜索|搜尋|用.{0,5}(?:浏览器|瀏覽器)|"
    r"列出(?:当前|目前|當前|所有|全部|浏览器|瀏覽器|打开|打開|的|中)*(?:标签页|標籤頁|分頁)|"
    r"\b(?:list|show)\s+(?:(?:the|my|all|current|open|browser)\s+)*tabs?\b|"
    r"\b(?:open|visit|browse|navigate|go to|load|reload|refresh|switch|close|read|inspect|click(?!-through)|fill|use)\b"
)
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
_CLICK = re.compile(r"点击(?!率|量)|點擊(?!率|量)|双击|雙擊|右击|右鍵點|\b(?:click|double-click|right-click)\b(?!-through)")
_SCREENSHOT_ACTION = re.compile(
    r"截(?:一?[个個張张]|一下).{0,10}[图圖]|(?:^\s*|请|請|帮我|幫我|给我|給我)(?:截屏|截[图圖])|"
    r"^\s*(?:给|給|为|為|对|對).{0,10}(?:网页|網頁|屏幕|桌面)(?:截屏|截[图圖])|"
    r"\b(?:take|capture)\s+(?:(?:a|the|my|desktop|screen)\s+){0,3}screenshot\b|^\s*(?:please\s+)?screenshot\b"
)
_DESKTOP_TARGET = re.compile(r"桌面|屏幕|螢幕|任务栏|工作列|\b(?:desktop|taskbar|start menu|system tray)\b")


def select_device_groups(text: str) -> tuple[str, ...]:
    """Return the matching group names, in the gateway's canonical group order."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _URL.sub("https:// ", text)
    text = _QUOTED.sub(lambda m: "https:// " if m[0][1:-1].strip() == "https://" else " ", text)
    groups: set[str] = set()
    for clause in _CLAUSE.split(text):
        if _NON_ACTION.search(clause):
            continue
        screenshot = bool(_SCREENSHOT_ACTION.search(clause))
        browser = bool(_WEB_CONTEXT.search(clause) and (_WEB_ACTION.search(clause) or screenshot)) or bool(
            _OTHER_WEB.search(clause)
        )
        if browser:
            groups.add(BUILTIN_GROUP_BROWSER)
        desktop_target = bool(_DESKTOP_TARGET.search(clause))
        if _DESKTOP_ACTION.search(clause) or (screenshot and (not browser or desktop_target)):
            groups.add(BUILTIN_GROUP_KEYBOARD_MOUSE)
        if _CLICK.search(clause) and (desktop_target or (not browser and BUILTIN_GROUP_BROWSER not in groups)):
            groups.add(BUILTIN_GROUP_KEYBOARD_MOUSE)
    return tuple(group for group in BUILTIN_GROUPS if group in groups)
