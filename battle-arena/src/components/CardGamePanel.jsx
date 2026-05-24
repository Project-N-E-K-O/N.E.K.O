import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, Swords, Zap, Heart, Wind, Flame, Snowflake, Star, RotateCcw, SkipForward, X, Combine, ArrowLeft, Shield, Activity, Target, Timer, BookOpen, Layers } from 'lucide-react'
import NewBattleDuelUI from './neko-brawl/NewBattleDuelUI'
import { loadForgedBrawlCards } from '../data/forgedBrawlCards'

// ─────────────────────────────────────────────────────────────────
// 卡牌属性定义
// ─────────────────────────────────────────────────────────────────
const CARD_ATTRIBUTES = [
  { id: 'passion', name: '热情', icon: Flame,     color: 'from-red-500 to-orange-400',     border: 'border-red-400/50',     text: 'text-red-300',     bg: 'bg-red-500/10' },
  { id: 'gentle',  name: '温柔', icon: Heart,     color: 'from-pink-400 to-rose-500',      border: 'border-pink-400/50',    text: 'text-pink-300',    bg: 'bg-pink-500/10' },
  { id: 'cool',    name: '高冷', icon: Snowflake, color: 'from-cyan-400 to-blue-500',      border: 'border-cyan-400/50',    text: 'text-cyan-300',    bg: 'bg-cyan-500/10' },
  { id: 'natural', name: '天然', icon: Wind,      color: 'from-emerald-400 to-teal-500',   border: 'border-emerald-400/50', text: 'text-emerald-300', bg: 'bg-emerald-500/10' },
]

// Boss 弱点/抗性生成：文档属性四类均参与 boss 弱点与 Combo 机制
const BASE_ATTRS = CARD_ATTRIBUTES

function generateBossWeakness() {
  const shuffled = [...BASE_ATTRS].sort(() => Math.random() - 0.5)
  return {
    weak:    [shuffled[0].id, shuffled[1].id],
    resist:  [shuffled[2].id],
    neutral: [shuffled[3].id],
  }
}

function generateComboAttrs(previous = []) {
  const attrIds = CARD_ATTRIBUTES.map(attr => attr.id)
  let next = []
  for (let attempt = 0; attempt < 8; attempt++) {
    next = shuffle(attrIds).slice(0, 2)
    const sameAsPrevious = next.length === previous.length && next.every(id => previous.includes(id))
    if (!sameAsPrevious) return next
  }
  return next
}

// ─────────────────────────────────────────────────────────────────
// 工具函数
// ─────────────────────────────────────────────────────────────────
function shuffle(arr) {
  const a = [...arr]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

const attrNameById = (id) => CARD_ATTRIBUTES.find(a => a.id === id)?.name || id
const cardCost = (card) => card?.cost ?? Math.max(1, Math.ceil((card?.power || 0) / 3))
const INITIAL_PLAYER_ENERGY = 3
const ENERGY_RECOVER_PER_ROUND = 1

const LOG_HIGHLIGHT_RULES = [
  { pattern: /造成\s*\d+\s*点(?:（[^）]+）)?|受到\s*\d+\s*点伤害|追加伤害\s*\+\d+/y, className: 'font-black text-red-300' },
  { pattern: /回复低生命角色\s*\d+|(?:你|队友|双方)回复\s*\d+/y, className: 'font-black text-emerald-300' },
  { pattern: /(?:你|队友|双方)?护盾\s*\+\d+|护盾抵消\s*\d+/y, className: 'font-black text-sky-300' },
  { pattern: /Combo(?:效果)?：[^；]+|连续Combo\s*\d+\s*回合/y, className: 'font-black text-fuchsia-300' },
  { pattern: /抽\s*\d+/y, className: 'font-black text-cyan-300' },
  { pattern: /封锁Boss下回合行动|行动被封锁/y, className: 'font-black text-violet-300' },
  { pattern: /弱化|只能出1张牌/y, className: 'font-black text-amber-300' },
  { pattern: /Boss生命上限\s*\+\d+/y, className: 'font-black text-rose-300' },
  { pattern: /能量不足|跳过出牌|无牌可出/y, className: 'font-black text-orange-300' },
]

function renderBattleLogText(text) {
  const source = String(text || '')
  const nodes = []
  let index = 0

  while (index < source.length) {
    let matched = null
    for (const rule of LOG_HIGHLIGHT_RULES) {
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

// ─────────────────────────────────────────────────────────────────
// 卡牌生成
// ─────────────────────────────────────────────────────────────────
const CARD_POOL = [
  { code: 'C001', name: '午后扑抱', attrId: 'passion', cost: 1, mainText: '对Boss造成1点伤害', comboText: '额外造成1点伤害', main: { damage: 1 }, combo: { damage: 1 } },
  { code: 'C002', name: '亮晶晶眼神', attrId: 'gentle', cost: 1, mainText: '回复生命最低的己方玩家1点生命', comboText: '自身回复1点生命', main: { healLowest: 1 }, combo: { healSelf: 1 } },
  { code: 'C003', name: '尾巴在说话', attrId: 'cool', cost: 1, mainText: '为自己获得1点护盾', comboText: '为队友提供1点护盾', main: { shieldSelf: 1 }, combo: { shieldOther: 1 } },
  { code: 'C004', name: '云朵经过的三秒', attrId: 'natural', cost: 1, mainText: '抽1张牌', comboText: '额外抽1张牌', main: { draw: 1 }, combo: { draw: 1 } },
  { code: 'C005', name: '还没认输呢', attrId: 'passion', cost: 2, mainText: '对Boss造成2点伤害', comboText: '额外造成1点伤害', main: { damage: 2 }, combo: { damage: 1 } },
  { code: 'C006', name: '怀中心跳', attrId: 'cool', cost: 2, mainText: '本回合Boss对自己造成的伤害-2', comboText: '队友本回合受到的伤害-2', main: { reduceSelfDamageThisRound: 2 }, combo: { reduceOtherDamageThisRound: 2 } },
  { code: 'C007', name: '熬夜到头秃', attrId: 'cool', cost: 2, mainText: '下回合造成伤害+2', comboText: '获得2点护盾', main: { damageBonusNext: 2 }, combo: { shieldSelf: 2 } },
  { code: 'C008', name: '拂面微风', attrId: 'natural', cost: 2, mainText: '双方玩家各回复1点生命', comboText: '额外为双方各获得1点护盾', main: { healBoth: 1 }, combo: { shieldBoth: 1 } },
  { code: 'C009', name: '纸箱里的秘密计划', attrId: 'gentle', cost: 2, mainText: '对Boss造成1点伤害，并使Boss下次攻击伤害-1', comboText: '额外造成1点伤害', main: { damage: 1, bossDamageReductionNext: 1 }, combo: { damage: 1 } },
  { code: 'C010', name: '屋顶上的晚安', attrId: 'cool', cost: 3, mainText: '回复双方玩家各2点生命', comboText: '清除1个负面状态', main: { healBoth: 2 }, combo: { clearDebuff: 1 } },
  { code: 'C011', name: '生人勿近', attrId: 'natural', cost: 3, mainText: '对Boss造成2点伤害，并为双方各获得1点护盾', comboText: '本回合Boss造成伤害-1', main: { damage: 2, shieldBoth: 1 }, combo: { bossDamageReductionThisRound: 1 } },
  { code: 'C012', name: '用尽全力奔向你', attrId: 'gentle', cost: 3, mainText: '对Boss造成4点伤害', comboText: '额外造成2点伤害', main: { damage: 4 }, combo: { damage: 2 } },
  { code: 'C013', name: '完全⭐奇迹', attrId: 'passion', cost: 4, mainText: '对Boss造成3点伤害，并封锁boss下回合行动', comboText: '自身获得2点护盾', main: { damage: 3, skipBossNext: true }, combo: { shieldSelf: 2 } },
]

const SAVED_DECK_STORAGE_KEY = 'neko-brawl-deck'

let cardIdCounter = 0
function getAvailableCardPool() {
  const forgedCards = loadForgedBrawlCards()
  return [...CARD_POOL, ...forgedCards]
}

function makeCardFromDefinition(def) {
  const attr = CARD_ATTRIBUTES.find(a => a.id === def.attrId) || CARD_ATTRIBUTES[0]
  const comboAttr = CARD_ATTRIBUTES.find(a => a.id === (def.comboAttrId || def.attrId)) || attr
  const power = def.main.damage || def.cost
  cardIdCounter++
  return {
    id: `card-${cardIdCounter}`,
    code: def.code,
    baseCode: def.baseCode || def.code,
    forged: Boolean(def.forged),
    name: def.name,
    attr,
    comboAttr,
    cost: def.cost,
    power,
    mainText: def.mainText,
    comboText: def.comboText,
    effects: { main: { ...def.main }, combo: { ...def.combo } },
    story: def.story,
    summary: def.summary,
    sourceEventName: def.sourceEventName,
    debuffs: [],
  }
}

function makeCard(code) {
  const availableCards = getAvailableCardPool()
  const def = code
    ? availableCards.find(card => card.code === code)
    : availableCards[Math.floor(Math.random() * availableCards.length)]
  return makeCardFromDefinition(def || availableCards[Math.floor(Math.random() * availableCards.length)])
}

function makeDeck(size) {
  return Array.from({ length: size }, () => makeCard())
}

function readSavedDeckCodes() {
  if (typeof window === 'undefined') return []
  try {
    const saved = JSON.parse(window.localStorage.getItem(SAVED_DECK_STORAGE_KEY) || '[]')
    if (!Array.isArray(saved)) return []
    const availableCards = getAvailableCardPool()
    return saved.filter(code => availableCards.some(card => card.code === code))
  } catch {
    return []
  }
}

function makePlayerDeck() {
  const savedCodes = readSavedDeckCodes()
  if (savedCodes.length === 0) return makeDeck(12)
  return shuffle(savedCodes.map(code => makeCard(code)))
}

// ─────────────────────────────────────────────────────────────────
// Boss 数据
// ─────────────────────────────────────────────────────────────────
const BOSS_LIST = [
  { name: '混沌猫灵',  emoji: '😈', maxTurns: 8  },
  { name: '暗影喵王',  emoji: '👹', maxTurns: 10 },
  { name: '虚空猫神',  emoji: '🐲', maxTurns: 6  },
]

// Boss 行动模式
const BOSS_ACTIONS = [
  { id: 'debuff_first',  name: '先手弱化', desc: '弱化先行动者，出牌数-1' },
  { id: 'debuff_last',   name: '末位弱化', desc: '弱化最后行动者的最后一张牌效果' },
  { id: 'weaken_next',   name: '诅咒',    desc: '下回合所有人第一张出牌效果减弱' },
  { id: 'big_attack',    name: '猛攻',    desc: '对全体施压，击破目标+2' },
  { id: 'nothing',       name: '观望',    desc: '什么都不做' },
]

const BOSS_IMAGE_DURATION_MS = 1000
const BOSS_DAMAGE_POPUP_DURATION_MS = 1200
const COMBO_POPUP_DURATION_MS = 900
const MAX_PLAYER_HP = 6
const BOSS_MAX_HP = 30
const BOSS_MAX_TURNS = 20
const BOSS_IMAGE_SOURCES = {
  normal: '/neko-brawl/Boss_normal_transparent.png?v=transparent-bg-restore',
  attack: '/neko-brawl/Boss_attack_transparent.png?v=transparent-bg-restore',
  damageTaken: '/neko-brawl/Boss_damagetaken_transparent.png?v=transparent-bg-restore',
  weakDamageTaken: '/neko-brawl/Boss_WeakDamageTaken_transparent.png?v=transparent-bg-restore',
}

// ─────────────────────────────────────────────────────────────────
// 单张卡牌 UI
// ─────────────────────────────────────────────────────────────────
function CardUI({ card, onClick, selected, small, faceDown, disabled }) {
  const AttrIcon = card?.attr?.icon || Star
  if (faceDown) {
    return (
      <motion.div
        layout
        className={`${small ? 'w-14 h-20' : 'w-20 h-28'} rounded-xl border border-white/10 bg-gradient-to-br from-slate-700 to-slate-800 
          flex items-center justify-center shadow-lg cursor-default select-none`}
      >
        <span className="text-xl opacity-30">🃏</span>
      </motion.div>
    )
  }
  const hasDebuff = card.debuffs && card.debuffs.length > 0
  return (
    <motion.div
      layout
      whileHover={!disabled ? { y: -6, scale: 1.06 } : {}}
      whileTap={!disabled ? { scale: 0.95 } : {}}
      onClick={!disabled ? onClick : undefined}
      className={`${small ? 'w-14 h-20' : 'w-20 h-28'} rounded-xl border-2 
        ${selected ? 'border-amber-400 ring-2 ring-amber-400/40 shadow-amber-500/30 shadow-lg' : hasDebuff ? 'border-red-500/50' : card.attr.border}
        bg-gradient-to-br from-slate-800/90 to-slate-900/90 backdrop-blur
        flex flex-col items-center justify-center gap-1 
        ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer hover:shadow-xl'}
        transition-shadow select-none relative overflow-hidden`}
    >
      <div className={`absolute inset-0 bg-gradient-to-br ${card.attr.color} opacity-10`} />
      <AttrIcon className={`${small ? 'w-4 h-4' : 'w-6 h-6'} ${card.attr.text} relative z-10`} />
      <span className={`${small ? 'text-[10px]' : 'text-xs'} font-bold ${card.attr.text} relative z-10`}>
        {card.attr.name}
      </span>
      <span className={`${small ? 'text-[10px]' : 'text-sm'} font-black text-white relative z-10`}>
        {card.power}
      </span>
      {hasDebuff && (
        <span className="absolute top-0.5 right-0.5 text-[8px] text-red-400">⬇</span>
      )}
      {card.temp && (
        <span className="absolute top-0.5 left-0.5 text-[8px] text-amber-400">✦</span>
      )}
    </motion.div>
  )
}

// ─────────────────────────────────────────────────────────────────
// 行动条（顶部）：当前回合 | 分隔 | 下回合（右侧）
// ─────────────────────────────────────────────────────────────────
function ActionBar({ order, currentIdx, roundLabel, nextOrder, roundKey }) {
  const colorMap = {
    boss:   { active: 'bg-red-500/30 border-red-400/60 text-red-200 shadow-red-500/20',
              wait:   'bg-red-500/10 border-red-500/20 text-red-400/80',
              ghost:  'bg-red-900/10 border-red-900/20 text-red-600/30' },
    player: { active: 'bg-violet-500/30 border-violet-400/60 text-violet-200 shadow-violet-500/20',
              wait:   'bg-violet-500/10 border-violet-500/20 text-violet-400/80',
              ghost:  'bg-violet-900/10 border-violet-900/20 text-violet-600/30' },
    ally:   { active: 'bg-sky-500/30 border-sky-400/60 text-sky-200 shadow-sky-500/20',
              wait:   'bg-sky-500/10 border-sky-500/20 text-sky-400/80',
              ghost:  'bg-sky-900/10 border-sky-900/20 text-sky-600/30' },
  }
  const style = (actor, isCurrent, isPast) => {
    const c = colorMap[actor.type] || colorMap.ally
    return isCurrent ? c.active : isPast ? c.ghost : c.wait
  }

  return (
    <div className="flex items-center gap-2">
      {/* ── 当前回合 ── */}
      <div className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-black/30 border border-white/10 min-w-0">
        <span className="text-[10px] text-gray-400 font-semibold shrink-0 mr-1">{roundLabel}</span>
        <AnimatePresence mode="popLayout">
          {order.map((actor, i) => (
            i < currentIdx ? null : (
              <motion.div
                key={`cur-${roundKey}-${actor.id}-${i}`}
                layout
                initial={{ opacity: 0, x: 30, scale: 0.8 }}
                animate={i === currentIdx
                  ? { opacity: 1, x: 0, scale: [1, 1.12, 1], transition: { scale: { repeat: Infinity, duration: 1.4 } } }
                  : { opacity: 1, x: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.5, y: -16, transition: { duration: 0.25 } }}
                transition={{ type: 'spring', stiffness: 300, damping: 25 }}
                className={`px-2.5 py-1 rounded-lg text-[11px] font-bold border shadow-sm whitespace-nowrap ${style(actor, i === currentIdx, i < currentIdx)}`}
              >
                {actor.emoji} {actor.name}
              </motion.div>
            )
          ))}
          {order.length > 0 && currentIdx >= order.length && (
            <motion.span
              key="done"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-[10px] text-gray-500 italic"
            >
              回合结束
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      {/* ── 分隔箭头 ── */}
      {nextOrder && nextOrder.length > 0 && (
        <span className="text-gray-600 text-xs select-none">▸</span>
      )}

      {/* ── 下回合预览 ── */}
      {nextOrder && nextOrder.length > 0 && (
        <div className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-black/15 border border-white/5 opacity-60 min-w-0">
          <span className="text-[9px] text-gray-500 font-semibold shrink-0 mr-1">Next</span>
          <AnimatePresence mode="popLayout">
            {nextOrder.map((actor, i) => (
              <motion.div
                key={`next-${actor.id}-${i}`}
                layout
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ type: 'spring', stiffness: 260, damping: 22, delay: i * 0.05 }}
                className={`px-2 py-0.5 rounded-md text-[10px] font-medium border whitespace-nowrap ${(colorMap[actor.type] || colorMap.ally).wait}`}
              >
                {actor.emoji} {actor.name}
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────
// 牌堆按钮（抽牌堆 / 弃牌堆）
// ─────────────────────────────────────────────────────────────────
function PileButton({ count, label, onClick, variant = 'deck' }) {
  const isDeck = variant === 'deck'
  return (
    <motion.button
      whileHover={{ scale: 1.08 }}
      whileTap={{ scale: 0.95 }}
      onClick={onClick}
      className="relative flex flex-col items-center gap-1 group"
    >
      {/* 数量徽章 */}
      <span className={`absolute -top-2 -right-2 z-10 min-w-[20px] h-5 flex items-center justify-center rounded-full text-[10px] font-black shadow
        ${isDeck ? 'bg-violet-500 text-white' : 'bg-gray-500 text-white'}`}>
        {count}
      </span>
      {/* 牌堆图示 */}
      <div className={`w-14 h-20 rounded-xl border-2 relative overflow-hidden transition-all
        ${isDeck
          ? 'border-violet-400/50 bg-gradient-to-br from-violet-900/80 to-indigo-900/80 group-hover:border-violet-300/70 group-hover:shadow-violet-500/20 group-hover:shadow-lg'
          : 'border-gray-500/40 bg-gradient-to-br from-gray-800/80 to-gray-900/80 group-hover:border-gray-400/60 group-hover:shadow-gray-400/10 group-hover:shadow-lg'
        }`}
      >
        {count > 0 ? (
          <>
            {/* 叠放效果 */}
            {count > 2 && <div className="absolute inset-0 translate-x-[3px] -translate-y-[3px] rounded-xl border border-white/5 bg-slate-800/30" />}
            {count > 1 && <div className="absolute inset-0 translate-x-[1.5px] -translate-y-[1.5px] rounded-xl border border-white/5 bg-slate-800/50" />}
            <div className="relative w-full h-full flex items-center justify-center">
              <span className="text-2xl opacity-50">{isDeck ? '🂠' : '🃏'}</span>
            </div>
          </>
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="text-xs text-gray-600">空</span>
          </div>
        )}
      </div>
      <span className={`text-[9px] font-medium ${isDeck ? 'text-violet-400' : 'text-gray-400'}`}>{label}</span>
    </motion.button>
  )
}

// ─────────────────────────────────────────────────────────────────
// 牌堆查看弹窗
// ─────────────────────────────────────────────────────────────────
function DeckPeekModal({ title, cards, onClose }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[110] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.85, opacity: 0, y: 20 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.85, opacity: 0, y: 20 }}
        transition={{ type: 'spring', stiffness: 300, damping: 25 }}
        onClick={e => e.stopPropagation()}
        className="bg-gradient-to-br from-slate-900 to-slate-800 border border-white/10 rounded-2xl shadow-2xl p-4 max-w-md w-[90vw] max-h-[60vh] flex flex-col"
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-bold text-white">{title}</h3>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>
        {cards.length === 0 ? (
          <p className="text-xs text-gray-500 text-center py-8">空空如也~</p>
        ) : (
          <div className="flex-1 overflow-y-auto pr-1">
            <div className="flex flex-wrap gap-2 justify-center">
              {cards.map((card, idx) => (
                <CardUI key={`peek-${card.id}-${idx}`} card={card} small disabled />
              ))}
            </div>
          </div>
        )}
        <div className="mt-3 pt-2 border-t border-white/5 text-center">
          <span className="text-[10px] text-gray-500">共 {cards.length} 张</span>
        </div>
      </motion.div>
    </motion.div>
  )
}

// ─────────────────────────────────────────────────────────────────
// 初始化一局游戏的完整状态
// ─────────────────────────────────────────────────────────────────
function createInitialState() {
  const b = BOSS_LIST[Math.floor(Math.random() * BOSS_LIST.length)]
  return {
    boss: { ...b, maxTurns: BOSS_MAX_TURNS, weakness: generateBossWeakness(), breakPoints: 0, breakGoal: BOSS_MAX_HP },
    myDeck: makePlayerDeck(),
    myHand: [],
    myDiscard: [],
    allyDeck: makeDeck(12),
    allyHand: [],
    allyDiscard: [],
  }
}

// ─────────────────────────────────────────────────────────────────
// 主组件
// ─────────────────────────────────────────────────────────────────
export default function CardGamePanel({ onClose, nekoName, nekoAvatar }) {
  const cardReferencePool = useMemo(() => getAvailableCardPool(), [])
  // 游戏配置
  const DRAW_COUNT = 5
  const HAND_LIMIT = 6
  const MAX_PLAY = 1

  // 游戏状态
  const [phase, setPhase] = useState('prep') // prep, playing, win, lose
  const [useNewUi, setUseNewUi] = useState(true)
  const [round, setRound] = useState(1)
  const [boss, setBoss] = useState(() => {
    const b = BOSS_LIST[Math.floor(Math.random() * BOSS_LIST.length)]
    return { ...b, maxTurns: BOSS_MAX_TURNS, weakness: generateBossWeakness(), breakPoints: 0, breakGoal: BOSS_MAX_HP }
  })
  const [comboAttrs, setComboAttrs] = useState(() => generateComboAttrs())
  const [bossImageState, setBossImageState] = useState('normal')
  const [bossDamagePopup, setBossDamagePopup] = useState(null)
  const [comboPopup, setComboPopup] = useState(null)
  const bossImageTimerRef = useRef(null)
  const bossDamagePopupTimerRef = useRef(null)
  const comboPopupTimerRef = useRef(null)
  const comboVisualChainRef = useRef(0)
  const [playerHp, setPlayerHp] = useState(MAX_PLAYER_HP)
  const [allyHp, setAllyHp] = useState(MAX_PLAYER_HP)
  const [playerShield, setPlayerShield] = useState(0)
  const [allyShield, setAllyShield] = useState(0)
  const [playerDamageBonusNext, setPlayerDamageBonusNext] = useState(0)
  const [allyDamageBonusNext, setAllyDamageBonusNext] = useState(0)
  const [playerEnergy, setPlayerEnergy] = useState(INITIAL_PLAYER_ENERGY)
  const [allyEnergyValue, setAllyEnergyValue] = useState(INITIAL_PLAYER_ENERGY)
  const [playerIncomingReduce, setPlayerIncomingReduce] = useState(0)
  const [allyIncomingReduce, setAllyIncomingReduce] = useState(0)
  const [bossDamageReduction, setBossDamageReduction] = useState(0)
  const [bossSkipNext, setBossSkipNext] = useState(false)
  const comboStatsRef = useRef({ total: 0, streak: 0, best: 0, lastRound: 0 })
  const [playedCardStats, setPlayedCardStats] = useState([])
  const [turnOrder, setTurnOrder] = useState([])
  const [nextTurnOrder, setNextTurnOrder] = useState([])
  const [turnIdx, setTurnIdx] = useState(0)

  // 全局debuff标记
  const [curseNextRound, setCurseNextRound] = useState(false) // 诅咒：下回合所有人第一张牌效果减弱

  // 玩家
  const [myDeck, setMyDeck] = useState(() => makePlayerDeck())
  const [myHand, setMyHand] = useState([])
  const [myDiscard, setMyDiscard] = useState([])
  const [myPlayed, setMyPlayed] = useState([])
  const [mySelected, setMySelected] = useState([])
  const [myMaxPlay, setMyMaxPlay] = useState(MAX_PLAY)

  // 队友 (AI)
  const [allyDeck, setAllyDeck] = useState(() => makeDeck(12))
  const [allyHand, setAllyHand] = useState([])
  const [allyDiscard, setAllyDiscard] = useState([])
  const [allyPlayed, setAllyPlayed] = useState([])
  const [allyMaxPlay, setAllyMaxPlay] = useState(MAX_PLAY)

  // 牌堆查看弹窗
  const [peekPile, setPeekPile] = useState(null) // null | 'myDeck' | 'myDiscard'

  // ── 拖拽系统 ──
  const [dragState, setDragState] = useState(null) // { cardId, startX, startY, x, y }
  const [dragOverTarget, setDragOverTarget] = useState(null) // 'boss' | cardId | null
  const bossAreaRef = useRef(null)
  const handCardRefs = useRef({}) // { [cardId]: HTMLElement }
  const panelRef = useRef(null)

  // 日志
  const [gameLog, setGameLog] = useState([])
  const logRef = useRef(null)

  const addActionLog = useCallback((text, type = 'info') => {
    setGameLog(prev => [...prev, { text, type, id: Date.now() + Math.random() }])
  }, [])

  const joinActionParts = useCallback((...groups) => {
    return groups.flat().filter(Boolean).join('；')
  }, [])

  const showBossImageState = useCallback((state) => {
    if (bossImageTimerRef.current) clearTimeout(bossImageTimerRef.current)
    setBossImageState(state)

    if (state !== 'normal') {
      bossImageTimerRef.current = setTimeout(() => {
        setBossImageState('normal')
        bossImageTimerRef.current = null
      }, BOSS_IMAGE_DURATION_MS)
    }
  }, [])

  const showBossDamagePopup = useCallback((amount, weak) => {
    if (bossDamagePopupTimerRef.current) clearTimeout(bossDamagePopupTimerRef.current)
    setBossDamagePopup({
      id: Date.now() + Math.random(),
      amount,
      weak,
    })

    bossDamagePopupTimerRef.current = setTimeout(() => {
      setBossDamagePopup(null)
      bossDamagePopupTimerRef.current = null
    }, BOSS_DAMAGE_POPUP_DURATION_MS)
  }, [])

  const showComboPopup = useCallback((count) => {
    if (comboPopupTimerRef.current) clearTimeout(comboPopupTimerRef.current)
    setComboPopup({
      id: Date.now() + Math.random(),
      count,
      sizeLevel: Math.min(5, count),
    })

    comboPopupTimerRef.current = setTimeout(() => {
      setComboPopup(null)
      comboPopupTimerRef.current = null
    }, COMBO_POPUP_DURATION_MS)
  }, [])

  const resetComboVisualChain = useCallback(() => {
    comboVisualChainRef.current = 0
  }, [])

  useEffect(() => {
    return () => {
      if (bossImageTimerRef.current) clearTimeout(bossImageTimerRef.current)
      if (bossDamagePopupTimerRef.current) clearTimeout(bossDamagePopupTimerRef.current)
      if (comboPopupTimerRef.current) clearTimeout(comboPopupTimerRef.current)
    }
  }, [])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [gameLog])

  // ── 从牌库抽牌，牌库不够时洗入弃牌堆 ──
  const drawCards = useCallback((deck, discard, count) => {
    let pool = [...deck]
    let disc = [...discard]
    if (pool.length < count) {
      pool = [...pool, ...shuffle(disc)]
      disc = []
    }
    const drawn = pool.slice(0, count)
    const remaining = pool.slice(count)
    return { drawn, deck: remaining, discard: disc }
  }, [])

  // ── 生成随机行动顺序 ──
  const makeActors = useCallback(() => {
    return [
      { id: 'boss', type: 'boss', name: boss.name, emoji: boss.emoji },
      { id: 'player', type: 'player', name: nekoName || '我方猫娘', emoji: '🐱' },
      { id: 'ally', type: 'ally', name: '队友猫娘', emoji: '🐈' },
    ].sort(() => Math.random() - 0.5)
  }, [boss, nekoName])

  // ── 回合开始（同步处理牌库循环 + 手牌上限） ──
  const doStartRound = useCallback(() => {
    const cursed = curseNextRound
    setCurseNextRound(false)

    // 玩家抽牌（先清除上回合残留的临时卡）
    const prevHand = myHand.filter(c => !c.temp)
    const myRes = drawCards(myDeck, myDiscard, DRAW_COUNT)
    let newMyHand = [...prevHand, ...myRes.drawn]
    let newMyDisc = myRes.discard
    if (cursed && newMyHand.length > 0) {
      newMyHand[0] = { ...newMyHand[0], debuffs: [...newMyHand[0].debuffs, 'weaken'] }
    }
    if (newMyHand.length > HAND_LIMIT) {
      const overflow = newMyHand.slice(HAND_LIMIT)
      const normalOverflow = overflow.filter(c => !c.temp)
      if (normalOverflow.length > 0) newMyDisc = [...newMyDisc, ...normalOverflow]
      newMyHand = newMyHand.slice(0, HAND_LIMIT)
    }
    setMyDeck(myRes.deck)
    setMyDiscard(newMyDisc)
    setMyHand(newMyHand)

    // 队友抽牌
    const allyRes = drawCards(allyDeck, allyDiscard, DRAW_COUNT)
    let newAllyHand = [...allyHand, ...allyRes.drawn]
    let newAllyDisc = allyRes.discard
    if (cursed && newAllyHand.length > 0) {
      newAllyHand[0] = { ...newAllyHand[0], debuffs: [...newAllyHand[0].debuffs, 'weaken'] }
    }
    if (newAllyHand.length > HAND_LIMIT) {
      const overflow = newAllyHand.slice(HAND_LIMIT)
      newAllyDisc = [...newAllyDisc, ...overflow]
      newAllyHand = newAllyHand.slice(0, HAND_LIMIT)
    }
    setAllyDeck(allyRes.deck)
    setAllyDiscard(newAllyDisc)
    setAllyHand(newAllyHand)

    // 行动顺序：使用上回合预告的 nextTurnOrder，没有则随机
    const actors = nextTurnOrder.length > 0 ? nextTurnOrder : makeActors()
    const nextActors = makeActors()
    const nextComboAttrs = generateComboAttrs(comboAttrs)
    setTurnOrder(actors)
    setNextTurnOrder(nextActors)
    setTurnIdx(0)
    setMyPlayed([])
    setAllyPlayed([])
    setMySelected([])
    setMyMaxPlay(MAX_PLAY)
    setAllyMaxPlay(MAX_PLAY)
    setPlayerEnergy(prev => round <= 1 ? INITIAL_PLAYER_ENERGY : Math.max(0, prev) + ENERGY_RECOVER_PER_ROUND)
    setAllyEnergyValue(prev => round <= 1 ? INITIAL_PLAYER_ENERGY : Math.max(0, prev) + ENERGY_RECOVER_PER_ROUND)
    setComboAttrs(nextComboAttrs)
    setPlayerIncomingReduce(0)
    setAllyIncomingReduce(0)
    setBossDamageReduction(0)
    setPhase('playing')
    addActionLog(`———— 回合 ${round} —————`, 'round')
  }, [curseNextRound, myDeck, myDiscard, myHand, allyDeck, allyDiscard, allyHand, drawCards, makeActors, nextTurnOrder, comboAttrs, round, addActionLog, DRAW_COUNT, HAND_LIMIT, MAX_PLAY])

  // 用 ref 保持 doStartRound 最新引用，避免 setTimeout 捕获旧闭包
  const doStartRoundRef = useRef(doStartRound)
  useEffect(() => { doStartRoundRef.current = doStartRound }, [doStartRound])

  // 首次开始
  const hasStarted = useRef(false)
  useEffect(() => {
    if (!hasStarted.current) {
      hasStarted.current = true
      doStartRound()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── 当前行动者 ──
  const currentActor = turnOrder[turnIdx]

  // ── 计算伤害 ──
  const calcDamage = useCallback((card, rawDamage = card.power) => {
    let dmg = rawDamage
    const attrId = card.attr.id
    if (boss.weakness.weak.includes(attrId)) dmg = Math.floor(dmg * 1.5)
    else if (boss.weakness.resist.includes(attrId)) dmg = Math.floor(dmg * 0.5)
    card.debuffs.forEach(d => {
      if (d === 'weaken') dmg = Math.floor(dmg * 0.6)
    })
    return Math.max(1, dmg)
  }, [boss])

  const registerCombo = useCallback((count = 1) => {
    const prev = comboStatsRef.current
    const sameRound = prev.lastRound === round
    const nextStreak = sameRound ? prev.streak : prev.lastRound === round - 1 ? prev.streak + 1 : 1
    const next = {
      total: prev.total + count,
      streak: nextStreak,
      best: Math.max(prev.best, nextStreak),
      lastRound: round,
    }
    comboStatsRef.current = next
    return next
  }, [round])

  const sideLabel = useCallback((side) => side === 'player' ? '你' : '队友', [])

  const describeEffect = useCallback((effect = {}, owner = 'player') => {
    const other = owner === 'player' ? 'ally' : 'player'
    const parts = []
    if (effect.healLowest) parts.push(`回复低生命角色 ${effect.healLowest}`)
    if (effect.healSelf) parts.push(`${sideLabel(owner)}回复 ${effect.healSelf}`)
    if (effect.healBoth) parts.push(`双方回复 ${effect.healBoth}`)
    if (effect.shieldSelf) parts.push(`${sideLabel(owner)}护盾 +${effect.shieldSelf}`)
    if (effect.shieldOther) parts.push(`${sideLabel(other)}护盾 +${effect.shieldOther}`)
    if (effect.shieldBoth) parts.push(`双方护盾 +${effect.shieldBoth}`)
    if (effect.draw) parts.push(`${sideLabel(owner)}抽 ${effect.draw}`)
    if (effect.reduceSelfDamageThisRound) parts.push(`${sideLabel(owner)}受Boss伤害 -${effect.reduceSelfDamageThisRound}`)
    if (effect.reduceOtherDamageThisRound) parts.push(`${sideLabel(other)}受Boss伤害 -${effect.reduceOtherDamageThisRound}`)
    if (effect.damageBonusNext) parts.push(`${sideLabel(owner)}下次伤害 +${effect.damageBonusNext}`)
    if (effect.bossDamageReductionNext) parts.push(`Boss下次攻击 -${effect.bossDamageReductionNext}`)
    if (effect.bossDamageReductionThisRound) parts.push(`Boss本回合伤害 -${effect.bossDamageReductionThisRound}`)
    if (effect.skipBossNext) parts.push('封锁Boss下回合行动')
    if (effect.clearDebuff) parts.push(`${sideLabel(owner)}清除负面状态`)
    return parts
  }, [sideLabel])

  const healSide = useCallback((side, amount) => {
    const setter = side === 'player' ? setPlayerHp : setAllyHp
    setter(prev => Math.min(MAX_PLAYER_HP, prev + amount))
  }, [])

  const shieldSide = useCallback((side, amount) => {
    const setter = side === 'player' ? setPlayerShield : setAllyShield
    setter(prev => prev + amount)
  }, [])

  const drawForSide = useCallback((side, count) => {
    const setDeck = side === 'player' ? setMyDeck : setAllyDeck
    const setHand = side === 'player' ? setMyHand : setAllyHand
    setDeck(prev => {
      const drawn = prev.slice(0, count)
      if (drawn.length > 0) {
        setHand(hand => [...hand, ...drawn])
      }
      return prev.slice(drawn.length)
    })
  }, [])

  const clearOneDebuff = useCallback((side) => {
    const setHand = side === 'player' ? setMyHand : setAllyHand
    let cleared = false
    setHand(prev => prev.map(card => {
      if (cleared || !card.debuffs?.length) return card
      cleared = true
      return { ...card, debuffs: [] }
    }))
  }, [])

  const addIncomingReduce = useCallback((side, amount) => {
    const setter = side === 'player' ? setPlayerIncomingReduce : setAllyIncomingReduce
    setter(prev => prev + amount)
  }, [])

  const applyCardEffectBlock = useCallback((effect, owner) => {
    const other = owner === 'player' ? 'ally' : 'player'
    let rawDamage = effect.damage || 0

    if (effect.healLowest) healSide(playerHp <= allyHp ? 'player' : 'ally', effect.healLowest)
    if (effect.healSelf) healSide(owner, effect.healSelf)
    if (effect.healBoth) {
      healSide('player', effect.healBoth)
      healSide('ally', effect.healBoth)
    }
    if (effect.shieldSelf) shieldSide(owner, effect.shieldSelf)
    if (effect.shieldOther) shieldSide(other, effect.shieldOther)
    if (effect.shieldBoth) {
      shieldSide('player', effect.shieldBoth)
      shieldSide('ally', effect.shieldBoth)
    }
    if (effect.draw) drawForSide(owner, effect.draw)
    if (effect.reduceSelfDamageThisRound) addIncomingReduce(owner, effect.reduceSelfDamageThisRound)
    if (effect.reduceOtherDamageThisRound) addIncomingReduce(other, effect.reduceOtherDamageThisRound)
    if (effect.damageBonusNext) {
      const setter = owner === 'player' ? setPlayerDamageBonusNext : setAllyDamageBonusNext
      setter(prev => prev + effect.damageBonusNext)
    }
    if (effect.bossDamageReductionNext) {
      setBossDamageReduction(prev => prev + effect.bossDamageReductionNext)
    }
    if (effect.bossDamageReductionThisRound) {
      setBossDamageReduction(prev => prev + effect.bossDamageReductionThisRound)
    }
    if (effect.skipBossNext) {
      setBossSkipNext(true)
    }
    if (effect.clearDebuff) clearOneDebuff(owner)

    return rawDamage
  }, [addIncomingReduce, allyHp, clearOneDebuff, drawForSide, healSide, playerHp, shieldSide])

  const resolvePlayedCard = useCallback((card, owner) => {
    const comboActive = comboAttrs.includes(card.comboAttr?.id || card.attr.id)
    const mainEffect = card.effects?.main || {}
    const comboEffect = card.effects?.combo || {}
    let rawDamage = applyCardEffectBlock(mainEffect, owner)
    let comboInfo = null
    const actionType = owner === 'player' ? 'player' : 'ally'
    const actionLabel = sideLabel(owner)
    const detailParts = [...describeEffect(mainEffect, owner)]

    if (comboActive) {
      comboInfo = registerCombo(1)
      comboVisualChainRef.current += 1
      showComboPopup(comboVisualChainRef.current)
      rawDamage += applyCardEffectBlock(comboEffect, owner)
      const comboEffectParts = describeEffect(comboEffect, owner)
      detailParts.push(`Combo：${card.comboText}`)
      if (comboEffectParts.length > 0) detailParts.push(`Combo效果：${comboEffectParts.join('、')}`)
    } else {
      resetComboVisualChain()
    }

    const pendingBonus = owner === 'player' ? playerDamageBonusNext : allyDamageBonusNext
    if (rawDamage > 0 && pendingBonus > 0) {
      rawDamage += pendingBonus
      const setter = owner === 'player' ? setPlayerDamageBonusNext : setAllyDamageBonusNext
      setter(0)
      detailParts.push(`追加伤害 +${pendingBonus}`)
    }

    if (rawDamage > 0 && comboInfo?.streak > 1) {
      const streakBonus = Math.min(3, comboInfo.streak - 1)
      rawDamage += streakBonus
      detailParts.push(`连续Combo ${comboInfo.streak} 回合，追加伤害 +${streakBonus}`)
    }

    const dmg = rawDamage > 0 ? calcDamage(card, rawDamage) : 0
    const relation = boss.weakness.weak.includes(card.attr.id) ? '（弱点）' :
                     boss.weakness.resist.includes(card.attr.id) ? '（抗性）' :
                     card.attr.id === 'spirit' ? '（灵）' : ''
    const resultText = dmg > 0 ? `造成 ${dmg} 点${relation}` : '支援'
    const effectSummary = joinActionParts(resultText, detailParts)
    addActionLog(`${actionLabel} 打出 [${card.code} ${card.name}]：${effectSummary}`, actionType)
    return { damage: dmg, comboActive, comboStreak: comboInfo?.streak || 0, effectSummary }
  }, [addActionLog, allyDamageBonusNext, applyCardEffectBlock, boss, calcDamage, comboAttrs, describeEffect, joinActionParts, playerDamageBonusNext, registerCombo, resetComboVisualChain, showComboPopup, sideLabel])

  const recordPlayedCardStats = useCallback((cards, owner, results) => {
    if (!cards.length) return
    const stamp = Date.now()
    setPlayedCardStats(prev => [
      ...prev,
      ...cards.map((card, index) => ({
        id: `${card.id || card.code}-${owner}-${round}-${stamp}-${index}`,
        cardSnapshot: {
          ...card,
          attr: card.attr ? { ...card.attr } : card.attr,
          comboAttr: card.comboAttr ? { ...card.comboAttr } : card.comboAttr,
          debuffs: Array.isArray(card.debuffs) ? [...card.debuffs] : [],
          effects: {
            main: { ...(card.effects?.main || {}) },
            combo: { ...(card.effects?.combo || {}) },
          },
        },
        owner,
        damage: results[index]?.damage || 0,
        comboActive: Boolean(results[index]?.comboActive),
        comboStreak: results[index]?.comboStreak || 0,
        effectSummary: results[index]?.effectSummary || '',
        round,
      })),
    ])
  }, [round])

  // ── 选牌 ──
  const toggleCard = useCallback((cardId) => {
    const card = myHand.find(c => c.id === cardId)
    if (card && cardCost(card) > playerEnergy) {
      return
    }
    setMySelected(prev => {
      if (prev.includes(cardId)) return prev.filter(id => id !== cardId)
      if (myMaxPlay <= 1) return [cardId]
      if (prev.length >= myMaxPlay) return prev
      return [...prev, cardId]
    })
  }, [myHand, myMaxPlay, playerEnergy])

  const setPreviewCard = useCallback((cardId) => {
    if (cardId) {
      const card = myHand.find(c => c.id === cardId)
      if (card && cardCost(card) > playerEnergy) {
        return
      }
    }
    setMySelected(cardId ? [cardId] : [])
  }, [myHand, playerEnergy])

  // ── 出牌确认 ──
  const confirmPlay = useCallback(() => {
    if (mySelected.length === 0) return
    const selectedIds = mySelected.slice(0, 1)
    const played = selectedIds.map(id => myHand.find(c => c.id === id)).filter(Boolean)
    const availableEnergy = playerEnergy
    const selectedCost = played.reduce((sum, card) => sum + cardCost(card), 0)
    if (selectedCost > availableEnergy) {
      return
    }
    setPlayerEnergy(prev => Math.max(0, prev - selectedCost))
    setMyPlayed(played)
    setMyHand(prev => prev.filter(c => !selectedIds.includes(c.id)))
    // 出过的牌进弃牌堆（临时卡不进）
    const normalPlayed = played.filter(c => !c.temp)
    if (normalPlayed.length > 0) setMyDiscard(prev => [...prev, ...normalPlayed.map(c => ({ ...c, debuffs: [] }))])
    setMySelected([])

    let totalDmg = 0
    const results = played.map(card => {
      const result = resolvePlayedCard(card, 'player')
      totalDmg += result.damage
      return result
    })
    recordPlayedCardStats(played, 'player', results)

    if (totalDmg > 0) {
      const hitWeakness = played.some(card => boss.weakness.weak.includes(card.attr.id))
      showBossImageState(hitWeakness ? 'weakDamageTaken' : 'damageTaken')
      showBossDamagePopup(totalDmg, hitWeakness)
    }
    setBoss(prev => ({ ...prev, breakPoints: prev.breakPoints + totalDmg }))
    setTurnIdx(prev => prev + 1)
  }, [mySelected, myHand, playerEnergy, boss, resolvePlayedCard, recordPlayedCardStats, showBossImageState, showBossDamagePopup])

  // ── 跳过回合 ──
  const skipTurn = useCallback(() => {
    resetComboVisualChain()
    addActionLog('你 跳过出牌', 'player')
    setMyPlayed([])
    setMySelected([])
    setTurnIdx(prev => prev + 1)
  }, [addActionLog, resetComboVisualChain])

  useEffect(() => {
    if (phase !== 'playing' || currentActor?.type !== 'player') return
    if (myHand.length === 0) return
    const hasPlayableCard = myHand.some(card => cardCost(card) <= playerEnergy)
    if (hasPlayableCard) return
    resetComboVisualChain()
    addActionLog(`你 因能量不足跳过出牌（当前能量 ${playerEnergy}）`, 'player')
    setMyPlayed([])
    setMySelected([])
    setTurnIdx(prev => prev + 1)
  }, [phase, currentActor, myHand, playerEnergy, addActionLog, resetComboVisualChain])

  // ── 拖拽：单卡直接打出到Boss ──
  const dragPlayCard = useCallback((card) => {
    if (currentActor?.type !== 'player' || phase !== 'playing') return
    const cost = cardCost(card)
    if (cost > playerEnergy) {
      return
    }
    resetComboVisualChain()
    setPlayerEnergy(prev => Math.max(0, prev - cost))
    setMyPlayed([card])
    setMyHand(prev => prev.filter(c => c.id !== card.id))
    if (!card.temp) setMyDiscard(prev => [...prev, { ...card, debuffs: [] }])
    setMySelected(prev => prev.filter(id => id !== card.id))

    const dmg = calcDamage(card)
    const relation = boss.weakness.weak.includes(card.attr.id) ? '💥弱点!' :
                     boss.weakness.resist.includes(card.attr.id) ? '🛡️抗性' :
                     card.attr.id === 'spirit' ? '✨灵' : ''
    const debuffTag = card.debuffs.length > 0 ? ' [被弱化]' : ''
    const tempTag = card.temp ? ' [临时]' : ''
    addActionLog(`你 打出 [${card.attr.name} ${card.power}]${debuffTag}${tempTag}：造成 ${dmg} 点 ${relation}`, 'player')
    const hitWeakness = boss.weakness.weak.includes(card.attr.id)
    showBossImageState(hitWeakness ? 'weakDamageTaken' : 'damageTaken')
    showBossDamagePopup(dmg, hitWeakness)
    setBoss(prev => ({ ...prev, breakPoints: prev.breakPoints + dmg }))
    setTurnIdx(prev => prev + 1)
  }, [currentActor, phase, playerEnergy, calcDamage, boss, addActionLog, resetComboVisualChain, showBossImageState, showBossDamagePopup])

  // ── 拖拽：合卡（拖动=素材，放下目标=基准，属性以基准为准） ──
  const mergeCards = useCallback((dragCardId, targetCardId) => {
    const dragCard = myHand.find(c => c.id === dragCardId)   // 素材
    const targetCard = myHand.find(c => c.id === targetCardId) // 基准
    if (!dragCard || !targetCard) return

    cardIdCounter++
    const sameAttr = dragCard.attr.id === targetCard.attr.id
    const mergedAttr = targetCard.attr // 属性始终以基准卡为准
    const rawPower = dragCard.power + targetCard.power
    const mergedPower = sameAttr ? rawPower + 2 : Math.floor(rawPower * 0.8)
    const merged = {
      id: `card-${cardIdCounter}`,
      attr: mergedAttr,
      power: mergedPower,
      debuffs: [...new Set([...dragCard.debuffs, ...targetCard.debuffs])],
      temp: true, // 合成卡为临时卡，打出后不进弃牌堆
    }
    // 被融合的原卡进弃牌堆循环（临时卡不进）
    const toDiscard = [dragCard, targetCard].filter(c => !c.temp)
    if (toDiscard.length > 0) setMyDiscard(prev => [...prev, ...toDiscard.map(c => ({ ...c, debuffs: [] }))])
    setMyHand(prev => prev.filter(c => c.id !== dragCardId && c.id !== targetCardId).concat(merged))
    setMySelected([])
    addActionLog(`你 合成 [${dragCard.attr.name} ${dragCard.power}] + [${targetCard.attr.name} ${targetCard.power}] → [${merged.attr.name} ${merged.power}]${sameAttr ? '（同属性+2）' : '（异属性×0.8）'}临时卡`, 'player')
  }, [myHand, addActionLog])

  // ── 拖拽事件处理 ──
  const dragPending = useRef(null) // 缓存 pointerdown，等距离超阈值才真正进入拖拽
  const DRAG_THRESHOLD = 6

  const handlePointerDown = useCallback((cardId, e) => {
    if (currentActor?.type !== 'player' || phase !== 'playing') return
    const rect = e.currentTarget.getBoundingClientRect()
    dragPending.current = {
      cardId,
      originX: e.clientX,
      originY: e.clientY,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
    }
  }, [currentActor, phase])

  const handlePointerMove = useCallback((e) => {
    // 还在等超过拖拽阈值
    if (dragPending.current && !dragState) {
      const dx = e.clientX - dragPending.current.originX
      const dy = e.clientY - dragPending.current.originY
      if (Math.abs(dx) + Math.abs(dy) >= DRAG_THRESHOLD) {
        setDragState({
          cardId: dragPending.current.cardId,
          x: e.clientX,
          y: e.clientY,
          offsetX: dragPending.current.offsetX,
          offsetY: dragPending.current.offsetY,
        })
        dragPending.current = null
      }
      return
    }
    if (!dragState) return

    setDragState(prev => prev ? { ...prev, x: e.clientX, y: e.clientY } : null)

    // 检测是否悬停在Boss区
    if (bossAreaRef.current) {
      const r = bossAreaRef.current.getBoundingClientRect()
      if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
        setDragOverTarget('boss')
        return
      }
    }
    // 检测是否悬停在其他手牌上
    for (const [cid, el] of Object.entries(handCardRefs.current)) {
      if (cid === dragState.cardId || !el) continue
      const r = el.getBoundingClientRect()
      if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
        setDragOverTarget(cid)
        return
      }
    }
    setDragOverTarget(null)
  }, [dragState])

  const handlePointerUp = useCallback(() => {
    // 没超过阈值 → 是一次点击，不处理（让 onClick 自然触发）
    if (dragPending.current) {
      dragPending.current = null
      return
    }
    if (!dragState) return
    const card = myHand.find(c => c.id === dragState.cardId)
    if (card && dragOverTarget === 'boss') {
      dragPlayCard(card)
    } else if (card && dragOverTarget && dragOverTarget !== 'boss') {
      mergeCards(dragState.cardId, dragOverTarget)
    }
    setDragState(null)
    setDragOverTarget(null)
  }, [dragState, dragOverTarget, myHand, dragPlayCard, mergeCards])

  // 全局 pointer 事件
  useEffect(() => {
    const onMove = (e) => handlePointerMove(e)
    const onUp = () => handlePointerUp()
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [handlePointerMove, handlePointerUp])

  // ── AI 队友行动（优先打弱点） ──
  const allyAction = useCallback(() => {
    if (allyHand.length === 0) {
      resetComboVisualChain()
      addActionLog('队友 无牌可出，跳过', 'ally')
      setTurnIdx(prev => prev + 1)
      return
    }

    // 按弱点优先排序
    const sorted = [...allyHand].sort((a, b) => {
      const aCombo = comboAttrs.includes(a.comboAttr?.id || a.attr.id) ? 3 : 0
      const bCombo = comboAttrs.includes(b.comboAttr?.id || b.attr.id) ? 3 : 0
      const aWeak = boss.weakness.weak.includes(a.attr.id) ? 2 : 0
      const bWeak = boss.weakness.weak.includes(b.attr.id) ? 2 : 0
      const aResist = boss.weakness.resist.includes(a.attr.id) ? -1 : 0
      const bResist = boss.weakness.resist.includes(b.attr.id) ? -1 : 0
      return (bCombo + bWeak + bResist) - (aCombo + aWeak + aResist) || b.power - a.power
    })

    let allyEnergy = allyEnergyValue
    const toPlay = []
    for (const card of sorted) {
      if (toPlay.length >= allyMaxPlay) break
      const cost = cardCost(card)
      if (cost > allyEnergy) continue
      toPlay.push(card)
      allyEnergy -= cost
    }
    if (toPlay.length === 0) {
      resetComboVisualChain()
      addActionLog(`队友 因能量不足跳过出牌（当前能量 ${allyEnergyValue}）`, 'ally')
      setTurnIdx(prev => prev + 1)
      return
    }
    const playedIds = new Set(toPlay.map(c => c.id))
    const spentEnergy = toPlay.reduce((sum, card) => sum + cardCost(card), 0)
    setAllyEnergyValue(prev => Math.max(0, prev - spentEnergy))
    setAllyPlayed(toPlay)
    setAllyHand(prev => prev.filter(c => !playedIds.has(c.id)))
    setAllyDiscard(prev => [...prev, ...toPlay.map(c => ({ ...c, debuffs: [] }))])

    let totalDmg = 0
    const results = toPlay.map(card => {
      const result = resolvePlayedCard(card, 'ally')
      totalDmg += result.damage
      return result
    })
    recordPlayedCardStats(toPlay, 'ally', results)

    if (totalDmg > 0) {
      const hitWeakness = toPlay.some(card => boss.weakness.weak.includes(card.attr.id))
      showBossImageState(hitWeakness ? 'weakDamageTaken' : 'damageTaken')
      showBossDamagePopup(totalDmg, hitWeakness)
    }
    setBoss(prev => ({ ...prev, breakPoints: prev.breakPoints + totalDmg }))
    setTurnIdx(prev => prev + 1)
  }, [allyHand, allyMaxPlay, allyEnergyValue, boss, comboAttrs, addActionLog, resetComboVisualChain, resolvePlayedCard, recordPlayedCardStats, showBossImageState, showBossDamagePopup])

  const damageSide = useCallback((side, amount) => {
    const shield = side === 'player' ? playerShield : allyShield
    const incomingReduce = side === 'player' ? playerIncomingReduce : allyIncomingReduce
    const finalDamage = Math.max(0, amount - incomingReduce - bossDamageReduction)
    const absorbed = Math.min(shield, finalDamage)
    const hpDamage = finalDamage - absorbed

    if (side === 'player') {
      setPlayerShield(Math.max(0, shield - absorbed))
      setPlayerHp(prev => Math.max(0, prev - hpDamage))
    } else {
      setAllyShield(Math.max(0, shield - absorbed))
      setAllyHp(prev => Math.max(0, prev - hpDamage))
    }

    return { side, finalDamage, hpDamage, absorbed }
  }, [allyIncomingReduce, allyShield, bossDamageReduction, playerIncomingReduce, playerShield])

  // ── Boss 行动（全部实装） ──
  const bossAction = useCallback(() => {
    resetComboVisualChain()
    if (bossSkipNext) {
      addActionLog(`Boss ${boss.name} 行动被封锁，跳过`, 'boss')
      setBossSkipNext(false)
      setTurnIdx(prev => prev + 1)
      return
    }

    const action = BOSS_ACTIONS[Math.floor(Math.random() * BOSS_ACTIONS.length)]
    const details = []
    if (action.id === 'big_attack') showBossImageState('attack')

    switch (action.id) {
      case 'debuff_first': {
        // 弱化行动条上第一个非boss的人
        const firstPlayer = turnOrder.find(a => a.type !== 'boss')
        if (firstPlayer?.type === 'player') {
          setMyMaxPlay(1)
          details.push('你本回合只能出1张牌')
        } else if (firstPlayer?.type === 'ally') {
          setAllyMaxPlay(1)
          details.push('队友本回合只能出1张牌')
        }
        break
      }
      case 'debuff_last': {
        // 弱化最后行动的非boss角色的最后一张手牌
        const lastPlayer = [...turnOrder].reverse().find(a => a.type !== 'boss')
        if (lastPlayer?.type === 'player') {
          setMyHand(prev => {
            if (prev.length === 0) return prev
            const copy = [...prev]
            const last = { ...copy[copy.length - 1], debuffs: [...copy[copy.length - 1].debuffs, 'weaken'] }
            copy[copy.length - 1] = last
            return copy
          })
          details.push('你的最后一张手牌被弱化')
        } else if (lastPlayer?.type === 'ally') {
          setAllyHand(prev => {
            if (prev.length === 0) return prev
            const copy = [...prev]
            const last = { ...copy[copy.length - 1], debuffs: [...copy[copy.length - 1].debuffs, 'weaken'] }
            copy[copy.length - 1] = last
            return copy
          })
          details.push('队友的最后一张手牌被弱化')
        }
        break
      }
      case 'weaken_next':
        setCurseNextRound(true)
        details.push('下回合双方第一张手牌会被弱化')
        break
      case 'big_attack': {
        setBoss(prev => ({ ...prev, breakGoal: prev.breakGoal + 2 }))
        const playerHit = damageSide('player', 2)
        const allyHit = damageSide('ally', 2)
        const formatHit = hit => `${sideLabel(hit.side)}受到 ${hit.hpDamage} 点伤害${hit.absorbed > 0 ? `（护盾抵消 ${hit.absorbed}）` : ''}`
        details.push(formatHit(playerHit), formatHit(allyHit), 'Boss生命上限 +2')
        break
      }
      case 'nothing':
        details.push('未行动')
        break
      default:
        break
    }

    addActionLog(`Boss ${boss.name} 使用 ${action.name}：${joinActionParts(details) || action.desc}`, 'boss')
    setTurnIdx(prev => prev + 1)
  }, [addActionLog, boss, bossSkipNext, damageSide, joinActionParts, resetComboVisualChain, sideLabel, turnOrder, showBossImageState])

  // ── 行动推进 ──
  useEffect(() => {
    if (phase !== 'playing') return

    if (boss.breakPoints >= boss.breakGoal) {
      setPhase('win')
      return
    }

    if (!currentActor) return

    if (currentActor.type === 'ally') {
      const timer = setTimeout(allyAction, 800)
      return () => clearTimeout(timer)
    }
    if (currentActor.type === 'boss') {
      const timer = setTimeout(bossAction, 800)
      return () => clearTimeout(timer)
    }
    // player → 等待手动出牌或跳过
  }, [turnIdx, phase, currentActor, allyAction, bossAction, boss])

  useEffect(() => {
    if (phase !== 'playing') return
    if (playerHp <= 0 && allyHp <= 0) {
      setPhase('lose')
    }
  }, [phase, playerHp, allyHp])

  // ── 回合结束检测（只设状态，不启动 timer） ──
  useEffect(() => {
    if (phase !== 'playing') return
    if (turnIdx >= turnOrder.length && turnOrder.length > 0) {
      // 已达成击破目标 → 交给行动推进 effect 处理胜利
      if (boss.breakPoints >= boss.breakGoal) return

      if (round >= boss.maxTurns) {
        setPhase('lose')
        return
      }
      setPlayerShield(0)
      setAllyShield(0)
      setPhase('round_end')
      setRound(prev => prev + 1)
    }
  }, [turnIdx, turnOrder, phase, round, boss])

  // ── round_end → 延迟启动下一回合 ──
  useEffect(() => {
    if (phase !== 'round_end') return
    const timer = setTimeout(() => doStartRoundRef.current(), 1200)
    return () => clearTimeout(timer)
  }, [phase])

  // ── 重新开始 ──
  const restartGame = useCallback(() => {
    cardIdCounter = 0
    const init = createInitialState()
    const nextComboAttrs = generateComboAttrs(comboAttrs)
    setBoss(init.boss)
    setComboAttrs(nextComboAttrs)
    setPlayerHp(MAX_PLAYER_HP)
    setAllyHp(MAX_PLAYER_HP)
    setPlayerShield(0)
    setAllyShield(0)
    setPlayerDamageBonusNext(0)
    setAllyDamageBonusNext(0)
    setPlayerEnergy(INITIAL_PLAYER_ENERGY)
    setAllyEnergyValue(INITIAL_PLAYER_ENERGY)
    setPlayerIncomingReduce(0)
    setAllyIncomingReduce(0)
    setBossDamageReduction(0)
    setBossSkipNext(false)
    comboStatsRef.current = { total: 0, streak: 0, best: 0, lastRound: 0 }
    setPlayedCardStats([])
    resetComboVisualChain()
    showBossImageState('normal')
    setBossDamagePopup(null)
    setComboPopup(null)
    if (bossDamagePopupTimerRef.current) {
      clearTimeout(bossDamagePopupTimerRef.current)
      bossDamagePopupTimerRef.current = null
    }
    if (comboPopupTimerRef.current) {
      clearTimeout(comboPopupTimerRef.current)
      comboPopupTimerRef.current = null
    }
    setMyDeck(init.myDeck)
    setMyHand(init.myHand)
    setMyDiscard(init.myDiscard)
    setAllyDeck(init.allyDeck)
    setAllyHand(init.allyHand)
    setAllyDiscard(init.allyDiscard)
    setMyPlayed([])
    setAllyPlayed([])
    setMySelected([])
    setMyMaxPlay(MAX_PLAY)
    setAllyMaxPlay(MAX_PLAY)
    setTurnOrder([])
    setNextTurnOrder([])
    setTurnIdx(0)
    setRound(1)
    setCurseNextRound(false)
    setPeekPile(null)
    setDragState(null)
    setDragOverTarget(null)
    handCardRefs.current = {}
    setGameLog([])
    setPhase('prep')
    hasStarted.current = false
    // 延迟一帧重新开始
    setTimeout(() => {
      setGameLog([])
      hasStarted.current = true
      // 用新数据手动开始
      const firstDraw = drawCards(init.myDeck, [], DRAW_COUNT)
      setMyDeck(firstDraw.deck)
      setMyHand(firstDraw.drawn)
      setMyDiscard(firstDraw.discard)
      const allyFirstDraw = drawCards(init.allyDeck, [], DRAW_COUNT)
      setAllyDeck(allyFirstDraw.deck)
      setAllyHand(allyFirstDraw.drawn)
      setAllyDiscard(allyFirstDraw.discard)
      const actors = [
        { id: 'boss', type: 'boss', name: init.boss.name, emoji: init.boss.emoji },
        { id: 'player', type: 'player', name: nekoName || '我方猫娘', emoji: '🐱' },
        { id: 'ally', type: 'ally', name: '队友猫娘', emoji: '🐈' },
      ].sort(() => Math.random() - 0.5)
      setTurnOrder(actors)
      setNextTurnOrder([
        { id: 'boss', type: 'boss', name: init.boss.name, emoji: init.boss.emoji },
        { id: 'player', type: 'player', name: nekoName || '我方猫娘', emoji: '🐱' },
        { id: 'ally', type: 'ally', name: '队友猫娘', emoji: '🐈' },
      ].sort(() => Math.random() - 0.5))
      setTurnIdx(0)
      setPhase('playing')
      setRound(1)
      setPlayerEnergy(INITIAL_PLAYER_ENERGY)
      setAllyEnergyValue(INITIAL_PLAYER_ENERGY)
      addActionLog('———— 回合 1 —————', 'round')
    }, 50)
  }, [addActionLog, comboAttrs, drawCards, nekoName, DRAW_COUNT, MAX_PLAY, resetComboVisualChain, showBossImageState])

  // ── 击破进度 ──
  const breakPercent = Math.min(100, Math.round((boss.breakPoints / boss.breakGoal) * 100))
  const bossHpValue = Math.max(0, boss.breakGoal - boss.breakPoints)
  const bossHpPercent = boss.breakGoal > 0
    ? Math.max(0, Math.min(100, Math.round((bossHpValue / boss.breakGoal) * 100)))
    : 0
  const gameOver = phase === 'win' || phase === 'lose'
  const isPlayerTurn = currentActor?.type === 'player' && phase === 'playing'
  const latestLogs = gameLog
  const phaseLabel = phase === 'win' ? '胜利' : phase === 'lose' ? '失败' : phase === 'round_end' ? '整备' : '战斗中'

  if (useNewUi) {
    return (
      <NewBattleDuelUI
        nekoName={nekoName}
        nekoAvatar={nekoAvatar}
        boss={boss}
        bossImageSrc={BOSS_IMAGE_SOURCES[bossImageState] || BOSS_IMAGE_SOURCES.normal}
        bossImageState={bossImageState}
        bossDamagePopup={bossDamagePopup}
        comboPopup={comboPopup}
        comboAttrs={comboAttrs}
        bossBreakValue={boss.breakPoints}
        bossBreakGoal={boss.breakGoal}
        round={round}
        currentActor={currentActor}
        myDeck={myDeck}
        myHand={myHand}
        myDiscard={myDiscard}
        myPlayed={myPlayed}
        mySelected={mySelected}
        myMaxPlay={myMaxPlay}
        playerEnergy={playerEnergy}
        allyDeck={allyDeck}
        allyHand={allyHand}
        allyPlayed={allyPlayed}
        allyEnergy={allyEnergyValue}
        playerHp={playerHp}
        playerShield={playerShield}
        allyHp={allyHp}
        allyShield={allyShield}
        bossBreakPercent={breakPercent}
        gameOver={gameOver}
        outcome={phase}
        comboStats={comboStatsRef.current}
        playedCardStats={playedCardStats}
        isPlayerTurn={isPlayerTurn}
        onToggleCard={toggleCard}
        onSetPreviewCard={setPreviewCard}
        onConfirmPlay={confirmPlay}
        onSkipTurn={skipTurn}
        onRestart={restartGame}
        onBackToClassic={() => setUseNewUi(false)}
        onClose={onClose}
        gameLog={gameLog}
        comboReference={cardReferencePool.map(card => ({
          code: card.code,
          name: card.name,
          attrId: card.attrId,
          attrName: attrNameById(card.attrId),
          comboAttrId: card.comboAttrId || card.attrId,
          comboAttrName: attrNameById(card.comboAttrId || card.attrId),
          cost: card.cost,
          mainText: card.mainText,
          comboText: card.comboText,
        }))}
      />
    )
  }

  return (
    <motion.div
      key="card-battle-page"
      initial={{ opacity: 0, x: 56 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -32 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
      className="fixed inset-0 z-[100] flex h-screen w-screen flex-col overflow-hidden bg-[#111827] text-white"
    >
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_18%,rgba(14,165,233,0.16),transparent_28%),linear-gradient(180deg,#142033_0%,#101827_48%,#0b111d_100%)]" />
      <div className="relative z-10 flex h-full flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-white/10 bg-[#0b1220]/90 px-5">
          <div className="flex min-w-0 items-center gap-4">
            <button
              type="button"
              onClick={onClose}
              className="flex h-9 w-9 items-center justify-center rounded-md border border-white/10 bg-white/[0.04] text-gray-300 transition-colors hover:border-sky-300/40 hover:bg-sky-400/10 hover:text-white"
              title="返回大厅"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => setUseNewUi(true)}
              className="rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-bold text-sky-100 transition-colors hover:border-sky-300/40 hover:bg-sky-400/10"
            >
              切换新版UI
            </button>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Swords className="h-4 w-4 text-sky-300" />
                <h2 className="truncate text-base font-black text-white">猫娘大乱斗</h2>
                <span className="rounded border border-sky-300/30 bg-sky-400/10 px-2 py-0.5 text-[10px] font-bold text-sky-200">Beta</span>
              </div>
              <p className="mt-0.5 text-[11px] text-gray-500">协力击破战 · 回合制卡牌</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.04] px-3 py-1.5">
              <Timer className="h-3.5 w-3.5 text-amber-300" />
              <span className="text-xs font-bold text-gray-200">{round}/{boss.maxTurns}</span>
            </div>
            <div className="flex items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.04] px-3 py-1.5">
              <Activity className={`h-3.5 w-3.5 ${phase === 'win' ? 'text-emerald-300' : phase === 'lose' ? 'text-red-300' : 'text-sky-300'}`} />
              <span className="text-xs font-bold text-gray-200">{phaseLabel}</span>
            </div>
          </div>
        </header>

        <main className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)_320px] grid-rows-[minmax(0,1fr)_190px] gap-3 p-3">
          <aside className="row-span-2 flex min-h-0 flex-col rounded-lg border border-white/10 bg-[#101a2a]/92">
            <div className="border-b border-white/10 p-4">
              <div className="flex items-center gap-3">
                <div className="h-14 w-14 shrink-0 overflow-hidden rounded-lg border border-violet-300/30 bg-violet-400/10">
                  {nekoAvatar ? (
                    <img src={nekoAvatar} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center text-xl">猫</div>
                  )}
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-black text-violet-100">{nekoName || '我方猫娘'}</p>
                  <p className="mt-0.5 text-[11px] text-gray-500">玩家单位 · 手牌 {myHand.length}/{HAND_LIMIT}</p>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-2">
                <div className={`rounded-md border px-3 py-2 ${isPlayerTurn ? 'border-violet-300/40 bg-violet-400/10' : 'border-white/10 bg-white/[0.03]'}`}>
                  <p className="text-[10px] text-gray-500">可出牌</p>
                  <p className="mt-1 text-lg font-black text-violet-100">{myMaxPlay}</p>
                </div>
                <div className="rounded-md border border-white/10 bg-white/[0.03] px-3 py-2">
                  <p className="text-[10px] text-gray-500">弃牌堆</p>
                  <p className="mt-1 text-lg font-black text-gray-200">{myDiscard.length}</p>
                </div>
              </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col border-b border-white/10 p-4">
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2 text-xs font-bold text-sky-200">
                  <Heart className="h-3.5 w-3.5" />
                  队友猫娘
                </div>
                <span className="rounded border border-sky-300/20 bg-sky-400/10 px-2 py-0.5 text-[10px] text-sky-200">AI</span>
              </div>
              <div className="rounded-md border border-sky-300/20 bg-sky-400/[0.06] p-3">
                <div className="flex items-center gap-2">
                  <div className="flex h-10 w-10 items-center justify-center rounded-md border border-sky-300/20 bg-sky-400/10 text-sm">AI</div>
                  <div>
                    <p className="text-xs font-bold text-sky-100">队友猫娘</p>
                    <p className="text-[10px] text-gray-500">牌库 {allyDeck.length} · 弃牌 {allyDiscard.length}</p>
                  </div>
                </div>
              </div>
              <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
                <p className="mb-2 text-[10px] font-bold uppercase tracking-wide text-gray-500">队友手牌</p>
                <div className="flex flex-wrap gap-1.5">
                  {allyHand.map(c => <CardUI key={c.id} card={c} small faceDown />)}
                </div>
                {allyPlayed.length > 0 && (
                  <>
                    <p className="mb-2 mt-4 text-[10px] font-bold uppercase tracking-wide text-sky-300">本回合出牌</p>
                    <div className="flex flex-wrap gap-1.5">
                      {allyPlayed.map(c => <CardUI key={c.id} card={c} small />)}
                    </div>
                  </>
                )}
              </div>
            </div>

            <div className="p-4">
              <div className="mb-3 flex items-center gap-2 text-xs font-bold text-gray-300">
                <Target className="h-3.5 w-3.5 text-amber-300" />
                行动序列
              </div>
              <ActionBar order={turnOrder} currentIdx={turnIdx} roundLabel={`R${round}`} nextOrder={nextTurnOrder} roundKey={round} />
            </div>
          </aside>

          <section
            ref={bossAreaRef}
            className={`relative min-h-0 overflow-hidden rounded-lg border bg-[#0d1624]/88 transition-all duration-200 ${
              dragOverTarget === 'boss'
                ? 'border-red-300/70 shadow-[0_0_0_1px_rgba(252,165,165,0.35),0_0_42px_rgba(239,68,68,0.18)]'
                : 'border-white/10'
            }`}
          >
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_35%,rgba(248,113,113,0.18),transparent_34%)]" />
            <div className="relative flex h-full flex-col items-center justify-center p-6">
              {dragOverTarget === 'boss' && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="absolute top-6 rounded-md border border-red-300/40 bg-red-500/15 px-4 py-2 text-xs font-bold text-red-100"
                >
                  松开卡牌攻击 Boss
                </motion.div>
              )}

              <motion.div
                animate={phase === 'win' ? { opacity: 0.35, scale: 0.92 } : { opacity: 1, scale: dragOverTarget === 'boss' ? 1.05 : 1 }}
                transition={{ type: 'spring', damping: 20 }}
                className="flex flex-col items-center"
              >
                <div className="flex h-36 w-36 items-center justify-center rounded-lg border border-red-300/30 bg-red-500/10 text-7xl shadow-2xl shadow-red-950/50">
                  {boss.emoji}
                </div>
                <h3 className="mt-5 text-2xl font-black text-red-100">{boss.name}</h3>
                <p className="mt-1 text-xs text-gray-500">Boss 单位 · 击破目标 {boss.breakGoal}</p>
              </motion.div>

              <div className="mt-6 w-full max-w-xl">
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="font-bold text-gray-300">Boss生命</span>
                  <span className="font-black text-white">{bossHpValue} / {boss.breakGoal}</span>
                </div>
                <div className="h-4 overflow-hidden rounded-full border border-white/10 bg-white/[0.06]">
                  <motion.div
                    animate={{ width: `${bossHpPercent}%` }}
                    transition={{ type: 'spring', damping: 20 }}
                    className="h-full rounded-full bg-gradient-to-r from-red-500 via-rose-500 to-orange-400"
                  />
                </div>
              </div>

              <div className="mt-5 grid w-full max-w-xl grid-cols-2 gap-3">
                <div className="rounded-md border border-emerald-300/20 bg-emerald-400/[0.06] p-3">
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-bold text-emerald-200">
                    <Zap className="h-3.5 w-3.5" />
                    弱点属性
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {boss.weakness.weak.map(id => (
                      <span key={id} className="rounded border border-emerald-300/30 bg-emerald-400/10 px-2 py-1 text-xs font-bold text-emerald-100">{attrNameById(id)}</span>
                    ))}
                  </div>
                </div>
                <div className="rounded-md border border-red-300/20 bg-red-400/[0.06] p-3">
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-bold text-red-200">
                    <Shield className="h-3.5 w-3.5" />
                    抗性属性
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {boss.weakness.resist.map(id => (
                      <span key={id} className="rounded border border-red-300/30 bg-red-400/10 px-2 py-1 text-xs font-bold text-red-100">{attrNameById(id)}</span>
                    ))}
                  </div>
                </div>
              </div>

              <AnimatePresence>
                {phase === 'win' && (
                  <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="absolute inset-x-0 bottom-8 mx-auto flex w-fit flex-col items-center rounded-lg border border-emerald-300/30 bg-emerald-500/10 px-8 py-5 text-center shadow-2xl backdrop-blur"
                  >
                    <p className="text-3xl font-black text-emerald-200">胜利</p>
                    <p className="mt-1 text-sm text-gray-300">Boss 已被击破</p>
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={restartGame}
                      className="mt-4 flex items-center gap-2 rounded-md bg-emerald-500 px-5 py-2 text-sm font-bold text-white shadow-lg"
                    >
                      <RotateCcw className="w-4 h-4" /> 再来一局
                    </motion.button>
                  </motion.div>
                )}
                {phase === 'lose' && (
                  <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="absolute inset-x-0 bottom-8 mx-auto flex w-fit flex-col items-center rounded-lg border border-red-300/30 bg-red-500/10 px-8 py-5 text-center shadow-2xl backdrop-blur"
                  >
                    <p className="text-3xl font-black text-red-200">失败</p>
                    <p className="mt-1 text-sm text-gray-300">回合耗尽，未能击破 Boss</p>
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={restartGame}
                      className="mt-4 flex items-center gap-2 rounded-md bg-red-500 px-5 py-2 text-sm font-bold text-white shadow-lg"
                    >
                      <RotateCcw className="w-4 h-4" /> 再来一局
                    </motion.button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </section>

          <aside className="flex min-h-0 flex-col rounded-lg border border-white/10 bg-[#101a2a]/92">
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <div className="flex items-center gap-2 text-xs font-bold text-gray-200">
                <BookOpen className="h-3.5 w-3.5 text-sky-300" />
                战斗记录
              </div>
              <span className="text-[10px] text-gray-500">{gameLog.length}</span>
            </div>
            <div ref={logRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
              {latestLogs.map(l => (
                <div
                  key={l.id}
                  className={`rounded-md border px-3 py-2 text-[11px] leading-relaxed ${
                    l.type === 'boss' ? 'border-red-300/15 bg-red-400/[0.06] text-red-100/85' :
                    l.type === 'player' ? 'border-violet-300/15 bg-violet-400/[0.06] text-violet-100/85' :
                    l.type === 'ally' ? 'border-sky-300/15 bg-sky-400/[0.06] text-sky-100/85' :
                    l.type === 'debuff' ? 'border-amber-300/15 bg-amber-400/[0.06] text-amber-100/85' :
                    l.type === 'round' ? 'border-white/10 bg-white/[0.04] text-center text-white/85 font-black' :
                    l.type === 'system' ? 'border-emerald-300/15 bg-emerald-400/[0.06] text-emerald-100/90 font-bold' :
                    'border-white/10 bg-white/[0.03] text-gray-300'
                  }`}
                >
                  {l.type === 'round' ? l.text : renderBattleLogText(l.text)}
                </div>
              ))}
              {latestLogs.length === 0 && (
                <div className="flex h-full items-center justify-center text-xs text-gray-600">等待战斗开始</div>
              )}
            </div>
          </aside>

          <section className="col-span-2 min-w-0 rounded-lg border border-white/10 bg-[#0f1929]/95">
            <div className="flex h-full flex-col">
              <div className="flex shrink-0 items-center justify-between border-b border-white/10 px-4 py-3">
                <div className="flex items-center gap-2">
                  <Layers className="h-4 w-4 text-violet-300" />
                  <div>
                    <p className="text-xs font-bold text-violet-100">指令手牌</p>
                    <p className="text-[10px] text-gray-500">点击选择出牌，拖到 Boss 区直接攻击，拖到手牌上进行合成</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {isPlayerTurn && (
                    <>
                      <span className="rounded border border-violet-300/30 bg-violet-400/10 px-2.5 py-1 text-[11px] font-bold text-violet-100">
                        你的回合 · 可出 {myMaxPlay} 张
                      </span>
                      <motion.button
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        onClick={skipTurn}
                        className="flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[11px] font-bold text-gray-300 transition-colors hover:bg-white/10 hover:text-white"
                      >
                        <SkipForward className="w-3 h-3" /> 跳过
                      </motion.button>
                    </>
                  )}
                  {mySelected.length > 0 && isPlayerTurn && (
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={confirmPlay}
                      className="rounded-md bg-violet-500 px-4 py-1.5 text-xs font-black text-white shadow-lg shadow-violet-950/30"
                    >
                      <Swords className="w-3 h-3 inline mr-1" />
                      出牌 ({mySelected.length})
                    </motion.button>
                  )}
                </div>
              </div>

              <div className="flex min-h-0 flex-1 items-center gap-4 px-4">
                <PileButton
                  count={myDeck.length}
                  label="抽牌堆"
                  variant="deck"
                  onClick={() => setPeekPile('myDeck')}
                />

                <div className="flex min-w-0 flex-1 items-end justify-center gap-2 overflow-x-auto pb-2 pt-4">
                  <AnimatePresence>
                    {myHand.map(card => {
                      const isDragging = dragState?.cardId === card.id
                      const isMergeTarget = dragOverTarget === card.id
                      const canPlayCard = isPlayerTurn && cardCost(card) <= playerEnergy
                      return (
                        <motion.div
                          key={card.id}
                          ref={el => { handCardRefs.current[card.id] = el }}
                          initial={{ opacity: 0, y: 40 }}
                          animate={{ opacity: isDragging ? 0.3 : 1, y: 0, scale: isMergeTarget ? 1.12 : 1 }}
                          exit={{ opacity: 0, y: 20, scale: 0.8 }}
                          className="relative"
                          onPointerDown={(e) => { if (canPlayCard) handlePointerDown(card.id, e) }}
                          style={{ touchAction: 'none' }}
                        >
                          <CardUI
                            card={card}
                            selected={mySelected.includes(card.id)}
                            onClick={() => { if (!dragState) toggleCard(card.id) }}
                            disabled={!canPlayCard}
                          />
                          {isMergeTarget && (
                            <motion.div
                              initial={{ opacity: 0, scale: 0.8 }}
                              animate={{ opacity: 1, scale: 1 }}
                              className="absolute inset-0 rounded-xl border-2 border-amber-400 bg-amber-400/10 flex items-center justify-center pointer-events-none z-20"
                            >
                              <div className="bg-black/70 rounded-full p-1.5">
                                <Combine className="w-4 h-4 text-amber-300" />
                              </div>
                            </motion.div>
                          )}
                        </motion.div>
                      )
                    })}
                  </AnimatePresence>
                  {myHand.length === 0 && phase === 'playing' && (
                    <p className="py-8 text-xs text-gray-600">手牌为空，等待下回合抽牌</p>
                  )}
                  {gameOver && myHand.length === 0 && (
                    <p className="py-8 text-xs text-gray-600">游戏结束</p>
                  )}
                </div>

                <PileButton
                  count={myDiscard.length}
                  label="弃牌堆"
                  variant="discard"
                  onClick={() => setPeekPile('myDiscard')}
                />
              </div>
              <div className="flex h-12 shrink-0 items-center gap-2 border-t border-white/10 px-4">
                <span className="text-[10px] font-bold uppercase tracking-wide text-gray-500">本回合出牌</span>
                <div className="flex gap-1.5">
                  {myPlayed.length > 0 ? (
                    myPlayed.map(c => <CardUI key={c.id} card={c} small />)
                  ) : (
                    <span className="text-[11px] text-gray-600">暂无</span>
                  )}
                </div>
              </div>
            </div>
          </section>
        </main>

        <AnimatePresence>
          {peekPile === 'myDeck' && (
            <DeckPeekModal
              title={`抽牌堆（${myDeck.length} 张）`}
              cards={myDeck}
              onClose={() => setPeekPile(null)}
            />
          )}
          {peekPile === 'myDiscard' && (
            <DeckPeekModal
              title={`弃牌堆（${myDiscard.length} 张）`}
              cards={myDiscard}
              onClose={() => setPeekPile(null)}
            />
          )}
        </AnimatePresence>

        {dragState && (() => {
          const card = myHand.find(c => c.id === dragState.cardId)
          if (!card) return null
          return (
            <div
              className="fixed pointer-events-none z-[200]"
              style={{
                left: dragState.x - dragState.offsetX,
                top: dragState.y - dragState.offsetY,
              }}
            >
              <div className={`w-20 h-28 rounded-xl border-2 ${card.attr.border}
                bg-gradient-to-br from-slate-800/95 to-slate-900/95 backdrop-blur
                flex flex-col items-center justify-center gap-1 shadow-2xl shadow-black/50
                rotate-3 scale-105 relative overflow-hidden`}
              >
                <div className={`absolute inset-0 bg-gradient-to-br ${card.attr.color} opacity-15`} />
                {(() => { const I = card.attr.icon || Star; return <I className={`w-6 h-6 ${card.attr.text} relative z-10`} /> })()}
                <span className={`text-xs font-bold ${card.attr.text} relative z-10`}>{card.attr.name}</span>
                <span className="text-sm font-black text-white relative z-10">{card.power}</span>
              </div>
            </div>
          )
        })()}
      </div>
    </motion.div>
  )
}
