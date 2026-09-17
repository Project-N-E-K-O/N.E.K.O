<template>
  <div class="pcf">
    <el-empty v-if="!modelValue" :description="t('common.noData')" />

    <div v-else>
      <ConfigValueEditor
        :model-value="modelValue"
        @update:model-value="(v) => emit('update:modelValue', v)"
        :baseline-value="baselineValue"
        :search="search"
        :filter="filter"
        :changes="changes"
        :compact="true"
        :segments="[]"
        @undo="emit('undo', $event)"
        path=""
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import ConfigValueEditor from '@/components/plugin/ConfigValueEditor.vue'
import type { ConfigChange, ConfigFilter } from '@/utils/configEditor'

interface Props {
  modelValue: Record<string, any> | null
  baselineValue: Record<string, any> | null
  search?: string
  filter?: ConfigFilter
  changes?: ConfigChange[]
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: Record<string, any> | null): void
  (e: 'undo', path: string[]): void
}>()

const { t } = useI18n()
</script>

<style scoped>
.pcf {
  padding: 0;
}
</style>
