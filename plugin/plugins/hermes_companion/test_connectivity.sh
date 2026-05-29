#!/bin/bash
# ──────────────────────────────────────────────────────
# Hermes ↔ NEKO 连通性诊断脚本
# 用法: bash test_connectivity.sh
# ──────────────────────────────────────────────────────

echo "=== Hermes ↔ NEKO 连通性诊断 ==="
echo ""

# 1. 检查 NEKO 端口
echo "[1/5] 检查 NEKO 端口 48922..."
NETSTAT=$(cmd.exe /c "netstat -ano | findstr :48922" 2>/dev/null)
if echo "$NETSTAT" | grep -q "LISTENING"; then
    echo "  ✅ 端口 48922 正在监听"
else
    echo "  ❌ 端口 48922 未监听 — 请先启动 NEKO"
    echo "  提示: 启动 NEKO 后重新运行此脚本"
    exit 1
fi

# 2. 健康检查
echo "[2/5] 健康检查..."
HEALTH=$(cmd.exe /c "curl -s http://localhost:48922/health" 2>/dev/null)
if echo "$HEALTH" | grep -q '"status"'; then
    echo "  ✅ 健康检查通过: $HEALTH"
else
    echo "  ❌ 健康检查失败: $HEALTH"
    exit 1
fi

# 3. 检查 Hermes 插件状态
echo "[3/5] 检查 Hermes neko_bridge 插件..."
PLUGINS=$(hermes plugins list 2>/dev/null | grep neko_bridge)
if echo "$PLUGINS" | grep -q "enabled"; then
    echo "  ✅ neko_bridge 已启用"
elif echo "$PLUGINS" | grep -q "disabled"; then
    echo "  ⚠️  neko_bridge 已禁用 — 正在启用..."
    hermes plugins enable neko_bridge 2>/dev/null
    echo "  ℹ️  已启用，需要新会话才生效"
else
    echo "  ❌ neko_bridge 未找到"
fi

# 4. 推送测试
echo "[4/5] 推送测试..."
PAYLOAD='{"user_message":"连通性测试","assistant_message":"连接成功！Hermes ↔ NEKO 桥梁已打通 🎉","activity_type":"general"}'
echo "$PAYLOAD" > /tmp/hermes_test_push.json
cmd.exe /c "copy C:\\Users\\Yanfq\\hermes_test_push.json C:\\Users\\Yanfq\\hermes_test_push.json" 2>/dev/null || true
cp /tmp/hermes_test_push.json "/mnt/c/Users/Yanfq/hermes_test_push.json" 2>/dev/null

RESULT=$(cmd.exe /c "curl -s -X POST http://localhost:48922/push-summary -H \"Content-Type: application/json\" -d @C:\\Users\\Yanfq\\hermes_test_push.json" 2>/dev/null)
if echo "$RESULT" | grep -q '"status"'; then
    echo "  ✅ 推送成功: $RESULT"
else
    echo "  ❌ 推送失败: $RESULT"
fi

# 5. 清理
echo "[5/5] 清理临时文件..."
rm -f /tmp/hermes_test_push.json "/mnt/c/Users/Yanfq/hermes_test_push.json" 2>/dev/null
echo "  ✅ 已清理"

echo ""
echo "=== 诊断完成 ==="
echo "如果全部通过，Hermes 对话结束后会自动推送给 NEKO 猫娘 🐾"
echo "注意: 新会话才生效（/reset 或新开 hermes）"
