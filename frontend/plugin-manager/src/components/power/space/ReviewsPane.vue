<template>
  <section v-loading="loading" class="panel">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <p class="muted">{{ t('power.space.reviews.hint') }}</p>
    <el-empty v-if="!loading && !reviews.length" :description="t('power.space.reviews.empty')" />
    <table v-else class="rows">
      <thead>
        <tr>
          <th>{{ t('power.space.reviews.task') }}</th>
          <th v-if="!project">{{ t('power.space.reviews.project') }}</th>
          <th>{{ t('power.space.reviews.ended') }}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="review in reviews" :key="review.commission_id" :data-review="review.commission_id">
          <td class="title">{{ review.title || review.commission_id }}</td>
          <td v-if="!project" class="muted">{{ review.project }}</td>
          <td class="muted">{{ review.ended }}</td>
          <td class="act">
            <el-button size="small" @click="open(review)">{{ t('power.space.reviews.open') }}</el-button>
            <el-button size="small" type="danger" plain @click="askRemove(review)">{{ t('power.space.reviews.remove') }}</el-button>
          </td>
        </tr>
      </tbody>
    </table>

    <el-dialog v-model="reading" :title="target?.title || target?.commission_id" width="680px">
      <pre class="plain" data-testid="review-text">{{ text }}</pre>
    </el-dialog>

    <el-dialog v-model="removing" :title="t('power.space.reviews.removeTitle')" width="440px">
      <p>{{ target?.title || target?.commission_id }}</p>
      <p class="muted">{{ t('power.space.reviews.removeText') }}</p>
      <template #footer>
        <el-button @click="removing = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="busy" data-testid="review-remove-confirm" @click="remove">{{ t('power.space.reviews.remove') }}</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { deleteReview, listReviews, readReview } from '@/api/console'
import type { SpaceReview } from '@/api/console'
import { describeConsoleError } from '../powerFormat'

const props = defineProps<{ project?: string | null }>()
const { t, te } = useI18n()
const reviews = ref<SpaceReview[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const reading = ref(false)
const removing = ref(false)
const target = ref<SpaceReview | null>(null)
const text = ref('')
let turn = 0

/** Read the list; `keep` leaves the message of what just failed where it is. */
async function load(keep = false) {
  const mine = ++turn
  loading.value = true
  try {
    const answer = await listReviews(props.project)
    if (mine !== turn) return
    reviews.value = answer.reviews
    if (!keep) error.value = ''
  } catch (failure) {
    if (mine === turn) error.value = describeConsoleError(failure, t, te)
  } finally {
    if (mine === turn) loading.value = false
  }
}

async function open(review: SpaceReview) {
  try {
    text.value = (await readReview(review.commission_id)).review.content
    target.value = review
    error.value = ''
    reading.value = true
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
    void load(true)
  }
}

function askRemove(review: SpaceReview) {
  target.value = review
  removing.value = true
}

async function remove() {
  if (!target.value) return
  busy.value = true
  let failed = false
  try {
    await deleteReview(target.value.commission_id)
    error.value = ''
  } catch (failure) {
    failed = true
    error.value = describeConsoleError(failure, t, te)
  } finally {
    busy.value = false
    removing.value = false
    await load(failed)
  }
}

watch(() => props.project, () => void load(), { immediate: true })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.rows { width: 100%; border-collapse: collapse; margin-top: 12px; }
.rows th, .rows td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--el-border-color-lighter); vertical-align: top; }
.rows th { border-top: 0; font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); }
.title { font-weight: 600; overflow-wrap: anywhere; }
.act { text-align: right; white-space: nowrap; }
.act .el-button + .el-button { margin-left: 6px; }
.plain { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.6; max-height: 60vh; overflow: auto; }
.block { margin-bottom: 12px; }
</style>
