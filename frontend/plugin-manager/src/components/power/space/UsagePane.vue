<template>
  <section class="usage" data-testid="usage-pane">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="usage-error" />
    <template v-if="usage">
      <template v-if="usage.commission">
        <h4>{{ t('power.usage.inHand') }}</h4>
        <p class="text">{{ usage.commission.title }}</p>
        <div v-if="usage.commission.limit" class="bar" role="img" :aria-label="limitLine">
          <i :style="{ width: `${share}%` }" :class="{ near: share >= 80 }" />
        </div>
        <p class="muted" data-testid="usage-limit-line">{{ limitLine }}</p>
      </template>
      <h4>{{ t('power.usage.overall') }}</h4>
      <dl class="tiles" data-testid="usage-tiles">
        <div><dt>{{ t('power.usage.requests') }}</dt><dd>{{ usage.session.requests.toLocaleString() }}</dd></div>
        <div><dt>{{ t('power.usage.input') }}</dt><dd>{{ formatTokens(usage.session.input + usage.session.cache_read + usage.session.cache_write) }}</dd></div>
        <div><dt>{{ t('power.usage.cacheRead') }}</dt><dd>{{ formatTokens(usage.session.cache_read) }}</dd></div>
        <div><dt>{{ t('power.usage.output') }}</dt><dd>{{ formatTokens(usage.session.output) }}</dd></div>
        <div><dt>{{ t('power.usage.total') }}</dt><dd>{{ formatTokens(usage.session.tokens) }}</dd></div>
        <div v-if="context"><dt>{{ t('power.usage.context') }}</dt><dd>{{ context }}</dd></div>
      </dl>
      <p class="muted">{{ t('power.usage.hint') }}</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getSessionUsage } from '@/api/console'
import type { SessionUsage } from '@/api/console'
import { describeConsoleError, formatTokens } from '../powerFormat'

/** What one familiar used over all it did, and how far the thing it has in hand is from where it stops to ask. */
const props = defineProps<{ sessionId: string; refresh?: unknown }>()
const { t, te } = useI18n()
const usage = ref<SessionUsage | null>(null)
const error = ref('')
let turn = 0

const share = computed(() => {
  const now = usage.value?.commission
  return now?.limit ? Math.min(100, Math.round((now.used / now.limit) * 100)) : 0
})
const limitLine = computed(() => {
  const now = usage.value?.commission
  if (!now) return ''
  return now.limit
    ? t('power.usage.ofLimit', { used: formatTokens(now.used), limit: formatTokens(now.limit) })
    : t('power.usage.noLimit', { used: formatTokens(now.used) })
})
const context = computed(() => {
  const now = usage.value
  if (!now?.session.context_tokens) return ''
  const window = now.window?.window
  return window
    ? `${Math.min(100, Math.round((now.session.context_tokens / window) * 100))}% (${formatTokens(now.session.context_tokens)})`
    : formatTokens(now.session.context_tokens)
})

async function load() {
  const mine = ++turn
  try {
    const answer = await getSessionUsage(props.sessionId)
    if (mine !== turn) return
    usage.value = answer
    error.value = ''
  } catch (failure) {
    if (mine !== turn) return
    error.value = describeConsoleError(failure, t, te)
  }
}
// Read again when what the familiar has in hand moved on (`refresh` changes then): the numbers
// stay meanwhile. Another familiar: none of the one before stay, read or not.
watch(() => [props.sessionId, props.refresh], ([sessionId], before) => {
  if (before && sessionId !== before[0]) usage.value = null
  void load()
}, { immediate: true })
</script>

<style scoped>
.usage h4 { margin: 12px 0 6px; font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); }
.text { margin: 0 0 6px; overflow-wrap: anywhere; }
.muted { margin: 4px 0 0; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.bar { height: 8px; border-radius: 4px; overflow: hidden; background: var(--el-fill-color); }
.bar i { display: block; height: 100%; background: var(--el-color-primary); }
.bar i.near { background: var(--el-color-warning); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin: 0; }
.tiles div { padding: 8px 10px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.tiles dt { font-size: 12px; color: var(--el-text-color-secondary); }
.tiles dd { margin: 0; font-size: 18px; font-weight: 600; }
</style>
