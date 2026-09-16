// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import { needsPageScroll, useConfigEditorLayout } from './useConfigEditorLayout'

const cleanup: (() => void)[] = []
afterEach(() => {
  cleanup.splice(0).forEach((fn) => fn())
  vi.restoreAllMocks()
})

describe('available configuration editing space', () => {
  it('uses separate entry and exit thresholds to avoid repeated mode changes', () => {
    expect(needsPageScroll(159, false)).toBe(true)
    expect(needsPageScroll(160, false)).toBe(false)
    expect(needsPageScroll(180, true)).toBe(true)
    expect(needsPageScroll(200, true)).toBe(false)
  })

  async function fixture() {
    const host = document.createElement('main')
    host.dataset.yuiGuideId = 'plugin-main'
    document.body.append(host)
    let height = 600
    Object.defineProperty(host, 'clientHeight', { get: () => height })
    vi.spyOn(host, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 800, 600))
    const pageReset = vi.spyOn(host, 'scrollTo').mockImplementation(() => {})
    let layout!: ReturnType<typeof useConfigEditorLayout>
    const editor = ref<HTMLElement | null>(null),
      content = ref<HTMLElement | null>(null)
    const toolbar = ref<HTMLElement | null>(null),
      workspace = ref<HTMLElement | null>(null)
    const navigation = ref<HTMLElement | null>(null),
      footer = ref<HTMLElement | null>(null)
    const app = createApp({
      setup() {
        layout = useConfigEditorLayout({ editor, content, toolbar, workspace, navigation, footer })
        return () =>
          h('div', { ref: editor }, [
            h('div', { ref: toolbar }),
            h(
              'div',
              {
                ref: workspace,
                style: { display: 'flex', flexDirection: 'column', rowGap: '8px' },
              },
              [h('nav', { ref: navigation }), h('div', { ref: content })]
            ),
            h('footer', { ref: footer }),
          ])
      },
    })
    app.mount(host)
    cleanup.push(() => {
      app.unmount()
      host.remove()
    })
    vi.spyOn(editor.value!, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList)
    vi.spyOn(editor.value!, 'getBoundingClientRect').mockImplementation(
      () => new DOMRect(0, 100 - host.scrollTop, 800, 500)
    )
    for (const element of [toolbar.value!, footer.value!])
      Object.defineProperty(element, 'offsetHeight', { value: 60, configurable: true })
    Object.defineProperty(navigation.value!, 'offsetHeight', { value: 40 })
    const innerReset = vi.spyOn(content.value!, 'scrollTo').mockImplementation(() => {})
    const resize = (next: number) => {
      height = next
      window.dispatchEvent(new Event('resize'))
    }
    resize(height)
    return {
      layout,
      host,
      content: content.value!,
      footer: footer.value!,
      resize,
      pageReset,
      innerReset,
    }
  }

  it('responds to actual chrome height and resets the active page scroll when switching profiles', async () => {
    const f = await fixture()
    f.resize(400)
    await vi.waitFor(() => expect(f.layout.pageScroll.value).toBe(true))
    await f.layout.resetScroll()
    expect(f.pageReset).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })
    expect(f.innerReset).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })
    // Unscrolled origin is stable even when the page is scrolled down.
    f.host.scrollTop = 300
    f.resize(430)
    await vi.waitFor(() => expect(f.layout.pageScroll.value).toBe(true))
    f.resize(600)
    await vi.waitFor(() => expect(f.layout.pageScroll.value).toBe(false))
  })

  it.each([false, true])(
    'reveals focus without native scrolling (page mode: %s)',
    async (pageMode) => {
      const f = await fixture()
      f.resize(pageMode ? 400 : 600)
      await new Promise((resolve) => requestAnimationFrame(resolve))
      await nextTick()
      expect(f.layout.pageScroll.value).toBe(pageMode)
      Object.defineProperty(f.content, 'clientHeight', { value: 200 })
      vi.spyOn(f.content, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 100, 800, 200))
      const input = document.createElement('input')
      f.content.append(input)
      const scroller = pageMode ? f.host : f.content
      const scroll = pageMode ? f.pageReset : f.innerReset
      const top = pageMode ? 0 : 100
      let inputTop = top + scroller.clientHeight + 50
      vi.spyOn(input, 'getBoundingClientRect').mockImplementation(
        () => new DOMRect(0, inputTop, 100, 30)
      )
      scroll.mockClear()
      input.focus({ preventScroll: true })
      await nextTick()
      expect(document.activeElement).toBe(input)
      expect(scroll).toHaveBeenLastCalledWith({ top: 80, behavior: 'auto' })

      input.blur()
      inputTop = top - 40
      scroll.mockClear()
      input.focus({ preventScroll: true })
      await nextTick()
      expect(scroll).toHaveBeenLastCalledWith({ top: -40, behavior: 'auto' })
    }
  )

  it('degrades when wrapped save actions grow without changing viewport size', async () => {
    const f = await fixture()
    f.resize(500)
    await new Promise((resolve) => requestAnimationFrame(resolve))
    expect(f.layout.pageScroll.value).toBe(false)
    Object.defineProperty(f.footer, 'offsetHeight', { value: 180 })
    f.resize(500)
    await vi.waitFor(() => expect(f.layout.pageScroll.value).toBe(true))
  })
})
