#!/usr/bin/env python3
"""Synchronize host adapter skill bundles from the canonical core skill."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import stat
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator, NamedTuple


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_SKILL = REPO_ROOT / "core" / "skill" / "map-project"
ADAPTER_SKILLS = {
    "codex": REPO_ROOT / "adapters" / "codex" / "skills" / "map-project",
    "claude-code": REPO_ROOT / "adapters" / "claude-code" / "skills" / "map-project",
}
CODEX_METADATA_PATH = PurePosixPath("agents/openai.yaml")
SYNC_STATE_DIR = REPO_ROOT / ".scratch"
SYNC_LOCK_PATH = SYNC_STATE_DIR / "sync-adapters.lock"
SYNC_JOURNAL_PATH = SYNC_STATE_DIR / "sync-adapters.journal.json"
JOURNAL_VERSION = 1
IGNORED_DIRECTORY_NAMES = {"__pycache__"}
IGNORED_FILE_NAMES = {".DS_Store"}
IGNORED_FILE_SUFFIXES = {".pyc", ".pyo"}
MAX_TREE_DEPTH = 32
MAX_TREE_DIRECTORIES = 2_048
MAX_TREE_FILES = 4_096
MAX_TREE_ENTRIES = 8_192
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
CODEX_METADATA = b'''interface:
  display_name: "Project Atlas"
  short_description: "Map project architecture, runtime, state, authority, and risks"
  default_prompt: "Use $project-atlas:map-project to create a verifiable atlas of this project."
'''
HANDOFF_TEMPLATE_PATHS = {
    PurePosixPath("assets/templates/standard/LIVE_HANDOFF.md"),
    PurePosixPath("assets/templates/forensic/LIVE_HANDOFF.md"),
}
CORE_SEARCH_ROOTS = (
    b'atlas_default_roots="${PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS:-}"'
)
ADAPTER_SEARCH_ROOTS = (
    b'atlas_default_roots="${PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS:-$HOME/.agents/skills:'
    b'${CODEX_HOME:-$HOME/.codex}/skills:${CODEX_HOME:-$HOME/.codex}/plugins/cache:'
    b'${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills:'
    b'${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache}"'
)


class SyncError(RuntimeError):
    """Raised when the canonical source cannot be packaged safely."""


class ObjectIdentity(NamedTuple):
    device: int
    inode: int
    file_type: int


class AdapterPlan(NamedTuple):
    adapter: str
    target: Path
    staging: Path
    previous: Path
    had_target: bool
    parent_identity: ObjectIdentity
    staging_identity: ObjectIdentity
    target_identity: ObjectIdentity | None
    directories: set[PurePosixPath]
    files: dict[PurePosixPath, tuple[bytes, int]]


class TreeSnapshot(NamedTuple):
    directories: set[PurePosixPath]
    files: set[PurePosixPath]
    symlinks: set[PurePosixPath]
    special_nodes: set[PurePosixPath]
    contents: dict[PurePosixPath, tuple[bytes, int]]


class TreeBudget:
    """Mutable resource accounting shared by one bounded descriptor walk."""

    def __init__(self) -> None:
        self.entries = 0
        self.directories = 0
        self.files = 0
        self.total_bytes = 0


@contextmanager
def repository_lock() -> Iterator[None]:
    """Serialize checks and writes with an OS-released lock anchored inside the repository."""
    assert_repo_path(SYNC_STATE_DIR, include_leaf=True, label="synchronization state directory")
    SYNC_STATE_DIR.mkdir(parents=True, exist_ok=True)
    assert_repo_path(SYNC_LOCK_PATH, include_leaf=False, label="synchronization lock")
    if SYNC_LOCK_PATH.is_symlink():
        raise SyncError("synchronization lock must not be a symbolic link")

    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(SYNC_LOCK_PATH, flags, 0o600)
    except OSError as exc:
        raise SyncError("cannot open the repository synchronization lock") from exc

    lock_file = os.fdopen(descriptor, "r+b", buffering=0)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(lock_file.fileno()).st_size == 0:
                lock_file.write(b"\0")
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SyncError("another synchronization owns the repository lock") from exc
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise SyncError("another synchronization owns the repository lock") from exc
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


@contextmanager
def repository_check_guard() -> Iterator[None]:
    """Observe synchronization state without creating or modifying repository files."""
    assert_repo_path(
        SYNC_STATE_DIR,
        include_leaf=True,
        label="synchronization state directory",
    )
    initial_state = object_identity(SYNC_STATE_DIR)
    if initial_state is None:
        yield
        if object_identity(SYNC_STATE_DIR) is not None:
            raise SyncError("synchronization state changed during read-only check")
        return
    if initial_state.file_type != stat.S_IFDIR:
        raise SyncError("synchronization state path is not a directory")
    if object_identity(SYNC_JOURNAL_PATH) is not None:
        raise SyncError("unfinished synchronization journal requires a recovery run")

    lock_identity = object_identity(SYNC_LOCK_PATH)
    descriptor: int | None = None
    acquired = False
    try:
        if lock_identity is not None:
            if lock_identity.file_type != stat.S_IFREG:
                raise SyncError("synchronization lock is not a regular file")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(SYNC_LOCK_PATH, flags)
            opened = os.fstat(descriptor)
            if _identity_from_stat(opened) != lock_identity:
                raise SyncError("synchronization lock changed before read-only check")
            if os.name == "nt":
                import msvcrt

                if opened.st_size < 1:
                    raise SyncError("synchronization lock is empty or unsafe")
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise SyncError(
                        "another synchronization owns the repository lock"
                    ) from exc
            else:
                import fcntl

                try:
                    fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except OSError as exc:
                    raise SyncError(
                        "another synchronization owns the repository lock"
                    ) from exc
            acquired = True

        yield
    finally:
        if acquired and descriptor is not None:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        if descriptor is not None:
            os.close(descriptor)

    if object_identity(SYNC_STATE_DIR) != initial_state:
        raise SyncError("synchronization state changed during read-only check")
    if object_identity(SYNC_LOCK_PATH) != lock_identity:
        raise SyncError("synchronization lock changed during read-only check")
    if object_identity(SYNC_JOURNAL_PATH) is not None:
        raise SyncError("synchronization journal appeared during read-only check")


def assert_repo_path(path: Path, *, include_leaf: bool, label: str) -> None:
    """Reject symlinked path components and paths resolving outside the repository."""
    inspected = path if include_leaf else path.parent
    try:
        relative = inspected.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise SyncError(f"{label} is outside the repository: {inspected}") from exc

    repository = REPO_ROOT.resolve(strict=True)
    current = REPO_ROOT
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SyncError(f"{label} contains a symlinked path component: {current}")
        if current.exists():
            try:
                current.resolve(strict=True).relative_to(repository)
            except ValueError as exc:
                raise SyncError(f"{label} resolves outside the repository: {current}") from exc

    try:
        inspected.resolve(strict=False).relative_to(repository)
    except ValueError as exc:
        raise SyncError(f"{label} resolves outside the repository: {inspected}") from exc


def is_generated_artifact(relative: PurePosixPath) -> bool:
    return (
        any(part in IGNORED_DIRECTORY_NAMES for part in relative.parts)
        or relative.name in IGNORED_FILE_NAMES
        or relative.suffix in IGNORED_FILE_SUFFIXES
    )


def _identity_from_stat(details: os.stat_result) -> ObjectIdentity:
    return ObjectIdentity(
        device=details.st_dev,
        inode=details.st_ino,
        file_type=stat.S_IFMT(details.st_mode),
    )


def _stable_stat_signature(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev,
        details.st_ino,
        stat.S_IFMT(details.st_mode),
        stat.S_IMODE(details.st_mode),
        details.st_nlink,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _windows_open_descriptor(path: Path, *, directory: bool) -> int:
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    desired_access = 0x00000080 if directory else 0x80000000
    share_mode = 0x00000001 | 0x00000002
    if not directory:
        share_mode |= 0x00000004
    flags = 0x00200000 | (0x02000000 if directory else 0)
    handle = create_file(
        os.fspath(path),
        desired_access,
        share_mode,
        None,
        3,
        flags,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        raise OSError(error, "CreateFileW failed")
    try:
        descriptor_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        return msvcrt.open_osfhandle(handle, descriptor_flags)
    except BaseException:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise


def _open_directory_descriptor(
    path: Path,
    expected: ObjectIdentity,
    *,
    parent_descriptor: int | None = None,
    name: str | None = None,
) -> int:
    try:
        if os.name == "nt":
            descriptor = _windows_open_descriptor(path, directory=True)
        else:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            if parent_descriptor is None:
                descriptor = os.open(path, flags)
            else:
                assert name is not None
                descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SyncError("directory changed while its tree was being verified") from exc
    opened = os.fstat(descriptor)
    if _identity_from_stat(opened) != expected or not stat.S_ISDIR(opened.st_mode):
        os.close(descriptor)
        raise SyncError("directory identity changed while its tree was being verified")
    return descriptor


def _stat_child(parent_descriptor: int, parent_path: Path, name: str) -> os.stat_result:
    try:
        if os.name == "nt":
            return os.lstat(parent_path / name)
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise SyncError("tree entry changed while its type was being verified") from exc


def read_verified_entry(
    parent_descriptor: int,
    parent_path: Path,
    name: str,
    expected: ObjectIdentity,
    label: str,
) -> tuple[bytes, int]:
    """Read one regular file through a no-follow descriptor tied to its checked identity."""
    if not name or name in {".", ".."} or Path(name).name != name:
        raise SyncError(f"{label} has an unsafe entry name")
    if expected.file_type != stat.S_IFREG:
        raise SyncError(f"{label} is no longer a regular file")
    try:
        if os.name == "nt":
            descriptor = _windows_open_descriptor(parent_path / name, directory=False)
        else:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SyncError(f"{label} changed before it could be read") from exc
    try:
        before = os.fstat(descriptor)
        if _identity_from_stat(before) != expected or not stat.S_ISREG(before.st_mode):
            raise SyncError(f"{label} changed before it could be read")
        if before.st_nlink != 1:
            raise SyncError(f"{label} has an unsupported hard link")
        if before.st_size < 0 or before.st_size > MAX_FILE_BYTES:
            raise SyncError(f"{label} exceeds the per-file byte limit")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise SyncError(f"{label} changed while it was being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise SyncError(f"{label} changed or exceeded its byte limit while read")
        after = os.fstat(descriptor)
        if _stable_stat_signature(after) != _stable_stat_signature(before):
            raise SyncError(f"{label} changed while it was being read")
        if after.st_nlink != 1:
            raise SyncError(f"{label} acquired an unsupported hard link")
        return b"".join(chunks), stat.S_IMODE(before.st_mode)
    finally:
        os.close(descriptor)


def _list_directory(
    parent_descriptor: int,
    parent_path: Path,
    *,
    budget: TreeBudget | None = None,
) -> list[str]:
    try:
        names: list[str] = []
        with os.scandir(parent_path if os.name == "nt" else parent_descriptor) as entries:
            for entry in entries:
                name = entry.name
                if not isinstance(name, str) or not name or Path(name).name != name:
                    raise SyncError("directory contains an unsafe entry name")
                names.append(name)
                if len(names) > MAX_TREE_ENTRIES:
                    raise SyncError("tree exceeds the entry count limit")
                if budget is not None:
                    budget.entries += 1
                    if budget.entries > MAX_TREE_ENTRIES:
                        raise SyncError("tree exceeds the entry count limit")
    except OSError as exc:
        raise SyncError("directory changed while its entries were being listed") from exc
    return sorted(names)


def _scan_tree_directory(
    descriptor: int,
    path: Path,
    relative_parent: PurePosixPath,
    snapshot: TreeSnapshot,
    read_paths: set[PurePosixPath] | None,
    budget: TreeBudget,
) -> None:
    before = os.fstat(descriptor)
    names = _list_directory(descriptor, path, budget=budget)
    for name in names:
        relative = relative_parent / name
        if len(relative.parts) > MAX_TREE_DEPTH:
            raise SyncError("tree exceeds the depth limit")
        if is_generated_artifact(relative):
            continue
        details = _stat_child(descriptor, path, name)
        identity = _identity_from_stat(details)
        if stat.S_ISLNK(details.st_mode):
            snapshot.symlinks.add(relative)
        elif stat.S_ISDIR(details.st_mode):
            budget.directories += 1
            if budget.directories > MAX_TREE_DIRECTORIES:
                raise SyncError("tree exceeds the directory count limit")
            snapshot.directories.add(relative)
            child_path = path / name
            child_descriptor = _open_directory_descriptor(
                child_path,
                identity,
                parent_descriptor=descriptor,
                name=name,
            )
            try:
                _scan_tree_directory(
                    child_descriptor,
                    child_path,
                    relative,
                    snapshot,
                    read_paths,
                    budget,
                )
            finally:
                os.close(child_descriptor)
        elif stat.S_ISREG(details.st_mode):
            if details.st_nlink != 1:
                raise SyncError(
                    f"tree entry {relative.as_posix()} has an unsupported hard link"
                )
            budget.files += 1
            if budget.files > MAX_TREE_FILES:
                raise SyncError("tree exceeds the file count limit")
            if details.st_size < 0 or details.st_size > MAX_FILE_BYTES:
                raise SyncError(
                    f"tree entry {relative.as_posix()} exceeds the per-file byte limit"
                )
            budget.total_bytes += details.st_size
            if budget.total_bytes > MAX_TOTAL_BYTES:
                raise SyncError("tree exceeds the aggregate byte limit")
            snapshot.files.add(relative)
            if read_paths is None or relative in read_paths:
                content = read_verified_entry(
                    descriptor,
                    path,
                    name,
                    identity,
                    f"tree entry {relative.as_posix()}",
                )
                if len(content[0]) != details.st_size:
                    raise SyncError(
                        f"tree entry {relative.as_posix()} changed while it was being read"
                    )
                snapshot.contents[relative] = content
        else:
            snapshot.special_nodes.add(relative)
    after_names = _list_directory(descriptor, path)
    after = os.fstat(descriptor)
    if after_names != names or _stable_stat_signature(after) != _stable_stat_signature(before):
        raise SyncError("directory changed while its tree was being verified")


def snapshot_tree(
    root: Path,
    *,
    read_paths: set[PurePosixPath] | None,
    expected_root: ObjectIdentity,
) -> TreeSnapshot:
    snapshot = TreeSnapshot(set(), set(), set(), set(), {})
    budget = TreeBudget()
    descriptor = _open_directory_descriptor(root, expected_root)
    try:
        _scan_tree_directory(
            descriptor,
            root,
            PurePosixPath(),
            snapshot,
            read_paths,
            budget,
        )
    finally:
        os.close(descriptor)
    return snapshot


def canonical_tree() -> tuple[set[PurePosixPath], dict[PurePosixPath, tuple[bytes, int]]]:
    assert_repo_path(CORE_SKILL, include_leaf=True, label="canonical skill")
    root_identity = object_identity(CORE_SKILL)
    if root_identity is None or root_identity.file_type != stat.S_IFDIR:
        raise SyncError(f"canonical skill directory is missing: {CORE_SKILL}")
    snapshot = snapshot_tree(CORE_SKILL, read_paths=None, expected_root=root_identity)
    if PurePosixPath("SKILL.md") not in snapshot.files:
        raise SyncError(f"canonical skill entrypoint is missing: {CORE_SKILL / 'SKILL.md'}")
    all_nodes = (
        snapshot.directories | snapshot.files | snapshot.symlinks | snapshot.special_nodes
    )
    if CODEX_METADATA_PATH in all_nodes:
        raise SyncError(
            "Codex-only agents/openai.yaml must live in the Codex adapter, not canonical core"
        )
    if snapshot.symlinks:
        raise SyncError(
            f"canonical skill must not contain symlinks: {min(snapshot.symlinks)}"
        )
    if snapshot.special_nodes:
        raise SyncError(f"unsupported canonical path type: {min(snapshot.special_nodes)}")
    if not snapshot.contents:
        raise SyncError(f"canonical skill is empty: {CORE_SKILL}")
    return snapshot.directories, snapshot.contents


def expected_tree(
    adapter: str,
    canonical_directories: set[PurePosixPath],
    canonical_files: dict[PurePosixPath, tuple[bytes, int]],
) -> tuple[set[PurePosixPath], dict[PurePosixPath, tuple[bytes, int]]]:
    directories = set(canonical_directories)
    files = dict(canonical_files)
    for relative in HANDOFF_TEMPLATE_PATHS:
        if relative not in files:
            raise SyncError(f"canonical handoff template is missing: {relative}")
        content, mode = files[relative]
        if content.count(CORE_SEARCH_ROOTS) != 1:
            raise SyncError(f"canonical handoff search-root contract drifted: {relative}")
        files[relative] = (content.replace(CORE_SEARCH_ROOTS, ADAPTER_SEARCH_ROOTS), mode)
    if adapter == "codex":
        directories.add(CODEX_METADATA_PATH.parent)
        files[CODEX_METADATA_PATH] = (CODEX_METADATA, 0o644)
    return directories, files


def inspect_drift(
    target: Path,
    expected_directories: set[PurePosixPath],
    expected_files: dict[PurePosixPath, tuple[bytes, int]],
) -> list[str]:
    root_identity = object_identity(target)
    if root_identity is None or root_identity.file_type != stat.S_IFDIR:
        return [f"{target.relative_to(REPO_ROOT)} is missing or is not a directory"]
    snapshot = snapshot_tree(
        target,
        read_paths=set(expected_files),
        expected_root=root_identity,
    )
    actual_directories = snapshot.directories
    actual_files = snapshot.files

    drift: list[str] = []
    drift.extend(f"unexpected symlink: {path}" for path in sorted(snapshot.symlinks))
    drift.extend(
        f"unexpected special node: {path}" for path in sorted(snapshot.special_nodes)
    )
    drift.extend(
        f"missing directory: {path}" for path in sorted(expected_directories - actual_directories)
    )
    drift.extend(
        f"unexpected directory: {path}" for path in sorted(actual_directories - expected_directories)
    )
    drift.extend(f"missing file: {path}" for path in sorted(set(expected_files) - actual_files))
    drift.extend(f"unexpected file: {path}" for path in sorted(actual_files - set(expected_files)))

    for relative in sorted(set(expected_files) & actual_files):
        expected_bytes, expected_mode = expected_files[relative]
        actual_bytes, actual_mode = snapshot.contents[relative]
        if actual_bytes != expected_bytes:
            drift.append(f"content differs: {relative}")
        if os.name != "nt" and actual_mode != expected_mode:
            drift.append(f"mode differs: {relative}")
    return drift


def object_identity(path: Path) -> ObjectIdentity | None:
    """Return a no-follow object identity, or None when the path is absent."""
    try:
        details = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return ObjectIdentity(
        device=details.st_dev,
        inode=details.st_ino,
        file_type=stat.S_IFMT(details.st_mode),
    )


def _require_identity(path: Path, expected: ObjectIdentity, *, label: str) -> None:
    if object_identity(path) != expected:
        raise SyncError(f"{label} changed ownership during synchronization")


def _require_absent(path: Path, *, label: str) -> None:
    if object_identity(path) is not None:
        raise SyncError(f"{label} is unexpectedly occupied")


def _revalidate_parent(plan: AdapterPlan) -> None:
    assert_repo_path(
        plan.target.parent,
        include_leaf=True,
        label=f"{plan.adapter} transaction parent",
    )
    _require_identity(
        plan.target.parent,
        plan.parent_identity,
        label=f"{plan.adapter} transaction parent",
    )


def _raise_rename_error(error: int) -> None:
    if error in {errno.EEXIST, errno.ENOTEMPTY, 80, 145, 183}:
        raise SyncError("transaction destination appeared before the atomic move")
    if error in {errno.ENOSYS, errno.EINVAL, getattr(errno, "ENOTSUP", -1)}:
        raise SyncError("filesystem does not support atomic no-replace moves")
    raise OSError(error, os.strerror(error) if error < 256 else "atomic move failed")


def rename_noreplace(
    source: Path,
    destination: Path,
    parent_identity: ObjectIdentity,
) -> None:
    """Atomically rename one child without replacing a concurrently-created destination."""
    if source.parent != destination.parent:
        raise SyncError("atomic transaction move crossed directory boundaries")
    if os.name == "nt":
        raise SyncError(
            "Windows synchronization is disabled because a secure handle-relative "
            "no-replace rename is unavailable"
        )
    parent_descriptor = _open_directory_descriptor(source.parent, parent_identity)
    try:
        library = ctypes.CDLL(None, use_errno=True)
        source_name = os.fsencode(source.name)
        destination_name = os.fsencode(destination.name)
        if sys.platform == "darwin":
            rename_call = library.renameatx_np
            rename_call.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename_call.restype = ctypes.c_int
            result = rename_call(
                parent_descriptor,
                source_name,
                parent_descriptor,
                destination_name,
                0x00000004,
            )
        elif sys.platform.startswith("linux"):
            try:
                rename_call = library.renameat2
            except AttributeError as exc:
                raise SyncError("platform lacks an atomic no-replace rename primitive") from exc
            rename_call.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename_call.restype = ctypes.c_int
            result = rename_call(
                parent_descriptor,
                source_name,
                parent_descriptor,
                destination_name,
                0x00000001,
            )
        else:
            raise SyncError("platform lacks an atomic no-replace rename primitive")
        if result != 0:
            _raise_rename_error(ctypes.get_errno())
    finally:
        os.close(parent_descriptor)


def _move_owned_path(
    plan: AdapterPlan,
    source: Path,
    destination: Path,
    expected: ObjectIdentity,
) -> None:
    if source.parent != plan.target.parent or destination.parent != plan.target.parent:
        raise SyncError("transaction move escaped its verified adapter parent")
    _revalidate_parent(plan)
    _require_identity(source, expected, label=f"{plan.adapter} transaction source")
    _require_absent(destination, label=f"{plan.adapter} transaction destination")
    _revalidate_parent(plan)
    _require_identity(source, expected, label=f"{plan.adapter} transaction source")
    _require_absent(destination, label=f"{plan.adapter} transaction destination")
    rename_noreplace(source, destination, plan.parent_identity)
    _revalidate_parent(plan)
    moved = object_identity(destination)
    if moved != expected:
        if moved is not None and object_identity(source) is None:
            try:
                rename_noreplace(destination, source, plan.parent_identity)
            except (OSError, SyncError) as restore_error:
                raise SyncError(
                    f"{plan.adapter} transaction moved a foreign source replacement "
                    "and could not restore it"
                ) from restore_error
            if object_identity(source) != moved:
                raise SyncError(
                    f"{plan.adapter} restored transaction replacement changed ownership"
                )
        raise SyncError(f"{plan.adapter} transaction source changed during atomic move")
    _require_identity(destination, expected, label=f"{plan.adapter} moved transaction object")


def _remove_owned_path(plan: AdapterPlan, path: Path, expected: ObjectIdentity) -> None:
    """Quarantine an owned object atomically before removing its contents.

    Moving the checked name to an unpredictable sibling makes a replacement at the
    public transaction path harmless.  The identity is checked again after that
    atomic move; an object that won the race is restored and never deleted.
    """
    if path.parent != plan.target.parent:
        raise SyncError("transaction cleanup escaped its verified adapter parent")
    if expected.file_type != stat.S_IFDIR:
        raise SyncError("transaction cleanup supports owned directory trees only")
    _revalidate_parent(plan)
    _require_identity(path, expected, label=f"{plan.adapter} transaction cleanup object")
    quarantine = path.parent / f".{path.name}.cleanup-{uuid.uuid4().hex}"
    _require_absent(quarantine, label=f"{plan.adapter} transaction cleanup quarantine")
    rename_noreplace(path, quarantine, plan.parent_identity)
    moved_identity = object_identity(quarantine)
    if moved_identity != expected:
        if object_identity(path) is None and moved_identity is not None:
            try:
                rename_noreplace(quarantine, path, plan.parent_identity)
            except (OSError, SyncError) as restore_error:
                raise SyncError(
                    f"{plan.adapter} cleanup quarantined a foreign replacement and "
                    "could not restore it"
                ) from restore_error
        raise SyncError(
            f"{plan.adapter} transaction cleanup object changed before quarantine"
        )
    _remove_quarantined_directory(plan, quarantine, expected)
    _revalidate_parent(plan)
    _require_absent(path, label=f"{plan.adapter} cleaned transaction path")


def _remove_quarantined_directory(
    plan: AdapterPlan,
    quarantine: Path,
    expected: ObjectIdentity,
) -> None:
    """Remove a quarantined directory without following paths outside its descriptor."""
    if os.name == "nt":
        raise SyncError(
            "Windows synchronization is disabled because secure descriptor-bound "
            "directory cleanup is unavailable"
        )
    parent_descriptor = _open_directory_descriptor(
        quarantine.parent,
        plan.parent_identity,
    )
    descriptor: int | None = None
    try:
        descriptor = _open_directory_descriptor(
            quarantine,
            expected,
            parent_descriptor=parent_descriptor,
            name=quarantine.name,
        )
        _remove_directory_contents(descriptor, quarantine)
        if _identity_from_stat(os.fstat(descriptor)) != expected:
            raise SyncError(f"{plan.adapter} quarantined cleanup directory changed")
        current = os.stat(
            quarantine.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if _identity_from_stat(current) != expected:
            raise SyncError(f"{plan.adapter} quarantined cleanup directory changed")
        os.rmdir(quarantine.name, dir_fd=parent_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)
    _require_absent(
        quarantine,
        label=f"{plan.adapter} quarantined cleanup directory",
    )


def _remove_directory_contents(descriptor: int, path: Path) -> None:
    """Empty one already-open directory using no-follow, descriptor-relative calls."""
    for name in _list_directory(descriptor, path):
        details = _stat_child(descriptor, path, name)
        identity = _identity_from_stat(details)
        if stat.S_ISDIR(details.st_mode):
            child = _open_directory_descriptor(
                path / name,
                identity,
                parent_descriptor=descriptor,
                name=name,
            )
            try:
                _remove_directory_contents(child, path / name)
            finally:
                os.close(child)
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if _identity_from_stat(current) != identity:
                raise SyncError("quarantined directory entry changed during cleanup")
            os.rmdir(name, dir_fd=descriptor)
        else:
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if _identity_from_stat(current) != identity:
                raise SyncError("quarantined directory entry changed during cleanup")
            os.unlink(name, dir_fd=descriptor)


def verify_created_staging_entry(
    parent_descriptor: int,
    parent_path: Path,
    name: str,
    expected: ObjectIdentity,
    label: str,
) -> os.stat_result:
    """Verify one newly-created name against its descriptor-derived identity."""
    details = _stat_child(parent_descriptor, parent_path, name)
    if _identity_from_stat(details) != expected:
        raise SyncError(f"{label} changed during staging materialization")
    if stat.S_ISREG(details.st_mode) and details.st_nlink != 1:
        raise SyncError(f"{label} has an unsupported hard link")
    return details


def _validate_staging_inventory(
    directories: set[PurePosixPath],
    files: dict[PurePosixPath, tuple[bytes, int]],
) -> None:
    """Reject ambiguous inventories before the empty staging root is mutated."""
    all_directories = set(directories)
    all_files = set(files)
    if len(all_directories) > MAX_TREE_DIRECTORIES:
        raise SyncError("adapter staging exceeds the directory count limit")
    if len(all_files) > MAX_TREE_FILES:
        raise SyncError("adapter staging exceeds the file count limit")
    if len(all_directories) + len(all_files) > MAX_TREE_ENTRIES:
        raise SyncError("adapter staging exceeds the entry count limit")
    total_bytes = 0
    for relative in all_directories | all_files:
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise SyncError("adapter staging inventory contains an unsafe path")
        if any(part in {"", "."} for part in relative.parts):
            raise SyncError("adapter staging inventory contains an unsafe path")
        if len(relative.parts) > MAX_TREE_DEPTH:
            raise SyncError("adapter staging exceeds the depth limit")
    for relative, (content, _mode) in files.items():
        if len(content) > MAX_FILE_BYTES:
            raise SyncError(
                f"adapter staging file {relative.as_posix()} exceeds the per-file byte limit"
            )
        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_BYTES:
            raise SyncError("adapter staging exceeds the aggregate byte limit")
    overlap = all_directories & all_files
    if overlap:
        raise SyncError(f"adapter staging path has conflicting types: {min(overlap)}")
    for relative in all_directories | all_files:
        parent = relative.parent
        if parent.parts and parent not in all_directories:
            raise SyncError(f"adapter staging parent is missing from inventory: {parent}")


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise SyncError("staged adapter file write made no progress")
        written += count


def build_tree(
    destination: Path,
    directories: set[PurePosixPath],
    files: dict[PurePosixPath, tuple[bytes, int]],
    *,
    expected_root: ObjectIdentity | None = None,
) -> None:
    """Materialize an adapter entirely through stable, no-follow descriptors."""
    _validate_staging_inventory(directories, files)
    root_identity = expected_root or object_identity(destination)
    if root_identity is None or root_identity.file_type != stat.S_IFDIR:
        raise SyncError("adapter staging path is missing or unsafe")
    root_descriptor = _open_directory_descriptor(destination, root_identity)
    descriptors: dict[PurePosixPath, int] = {PurePosixPath(): root_descriptor}
    try:
        for relative in sorted(
            directories,
            key=lambda item: (len(item.parts), item.as_posix()),
        ):
            parent = relative.parent
            parent_descriptor = descriptors[parent]
            parent_path = destination.joinpath(*parent.parts)
            try:
                os.mkdir(relative.name, 0o755, dir_fd=parent_descriptor)
            except OSError as exc:
                raise SyncError(
                    f"staged adapter directory could not be created: {relative}"
                ) from exc
            created = _stat_child(
                parent_descriptor,
                parent_path,
                relative.name,
            )
            identity = _identity_from_stat(created)
            if identity.file_type != stat.S_IFDIR:
                raise SyncError(f"staged adapter directory has unsafe type: {relative}")
            verify_created_staging_entry(
                parent_descriptor,
                parent_path,
                relative.name,
                identity,
                f"staged adapter directory {relative.as_posix()}",
            )
            descriptors[relative] = _open_directory_descriptor(
                destination / relative,
                identity,
                parent_descriptor=parent_descriptor,
                name=relative.name,
            )

        for relative, (content, mode) in sorted(files.items()):
            parent = relative.parent
            parent_descriptor = descriptors[parent]
            parent_path = destination.joinpath(*parent.parts)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor: int | None = None
            identity: ObjectIdentity | None = None
            try:
                descriptor = os.open(
                    relative.name,
                    flags,
                    mode,
                    dir_fd=parent_descriptor,
                )
                before = os.fstat(descriptor)
                identity = _identity_from_stat(before)
                if identity.file_type != stat.S_IFREG:
                    raise SyncError(f"staged adapter file has unsafe type: {relative}")
                if before.st_nlink != 1:
                    raise SyncError(
                        f"staged adapter file {relative.as_posix()} has an unsupported hard link"
                    )
                _write_all(descriptor, content)
                os.fchmod(descriptor, mode)
                after = os.fstat(descriptor)
                if _identity_from_stat(after) != identity:
                    raise SyncError(
                        f"staged adapter file {relative.as_posix()} changed while written"
                    )
                if after.st_nlink != 1:
                    raise SyncError(
                        f"staged adapter file {relative.as_posix()} acquired an unsupported hard link"
                    )
                if after.st_size != len(content) or stat.S_IMODE(after.st_mode) != mode:
                    raise SyncError(
                        f"staged adapter file {relative.as_posix()} failed write verification"
                    )
            except OSError as exc:
                raise SyncError(
                    f"staged adapter file could not be created: {relative}"
                ) from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            assert identity is not None
            verify_created_staging_entry(
                parent_descriptor,
                parent_path,
                relative.name,
                identity,
                f"staged adapter file {relative.as_posix()}",
            )
        if _identity_from_stat(os.fstat(root_descriptor)) != root_identity:
            raise SyncError("adapter staging root changed during materialization")
    finally:
        for descriptor in reversed(tuple(descriptors.values())):
            os.close(descriptor)


def load_expected_adapters() -> dict[
    str, tuple[set[PurePosixPath], dict[PurePosixPath, tuple[bytes, int]]]
]:
    """Load the canonical tree once and derive every host-specific adapter tree."""
    canonical_directories, canonical_files = canonical_tree()
    expected: dict[
        str, tuple[set[PurePosixPath], dict[PurePosixPath, tuple[bytes, int]]]
    ] = {}
    for adapter, target in ADAPTER_SKILLS.items():
        assert_repo_path(
            target,
            include_leaf=False,
            label=f"{adapter} adapter target parent",
        )
        expected[adapter] = expected_tree(adapter, canonical_directories, canonical_files)
    return expected


def verify_staged_tree(plan: AdapterPlan) -> None:
    """Reject a prepared adapter unless it exactly matches its derived tree."""
    _revalidate_parent(plan)
    _require_identity(
        plan.staging,
        plan.staging_identity,
        label=f"{plan.adapter} staged adapter",
    )
    drift = inspect_drift(plan.staging, plan.directories, plan.files)
    _revalidate_parent(plan)
    _require_identity(
        plan.staging,
        plan.staging_identity,
        label=f"{plan.adapter} staged adapter",
    )
    if drift:
        raise SyncError(
            f"{plan.adapter} staged adapter failed verification: " + "; ".join(drift)
        )


def _discard_prepared_staging(plans: list[AdapterPlan]) -> None:
    """Remove only staging objects whose captured identity is still present."""
    for plan in plans:
        _revalidate_parent(plan)
        _require_identity(
            plan.staging,
            plan.staging_identity,
            label=f"{plan.adapter} unpublished staging tree",
        )
    for plan in reversed(plans):
        _remove_owned_path(plan, plan.staging, plan.staging_identity)


def create_staging_root(
    parent: Path,
    parent_identity: ObjectIdentity,
    *,
    prefix: str,
) -> tuple[Path, ObjectIdentity]:
    """Create an exclusive staging root through the verified adapter-parent handle."""
    parent_descriptor = _open_directory_descriptor(parent, parent_identity)
    try:
        for _ in range(128):
            name = f"{prefix}{uuid.uuid4().hex}"
            try:
                os.mkdir(name, 0o700, dir_fd=parent_descriptor)
            except FileExistsError:
                continue
            details = _stat_child(parent_descriptor, parent, name)
            identity = _identity_from_stat(details)
            try:
                if identity.file_type != stat.S_IFDIR:
                    raise SyncError("staging root was created with an unsafe type")
                verify_created_staging_entry(
                    parent_descriptor,
                    parent,
                    name,
                    identity,
                    "adapter staging root",
                )
                root_descriptor = _open_directory_descriptor(
                    parent / name,
                    identity,
                    parent_descriptor=parent_descriptor,
                    name=name,
                )
                os.close(root_descriptor)
                _require_identity(
                    parent,
                    parent_identity,
                    label="adapter staging parent",
                )
                return parent / name, identity
            except BaseException:
                try:
                    current = os.stat(
                        name,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                else:
                    if _identity_from_stat(current) == identity:
                        os.rmdir(name, dir_fd=parent_descriptor)
                raise
        raise SyncError("could not allocate a unique adapter staging root")
    finally:
        os.close(parent_descriptor)


def prepare_adapter_updates(
    expected_by_adapter: dict[
        str, tuple[set[PurePosixPath], dict[PurePosixPath, tuple[bytes, int]]]
    ],
    adapters: tuple[str, ...],
) -> list[AdapterPlan]:
    """Build and verify every requested adapter before any published target moves."""
    requested = set(adapters)
    unknown = requested - set(ADAPTER_SKILLS)
    if unknown:
        raise SyncError("unknown adapter requested for synchronization")
    ordered_adapters = [adapter for adapter in ADAPTER_SKILLS if adapter in requested]
    if len(ordered_adapters) != len(adapters):
        raise SyncError("adapter synchronization request contains duplicates")

    transaction_id = uuid.uuid4().hex
    plans: list[AdapterPlan] = []
    try:
        for adapter in ordered_adapters:
            target = ADAPTER_SKILLS[adapter]
            assert_repo_path(
                target,
                include_leaf=False,
                label=f"{adapter} adapter target parent",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            parent_identity = object_identity(target.parent)
            if parent_identity is None or parent_identity.file_type != stat.S_IFDIR:
                raise SyncError(f"{adapter} adapter target parent is not a stable directory")
            directories, files = expected_by_adapter[adapter]
            target_identity = object_identity(target)
            staging, staging_identity = create_staging_root(
                target.parent,
                parent_identity,
                prefix=f".{target.name}.sync-{transaction_id}-",
            )
            previous = target.parent / (
                f".{target.name}.previous-{transaction_id}-{uuid.uuid4().hex}"
            )
            _require_absent(previous, label=f"{adapter} previous target")
            plan = AdapterPlan(
                adapter=adapter,
                target=target,
                staging=staging,
                previous=previous,
                had_target=target_identity is not None,
                parent_identity=parent_identity,
                staging_identity=staging_identity,
                target_identity=target_identity,
                directories=directories,
                files=files,
            )
            plans.append(plan)
            build_tree(
                staging,
                directories,
                files,
                expected_root=staging_identity,
            )
            verify_staged_tree(plan)
    except BaseException:
        if plans:
            _discard_prepared_staging(plans)
        raise
    return plans


def _relative_journal_path(path: Path) -> str:
    try:
        relative = path.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise SyncError("transaction journal contains an out-of-repository path") from exc
    pure = PurePosixPath(relative.as_posix())
    if pure.is_absolute() or ".." in pure.parts:
        raise SyncError("transaction journal contains an unsafe path")
    return pure.as_posix()


def _identity_payload(identity: ObjectIdentity | None) -> dict[str, int] | None:
    if identity is None:
        return None
    return {
        "device": identity.device,
        "inode": identity.inode,
        "file_type": identity.file_type,
    }


def _parse_identity(
    value: object,
    *,
    field: str,
    optional: bool = False,
) -> ObjectIdentity | None:
    if value is None and optional:
        return None
    if not isinstance(value, dict) or set(value) != {"device", "inode", "file_type"}:
        raise SyncError(f"synchronization journal has an invalid {field} identity")
    parts = (value["device"], value["inode"], value["file_type"])
    if any(not isinstance(part, int) or isinstance(part, bool) or part < 0 for part in parts):
        raise SyncError(f"synchronization journal has an invalid {field} identity")
    return ObjectIdentity(*parts)


def _flush_directory(directory: Path) -> None:
    """Best-effort directory durability on platforms that support directory fsync."""
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _quarantine_owned_regular_file(
    path: Path,
    expected: ObjectIdentity,
    parent_identity: ObjectIdentity,
    *,
    label: str,
) -> Path:
    """Atomically isolate an owned regular file without consuming a replacement."""
    if expected.file_type != stat.S_IFREG:
        raise SyncError(f"{label} is not an owned regular file")
    parent_descriptor = _open_directory_descriptor(path.parent, parent_identity)
    try:
        details = _stat_child(parent_descriptor, path.parent, path.name)
        if _identity_from_stat(details) != expected:
            raise SyncError(f"{label} changed ownership before quarantine")
        if details.st_nlink != 1:
            raise SyncError(f"{label} has an unsupported hard link")
    finally:
        os.close(parent_descriptor)

    quarantine = path.parent / f".{path.name}.quarantine-{uuid.uuid4().hex}"
    _require_absent(quarantine, label=f"{label} quarantine")
    rename_noreplace(path, quarantine, parent_identity)
    moved = object_identity(quarantine)
    if moved != expected:
        if object_identity(path) is None and moved is not None:
            try:
                rename_noreplace(quarantine, path, parent_identity)
            except (OSError, SyncError) as restore_error:
                raise SyncError(
                    f"{label} quarantined a foreign replacement and could not restore it"
                ) from restore_error
        raise SyncError(f"{label} changed before its atomic quarantine")
    return quarantine


def _restore_quarantined_regular_file(
    quarantine: Path,
    destination: Path,
    expected: ObjectIdentity,
    parent_identity: ObjectIdentity,
    *,
    label: str,
) -> None:
    if object_identity(destination) is not None:
        raise SyncError(f"{label} cannot be restored because its destination is occupied")
    _require_identity(quarantine, expected, label=f"{label} quarantine")
    rename_noreplace(quarantine, destination, parent_identity)
    _require_identity(destination, expected, label=label)


def _delete_quarantined_regular_file(
    quarantine: Path,
    expected: ObjectIdentity,
    parent_identity: ObjectIdentity,
    *,
    label: str,
) -> None:
    """Unlink a random quarantined name through its verified parent descriptor."""
    parent_descriptor = _open_directory_descriptor(quarantine.parent, parent_identity)
    descriptor: int | None = None
    try:
        details = _stat_child(
            parent_descriptor,
            quarantine.parent,
            quarantine.name,
        )
        if _identity_from_stat(details) != expected or not stat.S_ISREG(details.st_mode):
            raise SyncError(f"{label} quarantine changed ownership before deletion")
        if details.st_nlink != 1:
            raise SyncError(f"{label} quarantine has an unsupported hard link")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            quarantine.name,
            flags,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if _identity_from_stat(opened) != expected or opened.st_nlink != 1:
            raise SyncError(f"{label} quarantine changed before deletion")
        current = os.stat(
            quarantine.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if _identity_from_stat(current) != expected or current.st_nlink != 1:
            raise SyncError(f"{label} quarantine changed before deletion")
        os.unlink(quarantine.name, dir_fd=parent_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)
    _require_absent(quarantine, label=f"{label} quarantine")


def _remove_owned_regular_file(
    path: Path,
    expected: ObjectIdentity,
    parent_identity: ObjectIdentity,
    *,
    label: str,
) -> None:
    quarantine = _quarantine_owned_regular_file(
        path,
        expected,
        parent_identity,
        label=label,
    )
    _delete_quarantined_regular_file(
        quarantine,
        expected,
        parent_identity,
        label=label,
    )


def write_transaction_journal(
    plans: list[AdapterPlan],
    *,
    phase: str,
    expected_current: ObjectIdentity | None = None,
) -> ObjectIdentity:
    """Atomically publish a redacted, repository-relative recovery journal."""
    if phase not in {"prepared", "committed"}:
        raise SyncError("unsupported synchronization journal phase")
    if [plan.adapter for plan in plans] != list(ADAPTER_SKILLS):
        raise SyncError("transaction journal must cover every adapter exactly once")

    assert_repo_path(SYNC_STATE_DIR, include_leaf=True, label="synchronization state directory")
    SYNC_STATE_DIR.mkdir(parents=True, exist_ok=True)
    assert_repo_path(SYNC_JOURNAL_PATH, include_leaf=False, label="synchronization journal")
    state_identity = object_identity(SYNC_STATE_DIR)
    if state_identity is None or state_identity.file_type != stat.S_IFDIR:
        raise SyncError("synchronization state directory is not stable")

    payload = {
        "version": JOURNAL_VERSION,
        "phase": phase,
        "plans": [
            {
                "adapter": plan.adapter,
                "target": _relative_journal_path(plan.target),
                "staging": _relative_journal_path(plan.staging),
                "previous": _relative_journal_path(plan.previous),
                "had_target": plan.had_target,
                "parent_identity": _identity_payload(plan.parent_identity),
                "staging_identity": _identity_payload(plan.staging_identity),
                "target_identity": _identity_payload(plan.target_identity),
            }
            for plan in plans
        ],
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    temporary = SYNC_STATE_DIR / f".{SYNC_JOURNAL_PATH.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    state_descriptor = _open_directory_descriptor(SYNC_STATE_DIR, state_identity)
    descriptor: int | None = None
    temporary_identity: ObjectIdentity | None = None
    retired_journal: Path | None = None
    published = False
    try:
        descriptor = os.open(
            temporary.name,
            flags,
            0o600,
            dir_fd=state_descriptor,
        )
        temporary_details = os.fstat(descriptor)
        temporary_identity = _identity_from_stat(temporary_details)
        if temporary_identity.file_type != stat.S_IFREG or temporary_details.st_nlink != 1:
            raise SyncError("temporary synchronization journal is not a unique regular file")
        _write_all(descriptor, encoded)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        written = os.fstat(descriptor)
        if (
            _identity_from_stat(written) != temporary_identity
            or written.st_nlink != 1
            or written.st_size != len(encoded)
        ):
            raise SyncError("temporary synchronization journal failed write verification")
        os.close(descriptor)
        descriptor = None
        verify_created_staging_entry(
            state_descriptor,
            SYNC_STATE_DIR,
            temporary.name,
            temporary_identity,
            "temporary synchronization journal",
        )

        if expected_current is not None:
            retired_journal = _quarantine_owned_regular_file(
                SYNC_JOURNAL_PATH,
                expected_current,
                state_identity,
                label="synchronization journal",
            )
        elif object_identity(SYNC_JOURNAL_PATH) is not None:
            raise SyncError("synchronization journal destination is unexpectedly occupied")

        try:
            rename_noreplace(temporary, SYNC_JOURNAL_PATH, state_identity)
            published = True
        except BaseException:
            if retired_journal is not None and object_identity(SYNC_JOURNAL_PATH) is None:
                _restore_quarantined_regular_file(
                    retired_journal,
                    SYNC_JOURNAL_PATH,
                    expected_current,
                    state_identity,
                    label="synchronization journal",
                )
                retired_journal = None
            raise
        _require_identity(
            SYNC_JOURNAL_PATH,
            temporary_identity,
            label="synchronization journal",
        )
        if retired_journal is not None:
            assert expected_current is not None
            _delete_quarantined_regular_file(
                retired_journal,
                expected_current,
                state_identity,
                label="retired synchronization journal",
            )
            retired_journal = None
        _flush_directory(SYNC_STATE_DIR)
        return temporary_identity
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(state_descriptor)
        current_temporary = object_identity(temporary)
        if current_temporary is not None:
            if current_temporary != temporary_identity:
                raise SyncError("temporary journal path changed ownership")
            _remove_owned_regular_file(
                temporary,
                temporary_identity,
                state_identity,
                label="temporary synchronization journal",
            )
        if not published and retired_journal is not None:
            if object_identity(SYNC_JOURNAL_PATH) is None:
                _restore_quarantined_regular_file(
                    retired_journal,
                    SYNC_JOURNAL_PATH,
                    expected_current,
                    state_identity,
                    label="synchronization journal",
                )


def _journal_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str):
        raise SyncError(f"synchronization journal has an invalid {field} path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts or "\\" in value:
        raise SyncError(f"synchronization journal has an unsafe {field} path")
    candidate = REPO_ROOT.joinpath(*relative.parts)
    assert_repo_path(candidate, include_leaf=False, label=f"journal {field} path")
    return candidate


def _read_transaction_journal() -> tuple[str, list[AdapterPlan], ObjectIdentity]:
    assert_repo_path(SYNC_JOURNAL_PATH, include_leaf=False, label="synchronization journal")
    journal_identity = object_identity(SYNC_JOURNAL_PATH)
    if journal_identity is None or journal_identity.file_type != stat.S_IFREG:
        raise SyncError("synchronization journal is missing or unsafe")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(SYNC_JOURNAL_PATH, flags)
        opened_details = os.fstat(descriptor)
        opened_identity = ObjectIdentity(
            opened_details.st_dev,
            opened_details.st_ino,
            stat.S_IFMT(opened_details.st_mode),
        )
        if opened_identity != journal_identity:
            raise SyncError("synchronization journal changed ownership before it was read")
        if opened_details.st_nlink != 1:
            raise SyncError("synchronization journal has an unsupported hard link")
        if opened_details.st_size > 1024 * 1024:
            raise SyncError("synchronization journal exceeds its size limit")
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=True) as journal_file:
            descriptor = None
            payload = json.load(journal_file)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError("synchronization journal is invalid; contents redacted") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _require_identity(
        SYNC_JOURNAL_PATH,
        journal_identity,
        label="synchronization journal",
    )
    if not isinstance(payload, dict) or payload.get("version") != JOURNAL_VERSION:
        raise SyncError("synchronization journal has an unsupported format")
    phase = payload.get("phase")
    raw_plans = payload.get("plans")
    if phase not in {"prepared", "committed"} or not isinstance(raw_plans, list):
        raise SyncError("synchronization journal has an invalid transaction state")
    if len(raw_plans) != len(ADAPTER_SKILLS):
        raise SyncError("synchronization journal does not cover every adapter")

    plans: list[AdapterPlan] = []
    for raw_plan in raw_plans:
        if not isinstance(raw_plan, dict):
            raise SyncError("synchronization journal contains an invalid adapter plan")
        adapter = raw_plan.get("adapter")
        if not isinstance(adapter, str) or adapter not in ADAPTER_SKILLS:
            raise SyncError("synchronization journal contains an unknown adapter")
        target = _journal_path(raw_plan.get("target"), field="target")
        staging = _journal_path(raw_plan.get("staging"), field="staging")
        previous = _journal_path(raw_plan.get("previous"), field="previous")
        had_target = raw_plan.get("had_target")
        parent_identity = _parse_identity(
            raw_plan.get("parent_identity"), field="parent"
        )
        staging_identity = _parse_identity(
            raw_plan.get("staging_identity"), field="staging"
        )
        target_identity = _parse_identity(
            raw_plan.get("target_identity"), field="target", optional=True
        )
        if parent_identity is None or parent_identity.file_type != stat.S_IFDIR:
            raise SyncError("synchronization journal has an invalid parent identity")
        if staging_identity is None or staging_identity.file_type != stat.S_IFDIR:
            raise SyncError("synchronization journal has an invalid staging identity")
        expected_target = ADAPTER_SKILLS[adapter]
        if target != expected_target or staging.parent != target.parent or previous.parent != target.parent:
            raise SyncError("synchronization journal contains mismatched adapter paths")
        if not staging.name.startswith(f".{target.name}.sync-") or not previous.name.startswith(
            f".{target.name}.previous-"
        ):
            raise SyncError("synchronization journal contains unexpected transaction paths")
        if not isinstance(had_target, bool):
            raise SyncError("synchronization journal contains an invalid target state")
        if had_target != (target_identity is not None):
            raise SyncError("synchronization journal target identity contradicts its state")
        plans.append(
            AdapterPlan(
                adapter=adapter,
                target=target,
                staging=staging,
                previous=previous,
                had_target=had_target,
                parent_identity=parent_identity,
                staging_identity=staging_identity,
                target_identity=target_identity,
                directories=set(),
                files={},
            )
        )
    if [plan.adapter for plan in plans] != list(ADAPTER_SKILLS):
        raise SyncError("synchronization journal adapters are duplicated or out of order")
    return phase, plans, journal_identity


def _remove_journal(expected: ObjectIdentity) -> None:
    state_identity = object_identity(SYNC_STATE_DIR)
    if state_identity is None or state_identity.file_type != stat.S_IFDIR:
        raise SyncError("synchronization state directory is not stable")
    _remove_owned_regular_file(
        SYNC_JOURNAL_PATH,
        expected,
        state_identity,
        label="synchronization journal",
    )
    _flush_directory(SYNC_STATE_DIR)


def _prepared_state(plan: AdapterPlan) -> str:
    """Classify only identity-safe, resumable states of a prepared transaction."""
    _revalidate_parent(plan)
    target_identity = object_identity(plan.target)
    staging_identity = object_identity(plan.staging)
    previous_identity = object_identity(plan.previous)
    old = plan.target_identity
    new = plan.staging_identity

    if old is not None:
        valid = {
            (old, new, None): "untouched",
            (old, None, None): "recovered",
            (None, new, old): "old-moved",
            (new, None, old): "promoted",
            (None, None, old): "new-removed",
        }
    else:
        valid = {
            (None, new, None): "untouched",
            (new, None, None): "promoted",
            (None, None, None): "recovered",
        }
    state = valid.get((target_identity, staging_identity, previous_identity))
    if state is None:
        raise SyncError(
            f"{plan.adapter} prepared transaction contains a foreign or ambiguous object"
        )
    return state


def _committed_state_is_safe(plan: AdapterPlan) -> bool:
    _revalidate_parent(plan)
    if object_identity(plan.target) != plan.staging_identity:
        return False
    if object_identity(plan.staging) is not None:
        return False
    previous_identity = object_identity(plan.previous)
    return previous_identity in ({None, plan.target_identity} if plan.target_identity else {None})


def _plans_with_expected_trees(plans: list[AdapterPlan]) -> list[AdapterPlan]:
    """Attach the current canonical expectations to plans loaded from the journal."""
    expected = load_expected_adapters()
    return [
        plan._replace(
            directories=expected[plan.adapter][0],
            files=expected[plan.adapter][1],
        )
        for plan in plans
    ]


def _verify_promoted_plans(plans: list[AdapterPlan]) -> None:
    """Verify exact promoted contents while every previous tree is still retained."""
    for plan in plans:
        _revalidate_parent(plan)
        _require_identity(
            plan.target,
            plan.staging_identity,
            label=f"{plan.adapter} promoted adapter",
        )
        drift = inspect_drift(plan.target, plan.directories, plan.files)
        _revalidate_parent(plan)
        _require_identity(
            plan.target,
            plan.staging_identity,
            label=f"{plan.adapter} promoted adapter",
        )
        if drift:
            raise SyncError(
                f"{plan.adapter} promoted adapter failed verification: "
                + "; ".join(drift)
            )


def recover_pending_transaction() -> None:
    """Restore all old targets for a prepared transaction, or finalize a committed one."""
    if object_identity(SYNC_JOURNAL_PATH) is None:
        return
    phase, plans, journal_identity = _read_transaction_journal()
    if phase == "committed":
        if not all(_committed_state_is_safe(plan) for plan in plans):
            raise SyncError("committed synchronization contains a foreign transaction object")
        try:
            verified_plans = _plans_with_expected_trees(plans)
            _verify_promoted_plans(verified_plans)
        except (OSError, SyncError):
            journal_identity = write_transaction_journal(
                plans,
                phase="prepared",
                expected_current=journal_identity,
            )
            phase = "prepared"
        else:
            for plan in reversed(plans):
                if plan.target_identity is not None and object_identity(plan.previous) is not None:
                    _remove_owned_path(plan, plan.previous, plan.target_identity)
            if not all(_committed_state_is_safe(plan) for plan in plans):
                raise SyncError("committed synchronization cleanup could not be verified")
            _remove_journal(journal_identity)
            return

    recovery_actions = [_prepared_state(plan) for plan in plans]
    for plan, action in reversed(list(zip(plans, recovery_actions, strict=True))):
        if action == "untouched":
            _remove_owned_path(plan, plan.staging, plan.staging_identity)
        elif action == "old-moved":
            assert plan.target_identity is not None
            _move_owned_path(plan, plan.previous, plan.target, plan.target_identity)
            _remove_owned_path(plan, plan.staging, plan.staging_identity)
        elif action == "promoted":
            _remove_owned_path(plan, plan.target, plan.staging_identity)
            if plan.target_identity is not None:
                _move_owned_path(plan, plan.previous, plan.target, plan.target_identity)
        elif action == "new-removed":
            assert plan.target_identity is not None
            _move_owned_path(plan, plan.previous, plan.target, plan.target_identity)

    if any(_prepared_state(plan) != "recovered" for plan in plans):
        raise SyncError("prepared synchronization rollback could not be verified")
    _remove_journal(journal_identity)


def commit_adapter_updates(
    plans: list[AdapterPlan],
    prepared_journal: ObjectIdentity,
) -> None:
    """Promote every verified tree under one recoverable transaction journal."""
    if any(_prepared_state(plan) != "untouched" for plan in plans):
        raise SyncError("prepared adapter transaction changed before commit")

    for plan in plans:
        if plan.target_identity is not None:
            _move_owned_path(plan, plan.target, plan.previous, plan.target_identity)
        _move_owned_path(plan, plan.staging, plan.target, plan.staging_identity)
        _flush_directory(plan.target.parent)

    _verify_promoted_plans(plans)
    committed_journal = write_transaction_journal(
        plans,
        phase="committed",
        expected_current=prepared_journal,
    )
    try:
        _verify_promoted_plans(plans)
    except BaseException:
        write_transaction_journal(
            plans,
            phase="prepared",
            expected_current=committed_journal,
        )
        raise
    for plan in reversed(plans):
        if plan.target_identity is not None:
            _remove_owned_path(plan, plan.previous, plan.target_identity)
    _remove_journal(committed_journal)


def synchronize(*, check: bool) -> dict[str, list[str]]:
    """Check or synchronously publish every adapter under one repository lock."""
    if check:
        with repository_check_guard():
            expected_by_adapter = load_expected_adapters()
            return {
                adapter: drift
                for adapter, target in ADAPTER_SKILLS.items()
                if (drift := inspect_drift(target, *expected_by_adapter[adapter]))
            }
    if os.name == "nt" and not check:
        raise SyncError(
            "Windows synchronization is disabled because secure handle-relative "
            "transaction operations are unavailable"
        )
    with repository_lock():
        if object_identity(SYNC_JOURNAL_PATH) is not None:
            recover_pending_transaction()

        for adapter, target in ADAPTER_SKILLS.items():
            target_identity = object_identity(target)
            if (
                target_identity is not None
                and target_identity.file_type != stat.S_IFDIR
            ):
                raise SyncError(
                    f"{adapter} adapter target must be a directory or absent"
                )

        expected_by_adapter = load_expected_adapters()
        drift_by_adapter = {
            adapter: drift
            for adapter, target in ADAPTER_SKILLS.items()
            if (drift := inspect_drift(target, *expected_by_adapter[adapter]))
        }
        if not drift_by_adapter:
            return drift_by_adapter

        plans = prepare_adapter_updates(expected_by_adapter, tuple(ADAPTER_SKILLS))
        journal_written = False
        try:
            prepared_journal = write_transaction_journal(plans, phase="prepared")
            journal_written = True
            commit_adapter_updates(plans, prepared_journal)
        except BaseException as original_error:
            try:
                if journal_written or object_identity(SYNC_JOURNAL_PATH) is not None:
                    recover_pending_transaction()
                else:
                    _discard_prepared_staging(plans)
            except BaseException as recovery_error:
                raise SyncError(
                    "adapter synchronization failed and automatic recovery could not complete"
                ) from recovery_error
            raise original_error
        return drift_by_adapter


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy the canonical map-project skill into every host adapter bundle."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report adapter drift without changing files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        drift_by_adapter = synchronize(check=args.check)
    except KeyboardInterrupt:
        print("sync_adapters: interrupted; transaction recovery completed", file=sys.stderr)
        return 130
    except (OSError, SyncError) as exc:
        print(f"sync_adapters: {exc}", file=sys.stderr)
        return 2

    if args.check:
        if not drift_by_adapter:
            print("adapter bundles are synchronized")
            return 0
        for adapter, drift in drift_by_adapter.items():
            for item in drift:
                print(f"{adapter}: {item}", file=sys.stderr)
        return 1

    if drift_by_adapter:
        print("synchronized adapter bundles: " + ", ".join(ADAPTER_SKILLS))
    else:
        print("adapter bundles were already synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
