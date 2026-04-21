import { motion, AnimatePresence } from 'framer-motion'
import { BookOpen, Award, Sparkles } from 'lucide-react'

const ICON = {
  judge:  <Award     className="w-3 h-3 text-amber-400  flex-shrink-0" />,
  score:  <Sparkles  className="w-3 h-3 text-violet-400 flex-shrink-0" />,
  system: <BookOpen  className="w-3 h-3 text-blue-400   flex-shrink-0" />,
  result: <Award     className="w-3 h-3 text-emerald-400 flex-shrink-0" />,
}
const COLOR = {
  judge:  'text-amber-200/90',
  score:  'text-violet-200/90',
  system: 'text-blue-200/80',
  result: 'text-emerald-200',
}

export default function BattleLog({ logs = [] }) {
  return (
    <div className="flex flex-col gap-2 h-full">
      <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest">
        评审日志
      </p>
      <div className="flex-1 overflow-y-auto space-y-1.5 pr-1">
        <AnimatePresence initial={false}>
          {logs.length === 0 ? (
            <p className="text-xs text-gray-700 text-center pt-6">等待对战开始…</p>
          ) : (
            logs.map((log, i) => (
              <motion.div
                key={log.id ?? i}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25 }}
                className="flex items-start gap-1.5 text-[11px] leading-relaxed"
              >
                <span className="mt-0.5">{ICON[log.type] ?? ICON.system}</span>
                <span className={COLOR[log.type] ?? 'text-gray-300'}>{log.message}</span>
              </motion.div>
            ))
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
