from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.support import (
    FIXTURES_ROOT,
    ORACLES_ROOT,
    iter_release_files,
    load_oracle,
    run_command,
)


FIXTURE_NAMES = ("quick_cli", "standard_service", "forensic_legacy")


class FixtureContractTests(unittest.TestCase):
    def test_oracles_live_outside_fixture_roots_and_reference_real_evidence(self) -> None:
        release_files = set(iter_release_files())
        for fixture_name in FIXTURE_NAMES:
            with self.subTest(fixture=fixture_name):
                root = FIXTURES_ROOT / fixture_name
                oracle_path = ORACLES_ROOT / f"{fixture_name}.json"
                oracle = load_oracle(self, fixture_name)
                self.assertNotIn(root.resolve(), oracle_path.resolve().parents)
                for category, relative_paths in oracle["inventory"].items():
                    self.assertTrue(relative_paths, f"{fixture_name}:{category} must not be empty")
                    for relative_path in relative_paths:
                        evidence = (root / relative_path).resolve(strict=False)
                        try:
                            evidence.relative_to(root.resolve())
                        except ValueError:
                            self.fail(
                                f"oracle evidence escapes its public fixture: {fixture_name}/{category}"
                            )
                        self.assertIn(
                            evidence,
                            release_files,
                            f"oracle points outside public release evidence: {fixture_name}/{category}",
                        )

    def test_fixture_python_sources_compile_without_importing_production(self) -> None:
        release_files = set(iter_release_files())
        for fixture_name in FIXTURE_NAMES:
            root = FIXTURES_ROOT / fixture_name
            public_sources = sorted(
                path
                for path in release_files
                if root in path.parents and path.suffix.lower() == ".py"
            )
            for source in public_sources:
                with self.subTest(source=source.relative_to(FIXTURES_ROOT)):
                    text = source.read_text(encoding="utf-8")
                    try:
                        compile(text, str(source), "exec")
                    except SyntaxError as exc:
                        self.fail(f"fixture source does not compile: {source}: {exc}")

    def test_quick_cli_entrypoint_writes_state(self) -> None:
        root = FIXTURES_ROOT / "quick_cli"
        with tempfile.TemporaryDirectory(prefix="quick fixture state ") as temp_dir:
            state = Path(temp_dir) / "state with spaces.json"
            result = run_command(
                [sys.executable, "-m", "quick_cli", "41", "--state", state],
                cwd=root,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(state.read_text(encoding="utf-8")), {"input": 41, "result": 42})

    def test_standard_service_entrypoint_persists_api_writer(self) -> None:
        root = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="standard fixture state ") as temp_dir:
            database = Path(temp_dir) / "parcel state.sqlite3"
            result = run_command(
                [sys.executable, "-m", "service", "--db", database, "--parcel-id", "P-42"],
                cwd=root,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT parcel_id, status, writer FROM parcel_state"
                ).fetchone()
            self.assertEqual(row, ("P-42", "accepted", "api"))

    def test_forensic_gateway_entrypoint_appends_ledger_event(self) -> None:
        root = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="forensic fixture state ") as temp_dir:
            database = Path(temp_dir) / "settlement ledger.sqlite3"
            result = run_command(
                [
                    sys.executable,
                    "-m",
                    "legacy_system.gateway",
                    "--ledger",
                    database,
                    "--settlement-id",
                    "S-42",
                ],
                cwd=root,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT settlement_id, state, writer FROM settlement_events"
                ).fetchone()
            self.assertEqual(row, ("S-42", "submitted", "gateway"))

    def test_public_fixture_authority_retry_and_unknowns_are_explicit(self) -> None:
        release_files = set(iter_release_files())
        for fixture_name in FIXTURE_NAMES:
            root = FIXTURES_ROOT / fixture_name
            public_files = sorted(path for path in release_files if root in path.parents)
            public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_files)
            with self.subTest(fixture=fixture_name):
                self.assertTrue(public_files, "public fixture files are missing")
                self.assertIn("UNKNOWN", public_text, "public fixture must expose its unknowns")
                self.assertRegex(
                    public_text.lower(),
                    r"authorit|last word|outrank",
                    "public fixture must expose its authority rule",
                )
                self.assertIn("retry", public_text.lower(), "public fixture must expose retry behavior")

    def test_synthetic_ignored_private_fixture_is_not_a_release_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas synthetic private fixture ") as temp_dir:
            repo = (Path(temp_dir) / "fixture repo").resolve()
            repo.mkdir()
            (repo / ".gitignore").write_text(".private/\n", encoding="utf-8")
            (repo / "README.md").write_text(
                "UNKNOWN retry; operator authority has the last word.\n",
                encoding="utf-8",
            )
            public_oracle = repo / "tests" / "oracle.json"
            public_oracle.parent.mkdir()
            public_oracle.write_text('{"fixture": "synthetic"}\n', encoding="utf-8")
            private_file = repo / ".private" / "credentials.env"
            private_file.parent.mkdir()
            private_file.write_text("synthetic-private-value-never-read\n", encoding="utf-8")

            initialized = run_command(["git", "init", "-q"], cwd=repo)
            self.assertEqual(initialized.returncode, 0, "temporary Git fixture initialization failed")

            release_paths = {
                path.relative_to(repo).as_posix() for path in iter_release_files(repo)
            }
            self.assertEqual(
                release_paths,
                {".gitignore", "README.md", "tests/oracle.json"},
                "release inventory included ignored private content; values redacted",
            )
            self.assertNotIn(
                private_file.relative_to(repo).as_posix(),
                release_paths,
                "ignored private fixture path entered the release inventory; values redacted",
            )


if __name__ == "__main__":
    unittest.main()
