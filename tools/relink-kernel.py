#!/usr/bin/env python3
"""Relink the cas kernel on Windows with ld64.lld, from the recorded link inputs.

The WSL build tree records everything the linker needs:
  mach.development.bcm2837.link/mach.development.bcm2837.linkarguments
  mach.development.bcm2837.link/mach.development.bcm2837.linkarchives
  link.filelist

Those files hold POSIX paths; they are rewritten to UNC (//wsl.localhost/...) so
the Windows linker can read the objects straight out of the WSL tree, with no
copying. /dev/null (used by -sectcreate) becomes an empty local file.

Usage:
  python3 relink-kernel.py                 # link to build/kernel.relinked.macho
  python3 relink-kernel.py --extra "-Wl,-force_load,W:/path/libFoo.a"
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

CLANG = r'D:\unixnu\build\clang+llvm-20.1.8-x86_64-pc-windows-msvc\bin\clang.exe'
WSL = r'//wsl.localhost/Ubuntu-24.04'
TREE_POSIX = '/home/ddw/unixnu/src/UniXNU/out/cas/ipados-17.7.11-21H461/cas/tree'
TREE_UNC = WSL + '/home/ddw/unixnu/src/UniXNU/out/cas/ipados-17.7.11-21H461/cas/tree'
OBJ = 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extra', action='append', default=[],
                        help='extra argument for the linker (repeatable)')
    parser.add_argument('--output', default=r'D:\unixnu\build\kernel.relinked.macho')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    base = Path(WSL + TREE_POSIX.replace('//wsl.localhost/Ubuntu-24.04', '')) / OBJ
    args_file = base / 'mach.development.bcm2837.link' / 'mach.development.bcm2837.linkarguments'
    archives_file = base / 'mach.development.bcm2837.link' / 'mach.development.bcm2837.linkarchives'
    filelist = base / 'link.filelist'

    link_arguments = args_file.read_text(encoding='utf-8').split()
    link_archives = archives_file.read_text(encoding='utf-8').split()
    objects = [line.strip() for line in filelist.read_text(encoding='utf-8').splitlines() if line.strip()]
    # The recorded filelist omits the top-level family objects that the original
    # link passed directly: version.o carries osversion/ostype,
    # lastkernelconstructor.o carries last_kernel_symbol, and nonlto.o is the
    # non-LTO placeholder the kernel build always links.
    prefix = '/home/ddw/unixnu/src/UniXNU/out/cas/ipados-17.7.11-21H461/cas/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/'
    objects += [prefix + name for name in (
        'version.o', 'lastkernelconstructor.o', 'lastkerneldataconst.o', 'nonlto.o')]

    empty = Path(r'D:\unixnu\build\empty.bin')
    empty.write_bytes(b'')

    def fix(token):
        token = token.replace(TREE_POSIX, TREE_UNC)
        if '/dev/null' in token:
            # -sectcreate takes the empty section source inside the same token
            token = token.replace('/dev/null', str(empty))
        return token

    # The recorded arguments use the clang driver's -Wl, convention, so the
    # driver has to be clang (it forwards -Wl,X to ld64.lld as X).
    command = [CLANG, '--target=arm64-apple-ios17.0', '-fuse-ld=lld', '-Wl,--error-limit=0']
    command += [fix(t) for t in link_arguments]
    command += [fix(t) for t in link_archives]
    command += list(args.extra)

    # the object list goes into a response file so the command line stays short
    local_filelist = Path(r'D:\unixnu\build\link.filelist.win')
    local_filelist.write_text('\n'.join(fix(o) for o in objects) + '\n', encoding='utf-8')
    command += ['-filelist', str(local_filelist), '-o', args.output]
    # XNU's kernel build passes the generated alias list: the "_external"
    # implementations also answer to the plain kernel-internal names
    # (vm_map_create, kalloc_type_impl, mach_vm_remap, vm_fault).
    alias_list = base / 'all-alias.exp'
    if alias_list.is_file():
        # ld64 creates these aliases with -alias_list; ld64.lld has no such
        # option, so the same aliases are produced with the Mach-O assembler
        # instead: ".globl alias" + ".set alias, real" makes the alias share the
        # real symbol's final address, exactly as the existing kernel image
        # shows for _kalloc_type_impl / _kalloc_type_impl_external.
        local_alias = Path(r'D:\unixnu\build\all-alias.exp')
        local_alias.write_bytes(alias_list.read_bytes())
        lines = []
        for line in local_alias.read_text(encoding='utf-8').splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            real, alias = parts
            lines.append(f'.globl {alias}\n.set {alias}, {real}\n')
        asm = Path(r'D:\unixnu\build\kernel-aliases.S')
        asm.write_text(''.join(lines), encoding='utf-8')
        obj = Path(r'D:\unixnu\build\kernel-aliases.o')
        subprocess.run([CLANG, '--target=arm64-apple-ios17.0', '-c', str(asm), '-o', str(obj)],
                       check=True)
        print(f'别名对象: {len(lines)} 个别名 -> {obj.name}')
        command += [str(obj)]

    print(f'输入: {len(link_arguments)} 参数, {len(link_archives)} 归档项, {len(objects)} 目标文件')
    if args.dry_run:
        print(' '.join(command[:40]), '...')
        return
    result = subprocess.run(command, capture_output=True, text=True)
    out = (result.stdout or '') + (result.stderr or '')
    if out.strip():
        print(out[-4000:])
    print(f'退出码 {result.returncode}')
    if Path(args.output).exists():
        print(f'产出: {args.output}  {Path(args.output).stat().st_size} 字节')
    return result.returncode


if __name__ == '__main__':
    sys.exit(main() or 0)
