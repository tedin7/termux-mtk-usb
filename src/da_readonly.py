"""BROM RAM payload -> XFlash -> validated GPT / verified partition reads.

Runs code in volatile phone RAM. No unlock, format or storage-write commands.
"""
import argparse
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import probe
from da_transport import Session
from storage_backup import read_gpt, save_backups
ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT.parent / 'vendor/mtkclient'

def require_payload_ack(value):
    if type(value) is not int or value != 2711790500:
        raise RuntimeError('RAM payload not acknowledged; no storage operations attempted')

def install_guards():
    from mtkclient.Library.DA.xflash.xflash_lib import DAXFlash
    from mtkclient.Library.DA.xflash.xflash_param import Cmd
    from mtkclient.Library.DA.mtk_daloader import DAloader
    allowed_names = {'SYNC_SIGNAL', 'SETUP_ENVIRONMENT', 'SETUP_HW_INIT_PARAMS', 'INIT_EXT_RAM', 'BOOT_TO', 'DEVICE_CTRL', 'READ_DATA', 'SET_CHECKSUM_LEVEL', 'SET_RESET_KEY', 'SET_REMOTE_SEC_POLICY', 'SLA_ENABLED_STATUS'}
    allowed_names.update((name for name in vars(Cmd) if name.startswith('GET_')))
    allowed = {getattr(Cmd, name) for name in allowed_names}
    original = DAXFlash.xsend

    def guarded(self, data, *args, **kwargs):
        if isinstance(data, int) and data not in allowed:
            raise RuntimeError(f'Command blocked by read-only runner: {data:#x}')
        return original(self, data, *args, **kwargs)
    DAXFlash.xsend = guarded

    def denied(*args, **kwargs):
        raise RuntimeError('Persistent write operation blocked by read-only runner')
    for cls, names in [(DAloader, ('writeflash', 'formatflash', 'seccfg', 'write_rpmb', 'erase_rpmb', 'auth_rpmb')), (DAXFlash, ('writeflash', 'formatflash', 'set_meta', 'set_usb_speed'))]:
        for name in names:
            setattr(cls, name, denied)

def config_for_run(output):
    from mtkclient.config.mtk_config import MtkConfig
    from mtkclient.Library.settings import HwParam
    cfg = MtkConfig(loglevel=logging.WARNING)
    cfg.reconnect = False
    cfg.generatekeys = False
    cfg.loader = str(UPSTREAM / 'mtkclient/Loader/MTK_DA_V5.bin')
    cfg.hwparam = HwParam(cfg, None, str(output))
    cfg.init_hwcode(1799)
    return cfg

def offline_check():
    from mtkclient.Library.mtk_class import Mtk
    from mtkclient.Library.DA.daconfig import DAconfig
    from mtkclient.Library.Exploit.kamakiri2 import Kamakiri2
    cfg = config_for_run(ROOT)
    mtk = Mtk(cfg, preinit=False)
    mtk.port = SimpleNamespace(usbwrite=None, usbread=None)
    mtk.daloader = SimpleNamespace()
    mtk.daloader.daconfig = DAconfig(mtk, loader=cfg.loader)
    selected = mtk.daloader.daconfig.setup()
    if selected is None or selected.v6:
        raise RuntimeError('No compatible V5 loader entry')
    payload = UPSTREAM / 'mtkclient/payloads/mt6768_payload.bin'
    return {'ok': True, 'offline_only': True, 'hwcode': '0x707', 'loader': Path(cfg.loader).name, 'loader_sha256': hashlib.sha256(Path(cfg.loader).read_bytes()).hexdigest(), 'payload_sha256': hashlib.sha256(payload.read_bytes()).hexdigest()}

def ram_preloader(mtk):

    def read(address, length):
        words = mtk.preloader.read32(address, length // 4)
        if not isinstance(words, (list, tuple)) or len(words) != length // 4:
            raise RuntimeError('Incomplete RAM read')
        return b''.join((struct.pack('<I', word) for word in words))
    region = read(2097152, 65536)
    offset = region.find(b'MMM\x018\x00\x00\x00')
    if offset < 0 or offset + 36 > len(region):
        raise RuntimeError('Own preloader not present in RAM; stock firmware extraction required')
    length = struct.unpack_from('<I', region, offset + 32)[0]
    if not 256 <= length <= 1024 * 1024:
        raise RuntimeError('Invalid preloader RAM image length')
    data = bytearray()
    for pos in range(0, length, 4096):
        wanted = min(4096, length - pos)
        data.extend(read(2097152 + offset + pos, (wanted + 3) // 4 * 4)[:wanted])
    return bytes(data)

def run(mode, output, result):
    import mtkclient.Library.Port as port_module
    from mtkclient.Library.mtk_class import Mtk
    from mtkclient.Library.Exploit.kamakiri2 import Kamakiri2
    port_module.UsbClass = Session
    install_guards()
    cfg = config_for_run(output)
    mtk = Mtk(cfg, loglevel=logging.WARNING)
    cdc = mtk.port.cdc
    try:
        result['stage'] = 'brom_identification'
        cdc.connect()
        info = probe.identify(lambda data: cdc.EP_OUT.write(data, timeout=750), cdc.usbread)
        result['brom'] = info
        if info['hwcode'] != '0x707':
            raise RuntimeError('This runner is restricted to the confirmed MT6768/MT6769 target')
        flags = int(info['target_config'], 16)
        cfg.target_config = {key: bool(flags & bit) for key, bit in [('sbc', 1), ('sla', 2), ('daa', 4), ('epp', 8), ('cert', 16), ('memread', 32), ('memwrite', 64), ('cmdC8', 128)]}
        cfg.is_brom = True
        cfg.hw_sub_code, cfg.hwver, cfg.swver, status = mtk.preloader.get_hw_sw_ver()
        if status != 0:
            raise RuntimeError('GET_HW_SW_VER returned failure')
        mtk.preloader.get_blver()
        mtk.preloader.get_bromver()
        result['stage'] = 'ram_security_payload'
        if not mtk.preloader.setreg_disablewatchdogtimer(cfg.hwcode, cfg.hwver):
            raise RuntimeError('Watchdog setup failed')
        payload = (UPSTREAM / 'mtkclient/payloads/mt6768_payload.bin').read_bytes()
        secured = any((cfg.target_config[key] for key in ('sbc', 'sla', 'daa')))
        if secured:
            ack = Kamakiri2(mtk, logging.WARNING).runpayload(payload)
            require_payload_ack(ack)
            result['payload_ack'] = hex(ack)
        mtk.daloader.patch = True
        if secured:
            result['post_payload_brom'] = probe.identify(lambda data: cdc.EP_OUT.write(data, timeout=750), cdc.usbread)
        result['stage'] = 'read_own_preloader_ram'
        preloader = ram_preloader(mtk)
        (output / 'preloader-from-ram.bin').write_bytes(preloader)
        result['preloader_sha256'] = hashlib.sha256(preloader).hexdigest()
        da_config = mtk.daloader.daconfig
        da_config.setup()
        da_config.extract_emi(preloader)
        if not da_config.emi:
            raise RuntimeError('No valid DRAM configuration in own preloader')
        result['stage'] = 'upload_download_agent_to_ram'
        mtk.daloader.set_da()
        mtk.daloader.da.xft.patch = lambda: None
        if not mtk.daloader.da.upload_da():
            raise RuntimeError('Download Agent initialization failed')
        if da_config.storage.flashtype != 'emmc':
            raise RuntimeError('Unexpected storage type; refusing assumed sector geometry')
        flash_size = da_config.storage.flashsize
        result['stage'] = 'read_gpt'
        read = lambda addr, size: mtk.daloader.readflash(addr, size, '', 'user', display=False)
        entries, header, table = read_gpt(read, flash_size)
        (output / 'gpt-header.bin').write_bytes(header)
        (output / 'gpt-entries.bin').write_bytes(table)
        (output / 'partitions.json').write_text(json.dumps(entries, indent=2))
        result['partitions'] = entries
        if mode == 'backup':
            result['stage'] = 'verified_partition_backups'
            result['backups'] = save_backups(read, entries, output)
        result.update(ok=True, stage='complete')
    finally:
        cdc.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['check', 'gpt', 'backup'])
    args = parser.parse_args()
    if args.mode == 'check':
        print(json.dumps(offline_check(), indent=2))
        return 0
    os.umask(63)
    if 'TERMUX_USB_FD' not in os.environ:
        raise RuntimeError('Use launch.py to obtain a fresh Android USB FD')
    output = Path(os.environ['MTK_RUN_DIR'])
    result = {'ok': False, 'mode': args.mode}
    try:
        with (output / 'da-private.log').open('w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            logging.basicConfig(stream=log, level=logging.WARNING, force=True)
            run(args.mode, output, result)
    except (Exception, SystemExit) as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result['ok'] else 1
if __name__ == '__main__':
    sys.exit(main())
