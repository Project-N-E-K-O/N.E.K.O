<template>
  <div class="authoring-details">
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
    >
      <details v-if="project.error" class="authoring-diagnostic">
        <summary>{{ t('authoring.technicalDetails') }}</summary>
        <code>{{ project.error }}</code>
      </details>
    </el-alert>
    <el-descriptions :column="2" border>
      <el-descriptions-item :label="t('authoring.id')">
        <code v-if="project.plugin_id">{{ project.plugin_id }}</code>
        <span v-else class="authoring-muted">{{ t('common.noData') }}</span>
      </el-descriptions-item>
      <el-descriptions-item :label="t('authoring.version')">{{
        project.version || t('common.noData')
      }}</el-descriptions-item>
      <el-descriptions-item :label="t('authoring.type')">
        <el-tag v-if="project.plugin_type" size="small" type="info">{{
          project.plugin_type
        }}</el-tag>
        <span v-else class="authoring-muted">{{ t('common.noData') }}</span>
      </el-descriptions-item>
      <el-descriptions-item :label="t('authoring.runtimeStatus')">
        <AuthoringProjectStatus :project="project" />
      </el-descriptions-item>
      <el-descriptions-item :label="t('authoring.author')" :span="2">{{
        project.author || t('common.noData')
      }}</el-descriptions-item>
      <el-descriptions-item :label="t('authoring.description')" :span="2">{{
        project.description || t('common.noData')
      }}</el-descriptions-item>
      <el-descriptions-item :label="t('authoring.source')" :span="2">
        <code>{{ project.source_dir }}</code>
      </el-descriptions-item>
      <el-descriptions-item :label="t('authoring.entry')" :span="2">
        <code v-if="project.entry">{{ project.entry }}</code>
        <span v-else class="authoring-muted">{{ t('common.noData') }}</span>
      </el-descriptions-item>
    </el-descriptions>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AuthoringProjectStatus from '@/components/authoring/AuthoringProjectStatus.vue'
import type { AuthoringProject } from '@/api/pluginProjects'

defineProps<{ project: AuthoringProject }>()
const { t } = useI18n()
</script>
