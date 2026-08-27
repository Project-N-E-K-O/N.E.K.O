// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import ElementPlus from 'element-plus'
import ConfigValueEditor from './ConfigValueEditor.vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ locale: ref('en-US'), t: (key: string) => key }),
}))

const mounted: Array<{ unmount: () => void; host: HTMLElement }> = []

afterEach(() => {
  while (mounted.length) {
    const item = mounted.pop()
    item?.unmount()
    item?.host.remove()
  }
})

/**
 * 挂载编辑器根节点。`modelValue` 是 profile overlay，`baselineValue` 是
 * 「清单默认值 + 运行时配置」的合并基线。emitted 收集写回 overlay 的结果。
 */
function mountEditor(modelValue: any, baselineValue: any) {
  const emitted: any[] = []
  const host = document.createElement('div')
  document.body.appendChild(host)
  const Wrapper = defineComponent(() => () =>
    h(ConfigValueEditor as any, {
      modelValue,
      baselineValue,
      path: '',
      'onUpdate:modelValue': (v: any) => emitted.push(v),
    })
  )
  const app = createApp(Wrapper)
  app.use(ElementPlus)
  app.mount(host)
  mounted.push({ unmount: () => app.unmount(), host })
  return { host, emitted }
}

function lastEmit(emitted: any[]) {
  expect(emitted.length).toBeGreaterThan(0)
  return emitted[emitted.length - 1]
}

function typeInto(input: HTMLInputElement, value: string) {
  input.value = value
  input.dispatchEvent(new Event('input'))
  input.dispatchEvent(new Event('change'))
}

function rowFor(host: HTMLElement, key: string): HTMLElement {
  const rows = Array.from(host.querySelectorAll('.row')) as HTMLElement[]
  const row = rows.find((r) => r.querySelector('.k')?.textContent?.trim() === key)
  if (!row) throw new Error(`row for key "${key}" not found`)
  return row
}

function opsButtons(row: HTMLElement): string[] {
  return Array.from(row.querySelectorAll(':scope > .ops button')).map((b) =>
    (b.textContent || '').trim()
  )
}

describe('ConfigValueEditor — profile overlay 保持稀疏', () => {
  it('编辑「基线独有段」里的一个叶子时，只写回被改的键（不固化整段默认值）', async () => {
    const baseline = { llm: { model: 'gpt-a', temperature: 1, top_p: 0.9 } }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const input = rowFor(host, 'model').querySelector('input') as HTMLInputElement
    typeInto(input, 'gpt-b')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ llm: { model: 'gpt-b' } })
  })

  it('该段已有稀疏覆盖时，编辑不会把清单新增的默认值一起拖进 profile', async () => {
    // 模拟插件升级：清单新增了 top_p，profile 里早就覆盖过 model
    const baseline = { llm: { model: 'gpt-a', temperature: 1, top_p: 0.9 } }
    const { host, emitted } = mountEditor({ llm: { model: 'mine' } }, baseline)
    await nextTick()

    const input = rowFor(host, 'model').querySelector('input') as HTMLInputElement
    typeInto(input, 'mine2')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ llm: { model: 'mine2' } })
  })

  it('编辑顶层继承叶子时也只写回该键', async () => {
    const baseline = { alpha: 'a', beta: 'b' }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const input = rowFor(host, 'alpha').querySelector('input') as HTMLInputElement
    typeInto(input, 'changed')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ alpha: 'changed' })
  })

  it('继承中的键渲染基线值，但不提供任何写入按钮', async () => {
    const baseline = { llm: { model: 'gpt-a' } }
    const { host } = mountEditor({}, baseline)
    await nextTick()

    const input = rowFor(host, 'model').querySelector('input') as HTMLInputElement
    expect(input.value).toBe('gpt-a')
    expect(opsButtons(rowFor(host, 'llm'))).toEqual([])
  })

  it('「重置」把被覆盖的键移出 overlay，而不是把基线值写进去', async () => {
    const baseline = { alpha: 'default', beta: 'b' }
    const { host, emitted } = mountEditor({ alpha: 'overridden' }, baseline)
    await nextTick()

    const row = rowFor(host, 'alpha')
    expect(opsButtons(row)).toEqual(['common.reset'])
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({})
  })

  it('基线里没有的自定义键提供「删除」', async () => {
    const baseline = { alpha: 'default' }
    const { host, emitted } = mountEditor({ custom: 'x' }, baseline)
    await nextTick()

    const row = rowFor(host, 'custom')
    expect(opsButtons(row)).toEqual(['common.delete'])
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({})
  })

  it('根节点隐藏 plugin 段（profile 不允许覆盖）', async () => {
    const baseline = { plugin: { id: 'demo', name: 'Demo' }, alpha: 'a' }
    const { host } = mountEditor({}, baseline)
    await nextTick()

    const keys = Array.from(host.querySelectorAll('.row > .k')).map((n) =>
      (n.textContent || '').trim()
    )
    expect(keys).toContain('alpha')
    expect(keys).not.toContain('plugin')
  })

  it('「添加字段」与基线中已有的键重名时被拒绝（否则会把整段默认值覆盖成空值）', async () => {
    const baseline = { llm: { model: 'gpt-a' } }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    // 根节点自己的「添加字段」按钮（嵌套编辑器也各有一个，必须限定层级）
    const addBtn = host.querySelector(
      ':scope > .cve > .obj > .add button'
    ) as HTMLButtonElement
    addBtn.click()
    await nextTick()

    const dialogInput = document.querySelector('.el-dialog input') as HTMLInputElement
    dialogInput.value = 'llm'
    dialogInput.dispatchEvent(new Event('input'))
    await nextTick()

    const confirm = Array.from(document.querySelectorAll('.el-dialog button')).find(
      (b) => (b.textContent || '').trim() === 'common.confirm'
    ) as HTMLButtonElement
    confirm.click()
    await nextTick()

    expect(emitted).toEqual([])
  })

  it('数组整份写回（后端 deep_merge 对数组是替换语义，稀疏数组会产生空洞）', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const inputs = Array.from(host.querySelectorAll('input')) as HTMLInputElement[]
    expect(inputs).toHaveLength(3)
    typeInto(inputs[2]!, 'c2')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'b', 'c2'] })
  })
})
