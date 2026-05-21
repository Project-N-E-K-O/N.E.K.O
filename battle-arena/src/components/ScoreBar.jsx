export default function ScoreBar({ score, maxScore = 300, side = 'left', label = '羁绊评分' }) {
  const safeScore = score ?? 0
  const safeMaxScore = maxScore || 1
  const percent = Math.max(0, Math.min(100, (safeScore / safeMaxScore) * 100))

  const barColor = side === 'left'
    ? 'from-purple-400 to-violet-500'
    : 'from-pink-400 to-rose-500'

  return (
    <div className="w-full">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-gray-400">{label}</span>
        <span className="text-xs font-bold text-white">{safeScore}</span>
      </div>
      <div className="h-3 w-full overflow-hidden rounded-full border border-white/10 bg-black/40">
        <div
          className={`relative h-full rounded-full bg-gradient-to-r ${barColor} transition-all duration-1000 ease-out`}
          style={{ width: `${percent}%` }}
        >
          <div className="absolute inset-0 rounded-full bg-gradient-to-b from-white/30 to-transparent" />
        </div>
      </div>
    </div>
  )
}
