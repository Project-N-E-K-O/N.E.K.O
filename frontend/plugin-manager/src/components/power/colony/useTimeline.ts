/**
 * What happened among the familiars, kept up to date for a page.
 *
 * The host hands the timeline out forwards, a page at a time. A page that
 * opens wants what happened lately: it asks where to begin for that
 * (`getTimelineStart`), reads on from there to the end, and then asks every
 * couple of seconds for what came since (answered in a fraction of a
 * millisecond when nothing did). `fromBeginning` reads everything there is
 * instead. No more than `KEPT_ENTRIES` are held: the oldest are let go.
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { getTimeline, getTimelineStart } from '@/api/console'
import type { TimelineEntry } from '@/api/console'

export const KEPT_ENTRIES = 3000
const PAGE_ROWS = 1000
const POLL_MS = 2000
const POLL_FAILED_MS = 15000

export function useTimeline(project: () => string | null, describe: (failure: unknown) => string) {
  const entries = shallowRef<TimelineEntry[]>([])
  const loading = ref(true)
  const error = ref('')
  /** Whether what is held begins where the record begins. */
  const whole = ref(false)
  let after: string | undefined
  let timer: ReturnType<typeof setTimeout> | null = null
  let alive = true
  let turn = 0

  function add(page: TimelineEntry[]) {
    if (!page.length) return
    const joined = [...entries.value, ...page]
    if (joined.length > KEPT_ENTRIES) {
      entries.value = joined.slice(joined.length - KEPT_ENTRIES)
      whole.value = false
    } else {
      entries.value = joined
    }
  }

  /** Read on from where the last page stopped, to the end; then again in a while. */
  async function readOn(mine: number) {
    timer = null
    let wait = POLL_MS
    try {
      for (;;) {
        const page = await getTimeline({ project: project(), after, limit: PAGE_ROWS })
        if (mine !== turn) return
        add(page.entries)
        after = page.next
        if (!page.more) break
      }
      error.value = ''
    } catch (failure) {
      if (mine !== turn) return
      error.value = describe(failure)
      wait = POLL_FAILED_MS
    } finally {
      if (mine === turn) {
        loading.value = false
        // Not asked for while nobody looks at the page.
        if (alive && !document.hidden) timer = setTimeout(() => void readOn(mine), wait)
      }
    }
  }

  async function begin(fromStart: boolean) {
    const mine = ++turn
    if (timer) clearTimeout(timer)
    timer = null
    entries.value = []
    loading.value = true
    whole.value = fromStart
    after = undefined
    if (!fromStart) {
      try {
        const start = await getTimelineStart()
        if (mine !== turn) return
        after = start.after || undefined
        // Nothing lies before where it begins: what is read is all there is.
        whole.value = !start.after
      } catch {
        // A service without that route: read from the beginning, as it always could.
        if (mine !== turn) return
        whole.value = true
      }
    }
    await readOn(mine)
  }

  function onVisibility() {
    if (document.hidden || !alive || timer) return
    void readOn(turn)
  }

  watch(project, () => void begin(false))
  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    void begin(false)
  })
  onBeforeUnmount(() => {
    alive = false
    turn += 1
    document.removeEventListener('visibilitychange', onVisibility)
    if (timer) clearTimeout(timer)
  })

  return { entries, loading, error, whole, fromBeginning: () => begin(true) }
}
