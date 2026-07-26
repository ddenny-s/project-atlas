#!/usr/bin/env python3
"""Safe, dependency-free helpers for creating and checking a Project Atlas."""

from __future__ import annotations

import argparse
import ast
import ctypes
import csv
import errno
import fnmatch
import hashlib
import html
import io
import json
import os
import re
import selectors
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence
from urllib.parse import unquote


if sys.version_info < (3, 10):
    raise SystemExit("atlas: Python 3.10 or newer is required")


MODES = ("QUICK", "STANDARD", "FORENSIC")

MODE_FILES: dict[str, tuple[str, ...]] = {
    "QUICK": ("PROJECT_ATLAS.md",),
    "STANDARD": (
        "ATLAS_INDEX.md",
        "PRODUCT_AND_REQUIREMENTS.md",
        "CURRENT_ARCHITECTURE.md",
        "RUNTIME_AND_ENTRYPOINTS.md",
        "DATA_STATE_AND_AUTHORITY.md",
        "PRODUCT_FLOWS.md",
        "QUALITY_SECURITY_AND_OPERATIONS.md",
        "FINDINGS_AND_DISPOSITIONS.md",
        "TARGET_ARCHITECTURE.md",
        "MIGRATION_PLAN.md",
        "OPEN_UNKNOWNS.md",
        "LIVE_HANDOFF.md",
    ),
    "FORENSIC": (
        "ATLAS_INDEX.md",
        "PRODUCT_AND_REQUIREMENTS.md",
        "CURRENT_ARCHITECTURE.md",
        "RUNTIME_AND_ENTRYPOINTS.md",
        "DATA_STATE_AND_AUTHORITY.md",
        "PRODUCT_FLOWS.md",
        "QUALITY_SECURITY_AND_OPERATIONS.md",
        "FINDINGS_AND_DISPOSITIONS.md",
        "TARGET_ARCHITECTURE.md",
        "MIGRATION_PLAN.md",
        "TRACEABILITY.tsv",
        "OPEN_UNKNOWNS.md",
        "LIVE_HANDOFF.md",
    ),
}

REQUIRED_MARKERS: dict[str, tuple[str, ...]] = {
    "PROJECT_ATLAS.md": (
        "# Project Atlas",
        "QUICK",
        "## Scope and Depth Rationale",
        "## Evidence Snapshot",
        "## Purpose",
        "## Entry Point",
        "## Inputs and Outputs",
        "## Dependencies",
        "## Verification",
        "## Exact Validation Result",
        "## Risks",
        "## Exclusions",
        "## Evidence Legend",
        "## Next Safe Action",
        "## Source References",
        "## Unknowns",
    ),
    "ATLAS_INDEX.md": ("# Project Atlas", "## Document Map", "LIVE_HANDOFF.md"),
    "PRODUCT_AND_REQUIREMENTS.md": (
        "# Product and Requirements",
        "## Purpose",
        "## Users and Outcomes",
        "## Evidence",
    ),
    "CURRENT_ARCHITECTURE.md": ("# Current Architecture", "## Components", "## Evidence"),
    "RUNTIME_AND_ENTRYPOINTS.md": (
        "# Runtime and Entrypoints",
        "## Entry Points",
        "## Effects",
    ),
    "DATA_STATE_AND_AUTHORITY.md": (
        "# Data, State, and Authority",
        "## State Writers",
        "## Authority",
    ),
    "PRODUCT_FLOWS.md": ("# Product Flows", "## Flow Registry", "## Evidence"),
    "QUALITY_SECURITY_AND_OPERATIONS.md": (
        "# Quality, Security, and Operations",
        "## Test Evidence",
        "## Security",
        "## Reliability and Recovery",
    ),
    "FINDINGS_AND_DISPOSITIONS.md": (
        "# Findings and Dispositions",
        "Keep",
        "Rewrite",
        "Delete",
    ),
    "TARGET_ARCHITECTURE.md": ("# Target Architecture", "## Proposed Components", "## Tradeoffs"),
    "MIGRATION_PLAN.md": ("# Migration Plan", "## Sequence", "## Rollback"),
    "OPEN_UNKNOWNS.md": ("# Open Unknowns", "UNKNOWN"),
    "LIVE_HANDOFF.md": ("# Live Handoff", "## Evidence Freshness", "## Continue From Here"),
}

TRACEABILITY_HEADER = (
    "fact_id",
    "claim_kind",
    "claim",
    "source_type",
    "source_ref",
    "observed_at",
    "status",
    "atlas_refs",
    "notes",
)
CLAIM_KINDS = {"CONFIRMED", "INFERENCE", "HYPOTHESIS", "TARGET", "UNKNOWN"}
SOURCE_TYPES = {"FILE", "SCHEMA", "CONFIG", "TEST", "COMMAND", "RUNTIME", "EXTERNAL", "UNRESOLVED"}
ACTIVE_TRACE_STATUSES = {"ACTIVE", "CURRENT"}
TRACE_STATUSES = ACTIVE_TRACE_STATUSES | {"STALE", "SUPERSEDED"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

TABLE_CONTRACTS: dict[str, tuple[str, ...]] = {
    "PRODUCT_AND_REQUIREMENTS.md": ("ID", "Claim kind", "Requirement", "Source", "Status"),
    "FINDINGS_AND_DISPOSITIONS.md": (
        "ID",
        "Claim kind",
        "Severity",
        "Finding",
        "Affected scope",
        "Evidence",
        "Impact",
        "Disposition",
        "Prerequisites",
        "Verification",
        "Rollback",
        "Status",
    ),
    "MIGRATION_PLAN.md": (
        "Stage",
        "Change",
        "Preconditions",
        "Compatibility and state/data handling",
        "Primary signal",
        "Secondary signals",
        "Decision authority",
        "Rollback",
        "Status",
    ),
}
FORENSIC_TABLE_CONTRACTS: dict[str, tuple[str, ...]] = {
    **TABLE_CONTRACTS,
    "ATLAS_INDEX.md": (
        "ID",
        "Claim kind",
        "Claim",
        "Population",
        "Discovery method",
        "Numerator",
        "Denominator",
        "Exclusions",
        "Status",
    ),
    "MIGRATION_PLAN.md": (
        "Stage",
        "Claim kind",
        "Change",
        "Preconditions",
        "Compatibility and state/data handling",
        "Primary signal",
        "Secondary signals",
        "Decision authority",
        "Rollback",
        "Status",
    ),
    "OPEN_UNKNOWNS.md": ("ID", "UNKNOWN", "Consequence", "Next evidence", "Owner", "Status"),
    "LIVE_HANDOFF.md": (
        "ID",
        "Review kind",
        "Reviewer ref",
        "Independence",
        "Reviewed snapshot",
        "Verdict",
        "Critical",
        "Important",
        "Retained evidence summary",
        "Remaining limits",
        "Reviewed at",
        "Status",
    ),
}
TABLE_SECTIONS = {
    "ATLAS_INDEX.md": "Coverage Claims",
    "PRODUCT_AND_REQUIREMENTS.md": "Requirements",
    "FINDINGS_AND_DISPOSITIONS.md": "Findings",
    "MIGRATION_PLAN.md": "Sequence",
    "OPEN_UNKNOWNS.md": "Open Unknowns",
    "LIVE_HANDOFF.md": "Independent Reviews",
}
TABLE_IDENTITY_COLUMNS = {
    "ATLAS_INDEX.md": {"id", "claim"},
    "PRODUCT_AND_REQUIREMENTS.md": {"id", "requirement"},
    "FINDINGS_AND_DISPOSITIONS.md": {"id", "finding"},
    "MIGRATION_PLAN.md": {"stage", "change"},
    "OPEN_UNKNOWNS.md": {"id", "unknown"},
    "LIVE_HANDOFF.md": {"id", "review kind"},
}
TABLE_ID_COLUMNS = {
    "ATLAS_INDEX.md": "ID",
    "PRODUCT_AND_REQUIREMENTS.md": "ID",
    "FINDINGS_AND_DISPOSITIONS.md": "ID",
    "MIGRATION_PLAN.md": "Stage",
    "OPEN_UNKNOWNS.md": "ID",
    "LIVE_HANDOFF.md": "ID",
}
OPTIONAL_COMPLETION_TABLES = {"OPEN_UNKNOWNS.md", "LIVE_HANDOFF.md"}
REVIEW_KINDS = {"CORRECTNESS", "SECURITY"}
REVIEW_INDEPENDENCE = {"FRESH_CONTEXT", "EXTERNAL_REVIEWER"}
REVIEW_PLACEHOLDERS = {"", "-", "N/A", "NONE", "UNKNOWN"}
REVIEW_MIN_SUMMARY_CHARACTERS = 32
REVIEW_MIN_SUMMARY_WORDS = 5
REVIEW_MIN_LIMIT_CHARACTERS = 12
REVIEW_MIN_LIMIT_WORDS = 2
REVIEW_FRESHNESS_WINDOW = timedelta(days=7)
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
DEPTH_DECISION_FIELDS = (
    "Selected by",
    "Conflicting automatic signals",
    "Intentionally omitted coverage",
    "Escalation condition",
)
SOURCE_SNAPSHOT_VERSION = "0.2"
ATLAS_REF_PREFIXES = {
    "ATLAS_INDEX.md#coverage",
    "PRODUCT_AND_REQUIREMENTS.md#requirements",
    "FINDINGS_AND_DISPOSITIONS.md#findings",
    "MIGRATION_PLAN.md#migration",
    "OPEN_UNKNOWNS.md#unknowns",
    "LIVE_HANDOFF.md#reviews",
}

COMMAND_NOTES = re.compile(
    r"(?:^|;\s*)cwd=(?P<cwd>[^;]+);\s*exit=(?P<exit>-?\d+);\s*"
    r"stdout_sha256=(?P<digest>[0-9a-f]{64})(?:;|$)"
)
SHELL_FENCE = re.compile(r"```(?:sh|bash|shell)\s*\n(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)
ANGLE_SUBSTITUTION_TOKEN = re.compile(r"<[^>\n]+>")
HTML_TAG = re.compile(
    r"</?[A-Za-z][A-Za-z0-9-]*"
    r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*"
    r"(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))?)*\s*/?>"
)
HTML_ATTRIBUTE_VALUE = re.compile(
    r"\b(?P<name>[A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*"
    r"(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|"
    r"(?P<bare>[^\s\"'=<>`]+))"
)
HTML_SINGLE_URL_ATTRIBUTES = frozenset(
    {
        "action",
        "background",
        "cite",
        "classid",
        "codebase",
        "data",
        "data-source",
        "dynsrc",
        "formaction",
        "href",
        "icon",
        "itemid",
        "longdesc",
        "lowsrc",
        "manifest",
        "poster",
        "profile",
        "src",
        "usemap",
        "xlink:href",
    }
)
HTML_SPACE_SEPARATED_URL_ATTRIBUTES = frozenset({"archive", "attributionsrc", "ping"})
HTML_SRCSET_ATTRIBUTES = frozenset({"imagesrcset", "srcset"})
POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9+.:/*?$>{}\\])/(?:Users|home|private|tmp|var|opt|etc|usr|root|"
    r"srv|mnt|media|Volumes|workspace|workspaces|app|Applications|System|Library)(?:/[^\s`\"'<>|]*)?"
)
GENERIC_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9+.:/*?$>{}\\])/(?!/)[^\s`\"'<>|]+"
)
HTTP_ROUTE = re.compile(
    r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE|CONNECT)\s+/[^\s`\"'<>|]*",
    re.IGNORECASE,
)
LABELED_ROUTE = re.compile(
    r"\b(?:api\s+route|endpoint|http\s+path|route)\s*[:=]?\s*`?/[^\s`\"'<>|]*`?",
    re.IGNORECASE,
)
WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/](?:[^\s`\"'<>|]+)|"
    r"\\\\[^\\\s`\"'<>|]+\\[^\\\s`\"'<>|]+(?:\\[^\\\s`\"'<>|]+)*)"
)
HOME_SHORTHAND_PATH = re.compile(r"(?<![A-Za-z0-9_$])~[\\/][^\s`\"'<>|]+")
FILE_URI = re.compile(r"(?i)\bfile:(?://)?[\\/][^\s`\"'<>|]+")
SECRET_MATERIAL_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"npm_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:sk|rk)_live_[A-Za-z0-9]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"),
    re.compile(
        r"(?i)\bAuthorization\s*:\s*(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{16,}"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s/]+"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\b"
        r"\s*[:=]\s*(?:\"[^\"\r\n]{8,}\"|'[^'\r\n]{8,}')"
    ),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\b"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{12,}"
    ),
)

EXCLUDED_DIRECTORY_NAMES = {
    ".atlas-private",
    ".cache",
    ".git",
    ".gradle",
    ".hg",
    ".idea",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".private",
    ".pytest_cache",
    ".ruff_cache",
    ".scratch",
    ".secrets",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "credentials",
    "deriveddata",
    "dist",
    "env",
    "node_modules",
    "out",
    "pods",
    "private",
    "project-atlas",
    "secrets",
    "target",
    "vendor",
    "venv",
}
EXCLUDED_FILE_NAMES = {
    ".env",
    ".netrc",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
}
SENSITIVE_SUFFIXES = {
    ".db",
    ".dump",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}
TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cjs",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".cts",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".mjs",
    ".mts",
    ".php",
    ".prisma",
    ".properties",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SOURCE_SUFFIXES = TEXT_SUFFIXES - {".md", ".txt", ".json", ".yaml", ".yml", ".toml"}
GENERATED_FILE_NAMES = {name for names in MODE_FILES.values() for name in names} | {"SOURCE_SNAPSHOT.json"}
MAX_CLASSIFICATION_BYTES = 512 * 1024
MAX_CLASSIFICATION_FILES = 2_000
MAX_CLASSIFICATION_TOTAL_BYTES = 32 * 1024 * 1024
MAX_INVENTORY_FILES = 100_000
MAX_INVENTORY_DIRECTORIES = 20_000
MAX_INVENTORY_DEPTH = 64
MAX_INVENTORY_PATH_BYTES = 16 * 1024 * 1024
MAX_IGNORE_FILE_BYTES = 1024 * 1024
MAX_GIT_CHECK_IGNORE_STDOUT_BYTES = (
    MAX_INVENTORY_PATH_BYTES + MAX_INVENTORY_FILES + MAX_INVENTORY_DIRECTORIES
)
MAX_GIT_STDERR_BYTES = 256 * 1024
GIT_CHECK_IGNORE_SECONDS = 15
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_ATLAS_TOTAL_BYTES = 16 * 1024 * 1024
MAX_TRACEABILITY_BYTES = 4 * 1024 * 1024
MAX_TRACEABILITY_ROWS = 10_000
MAX_REGISTRY_ROWS = 5_000
MAX_SNAPSHOT_JSON_DEPTH = 8
MAX_SNAPSHOT_JSON_NODES = 50_000
MAX_EVIDENCE_SOURCE_BYTES = 16 * 1024 * 1024
MAX_REPLAY_FILES = 2_000
MAX_REPLAY_FILE_BYTES = 4 * 1024 * 1024
MAX_REPLAY_TOTAL_BYTES = 32 * 1024 * 1024
MAX_REPLAY_STDOUT_BYTES = 4 * 1024 * 1024
MAX_REPLAY_STDERR_BYTES = 256 * 1024
MAX_REPLAY_SECONDS = 15
MAX_JSON_OUTPUT_BYTES = 8 * 1024 * 1024
FILE_SOURCE_TYPES = {"FILE", "SCHEMA", "CONFIG", "TEST"}
REVIEW_ATLAS_REF_PREFIX = "LIVE_HANDOFF.md#reviews/"
QUICK_COMPLETION_SECTIONS = (
    "Scope and Depth Rationale",
    "Evidence Snapshot",
    "Purpose",
    "Entry Point",
    "Inputs and Outputs",
    "Dependencies",
    "Verification",
    "Exact Validation Result",
    "Risks",
    "Exclusions",
    "Next Safe Action",
    "Source References",
)
STANDARD_STATIC_TEMPLATE_SECTIONS = {
    ("ATLAS_INDEX.md", "Document Map"),
    ("FINDINGS_AND_DISPOSITIONS.md", "Disposition Vocabulary"),
    ("LIVE_HANDOFF.md", "Reproducible Commands"),
    ("MIGRATION_PLAN.md", "Completion Gate"),
    ("OPEN_UNKNOWNS.md", "Resolved Unknowns"),
}
STANDARD_CURRENT_SOURCE_COLUMNS = {
    "PRODUCT_AND_REQUIREMENTS.md": "Source",
    "FINDINGS_AND_DISPOSITIONS.md": "Evidence",
}
CURRENT_MATERIAL_CLAIM_KINDS = {"CONFIRMED", "INFERENCE", "HYPOTHESIS"}


@dataclass(frozen=True)
class SafeInventory:
    """One authoritative allowlist for every project-source read or digest."""

    root: Path
    members: frozenset[PurePosixPath]
    ordered: tuple[PurePosixPath, ...]
    excluded_count: int
    root_device: int
    root_inode: int


@dataclass(frozen=True)
class ArtifactInventory:
    """Root-anchored allowlist for generated Atlas artifacts."""

    root: Path
    members: frozenset[PurePosixPath]
    root_device: int
    root_inode: int


@dataclass(frozen=True)
class CanonicalTableRow:
    """One validated row from a canonical Atlas registry."""

    filename: str
    line_number: int
    values: dict[str, str]


@dataclass(frozen=True)
class MaterialClaim:
    """The exact claim projection that a traceability row must cover."""

    atlas_ref: str
    claim_kind: str
    claim: str
    owner: str
    line_number: int


@dataclass(frozen=True)
class SnapshotBinding:
    """Validated digests and chronology boundary used by independent reviews."""

    source_scope_sha256: str
    review_input_sha256: str
    evidence_observed_through: datetime


@dataclass(frozen=True)
class ReplayCommandPlan:
    """A validated deterministic command and its bounded project targets."""

    arguments: tuple[str, ...]
    cwd_relative: PurePosixPath
    targets: tuple[str, ...]


class AtlasError(Exception):
    """A user-correctable CLI error."""


def parse_mode(value: str) -> str:
    mode = value.upper()
    if mode not in MODES:
        raise argparse.ArgumentTypeError(f"mode must be one of: {', '.join(MODES)}")
    return mode


def parse_non_negative_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("count must be a non-negative integer") from None
    if count < 0:
        raise argparse.ArgumentTypeError("count must be a non-negative integer")
    return count


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def reject_symlink_components(path: Path, label: str) -> None:
    absolute = lexical_absolute(path)
    allowed_system_aliases = {"/etc", "/tmp", "/var"} if sys.platform == "darwin" else set()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink() and current.as_posix() not in allowed_system_aliases:
            raise AtlasError(f"{label} traverses a symbolic link")


def require_directory(path: Path, label: str) -> Path:
    absolute = canonical_directory_path(path)
    try:
        descriptor = open_directory_descriptor(absolute)
    except AtlasError:
        raise AtlasError(f"{label} is not a regular non-symbolic directory") from None
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISDIR(status.st_mode):
            raise AtlasError(f"{label} is not a directory")
    finally:
        os.close(descriptor)
    return absolute


def project_from_args(args: argparse.Namespace, *, required: bool = True) -> Path | None:
    option = getattr(args, "project", None)
    positional = getattr(args, "project_path", None)
    if option is not None and positional is not None:
        if option.expanduser().resolve() != positional.expanduser().resolve():
            raise AtlasError("provide the project either positionally or with --project, not both")
    selected = option or positional
    if selected is None:
        if required:
            raise AtlasError("a project directory is required; use --project PATH")
        return None
    return require_directory(selected, "project")


def canonical_directory_path(path: Path) -> Path:
    absolute = lexical_absolute(path)
    if sys.platform == "darwin" and absolute.parts[:2] in {
        ("/", "etc"),
        ("/", "tmp"),
        ("/", "var"),
    }:
        system_alias = Path(absolute.anchor, absolute.parts[1]).resolve(strict=True)
        return system_alias.joinpath(*absolute.parts[2:])
    return absolute


def open_directory_descriptor(path: Path) -> int:
    """Open an absolute directory component-by-component without following links."""

    if (
        os.open not in getattr(os, "supports_dir_fd", set())
        or not getattr(os, "O_NOFOLLOW", 0)
    ):
        raise AtlasError(
            "secure directory-descriptor operations are unavailable on this platform"
        )
    absolute = canonical_directory_path(path)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(absolute.anchor, directory_flags)
    except (OSError, TypeError, NotImplementedError):
        raise AtlasError("cannot safely open directory anchor") from None
    try:
        for part in absolute.parts[1:]:
            try:
                next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            except (OSError, TypeError, NotImplementedError):
                raise AtlasError("cannot safely traverse directory") from None
            status = os.fstat(next_descriptor)
            if not stat.S_ISDIR(status.st_mode):
                os.close(next_descriptor)
                raise AtlasError("directory path contains a non-directory component")
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_verified_directory(path: Path, label: str) -> tuple[Path, int]:
    resolved = require_directory(path, label)
    try:
        before = os.stat(resolved, follow_symlinks=False)
    except OSError:
        raise AtlasError(f"{label} cannot be inspected safely") from None
    if not stat.S_ISDIR(before.st_mode):
        raise AtlasError(f"{label} is not a directory")
    descriptor = open_directory_descriptor(resolved)
    after = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(descriptor)
        raise AtlasError(f"{label} changed during safe open")
    return resolved, descriptor


def _open_relative_descriptor(
    root: Path,
    relative: PurePosixPath,
    *,
    expected_root_identity: tuple[int, int] | None = None,
    label: str = "project source",
) -> int:
    """Open one regular file beneath root without following any path component."""

    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or any(part in {"", "."} for part in relative.parts)
    ):
        raise AtlasError("refusing an invalid relative file reference")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    supports_openat = os.open in getattr(os, "supports_dir_fd", set()) and bool(nofollow)

    if not supports_openat:
        candidate = root.joinpath(*relative.parts)
        if path_crosses_symlink(root, relative):
            raise AtlasError(f"{label} crosses a symbolic link: {relative.as_posix()}")
        try:
            root_status = root.lstat()
            if expected_root_identity is not None and (
                root_status.st_dev,
                root_status.st_ino,
            ) != expected_root_identity:
                raise AtlasError(f"{label} root changed after allowlist construction")
            before = candidate.lstat()
            descriptor = os.open(candidate, file_flags)
            after = os.fstat(descriptor)
        except OSError:
            raise AtlasError(f"cannot safely open {label}: {relative.as_posix()}") from None
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(after.st_mode)
            or before.st_nlink != 1
            or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            os.close(descriptor)
            raise AtlasError(f"{label} changed during safe open: {relative.as_posix()}")
        return descriptor

    try:
        root_descriptor = open_directory_descriptor(root)
    except AtlasError:
        raise AtlasError(f"cannot safely open {label} root") from None
    current_descriptor = root_descriptor
    try:
        root_status = os.fstat(root_descriptor)
        if not stat.S_ISDIR(root_status.st_mode):
            raise AtlasError(f"{label} root is not a regular directory")
        if expected_root_identity is not None and (
            root_status.st_dev,
            root_status.st_ino,
        ) != expected_root_identity:
            raise AtlasError(f"{label} root changed after allowlist construction")
        for part in relative.parts[:-1]:
            try:
                next_descriptor = os.open(
                    part, directory_flags | nofollow, dir_fd=current_descriptor
                )
            except OSError:
                raise AtlasError(
                    f"cannot safely traverse {label}: {relative.as_posix()}"
                ) from None
            next_status = os.fstat(next_descriptor)
            if not stat.S_ISDIR(next_status.st_mode):
                os.close(next_descriptor)
                raise AtlasError(
                    f"{label} parent is not a directory: {relative.as_posix()}"
                )
            if current_descriptor != root_descriptor:
                os.close(current_descriptor)
            current_descriptor = next_descriptor
        try:
            descriptor = os.open(
                relative.parts[-1], file_flags | nofollow, dir_fd=current_descriptor
            )
        except OSError:
            raise AtlasError(f"cannot safely open {label}: {relative.as_posix()}") from None
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            os.close(descriptor)
            raise AtlasError(
                f"{label} is not a single-link regular file: {relative.as_posix()}"
            )
        return descriptor
    finally:
        if current_descriptor != root_descriptor:
            os.close(current_descriptor)
        os.close(root_descriptor)


def _read_relative_bytes(
    root: Path,
    relative: PurePosixPath,
    *,
    expected_root_identity: tuple[int, int] | None = None,
    maximum_bytes: int | None = None,
    label: str = "project source",
) -> bytes:
    descriptor = _open_relative_descriptor(
        root,
        relative,
        expected_root_identity=expected_root_identity,
        label=label,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AtlasError(f"{label} is not a single-link regular file: {relative.as_posix()}")
        if maximum_bytes is not None and before.st_size > maximum_bytes:
            raise AtlasError(f"{label} exceeds the safe read limit: {relative.as_posix()}")
        chunks: list[bytes] = []
        total = 0
        while True:
            read_size = 1024 * 1024
            if maximum_bytes is not None:
                read_size = min(read_size, maximum_bytes - total + 1)
            chunk = os.read(descriptor, read_size)
            if not chunk:
                break
            total += len(chunk)
            if maximum_bytes is not None and total > maximum_bytes:
                raise AtlasError(f"{label} exceeds the safe read limit: {relative.as_posix()}")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_nlink,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_nlink,
        )
        if before_identity != after_identity or after.st_nlink != 1:
            raise AtlasError(f"{label} changed during stable read: {relative.as_posix()}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def read_inventory_bytes(
    inventory: SafeInventory,
    relative: PurePosixPath,
    *,
    maximum_bytes: int | None = None,
) -> bytes:
    if relative not in inventory.members:
        raise AtlasError(f"project source is outside the safe inventory: {relative.as_posix()}")
    return _read_relative_bytes(
        inventory.root,
        relative,
        expected_root_identity=(inventory.root_device, inventory.root_inode),
        maximum_bytes=maximum_bytes,
    )


def hash_inventory_file(inventory: SafeInventory, relative: PurePosixPath) -> str:
    if relative not in inventory.members:
        raise AtlasError(f"project source is outside the safe inventory: {relative.as_posix()}")
    descriptor = _open_relative_descriptor(
        inventory.root,
        relative,
        expected_root_identity=(inventory.root_device, inventory.root_inode),
    )
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AtlasError(
                f"project source is not a single-link regular file: {relative.as_posix()}"
            )
        if before.st_size > MAX_EVIDENCE_SOURCE_BYTES:
            raise AtlasError(
                f"project source exceeds the evidence hash limit: {relative.as_posix()}"
            )
        total = 0
        for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
            total += len(chunk)
            if total > MAX_EVIDENCE_SOURCE_BYTES:
                raise AtlasError(
                    f"project source exceeds the evidence hash limit: {relative.as_posix()}"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_nlink,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_nlink,
        ) or after.st_nlink != 1:
            raise AtlasError(
                f"project source changed during stable hash: {relative.as_posix()}"
            )
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def build_artifact_inventory(atlas_root: Path) -> ArtifactInventory:
    root = canonical_directory_path(atlas_root)
    descriptor = open_directory_descriptor(root)
    try:
        status = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    members = frozenset(PurePosixPath(name) for name in GENERATED_FILE_NAMES)
    return ArtifactInventory(
        root=root,
        members=members,
        root_device=status.st_dev,
        root_inode=status.st_ino,
    )


def _artifact_root_descriptor(artifacts: ArtifactInventory) -> int:
    descriptor = open_directory_descriptor(artifacts.root)
    status = os.fstat(descriptor)
    if (status.st_dev, status.st_ino) != (
        artifacts.root_device,
        artifacts.root_inode,
    ):
        os.close(descriptor)
        raise AtlasError("atlas artifact root changed after allowlist construction")
    return descriptor


def artifact_state(artifacts: ArtifactInventory, relative: PurePosixPath) -> str:
    if relative not in artifacts.members or len(relative.parts) != 1:
        return "outside-allowlist"
    descriptor = _artifact_root_descriptor(artifacts)
    try:
        try:
            status = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "unreadable"
    finally:
        os.close(descriptor)
    if stat.S_ISREG(status.st_mode):
        if status.st_nlink != 1:
            return "hardlink"
        return "regular"
    if stat.S_ISLNK(status.st_mode):
        return "symlink"
    return "other"


def read_artifact_bytes(
    artifacts: ArtifactInventory,
    relative: PurePosixPath,
    *,
    maximum_bytes: int | None = None,
) -> bytes:
    if relative not in artifacts.members:
        raise AtlasError("atlas artifact is outside the expected allowlist")
    if maximum_bytes is None:
        maximum_bytes = MAX_ARTIFACT_BYTES
    return _read_relative_bytes(
        artifacts.root,
        relative,
        expected_root_identity=(artifacts.root_device, artifacts.root_inode),
        maximum_bytes=maximum_bytes,
        label="atlas artifact",
    )


def read_artifact_text(
    artifacts: ArtifactInventory,
    relative: PurePosixPath,
    *,
    maximum_bytes: int | None = None,
) -> str:
    try:
        return read_artifact_bytes(
            artifacts, relative, maximum_bytes=maximum_bytes
        ).decode("utf-8")
    except UnicodeError:
        raise AtlasError(f"atlas artifact is not UTF-8: {relative.as_posix()}") from None


def validate_artifact_resource_bounds(
    artifacts: ArtifactInventory, names: Iterable[str]
) -> list[str]:
    errors: list[str] = []
    total = 0
    for name in names:
        relative = PurePosixPath(name)
        if artifact_state(artifacts, relative) != "regular":
            continue
        try:
            descriptor = _open_relative_descriptor(
                artifacts.root,
                relative,
                expected_root_identity=(artifacts.root_device, artifacts.root_inode),
                label="atlas artifact",
            )
        except AtlasError as exc:
            errors.append(str(exc))
            continue
        try:
            size = os.fstat(descriptor).st_size
        finally:
            os.close(descriptor)
        limit = MAX_TRACEABILITY_BYTES if name == "TRACEABILITY.tsv" else MAX_ARTIFACT_BYTES
        if size > limit:
            errors.append(f"atlas artifact exceeds its byte limit: {name}")
        total += size
    if total > MAX_ATLAS_TOTAL_BYTES:
        errors.append("atlas artifacts exceed the aggregate byte limit")
    return errors


@dataclass(frozen=True)
class IgnoreComponent:
    is_double_star: bool
    matcher: re.Pattern[str] | None


@dataclass(frozen=True)
class IgnoreRule:
    base: PurePosixPath
    components: tuple[IgnoreComponent, ...]
    directory_only: bool
    negated: bool
    anchored: bool


def run_bounded_process(
    arguments: Sequence[str],
    *,
    input_bytes: bytes | None,
    stdout_limit: int,
    stderr_limit: int,
    seconds: float,
    operation: str,
    executable: str | None = None,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run one bounded child and contain its ordinary descendants.

    On POSIX the child starts in a fresh session and cleanup kills every
    descendant that remains in that process group.  A descendant that
    deliberately creates another session or process group is outside this
    containment boundary.
    """
    process = subprocess.Popen(
        list(arguments),
        executable=executable,
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        start_new_session=os.name == "posix",
    )
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    streams = {"stdout": process.stdout, "stderr": process.stderr}
    input_offset = 0
    deadline = time.monotonic() + seconds
    try:
        for name, stream in streams.items():
            if stream is None:
                raise AtlasError(f"{operation} process did not expose bounded output pipes")
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, data=name)
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, data="stdin")

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AtlasError(f"{operation} process exceeded the time limit")
            events = selector.select(min(remaining, 0.25))
            if not events:
                continue
            for key, _mask in events:
                stream = key.fileobj
                name = key.data
                if name == "stdin":
                    assert input_bytes is not None
                    if input_offset >= len(input_bytes):
                        selector.unregister(stream)
                        stream.close()
                        continue
                    try:
                        written = os.write(
                            stream.fileno(),
                            memoryview(input_bytes)[input_offset : input_offset + 64 * 1024],
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    input_offset += written
                    if input_offset >= len(input_bytes):
                        selector.unregister(stream)
                        stream.close()
                    continue

                capacity = limits[name] - len(buffers[name])
                try:
                    chunk = os.read(stream.fileno(), min(64 * 1024, capacity + 1))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                if len(chunk) > capacity:
                    raise AtlasError(f"{operation} {name} exceeds the safe output limit")
                buffers[name].extend(chunk)

        remaining = max(0.0, deadline - time.monotonic())
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise AtlasError(f"{operation} process exceeded the time limit") from None
        completed = subprocess.CompletedProcess(
            list(arguments),
            return_code,
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
        )
        return completed
    finally:
        selector.close()
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        for stream in streams.values():
            if stream is not None and not stream.closed:
                stream.close()


def git_metadata_present(root: Path) -> bool:
    """Inspect root and its ancestors for non-symlink .git metadata."""

    descriptor = open_directory_descriptor(root)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        while True:
            try:
                metadata = os.stat(".git", dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError:
                raise AtlasError("Git metadata cannot be inspected safely") from None
            else:
                if stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
                    return True
                raise AtlasError("Git metadata cannot be inspected safely")

            parent_descriptor: int | None = None
            try:
                parent_descriptor = os.open("..", directory_flags, dir_fd=descriptor)
                current_status = os.fstat(descriptor)
                parent_status = os.fstat(parent_descriptor)
            except OSError:
                if parent_descriptor is not None:
                    os.close(parent_descriptor)
                raise AtlasError("Git metadata cannot be inspected safely") from None
            if (current_status.st_dev, current_status.st_ino) == (
                parent_status.st_dev,
                parent_status.st_ino,
            ):
                os.close(parent_descriptor)
                return False
            os.close(descriptor)
            descriptor = parent_descriptor
    finally:
        os.close(descriptor)


def read_ignore_metadata(
    directory: Path,
    root: Path,
) -> tuple[tuple[PurePosixPath, bytes], ...]:
    """Read in-scope ignore files through stable no-follow descriptors."""

    metadata: list[tuple[PurePosixPath, bytes]] = []
    for filename in (".gitignore", ".ignore"):
        path = directory / filename
        try:
            details = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            raise AtlasError("ignore metadata cannot be inspected safely") from None
        if not stat.S_ISREG(details.st_mode):
            raise AtlasError("ignore metadata must be a regular non-symbolic file")
        if details.st_nlink != 1:
            raise AtlasError("ignore metadata must not be hardlinked")
        relative = PurePosixPath(path.relative_to(root).as_posix())
        try:
            content = _read_relative_bytes(
                root,
                relative,
                maximum_bytes=MAX_IGNORE_FILE_BYTES,
                label="ignore metadata",
            )
        except AtlasError:
            raise AtlasError("ignore metadata cannot be read safely") from None
        metadata.append((relative, content))
    return tuple(metadata)


def trusted_host_executable(
    name: str,
    *,
    prohibited_roots: Sequence[Path],
) -> str | None:
    """Resolve a PATH tool without ever executing one supplied by the project."""

    candidate = shutil.which(name)
    if candidate is None:
        return None
    try:
        executable = Path(candidate).resolve(strict=True)
        metadata = executable.stat()
        resolved_roots = tuple(root.resolve(strict=True) for root in prohibited_roots)
    except OSError:
        raise AtlasError(f"host {name} executable is unsafe") from None
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else metadata.st_uid
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, effective_uid}
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(executable, os.X_OK)
    ):
        raise AtlasError(f"host {name} executable is unsafe")
    if any(executable == root or root in executable.parents for root in resolved_roots):
        raise AtlasError(f"host {name} executable is unsafe")
    try:
        executable_is_repository_controlled = git_metadata_present(executable.parent)
    except AtlasError:
        raise AtlasError(f"host {name} executable is unsafe") from None
    if executable_is_repository_controlled:
        raise AtlasError(f"host {name} executable is unsafe")
    for directory in executable.parents:
        try:
            directory_mode = directory.stat().st_mode
        except OSError:
            raise AtlasError(f"host {name} executable is unsafe") from None
        if directory_mode & stat.S_IWOTH and not directory_mode & stat.S_ISVTX:
            raise AtlasError(f"host {name} executable is unsafe")
    return os.fspath(executable)


def git_ignore_executable(root: Path) -> str | None:
    """Resolve trusted Git without allowing it to inspect source repository metadata."""

    executable = trusted_host_executable("git", prohibited_roots=(root,))
    if executable is None:
        if git_metadata_present(root):
            raise AtlasError("Git ignore metadata cannot be checked because Git is unavailable")
        return None
    return executable


def git_ignored_paths(
    root: Path,
    executable: str | None,
    candidates: Sequence[PurePosixPath],
    *,
    directory_candidates: frozenset[PurePosixPath] = frozenset(),
    gitignore_files: Sequence[tuple[PurePosixPath, bytes]] | None = None,
) -> frozenset[PurePosixPath]:
    """Classify names in an isolated mirror without consulting source Git metadata."""

    if executable is None or not candidates:
        return frozenset()
    if gitignore_files is None:
        gitignore_files = tuple(
            item
            for item in read_ignore_metadata(root, root)
            if item[0].name == ".gitignore"
        )
    encoded = b"\0".join(item.as_posix().encode("utf-8") for item in candidates) + b"\0"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        with tempfile.TemporaryDirectory(prefix="project-atlas-git-ignore-") as temporary:
            sandbox = Path(temporary)
            metadata_root = sandbox / "metadata"
            worktree_root = sandbox / "worktree"
            (metadata_root / "objects").mkdir(parents=True)
            (metadata_root / "refs" / "heads").mkdir(parents=True)
            worktree_root.mkdir()
            (metadata_root / "HEAD").write_text(
                "ref: refs/heads/project-atlas\n",
                encoding="utf-8",
            )
            (metadata_root / "config").write_text(
                "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
                encoding="utf-8",
            )

            for relative, content in gitignore_files:
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or relative.name != ".gitignore"
                ):
                    raise AtlasError("Git ignore metadata cannot be checked safely")
                destination = worktree_root.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)

            for relative in sorted(directory_candidates, key=lambda item: item.as_posix()):
                if relative not in candidates:
                    raise AtlasError("Git ignore metadata cannot be checked safely")
                worktree_root.joinpath(*relative.parts).mkdir(parents=True, exist_ok=True)
            for relative in candidates:
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise AtlasError("Git ignore metadata cannot be checked safely")
                destination = worktree_root.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if relative not in directory_candidates and not destination.exists():
                    destination.touch()

            completed = run_bounded_process(
                [
                    executable,
                    "--no-pager",
                    f"--git-dir={metadata_root}",
                    f"--work-tree={worktree_root}",
                    "check-ignore",
                    "--no-index",
                    "-z",
                    "--stdin",
                ],
                executable=executable,
                cwd=worktree_root,
                environment=environment,
                input_bytes=encoded,
                stdout_limit=min(MAX_GIT_CHECK_IGNORE_STDOUT_BYTES, len(encoded)),
                stderr_limit=MAX_GIT_STDERR_BYTES,
                seconds=GIT_CHECK_IGNORE_SECONDS,
                operation="Git ignore query",
            )
    except (AtlasError, OSError):
        raise AtlasError("Git ignore metadata could not be checked safely") from None
    if completed.returncode not in {0, 1}:
        raise AtlasError("Git ignore metadata could not be checked safely")
    candidate_set = frozenset(candidates)
    ignored: set[PurePosixPath] = set()
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            relative = PurePosixPath(raw.decode("utf-8"))
        except UnicodeDecodeError:
            raise AtlasError("Git returned a non-UTF-8 ignored path; value redacted") from None
        if relative not in candidate_set:
            raise AtlasError("Git returned an unexpected ignored path; value redacted")
        ignored.add(relative)
    return frozenset(ignored)


def trim_gitignore_trailing_spaces(raw: str) -> str:
    """Drop unescaped trailing spaces while retaining a backslash-escaped one."""

    end = len(raw)
    while end and raw[end - 1] == " ":
        backslashes = 0
        index = end - 2
        while index >= 0 and raw[index] == "\\":
            backslashes += 1
            index -= 1
        if backslashes % 2:
            break
        end -= 1
    return raw[:end]


IGNORE_ESCAPE_SENTINEL = re.compile("\0(?P<index>[0-9a-f]{8})\0")


def compile_ignore_component(raw: str) -> IgnoreComponent:
    """Compile one Git-style component without letting escapes become wildcards."""

    if not raw or "\0" in raw:
        raise AtlasError("ignore metadata contains unsupported pattern syntax")
    if raw == "**":
        return IgnoreComponent(True, re.compile(fnmatch.translate(raw)))

    transformed: list[str] = []
    escaped_literals: list[str] = []
    in_character_class = False
    index = 0
    while index < len(raw):
        character = raw[index]
        if character == "\\":
            if index + 1 >= len(raw) or in_character_class:
                raise AtlasError("ignore metadata contains unsupported pattern syntax")
            escaped = raw[index + 1]
            if escaped == "/":
                raise AtlasError("ignore metadata contains unsupported pattern syntax")
            sentinel = f"\0{len(escaped_literals):08x}\0"
            transformed.append(sentinel)
            escaped_literals.append(escaped)
            index += 2
            continue
        if character == "[":
            in_character_class = True
        elif character == "]":
            in_character_class = False
        transformed.append(character)
        index += 1

    translated = fnmatch.translate("".join(transformed))

    def restore_escape(match: re.Match[str]) -> str:
        escaped_index = int(match.group("index"), 16)
        if escaped_index >= len(escaped_literals):
            raise AtlasError("ignore metadata contains unsupported pattern syntax")
        return re.escape(escaped_literals[escaped_index])

    translated = IGNORE_ESCAPE_SENTINEL.sub(restore_escape, translated)
    try:
        matcher = re.compile(translated)
    except re.error:
        raise AtlasError("ignore metadata contains unsupported pattern syntax") from None
    return IgnoreComponent(False, matcher)


def split_ignore_pattern(raw: str) -> tuple[str, ...]:
    """Split only real separators; escaped separators are rejected fail-closed."""

    for index, character in enumerate(raw):
        if character != "/":
            continue
        backslashes = 0
        previous = index - 1
        while previous >= 0 and raw[previous] == "\\":
            backslashes += 1
            previous -= 1
        if backslashes % 2:
            raise AtlasError("ignore metadata contains unsupported pattern syntax")
    components = tuple(raw.split("/"))
    if not components or any(not component for component in components):
        raise AtlasError("ignore metadata contains unsupported pattern syntax")
    return components


def load_ignore_rules(
    directory: Path,
    root: Path,
    *,
    metadata: Sequence[tuple[PurePosixPath, bytes]] | None = None,
) -> tuple[IgnoreRule, ...]:
    rules: list[IgnoreRule] = []
    relative_directory = directory.relative_to(root)
    base = PurePosixPath(relative_directory.as_posix()) if relative_directory.parts else PurePosixPath()
    if metadata is None:
        metadata = read_ignore_metadata(directory, root)
    for _relative, content in metadata:
        try:
            lines = content.decode("utf-8", errors="strict").splitlines()
        except UnicodeError:
            raise AtlasError("ignore metadata cannot be read safely") from None
        for raw in lines:
            line = trim_gitignore_trailing_spaces(raw)
            if not line:
                continue
            if line.startswith("#"):
                continue
            if line.startswith("!"):
                line = line[1:]
                negated = True
            else:
                negated = False
            if not line:
                continue
            directory_only = line.endswith("/")
            anchored = line.startswith("/")
            if directory_only:
                line = line[:-1]
            if anchored:
                line = line[1:]
            if not line:
                continue
            components = tuple(
                compile_ignore_component(component)
                for component in split_ignore_pattern(line)
            )
            rules.append(IgnoreRule(base, components, directory_only, negated, anchored))
    return tuple(rules)


def gitignore_component_match(
    pattern_parts: tuple[IgnoreComponent, ...], path_parts: tuple[str, ...]
) -> bool:
    """Match one slash-delimited Git ignore pattern against one path prefix."""

    collapsed: list[IgnoreComponent] = []
    for component in pattern_parts:
        if (
            not component.is_double_star
            or not collapsed
            or not collapsed[-1].is_double_star
        ):
            collapsed.append(component)
    positions = {0}
    for pattern_index, component in enumerate(collapsed):
        if component.is_double_star:
            minimum = 1 if pattern_index == len(collapsed) - 1 else 0
            positions = {
                next_index
                for path_index in positions
                for next_index in range(path_index + minimum, len(path_parts) + 1)
            }
        else:
            assert component.matcher is not None
            positions = {
                path_index + 1
                for path_index in positions
                if path_index < len(path_parts)
                and component.matcher.fullmatch(path_parts[path_index]) is not None
            }
        if not positions:
            return False
    return len(path_parts) in positions


def matches_ignore_pattern(
    relative: PurePosixPath, *, is_directory: bool, rules: Sequence[IgnoreRule]
) -> bool:
    ignored = False
    for rule in rules:
        try:
            scoped = relative.relative_to(rule.base)
        except ValueError:
            continue
        scoped_parts = scoped.parts
        candidate_count = len(scoped_parts) if is_directory else len(scoped_parts) - 1
        if not rule.directory_only:
            candidate_count = len(scoped_parts)
        candidate_prefixes = (
            tuple(scoped_parts[:length]) for length in range(1, candidate_count + 1)
        )
        if len(rule.components) == 1 and not rule.anchored:
            component = rule.components[0]
            assert component.matcher is not None
            matched = any(
                component.matcher.fullmatch(path_component) is not None
                for prefix in candidate_prefixes
                for path_component in prefix[-1:]
            )
        else:
            matched = any(
                gitignore_component_match(rule.components, prefix)
                for prefix in candidate_prefixes
            )
        if matched:
            ignored = not rule.negated
    return ignored


def sensitive_relative_path(relative: PurePosixPath) -> bool:
    lowered_parts = tuple(part.lower() for part in relative.parts)
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in lowered_parts[:-1]):
        return True
    name = lowered_parts[-1] if lowered_parts else ""
    if name in EXCLUDED_FILE_NAMES or name.startswith(".env"):
        return True
    if any(marker in name for marker in ("credential", "private-key", "private_key", "secret")):
        return True
    return PurePosixPath(name).suffix.lower() in SENSITIVE_SUFFIXES


def should_exclude(
    relative: PurePosixPath,
    *,
    is_directory: bool,
    ignore_rules: Sequence[IgnoreRule],
) -> bool:
    if not relative.parts:
        return False
    if relative.name.lower() == ".git":
        return True
    if relative.name.lower() in EXCLUDED_DIRECTORY_NAMES and is_directory:
        return True
    if sensitive_relative_path(relative):
        return True
    return matches_ignore_pattern(relative, is_directory=is_directory, rules=ignore_rules)


def structural_files(
    root: Path, *, exact_exclusions: set[PurePosixPath] | None = None
) -> tuple[list[tuple[PurePosixPath, Path]], int]:
    exact_exclusions = exact_exclusions or set()
    discovered: list[tuple[PurePosixPath, Path]] = []
    excluded_count = 0
    git_executable = git_ignore_executable(root)
    traversed_files = 0
    traversed_directories = 0
    traversed_path_bytes = 0
    pending: list[
        tuple[
            Path,
            tuple[IgnoreRule, ...],
            tuple[tuple[PurePosixPath, bytes], ...],
        ]
    ] = [(root, (), ())]

    while pending:
        current_path, parent_rules, parent_gitignore_files = pending.pop()
        current_metadata = read_ignore_metadata(current_path, root)
        current_rules = parent_rules + load_ignore_rules(
            current_path,
            root,
            metadata=current_metadata,
        )
        current_gitignore_files = parent_gitignore_files + tuple(
            item for item in current_metadata if item[0].name == ".gitignore"
        )
        bounded_entries: list[tuple[PurePosixPath, str, bool]] = []
        try:
            with os.scandir(current_path) as iterator:
                for entry in iterator:
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        raise AtlasError(
                            "safe inventory traversal could not classify a directory entry"
                        ) from None
                    relative = PurePosixPath(
                        (current_path / entry.name).relative_to(root).as_posix()
                    )
                    if is_directory:
                        traversed_directories += 1
                        if traversed_directories > MAX_INVENTORY_DIRECTORIES:
                            raise AtlasError(
                                "safe inventory traversal exceeds the directory-count limit"
                            )
                    else:
                        traversed_files += 1
                        if traversed_files > MAX_INVENTORY_FILES:
                            raise AtlasError(
                                "safe inventory traversal exceeds the file-count limit"
                            )
                    if len(relative.parts) > MAX_INVENTORY_DEPTH:
                        raise AtlasError("safe inventory traversal exceeds the depth limit")
                    traversed_path_bytes += len(relative.as_posix().encode("utf-8"))
                    if traversed_path_bytes > MAX_INVENTORY_PATH_BYTES:
                        raise AtlasError(
                            "safe inventory traversal exceeds the path-byte limit"
                        )
                    bounded_entries.append((relative, entry.name, is_directory))
        except AtlasError:
            raise
        except OSError:
            raise AtlasError(
                "safe inventory traversal could not inspect a directory"
            ) from None

        bounded_entries.sort(key=lambda item: item[0].as_posix())
        directory_entries = [item for item in bounded_entries if item[2]]
        file_entries = [item for item in bounded_entries if not item[2]]
        candidates = tuple(relative for relative, _name, _is_directory in bounded_entries)
        git_ignored = git_ignored_paths(
            root,
            git_executable,
            candidates,
            directory_candidates=frozenset(
                relative for relative, _name, is_directory in bounded_entries if is_directory
            ),
            gitignore_files=current_gitignore_files,
        )
        routed_atlas_directory = {"ATLAS_INDEX.md", "LIVE_HANDOFF.md"}.issubset(
            {name for _relative, name, _is_directory in file_entries}
        )

        safe_directories: list[
            tuple[
                Path,
                tuple[IgnoreRule, ...],
                tuple[tuple[PurePosixPath, bytes], ...],
            ]
        ] = []
        for relative, name, _is_directory in directory_entries:
            path = current_path / name
            if (
                relative in git_ignored
                or path.is_symlink()
                or should_exclude(relative, is_directory=True, ignore_rules=current_rules)
            ):
                excluded_count += 1
                continue
            try:
                details = path.lstat()
            except OSError:
                raise AtlasError(
                    "safe inventory traversal could not revalidate a directory entry"
                ) from None
            if not stat.S_ISDIR(details.st_mode):
                raise AtlasError(
                    "safe inventory directory entry changed during traversal"
                )
            safe_directories.append((path, current_rules, current_gitignore_files))
        pending.extend(reversed(safe_directories))

        for relative, name, _is_directory in file_entries:
            path = current_path / name
            generated_here = name in GENERATED_FILE_NAMES and (
                routed_atlas_directory
                or (current_path == root and name in {"PROJECT_ATLAS.md", "SOURCE_SNAPSHOT.json"})
            )
            if (
                relative in exact_exclusions
                or relative in git_ignored
                or generated_here
                or path.is_symlink()
                or should_exclude(relative, is_directory=False, ignore_rules=current_rules)
            ):
                excluded_count += 1
                continue
            try:
                details = path.lstat()
            except OSError:
                excluded_count += 1
                continue
            if not stat.S_ISREG(details.st_mode):
                excluded_count += 1
                continue
            if details.st_nlink != 1:
                raise AtlasError(
                    f"safe inventory rejects hardlinked project source: {relative.as_posix()}"
                )
            discovered.append((relative, path))
    discovered.sort(key=lambda item: item[0].as_posix())
    return discovered, excluded_count


def build_safe_inventory(
    root: Path, *, exact_exclusions: set[PurePosixPath] | None = None
) -> SafeInventory:
    resolved_root = canonical_directory_path(root)
    files, excluded_count = structural_files(resolved_root, exact_exclusions=exact_exclusions)
    ordered = tuple(relative for relative, _path in files)
    root_descriptor = open_directory_descriptor(resolved_root)
    try:
        root_status = os.fstat(root_descriptor)
    finally:
        os.close(root_descriptor)
    return SafeInventory(
        root=resolved_root,
        members=frozenset(ordered),
        ordered=ordered,
        excluded_count=excluded_count,
        root_device=root_status.st_dev,
        root_inode=root_status.st_ino,
    )


def inventory_file_size(inventory: SafeInventory, relative: PurePosixPath) -> int:
    if relative not in inventory.members:
        raise AtlasError("project source is outside the safe inventory")
    descriptor = _open_relative_descriptor(
        inventory.root,
        relative,
        expected_root_identity=(inventory.root_device, inventory.root_inode),
    )
    try:
        return os.fstat(descriptor).st_size
    finally:
        os.close(descriptor)


def safe_text(inventory: SafeInventory, relative: PurePosixPath) -> str:
    name = relative.name.lower()
    if relative.suffix.lower() not in TEXT_SUFFIXES and name not in {"dockerfile", "makefile"}:
        return ""
    raw = read_inventory_bytes(
        inventory, relative, maximum_bytes=MAX_CLASSIFICATION_BYTES
    )
    if b"\0" in raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


MODE_SIGNAL_SUPPORT_PARTS = frozenset(
    {
        ".github",
        "doc",
        "docs",
        "example",
        "examples",
        "fixture",
        "fixtures",
        "oracle",
        "oracles",
        "sample",
        "samples",
        "template",
        "templates",
        "test",
        "tests",
    }
)

ROOT_MODE_SIGNAL_SUPPORT_NAME = re.compile(
    r"^(?:conftest\.py|test_.+\.[^.]+|.+_test\.[^.]+|.+_spec\.[^.]+|.+\.(?:spec|test)\.[^.]+)$",
    re.IGNORECASE,
)

DECLARATION_CONFIG_SUFFIXES = frozenset(
    {".cfg", ".conf", ".ini", ".json", ".toml", ".yaml", ".yml"}
)

EXPLICIT_CONFIG_SIGNAL_PHRASES = {
    "automatic_decisions": "automatic approval decisions",
    "authority_override": "operator override",
    "critical": "mission-critical system",
    "financial_data": "financial data",
    "legacy": "legacy system",
    "operator_override": "operator override",
    "production": "production service",
    "production_exposure": "production service",
    "sensitive_data": "personal data",
    "shared_state": "services share state",
}

TRUE_CONFIG_SIGNAL_VALUES = frozenset(
    {"1", "critical", "enabled", "high", "on", "production", "true", "yes"}
)


def is_product_signal_path(relative: PurePosixPath) -> bool:
    """Keep support contours in inventory without treating them as product topology."""

    if any(part.lower() in MODE_SIGNAL_SUPPORT_PARTS for part in relative.parts[:-1]):
        return False
    if len(relative.parts) == 1 and ROOT_MODE_SIGNAL_SUPPORT_NAME.fullmatch(relative.name):
        return False
    return True


def canonical_core_copy(relative: PurePosixPath) -> PurePosixPath | None:
    """Resolve the canonical counterpart of a packaged adapter skill member."""

    parts = relative.parts
    if len(parts) < 5 or parts[0].lower() != "adapters" or parts[2].lower() != "skills":
        return None
    return PurePosixPath("core", "skill", parts[3], *parts[4:])


def is_packaged_core_copy(
    relative: PurePosixPath,
    texts: dict[str, str],
) -> bool:
    """Collapse only a proven byte-equivalent canonical skill package copy."""

    canonical = canonical_core_copy(relative)
    if canonical is None:
        return False
    packaged_text = texts.get(relative.as_posix(), "")
    canonical_text = texts.get(canonical.as_posix(), "")
    return bool(packaged_text and packaged_text == canonical_text)


def declaration_paragraphs(text: str) -> tuple[str, ...]:
    """Split one bounded declaration source without joining separate evidence units."""

    return tuple(
        paragraph.strip().lower()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    )


def root_readme_evidence_units(texts: dict[str, str]) -> tuple[str, ...]:
    """Return bounded root README declarations without merging their provenance."""

    units: list[str] = []
    for relative_text, text in texts.items():
        relative = PurePosixPath(relative_text)
        if len(relative.parts) != 1 or not relative.name.lower().startswith("readme"):
            continue
        units.extend(declaration_paragraphs(text))
    return tuple(units)


def source_preamble_statement_end(suffix: str, text: str, position: int) -> int | None:
    """Consume only language framing that can precede a declaration comment."""

    line_end = r"[ \t]*(?:\r?\n|$)"
    patterns: tuple[str, ...]
    if suffix == ".java":
        patterns = (
            rf"(?:package|import(?:[ \t]+static)?)[ \t]+[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$*][A-Za-z0-9_$*]*)*;{line_end}",
        )
    elif suffix in {".kt", ".kts"}:
        patterns = (
            rf"(?:package|import)[ \t]+[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_*][A-Za-z0-9_]*)*(?:[ \t]+as[ \t]+[A-Za-z_][A-Za-z0-9_]*)?;?{line_end}",
        )
    elif suffix == ".cs":
        patterns = (
            rf"(?:global[ \t]+)?using[ \t]+(?:static[ \t]+)?[^;\r\n]+;{line_end}",
            rf"extern[ \t]+alias[ \t]+[A-Za-z_][A-Za-z0-9_]*;{line_end}",
            rf"namespace[ \t]+[A-Za-z_][A-Za-z0-9_.]*[ \t]*(?:;|\{{){line_end}",
            rf"#nullable\b[^\r\n]*{line_end}",
        )
    elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}:
        patterns = (
            rf"import\b[^\r\n]*;?{line_end}",
            rf"export[ \t]+(?:type[ \t]+)?(?:\*|\{{)[^\r\n]*;?{line_end}",
        )
    elif suffix in {".c", ".cc", ".cpp", ".h", ".hpp"}:
        patterns = (
            rf"#[ \t]*(?:include|include_next|pragma)\b[^\r\n]*{line_end}",
        )
    elif suffix == ".go":
        patterns = (
            rf"package[ \t]+[A-Za-z_][A-Za-z0-9_]*{line_end}",
            rf"import[ \t]+(?:[A-Za-z_][A-Za-z0-9_]*[ \t]+)?\"[^\"\r\n]+\"{line_end}",
            r"import[ \t]*\([\s\S]*?\)[ \t]*(?:\r?\n|$)",
        )
    elif suffix in {".scala", ".swift"}:
        patterns = (
            rf"(?:package|import)\b[^\r\n]*{line_end}",
        )
    elif suffix == ".rs":
        patterns = (
            rf"#!\[[^\r\n]*\]{line_end}",
            rf"(?:use|extern[ \t]+crate|mod)\b[^;\r\n]*;{line_end}",
        )
    elif suffix == ".php":
        patterns = (
            rf"<\?php{line_end}",
            rf"(?:declare|namespace|use)\b[^;\r\n]*;{line_end}",
        )
    else:
        return None
    for pattern in patterns:
        match = re.match(pattern, text[position:])
        if match is not None:
            return position + match.end()
    return None


def leading_source_comment_units(relative: PurePosixPath, text: str) -> tuple[str, ...]:
    """Read only leading comment blocks, never arbitrary source literals or regex bodies."""

    text = text.removeprefix("\ufeff")
    if text.startswith("#!"):
        _shebang, separator, remainder = text.partition("\n")
        text = remainder if separator else ""
    suffix = relative.suffix.lower()
    if suffix in {".py", ".rb", ".sh"}:
        markers = ("#",)
    elif suffix in {
        ".c",
        ".cc",
        ".cjs",
        ".cpp",
        ".cs",
        ".cts",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".mjs",
        ".mts",
        ".php",
        ".rs",
        ".scala",
        ".swift",
        ".ts",
        ".tsx",
    }:
        blocks: list[str] = []
        position = 0
        while position < len(text):
            while position < len(text) and text[position].isspace():
                position += 1
            if text.startswith("//", position):
                lines: list[str] = []
                while text.startswith("//", position):
                    line_end = text.find("\n", position + 2)
                    if line_end < 0:
                        line_end = len(text)
                    lines.append(text[position + 2 : line_end].strip())
                    position = min(line_end + 1, len(text))
                    while position < len(text) and text[position] in {" ", "\t"}:
                        position += 1
                blocks.append("\n".join(lines))
                continue
            if text.startswith("/*", position):
                block_end = text.find("*/", position + 2)
                if block_end < 0:
                    break
                raw_block = text[position + 2 : block_end]
                normalized_lines = [
                    re.sub(r"^\s*\*?\s?", "", line).rstrip()
                    for line in raw_block.splitlines()
                ]
                blocks.append("\n".join(normalized_lines).strip())
                position = block_end + 2
                continue
            preamble_end = source_preamble_statement_end(suffix, text, position)
            if preamble_end is not None:
                position = preamble_end
                continue
            break
        return declaration_paragraphs("\n\n".join(blocks))
    elif suffix == ".sql":
        markers = ("--",)
    else:
        return ()

    comment_lines: list[str] = []
    started = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if started:
                comment_lines.append("")
            continue
        marker = next((candidate for candidate in markers if stripped.startswith(candidate)), None)
        if marker is None:
            break
        started = True
        comment_lines.append(stripped[len(marker) :].strip())
    return declaration_paragraphs("\n".join(comment_lines))


def python_docstring_evidence_units(text: str) -> tuple[str, ...]:
    """Extract semantic declarations from Python docstrings, excluding other literals."""

    try:
        tree = ast.parse(text.removeprefix("\ufeff"))
    except (SyntaxError, ValueError):
        return ()
    units: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node, clean=False)
        if docstring:
            units.extend(declaration_paragraphs(docstring))
    return tuple(units)


def explicit_config_evidence_units(relative: PurePosixPath, text: str) -> tuple[str, ...]:
    """Normalize only allowlisted configuration keys into one file-scoped declaration unit."""

    if relative.suffix.lower() not in DECLARATION_CONFIG_SUFFIXES:
        return ()
    text = text.removeprefix("\ufeff")
    phrases: list[str] = []
    assignment = re.compile(
        r"^\s*[\"']?([A-Za-z][A-Za-z0-9_.-]*)[\"']?\s*[:=]\s*(.*?)\s*,?\s*$"
    )
    for raw_line in text.splitlines():
        match = assignment.match(raw_line)
        if match is None:
            continue
        key = match.group(1).lower().replace("-", "_").rsplit(".", 1)[-1]
        raw_value = match.group(2).split("#", 1)[0].strip().rstrip(",").strip()
        value = raw_value.strip("\"'").lower()
        phrase = EXPLICIT_CONFIG_SIGNAL_PHRASES.get(key)
        if phrase is not None and value in TRUE_CONFIG_SIGNAL_VALUES:
            phrases.append(phrase)
            continue
        if key in {"authority", "decision_authority"} and re.search(
            r"\b(?:human|manual|operator|override)\b", value
        ):
            phrases.append("operator override")
        elif key == "cost_of_error" and value == "critical":
            phrases.append("mission-critical system")
    if not phrases:
        return ()
    return (". ".join(dict.fromkeys(phrases)).lower(),)


def project_declaration_evidence_units(texts: dict[str, str]) -> tuple[str, ...]:
    """Collect bounded product declarations while preserving their source units."""

    units = list(root_readme_evidence_units(texts))
    for relative_text, text in texts.items():
        if not text:
            continue
        relative = PurePosixPath(relative_text)
        if not is_product_signal_path(relative) or is_packaged_core_copy(relative, texts):
            continue
        if relative.suffix.lower() == ".py":
            units.extend(python_docstring_evidence_units(text))
        if relative.suffix.lower() in SOURCE_SUFFIXES:
            units.extend(leading_source_comment_units(relative, text))
        units.extend(explicit_config_evidence_units(relative, text))
    return tuple(units)


def build_inventory(
    root: Path,
    *,
    exact_exclusions: set[PurePosixPath] | None = None,
    supplied: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inventory = build_safe_inventory(root, exact_exclusions=exact_exclusions)
    entrypoints: list[str] = []
    state_writers: list[str] = []
    authority: list[str] = []
    retries: list[str] = []
    unknowns: list[str] = []
    texts: dict[str, str] = {}
    classification_files = 0
    classification_bytes = 0
    classification_skipped = 0

    for relative in inventory.ordered:
        relative_text = relative.as_posix()
        name = relative.name.lower()
        is_text_candidate = (
            relative.suffix.lower() in TEXT_SUFFIXES
            or name in {"dockerfile", "makefile"}
        )
        text = ""
        if is_text_candidate:
            if classification_files >= MAX_CLASSIFICATION_FILES:
                classification_skipped += 1
            else:
                try:
                    size = inventory_file_size(inventory, relative)
                except AtlasError:
                    classification_skipped += 1
                else:
                    remaining = MAX_CLASSIFICATION_TOTAL_BYTES - classification_bytes
                    if size > MAX_CLASSIFICATION_BYTES or size > remaining:
                        classification_skipped += 1
                    else:
                        text = safe_text(inventory, relative)
                        classification_files += 1
                        classification_bytes += size
        texts[relative_text] = text

    product_signal_files: list[str] = []
    for relative in inventory.ordered:
        relative_text = relative.as_posix()
        text = texts[relative_text]
        lowered = text.lower()
        stem = relative.stem.lower()
        suffix = relative.suffix.lower()
        is_source = suffix in SOURCE_SUFFIXES

        if re.search(r"\bunknown\b", lowered):
            unknowns.append(relative_text)

        if not is_product_signal_path(relative) or is_packaged_core_copy(relative, texts):
            continue
        product_signal_files.append(relative_text)

        if is_source and (
            relative.name.lower() in {"__main__.py", "main.py", "main.go", "program.cs"}
            or stem in {"api", "app", "cli", "cron", "gateway", "server", "webhook", "worker"}
            or re.search(r"\bif\s+__name__\s*==\s*[\"']__main__[\"']", text)
        ):
            entrypoints.append(relative_text)

        if is_source and (
            any(marker in stem for marker in ("ledger", "repository", "state", "store", "writer"))
            or re.search(r"\bdef\s+(?:delete|persist|record|save|update|upsert|write)_", lowered)
            or re.search(r"\b(?:insert\s+into|update\s+\w+\s+set|delete\s+from)\b", lowered)
        ):
            state_writers.append(relative_text)

        if is_source and (
            any(marker in stem for marker in ("authority", "permission", "policy"))
            or re.search(r"\bdef\s+(?:authorize|choose|operator_allows|permit|resolve)_", lowered)
            or "authority boundary" in lowered
        ):
            authority.append(relative_text)

        if is_source and ("retry" in stem or re.search(r"\bretr(?:y|ies|ied|ying)\b", lowered)):
            retries.append(relative_text)

    relative_files = [relative.as_posix() for relative in inventory.ordered]
    mode_result = choose_mode(
        relative_files,
        texts,
        entrypoints,
        state_writers,
        authority,
        retries,
        supplied,
        signal_files=product_signal_files,
        semantic_units=project_declaration_evidence_units(texts),
    )
    classification_limited = classification_skipped > 0
    if classification_limited:
        mode_result["mode"] = "FORENSIC"
        mode_result["reasons"] = [
            "classification budget reached; safe-inventory members remain content-uninspected",
            *mode_result["reasons"],
        ]
    mode_result["signals"]["classification_limited"] = classification_limited
    return {
        "version": 1,
        "mode": mode_result["mode"],
        "files": relative_files,
        "file_count": len(relative_files),
        "excluded_count": inventory.excluded_count,
        "entrypoints": entrypoints,
        "state_writers": state_writers,
        "authority": authority,
        "retries": retries,
        "unknowns": unknowns,
        "classification": {
            "files_inspected": classification_files,
            "bytes_inspected": classification_bytes,
            "files_skipped": classification_skipped,
            "file_budget": MAX_CLASSIFICATION_FILES,
            "byte_budget": MAX_CLASSIFICATION_TOTAL_BYTES,
            "per_file_byte_budget": MAX_CLASSIFICATION_BYTES,
            "limited": classification_limited,
        },
        "selected_by": mode_result["selected_by"],
        "reasons": mode_result["reasons"],
        "signals": mode_result["signals"],
    }


def choose_mode(
    files: Sequence[str],
    texts: dict[str, str],
    entrypoints: Sequence[str],
    state_writers: Sequence[str],
    authority: Sequence[str],
    retries: Sequence[str],
    supplied: dict[str, Any] | None = None,
    *,
    signal_files: Sequence[str] | None = None,
    semantic_units: Sequence[str] | None = None,
) -> dict[str, Any]:
    supplied = supplied or {}
    structural_files = tuple(signal_files) if signal_files is not None else tuple(files)
    structural_file_count = len(structural_files)
    units = (
        tuple(unit.lower() for unit in semantic_units)
        if semantic_units is not None
        else tuple(text.lower() for text in texts.values() if text)
    )
    path_corpus = "\n".join(structural_files).lower()
    source_count = sum(
        1
        for item in structural_files
        if PurePosixPath(item).suffix.lower() in SOURCE_SUFFIXES
    )
    manifest_present = any(
        PurePosixPath(item).name.lower()
        in {"cargo.toml", "go.mod", "package.json", "pom.xml", "pyproject.toml", "build.gradle"}
        for item in structural_files
    )

    production_critical_pattern = re.compile(
        r"\bproduction[- ]critical\b|\bmission[- ]critical\b"
    )
    production_pattern = re.compile(
        r"\bdeployed\s+(?:in|to)\s+production\b|\bproduction\s+(?:service|system)\b"
    )
    sensitive_pattern = re.compile(
        r"\b(?:personal data|personally identifiable|health data|pii)\b"
    )
    financial_pattern = re.compile(
        r"\b(?:financial\s+(?:data|records?|transactions?|settlements?)|payments?|settlements?)\b"
    )
    legacy_pattern = re.compile(
        r"\b(?:legacy|deprecated|obsolete)\s+"
        r"(?:implementations?|systems?|services?|paths?|writers?|fixtures?|meshes?)\b"
    )
    automatic_action_pattern = re.compile(
        r"\b(?:automatic|automated)(?:\s+[a-z][a-z0-9_-]*){0,3}\s+"
        r"(?:decisions?|actions?|approvals?|rejections?|authorizations?|selections?|"
        r"routes?|routing|transitions?|updates?|writes?|state)\b|"
        r"\b(?:automatically|automation)\s+"
        r"(?:decides?|approves?|rejects?|authorizes?|selects?|routes?|transitions?|updates?|writes?)\b|"
        r"\bautomatically\s+(?:(?:makes?|performs?|executes?|takes?)\s+)?"
        r"(?:[a-z][a-z0-9_-]*\s+){0,3}"
        r"(?:decisions?|actions?|approvals?|rejections?|authorizations?|selections?|"
        r"routes?|routing|transitions?|updates?|writes?)\b"
    )
    authority_action_pattern = re.compile(
        r"\b(?:override(?:s|d)?|overridden|overriding|outrank(?:s|ed|ing)?|"
        r"authoriz(?:e|es|ed|ing)|permit(?:s|ted|ting)?|last word|final say|"
        r"final authority\s+(?:over|to)|authority\s+over)\b"
    )
    shared_state_pattern = re.compile(
        r"\b(?:runtimes?|workers?|services?|writers?)\b.{0,80}"
        r"\bshar(?:e|es|ed|ing)\b.{0,80}\b(?:state|store|database|ledger)\b|"
        r"\bshared\s+(?:state|store|database|ledger)\b"
    )

    unit_signals = tuple(
        {
            "production_critical": bool(production_critical_pattern.search(unit)),
            "production": bool(production_pattern.search(unit)),
            "sensitive": bool(sensitive_pattern.search(unit)),
            "financial": bool(financial_pattern.search(unit)),
            "legacy": bool(legacy_pattern.search(unit)),
            "automatic": bool(
                automatic_action_pattern.search(unit)
                and authority_action_pattern.search(unit)
            ),
            "shared_state": bool(shared_state_pattern.search(unit)),
        }
        for unit in units
    )
    production_critical = any(item["production_critical"] for item in unit_signals)
    production = bool(
        supplied.get("production")
        or production_critical
        or any(item["production"] for item in unit_signals)
    )
    critical = bool(
        supplied.get("critical")
        or production_critical
        or supplied.get("cost_of_error") == "critical"
    )
    sensitive = bool(
        supplied.get("sensitive_data")
        or any(item["sensitive"] for item in unit_signals)
    )
    financial = bool(
        supplied.get("financial_data")
        or any(item["financial"] for item in unit_signals)
    )
    legacy = bool(
        supplied.get("legacy")
        or supplied.get("legacy_implementations", 0) >= 2
        or "legacy" in path_corpus
        or any(item["legacy"] for item in unit_signals)
    )
    automatic_decisions = bool(
        supplied.get("automatic_decisions")
        or any(item["automatic"] for item in unit_signals)
    )
    explicitly_counted_shared_state = bool(
        (supplied.get("runtime_count") or 0) >= 4
        and (supplied.get("store_count") or 0) >= 1
    )
    shared_state = bool(
        explicitly_counted_shared_state
        or any(item["shared_state"] for item in unit_signals)
    )
    production_sensitive_unit = any(
        (item["production"] or item["production_critical"])
        and (item["financial"] or item["sensitive"])
        for item in unit_signals
    )
    legacy_high_consequence_unit = any(
        item["legacy"]
        and (
            item["production"]
            or item["production_critical"]
            or item["financial"]
            or item["sensitive"]
        )
        for item in unit_signals
    )
    supplied_production_sensitive = bool(
        supplied.get("production")
        and (supplied.get("financial_data") or supplied.get("sensitive_data"))
    )
    supplied_legacy_high_consequence = bool(
        (supplied.get("legacy") or supplied.get("legacy_implementations", 0) >= 2)
        and (
            supplied.get("production")
            or supplied.get("critical")
            or supplied.get("financial_data")
            or supplied.get("sensitive_data")
            or supplied.get("cost_of_error") == "critical"
        )
    )
    runtime_count = max(len(entrypoints), supplied.get("runtime_count") or 0)
    store_count = max(len(state_writers), supplied.get("store_count") or 0)
    authority_complexity = supplied.get("authority_complexity") or (
        "high" if len(authority) >= 3 else "medium" if authority else "low"
    )
    cost_of_error = supplied.get("cost_of_error") or (
        "critical" if critical else "high" if financial or sensitive else "medium" if production else "low"
    )
    long_lived = supplied.get("expected_lifetime") == "long"
    team_size = supplied.get("team_size") or 0

    signals: dict[str, Any] = {
        "file_count": len(files),
        "structural_file_count": structural_file_count,
        "source_file_count": source_count,
        "runtime_count": runtime_count,
        "state_writer_count": store_count,
        "authority_boundary_count": len(authority),
        "retry_path_count": len(retries),
        "production": production,
        "critical": critical,
        "sensitive_data": sensitive,
        "financial_data": financial,
        "legacy_overlap": legacy,
        "automatic_decisions": automatic_decisions,
        "shared_state": shared_state,
        "authority_complexity": authority_complexity,
        "cost_of_error": cost_of_error,
        "long_lived": long_lived,
        "team_size": team_size,
    }

    forensic_reasons: list[str] = []
    if critical:
        forensic_reasons.append("critical cost of error")
    if production_sensitive_unit or supplied_production_sensitive:
        forensic_reasons.append("production handles sensitive or financial data")
    if legacy_high_consequence_unit or supplied_legacy_high_consequence:
        forensic_reasons.append("legacy overlap raises a high-consequence boundary")
    if runtime_count >= 4 and store_count >= 1 and shared_state:
        forensic_reasons.append("four or more runtimes share state through at least one writer")
    if authority_complexity == "high" and (automatic_decisions or production):
        forensic_reasons.append("complex authority controls automated or production behavior")
    if structural_file_count >= 750:
        forensic_reasons.append("large structural surface")

    if forensic_reasons:
        mode = "FORENSIC"
        reasons = forensic_reasons
    else:
        standard_reasons: list[str] = []
        if runtime_count >= 2:
            standard_reasons.append("multiple runtime entry points")
        if store_count >= 2:
            standard_reasons.append("multiple state writers")
        if production or sensitive or financial or automatic_decisions:
            standard_reasons.append("risk signal requires routed documentation")
        if authority_complexity in {"medium", "high"} and runtime_count >= 2:
            standard_reasons.append("authority crosses runtime boundaries")
        if manifest_present and source_count >= 4:
            standard_reasons.append("active application or library structure")
        if long_lived or team_size >= 4:
            standard_reasons.append("long-lived or multi-maintainer project")
        if structural_file_count >= 30:
            standard_reasons.append("non-trivial structural surface")
        if standard_reasons:
            mode = "STANDARD"
            reasons = standard_reasons
        else:
            mode = "QUICK"
            reasons = ["small, single-runtime, low-risk structural surface"]

    return {"mode": mode, "selected_by": "signals", "reasons": reasons, "signals": signals}


def _atomic_rename_with_flags(
    directory_descriptor: int, source: str, destination: str, flags: int
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
    elif sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
    else:
        function = None
    if function is None:
        raise OSError(errno.ENOTSUP, "atomic rename flags are unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        directory_descriptor,
        source_bytes,
        directory_descriptor,
        destination_bytes,
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise FileExistsError(error_number, "destination exists")
        raise OSError(error_number, "atomic rename failed")


def atomic_rename_no_replace(
    directory_descriptor: int, source: str, destination: str
) -> None:
    flag = 1 if sys.platform.startswith("linux") else 4
    _atomic_rename_with_flags(directory_descriptor, source, destination, flag)


def atomic_exchange_names(
    directory_descriptor: int, source: str, destination: str
) -> None:
    _atomic_rename_with_flags(directory_descriptor, source, destination, 2)


def serialize_json_output(payload: dict[str, Any], *, indent: int | None = None) -> str:
    encoded = json.dumps(
        payload,
        indent=indent,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"
    data = encoded.encode("utf-8")
    if len(data) > MAX_JSON_OUTPUT_BYTES:
        raise AtlasError("JSON output exceeds the byte limit")
    return encoded


def write_json(path: Path, payload: dict[str, Any]) -> None:
    expanded = lexical_absolute(path)
    if expanded.name in {"", ".", ".."}:
        raise AtlasError("output path has no regular filename")
    encoded = serialize_json_output(payload, indent=2)
    data = encoded.encode("utf-8")
    _parent, parent_descriptor = open_verified_directory(
        expanded.parent, "output parent"
    )
    temporary_name = f".{expanded.name}.atlas-new-{secrets.token_hex(8)}"
    file_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    temporary_created = False
    temporary_identity: tuple[int, int] | None = None
    target_descriptor: int | None = None
    initial_target_identity: tuple[int, int] | None = None
    try:
        try:
            target_status = os.stat(
                expanded.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            target_status = None
        except OSError:
            raise AtlasError("output target cannot be inspected safely") from None
        if target_status is not None:
            if not stat.S_ISREG(target_status.st_mode) or target_status.st_nlink != 1:
                raise AtlasError("output target is not a single-link regular file")
            try:
                target_descriptor = os.open(
                    expanded.name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_descriptor,
                )
            except OSError:
                raise AtlasError("output target cannot be pinned safely") from None
            pinned_target = os.fstat(target_descriptor)
            initial_target_identity = (pinned_target.st_dev, pinned_target.st_ino)
            if (
                initial_target_identity != (target_status.st_dev, target_status.st_ino)
                or pinned_target.st_nlink != 1
            ):
                raise AtlasError("output target changed before safe replacement")
        try:
            temporary_descriptor = os.open(
                temporary_name, file_flags, 0o600, dir_fd=parent_descriptor
            )
        except FileExistsError:
            raise AtlasError("temporary output already exists") from None
        except OSError:
            raise AtlasError("temporary output cannot be created safely") from None
        temporary_created = True
        try:
            offset = 0
            while offset < len(data):
                offset += os.write(temporary_descriptor, data[offset:])
            os.fsync(temporary_descriptor)
            temporary_status = os.fstat(temporary_descriptor)
            if not stat.S_ISREG(temporary_status.st_mode) or temporary_status.st_nlink != 1:
                raise AtlasError("temporary output lost its single-link identity")
            temporary_identity = (temporary_status.st_dev, temporary_status.st_ino)
        finally:
            os.close(temporary_descriptor)

        if initial_target_identity is None:
            try:
                atomic_rename_no_replace(
                    parent_descriptor, temporary_name, expanded.name
                )
            except FileExistsError:
                raise AtlasError("output target appeared during no-clobber commit") from None
            except OSError:
                raise AtlasError("output cannot be committed with no-clobber semantics") from None
            temporary_created = False
            committed = os.stat(
                expanded.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if (
                temporary_identity != (committed.st_dev, committed.st_ino)
                or committed.st_nlink != 1
            ):
                raise AtlasError("output identity changed during no-clobber commit")
        else:
            try:
                atomic_exchange_names(
                    parent_descriptor, temporary_name, expanded.name
                )
            except OSError:
                raise AtlasError("output cannot enter identity-safe quarantine") from None
            committed = os.stat(
                expanded.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            quarantined = os.stat(
                temporary_name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if (
                temporary_identity != (committed.st_dev, committed.st_ino)
                or initial_target_identity != (quarantined.st_dev, quarantined.st_ino)
                or committed.st_nlink != 1
                or quarantined.st_nlink != 1
            ):
                try:
                    atomic_exchange_names(
                        parent_descriptor, temporary_name, expanded.name
                    )
                except OSError:
                    raise AtlasError(
                        "output race left the prior target quarantined; manual recovery is required"
                    ) from None
                restored = os.stat(
                    expanded.name, dir_fd=parent_descriptor, follow_symlinks=False
                )
                if (restored.st_dev, restored.st_ino) == initial_target_identity:
                    raise AtlasError("output target changed during replacement and was restored")
                raise AtlasError("output target changed during replacement; concurrent identity restored")
            os.unlink(temporary_name, dir_fd=parent_descriptor)
            temporary_created = False
        os.fsync(parent_descriptor)
    finally:
        if target_descriptor is not None:
            os.close(target_descriptor)
        if temporary_created:
            try:
                current = os.stat(
                    temporary_name, dir_fd=parent_descriptor, follow_symlinks=False
                )
                if temporary_identity == (current.st_dev, current.st_ino):
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
            except OSError:
                pass
        os.close(parent_descriptor)


def open_or_create_output_directory(output_root: Path) -> tuple[Path, int]:
    absolute = lexical_absolute(output_root)
    if absolute.name in {"", ".", ".."}:
        raise AtlasError("output directory has no safe final component")
    parent, parent_descriptor = open_verified_directory(
        absolute.parent, "output parent"
    )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        try:
            before = os.stat(
                absolute.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            try:
                os.mkdir(absolute.name, 0o755, dir_fd=parent_descriptor)
            except OSError:
                raise AtlasError("output directory cannot be created safely") from None
            before = os.stat(
                absolute.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        except OSError:
            raise AtlasError("output directory cannot be inspected safely") from None
        if not stat.S_ISDIR(before.st_mode):
            raise AtlasError("output path exists and is not a regular directory")
        try:
            descriptor = os.open(
                absolute.name, directory_flags, dir_fd=parent_descriptor
            )
        except OSError:
            raise AtlasError("output directory cannot be opened safely") from None
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            os.close(descriptor)
            raise AtlasError("output directory changed during safe open")
        return parent / absolute.name, descriptor
    finally:
        os.close(parent_descriptor)


def select_mode_command(args: argparse.Namespace) -> int:
    project = project_from_args(args, required=args.mode is None)
    supplied = {
        "production": args.production,
        "critical": args.critical,
        "sensitive_data": args.sensitive_data,
        "financial_data": args.financial_data,
        "automatic_decisions": args.automatic_decisions,
        "authority_complexity": args.authority_complexity,
        "runtime_count": args.runtime_count,
        "store_count": args.store_count,
        "legacy": args.legacy,
        "legacy_implementations": args.legacy_implementations,
        "team_size": args.team_size,
        "expected_lifetime": args.expected_lifetime,
        "cost_of_error": args.cost_of_error,
    }
    automatic: dict[str, Any] | None = None
    if project is not None:
        assert project is not None
        inventory = build_inventory(project, supplied=supplied)
        automatic = {
            key: inventory[key] for key in ("mode", "selected_by", "reasons", "signals")
        }
    else:
        automatic = choose_mode([], {}, [], [], [], [], supplied)
    if args.mode is not None:
        payload = {
            "mode": args.mode,
            "selected_by": "explicit",
            "reasons": ["explicit mode override"],
            "signals": automatic["signals"] if automatic is not None else {},
        }
        if automatic is not None:
            payload["recommended_mode"] = automatic["mode"]
            if MODES.index(args.mode) < MODES.index(automatic["mode"]):
                payload["coverage_warning"] = "explicit mode is shallower than the signal-based recommendation"
    else:
        assert automatic is not None
        payload = automatic
    sys.stdout.write(serialize_json_output(payload))
    return 0


def inventory_command(args: argparse.Namespace) -> int:
    project = project_from_args(args)
    assert project is not None
    exact_exclusions: set[PurePosixPath] = set()
    if args.output is not None:
        try:
            relative_output = args.output.expanduser().resolve().relative_to(project)
        except ValueError:
            pass
        else:
            exact_exclusions.add(PurePosixPath(relative_output.as_posix()))
    payload = build_inventory(project, exact_exclusions=exact_exclusions)
    if args.output is None:
        sys.stdout.write(serialize_json_output(payload, indent=2))
    else:
        write_json(args.output.expanduser(), payload)
    return 0


def template_root() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def default_output_root(project: Path, mode: str) -> Path:
    if mode == "QUICK":
        return project
    docs = project / "docs"
    return docs / "project-atlas" if docs.is_dir() and not docs.is_symlink() else project / "project-atlas"


def init_command(args: argparse.Namespace) -> int:
    project = project_from_args(args)
    assert project is not None
    inventory = build_inventory(project)
    mode = args.mode or inventory["mode"]
    output_root = (args.output.expanduser() if args.output is not None else default_output_root(project, mode))
    output_root, output_descriptor = open_or_create_output_directory(output_root)

    source_root = template_root() / mode.lower()
    if not source_root.is_dir():
        os.close(output_descriptor)
        raise AtlasError(f"templates are missing for mode {mode}")
    created: list[str] = []
    preserved: list[str] = []
    create_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for name in MODE_FILES[mode]:
            source = source_root / name
            if source.is_symlink() or not source.is_file():
                raise AtlasError(f"required template is missing: {mode}/{name}")
            try:
                existing = os.stat(name, dir_fd=output_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            except OSError:
                raise AtlasError(f"required artifact cannot be inspected safely: {name}") from None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode):
                    raise AtlasError(f"required artifact path is not a regular file: {name}")
                preserved.append(name)
                continue
            template_data = _read_relative_bytes(
                source_root, PurePosixPath(name), label="atlas template"
            )
            try:
                target_descriptor = os.open(
                    name, create_flags, 0o644, dir_fd=output_descriptor
                )
            except FileExistsError:
                raise AtlasError(f"required artifact appeared during creation: {name}") from None
            except OSError:
                raise AtlasError(f"required artifact cannot be created safely: {name}") from None
            try:
                offset = 0
                while offset < len(template_data):
                    offset += os.write(target_descriptor, template_data[offset:])
                os.fsync(target_descriptor)
            finally:
                os.close(target_descriptor)
            created.append(name)
        os.fsync(output_descriptor)
    finally:
        os.close(output_descriptor)
    sys.stdout.write(
        serialize_json_output(
            {"mode": mode, "created": created, "preserved": preserved}
        )
    )
    return 0


def detect_mode(artifacts: ArtifactInventory) -> str:
    quick = PurePosixPath("PROJECT_ATLAS.md")
    index = PurePosixPath("ATLAS_INDEX.md")
    quick_state = artifact_state(artifacts, quick)
    index_state = artifact_state(artifacts, index)
    if quick_state == "symlink" or index_state == "symlink":
        raise AtlasError("mode marker must not be a symbolic link")
    if quick_state not in {"regular", "missing"} or index_state not in {"regular", "missing"}:
        raise AtlasError("mode marker must be a regular file")
    has_quick = quick_state == "regular"
    has_index = index_state == "regular"
    if has_quick and has_index:
        raise AtlasError("mixed atlas modes: both PROJECT_ATLAS.md and ATLAS_INDEX.md exist")
    if has_quick:
        try:
            text = read_artifact_text(artifacts, quick)
        except AtlasError:
            raise AtlasError("cannot safely read PROJECT_ATLAS.md") from None
        if re.search(r"^Mode:\s*\*\*QUICK\*\*\s*$", text, re.MULTILINE) is None:
            raise AtlasError("PROJECT_ATLAS.md has no valid QUICK mode declaration")
        return "QUICK"
    if not has_index:
        raise AtlasError("cannot detect mode without PROJECT_ATLAS.md or ATLAS_INDEX.md")
    try:
        text = read_artifact_text(artifacts, index)
    except AtlasError:
        raise AtlasError("cannot safely read ATLAS_INDEX.md") from None
    declaration = re.search(r"^Mode:\s*\*\*(QUICK|STANDARD|FORENSIC)\*\*\s*$", text, re.MULTILINE)
    if declaration is None or declaration.group(1) == "QUICK":
        raise AtlasError("ATLAS_INDEX.md has no valid routed mode declaration")
    mode = declaration.group(1)
    traceability_state = artifact_state(artifacts, PurePosixPath("TRACEABILITY.tsv"))
    if traceability_state == "symlink":
        raise AtlasError("TRACEABILITY.tsv must not be a symbolic link")
    if mode == "STANDARD" and traceability_state != "missing":
        raise AtlasError("mixed atlas modes: STANDARD index includes FORENSIC traceability")
    return mode


def strip_source_location(value: str) -> str:
    without_fragment = re.sub(r"#L?\d+(?:-L?\d+)?$", "", value, flags=re.IGNORECASE)
    return re.sub(r":L?\d+(?:-L?\d+)?$", "", without_fragment, flags=re.IGNORECASE)


def decoded_scan_value(value: str) -> str:
    """Decode bounded percent-encoding layers before leakage classification."""

    current = value
    for _ in range(2):
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return current


def unsafe_repository_reference(value: str) -> bool:
    value = decoded_scan_value(value)
    if value.lower().startswith("file:") or re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    location = strip_source_location(value).replace("\\", "/")
    path = PurePosixPath(location)
    return path.is_absolute() or ".." in path.parts or location.startswith("~")


def html_attribute_values(tag: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in HTML_ATTRIBUTE_VALUE.finditer(tag):
        value = html.unescape(
            match.group("double") or match.group("single") or match.group("bare") or ""
        )
        if value:
            values.append(value)
    return tuple(values)


def html_path_scan_text(value: str) -> str:
    """Remove HTML tag syntax while retaining attribute values for leakage scans."""

    return HTML_TAG.sub(
        lambda match: " " + " ".join(html_attribute_values(match.group(0))) + " ",
        value,
    )


def contains_local_absolute_path(value: str) -> bool:
    value = html_path_scan_text(decoded_scan_value(value))
    known_root_scan = value.replace("/dev/null", "<null-device>")
    if (
        FILE_URI.search(known_root_scan)
        or POSIX_ABSOLUTE_PATH.search(known_root_scan)
        or WINDOWS_ABSOLUTE_PATH.search(known_root_scan)
        or HOME_SHORTHAND_PATH.search(known_root_scan)
    ):
        return True
    route_neutral = HTTP_ROUTE.sub("<http-route>", value)
    route_neutral = LABELED_ROUTE.sub("<http-route>", route_neutral)
    route_neutral = route_neutral.replace("/dev/null", "<null-device>")
    return bool(
        FILE_URI.search(route_neutral)
        or POSIX_ABSOLUTE_PATH.search(route_neutral)
        or GENERIC_POSIX_ABSOLUTE_PATH.search(route_neutral)
        or WINDOWS_ABSOLUTE_PATH.search(route_neutral)
        or HOME_SHORTHAND_PATH.search(route_neutral)
    )


def contains_secret_material(value: str) -> bool:
    normalized = decoded_scan_value(value)
    return any(pattern.search(normalized) for pattern in SECRET_MATERIAL_PATTERNS)


def sanitize_diagnostic(value: str) -> str:
    sanitized = decoded_scan_value(value)
    sanitized = FILE_URI.sub("<local-path>", sanitized)
    sanitized = POSIX_ABSOLUTE_PATH.sub("<local-path>", sanitized)
    sanitized = GENERIC_POSIX_ABSOLUTE_PATH.sub("<local-path>", sanitized)
    sanitized = WINDOWS_ABSOLUTE_PATH.sub("<local-path>", sanitized)
    sanitized = HOME_SHORTHAND_PATH.sub("<local-path>", sanitized)
    for pattern in SECRET_MATERIAL_PATTERNS:
        sanitized = pattern.sub("<secret-material>", sanitized)
    return sanitized


def validate_command_source(record: dict[str, str], line_number: int) -> list[str]:
    errors: list[str] = []
    source_ref = record["source_ref"]
    prefix = f"TRACEABILITY.tsv line {line_number} COMMAND"
    if ANGLE_SUBSTITUTION_TOKEN.search(source_ref):
        errors.append(f"{prefix} source_ref contains an unresolved substitution marker")
    if re.search(r"(?:^|\s)(?:--glob|-g)\s+(?:\*|\?|\[)", source_ref):
        errors.append(f"{prefix} source_ref contains an unquoted glob")
    if any(operator in source_ref for operator in ("&&", "||", ";", "`", "$(")):
        errors.append(f"{prefix} source_ref must be one explicit command without shell control operators")
    try:
        arguments = shlex.split(source_ref, posix=True)
    except ValueError as exc:
        errors.append(f"{prefix} source_ref is not shell-parseable: {exc}")
        arguments = []
    if not arguments or arguments[0].startswith("-"):
        errors.append(f"{prefix} source_ref has no executable command")
    notes_match = COMMAND_NOTES.search(record["notes"])
    if notes_match is None:
        errors.append(
            f"{prefix} notes must record cwd=<relative>; exit=<integer>; stdout_sha256=<64 hex>"
        )
    else:
        cwd = notes_match.group("cwd").strip()
        if unsafe_repository_reference(cwd):
            errors.append(f"{prefix} notes contain a cwd outside the project boundary")
        exit_code = int(notes_match.group("exit"))
        if record["claim_kind"] == "CONFIRMED" and exit_code not in {0, 1}:
            errors.append(f"{prefix} cannot support a CONFIRMED claim with exit={exit_code}")
    return errors


RG_FLAG_OPTIONS = {
    "--count",
    "--count-matches",
    "--files",
    "--fixed-strings",
    "--ignore-case",
    "--line-number",
    "--no-config",
    "--no-messages",
    "--smart-case",
    "--stats",
    "--word-regexp",
    "-F",
    "-S",
    "-c",
    "-i",
    "-n",
    "-s",
    "-w",
}
RG_VALUE_OPTIONS = {"--glob", "--type", "--type-not", "-T", "-g", "-t"}
RG_UNSAFE_OPTIONS = {
    "--follow",
    "--hidden",
    "--ignore-file",
    "--no-ignore",
    "--no-ignore-dot",
    "--no-ignore-exclude",
    "--no-ignore-files",
    "--no-ignore-global",
    "--no-ignore-messages",
    "--no-ignore-parent",
    "--no-ignore-vcs",
    "--pre",
    "--pre-glob",
    "--search-zip",
    "--unrestricted",
    "-L",
    "-u",
    "-uu",
    "-uuu",
    "-z",
}


def replay_target_is_safe(
    project_root: Path,
    working_directory: Path,
    raw_target: str,
    safe_inventory_members: set[PurePosixPath],
) -> bool:
    if raw_target in {"", ".", "./"} or any(character in raw_target for character in "*?["):
        return False
    if unsafe_repository_reference(raw_target):
        return False
    relative = PurePosixPath(raw_target.replace("\\", "/"))
    candidate = lexical_absolute(working_directory / Path(*relative.parts))
    try:
        project_relative = PurePosixPath(candidate.relative_to(project_root).as_posix())
    except ValueError:
        return False
    if (
        sensitive_relative_path(project_relative)
        or project_relative.name.casefold() in EXCLUDED_DIRECTORY_NAMES
        or path_crosses_symlink(project_root, project_relative)
    ):
        return False
    if candidate.is_symlink() or not candidate.exists():
        return False
    if candidate.is_file():
        return project_relative in safe_inventory_members
    if not candidate.is_dir():
        return False
    return any(project_relative in member.parents for member in safe_inventory_members)


def replay_target_members(
    inventory: SafeInventory, working_directory: Path, raw_target: str
) -> set[PurePosixPath]:
    target = lexical_absolute(working_directory / Path(*PurePosixPath(raw_target).parts))
    relative = PurePosixPath(target.relative_to(inventory.root).as_posix())
    if relative in inventory.members:
        return {relative}
    members: set[PurePosixPath] = set()
    for member in inventory.members:
        try:
            member.relative_to(relative)
        except ValueError:
            continue
        members.add(member)
    return members


def bounded_replay_member_sizes(
    inventory: SafeInventory, members: Iterable[PurePosixPath]
) -> dict[PurePosixPath, int]:
    selected = set(members)
    if len(selected) > MAX_REPLAY_FILES:
        raise AtlasError("replay mirror exceeds the file-count limit")
    selected_sizes: dict[PurePosixPath, int] = {}
    total_bytes = 0
    for relative in selected:
        size = inventory_file_size(inventory, relative)
        if size > MAX_REPLAY_FILE_BYTES:
            raise AtlasError("replay source exceeds the per-file byte limit")
        total_bytes += size
        if total_bytes > MAX_REPLAY_TOTAL_BYTES:
            raise AtlasError("replay mirror exceeds the aggregate byte limit")
        selected_sizes[relative] = size
    return selected_sizes


def build_replay_mirror(
    inventory: SafeInventory,
    cwd_relative: PurePosixPath,
    targets: Sequence[str],
    mirror_root: Path,
) -> Path:
    cwd = lexical_absolute(inventory.root / Path(*cwd_relative.parts))
    selected: set[PurePosixPath] = set()
    target_relatives: list[PurePosixPath] = []
    for target in targets:
        absolute = lexical_absolute(cwd / Path(*PurePosixPath(target).parts))
        target_relative = PurePosixPath(absolute.relative_to(inventory.root).as_posix())
        target_relatives.append(target_relative)
        selected.update(replay_target_members(inventory, cwd, target))

    for member in inventory.members:
        if member.name not in {".gitignore", ".ignore"}:
            continue
        parent = member.parent
        if any(
            target == parent or parent in target.parents or parent in cwd_relative.parents
            for target in target_relatives
        ):
            selected.add(member)

    selected_sizes = bounded_replay_member_sizes(inventory, selected)

    for target_relative in target_relatives:
        target_path = mirror_root.joinpath(*target_relative.parts)
        if target_relative not in selected:
            target_path.mkdir(parents=True, exist_ok=True)
    for relative in sorted(selected, key=lambda item: item.as_posix()):
        destination = mirror_root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = read_inventory_bytes(
            inventory,
            relative,
            maximum_bytes=min(MAX_REPLAY_FILE_BYTES, selected_sizes[relative]),
        )
        if len(data) != selected_sizes[relative]:
            raise AtlasError("replay source changed between sizing and copy")
        destination.write_bytes(data)
    mirrored_cwd = mirror_root.joinpath(*cwd_relative.parts)
    mirrored_cwd.mkdir(parents=True, exist_ok=True)
    return mirrored_cwd


def run_bounded_replay_process(
    arguments: Sequence[str],
    *,
    executable: str,
    cwd: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    return run_bounded_process(
        arguments,
        input_bytes=None,
        stdout_limit=MAX_REPLAY_STDOUT_BYTES,
        stderr_limit=MAX_REPLAY_STDERR_BYTES,
        seconds=MAX_REPLAY_SECONDS,
        operation="replay",
        executable=executable,
        cwd=cwd,
        environment=environment,
    )


def replay_command_plan(
    record: dict[str, str],
    line_number: int,
    inventory: SafeInventory,
    *,
    prefix: str | None = None,
) -> tuple[ReplayCommandPlan | None, list[str]]:
    prefix = prefix or f"TRACEABILITY.tsv line {line_number} COMMAND replay"
    try:
        arguments = shlex.split(record["source_ref"], posix=True)
    except ValueError:
        return None, [f"{prefix} source_ref is not shell-parseable"]
    if not arguments or arguments[0] != "rg":
        return None, [f"{prefix} supports only the literal rg executable"]
    if "--no-config" not in arguments:
        return None, [f"{prefix} requires rg --no-config for deterministic execution"]

    positional: list[str] = []
    option_values: list[str] = []
    sort_path_count = 0
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        option, has_equals, inline_value = argument.partition("=")
        if option in RG_UNSAFE_OPTIONS or option.startswith("--no-ignore"):
            return None, [f"{prefix} contains unsafe ripgrep option {option}"]
        if option in {"--sort", "--sortr"}:
            if option != "--sort" or has_equals:
                return None, [f"{prefix} requires exact --sort path for deterministic output"]
            index += 1
            if index >= len(arguments) or arguments[index] != "path":
                return None, [f"{prefix} requires exact --sort path for deterministic output"]
            sort_path_count += 1
            if sort_path_count > 1:
                return None, [f"{prefix} requires exactly one --sort path option"]
        elif option in RG_VALUE_OPTIONS:
            if has_equals:
                option_values.append(inline_value)
            else:
                index += 1
                if index >= len(arguments):
                    return None, [f"{prefix} option {option} has no value"]
                option_values.append(arguments[index])
        elif option in RG_FLAG_OPTIONS:
            if has_equals:
                return None, [f"{prefix} flag {option} must not have a value"]
        elif argument.startswith("-"):
            return None, [f"{prefix} contains unsupported ripgrep option {option}"]
        else:
            positional.append(argument)
        index += 1

    for option_value in option_values:
        normalized = option_value.lstrip("!")
        if any(part.casefold() in EXCLUDED_DIRECTORY_NAMES for part in normalized.split("/")):
            return None, [f"{prefix} option value names an excluded contour"]

    notes_match = COMMAND_NOTES.search(record["notes"])
    if notes_match is None:
        return None, [f"{prefix} lacks canonical cwd, exit, and stdout digest notes"]
    cwd_text = notes_match.group("cwd").strip()
    project_root = inventory.root
    cwd_relative = PurePosixPath(cwd_text.replace("\\", "/"))
    if unsafe_repository_reference(cwd_text):
        return None, [f"{prefix} working directory escapes the project"]
    if path_crosses_symlink(project_root, cwd_relative):
        return None, [f"{prefix} working directory crosses a symbolic link"]
    cwd = lexical_absolute(project_root / Path(*cwd_relative.parts))
    try:
        cwd.relative_to(project_root)
    except ValueError:
        return None, [f"{prefix} working directory escapes the project"]
    if not cwd.is_dir():
        return None, [f"{prefix} working directory does not exist"]
    if "--files" in arguments:
        targets = positional
    else:
        if len(positional) < 2:
            return None, [f"{prefix} must name a search expression and at least one bounded target"]
        targets = positional[1:]
    if not targets or any(
        not replay_target_is_safe(project_root, cwd, target, set(inventory.members))
        for target in targets
    ):
        return None, [f"{prefix} has an unsafe, broad, missing, or excluded target"]
    requires_path_sort = len(targets) > 1 or any(
        lexical_absolute(cwd / Path(*PurePosixPath(target).parts)).is_dir()
        for target in targets
    )
    if requires_path_sort and sort_path_count != 1:
        return None, [f"{prefix} requires exact --sort path for deterministic multi-path output"]
    return (
        ReplayCommandPlan(tuple(arguments), cwd_relative, tuple(targets)),
        [],
    )


def replay_command_evidence(
    record: dict[str, str],
    line_number: int,
    inventory: SafeInventory,
    *,
    prefix: str | None = None,
) -> list[str]:
    prefix = prefix or f"TRACEABILITY.tsv line {line_number} COMMAND replay"
    plan, plan_errors = replay_command_plan(
        record, line_number, inventory, prefix=prefix
    )
    if plan is None:
        return plan_errors
    arguments = list(plan.arguments)
    cwd_relative = plan.cwd_relative
    targets = list(plan.targets)
    notes_match = COMMAND_NOTES.search(record["notes"])
    assert notes_match is not None

    rg_executable = trusted_host_executable("rg", prohibited_roots=(inventory.root,))
    if rg_executable is None:
        return [f"{prefix} cannot run because rg is unavailable"]

    environment = os.environ.copy()
    environment.pop("RIPGREP_CONFIG_PATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        with tempfile.TemporaryDirectory(prefix="project-atlas-replay-") as temporary:
            mirror_root = Path(temporary) / "project"
            mirror_root.mkdir()
            mirrored_cwd = build_replay_mirror(
                inventory, cwd_relative, targets, mirror_root
            )
            completed = run_bounded_replay_process(
                arguments,
                executable=rg_executable,
                cwd=mirrored_cwd,
                environment=environment,
            )
    except (FileNotFoundError, PermissionError):
        return [
            f"{prefix} requires an accessible rg executable and a writable operating-system temporary directory"
        ]
    except AtlasError as exc:
        return [f"{prefix} {exc}"]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"{prefix} could not complete safely: {type(exc).__name__}"]
    expected_exit = int(notes_match.group("exit"))
    expected_digest = notes_match.group("digest")
    actual_digest = hashlib.sha256(completed.stdout).hexdigest()
    errors: list[str] = []
    if completed.returncode != expected_exit:
        errors.append(
            f"{prefix} exit mismatch: recorded {expected_exit}, observed {completed.returncode}"
        )
    if actual_digest != expected_digest:
        errors.append(f"{prefix} stdout digest does not match the recorded digest")
    return errors


def read_traceability_rows(artifacts: ArtifactInventory) -> list[list[str]]:
    try:
        text = read_artifact_text(
            artifacts,
            PurePosixPath("TRACEABILITY.tsv"),
            maximum_bytes=MAX_TRACEABILITY_BYTES,
        )
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t"))
        data_rows = sum(1 for row in rows[1:] if row and any(value.strip() for value in row))
        if data_rows > MAX_TRACEABILITY_ROWS:
            raise AtlasError("TRACEABILITY.tsv exceeds the row limit")
        return rows
    except AtlasError:
        raise
    except csv.Error:
        raise AtlasError("TRACEABILITY.tsv cannot be read as valid UTF-8 TSV") from None


def parse_atlas_refs(value: str, line_number: int) -> tuple[tuple[str, ...], list[str]]:
    prefix = f"TRACEABILITY.tsv line {line_number}"
    if value == "-":
        return (), []
    if not value:
        return (), [f"{prefix} has an empty atlas_refs field; use - for no material registry link"]
    raw_refs = value.split(";")
    if any(not raw_ref or raw_ref != raw_ref.strip() for raw_ref in raw_refs):
        return (), [f"{prefix} has malformed atlas_refs separators"]
    if len(raw_refs) != len(set(raw_refs)):
        return (), [f"{prefix} repeats an atlas_ref"]
    if raw_refs != sorted(raw_refs):
        return (), [f"{prefix} atlas_refs must be sorted lexically"]
    errors: list[str] = []
    for atlas_ref in raw_refs:
        match = re.fullmatch(
            r"(?P<prefix>[^#]+#[^/]+)/(?P<identifier>[A-Za-z0-9][A-Za-z0-9._-]*)"
            r"(?:/(?P<slot>[A-Za-z0-9_-]+))?",
            atlas_ref,
        )
        if match is None or match.group("prefix") not in ATLAS_REF_PREFIXES:
            errors.append(f"{prefix} has an invalid atlas_ref")
            continue
        slot = match.group("slot")
        if match.group("prefix") == "FINDINGS_AND_DISPOSITIONS.md#findings":
            if slot not in {"finding", "disposition"}:
                errors.append(f"{prefix} finding atlas_ref requires /finding or /disposition")
        elif slot is not None:
            errors.append(f"{prefix} has an unexpected atlas_ref claim slot")
    return tuple(raw_refs), errors


def parse_evidence_timestamp(value: str) -> datetime | None:
    """Parse the two canonical UTC evidence timestamp forms without normalization."""

    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        timestamp_format = "%Y-%m-%d"
    elif re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ):
        timestamp_format = "%Y-%m-%dT%H:%M:%SZ"
    else:
        return None
    try:
        parsed = datetime.strptime(value, timestamp_format)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def evidence_timestamp_is_future(
    timestamp: datetime,
    *,
    observed_now: datetime | None = None,
) -> bool:
    """Reject coordinated future evidence while allowing small host clock skew."""

    observed_now = observed_now or datetime.now(timezone.utc)
    return timestamp > observed_now + MAX_FUTURE_CLOCK_SKEW


def trace_record_compatibility_errors(
    record: dict[str, str], line_number: int
) -> list[str]:
    prefix = f"TRACEABILITY.tsv line {line_number}"
    errors: list[str] = []
    claim_kind = record.get("claim_kind", "")
    source_type = record.get("source_type", "")
    status = record.get("status", "")
    if status and status not in TRACE_STATUSES:
        errors.append(f"{prefix} has an invalid status")
    if source_type == "UNRESOLVED" and claim_kind != "UNKNOWN":
        errors.append(
            f"{prefix} cannot use UNRESOLVED evidence for a {claim_kind or 'missing'} claim"
        )
    observed_time = parse_evidence_timestamp(record.get("observed_at", ""))
    if observed_time is None:
        errors.append(f"{prefix} has an invalid observed_at timestamp")
    elif evidence_timestamp_is_future(observed_time):
        errors.append(f"{prefix} has a future observed_at timestamp")
    atlas_refs, atlas_ref_errors = parse_atlas_refs(record.get("atlas_refs", ""), line_number)
    if not atlas_ref_errors:
        review_refs = [ref for ref in atlas_refs if ref.startswith(REVIEW_ATLAS_REF_PREFIX)]
        if review_refs and len(review_refs) != len(atlas_refs):
            errors.append(f"{prefix} mixes review and non-review atlas_refs")
    return errors


def trace_record_is_completion_evidence(record: dict[str, str], line_number: int) -> bool:
    return (
        record.get("status", "") in ACTIVE_TRACE_STATUSES
        and not trace_record_compatibility_errors(record, line_number)
    )


def validate_traceability_rows(
    rows: list[list[str]],
    *,
    inventory: SafeInventory | None = None,
    replay_commands: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not rows:
        return ["TRACEABILITY.tsv is empty"]
    if tuple(rows[0]) != TRACEABILITY_HEADER:
        errors.append("TRACEABILITY.tsv has an invalid header; expected: " + "\\t".join(TRACEABILITY_HEADER))
        return errors
    seen_ids: set[str] = set()
    for line_number, row in enumerate(rows[1:], start=2):
        if not row or all(not value.strip() for value in row):
            continue
        if len(row) != len(TRACEABILITY_HEADER):
            errors.append(
                f"TRACEABILITY.tsv line {line_number} has {len(row)} columns; expected {len(TRACEABILITY_HEADER)}"
            )
            continue
        record = dict(zip(TRACEABILITY_HEADER, (value.strip() for value in row)))
        if any(contains_local_absolute_path(value) for value in record.values()):
            errors.append(f"TRACEABILITY.tsv line {line_number} contains a local absolute path")
        if any(contains_secret_material(value) for value in record.values()):
            errors.append(f"TRACEABILITY.tsv line {line_number} contains secret material")
        if not record["fact_id"] or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", record["fact_id"]):
            errors.append(f"TRACEABILITY.tsv line {line_number} has an invalid fact_id")
        elif record["fact_id"] in seen_ids:
            errors.append(f"TRACEABILITY.tsv line {line_number} repeats a fact_id")
        seen_ids.add(record["fact_id"])
        if record["claim_kind"] not in CLAIM_KINDS:
            errors.append(f"TRACEABILITY.tsv line {line_number} has an invalid claim_kind")
        if not record["claim"]:
            errors.append(f"TRACEABILITY.tsv line {line_number} has an empty claim")
        if record["source_type"] not in SOURCE_TYPES:
            errors.append(f"TRACEABILITY.tsv line {line_number} has an invalid source_type")
        parsed_source = parse_source_location(record["source_ref"])
        if not record["source_ref"]:
            errors.append(f"TRACEABILITY.tsv line {line_number} has an empty source_ref")
        elif unsafe_repository_reference(record["source_ref"]):
            errors.append(f"TRACEABILITY.tsv line {line_number} has a source_ref outside the project boundary")
        elif (
            record["source_type"] in FILE_SOURCE_TYPES
            and parsed_source is None
        ):
            errors.append(
                f"TRACEABILITY.tsv line {line_number} has an invalid structured file source_ref"
            )
        elif (
            inventory is not None
            and record["source_type"] not in FILE_SOURCE_TYPES
            and parsed_source is not None
            and parsed_source[0] in inventory.members
        ):
            errors.append(
                f"TRACEABILITY.tsv line {line_number} project-local file reference must use a file-like source_type"
            )
        if not record["observed_at"]:
            errors.append(f"TRACEABILITY.tsv line {line_number} has an empty observed_at")
        if not record["status"]:
            errors.append(f"TRACEABILITY.tsv line {line_number} has an empty status")
        _atlas_refs, atlas_ref_errors = parse_atlas_refs(record["atlas_refs"], line_number)
        errors.extend(atlas_ref_errors)
        errors.extend(trace_record_compatibility_errors(record, line_number))
        if record["source_type"] == "COMMAND":
            errors.extend(validate_command_source(record, line_number))
            if (
                replay_commands
                and inventory is not None
                and record["status"] in ACTIVE_TRACE_STATUSES
            ):
                errors.extend(replay_command_evidence(record, line_number, inventory))
    return errors


def validate_traceability(
    artifacts: ArtifactInventory,
    *,
    inventory: SafeInventory | None = None,
    replay_commands: bool = False,
) -> list[str]:
    try:
        rows = read_traceability_rows(artifacts)
    except AtlasError as exc:
        return [str(exc)]
    return validate_traceability_rows(
        rows, inventory=inventory, replay_commands=replay_commands
    )


def markdown_table_cells(line: str) -> list[str] | None:
    cursor, leading_columns = markdown_scan_indentation(
        line, MarkdownCursor(0, 0)
    )
    if leading_columns > 3:
        return None
    stripped = markdown_cursor_text(line, cursor).strip()
    if not stripped.startswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def markdown_section_name_matches(observed: str, canonical: str) -> bool:
    return observed == canonical or observed.startswith(canonical + " ")


def parse_table_contract(
    filename: str, text: str, expected: tuple[str, ...], *, draft: bool
) -> tuple[list[CanonicalTableRow], list[str]]:
    lines = commonmark_split_lines(
        markdown_rendered_block_text(text),
        keepends=False,
    )
    expected_normalized = tuple(header.casefold() for header in expected)
    table_headers: list[tuple[int, tuple[str, ...]]] = []
    for index, line in enumerate(lines):
        cells = markdown_table_cells(line)
        if cells is None or index + 1 >= len(lines):
            continue
        separator = markdown_table_cells(lines[index + 1])
        if (
            separator is not None
            and len(separator) == len(cells)
            and all(re.fullmatch(r":?-{3,}:?", cell) is not None for cell in separator)
        ):
            table_headers.append((index, tuple(cell.casefold() for cell in cells)))

    canonical_indexes = [
        index for index, headers in table_headers if headers == expected_normalized
    ]
    if not canonical_indexes:
        return [], [f"{filename} is missing the required table columns: " + " | ".join(expected)]
    if len(canonical_indexes) != 1:
        return [], [f"{filename} must contain exactly one canonical table; competing registries found"]
    header_index = canonical_indexes[0]

    required_section = TABLE_SECTIONS[filename]
    matching_section_headings = [
        heading.group(1)
        for line in lines
        if (heading := re.fullmatch(r"##\s+(.+?)\s*", line)) is not None
        and markdown_section_name_matches(heading.group(1), required_section)
    ]
    section: str | None = None
    for line in lines[:header_index]:
        heading = re.fullmatch(r"##\s+(.+?)\s*", line)
        if heading is not None:
            section = heading.group(1)
    if (
        len(matching_section_headings) != 1
        or section is None
        or not markdown_section_name_matches(section, required_section)
    ):
        return [], [
            f"{filename} canonical table must be in one unambiguous required section ## {required_section}"
        ]

    identity_columns = TABLE_IDENTITY_COLUMNS[filename]
    for index, headers in table_headers:
        if index == header_index:
            continue
        if identity_columns.issubset(set(headers)):
            return [], [f"{filename} contains a competing registry outside its required section"]
    if header_index + 1 >= len(lines):
        return [], [f"{filename} required table has no separator row"]
    separator = markdown_table_cells(lines[header_index + 1])
    if (
        separator is None
        or len(separator) != len(expected)
        or any(re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator)
    ):
        return [], [f"{filename} required table has an invalid separator row"]

    errors: list[str] = []
    parsed_rows: list[CanonicalTableRow] = []
    substantive_rows = 0
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines[header_index + 2 :], start=header_index + 3):
        cells = markdown_table_cells(line)
        if cells is None:
            if line.strip().startswith("##") or line.strip():
                break
            continue
        substantive_rows += 1
        if substantive_rows > MAX_REGISTRY_ROWS:
            errors.append(f"{filename} canonical table exceeds the row limit")
            break
        if len(cells) != len(expected):
            errors.append(
                f"{filename} line {line_number} has {len(cells)} table cells; expected {len(expected)}"
            )
            continue
        for column, value in zip(expected, cells):
            if not value:
                errors.append(f"{filename} line {line_number} has an empty {column} cell")
        row = dict(zip(expected, cells))
        parsed_rows.append(CanonicalTableRow(filename, line_number, row))
        identifier_column = TABLE_ID_COLUMNS[filename]
        identifier = row.get(identifier_column, "")
        if identifier and IDENTIFIER.fullmatch(identifier) is None:
            errors.append(f"{filename} line {line_number} has an invalid {identifier_column}")
        elif identifier in seen_ids:
            errors.append(f"{filename} line {line_number} repeats {identifier_column} {identifier}")
        seen_ids.add(identifier)
        claim_kind = row.get("Claim kind")
        if claim_kind and claim_kind not in CLAIM_KINDS:
            errors.append(f"{filename} line {line_number} has an invalid Claim kind")
        if filename == "FINDINGS_AND_DISPOSITIONS.md":
            severity = row.get("Severity", "").upper()
            if severity and severity not in {"P0", "P1", "P2", "P3", "UNKNOWN"}:
                errors.append(f"{filename} line {line_number} has an invalid Severity")
            disposition = row.get("Disposition", "").upper()
            if disposition and disposition not in {"KEEP", "REWRITE", "MERGE", "DELETE", "UNKNOWN"}:
                errors.append(f"{filename} line {line_number} has an invalid Disposition")
        if filename == "ATLAS_INDEX.md":
            try:
                numerator = int(row.get("Numerator", ""))
                denominator = int(row.get("Denominator", ""))
            except ValueError:
                errors.append(f"{filename} line {line_number} has a non-integer coverage count")
            else:
                if numerator < 0 or denominator < 0 or numerator > denominator:
                    errors.append(f"{filename} line {line_number} has invalid coverage arithmetic")
    if (
        not draft
        and substantive_rows == 0
        and filename not in OPTIONAL_COMPLETION_TABLES
    ):
        errors.append(
            f"{filename} canonical table has no substantive row; draft scaffold is not complete"
        )
    return parsed_rows, errors


def validate_table_contract(
    filename: str, text: str, expected: tuple[str, ...], *, draft: bool
) -> list[str]:
    _rows, errors = parse_table_contract(filename, text, expected, draft=draft)
    return errors


def collect_material_claims(
    registries: dict[str, list[CanonicalTableRow]],
) -> tuple[dict[str, MaterialClaim], list[str]]:
    claims: dict[str, MaterialClaim] = {}
    errors: list[str] = []

    def add(row: CanonicalTableRow, atlas_ref: str, claim_kind: str, claim: str) -> None:
        material = MaterialClaim(
            atlas_ref=atlas_ref,
            claim_kind=claim_kind,
            claim=claim.strip(),
            owner=row.filename,
            line_number=row.line_number,
        )
        if atlas_ref in claims:
            errors.append(f"{row.filename} line {row.line_number} repeats material atlas_ref {atlas_ref}")
        else:
            claims[atlas_ref] = material

    for row in registries.get("PRODUCT_AND_REQUIREMENTS.md", []):
        identifier = row.values.get("ID", "")
        add(
            row,
            f"PRODUCT_AND_REQUIREMENTS.md#requirements/{identifier}",
            row.values.get("Claim kind", ""),
            row.values.get("Requirement", ""),
        )
    for row in registries.get("FINDINGS_AND_DISPOSITIONS.md", []):
        identifier = row.values.get("ID", "")
        add(
            row,
            f"FINDINGS_AND_DISPOSITIONS.md#findings/{identifier}/finding",
            row.values.get("Claim kind", ""),
            row.values.get("Finding", ""),
        )
        disposition = row.values.get("Disposition", "").upper()
        add(
            row,
            f"FINDINGS_AND_DISPOSITIONS.md#findings/{identifier}/disposition",
            "UNKNOWN" if disposition == "UNKNOWN" else "TARGET",
            f"Disposition {identifier}: {disposition}",
        )
    for row in registries.get("MIGRATION_PLAN.md", []):
        identifier = row.values.get("Stage", "")
        add(
            row,
            f"MIGRATION_PLAN.md#migration/{identifier}",
            row.values.get("Claim kind", ""),
            row.values.get("Change", ""),
        )
    for row in registries.get("ATLAS_INDEX.md", []):
        identifier = row.values.get("ID", "")
        add(
            row,
            f"ATLAS_INDEX.md#coverage/{identifier}",
            row.values.get("Claim kind", ""),
            row.values.get("Claim", ""),
        )
    for row in registries.get("OPEN_UNKNOWNS.md", []):
        identifier = row.values.get("ID", "")
        add(
            row,
            f"OPEN_UNKNOWNS.md#unknowns/{identifier}",
            "UNKNOWN",
            row.values.get("UNKNOWN", ""),
        )
    for row in registries.get("LIVE_HANDOFF.md", []):
        identifier = row.values.get("ID", "")
        review_kind = row.values.get("Review kind", "").upper()
        verdict = row.values.get("Verdict", "").upper()
        critical = row.values.get("Critical", "")
        important = row.values.get("Important", "")
        add(
            row,
            f"LIVE_HANDOFF.md#reviews/{identifier}",
            "CONFIRMED",
            f"{review_kind} review {verdict}: {critical} Critical, {important} Important",
        )
    return claims, errors


def traceability_records(rows: list[list[str]]) -> list[tuple[int, dict[str, str]]]:
    if not rows or tuple(rows[0]) != TRACEABILITY_HEADER:
        return []
    records: list[tuple[int, dict[str, str]]] = []
    for line_number, row in enumerate(rows[1:], start=2):
        if not row or all(not value.strip() for value in row):
            continue
        if len(row) != len(TRACEABILITY_HEADER):
            continue
        records.append(
            (line_number, dict(zip(TRACEABILITY_HEADER, (value.strip() for value in row))))
        )
    return records


def validate_registry_ledger_coverage(
    material_claims: dict[str, MaterialClaim],
    trace_rows: list[list[str]],
    *,
    draft: bool,
) -> list[str]:
    errors: list[str] = []
    covered: set[str] = set()
    for line_number, record in traceability_records(trace_rows):
        atlas_refs, atlas_ref_errors = parse_atlas_refs(record["atlas_refs"], line_number)
        if atlas_ref_errors:
            continue
        for atlas_ref in atlas_refs:
            material = material_claims.get(atlas_ref)
            if material is None:
                errors.append(
                    f"TRACEABILITY.tsv line {line_number} has dangling material atlas_ref {atlas_ref}"
                )
                continue
            if record["claim_kind"] != material.claim_kind:
                errors.append(
                    f"TRACEABILITY.tsv line {line_number} claim_kind does not match {atlas_ref}"
                )
            if record["claim"].strip() != material.claim:
                errors.append(
                    f"TRACEABILITY.tsv line {line_number} claim text does not match {atlas_ref}"
                )
            if (
                record["claim_kind"] == material.claim_kind
                and record["claim"].strip() == material.claim
                and trace_record_is_completion_evidence(record, line_number)
            ):
                covered.add(atlas_ref)
    if not draft:
        for atlas_ref in sorted(set(material_claims) - covered):
            errors.append(f"material registry claim lacks ACTIVE traceability coverage: {atlas_ref}")
    return errors


def validate_independent_reviews(
    review_rows: list[CanonicalTableRow],
    *,
    draft: bool,
    snapshot_binding: SnapshotBinding | None,
) -> list[str]:
    errors: list[str] = []
    active_by_kind: dict[str, list[CanonicalTableRow]] = {kind: [] for kind in REVIEW_KINDS}
    parsed_times: dict[int, datetime] = {}
    for row in review_rows:
        values = row.values
        review_kind = values.get("Review kind", "").upper()
        reviewer_ref = values.get("Reviewer ref", "")
        independence = values.get("Independence", "").upper()
        verdict = values.get("Verdict", "").upper()
        summary = values.get("Retained evidence summary", "").strip()
        remaining_limits = values.get("Remaining limits", "").strip()
        reviewed_at = values.get("Reviewed at", "")
        review_status = values.get("Status", "")
        if review_kind not in REVIEW_KINDS:
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} has an invalid Review kind")
        if IDENTIFIER.fullmatch(reviewer_ref) is None:
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} has an invalid Reviewer ref")
        if independence not in REVIEW_INDEPENDENCE:
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} lacks independent review evidence")
        if verdict not in {"PASS", "FAIL"}:
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} has an invalid review Verdict")
        try:
            critical = int(values.get("Critical", ""))
            important = int(values.get("Important", ""))
        except ValueError:
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} has non-integer review counts")
            critical = important = -1
        if critical < 0:
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} has an invalid Critical count")
        if important < 0:
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} has an invalid Important count")
        if summary.upper() in REVIEW_PLACEHOLDERS or not substantive_text(
            summary,
            minimum_characters=REVIEW_MIN_SUMMARY_CHARACTERS,
            minimum_words=REVIEW_MIN_SUMMARY_WORDS,
        ):
            errors.append(
                f"LIVE_HANDOFF.md line {row.line_number} lacks a substantive retained evidence summary"
            )
        if remaining_limits.upper() in REVIEW_PLACEHOLDERS or not substantive_text(
            remaining_limits,
            minimum_characters=REVIEW_MIN_LIMIT_CHARACTERS,
            minimum_words=REVIEW_MIN_LIMIT_WORDS,
        ):
            errors.append(
                f"LIVE_HANDOFF.md line {row.line_number} lacks substantive remaining limits"
            )
        reviewed_time = parse_evidence_timestamp(reviewed_at)
        if (
            reviewed_time is None
            or "T" not in reviewed_at
            or not reviewed_at.endswith("Z")
        ):
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} has an invalid Reviewed at timestamp")
        else:
            parsed_times[row.line_number] = reviewed_time
            if evidence_timestamp_is_future(reviewed_time):
                errors.append(
                    f"LIVE_HANDOFF.md line {row.line_number} has a future Reviewed at timestamp"
                )
            elif snapshot_binding is not None:
                if reviewed_time < snapshot_binding.evidence_observed_through:
                    errors.append(
                        f"LIVE_HANDOFF.md line {row.line_number} violates review chronology"
                    )
                elif reviewed_time - snapshot_binding.evidence_observed_through > REVIEW_FRESHNESS_WINDOW:
                    errors.append(
                        f"LIVE_HANDOFF.md line {row.line_number} exceeds the review freshness window"
                    )
        if review_status and review_status not in TRACE_STATUSES:
            errors.append(f"LIVE_HANDOFF.md line {row.line_number} has an invalid review Status")
        if review_status in ACTIVE_TRACE_STATUSES and review_kind in REVIEW_KINDS:
            active_by_kind[review_kind].append(row)

    if draft:
        return errors
    active_reviewer_refs: list[str] = []
    for review_kind in sorted(REVIEW_KINDS):
        active = active_by_kind[review_kind]
        if len(active) != 1:
            errors.append(
                f"LIVE_HANDOFF.md requires exactly one ACTIVE {review_kind} independent review"
            )
            continue
        row = active[0]
        values = row.values
        active_reviewer_refs.append(values.get("Reviewer ref", ""))
        if values.get("Verdict", "").upper() != "PASS":
            errors.append(f"LIVE_HANDOFF.md ACTIVE {review_kind} review must record PASS")
        if values.get("Critical") != "0":
            errors.append(f"LIVE_HANDOFF.md ACTIVE {review_kind} review must record 0 Critical")
        if values.get("Important") != "0":
            errors.append(f"LIVE_HANDOFF.md ACTIVE {review_kind} review must record 0 Important")
        if (
            snapshot_binding is None
            or values.get("Reviewed snapshot") != snapshot_binding.review_input_sha256
        ):
            errors.append(
                f"LIVE_HANDOFF.md ACTIVE {review_kind} review is not bound to the current canonical review input"
            )
    if len(active_reviewer_refs) == len(REVIEW_KINDS) and len(set(active_reviewer_refs)) != len(
        active_reviewer_refs
    ):
        errors.append("LIVE_HANDOFF.md correctness and security reviews require distinct reviewers")
    return errors


def level_two_sections(text: str) -> tuple[dict[str, str], set[str]]:
    rendered_text = markdown_rendered_block_text(text)
    matches = list(re.finditer(r"(?m)^##\s+([^\n]+?)\s*$", rendered_text))
    sections: dict[str, str] = {}
    duplicates: set[str] = set()
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        body_start = match.end()
        body_end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(rendered_text)
        )
        body = rendered_text[body_start:body_end].strip()
        if name in sections:
            duplicates.add(name)
        else:
            sections[name] = body
    return sections, duplicates


def substantive_text(value: str, *, minimum_characters: int, minimum_words: int) -> bool:
    words = 0
    inside_word = False
    for character in value:
        if character.isalnum():
            if not inside_word:
                words += 1
            inside_word = True
        elif inside_word and character in {"_", "-"}:
            continue
        else:
            inside_word = False
    return len(value.strip()) >= minimum_characters and words >= minimum_words


def normalized_markdown_prose(value: str) -> str:
    return " ".join(
        "".join(
            character.casefold() if character.isalnum() else " "
            for character in value
        ).split()
    )


def text_without_delimited_spans(value: str, opening: str, closing: str) -> str:
    visible: list[str] = []
    position = 0
    while position < len(value):
        span_start = value.find(opening, position)
        if span_start < 0:
            visible.append(value[position:])
            break
        span_end = value.find(closing, span_start + len(opening))
        if span_end < 0:
            visible.append(value[position:])
            break
        visible.append(value[position:span_start])
        visible.append(" ")
        position = span_end + len(closing)
    return "".join(visible)


def text_without_html_declarations(value: str) -> str:
    visible: list[str] = []
    position = 0
    while position < len(value):
        declaration_start = value.find("<!", position)
        if declaration_start < 0:
            visible.append(value[position:])
            break
        marker_position = declaration_start + 2
        if (
            marker_position >= len(value)
            or not "A" <= value[marker_position] <= "Z"
        ):
            visible.append(value[position:marker_position])
            position = marker_position
            continue
        declaration_end = value.find(">", marker_position + 1)
        if declaration_end < 0:
            visible.append(value[position:])
            break
        visible.append(value[position:declaration_start])
        visible.append(" ")
        position = declaration_end + 1
    return "".join(visible)


def markdown_visible_prose_text(value: str) -> str:
    """Remove non-visible inline HTML while retaining text between tags."""

    visible = markdown_without_inline_code_spans(value)
    visible = markdown_replace_inline_links_until_stable(visible, preserve_labels=True)
    visible = text_without_delimited_spans(visible, "<!--", "-->")
    visible = text_without_delimited_spans(visible, "<?", "?>")
    visible = text_without_delimited_spans(visible, "<![CDATA[", "]]>")
    visible = text_without_html_declarations(visible)
    return markdown_text_without_html_tags(visible)


def markdown_without_inline_code_spans(value: str) -> str:
    visible: list[str] = []
    position = 0
    while position < len(value):
        if value[position] != "`":
            visible.append(value[position])
            position += 1
            continue
        run_end = markdown_backtick_run_end(value, position)
        run_length = run_end - position
        close_position = run_end
        closed = False
        while close_position < len(value):
            if value[close_position] != "`":
                close_position += 1
                continue
            close_end = markdown_backtick_run_end(value, close_position)
            if close_end - close_position == run_length:
                visible.append(" ")
                position = close_end
                closed = True
                break
            close_position = close_end
        if not closed:
            visible.append(value[position])
            position += 1
    return "".join(visible)


def markdown_replace_inline_links_until_stable(
    text: str,
    *,
    preserve_labels: bool,
) -> str:
    previous = text
    for _iteration in range(8):
        current = markdown_replace_inline_links(previous, preserve_labels=preserve_labels)
        if current == previous:
            return current
        previous = current
    return previous


def substantive_markdown_body(value: str) -> bool:
    """Require visible prose or a populated rendered Markdown table."""

    rendered = markdown_rendered_block_text(value)
    lines = commonmark_split_lines(rendered, keepends=False)
    prose = "\n".join(
        line.strip()
        for line in lines
        if line.strip() and markdown_table_cells(line) is None
    )
    visible_prose = markdown_visible_prose_text(prose)
    if substantive_text(
        visible_prose, minimum_characters=12, minimum_words=2
    ):
        return True

    for index in range(len(lines) - 2):
        header = markdown_table_cells(lines[index])
        separator = markdown_table_cells(lines[index + 1])
        row = markdown_table_cells(lines[index + 2])
        if (
            header is None
            or separator is None
            or row is None
            or len(header) != len(separator)
            or len(row) != len(header)
            or not all(
                re.fullmatch(r":?-{3,}:?", cell) is not None
                for cell in separator
            )
        ):
            continue
        if any(
            substantive_text(cell, minimum_characters=1, minimum_words=1)
            for cell in row
        ):
            return True
    return False


def validate_depth_decision(text: str, section_name: str) -> list[str]:
    """Require one durable depth-decision record in its canonical section."""

    errors: list[str] = []
    sections, duplicates = level_two_sections(text)
    if section_name in duplicates:
        return [f"depth decision section ## {section_name} is duplicated"]
    body = sections.get(section_name, "")
    if not body:
        return [f"depth decision requires canonical section ## {section_name}"]
    for label in DEPTH_DECISION_FIELDS:
        values = re.findall(rf"(?m)^{re.escape(label)}:\s*(.*?)\s*$", body)
        if len(values) != 1:
            errors.append(f"depth decision requires exactly one {label} field")
            continue
        value = values[0].strip()
        minimum_characters = 1 if label == "Selected by" else 12
        minimum_words = 1 if label == "Selected by" else 2
        if (
            value.upper() in REVIEW_PLACEHOLDERS
            or re.search(r"\bUNKNOWN\b", value, re.IGNORECASE)
            or not substantive_text(
                value,
                minimum_characters=minimum_characters,
                minimum_words=minimum_words,
            )
        ):
            errors.append(f"depth decision {label} lacks a substantive value")
    return errors


def validate_quick_completion(text: str, inventory: SafeInventory) -> list[str]:
    prefix = "QUICK completion"
    errors: list[str] = []
    sections, duplicates = level_two_sections(text)
    try:
        template_text = (template_root() / "quick" / "PROJECT_ATLAS.md").read_text(
            encoding="utf-8"
        )
    except OSError:
        return [f"{prefix} cannot load its canonical template"]
    template_sections, _template_duplicates = level_two_sections(template_text)
    for name in QUICK_COMPLETION_SECTIONS:
        body = sections.get(name, "")
        if name in duplicates:
            errors.append(f"{prefix} section ## {name} is duplicated")
            continue
        if not substantive_text(body, minimum_characters=12, minimum_words=2):
            errors.append(f"{prefix} section ## {name} lacks substantive content")
            continue
        if body == template_sections.get(name) or re.search(r"\bUNKNOWN\b", body):
            errors.append(f"{prefix} section ## {name} still contains scaffold content")

    unknowns = sections.get("Unknowns", "")
    if "Unknowns" in duplicates:
        errors.append(f"{prefix} section ## Unknowns is duplicated")
    elif not substantive_text(unknowns, minimum_characters=12, minimum_words=2):
        errors.append(f"{prefix} section ## Unknowns lacks substantive content")
    elif unknowns == template_sections.get("Unknowns"):
        errors.append(f"{prefix} section ## Unknowns still contains scaffold content")

    legend = sections.get("Evidence Legend", "")
    for claim_kind in sorted(CLAIM_KINDS):
        definitions = re.findall(
            rf"(?m)^-\s+\*\*{claim_kind}\*\*:\s*(.*?)\s*$",
            legend,
        )
        if len(definitions) != 1:
            errors.append(
                f"{prefix} Evidence Legend requires exactly one {claim_kind} definition"
            )
        elif not substantive_text(
            definitions[0], minimum_characters=8, minimum_words=2
        ):
            errors.append(f"{prefix} Evidence Legend {claim_kind} lacks a definition")

    evidence_snapshot = sections.get("Evidence Snapshot", "")
    observed_match = re.search(
        r"(?m)^Observed at:\s*([^.;\n]+(?:T[^.;\n]+Z)?)\.?\s*$", evidence_snapshot
    )
    observed_time = (
        parse_evidence_timestamp(observed_match.group(1).strip())
        if observed_match is not None
        else None
    )
    if observed_time is None:
        errors.append(f"{prefix} Evidence Snapshot requires a real UTC observation timestamp")
    elif evidence_timestamp_is_future(observed_time):
        errors.append(f"{prefix} Evidence Snapshot has a future observation timestamp")
    snapshot_match = re.search(
        r"(?m)^Source or worktree snapshot:\s*(.+?)\.?\s*$", evidence_snapshot
    )
    if (
        snapshot_match is None
        or not substantive_text(snapshot_match.group(1), minimum_characters=7, minimum_words=1)
        or snapshot_match.group(1).strip().upper() in REVIEW_PLACEHOLDERS
    ):
        errors.append(f"{prefix} Evidence Snapshot requires a concrete source snapshot")

    verification = sections.get("Verification", "")
    command_matches = re.findall(r"(?m)^Command:\s*`([^`\n]+)`\s*$", verification)
    command_match = command_matches[0] if len(command_matches) == 1 else None
    proof_match = re.search(r"(?m)^Proof boundary:\s*(.+?)\s*$", verification)
    if command_match is None:
        errors.append(f"{prefix} Verification requires exactly one backtick-delimited Command")
    else:
        command = command_match
        try:
            arguments = shlex.split(command, posix=True)
        except ValueError:
            arguments = []
        if not arguments or any(operator in command for operator in ("&&", "||", ";", "`", "$(")):
            errors.append(f"{prefix} Verification Command is not one reproducible command")
    if proof_match is None or not substantive_text(
        proof_match.group(1), minimum_characters=20, minimum_words=4
    ):
        errors.append(f"{prefix} Verification requires a substantive Proof boundary")

    exact_result = sections.get("Exact Validation Result", "")
    exit_match = re.search(r"(?m)^Exit code:\s*(-?\d+)\s*$", exact_result)
    if exit_match is None:
        errors.append(f"{prefix} Exact Validation Result requires an integer Exit code")
    result_match = re.search(r"(?m)^Observed result:\s*(.+?)\s*$", exact_result)
    if result_match is None or not substantive_text(
        result_match.group(1), minimum_characters=12, minimum_words=2
    ):
        errors.append(f"{prefix} Exact Validation Result requires a substantive observed result")
    digest_match = re.search(
        r"(?m)^Stdout SHA-256:\s*([0-9a-f]{64})\s*$", exact_result
    )
    if digest_match is None:
        errors.append(f"{prefix} Exact Validation Result requires a stdout SHA-256")

    if command_match is not None and exit_match is not None and digest_match is not None:
        errors.extend(
            replay_command_evidence(
                {
                    "source_ref": command_match,
                    "notes": (
                        f"cwd=.; exit={exit_match.group(1)}; "
                        f"stdout_sha256={digest_match.group(1)}"
                    ),
                },
                0,
                inventory,
                prefix=f"{prefix} Verification replay",
            )
        )

    source_references = sections.get("Source References", "")
    if not markdown_source_locations(source_references, inventory.members):
        errors.append(f"{prefix} Source References requires a project-relative source location")
    return errors


def validate_handoff_contract(text: str, mode: str) -> list[str]:
    errors: list[str] = []
    try:
        canonical_text = (template_root() / mode.lower() / "LIVE_HANDOFF.md").read_text(
            encoding="utf-8"
        )
    except OSError:
        canonical_text = ""
    canonical_blocks = [match.group("body") for match in SHELL_FENCE.finditer(canonical_text)]
    observed_blocks = [match.group("body") for match in SHELL_FENCE.finditer(text)]
    if len(canonical_blocks) != 1 or observed_blocks != canonical_blocks:
        errors.append(
            "LIVE_HANDOFF.md executable shell fence differs from the validator-owned canonical command sequence"
        )
    if ANGLE_SUBSTITUTION_TOKEN.search(text):
        errors.append("LIVE_HANDOFF.md contains a substitution marker instead of an exact command")
    if re.search(r"\$(?:\{)?SKILL_DIR\b", text):
        errors.append("LIVE_HANDOFF.md uses unresolved SKILL_DIR instead of a portable command")
    helper_contract_tokens = (
        "# Project Atlas helper resolution v1",
        "PROJECT_ATLAS_SCRIPT",
        "PROJECT_ATLAS_ROOT",
        "PROJECT_ATLAS_SEARCH_ROOTS",
        "PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS",
        "LC_ALL=C sort -u",
        "atlas_candidate_count",
    )
    if any(token not in text for token in helper_contract_tokens) or "-print -quit" in text:
        errors.append(
            "LIVE_HANDOFF.md does not implement the deterministic host-neutral helper resolution contract"
        )
    command_blocks = observed_blocks
    invocation_lines: list[list[str]] = []
    for block in command_blocks:
        for line in block.splitlines():
            if "$atlas_script" not in line or "python3" not in line:
                continue
            try:
                invocation_lines.append(shlex.split(line, posix=True))
            except ValueError:
                errors.append("LIVE_HANDOFF.md contains a non-parseable helper command")

    def validate_invocation(
        command: str,
        *,
        required_values: dict[str, str],
        required_flags: set[str] | None = None,
    ) -> None:
        candidates = [
            arguments
            for arguments in invocation_lines
            if len(arguments) >= 4 and arguments[3] == command
        ]
        if len(candidates) != 1:
            errors.append(f"LIVE_HANDOFF.md must contain exactly one executable atlas.py {command} command")
            return
        arguments = candidates[0]
        expected_prefix = ["PYTHONDONTWRITEBYTECODE=1", "python3", "$atlas_script", command]
        if arguments[:4] != expected_prefix:
            errors.append(
                f"LIVE_HANDOFF.md atlas.py {command} command has an invalid executable prefix"
            )
            return
        required_flags = required_flags or set()
        observed_values: dict[str, str] = {}
        observed_flags: set[str] = set()
        index = 4
        while index < len(arguments):
            option = arguments[index]
            if option in required_flags:
                if option in observed_flags:
                    errors.append(f"LIVE_HANDOFF.md atlas.py {command} repeats {option}")
                observed_flags.add(option)
                index += 1
                continue
            if option not in required_values or index + 1 >= len(arguments):
                errors.append(
                    f"LIVE_HANDOFF.md atlas.py {command} contains an unexpected argument"
                )
                return
            if option in observed_values:
                errors.append(f"LIVE_HANDOFF.md atlas.py {command} repeats {option}")
            observed_values[option] = arguments[index + 1]
            index += 2
        for option, expected_value in required_values.items():
            if observed_values.get(option) != expected_value:
                errors.append(
                    f"LIVE_HANDOFF.md atlas.py {command} command requires {option} {expected_value}"
                )
        for option in required_flags:
            if option not in observed_flags:
                errors.append(
                    f"LIVE_HANDOFF.md atlas.py {command} command is missing {option}"
                )

    validation_flags = {"--replay-command-evidence"} if mode == "FORENSIC" else set()
    validate_invocation(
        "validate",
        required_values={
            "--atlas": "$atlas_root",
            "--project": "$project_root",
            "--mode": mode,
        },
        required_flags=validation_flags,
    )
    if mode == "FORENSIC":
        validate_invocation(
            "snapshot",
            required_values={
                "--atlas": "$atlas_root",
                "--project": "$project_root",
                "--output": "$atlas_root/SOURCE_SNAPSHOT.json",
            },
        )
    return errors


def markdown_inline_links(
    text: str,
) -> Iterable[tuple[int, int, str, str]]:
    """Yield inline Markdown links with escaped and nested labels in one scan."""

    position = 0
    label_stack: list[int] = []
    escaped = False
    code_span_end: int | None = None
    top_level_code_span_end: int | None = None
    html_scan_disabled = False
    nested_link_found = False
    code_span_closes = markdown_code_span_close_map(text) if "`" in text else {}
    while position < len(text):
        character = text[position]
        if not label_stack and top_level_code_span_end is not None:
            if position < top_level_code_span_end:
                position += 1
                continue
            top_level_code_span_end = None
            continue
        if label_stack and code_span_end is not None:
            if position < code_span_end:
                position += 1
                continue
            code_span_end = None
            continue
        if escaped:
            escaped = False
            position += 1
            continue
        if character == "\\":
            escaped = True
            position += 1
            continue
        if not label_stack:
            if character == "`":
                run_end = markdown_backtick_run_end(text, position)
                close_end = code_span_closes.get(position)
                if close_end is None:
                    position = run_end
                    continue
                top_level_code_span_end = close_end
                position = run_end
                continue
            if character == "[":
                label_stack.append(markdown_inline_label_start(text, position))
                code_span_end = None
                nested_link_found = False
            position += 1
            continue
        if character == "`":
            run_end = markdown_backtick_run_end(text, position)
            close_end = code_span_closes.get(position)
            if close_end is None:
                position = run_end
                continue
            code_span_end = close_end
            position = run_end
            continue
        if character == "<" and not html_scan_disabled:
            inline_html_end = markdown_inline_html_end(text, position)
            if inline_html_end == -1:
                html_scan_disabled = True
            elif inline_html_end is not None:
                position = inline_html_end
                continue
        if character == "[":
            label_stack.append(position)
            position += 1
            continue
        if character != "]":
            position += 1
            continue

        if not label_stack:
            position += 1
            continue
        current_label_start = label_stack.pop()
        if (
            position + 1 >= len(text)
            or text[position + 1] != "("
        ):
            if not label_stack:
                nested_link_found = False
                code_span_end = None
                html_scan_disabled = False
            position += 1
            continue

        target_start = position + 2
        if target_start < len(text) and text[target_start] == ")":
            if not label_stack:
                escaped = False
                code_span_end = None
                html_scan_disabled = False
                nested_link_found = False
            position = target_start + 1
            continue
        destination_start, target_end, link_end, target_ok = markdown_inline_link_target(text, target_start)
        if not target_ok:
            if not label_stack:
                escaped = False
                code_span_end = None
                html_scan_disabled = False
                nested_link_found = False
            position = link_end
            continue
        if label_stack:
            yield (
                current_label_start,
                link_end + 1,
                text[
                    markdown_inline_label_content_start(
                        text, current_label_start
                    ) : position
                ],
                text[destination_start:target_end],
            )
            if text[current_label_start] != "!":
                nested_link_found = True
            position = link_end + 1
            continue
        if nested_link_found:
            nested_link_found = False
            escaped = False
            code_span_end = None
            html_scan_disabled = False
            position = link_end + 1
            continue
        if target_end > target_start:
            yield (
                current_label_start,
                link_end + 1,
                text[
                    markdown_inline_label_content_start(
                        text, current_label_start
                    ) : position
                ],
                text[destination_start:target_end],
            )
        escaped = False
        code_span_end = None
        html_scan_disabled = False
        nested_link_found = False
        position = link_end + 1


def markdown_inline_label_start(text: str, bracket_position: int) -> int:
    if bracket_position > 0 and text[bracket_position - 1] == "!":
        return bracket_position - 1
    return bracket_position


def markdown_inline_label_content_start(text: str, label_start: int) -> int:
    return label_start + 2 if text[label_start] == "!" else label_start + 1


def markdown_backtick_run_end(text: str, start: int) -> int:
    run_end = start
    while run_end < len(text) and text[run_end] == "`":
        run_end += 1
    return run_end


def markdown_code_span_close_map(text: str) -> dict[int, int]:
    opens: dict[int, int] = {}
    closes: dict[int, int] = {}
    position = 0
    while position < len(text):
        if text[position] != "`":
            position += 1
            continue
        run_end = markdown_backtick_run_end(text, position)
        run_length = run_end - position
        opener = opens.pop(run_length, None)
        if opener is None:
            opens[run_length] = position
        else:
            closes[opener] = run_end
        position = run_end
    return closes


def markdown_inline_html_end(text: str, start: int) -> int | None:
    if text.startswith("<!--", start):
        end = text.find("-->", start + 4)
        return -1 if end < 0 else end + 3
    if text.startswith("<?", start):
        end = text.find("?>", start + 2)
        return -1 if end < 0 else end + 2
    if text.startswith("<![CDATA[", start):
        end = text.find("]]>", start + 9)
        return -1 if end < 0 else end + 3
    if start + 2 < len(text) and text[start : start + 2] == "<!":
        end = text.find(">", start + 2)
        if end >= 0 and not any(character in {"\r", "\n"} for character in text[start:end]):
            return end + 1
        return -1
    tag = HTML_TAG.match(text, start)
    if tag is not None:
        return tag.end()
    return None


def markdown_inline_link_target(text: str, start: int) -> tuple[int, int, int, bool]:
    destination_start = start
    if destination_start < len(text) and text[destination_start] in {" ", "\t", "\n", "\r"}:
        destination_start = markdown_skip_inline_link_spaces(text, destination_start)
        if destination_start is None:
            return start, start, start, False
    target_end = markdown_inline_link_destination_end(text, destination_start)
    if target_end is None:
        resume = markdown_inline_link_destination_failure_resume(
            text, destination_start
        )
        return start, start, resume, False
    position = target_end
    if position < len(text) and text[position] in {" ", "\t", "\n", "\r"}:
        position = markdown_skip_inline_link_spaces(text, position)
        if position is None:
            return destination_start, target_end, target_end, False
        title_end = markdown_inline_link_title_end(text, position)
        if title_end is not None:
            position = markdown_skip_inline_link_spaces(text, title_end)
            if position is None:
                return destination_start, target_end, title_end, False
    if position < len(text) and text[position] == ")":
        return destination_start, target_end, position, True
    return destination_start, target_end, max(start + 1, position), False


def markdown_skip_inline_link_spaces(text: str, position: int) -> int | None:
    line_endings = 0
    while position < len(text) and text[position] in {" ", "\t", "\n", "\r"}:
        if text[position] in {"\n", "\r"}:
            line_endings += 1
            if line_endings > 1:
                return None
            if text[position] == "\r" and position + 1 < len(text) and text[position + 1] == "\n":
                position += 2
                continue
        position += 1
    return position


def markdown_inline_link_destination_end(text: str, start: int) -> int | None:
    if start >= len(text):
        return None
    if text[start] == "<":
        return markdown_angle_destination_end(text, start)

    escaped = False
    parentheses_depth = 0
    position = start
    while position < len(text):
        character = text[position]
        if character in {" ", "\t", "\r", "\n"}:
            break
        if ord(character) < 32 or ord(character) == 127:
            return None
        if escaped:
            escaped = False
            position += 1
            continue
        if character == "\\":
            escaped = True
            position += 1
            continue
        if character == "(":
            parentheses_depth += 1
        elif character == ")":
            if parentheses_depth == 0:
                break
            parentheses_depth -= 1
        position += 1
    if position == start or escaped or parentheses_depth:
        return None
    return position


def markdown_inline_link_destination_failure_resume(text: str, start: int) -> int:
    if start >= len(text):
        return start
    if text[start] == "<":
        position = start + 1
        while position < len(text) and text[position] not in {"\r", "\n", ">"}:
            position += 1
        return max(start + 1, position)
    escaped = False
    position = start
    while position < len(text):
        character = text[position]
        if character in {" ", "\t", "\r", "\n"}:
            break
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        position += 1
    return max(start + 1, position)


def markdown_inline_link_title_end(text: str, start: int) -> int | None:
    if start >= len(text) or text[start] not in {'"', "'", "("}:
        return None
    delimiter = text[start]
    closing = ")" if delimiter == "(" else delimiter
    escaped = False
    line_endings = 0
    position = start + 1
    while position < len(text):
        character = text[position]
        if character in {"\n", "\r"}:
            line_endings += 1
            if line_endings > 1:
                return None
            if character == "\r" and position + 1 < len(text) and text[position + 1] == "\n":
                position += 2
                continue
        if escaped:
            escaped = False
            position += 1
            continue
        if character == "\\":
            escaped = True
            position += 1
            continue
        if character == closing:
            return position + 1
        position += 1
    return None


def markdown_replace_inline_links(
    text: str,
    *,
    preserve_labels: bool,
) -> str:
    """Replace inline links while optionally retaining their rendered labels."""

    fragments: list[str] = []
    previous_end = 0
    for start, end, label, _target in markdown_inline_links(text):
        fragments.append(text[previous_end:start])
        fragments.append(label if preserve_labels else " ")
        previous_end = end
    fragments.append(text[previous_end:])
    return "".join(fragments)


def validate_internal_links(
    artifacts: ArtifactInventory,
    files: Iterable[str],
    inventory: SafeInventory,
) -> list[str]:
    errors: list[str] = []
    for name in files:
        if not name.endswith(".md"):
            continue
        relative_artifact = PurePosixPath(name)
        if artifact_state(artifacts, relative_artifact) != "regular":
            continue
        try:
            text = read_artifact_text(artifacts, relative_artifact)
        except AtlasError:
            continue
        for _start, _end, _label, target_text in markdown_inline_links(text):
            target_text = target_text.strip().strip("<>")
            if not target_text or target_text.startswith("#"):
                continue
            if target_text.lower().startswith("file:") or re.match(r"^[A-Za-z]:[\\/]", target_text):
                errors.append(f"{name} contains a forbidden local file link")
                continue
            if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target_text):
                continue
            project_source = parse_source_location(decoded_scan_value(target_text))
            if project_source is not None and project_source[0] in inventory.members:
                continue
            relative_target = target_text.split("#", 1)[0]
            if not relative_target:
                continue
            if "\\" in relative_target:
                errors.append(f"{name} contains a link outside the atlas")
                continue
            target = PurePosixPath(relative_target)
            if target.is_absolute() or ".." in target.parts:
                errors.append(f"{name} contains a link outside the atlas")
                continue
            combined = relative_artifact.parent / target
            if combined not in artifacts.members:
                errors.append(f"{name} contains a link outside expected atlas artifacts")
                continue
            state = artifact_state(artifacts, combined)
            if state == "symlink":
                errors.append(f"{name} contains a symbolic-link target")
                continue
            if state != "regular":
                errors.append(f"{name} contains a broken internal link")
    return errors


def atlas_is_untouched_scaffold(artifacts: ArtifactInventory, mode: str) -> bool:
    source_root = template_root() / mode.lower()
    for name in MODE_FILES[mode]:
        template = source_root / name
        try:
            if read_artifact_bytes(artifacts, PurePosixPath(name)) != template.read_bytes():
                return False
        except (AtlasError, OSError):
            return False
    return True


def untouched_scaffold_artifacts(
    artifacts: ArtifactInventory, mode: str
) -> list[str]:
    source_root = template_root() / mode.lower()
    untouched: list[str] = []
    for name in MODE_FILES[mode]:
        try:
            if read_artifact_bytes(artifacts, PurePosixPath(name)) == (
                source_root / name
            ).read_bytes():
                untouched.append(name)
        except (AtlasError, OSError):
            continue
    return untouched


def validate_standard_completion(
    artifacts: ArtifactInventory,
    inventory: SafeInventory,
    registries: dict[str, list[CanonicalTableRow]],
) -> list[str]:
    errors: list[str] = []
    source_root = template_root() / "standard"
    for name in MODE_FILES["STANDARD"]:
        if not name.endswith(".md"):
            continue
        relative = PurePosixPath(name)
        if artifact_state(artifacts, relative) != "regular":
            continue
        try:
            observed_text = read_artifact_text(artifacts, relative)
            template_text = (source_root / name).read_text(encoding="utf-8")
        except (AtlasError, OSError, UnicodeError):
            continue
        observed_sections, _observed_duplicates = level_two_sections(observed_text)
        template_sections, _template_duplicates = level_two_sections(template_text)
        for section, template_body in template_sections.items():
            is_static = (name, section) in STANDARD_STATIC_TEMPLATE_SECTIONS
            section_kind = "static" if is_static else "dynamic"
            section_candidates = [
                observed_section
                for observed_section in observed_sections
                if markdown_section_name_matches(observed_section, section)
            ]
            if not section_candidates:
                errors.append(
                    f"{name} is missing {section_kind} section ## {section}"
                )
                continue
            if len(section_candidates) != 1 or section_candidates[0] in _observed_duplicates:
                errors.append(
                    f"{name} has an ambiguous {section_kind} section extension for ## {section}"
                )
                continue
            if is_static:
                continue
            observed_body = observed_sections[section_candidates[0]]
            if not substantive_markdown_body(observed_body):
                errors.append(
                    f"{name} dynamic section ## {section} lacks substantive content"
                )
                continue
            template_without_fences = markdown_evidence_text(template_body)
            observed_without_fences = markdown_evidence_text(observed_body)
            template_prose = {
                line.strip()
                for line in commonmark_split_lines(
                    template_without_fences, keepends=False
                )
                if line.strip() and not line.lstrip().startswith("|")
            }
            observed_prose = [
                line.strip()
                for line in commonmark_split_lines(
                    observed_without_fences, keepends=False
                )
                if line.strip() and not line.lstrip().startswith("|")
            ]
            observed_normalized_prose = normalized_markdown_prose(
                "\n".join(observed_prose)
            )
            retained_prose = {
                template_line
                for template_line in template_prose
                if (
                    normalized_template_line := normalized_markdown_prose(
                        template_line
                    )
                )
                and normalized_template_line in observed_normalized_prose
            }
            template_table_lines = [
                line
                for line in commonmark_split_lines(
                    template_without_fences, keepends=False
                )
                if line.lstrip().startswith("|")
            ]
            observed_table_lines = [
                line
                for line in commonmark_split_lines(
                    observed_without_fences, keepends=False
                )
                if line.lstrip().startswith("|")
            ]
            empty_template_table_remains = bool(template_table_lines) and (
                len(observed_table_lines) <= len(template_table_lines)
            )
            if retained_prose or empty_template_table_remains:
                errors.append(
                    f"{name} scaffold section ## {section} retains canonical draft content"
                )

    for filename, source_column in STANDARD_CURRENT_SOURCE_COLUMNS.items():
        for row in registries.get(filename, []):
            values = row.values
            if values.get("Claim kind", "").upper() not in CURRENT_MATERIAL_CLAIM_KINDS:
                continue
            source_value = values.get(source_column, "")
            locations = markdown_source_locations(source_value, inventory.members)
            if not any(relative in inventory.members for relative, _start, _end in locations):
                errors.append(
                    f"{filename} line {row.line_number} current-material row requires a valid project-relative source in {source_column}"
                )
    return errors


def unexpected_reserved_artifacts(
    artifacts: ArtifactInventory, mode: str
) -> list[str]:
    expected = set(MODE_FILES[mode])
    if mode == "FORENSIC":
        expected.add("SOURCE_SNAPSHOT.json")
    unexpected: list[str] = []
    for name in sorted(GENERATED_FILE_NAMES - expected):
        if artifact_state(artifacts, PurePosixPath(name)) != "missing":
            unexpected.append(name)
    return unexpected


def validate_command(args: argparse.Namespace) -> int:
    atlas_value = args.atlas.expanduser()
    reject_symlink_components(atlas_value, "atlas path")
    if atlas_value.name in GENERATED_FILE_NAMES:
        atlas_root = require_directory(atlas_value.parent, "atlas")
    else:
        atlas_root = require_directory(atlas_value, "atlas")
    artifacts = build_artifact_inventory(atlas_root)
    mode = args.mode or detect_mode(artifacts)
    if args.project is None:
        raise AtlasError("--project is required for validate so project source references can be checked")
    project_root = require_directory(args.project.expanduser(), "project")
    safe_inventory = build_safe_inventory(project_root)
    errors: list[str] = []
    for name in unexpected_reserved_artifacts(artifacts, mode):
        errors.append(f"unexpected atlas artifact for {mode}: {name}")
    bounded_artifacts = list(MODE_FILES[mode])
    if artifact_state(artifacts, PurePosixPath("SOURCE_SNAPSHOT.json")) == "regular":
        bounded_artifacts.append("SOURCE_SNAPSHOT.json")
    errors.extend(validate_artifact_resource_bounds(artifacts, bounded_artifacts))
    registries: dict[str, list[CanonicalTableRow]] = {}
    if args.mode is not None:
        try:
            declared_mode = detect_mode(artifacts)
        except AtlasError as exc:
            errors.append(str(exc))
        else:
            if declared_mode != mode:
                errors.append(
                    f"declared atlas mode {declared_mode} does not match requested mode {mode}"
                )
    for name in MODE_FILES[mode]:
        relative = PurePosixPath(name)
        state = artifact_state(artifacts, relative)
        if state == "symlink":
            errors.append(f"required artifact is a symbolic link: {name}")
            continue
        if state != "regular":
            errors.append(f"missing required artifact: {name}")
            continue
        if name.endswith(".md"):
            try:
                text = read_artifact_text(artifacts, relative)
            except AtlasError:
                errors.append(f"cannot safely read required artifact: {name}")
                continue
            for marker in REQUIRED_MARKERS.get(name, ()):
                if marker not in text:
                    errors.append(f"missing required section or marker in {name}: {marker}")
            if contains_local_absolute_path(text):
                errors.append(f"{name} contains a local absolute path")
            if contains_secret_material(text):
                errors.append(f"{name} contains secret material")
            table_contract = (
                FORENSIC_TABLE_CONTRACTS.get(name)
                if mode == "FORENSIC"
                else TABLE_CONTRACTS.get(name)
            )
            if table_contract is not None:
                table_rows, table_errors = parse_table_contract(
                    name, text, table_contract, draft=args.draft
                )
                registries[name] = table_rows
                errors.extend(table_errors)
            if name == "LIVE_HANDOFF.md":
                errors.extend(validate_handoff_contract(text, mode))
            if name == "PROJECT_ATLAS.md" and mode == "QUICK" and not args.draft:
                errors.extend(validate_depth_decision(text, "Scope and Depth Rationale"))
                errors.extend(validate_quick_completion(text, safe_inventory))
            if name == "ATLAS_INDEX.md" and mode != "QUICK" and not args.draft:
                errors.extend(validate_depth_decision(text, "Scope and Coverage"))
    traceability_state = artifact_state(
        artifacts, PurePosixPath("TRACEABILITY.tsv")
    )
    trace_rows: list[list[str]] = []
    if mode == "FORENSIC" and traceability_state == "regular":
        try:
            trace_rows = read_traceability_rows(artifacts)
        except AtlasError as exc:
            errors.append(str(exc))
        else:
            errors.extend(
                validate_traceability_rows(
                    trace_rows,
                    inventory=safe_inventory,
                    replay_commands=args.replay_command_evidence,
                )
            )
            material_claims, material_errors = collect_material_claims(registries)
            errors.extend(material_errors)
            errors.extend(
                validate_registry_ledger_coverage(
                    material_claims, trace_rows, draft=args.draft
                )
            )
            if not args.draft:
                active_commands = [
                    record
                    for line_number, record in traceability_records(trace_rows)
                    if record.get("source_type") == "COMMAND"
                    and trace_record_is_completion_evidence(record, line_number)
                ]
                if not active_commands:
                    errors.append(
                        "FORENSIC completion requires at least one ACTIVE COMMAND evidence row"
                    )
    if mode == "FORENSIC" and not args.draft and not args.replay_command_evidence:
        errors.append(
            "FORENSIC completion requires --replay-command-evidence"
        )
    snapshot_relative = PurePosixPath("SOURCE_SNAPSHOT.json")
    snapshot_state = artifact_state(artifacts, snapshot_relative)
    snapshot_binding: SnapshotBinding | None = None
    if mode == "FORENSIC" and not args.draft and snapshot_state == "missing":
        errors.append("FORENSIC completion requires SOURCE_SNAPSHOT.json")
    if snapshot_state != "missing":
        if snapshot_state != "regular":
            errors.append("SOURCE_SNAPSHOT.json must be a regular non-symbolic file")
        else:
            try:
                snapshot_text = read_artifact_text(artifacts, snapshot_relative)
            except AtlasError:
                errors.append("SOURCE_SNAPSHOT.json cannot be read as UTF-8")
            else:
                if contains_local_absolute_path(snapshot_text):
                    errors.append("SOURCE_SNAPSHOT.json contains a local absolute path")
                if contains_secret_material(snapshot_text):
                    errors.append("SOURCE_SNAPSHOT.json contains secret material")
                if mode == "FORENSIC" and trace_rows:
                    snapshot_binding, snapshot_errors = validate_source_snapshot(
                        artifacts,
                        safe_inventory,
                        trace_rows,
                        require_source_evidence=not args.draft,
                    )
                    errors.extend(snapshot_errors)
    if mode == "FORENSIC":
        errors.extend(
            validate_independent_reviews(
                registries.get("LIVE_HANDOFF.md", []),
                draft=args.draft,
                snapshot_binding=snapshot_binding,
            )
        )
    errors.extend(validate_internal_links(artifacts, MODE_FILES[mode], safe_inventory))
    errors.extend(
        validate_project_source_references(
            artifacts, safe_inventory, MODE_FILES[mode]
        )
    )
    if mode == "STANDARD" and not args.draft:
        errors.extend(
            validate_standard_completion(artifacts, safe_inventory, registries)
        )
    if not args.draft:
        untouched = untouched_scaffold_artifacts(artifacts, mode)
        for name in untouched:
            errors.append(
                f"{name} remains an untouched draft scaffold; use --draft for structural validation"
            )
    if errors:
        for error in errors:
            print(f"atlas: {sanitize_diagnostic(error)}", file=sys.stderr)
        return 1
    sys.stdout.write(
        serialize_json_output(
            {
                "mode": mode,
                "status": "valid",
                "validation": "draft" if args.draft else "completion",
                "artifacts": len(MODE_FILES[mode]),
            }
        )
    )
    return 0


BACKTICK_TOKEN = re.compile(r"`([^`\n]+)`")
ANGLE_REFERENCE_TOKEN = re.compile(r"<([^<>\n]+)>")
NONSPACE_TOKEN = re.compile(r"\S+")
REFERENCE_DEFINITION = re.compile(
    r"(?:\A|(?<=[\r\n])) {0,3}\[[^\]\r\n]+\]:[ \t]*(?:(?:\r\n?|\n) {0,3})?"
    r"(?:<(?P<angle>[^<>\r\n]+)>|(?P<plain>\S+))",
)
MARKDOWN_LINK_TITLE = re.compile(
    r"(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))[ \t]*"
)


def markdown_reference_definition_label_end(candidate: str) -> int | None:
    if not candidate.startswith("["):
        return None
    escaped = False
    label_characters = 0
    position = 1
    while position < len(candidate):
        character = candidate[position]
        if character in {"\r", "\n"} or character < " ":
            return None
        if escaped:
            label_characters += 1
            if label_characters > 999:
                return None
            escaped = False
            position += 1
            continue
        if character == "\\":
            escaped = True
            position += 1
            continue
        if character == "[":
            return None
        if character == "]":
            if (
                label_characters == 0
                or not candidate[1:position].strip(" \t")
                or position + 1 >= len(candidate)
                or candidate[position + 1] != ":"
            ):
                return None
            return position + 2
        label_characters += 1
        if label_characters > 999:
            return None
        position += 1
    return None


def markdown_physical_line_end(text: str, start: int) -> int:
    position = start
    while position < len(text) and text[position] not in {"\r", "\n"}:
        position += 1
    return position


def markdown_next_line_start(text: str, line_end: int) -> int:
    if line_end >= len(text):
        return line_end
    if text[line_end] == "\r" and line_end + 1 < len(text) and text[line_end + 1] == "\n":
        return line_end + 2
    return line_end + 1


def markdown_reference_definition_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return complete top-level reference definitions, including title lines."""

    spans: list[tuple[int, int]] = []
    for match in REFERENCE_DEFINITION.finditer(text):
        destination_line_end = markdown_physical_line_end(text, match.end())
        remainder = text[match.end() : destination_line_end]
        if remainder.strip(" \t"):
            if MARKDOWN_LINK_TITLE.fullmatch(remainder.lstrip(" \t")) is None:
                continue
            spans.append((match.start(), destination_line_end))
            continue

        definition_end = destination_line_end
        title_line_start = markdown_next_line_start(text, destination_line_end)
        if title_line_start < len(text):
            title_line_end = markdown_physical_line_end(text, title_line_start)
            title_line = text[title_line_start:title_line_end]
            indentation = len(title_line) - len(title_line.lstrip(" "))
            if (
                indentation <= 3
                and MARKDOWN_LINK_TITLE.fullmatch(title_line[indentation:])
                is not None
            ):
                definition_end = title_line_end
        spans.append((match.start(), definition_end))
    return tuple(spans)


def markdown_reference_definition_remainder(candidate: str) -> str | None:
    opening_end = markdown_reference_definition_label_end(candidate)
    if opening_end is None:
        return None
    return candidate[opening_end:].lstrip(" \t")


def markdown_reference_destination_tail(value: str) -> str | None:
    if not value:
        return None
    if value.startswith("<"):
        destination_end = markdown_angle_destination_end(value, 0)
        if destination_end is None:
            return None
        if destination_end < len(value) and value[destination_end] not in {
            " ",
            "\t",
        }:
            return None
        return value[destination_end:].lstrip(" \t")
    destination_end = markdown_bare_destination_end(value)
    if destination_end == 0:
        return None
    return value[destination_end:].lstrip(" \t")


def markdown_bare_destination_end(value: str) -> int:
    escaped = False
    parentheses_depth = 0
    destination_end = 0
    while destination_end < len(value):
        character = value[destination_end]
        if character in {" ", "\t"}:
            break
        if ord(character) < 32 or ord(character) == 127:
            return 0
        if escaped:
            escaped = False
            destination_end += 1
            continue
        if character == "\\":
            escaped = True
            destination_end += 1
            continue
        if character == "(":
            parentheses_depth += 1
        elif character == ")":
            if parentheses_depth == 0:
                return 0
            parentheses_depth -= 1
        destination_end += 1
    if escaped or parentheses_depth != 0:
        return 0
    return destination_end


def markdown_angle_destination_end(value: str, start: int) -> int | None:
    if start >= len(value) or value[start] != "<":
        return None
    escaped = False
    position = start + 1
    while position < len(value):
        character = value[position]
        if character in {"\r", "\n"} or character < " ":
            return None
        if escaped:
            escaped = False
            position += 1
            continue
        if character == "\\":
            escaped = True
            position += 1
            continue
        if character == "<":
            return None
        if character == ">":
            return position + 1
        position += 1
    return None


def markdown_reference_title_delimiter(value: str) -> str | None:
    return value[0] if value and value[0] in {'"', "'", "("} else None


def markdown_reference_title_close_end(
    value: str,
    delimiter: str,
    *,
    opening_present: bool = True,
) -> int | None:
    closing = ")" if delimiter == "(" else delimiter
    escaped = False
    start = 1 if opening_present else 0
    for position, character in enumerate(value[start:], start=start):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if delimiter == "(" and character == "(":
            return None
        if character == closing:
            return position + 1
    return None


def markdown_reference_title_closes(
    value: str,
    delimiter: str,
    *,
    opening_present: bool = True,
) -> bool:
    return (
        markdown_reference_title_close_end(
            value,
            delimiter,
            opening_present=opening_present,
        )
        is not None
    )


def markdown_reference_title_invalid(
    value: str,
    delimiter: str,
    *,
    opening_present: bool = True,
) -> bool:
    if delimiter != "(":
        return False
    escaped = False
    start = 1 if opening_present else 0
    for character in value[start:]:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "(":
            return True
        if character == ")":
            return False
    return False


def markdown_reference_title_complete(
    value: str,
    delimiter: str,
    *,
    opening_present: bool = True,
) -> bool:
    close_end = markdown_reference_title_close_end(
        value,
        delimiter,
        opening_present=opening_present,
    )
    return close_end is not None and not value[close_end:].strip(" \t")


def markdown_reference_state_after_destination(
    value: str,
) -> tuple[str | None, str | None] | None:
    tail = markdown_reference_destination_tail(value)
    if tail is None:
        return None
    if not tail:
        return "optional-title", None
    delimiter = markdown_reference_title_delimiter(tail)
    if delimiter is None:
        return None
    if markdown_reference_title_invalid(tail, delimiter):
        return None
    if markdown_reference_title_complete(tail, delimiter):
        return None, None
    if markdown_reference_title_closes(tail, delimiter):
        return None
    return "title", delimiter


def markdown_reference_definition_line_valid(candidate: str) -> bool:
    remainder = markdown_reference_definition_remainder(candidate)
    if remainder is None:
        return False
    return not remainder or markdown_reference_state_after_destination(remainder) is not None


LABELED_SOURCE_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_])(?:source|evidence|file|path)[ \t]*[:=][ \t]+(?P<value>\S+)",
    re.IGNORECASE,
)
LINE_LABELED_SOURCE_REFERENCE = re.compile(
    r"^[ \t]{0,3}(?:source|evidence|file|path)[ \t]*[:=][ \t]+(?P<value>\S+)",
    re.IGNORECASE | re.MULTILINE,
)
STYLED_SOURCE_LABEL = re.compile(
    r"(?P<marker>\*\*|__|\*|_)(?P<label>source|evidence|file|path)"
    r"(?P<punctuation>[:=]?)(?P=marker)",
    re.IGNORECASE,
)
STANDALONE_LABELED_SOURCE_REFERENCE = re.compile(
    r"^[ \t]{0,3}(?:[-*+][ \t]+)?(?:source|evidence|file|path)"
    r"[ \t]*[:=]?[ \t]+(?P<value>\S+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
TABLE_CELL_LABELED_SOURCE_REFERENCE = re.compile(
    r"(?:^|\|)[ \t]*(?:source|evidence|file|path)[ \t]*[:=]?[ \t]+"
    r"(?P<value>[^|\s]+?)[ \t]*(?=\||$)",
    re.IGNORECASE | re.MULTILINE,
)
LIST_ITEM_MARKER = re.compile(r"(?:[-+*]|\d{1,9}[.)])(?P<spacing>[ \t]+)")
SOURCE_LINE_LOCATION = re.compile(
    r"(?:(?:[:#])L?(?P<start>\d+)(?:-L?(?P<end>\d+))?)$",
    re.IGNORECASE,
)


def normalize_source_location_token(raw_token: str) -> str:
    return raw_token.strip().strip("<>\"'`()[]{}").rstrip(".,;")


def source_location_candidate_values(raw_token: str) -> tuple[str, ...]:
    value = normalize_source_location_token(raw_token)
    if not value:
        return ()
    last_boundary_end: int | None = None
    for boundary in re.finditer(r"[\s<>()\[\]{}\"'`]+", value):
        last_boundary_end = boundary.end()
    if last_boundary_end is None:
        return (value,)
    suffix = normalize_source_location_token(value[last_boundary_end:])
    if not suffix or suffix == value:
        return (value,)
    return tuple(sorted((value, suffix)))


def embedded_unsafe_source_location(raw_token: str) -> str | None:
    """Return one boundary-delimited unsafe path start without suffix expansion."""

    value = decoded_scan_value(normalize_source_location_token(raw_token))
    if not value:
        return None

    def unsafe_start_at(start: int) -> bool:
        return (
            value.startswith(("../", "..\\", "~/", "~\\", "/", "\\\\"), start)
            or value[start : start + 5].casefold() == "file:"
            or (
                start + 2 < len(value)
                and value[start].isascii()
                and value[start].isalpha()
                and value[start + 1] == ":"
                and value[start + 2] in {"/", "\\"}
            )
        )

    if unsafe_start_at(0):
        return value
    for boundary in re.finditer(r"[\s<>()\[\]{}\"'`]+", value):
        start = boundary.end()
        if unsafe_start_at(start):
            return normalize_source_location_token(value[start:])
    return None


def markdown_column_after(text: str, initial: int = 0) -> int:
    column = initial
    for character in text:
        if character == "\t":
            column += 4 - (column % 4)
        else:
            column += 1
    return column


@dataclass(frozen=True)
class MarkdownCursor:
    position: int
    column: int
    virtual_spaces: int = 0


def commonmark_split_lines(text: str, *, keepends: bool) -> list[str]:
    """Split only on the CR, LF, and CRLF line endings defined by CommonMark."""

    lines: list[str] = []
    start = 0
    position = 0
    while position < len(text):
        character = text[position]
        if character == "\n":
            end = position + 1
        elif character == "\r":
            end = position + 2 if text[position + 1 : position + 2] == "\n" else position + 1
        else:
            position += 1
            continue
        lines.append(text[start:end] if keepends else text[start:position])
        start = end
        position = end
    if start < len(text):
        lines.append(text[start:])
    return lines


def markdown_cursor_text(body: str, cursor: MarkdownCursor) -> str:
    return " " * cursor.virtual_spaces + body[cursor.position :]


def markdown_scan_indentation(
    body: str, cursor: MarkdownCursor
) -> tuple[MarkdownCursor, int]:
    """Advance over logical whitespace without repeatedly copying suffixes."""

    start_column = cursor.column
    position = cursor.position
    current_column = cursor.column + cursor.virtual_spaces
    while position < len(body) and body[position] in {" ", "\t"}:
        current_column = markdown_column_after(body[position], current_column)
        position += 1
    return MarkdownCursor(position, current_column), current_column - start_column


def markdown_remove_cursor_indentation(
    body: str, cursor: MarkdownCursor, columns: int
) -> MarkdownCursor | None:
    """Remove indentation while retaining the unconsumed width of a tab."""

    if columns <= 0:
        return cursor
    position = cursor.position
    current_column = cursor.column
    virtual_spaces = cursor.virtual_spaces
    remaining_columns = columns
    if virtual_spaces:
        consumed = min(virtual_spaces, remaining_columns)
        virtual_spaces -= consumed
        current_column += consumed
        remaining_columns -= consumed
        if remaining_columns == 0:
            return MarkdownCursor(position, current_column, virtual_spaces)
    while remaining_columns:
        if position >= len(body) or body[position] not in {" ", "\t"}:
            return None
        next_column = markdown_column_after(body[position], current_column)
        character_columns = next_column - current_column
        position += 1
        if character_columns > remaining_columns:
            return MarkdownCursor(
                position,
                current_column + remaining_columns,
                character_columns - remaining_columns,
            )
        current_column = next_column
        remaining_columns -= character_columns
    return MarkdownCursor(position, current_column)


def markdown_cursor_starts_whitespace(body: str, cursor: MarkdownCursor) -> bool:
    return cursor.virtual_spaces > 0 or (
        cursor.position < len(body) and body[cursor.position] in {" ", "\t"}
    )


def markdown_non_whitespace_end(body: str) -> int:
    position = len(body)
    while position and body[position - 1] in {" ", "\t"}:
        position -= 1
    return position


def markdown_blockquote_marker_cursor(
    body: str, cursor: MarkdownCursor
) -> MarkdownCursor | None:
    marker_cursor, leading_columns = markdown_scan_indentation(body, cursor)
    if (
        leading_columns > 3
        or marker_cursor.position >= len(body)
        or body[marker_cursor.position] != ">"
    ):
        return None
    content_cursor = MarkdownCursor(
        marker_cursor.position + 1, marker_cursor.column + 1
    )
    if markdown_cursor_starts_whitespace(body, content_cursor):
        content_cursor = markdown_remove_cursor_indentation(
            body, content_cursor, 1
        )
        assert content_cursor is not None
    return content_cursor


def markdown_list_item_cursor(
    body: str, cursor: MarkdownCursor
) -> tuple[MarkdownCursor, int, bool] | None:
    marker_cursor, leading_columns = markdown_scan_indentation(body, cursor)
    if leading_columns > 3:
        return None
    marker = LIST_ITEM_MARKER.match(body, marker_cursor.position)
    if marker is None:
        return None
    spacing_start = marker.start("spacing")
    marker_column = markdown_column_after(
        body[marker_cursor.position : spacing_start], marker_cursor.column
    )
    content_column = markdown_column_after(
        marker.group("spacing"), marker_column
    )
    if content_column - marker_column <= 4:
        content_cursor = MarkdownCursor(marker.end(), content_column)
    else:
        # More than four columns after a marker means one column of list
        # padding; the remainder stays as logical content indentation.
        content_cursor = markdown_remove_cursor_indentation(
            body, MarkdownCursor(spacing_start, marker_column), 1
        )
        assert content_cursor is not None
    marker_text = body[marker_cursor.position : spacing_start]
    can_interrupt_paragraph = (
        content_cursor.position < markdown_non_whitespace_end(body)
        and (
            not marker_text[0].isdigit()
            or int(marker_text[:-1]) == 1
        )
    )
    return (
        content_cursor,
        content_cursor.column - cursor.column,
        can_interrupt_paragraph,
    )


def markdown_is_thematic_break(candidate: str, leading_columns: int) -> bool:
    return leading_columns <= 3 and (
        re.fullmatch(r"(?:\*[ \t]*){3,}", candidate) is not None
        or re.fullmatch(r"(?:_[ \t]*){3,}", candidate) is not None
        or re.fullmatch(r"(?:-[ \t]*){3,}", candidate) is not None
    )


def markdown_container_cursor_from(
    body: str,
    initial_cursor: MarkdownCursor,
) -> tuple[MarkdownCursor, tuple[tuple[str, int], ...], bool]:
    """Strip alternating CommonMark containers with monotonic source progress."""

    cursor = initial_cursor
    containers: list[tuple[str, int]] = []
    can_interrupt_paragraph = True
    interruption_decided = False
    thematic_totals: dict[str, int] | None = None
    thematic_last_invalid: dict[str, int] | None = None
    thematic_prefix = {"*": 0, "_": 0, "-": 0}
    thematic_prefix_position = 0

    def cursor_starts_thematic_break(candidate_cursor: MarkdownCursor) -> bool:
        nonlocal thematic_totals
        nonlocal thematic_last_invalid
        nonlocal thematic_prefix_position

        indented_cursor, leading_columns = markdown_scan_indentation(
            body, candidate_cursor
        )
        position = indented_cursor.position
        if (
            leading_columns > 3
            or position >= len(body)
            or body[position] not in thematic_prefix
        ):
            return False
        if thematic_totals is None or thematic_last_invalid is None:
            thematic_totals = {"*": 0, "_": 0, "-": 0}
            thematic_last_invalid = {"*": -1, "_": -1, "-": -1}
            for index, character in enumerate(body):
                if character in thematic_totals:
                    thematic_totals[character] += 1
                for marker in thematic_last_invalid:
                    if (
                        character != marker
                        and character != " "
                        and character != "\t"
                    ):
                        thematic_last_invalid[marker] = index
        while thematic_prefix_position < position:
            character = body[thematic_prefix_position]
            if character in thematic_prefix:
                thematic_prefix[character] += 1
            thematic_prefix_position += 1
        marker = body[position]
        return (
            position > thematic_last_invalid[marker]
            and thematic_totals[marker] - thematic_prefix[marker] >= 3
        )

    while True:
        if cursor_starts_thematic_break(cursor):
            break
        blockquote_cursor = markdown_blockquote_marker_cursor(body, cursor)
        if blockquote_cursor is not None:
            assert blockquote_cursor.position > cursor.position
            containers.append(("blockquote", 0))
            if not interruption_decided:
                can_interrupt_paragraph = True
                interruption_decided = True
            cursor = blockquote_cursor
            continue
        list_item = markdown_list_item_cursor(body, cursor)
        if list_item is None:
            break
        list_cursor, indentation, list_can_interrupt = list_item
        assert list_cursor.position > cursor.position
        containers.append(("list", indentation))
        if not interruption_decided:
            can_interrupt_paragraph = list_can_interrupt
            interruption_decided = True
        cursor = list_cursor
    return cursor, tuple(containers), can_interrupt_paragraph


def markdown_container_cursor(
    body: str,
) -> tuple[MarkdownCursor, tuple[tuple[str, int], ...]]:
    cursor, containers, _can_interrupt_paragraph = markdown_container_cursor_from(
        body, MarkdownCursor(0, 0)
    )
    return cursor, containers


def markdown_active_container_match(
    body: str,
    containers: tuple[tuple[str, int], ...],
    last_blockquote_index: int,
) -> tuple[
    MarkdownCursor | None,
    MarkdownCursor,
    tuple[tuple[str, int], ...],
]:
    """Return a full active-fence match plus any still-matched ancestors."""

    cursor = MarkdownCursor(0, 0)
    matched: list[tuple[str, int]] = []
    non_whitespace_end = markdown_non_whitespace_end(body)
    for index, (kind, indentation) in enumerate(containers):
        if kind == "blockquote":
            blockquote_cursor = markdown_blockquote_marker_cursor(body, cursor)
            if blockquote_cursor is None:
                return None, cursor, tuple(matched)
            cursor = blockquote_cursor
            matched.append((kind, indentation))
            continue
        if cursor.position >= non_whitespace_end:
            if last_blockquote_index > index:
                return None, cursor, tuple(matched)
            cursor, _columns = markdown_scan_indentation(body, cursor)
            return cursor, cursor, tuple(matched)
        list_cursor = markdown_remove_cursor_indentation(body, cursor, indentation)
        if list_cursor is None:
            return None, cursor, tuple(matched)
        cursor = list_cursor
        matched.append((kind, indentation))
    return cursor, cursor, tuple(matched)


def markdown_active_container_cursor(
    body: str,
    containers: tuple[tuple[str, int], ...],
    last_blockquote_index: int,
) -> MarkdownCursor | None:
    """Match the exact container sequence that opened an active fence."""

    active_cursor, _matched_cursor, _matched = markdown_active_container_match(
        body, containers, last_blockquote_index
    )
    return active_cursor


def markdown_fence_candidate(
    body: str, cursor: MarkdownCursor
) -> tuple[str, int]:
    indented_cursor, leading_columns = markdown_scan_indentation(body, cursor)
    candidate_cursor = indented_cursor if leading_columns <= 3 else cursor
    return markdown_cursor_text(body, candidate_cursor), leading_columns


def markdown_visible_line_starts_paragraph(
    body: str,
    cursor: MarkdownCursor,
    *,
    paragraph_was_active: bool,
) -> bool:
    candidate, leading_columns = markdown_fence_candidate(body, cursor)
    if not candidate.strip():
        return False
    if leading_columns <= 3 and re.match(r"#{1,6}(?:[ \t]+|$)", candidate):
        return False
    if markdown_is_thematic_break(candidate, leading_columns):
        return False
    if (
        leading_columns <= 3
        and markdown_reference_definition_line_valid(candidate)
    ):
        return paragraph_was_active
    if leading_columns <= 3 and re.fullmatch(r"(?:=+|-+)[ \t]*", candidate):
        return not paragraph_was_active
    return True


MARKDOWN_HTML_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "base",
        "basefont",
        "blockquote",
        "body",
        "caption",
        "center",
        "col",
        "colgroup",
        "dd",
        "details",
        "dialog",
        "dir",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "frame",
        "frameset",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hr",
        "html",
        "iframe",
        "legend",
        "li",
        "link",
        "main",
        "menu",
        "menuitem",
        "nav",
        "noframes",
        "ol",
        "optgroup",
        "option",
        "p",
        "param",
        "search",
        "section",
        "summary",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "title",
        "tr",
        "track",
        "ul",
    }
)
MARKDOWN_COMPLETE_HTML_TAG = re.compile(
    r"(?:"
    r"<[A-Za-z][A-Za-z0-9-]*"
    r"(?:[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*"
    r"(?:[ \t]*=[ \t]*(?:[^ \t\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*"
    r"[ \t]*/?>"
    r"|</[A-Za-z][A-Za-z0-9-]*[ \t]*>"
    r")"
)
MARKDOWN_LAZY_CONTINUATION_MARKER = "\0"


def markdown_html_block_start(candidate: str) -> tuple[str, str, bool] | None:
    type_one_tag = re.match(
        r"<([A-Za-z][A-Za-z0-9-]*)(?:[ \t]|>|$)",
        candidate,
    )
    if type_one_tag is not None and type_one_tag.group(1).casefold() in {
        "script",
        "pre",
        "style",
        "textarea",
    }:
        return "type-one", "", True
    raw_tag = re.match(
        r"</?([A-Za-z][A-Za-z0-9-]*)(?:[ \t]|/?>|$)",
        candidate,
    )
    if candidate.startswith("<!--"):
        return "token", "-->", True
    if candidate.startswith("<?"):
        return "token", "?>", True
    if candidate.startswith("<![CDATA["):
        return "token", "]]>", True
    if re.match(r"<![A-Z]", candidate):
        return "token", ">", True
    if raw_tag is not None and raw_tag.group(1).casefold() in MARKDOWN_HTML_BLOCK_TAGS:
        return "blank", "", True
    if MARKDOWN_COMPLETE_HTML_TAG.fullmatch(candidate.strip(" \t")) is not None:
        # CommonMark type 7 raw HTML blocks cannot interrupt a paragraph.
        return "blank", "", False
    return None


def markdown_html_block_ends(kind: str, token: str, candidate: str) -> bool:
    if kind == "blank":
        return not candidate.strip(" \t")
    if kind == "type-one":
        return (
            re.search(
                r"</(?:script|pre|style|textarea)>",
                candidate,
                re.IGNORECASE,
            )
            is not None
        )
    return token in candidate


def markdown_neutralize_line(
    line: str,
    start: int,
    *,
    preserve_html_tags: bool = False,
    html_tag_mask: bytearray | None = None,
    line_offset: int = 0,
) -> str:
    neutral = list(
        line[:start] + "".join(
            character if character in {"\r", "\n"} else " "
            for character in line[start:]
        )
    )
    if preserve_html_tags:
        body_end = len(line.rstrip("\r\n"))
        if html_tag_mask is None:
            for tag in HTML_TAG.finditer(line, start, body_end):
                neutral[tag.start() : tag.end()] = line[tag.start() : tag.end()]
        else:
            for position in range(start, body_end):
                if html_tag_mask[line_offset + position]:
                    neutral[position] = line[position]
    return "".join(neutral)


def markdown_evidence_text(
    text: str,
    *,
    preserve_html_tags: bool = True,
    preserve_reference_definitions: bool = False,
    mark_lazy_continuations: bool = False,
) -> str:
    """Neutralize non-rendered CommonMark code and raw HTML blocks."""

    evidence: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    fence_containers: tuple[tuple[str, int], ...] = ()
    fence_last_blockquote_index = -1
    html_kind: str | None = None
    html_token = ""
    html_containers: tuple[tuple[str, int], ...] = ()
    html_last_blockquote_index = -1
    paragraph_active = False
    continuation_containers: tuple[tuple[str, int], ...] = ()
    continuation_last_blockquote_index = -1
    lazy_container_paragraph = False
    reference_definition_state: str | None = None
    reference_definition_containers: tuple[tuple[str, int], ...] = ()
    reference_title_delimiter: str | None = None
    multiline_reference_lines: list[tuple[str, int]] = []
    multiline_reference_containers: tuple[tuple[str, int], ...] = ()
    multiline_reference_label_characters = 0
    multiline_reference_label_has_text = False
    multiline_reference_escaped = False
    html_tag_mask: bytearray | None = None
    if preserve_html_tags:
        html_tag_mask = bytearray(len(text))
        for tag in HTML_TAG.finditer(text):
            for position in range(tag.start(), tag.end()):
                html_tag_mask[position] = 1
    line_offset = 0
    for line in commonmark_split_lines(text, keepends=True):
        current_line_offset = line_offset
        line_offset += len(line)
        body = line.rstrip("\r\n")
        line_is_lazy = lazy_container_paragraph
        initial_cursor = MarkdownCursor(0, 0)
        inherited_containers: tuple[tuple[str, int], ...] = ()
        inherited_last_blockquote_index = -1
        if fence_character is not None:
            (
                active_cursor,
                matched_cursor,
                matched_containers,
            ) = markdown_active_container_match(
                body, fence_containers, fence_last_blockquote_index
            )
            if active_cursor is not None:
                candidate, leading_columns = markdown_fence_candidate(
                    body, active_cursor
                )
                if leading_columns <= 3 and re.fullmatch(
                    rf"{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                    candidate,
                ):
                    fence_character = None
                    fence_length = 0
                    fence_containers = ()
                    fence_last_blockquote_index = -1
                if line.endswith(("\n", "\r")):
                    evidence.append("\n")
                lazy_container_paragraph = False
                continue
            fence_character = None
            fence_length = 0
            fence_containers = ()
            fence_last_blockquote_index = -1
            initial_cursor = matched_cursor
            inherited_containers = matched_containers
            inherited_last_blockquote_index = max(
                (
                    index
                    for index, (kind, _indentation) in enumerate(
                        matched_containers
                    )
                    if kind == "blockquote"
                ),
                default=-1,
            )
        elif html_kind is not None:
            (
                active_cursor,
                matched_cursor,
                matched_containers,
            ) = markdown_active_container_match(
                body, html_containers, html_last_blockquote_index
            )
            if active_cursor is not None:
                active_text = markdown_cursor_text(body, active_cursor)
                evidence.append(
                    markdown_neutralize_line(
                        line,
                        active_cursor.position,
                        preserve_html_tags=preserve_html_tags,
                        html_tag_mask=html_tag_mask,
                        line_offset=current_line_offset,
                    )
                )
                if markdown_html_block_ends(html_kind, html_token, active_text):
                    html_kind = None
                    html_token = ""
                    html_containers = ()
                    html_last_blockquote_index = -1
                paragraph_active = False
                lazy_container_paragraph = False
                continue
            html_kind = None
            html_token = ""
            html_containers = ()
            html_last_blockquote_index = -1
            initial_cursor = matched_cursor
            inherited_containers = matched_containers
            inherited_last_blockquote_index = max(
                (
                    index
                    for index, (kind, _indentation) in enumerate(
                        matched_containers
                    )
                    if kind == "blockquote"
                ),
                default=-1,
            )
        elif continuation_containers:
            (
                continuation_cursor,
                matched_cursor,
                matched_containers,
            ) = markdown_active_container_match(
                body,
                continuation_containers,
                continuation_last_blockquote_index,
            )
            if continuation_cursor is not None:
                initial_cursor = continuation_cursor
                inherited_containers = continuation_containers
                inherited_last_blockquote_index = (
                    continuation_last_blockquote_index
                )
            else:
                if paragraph_active:
                    line_is_lazy = True
                initial_cursor = matched_cursor
                inherited_containers = matched_containers
                inherited_last_blockquote_index = max(
                    (
                        index
                        for index, (kind, _indentation) in enumerate(
                            matched_containers
                        )
                        if kind == "blockquote"
                    ),
                    default=-1,
                )

        (
            container_cursor,
            additional_containers,
            can_interrupt_paragraph,
        ) = markdown_container_cursor_from(body, initial_cursor)
        containers = inherited_containers + additional_containers
        continuation_containers = containers
        additional_last_blockquote_index = max(
            (
                index
                for index, (kind, _indentation) in enumerate(
                    additional_containers
                )
                if kind == "blockquote"
            ),
            default=-1,
        )
        continuation_last_blockquote_index = (
            len(inherited_containers) + additional_last_blockquote_index
            if additional_last_blockquote_index >= 0
            else inherited_last_blockquote_index
        )
        if paragraph_active and additional_containers:
            if not can_interrupt_paragraph:
                evidence.append(
                    MARKDOWN_LAZY_CONTINUATION_MARKER + line
                    if mark_lazy_continuations and line_is_lazy and body.strip()
                    else line
                )
                paragraph_active = True
                lazy_container_paragraph = line_is_lazy
                continue
            paragraph_active = False
            line_is_lazy = False
        candidate, leading_columns = markdown_fence_candidate(
            body, container_cursor
        )
        reference_line_consumed = False
        if multiline_reference_lines:
            multiline_invalid = False
            multiline_closed = False
            reference_remainder_from_label: str | None = None
            if containers != multiline_reference_containers or leading_columns > 3:
                multiline_invalid = True
            elif not candidate.strip(" \t"):
                multiline_invalid = True
            else:
                for index, character in enumerate(candidate):
                    if multiline_reference_escaped:
                        multiline_reference_label_characters += 1
                        multiline_reference_label_has_text = (
                            multiline_reference_label_has_text
                            or bool(character.strip(" \t"))
                        )
                        multiline_reference_escaped = False
                        continue
                    if character == "\\":
                        multiline_reference_escaped = True
                        continue
                    if character == "[" or ord(character) < 32 or ord(character) == 127:
                        multiline_invalid = True
                        break
                    if character == "]":
                        if index + 1 < len(candidate) and candidate[index + 1] == ":":
                            multiline_closed = True
                            reference_remainder_from_label = candidate[
                                index + 2 :
                            ].lstrip(" \t")
                        else:
                            multiline_invalid = True
                        break
                    multiline_reference_label_characters += 1
                    multiline_reference_label_has_text = (
                        multiline_reference_label_has_text
                        or bool(character.strip(" \t"))
                    )
                    if multiline_reference_label_characters > 999:
                        multiline_invalid = True
                        break
            if multiline_closed and (
                not multiline_reference_label_has_text
                or multiline_reference_label_characters > 999
                or multiline_reference_escaped
            ):
                multiline_invalid = True
            if multiline_invalid:
                for buffered_line, _buffered_start in multiline_reference_lines:
                    evidence.append(buffered_line)
                multiline_reference_lines = []
                multiline_reference_containers = ()
                multiline_reference_label_characters = 0
                multiline_reference_label_has_text = False
                multiline_reference_escaped = False
                paragraph_active = True
            elif not multiline_closed:
                multiline_reference_lines.append((line, container_cursor.position))
                lazy_container_paragraph = False
                continue
            else:
                multiline_reference_lines.append((line, container_cursor.position))
                reference_line_consumed = True
                if reference_remainder_from_label:
                    next_state = markdown_reference_state_after_destination(
                        reference_remainder_from_label
                    )
                    if next_state is None:
                        reference_line_consumed = False
                    else:
                        reference_definition_state, reference_title_delimiter = (
                            next_state
                        )
                else:
                    reference_definition_state = "destination"
                    reference_title_delimiter = None
                if reference_line_consumed:
                    for buffered_line, buffered_start in multiline_reference_lines:
                        evidence.append(
                            buffered_line
                            if preserve_reference_definitions
                            else markdown_neutralize_line(
                                buffered_line, buffered_start
                            )
                        )
                    paragraph_active = False
                    lazy_container_paragraph = False
                    multiline_reference_lines = []
                    multiline_reference_containers = ()
                    multiline_reference_label_characters = 0
                    multiline_reference_label_has_text = False
                    multiline_reference_escaped = False
                    continue

        if reference_definition_state is not None:
            if (
                containers != reference_definition_containers
                or leading_columns > 3
            ):
                reference_definition_state = None
                reference_definition_containers = ()
                reference_title_delimiter = None
            elif reference_definition_state == "destination":
                next_state = markdown_reference_state_after_destination(
                    candidate
                )
                if next_state is not None:
                    reference_definition_state, reference_title_delimiter = (
                        next_state
                    )
                    reference_line_consumed = True
                else:
                    reference_definition_state = None
                    reference_definition_containers = ()
                    reference_title_delimiter = None
            elif reference_definition_state == "optional-title":
                delimiter = markdown_reference_title_delimiter(candidate)
                if delimiter is not None:
                    reference_line_consumed = True
                    if markdown_reference_title_complete(candidate, delimiter):
                        reference_definition_state = None
                        reference_definition_containers = ()
                        reference_title_delimiter = None
                    elif markdown_reference_title_closes(candidate, delimiter):
                        reference_definition_state = None
                        reference_definition_containers = ()
                        reference_title_delimiter = None
                        reference_line_consumed = False
                    else:
                        reference_definition_state = "title"
                        reference_title_delimiter = delimiter
                else:
                    reference_definition_state = None
                    reference_definition_containers = ()
                    reference_title_delimiter = None
            elif candidate.strip() and reference_title_delimiter is not None:
                reference_line_consumed = True
                if markdown_reference_title_complete(
                    candidate,
                    reference_title_delimiter,
                    opening_present=False,
                ):
                    reference_definition_state = None
                    reference_definition_containers = ()
                    reference_title_delimiter = None
                elif markdown_reference_title_closes(
                    candidate,
                    reference_title_delimiter,
                    opening_present=False,
                ):
                    reference_definition_state = None
                    reference_definition_containers = ()
                    reference_title_delimiter = None
                    reference_line_consumed = False
            else:
                reference_definition_state = None
                reference_definition_containers = ()
                reference_title_delimiter = None

        reference_remainder = (
            markdown_reference_definition_remainder(candidate)
            if (
                not reference_line_consumed
                and reference_definition_state is None
                and not paragraph_active
                and leading_columns <= 3
            )
            else None
        )
        if reference_remainder is not None:
            reference_line_consumed = True
            reference_definition_containers = containers
            if not reference_remainder:
                reference_definition_state = "destination"
                reference_title_delimiter = None
            else:
                next_state = markdown_reference_state_after_destination(
                    reference_remainder
                )
                if next_state is None:
                    reference_definition_state = None
                    reference_definition_containers = ()
                    reference_title_delimiter = None
                    reference_line_consumed = False
                else:
                    reference_definition_state, reference_title_delimiter = (
                        next_state
                    )

        if (
            not reference_line_consumed
            and reference_definition_state is None
            and not paragraph_active
            and leading_columns <= 3
            and candidate.startswith("[")
            and "]" not in candidate
        ):
            prefix_invalid = False
            prefix_escaped = False
            prefix_characters = 0
            prefix_has_text = False
            for character in candidate[1:]:
                if prefix_escaped:
                    prefix_characters += 1
                    prefix_has_text = prefix_has_text or bool(character.strip(" \t"))
                    prefix_escaped = False
                    continue
                if character == "\\":
                    prefix_escaped = True
                    continue
                if character == "[" or ord(character) < 32 or ord(character) == 127:
                    prefix_invalid = True
                    break
                prefix_characters += 1
                prefix_has_text = prefix_has_text or bool(character.strip(" \t"))
                if prefix_characters > 999:
                    prefix_invalid = True
                    break
            if prefix_invalid:
                pass
            else:
                multiline_reference_lines = [(line, container_cursor.position)]
                multiline_reference_containers = containers
                multiline_reference_label_characters = prefix_characters
                multiline_reference_label_has_text = prefix_has_text
                multiline_reference_escaped = prefix_escaped
                lazy_container_paragraph = False
                continue

        if reference_line_consumed:
            evidence.append(
                line
                if preserve_reference_definitions
                else markdown_neutralize_line(
                    line, container_cursor.position
                )
            )
            paragraph_active = False
            lazy_container_paragraph = False
            continue
        opening = (
            re.match(r"(?P<fence>`{3,}|~{3,})(?P<info>.*)$", candidate)
            if leading_columns <= 3
            else None
        )
        if opening is not None and not (
            opening.group("fence").startswith("`")
            and "`" in opening.group("info")
        ):
            fence_character = opening.group("fence")[0]
            fence_length = len(opening.group("fence"))
            fence_containers = containers
            fence_last_blockquote_index = continuation_last_blockquote_index
            paragraph_active = False
            lazy_container_paragraph = False
            if line.endswith(("\n", "\r")):
                evidence.append("\n")
            continue
        html_cursor, html_leading_columns = markdown_scan_indentation(
            body, container_cursor
        )
        html_candidate = markdown_cursor_text(body, html_cursor)
        html_opening = (
            markdown_html_block_start(html_candidate)
            if html_leading_columns <= 3
            else None
        )
        if html_opening is not None:
            opening_kind, opening_token, can_interrupt_html_paragraph = html_opening
            if can_interrupt_html_paragraph or not paragraph_active:
                html_kind = opening_kind
                html_token = opening_token
                html_containers = containers
                html_last_blockquote_index = continuation_last_blockquote_index
                original_html_candidate = markdown_cursor_text(body, html_cursor)
                evidence.append(
                    markdown_neutralize_line(
                        line,
                        html_cursor.position,
                        preserve_html_tags=preserve_html_tags,
                        html_tag_mask=html_tag_mask,
                        line_offset=current_line_offset,
                    )
                )
                if markdown_html_block_ends(
                    html_kind, html_token, original_html_candidate
                ):
                    html_kind = None
                    html_token = ""
                    html_containers = ()
                    html_last_blockquote_index = -1
                paragraph_active = False
                lazy_container_paragraph = False
                continue
        if (
            not paragraph_active
            and markdown_remove_cursor_indentation(body, container_cursor, 4)
            is not None
        ):
            paragraph_active = False
            lazy_container_paragraph = False
            if line.endswith(("\n", "\r")):
                evidence.append("\n")
            continue
        paragraph_was_active = paragraph_active
        next_paragraph_active = markdown_visible_line_starts_paragraph(
            body,
            container_cursor,
            paragraph_was_active=paragraph_was_active,
        )
        evidence.append(
            MARKDOWN_LAZY_CONTINUATION_MARKER + line
            if (
                mark_lazy_continuations
                and line_is_lazy
                and next_paragraph_active
                and body.strip()
            )
            else line
        )
        paragraph_active = next_paragraph_active
        lazy_container_paragraph = line_is_lazy and paragraph_active
    return "".join(evidence)


def markdown_rendered_block_text(text: str) -> str:
    """Return visible Markdown block source with non-rendered blocks neutralized."""

    return markdown_evidence_text(
        text,
        preserve_html_tags=False,
        mark_lazy_continuations=True,
    )


def markdown_container_text(text: str) -> str:
    logical_lines: list[str] = []
    for line in commonmark_split_lines(text, keepends=True):
        body = line.rstrip("\r\n")
        line_ending = line[len(body) :]
        container_cursor, _containers = markdown_container_cursor(body)
        logical_lines.append(markdown_cursor_text(body, container_cursor) + line_ending)
    return "".join(logical_lines)


def reference_definition_destinations(text: str) -> tuple[str, ...]:
    return tuple(
        match.group("angle") or match.group("plain") or ""
        for match in REFERENCE_DEFINITION.finditer(text)
    )


def markdown_text_without_html_tags(text: str) -> str:
    """Return rendered Markdown text after removing raw tags and decoding entities."""

    return html.unescape(HTML_TAG.sub(" ", text))


def html_srcset_urls(value: str) -> tuple[str, ...]:
    """Extract srcset URLs without treating commas inside data URLs as separators."""

    urls: list[str] = []
    position = 0
    whitespace = " \t\n\r\f"
    while position < len(value):
        while position < len(value) and value[position] in whitespace + ",":
            position += 1
        if position >= len(value):
            break
        start = position
        while position < len(value) and value[position] not in whitespace:
            position += 1
        candidate = value[start:position]
        stripped = candidate.rstrip(",")
        if stripped:
            urls.append(stripped)
        if len(stripped) != len(candidate):
            continue
        parentheses = 0
        while position < len(value):
            character = value[position]
            position += 1
            if character == "(":
                parentheses += 1
            elif character == ")" and parentheses:
                parentheses -= 1
            elif character == "," and not parentheses:
                break
    return tuple(urls)


def html_source_attribute_values(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for tag_match in HTML_TAG.finditer(text):
        for attribute_match in HTML_ATTRIBUTE_VALUE.finditer(tag_match.group(0)):
            value = (
                attribute_match.group("double")
                or attribute_match.group("single")
                or attribute_match.group("bare")
                or ""
            )
            value = html.unescape(value)
            attribute_name = attribute_match.group("name").lower()
            if attribute_name in HTML_SRCSET_ATTRIBUTES:
                values.extend(html_srcset_urls(value))
                continue
            if attribute_name in HTML_SPACE_SEPARATED_URL_ATTRIBUTES:
                values.extend(value.split())
                continue
            if value and (
                attribute_name in HTML_SINGLE_URL_ATTRIBUTES
                or attribute_name.endswith(
                    ("-file", "-href", "-path", "-source", "-src", "-uri", "-url")
                )
                or unsafe_repository_reference(value)
            ):
                values.append(value)
    return tuple(values)


def markdown_label_text(text: str) -> str:
    return STYLED_SOURCE_LABEL.sub(
        lambda match: match.group("label") + match.group("punctuation"),
        text,
    )


def markdown_explicit_source_reference_tokens(text: str) -> tuple[str, ...]:
    evidence_text = markdown_container_text(
        markdown_evidence_text(text, preserve_reference_definitions=True)
    )
    tokens = list(html_source_attribute_values(evidence_text))
    html_neutral = markdown_label_text(markdown_text_without_html_tags(evidence_text))
    tokens.extend(BACKTICK_TOKEN.findall(html_neutral))
    tokens.extend(
        target
        for _start, _end, _label, target in markdown_inline_links(html_neutral)
    )
    tokens.extend(reference_definition_destinations(html_neutral))
    tokens.extend(ANGLE_REFERENCE_TOKEN.findall(html_neutral))
    tokens.extend(
        match.group("value") for match in LABELED_SOURCE_REFERENCE.finditer(html_neutral)
    )
    return tuple(
        sorted(
            {
                normalized
                for token in tokens
                if (normalized := normalize_source_location_token(token))
            }
        )
    )


def markdown_high_confidence_line_less_tokens(text: str) -> frozenset[str]:
    evidence_text = markdown_container_text(
        markdown_evidence_text(text, preserve_reference_definitions=True)
    )
    tokens = list(html_source_attribute_values(evidence_text))
    html_neutral = markdown_label_text(markdown_text_without_html_tags(evidence_text))
    tokens.extend(
        target
        for _start, _end, _label, target in markdown_inline_links(html_neutral)
    )
    tokens.extend(reference_definition_destinations(html_neutral))
    tokens.extend(ANGLE_REFERENCE_TOKEN.findall(html_neutral))
    tokens.extend(
        match.group("value") for match in LABELED_SOURCE_REFERENCE.finditer(html_neutral)
    )
    return frozenset(
        normalized
        for token in tokens
        if (normalized := normalize_source_location_token(token))
    )


def markdown_unambiguous_line_less_tokens(text: str) -> frozenset[str]:
    evidence_text = markdown_container_text(
        markdown_evidence_text(text, preserve_reference_definitions=True)
    )
    tokens = list(html_source_attribute_values(evidence_text))
    html_neutral = markdown_label_text(markdown_text_without_html_tags(evidence_text))
    tokens.extend(
        target
        for _start, _end, _label, target in markdown_inline_links(html_neutral)
    )
    tokens.extend(reference_definition_destinations(html_neutral))
    tokens.extend(ANGLE_REFERENCE_TOKEN.findall(html_neutral))
    tokens.extend(
        match.group("value")
        for match in LINE_LABELED_SOURCE_REFERENCE.finditer(html_neutral)
    )
    return frozenset(
        normalized
        for token in tokens
        if (normalized := normalize_source_location_token(token))
    )


def markdown_standalone_line_less_tokens(text: str) -> frozenset[str]:
    evidence_text = markdown_label_text(
        markdown_text_without_html_tags(
            markdown_container_text(
                markdown_evidence_text(
                    text, preserve_reference_definitions=True
                )
            )
        )
    )
    matches = (
        *STANDALONE_LABELED_SOURCE_REFERENCE.finditer(evidence_text),
        *TABLE_CELL_LABELED_SOURCE_REFERENCE.finditer(evidence_text),
    )
    return frozenset(
        normalized
        for match in matches
        if (normalized := normalize_source_location_token(match.group("value")))
    )


def is_path_shaped_line_less_source(relative: PurePosixPath) -> bool:
    return (
        len(relative.parts) > 1
        or relative.suffix.lower() in TEXT_SUFFIXES
        or relative.name.lower()
        in {
            "dockerfile",
            "gemfile",
            "makefile",
            "procfile",
        }
    )


def markdown_source_reference_tokens(text: str) -> tuple[str, ...]:
    evidence_text = markdown_container_text(
        markdown_evidence_text(text, preserve_reference_definitions=True)
    )
    tokens = list(markdown_explicit_source_reference_tokens(text))
    prose_text = markdown_label_text(markdown_text_without_html_tags(evidence_text))
    prose_text = BACKTICK_TOKEN.sub(" ", prose_text)
    prose_text = markdown_replace_inline_links(prose_text, preserve_labels=False)
    prose_text = REFERENCE_DEFINITION.sub(" ", prose_text)
    prose_text = ANGLE_REFERENCE_TOKEN.sub(" ", prose_text)
    prose_text = LABELED_SOURCE_REFERENCE.sub(" ", prose_text)
    tokens.extend(NONSPACE_TOKEN.findall(prose_text))
    return tuple(
        sorted(
            {
                normalized
                for token in tokens
                if (normalized := normalize_source_location_token(token))
            }
        )
    )


def parse_source_location(raw_token: str) -> tuple[PurePosixPath, int | None, int | None] | None:
    value = decoded_scan_value(normalize_source_location_token(raw_token))
    if not value or unsafe_repository_reference(value):
        return None
    if "\\" in value or any(ord(character) < 32 for character in value):
        return None
    line_match = SOURCE_LINE_LOCATION.search(value)
    location_text = value[: line_match.start()] if line_match is not None else value
    path_text = re.split(r"[?#]", location_text, maxsplit=1)[0]
    if not path_text or ":" in path_text or "#" in path_text:
        return None
    relative = PurePosixPath(path_text)
    if relative == PurePosixPath(".") or relative.is_absolute() or ".." in relative.parts:
        return None
    if relative.name in GENERATED_FILE_NAMES and len(relative.parts) == 1:
        return None
    start = int(line_match.group("start")) if line_match is not None else None
    end = (
        int(line_match.group("end"))
        if line_match is not None and line_match.group("end")
        else start
    )
    return relative, start, end


def markdown_source_locations(
    text: str,
    safe_inventory_members: frozenset[PurePosixPath],
) -> set[tuple[PurePosixPath, int | None, int | None]]:
    locations: set[tuple[PurePosixPath, int | None, int | None]] = set()
    explicit_tokens = frozenset(markdown_explicit_source_reference_tokens(text))
    high_confidence_tokens = markdown_high_confidence_line_less_tokens(text)
    unambiguous_tokens = markdown_unambiguous_line_less_tokens(text)
    standalone_tokens = markdown_standalone_line_less_tokens(text)
    for token in markdown_source_reference_tokens(text):
        parsed_candidates = {
            parsed
            for candidate in source_location_candidate_values(token)
            if (parsed := parse_source_location(candidate)) is not None
            and (
                parsed[1] is not None
                or parsed[0] in safe_inventory_members
                or token in unambiguous_tokens
                or token in standalone_tokens
                or (
                    token in high_confidence_tokens
                    and is_path_shaped_line_less_source(parsed[0])
                )
                or (
                    token in explicit_tokens
                    and "/" in parsed[0].as_posix()
                )
            )
        }
        inventory_candidates = {
            parsed for parsed in parsed_candidates if parsed[0] in safe_inventory_members
        }
        if inventory_candidates:
            locations.update(inventory_candidates)
        elif parsed_candidates:
            locations.add(
                min(
                    parsed_candidates,
                    key=lambda parsed: (
                        len(parsed[0].as_posix()),
                        parsed[0].as_posix(),
                        parsed[1] or 0,
                    ),
                )
            )
    return locations


def unsafe_markdown_source_references(text: str) -> set[str]:
    unsafe: set[str] = set()
    for candidate_text in {text, decoded_scan_value(text)}:
        for token in markdown_source_reference_tokens(candidate_text):
            candidates = source_location_candidate_values(token)
            embedded_candidate = embedded_unsafe_source_location(token)
            if embedded_candidate is not None and embedded_candidate not in candidates:
                candidates = (*candidates, embedded_candidate)
            for candidate in candidates:
                decoded = decoded_scan_value(normalize_source_location_token(candidate))
                if not decoded:
                    continue
                scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", decoded)
                windows_absolute = re.match(r"^[A-Za-z]:[\\/]", decoded)
                if (
                    scheme is not None
                    and scheme.group(0).lower() != "file:"
                    and windows_absolute is None
                ):
                    continue
                path = strip_source_location(decoded)
                if unsafe_repository_reference(decoded) and (
                    path.strip("/\\ \t")
                    and (
                        "/" in path
                        or "\\" in path
                        or SOURCE_LINE_LOCATION.search(decoded) is not None
                    )
                ):
                    unsafe.add(decoded)
    return unsafe


def validate_project_source_references(
    artifacts: ArtifactInventory, inventory: SafeInventory, files: Iterable[str]
) -> list[str]:
    errors: list[str] = []
    project_root = inventory.root
    observed: set[tuple[str, PurePosixPath, int | None, int | None]] = set()
    for name in files:
        if not name.endswith(".md"):
            continue
        artifact = PurePosixPath(name)
        if artifact_state(artifacts, artifact) != "regular":
            continue
        try:
            text = read_artifact_text(artifacts, artifact)
        except AtlasError:
            continue
        for unsafe_reference in sorted(unsafe_markdown_source_references(text)):
            errors.append(
                f"{name} contains an unsafe project source reference: {unsafe_reference}"
            )
        for relative, start, end in markdown_source_locations(text, inventory.members):
            observed.add((name, relative, start, end))

    if artifact_state(artifacts, PurePosixPath("TRACEABILITY.tsv")) == "regular":
        try:
            rows = read_traceability_rows(artifacts)
            if rows and tuple(rows[0]) == TRACEABILITY_HEADER:
                for row in rows[1:]:
                    if len(row) != len(TRACEABILITY_HEADER):
                        continue
                    record = dict(zip(TRACEABILITY_HEADER, (value.strip() for value in row)))
                    if record["source_type"] not in FILE_SOURCE_TYPES:
                        continue
                    parsed = parse_source_location(record["source_ref"])
                    if parsed is not None:
                        observed.add(("TRACEABILITY.tsv", *parsed))
        except AtlasError:
            pass

    for owner, relative, start, end in sorted(
        observed, key=lambda item: (item[0], item[1].as_posix(), item[2] or 0, item[3] or 0)
    ):
        rendered = relative.as_posix()
        if relative not in inventory.members:
            errors.append(f"{owner} references project source outside the safe inventory: {rendered}")
            continue
        if start is None:
            continue
        try:
            line_count = len(
                read_inventory_bytes(
                    inventory,
                    relative,
                    maximum_bytes=MAX_EVIDENCE_SOURCE_BYTES,
                )
                .decode("utf-8")
                .splitlines()
            )
        except (AtlasError, UnicodeError):
            errors.append(f"{owner} references non-text project source lines: {rendered}")
            continue
        assert end is not None
        if start < 1 or end < start or end > line_count:
            errors.append(
                f"{owner} references invalid line range {rendered}:L{start}-L{end}; file has {line_count} lines"
            )
    return errors


def infer_project_root(atlas_root: Path) -> Path:
    markers = (".git", ".gitignore", "Cargo.toml", "go.mod", "package.json", "pyproject.toml", "README.md")
    for candidate in (atlas_root, *atlas_root.parents):
        if any(
            not (candidate / marker).is_symlink() and (candidate / marker).exists()
            for marker in markers
        ):
            return candidate.resolve()
    return atlas_root.parent.resolve()


def path_crosses_symlink(root: Path, relative: PurePosixPath) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def split_review_material(text: str) -> tuple[bytes, list[list[str]]]:
    """Return exact non-review handoff bytes and canonical review table rows."""

    lines = text.splitlines(keepends=True)
    expected = FORENSIC_TABLE_CONTRACTS["LIVE_HANDOFF.md"]
    expected_normalized = tuple(value.casefold() for value in expected)
    header_index: int | None = None
    for index, line in enumerate(lines):
        cells = markdown_table_cells(line)
        if cells is not None and tuple(value.casefold() for value in cells) == expected_normalized:
            header_index = index
            break
    if header_index is None or header_index + 1 >= len(lines):
        raise AtlasError("LIVE_HANDOFF.md lacks the canonical independent review table")
    row_start = header_index + 2
    row_end = row_start
    rows: list[list[str]] = []
    while row_end < len(lines):
        cells = markdown_table_cells(lines[row_end])
        if cells is None:
            break
        rows.append(cells)
        row_end += 1
    without_rows = [*lines[:row_start], *lines[row_end:]]
    return "".join(without_rows).encode("utf-8"), rows


def canonical_traceability_partition(
    rows: list[list[str]], *, review_rows: bool
) -> tuple[bytes, int]:
    selected: list[list[str]] = [list(TRACEABILITY_HEADER)]
    count = 0
    for line_number, row in enumerate(rows[1:], start=2):
        if not row or all(not value.strip() for value in row):
            continue
        if len(row) != len(TRACEABILITY_HEADER):
            raise AtlasError("TRACEABILITY.tsv cannot be partitioned for review binding")
        raw_record = list(row)
        record = dict(zip(TRACEABILITY_HEADER, (value.strip() for value in raw_record)))
        atlas_refs, atlas_ref_errors = parse_atlas_refs(record["atlas_refs"], line_number)
        if atlas_ref_errors:
            raise AtlasError("TRACEABILITY.tsv has invalid atlas_refs for review binding")
        is_review = bool(atlas_refs) and all(
            atlas_ref.startswith(REVIEW_ATLAS_REF_PREFIX) for atlas_ref in atlas_refs
        )
        if is_review == review_rows:
            selected.append(raw_record)
            count += 1
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerows(selected)
    return stream.getvalue().encode("utf-8"), count


def review_binding_payload(
    artifacts: ArtifactInventory,
    trace_rows: list[list[str]],
    *,
    source_scope_sha256: str,
) -> tuple[dict[str, Any], str]:
    mode = detect_mode(artifacts)
    if mode == "FORENSIC":
        non_review_trace, non_review_count = canonical_traceability_partition(
            trace_rows, review_rows=False
        )
        review_trace, _review_trace_count = canonical_traceability_partition(
            trace_rows, review_rows=True
        )
    else:
        non_review_trace = b""
        non_review_count = 0
        review_trace = b""
    review_input_digest = hashlib.sha256()
    review_input_digest.update(b"project-atlas-review-input-v2\0")
    review_input_digest.update(b"source-scope-sha256\0")
    review_input_digest.update(source_scope_sha256.encode("ascii"))
    review_input_digest.update(b"\0")
    handoff_review_rows: list[list[str]] = []
    for name in MODE_FILES[mode]:
        relative = PurePosixPath(name)
        if artifact_state(artifacts, relative) != "regular":
            raise AtlasError(f"review binding requires a regular artifact: {name}")
        if mode == "FORENSIC" and name == "TRACEABILITY.tsv":
            content = non_review_trace
        elif mode == "FORENSIC" and name == "LIVE_HANDOFF.md":
            try:
                handoff_text = read_artifact_text(artifacts, relative)
            except AtlasError:
                raise AtlasError("LIVE_HANDOFF.md cannot be read for review binding") from None
            content, handoff_review_rows = split_review_material(handoff_text)
        else:
            content = read_artifact_bytes(artifacts, relative)
        review_input_digest.update(name.encode("utf-8"))
        review_input_digest.update(b"\0")
        review_input_digest.update(hashlib.sha256(content).digest())
        review_input_digest.update(b"\0")

    observed_times: list[datetime] = []
    for line_number, record in traceability_records(trace_rows):
        atlas_refs, atlas_ref_errors = parse_atlas_refs(record["atlas_refs"], line_number)
        if atlas_ref_errors or any(
            atlas_ref.startswith(REVIEW_ATLAS_REF_PREFIX) for atlas_ref in atlas_refs
        ):
            continue
        if record["status"].upper() not in ACTIVE_TRACE_STATUSES:
            continue
        observed = parse_evidence_timestamp(record["observed_at"])
        if observed is not None:
            observed_times.append(observed)
    observed_through = max(observed_times) if observed_times else None
    observed_text = (
        observed_through.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if observed_through is not None
        else None
    )

    review_records = json.dumps(
        {
            "handoff_rows": handoff_review_rows,
            "traceability": review_trace.decode("utf-8"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return (
        {
            "artifact_count": len(MODE_FILES[mode]),
            "non_review_traceability_rows": non_review_count,
            "evidence_observed_through": observed_text,
            "sha256": review_input_digest.hexdigest(),
        },
        hashlib.sha256(review_records).hexdigest(),
    )


def safe_inventory_path_manifest_sha256(inventory: SafeInventory) -> str:
    """Digest only allowlisted relative path names; never read unreferenced file contents."""
    digest = hashlib.sha256()
    for relative in inventory.ordered:
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def active_evidence_source_paths(
    rows: list[list[str]], inventory: SafeInventory
) -> tuple[PurePosixPath, ...]:
    paths: set[PurePosixPath] = set()
    command_paths: set[PurePosixPath] = set()
    for line_number, record in traceability_records(rows):
        if record["status"].upper() not in ACTIVE_TRACE_STATUSES:
            continue
        if record["source_type"] == "COMMAND":
            command_errors = validate_command_source(record, line_number)
            if command_errors:
                raise AtlasError(command_errors[0])
            plan, plan_errors = replay_command_plan(record, line_number, inventory)
            if plan is None:
                raise AtlasError(plan_errors[0])
            working_directory = lexical_absolute(
                inventory.root / Path(*plan.cwd_relative.parts)
            )
            for target in plan.targets:
                command_paths.update(
                    replay_target_members(inventory, working_directory, target)
                )
            continue
        if record["source_type"] not in FILE_SOURCE_TYPES:
            continue
        parsed = parse_source_location(record["source_ref"])
        if parsed is None:
            raise AtlasError("TRACEABILITY.tsv contains an invalid file source reference")
        relative, _start, _end = parsed
        if relative not in inventory.members:
            raise AtlasError(
                f"TRACEABILITY.tsv references project source outside the safe inventory: {relative.as_posix()}"
            )
        paths.add(relative)
    bounded_replay_member_sizes(inventory, command_paths)
    paths.update(command_paths)
    return tuple(sorted(paths, key=PurePosixPath.as_posix))


def build_source_snapshot_payload(
    artifacts: ArtifactInventory,
    inventory: SafeInventory,
    trace_rows: list[list[str]],
) -> dict[str, Any]:
    referenced = active_evidence_source_paths(trace_rows, inventory)
    files = [
        {"path": relative.as_posix(), "sha256": hash_inventory_file(inventory, relative)}
        for relative in referenced
    ]
    path_manifest_sha256 = safe_inventory_path_manifest_sha256(inventory)
    scope_digest = hashlib.sha256()
    scope_digest.update(SOURCE_SNAPSHOT_VERSION.encode("ascii"))
    scope_digest.update(b"\0")
    scope_digest.update(path_manifest_sha256.encode("ascii"))
    scope_digest.update(b"\0")
    for item in files:
        scope_digest.update(item["path"].encode("utf-8"))
        scope_digest.update(b"\0")
        scope_digest.update(item["sha256"].encode("ascii"))
        scope_digest.update(b"\0")
    traceability_state = artifact_state(artifacts, PurePosixPath("TRACEABILITY.tsv"))
    traceability_bytes = (
        read_artifact_bytes(artifacts, PurePosixPath("TRACEABILITY.tsv"))
        if traceability_state == "regular"
        else b""
    )
    source_scope_sha256 = scope_digest.hexdigest()
    review_input, review_records_sha256 = review_binding_payload(
        artifacts,
        trace_rows,
        source_scope_sha256=source_scope_sha256,
    )
    return {
        "schema_version": SOURCE_SNAPSHOT_VERSION,
        "safe_inventory": {
            "member_count": len(inventory.members),
            "excluded_count": inventory.excluded_count,
            "path_manifest_sha256": path_manifest_sha256,
        },
        "evidence_scope": {
            "unique_evidence_files": len(referenced),
            "hashed_files": len(files),
        },
        "review_input": review_input,
        "review_records_sha256": review_records_sha256,
        "traceability_sha256": hashlib.sha256(traceability_bytes).hexdigest(),
        "files": files,
        "sha256": source_scope_sha256,
    }


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def validate_snapshot_json_complexity(payload: Any) -> list[str]:
    nodes = 0
    stack: list[tuple[Any, int]] = [(payload, 1)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_SNAPSHOT_JSON_NODES:
            return ["SOURCE_SNAPSHOT.json exceeds the JSON node limit"]
        if depth > MAX_SNAPSHOT_JSON_DEPTH:
            return ["SOURCE_SNAPSHOT.json exceeds the JSON depth limit"]
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    return []


def validate_source_snapshot(
    artifacts: ArtifactInventory,
    inventory: SafeInventory,
    trace_rows: list[list[str]],
    *,
    require_source_evidence: bool,
) -> tuple[SnapshotBinding | None, list[str]]:
    relative = PurePosixPath("SOURCE_SNAPSHOT.json")
    try:
        text = read_artifact_text(artifacts, relative)
        payload = json.loads(text, object_pairs_hook=_strict_json_object)
    except (AtlasError, json.JSONDecodeError, ValueError, TypeError, RecursionError, MemoryError):
        return None, ["SOURCE_SNAPSHOT.json is not valid strict JSON"]
    if not isinstance(payload, dict):
        return None, ["SOURCE_SNAPSHOT.json must contain one JSON object"]
    try:
        expected = build_source_snapshot_payload(artifacts, inventory, trace_rows)
    except AtlasError as exc:
        return None, [str(exc)]

    errors: list[str] = []
    errors.extend(validate_snapshot_json_complexity(payload))
    expected_keys = {
        "schema_version",
        "safe_inventory",
        "evidence_scope",
        "review_input",
        "review_records_sha256",
        "traceability_sha256",
        "files",
        "sha256",
    }
    if set(payload) != expected_keys:
        errors.append("SOURCE_SNAPSHOT.json has an invalid v0.2 object shape")
    if payload.get("schema_version") != SOURCE_SNAPSHOT_VERSION:
        errors.append("SOURCE_SNAPSHOT.json has an unsupported schema_version")
    if payload.get("safe_inventory") != expected["safe_inventory"]:
        errors.append("SOURCE_SNAPSHOT.json safe inventory path manifest is stale")
    if payload.get("evidence_scope") != expected["evidence_scope"]:
        errors.append("SOURCE_SNAPSHOT.json evidence scope counts are stale")
    if require_source_evidence and not expected["files"]:
        errors.append(
            "SOURCE_SNAPSHOT.json completion requires at least one explicit project-source evidence file"
        )
    if payload.get("review_input") != expected["review_input"]:
        errors.append("SOURCE_SNAPSHOT.json canonical review input is stale")
    if payload.get("review_records_sha256") != expected["review_records_sha256"]:
        errors.append("SOURCE_SNAPSHOT.json review records digest is stale")
    actual_files = payload.get("files")
    expected_files = expected["files"]
    actual_paths = (
        [item.get("path") for item in actual_files if isinstance(item, dict)]
        if isinstance(actual_files, list)
        else []
    )
    expected_paths = [item["path"] for item in expected_files]
    if actual_paths != expected_paths:
        errors.append(
            "SOURCE_SNAPSHOT.json files must equal the exact active evidence source population"
        )
    elif actual_files != expected_files:
        errors.append("SOURCE_SNAPSHOT.json contains a stale project source hash")
    if payload.get("traceability_sha256") != expected["traceability_sha256"]:
        errors.append("SOURCE_SNAPSHOT.json traceability digest is stale")
    if payload.get("sha256") != expected["sha256"]:
        errors.append("SOURCE_SNAPSHOT.json scope digest is stale")
    snapshot_sha = payload.get("sha256")
    if not isinstance(snapshot_sha, str) or re.fullmatch(r"[0-9a-f]{64}", snapshot_sha) is None:
        errors.append("SOURCE_SNAPSHOT.json has an invalid sha256")
    review_input = expected["review_input"]
    review_sha = review_input.get("sha256")
    observed_text = review_input.get("evidence_observed_through")
    observed_through = (
        parse_evidence_timestamp(observed_text) if isinstance(observed_text, str) else None
    )
    if not isinstance(review_sha, str) or re.fullmatch(r"[0-9a-f]{64}", review_sha) is None:
        errors.append("SOURCE_SNAPSHOT.json has an invalid canonical review digest")
    if observed_through is None:
        errors.append("SOURCE_SNAPSHOT.json lacks a review evidence chronology boundary")
    elif evidence_timestamp_is_future(observed_through):
        errors.append("SOURCE_SNAPSHOT.json has a future review evidence chronology boundary")
    if not isinstance(review_sha, str) or observed_through is None:
        return None, errors
    return SnapshotBinding(expected["sha256"], review_sha, observed_through), errors


def snapshot_command(args: argparse.Namespace) -> int:
    atlas_root = require_directory(args.atlas.expanduser(), "atlas")
    artifacts = build_artifact_inventory(atlas_root)
    project_root = (
        require_directory(args.project.expanduser(), "project") if args.project is not None else infer_project_root(atlas_root)
    )
    inventory = build_safe_inventory(project_root)
    traceability = PurePosixPath("TRACEABILITY.tsv")
    traceability_state = artifact_state(artifacts, traceability)
    if traceability_state not in {"missing", "regular"}:
        raise AtlasError("TRACEABILITY.tsv must be a regular non-symbolic file")
    if traceability_state == "regular":
        rows = read_traceability_rows(artifacts)
        trace_errors = validate_traceability_rows(rows, inventory=inventory)
        if trace_errors:
            raise AtlasError(
                "TRACEABILITY.tsv is invalid; run atlas.py validate before creating a snapshot"
            )
    else:
        rows = [list(TRACEABILITY_HEADER)]
    payload = build_source_snapshot_payload(artifacts, inventory, rows)
    write_json(args.output.expanduser(), payload)
    return 0


def add_project_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_path", nargs="?", type=Path, help="project directory (alternative to --project)")
    parser.add_argument("--project", type=Path, help="project directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas.py",
        description="Select, initialize, inventory, validate, and snapshot a Project Atlas without network access.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    select_parser = subparsers.add_parser("select-mode", help="select QUICK, STANDARD, or FORENSIC")
    add_project_arguments(select_parser)
    select_parser.add_argument("--mode", type=parse_mode, help="explicit mode override")
    select_parser.add_argument("--production", action="store_true", help="mark the project as production-exposed")
    select_parser.add_argument("--critical", action="store_true", help="mark failure impact as critical")
    select_parser.add_argument("--sensitive-data", action="store_true", help="mark personal or sensitive data")
    select_parser.add_argument("--financial-data", action="store_true", help="mark financial data or effects")
    select_parser.add_argument("--automatic-decisions", action="store_true", help="mark automatic decisions")
    select_parser.add_argument("--legacy", action="store_true", help="mark legacy or overlapping implementations")
    select_parser.add_argument(
        "--runtime-count",
        type=parse_non_negative_count,
        default=0,
        help="known non-negative runtime-process count",
    )
    select_parser.add_argument(
        "--store-count",
        type=parse_non_negative_count,
        default=0,
        help="known non-negative state-store or writer count",
    )
    select_parser.add_argument(
        "--legacy-implementations",
        type=parse_non_negative_count,
        default=0,
        help="known non-negative legacy implementation count",
    )
    select_parser.add_argument(
        "--team-size",
        type=parse_non_negative_count,
        default=0,
        help="non-negative maintainer and agent count",
    )
    select_parser.add_argument("--authority-complexity", choices=("low", "medium", "high"))
    select_parser.add_argument("--expected-lifetime", choices=("short", "medium", "long"))
    select_parser.add_argument("--cost-of-error", choices=("low", "medium", "high", "critical"))
    select_parser.set_defaults(handler=select_mode_command)

    inventory_parser = subparsers.add_parser("inventory", help="write a safe structural inventory")
    add_project_arguments(inventory_parser)
    inventory_parser.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")
    inventory_parser.set_defaults(handler=inventory_command)

    init_parser = subparsers.add_parser("init", help="create only missing atlas files from mode templates")
    add_project_arguments(init_parser)
    init_parser.add_argument("--output", type=Path, help="atlas output directory")
    init_parser.add_argument("--mode", type=parse_mode, help="explicit mode override")
    init_parser.set_defaults(handler=init_command)

    validate_parser = subparsers.add_parser("validate", help="validate required artifacts, sections, and links")
    validate_parser.add_argument("--atlas", type=Path, required=True, help="atlas directory")
    validate_parser.add_argument(
        "--project", type=Path, help="project root required for validating source references"
    )
    validate_parser.add_argument("--mode", type=parse_mode, help="mode to validate; otherwise detect it")
    validate_parser.add_argument(
        "--draft",
        action="store_true",
        help="run structural draft validation; default validation enforces completion gates",
    )
    validate_parser.add_argument(
        "--replay-command-evidence",
        action="store_true",
        help=(
            "safely replay bounded rg COMMAND rows and compare exit codes and stdout digests; "
            "directory or multiple targets require exact --sort path"
        ),
    )
    validate_parser.set_defaults(handler=validate_command)

    snapshot_parser = subparsers.add_parser(
        "snapshot",
        help="hash active evidence source references and bounded command targets from the safe inventory",
    )
    snapshot_parser.add_argument("--atlas", type=Path, required=True, help="atlas directory")
    snapshot_parser.add_argument("--project", type=Path, help="project root; otherwise infer it")
    snapshot_parser.add_argument("--output", type=Path, required=True, help="deterministic JSON output file")
    snapshot_parser.set_defaults(handler=snapshot_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except AtlasError as exc:
        print(f"atlas: {sanitize_diagnostic(str(exc))}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:
        print(f"atlas: {sanitize_diagnostic(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
