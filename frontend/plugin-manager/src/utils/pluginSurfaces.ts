import type { PluginUiSurface } from '@/types/api'

/**
 * 面板 surface 的选取判据——**一处定义，多处使用**。
 *
 * 这份判据原先只内联在 `views/PluginDetail.vue` 里。适配器界面页（`views/AdapterUI.vue`）
 * 走的是另一条更老的路径（`PluginUIFrame` + `/plugin/{id}/ui-info` 的 `has_ui`，
 * 而该字段只表示"有没有 static/index.html"），于是插件只要用 `[plugin.ui] panel` +
 * `ui/panel.tsx` 声明界面、又没有 static 目录，适配器页就会显示"该插件没有自定义界面"，
 * 而同一个插件在详情页渲染完全正常（实测 mcp_adapter）。
 *
 * 两个页面必须用同一判据，否则会再次分叉。
 */

/**
 * 可渲染的面板 surface。
 *
 * `available === false` 的会被跳过（插件自己声明成不可用）；
 * `mode === 'auto'` 在 manifest 里合法但还没有对应渲染器，留着它只会用占位块挡住
 * 真正可用的界面。
 */
export function renderablePanelSurfaces(surfaces: PluginUiSurface[]): PluginUiSurface[] {
  return surfaces.filter(
    (surface) => surface.kind === 'panel' && surface.available !== false && surface.mode !== 'auto',
  )
}

/**
 * 单个面板视图（如适配器界面页）应该渲染哪一个。
 *
 * 后端会在已声明面板之前插入宿主合成的 legacy static 兼容面板，所以优先"插件自己声明的
 * hosted-tsx 面板"，其次是任一已声明面板，最后才轮到那个合成的兼容面板。
 */
export function pickPrimaryPanelSurface(surfaces: PluginUiSurface[]): PluginUiSurface | null {
  const renderable = renderablePanelSurfaces(surfaces)
  const declared = renderable.filter((surface) => !surface.legacy_static_compat)
  return declared.find((surface) => surface.mode === 'hosted-tsx') ?? declared[0] ?? renderable[0] ?? null
}
