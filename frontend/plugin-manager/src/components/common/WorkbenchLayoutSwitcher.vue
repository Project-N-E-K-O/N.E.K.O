<template>
  <div class="wb-layout-switcher" :data-yui-guide-id="guideId">
    <el-radio-group
      :model-value="layoutMode"
      size="small"
      class="wb-segmented"
      :aria-label="$t('plugins.layoutLabel')"
      @update:model-value="(v: LayoutMode) => $emit('update:layoutMode', v)"
    >
      <el-radio-button
        v-for="choice in choices"
        :key="choice.value"
        :value="choice.value"
      >
        {{ choice.label }}
      </el-radio-button>
    </el-radio-group>
  </div>
</template>

<script setup lang="ts">
import type { LayoutMode } from '@/composables/useGridWorkbench'
import type { LayoutChoiceDescriptor } from '@/composables/workbenchDescriptors'

defineProps<{
  layoutMode: LayoutMode
  choices: LayoutChoiceDescriptor[]
  guideId?: string
}>()

defineEmits<{
  'update:layoutMode': [value: LayoutMode]
}>()
</script>

<style scoped>
.wb-layout-switcher {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}
</style>
