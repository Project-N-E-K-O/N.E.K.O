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
      <article v-for="project in filtered" :key="project.key" class="authoring-project-card">
        <div class="authoring-project-card-heading">
          <span class="authoring-project-icon"
            ><el-icon :size="23"><Box /></el-icon
          ></span>
          <el-tag size="small" type="info" effect="plain">{{ t('authoring.developing') }}</el-tag>
        </div>
        <h3>
          <button class="authoring-title-button" @click="emit('open', project.key)">
            {{ project.name || project.id }}
          </button>
        </h3>
        <code class="authoring-muted">{{ project.id }}</code>
        <p class="authoring-project-description">{{ project.description || '—' }}</p>
        <div class="authoring-card-status">
          <span class="authoring-card-version">v{{ project.version }}</span>
        </div>
        <footer>
          <span class="authoring-small authoring-muted">{{ project.source }}</span
          ><el-button size="small" type="primary" plain @click="emit('open', project.key)"
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
import type { CreatedAuthoringProject } from '@/types/authoring'

const props = defineProps<{ projects: CreatedAuthoringProject[] }>()
const emit = defineEmits<{ open: [id: string]; create: [] }>()
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
    `${project.name} ${project.id} ${project.description}`.toLocaleLowerCase().includes(text)
  )
})
</script>
