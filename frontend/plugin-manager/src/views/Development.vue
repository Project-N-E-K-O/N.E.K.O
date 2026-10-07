<template>
  <el-card class="development-page">
    <DevelopmentPluginsPanel>
      <template #help><DevelopmentGuide /></template>
    </DevelopmentPluginsPanel>
  </el-card>
</template>

<script setup lang="ts">
import DevelopmentPluginsPanel from '@/components/plugin/DevelopmentPluginsPanel.vue'
import DevelopmentGuide from '@/components/plugin/DevelopmentGuide.vue'
</script>
