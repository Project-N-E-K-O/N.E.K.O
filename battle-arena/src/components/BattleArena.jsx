import { useState, useCallback, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, Crown, Zap, RotateCcw, ChevronRight, Medal, Star, Trophy, BarChart3 } from 'lucide-react'
import NekoCard from './NekoCard'
import BattleLog from './BattleLog'
import BottomTicker from './BottomTicker'

// ─────────────────────────────────────────────────────────────────────────────
// 占位数据  ——  将来由后端 API / 全局状态管理替换
// ─────────────────────────────────────────────────────────────────────────────
const NEKO_LEFT = {
  id: 'neko-left',
  name: '猫娘 A',
  title: '待匹配',
  level: 0,
  rank: '?',
  owner: '玩家A',
  avatar: null, // TODO: [头像接入] 由头像提取功能提供 URL
  wins: 0,
  totalBattles: 0,
  winRate: 0,
}

const NEKO_RIGHT = {
  id: 'neko-right',
  name: '猫娘 B',
  title: '待匹配',
  level: 0,
  rank: '?',
  owner: '玩家B',
  avatar: null, // TODO: [头像接入] 由头像提取功能提供 URL
  wins: 0,
  totalBattles: 0,
  winRate: 0,
}

// 排行榜占位
const RANKING = Array.from({ length: 5 }, (_, i) => ({
  rank: i + 1,
  name: '???',
  owner: '---',
  score: 0,
}))

const MAX_DAILY = 10

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms))
}

// ─────────────────────────────────────────────────────────────────────────────

export default function BattleArena() {
  const [scoreLeft,  setScoreLeft]  = useState(0)
  const [scoreRight, setScoreRight] = useState(0)
  const [logs,       setLogs]       = useState([])
  const [remaining,  setRemaining]  = useState(MAX_DAILY)
  const [battling,   setBattling]   = useState(false)
  const [phase,      setPhase]      = useState('idle')  // idle | judging | result
  const [activeSide, setActiveSide] = useState(null)    // 'left' | 'right' | null
  const [result,     setResult]     = useState(null)    // { winner, left, right }
  const logEndRef = useRef(null)

  const addLog = useCallback((type, message) => {
    setLogs(prev => [...prev, { id: Date.now() + Math.random(), type, message }])
  }, [])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  // ── 模拟战斗流程 ────────────────────────────────────────────────────────────
  // TODO: 将来接入真实羁绊数据 + LLM 评委 API
  // 当前为 UI 动画演示，不含羁绊内容
  // ─────────────────────────────────────────────────────────────────────────────
  const handleBattle = async () => {
    if (battling || remaining <= 0) return
    setBattling(true)
    setResult(null)
    setLogs([])
    setScoreLeft(0)
    setScoreRight(0)

    setPhase('judging')
    addLog('system', '评鉴仪式开始，评委席就位…')
    await sleep(900)

    // 左侧评审
    setActiveSide('left')
    addLog('judge', `正在品读 ${NEKO_LEFT.name} 的羁绊故事…`)
    await sleep(1100)
    const sl = Math.round(Math.random() * 60 + 120)   // 120-180
    setScoreLeft(sl)
    addLog('score', `${NEKO_LEFT.name} 获得评委评分：${sl} 分`)
    await sleep(700)

    // 右侧评审
    setActiveSide('right')
    addLog('judge', `正在品读 ${NEKO_RIGHT.name} 的羁绊故事…`)
    await sleep(1100)
    const sr = Math.round(Math.random() * 60 + 120)
    setScoreRight(sr)
    addLog('score', `${NEKO_RIGHT.name} 获得评委评分：${sr} 分`)
    await sleep(700)

    // 结果
    setPhase('result')
    addLog('system', `总分揭晓 — ${NEKO_LEFT.name} ${sl} 分  vs  ${NEKO_RIGHT.name} ${sr} 分`)
    await sleep(500)

    let winner
    if (sl > sr)       winner = 'left'
    else if (sr > sl)  winner = 'right'
    else               winner = 'draw'

    setResult({ winner, left: sl, right: sr })
    addLog('result',
      winner === 'draw'
        ? '平局！双方羁绊同样令人动容。'
        : `${winner === 'left' ? NEKO_LEFT.name : NEKO_RIGHT.name} 的羁绊更打动评委！`
    )

    setRemaining(p => p - 1)
    setActiveSide(null)
    setBattling(false)
    setPhase('idle')
  }

  const handleReset = () => {
    setScoreLeft(0)
    setScoreRight(0)
    setLogs([])
    setResult(null)
    setActiveSide(null)
    setBattling(false)
    setPhase('idle')
  }

  // ── 排行榜图标 ───────────────────────────────────────────────────────────────
  const [showRanking, setShowRanking] = useState(false)
  const [rankingPos, setRankingPos] = useState({ top: 0, right: 0 })
  const rankingBtnRef = useRef(null)

  const updateRankingPos = useCallback(() => {
    if (rankingBtnRef.current) {
      const rect = rankingBtnRef.current.getBoundingClientRect()
      setRankingPos({
        top: rect.bottom + 8,
        right: window.innerWidth - rect.right
      })
    }
  }, [])

  useEffect(() => {
    if (showRanking) {
      updateRankingPos()
      window.addEventListener('resize', updateRankingPos)
      return () => window.removeEventListener('resize', updateRankingPos)
    }
  }, [showRanking, updateRankingPos])

  const rankIcon = (r) => {
    if (r === 1) return <Crown className="w-3.5 h-3.5 text-amber-400" />
    if (r <= 3)  return <Medal className="w-3.5 h-3.5 text-gray-400" />
    return <span className="text-[11px] text-gray-600 w-3.5 text-center">{r}</span>
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // 渲染
  // ─────────────────────────────────────────────────────────────────────────────
  return (
    <div className="h-screen flex flex-col overflow-hidden select-none">

      {/* ══════════════════════════════════════════════════════════════
          全屏背景图 + 暗化遮罩
      ══════════════════════════════════════════════════════════════ */}
      {/* 背景图 — 从 public/ 目录直接引用 */}
      <div
        className="fixed inset-0 bg-cover bg-center bg-no-repeat -z-20"
        style={{ backgroundImage: 'url(/lightblue-14.jpg)' }}
      />
      {/* 暗化遮罩 — 保证文字可读性 */}
      <div className="fixed inset-0 bg-black/55 -z-10" />
      {/* 保留微光装饰层（更淡） */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden -z-10">
        <div className="absolute top-0    left-1/4  w-80 h-80 bg-violet-600/5 rounded-full blur-3xl" />
        <div className="absolute bottom-0 right-1/4 w-80 h-80 bg-pink-600/5   rounded-full blur-3xl" />
      </div>

      {/* ══════════════════════════════════════════════════════════════
          顶部标题栏
      ══════════════════════════════════════════════════════════════ */}
      <header className="relative z-20 flex-shrink-0 px-6 pt-5 pb-3 flex items-center justify-between">
        <motion.div
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2"
        >
          <Sparkles className="w-5 h-5 text-violet-400" />
          <h1 className="text-xl lg:text-2xl font-black gradient-text tracking-wide">
            猫娘大乱斗
          </h1>
          <Sparkles className="w-5 h-5 text-pink-400" />
        </motion.div>

        {/* 右侧功能区：排名按钮 + 次数 */}
        <div className="flex items-center gap-3">
          {/* 排名按钮 + 悬停面板 */}
          <div 
            ref={rankingBtnRef}
            className="relative"
            onMouseEnter={() => { updateRankingPos(); setShowRanking(true); }}
            onMouseLeave={() => setShowRanking(false)}
          >
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/10 border border-white/20 text-xs text-gray-300 hover:text-white hover:bg-white/15 transition-all"
            >
              <Trophy className="w-3.5 h-3.5 text-amber-400" />
              <span>全球排名</span>
              <BarChart3 className="w-3 h-3 text-gray-500" />
            </motion.button>

            {/* 悬停展开的面板 — fixed 定位真正置顶 */}
            <AnimatePresence>
              {showRanking && (
                <motion.div
                  initial={{ opacity: 0, y: 8, scale: 0.95 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 8, scale: 0.95 }}
                  transition={{ duration: 0.2 }}
                  className="fixed w-64 glass-card p-3 shadow-2xl"
                  style={{ top: rankingPos.top, right: rankingPos.right }}
                >
                  <div className="flex items-center justify-between mb-3 pb-2 border-b border-white/10">
                    <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">全球排名</span>
                    <span className="text-[10px] text-amber-400/80">实时更新</span>
                  </div>
                  <div className="space-y-1.5 max-h-48 overflow-y-auto">
                    {RANKING.map((r, i) => (
                      <motion.div
                        key={r.rank}
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: i * 0.05 }}
                        className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-white/[0.03] hover:bg-white/[0.08] transition-colors"
                      >
                        {rankIcon(r.rank)}
                        <span className="flex-1 text-[11px] text-gray-400 truncate">{r.name}</span>
                        <span className="text-[10px] text-amber-400/50 font-medium">{r.score || '—'}</span>
                      </motion.div>
                    ))}
                  </div>
                  <div className="mt-3 pt-2 border-t border-white/10 text-center">
                    <button className="text-[10px] text-violet-400 hover:text-violet-300 flex items-center justify-center gap-0.5 w-full">
                      查看完整榜单 <ChevronRight className="w-3 h-3" />
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          <div className="flex items-center gap-4 text-xs text-gray-500">
            <span className="flex items-center gap-1">
              <Zap className="w-3.5 h-3.5 text-amber-400" />
              今日剩余
              <span className="text-amber-400 font-bold ml-1">{remaining}</span>
              <span>/ {MAX_DAILY}</span>
            </span>
          </div>
        </div>
      </header>

      {/* ══════════════════════════════════════════════════════════════
          主体三栏 — flex-1 填满剩余高度
      ══════════════════════════════════════════════════════════════ */}
      <main className="relative z-10 flex-1 flex gap-0 px-4 pb-2 min-h-0">

        {/* ── 左侧猫娘卡 ───────────────────────────────────── */}
        <motion.div
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          className={`
            flex-1 glass-card p-4 lg:p-5 flex flex-col min-w-0 overflow-y-auto
            transition-all duration-500 mr-2
            ${activeSide === 'left' ? 'ring-1 ring-violet-500/40' : ''}
          `}
        >
          <NekoCard neko={NEKO_LEFT} side="left" isActive={activeSide === 'left'} score={scoreLeft} />
        </motion.div>

        {/* ── 中间梯形战斗区 ────────────────────────────────── */}
        <div className="relative flex-shrink-0 w-[320px] lg:w-[380px] flex flex-col">

          {/*
            梯形背景 — clip-path 上窄下宽 (上宽10%收进, 下保持全宽)
            opacity 很低，保持"不太明显"的效果
          */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              clipPath: 'polygon(8% 0%, 92% 0%, 100% 100%, 0% 100%)',
              background: 'linear-gradient(to bottom, rgba(109,40,217,0.07), rgba(168,85,247,0.04))',
            }}
          />
          {/* 梯形边框描边（更淡） */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              clipPath: 'polygon(8% 0%, 92% 0%, 100% 100%, 0% 100%)',
              background: 'transparent',
              boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.04)',
            }}
          />

          {/* 内容区 */}
          <div className="relative z-10 flex flex-col h-full gap-4 px-3 py-3">

            {/* VS 标志 */}
            <div className="flex items-center justify-center pt-1">
              <motion.div
                animate={battling ? { scale: [1, 1.15, 1], rotate: [0, 4, -4, 0] } : {}}
                transition={{ duration: 0.7, repeat: battling ? Infinity : 0 }}
                className="relative"
              >
                <div className="w-14 h-14 rounded-full bg-gradient-to-br from-violet-600 to-pink-600
                                flex items-center justify-center shadow-lg shadow-violet-900/50">
                  <span className="text-white font-black text-lg tracking-tight">VS</span>
                </div>
                {battling && (
                  <div className="absolute inset-0 rounded-full bg-violet-500/30 animate-ping" />
                )}
              </motion.div>
            </div>

            {/* 评分对比 */}
            <div className="glass-card px-3 py-2 flex items-center justify-between gap-2">
              <span className={`text-2xl font-black transition-all duration-700 ${
                result?.winner === 'left' ? 'text-amber-400' : 'text-gray-300'
              }`}>{scoreLeft}</span>
              <span className="text-[10px] text-gray-600 uppercase tracking-widest">分数</span>
              <span className={`text-2xl font-black transition-all duration-700 ${
                result?.winner === 'right' ? 'text-amber-400' : 'text-gray-300'
              }`}>{scoreRight}</span>
            </div>

            {/* 战斗结果 */}
            <AnimatePresence>
              {result && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.85 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.85 }}
                  className={`glass-card px-3 py-2 text-center border ${
                    result.winner === 'draw'
                      ? 'border-blue-500/20'
                      : 'border-amber-500/20'
                  }`}
                >
                  {result.winner !== 'draw' && <Crown className="w-4 h-4 text-amber-400 mx-auto mb-0.5" />}
                  <p className="text-xs font-bold gradient-text">
                    {result.winner === 'left'  && `${NEKO_LEFT.name} 获胜`}
                    {result.winner === 'right' && `${NEKO_RIGHT.name} 获胜`}
                    {result.winner === 'draw'  && '平局'}
                  </p>
                </motion.div>
              )}
            </AnimatePresence>

            {/* 战斗按钮 */}
            <div className="flex gap-2">
              <motion.button
                whileHover={!battling && remaining > 0 ? { scale: 1.03 } : {}}
                whileTap={!battling  && remaining > 0 ? { scale: 0.97 } : {}}
                onClick={handleBattle}
                disabled={battling || remaining <= 0}
                className={`flex-1 py-2.5 rounded-xl text-sm font-bold flex items-center
                            justify-center gap-2 transition-all duration-300 ${
                  !battling && remaining > 0
                    ? 'bg-gradient-to-r from-violet-600 to-pink-600 text-white shadow-lg shadow-violet-900/40 hover:shadow-violet-900/60'
                    : 'bg-white/5 text-gray-600 cursor-not-allowed'
                }`}
              >
                {battling ? (
                  <motion.span
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1.2, repeat: Infinity, ease: 'linear' }}
                  >
                    <Star className="w-4 h-4" />
                  </motion.span>
                ) : (
                  <Sparkles className="w-4 h-4" />
                )}
                {battling
                  ? phase === 'judging' ? '评审中…' : '处理中…'
                  : '开始评鉴'}
              </motion.button>

              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                onClick={handleReset}
                className="px-3 py-2.5 rounded-xl bg-white/5 border border-white/[0.07]
                           text-gray-500 hover:text-gray-300 transition-colors"
              >
                <RotateCcw className="w-4 h-4" />
              </motion.button>
            </div>

            {remaining <= 0 && (
              <p className="text-[11px] text-rose-400/80 text-center">今日次数已用完</p>
            )}

            {/* 评审日志 — 扩张填满剩余空间 */}
            <div className="flex-1 glass-card p-3 min-h-0 overflow-hidden flex flex-col">
              <BattleLog logs={logs} />
              <div ref={logEndRef} />
            </div>

            {/* 评委席装饰 */}
            <div className="glass-card p-3 flex-shrink-0">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest">
                  评委席状态
                </span>
                <span className={`text-[11px] ${battling ? 'text-emerald-400 animate-pulse' : 'text-gray-600'}`}>
                  {battling ? '评审中...' : '待机'}
                </span>
              </div>
              <div className="flex gap-1.5">
                {['GPT-4', 'Claude', 'Gemini', 'Kimi'].map((judge, i) => (
                  <motion.div
                    key={judge}
                    animate={battling ? {
                      scale: [1, 1.1, 1],
                      opacity: [0.5, 1, 0.5]
                    } : {}}
                    transition={{ duration: 1.5, delay: i * 0.2, repeat: battling ? Infinity : 0 }}
                    className={`flex-1 py-1.5 rounded-lg text-[10px] text-center font-medium
                      ${battling ? 'bg-violet-500/20 text-violet-300' : 'bg-white/[0.03] text-gray-600'}`}
                  >
                    {judge}
                  </motion.div>
                ))}
              </div>
            </div>

            {/* 羁绊共鸣度 */}
            <div className="glass-card p-3 flex-shrink-0">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest">
                  羁绊共鸣度
                </span>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-600 w-8">情感</span>
                  <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <motion.div
                      className="h-full bg-gradient-to-r from-violet-500 to-pink-500 rounded-full"
                      initial={{ width: '30%' }}
                      animate={{ width: battling ? '70%' : '30%' }}
                      transition={{ duration: 2 }}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-600 w-8">回忆</span>
                  <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <motion.div
                      className="h-full bg-gradient-to-r from-pink-500 to-rose-500 rounded-full"
                      initial={{ width: '45%' }}
                      animate={{ width: battling ? '85%' : '45%' }}
                      transition={{ duration: 2, delay: 0.3 }}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-600 w-8">默契</span>
                  <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <motion.div
                      className="h-full bg-gradient-to-r from-amber-500 to-orange-500 rounded-full"
                      initial={{ width: '25%' }}
                      animate={{ width: battling ? '60%' : '25%' }}
                      transition={{ duration: 2, delay: 0.6 }}
                    />
                  </div>
                </div>
              </div>
            </div>

          </div>
        </div>

        {/* ── 右侧猫娘卡 ───────────────────────────────────── */}
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          className={`
            flex-1 glass-card p-4 lg:p-5 flex flex-col min-w-0 overflow-y-auto
            transition-all duration-500 ml-2
            ${activeSide === 'right' ? 'ring-1 ring-pink-500/40' : ''}
          `}
        >
          <NekoCard neko={NEKO_RIGHT} side="right" isActive={activeSide === 'right'} score={scoreRight} />
        </motion.div>

      </main>

      {/* ══════════════════════════════════════════════════════════════
          底部滚动信息条
      ══════════════════════════════════════════════════════════════ */}
      <BottomTicker />

    </div>
  )
}
