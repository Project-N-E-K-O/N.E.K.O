import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, BookHeart, Trash2, Plus } from 'lucide-react'
import CardInspectModal from './components/CardInspectModal'
import {
  composeForgedCardStory,
  createForgedBrawlCard,
  loadForgedBrawlCards,
  saveForgedBrawlCards,
} from './data/forgedBrawlCards'

const FORGE_RARITY_TABLE = [
  { name: '普通', weight: 40, tagStyle: 'border-gray-400/30 bg-gray-500/10 text-gray-200', frame: 'border-gray-400/30 shadow-gray-900/10' },
  { name: '稀有', weight: 28, tagStyle: 'border-sky-400/30 bg-sky-500/10 text-sky-200', frame: 'border-sky-400/40 shadow-sky-900/20' },
  { name: '奇想', weight: 17, tagStyle: 'border-violet-400/30 bg-violet-500/10 text-violet-200', frame: 'border-violet-400/40 shadow-violet-900/25' },
  { name: '璀璨', weight: 11, tagStyle: 'border-amber-400/30 bg-amber-500/10 text-amber-200', frame: 'border-amber-400/50 shadow-amber-900/25' },
  { name: '唯一', weight: 4, tagStyle: 'border-rose-400/30 bg-rose-500/10 text-rose-200', frame: 'border-rose-400/50 shadow-rose-900/30' },
]

const FORGE_EVENT_POOL = [
  { id: 'bond-1', name: '第一次共享耳机', summary: '那天我们把同一首歌听成了共同秘密。' },
  { id: 'bond-2', name: '深夜送来的热牛奶', summary: '困意被驱散后，心跳反而变得更明显。' },
  { id: 'bond-3', name: '一起躲雨的屋檐', summary: '肩膀不经意碰到的瞬间被记了很久。' },
  { id: 'bond-4', name: '通宵拼好的小摆件', summary: '灯光很暗，但谁都没有先说要休息。' },
  { id: 'bond-5', name: '说晚安前的那句停顿', summary: '欲言又止的时候，羁绊自己长出了回音。' },
  { id: 'bond-6', name: '自动贩卖机前的最后一罐', summary: '谁都说自己不渴，却一起站了很久。' },
  { id: 'bond-7', name: '忘关灯的清晨客厅', summary: '沙发上的薄毯和睡着的侧脸被光线温柔收编。' },
  { id: 'bond-8', name: '一起挑错路的地铁站', summary: '明明绕远了，却像多偷来一段独处时间。' },
  { id: 'bond-9', name: '被风吹乱的刘海', summary: '伸手整理的动作比告白更先一步。' },
  { id: 'bond-10', name: '停电时借来的手电筒', summary: '狭小光圈里，彼此的表情都变得过分清晰。' },
  { id: 'bond-11', name: '练习失败的生日歌', summary: '笑场了很多次，但还是想唱给同一个人听。' },
  { id: 'bond-12', name: '深夜便利店的半价甜点', summary: '最后那一口谁也没舍得先吃掉。' },
  { id: 'bond-13', name: '阳台上晾不干的衬衫', summary: '伸手够衣角的时候，心事也差点一起暴露。' },
  { id: 'bond-14', name: '下雪天借出的围巾', summary: '体温在柔软纤维里留下了比天气更久的记忆。' },
  { id: 'bond-15', name: '雨后共享的一把伞', summary: '伞面不大，沉默却装得下很多没说出口的话。' },
]

const FORGE_MACHINE_SLOT_COUNT = 5

function pickTemporaryForgeSlots(count = FORGE_MACHINE_SLOT_COUNT, mode = 'fallback') {
  return [...FORGE_EVENT_POOL]
    .sort(() => Math.random() - 0.5)
    .slice(0, count)
    .map((slot, index) => ({
      ...slot,
      id: `temporary-${mode}-${slot.id}-${Date.now()}-${index}`,
      storyLead: slot.storyLead || slot.summary || '',
      factText: slot.summary || slot.storyLead || '',
      sourceKind: 'temporary',
      sourceCharacter: '',
      sourceFactId: null,
      sourceFactHash: null,
      sourceLabel: mode === 'fill' ? '临时补足' : '临时预设',
      temporaryFill: mode === 'fill',
    }))
}

function mapApiFactsToForgeSlots(facts, source = {}) {
  return facts.map((f) => {
    const text = typeof f.text === 'string' ? f.text : ''
    const shortText = text.length > 24 ? `${text.slice(0, 24)}…` : text || '（无文案）'
    const rawId = f.id != null && f.id !== '' ? String(f.id) : ''
    return {
      id: rawId ? `fact-slot-${rawId}` : `fact-${Date.now()}-${Math.random()}`,
      name: `记忆事件：${shortText}`,
      summary: `故事引子：${text || '暂无可用 fact 文本'}`,
      storyLead: text,
      factText: text,
      sourceKind: 'fact',
      sourceCharacter: source.character || '',
      sourceLabel: '记忆事件',
      recentGuaranteed: Boolean(f.recentGuaranteed),
      distantGuaranteed: Boolean(f.distantGuaranteed),
      sourceCollection: f.sourceCollection || 'facts',
      sourceFactId: rawId || null,
      sourceFactHash: f.hash || '',
      factMeta: {
        entity: f.entity || '',
        importance: f.importance ?? null,
        tags: Array.isArray(f.tags) ? f.tags : [],
        createdAt: f.created_at || null,
        eventStartAt: f.event_start_at || null,
      },
    }
  })
}

function formatForgeFactDebugStamp(slot) {
  if (slot?.sourceKind !== 'fact') return ''
  const createdAt = slot.factMeta?.eventStartAt || slot.factMeta?.createdAt
  const parsedDate = createdAt ? new Date(createdAt) : null
  const dateText = parsedDate && !Number.isNaN(parsedDate.getTime())
    ? `${String(parsedDate.getMonth() + 1).padStart(2, '0')}/${String(parsedDate.getDate()).padStart(2, '0')}`
    : '日期?'
  const importance = slot.factMeta?.importance
  const importanceText = importance == null || importance === '' ? 'I?' : `I${importance}`
  return `${dateText} · ${importanceText}`
}

function buildForgeMachineSlots(facts, source = {}) {
  const factSlots = mapApiFactsToForgeSlots(facts, source).slice(0, FORGE_MACHINE_SLOT_COUNT)
  if (factSlots.length >= FORGE_MACHINE_SLOT_COUNT) {
    return {
      slots: factSlots,
      status: 'facts',
      notice: source.character
        ? `已连接 ${source.character} 的记忆库，读取到 ${factSlots.length} 条记忆事件。`
        : `已读取到 ${factSlots.length} 条记忆事件。`,
    }
  }

  if (factSlots.length > 0) {
    const distantFactSlots = factSlots.filter(slot => slot.distantGuaranteed)
    const regularFactSlots = factSlots.filter(slot => !slot.distantGuaranteed)
    const temporaryFillSlots = pickTemporaryForgeSlots(FORGE_MACHINE_SLOT_COUNT - factSlots.length, 'fill')
    return {
      slots: [
        ...regularFactSlots,
        ...temporaryFillSlots,
        ...distantFactSlots,
      ],
      status: 'mixed',
      notice: `当前猫娘可用记忆不足 ${FORGE_MACHINE_SLOT_COUNT} 条，已保留 ${factSlots.length} 条真实记忆，并用临时事件补足。`,
    }
  }

  const reason = source.fallbackReason === 'all_available_facts_excluded'
    ? '可用记忆已全部铸造'
    : source.fallbackReason === 'runtime_character_hint_missing' || source.error === 'active_neko_runtime_not_linked'
      ? '未链接到当前猫娘运行态'
    : source.fallbackReason === 'no_facts_after_filter'
      ? '当前猫娘暂无可用记忆'
      : source.error
        ? '未链接到猫娘记忆库'
        : '当前没有可用记忆'
  return {
    slots: pickTemporaryForgeSlots(FORGE_MACHINE_SLOT_COUNT, 'fallback'),
    status: 'fallback',
    notice: `${reason}，临时使用预设事件。`,
  }
}

function rollForgeRarity() {
  const total = FORGE_RARITY_TABLE.reduce((sum, item) => sum + item.weight, 0)
  let roll = Math.random() * total

  for (const rarity of FORGE_RARITY_TABLE) {
    roll -= rarity.weight
    if (roll <= 0) return rarity
  }

  return FORGE_RARITY_TABLE[0]
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms))
}

function buildForgeStoryRequest(card, event, character) {
  return {
    character: character || event?.sourceCharacter || '',
    runtimeCharacterHint: character || event?.sourceCharacter || '',
    storyLead: card?.storyLead || event?.storyLead || event?.factText || event?.summary || '',
    sourceFactId: card?.sourceFactId || event?.sourceFactId || null,
    card: {
      attrName: card?.attrName || card?.attribute || '',
    },
  }
}

// 后端 FORGE_STORY_TIMEOUT_SECONDS=25，前端给一点宽裕：30 秒。
// 超过时长用 AbortController 触发 abort，避免铸造动画无限转圈。
const FORGE_STORY_FETCH_TIMEOUT_MS = 30_000

async function requestForgeCardStory(card, event, character) {
  if (!card?.storyLead && !event?.storyLead && !event?.factText && !event?.summary) return null
  const runtimeCharacter = character || event?.sourceCharacter || ''
  if (!runtimeCharacter) return null
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), FORGE_STORY_FETCH_TIMEOUT_MS)
  try {
    const res = await fetch('/forge/card-story', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildForgeStoryRequest(card, event, runtimeCharacter)),
      signal: controller.signal,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || !data?.success || !data?.story) return null
    const story = composeForgedCardStory(card?.storyLead || event?.storyLead || event?.factText || event?.summary || '', data.story, card)
    return {
      story,
      summary: story,
      storyGenerationStatus: data.storyGenerationStatus || 'ready',
      storyGeneratedAt: Date.now(),
      storyModel: data.model || '',
      storyProvider: data.provider || '',
    }
  } catch {
    // AbortError 或网络错误，回退到本地占位故事。
    return null
  } finally {
    clearTimeout(timeoutId)
  }
}

async function createForgedCardWithLlmStory(event, character, options = {}) {
  const card = createForgedBrawlCard(event, options)
  const storyPatch = await requestForgeCardStory(card, event, character)
  if (!storyPatch) {
    const story = composeForgedCardStory(card.storyLead, '', card)
    return {
      ...card,
      story,
      summary: story,
      storyGenerationStatus: 'temporary-fallback',
      storyError: 'LLM story generation failed',
    }
  }
  return {
    ...card,
    ...storyPatch,
  }
}

export default function App() {
  const [activeCharacterName, setActiveCharacterName] = useState(null)
  const [forgedInventory, setForgedInventory] = useState(() => loadForgedBrawlCards())
  const [inspectCard, setInspectCard] = useState(null)

  const [showForgeMachine, setShowForgeMachine] = useState(false)
  const [forgeMachineSlots, setForgeMachineSlots] = useState(() => pickTemporaryForgeSlots())
  const [forgeMachineLoading, setForgeMachineLoading] = useState(false)
  const [forgeMachineNotice, setForgeMachineNotice] = useState('')
  const [forgeMachineSourceStatus, setForgeMachineSourceStatus] = useState('fallback')
  const [machinePhase, setMachinePhase] = useState('idle')
  const [machineStoryStatus, setMachineStoryStatus] = useState('')
  const [machinePickedId, setMachinePickedId] = useState(null)
  const [machineForgedCard, setMachineForgedCard] = useState(null)

  const hasForgedRef = useRef(false)

  useEffect(() => {
    saveForgedBrawlCards(forgedInventory)
  }, [forgedInventory])

  // 从 NEKO 主服务同步当前猫娘名，作为 runtime_character_hint 提供给 /forge/facts。
  // 拿到空 name 时也要把本地 state 清掉，否则服务端缓存清空（例如重启）后前端仍会显示旧猫娘名。
  useEffect(() => {
    let timer = null
    async function fetchActiveCharacter() {
      try {
        const res = await fetch('/card-forge/active-character')
        if (!res.ok) {
          setActiveCharacterName(null)
          return
        }
        const { name } = await res.json()
        setActiveCharacterName(name ? name : null)
      } catch {
        // 主服务不可达或响应无效时 fail closed，避免继续携带旧 runtime hint。
        setActiveCharacterName(null)
      }
    }
    fetchActiveCharacter()
    timer = setInterval(fetchActiveCharacter, 5000)
    return () => clearInterval(timer)
  }, [])

  const loadForgeMachineSlots = useCallback(async () => {
    const qs = new URLSearchParams()
    if (!activeCharacterName) {
      return buildForgeMachineSlots([], {
        error: 'active_neko_runtime_not_linked',
        fallbackReason: 'runtime_character_hint_missing',
      })
    }
    qs.set('runtime_character_hint', activeCharacterName)
    qs.set('include_absorbed', 'true')
    qs.set('min_importance', '0')
    qs.set('limit', '5')
    // exclude 只能算当前猫娘已铸造过的 fact —— inventory 里可能含来自其他猫娘
    // 的卡牌 (用户切换过猫娘),把它们一并 exclude 会让本猫娘的可用 fact 池被错误地缩水。
    // 没有 sourceCharacter 字段的旧卡 (历史 / 临时) 当作"属于任意猫娘",保留 exclude
    // 以维持向后兼容。
    const inventoryForActiveCharacter = forgedInventory.filter(card => (
      !card.sourceCharacter || card.sourceCharacter === activeCharacterName
    ))
    const usedFactIds = inventoryForActiveCharacter.map(card => card.sourceFactId).filter(Boolean)
    const usedFactHashes = inventoryForActiveCharacter.map(card => card.sourceFactHash).filter(Boolean)
    if (usedFactIds.length > 0) qs.set('exclude_fact_ids', Array.from(new Set(usedFactIds)).join(','))
    if (usedFactHashes.length > 0) qs.set('exclude_hashes', Array.from(new Set(usedFactHashes)).join(','))
    try {
      const res = await fetch(`/forge/facts?${qs.toString()}`)
      if (!res.ok) throw new Error('forge-facts http')
      const data = await res.json()
      const facts = Array.isArray(data.facts) ? data.facts : []
      return buildForgeMachineSlots(facts, {
        character: data.character || '',
        fallbackReason: data.fallbackReason || '',
        error: data.error || '',
      })
    } catch (error) {
      return buildForgeMachineSlots([], {
        error: error?.message || 'fetch_failed',
      })
    }
  }, [forgedInventory, activeCharacterName])

  const applyForgeMachineLoad = useCallback(async () => {
    setForgeMachineLoading(true)
    const result = await loadForgeMachineSlots()
    setForgeMachineSlots(result.slots)
    setForgeMachineNotice(result.notice)
    setForgeMachineSourceStatus(result.status)
    setForgeMachineLoading(false)
  }, [loadForgeMachineSlots])

  const resetForgeMachine = useCallback(() => {
    setMachinePhase('idle')
    setMachinePickedId(null)
    setMachineForgedCard(null)
    setMachineStoryStatus('')
    hasForgedRef.current = false
    void applyForgeMachineLoad()
  }, [applyForgeMachineLoad])

  const openForgeMachine = useCallback(async () => {
    setMachinePhase('idle')
    setMachinePickedId(null)
    setMachineForgedCard(null)
    setMachineStoryStatus('')
    hasForgedRef.current = false
    setShowForgeMachine(true)
    await applyForgeMachineLoad()
  }, [applyForgeMachineLoad])

  const handleMachineCardClick = useCallback(async (slotId) => {
    if (machinePhase === 'idle') {
      setMachinePickedId(slotId)
      setMachineStoryStatus('')
      setMachinePhase('confirming')
    } else if (machinePhase === 'confirming' && machinePickedId === slotId) {
      const pickedSlot = forgeMachineSlots.find(s => s.id === slotId)
      if (!pickedSlot || hasForgedRef.current) return
      hasForgedRef.current = true
      setMachinePhase('burning')
      await sleep(900)
      setMachinePhase('floating')
      await sleep(800)
      setMachineStoryStatus('正在根据原始引子生成卡牌故事…')
      setMachinePhase('storyGenerating')
      const rarity = rollForgeRarity()
      const [forgedCard] = await Promise.all([
        createForgedCardWithLlmStory(pickedSlot, pickedSlot.sourceCharacter || activeCharacterName || ''),
        sleep(1400),
      ])
      const storedCard = {
        ...forgedCard,
        attribute: forgedCard.attrName,
        rarity: rarity.name,
        rarityStyle: rarity.tagStyle,
        rarityFrame: rarity.frame,
      }
      setMachineStoryStatus('故事已写入卡面，准备完成铸造…')
      setMachineForgedCard(storedCard)
      setForgedInventory(prev => [...prev, storedCard])
      setMachinePhase('flipping')
      await sleep(650)
      setMachinePhase('revealed')
    } else if (machinePhase === 'confirming') {
      setMachinePickedId(slotId)
      setMachineStoryStatus('')
    }
  }, [machinePhase, machinePickedId, forgeMachineSlots, activeCharacterName])

  const handleDeleteForgedCard = useCallback((card) => {
    if (!card) return
    setForgedInventory(prev => prev.filter(item => (
      item.id !== card.id &&
      item.code !== card.code
    )))
  }, [])

  const inventorySorted = useMemo(
    () => [...forgedInventory].sort((a, b) => (b.forgedAt || 0) - (a.forgedAt || 0)),
    [forgedInventory],
  )

  return (
    <div className="relative h-screen flex flex-col overflow-hidden select-none bg-[#1b2430] isolate">
      <div className="absolute inset-0 bg-gradient-to-br from-violet-950/40 via-slate-950 to-pink-950/30 z-0" />

      {/* 顶部标题栏 */}
      <div className="fixed top-0 left-0 right-0 z-50 h-12 bg-white border-b border-gray-200 flex items-center justify-between px-4">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-violet-500" />
          <h1 className="text-base font-black text-black tracking-wide">奇遇铸造机</h1>
          <Sparkles className="w-4 h-4 text-pink-500" />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">
            {activeCharacterName ? `当前猫娘：${activeCharacterName}` : '未链接 NEKO 当前猫娘'}
          </span>
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={openForgeMachine}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-violet-50 border border-violet-200 text-xs text-violet-700 hover:bg-violet-100 transition-all"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>打开奇遇铸造机</span>
          </motion.button>
        </div>
      </div>

      {/* 主体：成品卡仓库展示 */}
      <main className="relative z-10 flex-1 overflow-y-auto px-6 pt-[60px] pb-6">
        <div className="mx-auto max-w-6xl">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-black text-white flex items-center gap-2">
                <BookHeart className="w-4 h-4 text-violet-300" />
                铸造卡仓库
              </h2>
              <p className="text-xs text-gray-400 mt-1">
                共 {inventorySorted.length} 张铸造卡 · 点击卡片查看锻造故事
              </p>
            </div>
          </div>

          {inventorySorted.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-white/15 bg-white/[0.03] p-10 text-center">
              <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-violet-500/10 text-3xl">
                🎴
              </div>
              <p className="mt-4 text-sm font-bold text-white">还没有任何铸造卡</p>
              <p className="mt-1 text-xs text-gray-400">
                打开奇遇铸造机，从当前猫娘的记忆事件中铸造你的第一张羁绊卡。
              </p>
              <button
                type="button"
                onClick={openForgeMachine}
                className="mt-4 rounded-xl bg-gradient-to-r from-violet-500 to-pink-500 px-5 py-2 text-sm font-bold text-white shadow-lg shadow-violet-900/30 transition-transform hover:scale-105"
              >
                立即铸造
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {inventorySorted.map(card => (
                <motion.div
                  key={card.id}
                  layout
                  whileHover={{ scale: 1.02 }}
                  onClick={() => setInspectCard(card)}
                  className={`relative cursor-pointer rounded-2xl border bg-slate-950/60 p-4 shadow-lg transition-colors hover:border-violet-400/60 ${card.rarityFrame || 'border-white/10'}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[10px] font-black uppercase tracking-widest text-violet-300/80">
                      {card.baseCode || card.code}
                    </span>
                    {card.rarity && (
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] font-black ${card.rarityStyle || 'border-white/10 text-gray-300'}`}>
                        {card.rarity}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-sm font-black text-white">{card.name}</p>
                  <p className="mt-1 text-[11px] text-gray-400">
                    主属性 {card.attrName} · Combo {card.comboAttrName}
                  </p>
                  <p className="mt-3 line-clamp-3 text-[11px] leading-relaxed text-gray-300">
                    {card.storyLead || card.story}
                  </p>
                  <div className="mt-3 flex items-center justify-between">
                    <span className="text-[10px] text-gray-500">行动力 {card.cost}</span>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); handleDeleteForgedCard(card) }}
                      className="rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[10px] text-gray-400 hover:border-rose-400/40 hover:bg-rose-500/10 hover:text-rose-200"
                      title="删除"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                </motion.div>
              ))}
            </div>
          )}
        </div>
      </main>

      <CardInspectModal
        card={inspectCard}
        open={Boolean(inspectCard)}
        onClose={() => setInspectCard(null)}
        source="forged-inventory"
      />

      {/* 奇遇铸造机 modal */}
      <AnimatePresence>
        {showForgeMachine && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-40 flex items-center justify-center bg-black/50 px-4"
            onClick={() => {
              if (machinePhase === 'idle' || machinePhase === 'confirming' || machinePhase === 'revealed') {
                setShowForgeMachine(false)
                resetForgeMachine()
              }
            }}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 16 }}
              transition={{ duration: 0.22 }}
              className="w-full max-w-6xl overflow-hidden rounded-3xl border border-white/15 bg-[#1f2937] shadow-2xl relative"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex items-center justify-between px-5 pt-4 pb-2">
                <h3 className="text-lg font-black text-white">奇遇铸造机</h3>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    title="测试用重Roll入口"
                    onClick={() => {
                      if (machinePhase !== 'idle' && machinePhase !== 'confirming' && machinePhase !== 'revealed') return
                      setMachinePhase('idle')
                      setMachinePickedId(null)
                      setMachineForgedCard(null)
                      setMachineStoryStatus('')
                      hasForgedRef.current = false
                      void applyForgeMachineLoad()
                    }}
                    disabled={forgeMachineLoading || (machinePhase !== 'idle' && machinePhase !== 'confirming' && machinePhase !== 'revealed')}
                    className="rounded-lg border border-violet-300/25 bg-violet-500/10 px-3 py-1.5 text-sm font-bold text-violet-100 transition-colors hover:bg-violet-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    重Roll记忆
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (machinePhase !== 'idle' && machinePhase !== 'confirming' && machinePhase !== 'revealed') return
                      setShowForgeMachine(false)
                      resetForgeMachine()
                    }}
                    disabled={machinePhase !== 'idle' && machinePhase !== 'confirming' && machinePhase !== 'revealed'}
                    className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-gray-300 transition-colors hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    关闭
                  </button>
                </div>
              </div>

              {(forgeMachineNotice || forgeMachineLoading) && (
                <div className={`mx-5 mb-2 rounded-2xl border px-4 py-2 text-xs ${
                  forgeMachineSourceStatus === 'facts'
                    ? 'border-emerald-300/25 bg-emerald-500/10 text-emerald-100'
                    : forgeMachineSourceStatus === 'mixed'
                      ? 'border-amber-300/30 bg-amber-500/10 text-amber-100'
                      : 'border-rose-300/25 bg-rose-500/10 text-rose-100'
                }`}>
                  {forgeMachineLoading ? '正在链接当前猫娘记忆库…' : forgeMachineNotice}
                </div>
              )}

              {(machinePhase === 'floating' || machinePhase === 'storyGenerating' || machinePhase === 'flipping' || machinePhase === 'revealed') ? (() => {
                const pickedSlot = forgeMachineSlots.find(s => s.id === machinePickedId)
                return (
                <div className="flex flex-col items-center justify-center px-5 pb-8 pt-4 min-h-[400px]">
                  <motion.div
                    initial={{ y: 0, scale: 1 }}
                    animate={
                      machinePhase === 'floating'
                        ? { y: -20, scale: 1.15 }
                        : machinePhase === 'storyGenerating'
                        ? { y: -28, scale: 1.18, rotateY: [0, 360], boxShadow: '0 0 60px rgba(168,85,247,0.45)' }
                        : machinePhase === 'flipping'
                        ? { y: -20, scale: 1.15, rotateY: 360 }
                        : { y: 0, scale: 1.1, rotateY: 360 }
                    }
                    transition={
                      machinePhase === 'storyGenerating'
                        ? { rotateY: { duration: 0.9, repeat: Infinity, ease: 'linear' }, y: { duration: 0.45 }, scale: { duration: 0.45 }, boxShadow: { duration: 0.45 } }
                        : { duration: machinePhase === 'flipping' ? 0.65 : 0.6, ease: 'easeInOut' }
                    }
                    style={{ perspective: 800 }}
                    className="w-[180px] rounded-2xl border border-violet-400/50 bg-slate-950/80 p-4 flex flex-col items-center min-h-[300px] shadow-2xl shadow-violet-900/40"
                  >
                    <span className="text-[10px] font-semibold text-violet-400 uppercase tracking-widest mb-2">
                      {machinePhase === 'revealed' ? '✦ 铸造完成' : machinePhase === 'storyGenerating' ? '故事注入中…' : '铸造中…'}
                    </span>
                    <div className="flex-1 w-full rounded-xl border border-violet-400/20 bg-violet-500/5 flex flex-col items-center justify-center p-3">
                      <div className="w-12 h-12 rounded-full bg-violet-500/20 flex items-center justify-center text-2xl mb-3">
                        {machinePhase === 'revealed' ? '✨' : machinePhase === 'storyGenerating' ? '✍' : '🎴'}
                      </div>
                      <p className="text-sm font-bold text-white text-center">
                        {machineForgedCard?.name || pickedSlot?.name}
                      </p>
                      <p className="text-[10px] text-gray-400 text-center mt-2 leading-relaxed">
                        {machinePhase === 'storyGenerating'
                          ? (machineStoryStatus || '正在等待故事生成完成…')
                          : (machineForgedCard?.story || pickedSlot?.summary)}
                      </p>
                    </div>
                  </motion.div>

                  {machinePhase === 'storyGenerating' && (
                    <motion.div
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="mt-5 w-full max-w-md rounded-2xl border border-violet-400/30 bg-violet-500/10 p-3 text-center"
                    >
                      <p className="text-xs font-black text-violet-100">故事必须先写入卡面，才会完成铸造</p>
                      <p className="mt-1 text-[11px] leading-relaxed text-violet-200/80">
                        原始引子：{pickedSlot?.storyLead || pickedSlot?.summary}
                      </p>
                    </motion.div>
                  )}

                  <AnimatePresence>
                    {machinePhase === 'revealed' && (
                      <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3, duration: 0.5 }}
                        className="mt-6 text-center"
                      >
                        <p className="text-xl font-black text-transparent bg-clip-text bg-gradient-to-r from-amber-300 via-pink-400 to-violet-400">
                          新的羁绊事件诞生了！
                        </p>
                        <p className="mt-2 text-sm text-gray-300">恭喜！此卡片已收录到铸造卡仓库</p>
                        <button
                          type="button"
                          onClick={() => {
                            setShowForgeMachine(false)
                            resetForgeMachine()
                          }}
                          className="mt-4 rounded-xl bg-gradient-to-r from-violet-500 to-pink-500 px-6 py-2 text-sm font-bold text-white shadow-lg shadow-violet-900/30 transition-transform hover:scale-105"
                        >
                          确认收下
                        </button>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
                )
              })() : forgeMachineLoading ? (
                <div className="flex min-h-[360px] flex-col items-center justify-center px-5 pb-8 pt-6 text-center">
                  <div className="h-12 w-12 animate-spin rounded-full border-4 border-violet-300/20 border-t-violet-300" />
                  <p className="mt-4 text-sm font-black text-white">正在读取猫娘记忆库</p>
                  <p className="mt-2 max-w-md text-xs leading-relaxed text-gray-400">
                    铸造机会优先抽取当前猫娘的真实 facts；如果不足 5 条，会保留全部真实记忆并用临时事件补足。
                  </p>
                </div>
              ) : (
                <div className="flex gap-3 px-5 pb-5 pt-2">
                  <AnimatePresence>
                    {forgeMachineSlots.map((slot, index) => {
                      const isPicked = machinePickedId === slot.id
                      const isBurning = machinePhase === 'burning' && !isPicked
                      const isTemporary = slot.sourceKind === 'temporary'
                      const isRecentGuaranteed = Boolean(slot.recentGuaranteed)
                      const isDistantGuaranteed = Boolean(slot.distantGuaranteed)
                      const sourceLabel = slot.sourceLabel || (isTemporary ? '临时预设' : '记忆事件')
                      const factDebugStamp = formatForgeFactDebugStamp(slot)

                      if (isBurning) {
                        return (
                          <motion.div
                            key={slot.id}
                            initial={{ opacity: 1, scale: 1 }}
                            animate={{ opacity: 0, scale: 0.7, y: 30, filter: 'brightness(2) saturate(0)' }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.7, delay: index * 0.1 }}
                            className={`flex-1 rounded-2xl border p-3 flex flex-col items-center min-h-[340px] relative overflow-hidden ${
                              isRecentGuaranteed
                                ? 'border-emerald-300/70 bg-gradient-to-t from-emerald-500/30 via-lime-500/20 to-transparent ring-2 ring-emerald-300/35'
                                : isDistantGuaranteed
                                  ? 'border-orange-300/75 bg-gradient-to-t from-orange-500/30 via-amber-500/20 to-transparent ring-2 ring-orange-300/40'
                                : 'border-orange-500/40 bg-gradient-to-t from-orange-600/30 via-red-500/20 to-transparent'
                            }`}
                          >
                            <div className="absolute inset-0 bg-gradient-to-t from-orange-500/60 via-red-400/30 to-transparent animate-pulse" />
                            <div className="relative z-10 mb-2 flex w-full items-center justify-between gap-2">
                              <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest">No.{index + 1}</span>
                              <span className={`rounded-full border px-2 py-0.5 text-[9px] font-black ${
                                isTemporary
                                  ? 'border-amber-300/35 bg-amber-500/15 text-amber-100'
                                  : 'border-emerald-300/35 bg-emerald-500/15 text-emerald-100'
                              }`}>{sourceLabel}</span>
                            </div>
                            <div className="flex-1 w-full rounded-xl border border-white/8 bg-white/[0.03] flex flex-col items-center justify-center p-3 relative z-10 opacity-50">
                              <div className="w-10 h-10 rounded-full bg-orange-500/20 flex items-center justify-center text-lg mb-3">🔥</div>
                              <p className="text-sm font-bold text-orange-200 text-center">{slot.name}</p>
                            </div>
                          </motion.div>
                        )
                      }

                      return (
                        <motion.div
                          key={slot.id}
                          layout
                          exit={{ opacity: 0 }}
                          onClick={() => handleMachineCardClick(slot.id)}
                          className={`forge-card-wrapper relative flex-1 rounded-2xl border p-3 flex flex-col items-center min-h-[340px] cursor-pointer transition-all duration-200 ${
                            isPicked && machinePhase === 'confirming'
                              ? 'border-violet-400/60 bg-violet-500/10 ring-2 ring-violet-400/30'
                              : isTemporary
                                ? 'border-amber-300/25 bg-amber-950/20'
                                : isRecentGuaranteed
                                  ? 'border-emerald-300/70 bg-emerald-950/30 ring-2 ring-emerald-300/35 shadow-[0_0_26px_rgba(110,231,183,0.22)]'
                                  : isDistantGuaranteed
                                    ? 'border-orange-300/75 bg-orange-950/30 ring-2 ring-orange-300/40 shadow-[0_0_28px_rgba(251,146,60,0.24)]'
                                : 'border-emerald-300/20 bg-slate-950/45'
                          }`}
                        >
                          <div className="mb-2 flex w-full items-center justify-between gap-2">
                            <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest">No.{index + 1}</span>
                            <span className={`rounded-full border px-2 py-0.5 text-[9px] font-black ${
                              isTemporary
                                ? 'border-amber-300/35 bg-amber-500/15 text-amber-100'
                                : 'border-emerald-300/35 bg-emerald-500/15 text-emerald-100'
                            }`}>{sourceLabel}</span>
                          </div>
                          <div className={`flex-1 w-full rounded-xl border bg-white/[0.03] flex flex-col items-center justify-center p-3 ${
                            isRecentGuaranteed
                              ? 'border-emerald-200/25 shadow-inner shadow-emerald-900/20'
                              : isDistantGuaranteed
                                ? 'border-orange-200/30 shadow-inner shadow-orange-900/25'
                                : 'border-white/8'
                          }`}>
                            <div className="w-10 h-10 rounded-full bg-violet-500/10 flex items-center justify-center text-lg mb-3">🎴</div>
                            <p className="text-sm font-bold text-white text-center">{slot.name}</p>
                            <p className="text-[10px] text-gray-400 text-center mt-2 leading-relaxed">{slot.summary}</p>
                          </div>
                          {factDebugStamp && (
                            <div className={`pointer-events-none absolute bottom-2 right-2 rounded-full border px-2 py-0.5 text-[10px] font-black shadow-lg ${
                              isDistantGuaranteed
                                ? 'border-orange-300/60 bg-orange-500/15 text-orange-100 shadow-orange-950/30'
                                : isRecentGuaranteed
                                  ? 'border-emerald-300/60 bg-emerald-500/15 text-emerald-100 shadow-emerald-950/30'
                                  : 'border-slate-300/25 bg-slate-950/70 text-slate-200 shadow-black/25'
                            }`}>
                              {factDebugStamp}
                            </div>
                          )}
                          <AnimatePresence>
                            {isPicked && machinePhase === 'confirming' && (
                              <motion.div
                                initial={{ opacity: 0, y: 8 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: 8 }}
                                className="mt-2 w-full rounded-lg bg-violet-500/20 border border-violet-400/30 px-3 py-2 text-center"
                              >
                                <p className="text-xs text-violet-200 font-bold">确定选择这个事件吗？</p>
                                <p className="text-[10px] text-violet-300/70 mt-1">再次点击确认</p>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </motion.div>
                      )
                    })}
                  </AnimatePresence>
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
