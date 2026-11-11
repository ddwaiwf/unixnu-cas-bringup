#!/usr/bin/env python3
"""Rewrite one byte range of a flashed partition without touching the rest.

The sparse images produced by make-android-sparse.py start at block 0, so every
segment carries a FILL(0) prefix for [0, segment start) and a segmented flash
only works in strictly descending order -- one wrong order, or one segment that
never lands, and the tail silently reads back as zeros. That is exactly what
native-ufs-probe=15 showed on cas: the OS window block 0 hashes to the signed
value while the SystemOS window block 0 hashes to the SHA-256 of 4096 zero
bytes.

This writer emits a single sparse image whose prefix is DONT_CARE (0xCAC3)
instead of FILL(0): fastboot skips those blocks and writes nothing there, so
only [start, start+length) is overwritten. Inside that range all-zero 4096-byte
blocks become FILL(0) chunks to keep the transfer small, everything else RAW in
16 MiB chunks.

The expanded image is re-read and compared with the source range before the
file is accepted, so a truncated or misaligned writer fails here, not on the
phone.
"""
import argparse
import hashlib
import struct
import sys
from pathlib import Path

BLOCK = 4096
MAGIC = 0xED26FF3A
RAW, FILL, DONT_CARE = 0xCAC1, 0xCAC2, 0xCAC3
CHUNK_BLOCKS = 4096              # 16 MiB of payload data per chunk
ZERO = bytes(BLOCK)


def hash_range(path, offset, length):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        handle.seek(offset)
        left = length
        while left:
            data = handle.read(min(left, 8 << 20))
            if not data:
                raise SystemExit('short read at offset %d' % (offset + length - left))
            digest.update(data)
            left -= len(data)
    return digest.hexdigest()


def build(source, target, start, length):
    image_bytes = source.stat().st_size
    total_blocks = -(-image_bytes // BLOCK)
    if start % BLOCK or length % BLOCK or start + length > image_bytes:
        raise SystemExit('region must be block aligned and inside the image')
    start_block = start // BLOCK
    region_blocks = length // BLOCK

    chunks = []
    with open(source, 'rb') as src, open(target, 'wb') as out:
        out.write(b'\0' * 28)
        src.seek(start)

        def header(kind, count, total):
            out.write(struct.pack('<HHII', kind, 0, count, total))

        if start_block:
            header(DONT_CARE, start_block, 12)
            chunks.append(DONT_CARE)
        done = 0
        while done < region_blocks:
            take = min(CHUNK_BLOCKS, region_blocks - done)
            data = src.read(take * BLOCK)
            if len(data) != take * BLOCK:
                raise SystemExit('short read inside the region')
            if data == ZERO * take:
                header(FILL, take, 12 + 4)
                out.write(struct.pack('<I', 0))
                chunks.append(FILL)
            else:
                header(RAW, take, 12 + len(data))
                out.write(data)
                chunks.append(RAW)
            done += take
        tail = total_blocks - start_block - region_blocks
        if tail:
            header(DONT_CARE, tail, 12)
            chunks.append(DONT_CARE)
        out.seek(0)
        out.write(struct.pack('<IHHHHIIII', MAGIC, 1, 0, 28, 12, BLOCK,
                              total_blocks, len(chunks), 0))
    return total_blocks, chunks


def verify(target, source, start, length, total_blocks):
    """Expand and hash the region the device will see; DONT_CARE reads as zeros."""
    digest = hashlib.sha256()
    with open(target, 'rb') as handle:
        magic, major, minor, fhs, chs, blk, total, chunks, _ = struct.unpack('<IHHHHIIII', handle.read(28))
        if magic != MAGIC or (fhs, chs, blk) != (28, 12, BLOCK) or total != total_blocks:
            raise SystemExit('bad sparse header')
        block = 0
        zeros = bytes(1 << 20)
        for _ in range(chunks):
            kind, _, count, total_sz = struct.unpack('<HHII', handle.read(12))
            payload = handle.tell()
            a = max(block * BLOCK, start)
            b = min((block + count) * BLOCK, start + length)
            if b > a:
                want = b - a
                if kind == RAW:
                    handle.seek(payload + (a - block * BLOCK))
                    digest.update(handle.read(want))
                elif kind in (FILL, DONT_CARE):
                    while want:
                        step = min(want, len(zeros))
                        digest.update(zeros[:step])
                        want -= step
                else:
                    raise SystemExit('unexpected chunk type %#x' % kind)
            # Position the file pointer at the next chunk header.
            if kind == RAW:
                if total_sz != 12 + count * BLOCK:
                    raise SystemExit('bad RAW chunk size')
                handle.seek(payload + count * BLOCK)
            elif kind == FILL:
                if total_sz != 16:
                    raise SystemExit('bad FILL chunk size')
                value = handle.read(4)
                if value != b'\0\0\0\0':
                    raise SystemExit('unexpected fill value')
            elif kind == DONT_CARE:
                if total_sz != 12:
                    raise SystemExit('bad DONT_CARE chunk size')
            else:
                raise SystemExit('unexpected chunk type %#x' % kind)
            block += count
        if block != total_blocks:
            raise SystemExit('chunks cover %d of %d blocks' % (block, total_blocks))
    if digest.hexdigest() != hash_range(source, start, length):
        raise SystemExit('sparse expansion differs from the source range')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--start', type=lambda v: int(v, 0), required=True)
    parser.add_argument('--length', type=lambda v: int(v, 0), required=True)
    args = parser.parse_args()
    total_blocks, chunks = build(args.source, args.target, args.start, args.length)
    verify(args.target, args.source, args.start, args.length, total_blocks)
    sizes = {kind: chunks.count(kind) for kind in (RAW, FILL, DONT_CARE)}
    print('%s: %d blocks, %d chunks (raw %d, fill %d, dont-care %d), %d bytes'
          % (args.target.name, total_blocks, len(chunks), sizes[RAW], sizes[FILL],
             sizes[DONT_CARE], args.target.stat().st_size))
    print('region [0x%x, 0x%x) verified against the source image' % (args.start, args.start + args.length))


if __name__ == '__main__':
    main()
