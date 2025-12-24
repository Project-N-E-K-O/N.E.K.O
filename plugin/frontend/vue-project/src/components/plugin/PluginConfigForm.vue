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

    <el-skeleton v-if="loading" :rows="8" animated />

    <el-empty v-else-if="!configObj" :description="t('common.noData')" />

    <div v-else>
      <ConfigValueEditor v-model="configObj" path="" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { getPluginConfig } from '@/api/config'
import ConfigValueEditor from '@/components/plugin/ConfigValueEditor.vue'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()

const { t } = useI18n()

const loading = ref(false)
const configObj = ref<Record<string, any> | null>(null)

async function load() {
  if (!props.pluginId) return
  loading.value = true
  try {
    const res = await getPluginConfig(props.pluginId)
    configObj.value = (res.config || {}) as Record<string, any>
  } finally {
    loading.value = false
  }
}

function getValue() {
  return configObj.value || {}
}

defineExpose({
  load,
  getValue
})

onMounted(load)

watch(
  () => props.pluginId,
  () => {
    load()
  }
)
</script>

<style scoped>
.pcf {
  padding: 0;
}
</style>
