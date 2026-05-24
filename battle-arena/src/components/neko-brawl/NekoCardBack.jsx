import { Sparkles } from 'lucide-react'

const sizeClasses = {
  tiny: 'h-14 w-10 rounded',
  small: 'h-20 w-14 rounded-lg',
  medium: 'h-28 w-20 rounded-xl',
  large: 'h-36 w-24 rounded-xl',
}

export default function NekoCardBack({ size = 'medium', count, label, muted = false }) {
  const cardSize = sizeClasses[size] || sizeClasses.medium

  return (
    <div className="relative inline-flex flex-col items-center gap-1">
      {typeof count === 'number' && (
        <span className="absolute -right-2 -top-2 z-10 flex h-5 min-w-5 items-center justify-center rounded-full border border-white/60 bg-orange-500 px-1 text-[10px] font-black text-white shadow-sm">
          {count}
        </span>
      )}
      <div
        className={`${cardSize} relative overflow-hidden border-2 shadow-lg ${
          muted
            ? 'border-neutral-500/40 bg-neutral-800/80'
            : 'border-fuchsia-300/70 bg-gradient-to-br from-[#40115f] via-[#1f3d7a] to-[#0f766e]'
        }`}
      >
        <div className="absolute inset-1 rounded-[inherit] border border-white/25" />
        <div className="absolute inset-x-0 top-0 h-1/2 bg-white/10" />
        <div className="absolute -left-5 top-3 h-16 w-16 rounded-full bg-pink-300/20 blur-xl" />
        <div className="absolute -right-5 bottom-2 h-16 w-16 rounded-full bg-cyan-300/20 blur-xl" />
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="flex h-10 w-10 items-center justify-center rounded-full border-2 border-white/60 bg-white/15">
            <Sparkles className="h-5 w-5 text-white drop-shadow" />
          </div>
        </div>
        <div className="absolute bottom-2 left-1/2 w-[72%] -translate-x-1/2 border-t border-white/30" />
      </div>
      {label && <span className="text-[9px] font-black text-current">{label}</span>}
    </div>
  )
}
