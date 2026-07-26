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

            def swap_journal(source, destination, parent_identity):
                nonlocal raced
                if not raced and source == module.SYNC_JOURNAL_PATH:
                    raced = True
                    source.rename(displaced)
                    source.write_text("foreign-preserve\n", encoding="utf-8")
                return original_rename(source, destination, parent_identity)

            with patch.object(module, "rename_noreplace", swap_journal):
                with self.assertRaises(module.SyncError):
                    module._remove_journal(journal_identity)

            self.assertTrue(raced)
            self.assertEqual(
                module.SYNC_JOURNAL_PATH.read_text(encoding="utf-8"),
                "foreign-preserve\n",
            )
            module.SYNC_JOURNAL_PATH.unlink()
            displaced.rename(module.SYNC_JOURNAL_PATH)
            module.recover_pending_transaction()

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

    def test_mutation_after_promoted_verification_rolls_back_all_adapters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync promoted mutation ") as temp_dir:
            clone = self.make_clone(temp_dir)
            self.drift_both(clone)
            module = self.load_sync(clone)
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }
            original_inspect = module.inspect_drift
            mutated = False

            def mutate_after_promoted_verification(target, directories, files):
                nonlocal mutated
                drift = original_inspect(target, directories, files)
                codex_target = module.ADAPTER_SKILLS["codex"]
                if (
                    not mutated
                    and target == codex_target
                    and not drift
                ):
                    mutated = True
                    skill = target / "SKILL.md"
                    skill.write_bytes(skill.read_bytes() + b"\nMUTATED AFTER VERIFY\n")
                return drift

            with patch.object(module, "inspect_drift", mutate_after_promoted_verification):
                with self.assertRaises(module.SyncError):
                    module.synchronize(check=False)

            self.assertTrue(mutated, "test did not reach the post-verification mutation")
            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                before,
                "a corrupted promoted tree was accepted or old adapters were not restored",
            )
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())

    def test_recovery_rolls_back_a_corrupted_committed_promotion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync committed recovery ") as temp_dir:
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
            prepared = module.write_transaction_journal(plans, phase="prepared")
            for plan in plans:
                assert plan.target_identity is not None
                module._move_owned_path(
                    plan,
                    plan.target,
                    plan.previous,
                    plan.target_identity,
                )
                module._move_owned_path(
                    plan,
                    plan.staging,
                    plan.target,
                    plan.staging_identity,
                )
            module.write_transaction_journal(
                plans,
                phase="committed",
                expected_current=prepared,
            )
            corrupted = plans[0].target / "SKILL.md"
            corrupted.write_bytes(corrupted.read_bytes() + b"\nCORRUPTED BEFORE RECOVERY\n")

            module.recover_pending_transaction()

            self.assertEqual(
                {
                    adapter: tree_digest(target, excluded_names={"__pycache__"})
                    for adapter, target in module.ADAPTER_SKILLS.items()
                },
                before,
            )
            self.assertFalse(module.SYNC_JOURNAL_PATH.exists())

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

    def test_check_refuses_a_pending_journal_without_mutating_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas sync read only check ") as temp_dir:
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
            module.write_transaction_journal(plans, phase="prepared")

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
            before = {
                adapter: tree_digest(target, excluded_names={"__pycache__"})
                for adapter, target in module.ADAPTER_SKILLS.items()
            }
            plans = module.prepare_adapter_updates(
                module.load_expected_adapters(), tuple(module.ADAPTER_SKILLS)
            )
            module.write_transaction_journal(plans, phase="prepared")
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
