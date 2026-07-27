#!/usr/bin/env python3
"""Synchronize adapter bundles from canonical core and adapter-owned guidance."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
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
HOST_GUIDANCE_PATH = PurePosixPath("references/host-guidance.md")
HOST_GUIDANCE_SOURCES = {
    "codex": REPO_ROOT / "adapters" / "codex" / "MODEL_GUIDANCE.md",
    "claude-code": REPO_ROOT / "adapters" / "claude-code" / "MODEL_GUIDANCE.md",
}
SYNC_STATE_DIR = REPO_ROOT / ".scratch"
SYNC_LOCK_PATH = SYNC_STATE_DIR / "sync-adapters.lock"
SYNC_JOURNAL_PATH = SYNC_STATE_DIR / "sync-adapters.journal.json"
SYNC_RECEIPT_PATH = SYNC_STATE_DIR / "sync-adapters.commit.json"
JOURNAL_VERSION = 2
RECEIPT_VERSION = 1
ACTIVE_MARKER_NAME = ".project-atlas-sync-owner.json"
RETIRED_MARKER_NAME = ".project-atlas-sync-owner.retired.json"
REMOVING_MARKER_NAME = ".project-atlas-sync-owner.retired.removing.json"
PRIVATE_REMOVING_MARKER_PREFIX = ".project-atlas-sync-owner.private-removing-"
MAX_STATE_FILE_BYTES = 1024 * 1024
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
    transaction_id: str
    promotion_nonce: str
    target: Path
    staging: Path
    previous: Path
    had_target: bool
    parent_identity: ObjectIdentity
    staging_identity: ObjectIdentity
    target_identity: ObjectIdentity | None
    prepared_tree_sha256: str
    original_tree_sha256: str | None
    directories: set[PurePosixPath]
    files: dict[PurePosixPath, tuple[bytes, int]]


class TransactionJournal(NamedTuple):
    transaction_id: str
    plans: list[AdapterPlan]
    identity: ObjectIdentity
    sha256: str
    encoded: bytes


class CommitReceipt(NamedTuple):
    identity: ObjectIdentity
    encoded: bytes


class RecoveryPositions(NamedTuple):
    old: str | None
    new: str | None
    old_marker: str | None
    new_marker: str | None


class TreeSnapshot(NamedTuple):
    directories: set[PurePosixPath]
    files: set[PurePosixPath]
    symlinks: set[PurePosixPath]
    special_nodes: set[PurePosixPath]
    contents: dict[PurePosixPath, tuple[bytes, int]]
    symlink_targets: dict[PurePosixPath, str]
    special_modes: dict[PurePosixPath, tuple[int, int, int]]


class TreeBudget:
    """Mutable resource accounting shared by one bounded descriptor walk."""

    def __init__(self) -> None:
        self.entries = 0
        self.directories = 0
        self.files = 0
        self.total_bytes = 0


def _is_reserved_sync_state_name(name: str) -> bool:
    return name != SYNC_LOCK_PATH.name and (
        name.startswith("sync-adapters.")
        or name.startswith(".sync-adapters.")
    )


def _is_reserved_adapter_sibling(name: str, target_names: set[str]) -> bool:
    return any(
        name.startswith(prefix)
        for target_name in target_names
        for prefix in (
            f".{target_name}.sync-",
            f".{target_name}.previous-",
            f".{target_name}.cleanup-",
            f"..{target_name}.sync-",
            f"..{target_name}.previous-",
            f"..{target_name}.cleanup-",
        )
    )


def _reserved_sync_inventory_specs() -> dict[Path, set[str] | None]:
    specs: dict[Path, set[str] | None] = {SYNC_STATE_DIR: None}
    for target in ADAPTER_SKILLS.values():
        target_names = specs.setdefault(target.parent, set())
        if target_names is None:
            raise SyncError("adapter parent overlaps the synchronization state directory")
        target_names.add(target.name)
    return specs


def _scan_reserved_sync_inventory(
    specs: dict[Path, set[str] | None],
) -> dict[Path, tuple[ObjectIdentity | None, tuple[int, ...] | None]]:
    """Boundedly inspect reserved immediate children without following any entry."""
    inventory: dict[Path, tuple[ObjectIdentity | None, tuple[int, ...] | None]] = {}
    for directory, target_names in specs.items():
        assert_repo_path(
            directory,
            include_leaf=True,
            label="synchronization inventory directory",
        )
        initial_identity = object_identity(directory)
        if initial_identity is None:
            inventory[directory] = (None, None)
            continue
        if initial_identity.file_type != stat.S_IFDIR:
            raise SyncError("synchronization inventory path is not a directory")
        try:
            initial_details = os.lstat(directory)
        except OSError as exc:
            raise SyncError("cannot inspect synchronization inventory directory") from exc
        if _identity_from_stat(initial_details) != initial_identity:
            raise SyncError("synchronization inventory directory changed before scan")
        initial_path_signature = _stable_stat_signature(initial_details)
        entries = 0
        descriptor = _open_directory_descriptor(directory, initial_identity)
        try:
            try:
                opened_details = os.fstat(descriptor)
                opened_path_details = os.lstat(directory)
            except OSError as exc:
                raise SyncError(
                    "synchronization inventory directory changed before scan"
                ) from exc
            opened_handle_signature = _stable_stat_signature(opened_details)
            if (
                _identity_from_stat(opened_details) != initial_identity
                or _identity_from_stat(opened_path_details) != initial_identity
                or _stable_stat_signature(opened_path_details)
                != initial_path_signature
            ):
                raise SyncError(
                    "synchronization inventory directory changed before scan"
                )
            scan_root: int | Path = directory if os.name == "nt" else descriptor
            with os.scandir(scan_root) as children:
                for entry in children:
                    entries += 1
                    if entries > MAX_TREE_ENTRIES:
                        raise SyncError(
                            "synchronization inventory exceeds the entry limit"
                        )
                    try:
                        entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise SyncError(
                            "synchronization inventory entry changed during no-follow scan"
                        ) from exc
                    reserved = (
                        _is_reserved_sync_state_name(entry.name)
                        if target_names is None
                        else _is_reserved_adapter_sibling(entry.name, target_names)
                    )
                    if reserved:
                        raise SyncError(
                            "synchronization state changed or is unfinished; "
                            "requires a recovery run"
                        )
        except SyncError:
            raise
        except OSError as exc:
            raise SyncError("cannot scan synchronization inventory directory") from exc
        finally:
            try:
                final_opened = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        if (
            _identity_from_stat(final_opened) != initial_identity
            or _stable_stat_signature(final_opened) != opened_handle_signature
        ):
            raise SyncError("synchronization inventory directory changed during scan")
        try:
            final_details = os.lstat(directory)
        except OSError as exc:
            raise SyncError("synchronization inventory directory changed during scan") from exc
        if (
            _identity_from_stat(final_details) != initial_identity
            or _stable_stat_signature(final_details) != initial_path_signature
        ):
            raise SyncError("synchronization inventory directory changed during scan")
        inventory[directory] = (initial_identity, initial_path_signature)
    return inventory


def _verify_reserved_sync_inventory(
    specs: dict[Path, set[str] | None],
    expected: dict[Path, tuple[ObjectIdentity | None, tuple[int, ...] | None]],
) -> None:
    if _scan_reserved_sync_inventory(specs) != expected:
        raise SyncError("synchronization inventory changed during read-only check")


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
    specs = _reserved_sync_inventory_specs()
    initial_inventory = _scan_reserved_sync_inventory(specs)
    initial_state = initial_inventory[SYNC_STATE_DIR][0]

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

        _verify_reserved_sync_inventory(specs, initial_inventory)
        try:
            yield
        finally:
            _verify_reserved_sync_inventory(specs, initial_inventory)
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

    if object_identity(SYNC_LOCK_PATH) != lock_identity:
        raise SyncError("synchronization lock changed during read-only check")
    if object_identity(SYNC_STATE_DIR) != initial_state:
        raise SyncError("synchronization state changed during read-only check")


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
            try:
                if os.name == "nt":
                    symlink_target = os.readlink(path / name)
                else:
                    symlink_target = os.readlink(name, dir_fd=descriptor)
            except OSError as exc:
                raise SyncError(
                    f"tree symlink {relative.as_posix()} changed while it was being verified"
                ) from exc
            snapshot.symlink_targets[relative] = symlink_target
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
            snapshot.special_modes[relative] = (
                stat.S_IFMT(details.st_mode),
                stat.S_IMODE(details.st_mode),
                getattr(details, "st_rdev", 0),
            )
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
    snapshot = TreeSnapshot(set(), set(), set(), set(), {}, {}, {})
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


def _tree_payload_sha256(
    directories: set[PurePosixPath],
    files: dict[PurePosixPath, tuple[bytes, int]],
    *,
    symlinks: dict[PurePosixPath, str] | None = None,
    special_nodes: dict[PurePosixPath, tuple[int, int, int]] | None = None,
) -> str:
    """Digest one bounded, marker-free adapter payload without path ambiguities."""
    digest = hashlib.sha256()
    digest.update(b"project-atlas-adapter-tree-v1\0")
    for relative in sorted(directories):
        encoded_path = relative.as_posix().encode("utf-8")
        digest.update(b"D")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
    for relative in sorted(files):
        content, mode = files[relative]
        encoded_path = relative.as_posix().encode("utf-8")
        digest.update(b"F")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(mode.to_bytes(4, "big"))
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    for relative, target in sorted((symlinks or {}).items()):
        encoded_path = relative.as_posix().encode("utf-8")
        encoded_target = target.encode("utf-8", "surrogateescape")
        digest.update(b"L")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(encoded_target).to_bytes(4, "big"))
        digest.update(encoded_target)
    for relative, (file_type, mode, device_id) in sorted((special_nodes or {}).items()):
        encoded_path = relative.as_posix().encode("utf-8")
        digest.update(b"S")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(file_type.to_bytes(4, "big"))
        digest.update(mode.to_bytes(4, "big"))
        digest.update(device_id.to_bytes(8, "big", signed=False))
    return digest.hexdigest()


def _snapshot_payload_sha256(
    root: Path,
    expected_root: ObjectIdentity,
    *,
    excluded_marker_state: str | None = None,
    excluded_marker_name: str | None = None,
) -> str:
    """Hash an exact bounded tree, optionally excluding one verified role marker."""
    if excluded_marker_state is not None and excluded_marker_name is not None:
        raise SyncError("tree hashing received conflicting marker exclusions")
    snapshot = snapshot_tree(root, read_paths=None, expected_root=expected_root)
    directories = set(snapshot.directories)
    files = dict(snapshot.contents)
    if excluded_marker_state is not None:
        marker = PurePosixPath(_public_marker_name(excluded_marker_state))
        if marker not in files:
            raise SyncError("transaction ownership marker disappeared during tree hashing")
        del files[marker]
    if excluded_marker_name is not None:
        marker = PurePosixPath(excluded_marker_name)
        if marker not in files:
            raise SyncError("transaction ownership marker disappeared during tree hashing")
        del files[marker]
    return _tree_payload_sha256(
        directories,
        files,
        symlinks=snapshot.symlink_targets,
        special_nodes=snapshot.special_modes,
    )


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
    if HOST_GUIDANCE_PATH in all_nodes:
        raise SyncError(
            "host-specific references/host-guidance.md must live in adapters, "
            "not canonical core"
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


def load_host_guidance(adapter: str) -> bytes:
    """Read one adapter-owned guidance source through a bounded no-follow descriptor."""
    try:
        source = HOST_GUIDANCE_SOURCES[adapter]
    except KeyError as exc:
        raise SyncError(
            f"host guidance source is not configured for adapter: {adapter}"
        ) from exc
    assert_repo_path(
        source,
        include_leaf=True,
        label=f"{adapter} host guidance source",
    )
    parent_identity = object_identity(source.parent)
    if parent_identity is None or parent_identity.file_type != stat.S_IFDIR:
        raise SyncError(f"{adapter} host guidance parent is missing: {source.parent}")
    parent_descriptor = _open_directory_descriptor(source.parent, parent_identity)
    try:
        details = _stat_child(parent_descriptor, source.parent, source.name)
        identity = _identity_from_stat(details)
        if identity.file_type != stat.S_IFREG:
            raise SyncError(f"{adapter} host guidance source is not a regular file")
        content, _source_mode = read_verified_entry(
            parent_descriptor,
            source.parent,
            source.name,
            identity,
            f"{adapter} host guidance source",
        )
        after = _stat_child(parent_descriptor, source.parent, source.name)
        if (
            _identity_from_stat(after) != identity
            or _stable_stat_signature(after) != _stable_stat_signature(details)
        ):
            raise SyncError(f"{adapter} host guidance source changed while it was read")
    finally:
        os.close(parent_descriptor)

    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError(f"{adapter} host guidance source must be UTF-8") from exc
    if not content or not content.endswith(b"\n"):
        raise SyncError(
            f"{adapter} host guidance source must be non-empty and newline-terminated"
        )
    return content


def expected_tree(
    adapter: str,
    canonical_directories: set[PurePosixPath],
    canonical_files: dict[PurePosixPath, tuple[bytes, int]],
    host_guidance: bytes,
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
    directories.add(HOST_GUIDANCE_PATH.parent)
    files[HOST_GUIDANCE_PATH] = (host_guidance, 0o644)
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
    return _identity_from_stat(details)


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


def _restore_moved_path(
    plan: AdapterPlan,
    moved_path: Path,
    public_source: Path,
    moved_identity: ObjectIdentity,
    *,
    label: str,
) -> None:
    """Restore a raced replacement without overwriting another public occupant."""
    _revalidate_parent(plan)
    if object_identity(public_source) is not None:
        raise SyncError(f"{label} source became occupied; both objects were preserved")
    try:
        rename_noreplace(moved_path, public_source, plan.parent_identity)
    except (OSError, SyncError) as restore_error:
        raise SyncError(
            f"{label} could not restore the moved replacement; both objects were preserved"
        ) from restore_error
    if object_identity(public_source) != moved_identity:
        raise SyncError(f"{label} restored replacement changed ownership")
    _flush_directory(plan.target.parent)


def _move_owned_path(
    plan: AdapterPlan,
    source: Path,
    destination: Path,
    expected: ObjectIdentity,
    *,
    role: str | None = None,
    journal_sha256: str | None = None,
) -> None:
    if source.parent != plan.target.parent or destination.parent != plan.target.parent:
        raise SyncError("transaction move escaped its verified adapter parent")
    if (role is None) != (journal_sha256 is None):
        raise SyncError("transaction move has incomplete marker proof")
    _revalidate_parent(plan)
    _require_identity(source, expected, label=f"{plan.adapter} transaction source")
    if role is not None and journal_sha256 is not None:
        _require_role_marker(
            plan,
            source,
            expected,
            role=role,
            journal_sha256=journal_sha256,
            state="active",
        )
    _require_absent(destination, label=f"{plan.adapter} transaction destination")
    _revalidate_parent(plan)
    _require_identity(source, expected, label=f"{plan.adapter} transaction source")
    if role is not None and journal_sha256 is not None:
        _require_role_marker(
            plan,
            source,
            expected,
            role=role,
            journal_sha256=journal_sha256,
            state="active",
        )
    _require_absent(destination, label=f"{plan.adapter} transaction destination")
    rename_noreplace(source, destination, plan.parent_identity)
    _revalidate_parent(plan)
    moved = object_identity(destination)
    if moved != expected:
        if moved is not None and object_identity(source) is None:
            _restore_moved_path(
                plan,
                destination,
                source,
                moved,
                label=f"{plan.adapter} transaction move",
            )
        raise SyncError(f"{plan.adapter} transaction source changed during atomic move")
    _require_identity(destination, expected, label=f"{plan.adapter} moved transaction object")
    if role is not None and journal_sha256 is not None:
        try:
            _require_role_marker(
                plan,
                destination,
                expected,
                role=role,
                journal_sha256=journal_sha256,
                state="active",
            )
        except (OSError, SyncError) as proof_error:
            try:
                _restore_moved_path(
                    plan,
                    destination,
                    source,
                    moved,
                    label=f"{plan.adapter} transaction ownership proof",
                )
            except SyncError as restore_error:
                raise restore_error from proof_error
            raise SyncError(
                f"{plan.adapter} moved object failed its ownership marker postcheck"
            ) from proof_error
    _flush_directory(plan.target.parent)


def _remove_owned_path(
    plan: AdapterPlan,
    path: Path,
    expected: ObjectIdentity,
    *,
    quarantine: Path | None = None,
    role: str | None = None,
    journal_sha256: str | None = None,
) -> None:
    """Quarantine an owned object atomically before removing its contents.

    Moving the checked name to an unpredictable sibling makes a replacement at the
    public transaction path harmless.  The identity is checked again after that
    atomic move; an object that won the race is restored and never deleted.
    """
    if path.parent != plan.target.parent:
        raise SyncError("transaction cleanup escaped its verified adapter parent")
    if expected.file_type != stat.S_IFDIR:
        raise SyncError("transaction cleanup supports owned directory trees only")
    if (role is None) != (journal_sha256 is None):
        raise SyncError("transaction cleanup has incomplete marker proof")
    _revalidate_parent(plan)
    if quarantine is None:
        quarantine = path.parent / f".{path.name}.cleanup-{uuid.uuid4().hex}"
    if quarantine.parent != plan.target.parent:
        raise SyncError("transaction cleanup quarantine escaped its verified adapter parent")
    source_identity = object_identity(path)
    quarantine_identity = object_identity(quarantine)
    tombstone_identity = (
        None
        if role is None or journal_sha256 is None
        else _cleanup_tombstone_identity(
            plan,
            role=role,
            journal_sha256=journal_sha256,
        )
    )
    if source_identity is not None and quarantine_identity is not None:
        raise SyncError(f"{plan.adapter} transaction cleanup has two owned candidates")
    if source_identity is None:
        if quarantine_identity is None and tombstone_identity is not None:
            _remove_cleanup_tombstone(
                plan,
                role=role,
                journal_sha256=journal_sha256,
            )
            _flush_directory(plan.target.parent)
            return
        if quarantine_identity != expected:
            raise SyncError(
                f"{plan.adapter} transaction cleanup object changed before quarantine"
            )
    else:
        if source_identity != expected:
            raise SyncError(
                f"{plan.adapter} transaction cleanup object changed ownership"
            )
        if role is not None and journal_sha256 is not None:
            _require_role_marker(
                plan,
                path,
                expected,
                role=role,
                journal_sha256=journal_sha256,
                state="active",
            )
            expected_sha256 = (
                plan.prepared_tree_sha256
                if role == "new"
                else plan.original_tree_sha256
            )
            if expected_sha256 is None:
                raise SyncError(
                    f"{plan.adapter} transaction cleanup has no journal-bound digest"
                )
            _verify_journal_bound_tree(
                plan,
                path,
                expected,
                expected_sha256,
                role=role,
                journal_sha256=journal_sha256,
                marker_state="active",
            )
        _require_absent(
            quarantine,
            label=f"{plan.adapter} transaction cleanup quarantine",
        )
        rename_noreplace(path, quarantine, plan.parent_identity)
    moved_identity = object_identity(quarantine)
    if moved_identity != expected:
        if object_identity(path) is None and moved_identity is not None:
            _restore_moved_path(
                plan,
                quarantine,
                path,
                moved_identity,
                label=f"{plan.adapter} transaction cleanup",
            )
        raise SyncError(
            f"{plan.adapter} transaction cleanup object changed before quarantine"
        )
    marker_state: str | None = None
    if role is not None and journal_sha256 is not None:
        try:
            marker_state = _role_marker_state(
                plan,
                quarantine,
                expected,
                role=role,
                journal_sha256=journal_sha256,
            )
        except (OSError, SyncError) as proof_error:
            try:
                _restore_moved_path(
                    plan,
                    quarantine,
                    path,
                    moved_identity,
                    label=f"{plan.adapter} cleanup ownership proof",
                )
            except SyncError as restore_error:
                raise restore_error from proof_error
            raise SyncError(
                f"{plan.adapter} quarantined object failed its ownership marker postcheck"
            ) from proof_error
        if marker_state == "active":
            if tombstone_identity is None:
                expected_sha256 = (
                    plan.prepared_tree_sha256
                    if role == "new"
                    else plan.original_tree_sha256
                )
                if expected_sha256 is None:
                    raise SyncError(
                        f"{plan.adapter} transaction cleanup has no journal-bound digest"
                    )
                _verify_journal_bound_tree(
                    plan,
                    quarantine,
                    expected,
                    expected_sha256,
                    role=role,
                    journal_sha256=journal_sha256,
                    marker_state="active",
                )
            _ensure_cleanup_tombstone(
                plan,
                role=role,
                journal_sha256=journal_sha256,
            )
        elif marker_state == "private-removing":
            pass
        elif marker_state == "absent" and tombstone_identity is not None:
            raise SyncError(
                f"{plan.adapter} markerless cleanup quarantine requires manual recovery"
            )
        else:
            raise SyncError(
                f"{plan.adapter} quarantined cleanup lost its durable ownership proof"
            )
    _remove_quarantined_directory(
        plan,
        quarantine,
        expected,
        role=role,
        journal_sha256=journal_sha256,
        marker_state=marker_state,
    )
    _flush_directory(plan.target.parent)
    if role is not None and journal_sha256 is not None:
        _remove_cleanup_tombstone(
            plan,
            role=role,
            journal_sha256=journal_sha256,
        )
    _revalidate_parent(plan)
    _require_absent(path, label=f"{plan.adapter} cleaned transaction path")


def _remove_quarantined_directory(
    plan: AdapterPlan,
    quarantine: Path,
    expected: ObjectIdentity,
    *,
    role: str | None = None,
    journal_sha256: str | None = None,
    marker_state: str | None = None,
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
        if role is not None and journal_sha256 is not None:
            if marker_state not in {"active", "private-removing", "absent"}:
                raise SyncError("quarantined cleanup has an invalid marker state")
        if role is not None and journal_sha256 is not None and marker_state == "active":
            _require_role_marker_descriptor(
                descriptor,
                quarantine,
                _marker_bytes(plan, role=role, journal_sha256=journal_sha256),
                name=ACTIVE_MARKER_NAME,
            )
            _remove_directory_contents(
                descriptor,
                quarantine,
                preserve_name=ACTIVE_MARKER_NAME,
            )
            expected_marker = _marker_bytes(
                plan,
                role=role,
                journal_sha256=journal_sha256,
            )
            marker_identity = _require_role_marker_descriptor(
                descriptor,
                quarantine,
                expected_marker,
                name=ACTIVE_MARKER_NAME,
            )
            private_marker = quarantine / _private_removing_marker_name(
                role,
                journal_sha256,
            )
            rename_noreplace(quarantine / ACTIVE_MARKER_NAME, private_marker, expected)
            _require_role_marker(
                plan,
                quarantine,
                expected,
                role=role,
                journal_sha256=journal_sha256,
                state="private-removing",
            )
            _flush_directory(quarantine)
            _delete_quarantined_regular_file(
                private_marker,
                marker_identity,
                expected,
                label="active transaction marker",
            )
        elif role is not None and journal_sha256 is not None:
            private_name = _private_removing_marker_name(role, journal_sha256)
            entries = _list_directory(descriptor, quarantine)
            if entries == [private_name]:
                marker_identity = _require_role_marker_descriptor(
                    descriptor,
                    quarantine,
                    _marker_bytes(plan, role=role, journal_sha256=journal_sha256),
                    name=private_name,
                )
                _delete_quarantined_regular_file(
                    quarantine / private_name,
                    marker_identity,
                    expected,
                    label="active transaction marker",
                )
            elif entries:
                raise SyncError(
                    "marker-free cleanup quarantine is not empty despite its tombstone"
                )
        else:
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
        os.fsync(parent_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)
    _require_absent(
        quarantine,
        label=f"{plan.adapter} quarantined cleanup directory",
    )


def _remove_directory_contents(
    descriptor: int,
    path: Path,
    *,
    preserve_name: str | None = None,
) -> None:
    """Empty one already-open directory using no-follow, descriptor-relative calls."""
    for name in _list_directory(descriptor, path):
        if name == preserve_name:
            continue
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
    if set(HOST_GUIDANCE_SOURCES) != set(ADAPTER_SKILLS):
        raise SyncError("every adapter must own exactly one host guidance source")
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
        expected[adapter] = expected_tree(
            adapter,
            canonical_directories,
            canonical_files,
            load_host_guidance(adapter),
        )
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
            promotion_nonce = uuid.uuid4().hex
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
            reserved = {
                PurePosixPath(ACTIVE_MARKER_NAME),
                PurePosixPath(RETIRED_MARKER_NAME),
                PurePosixPath(REMOVING_MARKER_NAME),
            }
            if reserved & (set(directories) | set(files)):
                raise SyncError("adapter payload contains a reserved transaction marker")
            target_identity = object_identity(target)
            if (
                target_identity is not None
                and target_identity.file_type != stat.S_IFDIR
            ):
                raise SyncError(f"{adapter} adapter target is not a directory")
            prepared_tree_sha256 = _tree_payload_sha256(directories, files)
            original_tree_sha256 = (
                None
                if target_identity is None
                else _snapshot_payload_sha256(target, target_identity)
            )
            staging, staging_identity = create_staging_root(
                target.parent,
                parent_identity,
                prefix=(
                    f".{target.name}.sync-{transaction_id}-{promotion_nonce}-"
                ),
            )
            previous = target.parent / (
                f".{target.name}.previous-{transaction_id}-{promotion_nonce}"
            )
            _require_absent(previous, label=f"{adapter} previous target")
            plan = AdapterPlan(
                adapter=adapter,
                transaction_id=transaction_id,
                promotion_nonce=promotion_nonce,
                target=target,
                staging=staging,
                previous=previous,
                had_target=target_identity is not None,
                parent_identity=parent_identity,
                staging_identity=staging_identity,
                target_identity=target_identity,
                prepared_tree_sha256=prepared_tree_sha256,
                original_tree_sha256=original_tree_sha256,
                directories=directories,
                files=files,
            )
            for role in ("new", "old"):
                _require_absent(
                    _cleanup_quarantine(plan, role),
                    label=f"{adapter} {role} cleanup quarantine",
                )
                _require_absent(
                    _cleanup_tombstone(plan, role),
                    label=f"{adapter} {role} cleanup tombstone",
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


def _parse_uuid_hex(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 32:
        raise SyncError(f"synchronization journal has an invalid {field}")
    try:
        parsed = uuid.UUID(hex=value)
    except ValueError as exc:
        raise SyncError(f"synchronization journal has an invalid {field}") from exc
    if parsed.hex != value or parsed.version != 4:
        raise SyncError(f"synchronization journal has an invalid {field}")
    return value


def _parse_sha256(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SyncError(f"synchronization state has an invalid {field}")
    return value


def _cleanup_quarantine(plan: AdapterPlan, role: str) -> Path:
    if role not in {"new", "old"}:
        raise SyncError("transaction cleanup has an invalid marker role")
    return plan.target.parent / (
        f".{plan.target.name}.cleanup-{role}-"
        f"{plan.transaction_id}-{plan.promotion_nonce}"
    )


def _cleanup_tombstone(plan: AdapterPlan, role: str) -> Path:
    if role not in {"new", "old"}:
        raise SyncError("transaction cleanup has an invalid tombstone role")
    return SYNC_STATE_DIR / (
        f"sync-adapters.cleanup-{role}-"
        f"{plan.transaction_id}-{plan.promotion_nonce}.json"
    )


def _cleanup_tombstone_bytes(
    plan: AdapterPlan,
    *,
    role: str,
    journal_sha256: str,
) -> bytes:
    identity = plan.staging_identity if role == "new" else plan.target_identity
    if identity is None:
        raise SyncError("transaction cleanup tombstone refers to an absent tree")
    payload = {
        "adapter": plan.adapter,
        "journal_sha256": journal_sha256,
        "promotion_nonce": plan.promotion_nonce,
        "quarantine": _relative_journal_path(_cleanup_quarantine(plan, role)),
        "role": role,
        "root_identity": _identity_payload(identity),
        "transaction_id": plan.transaction_id,
        "version": JOURNAL_VERSION,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _marker_bytes(
    plan: AdapterPlan,
    *,
    role: str,
    journal_sha256: str,
) -> bytes:
    if role not in {"new", "old"}:
        raise SyncError("transaction marker has an invalid role")
    identity = plan.staging_identity if role == "new" else plan.target_identity
    if identity is None:
        raise SyncError("transaction marker refers to an absent old target")
    payload = {
        "adapter": plan.adapter,
        "journal_sha256": journal_sha256,
        "promotion_nonce": plan.promotion_nonce,
        "role": role,
        "root_identity": _identity_payload(identity),
        "transaction_id": plan.transaction_id,
        "version": JOURNAL_VERSION,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _private_removing_marker_name(role: str, journal_sha256: str) -> str:
    if role not in {"old", "new"}:
        raise SyncError("transaction marker role is invalid")
    _parse_sha256(journal_sha256, field="journal_sha256")
    return f"{PRIVATE_REMOVING_MARKER_PREFIX}{role}-{journal_sha256}.json"


def _public_marker_name(state: str) -> str:
    if state == "active":
        return ACTIVE_MARKER_NAME
    if state == "retired":
        return RETIRED_MARKER_NAME
    if state == "removing":
        return REMOVING_MARKER_NAME
    raise SyncError("transaction marker has an invalid lifecycle state")


def _marker_name_for_state(state: str, *, role: str, journal_sha256: str) -> str:
    if state == "private-removing":
        return _private_removing_marker_name(role, journal_sha256)
    return _public_marker_name(state)


def _read_exact_descriptor(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            raise SyncError("transaction state file was truncated while being read")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise SyncError("transaction state file grew while being read")
    return b"".join(chunks)


def _optional_stat_child(
    parent_descriptor: int,
    parent_path: Path,
    name: str,
) -> os.stat_result | None:
    try:
        if os.name == "nt":
            return os.lstat(parent_path / name)
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SyncError("transaction marker changed while it was inspected") from exc


def _require_role_marker_descriptor(
    root_descriptor: int,
    root_path: Path,
    expected: bytes,
    *,
    name: str,
) -> ObjectIdentity:
    details = _optional_stat_child(root_descriptor, root_path, name)
    if details is None:
        raise SyncError("transaction ownership marker is missing")
    identity = _identity_from_stat(details)
    if identity.file_type != stat.S_IFREG or details.st_nlink != 1:
        raise SyncError("transaction ownership marker is unsafe")
    if stat.S_IMODE(details.st_mode) != 0o600 or details.st_size != len(expected):
        raise SyncError("transaction ownership marker is wrong")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=root_descriptor)
    try:
        opened = os.fstat(descriptor)
        if (
            _identity_from_stat(opened) != identity
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_size != len(expected)
        ):
            raise SyncError("transaction ownership marker changed before it was read")
        content = _read_exact_descriptor(descriptor, len(expected))
        current = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if _identity_from_stat(current) != identity or current.st_nlink != 1:
            raise SyncError("transaction ownership marker changed while it was read")
    finally:
        os.close(descriptor)
    if content != expected:
        raise SyncError("transaction ownership marker is wrong")
    return identity


def _require_role_marker(
    plan: AdapterPlan,
    root: Path,
    expected_root: ObjectIdentity,
    *,
    role: str,
    journal_sha256: str,
    state: str,
) -> None:
    descriptor = _open_directory_descriptor(root, expected_root)
    try:
        _require_role_marker_descriptor(
            descriptor,
            root,
            _marker_bytes(plan, role=role, journal_sha256=journal_sha256),
            name=_marker_name_for_state(
                state,
                role=role,
                journal_sha256=journal_sha256,
            ),
        )
        if _identity_from_stat(os.fstat(descriptor)) != expected_root:
            raise SyncError("transaction marker root changed while it was verified")
    finally:
        os.close(descriptor)


def _role_marker_state(
    plan: AdapterPlan,
    root: Path,
    expected_root: ObjectIdentity,
    *,
    role: str,
    journal_sha256: str,
) -> str:
    descriptor = _open_directory_descriptor(root, expected_root)
    try:
        markers = {
            "active": _optional_stat_child(descriptor, root, ACTIVE_MARKER_NAME),
            "retired": _optional_stat_child(descriptor, root, RETIRED_MARKER_NAME),
            "removing": _optional_stat_child(descriptor, root, REMOVING_MARKER_NAME),
            "private-removing": _optional_stat_child(
                descriptor,
                root,
                _private_removing_marker_name(role, journal_sha256),
            ),
        }
        present = [state for state, details in markers.items() if details is not None]
        if len(present) > 1:
            raise SyncError("transaction root contains conflicting ownership markers")
        if not present:
            return "absent"
        state = present[0]
        _require_role_marker_descriptor(
            descriptor,
            root,
            _marker_bytes(plan, role=role, journal_sha256=journal_sha256),
            name=_marker_name_for_state(
                state,
                role=role,
                journal_sha256=journal_sha256,
            ),
        )
        return state
    finally:
        os.close(descriptor)


def _create_role_marker(
    plan: AdapterPlan,
    root: Path,
    expected_root: ObjectIdentity,
    *,
    role: str,
    journal_sha256: str,
) -> None:
    expected = _marker_bytes(plan, role=role, journal_sha256=journal_sha256)
    root_descriptor = _open_directory_descriptor(root, expected_root)
    descriptor: int | None = None
    identity: ObjectIdentity | None = None
    try:
        for name in (ACTIVE_MARKER_NAME, RETIRED_MARKER_NAME, REMOVING_MARKER_NAME):
            if _optional_stat_child(root_descriptor, root, name) is None:
                continue
            raise SyncError("transaction ownership marker destination is occupied")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            ACTIVE_MARKER_NAME,
            flags,
            0o600,
            dir_fd=root_descriptor,
        )
        opened = os.fstat(descriptor)
        identity = _identity_from_stat(opened)
        if identity.file_type != stat.S_IFREG or opened.st_nlink != 1:
            raise SyncError("transaction ownership marker is not a unique regular file")
        _write_all(descriptor, expected)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        written = os.fstat(descriptor)
        if (
            _identity_from_stat(written) != identity
            or written.st_nlink != 1
            or written.st_size != len(expected)
        ):
            raise SyncError("transaction ownership marker failed write verification")
        os.close(descriptor)
        descriptor = None
        verify_created_staging_entry(
            root_descriptor,
            root,
            ACTIVE_MARKER_NAME,
            identity,
            "transaction ownership marker",
        )
        os.fsync(root_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(root_descriptor)
    _require_role_marker(
        plan,
        root,
        expected_root,
        role=role,
        journal_sha256=journal_sha256,
        state="active",
    )


def _retire_role_marker(
    plan: AdapterPlan,
    root: Path,
    expected_root: ObjectIdentity,
    *,
    role: str,
    journal_sha256: str,
) -> None:
    state = _role_marker_state(
        plan,
        root,
        expected_root,
        role=role,
        journal_sha256=journal_sha256,
    )
    if state == "absent":
        return
    if state == "retired":
        return
    if state == "removing":
        return
    if state == "private-removing":
        return
    expected = _marker_bytes(plan, role=role, journal_sha256=journal_sha256)
    active = root / ACTIVE_MARKER_NAME
    retired = root / RETIRED_MARKER_NAME
    root_descriptor = _open_directory_descriptor(root, expected_root)
    try:
        marker_identity = _require_role_marker_descriptor(
            root_descriptor,
            root,
            expected,
            name=ACTIVE_MARKER_NAME,
        )
        if _optional_stat_child(root_descriptor, root, RETIRED_MARKER_NAME) is not None:
            raise SyncError("retired transaction marker destination is occupied")
        if _optional_stat_child(root_descriptor, root, REMOVING_MARKER_NAME) is not None:
            raise SyncError("removing transaction marker destination is occupied")
    finally:
        os.close(root_descriptor)
    rename_noreplace(active, retired, expected_root)
    moved_identity = object_identity(retired)
    try:
        if moved_identity != marker_identity:
            raise SyncError("active transaction marker changed during retirement")
        _require_role_marker(
            plan,
            root,
            expected_root,
            role=role,
            journal_sha256=journal_sha256,
            state="retired",
        )
    except (OSError, SyncError) as proof_error:
        if moved_identity is not None:
            try:
                _restore_quarantined_regular_file(
                    retired,
                    active,
                    moved_identity,
                    expected_root,
                    label="transaction marker retirement",
                )
            except SyncError as restore_error:
                raise restore_error from proof_error
        raise SyncError("retired transaction marker failed its atomic postcheck") from proof_error
    _flush_directory(root)


def _remove_retired_role_marker(
    plan: AdapterPlan,
    root: Path,
    expected_root: ObjectIdentity,
    *,
    role: str,
    journal_sha256: str,
) -> None:
    state = _role_marker_state(
        plan,
        root,
        expected_root,
        role=role,
        journal_sha256=journal_sha256,
    )
    if state == "absent":
        return
    if state == "active":
        raise SyncError("transaction ownership marker was not retired before removal")
    expected = _marker_bytes(plan, role=role, journal_sha256=journal_sha256)
    retired = root / RETIRED_MARKER_NAME
    removing = root / REMOVING_MARKER_NAME
    private_removing = root / _private_removing_marker_name(role, journal_sha256)
    if state == "retired":
        root_descriptor = _open_directory_descriptor(root, expected_root)
        try:
            marker_identity = _require_role_marker_descriptor(
                root_descriptor,
                root,
                expected,
                name=RETIRED_MARKER_NAME,
            )
            if _optional_stat_child(root_descriptor, root, REMOVING_MARKER_NAME) is not None:
                raise SyncError("removing transaction marker destination is occupied")
            if (
                _optional_stat_child(
                    root_descriptor,
                    root,
                    private_removing.name,
                )
                is not None
            ):
                raise SyncError("private removing transaction marker destination is occupied")
        finally:
            os.close(root_descriptor)
        rename_noreplace(retired, removing, expected_root)
        moved_identity = object_identity(removing)
        try:
            if moved_identity != marker_identity:
                raise SyncError("retired transaction marker changed during quarantine")
            _require_role_marker(
                plan,
                root,
                expected_root,
                role=role,
                journal_sha256=journal_sha256,
                state="removing",
            )
        except (OSError, SyncError) as proof_error:
            if moved_identity is not None:
                try:
                    _restore_quarantined_regular_file(
                        removing,
                        retired,
                        moved_identity,
                        expected_root,
                        label="retired transaction marker",
                    )
                except SyncError as restore_error:
                    raise restore_error from proof_error
            raise SyncError(
                "retired transaction marker failed its atomic quarantine postcheck"
            ) from proof_error
        _flush_directory(root)
        state = "removing"
    if state == "removing":
        marker_identity = object_identity(removing)
        if marker_identity is None:
            raise SyncError("removing transaction marker disappeared")
        _require_role_marker(
            plan,
            root,
            expected_root,
            role=role,
            journal_sha256=journal_sha256,
            state="removing",
        )
        rename_noreplace(removing, private_removing, expected_root)
        if object_identity(private_removing) != marker_identity:
            raise SyncError("removing transaction marker changed during private quarantine")
        _require_role_marker(
            plan,
            root,
            expected_root,
            role=role,
            journal_sha256=journal_sha256,
            state="private-removing",
        )
        _flush_directory(root)
    else:
        marker_identity = object_identity(private_removing)
        if marker_identity is None:
            raise SyncError("private removing transaction marker disappeared")
        _require_role_marker(
            plan,
            root,
            expected_root,
            role=role,
            journal_sha256=journal_sha256,
            state="private-removing",
        )
    _delete_quarantined_regular_file(
        private_removing,
        marker_identity,
        expected_root,
        label="retired transaction marker",
    )
    _flush_directory(root)


def _flush_directory(directory: Path) -> None:
    """Durably flush one no-follow directory descriptor."""
    if os.name == "nt":
        return
    identity = object_identity(directory)
    if identity is None or identity.file_type != stat.S_IFDIR:
        raise SyncError("directory durability target is not a stable directory")
    descriptor = _open_directory_descriptor(directory, identity)
    try:
        os.fsync(descriptor)
        if _identity_from_stat(os.fstat(descriptor)) != identity:
            raise SyncError("directory durability target changed ownership")
    finally:
        os.close(descriptor)


def _read_owned_regular_file(
    path: Path,
    expected: ObjectIdentity,
    parent_identity: ObjectIdentity,
    *,
    label: str,
    max_bytes: int = MAX_STATE_FILE_BYTES,
) -> bytes:
    """Read exact bytes from one descriptor-anchored, uniquely linked regular file."""
    parent_descriptor = _open_directory_descriptor(path.parent, parent_identity)
    descriptor: int | None = None
    try:
        details = _stat_child(parent_descriptor, path.parent, path.name)
        if (
            _identity_from_stat(details) != expected
            or not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_size < 0
            or details.st_size > max_bytes
            or stat.S_IMODE(details.st_mode) != 0o600
        ):
            raise SyncError(f"{label} changed ownership before it was read")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        if (
            _identity_from_stat(opened) != expected
            or opened.st_nlink != 1
            or opened.st_size != details.st_size
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise SyncError(f"{label} changed before it was read")
        encoded = _read_exact_descriptor(descriptor, opened.st_size)
        current = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            _identity_from_stat(current) != expected
            or current.st_nlink != 1
            or current.st_size != opened.st_size
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            raise SyncError(f"{label} changed while it was read")
        return encoded
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _restore_quarantined_regular_file(
    quarantine: Path,
    source: Path,
    moved: ObjectIdentity,
    parent_identity: ObjectIdentity,
    *,
    label: str,
) -> None:
    """Restore a moved replacement without overwriting a new public occupant."""
    if object_identity(source) is not None:
        raise SyncError(f"{label} public source became occupied; both objects were preserved")
    try:
        rename_noreplace(quarantine, source, parent_identity)
    except (OSError, SyncError) as restore_error:
        raise SyncError(
            f"{label} replacement could not be restored; both objects were preserved"
        ) from restore_error
    if object_identity(source) != moved:
        raise SyncError(f"{label} restored replacement changed ownership")


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
            _restore_quarantined_regular_file(
                quarantine,
                path,
                moved,
                parent_identity,
                label=label,
            )
        raise SyncError(f"{label} changed before its atomic quarantine")
    return quarantine


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
    expected_bytes: bytes | None = None,
) -> None:
    if expected_bytes is None:
        expected_bytes = _read_owned_regular_file(
            path,
            expected,
            parent_identity,
            label=label,
        )
    quarantine = _quarantine_owned_regular_file(
        path,
        expected,
        parent_identity,
        label=label,
    )
    try:
        quarantined_bytes = _read_owned_regular_file(
            quarantine,
            expected,
            parent_identity,
            label=f"{label} quarantine",
        )
        if quarantined_bytes != expected_bytes:
            raise SyncError(f"{label} bytes changed during atomic quarantine")
    except (OSError, SyncError) as proof_error:
        moved = object_identity(quarantine)
        if moved is not None:
            try:
                _restore_quarantined_regular_file(
                    quarantine,
                    path,
                    moved,
                    parent_identity,
                    label=label,
                )
            except SyncError as restore_error:
                raise restore_error from proof_error
        raise SyncError(f"{label} failed exact quarantine verification") from proof_error
    _delete_quarantined_regular_file(
        quarantine,
        expected,
        parent_identity,
        label=label,
    )


def _publish_state_file(path: Path, encoded: bytes, *, label: str) -> ObjectIdentity:
    """Publish one fsynced 0600 state file with atomic no-replace semantics."""
    if len(encoded) > MAX_STATE_FILE_BYTES:
        raise SyncError(f"{label} exceeds its size limit")
    assert_repo_path(SYNC_STATE_DIR, include_leaf=True, label="synchronization state directory")
    SYNC_STATE_DIR.mkdir(parents=True, exist_ok=True)
    assert_repo_path(path, include_leaf=False, label=label)
    state_identity = object_identity(SYNC_STATE_DIR)
    if state_identity is None or state_identity.file_type != stat.S_IFDIR:
        raise SyncError("synchronization state directory is not stable")
    if object_identity(path) is not None:
        raise SyncError(f"{label} destination is unexpectedly occupied")

    temporary = SYNC_STATE_DIR / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    state_descriptor = _open_directory_descriptor(SYNC_STATE_DIR, state_identity)
    descriptor: int | None = None
    temporary_identity: ObjectIdentity | None = None
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
            raise SyncError(f"temporary {label} is not a unique regular file")
        _write_all(descriptor, encoded)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        written = os.fstat(descriptor)
        if (
            _identity_from_stat(written) != temporary_identity
            or written.st_nlink != 1
            or written.st_size != len(encoded)
        ):
            raise SyncError(f"temporary {label} failed write verification")
        os.close(descriptor)
        descriptor = None
        verify_created_staging_entry(
            state_descriptor,
            SYNC_STATE_DIR,
            temporary.name,
            temporary_identity,
            f"temporary {label}",
        )
        rename_noreplace(temporary, path, state_identity)
        _require_identity(
            path,
            temporary_identity,
            label=label,
        )
        _flush_directory(SYNC_STATE_DIR)
        return temporary_identity
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(state_descriptor)
        current_temporary = object_identity(temporary)
        if current_temporary is not None:
            if current_temporary != temporary_identity:
                raise SyncError(f"temporary {label} path changed ownership")
            _remove_owned_regular_file(
                temporary,
                temporary_identity,
                state_identity,
                label=f"temporary {label}",
            )


def _journal_bytes(plans: list[AdapterPlan]) -> bytes:
    if [plan.adapter for plan in plans] != list(ADAPTER_SKILLS):
        raise SyncError("transaction journal must cover every adapter exactly once")
    transaction_ids = {plan.transaction_id for plan in plans}
    if len(transaction_ids) != 1:
        raise SyncError("transaction journal plans disagree on their transaction id")
    if len({plan.promotion_nonce for plan in plans}) != len(plans):
        raise SyncError("transaction journal promotion nonces must be unique")
    transaction_id = next(iter(transaction_ids))
    _parse_uuid_hex(transaction_id, field="transaction id")
    for plan in plans:
        _parse_sha256(
            plan.prepared_tree_sha256,
            field=f"{plan.adapter} prepared tree digest",
        )
        if plan.prepared_tree_sha256 != _tree_payload_sha256(
            plan.directories,
            plan.files,
        ):
            raise SyncError("transaction journal prepared tree digest is inconsistent")
        if plan.target_identity is None:
            if plan.original_tree_sha256 is not None:
                raise SyncError("transaction journal has a digest for an absent old target")
        else:
            _parse_sha256(
                plan.original_tree_sha256,
                field=f"{plan.adapter} original tree digest",
            )
    payload = {
        "phase": "prepared",
        "plans": [
            {
                "adapter": plan.adapter,
                "had_target": plan.had_target,
                "parent_identity": _identity_payload(plan.parent_identity),
                "previous": _relative_journal_path(plan.previous),
                "prepared_tree_sha256": plan.prepared_tree_sha256,
                "promotion_nonce": plan.promotion_nonce,
                "staging": _relative_journal_path(plan.staging),
                "staging_identity": _identity_payload(plan.staging_identity),
                "target": _relative_journal_path(plan.target),
                "target_identity": _identity_payload(plan.target_identity),
                "original_tree_sha256": plan.original_tree_sha256,
            }
            for plan in plans
        ],
        "transaction_id": transaction_id,
        "version": JOURNAL_VERSION,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _install_transaction_markers(plans: list[AdapterPlan], journal_sha256: str) -> None:
    """Install all role proofs only after the durable journal exists."""
    for plan in plans:
        for root, expected_root in (
            (plan.staging, plan.staging_identity),
            (plan.target, plan.target_identity),
        ):
            if expected_root is None:
                continue
            descriptor = _open_directory_descriptor(root, expected_root)
            try:
                for name in (
                    ACTIVE_MARKER_NAME,
                    RETIRED_MARKER_NAME,
                    REMOVING_MARKER_NAME,
                ):
                    if _optional_stat_child(descriptor, root, name) is None:
                        continue
                    raise SyncError("transaction ownership marker destination is occupied")
            finally:
                os.close(descriptor)
    for plan in plans:
        _create_role_marker(
            plan,
            plan.staging,
            plan.staging_identity,
            role="new",
            journal_sha256=journal_sha256,
        )
        if plan.target_identity is not None:
            _create_role_marker(
                plan,
                plan.target,
                plan.target_identity,
                role="old",
                journal_sha256=journal_sha256,
            )


def write_transaction_journal(
    plans: list[AdapterPlan],
    *,
    phase: str = "prepared",
    expected_current: ObjectIdentity | None = None,
) -> ObjectIdentity:
    """Publish one immutable prepared journal, then bind both tree roles to it."""
    if phase != "prepared" or expected_current is not None:
        raise SyncError("prepared synchronization journal is immutable")
    encoded = _journal_bytes(plans)
    identity = _publish_state_file(
        SYNC_JOURNAL_PATH,
        encoded,
        label="synchronization journal",
    )
    _install_transaction_markers(plans, hashlib.sha256(encoded).hexdigest())
    return identity


def write_commit_receipt(
    plans: list[AdapterPlan],
    prepared_journal: ObjectIdentity,
) -> ObjectIdentity:
    """Commit an immutable prepared journal with a separate no-replace receipt."""
    journal = _read_transaction_journal()
    if journal.identity != prepared_journal:
        raise SyncError("prepared synchronization journal changed before commit receipt")
    if _journal_bytes(plans) != journal.encoded:
        raise SyncError("commit receipt plans do not match the prepared journal")
    positions = [
        _recovery_positions(
            plan,
            journal_sha256=journal.sha256,
            committed=True,
        )
        for plan in plans
    ]
    for plan, state in zip(plans, positions, strict=True):
        expected_old = "previous" if plan.target_identity is not None else None
        if state.old != expected_old or state.new_marker != "active":
            raise SyncError("commit receipt requires every promoted rollback copy")
        _verify_exact_committed_tree(
            plan,
            journal_sha256=journal.sha256,
            marker_state="active",
        )
        _verify_committed_old_tree(
            plan,
            state,
            journal_sha256=journal.sha256,
        )
    payload = {
        "journal_sha256": journal.sha256,
        "transaction_id": journal.transaction_id,
        "version": RECEIPT_VERSION,
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    return _publish_state_file(
        SYNC_RECEIPT_PATH,
        encoded,
        label="synchronization commit receipt",
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


def _read_state_file(path: Path, *, label: str) -> tuple[bytes, ObjectIdentity]:
    assert_repo_path(path, include_leaf=False, label=label)
    state_identity = object_identity(SYNC_STATE_DIR)
    if state_identity is None or state_identity.file_type != stat.S_IFDIR:
        raise SyncError("synchronization state directory is missing or unsafe")
    file_identity = object_identity(path)
    if file_identity is None or file_identity.file_type != stat.S_IFREG:
        raise SyncError(f"{label} is missing or unsafe")
    state_descriptor = _open_directory_descriptor(SYNC_STATE_DIR, state_identity)
    descriptor: int | None = None
    try:
        details = _stat_child(state_descriptor, SYNC_STATE_DIR, path.name)
        if (
            _identity_from_stat(details) != file_identity
            or details.st_nlink != 1
            or details.st_size > MAX_STATE_FILE_BYTES
        ):
            raise SyncError(f"{label} changed ownership before it was read")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path.name, flags, dir_fd=state_descriptor)
        opened = os.fstat(descriptor)
        if (
            _identity_from_stat(opened) != file_identity
            or opened.st_nlink != 1
            or opened.st_size > MAX_STATE_FILE_BYTES
        ):
            raise SyncError(f"{label} changed ownership before it was read")
        encoded = _read_exact_descriptor(descriptor, opened.st_size)
        current = os.stat(path.name, dir_fd=state_descriptor, follow_symlinks=False)
        if _identity_from_stat(current) != file_identity or current.st_nlink != 1:
            raise SyncError(f"{label} changed ownership while it was read")
        if stat.S_IMODE(current.st_mode) != 0o600:
            raise SyncError(f"{label} must have mode 0600")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(state_descriptor)
    return encoded, file_identity


def _cleanup_tombstone_identity(
    plan: AdapterPlan,
    *,
    role: str,
    journal_sha256: str,
) -> ObjectIdentity | None:
    path = _cleanup_tombstone(plan, role)
    identity = object_identity(path)
    if identity is None:
        return None
    encoded, verified_identity = _read_state_file(
        path,
        label=f"{plan.adapter} {role} cleanup tombstone",
    )
    if encoded != _cleanup_tombstone_bytes(
        plan,
        role=role,
        journal_sha256=journal_sha256,
    ):
        raise SyncError(f"{plan.adapter} cleanup tombstone is wrong")
    return verified_identity


def _ensure_cleanup_tombstone(
    plan: AdapterPlan,
    *,
    role: str,
    journal_sha256: str,
) -> ObjectIdentity:
    current = _cleanup_tombstone_identity(
        plan,
        role=role,
        journal_sha256=journal_sha256,
    )
    if current is not None:
        return current
    return _publish_state_file(
        _cleanup_tombstone(plan, role),
        _cleanup_tombstone_bytes(
            plan,
            role=role,
            journal_sha256=journal_sha256,
        ),
        label=f"{plan.adapter} {role} cleanup tombstone",
    )


def _remove_cleanup_tombstone(
    plan: AdapterPlan,
    *,
    role: str,
    journal_sha256: str,
) -> None:
    identity = _cleanup_tombstone_identity(
        plan,
        role=role,
        journal_sha256=journal_sha256,
    )
    if identity is None:
        return
    expected_bytes = _cleanup_tombstone_bytes(
        plan,
        role=role,
        journal_sha256=journal_sha256,
    )
    _remove_state_file(
        _cleanup_tombstone(plan, role),
        identity,
        label=f"{plan.adapter} {role} cleanup tombstone",
        expected_bytes=expected_bytes,
    )


def _read_transaction_journal() -> TransactionJournal:
    encoded, journal_identity = _read_state_file(
        SYNC_JOURNAL_PATH,
        label="synchronization journal",
    )
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError("synchronization journal is invalid; contents redacted") from exc
    if isinstance(payload, dict) and payload.get("version") == 1:
        raise SyncError(
            "synchronization journal version 1 requires explicit manual recovery"
        )
    if (
        not isinstance(payload, dict)
        or payload.get("version") != JOURNAL_VERSION
        or set(payload) != {"phase", "plans", "transaction_id", "version"}
    ):
        raise SyncError("synchronization journal has an unsupported format")
    if payload.get("phase") != "prepared":
        raise SyncError("synchronization journal is not an immutable prepared journal")
    transaction_id = _parse_uuid_hex(
        payload.get("transaction_id"),
        field="transaction id",
    )
    raw_plans = payload.get("plans")
    if not isinstance(raw_plans, list):
        raise SyncError("synchronization journal has an invalid transaction state")
    if len(raw_plans) != len(ADAPTER_SKILLS):
        raise SyncError("synchronization journal does not cover every adapter")

    plans: list[AdapterPlan] = []
    for raw_plan in raw_plans:
        if not isinstance(raw_plan, dict):
            raise SyncError("synchronization journal contains an invalid adapter plan")
        if set(raw_plan) != {
            "adapter",
            "had_target",
            "original_tree_sha256",
            "parent_identity",
            "previous",
            "prepared_tree_sha256",
            "promotion_nonce",
            "staging",
            "staging_identity",
            "target",
            "target_identity",
        }:
            raise SyncError("synchronization journal contains an invalid adapter plan")
        adapter = raw_plan.get("adapter")
        if not isinstance(adapter, str) or adapter not in ADAPTER_SKILLS:
            raise SyncError("synchronization journal contains an unknown adapter")
        promotion_nonce = _parse_uuid_hex(
            raw_plan.get("promotion_nonce"),
            field="promotion nonce",
        )
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
        prepared_tree_sha256 = _parse_sha256(
            raw_plan.get("prepared_tree_sha256"),
            field="prepared tree digest",
        )
        raw_original_tree_sha256 = raw_plan.get("original_tree_sha256")
        original_tree_sha256 = (
            None
            if raw_original_tree_sha256 is None
            else _parse_sha256(
                raw_original_tree_sha256,
                field="original tree digest",
            )
        )
        if parent_identity is None or parent_identity.file_type != stat.S_IFDIR:
            raise SyncError("synchronization journal has an invalid parent identity")
        if staging_identity is None or staging_identity.file_type != stat.S_IFDIR:
            raise SyncError("synchronization journal has an invalid staging identity")
        if target_identity is not None and target_identity.file_type != stat.S_IFDIR:
            raise SyncError("synchronization journal has an invalid target identity")
        expected_target = ADAPTER_SKILLS[adapter]
        if target != expected_target or staging.parent != target.parent or previous.parent != target.parent:
            raise SyncError("synchronization journal contains mismatched adapter paths")
        if not staging.name.startswith(
            f".{target.name}.sync-{transaction_id}-{promotion_nonce}-"
        ) or previous.name != (
            f".{target.name}.previous-{transaction_id}-{promotion_nonce}"
        ):
            raise SyncError("synchronization journal contains unexpected transaction paths")
        if not isinstance(had_target, bool):
            raise SyncError("synchronization journal contains an invalid target state")
        if had_target != (target_identity is not None):
            raise SyncError("synchronization journal target identity contradicts its state")
        if (original_tree_sha256 is not None) != (target_identity is not None):
            raise SyncError(
                "synchronization journal original tree digest contradicts its state"
            )
        plans.append(
            AdapterPlan(
                adapter=adapter,
                transaction_id=transaction_id,
                promotion_nonce=promotion_nonce,
                target=target,
                staging=staging,
                previous=previous,
                had_target=had_target,
                parent_identity=parent_identity,
                staging_identity=staging_identity,
                target_identity=target_identity,
                prepared_tree_sha256=prepared_tree_sha256,
                original_tree_sha256=original_tree_sha256,
                directories=set(),
                files={},
            )
        )
    if [plan.adapter for plan in plans] != list(ADAPTER_SKILLS):
        raise SyncError("synchronization journal adapters are duplicated or out of order")
    if len({plan.promotion_nonce for plan in plans}) != len(plans):
        raise SyncError("synchronization journal promotion nonces are duplicated")
    return TransactionJournal(
        transaction_id=transaction_id,
        plans=plans,
        identity=journal_identity,
        sha256=hashlib.sha256(encoded).hexdigest(),
        encoded=encoded,
    )


def _read_commit_receipt(journal: TransactionJournal) -> CommitReceipt:
    encoded, receipt_identity = _read_state_file(
        SYNC_RECEIPT_PATH,
        label="synchronization commit receipt",
    )
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError("synchronization commit receipt is invalid; contents redacted") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"journal_sha256", "transaction_id", "version"}
        or payload.get("version") != RECEIPT_VERSION
    ):
        raise SyncError("synchronization commit receipt has an unsupported format")
    transaction_id = _parse_uuid_hex(
        payload.get("transaction_id"),
        field="receipt transaction id",
    )
    journal_sha256 = _parse_sha256(
        payload.get("journal_sha256"),
        field="receipt journal digest",
    )
    if transaction_id != journal.transaction_id or journal_sha256 != journal.sha256:
        raise SyncError("synchronization commit receipt does not match its journal")
    return CommitReceipt(identity=receipt_identity, encoded=encoded)


def _remove_state_file(
    path: Path,
    expected: ObjectIdentity,
    *,
    label: str,
    expected_bytes: bytes | None = None,
) -> None:
    state_identity = object_identity(SYNC_STATE_DIR)
    if state_identity is None or state_identity.file_type != stat.S_IFDIR:
        raise SyncError("synchronization state directory is not stable")
    _remove_owned_regular_file(
        path,
        expected,
        state_identity,
        label=label,
        expected_bytes=expected_bytes,
    )
    _flush_directory(SYNC_STATE_DIR)


def _remove_journal(
    expected: ObjectIdentity,
    expected_bytes: bytes | None = None,
) -> None:
    _remove_state_file(
        SYNC_JOURNAL_PATH,
        expected,
        label="synchronization journal",
        expected_bytes=expected_bytes,
    )


def _remove_receipt(
    expected: ObjectIdentity,
    expected_bytes: bytes | None = None,
) -> None:
    _remove_state_file(
        SYNC_RECEIPT_PATH,
        expected,
        label="synchronization commit receipt",
        expected_bytes=expected_bytes,
    )


def _verify_promoted_plans(
    plans: list[AdapterPlan],
    *,
    journal_sha256: str,
) -> None:
    """Verify exact promoted contents while every previous tree is still retained."""
    for plan in plans:
        _verify_exact_committed_tree(
            plan,
            journal_sha256=journal_sha256,
            marker_state="active",
        )
    for plan in plans:
        _verify_exact_committed_tree(
            plan,
            journal_sha256=journal_sha256,
            marker_state="active",
        )


def _position_for_identity(
    actual: ObjectIdentity | None,
    *,
    old: ObjectIdentity | None,
    new: ObjectIdentity,
    label: str,
) -> str | None:
    if actual is None:
        return None
    if old is not None and actual == old:
        return "old"
    if actual == new:
        return "new"
    raise SyncError(f"{label} contains a foreign or ambiguous object")


def _recovery_positions(
    plan: AdapterPlan,
    *,
    journal_sha256: str,
    committed: bool,
) -> RecoveryPositions:
    """Preflight every public and quarantine name before recovery mutates an adapter."""
    _revalidate_parent(plan)
    paths = {
        "target": plan.target,
        "staging": plan.staging,
        "previous": plan.previous,
        "new-quarantine": _cleanup_quarantine(plan, "new"),
        "old-quarantine": _cleanup_quarantine(plan, "old"),
    }
    identities = {name: object_identity(path) for name, path in paths.items()}
    allowed_roles = {
        "target": {"old", "new"},
        "staging": {"new"},
        "previous": {"old"},
        "new-quarantine": {"new"},
        "old-quarantine": {"old"},
    }
    roles: dict[str, str | None] = {}
    for name, actual in identities.items():
        role = _position_for_identity(
            actual,
            old=plan.target_identity,
            new=plan.staging_identity,
            label=f"{plan.adapter} {name}",
        )
        if role is not None and role not in allowed_roles[name]:
            raise SyncError(
                f"{plan.adapter} prepared transaction contains a foreign or ambiguous object"
            )
        roles[name] = role
    old_locations = [name for name, role in roles.items() if role == "old"]
    new_locations = [name for name, role in roles.items() if role == "new"]
    if len(old_locations) > 1 or len(new_locations) > 1:
        raise SyncError(
            f"{plan.adapter} prepared transaction contains duplicate owned objects"
        )
    old_position = old_locations[0] if old_locations else None
    new_position = new_locations[0] if new_locations else None
    old_tombstone = _cleanup_tombstone_identity(
        plan,
        role="old",
        journal_sha256=journal_sha256,
    )
    new_tombstone = _cleanup_tombstone_identity(
        plan,
        role="new",
        journal_sha256=journal_sha256,
    )
    if old_tombstone is not None:
        if old_position is None:
            old_position = "old-tombstone"
        elif old_position != "old-quarantine":
            raise SyncError("old cleanup tombstone contradicts the owned object position")
    if new_tombstone is not None:
        if new_position is None:
            new_position = "new-tombstone"
        elif new_position != "new-quarantine":
            raise SyncError("new cleanup tombstone contradicts the owned object position")
    if plan.target_identity is not None and old_position is None and not committed:
        raise SyncError(f"{plan.adapter} prepared transaction lost its old target")
    if committed:
        if (
            new_position != "target"
            or roles["staging"] is not None
            or roles["new-quarantine"] is not None
            or old_position
            not in {"previous", "old-quarantine", "old-tombstone", None}
        ):
            raise SyncError(
                f"{plan.adapter} committed synchronization has an invalid object state"
            )
    else:
        if old_position in {"old-quarantine", "old-tombstone"}:
            raise SyncError(
                f"{plan.adapter} prepared synchronization has an invalid old quarantine"
            )
        if new_position not in {
            "target",
            "staging",
            "new-quarantine",
            "new-tombstone",
            None,
        }:
            raise SyncError(
                f"{plan.adapter} prepared synchronization has an invalid new object state"
            )

    old_marker: str | None = None
    if old_position is not None:
        assert plan.target_identity is not None
        if old_position == "old-tombstone":
            old_marker = "tombstone"
        else:
            old_root = paths[old_position]
            old_marker = _role_marker_state(
                plan,
                old_root,
                plan.target_identity,
                role="old",
                journal_sha256=journal_sha256,
            )
            if (
                old_position == "old-quarantine"
                and old_marker == "absent"
                and old_tombstone is not None
            ):
                old_marker = "tombstone"
        old_is_terminal = (
            not committed
            and old_position == "target"
            and new_position is None
        )
        if not old_is_terminal and old_marker != "active":
            raise SyncError("old transaction ownership marker is missing or retired")
        if committed and old_marker not in {"active", "private-removing", "tombstone"}:
            raise SyncError("old transaction ownership marker is missing or retired")

    new_marker: str | None = None
    if new_position is not None:
        if new_position == "new-tombstone":
            new_marker = "tombstone"
        else:
            new_root = paths[new_position]
            new_marker = _role_marker_state(
                plan,
                new_root,
                plan.staging_identity,
                role="new",
                journal_sha256=journal_sha256,
            )
            if (
                new_position == "new-quarantine"
                and new_marker == "absent"
                and new_tombstone is not None
            ):
                new_marker = "tombstone"
        if committed:
            if new_marker not in {
                "active",
                "retired",
                "removing",
                "private-removing",
                "absent",
            }:
                raise SyncError("new transaction ownership marker has an invalid state")
        elif new_position == "new-quarantine":
            if new_marker not in {"active", "private-removing", "tombstone"}:
                raise SyncError("new transaction ownership marker is missing or retired")
        elif new_marker != "active":
            raise SyncError("new transaction ownership marker is missing or retired")
    return RecoveryPositions(
        old=old_position,
        new=new_position,
        old_marker=old_marker,
        new_marker=new_marker,
    )


def _verify_exact_committed_tree(
    plan: AdapterPlan,
    *,
    journal_sha256: str,
    marker_state: str,
) -> None:
    _revalidate_parent(plan)
    _require_identity(
        plan.target,
        plan.staging_identity,
        label=f"{plan.adapter} committed adapter",
    )
    if marker_state != "absent":
        _require_role_marker(
            plan,
            plan.target,
            plan.staging_identity,
            role="new",
            journal_sha256=journal_sha256,
            state=marker_state,
        )
    actual_sha256 = _snapshot_payload_sha256(
        plan.target,
        plan.staging_identity,
        excluded_marker_name=(
            None
            if marker_state == "absent"
            else _marker_name_for_state(
                marker_state,
                role="new",
                journal_sha256=journal_sha256,
            )
        ),
    )
    if actual_sha256 != plan.prepared_tree_sha256:
        raise SyncError(
            f"{plan.adapter} committed adapter failed exact tree digest verification"
        )
    _require_identity(
        plan.target,
        plan.staging_identity,
        label=f"{plan.adapter} committed adapter",
    )


def _verify_journal_bound_tree(
    plan: AdapterPlan,
    root: Path,
    expected_root: ObjectIdentity,
    expected_sha256: str,
    *,
    role: str,
    journal_sha256: str,
    marker_state: str,
) -> None:
    _revalidate_parent(plan)
    _require_identity(root, expected_root, label=f"{plan.adapter} journal-bound tree")
    if marker_state != "absent":
        _require_role_marker(
            plan,
            root,
            expected_root,
            role=role,
            journal_sha256=journal_sha256,
            state=marker_state,
        )
    actual_sha256 = _snapshot_payload_sha256(
        root,
        expected_root,
        excluded_marker_name=(
            None
            if marker_state == "absent"
            else _marker_name_for_state(
                marker_state,
                role=role,
                journal_sha256=journal_sha256,
            )
        ),
    )
    if actual_sha256 != expected_sha256:
        raise SyncError(f"{plan.adapter} journal-bound {role} tree digest changed")
    _require_identity(root, expected_root, label=f"{plan.adapter} journal-bound tree")


def _verify_prepared_commit_candidate(
    plan: AdapterPlan,
    state: RecoveryPositions,
    *,
    journal_sha256: str,
) -> None:
    if state.new != "staging" or state.new_marker is None:
        raise SyncError("prepared adapter candidate is no longer staged")
    _verify_journal_bound_tree(
        plan,
        plan.staging,
        plan.staging_identity,
        plan.prepared_tree_sha256,
        role="new",
        journal_sha256=journal_sha256,
        marker_state=state.new_marker,
    )
    if plan.target_identity is not None:
        if state.old != "target" or state.old_marker is None:
            raise SyncError("prepared adapter original is no longer published")
        assert plan.original_tree_sha256 is not None
        _verify_journal_bound_tree(
            plan,
            plan.target,
            plan.target_identity,
            plan.original_tree_sha256,
            role="old",
            journal_sha256=journal_sha256,
            marker_state=state.old_marker,
        )


def _verify_prepared_commit_candidates(
    journal: TransactionJournal,
    positions: list[RecoveryPositions],
) -> None:
    """Prove every old and new payload before the coordinated first move."""
    for plan, state in zip(journal.plans, positions, strict=True):
        _verify_prepared_commit_candidate(
            plan,
            state,
            journal_sha256=journal.sha256,
        )


def _verify_committed_old_tree(
    plan: AdapterPlan,
    state: RecoveryPositions,
    *,
    journal_sha256: str,
) -> None:
    if state.old is None or state.old == "old-tombstone":
        return
    if plan.target_identity is None or plan.original_tree_sha256 is None:
        raise SyncError("committed synchronization has an unexpected old tree")
    if state.old == "previous":
        root = plan.previous
    elif state.old == "old-quarantine":
        root = _cleanup_quarantine(plan, "old")
    else:
        raise SyncError("committed synchronization has an invalid old tree position")
    if state.old_marker is None:
        raise SyncError("committed synchronization old tree lost its ownership marker")
    marker_state = (
        "absent" if state.old_marker == "tombstone" else state.old_marker
    )
    _verify_journal_bound_tree(
        plan,
        root,
        plan.target_identity,
        plan.original_tree_sha256,
        role="old",
        journal_sha256=journal_sha256,
        marker_state=marker_state,
    )


def _verify_committed_old_trees(
    journal: TransactionJournal,
    positions: list[RecoveryPositions],
) -> None:
    """Retain rollback bytes unless every still-present old tree matches the journal."""
    for plan, state in zip(journal.plans, positions, strict=True):
        _verify_committed_old_tree(
            plan,
            state,
            journal_sha256=journal.sha256,
        )


def _resume_prepared_marker_installation(journal: TransactionJournal) -> None:
    """Finish the journal-publication cutpoint before normal rollback begins."""
    marker_states: list[tuple[AdapterPlan, str, str | None]] = []
    for plan in journal.plans:
        _revalidate_parent(plan)
        if (
            object_identity(plan.target) != plan.target_identity
            or object_identity(plan.staging) != plan.staging_identity
            or object_identity(plan.previous) is not None
            or object_identity(_cleanup_quarantine(plan, "new")) is not None
            or object_identity(_cleanup_quarantine(plan, "old")) is not None
            or object_identity(_cleanup_tombstone(plan, "new")) is not None
            or object_identity(_cleanup_tombstone(plan, "old")) is not None
            or _cleanup_tombstone_identity(
                plan,
                role="new",
                journal_sha256=journal.sha256,
            )
            is not None
            or _cleanup_tombstone_identity(
                plan,
                role="old",
                journal_sha256=journal.sha256,
            )
            is not None
        ):
            return
        new_state = _role_marker_state(
            plan,
            plan.staging,
            plan.staging_identity,
            role="new",
            journal_sha256=journal.sha256,
        )
        old_state: str | None = None
        if plan.target_identity is not None:
            old_state = _role_marker_state(
                plan,
                plan.target,
                plan.target_identity,
                role="old",
                journal_sha256=journal.sha256,
            )
        if new_state not in {"active", "absent"} or old_state not in {
            "active",
            "absent",
            None,
        }:
            raise SyncError("prepared marker installation has an invalid lifecycle state")
        assert plan.original_tree_sha256 is not None or plan.target_identity is None
        _verify_journal_bound_tree(
            plan,
            plan.staging,
            plan.staging_identity,
            plan.prepared_tree_sha256,
            role="new",
            journal_sha256=journal.sha256,
            marker_state=new_state,
        )
        if plan.target_identity is not None:
            assert plan.original_tree_sha256 is not None
            assert old_state is not None
            _verify_journal_bound_tree(
                plan,
                plan.target,
                plan.target_identity,
                plan.original_tree_sha256,
                role="old",
                journal_sha256=journal.sha256,
                marker_state=old_state,
            )
        marker_states.append((plan, new_state, old_state))
    for plan, new_state, old_state in marker_states:
        if new_state == "absent":
            _create_role_marker(
                plan,
                plan.staging,
                plan.staging_identity,
                role="new",
                journal_sha256=journal.sha256,
            )
        if plan.target_identity is not None and old_state == "absent":
            _create_role_marker(
                plan,
                plan.target,
                plan.target_identity,
                role="old",
                journal_sha256=journal.sha256,
            )


def _terminal_clean_commit(journal: TransactionJournal) -> bool:
    """Recognize only the journal-only state left after durable receipt removal."""
    for plan in journal.plans:
        _revalidate_parent(plan)
        if (
            object_identity(plan.target) != plan.staging_identity
            or object_identity(plan.staging) is not None
            or object_identity(plan.previous) is not None
            or object_identity(_cleanup_quarantine(plan, "new")) is not None
            or object_identity(_cleanup_quarantine(plan, "old")) is not None
            or object_identity(_cleanup_tombstone(plan, "new")) is not None
            or object_identity(_cleanup_tombstone(plan, "old")) is not None
        ):
            return False
        marker_state = _role_marker_state(
            plan,
            plan.target,
            plan.staging_identity,
            role="new",
            journal_sha256=journal.sha256,
        )
        if marker_state != "absent":
            return False
        _verify_exact_committed_tree(
            plan,
            journal_sha256=journal.sha256,
            marker_state="absent",
        )
    return True


def _flush_recovery_parents(plans: list[AdapterPlan]) -> None:
    """Durably publish every adapter-parent mutation before state proof deletion."""
    flushed: set[Path] = set()
    for plan in plans:
        parent = plan.target.parent
        if parent in flushed:
            continue
        _flush_directory(parent)
        flushed.add(parent)


def _verify_prepared_rollback_candidates(
    journal: TransactionJournal,
    positions: list[RecoveryPositions],
) -> None:
    """Fail closed before rollback if any journal-bound tree bytes changed."""
    for plan, state in zip(journal.plans, positions, strict=True):
        if state.new in {"staging", "target"}:
            assert state.new_marker is not None
            _verify_journal_bound_tree(
                plan,
                plan.staging if state.new == "staging" else plan.target,
                plan.staging_identity,
                plan.prepared_tree_sha256,
                role="new",
                journal_sha256=journal.sha256,
                marker_state=state.new_marker,
            )
        elif state.new == "new-quarantine":
            tombstone_identity = _cleanup_tombstone_identity(
                plan,
                role="new",
                journal_sha256=journal.sha256,
            )
            if tombstone_identity is None:
                assert state.new_marker is not None
                _verify_journal_bound_tree(
                    plan,
                    _cleanup_quarantine(plan, "new"),
                    plan.staging_identity,
                    plan.prepared_tree_sha256,
                    role="new",
                    journal_sha256=journal.sha256,
                    marker_state=state.new_marker,
                )
        if state.old in {"target", "previous"}:
            assert plan.target_identity is not None
            assert plan.original_tree_sha256 is not None
            assert state.old_marker is not None
            _verify_journal_bound_tree(
                plan,
                plan.target if state.old == "target" else plan.previous,
                plan.target_identity,
                plan.original_tree_sha256,
                role="old",
                journal_sha256=journal.sha256,
                marker_state=state.old_marker,
            )


def _rollback_prepared(journal: TransactionJournal) -> None:
    positions = [
        _recovery_positions(
            plan,
            journal_sha256=journal.sha256,
            committed=False,
        )
        for plan in journal.plans
    ]
    _verify_prepared_rollback_candidates(journal, positions)
    for plan, state in reversed(list(zip(journal.plans, positions, strict=True))):
        if state.new is not None:
            source = plan.target if state.new == "target" else plan.staging
            _remove_owned_path(
                plan,
                source,
                plan.staging_identity,
                quarantine=_cleanup_quarantine(plan, "new"),
                role="new",
                journal_sha256=journal.sha256,
            )
        if state.old == "previous":
            assert plan.target_identity is not None
            _move_owned_path(
                plan,
                plan.previous,
                plan.target,
                plan.target_identity,
                role="old",
                journal_sha256=journal.sha256,
            )
        if plan.target_identity is not None:
            marker_state = _role_marker_state(
                plan,
                plan.target,
                plan.target_identity,
                role="old",
                journal_sha256=journal.sha256,
            )
            if marker_state == "active":
                _retire_role_marker(
                    plan,
                    plan.target,
                    plan.target_identity,
                    role="old",
                    journal_sha256=journal.sha256,
                )
            _remove_retired_role_marker(
                plan,
                plan.target,
                plan.target_identity,
                role="old",
                journal_sha256=journal.sha256,
            )
    for plan in journal.plans:
        expected_target = plan.target_identity
        if (
            object_identity(plan.target) != expected_target
            or object_identity(plan.staging) is not None
            or object_identity(plan.previous) is not None
            or object_identity(_cleanup_quarantine(plan, "new")) is not None
            or object_identity(_cleanup_quarantine(plan, "old")) is not None
            or object_identity(_cleanup_tombstone(plan, "new")) is not None
            or object_identity(_cleanup_tombstone(plan, "old")) is not None
        ):
            raise SyncError("prepared synchronization rollback could not be verified")
        if expected_target is not None:
            marker_state = _role_marker_state(
                plan,
                plan.target,
                expected_target,
                role="old",
                journal_sha256=journal.sha256,
            )
            if marker_state != "absent":
                raise SyncError("prepared synchronization left an ownership marker")
    _flush_recovery_parents(journal.plans)
    _remove_journal(journal.identity, journal.encoded)


def _finalize_committed(
    journal: TransactionJournal,
    receipt: CommitReceipt,
) -> None:
    positions = [
        _recovery_positions(
            plan,
            journal_sha256=journal.sha256,
            committed=True,
        )
        for plan in journal.plans
    ]
    for plan, state in zip(journal.plans, positions, strict=True):
        assert state.new_marker is not None
        _verify_exact_committed_tree(
            plan,
            journal_sha256=journal.sha256,
            marker_state=state.new_marker,
        )
    _verify_committed_old_trees(journal, positions)
    for plan in journal.plans:
        marker_state = _role_marker_state(
            plan,
            plan.target,
            plan.staging_identity,
            role="new",
            journal_sha256=journal.sha256,
        )
        if marker_state == "active":
            _retire_role_marker(
                plan,
                plan.target,
                plan.staging_identity,
                role="new",
                journal_sha256=journal.sha256,
            )
        _remove_retired_role_marker(
            plan,
            plan.target,
            plan.staging_identity,
            role="new",
            journal_sha256=journal.sha256,
        )
    for plan in journal.plans:
        _verify_exact_committed_tree(
            plan,
            journal_sha256=journal.sha256,
            marker_state="absent",
        )
    _flush_recovery_parents(journal.plans)
    for plan, state in reversed(list(zip(journal.plans, positions, strict=True))):
        if state.old is not None:
            assert plan.target_identity is not None
            _verify_committed_old_tree(
                plan,
                state,
                journal_sha256=journal.sha256,
            )
            _remove_owned_path(
                plan,
                plan.previous,
                plan.target_identity,
                quarantine=_cleanup_quarantine(plan, "old"),
                role="old",
                journal_sha256=journal.sha256,
            )
    for plan in journal.plans:
        _verify_exact_committed_tree(
            plan,
            journal_sha256=journal.sha256,
            marker_state="absent",
        )
    _flush_recovery_parents(journal.plans)
    _remove_receipt(receipt.identity, receipt.encoded)
    if not _terminal_clean_commit(journal):
        raise SyncError("committed synchronization cleanup could not be verified")
    _flush_recovery_parents(journal.plans)
    _remove_journal(journal.identity, journal.encoded)


def recover_pending_transaction() -> None:
    """Roll back journal-only work or finalize a receipt-proven commit."""
    journal_identity = object_identity(SYNC_JOURNAL_PATH)
    receipt_identity = object_identity(SYNC_RECEIPT_PATH)
    if journal_identity is None:
        if receipt_identity is not None:
            raise SyncError("synchronization commit receipt exists without its journal")
        return
    journal = _read_transaction_journal()
    if receipt_identity is not None:
        verified_receipt = _read_commit_receipt(journal)
        _finalize_committed(journal, verified_receipt)
        return
    _resume_prepared_marker_installation(journal)
    if _terminal_clean_commit(journal):
        _flush_recovery_parents(journal.plans)
        _remove_journal(journal.identity, journal.encoded)
        return
    _rollback_prepared(journal)


def commit_adapter_updates(
    plans: list[AdapterPlan],
    prepared_journal: ObjectIdentity,
) -> None:
    """Promote every verified tree under one recoverable transaction journal."""
    journal = _read_transaction_journal()
    if journal.identity != prepared_journal or _journal_bytes(plans) != journal.encoded:
        raise SyncError("prepared adapter journal changed before commit")
    journal = journal._replace(plans=plans)
    positions = [
        _recovery_positions(
            plan,
            journal_sha256=journal.sha256,
            committed=False,
        )
        for plan in plans
    ]
    for plan, state in zip(plans, positions, strict=True):
        expected_old = "target" if plan.target_identity is not None else None
        if state.old != expected_old or state.new != "staging":
            raise SyncError("prepared adapter transaction changed before commit")

    _verify_prepared_commit_candidates(journal, positions)
    for plan, state in zip(plans, positions, strict=True):
        _verify_prepared_commit_candidate(
            plan,
            state,
            journal_sha256=journal.sha256,
        )
        if plan.target_identity is not None:
            _move_owned_path(
                plan,
                plan.target,
                plan.previous,
                plan.target_identity,
                role="old",
                journal_sha256=journal.sha256,
            )
        _move_owned_path(
            plan,
            plan.staging,
            plan.target,
            plan.staging_identity,
            role="new",
            journal_sha256=journal.sha256,
        )
        _flush_directory(plan.target.parent)

    _verify_promoted_plans(plans, journal_sha256=journal.sha256)
    write_commit_receipt(
        plans,
        prepared_journal,
    )
    _finalize_committed(journal, _read_commit_receipt(journal))


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
        if (
            object_identity(SYNC_JOURNAL_PATH) is not None
            or object_identity(SYNC_RECEIPT_PATH) is not None
        ):
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
                if (
                    journal_written
                    or object_identity(SYNC_JOURNAL_PATH) is not None
                    or object_identity(SYNC_RECEIPT_PATH) is not None
                ):
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
        description=(
            "Build every map-project adapter from canonical core and "
            "adapter-owned host guidance."
        )
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
