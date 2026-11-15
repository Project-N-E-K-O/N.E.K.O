<template>
  <div class="authoring-submission-metadata">
    <el-alert v-if="zonesError" :title="zonesError" type="warning" :closable="false" show-icon />
    <el-form-item :label="t('authoring.submissionZone')" :required="required">
      <el-select
        :model-value="zoneSlug"
        filterable
        :loading="zonesLoading"
        :disabled="disabled"
        :placeholder="t('authoring.submissionZonePlaceholder')"
        @update:model-value="emit('update:zoneSlug', $event)"
      >
        <el-option v-for="zone in zones" :key="zone.id" :label="zoneLabel(zone)" :value="zone.id" />
      </el-select>
    </el-form-item>
    <el-form-item :label="t('authoring.submissionTags')" :required="required">
      <div class="authoring-submission-tags">
        <el-check-tag
          v-for="tag in tagOptions"
          :key="tag"
          :checked="tags.includes(tag)"
          :disabled="disabled || (!tags.includes(tag) && tags.length >= SUBMISSION_TAGS_MAX)"
          @change="toggleTag(tag)"
        >
          {{ tagLabel(tag) }}
        </el-check-tag>
      </div>
      <p class="authoring-muted authoring-small">
        {{ t('authoring.submissionTagsCount', { count: tags.length, max: SUBMISSION_TAGS_MAX }) }}
      </p>
    </el-form-item>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  STANDARD_SUBMISSION_TAGS,
  SUBMISSION_TAGS_MAX,
  type MarketZone,
} from '@/api/marketSubmissions'

/** The Market zone and its standard tags (1 to 5), as the Market's own form offers them. */
const props = defineProps<{
  zoneSlug: string
  tags: string[]
  zones: MarketZone[]
  zonesLoading: boolean
  zonesError: string
  required: boolean
  disabled: boolean
}>()
const emit = defineEmits<{
  (event: 'update:zoneSlug', value: string): void
  (event: 'update:tags', value: string[]): void
}>()
const { t, te } = useI18n()

function zoneLabel(zone: MarketZone): string {
  const key = `market.zones.${zone.id}`
  return te(key) ? t(key) : zone.name
}

const standardTagIds = new Map<string, string>(STANDARD_SUBMISSION_TAGS.map((tag) => [tag.value, tag.id]))
const extraTags = ref<string[]>([])
watch(() => props.tags, (tags) => {
  extraTags.value = [...new Set([...extraTags.value, ...tags.filter((tag) => !standardTagIds.has(tag))])]
}, { immediate: true })
/** The standard tags, plus any other tag the submission already carries. */
const tagOptions = computed(() => [
  ...STANDARD_SUBMISSION_TAGS.map((tag) => tag.value),
  ...extraTags.value,
])

function tagLabel(tag: string): string {
  const id = standardTagIds.get(tag)
  return id ? t(`authoring.submissionTag${id[0]!.toUpperCase()}${id.slice(1)}`) : tag
}

function toggleTag(tag: string) {
  if (props.tags.includes(tag)) {
    emit('update:tags', props.tags.filter((current) => current !== tag))
  } else if (props.tags.length < SUBMISSION_TAGS_MAX) {
    emit('update:tags', [...props.tags, tag])
  }
}
</script>

<style scoped>
.authoring-submission-metadata .el-select {
  width: 100%;
}
.authoring-submission-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
