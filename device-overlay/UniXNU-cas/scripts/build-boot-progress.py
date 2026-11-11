#!/usr/bin/env python3
"""Build the opt-in board diagnostic against this source kernel's actual ABI.

This is build-boot-progress.py of UniXNU-sheng with the board coupling removed.
The sources are the device package's copy (package/BootProgress.c and its eight
companions), not a shared kit, and the one UEFI header the display layer needs
(Font.h) comes from this board's Mu-Silicium checkout, which the driver passes
as UNIXNU_UEFI, rather than from a sibling repository's fixed path.
"""
import hashlib
import json
import os
import pathlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise SystemExit(message)


OUT = ROOT / 'build/boot-progress'
OUT.mkdir(parents=True, exist_ok=True)
# The device package is linked into the tree as kits/<kit> (see scripts/unixnu/tree.py), so locate it the way
# build-platform-power.py does instead of assuming a fixed directory name.
sources = sorted(ROOT.glob('kits/*/BootProgress.c'))
require(len(sources) == 1, 'Expected one device package with BootProgress.c')
PACKAGE = sources[0].parent


def font_dir():
    """The Mu-Silicium checkout this board builds its firmware from.

    BootProgressDisplay.h includes Font.h, which belongs to the UEFI tree rather than to XNU. The driver passes
    that checkout as UNIXNU_UEFI; without it, find the sibling checkout by walking up from the tree, whose depth
    depends on the layout (out/<board>/tree/ before the migration, out/<device>/<os_profile>/<config>/tree/ after).
    """
    candidates = [os.environ.get('UNIXNU_UEFI')]
    candidates += [str(parent / 'uefi-alioth') for parent in ROOT.parents]
    for candidate in candidates:
        if not candidate:
            continue
        directory = Path(candidate) / 'Silicon/Silicium/SiliciumPkg/Library/FrameBufferSerialPortLib'
        if (directory / 'Font.h').is_file():
            return directory
    raise SystemExit('BootProgressDisplay.h includes Font.h from the Mu-Silicium checkout; run with UNIXNU_UEFI set')


record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json').read_text().strip().rstrip(','))
source = PACKAGE / 'BootProgress.c'
# Compile from a private copy on local disk. Reading straight off the virtiofs mount makes
# clang-24 abort with `MemoryBuffer::init: Buffer is not null terminated` when the shared
# directory's cached size and content disagree for a moment, which happens while this workspace is
# edited from the other side. The copy pins one consistent inode; the hash check proves it is the
# same file. build-gop-legacy-display.py does the same for the same reason.
import hashlib
import shutil
import tempfile
def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
_stage = pathlib.Path(tempfile.mkdtemp(prefix='unixnu-boot-progress-'))
_staged = _stage / 'BootProgress.c'
shutil.copyfile(source, _staged)
# BootProgress.c includes its own headers from the package directory, so the copy has to bring
# them or the compile fails on the first include.
for _header in ('BootProgressCore.h', 'BootProgressDisplay.h', 'BootProgressLifecycle.h',
                'BootProgressPulse.h', 'BootResetCore.h'):
    _from = source.parent / _header
    if _from.is_file():
        shutil.copyfile(_from, _stage / _header)
if _sha(_staged) != _sha(source):
    raise SystemExit(f'{source} changed while it was copied for compilation; rerun the build')
source = _staged
obj = OUT / 'BootProgress.o'
command = []
args = record['arguments']
i = 0
while i < len(args):
    arg = args[i]
    if arg == record['file']:
        command.append(str(source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o':
            command += ['-o', str(obj)]
        i += 1
    elif arg == '-Xclang' and args[i+1].startswith('-W'):
        i += 1
    elif arg == '-D' and args[i+1].startswith('XNU_BOOT_PROGRESS='):
        i += 1
    elif not arg.startswith(('-W', '-DXNU_BOOT_PROGRESS=')):
        command.append(arg)
    i += 1
command += ['-DXNU_BOOT_PROGRESS=1', '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror',
            '-I', str(ROOT / '.cache/xnu-work/osfmk'), '-I', str(PACKAGE), '-I', str(font_dir())]
subprocess.run(command, cwd=record['directory'], check=True, timeout=90)
(OUT / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
fault_source = PACKAGE / 'BootFaultProbe.c'
fault_obj = OUT / 'BootFaultProbe.o'
fault_command = [str(fault_source) if arg == str(source) else str(fault_obj) if arg == str(obj) else arg for arg in command]
subprocess.run(fault_command, cwd=record['directory'], check=True, timeout=90)
policy_source = PACKAGE / 'BootWatchdogPolicy.c'
policy_obj = OUT / 'BootWatchdogPolicy.o'
policy_record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o.json').read_text().strip().rstrip(','))
policy_command = []
policy_args = policy_record['arguments']
j = 0
while j < len(policy_args):
    arg = policy_args[j]
    if arg == policy_record['file']:
        policy_command.append(str(policy_source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o':
            policy_command += ['-o', str(policy_obj)]
        j += 1
    elif arg == '-Xclang' and policy_args[j+1].startswith('-W'):
        j += 1
    elif not arg.startswith('-W'):
        policy_command.append(arg)
    j += 1
policy_command += ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror', '-I', str(PACKAGE)]
subprocess.run(policy_command, cwd=policy_record['directory'], check=True, timeout=90)
# The diagnostic file export (BootDiagExport.c) reads files through the VFS: the same BSD-side flags.
diag_source = PACKAGE / 'BootDiagExport.c'
diag_obj = OUT / 'BootDiagExport.o'
diag_command = [str(diag_source) if arg == str(policy_source) else str(diag_obj) if arg == str(policy_obj) else arg for arg in policy_command]
subprocess.run(diag_command, cwd=policy_record['directory'], check=True, timeout=90)
# The firmware file reader of the audio DSP bring-up (AdspProbe.cpp) uses the VFS too.
fw_source = PACKAGE / 'AdspFirmware.c'
fw_obj = OUT / 'AdspFirmware.o'
fw_command = [str(fw_source) if arg == str(policy_source) else str(fw_obj) if arg == str(policy_obj) else arg for arg in policy_command]
subprocess.run(fw_command, cwd=policy_record['directory'], check=True, timeout=90)

# cas: BootLog.c reaches the board only through weak imports; BootReset.c is alioth's own
# (IMEM, not the SPMI arbiter) but compiles untouched for the same SM8250 kernel train.
log_source = PACKAGE / 'BootLog.c'
log_obj = OUT / 'BootLog.o'
log_command = [str(log_source) if arg == str(source) else str(log_obj) if arg == str(obj) else arg for arg in command]
subprocess.run(log_command, cwd=record['directory'], check=True, timeout=90)
reset_source = PACKAGE / 'BootReset.c'
reset_obj = OUT / 'BootReset.o'
reset_command = [str(reset_source) if arg == str(source) else str(reset_obj) if arg == str(obj) else arg for arg in command]
subprocess.run(reset_command, cwd=record['directory'], check=True, timeout=90)

# cas: the board lacks real providers (touch, virtio, USB console, UFS, legacy display, owner
# data) for many weak_import diagnostics that BootProgress.c/BootLog.c reference. A static
# kernel link refuses to leave any of them undefined, so generate the missing definitions
# from the package's own extern declarations: zero-initialised globals and no-op functions.
# alioth's BootProgressStubs.c is compiled as-is first; anything it does not cover is added
# by the generated cas stubs below (nm over the assembled objects decides what is missing).
stubs_source = PACKAGE / 'BootProgressStubs.c'
stubs_obj = OUT / 'BootProgressStubs.o'
stubs_command = [str(stubs_source) if arg == str(source) else str(stubs_obj) if arg == str(obj) else arg for arg in command]
subprocess.run(stubs_command, cwd=record['directory'], check=True, timeout=90)

import re as _re
nm = '/opt/homebrew/opt/llvm/bin/llvm-nm'
compiled = [obj, fault_obj, policy_obj, diag_obj, fw_obj, log_obj, reset_obj, stubs_obj]
defined = set()
for member in compiled:
    out = subprocess.run([nm, member], capture_output=True, text=True, check=True).stdout
    for match in _re.finditer(r'^[0-9a-fA-F]+ [A-Za-z] (\S+)$', out, _re.M):
        # C symbols carry a leading underscore in the Mach-O symbol table; the parsed
        # extern names do not, so normalise both sides or nothing ever matches.
        defined.add(match.group(1).lstrip('_'))
# cas: the storage drivers linked later by the baseline (force_load) define the real
# native_ufs_*/native_virtio_* diagnostics themselves. The kernel link refuses duplicate
# strong definitions, so anything those archives provide must NOT be stubbed here.
# component:ShengUfs / component:VirtioMmioBlock run earlier in the board archives list,
# so the driver archives already exist at this point.
for driver_archive in (ROOT / 'build/sheng-ufs/libShengUfs.a',
                       ROOT / 'build/virtio-mmio-block/libVirtioMmioBlock.a',
                       ROOT / 'build/virtio-mmio-block-verified-bundle/libVirtioMmioBlock.a'):
    if not driver_archive.exists():
        continue
    out = subprocess.run([nm, driver_archive], capture_output=True, text=True, check=True).stdout
    for match in _re.finditer(r'^[0-9a-fA-F]+ [A-Za-z] (\S+)$', out, _re.M):
        # C symbols carry a leading underscore in the Mach-O symbol table; the parsed
        # extern names do not, so normalise both sides or nothing ever matches.
        defined.add(match.group(1).lstrip('_'))

# Defined by the kernel core itself (patches/xnu/offline-owner-root-binding-audit.patch
# puts it in bsd_init.c); the boot-progress kit only reads it, so stubbing it here
# collides with bsd_init.o at link time.
CAS_KERNEL_DEFINED = {'native_owner_root_accepted'}

CAS_LINK_MISSING = {
    'native_gop_failures', 'native_gop_ready', 'native_gop_transfers', 'native_gpu_rows',
    'native_hid_class_sizes', 'native_hid_input_meta_status', 'native_hid_load_status',
    'native_hid_matching_load_status', 'native_hid_stage', 'native_hid_touch_status',
    'native_legacy_gop_height', 'native_legacy_gop_width', 'native_legacy_presented_generation',
    'native_legacy_retired_frames', 'native_legacy_surfaces', 'native_owner_data_readonly',
    'native_owner_data_stage', 'native_owner_data_status', 'native_owner_root_accepted',
    'native_touch_fail_step', 'native_touch_frame_raw', 'native_touch_frames',
    'native_touch_hid_reports', 'native_touch_irq_level', 'native_touch_irq_low',
    'native_touch_last_x', 'native_touch_last_y', 'native_touch_valid',
    'native_ufs_block_failures', 'native_ufs_block_reads', 'native_ufs_blocks_published',
    'native_ufs_export_blocks', 'native_ufs_export_write', 'native_ufs_integrity_failures',
    'native_ufs_log_open', 'native_ufs_log_read', 'native_ufs_log_read_bytes',
    'native_ufs_log_sync', 'native_ufs_log_write', 'native_ufs_progress_text',
    'native_ufs_store_gpt_check', 'native_ufs_verified_blocks', 'native_virtio_read_failures',
    'native_virtio_ready', 'native_virtio_read_requests',
    'qcom_usb_debug_write', 'qcom_usb_debug_get_status',
    'native_virtio_verified_blocks', 'native_ufs_verify_ticks', 'native_ufs_read_ticks',
    'native_ufs_probe_calls', 'native_ufs_matched', 'native_ufs_root_step',
    'native_ufs_root_failure', 'native_ram_block_sparse',
    'native_ram_block_bytes', 'native_ram_block_resident_bytes',
}
variables = {}
functions = {}
for path in sorted(PACKAGE.glob('*.c')):
    text = path.read_text()
    for statement in _re.finditer(r'extern\s+([^;]+);', text):
        inner = statement.group(1).split('__attribute__')[0].strip()
        func = _re.match(r'(.+?)\b([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)$', inner, _re.S)
        if func:
            functions[func.group(2)] = (func.group(1).strip(), func.group(3).strip())
            continue
        pieces = [piece.strip() for piece in inner.split(',')]
        shared = None
        for piece in pieces:
            match = _re.search(r'([A-Za-z_][A-Za-z0-9_]*(\[[0-9]*\])*)$', piece)
            if not match or not match.group(1):
                continue
            name = _re.sub(r'(\[[0-9]*\])+$', '', match.group(1))
            suffix = (match.group(2) or '')
            if shared is None:
                shared = piece[:match.start()].strip()
            variables.setdefault(name, (shared, name + suffix))

globals_lines = ['#include <stdint.h>', '',
                 '/* cas: zero-initialised stubs for the weak_import diagnostics of drivers this',
                 ' * board does not build (touch, virtio, UFS, legacy display, owner data). Types',
                 ' * come from the weak_import extern declarations in the package sources. */', '']
CAS_GLOBAL_OVERRIDES = {'native_gpu_rows': 'char native_gpu_rows[8][120];'}
pending_globals = 0
for name in sorted(variables):
    if name not in CAS_LINK_MISSING or name in CAS_KERNEL_DEFINED:
        continue
    if name in defined:
        continue
    base, use = variables[name]
    if 'struct' in base or 'union' in base or 'enum' in base or '(' in base:
        continue
    if name in CAS_GLOBAL_OVERRIDES:
        globals_lines.append(CAS_GLOBAL_OVERRIDES[name])
    else:
        globals_lines.append(f'{base} {use};')
    pending_globals += 1
cas_globals_obj = None
if pending_globals:
    cas_globals_source = OUT / 'cas-globals-stubs.c'
    cas_globals_source.write_text('\n'.join(globals_lines) + '\n')
    cas_globals_obj = OUT / 'cas-globals-stubs.o'
    globals_compile = []
    swapped_source = swapped_output = False
    for arg in stubs_command:
        if arg == str(stubs_source) and not swapped_source:
            globals_compile.append(str(cas_globals_source)); swapped_source = True
        elif arg == str(stubs_obj) and not swapped_output:
            globals_compile.append(str(cas_globals_obj)); swapped_output = True
        elif arg in ('-Wextra', '-Werror'):
            continue
        else:
            globals_compile.append(arg)
    subprocess.run(globals_compile, cwd=record['directory'], check=True, timeout=90)

_keywords = {'void', 'char', 'short', 'int', 'long', 'unsigned', 'signed', 'const',
             'volatile', 'struct', 'union', 'enum', 'float', 'double',
             'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t', 'int8_t', 'int16_t',
             'int32_t', 'int64_t', 'size_t', 'uintptr_t', 'intptr_t', 'boolean_t',
             'kern_return_t', 'errno_t', 'vm_offset_t', 'vm_size_t', 'user_addr_t'}
function_lines = ['#include <stdint.h>', '#include <stddef.h>', '']
pending_functions = 0
for name in sorted(functions):
    if name not in CAS_LINK_MISSING or name in CAS_KERNEL_DEFINED:
        continue
    if name in defined:
        continue
    ret, args = functions[name]
    named = []
    if args.strip() != 'void':
        for index, piece in enumerate([piece.strip() for piece in args.split(',') if piece.strip()]):
            match = _re.search(r'([A-Za-z_][A-Za-z0-9_]*)$', piece)
            if not match or match.group(1) in _keywords or piece.endswith('*'):
                named.append(f'{piece} arg{index}')
            else:
                named.append(piece)
    body = '' if ret == 'void' else ' return 0;'
    function_lines.append(f'{ret} {name}({", ".join(named)}) {{{body} }}')
    pending_functions += 1
cas_stub_obj = None
if pending_functions:
    cas_stub_source = OUT / 'cas-function-stubs.c'
    cas_stub_source.write_text('\n'.join(function_lines) + '\n')
    cas_stub_obj = OUT / 'cas-function-stubs.o'
    stub_compile = []
    swapped_source = swapped_output = False
    for arg in stubs_command:
        if arg == str(stubs_source) and not swapped_source:
            stub_compile.append(str(cas_stub_source)); swapped_source = True
        elif arg == str(stubs_obj) and not swapped_output:
            stub_compile.append(str(cas_stub_obj)); swapped_output = True
        elif arg in ('-Wextra', '-Werror'):
            continue
        else:
            stub_compile.append(arg)
    subprocess.run(stub_compile, cwd=record['directory'], check=True, timeout=90)

archive = OUT / 'libBootProgress.a'
archive.unlink(missing_ok=True)
members = [str(member) for member in compiled]
if cas_globals_obj:
    members.append(str(cas_globals_obj))
if cas_stub_obj:
    members.append(str(cas_stub_obj))
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), *members], check=True, timeout=30)
(OUT / 'build-report.json').write_text(json.dumps({
    'watchdog_policy_sha256': hashlib.sha256(policy_source.read_bytes()).hexdigest(),
    'diag_sha256': hashlib.sha256(diag_source.read_bytes()).hexdigest(),
    'fault_sha256': hashlib.sha256(fault_source.read_bytes()).hexdigest(),
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'lifecycle_sha256': hashlib.sha256((source.parent / 'BootProgressLifecycle.h').read_bytes()).hexdigest(),
    'core_sha256': hashlib.sha256((source.parent / 'BootProgressCore.h').read_bytes()).hexdigest(),
    'display_sha256': hashlib.sha256((source.parent / 'BootProgressDisplay.h').read_bytes()).hexdigest(),
    'pulse_sha256': hashlib.sha256((source.parent / 'BootProgressPulse.h').read_bytes()).hexdigest(),
    'boot_log_sha256': hashlib.sha256(log_source.read_bytes()).hexdigest(),
    'boot_reset_sha256': hashlib.sha256(reset_source.read_bytes()).hexdigest(),
    'boot_reset_core_sha256': hashlib.sha256((source.parent / 'BootResetCore.h').read_bytes()).hexdigest(),
    'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    'hardware_execution': False,
}, indent=2) + '\n')
print('PASS: strict source-kernel ARM64 boot diagnostic build')
