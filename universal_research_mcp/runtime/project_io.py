"""Protected project-local files for canonical writes and ingestion metadata.

POSIX operations are relative to no-follow directory descriptors. The portable
fallback checks every parent and rejects Windows reparse points before each
operation; it is not a sandbox against arbitrary same-user filesystem changes.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import stat
from typing import Iterator


_USE_DIRECTORY_FDS = os.name == "posix"


def _check_metadata(metadata: os.stat_result, *, directory: bool) -> None:
    if stat.S_ISLNK(metadata.st_mode) or (
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise ValueError("project write path contains a symlink or reparse point")
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("project write parent is not a directory")
    elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("project write target is not a regular single-link file")


def checked_project_path(root: Path, path: str | Path, *, directory: bool = False) -> Path:
    """Validate existing components without resolving away links or creating any."""

    supplied = Path(path)
    if ".." in supplied.parts:
        raise ValueError("project write path escapes root")
    target = supplied if supplied.is_absolute() else root / supplied
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("project write path escapes root") from exc
    if not relative.parts and not directory:
        raise ValueError("project write target must name a file")
    current = root
    components = ((), *[(part,) for part in relative.parts])
    for index, component in enumerate(components):
        current = current.joinpath(*component)
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            # Descendants cannot exist until this missing directory is created.
            break
        _check_metadata(metadata, directory=index < len(components) - 1 or directory)
    return target


class _Parent:
    def __init__(self, root: Path, path: Path, descriptor: int | None) -> None:
        self.root = root
        self.path = path
        self.descriptor = descriptor

    def _argument(self, name: str) -> str | Path:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("project file name must be a single path component")
        if self.descriptor is None:
            checked_project_path(self.root, self.path, directory=True)
            return self.path / name
        return name

    def metadata(self, name: str) -> os.stat_result | None:
        try:
            metadata = os.stat(
                self._argument(name), dir_fd=self.descriptor, follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        _check_metadata(metadata, directory=False)
        return metadata

    def _open(self, name: str, flags: int) -> int:
        self.metadata(name)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(self._argument(name), flags, 0o600, dir_fd=self.descriptor)
        try:
            opened = os.fstat(descriptor)
            _check_metadata(opened, directory=False)
            current = self.metadata(name)
            if current is None or not os.path.samestat(opened, current):
                raise ValueError("project write target changed while opening")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def sync(self) -> None:
        # Python has no portable directory fsync on Windows. File data is still
        # flushed with fsync before replace; do not claim POSIX power-loss parity.
        if self.descriptor is not None:
            os.fsync(self.descriptor)

    def read(self, name: str, *, max_bytes: int | None = None) -> bytes | None:
        try:
            descriptor = self._open(name, os.O_RDONLY)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as handle:
            payload = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
        if max_bytes is not None and len(payload) > max_bytes:
            raise ValueError("project file exceeds the maximum safe size")
        return payload

    def remove(self, name: str, *, identity: os.stat_result | None = None) -> None:
        current = self.metadata(name)
        if current is None:
            return
        if identity is not None and not os.path.samestat(identity, current):
            raise ValueError("project file changed before cleanup")
        os.unlink(self._argument(name), dir_fd=self.descriptor)
        self.sync()

    def create(self, name: str, payload: bytes) -> os.stat_result:
        descriptor = self._open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        identity = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self.sync()
        except BaseException:
            self.remove(name, identity=identity)
            raise
        return identity

    def replace(
        self, name: str, payload: bytes, *, expected: bytes | None = None,
        check_expected: bool = False,
    ) -> None:
        self.metadata(name)
        temporary = f".{name}.{secrets.token_hex(8)}.tmp"
        identity = self.create(temporary, payload)
        try:
            if check_expected and self.read(name) != expected:
                raise RuntimeError("project file changed before atomic replacement")
            self.metadata(name)
            os.replace(
                self._argument(temporary), self._argument(name),
                src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor,
            )
            self.sync()
        finally:
            self.remove(temporary, identity=identity)


class ProjectFiles:
    """Perform bounded file IO without following project-controlled links."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, path: str | Path, *, directory: bool = False) -> Path:
        return checked_project_path(self.root, path, directory=directory)

    @contextmanager
    def parent(self, path: str | Path, *, create: bool = False) -> Iterator[tuple[_Parent, str]]:
        target = self.path(path)
        parts = target.relative_to(self.root).parts
        if not _USE_DIRECTORY_FDS:
            current = self.root
            checked_project_path(self.root, current, directory=True)
            for component in parts[:-1]:
                current = current / component
                checked_project_path(self.root, current, directory=True)
                if create:
                    current.mkdir(exist_ok=True)
                _check_metadata(current.lstat(), directory=True)
            yield _Parent(self.root, target.parent, None), parts[-1]
            return

        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.root, flags)
        try:
            for component in parts[:-1]:
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield _Parent(self.root, target.parent, descriptor), parts[-1]
        finally:
            os.close(descriptor)

    def read(self, path: str | Path, *, max_bytes: int | None = None) -> bytes | None:
        try:
            with self.parent(path) as (parent, name):
                return parent.read(name, max_bytes=max_bytes)
        except FileNotFoundError:
            return None

    def exists(self, path: str | Path) -> bool:
        try:
            with self.parent(path) as (parent, name):
                return parent.metadata(name) is not None
        except FileNotFoundError:
            return False

    def create(self, path: str | Path, payload: bytes) -> None:
        with self.parent(path, create=True) as (parent, name):
            parent.create(name, payload)

    def sync(self, path: str | Path) -> None:
        """Finish durability on recovery even if a prior rename already applied."""
        with self.parent(path) as (parent, name):
            descriptor = parent._open(name, os.O_RDWR)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            parent.sync()

    def replace(
        self, path: str | Path, payload: bytes, *, expected: bytes | None = None,
        check_expected: bool = False,
    ) -> None:
        with self.parent(path, create=True) as (parent, name):
            parent.replace(name, payload, expected=expected, check_expected=check_expected)


__all__ = ["ProjectFiles", "checked_project_path"]
