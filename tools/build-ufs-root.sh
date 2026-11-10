#!/bin/bash
# UFS real-root build chain for the WSL/Linux toolchain.
# Run from the build tree root (the dir that contains unixnu / tree/).
#
#   bash build-ufs-root.sh            # build all archives + kernel
#   bash build-ufs-root.sh archives   # archives only
#   bash build-ufs-root.sh kernel     # kernel only (archives must exist)
#
# What it does (in order):
#   1. Puts tree/toolchain/xcrun (the macOS->Linux shim) in PATH
#   2. Builds the verified-root OS+SystemOS policy bundle probe
#   3. Builds VirtioMmioBlock in verified-root-bundle mode
#   4. Builds ShengUfs (NativeUfs) with verified-block flags
#   5. Appends the storage bindings (IOMedia + IOBlockStorageDevice aliases)
#   6. Runs the kernel build with force_load of all the above
set -euo pipefail

TREE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$TREE/toolchain:$PATH"
export UNIXNU_LLVM="${UNIXNU_LLVM:-/opt/unixnu-linux/llvm/bin}"

step() { echo; echo "==> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# --- sanity: shim works ---
command -v xcrun >/dev/null || fail "xcrun shim not found; expected $TREE/toolchain/xcrun"
xcrun --sdk iphoneos --show-sdk-path >/dev/null || fail "xcrun shim broken"

# --- 1. verified-root bundle (OS + SystemOS policies) ---
# Needs build/verified-root-os/ + build/verified-root-systemos/ (the signed
# policy dirs from the verified-root host toolchain, README §11.1).
build_verified_root() {
  step "verified-root bundle probe (OS + SystemOS)"
  if [ ! -f build/verified-root-os/verification.json ]; then
    echo "SKIP: build/verified-root-os/verification.json missing (run the host toolchain first)"
    return 0
  fi
  if [ ! -f build/verified-root-systemos/verification.json ]; then
    echo "SKIP: build/verified-root-systemos/verification.json missing"
    return 0
  fi
  python3 scripts/build-verified-root-probe.py --component os --include-systemos
  local a=build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a
  [ -f "$a" ] || fail "verified-root bundle not produced"
  "$UNIXNU_LLVM/llvm-nm" "$a" | grep -q '_verified_root_policies' \
    || fail "_verified_root_policies missing from $a"
  echo "OK: $a"
}

# --- 2. VirtioMmioBlock verified bundle ---
build_virtio() {
  step "VirtioMmioBlock verified-root bundle"
  python3 scripts/build-arm-driver.py NativeVirtioBlock --verified-root --verified-root-bundle
  local a=build/virtio-mmio-block-verified-bundle/libVirtioMmioBlock.a
  [ -f "$a" ] || fail "virtio verified bundle not produced"
  "$UNIXNU_LLVM/llvm-nm" "$a" | grep -q 'native_verified_block_root_authorized' \
    || fail "native_verified_block_root_authorized missing from $a"
  # must be a real definition (T/t), not a stub (S/s)
  local type
  type=$("$UNIXNU_LLVM/llvm-nm" "$a" | awk '/native_verified_block_root_authorized/{print $2}')
  [ "$type" = "T" ] || [ "$type" = "t" ] || fail "symbol is $type (stub?), expected T/t"
  echo "OK: $a  (native_verified_block_root_authorized = $type)"
}

# --- 3. ShengUfs / NativeUfs ---
build_ufs() {
  step "ShengUfs (NativeUfs) with verified-block"
  python3 scripts/build-arm-driver.py NativeUfs
  local a=build/sheng-ufs/libShengUfs.a
  [ -f "$a" ] || fail "libShengUfs.a not produced"
  "$UNIXNU_LLVM/llvm-nm" "$a" | grep -q 'native_ufs_block_authenticates' \
    || fail "native_ufs_block_authenticates missing from $a"
  echo "OK: $a"
}

# --- 4. storage bindings (IOMedia::gMetaClass etc.) ---
build_bindings() {
  step "storage bindings (append to libShengUfs.a)"
  if [ ! -f build/storage-abi/interface-audit.json ]; then
    echo "NOTE: interface-audit.json missing; running audit-storage-interface.py"
    python3 scripts/audit-storage-interface.py || {
      echo "WARN: audit failed (needs the prelinked kernel candidate)."
      echo "      Falling back to build-virtio-mmio-bindings.py for what it covers."
    }
  fi
  python3 scripts/build-virtio-mmio-bindings.py --component NativeVirtioBlock \
      --directory build/virtio-mmio-block || echo "WARN: virtio bindings step failed"
  # The IOMedia gap: build-virtio-mmio-bindings.py does not cover IOMedia.
  # Use the fill tool if the prelinked kernel candidate is available.
  if [ -f tools/fill-missing-original-bindings.py ]; then
    python3 tools/fill-missing-original-bindings.py \
        --tree "$TREE" \
        --object build/sheng-ufs/NativeUfs.o \
        --out-dir build/extra-storage-bindings \
        --archive build/sheng-ufs/libShengUfs.a \
      || echo "WARN: IOMedia binding fill skipped (prelinked candidate missing?)"
  fi
}

# --- 5. kernel ---
build_kernel() {
  step "kernel (force_load of UFS + verified-root chain)"
  ./unixnu kernel
}

case "${1:-all}" in
  archives) build_verified_root; build_virtio; build_ufs; build_bindings ;;
  kernel)   build_kernel ;;
  all)      build_verified_root; build_virtio; build_ufs; build_bindings; build_kernel ;;
  *) echo "usage: $0 [archives|kernel|all]"; exit 1 ;;
esac

echo
echo "==> done. Verify the kernel before flashing:"
echo "    \$UNIXNU_LLVM/llvm-nm kernel/kernel.macho | grep -E 'native_verified|native_ufs_block|IOMedia10gMetaClass'"
echo "    strings kernel/kernel.macho | grep NativeUfs:"
