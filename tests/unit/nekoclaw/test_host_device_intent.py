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

"""First-turn device selection from the current commission's own instructions."""

import pytest

from nekoclaw.host.capabilities import BUILTIN_GROUP_BROWSER as BROWSER
from nekoclaw.host.capabilities import BUILTIN_GROUP_KEYBOARD_MOUSE as DESKTOP
from nekoclaw.host.device_intent import select_device_groups


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("帮我打开这个网页，点击登录按钮。", (BROWSER,)),
        ("在瀏覽器打開這個網址", (BROWSER,)),
        ('Open "https://example.org" and inspect the page.', (BROWSER,)),
        ("Open example.com", (BROWSER,)),
        ("Please take a screenshot of the current browser tab.", (BROWSER,)),
        ("请截个图。", (DESKTOP,)),
        ("截一张网页截图", (BROWSER,)),
        ("分析这张截图。", ()),
        ("把这张网页截图保存成 PDF。", ()),
        ("Compare the colors in this webpage screenshot.", ()),
        ("点击“确定”按钮", (DESKTOP,)),
        ("用鼠标点击桌面图标，然后按下 Ctrl+C。", (DESKTOP,)),
        ("Press Ctrl+Shift+Esc", (DESKTOP,)),
        ("Open this link, then take a desktop screenshot.", (DESKTOP, BROWSER)),
        ("打开这个网站，然后点击桌面图标。", (DESKTOP, BROWSER)),
        ("不要打开浏览器，帮我截屏。", (DESKTOP,)),
        ("Don't use my mouse. Open this website instead.", (BROWSER,)),
        ("把“打开浏览器”翻译成英文，然后按下回车。", (DESKTOP,)),
        ("「リンクを開いて」と書いてください。", ()),
        ("ブラウザを開いてください", (BROWSER,)),
        ("브라우저를 열어 주세요", (BROWSER,)),
        ("Abre el navegador.", (BROWSER,)),
        ("Abra o navegador.", (BROWSER,)),
        ("Открой браузер.", (BROWSER,)),
        ("", ()),
        ("打开 README.md 看看写了什么。", ()),
        ("比较浏览器市场份额和网页点击率", ()),
        ("给我写一段文案：点击按钮就可以打开浏览器。", ()),
        ("解释如何点击此链接", ()),
        ("不要打开网页或点击按钮。", ()),
        ('Summarize this text: "Open a browser, click the link".', ()),
        ("Update sample code: `browser_click()`", ()),
        ("Write a script to open the browser", ()),
        ("Read README.md", ()),
    ],
)
def test_first_turn_device_groups(text, expected):
    assert select_device_groups(text) == expected
