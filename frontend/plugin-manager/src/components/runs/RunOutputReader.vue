<template>
  <div ref="reader" class="output-reader" :class="{ 'output-reader--fullscreen': fullScreen }">
    <div class="reader-toolbar">
      <template v-if="isJson && !needsLoad">
        <el-button text size="small" @click="setCollapsed(false)">{{
          t('runs.expandAll')
        }}</el-button>
        <el-button text size="small" @click="setCollapsed(true)">{{
          t('runs.collapseAll')
        }}</el-button>
        <div class="reader-search">
          <input
            v-model="query"
            type="search"
            :aria-label="t('runs.findOutput')"
            :placeholder="t('runs.findOutput')"
            @keydown.enter.prevent="moveMatch($event.shiftKey ? -1 : 1)"
            @keydown.esc="query = ''"
          />
          <span v-if="query" class="reader-search__count" role="status">{{
            matches.length ? `${matchIndex + 1}/${matches.length}` : t('runs.noMatches')
          }}</span>
          <button
            v-if="query"
            type="button"
            :disabled="!matches.length"
            :aria-label="t('runs.previousMatch')"
            @click="moveMatch(-1)"
          >
            <el-icon><ArrowUp /></el-icon>
          </button>
          <button
            v-if="query"
            type="button"
            :disabled="!matches.length"
            :aria-label="t('runs.nextMatch')"
            @click="moveMatch(1)"
          >
            <el-icon><ArrowDown /></el-icon>
          </button>
        </div>
      </template>
      <div class="reader-tools">
        <el-button text size="small" :icon="copied ? Check : CopyDocument" @click="copyOutput">{{
          t(copied ? 'runs.copied' : 'runs.copyOutput')
        }}</el-button>
        <el-button
          v-if="!fullScreen"
          text
          size="small"
          :icon="FullScreen"
          @click="$emit('expand')"
          >{{ t('runs.expandReading') }}</el-button
        >
      </div>
    </div>
    <div v-if="needsLoad" class="reader-deferred" role="status">
      <p>{{ t('runs.largeOutput') }}</p>
      <el-button size="small" @click="loadPreview">{{ t('runs.loadOutput') }}</el-button>
    </div>
    <div v-else-if="isJson" class="output-code" :aria-label="t('runs.outputTitle')">
      <div
        v-for="line in pageLines"
        :key="line.id"
        class="json-line"
        :class="{
          'json-line--match': matchSet.has(line.id),
          'json-line--active': !!query && line.id === activeMatch,
        }"
        :data-json-line="line.id"
        :style="{ '--json-depth': Math.min(line.depth, 12) }"
      >
        <span class="json-line__number" aria-hidden="true">{{ line.id + 1 }}</span>
        <span class="json-line__content">
          <button
            v-if="line.kind === 'open'"
            type="button"
            class="json-toggle"
            :aria-label="
              t(isCollapsed(line.id) ? 'runs.expandNode' : 'runs.collapseNode', {
                key: line.key ?? '$',
              })
            "
            :aria-expanded="!isCollapsed(line.id)"
            @click="toggle(line.id)"
          >
            <el-icon :class="{ 'json-toggle--open': !isCollapsed(line.id) }"
              ><ArrowRight
            /></el-icon>
          </button>
          <span v-else class="json-toggle-space" aria-hidden="true"></span>
          <span class="json-line__tokens"
            ><span v-if="line.key !== null" class="json-key"
              >{{ line.indexKey ? line.key : JSON.stringify(line.key)
              }}<span class="json-punctuation">: </span></span
            ><span :class="`json-${line.kind}`">{{ line.text }}</span
            ><template v-if="line.kind === 'open' && isCollapsed(line.id)"
              ><span class="json-preview"> … {{ line.text === '[' ? ']' : '}' }}</span
              ><span class="json-size">{{
                t('runs.jsonEntries', { count: line.count })
              }}</span></template
            ><span v-if="line.kind !== 'open' || isCollapsed(line.id)" class="json-punctuation">{{
              line.suffix
            }}</span></span
          >
        </span>
      </div>
      <nav
        v-if="visible.length > PAGE_SIZE"
        class="reader-pages"
        :aria-label="t('runs.outputTitle')"
      >
        <el-button size="small" :disabled="page === 0" @click="changePage(-1)">{{
          t('runs.previousChunk')
        }}</el-button>
        <span
          >{{ page * PAGE_SIZE + 1 }}–{{ Math.min((page + 1) * PAGE_SIZE, visible.length) }} /
          {{ visible.length }}</span
        >
        <el-button
          size="small"
          :disabled="(page + 1) * PAGE_SIZE >= visible.length"
          @click="changePage(1)"
          >{{ t('runs.nextChunk') }}</el-button
        >
      </nav>
    </div>
    <div v-else-if="item.type === 'url' || item.type === 'binary_url'" class="output-text">
      <button
        class="output-link"
        type="button"
        @click="openExternalUrl(item.url || item.binary_url || '')"
      >
        {{ item.url || item.binary_url }}
      </button>
    </div>
    <pre v-else class="output-text">{{ item.type === 'text' ? item.text : item.binary }}</pre>
  </div>
</template>
<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTimeoutFn } from '@vueuse/core'
import { ElMessage } from 'element-plus'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Check,
  CopyDocument,
  FullScreen,
} from '@element-plus/icons-vue'
import { RUN_OUTPUT_PAGE_SIZE, type RunOutputReader } from '@/composables/useRunOutputReader'
import { openExternalUrl } from '@/utils/openExternal'
const props = withDefaults(
  defineProps<{ model: RunOutputReader; fullScreen?: boolean; active?: boolean }>(),
  { active: true }
)
defineEmits<{ expand: [] }>()
const { t } = useI18n()
const reader = ref<HTMLElement>()
const PAGE_SIZE = RUN_OUTPUT_PAGE_SIZE
const {
  item,
  isJson,
  jsonValue,
  query,
  pageLines,
  matches,
  matchSet,
  activeMatch,
  matchIndex,
  page,
  visible,
  needsLoad,
  loadPreview,
  isCollapsed,
  toggle,
  setCollapsed,
} = props.model
const copied = ref(false)
const { start: resetCopied } = useTimeoutFn(
  () => {
    copied.value = false
  },
  1500,
  { immediate: false }
)
async function copyOutput() {
  const current = item.value
  try {
    const text = isJson.value
      ? (JSON.stringify(jsonValue.value, null, 2) ?? '')
      : (current.text ?? current.url ?? current.binary_url ?? current.binary ?? '')
    await navigator.clipboard.writeText(text)
    copied.value = true
    resetCopied()
  } catch {
    copied.value = false
    ElMessage.error(t('runs.copyFailed'))
  }
}
async function focusMatch() {
  const index = visible.value.findIndex((line) => line.id === activeMatch.value)
  if (index < 0 || !props.active) return
  page.value = Math.floor(index / PAGE_SIZE)
  await nextTick()
  reader.value
    ?.querySelector(`[data-json-line="${activeMatch.value}"]`)
    ?.scrollIntoView({ block: 'nearest' })
}
function moveMatch(direction: number) {
  props.model.moveMatch(direction)
  void focusMatch()
}
async function changePage(delta: number) {
  page.value += delta
  await nextTick()
  reader.value?.querySelector('.output-code')?.scrollIntoView({ block: 'start' })
}
watch(activeMatch, () => void focusMatch(), { immediate: true })
</script>
<style scoped>
.output-reader {
  --json-key: #315f92;
  --json-string: #28704f;
  --json-number: #7652a1;
  --json-boolean: #955b27;
  min-width: 0;
  color: var(--el-text-color-regular);
}
:global(html.dark .output-reader) {
  --json-key: #a2c8f1;
  --json-string: #9bd4b4;
  --json-number: #cbb5f0;
  --json-boolean: #e4bc8d;
}
.reader-toolbar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-workspace);
}
.reader-toolbar :deep(.el-button + .el-button) {
  margin-inline-start: 0;
}
.reader-tools {
  display: flex;
  gap: 4px;
  margin-inline-start: auto;
}
.reader-search {
  display: flex;
  flex: 1 1 155px;
  align-items: center;
  min-width: 0;
  max-width: 340px;
  border: 1px solid var(--surface-border);
  border-radius: var(--run-radius);
  background: var(--surface-workspace);
}
.reader-search:focus-within {
  border-color: var(--el-color-primary);
}
.reader-search input {
  width: 100%;
  min-width: 50px;
  background: transparent;
  color: inherit;
  border: 0;
  padding: 5px 9px;
  font: inherit;
  font-size: 0.75rem;
  outline: none;
}
.reader-search input::placeholder {
  color: var(--el-text-color-secondary);
}
.reader-search__count {
  white-space: nowrap;
  padding-inline: 4px;
  font-size: 0.6875rem;
  color: var(--el-text-color-secondary);
}
.reader-search button {
  display: inline-flex;
  padding: 6px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  border-radius: var(--run-radius);
}
.reader-search button:disabled {
  opacity: 0.4;
  cursor: default;
}
.output-code {
  padding: 10px 12px 14px 0;
  font:
    0.78125rem/1.75 ui-monospace,
    SFMono-Regular,
    Consolas,
    monospace;
}
.json-line {
  display: flex;
  align-items: flex-start;
  min-height: 1.75em;
  scroll-margin-block: 120px 12px;
}
.json-line--match {
  background: color-mix(in srgb, var(--el-color-warning) 10%, transparent);
}
.json-line--active {
  background: color-mix(in srgb, var(--el-color-warning) 20%, transparent);
  box-shadow: inset 2px 0 var(--el-color-warning);
}
.json-line__number {
  flex: 0 0 3.5em;
  padding-inline: 6px 10px;
  text-align: end;
  user-select: none;
  font-size: 0.6875rem;
  color: var(--el-text-color-secondary);
  opacity: 0.7;
}
.json-line__content {
  display: flex;
  flex: 1;
  min-width: 0;
  padding-inline-start: calc(var(--json-depth) * var(--json-indent, 1.25em));
}
.json-line__tokens {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  min-width: 0;
}
.json-toggle,
.json-toggle-space {
  flex: 0 0 16px;
  width: 16px;
  height: 1.75em;
}
.json-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  border-radius: var(--run-radius);
  font: inherit;
}
.json-toggle--open {
  transform: rotate(90deg);
}
.json-key {
  color: var(--json-key);
}
.json-string {
  color: var(--json-string);
}
.json-number {
  color: var(--json-number);
}
.json-boolean {
  color: var(--json-boolean);
}
.json-punctuation,
.json-open,
.json-close,
.json-null,
.json-preview {
  color: var(--el-text-color-secondary);
}
.json-size {
  padding-inline-start: 12px;
  color: var(--el-text-color-secondary);
  font-size: 0.6875rem;
}
.reader-deferred {
  padding: 10px 16px 18px;
  color: var(--el-text-color-secondary);
  font-size: 0.8125rem;
}
.reader-pages {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
  align-items: center;
  padding: 16px 12px 0;
  color: var(--el-text-color-secondary);
  font:
    0.75rem/1.5 system-ui,
    sans-serif;
}
.output-text {
  margin: 0;
  padding: 14px 16px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font:
    0.8125rem/1.7 ui-monospace,
    SFMono-Regular,
    Consolas,
    monospace;
}
.output-link {
  user-select: text;
  padding: 0;
  border: 0;
  background: transparent;
  font: inherit;
  color: var(--el-color-primary);
  cursor: pointer;
  text-align: start;
  overflow-wrap: anywhere;
  text-decoration: underline;
}
.json-toggle:focus-visible,
.reader-search button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}
.output-reader--fullscreen {
  --run-radius: var(--radius-card);
}
@container run-workbench (max-width: 500px) {
  .output-reader {
    --json-indent: 0.7em;
  }
  .json-line__number {
    flex-basis: 2.75em;
    padding-inline: 4px 6px;
  }
}
@media (max-width: 500px) {
  .output-reader--fullscreen {
    --json-indent: 0.7em;
  }
}
@media (pointer: coarse) {
  .json-toggle {
    min-height: 28px;
  }
  .reader-search button {
    padding: 10px;
  }
}
</style>
