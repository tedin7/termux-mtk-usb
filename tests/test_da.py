import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import da_readonly
from da_transport import Session


class DATests(unittest.TestCase):
    def test_numeric_payload_ack(self):
        da_readonly.require_payload_ack(0xA1A2A3A4)
        for invalid in (True, False, None, b'\xa1\xa2\xa3\xa4', 0):
            with self.assertRaises(RuntimeError):
                da_readonly.require_payload_ack(invalid)

    @classmethod
    def setUpClass(cls):
        da_readonly.install_guards()

    def test_storage_write_commands_blocked_before_usb(self):
        from mtkclient.Library.DA.xflash.xflash_lib import DAXFlash
        from mtkclient.Library.DA.xflash.xflash_param import Cmd
        dev = SimpleNamespace(usbwrite=MagicMock(), cmd=Cmd())
        for command in (Cmd.WRITE_DATA, Cmd.FORMAT, Cmd.WRITE_EFUSE,
                        Cmd.WRITE_OTP_ZONE, Cmd.SET_UFS_CONFIG, 0x0F000A):
            with self.assertRaisesRegex(RuntimeError, 'blocked'):
                DAXFlash.xsend(dev, command)
        dev.usbwrite.assert_not_called()

    def test_storage_read_command_allowed(self):
        from mtkclient.Library.DA.xflash.xflash_lib import DAXFlash
        from mtkclient.Library.DA.xflash.xflash_param import Cmd
        dev = SimpleNamespace(usbwrite=MagicMock(return_value=True), cmd=Cmd())
        self.assertTrue(DAXFlash.xsend(dev, Cmd.READ_DATA))
        self.assertEqual(dev.usbwrite.call_count, 2)

    def test_unlock_entrypoint_blocked(self):
        from mtkclient.Library.DA.mtk_daloader import DAloader
        with self.assertRaisesRegex(RuntimeError, 'blocked'):
            DAloader.seccfg(None, 'unlock')

    def test_session_reads_partial_packets(self):
        session = object.__new__(Session)
        session.reader = MagicMock(side_effect=[b'ab', b'c', b'd'])
        self.assertEqual(session.usbread(4), b'abcd')

    def test_transfer_timeout_keeps_partial_field(self):
        import usb.core
        session = object.__new__(Session)
        session.reader = MagicMock(side_effect=[b'ab', usb.core.USBTimeoutError('timeout'), b'cd'])
        self.assertEqual(session.usbread(4), b'abcd')

    def test_session_rejects_unbounded_read(self):
        session = object.__new__(Session)
        with self.assertRaises(ValueError):
            session.usbread(100 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
