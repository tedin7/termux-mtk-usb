"""Descriptor inspection and bounded BROM identification, without flash writes."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'vendor/mtkclient'))
import usb.core
import usb.util
from mtkclient.Library.Connection.termuxusb import TermuxFDBackend


class PacketReader:
    """Read full USB packets, retaining bytes across protocol field reads."""
    def __init__(self, endpoint, timeout=750):
        self.endpoint = endpoint
        self.timeout = timeout
        self.pending = bytearray()

    def __call__(self, count):
        if not self.pending:
            self.pending.extend(self.endpoint.read(self.endpoint.wMaxPacketSize, timeout=self.timeout))
        result = bytes(self.pending[:count])
        del self.pending[:len(result)]
        return result


def identify(write, read, trace=None):
    """Only synchronization, GET_HW_CODE, GET_TARGET_CONFIG are sent."""
    def exact(n):
        data = bytearray()
        for attempt in range(n + 3):
            chunk = bytes(read(n - len(data)))
            if trace is not None:
                trace.setdefault('reads', []).append({'requested': n - len(data), 'received': len(chunk)})
            data.extend(chunk)
            if len(data) == n:
                return bytes(data)
            if len(data) > n:
                raise RuntimeError('Oversized USB response')
        raise RuntimeError(f'Short USB response: {len(data)}/{n} bytes')

    def exchange(value, expected):
        if trace is not None:
            trace['last_command'] = hex(value)
        if write(bytes([value])) != 1:
            raise RuntimeError('Short USB write')
        received = exact(1)
        if received != bytes([expected]):
            raise RuntimeError(f'Unexpected protocol echo {received.hex()} for {value:02x}; reconnect before retrying')

    for value in (0xA0, 0x0A, 0x50, 0x05):
        exchange(value, value ^ 0xFF)
    exchange(0xFD, 0xFD)
    hwcode, hwver = struct.unpack('>HH', exact(4))
    exchange(0xD8, 0xD8)
    flags, status = struct.unpack('>IH', exact(6))
    if status > 0xFF:
        raise RuntimeError(f'GET_TARGET_CONFIG rejected: {status:#06x}')
    return {'hwcode': hex(hwcode), 'hwver': hex(hwver),
            'target_config': hex(flags), 'status': status,
            'sbc': bool(flags & 1), 'sla': bool(flags & 2),
            'daa': bool(flags & 4), 'memory_read_auth': bool(flags & 0x20),
            'memory_write_auth': bool(flags & 0x40)}


def describe(dev):
    # Never read string descriptors (serial number, manufacturer, etc.).
    return {'vid': f'{dev.idVendor:04x}', 'pid': f'{dev.idProduct:04x}',
            'device_class': dev.bDeviceClass,
            'configurations': [
                {'value': cfg.bConfigurationValue, 'interfaces': [
                    {'number': itf.bInterfaceNumber, 'alternate': itf.bAlternateSetting,
                     'class': itf.bInterfaceClass, 'endpoints': [
                         {'address': ep.bEndpointAddress, 'attributes': ep.bmAttributes,
                          'packet_size': ep.wMaxPacketSize} for ep in itf]}
                    for itf in cfg]} for cfg in dev]}


def brom_endpoints(dev):
    # This first version deliberately does not send BROM bytes to Preloader,
    # ADB, fastboot or arbitrary USB devices.
    if (dev.idVendor, dev.idProduct) != (0x0E8D, 0x0003):
        raise RuntimeError('BROM required (0e8d:0003); no protocol bytes sent')
    candidates = []
    for itf in dev.get_active_configuration():
        if itf.bAlternateSetting != 0:
            continue
        incoming = [ep for ep in itf if ep.bmAttributes & 3 == 2 and ep.bEndpointAddress & 0x80]
        outgoing = [ep for ep in itf if ep.bmAttributes & 3 == 2 and not ep.bEndpointAddress & 0x80]
        if len(incoming) == len(outgoing) == 1:
            candidates.append((itf.bInterfaceNumber, incoming[0], outgoing[0]))
    if len(candidates) != 1:
        raise RuntimeError('Ambiguous or missing bulk endpoint pair')
    return candidates[0]


@contextmanager
def claim_brom(dev, interface, report):
    if (dev.idVendor, dev.idProduct) != (0x0E8D, 0x0003):
        raise RuntimeError('Driver handling is restricted to BROM')
    detached = []
    claimed = False
    try:
        # CDC drivers may own both the control and data interfaces.
        for number in sorted({i.bInterfaceNumber for i in dev.get_active_configuration()}):
            try:
                active = dev.is_kernel_driver_active(number)
            except NotImplementedError:
                active = False
            if active:
                dev.detach_kernel_driver(number)
                detached.append(number)
        report['detached_driver_interfaces'] = detached.copy()
        usb.util.claim_interface(dev, interface)
        claimed = True
        yield
    finally:
        warnings = []
        if claimed:
            try:
                usb.util.release_interface(dev, interface)
            except usb.core.USBError as exc:
                warnings.append(str(exc))
        for number in reversed(detached):
            try:
                dev.attach_kernel_driver(number)
            except (usb.core.USBError, NotImplementedError) as exc:
                warnings.append(str(exc))
        if warnings:
            report['driver_cleanup_warnings'] = warnings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['describe', 'identify'])
    args = parser.parse_args()
    backend = dev = None
    result = {'mode': args.mode, 'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    rc = 1
    try:
        if 'TERMUX_USB_FD' not in os.environ:
            raise RuntimeError('Run through launch.py / termux-usb, not directly')
        # Exercise the actual mtkclient integration without its Preloader.init,
        # which also changes watchdog registers and requests device identifiers.
        from mtkclient.Library.Connection.usblib import UsbClass
        from mtkclient.config.usb_ids import default_ids
        transport = UsbClass(portconfig=default_ids)
        backend = transport.backend
        if not isinstance(backend, TermuxFDBackend):
            raise RuntimeError('mtkclient did not select the authorized-FD backend')
        dev = usb.core.find(backend=backend)
        if dev is None:
            raise RuntimeError('Authorized USB device absent')
        result['usb'] = describe(dev)
        if args.mode == 'identify':
            interface, ep_in, ep_out = brom_endpoints(dev)
            with claim_brom(dev, interface, result):
                result['protocol'] = {}
                result['brom'] = identify(lambda data: ep_out.write(data, timeout=750),
                                          PacketReader(ep_in), result['protocol'])
        result['ok'] = True
        rc = 0
    except Exception as exc:
        result.update(ok=False, error_type=type(exc).__name__, error=str(exc))
    finally:
        try:
            if dev is not None:
                usb.util.dispose_resources(dev)
        finally:
            if backend is not None:
                backend.shutdown()
    print(json.dumps(result, indent=2), flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main())
