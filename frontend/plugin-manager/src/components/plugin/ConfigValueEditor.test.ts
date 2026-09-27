// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import ElementPlus from 'element-plus'
import ConfigValueEditor from './ConfigValueEditor.vue'
import type { ConfigEditorSchema } from '@/types/configSchema'

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
function mountEditor(modelValue: any, baselineValue: any, schema?: ConfigEditorSchema) {
  const emitted: any[] = []
  const host = document.createElement('div')
  document.body.appendChild(host)
  const Wrapper = defineComponent(() => () =>
    h(ConfigValueEditor as any, {
      modelValue,
      baselineValue,
      schema,
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
  const row = rows.find((r) => r.querySelector('.k .el-tag')?.textContent?.trim() === key)
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

  it('重置某段最后一个覆盖项时，摘掉空掉的父表而不是存空表', async () => {
    // 后端 deep_merge 把空 mapping 当「替换」处理，存下 { llm: {} } 会把整段
    // 基线抹掉，而预览的合并不实现这条语义，界面上还显示着继承内容。
    const baseline = { llm: { model: 'gpt-a', temperature: 1 } }
    const { host, emitted } = mountEditor({ llm: { model: 'mine' } }, baseline)
    await nextTick()

    const row = rowFor(host, 'model')
    expect(opsButtons(row)).toEqual(['common.reset'])
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({})
  })

  it('多层嵌套时剪枝一路向上冒泡', async () => {
    const baseline = { a: { b: { c: 0, d: 2 } } }
    const { host, emitted } = mountEditor({ a: { b: { c: 1 } } }, baseline)
    await nextTick()

    const row = rowFor(host, 'c')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({})
  })

  it('基线里没有的自定义空表是显式意图，不被剪掉', async () => {
    const baseline = { alpha: 'a' }
    const { host, emitted } = mountEditor({ custom: { x: 1 } }, baseline)
    await nextTick()

    const row = rowFor(host, 'x')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ custom: {} })
  })

  it('数组尚未被覆盖时，编辑其中一项落成完整数组', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const inputs = Array.from(host.querySelectorAll('input')) as HTMLInputElement[]
    expect(inputs).toHaveLength(3)
    typeInto(inputs[1]!, 'b2')
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'b2', 'c'] })
  })

  it('数组尚未被覆盖时，添加项追加在基线全量之后', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const addBtn = Array.from(host.querySelectorAll('.arr > .add button')).pop() as HTMLButtonElement
    addBtn.click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'b', 'c', ''] })
  })

  it('数组尚未被覆盖时，删除一项落成剩余的完整数组', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({}, baseline)
    await nextTick()

    const row = rowFor(host, '0')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ hosts: ['b', 'c'] })
  })

  it('overlay 数组按自身长度渲染，不拿基线补尾巴', async () => {
    // 整体替换语义下尾部不会被继承，补出来就是删不掉的幻影项。
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host } = mountEditor({ hosts: ['a'] }, baseline)
    await nextTick()

    const inputs = Array.from(host.querySelectorAll('input')) as HTMLInputElement[]
    expect(inputs).toHaveLength(1)
    expect(inputs[0]!.value).toBe('a')
  })

  it('删掉末项后它不会被基线填回来', async () => {
    const baseline = { hosts: ['a', 'b', 'c'] }
    const { host, emitted } = mountEditor({ hosts: ['a', 'b', 'c'] }, baseline)
    await nextTick()

    const row = rowFor(host, '2')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ hosts: ['a', 'b'] })

    // 用写回后的 overlay 重新渲染：末项不该复活
    const again = mountEditor({ hosts: ['a', 'b'] }, baseline)
    await nextTick()
    expect(Array.from(again.host.querySelectorAll('input'))).toHaveLength(2)
  })

  it('数组项 overlay 存在时，不显示基线独有的字段', async () => {
    const baseline = { servers: [{ host: 'h1', port: 80 }] }
    const { host } = mountEditor({ servers: [{ host: 'mine' }] }, baseline)
    await nextTick()

    const keys = Array.from(host.querySelectorAll('.row > .k')).map((n) =>
      (n.textContent || '').trim()
    )
    expect(keys).toContain('host')
    expect(keys).not.toContain('port')
  })

  it('数组项里能显式补回基线独有的字段（不被当成重名拒绝）', async () => {
    const baseline = { servers: [{ host: 'h1', port: 80 }] }
    const { host, emitted } = mountEditor({ servers: [{ host: 'mine' }] }, baseline)
    await nextTick()

    // 数组项那一层的「添加字段」按钮（文档序里最靠前的那个 .add 属于它）
    const addBtn = host.querySelector('.add button') as HTMLButtonElement
    addBtn.click()
    await nextTick()

    const dialogInput = document.querySelector('.el-dialog input') as HTMLInputElement
    dialogInput.value = 'port'
    dialogInput.dispatchEvent(new Event('input'))
    await nextTick()

    const confirm = Array.from(document.querySelectorAll('.el-dialog button')).find(
      (b) => (b.textContent || '').trim() === 'common.confirm'
    ) as HTMLButtonElement
    confirm.click()
    await nextTick()

    expect(emitted.length).toBeGreaterThan(0)
  })

  it('数组项内的字段「重置」写回基线值，而不是把字段删掉', async () => {
    // 数组整体替换，没有继承回填 —— 摘掉键就等于把 servers[0].host
    // 从生效配置里删了。
    const baseline = { servers: [{ host: 'h1', port: 80 }] }
    const { host, emitted } = mountEditor({ servers: [{ host: 'mine', port: 80 }] }, baseline)
    await nextTick()

    const row = rowFor(host, 'host')
    ;(row.querySelector(':scope > .ops button') as HTMLButtonElement).click()
    await nextTick()

    expect(lastEmit(emitted)).toEqual({ servers: [{ host: 'h1', port: 80 }] })
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

describe('ConfigValueEditor — JSON Schema', () => {
  it('renders localized nested labels and descriptions while emitting only raw keys', async () => {
    const schema: ConfigEditorSchema = { type: 'object', properties: {
      search: { type: 'object', title: 'Search', properties: {
        query: { type: 'string', title: '查询', description: '说明',
          'x-title-i18n': { en: 'Search query', 'zh-CN': '查询' },
          'x-description-i18n': { en: '<b>Plain text</b>', 'zh-CN': '说明' } },
      } },
    } }
    const { host, emitted } = mountEditor({}, { search: { query: 'old', extra: 'keep' } }, schema)
    await nextTick()
    expect(rowFor(host, 'query').textContent).toContain('Search query')
    expect(rowFor(host, 'query').textContent).toContain('<b>Plain text</b>')
    expect(rowFor(host, 'query').querySelector('b')).toBeNull()
    expect(emitted).toEqual([])
    typeInto(rowFor(host, 'query').querySelector('input')!, 'new')
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ search: { query: 'new' } })
  })

  it('uses declared types for absent fields without persisting schema defaults', async () => {
    const schema: ConfigEditorSchema = { type: 'object', properties: {
      retries: { type: 'integer', minimum: 1, maximum: 9, default: 4 },
      enabled: { type: 'boolean', default: true },
      name: { type: 'string', maxLength: 12, default: 'example' },
      nested: { type: 'object', properties: { text: { type: 'string' } } },
    } }
    const { host, emitted } = mountEditor({}, {}, schema)
    await nextTick()
    expect(rowFor(host, 'retries').querySelector('.el-input-number')).not.toBeNull()
    expect(rowFor(host, 'enabled').querySelector('.el-switch')).not.toBeNull()
    expect(rowFor(host, 'name').querySelector('input')?.maxLength).toBe(12)
    expect(rowFor(host, 'text')).toBeTruthy()
    expect(emitted).toEqual([])
    typeInto(rowFor(host, 'text').querySelector('input')!, 'new')
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ nested: { text: 'new' } })
  })

  it('enforces integer bounds through the number control', async () => {
    const { host, emitted } = mountEditor({}, { count: 3 }, { type: 'object', properties: {
      count: { type: 'integer', minimum: 1, maximum: 5 },
    } })
    await nextTick()
    typeInto(rowFor(host, 'count').querySelector('input')!, '9.5')
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ count: 5 })
  })

  it('keeps numeric and boolean enum values typed', async () => {
    for (const values of [[1, 2], [false, true]]) {
      const { host, emitted } = mountEditor({}, { choice: values[0] }, { type: 'object', properties: {
        choice: { enum: values },
      } })
      await nextTick()
      ;(rowFor(host, 'choice').querySelector('.el-select__wrapper') as HTMLElement).click()
      await nextTick()
      const options = Array.from(document.querySelectorAll('.el-select-dropdown__item'))
      const option = options.find((o) => o.textContent?.trim() === String(values[1])) as HTMLElement
      option.click()
      await nextTick()
      expect(lastEmit(emitted)).toEqual({ choice: values[1] })
    }
  })

  it('propagates readOnly to nested controls and structural buttons', async () => {
    const { host, emitted } = mountEditor({ locked: { name: 'mine', list: ['a'] } },
      { locked: { name: 'old', list: ['b'] } }, { type: 'object', properties: {
        locked: { type: 'object', readOnly: true },
      } })
    await nextTick()
    const row = rowFor(host, 'locked')
    expect(Array.from(row.querySelectorAll('input')).every((input) => input.disabled)).toBe(true)
    expect(Array.from(row.querySelectorAll('button')).every((button) => button.disabled)).toBe(true)
    expect(emitted).toEqual([])
  })

  it('applies item schemas and preserves whole-array replacement when adding', async () => {
    const { host, emitted } = mountEditor({}, { servers: [{ port: 80 }] }, { type: 'object', properties: {
      servers: { type: 'array', items: { type: 'object', default: { port: 443 }, properties: {
        port: { type: 'integer', title: 'Port', minimum: 1 },
      } } },
    } })
    await nextTick()
    expect(rowFor(host, 'port').textContent).toContain('Port')
    const add = Array.from(rowFor(host, 'servers').querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'plugins.addItem',
    )!
    add.click()
    await nextTick()
    expect(lastEmit(emitted)).toEqual({ servers: [{ port: 80 }, { port: 443 }] })
  })

  it('ignores protected schema keys and keeps undeclared existing fields', async () => {
    const schema = JSON.parse('{"type":"object","properties":{"plugin":{"type":"object"},"__proto__":{"type":"object"},"constructor":{"type":"string"},"a.b":{"type":"string"}}}')
    const { host } = mountEditor({}, { legacy: 'value' }, schema)
    await nextTick()
    const keys = Array.from(host.querySelectorAll('.k .el-tag')).map((tag) => tag.textContent?.trim())
    expect(keys).toEqual(['legacy'])
  })
})
