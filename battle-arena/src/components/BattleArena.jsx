import { useState, useCallback, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, Crown, Zap, RotateCcw, ChevronRight, Medal, Star, Trophy, BarChart3, Volume2, VolumeX } from 'lucide-react'
import NekoCard from './NekoCard'
import BattleLog from './BattleLog'
import BottomTicker from './BottomTicker'
import CardGamePanel from './CardGamePanel'
import DeckBuilderPanel from './neko-brawl/DeckBuilderPanel'
import DeckLibraryPanel from './neko-brawl/DeckLibraryPanel'
import { playNekoBrawlSceneBgm, stopNekoBrawlBgm } from './neko-brawl/nekoBrawlAudio'
import {
  composeForgedCardStory,
  createForgedBrawlCard,
  loadForgedBrawlCards,
  saveForgedBrawlCards,
} from '../data/forgedBrawlCards'

// ─────────────────────────────────────────────────────────────────────────────
// 占位数据  ——  将来由后端 API / 全局状态管理替换
// ─────────────────────────────────────────────────────────────────────────────

// TODO: [羁绊列表接入] 以下为占位内容，待羁绊数据结构和 API 确定后替换为真实数据
// 真实数据来源: N.E.K.O 主应用的羁绊记录系统
const MY_BONDS_PLACEHOLDER = [
  '与主人愉快的第一天',
  '主人和我陪伴的100小时',
  '主人夸我的第一次',
  '和主人一起看的第一次日出',
  '主人生病时我在身旁的那个夜晚',
]

const NEKO_LEFT = {
  id: 'neko-left',
  name: '猫娘 A',
  title: '待匹配',
  level: 0,
  rank: '?',
  owner: '玩家A',
  avatar: null, // TODO: [头像接入] 由头像提取功能提供 URL
  wins: 0,
  totalBattles: 0,
  winRate: 0,
}

// 右侧对手数据由匹配服务器提供，此处仅保留空壳默认值
const NEKO_RIGHT_DEFAULT = {
  id: 'neko-right',
  name: '等待对手…',
  title: '匹配中',
  level: 0,
  rank: '?',
  owner: '???',
  avatar: null,
  wins: 0,
  totalBattles: 0,
  winRate: 0,
  bonds: [null, null, null, null, null],
}

// 排行榜占位
const RANKING = Array.from({ length: 5 }, (_, i) => ({
  rank: i + 1,
  name: '???',
  owner: '---',
  score: 0,
}))

const MAX_DAILY = 10
const SHOW_LEGACY_FORGE_PANEL = false

const JUDGING_FLAVOR_LINES = [
  '正在贿赂评委中....',
  '正在偷偷瞄战斗结果...',
  '正在假装认真翻阅羁绊档案...',
  '评委席正在交换意味深长的眼神...',
]

const WAITING_IDLES = [
  '/waiting_idle.gif',
  '/waiting_idle2.gif',
  '/waiting_idle3.gif',
  '/waiting_idle4.gif',
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
  { id: 'bond-16', name: '错发又撤回的消息', summary: '撤回得很快，但紧张已经暴露了全部。' },
  { id: 'bond-17', name: '被抢走的第一口冰淇淋', summary: '抗议声里藏着一点点理所当然的亲近。' },
  { id: 'bond-18', name: '图书馆里同一页批注', summary: '两种字迹在纸上靠近，像提前排练好的默契。' },
  { id: 'bond-19', name: '错过末班车后的长椅', summary: '夜色很安静，连心跳都像故意放慢了节奏。' },
  { id: 'bond-20', name: '午后窗边的打盹', summary: '醒来时发现有人替你挡住了刺眼的阳光。' },
]

const FORGE_ENCHANTMENTS = [
  '月光回响',
  '流星余温',
  '蜜糖誓约',
  '静电心跳',
  '晨雾守护',
  '极夜共鸣',
]

const FORGE_CARD_ATTRIBUTES = ['搞笑', '温馨', '逆天', '小丑', '傲娇']

const FORGE_ATTRIBUTE_COUNTERS = {
  搞笑: '克制傲娇，被小丑克制',
  温馨: '克制逆天，被傲娇克制',
  逆天: '克制小丑，被温馨克制',
  小丑: '克制搞笑，被逆天克制',
  傲娇: '克制温馨，被搞笑克制',
}

const FORGE_ATTRIBUTE_STYLES = {
  搞笑: {
    pill: 'border-yellow-400/30 bg-yellow-500/10 text-yellow-200',
    badge: 'text-yellow-300',
    title: 'text-yellow-300',
  },
  温馨: {
    pill: 'border-pink-400/30 bg-pink-500/10 text-pink-200',
    badge: 'text-pink-300',
    title: 'text-pink-300',
  },
  逆天: {
    pill: 'border-violet-400/30 bg-violet-500/10 text-violet-200',
    badge: 'text-violet-300',
    title: 'text-violet-300',
  },
  小丑: {
    pill: 'border-emerald-400/30 bg-emerald-500/10 text-emerald-200',
    badge: 'text-emerald-300',
    title: 'text-emerald-300',
  },
  傲娇: {
    pill: 'border-rose-400/30 bg-rose-500/10 text-rose-200',
    badge: 'text-rose-300',
    title: 'text-rose-300',
  },
}

const FORGE_RARITY_TABLE = [
  {
    name: '普通',
    weight: 40,
    tagStyle: 'border-gray-400/30 bg-gray-500/10 text-gray-200',
    frame: 'border-gray-400/30 shadow-gray-900/10',
  },
  {
    name: '稀有',
    weight: 28,
    tagStyle: 'border-sky-400/30 bg-sky-500/10 text-sky-200',
    frame: 'border-sky-400/40 shadow-sky-900/20',
  },
  {
    name: '奇想',
    weight: 17,
    tagStyle: 'border-violet-400/30 bg-violet-500/10 text-violet-200',
    frame: 'border-violet-400/40 shadow-violet-900/25',
  },
  {
    name: '璀璨',
    weight: 11,
    tagStyle: 'border-amber-400/30 bg-amber-500/10 text-amber-200',
    frame: 'border-amber-400/50 shadow-amber-900/25',
  },
  {
    name: '唯一',
    weight: 4,
    tagStyle: 'border-rose-400/30 bg-rose-500/10 text-rose-200',
    frame: 'border-rose-400/50 shadow-rose-900/30',
  },
]

const FORGE_MACHINE_SLOT_COUNT = 5

function pickUniqueForgeSlots() {
  return [...FORGE_EVENT_POOL]
    .sort(() => Math.random() - 0.5)
    .slice(0, FORGE_MACHINE_SLOT_COUNT)
}

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

/** 将 /arena/forge-facts 返回项映射为铸造机卡槽：fact 只作为故事引子，不直接当作最终卡牌故事。 */
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

async function requestForgeCardStory(card, event, character) {
  if (!card?.storyLead && !event?.storyLead && !event?.factText && !event?.summary) return null
  const runtimeCharacter = character || event?.sourceCharacter || ''
  if (!runtimeCharacter) return null
  try {
    const res = await fetch('/arena/forge-card-story', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildForgeStoryRequest(card, event, runtimeCharacter)),
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
    return null
  }
}

async function createForgedCardWithLlmStory(event, character, options = {}) {
  const card = createForgedBrawlCard(event, options)
  // 先 Roll 出基础卡与规则字段；故事生成只参考故事引子和主属性气质，
  // 不参考卡名、编号、费用、类型、效果或 Combo 属性，避免故事和游戏规则绑定。
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

// ─────────────────────────────────────────────────────────────────────────────

export default function BattleArena() {
  const [scoreLeft,  setScoreLeft]  = useState(0)
  const [scoreRight, setScoreRight] = useState(0)
  const [logs,       setLogs]       = useState([])
  const [remaining,  setRemaining]  = useState(MAX_DAILY)
  const [battling,   setBattling]   = useState(false)
  const [phase,      setPhase]      = useState('idle')  // idle | judging | result
  const [activeSide, setActiveSide] = useState(null)    // 'left' | 'right' | null
  const [result,     setResult]     = useState(null)    // { winner, left, right }
  const [avatarLeft,  setAvatarLeft]  = useState(null)   // 由 PR #556 avatarPortrait 提供
  const [nameLeft,    setNameLeft]    = useState(null)   // 由 N.E.K.O 主应用同步
  // 右侧对手数据来自匹配服务器
  const [playerId,    setPlayerId]    = useState(null)
  const [opponent,    setOpponent]    = useState(null)   // { nekoName, ownerName, avatar, bonds }
  const [matchStatus, setMatchStatus] = useState('idle') // idle | waiting | matched
  const [rematching, setRematching]  = useState(false)
  const [showReviewPanel, setShowReviewPanel] = useState(false)
  const [forgeSlots, setForgeSlots] = useState(() => pickUniqueForgeSlots())
  const [showForgePanel, setShowForgePanel] = useState(false)
  const [selectedForgeSlot, setSelectedForgeSlot] = useState(() => pickUniqueForgeSlots()[0].id)
  const [forging, setForging] = useState(false)
  const [forgedBondCard, setForgedBondCard] = useState(null)
  const [showForgeMachine, setShowForgeMachine] = useState(false)
  const [forgeMachineSlots, setForgeMachineSlots] = useState(() => pickUniqueForgeSlots())
  const [forgeMachineLoading, setForgeMachineLoading] = useState(false)
  const [forgeMachineNotice, setForgeMachineNotice] = useState('')
  const [forgeMachineSourceStatus, setForgeMachineSourceStatus] = useState('fallback')
  const [machinePhase, setMachinePhase] = useState('idle') // idle | confirming | burning | floating | storyGenerating | flipping | revealed
  const [machineStoryStatus, setMachineStoryStatus] = useState('')
  const [machinePickedId, setMachinePickedId] = useState(null)
  const [machineForgedCard, setMachineForgedCard] = useState(null)
  const [forgedInventory, setForgedInventory] = useState(() => loadForgedBrawlCards()) // 铸造完成的卡片仓库
  const [equippedBonds, setEquippedBonds] = useState([null, null, null, null, null]) // 左侧5个羁绊槽位装备的卡片
  const [bondMenuSlot, setBondMenuSlot] = useState(null) // 当前打开菜单的槽位 index (null=关闭)

  // 待机图片状态
  const [leftIdleIdx, setLeftIdleIdx] = useState(0)
  const [rightIdleIdx, setRightIdleIdx] = useState(() => Math.floor(Math.random() * WAITING_IDLES.length))
  const [showIdlePicker, setShowIdlePicker] = useState(false)
  const [showCardGame, setShowCardGame] = useState(false) // 猫娘大乱斗(卡牌)面板
  const [showDeckBuilder, setShowDeckBuilder] = useState(false) // 猫娘大乱斗战斗前组卡界面
  const [showDeckLibrary, setShowDeckLibrary] = useState(false) // 猫娘大乱斗卡组仓库界面
  const [temporaryBgmEnabled, setTemporaryBgmEnabled] = useState(true)
  const [cardGameSession, setCardGameSession] = useState(0)
  const [cardGameLoading, setCardGameLoading] = useState(false)
  const [cardGameLoadProgress, setCardGameLoadProgress] = useState(0)
  const [showDungeonPanel, setShowDungeonPanel] = useState(false) // 猫猫的地牢探险面板
  const [dungeonPhase, setDungeonPhase] = useState('idle') // idle, exploring, battling, event
  const [dungeonBonds, setDungeonBonds] = useState([null, null, null]) // 地牢探险中的3个羁绊槽
  const [dungeonBondMenuSlot, setDungeonBondMenuSlot] = useState(null) // 地牢探险中打开的羁绊槽菜单
  const [stamina, setStamina] = useState(100)
  const [mood, setMood] = useState(80)
  const [dungeonLog, setDungeonLog] = useState([]) // 探索/战斗文本日志
  const [currentEvent, setCurrentEvent] = useState(null) // 当前遭遇的事件

  const logEndRef = useRef(null)
  const hasForgedRef = useRef(false)
  const dungeonLogEndRef = useRef(null)
  const cardGameLoadDoneRef = useRef(false)

  const rightName = opponent?.nekoName || NEKO_RIGHT_DEFAULT.name
  const leftName = nameLeft || NEKO_LEFT.name
  const judgingFlavor = JUDGING_FLAVOR_LINES[logs.length % JUDGING_FLAVOR_LINES.length]
  const judgingLogPreview = logs.slice(-4)
  const selectedForgeEvent = forgeSlots.find(slot => slot.id === selectedForgeSlot) || forgeSlots[0]

  const openCardGame = useCallback(() => {
    if (cardGameLoading) return
    setShowCardGame(false)
    setCardGameSession(prev => prev + 1)
    setCardGameLoadProgress(0)
    setCardGameLoading(true)
  }, [cardGameLoading])

  const openDeckBuilder = useCallback(() => {
    if (cardGameLoading) return
    setShowDeckLibrary(false)
    setShowDeckBuilder(true)
  }, [cardGameLoading])

  const openDeckLibrary = useCallback(() => {
    if (cardGameLoading) return
    setShowDeckBuilder(false)
    setShowDeckLibrary(true)
  }, [cardGameLoading])

  const startCardGameFromDeckBuilder = useCallback(() => {
    setShowDeckBuilder(false)
    openCardGame()
  }, [openCardGame])

  const handleDeleteForgedCard = useCallback((card) => {
    if (!card) return
    setForgedInventory(prev => prev.filter(item => (
      item.id !== card.id &&
      item.code !== card.code
    )))
  }, [])

  useEffect(() => {
    if (!cardGameLoading) return

    cardGameLoadDoneRef.current = false
    const duration = 1050
    const startedAt = performance.now()
    let frameId = 0
    let revealTimer = 0

    const tick = (now) => {
      const raw = Math.min(1, (now - startedAt) / duration)
      const eased = raw < 0.72
        ? raw * 0.86
        : 0.62 + ((raw - 0.72) / 0.28) * 0.38
      const pct = Math.min(100, Math.round(eased * 100))

      setCardGameLoadProgress(pct)

      if (pct >= 100 && !cardGameLoadDoneRef.current) {
        cardGameLoadDoneRef.current = true
        setShowCardGame(true)
        revealTimer = window.setTimeout(() => {
          setCardGameLoading(false)
        }, 160)
        return
      }

      frameId = window.requestAnimationFrame(tick)
    }

    frameId = window.requestAnimationFrame(tick)

    return () => {
      window.cancelAnimationFrame(frameId)
      window.clearTimeout(revealTimer)
    }
  }, [cardGameLoading])

  const activeNekoBgmScene = cardGameLoading || showCardGame
    ? 'battle'
    : showDeckBuilder
      ? 'deckBuilder'
      : showDeckLibrary
        ? 'deckLibrary'
        : 'home'

  useEffect(() => {
    // 当前场景 BGM 是暂时占位实装用声音，不是最终结果；替换正式版 BGM 时请注明“正式版 BGM”。
    if (!temporaryBgmEnabled) {
      stopNekoBrawlBgm()
      return
    }
    playNekoBrawlSceneBgm(activeNekoBgmScene)
  }, [activeNekoBgmScene, temporaryBgmEnabled])

  useEffect(() => {
    return () => stopNekoBrawlBgm()
  }, [])

  const toggleTemporaryBgm = useCallback(() => {
    setTemporaryBgmEnabled(prev => !prev)
  }, [])

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
      // TODO: [铸造任务兜底持久化]
      // 当前前端只通过禁用关闭按钮避免普通点击打断，但无法防止刷新、强制关闭窗口、浏览器崩溃等情况。
      // 后续服务端化时，进入 storyGenerating 前应创建可恢复的 forge job，并记录事件引子、基础卡、token 调用状态和生成结果。
      // 即使用户强制关闭页面，只要 token 已消耗或故事已生成，也必须由后端兜底把成品卡写入组卡可见的卡牌收藏/仓库，避免“调用成功但卡丢失”。
      const rarity = rollForgeRarity()
      const [forgedCard] = await Promise.all([
        createForgedCardWithLlmStory(pickedSlot, pickedSlot.sourceCharacter || ''),
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
      setForgedInventory(prev => [
        ...prev,
        storedCard,
      ])
      setMachinePhase('flipping')
      await sleep(650)
      setMachinePhase('revealed')
    } else if (machinePhase === 'confirming') {
      setMachinePickedId(slotId)
      setMachineStoryStatus('')
    }
  }, [machinePhase, machinePickedId, forgeMachineSlots])

  const handleEquipBond = useCallback((slotIndex, card) => {
    setEquippedBonds(prev => {
      const next = [...prev]
      next[slotIndex] = card
      return next
    })
    setBondMenuSlot(null)
  }, [])

  const handleUnequipBond = useCallback((slotIndex) => {
    setEquippedBonds(prev => {
      const next = [...prev]
      next[slotIndex] = null
      return next
    })
  }, [])

  const loadForgeMachineSlots = useCallback(async () => {
    const qs = new URLSearchParams()
    // runtime_character_hint 来自 NEKO 本体同步的当前猫娘名，只用于运行态对齐；
    // 不再用旧 character 参数让前端任意覆盖记忆来源。
    if (!nameLeft) {
      return buildForgeMachineSlots([], {
        error: 'active_neko_runtime_not_linked',
        fallbackReason: 'runtime_character_hint_missing',
      })
    }
    qs.set('runtime_character_hint', nameLeft)
    // active facts 里的 absorbed 只表示已进入长期事实层，不代表归档；奇遇铸造机应读取当前 facts.json 的可用事实。
    qs.set('include_absorbed', 'true')
    // 铸造预览要优先保留当前猫娘全部可用 facts；不足 5 条时由前端临时事件补足，不按重要度提前过滤。
    qs.set('min_importance', '0')
    // 奇遇铸造机默认每轮抽取 5 条候选 fact；已铸造过的 fact id/hash 会排除，避免重复生成同一记忆来源。
    qs.set('limit', '5')
    const usedFactIds = forgedInventory.map(card => card.sourceFactId).filter(Boolean)
    const usedFactHashes = forgedInventory.map(card => card.sourceFactHash).filter(Boolean)
    if (usedFactIds.length > 0) qs.set('exclude_fact_ids', Array.from(new Set(usedFactIds)).join(','))
    if (usedFactHashes.length > 0) qs.set('exclude_hashes', Array.from(new Set(usedFactHashes)).join(','))
    try {
      const res = await fetch(`/arena/forge-facts?${qs.toString()}`)
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
  }, [forgedInventory, nameLeft])

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

  useEffect(() => {
    saveForgedBrawlCards(forgedInventory)
  }, [forgedInventory])

  const refreshForgeSlots = useCallback(() => {
    const nextSlots = pickUniqueForgeSlots()
    setForgeSlots(nextSlots)
    setSelectedForgeSlot(nextSlots[0].id)
    setForgedBondCard(null)
  }, [])

  const addLog = useCallback((type, message) => {
    setLogs(prev => [...prev, { id: Date.now() + Math.random(), type, message }])
  }, [])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  // ── PR #556 头像接入（仅左侧自身）─────────────────────────────────────────
  // Vite proxy: /battle-arena/avatar/left → localhost:48911
  useEffect(() => {
    let timer = null
    let lastLeftUrl = ''

    async function fetchMyAvatar() {
      try {
        const res = await fetch('/battle-arena/avatar/left')
        if (!res.ok) return
        const { dataUrl, name } = await res.json()
        if (dataUrl && dataUrl !== lastLeftUrl) {
          lastLeftUrl = dataUrl
          setAvatarLeft(dataUrl)
        }
        if (name) setNameLeft(name)
      } catch {
        // 主服务器未运行时静默忽略
      }
    }

    fetchMyAvatar()
    timer = setInterval(fetchMyAvatar, 5000)

    // postMessage 幁底（iframe 嵌入时由父页面推送）
    const onMessage = (event) => {
      const d = event.data
      if (!d || d.type !== 'neko-avatar' || d.side !== 'left') return
      if (d.dataUrl) { lastLeftUrl = d.dataUrl; setAvatarLeft(d.dataUrl) }
      if (d.name)   setNameLeft(d.name)
    }
    window.addEventListener('message', onMessage)

    return () => {
      clearInterval(timer)
      window.removeEventListener('message', onMessage)
    }
  }, [])

  // ── 匹配服务器：加入大乱斗 + 轮询对手 ───────────────────────────
  const playerIdRef    = useRef(null)
  const pollTimerRef  = useRef(null)
  const hasJoinedOnce  = useRef(false) // 防止 React StrictMode 双重 join

  const pollOpponent = useCallback(async () => {
    const id = playerIdRef.current
    if (!id) return
    try {
      const res = await fetch(`/arena/status/${id}`)
      if (!res.ok) return
      const { opponent: opp } = await res.json()
      if (opp) {
        setOpponent(opp)
        setMatchStatus('matched')
        clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
    } catch {
      // 匹配服务器未运行时静默容错
    }
  }, [])

  const joinArena = useCallback(async () => {
    try {
      setMatchStatus('waiting')
      const res = await fetch('/arena/join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // TODO: [羁绊列表接入] bonds 待替换为真实羁绊数据
        body: JSON.stringify({
          nekoName:  nameLeft  || NEKO_LEFT.name,
          ownerName: NEKO_LEFT.owner,
          avatar:    avatarLeft || null,
          bonds:     MY_BONDS_PLACEHOLDER, // TODO: 替换为真实羁绊列表
        }),
      })
      if (!res.ok) return
      const { playerId, opponent: opp } = await res.json()
      playerIdRef.current = playerId
      setPlayerId(playerId)
      if (opp) {
        setOpponent(opp)
        setMatchStatus('matched')
      } else {
        clearInterval(pollTimerRef.current)
        pollTimerRef.current = setInterval(pollOpponent, 2000)
      }
    } catch {
      // 匹配服务器未运行时静默容错
    }
  }, [avatarLeft, nameLeft, pollOpponent])

  const handleRematch = useCallback(async () => {
    if (rematching) return
    setRematching(true)
    setOpponent(null)
    setMatchStatus('waiting')

    clearInterval(pollTimerRef.current)
    pollTimerRef.current = null

    const oldId = playerIdRef.current
    playerIdRef.current = null
    setPlayerId(null)

    try {
      if (oldId) {
        await fetch(`/arena/leave/${oldId}`, { method: 'POST' })
      }
    } catch {
      // 忽略 leave 失败，继续重匹配测试
    }

    await sleep(1000)
    await joinArena()
    setRematching(false)
  }, [joinArena, rematching])

  useEffect(() => {
    // StrictMode 开发模式下 effect 会 mount→unmount→mount，
    // hasJoinedOnce 在 ref 中持久存在，确保只 join 一次
    if (hasJoinedOnce.current) return
    hasJoinedOnce.current = true

    joinArena()

    return () => {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
      const id = playerIdRef.current
      if (id) {
        playerIdRef.current = null
        fetch(`/arena/leave/${id}`, { method: 'POST' }).catch(() => {})
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── 模拟战斗流程 ────────────────────────────────────────────────────────────
  // TODO: 将来接入真实羁绊数据 + LLM 评委 API
  // 当前为 UI 动画演示，不含羁绊内容
  // ─────────────────────────────────────────────────────────────────────────────
  const handleBattle = async () => {
    if (battling || remaining <= 0) return
    setBattling(true)
    setResult(null)
    setLogs([])
    setScoreLeft(0)
    setScoreRight(0)

    setPhase('judging')
    addLog('system', '评鉴仪式开场，评委们已经端着茶杯入座。')
    await sleep(900)

    // 左侧评审
    setActiveSide('left')
    addLog('judge', `评委们正在围读 ${leftName} 的羁绊小作文，现场频频点头。`)
    await sleep(1100)
    const sl = Math.round(Math.random() * 60 + 120)   // 120-180
    setScoreLeft(sl)
    addLog('score', `${leftName} 先声夺人，偷偷拿下了 ${sl} 点心动值。`)
    await sleep(700)

    // 右侧评审
    setActiveSide('right')
    addLog('judge', `轮到 ${rightName} 登场，评委席已经有人悄悄抹眼泪了。`)
    await sleep(1100)
    const sr = Math.round(Math.random() * 60 + 120)
    setScoreRight(sr)
    addLog('score', `${rightName} 反手一击，现场收获了 ${sr} 点共鸣值。`)
    await sleep(700)

    // 结果
    setPhase('result')
    addLog('system', `红毯尽头传来最终结果：${leftName} ${sl} 分，对阵 ${rightName} ${sr} 分。`)
    await sleep(500)

    let winner
    if (sl > sr)       winner = 'left'
    else if (sr > sl)  winner = 'right'
    else               winner = 'draw'

    setResult({ winner, left: sl, right: sr, leftName, rightName })
    addLog('result',
      winner === 'draw'
        ? '双方谁也不服谁，评委们决定一起鼓掌算作平局。'
        : `${winner === 'left' ? leftName : rightName} 成功偷走了今晚评委席的心。`
    )

    setRemaining(p => p - 5)
    setActiveSide(null)
    setBattling(false)
    setPhase('idle')
  }

  const handleReset = () => {
    setScoreLeft(0)
    setScoreRight(0)
    setLogs([])
    setResult(null)
    setActiveSide(null)
    setBattling(false)
    setPhase('idle')
    setShowReviewPanel(false)
    setRightIdleIdx(Math.floor(Math.random() * WAITING_IDLES.length))
  }

  const handleForge = async () => {
    if (!selectedForgeEvent || forging) return
    setForging(true)
    setForgedBondCard(null)
    await sleep(900)
    const enchantment = FORGE_ENCHANTMENTS[Math.floor(Math.random() * FORGE_ENCHANTMENTS.length)]
    const attribute = FORGE_CARD_ATTRIBUTES[Math.floor(Math.random() * FORGE_CARD_ATTRIBUTES.length)]
    const rarity = rollForgeRarity()
    const forgedCard = await createForgedCardWithLlmStory(selectedForgeEvent, selectedForgeEvent.sourceCharacter || '')
    const displayCard = {
      ...forgedCard,
      id: `${selectedForgeEvent.id}-${Date.now()}`,
      title: `${selectedForgeEvent.name}（${attribute}）+${enchantment}`,
      baseName: selectedForgeEvent.name,
      summary: selectedForgeEvent.summary,
      rarity: rarity.name,
      rarityStyle: rarity.tagStyle,
      rarityFrame: rarity.frame,
      portal: '奇遇传送门',
      attribute,
      counterRule: FORGE_ATTRIBUTE_COUNTERS[attribute],
      enchantment,
    }
    const storedCard = {
      ...forgedCard,
      attribute,
      rarity: rarity.name,
      rarityStyle: rarity.tagStyle,
      rarityFrame: rarity.frame,
      enchantment,
      counterRule: FORGE_ATTRIBUTE_COUNTERS[attribute],
    }
    setForgedBondCard(displayCard)
    setForgedInventory(prev => [
      ...prev,
      storedCard,
    ])
    setForging(false)
  }

  // ── 排行榜图标 ───────────────────────────────────────────────────────────────
  const [showRanking, setShowRanking] = useState(false)
  const [rankingPos, setRankingPos] = useState({ top: 0, right: 0 })
  const rankingBtnRef = useRef(null)

  const updateRankingPos = useCallback(() => {
    if (rankingBtnRef.current) {
      const rect = rankingBtnRef.current.getBoundingClientRect()
      setRankingPos({
        top: rect.bottom + 8,
        right: window.innerWidth - rect.right
      })
    }
  }, [])

  useEffect(() => {
    if (showRanking) {
      updateRankingPos()
      window.addEventListener('resize', updateRankingPos)
      return () => window.removeEventListener('resize', updateRankingPos)
    }
  }, [showRanking, updateRankingPos])

  const rankIcon = (r) => {
    if (r === 1) return <Crown className="w-3.5 h-3.5 text-amber-400" />
    if (r <= 3)  return <Medal className="w-3.5 h-3.5 text-gray-400" />
    return <span className="text-[11px] text-gray-600 w-3.5 text-center">{r}</span>
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // 渲染
  // ─────────────────────────────────────────────────────────────────────────────@@
  return (
    <div className="relative h-screen flex flex-col overflow-hidden select-none bg-[#1b2430] isolate">

      <div
        className="absolute inset-0 bg-center bg-no-repeat bg-cover z-0"
        style={{ backgroundImage: 'url(/background_twisted.jpg)' }}
      />

      <div className="absolute inset-0 bg-black/15 z-0" />

      {/* ══════════════════════════════════════════════════════════════
          顶部标题栏 — Fixed 置顶风格（同底部滚动条）
      ══════════════════════════════════════════════════════════════ */}
      <div className="fixed top-0 left-0 right-0 z-50 h-12 bg-white border-b border-gray-200 flex items-center justify-between px-4">
        {/* 左侧：标题 */}
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-violet-500" />
          <h1 className="text-base font-black text-black tracking-wide">
            猫娘大乱斗
          </h1>
          <Sparkles className="w-4 h-4 text-pink-500" />
        </div>

        {/* 右侧功能区 */}
        <div className="flex items-center gap-2">
          {/* 猫娘大乱斗(beta) */}
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={toggleTemporaryBgm}
            className={`flex items-center gap-1 px-3 py-1.5 rounded-lg border text-xs transition-all ${
              temporaryBgmEnabled
                ? 'border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100'
                : 'border-zinc-300 bg-white text-zinc-500 hover:bg-zinc-100'
            }`}
            title="临时测试开关，后续会移除"
          >
            {temporaryBgmEnabled ? <Volume2 className="h-3.5 w-3.5" /> : <VolumeX className="h-3.5 w-3.5" />}
            <span>临时BGM：{temporaryBgmEnabled ? '开' : '关'}</span>
          </motion.button>

          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={openCardGame}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-700 hover:bg-amber-100 transition-all"
          >
            <span className="text-sm">🃏</span>
            <span>猫娘大乱斗</span>
          </motion.button>

          {/* 猫娘大乱斗 - 战斗前组卡 */}
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={openDeckBuilder}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-white border border-zinc-300 text-xs text-zinc-700 hover:bg-zinc-100 transition-all"
          >
            <span className="text-sm">▦</span>
            <span>组卡</span>
          </motion.button>

          {/* 猫娘大乱斗 - 卡组仓库 */}
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={openDeckLibrary}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-orange-50 border border-orange-200 text-xs text-orange-700 hover:bg-orange-100 transition-all"
          >
            <span className="text-sm">▣</span>
            <span>卡组仓库</span>
          </motion.button>

          {/* 猫猫的地牢探险(beta) */}
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => setShowDungeonPanel(true)}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-sky-50 border border-sky-200 text-xs text-sky-700 hover:bg-sky-100 transition-all"
          >
            <span className="text-sm">🐱</span>
            <span>猫猫的地牢探险</span>
          </motion.button>

          {/* 奇遇铸造机 */}
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={async () => {
              setMachinePhase('idle')
              setMachinePickedId(null)
              setMachineForgedCard(null)
              setMachineStoryStatus('')
              hasForgedRef.current = false
              setShowForgeMachine(true)
              await applyForgeMachineLoad()
            }}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-violet-50 border border-violet-200 text-xs text-violet-700 hover:bg-violet-100 transition-all"
          >
            <span className="text-sm">⚙️</span>
            <span>奇遇铸造机</span>
          </motion.button>

          {/* 临时保留的旧版加工台入口：旧链路只使用硬编码事件，不再暴露给正常流程。 */}
          {SHOW_LEGACY_FORGE_PANEL && (
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={() => {
                refreshForgeSlots()
                setShowForgePanel(true)
              }}
              className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-700 hover:bg-amber-100 transition-all"
            >
              <span className="text-sm">🔨</span>
              <span>奇遇加工台</span>
            </motion.button>
          )}

          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={handleRematch}
            disabled={rematching}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs transition-all ${
              rematching
                ? 'bg-gray-100 border-gray-200 text-gray-400 cursor-not-allowed'
                : 'bg-gray-50 border-gray-200 text-gray-700 hover:bg-gray-100'
            }`}
          >
            <RotateCcw className={`w-3.5 h-3.5 ${rematching ? 'text-gray-400' : 'text-violet-500'}`} />
            <span>{rematching ? '匹配中…' : '测试重匹配'}</span>
          </motion.button>

          {/* 排名按钮 + 悬停面板 */}
          <div 
            ref={rankingBtnRef}
            className="relative"
            onMouseEnter={() => { updateRankingPos(); setShowRanking(true); }}
            onMouseLeave={() => setShowRanking(false)}
          >
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200 text-xs text-gray-700 hover:bg-gray-100 transition-all"
            >
              <Trophy className="w-3.5 h-3.5 text-amber-500" />
              <span>全球排名</span>
              <BarChart3 className="w-3 h-3 text-gray-400" />
            </motion.button>

            {/* 悬停展开的面板 */}
            <AnimatePresence>
              {showRanking && (
                <motion.div
                  initial={{ opacity: 0, y: 8, scale: 0.95 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 8, scale: 0.95 }}
                  transition={{ duration: 0.2 }}
                  className="fixed w-64 glass-card p-3 shadow-2xl"
                  style={{ top: rankingPos.top, right: rankingPos.right }}
                >
                  <div className="flex items-center justify-between mb-3 pb-2 border-b border-white/10">
                    <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">全球排名</span>
                    <span className="text-[10px] text-amber-400/80">实时更新</span>
                  </div>
                  <div className="space-y-1.5 max-h-48 overflow-y-auto">
                    {RANKING.map((r, i) => (
                      <motion.div
                        key={r.rank}
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: i * 0.05 }}
                        className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-white/[0.03] hover:bg-white/[0.08] transition-colors"
                      >
                        {rankIcon(r.rank)}
                        <span className="flex-1 text-[11px] text-gray-400 truncate">{r.name}</span>
                        <span className="text-[10px] text-amber-400/50 font-medium">{r.score || '—'}</span>
                      </motion.div>
                    ))}
                  </div>
                  <div className="mt-3 pt-2 border-t border-white/10 text-center">
                    <button className="text-[10px] text-violet-400 hover:text-violet-300 flex items-center justify-center gap-0.5 w-full">
                      查看完整榜单 <ChevronRight className="w-3 h-3" />
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* 次数显示 */}
          <div className="flex items-center gap-1 text-xs text-gray-600">
            <Zap className="w-3.5 h-3.5 text-amber-500" />
            <span>剩余</span>
            <span className="text-amber-600 font-bold">{remaining}</span>
            <span>/ {MAX_DAILY}</span>
          </div>
        </div>
      </div>

      {/* ══════════════════════════════════════════════════════════════
          主体三栏 — 为顶部栏留出空间
      ══════════════════════════════════════════════════════════════ */}
      <main className="relative z-10 flex-1 flex gap-0 px-4 pb-2 pt-[60px] min-h-0">

        {/* ── 左侧猫娘卡 ───────────────────────────────────── */}
        <motion.div
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          className={`
            flex-1 bg-[#253240] border border-white/10 rounded-3xl p-4 lg:p-5 flex flex-col min-w-0 overflow-y-auto
            transition-all duration-500 mr-2
            ${activeSide === 'left' ? 'ring-1 ring-violet-500/40' : ''}
          `}
        >
          <NekoCard
            neko={{
              ...NEKO_LEFT,
              avatar: avatarLeft,
              ...(nameLeft ? { name: nameLeft } : {}),
              bonds: [null, null, null, null, null],
            }}
            side="left"
            isActive={activeSide === 'left'}
            score={scoreLeft}
            revealBonds
            equippedBonds={equippedBonds}
            bondMenuSlot={bondMenuSlot}
            onBondSlotClick={(i) => setBondMenuSlot(bondMenuSlot === i ? null : i)}
            onEquipBond={handleEquipBond}
            onUnequipBond={handleUnequipBond}
            forgedInventory={forgedInventory}
          />
        </motion.div>

        {/* ── 中间梯形战斗区 ────────────────────────────────── */}
        <div className="relative flex-shrink-0 w-[320px] lg:w-[380px] flex flex-col">

          {/*
            梯形背景 — clip-path 上窄下宽 (上宽10%收进, 下保持全宽)
            opacity 很低，保持"不太明显"的效果
          */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              clipPath: 'polygon(8% 0%, 92% 0%, 100% 100%, 0% 100%)',
              background: 'linear-gradient(to bottom, rgba(109,40,217,0.07), rgba(168,85,247,0.04))',
            }}
          />
          {/* 梯形边框描边（更淡） */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              clipPath: 'polygon(8% 0%, 92% 0%, 100% 100%, 0% 100%)',
              background: 'transparent',
              boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.04)',
            }}
          />

          {/* 内容区 */}
          <div className="relative z-10 flex flex-col h-full gap-4 px-3 py-3">

            {/* VS 标志 */}
            <div className="flex items-center justify-center pt-1">
              <motion.div
                animate={battling ? { scale: [1, 1.15, 1], rotate: [0, 4, -4, 0] } : {}}
                transition={{ duration: 0.7, repeat: battling ? Infinity : 0 }}
                className="relative"
              >
                <div className="w-14 h-14 rounded-full bg-gradient-to-br from-violet-600 to-pink-600
                                flex items-center justify-center shadow-lg shadow-violet-900/50">
                  <span className="text-white font-black text-lg tracking-tight">VS</span>
                </div>
                {battling && (
                  <div className="absolute inset-0 rounded-full bg-violet-500/30 animate-ping" />
                )}
              </motion.div>
            </div>

            {/* 评分对比 */}
            <div className="glass-card px-3 py-2 flex items-center justify-between gap-2">
              <span className={`text-2xl font-black transition-all duration-700 ${
                result?.winner === 'left' ? 'text-amber-400' : 'text-gray-300'
              }`}>{scoreLeft}</span>
              <span className="text-[10px] text-gray-600 uppercase tracking-widest">分数</span>
              <span className={`text-2xl font-black transition-all duration-700 ${
                result?.winner === 'right' ? 'text-amber-400' : 'text-gray-300'
              }`}>{scoreRight}</span>
            </div>

            {/* 中央状态区（待机 / 战斗结果）常驻显示 */}
            <div className="w-full rounded-3xl border border-white/10 bg-slate-950/55 p-3 relative">
              <div className="mb-2">
                <p className="text-xs uppercase tracking-[0.24em] text-violet-300/80">
                  {result ? '本场结果' : '准备就绪'}
                </p>
                <p className="mt-1 text-sm font-bold text-white">
                  {result
                    ? result.winner === 'draw'
                      ? '双方打成平局，今晚的评委席很难做。'
                      : `${result.winner === 'left' ? result.leftName : result.rightName} 赢下本场对决。`
                    : '等待对手… 随时可以开始评鉴。'}
                </p>
              </div>

              <div className="grid grid-cols-2 gap-3 relative">
                {/* 左侧图片：闲置时可点击切换 */}
                <div className="relative">
                  <div
                    className={`relative overflow-hidden rounded-2xl border border-white/10 bg-slate-900/70 p-2 transition-all ${
                      !result && !battling ? 'hover:border-violet-400/50 hover:bg-slate-800/80' : ''
                    }`}
                  >
                    <div className="flex h-32 items-center justify-center overflow-hidden rounded-xl bg-slate-950/80">
                      <img
                        src={result ? (result.winner === 'left' || result.winner === 'draw' ? '/celebration.gif' : '/cry.gif') : WAITING_IDLES[leftIdleIdx]}
                        alt={result ? '左侧结果' : '左侧待机'}
                        className="block h-full max-w-full object-contain"
                        style={{ transform: 'translateZ(0)', backfaceVisibility: 'hidden' }}
                      />
                    </div>
                    {!result && !battling && (
                      <button
                        type="button"
                        onClick={() => setShowIdlePicker(!showIdlePicker)}
                        className="absolute inset-0 z-10 cursor-pointer appearance-none rounded-2xl bg-transparent"
                        aria-label="切换左侧待机图"
                      />
                    )}
                  </div>

                  {/* 弹出图片选择列表 */}
                  <AnimatePresence>
                    {showIdlePicker && !result && !battling && (
                      <motion.div
                        initial={{ opacity: 0, y: 5, scale: 0.95 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: 5, scale: 0.95 }}
                        className="absolute top-[102%] left-0 z-50 grid w-[200%] grid-cols-4 gap-2 rounded-2xl border border-white/15 bg-slate-900 p-2 shadow-2xl"
                      >
                        {WAITING_IDLES.map((src, idx) => (
                          <button
                            key={src}
                            onClick={() => { setLeftIdleIdx(idx); setShowIdlePicker(false); }}
                            className={`overflow-hidden rounded-xl border-2 transition-all ${
                              leftIdleIdx === idx ? 'border-violet-500' : 'border-transparent hover:border-white/20'
                            }`}
                          >
                            <div className="flex h-16 w-16 items-center justify-center bg-slate-950/80">
                              <img
                                src={src}
                                alt="待机图"
                                className="block h-full w-full object-contain"
                                style={{ transform: 'translateZ(0)', backfaceVisibility: 'hidden' }}
                              />
                            </div>
                          </button>
                        ))}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>

                {/* 右侧图片：闲置时随机，不交互 */}
                <div className="overflow-hidden rounded-2xl border border-white/10 bg-slate-900/70 p-2">
                  <div className="flex h-32 items-center justify-center overflow-hidden rounded-xl bg-slate-950/80">
                    <img
                      src={result ? (result.winner === 'right' || result.winner === 'draw' ? '/celebration.gif' : '/cry.gif') : WAITING_IDLES[rightIdleIdx]}
                      alt={result ? '右侧结果' : '右侧待机'}
                      className="block h-full max-w-full object-contain"
                      style={{ transform: 'translateZ(0)', backfaceVisibility: 'hidden' }}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* 战斗按钮 */}
            <div className="flex gap-2">
              <motion.button
                whileHover={!battling && remaining > 0 ? { scale: 1.03 } : {}}
                whileTap={!battling  && remaining > 0 ? { scale: 0.97 } : {}}
                onClick={handleBattle}
                disabled={battling || remaining <= 0}
                className={`flex-1 py-2.5 rounded-xl text-sm font-bold flex items-center
                            justify-center gap-2 transition-all duration-300 ${
                  !battling && remaining > 0
                    ? 'bg-gradient-to-r from-violet-600 to-pink-600 text-white shadow-lg shadow-violet-900/40 hover:shadow-violet-900/60'
                    : 'bg-white/5 text-gray-600 cursor-not-allowed'
                }`}
              >
                {battling ? (
                  <motion.span
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1.2, repeat: Infinity, ease: 'linear' }}
                  >
                    <Star className="w-4 h-4" />
                  </motion.span>
                ) : (
                  <Sparkles className="w-4 h-4" />
                )}
                {battling
                  ? phase === 'judging' ? '评审中…' : '处理中…'
                  : '开始评鉴'}
              </motion.button>

              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                onClick={handleReset}
                className="px-3 py-2.5 rounded-xl bg-white/5 border border-white/[0.07]
                           text-gray-500 hover:text-gray-300 transition-colors"
              >
                <RotateCcw className="w-4 h-4" />
              </motion.button>
            </div>

            {/* 评审日志 — 可点击查看完整记录 */}
            <button
              type="button"
              onClick={() => setShowReviewPanel(true)}
              className="flex-1 glass-card p-3 min-h-0 overflow-hidden flex flex-col text-left transition-all hover:border-violet-400/30 hover:bg-white/[0.08]"
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[11px] font-semibold uppercase tracking-widest text-violet-300/80">评审日志</span>
                <span className="flex items-center gap-1 text-[11px] text-gray-400">
                  点击查看 <ChevronRight className="h-3.5 w-3.5" />
                </span>
              </div>
              <BattleLog logs={logs} />
              <div ref={logEndRef} />
            </button>

            {/* 评委席装饰 */}
            <div className="glass-card p-3 flex-shrink-0">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest">
                  评委席状态
                </span>
                <span className={`text-[11px] ${battling ? 'text-emerald-400 animate-pulse' : 'text-gray-600'}`}>
                  {battling ? '评审中...' : '待机'}
                </span>
              </div>
              <div className="flex gap-1.5">
                {['GPT-4', 'Claude', 'Gemini', 'Kimi'].map((judge, i) => (
                  <motion.div
                    key={judge}
                    animate={battling ? {
                      scale: [1, 1.1, 1],
                      opacity: [0.5, 1, 0.5]
                    } : {}}
                    transition={{ duration: 1.5, delay: i * 0.2, repeat: battling ? Infinity : 0 }}
                    className={`flex-1 py-1.5 rounded-lg text-[10px] text-center font-medium
                      ${battling ? 'bg-violet-500/20 text-violet-300' : 'bg-white/[0.03] text-gray-600'}`}
                  >
                    {judge}
                  </motion.div>
                ))}
              </div>
            </div>

            {/* 羁绊共鸣度 */}
            <div className="glass-card p-3 flex-shrink-0">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest">
                  羁绊共鸣度
                </span>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-600 w-8">情感</span>
                  <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <motion.div
                      className="h-full bg-gradient-to-r from-violet-500 to-pink-500 rounded-full"
                      initial={{ width: '30%' }}
                      animate={{ width: battling ? '70%' : '30%' }}
                      transition={{ duration: 2 }}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-600 w-8">回忆</span>
                  <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <motion.div
                      className="h-full bg-gradient-to-r from-pink-500 to-rose-500 rounded-full"
                      initial={{ width: '45%' }}
                      animate={{ width: battling ? '85%' : '45%' }}
                      transition={{ duration: 2, delay: 0.3 }}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-600 w-8">默契</span>
                  <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <motion.div
                      className="h-full bg-gradient-to-r from-amber-500 to-orange-500 rounded-full"
                      initial={{ width: '25%' }}
                      animate={{ width: battling ? '60%' : '25%' }}
                      transition={{ duration: 2, delay: 0.6 }}
                    />
                  </div>
                </div>
              </div>
            </div>

          </div>
        </div>

        {/* ── 右侧对手卡（来自匹配服务器） ────────────────── */}
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          className={`
            flex-1 bg-[#253240] border border-white/10 rounded-3xl p-4 lg:p-5 flex flex-col min-w-0 overflow-y-auto
            transition-all duration-500 ml-2
            ${activeSide === 'right' ? 'ring-1 ring-pink-500/40' : ''}
            ${matchStatus === 'waiting' ? 'opacity-60' : ''}
          `}
        >
          <NekoCard
            neko={{
              ...NEKO_RIGHT_DEFAULT,
              ...(opponent ? {
                name:   opponent.nekoName,
                owner:  opponent.ownerName,
                avatar: opponent.avatar,
                bonds:  opponent.bonds, // TODO: 替换为真实羁绊列表
              } : {}),
            }}
            side="right"
            isActive={activeSide === 'right'}
            score={scoreRight}
            revealBonds={false}
          />
        </motion.div>

      </main>

      {/* ══════════════════════════════════════════════════════════════
          底部滚动信息条
      ══════════════════════════════════════════════════════════════ */}
      <BottomTicker />

      <AnimatePresence>
        {phase === 'judging' && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-40 flex items-center justify-center bg-black/35 px-4"
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.92, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.92, y: 16 }}
              transition={{ duration: 0.22 }}
              className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-3xl border border-white/15 bg-[#1f2937] shadow-2xl"
            >
              <div className="flex-shrink-0 overflow-hidden border-b border-white/10 bg-black/20">
                <img
                  src="/Simple_design_judging.gif"
                  alt="评委评判中"
                  className="block h-auto max-h-[320px] w-full object-contain"
                />
              </div>

              <div className="min-h-0 overflow-y-auto px-5 pb-5 pt-5">
                <div className="text-center">
                  <h3 className="text-2xl font-black text-white">评委评判中</h3>
                  <p className="mt-3 text-lg font-semibold text-pink-300">{judgingFlavor}</p>
                  <p className="mx-auto mt-3 max-w-2xl text-sm leading-6 text-gray-300">
                    正在偷偷瞄战斗结果，顺便假装严肃地翻阅双方羁绊档案。
                    请保持安静，别打扰评委席的小动作。
                  </p>
                </div>

                <div className="mt-5 rounded-2xl border border-white/10 bg-slate-950/50 p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="text-base font-bold text-white">点评记录摘要</h3>
                    <span className="text-xs text-violet-300">评委席窃窃私语中</span>
                  </div>
                  <div className="grid gap-2 md:grid-cols-2">
                    {judgingLogPreview.map((log) => (
                      <div key={log.id} className="rounded-xl border border-white/8 bg-white/5 px-3 py-2 text-sm text-gray-200">
                        {log.message}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showReviewPanel && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-40 flex items-center justify-center bg-black/50 px-4"
            onClick={() => setShowReviewPanel(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 16 }}
              transition={{ duration: 0.22 }}
              className="w-full max-w-3xl rounded-3xl border border-white/15 bg-[#1f2937] p-5 shadow-2xl"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-xl font-black text-white">完整评审记录</h3>
                  <p className="mt-1 text-sm text-gray-300">这里可以回看这一场的完整评语与过程记录。</p>
                </div>
                <button
                  type="button"
                  onClick={() => setShowReviewPanel(false)}
                  className="rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-gray-300 transition-colors hover:bg-white/10 hover:text-white"
                >
                  关闭
                </button>
              </div>

              <div className="max-h-[60vh] space-y-2 overflow-y-auto rounded-2xl border border-white/10 bg-slate-950/45 p-3">
                {logs.map((log) => (
                  <div key={log.id} className="rounded-xl border border-white/8 bg-white/5 px-3 py-2 text-sm text-gray-200">
                    {log.message}
                  </div>
                ))}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

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
                    title="测试用重Roll入口，后期会删除"
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
                    重Roll记忆（测试）
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

              {/* 卡片区：floating/storyGenerating/flipping/revealed 阶段只显示选中卡 */}
              {(machinePhase === 'floating' || machinePhase === 'storyGenerating' || machinePhase === 'flipping' || machinePhase === 'revealed') ? (
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
                        {machineForgedCard?.name || forgeMachineSlots.find(s => s.id === machinePickedId)?.name}
                      </p>
                      <p className="text-[10px] text-gray-400 text-center mt-2 leading-relaxed">
                        {machinePhase === 'storyGenerating'
                          ? (machineStoryStatus || '正在等待故事生成完成…')
                          : (machineForgedCard?.story || forgeMachineSlots.find(s => s.id === machinePickedId)?.summary)}
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
                        原始引子：{forgeMachineSlots.find(s => s.id === machinePickedId)?.storyLead || forgeMachineSlots.find(s => s.id === machinePickedId)?.summary}
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
                        <p className="mt-2 text-sm text-gray-300">恭喜！此卡片已收录</p>
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
              ) : forgeMachineLoading ? (
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
                          {/* TODO: 临时记忆事件调试戳，仅用于确认 facts 抽取日期和重要性；抽取规则稳定后删除。 */}
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

      <AnimatePresence>
        {SHOW_LEGACY_FORGE_PANEL && showForgePanel && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-40 flex items-center justify-center bg-black/50 px-4"
            onClick={() => setShowForgePanel(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 16 }}
              transition={{ duration: 0.22 }}
              className="flex w-full max-w-6xl overflow-hidden rounded-3xl border border-white/15 bg-[#1f2937] shadow-2xl"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="w-[280px] flex-shrink-0 border-r border-white/10 bg-slate-950/45 p-4 flex flex-col">
                <h3 className="text-lg font-black text-white mb-3">奇遇加工台</h3>
                <div className="border border-white/10 rounded-lg overflow-hidden flex-1">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="bg-white/5 border-b border-white/10">
                        <th className="px-3 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">#</th>
                        <th className="px-3 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">羁绊事件</th>
                      </tr>
                    </thead>
                    <tbody>
                      {forgeSlots.map((slot, index) => (
                        <tr
                          key={slot.id}
                          onClick={() => setSelectedForgeSlot(slot.id)}
                          className={`cursor-pointer border-b border-white/5 last:border-0 transition-all ${
                            selectedForgeSlot === slot.id
                              ? 'bg-amber-500/20'
                              : 'hover:bg-white/5'
                          }`}
                        >
                          <td className="px-3 py-2 text-xs text-gray-400">{index + 1}</td>
                          <td className="px-3 py-2">
                            <p className="text-sm text-white font-medium">{slot.name}</p>
                            <p className="text-[10px] text-gray-400 leading-tight mt-0.5">{slot.summary.slice(0, 20)}...</p>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <button
                  type="button"
                  onClick={refreshForgeSlots}
                  className="mt-3 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs font-bold text-gray-200 transition-colors hover:bg-white/10"
                >
                  重新抽取 5 个事件
                </button>
              </div>

              <div className="w-[280px] flex-shrink-0 p-4 flex flex-col">
                <div className="flex items-center justify-end mb-3">
                  <button
                    type="button"
                    onClick={() => setShowForgePanel(false)}
                    className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-gray-300 transition-colors hover:bg-white/10 hover:text-white"
                  >
                    关闭
                  </button>
                </div>

                <div className="flex-1 flex flex-col gap-3">
                  <div className="rounded-xl border border-white/10 bg-slate-950/45 p-3">
                    <p className="text-xs text-gray-400 mb-1">已选事件</p>
                    <p className="text-base font-bold text-white">{selectedForgeEvent.name}</p>
                    <p className="text-xs text-gray-300 mt-1 leading-relaxed">{selectedForgeEvent.summary}</p>
                  </div>

                  <motion.button
                    whileHover={!forging ? { scale: 1.02 } : {}}
                    whileTap={!forging ? { scale: 0.98 } : {}}
                    type="button"
                    onClick={handleForge}
                    disabled={forging}
                    className={`w-full rounded-xl px-4 py-3 text-sm font-bold transition-all ${
                      forging
                        ? 'bg-white/5 text-gray-500 cursor-not-allowed'
                        : 'bg-gradient-to-r from-amber-500 to-pink-500 text-white shadow-lg shadow-amber-900/30'
                    }`}
                  >
                    {forging ? '附魔中…' : '投入奇遇传送门'}
                  </motion.button>

                  {forgedBondCard ? (
                    <div className={`flex-1 rounded-xl border bg-gradient-to-br from-amber-500/10 via-violet-500/10 to-pink-500/10 p-4 shadow-lg ${forgedBondCard.rarityFrame}`}>
                      <div className="flex items-center justify-between mb-2">
                        <span className="rounded-full border border-amber-300/30 bg-amber-400/10 px-2 py-0.5 text-[10px] font-semibold text-amber-200">附魔完成</span>
                        <span className={`text-xs font-bold ${FORGE_ATTRIBUTE_STYLES[forgedBondCard.attribute].badge}`}>{forgedBondCard.attribute}</span>
                      </div>
                      <h4 className={`text-lg font-black ${FORGE_ATTRIBUTE_STYLES[forgedBondCard.attribute].title} mb-2`}>{forgedBondCard.title}</h4>
                      <p className="text-xs text-gray-400 mb-2">{forgedBondCard.summary}</p>
                      <div className="flex flex-wrap gap-1.5 mb-2">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] border ${forgedBondCard.rarityStyle}`}>{forgedBondCard.rarity}</span>
                        <span className="rounded-full border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-200">{forgedBondCard.enchantment}</span>
                      </div>
                      <div className="rounded-lg border border-white/10 bg-black/20 p-2">
                        <p className="text-[10px] text-gray-400">克制：{forgedBondCard.counterRule}</p>
                      </div>
                    </div>
                  ) : (
                    <div className="flex-1 rounded-xl border border-dashed border-white/10 bg-white/[0.03] p-4 flex flex-col items-center justify-center text-center">
                      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-violet-500/10 text-2xl">🌀</div>
                      <p className="mt-2 text-xs text-gray-400">等待附魔</p>
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ══════════════════════════════════════════════════════════════
          猫娘大乱斗(beta) — 卡牌对战面板
      ══════════════════════════════════════════════════════════════ */}
      <AnimatePresence>
        {showDeckBuilder && (
          <DeckBuilderPanel
            onClose={() => {
              setShowDeckBuilder(false)
            }}
            onStartBattle={startCardGameFromDeckBuilder}
            onOpenDeckLibrary={openDeckLibrary}
            onDeleteForgedCard={handleDeleteForgedCard}
            forgedCards={forgedInventory}
            temporaryBgmEnabled={temporaryBgmEnabled}
            onToggleTemporaryBgm={toggleTemporaryBgm}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showDeckLibrary && (
          <DeckLibraryPanel
            onClose={() => {
              setShowDeckLibrary(false)
            }}
            onOpenDeckBuilder={openDeckBuilder}
            forgedCards={forgedInventory}
            temporaryBgmEnabled={temporaryBgmEnabled}
            onToggleTemporaryBgm={toggleTemporaryBgm}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showCardGame && (
          <CardGamePanel
            key={cardGameSession}
            onClose={() => {
              setShowCardGame(false)
            }}
            nekoName={nameLeft || NEKO_LEFT.name}
            nekoAvatar={avatarLeft}
            temporaryBgmEnabled={temporaryBgmEnabled}
            onToggleTemporaryBgm={toggleTemporaryBgm}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {cardGameLoading && (
          <motion.div
            key="card-game-loading"
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '-105%' }}
            transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            className="fixed inset-0 z-[140] flex h-screen w-screen items-center justify-center overflow-hidden bg-black text-white"
          >
            <div
              className="absolute inset-0 bg-center bg-cover"
              style={{ backgroundImage: 'url(/Simple_design_judging.gif)' }}
            />
            <div className="absolute inset-0 bg-black/45" />
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(125,211,252,0.18),transparent_36%)]" />

            <motion.div
              initial={{ opacity: 0, y: 24, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ delay: 0.08, duration: 0.18 }}
              className="relative z-10 flex w-[min(520px,82vw)] flex-col items-center"
            >
              <div className="mb-5 rounded-md border border-white/20 bg-black/35 px-4 py-2 backdrop-blur">
                <p className="text-[11px] font-bold uppercase tracking-[0.36em] text-sky-100">Neko Battle Arena</p>
              </div>
              <h2 className="text-4xl font-black tracking-[0.18em] text-white drop-shadow-[0_8px_24px_rgba(0,0,0,0.65)]">
                LOADING
              </h2>
              <p className="mt-3 text-xs font-bold tracking-[0.28em] text-white/70">
                {cardGameLoadProgress}%
              </p>

              <div className="mt-7 h-3 w-full overflow-hidden rounded-full border border-white/20 bg-black/35 shadow-2xl backdrop-blur">
                <motion.div
                  className="h-full rounded-full bg-gradient-to-r from-sky-300 via-violet-300 to-pink-300"
                  animate={{ width: `${cardGameLoadProgress}%` }}
                  transition={{ duration: 0.12, ease: 'linear' }}
                />
              </div>
              <div className="mt-3 flex w-full items-center justify-between text-[10px] font-bold uppercase tracking-[0.18em] text-white/55">
                <span>Preparing Cards</span>
                <span>Entering Arena</span>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ══════════════════════════════════════════════════════════════
          猫猫的地牢探险(beta)面板
      ══════════════════════════════════════════════════════════════ */}
      <AnimatePresence>
        {showDungeonPanel && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-40 flex items-center justify-center bg-black/60 px-4"
            onClick={() => setShowDungeonPanel(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 16 }}
              transition={{ duration: 0.22 }}
              className="w-full max-w-6xl h-[85vh] overflow-hidden rounded-3xl border border-white/15 bg-[#1a2332] shadow-2xl"
              onClick={(event) => event.stopPropagation()}
            >
              {/* 顶部标题栏 */}
              <div className="flex items-center justify-between px-5 py-3 border-b border-white/10 bg-black/20">
                <div className="flex items-center gap-2">
                  <span className="text-xl">🐱</span>
                  <h3 className="text-lg font-black text-white">猫猫的地牢探险</h3>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-sky-500/20 text-sky-300 border border-sky-400/30">Beta</span>
                </div>
                <button
                  type="button"
                  onClick={() => setShowDungeonPanel(false)}
                  className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-gray-300 transition-colors hover:bg-white/10 hover:text-white"
                >
                  关闭
                </button>
              </div>

              {/* 主体三区布局 */}
              <div className="flex h-[calc(85vh-60px)]">
                {/* ════════════════════════════════════════════════════════════════
                    左侧 B 区：头像 + 体力/心情 + 3个羁绊槽
                ════════════════════════════════════════════════════════════════ */}
                <div className="w-[300px] flex-shrink-0 border-r border-white/10 flex flex-col">
                  {/* B-上：头像 3/4 + 体力/心情 1/4 */}
                  <div className="flex-1 p-4 flex gap-3">
                    {/* 头像区 3/4 */}
                    <div className="flex-[3] flex flex-col items-center justify-center">
                      <div className="w-full aspect-square rounded-2xl border border-violet-400/30 bg-violet-500/10 flex items-center justify-center overflow-hidden">
                        {avatarLeft ? (
                          <img src={avatarLeft} alt="猫娘" className="w-full h-full object-cover" />
                        ) : (
                          <div className="text-6xl">🐱</div>
                        )}
                      </div>
                      <p className="mt-2 text-sm font-bold text-white">{leftName}</p>
                    </div>
                    {/* 体力/心情区 1/4 */}
                    <div className="flex-1 flex flex-col gap-2 justify-center">
                      <div className="rounded-xl border border-red-400/30 bg-red-500/10 p-2 text-center">
                        <p className="text-[10px] text-red-300/70 mb-1">体力</p>
                        <p className="text-lg font-bold text-red-300">{stamina}</p>
                      </div>
                      <div className="rounded-xl border border-pink-400/30 bg-pink-500/10 p-2 text-center">
                        <p className="text-[10px] text-pink-300/70 mb-1">心情</p>
                        <p className="text-lg font-bold text-pink-300">{mood}</p>
                      </div>
                    </div>
                  </div>

                  {/* B-下：3个横向羁绊槽 */}
                  <div className="p-4 border-t border-white/10 bg-black/10">
                    <p className="text-xs font-semibold text-gray-400 mb-3 flex items-center gap-1">
                      <Sparkles className="w-3 h-3 text-violet-400" />
                      探险羁绊
                    </p>
                    <div className="space-y-2">
                      {[0, 1, 2].map(i => {
                        const equipped = dungeonBonds[i]
                        const isMenuOpen = dungeonBondMenuSlot === i
                        return (
                          <div key={i} className="relative">
                            <div
                              onClick={() => setDungeonBondMenuSlot(dungeonBondMenuSlot === i ? null : i)}
                              className={`rounded-xl border px-3 py-2 min-h-[44px] flex items-center cursor-pointer transition-all
                                ${equipped
                                  ? 'border-violet-500/30 bg-violet-500/[0.06]'
                                  : 'border-dashed border-white/[0.07] bg-white/[0.015] hover:bg-violet-500/[0.08]'
                                }
                                ${isMenuOpen ? 'ring-1 ring-violet-400/40' : ''}
                              `}
                            >
                              {equipped ? (
                                <div className="flex items-center justify-between w-full gap-2">
                                  <span className="text-[11px] leading-snug font-medium truncate text-violet-300/90">
                                    {equipped.name}
                                  </span>
                                  <span className={`text-[9px] px-1.5 py-0.5 rounded-full border flex-shrink-0 ${equipped.rarityStyle || 'border-white/10 text-gray-400'}`}>
                                    {equipped.rarity || ''}
                                  </span>
                                </div>
                              ) : (
                                <span className="text-[11px] w-full text-center text-gray-600">
                                  槽位 #{i + 1}
                                </span>
                              )}
                            </div>

                            {isMenuOpen && (
                              <div className="absolute left-0 right-0 bottom-full mb-1 z-30 rounded-xl border border-violet-400/30 bg-[#1a2332] shadow-xl max-h-[200px] overflow-y-auto">
                                <div className="px-3 py-2 border-b border-white/10 flex items-center justify-between">
                                  <span className="text-[10px] text-gray-400 font-semibold">选择羁绊卡片</span>
                                  {equipped && (
                                    <button
                                      onClick={(e) => { e.stopPropagation(); setDungeonBonds(prev => { const next = [...prev]; next[i] = null; return next; }) }}
                                      className="text-[10px] text-red-400 hover:text-red-300"
                                    >
                                      卸下
                                    </button>
                                  )}
                                </div>
                                {forgedInventory.length === 0 ? (
                                  <div className="px-3 py-4 text-center">
                                    <p className="text-[10px] text-gray-500">暂无铸造卡片</p>
                                    <p className="text-[9px] text-gray-600 mt-1">前往铸造机获取</p>
                                  </div>
                                ) : (
                                  forgedInventory.map(card => {
                                    const alreadyEquipped = dungeonBonds.some((eb, idx) => eb && eb.id === card.id && idx !== i)
                                    return (
                                      <div
                                        key={card.id}
                                        onClick={(e) => {
                                          e.stopPropagation()
                                          if (!alreadyEquipped) {
                                            setDungeonBonds(prev => { const next = [...prev]; next[i] = card; return next; })
                                            setDungeonBondMenuSlot(null)
                                          }
                                        }}
                                        className={`px-3 py-2 border-b border-white/5 last:border-0 transition-colors
                                          ${alreadyEquipped
                                            ? 'opacity-30 cursor-not-allowed'
                                            : 'hover:bg-violet-500/10 cursor-pointer'
                                          }`}
                                      >
                                        <div className="flex items-center justify-between gap-2">
                                          <span className="text-[11px] text-white font-medium truncate">{card.name}</span>
                                          <span className={`text-[9px] px-1.5 py-0.5 rounded-full border flex-shrink-0 ${card.rarityStyle || 'border-white/10 text-gray-400'}`}>
                                            {card.rarity}
                                          </span>
                                        </div>
                                        <span className="text-[9px] text-gray-500">{card.attribute}</span>
                                      </div>
                                    )
                                  })
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </div>

                {/* ════════════════════════════════════════════════════════════════
                    中间：探索文本 / 战斗文本
                ════════════════════════════════════════════════════════════════ */}
                <div className="flex-1 flex flex-col border-r border-white/10">
                  {/* 操作按钮区 */}
                  <div className="p-4 border-b border-white/10 flex gap-2">
                    <motion.button
                      whileHover={{ scale: 1.02 }}
                      whileTap={{ scale: 0.98 }}
                      onClick={() => {
                        setDungeonPhase('exploring')
                        setDungeonLog(prev => [...prev, { type: 'explore', text: '🐾 你踏入了地牢的第一层，空气中弥漫着神秘的气息...' }])
                      }}
                      disabled={dungeonPhase === 'exploring' || dungeonPhase === 'battling'}
                      className={`flex-1 py-2 rounded-xl text-sm font-bold transition-all ${
                        dungeonPhase === 'exploring' || dungeonPhase === 'battling'
                          ? 'bg-white/5 text-gray-500 cursor-not-allowed'
                          : 'bg-gradient-to-r from-sky-500 to-violet-500 text-white shadow-lg shadow-sky-900/30 hover:shadow-sky-900/50'
                      }`}
                    >
                      {dungeonPhase === 'exploring' ? '探索中...' : '🗺️ 开始探索'}
                    </motion.button>
                    <motion.button
                      whileHover={{ scale: 1.02 }}
                      whileTap={{ scale: 0.98 }}
                      onClick={() => {
                        setDungeonPhase('battling')
                        setCurrentEvent({ type: 'combat', name: '史莱姆', hp: 50, maxHp: 50 })
                        setDungeonLog(prev => [...prev, { type: 'battle', text: '⚔️ 遭遇战斗！一只史莱姆挡住了去路！' }])
                      }}
                      disabled={dungeonPhase !== 'exploring'}
                      className={`flex-1 py-2 rounded-xl text-sm font-bold transition-all ${
                        dungeonPhase !== 'exploring'
                          ? 'bg-white/5 text-gray-500 cursor-not-allowed'
                          : 'bg-gradient-to-r from-rose-500 to-amber-500 text-white shadow-lg shadow-rose-900/30 hover:shadow-rose-900/50'
                      }`}
                    >
                      ⚔️ 遭遇战斗
                    </motion.button>
                  </div>

                  {/* 日志文本区 */}
                  <div className="flex-1 p-4 overflow-y-auto">
                    <div className="space-y-3">
                      {dungeonLog.length === 0 ? (
                        <div className="h-full flex flex-col items-center justify-center text-gray-500">
                          <span className="text-4xl mb-3">🏰</span>
                          <p className="text-sm">准备开始你的地牢探险...</p>
                          <p className="text-xs mt-1 text-gray-600">点击"开始探索"进入地牢</p>
                        </div>
                      ) : (
                        dungeonLog.map((log, idx) => (
                          <motion.div
                            key={idx}
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            className={`rounded-xl border p-3 ${
                              log.type === 'battle'
                                ? 'border-rose-500/20 bg-rose-500/5'
                                : log.type === 'event'
                                ? 'border-amber-500/20 bg-amber-500/5'
                                : 'border-sky-500/20 bg-sky-500/5'
                            }`}
                          >
                            <p className={`text-sm ${
                              log.type === 'battle' ? 'text-rose-200' : log.type === 'event' ? 'text-amber-200' : 'text-sky-200'
                            }`}>
                              {log.text}
                            </p>
                          </motion.div>
                        ))
                      )}
                      <div ref={dungeonLogEndRef} />
                    </div>
                  </div>
                </div>

                {/* ════════════════════════════════════════════════════════════════
                    右侧：事件详情 / 敌方状态栏
                ════════════════════════════════════════════════════════════════ */}
                <div className="w-[280px] flex-shrink-0 p-4">
                  {currentEvent ? (
                    currentEvent.type === 'combat' ? (
                      <div className="h-full flex flex-col">
                        <h4 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-rose-500 animate-pulse" />
                          敌方状态
                        </h4>
                        <div className="flex-1 rounded-2xl border border-rose-500/30 bg-rose-500/5 p-4">
                          <div className="text-center mb-4">
                            <div className="w-20 h-20 mx-auto rounded-full bg-rose-500/20 flex items-center justify-center text-4xl mb-3">
                              👾
                            </div>
                            <p className="text-lg font-bold text-rose-300">{currentEvent.name}</p>
                          </div>
                          <div className="space-y-3">
                            <div>
                              <div className="flex justify-between text-xs mb-1">
                                <span className="text-gray-400">生命值</span>
                                <span className="text-rose-300">{currentEvent.hp}/{currentEvent.maxHp}</span>
                              </div>
                              <div className="h-2 bg-white/10 rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-gradient-to-r from-rose-500 to-amber-500 rounded-full"
                                  style={{ width: `${(currentEvent.hp / currentEvent.maxHp) * 100}%` }}
                                />
                              </div>
                            </div>
                            <div className="rounded-xl border border-white/10 bg-black/20 p-3">
                              <p className="text-xs text-gray-400 mb-1">弱点</p>
                              <div className="flex flex-wrap gap-1">
                                <span className="text-[10px] px-2 py-0.5 rounded-full border border-violet-400/30 bg-violet-500/10 text-violet-300">逆天</span>
                                <span className="text-[10px] px-2 py-0.5 rounded-full border border-pink-400/30 bg-pink-500/10 text-pink-300">温馨</span>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="h-full flex flex-col">
                        <h4 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                          事件详情
                        </h4>
                        <div className="flex-1 rounded-2xl border border-amber-500/30 bg-amber-500/5 p-4">
                          <p className="text-sm text-amber-200 leading-relaxed">{currentEvent.description}</p>
                        </div>
                      </div>
                    )
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-gray-500">
                      <span className="text-4xl mb-3">📜</span>
                      <p className="text-sm">暂无遭遇</p>
                      <p className="text-xs mt-1 text-gray-600">探索或战斗后显示详情</p>
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

    </div>
  )
}
