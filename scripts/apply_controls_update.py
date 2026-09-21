"""Owner-run, hash-pinned 0.4.2 update and approved private screening activation."""
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

UPDATER = Path('/tmp/liberdus-update-0.4.2-20260921.pyz')
CONFIGURATOR = Path('/tmp/liberdus-controls-20260921.pyz')
UPDATER_SHA256 = '333f2ab4f2d618de6cab966fbd023d1e506bc4e4e35afde352ecbca649e572b9'
CONFIGURATOR_SHA256 = 'aa2e2785fad7703fbc9649659ed97ed12914177f3240083e0d53b3b6693de142'


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
        for source, digest in ((UPDATER, UPDATER_SHA256), (CONFIGURATOR, CONFIGURATOR_SHA256)):
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                raise RuntimeError('Bundle hash does not match the reviewed release.')
            verified_bytes.append((source.name, payload))
        with tempfile.TemporaryDirectory(prefix='liberdus-apply-042-') as temporary:
            files = []
            for name, payload in verified_bytes:
                target = Path(temporary) / name
                target.write_bytes(payload)
                target.chmod(0o600)
                files.append(target)
            os.environ['XDG_RUNTIME_DIR'] = f'/run/user/{os.getuid()}'
            os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + os.environ['XDG_RUNTIME_DIR'] + '/bus'
            step = 'disabling moderation and finishing the first restart'
            print('1/4 Disable moderation and restart the shared gateway', flush=True)
            run([hermes, '-p', 'liberdus-mod', 'config', 'set', 'platforms.liberdus_moderator.enabled', 'false'])
            restart(hermes)
            step = 'installing the 0.4.2 code'
            print('2/4 Install the update', flush=True)
            result = json.loads(run([sys.executable, str(files[0])]))
            if result.get('updated') is not True or result.get('version') != '0.4.2':
                raise RuntimeError('Updater did not confirm version 0.4.2.')
            step = 'configuring the approved screening trial'
            print('3/4 Exempt role 1302455329795342377 from JEV; preserve allowance', flush=True)
            result = json.loads(run([sys.executable, str(files[1]), 'exempt-role']))
            if result.get('configured') != 'exempt-role' or result.get('platform_enabled') is not False:
                raise RuntimeError('Screening setup did not confirm the disabled configuration.')
            step = 'enabling moderation and finishing the final restart'
            print('4/4 Enable moderation and restart the shared gateway', flush=True)
            run([hermes, '-p', 'liberdus-mod', 'config', 'set', 'platforms.liberdus_moderator.enabled', 'true'])
            restart(hermes)
        print('Update and restarts confirmed. In bot-mod, send !mod status.')
        print('Expect JEV: report_only. If moderation is paused, use !mod resume when ready.')
        print('Use !mod help and !mod exempt-role to check the saved toggle. Repetition checks remain active.')
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
