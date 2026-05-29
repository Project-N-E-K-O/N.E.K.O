import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, BookOpen, Clock3, FileText, Flame, Heart, Layers, RotateCcw, Shield, Snowflake, Sparkles, Star, Volume2, VolumeX, Wind, X, Zap } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import NekoAvatar from '../NekoAvatar'
import CardInspectModal from './CardInspectModal'
import BattleResultOverlay from './BattleResultOverlay'
import BattleTutorialPanel from './BattleTutorialPanel'
import NekoCardBack from './NekoCardBack'
import { playNekoBrawlCardSfx } from './nekoBrawlAudio'
import {
  ADVENTURE_CARD_TYPES,
  ADVENTURE_DECK_SIZE,
  advanceAdventureRun,
  buildAdventureEndingStory,
  buildAdventureLogEntry,
  calculateAdventureSteps,
  createAdventureRun,
  describeAdventureReveal,
  enterSideAdventure,
  getCardActionPoint,
  getCardAttrId,
  pickBetterEventOutcome,
  resolveEventCheck,
  skipSideAdventure,
} from '../../data/nekoBrawlAdventureDeck'

const BATTLE_BACKGROUND_SRC = '/neko-brawl/Background_forest.png'

const LOG_HIGHLIGHT_RULES = [
  { pattern: /造成\s*\d+\s*点(?:（[^）]+）)?|受到\s*\d+\s*点伤害|追加伤害\s*\+\d+/y, className: 'font-black text-red-600' },
  { pattern: /回复低生命角色\s*\d+|(?:你|队友|双方)回复\s*\d+/y, className: 'font-black text-emerald-600' },
  { pattern: /(?:你|队友|双方)?护盾\s*\+\d+|护盾抵消\s*\d+/y, className: 'font-black text-sky-600' },
  { pattern: /Combo(?:效果)?：[^；]+|连续Combo\s*\d+\s*回合/y, className: 'font-black text-fuchsia-600' },
  { pattern: /抽\s*\d+/y, className: 'font-black text-cyan-600' },
  { pattern: /封锁Boss下回合行动|行动被封锁/y, className: 'font-black text-violet-600' },
  { pattern: /弱化|只能出1张牌/y, className: 'font-black text-amber-600' },
  { pattern: /Boss生命上限\s*\+\d+/y, className: 'font-black text-rose-600' },
  { pattern: /行动力不足|跳过出牌|无牌可出/y, className: 'font-black text-orange-600' },
]

const CARD_TEXT_HIGHLIGHT_RULES = [
  { pattern: /(?:对Boss)?造成\d+点伤害|额外造成\d+点伤害|伤害[+-]\d+/y, className: 'font-black text-red-600' },
  { pattern: /回复(?:生命最低的己方玩家|双方玩家各|自身|队友)?\d+点生命/y, className: 'font-black text-emerald-600' },
  { pattern: /(?:获得|提供)\d+点护盾|为(?:自己|队友|双方各)获得\d+点护盾/y, className: 'font-black text-sky-600' },
  { pattern: /抽\d+张牌/y, className: 'font-black text-cyan-600' },
  { pattern: /封锁boss下回合行动|封锁Boss下回合行动/y, className: 'font-black text-violet-600' },
  { pattern: /清除\d+个负面状态/y, className: 'font-black text-amber-600' },
]

function renderHighlightedText(text, rules) {
  const source = String(text || '')
  const nodes = []
  let index = 0

  while (index < source.length) {
    let matched = null
    for (const rule of rules) {
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

function renderBattleLogText(text) {
  return renderHighlightedText(text, LOG_HIGHLIGHT_RULES)
}

function renderCardEffectText(text) {
  return renderHighlightedText(text, CARD_TEXT_HIGHLIGHT_RULES)
}

function MiniCard({ card, selected, disabled, dimmed = disabled, dragging, onClick, onPointerDown }) {
  const AttrIcon = card?.attr?.icon || Sparkles
  const power = card?.power ?? 0
  const cost = card?.cost ?? Math.max(1, Math.ceil(power / 3))
  const attrName = card?.attr?.name || '羁绊'
  const hasDebuff = card?.debuffs?.length > 0
  const clickable = typeof onClick === 'function'
  const draggable = !disabled && typeof onPointerDown === 'function'
  return (
    <motion.button
      type="button"
      disabled={disabled && !clickable}
      whileHover={!disabled && !dragging ? { y: -12, rotate: 0, scale: 1.03 } : {}}
      whileTap={!disabled && !dragging ? { scale: 0.96 } : {}}
      onClick={clickable ? onClick : undefined}
      onPointerDown={draggable ? onPointerDown : undefined}
      className={`relative h-44 w-32 shrink-0 select-none overflow-hidden rounded-md border-[3px] bg-white shadow-lg transition-all ${
        selected
          ? 'border-sky-500 ring-4 ring-sky-300/60'
          : 'border-neutral-600'
      } ${disabled ? (dimmed ? `opacity-45 ${clickable ? 'cursor-zoom-in' : 'cursor-not-allowed'}` : clickable ? 'cursor-zoom-in' : 'cursor-default') : clickable && !draggable ? 'cursor-zoom-in hover:shadow-2xl' : 'cursor-grab hover:shadow-2xl'} ${dragging ? 'opacity-25' : ''}`}
      style={{ touchAction: 'none' }}
    >
      <div className={`absolute inset-x-0 top-0 h-16 ${card?.attr?.bg || 'bg-neutral-100'}`} />
      <div className="absolute inset-x-0 top-16 h-px bg-neutral-200" />

      <div className="absolute left-2 top-2 flex h-9 w-9 items-center justify-center rounded-full border-2 border-orange-400 bg-neutral-950 text-white shadow-md">
        <span className="text-lg font-black leading-none">{cost}</span>
      </div>

      <div className="absolute right-2 top-2 flex items-center gap-1 rounded-full border border-white/80 bg-white/90 px-2 py-1 text-[10px] font-black text-neutral-800 shadow-sm">
        <AttrIcon className={`h-3 w-3 ${card?.attr?.text || 'text-neutral-500'}`} />
        {attrName}
      </div>

      <div className="absolute inset-x-2 top-12 flex justify-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-full border-2 border-white bg-white shadow-sm">
          <AttrIcon className={`h-8 w-8 ${card?.attr?.text || 'text-neutral-500'}`} />
        </div>
      </div>

      <div className="absolute left-2 right-2 top-[6.6rem]">
        <div className="truncate text-left text-xs font-black text-neutral-950">
          {card?.name || (hasDebuff ? '弱化羁绊' : '羁绊打击')}
        </div>
        <div className="mt-1 min-h-12 rounded-sm bg-neutral-100 px-2 py-1 text-left text-[10px] leading-tight text-neutral-600">
          {renderCardEffectText(card?.mainText || '卡牌主效果')}
        </div>
      </div>

      {card?.temp && (
        <div className="absolute bottom-2 right-2 rounded-full bg-amber-400 px-1.5 py-0.5 text-[9px] font-black text-neutral-900">
          临时
        </div>
      )}
    </motion.button>
  )
}

const COMBO_ATTR_META = {
  passion: {
    name: '热情',
    icon: Flame,
    text: 'text-red-500',
    bg: 'bg-red-50',
    border: 'border-red-300',
    activeText: 'text-red-600',
    activeBg: 'bg-red-100',
    activeBorder: 'border-red-500',
    activeRing: 'ring-red-300/75',
    activeShadow: 'shadow-[0_0_18px_rgba(239,68,68,0.72)]',
  },
  gentle: {
    name: '温柔',
    icon: Heart,
    text: 'text-pink-500',
    bg: 'bg-pink-50',
    border: 'border-pink-300',
    activeText: 'text-pink-600',
    activeBg: 'bg-pink-100',
    activeBorder: 'border-pink-500',
    activeRing: 'ring-pink-300/75',
    activeShadow: 'shadow-[0_0_18px_rgba(236,72,153,0.7)]',
  },
  cool: {
    name: '高冷',
    icon: Snowflake,
    text: 'text-cyan-500',
    bg: 'bg-cyan-50',
    border: 'border-cyan-300',
    activeText: 'text-cyan-600',
    activeBg: 'bg-cyan-100',
    activeBorder: 'border-cyan-500',
    activeRing: 'ring-cyan-300/75',
    activeShadow: 'shadow-[0_0_18px_rgba(6,182,212,0.72)]',
  },
  natural: {
    name: '天然',
    icon: Wind,
    text: 'text-emerald-500',
    bg: 'bg-emerald-50',
    border: 'border-emerald-300',
    activeText: 'text-emerald-600',
    activeBg: 'bg-emerald-100',
    activeBorder: 'border-emerald-500',
    activeRing: 'ring-emerald-300/75',
    activeShadow: 'shadow-[0_0_18px_rgba(16,185,129,0.72)]',
  },
  fire: {
    name: '炎',
    icon: Flame,
    text: 'text-red-500',
    bg: 'bg-red-50',
    border: 'border-red-300',
    activeText: 'text-red-600',
    activeBg: 'bg-red-100',
    activeBorder: 'border-red-500',
    activeRing: 'ring-red-300/75',
    activeShadow: 'shadow-[0_0_18px_rgba(239,68,68,0.72)]',
  },
  ice: {
    name: '冰',
    icon: Snowflake,
    text: 'text-cyan-500',
    bg: 'bg-cyan-50',
    border: 'border-cyan-300',
    activeText: 'text-cyan-600',
    activeBg: 'bg-cyan-100',
    activeBorder: 'border-cyan-500',
    activeRing: 'ring-cyan-300/75',
    activeShadow: 'shadow-[0_0_18px_rgba(6,182,212,0.72)]',
  },
  wind: {
    name: '风',
    icon: Wind,
    text: 'text-emerald-500',
    bg: 'bg-emerald-50',
    border: 'border-emerald-300',
    activeText: 'text-emerald-600',
    activeBg: 'bg-emerald-100',
    activeBorder: 'border-emerald-500',
    activeRing: 'ring-emerald-300/75',
    activeShadow: 'shadow-[0_0_18px_rgba(16,185,129,0.72)]',
  },
  thunder: {
    name: '雷',
    icon: Zap,
    text: 'text-amber-500',
    bg: 'bg-amber-50',
    border: 'border-amber-300',
    activeText: 'text-amber-600',
    activeBg: 'bg-amber-100',
    activeBorder: 'border-amber-500',
    activeRing: 'ring-amber-300/80',
    activeShadow: 'shadow-[0_0_18px_rgba(245,158,11,0.78)]',
  },
  spirit: {
    name: '灵',
    icon: Sparkles,
    text: 'text-violet-500',
    bg: 'bg-violet-50',
    border: 'border-violet-300',
    activeText: 'text-violet-600',
    activeBg: 'bg-violet-100',
    activeBorder: 'border-violet-500',
    activeRing: 'ring-violet-300/75',
    activeShadow: 'shadow-[0_0_18px_rgba(139,92,246,0.72)]',
  },
}

function PlayerHeader({ align = 'left', name, hp = 6, shield = 0, deckCount = 0, label }) {
  const infoNode = (
    <div className={align === 'right' ? 'text-right' : 'text-left'}>
      <p className="text-xs text-neutral-500">{label}</p>
      <p className="text-lg font-black text-neutral-950">{name}</p>
      <p className="mt-1 text-xl tracking-wide text-neutral-800">{'♡'.repeat(Math.max(0, hp))}</p>
      <p className="mt-1 text-xs font-black text-sky-700">护盾：{shield}</p>
      <p className="mt-2 text-xs font-bold text-neutral-600">剩余卡牌数：{deckCount}</p>
    </div>
  )

  return (
    <div className={`flex items-center gap-3 ${align === 'right' ? 'justify-end' : 'justify-start'}`}>
      {infoNode}
    </div>
  )
}

function CornerNekoAvatar({ align = 'left', name, avatar, label }) {
  return (
    <section className={`absolute bottom-24 z-20 flex w-52 flex-col ${align === 'right' ? 'right-6 items-end' : 'left-6 items-start'}`}>
      <div className="w-40 lg:w-52">
        <NekoAvatar avatar={avatar} name={name} side={align === 'right' ? 'right' : 'left'} />
      </div>
      <div className={`mt-2 rounded-sm border border-neutral-300 bg-white/90 px-3 py-1.5 text-xs shadow-sm ${align === 'right' ? 'text-right' : 'text-left'}`}>
        <p className="font-black text-neutral-900">{name}</p>
        <p className="text-[10px] font-bold text-neutral-500">{label}</p>
      </div>
    </section>
  )
}

function SideBattleRail({
  align = 'left',
  name,
  label,
  avatar,
  hp = 6,
  shield = 0,
  deckCount = 0,
  handCount,
  energyCount,
  showActionPointUi = true,
  zoneRef,
  avatarRef,
  statusRef,
  zoneCards = [],
  maxCards = 1,
  zoneTitle,
  zoneHint,
  zoneReady = false,
  zoneThinking = false,
  zonePreview = false,
  zoneCardsDraggable = false,
  returningZoneCardId = null,
  onZoneCardPointerDown,
  onInspectCard,
  isAlly = false,
}) {
  const isRight = align === 'right'
  const sideClass = isRight ? 'right-0 border-l' : 'left-0 border-r'
  const textAlign = 'text-left'
  const avatarSide = 'left'
  const displayHp = Math.max(0, hp)
  const hpPercent = Math.max(0, Math.min(100, (displayHp / 6) * 100))
  const shieldPercent = Math.max(0, Math.min(100, (shield / 6) * 100))

  return (
    <aside className={`absolute bottom-0 top-0 z-20 flex w-[18rem] flex-col items-start ${sideClass} border-neutral-800/70 bg-[#171814]/95 px-7 pb-6 pt-24 shadow-[0_0_48px_rgba(0,0,0,0.34)]`}>
      <section
        ref={zoneRef}
        className={`flex h-[18rem] w-full flex-col items-center justify-center rounded-[2.1rem] border-4 border-dashed bg-transparent px-4 transition-all duration-200 ${
          zoneReady
            ? 'border-amber-400 shadow-[0_0_28px_rgba(245,158,11,0.18)]'
            : 'border-neutral-500'
        }`}
      >
        <p className={`mb-5 text-xl font-black ${zoneReady ? 'text-amber-300' : zoneThinking ? 'text-neutral-300' : 'text-neutral-300'}`}>
          {zoneReady ? '准备出击！' : zoneThinking ? '还在思考中' : zoneTitle}
        </p>
        {zoneCards.length > 0 ? (
          <div className="relative h-40 w-36">
            {zoneCards.slice(0, Math.max(1, maxCards)).map((card, index, cards) => {
              const spread = (index - (cards.length - 1) / 2) * 34
              const rotate = (index - (cards.length - 1) / 2) * 7
              const cardNode = (
                <div
                  className="absolute left-1/2 top-1/2"
                  style={{
                    transform: `translate(calc(-50% + ${spread}px), -50%) rotate(${rotate}deg) scale(0.82)`,
                    opacity: returningZoneCardId === card.id ? 0.2 : 1,
                  }}
                >
                  <MiniCard
                    card={card}
                    selected
                    disabled={!zoneCardsDraggable}
                    dimmed={false}
                    dragging={returningZoneCardId === card.id}
                    onClick={!zoneCardsDraggable ? () => onInspectCard?.(card, isAlly ? 'ally-zone' : 'player-zone') : undefined}
                    onPointerDown={zoneCardsDraggable ? (event) => onZoneCardPointerDown?.(event, card) : undefined}
                  />
                </div>
              )

              return isAlly ? (
                <motion.div
                  key={card.id}
                  className="absolute inset-0"
                  initial={{ opacity: 0, x: 140, y: 160, rotate: 16, scale: 0.62 }}
                  animate={{ opacity: 1, x: 0, y: 0, rotate: 0, scale: 1 }}
                  transition={{ type: 'spring', stiffness: 250, damping: 24 }}
                >
                  {cardNode}
                </motion.div>
              ) : (
                <div key={card.id}>
                  {cardNode}
                </div>
              )
            })}
            {zonePreview && (
              <div className="absolute -bottom-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full border border-sky-300 bg-white px-2 py-1 text-[10px] font-black text-sky-700 shadow-sm">
                待确认
              </div>
            )}
          </div>
        ) : (
          <div className="flex h-48 w-36 items-center justify-center bg-transparent px-4 text-center text-sm font-black text-neutral-300">
            {zoneHint}
          </div>
        )}
      </section>

      <section className="mt-6 flex w-full flex-col items-start">
        <div ref={avatarRef} className="w-56">
          <NekoAvatar avatar={avatar} name={name} side={avatarSide} />
        </div>

        <div className={`mt-3 border border-white/10 bg-neutral-950/70 px-4 py-3 shadow-lg ${textAlign}`}>
          <p className="text-sm font-black text-white">{name}</p>
          <p className="mt-1 text-[11px] font-bold text-neutral-300">{label}</p>
        </div>

        <div ref={statusRef} className={`mt-4 w-56 rounded-sm bg-neutral-950/88 px-6 py-5 shadow-[0_16px_36px_rgba(0,0,0,0.42)] ${textAlign}`}>
          <p className="text-sm font-bold text-neutral-400">玩家</p>
          <p className="mt-2 text-2xl font-black text-white">{name}</p>
          <p className="mt-3 whitespace-nowrap text-2xl leading-none tracking-wide text-white">{'♡'.repeat(displayHp)}</p>
          <div className="mt-4">
            <div className="mb-1 flex items-center justify-between text-xs font-black text-rose-200">
              <span>生命</span>
              <span>{displayHp} / 6</span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-rose-400" style={{ width: `${hpPercent}%` }} />
            </div>
          </div>
          <div className="mt-3">
            <div className="mb-1 flex items-center justify-between text-xs font-black text-sky-300">
              <span>护盾</span>
              <span>{shield}</span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-sky-400" style={{ width: `${shieldPercent}%` }} />
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex items-center gap-2 text-neutral-300">
              <NekoCardBack size="tiny" count={deckCount} muted={deckCount <= 0} />
              <p className="text-sm font-black">剩余卡牌数：{deckCount}</p>
            </div>
            {typeof handCount === 'number' && (
              <div className="flex items-center gap-2 text-neutral-300">
                <div className="relative h-10 w-11">
                  <div className="absolute left-0 top-0 rotate-[-8deg] scale-75">
                    <NekoCardBack size="tiny" muted={handCount <= 0} />
                  </div>
                  <div className="absolute left-4 top-0 rotate-[8deg] scale-75">
                    <NekoCardBack size="tiny" count={handCount} muted={handCount <= 0} />
                  </div>
                </div>
                <p className="text-sm font-black">手牌数：{handCount}</p>
              </div>
            )}
            {showActionPointUi && typeof energyCount === 'number' && (
              <p className="text-sm font-black text-amber-300">行动力：{energyCount}</p>
            )}
          </div>
        </div>
      </section>
    </aside>
  )
}

function AdventureDeckStack({ count = ADVENTURE_DECK_SIZE }) {
  return (
    <div className="pointer-events-auto flex items-center gap-3 rounded-sm border border-emerald-900/20 bg-white/92 px-4 py-3 shadow-[0_12px_32px_rgba(15,23,42,0.18)]">
      <div className="relative h-20 w-16 shrink-0">
        <div className="absolute left-0 top-3 h-16 w-12 rotate-[-9deg] rounded-md border-2 border-emerald-900/35 bg-emerald-100 shadow-sm" />
        <div className="absolute left-2 top-1.5 h-16 w-12 rotate-[-2deg] rounded-md border-2 border-emerald-900/35 bg-lime-100 shadow-sm" />
        <div className="absolute left-4 top-0 h-16 w-12 rotate-[6deg] overflow-hidden rounded-md border-2 border-emerald-900/70 bg-white shadow-md">
          <div className="h-5 bg-emerald-700" />
          <div className="flex h-11 items-center justify-center bg-[linear-gradient(135deg,#ffffff_0%,#ffffff_42%,#dcfce7_42%,#dcfce7_58%,#ffffff_58%)]">
            <Layers className="h-6 w-6 text-emerald-800" />
          </div>
        </div>
      </div>
      <div className="min-w-20">
        <p className="text-xs font-black text-emerald-800">探索牌组</p>
        <p className="mt-1 text-3xl font-black leading-none text-neutral-950">{count}</p>
        <p className="mt-1 text-[11px] font-bold text-neutral-500">剩余张数</p>
      </div>
    </div>
  )
}

const ADVENTURE_TYPE_LABEL = {
  rest: '休息',
  event: '事件',
  battle: '战斗',
  encounter: '奇遇',
  end: '终点',
}

// 双方确认后的发牌动画：从中央探索牌堆顶部依次飞出 N 张卡，**叠放**到牌堆左侧的同一个堆叠位。
// 所有卡共享 absolute (0,0) 槽位，靠 zIndex 决定层次 + 每张 +2px/-2px 的微偏移做出"是一摞牌"的层次感。
// 落点（i = n-1）是最后抽出、堆在最上面那张：入场后再 3D 翻转露正面。
// 入场每张 0.35s、间隔 0.25s、翻转 0.55s（与 finalize useEffect 的 totalMs 对齐）。
// 卡样式刻意复用 AdventureDeckFillStack 的绿色卡面（emerald 顶部条 + Layers 图标），
// 配合从 x=180 飞入做到"像从牌堆顶被抽出来"。定位由调用方控制 — 本组件只暴露固定 h-24 w-20 槽位。
//
// 未来扩展（用户已点明，数据层尚未支持，本实现先按"全部经过 + 最后一张落点揭示"画）：
// 重要节点（第 10/20/30/40 张等强制揭示卡）若被中途经过，应在它入堆后**立即正面朝上**呈现，
// 触发并结算其事件流程，然后再继续抽剩余的牌堆叠到它之上，最后一张照常翻面。届时需要：
//   ① advanceAdventureRun 在 revealResult 里返回"中途强制揭示"的卡索引数组
//   ② 这里按段播放（入堆 → 暂停等待事件结算回调 → 继续下一段入堆）
function AdventureDealOverlay({ cards }) {
  const n = cards.length
  const flipStart = (n - 1) * 0.25 + 0.35 + 0.2
  // Fragment：所有卡 absolute 到调用方提供的 relative 槽位。zIndex 用 50+i 确保叠在
  // DealtPile（zIndex 0-5）之上 — dealing 期间飞入卡必须可见地"落"到已抽卡堆顶部。
  return (
    <>
      {cards.map((card, i) => {
        const isReveal = i === n - 1
        const enterDelay = i * 0.25
        // 堆叠层次：每张相对前一张向右上偏 2px，让玩家能看见是一摞而非单张。
        // i = 0（最早抽出，最底）在 (0,0)；i = n-1（最上面那张）在 ((n-1)*2, -(n-1)*2)。
        const stackX = i * 2
        const stackY = -i * 2
        return (
          <motion.div
            key={`adventure-dealt-${i}`}
            initial={{ x: 180, y: -8, opacity: 0, rotateY: 0 }}
            animate={isReveal
              ? { x: stackX, y: stackY, opacity: 1, rotateY: 180 }
              : { x: stackX, y: stackY, opacity: 1, rotateY: 0 }
            }
            transition={isReveal
              ? {
                  x: { delay: enterDelay, duration: 0.35, ease: 'easeOut' },
                  y: { delay: enterDelay, duration: 0.35, ease: 'easeOut' },
                  opacity: { delay: enterDelay, duration: 0.25 },
                  rotateY: { delay: flipStart, duration: 0.55, ease: 'easeInOut' },
                }
              : {
                  delay: enterDelay,
                  duration: 0.35,
                  ease: 'easeOut',
                }
            }
            style={{
              transformStyle: 'preserve-3d',
              perspective: '800px',
              zIndex: 50 + i,
            }}
            className="absolute left-0 top-0 h-24 w-20"
          >
            {/* 背面 — 与 AdventureDeckFillStack 卡面一致：emerald 顶部条 + Layers 图标 */}
            <div
              className="absolute inset-0 overflow-hidden rounded-md border-2 border-emerald-900/70 bg-white shadow-md"
              style={{ backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden' }}
            >
              <div className="h-7 rounded-t-[3px] bg-emerald-700" />
              <div className="flex h-[4.05rem] items-center justify-center bg-[linear-gradient(135deg,#ffffff_0%,#ffffff_42%,#dcfce7_42%,#dcfce7_58%,#ffffff_58%)]">
                <Layers className="h-8 w-8 text-emerald-800" />
              </div>
            </div>
            {/* 正面 — 仅落点卡需要（未来：中途强制揭示卡也走这一层） */}
            {isReveal && (
              <div
                className="absolute inset-0 flex flex-col items-center justify-center overflow-hidden rounded-md border-2 border-emerald-700 bg-white p-2 text-center shadow-md"
                style={{
                  backfaceVisibility: 'hidden',
                  WebkitBackfaceVisibility: 'hidden',
                  transform: 'rotateY(180deg)',
                }}
              >
                <p className="text-[9px] font-bold uppercase tracking-wide text-emerald-700">
                  {ADVENTURE_TYPE_LABEL[card?.type] || card?.type || '事件'}
                </p>
                <p className="mt-1 text-[11px] font-black leading-tight text-emerald-900">
                  {card?.title || '未知'}
                </p>
              </div>
            )}
          </motion.div>
        )
      })}
    </>
  )
}

// 已抽过的卡堆（跨回合持久）— 按总数渲染最多 6 层视觉叠加 + "已抽 N" badge。
// 输入 count = adventureRun.mainIndex（已离开牌堆的总张数，含落点 + 经过）。
// dealing 期间这个值还是上回合末尾（finalize 后才 setAdventureRun），所以与本回合飞入的 DealOverlay
// 在视觉上不会重复 — 飞入完成后 pile 数字一次性跳到新值，飞入卡被"吸收"进 pile。
// Fragment：和 DealOverlay 共用调用方的 relative h-24 w-20 槽位。
function AdventureDealtPile({ count }) {
  if (!count || count <= 0) return null
  const visibleLayers = Math.min(count, 6)
  return (
    <>
      {Array.from({ length: visibleLayers }).map((_, i) => (
        <div
          key={`dealt-pile-${i}`}
          className="absolute left-0 top-0 h-24 w-20 overflow-hidden rounded-md border-2 border-emerald-900/70 bg-white shadow-md"
          style={{
            zIndex: i,
            transform: `translate(${i * 2}px, ${-i * 2}px)`,
          }}
        >
          <div className="h-7 rounded-t-[3px] bg-emerald-700" />
          <div className="flex h-[4.05rem] items-center justify-center bg-[linear-gradient(135deg,#ffffff_0%,#ffffff_42%,#dcfce7_42%,#dcfce7_58%,#ffffff_58%)]">
            <Layers className="h-8 w-8 text-emerald-800" />
          </div>
        </div>
      ))}
      <div
        className="absolute -top-3 left-1/2 z-[150] -translate-x-1/2 whitespace-nowrap rounded-full border border-emerald-800 bg-emerald-50 px-2 py-0.5 text-[10px] font-black text-emerald-800 shadow-sm"
        style={{ transform: `translateX(calc(-50% + ${(visibleLayers - 1) * 1}px))` }}
      >
        已抽 {count}
      </div>
    </>
  )
}

// static=true 时跳过 12 张入场堆叠 + 顶卡上浮的动画，整堆以最终态直接呈现。
// 抽牌阶段必须传 static —— 抽牌时玩家盯着发牌动画，不应同时看到牌堆"洗牌"。
function AdventureDeckFillStack({ count = ADVENTURE_DECK_SIZE, static: isStatic = false }) {
  const cards = Array.from({ length: 12 })
  return (
    <div className="mx-auto flex flex-col items-center">
      <div className="relative h-36 w-28">
        <div className="absolute inset-x-3 bottom-0 h-4 rounded-full bg-emerald-950/20 blur-md" />
        {cards.map((_, index) => (
          <motion.div
            key={index}
            className="absolute left-1/2 top-1/2 h-24 w-20 -translate-x-1/2 -translate-y-1/2 rounded-md border-2 border-emerald-900/70 bg-white shadow-md"
            initial={isStatic ? false : {
              opacity: 0,
              y: -84 - index * 4,
              x: -40 + index * 7,
              rotate: -18 + index * 3,
              scale: 0.86,
            }}
            animate={{
              opacity: 1,
              y: index * -3,
              x: 0,
              rotate: -5 + index * 0.9,
              scale: 1,
            }}
            transition={isStatic ? { duration: 0 } : {
              delay: index * 0.055,
              duration: 0.42,
              ease: [0.22, 1, 0.36, 1],
            }}
            style={{ zIndex: index }}
          >
            <div className="h-7 rounded-t-[3px] bg-emerald-700" />
            <div className="flex h-[4.05rem] items-center justify-center bg-[linear-gradient(135deg,#ffffff_0%,#ffffff_42%,#dcfce7_42%,#dcfce7_58%,#ffffff_58%)]">
              <Layers className="h-8 w-8 text-emerald-800" />
            </div>
          </motion.div>
        ))}
        <motion.div
          className="absolute left-1/2 top-1/2 z-20 flex h-24 w-20 -translate-x-1/2 -translate-y-1/2 items-end justify-center rounded-md border-2 border-emerald-950 bg-emerald-800 pb-3 text-white shadow-[0_14px_28px_rgba(6,78,59,0.28)]"
          initial={isStatic ? false : { opacity: 0, y: 24, scale: 0.92 }}
          animate={{ opacity: 1, y: -38, scale: 1 }}
          transition={isStatic ? { duration: 0 } : { delay: 0.72, duration: 0.36, ease: 'easeOut' }}
        >
          <Layers className="h-8 w-8" />
        </motion.div>
        {/* 「剩余 N 张」浮标 — 坐在卡堆正上方，紧贴顶卡 */}
        <div className="absolute -top-3 left-1/2 z-30 -translate-x-1/2 whitespace-nowrap rounded-full border border-emerald-800 bg-emerald-700 px-2.5 py-0.5 text-[10px] font-black text-white shadow-sm">
          剩余 {count} 张
        </div>
      </div>
    </div>
  )
}

export default function NewBattleDuelUI({
  nekoName,
  nekoAvatar,
  boss,
  bossImageSrc,
  bossImageState = 'normal',
  bossDamagePopup,
  comboPopup,
  comboAttrs: comboAttrsProp,
  bossBreakValue = 0,
  bossBreakGoal = 0,
  round,
  currentActor,
  myDeck,
  myHand,
  myDiscard,
  myPlayed = [],
  mySelected,
  myMaxPlay,
  playerEnergy,
  allyDeck,
  allyHand,
  allyPlayed = [],
  allyEnergy,
  showActionPointUi = true,
  playerHp = 6,
  playerShield = 0,
  allyHp = 6,
  allyShield = 0,
  bossBreakPercent,
  gameOver,
  outcome = 'playing',
  comboStats,
  playedCardStats = [],
  isPlayerTurn,
  onSetPreviewCard,
  onConfirmPlay,
  onSkipTurn,
  onRestart,
  onBackToClassic,
  onClose,
  temporaryBgmEnabled = true,
  onToggleTemporaryBgm,
  gameLog = [],
  comboReference = [],
  adventureDeckCount = ADVENTURE_DECK_SIZE,
  adventureMode = false,
}) {
  const [showBattleLog, setShowBattleLog] = useState(false)
  const [showComboList, setShowComboList] = useState(false)
  const [showBattleTutorial, setShowBattleTutorial] = useState(false)
  const [dragState, setDragState] = useState(null)
  const [dragOverBoss, setDragOverBoss] = useState(false)
  const [dragLeftHandZone, setDragLeftHandZone] = useState(false)
  const [returnDragState, setReturnDragState] = useState(null)
  const [returnFlyingCard, setReturnFlyingCard] = useState(null)
  const [flyingCard, setFlyingCard] = useState(null)
  const [starStrike, setStarStrike] = useState(null)
  const [inspectedCard, setInspectedCard] = useState(null)
  const [adventureRun, setAdventureRun] = useState(() => createAdventureRun())
  const [adventureRound, setAdventureRound] = useState(1)
  const [adventurePlayerConfirmed, setAdventurePlayerConfirmed] = useState(false)
  const [adventureAllyConfirmed, setAdventureAllyConfirmed] = useState(false)
  const [adventureAllyActionCard, setAdventureAllyActionCard] = useState(null)
  const [adventureResult, setAdventureResult] = useState(null)
  // 抽牌动画中间态：双方确认后，先播一段「从牌堆抽 N 张到左侧」的发牌动画，
  // 最后一张（落点）翻转后再写入 adventureResult。dealing 期间禁止重入 advance。
  const [adventureDealing, setAdventureDealing] = useState(null)
  // 事件交互态：落点是 EVENT 卡时进入。玩家A 与 AI队友依次"打牌完成事件"，
  // 取较好结果。phase: 'player'（等玩家选牌确认）→ 'ally'（队友象征性完成）→ 'resolved'（出结果）。
  // playerCardId 是本地选择（不动父组件手牌状态，符合"只显示结果文本"）。
  // kind 还包含 'rest'（确认休息）/ 'encounter'（支线进出）/ 'ending'（终点结算）。
  const [adventureEvent, setAdventureEvent] = useState(null)
  // 探险历程记录：每完成一个落点交互就累积一条，供终点结算统计与讲故事。
  const [adventureLog, setAdventureLog] = useState([])
  const playZoneRef = useRef(null)
  const bossStageRef = useRef(null)
  const playerAvatarRef = useRef(null)
  const playerStatusRef = useRef(null)
  const handZoneRef = useRef(null)
  const comboAttrs = comboAttrsProp?.length ? comboAttrsProp : boss?.weakness?.weak || []
  const battleUiActive = !adventureMode
  const canUseCards = adventureMode ? !adventurePlayerConfirmed && !adventureResult : isPlayerTurn
  // 事件交互、等玩家选响应牌的阶段：手牌从"拖拽推进"切换成"点选完成事件"。
  // 仅打牌检定事件（kind=event）需要点手牌；休息（kind=rest）只需点确认。
  const inEventPick = adventureMode && adventureEvent?.kind === 'event' && adventureEvent?.phase === 'player'
  const getCardCost = (card) => card?.cost ?? Math.max(1, Math.ceil((card?.power || 0) / 3))
  const selectedCards = mySelected
    .map(id => myHand.find(card => card.id === id))
    .filter(Boolean)
    .slice(0, 1)
  const energyBase = typeof playerEnergy === 'number' ? playerEnergy : 3 + round
  const selectedCost = selectedCards.reduce((sum, card) => sum + getCardCost(card), 0)
  const energy = Math.max(0, energyBase - selectedCost)
  const energyTooLow = selectedCost > energyBase
  const adventureAllyCards = adventureAllyActionCard ? [adventureAllyActionCard] : []
  const adventureStepPreview = calculateAdventureSteps(selectedCards, adventureAllyCards)
  const adventureActiveDeckCount = adventureMode
    ? adventureRun.activeSideAdventure
      ? Math.max(0, adventureRun.activeSideAdventure.deck.length - adventureRun.activeSideAdventure.index)
      : Math.max(0, adventureRun.mainDeck.length - adventureRun.mainIndex)
    : adventureDeckCount
  const allyCommitted = allyPlayed.length > 0
  const allyThinking = currentActor?.type === 'ally' && !allyCommitted && !gameOver
  const damageText = bossDamagePopup ? `${bossDamagePopup.amount}${bossDamagePopup.weak ? '!' : ''}` : ''
  const damagePopupMinWidth = bossDamagePopup
    ? `${Math.max(10, damageText.length * (bossDamagePopup.weak ? 2.1 : 1.55))}rem`
    : '10rem'
  const comboPopupText = comboPopup
    ? `${comboPopup.count} ${comboPopup.count === 1 ? 'Combo' : 'Combos'}!`
    : ''
  const comboPopupFontSize = comboPopup
    ? `${3.4 + (Math.min(5, comboPopup.sizeLevel || comboPopup.count) - 1) * 0.72}rem`
    : '3.4rem'
  const bossHpValue = Math.max(0, bossBreakGoal - bossBreakValue)
  const bossHpPercent = bossBreakGoal > 0
    ? Math.max(0, Math.min(100, Math.round((bossHpValue / bossBreakGoal) * 100)))
    : 0
  const bossImageTone = {
    attack: 'border-rose-300/50 shadow-[inset_0_0_0_1px_rgba(244,63,94,0.18)]',
    damageTaken: 'border-amber-300/50 shadow-[inset_0_0_0_1px_rgba(245,158,11,0.16)]',
    weakDamageTaken: 'border-sky-300/55 shadow-[inset_0_0_0_1px_rgba(14,165,233,0.2)]',
    normal: 'border-neutral-200/35',
  }[bossImageState] || 'border-neutral-200/35'
  const starStrikeIsAttack = starStrike?.kind === 'boss'
  const starStrikeIsSupport = Boolean(starStrike && !starStrikeIsAttack)

  const playedPlayerCards = myPlayed
  const playerZoneCards = playedPlayerCards.length > 0 ? playedPlayerCards : selectedCards
  const playerZoneIsPreview = playedPlayerCards.length === 0 && selectedCards.length > 0
  const activeComboCodes = new Set(
    playerZoneCards
      .filter(card => comboAttrs.includes(card?.comboAttr?.id || card?.comboAttrId || card?.attr?.id))
      .map(card => card.code)
      .filter(Boolean)
  )
  const comboListItems = [...comboReference].sort((a, b) => {
    const aActive = activeComboCodes.has(a.code) ? 1 : 0
    const bActive = activeComboCodes.has(b.code) ? 1 : 0
    return bActive - aActive || a.cost - b.cost || a.code.localeCompare(b.code)
  })
  const activeComboCard = dragState?.card || flyingCard?.card || (playerZoneIsPreview ? selectedCards[0] : null)
  const activeComboAttrId = activeComboCard?.comboAttr?.id || activeComboCard?.comboAttrId || activeComboCard?.attr?.id
  const visibleHandCards = myHand.filter(card => (
    !mySelected.includes(card.id) &&
    flyingCard?.card?.id !== card.id &&
    !(dragLeftHandZone && dragState?.card?.id === card.id)
  ))
  const isPointInBossZone = (x, y) => {
    const rect = bossStageRef.current?.getBoundingClientRect()
    if (!rect) return false
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
  }
  const isPointInHandZone = (x, y) => {
    const rect = handZoneRef.current?.getBoundingClientRect()
    if (!rect) return false
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
  }

  const getCardEffectTarget = (card) => {
    const effectBlocks = [card?.effects?.main]
    if (comboAttrs.includes(card?.comboAttr?.id || card?.comboAttrId || card?.attr?.id)) effectBlocks.push(card?.effects?.combo)
    const effects = effectBlocks.filter(Boolean)
    const targetsBoss = effects.some(effect => (
      effect.damage ||
      effect.skipBossNext ||
      effect.bossDamageReductionNext ||
      effect.bossDamageReductionThisRound
    ))
    const targetsHeal = effects.some(effect => effect.healLowest || effect.healSelf || effect.healBoth)
    const targetsShield = effects.some(effect => (
      effect.shieldSelf ||
      effect.shieldOther ||
      effect.shieldBoth ||
      effect.reduceSelfDamageThisRound ||
      effect.reduceOtherDamageThisRound
    ))
    const targetsDraw = effects.some(effect => effect.draw)

    // 临时目标分类：正式版建议在卡牌数据里写入 target 字段，避免通过效果字段推断。
    if (targetsBoss) return 'boss'
    if (targetsHeal) return 'heal'
    if (targetsShield) return 'shield'
    if (targetsDraw) return 'draw'
    return 'support'
  }

  const isCardComboActive = (card) => (
    comboAttrs.includes(card?.comboAttr?.id || card?.comboAttrId || card?.attr?.id)
  )

  const handleConfirmPlay = () => {
    if (!isPlayerTurn || selectedCards.length === 0 || energyTooLow) return

    const selectedCard = selectedCards[0]
    const sourceRect = playZoneRef.current?.getBoundingClientRect()
    const effectTarget = getCardEffectTarget(selectedCard)
    // 当前卡牌 SFX 是暂时占位实装用声音，不是最终结果；替换正式版音效时请注明“正式版音效”。
    playNekoBrawlCardSfx(effectTarget, { comboActive: isCardComboActive(selectedCard) })
    const targetRect = {
      boss: bossStageRef.current?.getBoundingClientRect(),
      heal: playerAvatarRef.current?.getBoundingClientRect() || playerStatusRef.current?.getBoundingClientRect(),
      shield: playerStatusRef.current?.getBoundingClientRect() || playerAvatarRef.current?.getBoundingClientRect(),
      draw: handZoneRef.current?.getBoundingClientRect(),
      support: playerStatusRef.current?.getBoundingClientRect() || playerAvatarRef.current?.getBoundingClientRect(),
    }[effectTarget]
    const sourceX = sourceRect ? sourceRect.left + sourceRect.width / 2 : window.innerWidth * 0.24
    const sourceY = sourceRect ? sourceRect.top + sourceRect.height / 2 : window.innerHeight * 0.38
    const targetX = targetRect
      ? targetRect.left + targetRect.width * (effectTarget === 'boss' ? 0.56 : 0.5)
      : window.innerWidth * (effectTarget === 'boss' ? 0.56 : effectTarget === 'draw' ? 0.5 : 0.16)
    const targetY = targetRect
      ? targetRect.top + targetRect.height * (effectTarget === 'boss' ? 0.43 : 0.5)
      : window.innerHeight * (effectTarget === 'boss' ? 0.42 : effectTarget === 'draw' ? 0.78 : 0.64)

    // 临时特效：按效果类型飞向不同区域，后续可替换为正式动画素材。
    setStarStrike({
      id: `star-strike-${Date.now()}`,
      kind: effectTarget,
      fromX: sourceX - 32,
      fromY: sourceY - 32,
      midX: (sourceX + targetX) / 2 - 32,
      midY: effectTarget !== 'boss'
        ? Math.min(sourceY, targetY) - 72
        : Math.min(sourceY, targetY) - 150,
      toX: targetX - 32,
      toY: targetY - 32,
    })
    onConfirmPlay?.()
  }

  const handleConfirmAdventurePlayer = () => {
    if (!adventureMode || selectedCards.length === 0 || adventureResult) return
    setAdventurePlayerConfirmed(true)
  }

  const handleConfirmAdventureAlly = () => {
    if (!adventureMode || adventureResult) return
    setAdventureAllyActionCard(prev => prev || allyHand[0] || null)
    setAdventureAllyConfirmed(true)
  }

  const handleContinueAdventure = () => {
    // 记录刚完成的落点到历程（终点结算讲故事用）。终点卡自身不记入。
    if (adventureResult?.card && adventureResult.card.type !== ADVENTURE_CARD_TYPES.END) {
      setAdventureLog(log => [...log, buildAdventureLogEntry(adventureResult, adventureEvent)])
    }
    setAdventurePlayerConfirmed(false)
    setAdventureAllyConfirmed(false)
    setAdventureAllyActionCard(null)
    setAdventureResult(null)
    setAdventureEvent(null)
    setAdventureRound(r => r + 1)
    onSetPreviewCard?.(null)
  }

  // 揭示落地：写入 run + result；若落点是事件卡，额外进入事件交互态（phase: player）。
  const applyRevealResult = (pendingResult, nextRun) => {
    setAdventureRun(nextRun)
    setAdventureResult(pendingResult)
    const card = pendingResult?.card
    const trigger = pendingResult?.trigger
    if (card?.type === ADVENTURE_CARD_TYPES.EVENT && trigger?.check) {
      setAdventureEvent({
        kind: 'event',
        check: trigger.check,
        instruction: trigger.eventInstruction || '打出一张行动卡来完成这次事件。',
        title: card.title,
        summary: card.summary,
        phase: 'player',
        playerCardId: null,
        playerResult: null,
        allyCardId: null,
        allyResult: null,
        outcome: null,
      })
    } else if (card?.type === ADVENTURE_CARD_TYPES.REST) {
      // 休息点：点「确认休息」即恢复。restEffects 是预留空档（heal-all / refill-hand），
      // 当前按 Q3 只显示文本不真正改血量/手牌；后期"特殊休息处"可在此扩展不同效果。
      setAdventureEvent({
        kind: 'rest',
        title: card.title,
        summary: card.summary,
        phase: 'rest-confirm',
        restEffects: trigger?.effects || [],
      })
    } else if (card?.type === ADVENTURE_CARD_TYPES.ENCOUNTER && !card.subDeck) {
      // 奇遇入口（仅主线）：双方确认进入支线 / 跳过。进入后 enterSideAdventure 设置
      // activeSideAdventure，之后 advanceAdventureRun 自动走支线牌堆；翻到支线 END 即回主线。
      setAdventureEvent({
        kind: 'encounter',
        title: card.title,
        summary: card.summary,
        phase: 'offer',
        encounterCard: card,
        playerChoice: null,
      })
    } else if (card?.type === ADVENTURE_CARD_TYPES.END && !card.subDeck) {
      // 主线终点：进入结算态，按历程生成总结小故事（先试后端 LLM，失败回退前端模板）。
      // 支线 END（subDeck=true）不走这里 —— 它由 revealFromSideDeck 自动回主线，走下面 else。
      setAdventureEvent({
        kind: 'ending',
        title: card.title,
        summary: card.summary,
        phase: 'generating',
        story: null,
        storySource: null,
      })
    } else {
      setAdventureEvent(null)
    }
  }

  // 奇遇：玩家选「进入支线 / 跳过」，交给 AI 队友附议（phase: ally-confirm）。
  const handleEncounterChoice = (choice) => {
    setAdventureEvent(e => (e && e.kind === 'encounter' && e.phase === 'offer')
      ? { ...e, playerChoice: choice, phase: 'ally-confirm' }
      : e)
  }

  // 玩家确认休息：当前仅推进到 resolved 显示"已恢复"文案（恢复效果留空档，见 applyRevealResult）。
  const handleConfirmRest = () => {
    setAdventureEvent(e => (e && e.kind === 'rest' && e.phase === 'rest-confirm') ? { ...e, phase: 'resolved' } : e)
  }

  // 玩家在事件交互期点手牌 = 选它作为事件响应牌（仅本地选择，不出牌、不动父组件手牌）。
  const handleSelectEventCard = (cardId) => {
    setAdventureEvent(e => (e && e.phase === 'player') ? { ...e, playerCardId: cardId } : e)
  }

  // 玩家确认"完成事件"：用选中的牌判定，存 playerResult，移交给 AI 队友（phase: ally）。
  const handleCompleteEventPlayer = () => {
    setAdventureEvent(e => {
      if (!e || e.phase !== 'player' || !e.playerCardId) return e
      const card = myHand.find(c => c.id === e.playerCardId) || null
      const playerResult = resolveEventCheck(e.check, card ? [card] : [])
      return { ...e, playerResult, phase: 'ally' }
    })
  }

  useEffect(() => {
    if (!adventureMode || !adventurePlayerConfirmed || !adventureAllyConfirmed || adventureResult || adventureDealing) return
    if (selectedCards.length === 0) return

    const next = advanceAdventureRun(adventureRun, selectedCards, adventureAllyCards)
    // 本回合从"当前所在牌堆"经过的全部牌（含末尾落点），用于发牌动画。
    // 支线推进改的是 activeSideAdventure.index（不是 mainIndex），所以要按当前所在牌堆切片：
    //   在支线 → 从 side.deck 切（回主线那一步取到支线末尾，含支线 END）
    //   在主线 → 从 mainDeck 切
    const sideBefore = adventureRun.activeSideAdventure
    let dealtCards
    if (sideBefore) {
      const sideStart = sideBefore.index
      const sideEnd = next.run.activeSideAdventure ? next.run.activeSideAdventure.index : sideBefore.deck.length
      dealtCards = sideBefore.deck.slice(sideStart, sideEnd)
    } else {
      dealtCards = adventureRun.mainDeck.slice(adventureRun.mainIndex, next.run.mainIndex)
    }
    const pendingResult = {
      id: `adventure-result-${Date.now()}`,
      card: next.revealedCards[0] || null,
      trigger: next.triggers[0] || null,
      stepResult: next.stepResult,
      revealResult: next.revealResult,
      description: describeAdventureReveal(next.revealResult),
    }

    if (dealtCards.length === 0) {
      applyRevealResult(pendingResult, next.run)
      return
    }

    setAdventureDealing({
      cards: dealtCards,
      nextRun: next.run,
      pendingResult,
    })
  }, [
    adventureMode,
    adventurePlayerConfirmed,
    adventureAllyConfirmed,
    adventureResult,
    adventureDealing,
    adventureRun,
    selectedCards,
    adventureAllyCards,
  ])

  // 发牌动画结束 → 落地真正的探险状态。时长 = 每张入场(0.25s 间隔) + 末张翻转(0.55s) + 收尾(0.4s)
  useEffect(() => {
    if (!adventureDealing) return
    const n = adventureDealing.cards.length
    const totalMs = Math.round(((n - 1) * 0.25 + 0.35 + 0.2 + 0.55 + 0.4) * 1000)
    const timer = setTimeout(() => {
      applyRevealResult(adventureDealing.pendingResult, adventureDealing.nextRun)
      setAdventureDealing(null)
    }, totalMs)
    return () => clearTimeout(timer)
  }, [adventureDealing])

  // AI 队友象征性完成事件：玩家提交后（phase=ally）短暂延迟，队友自动挑一张手牌
  // （优先匹配推荐属性以"象征性努力"，否则取第一张）判定，再取双方较好结果 → phase=resolved。
  // 真人队友联机后，这里会换成等待队友自己的打牌输入。
  useEffect(() => {
    if (!adventureEvent || adventureEvent.phase !== 'ally') return
    const timer = setTimeout(() => {
      setAdventureEvent(e => {
        if (!e || e.phase !== 'ally') return e
        const pool = Array.isArray(allyHand) ? allyHand : []
        const allyCard = pool.find(c => getCardAttrId(c) === e.check?.recommendedAttrId) || pool[0] || null
        const allyResult = resolveEventCheck(e.check, allyCard ? [allyCard] : [])
        const outcome = pickBetterEventOutcome(e.check, e.playerResult, allyResult)
        return { ...e, allyCardId: allyCard?.id || null, allyResult, outcome, phase: 'resolved' }
      })
    }, 900)
    return () => clearTimeout(timer)
  }, [adventureEvent?.phase])

  // 奇遇：玩家选定后 AI 队友附议（phase=ally-confirm），短暂延迟后执行 进入/跳过 支线 → resolved。
  // setAdventureRun 副作用放在 updater 之外，闭包捕获当次 playerChoice/encounterCard。
  useEffect(() => {
    if (!adventureEvent || adventureEvent.kind !== 'encounter' || adventureEvent.phase !== 'ally-confirm') return
    const { playerChoice, encounterCard } = adventureEvent
    const timer = setTimeout(() => {
      if (playerChoice === 'enter') {
        setAdventureRun(run => enterSideAdventure(run, encounterCard))
      } else {
        setAdventureRun(run => skipSideAdventure(run, encounterCard))
      }
      setAdventureEvent(e => (e && e.kind === 'encounter' && e.phase === 'ally-confirm') ? { ...e, phase: 'resolved' } : e)
    }, 700)
    return () => clearTimeout(timer)
  }, [adventureEvent?.kind, adventureEvent?.phase])

  // 终点结算：进入 ending(generating) 后生成总结故事。先请求后端 LLM（:3001），
  // 不可用 / 失败 → 回退前端模板。两条路都覆盖（用户要求"两种都做"）。
  useEffect(() => {
    if (!adventureEvent || adventureEvent.kind !== 'ending' || adventureEvent.phase !== 'generating') return
    let cancelled = false
    const themeId = adventureRun.themeId
    const logSnapshot = adventureLog
    ;(async () => {
      let story = buildAdventureEndingStory(logSnapshot, { themeId })
      let storySource = 'template'
      try {
        const res = await fetch('http://127.0.0.1:3001/arena/adventure-ending', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ log: logSnapshot, themeId }),
        })
        if (res.ok) {
          const data = await res.json()
          if (data?.success && typeof data.story === 'string' && data.story.trim()) {
            story = data.story.trim()
            storySource = 'llm'
          }
        }
      } catch (_) {
        // 后端不可用（如只起了前端）→ 用前端模板，已在 story 里。
      }
      if (!cancelled) {
        setAdventureEvent(e => (e && e.kind === 'ending' && e.phase === 'generating')
          ? { ...e, phase: 'ready', story, storySource }
          : e)
      }
    })()
    return () => { cancelled = true }
  }, [adventureEvent?.kind, adventureEvent?.phase])

  const inspectCard = (card, source = 'hand') => {
    setInspectedCard({ card, source })
  }

  const handleCardPointerDown = (event, card) => {
    if (!canUseCards) return
    event.preventDefault()
    const rect = event.currentTarget.getBoundingClientRect()
    setDragLeftHandZone(false)
    setDragState({
      card,
      x: event.clientX,
      y: event.clientY,
      startX: event.clientX,
      startY: event.clientY,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      moved: false,
    })
  }

  const handleZoneCardPointerDown = (event, card) => {
    if (!canUseCards || !playerZoneIsPreview) return
    event.preventDefault()
    const rect = event.currentTarget.getBoundingClientRect()
    setReturnDragState({
      card,
      x: event.clientX,
      y: event.clientY,
      startX: event.clientX,
      startY: event.clientY,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      moved: false,
      overHand: false,
    })
  }

  useEffect(() => {
    if (!dragState) return undefined

    const handlePointerMove = (event) => {
      event.preventDefault()
      const moved = dragState.moved || Math.hypot(event.clientX - dragState.startX, event.clientY - dragState.startY) > 8
      const nextDragOverBoss = isPointInBossZone(event.clientX, event.clientY)
      setDragOverBoss(prev => prev === nextDragOverBoss ? prev : nextDragOverBoss)
      const nextLeftHandZone = moved && !isPointInHandZone(event.clientX, event.clientY)
      setDragLeftHandZone(prev => prev === nextLeftHandZone ? prev : nextLeftHandZone)
      setDragState(prev => prev ? {
        ...prev,
        x: event.clientX,
        y: event.clientY,
        moved,
      } : prev)
    }

    const handlePointerUp = (event) => {
      const moved = dragState.moved || Math.hypot(event.clientX - dragState.startX, event.clientY - dragState.startY) > 8
      const droppedOnBoss = moved && isPointInBossZone(event.clientX, event.clientY)

      if (droppedOnBoss) {
        const targetRect = playZoneRef.current?.getBoundingClientRect()
        const fromX = event.clientX - dragState.offsetX
        const fromY = event.clientY - dragState.offsetY
        setFlyingCard({
          id: `${dragState.card.id}-${Date.now()}`,
          card: dragState.card,
          fromX,
          fromY,
          toX: targetRect ? targetRect.left + targetRect.width / 2 - 64 : fromX,
          toY: targetRect ? targetRect.top + targetRect.height / 2 - 88 : fromY,
        })
      } else if (!moved) {
        inspectCard(dragState.card, 'hand')
      }

      setDragOverBoss(false)
      setDragLeftHandZone(false)
      setDragState(null)
    }

    window.addEventListener('pointermove', handlePointerMove, { passive: false })
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('pointercancel', handlePointerUp)

    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('pointercancel', handlePointerUp)
    }
  }, [dragState])

  useEffect(() => {
    if (!returnDragState) return undefined

    const handlePointerMove = (event) => {
      event.preventDefault()
      const moved = returnDragState.moved || Math.hypot(event.clientX - returnDragState.startX, event.clientY - returnDragState.startY) > 8
      setReturnDragState(prev => prev ? {
        ...prev,
        x: event.clientX,
        y: event.clientY,
        moved,
        overHand: moved,
      } : prev)
    }

    const handlePointerUp = (event) => {
      const moved = returnDragState.moved || Math.hypot(event.clientX - returnDragState.startX, event.clientY - returnDragState.startY) > 8
      if (moved) {
        const targetRect = handZoneRef.current?.getBoundingClientRect()
        const fromX = event.clientX - returnDragState.offsetX
        const fromY = event.clientY - returnDragState.offsetY
        setReturnFlyingCard({
          id: `${returnDragState.card.id}-return-${Date.now()}`,
          card: returnDragState.card,
          fromX,
          fromY,
          toX: targetRect ? targetRect.left + targetRect.width / 2 - 64 : fromX,
          toY: targetRect ? targetRect.top + targetRect.height / 2 - 88 : fromY,
        })
      } else {
        inspectCard(returnDragState.card, 'player-zone')
      }
      setReturnDragState(null)
    }

    window.addEventListener('pointermove', handlePointerMove, { passive: false })
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('pointercancel', handlePointerUp)

    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('pointercancel', handlePointerUp)
    }
  }, [returnDragState, onSetPreviewCard])

  return (
    <motion.div
      key="new-battle-duel-ui"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      className="fixed inset-0 z-[100] overflow-hidden text-neutral-950"
      style={{ background: '#eaf6dd' }}
    >
      <div
        className="absolute inset-0"
        style={{
          backgroundImage: `linear-gradient(180deg, rgba(255,255,255,0.12), rgba(255,255,255,0.28)), url("${BATTLE_BACKGROUND_SRC}")`,
          backgroundPosition: 'center center',
          backgroundSize: 'cover',
        }}
      />

      <header className="relative z-10 flex h-16 items-center justify-between border-b-2 border-neutral-200 bg-white px-6">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 items-center justify-center rounded-sm border border-neutral-300 bg-white text-neutral-700 shadow-sm hover:bg-neutral-100"
            title="返回大厅"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          {battleUiActive && (
            <button
              type="button"
              onClick={onBackToClassic}
              className="rounded-sm border border-neutral-300 bg-neutral-100 px-3 py-2 text-xs font-bold text-neutral-700 hover:bg-neutral-200"
            >
              返回经典UI
            </button>
          )}
          {battleUiActive && (
            <button
              type="button"
              onClick={() => setShowBattleTutorial(true)}
              className="flex items-center gap-1 rounded-sm border border-neutral-300 bg-white px-3 py-2 text-xs font-bold text-neutral-700 shadow-sm hover:bg-neutral-100"
            >
              <BookOpen className="h-3.5 w-3.5" />
              教程
            </button>
          )}
          <div>
            <h2 className="text-xl font-black tracking-wide text-neutral-950">猫娘大乱斗 · {adventureMode ? '探险开始' : '新版对局UI'}</h2>
            <p className="text-xs text-neutral-500">
              {adventureMode ? '探索牌组 · 事件推进 · 战斗模块暂时隐藏' : '同步出牌 · Combo 属性 · 白底模块原型'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs font-bold text-neutral-700">
          <button
            type="button"
            onClick={onToggleTemporaryBgm}
            className={`flex items-center gap-1 rounded-sm border px-3 py-2 text-xs font-bold shadow-sm ${
              temporaryBgmEnabled
                ? 'border-amber-400 bg-amber-50 text-amber-700 hover:bg-amber-100'
                : 'border-neutral-300 bg-white text-neutral-500 hover:bg-neutral-100'
            }`}
            title="临时测试开关，后续会移除"
          >
            {temporaryBgmEnabled ? <Volume2 className="h-3.5 w-3.5" /> : <VolumeX className="h-3.5 w-3.5" />}
            临时BGM：{temporaryBgmEnabled ? '开' : '关'}
          </button>
          {adventureMode && (
            <span className="flex items-center gap-1 rounded-sm border border-emerald-400 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700 shadow-sm">
              探索回合：第 {adventureRound} 回合
            </span>
          )}
          <span className="rounded-sm border border-neutral-300 bg-neutral-100 px-3 py-2">
            {adventureMode ? '状态：探险开始' : `当前行动：${currentActor?.name || '整备'}`}
          </span>
          <span className="rounded-sm border border-neutral-300 bg-neutral-100 px-3 py-2">手牌 {myHand.length}</span>
        </div>
      </header>

      <main className="relative z-10 h-[calc(100vh-64px)]">
        <SideBattleRail
          name={nekoName || '猫娘 A'}
          label="玩家头像"
          avatar={nekoAvatar}
          hp={playerHp}
          shield={playerShield}
          deckCount={myDeck.length}
          zoneRef={playZoneRef}
          avatarRef={playerAvatarRef}
          statusRef={playerStatusRef}
          zoneCards={playerZoneCards}
          maxCards={myMaxPlay}
          zoneTitle={adventureMode ? '玩家行动区' : '玩家出牌区'}
          zoneHint={adventureMode ? '拖到探索区准备行动' : '拖到探索区打出'}
          zonePreview={playerZoneIsPreview}
          zoneCardsDraggable={playerZoneIsPreview && canUseCards}
          returningZoneCardId={returnDragState?.card?.id || returnFlyingCard?.card?.id}
          onZoneCardPointerDown={handleZoneCardPointerDown}
          onInspectCard={inspectCard}
          showActionPointUi={battleUiActive && showActionPointUi}
        />

        <div className="absolute left-1/2 top-6 z-30 w-44 -translate-x-1/2 select-none border-2 border-neutral-300 bg-neutral-100 text-center shadow-sm">
          <div className="border-b border-neutral-300 py-1 text-[11px] font-bold text-neutral-600">
            {adventureMode ? '探索回合' : '当前回合数'}
          </div>
          <div className="py-2 text-5xl font-light text-neutral-950">{round}</div>
        </div>

        <SideBattleRail
          align="right"
          name="猫娘 B"
          label="队友头像"
          hp={allyHp}
          shield={allyShield}
          deckCount={allyDeck.length}
          handCount={allyHand.length}
          energyCount={typeof allyEnergy === 'number' ? allyEnergy : 3 + round}
          showActionPointUi={battleUiActive && showActionPointUi}
          zoneCards={adventureMode ? adventureAllyCards : allyPlayed}
          maxCards={adventureMode ? 1 : Math.max(1, allyPlayed.length)}
          zoneTitle={adventureMode ? '队友行动区' : '队友出牌区'}
          zoneHint={adventureMode ? (adventureAllyConfirmed ? '准备出发！' : '等待队友确认') : allyThinking ? '还在思考中' : '队友预出牌'}
          zoneReady={adventureMode ? adventureAllyConfirmed : battleUiActive && allyCommitted}
          zoneThinking={adventureMode ? !adventureAllyConfirmed : battleUiActive && allyThinking}
          onInspectCard={inspectCard}
          isAlly
        />

        <section
          ref={bossStageRef}
          className={`pointer-events-none absolute bottom-0 top-0 z-0 overflow-hidden border-x border-b bg-transparent transition-all duration-200 ${
            dragOverBoss ? 'ring-4 ring-sky-300/70' : ''
          } ${bossImageTone} left-[18rem] right-[18rem]`}
        >
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-[min(42rem,58vw)] border-2 border-dashed border-emerald-900/25 bg-white/70 px-8 py-8 text-center shadow-[0_18px_48px_rgba(15,23,42,0.12)]">
              <p className="mt-4 text-2xl font-black text-emerald-950">{adventureMode ? '探险开始' : '探索区域'}</p>
              {adventureMode ? (
                <div className="mt-4 flex flex-col items-center">
                  {/* 牌堆区：左 = 已抽卡堆（持久 + dealing 期间飞入卡叠加） / 右 = 未探索卡堆（剩余 N 张）。
                      gap-10 是为给 DealOverlay 飞入轨迹留出一段空间；高 9rem 给 badge 上溢空间。 */}
                  <div className="flex h-36 items-center justify-center gap-10">
                    {/* 已抽过卡堆 + 本回合飞入动画共享同一 h-24 w-20 槽位 */}
                    <div className="relative h-24 w-20">
                      <AdventureDealtPile count={adventureRun.mainIndex} />
                      {adventureDealing && (
                        <AdventureDealOverlay cards={adventureDealing.cards} />
                      )}
                    </div>
                    {/* 未探索卡堆 — dealing 期间 static，避免与发牌动画并发"洗牌" */}
                    <AdventureDeckFillStack count={adventureActiveDeckCount} static={!!adventureDealing} />
                  </div>

                  {/* 牌堆区下方：dealing 提示 / 揭示事件面板 / 默认引导文字，三选一 */}
                  {adventureDealing ? (
                    <p className="mt-4 text-xs font-black text-emerald-800">
                      从探索牌组抽取 {adventureDealing.cards.length} 张…
                    </p>
                  ) : adventureResult ? (
                    <div className="mt-4 w-full text-left">
                      <div className="rounded-sm border-2 border-emerald-900/30 bg-white/85 p-4">
                        <p className="text-xs font-black text-emerald-700">揭示事件</p>
                        <p className="mt-1 text-xl font-black text-neutral-950">
                          {adventureResult.card?.title || '没有事件'}
                        </p>
                        <p className="mt-2 text-sm font-bold leading-relaxed text-neutral-700">
                          {adventureResult.card?.summary || '本次没有揭示新的探索牌。'}
                        </p>
                        <p className="mt-3 text-xs font-black leading-relaxed text-emerald-800">
                          {adventureResult.description}
                        </p>
                        {adventureEvent?.kind === 'rest' && (
                          <div className="mt-3 border-t border-emerald-900/15 pt-3">
                            <p className="text-xs font-black text-emerald-700">休息点</p>
                            <p className="mt-1 text-sm font-bold text-neutral-700">
                              {adventureEvent.phase === 'resolved'
                                ? '队伍已休息，恢复生命并补满手牌。'
                                : '在此休息可恢复生命、把手牌补满。点「确认休息」继续。'}
                            </p>
                          </div>
                        )}
                        {adventureEvent?.kind === 'encounter' && (
                          <div className="mt-3 border-t border-emerald-900/15 pt-3">
                            <p className="text-xs font-black text-emerald-700">奇遇支线</p>
                            {adventureEvent.phase === 'offer' && (
                              <p className="mt-1 text-sm font-bold text-neutral-700">
                                发现一条支线小路。进入会展开一段额外探索，走到尽头自动回到这里继续主线。
                              </p>
                            )}
                            {adventureEvent.phase === 'ally-confirm' && (
                              <>
                                <p className="mt-1 text-sm font-bold text-emerald-800">你的选择：{adventureEvent.playerChoice === 'enter' ? '进入支线' : '跳过'}</p>
                                <p className="mt-1 text-xs font-bold text-sky-700">队友确认中…</p>
                              </>
                            )}
                            {adventureEvent.phase === 'resolved' && (
                              <p className="mt-1 text-sm font-black text-emerald-700">
                                {adventureEvent.playerChoice === 'enter' ? '✦ 双方进入支线，开始额外探索。' : '✧ 双方决定跳过，继续主线。'}
                              </p>
                            )}
                          </div>
                        )}
                        {adventureEvent?.kind === 'ending' && (
                          <div className="mt-3 border-t border-emerald-900/15 pt-3">
                            <p className="text-xs font-black text-emerald-700">探险结算</p>
                            {adventureEvent.phase === 'generating' ? (
                              <p className="mt-1 text-sm font-bold text-neutral-500">正在回顾这趟旅程，生成总结小故事…</p>
                            ) : (
                              <>
                                <p className="mt-1 whitespace-pre-wrap text-sm font-bold leading-relaxed text-neutral-800">{adventureEvent.story}</p>
                                <p className="mt-2 text-[10px] font-bold text-neutral-400">
                                  {adventureEvent.storySource === 'llm' ? '由 NEKO 核心生成' : '本地总结'}
                                </p>
                              </>
                            )}
                          </div>
                        )}
                        {adventureEvent?.kind === 'event' && (
                          <div className="mt-3 border-t border-emerald-900/15 pt-3">
                            <p className="text-xs font-black text-emerald-700">事件检定</p>
                            <p className="mt-1 text-sm font-bold text-neutral-700">{adventureEvent.instruction}</p>
                            {adventureEvent.phase === 'player' && (
                              <p className="mt-2 text-xs font-bold text-neutral-500">
                                {adventureEvent.playerCardId
                                  ? `已选择：${myHand.find(c => c.id === adventureEvent.playerCardId)?.name || '一张牌'}`
                                  : '点下方手牌选择一张作为响应，再点「完成事件」。'}
                              </p>
                            )}
                            {adventureEvent.phase === 'ally' && (
                              <>
                                <p className="mt-2 text-xs font-bold text-emerald-800">玩家 A：{adventureEvent.playerResult?.detail}</p>
                                <p className="mt-1 text-xs font-bold text-sky-700">队友 B 正在完成事件…</p>
                              </>
                            )}
                            {adventureEvent.phase === 'resolved' && (
                              <div className="mt-2 space-y-1">
                                <p className="text-xs font-bold text-emerald-800">玩家 A：{adventureEvent.playerResult?.detail}</p>
                                <p className="text-xs font-bold text-sky-700">队友 B：{adventureEvent.allyResult?.detail}</p>
                                <p className={`mt-1 text-sm font-black ${adventureEvent.outcome?.success ? 'text-emerald-700' : 'text-neutral-500'}`}>
                                  {adventureEvent.outcome?.success
                                    ? `✦ 事件成功（按${adventureEvent.outcome.winner === 'player' ? '玩家 A' : '队友 B'}的发展记录）`
                                    : '✧ 两人都没达成，事件草草收场（仅记录）'}
                                </p>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <>
                      <p className="mt-3 text-sm font-bold text-neutral-600">
                        先把行动卡拖到左侧行动区，再等待双方确认。
                      </p>
                      <p className="mt-2 text-xs font-black text-emerald-800">
                        双方都确认后，才会根据平均行动力推进探索牌组。
                      </p>
                    </>
                  )}
                </div>
              ) : (
                <>
                  <div className="mx-auto mt-4 flex h-16 w-16 items-center justify-center rounded-full border-2 border-emerald-800/30 bg-emerald-50 text-emerald-800">
                    <Layers className="h-8 w-8" />
                  </div>
                  <p className="mt-2 text-sm font-bold text-neutral-600">
                    把卡牌拖到这里，决定本回合推进到哪一张探索牌。
                  </p>
                </>
              )}
            </div>
          </div>

          <AnimatePresence>
            {battleUiActive && comboPopup && (
              <motion.div
                key={comboPopup.id}
                initial={{ opacity: 0, scale: 0.45, rotate: -10, y: 28 }}
                animate={{
                  opacity: [0, 1, 1],
                  scale: [0.45, 1.18, 1],
                  rotate: [-10, 4, -3],
                  y: [28, -8, 0],
                }}
                exit={{ opacity: 0, scale: 0.92, y: -26 }}
                transition={{ duration: 0.42, ease: 'easeOut' }}
                className="absolute right-8 top-8 z-20 whitespace-nowrap text-right font-black leading-none text-yellow-300 drop-shadow-[0_7px_0_rgba(127,29,29,0.95)]"
                style={{
                  fontSize: comboPopupFontSize,
                  WebkitTextStroke: '2px #991b1b',
                }}
              >
                {comboPopupText}
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence>
            {battleUiActive && bossDamagePopup && (
              <motion.div
                key={bossDamagePopup.id}
                initial={{ opacity: 0, scale: 0.55, rotate: -8, y: 18 }}
                animate={{ opacity: 1, scale: 1, rotate: -5, y: 0 }}
                exit={{ opacity: 0, scale: 0.9, y: -18 }}
                transition={{ type: 'spring', stiffness: 360, damping: 20 }}
                className="absolute right-8 top-28 text-center"
                style={{
                  minWidth: damagePopupMinWidth,
                }}
              >
                <div
                  className={`absolute inset-0 border-4 shadow-[0_16px_42px_rgba(15,23,42,0.22)] ${
                    bossDamagePopup.weak
                      ? 'border-red-500 bg-yellow-100'
                      : 'border-neutral-800 bg-white'
                  }`}
                  style={{
                    clipPath: 'polygon(50% 0%, 61% 18%, 82% 9%, 78% 32%, 100% 42%, 80% 55%, 91% 78%, 66% 73%, 50% 100%, 35% 73%, 9% 78%, 20% 55%, 0% 42%, 22% 32%, 18% 9%, 39% 18%)',
                  }}
                />
                <div className={`relative z-10 flex min-h-28 flex-col items-center justify-center px-12 py-5 ${
                  bossDamagePopup.weak ? 'text-red-600' : 'text-neutral-950'
                }`}>
                  <p className="whitespace-nowrap text-[11px] font-black text-inherit opacity-75">本次伤害</p>
                  <p className={`${bossDamagePopup.weak ? 'text-6xl' : 'text-4xl'} mt-1 whitespace-nowrap font-black leading-none`}>
                    {damageText}
                  </p>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {battleUiActive && (
            <motion.div
              drag
              dragMomentum={false}
              dragElastic={0.06}
              whileDrag={{ scale: 1.03 }}
              className="pointer-events-auto absolute left-1/2 top-24 z-10 w-[min(34rem,42vw)] -translate-x-1/2 cursor-grab select-none border border-neutral-300/80 bg-white/90 px-5 py-4 text-center shadow-[0_14px_42px_rgba(15,23,42,0.14)] active:cursor-grabbing"
              style={{ touchAction: 'none' }}
              title="临时可拖拽：用于评估探索状态栏位置"
            >
              <div className="text-lg font-black text-emerald-800 drop-shadow-sm">森林探索</div>
              <div className="mt-3 w-full px-2">
                <div className="mb-1 flex justify-between text-xs font-black text-emerald-800">
                  <span>探索阻力</span>
                  <span>{bossHpValue} / {bossBreakGoal}</span>
                </div>
                <div className="h-3 rounded-full bg-emerald-100">
                  <div
                    className="h-full rounded-full bg-emerald-600"
                    style={{ width: `${bossHpPercent}%` }}
                  />
                </div>
              </div>
              <div className="mt-3 w-full border-t border-neutral-300/70 pt-3">
                <p className="text-[11px] font-black text-emerald-800">当前Combo属性</p>
                <div className="mt-2 flex justify-center gap-2">
                  {comboAttrs.map(attr => {
                    const meta = COMBO_ATTR_META[attr] || {
                      name: attr,
                      icon: Sparkles,
                      text: 'text-neutral-600',
                      bg: 'bg-white',
                      border: 'border-neutral-300',
                      activeText: 'text-neutral-900',
                      activeBg: 'bg-neutral-100',
                      activeBorder: 'border-neutral-700',
                      activeRing: 'ring-neutral-300/75',
                      activeShadow: 'shadow-[0_0_18px_rgba(82,82,82,0.45)]',
                    }
                    const AttrIcon = meta.icon
                    const isComboMatchedByActiveCard = activeComboAttrId === attr
                    return (
                      <div
                        key={attr}
                        className={`flex h-9 w-9 items-center justify-center rounded-full border-2 transition-all duration-150 ${
                          isComboMatchedByActiveCard
                            ? `scale-110 ${meta.activeBorder} ${meta.activeBg} ${meta.activeShadow} ring-4 ${meta.activeRing}`
                            : `${meta.border} ${meta.bg} shadow-sm`
                        }`}
                        title={meta.name}
                      >
                        <AttrIcon className={`${isComboMatchedByActiveCard ? `h-6 w-6 ${meta.activeText} drop-shadow-sm` : `h-5 w-5 ${meta.text}`} transition-all duration-150`} />
                      </div>
                    )
                  })}
                </div>
              </div>
            </motion.div>
          )}
        </section>

        <section ref={handZoneRef} className="absolute bottom-[5.8rem] left-1/2 z-20 flex min-h-56 -translate-x-1/2 items-end gap-4 px-5 py-3">
          {visibleHandCards.map((card, index) => (
            <div
              key={card.id}
              style={{ transform: `rotate(${(index - (visibleHandCards.length - 1) / 2) * 4}deg)` }}
              className="-mx-1"
            >
              <MiniCard
                card={card}
                selected={inEventPick ? adventureEvent.playerCardId === card.id : mySelected.includes(card.id)}
                disabled={inEventPick ? false : (!canUseCards || (!adventureMode && getCardCost(card) > energyBase))}
                dragging={dragState?.card?.id === card.id || flyingCard?.card?.id === card.id}
                onClick={() => inEventPick ? handleSelectEventCard(card.id) : inspectCard(card, 'hand')}
                onPointerDown={(event) => handleCardPointerDown(event, card)}
              />
            </div>
          ))}
        </section>

        {(!adventureMode || adventureResult) && (
          <section className="absolute bottom-[6.4rem] right-[19rem] z-30">
            <AdventureDeckStack count={adventureActiveDeckCount} />
          </section>
        )}

        {adventureMode && (
          <section className="pointer-events-none absolute bottom-3 left-[18rem] right-[18rem] z-30 flex justify-center">
            <div className="pointer-events-auto flex w-[min(52rem,70vw)] items-center justify-between gap-4 rounded-sm border-2 border-emerald-900/25 bg-white/94 px-5 py-3 shadow-[0_14px_38px_rgba(15,23,42,0.18)]">
              <div className="min-w-0">
                <p className="text-xs font-black text-emerald-800">探索确认</p>
                <p className="mt-1 truncate text-sm font-bold text-neutral-600">
                  {adventureEvent && adventureEvent.phase !== 'resolved'
                    ? (adventureEvent.kind === 'rest'
                        ? '在休息点恢复，点「确认休息」继续探索。'
                        : adventureEvent.kind === 'encounter'
                          ? '发现奇遇支线：双方确认「进入支线」或「跳过」。'
                          : adventureEvent.kind === 'ending'
                            ? '探险抵达终点，正在为这趟旅程做结算。'
                            : adventureEvent.instruction)
                    : selectedCards.length === 0
                      ? '请先拖出一张行动卡。'
                      : adventureResult
                        ? adventureResult.description
                        : `当前平均行动力 ${adventureStepPreview.averageActionPoint.toFixed(1)}，将揭示从牌组顶端向下第 ${adventureStepPreview.revealOrdinal || 0} 张。`}
                </p>
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-black">
                  <span className={`rounded-sm border px-2 py-1 ${adventurePlayerConfirmed ? 'border-emerald-500 bg-emerald-50 text-emerald-700' : 'border-neutral-300 bg-neutral-100 text-neutral-500'}`}>
                    玩家：{adventurePlayerConfirmed ? '已确认' : '未确认'}
                  </span>
                  <span className={`rounded-sm border px-2 py-1 ${adventureAllyConfirmed ? 'border-emerald-500 bg-emerald-50 text-emerald-700' : 'border-neutral-300 bg-neutral-100 text-neutral-500'}`}>
                    队友：{adventureAllyConfirmed ? '已确认' : '未确认'}
                  </span>
                  {selectedCards[0] && (
                    <span className="rounded-sm border border-orange-300 bg-orange-50 px-2 py-1 text-orange-700">
                      玩家行动力 {getCardActionPoint(selectedCards[0])}
                    </span>
                  )}
                  {adventureAllyActionCard && (
                    <span className="rounded-sm border border-sky-300 bg-sky-50 px-2 py-1 text-sky-700">
                      队友行动力 {getCardActionPoint(adventureAllyActionCard)}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {adventureEvent && adventureEvent.phase !== 'resolved' ? (
                  adventureEvent.kind === 'rest' ? (
                    <button
                      type="button"
                      onClick={handleConfirmRest}
                      className="h-12 rounded-sm border-2 border-emerald-800 bg-emerald-700 px-5 text-sm font-black text-white shadow-sm hover:bg-emerald-800"
                    >
                      确认休息
                    </button>
                  ) : adventureEvent.kind === 'encounter' ? (
                    adventureEvent.phase === 'offer' ? (
                      <>
                        <button
                          type="button"
                          onClick={() => handleEncounterChoice('enter')}
                          className="h-12 rounded-sm border-2 border-emerald-800 bg-emerald-700 px-5 text-sm font-black text-white shadow-sm hover:bg-emerald-800"
                        >
                          进入支线
                        </button>
                        <button
                          type="button"
                          onClick={() => handleEncounterChoice('skip')}
                          className="h-12 rounded-sm border-2 border-neutral-400 bg-white px-5 text-sm font-black text-neutral-600 shadow-sm hover:bg-neutral-100"
                        >
                          跳过
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        disabled
                        className="h-12 rounded-sm border-2 border-neutral-300 bg-neutral-200 px-5 text-sm font-black text-neutral-500"
                      >
                        队友确认中…
                      </button>
                    )
                  ) : adventureEvent.kind === 'ending' ? (
                    adventureEvent.phase === 'ready' ? (
                      <button
                        type="button"
                        onClick={onClose}
                        className="h-12 rounded-sm border-2 border-emerald-800 bg-emerald-700 px-5 text-sm font-black text-white shadow-sm hover:bg-emerald-800"
                      >
                        结束探险
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled
                        className="h-12 rounded-sm border-2 border-neutral-300 bg-neutral-200 px-5 text-sm font-black text-neutral-500"
                      >
                        结算中…
                      </button>
                    )
                  ) : adventureEvent.phase === 'player' ? (
                    <button
                      type="button"
                      onClick={handleCompleteEventPlayer}
                      disabled={!adventureEvent.playerCardId}
                      className="h-12 rounded-sm border-2 border-emerald-800 bg-emerald-700 px-5 text-sm font-black text-white shadow-sm hover:bg-emerald-800 disabled:cursor-not-allowed disabled:border-neutral-300 disabled:bg-neutral-200 disabled:text-neutral-500"
                    >
                      {adventureEvent.playerCardId ? '完成事件' : '请选择响应牌'}
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled
                      className="h-12 rounded-sm border-2 border-neutral-300 bg-neutral-200 px-5 text-sm font-black text-neutral-500"
                    >
                      队友完成中…
                    </button>
                  )
                ) : adventureResult ? (
                  <button
                    type="button"
                    onClick={handleContinueAdventure}
                    className="h-12 rounded-sm border-2 border-emerald-800 bg-emerald-700 px-5 text-sm font-black text-white shadow-sm hover:bg-emerald-800"
                  >
                    继续探索
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={handleConfirmAdventurePlayer}
                      disabled={selectedCards.length === 0 || adventurePlayerConfirmed}
                      className="h-12 rounded-sm border-2 border-neutral-900 bg-neutral-900 px-5 text-sm font-black text-white shadow-sm disabled:cursor-not-allowed disabled:border-neutral-300 disabled:bg-neutral-200 disabled:text-neutral-500"
                    >
                      {adventurePlayerConfirmed ? '玩家已确认' : '确认探索'}
                    </button>
                    <button
                      type="button"
                      onClick={handleConfirmAdventureAlly}
                      disabled={adventureAllyConfirmed}
                      className="h-12 rounded-sm border-2 border-sky-700 bg-sky-50 px-5 text-sm font-black text-sky-800 shadow-sm hover:bg-sky-100 disabled:cursor-not-allowed disabled:border-neutral-300 disabled:bg-neutral-100 disabled:text-neutral-400"
                    >
                      {adventureAllyConfirmed ? '队友已确认' : '队友确认'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </section>
        )}

        <CardInspectModal
          open={Boolean(inspectedCard)}
          card={inspectedCard?.card}
          source={inspectedCard?.source}
          onClose={() => setInspectedCard(null)}
        />

        <BattleTutorialPanel
          open={battleUiActive && showBattleTutorial}
          onClose={() => setShowBattleTutorial(false)}
        />

        {battleUiActive && (
        <section className="absolute bottom-[6.2rem] left-[calc(18rem+2rem)] z-20 flex flex-col items-center gap-2">
          {showActionPointUi && (
            <div className="flex h-24 w-24 items-center justify-center rounded-full border-4 border-orange-400 bg-white text-xl font-black shadow-sm">
              <div className="text-center">
                <Zap className="mx-auto h-5 w-5" />
                <p>{energy}</p>
                <p className="text-[10px] font-bold">行动力</p>
              </div>
            </div>
          )}
          <button
            type="button"
            onClick={() => setShowBattleLog(true)}
            className="flex items-center gap-1 rounded-sm border border-neutral-300 bg-white/90 px-3 py-1.5 text-[11px] font-bold text-neutral-700 shadow-sm hover:bg-neutral-100"
          >
            <FileText className="h-3.5 w-3.5" />
            战斗日志
          </button>
          <button
            type="button"
            onClick={() => setShowComboList(prev => !prev)}
            className={`flex items-center gap-1 rounded-sm border px-3 py-1.5 text-[11px] font-bold shadow-sm hover:bg-neutral-100 ${
              showComboList
                ? 'border-amber-400 bg-amber-50 text-amber-800 ring-2 ring-amber-200'
                : 'border-neutral-300 bg-white/90 text-neutral-700'
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            Combo列表
          </button>
        </section>
        )}

        {battleUiActive && (
        <section className="pointer-events-none absolute bottom-3 left-[18rem] right-[18rem] z-30 flex items-center justify-center gap-4">
          <button
            type="button"
            onClick={handleConfirmPlay}
            disabled={!isPlayerTurn || selectedCards.length === 0 || energyTooLow}
            className={`pointer-events-auto h-14 w-80 rounded-md border-2 text-sm font-black shadow-sm ${
              isPlayerTurn && selectedCards.length > 0 && !energyTooLow
                ? 'border-neutral-900 bg-neutral-900 text-white'
                : 'border-neutral-300 bg-neutral-200 text-neutral-500'
            }`}
          >
            {energyTooLow ? `行动力不足 ${selectedCost}/${energyBase}` : '确认出牌'}
          </button>
          <button
            type="button"
            onClick={onSkipTurn}
            disabled={!isPlayerTurn}
            className="pointer-events-auto h-14 rounded-md border-2 border-neutral-300 bg-white px-5 text-sm font-bold text-neutral-700 shadow-sm disabled:opacity-40"
          >
            跳过
          </button>
          <button
            type="button"
            onClick={onRestart}
            className="pointer-events-auto flex h-14 items-center gap-2 rounded-md border-2 border-neutral-300 bg-white px-5 text-sm font-bold text-neutral-700 shadow-sm"
          >
            <RotateCcw className="h-4 w-4" />
            重开
          </button>
          <button
            type="button"
            className="pointer-events-auto flex h-14 items-center gap-2 rounded-md border-2 border-neutral-300 bg-white px-5 text-sm font-bold text-neutral-700 shadow-sm"
          >
            <Clock3 className="h-4 w-4" />
            自动战斗
          </button>
        </section>
        )}

        <AnimatePresence>
          {battleUiActive && gameOver && (
            <BattleResultOverlay
              outcome={outcome}
              round={round}
              boss={boss}
              bossHp={bossHpValue}
              playerHp={playerHp}
              playerShield={playerShield}
              allyHp={allyHp}
              allyShield={allyShield}
              comboStats={comboStats}
              playedCardStats={playedCardStats}
              onRestart={onRestart}
              onClose={onClose}
              onInspectCard={inspectCard}
            />
          )}
        </AnimatePresence>

        <AnimatePresence>
          {battleUiActive && showBattleLog && (
            <motion.div
              key="battle-log-drawer"
              className="absolute inset-0 z-40 bg-transparent"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setShowBattleLog(false)}
            >
              <motion.aside
                initial={{ x: '-105%' }}
                animate={{ x: 0 }}
                exit={{ x: '-105%' }}
                transition={{ type: 'spring', stiffness: 320, damping: 34 }}
                onClick={(event) => event.stopPropagation()}
                className="flex h-full w-[min(380px,82vw)] flex-col border-r-2 border-neutral-300 bg-white shadow-2xl"
              >
                <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
                  <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-neutral-700" />
                    <p className="text-sm font-black text-neutral-900">战斗日志</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowBattleLog(false)}
                    className="flex h-8 w-8 items-center justify-center rounded-sm border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100"
                    title="关闭战斗日志"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-4">
                  {gameLog.length === 0 ? (
                    <div className="flex h-full items-center justify-center text-sm text-neutral-400">
                      暂无战斗记录
                    </div>
                  ) : (
                    gameLog.map(item => (
                      <div
                        key={item.id}
                        className={`rounded-sm border px-3 py-2 text-xs leading-relaxed ${
                          item.type === 'round'
                            ? 'border-neutral-300 bg-white text-center font-black text-neutral-950'
                            : 'border-neutral-200 bg-neutral-50 text-neutral-700'
                        }`}
                      >
                        {item.type === 'round' ? item.text : renderBattleLogText(item.text)}
                      </div>
                    ))
                  )}
                </div>
              </motion.aside>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {battleUiActive && showComboList && (
            <motion.div
              key="combo-list-drawer"
              className="pointer-events-none absolute inset-0 z-40 bg-transparent"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <motion.aside
                initial={{ x: '-105%', opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                exit={{ x: '-105%', opacity: 0 }}
                transition={{ type: 'spring', stiffness: 320, damping: 34 }}
                className="pointer-events-auto absolute bottom-0 left-0 top-[24rem] flex w-[18rem] flex-col border-r-2 border-t-2 border-neutral-300 bg-white shadow-2xl"
              >
                <div className="flex items-center border-b border-neutral-200 px-4 py-3">
                  <div className="flex items-center gap-2">
                    <Sparkles className="h-4 w-4 text-neutral-700" />
                    <p className="text-sm font-black text-neutral-900">Combo效果列表</p>
                  </div>
                </div>
                <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-4">
                  {comboListItems.length === 0 ? (
                    <div className="flex h-full items-center justify-center text-sm text-neutral-400">
                      暂无Combo资料
                    </div>
                  ) : (
                    comboListItems.map(card => {
                      const isActive = activeComboCodes.has(card.code)
                      const comboAttrId = card.comboAttrId || card.attrId
                      const meta = COMBO_ATTR_META[comboAttrId] || {
                        name: card.comboAttrName || card.attrName || comboAttrId,
                        icon: Sparkles,
                        text: 'text-neutral-600',
                        bg: 'bg-neutral-50',
                        border: 'border-neutral-300',
                        activeBg: 'bg-neutral-100',
                        activeBorder: 'border-neutral-700',
                        activeRing: 'ring-neutral-300/75',
                        activeShadow: 'shadow-[0_0_18px_rgba(82,82,82,0.45)]',
                      }
                      const AttrIcon = meta.icon
                      return (
                        <article
                          key={card.code}
                          className={`rounded-sm border-2 px-3 py-2 text-left transition-all duration-150 ${
                            isActive
                              ? `${meta.activeBorder} ${meta.activeBg} ${meta.activeShadow} ring-4 ${meta.activeRing}`
                              : 'border-neutral-200 bg-neutral-50'
                          }`}
                        >
                          <div className="flex min-w-0 items-center gap-2">
                            <p className="min-w-0 flex-1 truncate text-sm font-black text-neutral-950">
                              {card.code} · {card.name}
                              <span className="ml-2 text-[11px] font-black text-neutral-500">行动力 {card.cost}</span>
                              {isActive && <span className="ml-2 text-[10px] font-black text-amber-700">可触发</span>}
                            </p>
                            <div className={`flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-black ${isActive ? `${meta.activeBorder} bg-white/80 ${meta.activeText || meta.text}` : `${meta.border} ${meta.bg} ${meta.text}`}`}>
                              <AttrIcon className="h-3.5 w-3.5" />
                              {meta.name}
                            </div>
                          </div>
                          <p className={`mt-1 truncate text-xs font-black leading-relaxed ${isActive ? 'text-neutral-950' : 'text-neutral-700'}`}>
                            Combo：{card.comboText}
                          </p>
                        </article>
                      )
                    })
                  )}
                </div>
              </motion.aside>
            </motion.div>
          )}
        </AnimatePresence>

        {dragState && (
          <div
            className="pointer-events-none fixed z-[180]"
            style={{
              left: dragState.x - dragState.offsetX,
              top: dragState.y - dragState.offsetY,
              transform: `rotate(${dragOverBoss ? 0 : -5}deg) scale(${dragOverBoss ? 1.08 : 1.02})`,
              transition: 'transform 120ms ease',
            }}
          >
            <MiniCard card={dragState.card} selected={dragOverBoss} />
          </div>
        )}

        {returnDragState && (
          <div
            className="pointer-events-none fixed z-[185]"
            style={{
              left: returnDragState.x - returnDragState.offsetX,
              top: returnDragState.y - returnDragState.offsetY,
              transform: `rotate(${returnDragState.overHand ? -2 : 4}deg) scale(${returnDragState.overHand ? 1.02 : 0.96})`,
              transition: 'transform 120ms ease',
            }}
          >
            <MiniCard card={returnDragState.card} selected={returnDragState.overHand} />
          </div>
        )}

        {returnFlyingCard && (
          <motion.div
            key={returnFlyingCard.id}
            className="pointer-events-none fixed left-0 top-0 z-[190] will-change-transform"
            initial={{
              x: returnFlyingCard.fromX,
              y: returnFlyingCard.fromY,
              rotate: 4,
              scale: 0.96,
              opacity: 1,
            }}
            animate={{
              x: returnFlyingCard.toX,
              y: returnFlyingCard.toY,
              rotate: -4,
              scale: 0.86,
              opacity: 1,
            }}
            transition={{ type: 'spring', stiffness: 300, damping: 28, mass: 0.72 }}
            onAnimationComplete={() => {
              setReturnFlyingCard(null)
              onSetPreviewCard?.(null)
            }}
          >
            <MiniCard card={returnFlyingCard.card} selected disabled dimmed={false} />
          </motion.div>
        )}

        {flyingCard && (
          <motion.div
            key={flyingCard.id}
            className="pointer-events-none fixed left-0 top-0 z-[190] will-change-transform"
            initial={{
              x: flyingCard.fromX,
              y: flyingCard.fromY,
              rotate: -5,
              scale: 1.02,
              opacity: 1,
            }}
            animate={{
              x: flyingCard.toX,
              y: flyingCard.toY,
              rotate: 0,
              scale: 0.82,
              opacity: 1,
            }}
            transition={{ type: 'spring', stiffness: 300, damping: 30, mass: 0.75 }}
            onAnimationComplete={() => {
              const previewCardId = flyingCard.card.id
              setFlyingCard(null)
              onSetPreviewCard?.(previewCardId)
            }}
          >
            <MiniCard card={flyingCard.card} selected disabled dimmed={false} />
          </motion.div>
        )}

        {starStrike && (
          <motion.div
            key={starStrike.id}
            className="pointer-events-none fixed left-0 top-0 z-[195] h-16 w-16 will-change-transform"
            initial={{
              x: starStrike.fromX,
              y: starStrike.fromY,
              rotate: 0,
              scale: starStrikeIsSupport ? 0.54 : 0.72,
              opacity: 0,
            }}
            animate={{
              x: [starStrike.fromX, starStrike.midX, starStrike.toX],
              y: [starStrike.fromY, starStrike.midY, starStrike.toY],
              rotate: starStrikeIsSupport ? [0, 90, 180] : [0, 520, 920],
              scale: starStrikeIsSupport ? [0.54, 1.05, 1.18] : [0.72, 1.25, 0.82],
              opacity: starStrikeIsSupport ? [0, 0.92, 0.96] : [0, 1, 1],
            }}
            transition={{
              duration: starStrikeIsSupport ? 0.86 : 0.62,
              times: [0, 0.55, 1],
              ease: 'easeInOut',
            }}
            onAnimationComplete={() => setStarStrike(null)}
          >
            {starStrike.kind === 'heal' ? (
              <>
                <div className="absolute left-1/2 top-1/2 h-24 w-24 -translate-x-1/2 -translate-y-1/2 rounded-full bg-rose-200/30 blur-xl" />
                <div className="absolute -left-10 top-1/2 h-3 w-16 -translate-y-1/2 rounded-full bg-gradient-to-l from-rose-200/80 to-transparent blur-[2px]" />
                <Heart className="absolute left-2 top-2 h-10 w-10 fill-rose-200 text-rose-400 drop-shadow-[0_0_14px_rgba(251,113,133,0.75)]" />
                <Sparkles className="absolute right-1 top-7 h-6 w-6 text-white drop-shadow-[0_0_10px_rgba(255,255,255,0.95)]" />
              </>
            ) : starStrike.kind === 'shield' ? (
              <>
                <div className="absolute left-1/2 top-1/2 h-24 w-24 -translate-x-1/2 -translate-y-1/2 rounded-full bg-sky-200/25 blur-xl" />
                <div className="absolute -left-10 top-1/2 h-3 w-16 -translate-y-1/2 rounded-full bg-gradient-to-l from-sky-200/80 to-transparent blur-[2px]" />
                <Shield className="absolute left-2 top-1 h-11 w-11 fill-sky-100 text-sky-300 drop-shadow-[0_0_14px_rgba(125,211,252,0.78)]" />
                <Star className="absolute right-2 bottom-1 h-5 w-5 fill-white text-white drop-shadow-[0_0_9px_rgba(255,255,255,0.85)]" />
              </>
            ) : starStrike.kind === 'draw' ? (
              <>
                <div className="absolute left-1/2 top-1/2 h-24 w-24 -translate-x-1/2 -translate-y-1/2 rounded-full bg-cyan-200/25 blur-xl" />
                <div className="absolute -left-10 top-1/2 h-3 w-16 -translate-y-1/2 rounded-full bg-gradient-to-l from-cyan-200/80 to-transparent blur-[2px]" />
                <Layers className="absolute left-2 top-2 h-10 w-10 text-cyan-200 drop-shadow-[0_0_14px_rgba(103,232,249,0.78)]" />
                <Sparkles className="absolute right-1 top-7 h-6 w-6 text-white drop-shadow-[0_0_10px_rgba(255,255,255,0.95)]" />
              </>
            ) : starStrike.kind === 'support' ? (
              <>
                <div className="absolute left-1/2 top-1/2 h-24 w-24 -translate-x-1/2 -translate-y-1/2 rounded-full bg-pink-200/30 blur-xl" />
                <div className="absolute -left-10 top-1/2 h-3 w-16 -translate-y-1/2 rounded-full bg-gradient-to-l from-pink-200/70 to-transparent blur-[2px]" />
                <Sparkles className="absolute left-1 top-1 h-8 w-8 text-pink-200 drop-shadow-[0_0_12px_rgba(251,207,232,0.95)]" />
                <Star className="absolute left-7 top-5 h-7 w-7 fill-sky-100 text-sky-200 drop-shadow-[0_0_12px_rgba(186,230,253,0.95)]" />
                <Star className="absolute right-2 top-8 h-5 w-5 fill-emerald-100 text-emerald-200 drop-shadow-[0_0_10px_rgba(187,247,208,0.95)]" />
                <Sparkles className="absolute bottom-1 right-5 h-6 w-6 text-white drop-shadow-[0_0_10px_rgba(255,255,255,0.95)]" />
              </>
            ) : (
              <>
                <div className="absolute -left-14 top-1/2 h-3 w-20 -translate-y-1/2 rounded-full bg-gradient-to-l from-amber-300/80 to-transparent blur-[1px]" />
                <div className="absolute left-1/2 top-1/2 h-20 w-20 -translate-x-1/2 -translate-y-1/2 rounded-full bg-amber-300/25 blur-md" />
                <Star className="relative h-16 w-16 fill-amber-300 text-amber-500 drop-shadow-[0_0_18px_rgba(245,158,11,0.95)]" />
                <Sparkles className="absolute -right-2 -top-2 h-6 w-6 text-yellow-100 drop-shadow-[0_0_10px_rgba(255,255,255,0.9)]" />
              </>
            )}
          </motion.div>
        )}
      </main>
    </motion.div>
  )
}
