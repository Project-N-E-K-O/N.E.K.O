# 前端视觉 / 布局问题：先量，再改

## 规则

阴影被裁、间距不对、元素点不动、resize 时跳变或抖动——这类问题不要只读代码、在脑子里算盒模型然后改 CSS 让人去验。盒模型、`overflow`、`mask`、flex 叠在一起的实际效果很反直觉，在 compact 历史区那一组问题上，纯推理连续改错了三次以上，最后是量出真实像素才一次定位。

## 怎么量

1. 把页面跑起来。两种办法：
   - 聊天组件单独看：`frontend/react-neko-chat/` 里 `npm run dev`（vite）。需要时写一个最小的 harness HTML，照真实 DOM 结构搭出来并引用真实的 `src/styles.css`。
   - 要看和主页、模型、宿主脚本的配合：起后端开主页。
2. 在浏览器里取数，不要靠肉眼：
   - `el.getBoundingClientRect()` 看真实位置和尺寸；
   - `getComputedStyle(el)` 看最终生效的属性；
   - `el.scrollHeight` / `clientHeight` 看可滚区到底有多大；
   - `document.elementFromPoint(x, y)` 看某个点上到底是谁。
3. 改之前、改之后各量一次，用数值（或截图对比）证明确实变了。「改了但没变化」通常说明改的不是生效的那条规则。
4. 页面里有常驻动画时，截图类工具可能一直等不到空闲帧而超时（实测观察）；这种情况直接用上面的取数办法。

## 踩过的反直觉点

- ⚠️ **flex / grid 列方向的滚动容器，末端（底部）padding 不计入可滚区**（Chromium 实测：`scrollHeight` 不含它）。钉到底时最下面的子元素贴着裁剪边，`box-shadow` 被 `overflow: auto` 切掉；把 padding 加在容器上还是子元素上都没用。要给滚动容器底部留出阴影的空间，滚动容器本身得是 `display: block`，里面再放一个子元素做 flex 排列。现成的例子和说明在 `frontend/react-neko-chat/src/styles.css` 的 `.compact-export-history-scroll`。
- **「看得见但点不动」多半不是层叠遮挡**。`index.html` 整页是 `pointer-events: none` 的穿透底，先看 `getComputedStyle(el).pointerEvents`（详见 `references/windows-and-runtime-gates.md` 第 6 节）。
- **合成点击不能证明可点**。`el.dispatchEvent(new MouseEvent('click'))` 会绕过 `pointer-events: none`，处理函数照样触发。验证可点性看 `pointerEvents` 的计算值加 `elementFromPoint` 的命中结果。
- **窄的预览窗口会造出假遮挡**。预览视口只有五百多像素宽时，会进手机布局，或者把模型旁边的控件默认位置算到底部的 compact 聊天胶囊上面。量之前先把视口调到和问题现场一致的宽度。
- **桌面端的穿透窗口在浏览器里复现不了**。compact 里「点上去没反应」如果只在桌面端出现，先查 hit region 有没有登记（`references/chat-surfaces.md` 第 6 节），不要在浏览器里找原因。
- **Electron 不是普通浏览器**。`frontend/react-neko-chat/README.md` 记了一条已知差异（平滑滚动在 Electron 里不生效）。滚动、动效、尺寸观察这几类路径，浏览器里通过不等于 Electron 里通过。
