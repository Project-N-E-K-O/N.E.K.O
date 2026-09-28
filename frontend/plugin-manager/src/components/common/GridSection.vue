<template>
  <Transition
    appear
    :css="false"
    @leave-cancelled="cancel"
    @enter="enterSection"
    @leave="leaveSection"
  >
    <section
      v-if="items.length > 0"
      class="grid-section"
      :class="[
        sectionClass,
        { 'grid-section--large': items.length > motionPolicy.largeList },
      ]"
      :data-yui-guide-id="sectionGuideId"
    >
      <div
        v-if="$slots.header || title"
        class="grid-section__header"
        :class="headerClass"
        :data-yui-guide-id="headerGuideId"
      >
        <slot name="header">
          <span class="grid-section__title">
            <el-icon v-if="icon"><component :is="icon" /></el-icon>
            {{ title }}
            <span class="grid-section__title-count">
              (
              <Transition name="count-fade" mode="out-in">
                <span :key="items.length">{{ items.length }}</span>
              </Transition>
              )
            </span>
          </span>
        </slot>
      </div>

      <TransitionGroup
        :css="false"
        :move-class="motionPhase === 'filter' || items.length > motionPolicy.largeList ? 'grid-item-still' : 'grid-item-move'"
        @enter="enterItem"
        @leave="leaveItem"
        @leave-cancelled="element => { cancel(element); clearLeavingItemStyles(element) }"
        tag="div"
        class="grid-section__grid"
        :class="gridLayoutClass"
        @before-leave="pinLeavingItem"
        @after-leave="clearLeavingItemStyles"
      >
        <div
          v-for="(item, index) in items"
          :key="item.id"
          class="grid-section__item"
          :class="itemClass(item)"
          :data-yui-guide-id="itemGuideIdFor(item, index)"
          :data-motion-index="index"
        >
          <Transition name="check-pop">
            <button
              v-if="multiSelectEnabled"
              type="button"
              class="grid-section__select"
              :aria-pressed="isItemSelected(item.id)"
              :aria-label="t('common.toggleSelection')"
              @click.stop="$emit('toggle-selection', item.id)"
            >
              <div
                class="grid-section__check"
                :class="{ 'grid-section__check--checked': isItemSelected(item.id) }"
              >
                <svg
                  v-if="isItemSelected(item.id)"
                  class="grid-section__check-icon"
                  viewBox="0 0 16 16"
                  fill="none"
                >
                  <path
                    d="M3.5 8.5L6.5 11.5L12.5 4.5"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </div>
            </button>
          </Transition>

          <slot
            name="item"
            :item="item"
            :index="index"
            :layout-mode="layoutMode"
            :is-selected="isItemSelected(item.id)"
            :multi-select-enabled="multiSelectEnabled"
          />
        </div>
      </TransitionGroup>
    </section>
  </Transition>
</template>

<script setup lang="ts" generic="T extends { id: string }">
import { computed, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGridMotionController } from '@/motion/grid'
import { motionPolicy } from '@/motion/policy'
import type { LayoutMode } from '@/composables/useGridWorkbench'

const props = withDefaults(defineProps<{
  title?: string
  icon?: Component
  items: T[]
  layoutMode: LayoutMode
  multiSelectEnabled: boolean
  selectedIds: string[]
  variant?: string
  /** 可选：用于 data-yui-guide-id 的前缀（如 "plugin-list"）。 */
  guidePrefix?: string
  /** Lightweight updates used while the user is actively filtering. */
  motionPhase?: 'initial' | 'filter'
}>(), {
  title: undefined,
  icon: undefined,
  variant: 'default',
  guidePrefix: undefined,
  motionPhase: 'initial',
})

defineEmits<{
  'toggle-selection': [id: string]
}>()

const { t } = useI18n()

const {
  pinLeavingItem, clearLeavingItemStyles, enterSection, leaveSection,
  enterItem, leaveItem, cancel,
} = useGridMotionController({
  phase: () => props.motionPhase,
})
const gridLayoutClass = computed(() => `grid-section__grid--${props.layoutMode}`)

const headerClass = computed(() => {
  if (props.variant === 'adapter') return 'grid-section__header--adapter'
  return ''
})

const sectionClass = computed(() => {
  if (props.variant === 'adapter') return 'grid-section--adapter'
  return ''
})

const sectionGuideId = computed(() =>
  props.guidePrefix ? `${props.guidePrefix}-section-${props.variant}` : undefined,
)
const headerGuideId = computed(() =>
  props.guidePrefix ? `${props.guidePrefix}-section-${props.variant}-header` : undefined,
)

function itemGuideIdFor(item: T, index: number): string | undefined {
  if (!props.guidePrefix) return undefined
  return index === 0
    ? `${props.guidePrefix}-card`
    : `${props.guidePrefix}-card-${item.id}`
}

const selectedIdSet = computed(() => new Set(props.selectedIds))

function isItemSelected(id: string): boolean {
  return selectedIdSet.value.has(id)
}

function itemClass(item: T) {
  const selected = props.multiSelectEnabled && isItemSelected(item.id)
  return {
    'grid-section__item--selection-mode': props.multiSelectEnabled,
    'grid-section__item--selected': selected,
    'grid-section__item--list-layout': props.layoutMode === 'list',
  }
}
</script>

<style scoped>
.grid-section {
  position: relative;
}

.grid-section--adapter {
  margin-top: 24px;
}

.grid-section__header {
  margin-bottom: 12px;
}

.grid-section__title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.grid-section__title-count {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  min-width: 2ch;
}

.grid-section__grid {
  display: grid;
  gap: 16px;
  align-items: stretch;
  position: relative;
  transition: grid-template-columns 0.24s cubic-bezier(0.22, 1, 0.36, 1);
}

.grid-section__grid--list,
.grid-section__grid--single {
  grid-template-columns: 1fr;
}

.grid-section__grid--double {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 360px), 1fr));
}

.grid-section__grid--compact {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 240px), 1fr));
}

.grid-section__item {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
}

/* Swallows hover while scrolling. A shield instead of pointer-events on the
   grid: that inherited toggle restyles every card node at scroll start/stop. */
[data-scrolling] .grid-section__grid::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 3;
}

/* Keep the full DOM for selection, tutorial anchors, and keyboard navigation,
 * while letting Chromium skip layout/paint work for far-off cards in large
 * registries. The placeholder height tracks a typical card so the scroll
 * track barely moves before an item is first measured; `auto` keeps the real
 * size afterwards.
 *
 * The paint containment this implies clips the hover shadow/lift and the
 * select badge. The clip margin is scoped to the states that paint outside
 * the card: on every item it costs ~15% scroll fps under CPU throttling.
 * Dropping it waits out the card's 0.24s shadow fade. */
.grid-section--large .grid-section__item {
  content-visibility: auto;
  contain-intrinsic-size: auto 0px auto 200px;
  transition: overflow-clip-margin 0s 0.24s allow-discrete;
}

/* Select badge sits 6px outside, plus its own shadow. */
.grid-section--large .grid-section__item--selection-mode {
  overflow-clip-margin: 20px;
  transition-delay: 0s;
}

/* Hover and selected shadows reach 48px below the card. */
.grid-section--large .grid-section__item:is(:hover, :focus-within, .grid-section__item--selected) {
  overflow-clip-margin: 48px;
  transition-delay: 0s;
}

.grid-section__select {
  position: absolute;
  top: -6px;
  right: -6px;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  /* Native <button> reset so the new a11y semantics (Phase 4 task 2.4.1)
     don't repaint the chip: el-style/font/spacing must stay identical to
     the previous div implementation, otherwise multi-select mode would
     visually flicker on rollout. */
  border: 0;
  padding: 0;
  margin: 0;
  background: transparent;
  font: inherit;
  color: inherit;
  -webkit-appearance: none;
  appearance: none;
}

.grid-section__select:focus-visible {
  /* Focus ring only on keyboard navigation. Mouse / touch users see no
     focus outline (browser default for :focus-visible). */
  outline: 2px solid var(--el-color-primary);
  outline-offset: 4px;
  border-radius: 11px;
}

.grid-section__check {
  width: 26px;
  height: 26px;
  border-radius: 9px;
  border: 2px solid color-mix(in srgb, var(--el-border-color) 80%, transparent);
  background: color-mix(in srgb, var(--el-bg-color) 92%, white);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-text-color-primary) 8%, transparent);
  transition:
    border-color 0.2s ease,
    background-color 0.2s ease,
    transform 0.2s cubic-bezier(0.34, 1.56, 0.64, 1),
    box-shadow 0.2s ease;
}

.grid-section__check:hover {
  border-color: var(--el-color-primary);
  transform: scale(1.08);
}

.grid-section__check--checked {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary);
  box-shadow: 0 4px 14px color-mix(in srgb, var(--el-color-primary) 36%, transparent);
}

.grid-section__check--checked:hover {
  transform: scale(1.08);
}

.grid-section__check-icon {
  width: 14px;
  height: 14px;
  color: #fff;
  animation: check-draw var(--motion-duration-normal) var(--motion-ease-spring) forwards;
}

@keyframes check-draw {
  from {
    opacity: 0;
    transform: scale(0.5);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.check-pop-enter-active {
  transition:
    opacity var(--motion-duration-normal) var(--motion-ease-standard),
    transform var(--motion-duration-emphasis) var(--motion-ease-spring);
}

.check-pop-leave-active {
  transition:
    opacity var(--motion-duration-fast) var(--motion-ease-exit),
    transform var(--motion-duration-fast) var(--motion-ease-exit);
}

.check-pop-enter-from {
  opacity: 0;
  transform: scale(0.4);
}

.check-pop-leave-to {
  opacity: 0;
  transform: scale(0.6);
}

.grid-section__item--selected :deep(.plugin-card),
.grid-section__item--selected :deep(.grid-section-card) {
  border-color: var(--el-color-primary);
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--el-color-primary) 4%, var(--el-bg-color)),
    color-mix(in srgb, var(--el-color-primary) 2%, var(--el-bg-color))
  );
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--el-color-primary) 16%, transparent),
    0 16px 32px color-mix(in srgb, var(--el-color-primary) 12%, transparent),
    0 6px 14px color-mix(in srgb, var(--el-text-color-primary) 6%, transparent);
}

.grid-section__item--selected :deep(.plugin-list-row-card) {
  border-color: var(--el-color-primary);
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--el-color-primary) 4%, var(--el-bg-color)),
    color-mix(in srgb, var(--el-color-primary) 2%, var(--el-bg-color))
  );
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--el-color-primary) 16%, transparent),
    0 14px 28px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
}

.grid-section__item--selection-mode {
  cursor: pointer;
}

.grid-section__item--selection-mode:hover :deep(.plugin-card),
.grid-section__item--selection-mode:hover :deep(.plugin-list-row-card) {
  border-color: color-mix(in srgb, var(--el-color-primary) 40%, var(--el-border-color));
}

.grid-section__item :deep(.plugin-card) {
  height: 100%;
  display: flex;
  flex-direction: column;
  transition:
    transform 0.24s ease,
    box-shadow 0.24s ease,
    border-color 0.24s ease;
}

.grid-section__item:hover :deep(.plugin-card) {
  transform: translateY(-3px);
}

.grid-section__item :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.grid-item-move {
  transition: transform var(--motion-duration-normal) var(--motion-ease-standard);
}

.count-fade-enter-active,
.count-fade-leave-active {
  transition:
    opacity var(--motion-duration-fast) var(--motion-ease-standard),
    transform var(--motion-duration-fast) var(--motion-ease-standard);
}

.count-fade-enter-from,
.count-fade-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

@media (prefers-reduced-motion: reduce) {
  .grid-item-move,
  .count-fade-enter-active,
  .count-fade-leave-active {
    transition-duration: 0.01ms;
    transition-delay: 0ms;
  }

  .grid-section__grid {
    transition: none;
  }
}
</style>
