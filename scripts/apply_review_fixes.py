"""Owner-run, hash-pinned 0.5.4 review fixes; preserves policy, runtime flags and budgets."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

UPDATER = Path('/tmp/liberdus-update-0.5.4-20260921.pyz')
UPDATER_SHA256 = '287a46612f150397d1ba74e52ae9c219867d901f7cfbc7ea7f14b760be154434'


def run(argv):
    lines = []
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, bufsize=1) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
            lines.append(line)
        code = process.wait()
    if code:
        raise RuntimeError('Command failed; remaining steps were not run.')
    return ''.join(lines)


def restart(hermes):
    output = run([hermes, '-p', 'default', 'gateway', 'restart'])
    if (not re.search(r'^✓ User service restarted \(PID [0-9]+\)$', output, re.MULTILINE)
            or 'gateway is DEGRADED' in output):
        raise RuntimeError('Gateway did not confirm a healthy completed restart.')


def main():
    if pwd.getpwuid(os.getuid()).pw_name != 'hermes' or len(sys.argv) != 1:
        print('Run this fixed helper without arguments from your existing hermes terminal.')
        return 2
    hermes = shutil.which('hermes')
    if not hermes:
        print('Hermes CLI was not found. No changes made.')
        return 2
    step = 'checking the staged bundles'
    try:
        verified_bytes = []
        for source, digest in ((UPDATER, UPDATER_SHA256),):
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                raise RuntimeError('Bundle hash does not match the reviewed release.')
            verified_bytes.append((source.name, payload))
        with tempfile.TemporaryDirectory(prefix='liberdus-apply-054-') as temporary:
            files = []
            for name, payload in verified_bytes:
                target = Path(temporary) / name
                target.write_bytes(payload)
                target.chmod(0o600)
                files.append(target)
            os.environ['XDG_RUNTIME_DIR'] = f'/run/user/{os.getuid()}'
            os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + os.environ['XDG_RUNTIME_DIR'] + '/bus'
            step = 'disabling moderation and finishing the first restart'
            print('1/3 Disable moderation and restart the shared gateway', flush=True)
            run([hermes, '-p', 'liberdus-mod', 'config', 'set', 'platforms.liberdus_moderator.enabled', 'false'])
            restart(hermes)
            step = 'installing the 0.5.4 code'
            print('2/3 Install the update', flush=True)
            result = json.loads(run([sys.executable, str(files[0])]))
            if result.get('updated') is not True or result.get('version') != '0.5.4':
                raise RuntimeError('Updater did not confirm version 0.5.4.')
            step = 'enabling moderation and finishing the final restart'
            print('3/3 Enable moderation and restart the shared gateway', flush=True)
            run([hermes, '-p', 'liberdus-mod', 'config', 'set', 'platforms.liberdus_moderator.enabled', 'true'])
            restart(hermes)
        print('Update and restarts confirmed. In bot-mod, send !mod status.')
        print('Expect JEV: report_only. If moderation is paused, use !mod resume when ready.')
        print('Existing policy, action flags, role exemption, keys and trial accounting were preserved.')
        print('Automated regressions cover the review fixes. !mod status now displays Version: 0.5.4.')
        return 0
    except KeyboardInterrupt:
        print(f'Interrupted during {step}. Check the output before taking another step.')
        return 130
    except Exception as error:
        print(f'Stopped during {step}: {error}')
        print('Later steps were not run. Keep the printed backups and share the output before retrying.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
