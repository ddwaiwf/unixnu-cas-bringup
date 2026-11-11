#!/bin/bash
# One-shot: boot a released hackios loader for cas, then drive it from macOS.
#
#   ./run_hackios.sh          # 2026-10-29 UFS build  (cas-ufs2 + stable payload, drivers only)
#   ./run_hackios.sh gpu      # display build        (cas-touch2 + gpu/wifi payload, tries SpringBoard)
#
# The loader speaks a small fastboot-style protocol over bulk endpoints; the
# Windows release drives it with a WinUSB daemon. boot_hackios.py is the macOS
# equivalent (pyusb + the libusb bundled by the libusb-package wheel).
set -u
cd "$(dirname "$0")"
FB=tools/platform-tools/fastboot
A=ref-hackios-assets

case "${1:-ufs}" in
  # cas-touch2.img has no TzDxeLA patch, so its UEFI drops off the bus ~2 s in
  # (release note #1) — keep it only for reference.
  gpu) LOADER="$A/cas-touch2.img"; PAYLOAD="$A/run-payload-gpu-wifi.bin"; ROOTFS="$A/root-gpu.apfs" ;;
  # only cas-ufs2.img carries the TzDxeLA patch (release note #1), so it is the
  # one loader that survives past the first two seconds; pair it with the GPU
  # payload to get both a live bus and display bring-up.
  ufs2gpu) LOADER="$A/cas-ufs2.img"; PAYLOAD="$A/run-payload-gpu-wifi.bin"; ROOTFS="$A/root-gpu.apfs" ;;
  # the umi loader has the "hand the GPU back" reset that the self-built one
  # lacks, which is what the release notes recommend once a GPU bring-up has
  # already stalled at adreno step 20.
  umi) LOADER="$A/cas-umiloader.img"; PAYLOAD="$A/run-payload-gpu-wifi.bin"; ROOTFS="$A/root-gpu.apfs" ;;
  *)   LOADER="$A/cas-ufs2.img";   PAYLOAD="$A/run-payload-cas-stable.bin"; ROOTFS="$A/root.apfs" ;;
esac

for f in "$LOADER" "$PAYLOAD" "$ROOTFS"; do
  [ -f "$f" ] || { echo "missing $f"; exit 1; }
done

echo "=== 1/2 fastboot boot $(basename "$LOADER") ($(du -h "$LOADER" | cut -f1)) ==="
for try in 1 2 3 4; do
  out=$($FB boot "$LOADER" 2>&1 | tail -3)
  echo "$out"
  echo "$out" | grep -q "Booting.*OKAY" && break
  sleep 2
done

echo "=== 2/2 driving the loader (payload $(du -h "$PAYLOAD" | cut -f1), rootfs $(du -h "$ROOTFS" | cut -f1)) ==="
# the loader needs a few seconds after enumerating before its command loop runs;
# boot_hackios.py retries the handshake for 40 s, so a short sleep is enough here
sleep 5
python3 -u boot_hackios.py "$PAYLOAD" "$ROOTFS"
