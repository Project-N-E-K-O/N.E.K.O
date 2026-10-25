import { computed, ref, shallowRef, watch } from 'vue'
import type { ExportItem } from '@/api/runs'
import {
  buildJsonLines,
  jsonAncestorIds,
  visibleJsonLines,
  isAutoPreviewJson,
  sameJsonValue,
} from '@/utils/runJson'

export const RUN_OUTPUT_PAGE_SIZE = 400

/** One reading session per export, shared by the inline and expanded views.
 * Equal snapshots keep both the outline and the user's position intact. */
export function createRunOutputReader(initial: ExportItem) {
  const item = shallowRef(initial)
  const isJson = computed(() => item.value.type === 'json')
  const jsonValue = computed(() =>
    item.value.json !== undefined ? item.value.json : item.value.json_data
  )
  const lines = shallowRef<ReturnType<typeof buildJsonLines>>([])
  const searchText = shallowRef<string[]>([])
  const needsLoad = ref(false)
  const collapsed = ref(new Set<number>())
  const query = ref('')
  const needle = ref('')
  const matchIndex = ref(0)
  const page = ref(0)
  let prepared = false
  let preparedValue: unknown
  let requested = false
  let timer: ReturnType<typeof setTimeout> | undefined

  function prepare(explicit = false) {
    if (!isJson.value) {
      lines.value = []
      needsLoad.value = false
      return
    }
    if (!explicit && !requested && !isAutoPreviewJson(jsonValue.value)) {
      needsLoad.value = true
      prepared = false
      preparedValue = undefined
      lines.value = []
      searchText.value = []
      return
    }
    if (prepared && sameJsonValue(preparedValue, jsonValue.value)) return
    requested ||= explicit
    const outline = buildJsonLines(jsonValue.value)
    preparedValue = jsonValue.value
    prepared = true
    needsLoad.value = false
    collapsed.value = new Set(
      outline.filter((line) => line.kind === 'open' && line.depth >= 3).map((line) => line.id)
    )
    matchIndex.value = 0
    page.value = 0
    searchText.value = outline.map((line) =>
      line.kind === 'close'
        ? ''
        : `${line.key ?? ''} ${line.kind === 'open' ? '' : line.text}`.toLocaleLowerCase()
    )
    lines.value = outline
  }
  const stopPrepare = watch([isJson, jsonValue], () => prepare(), {
    immediate: true,
    flush: 'sync',
  })
  const stopQuery = watch(query, (value) => {
    clearTimeout(timer)
    const update = () => {
      matchIndex.value = 0
      needle.value = value.trim().toLocaleLowerCase()
    }
    if (!value.trim()) update()
    else timer = setTimeout(update, 150)
  })
  const matches = computed(() =>
    needle.value
      ? lines.value
          .filter(
            (line) => line.kind !== 'close' && searchText.value[line.id]?.includes(needle.value)
          )
          .map((line) => line.id)
      : []
  )
  const matchSet = computed(() => new Set(matches.value))
  const activeMatch = computed(() => matches.value[matchIndex.value])
  const revealed = computed(() =>
    activeMatch.value === undefined
      ? new Set<number>()
      : jsonAncestorIds(lines.value, activeMatch.value)
  )
  const visible = computed(() => visibleJsonLines(lines.value, collapsed.value, revealed.value))
  const pageLines = computed(() =>
    visible.value.slice(page.value * RUN_OUTPUT_PAGE_SIZE, (page.value + 1) * RUN_OUTPUT_PAGE_SIZE)
  )
  const stopPage = watch(visible, (value) => {
    page.value = Math.min(
      page.value,
      Math.max(0, Math.ceil(value.length / RUN_OUTPUT_PAGE_SIZE) - 1)
    )
  })
  function isCollapsed(id: number) {
    return collapsed.value.has(id) && !revealed.value.has(id)
  }
  function toggle(id: number) {
    const shouldCollapse = !isCollapsed(id)
    if (revealed.value.has(id)) {
      query.value = ''
      needle.value = ''
    }
    const next = new Set(collapsed.value)
    if (shouldCollapse) next.add(id)
    else next.delete(id)
    collapsed.value = next
  }
  function setCollapsed(value: boolean) {
    query.value = ''
    needle.value = ''
    collapsed.value = new Set(
      value ? lines.value.filter((line) => line.kind === 'open').map((line) => line.id) : []
    )
    page.value = 0
  }
  function moveMatch(direction: number) {
    if (matches.value.length)
      matchIndex.value =
        (matchIndex.value + direction + matches.value.length) % matches.value.length
  }
  return {
    item,
    isJson,
    jsonValue,
    lines,
    query,
    needle,
    matches,
    matchSet,
    activeMatch,
    matchIndex,
    collapsed,
    visible,
    pageLines,
    page,
    needsLoad,
    isCollapsed,
    toggle,
    setCollapsed,
    moveMatch,
    loadPreview: () => prepare(true),
    update: (value: ExportItem) => {
      item.value = value
    },
    dispose() {
      clearTimeout(timer)
      stopPrepare()
      stopQuery()
      stopPage()
    },
  }
}
export type RunOutputReader = ReturnType<typeof createRunOutputReader>
