from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import REPO_ROOT, run_atlas


FIXTURE = REPO_ROOT / "tests" / "fixtures" / "standard_service"
LINEAGE_ROOT = REPO_ROOT / "docs" / "case-study-artifacts" / "standard-service"
PATCH_PATH = LINEAGE_ROOT / "ATLAS-001.patch"
CASE_STUDIES = (
    REPO_ROOT / "docs" / "case-study.md",
    REPO_ROOT / "docs" / "case-study.ru.md",
)


def run_probe(project: Path, source: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(project)
    with tempfile.TemporaryDirectory(prefix="atlas case-study probe ") as temp_dir:
        for variable in ("TMPDIR", "TMP", "TEMP"):
            environment[variable] = temp_dir
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=project,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )


def apply_documented_patch(project: Path) -> None:
    for check_only in (True, False):
        command = ["git", "apply", "--whitespace=error-all"]
        if check_only:
            command.append("--check")
        command.append(str(PATCH_PATH))
        result = subprocess.run(
            command,
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                "published case-study patch did not apply cleanly\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )


def read_published_hunk() -> str:
    patch_lines = PATCH_PATH.read_text(encoding="utf-8").splitlines()
    hunk_index = next(
        index for index, line in enumerate(patch_lines) if line.startswith("@@ ")
    )
    return "\n".join(patch_lines[hunk_index + 1 :]).rstrip() + "\n"


class DocumentationCaseStudyTests(unittest.TestCase):
    def assert_completion_valid(self, atlas: Path, project: Path) -> dict[str, object]:
        result = run_atlas(
            "validate",
            "--atlas",
            atlas,
            "--project",
            project,
            "--mode",
            "QUICK",
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Atlas completion validation failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"Atlas validator did not return JSON: {error}\n{result.stdout}")
        if not isinstance(payload, dict):
            self.fail(f"Atlas validator returned non-object JSON: {payload!r}")
        self.assertEqual(payload.get("status"), "valid")
        self.assertEqual(payload.get("mode"), "QUICK")
        self.assertEqual(payload.get("validation"), "completion")
        return payload

    def test_public_case_study_reproduces_before_and_after_states(self) -> None:
        before_probe = run_probe(
            FIXTURE,
            """
import sqlite3
import tempfile
from pathlib import Path
from service.api import accept_parcel
from service.worker import process_delivery

database = Path(tempfile.mkdtemp()) / "before.sqlite"
try:
    accept_parcel(database, "   ")
except ValueError as error:
    print(f"api blank: rejected ({error})")
else:
    raise AssertionError("API accepted a blank parcel_id")

process_delivery(database, "   ", lambda: "delivered")
row = sqlite3.connect(database).execute(
    "SELECT parcel_id, status, writer FROM parcel_state"
).fetchone()
print(f"worker blank: {row}")
""",
        )
        self.assertEqual(before_probe.returncode, 0, before_probe.stderr)
        self.assertEqual(
            before_probe.stdout.splitlines(),
            [
                "api blank: rejected (parcel_id is required)",
                "worker blank: ('   ', 'delivered', 'worker')",
            ],
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate = Path(temporary_directory) / "standard_service"
            shutil.copytree(FIXTURE, candidate)
            before_files = {
                path.relative_to(candidate).as_posix(): path.read_bytes()
                for path in candidate.rglob("*")
                if path.is_file()
            }
            apply_documented_patch(candidate)
            after_files = {
                path.relative_to(candidate).as_posix(): path.read_bytes()
                for path in candidate.rglob("*")
                if path.is_file()
            }
            changed_files = sorted(
                path
                for path in before_files.keys() | after_files.keys()
                if before_files.get(path) != after_files.get(path)
            )
            self.assertEqual(changed_files, ["service/state.py"])
            expected_state = before_files["service/state.py"].replace(
                b'    """Persistent state writer shared by the API and worker runtimes."""\n'
                b"    database.parent.mkdir(parents=True, exist_ok=True)\n",
                b'    """Persistent state writer shared by the API and worker runtimes."""\n'
                b"    if not parcel_id.strip():\n"
                b'        raise ValueError("parcel_id is required")\n'
                b"    database.parent.mkdir(parents=True, exist_ok=True)\n",
            )
            self.assertNotEqual(expected_state, before_files["service/state.py"])
            self.assertEqual(after_files["service/state.py"], expected_state)

            after_probe = run_probe(
                candidate,
                """
import sqlite3
import tempfile
from pathlib import Path
from service.api import accept_parcel
from service.worker import process_delivery

database = Path(tempfile.mkdtemp()) / "after.sqlite"
blank_calls = (
    ("api", lambda: accept_parcel(database, "   ")),
    ("worker", lambda: process_delivery(database, "   ", lambda: "delivered")),
)
for label, call in blank_calls:
    try:
        call()
    except ValueError as error:
        print(f"{label} blank: rejected ({error})")
    else:
        raise AssertionError(f"{label} accepted a blank parcel_id")

accept_parcel(database, "parcel-api")
process_delivery(database, "parcel-7", lambda: "delivered")
rows = sqlite3.connect(database).execute(
    "SELECT parcel_id, status, writer FROM parcel_state ORDER BY parcel_id"
).fetchall()
print(f"valid: {rows}")
""",
            )
            self.assertEqual(after_probe.returncode, 0, after_probe.stderr)
            self.assertEqual(
                after_probe.stdout.splitlines(),
                [
                    "api blank: rejected (parcel_id is required)",
                    "worker blank: rejected (parcel_id is required)",
                    "valid: [('parcel-7', 'delivered', 'worker'), "
                    "('parcel-api', 'accepted', 'api')]",
                ],
            )

    def test_frozen_atlas_lineage(self) -> None:
        before_atlas = LINEAGE_ROOT / "before"
        context_packet = LINEAGE_ROOT / "ATLAS-001-context-packet.md"
        after_atlas = LINEAGE_ROOT / "after"

        before_payload = self.assert_completion_valid(before_atlas, FIXTURE)
        self.assertEqual(before_payload.get("artifacts"), 1)

        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate = Path(temporary_directory) / "standard_service"
            shutil.copytree(FIXTURE, candidate)
            apply_documented_patch(candidate)
            after_payload = self.assert_completion_valid(after_atlas, candidate)
        self.assertEqual(after_payload.get("artifacts"), 1)

        before = (before_atlas / "PROJECT_ATLAS.md").read_text(encoding="utf-8")
        packet = context_packet.read_text(encoding="utf-8")
        patch = PATCH_PATH.read_text(encoding="utf-8")
        after = (after_atlas / "PROJECT_ATLAS.md").read_text(encoding="utf-8")

        lineage_ids = (
            "ATLAS-001",
            "CLAIM-API-001",
            "CLAIM-WORKER-002",
            "UNKNOWN:PROVIDER-ORDERING",
        )
        for artifact_name, content in (
            ("before", before),
            ("context packet", packet),
            ("after", after),
        ):
            with self.subTest(artifact=artifact_name):
                for lineage_id in lineage_ids:
                    self.assertIn(lineage_id, content)

        for marker in (
            "--- a/service/state.py",
            "+++ b/service/state.py",
            "if not parcel_id.strip():",
            'raise ValueError("parcel_id is required")',
        ):
            self.assertIn(marker, patch)
        packet_markers = (
            "Source map: `before/PROJECT_ATLAS.md`",
            "## Task",
            "Outcome: Make every status write reject a blank parcel identifier.",
            "## Acceptance",
            "## CURRENT Claims",
            "`service/api.py:L8-L12`",
            "`service/worker.py:L27-L41`",
            "`service/state.py:L8-L21`",
            "`service/authority.py:L4-L10`",
            "## Owning Layer",
            "The invariant belongs there so the API and worker cannot diverge.",
            "## Authority Boundary",
            "## Related Unknown",
            "## Scope",
            "- Add the whitespace-only identifier guard to the shared writer.",
            "- Verify rejection through both API and worker paths.",
            "- Verify valid API and worker writes remain persisted.",
            "## Non-goals",
            "- Provider retry behavior.",
            "- Provider event ordering.",
            "- Administrator authority.",
            "- Status model redesign.",
            "- Deployment or production readiness.",
            "## Acceptance Criteria",
            "1. A whitespace-only `parcel_id` is rejected through the API path.",
            "2. A whitespace-only `parcel_id` is rejected through the worker path.",
            "3. A valid API parcel remains persisted with writer `api`.",
            "4. A valid worker parcel remains persisted with writer `worker`.",
            "5. `UNKNOWN:PROVIDER-ORDERING` remains visible and unresolved.",
            "6. The refreshed QUICK map passes completion validation against the changed snapshot.",
            "## Required Checks",
            "## Freshness",
            "## Excluded",
        )
        for marker in packet_markers:
            self.assertIn(marker, packet)

        self.assertRegex(
            before,
            r"(?m)^\| ATLAS-001 \| TARGET \| .* \| READY \|$",
        )
        self.assertRegex(
            after,
            r"(?m)^\| ATLAS-001 \| TARGET \| .* \| SUPERSEDED \|$",
        )
        self.assertIn("## Task Receipts", after)
        self.assertRegex(after, r"(?m)^\| ATLAS-001 \| VERIFIED \|")
        self.assertRegex(
            after,
            r"(?m)^\| ATLAS-002 \| TARGET \| .*UNKNOWN:PROVIDER-ORDERING.* \| BLOCKED \|$",
        )

    def test_case_studies_name_the_complete_atlas_loop(self) -> None:
        published_hunk = read_published_hunk()
        required_markers = (
            "CURRENT",
            "CONFIRMED",
            "TARGET",
            "UNKNOWN",
            "ATLAS-001",
            "SUPERSEDED",
            "ATLAS-002",
            "BLOCKED",
            "PYTHONPATH=tests/fixtures/standard_service",
            "./case-study-artifacts/standard-service/before/PROJECT_ATLAS.md",
            "./case-study-artifacts/standard-service/ATLAS-001-context-packet.md",
            "./case-study-artifacts/standard-service/ATLAS-001.patch",
            "./case-study-artifacts/standard-service/after/PROJECT_ATLAS.md",
            "tests/test_documentation_case_study.py",
        )
        for path in CASE_STUDIES:
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                for marker in required_markers:
                    self.assertIn(marker, content)
                self.assertNotIn("\ncd tests/fixtures/standard_service\n", content)
                fence_start = content.index("```diff\n") + len("```diff\n")
                fence_end = content.index("\n```", fence_start)
                displayed_hunk = content[fence_start:fence_end].rstrip() + "\n"
                self.assertEqual(displayed_hunk, published_hunk)


if __name__ == "__main__":
    unittest.main()
