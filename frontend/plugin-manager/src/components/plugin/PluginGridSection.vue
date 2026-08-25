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
        :enable-ui-action="true"
        v-bind="mode === 'list' ? {} : { showIdentity: identityPluginIds.includes(item.id) }"
        @click="$emit('item-click', item.id)"
        @open-ui="$emit('item-open-ui', item, $event)"
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
import type { PluginListAction } from '@/types/api'

defineProps<{
  title: string
  icon?: Component
  items: PluginWorkbenchItem[]
  layoutMode: PluginWorkbenchLayoutMode
  multiSelectEnabled: boolean
  selectedPluginIds: string[]
  showMetrics: boolean
  showSourceDetail?: boolean
  identityPluginIds: string[]
  variant?: 'default' | 'adapter'
}>()

defineEmits<{
  'item-click': [pluginId: string]
  'item-open-ui': [plugin: PluginWorkbenchItem, action: PluginListAction]
  'item-contextmenu': [event: MouseEvent, plugin: PluginWorkbenchItem]
  'toggle-selection': [pluginId: string]
}>()
</script>
