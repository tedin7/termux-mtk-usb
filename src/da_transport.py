"""One authorized FD and packet buffering for the BROM -> XFlash session."""
import time
import usb.core
from probe import PacketReader, brom_endpoints, claim_brom
from mtkclient.Library.Connection.usblib import UsbClass


class Session(UsbClass):
    def connect(self, *args, **kwargs):
        if self.connected:
            return True
        if getattr(self, '_ended', False):
            raise RuntimeError('USB re-enumerated: a new Android authorization is required')
        self.device = usb.core.find(backend=self.backend)
        if self.device is None or (self.device.idVendor, self.device.idProduct) != (0x0E8D, 0x0003):
            raise RuntimeError('Expected the authorized MediaTek USB attachment')
        try:
            self.device.get_active_configuration()
        except usb.core.USBError as exc:
            if exc.strerror != 'Configuration not set':
                raise
            configurations = list(self.device)
            if len(configurations) != 1 or configurations[0].bConfigurationValue != 1:
                raise RuntimeError('Unexpected USB configuration layout') from exc
            self.device.set_configuration(1)
        self.interface, self.EP_IN, self.EP_OUT = brom_endpoints(self.device)
        self.vid, self.pid = self.device.idVendor, self.device.idProduct
        self.driver_report = {}
        self._claim = claim_brom(self.device, self.interface, self.driver_report)
        self._claim.__enter__()
        self.reader = PacketReader(self.EP_IN, timeout=2000)
        self.connected = True
        return True

    def usbread(self, resplen=None, maxtimeout=1000, w_max_packet_size=None, **kwargs):
        if not isinstance(resplen, int) or not 0 <= resplen <= 16 * 1024 * 1024:
            raise ValueError('Invalid/beyond-limit USB read size')
        data = bytearray()
        deadline = time.monotonic() + 15
        empty = 0
        while len(data) < resplen:
            if time.monotonic() > deadline:
                raise TimeoutError('USB field deadline exceeded')
            try:
                chunk = self.reader(resplen - len(data))
            except usb.core.USBTimeoutError:
                # RAM initialization can exceed a single USB transfer timeout.
                # Keep partially received fields until the overall deadline.
                continue
            empty = empty + 1 if not chunk else 0
            if empty > 3:
                raise RuntimeError('Repeated empty USB packets')
            data.extend(chunk)
        return bytes(data)

    def usbwrite(self, data, pktsize=None):
        data = bytes(data)
        size = pktsize or 16384
        for offset in range(0, len(data), size):
            chunk = data[offset:offset + size]
            if self.EP_OUT.write(chunk, timeout=2000) != len(chunk):
                raise RuntimeError('Short USB write')
        return True

    def close(self, reset=False):
        try:
            if self.connected:
                self._claim.__exit__(None, None, None)
                usb.util.dispose_resources(self.device)
        finally:
            self.connected = False
            self._ended = True
            self.backend.shutdown()
        if reset:
            raise RuntimeError('USB reset is not supported in this single-attachment session')
