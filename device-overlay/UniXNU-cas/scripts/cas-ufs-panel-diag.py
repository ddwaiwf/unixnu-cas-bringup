#!/usr/bin/env python3
"""Expose the UFS root-layout verdict on the boot-progress panel.

Why
---
When `startRoot()` rejects the userdata SHNGROOT manifest it only IOLogs a
status code, and that line scrolls off the screen before the panic is taken.
The panel already prints probe/match/root_step, but those cannot tell a
manifest rejection (status 1..7 from RootLayout.h) from a later publication
failure: both leave published=0.

This adds three plain globals to the driver and fills them right after
`findRootLayout()` succeeds, so the panel can print:

    mstat=<manifestStatus hex> win=<window count> mgc=<magic0>/<magic1>

0x0 means the manifest parsed; 0xFF means startRoot never got that far.
Status values are the ones RootLayout.h assigns: 1 no magic, 2 version/count,
3 header SHA mismatch, 4 window bounds, 5 window name, 6 window overlap,
7 sector size/count.

Runs from the board archives list before component:ShengUfs. Idempotent.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'drivers/ShengUfs/NativeUfs.cpp'
MARKER = 'native_ufs_manifest_status'

ANCHOR = 'volatile uint32_t native_ufs_probe_calls = 0;'
GLOBALS = '''volatile uint32_t native_ufs_probe_calls = 0;
// cas: the root-layout verdict, for the boot-progress panel (see cas-ufs-panel-diag.py).
volatile uint32_t native_ufs_manifest_status = 0xFF;
volatile uint32_t native_ufs_windows = 0;
volatile uint32_t native_ufs_magic0 = 0;
volatile uint32_t native_ufs_magic1 = 0;'''

STEP_ANCHOR = '''  native_ufs_root_step = 3;
  if (layout->manifestStatus != 0) {'''
STEP_REPLACE = '''  native_ufs_root_step = 3;
  native_ufs_manifest_status = layout->manifestStatus;
  native_ufs_windows = layout->windows;
  native_ufs_magic0 = layout->magic0;
  native_ufs_magic1 = layout->magic1;
  if (layout->manifestStatus != 0) {'''


def main():
    if not SOURCE.is_file():
        raise SystemExit(f'cas-ufs-panel-diag: driver source not found: {SOURCE}')
    text = SOURCE.read_text()
    if MARKER in text:
        print('cas-ufs-panel-diag: already applied')
        return 0
    if text.count(ANCHOR) != 1:
        raise SystemExit('cas-ufs-panel-diag: probe_calls anchor not unique')
    text = text.replace(ANCHOR, GLOBALS, 1)
    if text.count(STEP_ANCHOR) != 1:
        raise SystemExit('cas-ufs-panel-diag: startRoot anchor not unique')
    text = text.replace(STEP_ANCHOR, STEP_REPLACE, 1)
    SOURCE.write_text(text)
    print('cas-ufs-panel-diag: manifest status/windows/magic exported')
    return 0


if __name__ == '__main__':
    sys.exit(main())
