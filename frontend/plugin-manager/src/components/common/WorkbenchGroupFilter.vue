<template>
  <div class="wb-group-filter">
    <!-- 多选：checkbox-button-group -->
    <el-checkbox-group
      v-if="selectionMode === 'multiple'"
      :model-value="selectedIds"
      size="small"
      class="wb-segmented"
      :class="{ 'wb-segmented--all-selected': allSelected }"
      :data-yui-guide-id="guideId"
      @update:model-value="(v: string[]) => $emit('update:selectedIds', v)"
    >
      <el-checkbox-button
        v-for="choice in choices"
        :key="choice.id"
        :value="choice.id"
      >
        <el-icon v-if="choice.icon"><component :is="choice.icon" /></el-icon>
        {{ choice.label }}
        <span v-if="showCounts" class="wb-group-filter__count">
          ({{ counts.get(choice.id) ?? 0 }})
        </span>
      </el-checkbox-button>
    </el-checkbox-group>

    <!-- 单选：radio-button-group -->
    <el-radio-group
      v-else
      :model-value="selectedIds[0] ?? ''"
      size="small"
      class="wb-segmented"
      :data-yui-guide-id="guideId"
      @update:model-value="(v: string) => $emit('update:selectedIds', [v])"
    >
      <el-radio-button
        v-for="choice in choices"
        :key="choice.id"
        :value="choice.id"
      >
        <el-icon v-if="choice.icon"><component :is="choice.icon" /></el-icon>
        {{ choice.label }}
        <span v-if="showCounts" class="wb-group-filter__count">
          ({{ counts.get(choice.id) ?? 0 }})
        </span>
      </el-radio-button>
    </el-radio-group>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { GroupSelectionMode } from '@/composables/useGridWorkbench'
import type { GroupChoiceDescriptor } from '@/composables/workbenchDescriptors'

const props = withDefaults(defineProps<{
  choices: GroupChoiceDescriptor[]
  selectedIds: string[]
  counts?: Map<string, number>
  selectionMode?: GroupSelectionMode
  showCounts?: boolean
  guideId?: string
}>(), {
  counts: () => new Map(),
  selectionMode: 'multiple',
  showCounts: true,
  guideId: undefined,
})

defineEmits<{
  'update:selectedIds': [value: string[]]
}>()

// Every group checked means nothing is filtered out.
const allSelected = computed(() =>
  props.choices.length > 1
  && props.choices.every((choice) => props.selectedIds.includes(choice.id)),
)
</script>

<style scoped>
.wb-group-filter {
  flex: 1 1 auto;
  min-width: 0;
}

.wb-group-filter__count {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}
</style>
