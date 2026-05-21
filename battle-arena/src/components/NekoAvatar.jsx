/**
 * 猫娘头像组件
 *
 * TODO: [头像接入点]
 * - 当 `avatar` prop 不为 null 时渲染真实图片
 * - 将来由头像提取功能生成 URL 后传入此处
 * - 占位时显示 waiting.gif 测试动图
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
        className={`absolute -inset-2 bg-gradient-to-r ${gradient} rounded-[2rem] opacity-30
                    group-hover:opacity-60 blur-xl transition-all duration-700`}
      />

      {/* 头像圆框 */}
      <div
        className={`relative w-36 h-36 lg:w-48 lg:h-48 rounded-[1.75rem] overflow-hidden
                    border border-white/20 shadow-2xl ${glow}`}
      >
        {/* TODO: [头像接入] avatar 有值时用真实头像，否则用 waiting.gif 占位 */}
        {avatar ? (
          <img src={avatar} alt={`${name}的头像`} className="w-full h-full object-cover" />
        ) : (
          <img src="/waiting.gif" alt="等待中头像" className="w-full h-full object-cover" />
        )}
      </div>

      {/* 在线指示 */}
      <span className="absolute bottom-0.5 right-0.5 w-4 h-4 bg-emerald-400 rounded-full border-2 border-neko-darker" />
    </div>
  )
}
