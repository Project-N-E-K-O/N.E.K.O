#!/bin/sh
# =============================================================================
# docker/backup.sh 的隔离回归测试
#
#   sh docker/test-backup.sh
#
# 只碰临时目录：不读真实数据、不停任何容器、不调 docker。
# 覆盖：主副本清单、vendor 排除、校验和、轮转、以及四类拒绝条件。
# =============================================================================
set -eu

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd -P)
BACKUP="$SCRIPT_DIR/backup.sh"
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT

# 实例锁目录固定在临时目录里：脚本默认会用 /run/neko-backup（root）或
# $XDG_RUNTIME_DIR，测试不该往这些地方写东西。
NEKO_BACKUP_LOCK_DIR="$WORK/locks"
mkdir -p "$NEKO_BACKUP_LOCK_DIR"
export NEKO_BACKUP_LOCK_DIR

FAILED=0
pass() { printf '  ✅ %s\n' "$1"; }
fail() { printf '  ❌ %s\n' "$1" >&2; FAILED=1; }

# 造一个"看起来像 neko-home"的树
make_home() {
    home="$1"
    state="$home/.local/share/N.E.K.O"
    # 目录名必须与 storage_roots.py 一致（character_cards 不是 characters，
    # .cloudsave_staging 带点），否则测试会"验证"一个不存在的布局。
    mkdir -p "$state/config" "$state/memory" "$state/knowledge" "$state/plugins" \
             "$state/live2d" "$state/vrm" "$state/mmd" "$state/pngtuber" \
             "$state/avatar_tools" "$state/workshop" "$state/character_cards" \
             "$state/card_faces" "$state/state" "$state/cloudsave" \
             "$state/.cloudsave_staging" "$state/cloudsave_backups" \
             "$state/.neko-plugin-installations/demo/vendor" "$home/ssl"
    printf 'core\n'   > "$state/config/core_config.json"
    printf 'cat\n'    > "$state/character_cards/cat.json"
    printf 'model\n'  > "$state/live2d/model.moc3"
    printf 'pack\n'   > "$state/workshop/pack.json"
    printf 'recent\n' > "$state/memory/recent.json"
    printf 'facts\n'  > "$state/memory/facts.json"
    printf 'secret\n' > "$state/instance_access.key"
    printf 'cert\n'   > "$home/ssl/N.E.K.O.crt"
    # 可重建的东西：必须被排除
    printf 'big\n'    > "$state/.neko-plugin-installations/demo/vendor/lib.py"
    mkdir -p "$state/memory/__pycache__"
    printf 'noise\n'  > "$state/memory/__pycache__/x.pyc"
    # 不该被打包的东西（不在清单里）
    printf 'other\n'  > "$home/unrelated.txt"
}

echo "== T1 --dry-run 不写文件 =="
make_home "$WORK/t1home"
if sh "$BACKUP" --home "$WORK/t1home" --out "$WORK/t1out" --dry-run >/dev/null 2>&1; then
    if [ -e "$WORK/t1out" ] && [ -n "$(ls -A "$WORK/t1out" 2>/dev/null)" ]; then
        fail "--dry-run 却写了文件"
    else
        pass "--dry-run 未写入任何文件"
    fi
else
    fail "--dry-run 执行失败"
fi

echo
# 测试机没有 docker（CI 上也没有名为 neko 的容器），冷备门禁会拒绝执行 ——
# 这正是 T9 要覆盖的行为。成功路径必须显式接受风险：--no-stop。
echo "== T2 正常备份：主副本在内、可重建的在外 =="
make_home "$WORK/t2home"
if sh "$BACKUP" --home "$WORK/t2home" --out "$WORK/t2out" --no-stop >"$WORK/t2.log" 2>&1; then
    archive=$(ls "$WORK/t2out"/neko-backup-*.tar.gz 2>/dev/null | head -1)
    if [ -z "$archive" ]; then
        fail "没有生成归档"
    else
        entries=$(tar -tzf "$archive")
        # 覆盖每一类用户资产：配置、角色卡、记忆、模型、工坊、密钥、证书。
        # 少一类就是"恢复了但东西没了"，而这正是评审抓到的原始问题。
        for want in .local/share/N.E.K.O/config/core_config.json \
                    .local/share/N.E.K.O/character_cards/cat.json \
                    .local/share/N.E.K.O/memory/facts.json \
                    .local/share/N.E.K.O/live2d/model.moc3 \
                    .local/share/N.E.K.O/workshop/pack.json \
                    .local/share/N.E.K.O/instance_access.key \
                    ssl/N.E.K.O.crt; do
            printf '%s\n' "$entries" | grep -qF "$want" || fail "主副本缺失: $want"
        done
        # 插件 vendor/ 是插件的 Python 依赖，容器启动时不会重建：默认必须备份，
        # 否则恢复出来的插件会报依赖缺失并被跳过。
        printf '%s\n' "$entries" | grep -qF 'vendor' \
            || fail "插件 vendor/ 没有被备份（恢复后插件会报依赖缺失）"
        pass "主副本齐全，插件 vendor/ 也在归档内"
        printf '%s\n' "$entries" | grep -qF 'unrelated.txt' && fail "清单外内容被打包了" || pass "清单外内容未被打包"
        printf '%s\n' "$entries" | grep -qF '__pycache__' && fail "__pycache__ 未被排除" || pass "__pycache__ 已排除"
    fi
else
    fail "正常备份执行失败"
    sed 's/^/     /' "$WORK/t2.log" >&2
fi

echo
echo "== T3 校验和与清单 =="
archive=$(ls "$WORK/t2out"/neko-backup-*.tar.gz 2>/dev/null | head -1)
if [ -n "$archive" ] && [ -s "$archive.sha256" ] && [ -s "$archive.manifest.txt" ]; then
    if (cd "$WORK/t2out" && sha256sum -c "$(basename "$archive").sha256" >/dev/null 2>&1); then
        pass "sha256 校验通过，清单非空（$(wc -l < "$archive.manifest.txt" | tr -d ' ') 条）"
    else
        fail "sha256 校验失败"
    fi
else
    fail "缺少 .sha256 或 .manifest.txt"
fi

echo
echo "== T4 归档名唯一 + --keep 轮转 =="
make_home "$WORK/t4home"
# 同一秒内连做两次：如果归档名只有秒级精度，第二次会截断第一份
sh "$BACKUP" --home "$WORK/t4home" --out "$WORK/t4out" --no-stop >/dev/null 2>&1
sh "$BACKUP" --home "$WORK/t4home" --out "$WORK/t4out" --no-stop >/dev/null 2>&1
both=$(ls -1 "$WORK/t4out"/neko-backup-*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
if [ "$both" = "2" ]; then
    pass "同一秒内连续两次备份生成了 2 份不同归档"
else
    fail "同一秒内连续两次备份只留下 $both 份（归档名冲突，前一份被覆盖）"
fi
sh "$BACKUP" --home "$WORK/t4home" --out "$WORK/t4out" --no-stop --keep 1 >/dev/null 2>&1
left=$(ls -1 "$WORK/t4out"/neko-backup-*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
if [ "$left" = "1" ]; then
    pass "--keep 1 之后只剩 1 份"
else
    fail "--keep 1 之后剩了 $left 份"
fi

echo
echo "== T5 拒绝符号链接的数据目录 =="
make_home "$WORK/t5real"
ln -s "$WORK/t5real" "$WORK/t5link"
if sh "$BACKUP" --home "$WORK/t5link" --out "$WORK/t5out" >/dev/null 2>&1; then
    fail "符号链接的 home 被接受了"
else
    pass "拒绝符号链接的 home"
fi

echo
echo "== T6 拒绝把输出目录放在数据目录内部 =="
make_home "$WORK/t6home"
if sh "$BACKUP" --home "$WORK/t6home" --out "$WORK/t6home/.local/share/N.E.K.O/backups" >/dev/null 2>&1; then
    fail "输出目录落在数据目录内却被接受"
else
    pass "拒绝输出目录位于数据目录内部"
fi

echo
echo "== T7 空目录拒绝而不是产出空归档 =="
mkdir -p "$WORK/t7home"
if sh "$BACKUP" --home "$WORK/t7home" --out "$WORK/t7out" >/dev/null 2>&1; then
    fail "空目录被当成成功备份"
else
    pass "空目录明确失败，未产出空归档"
fi

echo
echo "== T8 --no-stop 必须明确提示没有一致性保证 =="
make_home "$WORK/t8home"
if sh "$BACKUP" --home "$WORK/t8home" --out "$WORK/t8out" --no-stop >"$WORK/t8.log" 2>&1; then
    if grep -q '不保证可恢复' "$WORK/t8.log"; then
        pass "--no-stop 给出了明确的一致性警告"
    else
        fail "--no-stop 没有警告"
    fi
else
    fail "--no-stop 执行失败"
fi

echo
echo "== T9 无法确认容器状态时拒绝冷备（而不是默默继续）=="
make_home "$WORK/t9home"
if sh "$BACKUP" --home "$WORK/t9home" --out "$WORK/t9out" >"$WORK/t9.log" 2>&1; then
    fail "容器状态无法确认却按冷备继续了"
else
    if grep -q '无法确认容器' "$WORK/t9.log" && grep -q -- '--no-stop' "$WORK/t9.log"; then
        pass "拒绝冷备并指向 --no-stop"
    else
        fail "拒绝了但没有给出 --no-stop 的指引"
    fi
fi

echo
echo "== T10 备份产物权限收紧（umask 077）=="
make_home "$WORK/t10home"
sh "$BACKUP" --home "$WORK/t10home" --out "$WORK/t10out" --no-stop >/dev/null 2>&1
archive=$(ls "$WORK/t10out"/neko-backup-*.tar.gz 2>/dev/null | head -1)
if [ -n "$archive" ]; then
    dir_mode=$(stat -c '%a' "$WORK/t10out")
    file_mode=$(stat -c '%a' "$archive")
    if [ "$dir_mode" = "700" ] && [ "$file_mode" = "600" ]; then
        pass "输出目录 700、归档 600（同机其它账号读不到 API Key 与私钥）"
    else
        fail "权限过宽：目录 $dir_mode / 归档 $file_mode"
    fi
else
    fail "没有生成归档"
fi

echo
echo "== T11 --slim 才排除插件 vendor/（默认必须备份）=="
make_home "$WORK/t11home"
sh "$BACKUP" --home "$WORK/t11home" --out "$WORK/t11out" --no-stop --slim >/dev/null 2>&1
archive=$(ls "$WORK/t11out"/neko-backup-*.tar.gz 2>/dev/null | head -1)
if [ -n "$archive" ]; then
    if tar -tzf "$archive" | grep -qF 'vendor'; then
        fail "--slim 仍然打进了 vendor/"
    else
        pass "--slim 排除了 vendor/（恢复后需重新安装插件依赖）"
    fi
else
    fail "--slim 没有生成归档"
fi

echo
if [ "$FAILED" = "0" ]; then
    echo "✅ docker/backup.sh 回归测试全部通过"
else
    echo "❌ docker/backup.sh 回归测试存在失败项" >&2
fi
exit "$FAILED"
