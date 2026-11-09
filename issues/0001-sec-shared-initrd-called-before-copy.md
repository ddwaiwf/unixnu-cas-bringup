# SEC: `CasPrepareSharedInitrd()` is called before the payload is copied into `CAS_PAYLOAD_BASE`

**Component:** `uefi-cas` — `Platforms/Xiaomi/casPkg/Sec/Sec.c` (only built with `CAS_UPSTREAM_PROFILE`)
**Observed on:** Xiaomi 10 Ultra (`cas`, SM8250), `cas-upstream.dsc` / `cas-upstream.fdf`, build `cas-upstream-002`
**Status:** confirmed on hardware, fix verified to move the boot past SEC

---

## Symptom

With the standard upstream profile (`-p casPkg/cas-upstream.dsc`), the device boots the
bounded shim, copies the FD and the FDT, enters SEC — and then stalls with the stage bar
showing **stage 4 (`CasStageSec`)** plus a fault bar carrying

```
0x8000000000000021          (EFI_COMPROMISED_DATA)
```

No UART is wired on this phone, so this display fault code is the only signal we get.

## Root cause

`Sec.c` calls `CasPrepareSharedInitrd()` **15 lines before** the initrd is copied to its
destination:

```c
 62:  if (!CasPrepareSharedInitrd ((VOID *)FdtAddress, Info)) { Stop (EFI_COMPROMISED_DATA); }
 ...
 75:      InvalidateDataCacheRange ((VOID *)(UINTN)CAS_PAYLOAD_BASE, ALIGN_VALUE ((UINTN)Info->InitrdSize, 64));
 77:      CopyMem ((VOID *)(UINTN)CAS_PAYLOAD_BASE, (VOID *)(UINTN)Info->OriginalInitrd, (UINTN)Info->InitrdSize);
```

and `CasPrepareSharedInitrd()` starts by validating the payload's FAT boot signature at
that very address (`Platforms/Xiaomi/casPkg/Include/CasSharedInitrd.h`):

```c
UINT8 CONST *Fat = (VOID *)(UINTN)CAS_PAYLOAD_BASE;
if (Info->InitrdCopy != CAS_PAYLOAD_BASE || Info->InitrdSize < 512 ||
    Info->InitrdSize > CAS_PAYLOAD_SIZE || Info->InitrdSize % 512 != 0 ||
    Fat[510] != 0x55 || Fat[511] != 0xaa) { return FALSE; }
```

At line 62 `CAS_PAYLOAD_BASE` still holds whatever ABL left there, so `Fat[510..511]` is
never `0x55 0xaa` and the function returns `FALSE` unconditionally. `SEC` then stops with
`EFI_COMPROMISED_DATA` and dead-loops.

Because the whole block is inside `#ifdef CAS_UPSTREAM_PROFILE`, the legacy device profile
(`cas.dsc`) never executes it — which is why this has not been noticed. The device record
already says the standard profile "has not been booted on the phone yet".

## Fix

Move the call below the copy (and below the cache invalidate):

```c
  if (Info->InitrdSize != 0) {
    InvalidateDataCacheRange ((VOID *)(UINTN)CAS_PAYLOAD_BASE, ALIGN_VALUE ((UINTN)Info->InitrdSize, 64));
    // EDK II CopyMem explicitly supports overlapping ranges.
    CopyMem ((VOID *)(UINTN)CAS_PAYLOAD_BASE, (VOID *)(UINTN)Info->OriginalInitrd, (UINTN)Info->InitrdSize);
  }
#ifdef CAS_UPSTREAM_PROFILE
  if (!CasPrepareSharedInitrd ((VOID *)FdtAddress, Info)) { Stop (EFI_COMPROMISED_DATA); }
#endif
```

Note this is **not sufficient on its own** — see the companion issue about
`linux,initrd-start` / `linux,initrd-end` missing from `/chosen`. Both must be fixed
before SEC passes.

## Verification

After both fixes, the stage bar on a Xiaomi 10 Ultra advances past `Sec` and reaches
`CasStageKeys` (10 of 12) with no fault:

```
1 ShimValidated ✅  2 FdCopied ✅  3 FdtCopied ✅  4 Sec ✅
5 Mmu ✅  6 Hobs ✅  7 Decompressed ✅  8 Dxe ✅  9 Gop ✅  10 Keys ✅
```

Firmware image: `SILICIUM_UEFI.fd`, exactly 8,388,608 bytes, `cas-upstream-002` PCDs.
Booted with `fastboot boot` (memory only, no partition written).

## Suggested upstream change

1. Move the `CasPrepareSharedInitrd()` call after the `CopyMem()` (and after the
   `InvalidateDataCacheRange()`), or
2. Drop the FAT-signature probe from `CasPrepareSharedInitrd()` and validate the payload
   once, right after the copy, so the ordering dependency cannot reappear.
