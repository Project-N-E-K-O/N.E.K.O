<template>
  <Transition
    appear
    @before-enter="beforeSectionEnter"
    @enter="enterSection"
    @after-enter="afterSectionEnter"
    @before-leave="beforeSectionLeave"
    @leave="leaveSection"
    @after-leave="afterSectionLeave"
  >
    <section
      v-if="items.length > 0"
      class="plugin-grid-section"
      :class="sectionClass"
    >
      <div class="section-header" :class="headerClass">
        <span class="section-title">
          <el-icon v-if="icon"><component :is="icon" /></el-icon>
          {{ title }}
          <span class="section-title__count">
            (
            <Transition name="count-fade" mode="out-in">
              <span :key="items.length">{{ items.length }}</span>
            </Transition>
            )
          </span>
        </span>
      </div>

      <TransitionGroup
        name="grid-item"
        tag="div"
        class="plugin-grid"
        :class="layoutClass"
        @before-leave="pinLeavingItem"
        @after-leave="clearLeavingItemStyles"
      >
        <div
          v-for="(item, index) in items"
          :key="item.id"
          class="plugin-item"
          :class="pluginItemClass(item.id)"
          :style="itemMotionStyle(index)"
        >
          <div v-if="multiSelectEnabled" class="plugin-item__select">
            <el-checkbox
              :model-value="isSelected(item.id)"
              @click.stop
              @change="$emit('toggle-selection', item.id)"
            />
          </div>

          <component
            :is="layoutMode === 'list' ? PluginListRow : PluginCard"
            :plugin="item"
            :is-selected="multiSelectEnabled && isSelected(item.id)"
            :show-metrics="showMetrics"
            @click="$emit('item-click', item.id)"
            @contextmenu="$emit('item-contextmenu', $event, item)"
          />
        </div>
      </TransitionGroup>
    </section>
  </Transition>
</template>

<script setup lang="ts">
import { computed, type Component } from 'vue'
import PluginCard from '@/components/plugin/PluginCard.vue'
import PluginListRow from '@/components/plugin/PluginListRow.vue'
import { useAnimatedGridTransition } from '@/composables/useAnimatedGridTransition'
import type { PluginWorkbenchItem, PluginWorkbenchLayoutMode } from '@/composables/usePluginWorkbench'

const props = withDefaults(defineProps<{
  title: string
  icon?: Component
  items: PluginWorkbenchItem[]
  layoutMode: PluginWorkbenchLayoutMode
  multiSelectEnabled: boolean
  selectedPluginIds: string[]
  showMetrics: boolean
  variant?: 'default' | 'adapter' | 'extension'
}>(), {
  icon: undefined,
  variant: 'default',
})

defineEmits<{
  'item-click': [pluginId: string]
  'item-contextmenu': [event: MouseEvent, plugin: PluginWorkbenchItem]
  'toggle-selection': [pluginId: string]
}>()

const {
  itemMotionStyle,
  pinLeavingItem,
  clearLeavingItemStyles,
  beforeSectionEnter,
  enterSection,
  afterSectionEnter,
  beforeSectionLeave,
  leaveSection,
  afterSectionLeave,
} = useAnimatedGridTransition()

const layoutClass = computed(() => `plugin-grid--${props.layoutMode}`)
const headerClass = computed(() => {
  if (props.variant === 'adapter') return 'section-header--adapter'
  if (props.variant === 'extension') return 'section-header--ext'
  return ''
})
const sectionClass = computed(() => {
  if (props.variant === 'adapter') return 'plugin-grid-section--adapter'
  if (props.variant === 'extension') return 'plugin-grid-section--ext'
  return ''
})

function isSelected(pluginId: string) {
  return props.selectedPluginIds.includes(pluginId)
}

function pluginItemClass(pluginId: string) {
  return {
    'plugin-item--selection-mode': props.multiSelectEnabled,
    'plugin-item--selected': props.multiSelectEnabled && isSelected(pluginId),
    'plugin-item--list-layout': props.layoutMode === 'list',
  }
}
</script>

<style scoped>
.plugin-grid-section {
  position: relative;
}

.plugin-grid-section--adapter,
.plugin-grid-section--ext {
  margin-top: 24px;
}

.section-header {
  margin-bottom: 12px;
}

.section-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.section-title__count {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  min-width: 2ch;
}

.plugin-grid {
  display: grid;
  gap: 16px;
  align-items: stretch;
  position: relative;
}

.plugin-grid--list,
.plugin-grid--single {
  grid-template-columns: 1fr;
}

.plugin-grid--double {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.plugin-grid--compact {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.plugin-item {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  will-change: transform, opacity;
}

.plugin-item__select {
  position: absolute;
  top: -8px;
  right: -8px;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  min-height: 32px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--el-bg-color) 86%, white);
  box-shadow: 0 8px 18px color-mix(in srgb, var(--el-text-color-primary) 10%, transparent);
  backdrop-filter: blur(10px);
}

.plugin-item--selected :deep(.plugin-card) {
  border-color: var(--el-color-primary);
  box-shadow:
    0 16px 32px color-mix(in srgb, var(--el-color-primary) 16%, transparent),
    0 6px 14px color-mix(in srgb, var(--el-text-color-primary) 8%, transparent);
}

.plugin-item--selected :deep(.plugin-list-row-card) {
  border-color: var(--el-color-primary);
}

.plugin-item :deep(.plugin-card) {
  height: 100%;
  display: flex;
  flex-direction: column;
  transition:
    transform 0.24s ease,
    box-shadow 0.24s ease,
    border-color 0.24s ease;
}

.plugin-item:hover :deep(.plugin-card) {
  transform: translateY(-3px);
}

.plugin-item :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.grid-item-enter-active,
.grid-item-leave-active {
  transition:
    transform 0.34s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.24s ease,
    filter 0.24s ease;
}

.grid-item-enter-active {
  transition-delay: var(--item-stagger-delay, 0ms);
}

.grid-item-enter-from {
  opacity: 0;
  transform: scale(0.95) translateY(12px);
  filter: blur(6px);
}

.grid-item-leave-to {
  opacity: 0;
  transform: scale(0.94) translateY(-12px);
  filter: blur(6px);
}

.grid-item-enter-to,
.grid-item-leave-from {
  opacity: 1;
  transform: scale(1) translateY(0);
  filter: blur(0);
}

.grid-item-leave-active {
  position: absolute;
  z-index: 0;
  pointer-events: none;
  margin: 0;
}

.grid-item-move {
  transition: transform 0.34s cubic-bezier(0.22, 1, 0.36, 1);
}

.count-fade-enter-active,
.count-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.count-fade-enter-from,
.count-fade-leave-to {
  opacity: 0;
  transform: translateY(6px);
}

@media (max-width: 1280px) {
  .plugin-grid--compact {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1180px) {
  .plugin-grid--compact {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .plugin-grid--double,
  .plugin-grid--compact {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .grid-item-enter-active,
  .grid-item-leave-active,
  .grid-item-move,
  .count-fade-enter-active,
  .count-fade-leave-active {
    transition-duration: 0.01ms;
    transition-delay: 0ms;
  }
}
</style>
