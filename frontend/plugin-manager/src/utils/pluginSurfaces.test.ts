import { describe, expect, it } from 'vitest'
import type { PluginUiSurface } from '@/types/api'
import { pickPrimaryPanelSurface, renderablePanelSurfaces } from './pluginSurfaces'

/**
 * 重构等价性。
 *
 * `renderablePanelSurfaces` / `pickPrimaryPanelSurface` 是从 `views/PluginDetail.vue`
 * 里抽出来共享的——抽之前那段判据只内联在详情页里，适配器界面页压根没有，于是只用
 * surface 声明界面的适配器被误报"没有自定义界面"。抽取意味着**所有插件**的详情页面板
 * 选取都换了代码路径，所以这里把重构前的那三行表达式原样写成参照实现，在边界矩阵上
 * 逐一对齐：任何一格不一致都说明重构改了既有插件的行为。
 *
 * 参照实现（重构前的 PluginDetail.vue）：
 *   const availablePanelSurfaces = panelSurfaces.filter(s => s.available !== false)
 *   const renderable   = availablePanelSurfaces.filter(s => s.mode !== 'auto')
 *   const declared     = renderable.filter(s => !s.legacy_static_compat)
 *   default = declared.find(s => s.mode === 'hosted-tsx') ?? declared[0] ?? renderable[0]
 */
function legacyReference(surfaces: PluginUiSurface[]): {
  renderable: PluginUiSurface[]
  default_: PluginUiSurface | undefined
} {
  const panelSurfaces = surfaces.filter((s) => s.kind === 'panel')
  const availablePanelSurfaces = panelSurfaces.filter((s) => s.available !== false)
  // `auto` 在 manifest 里合法但没有渲染器
  const renderable = availablePanelSurfaces.filter((s) => s.mode !== 'auto')
  const declared = renderable.filter((s) => !s.legacy_static_compat)
  return {
    renderable,
    default_: declared.find((s) => s.mode === 'hosted-tsx') ?? declared[0] ?? renderable[0],
  }
}

const surface = (over: Partial<PluginUiSurface> & { id: string }): PluginUiSurface =>
  ({ kind: 'panel', mode: 'static', ...over }) as PluginUiSurface

const CASES: Array<{ name: string; list: PluginUiSurface[] }> = [
  { name: '空列表', list: [] },
  {
    name: '只有 guide（内置 lifekit / game_agent_minecraft / wechat_integration 就这种）',
    list: [
      surface({ id: 'g1', kind: 'guide', mode: 'hosted-tsx' }),
      surface({ id: 'g2', kind: 'docs', mode: 'markdown' }),
    ],
  },
  {
    name: 'sole panel 但 available=false',
    list: [surface({ id: 'p1', available: false })],
  },
  {
    name: 'sole panel 但 mode=auto（没有渲染器）',
    list: [surface({ id: 'p1', mode: 'auto' })],
  },
  {
    name: '只有宿主合成的 legacy 兼容面板（老插件 static/index.html 的情况）',
    list: [surface({ id: 'main', mode: 'static', legacy_static_compat: true })],
  },
  {
    name: '合成兼容面板 + 插件自己声明的 hosted-tsx（mcp_adapter 那种）',
    list: [
      surface({ id: 'main', mode: 'static', legacy_static_compat: true }),
      surface({ id: 'ms', mode: 'hosted-tsx', title: 'MCP Adapter' }),
    ],
  },
  {
    name: '多个已声明面板，hosted-tsx 排在后面（应优先 hosted-tsx）',
    list: [
      surface({ id: 'a', mode: 'static' }),
      surface({ id: 'b', mode: 'markdown' }),
      surface({ id: 'c', mode: 'hosted-tsx' }),
    ],
  },
  {
    name: '已声明全是非 hosted-tsx（应取第一个已声明）',
    list: [surface({ id: 'a', mode: 'markdown' }), surface({ id: 'b', mode: 'static' })],
  },
  {
    name: '声明面板都不可用，只剩合成兼容面板',
    list: [
      surface({ id: 'x', available: false, mode: 'hosted-tsx' }),
      surface({ id: 'main', legacy_static_compat: true }),
    ],
  },
  {
    name: '混合噪声：guide + auto + 不可用 + 合成 + 已声明',
    list: [
      surface({ id: 'g', kind: 'guide' }),
      surface({ id: 'auto', mode: 'auto' }),
      surface({ id: 'off', available: false }),
      surface({ id: 'legacy', legacy_static_compat: true }),
      surface({ id: 'real', mode: 'hosted-tsx' }),
    ],
  },
]

describe('pluginSurfaces 与重构前的内联判据等价', () => {
  for (const { name, list } of CASES) {
    it(name, () => {
      const ref = legacyReference(list)
      expect(renderablePanelSurfaces(list).map((s) => s.id)).toEqual(ref.renderable.map((s) => s.id))
      // 参照实现空列表时给 undefined，共享实现对外的契约是 null，两者都为假值
      expect(pickPrimaryPanelSurface(list)?.id ?? null).toEqual(ref.default_?.id ?? null)
    })
  }

  it('pickPrimaryPanelSurface 空列表返回 null（调用方用真值判断，语义不变）', () => {
    expect(pickPrimaryPanelSurface([])).toBeNull()
    expect(Boolean(pickPrimaryPanelSurface([]))).toBe(Boolean(legacyReference([]).default_))
  })
})
