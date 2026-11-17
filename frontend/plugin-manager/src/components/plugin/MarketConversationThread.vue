<template>
  <ul v-if="messages.length" class="market-messages">
    <li v-for="message in messages" :key="message.id" class="market-message">
      <el-avatar :size="30" :src="avatarFor(message)">
        {{ authorName(message).slice(0, 1) }}
      </el-avatar>
      <div class="market-message__content">
        <div class="market-message__meta">
          <strong>{{ authorName(message) }}</strong>
          <time :datetime="message.created_at">{{
            new Date(message.created_at).toLocaleString(locale)
          }}</time>
        </div>
        <div v-if="message.reply_to" class="market-message__context">
          {{ t('market.detailCommentReplyTo', { name: authorName(message.reply_to) }) }}
        </div>
        <p>{{ message.is_hidden ? t('market.detailCommentHidden') : message.body }}</p>
        <el-button
          v-if="canWrite && !message.is_hidden"
          text
          size="small"
          :disabled="sending"
          @click="replyTo = message"
        >
          {{ t('marketFeedback.reply') }}
        </el-button>
      </div>
    </li>
  </ul>
  <el-empty v-else :description="emptyText" :image-size="72" />
  <el-button v-if="hasMore" :loading="loadingMore" @click="emit('more')">
    {{ t('marketFeedback.loadMore') }}
  </el-button>
  <form v-if="canWrite" class="market-message-form" @submit.prevent="submit">
    <div v-if="replyTo" class="market-message__meta">
      <span>{{ t('market.detailCommentReplyTo', { name: authorName(replyTo) }) }}</span>
      <el-button text size="small" :disabled="sending" @click="replyTo = null">{{
        t('common.cancel')
      }}</el-button>
    </div>
    <label>
      {{ t('marketFeedback.message') }}
      <el-input v-model="body" type="textarea" :rows="3" :maxlength="10000" :disabled="sending" />
    </label>
    <el-alert v-if="sendError" :title="t(sendError)" type="error" :closable="false" show-icon />
    <el-button native-type="submit" type="primary" :disabled="!body.trim()" :loading="sending">
      {{ t('marketFeedback.send') }}
    </el-button>
  </form>
  <el-button v-else-if="!session" :loading="marketAuthBusy" @click="startMarketLogin()">
    {{ t('market.login') }}
  </el-button>
  <p v-else-if="requiresEmailVerification">{{ t('marketFeedback.verifyEmail') }}</p>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { MarketPluginComment } from '@/api/market'
import { fetchCommunityAuthorAvatars } from '@/api/marketCommunityProfiles'
import {
  MarketConversationError,
  marketConversationSessionChanged,
  type MarketMessageDraft,
} from '@/api/marketConversations'
import { MARKET_AUTH_CHANGED_EVENT } from '@/api/marketBridge'
import { useMarketAuth } from '@/composables/useMarketAuth'

const props = defineProps<{
  messages: MarketPluginComment[]
  emptyText: string
  session: string | null
  canWrite: boolean
  requiresEmailVerification?: boolean
  hasMore: boolean
  loadingMore: boolean
  send: (draft: MarketMessageDraft) => Promise<void>
}>()
const emit = defineEmits<{ more: []; 'session-updated': [] }>()
const { t, locale } = useI18n()
const { marketAuthBusy, startMarketLogin } = useMarketAuth()
const body = ref('')
const replyTo = ref<MarketPluginComment | null>(null)
const sending = ref(false)
const sendError = ref('')
const avatars = ref<Record<string, string>>({})
let lastDraft: MarketMessageDraft | undefined

function onAccountChange() {
  emit('session-updated')
}
onMounted(() => window.addEventListener(MARKET_AUTH_CHANGED_EVENT, onAccountChange))
onBeforeUnmount(() => window.removeEventListener(MARKET_AUTH_CHANGED_EVENT, onAccountChange))

const queried = new Set<string>()
watch(
  () => props.messages,
  async (messages, _, onCleanup) => {
    const actors = new Map(
      messages.flatMap((message) => {
        const id = message.author.community_profile_id
        return !message.is_hidden && id && !queried.has(id) ? [[id, message.author] as const] : []
      })
    )
    if (!actors.size) return
    const request = new AbortController()
    onCleanup(() => request.abort())
    const entries = [...actors.values()]
    for (let offset = 0; offset < entries.length && !request.signal.aborted; offset += 100) {
      const batch = entries.slice(offset, offset + 100)
      const values = await fetchCommunityAuthorAvatars(batch, request.signal)
      if (request.signal.aborted) return
      for (const actor of batch) {
        if (actor.community_profile_id) queried.add(actor.community_profile_id)
      }
      Object.assign(avatars.value, values)
    }
  },
  { immediate: true }
)

function authorName(entry: Pick<MarketPluginComment, 'author'>): string {
  return entry.author.display_name || entry.author.username || t('market.unknownAuthor')
}

function avatarFor(message: MarketPluginComment): string {
  if (message.is_hidden) return ''
  const id = message.author.community_profile_id
  return (id && avatars.value[id]) || message.author.avatar_url || ''
}

async function submit() {
  const text = body.value.trim()
  const replyId = replyTo.value?.id ?? null
  if (!text || sending.value || !props.canWrite) return
  if (!lastDraft || lastDraft.body !== text || lastDraft.reply_to_id !== replyId) {
    lastDraft = { body: text, reply_to_id: replyId, client_message_id: crypto.randomUUID() }
  }
  sending.value = true
  sendError.value = ''
  try {
    await props.send(lastDraft)
    body.value = ''
    replyTo.value = null
    lastDraft = undefined
  } catch (error) {
    sendError.value =
      error instanceof MarketConversationError && error.code === 'market_email_unverified'
        ? 'marketFeedback.verifyEmail'
        : 'marketFeedback.sendFailed'
    if (marketConversationSessionChanged(error)) {
      emit('session-updated')
    }
  } finally {
    sending.value = false
  }
}
</script>

<style scoped>
.market-messages {
  display: grid;
  gap: 12px;
  list-style: none;
  padding: 0;
}
.market-message {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
}
.market-message__content {
  min-width: 0;
}
.market-message__content p {
  margin: 6px 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.market-message__meta {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.market-message__meta time,
.market-message__context {
  font-size: 0.85rem;
  color: var(--el-text-color-secondary);
}
.market-message-form {
  display: grid;
  gap: 8px;
  justify-items: start;
  margin-top: 16px;
}
.market-message-form label {
  display: grid;
  gap: 4px;
  width: 100%;
}
</style>
