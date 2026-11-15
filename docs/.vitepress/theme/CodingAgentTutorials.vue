<script setup lang="ts">
import { computed } from 'vue'
import { useData, withBase } from 'vitepress'
import {
  ONBOARDING_COPY,
  TUTORIALS_COPY,
  normalizeDocsLocale,
} from './coding-agents-data'

const { lang } = useData()

const locale = computed(() => normalizeDocsLocale(lang.value))
const copy = computed(() => TUTORIALS_COPY[locale.value])
const agents = computed(() =>
  ONBOARDING_COPY[locale.value].agents.filter((agent) => agent.tutorials?.length),
)

function shotUrl(image: string): string {
  return withBase(`/images/plugins/onboarding/${image}`)
}
</script>

<template>
  <div class="NekoAgentTutorials">
    <p class="NekoAgentTutorials-intro">{{ copy.pageIntro }}</p>

    <div class="NekoAgentTutorials-grid">
      <article
        v-for="agent in agents"
        :key="agent.id"
        class="NekoAgentTutorials-card"
      >
        <span class="NekoAgentTutorials-eyebrow">{{ agent.eyebrow }}</span>
        <h3 class="NekoAgentTutorials-title">{{ agent.title }}</h3>
        <p class="NekoAgentTutorials-tagline">{{ agent.tagline }}</p>
        <div class="NekoAgentTutorials-links">
          <a
            v-for="tutorial in agent.tutorials"
            :key="tutorial.url"
            class="NekoAgentTutorials-link"
            :href="tutorial.url"
            target="_blank"
            rel="noopener noreferrer"
          >
            <span aria-hidden="true">{{ tutorial.kind === 'video' ? '▶' : '📄' }}</span>
            <span>{{ tutorial.label }}</span>
            <span aria-hidden="true">↗</span>
          </a>
        </div>
      </article>
    </div>

    <h2 class="NekoAgentTutorials-nekoHeading">{{ copy.nekoHeading }}</h2>
    <p class="NekoAgentTutorials-nekoIntro">{{ copy.nekoIntro }}</p>

    <figure
      v-for="step in copy.nekoSteps"
      :key="step.image"
      class="NekoAgentTutorials-shot"
    >
      <img :src="shotUrl(step.image)" :alt="step.alt" loading="lazy" />
      <figcaption class="NekoAgentTutorials-caption">
        <strong>{{ step.title }}</strong>
        <span>{{ step.description }}</span>
      </figcaption>
    </figure>
  </div>
</template>

<style scoped>
.NekoAgentTutorials {
  margin: 16px 0 32px;
}

.NekoAgentTutorials-intro {
  margin: 0 0 20px !important;
  color: var(--vp-c-text-2);
  font-size: 14.5px;
  line-height: 1.7;
}

.NekoAgentTutorials-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
  margin-bottom: 36px;
}

.NekoAgentTutorials-card {
  padding: 18px 20px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 14px;
  background: var(--vp-c-bg-soft);
}

.NekoAgentTutorials-eyebrow {
  display: inline-block;
  margin-bottom: 4px;
  color: var(--vp-c-brand-1);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.NekoAgentTutorials-title {
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
  color: var(--vp-c-text-1);
  font-size: 17px !important;
  font-weight: 700;
  line-height: 1.4 !important;
}

.NekoAgentTutorials-tagline {
  margin: 6px 0 14px !important;
  color: var(--vp-c-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.NekoAgentTutorials-links {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.NekoAgentTutorials-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  background: var(--vp-c-bg);
  color: var(--vp-c-text-1) !important;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
  text-decoration: none !important;
  transition: border-color 0.18s ease, color 0.18s ease;
}

.NekoAgentTutorials-link:hover {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1) !important;
}

.NekoAgentTutorials-nekoHeading {
  margin: 0 0 8px !important;
  padding: 0 !important;
  border: 0 !important;
  color: var(--vp-c-text-1);
  font-size: 20px !important;
  font-weight: 700;
  line-height: 1.4 !important;
}

.NekoAgentTutorials-nekoIntro {
  margin: 0 0 18px !important;
  color: var(--vp-c-text-2);
  font-size: 14px;
  line-height: 1.7;
}

.NekoAgentTutorials-shot {
  margin: 0 0 22px;
}

.NekoAgentTutorials-shot img {
  display: block;
  width: 100%;
  max-width: 960px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 12px;
}

.NekoAgentTutorials-caption {
  margin-top: 8px;
  max-width: 960px;
  color: var(--vp-c-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.NekoAgentTutorials-caption strong {
  display: block;
  color: var(--vp-c-text-1);
  font-size: 13.5px;
}
</style>
