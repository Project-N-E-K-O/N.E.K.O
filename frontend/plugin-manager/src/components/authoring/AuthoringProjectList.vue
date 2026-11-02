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
      <el-radio-group
        v-model="filter"
        class="wb-segmented"
        size="small"
        :aria-label="t('common.filter')"
      >
        <el-radio-button v-for="option in filters" :key="option" :value="option">{{
          t(`authoring.${option}`)
        }}</el-radio-button>
      </el-radio-group>
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
        :key="project.key"
        class="authoring-project-card"
        :data-project-kind="project.kind"
      >
        <div class="authoring-project-card-heading">
          <span class="authoring-project-icon"
            ><el-icon :size="23"
              ><component
                :is="
                  project.kind === 'demo' && project.sample === 'weather'
                    ? Sunny
                    : project.kind === 'demo' && project.sample === 'memo'
                      ? Bell
                      : Box
                " /></el-icon
          ></span>
          <el-tag
            size="small"
            :type="
              stage(project) === 'published'
                ? 'success'
                : stage(project) === 'ready'
                  ? 'primary'
                  : 'info'
            "
            effect="plain"
            >{{ t(`authoring.${stage(project)}`) }}</el-tag
          >
        </div>
        <h3>
          <button class="authoring-title-button" @click="emit('open', project.key)">
            {{ name(project) }}
          </button>
        </h3>
        <code class="authoring-muted">{{ project.id }}</code>
        <p class="authoring-project-description">
          {{
            project.description ||
            (project.kind === 'demo' && project.sample
              ? t(`authoring.${project.sample}Description`)
              : '—')
          }}
        </p>
        <div class="authoring-card-status">
          <span
            class="authoring-status-dot"
            :class="`is-${project.kind === 'demo' ? project.check : 'unchecked'}`"
          />{{ t(`authoring.${project.kind === 'demo' ? project.check : 'unchecked'}`)
          }}<span class="authoring-card-version">v{{ project.version }}</span>
        </div>
        <footer>
          <span class="authoring-small authoring-muted">{{ project.source }}</span
          ><el-button size="small" type="primary" plain @click="emit('open', project.key)"
            >{{ t(project.kind === 'demo' ? 'authoring.continue' : 'authoring.projectDetails')
            }}<el-icon><ArrowRight /></el-icon
          ></el-button>
        </footer>
      </article>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight, Bell, Box, FolderOpened, Search, Sunny } from '@element-plus/icons-vue'
import EmptyState from '@/components/common/EmptyState.vue'
import type { AuthoringProjectItem } from '@/types/authoring'

const props = defineProps<{ projects: AuthoringProjectItem[] }>()
const emit = defineEmits<{ open: [id: string]; create: [] }>()
const { t } = useI18n()
const query = ref('')
const filter = ref('all')
const filters = ['all', 'developing', 'ready', 'published'] as const
function resetFilters() {
  query.value = ''
  filter.value = 'all'
}
defineExpose({ resetFilters })
const name = (project: AuthoringProjectItem) =>
  project.name ||
  (project.kind === 'demo' && project.sample ? t(`authoring.${project.sample}`) : project.id)
function stage(project: AuthoringProjectItem) {
  if (project.kind === 'created') return 'developing'
  if (project.release.phase === 4) return 'published'
  if (project.check === 'passed' && project.artifact?.revision === project.revision) return 'ready'
  return 'developing'
}
const filtered = computed(() =>
  props.projects.filter((project) => {
    const text =
      `${name(project)} ${project.id} ${project.description} ${project.kind === 'demo' && project.sample ? t(`authoring.${project.sample}Description`) : ''}`.toLocaleLowerCase()
    if (!text.includes(query.value.trim().toLocaleLowerCase())) return false
    return filter.value === 'all' || stage(project) === filter.value
  })
)
</script>
