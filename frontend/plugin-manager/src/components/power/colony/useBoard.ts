/**
 * The board of the familiars, kept up to date for a page.
 *
 * Asked for every couple of seconds with the revision that is shown: the
 * host answers "unchanged" in a few milliseconds when nothing moved. Slower
 * once it could not be read, and not at all while nobody looks at the page.
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { consoleErrorCode, getBoard } from '@/api/console'
import type { Board } from '@/api/console'

const POLL_MS = 2000
const POLL_FAILED_MS = 15000

/** `missing` is what to say when the service has no board yet; `describe` words any other failure. */
export function useBoard(missing: () => string, describe: (failure: unknown) => string) {
  const board = ref<Board | null>(null)
  const loaded = ref(false)
  const error = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null
  let alive = true
  let turn = 0

  async function load() {
    const mine = ++turn
    if (timer) clearTimeout(timer)
    timer = null
    let wait = POLL_MS
    try {
      const answer = await getBoard(board.value?.revision)
      if (mine !== turn) return
      // "Unchanged" carries nothing else: what is shown stays.
      if (!answer.unchanged) board.value = answer
      error.value = ''
    } catch (failure) {
      if (mine !== turn) return
      // A service from before the board was added has no such route.
      const none = (failure as { response?: { status?: number } })?.response?.status === 404 && !consoleErrorCode(failure)
      error.value = none ? missing() : describe(failure)
      wait = POLL_FAILED_MS
    } finally {
      if (mine === turn) {
        loaded.value = true
        if (alive && !document.hidden) timer = setTimeout(() => void load(), wait)
      }
    }
  }

  function onVisibility() {
    if (!document.hidden && alive) void load()
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    void load()
  })
  onBeforeUnmount(() => {
    alive = false
    document.removeEventListener('visibilitychange', onVisibility)
    if (timer) clearTimeout(timer)
  })

  return { board, loaded, error, load }
}
