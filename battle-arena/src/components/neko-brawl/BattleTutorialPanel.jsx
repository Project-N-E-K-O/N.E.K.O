import { BookOpen, CheckCircle2, MousePointer2, Sparkles, Swords, X, Zap } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'

const tutorialItems = [
  {
    icon: MousePointer2,
    title: '点击卡牌查看详情',
    body: '未拖动时点击手牌、玩家出牌区、队友出牌区或结算高光卡，会打开统一卡牌鉴赏弹层。拖拽中不会误触发查看。',
  },
  {
    icon: Swords,
    title: '拖到Boss区视为准备出牌',
    body: '把手牌拖到中间Boss展示区松手，卡牌会飞入玩家出牌区。此时仍然只是预出牌，需要点击确认出牌才会结算。',
  },
  {
    icon: CheckCircle2,
    title: '确认出牌后才生效',
    body: '确认后会消耗卡面左上角的行动力，并根据卡牌效果造成伤害、治疗、护盾或控制。',
  },
  {
    icon: Sparkles,
    title: 'Combo属性',
    body: '顶部Boss状态栏会显示当前Combo属性。卡牌右上角是主属性，鉴赏和卡面信息里会显示Combo属性；若Combo属性命中当前属性，卡牌边框会按对应属性颜色高亮，并在结算时追加Combo效果。',
  },
  {
    icon: Sparkles,
    title: '为什么卡牌会高亮',
    body: '拖动中的牌或已经进入玩家出牌区的牌，如果Combo属性匹配当前Combo属性，就会持续高亮。拿起另一张牌后，高亮会切到新牌，用来帮助判断这次确认是否值得打出。',
  },
  {
    icon: CheckCircle2,
    title: '队友状态提示',
    body: '队友回合里，“还在思考中”表示队友还未选择卡牌；队友确认后，队友出牌区会出现对应卡牌动画，外框变成金色并显示“准备出击！”。',
  },
  {
    icon: Zap,
    title: '行动力与跳过',
    body: '行动力不足时无法确认出牌。没有可用卡牌时会自动跳过，也可以在玩家回合手动点击跳过。',
  },
  {
    icon: BookOpen,
    title: '日志与Combo列表',
    body: '左侧行动力下方可以打开战斗日志或Combo列表。Combo列表打开期间不会阻止拖拽和出牌，只能再次点击Combo列表按钮关闭。',
  },
]

export default function BattleTutorialPanel({ open, onClose }) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="battle-tutorial-panel"
          className="absolute inset-0 z-[245] bg-black/20"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.aside
            className="absolute bottom-0 right-0 top-0 flex w-[min(470px,92vw)] flex-col border-l-2 border-neutral-900 bg-white text-neutral-950 shadow-2xl"
            initial={{ x: '105%' }}
            animate={{ x: 0 }}
            exit={{ x: '105%' }}
            transition={{ type: 'spring', stiffness: 300, damping: 34 }}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="flex items-center justify-between border-b-2 border-neutral-900 px-5 py-4">
              <div>
                <div className="flex items-center gap-2">
                  <BookOpen className="h-5 w-5" />
                  <h2 className="text-lg font-black">战斗教程</h2>
                </div>
                <p className="mt-1 text-xs font-bold text-neutral-500">纯前端临时教程，不记录已读状态。</p>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="flex h-9 w-9 items-center justify-center border-2 border-neutral-900 bg-white hover:bg-neutral-100"
                title="关闭教程"
              >
                <X className="h-4 w-4" />
              </button>
            </header>

            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
              {tutorialItems.map(({ icon: Icon, title, body }, index) => (
                <section key={title} className="border-2 border-neutral-900 bg-white p-4 shadow-[3px_3px_0_#18181b]">
                  <div className="flex items-center gap-3">
                    <span className="flex h-8 w-8 items-center justify-center rounded-full border-2 border-orange-500 bg-orange-50 text-xs font-black text-orange-700">
                      {index + 1}
                    </span>
                    <Icon className="h-5 w-5 text-neutral-800" />
                    <h3 className="text-sm font-black">{title}</h3>
                  </div>
                  <p className="mt-3 text-sm font-bold leading-relaxed text-neutral-700">{body}</p>
                </section>
              ))}
            </div>
          </motion.aside>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
