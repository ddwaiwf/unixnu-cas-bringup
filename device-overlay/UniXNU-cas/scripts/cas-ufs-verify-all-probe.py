#!/usr/bin/env python3
"""Add native-ufs-probe=16: verify every block of every root window, read-only.

The flaky part of this bring-up is no longer the driver: it is whether a 6-9 GiB
sparse flash really landed on the eMMC/UFS. v23 stopped at
"root window read failed / verify=5 (VR_BLOCK)" on a block 5.1 GiB into the OS
window after 243 good reads, i.e. exactly the kind of hole a partial flash
leaves behind. Sampling a few blocks is not enough to trust the media, so this
probe authenticates the owner policy for each window and then verifies *every*
4096-byte block against the signed hash table:

  - ok/bad counters per window,
  - the first bad window and LBA,
  - total bytes and elapsed milliseconds.

It runs entirely inside start() and ends in a panic, so the answer arrives on
the panic screen with no userspace involved. Cost is one full pass over the
image (SHA-256 per block), which is the same work a real mount does anyway.

Idempotent: re-running replaces the previous insertion.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'drivers/ShengUfs/NativeUfs.cpp'
BEGIN = '  // cas-verify-all-probe-begin'
END = '  // cas-verify-all-probe-end'
# Insert with the other probe branches. NOTE: a full 9.4 GiB verification takes
# minutes, while probe mode publishes no IOMedia, so bsd_init's root wait times
# out and panics "Failed to mount root device" first (v26). This probe is only
# usable together with an already-mounted root or a delayed root wait; the
# production path verifies every block as it is read anyway, so it is kept as an
# optional archive, never enabled by the default kernel args.
ANCHOR = '  if (probe == 11) {'
GATE_OLD = '                          probe == 15 ||'
GATE_NEW = ('                          probe == 15 || probe == 16 ||')

PROBE = f"""{BEGIN}
#if defined(NATIVE_VERIFIED_BLOCK) && NATIVE_VERIFIED_BLOCK
  if (probe >= 16) {{
    // Read-only full-window integrity pass: every 4096-byte block against the
    // owner-signed hash table, so a partial flash cannot hide behind a sample.
    const char *why = "";
    if (!hostBringUp (&why)) {{
      panic ("NATIVE_UFS verify all: bring-up failed: %s", why);
    }}
    RootLayout *layout = static_cast<RootLayout *> (IOMalloc (sizeof (RootLayout)));
    if (!layout || !findRootLayout (layout, &why)) {{
      panic ("NATIVE_UFS verify all: %s", why);
    }}
    uint64_t t0 = timerTicks ();
    uint64_t passed = 0, failed = 0, bytesDone = 0;
    const char *badWindow = "-";
    uint64_t badLba = 0, badIndex = 0, badExpected = 0;
    static char evidence[900];
    unsigned used = 0;
    auto put = [&] (int written) {{
      if (used >= sizeof (evidence) || written < 0 || size_t (written) >= sizeof (evidence) - used) {{
        used = sizeof (evidence) - 1;
        return;
      }}
      used += unsigned (written);
    }};
    for (unsigned index = 0; index < layout->windows && index < 4; ++index) {{
      uint64_t bytes = layout->window[index].size;
      uint64_t absolute = layout->userdataFirst * 4096ULL + layout->window[index].offset;
      const uint8_t *manifest = nullptr;
      uint64_t manifestBytes = 0;
      for (uint64_t policy = 0; policy < verified_root_policy_count; ++policy) {{
        const NativeVerifiedBlockPolicy &entry = verified_root_policies[policy];
        uint64_t imageBytes = 0;
        for (unsigned i = 0; i < 8; ++i) {{
          imageBytes |= static_cast<uint64_t> (entry.manifest[24 + i]) << (8 * i);
        }}
        if (entry.manifest && entry.manifestBytes >= VR_HEADER_BYTES + VR_SIGNATURE_BYTES && imageBytes == bytes) {{
          manifest = entry.manifest;
          manifestBytes = entry.manifestBytes;
          break;
        }}
      }}
      if (!manifest) {{
        put (snprintf (evidence + used, sizeof (evidence) - used, "w%u no policy for %llx bytes\\n",
                       index, bytes));
        failed++;
        continue;
      }}
      vr_view view = {{}};
      int opened = vr_open (manifest, manifestBytes, verified_root_key, 1, &vr_xnu_crypto, &view);
      if (opened != VR_OK) {{
        put (snprintf (evidence + used, sizeof (evidence) - used, "w%u policy open %d\\n", index, opened));
        failed++;
        continue;
      }}
      uint64_t block = 0, windowPassed = 0, windowFailed = 0;
      while (block < view.block_count) {{
        uint64_t count = view.block_count - block;
        if (count > kDataMax / 4096) count = kDataMax / 4096;
        uint64_t lba = absolute / 4096 + block;
        if (hostRead4k (0, lba, static_cast<uint32_t> (count), gScratch) != kIOReturnSuccess) {{
          windowFailed += count;
          if (!badLba) {{ badWindow = layout->window[index].name; badLba = lba; badIndex = block; badExpected = count; }}
          block += count;
          continue;
        }}
        for (uint64_t inner = 0; inner < count; ++inner) {{
          uint64_t offset = (block + inner) * 4096;
          uint64_t length = bytes - offset < 4096 ? bytes - offset : 4096;
          if (vr_verify_block (&view, block + inner, gScratch + inner * 4096,
                               static_cast<size_t> (length), &vr_xnu_crypto) == VR_OK) {{
            ++windowPassed;
          }} else {{
            ++windowFailed;
            if (!badLba) {{
              badWindow = layout->window[index].name;
              badLba = lba;
              badIndex = block + inner;
              badExpected = length;
            }}
          }}
        }}
        block += count;
      }}
      bytesDone += bytes;
      passed += windowPassed;
      failed += windowFailed;
      put (snprintf (evidence + used, sizeof (evidence) - used,
                     "w%u %s blocks=%llu ok=%llu bad=%llu\\n", index, layout->window[index].name,
                     view.block_count, windowPassed, windowFailed));
    }}
    uint64_t elapsed = timerTicks () - t0;
    panic ("NATIVE_UFS verify all\\n"
           "userdata first=%llx last=%llx windows=%u manifest=%u policies=%llu\\n"
           "%s"
           "total ok=%llu bad=%llu bytes=%llu elapsed=%llu ms\\n"
           "first bad window=%s lba=%llx index=%llu length=%llu",
           layout->userdataFirst, layout->userdataLast, layout->windows, layout->manifestStatus,
           verified_root_policy_count, evidence, passed, failed, bytesDone,
           (elapsed * 1000) / 24000000ULL, badWindow, badLba, badIndex, badExpected);
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
    if 'probe == 16 ||' not in text:
        if GATE_OLD not in text:
            sys.exit('NativeUfs.cpp mutatesTransport list changed shape')
        text = text.replace(GATE_OLD, GATE_NEW, 1)
    text = text.replace(ANCHOR, PROBE + ANCHOR, 1)
    SOURCE.write_text(text)
    print('NativeUfs.cpp: native-ufs-probe=16 full-window verification installed')


if __name__ == '__main__':
    main()
