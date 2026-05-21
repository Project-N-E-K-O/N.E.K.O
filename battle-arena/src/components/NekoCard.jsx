import { Trophy, Star, TrendingUp, Sparkles } from 'lucide-react'
import NekoAvatar from './NekoAvatar'
import ScoreBar from './ScoreBar'

/**
 * 猫娘角色卡
 * - 左侧 side="left"，右侧 side="right"
 * - score: 本局累计评分（替代血条）
 */
export default function NekoCard({ neko, side = 'left', isActive, score = 0, revealBonds = false, equippedBonds, bondMenuSlot, onBondSlotClick, onEquipBond, onUnequipBond, forgedInventory }) {
  const isLeft = side === 'left'
  const align  = isLeft ? 'items-start' : 'items-end'
  const tAlign = isLeft ? 'text-left'   : 'text-right'
  const rowDir = isLeft ? ''            : 'flex-row-reverse'

  return (
    <div className={`flex flex-col ${align} justify-between gap-0 h-full`}>

      {/* ── 名称区 ── */}
      <div className={`${tAlign} w-full pb-3`}>
        <div
          className="flex items-center gap-2 flex-wrap mb-1"
          style={{ justifyContent: isLeft ? 'flex-start' : 'flex-end' }}
        >
          <span className="px-2 py-0.5 rounded-md bg-amber-500/15 border border-amber-500/30 text-amber-400 text-[11px] font-bold tracking-wider">
            {neko.rank}
          </span>
          <span className="text-gray-500 text-xs">Lv.{neko.level}</span>
        </div>

        <h2 className="text-2xl lg:text-3xl font-black gradient-text leading-tight">
          {neko.name}
        </h2>
        <p className="text-sm text-gray-400 mt-0.5">「{neko.title}」</p>
        <p className="text-xs text-gray-600 mt-1">主人 · {neko.owner}</p>
      </div>

      {/* ── 头像 ── */}
      {/*
        TODO: [头像接入] neko.avatar 当前为 null
        将来由头像提取功能提供 URL → 传入 NekoAvatar 的 avatar prop
      */}
      <div className="w-full flex justify-center py-2">
        <NekoAvatar avatar={neko.avatar} name={neko.name} side={side} />
      </div>

      {/* ── 本局评分条 ── */}
      <div className="w-full pt-1 pb-3">
        <ScoreBar score={score} maxScore={300} side={side} label="本局评分" />
      </div>

      {/* ── 历史战绩 ── */}
      <div className={`flex gap-3 text-xs pb-3 ${rowDir}`}>
        <span className="flex items-center gap-1 text-amber-400">
          <Trophy className="w-3 h-3" /> {neko.wins}胜
        </span>
        <span className="flex items-center gap-1 text-gray-500">
          <Star className="w-3 h-3" /> {neko.totalBattles}场
        </span>
        <span className="flex items-center gap-1 text-violet-400">
          <TrendingUp className="w-3 h-3" /> {neko.winRate}%
        </span>
      </div>

      {/* ── 羁绊槽位 ── */}
      <div className="w-full">
        <div
          className="flex items-center gap-1.5 mb-2"
          style={{ justifyContent: isLeft ? 'flex-start' : 'flex-end' }}
        >
          <Sparkles className={`w-3.5 h-3.5 ${isLeft ? 'text-violet-400' : 'text-pink-400'}`} />
          <span className="text-xs font-semibold text-gray-400">羁绊</span>
        </div>

        <div className="space-y-1.5">
          {[0, 1, 2, 3, 4].map(i => {
            const equipped = isLeft && equippedBonds ? equippedBonds[i] : null
            const isMenuOpen = isLeft && bondMenuSlot === i

            return (
              <div key={i} className="relative">
                <div
                  onClick={isLeft && onBondSlotClick ? () => onBondSlotClick(i) : undefined}
                  className={`rounded-xl border px-3 py-2 min-h-[40px] flex items-center transition-all
                    ${isLeft ? 'cursor-pointer hover:bg-violet-500/[0.08]' : ''}
                    ${equipped
                      ? (isLeft ? 'border-violet-500/30 bg-violet-500/[0.06]' : 'border-pink-500/20 bg-pink-500/[0.04]')
                      : 'border-dashed border-white/[0.07] bg-white/[0.015]'
                    }
                    ${isMenuOpen ? 'ring-1 ring-violet-400/40' : ''}
                  `}
                >
                  {equipped ? (
                    <div className="flex items-center justify-between w-full gap-2">
                      <span className={`text-[11px] leading-snug font-medium truncate
                        ${isLeft ? 'text-violet-300/90' : 'text-pink-300/70'}`}>
                        {equipped.name}
                      </span>
                      <span className={`text-[9px] px-1.5 py-0.5 rounded-full border flex-shrink-0 ${equipped.rarityStyle || 'border-white/10 text-gray-400'}`}>
                        {equipped.rarity || ''}
                      </span>
                    </div>
                  ) : (
                    <span className={`text-[11px] w-full text-center
                      ${isLeft ? 'text-gray-600' : 'text-gray-700'}`}>
                      {!isLeft && !revealBonds ? `私密槽位 #${i + 1}` : `槽位 #${i + 1}`}
                    </span>
                  )}
                </div>

                {isMenuOpen && forgedInventory && (
                  <div className="absolute left-0 right-0 top-full mt-1 z-30 rounded-xl border border-violet-400/30 bg-[#1a2332] shadow-xl max-h-[200px] overflow-y-auto">
                    <div className="px-3 py-2 border-b border-white/10 flex items-center justify-between">
                      <span className="text-[10px] text-gray-400 font-semibold">选择羁绊卡片</span>
                      {equipped && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onUnequipBond && onUnequipBond(i) }}
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
                        const alreadyEquipped = equippedBonds && equippedBonds.some((eb, idx) => eb && eb.id === card.id && idx !== i)
                        return (
                          <div
                            key={card.id}
                            onClick={(e) => {
                              e.stopPropagation()
                              if (!alreadyEquipped) onEquipBond && onEquipBond(i, card)
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
  )
}
