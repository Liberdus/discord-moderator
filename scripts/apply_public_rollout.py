"""Owner-run public observation rollout; verifies a saved plan before stopping Hermes."""
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

UPDATER = Path('/tmp/liberdus-update-0.5.7-20260921.pyz')
UPDATER_SHA256 = '17a731c307b486b39be0e6ff465b6cd5cd2cf93ce08b960c994db53b6d89f8cf'
ROLLOUT = Path('/tmp/liberdus-category-rollout-20260921.pyz')
ROLLOUT_SHA256 = '6880bb50753445dc39b6543371f38d824f24309727076e7bae2811994941e951'


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
    step = 'checking the staged bundles and saved channel plan'
    try:
        verified_bytes = []
        for source, digest in ((UPDATER, UPDATER_SHA256), (ROLLOUT, ROLLOUT_SHA256)):
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                raise RuntimeError('Bundle hash does not match the reviewed release.')
            verified_bytes.append((source.name, payload))
        with tempfile.TemporaryDirectory(prefix='liberdus-apply-categories-057-') as temporary:
            files = []
            for name, payload in verified_bytes:
                target = Path(temporary) / name
                target.write_bytes(payload)
                target.chmod(0o600)
                files.append(target)
            verified = json.loads(run([sys.executable, str(files[1]), 'verify']))
            if verified.get('verified') is not True or verified.get('actions_enabled') is not False:
                raise RuntimeError('Saved public observation plan did not pass verification.')
            os.environ['XDG_RUNTIME_DIR'] = f'/run/user/{os.getuid()}'
            os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + os.environ['XDG_RUNTIME_DIR'] + '/bus'
            step = 'disabling moderation and finishing the first restart'
            print('1/4 Disable moderation and restart the shared gateway', flush=True)
            run([hermes, '-p', 'liberdus-mod', 'config', 'set', 'platforms.liberdus_moderator.enabled', 'false'])
            restart(hermes)
            step = 'installing the 0.5.7 code'
            print('2/4 Install the update', flush=True)
            result = json.loads(run([sys.executable, str(files[0])]))
            if result.get('updated') is not True or result.get('version') != '0.5.7':
                raise RuntimeError('Updater did not confirm version 0.5.7.')
            step = 'applying the verified channel scope and disabling actions'
            print('3/4 Apply public observation scope; disable all moderation actions', flush=True)
            result = json.loads(run([sys.executable, str(files[1]), 'apply']))
            if (result.get('configured') is not True or result.get('actions_enabled') is not False
                    or result.get('action_flags_reset') is not True or result.get('platform_enabled') is not False):
                raise RuntimeError('Scope helper did not confirm report-only policy and action switches OFF.')
            step = 'enabling moderation and finishing the final restart'
            print('4/4 Enable observation and restart the shared gateway', flush=True)
            run([hermes, '-p', 'liberdus-mod', 'config', 'set', 'platforms.liberdus_moderator.enabled', 'true'])
            restart(hermes)
        print('Public observation configured and gateway restarts confirmed.')
        print('In bot-mod, use !mod status and !mod summary. Expect Version: 0.5.7 and all action switches OFF.')
        print('Committers is excluded. Only the saved public text-channel IDs inside the two approved categories are monitored; new channels are not added automatically.')
        print('JEV budgets, usage, keys, review history and pause state were preserved. If paused, use !mod resume when ready.')
        return 0
    except KeyboardInterrupt:
        print(f'Interrupted during {step}. Inspect output before taking another step.')
        return 130
    except Exception as error:
        print(f'Stopped during {step}: {error}')
        print('Later steps were not run. Keep the printed backups and share the output before retrying.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
