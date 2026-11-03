<template>
  <div class="authoring-collection">
    <div class="authoring-filters">
      <el-input
        v-model="query"
        :prefix-icon="Search"
        :placeholder="t('authoring.search')"
        clearable
        :aria-label="t('authoring.search')"
      />
      <span class="authoring-count">{{ filtered.length }} / {{ projects.length }}</span>
    </div>
    <EmptyState
      v-if="!filtered.length"
      :description="projects.length ? t('common.noData') : t('authoring.noProjects')"
      :icon="FolderOpened"
    >
      <el-button v-if="!projects.length" type="primary" @click="emit('create')">{{
        t('authoring.newProject')
      }}</el-button>
    </EmptyState>
    <div v-else class="authoring-project-grid">
      <article v-for="project in filtered" :key="project.project_id" class="authoring-project-card">
        <div class="authoring-project-card-heading">
          <span class="authoring-project-icon"
            ><el-icon :size="23"><Box /></el-icon
          ></span>
          <el-tag size="small" :type="status(project).type" effect="plain">{{
            t(`authoring.${status(project).label}`)
          }}</el-tag>
        </div>
        <h3>
          <button class="authoring-title-button" @click="emit('open', project.project_id)">
            {{ projectTitle(project) }}
          </button>
        </h3>
        <code class="authoring-muted">{{ project.plugin_id || '—' }}</code>
        <p class="authoring-project-description">{{ project.description || '—' }}</p>
        <div class="authoring-card-status">
          <span v-if="project.version" class="authoring-card-version">v{{ project.version }}</span>
        </div>
        <footer>
          <span class="authoring-small authoring-muted">{{ project.source_dir }}</span
          ><el-button size="small" type="primary" plain @click="emit('open', project.project_id)"
            >{{ t('authoring.projectDetails') }}<el-icon><ArrowRight /></el-icon
          ></el-button>
        </footer>
      </article>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight, Box, FolderOpened, Search } from '@element-plus/icons-vue'
import EmptyState from '@/components/common/EmptyState.vue'
import type { AuthoringProject } from '@/api/pluginProjects'
import { projectTitle } from '@/composables/useAuthoringProjects'

const props = defineProps<{ projects: AuthoringProject[] }>()
const emit = defineEmits<{ open: [projectId: string]; create: [] }>()
const { t } = useI18n()
const query = ref('')
function resetFilters() {
  query.value = ''
}
defineExpose({ resetFilters })
function status(project: AuthoringProject) {
  if (project.status === 'missing') return { type: 'danger', label: 'statusMissing' } as const
  if (project.status === 'invalid') return { type: 'warning', label: 'statusInvalid' } as const
  if (project.development === 'mounted') return { type: 'success', label: 'mounted' } as const
  if (project.development === 'mode_disabled')
    return { type: 'warning', label: 'mountedModeDisabled' } as const
  return { type: 'info', label: 'developing' } as const
}
const filtered = computed(() => {
  const text = query.value.trim().toLocaleLowerCase()
  if (!text) return props.projects
  return props.projects.filter((project) =>
    [project.name, project.plugin_id, project.description, project.source_dir]
      .join(' ')
      .toLocaleLowerCase()
      .includes(text)
  )
})
</script>
