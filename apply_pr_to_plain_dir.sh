#!/usr/bin/env bash
set -euo pipefail

PR="${1:?usage: apply_pr_to_plain_dir.sh <pr_number> [target_dir]}"
TARGET="${2:-/workspace/llm-stack/CacheRoute-wangchen}"
REPO="${REPO:-https://github.com/AstraNetLab/CacheRoute.git}"

TMP="/tmp/cacheroute_pr_${PR}"
BACKUP="/tmp/cacheroute_backup_pr_${PR}_$(date +%Y%m%d_%H%M%S)"

if [[ ! -d "$TARGET" ]]; then
    echo "[ERROR] Target directory does not exist: $TARGET" >&2
    exit 1
fi

# 使用目标仓库根目录的所有者作为新文件的默认所有者。
TARGET_UID="${TARGET_UID:-$(stat -c '%u' "$TARGET")}"
TARGET_GID="${TARGET_GID:-$(stat -c '%g' "$TARGET")}"

echo "[Target] $TARGET"
echo "[PR] #$PR"
echo "[Owner] ${TARGET_UID}:${TARGET_GID}"

rm -rf "$TMP"
git clone --quiet "$REPO" "$TMP"

cd "$TMP"

git fetch origin "pull/${PR}/head:pr-${PR}" --force
git switch --quiet "pr-${PR}"

BASE="$(git merge-base origin/main "pr-${PR}")"

echo
echo "[Changed files]"
git diff --name-status "$BASE" "pr-${PR}"

echo
read -r -p "Apply these files to $TARGET ? [y/N] " ans

if [[ ! "$ans" =~ ^[Yy]$ ]]; then
    echo "Canceled."
    exit 0
fi

mkdir -p "$BACKUP"

# 备份目录只用于容器内部恢复，不影响宿主机仓库。
backup_path() {
    local rel_path="$1"
    local src="$TARGET/$rel_path"

    if [[ -e "$src" || -L "$src" ]]; then
        mkdir -p "$BACKUP/$(dirname "$rel_path")"
        cp -a "$src" "$BACKUP/$rel_path"
    fi
}

# 确保目标目录属于目标用户。
ensure_target_dir() {
    local dir="$1"

    mkdir -p "$dir"
    chown "$TARGET_UID:$TARGET_GID" "$dir"
}

copy_file() {
    local src_path="$1"
    local rel_path="$2"
    local src="$TMP/$src_path"
    local dst="$TARGET/$rel_path"
    local dst_dir
    local mode

    dst_dir="$(dirname "$dst")"

    backup_path "$rel_path"
    ensure_target_dir "$dst_dir"

    # 读取 Git 中源文件当前的权限位，例如 644 或 755。
    mode="$(stat -c '%a' "$src")"

    if [[ -L "$src" ]]; then
        rm -f "$dst"
        cp -P "$src" "$dst"

        # chown -h 修改符号链接本身的所有者。
        chown -h "$TARGET_UID:$TARGET_GID" "$dst"
    else
        # install 会复制文件，并显式设置权限和所有者。
        install \
            -m "$mode" \
            -o "$TARGET_UID" \
            -g "$TARGET_GID" \
            "$src" \
            "$dst"
    fi

    echo "[COPY] $rel_path"
}

delete_path() {
    local rel_path="$1"
    local dst="$TARGET/$rel_path"

    if [[ -e "$dst" || -L "$dst" ]]; then
        backup_path "$rel_path"
        rm -rf "$dst"
        echo "[DELETE] $rel_path"
    fi
}

while IFS=$'\t' read -r status path1 path2; do
    case "$status" in
        D*)
            delete_path "$path1"
            ;;

        R*)
            # Git 重命名：先备份并删除旧文件，再复制新文件。
            backup_path "$path1"
            delete_path "$path1"
            copy_file "$path2" "$path2"
            echo "[RENAME] $path1 -> $path2"
            ;;

        C*)
            # Git 复制：保留旧文件，只添加新文件。
            copy_file "$path2" "$path2"
            echo "[COPY-RENAME] $path1 -> $path2"
            ;;

        A*|M*|T*)
            copy_file "$path1" "$path1"
            ;;

        *)
            echo "[WARN] Unsupported git status: $status $path1 $path2" >&2
            ;;
    esac
done < <(git diff --name-status "$BASE" "pr-${PR}")

# 修正本次操作可能创建的空目录或中间目录。
while IFS= read -r -d '' dir; do
    chown "$TARGET_UID:$TARGET_GID" "$dir"
done < <(find "$TARGET" -type d -print0)

echo
echo "[Done]"
echo "Backup saved to: $BACKUP"
echo "Applied files are owned by: ${TARGET_UID}:${TARGET_GID}"
