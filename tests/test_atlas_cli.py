from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.support import (
    ATLAS_SCRIPT,
    CORE_SKILL,
    FIXTURES_ROOT,
    MODES,
    assert_directory,
    assert_file,
    load_json,
    load_oracle,
    parse_mode_output,
    run_atlas,
    run_command,
    tree_digest,
)


FIXTURE_NAMES = ("quick_cli", "standard_service", "forensic_legacy")
TRACE_HEADER = (
    "fact_id\tclaim_kind\tclaim\tsource_type\tsource_ref\tobserved_at\tstatus\t"
    "atlas_refs\tnotes\n"
)


def add_table_rows(path: Path, header: str, separator: str, rows: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    marker = f"{header}\n{separator}"
    if marker not in text:
        raise AssertionError(f"canonical table is missing from {path.name}: {header}")
    path.write_text(
        text.replace(marker, marker + "\n" + "\n".join(rows), 1),
        encoding="utf-8",
    )


class AtlasCliContractTests(unittest.TestCase):
    def materialize_minimal_standard_contract(self, fixture: Path, output: Path) -> None:
        initialized = run_atlas(
            "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)

        static_sections = {
            ("ATLAS_INDEX.md", "Document Map"),
            ("FINDINGS_AND_DISPOSITIONS.md", "Disposition Vocabulary"),
            ("LIVE_HANDOFF.md", "Reproducible Commands"),
            ("MIGRATION_PLAN.md", "Completion Gate"),
            ("OPEN_UNKNOWNS.md", "Resolved Unknowns"),
        }
        evidence_sources = {
            "ATLAS_INDEX.md": "README.md:L3-L9",
            "PRODUCT_AND_REQUIREMENTS.md": "README.md:L3-L9",
            "CURRENT_ARCHITECTURE.md": "README.md:L3-L6",
            "RUNTIME_AND_ENTRYPOINTS.md": "service/__main__.py:L7-L15",
            "DATA_STATE_AND_AUTHORITY.md": "service/state.py:L8-L21",
            "PRODUCT_FLOWS.md": "service/api.py:L7-L12",
            "QUALITY_SECURITY_AND_OPERATIONS.md": "service/worker.py:L10-L23",
            "FINDINGS_AND_DISPOSITIONS.md": "service/worker.py:L8-L23",
            "TARGET_ARCHITECTURE.md": "README.md:L3-L9",
            "MIGRATION_PLAN.md": "service/worker.py:L10-L23",
            "LIVE_HANDOFF.md": "README.md:L3-L10",
        }
        section_bodies = {
            (
                "ATLAS_INDEX.md",
                "Scope and Coverage",
            ): """The bounded service contour is covered by `README.md:L3-L9`.

Selected by: Explicit STANDARD mode for the two-runtime service.
Conflicting automatic signals: None in the public synthetic fixture.
Intentionally omitted coverage: External provider behavior is excluded.
Escalation condition: Escalate when additional runtimes or stores are introduced.""",
            (
                "PRODUCT_AND_REQUIREMENTS.md",
                "Requirements",
            ): """| ID | Claim kind | Requirement | Source | Status |
| --- | --- | --- | --- | --- |
| REQ-1 | CONFIRMED | Accept a nonblank parcel ID | service/api.py:L7-L12 | ACTIVE |""",
            (
                "RUNTIME_AND_ENTRYPOINTS.md",
                "Entry Points",
            ): """| Runtime | Trigger | Source | Status |
| --- | --- | --- | --- |
| Request handler | CLI call | service/__main__.py:L7-L15 | CONFIRMED |""",
            (
                "DATA_STATE_AND_AUTHORITY.md",
                "State Writers",
            ): """| State | Writer | Effect | Source | Status |
| --- | --- | --- | --- | --- |
| Parcel state | API and worker | Upsert status | service/state.py:L8-L21 | CONFIRMED |""",
            (
                "DATA_STATE_AND_AUTHORITY.md",
                "Authority",
            ): """| Conflict | Candidates | Final authority | Source | Status |
| --- | --- | --- | --- | --- |
| Automatic status versus override | Worker and administrator | Administrator override | service/authority.py:L4-L10 | CONFIRMED |""",
            (
                "PRODUCT_FLOWS.md",
                "Flow Registry",
            ): """| Flow | Trigger | Outcome | State/effects | Status |
| --- | --- | --- | --- | --- |
| Accept parcel | CLI call | Accepted status stored | SQLite upsert | CONFIRMED |""",
            (
                "FINDINGS_AND_DISPOSITIONS.md",
                "Findings",
            ): """| ID | Claim kind | Severity | Finding | Affected scope | Evidence | Impact | Disposition | Prerequisites | Verification | Rollback | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F-1 | CONFIRMED | P2 | Retry ordering remains unknown after timeouts | Worker retry | service/worker.py:L8-L23 | Duplicate provider effects remain possible | KEEP | Keep the retry bound | Validate bounded retries | Restore the prior atlas row | ACTIVE |""",
            (
                "MIGRATION_PLAN.md",
                "Sequence",
            ): """| Stage | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M-1 | Retain bounded retries | Current fixture behavior remains in scope | No data migration | Retry behavior passes | Atlas validates | Maintainer | Restore prior documentation | ACTIVE |""",
        }
        section_heading = re.compile(r"(?m)^## ([^\n]+)\n")
        for artifact in sorted(output.glob("*.md")):
            text = artifact.read_text(encoding="utf-8")
            headings = list(section_heading.finditer(text))
            rebuilt = [text[: headings[0].start()]]
            for index, heading in enumerate(headings):
                section = heading.group(1)
                end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                key = (artifact.name, section)
                if key in static_sections:
                    rebuilt.append(text[heading.start() : end])
                    continue
                body = section_bodies.get(
                    key,
                    f"This bounded fixture section is evidenced by "
                    f"`{evidence_sources[artifact.name]}`.",
                )
                rebuilt.append(f"## {section}\n\n{body}\n\n")
            artifact.write_text("".join(rebuilt).rstrip() + "\n", encoding="utf-8")

        add_table_rows(
            output / "OPEN_UNKNOWNS.md",
            "| ID | UNKNOWN | Consequence | Next evidence | Owner | Status |",
            "| --- | --- | --- | --- | --- | --- |",
            [
                "| U-1 | Provider ordering after timeout | Delivery order may be ambiguous | Inspect provider contract | Maintainer | ACTIVE |"
            ],
        )

    def materialize_minimal_forensic_contract(self, fixture: Path, output: Path) -> str:
        initialized = run_atlas(
            "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)

        index = output / "ATLAS_INDEX.md"
        index_text = index.read_text(encoding="utf-8")
        for before, after in (
            ("Selected by: UNKNOWN", "Selected by: The fixture signals selected FORENSIC depth."),
            (
                "Conflicting automatic signals: UNKNOWN",
                "Conflicting automatic signals: No conflict; the automatic recommendation was FORENSIC.",
            ),
            (
                "Intentionally omitted coverage: UNKNOWN",
                "Intentionally omitted coverage: No lower-depth override; external production remains excluded.",
            ),
            (
                "Escalation condition: UNKNOWN",
                "Escalation condition: Stop and request new authority when the declared fixture scope is insufficient.",
            ),
        ):
            index_text = index_text.replace(before, after, 1)
        index.write_text(index_text, encoding="utf-8")

        # Every routed document must contain retained fixture-specific work.  The
        # helper is intentionally small, but no required artifact may remain the
        # byte-identical generated scaffold and still model a completed run.
        for artifact in sorted(output.glob("*.md")):
            artifact.write_text(
                artifact.read_text(encoding="utf-8")
                + "\n## Retained Fixture Evidence\n\n"
                + "The bounded synthetic legacy contour is evidenced by "
                + "`legacy_system/gateway.py:L8-L11` and remains limited to this fixture.\n",
                encoding="utf-8",
            )

        add_table_rows(
            output / "PRODUCT_AND_REQUIREMENTS.md",
            "| ID | Claim kind | Requirement | Source | Status |",
            "| --- | --- | --- | --- | --- |",
            [
                "| REQ-1 | CONFIRMED | Gateway accepts a request | legacy_system/gateway.py:L8-L11 | ACTIVE |"
            ],
        )
        add_table_rows(
            output / "FINDINGS_AND_DISPOSITIONS.md",
            "| ID | Claim kind | Severity | Finding | Affected scope | Evidence | Impact | Disposition | Prerequisites | Verification | Rollback | Status |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            [
                "| F-1 | CONFIRMED | P2 | Legacy writer remains reachable | legacy_system | legacy_system/migration_writer.py:L8-L13 | Duplicate writes | KEEP | None | Re-run focused test | Restore prior routing | ACTIVE |"
            ],
        )
        migration = output / "MIGRATION_PLAN.md"
        migration_text = migration.read_text(encoding="utf-8").replace(
            "| Stage | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            "| Stage | Claim kind | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        )
        migration.write_text(migration_text, encoding="utf-8")
        add_table_rows(
            migration,
            "| Stage | Claim kind | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            [
                "| M-1 | TARGET | Retain the current writer | Evidence remains current | No data movement | Focused flow passes | Static checks | Maintainer | Restore prior atlas decision | ACTIVE |"
            ],
        )
        add_table_rows(
            output / "ATLAS_INDEX.md",
            "| ID | Claim kind | Claim | Population | Discovery method | Numerator | Denominator | Exclusions | Status |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            [
                "| COV-1 | CONFIRMED | One gateway runtime is traced | Gateway runtimes | Safe inventory and bounded source trace | 1 | 1 | None | ACTIVE |"
            ],
        )
        add_table_rows(
            output / "OPEN_UNKNOWNS.md",
            "| ID | UNKNOWN | Consequence | Next evidence | Owner | Status |",
            "| --- | --- | --- | --- | --- | --- |",
            [
                "| U-1 | Recovery owner remains unknown | Recovery can stall | Ask the maintainer | Maintainer | ACTIVE |"
            ],
        )

        review_placeholder = "0" * 64
        add_table_rows(
            output / "LIVE_HANDOFF.md",
            "| ID | Review kind | Reviewer ref | Independence | Reviewed snapshot | Verdict | Critical | Important | Retained evidence summary | Remaining limits | Reviewed at | Status |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            [
                f"| REV-CORRECTNESS | CORRECTNESS | reviewer-correctness | FRESH_CONTEXT | {review_placeholder} | PASS | 0 | 0 | Checked material claim coverage and contradiction cases. | Runtime behavior outside the inspected fixture remains unobserved. | 2026-07-22T00:00:00Z | ACTIVE |",
                f"| REV-SECURITY | SECURITY | reviewer-security | FRESH_CONTEXT | {review_placeholder} | PASS | 0 | 0 | Checked safe inventory boundaries and snapshot reads. | External identity and production access remain outside scope. | 2026-07-22T00:01:00Z | ACTIVE |",
            ],
        )

        command = (
            "rg --no-config --sort path --line-number --fixed-strings "
            "'write_event' legacy_system"
        )
        command_result = run_command(
            [
                "rg",
                "--no-config",
                "--sort",
                "path",
                "--line-number",
                "--fixed-strings",
                "write_event",
                "legacy_system",
            ],
            cwd=fixture,
        )
        self.assertEqual(command_result.returncode, 0, command_result.stderr)
        command_digest = hashlib.sha256(command_result.stdout.encode("utf-8")).hexdigest()
        trace_rows = [
            ("REQ-1", "CONFIRMED", "Gateway accepts a request", "FILE", "legacy_system/gateway.py:L8-L11", "PRODUCT_AND_REQUIREMENTS.md#requirements/REQ-1", ""),
            ("EV-F1", "CONFIRMED", "Legacy writer remains reachable", "FILE", "legacy_system/migration_writer.py:L8-L13", "FINDINGS_AND_DISPOSITIONS.md#findings/F-1/finding", ""),
            ("EV-D1", "TARGET", "Disposition F-1: KEEP", "FILE", "legacy_system/migration_writer.py:L8-L13", "FINDINGS_AND_DISPOSITIONS.md#findings/F-1/disposition", ""),
            ("EV-M1", "TARGET", "Retain the current writer", "FILE", "legacy_system/migration_writer.py:L8-L13", "MIGRATION_PLAN.md#migration/M-1", ""),
            ("EV-C1", "CONFIRMED", "One gateway runtime is traced", "FILE", "legacy_system/gateway.py:L8-L11", "ATLAS_INDEX.md#coverage/COV-1", ""),
            ("EV-U1", "UNKNOWN", "Recovery owner remains unknown", "FILE", "README.md:L8-L9", "OPEN_UNKNOWNS.md#unknowns/U-1", ""),
            ("EV-RC", "CONFIRMED", "CORRECTNESS review PASS: 0 Critical, 0 Important", "EXTERNAL", "review/REV-CORRECTNESS", "LIVE_HANDOFF.md#reviews/REV-CORRECTNESS", ""),
            ("EV-RS", "CONFIRMED", "SECURITY review PASS: 0 Critical, 0 Important", "EXTERNAL", "review/REV-SECURITY", "LIVE_HANDOFF.md#reviews/REV-SECURITY", ""),
            (
                "EV-CMD",
                "CONFIRMED",
                "Gateway source contains the shared state writer call",
                "COMMAND",
                command,
                "-",
                f"cwd=.; exit=0; stdout_sha256={command_digest}",
            ),
        ]
        (output / "TRACEABILITY.tsv").write_text(
            TRACE_HEADER
            + "".join(
                f"{fact_id}\t{kind}\t{claim}\t{source_type}\t{source_ref}\t2026-07-22\tACTIVE\t{atlas_ref}\t{notes}\n"
                for fact_id, kind, claim, source_type, source_ref, atlas_ref, notes in trace_rows
            ),
            encoding="utf-8",
        )
        snapshotted = run_atlas(
            "snapshot",
            "--atlas",
            output,
            "--project",
            fixture,
            "--output",
            output / "SOURCE_SNAPSHOT.json",
        )
        self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
        snapshot_payload = load_json(self, output / "SOURCE_SNAPSHOT.json")
        review_input_sha = str(snapshot_payload["review_input"]["sha256"])
        handoff = output / "LIVE_HANDOFF.md"
        handoff.write_text(
            handoff.read_text(encoding="utf-8").replace(review_placeholder, review_input_sha),
            encoding="utf-8",
        )
        refreshed = run_atlas(
            "snapshot",
            "--atlas",
            output,
            "--project",
            fixture,
            "--output",
            output / "SOURCE_SNAPSHOT.json",
        )
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        refreshed_payload = load_json(self, output / "SOURCE_SNAPSHOT.json")
        self.assertEqual(refreshed_payload["review_input"]["sha256"], review_input_sha)
        return review_input_sha

    def test_cli_exposes_all_public_subcommands(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        result = run_atlas("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("select-mode", "inventory", "init", "validate", "snapshot"):
            with self.subTest(command=command):
                self.assertIn(command, result.stdout)

    def test_every_subcommand_has_help(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        for command in ("select-mode", "inventory", "init", "validate", "snapshot"):
            with self.subTest(command=command):
                result = run_atlas(command, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage", result.stdout.lower())

    def test_validate_help_discloses_deterministic_replay_sort_contract(self) -> None:
        result = run_atlas("validate", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("exact --sort path", result.stdout)

    def test_mode_selection_matches_three_independent_fixtures(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        for fixture_name in FIXTURE_NAMES:
            oracle = load_oracle(self, fixture_name)
            result = run_atlas("select-mode", "--project", FIXTURES_ROOT / fixture_name)
            with self.subTest(fixture=fixture_name):
                self.assertEqual(parse_mode_output(self, result), oracle["expected_mode"])

    def test_mode_selection_uses_product_evidence_not_support_vocabulary(self) -> None:
        repository = ATLAS_SCRIPT.parents[4]
        selected = run_atlas("select-mode", "--project", repository)
        self.assertEqual(selected.returncode, 0, selected.stderr)
        payload = json.loads(selected.stdout)
        self.assertEqual(payload["mode"], "STANDARD")
        self.assertFalse(payload["signals"]["production"])
        self.assertFalse(payload["signals"]["critical"])
        self.assertFalse(payload["signals"]["financial_data"])
        self.assertEqual(payload["signals"]["runtime_count"], 2)
        self.assertEqual(payload["signals"]["state_writer_count"], 2)

        with tempfile.TemporaryDirectory(prefix="atlas signal provenance ") as temp_dir:
            project = Path(temp_dir) / "project"
            (project / "services" / "a").mkdir(parents=True)
            (project / "services" / "b").mkdir(parents=True)
            (project / "tests" / "fixtures" / "legacy").mkdir(parents=True)
            (project / "README.md").write_text(
                "# Pair\n\nTwo local command runtimes process ordinary text records.\n",
                encoding="utf-8",
            )
            identical_runtime = (
                "def main():\n    return 0\n\n"
                "if __name__ == '__main__':\n    raise SystemExit(main())\n"
            )
            for service in ("a", "b"):
                (project / "services" / service / "main.py").write_text(
                    identical_runtime,
                    encoding="utf-8",
                )
            (project / "tests" / "fixtures" / "legacy" / "main.py").write_text(
                "# production-critical financial settlement fixture\n"
                "def write_ledger():\n    return None\n",
                encoding="utf-8",
            )
            synthetic = run_atlas("select-mode", "--project", project)
            self.assertEqual(synthetic.returncode, 0, synthetic.stderr)
            synthetic_payload = json.loads(synthetic.stdout)
            self.assertEqual(synthetic_payload["mode"], "STANDARD")
            self.assertEqual(synthetic_payload["signals"]["runtime_count"], 2)
            self.assertFalse(synthetic_payload["signals"]["production"])
            self.assertFalse(synthetic_payload["signals"]["financial_data"])

    def test_four_runtimes_with_one_state_writer_requires_forensic_mode(self) -> None:
        result = run_atlas(
            "select-mode",
            "--project",
            FIXTURES_ROOT / "quick_cli",
            "--runtime-count",
            "4",
            "--store-count",
            "1",
        )
        self.assertEqual(parse_mode_output(self, result), "FORENSIC")
        payload = json.loads(result.stdout)
        self.assertIn("four or more runtimes", " ".join(payload["reasons"]).lower())

    def test_select_mode_rejects_negative_count_inputs(self) -> None:
        for option in (
            "--runtime-count",
            "--store-count",
            "--legacy-implementations",
            "--team-size",
        ):
            with self.subTest(option=option):
                result = run_atlas("select-mode", "--mode", "QUICK", option, "-1")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("non-negative integer", result.stderr)

    def test_explicit_mode_override_wins_and_invalid_mode_fails(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        fixture = FIXTURES_ROOT / "quick_cli"
        for mode in MODES:
            with self.subTest(mode=mode):
                result = run_atlas("select-mode", "--project", fixture, "--mode", mode)
                self.assertEqual(parse_mode_output(self, result), mode)
        invalid = run_atlas("select-mode", "--project", fixture, "--mode", "DEEP")
        self.assertNotEqual(invalid.returncode, 0)

    def test_inventory_finds_runtime_state_authority_retry_and_unknowns_without_leaks(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        for fixture_name in FIXTURE_NAMES:
            fixture = FIXTURES_ROOT / fixture_name
            oracle = load_oracle(self, fixture_name)
            with self.subTest(fixture=fixture_name), tempfile.TemporaryDirectory(
                prefix=f"atlas inventory {fixture_name} "
            ) as temp_dir:
                output = Path(temp_dir) / "inventory output.json"
                result = run_atlas("inventory", "--project", fixture, "--output", output)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = load_json(self, output)
                self.assertEqual(str(payload.get("mode", "")).upper(), oracle["expected_mode"])
                for category, expected_paths in oracle["inventory"].items():
                    self.assertIn(category, payload)
                    serialized = json.dumps(payload[category], sort_keys=True)
                    for relative_path in expected_paths:
                        self.assertIn(relative_path, serialized, f"{category} missed {relative_path}")
                serialized_payload = output.read_text(encoding="utf-8")
                self.assertNotIn(str(fixture.resolve()), serialized_payload)
                self.assertNotRegex(serialized_payload, r"/(?:Users|home)/")
                self.assertNotRegex(serialized_payload, r"\.atlas-private|\.private")

    def test_init_selects_templates_and_outputs_validate(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        for fixture_name in FIXTURE_NAMES:
            fixture = FIXTURES_ROOT / fixture_name
            oracle = load_oracle(self, fixture_name)
            with self.subTest(fixture=fixture_name), tempfile.TemporaryDirectory(
                prefix=f"atlas init {fixture_name} "
            ) as temp_dir:
                output = Path(temp_dir) / "atlas output"
                result = run_atlas("init", "--project", fixture, "--output", output)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_oracle_output(output, oracle)

                validation = run_atlas(
                    "validate",
                    "--atlas",
                    output,
                    "--project",
                    fixture,
                    "--mode",
                    oracle["expected_mode"],
                    "--draft",
                )
                self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_validate_distinguishes_draft_scaffolds_from_completion(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas validation stage ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            structural = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD", "--draft"
            )
            self.assertEqual(structural.returncode, 0, structural.stderr)
            completion = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD"
            )
            self.assertNotEqual(completion.returncode, 0)
            self.assertIn("draft scaffold", f"{completion.stdout}\n{completion.stderr}".lower())

    def test_standard_completion_accepts_a_fully_materialized_public_fixture(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas standard completion ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_standard_contract(fixture, output)

            completion = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD"
            )
            self.assertEqual(completion.returncode, 0, completion.stderr)
            payload = json.loads(completion.stdout)
            self.assertEqual(payload["mode"], "STANDARD")
            self.assertEqual(payload["status"], "valid")
            self.assertEqual(payload["validation"], "completion")
            self.assertEqual(payload["artifacts"], 12)
            self.assertEqual(len([path for path in output.iterdir() if path.is_file()]), 12)

    def test_completion_rejects_a_partially_edited_scaffold(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas partial scaffold ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            index = output / "ATLAS_INDEX.md"
            index.write_text(
                index.read_text(encoding="utf-8") + "\nOne narrative sentence was edited.\n",
                encoding="utf-8",
            )
            completion = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD"
            )
            self.assertNotEqual(completion.returncode, 0)
            self.assertIn(
                "draft",
                f"{completion.stdout}\n{completion.stderr}".lower(),
                "editing one artifact must not make untouched draft markers completion-ready",
            )

    def test_standard_completion_rejects_append_only_scaffolds_and_unsourced_current_rows(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas standard false completion ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            for artifact in output.glob("*.md"):
                artifact.write_text(
                    artifact.read_text(encoding="utf-8")
                    + "\nArbitrary completion sentence without retained project evidence.\n",
                    encoding="utf-8",
                )
            add_table_rows(
                output / "PRODUCT_AND_REQUIREMENTS.md",
                "| ID | Claim kind | Requirement | Source | Status |",
                "| --- | --- | --- | --- | --- |",
                ["| REQ-1 | CONFIRMED | Service accepts work | reviewer note | ACTIVE |"],
            )
            add_table_rows(
                output / "FINDINGS_AND_DISPOSITIONS.md",
                "| ID | Claim kind | Severity | Finding | Affected scope | Evidence | Impact | Disposition | Prerequisites | Verification | Rollback | Status |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                [
                    "| F-1 | CONFIRMED | P2 | Writer is reachable | service | reviewer note | Duplicate effects | KEEP | Confirm owner | Run focused test | Restore prior route | ACTIVE |"
                ],
            )
            add_table_rows(
                output / "MIGRATION_PLAN.md",
                "| Stage | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                [
                    "| M-1 | Retain writer | Owner confirms | No data movement | Flow passes | Static checks | Maintainer | Restore prior route | TARGET |"
                ],
            )

            completion = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD"
            )
            self.assertNotEqual(completion.returncode, 0)
            diagnostic = f"{completion.stdout}\n{completion.stderr}".lower()
            self.assertIn("scaffold section", diagnostic)
            self.assertIn("project-relative source", diagnostic)

    def test_standard_completion_requires_dynamic_sections_but_accepts_heading_extensions(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas standard dynamic section ") as temp_dir:
            root = Path(temp_dir)
            missing_output = root / "missing"
            initialized = run_atlas(
                "init",
                "--project",
                fixture,
                "--output",
                missing_output,
                "--mode",
                "STANDARD",
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            security = missing_output / "QUALITY_SECURITY_AND_OPERATIONS.md"
            security.write_text(
                security.read_text(encoding="utf-8").replace("## Security\n", ""),
                encoding="utf-8",
            )
            index = missing_output / "ATLAS_INDEX.md"
            index.write_text(
                index.read_text(encoding="utf-8").replace("## Document Map\n", ""),
                encoding="utf-8",
            )
            missing = run_atlas(
                "validate",
                "--atlas",
                missing_output,
                "--project",
                fixture,
                "--mode",
                "STANDARD",
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn(
                "missing dynamic section ## security",
                f"{missing.stdout}\n{missing.stderr}".lower(),
            )
            self.assertIn(
                "missing static section ## document map",
                f"{missing.stdout}\n{missing.stderr}".lower(),
            )

            extended_output = root / "extended"
            initialized = run_atlas(
                "init",
                "--project",
                fixture,
                "--output",
                extended_output,
                "--mode",
                "STANDARD",
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            security = extended_output / "QUALITY_SECURITY_AND_OPERATIONS.md"
            security.write_text(
                security.read_text(encoding="utf-8").replace(
                    "## Security\n", "## Security and Privacy\n"
                ),
                encoding="utf-8",
            )
            extended = run_atlas(
                "validate",
                "--atlas",
                extended_output,
                "--project",
                fixture,
                "--mode",
                "STANDARD",
            )
            self.assertNotEqual(extended.returncode, 0)
            self.assertNotIn(
                "missing dynamic section ## security",
                f"{extended.stdout}\n{extended.stderr}".lower(),
            )

    def test_standard_completion_rejects_fenced_required_heading_spoof(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas fenced heading spoof ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_standard_contract(fixture, output)
            security = output / "QUALITY_SECURITY_AND_OPERATIONS.md"
            security.write_text(
                security.read_text(encoding="utf-8").replace(
                    "## Security\n",
                    "```text\n## Security\n```\n",
                    1,
                ),
                encoding="utf-8",
            )

            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "STANDARD",
            )

            self.assertNotEqual(completion.returncode, 0)
            self.assertIn(
                "missing dynamic section ## security",
                f"{completion.stdout}\n{completion.stderr}".lower(),
            )

    def test_standard_completion_rejects_fenced_canonical_table_spoof(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas fenced table spoof ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_standard_contract(fixture, output)
            requirements = output / "PRODUCT_AND_REQUIREMENTS.md"
            canonical_table = """| ID | Claim kind | Requirement | Source | Status |
| --- | --- | --- | --- | --- |
| REQ-1 | CONFIRMED | Accept a nonblank parcel ID | service/api.py:L7-L12 | ACTIVE |"""
            rendered_decoy = """| Display | Value |
| --- | --- |
| Requirement summary | See the registry above |"""
            requirements.write_text(
                requirements.read_text(encoding="utf-8").replace(
                    canonical_table,
                    f"```text\n{canonical_table}\n```\n\n{rendered_decoy}",
                    1,
                ),
                encoding="utf-8",
            )

            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "STANDARD",
            )

            self.assertNotEqual(completion.returncode, 0)
            self.assertIn(
                "missing the required table columns",
                f"{completion.stdout}\n{completion.stderr}".lower(),
            )

    def test_standard_completion_rejects_raw_html_canonical_table_spoof(
        self,
    ) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas raw html table spoof ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_standard_contract(fixture, output)
            requirements = output / "PRODUCT_AND_REQUIREMENTS.md"
            canonical_table = """| ID | Claim kind | Requirement | Source | Status |
| --- | --- | --- | --- | --- |
| REQ-1 | CONFIRMED | Accept a nonblank parcel ID | service/api.py:L7-L12 | ACTIVE |"""
            requirements.write_text(
                requirements.read_text(encoding="utf-8").replace(
                    canonical_table,
                    (
                        "<atlas-registry>\n"
                        f"{canonical_table}\n"
                        "</atlas-registry>\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )

            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "STANDARD",
            )

            self.assertNotEqual(completion.returncode, 0)
            self.assertIn(
                "missing the required table columns",
                f"{completion.stdout}\n{completion.stderr}".lower(),
            )

    def test_standard_completion_rejects_escaped_backtick_html_spoof(
        self,
    ) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas escaped html spoof ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_standard_contract(fixture, output)
            requirements = output / "PRODUCT_AND_REQUIREMENTS.md"
            canonical_table = """| ID | Claim kind | Requirement | Source | Status |
| --- | --- | --- | --- | --- |
| REQ-1 | CONFIRMED | Accept a nonblank parcel ID | service/api.py:L7-L12 | ACTIVE |"""
            requirements.write_text(
                requirements.read_text(encoding="utf-8").replace(
                    canonical_table,
                    (
                        "\\`\n"
                        "<script>\n"
                        f"{canonical_table}\n"
                        "</script>\n"
                        "\\`\n"
                    ),
                    1,
                ),
                encoding="utf-8",
            )

            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "STANDARD",
            )

            self.assertNotEqual(completion.returncode, 0)
            self.assertIn(
                "missing the required table columns",
                f"{completion.stdout}\n{completion.stderr}".lower(),
            )

    def test_standard_completion_rejects_raw_html_after_block_prefixes(
        self,
    ) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas html block prefix ") as temp_dir:
            canonical_table = """| ID | Claim kind | Requirement | Source | Status |
| --- | --- | --- | --- | --- |
| REQ-1 | CONFIRMED | Accept a nonblank parcel ID | service/api.py:L7-L12 | ACTIVE |"""
            raw_variants = (
                (
                    "`foo\n"
                    "<script>\n"
                    f"{canonical_table}\n"
                    "</script>\n"
                    "bar`\n"
                ),
                (
                    "[ref]: /url\n"
                    "<atlas-registry>\n"
                    f"{canonical_table}\n"
                    "</atlas-registry>\n\n"
                ),
                (
                    "[ref]:\n"
                    "  README.md\n"
                    "<atlas-registry>\n"
                    f"{canonical_table}\n"
                    "</atlas-registry>\n\n"
                ),
                (
                    "[ref]:\n"
                    "  README.md\n"
                    '  "Architecture reference"\n'
                    "<atlas-registry>\n"
                    f"{canonical_table}\n"
                    "</atlas-registry>\n\n"
                ),
                (
                    "- [ref]:\n"
                    "  README.md\n"
                    "  <atlas-registry>\n"
                    + "\n".join(
                        f"  {line}" for line in canonical_table.splitlines()
                    )
                    + "\n  </atlas-registry>\n\n"
                ),
                (
                    '[ref]: README.md "Architecture\n'
                    'reference"\n'
                    "<atlas-registry>\n"
                    f"{canonical_table}\n"
                    "</atlas-registry>\n\n"
                ),
                (
                    "* * *\n"
                    "<atlas-registry>\n"
                    f"{canonical_table}\n"
                    "</atlas-registry>\n\n"
                ),
                (
                    "> quoted paragraph\n"
                    "<atlas-registry>\n"
                    f"{canonical_table}\n"
                    "</atlas-registry>\n\n"
                ),
                (
                    "- list paragraph\n"
                    "<atlas-registry>\n"
                    f"{canonical_table}\n"
                    "</atlas-registry>\n\n"
                ),
            )
            for index, raw_variant in enumerate(raw_variants):
                with self.subTest(raw_variant=raw_variant):
                    output = Path(temp_dir) / f"atlas-{index}"
                    self.materialize_minimal_standard_contract(fixture, output)
                    requirements = output / "PRODUCT_AND_REQUIREMENTS.md"
                    requirements.write_text(
                        requirements.read_text(encoding="utf-8").replace(
                            canonical_table,
                            raw_variant,
                            1,
                        ),
                        encoding="utf-8",
                    )

                    completion = run_atlas(
                        "validate",
                        "--atlas",
                        output,
                        "--project",
                        fixture,
                        "--mode",
                        "STANDARD",
                    )

                    self.assertNotEqual(completion.returncode, 0)
                    self.assertIn(
                        "missing the required table columns",
                        f"{completion.stdout}\n{completion.stderr}".lower(),
                    )

    def test_standard_completion_rejects_empty_dynamic_section_body(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas empty dynamic section ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_standard_contract(fixture, output)
            architecture = output / "CURRENT_ARCHITECTURE.md"
            architecture.write_text(
                re.sub(
                    r"(?ms)(^## Components[^\n]*\n).*?(?=^## )",
                    r"\1\n",
                    architecture.read_text(encoding="utf-8"),
                    count=1,
                ),
                encoding="utf-8",
            )

            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "STANDARD",
            )

            self.assertNotEqual(completion.returncode, 0)
            self.assertIn(
                "section ## components lacks substantive content",
                f"{completion.stdout}\n{completion.stderr}".lower(),
            )

    def test_standard_completion_rejects_html_comment_only_dynamic_section(
        self,
    ) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas comment dynamic section ") as temp_dir:
            hidden_bodies = (
                "<!-- no actual section evidence here -->",
                "<!-- hidden -->\n    no visible section evidence here",
                "<script>\nsubstantive hidden words\n</script>",
                (
                    "<atlas-notes>\n"
                    "substantive hidden words\n"
                    "</atlas-notes>\n"
                ),
                "<span></span>",
                "<span><!-- hidden architecture words --></span>",
                "<span><?hidden architecture words?></span>",
                "<span><![CDATA[hidden architecture words]]></span>",
                "<span><!DOCTYPE hidden architecture words></span>",
                "[](README.md)",
            )
            for index, hidden_body in enumerate(hidden_bodies):
                with self.subTest(hidden_body=hidden_body):
                    output = Path(temp_dir) / f"atlas-{index}"
                    self.materialize_minimal_standard_contract(fixture, output)
                    architecture = output / "CURRENT_ARCHITECTURE.md"
                    architecture.write_text(
                        re.sub(
                            r"(?ms)(^## Components[^\n]*\n).*?(?=^## )",
                            rf"\1\n{hidden_body}\n\n",
                            architecture.read_text(encoding="utf-8"),
                            count=1,
                        ),
                        encoding="utf-8",
                    )

                    completion = run_atlas(
                        "validate",
                        "--atlas",
                        output,
                        "--project",
                        fixture,
                        "--mode",
                        "STANDARD",
                    )

                    self.assertNotEqual(completion.returncode, 0)
                    self.assertIn(
                        "section ## components lacks substantive content",
                        f"{completion.stdout}\n{completion.stderr}".lower(),
                    )

    def test_standard_completion_accepts_unambiguous_table_section_extension(
        self,
    ) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas table heading extension ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_standard_contract(fixture, output)
            requirements = output / "PRODUCT_AND_REQUIREMENTS.md"
            requirements.write_text(
                requirements.read_text(encoding="utf-8").replace(
                    "## Requirements\n",
                    "## Requirements Registry\n",
                    1,
                ),
                encoding="utf-8",
            )

            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "STANDARD",
            )

            self.assertEqual(completion.returncode, 0, completion.stderr)

    def test_standard_completion_rejects_scaffold_prose_with_suffix_or_emphasis(
        self,
    ) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas retained scaffold prose ") as temp_dir:
            retained_bodies = (
                "No component has been confirmed yet. Additional project notes are pending.",
                "**No component has been confirmed yet.** Additional project notes are pending.",
            )
            for index, retained_body in enumerate(retained_bodies):
                with self.subTest(retained_body=retained_body):
                    output = Path(temp_dir) / f"atlas-{index}"
                    self.materialize_minimal_standard_contract(fixture, output)
                    architecture = output / "CURRENT_ARCHITECTURE.md"
                    architecture.write_text(
                        re.sub(
                            r"(?ms)(^## Components[^\n]*\n).*?(?=^## )",
                            rf"\1\n{retained_body}\n\n",
                            architecture.read_text(encoding="utf-8"),
                            count=1,
                        ),
                        encoding="utf-8",
                    )

                    completion = run_atlas(
                        "validate",
                        "--atlas",
                        output,
                        "--project",
                        fixture,
                        "--mode",
                        "STANDARD",
                    )

                    self.assertNotEqual(completion.returncode, 0)
                    self.assertIn(
                        "retains canonical draft content",
                        f"{completion.stdout}\n{completion.stderr}".lower(),
                    )

    def test_completion_rejects_one_required_artifact_restored_to_its_scaffold(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas one restored scaffold ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_forensic_contract(fixture, output)
            restored = output / "CURRENT_ARCHITECTURE.md"
            restored.write_bytes(
                (CORE_SKILL / "assets/templates/forensic/CURRENT_ARCHITECTURE.md").read_bytes()
            )
            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertNotEqual(completion.returncode, 0)
            self.assertIn(
                "current_architecture.md remains an untouched draft scaffold",
                f"{completion.stdout}\n{completion.stderr}".lower(),
            )

    def test_completion_rejects_reserved_artifacts_from_another_mode(self) -> None:
        fixture = FIXTURES_ROOT / "quick_cli"
        with tempfile.TemporaryDirectory(prefix="atlas mixed reserved artifacts ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "QUICK"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (output / "TRACEABILITY.tsv").write_text(TRACE_HEADER, encoding="utf-8")
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK", "--draft"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unexpected atlas artifact", f"{result.stdout}\n{result.stderr}".lower())

    def test_forensic_completion_requires_snapshot_and_replayed_commands(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas forensic completion gates ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            completion = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "FORENSIC"
            )
            self.assertNotEqual(completion.returncode, 0)
            diagnostic = f"{completion.stdout}\n{completion.stderr}".lower()
            self.assertIn("replay-command-evidence", diagnostic)
            self.assertIn("source_snapshot.json", diagnostic)

    def test_forensic_completion_requires_at_least_one_replayed_command_row(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas missing command evidence ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_forensic_contract(fixture, output)
            traceability = output / "TRACEABILITY.tsv"
            lines = traceability.read_text(encoding="utf-8").splitlines()
            traceability.write_text(
                "\n".join(line for line in lines if not line.startswith("EV-CMD\t")) + "\n",
                encoding="utf-8",
            )
            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertNotEqual(completion.returncode, 0)
            self.assertIn(
                "at least one active command",
                f"{completion.stdout}\n{completion.stderr}".lower(),
            )

    def test_forensic_traceability_rejects_unresolved_confirmed_or_target_evidence(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        for claim_kind in ("CONFIRMED", "TARGET"):
            with self.subTest(claim_kind=claim_kind), tempfile.TemporaryDirectory(
                prefix="atlas incompatible unresolved evidence "
            ) as temp_dir:
                output = Path(temp_dir) / "atlas"
                initialized = run_atlas(
                    "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                (output / "TRACEABILITY.tsv").write_text(
                    TRACE_HEADER
                    + f"EV-1\t{claim_kind}\tMaterial claim\tUNRESOLVED\tunresolved/EV-1\t"
                    + "2026-07-22\tACTIVE\t-\tEvidence is not available.\n",
                    encoding="utf-8",
                )
                result = run_atlas(
                    "validate",
                    "--atlas",
                    output,
                    "--project",
                    fixture,
                    "--mode",
                    "FORENSIC",
                    "--draft",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unresolved", f"{result.stdout}\n{result.stderr}".lower())

    def test_forensic_traceability_requires_exact_status_and_timestamp_forms(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        mutations = (
            ("2026-07-22\tACTIVE", "2026-7-22\tACTIVE", "observed_at"),
            ("2026-07-22\tACTIVE", "2026-07-22\tactive", "status"),
            ("2026-07-22\tACTIVE", "2026-07-22\tOPEN", "status"),
        )
        for old, new, expected in mutations:
            with self.subTest(new=new), tempfile.TemporaryDirectory(
                prefix="atlas strict evidence form "
            ) as temp_dir:
                output = Path(temp_dir) / "atlas"
                initialized = run_atlas(
                    "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                row = (
                    TRACE_HEADER
                    + "EV-1\tUNKNOWN\tMaterial gap\tUNRESOLVED\tunresolved/EV-1\t"
                    + "2026-07-22\tACTIVE\t-\tEvidence is not available.\n"
                )
                self.assertIn(old, row)
                (output / "TRACEABILITY.tsv").write_text(
                    row.replace(old, new, 1), encoding="utf-8"
                )
                result = run_atlas(
                    "validate",
                    "--atlas",
                    output,
                    "--project",
                    fixture,
                    "--mode",
                    "FORENSIC",
                    "--draft",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, f"{result.stdout}\n{result.stderr}".lower())

    def test_forensic_completion_rejects_zero_project_source_evidence_scope(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas zero project evidence ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            old_snapshot = self.materialize_minimal_forensic_contract(fixture, output)
            traceability = output / "TRACEABILITY.tsv"
            rewritten: list[str] = []
            for line in traceability.read_text(encoding="utf-8").splitlines():
                cells = line.split("\t")
                if len(cells) == 9 and cells[3] == "FILE":
                    cells[3] = "EXTERNAL"
                    cells[4] = f"primary/{cells[0]}"
                    line = "\t".join(cells)
                elif len(cells) == 9 and cells[3] == "COMMAND":
                    cells[6] = "STALE"
                    line = "\t".join(cells)
                rewritten.append(line)
            traceability.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
            snapshotted = run_atlas(
                "snapshot",
                "--atlas",
                output,
                "--project",
                fixture,
                "--output",
                output / "SOURCE_SNAPSHOT.json",
            )
            self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
            new_snapshot = str(
                load_json(self, output / "SOURCE_SNAPSHOT.json")["review_input"]["sha256"]
            )
            handoff = output / "LIVE_HANDOFF.md"
            handoff.write_text(
                handoff.read_text(encoding="utf-8").replace(old_snapshot, new_snapshot),
                encoding="utf-8",
            )
            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertNotEqual(completion.returncode, 0)
            self.assertIn("project-source evidence", f"{completion.stdout}\n{completion.stderr}".lower())

    def test_forensic_completion_accepts_exact_material_coverage_and_reviews(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas complete forensic contract ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_forensic_contract(fixture, output)
            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertEqual(completion.returncode, 0, completion.stderr)

    def test_forensic_completion_rejects_unsafe_markdown_source_reference(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas unsafe markdown source ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            previous_review_input = self.materialize_minimal_forensic_contract(
                fixture, output
            )
            architecture = output / "CURRENT_ARCHITECTURE.md"
            architecture.write_text(
                architecture.read_text(encoding="utf-8")
                + "\nUnsafe external citation: `../../outside/private.py:L1`.\n",
                encoding="utf-8",
            )

            snapshotted = run_atlas(
                "snapshot",
                "--atlas",
                output,
                "--project",
                fixture,
                "--output",
                output / "SOURCE_SNAPSHOT.json",
            )
            self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
            current_review_input = str(
                load_json(self, output / "SOURCE_SNAPSHOT.json")["review_input"][
                    "sha256"
                ]
            )
            handoff = output / "LIVE_HANDOFF.md"
            handoff.write_text(
                handoff.read_text(encoding="utf-8").replace(
                    previous_review_input, current_review_input
                ),
                encoding="utf-8",
            )
            refreshed = run_atlas(
                "snapshot",
                "--atlas",
                output,
                "--project",
                fixture,
                "--output",
                output / "SOURCE_SNAPSHOT.json",
            )
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)

            completion = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertNotEqual(completion.returncode, 0)
            diagnostic = f"{completion.stdout}\n{completion.stderr}".lower()
            self.assertIn("unsafe project source reference", diagnostic)

    def test_forensic_completion_does_not_match_registry_rows_by_fact_id(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas explicit registry refs ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            self.materialize_minimal_forensic_contract(fixture, output)
            traceability = output / "TRACEABILITY.tsv"
            traceability.write_text(
                traceability.read_text(encoding="utf-8").replace(
                    "PRODUCT_AND_REQUIREMENTS.md#requirements/REQ-1", "-", 1
                ),
                encoding="utf-8",
            )
            result = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertNotEqual(result.returncode, 0)
            diagnostic = f"{result.stdout}\n{result.stderr}"
            self.assertIn("PRODUCT_AND_REQUIREMENTS.md#requirements/REQ-1", diagnostic)

    def test_forensic_completion_rejects_dangling_mismatched_or_stale_material_links(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        mutations = (
            (
                "PRODUCT_AND_REQUIREMENTS.md#requirements/REQ-1",
                "PRODUCT_AND_REQUIREMENTS.md#requirements/REQ-MISSING",
                "dangling material atlas_ref",
            ),
            (
                "REQ-1\tCONFIRMED\tGateway accepts a request",
                "REQ-1\tCONFIRMED\tGateway accepts a different request",
                "claim text does not match",
            ),
            (
                "REQ-1\tCONFIRMED\tGateway accepts a request",
                "REQ-1\tTARGET\tGateway accepts a request",
                "claim_kind does not match",
            ),
            (
                "\tACTIVE\tPRODUCT_AND_REQUIREMENTS.md#requirements/REQ-1\t",
                "\tSTALE\tPRODUCT_AND_REQUIREMENTS.md#requirements/REQ-1\t",
                "lacks ACTIVE traceability coverage",
            ),
        )
        for old, new, expected in mutations:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory(
                prefix="atlas invalid material link "
            ) as temp_dir:
                output = Path(temp_dir) / "atlas"
                self.materialize_minimal_forensic_contract(fixture, output)
                traceability = output / "TRACEABILITY.tsv"
                original = traceability.read_text(encoding="utf-8")
                self.assertIn(old, original)
                traceability.write_text(original.replace(old, new, 1), encoding="utf-8")
                result = run_atlas(
                    "validate",
                    "--atlas",
                    output,
                    "--project",
                    fixture,
                    "--mode",
                    "FORENSIC",
                    "--replay-command-evidence",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected.lower(), f"{result.stdout}\n{result.stderr}".lower())

    def test_forensic_completion_requires_snapshot_bound_pass_reviews(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        mutations = (
            ("| PASS | 0 | 0 |", "| FAIL | 0 | 0 |", "PASS"),
            ("| PASS | 0 | 0 |", "| PASS | 1 | 0 |", "Critical"),
            ("| PASS | 0 | 0 |", "| PASS | 0 | 1 |", "Important"),
            ("Checked material claim coverage and contradiction cases.", "UNKNOWN", "summary"),
        )
        for old, new, expected in mutations:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory(
                prefix="atlas invalid review gate "
            ) as temp_dir:
                output = Path(temp_dir) / "atlas"
                self.materialize_minimal_forensic_contract(fixture, output)
                handoff = output / "LIVE_HANDOFF.md"
                handoff.write_text(
                    handoff.read_text(encoding="utf-8").replace(old, new, 1),
                    encoding="utf-8",
                )
                result = run_atlas(
                    "validate",
                    "--atlas",
                    output,
                    "--project",
                    fixture,
                    "--mode",
                    "FORENSIC",
                    "--replay-command-evidence",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected.lower(), f"{result.stdout}\n{result.stderr}".lower())

    def test_forensic_completion_rejects_review_bound_to_another_snapshot(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas stale review snapshot ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            snapshot_sha = self.materialize_minimal_forensic_contract(fixture, output)
            replacement = "f" * 64 if snapshot_sha != "f" * 64 else "e" * 64
            handoff = output / "LIVE_HANDOFF.md"
            original = handoff.read_text(encoding="utf-8")
            self.assertIn(snapshot_sha, original)
            handoff.write_text(
                original.replace(snapshot_sha, replacement, 1), encoding="utf-8"
            )
            result = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "not bound to the current canonical review input",
                f"{result.stdout}\n{result.stderr}".lower(),
            )

    def test_forensic_reviews_require_real_fresh_timestamps_and_substantive_evidence(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        mutations = (
            ("2026-07-22T00:00:00Z", "2026-02-30T00:00:00Z", "timestamp"),
            ("2026-07-22T00:00:00Z", "2026-7-22T00:00:00Z", "timestamp"),
            ("2026-07-22T00:00:00Z", "2026-07-20T00:00:00Z", "chronolog"),
            ("2026-07-22T00:00:00Z", "2026-08-01T00:00:00Z", "future"),
            (
                "Checked material claim coverage and contradiction cases.",
                "x",
                "evidence summary",
            ),
            (
                "Runtime behavior outside the inspected fixture remains unobserved.",
                "x",
                "remaining limits",
            ),
        )
        for old, new, expected in mutations:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory(
                prefix="atlas invalid review evidence "
            ) as temp_dir:
                output = Path(temp_dir) / "atlas"
                self.materialize_minimal_forensic_contract(fixture, output)
                handoff = output / "LIVE_HANDOFF.md"
                original = handoff.read_text(encoding="utf-8")
                self.assertIn(old, original)
                handoff.write_text(original.replace(old, new, 1), encoding="utf-8")
                result = run_atlas(
                    "validate",
                    "--atlas",
                    output,
                    "--project",
                    fixture,
                    "--mode",
                    "FORENSIC",
                    "--replay-command-evidence",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, f"{result.stdout}\n{result.stderr}".lower())

    def test_forensic_reviews_accept_cyrillic_substantive_evidence(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas cyrillic review evidence ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            review_input = self.materialize_minimal_forensic_contract(fixture, output)
            handoff = output / "LIVE_HANDOFF.md"
            cyrillic = (
                handoff.read_text(encoding="utf-8")
                .replace(
                    "Checked material claim coverage and contradiction cases.",
                    "Проверены существенные утверждения, противоречия и границы доказательств.",
                )
                .replace(
                    "Runtime behavior outside the inspected fixture remains unobserved.",
                    "Продакшен и внешние системы остались за границами проверки.",
                )
                .replace(
                    "Checked safe inventory boundaries and snapshot reads.",
                    "Проверены безопасные границы инвентаря и чтение снимка проекта.",
                )
                .replace(
                    "External identity and production access remain outside scope.",
                    "Внешняя идентификация и продакшен-доступ не проверялись.",
                )
            )
            handoff.write_text(cyrillic, encoding="utf-8")
            refreshed = run_atlas(
                "snapshot",
                "--atlas",
                output,
                "--project",
                fixture,
                "--output",
                output / "SOURCE_SNAPSHOT.json",
            )
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertEqual(
                load_json(self, output / "SOURCE_SNAPSHOT.json")["review_input"]["sha256"],
                review_input,
            )
            validated = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_forensic_review_binding_detects_post_review_artifact_change(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        for mutation in (
            "artifact",
            "non-review traceability",
            "non-review traceability whitespace",
            "non-review handoff whitespace",
            "command-only source",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="atlas post review drift "
            ) as temp_dir:
                project = fixture
                if mutation == "command-only source":
                    project = Path(temp_dir) / "project"
                    shutil.copytree(fixture, project)
                output = Path(temp_dir) / "atlas"
                self.materialize_minimal_forensic_contract(project, output)
                if mutation == "artifact":
                    architecture = output / "CURRENT_ARCHITECTURE.md"
                    architecture.write_text(
                        architecture.read_text(encoding="utf-8")
                        + "\nCONFIRMED: a material runtime ownership claim changed after review.\n",
                        encoding="utf-8",
                    )
                elif mutation in {
                    "non-review traceability",
                    "non-review traceability whitespace",
                }:
                    traceability = output / "TRACEABILITY.tsv"
                    original = traceability.read_text(encoding="utf-8")
                    marker = "PRODUCT_AND_REQUIREMENTS.md#requirements/REQ-1\t\n"
                    self.assertIn(marker, original)
                    replacement = (
                        marker.rstrip("\n") + "post-review drift\n"
                        if mutation == "non-review traceability"
                        else marker.rstrip("\n") + " \n"
                    )
                    traceability.write_text(
                        original.replace(marker, replacement, 1),
                        encoding="utf-8",
                    )
                elif mutation == "non-review handoff whitespace":
                    handoff = output / "LIVE_HANDOFF.md"
                    original = handoff.read_text(encoding="utf-8")
                    self.assertTrue(original.endswith("\n"))
                    handoff.write_text(original[:-1] + " \n", encoding="utf-8")
                else:
                    command = [
                        "rg",
                        "--no-config",
                        "--sort",
                        "path",
                        "--line-number",
                        "--fixed-strings",
                        "write_event",
                        "legacy_system",
                    ]
                    before = run_command(command, cwd=project)
                    self.assertEqual(before.returncode, 0, before.stderr)
                    command_only_source = project / "legacy_system" / "cron.py"
                    command_only_source.write_text(
                        command_only_source.read_text(encoding="utf-8")
                        + "\nCOMMAND_ONLY_DRIFT = True\n",
                        encoding="utf-8",
                    )
                    after = run_command(command, cwd=project)
                    self.assertEqual(after.returncode, 0, after.stderr)
                    self.assertEqual(
                        hashlib.sha256(before.stdout.encode("utf-8")).hexdigest(),
                        hashlib.sha256(after.stdout.encode("utf-8")).hexdigest(),
                    )
                result = run_atlas(
                    "validate",
                    "--atlas",
                    output,
                    "--project",
                    project,
                    "--mode",
                    "FORENSIC",
                    "--replay-command-evidence",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("review", f"{result.stdout}\n{result.stderr}".lower())

    def assert_oracle_output(self, output: Path, oracle: dict[str, object]) -> None:
        assert_directory(self, output)
        for filename in oracle["required_artifacts"]:  # type: ignore[index]
            assert_file(self, output / str(filename))
        for filename in oracle["forbidden_artifacts"]:  # type: ignore[index]
            self.assertFalse((output / str(filename)).exists(), f"unexpected artifact: {filename}")
        for filename, markers in oracle["required_markers"].items():  # type: ignore[union-attr]
            text = (output / str(filename)).read_text(encoding="utf-8")
            for marker in markers:
                self.assertIn(str(marker), text, f"{filename} is missing marker {marker!r}")

        all_output = "\n".join(
            path.read_text(encoding="utf-8")
            for path in output.rglob("*")
            if path.is_file()
        )
        self.assertNotRegex(all_output, r"/(?:Users|home)/")

        if oracle["expected_mode"] == "QUICK":
            visible_files = [
                path
                for path in output.rglob("*")
                if path.is_file() and not any(part.startswith(".") for part in path.relative_to(output).parts)
            ]
            self.assertEqual(
                [path.relative_to(output).as_posix() for path in visible_files],
                ["PROJECT_ATLAS.md"],
                "QUICK mode must remain a single compact document",
            )

    def test_validate_rejects_missing_standard_artifacts(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        with tempfile.TemporaryDirectory(prefix="atlas invalid output ") as temp_dir:
            output = Path(temp_dir) / "incomplete atlas"
            output.mkdir()
            (output / "ATLAS_INDEX.md").write_text("# Project Atlas\n", encoding="utf-8")
            result = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                FIXTURES_ROOT / "standard_service",
                "--mode",
                "STANDARD",
            )
            self.assertNotEqual(result.returncode, 0)
            diagnostic = f"{result.stdout}\n{result.stderr}"
            self.assertIn("CURRENT_ARCHITECTURE.md", diagnostic)

    def test_validate_rejects_malformed_forensic_traceability(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas traceability invalid ") as temp_dir:
            output = Path(temp_dir) / "forensic atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            traceability = output / "TRACEABILITY.tsv"
            assert_file(self, traceability)
            traceability.write_text("bad\theader\n", encoding="utf-8")
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "FORENSIC"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("trace", f"{result.stdout}\n{result.stderr}".lower())

    def test_validate_requires_project_root_for_source_checks(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas validate project ") as temp_dir:
            output = Path(temp_dir) / "standard atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            result = run_atlas("validate", "--atlas", output, "--mode", "STANDARD")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--project", f"{result.stdout}\n{result.stderr}")

    def test_validate_checks_declared_mode_even_with_explicit_mode_argument(self) -> None:
        cases = (
            ("QUICK", "quick_cli", "PROJECT_ATLAS.md", "Mode: **QUICK** (automatic)"),
            ("STANDARD", "standard_service", "ATLAS_INDEX.md", "Mode: **STANDARD** (automatic)"),
            ("FORENSIC", "forensic_legacy", "ATLAS_INDEX.md", "Mode: **STANDARD**"),
        )
        for mode, fixture_name, marker_file, replacement in cases:
            fixture = FIXTURES_ROOT / fixture_name
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(
                prefix=f"atlas mode declaration {mode.lower()} "
            ) as temp_dir:
                output = Path(temp_dir) / "atlas"
                initialized = run_atlas(
                    "init", "--project", fixture, "--output", output, "--mode", mode
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                marker = output / marker_file
                marker.write_text(
                    marker.read_text(encoding="utf-8").replace(f"Mode: **{mode}**", replacement),
                    encoding="utf-8",
                )
                result = run_atlas(
                    "validate", "--atlas", output, "--project", fixture, "--mode", mode
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("mode", f"{result.stdout}\n{result.stderr}".lower())

    def test_validate_rejects_incomplete_finding_rows(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas incomplete finding ") as temp_dir:
            output = Path(temp_dir) / "standard atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            findings = output / "FINDINGS_AND_DISPOSITIONS.md"
            findings.write_text(
                """# Findings and Dispositions

## Findings

| ID | Claim kind | Severity | Finding | Affected scope | Evidence | Impact | Disposition | Prerequisites | Verification | Rollback | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F-1 | CONFIRMED | P1 | Competing writers | service/state.py:L1 | service/state.py:L1 | Lost updates | Rewrite |  |  |  | OPEN |

## Disposition Vocabulary

Keep, Rewrite, Delete, or Merge.
""",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD"
            )
            self.assertNotEqual(result.returncode, 0)
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
            self.assertRegex(diagnostic, r"prerequisite|verification|rollback")

    def test_validate_rejects_unclassified_requirements_and_incomplete_migration_gates(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas incomplete contracts ") as temp_dir:
            output = Path(temp_dir) / "standard atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            requirements = output / "PRODUCT_AND_REQUIREMENTS.md"
            requirements.write_text(
                """# Product and Requirements

## Purpose

UNKNOWN.

## Users and Outcomes

UNKNOWN.

## Requirements

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| R-1 | Deduplicate every effect | TARGET_ARCHITECTURE.md | proposed |

## Evidence

No current source establishes R-1.
""",
                encoding="utf-8",
            )
            migration = output / "MIGRATION_PLAN.md"
            migration.write_text(
                """# Migration Plan

## Sequence

| Step | Change | Verification gate | Rollback |
| --- | --- | --- | --- |
| M-1 | Replace writer | tests pass | restore old writer |

## Rollback

Restore the old writer.
""",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD"
            )
            self.assertNotEqual(result.returncode, 0)
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
            self.assertIn("claim kind", diagnostic)
            self.assertIn("primary signal", diagnostic)
            self.assertIn("decision authority", diagnostic)

    def test_validate_rejects_decoy_or_competing_canonical_tables(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas competing table ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            requirements = output / "PRODUCT_AND_REQUIREMENTS.md"
            requirements.write_text(
                requirements.read_text(encoding="utf-8").replace(
                    "## Requirements",
                    """## Decoy Registry

| ID | Claim kind | Requirement | Source | Status |
| --- | --- | --- | --- | --- |
| D-1 | TARGET | Decoy | README.md:L1 | OPEN |

## Requirements""",
                ),
                encoding="utf-8",
            )
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD", "--draft"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(
                f"{result.stdout}\n{result.stderr}".lower(), r"competing|exactly one|required section"
            )

    def test_handoff_requires_deterministic_helper_resolution_and_exact_forensic_commands(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas handoff command contract ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            handoff = output / "LIVE_HANDOFF.md"
            original = handoff.read_text(encoding="utf-8")

            stale_locator = original.replace(
                "# Project Atlas helper resolution v1", "# arbitrary first-match helper"
            ).replace("LC_ALL=C sort -u", "head -n 1")
            handoff.write_text(stale_locator, encoding="utf-8")
            locator_result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "FORENSIC", "--draft"
            )
            self.assertNotEqual(locator_result.returncode, 0)
            self.assertIn("helper resolution", f"{locator_result.stdout}\n{locator_result.stderr}".lower())

            handoff.write_text(
                original.replace(" --replay-command-evidence", "").replace(
                    ' --output "$atlas_root/SOURCE_SNAPSHOT.json"', ""
                ),
                encoding="utf-8",
            )
            command_result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "FORENSIC", "--draft"
            )
            self.assertNotEqual(command_result.returncode, 0)
            diagnostic = f"{command_result.stdout}\n{command_result.stderr}".lower()
            self.assertIn("replay-command-evidence", diagnostic)
            self.assertIn("--output", diagnostic)

            handoff.write_text(
                original.replace('test -f "$atlas_script"', 'test -f "$atlas_script"\ntrue'),
                encoding="utf-8",
            )
            extra_command = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--draft",
            )
            self.assertNotEqual(extra_command.returncode, 0)
            self.assertIn("canonical", f"{extra_command.stdout}\n{extra_command.stderr}".lower())

    def test_quick_completion_rejects_one_arbitrary_sentence_on_scaffold(self) -> None:
        fixture = FIXTURES_ROOT / "quick_cli"
        with tempfile.TemporaryDirectory(prefix="atlas quick false completion ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "QUICK"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            quick = output / "PROJECT_ATLAS.md"
            quick.write_text(
                quick.read_text(encoding="utf-8") + "\nOne arbitrary sentence.\n",
                encoding="utf-8",
            )
            completion = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
            )
            self.assertNotEqual(completion.returncode, 0)
            self.assertIn("quick completion", f"{completion.stdout}\n{completion.stderr}".lower())

    def test_quick_completion_accepts_substantive_evidence_validation_and_continuation(self) -> None:
        fixture = FIXTURES_ROOT / "quick_cli"
        with tempfile.TemporaryDirectory(prefix="atlas complete quick contract ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "QUICK"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            quick = output / "PROJECT_ATLAS.md"
            command = (
                "rg --no-config --sort path --line-number --fixed-strings "
                "'def write_state' quick_cli/runtime.py"
            )
            command_result = run_command(
                [
                    "rg",
                    "--no-config",
                    "--sort",
                    "path",
                    "--line-number",
                    "--fixed-strings",
                    "def write_state",
                    "quick_cli/runtime.py",
                ],
                cwd=fixture,
            )
            self.assertEqual(command_result.returncode, 0, command_result.stderr)
            command_digest = hashlib.sha256(command_result.stdout.encode("utf-8")).hexdigest()
            quick.write_text(
                f"""# Project Atlas

Mode: **QUICK**

## Scope and Depth Rationale

The dominant CLI runtime is included; deployment and external providers are excluded because this bounded fixture has one local entry path.

Selected by: The repository signals selected QUICK depth.
Conflicting automatic signals: No conflict; the automatic recommendation was QUICK.
Intentionally omitted coverage: No lower-depth override; external deployment remains outside the declared investigation scope.
Escalation condition: Escalate when another runtime, shared state writer, or production authority boundary is confirmed.

## Evidence Snapshot

Observed at: 2026-07-22T00:00:00Z
Source or worktree snapshot: worktree-manifest-a1b2c3d4

## Purpose

The package exposes a small command that persists one observable local state transition.

## Entry Point

The supported module entry point delegates directly to the runtime implementation.

## Inputs and Outputs

It accepts local command input and writes a deterministic state record inside the fixture boundary.

## Dependencies

Only the Python standard library is required by the mapped execution path.

## Verification

Command: `{command}`
Proof boundary: The bounded command proves the mapped state writer is present in the cited safe source file.

## Exact Validation Result

Exit code: 0
Observed result: The command located the mapped state writer definition in the cited source file.
Stdout SHA-256: {command_digest}

## Risks

Production configuration and concurrent writes remain outside this deliberately bounded QUICK investigation.

## Exclusions

External deployment, provider behavior, and production data are excluded from the one-runtime denominator.

## Evidence Legend

- **CONFIRMED**: directly supported by a project-relative source or captured command.
- **INFERENCE**: reasoned from confirmed evidence but not directly observed.
- **HYPOTHESIS**: testable explanation that still requires discriminating evidence.
- **TARGET**: proposed future state, never evidence of current behavior.
- **UNKNOWN**: not established within the declared scope and snapshot.

## Next Safe Action

Inspect the caller that supplies production inputs before expanding this atlas beyond the local CLI contour.

## Source References

- `quick_cli/runtime.py:L1` owns the mapped runtime behavior.

## Unknowns

- UNKNOWN: production invocation and deployment configuration remain outside the declared scope.
""",
                encoding="utf-8",
            )
            completion = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
            )
            self.assertEqual(completion.returncode, 0, completion.stderr)

            valid_text = quick.read_text(encoding="utf-8")
            decision_lines = (
                "Selected by: The repository signals selected QUICK depth.\n",
                "Conflicting automatic signals: No conflict; the automatic recommendation was QUICK.\n",
                "Intentionally omitted coverage: No lower-depth override; external deployment remains outside the declared investigation scope.\n",
                "Escalation condition: Escalate when another runtime, shared state writer, or production authority boundary is confirmed.\n",
            )
            for decision_line in decision_lines:
                with self.subTest(missing_depth_decision=decision_line.split(":", 1)[0]):
                    quick.write_text(valid_text.replace(decision_line, "", 1), encoding="utf-8")
                    missing_decision = run_atlas(
                        "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
                    )
                    self.assertNotEqual(missing_decision.returncode, 0)
                    self.assertIn(
                        "depth decision",
                        f"{missing_decision.stdout}\n{missing_decision.stderr}".lower(),
                    )
            quick.write_text(
                valid_text.replace(decision_lines[0], decision_lines[0] * 2, 1),
                encoding="utf-8",
            )
            duplicate_decision = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
            )
            self.assertNotEqual(duplicate_decision.returncode, 0)
            self.assertIn(
                "depth decision",
                f"{duplicate_decision.stdout}\n{duplicate_decision.stderr}".lower(),
            )
            quick.write_text(valid_text, encoding="utf-8")
            cyrillic_text = (
                valid_text.replace(
                    "The package exposes a small command that persists one observable local state transition.",
                    "Пакет запускает небольшую команду и сохраняет наблюдаемый переход локального состояния.",
                )
                .replace(
                    "The bounded command proves the mapped state writer is present in the cited safe source file.",
                    "Ограниченная проверка подтверждает наличие описанного обработчика в указанном безопасном исходнике.",
                )
                .replace(
                    "The command located the mapped state writer definition in the cited source file.",
                    "Команда нашла определение обработчика состояния в указанном исходном файле.",
                )
                .replace(
                    "- UNKNOWN: production invocation and deployment configuration remain outside the declared scope.",
                    "- НЕИЗВЕСТНО: способ запуска в продакшене остаётся за границами заявленного исследования.",
                )
            )
            quick.write_text(cyrillic_text, encoding="utf-8")
            cyrillic_completion = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
            )
            self.assertEqual(cyrillic_completion.returncode, 0, cyrillic_completion.stderr)

            quick.write_text(
                valid_text.replace(
                    "## Unknowns\n\n- UNKNOWN: production invocation and deployment configuration remain outside the declared scope.\n",
                    "## Unknowns\n",
                ),
                encoding="utf-8",
            )
            blank_unknowns = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
            )
            self.assertNotEqual(blank_unknowns.returncode, 0)
            self.assertIn("unknowns", f"{blank_unknowns.stdout}\n{blank_unknowns.stderr}".lower())

            label_only_legend = valid_text
            for claim_kind in ("CONFIRMED", "INFERENCE", "HYPOTHESIS", "TARGET", "UNKNOWN"):
                label_only_legend = re.sub(
                    rf"(?m)^- \*\*{claim_kind}\*\*:.*$",
                    f"- **{claim_kind}**:",
                    label_only_legend,
                )
            quick.write_text(label_only_legend, encoding="utf-8")
            undefined_legend = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
            )
            self.assertNotEqual(undefined_legend.returncode, 0)
            self.assertIn("definition", f"{undefined_legend.stdout}\n{undefined_legend.stderr}".lower())

            quick.write_text(valid_text, encoding="utf-8")

            fabricated = valid_text.replace(command_digest, "0" * 64)
            quick.write_text(fabricated, encoding="utf-8")
            rejected = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("stdout digest", f"{rejected.stdout}\n{rejected.stderr}".lower())

            quick.write_text(
                fabricated.replace(command, "python3 -m quick_cli"), encoding="utf-8"
            )
            unsafe = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK"
            )
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertIn("literal rg", f"{unsafe.stdout}\n{unsafe.stderr}".lower())

    def test_quick_contract_requires_evidence_boundary_and_continuation_fields(self) -> None:
        fixture = FIXTURES_ROOT / "quick_cli"
        required = (
            "## Scope and Depth Rationale",
            "## Evidence Snapshot",
            "## Exclusions",
            "## Evidence Legend",
            "## Exact Validation Result",
            "## Next Safe Action",
            "## Source References",
        )
        with tempfile.TemporaryDirectory(prefix="atlas quick contract ") as temp_dir:
            output = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "QUICK"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            quick = output / "PROJECT_ATLAS.md"
            text = quick.read_text(encoding="utf-8")
            for marker in required:
                self.assertIn(marker, text)
            quick.write_text(text.replace(required[0], "## Scope"), encoding="utf-8")
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "QUICK", "--draft"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(required[0], f"{result.stdout}\n{result.stderr}")

    def test_validate_accepts_unicode_project_source_references(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas unicode source ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            source = project / "módulo" / "сервис.py"
            source.parent.mkdir(parents=True)
            source.write_text("print('ok')\n", encoding="utf-8")
            output = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--output", output, "--mode", "FORENSIC"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (output / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "F-1\tCONFIRMED\tUnicode source\tFILE\tmódulo/сервис.py:L1\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate", "--atlas", output, "--project", project, "--mode", "FORENSIC", "--draft"
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_validate_rejects_unresolved_handoff_commands(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas unresolved handoff ") as temp_dir:
            output = Path(temp_dir) / "standard atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            handoff = output / "LIVE_HANDOFF.md"
            handoff.write_text(
                handoff.read_text(encoding="utf-8")
                + """
```sh
python3 <map-project-skill-dir>/scripts/atlas.py validate --atlas project-atlas --mode STANDARD
```
""",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("substitution", f"{result.stdout}\n{result.stderr}".lower())

    def test_validate_rejects_host_specific_temporary_paths(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas local handoff ") as temp_dir:
            output = Path(temp_dir) / "standard atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            handoff = output / "LIVE_HANDOFF.md"
            handoff.write_text(
                handoff.read_text(encoding="utf-8")
                + "\nObserved helper candidate: /private/tmp/project-atlas-agent-home.\n",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD", "--draft"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("local absolute path", f"{result.stdout}\n{result.stderr}".lower())

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required for command replay")
    def test_validate_replays_safe_rg_command_evidence(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas command replay ") as temp_dir:
            output = Path(temp_dir) / "forensic atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            command = [
                "rg",
                "--no-config",
                "--files",
                "--sort",
                "path",
                "legacy_system",
            ]
            observed = run_command(command, cwd=fixture)
            self.assertEqual(observed.returncode, 0, observed.stderr)
            digest = hashlib.sha256(observed.stdout.encode("utf-8")).hexdigest()
            traceability = output / "TRACEABILITY.tsv"
            traceability.write_text(
                traceability.read_text(encoding="utf-8")
                + "FACT-CMD\tCONFIRMED\tSource members were enumerated.\tCOMMAND\t"
                + "rg --no-config --files --sort path legacy_system\t2026-07-21\tCURRENT\t-\t"
                + f"cwd=.; exit=0; stdout_sha256={digest}\n",
                encoding="utf-8",
            )

            valid = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
                "--draft",
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)

            traceability.write_text(
                traceability.read_text(encoding="utf-8").replace(digest, "0" * 64),
                encoding="utf-8",
            )
            invalid = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
                "--draft",
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("digest", f"{invalid.stdout}\n{invalid.stderr}".lower())

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required for command replay")
    def test_validate_does_not_replay_stale_or_superseded_command_evidence(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        for status in ("STALE", "SUPERSEDED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory(
                prefix=f"atlas inactive replay {status.lower()} "
            ) as temp_dir:
                output = Path(temp_dir) / "forensic atlas"
                initialized = run_atlas(
                    "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                traceability = output / "TRACEABILITY.tsv"
                traceability.write_text(
                    traceability.read_text(encoding="utf-8")
                    + "FACT-OLD\tCONFIRMED\tHistorical source enumeration.\tCOMMAND\t"
                    + "rg --no-config --files --sort path legacy_system\t2026-07-21\t"
                    + status
                    + "\t-\tcwd=.; exit=0; stdout_sha256="
                    + "0" * 64
                    + "\n",
                    encoding="utf-8",
                )

                result = run_atlas(
                    "validate",
                    "--atlas",
                    output,
                    "--project",
                    fixture,
                    "--mode",
                    "FORENSIC",
                    "--draft",
                    "--replay-command-evidence",
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_validate_rejects_unsafe_rg_replay_flags(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas unsafe replay ") as temp_dir:
            output = Path(temp_dir) / "forensic atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            traceability = output / "TRACEABILITY.tsv"
            traceability.write_text(
                traceability.read_text(encoding="utf-8")
                + "FACT-CMD\tCONFIRMED\tHidden members were enumerated.\tCOMMAND\t"
                + "rg --no-config --hidden --files .\t2026-07-21\tCURRENT\t-\t"
                + f"cwd=.; exit=0; stdout_sha256={'0' * 64}\n",
                encoding="utf-8",
            )
            invalid = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--replay-command-evidence",
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("unsafe", f"{invalid.stdout}\n{invalid.stderr}".lower())

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required for command replay")
    def test_validate_rejects_unsorted_directory_replay(self) -> None:
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas unsorted replay ") as temp_dir:
            output = Path(temp_dir) / "forensic atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "FORENSIC"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            command = ["rg", "--no-config", "--files", "legacy_system"]
            observed = run_command(command, cwd=fixture)
            self.assertEqual(observed.returncode, 0, observed.stderr)
            digest = hashlib.sha256(observed.stdout.encode("utf-8")).hexdigest()
            traceability = output / "TRACEABILITY.tsv"
            traceability.write_text(
                traceability.read_text(encoding="utf-8")
                + "FACT-CMD\tCONFIRMED\tSource members were enumerated.\tCOMMAND\t"
                + "rg --no-config --files legacy_system\t2026-07-21\tCURRENT\t-\t"
                + f"cwd=.; exit=0; stdout_sha256={digest}\n",
                encoding="utf-8",
            )

            invalid = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "FORENSIC",
                "--draft",
                "--replay-command-evidence",
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("--sort path", f"{invalid.stdout}\n{invalid.stderr}".lower())

    def test_validate_rejects_project_source_refs_with_invalid_lines(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas invalid source line ") as temp_dir:
            output = Path(temp_dir) / "standard atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "STANDARD"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            architecture = output / "CURRENT_ARCHITECTURE.md"
            architecture.write_text(
                architecture.read_text(encoding="utf-8")
                + "\nCONFIRMED: API boundary at `service/api.py:L999`.\n",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate", "--atlas", output, "--project", fixture, "--mode", "STANDARD"
            )
            self.assertNotEqual(result.returncode, 0)
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
            self.assertIn("service/api.py", diagnostic)
            self.assertIn("line", diagnostic)

    def test_validate_does_not_treat_code_symbols_or_ignore_globs_as_source_refs(self) -> None:
        fixture = FIXTURES_ROOT / "quick_cli"
        with tempfile.TemporaryDirectory(prefix="atlas source vocabulary ") as temp_dir:
            output = Path(temp_dir) / "quick atlas"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", output, "--mode", "QUICK"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            atlas = output / "PROJECT_ATLAS.md"
            atlas.write_text(
                atlas.read_text(encoding="utf-8")
                + "\nImplementation vocabulary: `Path.replace`, `.new`, and `*.sqlite3`.\n",
                encoding="utf-8",
            )

            result = run_atlas(
                "validate",
                "--atlas",
                output,
                "--project",
                fixture,
                "--mode",
                "QUICK",
                "--draft",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_repeated_init_preserves_user_additions_byte_for_byte(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        fixture = FIXTURES_ROOT / "standard_service"
        with tempfile.TemporaryDirectory(prefix="atlas idempotent rerun ") as temp_dir:
            output = Path(temp_dir) / "standard atlas"
            command = ("init", "--project", fixture, "--output", output, "--mode", "STANDARD")
            first = run_atlas(*command)
            self.assertEqual(first.returncode, 0, first.stderr)
            user_owned = output / "CURRENT_ARCHITECTURE.md"
            assert_file(self, user_owned)
            user_owned.write_text(
                user_owned.read_text(encoding="utf-8") + "\nUSER-ADDITION-MUST-SURVIVE\n",
                encoding="utf-8",
            )
            before = tree_digest(output)

            second = run_atlas(*command)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(tree_digest(output), before)
            self.assertIn("USER-ADDITION-MUST-SURVIVE", user_owned.read_text(encoding="utf-8"))

    def test_snapshot_is_deterministic_relative_and_content_addressed(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        fixture = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas deterministic snapshot ") as temp_dir:
            root = Path(temp_dir)
            atlas = root / "atlas with spaces"
            initialized = run_atlas(
                "init", "--project", fixture, "--output", atlas, "--mode", "FORENSIC"
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "F-1\tCONFIRMED\tgateway\tFILE\tlegacy_system/gateway.py:L1\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            first_path = root / "snapshot one.json"
            second_path = root / "snapshot two.json"
            first = run_atlas(
                "snapshot", "--atlas", atlas, "--project", fixture, "--output", first_path
            )
            second = run_atlas(
                "snapshot", "--atlas", atlas, "--project", fixture, "--output", second_path
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

            payload = load_json(self, first_path)
            self.assertRegex(str(payload.get("sha256", "")), r"^[0-9a-f]{64}$")
            self.assertIsInstance(payload.get("files"), list)
            self.assertTrue(payload["files"])
            for entry in payload["files"]:
                self.assertIsInstance(entry, dict)
                relative = Path(str(entry.get("path", "")))
                self.assertFalse(relative.is_absolute())
                self.assertNotIn("..", relative.parts)
                self.assertRegex(str(entry.get("sha256", "")), r"^[0-9a-f]{64}$")
            serialized = first_path.read_text(encoding="utf-8")
            self.assertNotIn(str(atlas.resolve()), serialized)
            self.assertNotRegex(serialized, r"/(?:Users|home)/")

    def test_all_cli_workflows_support_source_and_output_paths_with_spaces(self) -> None:
        assert_file(self, ATLAS_SCRIPT)
        with tempfile.TemporaryDirectory(prefix="atlas path contract ") as temp_dir:
            root = Path(temp_dir)
            fixture = root / "source project with spaces"
            shutil.copytree(FIXTURES_ROOT / "quick_cli", fixture)
            inventory = root / "inventory output with spaces.json"
            atlas = root / "atlas output with spaces"
            snapshot = root / "snapshot output with spaces.json"

            selected = run_atlas("select-mode", "--project", fixture)
            self.assertEqual(parse_mode_output(self, selected), "QUICK")
            inventoried = run_atlas("inventory", "--project", fixture, "--output", inventory)
            self.assertEqual(inventoried.returncode, 0, inventoried.stderr)
            initialized = run_atlas("init", "--project", fixture, "--output", atlas)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            validated = run_atlas(
                "validate", "--atlas", atlas, "--project", fixture, "--mode", "QUICK", "--draft"
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)
            snapshotted = run_atlas("snapshot", "--atlas", atlas, "--output", snapshot)
            self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
            self.assertTrue(inventory.is_file())
            self.assertTrue(snapshot.is_file())


class AtlasTemplateContractTests(unittest.TestCase):
    def test_all_modes_template_a_structured_depth_decision_record(self) -> None:
        templates_root = CORE_SKILL / "assets" / "templates"
        labels = (
            "Selected by:",
            "Conflicting automatic signals:",
            "Intentionally omitted coverage:",
            "Escalation condition:",
        )
        for mode, filename in (
            ("quick", "PROJECT_ATLAS.md"),
            ("standard", "ATLAS_INDEX.md"),
            ("forensic", "ATLAS_INDEX.md"),
        ):
            with self.subTest(mode=mode):
                text = (templates_root / mode / filename).read_text(encoding="utf-8")
                for label in labels:
                    self.assertEqual(text.count(label), 1)

    def test_mode_template_directories_match_output_oracles(self) -> None:
        templates_root = CORE_SKILL / "assets" / "templates"
        assert_directory(self, templates_root)
        for fixture_name in FIXTURE_NAMES:
            oracle = load_oracle(self, fixture_name)
            mode = str(oracle["expected_mode"])
            template_dir = templates_root / mode.lower()
            with self.subTest(mode=mode):
                assert_directory(self, template_dir)
                for filename in oracle["required_artifacts"]:
                    template = template_dir / str(filename)
                    assert_file(self, template)
                    text = template.read_text(encoding="utf-8")
                    for marker in oracle["required_markers"].get(str(filename), []):
                        self.assertIn(str(marker), text)

                if mode == "QUICK":
                    template_files = sorted(
                        path.relative_to(template_dir).as_posix()
                        for path in template_dir.rglob("*")
                        if path.is_file()
                    )
                    self.assertEqual(template_files, ["PROJECT_ATLAS.md"])
                    for claim_kind in (
                        "CONFIRMED",
                        "INFERENCE",
                        "HYPOTHESIS",
                        "TARGET",
                        "UNKNOWN",
                    ):
                        self.assertIn(f"**{claim_kind}**", text)


if __name__ == "__main__":
    unittest.main()
