<template>
  <section class="authoring-panel">
    <el-alert
      v-if="project.status !== 'ok'"
      :title="
        t(
          project.status === 'missing'
            ? 'authoring.statusMissingHint'
            : 'authoring.statusInvalidHint'
        )
      "
      type="warning"
      :closable="false"
      show-icon
      class="authoring-create-notice"
    >
      <details v-if="project.error" class="authoring-diagnostic">
        <summary>{{ t('authoring.technicalDetails') }}</summary>
        <code>{{ project.error }}</code>
      </details>
    </el-alert>
    <h3 class="authoring-panel-title">
      <el-icon><Document /></el-icon>{{ t('authoring.projectDetails') }}
    </h3>
    <dl class="authoring-details">
      <template v-for="field in fields" :key="field.label">
        <dt>{{ t(`authoring.${field.label}`) }}</dt>
        <dd :class="{ 'authoring-code': field.code }">{{ field.value || '—' }}</dd>
      </template>
    </dl>
    <div class="authoring-project-actions">
      <el-button
        :icon="FolderOpened"
        :disabled="busy || project.status === 'missing'"
        @click="emit('openFolder')"
        >{{ t('authoring.openFolder') }}</el-button
      >
      <el-button
        v-if="project.status === 'ok' && project.development === 'unmounted'"
        type="primary"
        :disabled="busy"
        @click="emit('mount')"
        >{{ t('authoring.mountDevelopment') }}</el-button
      >
      <el-button
        v-if="project.development === 'mounted' || project.development === 'mode_disabled'"
        :disabled="busy"
        @click="emit('goDevelopment')"
        >{{ t('authoring.goDevelopment') }}</el-button
      >
      <el-button type="danger" plain :icon="Delete" :disabled="busy" @click="emit('remove')">{{
        t('authoring.removeProject')
      }}</el-button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Delete, Document, FolderOpened } from '@element-plus/icons-vue'
import type { AuthoringProject } from '@/api/pluginProjects'

const props = defineProps<{ project: AuthoringProject; busy: boolean }>()
const emit = defineEmits<{ openFolder: []; mount: []; goDevelopment: []; remove: [] }>()
const { t } = useI18n()
const DEVELOPMENT_LABELS = {
  unmounted: 'notMounted',
  mounted: 'mounted',
  mode_disabled: 'mountedModeDisabled',
} as const
const developmentStatus = computed(() => {
  const state = props.project.development
  return t(`authoring.${state === null ? 'mountedUnknown' : DEVELOPMENT_LABELS[state]}`)
})
const fields = computed(() => [
  { label: 'id', value: props.project.plugin_id, code: true },
  { label: 'type', value: props.project.plugin_type, code: false },
  { label: 'author', value: props.project.author, code: false },
  { label: 'source', value: props.project.source_dir, code: true },
  { label: 'entry', value: props.project.entry, code: true },
  { label: 'version', value: props.project.version, code: true },
  { label: 'developmentStatus', value: developmentStatus.value, code: false },
])
</script>
