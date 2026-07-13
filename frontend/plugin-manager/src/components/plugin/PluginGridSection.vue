<template>
  <GridSection
    :title="title"
    :icon="icon"
    :items="items"
    :layout-mode="layoutMode"
    :multi-select-enabled="multiSelectEnabled"
    :selected-ids="selectedPluginIds"
    :variant="variant"
    guide-prefix="plugin-list"
    @toggle-selection="(id) => $emit('toggle-selection', id)"
  >
    <template #item="{ item, layoutMode: mode }">
      <component
        :is="mode === 'list' ? PluginListRow : PluginCard"
        :plugin="item"
        :is-selected="multiSelectEnabled && selectedPluginIds.includes(item.id)"
        :show-metrics="showMetrics"
        :show-source-detail="showSourceDetail"
        @click="$emit('item-click', item.id)"
        @contextmenu="$emit('item-contextmenu', $event, item)"
      />
    </template>
  </GridSection>
</template>

<script setup lang="ts">
import { type Component } from 'vue'
import GridSection from '@/components/common/GridSection.vue'
import PluginCard from '@/components/plugin/PluginCard.vue'
import PluginListRow from '@/components/plugin/PluginListRow.vue'
import type { PluginWorkbenchItem, PluginWorkbenchLayoutMode } from '@/composables/usePluginWorkbench'

defineProps<{
  title: string
  icon?: Component
  items: PluginWorkbenchItem[]
  layoutMode: PluginWorkbenchLayoutMode
  multiSelectEnabled: boolean
  selectedPluginIds: string[]
  showMetrics: boolean
  showSourceDetail?: boolean
  variant?: 'default' | 'adapter'
}>()

defineEmits<{
  'item-click': [pluginId: string]
  'item-contextmenu': [event: MouseEvent, plugin: PluginWorkbenchItem]
  'toggle-selection': [pluginId: string]
}>()
</script>
