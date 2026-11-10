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
# ShengUfs 的绑定脚本不在 UniXNU 框架里，来自 alioth 的 kit（gitlink）
if [ ! -f "$TREE/scripts/build-storage-bindings.py" ]; then
  echo "    注意: scripts/build-storage-bindings.py 缺失，board.toml 的 archives 会失败"
  echo "           从 alioth 设备仓的 kits/ 或已有构建树里拷一份"
fi
ok

# --- 5b. 前置条件体检：明确告诉你哪些还没就绪 ---
step "5b/6 前置条件体检（缺的会列出来，不会静默跳过）"
cd "$TREE"
missing=0
check_dir() {
  if [ -d "$1" ] && [ -n "$(ls -A "$1" 2>/dev/null)" ]; then
    echo "    [OK]   $2"
  else
    echo "    [缺]  $2  <- $3"
    missing=$((missing+1))
  fi
}
check_file() {
  if [ -f "$1" ]; then echo "    [OK]   $2"; else
    echo "    [缺]  $2  <- $3"; missing=$((missing+1)); fi
}
check_dir  build/verified-root-os            "OS 签名策略"        "在 Mac 上跑 build-owner-os-policy.py（需 hdiutil 挂载 044-76040-038.dmg）"
check_dir  build/verified-root-systemos      "SystemOS 签名策略"  "同上，044-76034-038.dmg"
check_dir  build/verified-root-host          "host 工具链+密钥"   "README §11.1 说 Mac 已就绪"
check_dir  build/xnu-prelink                 "prelinked candidate" "构建链产出 native-apfs-load-positive.macho"
check_dir  build/darwinboot-storage-native-metadata "真机 GDB live metadata" "必须从运行中的内核抓，Windows 侧无法代做"
check_dir  build/storage-abi                 "存储 ABI 审计"      "跑 audit-storage-interface.py"
check_file scripts/build-storage-bindings.py "ShengUfs 绑定脚本"  "来自 alioth kit"
echo
if [ "$missing" -gt 0 ]; then
  echo "    还有 $missing 项没就绪。下面仍会尝试构建，但很可能在审计/绑定这步停下。"
  echo "    这是预期内的：存储绑定的地址要从真机运行时抓，不是纯离线能补的。"
fi

# --- 6. 开编 ---
step "6/6 构建 UFS 真根内核"
cd "$TREE"
bash build-ufs-root.sh

echo
echo "==> 完成。上机前自检："
echo "    llvm-nm kernel/kernel.macho | grep -E 'native_verified|native_ufs_block|IOMedia10gMetaClass'"
echo "    strings kernel/kernel.macho | grep NativeUfs:"
echo "    fastboot boot <镜像>       # 先看 boot-progress 面板 published/probe/match 是否非零"
