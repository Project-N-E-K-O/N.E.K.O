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

FAILED=0
pass() { printf '  ✅ %s\n' "$1"; }
fail() { printf '  ❌ %s\n' "$1" >&2; FAILED=1; }

# 造一个"看起来像 neko-home"的树
make_home() {
    home="$1"
    state="$home/.local/share/N.E.K.O"
    mkdir -p "$state/config" "$state/characters" "$state/memory" \
             "$state/plugins" "$state/state" "$state/cloudsave" \
             "$state/.neko-plugin-installations/demo/vendor" "$home/ssl"
    printf 'core\n'   > "$state/config/core_config.json"
    printf 'cat\n'    > "$state/characters/cat.json"
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
echo "== T2 正常备份：主副本在内、可重建的在外 =="
make_home "$WORK/t2home"
if sh "$BACKUP" --home "$WORK/t2home" --out "$WORK/t2out" >"$WORK/t2.log" 2>&1; then
    archive=$(ls "$WORK/t2out"/neko-backup-*.tar.gz 2>/dev/null | head -1)
    if [ -z "$archive" ]; then
        fail "没有生成归档"
    else
        entries=$(tar -tzf "$archive")
        for want in .local/share/N.E.K.O/config/core_config.json \
                    .local/share/N.E.K.O/characters/cat.json \
                    .local/share/N.E.K.O/memory/facts.json \
                    .local/share/N.E.K.O/instance_access.key \
                    ssl/N.E.K.O.crt; do
            printf '%s\n' "$entries" | grep -qF "$want" || fail "主副本缺失: $want"
        done
        printf '%s\n' "$entries" | grep -qF 'vendor' && fail "vendor/ 未被排除" || pass "主副本齐全，vendor/ 已排除"
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
echo "== T4 --keep 轮转 =="
make_home "$WORK/t4home"
i=0
while [ "$i" -lt 3 ]; do
    sh "$BACKUP" --home "$WORK/t4home" --out "$WORK/t4out" --keep 1 >/dev/null 2>&1
    i=$((i + 1))
done
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
if [ "$FAILED" = "0" ]; then
    echo "✅ docker/backup.sh 回归测试全部通过"
else
    echo "❌ docker/backup.sh 回归测试存在失败项" >&2
fi
exit "$FAILED"
