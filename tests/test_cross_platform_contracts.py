from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import ATLAS_SCRIPT, REPO_ROOT, resolve_internal_link


def windows_junction_command(link: Path, target: Path) -> list[str]:
    return [
        "cmd.exe",
        "/d",
        "/c",
        "mklink",
        "/J",
        os.fspath(link),
        os.fspath(target),
    ]


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

    def test_windows_junction_command_keeps_cmd_tail_as_separate_arguments(
        self,
    ) -> None:
        link = Path(r"C:\Atlas junction test\relay")
        target = Path(r"C:\Atlas junction test\target")
        command = windows_junction_command(link, target)
        self.assertEqual(command[3:5], ["mklink", "/J"])
        serialized = subprocess.list2cmdline(command)
        self.assertNotIn(r"\"", serialized)
        self.assertIn(f'"{link}"', serialized)
        self.assertIn(f'"{target}"', serialized)

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_windows_junction_resolution_preserves_every_directory_hop(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()

        def create_junction(link: Path, target: Path) -> None:
            result = subprocess.run(
                windows_junction_command(link, target),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"unable to create Windows junction: {result.stderr}",
            )

        with tempfile.TemporaryDirectory(
            prefix="atlas Windows junction chain "
        ) as temp_dir:
            root = Path(temp_dir)
            repository = root / "repository"
            trusted_bin = root / "trusted-host" / "bin"
            repository.mkdir()
            trusted_bin.mkdir(parents=True)
            trusted_rg = trusted_bin / "rg.exe"
            trusted_rg.write_bytes(b"synthetic executable identity\n")
            repository_relay = repository / "directory-relay"
            external_alias = root / "external-directory-alias"
            create_junction(repository_relay, trusted_bin)
            create_junction(external_alias, repository_relay)

            self.assertTrue(
                atlas_module.executable_path_is_link(
                    repository_relay.lstat()
                )
            )
            chain = atlas_module.executable_symlink_chain(
                external_alias / "rg.exe"
            )
            self.assertTrue(
                any(
                    path.name == "directory-relay"
                    and path.parent.name == "repository"
                    for path in chain
                ),
                chain,
            )
            self.assertEqual(
                chain[-1].resolve(strict=True),
                trusted_rg.resolve(strict=True),
            )

    def test_windows_ci_runs_only_portable_sync_contracts(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        windows_job = workflow.split("  windows-python-contract:\n", 1)[1]
        portable_step = windows_job.split(
            "      - name: Verify generated adapter bundles\n", 1
        )[0]
        command_lines = {
            line.strip()
            for line in portable_step.splitlines()
            if line.startswith("          ")
            and not line.lstrip().startswith("#")
        }
        portable_contracts = (
            "tests.test_cross_platform_contracts",
            (
                "tests.test_atlas_security.AtlasSecurityRegressionTests."
                "test_host_executable_without_unix_identity_apis_fails_closed_portably"
            ),
            (
                "tests.test_atlas_security.AtlasSecurityRegressionTests."
                "test_host_executable_rejects_unsupported_platform_before_path_lookup"
            ),
            (
                "tests.test_sync_adapters.SyncAdaptersTransactionTests."
                "test_check_mode_is_state_free_when_scratch_is_absent"
            ),
            (
                "tests.test_sync_adapters.SyncAdaptersTransactionTests."
                "test_windows_clean_check_mode_does_not_report_inventory_change"
            ),
            (
                "tests.test_sync_adapters.SyncAdaptersTransactionTests."
                "test_check_mode_fails_closed_if_state_appears_during_scan"
            ),
            (
                "tests.test_sync_adapters.SyncAdaptersTransactionTests."
                "test_windows_write_mode_is_rejected_before_repository_lock"
            ),
            (
                "tests.test_sync_adapters.SyncAdaptersTransactionTests."
                "test_windows_promotion_fails_closed_without_handle_relative_rename"
            ),
        )
        for contract in portable_contracts:
            self.assertIn(contract, command_lines)
        self.assertNotIn("\n          tests.test_sync_adapters\n", portable_step)


if __name__ == "__main__":
    unittest.main()
