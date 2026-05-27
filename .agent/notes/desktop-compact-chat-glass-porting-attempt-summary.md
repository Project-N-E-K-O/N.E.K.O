# 桌面端紧凑聊天框毛玻璃迁移尝试总结

本文记录本轮对话中，尝试把网页端紧凑聊天框的半透明毛玻璃背景效果迁移到桌面端的实际经验、失败点和可保留结论。代码修改随后已按要求撤销，本文只作为后续继续时的上下文记录。

## 原始目标

- 一比一参考网页端紧凑聊天框的背景相关效果。
- 桌面端显示效果要接近网页端：半透明、毛玻璃、可透出并模糊背后的模型和桌面背景。
- 三端都要可用：网页端作为基准不能被破坏，桌面端至少要同时考虑 macOS 和 Windows；如果 Linux/Wayland 也在运行范围内，需要有不会黑屏/大框/遮挡交互的降级方案。
- 不影响紧凑聊天框已有功能：拖动、锚点、历史面板、选项、工具扇、点击穿透、球按钮、窗口定位。
- 必须实际启动桌面端、截图比对，不允许只凭代码判断完成。
- 不能只盯 macOS。macOS 的 `vibrancy` 只可作为参考路径之一，不能替代 Windows acrylic / DWM 路径，也不能在已知会发黑或变灰的情况下继续当作完成方案。

## 必须遵守的项目约束

- 先读 `.agent/notes/project-handoff-notes.md`。
- 不碰用户已有 `.agent` 脏文档，除非用户明确要求。
- 网页端紧凑聊天框真实实现链路在 React chat，不是旧的 `#chat-container`。
- 紧凑几何 contract 依赖 `data-compact-geometry-owner/item`；历史、选项、工具扇不能改变 base surface anchor。
- 修改 `frontend/react-neko-chat/src/*` 后才需要跑 `bash build_frontend.sh`。本轮没有修改该目录。
- NEKO 与 NEKO-PC 是两个仓库，网页端代码在 `N.E.K.O`，桌面端代码在 `N.E.K.O.-PC`。

## 网页端参考参数

网页端参考来自 `frontend/react-neko-chat/src/styles.css` 的 `.compact-chat-surface-frame`：

```css
height: 54px;
border: 1px solid rgba(255, 255, 255, 0.6);
border-radius: 999px;
background: rgba(255, 255, 255, 0.06);
box-shadow:
  0 0 0 1px rgba(208, 233, 255, 0.22),
  0 8px 16px rgba(8, 22, 40, 0.03);
backdrop-filter: blur(8px) saturate(1.1) brightness(1.08);
-webkit-backdrop-filter: blur(8px) saturate(1.1) brightness(1.08);
```

深色主题下也保持同一组背景、边框、阴影参数。

## 网页端多层实现要点

网页端不是“单个透明背景色”实现。后续复刻必须把它当成至少三层效果叠加，而不是只抄一个 `background` 或只抄一个 `backdrop-filter`。

1. 背景采样层：
   - `.compact-chat-surface-frame` 自身 `overflow: hidden`、`isolation: isolate`、`border-radius: 999px`。
   - `backdrop-filter: blur(8px) saturate(1.1) brightness(1.08)` 采样同一页面合成背景。
   - 这是网页端能自然模糊模型/背景的关键，因为它处在同一个浏览器合成上下文里。

2. 透明白覆盖层：
   - `background: rgba(255, 255, 255, 0.06)`。
   - 这层非常薄，不能擅自加到 `0.18`、`0.3` 之类，否则会变成灰白块，丢掉网页端透明感。

3. 边框与光晕层：
   - `border: 1px solid rgba(255, 255, 255, 0.6)`。
   - `box-shadow: 0 0 0 1px rgba(208, 233, 255, 0.22), 0 8px 16px rgba(8, 22, 40, 0.03)`。
   - 这层负责胶囊边缘的浅色轮廓和极轻阴影，不应扩大成外层方形框。

4. 内容层：
   - `.compact-chat-surface-frame > * { position: relative; z-index: 2; }`。
   - 文本、按钮、输入框在玻璃层之上。
   - 复刻时不能让独立材质层遮住内容，也不能让内容跟着材质层变灰发虚。

5. 禁用伪元素层：
   - `.compact-chat-surface-frame::before` 和 `::after` 当前为 `content: none`。
   - 因此本轮网页端基准不是依赖额外渐变伪元素，而是主框的背景采样、透明覆盖、边框、阴影共同形成。

桌面端失败的核心原因之一，就是多次只复刻了某一层：有时只复刻前景 CSS，有时只做系统材质，有时只截模型背景。真正目标必须把“完整背景采样 + 轻薄白覆盖 + 边框/光晕 + 内容层”一起对齐。

## 正确实施细节要求

### 三端范围

1. 网页端：
   - 只作为视觉基准，不应为了桌面端效果破坏网页端当前 CSS。
   - 参考必须来自实际 Project N.E.K.O 页面中的 React compact chat，而不是旧容器、错误页面、其他 Chrome 标签页或单独 mock。

2. macOS 桌面端：
   - 不能只用 `vibrancy` 后看到灰黑块就宣布完成。
   - 如果用系统材质，必须实际截图确认没有黑色、灰色厚块、外层大框。
   - 如果系统材质无法接近网页端，应转向系统合成截图/ScreenCaptureKit 等可控背景采样方案，而不是继续调 macOS material 名称。
   - Apple 官方 `NSVisualEffectView` 的要点是 `material + blendingMode + state + mask`。后续如果参考 Apple 实现，必须关注 blending mode 是 behind-window 还是 within-window，不能只换 material 名称。

3. Windows 桌面端：
   - 不能只在 macOS 上写 `backgroundMaterial: 'acrylic'` 就算适配。
   - 需要在 Windows 实机或可信 Windows 环境实际运行截图确认：透明度、模糊、点击穿透、拖动、锚点都正常。
   - Windows acrylic/DWM 的颜色混合由系统控制，必须单独调前景覆盖层，不可假设与 macOS 相同。

4. Linux/Wayland/X11 降级：
   - 若目标包含 Linux，必须明确降级策略：不应出现黑框、不应扩大点击区域、不应遮挡历史/工具扇。
   - 没有系统级毛玻璃能力时，允许退回网页端同参数的半透明层，但要在文档中标注不是完整桌面背景模糊。

### 视觉层结构

- 外层 Electron/React chat shell 必须透明，不允许产生大矩形背景。
- 真实可见胶囊只能贴合 `.compact-chat-surface-frame`，不能贴合整个 chat window。
- 胶囊大小应来自真实 DOM bounds，而不是硬编码：例如实测曾为 `430x54 @ (142,739)`，但实际要跟随用户 resize 和状态变化。
- 背景模糊必须覆盖完整系统合成背景：模型、网页背景、桌面壁纸、VSCode/其他后方窗口都应参与；只模糊模型不符合要求。
- 前景视觉参数必须从网页端 `.compact-chat-surface-frame` 读取并保持一致，尤其是 `background: rgba(255,255,255,0.06)`、`blur(8px)`、`saturate(1.1)`、`brightness(1.08)`、边框和阴影。

### 明确禁止的视觉结果

- 不允许出现黑不拉几的厚块、黑底、灰黑底、深色遮罩。
- 不允许出现很大的外层方形框。任何可见背景都必须缩到紧凑对话框胶囊本体大小。
- 不允许只把模型模糊了，而桌面壁纸、VSCode、其他背景窗口完全没处理。
- 不允许把网页端 `rgba(255,255,255,0.06)` 擅自加厚到明显白/灰块，再说是毛玻璃。
- 不允许用 macOS `vibrancy` 产生的黑灰系统材质替代网页端透明毛玻璃。
- 不允许材质层边缘露出矩形裁切、黑边、灰边、整窗阴影。
- 不允许截图里没有真实背景、没有网页端基准、没有同一位置模型，就宣布完成。

判定标准应以实际截图为准：如果用户肉眼看到的是黑灰块、大框、只糊模型、没有透明感，就算代码用了官方 API 也仍然失败。

### 交互约束

- 任何新增材质层必须 `ignore mouse events` 或等价点击穿透，不得改变输入框、历史、选项、工具扇的命中区域。
- 不得改变 compact base surface anchor；历史/选项/工具扇仍然只能作为 extra island 或 bounds-only 区域参与布局。
- 拖动、resize、球按钮展开/收起、minimized/full/compact 切换时，材质层必须同步显示/隐藏/移动。
- 不能用周期性 `moveTop` 或高频截图制造抖动、性能问题或焦点异常。

### 比对流程要求

- 必须先截图网页端真实基准，再截图桌面端同状态结果。
- 两边必须尽量统一：同一模型位置、同一背景、同一 compact state、同一输入/历史状态。
- 每次截图后必须实际查看图片，不能只看文件生成成功。
- 如果截图是错误页、其他网页、空白页、无背景页，就不能用于判断。
- 不允许只看桌面端一张图后说“接近网页端”。

## 尝试过的方案

### 方案 A：只在桌面 React chat 壳层加 CSS 复刻

做法：
- 在 `templates/chat.html` 中隐藏桌面端外层 React shell 的边框、阴影和背景。
- 对 `.compact-chat-surface-frame` 加网页端同款边框、圆角、半透明背景、`backdrop-filter`。

实际结果：
- 用户反馈“第一次实现除了外面的大框，其余都很符合要求”。
- 主要问题是外面出现了很大的方形框，说明外层窗口/shell 仍然参与了视觉背景。

经验：
- 这个方向前景参数接近网页端。
- 必须避免外层 shell 或 BrowserWindow 自身形成大矩形背景。

### 方案 B：额外创建透明材质窗口，窗口大小贴合紧凑框

做法：
- 在 NEKO-PC 增加 `COMPACT_CHAT_MATERIAL_CHANNELS`。
- `preload-chat-react.js` 从 `.compact-chat-surface-frame.getBoundingClientRect()` 计算屏幕坐标。
- `main.js` 收到 show/hide 后，由 `window-manager.js` 创建一个 `N.E.K.O Compact Material` 窗口。
- 窗口大小为紧凑框本体大小，例如实测 `430x54 @ (142,739)`，解决了“大框”不是窗口尺寸的问题。

实际截图结论：
- `/tmp/neko-current-desktop-crop-2.png` 显示材质层已是紧凑框大小。
- 但背景没有完整处理，只看到模型区域有轻微处理，右侧 VSCode/桌面背景几乎不被模糊。

经验：
- bounds 跟随 `.compact-chat-surface-frame` 是对的。
- 只解决窗口尺寸，不等于解决系统背景模糊。

### 方案 C：对材质窗口内部使用 CSS `backdrop-filter`

做法：
- 透明 BrowserWindow 内放一个圆角胶囊 div。
- div 使用 `backdrop-filter: blur(8px) saturate(1.1) brightness(1.08)`。

实际结果：
- 对普通网页内容可能成立，但对独立透明 Electron BrowserWindow 后面的系统合成画面不可靠。
- 实测右侧 VSCode、壁纸等桌面背景没有得到稳定同等模糊。

经验：
- CSS `backdrop-filter` 在独立透明桌面窗口里不能等价于网页端同一 DOM 树内的背景采样。
- 这条路会造成“看起来只是叠了一层白/灰”，不是完整半透明毛玻璃。

### 方案 D：截取 Pet 窗口画面作为材质背景

做法：
- 用 `petWindow.webContents.capturePage()` 截取紧凑框背后的 Pet 窗口区域。
- 把截图放到材质窗口里，再对图片做 blur/saturate/brightness。

实际结果：
- 可以处理模型和 Pet 窗口内的背景。
- 但用户指出“你只模糊了模型。其他包括背景都没有处理”，这是准确的。
- 因为该方案只采样 Pet window，不采样系统合成后的桌面、VSCode、其他窗口。

经验：
- 这条路天然缺少桌面级背景，不适合最终目标。
- 如果继续走截图路线，必须使用系统级屏幕捕获并排除自身窗口；普通 `capturePage` 不够。

### 方案 E：Electron 原生系统材质

做法：
- macOS 尝试 `BrowserWindow` 的 `vibrancy` / `visualEffectState`。
- Windows 方向尝试 `backgroundMaterial: 'acrylic'`。
- 前景 React 框继续使用网页端同款 `0.06` 背景、边框和阴影。

实测过程：
- `vibrancy: 'popover'`：实际截图 `/tmp/neko-native-material-crop.png` 显示过灰、不透明，透明感明显不对。
- `vibrancy: 'under-window'`：实际截图 `/tmp/neko-under-window-crop.png` 比前一版更接近系统模糊，但仍偏暗偏灰，不等于网页端像素级效果。

经验：
- 这是唯一接近“系统合成背景模糊”的官方方向。
- 但 macOS 不同 `vibrancy` material 的色调和透明度由系统控制，不会天然等于网页端 CSS 参数。
- Windows acrylic 也会由 DWM 控制，不能保证与网页端 CSS 像素级一致。
- 这次错误在于实际只验证了 macOS，没有完成 Windows 实测，也没有在已知 macOS 会变黑/变灰时及时停止这条路径。

## Apple 参考实现/教程要点

本轮口头提到“参考苹果”，但执行时没有把官方实现机制写清楚。后续若再参考 Apple，应按以下要点理解，而不是只在 Electron 里试 `vibrancy: 'popover'`、`vibrancy: 'under-window'`。

### Apple 官方机制

- Apple AppKit 的核心类是 `NSVisualEffectView`。
- 其作用是给界面添加 translucency 和 vibrancy；背景内容的半透明与模糊用于提供深度，vibrancy 用于让前景内容与背景混合后仍保持可读。
- 官方文档强调 `material`、`blendingMode`、`state` 会共同决定视觉效果；不是所有 material 都适合透明玻璃。
- 官方 HIG 的 Materials 不是让开发者随便套一个灰色材质，而是按界面语义选择材质，并保持内容可读。

### blending mode 是关键

- `behind-window`：使用窗口后方内容作为 visual effect 背景，适合需要桌面/其他窗口透出的效果。
- `within-window`：使用本窗口内容作为 visual effect 背景，适合工具栏、滚动内容等窗口内部模糊。
- 本项目桌面端想要的是“模型、桌面、VSCode 等后方合成内容都参与”，理论上更接近 behind-window 语义。
- 只在 Electron 透明窗口内部写 CSS `backdrop-filter`，更像试图做当前 webContents 内采样，无法稳定取得 behind-window 的系统合成结果。

### mask/圆角也必须处理

- Apple `NSVisualEffectView` 支持 `maskImage`。胶囊形状不能只靠外层透明窗口“看起来圆”，材质采样区域也要被圆角/遮罩限制。
- 本项目需要的是 999px 胶囊，不是整块矩形材质；任何外层矩形阴影、灰底、黑底都算失败。

### 前景内容不要直接 vibrancy 化

- Apple 文档提示 custom view 要显式处理 vibrancy，且不建议在父层随意开启，因为子视图可能继承后变得不正确。
- 对本项目来说，玻璃材质层和 React 内容层应分离：材质只处理背景，文本/按钮保持网页端可读性，不要被系统 vibrancy 染色到发灰。

### Electron 映射限制

- Electron 提供 macOS `vibrancy` / `setVibrancy(type)`，可选 `titlebar`、`selection`、`menu`、`popover`、`sidebar`、`header`、`sheet`、`window`、`hud`、`fullscreen-ui`、`tooltip`、`content`、`under-window`、`under-page` 等。
- Electron 提供 Windows `backgroundMaterial` / `setBackgroundMaterial(material)`，其中 `acrylic` 是 Windows 背景材质路径。
- 这些 API 是平台系统材质入口，不是网页端 CSS `backdrop-filter` 的像素级替代品；必须分别实测 macOS/Windows。
- 如果实际截图仍黑、灰、不透，不能继续把它包装成 Apple 风格；应明确判为失败。

### Apple 和 Electron 参考链接

- Apple `NSVisualEffectView` 官方文档：https://developer.apple.com/documentation/AppKit/NSVisualEffectView
- Apple Human Interface Guidelines - Materials：https://developer.apple.com/design/human-interface-guidelines/materials
- Electron `BrowserWindow` 官方文档：https://www.electronjs.org/docs/api/browser-window

后续如果写实现方案，应把这些链接作为依据，并在方案里明确：使用系统材质只是实现“桌面背景参与模糊”的候选路径，不保证等于网页端多层 CSS。若实际截图仍黑、灰、不透，就必须停止该路径或改成系统截图采样方案。

## 截图记录与问题

本轮出现过多次截图问题，必须明确记录，避免后续继续误判。

### 有效桌面端截图观察

- `/tmp/neko-current-desktop-crop.png`、`/tmp/neko-current-desktop-crop-2.png`：
  - 证明材质窗口已经缩到紧凑框大小，不再是大窗口尺寸问题。
  - 也证明 CSS/采样路径没有完整处理背景，右侧 VSCode/桌面背景没有同等毛玻璃效果。

- `/tmp/neko-native-material-crop.png`：
  - 使用 macOS `vibrancy: 'popover'` 后截图偏灰、偏厚、不透明。
  - 不符合网页端透明毛玻璃感。

- `/tmp/neko-under-window-crop.png`：
  - 使用 macOS `vibrancy: 'under-window'` 后比 `popover` 透一点，但仍偏暗偏灰。
  - 仍不能视作完成，也不能代表 Windows。

这些路径是临时截图路径，只作为本轮观察记录；后续继续时应重新生成新的对比截图。

### 截图方法问题

- 曾截到 Chrome 的 Gemini 页面，不能作为网页端参考。
- 曾截到错误提示页或无实际模型背景的页面，也不能作为参考。
- 曾用 Playwright 等 `networkidle`，导致长时间等待，看起来像卡死。
- 曾只截图桌面端，然后主观判断接近网页端，没有同时放网页端真实基准。
- 曾没有统一模型位置和背景，导致所谓像素差比对没有意义。

### 后续截图规范

- 网页端截图前必须确认：
  - URL 是 `http://localhost:48911/` 或实际 Project N.E.K.O 页面。
  - 页面标题/DOM 能证明是 Project N.E.K.O，不是 Gemini、错误页、空白页。
  - `.compact-chat-surface-frame` 存在，且处于 compact input/default 目标状态。

- 桌面端截图前必须确认：
  - NEKO-PC 已重启并加载当前代码。
  - 窗口列表里有 React Chat、Pet window，以及如有的材质窗口。
  - 材质窗口 bounds 等于 `.compact-chat-surface-frame` bounds，不是整个 chat shell。

- 截图后必须查看：
  - 完整屏幕图。
  - 紧凑框周边 crop。
  - 必要时单窗口截图，但单窗口透明截图不能单独作为最终判断。

## 卡顿/误判原因

- 曾用 Playwright `waitUntil: 'networkidle'` 截网页参考。该项目页面有常驻连接/持续前端任务，可能长期不会进入 network idle，导致命令表现为卡死。
- 后续应使用 `domcontentloaded + 固定短等待 + 元素存在判断 + 硬超时`，不要再用 `networkidle`。
- 曾错误截到 Chrome 的 Gemini 页面或错误提示页，不能作为网页端参考。
- 曾把临时 Electron/Playwright 页面当成网页端参考，但它没有保证真实 Project N.E.K.O 状态、模型位置、背景和紧凑状态一致。
- 曾读到了网页端 CSS 参数，却没有把“多层实际效果”和运行态背景一起验证，导致桌面端实现只像参数，不像效果。
- 正确比对必须同时保证：
  - 网页端是实际 Project N.E.K.O 页面，不是其他 Chrome 标签。
  - 桌面端已重启并加载当前代码。
  - 模型、背景、紧凑框位置尽量统一，否则像素差没有意义。

## 验证过的命令

桌面端契约测试曾通过：

```bash
node --check src/window-manager.js
node --test test/desktop-compact-layout-contract.test.js
```

最后一次运行结果：
- `desktop-compact-layout-contract.test.js`：27/27 pass。

## 后续建议

1. 若继续追求三端可用：
   - 先把网页端真实基准截图固定下来。
   - macOS、Windows 分别实现并分别截图，不得只用 macOS 结果代表桌面端。
   - Linux/Wayland/X11 如果纳入范围，明确降级策略。

2. 若继续追求像素级接近网页端：
   - 需要同一背景源、同一模型位置、同一窗口位置。
   - 桌面端必须拿到系统合成后的背景图，再用与网页端一致的滤镜管线处理。
   - macOS 可能需要 ScreenCaptureKit/系统屏幕捕获并排除 chat/material 自身窗口；普通 Pet `capturePage` 不够。
   - Windows 可能需要 Desktop Duplication / DWM 相关能力或 Electron 可用的系统捕获桥，不能只依赖 CSS。

3. 不建议继续的路径：
   - 只在透明 Electron 材质窗里写 CSS `backdrop-filter`，因为它不能可靠采样整个桌面合成背景。
   - 只截 Pet window，因为它只能处理模型和 Pet 页背景，不处理桌面/其他窗口。
   - 不启动实际桌面端就判断完成。
   - 在已知 macOS `vibrancy` 发黑/发灰时继续调 material 名称并宣称接近。

## 本轮撤销范围

按用户要求，已撤销以下代码文件改动：

- `N.E.K.O/templates/chat.html`
- `N.E.K.O.-PC/src/ipc-channels.js`
- `N.E.K.O.-PC/src/main.js`
- `N.E.K.O.-PC/src/preload-chat-react.js`
- `N.E.K.O.-PC/src/window-manager.js`

保留本文档作为经验记录。撤销后 NEKO-PC 工作区应为干净；NEKO 工作区只保留用户原有 `.agent` 改动和本文档新增。
