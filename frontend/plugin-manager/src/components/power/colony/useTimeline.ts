/**
 * What happened among the familiars, kept up to date for a page.
 *
 * The host hands the timeline out forwards, a page at a time. A page that
 * opens wants what happened lately: it asks where to begin for that
 * (`getTimelineStart`), reads on from there to the end, and then asks every
 * couple of seconds for what came since (answered in a fraction of a
 * millisecond when nothing did). `fromBeginning` reads everything there is
 * instead. No more than `KEPT_ENTRIES` are held: the oldest are let go.
 * While nobody looks at the page nothing is asked for; it reads on from
 * where it stopped when they look again.
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { getTimeline, getTimelineStart } from '@/api/console'
import type { TimelineEntry } from '@/api/console'

export const KEPT_ENTRIES = 3000
const PAGE_ROWS = 1000
const POLL_MS = 2000
const POLL_FAILED_MS = 15000

/**
 * `wholeAtFirst` says whether a page begins with everything there is and not
 * with the last stretch: asked each time it begins anew.
 */
export function useTimeline(
  project: () => string | null, describe: (failure: unknown) => string, wholeAtFirst: () => boolean = () => false,
) {
  const entries = shallowRef<TimelineEntry[]>([])
  const loading = ref(true)
  const error = ref('')
  /** Whether what is held begins where the record begins. */
  const whole = ref(false)
  /** How many entries were let go at the front since it began: whoever keeps a place among them moves it by as many. */
  const dropped = ref(0)
  // Whether a read is under way: a second one beside it would be given the same page and add it twice.
  let reading = false
  let after: string | undefined
  // Whether it is known where to begin: asked first, and again after a failure, so that a
  // failure never makes a page read everything there ever was.
  let begun = false
  let timer: ReturnType<typeof setTimeout> | null = null
  let alive = true
  let turn = 0

  function add(page: TimelineEntry[]) {
    if (!page.length) return
    const joined = [...entries.value, ...page]
    if (joined.length > KEPT_ENTRIES) {
      dropped.value += joined.length - KEPT_ENTRIES
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
    reading = true
    try {
      if (!begun) {
        const start = await getTimelineStart()
        if (mine !== turn) return
        after = start.after || undefined
        // Nothing lies before where it begins: what is read is all there is.
        whole.value = !start.after
        begun = true
      }
      for (;;) {
        const page = await getTimeline({ project: project(), after, limit: PAGE_ROWS })
        if (mine !== turn) return
        add(page.entries)
        after = page.next
        // The rest waits while nobody looks: it is read from here when they do.
        if (!page.more || document.hidden) break
      }
      error.value = ''
    } catch (failure) {
      if (mine !== turn) return
      error.value = describe(failure)
      wait = POLL_FAILED_MS
    } finally {
      if (mine === turn) {
        reading = false
        loading.value = false
        // Not asked for while nobody looks at the page.
        if (alive && !document.hidden) timer = setTimeout(() => void readOn(mine), wait)
      }
    }
  }

  function begin(fromStart: boolean) {
    const mine = ++turn
    if (timer) clearTimeout(timer)
    timer = null
    entries.value = []
    dropped.value = 0
    loading.value = true
    whole.value = fromStart
    after = undefined
    begun = fromStart
    return readOn(mine)
  }

  function onVisibility() {
    // Looked at again: read on now, unless a read is under way or one is due anyway.
    if (document.hidden || !alive || timer || reading) return
    void readOn(turn)
  }

  watch(project, () => void begin(wholeAtFirst()))
  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    void begin(wholeAtFirst())
  })
  onBeforeUnmount(() => {
    alive = false
    turn += 1
    document.removeEventListener('visibilitychange', onVisibility)
    if (timer) clearTimeout(timer)
  })

  return { entries, loading, error, whole, dropped, fromBeginning: () => begin(true) }
}
