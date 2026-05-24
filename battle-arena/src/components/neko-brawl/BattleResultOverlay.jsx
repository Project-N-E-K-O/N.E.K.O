import { ArrowLeft, Heart, MousePointerClick, RotateCcw, Shield, Sparkles, Swords } from 'lucide-react'
import { motion } from 'framer-motion'
import NekoCardBack from './NekoCardBack'

function pickFeaturedCard(stats = []) {
  if (!stats.length) return null
  const withDamage = stats.filter(item => (item.damage || 0) > 0)
  if (withDamage.length > 0) {
    return [...withDamage].sort((a, b) => (b.damage || 0) - (a.damage || 0))[0]
  }
  return stats[stats.length - 1]
}

export default function BattleResultOverlay({
  outcome,
  round,
  boss,
  bossHp = 0,
  playerHp = 0,
  playerShield = 0,
  allyHp = 0,
  allyShield = 0,
  comboStats,
  playedCardStats = [],
  onRestart,
  onClose,
  onInspectCard,
}) {
  const won = outcome === 'win'
  const featured = pickFeaturedCard(playedCardStats)
  const featuredCard = featured?.cardSnapshot
  const comboTotal = comboStats?.total || 0
  const comboBest = comboStats?.best || 0
  const totalDamage = playedCardStats.reduce((sum, item) => sum + (item.damage || 0), 0)
  const playerDamage = playedCardStats
    .filter(item => item.owner === 'player')
    .reduce((sum, item) => sum + (item.damage || 0), 0)
  const allyDamage = playedCardStats
    .filter(item => item.owner === 'ally')
    .reduce((sum, item) => sum + (item.damage || 0), 0)
  const playedCount = playedCardStats.length
  const comboPlayedCount = playedCardStats.filter(item => item.comboActive).length
  const FeaturedIcon = featuredCard?.attr?.icon || Sparkles
  const featuredCost = featuredCard?.cost ?? Math.max(1, Math.ceil((featuredCard?.power || 0) / 3))
  const featuredAttrName = featuredCard?.attr?.name || featuredCard?.attrName || featuredCard?.attrId || '属性'

  return (
    <motion.div
      key="battle-result-overlay"
      className="absolute inset-0 z-[230] flex items-center justify-center overflow-y-auto bg-white/82 px-4 py-6 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.article
        className="grid max-h-full w-full max-w-5xl grid-cols-1 overflow-hidden rounded-sm border-2 border-neutral-950 bg-white text-neutral-950 shadow-2xl lg:grid-cols-[minmax(0,1fr)_320px]"
        initial={{ opacity: 0, y: 28, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 18, scale: 0.98 }}
        transition={{ type: 'spring', stiffness: 260, damping: 28 }}
      >
        <section className="min-h-0 overflow-y-auto p-5 lg:p-7">
          <div className="flex items-start justify-between gap-5 border-b-2 border-neutral-950 pb-5">
            <div>
              <div className="flex items-center gap-2">
                <Swords className={`h-7 w-7 ${won ? 'text-emerald-600' : 'text-red-600'}`} />
                <p className={`text-4xl font-black ${won ? 'text-emerald-700' : 'text-red-700'}`}>
                  {won ? '胜利' : '失败'}
                </p>
              </div>
              <p className="mt-2 text-sm font-bold text-neutral-600">
                {won ? `${boss?.name || 'Boss'} 已被击破` : `${boss?.name || 'Boss'} 仍剩余 ${bossHp} 点生命`}
              </p>
            </div>
            <div className="border-2 border-neutral-950 px-4 py-3 text-right">
              <p className="text-xs font-black text-neutral-500">回合</p>
              <p className="text-3xl font-black">{round}</p>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3 xl:grid-cols-4">
            <div className="border-2 border-rose-300 bg-rose-50 p-4">
              <p className="flex items-center gap-1 text-xs font-black text-rose-700"><Heart className="h-4 w-4" />玩家生命</p>
              <p className="mt-2 text-2xl font-black">{playerHp}</p>
            </div>
            <div className="border-2 border-sky-300 bg-sky-50 p-4">
              <p className="flex items-center gap-1 text-xs font-black text-sky-700"><Shield className="h-4 w-4" />玩家护盾</p>
              <p className="mt-2 text-2xl font-black">{playerShield}</p>
            </div>
            <div className="border-2 border-rose-300 bg-rose-50 p-4">
              <p className="flex items-center gap-1 text-xs font-black text-rose-700"><Heart className="h-4 w-4" />队友生命</p>
              <p className="mt-2 text-2xl font-black">{allyHp}</p>
            </div>
            <div className="border-2 border-sky-300 bg-sky-50 p-4">
              <p className="flex items-center gap-1 text-xs font-black text-sky-700"><Shield className="h-4 w-4" />队友护盾</p>
              <p className="mt-2 text-2xl font-black">{allyShield}</p>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="border-2 border-amber-300 bg-amber-50 p-4">
              <p className="flex items-center gap-1 text-xs font-black text-amber-700"><Sparkles className="h-4 w-4" />Combo总触发</p>
              <p className="mt-2 text-3xl font-black">{comboTotal}</p>
            </div>
            <div className="border-2 border-fuchsia-300 bg-fuchsia-50 p-4">
              <p className="flex items-center gap-1 text-xs font-black text-fuchsia-700"><Sparkles className="h-4 w-4" />最高连续</p>
              <p className="mt-2 text-3xl font-black">{comboBest}</p>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3 xl:grid-cols-5">
            <div className="border-2 border-red-300 bg-red-50 p-3">
              <p className="text-[11px] font-black text-red-700">总伤害</p>
              <p className="mt-1 text-2xl font-black">{totalDamage}</p>
            </div>
            <div className="border-2 border-neutral-300 bg-neutral-50 p-3">
              <p className="text-[11px] font-black text-neutral-600">玩家伤害</p>
              <p className="mt-1 text-2xl font-black">{playerDamage}</p>
            </div>
            <div className="border-2 border-neutral-300 bg-neutral-50 p-3">
              <p className="text-[11px] font-black text-neutral-600">队友伤害</p>
              <p className="mt-1 text-2xl font-black">{allyDamage}</p>
            </div>
            <div className="border-2 border-sky-300 bg-sky-50 p-3">
              <p className="text-[11px] font-black text-sky-700">有效出牌</p>
              <p className="mt-1 text-2xl font-black">{playedCount}</p>
            </div>
            <div className="border-2 border-violet-300 bg-violet-50 p-3">
              <p className="text-[11px] font-black text-violet-700">Combo牌</p>
              <p className="mt-1 text-2xl font-black">{comboPlayedCount}</p>
            </div>
          </div>

          <div className="mt-6 flex gap-3">
            <button
              type="button"
              onClick={onRestart}
              className="flex h-12 items-center gap-2 border-2 border-neutral-950 bg-neutral-950 px-5 text-sm font-black text-white hover:bg-neutral-800"
            >
              <RotateCcw className="h-4 w-4" />
              再来一局
            </button>
            <button
              type="button"
              onClick={onClose}
              className="flex h-12 items-center gap-2 border-2 border-neutral-950 bg-white px-5 text-sm font-black hover:bg-neutral-100"
            >
              <ArrowLeft className="h-4 w-4" />
              返回大厅
            </button>
          </div>
        </section>

        <aside className="min-h-0 overflow-y-auto border-t-2 border-neutral-950 bg-neutral-100 p-5 lg:border-l-2 lg:border-t-0">
          <p className="text-xs font-black text-neutral-500">本局高光卡牌</p>
          {featuredCard ? (
            <button
              type="button"
              onClick={() => onInspectCard?.(featuredCard, 'result')}
              className="mt-4 w-full border-2 border-neutral-950 bg-white p-4 text-left shadow-[5px_5px_0_#18181b] hover:bg-neutral-50"
            >
              <div className="relative overflow-hidden border-2 border-neutral-950 bg-white">
                <div className={`h-16 ${featuredCard?.attr?.bg || 'bg-neutral-100'}`} />
                <div className="absolute left-3 top-3 flex h-10 w-10 items-center justify-center rounded-full border-2 border-orange-500 bg-neutral-950 text-lg font-black text-white">
                  {featuredCost}
                </div>
                <div className="absolute right-3 top-3 flex items-center gap-1 border-2 border-neutral-950 bg-white px-2 py-1 text-[11px] font-black">
                  <FeaturedIcon className={`h-3.5 w-3.5 ${featuredCard?.attr?.text || 'text-neutral-700'}`} />
                  {featuredAttrName}
                </div>
                <div className="flex flex-col items-center px-4 pb-4 pt-2">
                  <div className="flex h-20 w-20 items-center justify-center rounded-full border-4 border-white bg-white shadow-md">
                    <FeaturedIcon className={`h-11 w-11 ${featuredCard?.attr?.text || 'text-neutral-700'}`} />
                  </div>
                  <p className="mt-3 text-xs font-black text-neutral-500">{featuredCard.code}</p>
                  <h3 className="mt-1 text-center text-xl font-black leading-tight">{featuredCard.name}</h3>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-1">
                <div className="border-2 border-red-300 bg-red-50 p-2">
                  <p className="text-[10px] font-black text-red-700">单次伤害</p>
                  <p className="text-xl font-black">{featured.damage || 0}</p>
                </div>
                <div className="border-2 border-neutral-300 bg-neutral-50 p-2">
                  <p className="text-[10px] font-black text-neutral-600">来源</p>
                  <p className="text-sm font-black">{featured.owner === 'ally' ? '队友' : '玩家'} · 第 {featured.round} 回合</p>
                </div>
              </div>
              {featured.comboActive && (
                <p className="mt-2 border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-black text-amber-700">
                  触发Combo
                </p>
              )}
              {featured.effectSummary && (
                <p className="mt-3 line-clamp-3 border-2 border-dashed border-neutral-300 bg-neutral-50 p-2 text-xs font-bold leading-relaxed text-neutral-700">
                  {featured.effectSummary}
                </p>
              )}
              <p className="mt-4 flex items-center gap-1 text-xs font-black text-neutral-500">
                <MousePointerClick className="h-3.5 w-3.5" />
                点击查看卡牌故事与详情
              </p>
            </button>
          ) : (
            <div className="mt-4 border-2 border-dashed border-neutral-400 bg-white p-5 text-sm font-bold text-neutral-500">
              <NekoCardBack size="medium" muted label="暂无高光" />
              本局暂无有效出牌记录
            </div>
          )}
        </aside>
      </motion.article>
    </motion.div>
  )
}
