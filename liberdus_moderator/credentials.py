"""Explicit credential providers; no dotenv execution or environment-key fallback."""

import getpass
import os
from pathlib import Path
import stat
import warnings

from .secure_files import SetupError, checked_directory, read_private, write_private

NAMES = {"discord": "discord-token", "jev": "jev-key"}


def validate_secret(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or not value.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise SetupError("Enter a nonempty credential without spaces or control characters.")
    return value


def prompt_secret(label, prompt=None):
    # getpass otherwise silently falls back to echoed input on some terminals.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = (prompt or getpass.getpass)(label)
        except getpass.GetPassWarning:
            raise SetupError("A terminal with hidden input is required for credentials.") from None
    return validate_secret(value)


class Credentials:
    def __init__(self, home, *, backend="file", directory=None):
        if backend not in ("file", "systemd"):
            raise SetupError("Unknown credential storage mode.")
        self.home = Path(home)
        self.backend = backend
        self.directory = Path(directory) if directory is not None else None

    def __repr__(self):
        return f"Credentials(backend={self.backend!r})"

    def get(self, name):
        if name not in NAMES:
            raise SetupError("Unknown credential name.")
        if self.backend == "file":
            directory = checked_directory(self.home / "credentials")
        else:
            # systemd supplies a runtime-only, private directory. Never fall
            # back to an unrelated process environment key or portable file.
            raw = self.directory or os.environ.get("CREDENTIALS_DIRECTORY")
            if not raw:
                raise SetupError("Systemd credentials are only available inside the installed service.")
            directory = Path(raw)
            if not directory.is_absolute() or any(p.is_symlink() for p in (directory, *directory.parents)):
                raise SetupError("Invalid service credential directory.")
            info = directory.lstat()
            # systemd 255 can expose a root-owned, read-only credential mount
            # with group r-x. Accept only the exact mode and trusted identities;
            # portable directories still use checked_directory's private rule.
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
                    or info.st_gid not in (0, os.getgid())
                    or stat.S_IMODE(info.st_mode) != 0o550):
                raise SetupError("Service credentials have unsafe directory permissions.")
        return validate_secret(read_private(directory / NAMES[name], limit=4096,
                                            root_owned=self.backend == "systemd").decode("ascii").rstrip("\n"))

    def put(self, name, value, *, replace=False):
        if self.backend != "file" or name not in NAMES:
            raise SetupError("Use the service credential replacement command for this storage mode.")
        directory = checked_directory(self.home / "credentials", create=True)
        write_private(directory / NAMES[name], validate_secret(value), replace=replace)
