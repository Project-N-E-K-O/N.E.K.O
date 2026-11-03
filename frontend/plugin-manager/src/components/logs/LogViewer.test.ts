// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import LogViewer from './LogViewer.vue'

vi.mock('@/api/index', () => ({ get: vi.fn() }))

const messages = import.meta.glob('../../i18n/locales/*.ts', { eager: true, import: 'default' }) as Record<string, typeof import('../../i18n/locales/zh-CN').default>
class Socket {
  static instances: Socket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  close = vi.fn()
  constructor(public url: string) { Socket.instances.push(this) }
  receive(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }) }
}
const cleanup: (() => void)[] = []
const latest = () => Socket.instances.at(-1)!

function mountViewer(locale = 'zh-CN') {
  const i18n = createI18n({
    legacy: false, locale, fallbackLocale: false,
    messages: Object.fromEntries(Object.entries(messages).map(([path, value]) => [path.split('/').at(-1)!.replace('.ts', ''), value])),
  })
  const root = document.createElement('div')
  const app = createApp(LogViewer, { pluginId: 'demo' })
  const container = defineComponent({ setup: (_, { attrs, slots }) => () => h('div', attrs, slots.default?.()) })
  for (const name of ['ElSpace', 'ElTag', 'ElSelect', 'ElOption', 'ElInputNumber', 'ElIcon', 'ElButton']) app.component(name, container)
  app.component('ElSwitch', defineComponent({
    props: ['modelValue'], emits: ['update:modelValue'],
    setup: (props, { attrs, emit }) => () => h('input', {
      ...attrs, type: 'checkbox', checked: props.modelValue,
      onChange: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).checked),
    }),
  }))
  app.component('ElAlert', defineComponent({
    props: ['title'], setup: (props, { attrs }) => () => h('div', attrs, props.title),
  }))
  app.component('ElEmpty', defineComponent({
    props: ['description'], setup: (props) => () => h('div', { 'data-test': 'empty' }, props.description),
  }))
  app.component('ElInput', defineComponent({
    props: ['modelValue'], emits: ['update:modelValue'],
    setup: (props, { attrs, emit }) => () => h('input', {
      ...attrs, value: props.modelValue,
      onInput: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).value),
    }),
  }))
  app.use(i18n)
  app.mount(root)
  cleanup.push(() => app.unmount())
  return { root, i18n }
}

beforeEach(() => {
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
})
afterEach(() => {
  cleanup.splice(0).forEach(dispose => dispose())
  vi.unstubAllGlobals()
})

describe('localized log failures', () => {
  it.each([
    ['zh-CN', '无法读取日志文件，请检查日志目录权限。'],
    ['en-US', 'Could not read log files. Check the log directory permissions.'],
    ['ja', 'ログファイルを読み取れません。ログディレクトリの権限を確認してください。'],
  ])('renders a server error code in %s', async (locale, expected) => {
    const { root } = mountViewer(locale)
    latest().receive({ type: 'error', code: 'LOG_READ_FAILED', error: 'Internal English disk failure' })
    await nextTick()
    const alert = root.querySelector('[data-yui-guide-id="log-load-error"]')!
    expect(alert.textContent).toContain(expected)
    expect(alert.textContent).not.toContain('Internal English disk failure')
  })

  it.each([
    ['INVALID_PLUGIN_ID', '插件标识无效。'],
    ['INVALID_LOG_QUERY', '日志查询条件无效。'],
    ['FUTURE_SERVER_ERROR', '无法读取日志文件，请检查日志目录权限。'],
    [undefined, '无法读取日志文件，请检查日志目录权限。'],
  ])('handles %s including older servers without codes', async (code, expected) => {
    const { root } = mountViewer()
    latest().receive({ type: 'error', code, error: 'Untranslated server detail' })
    await nextTick()
    expect(root.querySelector('[data-yui-guide-id="log-load-error"]')!.textContent).toContain(expected)
  })

  it('updates a visible error on locale changes and clears it on recovery', async () => {
    const { root, i18n } = mountViewer()
    latest().receive({ type: 'error', code: 'LOG_READ_FAILED', error: 'read failure' })
    await nextTick()
    i18n.global.locale.value = 'ja'
    await nextTick()
    expect(root.querySelector('[data-yui-guide-id="log-load-error"]')!.textContent).toContain('ログファイルを読み取れません')
    latest().receive({ type: 'initial', logs: [] })
    await nextTick()
    expect(root.querySelector('[data-yui-guide-id="log-load-error"]')).toBeNull()
  })
})

describe('log search compatibility', () => {
  it.each(['alpha.py:7', 'INFO', '2026-10-26', 'ready'])('filters draft search across displayed fields: %s', async keyword => {
    const { root } = mountViewer()
    latest().receive({ type: 'initial', logs: [
      { id: '1', timestamp: '2026-10-26 12:00:00', level: 'INFO', file: 'alpha.py', line: 7, message: 'ready' },
      { id: '2', timestamp: '2026-10-27 12:00:00', level: 'ERROR', file: 'beta.py', line: 9, message: 'failed' },
    ] })
    await nextTick()
    const input = root.querySelector<HTMLInputElement>('[data-yui-guide-id="log-search"]')!
    input.value = keyword
    input.dispatchEvent(new Event('input'))
    await nextTick()
    expect(root.querySelectorAll('.log-item')).toHaveLength(1)
    expect(root.querySelector('.log-message')!.textContent).toBe('ready')
    expect(Socket.instances).toHaveLength(1)
    input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter' }))
    const query = new URL(latest().url).searchParams
    expect(query.get('search')).toBe(keyword)
    expect(query.get('search_scope')).toBe('all')
  })

  it('distinguishes filtered history from a genuinely empty log', async () => {
    const { root } = mountViewer()
    latest().receive({ type: 'initial', logs: [], total_lines: 5, returned_lines: 0 })
    await nextTick()
    expect(root.querySelector('[data-test="empty"]')!.textContent).toBe('没有匹配的日志')
    latest().receive({ type: 'initial', logs: [], total_lines: 0, returned_lines: 0 })
    await nextTick()
    expect(root.querySelector('[data-test="empty"]')!.textContent).toBe('暂无日志')
  })
})

describe('log scroll following', () => {
  const entry = (id: string) => ({ id, timestamp: '2026-10-28 12:00:00', level: 'INFO', file: 'server.py', line: 1, message: id })

  async function scrollableViewer() {
    const { root } = mountViewer()
    const list = root.querySelector<HTMLElement>('[data-yui-guide-id="log-list"]')!
    // happy-dom does not lay out elements; model a growing list explicitly.
    Object.defineProperties(list, {
      clientHeight: { value: 200 },
      scrollHeight: { get: () => list.querySelectorAll('.log-item').length * 1000 },
    })
    latest().receive({ type: 'initial', logs: [entry('initial')] })
    await nextTick()
    await nextTick()
    const scroll = (top: number) => {
      list.scrollTop = top
      list.dispatchEvent(new Event('scroll'))
    }
    const append = async (id = 'appended') => {
      latest().receive({ type: 'append', logs: [entry(id)] })
      await nextTick()
      await nextTick()
    }
    const toggle = async (enabled: boolean) => {
      const input = root.querySelector<HTMLInputElement>('[data-yui-guide-id="log-auto-scroll"]')!
      input.checked = enabled
      input.dispatchEvent(new Event('change'))
      await nextTick()
      await nextTick()
    }
    return { list, scroll, append, toggle }
  }

  it('shows the latest initial logs and follows new logs while at the bottom', async () => {
    const { list, scroll, append } = await scrollableViewer()
    expect(list.scrollTop).toBe(1000)
    scroll(800)
    await append()
    expect(list.scrollTop).toBe(2000)
  })

  it('keeps the reading position across new batches after scrolling up', async () => {
    const { list, scroll, append } = await scrollableViewer()
    scroll(300)
    await append('second')
    await append('third')
    expect(list.querySelectorAll('.log-item')).toHaveLength(3)
    expect(list.scrollTop).toBe(300)
  })

  it('resumes following when the user scrolls back to the bottom', async () => {
    const { list, scroll, append } = await scrollableViewer()
    scroll(300)
    await append('second')
    scroll(1800)
    await append('third')
    expect(list.scrollTop).toBe(3000)
  })

  it('tolerates fractional scroll positions near the bottom', async () => {
    const { list, scroll, append } = await scrollableViewer()
    scroll(799.5)
    await append()
    expect(list.scrollTop).toBe(2000)
  })

  it('honors the switch and resumes immediately when enabled again', async () => {
    const { list, scroll, append, toggle } = await scrollableViewer()
    await toggle(false)
    scroll(800)
    await append('second')
    expect(list.scrollTop).toBe(800)
    scroll(300)
    await toggle(true)
    expect(list.scrollTop).toBe(2000)
    await append('third')
    expect(list.scrollTop).toBe(3000)
  })
})
