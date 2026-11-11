#!/usr/bin/env python3
"""Mac replacement for mi10ultra/scripts/windows/winfbd3.ps1.

The hackios loader (xnu loader) exposes a vendor-class USB device
  VID 18D1 / PID D00D, interface name "xnuloader"
and speaks a very small ASCII protocol over two bulk endpoints:
  EP 0x01 OUT  -> command bytes ("oem ramdisk", "stage 0x...", "oem run", ...)
  EP 0x81 IN   -> reply (a single short ASCII line)

Staging a file is:
  send "upload"            -> reply "DATA<total-bytes-in-hex>"
  write the whole file in bulk chunks
  read one reply line      -> "OKAY" (or an error string)

The Windows daemon keeps the handle open in a resident loop because closing
the handle makes the loader deaf. Here the same effect is achieved by doing a
whole boot in one process run, so the handle stays open for the whole sequence.

Usage:
  boot_hackios.py [--loader-log FILE] [--screenshot FILE]
                  [--ramdisk none] PAYLOAD ROOTFS
"""

import argparse
import os
import sys
import time

import libusb_package
import usb.core
import usb.util

VENDOR = 0x18D1
PRODUCT = 0xD00D
EP_OUT = 0x01
EP_IN = 0x81
CHUNK = 1 << 20
REPLY_TIMEOUT_MS = 8000
HEADER_TIMEOUT_MS = 600000


def open_loader(wait_seconds=60):
    backend = libusb_package.get_libusb1_backend()
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT, backend=backend)
        if dev is not None:
            break
        time.sleep(1)
    else:
        sys.exit('xnuloader (18d1:d00d) never appeared; is the loader running?')

    try:
        dev.set_configuration()
    except usb.core.USBError:
        # already configured, or the loader ignores SET_CONFIGURATION
        pass

    # libusb on macOS refuses bulk I/O on an interface the process has not
    # claimed: the writes just sit on the endpoint and time out. The Windows
    # daemon gets this for free because WinUSB opens the device interface
    # directly, so it has no equivalent step.
    try:
        usb.util.claim_interface(dev, 0)
        print('interface 0 claimed')
    except usb.core.USBError as exc:
        print('claim_interface: %s (continuing)' % exc)

    cfg = dev.get_active_configuration()
    iface = cfg[(0, 0)]
    ep_out = usb.util.find_descriptor(
        iface, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
    ep_in = usb.util.find_descriptor(
        iface, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
    if ep_out is None or ep_in is None:
        # fall back to the addresses the Windows daemon uses
        ep_out = usb.util.find_descriptor(iface, bEndpointAddress=EP_OUT)
        ep_in = usb.util.find_descriptor(iface, bEndpointAddress=EP_IN)
    if ep_out is None or ep_in is None:
        sys.exit('bulk endpoints not found on the loader interface')
    # A previously interrupted transfer leaves the pipe halted; every later
    # write then times out. Clearing both pipes is harmless when they are fine.
    for ep in (ep_out, ep_in):
        try:
            ep.clear_halt()
        except usb.core.USBError:
            pass
    print('loader up: EP OUT %#04x / EP IN %#04x' % (ep_out.bEndpointAddress, ep_in.bEndpointAddress))
    return dev, ep_out, ep_in


def send(ep_out, text):
    ep_out.write(text.encode('ascii'), timeout=REPLY_TIMEOUT_MS)


def recv(ep_in, timeout=REPLY_TIMEOUT_MS):
    try:
        data = ep_in.read(64, timeout=timeout)
        return bytes(data).decode('ascii', 'replace').strip('\x00').strip()
    except usb.core.USBTimeoutError:
        return '<timeout>'


def cmd(ep_out, ep_in, text, timeout=REPLY_TIMEOUT_MS, required=True):
    send(ep_out, text)
    reply = recv(ep_in, timeout)
    print('  %-22s -> %s' % (text, reply or '<empty>'), flush=True)
    if required and not reply.startswith('OKAY'):
        sys.exit('command %r was not accepted (%r); the loader only reads the next '
                 'command once the current transfer finished, so a half-sent stage '
                 'means it has to be cold-booted again' % (text, reply))
    return reply


def stage(ep_out, ep_in, path):
    size = os.path.getsize(path)
    print('staging %s (%d bytes)' % (os.path.basename(path), size))
    # The Windows daemon stages with the standard fastboot download command,
    # "download:<size as 8 hex digits>". A healthy loader answers "DATA<size>"
    # (its buffer is ready) — not "OKAY" — so that reply must be accepted here;
    # "upload" is the opposite direction (reading a log back out of the loader),
    # which is why using it earlier made the loader report its own buffer size.
    reply = cmd(ep_out, ep_in, 'download:%08x' % size, HEADER_TIMEOUT_MS, required=False)
    if not (reply.startswith('OKAY') or reply.startswith('DATA')):
        sys.exit('download rejected, loader said %r (err=203 means the handle died)' % reply)

    sent = 0
    with open(path, 'rb') as handle:
        while sent < size:
            block = handle.read(CHUNK)
            if not block:
                break
            ep_out.write(block, timeout=HEADER_TIMEOUT_MS)
            sent += len(block)
            if sent % (32 << 20) < CHUNK:
                print('  staged %d / %d MB' % (sent >> 20, size >> 20), flush=True)
    ack = recv(ep_in, HEADER_TIMEOUT_MS)
    print('  staged %d bytes, ack -> %s' % (sent, ack or '<empty>'))
    return ack


def dump_log(ep_out, ep_in, destination):
    print('fetching loader log')
    reply = cmd(ep_out, ep_in, 'oem log', HEADER_TIMEOUT_MS)
    if not reply.startswith('DATA'):
        print('  no log: %r' % reply)
        return
    total = int(reply[4:], 16)
    got = bytearray()
    while len(got) < total:
        try:
            piece = ep_in.read(min(CHUNK, total - len(got)), timeout=HEADER_TIMEOUT_MS)
        except usb.core.USBTimeoutError:
            break
        if not piece:
            break
        got.extend(piece)
    with open(destination, 'wb') as handle:
        handle.write(got)
    print('  log: %d bytes -> %s' % (len(got), destination))
    text = got.decode('utf-8', 'replace')
    tail = [line for line in text.splitlines() if line.strip()][-40:]
    if tail:
        print('  --- loader log tail ---')
        for line in tail:
            print('   ', line)


def handshake(ep_out, ep_in, seconds=40):
    """The loader enumerates a couple of seconds before its command loop is up.

    Writing during that window fails with an average USB error, so retry until
    it answers instead of giving up on the first attempt (that is exactly what
    the Windows daemon's CreateFile retry loop papers over).
    """
    deadline = time.time() + seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            ep_out.write(b'getvar:version', timeout=2000)
            reply = bytes(ep_in.read(64, timeout=2000)).decode('ascii', 'replace').strip('\x00').strip()
            if reply.startswith('OKAY'):
                print('  handshake ok after %d attempt(s): %s' % (attempt, reply), flush=True)
                return reply
            print('  handshake reply %r, retrying' % reply, flush=True)
        except usb.core.USBError as exc:
            if attempt % 5 == 1:
                print('  handshake attempt %d: %s' % (attempt, exc), flush=True)
        time.sleep(1)
    sys.exit('loader never answered getvar:version; cold-boot the phone and retry')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('payload', help='run-payload-*.bin')
    ap.add_argument('rootfs', help='root.apfs / system.apfs staged before the payload')
    ap.add_argument('--ramdisk', default='on', choices=['on', 'none'])
    ap.add_argument('--skip-run', action='store_true')
    ap.add_argument('--loader-log', default=None)
    args = ap.parse_args()

    dev, ep_out, ep_in = open_loader()
    try:
        # Handshake first: if the loader is in a half-consumed transfer it will
        # not answer, and it is much cheaper to find that out here than after
        # staging 92 MiB into the void.
        handshake(ep_out, ep_in)
        if args.ramdisk == 'none':
            cmd(ep_out, ep_in, 'oem ramdisk none')
        else:
            cmd(ep_out, ep_in, 'oem ramdisk')
        stage(ep_out, ep_in, args.rootfs)
        stage(ep_out, ep_in, args.payload)
        if args.skip_run:
            print('skipping run (--skip-run)')
            return
        print('starting the kernel (this blocks until the loader returns)')
        reply = cmd(ep_out, ep_in, 'oem run', HEADER_TIMEOUT_MS, required=False)
        print('oem run -> %r' % reply)
        # the loader wants the colon form; 'getvar result' answers FAILunknown command
        result = cmd(ep_out, ep_in, 'getvar:result', HEADER_TIMEOUT_MS, required=False)
        print('result -> %r   (expect OKAYrunning 0 0)' % result)
    finally:
        if args.loader_log:
            try:
                dump_log(ep_out, ep_in, args.loader_log)
            except Exception as exc:  # keep the boot alive even if the log fetch fails
                print('log fetch failed: %s' % exc)
        usb.util.dispose_resources(dev)


if __name__ == '__main__':
    main()
