from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from tests.support import REPO_ROOT, run_command, tree_digest


class SyncAdaptersTransactionTests(unittest.TestCase):
    def make_clone(self, temp_dir: str) -> Path:
        clone = Path(temp_dir) / "project atlas"
        clone.mkdir()
        shutil.copytree(REPO_ROOT / "core", clone / "core")
        shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
        shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
        return clone.resolve()

    def load_sync(self, clone: Path):
        script = clone / "scripts" / "sync_adapters.py"
        spec = importlib.util.spec_from_file_location("atlas_sync_transaction_test", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def drift_both(self, clone: Path) -> None:
        for adapter in ("codex", "claude-code"):
            skill = clone / "adapters" / adapter / "skills" / "map-project" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nDRIFT\n", encoding="utf-8")

    def promote_prepared_plans(self, module, plans) -> None:
        for plan in plans:
            if plan.target_identity is not None:
                plan.target.rename(plan.previous)
            plan.staging.rename(plan.target)

    def inject_reused_identity(self, module, path: Path, expected):
        foreign = os.lstat(path)
        original = module._identity_from_stat

        def reused(details):
            if (
                details.st_dev == foreign.st_dev
                and details.st_ino == foreign.st_ino
                and stat.S_IFMT(details.st_mode) == expected.file_type
            ):
                return expected
            return original(details)

        return patch.object(module, "_identity_from_stat", reused)

    def inject_reused_directory_identity(self, module, path: Path, expected):
        return self.inject_reused_identity(module, path, expected)

    def test_repository_lock_rejects_a_concurrent_sync(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync lock ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            self.assertEqual(
                module.SYNC_LOCK_PATH,
                clone / ".scratch" / "sync-adapters.lock",
            )
            with module.repository_lock():
                blocked = run_command(
                    [sys.executable, clone / "scripts" / "sync_adapters.py", "--check"],
                    cwd=clone,
                )
            self.assertEqual(blocked.returncode, 2)
            self.assertEqual(
                blocked.stderr,
                "sync_adapters: another synchronization owns the repository lock\n",
            )

    def test_check_mode_is_state_free_when_scratch_is_absent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas state free check ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            self.assertFalse(module.SYNC_STATE_DIR.exists())

            def write_lock_must_not_run():
                raise AssertionError("check mode entered the write repository lock")

            with patch.object(module, "repository_lock", write_lock_must_not_run):
                drift = module.synchronize(check=True)

            self.assertIsInstance(drift, dict)
            self.assertFalse(
                module.SYNC_STATE_DIR.exists(),
                "--check created synchronization state",
            )

    def test_check_mode_fails_closed_if_state_appears_during_scan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas check state race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            original_inspect = module.inspect_drift
            raced = False

            def create_pending_state(target, directories, files):
                nonlocal raced
                drift = original_inspect(target, directories, files)
                if not raced:
                    raced = True
                    module.SYNC_STATE_DIR.mkdir()
                    module.SYNC_JOURNAL_PATH.write_text(
                        "foreign-pending-state\n",
                        encoding="utf-8",
                    )
                return drift

            with patch.object(module, "inspect_drift", create_pending_state):
                with self.assertRaisesRegex(module.SyncError, "state changed|journal"):
                    module.synchronize(check=True)

            self.assertTrue(raced)
            self.assertEqual(
                module.SYNC_JOURNAL_PATH.read_text(encoding="utf-8"),
                "foreign-pending-state\n",
            )

    def test_check_mode_rejects_orphan_reserved_state_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas check orphan state ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            module.SYNC_STATE_DIR.mkdir()
            ordinary = module.SYNC_STATE_DIR / "github-preview.css"
            ordinary.write_text("body {}\n", encoding="utf-8")
            orphan = module.SYNC_STATE_DIR / (
                "sync-adapters.cleanup-old-"
                + ("1" * 32)
                + "-"
                + ("2" * 32)
                + ".json"
            )
            orphan.write_text("foreign-orphan\n", encoding="utf-8")
            before = {
                path: (module.object_identity(path), path.read_bytes())
                for path in (ordinary, orphan)
            }

            with self.assertRaisesRegex(module.SyncError, "requires a recovery run"):
                module.synchronize(check=True)

            self.assertEqual(
                {
                    path: (module.object_identity(path), path.read_bytes())
                    for path in (ordinary, orphan)
                },
                before,
                "--check mutated ordinary or reserved synchronization state",
            )

    def test_check_mode_allows_ordinary_scratch_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas check ordinary state ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            module.SYNC_STATE_DIR.mkdir()
            ordinary = module.SYNC_STATE_DIR / "github-preview.css"
            ordinary.write_text("body {}\n", encoding="utf-8")
            before = (module.object_identity(ordinary), ordinary.read_bytes())

            self.assertEqual(module.synchronize(check=True), {})

            self.assertEqual(
                (module.object_identity(ordinary), ordinary.read_bytes()),
                before,
                "--check mutated an ordinary scratch artifact",
            )

    def test_check_mode_rejects_orphan_adapter_siblings_without_mutation(self) -> None:
        reserved_names = (
            ".map-project.sync-" + ("1" * 32) + "-" + ("2" * 32) + "-orphan",
            ".map-project.previous-" + ("1" * 32) + "-" + ("2" * 32),
            ".map-project.cleanup-old-" + ("1" * 32) + "-" + ("2" * 32),
            "..map-project.sync-orphan.cleanup-" + ("3" * 32),
        )
        for reserved_name in reserved_names:
            with self.subTest(reserved_name=reserved_name), tempfile.TemporaryDirectory(
                prefix="atlas check orphan sibling "
            ) as temp_dir:
                clone = self.make_clone(temp_dir)
                module = self.load_sync(clone)
                parent = module.ADAPTER_SKILLS["codex"].parent
                ordinary = parent / ".unrelated-skill-cache"
                ordinary.mkdir()
                orphan = parent / reserved_name
                orphan.mkdir()
                marker = orphan / "preserve.txt"
                marker.write_text("foreign-orphan\n", encoding="utf-8")
                before = {
                    path: module.object_identity(path)
                    for path in (ordinary, orphan, marker)
                }

                with self.assertRaisesRegex(module.SyncError, "requires a recovery run"):
                    module.synchronize(check=True)

                self.assertEqual(
                    {
                        path: module.object_identity(path)
                        for path in (ordinary, orphan, marker)
                    },
                    before,
                    "--check mutated an ordinary or reserved adapter sibling",
                )
                self.assertEqual(marker.read_text(encoding="utf-8"), "foreign-orphan\n")

    def test_check_mode_bounds_reserved_state_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas check bounded state ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            module.SYNC_STATE_DIR.mkdir()
            for index in range(3):
                (module.SYNC_STATE_DIR / f"ordinary-{index}.txt").write_text(
                    "preserve\n",
                    encoding="utf-8",
                )

            with patch.object(module, "MAX_TREE_ENTRIES", 2):
                with self.assertRaisesRegex(module.SyncError, "entry limit"):
                    module.synchronize(check=True)

    def test_tree_resource_limits_reject_depth_count_size_and_aggregate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync resource limits ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            for name in (
                "MAX_TREE_DEPTH",
                "MAX_TREE_DIRECTORIES",
                "MAX_TREE_FILES",
                "MAX_FILE_BYTES",
                "MAX_TOTAL_BYTES",
            ):
                self.assertTrue(hasattr(module, name), f"missing explicit ceiling: {name}")

            isolated = clone / "resource-tree"
            isolated.mkdir()
            isolated_identity = module.object_identity(isolated)
            assert isolated_identity is not None

            deep = isolated
            with patch.object(module, "MAX_TREE_DEPTH", 2):
                for index in range(3):
                    deep = deep / f"d{index}"
                    deep.mkdir()
                with self.assertRaisesRegex(module.SyncError, "depth limit"):
                    module.snapshot_tree(
                        isolated,
                        read_paths=None,
                        expected_root=isolated_identity,
                    )
            shutil.rmtree(isolated)
            isolated.mkdir()
            isolated_identity = module.object_identity(isolated)
            assert isolated_identity is not None

            for index in range(3):
                (isolated / f"f{index}.txt").write_text("x", encoding="utf-8")
            with patch.object(module, "MAX_TREE_FILES", 2):
                with self.assertRaisesRegex(module.SyncError, "file count limit"):
                    module.snapshot_tree(
                        isolated,
                        read_paths=None,
                        expected_root=isolated_identity,
                    )
            shutil.rmtree(isolated)
            isolated.mkdir()
            isolated_identity = module.object_identity(isolated)
            assert isolated_identity is not None

            oversized = isolated / "oversized.bin"
            with oversized.open("wb") as handle:
                handle.truncate(9)
            with patch.object(module, "MAX_FILE_BYTES", 8):
                with self.assertRaisesRegex(module.SyncError, "per-file byte limit"):
                    module.snapshot_tree(
                        isolated,
                        read_paths=None,
                        expected_root=isolated_identity,
                    )
            oversized.unlink()
            (isolated / "a.bin").write_bytes(b"123456")
            (isolated / "b.bin").write_bytes(b"abcdef")
            with patch.object(module, "MAX_TOTAL_BYTES", 10):
                with self.assertRaisesRegex(module.SyncError, "aggregate byte limit"):
                    module.snapshot_tree(
                        isolated,
                        read_paths=None,
                        expected_root=isolated_identity,
                    )

    def test_staging_inventory_limits_fail_before_materialization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas staging resource limits ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            staging = clone / "staging"
            staging.mkdir()

            with patch.object(module, "MAX_FILE_BYTES", 4):
                with self.assertRaisesRegex(module.SyncError, "per-file byte limit"):
                    module.build_tree(
                        staging,
                        set(),
                        {PurePosixPath("large.bin"): (b"12345", 0o600)},
                    )

            self.assertEqual(list(staging.iterdir()), [])

            with patch.object(module, "MAX_TREE_DIRECTORIES", 1):
                with self.assertRaisesRegex(module.SyncError, "directory count limit"):
                    module.build_tree(
                        staging,
                        {PurePosixPath("a"), PurePosixPath("a/b")},
                        {},
                    )
            self.assertEqual(list(staging.iterdir()), [])

    def test_all_staged_adapters_verify_before_the_first_target_move(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync stage verify ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }
            original_verify = module.verify_staged_tree
            verified: list[str] = []

            def failing_second_verification(plan) -> None:
                verified.append(plan.adapter)
                original_verify(plan)
                if plan.adapter == "claude-code":
                    raise module.SyncError("synthetic second-stage verification failure")

            with patch.object(module, "verify_staged_tree", failing_second_verification):
                with self.assertRaisesRegex(module.SyncError, "second-stage"):
                    module.synchronize(check=False)

            self.assertEqual(verified, ["codex", "claude-code"])
            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                before,
                "a target moved before every staged adapter verified",
            )

    def test_non_directory_adapter_target_is_rejected_before_staging(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync target fifo ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            target = module.ADAPTER_SKILLS["codex"]
            shutil.rmtree(target)
            os.mkfifo(target)
            staging_reached = False

            def forbidden_prepare(*args, **kwargs):
                nonlocal staging_reached
                staging_reached = True
                raise module.SyncError("staging was reached")

            with patch.object(module, "prepare_adapter_updates", forbidden_prepare):
                with self.assertRaisesRegex(
                    module.SyncError,
                    "adapter target must be a directory or absent",
                ):
                    module.synchronize(check=False)

            self.assertFalse(staging_reached)
            self.assertTrue(stat.S_ISFIFO(os.lstat(target).st_mode))
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())
            self.assertEqual(
                list(target.parent.glob(f".{target.name}.sync-*")),
                [],
            )

    def test_staging_directory_swap_is_rejected_before_child_materialization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas staging parent swap ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            self.assertTrue(
                hasattr(module, "verify_created_staging_entry"),
                "staging creation requires descriptor-relative identity verification",
            )
            staging = clone / "staging"
            staging.mkdir()
            displaced = clone / "displaced-staging-parent"
            foreign = staging / "nested" / "foreign-owner.txt"
            original_verify = module.verify_created_staging_entry
            swapped = False

            def swap_directory(parent_descriptor, parent_path, name, expected, label):
                nonlocal swapped
                if not swapped and name == "nested":
                    swapped = True
                    (staging / "nested").rename(displaced)
                    (staging / "nested").mkdir()
                    foreign.write_text("preserve\n", encoding="utf-8")
                return original_verify(
                    parent_descriptor,
                    parent_path,
                    name,
                    expected,
                    label,
                )

            with patch.object(
                module,
                "verify_created_staging_entry",
                swap_directory,
            ):
                with self.assertRaises(module.SyncError):
                    module.build_tree(
                        staging,
                        {PurePosixPath("nested")},
                        {PurePosixPath("nested/data.txt"): (b"expected\n", 0o644)},
                    )

            self.assertTrue(swapped)
            self.assertEqual(foreign.read_text(encoding="utf-8"), "preserve\n")
            self.assertFalse((staging / "nested" / "data.txt").exists())

    def test_staging_root_creation_is_descriptor_relative_to_verified_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas staging root parent swap ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            self.assertTrue(
                hasattr(module, "create_staging_root"),
                "staging root creation must be descriptor-relative",
            )
            parent = module.ADAPTER_SKILLS["codex"].parent
            parent_identity = module.object_identity(parent)
            assert parent_identity is not None
            displaced_parent = parent.with_name(parent.name + ".displaced")
            foreign_marker = parent / "foreign-owner.txt"
            original_verify = module.verify_created_staging_entry
            swapped = False

            def swap_parent(parent_descriptor, parent_path, name, expected, label):
                nonlocal swapped
                if not swapped and "staging root" in label:
                    swapped = True
                    parent.rename(displaced_parent)
                    parent.mkdir()
                    foreign_marker.write_text("preserve\n", encoding="utf-8")
                return original_verify(
                    parent_descriptor,
                    parent_path,
                    name,
                    expected,
                    label,
                )

            with patch.object(module, "verify_created_staging_entry", swap_parent):
                with self.assertRaises(module.SyncError):
                    module.create_staging_root(
                        parent,
                        parent_identity,
                        prefix=".map-project.sync-test-",
                    )

            self.assertTrue(swapped)
            self.assertEqual(foreign_marker.read_text(encoding="utf-8"), "preserve\n")
            self.assertEqual(
                list(parent.glob(".map-project.sync-test-*")),
                [],
                "staging root was created through the replacement parent path",
            )

    def test_staging_file_swap_is_rejected_after_descriptor_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas staging file swap ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            self.assertTrue(
                hasattr(module, "verify_created_staging_entry"),
                "staging creation requires descriptor-relative identity verification",
            )
            staging = clone / "staging"
            staging.mkdir()
            displaced = clone / "displaced-staging-file"
            output = staging / "data.txt"
            original_verify = module.verify_created_staging_entry
            swapped = False

            def swap_file(parent_descriptor, parent_path, name, expected, label):
                nonlocal swapped
                if not swapped and name == "data.txt":
                    swapped = True
                    output.rename(displaced)
                    output.write_text("foreign-preserve\n", encoding="utf-8")
                return original_verify(
                    parent_descriptor,
                    parent_path,
                    name,
                    expected,
                    label,
                )

            with patch.object(module, "verify_created_staging_entry", swap_file):
                with self.assertRaises(module.SyncError):
                    module.build_tree(
                        staging,
                        set(),
                        {PurePosixPath("data.txt"): (b"expected\n", 0o600)},
                    )

            self.assertTrue(swapped)
            self.assertEqual(output.read_text(encoding="utf-8"), "foreign-preserve\n")
            self.assertEqual(displaced.read_bytes(), b"expected\n")

    def test_hardlinked_files_are_rejected_in_canonical_drift_and_staging_trees(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync hardlinks ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)

            canonical_skill = module.CORE_SKILL / "SKILL.md"
            canonical_alias = module.CORE_SKILL / "SKILL.alias.md"
            os.link(canonical_skill, canonical_alias)
            with self.assertRaisesRegex(module.SyncError, "hard link"):
                module.canonical_tree()
            canonical_alias.unlink()

            expected = module.load_expected_adapters()["codex"]
            drift_target = module.ADAPTER_SKILLS["codex"]
            drift_alias = drift_target / "SKILL.alias.md"
            os.link(drift_target / "SKILL.md", drift_alias)
            with self.assertRaisesRegex(module.SyncError, "hard link"):
                module.inspect_drift(drift_target, *expected)
            drift_alias.unlink()

            staging = clone / "hardlinked-staging"
            staging.mkdir()
            staged = staging / "data.txt"
            staged.write_text("data\n", encoding="utf-8")
            os.link(staged, staging / "data.alias.txt")
            staging_identity = module.object_identity(staging)
            assert staging_identity is not None
            with self.assertRaisesRegex(module.SyncError, "hard link"):
                module.snapshot_tree(
                    staging,
                    read_paths=None,
                    expected_root=staging_identity,
                )

    def test_staging_write_rejects_a_hardlink_created_before_post_write_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas staging hardlink race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            self.assertTrue(hasattr(module, "verify_created_staging_entry"))
            staging = clone / "staging"
            staging.mkdir()
            alias = staging / "data.alias.txt"
            original_verify = module.verify_created_staging_entry
            linked = False

            def add_hardlink(parent_descriptor, parent_path, name, expected, label):
                nonlocal linked
                if not linked and name == "data.txt":
                    linked = True
                    os.link(staging / "data.txt", alias)
                return original_verify(
                    parent_descriptor,
                    parent_path,
                    name,
                    expected,
                    label,
                )

            with patch.object(module, "verify_created_staging_entry", add_hardlink):
                with self.assertRaisesRegex(module.SyncError, "hard link"):
                    module.build_tree(
                        staging,
                        set(),
                        {PurePosixPath("data.txt"): (b"expected\n", 0o600)},
                    )

            self.assertTrue(linked)

    def test_journal_publication_never_overwrites_a_foreign_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas journal publication race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            original_rename = module.rename_noreplace
            raced = False

            def occupy_journal(source, destination, parent_identity):
                nonlocal raced
                if not raced and destination == module.SYNC_JOURNAL_PATH:
                    raced = True
                    destination.write_text("foreign-preserve\n", encoding="utf-8")
                return original_rename(source, destination, parent_identity)

            with patch.object(module, "rename_noreplace", occupy_journal):
                with self.assertRaises(module.SyncError):
                    module.write_transaction_journal(plans, phase="prepared")

            self.assertTrue(raced)
            self.assertEqual(
                module.SYNC_JOURNAL_PATH.read_text(encoding="utf-8"),
                "foreign-preserve\n",
            )
            module.SYNC_JOURNAL_PATH.unlink()
            module._discard_prepared_staging(plans)

    def test_journal_removal_never_deletes_a_foreign_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas journal removal race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            journal_identity = module.write_transaction_journal(plans, phase="prepared")
            displaced = module.SYNC_JOURNAL_PATH.with_name("journal.displaced")
            original_rename = module.rename_noreplace
            raced = False
            foreign_signature = None
            original_identity = module._identity_from_stat

            def swap_journal(source, destination, parent_identity):
                nonlocal raced, foreign_signature
                if not raced and source == module.SYNC_JOURNAL_PATH:
                    raced = True
                    source.rename(displaced)
                    source.write_text("foreign-preserve\n", encoding="utf-8")
                    os.chmod(source, 0o600)
                    details = os.lstat(source)
                    foreign_signature = (details.st_dev, details.st_ino)
                return original_rename(source, destination, parent_identity)

            def reuse_journal_identity(details):
                if foreign_signature == (details.st_dev, details.st_ino):
                    return journal_identity
                return original_identity(details)

            with patch.object(module, "_identity_from_stat", reuse_journal_identity), patch.object(
                module, "rename_noreplace", swap_journal
            ):
                with self.assertRaises(module.SyncError):
                    module._remove_journal(journal_identity)

            self.assertTrue(raced)
            self.assertEqual(
                module.SYNC_JOURNAL_PATH.read_text(encoding="utf-8"),
                "foreign-preserve\n",
            )
            self.assertEqual(
                list(module.SYNC_STATE_DIR.glob(".sync-adapters.journal.json.quarantine-*")),
                [],
            )
            module.SYNC_JOURNAL_PATH.unlink()
            displaced.rename(module.SYNC_JOURNAL_PATH)
            module.recover_pending_transaction()

    def test_receipt_removal_revalidates_bytes_after_a_reused_identity_race(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas receipt removal race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            prepared = module.write_transaction_journal(plans, phase="prepared")
            self.promote_prepared_plans(module, plans)
            receipt_identity = module.write_commit_receipt(plans, prepared)
            displaced = module.SYNC_RECEIPT_PATH.with_name("receipt.displaced")
            original_rename = module.rename_noreplace
            original_identity = module._identity_from_stat
            foreign_signature = None
            raced = False

            def swap_receipt(source, destination, parent_identity):
                nonlocal foreign_signature, raced
                if not raced and source == module.SYNC_RECEIPT_PATH:
                    raced = True
                    source.rename(displaced)
                    source.write_text("foreign-receipt-preserve\n", encoding="utf-8")
                    os.chmod(source, 0o600)
                    details = os.lstat(source)
                    foreign_signature = (details.st_dev, details.st_ino)
                return original_rename(source, destination, parent_identity)

            def reuse_receipt_identity(details):
                if foreign_signature == (details.st_dev, details.st_ino):
                    return receipt_identity
                return original_identity(details)

            with patch.object(module, "_identity_from_stat", reuse_receipt_identity), patch.object(
                module, "rename_noreplace", swap_receipt
            ):
                with self.assertRaises(module.SyncError):
                    module._remove_receipt(receipt_identity)

            self.assertTrue(raced)
            self.assertEqual(
                module.SYNC_RECEIPT_PATH.read_text(encoding="utf-8"),
                "foreign-receipt-preserve\n",
            )
            self.assertEqual(
                list(module.SYNC_STATE_DIR.glob(".sync-adapters.commit.json.quarantine-*")),
                [],
            )
            self.assertTrue(displaced.is_file())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())

    def test_windows_write_mode_is_rejected_before_repository_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas windows early failure ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)

            def lock_must_not_run():
                raise AssertionError("repository lock was entered")

            with patch.object(module.os, "name", "nt"), patch.object(
                module,
                "repository_lock",
                lock_must_not_run,
            ):
                with self.assertRaisesRegex(
                    module.SyncError,
                    "Windows synchronization is disabled",
                ):
                    module.synchronize(check=False)

            self.assertFalse(module.SYNC_STATE_DIR.exists())

    def test_move_never_replaces_a_destination_that_appears_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync destination race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            self.assertTrue(
                hasattr(module, "rename_noreplace"),
                "synchronization requires an atomic no-replace rename primitive",
            )
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            plan = plans[0]
            assert plan.target_identity is not None
            original_rename_noreplace = module.rename_noreplace

            def destination_appears(source, destination, parent_identity) -> None:
                destination.mkdir()
                original_rename_noreplace(source, destination, parent_identity)

            with patch.object(module, "rename_noreplace", destination_appears):
                with self.assertRaises(module.SyncError):
                    module._move_owned_path(
                        plan,
                        plan.target,
                        plan.previous,
                        plan.target_identity,
                    )

            self.assertTrue(plan.target.is_dir())
            self.assertTrue(plan.previous.is_dir())
            self.assertEqual(list(plan.previous.iterdir()), [])
            module._discard_prepared_staging(plans)

    def test_move_restores_a_source_replacement_raced_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync source move race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            plan = plans[0]
            assert plan.target_identity is not None
            displaced = plan.target.with_name(plan.target.name + ".displaced")
            foreign_marker = plan.target / "foreign-source-marker.txt"
            original_rename = module.rename_noreplace
            raced = False

            def replace_source(source, destination, parent_identity):
                nonlocal raced
                if not raced and source == plan.target and destination == plan.previous:
                    raced = True
                    source.rename(displaced)
                    source.mkdir()
                    foreign_marker.write_text("restore-to-source\n", encoding="utf-8")
                return original_rename(source, destination, parent_identity)

            with patch.object(module, "rename_noreplace", replace_source):
                with self.assertRaises(module.SyncError):
                    module._move_owned_path(
                        plan,
                        plan.target,
                        plan.previous,
                        plan.target_identity,
                    )

            self.assertTrue(raced)
            self.assertEqual(
                foreign_marker.read_text(encoding="utf-8"),
                "restore-to-source\n",
                "transaction move hid a foreign source replacement at its destination",
            )
            self.assertFalse(plan.previous.exists())
            shutil.rmtree(plan.target)
            displaced.rename(plan.target)
            module._discard_prepared_staging(plans)

    def test_cleanup_never_deletes_a_foreign_replacement_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync cleanup race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            plan = plans[0]
            displaced = plan.staging.with_name(plan.staging.name + ".displaced")
            foreign_marker = plan.staging / "foreign-owner.txt"
            original_rename_noreplace = module.rename_noreplace
            swapped = False

            def swap_before_quarantine(source, destination, parent_identity):
                nonlocal swapped
                if not swapped and source == plan.staging:
                    swapped = True
                    plan.staging.rename(displaced)
                    plan.staging.mkdir()
                    foreign_marker.write_text("preserve\n", encoding="utf-8")
                original_rename_noreplace(source, destination, parent_identity)

            with patch.object(module, "rename_noreplace", swap_before_quarantine):
                with self.assertRaises(module.SyncError):
                    module._remove_owned_path(
                        plan,
                        plan.staging,
                        plan.staging_identity,
                    )

            self.assertTrue(swapped, "test did not reach the cleanup quarantine race")
            self.assertEqual(
                foreign_marker.read_text(encoding="utf-8"),
                "preserve\n",
                "cleanup deleted an object that replaced the owned path",
            )
            shutil.rmtree(plan.staging)
            displaced.rename(plan.staging)
            module._discard_prepared_staging(plans)

    def test_windows_promotion_fails_closed_without_handle_relative_rename(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync windows fail closed ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            parent = clone / "windows-parent"
            parent.mkdir()
            source = parent / "source"
            destination = parent / "destination"
            source.mkdir()
            parent_identity = module.object_identity(parent)
            assert parent_identity is not None

            with patch.object(module.os, "name", "nt"):
                with self.assertRaisesRegex(
                    module.SyncError,
                    "Windows synchronization is disabled",
                ):
                    module.rename_noreplace(source, destination, parent_identity)

            self.assertTrue(source.is_dir())
            self.assertFalse(destination.exists())

    def test_commit_rechecks_original_digest_before_the_first_move(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync original digest barrier ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            prepared = module.write_transaction_journal(plans, phase="prepared")
            first = plans[0]
            changed = first.target / "SKILL.md"
            changed.write_bytes(changed.read_bytes() + b"\nCHANGED BEFORE FIRST MOVE\n")
            identities_before = {
                (plan.adapter, role): module.object_identity(path)
                for plan in plans
                for role, path in (
                    ("target", plan.target),
                    ("staging", plan.staging),
                    ("previous", plan.previous),
                )
            }

            with self.assertRaisesRegex(module.SyncError, "old tree digest changed"):
                module.commit_adapter_updates(plans, prepared)

            self.assertEqual(
                {
                    (plan.adapter, role): module.object_identity(path)
                    for plan in plans
                    for role, path in (
                        ("target", plan.target),
                        ("staging", plan.staging),
                        ("previous", plan.previous),
                    )
                },
                identities_before,
                "commit moved an adapter before proving every original tree digest",
            )
            self.assertIn(b"CHANGED BEFORE FIRST MOVE", changed.read_bytes())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())
            self.assertFalse(module.SYNC_RECEIPT_PATH.exists())

    def test_receipt_rechecks_previous_digest_and_preserves_changed_old_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync previous digest receipt ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            prepared = module.write_transaction_journal(plans, phase="prepared")
            original_write_receipt = module.write_commit_receipt
            changed_previous: Path | None = None

            def mutate_previous_before_receipt(current_plans, current_journal):
                nonlocal changed_previous
                changed_previous = current_plans[0].previous / "SKILL.md"
                changed_previous.write_bytes(
                    changed_previous.read_bytes() + b"\nCHANGED BEFORE RECEIPT\n"
                )
                return original_write_receipt(current_plans, current_journal)

            with patch.object(
                module,
                "write_commit_receipt",
                mutate_previous_before_receipt,
            ):
                with self.assertRaisesRegex(module.SyncError, "old tree digest changed"):
                    module.commit_adapter_updates(plans, prepared)

            assert changed_previous is not None
            self.assertIn(b"CHANGED BEFORE RECEIPT", changed_previous.read_bytes())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())
            self.assertFalse(module.SYNC_RECEIPT_PATH.exists())
            for plan in plans:
                self.assertEqual(
                    module.object_identity(plan.target),
                    plan.staging_identity,
                )
                self.assertEqual(
                    module.object_identity(plan.previous),
                    plan.target_identity,
                )

    def test_final_cleanup_rechecks_previous_digest_before_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync previous digest cleanup ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            prepared = module.write_transaction_journal(plans, phase="prepared")
            original_finalize = module._finalize_committed
            changed_previous: Path | None = None

            def mutate_previous_before_finalize(journal, receipt):
                nonlocal changed_previous
                changed_previous = plans[0].previous / "SKILL.md"
                changed_previous.write_bytes(
                    changed_previous.read_bytes() + b"\nCHANGED BEFORE CLEANUP\n"
                )
                return original_finalize(journal, receipt)

            with patch.object(module, "_finalize_committed", mutate_previous_before_finalize):
                with self.assertRaisesRegex(module.SyncError, "old tree digest changed"):
                    module.commit_adapter_updates(plans, prepared)

            assert changed_previous is not None
            self.assertIn(b"CHANGED BEFORE CLEANUP", changed_previous.read_bytes())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())
            self.assertTrue(module.SYNC_RECEIPT_PATH.is_file())
            self.assertEqual(
                module.object_identity(plans[0].previous),
                plans[0].target_identity,
                "final cleanup deleted the changed old rollback tree",
            )

    def test_mutation_after_promoted_verification_preserves_all_transaction_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync promoted mutation ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }
            original_verify = module._verify_exact_committed_tree
            mutated = False

            def mutate_after_promoted_verification(plan, *args, **kwargs):
                nonlocal mutated
                result = original_verify(plan, *args, **kwargs)
                if not mutated and plan.adapter == "codex":
                    mutated = True
                    skill = plan.target / "SKILL.md"
                    skill.write_bytes(skill.read_bytes() + b"\nMUTATED AFTER VERIFY\n")
                return result

            with patch.object(
                module,
                "_verify_exact_committed_tree",
                mutate_after_promoted_verification,
            ):
                with self.assertRaisesRegex(
                    module.SyncError,
                    "automatic recovery could not complete",
                ):
                    module.synchronize(check=False)

            self.assertTrue(mutated, "test did not reach the post-verification mutation")
            self.assertIn(
                b"MUTATED AFTER VERIFY",
                (module.ADAPTER_SKILLS["codex"] / "SKILL.md").read_bytes(),
            )
            for adapter, target in module.ADAPTER_SKILLS.items():
                previous = list(target.parent.glob(f".{target.name}.previous-*"))
                self.assertEqual(len(previous), 1, f"{adapter} rollback tree was not preserved")
                self.assertEqual(
                    tree_digest(
                        previous[0],
                        excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
                    ),
                    before[adapter],
                    f"{adapter} old rollback bytes changed",
                )
                self.assertFalse(
                    list(target.parent.glob(f".{target.name}.sync-*")),
                    f"{adapter} retained a duplicate staging path after promotion",
                )
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())
            self.assertFalse(module.SYNC_RECEIPT_PATH.exists())

    def test_recovery_fails_closed_for_a_corrupted_receipted_promotion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync committed recovery ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            prepared = module.write_transaction_journal(plans, phase="prepared")
            self.promote_prepared_plans(module, plans)
            module.write_commit_receipt(plans, prepared)
            corrupted = plans[0].target / "SKILL.md"
            corrupted.write_bytes(corrupted.read_bytes() + b"\nCORRUPTED BEFORE RECOVERY\n")
            previous_before = tree_digest(
                plans[0].previous,
                excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
            )

            with self.assertRaisesRegex(module.SyncError, "exact tree"):
                module.recover_pending_transaction()

            self.assertEqual(
                tree_digest(
                    plans[0].previous,
                    excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
                ),
                previous_before,
                "committed recovery deleted the rollback copy after target corruption",
            )
            self.assertTrue(module.SYNC_JOURNAL_PATH.exists())
            self.assertTrue(module.SYNC_RECEIPT_PATH.exists())

    def test_canonical_read_rejects_an_entry_swapped_after_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas canonical read race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            self.assertTrue(
                hasattr(module, "read_verified_entry"),
                "canonical reads require a descriptor-anchored no-follow primitive",
            )
            canonical = clone / "core" / "skill" / "map-project"
            raced = canonical / "race-source.txt"
            raced.write_text("canonical\n", encoding="utf-8")
            outside = clone / "foreign-source.txt"
            outside.write_text("foreign-source-must-not-be-read\n", encoding="utf-8")
            original = module.read_verified_entry
            swapped = False

            def swap_after_inventory(parent_descriptor, parent_path, name, expected, label):
                nonlocal swapped
                if not swapped and parent_path == raced.parent and name == raced.name:
                    swapped = True
                    raced.rename(raced.with_name(raced.name + ".original"))
                    raced.symlink_to(outside)
                return original(parent_descriptor, parent_path, name, expected, label)

            with patch.object(module, "read_verified_entry", swap_after_inventory):
                with self.assertRaises(module.SyncError) as raised:
                    module.canonical_tree()

            self.assertTrue(swapped, "test did not reach the canonical read race")
            self.assertNotIn("foreign-source-must-not-be-read", str(raised.exception))

    def test_drift_read_rejects_an_entry_swapped_after_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas drift read race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            self.assertTrue(
                hasattr(module, "read_verified_entry"),
                "drift reads require a descriptor-anchored no-follow primitive",
            )
            target = module.ADAPTER_SKILLS["codex"]
            raced = target / "SKILL.md"
            outside = clone / "foreign-adapter.txt"
            outside.write_text("foreign-adapter-must-not-be-read\n", encoding="utf-8")
            expected = module.load_expected_adapters()["codex"]
            original = module.read_verified_entry
            swapped = False

            def swap_after_inventory(parent_descriptor, parent_path, name, identity, label):
                nonlocal swapped
                if not swapped and parent_path == raced.parent and name == raced.name:
                    swapped = True
                    raced.rename(raced.with_name(raced.name + ".original"))
                    raced.symlink_to(outside)
                return original(parent_descriptor, parent_path, name, identity, label)

            with patch.object(module, "read_verified_entry", swap_after_inventory):
                with self.assertRaises(module.SyncError) as raised:
                    module.inspect_drift(target, *expected)

            self.assertTrue(swapped, "test did not reach the drift read race")
            self.assertNotIn("foreign-adapter-must-not-be-read", str(raised.exception))

    def test_interrupt_during_second_promotion_rolls_back_both_adapters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync rollback ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }
            original_rename = module.rename_noreplace
            interrupted = False

            def interrupting_rename(source, destination, parent_identity) -> None:
                nonlocal interrupted
                original_rename(source, destination, parent_identity)
                if (
                    not interrupted
                    and source.name.startswith(".map-project.sync-")
                    and destination == module.ADAPTER_SKILLS["claude-code"]
                ):
                    interrupted = True
                    raise KeyboardInterrupt("interrupt after second adapter promotion")

            with patch.object(module, "rename_noreplace", interrupting_rename):
                with self.assertRaises(KeyboardInterrupt):
                    module.synchronize(check=False)

            self.assertTrue(interrupted)
            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                before,
                "interrupted coordinated commit left split adapter bundles",
            )
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())

    def test_prepared_journal_recovers_a_partial_promotion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync journal recovery ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            before = tree_digest(clone / "adapters", excluded_names={"__pycache__"})

            expected = module.load_expected_adapters()
            plans = module.prepare_adapter_updates(expected, tuple(module.ADAPTER_SKILLS))
            module.write_transaction_journal(plans, phase="prepared")
            first = plans[0]
            first.target.rename(first.previous)
            first.staging.rename(first.target)

            module.recover_pending_transaction()

            self.assertEqual(
                tree_digest(clone / "adapters", excluded_names={"__pycache__"}),
                before,
            )
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())
            for plan in plans:
                self.assertFalse(plan.staging.exists())
                self.assertFalse(plan.previous.exists())

    def test_prepared_rollback_fails_closed_if_previous_tree_digest_changed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync corrupt previous ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            journal_identity = module.write_transaction_journal(plans, phase="prepared")
            first = plans[0]
            first.target.rename(first.previous)
            first.staging.rename(first.target)
            prepared_before = tree_digest(
                first.target,
                excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
            )
            previous_skill = first.previous / "SKILL.md"
            previous_skill.write_bytes(previous_skill.read_bytes() + b"\nCORRUPT OLD\n")
            previous_before = tree_digest(
                first.previous,
                excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
            )
            journal_before = module.SYNC_JOURNAL_PATH.read_bytes()

            with self.assertRaisesRegex(module.SyncError, "digest changed"):
                module.recover_pending_transaction()

            self.assertEqual(
                tree_digest(
                    first.target,
                    excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
                ),
                prepared_before,
                "rollback deleted the valid promoted candidate before old digest proof",
            )
            self.assertEqual(
                tree_digest(
                    first.previous,
                    excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
                ),
                previous_before,
                "rollback published or rewrote the corrupted previous tree",
            )
            self.assertEqual(module.SYNC_JOURNAL_PATH.read_bytes(), journal_before)
            self.assertEqual(module.object_identity(module.SYNC_JOURNAL_PATH), journal_identity)

    def test_prepared_rollback_preserves_all_state_if_new_tree_digest_changed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync corrupt prepared ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            journal_identity = module.write_transaction_journal(plans, phase="prepared")
            first = plans[0]
            first.target.rename(first.previous)
            first.staging.rename(first.target)

            changed_skill = first.target / "SKILL.md"
            changed_skill.write_bytes(changed_skill.read_bytes() + b"\nCORRUPT NEW\n")
            canary = first.target / "foreign-canary.txt"
            canary.write_text("preserve\n", encoding="utf-8")
            self.assertEqual(
                module.object_identity(first.target),
                first.staging_identity,
                "test changed the prepared root identity",
            )
            journal_before = module.SYNC_JOURNAL_PATH.read_bytes()
            object_state_before = {
                (plan.adapter, role): module.object_identity(path)
                for plan in plans
                for role, path in (
                    ("target", plan.target),
                    ("staging", plan.staging),
                    ("previous", plan.previous),
                )
            }
            tree_state_before = {
                (plan.adapter, role): tree_digest(
                    path,
                    excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
                )
                for plan in plans
                for role, path in (
                    ("target", plan.target),
                    ("staging", plan.staging),
                    ("previous", plan.previous),
                )
                if path.is_dir()
            }

            with self.assertRaisesRegex(
                module.SyncError,
                "journal-bound new tree digest changed",
            ):
                module.recover_pending_transaction()

            self.assertEqual(
                {
                    (plan.adapter, role): module.object_identity(path)
                    for plan in plans
                    for role, path in (
                        ("target", plan.target),
                        ("staging", plan.staging),
                        ("previous", plan.previous),
                    )
                },
                object_state_before,
                "prepared rollback moved a tree before rejecting changed new bytes",
            )
            self.assertEqual(
                {
                    (plan.adapter, role): tree_digest(
                        path,
                        excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
                    )
                    for plan in plans
                    for role, path in (
                        ("target", plan.target),
                        ("staging", plan.staging),
                        ("previous", plan.previous),
                    )
                    if path.is_dir()
                },
                tree_state_before,
                "prepared rollback rewrote a tree before rejecting changed new bytes",
            )
            self.assertEqual(canary.read_text(encoding="utf-8"), "preserve\n")
            self.assertIn(b"CORRUPT NEW", changed_skill.read_bytes())
            self.assertEqual(module.SYNC_JOURNAL_PATH.read_bytes(), journal_before)
            self.assertEqual(module.object_identity(module.SYNC_JOURNAL_PATH), journal_identity)

    def test_check_refuses_a_pending_journal_without_mutating_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync read only check ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }

            with self.assertRaisesRegex(module.SyncError, "requires a recovery run"):
                module.synchronize(check=True)

            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                before,
                "a read-only check recovered or published a pending transaction",
            )
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())
            module.recover_pending_transaction()

    def test_recovery_rejects_an_escaping_journal_before_touching_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync unsafe journal ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }
            payload = json.loads(module.SYNC_JOURNAL_PATH.read_text(encoding="utf-8"))
            payload["plans"][0]["previous"] = "../outside"
            module.SYNC_JOURNAL_PATH.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(module.SyncError, "unsafe previous path"):
                module.recover_pending_transaction()

            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                before,
                "an unsafe recovery journal changed an adapter target",
            )
            module.SYNC_JOURNAL_PATH.unlink()
            module._discard_prepared_staging(plans)

    def test_recovery_refuses_a_foreign_replacement_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync foreign target ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            first = plans[0]
            first.target.rename(first.previous)
            first.staging.rename(first.target)
            shutil.rmtree(first.target)
            first.target.mkdir()
            foreign_marker = first.target / "foreign-owner.txt"
            foreign_marker.write_text("preserve\n", encoding="utf-8")
            second_before = tree_digest(plans[1].target, excluded_names={"__pycache__"})

            with self.assertRaisesRegex(module.SyncError, "foreign or ambiguous"):
                module.recover_pending_transaction()

            self.assertEqual(foreign_marker.read_text(encoding="utf-8"), "preserve\n")
            self.assertEqual(
                tree_digest(plans[1].target, excluded_names={"__pycache__"}),
                second_before,
                "preflight changed another adapter before rejecting the foreign target",
            )
            self.assertTrue(first.previous.exists())
            self.assertTrue(module.SYNC_JOURNAL_PATH.exists())

    def test_recovery_rejects_reused_target_identity_with_a_foreign_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync reused target ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            journal = module.write_transaction_journal(plans, phase="prepared")
            first = plans[0]
            first.target.rename(first.previous)
            first.staging.rename(first.target)
            shutil.rmtree(first.target)
            first.target.mkdir()
            foreign_marker = first.target / module.ACTIVE_MARKER_NAME
            foreign_marker.write_text("foreign ownership proof\n", encoding="utf-8")
            os.chmod(foreign_marker, 0o600)
            second_before = tree_digest(
                plans[1].target,
                excluded_names={"__pycache__"},
            )
            journal_before = module.SYNC_JOURNAL_PATH.read_bytes()

            with self.inject_reused_directory_identity(
                module, first.target, first.staging_identity
            ):
                with self.assertRaisesRegex(module.SyncError, "marker"):
                    module.recover_pending_transaction()

            self.assertEqual(foreign_marker.read_text(encoding="utf-8"), "foreign ownership proof\n")
            self.assertEqual(
                tree_digest(
                    plans[1].target,
                    excluded_names={"__pycache__"},
                ),
                second_before,
                "another adapter changed before the reused target was rejected",
            )
            self.assertEqual(module.SYNC_JOURNAL_PATH.read_bytes(), journal_before)
            self.assertEqual(module.object_identity(module.SYNC_JOURNAL_PATH), journal)

    def test_recovery_rejects_reused_previous_identity_with_a_foreign_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync reused previous ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            first = plans[0]
            first.target.rename(first.previous)
            first.staging.rename(first.target)
            shutil.rmtree(first.previous)
            first.previous.mkdir()
            foreign_marker = first.previous / module.ACTIVE_MARKER_NAME
            foreign_marker.write_text("foreign old ownership proof\n", encoding="utf-8")
            os.chmod(foreign_marker, 0o600)
            promoted_before = tree_digest(
                first.target,
                excluded_names={"__pycache__"},
            )
            second_before = tree_digest(
                plans[1].target,
                excluded_names={"__pycache__"},
            )
            journal_before = module.SYNC_JOURNAL_PATH.read_bytes()
            assert first.target_identity is not None

            with self.inject_reused_directory_identity(
                module, first.previous, first.target_identity
            ):
                with self.assertRaisesRegex(module.SyncError, "marker"):
                    module.recover_pending_transaction()

            self.assertEqual(
                foreign_marker.read_text(encoding="utf-8"),
                "foreign old ownership proof\n",
            )
            self.assertEqual(
                tree_digest(
                    first.target,
                    excluded_names={"__pycache__"},
                ),
                promoted_before,
            )
            self.assertEqual(
                tree_digest(
                    plans[1].target,
                    excluded_names={"__pycache__"},
                ),
                second_before,
                "another adapter changed before the reused previous tree was rejected",
            )
            self.assertEqual(module.SYNC_JOURNAL_PATH.read_bytes(), journal_before)

    def test_move_restores_a_reused_identity_replacement_after_marker_postcheck(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync marker move race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            assert first.target_identity is not None
            displaced = first.target.with_name(first.target.name + ".displaced")
            original_rename = module.rename_noreplace
            original_identity = module._identity_from_stat
            foreign_signature = None
            raced = False
            second_before = tree_digest(
                plans[1].target,
                excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
            )
            journal_before = module.SYNC_JOURNAL_PATH.read_bytes()

            def replace_old_root(source, destination, parent_identity):
                nonlocal foreign_signature, raced
                if not raced and source == first.target and destination == first.previous:
                    raced = True
                    source.rename(displaced)
                    source.mkdir()
                    (source / module.ACTIVE_MARKER_NAME).write_text(
                        "foreign-old-proof\n",
                        encoding="utf-8",
                    )
                    os.chmod(source / module.ACTIVE_MARKER_NAME, 0o600)
                    (source / "foreign-owner.txt").write_text(
                        "restore-public-source\n",
                        encoding="utf-8",
                    )
                    details = os.lstat(source)
                    foreign_signature = (details.st_dev, details.st_ino)
                return original_rename(source, destination, parent_identity)

            def reuse_old_identity(details):
                if foreign_signature == (details.st_dev, details.st_ino):
                    return first.target_identity
                return original_identity(details)

            with patch.object(module, "_identity_from_stat", reuse_old_identity), patch.object(
                module, "rename_noreplace", replace_old_root
            ):
                with self.assertRaisesRegex(module.SyncError, "marker|ownership proof"):
                    module._move_owned_path(
                        first,
                        first.target,
                        first.previous,
                        first.target_identity,
                        role="old",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(raced)
            self.assertEqual(
                (first.target / "foreign-owner.txt").read_text(encoding="utf-8"),
                "restore-public-source\n",
            )
            self.assertFalse(first.previous.exists())
            self.assertEqual(
                tree_digest(
                    plans[1].target,
                    excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
                ),
                second_before,
            )
            self.assertEqual(module.SYNC_JOURNAL_PATH.read_bytes(), journal_before)

    def test_cleanup_restores_a_reused_identity_replacement_after_marker_postcheck(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync marker cleanup race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            quarantine = module._cleanup_quarantine(first, "new")
            displaced = first.staging.with_name(first.staging.name + ".displaced")
            original_rename = module.rename_noreplace
            original_identity = module._identity_from_stat
            foreign_signature = None
            raced = False
            second_before = tree_digest(
                plans[1].target,
                excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
            )
            journal_before = module.SYNC_JOURNAL_PATH.read_bytes()

            def replace_new_root(source, destination, parent_identity):
                nonlocal foreign_signature, raced
                if not raced and source == first.staging and destination == quarantine:
                    raced = True
                    source.rename(displaced)
                    source.mkdir()
                    (source / module.ACTIVE_MARKER_NAME).write_text(
                        "foreign-new-proof\n",
                        encoding="utf-8",
                    )
                    os.chmod(source / module.ACTIVE_MARKER_NAME, 0o600)
                    (source / "foreign-owner.txt").write_text(
                        "restore-public-staging\n",
                        encoding="utf-8",
                    )
                    details = os.lstat(source)
                    foreign_signature = (details.st_dev, details.st_ino)
                return original_rename(source, destination, parent_identity)

            def reuse_new_identity(details):
                if foreign_signature == (details.st_dev, details.st_ino):
                    return first.staging_identity
                return original_identity(details)

            with patch.object(module, "_identity_from_stat", reuse_new_identity), patch.object(
                module, "rename_noreplace", replace_new_root
            ):
                with self.assertRaisesRegex(module.SyncError, "marker|ownership proof"):
                    module._remove_owned_path(
                        first,
                        first.staging,
                        first.staging_identity,
                        quarantine=quarantine,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(raced)
            self.assertEqual(
                (first.staging / "foreign-owner.txt").read_text(encoding="utf-8"),
                "restore-public-staging\n",
            )
            self.assertFalse(quarantine.exists())
            self.assertEqual(
                tree_digest(
                    plans[1].target,
                    excluded_names={"__pycache__", module.ACTIVE_MARKER_NAME},
                ),
                second_before,
            )
            self.assertEqual(module.SYNC_JOURNAL_PATH.read_bytes(), journal_before)

    def test_receipted_recovery_resumes_active_retired_and_absent_new_markers(self) -> None:
        for marker_state in ("active", "retired", "absent"):
            with self.subTest(marker_state=marker_state), tempfile.TemporaryDirectory(
                prefix=f"atlas sync committed {marker_state} "
            ) as temp_dir:
                clone = self.make_clone(temp_dir)
                self.drift_both(clone)
                module = self.load_sync(clone)
                plans = module.prepare_adapter_updates(
                    module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
                )
                prepared = module.write_transaction_journal(plans, phase="prepared")
                self.promote_prepared_plans(module, plans)
                module.write_commit_receipt(plans, prepared)
                journal = module._read_transaction_journal()
                first = plans[0]
                if marker_state in {"retired", "absent"}:
                    module._retire_role_marker(
                        first,
                        first.target,
                        first.staging_identity,
                        role="new",
                        journal_sha256=journal.sha256,
                    )
                if marker_state == "absent":
                    module._remove_retired_role_marker(
                        first,
                        first.target,
                        first.staging_identity,
                        role="new",
                        journal_sha256=journal.sha256,
                    )
                target_identity = first.staging_identity
                target_before = tree_digest(
                    first.target,
                    excluded_names={
                        "__pycache__",
                        module.ACTIVE_MARKER_NAME,
                        module.RETIRED_MARKER_NAME,
                    },
                )
                original_remove = module._remove_owned_path

                def never_delete_committed_target(plan, path, expected, *args, **kwargs):
                    self.assertNotEqual(
                        path,
                        first.target,
                        "marker-absent committed recovery attempted to delete the target",
                    )
                    return original_remove(plan, path, expected, *args, **kwargs)

                with patch.object(module, "_remove_owned_path", never_delete_committed_target):
                    module.recover_pending_transaction()

                self.assertEqual(module.object_identity(first.target), target_identity)
                self.assertEqual(
                    tree_digest(first.target, excluded_names={"__pycache__"}),
                    target_before,
                )
                self.assertFalse((first.target / module.ACTIVE_MARKER_NAME).exists())
                self.assertFalse((first.target / module.RETIRED_MARKER_NAME).exists())
                self.assertFalse(module.SYNC_RECEIPT_PATH.exists())
                self.assertFalse(module.SYNC_JOURNAL_PATH.exists())

    def test_journal_publication_cutpoint_is_resumable_without_installed_markers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync journal publish crash ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )

            with patch.object(
                module,
                "_install_transaction_markers",
                side_effect=KeyboardInterrupt("crash immediately after journal publication"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    module.write_transaction_journal(plans, phase="prepared")

            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())
            for plan in plans:
                self.assertFalse((plan.target / module.ACTIVE_MARKER_NAME).exists())
                self.assertFalse((plan.staging / module.ACTIVE_MARKER_NAME).exists())

            module.recover_pending_transaction()

            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                before,
            )
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())
            for plan in plans:
                self.assertFalse(plan.staging.exists())
                self.assertFalse(plan.previous.exists())

    def test_committed_recovery_uses_the_prepared_tree_digest_after_canonical_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync canonical drift recovery ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            prepared = module.write_transaction_journal(plans, phase="prepared")
            self.promote_prepared_plans(module, plans)
            module.write_commit_receipt(plans, prepared)
            promoted = {
                plan.adapter: tree_digest(
                    plan.target,
                    excluded_names={
                        "__pycache__",
                        module.ACTIVE_MARKER_NAME,
                        module.RETIRED_MARKER_NAME,
                    },
                )
                for plan in plans
            }
            canonical_entrypoint = module.CORE_SKILL / "SKILL.md"
            canonical_entrypoint.write_text(
                canonical_entrypoint.read_text(encoding="utf-8")
                + "\nCANONICAL DRIFT AFTER COMMIT\n",
                encoding="utf-8",
            )

            module.recover_pending_transaction()

            self.assertEqual(
                {
                    plan.adapter: tree_digest(
                        plan.target,
                        excluded_names={"__pycache__"},
                    )
                    for plan in plans
                },
                promoted,
            )
            self.assertEqual(set(module.synchronize(check=True)), set(module.ADAPTER_SKILLS))
            self.assertFalse(module.SYNC_RECEIPT_PATH.exists())
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())

    def test_finalize_preserves_every_rollback_tree_until_all_marker_free_targets_verify(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync finalize barrier ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            prepared = module.write_transaction_journal(plans, phase="prepared")
            self.promote_prepared_plans(module, plans)
            module.write_commit_receipt(plans, prepared)
            original_remove_marker = module._remove_retired_role_marker
            mutated = False

            def mutate_second_after_first_marker_removal(plan, *args, **kwargs):
                nonlocal mutated
                result = original_remove_marker(plan, *args, **kwargs)
                if not mutated and plan.adapter == plans[0].adapter:
                    mutated = True
                    entrypoint = plans[1].target / "SKILL.md"
                    entrypoint.write_text(
                        entrypoint.read_text(encoding="utf-8")
                        + "\nCORRUPTED AFTER PRE-FLIGHT\n",
                        encoding="utf-8",
                    )
                return result

            with patch.object(
                module,
                "_remove_retired_role_marker",
                mutate_second_after_first_marker_removal,
            ):
                with self.assertRaisesRegex(module.SyncError, "exact tree|digest"):
                    module.recover_pending_transaction()

            self.assertTrue(mutated)
            for plan in plans:
                self.assertTrue(
                    plan.previous.is_dir(),
                    f"{plan.adapter} rollback tree was deleted before the global barrier",
                )
            self.assertTrue(module.SYNC_RECEIPT_PATH.is_file())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())

    def test_recovery_flushes_adapter_parents_before_transaction_state_deletion(self) -> None:
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory(
                prefix=f"atlas sync durability {'commit' if committed else 'rollback'} "
            ) as temp_dir:
                clone = self.make_clone(temp_dir)
                self.drift_both(clone)
                module = self.load_sync(clone)
                plans = module.prepare_adapter_updates(
                    module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
                )
                prepared = module.write_transaction_journal(plans, phase="prepared")
                if committed:
                    self.promote_prepared_plans(module, plans)
                    module.write_commit_receipt(plans, prepared)
                events = []
                original_flush = module._flush_directory
                original_remove_journal = module._remove_journal
                original_remove_receipt = module._remove_receipt

                def record_flush(directory):
                    events.append(("flush", directory))
                    return original_flush(directory)

                def record_journal(*args, **kwargs):
                    events.append(("state", module.SYNC_JOURNAL_PATH))
                    return original_remove_journal(*args, **kwargs)

                def record_receipt(*args, **kwargs):
                    events.append(("state", module.SYNC_RECEIPT_PATH))
                    return original_remove_receipt(*args, **kwargs)

                with patch.object(module, "_flush_directory", record_flush), patch.object(
                    module, "_remove_journal", record_journal
                ), patch.object(module, "_remove_receipt", record_receipt):
                    module.recover_pending_transaction()

                first_state = next(
                    index for index, event in enumerate(events) if event[0] == "state"
                )
                for parent in {plan.target.parent for plan in plans}:
                    self.assertTrue(
                        any(
                            event == ("flush", parent)
                            for event in events[:first_state]
                        ),
                        f"{parent} was not durably flushed before state deletion",
                    )
                if committed:
                    self.assertLess(
                        events.index(("state", module.SYNC_RECEIPT_PATH)),
                        events.index(("state", module.SYNC_JOURNAL_PATH)),
                    )

    def test_successful_commit_leaves_no_public_marker_or_transaction_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync marker free commit ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)

            drift = module.synchronize(check=False)

            self.assertEqual(set(drift), set(module.ADAPTER_SKILLS))
            self.assertEqual(module.synchronize(check=True), {})
            for target in module.ADAPTER_SKILLS.values():
                self.assertFalse((target / module.ACTIVE_MARKER_NAME).exists())
                self.assertFalse((target / module.RETIRED_MARKER_NAME).exists())
                self.assertEqual(
                    list(target.parent.glob(f".{target.name}.previous-*")),
                    [],
                )
                self.assertEqual(
                    list(target.parent.glob(f".{target.name}.cleanup-*")),
                    [],
                )
            self.assertFalse(module.SYNC_RECEIPT_PATH.exists())
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())

    def test_cleanup_rechecks_marker_after_quarantine_before_recursive_delete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync marker quarantine ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            quarantine = module._cleanup_quarantine(first, "new")
            original_rename = module.rename_noreplace
            replaced = False

            def replace_marker_after_quarantine(source, destination, parent_identity):
                nonlocal replaced
                original_rename(source, destination, parent_identity)
                if source == first.staging and destination == quarantine:
                    replaced = True
                    marker = quarantine / module.ACTIVE_MARKER_NAME
                    marker.unlink()
                    marker.write_text("foreign marker after quarantine\n", encoding="utf-8")
                    os.chmod(marker, 0o600)

            with patch.object(module, "rename_noreplace", replace_marker_after_quarantine):
                with self.assertRaisesRegex(module.SyncError, "marker"):
                    module._remove_owned_path(
                        first,
                        first.staging,
                        first.staging_identity,
                        quarantine=quarantine,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(replaced)
            self.assertEqual(
                (first.staging / module.ACTIVE_MARKER_NAME).read_text(encoding="utf-8"),
                "foreign marker after quarantine\n",
            )
            self.assertTrue((first.staging / "SKILL.md").is_file())
            self.assertFalse(quarantine.exists())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())

    def test_recursive_cleanup_keeps_ownership_proof_until_payload_is_empty(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync recursive cleanup crash ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            quarantine = module._cleanup_quarantine(first, "new")
            original_unlink = module.os.unlink
            interrupted = False

            def interrupt_after_payload_unlink(path, *args, **kwargs):
                nonlocal interrupted
                result = original_unlink(path, *args, **kwargs)
                if (
                    not interrupted
                    and path
                    not in {
                        module.ACTIVE_MARKER_NAME,
                        module.RETIRED_MARKER_NAME,
                    }
                ):
                    interrupted = True
                    raise KeyboardInterrupt("crash during recursive payload cleanup")
                return result

            with patch.object(module.os, "unlink", interrupt_after_payload_unlink):
                with self.assertRaises(KeyboardInterrupt):
                    module._remove_owned_path(
                        first,
                        first.staging,
                        first.staging_identity,
                        quarantine=quarantine,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(interrupted)
            self.assertTrue((quarantine / module.ACTIVE_MARKER_NAME).is_file())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())
            module.recover_pending_transaction()
            self.assertFalse(quarantine.exists())
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())

    def test_cleanup_fails_closed_after_marker_unlink_to_rmdir_cutpoint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync empty quarantine crash ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            quarantine = module._cleanup_quarantine(first, "new")
            tombstone = module._cleanup_tombstone(first, "new")
            original_rmdir = module.os.rmdir
            interrupted = False

            def interrupt_before_quarantine_rmdir(path, *args, **kwargs):
                nonlocal interrupted
                if not interrupted and path == quarantine.name:
                    interrupted = True
                    raise KeyboardInterrupt("crash after marker unlink")
                return original_rmdir(path, *args, **kwargs)

            with patch.object(module.os, "rmdir", interrupt_before_quarantine_rmdir):
                with self.assertRaises(KeyboardInterrupt):
                    module._remove_owned_path(
                        first,
                        first.staging,
                        first.staging_identity,
                        quarantine=quarantine,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(interrupted)
            self.assertTrue(quarantine.is_dir())
            self.assertEqual(list(quarantine.iterdir()), [])
            self.assertTrue(tombstone.is_file())
            with self.assertRaisesRegex(module.SyncError, "markerless cleanup quarantine"):
                module.recover_pending_transaction()
            self.assertTrue(quarantine.is_dir())
            self.assertTrue(tombstone.is_file())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())

    def test_markerless_cleanup_quarantine_preserves_reused_foreign_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync markerless foreign ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            quarantine = module._cleanup_quarantine(first, "new")
            tombstone = module._cleanup_tombstone(first, "new")
            original_rmdir = module.os.rmdir

            def interrupt_before_quarantine_rmdir(path, *args, **kwargs):
                if path == quarantine.name:
                    raise KeyboardInterrupt("crash after marker unlink")
                return original_rmdir(path, *args, **kwargs)

            with patch.object(module.os, "rmdir", interrupt_before_quarantine_rmdir):
                with self.assertRaises(KeyboardInterrupt):
                    module._remove_owned_path(
                        first,
                        first.staging,
                        first.staging_identity,
                        quarantine=quarantine,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            quarantine.rmdir()
            quarantine.mkdir()
            with self.inject_reused_directory_identity(
                module,
                quarantine,
                first.staging_identity,
            ):
                with self.assertRaisesRegex(module.SyncError, "markerless cleanup quarantine"):
                    module.recover_pending_transaction()

            self.assertTrue(quarantine.is_dir())
            self.assertEqual(list(quarantine.iterdir()), [])
            self.assertTrue(tombstone.is_file())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())

    def test_marker_retirement_restores_a_reused_identity_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync marker retire race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            active = first.staging / module.ACTIVE_MARKER_NAME
            retired = first.staging / module.RETIRED_MARKER_NAME
            displaced = first.staging / "marker.displaced"
            marker_identity = module.object_identity(active)
            assert marker_identity is not None
            original_rename = module.rename_noreplace
            original_identity = module._identity_from_stat
            foreign_signature = None
            raced = False

            def replace_active_marker(source, destination, parent_identity):
                nonlocal foreign_signature, raced
                if not raced and source == active and destination == retired:
                    raced = True
                    source.rename(displaced)
                    source.write_text("foreign-active-marker\n", encoding="utf-8")
                    os.chmod(source, 0o600)
                    details = os.lstat(source)
                    foreign_signature = (details.st_dev, details.st_ino)
                return original_rename(source, destination, parent_identity)

            def reuse_marker_identity(details):
                if foreign_signature == (details.st_dev, details.st_ino):
                    return marker_identity
                return original_identity(details)

            with patch.object(module, "_identity_from_stat", reuse_marker_identity), patch.object(
                module, "rename_noreplace", replace_active_marker
            ):
                with self.assertRaisesRegex(module.SyncError, "marker"):
                    module._retire_role_marker(
                        first,
                        first.staging,
                        first.staging_identity,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(raced)
            self.assertEqual(active.read_text(encoding="utf-8"), "foreign-active-marker\n")
            self.assertFalse(retired.exists())
            self.assertTrue(displaced.is_file())

    def test_marker_removal_restores_a_reused_identity_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync marker removal race ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            module._retire_role_marker(
                first,
                first.staging,
                first.staging_identity,
                role="new",
                journal_sha256=journal.sha256,
            )
            retired = first.staging / module.RETIRED_MARKER_NAME
            removing = first.staging / module.REMOVING_MARKER_NAME
            displaced = first.staging / "retired-marker.displaced"
            marker_identity = module.object_identity(retired)
            assert marker_identity is not None
            original_rename = module.rename_noreplace
            original_identity = module._identity_from_stat
            foreign_signature = None
            raced = False

            def replace_retired_marker(source, destination, parent_identity):
                nonlocal foreign_signature, raced
                if not raced and source == retired and destination == removing:
                    raced = True
                    source.rename(displaced)
                    source.write_text("foreign-retired-marker\n", encoding="utf-8")
                    os.chmod(source, 0o600)
                    details = os.lstat(source)
                    foreign_signature = (details.st_dev, details.st_ino)
                return original_rename(source, destination, parent_identity)

            def reuse_marker_identity(details):
                if foreign_signature == (details.st_dev, details.st_ino):
                    return marker_identity
                return original_identity(details)

            with patch.object(module, "_identity_from_stat", reuse_marker_identity), patch.object(
                module, "rename_noreplace", replace_retired_marker
            ):
                with self.assertRaisesRegex(module.SyncError, "marker"):
                    module._remove_retired_role_marker(
                        first,
                        first.staging,
                        first.staging_identity,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(raced)
            self.assertEqual(
                retired.read_text(encoding="utf-8"),
                "foreign-retired-marker\n",
            )
            self.assertFalse(removing.exists())
            self.assertTrue(displaced.is_file())

    def test_retired_marker_final_unlink_preserves_public_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync retired final unlink ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            module._retire_role_marker(
                first,
                first.staging,
                first.staging_identity,
                role="new",
                journal_sha256=journal.sha256,
            )
            public_replacement = first.staging / module.REMOVING_MARKER_NAME
            private_name = module._private_removing_marker_name("new", journal.sha256)
            original_unlink = module.os.unlink
            raced = False

            def replace_public_marker_at_final_unlink(path, *args, **kwargs):
                nonlocal raced
                if not raced and path == private_name:
                    raced = True
                    public_replacement.write_text(
                        "foreign removing marker\n",
                        encoding="utf-8",
                    )
                    os.chmod(public_replacement, 0o600)
                return original_unlink(path, *args, **kwargs)

            with patch.object(module.os, "unlink", replace_public_marker_at_final_unlink):
                module._remove_retired_role_marker(
                    first,
                    first.staging,
                    first.staging_identity,
                    role="new",
                    journal_sha256=journal.sha256,
                )

            self.assertTrue(raced)
            self.assertEqual(
                public_replacement.read_text(encoding="utf-8"),
                "foreign removing marker\n",
            )
            self.assertFalse((first.staging / private_name).exists())

    def test_active_cleanup_final_unlink_preserves_public_marker_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync active final unlink ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            quarantine = module._cleanup_quarantine(first, "new")
            private_name = module._private_removing_marker_name("new", journal.sha256)
            original_unlink = module.os.unlink
            raced = False

            def replace_active_marker_at_final_unlink(path, *args, **kwargs):
                nonlocal raced
                if not raced and path == private_name:
                    raced = True
                    marker = quarantine / module.ACTIVE_MARKER_NAME
                    marker.write_text("foreign active marker\n", encoding="utf-8")
                    os.chmod(marker, 0o600)
                return original_unlink(path, *args, **kwargs)

            with patch.object(module.os, "unlink", replace_active_marker_at_final_unlink):
                with self.assertRaises(OSError):
                    module._remove_owned_path(
                        first,
                        first.staging,
                        first.staging_identity,
                        quarantine=quarantine,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(raced)
            self.assertEqual(
                (quarantine / module.ACTIVE_MARKER_NAME).read_text(encoding="utf-8"),
                "foreign active marker\n",
            )
            self.assertFalse((quarantine / private_name).exists())
            self.assertTrue(module.SYNC_JOURNAL_PATH.is_file())

    def test_retired_marker_private_quarantine_resumes_after_crash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync retired private resume ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            journal = module._read_transaction_journal()
            first = plans[0]
            module._retire_role_marker(
                first,
                first.staging,
                first.staging_identity,
                role="new",
                journal_sha256=journal.sha256,
            )
            private = first.staging / module._private_removing_marker_name(
                "new",
                journal.sha256,
            )
            original_delete = module._delete_quarantined_regular_file
            interrupted = False

            def interrupt_private_marker_delete(path, *args, **kwargs):
                nonlocal interrupted
                if not interrupted and path == private:
                    interrupted = True
                    raise KeyboardInterrupt("crash after private marker quarantine")
                return original_delete(path, *args, **kwargs)

            with patch.object(
                module,
                "_delete_quarantined_regular_file",
                interrupt_private_marker_delete,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    module._remove_retired_role_marker(
                        first,
                        first.staging,
                        first.staging_identity,
                        role="new",
                        journal_sha256=journal.sha256,
                    )

            self.assertTrue(interrupted)
            self.assertTrue(private.is_file())
            self.assertFalse((first.staging / module.REMOVING_MARKER_NAME).exists())
            module._remove_retired_role_marker(
                first,
                first.staging,
                first.staging_identity,
                role="new",
                journal_sha256=journal.sha256,
            )
            self.assertFalse(private.exists())

    def test_journal_only_terminal_clean_state_removes_only_the_journal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync terminal journal ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            prepared = module.write_transaction_journal(plans, phase="prepared")
            journal_bytes = module.SYNC_JOURNAL_PATH.read_bytes()
            self.promote_prepared_plans(module, plans)
            module.write_commit_receipt(plans, prepared)

            with patch.object(
                module,
                "_remove_journal",
                side_effect=KeyboardInterrupt("crash after receipt removal"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    module.recover_pending_transaction()

            self.assertFalse(module.SYNC_RECEIPT_PATH.exists())
            self.assertTrue(module.SYNC_JOURNAL_PATH.exists())
            self.assertEqual(module.SYNC_JOURNAL_PATH.read_bytes(), journal_bytes)
            targets_before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }

            with patch.object(
                module,
                "_remove_owned_path",
                side_effect=AssertionError("terminal cleanup attempted a tree deletion"),
            ):
                module.recover_pending_transaction()

            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                targets_before,
            )
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())

    def test_pending_v1_journal_fails_closed_without_adapter_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync v1 journal ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }
            module.SYNC_STATE_DIR.mkdir()
            legacy = b'{"phase":"prepared","plans":[],"version":1}\n'
            module.SYNC_JOURNAL_PATH.write_bytes(legacy)
            os.chmod(module.SYNC_JOURNAL_PATH, 0o600)

            with self.assertRaisesRegex(module.SyncError, "version 1"):
                module.recover_pending_transaction()

            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                before,
            )
            self.assertEqual(module.SYNC_JOURNAL_PATH.read_bytes(), legacy)

    def test_receipt_without_journal_fails_closed_without_adapter_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync orphan receipt ") as temp_dir:
            clone = self.make_clone(temp_dir)
            module = self.load_sync(clone)
            before = tree_digest(clone / "adapters", excluded_names={"__pycache__"})
            module.SYNC_STATE_DIR.mkdir()
            module.SYNC_RECEIPT_PATH.write_text(
                '{"journal_sha256":"'
                + ("0" * 64)
                + '","transaction_id":"'
                + ("0" * 32)
                + '","version":1}\n',
                encoding="utf-8",
            )
            os.chmod(module.SYNC_RECEIPT_PATH, 0o600)

            with self.assertRaisesRegex(module.SyncError, "receipt.*journal"):
                module.recover_pending_transaction()

            self.assertEqual(
                tree_digest(clone / "adapters", excluded_names={"__pycache__"}),
                before,
            )
            self.assertTrue(module.SYNC_RECEIPT_PATH.exists())

    def test_recovery_refuses_an_adapter_parent_inode_swap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync parent swap ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
            parent = plans[0].target.parent
            original_parent = parent.with_name(parent.name + "-original")
            parent.rename(original_parent)
            parent.mkdir()
            foreign_target = parent / plans[0].target.name
            foreign_target.mkdir()
            foreign_marker = foreign_target / "foreign-owner.txt"
            foreign_marker.write_text("preserve\n", encoding="utf-8")

            with self.assertRaisesRegex(module.SyncError, "parent changed ownership"):
                module.recover_pending_transaction()

            self.assertEqual(foreign_marker.read_text(encoding="utf-8"), "preserve\n")
            self.assertTrue(module.SYNC_JOURNAL_PATH.exists())
            shutil.rmtree(parent)
            original_parent.rename(parent)
            module.recover_pending_transaction()
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())


if __name__ == "__main__":
    unittest.main()
