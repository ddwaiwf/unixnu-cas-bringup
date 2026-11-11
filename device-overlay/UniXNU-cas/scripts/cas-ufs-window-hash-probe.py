#!/usr/bin/env python3
"""Add native-ufs-probe=15: read each root window's first block and hash it.

v21 fixed the bounce-buffer overflow, yet the publisher still stops with
"UFS ROOT FAILURE: root window read failed / stage=read block integrity /
result=e00002c1 / ver=5". result e00002c1 is simply what Operations::verify()
returns when vr_verify_block() reports VR_BLOCK, i.e. the 4096 bytes read from
the device do not hash to the owner-signed value for that block index. Locally
the same bytes of userdata-root.img do hash to it (OS 9952a041..., SystemOS
55780285...), and the kernel really carries those manifests, so the remaining
question is what the device returns at the window start.

probe=15 answers exactly that, read-only: for every window it reads the first
block, reads the next block, reads the first block again, and panics with the
SHA-256 of the first and of the repeated read plus the first 16 bytes. The
repeat read distinguishes bad media (flashed gap: zeros or MIUI leftovers) from
a stale DMA/bounce (repeat equals first but both differ from the image) and from
a verification bug (digest equals the image's).

Idempotent: re-running replaces the previous insertion.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'drivers/ShengUfs/NativeUfs.cpp'
BEGIN = '  // cas-window-hash-probe-begin'
END = '  // cas-window-hash-probe-end'
ANCHOR = '  if (probe == 11) {'
GATE_OLD = '                          probe == 11 || probe == 12 || probe == 13 ||'
GATE_NEW = ('                          probe == 11 || probe == 12 || probe == 13 ||\n'
            '                          probe == 15 ||')

PROBE = f"""{BEGIN}
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
  if (probe == 15) {{
    // Read-only. The publisher fails with result e00002c1 (what verify() returns for
    // VR_BLOCK) when the block read from the device does not hash to the signed value,
    // so report what the device really returns for each window's first block, twice.
    const char *why = "";
    if (!hostBringUp (&why)) {{
      panic ("NATIVE_UFS window hash: bring-up failed: %s", why);
    }}
    RootLayout *layout = static_cast<RootLayout *> (IOMalloc (sizeof (RootLayout)));
    if (!layout || !findRootLayout (layout, &why)) {{
      panic ("NATIVE_UFS window hash: %s", why);
    }}
    uint8_t *first = gScratch;
    uint8_t *other = gScratch + 4096;
    uint8_t *repeat = gScratch + 8192;
    static char evidence[3000];
    unsigned used = 0;
    auto put = [&] (int written) {{
      if (used >= sizeof (evidence) || written < 0 || size_t (written) >= sizeof (evidence) - used) {{
        used = sizeof (evidence) - 1;
        return;
      }}
      used += unsigned (written);
    }};
    put (snprintf (evidence, sizeof (evidence),
                   "NATIVE_UFS window hash probe\\n"
                   "userdata first=%llx last=%llx windows=%u manifest=%u\\n",
                   layout->userdataFirst, layout->userdataLast, layout->windows,
                   layout->manifestStatus));
    for (unsigned index = 0; index < layout->windows && index < 4; ++index) {{
      uint64_t absolute = layout->userdataFirst * 4096ULL + layout->window[index].offset;
      uint64_t lba = absolute / 4096;
      IOReturn firstStatus = hostRead4k (0, lba, 1, first);
      IOReturn otherStatus = hostRead4k (0, lba + 1, 1, other);
      IOReturn repeatStatus = hostRead4k (0, lba, 1, repeat);
      uint8_t digest[32] = {{}};
      uint8_t second[32] = {{}};
      bool hashed = firstStatus == kIOReturnSuccess && vr_xnu_crypto.sha256 (first, 4096, digest) != 0;
      bool rehashed = repeatStatus == kIOReturnSuccess && vr_xnu_crypto.sha256 (repeat, 4096, second) != 0;
      put (snprintf (evidence + used, sizeof (evidence) - used,
                     "w%u %s off=%llx size=%llx lba=%llx read=%08x/%08x/%08x hash=%u/%u same=%u\\n",
                     index, layout->window[index].name, layout->window[index].offset,
                     layout->window[index].size, lba, firstStatus, otherStatus, repeatStatus,
                     hashed ? 1U : 0U, rehashed ? 1U : 0U,
                     hashed && rehashed && memcmp (digest, second, 32) == 0 ? 1U : 0U));
      put (snprintf (evidence + used, sizeof (evidence) - used, " head="));
      for (unsigned byte = 0; byte < 16; ++byte) {{
        put (snprintf (evidence + used, sizeof (evidence) - used, "%02x", first[byte]));
      }}
      put (snprintf (evidence + used, sizeof (evidence) - used, "\\n sha="));
      for (unsigned byte = 0; byte < 32; ++byte) {{
        put (snprintf (evidence + used, sizeof (evidence) - used, "%02x", digest[byte]));
      }}
      put (snprintf (evidence + used, sizeof (evidence) - used, "\\n"));
    }}
    panic ("%s", evidence);
  }}
#endif
{END}
"""


def strip_block(text):
    if BEGIN not in text:
        return text
    head, rest = text.split(BEGIN, 1)
    return head + rest.split(END, 1)[1].lstrip('\n')


def main():
    text = strip_block(SOURCE.read_text())
    if ANCHOR not in text:
        sys.exit('NativeUfs.cpp no longer starts probe 11 with %r' % ANCHOR)
    # The probe touches the transport, so it belongs on the mutating-probe list too.
    if 'probe == 15 ||' not in text:
        if GATE_OLD not in text:
            sys.exit('NativeUfs.cpp mutatesTransport list changed shape')
        text = text.replace(GATE_OLD, GATE_NEW, 1)
    text = text.replace(ANCHOR, PROBE + ANCHOR, 1)
    SOURCE.write_text(text)
    print('NativeUfs.cpp: native-ufs-probe=15 window hash probe installed')


if __name__ == '__main__':
    main()
