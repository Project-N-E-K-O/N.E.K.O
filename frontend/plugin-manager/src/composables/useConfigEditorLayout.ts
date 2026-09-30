import { nextTick, onBeforeUnmount, onMounted, ref, shallowRef, type Ref } from 'vue'
import { useEventListener, useResizeObserver } from '@vueuse/core'

// Reserve room for a label, input and field actions. A separate return threshold
// prevents scrollbar/wrapping changes from repeatedly switching layout modes.
export function needsPageScroll(available: number, current: boolean): boolean {
  return available < (current ? 200 : 160)
}

interface LayoutElements {
  editor: Ref<HTMLElement | null>
  content: Ref<HTMLElement | null>
  toolbar: Ref<HTMLElement | null>
  workspace: Ref<HTMLElement | null>
  navigation: Ref<HTMLElement | null>
  footer: Ref<HTMLElement | null>
}

export function useConfigEditorLayout(elements: LayoutElements) {
  const pageScroll = ref(false)
  const pageHost = shallowRef<HTMLElement | null>(null)
  const editorHost = shallowRef<HTMLElement | null>(null)
  let frame = 0
  let disposed = false
  let changingMode = false

  function scrollContainer() {
    return pageScroll.value ? pageHost.value : elements.content.value
  }

  function scrollToElement(target: HTMLElement, alignStart = false) {
    const scroller = scrollContainer()
    if (!scroller) return
    const bounds = scroller.getBoundingClientRect()
    const item = target.getBoundingClientRect()
    const top = scroller === document.scrollingElement ? 0 : bounds.top + (scroller.clientTop || 0)
    const bottom = top + scroller.clientHeight
    let delta = 0
    if (alignStart || item.top < top || item.height > scroller.clientHeight) delta = item.top - top
    else if (item.bottom > bottom) delta = item.bottom - bottom
    if (delta) scroller.scrollTo({ top: scroller.scrollTop + delta, behavior: 'auto' })
  }

  function revealFocusedField() {
    const focused = document.activeElement
    if (focused instanceof HTMLElement && elements.content.value?.contains(focused))
      scrollToElement(focused)
  }

  async function measure() {
    const editor = elements.editor.value
    const host = pageHost.value
    if (disposed || changingMode || !editor?.getClientRects().length || !host) return
    const hostStyle = getComputedStyle(host)
    const workspace = elements.workspace.value
    const horizontalNavigation = workspace && getComputedStyle(workspace).flexDirection === 'column'
    const navigationHeight =
      horizontalNavigation && elements.navigation.value
        ? elements.navigation.value.offsetHeight +
          (parseFloat(getComputedStyle(workspace!).rowGap) || 0)
        : 0
    // Use the page's viewport and the editor's unscrolled origin, not its current
    // height: page mode expands the form and must not change this measurement.
    const origin =
      editor.getBoundingClientRect().top - host.getBoundingClientRect().top + host.scrollTop
    const available =
      host.clientHeight -
      (parseFloat(hostStyle.paddingBottom) || 0) -
      origin -
      (elements.toolbar.value?.offsetHeight || 0) -
      (elements.footer.value?.offsetHeight || 0) -
      navigationHeight -
      (parseFloat(getComputedStyle(editor).paddingTop) || 0) -
      2
    const next = needsPageScroll(available, pageScroll.value)
    if (next !== pageScroll.value) {
      const pane = elements.content.value
      const focused = document.activeElement
      const anchor =
        focused instanceof HTMLElement && pane?.contains(focused)
          ? focused
          : [...(pane?.querySelectorAll<HTMLElement>('[data-config-path]') || [])]
              .filter((row) => !row.querySelector('[data-config-path]'))
              .find(
                (row) =>
                  row.getClientRects().length &&
                  row.getBoundingClientRect().bottom >
                    (scrollContainer()?.getBoundingClientRect().top || 0)
              )
      changingMode = true
      pageScroll.value = next
      await nextTick()
      if (disposed) return
      if (!next) host.scrollTo({ top: 0, behavior: 'auto' })
      if (anchor?.isConnected) scrollToElement(anchor)
      changingMode = false
    }
    revealFocusedField()
  }

  function scheduleMeasure() {
    if (disposed || frame) return
    frame = requestAnimationFrame(() => {
      frame = 0
      void measure()
    })
  }

  async function resetScroll() {
    await nextTick()
    if (disposed) return
    elements.content.value?.scrollTo({ top: 0, behavior: 'auto' })
    if (pageScroll.value) pageHost.value?.scrollTo({ top: 0, behavior: 'auto' })
  }

  onMounted(() => {
    // Siblings such as model bindings load asynchronously. In page mode they
    // change the editor's origin without changing the editor's own size.
    editorHost.value = elements.editor.value?.parentElement || null
    // This is the scroll viewport owned by AppLayout, not a content-sized card.
    pageHost.value =
      elements.editor.value?.closest<HTMLElement>('[data-yui-guide-id="plugin-main"]') ||
      (document.scrollingElement as HTMLElement | null)
    scheduleMeasure()
  })
  useResizeObserver(
    [
      elements.editor,
      elements.toolbar,
      elements.workspace,
      elements.navigation,
      elements.footer,
      pageHost,
      editorHost,
    ],
    scheduleMeasure
  )
  useEventListener(window, 'resize', scheduleMeasure)
  useEventListener(elements.content, 'focusin', () => {
    void nextTick(revealFocusedField)
  })
  onBeforeUnmount(() => {
    disposed = true
    cancelAnimationFrame(frame)
  })

  return { pageScroll, scrollContainer, scrollToElement, resetScroll }
}
