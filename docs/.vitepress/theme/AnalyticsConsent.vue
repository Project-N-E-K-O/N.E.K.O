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
    body: 'We use Google Analytics to understand which documentation pages are useful and when visitors choose the Steam link. Google Analytics is not loaded until you accept, and advertising storage remains disabled.',
    accept: 'Allow',
    reject: 'Decline',
    settings: 'Cookie settings',
    close: 'Close',
    detailsPrefix: 'If you would like to learn more, please see our ',
    privacy: 'Privacy Policy',
    detailsSuffix: '.',
    footer: 'Privacy options',
  },
  'zh-CN': {
    title: '分析偏好',
    body: '我们使用 Google Analytics 来了解哪些文档页面有用，以及访问者何时选择 Steam 链接。在你接受之前不会加载 Google Analytics，广告存储始终保持禁用。',
    accept: '允许',
    reject: '拒绝',
    settings: 'Cookie 设置',
    close: '关闭',
    detailsPrefix: '如需了解更多信息，请查看我们的',
    privacy: '隐私政策',
    detailsSuffix: '。',
    footer: '隐私选项',
  },
  ja: {
    title: '解析設定',
    body: 'Google Analytics を使用して、どのドキュメントページが役立っているか、訪問者がいつ Steam リンクを選択したかを把握します。許可するまで Google Analytics は読み込まれず、広告用ストレージは無効のままです。',
    accept: '許可',
    reject: '拒否',
    settings: 'Cookie 設定',
    close: '閉じる',
    detailsPrefix: '詳しくは、',
    privacy: 'プライバシーポリシー',
    detailsSuffix: 'をご覧ください。',
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
      :aria-label="copy.title"
      aria-describedby="neko-analytics-consent-description"
    >
      <div class="NekoAnalyticsConsent-copy">
        <p id="neko-analytics-consent-description">
          {{ copy.body }}
          <span class="NekoAnalyticsConsent-details">
            {{ copy.detailsPrefix }}<a
              class="NekoAnalyticsConsent-privacy"
              :href="privacyPath"
            >{{ copy.privacy }}</a>{{ copy.detailsSuffix }}
          </span>
        </p>
      </div>

      <div class="NekoAnalyticsConsent-actions">
        <button
          ref="allowButton"
          class="NekoAnalyticsConsent-button"
          :class="{ 'NekoAnalyticsConsent-button--selected': choice === 'granted' }"
          type="button"
          :aria-pressed="choice === 'granted'"
          @click="accept"
        >
          {{ copy.accept }}
        </button>
        <button
          ref="rejectButton"
          class="NekoAnalyticsConsent-button"
          :class="{ 'NekoAnalyticsConsent-button--selected': choice === 'denied' }"
          type="button"
          :aria-pressed="choice === 'denied'"
          @click="reject"
        >
          {{ copy.reject }}
        </button>
        <button
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
  gap: 32px;
  min-height: 76px;
  padding: 14px max(32px, calc((100vw - 1440px) / 2));
  border: 1px solid #64748b;
  color: #334155;
  background: #fff;
  box-shadow: 0 -6px 20px rgba(15, 23, 42, 0.18);
}

.NekoAnalyticsConsent-copy {
  flex: 1 1 auto;
  min-width: 0;
}

.NekoAnalyticsConsent-copy p {
  margin: 0;
  color: #475569;
  font-size: 14px;
  line-height: 1.55;
}

.NekoAnalyticsConsent-privacy {
  color: #0369a1;
  text-decoration: underline;
  white-space: nowrap;
}

.NekoAnalyticsConsent-details {
  margin-left: 6px;
}

.NekoAnalyticsConsent-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 10px;
}

.NekoAnalyticsConsent-button {
  width: 124px;
  min-width: 124px;
  min-height: 46px;
  padding: 10px 18px;
  border: 1px solid #38bdf8;
  border-radius: 0;
  color: #fff;
  background: #0ea5e9;
  cursor: pointer;
  font-size: 14px;
  font-weight: 700;
}

.NekoAnalyticsConsent-button:hover {
  border-color: #7dd3fc;
  color: #fff;
  background: #0284c7;
}

.NekoAnalyticsConsent-button--selected {
  box-shadow: inset 0 0 0 2px #1e293b;
}

.NekoAnalyticsConsent-close {
  display: grid;
  width: 40px;
  height: 46px;
  padding: 0;
  border: 0;
  border-radius: 0;
  place-items: center;
  color: #475569;
  background: transparent;
  cursor: pointer;
  font-size: 24px;
  font-weight: 700;
}

.NekoAnalyticsConsent-close:hover {
  color: #0f172a;
  background: #e2e8f0;
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
  min-height: 92px;
}

@media (max-width: 720px) {
  .NekoAnalyticsConsent-banner {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
    min-height: 0;
    padding: 14px 16px;
  }

  .NekoAnalyticsConsent-actions {
    width: 100%;
  }

  .NekoAnalyticsConsent-spacer {
    min-height: 220px;
  }
}

@media (max-width: 520px) {
  .NekoAnalyticsConsent-button {
    width: 112px;
    min-width: 112px;
  }

  .NekoAnalyticsConsent-close {
    width: 34px;
  }
}
</style>
