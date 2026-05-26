import { BookOpen, X } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'

const tutorialSections = [
  ['卡池区', '中间卡池区列出当前可用于组卡的预设卡和奇遇锻造卡。点击卡牌主体会把它加入已选卡组；想先阅读卡牌时，使用卡牌上的“查看”按钮打开鉴赏弹层。'],
  ['筛选区', '搜索框、行动力、类型、属性和喜爱筛选都会先缩小卡池范围，再决定当前显示哪些卡。筛选只影响浏览，不会删除已经放入卡组的卡。'],
  ['已选卡组区', '已选卡组区展示准备带进对局的卡。这里用于核对数量、同名张数和行动力结构，移除卡牌后卡池仍可重新选择。'],
  ['卡组数量', '卡组需要正好 18 张卡。同名卡最多 3 张，左侧会显示当前数量和平均行动力。'],
  ['行动力曲线', '行动力决定对局里确认出牌时消耗的资源。行动力曲线按档位统计卡组分布，用于快速观察卡组是否过重、过轻，或缺少前期能打出的牌。'],
  ['属性分布', '左侧属性分布显示当前卡组里的热情、温柔、高冷、天然数量，也可以点击属性按钮筛选卡池。'],
  ['主效果与Combo效果', '主效果是打出卡牌一定生效的内容。Combo效果只有当前Combo属性匹配时才会额外生效。'],
  ['卡牌属性与Combo属性', '卡牌右上角是主属性，影响Boss弱点/抗性。Combo属性单独显示，用来判断本回合能否触发Combo。'],
  ['喜爱卡牌', '卡牌左下角的空心心形是喜爱标记。点击后变成实心心形，再点击可以取消。顶部“喜爱”按钮可以只显示喜爱卡。'],
  ['Forged卡', '奇遇制造机产出的卡会带有(Forged)后缀。它们的主效果、行动力、名字、主属性跟随基础卡，Combo属性会随机，故事当前使用临时事件文本。'],
  ['保存与进入对局', '调整好 18 张卡后点击保存，卡组会记录在本地。点击进入对局后，战斗抽牌会读取当前保存的卡组。'],
]

export default function DeckBuilderTutorialPanel({ open, onClose }) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="deck-builder-tutorial"
          className="fixed inset-0 z-[250] bg-black/20"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.aside
            className="absolute bottom-0 right-0 top-0 flex w-[min(460px,92vw)] flex-col border-l-2 border-neutral-950 bg-white text-neutral-950 shadow-2xl"
            initial={{ x: '105%' }}
            animate={{ x: 0 }}
            exit={{ x: '105%' }}
            transition={{ type: 'spring', stiffness: 300, damping: 32 }}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="flex items-center justify-between border-b-2 border-neutral-950 px-5 py-4">
              <div className="flex items-center gap-2">
                <BookOpen className="h-5 w-5" />
                <h2 className="text-lg font-black">组卡教程</h2>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="flex h-9 w-9 items-center justify-center border-2 border-neutral-950 bg-white hover:bg-neutral-100"
                title="关闭教程"
              >
                <X className="h-4 w-4" />
              </button>
            </header>

            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
              {tutorialSections.map(([title, body], index) => (
                <section key={title} className="border-2 border-neutral-950 bg-white p-4">
                  <div className="flex items-center gap-2">
                    <span className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-orange-500 text-xs font-black text-orange-700">
                      {index + 1}
                    </span>
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
