<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useData, withBase } from 'vitepress'
import {
  ANALYTICS_CONSENT_EVENT,
  acceptGoogleAnalytics,
  getAnalyticsConsent,
  handleAnalyticsConsentStorageEvent,
  rejectGoogleAnalytics,
} from './analytics-consent.mjs'

type ConsentChoice = 'granted' | 'denied' | null
type ConsentLocale = 'en' | 'zh-CN' | 'ja'

const messages = {
  en: {
    title: 'Analytics preferences',
    body: 'Allow Google Analytics to help us improve these docs.',
    accept: 'Allow',
    reject: 'Decline',
    settings: 'Cookie settings',
    close: 'Close',
    privacy: 'Privacy',
    footer: 'Privacy options',
  },
  'zh-CN': {
    title: '分析偏好',
    body: '允许 Google Analytics 帮助我们改进文档。',
    accept: '允许',
    reject: '拒绝',
    settings: 'Cookie 设置',
    close: '关闭',
    privacy: '隐私',
    footer: '隐私选项',
  },
  ja: {
    title: '解析設定',
    body: 'ドキュメント改善のため、Google Analytics の利用を許可してください。',
    accept: '許可',
    reject: '拒否',
    settings: 'Cookie 設定',
    close: '閉じる',
    privacy: 'プライバシー',
    footer: 'プライバシー設定',
  },
} as const

const { lang } = useData()
const ready = ref(false)
const panelOpen = ref(false)
const choice = ref<ConsentChoice>(null)
const allowButton = ref<HTMLButtonElement | null>(null)
const rejectButton = ref<HTMLButtonElement | null>(null)
const settingsButton = ref<HTMLButtonElement | null>(null)

const locale = computed<ConsentLocale>(() => {
  if (lang.value.toLowerCase().startsWith('zh')) return 'zh-CN'
  if (lang.value.toLowerCase().startsWith('ja')) return 'ja'
  return 'en'
})
const copy = computed(() => messages[locale.value])
const privacyPath = computed(() => {
  if (locale.value === 'zh-CN') return withBase('/zh-CN/privacy')
  if (locale.value === 'ja') return withBase('/ja/privacy')
  return withBase('/privacy')
})
function syncChoice(event?: Event) {
  const eventChoice = (event as CustomEvent<{ choice?: ConsentChoice }>)
    ?.detail?.choice
  choice.value = eventChoice || getAnalyticsConsent()
}

async function restoreSettingsFocus() {
  await nextTick()
  settingsButton.value?.focus()
}

async function openSettings() {
  panelOpen.value = true
  await nextTick()
  const selectedButton = choice.value === 'denied'
    ? rejectButton.value
    : allowButton.value
  selectedButton?.focus()
}

async function closeSettings() {
  panelOpen.value = false
  await restoreSettingsFocus()
}

async function accept() {
  const restoreFocus = choice.value !== null
  acceptGoogleAnalytics()
  choice.value = 'granted'
  panelOpen.value = false
  if (restoreFocus) await restoreSettingsFocus()
}

async function reject() {
  const restoreFocus = choice.value !== null
  const wasActive = rejectGoogleAnalytics()
  choice.value = 'denied'
  if (!wasActive) {
    panelOpen.value = false
    if (restoreFocus) await restoreSettingsFocus()
  }
}

function syncStorageChoice(event: StorageEvent) {
  handleAnalyticsConsentStorageEvent(event)
}

onMounted(() => {
  syncChoice()
  panelOpen.value = choice.value === null
  ready.value = true
  window.addEventListener(ANALYTICS_CONSENT_EVENT, syncChoice)
  window.addEventListener('storage', syncStorageChoice)
})

onBeforeUnmount(() => {
  window.removeEventListener(ANALYTICS_CONSENT_EVENT, syncChoice)
  window.removeEventListener('storage', syncStorageChoice)
})
</script>

<template>
  <div v-if="ready" class="NekoAnalyticsConsent">
    <section
      v-if="panelOpen"
      class="NekoAnalyticsConsent-banner"
      :class="{ 'NekoAnalyticsConsent-banner--revisit': choice !== null }"
      role="dialog"
      aria-labelledby="neko-analytics-consent-title"
      aria-describedby="neko-analytics-consent-description"
    >
      <div class="NekoAnalyticsConsent-copy">
        <h2 id="neko-analytics-consent-title">
          {{ copy.title }}
        </h2>
        <p id="neko-analytics-consent-description">
          {{ copy.body }}
          <a class="NekoAnalyticsConsent-privacy" :href="privacyPath">
            {{ copy.privacy }}
          </a>
        </p>
      </div>

      <div class="NekoAnalyticsConsent-actions">
        <button
          ref="allowButton"
          class="NekoAnalyticsConsent-button"
          :class="{ 'NekoAnalyticsConsent-button--primary': choice !== 'denied' }"
          type="button"
          :aria-pressed="choice === 'granted'"
          @click="accept"
        >
          {{ copy.accept }}
        </button>
        <button
          ref="rejectButton"
          class="NekoAnalyticsConsent-button"
          :class="{ 'NekoAnalyticsConsent-button--primary': choice === 'denied' }"
          type="button"
          :aria-pressed="choice === 'denied'"
          @click="reject"
        >
          {{ copy.reject }}
        </button>
        <button
          v-if="choice !== null"
          class="NekoAnalyticsConsent-close"
          type="button"
          :aria-label="copy.close"
          @click="closeSettings"
        >
          ×
        </button>
      </div>
    </section>

    <nav class="NekoAnalyticsConsent-footer" :aria-label="copy.footer">
      <a :href="privacyPath">{{ copy.privacy }}</a>
      <span aria-hidden="true">·</span>
      <button
        ref="settingsButton"
        type="button"
        @click="openSettings"
      >
        {{ copy.settings }}
      </button>
    </nav>

    <div
      v-if="panelOpen"
      class="NekoAnalyticsConsent-spacer"
      aria-hidden="true"
    />
  </div>
</template>

<style scoped>
.NekoAnalyticsConsent-banner {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  gap: 24px;
  min-height: 64px;
  padding: 12px max(20px, calc((100vw - 1440px) / 2));
  border-top: 1px solid rgba(94, 129, 244, 0.22);
  color: #f8fafc;
  background: #090c20;
  box-shadow: 0 -8px 24px rgba(3, 7, 18, 0.28);
}

.NekoAnalyticsConsent-copy {
  flex: 1 1 auto;
  min-width: 0;
}

.NekoAnalyticsConsent-copy h2 {
  margin: 0 0 4px;
  border: 0;
  color: #fff;
  font-size: 16px;
  line-height: 1.35;
}

.NekoAnalyticsConsent-copy p {
  margin: 0;
  color: #dbe4f0;
  font-size: 13px;
  line-height: 1.5;
}

.NekoAnalyticsConsent-privacy {
  margin-left: 6px;
  color: #7dd3fc;
  white-space: nowrap;
}

.NekoAnalyticsConsent-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
}

.NekoAnalyticsConsent-button {
  width: 88px;
  min-width: 88px;
  min-height: 32px;
  padding: 5px 11px;
  border: 1px solid #667085;
  border-radius: 6px;
  color: #f8fafc;
  background: #1a2138;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
}

.NekoAnalyticsConsent-button:hover {
  border-color: #94a3b8;
  color: #fff;
  background: #232c48;
}

.NekoAnalyticsConsent-button--primary {
  border-color: #38bdf8;
  color: #fff;
  background: #0ea5e9;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2);
}

.NekoAnalyticsConsent-button--primary:hover {
  border-color: #7dd3fc;
  color: #fff;
  background: #0284c7;
}

.NekoAnalyticsConsent-close {
  display: grid;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  place-items: center;
  color: #94a3b8;
  background: transparent;
  cursor: pointer;
  font-size: 20px;
}

.NekoAnalyticsConsent-close:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.08);
}

.NekoAnalyticsConsent-footer {
  display: flex;
  justify-content: center;
  gap: 7px;
  padding: 8px 16px 16px;
  color: var(--vp-c-text-3);
  font-size: 12px;
}

.NekoAnalyticsConsent-footer a,
.NekoAnalyticsConsent-footer button {
  padding: 0;
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  font: inherit;
}

.NekoAnalyticsConsent-footer a:hover,
.NekoAnalyticsConsent-footer button:hover {
  color: var(--vp-c-text-2);
  text-decoration: underline;
}

.NekoAnalyticsConsent-spacer {
  min-height: 76px;
}

@media (max-width: 720px) {
  .NekoAnalyticsConsent-banner {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
    min-height: 0;
    padding: 12px 16px;
  }

  .NekoAnalyticsConsent-actions {
    width: 100%;
  }

  .NekoAnalyticsConsent-spacer {
    min-height: 132px;
  }
}

@media (max-width: 520px) {
  .NekoAnalyticsConsent-banner {
    flex-direction: column;
  }

  .NekoAnalyticsConsent-close {
    position: absolute;
    top: 8px;
    right: 8px;
  }

  .NekoAnalyticsConsent-banner--revisit .NekoAnalyticsConsent-copy {
    padding-right: 28px;
  }
}
</style>
