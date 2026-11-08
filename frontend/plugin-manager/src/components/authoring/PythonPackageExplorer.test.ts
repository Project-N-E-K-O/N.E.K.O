// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import Explorer from './PythonPackageExplorer.vue'
import { pythonPackageMessages } from '@/i18n/python-packages'
import type { InstalledPythonPackage, PythonDependencyGraph } from '@/api/projectPythonPackages'

const en = pythonPackageMessages['en-US']
function graph(): PythonDependencyGraph {
  return {
    roots: ['alpha', 'beta'],
    other_roots: ['standalone'],
    nodes: [
      {
        id: 'alpha',
        name: 'alpha',
        version: '1.0',
        direct: true,
        status: 'satisfied',
        declarations: ['alpha>=1'],
        dependencies: [{ id: 'shared', requirements: ['shared>=1'], status: 'satisfied' }],
        required_by: [],
      },
      {
        id: 'beta',
        name: 'beta',
        version: '1.0',
        direct: true,
        status: 'satisfied',
        declarations: ['beta'],
        dependencies: [{ id: 'shared', requirements: ['shared'], status: 'satisfied' }],
        required_by: [],
      },
      {
        id: 'shared',
        name: 'shared',
        version: '2.0',
        direct: false,
        status: 'satisfied',
        declarations: [],
        dependencies: [{ id: 'leaf', requirements: ['leaf'], status: 'satisfied' }],
        required_by: ['alpha', 'beta'],
      },
      {
        id: 'leaf',
        name: 'leaf',
        version: '3.0',
        direct: false,
        status: 'satisfied',
        declarations: [],
        dependencies: [],
        required_by: ['shared'],
      },
      {
        id: 'standalone',
        name: 'standalone',
        version: '1.0',
        direct: false,
        status: 'satisfied',
        declarations: [],
        dependencies: [],
        required_by: [],
      },
    ],
  }
}
let app: ReturnType<typeof createApp> | undefined
async function flush() {
  for (let n = 0; n < 15; n++) {
    await Promise.resolve()
    await nextTick()
  }
}
async function mount(value = graph()) {
  const packages: InstalledPythonPackage[] = value.nodes
    .filter((node) => node.version)
    .map((node) => ({
      name: node.name,
      version: node.version!,
      direct: node.direct,
      summary: `Summary for ${node.name}`,
      requires_python: '>=3.11',
      requires_dist: node.dependencies.flatMap((edge) => edge.requirements),
    }))
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
  it('clears stale details when the installed graph becomes empty', async () => {
    const props = await mount()
    props.graph = { roots: [], other_roots: [], nodes: [] }
    props.packages = []
    await flush()
    expect(document.body.textContent).not.toContain('Summary for alpha')
    expect(document.body.textContent).toContain(en.empty)
  })
})
