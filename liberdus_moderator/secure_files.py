"""Private, bounded local files. Errors never include file contents or secrets."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import pwd
import stat
import tempfile


class SetupError(ValueError):
    """Only fixed, safe operator-facing messages belong here."""


def checked_directory(path, *, create=False):
    path = Path(os.path.abspath(path))
    # Reject redirected parents as well as a redirected leaf. A sticky /tmp is
    # acceptable; the instance directory beneath it must still be private.
    for parent in reversed((path, *path.parents)):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if not create:
                raise SetupError("Instance directory does not exist. Run setup first.") from None
            parent.mkdir(mode=0o700)
            parent.chmod(0o700)
            info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SetupError("Directories must be real directories, without symlinks.")
        if info.st_uid not in (0, os.getuid()):
            raise SetupError("Directory ownership is not trusted.")
        if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise SetupError("A parent directory is writable by other users.")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SetupError("The instance directory must be owned by this user with permissions 700.")
    return path


def read_private(path, *, limit=65536, root_owned=False):
    path = Path(path)
    # The caller checks the containing directory, which is not writable by
    # another user. O_NOFOLLOW and fstat also protect the final open.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        owners = (0, os.getuid()) if root_owned else (os.getuid(),)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise SetupError("Files must be private, regular, owned files without hard links (permissions 600 or 400).")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise SetupError("A local file exceeds its size limit.")
    return data


def write_private(path, data, *, replace=False):
    path = Path(path)
    checked_directory(path.parent)
    if path.exists() or path.is_symlink():
        if not replace:
            raise SetupError("A destination file already exists; setup will not overwrite it.")
        read_private(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # Atomic publication without overwriting a file created meanwhile.
            os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary) if os.path.exists(temporary) else None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def file_lock(path):
    path = Path(path)
    checked_directory(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise SetupError("The lock file has unsafe ownership or permissions.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError("Another instance is running. Stop it before continuing.") from None
        yield fd
    finally:
        os.close(fd)


def token_lock(token):
    # A private path under the OS account's home cannot be pre-created by an
    # unrelated user, and works across different instance directories and
    # systemd PrivateTmp namespaces. Do not derive this from $HOME or cwd.
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    directory = checked_directory(account_home / ".liberdus-moderator-locks", create=True)
    return file_lock(directory / (hashlib.sha256(token.encode("ascii")).hexdigest() + ".lock"))
