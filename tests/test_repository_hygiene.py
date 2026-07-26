from __future__ import annotations

import ast
import importlib.util
import re
import sys
import unittest
from pathlib import Path

from tests.support import ATLAS_SCRIPT, REPO_ROOT, iter_release_files, resolve_internal_link


TEXT_SUFFIXES = {
    "",
    ".md",
    ".py",
    ".sh",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".tsv",
    ".txt",
    ".gitignore",
}


def read_production_text(path: Path) -> str | None:
    if path.is_symlink():
        return None
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"LICENSE", ".gitignore"}:
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


class RepositoryHygieneTests(unittest.TestCase):
    def test_release_inventory_is_exactly_git_tracked_and_nonignored_files(self) -> None:
        release_paths = {
            path.relative_to(REPO_ROOT).as_posix() for path in iter_release_files()
        }
        required_public_surfaces = {
            "tests/test_fixture_contracts.py",
            "tests/fixtures/quick_cli/README.md",
            "tests/fixtures/standard_service/README.md",
            "tests/fixtures/forensic_legacy/README.md",
            "tests/oracles/quick_cli.json",
            "tests/oracles/standard_service.json",
            "tests/oracles/forensic_legacy.json",
        }
        self.assertTrue(
            required_public_surfaces <= release_paths,
            f"release hygiene omitted public test surfaces: {sorted(required_public_surfaces - release_paths)}",
        )
        self.assertFalse(
            any(".private" in Path(relative).parts for relative in release_paths),
            "release inventory contains ignored private paths; values redacted",
        )

    def test_release_inventory_contains_no_symbolic_links(self) -> None:
        offenders = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in iter_release_files()
            if path.is_symlink()
        ]
        self.assertEqual(
            offenders,
            [],
            "release inventory contains symbolic links; link targets were not opened: "
            + repr(offenders),
        )

    def test_release_inventory_contains_no_hardlinked_files(self) -> None:
        offenders = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in iter_release_files()
            if path.is_file() and path.stat(follow_symlinks=False).st_nlink != 1
        ]
        self.assertEqual(
            offenders,
            [],
            "release inventory contains hardlinked files; file contents were not opened: "
            + repr(offenders),
        )

    def test_internal_markdown_links_resolve(self) -> None:
        broken: list[str] = []
        inline_link = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
        reference_link = re.compile(r"(?m)^\[[^\]]+\]:\s+(\S+)")
        for markdown in (path for path in iter_release_files() if path.suffix.lower() == ".md"):
            if markdown.is_symlink():
                broken.append(f"{markdown.relative_to(REPO_ROOT).as_posix()} is a symbolic link")
                continue
            text = markdown.read_text(encoding="utf-8")
            targets = inline_link.findall(text) + reference_link.findall(text)
            for raw_target in targets:
                try:
                    resolved = resolve_internal_link(markdown, raw_target)
                except ValueError:
                    broken.append(
                        f"{markdown.relative_to(REPO_ROOT).as_posix()} -> {raw_target}"
                    )
                    continue
                if resolved is not None and not resolved.exists():
                    broken.append(f"{markdown.relative_to(REPO_ROOT).as_posix()} -> {raw_target}")
        self.assertEqual(broken, [], "broken internal Markdown links:\n" + "\n".join(broken))

    def test_production_has_no_todo_or_placeholder_tokens(self) -> None:
        unfinished_markers = (
            "TO" + "DO",
            "T" + "BD",
            "FIX" + "ME",
            "X" * 3,
            "PLACE" + "HOLDER",
            "CHANGE" + "ME",
        )
        pattern = re.compile(
            r"(?i)\b(?:" + "|".join(map(re.escape, unfinished_markers)) + r")\b"
        )
        offenders: list[str] = []
        for path in iter_release_files():
            text = read_production_text(path)
            if text is None:
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}")
        self.assertEqual(offenders, [], f"unfinished scaffold markers found: {offenders}")

    def test_production_has_no_secret_material_or_private_canaries(self) -> None:
        spec = importlib.util.spec_from_file_location("atlas_hygiene_subject", ATLAS_SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)  # type: ignore[union-attr]
        atlas_module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[spec.name] = atlas_module  # type: ignore[union-attr]
        self.addCleanup(sys.modules.pop, spec.name, None)  # type: ignore[union-attr]
        spec.loader.exec_module(atlas_module)  # type: ignore[union-attr]
        self.assertTrue(
            atlas_module.contains_secret_material("npm" + "_" + "A" * 24),
            "release hygiene must share the runtime scanner's package-token coverage",
        )
        private_canary = re.compile(r"ATLAS_TEST_SECRET_CANARY_[A-Z0-9_]+")
        offenders: list[str] = []
        for path in iter_release_files():
            text = read_production_text(path)
            if text is None:
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if atlas_module.contains_secret_material(line) or private_canary.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}")
        self.assertEqual(
            offenders,
            [],
            "possible secret material found in release files; matched values redacted: "
            + repr(offenders),
        )

    def test_production_has_no_accidental_local_paths_or_private_project_names(self) -> None:
        private_project_name = "Cry" + "stal"
        forbidden = re.compile(
            r"(?i)(?:/[U]sers/[^\s`'\"]+|/[h]ome/[^\s`'\"]+|[A-Z]:\\[U]sers\\|\b"
            + re.escape(private_project_name)
            + r"\b)"
        )
        offenders: list[str] = []
        for path in iter_release_files():
            text = read_production_text(path)
            if text is None:
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if forbidden.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_number}")
        self.assertEqual(offenders, [], f"local/private references found: {offenders}")

    def test_obsolete_codex_project_atlas_brand_is_absent(self) -> None:
        offenders: list[str] = []
        obsolete_slug = "codex" + "-project-atlas"
        obsolete_title = "Codex" + " Project Atlas"
        pattern = re.compile(
            rf"(?i)(?:{re.escape(obsolete_slug)}|{re.escape(obsolete_title)})"
        )
        for path in iter_release_files():
            text = read_production_text(path)
            if text is not None and pattern.search(text):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(offenders, [], f"obsolete product branding remains: {offenders}")

    def test_python_implementation_uses_only_standard_library_and_local_modules(self) -> None:
        python_files = [path for path in iter_release_files() if path.suffix == ".py"]
        local_modules = {path.stem for path in python_files}
        for path in python_files:
            relative = path.relative_to(REPO_ROOT)
            local_modules.update(relative.parts[:-1])
        allowed = set(getattr(sys, "stdlib_module_names", ())) | local_modules | {"__future__"}
        offenders: list[str] = []
        for path in python_files:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                self.fail(f"production Python has a syntax error in {path}: {exc}")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_level = alias.name.split(".", 1)[0]
                        if top_level not in allowed:
                            offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    module = node.module.split(".", 1)[0]
                    if module not in allowed:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {node.module}")
        self.assertEqual(offenders, [], "non-stdlib Python dependencies found:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
