import { Trophy, Star, TrendingUp, Sparkles } from 'lucide-react'
import NekoAvatar from './NekoAvatar'
import ScoreBar from './ScoreBar'

/**
 * 猫娘角色卡
 * - 左侧 side="left"，右侧 side="right"
 * - score: 本局累计评分（替代血条）
 */
export default function NekoCard({ neko, side = 'left', isActive, score = 0 }) {
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

        <div className="space-y-2">
          {/*
            TODO: [羁绊接入] 这里将来 map 渲染 BondCard 组件
            每个羁绊是玩家与猫娘互动生成的文本摘要
            羁绊数据由外部模块（互动系统）提供
          */}
          {[0, 1, 2].map(i => (
            <div
              key={i}
              className="rounded-xl border border-dashed border-white/[0.07]
                         bg-white/[0.015] px-3 py-4 flex items-center justify-center
                         min-h-[64px]"
            >
              <span className="text-[11px] text-gray-700">羁绊槽位 #{i + 1}</span>
            </div>
          ))}
        </div>
      </div>

    </div>
  )
}
