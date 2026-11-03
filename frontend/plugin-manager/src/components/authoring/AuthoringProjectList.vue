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
      <article
        v-for="project in filtered"
        :key="project.project_id"
        class="authoring-project-card"
        @click="emit('open', project.project_id)"
      >
        <header class="authoring-card__header">
          <h3 class="authoring-card__name">
            <button
              type="button"
              class="authoring-card__name-button"
              @click.stop="emit('open', project.project_id)"
            >
              {{ projectTitle(project) }}
            </button>
          </h3>
          <span v-if="project.plugin_id" class="authoring-card__id"
            >ID: {{ project.plugin_id }}</span
          >
          <AuthoringProjectStatus :project="project" />
        </header>
        <p class="authoring-card__description">
          {{ project.description || t('common.noData') }}
        </p>
        <footer class="authoring-card__footer">
          <el-tag v-if="project.version" size="small" type="info">v{{ project.version }}</el-tag>
          <span class="authoring-card__path" :title="project.source_dir">{{
            project.source_dir
          }}</span>
        </footer>
      </article>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { FolderOpened, Search } from '@element-plus/icons-vue'
import EmptyState from '@/components/common/EmptyState.vue'
import AuthoringProjectStatus from '@/components/authoring/AuthoringProjectStatus.vue'
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
