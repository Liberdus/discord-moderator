"""Explicit administrator-run installation of a dedicated Linux system service.

No system files are changed by importing this module or rendering a unit. The
installer is for fresh installations; existing live instances use migration.
"""

import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import tempfile
import zipfile

from .credentials import NAMES, prompt_secret
from .secure_files import SetupError

USER = "liberdus-mod"
HOME = Path("/var/lib/liberdus-moderator")
PREFIX = Path("/opt/liberdus-moderator")
SECRETS = Path("/etc/liberdus-moderator")
UNIT = Path("/etc/systemd/system/liberdus-moderator.service")


def service_unit(*, ai, encrypted=True):
    credentials = []
    for name in ("discord-token", "jev-key") if ai else ("discord-token",):
        directive = "LoadCredentialEncrypted" if encrypted else "LoadCredential"
        suffix = ".cred" if encrypted else ""
        credentials.append(f"{directive}={name}:{SECRETS}/{name}{suffix}")
    return "\n".join([
        "[Unit]", "Description=Liberdus Discord Moderator", "Wants=network-online.target",
        "After=network-online.target", "StartLimitIntervalSec=300", "StartLimitBurst=3", "",
        "[Service]", "Type=simple", f"User={USER}", f"Group={USER}",
        f"WorkingDirectory={HOME}", "UMask=0077", "Environment=PYTHONDONTWRITEBYTECODE=1",
        *credentials,
        f"ExecStartPre={PREFIX}/venv/bin/python -m liberdus_moderator doctor --home {HOME}",
        f"ExecStart={PREFIX}/venv/bin/python -m liberdus_moderator start --home {HOME} --checked",
        "Restart=on-failure", "RestartSec=15", "TimeoutStopSec=75", "KillSignal=SIGTERM",
        "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true",
        f"ReadWritePaths={HOME}", "PrivateDevices=true", "PrivateMounts=true", "PrivateTmp=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", "RestrictSUIDSGID=true",
        "ProtectKernelTunables=true", "ProtectKernelModules=true", "ProtectKernelLogs=true",
        "ProtectControlGroups=true", "CapabilityBoundingSet=", "AmbientCapabilities=",
        "LockPersonality=true", "LimitCORE=0", "StandardOutput=journal", "StandardError=journal",
        "", "[Install]", "WantedBy=multi-user.target", "",
    ])


def _root():
    if os.geteuid() != 0:
        raise SetupError("This command creates a system service and requires sudo. Portable setup and start do not require sudo.")


def _run(arguments, *, data=None, interactive=False, timeout=180):
    try:
        result = subprocess.run(arguments, input=data, capture_output=not interactive,
                                timeout=timeout, check=False,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
                                umask=0o022)
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("A service installation command could not finish. No command output or credentials were printed.") from None
    if result.returncode:
        raise SetupError("A service installation command failed. Check the installation prerequisites; existing files were retained for inspection.")


def _fresh_directory(path, mode):
    if path.exists() or path.is_symlink():
        raise SetupError("A service destination already exists. The fresh installer will not overwrite it.")
    if path.parent.is_symlink() or path.parent.stat().st_uid != 0 or path.parent.stat().st_mode & 0o022:
        raise SetupError("Service parent directories must be root-owned and not writable by other users.")
    path.mkdir(mode=mode)
    path.chmod(mode)


def _owned_write(path, data, uid=0, gid=0, mode=0o600):
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".install-", dir=path.parent)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _credential_bytes(path, uid):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (info.st_uid != uid or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1):
            raise SetupError("The setup credential file has unsafe ownership or permissions.")
        data = stream.read(4097)
    from .credentials import validate_secret
    validate_secret(data.decode("ascii"))
    return data


def _store_credential(name, data, encrypted):
    destination = SECRETS / (name + ".cred" if encrypted else name)
    if encrypted:
        # Secret bytes go through stdin, never arguments, environment, or logs.
        fd, temporary = tempfile.mkstemp(prefix=".encrypt-", dir=SECRETS)
        os.close(fd)
        try:
            # systemd-creds refuses to overwrite an existing output file.
            Path(temporary).unlink()
            _run(["systemd-creds", "encrypt", "--with-key=host", "--name=" + name,
                  "-", temporary], data=data, timeout=20)
            Path(temporary).chmod(0o600)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    else:
        _owned_write(destination, data, mode=0o400)


def install(wheel, *, encrypted=True, start=False, output=print):
    _root()
    wheel = Path(wheel).absolute()
    if not wheel.is_file() or wheel.is_symlink() or wheel.stat().st_size > 16_000_000:
        raise SetupError("Provide the locally built moderator wheel as a regular file.")
    from . import __version__
    if wheel.name != f"liberdus_discord_moderator-{__version__}-py3-none-any.whl":
        raise SetupError("The wheel must match this installer's moderator release.")
    with zipfile.ZipFile(wheel) as archive:
        if (len(archive.infolist()) > 1000 or sum(item.file_size for item in archive.infolist()) > 32_000_000
                or archive.testzip() is not None or "liberdus_moderator/standalone.py" not in archive.namelist()):
            raise SetupError("The moderator wheel failed its integrity check.")
    for executable in ("systemctl", "useradd", "runuser", "python3", *(('systemd-creds',) if encrypted else ())):
        if shutil.which(executable) is None:
            raise SetupError("Install Python with venv support and systemd (including systemd-creds for encrypted storage) first.")
    if any(path.exists() or path.is_symlink() for path in (HOME, PREFIX, SECRETS, UNIT)):
        raise SetupError("A system installation already exists. Use the documented update or recovery procedure.")
    try:
        pwd.getpwnam(USER)
    except KeyError:
        pass
    else:
        raise SetupError("The dedicated account already exists. Review it before using the fresh installer.")
    output("Installing a dedicated account and service. Setup will ask for credentials privately.")
    output("Credential protection: " + ("encrypted on disk using this host's systemd key" if encrypted else "root-only files, not encrypted on disk"))
    committed = user_created = False
    created_directories = []
    try:
        _fresh_directory(PREFIX, 0o755)
        created_directories.append(PREFIX)
        saved_wheel = PREFIX / wheel.name
        _owned_write(saved_wheel, wheel.read_bytes(), mode=0o644)
        _run(["python3", "-m", "venv", str(PREFIX / "venv")])
        python = str(PREFIX / "venv/bin/python")
        _run([python, "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
              "--index-url", "https://pypi.org/simple", str(saved_wheel) + "[discord]"])
        _run(["useradd", "--system", "--user-group", "--home-dir", str(HOME),
              "--shell", "/usr/sbin/nologin", USER])
        user_created = True
        user = pwd.getpwnam(USER)
        _fresh_directory(HOME, 0o700)
        created_directories.append(HOME)
        os.chown(HOME, user.pw_uid, user.pw_gid)
        _run(["runuser", "-u", USER, "--", python, "-m", "liberdus_moderator", "setup",
              "--home", str(HOME)], interactive=True, timeout=3600)
        marker = HOME / "instance.json"
        if not marker.is_file() or marker.is_symlink():
            raise SetupError("Setup was cancelled or incomplete. The service was not installed or started.")
        import tomllib
        from .config import Config
        policy = Config.from_dict(tomllib.loads((HOME / "moderation.toml").read_text()))
        _fresh_directory(SECRETS, 0o700)
        created_directories.append(SECRETS)
        names = ("discord-token", "jev-key") if policy.ai_enabled else ("discord-token",)
        for name in names:
            _store_credential(name, _credential_bytes(HOME / "credentials" / name, user.pw_uid), encrypted)
        _owned_write(marker, json.dumps({"schema": 1, "credentials": "systemd"}) + "\n", user.pw_uid, user.pw_gid)
        # Publish every service credential before removing the portable originals.
        # Unlinking is not a guarantee of erasure from snapshots or the storage device.
        for name in names:
            (HOME / "credentials" / name).unlink()
        (HOME / "credentials").rmdir()
        _owned_write(SECRETS / "storage.json", json.dumps({"encrypted": encrypted, "ai": policy.ai_enabled}) + "\n")
        _owned_write(UNIT, service_unit(ai=policy.ai_enabled, encrypted=encrypted), mode=0o644)
        committed = True
        _run(["systemctl", "daemon-reload"])
        if start:
            _run(["systemctl", "enable", "--now", "liberdus-moderator.service"])
            output("Service installed and started. Check: systemctl status liberdus-moderator")
        else:
            output("Service installed. Start it with: sudo systemctl enable --now liberdus-moderator")
    except BaseException:
        # Only fresh, not-yet-published installations are rolled back. Once a
        # unit exists, a failed start may already have connected: retain it.
        if not committed:
            for directory in reversed(created_directories):
                if directory.is_dir() and not directory.is_symlink():
                    shutil.rmtree(directory)
            if user_created:
                try:
                    _run(["userdel", USER])
                except SetupError:
                    pass
        raise


def replace_service_key(name, *, secret_prompt=None, output=print):
    _root()
    if name not in NAMES:
        raise SetupError("Choose the discord or jev credential.")
    from .secure_files import checked_directory, read_private
    checked_directory(SECRETS)
    metadata = json.loads(read_private(SECRETS / "storage.json"))
    if (set(metadata) != {"encrypted", "ai"} or type(metadata["encrypted"]) is not bool
            or type(metadata["ai"]) is not bool or (name == "jev" and not metadata["ai"])):
        raise SetupError("This service does not have the requested credential configured.")
    output("Replacing this credential will restart the installed moderation service.")
    value = prompt_secret("New credential (hidden): ", secret_prompt)
    _store_credential(NAMES[name], value.encode("ascii"), metadata["encrypted"])
    _run(["systemctl", "restart", "liberdus-moderator.service"])
    output("Credential replaced and service restarted.")
