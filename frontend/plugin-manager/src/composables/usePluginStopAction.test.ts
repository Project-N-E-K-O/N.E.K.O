// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStopAction } from './usePluginStopAction'

const store = vi.hoisted(() => ({ stop: vi.fn() }))
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => store }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/utils/request', () => ({ formatHttpError: () => '' }))

const apps: Array<ReturnType<typeof createApp>> = []
function useTwice() {
  const actions: Array<ReturnType<typeof usePluginStopAction>> = []
  for (let n = 0; n < 2; n++) {
    const app = createApp(defineComponent({ setup: () => { actions.push(usePluginStopAction()); return () => h('i') } }))
    app.mount(document.createElement('div'))
    apps.push(app)
  }
  return actions as [ReturnType<typeof usePluginStopAction>, ReturnType<typeof usePluginStopAction>]
}
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  vi.restoreAllMocks()
  store.stop.mockReset()
})

describe('usePluginStopAction', () => {
  it('shares one confirmation and one stop between two buttons of the same plugin', async () => {
    let confirmDone: () => void = () => {}
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockImplementation(
      () => new Promise((resolve) => { confirmDone = () => resolve('confirm' as never) }) as never,
    )
    vi.spyOn(ElMessage, 'success').mockImplementation(() => ({ close: () => {} }) as never)
    let stopDone: () => void = () => {}
    store.stop.mockImplementation(() => new Promise<void>((resolve) => { stopDone = resolve }))
    const [top, tab] = useTwice()
    const first = top.stop('demo')
    const second = tab.stop('demo')
    expect(second).toBe(first)
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(top.isStopping('demo')).toBe(false)
    confirmDone()
    await nextTick()
    await nextTick()
    expect(tab.isStopping('demo')).toBe(true)
    stopDone()
    await first
    expect(store.stop).toHaveBeenCalledTimes(1)
    expect(top.isStopping('demo')).toBe(false)
    // Done: a later click asks again.
    confirm.mockRejectedValueOnce('cancel')
    await tab.stop('demo')
    expect(confirm).toHaveBeenCalledTimes(2)
  })
})
