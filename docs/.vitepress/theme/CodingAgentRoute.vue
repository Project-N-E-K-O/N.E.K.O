<script setup lang="ts">
import { computed } from 'vue'
import { useData } from 'vitepress'
import { resetSharedStateForSSR, selectedRoute } from './agent-selection'
import { ONBOARDING_COPY, normalizeDocsLocale } from './coding-agents-data'

const props = defineProps<{ route: 'A' | 'B' }>()

resetSharedStateForSSR()

const { lang } = useData()
const locale = computed(() => normalizeDocsLocale(lang.value))
const copy = computed(() => ONBOARDING_COPY[locale.value])

const card = computed(() => copy.value.routeCards.find((c) => c.id === props.route))
const label = computed(() =>
  props.route === 'A' ? copy.value.routeALabel : copy.value.routeBLabel,
)
const isActive = computed(() => selectedRoute.value === props.route)
const contentId = computed(() => `neko-route-${props.route.toLowerCase()}-content`)
const switchText = computed(() =>
  copy.value.routeSwitchText.replace('{route}', label.value),
)

function select() {
  selectedRoute.value = props.route
}
</script>

<template>
  <section
    :id="`neko-route-${route.toLowerCase()}`"
    class="NekoRouteSteps"
    :class="{ 'is-active': isActive }"
  >
    <div v-if="isActive" class="NekoRouteSteps-titleBar">
      <span class="NekoRouteSteps-title">{{ card?.title }}</span>
      <span class="NekoRouteSteps-tag">{{ copy.routeCurrentTag }}</span>
    </div>
    <button
      v-else
      type="button"
      class="NekoRouteSteps-collapsed"
      :aria-expanded="false"
      :aria-controls="contentId"
      @click="select"
    >
      {{ switchText }} ▸
    </button>
    <div v-show="isActive" :id="contentId" class="NekoRouteSteps-body">
      <slot />
    </div>
  </section>
</template>

<style>
.NekoRouteSteps {
  margin: 16px 0;
  border: 1px solid var(--vp-c-divider);
  border-radius: 14px;
  background: var(--vp-c-bg-soft);
}

.NekoRouteSteps.is-active {
  border-color: var(--vp-c-brand-1);
}

.NekoRouteSteps-titleBar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 14px 20px 12px;
  border-bottom: 1px solid var(--vp-c-divider);
}

.NekoRouteSteps-title {
  color: var(--vp-c-text-1);
  font-size: 15px;
  font-weight: 700;
}

.NekoRouteSteps-tag {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--vp-c-brand-1);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
}

.NekoRouteSteps-collapsed {
  display: block;
  width: 100%;
  padding: 13px 20px;
  border: 0;
  background: transparent;
  color: var(--vp-c-text-2);
  font-size: 13.5px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}

.NekoRouteSteps-collapsed:hover {
  color: var(--vp-c-brand-1);
}

.NekoRouteSteps-body {
  padding: 4px 20px 18px;
}
</style>
