#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
revision=cd25cf9c1ff6d36e82697ac2c798e69e9cfb78c3
mkdir -p vendor
if [ ! -d vendor/mtkclient/.git ]; then
  git clone https://github.com/bkerler/mtkclient.git vendor/mtkclient
  git -C vendor/mtkclient checkout "$revision"
fi
[ "$(git -C vendor/mtkclient rev-parse HEAD)" = "$revision" ] || { echo 'Unexpected upstream revision'; exit 1; }
if git -C vendor/mtkclient apply --check ../../patches/usblib.patch; then
  git -C vendor/mtkclient apply ../../patches/usblib.patch
else
  git -C vendor/mtkclient apply --reverse --check ../../patches/usblib.patch
fi
cp src/termuxusb.py vendor/mtkclient/mtkclient/Library/Connection/termuxusb.py
python -m venv .venv
.venv/bin/python -m pip install -r src/requirements.txt
