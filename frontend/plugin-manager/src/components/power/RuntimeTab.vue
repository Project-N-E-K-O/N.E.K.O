<template>
  <div v-loading="loading && !view" class="runtime-tab">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />

    <template v-if="view">
      <section class="panel">
        <h2>{{ t('power.runtime.choose.title') }}</h2>
        <el-radio-group
          :model-value="view.dsh.preference"
          class="choices"
          :disabled="busy"
          data-testid="runtime-preference"
          @change="choose"
        >
          <el-radio value="auto">
            <span class="choice">{{ t('power.runtime.choose.auto') }}<small>{{ t('power.runtime.choose.autoHint') }}</small></span>
          </el-radio>
          <el-radio value="builtin">
            <span class="choice">{{ t('power.runtime.choose.builtin') }}<small>{{ t('power.runtime.choose.builtinHint') }}</small></span>
          </el-radio>
        </el-radio-group>
      </section>

      <section class="panel">
        <div class="heading">
          <h2>{{ t('power.runtime.dsh.title') }}</h2>
          <el-tag :type="stateTag.type" effect="plain" data-testid="dsh-state">{{ stateTag.label }}</el-tag>
        </div>
        <p class="muted">{{ t('power.runtime.dsh.about', { version: view.dsh.version }) }}</p>
        <p v-if="view.dsh.download_bytes" class="muted">
          {{ t('power.runtime.dsh.size', { download: formatBytes(view.dsh.download_bytes), installed: '270 MB' }) }}
        </p>

        <div v-if="view.dsh.downloading" class="progress" data-testid="dsh-progress">
          <el-progress :percentage="percent" :indeterminate="percent === 0" :show-text="false" />
          <span class="muted">{{ progressText }}</span>
        </div>

        <el-alert
          v-if="view.dsh.last_error_code && !view.dsh.downloading"
          :title="t('power.runtime.dsh.errorTitle')"
          type="warning"
          :closable="false"
          show-icon
          class="block"
          data-testid="dsh-error"
        >
          <p>{{ errorText }}</p>
          <details v-if="view.dsh.last_error"><summary>{{ t('power.runtime.dsh.errorDetail') }}</summary><code>{{ view.dsh.last_error }}</code></details>
        </el-alert>

        <dl v-if="view.dsh.installed" class="facts">
          <div v-if="view.dsh.path"><dt>{{ t('power.runtime.dsh.path') }}</dt><dd>{{ view.dsh.path }}</dd></div>
          <div v-if="installedAt"><dt>{{ t('power.runtime.dsh.installedAt') }}</dt><dd>{{ installedAt }}</dd></div>
          <div><dt>{{ t('power.runtime.dsh.sessions') }}</dt><dd>{{ view.dsh.sessions }}</dd></div>
        </dl>
        <p v-if="view.dsh.fallback_reason" class="muted">{{ t('power.runtime.dsh.fallback', { reason: view.dsh.fallback_reason }) }}</p>

        <div class="actions">
          <el-button
            v-if="!view.dsh.installed"
            type="primary"
            :disabled="!view.dsh.supported || view.dsh.downloading || busy"
            :loading="view.dsh.downloading"
            data-testid="dsh-install"
            @click="install"
          >
            {{ t(view.dsh.last_error_code ? 'power.runtime.dsh.retry' : 'power.runtime.dsh.download') }}
          </el-button>
          <el-tooltip v-else :content="t('power.runtime.dsh.inUse')" :disabled="view.dsh.sessions === 0">
            <span>
              <el-button
                type="danger"
                plain
                :disabled="view.dsh.sessions > 0 || busy"
                data-testid="dsh-uninstall"
                @click="askUninstall"
              >{{ t('power.runtime.dsh.uninstall') }}</el-button>
            </span>
          </el-tooltip>
        </div>
      </section>
    </template>

    <el-dialog v-model="uninstalling" :title="t('power.runtime.dsh.uninstallTitle')" width="460px">
      <p>{{ t('power.runtime.dsh.uninstallText') }}</p>
      <el-checkbox v-model="purge" data-testid="dsh-purge">{{ t('power.runtime.dsh.purge') }}</el-checkbox>
      <template #footer>
        <el-button @click="uninstalling = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="busy" data-testid="dsh-uninstall-confirm" @click="uninstall">{{ t('power.runtime.dsh.uninstall') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRuntime, installDsh, setRuntimePreference, uninstallDsh } from '@/api/console'
import type { RuntimeView } from '@/api/console'
import { describeConsoleError, formatBytes } from './powerFormat'

const emit = defineEmits<{ loaded: [view: RuntimeView] }>()
const { t, te, locale } = useI18n()
const view = ref<RuntimeView | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const uninstalling = ref(false)
const purge = ref(false)
// How often the page asks how far the download is, in milliseconds.
const POLL_MS = 1000
let timer: ReturnType<typeof setTimeout> | null = null
let alive = true

const percent = computed(() => {
  const dsh = view.value?.dsh
  if (!dsh?.download_bytes || !dsh.downloaded_bytes) return 0
  return Math.min(100, Math.round((dsh.downloaded_bytes / dsh.download_bytes) * 100))
})
const progressText = computed(() => {
  const dsh = view.value?.dsh
  if (!dsh?.downloaded_bytes || !dsh.download_bytes) return t('power.runtime.dsh.starting')
  return t('power.runtime.dsh.progress', { done: formatBytes(dsh.downloaded_bytes), total: formatBytes(dsh.download_bytes) })
})
const stateTag = computed(() => {
  const dsh = view.value?.dsh
  if (!dsh?.supported) return { type: 'info' as const, label: t('power.runtime.dsh.unsupported') }
  if (dsh.downloading) return { type: 'warning' as const, label: t('power.runtime.dsh.downloading') }
  if (dsh.installed) return { type: 'success' as const, label: t('power.runtime.dsh.installed') }
  return { type: 'info' as const, label: t('power.runtime.dsh.notInstalled') }
})
const errorText = computed(() => {
  const code = view.value?.dsh.last_error_code
  return code && te(`power.errors.${code}`) ? t(`power.errors.${code}`) : t('power.errors.dsh_install')
})
const installedAt = computed(() => {
  const at = view.value?.dsh.installed_at
  if (!at) return ''
  const date = typeof at === 'number' ? new Date(at * 1000) : new Date(at)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(locale.value)
})

function show(next: RuntimeView) {
  view.value = next
  emit('loaded', next)
  if (timer) clearTimeout(timer)
  timer = null
  // While a download runs the page keeps asking; it stops by itself after.
  if (next.dsh.downloading && alive) timer = setTimeout(() => void load(true), POLL_MS)
}

async function load(quiet = false) {
  if (!quiet) loading.value = true
  try {
    show(await getRuntime())
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
    if (quiet && alive) timer = setTimeout(() => void load(true), POLL_MS * 3)
  } finally {
    loading.value = false
  }
}

async function act(work: () => Promise<RuntimeView>) {
  busy.value = true
  try {
    show(await work())
    error.value = ''
    return true
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
    return false
  } finally {
    busy.value = false
  }
}

const choose = (value: unknown) => act(() => setRuntimePreference(value === 'builtin' ? 'builtin' : 'auto'))
const install = () => act(installDsh)
function askUninstall() {
  purge.value = false
  uninstalling.value = true
}
async function uninstall() {
  if (await act(() => uninstallDsh(purge.value))) uninstalling.value = false
}

onMounted(() => void load())
onBeforeUnmount(() => {
  alive = false
  if (timer) clearTimeout(timer)
})
defineExpose({ load })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; margin-bottom: 16px; }
.panel h2 { margin: 0 0 12px; font-size: 16px; }
.heading { display: flex; align-items: center; gap: 12px; }
.heading h2 { margin: 0; }
.choices { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.choices :deep(.el-radio) { height: auto; align-items: flex-start; white-space: normal; margin-right: 0; padding: 4px 0; }
.choice { display: flex; flex-direction: column; gap: 2px; line-height: 1.5; }
.choice small, .muted { color: var(--el-text-color-secondary); }
.muted { margin: 8px 0; line-height: 1.6; font-size: 13px; }
.progress { display: flex; flex-direction: column; gap: 6px; margin: 12px 0; }
.block { margin: 12px 0; }
.block p { margin: 0 0 6px; }
.block code { display: block; margin-top: 6px; font-size: 12px; overflow-wrap: anywhere; }
.facts { margin: 12px 0; font-size: 13px; }
.facts > div { display: flex; gap: 16px; margin: 6px 0; }
.facts dt { color: var(--el-text-color-secondary); min-width: 140px; }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.actions { margin-top: 14px; }
</style>
