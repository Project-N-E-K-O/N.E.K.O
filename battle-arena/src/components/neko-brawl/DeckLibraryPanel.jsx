import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import {
  ArrowLeft,
  Archive,
  Download,
  Edit3,
  Flame,
  Heart,
  Save,
  Snowflake,
  Sparkles,
  Trash2,
  Upload,
  Volume2,
  VolumeX,
  X,
  Wind,
} from 'lucide-react'
import {
  loadForgedBrawlCards,
  normalizeForgedBrawlCard,
} from '../../data/forgedBrawlCards'

const DECK_SIZE = 18
const CURRENT_DECK_STORAGE_KEY = 'neko-brawl-deck'
const DECK_LIBRARY_STORAGE_KEY = 'neko-brawl-deck-library'

const ATTRIBUTES = [
  { id: 'passion', name: '热情', icon: Flame, mark: '火', accent: 'border-red-500 text-red-700 bg-red-50' },
  { id: 'gentle', name: '温柔', icon: Heart, mark: '心', accent: 'border-pink-500 text-pink-700 bg-pink-50' },
  { id: 'cool', name: '高冷', icon: Snowflake, mark: '冰', accent: 'border-cyan-500 text-cyan-700 bg-cyan-50' },
  { id: 'natural', name: '天然', icon: Wind, mark: '风', accent: 'border-emerald-500 text-emerald-700 bg-emerald-50' },
]

const CARD_POOL = [
  { code: 'C001', name: '午后扑抱', attrId: 'passion', cost: 1, type: '攻击' },
  { code: 'C002', name: '亮晶晶眼神', attrId: 'gentle', cost: 1, type: '回复' },
  { code: 'C003', name: '尾巴在说话', attrId: 'cool', cost: 1, type: '防御' },
  { code: 'C004', name: '云朵经过的三秒', attrId: 'natural', cost: 1, type: '抽牌' },
  { code: 'C005', name: '还没认输呢', attrId: 'passion', cost: 2, type: '攻击' },
  { code: 'C006', name: '怀中心跳', attrId: 'cool', cost: 2, type: '防御' },
  { code: 'C007', name: '熬夜到头秃', attrId: 'cool', cost: 2, type: '强化' },
  { code: 'C008', name: '拂面微风', attrId: 'natural', cost: 2, type: '回复' },
  { code: 'C009', name: '纸箱里的秘密计划', attrId: 'gentle', cost: 2, type: '控制' },
  { code: 'C010', name: '屋顶上的晚安', attrId: 'cool', cost: 3, type: '回复' },
  { code: 'C011', name: '生人勿近', attrId: 'natural', cost: 3, type: '防御' },
  { code: 'C012', name: '用尽全力奔向你', attrId: 'gentle', cost: 3, type: '攻击' },
  { code: 'C013', name: '完全奇迹', attrId: 'passion', cost: 4, type: '控制' },
]

const attrById = ATTRIBUTES.reduce((map, attr) => ({ ...map, [attr.id]: attr }), {})

function normalizeDeckCodes(deck, cardPool) {
  if (!Array.isArray(deck)) return []
  return deck
    .filter(code => typeof code === 'string' && cardPool.some(card => card.code === code))
    .slice(0, DECK_SIZE)
}

function readCurrentDeck(cardPool) {
  try {
    return normalizeDeckCodes(JSON.parse(window.localStorage.getItem(CURRENT_DECK_STORAGE_KEY) || '[]'), cardPool)
  } catch {
    return []
  }
}

function readDeckLibrary(cardPool) {
  try {
    const saved = JSON.parse(window.localStorage.getItem(DECK_LIBRARY_STORAGE_KEY) || '[]')
    if (!Array.isArray(saved)) return []
    return saved
      .map((slot, index) => {
        const cards = normalizeDeckCodes(slot?.cards, cardPool)
        if (cards.length === 0) return null
        return {
          id: typeof slot.id === 'string' ? slot.id : `deck-slot-${Date.now()}-${index}`,
          name: typeof slot.name === 'string' ? slot.name : `卡组 ${index + 1}`,
          cards,
          savedAt: typeof slot.savedAt === 'number' ? slot.savedAt : Date.now(),
        }
      })
      .filter(Boolean)
  } catch {
    return []
  }
}

function saveDeckLibrary(slots) {
  window.localStorage.setItem(DECK_LIBRARY_STORAGE_KEY, JSON.stringify(slots))
}

function cardCopies(deck, code) {
  return deck.filter(item => item === code).length
}

function compactDeck(deck, cardPool) {
  return cardPool
    .map(card => ({
      ...card,
      attr: attrById[card.attrId] || attrById.passion,
      copies: cardCopies(deck, card.code),
    }))
    .filter(card => card.copies > 0)
}

function formatDeckSlotTime(timestamp) {
  if (!timestamp) return '刚刚'
  const date = new Date(timestamp)
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

function makeDeckSlotName(slots) {
  return `卡组 ${slots.length + 1}`
}

function normalizeDeckSlotName(name) {
  return String(name || '').trim().slice(0, 24)
}

function getAverageCost(deck, cardPool) {
  if (!deck.length) return '0.0'
  return (deck.reduce((sum, code) => sum + (cardPool.find(card => card.code === code)?.cost || 0), 0) / deck.length).toFixed(1)
}

function getAttackCount(deck, cardPool) {
  return compactDeck(deck, cardPool)
    .filter(card => ['攻击', '控制'].includes(card.type))
    .reduce((sum, card) => sum + card.copies, 0)
}

export default function DeckLibraryPanel({
  onClose,
  onOpenDeckBuilder,
  forgedCards = [],
  temporaryBgmEnabled = true,
  onToggleTemporaryBgm,
}) {
  const availableCards = useMemo(() => {
    const propCards = Array.isArray(forgedCards)
      ? forgedCards.map(normalizeForgedBrawlCard).filter(Boolean)
      : []
    const storedCards = loadForgedBrawlCards()
    const mergedForgedCards = [...storedCards, ...propCards].reduce((list, card) => {
      if (!list.some(item => item.code === card.code)) list.push(card)
      return list
    }, [])
    return [...CARD_POOL, ...mergedForgedCards]
  }, [forgedCards])

  const [currentDeck, setCurrentDeck] = useState(() => readCurrentDeck(availableCards))
  const [deckLibrary, setDeckLibrary] = useState(() => readDeckLibrary(availableCards))
  const [statusText, setStatusText] = useState('')
  const [renamingSlotId, setRenamingSlotId] = useState(null)
  const [renameValue, setRenameValue] = useState('')
  const libraryFull = deckLibrary.length >= 8

  const setStatus = (text) => {
    setStatusText(text)
    window.setTimeout(() => setStatusText(''), 1600)
  }

  const persistLibrary = (nextLibrary) => {
    setDeckLibrary(nextLibrary)
    saveDeckLibrary(nextLibrary)
  }

  const saveCurrentDeckToLibrary = () => {
    if (currentDeck.length === 0 || libraryFull) return
    const now = Date.now()
    persistLibrary([
      {
        id: `deck-slot-${now}`,
        name: makeDeckSlotName(deckLibrary),
        cards: [...currentDeck],
        savedAt: now,
      },
      ...deckLibrary,
    ])
    setStatus('已将当前卡组存入仓库')
  }

  const loadDeck = (slot, openBuilder = false) => {
    const nextDeck = normalizeDeckCodes(slot.cards, availableCards)
    window.localStorage.setItem(CURRENT_DECK_STORAGE_KEY, JSON.stringify(nextDeck))
    setCurrentDeck(nextDeck)
    setStatus(`已读取：${slot.name}`)
    if (openBuilder) onOpenDeckBuilder?.()
  }

  const overwriteSlot = (slotId) => {
    if (currentDeck.length === 0) return
    persistLibrary(deckLibrary.map(slot => (
      slot.id === slotId
        ? { ...slot, cards: [...currentDeck], savedAt: Date.now() }
        : slot
    )))
    setStatus('已覆盖仓库卡组')
  }

  const deleteSlot = (slotId) => {
    persistLibrary(deckLibrary.filter(slot => slot.id !== slotId))
    if (renamingSlotId === slotId) {
      setRenamingSlotId(null)
      setRenameValue('')
    }
    setStatus('已删除仓库卡组')
  }

  const startRenameSlot = (slot) => {
    setRenamingSlotId(slot.id)
    setRenameValue(slot.name)
  }

  const cancelRenameSlot = () => {
    setRenamingSlotId(null)
    setRenameValue('')
  }

  const confirmRenameSlot = (slotId) => {
    const nextName = normalizeDeckSlotName(renameValue)
    if (!nextName) {
      setStatus('卡组名不能为空')
      return
    }

    persistLibrary(deckLibrary.map(slot => (
      slot.id === slotId
        ? { ...slot, name: nextName }
        : slot
    )))
    setRenamingSlotId(null)
    setRenameValue('')
    setStatus('已重命名卡组')
  }

  const currentDeckCards = compactDeck(currentDeck, availableCards)

  return (
    <motion.div
      initial={{ opacity: 0, y: 28 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 28 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
      className="fixed inset-0 z-[130] flex h-screen w-screen flex-col overflow-hidden bg-[#fff7ed] text-zinc-950"
    >
      <header className="flex h-16 shrink-0 items-center justify-between border-b-2 border-zinc-950 bg-white px-4">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-zinc-950 bg-white hover:bg-zinc-100"
            title="返回主页面"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div className="min-w-0">
            <h2 className="truncate text-lg font-black">猫娘大乱斗 - 卡组仓库</h2>
            <p className="truncate text-xs font-bold text-zinc-500">独立卡组存档界面，用于保存和读取多套本地卡组</p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {statusText && <p className="text-xs font-black text-emerald-700">{statusText}</p>}
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
            onClick={onOpenDeckBuilder}
            className="flex h-10 items-center gap-2 border-2 border-zinc-950 bg-white px-3 text-sm font-black hover:bg-zinc-100"
          >
            <Edit3 className="h-4 w-4" />
            进入组卡
          </button>
          <button
            type="button"
            onClick={saveCurrentDeckToLibrary}
            disabled={currentDeck.length === 0 || libraryFull}
            className="flex h-10 items-center gap-2 border-2 border-zinc-950 bg-zinc-950 px-4 text-sm font-black text-white disabled:cursor-not-allowed disabled:border-zinc-300 disabled:bg-zinc-200 disabled:text-zinc-500"
          >
            <Upload className="h-4 w-4" />
            存入仓库
          </button>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)] overflow-hidden">
        <aside className="flex min-h-0 flex-col border-r-2 border-zinc-950 bg-white">
          <section className="border-b-2 border-zinc-950 p-5">
            <div className="flex items-center gap-2">
              <Archive className="h-5 w-5 text-orange-700" />
              <h3 className="text-base font-black">当前战斗卡组</h3>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center">
              <div className="border-2 border-zinc-950 p-3">
                <p className="text-[11px] font-black text-zinc-500">数量</p>
                <p className="mt-1 text-xl font-black">{currentDeck.length}/{DECK_SIZE}</p>
              </div>
              <div className="border-2 border-orange-300 bg-orange-50 p-3">
                <p className="text-[11px] font-black text-orange-700">平均费</p>
                <p className="mt-1 text-xl font-black">{getAverageCost(currentDeck, availableCards)}</p>
              </div>
              <div className="border-2 border-red-300 bg-red-50 p-3">
                <p className="text-[11px] font-black text-red-700">进攻</p>
                <p className="mt-1 text-xl font-black">{getAttackCount(currentDeck, availableCards)}</p>
              </div>
            </div>
          </section>

          <section className="min-h-0 flex-1 overflow-y-auto p-4">
            <p className="mb-2 text-xs font-black text-zinc-500">当前卡组内容</p>
            <div className="space-y-2">
              {currentDeckCards.map(card => {
                const Icon = card.attr.icon
                return (
                  <div key={card.code} className="grid grid-cols-[28px_1fr_36px] items-center gap-2 border-2 border-zinc-950 bg-white p-2">
                    <span className={`flex h-7 w-7 items-center justify-center border-2 text-xs font-black ${card.attr.accent}`}>
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-black">{card.name}</span>
                      <span className="block truncate text-[11px] font-bold text-zinc-500">行动力 {card.cost} / {card.type}</span>
                    </span>
                    <span className="text-right text-sm font-black">x{card.copies}</span>
                  </div>
                )
              })}
              {currentDeckCards.length === 0 && (
                <div className="border-2 border-dashed border-zinc-400 p-4 text-center text-sm font-bold text-zinc-500">
                  还没有当前卡组。请先进入组卡界面保存一套卡组。
                </div>
              )}
            </div>
          </section>
        </aside>

        <section className="min-h-0 overflow-y-auto p-6">
          <div className="mb-5 flex items-end justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-orange-700" />
                <h3 className="text-xl font-black">仓库槽位</h3>
              </div>
              <p className="mt-1 text-sm font-bold text-zinc-600">最多暂存 8 套卡组。读取后会成为当前战斗卡组。</p>
            </div>
            <p className="text-sm font-black text-zinc-600">{deckLibrary.length}/8</p>
          </div>

          <div className="grid grid-cols-[repeat(auto-fill,minmax(270px,1fr))] gap-4">
            {deckLibrary.map(slot => {
              const slotCards = compactDeck(slot.cards, availableCards)
              const renaming = renamingSlotId === slot.id
              return (
                <article key={slot.id} className="border-2 border-zinc-950 bg-white p-4 shadow-[5px_5px_0_#18181b]">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      {renaming ? (
                        <div className="flex min-w-0 items-center gap-1">
                          <input
                            value={renameValue}
                            onChange={event => setRenameValue(event.target.value)}
                            onKeyDown={event => {
                              if (event.key === 'Enter') confirmRenameSlot(slot.id)
                              if (event.key === 'Escape') cancelRenameSlot()
                            }}
                            maxLength={24}
                            autoFocus
                            className="h-8 min-w-0 flex-1 border-2 border-orange-500 bg-orange-50 px-2 text-sm font-black outline-none"
                          />
                          <button
                            type="button"
                            onClick={() => confirmRenameSlot(slot.id)}
                            className="flex h-8 w-8 shrink-0 items-center justify-center border-2 border-zinc-950 bg-zinc-950 text-white hover:bg-zinc-800"
                            title="确认重命名"
                          >
                            <Save className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={cancelRenameSlot}
                            className="flex h-8 w-8 shrink-0 items-center justify-center border-2 border-zinc-950 bg-white hover:bg-zinc-100"
                            title="取消重命名"
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ) : (
                        <div className="flex min-w-0 items-center gap-2">
                          <h4 className="min-w-0 truncate text-lg font-black">{slot.name}</h4>
                          <button
                            type="button"
                            onClick={() => startRenameSlot(slot)}
                            className="flex h-7 w-7 shrink-0 items-center justify-center border-2 border-zinc-950 bg-white hover:bg-orange-50"
                            title="重命名卡组"
                          >
                            <Edit3 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )}
                      <p className="mt-1 text-xs font-bold text-zinc-500">{formatDeckSlotTime(slot.savedAt)}</p>
                    </div>
                    <span className={`shrink-0 border-2 px-2 py-1 text-xs font-black ${
                      slot.cards.length >= DECK_SIZE
                        ? 'border-emerald-500 bg-emerald-50 text-emerald-700'
                        : 'border-orange-500 bg-orange-50 text-orange-700'
                    }`}>
                      {slot.cards.length}/{DECK_SIZE}
                    </span>
                  </div>

                  <div className="mt-4 grid grid-cols-2 gap-2">
                    <div className="border-2 border-orange-300 bg-orange-50 p-3">
                      <p className="text-[11px] font-black text-orange-700">平均行动力</p>
                      <p className="mt-1 text-2xl font-black">{getAverageCost(slot.cards, availableCards)}</p>
                    </div>
                    <div className="border-2 border-red-300 bg-red-50 p-3">
                      <p className="text-[11px] font-black text-red-700">进攻卡</p>
                      <p className="mt-1 text-2xl font-black">{getAttackCount(slot.cards, availableCards)}</p>
                    </div>
                  </div>

                  <div className="mt-3 flex flex-wrap gap-1">
                    {ATTRIBUTES.map(attr => {
                      const count = slot.cards.filter(code => availableCards.find(card => card.code === code)?.attrId === attr.id).length
                      return (
                        <span key={attr.id} className={`border px-2 py-1 text-[11px] font-black ${attr.accent}`}>
                          {attr.mark}{count}
                        </span>
                      )
                    })}
                  </div>

                  <div className="mt-4 max-h-28 space-y-1 overflow-y-auto border-y-2 border-dashed border-zinc-300 py-2">
                    {slotCards.slice(0, 6).map(card => (
                      <div key={card.code} className="flex items-center justify-between gap-2 text-xs font-bold text-zinc-700">
                        <span className="truncate">{card.name}</span>
                        <span className="shrink-0">x{card.copies}</span>
                      </div>
                    ))}
                    {slotCards.length > 6 && <p className="text-xs font-black text-zinc-400">还有 {slotCards.length - 6} 种卡牌...</p>}
                  </div>

                  <div className="mt-4 grid grid-cols-[1fr_1fr_36px] gap-2">
                    <button
                      type="button"
                      onClick={() => loadDeck(slot)}
                      className="flex h-9 items-center justify-center gap-1 border-2 border-zinc-950 bg-white text-xs font-black hover:bg-zinc-100"
                    >
                      <Download className="h-3.5 w-3.5" />
                      读取
                    </button>
                    <button
                      type="button"
                      onClick={() => loadDeck(slot, true)}
                      className="flex h-9 items-center justify-center gap-1 border-2 border-zinc-950 bg-zinc-950 text-xs font-black text-white hover:bg-zinc-800"
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                      组卡
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteSlot(slot.id)}
                      className="flex h-9 items-center justify-center border-2 border-zinc-950 bg-white text-red-600 hover:bg-red-50"
                      title="删除卡组"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>

                  <button
                    type="button"
                    onClick={() => overwriteSlot(slot.id)}
                    disabled={currentDeck.length === 0}
                    className="mt-2 flex h-9 w-full items-center justify-center gap-1 border-2 border-zinc-950 bg-white text-xs font-black hover:bg-zinc-100 disabled:cursor-not-allowed disabled:border-zinc-300 disabled:text-zinc-400"
                  >
                    <Save className="h-3.5 w-3.5" />
                    用当前卡组覆盖这个槽位
                  </button>
                </article>
              )
            })}

            {deckLibrary.length === 0 && (
              <div className="col-span-full flex min-h-[360px] flex-col items-center justify-center border-2 border-dashed border-orange-400 bg-white/70 p-8 text-center">
                <Archive className="h-12 w-12 text-orange-500" />
                <p className="mt-4 text-lg font-black text-zinc-800">卡组仓库还是空的</p>
                <p className="mt-2 max-w-md text-sm font-bold leading-relaxed text-zinc-500">
                  先进入组卡界面保存一套当前卡组，然后回到这里点击“存入仓库”，它就会成为一个独立卡组槽位。
                </p>
              </div>
            )}
          </div>
        </section>
      </main>
    </motion.div>
  )
}
