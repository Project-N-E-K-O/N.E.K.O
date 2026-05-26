import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import {
  ArrowLeft,
  Archive,
  BookOpen,
  Filter,
  Flame,
  Heart,
  HelpCircle,
  Layers,
  Play,
  Save,
  Search,
  Snowflake,
  Sparkles,
  Trash2,
  Volume2,
  VolumeX,
  Wand2,
  Wind,
  X,
} from 'lucide-react'
import {
  deleteForgedBrawlCard,
  loadForgedBrawlCards,
  normalizeForgedBrawlCard,
} from '../../data/forgedBrawlCards'
import CardInspectModal from './CardInspectModal'
import DeckBuilderTutorialPanel from './DeckBuilderTutorialPanel'

const DECK_SIZE = 18
const MAX_CARD_COPIES = 3
const FORGED_CARD_COPIES = 1
const CURRENT_DECK_STORAGE_KEY = 'neko-brawl-deck'
const DECK_LIBRARY_STORAGE_KEY = 'neko-brawl-deck-library'
const FAVORITE_CARDS_STORAGE_KEY = 'neko-brawl-favorite-cards'
// 临时管理功能：当前用于开发期清理测试 Forged 卡；正式版上线或有正式收藏上限后应隐藏/移除。
const SHOW_TEMP_FORGED_DELETE = true

const ATTRIBUTES = [
  { id: 'passion', name: '热情', icon: Flame, mark: '火', accent: 'border-red-500 text-red-700 bg-red-50' },
  { id: 'gentle', name: '温柔', icon: Heart, mark: '心', accent: 'border-pink-500 text-pink-700 bg-pink-50' },
  { id: 'cool', name: '高冷', icon: Snowflake, mark: '冰', accent: 'border-cyan-500 text-cyan-700 bg-cyan-50' },
  { id: 'natural', name: '天然', icon: Wind, mark: '风', accent: 'border-emerald-500 text-emerald-700 bg-emerald-50' },
]

const CARD_POOL = [
  { code: 'C001', name: '午后扑抱', attrId: 'passion', cost: 1, type: '攻击', mainText: '对Boss造成1点伤害', comboText: '额外造成1点伤害' },
  { code: 'C002', name: '亮晶晶眼神', attrId: 'gentle', cost: 1, type: '回复', mainText: '回复生命最低的己方玩家1点生命', comboText: '自身回复1点生命' },
  { code: 'C003', name: '尾巴在说话', attrId: 'cool', cost: 1, type: '防御', mainText: '为自己获得1点护盾', comboText: '为队友提供1点护盾' },
  { code: 'C004', name: '云朵经过的三秒', attrId: 'natural', cost: 1, type: '抽牌', mainText: '抽1张牌', comboText: '额外抽1张牌' },
  { code: 'C005', name: '还没认输呢', attrId: 'passion', cost: 2, type: '攻击', mainText: '对Boss造成2点伤害', comboText: '额外造成1点伤害' },
  { code: 'C006', name: '怀中心跳', attrId: 'cool', cost: 2, type: '防御', mainText: '本回合Boss对自己造成的伤害-2', comboText: '队友本回合受到的伤害-2' },
  { code: 'C007', name: '熬夜到头秃', attrId: 'cool', cost: 2, type: '强化', mainText: '下回合造成伤害+2', comboText: '获得2点护盾' },
  { code: 'C008', name: '拂面微风', attrId: 'natural', cost: 2, type: '回复', mainText: '双方玩家各回复1点生命', comboText: '额外为双方各获得1点护盾' },
  { code: 'C009', name: '纸箱里的秘密计划', attrId: 'gentle', cost: 2, type: '控制', mainText: '对Boss造成1点伤害，并使Boss下次攻击伤害-1', comboText: '额外造成1点伤害' },
  { code: 'C010', name: '屋顶上的晚安', attrId: 'cool', cost: 3, type: '回复', mainText: '回复双方玩家各2点生命', comboText: '清除1个负面状态' },
  { code: 'C011', name: '生人勿近', attrId: 'natural', cost: 3, type: '防御', mainText: '对Boss造成2点伤害，并为双方各获得1点护盾', comboText: '本回合Boss造成伤害-1' },
  { code: 'C012', name: '用尽全力奔向你', attrId: 'gentle', cost: 3, type: '攻击', mainText: '对Boss造成4点伤害', comboText: '额外造成2点伤害' },
  { code: 'C013', name: '完全奇迹', attrId: 'passion', cost: 4, type: '控制', mainText: '对Boss造成3点伤害，并封锁Boss下回合行动', comboText: '自身获得2点护盾' },
]

const attrById = ATTRIBUTES.reduce((map, attr) => ({ ...map, [attr.id]: attr }), {})
const TYPES = ['全部', ...Array.from(new Set(CARD_POOL.map(card => card.type)))]
const COSTS = ['全部', 1, 2, 3, 4]
const CARD_TEXT_HIGHLIGHT_RULES = [
  { pattern: /(?:对Boss)?造成\d+点伤害|额外造成\d+点伤害|伤害[+-]\d+/y, className: 'font-black text-red-700' },
  { pattern: /回复(?:生命最低的己方玩家|双方玩家各|自身|队友)?\d+点生命/y, className: 'font-black text-emerald-700' },
  { pattern: /(?:获得|提供)\d+点护盾|为(?:自己|队友|双方各)获得\d+点护盾/y, className: 'font-black text-sky-700' },
  { pattern: /抽\d+张牌/y, className: 'font-black text-cyan-700' },
  { pattern: /封锁boss下回合行动|封锁Boss下回合行动/y, className: 'font-black text-violet-700' },
  { pattern: /清除\d+个负面状态/y, className: 'font-black text-amber-700' },
]

function renderCardEffectText(text) {
  const source = String(text || '')
  const nodes = []
  let index = 0

  while (index < source.length) {
    let matched = null
    for (const rule of CARD_TEXT_HIGHLIGHT_RULES) {
      rule.pattern.lastIndex = index
      const result = rule.pattern.exec(source)
      if (result?.index === index) {
        matched = { text: result[0], className: rule.className }
        break
      }
    }

    if (matched) {
      nodes.push(
        <span key={`${index}-${matched.text}`} className={matched.className}>
          {matched.text}
        </span>
      )
      index += matched.text.length
    } else {
      nodes.push(source[index])
      index += 1
    }
  }

  return nodes
}

function cardCopies(deck, code) {
  return deck.filter(item => item === code).length
}

function maxCopiesForCard(card) {
  return card?.forged ? FORGED_CARD_COPIES : MAX_CARD_COPIES
}

function compactDeck(deck, cardPool = CARD_POOL) {
  return cardPool
    .map(card => ({ ...card, copies: cardCopies(deck, card.code), attr: attrById[card.attrId] }))
    .filter(card => card.copies > 0)
}

function makeAutoDeck(cardPool = CARD_POOL) {
  const picks = []
  const sorted = [...cardPool].sort((a, b) => a.cost - b.cost || a.code.localeCompare(b.code))
  let cursor = 0

  while (picks.length < DECK_SIZE) {
    const card = sorted[cursor % sorted.length]
    if (cardCopies(picks, card.code) < maxCopiesForCard(card)) {
      picks.push(card.code)
    }
    cursor += 1
  }

  return picks
}

function normalizeDeckCodes(deckCodes, cardPool = CARD_POOL) {
  const picked = []
  for (const code of Array.isArray(deckCodes) ? deckCodes : []) {
    if (picked.length >= DECK_SIZE) break
    const card = cardPool.find(item => item.code === code)
    if (!card) continue
    if (cardCopies(picked, code) >= maxCopiesForCard(card)) continue
    picked.push(code)
  }
  return picked
}

export default function DeckBuilderPanel({
  onClose,
  onStartBattle,
  onOpenDeckLibrary,
  onDeleteForgedCard,
  forgedCards = [],
  temporaryBgmEnabled = true,
  onToggleTemporaryBgm,
}) {
  const [deletedForgedCodes, setDeletedForgedCodes] = useState([])
  const availableCards = useMemo(() => {
    const propCards = Array.isArray(forgedCards)
      ? forgedCards.map(normalizeForgedBrawlCard).filter(Boolean)
      : []
    const storedCards = loadForgedBrawlCards()
    const mergedForgedCards = [...storedCards, ...propCards].reduce((list, card) => {
      if (!list.some(item => item.code === card.code)) list.push(card)
      return list
    }, []).filter(card => !deletedForgedCodes.includes(card.code))
    return [...CARD_POOL, ...mergedForgedCards]
  }, [deletedForgedCodes, forgedCards])

  const [deck, setDeck] = useState(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(CURRENT_DECK_STORAGE_KEY) || '[]')
      const initialCards = [...CARD_POOL, ...loadForgedBrawlCards()]
      const valid = normalizeDeckCodes(saved, initialCards)
      return valid.length > 0 ? valid.slice(0, DECK_SIZE) : makeAutoDeck()
    } catch {
      return makeAutoDeck()
    }
  })
  const [query, setQuery] = useState('')
  const [attrFilter, setAttrFilter] = useState('all')
  const [favoritesOnly, setFavoritesOnly] = useState(false)
  const [favoriteCodes, setFavoriteCodes] = useState(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(FAVORITE_CARDS_STORAGE_KEY) || '[]')
      return Array.isArray(saved) ? saved.filter(code => typeof code === 'string') : []
    } catch {
      return []
    }
  })
  const [costFilter, setCostFilter] = useState('全部')
  const [typeFilter, setTypeFilter] = useState('全部')
  const [savedAt, setSavedAt] = useState('')
  const [inspectedCard, setInspectedCard] = useState(null)
  const [showTutorial, setShowTutorial] = useState(false)
  const favoriteSet = useMemo(() => new Set(favoriteCodes), [favoriteCodes])

  useEffect(() => {
    window.localStorage.setItem(FAVORITE_CARDS_STORAGE_KEY, JSON.stringify(favoriteCodes))
  }, [favoriteCodes])

  const groupedDeck = useMemo(() => compactDeck(deck, availableCards), [deck, availableCards])
  const averageCost = deck.length
    ? (deck.reduce((sum, code) => sum + (availableCards.find(card => card.code === code)?.cost || 0), 0) / deck.length).toFixed(1)
    : '0.0'

  const attrStats = ATTRIBUTES.map(attr => ({
    ...attr,
    count: deck.filter(code => availableCards.find(card => card.code === code)?.attrId === attr.id).length,
  }))

  const filteredCards = availableCards
    .map(card => ({ ...card, attr: attrById[card.attrId] }))
    .filter(card => {
      const text = `${card.name} ${card.code} ${card.baseCode || ''} ${card.mainText} ${card.comboText} ${card.story || ''}`.toLowerCase()
      const matchQuery = text.includes(query.trim().toLowerCase())
      const matchAttr = attrFilter === 'all' || card.attrId === attrFilter
      const matchCost = costFilter === '全部' || card.cost === costFilter
      const matchType = typeFilter === '全部' || card.type === typeFilter
      const matchFavorite = !favoritesOnly || favoriteSet.has(card.code)
      return matchQuery && matchAttr && matchCost && matchType && matchFavorite
    })

  const fullDeck = deck.length >= DECK_SIZE

  const addCard = (card) => {
    if (deck.length >= DECK_SIZE) return
    if (cardCopies(deck, card.code) >= maxCopiesForCard(card)) return
    setDeck(prev => [...prev, card.code])
  }

  const toggleFavorite = (code, event) => {
    event?.stopPropagation()
    setFavoriteCodes(prev => (
      prev.includes(code)
        ? prev.filter(item => item !== code)
        : [...prev, code]
    ))
  }

  const removeCard = (code) => {
    setDeck(prev => {
      const index = prev.indexOf(code)
      if (index < 0) return prev
      return prev.filter((_, itemIndex) => itemIndex !== index)
    })
  }

  const deleteForgedCardFromCollection = (card, event) => {
    event?.stopPropagation()
    if (!card?.forged) return
    const confirmed = window.confirm(`删除生成卡牌「${card.name}」？这会从组卡收藏、当前卡组和本地卡组仓库中移除它。`)
    if (!confirmed) return

    // 临时管理功能：用于测试阶段清理 Forged 卡；正式版有收藏上限/正式删除规则时应隐藏此入口。
    deleteForgedBrawlCard(card)
    onDeleteForgedCard?.(card)
    setDeletedForgedCodes(prev => (prev.includes(card.code) ? prev : [...prev, card.code]))

    setDeck(prev => {
      const next = prev.filter(code => code !== card.code)
      window.localStorage.setItem(CURRENT_DECK_STORAGE_KEY, JSON.stringify(next))
      return next
    })
    setFavoriteCodes(prev => prev.filter(code => code !== card.code))
    setInspectedCard(prev => (prev?.code === card.code ? null : prev))

    try {
      const library = JSON.parse(window.localStorage.getItem(DECK_LIBRARY_STORAGE_KEY) || '[]')
      if (Array.isArray(library)) {
        const nextLibrary = library.map(slot => ({
          ...slot,
          deck: Array.isArray(slot.deck) ? slot.deck.filter(code => code !== card.code) : [],
        }))
        window.localStorage.setItem(DECK_LIBRARY_STORAGE_KEY, JSON.stringify(nextLibrary))
      }
    } catch {
      /* 本地仓库损坏时忽略，后续读取会走既有容错。 */
    }
  }

  const saveDeck = () => {
    window.localStorage.setItem(CURRENT_DECK_STORAGE_KEY, JSON.stringify(deck))
    setSavedAt('已保存')
    window.setTimeout(() => setSavedAt(''), 1600)
  }

  const startBattle = () => {
    saveDeck()
    onStartBattle?.()
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 28 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 28 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
      className="fixed inset-0 z-[130] flex h-screen w-screen flex-col overflow-hidden bg-white text-zinc-950"
    >
      <header className="flex h-16 shrink-0 items-center justify-between border-b-2 border-zinc-950 bg-white px-4">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-zinc-950 bg-white hover:bg-zinc-100"
            title="返回初始界面"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div className="min-w-0">
            <h2 className="truncate text-lg font-black">猫娘大乱斗 - 组卡界面</h2>
            <p className="truncate text-xs font-bold text-zinc-500">进入战斗前配置卡组，当前版本先保存为本地预设</p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onToggleTemporaryBgm}
            className={`hidden h-10 items-center gap-2 border-2 px-3 text-sm font-black md:flex ${
              temporaryBgmEnabled
                ? 'border-amber-500 bg-amber-50 text-amber-700 hover:bg-amber-100'
                : 'border-zinc-400 bg-white text-zinc-500 hover:bg-zinc-100'
            }`}
            title="临时测试开关，后续会移除"
          >
            {temporaryBgmEnabled ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
            临时BGM：{temporaryBgmEnabled ? '开' : '关'}
          </button>
          <button
            type="button"
            onClick={() => setShowTutorial(true)}
            className="hidden h-10 items-center gap-2 border-2 border-zinc-950 bg-white px-3 text-sm font-black hover:bg-zinc-100 md:flex"
          >
            <BookOpen className="h-4 w-4" />
            教程
          </button>
          <button
            type="button"
            onClick={onOpenDeckLibrary}
            className="hidden h-10 items-center gap-2 border-2 border-orange-500 bg-orange-50 px-3 text-sm font-black text-orange-700 hover:bg-orange-100 md:flex"
          >
            <Archive className="h-4 w-4" />
            卡组仓库
          </button>
          <button
            type="button"
            onClick={saveDeck}
            className="flex h-10 items-center gap-2 border-2 border-zinc-950 bg-white px-3 text-sm font-black hover:bg-zinc-100"
          >
            <Save className="h-4 w-4" />
            保存
          </button>
          <button
            type="button"
            onClick={startBattle}
            disabled={!fullDeck}
            className="flex h-10 items-center gap-2 border-2 border-zinc-950 bg-zinc-950 px-4 text-sm font-black text-white disabled:cursor-not-allowed disabled:border-zinc-300 disabled:bg-zinc-200 disabled:text-zinc-500"
          >
            <Play className="h-4 w-4" />
            进入对局
          </button>
          <button
            type="button"
            onClick={() => setShowTutorial(true)}
            className="flex h-10 w-10 items-center justify-center border-2 border-zinc-950 bg-white hover:bg-zinc-100"
            title="帮助"
          >
            <HelpCircle className="h-5 w-5" />
          </button>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)_280px] gap-0 overflow-hidden">
        <aside className="flex min-h-0 flex-col border-r-2 border-zinc-950 bg-white">
          <div className="border-b-2 border-zinc-950 p-4">
            <div className="flex items-end justify-between">
              <div>
                <p className="text-xs font-black text-zinc-500">当前卡组</p>
                <p className="text-3xl font-black">{deck.length}/{DECK_SIZE}</p>
              </div>
              <div className="text-right">
                <p className="text-xs font-black text-zinc-500">平均行动力</p>
                <p className="text-2xl font-black">{averageCost}</p>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setDeck(makeAutoDeck(availableCards))}
                className="flex h-10 items-center justify-center gap-2 border-2 border-zinc-950 bg-white text-sm font-black hover:bg-zinc-100"
              >
                <Wand2 className="h-4 w-4" />
                自动填充
              </button>
              <button
                type="button"
                onClick={() => setDeck([])}
                className="flex h-10 items-center justify-center gap-2 border-2 border-zinc-950 bg-white text-sm font-black hover:bg-zinc-100"
              >
                <Trash2 className="h-4 w-4" />
                清空
              </button>
            </div>
            {savedAt && <p className="mt-2 text-xs font-black text-emerald-700">{savedAt}</p>}
          </div>

          <div className="border-b-2 border-zinc-950 p-4">
            <p className="mb-2 text-xs font-black text-zinc-500">属性分布</p>
            <div className="space-y-2">
              {attrStats.map(attr => {
                const Icon = attr.icon
                return (
                  <button
                    type="button"
                    key={attr.id}
                    onClick={() => setAttrFilter(attrFilter === attr.id ? 'all' : attr.id)}
                    className={`flex w-full items-center justify-between border-2 px-3 py-2 text-sm font-black ${attrFilter === attr.id ? attr.accent : 'border-zinc-950 bg-white text-zinc-900 hover:bg-zinc-100'}`}
                  >
                    <span className="flex items-center gap-2">
                      <Icon className="h-4 w-4" />
                      {attr.name}
                    </span>
                    <span>{attr.count}</span>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <p className="mb-2 text-xs font-black text-zinc-500">已选卡牌</p>
            <div className="space-y-2">
              {groupedDeck.map(card => {
                const Icon = card.attr.icon
                return (
                  <button
                    type="button"
                    key={card.code}
                    onClick={() => removeCard(card.code)}
                    className="grid w-full grid-cols-[28px_1fr_36px] items-center gap-2 border-2 border-zinc-950 bg-white p-2 text-left hover:bg-zinc-100"
                  >
                    <span className={`flex h-7 w-7 items-center justify-center border-2 text-xs font-black ${card.attr.accent}`}>
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-black">{card.name}</span>
                      <span className="block truncate text-[11px] font-bold text-zinc-500">行动力 {card.cost} / {card.type}</span>
                    </span>
                    <span className="text-right text-sm font-black">x{card.copies}</span>
                  </button>
                )
              })}
              {groupedDeck.length === 0 && (
                <div className="border-2 border-dashed border-zinc-400 p-4 text-center text-sm font-bold text-zinc-500">
                  从中间牌库选择卡牌
                </div>
              )}
            </div>
          </div>
        </aside>

        <section className="flex min-h-0 min-w-0 flex-col overflow-hidden bg-white">
          <div className="flex shrink-0 items-center gap-3 border-b-2 border-zinc-950 p-4">
            <div className="flex h-11 min-w-0 flex-1 items-center gap-2 border-2 border-zinc-950 px-3">
              <Search className="h-4 w-4 shrink-0" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索卡名、编号或效果"
                className="min-w-0 flex-1 bg-transparent text-sm font-bold outline-none placeholder:text-zinc-400"
              />
              {query && (
                <button type="button" onClick={() => setQuery('')} className="shrink-0">
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>
            <button
              type="button"
              onClick={() => setFavoritesOnly(prev => !prev)}
              className={`flex h-11 shrink-0 items-center gap-2 border-2 px-3 text-sm font-black ${
                favoritesOnly
                  ? 'border-rose-500 bg-rose-50 text-rose-700'
                  : 'border-zinc-950 bg-white text-zinc-900 hover:bg-zinc-100'
              }`}
              title="只显示喜爱卡牌"
            >
              <span className="text-base leading-none">{favoritesOnly ? '\u2665' : '\u2661'}</span>
              喜爱
            </button>
            <div className="flex shrink-0 items-center gap-2">
              {COSTS.map(cost => (
                <button
                  type="button"
                  key={cost}
                  onClick={() => setCostFilter(cost)}
                  className={`h-11 min-w-11 border-2 px-3 text-sm font-black ${costFilter === cost ? 'border-orange-500 bg-orange-50 text-orange-700' : 'border-zinc-950 bg-white hover:bg-zinc-100'}`}
                >
                  {cost === '全部' ? '全部' : cost}
                </button>
              ))}
            </div>
          </div>

          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex shrink-0 items-center justify-between border-b-2 border-zinc-950 bg-zinc-50 px-4 py-2">
              <div>
                <p className="text-xs font-black text-zinc-500">卡牌收藏</p>
                <p className="text-sm font-black text-zinc-900">
                  显示 {filteredCards.length} / {availableCards.length} 张
                </p>
              </div>
              <p className="text-[11px] font-bold text-zinc-500">向下滚动查看全部卡牌</p>
            </div>
            <div
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 pb-28 pr-3 [scrollbar-gutter:stable]"
              style={{ scrollPaddingBottom: '7rem' }}
            >
              <div className="grid grid-cols-[repeat(auto-fill,minmax(176px,1fr))] gap-3 pb-16">
              {filteredCards.map(card => {
                const copies = cardCopies(deck, card.code)
                const maxCopies = maxCopiesForCard(card)
                const locked = fullDeck || copies >= maxCopies
                const Icon = card.attr.icon
                const favorite = favoriteSet.has(card.code)

                return (
                  <article
                    role="button"
                    tabIndex={locked ? -1 : 0}
                    key={card.code}
                    onClick={() => addCard(card)}
                    onKeyDown={(event) => {
                      if (!locked && (event.key === 'Enter' || event.key === ' ')) {
                        event.preventDefault()
                        addCard(card)
                      }
                    }}
                    aria-disabled={locked}
                    className={`group flex min-h-[252px] scroll-mb-24 flex-col border-2 p-3 text-left transition ${copies > 0 ? 'border-zinc-950 bg-zinc-50 shadow-[4px_4px_0_#18181b]' : 'border-zinc-950 bg-white hover:bg-zinc-50 hover:shadow-[4px_4px_0_#18181b]'} ${locked ? 'cursor-not-allowed opacity-55' : 'cursor-pointer'}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="flex h-8 w-8 items-center justify-center rounded-full border-2 border-orange-500 bg-white text-sm font-black text-orange-700">
                        {card.cost}
                      </span>
                      <span className={`flex h-8 min-w-8 items-center justify-center border-2 px-2 text-xs font-black ${card.attr.accent}`}>
                        <Icon className="mr-1 h-3.5 w-3.5" />
                        {card.attr.mark}
                      </span>
                    </div>
                    <div className="mt-4 min-h-0 flex-1 overflow-y-auto pr-1">
                      <p className="text-base font-black leading-tight">{card.name}</p>
                      <p className="mt-1 text-xs font-black text-zinc-500">{card.code} / {card.type}</p>
                      {card.forged && (
                        <p className="mt-1 max-h-16 overflow-y-auto text-[11px] font-black leading-snug text-violet-700">
                          Story：{card.story}
                        </p>
                      )}
                      <p className="mt-3 text-sm font-bold leading-snug text-zinc-800">{renderCardEffectText(card.mainText)}</p>
                      <p className="mt-2 border-t-2 border-dashed border-zinc-300 pt-2 text-xs font-bold leading-snug text-zinc-600">
                        Combo：{renderCardEffectText(card.comboText)}
                      </p>
                      <p className="mt-1 text-[11px] font-black text-orange-700">
                        Combo属性：{attrById[card.comboAttrId || card.attrId]?.name || card.comboAttrName || card.attr?.name}
                      </p>
                    </div>
                    <div className="mt-3 grid grid-cols-[28px_1fr_auto_auto_auto] items-center gap-2 border-t-2 border-zinc-950 pt-2 text-xs font-black">
                      <button
                        type="button"
                        onClick={(event) => toggleFavorite(card.code, event)}
                        className={`flex h-7 w-7 items-center justify-center border-2 text-base leading-none transition ${
                          favorite
                            ? 'border-rose-500 bg-rose-50 text-rose-600'
                            : 'border-zinc-950 bg-white text-zinc-500 hover:border-rose-500 hover:text-rose-600'
                        }`}
                        title={favorite ? '取消喜爱' : '标记为喜爱'}
                        aria-label={favorite ? '取消喜爱卡牌' : '标记为喜爱卡牌'}
                      >
                        {favorite ? '\u2665' : '\u2661'}
                      </button>
                      <span>已选 x{copies}/{maxCopies}</span>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation()
                          setInspectedCard(card)
                        }}
                        className="h-7 border-2 border-zinc-950 bg-white px-2 text-xs font-black text-zinc-900 hover:bg-zinc-100"
                      >
                        查看
                      </button>
                      {SHOW_TEMP_FORGED_DELETE && card.forged && (
                        <button
                          type="button"
                          onClick={(event) => deleteForgedCardFromCollection(card, event)}
                          className="flex h-7 items-center gap-1 border-2 border-red-500 bg-red-50 px-2 text-xs font-black text-red-700 hover:bg-red-100"
                          title="临时删除生成卡：正式版收藏上限上线后应隐藏"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          删除
                        </button>
                      )}
                      <span>{locked ? '不可添加' : '添加'}</span>
                    </div>
                  </article>
                )
              })}
              </div>
              {filteredCards.length === 0 && (
                <div className="mt-8 border-2 border-dashed border-zinc-400 p-6 text-center text-sm font-bold text-zinc-500">
                  没有符合筛选条件的卡牌
                </div>
              )}
            </div>
          </div>
        </section>

        <aside className="flex min-h-0 flex-col border-l-2 border-zinc-950 bg-white">
          <div className="border-b-2 border-zinc-950 p-4">
            <div className="mb-3 flex items-center gap-2">
              <Filter className="h-5 w-5" />
              <h3 className="text-base font-black">筛选</h3>
            </div>
            <p className="mb-2 text-xs font-black text-zinc-500">效果类型</p>
            <div className="grid grid-cols-2 gap-2">
              {TYPES.map(type => (
                <button
                  type="button"
                  key={type}
                  onClick={() => setTypeFilter(type)}
                  className={`h-9 border-2 px-2 text-xs font-black ${typeFilter === type ? 'border-zinc-950 bg-zinc-950 text-white' : 'border-zinc-950 bg-white hover:bg-zinc-100'}`}
                >
                  {type}
                </button>
              ))}
            </div>
          </div>

          <div className="border-b-2 border-zinc-950 p-4">
            <div className="mb-3 flex items-center gap-2">
              <Layers className="h-5 w-5" />
              <h3 className="text-base font-black">组卡规则</h3>
            </div>
            <div className="space-y-2 text-sm font-bold leading-relaxed text-zinc-700">
              <p>卡组需要 {DECK_SIZE} 张卡。</p>
              <p>普通同名卡暂定最多 {MAX_CARD_COPIES} 张；Forged 铸造卡是具体实例，只能加入 {FORGED_CARD_COPIES} 张。</p>
              <p>保存后会记录到本地，方便后续接入真实战斗牌库。</p>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <div className="mb-3 flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              <h3 className="text-base font-black">预设评估</h3>
            </div>
            <div className="space-y-3">
              <div className="border-2 border-zinc-950 p-3">
                <p className="text-xs font-black text-zinc-500">进攻卡</p>
                <p className="mt-1 text-2xl font-black">{groupedDeck.filter(card => ['攻击', '控制'].includes(card.type)).reduce((sum, card) => sum + card.copies, 0)}</p>
              </div>
              <div className="border-2 border-zinc-950 p-3">
                <p className="text-xs font-black text-zinc-500">回复/防御</p>
                <p className="mt-1 text-2xl font-black">{groupedDeck.filter(card => ['回复', '防御'].includes(card.type)).reduce((sum, card) => sum + card.copies, 0)}</p>
              </div>
              <div className="border-2 border-zinc-950 p-3">
                <p className="text-xs font-black text-zinc-500">行动力曲线</p>
                <div className="mt-3 flex h-24 items-end gap-2">
                  {[1, 2, 3, 4].map(cost => {
                    const count = deck.filter(code => availableCards.find(card => card.code === code)?.cost === cost).length
                    return (
                      <div key={cost} className="flex flex-1 flex-col items-center gap-1">
                        <div
                          className="w-full border-2 border-orange-500 bg-orange-100"
                          style={{ height: `${Math.max(10, count * 10)}px` }}
                        />
                        <span className="text-xs font-black">{cost}</span>
                      </div>
                    )
                  })}
                </div>
              </div>
              <div className="border-2 border-dashed border-zinc-400 p-3 text-xs font-bold leading-relaxed text-zinc-500">
                当前界面是战斗前组卡入口。正式接入后，战斗抽牌会读取这里保存的卡组。
              </div>
            </div>
          </div>
        </aside>
      </main>

      <CardInspectModal
        open={Boolean(inspectedCard)}
        card={inspectedCard}
        source="deck-builder"
        onClose={() => setInspectedCard(null)}
      />
      <DeckBuilderTutorialPanel
        open={showTutorial}
        onClose={() => setShowTutorial(false)}
      />
    </motion.div>
  )
}
