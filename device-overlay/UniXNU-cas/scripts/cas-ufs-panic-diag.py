#!/usr/bin/env python3
"""Report the UFS root-layout parser's real findings through panic.

The boot-progress panel reads diagnostics from globals that the kernel links as
zero-initialised stubs when the driver does not define them, so a panel counter
can read 0 while the driver is actually working (and vice versa). Panic output
comes from the kernel's own console path and cannot be substituted that way, so
for bring-up the parser reports its findings there instead.

Only a failed parse panics: once the flashed image is correct the check is
silent and the boot continues.

Idempotent: re-running replaces the previous insertion.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / 'drivers/ShengUfs/RootLayout.h'
SOURCE = ROOT / 'drivers/ShengUfs/NativeUfs.cpp'
BEGIN = '  // cas-bring-up-panic-begin'
END = '  // cas-bring-up-panic-end'
PROBE_BEGIN = '  // cas-bring-up-probe-panic-begin'
PROBE_END = '  // cas-bring-up-probe-panic-end'

INCLUDE = '#include <kern/debug.h>'
ANCHOR_INC = '#pragma once'
ANCHOR_CODE = '  gRootDiagnostic.windows = layout->windows;'
ANCHOR_PROBE = '  native_ufs_probe_calls = native_ufs_probe_calls + 1;'

PROBE_REPORT = f"""{PROBE_BEGIN}
  // probe() is the only way the personality reaches this driver, and its very
  // first statement bumps the counter above, so reaching here proves the match
  // happened. Panic (rather than a counter a stub could shadow) leaves no doubt,
  // and the provider's name says which nub matched.
  {{
    const char *nubname = provider ? provider->getName () : 0;
    panic ("CASUFS probe entered provider=%s", nubname ? nubname : "null");
  }}
{PROBE_END}"""

REPORT = f"""{BEGIN}
  // magic0/magic1 are the partition's first eight bytes: "SHNGROOT"
  // (0x474e4853/0x544f4f52) when our image landed, zero when the block was never
  // written, and a filesystem signature (ext4 0xef53 at byte 1080, f2fs at 1024)
  // when something else owns the partition. status tells which check failed:
  // 1 magic, 2 version/count, 3 header hash, 4 bounds, 5 names, 6 overlap, 7 sector size.
  if (layout->manifestStatus != 0) {{
    panic("CASUFS layout magic=%08x/%08x status=%u windows=%u "
          "first=%llx last=%llx ext4=%04x imageBytes=%llx",
          layout->magic0, layout->magic1, layout->manifestStatus, layout->windows,
          layout->userdataFirst, layout->userdataLast, layout->ext4Magic, layout->imageBytes);
  }}
{END}"""


def strip_block(text, begin, end):
    """Drop an earlier insertion so re-running the patch stays idempotent."""
    if begin not in text:
        return text
    head, rest = text.split(begin, 1)
    return head.rstrip(' ') + rest.split(end, 1)[1].lstrip('\n')


def main():
    # --- RootLayout.h: the parser's verdict, only when parsing failed ---------
    text = strip_block(HEADER.read_text(), BEGIN, END).replace('  \n', '\n')
    if INCLUDE not in text:
        text = text.replace(ANCHOR_INC, ANCHOR_INC + '\n' + INCLUDE, 1)
    if ANCHOR_CODE not in text:
        sys.exit('RootLayout.h no longer ends its parse with %r' % ANCHOR_CODE)
    HEADER.write_text(text.replace(ANCHOR_CODE, ANCHOR_CODE + '\n' + REPORT, 1))
    print('RootLayout.h: layout verdict panic installed')

    # --- NativeUfs.cpp: prove whether probe() runs at all ---------------------
    source = strip_block(SOURCE.read_text(), PROBE_BEGIN, PROBE_END).replace('  \n', '\n')
    if INCLUDE not in source and '#include "RootLayout.h"' not in source:
        source = source.replace('#include "VerifiedBlockPolicy.h"',
                                '#include "VerifiedBlockPolicy.h"\n' + INCLUDE, 1)
    if ANCHOR_PROBE not in source:
        sys.exit('NativeUfs.cpp probe() no longer starts with %r' % ANCHOR_PROBE)
    SOURCE.write_text(source.replace(ANCHOR_PROBE, ANCHOR_PROBE + '\n' + PROBE_REPORT, 1))
    print('NativeUfs.cpp: probe-entry panic installed')


if __name__ == '__main__':
    main()
