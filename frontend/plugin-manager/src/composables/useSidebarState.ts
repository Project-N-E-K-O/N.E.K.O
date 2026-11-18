import { computed, onBeforeUnmount, ref, watch } from 'vue'

const SIDEBAR_COLLAPSED_KEY = 'neko-sidebar-collapsed'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true'
  } catch {
    return false
  }
}

/** Mobile compact mode must not overwrite the user's desktop preference. */
export function useSidebarState() {
  const desktopCollapsed = ref(readCollapsed())
  const media = window.matchMedia('(max-width: 640px)')
  const narrow = ref(media.matches)
  const collapsed = computed(() => narrow.value || desktopCollapsed.value)
  function updateNarrow(event: MediaQueryListEvent) {
    narrow.value = event.matches
  }
  media.addEventListener('change', updateNarrow)
  onBeforeUnmount(() => media.removeEventListener('change', updateNarrow))
  watch(desktopCollapsed, (value) => {
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(value))
    } catch {
      // Storage may be disabled in an embedded panel; the current session still works.
    }
  })
  function toggleCollapsed() {
    if (!narrow.value) desktopCollapsed.value = !desktopCollapsed.value
  }
  return { collapsed, narrow, toggleCollapsed }
}
