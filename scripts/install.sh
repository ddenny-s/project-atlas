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
  if output="$("$trusted_stat" -f '%u %g %p %l' -- "$target" 2>/dev/null)"; then
    :
  elif output="$("$trusted_stat" -c '%u %g %a %h' -- "$target" 2>/dev/null)"; then
    :
  else
    return 1
  fi
  builtin read -r stat_owner stat_group stat_mode stat_link_count stat_extra <<<"$output"
  [[
    -n "${stat_owner:-}" &&
    -n "${stat_group:-}" &&
    -n "${stat_mode:-}" &&
    -n "${stat_link_count:-}" &&
    -z "${stat_extra:-}" &&
    "$stat_owner" =~ ^[0-9]+$ &&
    "$stat_group" =~ ^[0-9]+$ &&
    "$stat_mode" =~ ^[0-7]+$ &&
    "$stat_link_count" =~ ^[1-9][0-9]*$
  ]]
}

is_trusted_executable_link_count() {
  local owner="$1"
  local link_count="$2"
  [[ "$owner" =~ ^[0-9]+$ ]] || return 1
  [[ "$link_count" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$owner" == "0" || "$link_count" == "1" ]]
}

is_process_group() {
  local candidate="$1"
  local process_group
  for process_group in "${GROUPS[@]}"; do
    if [[ "$candidate" == "$process_group" ]]; then
      return 0
    fi
  done
  return 1
}

is_trusted_homebrew_cellar_directory() {
  local candidate="$1"
  local owner="$2"
  local group="$3"
  local mode_value="$4"
  case "$candidate" in
    /opt/homebrew/Cellar | \
    /usr/local/Cellar | \
    /home/linuxbrew/.linuxbrew/Cellar)
      ;;
    *)
      return 1
      ;;
  esac
  [[ "$owner" == "0" || "$owner" == "$EUID" ]] &&
    is_process_group "$group" &&
    ((mode_value & 0020)) &&
    ! ((mode_value & 0002))
}

is_trusted_sticky_directory() {
  local owner="$1"
  local mode_value="$2"
  [[ "$owner" == "0" || "$owner" == "$EUID" ]] &&
    ((mode_value & 0002)) &&
    ((mode_value & 01000))
}

is_trusted_executable_ancestor() {
  local candidate="$1"
  local owner="$2"
  local group="$3"
  local mode_value="$4"
  if [[ "$owner" != "0" && "$owner" != "$EUID" ]]; then
    return 1
  fi
  if ! ((mode_value & 0022)); then
    return 0
  fi
  if is_trusted_sticky_directory "$owner" "$mode_value"; then
    return 0
  fi
  is_trusted_homebrew_cellar_directory \
    "$candidate" "$owner" "$group" "$mode_value"
}

stat_identity() {
  local target="$1"
  local output
  if output="$("$trusted_stat" -f '%d %i' -- "$target" 2>/dev/null)"; then
    :
  elif output="$("$trusted_stat" -c '%d %i' -- "$target" 2>/dev/null)"; then
    :
  else
    return 1
  fi
  builtin read -r stat_device stat_inode stat_extra <<<"$output"
  [[
    -n "${stat_device:-}" &&
    -n "${stat_inode:-}" &&
    -z "${stat_extra:-}" &&
    "$stat_device" =~ ^[0-9]+$ &&
    "$stat_inode" =~ ^[0-9]+$
  ]]
}

source_repository_identities=()
for repository_root in "${source_repository_roots[@]}"; do
  if ! stat_identity "$repository_root"; then
    builtin printf '%s: unable to identify installer source repository\n' "$command_name" >&2
    exit 1
  fi
  source_repository_identities+=("$stat_device:$stat_inode")
done

normalize_absolute_path() {
  local candidate="$1"
  local component
  local component_count
  local remaining
  local resolved_cwd
  local -a components=()
  if [[ "$candidate" != /* ]]; then
    if ! resolved_cwd="$(physical_directory ".")"; then
      return 1
    fi
    candidate="$resolved_cwd/$candidate"
  fi
  remaining="${candidate#/}"
  while [[ -n "$remaining" ]]; do
    if [[ "$remaining" == */* ]]; then
      component="${remaining%%/*}"
      remaining="${remaining#*/}"
    else
      component="$remaining"
      remaining=""
    fi
    case "$component" in
      "" | .)
        ;;
      ..)
        component_count=${#components[@]}
        if ((component_count)); then
          unset "components[$((component_count - 1))]"
        fi
        ;;
      *)
        components[${#components[@]}]="$component"
        ;;
    esac
  done
  normalized_path=""
  for component in "${components[@]}"; do
    normalized_path+="/$component"
  done
  [[ -n "$normalized_path" ]] || normalized_path="/"
}

path_has_source_repository_ancestor() {
  local current="$1"
  local current_identity
  local repository_identity
  while :; do
    if ! stat_identity "$current"; then
      return 2
    fi
    current_identity="$stat_device:$stat_inode"
    for repository_identity in "${source_repository_identities[@]}"; do
      if [[ "$current_identity" == "$repository_identity" ]]; then
        return 0
      fi
    done
    [[ "$current" == "/" ]] && break
    current="${current%/*}"
    [[ -n "$current" ]] || current="/"
  done
  return 1
}

is_source_repository_path() {
  if ! normalize_absolute_path "$1"; then
    return 2
  fi
  path_has_source_repository_ancestor "$normalized_path"
}

rewrite_first_symlink() {
  local candidate="$1"
  local component
  local link_parent
  local link_target
  local prefix=""
  local remaining="${candidate#/}"
  local replacement
  while [[ -n "$remaining" ]]; do
    if [[ "$remaining" == */* ]]; then
      component="${remaining%%/*}"
      remaining="${remaining#*/}"
    else
      component="$remaining"
      remaining=""
    fi
    prefix="$prefix/$component"
    if [[ ! -L "$prefix" ]]; then
      continue
    fi
    if ! link_target="$("$trusted_readlink" "$prefix")" || [[ -z "$link_target" ]]; then
      return 2
    fi
    if [[ "$link_target" == /* ]]; then
      replacement="$link_target"
    else
      link_parent="${prefix%/*}"
      [[ -n "$link_parent" ]] || link_parent="/"
      replacement="$link_parent/$link_target"
    fi
    if [[ -n "$remaining" ]]; then
      replacement="$replacement/$remaining"
    fi
    if ! normalize_absolute_path "$replacement"; then
      return 2
    fi
    rewritten_path="$normalized_path"
    return 0
  done
  return 1
}

resolve_executable_path() {
  local candidate="$1"
  local rewrite_status
  local source_status
  local hops=0
  if ! normalize_absolute_path "$candidate"; then
    return 1
  fi
  candidate="$normalized_path"
  while :; do
    if path_has_source_repository_ancestor "$candidate"; then
      source_status=0
    else
      source_status=$?
    fi
    ((source_status == 1)) || return 1
    if rewrite_first_symlink "$candidate"; then
      rewrite_status=0
    else
      rewrite_status=$?
    fi
    if ((rewrite_status == 1)); then
      builtin printf '%s\n' "$candidate"
      return 0
    fi
    ((rewrite_status == 0)) || return 1
    ((hops += 1))
    if ((hops > 32)); then
      return 1
    fi
    candidate="$rewritten_path"
  done
}

if ! python_candidate="$(builtin type -P python3)" || [[ -z "$python_candidate" ]]; then
  builtin printf '%s: required external python3 executable is unavailable\n' "$command_name" >&2
  exit 1
fi
if ! python_executable="$(resolve_executable_path "$python_candidate")"; then
  builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
  exit 1
fi
if is_source_repository_path "$python_candidate"; then
  python_candidate_source_status=0
else
  python_candidate_source_status=$?
fi
if is_source_repository_path "$python_executable"; then
  python_executable_source_status=0
else
  python_executable_source_status=$?
fi
if [[ ! -f "$python_executable" || ! -x "$python_executable" ]] || \
  ((python_candidate_source_status != 1)) || \
  ((python_executable_source_status != 1)) || \
  ! stat_owner_and_mode "$python_executable"; then
  builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
  exit 1
fi
python_mode_value=$((8#$stat_mode))
if [[ "$stat_owner" != "0" && "$stat_owner" != "$EUID" ]] || \
  ((python_mode_value & 0022)); then
  builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
  exit 1
fi
if ! is_trusted_executable_link_count "$stat_owner" "$stat_link_count"; then
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
  if ! is_trusted_executable_ancestor \
    "$python_parent" "$stat_owner" "$stat_group" "$python_parent_mode"; then
    builtin printf '%s: external python3 executable is unsafe\n' "$command_name" >&2
    exit 1
  fi
  [[ "$python_parent" == "/" ]] && break
  python_parent="${python_parent%/*}"
  [[ -n "$python_parent" ]] || python_parent="/"
done

"$python_executable" -I - "$source_skill" "$installation_root" "$force" "$installer_host" "$command_name" 9 <<'PY'
from __future__ import annotations

import ctypes
from contextlib import contextmanager
import hashlib
import json
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
INSTALL_MARKER_NAME = ".project-atlas-install-owner.json"
MAX_PACKAGE_FILES = 2_048
MAX_PACKAGE_DIRECTORIES = 512
MAX_PACKAGE_DEPTH = 32
MAX_PACKAGE_FILE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_TOTAL_BYTES = 64 * 1024 * 1024
EXTERNAL_DIFF_TIMEOUT_SECONDS = 30.0
EXTERNAL_DIFF_KILL_WAIT_SECONDS = 1.0


class InstallFailure(RuntimeError):
    pass


def stat_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


def has_ancestor_identity(
    path: Path,
    expected_identities: set[tuple[int, int]],
) -> bool:
    return any(
        stat_identity(ancestor) in expected_identities
        for ancestor in (path, *path.parents)
    )


def is_trusted_homebrew_cellar_directory(
    directory: Path,
    metadata: os.stat_result,
    trusted_owners: set[int],
    process_groups: set[int],
) -> bool:
    return (
        directory
        in {
            Path("/opt/homebrew/Cellar"),
            Path("/usr/local/Cellar"),
            Path("/home/linuxbrew/.linuxbrew/Cellar"),
        }
        and getattr(metadata, "st_uid", None) in trusted_owners
        and getattr(metadata, "st_gid", None) in process_groups
        and bool(metadata.st_mode & stat.S_IWGRP)
        and not metadata.st_mode & stat.S_IWOTH
    )


def is_trusted_sticky_directory(
    metadata: os.stat_result,
    trusted_owners: set[int],
) -> bool:
    return (
        getattr(metadata, "st_uid", None) in trusted_owners
        and bool(metadata.st_mode & stat.S_IWOTH)
        and bool(metadata.st_mode & stat.S_ISVTX)
    )


def is_trusted_executable_ancestor(
    directory: Path,
    metadata: os.stat_result,
    trusted_owners: set[int],
    process_groups: set[int],
) -> bool:
    if getattr(metadata, "st_uid", None) not in trusted_owners:
        return False
    if not metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return True
    return (
        is_trusted_sticky_directory(metadata, trusted_owners)
        or is_trusted_homebrew_cellar_directory(
            directory,
            metadata,
            trusted_owners,
            process_groups,
        )
    )


def is_trusted_executable_link_count(metadata: os.stat_result) -> bool:
    owner = getattr(metadata, "st_uid", None)
    link_count = getattr(metadata, "st_nlink", None)
    return (
        type(owner) is int
        and type(link_count) is int
        and link_count > 0
        and (owner == 0 or link_count == 1)
    )


def host_executable_name_matches(
    name: str,
    executable: Path,
    *,
    platform_name: str = os.name,
) -> bool:
    if platform_name == "nt":
        expected = name.casefold()
        actual = executable.name.casefold()
        return actual in {expected, f"{expected}.exe"}
    return executable.name == name


def executable_path_is_link(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_tag = getattr(metadata, "st_reparse_tag", None)
    if reparse_tag is not None and not isinstance(reparse_tag, int):
        raise OSError("invalid executable reparse metadata")
    supported_reparse_tags = {
        tag
        for tag in (
            getattr(stat, "IO_REPARSE_TAG_SYMLINK", None),
            getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None),
        )
        if isinstance(tag, int)
    }
    if reparse_tag in supported_reparse_tags:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if not isinstance(file_attributes, int):
        raise OSError("invalid executable reparse metadata")
    if reparse_flag and file_attributes & reparse_flag:
        raise OSError("unsupported executable reparse point")
    return False


def executable_symlink_chain(
    candidate: Path,
    *,
    maximum_hops: int = 32,
) -> tuple[Path, ...]:
    current = Path(os.path.abspath(candidate))
    chain = [current]
    hops = 0
    while True:
        anchor = Path(current.anchor)
        prefix = anchor
        anchor_parts = len(anchor.parts)
        symlink_found = False
        for index, component in enumerate(
            current.parts[anchor_parts:],
            start=anchor_parts,
        ):
            prefix = prefix / component
            metadata = prefix.lstat()
            if not executable_path_is_link(metadata):
                continue
            if hops >= maximum_hops:
                raise OSError("external executable symlink chain is too deep")
            hops += 1
            target = Path(os.readlink(prefix))
            if not target.is_absolute():
                target = prefix.parent / target
            remainder = current.parts[index + 1 :]
            if remainder:
                target = target.joinpath(*remainder)
            current = Path(os.path.abspath(target))
            chain.extend((prefix, current))
            symlink_found = True
            break
        if not symlink_found:
            return tuple(chain)


def trusted_external_executable(name: str) -> str:
    """Resolve a host tool while rejecting executables supplied by a source repository."""

    candidate = shutil.which(name)
    if candidate is None:
        raise InstallFailure(f"required external {name} executable is unavailable")
    try:
        candidate_path = Path(os.path.abspath(candidate))
        candidate_chain = executable_symlink_chain(candidate_path)
        resolved_candidate_parents = tuple(
            path.parent.resolve(strict=True) for path in candidate_chain
        )
        executable = candidate_chain[-1].resolve(strict=True)
        package_root = Path(source_skill).parents[3].resolve(strict=True)
        metadata = executable.stat()
    except (IndexError, OSError, RuntimeError):
        raise InstallFailure(f"external {name} executable is unsafe") from None
    effective_uid_getter = getattr(os, "geteuid", None)
    trusted_owners = {0}
    if callable(effective_uid_getter):
        trusted_owners.add(effective_uid_getter())
    try:
        process_groups = set(os.getgroups())
    except OSError:
        raise InstallFailure(f"external {name} executable is unsafe") from None
    effective_gid_getter = getattr(os, "getegid", None)
    if callable(effective_gid_getter):
        process_groups.add(effective_gid_getter())
    repository_roots = {package_root}
    for ancestor in package_root.parents:
        try:
            (ancestor / ".git").lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise InstallFailure(f"external {name} executable is unsafe") from None
        repository_roots.add(ancestor)
    try:
        repository_identities = {stat_identity(root) for root in repository_roots}
        supplied_by_source_repository = any(
            has_ancestor_identity(path, repository_identities)
            for path in (
                *candidate_chain,
                *resolved_candidate_parents,
                executable,
            )
        )
    except OSError:
        raise InstallFailure(f"external {name} executable is unsafe") from None
    if (
        supplied_by_source_repository
        or not host_executable_name_matches(name, executable)
        or not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_uid", None) not in trusted_owners
        or not is_trusted_executable_link_count(metadata)
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(executable, os.X_OK)
    ):
        raise InstallFailure(f"external {name} executable is unsafe")
    for directory in executable.parents:
        try:
            directory_metadata = directory.stat()
        except OSError:
            raise InstallFailure(f"external {name} executable is unsafe") from None
        if not is_trusted_executable_ancestor(
            directory,
            directory_metadata,
            trusted_owners,
            process_groups,
        ):
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


def ownership_marker_payload(transaction_nonce: str, role: str) -> bytes:
    return (
        json.dumps(
            {
                "nonce": transaction_nonce,
                "role": role,
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def create_ownership_marker(
    directory_fd: int,
    payload: bytes,
) -> tuple[bytes, tuple[int, int]]:
    marker_fd: int | None = None
    try:
        marker_fd = os.open(
            INSTALL_MARKER_NAME,
            destination_file_flags,
            0o600,
            dir_fd=directory_fd,
        )
        opened = os.fstat(marker_fd)
        marker_identity = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise InstallFailure("installer ownership marker is not a unique private file")
        view = memoryview(payload)
        while view:
            written = os.write(marker_fd, view)
            if written <= 0:
                raise InstallFailure("installer ownership marker write made no progress")
            view = view[written:]
        os.fchmod(marker_fd, 0o600)
        final = os.fstat(marker_fd)
        if (
            (final.st_dev, final.st_ino) != marker_identity
            or final.st_nlink != 1
            or stat.S_IMODE(final.st_mode) != 0o600
            or final.st_size != len(payload)
        ):
            raise InstallFailure("installer ownership marker failed write verification")
    except OSError as exc:
        raise InstallFailure("unable to create installer ownership marker") from exc
    finally:
        if marker_fd is not None:
            os.close(marker_fd)
    proof = (payload, marker_identity)
    verify_ownership_marker(directory_fd, proof, "created installer directory")
    return proof


def open_verified_ownership_marker(
    directory_fd: int,
    proof: tuple[bytes, tuple[int, int]],
    label: str,
) -> int:
    payload, expected_identity = proof
    marker_fd: int | None = None
    try:
        path_metadata = os.stat(
            INSTALL_MARKER_NAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            (path_metadata.st_dev, path_metadata.st_ino) != expected_identity
            or not stat.S_ISREG(path_metadata.st_mode)
            or path_metadata.st_nlink != 1
            or stat.S_IMODE(path_metadata.st_mode) != 0o600
            or path_metadata.st_size != len(payload)
        ):
            raise InstallFailure(f"{label} ownership marker changed")
        marker_fd = os.open(
            INSTALL_MARKER_NAME,
            regular_file_flags,
            dir_fd=directory_fd,
        )
        opened = os.fstat(marker_fd)
        if (
            (opened.st_dev, opened.st_ino) != expected_identity
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_size != len(payload)
        ):
            raise InstallFailure(f"{label} ownership marker changed before it was read")
        chunks: list[bytes] = []
        remaining = len(payload)
        while remaining:
            chunk = os.read(marker_fd, remaining)
            if not chunk:
                raise InstallFailure(f"{label} ownership marker was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(marker_fd, 1):
            raise InstallFailure(f"{label} ownership marker grew while it was read")
        current = os.stat(
            INSTALL_MARKER_NAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            (current.st_dev, current.st_ino) != expected_identity
            or current.st_nlink != 1
        ):
            raise InstallFailure(f"{label} ownership marker changed while it was read")
        if b"".join(chunks) != payload:
            raise InstallFailure(f"{label} ownership marker nonce changed")
        result = marker_fd
        marker_fd = None
        return result
    except FileNotFoundError:
        raise InstallFailure(f"{label} ownership marker is missing") from None
    except OSError as exc:
        raise InstallFailure(f"{label} ownership marker could not be verified") from exc
    finally:
        if marker_fd is not None:
            os.close(marker_fd)


def verify_ownership_marker(
    directory_fd: int,
    proof: tuple[bytes, tuple[int, int]],
    label: str,
) -> None:
    marker_fd = open_verified_ownership_marker(directory_fd, proof, label)
    os.close(marker_fd)


def remove_ownership_marker(
    directory_fd: int,
    proof: tuple[bytes, tuple[int, int]],
    label: str,
) -> None:
    marker_fd = open_verified_ownership_marker(directory_fd, proof, label)
    try:
        os.unlink(INSTALL_MARKER_NAME, dir_fd=directory_fd)
        if entry_identity(directory_fd, INSTALL_MARKER_NAME) is not None:
            raise InstallFailure(f"{label} ownership marker removal was not stable")
    except OSError as exc:
        raise InstallFailure(f"{label} ownership marker could not be removed") from exc
    finally:
        os.close(marker_fd)


def verify_owned_directory(
    parent_fd: int,
    name: str,
    expected: tuple[int, int],
    *,
    label: str,
    marker_proof: tuple[bytes, tuple[int, int]] | None = None,
    payload_manifest: dict[tuple[str, ...], tuple[str, int, int, str]] | None = None,
) -> None:
    if (marker_proof is None) == (payload_manifest is None):
        raise InstallFailure(f"{label} has incomplete or ambiguous ownership proof")
    try:
        directory_fd = os.open(name, directory_flags, dir_fd=parent_fd)
    except OSError as exc:
        raise InstallFailure(f"{label} could not be opened for ownership verification") from exc
    try:
        if fd_identity(directory_fd) != expected:
            raise InstallFailure(f"{label} identity changed")
        if marker_proof is not None:
            verify_ownership_marker(directory_fd, marker_proof, label)
        else:
            assert payload_manifest is not None
            if descriptor_tree_manifest(directory_fd, label) != payload_manifest:
                raise InstallFailure(f"{label} payload digest changed")
        if fd_identity(directory_fd) != expected:
            raise InstallFailure(f"{label} identity changed during ownership verification")
        if entry_identity(parent_fd, name) != expected:
            raise InstallFailure(f"{label} path changed during ownership verification")
    finally:
        os.close(directory_fd)


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
    marker_proof: tuple[bytes, tuple[int, int]] | None = None,
    payload_manifest: dict[tuple[str, ...], tuple[str, int, int, str]] | None = None,
    barrier: str | None = None,
    after_move_test_point: str | None = None,
) -> None:
    """Move only the captured source object, restoring a raced replacement."""
    verify_owned_directory(
        source_fd,
        source,
        expected,
        label=f"{label} source",
        marker_proof=marker_proof,
        payload_manifest=payload_manifest,
    )
    if barrier is not None:
        wait_at_test_barrier(barrier)
    rename_noreplace(source_fd, source, destination_fd, destination)
    moved = entry_identity(destination_fd, destination)
    if moved == expected:
        try:
            verify_owned_directory(
                destination_fd,
                destination,
                expected,
                label=f"{label} destination",
                marker_proof=marker_proof,
                payload_manifest=payload_manifest,
            )
        except InstallFailure as proof_error:
            try:
                rename_noreplace(destination_fd, destination, source_fd, source)
            except OSError as restore_error:
                raise InstallFailure(
                    f"{label} ownership proof changed during the atomic move; "
                    f"the moved object remains preserved at {destination}"
                ) from restore_error
            if entry_identity(source_fd, source) != moved:
                raise InstallFailure(
                    f"{label} restored object changed identity at {source}"
                ) from proof_error
            raise InstallFailure(
                f"{label} ownership proof changed during the atomic move"
            ) from proof_error
        else:
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


def remove_owned_tree_entry(parent_fd: int, name: str) -> None:
    """Remove one descriptor-anchored regular tree without following links."""

    try:
        path_metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise InstallFailure(
            "owned cleanup tree entry could not be inspected"
        ) from exc

    if stat.S_ISREG(path_metadata.st_mode):
        if path_metadata.st_nlink != 1:
            raise InstallFailure(
                "owned cleanup tree contains a hardlinked file"
            )
        try:
            file_fd = os.open(name, regular_file_flags, dir_fd=parent_fd)
        except OSError as exc:
            raise InstallFailure(
                "owned cleanup tree file could not be anchored"
            ) from exc
        try:
            opened_metadata = os.fstat(file_fd)
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or opened_metadata.st_nlink != 1
                or stable_metadata(opened_metadata)
                != stable_metadata(path_metadata)
            ):
                raise InstallFailure(
                    "owned cleanup tree file changed while being anchored"
                )
            current_path_metadata = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            current_opened_metadata = os.fstat(file_fd)
            if (
                stable_metadata(current_path_metadata)
                != stable_metadata(current_opened_metadata)
                or stable_metadata(current_opened_metadata)
                != stable_metadata(opened_metadata)
            ):
                raise InstallFailure(
                    "owned cleanup tree file changed before removal"
                )
            os.unlink(name, dir_fd=parent_fd)
            if entry_identity(parent_fd, name) is not None:
                raise InstallFailure(
                    "owned cleanup tree file remained after removal"
                )
        except InstallFailure:
            raise
        except OSError as exc:
            raise InstallFailure(
                "owned cleanup tree file could not be removed safely"
            ) from exc
        finally:
            os.close(file_fd)
        return

    if not stat.S_ISDIR(path_metadata.st_mode):
        raise InstallFailure(
            "owned cleanup tree contains a symlink or special filesystem node"
        )

    expected_identity = (path_metadata.st_dev, path_metadata.st_ino)
    try:
        directory_fd = os.open(name, directory_flags, dir_fd=parent_fd)
    except OSError as exc:
        raise InstallFailure(
            "owned cleanup tree directory could not be anchored"
        ) from exc
    try:
        opened_metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(opened_metadata.st_mode)
            or stable_metadata(opened_metadata) != stable_metadata(path_metadata)
        ):
            raise InstallFailure(
                "owned cleanup tree directory changed while being anchored"
            )
        try:
            children = tuple(os.listdir(directory_fd))
        except OSError as exc:
            raise InstallFailure(
                "owned cleanup tree directory could not be enumerated"
            ) from exc
        for child in children:
            remove_owned_tree_entry(directory_fd, child)

        try:
            remaining = tuple(os.listdir(directory_fd))
            current_path_metadata = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            current_opened_metadata = os.fstat(directory_fd)
        except OSError as exc:
            raise InstallFailure(
                "owned cleanup tree directory could not be revalidated"
            ) from exc
        if (
            remaining
            or fd_identity(directory_fd) != expected_identity
            or (
                current_path_metadata.st_dev,
                current_path_metadata.st_ino,
            )
            != expected_identity
            or stable_metadata(current_path_metadata)
            != stable_metadata(current_opened_metadata)
        ):
            raise InstallFailure(
                "owned cleanup tree directory changed before removal"
            )
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError as exc:
            raise InstallFailure(
                "owned cleanup tree directory could not be removed safely"
            ) from exc
        if entry_identity(parent_fd, name) is not None:
            raise InstallFailure(
                "owned cleanup tree directory remained after removal"
            )
    finally:
        os.close(directory_fd)


def remove_owned_entry(
    parent_fd: int,
    name: str,
    owned: tuple[int, int],
    marker_proof: tuple[bytes, tuple[int, int]],
) -> bool:
    try:
        verify_owned_directory(
            parent_fd,
            name,
            owned,
            label=f"cleanup entry {name}",
            marker_proof=marker_proof,
        )
    except InstallFailure:
        return False
    wait_at_test_barrier("CLEANUP_BEFORE_QUARANTINE")
    try:
        verify_owned_directory(
            parent_fd,
            name,
            owned,
            label=f"cleanup entry {name}",
            marker_proof=marker_proof,
        )
    except InstallFailure:
        return False

    for _attempt in range(128):
        quarantine = f".map-project.cleanup-{secrets.token_hex(12)}"
        try:
            rename_noreplace(parent_fd, name, parent_fd, quarantine)
        except FileExistsError:
            continue

        def restore_verified_quarantine() -> bool:
            if entry_identity(parent_fd, quarantine) != owned:
                return False
            try:
                rename_noreplace(parent_fd, quarantine, parent_fd, name)
            except OSError:
                return False
            return entry_identity(parent_fd, name) == owned

        try:
            verify_owned_directory(
                parent_fd,
                quarantine,
                owned,
                label=f"quarantined cleanup entry {name}",
                marker_proof=marker_proof,
            )
        except InstallFailure:
            restore_verified_quarantine()
            return False

        wait_at_test_barrier("CLEANUP_REMOVE")
        try:
            verify_owned_directory(
                parent_fd,
                quarantine,
                owned,
                label=f"quarantined cleanup entry {name}",
                marker_proof=marker_proof,
            )
        except InstallFailure:
            restore_verified_quarantine()
            return False

        try:
            quarantine_fd = os.open(quarantine, directory_flags, dir_fd=parent_fd)
        except OSError:
            print(
                f"{command_name}: cleanup preserved an unverified entry at: "
                f"{os.path.join(installation_root, 'skills', quarantine)}",
                file=sys.stderr,
            )
            return False
        marker_fd: int | None = None
        try:
            if fd_identity(quarantine_fd) != owned:
                return False
            try:
                for child in tuple(os.listdir(quarantine_fd)):
                    if child == INSTALL_MARKER_NAME:
                        continue
                    remove_owned_tree_entry(quarantine_fd, child)
            except (OSError, InstallFailure):
                return False
            if (
                fd_identity(quarantine_fd) != owned
                or entry_identity(parent_fd, quarantine) != owned
            ):
                return False
            marker_fd = open_verified_ownership_marker(
                quarantine_fd,
                marker_proof,
                f"quarantined cleanup entry {name}",
            )
            if tuple(os.listdir(quarantine_fd)) != (INSTALL_MARKER_NAME,):
                return False
            try:
                with defer_handled_signals():
                    os.unlink(INSTALL_MARKER_NAME, dir_fd=quarantine_fd)
                    if entry_identity(quarantine_fd, INSTALL_MARKER_NAME) is not None:
                        return False
                    if (
                        fd_identity(quarantine_fd) != owned
                        or entry_identity(parent_fd, quarantine) != owned
                    ):
                        return False
                    os.rmdir(quarantine, dir_fd=parent_fd)
            except OSError:
                return False
            return entry_identity(parent_fd, quarantine) is None
        finally:
            if marker_fd is not None:
                os.close(marker_fd)
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
lock_marker_proof: tuple[bytes, tuple[int, int]] | None = None
stage_marker_proof: tuple[bytes, tuple[int, int]] | None = None
target_marker_proof: tuple[bytes, tuple[int, int]] | None = None
backup_payload_manifest: dict[tuple[str, ...], tuple[str, int, int, str]] | None = None
expected_manifest: dict[tuple[str, ...], tuple[str, int, int, str]] | None = None
backup_name: str | None = None
stage_name: str | None = None
transaction_nonce = secrets.token_hex(32)
promoted_marker_active = False
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
    lock_fd = os.open(lock_name, directory_flags, dir_fd=skills_fd)
    try:
        if fd_identity(lock_fd) != lock_identity:
            raise InstallFailure("installation lock identity changed during setup")
        lock_marker_proof = create_ownership_marker(
            lock_fd,
            ownership_marker_payload(transaction_nonce, "lock"),
        )
    finally:
        os.close(lock_fd)

    if entry_identity(skills_fd, target_name) is not None and not force:
        raise InstallFailure(
            f"refusing to overwrite existing installation: "
            f"{os.path.join(installation_root, 'skills', target_name)}; "
            "re-run with --force to preserve it as a backup"
        )

    stage_name, stage_identity = make_unique_directory(skills_fd, ".map-project.install-")
    stage_fd = open_child_directory(skills_fd, stage_name, create=False)
    stage_marker_proof = create_ownership_marker(
        stage_fd,
        ownership_marker_payload(transaction_nonce, "staging-root"),
    )
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
    staged_source_fd = os.open("map-project", directory_flags, dir_fd=stage_fd)
    try:
        target_marker_proof = create_ownership_marker(
            staged_source_fd,
            ownership_marker_payload(transaction_nonce, "promoted-target"),
        )
    finally:
        os.close(staged_source_fd)
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
        try:
            existing_target_fd = os.open(target_name, directory_flags, dir_fd=skills_fd)
        except OSError as exc:
            raise InstallFailure(
                "existing installation cannot be anchored for exact backup verification"
            ) from exc
        try:
            if fd_identity(existing_target_fd) != existing_target_identity:
                raise InstallFailure(
                    "existing installation identity changed before backup verification"
                )
            backup_payload_manifest = descriptor_tree_manifest(
                existing_target_fd,
                "existing installation backup payload",
            )
            if entry_identity(skills_fd, target_name) != existing_target_identity:
                raise InstallFailure(
                    "existing installation identity changed during backup verification"
                )
        finally:
            os.close(existing_target_fd)
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
                        payload_manifest=backup_payload_manifest,
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
    if target_marker_proof is None:
        raise InstallFailure("staged installation has no run-specific ownership proof")
    promoted_identity = staged_skill_identity
    promoted_marker_active = True
    try:
        with defer_handled_signals():
            move_owned_noreplace(
                stage_fd,
                "map-project",
                skills_fd,
                target_name,
                staged_skill_identity,
                label="staged installation promotion",
                marker_proof=target_marker_proof,
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
        verify_ownership_marker(
            target_fd,
            target_marker_proof,
            "installed target",
        )
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
    ):
        if backup_payload_manifest is None:
            raise InstallFailure("backup has no exact transaction payload proof")
        verify_owned_directory(
            backup_fd,
            backup_name,
            backup_identity,
            label="preserved installation backup",
            payload_manifest=backup_payload_manifest,
        )

    if expected_manifest is None or target_marker_proof is None:
        raise InstallFailure("installed target verification proof is incomplete")
    clean_expected_manifest = dict(expected_manifest)
    if clean_expected_manifest.pop((INSTALL_MARKER_NAME,), None) is None:
        raise InstallFailure("expected installation snapshot lost its ownership marker")
    target_fd = os.open(target_name, directory_flags, dir_fd=skills_fd)
    try:
        if fd_identity(target_fd) != promoted_identity:
            raise InstallFailure("installed target identity changed before commit")
        verify_ownership_marker(
            target_fd,
            target_marker_proof,
            "installed target",
        )
        with defer_handled_signals():
            remove_ownership_marker(
                target_fd,
                target_marker_proof,
                "installed target",
            )
            promoted_marker_active = False
        if (
            descriptor_tree_manifest(target_fd, f"committed {host_label} skill")
            != clean_expected_manifest
        ):
            raise InstallFailure("installed target changed at the marker-removal commit point")
        if (
            fd_identity(target_fd) != promoted_identity
            or entry_identity(skills_fd, target_name) != promoted_identity
        ):
            raise InstallFailure("installed target identity changed during commit")
    finally:
        os.close(target_fd)
    exit_code = 0
except BaseException as exc:
    interrupted_signal = exc.signum if isinstance(exc, InstallInterrupted) else None
    if (
        skills_fd is not None
        and promoted_identity is not None
        and promoted_marker_active
        and target_marker_proof is not None
    ):
        current_target_identity = entry_identity(skills_fd, target_name)
        if current_target_identity == promoted_identity:
            if not remove_owned_entry(
                skills_fd,
                target_name,
                promoted_identity,
                target_marker_proof,
            ):
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
                    payload_manifest=backup_payload_manifest,
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
        if stage_marker_proof is None or not remove_owned_entry(
            skills_fd,
            stage_name,
            stage_identity,
            stage_marker_proof,
        ):
            print(f"{command_name}: unable to remove owned staging directory", file=sys.stderr)
            exit_code = 1
    if skills_fd is not None and lock_identity is not None:
        if lock_marker_proof is None or not remove_owned_entry(
            skills_fd,
            lock_name,
            lock_identity,
            lock_marker_proof,
        ):
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
