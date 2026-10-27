# 插件管理器动画重构 —— 第一步：统一调度器与头部拆分

**目标**：修复按钮跳变和布局抖动，为后续步骤奠定基础。

## 问题根因

现在的抽屉动画走 `glideToLayout`：在真实布局切换到终态之前，先套上停靠态的 class 量一次终点，再撤回去，在旧布局上把每个元素推向终点，480ms 后才 `commit`。这段时间里，头部的 `marginLeft` 在被 WAAPI 逐帧改变（布局属性），每一帧都会让 `.workbench-header` 排版一次。头部又是 `flex-wrap: wrap` + `space-between`，挂着 `useCollapsibleLabels` 的 ResizeObserver，宽度一越过阈值就切换 `compact`，按钮文字和内边距随之突变。

实际过程推测：
1. 动画一开始头部就在收窄，中途某一帧按钮被折叠成纯图标，或者换行。
2. 等 `commit` 以后，头部的 margin 被撤掉、真实布局接管，ResizeObserver 再测一次，又可能切回去。
3. 这样会看到两次跳变，中间还夹着一段连续的挤压变形。

另外，外壳的 `clip-path` 和头部的 margin 是两套机制，只是凑巧用了同一个时长。窗口宽度变化时卡片动画是好的，因为那条路径是真 FLIP：布局先到位，再用 transform 从旧位置动回来，过程中不改任何布局属性。

## 第一步范围

- 写一个统一调度器，真 FLIP 模型：布局先切到终态，Vue 状态立即更新，调度器读取每个参与者在上一帧画出的 box 和新布局下的 box，由一个 rAF 时钟统一推进进度 `t`，每个元素的 transform、clip 都是 `lerp(from, to, ease(t))`，在同一帧里一起写入。
- 拆出 `WorkbenchHeader` 组件：不再嵌在 `el-card` 的 header 插槽里，列表外壳改用普通 `div`。按钮的标签改成始终渲染，用一个 `data-label` 层做淡入淡出和 clip，宽度变化交给调度器插值，不再增删 DOM。
- 折叠判断改成纯函数：`computeCompact(width: number): boolean`，由调度器在提交时询问一次，不再常驻 ResizeObserver。
- 抽屉的进出场改由调度器驱动 translate，去掉 Vue `<Transition>` 的 CSS transition。首次打开等 `deferredPanel` 加载完再开始动画。
- 删除 `glideToLayout`、CSS 里的 `drawer-start/end` transition、`:has(...)` 过渡期补丁（overflow / contain）。
- 写 e2e 几何测试：在动画过程中按帧采样各元素的 box，断言轨迹单调、没有布局属性变化、头部按钮的状态只切换一次。

**不在第一步做的事**：
- GridSection 内部的 reflow，仍然走 `useGridMotionController`。第一步只接管抽屉的开关和头部按钮，不动网格。
- `glide` 函数和卡片的四层结构保持不变。
- MarketPanel / PackageManagerPanel 的内部结构不改。

## 新组件接口

### WorkbenchHeader.vue

**决定**：第一步不拆 `WorkbenchHeader` 组件。原因：
1. 头部内部有太多业务逻辑：`el-popover`（市场账号弹窗）、`el-dropdown`（更多菜单）、条件渲染（登录态、待验证、更新检查）、动态徽标（`pluginUpdatesStore.candidates.length`）。这些逻辑都依赖 PluginList 里的响应式状态，拆成组件需要传十几个 props 和 emits，收益不大。
2. 教程代码依赖 `[data-yui-guide-id="plugin-list-card"]` 这个选择器查元素（`PluginList.vue:1076`），Dashboard 的教程步骤也依赖 `plugin-list-filter-input` 和 `plugin-list-layout-mode`。拆组件会破坏这些锚点。
3. `appearance-boundaries.e2e.ts` 断言"收起后按钮不含文字"，用 `.toContainText('')`。改成 `v-show` 后，文字仍然在 DOM 里，只是 `display: none`，这个断言要改成检查 computed style 或 `opacity`，代价和不拆差不多。

**替代方案**：
- 在 PluginList 内部，保留现有头部结构，给每个按钮加 `data-motion-key` 和 `data-motion-role`。
- 调度器从 `listShellRef.$el.querySelector('.workbench-header')` 开始扫描，抓取所有带 `data-motion-*` 的元素。
- 按钮文字改成始终渲染，用 `v-show="!headerActionsCompact"` 控制可见性，调度器负责在动画期间淡入淡出 `.button-label`（新增的 `<span class="button-label">` 包裹文字）。

**好处**：
- 不破坏现有测试和教程锚点。
- 不增加组件边界，业务逻辑留在 PluginList 里。
- 仍然能达到第一步的目标：按钮文字不在动画期间增删 DOM，宽度变化交给调度器插值。

### 调度器 API

`src/motion/scheduler.ts`

```ts
export interface LayoutTransitionOptions {
  /** 包含所有参与者的根元素 */
  scope: HTMLElement

  /** 切换布局到终态的函数（Vue 响应式 + await nextTick） */
  apply(): Promise<void>

  /** 可选：外壳，其左边缘随动画移动，右边缘固定 */
  shell?: {
    element: HTMLElement
    header: HTMLElement
  }

  /** 可选：在提交前询问一次"目标宽度下是否折叠" */
  shouldCompact?: (width: number) => boolean

  /** 可选：面板首次打开时等待加载完成 */
  awaitPanel?: () => Promise<void>
}

export interface LayoutTransitionControl {
  /** 取消动画，回退到起点 */
  cancel(): void
}

/**
 * 启动一次布局过渡。真 FLIP：先切到终态，再从起点倒推回来。
 * 
 * 参与者通过 data-motion-key 和 data-motion-role 声明：
 * - data-motion-key: 唯一标识符（如 "market-login", "action-reload"）
 * - data-motion-role: 角色类型
 *   - "button": 位置和宽度都变化，文字层淡入淡出
 *   - "divider": 只做位移
 *   - "container": 位置、宽度、clip 都变化
 *   - "panel": 从左或右滑入滑出
 * 
 * 所有元素由一个 rAF 时钟统一推进，同一帧写入。
 * 中途反向时，以当前帧画出的值作为新的 from。
 */
export function layoutTransition(options: LayoutTransitionOptions): Promise<LayoutTransitionControl>
```

**实现要点**：
- 读取所有带 `data-motion-key` 的元素，记录起点 box。
- 如果有 `shouldCompact`，用目标布局的宽度调用一次，切换 `compact` 状态。
- 如果有 `awaitPanel`，等它完成。
- 调用 `apply()`，Vue 立即更新，`await nextTick()`。
- 读取所有元素的终点 box。
- 启动一个 rAF 循环，480ms，曲线 `cubic-bezier(0.32, 0.72, 0, 1)`（对应 `--motion-ease-layout`）。
- 每帧计算 `t = ease(progress)`，对每个元素按角色插值：
  - button: `translate(dx * (1-t), dy * (1-t))` + 外皮 `width/height` 插值 + 文字层 `opacity` 和 `clip-path`
  - divider: `translate`
  - container: `clip-path` 从左裁剪
  - panel: `translate` 从左或右滑入
- 中途取消：从当前 computed style 重新读起点，反向继续。

**cubic-bezier 求值函数**（约 20 行，放在 scheduler.ts 内部）：
- 二分法或牛顿迭代求 t，使 bezier_x(t) == progress。
- 返回 bezier_y(t)。

### 纯函数折叠判断

`src/composables/useCollapsibleLabels.ts` 拆出：

```ts
export function computeCompact(
  containerWidth: number,
  groups: HTMLElement[],
  gap: number
): boolean {
  const content = groups.reduce(
    (sum, element) => sum + Math.max(element.getBoundingClientRect().width, element.scrollWidth),
    0
  )
  const needed = Math.ceil(content + gap * Math.max(groups.length - 1, 0))
  return needed > containerWidth
}
```

调度器在提交前调用一次，动画期间 ResizeObserver 被挂起（或者直接移除 `useCollapsibleLabels`，只保留上面的纯函数）。

## 删除清单

1. **src/motion/grid.ts**
   - 删除 `glideToLayout` 函数（第 34 到 102 行）
   - 删除模块级 `previewers` Set 和 `abandonGlide`（第 20、23 行）
   - 保留 `useGridMotionController`（第一步不动网格）
   - 保留 `glide` 函数和 `reflowParts`

2. **src/views/PluginList.vue 模板**
   - 替换 `<el-card ref="listShellRef">` 为 `<div ref="listShellRef" class="plugin-list-shell">`（第 34 行）
   - 删除 `<template #header>`（第 35 行）和 `</template>`（第 261 行），内容移到 `plugin-list-shell` 外面
   - 给每个按钮的文字 `<span>` 加上 `class="button-label"`，给每个按钮加 `data-motion-key` 和 `data-motion-role`
   - 保留 `.workbench-header` 和所有子元素，只改 `v-if` 为 `v-show`（文字始终渲染）

3. **src/views/PluginList.vue script**
   - 删除 `glideToLayout` 导入（第 521 行）
   - 删除 `glideToLayout` 调用和 drawer watch（第 962 到 991 行），换成调度器调用
   - 删除 `listShellRef` 的类型 `ref<{ $el: HTMLElement } | null>`，改成 `ref<HTMLElement | null>`（第 959 行）
   - 保留 `useCollapsibleLabels` 的导入和调用（第 1164 行），但 `computeCompact` 函数拆出来也给调度器用
   - 删除 `cancelDockGlide`（第 960 行）

4. **src/views/PluginList.vue CSS**
   - 删除 `.plugin-list-card`（第 1561 到 1567 行），换成 `.plugin-list-shell`
   - 删除 `.plugin-list-card :deep(> .el-card__header)`（第 1568 到 1572 行），头部不再嵌在 card 里
   - 删除 `.plugin-workbench--multi-select .plugin-list-card :deep(.el-card__body)`（第 1446 行），换成 `.plugin-workbench--multi-select .plugin-list-shell`
   - 删除 CSS：`drawer-start/end-enter/leave-active`（第 1518 到 1549 行）和 `.plugin-workbench__rail:has(...)` 补丁（第 1492 到 1500 行）
   - 保留 `.workbench-header` 及其所有子规则（第 1574 到 1641 行），不移到新组件

5. **src/motion/layout.ts**
   - 删除整个未跟踪文件（草稿缺陷太多，从头写）

6. **不删除的部分**
   - `src/composables/useCollapsibleLabels.ts` 整个文件保留，因为 PluginDetail.vue 可能还在用（没确认）
   - `src/components/common/GridSection.vue` 不改，保持原样
   - `src/components/plugin/PluginCard.vue` 的四层结构不改
   - `data-yui-guide-id` 锚点一个都不删，原样保留
   - `WorkbenchFilterBar.vue` 不改，但它的 `v-bind('PANEL_HOST_MIN_HEIGHT')` 会被去掉（shell 不再是 el-card，不需要 min-height）

## e2e 几何测试

**文件**：`src/e2e/drawer-motion-geometry.e2e.ts`

**运行**：`npm run test:boot:e2e -- drawer-motion-geometry`

**测试用例**：

### 1. 打开市场抽屉 —— 轨迹单调
- 点击市场按钮，在动画过程中每 80ms 采样一次：
  - 市场按钮的 `x`（单调递增或不变）
  - 第一张卡片的 `x`（单调递增）
  - 头部的 `width`（单调递减）
  - 面板的 `x`（从负数单调递增到 0）
- 断言：每条曲线都单调，不回头。

### 2. 打开市场抽屉 —— 按钮状态只切换一次
- 记录动画前后头部的 `compact` class 和每个按钮的文字可见性。
- 断言：`compact` 状态在第一帧切换一次，之后不再变化。
- 断言：按钮文字要么一直可见，要么在第一帧消失后逐渐淡出，不会中途重新出现。

### 3. 打开市场抽屉 —— 布局属性不变
- 动画期间每 80ms 采样头部的 computed style：`marginLeft`、`paddingLeft`、`width`（读自 inline style，不是 offsetWidth）。
- 断言：动画开始后，这些属性只在最后一帧变化一次（提交时）。

### 4. 关闭市场抽屉 —— 对称性
- 打开 → 等待完成 → 关闭。
- 采样关闭过程的轨迹，与打开过程镜像对比（允许几个像素的误差）。

### 5. 中途反向 —— 无跳变
- 打开到一半（约 240ms）→ 点击关闭。
- 断言：反向那一帧，各元素的位置连续（与上一帧相差小于 5px）。
- 断言：反向后轨迹单调回退到起点。

### 6. 窗口 resize 不干扰
- 打开市场 → 动画期间改变窗口宽度 → 等待完成。
- 断言：抽屉动画不受影响，480ms 后正常结束。
- 断言：resize 触发的 grid reflow 和抽屉动画各自完成，不互相取消。

**辅助函数**：

```ts
async function sampleMotion<T>(
  fn: () => T,
  intervalMs: number,
  durationMs: number
): Promise<T[]> {
  const samples: T[] = []
  const start = Date.now()
  while (Date.now() - start < durationMs) {
    samples.push(fn())
    await page.waitForTimeout(intervalMs)
  }
  return samples
}

function assertMonotonic(values: number[], direction: 'increasing' | 'decreasing') {
  for (let i = 1; i < values.length; i++) {
    if (direction === 'increasing') {
      expect(values[i]).toBeGreaterThanOrEqual(values[i - 1] - 0.5)
    } else {
      expect(values[i]).toBeLessThanOrEqual(values[i - 1] + 0.5)
    }
  }
}
```

**现有 e2e 受影响的部分**：

- `appearance-boundaries.e2e.ts:581`：断言头部有 `workbench-header--compact` class。这个保留不变。
- `appearance-boundaries.e2e.ts:590`：断言 `.header-actions > .el-button` 不含文字（`.toContainText('')`）。改成检查 `visibility: hidden` 或 `opacity: 0`（调度器在动画期间把 `.button-label` 的 opacity 插值到 0）。
- `plugin-state-consistency.e2e.ts:236`：依赖 `plugin-list-main` 和 `plugin-list-card`。`plugin-list-main` 不变，`plugin-list-card` 锚点移到 `.plugin-list-shell` 上（如果测试依赖它的话，grep 确认后再改）。

**`PluginList.states.test.ts` 受影响的部分**：

- Mock 了 `WorkbenchFilterBar` 等子组件（第 28 到 30 行）。第一步不拆 `WorkbenchHeader`，所以这些 mock 不需要改。

## 实施顺序

1. **写 `computeCompact` 纯函数**（可选拆出）
   - 在 `useCollapsibleLabels.ts` 里拆出纯函数，或者直接在调度器里内联
   - 单元测试验证（如果拆出来的话）

2. **写 `scheduler.ts`**
   - 先写 cubic-bezier 求值函数（20 行）
   - 实现 `layoutTransition`，先只支持 button 和 container 角色
   - 写一个 vitest 单元测试（mock DOM 和 rAF），覆盖：
     - 启动一次过渡，验证每帧调用 `requestAnimationFrame`
     - 中途取消，验证动画停止
     - 按钮文字层淡入淡出（opacity 从 0 到 1）

3. **在 PluginList 里接入调度器**
   - 给每个按钮的文字 `<span>` 加 `class="button-label"`
   - 给每个按钮、divider 加 `data-motion-key` 和 `data-motion-role`
   - 替换 `el-card` 为 `div.plugin-list-shell`
   - 在 drawer watch 里调用 `layoutTransition` 替换 `glideToLayout`
   - 删除旧代码：`glideToLayout` 导入、`cancelDockGlide`、CSS 的 `drawer-*-enter/leave-active` 和 `:has(...)`

4. **跑类型检查和单元测试**
   - `npm run type-check && npm run lint && npm run test:unit`
   - 修复类型错误和 lint 警告

5. **写 `drawer-motion-geometry.e2e.ts`**
   - 写所有 6 个用例（见上面"e2e 几何测试"）
   - 先让它跑起来，失败是预期的（暴露现有问题）
   - `npm run test:boot:e2e -- drawer-motion-geometry`

6. **调试调度器，让几何测试通过**
   - 根据测试失败的采样数据，调整插值、timing、角色处理
   - 逐个用例修到绿

7. **修复 `appearance-boundaries.e2e.ts`**
   - 把"按钮不含文字"的断言改成检查 `opacity: 0` 或 `display: none`
   - 重新跑一遍确认没破坏其他断言
   - `npm run test:boot:e2e -- appearance-boundaries`

8. **手工验证**
   - 打开 / 关闭市场抽屉，按钮文字淡入淡出流畅，不跳变
   - 打开到一半点关闭，立即反向，不卡顿
   - 窗口 resize 不干扰抽屉动画
   - Performance 面板：480ms 动画期间没有长帧（> 20ms）

9. **清理和提交**
   - 删除 `src/motion/layout.ts`
   - 删除 `glideToLayout`、`previewers`、`abandonGlide`
   - 删除未跟踪的 diff（`grid.ts` 里的 `.grid-section__header` 补丁）
   - 提交，commit message 说明改了什么、为什么、测试覆盖

## 风险与限制

**已知风险**：
- 外皮改的是真实宽高，会在主线程上计算布局。十几张卡片加十几个按钮应该没问题，但第一次跑 e2e 时要看 Performance 面板确认没有长帧（> 16.7ms）。如果掉帧，回退到 scale + 反向缩放圆角。
- 宽度变窄后，筛选栏底部会换行，分段控件会斜着滑到下一行。按规则它也是平滑的，但斜着走好不好看，要看录屏再定。

**第一步不解决的问题**：
- 网格的 reflow 仍然走 `useGridMotionController`，和调度器是两套时钟。窗口 resize 时可能会有细微错位（几个像素）。这是第二步的任务。
- 卡片的四层结构（surface / content / end / bottom）仍然硬编码在 `grid.ts` 的 `glide` 里。
- 抽屉面板的内容加载还是异步的，首次打开会有一个 `deferredPanel` 占位。第一步在调度器里等它加载完，但占位的高度可能和真实面板不一致，第一次打开时面板可能会"长高"。这个问题留到第三步处理（提前挂载面板，或者用固定高度骨架）。

**不向后兼容的改动**：
- `el-card` 换成普通 `div`，如果有 CSS 依赖 `.el-card__body` 的深度选择器，会失效。grep 确认只有 `plugin-workbench--multi-select` 那一处，已经在删除清单里标出。
- `glideToLayout` 被删除，如果项目里其他地方有调用（MarketPanel?），第一步不处理，留到第二步统一接入调度器。grep 确认只有 PluginList.vue 一处调用。

## 成功标准

- [ ] 所有 e2e 几何测试通过（6 个用例）
- [ ] `npm run type-check && npm run lint && npm run test:unit` 全绿
- [ ] 手工验证：打开 / 关闭市场抽屉，按钮文字淡入淡出流畅，不跳变，不换行
- [ ] 手工验证：打开到一半点关闭，立即反向，不卡顿
- [ ] 手工验证：窗口 resize 不干扰抽屉动画
- [ ] Performance 面板：480ms 动画期间没有长帧（> 20ms）
- [ ] 删除清单里的所有文件和代码已清理

---

**方案确认后，下一步就是按实施顺序逐项实现，每一步都跑测试验证。**
