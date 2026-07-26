#!/bin/bash -p
set -euo pipefail

installer_host="${ATLAS_INSTALL_ENTRYPOINT:-codex}"
case "$installer_host" in
  codex)
    command_name="install.sh"
    host_label="Codex"
    ;;
  claude-code)
    command_name="install-claude.sh"
    host_label="Claude Code"
    ;;
  *)
    builtin printf 'install.sh: unsupported internal installer host: %s\n' "$installer_host" >&2
    exit 2
    ;;
esac

usage() {
  if [[ "$installer_host" == "claude-code" ]]; then
    builtin printf '%s\n' \
      'Usage: install-claude.sh [--force]' \
      '' \
      'Install the Project Atlas skill for Claude Code.' \
      '' \
      '  --force     Replace an existing installation after moving it to a backup.' \
      '  -h, --help  Show this help text.'
    return
  fi
  builtin printf '%s\n' \
    'Usage: install.sh [--user-scope] [--force]' \
    '' \
    'Install the Project Atlas skill for Codex.' \
    '' \
    '  --user-scope  Install to $HOME/.agents/skills/map-project.' \
    '  --force       Replace an existing installation after moving it to a backup.' \
    '  -h, --help    Show this help text.'
}

user_scope=false
force=false
while (($#)); do
  case "$1" in
    --user-scope)
      user_scope=true
      ;;
    --force)
      force=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      builtin printf '%s: unknown argument: %s\n' "$command_name" "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "$installer_host" == "claude-code" && "$user_scope" == true ]]; then
  builtin printf 'install-claude.sh: --user-scope is only supported by the Codex installer\n' >&2
  exit 2
fi

physical_directory() (
  builtin unset CDPATH
  builtin cd -P -- "$1"
  builtin pwd -P
)

script_path="${BASH_SOURCE[0]}"
if [[ "$script_path" == */* ]]; then
  script_parent="${script_path%/*}"
else
  script_parent="."
fi
if ! script_dir="$(physical_directory "$script_parent")"; then
  builtin printf '%s: unable to resolve installer directory\n' "$command_name" >&2
  exit 1
fi
if ! repo_root="$(physical_directory "$script_dir/..")"; then
  builtin printf '%s: unable to resolve installer source root\n' "$command_name" >&2
  exit 1
fi
if [[ "$installer_host" == "claude-code" ]]; then
  adapter_root="$repo_root/adapters/claude-code"
else
  adapter_root="$repo_root/adapters/codex"
fi
source_skill="$adapter_root/skills/map-project"

for source_component in \
  "$repo_root/adapters" \
  "$adapter_root" \
  "$adapter_root/skills" \
  "$source_skill"; do
  if [[ -L "$source_component" ]]; then
    builtin printf '%s: packaged %s skill path contains a symlink\n' \
      "$command_name" "$host_label" >&2
    exit 1
  fi
done
if ! exec 9<"$source_skill"; then
  builtin printf '%s: unable to anchor packaged %s skill source\n' \
    "$command_name" "$host_label" >&2
  exit 1
fi
if [[ ! -f "$source_skill/SKILL.md" || -L "$source_skill/SKILL.md" ]]; then
  builtin printf '%s: packaged %s skill is missing; run scripts/sync_adapters.py first\n' \
    "$command_name" "$host_label" >&2
  exit 1
fi

if [[ "$installer_host" == "claude-code" ]]; then
  if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
    installation_root="$CLAUDE_CONFIG_DIR"
  else
    user_home="${HOME:?install-claude.sh: HOME is not set and CLAUDE_CONFIG_DIR is empty}"
    installation_root="$user_home/.claude"
  fi
elif [[ "$user_scope" == true ]]; then
  user_home="${HOME:?install.sh: HOME is not set for --user-scope}"
  installation_root="$user_home/.agents"
elif [[ -n "${CODEX_HOME:-}" ]]; then
  installation_root="$CODEX_HOME"
else
  user_home="${HOME:?install.sh: HOME is not set and CODEX_HOME is empty}"
  installation_root="$user_home/.codex"
fi

source_repository_roots=("$repo_root")
ancestor="$repo_root"
while [[ "$ancestor" != "/" ]]; do
  ancestor="${ancestor%/*}"
  if [[ -z "$ancestor" ]]; then
    ancestor="/"
  fi
  if [[ -e "$ancestor/.git" || -L "$ancestor/.git" ]]; then
    source_repository_roots+=("$ancestor")
  fi
done

is_source_repository_path() {
  local candidate="$1"
  local repository_root
  for repository_root in "${source_repository_roots[@]}"; do
    if [[
      "$repository_root" == "/" ||
      "$candidate" == "$repository_root" ||
      "$candidate" == "$repository_root/"*
    ]]; then
      return 0
    fi
  done
  return 1
}

if [[ -x /usr/bin/stat ]]; then
  trusted_stat=/usr/bin/stat
elif [[ -x /bin/stat ]]; then
  trusted_stat=/bin/stat
else
  builtin printf '%s: required trusted stat executable is unavailable\n' "$command_name" >&2
  exit 1
fi
if [[ -x /usr/bin/readlink ]]; then
  trusted_readlink=/usr/bin/readlink
elif [[ -x /bin/readlink ]]; then
  trusted_readlink=/bin/readlink
else
  builtin printf '%s: required trusted readlink executable is unavailable\n' "$command_name" >&2
  exit 1
fi

stat_owner_and_mode() {
  local target="$1"
  local output
  if output="$("$trusted_stat" -f '%u %Lp' -- "$target" 2>/dev/null)"; then
    :
  elif output="$("$trusted_stat" -c '%u %a' -- "$target" 2>/dev/null)"; then
    :
  else
    return 1
  fi
  builtin read -r stat_owner stat_mode stat_extra <<<"$output"
  [[
    -n "${stat_owner:-}" &&
    -n "${stat_mode:-}" &&
    -z "${stat_extra:-}" &&
    "$stat_owner" =~ ^[0-9]+$ &&
    "$stat_mode" =~ ^[0-7]+$
  ]]
}

resolve_executable_path() {
  local candidate="$1"
  local link_target
  local link_parent
  local link_name
  local hops=0
  while [[ -L "$candidate" ]]; do
    ((hops += 1))
    if ((hops > 32)); then
      return 1
    fi
    if ! link_target="$("$trusted_readlink" "$candidate")" || [[ -z "$link_target" ]]; then
      return 1
    fi
    if [[ "$link_target" == /* ]]; then
      candidate="$link_target"
    else
      link_parent="${candidate%/*}"
      [[ "$link_parent" == "$candidate" ]] && link_parent="."
      candidate="$link_parent/$link_target"
    fi
    link_name="${candidate##*/}"
    link_parent="${candidate%/*}"
    [[ "$link_parent" == "$candidate" ]] && link_parent="."
    if ! link_parent="$(physical_directory "$link_parent")"; then
      return 1
    fi
    candidate="$link_parent/$link_name"
  done
  link_name="${candidate##*/}"
  link_parent="${candidate%/*}"
  [[ "$link_parent" == "$candidate" ]] && link_parent="."
  if ! link_parent="$(physical_directory "$link_parent")"; then
    return 1
  fi
  builtin printf '%s/%s\n' "$link_parent" "$link_name"
}

if ! python_candidate="$(builtin type -P python3)" || [[ -z "$python_candidate" ]]; then
  builtin printf '%s: required external python3 executable is unavailable\n' "$command_name" >&2
  exit 1
fi
if ! python_executable="$(resolve_executable_path "$python_candidate")"; then
  builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
  exit 1
fi
if [[ ! -f "$python_executable" || ! -x "$python_executable" ]] || \
  is_source_repository_path "$python_candidate" || \
  is_source_repository_path "$python_executable" || \
  ! stat_owner_and_mode "$python_executable"; then
  builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
  exit 1
fi
python_mode_value=$((8#$stat_mode))
if ((python_mode_value & 0002)); then
  builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
  exit 1
fi
python_parent="${python_executable%/*}"
while :; do
  if ! stat_owner_and_mode "$python_parent"; then
    builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
    exit 1
  fi
  python_parent_mode=$((8#$stat_mode))
  if ((python_parent_mode & 0002)) && ! ((python_parent_mode & 01000)); then
    builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
    exit 1
  fi
  [[ "$python_parent" == "/" ]] && break
  python_parent="${python_parent%/*}"
  [[ -n "$python_parent" ]] || python_parent="/"
done

"$python_executable" - "$source_skill" "$installation_root" "$force" "$installer_host" "$command_name" 9 <<'PY'
from __future__ import annotations

import ctypes
from contextlib import contextmanager
import hashlib
import os
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path


if sys.version_info < (3, 10):
    raise SystemExit(f"{sys.argv[5]}: Python 3.10 or newer is required")


source_skill = os.path.abspath(sys.argv[1])
installation_root = os.path.abspath(sys.argv[2])
force = sys.argv[3] == "true"
installer_host = sys.argv[4]
command_name = sys.argv[5]
inherited_source_fd = int(sys.argv[6])
host_label = "Claude Code" if installer_host == "claude-code" else "Codex"
target_name = "map-project"
lock_name = ".map-project.install.lock"
MAX_PACKAGE_FILES = 2_048
MAX_PACKAGE_DIRECTORIES = 512
MAX_PACKAGE_DEPTH = 32
MAX_PACKAGE_FILE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_TOTAL_BYTES = 64 * 1024 * 1024
EXTERNAL_DIFF_TIMEOUT_SECONDS = 30.0
EXTERNAL_DIFF_KILL_WAIT_SECONDS = 1.0


class InstallFailure(RuntimeError):
    pass


def trusted_external_executable(name: str) -> str:
    """Resolve a host tool while rejecting executables supplied by a source repository."""

    candidate = shutil.which(name)
    if candidate is None:
        raise InstallFailure(f"required external {name} executable is unavailable")
    try:
        executable = Path(candidate).resolve(strict=True)
        package_root = Path(source_skill).parents[3].resolve(strict=True)
        metadata = executable.stat()
    except (IndexError, OSError):
        raise InstallFailure(f"external {name} executable is unsafe") from None
    repository_roots = {package_root}
    for ancestor in package_root.parents:
        try:
            (ancestor / ".git").lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise InstallFailure(f"external {name} executable is unsafe") from None
        repository_roots.add(ancestor)
    if (
        any(
            executable == repository_root or repository_root in executable.parents
            for repository_root in repository_roots
        )
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & stat.S_IWOTH
        or not os.access(executable, os.X_OK)
    ):
        raise InstallFailure(f"external {name} executable is unsafe")
    for directory in executable.parents:
        try:
            directory_mode = directory.stat().st_mode
        except OSError:
            raise InstallFailure(f"external {name} executable is unsafe") from None
        if directory_mode & stat.S_IWOTH and not directory_mode & stat.S_ISVTX:
            raise InstallFailure(f"external {name} executable is unsafe")
    return os.fspath(executable)


class InstallInterrupted(InstallFailure):
    def __init__(self, signum: int) -> None:
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


class PackageBudget:
    def __init__(self) -> None:
        self.files = 0
        self.directories = 0
        self.total_bytes = 0

    def add_directory(self, depth: int, label: str) -> None:
        if depth > MAX_PACKAGE_DEPTH:
            raise InstallFailure(f"{label} exceeds the directory-depth limit")
        self.directories += 1
        if self.directories > MAX_PACKAGE_DIRECTORIES:
            raise InstallFailure(f"{label} exceeds the directory-count limit")

    def add_file(self, size: int, label: str) -> None:
        if size > MAX_PACKAGE_FILE_BYTES:
            raise InstallFailure(f"{label} exceeds the per-file byte limit")
        self.files += 1
        if self.files > MAX_PACKAGE_FILES:
            raise InstallFailure(f"{label} exceeds the file-count limit")
        self.total_bytes += size
        if self.total_bytes > MAX_PACKAGE_TOTAL_BYTES:
            raise InstallFailure(f"{label} exceeds the aggregate byte limit")


active_external_diff_process: subprocess.Popen[bytes] | None = None
diff_executable = trusted_external_executable("diff")


def kill_external_diff_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except ProcessLookupError:
            pass


def interrupt(signum: int, _frame: object) -> None:
    process = active_external_diff_process
    if process is not None:
        kill_external_diff_process_group(process)
    raise InstallInterrupted(signum)


HANDLED_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
if not hasattr(signal, "pthread_sigmask"):
    raise SystemExit(f"{command_name}: this installer requires pthread signal masks")
for handled_signal in HANDLED_SIGNALS:
    signal.signal(handled_signal, interrupt)


@contextmanager
def defer_handled_signals() -> object:
    """Commit one rename and its transaction state before delivering a trapped signal."""

    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, HANDLED_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def wait_at_test_barrier(name: str) -> None:
    barrier = os.environ.get(f"ATLAS_TEST_{name}_BARRIER")
    if not barrier:
        return
    release = os.environ.get(f"ATLAS_TEST_{name}_RELEASE")
    if not release:
        raise InstallFailure(f"test barrier {name} has no release path")
    try:
        with Path(barrier).open("x", encoding="utf-8") as barrier_file:
            barrier_file.write(f"{os.getpid()}\n")
    except FileExistsError:
        return
    while not os.path.exists(release):
        time.sleep(0.01)


def reach_after_move_test_point(name: str) -> None:
    wait_at_test_barrier(name)
    if os.environ.get(f"ATLAS_TEST_{name}_FAIL") == "1":
        raise InstallFailure(f"injected failure at test point {name}")


if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
    raise SystemExit(f"{command_name}: this installer requires O_NOFOLLOW and O_DIRECTORY")

directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
follow_directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
regular_file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
rewrite_file_flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
destination_file_flags = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
)


def stable_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def signature_is_regular(signature: tuple[int, ...] | None) -> bool:
    return signature is not None and stat.S_ISREG(signature[2]) and signature[3] == 1


def display_source_entry(relative: tuple[str, ...]) -> str:
    return "/".join(relative) if relative else "."


def entry_identity(parent_fd: int, name: str) -> tuple[int, int] | None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return metadata.st_dev, metadata.st_ino


def fd_identity(fd: int) -> tuple[int, int]:
    metadata = os.fstat(fd)
    return metadata.st_dev, metadata.st_ino


def parent_is_mutable_by_current_user(parent_fd: int) -> bool:
    metadata = os.fstat(parent_fd)
    mode = metadata.st_mode
    effective_uid = os.geteuid()
    if effective_uid == 0:
        return True
    if metadata.st_uid == effective_uid and mode & stat.S_IWUSR:
        return True
    groups = set(os.getgroups()) | {os.getegid()}
    if metadata.st_gid in groups and mode & stat.S_IWGRP:
        return True
    return bool(mode & stat.S_IWOTH)


def open_child_directory(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)

    if stat.S_ISLNK(metadata.st_mode):
        if parent_is_mutable_by_current_user(parent_fd):
            raise InstallFailure(f"destination path contains a mutable symlink component: {name}")
        flags = follow_directory_flags
    elif stat.S_ISDIR(metadata.st_mode):
        flags = directory_flags
    else:
        raise InstallFailure(f"destination path component is not a directory: {name}")

    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise InstallFailure(f"unable to anchor destination path component: {name}: {exc}") from exc


def path_parts(path: str) -> list[str]:
    absolute = os.path.abspath(path)
    if absolute == os.path.sep:
        raise InstallFailure("installation root must not be the filesystem root")
    return [part for part in absolute.split(os.path.sep) if part]


def inspect_existing_components(path: str) -> None:
    current_fd = os.open(os.path.sep, follow_directory_flags)
    try:
        for part in path_parts(path):
            try:
                next_fd = open_child_directory(current_fd, part, create=False)
            except FileNotFoundError:
                return
            os.close(current_fd)
            current_fd = next_fd
    finally:
        os.close(current_fd)


def open_directory_path(path: str, *, create: bool) -> int:
    current_fd = os.open(os.path.sep, follow_directory_flags)
    try:
        for part in path_parts(path):
            next_fd = open_child_directory(current_fd, part, create=create)
            os.close(current_fd)
            current_fd = next_fd
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def require_source_path_identity(anchored_source_fd: int) -> None:
    try:
        current_source_fd = open_directory_path(source_skill, create=False)
    except (FileNotFoundError, OSError) as exc:
        raise InstallFailure(f"packaged {host_label} skill source path changed") from exc
    try:
        if fd_identity(current_source_fd) != fd_identity(anchored_source_fd):
            raise InstallFailure(f"packaged {host_label} skill source identity changed")
    finally:
        os.close(current_source_fd)


libc = ctypes.CDLL(None, use_errno=True)
if sys.platform == "darwin":
    rename_exclusive = libc.renameatx_np
    rename_exclusive_flag = 0x00000004  # RENAME_EXCL
elif sys.platform.startswith("linux"):
    try:
        rename_exclusive = libc.renameat2
    except AttributeError as exc:
        raise SystemExit(f"{command_name}: libc does not provide renameat2") from exc
    rename_exclusive_flag = 1  # RENAME_NOREPLACE
else:
    raise SystemExit(f"{command_name}: unsupported platform for atomic installation: {sys.platform}")
rename_exclusive.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_uint,
]
rename_exclusive.restype = ctypes.c_int


def rename_noreplace(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
    result = rename_exclusive(
        source_fd,
        os.fsencode(source),
        destination_fd,
        os.fsencode(destination),
        rename_exclusive_flag,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


def move_owned_noreplace(
    source_fd: int,
    source: str,
    destination_fd: int,
    destination: str,
    expected: tuple[int, int],
    *,
    label: str,
    barrier: str | None = None,
    after_move_test_point: str | None = None,
) -> None:
    """Move only the captured source object, restoring a raced replacement."""
    if entry_identity(source_fd, source) != expected:
        raise InstallFailure(f"{label} source changed before the atomic move")
    if barrier is not None:
        wait_at_test_barrier(barrier)
    rename_noreplace(source_fd, source, destination_fd, destination)
    moved = entry_identity(destination_fd, destination)
    if moved == expected:
        if after_move_test_point is not None:
            reach_after_move_test_point(after_move_test_point)
        return
    if moved is not None and entry_identity(source_fd, source) is None:
        try:
            rename_noreplace(destination_fd, destination, source_fd, source)
        except OSError as exc:
            raise InstallFailure(
                f"{label} moved a foreign replacement to {destination}; "
                f"it could not be restored to {source}"
            ) from exc
        if entry_identity(source_fd, source) != moved:
            raise InstallFailure(
                f"{label} restored replacement changed identity at {source}"
            )
    raise InstallFailure(f"{label} source changed during the atomic move")


def make_unique_directory(parent_fd: int, prefix: str) -> tuple[str, tuple[int, int]]:
    for _attempt in range(128):
        name = f"{prefix}{secrets.token_hex(12)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        identity = entry_identity(parent_fd, name)
        if identity is None:
            raise InstallFailure(f"created directory disappeared before it could be anchored: {name}")
        return name, identity
    raise InstallFailure(f"unable to reserve a unique directory under {prefix}")


def remove_owned_entry(parent_fd: int, name: str, owned: tuple[int, int]) -> bool:
    if entry_identity(parent_fd, name) != owned:
        return False
    wait_at_test_barrier("CLEANUP_BEFORE_QUARANTINE")
    for _attempt in range(128):
        quarantine = f".map-project.cleanup-{secrets.token_hex(12)}"
        try:
            rename_noreplace(parent_fd, name, parent_fd, quarantine)
        except FileExistsError:
            continue
        try:
            quarantine_fd = os.open(quarantine, directory_flags, dir_fd=parent_fd)
        except OSError:
            print(
                f"{command_name}: cleanup preserved an unverified entry at: "
                f"{os.path.join(installation_root, 'skills', quarantine)}",
                file=sys.stderr,
            )
            return False
        try:
            if fd_identity(quarantine_fd) != owned:
                moved_identity = fd_identity(quarantine_fd)
                try:
                    rename_noreplace(parent_fd, quarantine, parent_fd, name)
                except FileExistsError:
                    print(
                        f"{command_name}: cleanup preserved a foreign entry at: "
                        f"{os.path.join(installation_root, 'skills', quarantine)}",
                        file=sys.stderr,
                    )
                    return False
                except OSError:
                    print(
                        f"{command_name}: cleanup could not restore a foreign entry; "
                        f"it remains recoverable at: "
                        f"{os.path.join(installation_root, 'skills', quarantine)}",
                        file=sys.stderr,
                    )
                    return False
                if entry_identity(parent_fd, name) != moved_identity:
                    print(
                        f"{command_name}: restored cleanup entry changed identity at: "
                        f"{os.path.join(installation_root, 'skills', name)}",
                        file=sys.stderr,
                    )
                return False
            wait_at_test_barrier("CLEANUP_REMOVE")

            def preserve_open_root(function: object, path: str, error: tuple[object, object, object]) -> None:
                if function is os.rmdir and path == ".":
                    return
                exception = error[1]
                if isinstance(exception, BaseException):
                    raise exception
                raise InstallFailure("unable to remove owned cleanup contents")

            try:
                shutil.rmtree(".", dir_fd=quarantine_fd, onerror=preserve_open_root)
            except OSError:
                return False
            if entry_identity(parent_fd, quarantine) != owned:
                return False
            try:
                os.rmdir(quarantine, dir_fd=parent_fd)
            except OSError:
                return False
            return entry_identity(parent_fd, quarantine) is None
        finally:
            os.close(quarantine_fd)
    return False


def copy_regular_source_file(
    source_parent_fd: int,
    destination_parent_fd: int,
    name: str,
    relative: tuple[str, ...],
    signatures: dict[tuple[str, ...], tuple[int, ...]],
    budget: PackageBudget,
) -> None:
    try:
        path_metadata = os.stat(name, dir_fd=source_parent_fd, follow_symlinks=False)
        source_file_fd = os.open(name, regular_file_flags, dir_fd=source_parent_fd)
    except OSError as exc:
        raise InstallFailure(
            f"unable to anchor packaged source file: {display_source_entry(relative)}: {exc}"
        ) from exc
    destination_file_fd: int | None = None
    try:
        opened_metadata = os.fstat(source_file_fd)
        if not stat.S_ISREG(opened_metadata.st_mode) or opened_metadata.st_nlink != 1:
            raise InstallFailure(
                f"packaged source contains a non-isolated regular file: "
                f"{display_source_entry(relative)}"
            )
        if stable_metadata(path_metadata) != stable_metadata(opened_metadata):
            raise InstallFailure(
                f"packaged source entry changed while being anchored: "
                f"{display_source_entry(relative)}"
            )
        budget.add_file(
            opened_metadata.st_size,
            f"packaged source {display_source_entry(relative)}",
        )
        try:
            destination_file_fd = os.open(
                name,
                destination_file_flags,
                0o600,
                dir_fd=destination_parent_fd,
            )
        except OSError as exc:
            raise InstallFailure(
                f"unable to create staged source file: {display_source_entry(relative)}: {exc}"
            ) from exc

        copied_bytes = 0
        while True:
            chunk = os.read(source_file_fd, 1024 * 1024)
            if not chunk:
                break
            copied_bytes += len(chunk)
            if copied_bytes > MAX_PACKAGE_FILE_BYTES:
                raise InstallFailure(
                    f"packaged source {display_source_entry(relative)} exceeds the per-file byte limit"
                )
            view = memoryview(chunk)
            while view:
                written = os.write(destination_file_fd, view)
                if written <= 0:
                    raise InstallFailure(
                        f"unable to write staged source file: {display_source_entry(relative)}"
                    )
                view = view[written:]

        final_metadata = os.fstat(source_file_fd)
        if stable_metadata(opened_metadata) != stable_metadata(final_metadata):
            raise InstallFailure(
                f"packaged source file changed while being copied: "
                f"{display_source_entry(relative)}"
            )
        os.fchmod(destination_file_fd, stat.S_IMODE(opened_metadata.st_mode))
        signatures[relative] = stable_metadata(final_metadata)
    finally:
        if destination_file_fd is not None:
            os.close(destination_file_fd)
        os.close(source_file_fd)


def copy_source_directory(
    source_directory_fd: int,
    destination_directory_fd: int,
    relative: tuple[str, ...],
    signatures: dict[tuple[str, ...], tuple[int, ...]],
    children: dict[tuple[str, ...], tuple[str, ...]],
    budget: PackageBudget,
    depth: int = 0,
) -> None:
    initial_metadata = os.fstat(source_directory_fd)
    if not stat.S_ISDIR(initial_metadata.st_mode):
        raise InstallFailure(
            f"packaged source contains a non-directory: {display_source_entry(relative)}"
        )
    budget.add_directory(depth, f"packaged source {display_source_entry(relative)}")
    try:
        names = tuple(sorted(os.listdir(source_directory_fd)))
    except OSError as exc:
        raise InstallFailure(
            f"unable to enumerate packaged source: {display_source_entry(relative)}: {exc}"
        ) from exc

    for name in names:
        child_relative = (*relative, name)
        try:
            path_metadata = os.stat(name, dir_fd=source_directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise InstallFailure(
                f"unable to inspect packaged source entry: "
                f"{display_source_entry(child_relative)}: {exc}"
            ) from exc

        if stat.S_ISREG(path_metadata.st_mode):
            copy_regular_source_file(
                source_directory_fd,
                destination_directory_fd,
                name,
                child_relative,
                signatures,
                budget,
            )
            continue
        if not stat.S_ISDIR(path_metadata.st_mode):
            raise InstallFailure(
                f"packaged source contains a symlink or special filesystem node: "
                f"{display_source_entry(child_relative)}"
            )

        try:
            source_child_fd = os.open(name, directory_flags, dir_fd=source_directory_fd)
        except OSError as exc:
            raise InstallFailure(
                f"unable to anchor packaged source directory: "
                f"{display_source_entry(child_relative)}: {exc}"
            ) from exc
        destination_child_fd: int | None = None
        try:
            opened_metadata = os.fstat(source_child_fd)
            if stable_metadata(path_metadata) != stable_metadata(opened_metadata):
                raise InstallFailure(
                    f"packaged source directory changed while being anchored: "
                    f"{display_source_entry(child_relative)}"
                )
            os.mkdir(name, 0o700, dir_fd=destination_directory_fd)
            destination_child_fd = os.open(
                name,
                directory_flags,
                dir_fd=destination_directory_fd,
            )
            copy_source_directory(
                source_child_fd,
                destination_child_fd,
                child_relative,
                signatures,
                children,
                budget,
                depth + 1,
            )
            os.fchmod(destination_child_fd, stat.S_IMODE(opened_metadata.st_mode))
        finally:
            if destination_child_fd is not None:
                os.close(destination_child_fd)
            os.close(source_child_fd)

    final_metadata = os.fstat(source_directory_fd)
    try:
        final_names = tuple(sorted(os.listdir(source_directory_fd)))
    except OSError as exc:
        raise InstallFailure(
            f"unable to re-enumerate packaged source: {display_source_entry(relative)}: {exc}"
        ) from exc
    if (
        stable_metadata(initial_metadata) != stable_metadata(final_metadata)
        or names != final_names
    ):
        raise InstallFailure(
            f"packaged source directory changed while being copied: "
            f"{display_source_entry(relative)}"
        )
    signatures[relative] = stable_metadata(final_metadata)
    children[relative] = names


def verify_source_directory(
    source_directory_fd: int,
    relative: tuple[str, ...],
    signatures: dict[tuple[str, ...], tuple[int, ...]],
    children: dict[tuple[str, ...], tuple[str, ...]],
) -> None:
    expected_signature = signatures.get(relative)
    expected_names = children.get(relative)
    if expected_signature is None or expected_names is None:
        raise InstallFailure(
            f"packaged source snapshot is incomplete: {display_source_entry(relative)}"
        )
    if stable_metadata(os.fstat(source_directory_fd)) != expected_signature:
        raise InstallFailure(
            f"packaged source directory changed after copying: {display_source_entry(relative)}"
        )
    try:
        names = tuple(sorted(os.listdir(source_directory_fd)))
    except OSError as exc:
        raise InstallFailure(
            f"unable to verify packaged source: {display_source_entry(relative)}: {exc}"
        ) from exc
    if names != expected_names:
        raise InstallFailure(
            f"packaged source contents changed after copying: {display_source_entry(relative)}"
        )

    for name in names:
        child_relative = (*relative, name)
        expected_child_signature = signatures.get(child_relative)
        if expected_child_signature is None:
            raise InstallFailure(
                f"packaged source snapshot is missing an entry: "
                f"{display_source_entry(child_relative)}"
            )
        try:
            path_metadata = os.stat(name, dir_fd=source_directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise InstallFailure(
                f"unable to re-inspect packaged source entry: "
                f"{display_source_entry(child_relative)}: {exc}"
            ) from exc
        if stable_metadata(path_metadata) != expected_child_signature:
            raise InstallFailure(
                f"packaged source entry changed after copying: "
                f"{display_source_entry(child_relative)}"
            )

        if stat.S_ISDIR(path_metadata.st_mode):
            try:
                child_fd = os.open(name, directory_flags, dir_fd=source_directory_fd)
            except OSError as exc:
                raise InstallFailure(
                    f"unable to re-anchor packaged source directory: "
                    f"{display_source_entry(child_relative)}: {exc}"
                ) from exc
            try:
                if stable_metadata(os.fstat(child_fd)) != expected_child_signature:
                    raise InstallFailure(
                        f"packaged source directory identity changed after copying: "
                        f"{display_source_entry(child_relative)}"
                    )
                verify_source_directory(child_fd, child_relative, signatures, children)
            finally:
                os.close(child_fd)
            continue
        if not stat.S_ISREG(path_metadata.st_mode):
            raise InstallFailure(
                f"packaged source entry became a symlink or special filesystem node: "
                f"{display_source_entry(child_relative)}"
            )
        if path_metadata.st_nlink != 1:
            raise InstallFailure(
                f"packaged source entry became a hardlinked file: "
                f"{display_source_entry(child_relative)}"
            )
        try:
            child_fd = os.open(name, regular_file_flags, dir_fd=source_directory_fd)
        except OSError as exc:
            raise InstallFailure(
                f"unable to re-anchor packaged source file: "
                f"{display_source_entry(child_relative)}: {exc}"
            ) from exc
        try:
            if stable_metadata(os.fstat(child_fd)) != expected_child_signature:
                raise InstallFailure(
                    f"packaged source file identity changed after copying: "
                    f"{display_source_entry(child_relative)}"
                )
        finally:
            os.close(child_fd)

    if stable_metadata(os.fstat(source_directory_fd)) != expected_signature:
        raise InstallFailure(
            f"packaged source directory changed during verification: "
            f"{display_source_entry(relative)}"
        )


def validate_regular_tree(path: str, label: str) -> None:
    if not os.path.isdir(path) or os.path.islink(path):
        raise InstallFailure(f"{label} is missing or is not a regular directory")
    for root, directories, files in os.walk(path, followlinks=False):
        for name in [*directories, *files]:
            candidate = os.path.join(root, name)
            metadata = os.lstat(candidate)
            if stat.S_ISLNK(metadata.st_mode):
                raise InstallFailure(f"{label} contains a symlink")
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise InstallFailure(f"{label} contains a special filesystem node")
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                raise InstallFailure(f"{label} contains a hardlinked file")


def descriptor_tree_manifest(
    root_fd: int,
    label: str,
) -> dict[tuple[str, ...], tuple[str, int, int, str]]:
    """Hash one descriptor-anchored regular tree under strict package ceilings."""

    manifest: dict[tuple[str, ...], tuple[str, int, int, str]] = {}
    budget = PackageBudget()

    def walk(directory_fd: int, relative: tuple[str, ...], depth: int) -> None:
        initial = os.fstat(directory_fd)
        if not stat.S_ISDIR(initial.st_mode):
            raise InstallFailure(f"{label} contains a non-directory")
        budget.add_directory(depth, label)
        try:
            names = tuple(sorted(os.listdir(directory_fd)))
        except OSError as exc:
            raise InstallFailure(f"unable to enumerate {label}") from exc
        manifest[relative] = ("directory", stat.S_IMODE(initial.st_mode), 0, "")
        for name in names:
            child_relative = (*relative, name)
            try:
                path_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise InstallFailure(f"unable to inspect {label}") from exc
            if stat.S_ISDIR(path_metadata.st_mode):
                try:
                    child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise InstallFailure(f"unable to anchor {label} directory") from exc
                try:
                    if stable_metadata(os.fstat(child_fd)) != stable_metadata(path_metadata):
                        raise InstallFailure(f"{label} directory changed while being anchored")
                    walk(child_fd, child_relative, depth + 1)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(path_metadata.st_mode) or path_metadata.st_nlink != 1:
                raise InstallFailure(f"{label} contains a symlink, hardlink, or special node")
            budget.add_file(path_metadata.st_size, label)
            try:
                child_fd = os.open(name, regular_file_flags, dir_fd=directory_fd)
            except OSError as exc:
                raise InstallFailure(f"unable to anchor {label} file") from exc
            try:
                opened = os.fstat(child_fd)
                if stable_metadata(opened) != stable_metadata(path_metadata):
                    raise InstallFailure(f"{label} file changed while being anchored")
                digest = hashlib.sha256()
                observed_bytes = 0
                while True:
                    chunk = os.read(child_fd, 1024 * 1024)
                    if not chunk:
                        break
                    observed_bytes += len(chunk)
                    if observed_bytes > MAX_PACKAGE_FILE_BYTES:
                        raise InstallFailure(f"{label} file exceeds the per-file byte limit")
                    digest.update(chunk)
                if stable_metadata(os.fstat(child_fd)) != stable_metadata(opened):
                    raise InstallFailure(f"{label} file changed while being verified")
                manifest[child_relative] = (
                    "file",
                    stat.S_IMODE(opened.st_mode),
                    observed_bytes,
                    digest.hexdigest(),
                )
            finally:
                os.close(child_fd)
        try:
            final_names = tuple(sorted(os.listdir(directory_fd)))
        except OSError as exc:
            raise InstallFailure(f"unable to re-enumerate {label}") from exc
        if names != final_names or stable_metadata(os.fstat(directory_fd)) != stable_metadata(initial):
            raise InstallFailure(f"{label} changed during descriptor verification")

    walk(root_fd, (), 0)
    return manifest


def rewrite_codex_metadata(stage_fd: int) -> None:
    try:
        skill_fd = os.open("map-project", directory_flags, dir_fd=stage_fd)
    except OSError as exc:
        raise InstallFailure("packaged Codex metadata is missing") from exc
    agents_fd: int | None = None
    metadata_fd: int | None = None
    try:
        agents_fd = os.open("agents", directory_flags, dir_fd=skill_fd)
        path_metadata = os.stat("openai.yaml", dir_fd=agents_fd, follow_symlinks=False)
        metadata_fd = os.open("openai.yaml", rewrite_file_flags, dir_fd=agents_fd)
        opened_metadata = os.fstat(metadata_fd)
        if not stat.S_ISREG(opened_metadata.st_mode) or opened_metadata.st_nlink != 1:
            raise InstallFailure("packaged Codex metadata is not an isolated regular file")
        if stable_metadata(path_metadata) != stable_metadata(opened_metadata):
            raise InstallFailure("packaged Codex metadata changed while being anchored")

        wait_at_test_barrier("CODEX_METADATA_REWRITE")
        if entry_identity(agents_fd, "openai.yaml") != fd_identity(metadata_fd):
            raise InstallFailure("packaged Codex metadata identity changed before rewrite")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(metadata_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        if stable_metadata(os.fstat(metadata_fd)) != stable_metadata(opened_metadata):
            raise InstallFailure("packaged Codex metadata changed while being read")
        plugin_invocation = b"$project-atlas:map-project"
        if plugin_invocation not in content:
            raise InstallFailure("packaged Codex metadata does not contain the plugin invocation")
        replacement = content.replace(plugin_invocation, b"$map-project")

        os.lseek(metadata_fd, 0, os.SEEK_SET)
        os.ftruncate(metadata_fd, 0)
        view = memoryview(replacement)
        while view:
            written = os.write(metadata_fd, view)
            if written <= 0:
                raise InstallFailure("unable to rewrite packaged Codex metadata")
            view = view[written:]
        if entry_identity(agents_fd, "openai.yaml") != fd_identity(metadata_fd):
            raise InstallFailure("packaged Codex metadata identity changed during rewrite")
    except OSError as exc:
        raise InstallFailure("unable to rewrite packaged Codex metadata safely") from exc
    finally:
        if metadata_fd is not None:
            os.close(metadata_fd)
        if agents_fd is not None:
            os.close(agents_fd)
        os.close(skill_fd)


def external_diff_timeout_seconds() -> float:
    raw_timeout = os.environ.get("ATLAS_TEST_DIFF_TIMEOUT_SECONDS")
    if raw_timeout is None:
        return EXTERNAL_DIFF_TIMEOUT_SECONDS
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise InstallFailure("invalid internal external-diff timeout") from exc
    if not 0.0 < timeout <= EXTERNAL_DIFF_TIMEOUT_SECONDS:
        raise InstallFailure("invalid internal external-diff timeout")
    return timeout


def stop_external_diff(process: subprocess.Popen[bytes]) -> None:
    with defer_handled_signals():
        if process.poll() is None:
            kill_external_diff_process_group(process)
        try:
            process.wait(timeout=EXTERNAL_DIFF_KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def run_external_diff(
    first: str,
    second: str,
    *,
    pass_fds: tuple[int, ...] = (),
    failure_message: str,
) -> None:
    global active_external_diff_process

    timeout = external_diff_timeout_seconds()
    process: subprocess.Popen[bytes] | None = None
    try:
        with defer_handled_signals():
            process = subprocess.Popen(
                [diff_executable, "-qr", first, second],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=pass_fds,
                start_new_session=True,
            )
            active_external_diff_process = process
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        wait_at_test_barrier("DIFF_TIMEOUT_CLEANUP")
        raise InstallFailure("external diff verification timed out") from exc
    finally:
        if process is not None:
            with defer_handled_signals():
                if process.returncode is None:
                    stop_external_diff(process)
                if active_external_diff_process is process:
                    active_external_diff_process = None
    if returncode != 0:
        raise InstallFailure(failure_message)


def run_diff(first: str, second: str) -> None:
    run_external_diff(
        first,
        second,
        failure_message="installed files failed verification",
    )


def run_descriptor_diff(first_fd: int, second_fd: int) -> None:
    run_external_diff(
        f"/dev/fd/{first_fd}",
        f"/dev/fd/{second_fd}",
        pass_fds=(first_fd, second_fd),
        failure_message="installed files failed descriptor-anchored verification",
    )


source_fd: int | None = None
root_fd: int | None = None
skills_fd: int | None = None
backup_fd: int | None = None
stage_fd: int | None = None
lock_identity: tuple[int, int] | None = None
stage_identity: tuple[int, int] | None = None
promoted_identity: tuple[int, int] | None = None
backup_identity: tuple[int, int] | None = None
backup_name: str | None = None
stage_name: str | None = None
exit_code = 1

try:
    try:
        source_fd = os.dup(inherited_source_fd)
    except OSError as exc:
        raise InstallFailure(f"unable to retain packaged {host_label} skill source") from exc
    if not stat.S_ISDIR(os.fstat(source_fd).st_mode):
        raise InstallFailure(f"packaged {host_label} skill source is not a directory")
    require_source_path_identity(source_fd)
    wait_at_test_barrier("SOURCE_ANCHORED")

    inspect_existing_components(installation_root)
    root_fd = open_directory_path(installation_root, create=True)
    root_identity = fd_identity(root_fd)
    skills_fd = open_child_directory(root_fd, "skills", create=True)
    skills_identity = fd_identity(skills_fd)

    post_root_fd = open_directory_path(installation_root, create=False)
    try:
        if fd_identity(post_root_fd) != root_identity:
            raise InstallFailure("installation root identity changed during setup")
        post_skills_fd = open_child_directory(post_root_fd, "skills", create=False)
        try:
            if fd_identity(post_skills_fd) != skills_identity:
                raise InstallFailure("skills directory identity changed during setup")
        finally:
            os.close(post_skills_fd)
    finally:
        os.close(post_root_fd)

    try:
        os.mkdir(lock_name, 0o700, dir_fd=skills_fd)
    except FileExistsError as exc:
        raise InstallFailure(
            f"another installation is active or a stale lock exists: "
            f"{os.path.join(installation_root, 'skills', lock_name)}"
        ) from exc
    lock_identity = entry_identity(skills_fd, lock_name)
    if lock_identity is None:
        raise InstallFailure("installation lock disappeared before it could be anchored")

    if entry_identity(skills_fd, target_name) is not None and not force:
        raise InstallFailure(
            f"refusing to overwrite existing installation: "
            f"{os.path.join(installation_root, 'skills', target_name)}; "
            "re-run with --force to preserve it as a backup"
        )

    stage_name, stage_identity = make_unique_directory(skills_fd, ".map-project.install-")
    stage_fd = open_child_directory(skills_fd, stage_name, create=False)
    os.mkdir("map-project", 0o700, dir_fd=stage_fd)
    staged_source_fd = os.open("map-project", directory_flags, dir_fd=stage_fd)
    try:
        source_signatures: dict[tuple[str, ...], tuple[int, ...]] = {}
        source_children: dict[tuple[str, ...], tuple[str, ...]] = {}
        copy_budget = PackageBudget()
        copy_source_directory(
            source_fd,
            staged_source_fd,
            (),
            source_signatures,
            source_children,
            copy_budget,
        )
        os.fchmod(staged_source_fd, stat.S_IMODE(os.fstat(source_fd).st_mode))
        verify_source_directory(source_fd, (), source_signatures, source_children)
        if not signature_is_regular(source_signatures.get(("SKILL.md",))):
            raise InstallFailure(
                f"packaged {host_label} skill is missing; run scripts/sync_adapters.py first"
            )
    finally:
        os.close(staged_source_fd)
    require_source_path_identity(source_fd)

    os.fchdir(stage_fd)
    validate_regular_tree("map-project", f"staged {host_label} skill")
    if installer_host == "codex":
        rewrite_codex_metadata(stage_fd)
    shutil.copytree("map-project", "expected-map-project", symlinks=True)
    validate_regular_tree("expected-map-project", f"expected {host_label} skill snapshot")

    os.fchdir(skills_fd)
    run_diff(
        f"{stage_name}/expected-map-project",
        f"{stage_name}/map-project",
    )
    require_source_path_identity(source_fd)

    existing_target_identity = entry_identity(skills_fd, target_name)
    if existing_target_identity is not None:
        backups_root_fd = open_child_directory(root_fd, ".skill-backups", create=True)
        try:
            backup_fd = open_child_directory(backups_root_fd, "project-atlas", create=True)
        finally:
            os.close(backups_root_fd)
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        for _attempt in range(128):
            candidate = f"map-project-{timestamp}-{secrets.token_hex(6)}"
            backup_name = candidate
            backup_identity = existing_target_identity
            try:
                with defer_handled_signals():
                    move_owned_noreplace(
                        skills_fd,
                        target_name,
                        backup_fd,
                        candidate,
                        existing_target_identity,
                        label="existing installation backup",
                        after_move_test_point="AFTER_BACKUP_MOVE",
                    )
            except FileExistsError:
                backup_name = None
                backup_identity = None
                continue
            break
        if backup_name is None:
            raise InstallFailure("unable to reserve a unique backup destination")

    os.fchdir(skills_fd)
    run_diff(
        f"{stage_name}/expected-map-project",
        f"{stage_name}/map-project",
    )

    staged_skill_identity = entry_identity(stage_fd, "map-project")
    if staged_skill_identity is None:
        raise InstallFailure("staged installation disappeared before promotion")
    promoted_identity = staged_skill_identity
    try:
        with defer_handled_signals():
            move_owned_noreplace(
                stage_fd,
                "map-project",
                skills_fd,
                target_name,
                staged_skill_identity,
                label="staged installation promotion",
                after_move_test_point="AFTER_PROMOTION_MOVE",
            )
    except FileExistsError as exc:
        raise InstallFailure("target appeared during installation; refusing to replace it") from exc

    target_fd: int | None = None
    expected_fd: int | None = None
    try:
        target_fd = os.open(target_name, directory_flags, dir_fd=skills_fd)
        if fd_identity(target_fd) != promoted_identity:
            raise InstallFailure("installed target identity changed before verification")
        expected_fd = os.open("expected-map-project", directory_flags, dir_fd=stage_fd)
        expected_manifest = descriptor_tree_manifest(
            expected_fd, f"expected {host_label} skill snapshot"
        )
        installed_manifest = descriptor_tree_manifest(
            target_fd, f"installed {host_label} skill"
        )
        if installed_manifest != expected_manifest:
            raise InstallFailure("installed files failed descriptor manifest verification")
        run_descriptor_diff(expected_fd, target_fd)
        if descriptor_tree_manifest(target_fd, f"installed {host_label} skill") != expected_manifest:
            raise InstallFailure("installed files changed during descriptor verification")
        if entry_identity(skills_fd, target_name) != promoted_identity:
            raise InstallFailure("installed target identity changed during verification")
    finally:
        if expected_fd is not None:
            os.close(expected_fd)
        if target_fd is not None:
            os.close(target_fd)

    final_root_fd = open_directory_path(installation_root, create=False)
    try:
        if fd_identity(final_root_fd) != root_identity:
            raise InstallFailure("installation root path changed during the transaction")
        final_skills_fd = open_child_directory(final_root_fd, "skills", create=False)
        try:
            if fd_identity(final_skills_fd) != skills_identity:
                raise InstallFailure("skills path changed during the transaction")
        finally:
            os.close(final_skills_fd)
    finally:
        os.close(final_root_fd)

    if (
        backup_fd is not None
        and backup_name is not None
        and backup_identity is not None
        and entry_identity(backup_fd, backup_name) != backup_identity
    ):
        raise InstallFailure("backup identity changed during the transaction")
    exit_code = 0
except BaseException as exc:
    interrupted_signal = exc.signum if isinstance(exc, InstallInterrupted) else None
    if skills_fd is not None and promoted_identity is not None:
        current_target_identity = entry_identity(skills_fd, target_name)
        if current_target_identity == promoted_identity:
            if not remove_owned_entry(skills_fd, target_name, promoted_identity):
                print(
                    f"{command_name}: unable to remove the installer-owned target",
                    file=sys.stderr,
                )
        elif current_target_identity is not None:
            print(
                f"{command_name}: refusing to remove a target not owned by this installer run",
                file=sys.stderr,
            )
    if (
        backup_fd is not None
        and backup_name is not None
        and backup_identity is not None
        and skills_fd is not None
    ):
        current_backup_identity = entry_identity(backup_fd, backup_name)
        current_target_identity = entry_identity(skills_fd, target_name)
        if current_target_identity == backup_identity:
            backup_name = None
        elif current_backup_identity != backup_identity:
            print(
                f"{command_name}: refusing to restore a backup whose identity changed",
                file=sys.stderr,
            )
        else:
            try:
                move_owned_noreplace(
                    backup_fd,
                    backup_name,
                    skills_fd,
                    target_name,
                    backup_identity,
                    label="backup restore",
                    barrier="BACKUP_RESTORE",
                )
                backup_name = None
                print(
                    f"Interrupted installation restored the previous version at: "
                    f"{os.path.join(installation_root, 'skills', target_name)}",
                    file=sys.stderr,
                )
            except FileExistsError:
                print(
                    f"{command_name}: target is occupied; previous version remains backed up at: "
                    f"{os.path.join(installation_root, '.skill-backups', 'project-atlas', backup_name)}",
                    file=sys.stderr,
                )
            except (OSError, InstallFailure) as restore_error:
                print(f"{command_name}: automatic restore failed: {restore_error}", file=sys.stderr)
    print(f"{command_name}: {exc}", file=sys.stderr)
    exit_code = 128 + interrupted_signal if interrupted_signal is not None else 1
finally:
    if skills_fd is not None and stage_name is not None and stage_identity is not None:
        if not remove_owned_entry(skills_fd, stage_name, stage_identity):
            print(f"{command_name}: unable to remove owned staging directory", file=sys.stderr)
            exit_code = 1
    if skills_fd is not None and lock_identity is not None:
        if not remove_owned_entry(skills_fd, lock_name, lock_identity):
            print(f"{command_name}: unable to release the owned installation lock", file=sys.stderr)
            exit_code = 1
    for descriptor in (stage_fd, backup_fd, skills_fd, root_fd, source_fd):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

if exit_code == 0:
    if backup_name is not None:
        print(
            "Previous installation backed up to: "
            + os.path.join(installation_root, ".skill-backups", "project-atlas", backup_name)
        )
    print(
        f"Installed Project Atlas for {host_label} at: "
        f"{os.path.join(installation_root, 'skills', target_name)}"
    )
raise SystemExit(exit_code)
PY
