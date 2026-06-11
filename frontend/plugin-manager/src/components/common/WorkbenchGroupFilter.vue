<template>
  <div class="wb-group-filter">
    <!-- 多选：checkbox-button-group -->
    <el-checkbox-group
      v-if="selectionMode === 'multiple'"
      :model-value="selectedIds"
      class="wb-group-filter__group"
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
      class="wb-group-filter__group"
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
import type { GroupSelectionMode } from '@/composables/useGridWorkbench'
import type { GroupChoiceDescriptor } from '@/composables/workbenchDescriptors'

withDefaults(defineProps<{
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
</script>

<style scoped>
.wb-group-filter {
  flex: 1 1 auto;
  min-width: 0;
}

.wb-group-filter__group {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.wb-group-filter__group :deep(.el-checkbox-button) {
  --el-checkbox-button-checked-bg-color: var(--el-color-primary);
}

.wb-group-filter__group :deep(.el-checkbox-button__inner),
.wb-group-filter__group :deep(.el-radio-button__inner) {
  display: flex;
  align-items: center;
  gap: 5px;
  border-radius: 10px;
  padding: 6px 14px;
  font-size: 13px;
  font-weight: 500;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 50%, transparent);
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease,
    box-shadow 0.2s ease,
    transform 0.18s ease;
}

.wb-group-filter__group :deep(.el-checkbox-button__inner:hover),
.wb-group-filter__group :deep(.el-radio-button__inner:hover) {
  transform: translateY(-1px);
}

.wb-group-filter__group :deep(.el-checkbox-button.is-checked .el-checkbox-button__inner),
.wb-group-filter__group :deep(.el-radio-button.is-active .el-radio-button__inner) {
  box-shadow: 0 4px 12px color-mix(in srgb, var(--el-color-primary) 24%, transparent);
  border-color: transparent;
}

.wb-group-filter__count {
  opacity: 0.8;
  font-weight: 400;
}
</style>
