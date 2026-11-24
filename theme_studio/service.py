"""neko_theme_studio —— N.E.K.O 主题工坊插件。

职责（见 theme-studio/THEME_CONTRACT.md）：

1. 保管一份「调色板」JSON（``static/theme.json``），它是应用侧加载器
   （``resources/bin/static/neko-theme-studio.js``）唯一读取的数据源。
2. 提供 entry 供静态面板（``static/index.html`` + ``static/app.js``）读取 /
   修改 / 重置 / 导出 / 导入这份调色板，并支持「主题仓库」（保存 / 切换 /
   删除 / 打分享包）与聊天背景图上传。
3. **服务端校验是安全边界**：面板是跨源 iframe，所有输入都不可信。任何
   不合规的变量名或值都必须被拒绝，且绝不落盘。

本体化说明（v1.0.6）：本模块已作为 **本体内置功能** 运行，不再是插件 ——
没有插件宿主、没有 48916 端口、没有任何「往本体里注入东西」的补丁。
调色板落盘在用户数据目录（``<app_docs>/theme_studio/theme.json``），
由 ``main_routers/theme_studio_router.py`` 通过 ``/api/theme_studio/*`` 提供；
``static/theme-studio/loader/`` 两件套是本体正常静态资源，由模板直接 <script> 引入。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ._sdk_compat import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    tr,
    ui,
)

from . import materials as _materials

# ---------------------------------------------------------------------------- 路径
#: 本文件所在目录（本体代码树里的 ``theme_studio/``）。
PACKAGE_DIR = Path(__file__).resolve().parent
#: 本体根目录。
APP_ROOT = PACKAGE_DIR.parent
#: 只读资源：loader 两件套等前端静态文件，由本体的 /static 路由提供。
APP_STATIC_DIR = APP_ROOT / "static" / "theme-studio"
#: 只读内置主题（随本体发布）。
PRESETS_DIR = PACKAGE_DIR / "presets"
#: 本体内主题工坊的 HTTP 前缀，必须与 ``main_routers/theme_studio_router.py`` 一致。
API_BASE = "/api/theme_studio"
#: 背景图对外 URL 前缀。
BACKGROUNDS_URL_BASE = API_BASE + "/backgrounds"

# --------------------------------------------------------------------------- 常量

SCHEMA_VERSION = 1
DEFAULT_THEME_NAME = "默认"

#: 写文件的全局锁。宿主可能并发调用 entry（面板点击 + 轮询 + 生命周期），
#: 必须串行化「读 → 合并 → 原子替换」整段，否则会丢更新。
# 用 RLock 而不是 Lock：同一线程里「先持锁做一段读-改-写，再调用内部持锁函数」
# 是很容易写出来的嵌套（save_theme 就这样自死锁过 —— 整个插件进程的 entry 队列
# 被一个永久等待的线程堵死，所有 entry 30s 超时）。可重入锁把这类写法变成安全行为。
_WRITE_LOCK = threading.RLock()

#: 变量名前缀与形态（契约 §3）。
RE_VAR_NAME = re.compile(r"^--nts-[a-z0-9-]{1,60}$")

#: 已知变量白名单 —— **形式合法不等于有意义**。
#:
#: 缺了这道门会出现「报成功的静默空操作」：``apply_theme`` 传入一个拼错的变量名
#: （例如 ``--nts-nope-var``）时，后端照收、写进 theme.json、回显为已接受，
#: 但 ``neko-theme-studio.css`` 里没有任何规则消费它，用户看不到任何变化。
#: 独立验证者实测确认过这一点。
#:
#: 清单来源 = ``tools/gen-css.js`` 实际产出的样式表里出现过的全部 ``--nts-*``
#: （可用 ``build/vars.json`` 交叉核对：CSS 消费 59 个 = 本集合）。
#: 新增变量的顺序：先在 ``test/targets.js`` 加目标 → 跑 ``tools/build-theme-css.ps1``
#: → 把生成 CSS 里新出现的名字加到这里与面板 CATALOG。
KNOWN_VARS = frozenset(
    {
        "--nts-accent",
        "--nts-accent-bg",
        "--nts-ai-bubble-bg",
        "--nts-ai-bubble-border",
        "--nts-ai-bubble-text",
        "--nts-bubble-radius",
        "--nts-bubble-veil",
        "--nts-chat-bg-image-brightness",
        "--nts-chat-top-fade",
        "--nts-button-bg",
        "--nts-button-hover-bg",
        "--nts-button-text",
        "--nts-card-radius",
        "--nts-chat-bg",
        "--nts-chat-bg-image",
        "--nts-chat-blur",
        "--nts-chat-opacity",
        "--nts-chat-panel-bg",
        "--nts-chat-panel-radius",
        "--nts-chip-bg",
        "--nts-chip-text",
        "--nts-code-bg",
        "--nts-code-text",
        "--nts-font-family",
        "--nts-font-size",
        "--nts-highlight",
        "--nts-image-bg",
        "--nts-input-bg",
        "--nts-input-border",
        "--nts-input-placeholder",
        "--nts-input-text",
        "--nts-link",
        "--nts-modal-bg",
        "--nts-modal-radius",
        "--nts-modal-text",
        "--nts-overlay-bg",
        "--nts-panel-bg",
        "--nts-panel-border-color",
        "--nts-panel-border-width",
        "--nts-panel-corner-glow",
        "--nts-panel-edge-gradient",
        "--nts-panel-glass-blur",
        "--nts-panel-glass-tint",
        "--nts-panel-glow-color",
        "--nts-panel-glow-pulse",
        "--nts-panel-glow-size",
        "--nts-panel-glow-speed",
        "--nts-panel-glow-strength",
        "--nts-panel-inner-glow",
        "--nts-panel-sheen",
        "--nts-panel-border",
        "--nts-panel-text",
        "--nts-panel-text-sub",
        "--nts-popover-bg",
        "--nts-popover-border",
        "--nts-popover-radius",
        "--nts-popover-text",
        "--nts-radius",
        "--nts-rec-bg",
        "--nts-rec-border",
        "--nts-rec-radius",
        "--nts-rec-text",
        "--nts-scrollbar-thumb",
        "--nts-scrollbar-track",
        "--nts-send-bg",
        "--nts-send-text",
        "--nts-shadow",
        "--nts-shell-radius",
        "--nts-status-bg",
        "--nts-status-text",
        "--nts-tool-bubble-bg",
        "--nts-tool-bubble-text",
        "--nts-topbar-bg",
        "--nts-topbar-text",
        "--nts-user-bubble-bg",
        "--nts-user-bubble-border",
        "--nts-user-bubble-text",
    }
)

#: v0.1.11 新增：液态玻璃 + 自定义渐变（契约 _work/FEATURE-CONTRACT-0.1.11.md §1）。
#: 追加成并集，保持 ``KNOWN_VARS`` 仍是唯一公开清单（面板/loader 的变量表都以它为准）。
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-glass-filter",
        "--nts-glass-tint",
        "--nts-glass-highlight",
        "--nts-bubble-glass-filter",
        "--nts-bubble-glass-tint",
        "--nts-chat-gradient",
        "--nts-panel-gradient",
        "--nts-user-bubble-gradient",
        "--nts-ai-bubble-gradient",
        #: v0.13.0：文字渐变。填一条 linear/radial-gradient，就把**聊天正文**的文字
        #: 填充换成这条渐变（``background-clip: text``）。它只作用于消息文本块，
        #: 不碰气泡背景 —— 否则会把气泡自己的底色一起裁成文字形状。
        "--nts-text-gradient",
    }
)

#: v0.2.0 新增：真·液态玻璃质感（契约 _work/FEATURE-CONTRACT-0.2.0.md §1）。
#: ``--nts-glass-rim`` 是边缘反射光颜色；``--nts-glass-sheen`` / ``--nts-glass-noise``
#: 是 0–1 的连续量（掠光强度 / 雾面颗粒），统一并进 ``GLOW_AMOUNT_VARS``，
#: 非法值表现是「回到原生」而不是「变成全亮」。
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-glass-rim",
        "--nts-glass-sheen",
        "--nts-glass-noise",
    }
)

#: v0.3.0 新增：Apple Liquid Glass 风格的材质参数（契约 _work/FEATURE-CONTRACT-0.3.0.md §1）。
#: 三个都是 0–1 的连续量，未配置 == 零效果（旧主题外观不变）：
#:   ``--nts-glass-lens``      边缘透镜亮环（折射带的感知近似）
#:   ``--nts-glass-thickness`` 玻璃厚度：上缘高光 / 下缘暗边 / 内侧柔光
#:   ``--nts-glass-specular``  镜面高光强度（位置由 loader 跟随指针写入 --nts-spec-x/y）
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-glass-lens",
        "--nts-glass-thickness",
        "--nts-glass-specular",
    }
)

#: v0.4.0 新增：光效引擎的三个连续量（契约 _work/FEATURE-CONTRACT-0.4.0.md §1）。
#: 同样是 0–1、未配置 == 零效果，所以旧主题/旧预设外观完全不变：
#:   ``--nts-ambient-glow``      指针光源：柔光跟随鼠标（比单点镜面更像真实光照）
#:   ``--nts-glass-dispersion``  边缘分光：暖色左上 / 冷色右下两条 1px 折射亮线
#:   ``--nts-glass-caustic``     流动焦散：玻璃后面的光斑缓慢胀缩（13s，与掠光不同步）
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-ambient-glow",
        "--nts-glass-dispersion",
        "--nts-glass-caustic",
    }
)

#: v0.12.0 新增：背景图位置 / 平铺 / 行高（批次三）。
#: 前两个是 keyword（见 ``KEYWORD_VARS``），行高是下面新引入的 ``ratio`` 种类。
KNOWN_VARS = KNOWN_VARS | frozenset(
    {
        "--nts-chat-bg-position",
        "--nts-chat-bg-repeat",
        "--nts-line-height",
    }
)

#: 滤镜链（喂给 ``backdrop-filter``）：``blur(20px) saturate(150%)``。
#: 1–4 个 token、互不重复；blur ≤ 40px、百分比 ≤ 400%（见 ``_filter_chain_ok``）。
FILTER_VARS = frozenset({"--nts-glass-filter", "--nts-bubble-glass-filter"})
#: 渐变（喂给 ``background-image``）：``linear-gradient(135deg, #a 0%, #b 100%)``。
#: 只接受 linear / radial，2–4 个停止点，停止点必须带百分比（见 ``_gradient_ok``）。
GRADIENT_VARS = frozenset(
    {
        "--nts-chat-gradient",
        "--nts-panel-gradient",
        "--nts-user-bubble-gradient",
        "--nts-ai-bubble-gradient",
        #: v0.13.0：文字渐变。消费方是 ``background-image`` + ``background-clip: text``，
        #: 与背景渐变同一套校验（linear/radial、2–4 个停止点、停止点必须带百分比）。
        "--nts-text-gradient",
    }
)
RE_FILTER_TOKEN = re.compile(
    r"^(blur|saturate|brightness|contrast|grayscale)"
    r"\(([0-9]{1,3}(?:\.[0-9]+)?)(px|%)\)$"
)
FILTER_MAX_BLUR_PX = 40.0
FILTER_MAX_PERCENT = 400.0
RE_GRADIENT_HEAD = re.compile(r"^(linear|radial)-gradient\(([\s\S]*)\)$", re.IGNORECASE)
RE_GRADIENT_ANGLE = re.compile(r"^-?[0-9]{1,3}(?:\.[0-9]+)?deg$", re.IGNORECASE)
RE_GRADIENT_STOP = re.compile(
    r"^((?:#[0-9a-f]{3,8})|(?:rgba?\([^()]*\))|(?:hsla?\([^()]*\))|(?:[a-z]{3,20}))"
    r"(?:\s+([0-9]{1,3}(?:\.[0-9]+)?)%)?$",
    re.IGNORECASE,
)
GRADIENT_MAX_STOPS = 4
GRADIENT_MAX_CHARS = 300

#: 允许的颜色值形态。刻意保守：整串锚定，杜绝把任意 CSS 注入变量。
RE_HEX = re.compile(
    r"^#([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE
)
RE_RGB = re.compile(
    r"^rgba?\(\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}"
    r"\s*(?:,\s*(?:0|1|0?\.[0-9]+)\s*)?\)$",
    re.IGNORECASE,
)
RE_HSL = re.compile(
    r"^hsla?\(\s*-?[0-9]{1,3}(?:\.[0-9]+)?\s*,"
    r"\s*[0-9]{1,3}(?:\.[0-9]+)?%\s*,"
    r"\s*[0-9]{1,3}(?:\.[0-9]+)?%\s*"
    r"(?:,\s*(?:0|1|0?\.[0-9]+)\s*)?\)$",
    re.IGNORECASE,
)
#: CSS 命名颜色（规范里的 148 个）加上 currentcolor。
#: 刻意**不**收 inherit / initial / unset / revert —— 它们把颜色继承成父级，
#: 会让「只改气泡文字色」串到别处去。
RE_KEYWORD_COLOR = re.compile(
    r"^(?:transparent|aliceblue|antiquewhite|aqua|aquamarine|azure|beige|bisque|black|blanchedalmond|blue|blueviolet|brown|burlywood|cadetblue|chartreuse|chocolate|coral|cornflowerblue|cornsilk|crimson|currentcolor|cyan|darkblue|darkcyan|darkgoldenrod|darkgray|darkgreen|darkgrey|darkkhaki|darkmagenta|darkolivegreen|darkorange|darkorchid|darkred|darksalmon|darkseagreen|darkslateblue|darkslategray|darkslategrey|darkturquoise|darkviolet|deeppink|deepskyblue|dimgray|dimgrey|dodgerblue|firebrick|floralwhite|forestgreen|fuchsia|gainsboro|ghostwhite|gold|goldenrod|gray|green|greenyellow|grey|honeydew|hotpink|indianred|indigo|ivory|khaki|lavender|lavenderblush|lawngreen|lemonchiffon|lightblue|lightcoral|lightcyan|lightgoldenrodyellow|lightgray|lightgreen|lightgrey|lightpink|lightsalmon|lightseagreen|lightskyblue|lightslategray|lightslategrey|lightsteelblue|lightyellow|lime|limegreen|linen|magenta|maroon|mediumaquamarine|mediumblue|mediumorchid|mediumpurple|mediumseagreen|mediumslateblue|mediumspringgreen|mediumturquoise|mediumvioletred|midnightblue|mintcream|mistyrose|moccasin|navajowhite|navy|oldlace|olive|olivedrab|orange|orangered|orchid|palegoldenrod|palegreen|paleturquoise|palevioletred|papayawhip|peachpuff|peru|pink|plum|powderblue|purple|rebeccapurple|red|rosybrown|royalblue|saddlebrown|salmon|sandybrown|seagreen|seashell|sienna|silver|skyblue|slateblue|slategray|slategrey|snow|springgreen|steelblue|tan|teal|thistle|tomato|turquoise|violet|wheat|white|whitesmoke|yellow|yellowgreen)$",
    re.IGNORECASE,
)

#: 非颜色项：长度 / 百分比。
RE_LENGTH = re.compile(r"^-?[0-9]{1,4}(?:\.[0-9]+)?(?:px|rem|em|%|pt|vh|vw)$", re.IGNORECASE)
RE_PERCENT = re.compile(r"^[0-9]{1,3}(?:\.[0-9]+)?%$")
RE_ZERO = re.compile(r"^0$")

#: 透明度类（0–1 或百分比）。
RE_NUMBER = re.compile(r"^(?:0|1|0?\.[0-9]+|1\.0+)$")  # 放行 1.0 / 1.00 写法

#: 时间值（v0.1.11，呼吸节奏）。接受 ``3s`` / ``1.5s`` / ``800ms``，也接受裸数字
#: （按秒解释，``3`` = ``3s``）。上限 60s：再长用户在面板上试不出来，而且
#: 一个无限循环的 60s 动画已经足够"没有动作感"，没有实际意义。
RE_TIME = re.compile(r"^(?:(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?(?:s|ms))$", re.IGNORECASE)
RE_TIME_BARE = re.compile(r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,3})?$")
TIME_MAX_SECONDS = 60.0

#: 字体名白名单：字母数字、中日文、空格、逗号、引号、连字符、下划线、点。
RE_FONT = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff ,'\"\-_.]{1,200}$")

#: 明确拒绝的危险片段。注意这里**不**禁用圆括号 —— rgb()/hsl() 合法值本身
#: 需要括号，安全性由上面每个 kind 的「整串锚定」正则保证。
RE_FORBIDDEN = re.compile(r"[;{}<>\\]|@import|url\s*\(", re.IGNORECASE)

#: 长度/百分比语义的变量。
#:
#: 分区圆角（``--nts-bubble-radius`` 等 6 个）与 ``--nts-radius`` 是**并列**的独立
#: 旋钮，不是别名：``neko-theme-studio.css`` 为每个区域单独生成声明，且每个变量
#: 都带自己的原生回退值（气泡 16px/8px、外壳 14px、卡片 0px、弹窗 0px、浮层 12px、
#: 推荐卡 18px）。因此留空即完全保持原生外观 —— 把它们纳入白名单不会破坏
#: 契约 §9.3 的「未配置零变化」。
LENGTH_VARS = frozenset(
    {
        "--nts-radius",
        "--nts-font-size",
        "--nts-bubble-radius",
        "--nts-shell-radius",
        "--nts-card-radius",
        "--nts-modal-radius",
        "--nts-popover-radius",
        "--nts-rec-radius",
        "--nts-chat-panel-radius",
    }
)
#: ``--nts-chat-blur`` 在契约 §3 里写作「px」，但 ``neko-theme-studio.css`` 实际
#: 把它的原生值写成 ``blur(18px)`` 并直接喂给 ``backdrop-filter``：
#:   ``backdrop-filter: var(--nts-chat-blur, blur(18px))``
#: 若只写 ``18px``，backdrop-filter 会因语法非法被整条丢弃 → 毛玻璃消失，
#: 且**没有任何报错**。这里两种写法都接受，并由面板统一输出 ``blur(...)``。
BLUR_VARS = frozenset({
    "--nts-chat-blur",
    "--nts-panel-glass-blur",
})
RE_BLUR = re.compile(r"^blur\(\s*-?[0-9]{1,4}(?:\.[0-9]+)?(?:px|rem|em|pt)\s*\)$", re.IGNORECASE)
#: 裸长度里对 blur 合法的写法。``blur()`` 只接受绝对长度，``%``/``vh``/``vw``
#: 会被 ``backdrop-filter`` 整条丢弃；而 ``RE_LENGTH`` 为了 shadow 偏移量必须放行它们，
#: 所以这里单列一个更严的正则，避免 ``blur(18%)`` 这种「校验通过、CSS 无效」的产物。
RE_BLUR_BARE = re.compile(r"^-?[0-9]{1,4}(?:\.[0-9]+)?(?:px|rem|em|pt)$", re.IGNORECASE)
#: 面板边缘光效 / 玻璃拟态（v0.1.11）。
#:
#: 这是一组**纯装饰**变量，全部可选；留空则整块 CSS 不加载，外观与原生完全一致
#: （契约「未配置零变化」仍然成立）。挂载点由 targets.js 的 NTS_GLOW 定义：
#:
#:   * 外发光 / 边缘光晕  → .compact-export-history-anchor::before
#:     （不能挂在 .compact-export-history-scroll 上：它自带 mask-image，
#:      实测外发光只能透出 4 个像素，用整图差分验证过）
#:   * 描边 / 四角高光 / 反射高光 / 内阴影 → .compact-export-history-anchor::after
#:   * 磨砂层（模糊背景图本身）→ .compact-export-history-scroll::before，z-index:-1
#:     挂到 ::after 会盖住消息文字，挂到 backdrop-filter 又糊不到画在自身上的
#:     背景图；负 z 层恰好夹在「容器背景」与「消息内容」之间
#:     （实测：图片清晰度 -62%、文字清晰度 -1%）
GLOW_COLOR_VARS = frozenset(
    {
        "--nts-panel-glow-color",
        "--nts-panel-border-color",
        "--nts-panel-glass-tint",
    }
)

#: 0–1 乘子。与 ``--nts-bubble-veil`` 同理，做成连续量而不是布尔开关：
#: 用户可以要「一点点光」，且值非法时表现为回到原生（而不是变成全亮/全黑）。
GLOW_AMOUNT_VARS = frozenset(
    {
        "--nts-panel-glow-strength",
        "--nts-panel-glow-pulse",
        "--nts-panel-corner-glow",
        "--nts-panel-edge-gradient",
        "--nts-panel-sheen",
        "--nts-panel-inner-glow",
        # v0.2.0：真·液态玻璃的两条 0–1 连续量。
        "--nts-glass-sheen",
        "--nts-glass-noise",
        # v0.3.0：Apple Liquid Glass 材质的三条 0–1 连续量。
        "--nts-glass-lens",
        "--nts-glass-thickness",
        "--nts-glass-specular",
        # v0.4.0：光效引擎的三条 0–1 连续量。
        "--nts-ambient-glow",
        "--nts-glass-dispersion",
        "--nts-glass-caustic",
    }
)

#: 长度类：光晕扩散半径、描边宽度。
GLOW_LENGTH_VARS = frozenset(
    {
        "--nts-panel-glow-size",
        "--nts-panel-border-width",
    }
)

#: 时间类：呼吸周期。单位接受 s / ms；不带单位一律按秒（``3`` = ``3s``）。
TIME_VARS = frozenset({"--nts-panel-glow-speed"})

#: 阴影变量：契约 §3 列为非颜色项，但真正需要的是一个 box-shadow 串，
#: 因此按 token 逐项校验（长度 / 安全颜色 / inset），而不是当成单一长度。
SHADOW_VARS = frozenset({"--nts-shadow"})
#: 字体变量。
FONT_VARS = frozenset({"--nts-font-family"})
#: 透明度变量（契约 §3 列为非颜色项，但既非长度也非字体）。
#: ``--nts-bubble-veil`` 是「聊天背景上那层虚化」的总开关：0 = 完全清透（关掉白纱
#: 与背景模糊），1 = 应用原生观感（半透明白纱 + backdrop blur）。它是一个 0–1 的
#: 乘子，由 CSS 的 ``calc()`` 同时乘进 alpha 与 blur 半径 —— 见 gen-css.js 的
#: bubble veil 段。之所以做成乘子而不是布尔开关：乘子取值连续，用户可以要
#: 「一半虚化」，且值非法时整条声明失效的表现是"回到原生"而不是"变成全透明"。
NUMBER_VARS = frozenset({
    "--nts-chat-opacity",
    "--nts-bubble-veil",
    "--nts-chat-bg-image-brightness",
    "--nts-chat-top-fade",
})

#: v0.13.0：文字渐变（见 ``GRADIENT_VARS``）。
KNOWN_VARS = KNOWN_VARS | frozenset({"--nts-text-gradient"})

#: 行高：无单位倍数。CSS 的 ``line-height`` 用无单位数字最稳（跟着字体大小缩放），
#: 但 ``RE_NUMBER`` 的 0–1 乘子语义放不下 1.4~2.0 这种常用行高，因此单列一个
#: ``ratio`` 种类：0.8–3.0、最多两位小数。三端（Python / 面板 / loader）逐条同判。
RATIO_VARS = frozenset({"--nts-line-height"})
RE_RATIO = re.compile(r"^[0-9](?:\.[0-9]{1,2})?$")
RATIO_MIN = 0.8
RATIO_MAX = 3.0

#: ``import_theme`` 接受的 JSON 文本上限（与 input_schema 的 ``maxLength`` 同源）。
#: input_schema 只是给宿主的声明，宿主不保证强制执行，因此代码里必须自己兜底。
IMPORT_MAX_CHARS = 262144

#: 面板 / 加载器读取调色板的对外路径前缀（本体同源，见 main_routers/theme_studio_router.py）。
PLUGIN_ID = "neko_theme_studio"

#: 主题仓库（``<数据目录>/themes/<id>.json``）与背景图（``<数据目录>/backgrounds/<hash>.<ext>``）
#: 都放在用户数据目录下 —— 本体通过 ``/api/theme_studio/backgrounds/*`` 把图片递给
#: 应用窗口，所以不能再像插件时代那样依赖插件静态目录。
LIBRARY_DIR_NAME = "themes"
BACKGROUND_DIR_NAME = "backgrounds"

#: 内置主题（随本体一起发布，用户不能删除）：``theme_studio/presets/<id>.json``。
#: 格式与仓库条目一致，另外带 ``"builtin": true``。里面的背景图写成**可移植**的
#: ``url("images/<hash>.<ext>")``，读出来时再改写成这台机器上的绝对 URL ——
#: 否则一份预设换台机器就指向了别人的端口。图片本体放进用户数据目录的
#: ``backgrounds/``（与用户上传的是同一个目录、同一套命名），导入时自动拷贝过去。
PRESET_DIR_NAME = "presets"

#: 「按时段自动换主题」的排程（``<数据目录>/schedule.json``）：面板写、服务读。
#: loader 自己
#: 决定此刻该用哪套内置主题，不依赖面板是否开着。
SCHEDULE_FILE_NAME = "schedule.json"

#: 四个时段。**判定规则必须与加载器的 ``scheduleZone()`` 一致**：
#: 06:00–09:59 morning、10:00–17:59 day、18:00–21:59 evening、22:00–05:59 night。
#: 边界写在这里并由 ``get_schedule`` 一并返回，面板文案也用它，避免三处各写一份。
SCHEDULE_ZONES = ("morning", "day", "evening", "night")

#: 时段边界（本地时间小时，左闭右开）。night 跨零点，单独用 (22, 6) 表达。
SCHEDULE_ZONE_HOURS: dict[str, tuple[int, int]] = {
    "morning": (6, 10),
    "day": (10, 18),
    "evening": (18, 22),
    "night": (22, 6),
}


#: 排程分两套时段表：工作日（周一–周五）与周末（周六–周日）。
#: 周末表为空 = 和平时完全一样（老版本 schedule.json 就是这种形状，自动兼容）。
SCHEDULE_DAY_KINDS = ("weekday", "weekend")

#: Python ``datetime.weekday()`` 的周六 / 周日（周一 = 0）。
SCHEDULE_WEEKEND_WEEKDAYS = (5, 6)

SCHEDULE_DAY_KIND_LABELS = {"weekday": "工作日", "weekend": "周末"}



def _default_data_dir() -> Path:
    """可写数据目录：优先用本体配置管理器给的用户数据根。

    失败（例如在没有本体运行环境的脚本里导入）时回落到用户文档目录，
    绝不让「取不到目录」把整个服务构造打挂。
    """
    try:  # 延迟导入：避免与 app 包初始化形成循环依赖
        from utils.config_manager import get_config_manager

        docs = getattr(get_config_manager(), "app_docs_dir", None)
        if docs:
            return Path(docs) / "theme_studio"
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / "Documents" / "N.E.K.O" / "theme_studio"


def _day_kind_for(weekday: Any) -> str:
    """把星期几（周一=0）映射成工作日 / 周末。

    只认 ``SCHEDULE_WEEKEND_WEEKDAYS`` 这一份数据，别处不要再写 5/6 ——
    面板文案用的是 ``get_schedule`` 返回的 ``now.day_kind``。
    """
    try:
        index = int(weekday) % 7
    except (TypeError, ValueError):
        return SCHEDULE_DAY_KINDS[0]
    return "weekend" if index in SCHEDULE_WEEKEND_WEEKDAYS else "weekday"


def _schedule_windows() -> list[dict[str, Any]]:
    """把时段边界整理成面板/加载器都能直接用的形状（只含小时，不含本地化文案）。"""
    return [
        {"zone": zone, "start": SCHEDULE_ZONE_HOURS[zone][0], "end": SCHEDULE_ZONE_HOURS[zone][1]}
        for zone in SCHEDULE_ZONES
    ]


#: 后台检查间隔（秒）。加载器每 1.5s 轮询 theme.json，所以换主题的实际生效
#: 延迟 = 本间隔 + 1.5s。时段粒度是小时，30s 已足够准，又不至于让插件线程白转。
SCHEDULE_TICK_SECONDS = 30.0


def _zone_for_hour(hour: Any) -> str:
    """把本地小时映射到时段名。

    边界**只从 SCHEDULE_ZONE_HOURS 取**，不在这里另写一份数字 —— 三处各写一份
    必然漂移（加载器那侧是 ``scheduleZone()``，面板文案用的是 ``get_schedule``
    返回的 ``windows``）。
    """
    try:
        h = int(hour) % 24
    except (TypeError, ValueError):
        h = 0
    for zone in SCHEDULE_ZONES:
        start, end = SCHEDULE_ZONE_HOURS[zone]
        if start < end:
            if start <= h < end:
                return zone
        elif h >= start or h < end:
            # night 跨零点：(22, 6) → 22,23,0..5
            return zone
    return SCHEDULE_ZONES[-1]

#: 主题「风格族」：面板主题库按族分组浏览（v0.2.0）。
#: 值必须与 ``plugin/static/app.js`` 的族表、``_work/FEATURE-CONTRACT-0.2.0.md`` §3
#: 三处一致；未知族一律规整成 ``light``（老条目没有 family 字段时也从亮/暗推断）。
PRESET_FAMILIES = (
    "glassy",    # 液态玻璃
    "gradient",  # 渐变
    "dark",      # 暗色
    "light",     # 浅色
    "texture",   # 纸感 / 质地
    "minimal",   # 极简
)

#: 分享包（zip）与图片的体积上限。压缩包是**别人给的**文件，必须按不可信输入处理。
ARCHIVE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_MAX_BYTES = 4 * 1024 * 1024
ARCHIVE_MAX_ENTRIES = 300
ARCHIVE_MAX_UNCOMPRESSED = 24 * 1024 * 1024
LIBRARY_MAX_ITEMS = 200
# 每套主题含一个 JSON，亮暗模式各可引用一张背景图；另含清单和说明文件。
LIBRARY_ARCHIVE_MAX_ENTRIES = LIBRARY_MAX_ITEMS * 3 + 2
#: box-shadow 允许用逗号叠多层（霓虹/发光主题常见写法：两层不同颜色的光晕）。
#: 早先三处校验器都只认单层，把含逗号的值整串拒掉 —— 用户从别处拿到的主题
#: 一应用就少一条 `--nts-shadow`，还没人告诉他为什么。这里统一放开到 4 层。
SHADOW_MAX_LAYERS = 4

#: 分享包里的说明文件（纯文本，方便别人拿到 zip 后知道怎么用）。
ARCHIVE_README = (
    "N.E.K.O 主题工坊 · 主题分享包\n"
    "\n"
    "theme.json 是调色板；images/ 里是与它配套的背景图。\n"
    "用法：打开 插件 → 主题工坊 → 主题仓库 → 导入压缩包，选择本文件即可。\n"
    "导入只会把主题存进仓库，不会立刻改动你当前的配色；在仓库里点「应用」才切换。\n"
)

#: 主题库备份包（zip）格式标记。区别于单主题分享包：这里一个包里有多个
#: ``themes/*.json`` + 共用 ``images/`` + ``manifest.json``。
LIBRARY_ARCHIVE_FORMAT = "neko-theme-studio-library/1"

#: 库备份包里的说明文件。
LIBRARY_ARCHIVE_README = (
    "N.E.K.O 主题工坊 · 主题库备份包\n"
    "\n"
    "themes/ 里是你保存过的每一套主题（每套一个 json），images/ 是它们共用的背景图。\n"
    "用法：打开 插件 → 主题工坊 → 主题仓库 → 恢复备份，选择本文件即可。\n"
    "恢复只会往仓库里「新增」主题，不会覆盖或删除现有的任何一套；同名不同 id 会各存一份。\n"
    "恢复不会立刻改动你当前的配色；在仓库里点「应用」才切换。\n"
)

#: 主题仓库条目 id：服务端生成，绝不采用上传文件名（否则就是路径穿越入口）。
RE_THEME_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: 背景图文件名：服务端按内容哈希生成。
RE_BG_NAME = re.compile(r"^[a-z0-9]{8,32}\.(?:png|jpg|jpeg|webp|gif)$")
# 从 theme.json / 仓库条目的原始文本里捞背景图引用（孤儿回收用）。
#: 两处引用都要认：theme.json / 仓库条目写的是 ``backgrounds/<name>``，
#: 而内置主题里是可移植写法 ``images/<name>``。早先只匹配前者，结果插件一启动
#: 就把「只被内置主题引用」的图片当孤儿删掉 —— 预设点开就没有背景图了。
RE_BG_REF_TEXT = re.compile(
    r"(?:backgrounds|images)/([a-z0-9]{8,32}\.(?:png|jpg|jpeg|webp|gif))"
)
#: 背景图变量（值只有两种合法形态：``none`` 或指向本插件 backgrounds/ 的 url()）。
IMAGE_VARS = frozenset({"--nts-chat-bg-image"})
#: 值里抓文件名（兼容 ``url("images/x.png")`` 与绝对地址两种写法）。
RE_BG_VALUE_CAPTURE = re.compile(r"/backgrounds/([A-Za-z0-9._-]{1,80})")
RE_URL_ANY = re.compile(r'url\(\s*"?([^")\s]+)"?\s*\)')
#: 绝对地址或裸路径都接受，但必须是本插件的 backgrounds/ 目录。
RE_BG_URL = re.compile(
    r'^url\(\s*"?(?:https?://(?:127\.0\.0\.1|localhost):[0-9]{2,5})?'
    r"/api/theme_studio/backgrounds/[A-Za-z0-9._-]{1,80}"
    r'"?\s*\)$'
)
#: 图片判定（魔数优先，扩展名只用于分享包内文件筛选）。
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _image_extension(data: bytes) -> str:
    """按魔数判定图片格式；不认识返回空串（绝不按文件扩展名信任内容）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _strip_controls(text: str) -> str:
    """去掉 C0/C1 控制字符与格式字符（RTL 覆写、零宽空格、BOM 等）。

    主题名/简介/标签会进 theme.json、主题库、分享包与日志。控制字符在 JSON 里
    是合法的（写成 ``\u0000``），但面板显示会断行、日志会串行、分享包接收方
    看到的是乱码 —— 在入口处清掉比在渲染层补救可靠。
    """
    if not text:
        return text
    return "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")


def _clip_name(value: Any, fallback: str) -> str:
    """主题名规整：去控制字符、去首尾空白、截 60 字、空则用 fallback。"""
    if isinstance(value, str):
        cleaned = _strip_controls(value).strip()
        if cleaned:
            return cleaned[:60]
    return fallback


#: 面板主题卡片缩略图取哪些变量（v0.1.12）。
#:
#: 面板不能为了画缩略图把整套 82 个变量都拿过去（主题仓库一屏有十几套，
#: 每套 16 KB 的 JSON 不划算），这里只摘 7 个最有辨识度的颜色/渐变。
#: 值全部来自 ``_validate_palette`` 的返回值，仍截断到 300 字符再交给面板。
PREVIEW_VARS = (
    ("bg", "--nts-chat-bg"),
    ("gradient", "--nts-chat-gradient"),
    ("accent", "--nts-send-bg"),
    ("user", "--nts-user-bubble-gradient"),
    ("userSolid", "--nts-user-bubble-bg"),
    ("ai", "--nts-ai-bubble-bg"),
    ("panel", "--nts-panel-glass-tint"),
)


def _material_badge(raw: Any) -> dict[str, Any] | None:
    """主题自带的材质摘要（面板卡片徽标用）。

    ``None`` 表示这套主题**没有**材质设置 —— 应用它时材质保持不变
    （向后兼容老分享包与老仓库条目）；返回 dict 时应用会整份替换材质，
    除非用户把当前材质设成「锁定」。
    """
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        clean, _ = _materials.normalize_materials(raw)
    except Exception:  # noqa: BLE001 - 摘要失败不该让整页主题列表崩
        return None
    preset = str(clean.get("preset") or "custom")
    quality = str(clean.get("quality") or "balanced")
    return {
        "enabled": bool(clean.get("enabled")),
        "preset": preset,
        "label": _materials.PRESET_LABELS.get(preset, preset),
        "quality": quality,
        "quality_label": _materials.QUALITY_LABELS.get(quality, quality),
    }

def _preset_preview(light: dict, dark: dict) -> dict:
    """给面板的缩略色板：亮/暗各取 7 个已校验值。"""

    def snap(source: dict) -> dict:
        out: dict[str, str] = {}
        for name, var in PREVIEW_VARS:
            value = source.get(var)
            if isinstance(value, str) and value:
                out[name] = value[:300]
        return out

    return {"light": snap(light), "dark": snap(dark)}


def _color_luma(value: Any) -> float | None:
    """尽量把 ``#rgb/#rrggbb/rgba()/rgb()`` 折成 0–1 亮度，认不出就返回 None。

    只用于给「没有 family 字段的老预设」猜一个族，不需要精确 —— 认不出就当浅色。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if text.startswith("#"):
            body = text[1:]
            if len(body) in (3, 4):
                body = "".join(ch * 2 for ch in body[:3])
            elif len(body) in (6, 8):
                body = body[:6]
            else:
                return None
            r, g, b = (int(body[i : i + 2], 16) for i in (0, 2, 4))
        else:
            match = re.match(r"rgba?\(([^()]*)\)", text)
            if not match:
                return None
            parts = [part.strip() for part in match.group(1).split(",")]
            if len(parts) < 3:
                return None
            r, g, b = (int(float(parts[i])) for i in range(3))
    except (TypeError, ValueError):
        return None
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _color_alpha(value: Any) -> float | None:
    """取颜色的 alpha（0–1）；不透明色返回 1.0，认不出返回 None。"""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text == "transparent":
        return 0.0
    match = re.match(r"rgba?\(([^()]*)\)", text)
    if match:
        parts = [part.strip() for part in match.group(1).split(",")]
        if len(parts) >= 4:
            try:
                return max(0.0, min(1.0, float(parts[3])))
            except ValueError:
                return None
        return 1.0
    match = re.fullmatch(r"#([0-9a-f]{4}|[0-9a-f]{8})", text)
    if match:
        digits = match.group(1)
        return int(digits[-2:], 16) / 255.0
    if re.fullmatch(r"#[0-9a-f]{3}|#[0-9a-f]{6}", text):
        return 1.0
    return 1.0


def _preset_family(raw: dict, vars_map: dict, dark_map: dict) -> str:
    """风格族：优先取 JSON 里的 ``family``，否则按「真半透 → 暗色 → 浅色」推断。

    推断只服务「没有 family 字段」的老条目/用户自存主题：只有玻璃底色真的半透
    （alpha ≤ 0.35）才算 glassy，否则大多数主题都带着 ``--nts-glass-filter``，
    一律判成玻璃族就失去分类意义了。
    """
    declared = raw.get("family")
    if isinstance(declared, str) and declared.strip().lower() in PRESET_FAMILIES:
        return declared.strip().lower()
    for source in (vars_map or {}, dark_map or {}):
        tint = source.get("--nts-glass-tint")
        alpha = _color_alpha(tint)
        chain = str(source.get("--nts-glass-filter") or "")
        blur = re.search(r"blur\(\s*([0-9]+(?:\.[0-9]+)?)px", chain)
        # 真半透（0.02 < alpha ≤ 0.30）+ 足够强的 backdrop blur 才算玻璃族；
        # 老主题里大量 ``transparent``/``blur(18px)`` 的写法是为了「不影响原生」，
        # 把它们判成玻璃族会让分类失去意义。
        if (
            alpha is not None
            and 0.02 < alpha <= 0.30
            and blur is not None
            and float(blur.group(1)) >= 20.0
        ):
            return "glassy"
    # 亮色模式就是用户第一眼看到的默认形态：亮色底偏暗才归 dark，其余归 light。
    light_luma = _color_luma((vars_map or {}).get("--nts-chat-bg"))
    if light_luma is not None and light_luma < 0.42:
        return "dark"
    return "light"


def _fmt_saved_at(value: Any) -> str:
    """``2026-11-15T14:02:11+08:00`` → ``2026-11-15 14:02:11``（面板展示用）。"""
    if isinstance(value, str) and value.strip():
        return value.strip().replace("T", " ")[:19]
    return "—"


def _safe_archive_name(name: str) -> str:
    """把主题名变成安全的下载文件名（去掉路径分隔符与 Windows 非法字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "-", name or "").strip("-")
    return cleaned[:40] or "theme"


def _parse_theme_json(payload: bytes) -> dict:
    """解析「主题 JSON 文件」（.json 分享 / 导出的 theme.json），返回 theme dict。

    接受两种形态：

    - 面板「导出主题」写出的完整 ``theme.json``（``schema``/``name``/``vars``/
      ``dark_vars``/``note``/``tags``）；
    - 别人手工精简的 ``{"vars": {...}, "dark_vars": {...}}``。

    只做「结构 + 类型」这一层校验；每个变量的合法性仍由 ``_validate_palette``
    判定，坏值逐条丢弃而不是整份拒绝（和 zip 分享包一致）。
    """
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SdkError("主题文件不是 UTF-8 文本，可能选错了文件。") from exc
    text = text.strip()
    if not text:
        raise SdkError("主题文件是空的。")
    if len(text) > IMPORT_MAX_CHARS:
        raise SdkError(f"主题文件过长（{len(text)} 字符，上限 {IMPORT_MAX_CHARS}）。")
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise SdkError(f"主题文件不是合法 JSON：{exc}") from exc
    if not isinstance(parsed, dict):
        raise SdkError("主题文件必须是一个 JSON 对象。")
    if not isinstance(parsed.get("vars"), dict) and not isinstance(
        parsed.get("dark_vars"), dict
    ):
        raise SdkError("主题文件缺少 vars / dark_vars 字段。")
    return parsed


def _theme_note(value: Any) -> str:
    """导入/保存时保留的一行简介（与内置主题同样截 120 字）。"""
    return _strip_controls(value).strip()[:120] if isinstance(value, str) else ""


def _theme_tags(value: Any) -> list:
    """导入/保存时保留的标签（最多 4 个、每个 12 字，与内置主题一致）。"""
    if not isinstance(value, list):
        return []
    out = []
    for tag in value[:4]:
        if isinstance(tag, str):
            cleaned = _strip_controls(tag).strip()
            if cleaned:
                out.append(cleaned[:12])
    return out


# --------------------------------------------------------------------------- 默认调色板

def _default_vars() -> dict[str, str]:
    """亮色（``:root``）默认值 —— **空**，这是刻意的。

    契约 §9.3 要求「未配置时视觉零变化」。而 ``neko-theme-studio.css`` 里
    同一条变量在不同规则上带着**不同**的原生回退值，例如：

    - ``--nts-radius``: ``border-radius: var(--nts-radius, 0|8px|10px|12px)``
      （topbar 是 0，卡片/面板是 12px，按钮/发送键是 10px）
    - ``--nts-input-bg``: ``#text-input-area`` 回退 ``rgba(40,40,40,0.65)``，
      而 ``.composer-input`` 回退 ``transparent``

    一旦在 ``:root`` 里钉住任一值，所有这些规则都会被同一个值覆盖 ——
    也就是「没配主题却看得到变化」，直接违反 §9.3。应用侧加载器同样把空
    调色板理解为「恢复原样」（``neko-theme-studio.js``：空 vars ⇒ removeStyle）。

    因此出厂调色板是空的；完整变量目录 + 原生参考值放在面板 ``static/app.js``
    的 CATALOG 里（面板默认显示原生值，仅在用户真正改动某一行时才写入该行）。
    """
    return {}


def _default_dark_vars() -> dict[str, str]:
    """暗色（``:root[data-theme="dark"]``）默认值 —— 同样为空，理由见上。"""
    return {}


def _now_iso() -> str:
    """本地时区 ISO-8601，例如 ``2026-11-07T16:00:00+08:00``。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_palette() -> dict[str, Any]:
    """内置默认调色板（``reset_theme`` 与首次落盘都用它）。"""
    return {
        "schema": SCHEMA_VERSION,
        "name": DEFAULT_THEME_NAME,
        "enabled": True,
        "updated_at": _now_iso(),
        "vars": _default_vars(),
        "dark_vars": _default_dark_vars(),
        # 材质默认**未启用**：面板一打开就有完整参数可调，但应用窗口零变化。
        "materials": _materials.default_materials(),
    }


# --------------------------------------------------------------------------- 校验

#: 只接受固定关键字的变量（值既不是颜色也不是长度，而是 CSS 枚举值）。
#: ``--nts-chat-bg-fit`` 决定背景图是 ``cover``（铺满、可能裁切）还是 ``contain``
#: （整图可见、两侧留底色）。白名单整串匹配，绝不接受 ``var()`` / 多余内容。
KEYWORD_VARS: dict[str, tuple[str, ...]] = {
    "--nts-chat-bg-fit": ("cover", "contain"),
    #: v0.8.0：圆角几何形状。``round`` = 普通圆角（原生行为），``continuous`` =
    #: 连续曲率（超椭圆 / iOS 那种「转角没有突然变直」的过渡）。后者需要
    #: Chrome 139+ 的原生 ``corner-shape``，旧内核上加载器打的属性还在、但 CSS
    #: 整块在 ``@supports`` 里不生效 —— 外观退回普通圆角，不会坏。
    "--nts-corner-style": ("round", "continuous"),
    #: v0.12.0：背景图位置 / 平铺。各管一条声明（``background-position`` /
    #: ``background-repeat``），不碰 ``background-image`` 与 ``background-size``，
    #: 所以和「背景图 / 亮度 / 适应方式」可以任意组合。未设置时那段 CSS 不匹配，
    #: 外观与应用原生完全一致（居中、不平铺）。
    "--nts-chat-bg-position": ("center", "top", "bottom", "left", "right"),
    "--nts-chat-bg-repeat": ("no-repeat", "repeat", "repeat-x", "repeat-y"),
}

#: v0.4.27 新增：背景图适应方式。并进 ``KNOWN_VARS``，三端（后端 / 面板 / loader）同表。
KNOWN_VARS = KNOWN_VARS | frozenset({"--nts-chat-bg-fit", "--nts-corner-style"})


def _value_kind(name: str) -> str:
    if name in KEYWORD_VARS:
        return "keyword"
    # 光效 / 玻璃拟态一组：必须在默认 "color" 之前判掉，
    # 否则 0-1 乘子和长度值会被当颜色、整条被拒。
    if name in FILTER_VARS:
        return "filter"
    if name in GRADIENT_VARS:
        return "gradient"
    if name in GLOW_AMOUNT_VARS:
        return "number"
    if name in GLOW_LENGTH_VARS:
        return "length"
    if name in TIME_VARS:
        return "time"
    if name in IMAGE_VARS:
        return "image"
    if name in RATIO_VARS:
        return "ratio"
    if name in LENGTH_VARS:
        return "length"
    if name in BLUR_VARS:
        return "blur"
    if name in SHADOW_VARS:
        return "shadow"
    if name in FONT_VARS:
        return "font"
    if name in NUMBER_VARS:
        return "number"
    return "color"


def _shadow_tokens(value: str) -> list[str]:
    """按「括号外空白」切分阴影串；括号不配对则返回空列表。"""
    tokens: list[str] = []
    depth = 0
    buf = ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return []
        if ch.isspace() and depth == 0:
            if buf:
                tokens.append(buf)
                buf = ""
            continue
        buf += ch
    if buf:
        tokens.append(buf)
    if depth != 0:
        return []
    return tokens


def _split_shadow_layers(value: str) -> list:
    """按「顶层逗号」把多层 box-shadow 切成单层；括号不配对返回空列表。

    ``0 0 20px rgba(0,0,0,.4), 0 0 15px rgba(179,136,255,.5)``
    → 两层。不能直接 ``value.split(",")``：颜色是 ``rgba(0, 0, 0, .4)``，
    里面全是逗号。
    """
    layers: list = []
    depth = 0
    buf = ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return []
        if ch == "," and depth == 0:
            if buf.strip():
                layers.append(buf.strip())
            buf = ""
            continue
        buf += ch
    if depth != 0:
        return []
    if buf.strip():
        layers.append(buf.strip())
    return layers


def _shadow_layer_ok(tokens: list) -> bool:
    """单层 box-shadow 的 token 校验（原本内联在 ``_is_safe_value`` 里）。"""
    if not tokens or len(tokens) > 6:
        return False
    lengths = 0
    colors = 0
    insets = 0
    for token in tokens:
        if token.lower() == "inset":
            insets += 1
            if insets > 1:
                return False
            continue
        if (
            RE_KEYWORD_COLOR.match(token)
            or RE_HEX.match(token)
            or RE_RGB.match(token)
            or RE_HSL.match(token)
        ):
            colors += 1
            if colors > 1:
                return False
            continue
        if RE_PERCENT.match(token) or "%" in token:
            return False
        if RE_LENGTH.match(token) or RE_ZERO.match(token):
            lengths += 1
            if lengths > 4:
                return False
            # 负值只允许前两个长度（x/y 偏移）；模糊/扩散半径不能为负
            if token.startswith("-") and lengths > 2:
                return False
            continue
        return False
    return lengths >= 2


def _split_top_level_commas(text: str) -> list[str] | None:
    """按「顶层逗号」切分（括号里的逗号不切）；括号不配对返回 ``None``。

    渐变/阴影的颜色里都带逗号（``rgba(0, 0, 0, .4)``），直接 ``split(",")``
    会把一个颜色切成三段，然后整条值被误拒。与 ``_split_shadow_layers`` 同一套
    括号计数逻辑，抽出来给两处共用。
    """
    parts: list[str] = []
    depth = 0
    buf = ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
        if ch == "," and depth == 0:
            parts.append(buf.strip())
            buf = ""
            continue
        buf += ch
    if depth != 0:
        return None
    parts.append(buf.strip())
    return parts


def _color_ok(value: str) -> bool:
    """单色（渐变停止点用）：hex / rgb() / hsl() / 命名色 / transparent 之一。"""
    return bool(
        RE_KEYWORD_COLOR.match(value)
        or RE_HEX.match(value)
        or RE_RGB.match(value)
        or RE_HSL.match(value)
    )


def _filter_chain_ok(value: str) -> bool:
    """``blur(20px) saturate(150%)`` —— backdrop-filter 的滤镜链校验。

    为什么按 token 校验而不是整串一个大正则：需要额外表达上限（blur ≤ 40px、
    百分比 ≤ 400%）和「函数不能重复」，一个大正则表达不了。三端（Python /
    面板 app.js / loader）必须逐字同判定，见 FEATURE-CONTRACT-0.1.11.md §2.1。
    """
    text = value.strip()
    if not text or len(text) > 120:
        return False
    tokens = text.split(" ")
    if not 1 <= len(tokens) <= 4:
        return False
    seen: set[str] = set()
    for token in tokens:
        match = RE_FILTER_TOKEN.match(token)
        if not match:
            return False
        func = match.group(1).lower()
        unit = match.group(3).lower()
        number = float(match.group(2))
        if func in seen:
            return False
        seen.add(func)
        if match.group(0) != token:  # 整串锚定，杜绝大小写/空白技巧
            return False
        if func == "blur":
            if unit != "px" or number > FILTER_MAX_BLUR_PX:
                return False
        elif unit != "%" or number > FILTER_MAX_PERCENT:
            return False
    return True


def _gradient_ok(value: str) -> bool:
    """``linear-gradient(135deg, #fff 0%, #000 100%)`` —— 渐变值校验。

    只收 linear / radial（面板只生成这两种；conic 需要额外的 ``from`` 语法，
    没有真实需求就先不开这个口子）。linear 的第一个参数必须是角度，停止点必须
    带百分比 —— 这样「面板生成的值」是唯一形态，校验也就能做到整串锚定。
    """
    text = value.strip()
    if not text or len(text) > GRADIENT_MAX_CHARS:
        return False
    head = RE_GRADIENT_HEAD.match(text)
    if not head:
        return False
    kind = head.group(1).lower()
    parts = _split_top_level_commas(head.group(2))
    if parts is None:
        return False
    if kind == "linear":
        if len(parts) < 3 or not RE_GRADIENT_ANGLE.match(parts[0]):
            return False
        stops = parts[1:]
    else:
        stops = parts
    if not 2 <= len(stops) <= GRADIENT_MAX_STOPS:
        return False
    for stop in stops:
        match = RE_GRADIENT_STOP.match(stop)
        if not match:
            return False
        if not _color_ok(match.group(1)):
            return False
        # 停止点必须带百分比：这样「面板生成什么、校验就收什么」，
        # 不会出现 ``#fff`` 这种带浏览器自动分布的值让两端判断分叉。
        if match.group(2) is None or float(match.group(2)) > 100.0:
            return False
    return True


def _is_safe_value(name: str, value: Any) -> bool:
    """值是否可安全写进给定变量（整串匹配，不允许任何附加内容）。"""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > 300:
        return False

    kind = _value_kind(name)
    if kind == "filter":
        # 先于 RE_FORBIDDEN：滤镜链里没有 url(= / 分号，但保留这条路径更清晰。
        return _filter_chain_ok(v)
    if kind == "gradient":
        return _gradient_ok(v)
    if kind == "image":
        # 背景图的值本身就是 url(...)，必须跳过 RE_FORBIDDEN 的 url( 拦截，
        # 改由 RE_BG_URL 整串锚定（只允许本插件 backgrounds/ 目录里的文件）。
        return v.lower() == "none" or bool(RE_BG_URL.match(v))
    if kind == "keyword":
        # 只收白名单里的关键字（cover / contain / round / continuous），整串匹配；大小写不敏感。
        return v.lower() in KEYWORD_VARS[name]
    if RE_FORBIDDEN.search(v):
        return False
    if kind == "color":
        return bool(
            RE_KEYWORD_COLOR.match(v)
            or RE_HEX.match(v)
            or RE_RGB.match(v)
            or RE_HSL.match(v)
        )
    if kind == "length":
        # 负长度在 CSS 里一律非法（``border-radius`` / ``font-size`` 收到负值会把整条
        # 声明丢掉 → 面板报成功、外观不变）。RE_LENGTH 为了 shadow 的偏移量必须允许
        # 负号，而 shadow 走的是自己的 token 分支，所以在这里单独拦住即可。
        if "-" in v:
            return False
        return bool(RE_LENGTH.match(v) or RE_PERCENT.match(v) or RE_ZERO.match(v))
    if kind == "blur":
        # 同时接受 `blur(18px)`（CSS 能用的形态）与裸长度 `18px`（契约 §3 的写法），
        # 裸长度稍后会被规范化为 blur(...)。
        # 负半径会让 ``backdrop-filter`` 整条失效（静默无变化），而模糊值永远用不到负号。
        if "-" in v:
            return False
        # 只收绝对长度：blur(18%) / blur(18vw) 不是合法 CSS，放行就会落盘一个
        # loader 必丢的值（面板报成功、外观不变）。
        return bool(RE_BLUR.match(v) or RE_BLUR_BARE.match(v) or RE_ZERO.match(v))
    if kind == "time":
        # 呼吸周期。裸数字按秒解释；带单位时换算成秒再卡上限。
        # 只收 s / ms 两种单位：呼吸节奏用不到别的，放宽只会放出无效 CSS。
        if RE_TIME.match(v):
            unit = v[-2:].lower()
            try:
                seconds = float(v[:-2]) / 1000.0 if unit == "ms" else float(v[:-1])
            except ValueError:  # pragma: no cover — RE_TIME 已保证是数字
                return False
            return 0.0 <= seconds <= TIME_MAX_SECONDS
        if RE_TIME_BARE.match(v):
            return float(v) <= TIME_MAX_SECONDS
        return False
    if kind == "number":
        # 契约语义是 0–1 的小数；百分比是面板的友好写法，但只有 0%–100% 有意义
        # （999% 归一后 >1，loader 会丢弃 → 又变成「能存不能用」）。
        if RE_NUMBER.match(v):
            return True
        if not RE_PERCENT.match(v):
            return False
        try:
            return float(v[:-1]) <= 100.0
        except ValueError:  # pragma: no cover — RE_PERCENT 已保证是数字
            return False
    if kind == "ratio":
        # 无单位倍数（行高）。范围 0.8–3.0：小于 0.8 会把多行文字压成一团，
        # 大于 3.0 在聊天气泡里已经没有可读性。
        if not RE_RATIO.match(v):
            return False
        return RATIO_MIN <= float(v) <= RATIO_MAX
    if kind == "font":
        return bool(RE_FONT.match(v))
    if kind == "shadow":
        # box-shadow 的 CSS 语法是 inset? && <length>{2,4} && <color>?。
        # 只做 token 白名单会放行 0 0 10% #000（百分比不是 box-shadow 的合法长度）、
        # 1px（长度不足）、1px 2px 3px 4px 5px（长度过多）——浏览器会把整条声明丢掉，
        # 面板却显示已应用（与「改了没反应」同症状）。
        # 面板 isSafeShadow / loader isSafeValue 必须逐条同判定（THEME_CONTRACT 9.10b）。
        layers = _split_shadow_layers(v)
        if not layers or len(layers) > SHADOW_MAX_LAYERS:
            return False
        return all(
            _shadow_layer_ok(_shadow_tokens(layer)) for layer in layers
        )
    return False


def _canonicalize_value(name: str, value: str) -> str:
    """把通过校验的值规范化成 CSS 真正需要的形态。

    两处：

    - ``--nts-chat-blur``：消费方是
      ``backdrop-filter: var(--nts-chat-blur, blur(18px))``，因此裸长度
      ``18px`` 必须补成 ``blur(18px)``，否则整条声明被浏览器丢弃。
    - ``--nts-chat-opacity``：契约语义是 0–1 的小数，而面板允许用户写
      ``50%``。这里统一归一成小数（四舍五入到 4 位、去尾零），使 theme.json
      里永远是 loader 能直接消费的形态 —— 否则 loader 会把百分比静默丢弃，
      用户看到「面板说成功、透明度没变」。
    - ``--nts-panel-glow-speed``（v0.1.11）：面板允许只敲 ``3``，这里补成
      ``3s``。CSS 的 ``animation-duration: 3`` 是非法值（时长必须带单位），
      不补的话整条 animation 声明会被丢弃 → 用户看到「节奏填了但不动」。
    """
    if name in KEYWORD_VARS:
        # 关键字大小写不敏感（CSS 本身也是），统一落盘成小写，面板下拉才能对上。
        return value.lower()
    if name in BLUR_VARS and RE_BLUR_BARE.match(value):
        return f"blur({value})"
    if name in TIME_VARS and RE_TIME_BARE.match(value):
        return f"{value}s"
    if name in NUMBER_VARS and value.endswith("%"):
        try:
            fraction = round(float(value[:-1]) / 100.0, 4)
        except ValueError:  # pragma: no cover — 校验阶段已保证是数字
            return value
        text = f"{fraction:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return value


def _validate_palette(
    vars_in: Any, dark_in: Any
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """校验并过滤两组变量。

    :returns: ``(clean_vars, clean_dark, errors)`` —— clean 字典是**唯一**
        允许落盘的内容；errors 是给用户看的拒绝原因列表。
    """
    clean_vars: dict[str, str] = {}
    clean_dark: dict[str, str] = {}
    errors: list[str] = []

    for label, src, out in (("vars", vars_in, clean_vars), ("dark_vars", dark_in, clean_dark)):
        if src is None:
            continue
        if not isinstance(src, dict):
            errors.append(f"{label}: 必须是对象")
            continue
        for raw_name, raw_value in src.items():
            name = str(raw_name)
            if not RE_VAR_NAME.match(name):
                errors.append(f"{label}: 变量名非法已拒绝 {name!r}")
                continue
            if name not in KNOWN_VARS:
                # 形式合法但没有规则消费 → 必须报错。否则「报成功的静默空操作」
                # 会让用户以为改成功了（独立验证者实测过 --nts-nope-var）。
                errors.append(
                    f"{label}: 未知变量名已拒绝 {name!r}（拼写错误？见 README 变量清单）"
                )
                continue
            if raw_value is None:
                # 显式置空 = 删除该键（回退到 N.E.K.O 原生值）。
                continue
            if not isinstance(raw_value, str):
                errors.append(f"{label}: {name} 的值必须是字符串")
                continue
            value = raw_value.strip()
            if not value:
                # 空串 = 删除该键（面板的「清空」语义）。
                continue
            if not _is_safe_value(name, value):
                errors.append(f"{label}: {name} 的值非法已拒绝 {value!r}")
                continue
            out[name] = _canonicalize_value(name, value)

    return clean_vars, clean_dark, errors


def _permission_hint(exc: BaseException) -> str:
    """把 PermissionError 翻译成可执行的修复建议。

    根因（2026-11-18 实测）：N.E.K.O 的插件子进程以 **Low 完整性级别（IL）** 运行
    （``projectneko_server.exe --multiprocessing-fork``），而 install.ps1 提权运行
    会把插件目录/文件写成 Medium/High。Low 进程往里 CREATE 新文件会被拒绝，
    表现为面板「应用」报 ``[Errno 13] Permission denied: ...\\static\\.theme.json...
    .tmp``，以及插件 startup 因写不了模板备份而静默跳过补丁。
    逐字报 Errno 13 会让用户以为是磁盘满了，所以这里直接给出修复命令。
    """
    plugin_dir = Path(__file__).resolve().parent
    return (
        f"写入主题失败：{exc}。"
        "该目录对插件进程不可写：N.E.K.O 的插件子进程以 Low 完整性级别运行，"
        "而目录可能被提权进程改成了 Medium/High。"
        "修复：在插件面板「应用集成」里点一次「修复应用侧集成」；"
        "若仍失败，再以管理员身份重新运行 install.ps1（它会自动把插件目录降为 Low）；"
        f'或手动执行：icacls "{plugin_dir}" /setintegritylevel (OI)(CI)L /T /C'
    )


_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})


def _coerce_bool(value: Any) -> bool | None:
    """把「布尔语义」的值规整成 bool；无法判定时返回 ``None``。

    存在的理由：theme.json 可能被手工编辑成 ``"enabled": "false"``，而
    ``bool("false")`` 是 True —— 那会把用户关掉的主题静默重新启用。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, float):
        return None if value != value else bool(value)  # NaN → 无法判定
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
    return None


class _ThemeUnreadable(RuntimeError):
    """theme.json 存在但读不出来（权限 / 被占用 / JSON 损坏）。

    写路径遇到它必须中止：若像读路径那样退回 ``default_palette()``，一次瞬时
    读取失败（Windows 上杀毒软件或索引器锁文件即可触发）就会把用户已保存的
    全部变量抹掉、只剩本次新值，而且返回 Ok。
    """


def _safe_int(value: Any, default: int = 0) -> int:
    """把加载器上报的数字字段安全转成 int。

    为什么兜底：``loader_report`` 的唯一输入来自页面里的加载器，版本错配时它可能
    送出字符串、布尔或对象（``"12"`` / ``true`` / ``{"a":1}``）。以前直接 ``int(...)``，
    一个怪类型就让整个入口抛异常，宿主看到 500，诊断记录再也落不了盘 —— 而那恰恰
    是最需要诊断的时候。非数字、负数、超量级一律退回默认值。
    """
    if isinstance(value, bool):
        return int(value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < 0:
        return default
    return min(number, 1_000_000_000)


def _clean_attrs(attrs: Any) -> dict[str, str]:
    """根节点属性：最多 40 条、键 40 字、值 200 字。

    诊断文件每轮上报整体覆写一次，字段不封顶时一个渲染 bug（比如把 className
    拼进属性值、把整个 style 串塞进 data-*）就能把插件目录写成几十 MB。
    """
    if not isinstance(attrs, dict):
        return {}
    clean: dict[str, str] = {}
    for key, val in list(attrs.items())[:40]:
        if isinstance(key, str):
            clean[key[:40]] = str(val)[:200]
    return clean


def _clean_list(value: Any, limit: int, count: int) -> list[str]:
    """上报里的链路/错误数组：非数组一律当空，单条与条数都截断。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item)[:limit] for item in list(value)[:count]]


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """原子写 JSON：同目录临时文件 + fsync + ``os.replace``（Windows 带重试）。

    同目录是硬要求，否则 ``os.replace`` 可能跨卷失败（非原子）。

    为什么要重试：Windows 上只要有别的进程（宿主读文件、杀软扫描、编辑器预览）
    恰好持有目标文件句柄，``os.replace`` 就会抛 ``PermissionError: [WinError 5]``，
    表现为「保存随机失败一次、再点就好」。句柄通常 50–200ms 内释放，所以短重试
    能消掉绝大部分抖动；重试用尽才抛。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                os.replace(tmp_path, path)
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                if getattr(exc, "winerror", None) not in (5, 32) or attempt == 5:
                    raise
                time.sleep(0.04 * (attempt + 1))
        if last_error is not None:  # pragma: no cover - 上面要么 break 要么 raise
            raise last_error
    finally:
        # 替换成功后 tmp 已不存在；失败路径必须自己清理。
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- 插件

class ThemeStudioService(NekoPluginBase):
    """主题工坊：调色板的唯一权威保管者。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        ctx: Any = None,
    ) -> None:
        super().__init__(ctx)
        try:
            self.logger = self.enable_file_logging(log_level="INFO")
        except Exception:  # noqa: BLE001 — 文件日志不可用不应阻止功能
            self.logger = getattr(ctx, "logger", None)

        #: 本体代码树（只读）：loader 两件套等静态资源。
        self.plugin_root = PACKAGE_DIR
        self.static_dir = APP_STATIC_DIR
        #: 可写数据目录：theme.json / themes / backgrounds / presets 缓存 / exports。
        self.data_dir = Path(data_dir) if data_dir else _default_data_dir()
        self.theme_path = self.data_dir / "theme.json"
        #: 按时段自动换主题的后台线程状态。
        self._schedule_stop = False
        self._schedule_started = False
        self._schedule_last_result: dict[str, Any] = {}

    # ------------------------------------------------------------- 内部工具

    def _log(self, level: str, message: str) -> None:
        logger = getattr(self, "logger", None)
        fn = getattr(logger, level, None)
        if callable(fn):
            try:
                fn(message)
            except Exception:  # noqa: BLE001
                pass

    def _normalize(self, raw: Any) -> dict[str, Any]:
        """把任意来源的数据规整成合法调色板；坏数据一律回退默认值。"""
        base = default_palette()
        if not isinstance(raw, dict):
            return base

        # 老版本的 theme.json 里可能有 "version" 计数字段：这里刻意忽略它，
        # 下次落盘时自然消失（schema 未变，读旧文件不会报错）。
        name = raw.get("name")
        name = str(name) if isinstance(name, str) and name.strip() else base["name"]

        updated_at = raw.get("updated_at")
        updated_at = (
            str(updated_at)
            if isinstance(updated_at, str) and updated_at.strip()
            else base["updated_at"]
        )

        # 磁盘上的内容也要过滤：文件可能被手工编辑或被其它程序写坏。
        clean_vars, clean_dark, errors = _validate_palette(
            raw.get("vars"), raw.get("dark_vars")
        )
        for message in errors:
            self._log("warning", f"theme.json 中被忽略的条目 —— {message}")

        # 材质：缺失 / 老版本 theme.json 都没有这一段 → 补默认（未启用）。
        clean_materials, material_warnings = _materials.normalize_materials(
            raw.get("materials")
        )
        for message in material_warnings:
            self._log("warning", f"theme.json 的材质设置被修正 —— {message}")

        # 不能直接 bool(...)：字符串 "false" 是真值，会把用户关掉的主题静默重开。
        raw_enabled = raw.get("enabled", True)
        enabled = _coerce_bool(raw_enabled)
        if enabled is None:
            self._log("warning", f"theme.json 的 enabled 无法解析（{raw_enabled!r}），按启用处理")
            enabled = True

        return {
            "schema": SCHEMA_VERSION,
            "name": name,
            "enabled": enabled,
            "updated_at": updated_at,
            "vars": clean_vars,
            "dark_vars": clean_dark,
            "materials": clean_materials,
        }

    def _read_unlocked(self, *, strict: bool = False) -> dict[str, Any]:
        """读盘。文件缺失/损坏/权限错误 → 默认值，绝不抛异常。

        :param strict: 给**写路径**用。文件存在却读不出来时抛 ``_ThemeUnreadable``
            而不是退回默认值 —— 默认值只适合「首次运行」，把它当成合并基线
            会静默清空用户已保存的变量（见 ``_ThemeUnreadable`` 的说明）。
            文件确实不存在时两条路径都返回默认值（首次运行的正常情形）。
        """
        try:
            text = self.theme_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default_palette()
        except OSError as exc:
            if strict:
                raise _ThemeUnreadable(f"读取 theme.json 失败：{exc}") from exc
            self._log("warning", f"读取 theme.json 失败，使用默认值：{exc}")
            return default_palette()
        try:
            return self._normalize(json.loads(text))
        except (ValueError, TypeError) as exc:
            if strict:
                raise _ThemeUnreadable(f"theme.json 解析失败：{exc}") from exc
            self._log("warning", f"theme.json 解析失败，使用默认值：{exc}")
            return default_palette()

    # ---------------------------------------------------------------- 资源指纹
    # theme.json 里附带的两枚指纹（v0.7.0）：加载器靠它们判断「页面里的 CSS 是不是
    # 当前磁盘上这份」「窗口里跑的加载器是不是当前版本」，**不必再整份下载** CSS/JS。
    #: 指纹缓存：{(mtime_ns, size) of css/js → {css_sha8, loader_version}}
    _asset_meta_cache: dict[tuple[int, ...], dict[str, str]] = {}

    def _asset_meta(self) -> dict[str, str]:
        """读 plugin/static/loader/ 两件套，返回 ``{"css_sha8", "loader_version"}``。

        文件缺失 / 读失败时返回空字符串 —— 加载器把空值当作「旧版插件，走原来的
        整份下载路径」，所以这里绝不能抛异常，也不能编一个假指纹（假指纹会让
        加载器永远认为页面是旧的，触发换表/reload 循环）。
        """
        loader_dir = self.static_dir / "loader"
        css_path = loader_dir / "neko-theme-studio.css"
        js_path = loader_dir / "neko-theme-studio.js"
        meta = {"css_sha8": "", "loader_version": ""}
        try:
            stat_css = css_path.stat() if css_path is not None else None
            stat_js = js_path.stat() if js_path is not None else None
        except OSError:
            return meta
        if stat_css is None or stat_js is None:
            return meta
        key = (stat_css.st_mtime_ns, stat_css.st_size, stat_js.st_mtime_ns, stat_js.st_size)
        cached = self._asset_meta_cache.get(key)
        if cached is not None:
            return dict(cached)
        try:
            css_bytes = css_path.read_bytes()
            js_text = js_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return meta
        meta["css_sha8"] = hashlib.sha256(css_bytes).hexdigest()[:8]
        found = re.search(r"SCRIPT_VERSION\s*=\s*'([0-9]+\.[0-9]+\.[0-9]+)'", js_text)
        meta["loader_version"] = found.group(1) if found else ""
        self._asset_meta_cache[key] = dict(meta)
        return meta

    def _with_asset_meta(self, palette: dict[str, Any]) -> dict[str, Any]:
        """复制一份调色板并附上两枚指纹（不改调用方对象）。"""
        payload = dict(palette)
        meta = self._asset_meta()
        if meta.get("css_sha8"):
            payload["css_sha8"] = meta["css_sha8"]
        if meta.get("loader_version"):
            payload["loader_version"] = meta["loader_version"]
        return payload

    def _with_material_vars(self, palette: dict[str, Any]) -> dict[str, Any]:
        """复制一份调色板，附上**编译好的材质变量**（不落盘，纯派生）。

        为什么不把这份当事实：``materials`` 是唯一事实来源，变量只是它的投影。
        磁盘上那份镜像（见 ``_with_material_cache``）是给静态路由 / 加载器用的，
        读进来时被 ``_normalize`` 丢掉并按 materials 重算 —— 用户手改 theme.json、
        导入别人的主题、老版本插件写回，都不会出现「变量说 A、参数说 B」。

        ``enabled=false`` 时返回空的 light/dark —— 加载器看到空表就不写变量、
        也不打 ``data-nts-mt`` 属性，材质 CSS 整段惰性。
        """
        payload = dict(palette)
        raw_materials = payload.get("materials")
        clean, _warnings = _materials.normalize_materials(raw_materials)
        payload["materials"] = clean
        if clean.get("enabled"):
            light, dark = _materials.compile_materials(clean)
            payload["material_vars"] = {"light": light, "dark": dark}
            payload["material_attrs"] = _materials.material_attributes(clean)
            payload["material_compile_version"] = _materials.COMPILE_VERSION
        else:
            payload["material_vars"] = {"light": {}, "dark": {}}
            payload["material_attrs"] = {}
        return payload

    def _with_material_cache(self, palette: dict[str, Any]) -> dict[str, Any]:
        """把材质编译结果附到**落盘**的那份 payload 上（缓存镜像，不是事实来源）。

        加载器是通过 `/plugin/<id>/ui/theme.json` 拿主题的 —— 那条路由由宿主直接
        读磁盘文件，不经过 entry。只把派生变量挂在 entry 返回值上时，应用窗口
        永远看不到材质（运行期诊断里 `mt_vars=0` 就是这么来的）。

        读取侧不信任这份镜像：`_normalize` 只认 `materials`，读进来就重算一遍，
        磁盘上的 `material_vars` / `material_attrs` 会被丢弃。
        """
        payload = dict(palette)
        materials = payload.get("materials")
        clean: dict[str, Any] | None = None
        if isinstance(materials, dict):
            clean, _warnings = _materials.normalize_materials(materials)
        if clean is not None and clean.get("enabled"):
            light, dark = _materials.compile_materials(clean)
            payload["material_vars"] = {"light": light, "dark": dark}
            payload["material_attrs"] = _materials.material_attributes(clean)
            payload["material_compile_version"] = _materials.COMPILE_VERSION
        else:
            payload.pop("material_vars", None)
            payload.pop("material_attrs", None)
            payload.pop("material_compile_version", None)
        return payload

    def _write_unlocked(self, palette: dict[str, Any]) -> None:
        """原子写 theme.json（实现见模块级 ``_write_json_atomic``）。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # 附上资源指纹（v0.7.0）：加载器据此零下载判断 CSS / 加载器是否过期。
        # 材质镜像（v0.15.1）：静态路由不经过 entry，磁盘上必须有编译结果。
        payload = self._with_asset_meta(self._with_material_cache(palette))
        _write_json_atomic(self.theme_path, payload)

    def _ensure_file(self) -> None:
        """启动时保证 ``static/theme.json`` 存在（内容不合法则备份后重写为默认）。"""
        with _WRITE_LOCK:
            if not self.theme_path.exists():
                self._write_unlocked(default_palette())
                self._log("info", f"已创建默认调色板：{self.theme_path}")
                return
            try:
                text = self.theme_path.read_text(encoding="utf-8")
                parsed = json.loads(text)
                # v0.7.0：缺资源指纹时补一次。install.ps1 恢复用户调色板是直接把备份
                # 文件写回来，那份不带 css_sha8 / loader_version —— 不补的话加载器要等
                # 用户下一次应用主题才拿到指纹，这段时间仍会整份下载 CSS/JS。
                # 只补指纹，变量与名称一个字不动。
                if '"css_sha8"' not in text and isinstance(parsed, dict):
                    self._write_unlocked(self._normalize(parsed))
                    self._log("info", "已为 theme.json 补上资源指纹（css_sha8 / loader_version）")
                elif isinstance(parsed, dict):
                    # v0.15.4：指纹过期同样要补。install 升级了加载器，theme.json 里
                    # 仍是旧 loader_version，页面内的旧加载器据此认定「远端就是我这一版」
                    # 而永不升级 —— 只能靠插件启动时把指纹刷新到当前资产。
                    meta = self._asset_meta()
                    stale = (
                        parsed.get("css_sha8") != meta["css_sha8"]
                        or parsed.get("loader_version") != meta["loader_version"]
                        or parsed.get("material_compile_version")
                        != _materials.COMPILE_VERSION
                    )
                    if stale:
                        self._write_unlocked(self._normalize(parsed))
                        self._log(
                            "info",
                            f"已刷新 theme.json 资源指纹（css_sha8={meta['css_sha8']} / "
                            f"loader_version={meta['loader_version']}）",
                        )
                    # v0.15.1：老 theme.json 里材质已启用但没有编译镜像 —— 加载器读的是
                    # 磁盘文件，缺镜像就等于材质不生效。启动时补一次，用户不必重新点应用。
                    materials = parsed.get("materials")
                    if (
                        not stale
                        and isinstance(materials, dict)
                        and materials.get("enabled")
                        and "material_vars" not in parsed
                    ):
                        self._write_unlocked(self._normalize(parsed))
                        self._log("info", "已为 theme.json 补上材质变量镜像")
            except OSError as exc:
                # 「读不出来」（被占用 / 权限）≠「内容坏了」：绝不能拿默认值覆盖，
                # 否则一次瞬时占用就把用户的主题毁掉。保持原样，下次启动再试。
                self._log("warning", f"theme.json 暂时不可读（{exc}），保持原样")
                return
            except ValueError as exc:
                # 内容确实坏了：先把原文件留档再写默认值，不静默销毁用户数据。
                backup = self.theme_path.with_name(self.theme_path.name + ".corrupt")
                try:
                    os.replace(self.theme_path, backup)
                    self._log(
                        "warning",
                        f"theme.json 解析失败（{exc}），原文件已备份为 {backup.name}",
                    )
                except OSError as move_exc:  # pragma: no cover — 备份失败也不阻止恢复
                    self._log(
                        "warning",
                        f"theme.json 解析失败（{exc}），备份失败（{move_exc}）",
                    )
                self._write_unlocked(default_palette())

    def _apply_and_store(
        self,
        *,
        vars_in: Any = None,
        dark_in: Any = None,
        name: Any = None,
        enabled: Any = None,
        materials_in: Any = None,
    ) -> tuple[dict[str, Any], list[str], int]:
        """合并 → 校验 → 原子写。返回 (新调色板, 拒绝原因, 被接受的输入字段数)。

        第三个返回值让调用方能区分「全被拒」与「部分成功」：前者应该报失败，
        否则调用方只看 status 会误以为写进去了（实际磁盘一个字节都没变）。
        """
        with _WRITE_LOCK:
            # strict：拿不到真实基线就中止，绝不用默认值覆盖用户已保存的调色板。
            palette = self._read_unlocked(strict=True)

            merged_vars = dict(palette["vars"])
            merged_dark = dict(palette["dark_vars"])

            clean_vars, clean_dark, errors = _validate_palette(vars_in, dark_in)

            # 删除语义：显式传空串 / None = 回退到 N.E.K.O 原生值。
            # 注意：**被拒绝**的值不会走到这里（它既不在 clean 里，也不是空值），
            # 因此旧值会被原样保留 —— 一次误输入不会毁掉已有的好值。
            for source, merged in ((vars_in, merged_vars), (dark_in, merged_dark)):
                if not isinstance(source, dict):
                    continue
                for raw_name, raw_value in source.items():
                    key = str(raw_name)
                    if not RE_VAR_NAME.match(key):
                        continue
                    if raw_value is None or (
                        isinstance(raw_value, str) and not raw_value.strip()
                    ):
                        merged.pop(key, None)
            merged_vars.update(clean_vars)
            merged_dark.update(clean_dark)

            palette["vars"] = merged_vars
            palette["dark_vars"] = merged_dark

            # 有几个输入字段真的被接受了。静默丢弃（不在 KNOWN_VARS、值非法）
            # 的条目不计数 —— 它们既没生效也不该被视为成功。
            accepted = len(clean_vars) + len(clean_dark)

            if isinstance(name, str):
                # 传空串/纯空白 = 恢复默认名（面板清空输入框时走这条），不是错误。
                palette["name"] = _clip_name(name, DEFAULT_THEME_NAME)
                accepted += 1
            elif name is not None:
                errors.append(f"name: 必须是字符串，收到 {type(name).__name__}")

            if enabled is not None:
                coerced = _coerce_bool(enabled)
                if coerced is None:
                    errors.append(f"enabled: 无法解析为布尔值 {enabled!r}")
                else:
                    palette["enabled"] = coerced
                    accepted += 1

            # 材质是**整份替换**而不是按键合并：面板持有完整参数树，每次保存都
            # 提交全量；按键合并会让「把某参数改回默认」这种操作无法表达
            # （缺省键 = 保持不变，与「改回默认」在语义上撞车）。
            if isinstance(materials_in, dict):
                clean_materials, material_warnings = _materials.normalize_materials(
                    materials_in
                )
                # custom_presets 是用户的素材库，跟五节参数不同：五节是「当前这一套」，
                # 整份替换才对；预设库要是也整份换，面板漏带一次就把用户存的预设全清空。
                # 所以按名字合并 —— 删除走 delete_material_preset 那条独立路径。
                stored_materials = _materials.normalize_materials(
                    palette.get("materials")
                )[0]
                merged_custom = dict(stored_materials.get("custom_presets") or {})
                merged_custom.update(clean_materials.get("custom_presets") or {})
                clean_materials["custom_presets"] = merged_custom
                # 传了自定义预设名却没带库（导出后重新导入的老面板）时，按合并结果补回标签。
                raw_preset = str((materials_in or {}).get("preset") or "").strip()
                if (
                    raw_preset
                    and raw_preset not in _materials.PRESET_IDS
                    and raw_preset in merged_custom
                ):
                    clean_materials["preset"] = raw_preset
                palette["materials"] = clean_materials
                # 材质警告是「已钳制生效」，不是「被拒绝」——不能塞进 errors，
                # 否则面板会对一次成功保存报「拒绝 N 条」。
                for message in material_warnings:
                    self._log("warning", f"apply_theme 材质被修正 —— {message}")
                accepted += 1
            elif materials_in is not None:
                errors.append(
                    f"materials: 必须是对象，收到 {type(materials_in).__name__}"
                )

            palette["updated_at"] = _now_iso()
            palette["schema"] = SCHEMA_VERSION

            self._write_unlocked(palette)
            return palette, errors, accepted

    # ------------------------------------------------------------- 生命周期

    def startup_sync(self) -> dict[str, Any]:
        """启动初始化（同步版）：建目录、建 theme.json、回收背景图、起排程线程。

        本体没有「插件生命周期」这一层，服务是在第一次请求或应用 startup 钩子里
        初始化的；两种上下文（协程内 / 非协程内）都要能用，所以真正干活的是这个
        同步方法，``startup()`` 只是它的协程外壳。
        """
        self._ensure_file()
        self._ensure_dirs()
        palette = self._read_unlocked()
        # 回收没人引用的背景图。失败绝不影响启动。
        try:
            self._gc_backgrounds()
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"背景图回收失败（忽略）：{exc}")
        self._log(
            "info",
            "主题工坊已就绪，调色板 "
            f"({len(palette['vars'])} 亮色 / {len(palette['dark_vars'])} 暗色变量)",
        )
        # 按时段自动换主题：后台线程，每 30s 醒一次、只在时段变化时写盘。
        # 放在启动阶段是唯一能保证「用户重开应用后仍按时段生效」的时机。
        self._start_schedule_worker()
        return {"status": "ready", "theme_path": str(self.theme_path)}

    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok(self.startup_sync())

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        # 排程线程是 daemon，进程退出本来也会带走；但明确置位能让 30s 的
        # sleep 立刻结束，应用退出时不会残留一个还在写 theme.json 的线程。
        self._schedule_stop = True
        self._log("info", "主题工坊已停止")
        return Ok({"status": "shutdown"})

    # ------------------------------------------------------------- UI context

    @ui.context(id="dashboard", title=tr("plugin.name", default="主题工坊"))
    async def studio_context(self) -> dict[str, Any]:
        palette = self._read_unlocked()
        return {
            "name": palette["name"],
            "enabled": palette["enabled"],
            "updated_at": palette["updated_at"],
            "var_count": len(palette["vars"]),
            "dark_count": len(palette["dark_vars"]),
            "theme_url": f"{API_BASE}/theme.json",
            "actions": [
                {"id": "get_theme", "entry_id": "get_theme"},
                {"id": "apply_theme", "entry_id": "apply_theme"},
                {"id": "reset_theme", "entry_id": "reset_theme"},
                {"id": "export_theme", "entry_id": "export_theme"},
                {"id": "theme_json", "entry_id": "theme_json"},
                {"id": "import_theme", "entry_id": "import_theme"},
                {"id": "list_themes", "entry_id": "list_themes"},
                {"id": "get_schedule", "entry_id": "get_schedule"},
                {"id": "save_schedule", "entry_id": "save_schedule"},
                {"id": "save_theme", "entry_id": "save_theme"},
                {"id": "apply_saved_theme", "entry_id": "apply_saved_theme"},
                {"id": "delete_theme", "entry_id": "delete_theme"},
                {"id": "update_theme_meta", "entry_id": "update_theme_meta"},
                {"id": "import_theme_archive", "entry_id": "import_theme_archive"},
                {"id": "export_theme_archive", "entry_id": "export_theme_archive"},
                {"id": "import_background", "entry_id": "import_background"},
                {"id": "import_library", "entry_id": "import_library"},
            ],
        }

    # ------------------------------------------------------------- entries

    @plugin_entry(
        id="get_theme",
        name=tr("entries.get_theme.name", default="读取当前主题"),
        description=tr(
            "entries.get_theme.description",
            default="返回当前调色板：亮色变量、暗色变量、名称与启用状态。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def get_theme(self, **_):
        try:
            # 附带 material_vars：加载器不解析材质参数，只认编译好的变量表。
            return Ok(self._with_material_vars(self._read_unlocked()))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"读取主题失败：{exc}"))

    @plugin_entry(
        id="studio_status",
        name=tr("entries.studio_status.name", default="读取运行状态"),
        description=tr(
            "entries.studio_status.description",
            default=(
                "返回内置主题工坊的运行状态：数据目录、当前主题的名称/更新时间/启用状态、"
                "主题仓库与背景图数量、加载器资源版本，以及应用侧加载器最近一次上报的诊断。"
                "面板用它在「运行状态」卡片里说明「主题到底有没有真的贴到界面上」。"
            ),
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def studio_status(self, **_):
        try:
            palette = self._read_unlocked()
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"读取运行状态失败：{exc}"))
        try:
            library_count = len(self._library_items())
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"列出主题仓库失败：{exc}")
            library_count = 0
        try:
            preset_count = len(self._preset_items())
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"列出内置主题失败：{exc}")
            preset_count = 0
        backgrounds: list[str] = []
        try:
            bg_dir = self._backgrounds_dir()
            if bg_dir.is_dir():
                backgrounds = sorted(p.name for p in bg_dir.iterdir() if p.is_file())
        except OSError as exc:
            self._log("warning", f"读取背景图目录失败：{exc}")
        meta = self._asset_meta()
        diagnostics = None
        diag_path = self.data_dir / "diagnostics.json"
        try:
            if diag_path.is_file():
                raw_diag = json.loads(diag_path.read_text(encoding="utf-8"))
            else:
                raw_diag = None
        except (OSError, ValueError):
            raw_diag = None
        if isinstance(raw_diag, dict):
            # 只回传给面板看的摘要：完整 payload 有几万字，面板用不上。
            diagnostics = {
                "received_at": str(raw_diag.get("received_at") or ""),
                "page": str(raw_diag.get("page") or ""),
                "loader_version": str(raw_diag.get("loader_version") or ""),
                "nts_vars": _safe_int(raw_diag.get("nts_vars")),
                "mt_vars": _safe_int(raw_diag.get("mt_vars")),
                "rules": _safe_int(raw_diag.get("rules")),
                "enabled": bool(raw_diag.get("enabled")),
                "errors": _clean_list(raw_diag.get("errors"), 200, 5),
            }
        return Ok(
            {
                "data_dir": str(self.data_dir),
                "theme_path": str(self.theme_path),
                "theme_name": _clip_name(palette.get("name"), DEFAULT_THEME_NAME),
                "theme_enabled": bool(palette.get("enabled")),
                "theme_updated_at": str(palette.get("updated_at") or ""),
                "light_vars": len(palette.get("vars") or {}),
                "dark_vars": len(palette.get("dark_vars") or {}),
                "library_count": library_count,
                "preset_count": preset_count,
                "background_count": len(backgrounds),
                "backgrounds": backgrounds[:12],
                "loader_version": meta.get("loader_version", ""),
                "css_sha8": meta.get("css_sha8", ""),
                "diagnostics": diagnostics,
            }
        )

    @plugin_entry(
        id="material_spec",
        name=tr("entries.material_spec.name", default="读取材质参数表"),
        description=tr(
            "entries.material_spec.description",
            default=(
                "返回高级材质系统的参数定义、内置预设、画质档位与组件表，"
                "以及当前生效的材质设置。面板据此渲染表单，避免前后端参数表漂移。"
            ),
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def material_spec(self, **_):
        try:
            palette = self._read_unlocked()
            current = palette.get("materials")
            if not isinstance(current, dict):
                current = _materials.default_materials()
            return Ok(
                {
                    "version": _materials.MATERIAL_VERSION,
                    "params": [
                        {
                            "key": param.key,
                            "section": param.section,
                            "kind": param.kind,
                            "default": param.default,
                            "min": param.minimum,
                            "max": param.maximum,
                            "choices": list(param.choices),
                            "label": param.label,
                            "var": _materials.var_name(param),
                        }
                        for param in _materials.PARAMS
                    ],
                    "sections": [
                        {"id": section, "label": _materials.SECTION_LABELS[section]}
                        for section in _materials.SECTIONS
                    ],
                    "components": [
                        {"id": comp, "label": _materials.COMPONENT_LABELS[comp]}
                        for comp in _materials.COMPONENTS
                    ],
                    "component_params": list(_materials.COMPONENT_PARAMS),
                    "quality": [
                        {"id": mode, "label": _materials.QUALITY_LABELS[mode]}
                        for mode in _materials.QUALITY_MODES
                    ],
                    # 六套内置 + custom 一共七张卡：custom 不是「另存的一套值」，
                    # 而是「回到参数默认值」，所以它的 params 是默认表而不是空表。
                    "presets": {
                        preset_id: {
                            "label": _materials.PRESET_LABELS[preset_id],
                            "params": _materials.PRESETS.get(preset_id)
                            or _materials._blank_params(),
                        }
                        for preset_id in _materials.PRESET_IDS
                    },
                    "preset_labels": dict(_materials.PRESET_LABELS),
                    "easings": list(_materials.EASINGS),
                    "blur_levels": dict(_materials.BLUR_LEVELS),
                    # 用户自存的预设：面板按名字列卡片，切换时后端按整套参数铺底，
                    # 与内置预设走同一条 apply_materials 分支。
                    "custom_presets": dict(
                        _materials.normalize_materials(current)[0].get("custom_presets") or {}
                    ),
                    "current": current,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"读取材质参数表失败：{exc}"))

    @plugin_entry(
        id="preview_materials",
        name=tr("entries.preview_materials.name", default="预览材质变量"),
        description=tr(
            "entries.preview_materials.description",
            default=(
                "把给定的材质设置编译成 CSS 变量表返回，不落盘。"
                "面板拖动滑块时用它做实时预览：编译逻辑只有后端这一份，前端不再重写，预览与应用窗口因此不会出现偏差。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "materials": {
                    "type": "object",
                    "description": "待编译的材质设置；省略时使用当前已保存值",
                },
                "mode": {
                    "type": "string",
                    "enum": ["light", "dark"],
                    "default": "light",
                },
            },
        },
    )
    async def preview_materials(self, materials: Any = None, mode: Any = None, **_):
        try:
            if isinstance(materials, dict):
                clean, warnings = _materials.normalize_materials(materials)
            else:
                palette = self._read_unlocked()
                clean, warnings = _materials.normalize_materials(palette.get("materials"))
            # 注意：这里不看 enabled。预览的意义就是「还没启用也能看效果」，
            # 启用与否由 get_theme / 加载器的 material_attrs 决定。
            light_vars, dark_vars = _materials.compile_materials(clean)
            want_dark = str(mode or "light").strip().lower() == "dark"
            return Ok(
                {
                    "mode": "dark" if want_dark else "light",
                    "vars": dark_vars if want_dark else light_vars,
                    "light": light_vars,
                    "dark": dark_vars,
                    "attrs": _materials.material_attributes(clean),
                    "warnings": warnings,
                    "materials": clean,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"编译材质变量失败：{exc}"))

    @plugin_entry(
        id="save_material_preset",
        name=tr("entries.save_material_preset.name", default="保存自定义材质预设"),
        description=tr(
            "entries.save_material_preset.description",
            default=(
                "把当前五节材质参数存成一份命名预设（custom_presets），预设列表里即可一键切换。",
                "传入 materials 时以传入值为准（等于「保存并应用」）。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "预设名（1-40 字符）"},
                "materials": {"type": "object", "description": "可选；要保存的材质树，缺省用磁盘当前值"},
            },
            "required": ["name"],
        },
    )
    async def save_material_preset(self, name: Any = None, materials: Any = None, **_):
        try:
            title = str(name or "").strip()
            if not title:
                return Err(SdkError("name 不能为空。"))
            if len(title) > 40:
                return Err(SdkError(f"预设名最多 40 个字符（当前 {len(title)}）。"))
            palette = self._read_unlocked(strict=True)
            source = materials if isinstance(materials, dict) else palette.get("materials")
            clean, warnings = _materials.normalize_materials(source)
            for warning in warnings:
                self._log("warning", f"混合材质预设：{warning}")
            stored = dict(clean.get("custom_presets") or {})
            stored[title] = {section: dict(clean.get(section) or {}) for section in _materials.SECTIONS}
            clean["custom_presets"] = stored
            palette, errors, _accepted = self._apply_and_store(materials_in=clean)
            if errors:
                return Err(SdkError("；".join(errors)))
            current = palette.get("materials") or {}
            return Ok({
                "name": title,
                "names": sorted((current.get("custom_presets") or {}).keys()),
                "materials": current,
            })
        except _ThemeUnreadable as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"保存自定义预设失败：{exc}"))

    @plugin_entry(
        id="delete_material_preset",
        name=tr("entries.delete_material_preset.name", default="删除自定义材质预设"),
        description=tr(
            "entries.delete_material_preset.description",
            default="按名字删除一份自定义预设；正在使用该预设时回落到 custom（参数保持不动）。",
        ),
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "预设名"}},
            "required": ["name"],
        },
    )
    async def delete_material_preset(self, name: Any = None, **_):
        try:
            title = str(name or "").strip()[:40]
            if not title:
                return Err(SdkError("name 不能为空。"))
            # 删除必须绕开 _apply_and_store 的「预设库按名合并」：合并会把刚删的
            # 那条又加回来。这里直接改磁盘上的 materials 并原子写。
            with _WRITE_LOCK:
                palette = self._read_unlocked(strict=True)
                clean, _warnings = _materials.normalize_materials(palette.get("materials"))
                stored = dict(clean.get("custom_presets") or {})
                if title not in stored:
                    return Err(SdkError(f"没有名为「{title}」的自定义预设。"))
                stored.pop(title, None)
                clean["custom_presets"] = stored
                if clean.get("preset") == title:
                    # 删掉正在使用的预设：参数保持不动，只把标签退回 custom。
                    clean["preset"] = "custom"
                palette["materials"] = clean
                palette["updated_at"] = _now_iso()
                self._write_unlocked(palette)
            current = palette.get("materials") or {}
            return Ok({
                "deleted": title,
                "names": sorted((current.get("custom_presets") or {}).keys()),
                "materials": current,
            })
        except _ThemeUnreadable as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"删除自定义预设失败：{exc}"))

    @plugin_entry(
        id="loader_report",
        name=tr("entries.loader_report.name", default="加载器诊断"),
        description=tr(
            "entries.loader_report.description",
            default="应用侧加载器回传运行期状态，写入 diagnostics.json 供真机自检取证。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "report": {"type": "object", "description": "加载器上报的诊断对象"},
            },
        },
    )
    async def loader_report(self, report: Any = None, **_):
        """记录应用侧加载器的运行时诊断。

        为什么要有这条：材质是否真的注入页面，从宿主进程外部只能推断。
        加载器把它看到的变量数、根属性、样式规则数与错误回传，写进
        ``static/diagnostics.json``，于是「生效了吗」变成可读数据。
        """
        if not isinstance(report, dict):
            return Err("report 必须是对象。")
        received_at = datetime.now().astimezone().isoformat(timespec="seconds")
        attrs = report.get("attrs")
        errors = report.get("errors")
        raw_samples = report.get("samples")
        sample_clean: dict[str, str] = {}
        if isinstance(raw_samples, dict):
            for key, val in list(raw_samples.items())[:20]:
                if isinstance(key, str) and key.startswith("--nts-"):
                    sample_clean[key[:40]] = str(val)[:160]
        raw_elem = report.get("elemSamples")
        elem_clean: dict[str, str] = {}
        ELEM_PROPS = {
            "background-color",
            "background-image",
            "backdrop-filter",
            "border-radius",
            "box-shadow",
            "opacity",
        }
        if isinstance(raw_elem, dict):
            for key, val in list(raw_elem.items())[:24]:
                if isinstance(key, str) and (key in ELEM_PROPS or key.startswith("--nts-mt-")):
                    elem_clean[key[:40]] = str(val)[:200]
        raw_elem2 = report.get("elem2")
        elem2_clean: dict[str, dict[str, str]] = {}
        if isinstance(raw_elem2, dict):
            for key, val in list(raw_elem2.items())[:8]:
                if isinstance(key, str) and isinstance(val, dict):
                    elem2_clean[key[:40]] = {
                        "bg": str(val.get("bg") or "")[:100],
                        "bf": str(val.get("bf") or "")[:80],
                    }
        raw_root = report.get("rootInfo")
        root_clean: dict[str, str] = {}
        if isinstance(raw_root, dict):
            for key in ("className", "inline", "bg", "bgi", "bf", "hits", "rect", "opacity",
                        "visibility", "display", "zIndex"):
                if raw_root.get(key):
                    root_clean[key] = str(raw_root.get(key))[:400]
        raw_shell = report.get("shellInfo")
        shell_clean: dict[str, str] = {}
        if isinstance(raw_shell, dict):
            for key in ("className", "rect", "bg", "bgi", "bf", "opacity", "visibility", "display", "zIndex"):
                if raw_shell.get(key):
                    shell_clean[key] = str(raw_shell.get(key))[:160]
        raw_rects = report.get("compRects")
        rects_clean: dict[str, str] = {}
        if isinstance(raw_rects, dict):
            for sel, rect in list(raw_rects.items())[:8]:
                if isinstance(sel, str) and isinstance(rect, str):
                    rects_clean[sel[:80]] = rect[:40]
        payload = {
            "received_at": received_at,
            "page": str(report.get("page") or "")[:120],
            "comp_matched": str(report.get("compMatched") or "")[:80],
            "loader_version": str(report.get("version") or ""),
            "nts_vars": _safe_int(report.get("ntsVars")),
            "mt_vars": _safe_int(report.get("mtVars")),
            "attrs": _clean_attrs(attrs),
            "rules": _safe_int(report.get("rules")),
            "css_len": _safe_int(report.get("cssLen")),
            "focus_rules": _safe_int(report.get("focusRules")),
            "backdrop": bool(report.get("backdrop")),
            "pointer": bool(report.get("pointer")),
            "enabled": bool(report.get("enabled")),
            "applied_at": str(report.get("appliedAt") or ""),
            "errors": _clean_list(errors, 400, 20),
            "samples": sample_clean,
            "elem_samples": elem_clean,
            "elem2": elem2_clean,
            "root_info": root_clean,
            "shell_info": shell_clean,
            "comp_rects": rects_clean,
            "center_chain": _clean_list(report.get("centerChain"), 260, 10),
            "shell_chain": _clean_list(report.get("shellChain"), 240, 10),
            "viewport": str(report.get("viewport") or "")[:20],
            "mt_rule_count": _safe_int(report.get("mtRuleCount")),
            "mt_decl_count": _safe_int(report.get("mtDeclCount")),
            "page_theme": str(report.get("pageTheme") or "")[:20],
            "history": [],
        }
        target = self.data_dir / "diagnostics.json"
        try:
            # 保留最近 20 次，方便对照「改一版 → 看一次」的因果。
            if target.is_file():
                try:
                    old = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    old = {}
                past = old.get("history")
                if isinstance(past, list):
                    payload["history"] = past[-19:]
            payload["history"].append({
                "at": received_at,
                "page": payload["page"],
                "loader_version": payload["loader_version"],
                "nts_vars": payload["nts_vars"],
                "mt_vars": payload["mt_vars"],
                "rules": payload["rules"],
                "focus_rules": payload["focus_rules"],
                "attrs": payload["attrs"],
                "errors": payload["errors"],
                "under_glow": sample_clean.get("--nts-mt-under-glow", ""),
                "rim_img": sample_clean.get("--nts-mt-rim-img", "")[:80],
                "comp_matched": payload["comp_matched"],
                "bg_c": sample_clean.get("--nts-mt-bg-c", "")[:60],
                "elem_bg": elem_clean.get("background-color", ""),
                "elem_filter": elem_clean.get("backdrop-filter", "")[:60],
            })
            _write_json_atomic(target, payload)
            return Ok({"path": str(target), "received_at": received_at})
        except OSError as exc:
            return Err(f"写入诊断记录失败：{exc}")

    @plugin_entry(
        id="apply_theme",
        name=tr("entries.apply_theme.name", default="应用主题"),
        description=tr(
            "entries.apply_theme.description",
            default="把给定的 CSS 变量合并进当前调色板并原子写入 theme.json。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "vars": {"type": "object", "description": "亮色变量表 --nts-* → 值"},
                "dark_vars": {"type": "object", "description": "暗色变量表 --nts-* → 值"},
                "name": {"type": "string", "maxLength": 60},
                "enabled": {"type": "boolean", "default": True},
                "materials": {
                    "type": "object",
                    "description": (
                        "高级材质设置（整份替换）：version/enabled/preset/quality/"
                        "global/lighting/refraction/surface/animation/dark/"
                        "components/custom_presets"
                    ),
                },
            },
        },
    )
    async def apply_theme(
        self,
        vars: Any = None,  # noqa: A002 — 名称与契约 input_schema 一致
        dark_vars: Any = None,
        name: Any = None,
        enabled: Any = None,
        materials: Any = None,
        **_,
    ):
        try:
            palette, errors, accepted = self._apply_and_store(
                vars_in=vars,
                dark_in=dark_vars,
                name=name,
                enabled=enabled,
                materials_in=materials,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, PermissionError):
                return Err(SdkError(_permission_hint(exc)))
            return Err(SdkError(f"写入主题失败：{exc}"))

        gave_input = any(
            x is not None for x in (vars, dark_vars, name, enabled, materials)
        )

        if errors:
            # 合法部分已落盘；把被拒绝的条目如实回传给面板展示。
            self._log("warning", f"apply_theme 拒绝 {len(errors)} 条：{errors[:3]}")
            if gave_input and accepted == 0:
                # 一项都没被接受 = 这次调用什么也没做，必须报失败，
                # 否则调用方只看 status 会以为写成功了。
                return Err(SdkError("没有写入任何改动：" + "；".join(errors)))
            return Ok({**self._with_material_vars(palette), "rejected": errors})
        return Ok(self._with_material_vars(palette))

    @plugin_entry(
        id="reset_theme",
        name=tr("entries.reset_theme.name", default="恢复默认主题"),
        description=tr(
            "entries.reset_theme.description",
            default="把内置默认调色板写回 theme.json（清空所有自定义颜色）。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def reset_theme(self, **_):
        try:
            with _WRITE_LOCK:
                palette = default_palette()
                palette["updated_at"] = _now_iso()
                self._write_unlocked(palette)
            return Ok(self._with_material_vars(palette))
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"恢复默认主题失败：{exc}"))

    @plugin_entry(
        id="export_theme",
        name=tr("entries.export_theme.name", default="导出主题"),
        description=tr(
            "entries.export_theme.description",
            default="把当前调色板序列化为 JSON 文本，供用户复制保存或分享。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def export_theme(self, **_):
        try:
            raw = self._read_unlocked()
            # 磁盘上的 theme.json 带派生镜像（material_vars / material_attrs /
            # material_compile_version），那是给加载器读的投影。导出的 JSON 是给人看、
            # 给人改、给人分享的：混进 250 个派生变量只会让文件翻倍并误导用户，
            # 所以这里只导出事实字段（materials 子树才是材质的唯一真相）。
            clean = self._normalize(raw)
            payload = json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=False)
            return Ok(
                {
                    "payload": payload,
                    "name": clean["name"],
                    "updated_at": clean["updated_at"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"导出主题失败：{exc}"))

    @plugin_entry(
        id="theme_json",
        name=tr("entries.theme_json.name", default="复制主题 JSON"),
        description=tr(
            "entries.theme_json.description",
            default=(
                "把仓库里或内置的一套主题导出成可分享的纯文本 JSON；"
                "引用背景图的变量会被摘掉（图片无法放进文本）。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {
                    "type": "string",
                    "description": "主题 id（仓库条目或内置主题）",
                },
                "id": {"type": "string", "description": "theme_id 的别名（面板习惯用 id）"},
            },
        },
    )
    async def theme_json(self, theme_id: Any = "", id: Any = "", **_):
        """把一套已存在的主题序列化成可以直接贴给别人的 JSON 文本。

        与 ``export_theme`` 的区别：那个导出的是**当前正在用**的调色板（含未保存状态），
        这个导出的是**仓库里某一套**主题（内置主题也可以），方便在对话/issue 里贴文本。

        背景图没法放进文本：引用 ``backgrounds/<name>`` 的变量会被摘掉并在返回值里
        报告张数，由面板提示「要连图一起分享请用分享包」。
        """
        theme_id = theme_id or id
        try:
            record = self._load_library_item(theme_id)
            raw_vars = record.get("vars") if isinstance(record.get("vars"), dict) else {}
            raw_dark = (
                record.get("dark_vars")
                if isinstance(record.get("dark_vars"), dict)
                else {}
            )
            # 图片没法变成文本：先数出这套主题引用了哪些图（去重后的文件名），
            # 再把对应变量从导出的 JSON 里摘掉。
            # 必须在 _validate_palette **之前**从原始记录里数：内置主题存的是可移植的
            # url("images/<hash>")，而校验只认本机 backgrounds/ 地址，会把它们判非法
            # 丢掉 —— 先校验再数，面板就会对「含背景图」的主题显示「0 张」。
            image_names = set()
            image_keys = set()
            for source in (raw_vars, raw_dark):
                for key, value in source.items():
                    if not isinstance(value, str):
                        continue
                    match = RE_URL_ANY.search(value)
                    if not match:
                        continue
                    image_keys.add(key)
                    basename = match.group(1).replace("\\", "/").rsplit("/", 1)[-1]
                    if basename:
                        image_names.add(basename)
            clean_vars, clean_dark, _errors = _validate_palette(
                record.get("vars"), record.get("dark_vars")
            )
            for mapping in (clean_vars, clean_dark):
                for key in [k for k in mapping if k in image_keys]:
                    mapping.pop(key, None)
            if not clean_vars and not clean_dark:
                raise SdkError("这套主题里没有可分享的调色板变量。")
            payload = {
                "schema": SCHEMA_VERSION,
                "name": _clip_name(record.get("name"), DEFAULT_THEME_NAME),
                "note": _theme_note(record.get("note")),
                "tags": _theme_tags(record.get("tags")),
                "vars": clean_vars,
                "dark_vars": clean_dark,
            }
            # 材质子树只在启用时随文本一起分享：没启用时它整份是默认值，
            # 塞进 JSON 只会让贴出来的内容翻几倍，且对方导入后也不会生效。
            record_materials = record.get("materials")
            if isinstance(record_materials, dict):
                clean_materials, _material_warnings = _materials.normalize_materials(
                    record_materials
                )
                if clean_materials.get("enabled"):
                    payload["materials"] = clean_materials
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"生成主题 JSON 失败：{exc}"))
        return Ok(
            {
                "payload": text,
                "name": payload["name"],
                "light": len(clean_vars),
                "dark": len(clean_dark),
                "images": len(image_names),
                "bytes": len(text.encode("utf-8")),
            }
        )

    @plugin_entry(
        id="import_theme",
        name=tr("entries.import_theme.name", default="导入主题"),
        description=tr(
            "entries.import_theme.description",
            default="解析并校验一段调色板 JSON 文本，合法后整体应用（非法条目一律丢弃）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "payload": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": IMPORT_MAX_CHARS,
                    "description": "调色板 JSON 文本",
                }
            },
            "required": ["payload"],
        },
    )
    async def import_theme(self, payload: str = "", **_):
        if not isinstance(payload, str):
            return Err(SdkError(f"导入内容必须是字符串，收到 {type(payload).__name__}。"))
        text = payload.strip()
        if not text:
            return Err(SdkError("导入内容为空。"))
        if len(text) > IMPORT_MAX_CHARS:
            # input_schema 的 maxLength 只是声明，宿主不一定执行 → 自己兜底。
            return Err(
                SdkError(f"导入内容过长（{len(text)} 字符，上限 {IMPORT_MAX_CHARS}）。")
            )

        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            return Err(SdkError(f"导入内容不是合法 JSON：{exc}"))
        if not isinstance(parsed, dict):
            return Err(SdkError("导入内容必须是一个 JSON 对象。"))

        payload_vars = parsed.get("vars")
        payload_dark = parsed.get("dark_vars")
        if not isinstance(payload_vars, dict) and not isinstance(payload_dark, dict):
            return Err(SdkError("导入内容缺少 vars / dark_vars 字段。"))

        # 导入是「整体替换」而非合并：先清空再写入合法子集。
        try:
            with _WRITE_LOCK:
                # strict：读不出旧基线时中止，绝不拿默认值当基线覆盖用户调色板。
                palette = self._read_unlocked(strict=True)
                clean_vars, clean_dark, errors = _validate_palette(payload_vars, payload_dark)

                # 同 apply_saved_theme：丢掉指向已删除图片的引用。
                clean_vars, gone_light = self._without_missing_images(clean_vars)
                clean_dark, gone_dark = self._without_missing_images(clean_dark)
                for gone in (gone_light, gone_dark):
                    for key in gone:
                        errors = list(errors) + [
                            f"{key}: 引用的背景图已不存在，已回退原生"
                        ]

                # 全被拒 = 这次导入没有任何可用内容。旧实现在这里照样写盘，
                # 结果「导入成功」却把用户原来的调色板清空（变量全丢、名字还改了）。
                # 但「只调了材质、一个颜色都没改」的主题是合法的：materials.enabled
                # 为真就算有内容，否则这种主题导出来就再也导不回去（0.15.39）。
                incoming_materials = parsed.get("materials")
                has_material = (
                    isinstance(incoming_materials, dict)
                    and bool(incoming_materials.get("enabled"))
                    # 只有「包本身就没带颜色」才放行：带了颜色却全被拒的是坏包，
                    # 那种情况必须继续拦，否则一次误导入就把用户调色板清空。
                    and not payload_vars
                    and not payload_dark
                )
                if not clean_vars and not clean_dark and not has_material:
                    raise SdkError(
                        "导入内容里没有可用的变量（变量名或取值都不被识别），"
                        "已取消导入，当前调色板没有改动。"
                    )

                old_light = set(palette["vars"])
                old_dark = set(palette["dark_vars"])
                palette["vars"] = clean_vars
                palette["dark_vars"] = clean_dark
                cleared = {
                    "light": len(old_light - set(clean_vars)),
                    "dark": len(old_dark - set(clean_dark)),
                }

                name = parsed.get("name")
                if isinstance(name, str) and name.strip():
                    palette["name"] = _clip_name(name, DEFAULT_THEME_NAME)
                if isinstance(parsed.get("enabled"), bool):
                    palette["enabled"] = bool(parsed["enabled"])

                # 材质：导入是整体替换。没带这一段的老主题保留用户当前材质，
                # 而不是把它清成默认 —— 换一套配色不该顺手重置材质。
                if isinstance(parsed.get("materials"), dict):
                    (
                        palette["materials"],
                        material_warnings,
                    ) = _materials.normalize_materials(parsed["materials"])
                    for message in material_warnings:
                        self._log("warning", f"导入的材质设置被修正 —— {message}")

                palette["updated_at"] = _now_iso()
                palette["schema"] = SCHEMA_VERSION

                self._write_unlocked(palette)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"导入主题失败：{exc}"))

        if errors:
            self._log("warning", f"import_theme 拒绝 {len(errors)} 条：{errors[:3]}")
            return Ok(
                {**self._with_material_vars(palette), "rejected": errors, "cleared": cleared}
            )
        return Ok({**self._with_material_vars(palette), "cleared": cleared})

    # ------------------------------------------------------- 主题仓库 / 背景图

    def _library_dir(self) -> Path:
        return self.data_dir / LIBRARY_DIR_NAME

    def _backgrounds_dir(self) -> Path:
        return self.data_dir / BACKGROUND_DIR_NAME

    def _presets_dir(self) -> Path:
        """内置主题目录（随插件发布，只读）。"""
        return PRESETS_DIR

    def _preset_path(self, theme_id: str) -> Path:
        return self._presets_dir() / f"{theme_id}.json"

    # -- 「按时段自动换主题」排程 -----------------------------------------

    def _schedule_path(self) -> Path:
        return self.data_dir / SCHEDULE_FILE_NAME

    def _schedule_defaults(self) -> dict[str, Any]:
        # schema 2：多了 weekend_zones（周末另一套时段表）。schema 1 的文件读进来
        # 时 weekend_zones 会退化成空表 = 和平时一样，不需要迁移脚本。
        return {
            "schema": 2,
            "enabled": False,
            "zones": {zone: "" for zone in SCHEDULE_ZONES},
            "weekend_zones": {zone: "" for zone in SCHEDULE_ZONES},
        }

    def _theme_exists(self, theme_id: str) -> bool:
        """主题 id 是否真的存在（内置预设或用户仓库条目）。"""
        if not isinstance(theme_id, str) or not RE_THEME_ID.match(theme_id):
            return False
        if self._preset_path(theme_id).is_file():
            return True
        return (self._library_dir() / f"{theme_id}.json").is_file()

    def _normalize_schedule(
        self,
        raw: Any,
        *,
        strict: bool = True,
        dropped: list[dict[str, Any]] | None = None,
        drop_missing: bool = True,
    ) -> tuple[dict[str, Any], list[str]]:
        """把任意来源的排程数据规整成 {schema,enabled,zones}；返回 (排程, 错误列表)。

        strict=True（保存路径）时任何一项不合法都记进错误、由调用方整体拒绝；
        strict=False（读取路径）时坏项被丢弃并记 warning，绝不让一个手改坏的
        schedule.json 把插件 entry 打崩。
        """
        errors: list[str] = []
        schedule = self._schedule_defaults()
        if raw is None:
            return schedule, errors
        if not isinstance(raw, dict):
            errors.append("排程数据必须是一个 JSON 对象")
            return schedule, errors

        flag = _coerce_bool(raw.get("enabled", False))
        if flag is None:
            errors.append(f"enabled 不是可识别的布尔值：{raw.get('enabled')!r}")
        else:
            schedule["enabled"] = flag

        for field, label in (("zones", "工作日"), ("weekend_zones", "周末")):
            raw_zones = raw.get(field, {})
            if raw_zones is None:
                raw_zones = {}
            if not isinstance(raw_zones, dict):
                errors.append(f"{field} 必须是一个对象")
                continue
            unknown = sorted(set(raw_zones) - set(SCHEDULE_ZONES))
            if unknown:
                errors.append(label + "的未知时段：" + "、".join(unknown))
            target = schedule[field]
            for zone in SCHEDULE_ZONES:
                value = raw_zones.get(zone, "")
                if value is None:
                    value = ""
                if not isinstance(value, str):
                    errors.append(f"{label}时段「{zone}」的值必须是字符串主题 id")
                    continue
                value = value.strip()
                if not value:
                    continue
                if not RE_THEME_ID.match(value):
                    errors.append(f"{label}时段「{zone}」的主题 id 不合法：{value!r}")
                    continue
                if not self._theme_exists(value):
                    # drop_missing=False 只给「回写运行时标记」用（_update_schedule_stamp）：
                    # 那里读盘只是拿基线，如果顺手把失效时段丢掉，再把整份写回去，
                    # 就等于用户什么都没做、配置却被静默清空 —— tick 只能走 missing
                    # 分支去抖，永久丢配置。所以这条路径必须原样保留用户值。
                    if not drop_missing and not strict:
                        target[zone] = value
                        continue
                    errors.append(f"{label}时段「{zone}」引用了不存在的主题：{value}")
                    # 非严格读（_read_schedule）时把「被丢掉的时段」留一份，
                    # 面板要据此提醒用户「这套主题已经不在仓库里了」。
                    if dropped is not None:
                        dropped.append({"field": field, "zone": zone, "theme_id": value})
                    continue
                target[zone] = value

        # 「上次按哪个时段切过哪套主题」由后台线程写回，用来避免每 30s 重写一次
        # theme.json（每次写都会让加载器重绘，没必要）。坏值直接丢弃，不算错误 ——
        # 它只是运行时标记，不是用户配置。
        last_zone = raw.get("last_zone")
        if isinstance(last_zone, str) and last_zone in SCHEDULE_ZONES:
            schedule["last_zone"] = last_zone
        last_theme = raw.get("last_theme")
        if isinstance(last_theme, str) and RE_THEME_ID.match(last_theme.strip()):
            schedule["last_theme"] = last_theme.strip()
        # 跨天型边界（周日 23:59 → 周一 00:01）时时段名可能相同（都是 night），
        # 但两边用的是不同的表，所以「上次切过哪套」必须连天型一起记。
        last_day_kind = raw.get("last_day_kind")
        if isinstance(last_day_kind, str) and last_day_kind in SCHEDULE_DAY_KINDS:
            schedule["last_day_kind"] = last_day_kind
        # 「上次因为主题不存在而跳过」的指纹（day_kind|zone|theme_id）。它唯一的
        # 作用是让 warning 只刷一次 —— 以前缺主题时每 30 秒写一条，一天 2880 条，
        # 真出问题时反而把有用的日志淹了。同样是运行时标记，坏值丢弃。
        last_missing = raw.get("last_missing")
        if isinstance(last_missing, str) and 0 < len(last_missing) <= 200:
            schedule["last_missing"] = last_missing
        return schedule, errors

    def _read_schedule(self, *, drop_missing: bool = True) -> dict[str, Any]:
        """读 schedule.json；不存在/读不出/坏 JSON 都按「未启用」处理。

        :param drop_missing: 引用「已不存在主题」的时段要不要丢掉。面板/诊断要
            丢掉（这样能报 stale 并提示用户重选）；回写运行时标记时必须保留，
            否则一次写回就把用户配置永久清空。
        """
        path = self._schedule_path()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._schedule_defaults()
        except OSError as exc:
            self._log("warning", f"读取 schedule.json 失败（{exc}），按未启用处理")
            return self._schedule_defaults()
        try:
            raw = json.loads(text)
        except ValueError as exc:
            self._log("warning", f"schedule.json 不是合法 JSON（{exc}），按未启用处理")
            return self._schedule_defaults()
        dropped: list[dict[str, Any]] = []
        schedule, errors = self._normalize_schedule(
            raw, strict=False, dropped=dropped, drop_missing=drop_missing
        )
        self._schedule_stale = dropped
        # 去抖：后台线程每 30 秒读一次这份文件，而失效时段不会因为「读」而被改写，
        # 所以同一组错误会一直被读到。以前这里每 30 秒写一条 warning（一天 2880 条），
        # 真出问题时反而把有用的日志淹了。同一组错误只记一次；错误消失后签名归零，
        # 下次再出现（用户又删了一次）还能看到。
        signature = "|".join(errors)
        if signature != getattr(self, "_schedule_warn_sig", ""):
            self._schedule_warn_sig = signature
            for message in errors:
                self._log("warning", f"schedule.json：{message}（该时段已忽略）")
        return schedule

    def _save_schedule_unlocked(self, schedule: dict[str, Any]) -> None:
        _write_json_atomic(self._schedule_path(), schedule)

    def _update_schedule_stamp(self, **changes: Any) -> None:
        """只更新运行期标记（last_*），绝不覆盖用户配置。

        tick 是「锁外算、算完回写」：它开始时读到的 schedule 可能是几十毫秒前的旧快照，
        而这段时间里用户刚点了保存。旧实现把整份旧快照写回去，于是 enabled/zones 被冲掉，
        用户看到「刚保存就失效、开关自己跳回」，日志里一条报错都没有。
        这里在锁内重新读一次，只合并传进来的键（value=None 表示删除该键）。
        """
        try:
            with _WRITE_LOCK:
                # 关键：拿基线时不要丢掉「引用了已删除主题」的时段。
                fresh = self._read_schedule(drop_missing=False)
                for key, value in changes.items():
                    if value is None:
                        fresh.pop(key, None)
                    else:
                        fresh[key] = value
                self._save_schedule_unlocked(fresh)
        except Exception as exc:  # noqa: BLE001
            # 标记写不回去最多多重切一次主题，不影响配置本身。
            self._log("warning", f"自动换主题：写回 schedule.json 失败（{exc}）")

    # -- 「按时段自动换主题」执行 -----------------------------------------

    def _schedule_zone_map(self, schedule: dict[str, Any], day_kind: str) -> dict[str, Any]:
        """取某天型此刻该用哪张表；周末表全是空值 = 和平时一样。"""
        zones = schedule.get("zones")
        if not isinstance(zones, dict):
            zones = {}
        if day_kind == "weekend":
            weekend = schedule.get("weekend_zones")
            if isinstance(weekend, dict) and any(str(value or "").strip() for value in weekend.values()):
                return weekend
        return zones

    def _now(self) -> datetime:
        """当前时间。单测会覆盖它固定时段 —— 否则跨午夜跑测试时
        weekday/weekend 或时段边界一换，用例就随机变红（与功能无关的假失败）。
        """
        return datetime.now()

    def _schedule_tick(self, *, force: bool = False) -> dict[str, Any]:
        """检查一次：当前时段该用哪套主题，需要时换过去。**绝不抛异常**。

        换主题 = 改写 theme.json，加载器 1.5s 内自己轮询到、所有窗口一起变。
        这样「自动换」与「手动点主题」走的是同一条下发通路，不需要让加载器
        再认识一套预设格式（否则两端各解析一次，契约面翻倍）。
        """
        try:
            schedule = self._read_schedule()
        except Exception as exc:  # noqa: BLE001
            return {"action": "error", "error": f"读取排程失败：{exc}"}
        if not schedule.get("enabled"):
            return {"action": "disabled"}
        stamp = self._now()
        day_kind = _day_kind_for(stamp.weekday())
        zone = _zone_for_hour(stamp.hour)
        theme_id = str(self._schedule_zone_map(schedule, day_kind).get(zone) or "")
        if not theme_id:
            return {"action": "unset", "zone": zone, "day_kind": day_kind}
        # 「上次已经按这套切过」才跳过。但主题可能已经被删掉了，所以必须再确认它
        # 还在 —— 否则用户把主题重新导入回来之后，unchanged 会永远短路，自动换主题
        # 再也恢复不了（这一条是 v0.4.22 修的）。
        same_stamp = (
            schedule.get("last_zone") == zone
            and schedule.get("last_theme") == theme_id
            and schedule.get("last_day_kind", "weekday") == day_kind
        )
        if not force and same_stamp and self._theme_exists(theme_id):
            return {"action": "unchanged", "zone": zone, "day_kind": day_kind, "theme_id": theme_id}
        if not self._theme_exists(theme_id):
            fingerprint = f"{day_kind}|{zone}|{theme_id}"
            if schedule.get("last_missing") != fingerprint:
                self._log(
                    "warning",
                    f"自动换主题：{day_kind}时段「{zone}」指定的主题 {theme_id} 已被删除，"
                    "先保持当前主题不动（重新导入或改选主题后会自动恢复）",
                )
            # 把 last_* 一起推进：这样下一轮走 same_stamp 分支时 theme_exists 为假，
            # 会重新落到这里（但 warning 已被 last_missing 去抖），而主题一旦被找回
            # 来，same_stamp 为真 + theme_exists 为真 → 走 unchanged 之前会先 apply。
            self._update_schedule_stamp(
                last_zone=zone,
                last_day_kind=day_kind,
                last_theme=theme_id,
                last_missing=fingerprint,
            )
            return {"action": "missing", "zone": zone, "day_kind": day_kind, "theme_id": theme_id}
        try:
            outcome = self._apply_theme_record(theme_id)
        except Exception as exc:  # noqa: BLE001
            # 条目本身坏了（例如旧版本存的空配色）时 apply 会一直失败，而排程线程
            # 每 30s 重试一次 → 一天 2880 条一模一样的 warning，真出问题时反而把
            # 有用日志淹掉。和「主题被删」一样按指纹去抖：只报第一次。
            # 注意这里**不**推进 last_zone/last_theme：条目被改好之后下一轮要能
            # 自动恢复（推进了就会被 same_stamp 判成 unchanged 而永远不再尝试）。
            message = str(exc)
            fingerprint = f"{day_kind}|{zone}|{theme_id}|error:{message[:120]}"
            if schedule.get("last_missing") != fingerprint:
                self._log("warning", f"自动换主题失败（{zone} → {theme_id}）：{message}")
                self._update_schedule_stamp(last_missing=fingerprint)
            return {
                "action": "error",
                "zone": zone,
                "day_kind": day_kind,
                "theme_id": theme_id,
                "error": str(exc),
            }
        # 记下「已按这个时段切过这套主题」，避免每 30s 重写一次 theme.json。
        # 只合并标记、不写回开始时读到的那份快照（见 _update_schedule_stamp 的说明）。
        self._update_schedule_stamp(
            last_zone=zone,
            last_day_kind=day_kind,
            last_theme=theme_id,
            # 真的切成功了：清掉「缺主题」标记，下次再缺（换了主题又被删）还要能警告。
            last_missing=None,
        )
        name = str((outcome.get("theme") or {}).get("name") or theme_id)
        self._log("info", f"自动换主题：{zone} → {name}")
        return {
            "action": "applied",
            "zone": zone,
            "day_kind": day_kind,
            "theme_id": theme_id,
            "theme": outcome.get("theme"),
        }

    def _run_schedule_worker(self) -> None:
        """后台线程：每 SCHEDULE_TICK_SECONDS 检查一次时段。

        sleep 切成 1s 小片，是为了 shutdown 置位后能立刻退出 —— 一整段
        time.sleep(30) 会让「停止插件」卡 30s，用户会以为插件挂了。
        """
        while not self._schedule_stop:
            try:
                state = self._schedule_tick()
                if state.get("action") == "applied":
                    self._schedule_last_result = state
            except Exception as exc:  # noqa: BLE001 —— 线程不能死
                self._log("warning", f"自动换主题检查异常（已忽略）：{exc}")
            deadline = time.time() + SCHEDULE_TICK_SECONDS
            while not self._schedule_stop:
                left = deadline - time.time()
                if left <= 0:
                    break
                time.sleep(min(1.0, left))

    def _start_schedule_worker(self) -> None:
        """起排程线程（幂等：重复 startup 不会起第二个）。"""
        if self._schedule_started:
            return
        self._schedule_started = True
        self._schedule_stop = False
        threading.Thread(
            target=self._run_schedule_worker, name="nts-schedule", daemon=True
        ).start()

    def _ensure_dirs(self) -> None:
        """确保 themes/ 与 backgrounds/ 存在（插件启动时调用一次）。"""
        for path in (
            self.data_dir,
            self._library_dir(),
            self._backgrounds_dir(),
            self._presets_dir(),
        ):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # pragma: no cover — 磁盘异常不该让 entry 崩掉
                self._log("warning", f"创建目录失败 {path}：{exc}")

    def _background_url(self, filename: str) -> str:
        return f"{BACKGROUNDS_URL_BASE}/{filename}"

    @staticmethod
    def _image_filename_in_value(value: Any) -> str:
        """从 ``url("…/backgrounds/<name>")`` 里取出文件名；不是图片值返回空串。"""
        if not isinstance(value, str):
            return ""
        match = RE_BG_VALUE_CAPTURE.search(value)
        return match.group(1) if match else ""

    def _image_refs(self, mapping: Any) -> list:
        """``[(变量名, 背景图文件名)]`` —— 只保留磁盘上真的存在的文件。"""
        refs = []
        if not isinstance(mapping, dict):
            return refs
        for key, value in mapping.items():
            name = self._image_filename_in_value(value)
            if not name or not RE_BG_NAME.match(name):
                continue
            if not (self._backgrounds_dir() / name).is_file():
                continue
            refs.append((str(key), name))
        return refs

    def _without_missing_images(self, mapping: Any) -> tuple:
        """丢掉引用了「磁盘上已不存在」的背景图的变量。

        返回 ``(clean, dropped_keys)``。把一张已经被删掉的图片 URL 留在
        theme.json 里，只会让应用侧请求 404，而且 ``has_image`` 会自相矛盾地
        说没有 —— 所以写入前就把它摘掉（= 回退原生）。
        """
        if not isinstance(mapping, dict):
            return {}, []
        clean = {}
        dropped = []
        for key, value in mapping.items():
            name = self._image_filename_in_value(value)
            if name and RE_BG_NAME.match(name) and not (
                self._backgrounds_dir() / name
            ).is_file():
                dropped.append(str(key))
                continue
            clean[key] = value
        return clean, dropped

    def _gc_backgrounds(self) -> list:
        """删掉没被 theme.json / 任何仓库条目引用的背景图，返回删掉的文件名。

        每次上传都按内容哈希落一个文件，用户删主题、导入失败都会留下孤儿图。
        这里用**原始文本**扫引用（而不是校验后的 vars），避免把「变量非法但
        文件仍是用户资产」的图片误删。
        """
        bg_dir = self._backgrounds_dir()
        if not bg_dir.is_dir():
            return []
        texts = []
        # P0（审查 py-review P0-1）：读不到 theme.json 时**必须中止**，绝不能
        # 「当它没有引用任何图片」继续删。实测 PermissionError 场景下，用户当前
        # 正在用的背景图会被删掉且没有备份 —— 主题直接变成一片空白。
        # 宁可这轮不回收（孤儿图只是占几 MB），也不能删活数据。
        if self.theme_path.exists():
            try:
                texts.append(self.theme_path.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                self._log("warning", f"背景图回收已跳过：theme.json 暂时不可读（{exc}）")
                return []
        # 仓库/预设条目读不出来同样中止：它们也可能引用图片。
        unreadable = False
        for directory in (self._library_dir(), self._presets_dir()):
            if not directory.is_dir():
                continue
            for item in directory.glob("*.json"):
                try:
                    texts.append(item.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    unreadable = True
                    break
            if unreadable:
                break
        if unreadable:
            self._log("warning", "背景图回收已跳过：主题仓库/预设暂时不可读")
            return []
        referenced = set()
        for text in texts:
            referenced.update(RE_BG_REF_TEXT.findall(text))
        removed = []
        # 删除改为「移入 .trash」而不是 unlink：误删时用户还能自己捞回来，
        # 也避免云同步/杀软占用句柄导致的半删除状态。
        trash = bg_dir / ".trash"
        for path in bg_dir.iterdir():
            if not path.is_file() or path.name in referenced:
                continue
            if not RE_BG_NAME.match(path.name):
                continue
            try:
                trash.mkdir(exist_ok=True)
                target = trash / path.name
                if target.exists():
                    target.unlink()
                os.replace(path, target)
                removed.append(path.name)
            except OSError:
                continue
        if removed:
            self._log("info", f"回收了 {len(removed)} 张没被引用的背景图")
        return removed

    def _save_background_bytes(self, data: bytes, *, label: str = "") -> str:
        """校验魔数后落盘一张背景图，返回文件名（``<hash16>.<ext>``）。"""
        if not data:
            raise SdkError("图片内容为空。")
        if len(data) > IMAGE_MAX_BYTES:
            raise SdkError(
                f"图片过大（{len(data)} 字节，上限 {IMAGE_MAX_BYTES} 字节）。"
            )
        ext = _image_extension(data)
        if not ext:
            raise SdkError("不支持的图片格式（只接受 png / jpg / webp / gif）。")
        filename = f"{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
        self._ensure_dirs()
        target = self._backgrounds_dir() / filename
        wrote = False
        try:
            # 同一张图（预设自带的背景）会在每次 list_themes / apply_saved_theme 时
            # 走一遍这里：文件已经在了、大小也一样就什么都不用做。
            if not target.is_file() or target.stat().st_size != len(data):
                # tmp 名必须唯一：排程线程 apply 与面板 list_themes/import_background
                # 可能同时落同一张图，共用一个 <hash>.tmp 时先完成的一方 replace 之后，
                # 另一方就找不到自己的 tmp（报「保存图片失败」），还会残留 .tmp 文件。
                tmp = target.with_name(
                    f"{target.name}.{os.getpid()}-{threading.get_ident()}.tmp"
                )
                try:
                    tmp.write_bytes(data)
                    # os.replace 在 Windows 上会被「目标正被替换 / 杀软正在扫描」顶掉
                    # （WinError 5 / 32）。同一张图可能由排程线程与面板同时落盘，
                    # 这里只重试这两类错误，其余 OSError 立刻上抛。
                    for attempt in range(6):
                        try:
                            os.replace(tmp, target)
                            break
                        except OSError as exc:
                            if getattr(exc, "winerror", None) not in (5, 32) or attempt == 5:
                                raise
                            time.sleep(0.04 * (attempt + 1))
                    wrote = True
                finally:
                    if tmp.exists():
                        try:
                            tmp.unlink()
                        except OSError:
                            pass
        except OSError as exc:
            raise SdkError(f"保存图片失败：{exc}") from exc
        # 只在真写盘时记一行；否则每次列主题都会给每张预设图刷一条「已保存」，
        # 日志里全是没发生的事（实测一次 list_themes 4 行）。
        if wrote:
            self._log("info", f"背景图已保存：{filename}（{len(data)} 字节）{label}")
        return filename

    # ------------------------------------------------------------- 仓库读写

    def _library_items(self) -> list:
        """仓库条目列表（按保存时间倒序）；坏文件跳过并记日志。"""
        directory = self._library_dir()
        items = []
        if not directory.is_dir():
            return items
        for path in directory.glob("*.json"):
            if not RE_THEME_ID.match(path.stem):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                self._log("warning", f"仓库条目已损坏，跳过 {path.name}：{exc}")
                continue
            if not isinstance(raw, dict):
                continue
            clean_vars, clean_dark, errors = _validate_palette(
                raw.get("vars"), raw.get("dark_vars")
            )
            if errors:
                self._log("warning", f"仓库条目 {path.name} 有 {len(errors)} 条被忽略")
            items.append(
                {
                    "id": path.stem,
                    "name": _clip_name(raw.get("name"), path.stem),
                    "saved_at": _fmt_saved_at(raw.get("saved_at")),
                    "light": len(clean_vars),
                    "dark": len(clean_dark),
                    "has_image": bool(
                        self._image_refs(clean_vars)
                        or self._image_refs(clean_dark)
                    ),
                    "builtin": False,
                    "note": (raw.get("note").strip()[:120]
                             if isinstance(raw.get("note"), str) else ""),
                    "tags": [
                        tag.strip()[:12]
                        for tag in (raw.get("tags") or [])[:4]
                        if isinstance(tag, str) and tag.strip()
                    ] if isinstance(raw.get("tags"), list) else [],
                    "family": _preset_family(raw, clean_vars, clean_dark),
                    "preview": _preset_preview(clean_vars, clean_dark),
                    "material": _material_badge(raw.get("materials")),
                    "_sort": str(raw.get("saved_at") or ""),
                }
            )
        items.sort(key=lambda item: item["_sort"], reverse=True)
        for item in items:
            item.pop("_sort", None)
        return items

    def _copy_preset_item(self, item: dict) -> dict:
        """缓存项的深一层副本：tags 是 list、preview 是 dict，浅拷贝会把它们共享出去。

        调用方（面板渲染、导出、测试）拿到的东西必须能随便改，
        否则「某个调用方给 tags 加了一项」会污染所有后续读取。
        """
        copy = dict(item)
        tags = item.get("tags")
        copy["tags"] = list(tags) if isinstance(tags, (list, tuple)) else []
        preview = item.get("preview")
        copy["preview"] = dict(preview) if isinstance(preview, dict) else {}
        return copy

    def _presets_signature(self, directory: Path):
        """内置主题目录的「内容指纹」，用来缓存 ``_preset_items`` 的解析结果。

        每次 ``list_themes`` 都把二十几套内置主题重新读盘、重新校验、再把随包
        背景图重新算一遍（实测 35~55 ms，全在面板打开/刷新的关键路径上），
        而这些内容只在「装了新版本」时才会变。用 (文件名, mtime_ns, 大小)
        做指纹：升级换文件必然失效，「往 presets/ 里塞了自己的文件」也会失效，
        不会把旧结果缓存成陈旧数据。
        """
        try:
            entries = []
            for path in sorted(directory.glob("*.json")):
                stat = path.stat()
                entries.append((path.name, stat.st_mtime_ns, stat.st_size))
            images = directory / "images"
            if images.is_dir():
                for path in sorted(images.iterdir()):
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    entries.append((path.name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            return None
        return tuple(entries)

    def _preset_items(self) -> list:
        """内置主题列表（随插件发布、只读），格式与仓库条目一致。

        预设里的背景图写的是可移植的 ``url("images/<hash>")``，而 ``_validate_palette``
        只接受指向本机 ``backgrounds/`` 的绝对地址 —— 所以先用
        ``_rewrite_archive_refs`` 改写成本机地址再校验，否则那条变量会被判非法、
        条目上「含背景图」也会谎报 False。
        """
        directory = self._presets_dir()
        items = []
        if not directory.is_dir():
            return items
        signature = self._presets_signature(directory)
        cached = getattr(self, "_preset_items_cache", None)
        if cached is not None and signature is not None and cached[0] == signature:
            return [self._copy_preset_item(item) for item in cached[1]]
        for path in sorted(directory.glob("*.json")):
            if not RE_THEME_ID.match(path.stem):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                self._log("warning", f"内置主题已损坏，跳过 {path.name}：{exc}")
                continue
            if not isinstance(raw, dict):
                continue
            local = self._rewrite_archive_refs(
                raw, {}, label=f"内置主题 {path.stem}", from_presets=True
            )
            clean_vars, clean_dark, errors = _validate_palette(
                local.get("vars"), local.get("dark_vars")
            )
            if errors:
                self._log("warning", f"内置主题 {path.name} 有 {len(errors)} 条被忽略")
            tags_raw = raw.get("tags")
            tags = []
            if isinstance(tags_raw, list):
                for tag in tags_raw[:4]:
                    if isinstance(tag, str) and tag.strip():
                        tags.append(tag.strip()[:12])
            note_raw = raw.get("note")
            note = note_raw.strip()[:120] if isinstance(note_raw, str) else ""
            items.append(
                {
                    "id": path.stem,
                    "name": _clip_name(raw.get("name"), path.stem),
                    "saved_at": "内置",
                    "light": len(clean_vars),
                    "dark": len(clean_dark),
                    "has_image": bool(
                        self._image_refs(clean_vars)
                        or self._image_refs(clean_dark)
                    ),
                    "builtin": True,
                    "note": note,
                    "tags": tags,
                    "family": _preset_family(raw, clean_vars, clean_dark),
                    "preview": _preset_preview(clean_vars, clean_dark),
                    "material": _material_badge(raw.get("materials")),
                }
            )
        if signature is not None:
            self._preset_items_cache = (signature, items)
        return [self._copy_preset_item(item) for item in items]

    def _load_library_item(self, theme_id: Any) -> dict:
        if not isinstance(theme_id, str) or not RE_THEME_ID.match(theme_id.strip()):
            raise SdkError("主题仓库条目 id 非法。")
        clean_id = theme_id.strip()
        path = self._library_dir() / f"{clean_id}.json"
        if not path.is_file():
            # 仓库里没有就再看内置主题 —— 内置主题与应用/导出走同一条路径。
            preset = self._preset_path(clean_id)
            if preset.is_file():
                path = preset
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SdkError(f"主题仓库里没有这个主题：{theme_id}") from exc
        except OSError as exc:
            raise SdkError(f"读取主题仓库条目失败：{exc}") from exc
        except (ValueError, TypeError) as exc:
            raise SdkError(f"主题仓库条目已损坏：{exc}") from exc
        if not isinstance(raw, dict):
            raise SdkError("主题仓库条目格式不正确。")
        # 文件名才是仓库主键（_library_items 也按 path.stem 报 id）。记录里的 "id"
        # 是内容、可被手工改坏；一旦两者不一致，任何「按记录 id 定位」的代码都会
        # 操作到另一套主题（曾出现 delete_theme("beta") 删掉 alpha.json）。
        # 统一在读取处把 id 归一成文件名，单点消灭这一类歧义。
        if path.parent == self._library_dir():
            raw["id"] = path.stem
        return raw

    def _save_library_item(
        self,
        *,
        name: Any,
        vars_map: dict,
        dark_map: dict,
        note: Any = None,
        tags: Any = None,
        materials: Any = None,
    ) -> dict:
        """写一个新仓库条目；id 由服务端按「名称 slug + 时间戳」生成。

        全程持 ``_WRITE_LOCK``：并发保存（面板连点、两个窗口）时，不持锁会
        两个请求算出同一个 id、抢同一个 ``.tmp``，最后一条静默覆盖另一条。
        """
        self._ensure_dirs()
        with _WRITE_LOCK:
            directory = self._library_dir()
            if len(list(directory.glob("*.json"))) >= LIBRARY_MAX_ITEMS:
                raise SdkError(
                    f"主题仓库已满（上限 {LIBRARY_MAX_ITEMS} 个），请先删除一些再保存。"
                )
            label = _clip_name(name, DEFAULT_THEME_NAME)
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "theme"
            base = f"{slug[:32]}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            theme_id = base
            counter = 2
            while (directory / f"{theme_id}.json").exists():
                theme_id = f"{base}-{counter}"
                counter += 1
            record = {
                "schema": SCHEMA_VERSION,
                "id": theme_id,
                "name": label,
                "saved_at": _now_iso(),
                "vars": dict(vars_map),
                "dark_vars": dict(dark_map),
            }
            # 材质随主题成套保存：切回这套主题时「配色 + 玻璃质感」必须一起回来，
            # 否则用户会看到「换个主题连材质都变了」。
            clean_materials, _material_warnings = _materials.normalize_materials(
                materials
            )
            record["materials"] = clean_materials
            # 导入别人的主题时把作者写的简介/标签一起留住（面板「自定义」里会展示）。
            clean_note = _theme_note(note)
            if clean_note:
                record["note"] = clean_note
            clean_tags = _theme_tags(tags)
            if clean_tags:
                record["tags"] = clean_tags
            path = directory / f"{theme_id}.json"
            # 唯一临时名 + fsync：断电/崩溃时要么是完整条目，要么什么都没有，
            # 不会留下半截 JSON 让列表页报「条目已损坏」。
            tmp = directory / f".{theme_id}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, indent=2))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
            return record

    @staticmethod
    def _theme_summary(
        record: dict, *, light: int, dark: int, has_image: bool
    ) -> dict:
        return {
            "id": str(record.get("id") or ""),
            "name": _clip_name(record.get("name"), DEFAULT_THEME_NAME),
            "saved_at": _fmt_saved_at(record.get("saved_at")),
            "light": light,
            "dark": dark,
            "has_image": has_image,
            # 摘要也要带简介/标签：面板「保存」成功后直接拿返回值渲染卡片，
            # 少了这两项就会出现「存完卡片上没有刚写的简介」。
            "note": _theme_note(record.get("note")),
            "tags": _theme_tags(record.get("tags")),
        }

    # --------------------------------------------------- 分享包（zip）导入导出

    def _guard_export_size(self, data: bytes, *, what: str) -> None:
        """导出前自己先卡一次上限 —— 导出用 Ok、恢复用 SdkError 是最坏的一种不匹配。

        ``_parse_archive`` / ``_parse_library_archive`` 都按 ``ARCHIVE_MAX_BYTES``
        拒绝大包；如果导出不检查，用户会拿到一个「自己的插件拒绝恢复」的备份，
        而且通常是在重装/换机那一刻才发现。
        """
        size = len(data)
        if size <= ARCHIVE_MAX_BYTES:
            return
        limit = ARCHIVE_MAX_BYTES
        raise SdkError(
            f"{what}有 {size / 1048576:.1f} MB，超过恢复上限 "
            f"{limit / 1048576:.0f} MB，导入时会被拒绝。"
            "背景图会原样打进包里：先去「变量编辑 → 聊天背景图」去掉几张图再导出，"
            "或者只导出配色（导出 JSON）。"
        )

    def _build_archive(
        self,
        *,
        name: str,
        vars_map: dict,
        dark_map: dict,
        note: Any = None,
        tags: Any = None,
        materials: Any = None,
    ) -> tuple:
        """打分享包：``theme.json``（图片引用改写成 ``images/<name>``）+ ``images/``。"""
        refs = self._image_refs(vars_map) + self._image_refs(dark_map)

        def portable(value: Any) -> Any:
            filename = self._image_filename_in_value(value)
            return f'url("images/{filename}")' if filename else value

        record = {
            "schema": SCHEMA_VERSION,
            "name": name,
            "saved_at": _now_iso(),
            "vars": {key: portable(value) for key, value in vars_map.items()},
            "dark_vars": {key: portable(value) for key, value in dark_map.items()},
        }
        # 分享包也带上材质：别人导入的是「一套完整外观」，不是只有颜色。
        clean_materials, _material_warnings = _materials.normalize_materials(materials)
        if clean_materials.get("enabled"):
            record["materials"] = clean_materials
        # 作者写的介绍/标签一起打包：别人导入后，面板「自定义」里能看到出处，
        # 而不是一条没有说明的匿名配色。
        clean_note = _theme_note(note)
        if clean_note:
            record["note"] = clean_note
        clean_tags = _theme_tags(tags)
        if clean_tags:
            record["tags"] = clean_tags
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "theme.json", json.dumps(record, ensure_ascii=False, indent=2)
            )
            archive.writestr("README.txt", ARCHIVE_README)
            for filename in sorted({item[1] for item in refs}):
                path = self._backgrounds_dir() / filename
                try:
                    archive.write(path, f"images/{filename}")
                except OSError as exc:  # pragma: no cover
                    self._log("warning", f"打包图片失败（已跳过）{filename}：{exc}")
        payload = buffer.getvalue()
        self._guard_export_size(payload, what="这个分享包")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"{_safe_archive_name(name)}-{stamp}.zip", payload

    # ------------------------------------------------------- 主题库备份 / 恢复

    def _library_records_for_backup(self) -> list:
        """读出仓库里所有可用条目；坏条目跳过并记 warning，不让一个坏文件毁掉整份备份。"""
        records = []
        for path in sorted(self._library_dir().glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._log("warning", f"备份跳过损坏的仓库条目 {path.name}：{exc}")
                continue
            if not isinstance(raw, dict):
                continue
            if not isinstance(raw.get("vars"), dict) or not isinstance(
                raw.get("dark_vars"), dict
            ):
                continue
            theme_id = str(raw.get("id") or path.stem).strip()
            if not RE_THEME_ID.match(theme_id):
                # 条目的 id 字段被手改成非法值时，退回文件名（同样是服务端生成过的）。
                theme_id = path.stem
            if not RE_THEME_ID.match(theme_id):
                self._log("warning", f"备份跳过 id 不合法的仓库条目 {path.name}")
                continue
            record = dict(raw)
            record["id"] = theme_id
            records.append(record)
        return records

    @staticmethod
    def _library_archive_files(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        """导出与恢复使用相同的文件数和解压体积上限。"""
        files = [info for info in archive.infolist() if not info.is_dir()]
        if len(files) > LIBRARY_ARCHIVE_MAX_ENTRIES:
            raise SdkError(
                f"备份包条目过多（{len(files)} 个，上限 {LIBRARY_ARCHIVE_MAX_ENTRIES} 个）。"
            )
        total = sum(info.file_size for info in files)
        if total > ARCHIVE_MAX_UNCOMPRESSED:
            raise SdkError(
                f"备份包解压后过大（{total} 字节，上限 {ARCHIVE_MAX_UNCOMPRESSED} 字节）。"
            )
        return files

    def _build_library_archive(self) -> tuple:
        """把所有自定义主题打成一个 zip：``themes/<id>.json`` + ``images/`` + ``manifest.json``。

        图片引用改写成可移植的 ``url("images/<name>")`` —— 备份包换台机器也要能恢复，
        所以绝不能把本机 ``/plugin/.../ui/backgrounds/...`` 这种绝对地址写进去。
        """
        records = self._library_records_for_backup()
        if not records:
            raise SdkError("主题仓库里还没有自己的主题，没有什么可备份的。")
        manifest = {
            "format": LIBRARY_ARCHIVE_FORMAT,
            "schema": SCHEMA_VERSION,
            "exported_at": _now_iso(),
            "themes": [],
        }
        images = set()

        def portable(value):
            filename = self._image_filename_in_value(value)
            return f'url("images/{filename}")' if filename else value

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for record in records:
                vars_map = {
                    str(key): portable(value)
                    for key, value in record["vars"].items()
                }
                dark_map = {
                    str(key): portable(value)
                    for key, value in record["dark_vars"].items()
                }
                images.update(item[1] for item in self._image_refs(record["vars"]))
                images.update(item[1] for item in self._image_refs(record["dark_vars"]))
                doc = {
                    "schema": SCHEMA_VERSION,
                    "id": record["id"],
                    "name": _clip_name(record.get("name"), DEFAULT_THEME_NAME),
                    "saved_at": _fmt_saved_at(record.get("saved_at")),
                    "vars": vars_map,
                    "dark_vars": dark_map,
                }
                note = _theme_note(record.get("note"))
                if note:
                    doc["note"] = note
                tags = _theme_tags(record.get("tags"))
                if tags:
                    doc["tags"] = tags
                # 备份必须连材质一起带走，否则「恢复备份后质感全丢」。
                if isinstance(record.get("materials"), dict):
                    doc["materials"] = record["materials"]
                archive.writestr(
                    f"themes/{record['id']}.json",
                    json.dumps(doc, ensure_ascii=False, indent=2),
                )
                manifest["themes"].append(
                    {
                        "id": record["id"],
                        "name": doc["name"],
                        "light": len(vars_map),
                        "dark": len(dark_map),
                        "has_image": bool(
                            self._image_refs(record["vars"])
                            or self._image_refs(record["dark_vars"])
                        ),
                    }
                )
            for filename in sorted(images):
                try:
                    archive.write(
                        self._backgrounds_dir() / filename, f"images/{filename}"
                    )
                except OSError as exc:  # pragma: no cover
                    self._log("warning", f"备份图片失败（已跳过）{filename}：{exc}")
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            archive.writestr("README.txt", LIBRARY_ARCHIVE_README)
            self._library_archive_files(archive)
            count = len(manifest["themes"])
        payload = buffer.getvalue()
        self._guard_export_size(payload, what="这份主题库备份")
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"neko-theme-studio-library-{stamp}.zip", payload, count

    def _parse_library_archive(self, data: bytes) -> tuple:
        """解析库备份包，返回 ``(条目列表, {图片文件名: 字节})``。

        条目列表里每项是 ``{"theme": dict, "path": str}`` 或 ``{"error": 说明}``：
        单条坏了只跳过它，不拒绝整个包（用户可能只是有一两条手改坏了）。
        """
        if len(data) > ARCHIVE_MAX_BYTES:
            raise SdkError(
                f"备份包过大（{len(data)} 字节，上限 {ARCHIVE_MAX_BYTES} 字节）。"
            )
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise SdkError("备份包不是有效的 zip 文件。") from exc
        items = []
        images = {}
        with archive:
            infos = self._library_archive_files(archive)
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("images/"):
                    basename = name.rsplit("/", 1)[-1]
                    if RE_BG_NAME.match(basename):
                        images[basename] = archive.read(info)
            for info in infos:
                name = info.filename.replace("\\", "/")
                if not (name.startswith("themes/") and name.endswith(".json")):
                    continue
                try:
                    raw = json.loads(archive.read(info).decode("utf-8-sig"))
                except (ValueError, UnicodeDecodeError) as exc:
                    items.append({"error": f"{name} 不是合法 JSON：{exc}"})
                    continue
                if not isinstance(raw, dict):
                    items.append({"error": f"{name} 的内容不是主题对象"})
                    continue
                items.append({"theme": raw, "path": name})
        if not items:
            raise SdkError("备份包里没有任何主题（期望 themes/*.json）。")
        return items, images

    def _rewrite_archive_refs(
        self, raw: dict, images: dict, *, label: str, from_presets: bool = False
    ) -> dict:
        """把分享包里的图片落盘到本机，并把引用改写成绝对 URL。

        ``from_presets=True`` 时，包内没带这张图会再去 ``presets/`` 里找 ——
        内置主题的图是**随插件一起发布**的，不是"缺失"，绝不能按第三方分享包
        的规则丢掉（踩过：应用一次内置主题后背景图就凭空消失，``backgrounds/``
        被判成孤儿回收，重装/重启也回不来）。
        """

        def rewrite(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            match = RE_URL_ANY.search(value)
            if not match:
                return value
            basename = match.group(1).replace("\\", "/").rsplit("/", 1)[-1]
            if not basename:
                return ""
            payload = images.get(basename)
            if payload is None and from_presets:
                # 内置主题的图随插件一起发布，放在 presets/images/ 下 ——
                # 写法与分享包内部结构一致（都是 images/<hash>）。
                for shipped in (
                    self._presets_dir() / "images" / basename,
                    self._presets_dir() / basename,
                ):
                    if not shipped.is_file():
                        continue
                    try:
                        payload = shipped.read_bytes()
                    except OSError:
                        payload = None
                    else:
                        break
            if payload is None:
                # 裸 JSON 导入（或 zip 里没带这张图）时，如果本机已经有同名文件
                # —— 文件名就是内容哈希，同名必然同内容 —— 直接复用它。
                # 不这么做的话，「复制导出的 JSON 再粘贴导入」会把用户自己的背景图
                # 静默丢掉，只剩下颜色（真 bug，实测复现）。
                if RE_BG_NAME.match(basename) and (
                    self._backgrounds_dir() / basename
                ).is_file():
                    return f'url("{self._background_url(basename)}")'
                # 确实没有这张图 → 整条丢弃（= 回退原生），绝不把别人的路径写进 theme.json。
                self._log("warning", f"分享包里缺少图片 {basename}，该引用已丢弃")
                return ""
            filename = self._save_background_bytes(payload, label=label)
            return f'url("{self._background_url(filename)}")'

        vars_in = raw.get("vars")
        dark_in = raw.get("dark_vars")
        out = {
            "schema": SCHEMA_VERSION,
            "name": _clip_name(raw.get("name"), DEFAULT_THEME_NAME),
            "vars": (
                {str(k): rewrite(v) for k, v in vars_in.items()}
                if isinstance(vars_in, dict)
                else {}
            ),
            "dark_vars": (
                {str(k): rewrite(v) for k, v in dark_in.items()}
                if isinstance(dark_in, dict)
                else {}
            ),
        }
        # 材质整段原样带过来：它只含颜色/数值，不含图片引用，不需要 rewrite。
        # 落盘前的合法性由 _materials.normalize_materials 统一兜底。
        if isinstance(raw.get("materials"), dict):
            out["materials"] = raw["materials"]
        # 作者写的介绍/标签跟着分享走：面板「自定义」要显示它们，导入时丢掉
        # 会让「别人分享的主题」看起来像一条没有出处的匿名配色（踩过）。
        note = _theme_note(raw.get("note"))
        if note:
            out["note"] = note
        tags = _theme_tags(raw.get("tags"))
        if tags:
            out["tags"] = tags
        return out

    def _parse_archive(self, data: bytes, *, label: str) -> tuple:
        """解析分享包（zip 或裸 JSON），返回 ``(调色板, 来源格式)``。"""
        if len(data) > ARCHIVE_MAX_BYTES:
            raise SdkError(
                f"分享包过大（{len(data)} 字节，上限 {ARCHIVE_MAX_BYTES} 字节）。"
            )
        if data[:4] != b"PK\x03\x04":
            # 裸 JSON（别人直接发 theme.json / 手工精简的 {"vars":{...}}）：
            # 走与 zip 包内 theme.json 相同的结构校验，再改写图片引用。
            parsed = _parse_theme_json(data)
            return self._rewrite_archive_refs(parsed, {}, label=label), "json"

        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise SdkError("分享包不是有效的 zip 压缩包。") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > ARCHIVE_MAX_ENTRIES:
                raise SdkError(
                    f"压缩包条目过多（{len(infos)} 个，上限 {ARCHIVE_MAX_ENTRIES}）。"
                )
            total = sum(info.file_size for info in infos)
            if total > ARCHIVE_MAX_UNCOMPRESSED:
                raise SdkError(
                    "压缩包解压后过大"
                    f"（{total} 字节，上限 {ARCHIVE_MAX_UNCOMPRESSED}）。"
                )
            entries = {}
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/") or ":" in name:
                    raise SdkError(f"压缩包里含有非法路径：{name}")
                # 重名条目（大小写/斜杠差异归一化后撞名）会让后一个静默覆盖前一个，
                # 攻击者可以用它把「校验过的 theme.json」换成「另一个同名条目」。
                # 直接拒绝，合法分享包不会出现重名。
                if name in entries:
                    raise SdkError(f"压缩包里有重复文件名：{name}")
                entries[name] = info.filename

            json_names = [n for n in entries if n.lower().endswith(".json")]
            json_names.sort(
                key=lambda item: (
                    0 if item.rsplit("/", 1)[-1].lower() == "theme.json" else 1,
                    item,
                )
            )
            if not json_names:
                raise SdkError(
                    "压缩包里没有 theme.json（主题分享包应包含一个 JSON 调色板）。"
                )
            try:
                parsed = json.loads(
                    archive.read(entries[json_names[0]]).decode("utf-8", "replace")
                )
            except (ValueError, TypeError) as exc:
                raise SdkError(f"压缩包里的 JSON 无法解析：{exc}") from exc
            if not isinstance(parsed, dict):
                raise SdkError("压缩包里的 JSON 必须是一个对象。")

            images = {}
            for name in entries:
                if not name.lower().endswith(IMAGE_SUFFIXES):
                    continue
                images[name.rsplit("/", 1)[-1]] = archive.read(entries[name])
            return self._rewrite_archive_refs(parsed, images, label=label), "zip"

    # ---------------------------------------------------------------- entries

    @plugin_entry(
        id="list_themes",
        name=tr("entries.list_themes.name", default="列出主题仓库"),
        description=tr(
            "entries.list_themes.description",
            default="返回主题仓库里的全部条目：名称、保存时间、亮/暗变量条数、是否含背景图。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def list_themes(self, **_):
        try:
            items = self._preset_items() + self._library_items()
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"列出主题仓库失败：{exc}"))
        return Ok({"count": len(items), "themes": items})

    @plugin_entry(
        id="get_schedule",
        name=tr("entries.get_schedule.name", default="读取自动换主题排程"),
        description=tr(
            "entries.get_schedule.description",
            default="返回「按时段自动换主题」的设置：是否启用、四个时段各用哪套主题，以及时段边界。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def get_schedule(self, **_):
        try:
            schedule = self._read_schedule()
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"读取自动换主题排程失败：{exc}"))
        stamp = datetime.now()
        day_kind = _day_kind_for(stamp.weekday())
        zone = _zone_for_hour(stamp.hour)
        zone_map = self._schedule_zone_map(schedule, day_kind)
        # 「排程里还记着、但仓库里已经没有」的时段：不静默变空，让面板能说明白。
        stale = [
            {
                "day_kind": "weekend" if item.get("field") == "weekend_zones" else "weekday",
                "zone": item.get("zone"),
                "theme_id": item.get("theme_id"),
            }
            for item in getattr(self, "_schedule_stale", [])
        ]
        return Ok(
            {
                "schedule": schedule,
                "stale": stale,
                "zones": list(SCHEDULE_ZONES),
                "day_kinds": list(SCHEDULE_DAY_KINDS),
                # 面板要画「早晨 06:00–10:00」这类边界文案；边界只有后端这一份，
                # 与其让前端再写一遍（第三份实现、必然漂移），不如直接给它。
                "windows": _schedule_windows(),
                # 面板要显示「现在是工作日还是周末、属于哪个时段、该用哪套」，
                # 与其让前端再算一遍边界（第三份实现），不如直接把判定结果给它。
                "now": {
                    "zone": zone,
                    "hour": stamp.hour,
                    "day_kind": day_kind,
                    "weekend": day_kind == "weekend",
                    "theme_id": str(zone_map.get(zone) or ""),
                },
                "last": getattr(self, "_schedule_last_result", None) or None,
            }
        )

    @plugin_entry(
        id="save_schedule",
        name=tr("entries.save_schedule.name", default="保存自动换主题排程"),
        description=tr(
            "entries.save_schedule.description",
            default="保存「按时段自动换主题」的设置。zones 里的主题 id 必须来自内置主题或主题仓库。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "zones": {"type": "object"},
                "weekend_zones": {"type": "object"},
            },
        },
    )
    async def save_schedule(
        self,
        enabled: Any = None,
        zones: Any = None,
        weekend_zones: Any = None,
        **_,
    ):
        raw = {
            "enabled": enabled if enabled is not None else False,
            "zones": zones if isinstance(zones, dict) else {},
        }
        if weekend_zones is not None:
            raw["weekend_zones"] = weekend_zones if isinstance(weekend_zones, dict) else {}
        else:
            # 没传这个字段 = 「沿用已保存的周末表」。老面板（或打开后一直没刷新的
            # 窗口）保存时不会带它，直接按空表写会把用户配好的周末表凭空清空。
            try:
                keep = self._read_schedule().get("weekend_zones")
            except Exception:  # noqa: BLE001
                keep = None
            raw["weekend_zones"] = keep if isinstance(keep, dict) else {}
        schedule, errors = self._normalize_schedule(raw, strict=True)
        if errors:
            return Err(SdkError("排程没有保存：" + "；".join(errors)))
        try:
            with _WRITE_LOCK:
                self._save_schedule_unlocked(schedule)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"保存自动换主题排程失败：{exc}"))
        self._log(
            "info",
            "自动换主题排程已保存：enabled="
            + str(schedule["enabled"])
            + " zones="
            + json.dumps(schedule["zones"], ensure_ascii=False)
            + " weekend_zones="
            + json.dumps(schedule.get("weekend_zones") or {}, ensure_ascii=False),
        )
        # 存完立刻生效一次，否则用户勾上「启用」后要等到下一个时段边界才看到
        # 任何变化，会以为没生效。force=True：不受 last_zone 标记抑制。
        applied = None
        if schedule["enabled"]:
            applied = self._schedule_tick(force=True)
            if applied.get("action") == "applied":
                self._schedule_last_result = applied
        return Ok(
            {
                "saved": True,
                "schedule": schedule,
                "windows": _schedule_windows(),
                "now": {
                    "zone": _zone_for_hour(datetime.now().hour),
                    "hour": datetime.now().hour,
                    "day_kind": _day_kind_for(datetime.now().weekday()),
                },
                "applied": applied,
            }
        )

    @plugin_entry(
        id="save_theme",
        name=tr("entries.save_theme.name", default="保存到主题仓库"),
        description=tr(
            "entries.save_theme.description",
            default=(
                "把当前已保存的调色板存成一个仓库条目，便于以后一键切换或打包分享；"
                "可顺便写一句简介（note）与标签（tags），分享包会一起带走。"
            ),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 60},
                "note": {"type": "string", "maxLength": 120},
                "tags": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "maxLength": 12},
                },
            },
        },
    )
    async def save_theme(
        self,
        name: Any = None,
        note: Any = None,
        tags: Any = None,
        **_,
    ):
        try:
            # 不在这里套 _WRITE_LOCK：``_save_library_item`` 内部已经持锁，外面再套
            # 一层等于同线程二次加锁 —— 非可重入锁会永久阻塞（真踩过：点「保存」
            # 之后整个插件的 entry 队列全挂，每条都 30s 超时）。
            palette = self._read_unlocked(strict=True)
            # 空条目守卫：默认/重置后的调色板是空的，存进仓库只会得到一条
            # 「点一下就把整套配色清空」的陷阱条目（旧版本已经能产生这种条目）。
            # 这里保存的是**整套配色**，一个可用变量都没有就没有可存的东西；
            # 只想存材质参数的用户走「材质预设」（save_material_preset），功能不缺。
            _clean_vars, _clean_dark, _ignored = _validate_palette(
                palette["vars"], palette["dark_vars"]
            )
            if not _clean_vars and not _clean_dark:
                return Err(
                    SdkError(
                        "当前主题没有任何可用的配色变量（可能刚恢复默认），"
                        "先调几个颜色再保存到主题库。"
                    )
                )
            record = self._save_library_item(
                name=(
                    name
                    if isinstance(name, str) and name.strip()
                    else palette["name"]
                ),
                vars_map=palette["vars"],
                dark_map=palette["dark_vars"],
                # 简介/标签在保存时就能写：面板「保存」框里有「一句话简介」，
                # 不必先存完再进「编辑」补 —— 分享包原样带走这两项。
                note=note,
                tags=tags,
                materials=palette.get("materials"),
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"保存到主题仓库失败：{exc}"))
        summary = self._theme_summary(
            record,
            light=len(record["vars"]),
            dark=len(record["dark_vars"]),
            has_image=bool(
                self._image_refs(record["vars"])
                or self._image_refs(record["dark_vars"])
            ),
        )
        self._log("info", f"已保存到主题仓库：{summary['name']}（{summary['id']}）")
        return Ok({"saved": True, "theme": summary})

    @plugin_entry(
        id="apply_saved_theme",
        name=tr("entries.apply_saved_theme.name", default="切换到仓库主题"),
        description=tr(
            "entries.apply_saved_theme.description",
            default="把仓库里某个主题整体替换为当前调色板（含背景图），并写入 theme.json。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                # 面板发的是 id（更自然），CLI/脚本里常用 theme_id。两个都收，
                # 避免调用方猜参数名 —— 曾因只认 theme_id 导致面板「应用/分享/删除」
                # 全部报「条目 id 非法」。
                "id": {"type": "string", "maxLength": 64},
            },
        },
    )
    def _apply_theme_record(self, theme_id: str) -> dict[str, Any]:
        """把仓库/内置主题整体换进 theme.json，返回摘要。**会抛异常**。

        抽出来是因为「用户点主题」和「后台按时段切主题」必须走同一条路径：
        任何一边多写一行（比如忘了 _without_missing_images），就会出现
        「面板切过去背景是 404、自动切换却是好的」这类只有一半人复现的 bug。
        """
        record = self._load_library_item(theme_id)
        # 内置主题的背景图是可移植写法（images/<hash>），先改成本机绝对 URL。
        local = self._rewrite_archive_refs(
            record,
            {},
            label=f"主题 {record.get('name') or theme_id}",
            from_presets=True,
        )
        record = dict(record)
        record["vars"] = local["vars"]
        record["dark_vars"] = local["dark_vars"]
        record["name"] = local["name"]
        with _WRITE_LOCK:
            palette = self._read_unlocked(strict=True)
            clean_vars, clean_dark, errors = _validate_palette(
                record.get("vars"), record.get("dark_vars")
            )
            # 仓库条目可能引用一张后来被删掉的背景图；写进 theme.json 只会 404。
            clean_vars, gone_light = self._without_missing_images(clean_vars)
            clean_dark, gone_dark = self._without_missing_images(clean_dark)
            for gone in (gone_light, gone_dark):
                for key in gone:
                    errors = list(errors) + [
                        f"{key}: 引用的背景图已不存在，已回退原生"
                    ]
            if not clean_vars and not clean_dark:
                # 旧版本允许保存空调色板，仓库里可能已经躺着这种条目。直接应用会
                # 把用户当前配色整片清空（面板只显示「已应用」），必须在写盘前拦住。
                raise SdkError(
                    f"主题条目 {record.get('name') or theme_id} 没有任何可用的配色变量"
                    "（多为旧版本保存的空条目），已拒绝应用——否则会把当前配色清空。"
                )
            palette["vars"] = clean_vars
            palette["dark_vars"] = clean_dark
            palette["name"] = _clip_name(record.get("name"), palette["name"])
            # 老条目没有 materials 字段 → 保持用户当前材质不动（不要顺手清成默认）。
            record_materials = record.get("materials")
            # 材质锁定：用户手调的那套要留住，切主题不覆盖（lock 由面板「跟随主题」开关写）。
            locked_materials = _materials.normalize_materials(
                palette.get("materials")
            )[0]
            material_applied = False
            # 只带开关的 materials（内置「非材质版」主题就是 {"enabled": false}）按补丁处理：
            # 关掉高级材质，但保留用户手调的五节参数 —— 重新打开不该回到出厂默认。
            material_patch = (
                isinstance(record_materials, dict)
                and set(record_materials) <= {"enabled", "lock", "version"}
            )
            if isinstance(record_materials, dict) and locked_materials.get("lock"):
                self._log(
                    "info",
                    f"材质已锁定，保留当前设置（主题 {record.get('name') or theme_id} 的材质未应用）",
                )
            elif material_patch:
                (
                    merged_materials,
                    material_warnings,
                ) = _materials.apply_materials(locked_materials, record_materials)
                palette["materials"] = merged_materials
                material_applied = True
                for message in material_warnings:
                    self._log("warning", f"主题材质设置被修正 —— {message}")
            elif isinstance(record_materials, dict):
                (
                    imported_materials,
                    material_warnings,
                ) = _materials.normalize_materials(record_materials)
                # 预设库是素材库，不是「这一套参数」的一部分：应用条目时按名合并，
                # 本地独有的自定义预设必须留下。整份替换的话，用户点一次
                # 「应用一套带材质的历史主题」就把自己存的预设全清空了。
                merged_custom = dict(locked_materials.get("custom_presets") or {})
                merged_custom.update(imported_materials.get("custom_presets") or {})
                imported_materials["custom_presets"] = merged_custom
                raw_preset = str(imported_materials.get("preset") or "").strip()
                if (
                    raw_preset
                    and raw_preset not in _materials.PRESET_IDS
                    and raw_preset in merged_custom
                ):
                    imported_materials["preset"] = raw_preset
                palette["materials"] = imported_materials
                material_applied = True
                for message in material_warnings:
                    self._log("warning", f"主题材质设置被修正 —— {message}")
            palette["enabled"] = True
            palette["updated_at"] = _now_iso()
            palette["schema"] = SCHEMA_VERSION
            self._write_unlocked(palette)
        return {
            "theme": self._theme_summary(
                record,
                light=len(clean_vars),
                dark=len(clean_dark),
                has_image=bool(
                    self._image_refs(clean_vars) or self._image_refs(clean_dark)
                ),
            ),
            "light": len(clean_vars),
            "dark": len(clean_dark),
            "rejected": errors,
            # 面板据此决定 toast 文案：主题自带材质被应用（True）/ 材质锁定被跳过（False）。
            "material_applied": material_applied,
            "material_present": isinstance(record_materials, dict),
            "material_locked": bool(locked_materials.get("lock")),
            # 应用后高级材质是开还是关：面板据此把「材质已换」和「材质已关闭」说清楚。
            "material_enabled": bool(
                (palette.get("materials") or {}).get("enabled")
                if isinstance(palette.get("materials"), dict)
                else False
            ),
        }

    async def apply_saved_theme(self, theme_id: Any = "", id: Any = "", **_):
        theme_id = theme_id or id
        try:
            outcome = self._apply_theme_record(theme_id)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"切换主题失败：{exc}"))
        rejected = outcome["rejected"]
        if rejected:
            self._log("warning", f"apply_saved_theme 拒绝 {len(rejected)} 条：{rejected[:3]}")
        return Ok(
            {
                "applied": True,
                "theme": outcome["theme"],
                "light": outcome["light"],
                "dark": outcome["dark"],
                "rejected": rejected,
                "material_applied": outcome.get("material_applied", False),
                "material_present": outcome.get("material_present", False),
                "material_locked": outcome.get("material_locked", False),
                "material_enabled": outcome.get("material_enabled", False),
            }
        )

    @plugin_entry(
        id="delete_theme",
        name=tr("entries.delete_theme.name", default="删除仓库主题"),
        description=tr(
            "entries.delete_theme.description",
            default="从主题仓库删除一个条目（不影响当前正在使用的调色板）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                # 面板发的是 id（更自然），CLI/脚本里常用 theme_id。两个都收，
                # 避免调用方猜参数名 —— 曾因只认 theme_id 导致面板「应用/分享/删除」
                # 全部报「条目 id 非法」。
                "id": {"type": "string", "maxLength": 64},
            },
        },
    )
    async def delete_theme(self, theme_id: Any = "", id: Any = "", **_):
        theme_id = theme_id or id
        try:
            record = self._load_library_item(theme_id)
            # 用**请求里的 id** 判断/定位：文件名才是仓库主键（_library_items 也按文件名），
            # 记录里的 "id" 字段只是文件内容，手改过就会指向另一套主题。
            requested = str(theme_id or "").strip()
            if not RE_THEME_ID.match(requested):
                raise SdkError(f"仓库条目 id 非法，已拒绝删除：{requested!r}")
            if self._preset_path(requested).is_file():
                raise SdkError(
                    "内置主题不能删除（它随插件一起发布）。"
                    "想留下改动过的版本：先点「应用」，再「保存到仓库」另存一份。"
                )
            directory = self._library_dir().resolve()
            path = (directory / f"{requested}.json").resolve()
            if path.parent != directory:
                raise SdkError("仓库条目路径不在主题仓库目录内，已拒绝删除。")
            if not path.is_file():
                raise SdkError(f"主题仓库里没有这个主题：{requested}")
            path.unlink(missing_ok=True)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"删除仓库主题失败：{exc}"))
        return Ok({"deleted": True, "theme_id": str(theme_id)})

    @plugin_entry(
        id="update_theme_meta",
        name=tr("entries.update_theme_meta.name", default="修改主题信息"),
        description=tr(
            "entries.update_theme_meta.description",
            default="改仓库里一套主题的名称 / 备注 / 标签。条目 id 不变（收藏与按时段排程都靠它认主题）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                "id": {"type": "string", "maxLength": 64},
                "name": {"type": "string", "maxLength": 60},
                "note": {"type": "string", "maxLength": 120},
                "tags": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 12},
                    "maxItems": 4,
                },
            },
        },
    )
    async def update_theme_meta(
        self,
        theme_id: Any = "",
        id: Any = "",
        name: Any = None,
        note: Any = None,
        tags: Any = None,
        **_,
    ):
        """只改「名字 / 备注 / 标签」，**绝不改 id、vars、dark_vars、saved_at**。

        为什么 id 不能跟着名字变：id 是收藏（面板 localStorage）、按时段排程
        （schedule.json）、主题库备份包 manifest 共同引用的键。改名顺手改 id
        会让「收藏的那套」和「排程里选的那套」变成悬空引用 —— 用户看到的是
        「我明明收藏了，怎么没了」和「排程里选的主题不存在」。所以 id 一旦
        生成就固定，改名只动显示用的字段。
        """
        theme_id = theme_id or id
        if name is None and note is None and tags is None:
            return Err(SdkError("没有要修改的内容（name / note / tags 都没给）。"))
        try:
            record = self._load_library_item(theme_id)
            # 同删除入口：文件名才是主键，记录里的 "id" 只是内容。
            record_id = str(theme_id or "").strip()
            if not RE_THEME_ID.match(record_id):
                raise SdkError(f"仓库条目 id 非法，已拒绝修改：{record_id!r}")
            if self._preset_path(record_id).is_file():
                raise SdkError(
                    "内置主题不能改名/改备注（它随插件一起发布）。"
                    "想改就用「应用」+「保存到仓库」另存一份，再改那一份。"
                )
            directory = self._library_dir().resolve()
            path = (directory / f"{record_id}.json").resolve()
            if path.parent != directory:
                raise SdkError("仓库条目路径不在主题仓库目录内，已拒绝修改。")
            if not path.is_file():
                raise SdkError(f"主题仓库里没有这个主题：{record_id}")

            payload = dict(record)
            changed = []

            if name is not None:
                if not isinstance(name, str) or not name.strip():
                    raise SdkError("主题名称不能为空。")
                label = _clip_name(name, DEFAULT_THEME_NAME)
                if label != str(payload.get("name") or ""):
                    changed.append("name")
                payload["name"] = label

            if note is not None:
                clean_note = _theme_note(note)
                if clean_note:
                    if clean_note != str(payload.get("note") or ""):
                        changed.append("note")
                    payload["note"] = clean_note
                else:
                    # 传空字符串 = 清掉备注；条目里不留空字段，面板读到的就是「没有备注」。
                    if "note" in payload:
                        changed.append("note")
                    payload.pop("note", None)

            if tags is not None:
                if isinstance(tags, str):
                    # 面板发数组；CLI 里手写「暖色,渐变」也接受，省得调用方猜格式。
                    tags = [part for part in re.split(r"[,，、;；\s]+", tags) if part]
                clean_tags = _theme_tags(tags)
                if clean_tags:
                    if clean_tags != list(payload.get("tags") or []):
                        changed.append("tags")
                    payload["tags"] = clean_tags
                else:
                    if "tags" in payload:
                        changed.append("tags")
                    payload.pop("tags", None)

            if not changed:
                return Ok(
                    {
                        "updated": False,
                        "changed": [],
                        "theme": self._theme_summary(
                            payload,
                            light=len(payload.get("vars") or {}),
                            dark=len(payload.get("dark_vars") or {}),
                            has_image=bool(
                                self._image_refs(payload.get("vars") or {})
                                or self._image_refs(payload.get("dark_vars") or {})
                            ),
                        ),
                    }
                )

            with _WRITE_LOCK:
                _write_json_atomic(path, payload)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"修改主题信息失败：{exc}"))

        self._log(
            "info",
            f"主题 {record_id} 已更新：{', '.join(changed)} → {payload.get('name')}",
        )
        return Ok(
            {
                "updated": True,
                "changed": changed,
                "theme_id": record_id,
                "name": payload.get("name"),
                "note": payload.get("note") or "",
                "tags": list(payload.get("tags") or []),
            }
        )

    @plugin_entry(
        id="import_theme_archive",
        name=tr("entries.import_theme_archive.name", default="导入主题分享包"),
        description=tr(
            "entries.import_theme_archive.description",
            default="导入主题分享包（zip：theme.json + images/，也接受裸 JSON），存进仓库。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "maxLength": 200},
                "data": {"type": "string", "description": "分享包内容的 base64"},
            },
            "required": ["data"],
        },
    )
    async def import_theme_archive(self, filename: str = "", data: str = "", **_):
        if not isinstance(data, str) or not data.strip():
            return Err(SdkError("分享包内容为空。"))
        raw = data.strip()
        if len(raw) > ARCHIVE_MAX_BYTES * 2:
            return Err(SdkError("分享包内容过大（base64 超过上限）。"))
        if raw.startswith("data:"):
            # 容忍 data URL 前缀（面板正常会剥掉，手工调用可能带）。
            _, _, raw = raw.partition(",")
        try:
            payload = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001 — binascii.Error 是 ValueError 的子类
            return Err(SdkError("分享包内容不是合法的 base64。"))
        label = f"来自 {filename or '分享包'}"
        try:
            theme, source_format = self._parse_archive(payload, label=label)
            clean_vars, clean_dark, errors = _validate_palette(
                theme.get("vars"), theme.get("dark_vars")
            )
            # 别人的 JSON 里常带着「他本机的背景图地址」（旧插件时代是
            # http://127.0.0.1:48916/plugin/.../backgrounds/...，现在是 /api/theme_studio/...），
            # 到了本机就是 404 引用。落库前先丢掉，条目上也就不会谎报「含背景图」。
            clean_vars, gone_light = self._without_missing_images(clean_vars)
            clean_dark, gone_dark = self._without_missing_images(clean_dark)
            for gone in (gone_light, gone_dark):
                for key in gone:
                    errors = list(errors) + [
                        f"{key}: 引用的背景图在本机不存在，已回退原生"
                    ]
            if not clean_vars and not clean_dark:
                raise SdkError(
                    "分享包里没有可用的调色板变量（可能不是主题工坊的分享包）。"
                )
            record = self._save_library_item(
                name=theme.get("name"),
                vars_map=clean_vars,
                dark_map=clean_dark,
                note=theme.get("note"),
                tags=theme.get("tags"),
                materials=theme.get("materials"),
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"导入主题分享包失败：{exc}"))
        if errors:
            self._log(
                "warning", f"import_theme_archive 拒绝 {len(errors)} 条：{errors[:3]}"
            )
        summary = self._theme_summary(
            record,
            light=len(clean_vars),
            dark=len(clean_dark),
            has_image=bool(
                self._image_refs(clean_vars) or self._image_refs(clean_dark)
            ),
        )
        return Ok(
            {
                "imported": True,
                "theme": summary,
                "source_format": source_format,
                "rejected": errors,
            }
        )

    @plugin_entry(
        id="export_theme_archive",
        name=tr("entries.export_theme_archive.name", default="导出主题分享包"),
        description=tr(
            "entries.export_theme_archive.description",
            default="把仓库条目打成 zip 分享包（theme.json + images/），返回 base64 供面板下载。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "maxLength": 64},
                # 面板发的是 id（更自然），CLI/脚本里常用 theme_id。两个都收，
                # 避免调用方猜参数名 —— 曾因只认 theme_id 导致面板「应用/分享/删除」
                # 全部报「条目 id 非法」。
                "id": {"type": "string", "maxLength": 64},
            },
        },
    )
    async def export_theme_archive(self, theme_id: Any = "", id: Any = "", **_):
        theme_id = theme_id or id
        try:
            record = self._load_library_item(theme_id)
            local = self._rewrite_archive_refs(
                record,
                {},
                label=f"主题 {record.get('name') or theme_id}",
                from_presets=True,
            )
            record = dict(record)
            record["vars"] = local["vars"]
            record["dark_vars"] = local["dark_vars"]
            record["name"] = local["name"]
            clean_vars, clean_dark, _errors = _validate_palette(
                record.get("vars"), record.get("dark_vars")
            )
            name = _clip_name(record.get("name"), DEFAULT_THEME_NAME)
            filename, payload = self._build_archive(
                name=name,
                vars_map=clean_vars,
                dark_map=clean_dark,
                note=record.get("note"),
                tags=record.get("tags"),
                materials=local.get("materials"),
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"导出主题分享包失败：{exc}"))
        images = len(self._image_refs(clean_vars)) + len(self._image_refs(clean_dark))
        return Ok(
            {
                "filename": filename,
                "data": base64.b64encode(payload).decode("ascii"),
                "bytes": len(payload),
                "images": images,
            }
        )

    def _export_dir(self, target: str) -> Path:
        """导出目标目录。面板跑在 sandbox iframe 里，<a download> 会被静默丢弃
        （宿主 sandbox 串里没有 allow-downloads），所以文件由插件进程直接写盘。"""
        home = Path(os.path.expanduser("~"))
        if target == "downloads":
            return home / "Downloads"
        if target == "plugin":
            out = self.data_dir / "exports"
            out.mkdir(parents=True, exist_ok=True)
            return out
        # desktop：OneDrive 重定向时 ~/Desktop 可能不存在，回落 ~/OneDrive/Desktop
        for cand in (home / "Desktop", home / "OneDrive" / "Desktop", home):
            if cand.is_dir():
                return cand
        return home

    def _unique_path(self, directory: Path, filename: str) -> Path:
        """重名不覆盖，追加 -2 / -3 …；文件名只保留安全字符。"""
        stem, suffix = os.path.splitext(_safe_archive_name(filename))
        if not suffix:
            # ``_safe_archive_name`` 会把名字截到 40 字符 —— 长文件名（例如
            # ``neko-theme-studio-library-20261119095928.zip``）会连 ``.zip`` 一起
            # 被截掉，导出成一个没有扩展名的文件，双击打不开、压缩软件也不认。
            # 截断丢掉的扩展名在这里按白名单补回来。
            raw_suffix = os.path.splitext(str(filename))[1].lower()
            if raw_suffix in (".zip", ".json", ".png", ".jpg", ".jpeg", ".webp", ".gif"):
                suffix = raw_suffix
        stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", stem).strip("-") or "theme"
        candidate = directory / (stem + suffix)
        n = 2
        while candidate.exists():
            candidate = directory / (f"{stem}-{n}{suffix}")
            n += 1
            if n > 500:
                raise SdkError("导出失败：同名文件太多，请先清理目录。")
        return candidate

    def _current_or_saved(self, theme_id: str):
        """theme_id 为空用当前调色板，否则用仓库/内置条目。

        返回 (name, vars, dark, materials)。材质必须一起带出来 ——
        导出成文件时只写颜色不写材质，用户拿到的是「半套主题」。
        """
        if theme_id:
            record = self._load_library_item(theme_id)
            local = self._rewrite_archive_refs(
                record,
                {},
                label=f"主题 {record.get('name') or theme_id}",
                from_presets=True,
            )
            clean_vars, clean_dark, _errors = _validate_palette(
                local.get("vars"), local.get("dark_vars")
            )
            return (
                _clip_name(local.get("name"), DEFAULT_THEME_NAME),
                clean_vars,
                clean_dark,
                local.get("materials"),
            )
        palette = self._normalize(self._read_unlocked())
        clean_vars, clean_dark, _errors = _validate_palette(
            palette.get("vars"), palette.get("dark_vars")
        )
        return (
            _clip_name(palette.get("name"), DEFAULT_THEME_NAME),
            clean_vars,
            clean_dark,
            palette.get("materials"),
        )

    @plugin_entry(
        id="import_library",
        name=tr("entries.import_library.name", default="恢复主题库备份"),
        description=tr(
            "entries.import_library.description",
            default="导入主题库备份包（zip：themes/*.json + images/ + manifest.json），并进仓库。只新增，不覆盖现有主题。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "maxLength": 200},
                "data": {"type": "string", "description": "备份包内容的 base64"},
            },
            "required": ["data"],
        },
    )
    async def import_library(self, filename: str = "", data: str = "", **_):
        if not isinstance(data, str) or not data.strip():
            return Err(SdkError("备份包内容为空。"))
        raw = data.strip()
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")
        if len(raw) > ARCHIVE_MAX_BYTES * 2:
            return Err(SdkError("备份包过大（base64 超过上限）。"))
        try:
            payload = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            return Err(SdkError("备份包内容不是合法的 base64。"))
        try:
            items, images = self._parse_library_archive(payload)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"解析备份包失败：{exc}"))

        imported = []
        skipped = []
        for item in items:
            if "error" in item:
                skipped.append({"name": item.get("path") or "未知条目", "reason": item["error"]})
                continue
            theme = item["theme"]
            label = _clip_name(theme.get("name"), DEFAULT_THEME_NAME) or item.get("path") or "主题"
            try:
                local = self._rewrite_archive_refs(
                    theme, images, label=f"主题库备份 · {label}"
                )
                clean_vars, clean_dark, _errors = _validate_palette(
                    local.get("vars"), local.get("dark_vars")
                )
                if not clean_vars and not clean_dark:
                    raise SdkError("这套主题里没有可用的调色板变量。")
                record = self._save_library_item(
                    name=label,
                    vars_map=clean_vars,
                    dark_map=clean_dark,
                    note=local.get("note"),
                    tags=local.get("tags"),
                    materials=local.get("materials"),
                )
            except SdkError as exc:
                skipped.append({"name": label, "reason": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001
                skipped.append({"name": label, "reason": f"导入失败：{exc}"})
                continue
            imported.append(
                self._theme_summary(
                    record,
                    light=len(record["vars"]),
                    dark=len(record["dark_vars"]),
                    has_image=bool(
                        self._image_refs(record["vars"])
                        or self._image_refs(record["dark_vars"])
                    ),
                )
            )

        if not imported:
            reason = skipped[0]["reason"] if skipped else "包里没有可导入的主题"
            return Err(SdkError(f"备份包里的主题都没能导入：{reason}"))
        self._log(
            "info",
            f"已恢复主题库备份 {filename or '（无文件名）'}："
            f"{len(imported)} 套成功，{len(skipped)} 套跳过，{len(images)} 张图片",
        )
        return Ok(
            {
                "imported": len(imported),
                "themes": imported,
                "skipped": skipped,
                "images": len(images),
                "filename": filename,
            }
        )

    @plugin_entry(
        id="export_to_file",
        name=tr("entries.export_to_file.name", default="导出到文件"),
        description=tr(
            "entries.export_to_file.description",
            default="把当前主题或仓库条目写成文件（桌面 / 下载 / 插件 exports/），返回完整路径。面板在 iframe 里无法触发浏览器下载时用这条。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["zip", "json", "library"],
                    "description": "zip/json = 一套主题；library = 库里所有自定义主题打成一个备份包",
                },
                "theme_id": {"type": "string", "maxLength": 64},
                "id": {"type": "string", "maxLength": 64},
                "target": {"type": "string", "enum": ["desktop", "downloads", "plugin"]},
                "name": {"type": "string", "maxLength": 60},
            },
        },
    )
    async def export_to_file(
        self,
        kind: str = "zip",
        theme_id: Any = "",
        id: Any = "",
        target: str = "desktop",
        name: str = "",
        **_,
    ):
        theme_id = theme_id or id or ""
        # 参数类型守卫：面板总是送字符串，但入口是公开的（宿主/脚本都能调）。
        # 以前直接 .strip()，非字符串会抛 AttributeError 变成 500 —— 报错应该是 Err，
        # 不是未捕获异常。
        for _label, _raw in (("kind", kind), ("target", target), ("name", name)):
            if _raw is not None and not isinstance(_raw, str):
                return Err(
                    SdkError(
                        f"导出参数 {_label} 必须是字符串，收到 {type(_raw).__name__}。"
                    )
                )
        kind = (kind or "zip").strip().lower()
        target = (target or "desktop").strip().lower()
        if kind not in ("zip", "json", "library"):
            return Err(
                SdkError(f"不支持的导出格式：{kind}（只能是 zip / json / library）。")
            )
        if target not in ("desktop", "downloads", "plugin"):
            return Err(SdkError(f"不支持的导出位置：{target}（只能是 desktop / downloads / plugin）。"))
        if kind == "library":
            # 库备份不需要「当前主题」：一个包里是仓库里所有自定义主题 + 共用图片。
            try:
                filename, payload, count = self._build_library_archive()
            except SdkError as exc:
                return Err(exc)
            except Exception as exc:  # noqa: BLE001
                return Err(SdkError(f"备份主题库失败：{exc}"))
            try:
                directory = self._export_dir(target)
                path = self._unique_path(directory, filename)
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_bytes(payload)
                os.replace(tmp, path)
            except Exception as exc:  # noqa: BLE001
                return Err(SdkError(f"写入文件失败：{exc}"))
            self._log("info", f"已备份主题库到 {path}（{count} 套 / {len(payload)} 字节）")
            return Ok(
                {
                    "path": str(path),
                    "filename": path.name,
                    "bytes": len(payload),
                    "kind": kind,
                    "target": target,
                    "themes": count,
                }
            )

        try:
            theme_name, vars_map, dark_map, materials_map = self._current_or_saved(
                str(theme_id).strip()
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"读取主题失败：{exc}"))
        if name and name.strip():
            theme_name = _clip_name(name.strip(), theme_name)

        try:
            if kind == "zip":
                filename, payload = self._build_archive(
                    name=theme_name,
                    vars_map=vars_map,
                    dark_map=dark_map,
                    materials=materials_map,
                )
            else:
                doc = {
                    "schema": SCHEMA_VERSION,
                    "name": theme_name,
                    "enabled": True,
                    "updated_at": _now_iso(),
                    "vars": vars_map,
                    "dark_vars": dark_map,
                    "materials": _materials.normalize_materials(materials_map)[0],
                }
                payload = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
                filename = f"{_safe_archive_name(theme_name)}.json"
            directory = self._export_dir(target)
            path = self._unique_path(directory, filename)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(payload)
            os.replace(tmp, path)
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"写入文件失败：{exc}"))

        result = {
            "path": str(path),
            "filename": path.name,
            "bytes": len(payload),
            "kind": kind,
            "target": target,
            "images": len(self._image_refs(vars_map)) + len(self._image_refs(dark_map)),
        }
        if target == "plugin":
            result["url"] = f"{API_BASE}/exports/{urllib.parse.quote(path.name)}"
        self._log("info", f"导出到文件 {path}（{len(payload)} 字节）")
        return Ok(result)

    @plugin_entry(
        id="import_background",
        name=tr("entries.import_background.name", default="上传聊天背景图"),
        description=tr(
            "entries.import_background.description",
            default="保存背景图到插件 static/backgrounds/，返回可写进 --nts-chat-bg-image 的绝对 URL。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "maxLength": 200},
                "data": {"type": "string", "description": "图片内容的 base64"},
            },
            "required": ["data"],
        },
    )
    async def import_background(self, filename: str = "", data: str = "", **_):
        if not isinstance(data, str) or not data.strip():
            return Err(SdkError("图片内容为空。"))
        raw = data.strip()
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")
        if len(raw) > IMAGE_MAX_BYTES * 2:
            return Err(SdkError("图片过大（base64 超过上限）。"))
        try:
            payload = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            return Err(SdkError("图片内容不是合法的 base64。"))
        try:
            saved = self._save_background_bytes(
                payload, label=f"来自 {filename or '面板'}"
            )
        except SdkError as exc:
            return Err(exc)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"保存背景图失败：{exc}"))
        return Ok(
            {
                "url": self._background_url(saved),
                "filename": saved,
                "bytes": len(payload),
                "kind": _image_extension(payload) or "unknown",
            }
        )
