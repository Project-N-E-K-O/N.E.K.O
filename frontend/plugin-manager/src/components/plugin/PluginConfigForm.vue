<template>
  <div class="pcf">
    <el-alert
      type="info"
      :title="t('plugins.formMode')"
      :description="t('plugins.formModeHint')"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    />

    <el-empty v-if="!modelValue" :description="t('common.noData')" />

    <div v-else>
      <ConfigValueEditor
        :model-value="modelValue"
        @update:model-value="(v) => emit('update:modelValue', v)"
        :baseline-value="baselineValue"
        path=""
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import ConfigValueEditor from '@/components/plugin/ConfigValueEditor.vue'

interface Props {
  modelValue: Record<string, any> | null
  baselineValue: Record<string, any> | null
}

const props = defineProps<Props>()
const emit = defineEmits<{ (e: 'update:modelValue', v: Record<string, any> | null): void }>()

const { t } = useI18n()
</script>

<style scoped>
.pcf {
  padding: 0;
}
</style>
