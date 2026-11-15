<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useData, withBase } from 'vitepress'
import {
  ONBOARDING_COPY,
  buildSetupPrompt,
  copyToClipboard,
  normalizeDocsLocale,
} from './coding-agents-data'

const { lang } = useData()

const locale = computed(() => normalizeDocsLocale(lang.value))
const copy = computed(() => ONBOARDING_COPY[locale.value])

const selectedAgentId = ref<string>('')
const pluginIdea = ref('')
const promptExpanded = ref(false)
const copiedKey = ref<string | null>(null)
let copyTimer: ReturnType<typeof setTimeout> | null = null

const activeAgent = computed(() => {
  const list = copy.value.agents
  return list.find((item) => item.id === selectedAgentId.value) ?? list[0]
})

const generatedPrompt = computed(() =>
  buildSetupPrompt(locale.value, pluginIdea.value),
)

const guideBaseUrl = computed(() => {
  if (locale.value === 'zh-CN') return withBase('/zh-CN/plugins/coding-agent-guide')
  if (locale.value === 'ja') return withBase('/ja/plugins/coding-agent-guide')
  return withBase('/plugins/coding-agent-guide')
})

watch(
  locale,
  () => {
    const exists = copy.value.agents.some((a) => a.id === selectedAgentId.value)
    if (!exists) {
      selectedAgentId.value = copy.value.agents[0].id
    }
  },
  { immediate: true },
)

function isExternalUrl(url: string): boolean {
  return /^https?:\/\//i.test(url)
}

function resolveAgentUrl(url: string): string {
  return isExternalUrl(url) ? url : withBase(url)
}

async function writeClipboard(text: string, key: string) {
  const ok = await copyToClipboard(text)
  copiedKey.value = ok ? key : null
  if (!ok) return
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copiedKey.value = null
  }, 2200)
}

function selectAgent(id: string) {
  selectedAgentId.value = id
}

function onTabKeydown(event: KeyboardEvent, index: number) {
  const agents = copy.value.agents
  if (!agents.length) return
  let nextIndex = -1
  if (event.key === 'ArrowRight') {
    nextIndex = (index + 1) % agents.length
  } else if (event.key === 'ArrowLeft') {
    nextIndex = (index - 1 + agents.length) % agents.length
  } else if (event.key === 'Home') {
    nextIndex = 0
  } else if (event.key === 'End') {
    nextIndex = agents.length - 1
  }
  if (nextIndex >= 0) {
    event.preventDefault()
    selectedAgentId.value = agents[nextIndex].id
    const tabEl = document.getElementById(`neko-agent-tab-${agents[nextIndex].id}`)
    tabEl?.focus()
  }
}

onMounted(() => {
  const params = new URLSearchParams(window.location.search)
  const requested = params.get('agent')
  if (requested && copy.value.agents.some((a) => a.id === requested)) {
    selectedAgentId.value = requested
  }
})

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
})
</script>

<template>
  <div class="NekoAgentOnboarding">
    <div class="NekoAgentOnboarding-prep">
      <span class="NekoAgentOnboarding-prepBadge">{{ copy.prepBadge }}</span>
      <p class="NekoAgentOnboarding-prepText">{{ copy.prepText }}</p>
    </div>

    <div class="NekoAgentOnboarding-shell">
      <div class="NekoAgentOnboarding-tabHeader">
        <span class="NekoAgentOnboarding-tabTitle">{{ copy.tabHeading }}</span>
        <span class="NekoAgentOnboarding-tabHint">（{{ copy.tabHint }}）</span>
      </div>

      <div
        class="NekoAgentOnboarding-tabs"
        role="tablist"
        :aria-label="copy.tabHeading"
      >
        <button
          v-for="(agent, idx) in copy.agents"
          :id="`neko-agent-tab-${agent.id}`"
          :key="agent.id"
          type="button"
          role="tab"
          class="NekoAgentOnboarding-tab"
          :class="{ 'is-active': activeAgent.id === agent.id }"
          :aria-selected="activeAgent.id === agent.id"
          :aria-controls="`neko-agent-panel-${agent.id}`"
          :tabindex="activeAgent.id === agent.id ? 0 : -1"
          @click="selectAgent(agent.id)"
          @keydown="onTabKeydown($event, idx)"
        >
          <span class="NekoAgentOnboarding-tabIndex">{{ idx + 1 }}</span>
          <span>{{ agent.label }}</span>
        </button>
      </div>

      <section
        :id="`neko-agent-panel-${activeAgent.id}`"
        class="NekoAgentOnboarding-card"
        role="tabpanel"
        :aria-labelledby="`neko-agent-tab-${activeAgent.id}`"
      >
        <header class="NekoAgentOnboarding-cardHeader">
          <div class="NekoAgentOnboarding-cardMeta">
            <span class="NekoAgentOnboarding-eyebrow">{{ activeAgent.eyebrow }}</span>
            <h2 class="NekoAgentOnboarding-agentTitle">{{ activeAgent.title }}</h2>
            <p class="NekoAgentOnboarding-tagline">{{ activeAgent.tagline }}</p>
          </div>
          <div class="NekoAgentOnboarding-cardActions">
            <a
              class="NekoAgentOnboarding-siteBtn"
              :href="resolveAgentUrl(activeAgent.websiteUrl)"
              :target="isExternalUrl(activeAgent.websiteUrl) ? '_blank' : undefined"
              :rel="isExternalUrl(activeAgent.websiteUrl) ? 'noopener noreferrer' : undefined"
            >
              {{ activeAgent.websiteLabel }}
              <span v-if="isExternalUrl(activeAgent.websiteUrl)" aria-hidden="true">↗</span>
            </a>
          </div>
        </header>

        <div class="NekoAgentOnboarding-stepsLabel">{{ copy.stepsHeading }}</div>

        <ol class="NekoAgentOnboarding-steps">
          <li
            v-for="(step, sIdx) in activeAgent.steps"
            :key="`${activeAgent.id}-${sIdx}`"
            class="NekoAgentOnboarding-step"
            :class="{ 'NekoAgentOnboarding-step--prompt': step.isPromptStep }"
          >
            <div class="NekoAgentOnboarding-stepRow">
              <span class="NekoAgentOnboarding-stepNum">{{ sIdx + 1 }}</span>
              <span class="NekoAgentOnboarding-stepText">{{ step.text }}</span>
            </div>

            <div v-if="step.isPromptStep" class="NekoAgentOnboarding-promptBox">
              <div class="NekoAgentOnboarding-promptTop">
                <div class="NekoAgentOnboarding-promptInfo">
                  <div class="NekoAgentOnboarding-promptTitle">{{ copy.promptStepTitle }}</div>
                  <div class="NekoAgentOnboarding-promptDesc">{{ copy.promptStepDesc }}</div>
                </div>
                <button
                  type="button"
                  class="NekoAgentOnboarding-primaryCopy"
                  :class="{ 'is-copied': copiedKey === 'setup-prompt' }"
                  @click="writeClipboard(generatedPrompt, 'setup-prompt')"
                >
                  <span>{{
                    copiedKey === 'setup-prompt' ? `✓ ${copy.copiedPrompt}` : copy.copyPrompt
                  }}</span>
                </button>
              </div>

              <label class="NekoAgentOnboarding-ideaField">
                <span class="NekoAgentOnboarding-ideaLabel">{{ copy.ideaLabel }}</span>
                <input
                  v-model="pluginIdea"
                  type="text"
                  class="NekoAgentOnboarding-ideaInput"
                  :placeholder="copy.ideaPlaceholder"
                />
              </label>

              <div class="NekoAgentOnboarding-promptFooter">
                <button
                  type="button"
                  class="NekoAgentOnboarding-previewToggle"
                  :aria-expanded="promptExpanded"
                  @click="promptExpanded = !promptExpanded"
                >
                  {{ promptExpanded ? `${copy.hidePrompt} ▴` : `${copy.showPrompt} ▾` }}
                </button>
              </div>

              <pre v-if="promptExpanded" class="NekoAgentOnboarding-promptPre"><code>{{ generatedPrompt }}</code></pre>
            </div>
          </li>
        </ol>

        <div
          v-if="activeAgent.tutorials?.length"
          class="NekoAgentOnboarding-tutorials"
        >
          <div class="NekoAgentOnboarding-tutorialsLabel">{{ copy.tutorialsHeading }}</div>
          <div class="NekoAgentOnboarding-tutorialsRow">
            <a
              v-for="tutorial in activeAgent.tutorials"
              :key="tutorial.url"
              class="NekoAgentOnboarding-tutorialBtn"
              :href="tutorial.url"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span aria-hidden="true">{{ tutorial.kind === 'video' ? '▶' : '📄' }}</span>
              <span>{{ tutorial.label }}</span>
              <span aria-hidden="true">↗</span>
            </a>
          </div>
        </div>

        <div class="NekoAgentOnboarding-fallbacks">
          <details
            v-for="(fb, fIdx) in copy.fallbacks"
            :key="fIdx"
            class="NekoAgentOnboarding-fallback"
          >
            <summary class="NekoAgentOnboarding-fallbackSummary">
              <span>{{ fb.title }}</span>
            </summary>
            <div class="NekoAgentOnboarding-fallbackBody">
              <p class="NekoAgentOnboarding-fallbackIntro">{{ fb.intro }}</p>
              <div
                v-for="(item, iIdx) in fb.items"
                :key="iIdx"
                class="NekoAgentOnboarding-fallbackItem"
              >
                <div class="NekoAgentOnboarding-fallbackItemTitle">{{ item.label }}</div>
                <div class="NekoAgentOnboarding-fallbackItemDesc">{{ item.description }}</div>
              </div>
            </div>
          </details>
        </div>
      </section>
    </div>

    <div class="NekoAgentOnboarding-walkthroughBanner">
      <div class="NekoAgentOnboarding-walkthroughCopy">
        <div class="NekoAgentOnboarding-walkthroughTitle">{{ copy.tutorialsCtaTitle }}</div>
        <div class="NekoAgentOnboarding-walkthroughDesc">{{ copy.tutorialsCtaDesc }}</div>
      </div>
      <div class="NekoAgentOnboarding-walkthroughLinks">
        <a class="NekoAgentOnboarding-ctaLink is-primary" :href="guideBaseUrl">
          {{ copy.tutorialsCtaLabel }} →
        </a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.NekoAgentOnboarding {
  margin: 20px 0 32px;
}

.NekoAgentOnboarding-prep {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 18px;
  margin-bottom: 18px;
  border: 1px solid var(--vp-c-brand-soft);
  border-radius: 12px;
  background: var(--vp-c-brand-soft);
}

.NekoAgentOnboarding-prepBadge {
  flex: 0 0 auto;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--vp-c-brand-1);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  line-height: 20px;
}

.NekoAgentOnboarding-prepText {
  margin: 0 !important;
  color: var(--vp-c-text-1);
  font-size: 14px;
  line-height: 1.65;
}

.NekoAgentOnboarding-shell {
  padding: 20px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 16px;
  background: var(--vp-c-bg-soft);
}

.NekoAgentOnboarding-tabHeader {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 14px;
}

.NekoAgentOnboarding-tabTitle {
  color: var(--vp-c-text-1);
  font-size: 15px;
  font-weight: 700;
}

.NekoAgentOnboarding-tabHint {
  color: var(--vp-c-text-2);
  font-size: 13px;
}

.NekoAgentOnboarding-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 18px;
}

.NekoAgentOnboarding-tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 14px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 999px;
  background: var(--vp-c-bg);
  color: var(--vp-c-text-1);
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.3;
  cursor: pointer;
  transition: border-color 0.18s ease, background-color 0.18s ease, color 0.18s ease;
}

.NekoAgentOnboarding-tab:hover {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1);
}

.NekoAgentOnboarding-tab.is-active {
  border-color: var(--vp-c-brand-1);
  background: var(--vp-c-brand-soft);
  color: var(--vp-c-brand-1);
}

.NekoAgentOnboarding-tabIndex {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 6px;
  background: var(--vp-c-bg-soft);
  color: var(--vp-c-text-2);
  font-size: 11px;
  font-weight: 700;
}

.NekoAgentOnboarding-tab.is-active .NekoAgentOnboarding-tabIndex {
  background: var(--vp-c-brand-1);
  color: #fff;
}

.NekoAgentOnboarding-card {
  padding: 22px 24px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 14px;
  background: var(--vp-c-bg);
}

.NekoAgentOnboarding-cardHeader {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--vp-c-divider);
}

.NekoAgentOnboarding-cardMeta {
  flex: 1 1 320px;
  min-width: 0;
}

.NekoAgentOnboarding-eyebrow {
  display: inline-block;
  margin-bottom: 4px;
  color: var(--vp-c-brand-1);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.NekoAgentOnboarding-agentTitle {
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  color: var(--vp-c-text-1);
  font-size: 20px !important;
  font-weight: 700;
  line-height: 1.35 !important;
}

.NekoAgentOnboarding-tagline {
  margin: 6px 0 0 !important;
  color: var(--vp-c-text-2);
  font-size: 13.5px;
  line-height: 1.55;
}

.NekoAgentOnboarding-cardActions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.NekoAgentOnboarding-tutorials {
  margin-top: 16px;
}

.NekoAgentOnboarding-tutorialsLabel {
  margin-bottom: 8px;
  color: var(--vp-c-text-1);
  font-size: 13.5px;
  font-weight: 700;
}

.NekoAgentOnboarding-tutorialsRow {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.NekoAgentOnboarding-tutorialBtn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 999px;
  background: var(--vp-c-bg-soft);
  color: var(--vp-c-text-1) !important;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.3;
  text-decoration: none !important;
  transition: border-color 0.18s ease, color 0.18s ease;
}

.NekoAgentOnboarding-tutorialBtn:hover {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1) !important;
}

.NekoAgentOnboarding-siteBtn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 14px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 999px;
  background: var(--vp-c-bg-soft);
  color: var(--vp-c-text-1) !important;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none !important;
  transition: border-color 0.18s ease, color 0.18s ease;
}

.NekoAgentOnboarding-siteBtn:hover {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1) !important;
}

.NekoAgentOnboarding-stepsLabel {
  margin: 16px 0 10px;
  color: var(--vp-c-text-1);
  font-size: 14px;
  font-weight: 700;
}

.NekoAgentOnboarding-steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0 !important;
  padding: 0 !important;
  list-style: none !important;
}

.NekoAgentOnboarding-step {
  margin: 0 !important;
  padding: 12px 16px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 10px;
  background: var(--vp-c-bg-soft);
}

.NekoAgentOnboarding-step--prompt {
  border-color: var(--vp-c-brand-1);
  background: var(--vp-c-bg);
  box-shadow: 0 2px 12px rgba(217, 102, 198, 0.08);
}

.NekoAgentOnboarding-stepRow {
  display: flex;
  align-items: center;
  gap: 12px;
}

.NekoAgentOnboarding-stepNum {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--vp-c-bg-mute);
  color: var(--vp-c-text-1);
  font-size: 12.5px;
  font-weight: 700;
}

.NekoAgentOnboarding-step--prompt .NekoAgentOnboarding-stepNum {
  background: var(--vp-c-brand-1);
  color: #fff;
}

.NekoAgentOnboarding-stepText {
  color: var(--vp-c-text-1);
  font-size: 14px;
  line-height: 1.55;
}

.NekoAgentOnboarding-promptBox {
  margin-top: 12px;
  margin-left: 36px;
  padding: 14px 16px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 10px;
  background: var(--vp-c-bg-soft);
}

.NekoAgentOnboarding-promptTop {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.NekoAgentOnboarding-promptInfo {
  flex: 1 1 240px;
  min-width: 0;
}

.NekoAgentOnboarding-promptTitle {
  color: var(--vp-c-text-1);
  font-size: 14px;
  font-weight: 700;
}

.NekoAgentOnboarding-promptDesc {
  margin-top: 2px;
  color: var(--vp-c-text-2);
  font-size: 12.5px;
}

.NekoAgentOnboarding-primaryCopy {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  padding: 9px 18px;
  border: 1px solid var(--vp-c-brand-1);
  border-radius: 8px;
  background: var(--vp-c-brand-1);
  color: #fff;
  font-size: 13.5px;
  font-weight: 700;
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease;
}

.NekoAgentOnboarding-primaryCopy:hover {
  border-color: var(--vp-c-brand-2);
  background: var(--vp-c-brand-2);
}

.NekoAgentOnboarding-primaryCopy.is-copied {
  border-color: #16a34a;
  background: #16a34a;
}

.NekoAgentOnboarding-ideaField {
  display: block;
  margin-top: 12px;
}

.NekoAgentOnboarding-ideaLabel {
  display: block;
  margin-bottom: 6px;
  color: var(--vp-c-text-2);
  font-size: 12.5px;
}

.NekoAgentOnboarding-ideaInput {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 12px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  background: var(--vp-c-bg);
  color: var(--vp-c-text-1);
  font-size: 13.5px;
  line-height: 1.4;
}

.NekoAgentOnboarding-ideaInput:focus {
  outline: none;
  border-color: var(--vp-c-brand-1);
}

.NekoAgentOnboarding-promptFooter {
  margin-top: 10px;
}

.NekoAgentOnboarding-previewToggle {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--vp-c-brand-1);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}

.NekoAgentOnboarding-previewToggle:hover {
  text-decoration: underline;
}

.NekoAgentOnboarding-promptPre {
  max-height: 320px;
  overflow: auto;
  margin: 10px 0 0 !important;
  padding: 12px 14px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  background: var(--vp-code-block-bg);
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.NekoAgentOnboarding-fallbacks {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 18px;
}

.NekoAgentOnboarding-fallback {
  border: 1px solid var(--vp-c-divider);
  border-radius: 10px;
  background: var(--vp-c-bg-soft);
}

.NekoAgentOnboarding-fallbackSummary {
  padding: 10px 14px;
  color: var(--vp-c-text-2);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.NekoAgentOnboarding-fallbackSummary:hover {
  color: var(--vp-c-brand-1);
}

.NekoAgentOnboarding-fallbackBody {
  padding: 4px 14px 14px;
  border-top: 1px solid var(--vp-c-divider);
}

.NekoAgentOnboarding-fallbackIntro {
  margin: 10px 0 !important;
  color: var(--vp-c-text-2);
  font-size: 13px;
}

.NekoAgentOnboarding-fallbackItem {
  margin-top: 10px;
}

.NekoAgentOnboarding-fallbackItemTitle {
  color: var(--vp-c-text-1);
  font-size: 13px;
  font-weight: 700;
}

.NekoAgentOnboarding-fallbackItemDesc {
  margin-top: 2px;
  color: var(--vp-c-text-2);
  font-size: 12.5px;
}

.NekoAgentOnboarding-walkthroughBanner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 16px;
  padding: 16px 20px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 14px;
  background: var(--vp-c-bg-soft);
}

.NekoAgentOnboarding-walkthroughCopy {
  flex: 1 1 280px;
}

.NekoAgentOnboarding-walkthroughTitle {
  color: var(--vp-c-text-1);
  font-size: 14.5px;
  font-weight: 700;
}

.NekoAgentOnboarding-walkthroughDesc {
  margin-top: 3px;
  color: var(--vp-c-text-2);
  font-size: 13px;
}

.NekoAgentOnboarding-walkthroughLinks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.NekoAgentOnboarding-ctaLink {
  display: inline-flex;
  align-items: center;
  padding: 8px 14px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  background: var(--vp-c-bg);
  color: var(--vp-c-text-1) !important;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none !important;
  transition: border-color 0.18s ease, color 0.18s ease;
}

.NekoAgentOnboarding-ctaLink:hover {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1) !important;
}

.NekoAgentOnboarding-ctaLink.is-primary {
  border-color: var(--vp-c-brand-1);
  background: var(--vp-c-brand-soft);
  color: var(--vp-c-brand-1) !important;
}

@media (max-width: 640px) {
  .NekoAgentOnboarding-shell {
    padding: 14px;
  }

  .NekoAgentOnboarding-card {
    padding: 16px;
  }

  .NekoAgentOnboarding-promptBox {
    margin-left: 0;
  }
}
</style>
