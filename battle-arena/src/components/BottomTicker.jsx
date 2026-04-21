/**
 * 底部滚动信息条 — meme/玩梗对话版
 *
 * 模拟猫娘之间的日常吐槽和玩梗对话
 */
const MEME_ITEMS = [
  '【号外】某猫娘因在午睡时梦见激光笔，触发瞬移异能导致天花板报废。',
  '【独家】猫娘协会秘密研讨：关于“为什么人类要在盒子里装猫，而不是把自己装进盒子”的哲学思考。',
  '【科研】最新统计显示，99%的猫娘认为吸尘器是外星文明派来的收割机。',
  '【头条】猫娘密谋取代人类计划进入第二阶段：先从学会拒绝“握手”指令开始。',
  '【预测】据测算，猫娘完全统治地球的时间预计为公元9999年，目前主要障碍是罐头拉环还没进化出来。',
  '【公告】禁止在服务器机房附近练习“踩奶”，已有三台物理机因过度舒适导致散热故障。',
  '【趣闻】某猫娘试图通过盯着水杯看将其“意念移位”，结果水杯由于害怕自行滑落。',
  '【气象】今日Nekoverse全境大范围降下“逗猫棒雨”，请各位注意不要在街上疯狂转圈。',
  '【吐槽】“人类为什么要工作？他们明明只要对着屏幕发呆就能变出纸币。”——某路人猫娘。',
  '【警示】严禁猫娘在深夜潜入厨房研究“火点燃的原理”，上次那根焦掉的胡须还没长出来。',
  '【百科】猫娘的尾巴其实是独立生物，据传它们有自己的深夜电台。',
  '【时尚】本周流行：半折耳式睡姿。据称能有效接收来自喵星的微弱信号。',
  '【调查】关于“铲屎官”这个职业的晋升机制，目前猫娘界普遍认为只要会开罐头就是高级职称。',
  '【惊悚】传闻有一只猫娘因为忍住了不去抓滚动的毛线球，被怀疑已经进化成了究极生命体。',
  '【深夜】别看了，人类。你现在的眼神比我寻找掉进沙发缝里的零食时还要呆滞。'
]

export default function BottomTicker({ items = MEME_ITEMS }) {
  // 复制两份拼接，形成无缝滚动
  const doubled = [...items, ...items]

  return (
    <div className="w-full h-10 bg-white/20 border-t border-white/30 flex items-center overflow-hidden relative">
      {/* 滚动内容 — 去掉左右渐变遮罩 */}
      <div className="flex items-center animate-ticker whitespace-nowrap will-change-transform pl-4">
        {doubled.map((item, i) => (
          <span key={i} className="inline-flex items-center">
            <span className="text-xs text-gray-400 px-3">{item}</span>
            <span className="text-violet-400/60 text-xs">✦</span>
          </span>
        ))}
      </div>
    </div>
  )
}
