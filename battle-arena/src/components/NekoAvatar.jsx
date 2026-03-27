import { Cat } from 'lucide-react'

/**
 * 猫娘头像组件
 *
 * TODO: [头像接入点]
 * - 当 `avatar` prop 不为 null 时渲染真实图片
 * - 将来由头像提取功能生成 URL 后传入此处
 * - 占位时显示渐变背景 + 猫咪图标
 *
 * 接入示例：
 *   const url = await avatarExtractor.generate(neko.description)
 *   <NekoAvatar avatar={url} name={neko.name} side="left" />
 */
export default function NekoAvatar({ avatar, name, side = 'left' }) {
  const gradient = side === 'left'
    ? 'from-violet-600 to-indigo-500'
    : 'from-pink-600 to-rose-500'

  const glow = side === 'left'
    ? 'shadow-violet-500/40'
    : 'shadow-pink-500/40'

  return (
    <div className="relative group mx-auto">
      {/* 光晕 */}
      <div
        className={`absolute -inset-2 bg-gradient-to-r ${gradient} rounded-full opacity-30
                    group-hover:opacity-60 blur-xl transition-all duration-700`}
      />

      {/* 头像圆框 */}
      <div
        className={`relative w-36 h-36 lg:w-48 lg:h-48 rounded-full overflow-hidden
                    border border-white/20 shadow-2xl ${glow}`}
      >
        {/* TODO: [头像接入] avatar 有值时用 <img>，否则用占位符 */}
        {avatar ? (
          <img src={avatar} alt={`${name}的头像`} className="w-full h-full object-cover" />
        ) : (
          <div className={`w-full h-full bg-gradient-to-br ${gradient} flex items-center justify-center`}>
            {/* 占位符 — 将来替换为真实头像 */}
            <Cat className="w-16 h-16 lg:w-20 lg:h-20 text-white/70" strokeWidth={1.2} />
          </div>
        )}
      </div>

      {/* 在线指示 */}
      <span className="absolute bottom-0.5 right-0.5 w-4 h-4 bg-emerald-400 rounded-full border-2 border-neko-darker" />
    </div>
  )
}
