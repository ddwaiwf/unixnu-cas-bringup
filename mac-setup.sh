#!/bin/bash
# Mac 侧一键收口：把 Windows 端今晚的改动全部同步到 Mac 构建树，然后开编。
# 用法：改下面 3 个路径 -> bash mac-setup.sh
set -euo pipefail

# === 按你的实际路径改这 3 行 ===
REPO="${REPO:-$HOME/unixnu/repo-cas-bringup}"        # 本仓库（文档+补丁+工具）
BOARD="${BOARD:-$HOME/unixnu/UniXNU-cas}"            # cas 设备仓
FW="${FW:-$HOME/unixnu/UniXNU}"                     # 框架仓（scripts/ 在这里）
TREE="${TREE:-$FW/out/cas/ipados-17.7.11-21H461/cas/tree}"  # 构建树

step() { echo; echo "==> $*"; }
ok()   { echo "    OK"; }
skip() { echo "    SKIP: $*"; }

# --- 1. 拿到今晚的全部交付 ---
step "1/6 拉取仓库最新（9d7773f）"
if [ -d "$REPO/.git" ]; then
  git -C "$REPO" pull --ff-only
else
  git clone https://github.com/ddwaiwf/unixnu-cas-bringup.git "$REPO"
fi
ok

# --- 2. 设备仓：defconfig + board.toml ---
step "2/6 应用 cas 设备仓补丁（UFS 开关 + archives 顺序）"
cd "$BOARD"
if git apply --check "$REPO/patches/cas-device-ufs-root.patch" 2>/dev/null; then
  git apply "$REPO/patches/cas-device-ufs-root.patch"
  ok
else
  # Mac 白天可能已经改过同样的内容；三方合并，冲突时停下来让人看
  echo "    直接 apply 失败，试三方合并..."
  git apply --3way "$REPO/patches/cas-device-ufs-root.patch" || {
    echo "    有冲突，请手动核对 configs/cas_defconfig 与 board.toml 的 archives 行"; exit 1; }
  ok
fi

# --- 3. 框架仓：IOMedia 绑定修复 ---
step "3/6 应用框架补丁（audit / bindings 覆盖 IOMedia）"
cd "$FW"
if git apply --check "$REPO/patches/storage-bindings-cover-iomedia.patch" 2>/dev/null; then
  git apply "$REPO/patches/storage-bindings-cover-iomedia.patch"
  ok
else
  skip "框架补丁已应用或有差异，检查 scripts/audit-storage-interface.py 是否含 iomedias"
fi

# --- 4. 补脚本 ---
step "4/6 补齐脚本"
cp "$REPO/tools/audit-iomedia-metaclass.py" "$TREE/scripts/"
cp "$REPO/tools/fill-missing-original-bindings.py" "$TREE/tools/" 2>/dev/null || {
  mkdir -p "$TREE/tools"; cp "$REPO/tools/fill-missing-original-bindings.py" "$TREE/tools/"; }
cp "$REPO/tools/build-ufs-root.sh" "$TREE/"
chmod +x "$TREE/build-ufs-root.sh"
ok

# --- 5. 补驱动源码与 kit（若 Mac 树里还没有） ---
step "5/6 检查驱动源码 / VerifiedRoot kit / boot-progress"
for d in drivers/ShengUfs drivers/VirtioMmioBlock kits/VerifiedRoot; do
  if [ -d "$TREE/$d" ] && [ -n "$(ls -A "$TREE/$d" 2>/dev/null)" ]; then
    skip "$d 已存在"
  else
    echo "    缺 $d —— 从 GitHub 拉："
    case "$d" in
      drivers/ShengUfs)        git clone --depth 1 https://github.com/FFT-infra/NativeUfs-alioth.git "$TREE/$d" ;;
      drivers/VirtioMmioBlock) git clone --depth 1 https://github.com/UniXNU/VirtioMmioBlock.git "$TREE/$d" ;;
      kits/VerifiedRoot)       git clone --depth 1 https://github.com/UniXNU/VerifiedRoot.git "$TREE/$d" ;;
    esac
  fi
done
for s in build-boot-progress.py build-link-stubs.py; do
  [ -f "$TREE/scripts/$s" ] || echo "    注意: scripts/$s 需要从 alioth 设备仓拷（同 SM8250）"
done
ok

# --- 6. 开编 ---
step "6/6 构建 UFS 真根内核"
cd "$TREE"
bash build-ufs-root.sh

echo
echo "==> 完成。上机前自检："
echo "    llvm-nm kernel/kernel.macho | grep -E 'native_verified|native_ufs_block|IOMedia10gMetaClass'"
echo "    strings kernel/kernel.macho | grep NativeUfs:"
echo "    fastboot boot <镜像>       # 先看 boot-progress 面板 published/probe/match 是否非零"
