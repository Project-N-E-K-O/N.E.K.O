import { nextTick, readonly, ref, watch, type Ref, type WatchSource } from 'vue'
import { useResizeObserver } from '@vueuse/core'

/**
 * 按内容决定一行按钮组是否只显示图标：带文字时各组排不进容器的一行就收起文字，
 * 并记下带文字所需宽度；容器重新宽到该值才恢复文字。文案会变（语言、登录态、
 * 忙碌态等）时由 contentKey 触发重新测量。
 */
export function useCollapsibleLabels(
  container: Ref<HTMLElement | null>,
  groups: Ref<HTMLElement | null>[],
  contentKey: WatchSource,
) {
  const compact = ref(false)
  let labelledWidth = 0
  let observedWidth = -1

  function availableWidth(host: HTMLElement) {
    const style = getComputedStyle(host)
    return host.clientWidth - Number.parseFloat(style.paddingLeft) - Number.parseFloat(style.paddingRight)
  }

  function labelledRowWidth(host: HTMLElement) {
    const elements = groups.map(group => group.value).filter((element): element is HTMLElement => element !== null)
    const gap = Number.parseFloat(getComputedStyle(host).columnGap) || 0
    // 组在独占一行时会被压缩，取 scrollWidth 才是内容本身的宽度。
    const content = elements.reduce(
      (sum, element) => sum + Math.max(element.getBoundingClientRect().width, element.scrollWidth),
      0,
    )
    return Math.ceil(content + gap * Math.max(elements.length - 1, 0))
  }

  function update() {
    const host = container.value
    if (!host) return
    const available = availableWidth(host)
    if (compact.value) {
      if (available >= labelledWidth) compact.value = false
      return
    }
    const needed = labelledRowWidth(host)
    if (needed > available) {
      labelledWidth = needed
      compact.value = true
    }
  }

  // 只响应宽度变化：收起文字会改变高度，按高度触发会来回切换。
  useResizeObserver(container, (entries) => {
    const width = entries[0]?.contentRect.width ?? 0
    if (width === observedWidth) return
    observedWidth = width
    update()
  })
  // 字体晚到、徽标数字变化等会在容器宽度不变时撑宽按钮组；收起状态下 update 只看容器宽度，不会来回切换。
  useResizeObserver(groups, update)

  watch(contentKey, async () => {
    compact.value = false
    labelledWidth = 0
    await nextTick()
    update()
  }, { flush: 'post' })

  return { compact: readonly(compact) }
}
