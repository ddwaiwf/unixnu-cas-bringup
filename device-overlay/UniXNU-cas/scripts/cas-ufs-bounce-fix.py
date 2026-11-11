#!/usr/bin/env python3
"""Allocate the root-window bounce buffer for the largest read the transport does.

ufsReadWindow() reads up to kDataMax (131072 bytes = 32 x 4K) per hostRead4k()
call, but NativeUfsBlock allocated its bounce buffer with
alioth::whole_userdata_root::kMediaBlockBytes, which is 4096. Every window read
therefore wrote 128 KiB into a 4 KiB heap allocation: the first block usually
survived inside the allocation, the rest landed in neighbouring kernel heap, and
the integrity verifier was handed corrupted data. That surfaced as
"root window read failed" with result 0xe00002c1 (kIOReturnNotPrivileged, what
verify() returns) and vr status 5 (VR_BLOCK) even though the flashed image and
the policy table were verified to match byte for byte.

kMediaBlockBytes itself must stay 4096: it is the media logical block size, and
the parser compares the device's reported LBA size against it.

Idempotent: re-running is a no-op once the sizes are fixed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'drivers/ShengUfs/NativeUfs.cpp'

OLD_ALLOC = 'IOMalloc (alioth::whole_userdata_root::kMediaBlockBytes)'
NEW_ALLOC = 'IOMalloc (kDataMax)'
OLD_FREE = 'IOFree (bounce, alioth::whole_userdata_root::kMediaBlockBytes)'
NEW_FREE = 'IOFree (bounce, kDataMax)'


def main():
    text = SOURCE.read_text()
    alloc_hits = text.count(OLD_ALLOC)
    free_hits = text.count(OLD_FREE)
    if not alloc_hits and not free_hits:
        print('NativeUfs.cpp: bounce sizes already fixed (%s/%s)' % (NEW_ALLOC, NEW_FREE))
        return
    if alloc_hits != 1 or free_hits != 2:
        sys.exit('unexpected match count alloc=%d free=%d; the driver changed shape'
                 % (alloc_hits, free_hits))

    text = text.replace(OLD_ALLOC, NEW_ALLOC).replace(OLD_FREE, NEW_FREE)
    SOURCE.write_text(text)
    print('NativeUfs.cpp: bounce buffer sized for kDataMax (131072 bytes)')


if __name__ == '__main__':
    main()
