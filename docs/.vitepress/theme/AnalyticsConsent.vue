<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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
    title: 'Help us improve the docs',
    body: 'With your permission, we use Google Analytics to understand which documentation pages are useful. Google Analytics is not loaded until you accept, and advertising storage remains disabled.',
    accept: 'Accept analytics',
    reject: 'Reject',
    settings: 'Analytics settings',
    close: 'Close',
    notice: 'Analytics & cookie notice',
    current: 'Current choice',
    granted: 'Analytics enabled',
    denied: 'Analytics disabled',
  },
  'zh-CN': {
    title: '帮助我们改进文档',
    body: '经你同意后，我们会使用 Google Analytics 了解哪些文档页面更有帮助。你接受前不会加载 Google Analytics，广告存储始终保持关闭。',
    accept: '同意分析统计',
    reject: '拒绝',
    settings: '分析统计设置',
    close: '关闭',
    notice: '分析统计与 Cookie 说明',
    current: '当前选择',
    granted: '已启用分析统计',
    denied: '已关闭分析统计',
  },
  ja: {
    title: 'ドキュメント改善へのご協力',
    body: '許可いただいた場合に限り、役立つページを把握するため Google Analytics を使用します。同意するまで Google Analytics は読み込まれず、広告用ストレージは常に無効です。',
    accept: 'アクセス解析を許可',
    reject: '拒否',
    settings: 'アクセス解析の設定',
    close: '閉じる',
    notice: 'アクセス解析と Cookie に関するお知らせ',
    current: '現在の選択',
    granted: 'アクセス解析は有効です',
    denied: 'アクセス解析は無効です',
  },
} as const

const { lang } = useData()
const ready = ref(false)
const panelOpen = ref(false)
const choice = ref<ConsentChoice>(null)

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
const statusText = computed(() =>
  choice.value === 'granted' ? copy.value.granted : copy.value.denied,
)

function syncChoice(event?: Event) {
  const eventChoice = (event as CustomEvent<{ choice?: ConsentChoice }>)
    ?.detail?.choice
  choice.value = eventChoice || getAnalyticsConsent()
}

function accept() {
  acceptGoogleAnalytics()
  choice.value = 'granted'
  panelOpen.value = false
}

function reject() {
  const wasActive = rejectGoogleAnalytics()
  choice.value = 'denied'
  if (!wasActive) panelOpen.value = false
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
  <div v-if="ready">
    <button
      v-if="choice !== null && !panelOpen"
      class="NekoAnalyticsConsent-settings"
      type="button"
      @click="panelOpen = true"
    >
      {{ copy.settings }}
    </button>

    <div
      v-if="panelOpen"
      class="NekoAnalyticsConsent-overlay"
      role="presentation"
    >
      <section
        class="NekoAnalyticsConsent-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="neko-analytics-consent-title"
        aria-describedby="neko-analytics-consent-description"
      >
        <button
          v-if="choice !== null"
          class="NekoAnalyticsConsent-close"
          type="button"
          :aria-label="copy.close"
          @click="panelOpen = false"
        >
          ×
        </button>
        <h2 id="neko-analytics-consent-title">
          {{ copy.title }}
        </h2>
        <p id="neko-analytics-consent-description">
          {{ copy.body }}
        </p>
        <p v-if="choice !== null" class="NekoAnalyticsConsent-status">
          <strong>{{ copy.current }}:</strong> {{ statusText }}
        </p>
        <a class="NekoAnalyticsConsent-notice" :href="privacyPath">
          {{ copy.notice }}
        </a>
        <div class="NekoAnalyticsConsent-actions">
          <button
            class="NekoAnalyticsConsent-button NekoAnalyticsConsent-button--primary"
            type="button"
            @click="accept"
          >
            {{ copy.accept }}
          </button>
          <button
            class="NekoAnalyticsConsent-button"
            type="button"
            @click="reject"
          >
            {{ copy.reject }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.NekoAnalyticsConsent-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 24px;
  background: rgba(16, 18, 27, 0.62);
}

.NekoAnalyticsConsent-panel {
  position: relative;
  width: min(100%, 640px);
  padding: 24px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 16px;
  color: var(--vp-c-text-1);
  background: var(--vp-c-bg-elv);
  box-shadow: var(--vp-shadow-5);
}

.NekoAnalyticsConsent-panel h2 {
  margin: 0 40px 8px 0;
  border: 0;
  font-size: 20px;
  line-height: 1.4;
}

.NekoAnalyticsConsent-panel p {
  margin: 0 0 12px;
  color: var(--vp-c-text-2);
  font-size: 14px;
  line-height: 1.65;
}

.NekoAnalyticsConsent-panel .NekoAnalyticsConsent-status {
  color: var(--vp-c-text-1);
}

.NekoAnalyticsConsent-close {
  position: absolute;
  top: 16px;
  right: 16px;
  display: grid;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  place-items: center;
  color: var(--vp-c-text-2);
  background: transparent;
  cursor: pointer;
  font-size: 24px;
}

.NekoAnalyticsConsent-close:hover {
  color: var(--vp-c-text-1);
  background: var(--vp-c-default-soft);
}

.NekoAnalyticsConsent-notice {
  display: inline-block;
  color: var(--vp-c-brand-1);
  font-size: 14px;
  font-weight: 600;
}

.NekoAnalyticsConsent-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 20px;
}

.NekoAnalyticsConsent-button,
.NekoAnalyticsConsent-settings {
  border: 1px solid var(--vp-c-divider);
  border-radius: 9px;
  color: var(--vp-c-text-1);
  background: var(--vp-c-bg-soft);
  cursor: pointer;
  font-weight: 600;
}

.NekoAnalyticsConsent-button {
  min-height: 40px;
  padding: 8px 16px;
}

.NekoAnalyticsConsent-button:hover,
.NekoAnalyticsConsent-settings:hover {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1);
}

.NekoAnalyticsConsent-button--primary {
  border-color: var(--vp-c-brand-1);
  color: #fff;
  background: var(--vp-c-brand-1);
}

.NekoAnalyticsConsent-button--primary:hover {
  border-color: var(--vp-c-brand-2);
  color: #fff;
  background: var(--vp-c-brand-2);
}

.NekoAnalyticsConsent-settings {
  position: fixed;
  z-index: 50;
  right: 16px;
  bottom: 16px;
  min-height: 34px;
  padding: 6px 11px;
  box-shadow: var(--vp-shadow-2);
  font-size: 12px;
}

@media (max-width: 640px) {
  .NekoAnalyticsConsent-overlay {
    padding: 12px;
  }

  .NekoAnalyticsConsent-panel {
    padding: 20px;
  }

  .NekoAnalyticsConsent-actions {
    display: grid;
  }

  .NekoAnalyticsConsent-button {
    width: 100%;
  }
}
</style>
