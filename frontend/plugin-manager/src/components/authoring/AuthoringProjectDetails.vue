<template>
  <section class="authoring-panel">
    <h3 class="authoring-panel-title">
      <el-icon><Document /></el-icon>{{ t('authoring.projectDetails') }}
    </h3>
    <dl class="authoring-details">
      <template v-for="field in fields" :key="field.label">
        <dt>{{ t(`authoring.${field.label}`) }}</dt>
        <dd :class="{ 'authoring-code': field.code }">{{ field.value }}</dd>
      </template>
    </dl>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Document } from '@element-plus/icons-vue'
import type { AuthoringProjectMetadata } from '@/types/authoring'

const props = defineProps<{ project: AuthoringProjectMetadata }>()
const { t } = useI18n()
const fields = computed(() => [
  { label: 'type', value: props.project.type, code: false },
  { label: 'author', value: props.project.author || '—', code: false },
  { label: 'source', value: props.project.source, code: true },
  { label: 'entry', value: props.project.entry, code: true },
  { label: 'version', value: props.project.version, code: true },
])
</script>
