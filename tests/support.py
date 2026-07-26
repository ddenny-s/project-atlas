from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import unquote


REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent
CORE_SKILL = REPO_ROOT / "core" / "skill" / "map-project"
ATLAS_SCRIPT = CORE_SKILL / "scripts" / "atlas.py"
CODEX_ADAPTER = REPO_ROOT / "adapters" / "codex"
CLAUDE_ADAPTER = REPO_ROOT / "adapters" / "claude-code"
FIXTURES_ROOT = TESTS_ROOT / "fixtures"
ORACLES_ROOT = TESTS_ROOT / "oracles"
MODES = ("QUICK", "STANDARD", "FORENSIC")


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def assert_file(testcase: Any, path: Path) -> None:
    testcase.assertTrue(path.is_file(), f"required file is missing: {display_path(path)}")


def assert_directory(testcase: Any, path: Path) -> None:
    testcase.assertTrue(path.is_dir(), f"required directory is missing: {display_path(path)}")


def read_text(testcase: Any, path: Path) -> str:
    assert_file(testcase, path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        testcase.fail(f"{display_path(path)} is not UTF-8: {exc}")
        raise AssertionError("unreachable")


def load_json(testcase: Any, path: Path) -> Any:
    text = read_text(testcase, path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        testcase.fail(f"invalid JSON in {display_path(path)}: {exc}")
        raise AssertionError("unreachable")


def load_oracle(testcase: Any, fixture_name: str) -> dict[str, Any]:
    payload = load_json(testcase, ORACLES_ROOT / f"{fixture_name}.json")
    testcase.assertIsInstance(payload, dict)
    testcase.assertEqual(payload.get("fixture"), fixture_name)
    return payload


def parse_frontmatter(testcase: Any, path: Path) -> tuple[dict[str, str], str]:
    text = read_text(testcase, path)
    lines = text.splitlines()
    testcase.assertGreaterEqual(len(lines), 3, f"{path} is too short for YAML frontmatter")
    testcase.assertEqual(lines[0].strip(), "---", f"{path} must start with YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        testcase.fail(f"{path} has no closing YAML frontmatter delimiter")
        raise AssertionError("unreachable")

    metadata: dict[str, str] = {}
    current_key: str | None = None
    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?$", line)
        if match:
            current_key = match.group(1)
            metadata[current_key] = (match.group(2) or "").strip().strip("\"'")
        elif current_key and (line.startswith(" ") or line.startswith("\t")):
            metadata[current_key] = f"{metadata[current_key]} {line.strip()}".strip()
        elif line.strip():
            testcase.fail(f"unsupported or invalid frontmatter line in {path}: {line!r}")
    return metadata, "\n".join(lines[end + 1 :]).lstrip()


def run_command(
    args: list[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(item) for item in args]
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        command,
        cwd=os.fspath(cwd or REPO_ROOT),
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def run_atlas(*args: str | os.PathLike[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return run_command([sys.executable, ATLAS_SCRIPT, *args], cwd=cwd)


def parse_mode_output(testcase: Any, result: subprocess.CompletedProcess[str]) -> str:
    testcase.assertEqual(
        result.returncode,
        0,
        f"select-mode failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
    )
    output = result.stdout.strip()
    testcase.assertTrue(output, "select-mode must print the selected mode")
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return output.upper()
    testcase.assertIsInstance(parsed, dict, "JSON select-mode output must be an object")
    mode = parsed.get("mode")
    testcase.assertIsInstance(mode, str, "JSON select-mode output must contain string field 'mode'")
    return mode.upper()


def iter_release_files(repository: Path = REPO_ROOT) -> Iterable[Path]:
    """Yield the exact tracked and non-ignored working-tree release candidates."""
    root = repository.resolve(strict=True)
    result = subprocess.run(
        [
            "git",
            "-C",
            os.fspath(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("cannot enumerate the Git release file set; command output redacted")

    for raw_relative in result.stdout.split(b"\0"):
        if not raw_relative:
            continue
        try:
            relative_text = raw_relative.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("release inventory contains a non-UTF-8 path; value redacted") from exc
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts or "\\" in relative_text:
            raise RuntimeError("release inventory contains an unsafe path; value redacted")
        candidate = root.joinpath(*relative.parts)
        try:
            candidate.absolute().relative_to(root)
        except ValueError as exc:
            raise RuntimeError("release inventory escaped the repository; value redacted") from exc
        if not candidate.exists() and not candidate.is_symlink():
            raise RuntimeError(f"release inventory path is missing: {relative.as_posix()}")
        if candidate.is_file() or candidate.is_symlink():
            yield candidate


def resolve_internal_link(
    source: Path,
    raw_target: str,
    *,
    repository: Path = REPO_ROOT,
) -> Path | None:
    """Resolve a local Markdown link while rejecting host-specific or escaping paths."""
    root = repository.resolve(strict=True)
    try:
        source.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ValueError("source document is outside the repository") from exc

    target = raw_target.strip().strip("<>")
    if "\n" in target:
        raise ValueError("link target contains a line break")
    target = target.rstrip("\r")
    if "\r" in target:
        raise ValueError("link target contains an embedded carriage return")
    target = target.split(maxsplit=1)[0] if target else ""
    if not target or target.startswith("#"):
        return None

    decoded = unquote(target.split("#", 1)[0])
    if not decoded:
        return None
    if decoded.startswith("\\\\") or "\\" in decoded:
        raise ValueError("Windows or UNC separators are not allowed in repository links")
    if PureWindowsPath(decoded).is_absolute() or PurePosixPath(decoded).is_absolute():
        raise ValueError("absolute repository links are not allowed")
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", decoded)
    if scheme:
        if scheme.group(1).lower() == "file":
            raise ValueError("file links are not allowed")
        return None
    if decoded.startswith("//"):
        return None

    candidate = source.parent.joinpath(*PurePosixPath(decoded).parts)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("repository link escapes through traversal or a symbolic link") from exc
    return resolved


def tree_digest(root: Path, *, excluded_names: set[str] | None = None) -> str:
    excluded_names = excluded_names or set()
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if any(part in excluded_names for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
