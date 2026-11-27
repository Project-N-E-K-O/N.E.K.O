<template>
  <div v-loading="loading && !switches" class="builtin-tab">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <template v-if="switches">
      <el-alert
        v-if="!switches.enabled"
        :title="t('power.runtime.builtin.needsPower')"
        type="info"
        :closable="false"
        show-icon
        class="block"
        data-testid="builtin-needs-power"
      />

      <section class="panel">
        <h2>{{ t('power.runtime.builtin.keyboard') }}</h2>
        <div class="row">
          <div class="label">
            {{ t('power.runtime.builtin.keyboardSwitch') }}
            <small>{{ t('power.runtime.builtin.keyboardHint') }}</small>
            <small v-if="reasonOf('computer_use')" class="warn" data-testid="keyboard-reason">
              {{ t('power.runtime.builtin.notReady', { reason: reasonOf('computer_use') }) }}
            </small>
          </div>
          <el-switch
            :model-value="!!switches.flags.computer_use_enabled"
            :loading="switching === 'computer_use_enabled'"
            :disabled="switching !== ''"
            :aria-label="t('power.runtime.builtin.keyboardSwitch')"
            data-testid="switch-keyboard"
            @change="(value: string | number | boolean) => flip('computer_use_enabled', value === true)"
          />
        </div>
        <div class="row">
          <div class="label">
            {{ t('power.runtime.builtin.images') }}
            <small data-testid="model-images">{{ imagesText }}</small>
            <details v-if="switches.model_images.reason">
              <summary>{{ t('power.runtime.builtin.imagesReason') }}</summary>
              <code>{{ switches.model_images.reason }}</code>
            </details>
          </div>
        </div>
      </section>

      <section class="panel">
        <h2>{{ t('power.runtime.builtin.browser') }}</h2>
        <div class="row">
          <div class="label">
            {{ t('power.runtime.builtin.browserSwitch') }}
            <small>{{ t('power.runtime.builtin.browserHint') }}</small>
            <small v-if="reasonOf('browser_use')" class="warn" data-testid="browser-reason">
              {{ t('power.runtime.builtin.notReady', { reason: reasonOf('browser_use') }) }}
            </small>
          </div>
          <el-switch
            :model-value="!!switches.flags.browser_use_enabled"
            :loading="switching === 'browser_use_enabled'"
            :disabled="switching !== ''"
            :aria-label="t('power.runtime.builtin.browserSwitch')"
            data-testid="switch-browser"
            @change="(value: string | number | boolean) => flip('browser_use_enabled', value === true)"
          />
        </div>

        <template v-if="browser">
          <div class="row">
            <div class="label">
              {{ t('power.runtime.builtin.login') }}
              <small>{{ t('power.runtime.builtin.loginHint') }}</small>
            </div>
            <el-select
              :model-value="browser.settings.mode"
              class="wide"
              :disabled="saving"
              :aria-label="t('power.runtime.builtin.login')"
              data-testid="browser-mode"
              @change="(mode: string) => save({ mode })"
            >
              <el-option
                v-for="mode in browser.modes"
                :key="mode"
                :value="mode"
                :label="te(`power.runtime.builtin.modes.${mode}`) ? t(`power.runtime.builtin.modes.${mode}`) : mode"
              />
            </el-select>
          </div>
          <div class="row">
            <div class="label">
              {{ t('power.runtime.builtin.which') }}
              <small>{{ foundText }}</small>
            </div>
            <el-select
              :model-value="browser.settings.executable ?? ''"
              :empty-values="[null, undefined]"
              class="wide"
              :disabled="saving"
              :aria-label="t('power.runtime.builtin.which')"
              data-testid="browser-executable"
              @change="(executable: string) => save({ executable: executable || null })"
            >
              <el-option value="" :label="t('power.runtime.builtin.whichAuto')" />
              <el-option v-for="found in browser.browsers" :key="found.executable" :value="found.executable" :label="found.name" />
            </el-select>
          </div>
          <div class="row">
            <div class="label">
              {{ t('power.runtime.builtin.headless') }}
              <small>{{ t('power.runtime.builtin.headlessHint') }}</small>
            </div>
            <el-switch
              :model-value="browser.settings.headless"
              :disabled="saving"
              :aria-label="t('power.runtime.builtin.headless')"
              data-testid="browser-headless"
              @change="(value: string | number | boolean) => save({ headless: value === true })"
            />
          </div>
          <p class="muted">{{ t('power.runtime.builtin.changeNote') }}</p>
        </template>
      </section>

      <!-- Not a capability, and not held by the switch for all of it: whether the main page shows the task HUD. -->
      <section v-if="switches.task_hud_visible !== undefined" class="panel">
        <h2>{{ t('power.runtime.builtin.taskHud') }}</h2>
        <div class="row">
          <div class="label">
            {{ t('power.runtime.builtin.taskHudSwitch') }}
            <small>{{ t('power.runtime.builtin.taskHudHint') }}</small>
          </div>
          <el-switch
            :model-value="switches.task_hud_visible"
            :loading="switching === 'task_hud_visible'"
            :disabled="switching !== ''"
            :aria-label="t('power.runtime.builtin.taskHudSwitch')"
            data-testid="switch-task-hud"
            @change="(value: string | number | boolean) => flip('task_hud_visible', value === true)"
          />
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getBrowser, getSwitches, setBrowser, setDeviceSwitch } from '@/api/console'
import type { BrowserSettings, BrowserView, ConsoleSwitch, Switches } from '@/api/console'
import { describeConsoleError } from './powerFormat'

const { t, te, locale } = useI18n()
const switches = ref<Switches | null>(null)
const browser = ref<BrowserView | null>(null)
const loading = ref(false)
const switching = ref('')
const saving = ref(false)
const error = ref('')

/** Why a capability that is switched on cannot be used right now, or '' when it can or is off. */
function reasonOf(capability: 'computer_use' | 'browser_use'): string {
  const now = switches.value
  if (!now?.flags[`${capability}_enabled`]) return ''
  const state = now.capabilities[capability]
  return state && state.ready === false ? state.reason || '' : ''
}

const imagesText = computed(() => {
  const images = switches.value?.model_images
  if (!images || images.sees) return t('power.runtime.builtin.imagesYes')
  if (images.source !== 'refused') return t('power.runtime.builtin.imagesByHand')
  const until = images.lapses_at
    ? t('power.runtime.builtin.imagesUntil', { date: new Date(images.lapses_at * 1000).toLocaleDateString(locale.value) })
    : ''
  return t('power.runtime.builtin.imagesRefused', { until })
})
const foundText = computed(() => {
  const found = browser.value?.browsers ?? []
  if (!found.length) return t('power.runtime.builtin.whichNone')
  return t('power.runtime.builtin.whichHint', { names: found.map(item => item.name).join(', ') })
})

async function load() {
  loading.value = true
  try {
    const [nextSwitches, nextBrowser] = await Promise.all([getSwitches(), getBrowser()])
    // An answer without them is no answer: said, rather than an empty page shown.
    if (!nextSwitches?.switches || !nextBrowser?.settings) throw new Error('incomplete answer')
    switches.value = nextSwitches.switches
    browser.value = nextBrowser
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loading.value = false
  }
}

async function flip(key: ConsoleSwitch, value: boolean) {
  switching.value = key
  try {
    await setDeviceSwitch(key, value)
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  }
  // Whether it took or not, what is shown is what the agent process says now:
  // a switch can be put off again right away (a model that cannot be reached).
  try {
    switches.value = (await getSwitches()).switches
  } catch (failure) {
    // The change may have been taken and cannot be shown: said, unless why it was not taken is said already.
    if (!error.value) error.value = describeConsoleError(failure, t, te)
  } finally {
    switching.value = ''
  }
}

async function save(change: Partial<BrowserSettings>) {
  saving.value = true
  try {
    browser.value = await setBrowser(change)
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())
defineExpose({ load })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; margin-bottom: 16px; }
.panel h2 { margin: 0 0 4px; font-size: 16px; }
.row { display: flex; align-items: center; gap: 16px; padding: 14px 0; border-top: 1px solid var(--el-border-color-lighter); }
.panel h2 + .row { border-top: 0; }
.label { flex: 1; min-width: 0; line-height: 1.5; }
.label small { display: block; color: var(--el-text-color-secondary); line-height: 1.6; margin-top: 2px; }
.label small.warn { color: var(--el-color-warning); }
.label details { margin-top: 4px; font-size: 12px; color: var(--el-text-color-secondary); }
.label code { display: block; margin-top: 4px; overflow-wrap: anywhere; }
.wide { width: 240px; max-width: 45%; flex: none; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 10px 0 0; }
.block { margin-bottom: 12px; }
</style>
