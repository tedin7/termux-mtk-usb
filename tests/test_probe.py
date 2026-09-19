import os
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import probe
from mtkclient.Library.Connection.termuxusb import TermuxFDBackend


class ProtocolTests(unittest.TestCase):
    def test_packet_reader_preserves_coalesced_fields(self):
        endpoint = MagicMock(wMaxPacketSize=512)
        endpoint.read.return_value = b'\xfd\x07\x07\xca\x00'
        reader = probe.PacketReader(endpoint)
        self.assertEqual(reader(1), b'\xfd')
        self.assertEqual(reader(2), b'\x07\x07')
        self.assertEqual(reader(2), b'\xca\x00')
        endpoint.read.assert_called_once_with(512, timeout=750)

    def test_fragmented_hardware_and_flags_responses(self):
        replies = iter([b'\x5f', b'\xf5', b'\xaf', b'\xfa', b'\xfd',
                        b'\x07\x07', b'\xca\x00', b'\xd8',
                        b'\x00\x00\x00\x07', b'\x00\x00'])
        result = probe.identify(lambda d: len(d), lambda n: next(replies))
        self.assertEqual(result['hwcode'], '0x707')
        self.assertTrue(result['daa'])

    def test_driver_restored_when_protocol_fails(self):
        dev = MagicMock(idVendor=0x0E8D, idProduct=3)
        dev.get_active_configuration.return_value = [SimpleNamespace(bInterfaceNumber=0), SimpleNamespace(bInterfaceNumber=1)]
        dev.is_kernel_driver_active.side_effect = [True, False]
        report = {}
        with patch.object(probe.usb.util, 'claim_interface') as claim, \
             patch.object(probe.usb.util, 'release_interface') as release:
            with self.assertRaisesRegex(RuntimeError, 'protocol'):
                with probe.claim_brom(dev, 1, report):
                    raise RuntimeError('protocol failed')
            claim.assert_called_once_with(dev, 1)
            release.assert_called_once_with(dev, 1)
        dev.detach_kernel_driver.assert_called_once_with(0)
        dev.attach_kernel_driver.assert_called_once_with(0)
        self.assertEqual(report['detached_driver_interfaces'], [0])

    def test_no_driver_changes_outside_brom(self):
        dev = MagicMock(idVendor=0x0E8D, idProduct=0x20FF)
        with self.assertRaisesRegex(RuntimeError, 'restricted'):
            with probe.claim_brom(dev, 0, {}):
                self.fail('Must reject wrong mode')
        dev.get_active_configuration.assert_not_called()

    def replies(self, status=0):
        return iter([b'\x5f', b'\xf5', b'\xaf', b'\xfa', b'\xfd',
                     struct.pack('>HH', 0x707, 0xCA00), b'\xd8',
                     struct.pack('>IH', 7, status)])

    def test_only_identification_commands(self):
        writes, replies = [], self.replies()
        def write(data):
            writes.append(data)
            return len(data)
        result = probe.identify(write, lambda n: next(replies))
        self.assertEqual(b''.join(writes), b'\xa0\x0a\x50\x05\xfd\xd8')
        self.assertEqual(result['hwcode'], '0x707')
        self.assertTrue(result['sbc'] and result['sla'] and result['daa'])

    def test_bad_echo_stops_immediately(self):
        writes = []
        def write(data):
            writes.append(data)
            return 1
        with self.assertRaisesRegex(RuntimeError, 'echo'):
            probe.identify(write, lambda n: b'\x00')
        self.assertEqual(writes, [b'\xa0'])

    def test_short_read(self):
        with self.assertRaisesRegex(RuntimeError, 'Short'):
            probe.identify(lambda d: 1, lambda n: b'')

    def test_short_write(self):
        with self.assertRaisesRegex(RuntimeError, 'Short'):
            probe.identify(lambda d: 0, lambda n: self.fail('Must not read'))

    def test_rejected_target_config(self):
        replies = self.replies(0x1000)
        with self.assertRaisesRegex(RuntimeError, 'rejected'):
            probe.identify(lambda d: len(d), lambda n: next(replies))

    def test_disconnect_propagates(self):
        with self.assertRaises(OSError):
            probe.identify(lambda d: 1, lambda n: (_ for _ in ()).throw(OSError('disconnected')))

    def test_wrong_device_never_claimed(self):
        for vid, pid in [(0x2717, 0xFF48), (0x0E8D, 0x2000), (0x18D1, 0xD00D)]:
            with self.assertRaisesRegex(RuntimeError, 'no protocol bytes'):
                probe.brom_endpoints(SimpleNamespace(idVendor=vid, idProduct=pid))

    def test_cleanup_idempotent_and_fd_owner(self):
        # Real OS descriptors prove that only our duplicate is closed.
        from ctypes import c_void_p
        r, w = os.pipe()
        backend = TermuxFDBackend.__new__(TermuxFDBackend)
        backend.closed = False
        backend.fd = os.dup(r)
        backend.handle = c_void_p(1)
        backend.ctx = c_void_p(2)
        calls = []
        backend.lib = SimpleNamespace(libusb_close=lambda h: calls.append('close'),
                                      libusb_exit=lambda c: calls.append('exit'))
        try:
            backend.shutdown()
            backend.shutdown()
            os.fstat(r)
            self.assertEqual(calls, ['close', 'exit'])
            with self.assertRaises(RuntimeError):
                list(backend.enumerate_devices())
        finally:
            os.close(r)
            os.close(w)


if __name__ == '__main__':
    unittest.main()
