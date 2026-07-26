from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import ATLAS_SCRIPT, REPO_ROOT, resolve_internal_link


class CrossPlatformContractTests(unittest.TestCase):
    def load_atlas_subject(self):
        spec = importlib.util.spec_from_file_location(
            "atlas_cross_platform_subject", ATLAS_SCRIPT
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)  # type: ignore[union-attr]
        atlas_module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[spec.name] = atlas_module  # type: ignore[union-attr]
        self.addCleanup(sys.modules.pop, spec.name, None)  # type: ignore[union-attr]
        spec.loader.exec_module(atlas_module)  # type: ignore[union-attr]
        return atlas_module

    def test_atlas_cli_fails_cleanly_without_directory_descriptor_support(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas unsupported descriptor ") as temp_dir:
            with mock.patch.object(atlas_module.os, "supports_dir_fd", set()):
                with self.assertRaises(atlas_module.AtlasError) as raised:
                    atlas_module.open_directory_descriptor(Path(temp_dir))
        diagnostic = str(raised.exception)
        self.assertIn("unavailable on this platform", diagnostic)
        self.assertNotIn(temp_dir, diagnostic)

    def test_internal_links_reject_windows_drive_unc_and_backslash_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas cross platform links ") as temp_dir:
            root = Path(temp_dir)
            source = root / "docs" / "guide.md"
            source.parent.mkdir()
            source.write_text("guide\n", encoding="utf-8")

            separator = "\\"
            for target in (
                "C:" + separator + "Users" + separator + "person" + separator + "secret.md",
                separator * 2 + "server" + separator + "share" + separator + "secret.md",
                ".." + separator + "README.md",
            ):
                with self.subTest(target=target):
                    with self.assertRaises(ValueError):
                        resolve_internal_link(source, target, repository=root)

    @unittest.skipIf(os.name == "nt", "unprivileged Windows cannot reliably create symlinks")
    def test_internal_links_reject_a_symbolic_link_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas symlink link contract ") as temp_dir:
            parent = Path(temp_dir)
            root = parent / "repository"
            source = root / "docs" / "guide.md"
            source.parent.mkdir(parents=True)
            source.write_text("guide\n", encoding="utf-8")
            outside = parent / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            (root / "escaped.md").symlink_to(outside)

            with self.assertRaises(ValueError):
                resolve_internal_link(source, "../escaped.md", repository=root)

    def test_internal_links_handle_crlf_without_accepting_line_injection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas crlf links ") as temp_dir:
            root = Path(temp_dir)
            readme = root / "README.md"
            readme.write_text("readme\r\n", encoding="utf-8", newline="")
            source = root / "docs" / "guide.md"
            source.parent.mkdir()
            source.write_text("guide\r\n", encoding="utf-8", newline="")

            self.assertEqual(
                resolve_internal_link(source, "../README.md\r", repository=root),
                readme.resolve(),
            )
            for target in ("../README.md\r\nsecond.md", "../README.md\nsecond.md"):
                with self.subTest(target=target):
                    with self.assertRaises(ValueError):
                        resolve_internal_link(source, target, repository=root)

    def test_windows_ci_runs_only_portable_sync_contracts(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        windows_job = workflow.split("  windows-python-contract:\n", 1)[1]
        portable_step = windows_job.split(
            "      - name: Verify generated adapter bundles\n", 1
        )[0]
        portable_sync_tests = (
            "test_check_mode_is_state_free_when_scratch_is_absent",
            "test_check_mode_fails_closed_if_state_appears_during_scan",
            "test_windows_write_mode_is_rejected_before_repository_lock",
            "test_windows_promotion_fails_closed_without_handle_relative_rename",
        )
        for test_name in portable_sync_tests:
            self.assertIn(test_name, portable_step)
        self.assertNotIn("\n          tests.test_sync_adapters\n", portable_step)


if __name__ == "__main__":
    unittest.main()
