export default function ScoreBar({ score, maxScore = 300, side = 'left', label = '羁绊评分' }) {
  const safeScore = score ?? 0
  const safeMaxScore = maxScore || 1
  const percent = Math.max(0, Math.min(100, (safeScore / safeMaxScore) * 100))

  const barColor = side === 'left'
    ? 'from-purple-400 to-violet-500'
    : 'from-pink-400 to-rose-500'

  return (
    <div className="w-full">
      <div className="flex justify-between items-center mb-1">
        <span className="text-xs text-gray-400 font-medium">{label}</span>
        <span className="text-xs font-bold text-white">{safeScore}</span>
      </div>
      <div className="w-full h-3 bg-black/40 rounded-full overflow-hidden border border-white/10">
        <div
          className={`h-full rounded-full bg-gradient-to-r ${barColor} relative transition-all duration-1000 ease-out`}
          style={{ width: `${percent}%` }}
        >
          <div className="absolute inset-0 bg-gradient-to-b from-white/30 to-transparent rounded-full" />
        </div>
      </div>
    </div>
  )
}
