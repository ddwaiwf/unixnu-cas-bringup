#!/usr/bin/env python3
"""Fill missing original-kernel link sources for a driver object.

Background
----------
ShengUfs / VirtioMmioBlock / RamBlock subclass IOBlockStorageDevice and use
IOMedia via OSDynamicCast. IOStorageFamily is *prelinked* (stays in the kernel
collection), so none of its symbols are linkable at kernel link time. The
kernel link therefore fails with undefined:

    __ZN6IOMedia10gMetaClassE            (IOMedia::gMetaClass)
    __ZN20IOBlockStorageDevice10gMetaClassE
    __ZN20IOBlockStorageDeviceC2EPK11OSMetaClass
    ...

build-virtio-mmio-bindings.py already covers the IOBlockStorageDevice family,
but NOT IOMedia. This tool closes that gap generically: it scans a compiled
object's undefined symbols, looks each one up in the prelinked kernel's symbol
table, and emits a `.globl/.set` absolute-address binding object (the same
pattern as scripts/data/native-aes-original-bindings.S).

Run it on the Mac, inside the build tree, after the kernel candidate exists.

  python3 fill-missing-original-bindings.py \\
      --tree /path/to/UniXNU-tree \\
      --object build/sheng-ufs/NativeUfs.o \\
      --out-dir build/sheng-ufs-extra-bindings \\
      --archive build/sheng-ufs/libShengUfs.a      # append (never overwrite)

The binding addresses belong to the kernelcache pinned in board.toml. If the
kernel candidate changes, re-run audit-storage-interface.py first.
"""
import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'kits/XnuPrelink'))
try:
    import native_prelink as np
except ImportError:
    sys.path.insert(0, '/d/unixnu/src/ref/XnuPrelink')
    import native_prelink as np

# Only these original kext namespaces are allowed to be bound. Anything else
# that is undefined is a real bug and must stay a link error.
ALLOWED_PREFIXES = (
    '__ZN6IOMedia',            # IOMedia::
    '__ZN20IOBlockStorageDevice',  # IOBlockStorageDevice::
    '__ZTV20IOBlockStorageDevice',
    '__ZN22IOBlockStorageDriver',  # IOBlockStorageDriver::
    '__ZN18IOMediaBSDClient',      # IOMediaBSDClient::
    '__ZN16IOStorageFamily',
)


def undefined_symbols(obj_path: Path):
    """Return {name: kind} for undefined (external, no value) symbols."""
    data = obj_path.read_bytes()
    image = np.macho(data)
    cmd = next(at for c, at, ln in image['commands'] if c == 2)
    symoff, count, stroff, strsize = struct.unpack_from('<4I', data, cmd + 8)
    strings = data[stroff:stroff + strsize]
    out = {}
    for i in range(count):
        strx, kind, sect, desc, value = struct.unpack_from('<IBBHQ', data, symoff + i * 16)
        # undefined external: N_UND(0x0) with N_EXT(0x01)
        if (kind & 0x0e) == 0 and (kind & 0x01):
            end = strings.find(b'\0', strx)
            name = strings[strx:end].decode()
            out[name] = kind
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--tree', type=Path, required=True,
                   help='build tree root (the directory that contains build/ and kits/)')
    p.add_argument('--object', type=Path, action='append', required=True,
                   help='compiled driver object (repeatable)')
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--archive', type=Path,
                   help='archive to append original-bindings.o to (never overwritten)')
    p.add_argument('--kernel', type=Path,
                   help='prelinked kernel candidate (default: the usual tree path)')
    p.add_argument('--allow-extra', action='append', default=[],
                   help='additional symbol-name prefix to bind (repeatable)')
    args = p.parse_args()

    kernel_path = args.kernel or (args.tree / 'build/xnu-prelink/native-defaults-candidate.macho')
    if not kernel_path.is_file():
        # the storage audit pins a different candidate name; accept either
        alt = args.tree / 'build/xnu-prelink/native-apfs-load-positive.macho'
        if alt.is_file():
            kernel_path = alt
        else:
            raise SystemExit(f'prelinked kernel candidate not found: {kernel_path}')

    kernel = kernel_path.read_bytes()
    image = np.macho(kernel)
    by_name = {}
    for address, names in np.exports(kernel, image).items():
        for name in names:
            by_name[name] = address

    allowed = ALLOWED_PREFIXES + tuple(args.allow_extra)
    bindings = {}
    skipped = []
    for obj_path in args.object:
        for name in sorted(undefined_symbols(obj_path)):
            if not name.startswith(allowed):
                skipped.append(name)
                continue
            if name not in by_name:
                skipped.append(name + ' (not in prelinked kernel)')
                continue
            bindings[name] = by_name[name]

    if skipped:
        print('NOT bound (still undefined, or not an allowed original):')
        for name in skipped:
            print('   ', name)
    if not bindings:
        raise SystemExit('No bindable original symbols found — nothing to do')

    args.out_dir.mkdir(parents=True, exist_ok=True)
    asm = args.out_dir / 'original-bindings-extra.S'
    asm.write_text(''.join(f'.globl {n}\n.set {n}, {v:#x}\n' for n, v in sorted(bindings.items())))
    obj = args.out_dir / 'original-bindings-extra.o'
    subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-Werror',
                    '-Wno-incompatible-sysroot', '-c', str(asm), '-o', str(obj)], check=True)

    print(f'bound {len(bindings)} original symbols from {kernel_path.name}:')
    for name, value in sorted(bindings.items()):
        print(f'   {name} = {value:#x}')

    if args.archive:
        if not args.archive.is_file():
            raise SystemExit(f'archive not found: {args.archive}')
        # ar rcs APPENDS members; it never replaces the archive wholesale.
        subprocess.run(['xcrun', 'ar', 'rcs', str(args.archive), str(obj)], check=True)
        print(f'appended {obj.name} to {args.archive}')

    report = dict(kernel=kernel_path.name,
                  kernel_sha256=__import__('hashlib').sha256(kernel).hexdigest(),
                  objects=[str(o) for o in args.object],
                  bindings={n: hex(v) for n, v in bindings.items()},
                  skipped=skipped)
    (args.out_dir / 'extra-bindings.json').write_text(json.dumps(report, indent=2) + '\n')
    print('wrote', args.out_dir / 'extra-bindings.json')


if __name__ == '__main__':
    main()
