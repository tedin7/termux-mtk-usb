"""PyUSB 1.3.1 backend adopting an Android-authorized USB descriptor.

One callback/process per USB attachment. Never opens /dev/bus/usb itself.
The caller must dispose PyUSB resources before shutdown().
"""
import ctypes as C
import os
from types import SimpleNamespace

import usb.backend
import usb.backend.libusb1 as L


class TermuxFDBackend(L._LibUSB):
    def __init__(self, fd):
        usb.backend.IBackend.__init__(self)
        self.ctx = C.c_void_p()
        self.handle = C.c_void_p()
        self.fd = -1
        self.closed = False
        # PyUSB's private ABI is pinned to 1.3.1 in this prototype.
        if L._lib is None:
            L._lib = L._load_library(lambda _: '/data/data/com.termux/files/usr/lib/libusb-1.0.so')
            L._setup_prototypes(L._lib)
        self.lib = L._lib
        self.lib.libusb_set_option.argtypes = [C.c_void_p, C.c_int]
        self.lib.libusb_set_option.restype = C.c_int
        self.lib.libusb_wrap_sys_device.argtypes = [C.c_void_p, C.c_ssize_t, C.POINTER(C.c_void_p)]
        self.lib.libusb_wrap_sys_device.restype = C.c_int
        self.lib.libusb_get_device.argtypes = [C.c_void_p]
        self.lib.libusb_get_device.restype = C.c_void_p
        try:
            # LIBUSB_OPTION_NO_DEVICE_DISCOVERY, before creating the context.
            L._check(self.lib.libusb_set_option(None, 2))
            L._check(self.lib.libusb_init(C.byref(self.ctx)))
            self.fd = os.dup(int(fd))
            os.set_inheritable(self.fd, False)
            L._check(self.lib.libusb_wrap_sys_device(self.ctx, self.fd, C.byref(self.handle)))
            devid = self.lib.libusb_get_device(self.handle)
            if not devid:
                raise RuntimeError('libusb returned no device for the authorized FD')
            self.device = SimpleNamespace(devid=devid)
            self.wrapped = SimpleNamespace(handle=self.handle, devid=devid)
        except BaseException:
            self.shutdown()
            raise

    def enumerate_devices(self):
        if self.closed:
            raise RuntimeError('USB attachment closed; request a new Android FD')
        return iter((self.device,))

    def open_device(self, dev):
        if self.closed or dev.devid != self.device.devid:
            raise RuntimeError('Invalid or expired USB attachment')
        return self.wrapped

    def close_device(self, dev_handle):
        # libusb_close belongs to shutdown, after PyUSB releases interfaces.
        pass

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        if self.handle:
            self.lib.libusb_close(self.handle)
            self.handle = C.c_void_p()
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        if self.ctx:
            self.lib.libusb_exit(self.ctx)
            self.ctx = C.c_void_p()

    def _finalize_object(self):
        if hasattr(self, 'closed'):
            self.shutdown()
