#!/usr/bin/env python3
"""Qualify the IOMedia metaclass inside the prelinked IOStorageFamily component.

Why this exists
---------------
ShengUfs reaches IOMedia through OSDynamicCast(IOMedia, ...). That macro is

    #define OSDynamicCast(type, inst) \\
        ((type *) OSMetaClassBase::safeMetaCast((inst), OSTypeID(type)))
    #define OSTypeID(type)   (type::metaClass)

so the driver needs the *address of the storage* of `IOMedia::metaClass`, a
`const OSMetaClass * const` initialised with `&IOMedia::gMetaClass`.

IOStorageFamily is prelinked: it lives inside the kernel collection and none of
its symbols are exported by the kernel, so a plain export-table lookup cannot
find them. build-virtio-mmio-bindings.py already solves this for
IOBlockStorageDevice by qualifying the address out of the component's own
data. This script does the same for IOMedia.

How it qualifies the address
----------------------------
OSDefineMetaClassWithInit generates

    IOMedia::MetaClass IOMedia::gMetaClass;
    const OSMetaClass * const IOMedia::metaClass = &IOMedia::gMetaClass;

so inside the component's data there is a pointer slot whose value is the
address of the IOMedia metaclass object. The OSMetaClass object itself stores
its own class name at offset 24 (the layout the AES audit already pins:
superClassLink=16, className=24, classSize=32), so the metaclass is
identified by reading that string rather than by guessing.

Output: merges an "iomedias" section into build/storage-abi/interface-audit.json
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
import native_prelink as np

# Offsets inside an OSMetaClass object, pinned by scripts/audit-aes-interface.py
# (compiled_metaclass_offsets = dict(superClassLink=16, className=24, classSize=32)).
CLASS_NAME_OFFSET = 24


def main():
    kernel_path = ROOT / 'build/xnu-prelink/native-apfs-load-positive.macho'
    if not kernel_path.is_file():
        raise SystemExit(f'missing prelinked candidate: {kernel_path}')
    kernel = kernel_path.read_bytes()
    plan = json.loads(kernel_path.with_suffix('.json').read_text())
    if hashlib.sha256(kernel).hexdigest() != plan['candidate_sha256']:
        raise SystemExit('prelinked candidate does not match its plan')

    image = np.macho(kernel)
    extents = [e for e in plan['extents'] if e['module'] == 'com.apple.iokit.IOStorageFamily']
    if not extents:
        raise SystemExit('the candidate does not carry the IOStorageFamily component')
    data_extents = [e for e in extents if e['segment'] in ('__DATA_CONST', '__DATA', '__DATA_DIRTY')]
    text_extents = [e for e in extents if e['segment'] in ('__TEXT_EXEC', '__TEXT', '__DATA_CONST')]

    def read(va, length):
        at = np.backed_offset(image['segments'], va, length, len(kernel))
        return kernel[at:at + length]

    def in_extents(address, group):
        return any(e['va'] <= address < e['va'] + e['size'] for e in group)

    def read_cstring(address, limit=64):
        if not in_extents(address, text_extents + data_extents):
            return None
        try:
            blob = read(address, limit)
        except Exception:
            return None
        end = blob.find(b'\0')
        if end < 0:
            return None
        try:
            return blob[:end].decode('ascii')
        except UnicodeDecodeError:
            return None

    # Every OSMetaClass object of the component lives in its data. Each one is
    # pointed at by the `metaClass` const pointer of its own class.
    found = {}
    for extent in data_extents:
        blob = read(extent['va'], extent['size'])
        for at in range(0, len(blob) - 8, 8):
            target, = struct.unpack_from('<Q', blob, at)
            if not in_extents(target, data_extents):
                continue
            # OSMetaClass: +16 superClass, +24 className, +32 classSize
            try:
                name_pointer, = struct.unpack_from('<Q', read(target + CLASS_NAME_OFFSET, 8))
                class_size, = struct.unpack_from('<I', read(target + 32, 4))
            except Exception:
                continue
            if not name_pointer or not class_size:
                continue
            name = read_cstring(name_pointer)
            if name != 'IOMedia':
                continue
            if name in found:
                raise SystemExit(f'ambiguous IOMedia metaclass at {found[name]:#x} and {target:#x}')
            # `metaClass` is the pointer slot; `gMetaClass` is the object itself.
            found[name] = (extent['va'] + at, target)

    if 'IOMedia' not in found:
        raise SystemExit('no IOMedia metaclass found in the IOStorageFamily component')

    slot, meta = found['IOMedia']
    if struct.unpack_from('<Q', read(slot, 8))[0] != meta:
        raise SystemExit('the IOMedia metaClass slot does not point at its own metaclass')

    iomedias = {
        '__ZN6IOMedia10metaClassE': hex(slot),   # address of the const pointer storage
        '__ZN6IOMedia10gMetaClassE': hex(meta),   # address of the metaclass object
    }

    audit_path = ROOT / 'build/storage-abi/interface-audit.json'
    audit = json.loads(audit_path.read_text())
    audit['iomedias'] = iomedias
    audit_path.write_text(json.dumps(audit, indent=2) + '\n')
    for name, value in iomedias.items():
        print(f'{name} = {value}')
    print(f'merged into {audit_path}')


if __name__ == '__main__':
    main()
