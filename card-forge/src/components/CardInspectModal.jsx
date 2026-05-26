import { useEffect } from 'react'
import { BookHeart, Sparkles, X } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'

const attrFallbackName = {
  passion: '热情',
  gentle: '温柔',
  cool: '高冷',
  natural: '天然',
}

function getAttrName(card) {
  return card?.attr?.name || card?.attrName || attrFallbackName[card?.attrId] || card?.attrId || '未知属性'
}

function getComboAttrName(card) {
  return card?.comboAttr?.name || card?.comboAttrName || attrFallbackName[card?.comboAttrId] || attrFallbackName[card?.attrId] || card?.comboAttrId || card?.attrId || '未知Combo'
}

function getCost(card) {
  return card?.cost ?? Math.max(1, Math.ceil((card?.power || 0) / 3))
}

export default function CardInspectModal({ card, open, onClose, source = 'card' }) {
  const AttrIcon = card?.attr?.icon || Sparkles
  const ComboIcon = card?.comboAttr?.icon || AttrIcon
  const story = card?.story || (card?.forged ? card?.summary : '预设卡牌暂无故事')
  const storyLead = card?.storyLead || card?.factText || card?.eventLead || ''

  useEffect(() => {
    if (!open) return undefined
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  return (
    <AnimatePresence>
      {open && card && (
        <motion.div
          key="card-inspect-modal"
          className="fixed inset-0 z-[260] flex items-center justify-center overflow-y-auto bg-black/45 px-4 py-5"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.article
            className="grid max-h-full w-full max-w-3xl grid-cols-1 overflow-hidden rounded-sm border-2 border-neutral-950 bg-white text-neutral-950 shadow-2xl md:grid-cols-[260px_minmax(0,1fr)]"
            initial={{ opacity: 0, y: 28, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 260, damping: 28 }}
            onClick={(event) => event.stopPropagation()}
          >
            <section className="relative min-h-[250px] overflow-hidden border-b-2 border-neutral-950 bg-neutral-100 p-5 md:min-h-[380px] md:border-b-0 md:border-r-2">
              <div className={`absolute inset-x-0 top-0 h-24 ${card?.attr?.bg || 'bg-neutral-200'}`} />
              <div className="relative z-10 flex items-start justify-between">
                <div className="flex h-14 w-14 items-center justify-center rounded-full border-4 border-orange-500 bg-neutral-950 text-2xl font-black text-white shadow-md">
                  {getCost(card)}
                </div>
                <div className="flex items-center gap-1 border-2 border-neutral-950 bg-white px-2 py-1 text-xs font-black">
                  <AttrIcon className={`h-4 w-4 ${card?.attr?.text || 'text-neutral-700'}`} />
                  {getAttrName(card)}
                </div>
              </div>

              <div className="relative z-10 mt-8 flex flex-col items-center md:mt-12">
                <div className="flex h-24 w-24 items-center justify-center rounded-full border-4 border-white bg-white shadow-lg">
                  <AttrIcon className={`h-14 w-14 ${card?.attr?.text || 'text-neutral-700'}`} />
                </div>
                <h3 className="mt-5 text-center text-xl font-black leading-tight">{card.name || '未知卡牌'}</h3>
                <p className="mt-2 text-center text-xs font-black text-neutral-500">{card.code || card.baseCode || source}</p>
                {card?.forged && (
                  <span className="mt-3 border-2 border-violet-500 bg-violet-50 px-2 py-1 text-[11px] font-black text-violet-700">
                    Forged
                  </span>
                )}
              </div>
            </section>

            <section className="flex min-h-0 flex-col overflow-hidden p-5 md:min-h-[380px]">
              <div className="flex items-start justify-between gap-4 border-b-2 border-neutral-950 pb-4">
                <div>
                  <p className="text-xs font-black uppercase text-neutral-500">卡牌鉴赏</p>
                  <h2 className="mt-1 text-2xl font-black">{card.name || '未知卡牌'}</h2>
                </div>
                <button
                  type="button"
                  onClick={onClose}
                  className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-neutral-950 bg-white hover:bg-neutral-100"
                  title="关闭"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
                <div className="border-2 border-orange-500 bg-orange-50 p-3">
                  <p className="text-[11px] font-black text-orange-700">行动力</p>
                  <p className="mt-1 text-2xl font-black">{getCost(card)}</p>
                </div>
                <div className="border-2 border-neutral-300 bg-neutral-50 p-3">
                  <p className="text-[11px] font-black text-neutral-500">主属性</p>
                  <p className="mt-1 text-sm font-black">{getAttrName(card)}</p>
                </div>
                <div className="border-2 border-amber-400 bg-amber-50 p-3">
                  <p className="text-[11px] font-black text-amber-700">Combo属性</p>
                  <p className="mt-1 flex items-center gap-1 text-sm font-black">
                    <ComboIcon className={`h-3.5 w-3.5 ${card?.comboAttr?.text || card?.attr?.text || 'text-neutral-700'}`} />
                    {getComboAttrName(card)}
                  </p>
                </div>
              </div>

              <div className="mt-4 min-h-0 space-y-3 overflow-y-auto pr-1">
                <div className="border-2 border-neutral-950 bg-white p-3">
                  <p className="text-xs font-black text-neutral-500">主效果</p>
                  <p className="mt-1 break-words text-sm font-bold leading-relaxed text-neutral-900">{card.mainText || '暂无主效果'}</p>
                </div>
                <div className="border-2 border-dashed border-neutral-400 bg-neutral-50 p-3">
                  <p className="text-xs font-black text-neutral-500">Combo效果</p>
                  <p className="mt-1 break-words text-sm font-bold leading-relaxed text-neutral-900">{card.comboText || '暂无Combo效果'}</p>
                </div>
                <div className={`min-h-24 border-2 p-3 ${card?.forged ? 'border-violet-500 bg-violet-50' : 'border-violet-200 bg-violet-50/60'}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="flex items-center gap-1 text-xs font-black text-violet-700">
                      <BookHeart className="h-3.5 w-3.5" />
                      {card?.forged ? '锻造故事' : '故事'}
                    </p>
                    {card?.forged && (
                      <span className="border border-violet-300 bg-white px-2 py-0.5 text-[10px] font-black text-violet-700">
                        Forged
                      </span>
                    )}
                  </div>
                  {card?.forged && (
                    <div className="mt-2 border border-violet-200 bg-white/80 px-2 py-1">
                      <p className="text-[11px] font-black text-violet-600">来源事件</p>
                      <p className="break-words text-xs font-bold text-neutral-800">{card?.sourceEventName || '临时事件记录'}</p>
                    </div>
                  )}
                  {card?.forged && storyLead && (
                    <div className="mt-2 border border-amber-200 bg-amber-50 px-2 py-1">
                      <p className="text-[11px] font-black text-amber-700">故事引子</p>
                      <p className="break-words text-xs font-bold leading-relaxed text-neutral-800">{storyLead}</p>
                    </div>
                  )}
                  <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words pr-1 text-sm font-bold leading-relaxed text-neutral-800">
                    {story}
                  </p>
                </div>
              </div>
            </section>
          </motion.article>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
