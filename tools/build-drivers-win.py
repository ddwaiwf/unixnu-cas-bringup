#!/usr/bin/env python3
"""Build the cas UFS driver archives on Windows with the portable LLVM.

Uses the same compile record the framework uses (the one the XNU build emitted
for IOPlatformExpert), so the driver's flags match the kernel's exactly, but
runs the portable clang instead of the Linux toolchain and rewrites the POSIX
tree paths to UNC paths so the objects can be read straight out of the WSL tree.

Outputs (into the tree's build/ directory):
  build/sheng-ufs/NativeUfs.o + libShengUfs.a
  build/virtio-mmio-block/NativeVirtioBlock.o + libVirtioMmioBlock.a
  build/virtio-mmio-block-verified-bundle/libVirtioMmioBlock.a
"""
import json
import os
import subprocess
import sys
from pathlib import Path

CLANG = r'D:\unixnu\build\clang+llvm-20.1.8-x86_64-pc-windows-msvc\bin\clang.exe'
AR = r'D:\unixnu\build\clang+llvm-20.1.8-x86_64-pc-windows-msvc\bin\llvm-ar.exe'
POSIX = '/home/ddw/unixnu/src/UniXNU/out/cas/ipados-17.7.11-21H461/cas/tree'
UNC = '//wsl.localhost/Ubuntu-24.04' + POSIX

TREE = Path(r'\\wsl.localhost\Ubuntu-24.04\home\ddw\unixnu\src\UniXNU\out\cas\ipados-17.7.11-21H461\cas\tree')
RECORD = TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'


def fix(token):
    token = token.replace(POSIX, UNC)
    token = token.replace('/opt/unixnu-linux/llvm/bin/clang-24', CLANG)
    return token


def compile_from(record, source, destination, extra_flags, includes):
    """Compile one source with the kernel's own flags, like the framework does."""
    args = record['arguments']
    command, index = [], 0
    while index < len(args):
        value = args[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value in ('-MF', '-MJ'):
            index += 1
        elif value == '-Xclang' and args[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W') and value not in ('-MD', '-MP'):
            command.append(value)
        index += 1
    # the portable clang must not be told about Apple's SDK
    command = [fix(c) for c in command]
    command = [c for c in command if c != '-fapple-kext']
    command += list(includes) + list(extra_flags)
    # Apple clang has the XNU kalloc type traits; upstream clang does not.
    # Approximate them for the driver's own compile.
    command += ['-D__builtin_xnu_types_compatible(a,b)=__builtin_types_compatible_p(a,b)',
                '-D__builtin_xnu_type_summary(x)=0', '-D__builtin_xnu_type_signature(x)=0']
    destination.parent.mkdir(parents=True, exist_ok=True)
    cwd = record['directory'].replace(POSIX, UNC)
    result = subprocess.run(command, capture_output=True, text=True, cwd=cwd)
    Path(r'D://unixnu//build').joinpath(destination.name + '.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        print(result.stdout[-3000:] + result.stderr[-3000:])
        raise SystemExit(f'compile failed: {destination.name}')


def archive(members, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    subprocess.run([AR, 'rcs', str(out_path)] + [str(m) for m in members], check=True)


def main():
    record = json.loads(RECORD.read_text(encoding='utf-8').strip().rstrip(','))
    storage_inc = TREE / 'build/storage-abi/include'
    verified_inc = TREE / 'kits/VerifiedRoot/include'

    # --- ShengUfs / NativeUfs ---
    ufs_src = TREE / 'drivers/ShengUfs/NativeUfs.cpp'
    ufs_obj = TREE / 'build/sheng-ufs/NativeUfs.o'
    print('编译 NativeUfs.cpp ...')
    compile_from(record, ufs_src, ufs_obj,
                 extra_flags=['-DNATIVE_VERIFIED_BLOCK=1', '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1',
                              '-mgeneral-regs-only'],
                 includes=['-I', str(storage_inc), '-I', str(verified_inc),
                           '-I', str(TREE / 'drivers/VirtioMmioBlock')])
    archive([ufs_obj], TREE / 'build/sheng-ufs/libShengUfs.a')
    print(f'  -> libShengUfs.a')

    # --- VirtioMmioBlock (plain and verified bundle) ---
    v_src = TREE / 'drivers/VirtioMmioBlock/NativeVirtioBlock.cpp'
    for out_dir, flags in (('build/virtio-mmio-block', []),
                           ('build/virtio-mmio-block-verified-bundle',
                            ['-DNATIVE_VERIFIED_BLOCK=1', '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1'])):
        obj = TREE / out_dir / 'NativeVirtioBlock.o'
        print(f'编译 NativeVirtioBlock.cpp -> {out_dir} ...')
        compile_from(record, v_src, obj, extra_flags=flags,
                     includes=['-I', str(storage_inc), '-I', str(verified_inc)])
        archive([obj], TREE / out_dir / 'libVirtioMmioBlock.a')
        print(f'  -> libVirtioMmioBlock.a')


if __name__ == '__main__':
    main()
