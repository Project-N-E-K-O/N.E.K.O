import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const SECTION_EASING = 'cubic-bezier(0.22, 1, 0.36, 1)'

function onceTransitionEnd(node: HTMLElement, done: () => void) {
  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    node.removeEventListener('transitionend', onTransitionEnd)
    done()
  }
  const onTransitionEnd = (event: Event) => {
    if (event.target === node) {
      finish()
    }
  }
  node.addEventListener('transitionend', onTransitionEnd)
  window.setTimeout(finish, 420)
}

export function useAnimatedGridTransition() {
  const prefersReducedMotion = ref(false)
  let mediaQuery: MediaQueryList | null = null
  let cleanupListener: (() => void) | null = null

  const itemStaggerEnabled = computed(() => !prefersReducedMotion.value)

  function updateReducedMotionPreference(event?: MediaQueryListEvent) {
    prefersReducedMotion.value = event ? event.matches : !!mediaQuery?.matches
  }

  onMounted(() => {
    if (typeof window === 'undefined' || !window.matchMedia) {
      return
    }
    mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    updateReducedMotionPreference()

    const listener = (event: MediaQueryListEvent) => updateReducedMotionPreference(event)
    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', listener)
      cleanupListener = () => mediaQuery?.removeEventListener('change', listener)
    } else {
      mediaQuery.addListener(listener)
      cleanupListener = () => mediaQuery?.removeListener(listener)
    }
  })

  onBeforeUnmount(() => {
    cleanupListener?.()
  })

  function itemMotionStyle(index: number) {
    const delay = itemStaggerEnabled.value ? Math.min(index, 7) * 18 : 0
    return {
      '--item-stagger-delay': `${delay}ms`,
    }
  }

  function pinLeavingItem(element: Element) {
    if (prefersReducedMotion.value) {
      return
    }
    const node = element as HTMLElement
    node.style.left = `${node.offsetLeft}px`
    node.style.top = `${node.offsetTop}px`
    node.style.width = `${node.offsetWidth}px`
    node.style.height = `${node.offsetHeight}px`
  }

  function clearLeavingItemStyles(element: Element) {
    const node = element as HTMLElement
    node.style.left = ''
    node.style.top = ''
    node.style.width = ''
    node.style.height = ''
  }

  function beforeSectionEnter(element: Element) {
    if (prefersReducedMotion.value) {
      return
    }
    const node = element as HTMLElement
    node.style.height = '0px'
    node.style.opacity = '0'
    node.style.transform = 'translateY(10px) scale(0.985)'
    node.style.overflow = 'clip'
  }

  function enterSection(element: Element, done: () => void) {
    if (prefersReducedMotion.value) {
      done()
      return
    }

    const node = element as HTMLElement
    const targetHeight = `${node.scrollHeight}px`

    requestAnimationFrame(() => {
      node.style.transition = [
        `height 300ms ${SECTION_EASING}`,
        'opacity 220ms ease',
        `transform 300ms ${SECTION_EASING}`,
      ].join(', ')
      node.style.height = targetHeight
      node.style.opacity = '1'
      node.style.transform = 'translateY(0) scale(1)'
      onceTransitionEnd(node, done)
    })
  }

  function afterSectionEnter(element: Element) {
    const node = element as HTMLElement
    node.style.height = ''
    node.style.opacity = ''
    node.style.transform = ''
    node.style.overflow = ''
    node.style.transition = ''
  }

  function beforeSectionLeave(element: Element) {
    if (prefersReducedMotion.value) {
      return
    }
    const node = element as HTMLElement
    node.style.height = `${node.scrollHeight}px`
    node.style.opacity = '1'
    node.style.transform = 'translateY(0) scale(1)'
    node.style.overflow = 'clip'
  }

  function leaveSection(element: Element, done: () => void) {
    if (prefersReducedMotion.value) {
      done()
      return
    }

    const node = element as HTMLElement

    requestAnimationFrame(() => {
      node.style.transition = [
        `height 260ms ${SECTION_EASING}`,
        'opacity 180ms ease',
        `transform 260ms ${SECTION_EASING}`,
      ].join(', ')
      node.style.height = '0px'
      node.style.opacity = '0'
      node.style.transform = 'translateY(-8px) scale(0.985)'
      onceTransitionEnd(node, done)
    })
  }

  function afterSectionLeave(element: Element) {
    const node = element as HTMLElement
    node.style.height = ''
    node.style.opacity = ''
    node.style.transform = ''
    node.style.overflow = ''
    node.style.transition = ''
  }

  return {
    prefersReducedMotion,
    itemMotionStyle,
    pinLeavingItem,
    clearLeavingItemStyles,
    beforeSectionEnter,
    enterSection,
    afterSectionEnter,
    beforeSectionLeave,
    leaveSection,
    afterSectionLeave,
  }
}
