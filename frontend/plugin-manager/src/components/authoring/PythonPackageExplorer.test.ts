// @vitest-environment happy-dom
import {
  pythonDependencyGraph,
  pythonDependencyNode,
  installedPythonPackages,
} from '@/test/fixtures/pythonPackages'
import { afterEach, describe, expect, it } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import Explorer from './PythonPackageExplorer.vue'
import { pythonPackageMessages } from '@/i18n/python-packages'
import type { PythonDependencyGraph } from '@/api/projectPythonPackages'

const en = pythonPackageMessages['en-US']
function graph(): PythonDependencyGraph {
  return pythonDependencyGraph(
    ['alpha', 'beta'],
    [
      pythonDependencyNode('alpha', ['shared'], { declarations: ['alpha>=1'] }),
      pythonDependencyNode('beta', ['shared'], { declarations: ['beta'] }),
      pythonDependencyNode('shared', ['leaf'], { version: '2.0' }),
      pythonDependencyNode('leaf', [], { version: '3.0' }),
      pythonDependencyNode('standalone'),
    ],
    ['standalone']
  )
}
let app: ReturnType<typeof createApp> | undefined
async function flush() {
  for (let n = 0; n < 15; n++) {
    await Promise.resolve()
    await nextTick()
  }
}
async function mount(value = graph()) {
  const packages = installedPythonPackages(value)
  const props = reactive({ graph: value, packages })
  const host = document.createElement('div')
  document.body.append(host)
  app = createApp({ render: () => h(Explorer, props) })
    .use(ElementPlus)
    .use(
      createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { pythonPackages: en } } })
    )
  app.mount(host)
  await flush()
  return props
}
afterEach(() => {
  app?.unmount()
  document.body.innerHTML = ''
  app = undefined
})
describe('installed package dependency explorer', () => {
  it('shows dependencies under each parent and updates the detail pane when selected', async () => {
    await mount()
    const shared = document.querySelectorAll<HTMLElement>('[data-package-id="shared"]')
    expect(shared).toHaveLength(2)
    shared[0]!.click()
    await flush()
    const info = document.querySelector('[data-testid="python-package-info"]')!
    expect(info.querySelector('h4')?.textContent).toBe('shared')
    expect(info.textContent).toContain('Summary for shared')
    expect(info.textContent).toContain('alpha')
    expect(info.textContent).toContain('beta')
    expect(info.textContent).toContain('leaf')
    expect(shared[0]!.textContent).toContain(en.sharedDependency)
  })
  it('finds a nested package while retaining its parent paths', async () => {
    await mount()
    const input = document.querySelector<HTMLInputElement>('input')!
    input.value = 'leaf'
    input.dispatchEvent(new Event('input'))
    await flush()
    expect(document.querySelectorAll('[data-package-id="leaf"]').length).toBeGreaterThan(0)
    expect(document.querySelector('[data-package-id="alpha"]')).toBeTruthy()
    expect(document.querySelector('[data-package-id="standalone"]')).toBeNull()
    expect(document.querySelector('[data-testid="python-package-info"] h4')?.textContent).toBe(
      'leaf'
    )
  })
  it('renders a missing dependency and stops expanding ancestor cycles', async () => {
    const value = graph()
    value.nodes[2]!.dependencies = [
      { id: 'alpha', requirements: ['alpha'], status: 'satisfied' },
      { id: 'absent', requirements: ['absent'], status: 'missing' },
    ]
    value.nodes[0]!.required_by = ['shared']
    value.nodes.push({
      id: 'absent',
      name: 'absent',
      version: null,
      direct: false,
      status: 'missing',
      declarations: [],
      dependencies: [],
      required_by: ['shared'],
    })
    await mount(value)
    const shared = document.querySelector<HTMLElement>('[data-package-id="shared"]')!
    shared
      .closest('.el-tree-node__content')!
      .querySelector<HTMLElement>('.el-tree-node__expand-icon')!
      .click()
    await flush()
    const absent = document.querySelector<HTMLElement>('[data-package-id="absent"]')!
    expect(absent.textContent).toContain(en.missing)
    expect(document.body.textContent).toContain(en.cycleReference)
    absent.click()
    await flush()
    expect(document.querySelector('[data-testid="python-package-info"]')?.textContent).toContain(
      en.missing
    )
  })
  it('shows each incoming edge status without overriding it with the shared node status', async () => {
    const value = graph()
    value.nodes[1]!.dependencies[0] = {
      id: 'shared',
      requirements: ['shared>=3'],
      status: 'version_mismatch',
    }
    value.nodes[2]!.status = 'version_mismatch'
    value.roots.push('shared')
    await mount(value)
    const rows = [...document.querySelectorAll<HTMLElement>('[data-package-id="shared"]')]
    expect(rows).toHaveLength(3)
    expect(rows[0]!.textContent).not.toContain(en.incompatible)
    expect(rows[0]!.textContent).toContain('2.0')
    expect(rows[1]!.textContent).toContain(en.incompatible)
    expect(rows[2]!.textContent).toContain(en.incompatible)
  })
  it('bounds search rendering when a small graph shares dependencies across many paths', async () => {
    const value: PythonDependencyGraph = { roots: ['root'], other_roots: [], nodes: [] }
    const levels = [
      ['root'],
      ...Array.from({ length: 8 }, (_, n) => [`level-${n}-a`, `level-${n}-b`]),
      ['needle'],
    ]
    levels.forEach((ids, depth) =>
      ids.forEach((id) =>
        value.nodes.push({
          id,
          name: id,
          version: '1',
          direct: depth === 0,
          status: 'satisfied',
          declarations: depth === 0 ? ['root'] : [],
          dependencies: (levels[depth + 1] ?? []).map((child) => ({
            id: child,
            requirements: [child],
            status: 'satisfied',
          })),
          required_by: levels[depth - 1] ?? [],
        })
      )
    )
    await mount(value)
    const input = document.querySelector<HTMLInputElement>('input')!
    input.value = 'needle'
    input.dispatchEvent(new Event('input'))
    await flush()
    expect(document.querySelectorAll('[data-package-id]').length).toBeLessThanOrEqual(500)
    expect(document.body.textContent).toContain('Search results limited. Refine your search.')
    expect(document.querySelector('[data-package-id="needle"]')).toBeTruthy()
  })
  it('clears stale details when the installed graph becomes empty', async () => {
    const props = await mount()
    props.graph = { roots: [], other_roots: [], nodes: [] }
    props.packages = []
    await flush()
    expect(document.body.textContent).not.toContain('Summary for alpha')
    expect(document.body.textContent).toContain(en.empty)
  })
})
