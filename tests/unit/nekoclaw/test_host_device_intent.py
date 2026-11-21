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

import runpy

import pytest

from config import nekoclaw_settings
from nekoclaw.host.capabilities import BUILTIN_GROUP_BROWSER as BROWSER
from nekoclaw.host.capabilities import BUILTIN_GROUP_KEYBOARD_MOUSE as DESKTOP
from nekoclaw.host.device_intent import select_device_groups


@pytest.mark.parametrize(
    ("value", "enabled"),
    [(None, True), ("0", False), ("OFF", False), ("no", False), ("false", False), ("1", True), (" true ", True),
     ("yes", True), ("on", True), ("typo", False)],
)
def test_device_tools_default_and_override(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("NEKOCLAW_FIRST_TURN_DEVICE_TOOLS", raising=False)
    else:
        monkeypatch.setenv("NEKOCLAW_FIRST_TURN_DEVICE_TOOLS", value)
    assert runpy.run_path(nekoclaw_settings.__file__)["FIRST_TURN_DEVICE_TOOLS"] is enabled


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("帮我打开这个网页，点击登录按钮。", (BROWSER,)),
        ("在瀏覽器打開這個網址", (BROWSER,)),
        ('Open "https://example.org" and inspect the page.', (BROWSER,)),
        ("Open example.com", (BROWSER,)),
        ("Open example.com/about.html", (BROWSER,)),
        ("打开example.com/index.html", (BROWSER,)),
        ("Open the file ./example.com/index.html", ()),
        ("Open the website and click Upload file", ()),
        ("Go to the page and click the Download files button", ()),
        ("Click the Files link on the page", ()),
        ("Take a screenshot of the website's config page", ()),
        ("打开 https://example.com，然后截一张桌面截图", (DESKTOP, BROWSER)),
        ("打开https://example.com，点击桌面上的图标", (DESKTOP, BROWSER)),
        ("打开 https://example.com;点击桌面上的图标", (DESKTOP, BROWSER)),
        ("Summarize https://example.com?q=click", ()),
        ("Open the browser and go to example.com without logging in", (BROWSER,)),
        ("Read README.md without opening the browser", ()),
        ("Refresh the cache and fix the edge case", ()),
        ("Open Edge", (BROWSER,)),
        ("Open the file page.tsx and fix the bug", ()),
        ("Use the site-packages folder", ()),
        ("Switch tabs to spaces in config", ()),
        ("Load the page data from the API", ()),
        ("查看页面组件代码", ()),
        ("检查一下网站的 nginx 配置", ()),
        ("Add click handling to browser.tsx", ()),
        ("Open the site in Firefox to check the bug", (BROWSER,)),
        ("打开浏览器访问 example.com，检查组件", (BROWSER,)),
        ("Click the file on the desktop", (DESKTOP,)),
        ("Double-click the Downloads folder", (DESKTOP,)),
        ("Double-click handlers.py in File Explorer", (DESKTOP,)),
        ("Use the browser API to parse the response", ()),
        ("Add a fill method to the browser API", ()),
        ("给浏览器 API 添加搜索功能", ()),
        ("Fill the form on the website", (BROWSER,)),
        ("Use the browser to open example.com", (BROWSER,)),
        ("Right-click the file", (DESKTOP,)),
        ("Add click handling to the file", ()),
        ("Implement a click listener for the button", ()),
        ("给按钮添加点击事件", ()),
        ("Open example.com, then double-click the Downloads folder in File Explorer", (DESKTOP, BROWSER)),
        ("打开网站，再点击文件资源管理器里的文件", (DESKTOP, BROWSER)),
        ("打开浏览器，然后点击登录按钮", (BROWSER,)),
        ("Open the folder named page", ()),
        ("Summarize the article at https://example.com\nclick the accept cookies button", (BROWSER,)),
        ("Summarize the article at https://example.com\nclick the desktop icon", (DESKTOP,)),
        ("Open example.com and describe the layout", (BROWSER,)),
        ("打开这个网页并翻译成中文", (BROWSER,)),
        ("Don't open the website and click the button", ()),
        ("列出浏览器标签页", (BROWSER,)),
        ("List open tabs", (BROWSER,)),
        ("List browser tabs", (BROWSER,)),
        ("列出浏览器的优缺点", ()),
        ("List the pros and cons of a browser", ()),
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


@pytest.mark.parametrize(("text", "expected"), [
    ("点击登录按钮", (BROWSER,)), ("Click the login button", (BROWSER,)),
    ("继续", ()), ("解释如何点击登录按钮", ()), ('Translate "click login"', ()),
    ("截桌面", (DESKTOP,)), ("Click the taskbar icon", (DESKTOP,)),
    ("Double-click the Downloads folder", (DESKTOP,)),
    ("切换到桌面，再点击登录按钮", (DESKTOP,)),
    ("Switch to Notepad, then click OK", (DESKTOP,)),
    ("切换到记事本，再点击确定", (DESKTOP,)),
    ("先截个图，再点击登录按钮", (DESKTOP,)),
    ("Use the mouse to click login", (DESKTOP,)),
])
def test_an_earlier_browser_only_disambiguates_an_unqualified_click(text, expected):
    assert select_device_groups(text, browser_context=True) == expected


@pytest.mark.parametrize(("text", "focus"), [
    ("Switch to Notepad", False), ("切换到记事本", False),
    ("Switch to Excel", False), ("切换到 Excel", False), ("Switch to Microsoft Excel", False),
    ("switch to excel", False), ("切换到EXCEL", False),
    ("Switch to Chinese", True), ("Switch to Dark Mode", True),
    ("Switch to Theme", True),
    ("Switch to Dark Mode in the current window", True),
    ("Switch to Chinese in the current app", True),
    ("Switch to Dark Mode on the desktop", True),
    ("在当前桌面应用里切换到深色模式", True),
    ("切换到深色模式（在当前窗口）", True),
    ("切换到当前窗口的深色模式", True),
    ("Switch to the Example app on the desktop", False),
    ("Switch to the Example app from the browser", False),
    ("切换到 VS Code", False),
    ("Switch to Example", True), ("Switch to the Example app", False),
    ("Summarize the file.", True), ("Don't switch to Notepad", True),
    ('Translate "Switch to Notepad"', True),
])
def test_click_target_can_change_without_mounting_tools(text, focus):
    from nekoclaw.device_intent import classify_device_intent

    groups, next_focus = classify_device_intent(text, browser_context=True)
    assert groups == () and next_focus is focus
    assert select_device_groups("Click OK", browser_context=next_focus) == ((BROWSER,) if focus else (DESKTOP,))


def test_unrelated_input_preserves_an_unknown_click_target_until_the_registry_answers():
    from nekoclaw.device_intent import classify_device_intent

    assert classify_device_intent("Continue.", browser_context=None) == ((), None)
    assert classify_device_intent("Switch to Notepad", browser_context=None) == ((), False)


@pytest.mark.parametrize(("text", "groups", "focus"), [
    ("在浏览器中切换到深色模式并点击确认", (BROWSER,), True),
    ("Switch to Dark Mode in the browser", (BROWSER,), True),
    ("切换到深色模式（在浏览器中）", (BROWSER,), True),
    ("在浏览器中切换到 Word，然后点击保存", (DESKTOP,), False),
    ("Switch to Word from the browser, then click Save", (DESKTOP,), False),
])
def test_a_source_browser_locates_a_setting_but_an_explicit_destination_takes_over(text, groups, focus):
    from nekoclaw.device_intent import classify_device_intent

    assert classify_device_intent(text, browser_context=None) == (groups, focus)
    assert classify_device_intent(text, browser_context=False) == (groups, focus)
    assert select_device_groups("Click OK", browser_context=focus) == groups


def test_receipts_can_distinguish_unrelated_input_from_an_explicit_target():
    from nekoclaw.device_intent import classify_device_intent

    assert classify_device_intent("Continue.", browser_context=False, inherit_target=False) == ((), None)
    assert classify_device_intent("Switch to Notepad", browser_context=False, inherit_target=False) == ((), False)
    assert classify_device_intent("Click OK", browser_context=True, inherit_target=False) == ((BROWSER,), True)


@pytest.mark.parametrize(("instruction", "groups", "focus"), [
    ("转到下一页，点击确定", (BROWSER,), True),
    ("Switch to Dark Mode in the current window, then click OK", (BROWSER,), True),
    ("Switch to Theme on the desktop and click OK", (BROWSER,), True),
    ("Switch to Chinese in the mouse settings app, then click OK", (BROWSER,), True),
    ("Switch to Theme in the app where I use the mouse, then click OK", (BROWSER,), True),
    ("切换到深色模式（在当前桌面应用内），然后点击确定", (BROWSER,), True),
    ("Switch to Theme and click the desktop icon", (DESKTOP,), False),
    ("upload the file, then click submit", (BROWSER,), True),
    ("Upload the Documents folder, then click submit", (BROWSER,), True),
    ("打开上传文件夹的对话框，然后点击提交", (BROWSER,), True),
    ("Open the Downloads folder on the website, then click submit", (BROWSER,), True),
    ("切换到微信，然后点击发送", (DESKTOP,), False),
    ("切换到 Word，然后点击保存", (DESKTOP,), False),
    ("Switch to VS Code, then click Save", (DESKTOP,), False),
    ("Switch to VS Code and click Save", (DESKTOP,), False),
    ("switch  to Notepad and type hello", (DESKTOP,), False),
    ("切换到记事本并输入你好", (DESKTOP,), False),
    ("打开 Downloads 文件夹，再双击 setup", (DESKTOP,), False),
    ("打开文件夹 Downloads，然后双击 setup", (DESKTOP,), False),
    ("进入文件夹 Documents，然后双击草稿", (DESKTOP,), False),
    ("打开 Downloads 文件夹", (DESKTOP,), False),
    ("Open the Downloads folder, then double-click setup", (DESKTOP,), False),
    ("Open the folder Documents, then double-click draft", (DESKTOP,), False),
    ("Enter my client reports folder, then double-click draft", (DESKTOP,), False),
    ('请解释“打开文件夹 Documents”的意思', (), True),
    ("The instructions say to open the Documents folder.", (), True),
    ("Open the Documents folder in this code example.", (), True),
    ("回到桌面", (), False),
    ("go back to the desktop", (), False),
])
def test_browser_focus_follows_explicit_surface_changes(instruction, groups, focus):
    from nekoclaw.device_intent import classify_device_intent

    assert classify_device_intent(instruction, browser_context=True) == (groups, focus)
    assert select_device_groups("Click OK", browser_context=focus) == ((BROWSER,) if focus else (DESKTOP,))


@pytest.mark.parametrize("instruction", ["Switch to the Excel tab", "返回 Excel 网页标签", "Return to the browser tab"])
def test_named_tab_selects_the_browser_even_after_switching_to_an_app(instruction):
    from nekoclaw.device_intent import classify_device_intent

    assert classify_device_intent(instruction, browser_context=False) == ((BROWSER,), True)


@pytest.mark.parametrize(("text", "groups", "focus"), [
    ("Open the browser, switch to Notepad, then click OK", (DESKTOP, BROWSER), False),
    ("Take a desktop screenshot. Open the browser.", (DESKTOP, BROWSER), True),
    ("打开浏览器，切换到记事本，再点击确定，然后点击保存", (DESKTOP, BROWSER), False),
    ("Open the browser. Switch to Notepad.", (BROWSER,), False),
])
def test_clauses_select_all_needed_tools_but_last_target_locates_the_next_click(text, groups, focus):
    from nekoclaw.device_intent import classify_device_intent

    assert classify_device_intent(text) == (groups, focus)
    assert select_device_groups("Click OK, then click Save.", browser_context=focus) == ((BROWSER,) if focus else (DESKTOP,))
