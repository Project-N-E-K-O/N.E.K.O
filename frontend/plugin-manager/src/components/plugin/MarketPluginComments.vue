<template>
  <LoadingSpinner v-if="!thread && loading" :loading="true" />
  <div v-else-if="!thread">
    <el-alert :title="t('market.detailLoadFailed')" type="error" :closable="false" show-icon />
    <el-button @click="load()">{{ t('common.refresh') }}</el-button>
  </div>
  <template v-else>
    <el-alert
      v-if="failed"
      :title="t('market.detailLoadFailed')"
      type="error"
      :closable="false"
      show-icon
    />
    <!--
      Market contract: for a signed-in actor, plugin comments' can_reply is false only
      when the Market requires email verification and the account is unverified.
    -->
    <MarketConversationThread
      :key="session || 'guest'"
      :messages="thread.messages"
      :empty-text="t('market.detailCommentsEmpty')"
      :session="session"
      :draft-key="`comments:${pluginId}`"
      :can-write="!!session && !!thread.can_reply"
      :requires-email-verification="!!session && thread.can_reply === false"
      :has-more="thread.next_cursor !== null"
      :loading-more="loading"
      :send="send"
      @more="load(true)"
      @session-updated="load()"
    />
  </template>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { MarketPluginComments } from '@/api/market'
import {
  MarketConversationError,
  marketConversationSessionChanged,
  readMarketComments,
  sendMarketComment,
  type MarketMessageDraft,
} from '@/api/marketConversations'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import MarketConversationThread from './MarketConversationThread.vue'

const props = defineProps<{ pluginId: number | string }>()
const emit = defineEmits<{ 'feedback-changed': [] }>()
const { t } = useI18n()
const thread = ref<MarketPluginComments | null>(null)
const session = ref<string | null>(null)
const failed = ref(false)
const loading = ref(true)
let request: AbortController | undefined

async function load(more = false) {
  if (more && (loading.value || thread.value?.next_cursor == null)) return
  request?.abort()
  const current = new AbortController()
  request = current
  failed.value = false
  loading.value = true
  if (!more) {
    session.value = null
    thread.value = null
  }
  try {
    const result = await readMarketComments(
      props.pluginId,
      current.signal,
      more ? (thread.value?.next_cursor ?? undefined) : undefined,
      more ? session.value : undefined
    )
    if (current.signal.aborted) return
    if (more && session.value !== result.session) {
      void load()
      return
    }
    const messages = new Map((thread.value?.messages ?? []).map((message) => [message.id, message]))
    for (const message of result.data.messages) messages.set(message.id, message)
    session.value = result.session
    thread.value = {
      ...result.data,
      messages: [...messages.values()].sort((left, right) => left.id - right.id),
    }
  } catch (error) {
    if (current.signal.aborted) return
    if (more && marketConversationSessionChanged(error)) {
      void load()
    } else {
      failed.value = true
    }
  } finally {
    if (request === current) loading.value = false
  }
}

async function send(draft: MarketMessageDraft) {
  const activeSession = session.value
  // Throw, not return: the form treats a resolved send as posted and clears the draft.
  if (!activeSession || !thread.value?.can_reply) {
    // A 4xx-class status: nothing reached the Market, so the draft is safe to resend.
    throw new MarketConversationError(409, 'market_feedback_unavailable')
  }
  const message = await sendMarketComment(props.pluginId, activeSession, draft)
  if (session.value === activeSession && thread.value && !request?.signal.aborted) {
    const messages = thread.value.messages
    if (!messages.some((existing) => existing.id === message.id)) {
      thread.value = { ...thread.value, messages: [...messages, message] }
    }
  }
  emit('feedback-changed')
}

onMounted(() => void load())
onBeforeUnmount(() => request?.abort())
</script>
