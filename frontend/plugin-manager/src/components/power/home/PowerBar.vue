<template>
  <section class="power-bar">
    <div class="switch">
      <!-- A switch of its own, and the meters below as plain bars: this is the
           first page shown, and the library's switch and progress styles would
           be loaded with it for these three small things. -->
      <button
        type="button"
        role="switch"
        class="toggle"
        :class="{ 'toggle--on': !!switches?.enabled, 'toggle--busy': switching }"
        :aria-checked="!!switches?.enabled"
        :aria-label="t('power.home.power')"
        :aria-busy="switching"
        :disabled="!switches || switching"
        data-testid="power-switch"
        @click="flip(!switches?.enabled)"
      ><span class="toggle__knob" /></button>
      <div class="words">
        <strong>{{ t('power.home.power') }}</strong>
        <small v-if="error" class="warn" data-testid="power-error">{{ error }}</small>
        <small v-else-if="switches" data-testid="power-state">{{ t(switches.enabled ? 'power.home.on' : 'power.home.off') }}</small>
        <small v-if="reason" class="warn" data-testid="power-reason">{{ t('power.home.notReady', { reason }) }}</small>
      </div>
    </div>
    <div v-if="metrics" class="meters" data-testid="power-meters">
      <span class="meter">
        {{ t('power.home.cpu') }}
        <span class="bar" role="presentation"><i :style="{ width: `${cpu}%` }" /></span>
        {{ cpu.toFixed(0) }}%
      </span>
      <span class="meter">
        {{ t('power.home.memory') }}
        <span class="bar" role="presentation"><i :style="{ width: `${memory}%` }" /></span>
        {{ formatBytes(metrics.total_memory_mb * 1024 * 1024) }}
      </span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getSwitches, setPower } from '@/api/console'
import type { Switches } from '@/api/console'
import { getAllMetrics } from '@/api/metrics'
import type { GlobalMetrics } from '@/types/api'
import { describeConsoleError, formatBytes } from '../powerFormat'

const emit = defineEmits<{ changed: [enabled: boolean] }>()
const { t, te } = useI18n()
const switches = ref<Switches | null>(null)
const metrics = ref<GlobalMetrics | null>(null)
const switching = ref(false)
const error = ref('')
// How often the two meters are read, in milliseconds. They are an aside: a read that fails is skipped.
const METERS_MS = 5000
let timer: ReturnType<typeof setInterval> | null = null

const cpu = computed(() => Math.min(100, Math.max(0, metrics.value?.total_cpu_percent ?? 0)))
const memory = computed(() => Math.min(100, Math.max(0, metrics.value?.total_memory_percent ?? 0)))
/** Why it is on and cannot be used yet, as the service says it; '' when there is nothing to say. */
const reason = computed(() => {
  const now = switches.value
  if (!now?.enabled || now.gate?.ready !== false) return ''
  return (now.gate.reasons ?? [])[0] ?? ''
})

async function load() {
  try {
    switches.value = (await getSwitches()).switches ?? null
    error.value = switches.value ? '' : t('power.errors.agent_unreachable')
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  }
}

async function flip(enabled: boolean) {
  switching.value = true
  try {
    await setPower(enabled)
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  }
  // What is shown is where the service stands now, whether the switch took or not.
  const failed = error.value
  await load()
  if (failed) error.value = failed
  switching.value = false
  if (switches.value) emit('changed', switches.value.enabled)
}

async function readMeters() {
  try {
    metrics.value = (await getAllMetrics()).global ?? null
  } catch {
    /* Left as they were. */
  }
}

onMounted(() => {
  void load()
  void readMeters()
  timer = setInterval(() => {
    void readMeters()
    // The switch could not be read (the service was not up yet): asked again, so that it comes to be usable.
    if (!switches.value && !switching.value) void load()
  }, METERS_MS)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
defineExpose({ load })
</script>

<style scoped>
.power-bar { display: flex; align-items: center; justify-content: space-between; gap: 20px; flex-wrap: wrap; background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 14px 20px; }
.switch { display: flex; align-items: center; gap: 14px; min-width: 0; }
.words { display: flex; flex-direction: column; line-height: 1.5; min-width: 0; }
.words strong { font-size: 16px; }
.words small { color: var(--el-text-color-secondary); }
.words small.warn { color: var(--el-color-warning); }
.meters { display: flex; align-items: center; gap: 18px; color: var(--el-text-color-secondary); font-size: 12px; }
.meter { display: inline-flex; align-items: center; gap: 8px; white-space: nowrap; }
.bar { display: inline-block; width: 72px; height: 6px; border-radius: 3px; background: var(--el-fill-color); overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 3px; background: var(--el-color-primary); transition: width 0.3s; }
.toggle { flex: none; position: relative; width: 52px; height: 28px; padding: 0; border: 0; border-radius: 14px; background: var(--el-border-color); cursor: pointer; transition: background 0.2s; }
.toggle--on { background: var(--el-color-primary); }
.toggle:disabled { cursor: not-allowed; opacity: 0.6; }
.toggle:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.toggle__knob { position: absolute; top: 3px; left: 3px; width: 22px; height: 22px; border-radius: 50%; background: #fff; transition: transform 0.2s; }
.toggle--on .toggle__knob { transform: translateX(24px); }
.toggle--busy .toggle__knob { animation: pulse 0.9s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
@media (prefers-reduced-motion: reduce) {
  .toggle, .toggle__knob, .bar i { transition: none; }
  .toggle--busy .toggle__knob { animation: none; }
}
</style>
