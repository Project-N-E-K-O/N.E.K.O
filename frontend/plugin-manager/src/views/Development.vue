<template>
  <el-card class="development-page">
    <div class="development-authoring-entry">
      <el-button type="primary" plain @click="$router.push('/development/workbench')">{{ $t('authoring.title') }}</el-button>
    </div>
    <DevelopmentPluginsPanel>
      <template #help><DevelopmentGuide /></template>
    </DevelopmentPluginsPanel>
  </el-card>
</template>

<script setup lang="ts">
import DevelopmentPluginsPanel from '@/components/plugin/DevelopmentPluginsPanel.vue'
import DevelopmentGuide from '@/components/plugin/DevelopmentGuide.vue'
</script>

<style scoped>
.development-authoring-entry { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin-bottom: 16px; }
</style>
