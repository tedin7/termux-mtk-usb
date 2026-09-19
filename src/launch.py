"""Request a fresh Android USB FD and run a bounded diagnostic callback."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid
ROOT = Path(__file__).resolve().parent
PREFIX = Path('/data/data/com.termux/files/usr')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['describe', 'identify', 'gpt', 'backup'])
    parser.add_argument('--device', help='Exact path from termux-usb -l; required if multiple devices exist')
    args = parser.parse_args()
    os.umask(63)
    env = dict(os.environ, PATH=f'{PREFIX}/bin:/system/bin')
    usb = str(PREFIX / 'bin/termux-usb')
    raw = subprocess.check_output([usb, '-l'], env=env, text=True, timeout=15)
    devices = json.loads(raw)
    if not isinstance(devices, list) or not all((isinstance(x, str) for x in devices)):
        raise RuntimeError('Invalid USB enumeration response')
    if args.device:
        if args.device not in devices:
            raise RuntimeError('Requested device no longer attached; enumerate again')
        device = args.device
    elif len(devices) == 1:
        device = devices[0]
    else:
        raise RuntimeError(f'Expected one USB device, found {len(devices)}; use --device if needed')
    run = ROOT.parent / 'runs' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, mode=448)
    callback = run / 'callback.sh'
    is_da = args.mode in ('gpt', 'backup')
    runtime = 1800 if args.mode in ('backup',) else 300 if is_da else 20
    script = 'da_readonly.py' if is_da else 'probe.py'
    command = [str(PREFIX / 'bin/timeout'), '-k', '2', str(runtime), sys.executable, str(ROOT / script), args.mode]
    callback.write_text(f"#!{PREFIX}/bin/sh\nexport PATH={shlex.quote(env['PATH'])}\n" + f'export MTK_RUN_DIR={shlex.quote(str(run))}\n' + f'[ "$(date +%s)" -le {int(time.time()) + 45} ] || exit 124\n' + 'date +%s >' + shlex.quote(str(run / 'started')) + '\n' + shlex.join(command) + ' >' + shlex.quote(str(run / 'result.json')) + ' 2>' + shlex.quote(str(run / 'stderr.log')) + '\n' + 'rc=$?\nprintf "%s\\n" "$rc" >' + shlex.quote(str(run / 'status')) + '\n' + 'exit "$rc"\n')
    callback.chmod(448)
    print(f'Results: {run}', flush=True)
    with (run / 'permission.log').open('w') as output:
        process = subprocess.Popen([usb, '-r', '-E', '-e', str(callback), device], env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 45
            while not (run / 'status').exists():
                if is_da and (run / 'started').exists():
                    is_da = False
                    deadline = time.monotonic() + runtime + 5
                if process.poll() is not None:
                    raise RuntimeError('USB callback did not complete: ' + (run / 'permission.log').read_text().strip())
                if time.monotonic() >= deadline:
                    raise TimeoutError('USB permission/callback timeout; reconnect and retry')
                time.sleep(0.1)
            rc = int((run / 'status').read_text())
            result_text = (run / 'result.json').read_text()
            if not result_text.strip():
                raise RuntimeError(f'Callback terminated without a report (exit {rc}); inspect local stderr.log')
            print(result_text, end='')
            return rc
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
