# `cas-upstream.dsc`: boot DTB has no `linux,initrd-start` / `linux,initrd-end`, so `CasSetExistingInitrd()` always fails

**Component:** `uefi-cas` — `Platforms/Xiaomi/casPkg/Include/CasSharedInitrd.h` + the boot DTB used by `cas`
**Observed on:** Xiaomi 10 Ultra (`cas`, SM8250), stock `boot.img` DTB index 2, `cas-upstream.dsc`
**Status:** confirmed on hardware, fix verified

---

## Symptom

With `cas-upstream.dsc`, SEC stops at stage 4 (`CasStageSec`) with
`0x8000000000000021` (`EFI_COMPROMISED_DATA`) — the same code the companion issue
produces, which makes the two very easy to confuse. Fixing the call order alone does not
help: the next thing `CasPrepareSharedInitrd()` does still fails.

## Root cause

`CasPrepareSharedInitrd()` is supposed to publish the payload location into the device
tree so the UniXNU ramdisk driver and the kernel can find it:

```c
STATIC BOOLEAN CasSetExistingInitrd (VOID *Fdt, INT32 Node, CONST CHAR8 *Name, UINT64 Address) {
  INT32 Length;
  CONST VOID *Old = FdtGetProp (Fdt, Node, Name, &Length);
  UINT32 Value32;
  UINT64 Value64;
  if (Old == NULL) { return FALSE; }        // ← only updates, never creates
  ...
}
```

It **only rewrites an existing property**. The assumption is that the bootloader
(U-Boot and friends) already put `linux,initrd-start` / `linux,initrd-end` into `/chosen`.

The stock Xiaomi DTB does not. Dumping `/chosen` from the DTB inside the retail
`boot.img` (Xiaomi 10 Ultra, HyperOS / iPadOS-17.7.11-21H461 restore image, FDT index 2)
gives exactly one property:

```
/chosen 属性: bootargs  (48 bytes) = "rcupdate.rcu_expedited=1 rcu_nocbs=0-7 kpti=off"
```

`FdtGetProp` therefore returns `NULL` for both initrd properties,
`CasSetExistingInitrd()` returns `FALSE`, and SEC stops.

Note this is the **boot DTB handed over by ABL**, not `dts/cas.dts` — `cas.dts` only feeds
the ADT / device-tree build for the kernel, so editing it does not help here.

## Fix

Insert the two properties into `/chosen` of the DTB that goes into the boot image,
before packing. They only need to exist; SEC overwrites them with the real values
(`CAS_PAYLOAD_BASE` and `CAS_PAYLOAD_BASE + InitrdSize`), and it needs them to keep the
same length so `FdtTotalSize()` does not change.

Minimal, no-dtc patcher (structural insert only, no decompile round-trip):

```python
# find the FDT_BEGIN_NODE "chosen" inside the structure block, insert two FDT_PROP
# entries right after its name:
#   linux,initrd-start = <0x0 0xa4000000>   (8 bytes)
#   linux,initrd-end   = <0x0 0xa4000000>   (8 bytes)
# append the two property names to the strings block, then fix up
# totalsize / off_dt_strings / size_dt_struct / size_dt_strings.
```

Result on the `cas` DTB:

```
FDT[2] 470368 → 470444 bytes (+76) → padded to 478636
/chosen:
   linux,initrd-start   len=8   00000000a4000000
   linux,initrd-end     len=8   00000000a4000000
   bootargs             len=48  rcupdate.rcu_expedited=1 ...
```

The patched blob must still walk cleanly to `FDT_END` with depth returning to 0, and
`(off_dt_struct + size_dt_struct) == off_dt_strings` must hold.

A working implementation is in this repository's companion build tree
(`tools/patch_dtb_initrd.py`).

## Verification

With both this and the call-order fix applied, the stage bar passes `Sec` and reaches
`CasStageKeys` (10/12) with no fault.

## Suggested upstream change

Pick one:

1. **Create instead of update** — in `CasPrepareSharedInitrd()`, when
   `FdtGetProp()` returns `NULL`, call `FdtSetProp()` to append a fresh property.
   This makes the profile work on any bootloader, and is the smallest change.
2. **Ship the properties in the shipped DTB** — patch `binaries`/`Resources` so the
   `cas` boot DTB already carries them, and document that requirement next to
   `cas-upstream.dsc`.
3. **Drop the FDT hand-off entirely** — read the payload window from `CAS_BOOT_INFO`
   (`Info->InitrdCopy` / `Info->InitrdSize`) wherever it is needed, instead of
   round-tripping it through `/chosen`.

Option 1 is probably the right long-term answer: nothing in the platform contract says
the bootloader must pre-populate `linux,initrd-*`.
