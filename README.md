# termux-mtk-usb

Experimental Android-authorized USB transport for mtkclient/PyUSB on an **unrooted Termux host**, with a Redmi 9 (`lancelot`, hardware code `0x707`) case study.

The backend adopts the USB file descriptor granted by Android using `termux-usb` and `libusb_wrap_sys_device`. The native Termux process owns the USB connection; Debian/PRoot was used for development, not to obtain USB privileges.

## What is included

- A pinned mtkclient integration patch and PyUSB backend with explicit descriptor ownership and cleanup.
- Descriptor inspection and bounded BootROM identification.
- A single-session BROM → RAM payload → XFlash transport, validated GPT reader and partition backup runner restricted to hardware code `0x707`.
- Two device reads plus a local SHA-256 check for every finalized partition backup.
- Unit tests and a documented hardware case study.

The public CLI exposes `describe`, `identify`, `gpt` and `backup`. It does **not** expose the one-off phone-specific unlock writer used in the case study: that writer depended on private backups, exact live partition comparisons and a candidate cryptographically validated on that phone. There is no reusable unlock image. This repository is an integration prototype, not a universal unlocking utility.

## Setup on native Termux

Use an Android USB host/OTG connection. `termux-usb -l` must work; some Termux distributions require the matching Termux:API app. Tested host: OnePlus OPD2415, Android 16, unrooted, Play Store Termux, libusb 1.0.30. Other combinations are unverified.

```sh
pkg install git python libusb clang make pkg-config
# If termux-usb is absent, install/configure Termux:API for your distribution.
bash setup.sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python src/da_readonly.py check
.venv/bin/python src/launch.py describe
```

`setup.sh` downloads upstream mtkclient at commit `cd25cf9c1ff6d36e82697ac2c798e69e9cfb78c3`, applies the backend patch and installs pinned Python dependencies. Upstream contains the existing payload/DA resources; this repository does not redistribute firmware or device dumps.

For the tested Redmi, power off, disconnect USB, hold both volume keys and reconnect without pressing Power. Release the keys when the tablet requests USB permission and authorize the callback:

```sh
.venv/bin/python src/launch.py identify
# A fresh BootROM attachment is required for each subsequent operation.
.venv/bin/python src/launch.py gpt
# Re-enter BootROM again before backup:
.venv/bin/python src/launch.py backup
```

`gpt` and `backup` execute upstream code in volatile device RAM and change watchdog/security state there. Their persistent-storage write entrypoints are blocked. The read-only guards are operational checks, not a sandbox against arbitrary Python code. Outputs are private local files under ignored `runs/`; radio backups and logs can contain identifiers and must not be published.

## Limits

Only the documented Redmi 9 configuration was hardware-tested. The public packaging and removal of private unlock paths have unit-test coverage; the complete packaged backup workflow has not been repeated on hardware after the successful original run. There is no automatic re-enumeration/reconnect support. Reopening an existing DA session failed in testing; start a fresh BootROM session. PyUSB's private ABI is pinned to 1.3.1. Backups cover selected radio/configuration partitions, **not** personal files or the whole flash.

## Provenance and prior work

This is not a new exploit and we make no first-ever claim. Credit belongs to [bkerler/mtkclient](https://github.com/bkerler/mtkclient), its contributors and existing Kamakiri2/payload work, [PyUSB](https://github.com/pyusb/pyusb), [libusb's Android FD support](https://github.com/libusb/libusb/wiki/Android), [Termux USB](https://wiki.termux.com/wiki/Termux-usb) and [nohajc/termux-adb](https://github.com/nohajc/termux-adb) for the separate ADB/Fastboot host tooling.

Related work found during the post-experiment search includes [MTKClient-NoRoot-Termux](https://github.com/itz-termux-dev/MTKClient-NoRoot-Termux), [Termux-MTKClient](https://github.com/miaoxiaocheng/Termux-MTKClient), and [mtkclient discussion #337](https://github.com/bkerler/mtkclient/discussions/337). Their existence rules out claiming novelty for the broad idea; we have not established feature parity or independently reproduced their claims.

See [the case study](docs/redmi9-case-study.md). Source and integration patch distributed under GPL-3.0; upstream attribution and license are retained.
