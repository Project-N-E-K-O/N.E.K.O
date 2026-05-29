#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Hermes → NEKO 推送脚本
# 从 Hermes SQLite 数据库提取最新对话，推送给 NEKO 猫娘
#
# 用法:
#   bash push_hermes.sh                    # 自动提取最新对话
#   bash push_hermes.sh "用户消息" "助手回复" [活动类型]  # 手动指定
#
# 端口: Hermes Companion 用 48922，OpenClaw 用 48921
# ──────────────────────────────────────────────────────────────

NEKO_PORT="${NEKO_PORT:-48922}"
NEKO_URL="http://localhost:${NEKO_PORT}/push-summary"
WIN_USER="${WIN_USER:-Yanfq}"
WIN_TMP="C:\\Users\\${WIN_USER}\\hermes_push.json"

# ── 模式 1: 手动指定消息 ──
if [ -n "$1" ] && [ -n "$2" ]; then
    USER_MSG="$1"
    ASSISTANT_MSG="$2"
    ACTIVITY_TYPE="${3:-general}"
else
    # ── 模式 2: 从 Hermes 数据库自动提取 ──
    DB_PATH="${HERMES_HOME:-$HOME/.hermes}/state.db"

    if [ ! -f "$DB_PATH" ]; then
        echo "❌ 未找到 Hermes 数据库: $DB_PATH"
        exit 1
    fi

    # 用 python3 提取最新对话（最可靠）
    EXTRACTED=$(python3 -c "
import sqlite3, json, sys

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# 获取最新 session
row = conn.execute('SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1').fetchone()
if not row:
    print(json.dumps({'error': 'no session'}))
    conn.close()
    sys.exit(0)

session_id = row['id']
rows = conn.execute(
    'SELECT role, content, tool_calls, tool_name FROM messages '
    'WHERE session_id = ? ORDER BY timestamp DESC, id DESC LIMIT 20',
    (session_id,)
).fetchall()

user_msg = ''
assistant_msg = ''
tools_used = []

found_assistant = False
for msg in rows:
    role = msg['role']
    content = msg['content'] or ''
    tc_raw = msg['tool_calls']

    if role == 'assistant' and not found_assistant:
        if tc_raw:
            try:
                tc_list = json.loads(tc_raw) if isinstance(tc_raw, str) else tc_raw
                if isinstance(tc_list, list):
                    for tc in tc_list:
                        fn = tc.get('function', {})
                        name = fn.get('name', '')
                        if name and name not in tools_used:
                            tools_used.append(name)
            except: pass
        if content and content.strip():
            found_assistant = True
            assistant_msg = content.strip()

    if role == 'user' and found_assistant and not user_msg:
        if content and content.strip():
            user_msg = content.strip()

conn.close()

# 检测活动类型
combined = (user_msg + ' ' + assistant_msg).lower()
tools_str = ' '.join(tools_used).lower()
activity = 'general'
keywords = {
    'edit': ['edit', '修改', 'write_file', 'patch'],
    'debug': ['debug', 'fix', 'bug', '调试', '修复'],
    'test': ['test', 'pytest', '测试'],
    'git': ['git', 'commit', 'push', 'pull'],
    'search': ['search', 'find', '搜索', '查找'],
    'build': ['build', 'npm', 'pip', 'cargo', 'install'],
    'skill': ['skill', '技能'],
}
for atype, kws in keywords.items():
    if any(kw in combined or kw in tools_str for kw in kws):
        activity = atype
        break

print(json.dumps({
    'user_message': user_msg[:500],
    'assistant_message': assistant_msg[:500],
    'activity_type': activity,
    'tools_used': tools_used[:10],
}, ensure_ascii=False))
" "$DB_PATH" 2>/dev/null)

    if echo "$EXTRACTED" | grep -q '"error"'; then
        echo "❌ 未找到对话数据"
        exit 1
    fi

    USER_MSG=$(echo "$EXTRACTED" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('user_message',''))" 2>/dev/null)
    ASSISTANT_MSG=$(echo "$EXTRACTED" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('assistant_message',''))" 2>/dev/null)
    ACTIVITY_TYPE=$(echo "$EXTRACTED" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('activity_type','general'))" 2>/dev/null)

    if [ -z "$USER_MSG" ] && [ -z "$ASSISTANT_MSG" ]; then
        echo "⚠️ 最新对话为空，跳过推送"
        exit 0
    fi
fi

# ── 构建 JSON ──

# 用 python3 做 JSON 转义（最可靠）
USER_MSG_ESCAPED=$(python3 -c "import sys,json; print(json.dumps(sys.argv[1]))" "$USER_MSG" 2>/dev/null || echo "\"$USER_MSG\"")
ASSISTANT_MSG_ESCAPED=$(python3 -c "import sys,json; print(json.dumps(sys.argv[1]))" "$ASSISTANT_MSG" 2>/dev/null || echo "\"$ASSISTANT_MSG\"")

# 写临时 JSON
TMP_JSON="/tmp/hermes_push_$$.json"
cat > "$TMP_JSON" << EOF
{"user_message":${USER_MSG_ESCAPED},"assistant_message":${ASSISTANT_MSG_ESCAPED},"activity_type":"${ACTIVITY_TYPE}"}
EOF

# ── 推送到 NEKO ──

# 复制到 Windows 可访问路径
WIN_TMP_UNIX="/mnt/c/Users/${WIN_USER}/hermes_push.json"
cp "$TMP_JSON" "$WIN_TMP_UNIX"

# 通过 cmd.exe 推送
RESULT=$(/mnt/c/Windows/System32/cmd.exe /c "curl -s -X POST ${NEKO_URL} -H \"Content-Type: application/json\" -d @${WIN_TMP}" 2>/dev/null)

# 清理
rm -f "$TMP_JSON" "$WIN_TMP_UNIX"

# 检查结果
if echo "$RESULT" | grep -q '"status"'; then
    echo "✅ 推送成功 → NEKO (port ${NEKO_PORT})"
    echo "   用户: ${USER_MSG:0:60}..."
    echo "   助手: ${ASSISTANT_MSG:0:60}..."
    echo "   类型: ${ACTIVITY_TYPE}"
else
    echo "❌ 推送失败: $RESULT"
    echo "   请确认 NEKO 的 hermes_companion 插件已启动（端口 ${NEKO_PORT}）"
    exit 1
fi
