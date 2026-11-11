#!/usr/bin/env python3
"""Make the ShengUfs whole-userdata rename path IOMedia-free.

Why
---
`renameWholeUserdataThread()` walks the IOMedia services to rename the published
whole-userdata media to `ufsroot0`. Two `OSDynamicCast(IOMedia, ...)` in that
loop are the *only* references to the IOStorageFamily IOMedia class in the
driver, and IOStorageFamily is prelinked: IOMedia::metaClass is not linkable at
kernel link time unless it is bound to an absolute address out of the prelink
closure. On cas that binding does not exist (the donor kernelcache keeps no
per-kext symbol table for it), and the link dies with:

    __ZN7IOMedia9metaClassE  referenced from libShengUfs.a[2](NativeUfs.o)

The rename itself does not need IOMedia's type: `Whole` is a plain IOMedia
property (`kIOMediaWholeKey`) and the rest of the loop only uses IOService /
IORegistryEntry members (getProperty, setProperty, getRegistryEntryID, and the
parent walk). Casting to IOService and testing the property keeps the exact
behaviour (whole media only, ancestor-filtered) with zero IOStorageFamily
symbols, so the kernel links with the storage bindings it already has.

Runs from the board archives list, before component:ShengUfs, and is idempotent.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'drivers/ShengUfs/NativeUfs.cpp'
MARKER = 'ufsServiceIsWhole'

HELPER = '''// cas: IOStorageFamily is prelinked, so IOMedia::metaClass is not linkable here.
// `Whole` is IOMedia's own property (kIOMediaWholeKey); testing it on the
// IOService returned by serviceMatching("IOMedia") is the same filter without
// referencing the class symbol. The rest of the rename loop only uses IOService
// and IORegistryEntry members.
static bool ufsServiceIsWhole (IOService *service)
{
  OSBoolean *whole = OSDynamicCast (OSBoolean, service->getProperty ("Whole"));
  return whole && whole->getValue ();
}

'''


def fail(message):
    raise SystemExit('cas-ufs-iomedia-free: ' + message)


def main():
    if not SOURCE.is_file():
        fail(f'driver source not found: {SOURCE}')
    text = SOURCE.read_text()
    if MARKER in text:
        print('cas-ufs-iomedia-free: already applied')
        return

    if '#include <libkern/c++/OSBoolean.h>' not in text:
        anchor = '#include <libkern/c++/OSString.h>\n'
        if anchor not in text:
            fail('cannot find the OSString include to anchor the OSBoolean include')
        text = text.replace(anchor, anchor + '#include <libkern/c++/OSBoolean.h>\n', 1)

    cast = 'OSDynamicCast (IOMedia, '
    if text.count(cast) != 2:
        fail(f'expected exactly two IOMedia casts, found {text.count(cast)}')
    text = text.replace('while (IOMedia *media = OSDynamicCast (IOMedia, ',
                        'while (IOService *media = OSDynamicCast (IOService, ')
    if 'OSDynamicCast (IOMedia, ' in text:
        fail('an IOMedia cast outside the rename loops remains; inspect the source')

    whole = 'if (!media->isWhole () || !ufsHasAncestor (media, device)) continue;'
    if text.count(whole) != 2:
        fail(f'expected two isWhole filters, found {text.count(whole)}')
    text = text.replace(whole, 'if (!ufsServiceIsWhole (media) || !ufsHasAncestor (media, device)) continue;')

    anchor = '// IOBlockStorageDriver publishes a normal IOMedia and IOMediaBSDClient assigns'
    if anchor not in text:
        fail('cannot find the rename thread comment to anchor the helper')
    text = text.replace(anchor, HELPER + anchor, 1)

    SOURCE.write_text(text)
    print('cas-ufs-iomedia-free: rename path now uses IOService + the Whole property')


if __name__ == '__main__':
    sys.exit(main())
