#!/usr/bin/env bash
# plugin/plugins/plugin-vc.sh
# 插件版本控制快速提交脚本
# 用法：
#   ./plugin-vc.sh                    # 提交所有插件变更
#   ./plugin-vc.sh cosplay_plugin     # 只提交指定插件的变更
#   ./plugin-vc.sh cosplay "修复OCR问题"  # 带自定义提交信息

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

PLUGIN_NAME="${1:-}"
CUSTOM_MSG="${2:-}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# 检查是否在 git 仓库中
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    err "当前目录不是 Git 仓库"
    exit 1
fi

# 获取插件变更统计
get_plugin_changes() {
    local plugin_path="$1"
    local added modified deleted untracked
    added=$(git diff --cached --name-only -- "$plugin_path" 2>/dev/null | wc -l)
    modified=$(git diff --name-only -- "$plugin_path" 2>/dev/null | wc -l)
    untracked=$(git ls-files --others --exclude-standard -- "$plugin_path" 2>/dev/null | wc -l)
    echo "$added $modified $untracked"
}

# 显示插件状态
show_status() {
    info "=== 插件目录变更状态 ==="
    echo ""
    
    local has_changes=false
    for plugin_dir in plugin/plugins/*/; do
        local name
        name=$(basename "$plugin_dir")
        [[ "$name" == "__pycache__" || "$name" == "_shared" ]] && continue
        
        local stats
        stats=$(get_plugin_changes "$plugin_dir")
        local added modified untracked
        read -r added modified untracked <<< "$stats"
        
        if [[ "$modified" -gt 0 || "$untracked" -gt 0 ]]; then
            has_changes=true
            printf "  ${YELLOW}%-30s${NC}  修改: %d  未跟踪: %d\n" "$name" "$modified" "$untracked"
        fi
    done
    
    if ! $has_changes; then
        ok "所有插件目录无变更"
    fi
    echo ""
}

# 提交指定插件
commit_plugin() {
    local plugin_name="$1"
    local plugin_path="plugin/plugins/$plugin_name"
    
    if [[ ! -d "$plugin_path" ]]; then
        err "插件目录不存在: $plugin_path"
        exit 1
    fi
    
    # 暂存变更
    git add "$plugin_path/"
    
    # 检查是否有变更
    if git diff --cached --quiet -- "$plugin_path/"; then
        warn "插件 $plugin_name 没有变更需要提交"
        return 0
    fi
    
    # 生成提交信息
    local msg
    if [[ -n "$CUSTOM_MSG" ]]; then
        msg="$CUSTOM_MSG"
    else
        local change_count
        change_count=$(git diff --cached --name-only -- "$plugin_path/" | wc -l)
        msg="chore($plugin_name): 更新插件 ($change_count files changed)"
    fi
    
    git commit -m "$msg"
    ok "已提交: $plugin_name"
}

# 提交所有插件
commit_all() {
    local plugin_path="plugin/plugins/"
    
    git add "$plugin_path"
    
    if git diff --cached --quiet -- "$plugin_path"; then
        warn "所有插件目录没有变更需要提交"
        return 0
    fi
    
    local msg
    if [[ -n "$CUSTOM_MSG" ]]; then
        msg="$CUSTOM_MSG"
    else
        local change_count
        change_count=$(git diff --cached --name-only -- "$plugin_path" | wc -l)
        msg="chore(plugins): 批量更新插件 ($change_count files changed)"
    fi
    
    git commit -m "$msg"
    ok "已提交所有插件变更"
}

# 主逻辑
case "${1:-}" in
    -h|--help)
        echo "用法:"
        echo "  $0                    # 显示插件变更状态"
        echo "  $0 <plugin_name>      # 提交指定插件变更"
        echo "  $0 <plugin_name> \"msg\" # 带自定义提交信息"
        echo "  $0 --all              # 提交所有插件变更"
        echo "  $0 --all \"msg\"        # 带自定义提交信息"
        echo "  $0 --status           # 显示插件变更状态"
        ;;
    --status)
        show_status
        ;;
    --all)
        commit_all
        ;;
    "")
        show_status
        ;;
    *)
        commit_plugin "$PLUGIN_NAME"
        ;;
esac
