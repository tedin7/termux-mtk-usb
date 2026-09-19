# Redmi 9 case study — 2026-09-19

## Environment and observed outcome

Host: unrooted OnePlus OPD2415 tablet, Android 16, native Termux USB callback; Debian PRoot development environment. Target: Redmi 9 Global M2004J19C, lancelot, stock Android 12 V13.0.2.0.SJCMIXM, MediaTek hardware code 0x707.

The original experiment completed these steps on one owned device:

1. Android authorized a USB file descriptor. libusb adopted its duplicate without opening USB device nodes directly.
2. BROM was identified as USB 0e8d:0003, with security configuration 0xe7.
3. Upstream Kamakiri2 acknowledged the RAM payload with integer 0xa1a2a3a4. A fresh handshake reported configuration 0x0. This alone did not unlock the bootloader.
4. The phone's own preloader was read from RAM for EMI/DRAM configuration. The pinned upstream V5 Download Agent initialized eMMC.
5. GPT header/table CRCs and partition geometry passed validation. Seven radio/configuration partitions totaling 216,391,680 bytes were backed up, each with two matching device reads and local hash verification. An external copy was verified separately.
6. A correct stock recovery archive was downloaded from Xiaomi's CDN and verified before persistent changes.
7. The upstream SecCfgV4 implementation validated the original hardware-backed configuration and generated an unlock candidate using hardware crypto. The original sector padding was preserved. Only the lock-state field and authentication bytes differed.
8. In a new uninterrupted BROM/DA session, live GPT and the entire seccfg partition were checked against their originals. One 512-byte sector was written. Reading the whole partition back confirmed that only the intended candidate sector changed.
9. After physical reboot into Fastboot, the device returned `product: lancelot` and `unlocked: yes`. This was the independent confirmation of permanent unlock.
10. The official Lineage recovery image was flashed successfully. LineageOS sideload subsequently completed with `Total xfer: 1.00x`; MindTheGapps sideload also completed with `Total xfer: 1.00x`. The user initiated the first system boot; functional Maps/Cast validation remained pending.

## Engineering findings

- Root on the Android host was not necessary once the Android-granted descriptor was adopted correctly.
- The CDC control interface's driver had to be detached before claiming the data interface, then restored during cleanup.
- Reading one byte from a bulk endpoint caused overflow; whole-packet reads with retained surplus bytes fixed protocol field parsing.
- USB timeout handling must retain partial fields instead of discarding received bytes.
- Kamakiri2's payload result is a numeric acknowledgement, not boolean `True`.
- Reopening the DA after callback teardown did not work reliably. BROM setup, DA initialization and the intended operation must stay in one authorized process/session.
- The temporary RAM security change and the permanent Fastboot unlock state are different milestones.

## What the public code can reproduce

The USB transport, BROM identification, constrained GPT/backup workflow and offline tests are included. Phone-specific unlock inputs and the one-use writer are not distributed. The methodology is described above to preserve the result without turning private device state into a purported universal recipe. No claim is made for another model, firmware, hardware code or Android host.

Serials, eMMC CID, GPT disk GUIDs, account information, phone numbers, partition dumps, signed unlock candidates, session logs and stock/ROM binaries have been excluded from this repository.
