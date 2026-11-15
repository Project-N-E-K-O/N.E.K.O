<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useData } from 'vitepress'
import { selectedAgentId, selectedRoute } from './agent-selection'
import {
  ONBOARDING_COPY,
  buildSetupPrompt,
  copyToClipboard,
  normalizeDocsLocale,
} from './coding-agents-data'

const props = withDefaults(defineProps<{ section?: 'agent' | 'prompt' }>(), {
  section: 'agent',
})

const { lang } = useData()

const locale = computed(() => normalizeDocsLocale(lang.value))
const copy = computed(() => ONBOARDING_COPY[locale.value])

const pluginIdea = ref('')
const promptExpanded = ref(false)
const copiedKey = ref<string | null>(null)
let copyTimer: ReturnType<typeof setTimeout> | null = null

const activeAgent = computed(() => {
  const list = copy.value.agents
  return list.find((item) => item.id === selectedAgentId.value) ?? list[0]
})

const generatedPrompt = computed(() =>
  buildSetupPrompt(locale.value, pluginIdea.value, selectedRoute.value),
)

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
  if (props.section !== 'agent') return
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
  <div v-if="section === 'agent'" class="NekoAgentOnboarding">
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
            <h2 class="NekoAgentOnboarding-agentTitle">{{ activeAgent.title }}</h2>
          </div>
          <div class="NekoAgentOnboarding-cardActions">
            <a
              v-if="activeAgent.downloadUrl"
              class="NekoAgentOnboarding-siteBtn"
              :href="activeAgent.downloadUrl"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ copy.downloadLabel }}
              <span aria-hidden="true">↗</span>
            </a>
          </div>
        </header>

        <div v-if="activeAgent.tutorials?.length" class="NekoAgentOnboarding-tutorials">
          <div class="NekoAgentOnboarding-fieldLabel">{{ copy.tutorialsHeading }}</div>
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

        <dl class="NekoAgentOnboarding-fields">
          <div class="NekoAgentOnboarding-field">
            <dt class="NekoAgentOnboarding-fieldLabel">{{ copy.openFolderHeading }}</dt>
            <dd class="NekoAgentOnboarding-fieldText">{{ activeAgent.openFolder }}</dd>
          </div>
          <div class="NekoAgentOnboarding-field">
            <dt class="NekoAgentOnboarding-fieldLabel">{{ copy.allowActionsHeading }}</dt>
            <dd class="NekoAgentOnboarding-fieldText">{{ activeAgent.allowActions }}</dd>
          </div>
          <div class="NekoAgentOnboarding-field">
            <dt class="NekoAgentOnboarding-fieldLabel">{{ copy.contextNoteHeading }}</dt>
            <dd class="NekoAgentOnboarding-fieldText">{{ activeAgent.contextNote }}</dd>
          </div>
        </dl>
      </section>
    </div>
  </div>

  <div v-else class="NekoAgentPrompt">
    <div class="NekoAgentOnboarding-promptBox">
      <div class="NekoAgentOnboarding-promptTop">
        <div class="NekoAgentOnboarding-promptInfo">
          <div class="NekoAgentOnboarding-promptTitle">{{ copy.promptTitle }}</div>
          <div class="NekoAgentOnboarding-promptDesc">{{ copy.promptDesc }}</div>
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

      <div class="NekoAgentOnboarding-routeSwitch" role="group">
        <button
          type="button"
          class="NekoAgentOnboarding-routeBtn"
          :class="{ 'is-active': selectedRoute === 'A' }"
          :aria-pressed="selectedRoute === 'A'"
          @click="selectedRoute = 'A'"
        >
          {{ copy.routeALabel }}
        </button>
        <button
          type="button"
          class="NekoAgentOnboarding-routeBtn"
          :class="{ 'is-active': selectedRoute === 'B' }"
          :aria-pressed="selectedRoute === 'B'"
          @click="selectedRoute = 'B'"
        >
          {{ copy.routeBLabel }}
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
  </div>
</template>

<style scoped>
.NekoAgentOnboarding {
  margin: 16px 0 24px;
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
  padding-bottom: 14px;
  border-bottom: 1px solid var(--vp-c-divider);
}

.NekoAgentOnboarding-cardMeta {
  flex: 1 1 320px;
  min-width: 0;
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

.NekoAgentOnboarding-cardActions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
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

.NekoAgentOnboarding-tutorials {
  margin-top: 14px;
}

.NekoAgentOnboarding-tutorialsRow {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
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

.NekoAgentOnboarding-fields {
  margin: 16px 0 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.NekoAgentOnboarding-field {
  margin: 0;
}

.NekoAgentOnboarding-fieldLabel {
  margin: 0;
  color: var(--vp-c-text-1);
  font-size: 13.5px;
  font-weight: 700;
}

.NekoAgentOnboarding-fieldText {
  margin: 3px 0 0;
  color: var(--vp-c-text-2);
  font-size: 13.5px;
  line-height: 1.6;
}

.NekoAgentPrompt {
  margin: 16px 0 24px;
}

.NekoAgentOnboarding-promptBox {
  padding: 14px 16px;
  border: 1px solid var(--vp-c-brand-1);
  border-radius: 10px;
  background: var(--vp-c-bg);
  box-shadow: 0 2px 12px rgba(217, 102, 198, 0.08);
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
  background: var(--vp-c-bg-soft);
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

.NekoAgentOnboarding-routeSwitch {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.NekoAgentOnboarding-routeBtn {
  display: inline-flex;
  align-items: center;
  padding: 6px 14px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 999px;
  background: var(--vp-c-bg-soft);
  color: var(--vp-c-text-1);
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.3;
  cursor: pointer;
  transition: border-color 0.18s ease, background-color 0.18s ease, color 0.18s ease;
}

.NekoAgentOnboarding-routeBtn:hover {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1);
}

.NekoAgentOnboarding-routeBtn.is-active {
  border-color: var(--vp-c-brand-1);
  background: var(--vp-c-brand-soft);
  color: var(--vp-c-brand-1);
}
</style>

<!-- Route cards written in the markdown pages live outside this component,
     so their styles must be unscoped. -->
<style>
.neko-route-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin: 12px 0 4px;
}

.neko-route-card {
  padding: 14px 16px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 12px;
  background: var(--vp-c-bg-soft);
}

.neko-route-card--recommended {
  border-color: var(--vp-c-brand-1);
}

.neko-route-card h3 {
  margin: 0 0 8px;
  padding: 0;
  border: 0;
  color: var(--vp-c-text-1);
  font-size: 15px;
  font-weight: 700;
}

.neko-route-card ul {
  margin: 0;
  padding-left: 18px;
  color: var(--vp-c-text-2);
  font-size: 13.5px;
  line-height: 1.7;
}

@media (max-width: 720px) {
  .neko-route-cards {
    grid-template-columns: 1fr;
  }
}
</style>
