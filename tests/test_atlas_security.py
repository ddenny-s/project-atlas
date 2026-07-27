from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.support import ATLAS_SCRIPT, FIXTURES_ROOT, load_json, run_atlas, run_command


TRACE_HEADER = (
    "fact_id\tclaim_kind\tclaim\tsource_type\tsource_ref\tobserved_at\tstatus\t"
    "atlas_refs\tnotes\n"
)

QUESTION_HEADER = (
    "| Question ID | Batch ID | Topic | Question | Option A | Option B | Option C | "
    "Option D | Selected | Free-form note | Answer state | Map effect | Provenance | "
    "Answered at |"
)
QUESTION_SEPARATOR = "| " + " | ".join(["---"] * 14) + " |"
BATCH_HEADER = (
    "| Batch ID | Sequence | Question IDs | Remaining material gaps | Decision | "
    "Decision provenance | Status |"
)
BATCH_SEPARATOR = "| " + " | ".join(["---"] * 7) + " |"
ECONOMICS_HEADER = (
    "| Run ID | Block ID | Entry | Block | Unit | Min | Typical | Max | Basis | "
    "Model tier and effort | Input | Output | Reasoning | Total | Telemetry | "
    "Variance vs typical | Recorded at | Status |"
)
ECONOMICS_SEPARATOR = "| " + " | ".join(["---"] * 18) + " |"
FUTURE_TASKS_HEADER = (
    "| Task ID | Claim kind | Priority | Outcome | Basis | Affected areas | Scope | "
    "Non-goals | Acceptance criteria | Dependencies and unknowns | Risks | "
    "Verification | Status |"
)
FUTURE_TASKS_SEPARATOR = "| " + " | ".join(["---"] * 13) + " |"


def alignment_section(
    section: str,
    phase: str,
    question_count: int,
    *,
    stop_decision: str = "STOP_STABLE",
) -> str:
    question_rows: list[str] = []
    batches: list[list[str]] = []
    for index in range(question_count):
        batch_index = index // 3 + 1
        while len(batches) < batch_index:
            batches.append([])
        question_id = f"Q-{phase}-{index + 1}"
        batch_id = f"B-{phase}-{batch_index}"
        batches[batch_index - 1].append(question_id)
        question_rows.append(
            f"| {question_id} | {batch_id} | Direction | "
            f"Choose direction {index + 1} for the target map | "
            "Keep current contour | Narrow the contour | Replace the contour | "
            "Другое — напишу сам | A | - | ANSWERED | "
            f"TARGET: direction {index + 1} | USER_INPUT:{question_id} | "
            "2026-07-22T10:00:00Z |"
        )
    batch_rows: list[str] = []
    for index, question_ids in enumerate(batches, start=1):
        final = index == len(batches)
        decision = stop_decision if final else "CONTINUE"
        gaps = "None" if decision == "STOP_STABLE" else f"UNKNOWN:gap-{phase.lower()}-{index}"
        if decision == "STOP_USER":
            provenance = f"USER_INPUT:stop-{phase.lower()}-{index}"
        elif decision == "STOP_UNAVAILABLE":
            provenance = f"UNAVAILABLE:stop-{phase.lower()}-{index}"
        elif decision == "STOP_STABLE":
            provenance = "PROTOCOL:SEMANTIC_STOP"
        else:
            provenance = f"USER_INPUT:continue-{phase.lower()}-{index}"
        batch_rows.append(
            f"| B-{phase}-{index} | {index} | {';'.join(question_ids)} | {gaps} | "
            f"{decision} | {provenance} | COMPLETE |"
        )
    return (
        f"## {section}\n\n"
        f"{QUESTION_HEADER}\n{QUESTION_SEPARATOR}\n"
        + "\n".join(question_rows)
        + "\n\n"
        f"{BATCH_HEADER}\n{BATCH_SEPARATOR}\n"
        + "\n".join(batch_rows)
        + "\n"
    )


def run_economics_section(*, measured_total: int = 1200, variance: int = 200) -> str:
    return (
        "## Run Economics\n\n"
        f"{ECONOMICS_HEADER}\n{ECONOMICS_SEPARATOR}\n"
        "| RUN-1 | BLOCK-1 | PRE | Inventory and scope | MODEL_TOKENS | 800 | 1000 | "
        "1600 | Safe inventory size and declared depth | standard effort | UNMEASURED | "
        "UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | "
        "2026-07-22T09:00:00Z | MODELLED |\n"
        "| RUN-1 | BLOCK-1 | POST | Inventory and scope | MODEL_TOKENS | UNMEASURED | "
        "UNMEASURED | UNMEASURED | PRE:BLOCK-1 | standard effort | 500 | 400 | 300 | "
        f"{measured_total} | HOST:run-1-block-1 | {variance:+d} | "
        "2026-07-22T11:00:00Z | MEASURED |\n"
    )


def future_tasks_section(
    *,
    answer_ref: str = "USER_INPUT:Q-START-1",
    status: str = "READY",
) -> str:
    basis = "; ".join(
        part
        for part in (
            answer_ref,
            "src/main.py:L1",
            "MAP:MIGRATION_PLAN.md#sequence",
        )
        if part
    )
    dependencies = (
        "UNKNOWN:task-1-blocker Owner decision is still required"
        if status == "BLOCKED"
        else "None"
    )
    return (
        "## Future Tasks\n\n"
        f"{FUTURE_TASKS_HEADER}\n{FUTURE_TASKS_SEPARATOR}\n"
        "| TASK-1 | TARGET | P1 | Preserve the selected target direction | "
        f"{basis} | "
        "src | Bounded target contour | Production rollout | "
        f"The target contour is mapped and validated | {dependencies} | "
        "Drift from current runtime | Run the focused contract tests | "
        f"{status} |\n"
    )


class AtlasSecurityRegressionTests(unittest.TestCase):
    def test_alignment_question_count_is_semantically_unbounded(self) -> None:
        atlas_module = self.load_atlas_subject()
        for count in (3, 11):
            with self.subTest(count=count):
                _questions, _batches, errors = atlas_module.validate_alignment_section(
                    "PROJECT_ATLAS.md",
                    alignment_section("Start Alignment", "START", count),
                    "Start Alignment",
                    draft=False,
                )
                self.assertEqual(errors, [])

    def test_alignment_rejects_fabricated_or_incompatible_answers(self) -> None:
        atlas_module = self.load_atlas_subject()
        valid = alignment_section("Start Alignment", "START", 3)
        mutations = (
            (
                "Другое — напишу сам | A | - | ANSWERED",
                "Keep current contour | A | - | ANSWERED",
                "exact Option D",
            ),
            (
                "| A | - | ANSWERED | TARGET: direction 1 |",
                "| D | - | ANSWERED | TARGET: direction 1 |",
                "Free-form note",
            ),
            (
                "| A | - | ANSWERED | TARGET: direction 1 |",
                "| A | - | SKIPPED | TARGET: fabricated |",
                "SKIPPED",
            ),
            (
                "| A | - | ANSWERED | TARGET: direction 1 |",
                "| A | - | ANSWERED | - |",
                "Map effect",
            ),
            (
                "USER_INPUT:Q-START-1 | 2026-07-22T10:00:00Z",
                "USER_INPUT:person@example.com | 2099-07-22T10:00:00Z",
                "Provenance",
            ),
            (
                "USER_INPUT:Q-START-1 | 2026-07-22T10:00:00Z",
                "USER_INPUT:Q-START-999 | 2026-07-22T10:00:00Z",
                "stable Question ID",
            ),
        )
        for before, after, diagnostic in mutations:
            with self.subTest(diagnostic=diagnostic):
                _questions, _batches, errors = atlas_module.validate_alignment_section(
                    "PROJECT_ATLAS.md",
                    valid.replace(before, after, 1),
                    "Start Alignment",
                    draft=False,
                )
                self.assertTrue(errors)
                self.assertTrue(
                    any(diagnostic.casefold() in error.casefold() for error in errors),
                    errors,
                )

    def test_alignment_enforces_batch_chronology_and_stop_semantics(self) -> None:
        atlas_module = self.load_atlas_subject()
        for stop_decision in ("STOP_USER", "STOP_UNAVAILABLE"):
            with self.subTest(valid_stop=stop_decision):
                _questions, _batches, observed = atlas_module.validate_alignment_section(
                    "LIVE_HANDOFF.md",
                    alignment_section(
                        "Finish Alignment",
                        "FINISH",
                        4,
                        stop_decision=stop_decision,
                    ),
                    "Finish Alignment",
                    draft=False,
                )
                self.assertEqual(observed, [])
        valid = alignment_section("Finish Alignment", "FINISH", 4)
        mutations = (
            ("| B-FINISH-2 | 2 |", "| B-FINISH-2 | 4 |", "contiguous"),
            (
                "| STOP_STABLE | PROTOCOL:SEMANTIC_STOP |",
                "| CONTINUE | PROTOCOL:SEMANTIC_STOP |",
                "STOP",
            ),
            ("| None | STOP_STABLE |", "| UNKNOWN:still-open | STOP_STABLE |", "None"),
            (
                "| UNKNOWN:gap-finish-1 | CONTINUE |",
                "| None | STOP_USER |",
                "named",
            ),
        )
        for before, after, diagnostic in mutations:
            with self.subTest(diagnostic=diagnostic):
                _questions, _batches, errors = atlas_module.validate_alignment_section(
                    "LIVE_HANDOFF.md",
                    valid.replace(before, after, 1),
                    "Finish Alignment",
                    draft=False,
                )
                self.assertTrue(errors)
                self.assertTrue(
                    any(diagnostic.casefold() in error.casefold() for error in errors),
                    errors,
                )

    def test_unknown_references_require_standalone_bounded_tokens(self) -> None:
        atlas_module = self.load_atlas_subject()
        canonical_alignment = alignment_section(
            "Finish Alignment",
            "FINISH",
            1,
            stop_decision="STOP_UNAVAILABLE",
        )
        canonical_token = "UNKNOWN:gap-finish-1"
        valid_tokens = (
            canonical_token,
            f"({canonical_token}),",
            "UNKNOWN:" + ("a" * 128),
        )
        invalid_tokens = (
            "NOTUNKNOWN:gap-finish-1",
            "XUNKNOWN:gap-finish-1",
            "éUNKNOWN:gap-finish-1",
            "中UNKNOWN:gap-finish-1",
            "９UNKNOWN:gap-finish-1",
            "UNKNOWN:gap-finish-1é",
            "UNKNOWN:gap-finish-1中",
            "UNKNOWN:gap-finish-1９",
            "UNKNOWN:gap-finish-1\u0301",
            "\u200dUNKNOWN:gap-finish-1",
            "UNKNOWN:gap-finish-1\u200d",
            "UNKNOWN:" + ("a" * 129),
            "UNKNOWN:" + ("a" * 128) + ".suffix",
        )

        for token in valid_tokens:
            with self.subTest(surface="alignment", valid_token=token):
                _questions, _batches, errors = atlas_module.validate_alignment_section(
                    "LIVE_HANDOFF.md",
                    canonical_alignment.replace(canonical_token, token, 1),
                    "Finish Alignment",
                    draft=False,
                )
                self.assertEqual(errors, [])
        for token in invalid_tokens:
            with self.subTest(surface="alignment", invalid_token=token):
                _questions, _batches, errors = atlas_module.validate_alignment_section(
                    "LIVE_HANDOFF.md",
                    canonical_alignment.replace(canonical_token, token, 1),
                    "Finish Alignment",
                    draft=False,
                )
                self.assertTrue(
                    any("named UNKNOWN" in error for error in errors),
                    errors,
                )

        continue_alignment = alignment_section(
            "Finish Alignment",
            "FINISH",
            4,
            stop_decision="STOP_UNAVAILABLE",
        )
        for token in valid_tokens:
            with self.subTest(surface="alignment CONTINUE", valid_token=token):
                _questions, _batches, errors = atlas_module.validate_alignment_section(
                    "LIVE_HANDOFF.md",
                    continue_alignment.replace(canonical_token, token, 1),
                    "Finish Alignment",
                    draft=False,
                )
                self.assertEqual(errors, [])
        for token in (*invalid_tokens, "Owner decision still needs evidence"):
            with self.subTest(surface="alignment CONTINUE", invalid_token=token):
                _questions, _batches, errors = atlas_module.validate_alignment_section(
                    "LIVE_HANDOFF.md",
                    continue_alignment.replace(canonical_token, token, 1),
                    "Finish Alignment",
                    draft=False,
                )
                self.assertTrue(
                    any(
                        "CONTINUE requires a named UNKNOWN" in error
                        for error in errors
                    ),
                    errors,
                )

        with tempfile.TemporaryDirectory(prefix="atlas bounded unknown tokens ") as temp_dir:
            project = Path(temp_dir)
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text("pass\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)
            canonical_task = future_tasks_section(answer_ref="", status="BLOCKED")
            canonical_task_token = "UNKNOWN:task-1-blocker"

            def validate_task(token: str) -> list[str]:
                task = canonical_task.replace(canonical_task_token, token, 1)
                _rows, errors = atlas_module.validate_future_tasks_section(
                    "MIGRATION_PLAN.md",
                    task,
                    inventory,
                    active_answers=set(),
                    draft=False,
                    mode="STANDARD",
                    artifact_texts={
                        "MIGRATION_PLAN.md": (
                            "## Sequence\n\n"
                            "The visible migration sequence is grounded in current evidence.\n\n"
                            + task
                        )
                    },
                )
                return errors

            for token in valid_tokens:
                with self.subTest(surface="future task", valid_token=token):
                    self.assertEqual(validate_task(token), [])
            for token in invalid_tokens:
                with self.subTest(surface="future task", invalid_token=token):
                    errors = validate_task(token)
                    self.assertTrue(
                        any("BLOCKED requires a named UNKNOWN" in error for error in errors),
                        errors,
                    )

        mixed_invalid_tokens = (
            "UNKNOWN:",
            "UNKNOWN:" + ("b" * 129),
            "UNKNOWN:otheré",
        )
        decision_sections = (
            (
                "STOP_USER",
                alignment_section(
                    "Finish Alignment",
                    "FINISH",
                    1,
                    stop_decision="STOP_USER",
                ),
            ),
            (
                "STOP_UNAVAILABLE",
                alignment_section(
                    "Finish Alignment",
                    "FINISH",
                    1,
                    stop_decision="STOP_UNAVAILABLE",
                ),
            ),
            (
                "CONTINUE",
                alignment_section(
                    "Finish Alignment",
                    "FINISH",
                    4,
                    stop_decision="STOP_UNAVAILABLE",
                ),
            ),
        )
        for decision, section in decision_sections:
            for malformed in mixed_invalid_tokens:
                with self.subTest(
                    surface="alignment mixed UNKNOWN",
                    decision=decision,
                    malformed=malformed,
                ):
                    mixed = section.replace(
                        canonical_token,
                        canonical_token + "; " + malformed,
                        1,
                    )
                    _questions, _batches, errors = (
                        atlas_module.validate_alignment_section(
                            "LIVE_HANDOFF.md",
                            mixed,
                            "Finish Alignment",
                            draft=False,
                        )
                    )
                    self.assertTrue(
                        any("malformed UNKNOWN" in error for error in errors),
                        errors,
                    )

        for malformed in mixed_invalid_tokens:
            with self.subTest(
                surface="future task mixed UNKNOWN",
                malformed=malformed,
            ):
                errors = validate_task(
                    canonical_task_token + "; " + malformed
                )
                self.assertTrue(
                    any("malformed UNKNOWN" in error for error in errors),
                    errors,
                )

    def test_run_economics_requires_real_pre_post_pair_and_exact_variance(self) -> None:
        atlas_module = self.load_atlas_subject()
        rows, errors = atlas_module.validate_run_economics_section(
            "LIVE_HANDOFF.md",
            run_economics_section(),
            draft=False,
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2)
        unmeasured = run_economics_section().replace(
            "| standard effort | 500 | 400 | 300 | 1200 | HOST:run-1-block-1 | +200 | "
            "2026-07-22T11:00:00Z | MEASURED |",
            "| UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | "
            "UNMEASURED | UNMEASURED | 2026-07-22T11:00:00Z | UNMEASURED |",
        )
        _rows, errors = atlas_module.validate_run_economics_section(
            "LIVE_HANDOFF.md",
            unmeasured,
            draft=False,
        )
        self.assertEqual(errors, [])

        for text, diagnostic in (
            (run_economics_section(variance=201), "Variance"),
            (
                run_economics_section().replace(
                    "2026-07-22T11:00:00Z", "2026-07-22T08:00:00Z"
                ),
                "chronologically",
            ),
            (
                run_economics_section().replace(
                    "| 500 | 400 | 300 | 1200 |",
                    "| 500 | 400 | UNMEASURED | UNMEASURED |",
                ),
                "all UNMEASURED",
            ),
        ):
            with self.subTest(diagnostic=diagnostic):
                _rows, observed = atlas_module.validate_run_economics_section(
                    "LIVE_HANDOFF.md",
                    text,
                    draft=False,
                )
                self.assertTrue(
                    any(diagnostic.casefold() in error.casefold() for error in observed),
                    observed,
                )

    def test_run_economics_rejects_orphan_pre_and_token_quota_conversion(self) -> None:
        atlas_module = self.load_atlas_subject()
        complete = run_economics_section()
        orphan_pre = complete.rsplit("\n", 2)[0] + "\n"
        _rows, errors = atlas_module.validate_run_economics_section(
            "LIVE_HANDOFF.md",
            orphan_pre,
            draft=False,
        )
        self.assertTrue(
            any("pre has no matching post" in error.casefold() for error in errors),
            errors,
        )

        conversion_claims = (
            "1200 tokens are 4% of weekly quota",
            "1200 tokens constitute 4 percent of weekly quota",
            "weekly quota remaining is 4% for 1200 tokens",
            "1200 токенов — это 4% недельного лимита",
        )
        for field in ("Block", "Basis", "Model tier and effort", "Telemetry"):
            for conversion_claim in conversion_claims:
                with self.subTest(field=field, conversion_claim=conversion_claim):
                    if field == "Block":
                        converted = complete.replace(
                            "Inventory and scope",
                            conversion_claim,
                        )
                    elif field == "Basis":
                        converted = complete.replace(
                            "Safe inventory size and declared depth",
                            conversion_claim,
                            1,
                        )
                    elif field == "Model tier and effort":
                        converted = complete.replace(
                            "standard effort",
                            conversion_claim,
                            1,
                        )
                    else:
                        converted = complete.replace(
                            "HOST:run-1-block-1",
                            conversion_claim,
                            1,
                        )
                    _rows, errors = atlas_module.validate_run_economics_section(
                        "LIVE_HANDOFF.md",
                        converted,
                        draft=False,
                    )
                    self.assertTrue(
                        any(
                            f" {field} must not infer or convert a quota percentage "
                            "from model tokens"
                            in error
                            for error in errors
                        ),
                        errors,
                    )

        exact_host_signal = complete.replace(
            "HOST:run-1-block-1",
            "HOST_EXACT_WEEKLY_REMAINING:42%",
        )
        _rows, errors = atlas_module.validate_run_economics_section(
            "LIVE_HANDOFF.md",
            exact_host_signal,
            draft=False,
        )
        self.assertEqual(errors, [])

    def test_run_economics_rejects_token_quota_split_across_narrative_fields(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()
        split_claim = run_economics_section().replace(
            "standard effort",
            "standard effort for 1200 tokens",
            1,
        ).replace(
            "Safe inventory size and declared depth",
            "Host reports 4% weekly quota remaining",
            1,
        )
        _rows, errors = atlas_module.validate_run_economics_section(
            "LIVE_HANDOFF.md",
            split_claim,
            draft=False,
        )
        self.assertTrue(
            any(
                "narrative fields" in error.casefold()
                and "quota" in error.casefold()
                for error in errors
            ),
            errors,
        )
        self.assertEqual(len(errors), 1, errors)

    def test_future_tasks_require_non_dangling_answer_and_safe_basis(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas future task basis ") as temp_dir:
            project = Path(temp_dir)
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text("pass\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)
            active_answers = {"USER_INPUT:Q-START-1"}
            valid_task = future_tasks_section()
            artifact_texts = {
                "MIGRATION_PLAN.md": (
                    "# Migration Plan\n\n## Sequence\n\n"
                    "The visible migration sequence is grounded in current evidence.\n\n"
                    + valid_task
                )
            }

            rows, errors = atlas_module.validate_future_tasks_section(
                "MIGRATION_PLAN.md",
                valid_task,
                inventory,
                active_answers=active_answers,
                draft=False,
                mode="STANDARD",
                artifact_texts=artifact_texts,
            )
            self.assertEqual(errors, [])
            self.assertEqual(len(rows), 1)

            for text, diagnostic in (
                (
                    future_tasks_section(answer_ref="USER_INPUT:missing-answer"),
                    "dangling",
                ),
                (
                    future_tasks_section().replace(
                        "src/main.py:L1; MAP:MIGRATION_PLAN.md#sequence",
                        "missing/main.py:L1",
                    ),
                    "safe project-relative source or map basis",
                ),
                (
                    future_tasks_section().replace(
                        "MAP:MIGRATION_PLAN.md#sequence",
                        "MAP:MIGRATION_PLAN.md#missing-anchor",
                    ),
                    "visible map anchor",
                ),
                (
                    future_tasks_section().replace(
                        "| src | Bounded target contour |",
                        "| - | Bounded target contour |",
                    ),
                    "Affected areas",
                ),
                (
                    future_tasks_section().replace("| READY |", "| BLOCKED |"),
                    "named UNKNOWN",
                ),
            ):
                with self.subTest(diagnostic=diagnostic):
                    _rows, observed = atlas_module.validate_future_tasks_section(
                        "MIGRATION_PLAN.md",
                        text,
                        inventory,
                        active_answers=active_answers,
                        draft=False,
                        mode="STANDARD",
                        artifact_texts={
                            "MIGRATION_PLAN.md": (
                                "# Migration Plan\n\n## Sequence\n\n"
                                "The visible migration sequence is grounded in current evidence.\n\n"
                                + text
                            )
                        },
                    )
                    self.assertTrue(
                        any(diagnostic.casefold() in error.casefold() for error in observed),
                        observed,
                    )

            mandatory_field_mutations = (
                ("Preserve the selected target direction", "-", "Outcome"),
                (
                    "USER_INPUT:Q-START-1; src/main.py:L1; "
                    "MAP:MIGRATION_PLAN.md#sequence",
                    "-",
                    "Basis",
                ),
                (
                    "| src | Bounded target contour |",
                    "| - | Bounded target contour |",
                    "Affected areas",
                ),
                ("Bounded target contour", "-", "Scope"),
                ("Production rollout", "-", "Non-goals"),
                (
                    "The target contour is mapped and validated",
                    "-",
                    "Acceptance criteria",
                ),
                ("None", "-", "Dependencies and unknowns"),
                ("Drift from current runtime", "-", "Risks"),
                ("Run the focused contract tests", "-", "Verification"),
            )
            for before, after, field in mandatory_field_mutations:
                with self.subTest(mandatory_field=field):
                    invalid = valid_task.replace(before, after, 1)
                    _rows, observed = atlas_module.validate_future_tasks_section(
                        "MIGRATION_PLAN.md",
                        invalid,
                        inventory,
                        active_answers=active_answers,
                        draft=False,
                        mode="STANDARD",
                        artifact_texts={
                            "MIGRATION_PLAN.md": (
                                "# Migration Plan\n\n## Sequence\n\n"
                                "The visible migration sequence is grounded in current evidence.\n\n"
                                + invalid
                            )
                        },
                    )
                    self.assertTrue(
                        any(field.casefold() in error.casefold() for error in observed),
                        observed,
                    )

    def test_future_tasks_apply_active_gates_to_ready_and_blocked(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas active future task gates ") as temp_dir:
            project = Path(temp_dir)
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text("pass\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)
            active_answers = {"USER_INPUT:Q-START-1"}
            mandatory_field_mutations = (
                ("Preserve the selected target direction", "-", "Outcome"),
                (
                    "USER_INPUT:Q-START-1; src/main.py:L1; "
                    "MAP:MIGRATION_PLAN.md#sequence",
                    "-",
                    "Basis",
                ),
                ("| src | Bounded target contour |", "| - | Bounded target contour |", "Affected areas"),
                ("Bounded target contour", "-", "Scope"),
                ("Production rollout", "-", "Non-goals"),
                (
                    "The target contour is mapped and validated",
                    "-",
                    "Acceptance criteria",
                ),
                ("Drift from current runtime", "-", "Risks"),
                ("Run the focused contract tests", "-", "Verification"),
            )

            def artifact_texts(task: str) -> dict[str, str]:
                return {
                    "MIGRATION_PLAN.md": (
                        "# Migration Plan\n\n## Sequence\n\n"
                        "The visible migration sequence is grounded in current evidence.\n\n"
                        + task
                    )
                }

            for status in ("READY", "BLOCKED"):
                valid_task = future_tasks_section(status=status)
                with self.subTest(status=status, case="valid"):
                    _rows, errors = atlas_module.validate_future_tasks_section(
                        "MIGRATION_PLAN.md",
                        valid_task,
                        inventory,
                        active_answers=active_answers,
                        draft=False,
                        mode="STANDARD",
                        artifact_texts=artifact_texts(valid_task),
                    )
                    self.assertEqual(errors, [])

                for before, after, field in mandatory_field_mutations:
                    with self.subTest(status=status, mandatory_field=field):
                        invalid = valid_task.replace(before, after, 1)
                        _rows, errors = atlas_module.validate_future_tasks_section(
                            "MIGRATION_PLAN.md",
                            invalid,
                            inventory,
                            active_answers=active_answers,
                            draft=False,
                            mode="STANDARD",
                            artifact_texts=artifact_texts(invalid),
                        )
                        self.assertTrue(
                            any(
                                f"{status} {field}".casefold() in error.casefold()
                                for error in errors
                            ),
                            errors,
                        )

                for draft_marker in (
                    "TO" + "DO.",
                    "TO-" + "DO",
                    "TO " + "DO",
                    "[TO" + "DO]",
                    "T.B.D.",
                    "T B D",
                    "T/B/D",
                    "place" + "holder.",
                    "PLACE " + "HOLDER",
                    "D R A F T",
                    "D/R/A/F/T",
                    "N / A",
                    "U N K N O W N",
                    "T:B:D",
                    "T,B,D",
                    "T;B;D",
                    "TO:DO",
                    "D:R:A:F:T",
                    "/T" + "BD/",
                    "//TO" + "DO//",
                    "«DRA" + "FT»",
                    "【PLACE" + "HOLDER】",
                    "/T/B/D/",
                    "🔹T" + "BD🔹",
                    "\ufe0fTO" + "DO\ufe0f",
                    "TO\ufe0f" + "DO",
                    "\u0301T" + "BD\u0301",
                    "D\u0307RA" + "FT",
                    "T\u200d" + "BD",
                    "PLACE\u2060" + "HOLDER",
                    "DRA\u200e" + "FT",
                    "\u2066PLACE" + "HOLDER\u2069",
                ):
                    with self.subTest(
                        status=status,
                        mandatory_field="Risks",
                        draft_marker=draft_marker,
                    ):
                        invalid = valid_task.replace(
                            "Drift from current runtime",
                            draft_marker,
                            1,
                        )
                        _rows, errors = atlas_module.validate_future_tasks_section(
                            "MIGRATION_PLAN.md",
                            invalid,
                            inventory,
                            active_answers=active_answers,
                            draft=False,
                            mode="STANDARD",
                            artifact_texts=artifact_texts(invalid),
                        )
                        self.assertTrue(
                            any(
                                f"{status} Risks".casefold() in error.casefold()
                                for error in errors
                            ),
                            errors,
                        )

                historical_marker = valid_task.replace(
                    "Drift from current runtime",
                    "The historical dra" + "ft plan is retained for audit evidence",
                    1,
                )
                with self.subTest(status=status, case="marker in substantive prose"):
                    _rows, errors = atlas_module.validate_future_tasks_section(
                        "MIGRATION_PLAN.md",
                        historical_marker,
                        inventory,
                        active_answers=active_answers,
                        draft=False,
                        mode="STANDARD",
                        artifact_texts=artifact_texts(historical_marker),
                    )
                    self.assertEqual(errors, [])

                without_grounding = valid_task.replace(
                    "src/main.py:L1; MAP:MIGRATION_PLAN.md#sequence",
                    "no grounded project evidence",
                )
                with self.subTest(status=status, case="safe basis"):
                    _rows, errors = atlas_module.validate_future_tasks_section(
                        "MIGRATION_PLAN.md",
                        without_grounding,
                        inventory,
                        active_answers=active_answers,
                        draft=False,
                        mode="STANDARD",
                        artifact_texts=artifact_texts(without_grounding),
                    )
                    self.assertTrue(
                        any(
                            f"{status} Basis requires a safe project-relative source "
                            "or map basis"
                            in error
                            for error in errors
                        ),
                        errors,
                    )

                if status == "READY":
                    with self.subTest(status=status, case="active answer provenance"):
                        _rows, errors = atlas_module.validate_future_tasks_section(
                            "MIGRATION_PLAN.md",
                            valid_task,
                            inventory,
                            active_answers=set(),
                            draft=False,
                            mode="STANDARD",
                            artifact_texts=artifact_texts(valid_task),
                        )
                        self.assertTrue(
                            any(
                                "READY Basis requires active answer provenance"
                                in error
                                for error in errors
                            ),
                            errors,
                        )

    def test_future_task_map_basis_requires_substantive_non_interaction_body(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas substantive map basis ") as temp_dir:
            project = Path(temp_dir)
            inventory = atlas_module.build_safe_inventory(project)
            active_answers = {"USER_INPUT:Q-START-1"}
            map_only_task = future_tasks_section().replace("src/main.py:L1; ", "")

            def validate(map_text: str, task: str = map_only_task) -> list[str]:
                _rows, errors = atlas_module.validate_future_tasks_section(
                    "MIGRATION_PLAN.md",
                    task,
                    inventory,
                    active_answers=active_answers,
                    draft=False,
                    mode="STANDARD",
                    artifact_texts={"MIGRATION_PLAN.md": map_text + "\n\n" + task},
                )
                return errors

            valid_map = (
                "# Migration Plan\n\n## Sequence\n\n"
                "The visible migration sequence is grounded in current evidence."
            )
            self.assertEqual(validate(valid_map), [])
            populated_table_map = (
                "# Migration Plan\n\n## Sequence\n\n"
                "| File | Meaning |\n"
                "|---|---|\n"
                "| src/main.py | Current entry point from the inspected snapshot |\n"
            )
            self.assertEqual(validate(populated_table_map), [])
            linked_prose_map = (
                "# Migration Plan\n\n## Sequence\n\n"
                "The inspected entry point is documented in "
                "[the repository guide](README.md)."
            )
            self.assertEqual(validate(linked_prose_map), [])
            for container_setext in (
                "# Migration Plan\n\n> Sequence\n---\n\n"
                "Real evidence belongs to a different container.",
                "# Migration Plan\n\n- Sequence\n---\n\n"
                "Real evidence belongs to a different container.",
                "# Migration Plan\n\n- Parent\n\n  ## Sequence\n\n"
                "  Real evidence remains inside the bullet item.",
                "# Migration Plan\n\n1. Parent\n\n   ## Sequence\n\n"
                "   Real evidence remains inside the ordered item.",
                "# Migration Plan\n\n- Parent\n\n  Sequence\n  --------\n\n"
                "  Real evidence remains inside the bullet item.",
                "# Migration Plan\n\n1. Parent\n\n   Sequence\n   ========\n\n"
                "   Real evidence remains inside the ordered item.",
                "# Migration Plan\n\n> Prefix\nSequence\n--------\n\n"
                "Real evidence remains a lazy blockquote continuation.",
            ):
                with self.subTest(container_setext=container_setext):
                    errors = validate(container_setext)
                    self.assertTrue(
                        any(
                            "Basis requires a safe project-relative source or map basis"
                            in error
                            for error in errors
                        ),
                        errors,
                    )

            nested_heading_cases = (
                "- Parent\n\n  ## Sequence\n",
                "1. Parent\n\n   ## Sequence\n",
                "- Parent\n\n  Sequence\n  --------\n",
                "1. Parent\n\n   Sequence\n   ========\n",
                "> Prefix\nSequence\n--------\n",
            )
            for nested_heading in nested_heading_cases:
                with self.subTest(nested_heading=nested_heading):
                    _rendered, headings = (
                        atlas_module.top_level_markdown_section_headings(
                            nested_heading
                        )
                    )
                    self.assertEqual(headings, ())

            for indentation in range(4):
                prefix = " " * indentation
                top_level = (
                    f"{prefix}# Top H1\n\n"
                    f"{prefix}## Top H2\n\n"
                    f"{prefix}Setext H1\n{prefix}==========\n\n"
                    f"{prefix}Setext H2\n{prefix}----------\n"
                )
                with self.subTest(top_level_indentation=indentation):
                    _rendered, headings = (
                        atlas_module.top_level_markdown_section_headings(
                            top_level
                        )
                    )
                    self.assertEqual(
                        tuple((level, name) for _start, _end, level, name in headings),
                        (
                            (1, "Top H1"),
                            (2, "Top H2"),
                            (1, "Setext H1"),
                            (2, "Setext H2"),
                        ),
                    )

            _rendered, headings = atlas_module.top_level_markdown_section_headings(
                "> Prefix\nlazy continuation\n## Real top level\n"
            )
            self.assertEqual(
                tuple((level, name) for _start, _end, level, name in headings),
                ((2, "Real top level"),),
            )
            for prefix in ("é", "中", "９", "\u200d"):
                with self.subTest(map_prefix=prefix):
                    prefixed_task = map_only_task.replace(
                        "MAP:MIGRATION_PLAN.md#sequence",
                        prefix + "MAP:MIGRATION_PLAN.md#sequence",
                        1,
                    )
                    errors = validate(valid_map, prefixed_task)
                    self.assertTrue(
                        any(
                            "Basis requires a safe project-relative source or map basis"
                            in error
                            for error in errors
                        ),
                        errors,
                    )

            for body in (
                "",
                "TO" + "DO",
                "T" + "BD: document sequence",
                "place" + "holder content",
                "| File | Meaning |\n|---|---|",
                "### Current Evidence",
                "> ### Current Evidence",
                "1. ### Current Evidence",
                "Current Evidence\n----------------",
                "Current Evidence\n================",
                "Current Evidence\n----------------\n\n"
                "Real evidence belongs to the following section.",
                "# Other\n\nReal evidence belongs to the following section.",
                "[Current evidence](README.md)",
                "[Current evidence][guide]\n\n[guide]: README.md",
                "![Current evidence](assets/evidence.svg)",
                "<https://example.com/current-evidence>",
                "https://example.com/current-evidence",
            ):
                with self.subTest(body=body):
                    errors = validate(
                        "# Migration Plan\n\n## Sequence\n\n" + body
                    )
                    self.assertTrue(
                        any("no visible map anchor" in error.casefold() for error in errors),
                        errors,
                    )

            circular_task = map_only_task.replace(
                "MAP:MIGRATION_PLAN.md#sequence",
                "MAP:MIGRATION_PLAN.md#future-tasks",
            )
            circular_errors = validate("# Migration Plan", circular_task)
            self.assertTrue(
                any("no visible map anchor" in error.casefold() for error in circular_errors),
                circular_errors,
            )

            decoy_maps = (
                (
                    "fenced",
                    "```markdown\n## Sequence\n\n"
                    "The decoy sequence looks substantive but is fenced.\n```",
                ),
                (
                    "html",
                    "<div>\n## Sequence\n\n"
                    "The decoy sequence looks substantive but is hidden.\n</div>",
                ),
            )
            for label, decoy_map in decoy_maps:
                with self.subTest(decoy=label):
                    errors = validate(decoy_map)
                    self.assertTrue(
                        any("no visible map anchor" in error.casefold() for error in errors),
                        errors,
                    )

            normalized_collision = (
                "# Migration Plan\n\n## Sequence\n\n"
                + "TO"
                + "DO"
                + "\n\n## sequence\n\n"
                "The second spelling has substantive current evidence."
            )
            collision_errors = validate(normalized_collision)
            self.assertTrue(
                any(
                    "no visible map anchor" in error.casefold()
                    for error in collision_errors
                ),
                collision_errors,
            )

    def test_inactive_answers_cannot_authorize_active_future_tasks(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas inactive answer provenance ") as temp_dir:
            project = Path(temp_dir)
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text("pass\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)
            answered_fragment = (
                "| A | - | ANSWERED | TARGET: direction 1 | "
                "USER_INPUT:Q-START-1 | 2026-07-22T10:00:00Z |"
            )
            for answer_state in ("SKIPPED", "UNAVAILABLE", "SUPERSEDED"):
                inactive_fragment = (
                    f"| - | - | {answer_state} | - | "
                    "USER_INPUT:Q-START-1 | 2026-07-22T10:00:00Z |"
                )
                for task_status in ("READY", "BLOCKED"):
                    with self.subTest(
                        answer_state=answer_state,
                        task_status=task_status,
                    ):
                        start = alignment_section(
                            "Start Alignment", "START", 3
                        ).replace(answered_fragment, inactive_fragment, 1)
                        documents = {
                            "PRODUCT_AND_REQUIREMENTS.md": start,
                            "LIVE_HANDOFF.md": (
                                alignment_section(
                                    "Finish Alignment", "FINISH", 3
                                )
                                + "\n"
                                + run_economics_section()
                            ),
                            "MIGRATION_PLAN.md": (
                                "## Sequence\n\n"
                                "The visible migration sequence is grounded in current evidence.\n\n"
                                + future_tasks_section(status=task_status)
                            ),
                        }
                        _registries, errors = (
                            atlas_module.validate_interaction_documents(
                                "STANDARD",
                                documents,
                                inventory,
                                draft=False,
                            )
                        )
                        self.assertTrue(
                            any(
                                f"{task_status} Basis requires active answer provenance"
                                in error
                                for error in errors
                            ),
                            errors,
                        )

    def test_blocked_rejects_malformed_user_input_like_references(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(
            prefix="atlas malformed blocked user input "
        ) as temp_dir:
            project = Path(temp_dir)
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text(
                "pass\n",
                encoding="utf-8",
            )
            inventory = atlas_module.build_safe_inventory(project)
            canonical = future_tasks_section(answer_ref="", status="BLOCKED")
            for malformed in (
                "USER_INPUT:",
                "USER_INPUT:" + ("a" * 129),
                "éUSER_INPUT:Q-START-1",
                "USER_INPUT:Q-START-1é",
                "\u200dUSER_INPUT:Q-START-1",
                "USER_INPUT:Q-START-1\u200d",
            ):
                with self.subTest(malformed=malformed):
                    task = canonical.replace(
                        "src/main.py:L1",
                        malformed + "; src/main.py:L1",
                        1,
                    )
                    _rows, errors = atlas_module.validate_future_tasks_section(
                        "MIGRATION_PLAN.md",
                        task,
                        inventory,
                        active_answers=set(),
                        draft=False,
                        mode="STANDARD",
                        artifact_texts={
                            "MIGRATION_PLAN.md": (
                                "# Migration Plan\n\n## Sequence\n\n"
                                "The visible migration sequence is grounded in "
                                "current evidence.\n\n"
                                + task
                            )
                        },
                    )
                    self.assertTrue(
                        any(
                            "malformed answer provenance USER_INPUT:<Question ID>"
                            in error
                            for error in errors
                        ),
                        errors,
                    )

    def test_stop_unavailable_without_active_answers_allows_blocked_not_ready(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas unavailable blocked fallback ") as temp_dir:
            project = Path(temp_dir)
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text("pass\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)

            def unavailable_alignment(section: str, phase: str) -> str:
                text = alignment_section(
                    section,
                    phase,
                    3,
                    stop_decision="STOP_UNAVAILABLE",
                )
                for index in range(1, 4):
                    text = text.replace(
                        f"| A | - | ANSWERED | TARGET: direction {index} | "
                        f"USER_INPUT:Q-{phase}-{index} | "
                        "2026-07-22T10:00:00Z |",
                        "| - | - | UNAVAILABLE | - | - | - |",
                        1,
                    )
                return text

            common_documents = {
                "PRODUCT_AND_REQUIREMENTS.md": unavailable_alignment(
                    "Start Alignment",
                    "START",
                ),
                "LIVE_HANDOFF.md": (
                    unavailable_alignment("Finish Alignment", "FINISH")
                    + "\n"
                    + run_economics_section()
                ),
            }
            for status in ("BLOCKED", "READY"):
                task = future_tasks_section(answer_ref="", status=status)
                documents = {
                    **common_documents,
                    "MIGRATION_PLAN.md": (
                        "## Sequence\n\n"
                        "The visible migration sequence is grounded in current evidence.\n\n"
                        + task
                    ),
                }
                with self.subTest(status=status):
                    _registries, errors = atlas_module.validate_interaction_documents(
                        "STANDARD",
                        documents,
                        inventory,
                        draft=False,
                    )
                    if status == "BLOCKED":
                        self.assertEqual(errors, [])
                    else:
                        self.assertTrue(
                            any(
                                "READY Basis requires active answer provenance"
                                in error
                                for error in errors
                            ),
                            errors,
                        )

    def test_named_interaction_sections_ignore_non_rendered_decoys_and_reject_duplicates(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()
        visible = alignment_section("Start Alignment", "START", 3)
        fenced_decoy = (
            "```markdown\n"
            + alignment_section("Start Alignment", "DECOY", 3)
            + "```\n"
        )
        html_decoy = (
            "<div>\n"
            + alignment_section("Start Alignment", "HTML", 3)
            + "</div>\n\n"
        )
        _questions, _batches, errors = atlas_module.validate_alignment_section(
            "PROJECT_ATLAS.md",
            fenced_decoy + html_decoy + visible,
            "Start Alignment",
            draft=False,
        )
        self.assertEqual(errors, [])

        duplicated = visible + "\n" + visible
        _questions, _batches, errors = atlas_module.validate_alignment_section(
            "PROJECT_ATLAS.md",
            duplicated,
            "Start Alignment",
            draft=False,
        )
        self.assertTrue(any("exactly one" in error.casefold() for error in errors), errors)

    def test_interaction_tables_do_not_cross_top_level_heading_boundaries(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()
        boundaries = (
            ("atx h1", "# Foreign Boundary", None),
            ("setext h1", "Foreign Boundary\n================", None),
            ("setext h2", "Foreign Boundary\n----------------", "Foreign Boundary"),
        )
        valid = alignment_section("Start Alignment", "START", 3)
        for label, boundary, expected_second_owner in boundaries:
            with self.subTest(boundary=label, surface="partial alignment"):
                crossed = valid.replace(
                    "\n\n" + BATCH_HEADER,
                    "\n\n" + boundary + "\n\n" + BATCH_HEADER,
                    1,
                )
                questions, batches, errors = atlas_module.validate_alignment_section(
                    "PRODUCT_AND_REQUIREMENTS.md",
                    crossed,
                    "Start Alignment",
                    draft=False,
                )
                self.assertEqual(len(questions), 3)
                self.assertEqual(batches, [])
                self.assertTrue(
                    any(
                        "must contain exactly one batch ledger table" in error
                        for error in errors
                    ),
                    errors,
                )
                owners = tuple(
                    section
                    for section, header in atlas_module.visible_interaction_table_headers(
                        crossed
                    )
                    if header
                    in {
                        tuple(column.casefold() for column in atlas_module.QUESTION_TABLE_COLUMNS),
                        tuple(column.casefold() for column in atlas_module.BATCH_LEDGER_COLUMNS),
                    }
                )
                self.assertEqual(
                    owners,
                    ("Start Alignment", expected_second_owner),
                )

        with tempfile.TemporaryDirectory(prefix="atlas crossed interaction sections ") as temp_dir:
            project = Path(temp_dir)
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text("pass\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)
            base_documents = {
                "PRODUCT_AND_REQUIREMENTS.md": alignment_section(
                    "Start Alignment", "START", 3
                ),
                "LIVE_HANDOFF.md": (
                    alignment_section("Finish Alignment", "FINISH", 3)
                    + "\n"
                    + run_economics_section()
                ),
                "MIGRATION_PLAN.md": (
                    "## Sequence\n\n"
                    "The visible migration sequence is grounded in current evidence.\n\n"
                    + future_tasks_section()
                ),
            }
            surfaces = (
                ("Start Alignment", "PRODUCT_AND_REQUIREMENTS.md"),
                ("Finish Alignment", "LIVE_HANDOFF.md"),
                ("Run Economics", "LIVE_HANDOFF.md"),
                ("Future Tasks", "MIGRATION_PLAN.md"),
            )
            for section_name, filename in surfaces:
                for label, boundary, _expected_owner in boundaries:
                    with self.subTest(
                        surface=section_name,
                        boundary=label,
                    ):
                        documents = dict(base_documents)
                        documents[filename] = documents[filename].replace(
                            f"## {section_name}\n\n",
                            f"## {section_name}\n\n{boundary}\n\n",
                            1,
                        )
                        _registries, errors = (
                            atlas_module.validate_interaction_documents(
                                "STANDARD",
                                documents,
                                inventory,
                                draft=False,
                            )
                        )
                        self.assertTrue(
                            any(
                                section_name.casefold() in error.casefold()
                                and "must contain exactly one" in error.casefold()
                                for error in errors
                            ),
                            errors,
                        )
                        self.assertTrue(
                            any(
                                "misplaced or competing interaction table"
                                in error.casefold()
                                for error in errors
                            ),
                            errors,
                        )

    def test_interaction_documents_enforce_mode_placement_and_visible_decoys(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas interaction placement ") as temp_dir:
            project = Path(temp_dir)
            (project / "src").mkdir()
            (project / "src" / "main.py").write_text("pass\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)
            documents = {
                "PRODUCT_AND_REQUIREMENTS.md": alignment_section(
                    "Start Alignment", "START", 3
                ),
                "LIVE_HANDOFF.md": (
                    alignment_section("Finish Alignment", "FINISH", 3)
                    + "\n"
                    + run_economics_section()
                ),
                "MIGRATION_PLAN.md": (
                    "## Sequence\n\n"
                    "The visible migration sequence is grounded in current evidence.\n\n"
                    + future_tasks_section()
                ),
            }
            _registries, errors = atlas_module.validate_interaction_documents(
                "STANDARD",
                documents,
                inventory,
                draft=False,
            )
            self.assertEqual(errors, [])

            visible_decoy = (
                "\n## Notes\n\n"
                f"{FUTURE_TASKS_HEADER}\n{FUTURE_TASKS_SEPARATOR}\n"
            )
            documents["LIVE_HANDOFF.md"] += visible_decoy
            _registries, errors = atlas_module.validate_interaction_documents(
                "STANDARD",
                documents,
                inventory,
                draft=False,
            )
            self.assertTrue(
                any("competing interaction table" in error.casefold() for error in errors),
                errors,
            )

    def test_interaction_table_row_ceiling_is_enforced(self) -> None:
        atlas_module = self.load_atlas_subject()
        row = (
            "| Q-START-X | B-START-1 | Direction | Choose a target direction | A | B | C | "
            "Другое — напишу сам | A | - | ANSWERED | TARGET: direction | "
            "USER_INPUT:answer-start-x | 2026-07-22T10:00:00Z |"
        )
        oversized = (
            "## Start Alignment\n\n"
            f"{QUESTION_HEADER}\n{QUESTION_SEPARATOR}\n"
            + "\n".join(row.replace("Q-START-X", f"Q-START-{index}") for index in range(5_001))
            + "\n\n"
            f"{BATCH_HEADER}\n{BATCH_SEPARATOR}\n"
        )
        _questions, _batches, errors = atlas_module.validate_alignment_section(
            "PROJECT_ATLAS.md",
            oversized,
            "Start Alignment",
            draft=True,
        )
        self.assertTrue(any("row limit" in error.casefold() for error in errors), errors)

    def test_future_dated_trace_evidence_is_rejected(self) -> None:
        atlas_module = self.load_atlas_subject()
        record = {
            "claim_kind": "CONFIRMED",
            "source_type": "FILE",
            "status": "ACTIVE",
            "observed_at": "2099-01-01T00:00:00Z",
            "atlas_refs": "-",
        }
        errors = atlas_module.trace_record_compatibility_errors(record, 2)
        self.assertTrue(any("future" in error.lower() for error in errors), errors)

    def test_future_timestamp_clock_skew_boundary_is_exact(self) -> None:
        atlas_module = self.load_atlas_subject()
        observed_now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        self.assertFalse(
            atlas_module.evidence_timestamp_is_future(
                observed_now + timedelta(seconds=300), observed_now=observed_now
            )
        )
        self.assertTrue(
            atlas_module.evidence_timestamp_is_future(
                observed_now + timedelta(seconds=301), observed_now=observed_now
            )
        )

    def test_unsafe_markdown_source_reference_detection_is_fail_closed(self) -> None:
        atlas_module = self.load_atlas_subject()
        for reference in (
            "../../outside/private.py:L1",
            "../outside.py:1",
            "%2e%2e/outside.py:L1",
            "~/private.py:L1",
        ):
            with self.subTest(reference=reference):
                self.assertEqual(
                    atlas_module.unsafe_markdown_source_references(f"`{reference}`"),
                    {atlas_module.decoded_scan_value(reference)},
                )
        self.assertFalse(
            atlas_module.unsafe_markdown_source_references(
                "`legacy_system/gateway.py:L8-L11`"
            )
        )
        self.assertFalse(atlas_module.unsafe_markdown_source_references("`..`"))
        self.assertFalse(
            atlas_module.unsafe_markdown_source_references("`api` / `worker`")
        )
        self.assertFalse(
            atlas_module.unsafe_markdown_source_references(
                "<https://example.com/a/../outside.py>"
            )
        )
        self.assertFalse(
            atlas_module.unsafe_markdown_source_references(
                "```text\n../../outside/private.py:L1\n```"
            )
        )
        self.assertFalse(
            atlas_module.unsafe_markdown_source_references(
                "~~~text\n../../outside/private.py:L1\n~~~"
            )
        )
        self.assertFalse(
            atlas_module.unsafe_markdown_source_references(
                "    ../../outside/private.py:L1\n"
            )
        )
        for markup in (
            "Source ../outside.txt:L1",
            "Source %2e%2e/outside.txt:L1",
            "Unsafe ../../outside/private.py:L1 prose",
            "[outside](%2e%2e/outside/private.py#L1)",
            "[outside](%252e%252e/outside/private.py#L1)",
            "Source ..\\outside.py:L1 in ordinary prose.",
            "Source ..%5Coutside.py:L1 in ordinary prose.",
            "Source ../Dockerfile:L1 in ordinary prose.",
            "Source ../секрет.py in ordinary prose.",
            "Source ../secret@v1.py in ordinary prose.",
            "Source ../%D1%81%D0%B5%D0%BA%D1%80%D0%B5%D1%82.py in ordinary prose.",
            "Source: ..&#x2f;outside.py:L1",
            "[outside]:<../outside.py#L1>",
            "[outside]:\n  ../outside.py#L1",
            '<a href="../outside.py#L1">outside</a>',
            "<a href='../outside.py#L1'>outside</a>",
            "<a href=../outside.py#L1>outside</a>",
            '<a href="..&#x2f;outside.py#L1">outside</a>',
            '<form action="../outside.py#L1">outside</form>',
            '<a href="redirect(../outside.py:L1)tail">outside</a>',
            '<div\n href="../outside.py:L1">\n</div>\n',
            '<script\n src="../outside.py:L1">\n</script>\n',
            '<svg xlink:href="../outside.py#L1"></svg>',
            '<img srcset="safe.png 1x, ../outside.py#L1 2x">',
        ):
            with self.subTest(markup=markup):
                self.assertTrue(
                    atlas_module.unsafe_markdown_source_references(markup), markup
                )

    def test_source_location_candidate_suffix_scan_is_bounded(self) -> None:
        atlas_module = self.load_atlas_subject()
        wrapped = ("segment(" * 4096) + "../outside.py:L1"

        self.assertEqual(
            atlas_module.source_location_candidate_values(wrapped),
            tuple(sorted((wrapped, "../outside.py:L1"))),
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(f"Source: {wrapped}"),
            {"../outside.py:L1"},
        )

    def test_validate_rejects_unsafe_markdown_source_reference_in_every_mode(self) -> None:
        modes = (
            ("QUICK", "PROJECT_ATLAS.md"),
            ("STANDARD", "CURRENT_ARCHITECTURE.md"),
            ("FORENSIC", "CURRENT_ARCHITECTURE.md"),
        )
        unsafe_markup = (
            "Source ..\\outside.py:L1 in ordinary prose.",
            "Source ..%5Coutside.py:L1 in ordinary prose.",
            "Source ../Dockerfile:L1 in ordinary prose.",
            "Source ../секрет.py in ordinary prose.",
            "Source ../secret@v1.py in ordinary prose.",
            "Source ../%D1%81%D0%B5%D0%BA%D1%80%D0%B5%D1%82.py in ordinary prose.",
            "Source: ..&#x2f;outside.py:L1",
            '<form action="../outside.py#L1">outside</form>',
            "```text\n> ```\n```\nSource: ../outside.py:L1",
            "> ```text\n> code\n> > ```\n> ```\n> Source: ../outside.py:L1",
            "-\t```text\n  Source: ../outside.py:L1",
            "-\t  ```text\n  Source: ../outside.py:L1",
        )
        safe_markup = (
            "Source src/module.py:L1.",
            "[source](src/module.py#L1)",
            "![source](src/module.py#L1)",
        )
        with tempfile.TemporaryDirectory(prefix="atlas every mode unsafe source ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src" / "module.py").write_text(
                "print('safe source')\n", encoding="utf-8"
            )
            for filename in ("outside.py", "5Coutside.py", ".py", "Dockerfile"):
                (project / filename).write_text("fixture\n", encoding="utf-8")

            for mode, artifact_name in modes:
                for index, markup in enumerate(unsafe_markup):
                    with self.subTest(mode=mode, unsafe=markup):
                        atlas = root / f"{mode.lower()}-unsafe-{index}"
                        initialized = run_atlas(
                            "init",
                            "--project",
                            project,
                            "--mode",
                            mode,
                            "--output",
                            atlas,
                        )
                        self.assertEqual(initialized.returncode, 0, initialized.stderr)
                        artifact = atlas / artifact_name
                        artifact.write_text(
                            artifact.read_text(encoding="utf-8") + f"\n{markup}\n",
                            encoding="utf-8",
                        )
                        validated = run_atlas(
                            "validate",
                            "--atlas",
                            atlas,
                            "--project",
                            project,
                            "--mode",
                            mode,
                            "--draft",
                        )
                        self.assertNotEqual(validated.returncode, 0)
                        diagnostic = f"{validated.stdout}\n{validated.stderr}".lower()
                        self.assertIn("unsafe project source reference", diagnostic)

                for index, markup in enumerate(safe_markup):
                    with self.subTest(mode=mode, safe=markup):
                        atlas = root / f"{mode.lower()}-safe-{index}"
                        initialized = run_atlas(
                            "init",
                            "--project",
                            project,
                            "--mode",
                            mode,
                            "--output",
                            atlas,
                        )
                        self.assertEqual(initialized.returncode, 0, initialized.stderr)
                        artifact = atlas / artifact_name
                        artifact.write_text(
                            artifact.read_text(encoding="utf-8") + f"\n{markup}\n",
                            encoding="utf-8",
                        )
                        validated = run_atlas(
                            "validate",
                            "--atlas",
                            atlas,
                            "--project",
                            project,
                            "--mode",
                            mode,
                            "--draft",
                        )
                        self.assertEqual(
                            validated.returncode,
                            0,
                            f"{validated.stdout}\n{validated.stderr}",
                        )

    def test_commonmark_reference_definitions_are_rejected_in_every_mode(self) -> None:
        modes = (
            ("QUICK", "PROJECT_ATLAS.md"),
            ("STANDARD", "CURRENT_ARCHITECTURE.md"),
            ("FORENSIC", "CURRENT_ARCHITECTURE.md"),
        )
        definitions = {
            "without-space": "[evidence]:../outside/private.py:L1",
            "with-space": "[evidence]: ../outside/private.py:L1",
        }
        with tempfile.TemporaryDirectory(prefix="atlas commonmark definition ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()

            for mode, artifact_name in modes:
                for variant, definition in definitions.items():
                    with self.subTest(mode=mode, variant=variant):
                        atlas = root / f"{mode.lower()}-{variant}"
                        initialized = run_atlas(
                            "init",
                            "--project",
                            project,
                            "--mode",
                            mode,
                            "--output",
                            atlas,
                        )
                        self.assertEqual(initialized.returncode, 0, initialized.stderr)
                        artifact = atlas / artifact_name
                        artifact.write_text(
                            artifact.read_text(encoding="utf-8")
                            + f"\n{definition}\n",
                            encoding="utf-8",
                        )

                        validated = run_atlas(
                            "validate",
                            "--atlas",
                            atlas,
                            "--project",
                            project,
                            "--mode",
                            mode,
                            "--draft",
                        )

                        self.assertNotEqual(validated.returncode, 0)
                        self.assertIn(
                            "unsafe project source reference",
                            f"{validated.stdout}\n{validated.stderr}".lower(),
                        )

    def test_line_less_explicit_source_references_reach_safe_inventory_rejection_in_every_mode(
        self,
    ) -> None:
        modes = (
            ("QUICK", "PROJECT_ATLAS.md"),
            ("STANDARD", "CURRENT_ARCHITECTURE.md"),
            ("FORENSIC", "CURRENT_ARCHITECTURE.md"),
        )
        ignored_references = (
            "ignored.py",
            "ignored-root",
            "ignored@v1",
            "ignoredroot",
            "@private",
            "BUILD",
            "private/secret.py",
            "private/секрет.py",
            "private/Dockerfile",
            "private/module@v1.py",
        )
        source_templates = (
            "Source {}",
            "Source: {}",
            "**Source:** {}",
            "Source: {} remains verified",
            "**Source:** {} remains verified",
            "1. 1. Source: {}",
            "- 1. **Source:** {} remains verified",
            "| **Source:** {} |",
            "[source]({})",
            "[source]({}#section)",
            "[source]({}?view=raw#section)",
            "[source]: {}",
            "> [source]: {}",
            "- Source: {}",
            '<a data-source="{}">source</a>',
            '<a href="{}#section">source</a>',
            '<form action="{}">source</form>',
            '<button formaction="{}">source</button>',
            '<object data="{}">source</object>',
            '<video poster="{}"></video>',
            '<svg xlink:href="{}"></svg>',
            '<img srcset="{} 1x">',
            '<img srcset="{}#section 1x">',
            '<link imagesrcset="{} 1x">',
            '<link imagesrcset="{}#section 1x">',
            '<a ping="{}">source</a>',
            '<a ping="{}#section">source</a>',
            '<object archive="{}"></object>',
            '<object archive="{}#section"></object>',
        )
        with tempfile.TemporaryDirectory(prefix="atlas ignored explicit source ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            private = project / "private"
            private.mkdir(parents=True)
            (project / ".gitignore").write_text(
                "ignored.py\nignored-root\nignored@v1\nignoredroot\n@private\nBUILD\nprivate/\n",
                encoding="utf-8",
            )
            for relative in ignored_references:
                (project / relative).write_text("private evidence\n", encoding="utf-8")

            for mode, artifact_name in modes:
                for label_index, source_template in enumerate(source_templates):
                    with self.subTest(mode=mode, source_template=source_template):
                        atlas = root / f"{mode.lower()}-source-label-{label_index}"
                        initialized = run_atlas(
                            "init",
                            "--project",
                            project,
                            "--mode",
                            mode,
                            "--output",
                            atlas,
                        )
                        self.assertEqual(initialized.returncode, 0, initialized.stderr)
                        artifact = atlas / artifact_name
                        explicit_references = "\n".join(
                            source_template.format(relative)
                            for relative in ignored_references
                        )
                        artifact.write_text(
                            artifact.read_text(encoding="utf-8")
                            + f"\n{explicit_references}\n",
                            encoding="utf-8",
                        )

                        validated = run_atlas(
                            "validate",
                            "--atlas",
                            atlas,
                            "--project",
                            project,
                            "--mode",
                            mode,
                            "--draft",
                        )

                        self.assertNotEqual(validated.returncode, 0)
                        diagnostic = f"{validated.stdout}\n{validated.stderr}"
                        self.assertIn("outside the safe inventory", diagnostic.lower())
                        for relative in ignored_references:
                            self.assertIn(relative, diagnostic)

    def test_validate_accepts_benign_details_and_source_like_vocabulary_in_every_mode(
        self,
    ) -> None:
        modes = (
            ("QUICK", "PROJECT_ATLAS.md"),
            ("STANDARD", "CURRENT_ARCHITECTURE.md"),
            ("FORENSIC", "CURRENT_ARCHITECTURE.md"),
        )
        benign_markup = (
            "<details><summary>Evidence</summary></details>\n\n"
            "Source or worktree snapshot: UNKNOWN\n\n"
            "The source state/data boundary is documented.\n\n"
            "Admitted product evidence: README and nine safe package files.\n\n"
            "Compatibility and state/data handling uses `Path.replace`, `*.sqlite3`, "
            "and `.new`."
        )
        with tempfile.TemporaryDirectory(prefix="atlas benign source vocabulary ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()

            for mode, artifact_name in modes:
                with self.subTest(mode=mode):
                    atlas = root / mode.lower()
                    initialized = run_atlas(
                        "init",
                        "--project",
                        project,
                        "--mode",
                        mode,
                        "--output",
                        atlas,
                    )
                    self.assertEqual(initialized.returncode, 0, initialized.stderr)
                    artifact = atlas / artifact_name
                    artifact.write_text(
                        artifact.read_text(encoding="utf-8")
                        + f"\n{benign_markup}\n",
                        encoding="utf-8",
                    )

                    validated = run_atlas(
                        "validate",
                        "--atlas",
                        atlas,
                        "--project",
                        project,
                        "--mode",
                        mode,
                        "--draft",
                    )

                    self.assertEqual(
                        validated.returncode,
                        0,
                        f"{validated.stdout}\n{validated.stderr}",
                    )

    def test_citations_inside_commonmark_fences_are_not_scanned(self) -> None:
        atlas_module = self.load_atlas_subject()
        safe_members = frozenset(
            {atlas_module.PurePosixPath("private/secret.py")}
        )
        fenced_blocks = {
            "backtick-longer-close": (
                "```text\n"
                "Source: private/secret.py:L1\n"
                "Source: ../outside.py:L1\n"
                "````\n"
            ),
            "tilde-longer-close": (
                "~~~text\n"
                "Source: private/secret.py:L1\n"
                "Source: ../outside.py:L1\n"
                "~~~~\n"
            ),
            "backtick-unclosed-to-eof": (
                "```text\n"
                "Source: private/secret.py:L1\n"
                "Source: ../outside.py:L1\n"
            ),
            "tilde-unclosed-to-eof": (
                "~~~text\n"
                "Source: private/secret.py:L1\n"
                "Source: ../outside.py:L1\n"
            ),
            "backtick-shorter-non-close": (
                "````text\n"
                "Source: private/secret.py:L1\n"
                "```\n"
                "Source: ../outside.py:L1\n"
            ),
            "tilde-other-character-non-close": (
                "~~~~text\n"
                "Source: private/secret.py:L1\n"
                "````\n"
                "Source: ../outside.py:L1\n"
            ),
            "blockquote-fence": (
                "> ```text\n"
                "> Source: private/secret.py:L1\n"
                "> Source: ../outside.py:L1\n"
                "> ````\n"
            ),
            "list-fence": (
                "- ~~~text\n"
                "  Source: private/secret.py:L1\n"
                "  Source: ../outside.py:L1\n"
                "  ~~~~\n"
            ),
            "nested-blockquote-list-fence": (
                "> 1. ```text\n"
                ">    Source: private/secret.py:L1\n"
                ">    Source: ../outside.py:L1\n"
                ">    ````\n"
            ),
            "ordered-list-start-two-after-blank": (
                "ordinary paragraph\n\n"
                "2. ```text\n"
                "   Source: private/secret.py:L1\n"
                "   Source: ../outside.py:L1\n"
                "   ```\n"
            ),
            "nested-ordered-list-after-blockquote-interrupt": (
                "ordinary paragraph\n"
                "> 2. ```text\n"
                ">    Source: private/secret.py:L1\n"
                ">    Source: ../outside.py:L1\n"
                ">    ```\n"
            ),
        }

        for variant, markup in fenced_blocks.items():
            with self.subTest(variant=variant):
                self.assertEqual(
                    atlas_module.markdown_source_locations(markup, safe_members),
                    set(),
                )
                self.assertEqual(
                    atlas_module.unsafe_markdown_source_references(markup),
                    set(),
                )
        invalid_backtick_info = (
            "```text`invalid\nSource: ../outside.py:L1\n```\n"
        )
        self.assertTrue(
            atlas_module.unsafe_markdown_source_references(invalid_backtick_info)
        )
        for escaped_container in (
            "> ```text\n> code only\nSource: ../outside.py:L1\n",
            "- ~~~text\n  code only\nSource: ../outside.py:L1\n",
            "```text\n> ```\n```\nSource: ../outside.py:L1\n",
            "> ```text\n> code\n> > ```\n> ```\n> Source: ../outside.py:L1\n",
            "-\t```text\n  Source: ../outside.py:L1\n",
            "-\t  ```text\n  Source: ../outside.py:L1\n",
        ):
            with self.subTest(escaped_container=escaped_container):
                self.assertTrue(
                    atlas_module.unsafe_markdown_source_references(escaped_container)
                )
        data_srcset = '<img srcset="data:image/png;base64,AAAA 1x, BUILD 2x">'
        self.assertEqual(
            atlas_module.html_srcset_urls("data:image/png;base64,AAAA 1x, BUILD 2x"),
            ("data:image/png;base64,AAAA", "BUILD"),
        )
        self.assertEqual(
            atlas_module.markdown_source_locations(data_srcset, frozenset()),
            {(atlas_module.PurePosixPath("BUILD"), None, None)},
        )

    def test_commonmark_container_columns_and_alternation_are_preserved(self) -> None:
        atlas_module = self.load_atlas_subject()
        mixed_list_fence_blockquote_tab = (
            "1. ```text\n> \tSource: ../outside.py:L1\n"
        )
        self.assertEqual(
            atlas_module.markdown_evidence_text(mixed_list_fence_blockquote_tab),
            "\n> \tSource: ../outside.py:L1\n",
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(
                mixed_list_fence_blockquote_tab
            ),
            {"../outside.py:L1"},
        )

        valid_indented_code = " \tSource: ../outside.py:L1\n"
        self.assertEqual(
            atlas_module.markdown_evidence_text(valid_indented_code),
            "\n",
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(valid_indented_code),
            set(),
        )

        expected_location = {
            (atlas_module.PurePosixPath("ignored.py"), None, None)
        }
        partial_tab_blockquote = ">\tSource ignored.py\n"
        self.assertEqual(
            atlas_module.markdown_evidence_text(partial_tab_blockquote),
            partial_tab_blockquote,
        )
        self.assertEqual(
            atlas_module.markdown_source_locations(
                partial_tab_blockquote, frozenset()
            ),
            expected_location,
        )

        blockquote_indented_code = ">\t\tSource: ../outside.py:L1\n"
        self.assertEqual(
            atlas_module.markdown_evidence_text(blockquote_indented_code),
            "\n",
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(
                blockquote_indented_code
            ),
            set(),
        )

        blockquote_tabbed_fence = (
            ">\t```text\n"
            ">\tSource: ../outside.py:L1\n"
            ">\t```\n"
        )
        self.assertEqual(
            atlas_module.markdown_evidence_text(blockquote_tabbed_fence),
            "\n\n\n",
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(
                blockquote_tabbed_fence
            ),
            set(),
        )

        alternating_fence = (
            "> 1. > ```text\n"
            ">    > Source: ../outside.py:L1\n"
            ">    > ```\n"
        )
        self.assertEqual(
            atlas_module.markdown_evidence_text(alternating_fence),
            "\n\n\n",
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(alternating_fence),
            set(),
        )

        reordered_container = (
            "> 1. ```text\n"
            "1. > Source: ../outside.py:L1\n"
        )
        self.assertEqual(
            atlas_module.markdown_evidence_text(reordered_container),
            "\n1. > Source: ../outside.py:L1\n",
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(reordered_container),
            {"../outside.py:L1"},
        )

        deeply_alternating = ("> 1. " * 256) + "Source ignored.py\n"
        self.assertEqual(
            atlas_module.markdown_source_locations(
                deeply_alternating, frozenset()
            ),
            expected_location,
        )

        nested_list_depth = 2048
        nested_list_indent = " " * (3 * nested_list_depth)
        deeply_nested_fence = (
            ("1. " * nested_list_depth)
            + "```text\n"
            + "\n"
            + nested_list_indent
            + "Source: ../outside.py:L1\n"
            + nested_list_indent
            + "```\n"
        )
        self.assertEqual(
            atlas_module.markdown_evidence_text(deeply_nested_fence),
            "\n\n\n\n",
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(
                deeply_nested_fence
            ),
            set(),
        )

        for markup in (
            "1. > Source ignored.py\n",
            "> 1. > Source ignored.py\n",
        ):
            with self.subTest(markup=markup):
                self.assertEqual(
                    atlas_module.markdown_evidence_text(markup),
                    markup,
                )
                self.assertEqual(
                    atlas_module.markdown_source_locations(markup, frozenset()),
                    expected_location,
                )

        original_cursor_text = atlas_module.markdown_cursor_text
        cursor_text_calls = 0

        def counted_cursor_text(body, cursor):
            nonlocal cursor_text_calls
            cursor_text_calls += 1
            return original_cursor_text(body, cursor)

        atlas_module.markdown_cursor_text = counted_cursor_text
        try:
            _cursor, containers = atlas_module.markdown_container_cursor(
                ("1. " * 4096) + "Source ignored.py"
            )
        finally:
            atlas_module.markdown_cursor_text = original_cursor_text
        self.assertEqual(len(containers), 4096)
        self.assertLessEqual(cursor_text_calls, 1)

    def test_reference_definition_state_respects_code_tabs_and_lone_cr(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()

        tab_indented_code = "\t[ref]: ../outside.py:L1\n"
        self.assertFalse(
            atlas_module.markdown_evidence_text(tab_indented_code).strip(),
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(tab_indented_code),
            set(),
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(
                "- [ref]:\n  ../outside.py:L1\n"
            ),
            {"../outside.py:L1"},
        )

        lone_cr_definition = (
            "# prior block\r"
            "[ref]:\r"
            "  README.md\r"
            "<atlas-registry>\r"
            "| ID | Claim kind | Requirement | Source | Status |\r"
            "| --- | --- | --- | --- | --- |\r"
            "| REQ-1 | CONFIRMED | hidden | service/api.py:L1 | ACTIVE |\r"
            "</atlas-registry>\r\r"
        )
        rendered = atlas_module.markdown_rendered_block_text(
            lone_cr_definition
        )
        self.assertNotIn("| ID | Claim kind |", rendered)
        self.assertNotIn("service/api.py:L1", rendered)

        malformed_definitions = (
            "[ref]: foo(bar",
            "[ref]: foo)bar",
            "[ref]: foo\x1fbar",
            "[ref]: foo\x7fbar",
            "[ref]: <README.md>'title'",
            "[ref]: <README.md> garbage",
            "[ref]: README.md garbage",
            '[ref]: README.md "title" garbage',
            '[ref]: README.md "title\ncontinued" garbage',
            "[ref]: README.md (foo(bar)",
            "[" + ("a" * 1000) + "]: README.md",
            "[a[b]: README.md",
        )
        for definition in malformed_definitions:
            with self.subTest(definition=definition):
                markup = (
                    f"{definition}\n"
                    "<atlas-registry>\n"
                    "Source: ../outside.py:L1\n"
                    "</atlas-registry>\n\n"
                )
                self.assertIn(
                    "Source: ../outside.py:L1",
                    atlas_module.markdown_rendered_block_text(markup),
                )
                self.assertEqual(
                    atlas_module.unsafe_markdown_source_references(markup),
                    {"../outside.py:L1"},
                )
        valid_escaped_label = (
            "[a\\]]: README.md\n"
            "<atlas-registry>\n"
            "Source: ../outside.py:L1\n"
            "</atlas-registry>\n\n"
        )
        self.assertNotIn(
            "Source: ../outside.py:L1",
            atlas_module.markdown_rendered_block_text(valid_escaped_label),
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(valid_escaped_label),
            set(),
        )
        multiline_label = (
            "[\n"
            "foo\n"
            "]: README.md\n"
            "<atlas-registry>\n"
            "Source: ../outside.py:L1\n"
            "</atlas-registry>\n\n"
        )
        self.assertNotIn(
            "Source: ../outside.py:L1",
            atlas_module.markdown_rendered_block_text(multiline_label),
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(multiline_label),
            set(),
        )
        for multiline_label_variant in (
            "[foo\n]: README.md",
            "[\nfoo]: README.md",
            "[foo\nbar]: README.md",
        ):
            with self.subTest(multiline_label_variant=multiline_label_variant):
                markup = (
                    f"{multiline_label_variant}\n"
                    "<atlas-registry>\n"
                    "Source: ../outside.py:L1\n"
                    "</atlas-registry>\n\n"
                )
                self.assertNotIn(
                    "Source: ../outside.py:L1",
                    atlas_module.markdown_rendered_block_text(markup),
                )
                self.assertEqual(
                    atlas_module.unsafe_markdown_source_references(markup),
                    set(),
                )
        multiline_blank = (
            "[foo\n\nbar]: README.md\n"
            "<atlas-registry>\n"
            "Source: ../outside.py:L1\n"
            "</atlas-registry>\n\n"
        )
        self.assertIn(
            "Source: ../outside.py:L1",
            atlas_module.markdown_rendered_block_text(multiline_blank),
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(multiline_blank),
            {"../outside.py:L1"},
        )
        for valid_angle_definition in (
            "[ref]: <README\\>archive.md>",
            "[ref]: <README\\<archive.md>",
        ):
            with self.subTest(valid_angle_definition=valid_angle_definition):
                markup = (
                    f"{valid_angle_definition}\n"
                    "<atlas-registry>\n"
                    "Source: ../outside.py:L1\n"
                    "</atlas-registry>\n\n"
                )
                self.assertNotIn(
                    "Source: ../outside.py:L1",
                    atlas_module.markdown_rendered_block_text(markup),
                )
                self.assertEqual(
                    atlas_module.unsafe_markdown_source_references(markup),
                    set(),
                )

    def test_commonmark_paragraph_interruptions_and_matched_ancestors_remain_evidence(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()
        unsafe_fragments = (
            "ordinary paragraph\n    Source: ../outside.py:L1",
            "ordinary paragraph\n2. ```text\n   Source: ../outside.py:L1\n   ```\n",
            "- > ```text\n    Source: ../outside.py:L1",
            "1.     ```\n    Source: ../outside.py:L1\n```\n",
            "ordinary paragraph\n- \n    Source: ../outside.py:L1\n",
            "====\n    Source: ../outside.py:L1\n",
            "--\n    Source: ../outside.py:L1\n",
            "<!--\n```\n-->\nSource: ../outside.py:L1\n",
            "<script>\n```\n</script>\nSource: ../outside.py:L1\n",
            "<![cdata[\nSource: ../outside.py:L1\n]]>\n",
            "~~~html\n<script>\n~~~\nSource: ../outside.py:L1\n",
            (
                "ordinary paragraph\n"
                "<atlas-registry>\n"
                "Source: ../outside.py:L1\n"
            ),
            "</atlas-registry invalid=yes>\nSource: ../outside.py:L1\n",
            "</script\nSource: ../outside.py:L1\n",
            (
                "<script>\n"
                "hidden raw content\n"
                "</style>\n"
                "Source: ../outside.py:L1\n"
            ),
            "\v<atlas-registry>\nSource: ../outside.py:L1\n",
        )

        for fragment in unsafe_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(
                    "../outside.py:L1",
                    atlas_module.unsafe_markdown_source_references(fragment),
                )

    def test_structural_markdown_view_ignores_nonrendered_tables_and_finds_competitors(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()
        expected = atlas_module.TABLE_CONTRACTS["PRODUCT_AND_REQUIREMENTS.md"]
        canonical_table = """| ID | Claim kind | Requirement | Source | Status |
| --- | --- | --- | --- | --- |
| REQ-1 | CONFIRMED | fake requirement | service/api.py:L1 | ACTIVE |"""
        hidden_tables = (
            "## Requirements\nx\n> 2. ```\n"
            + "\n".join(f"    {line}" for line in canonical_table.splitlines())
            + "\n```\n",
            "## Requirements\nx\n    ```\n1.     hidden\n"
            + "\n".join(f"       {line}" for line in canonical_table.splitlines())
            + "\n",
            "## Requirements\n<script>\n"
            + canonical_table
            + "\n</script>\n",
            "## Requirements\n<atlas-registry>\n"
            + canonical_table
            + "\n</atlas-registry>\n\n",
            "## Requirements\n\\`\n<script>\n"
            + canonical_table
            + "\n</script>\n\\`\n",
            "## Requirements\n`foo\n<script>\n"
            + canonical_table
            + "\n</script>\nbar`\n",
            "## Requirements\n[ref]: /url\n<atlas-registry>\n"
            + canonical_table
            + "\n</atlas-registry>\n\n",
            "## Requirements\n* * *\n<atlas-registry>\n"
            + canonical_table
            + "\n</atlas-registry>\n\n",
            "## Requirements\n> quoted paragraph\n<atlas-registry>\n"
            + canonical_table
            + "\n</atlas-registry>\n\n",
            "## Requirements\n- list paragraph\n<atlas-registry>\n"
            + canonical_table
            + "\n</atlas-registry>\n\n",
        )

        for markup in hidden_tables:
            with self.subTest(markup=markup):
                rows, errors = atlas_module.parse_table_contract(
                    "PRODUCT_AND_REQUIREMENTS.md",
                    markup,
                    expected,
                    draft=False,
                )
                self.assertEqual(rows, [])
                self.assertTrue(
                    any("missing the required table columns" in error for error in errors),
                    errors,
                )

        competing_visible_tables = (
            "## Requirements\n"
            + canonical_table
            + "\n\n`<!--`\n\n"
            + canonical_table.replace("REQ-1", "REQ-2")
            + "\n\n`-->`\n"
        )
        rows, errors = atlas_module.parse_table_contract(
            "PRODUCT_AND_REQUIREMENTS.md",
            competing_visible_tables,
            expected,
            draft=False,
        )
        self.assertEqual(rows, [])
        self.assertTrue(
            any("exactly one canonical table" in error for error in errors),
            errors,
        )
        self.assertTrue(
            atlas_module.substantive_markdown_body(
                "<atlas-note>Visible architecture detail</atlas-note>"
            )
        )

    def test_markdown_inline_link_scanner_is_bounded_and_preserves_labels(
        self,
    ) -> None:
        atlas_module = self.load_atlas_subject()

        links = tuple(
            atlas_module.markdown_inline_links(
                "before [visible label](README.md) [[nested]](service/api.py:L7) "
                "[]() [later](CURRENT_ARCHITECTURE.md#components)"
            )
        )
        self.assertEqual(
            [(label, target) for _start, _end, label, target in links],
            [
                ("visible label", "README.md"),
                ("[nested]", "service/api.py:L7"),
                ("later", "CURRENT_ARCHITECTURE.md#components"),
            ],
        )
        self.assertFalse(atlas_module.substantive_markdown_body("[](README.md)"))
        self.assertTrue(
            atlas_module.substantive_markdown_body(
                "[Visible architecture detail](README.md)"
            )
        )
        self.assertTrue(
            atlas_module.substantive_markdown_body(
                "![Architecture diagram detail](architecture.png)"
            )
        )
        for non_substantive_link in (
            r"[\]](architecture/reference/material.md)",
            r"![\]](architecture/reference/material.png)",
            "[x [y]](architecture/reference/material.md)",
            "[](<architecture)reference/material.md>)",
            '[](README.md "architecture) reference material")',
            "[](   architecture/reference/material.md )",
            "[`[`](architecture/reference/material.md)",
            "[`]`](architecture/reference/material.md)",
            "[`](architecture/reference/material.md)",
            "[``](architecture/reference/material.md)",
            "[<!-- [ -->](architecture/reference/material.md)",
            "[<?x [ ?>](architecture/reference/material.md)",
            '[<span title="[">x</span>](architecture/reference/material.md)',
            '[<span title=">[">x</span>](architecture/reference/material.md)',
        ):
            with self.subTest(non_substantive_link=non_substantive_link):
                self.assertFalse(
                    atlas_module.substantive_markdown_body(
                        non_substantive_link
                    )
                )
        invalid_blank_line_link = '[](README.md\n\n"../outside.py:L1")'
        self.assertIn(
            "../outside.py:L1",
            atlas_module.markdown_source_reference_tokens(invalid_blank_line_link),
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(invalid_blank_line_link),
            {"../outside.py:L1"},
        )
        invalid_blank_title_link = '[](README.md "foo\n\n../outside.py:L1")'
        self.assertIn(
            "../outside.py:L1",
            atlas_module.markdown_source_reference_tokens(invalid_blank_title_link),
        )
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(invalid_blank_title_link),
            {"../outside.py:L1"},
        )
        for invalid_control_link in (
            '[](README\x1f "../outside.py:L1")',
            '[](README\x7f "../outside.py:L1")',
        ):
            with self.subTest(invalid_control_link=invalid_control_link):
                self.assertIn(
                    "../outside.py:L1",
                    atlas_module.markdown_source_reference_tokens(
                        invalid_control_link
                    ),
                )
                self.assertEqual(
                    atlas_module.unsafe_markdown_source_references(
                        invalid_control_link
                    ),
                    {"../outside.py:L1"},
                )
        links_after_invalid_target = tuple(
            atlas_module.markdown_inline_links(
                "[broken](bad target) [later](missing/file.md)"
            )
        )
        self.assertEqual(
            [(label, target) for _start, _end, label, target in links_after_invalid_target],
            [("later", "missing/file.md")],
        )
        self.assertEqual(
            tuple(atlas_module.markdown_inline_links("`[x](missing/file.md)`")),
            (),
        )
        self.assertEqual(
            [
                (label, target)
                for _start, _end, label, target in atlas_module.markdown_inline_links(
                    "` prefix [later](missing/file.md)"
                )
            ],
            [("later", "missing/file.md")],
        )
        self.assertFalse(
            atlas_module.substantive_markdown_body(
                "`[](architecture/reference/material.md)`"
            )
        )
        nested_link = "[outer [inner](../outside.py:L1)](README.md)"
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(nested_link),
            {"../outside.py:L1"},
        )
        nested_image = "[outer ![alt](../outside.png)](README.md)"
        self.assertEqual(
            atlas_module.unsafe_markdown_source_references(nested_image),
            {"../outside.png"},
        )
        self.assertFalse(
            atlas_module.substantive_markdown_body(
                "[![](architecture/reference/material.png)](README.md)"
            )
        )

        class CountingText(str):
            reads = 0

            def __getitem__(self, key):
                type(self).reads += 1
                return super().__getitem__(key)

        unmatched = CountingText("[" * 32_768)
        self.assertEqual(tuple(atlas_module.markdown_inline_links(unmatched)), ())
        self.assertLessEqual(
            unmatched.reads,
            len(unmatched) + 1,
            "unmatched link labels must be scanned once, not retried per '['",
        )
        CountingText.reads = 0
        unmatched_backticks = CountingText("`" * 32_768)
        self.assertEqual(
            tuple(atlas_module.markdown_inline_links(unmatched_backticks)),
            (),
        )
        self.assertLessEqual(
            unmatched_backticks.reads,
            (len(unmatched_backticks) * 3) + 1,
            "unmatched code span markers must not be rescanned per '`'",
        )
        distinct_backticks = CountingText("a".join("`" * index for index in range(1, 257)))
        self.assertEqual(tuple(atlas_module.markdown_inline_links(distinct_backticks)), ())
        self.assertLessEqual(
            distinct_backticks.reads,
            (len(distinct_backticks) * 5) + 1,
            "distinct unmatched code span runs must not rescan later runs",
        )
        CountingText.reads = 0
        unmatched_targets = CountingText("[x](" * 8192)
        self.assertEqual(
            tuple(atlas_module.markdown_inline_links(unmatched_targets)),
            (),
        )
        self.assertLessEqual(
            unmatched_targets.reads,
            (len(unmatched_targets) * 2) + 1,
            "failed link targets must not rescan the suffix per candidate",
        )
        html_probe = "[" + ("<" * 32_768) + ">](README.md)"
        self.assertEqual(
            [
                target
                for _start, _end, _label, target in atlas_module.markdown_inline_links(
                    html_probe
                )
            ],
            ["README.md"],
        )

    def test_repository_local_host_executables_are_rejected(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas repository local tool ") as temp_dir:
            project = Path(temp_dir) / "project"
            tool_directory = project / "bin"
            tool_directory.mkdir(parents=True)
            canary = Path(temp_dir) / "executed"
            fake = tool_directory / "git"
            fake.write_text(
                "#!/bin/sh\n"
                f"touch {str(canary)!r}\n"
                "printf 'true\\n'\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            original_which = atlas_module.shutil.which
            atlas_module.shutil.which = lambda _name: str(fake)
            try:
                with self.assertRaises(atlas_module.AtlasError) as raised:
                    atlas_module.git_ignore_executable(project)
            finally:
                atlas_module.shutil.which = original_which
            self.assertIn("unsafe", str(raised.exception).lower())
            self.assertFalse(canary.exists(), "repository-local executable was launched")

    def test_enclosing_repository_host_executables_are_rejected(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas enclosing repository tool ") as temp_dir:
            repository = Path(temp_dir) / "repository"
            project = repository / "packages" / "service"
            tool_directory = repository / "bin"
            (repository / ".git").mkdir(parents=True)
            project.mkdir(parents=True)
            tool_directory.mkdir()

            for name in ("git", "rg"):
                with self.subTest(name=name):
                    canary = Path(temp_dir) / f"{name}-executed"
                    fake = tool_directory / name
                    fake.write_text(
                        "#!/bin/sh\n"
                        f"touch {str(canary)!r}\n"
                        "printf 'true\\n'\n",
                        encoding="utf-8",
                    )
                    fake.chmod(0o755)
                    original_which = atlas_module.shutil.which
                    atlas_module.shutil.which = lambda _name, candidate=fake: str(candidate)
                    try:
                        with self.assertRaises(atlas_module.AtlasError) as raised:
                            if name == "git":
                                atlas_module.git_ignore_executable(project)
                            else:
                                atlas_module.trusted_host_executable(
                                    name,
                                    prohibited_roots=(project,),
                                )
                    finally:
                        atlas_module.shutil.which = original_which
                    self.assertIn("unsafe", str(raised.exception).lower())
                    self.assertFalse(
                        canary.exists(),
                        "executable supplied by the enclosing repository was launched",
                    )

    def test_group_writable_host_executables_are_rejected(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas group writable host tool ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            tool_directory = root / "host-tools" / "bin"
            project.mkdir()
            tool_directory.mkdir(parents=True)

            for name in ("git", "rg"):
                with self.subTest(name=name):
                    canary = root / f"{name}-executed"
                    fake = tool_directory / name
                    fake.write_text(
                        "#!/bin/sh\n"
                        f": > {str(canary)!r}\n"
                        "exit 0\n",
                        encoding="utf-8",
                    )
                    fake.chmod(0o775)
                    with mock.patch.object(
                        atlas_module.shutil,
                        "which",
                        return_value=str(fake),
                    ):
                        with self.assertRaises(atlas_module.AtlasError) as raised:
                            atlas_module.trusted_host_executable(
                                name,
                                prohibited_roots=(project,),
                            )
                    self.assertIn("unsafe", str(raised.exception).lower())
                    self.assertFalse(
                        canary.exists(),
                        "group-writable host executable was launched",
                    )

    def test_group_writable_host_executable_ancestors_are_rejected(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(
            prefix="atlas group writable host ancestor "
        ) as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            tool_parent = root / "host-tools"
            tool_directory = tool_parent / "bin"
            project.mkdir()
            tool_directory.mkdir(parents=True)
            tool_parent.chmod(0o770)
            fake = tool_directory / "git"
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)

            with mock.patch.object(
                atlas_module.shutil,
                "which",
                return_value=str(fake),
            ):
                with self.assertRaises(atlas_module.AtlasError) as raised:
                    atlas_module.trusted_host_executable(
                        "git",
                        prohibited_roots=(project,),
                    )
            self.assertIn("unsafe", str(raised.exception).lower())

    def test_homebrew_cellar_group_writable_exception_is_narrow(self) -> None:
        atlas_module = self.load_atlas_subject()
        trusted_owners = {0, 501}
        process_groups = {20}
        metadata = mock.Mock(st_mode=stat.S_IFDIR | 0o775, st_uid=501, st_gid=20)

        self.assertTrue(
            atlas_module.is_trusted_homebrew_cellar_directory(
                Path("/opt/homebrew/Cellar"),
                metadata,
                trusted_owners,
                process_groups,
            )
        )
        self.assertFalse(
            atlas_module.is_trusted_homebrew_cellar_directory(
                Path("/opt/homebrew/bin"),
                metadata,
                trusted_owners,
                process_groups,
            )
        )

    def test_host_executable_owner_must_be_root_or_effective_user(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas wrong owner host tool ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            tool_directory = root / "host-tools" / "bin"
            project.mkdir()
            tool_directory.mkdir(parents=True)
            fake = tool_directory / "git"
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            executable = fake.resolve(strict=True)
            original_stat = atlas_module.Path.stat
            effective_uid = os.geteuid() if hasattr(os, "geteuid") else 0
            wrong_uid = 1
            while wrong_uid in {0, effective_uid}:
                wrong_uid += 1

            def controlled_stat(path: Path, *args: object, **kwargs: object) -> object:
                metadata = original_stat(path, *args, **kwargs)
                if path == executable:
                    return mock.Mock(st_mode=metadata.st_mode, st_uid=wrong_uid)
                return metadata

            with (
                mock.patch.object(atlas_module.shutil, "which", return_value=str(fake)),
                mock.patch.object(atlas_module.Path, "stat", new=controlled_stat),
            ):
                with self.assertRaises(atlas_module.AtlasError) as raised:
                    atlas_module.trusted_host_executable(
                        "git",
                        prohibited_roots=(project,),
                    )
            self.assertIn("unsafe", str(raised.exception).lower())

    def test_host_executable_without_geteuid_trusts_only_root_owner(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas portable owner host tool ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            tool_directory = root / "host-tools" / "bin"
            project.mkdir()
            tool_directory.mkdir(parents=True)
            fake = tool_directory / "git"
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            executable = fake.resolve(strict=True)
            original_stat = atlas_module.Path.stat

            for file_uid, accepted in ((0, True), (1, False)):
                with self.subTest(file_uid=file_uid):
                    def controlled_stat(
                        path: Path,
                        *args: object,
                        **kwargs: object,
                    ) -> object:
                        metadata = original_stat(path, *args, **kwargs)
                        if path == executable:
                            return mock.Mock(st_mode=metadata.st_mode, st_uid=file_uid)
                        return metadata

                    with (
                        mock.patch.object(atlas_module.shutil, "which", return_value=str(fake)),
                        mock.patch.object(atlas_module.Path, "stat", new=controlled_stat),
                        mock.patch.object(atlas_module.os, "geteuid", None, create=True),
                    ):
                        if accepted:
                            resolved = atlas_module.trusted_host_executable(
                                "git",
                                prohibited_roots=(project,),
                            )
                            self.assertEqual(resolved, str(executable))
                        else:
                            with self.assertRaises(atlas_module.AtlasError):
                                atlas_module.trusted_host_executable(
                                    "git",
                                    prohibited_roots=(project,),
                                )

    def test_unrelated_repository_host_executables_are_accepted(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas unrelated repository tool ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            tool_repository = root / "host-tools"
            tool_directory = tool_repository / "bin"
            project.mkdir()
            (tool_repository / ".git").mkdir(parents=True)
            tool_directory.mkdir()

            for name in ("git", "rg"):
                with self.subTest(name=name):
                    fake = tool_directory / name
                    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    fake.chmod(0o755)
                    original_which = atlas_module.shutil.which
                    atlas_module.shutil.which = lambda _name, candidate=fake: str(candidate)
                    try:
                        executable = atlas_module.trusted_host_executable(
                            name,
                            prohibited_roots=(project,),
                        )
                    finally:
                        atlas_module.shutil.which = original_which
                    self.assertEqual(executable, str(fake.resolve(strict=True)))

    def test_nested_tool_repository_does_not_mask_an_enclosing_repository(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas nested repository tool ") as temp_dir:
            repository = Path(temp_dir) / "repository"
            project = repository / "packages" / "service"
            tool_repository = repository / "vendor" / "host-tools"
            tool_directory = tool_repository / "bin"
            (repository / ".git").mkdir(parents=True)
            project.mkdir(parents=True)
            (tool_repository / ".git").mkdir(parents=True)
            tool_directory.mkdir()

            for name in ("git", "rg"):
                with self.subTest(name=name):
                    fake = tool_directory / name
                    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    fake.chmod(0o755)
                    original_which = atlas_module.shutil.which
                    atlas_module.shutil.which = lambda _name, candidate=fake: str(candidate)
                    try:
                        with self.assertRaises(atlas_module.AtlasError) as raised:
                            atlas_module.trusted_host_executable(
                                name,
                                prohibited_roots=(project,),
                            )
                    finally:
                        atlas_module.shutil.which = original_which
                    self.assertIn("unsafe", str(raised.exception).lower())

    def test_case_alias_cannot_bypass_project_local_executable_rejection(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas case alias tool ") as temp_dir:
            root = Path(temp_dir)
            project = root / "ProjectRoot"
            tool_directory = project / "Bin"
            tool_directory.mkdir(parents=True)
            fake = tool_directory / "git"
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            alternate = root / "projectroot" / "bin" / "git"
            try:
                same_file = os.path.samefile(fake, alternate)
            except (FileNotFoundError, OSError):
                self.skipTest("filesystem is case-sensitive")
            if not same_file:
                self.skipTest("filesystem is case-sensitive")

            original_which = atlas_module.shutil.which
            atlas_module.shutil.which = lambda _name: str(alternate)
            try:
                with self.assertRaises(atlas_module.AtlasError) as raised:
                    atlas_module.git_ignore_executable(project)
            finally:
                atlas_module.shutil.which = original_which
            self.assertIn("unsafe", str(raised.exception).lower())

    def load_atlas_subject(self):
        spec = importlib.util.spec_from_file_location("atlas_security_subject", ATLAS_SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)  # type: ignore[union-attr]
        atlas_module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[spec.name] = atlas_module  # type: ignore[union-attr]
        self.addCleanup(sys.modules.pop, spec.name, None)  # type: ignore[union-attr]
        spec.loader.exec_module(atlas_module)  # type: ignore[union-attr]
        return atlas_module

    def test_safe_inventory_descriptor_read_rejects_a_post_inventory_symlink_swap(self) -> None:
        spec = importlib.util.spec_from_file_location("atlas_security_subject", ATLAS_SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)  # type: ignore[union-attr]
        atlas_module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[spec.name] = atlas_module  # type: ignore[union-attr]
        self.addCleanup(sys.modules.pop, spec.name, None)  # type: ignore[union-attr]
        spec.loader.exec_module(atlas_module)  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory(prefix="atlas descriptor swap ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            source = project / "source.py"
            source.write_text("print('safe')\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)
            outside = root / "outside.txt"
            outside.write_text("must not be read\n", encoding="utf-8")
            source.unlink()
            source.symlink_to(outside)

            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.read_inventory_bytes(
                    inventory, atlas_module.PurePosixPath("source.py")
                )

    def test_safe_source_and_artifact_reads_reject_hardlinks_before_and_after_io(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas hardlink identity ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            source = project / "source.py"
            source.write_text("print('safe')\n", encoding="utf-8")
            source_alias = project / "source-alias.py"
            os.link(source, source_alias)
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.build_safe_inventory(project)
            source_alias.unlink()

            inventory = atlas_module.build_safe_inventory(project)
            late_alias = root / "late-source-link.py"
            original_read = atlas_module.os.read
            linked = False

            def link_during_read(descriptor, count):
                nonlocal linked
                chunk = original_read(descriptor, count)
                if chunk and not linked:
                    os.link(source, late_alias)
                    linked = True
                return chunk

            atlas_module.os.read = link_during_read
            self.addCleanup(setattr, atlas_module.os, "read", original_read)
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.read_inventory_bytes(
                    inventory, atlas_module.PurePosixPath("source.py")
                )
            atlas_module.os.read = original_read
            late_alias.unlink()

            linked = False
            atlas_module.os.read = link_during_read
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.hash_inventory_file(
                    inventory, atlas_module.PurePosixPath("source.py")
                )
            atlas_module.os.read = original_read
            late_alias.unlink()

            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "QUICK", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            artifact = atlas / "PROJECT_ATLAS.md"
            artifact_alias = root / "artifact-alias.md"
            os.link(artifact, artifact_alias)
            artifacts = atlas_module.build_artifact_inventory(atlas)
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.read_artifact_bytes(
                    artifacts, atlas_module.PurePosixPath("PROJECT_ATLAS.md")
                )

    def test_atlas_artifact_reader_rejects_post_check_symlink_swap_without_path_leak(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas artifact descriptor swap ") as temp_dir:
            root = Path(temp_dir)
            atlas = root / "atlas"
            atlas.mkdir()
            marker = atlas / "ATLAS_INDEX.md"
            marker.write_text("# Project Atlas\n\nMode: **STANDARD**\n", encoding="utf-8")
            artifacts = atlas_module.build_artifact_inventory(atlas)
            relative = atlas_module.PurePosixPath("ATLAS_INDEX.md")
            self.assertEqual(atlas_module.artifact_state(artifacts, relative), "regular")
            outside = root / "outside-index"
            outside.write_text("EXTERNAL_ARTIFACT_MUST_NOT_BE_READ\n", encoding="utf-8")
            marker.unlink()
            marker.symlink_to(outside)

            with self.assertRaises(atlas_module.AtlasError) as raised:
                atlas_module.read_artifact_bytes(artifacts, relative)
            diagnostic = atlas_module.sanitize_diagnostic(str(raised.exception))
            self.assertNotIn(str(root), diagnostic)
            self.assertNotIn("EXTERNAL_ARTIFACT_MUST_NOT_BE_READ", diagnostic)

    def test_write_json_rejects_parent_swap_before_descriptor_open(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas output parent swap ") as temp_dir:
            root = Path(temp_dir)
            parent = root / "output-parent"
            parent.mkdir()
            anchored = root / "original-parent"
            outside = root / "outside"
            outside.mkdir()
            output = parent / "inventory.json"
            original_open_directory = atlas_module.open_directory_descriptor
            swapped = False

            def swap_then_open(path):
                nonlocal swapped
                if Path(path).name == parent.name and not swapped:
                    parent.rename(anchored)
                    parent.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return original_open_directory(path)

            atlas_module.open_directory_descriptor = swap_then_open
            self.addCleanup(
                setattr,
                atlas_module,
                "open_directory_descriptor",
                original_open_directory,
            )
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.write_json(output, {"safe": True})
            self.assertFalse((outside / "inventory.json").exists())
            self.assertFalse((anchored / "inventory.json").exists())

    def test_write_json_restores_identity_when_target_changes_before_commit(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas json commit race ") as temp_dir:
            root = Path(temp_dir)
            target = root / "snapshot.json"
            displaced = root / "original-snapshot.json"
            target.write_text('{"owner":"original"}\n', encoding="utf-8")
            original_fsync = atlas_module.os.fsync
            raced = False

            def race_after_temp_flush(descriptor):
                nonlocal raced
                status = os.fstat(descriptor)
                if stat.S_ISREG(status.st_mode) and not raced:
                    target.rename(displaced)
                    target.write_text('{"owner":"concurrent"}\n', encoding="utf-8")
                    raced = True
                return original_fsync(descriptor)

            atlas_module.os.fsync = race_after_temp_flush
            self.addCleanup(setattr, atlas_module.os, "fsync", original_fsync)
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.write_json(target, {"owner": "atlas"})
            self.assertEqual(target.read_text(encoding="utf-8"), '{"owner":"concurrent"}\n')
            self.assertEqual(displaced.read_text(encoding="utf-8"), '{"owner":"original"}\n')
            self.assertFalse(any("atlas-new" in path.name for path in root.iterdir()))

    def test_write_json_no_clobber_preserves_target_created_during_commit(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas json no clobber race ") as temp_dir:
            root = Path(temp_dir)
            target = root / "snapshot.json"
            original_fsync = atlas_module.os.fsync
            raced = False

            def create_target_after_temp_flush(descriptor):
                nonlocal raced
                status = os.fstat(descriptor)
                if stat.S_ISREG(status.st_mode) and not raced:
                    target.write_text('{"owner":"concurrent"}\n', encoding="utf-8")
                    raced = True
                return original_fsync(descriptor)

            atlas_module.os.fsync = create_target_after_temp_flush
            self.addCleanup(setattr, atlas_module.os, "fsync", original_fsync)
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.write_json(target, {"owner": "atlas"})
            self.assertEqual(target.read_text(encoding="utf-8"), '{"owner":"concurrent"}\n')
            self.assertFalse(any("atlas-new" in path.name for path in root.iterdir()))

    def test_init_rejects_output_parent_swap_before_descriptor_open(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas init parent swap ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            (project / "runtime.py").write_text("print('safe')\n", encoding="utf-8")
            parent = root / "atlas-output-parent"
            parent.mkdir()
            anchored = root / "original-output-parent"
            outside = root / "outside"
            outside.mkdir()
            output = parent / "atlas"
            original_open_directory = atlas_module.open_directory_descriptor
            swapped = False

            def swap_then_open(path):
                nonlocal swapped
                if Path(path).name == parent.name and not swapped:
                    parent.rename(anchored)
                    parent.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return original_open_directory(path)

            atlas_module.open_directory_descriptor = swap_then_open
            self.addCleanup(
                setattr,
                atlas_module,
                "open_directory_descriptor",
                original_open_directory,
            )
            arguments = atlas_module.argparse.Namespace(
                project=project,
                project_path=None,
                mode="QUICK",
                output=output,
            )
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.init_command(arguments)
            self.assertFalse((outside / "atlas" / "PROJECT_ATLAS.md").exists())
            self.assertFalse((anchored / "atlas" / "PROJECT_ATLAS.md").exists())

    def test_nested_ignore_rules_exclude_content_and_risk_signals(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas nested ignore ") as temp_dir:
            project = Path(temp_dir) / "project"
            shutil.copytree(FIXTURES_ROOT / "quick_cli", project)
            nested = project / "nested"
            nested.mkdir()
            (nested / ".gitignore").write_text("ignored.yaml\n", encoding="utf-8")
            (nested / "ignored.yaml").write_text(
                "production-critical financial settlement\nNESTED_IGNORE_CANARY\n",
                encoding="utf-8",
            )
            output = Path(temp_dir) / "inventory.json"
            result = run_atlas("inventory", "--project", project, "--output", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = load_json(self, output)
            serialized = output.read_text(encoding="utf-8")
            self.assertEqual(payload["mode"], "QUICK")
            self.assertNotIn("ignored.yaml", serialized)
            self.assertNotIn("NESTED_IGNORE_CANARY", serialized)

    def test_ignore_file_escaped_leading_markers_are_literal_patterns(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas escaped ignore markers ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            (project / ".ignore").write_text(
                "\\!excluded.txt\n\\#commented.txt\n",
                encoding="utf-8",
            )
            (project / "!excluded.txt").write_text(
                "ESCAPED_BANG_IGNORE_CANARY\n",
                encoding="utf-8",
            )
            (project / "#commented.txt").write_text(
                "ESCAPED_HASH_IGNORE_CANARY\n",
                encoding="utf-8",
            )
            (project / "included.txt").write_text("public\n", encoding="utf-8")

            inventory = atlas_module.build_safe_inventory(project)

            self.assertIn(atlas_module.PurePosixPath("included.txt"), inventory.members)
            self.assertNotIn(atlas_module.PurePosixPath("!excluded.txt"), inventory.members)
            self.assertNotIn(atlas_module.PurePosixPath("#commented.txt"), inventory.members)

    def test_ignore_negation_reincludes_paths_and_preserves_last_match_order(self) -> None:
        atlas_module = self.load_atlas_subject()
        for ignore_name in (".gitignore", ".ignore"):
            with self.subTest(ignore_name=ignore_name), tempfile.TemporaryDirectory(
                prefix="atlas ignore negation "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                project.mkdir()
                (project / ignore_name).write_text(
                    "*.txt\n!included.txt\n!excluded-again.txt\nexcluded-again.txt\n",
                    encoding="utf-8",
                )
                (project / "included.txt").write_text("public\n", encoding="utf-8")
                (project / "excluded.txt").write_text("private\n", encoding="utf-8")
                (project / "excluded-again.txt").write_text("private\n", encoding="utf-8")

                inventory = atlas_module.build_safe_inventory(project)

                self.assertIn(atlas_module.PurePosixPath("included.txt"), inventory.members)
                self.assertNotIn(atlas_module.PurePosixPath("excluded.txt"), inventory.members)
                self.assertNotIn(
                    atlas_module.PurePosixPath("excluded-again.txt"), inventory.members
                )

    def test_ignore_globs_match_git_component_anchoring_and_double_star_semantics(self) -> None:
        atlas_module = self.load_atlas_subject()
        rules = (
            "*.txt\n"
            "!public/*.txt\n"
            "*.cfg\n"
            "!one/?.cfg\n"
            "*.data\n"
            "!public/**/allowed.data\n"
            "/anchored.log\n"
        )
        relative_paths = (
            "public/direct.txt",
            "public/deep/nested.txt",
            "one/a.cfg",
            "one/deep/a.cfg",
            "public/allowed.data",
            "public/deep/allowed.data",
            "anchored.log",
            "nested/anchored.log",
        )
        with tempfile.TemporaryDirectory(prefix="atlas git ignore oracle ") as temp_dir:
            root = Path(temp_dir)
            oracle = root / "oracle"
            oracle.mkdir()
            initialized = run_command(["git", "init", "--quiet"], cwd=oracle)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (oracle / ".gitignore").write_text(rules, encoding="utf-8")
            local = root / "local"
            local.mkdir()
            (local / ".ignore").write_text(rules, encoding="utf-8")
            for relative in relative_paths:
                for project in (oracle, local):
                    target = project / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("bounded fixture\n", encoding="utf-8")

            inventory = atlas_module.build_safe_inventory(local)
            for relative in relative_paths:
                oracle_result = run_command(
                    ["git", "check-ignore", "--no-index", "--quiet", "--", relative],
                    cwd=oracle,
                )
                self.assertIn(oracle_result.returncode, {0, 1}, oracle_result.stderr)
                expected_ignored = oracle_result.returncode == 0
                actual_ignored = atlas_module.PurePosixPath(relative) not in inventory.members
                with self.subTest(relative=relative):
                    self.assertEqual(actual_ignored, expected_ignored)

    def test_ignore_backslash_and_space_semantics_match_git_oracle(self) -> None:
        atlas_module = self.load_atlas_subject()
        rules = (
            "escaped\\ file.txt\n"
            "star\\*.txt\n"
            "question\\?.txt\n"
            "bracket\\[.txt\n"
            " leading.txt\n"
            "trimmed.txt   \n"
            "kept.txt\\ \n"
            "space\\ dir/*.py\n"
            "normal[0-9].txt\n"
        )
        relative_paths = (
            "escaped file.txt",
            "escapedXfile.txt",
            "star*.txt",
            "starX.txt",
            "question?.txt",
            "questionA.txt",
            "bracket[.txt",
            "bracketA.txt",
            " leading.txt",
            "leading.txt",
            "trimmed.txt",
            "trimmed.txt   ",
            "kept.txt ",
            "kept.txt",
            "space dir/matched.py",
            "spaceXdir/matched.py",
            "normal7.txt",
            "normalx.txt",
        )
        with tempfile.TemporaryDirectory(prefix="atlas escaped ignore oracle ") as temp_dir:
            root = Path(temp_dir)
            oracle = root / "oracle"
            oracle.mkdir()
            initialized = run_command(["git", "init", "--quiet"], cwd=oracle)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (oracle / ".gitignore").write_text(rules, encoding="utf-8")
            local = root / "local"
            local.mkdir()
            (local / ".ignore").write_text(rules, encoding="utf-8")
            for relative in relative_paths:
                for project in (oracle, local):
                    target = project / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("bounded fixture\n", encoding="utf-8")

            inventory = atlas_module.build_safe_inventory(local)
            for relative in relative_paths:
                oracle_result = run_command(
                    ["git", "check-ignore", "--no-index", "--quiet", "--", relative],
                    cwd=oracle,
                )
                self.assertIn(oracle_result.returncode, {0, 1}, oracle_result.stderr)
                expected_ignored = oracle_result.returncode == 0
                actual_ignored = atlas_module.PurePosixPath(relative) not in inventory.members
                with self.subTest(relative=relative):
                    self.assertEqual(actual_ignored, expected_ignored)

    def test_custom_ignore_fails_closed_on_unsupported_escape_syntax(self) -> None:
        atlas_module = self.load_atlas_subject()
        invalid_patterns = ("dangling\\\n", "class[\\*].txt\n", "escaped\\/slash.txt\n")
        for pattern in invalid_patterns:
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory(
                prefix="atlas invalid ignore escape "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                project.mkdir()
                (project / ".ignore").write_text(pattern, encoding="utf-8")
                (project / "harmless.txt").write_text(
                    "bounded fixture\n", encoding="utf-8"
                )

                with self.assertRaises(atlas_module.AtlasError):
                    atlas_module.build_safe_inventory(project)

    def test_oversized_ignore_metadata_fails_closed_without_disclosing_content(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas oversized ignore ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            marker = "OVERSIZED_IGNORE_PRIVATE_CANARY"
            (project / ".ignore").write_text(marker * 8, encoding="utf-8")
            atlas_module.MAX_IGNORE_FILE_BYTES = 32

            with self.assertRaises(atlas_module.AtlasError) as raised:
                atlas_module.build_safe_inventory(project)

            diagnostic = str(raised.exception)
            self.assertNotIn(marker, diagnostic)
            self.assertNotIn(str(project), diagnostic)

    def test_external_git_metadata_and_configs_are_not_inventory_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas git excludes ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            initialized = run_command(["git", "init", "--quiet"], cwd=project)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (project / ".git" / "info" / "exclude").write_text(
                "local-private.py\n", encoding="utf-8"
            )
            global_excludes = root / "global-excludes"
            global_excludes.write_text("global-private.py\n", encoding="utf-8")
            global_config = root / "git-config"
            global_config.write_text(
                f"[core]\n\texcludesFile = {global_excludes.as_posix()}\n",
                encoding="utf-8",
            )
            (project / ".gitignore").write_text(
                "in-tree-private.py\n",
                encoding="utf-8",
            )
            declaration = (
                '"""This production service handles financial payments.\n\n'
                "The service automatically makes approval decisions. "
                'Operators can override them.\n"""\n'
            )
            (project / "local-private.py").write_text(
                declaration + "LOCAL_EXCLUDE_CANARY = True\n", encoding="utf-8"
            )
            (project / "global-private.py").write_text(
                declaration + "GLOBAL_EXCLUDE_CANARY = True\n", encoding="utf-8"
            )
            (project / "in-tree-private.py").write_text(
                declaration + "IN_TREE_IGNORE_CANARY = True\n", encoding="utf-8"
            )
            output = root / "inventory.json"
            result = run_command(
                [
                    sys.executable,
                    ATLAS_SCRIPT,
                    "inventory",
                    "--project",
                    project,
                    "--output",
                    output,
                ],
                env={
                    "GIT_CONFIG_GLOBAL": os.fspath(global_config),
                    "GIT_CONFIG_NOSYSTEM": "1",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = load_json(self, output)
            serialized = output.read_text(encoding="utf-8")
            self.assertEqual(payload["mode"], "FORENSIC")
            self.assertIn("local-private.py", payload["files"])
            self.assertIn("global-private.py", payload["files"])
            self.assertNotIn("in-tree-private.py", payload["files"])
            self.assertNotIn("LOCAL_EXCLUDE_CANARY", serialized)
            self.assertNotIn("GLOBAL_EXCLUDE_CANARY", serialized)
            self.assertNotIn("IN_TREE_IGNORE_CANARY", serialized)

    def test_git_ignore_queries_do_not_use_unbounded_subprocess_run(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas bounded git queries ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            initialized = run_command(["git", "init", "--quiet"], cwd=project)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (project / ".gitignore").write_text(
                "*.txt\n!included.txt\n", encoding="utf-8"
            )
            (project / "ignored.txt").write_text("private\n", encoding="utf-8")
            (project / "included.txt").write_text("public\n", encoding="utf-8")

            original_run = atlas_module.subprocess.run

            def reject_unbounded_run(*_args, **_kwargs):
                raise AssertionError("Git queries must not use subprocess.run with PIPE")

            atlas_module.subprocess.run = reject_unbounded_run
            try:
                executable = atlas_module.git_ignore_executable(project)
                self.assertIsNotNone(executable)
                ignored = atlas_module.git_ignored_paths(
                    project,
                    executable,
                    (
                        atlas_module.PurePosixPath("ignored.txt"),
                        atlas_module.PurePosixPath("included.txt"),
                    ),
                )
                inventory = atlas_module.build_safe_inventory(project)
            finally:
                atlas_module.subprocess.run = original_run

            self.assertEqual(
                ignored,
                frozenset({atlas_module.PurePosixPath("ignored.txt")}),
            )
            self.assertIn(atlas_module.PurePosixPath("included.txt"), inventory.members)
            self.assertNotIn(atlas_module.PurePosixPath("ignored.txt"), inventory.members)

    def test_isolated_git_query_uses_only_copied_in_tree_gitignore(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas tracked git exclusion ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            initialized = run_command(["git", "init", "--quiet"], cwd=project)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            tracked = project / "info-excluded.txt"
            tracked.write_text("bounded fixture\n", encoding="utf-8")
            (project / ".git" / "info" / "exclude").write_text(
                tracked.name + "\n", encoding="utf-8"
            )
            in_tree = project / "in-tree-excluded.txt"
            in_tree.write_text("must not be read\n", encoding="utf-8")
            (project / ".gitignore").write_text(
                in_tree.name + "\n",
                encoding="utf-8",
            )

            git_executable = atlas_module.git_ignore_executable(project)
            self.assertIsNotNone(git_executable)
            original_bounded_process = atlas_module.run_bounded_process
            observed_query: dict[str, object] = {}

            def record_bounded_query(arguments, **kwargs):
                observed_query["arguments"] = tuple(arguments)
                observed_query["input_bytes"] = kwargs.get("input_bytes")
                observed_query["stdout_limit"] = kwargs.get("stdout_limit")
                observed_query["environment"] = kwargs.get("environment")
                observed_query["cwd"] = kwargs.get("cwd")
                return original_bounded_process(arguments, **kwargs)

            atlas_module.run_bounded_process = record_bounded_query
            try:
                ignored = atlas_module.git_ignored_paths(
                    project,
                    git_executable,
                    (
                        atlas_module.PurePosixPath(tracked.name),
                        atlas_module.PurePosixPath(in_tree.name),
                    ),
                )
            finally:
                atlas_module.run_bounded_process = original_bounded_process

            encoded = (
                tracked.name.encode("utf-8")
                + b"\0"
                + in_tree.name.encode("utf-8")
                + b"\0"
            )
            self.assertEqual(
                ignored, frozenset({atlas_module.PurePosixPath(in_tree.name)})
            )
            self.assertIn("--no-index", observed_query["arguments"])
            self.assertNotIn(str(project), "\n".join(observed_query["arguments"]))
            self.assertEqual(observed_query["input_bytes"], encoded)
            self.assertEqual(observed_query["stdout_limit"], len(encoded))
            environment = observed_query["environment"]
            self.assertIsInstance(environment, dict)
            self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertEqual(environment["GIT_CONFIG_SYSTEM"], os.devnull)
            self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertNotEqual(Path(observed_query["cwd"]), project)

            original_read = atlas_module._read_relative_bytes

            def reject_excluded_read(root, relative, **kwargs):
                if relative == atlas_module.PurePosixPath(in_tree.name):
                    raise AssertionError("in-tree ignored file content must not be read")
                return original_read(root, relative, **kwargs)

            atlas_module._read_relative_bytes = reject_excluded_read
            try:
                inventory = atlas_module.build_safe_inventory(project)
            finally:
                atlas_module._read_relative_bytes = original_read

            self.assertIn(atlas_module.PurePosixPath(tracked.name), inventory.members)
            self.assertNotIn(atlas_module.PurePosixPath(in_tree.name), inventory.members)

    def test_source_gitdir_file_is_not_followed_or_added_to_inventory(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas external gitdir ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            external_gitdir = root / "external.git"
            (external_gitdir / "info").mkdir(parents=True)
            (external_gitdir / "HEAD").write_text(
                "ref: refs/heads/main\n",
                encoding="utf-8",
            )
            (external_gitdir / "config").write_text(
                "[include]\n\tpath = ../outside-config\n",
                encoding="utf-8",
            )
            (external_gitdir / "info" / "exclude").write_text(
                "candidate.py\n",
                encoding="utf-8",
            )
            (root / "outside-config").write_text(
                "[core]\n\texcludesFile = ../outside-excludes\n",
                encoding="utf-8",
            )
            (root / "outside-excludes").write_text("candidate.py\n", encoding="utf-8")
            (project / ".git").write_text(
                f"gitdir: {external_gitdir}\n",
                encoding="utf-8",
            )
            (project / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")

            inventory = atlas_module.build_safe_inventory(project)

            self.assertIn(atlas_module.PurePosixPath("candidate.py"), inventory.members)
            self.assertNotIn(atlas_module.PurePosixPath(".git"), inventory.members)

    @unittest.skipUnless(os.name == "posix", "process-group containment requires POSIX")
    def test_successful_git_query_terminates_same_group_descendants(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas residual git child ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            pid_file = root / "child.pid"
            fake = root / "forking-git"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "import time\n"
                "from pathlib import Path\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    for descriptor in (0, 1, 2):\n"
                "        try:\n"
                "            os.close(descriptor)\n"
                "        except OSError:\n"
                "            pass\n"
                "    time.sleep(30)\n"
                "    os._exit(0)\n"
                f"Path({str(pid_file)!r}).write_text(str(child), encoding='utf-8')\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            child_pid = None
            try:
                ignored = atlas_module.git_ignored_paths(
                    project,
                    str(fake.resolve()),
                    (atlas_module.PurePosixPath("candidate.txt"),),
                )
                self.assertEqual(ignored, frozenset())
                child_pid = int(pid_file.read_text(encoding="utf-8"))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("successful Git query left a same-group child process alive")
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_git_ignore_queries_bound_hostile_output_and_timeout(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas hostile git query ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()

            def fake_git(name: str, body: str) -> Path:
                executable = root / name
                executable.write_text(
                    "#!/usr/bin/env python3\nimport os\nimport time\n" + body,
                    encoding="utf-8",
                )
                executable.chmod(0o755)
                return executable

            atlas_module.MAX_GIT_CHECK_IGNORE_STDOUT_BYTES = 32
            atlas_module.MAX_GIT_STDERR_BYTES = 32
            atlas_module.GIT_CHECK_IGNORE_SECONDS = 0.3

            noisy_ignore = fake_git(
                "noisy-check-ignore-git",
                "os.write(1, b'candidate.txt\\0' * 64)\n",
            )
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.git_ignored_paths(
                    project,
                    str(noisy_ignore),
                    (atlas_module.PurePosixPath("candidate.txt"),),
                )

            noisy_ignore_stderr = fake_git(
                "noisy-check-ignore-stderr-git",
                "os.write(2, b'x' * 1024)\n",
            )
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.git_ignored_paths(
                    project,
                    str(noisy_ignore_stderr),
                    (atlas_module.PurePosixPath("candidate.txt"),),
                )

            slow_ignore = fake_git(
                "slow-check-ignore-git",
                "import subprocess\n"
                "import sys\n"
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'])\n",
            )
            started = time.monotonic()
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.git_ignored_paths(
                    project,
                    str(slow_ignore),
                    (atlas_module.PurePosixPath("candidate.txt"),),
                )
            self.assertLess(time.monotonic() - started, 0.75)

    def test_structural_classification_has_a_repository_wide_read_budget(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas classification budget ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            for index in range(5):
                marker = "production-critical financial settlement" if index == 4 else "safe"
                (project / f"module_{index}.py").write_text(
                    f"# {marker}\n", encoding="utf-8"
                )
            atlas_module.MAX_CLASSIFICATION_FILES = 2
            atlas_module.MAX_CLASSIFICATION_TOTAL_BYTES = 1024
            payload = atlas_module.build_inventory(project)
            self.assertEqual(payload["mode"], "FORENSIC")
            self.assertTrue(payload["classification"]["limited"])
            self.assertEqual(payload["classification"]["files_inspected"], 2)
            self.assertEqual(payload["classification"]["file_budget"], 2)
            self.assertIn("classification budget", " ".join(payload["reasons"]).lower())

    def test_safe_inventory_traversal_fails_closed_at_each_structural_ceiling(self) -> None:
        cases = (
            ("MAX_INVENTORY_FILES", 1, ("one.py", "two.py"), (), "file-count"),
            ("MAX_INVENTORY_DIRECTORIES", 1, (), ("one", "two"), "directory-count"),
            ("MAX_INVENTORY_DEPTH", 1, ("one/two.py",), (), "depth"),
            ("MAX_INVENTORY_PATH_BYTES", 3, ("long-name.py",), (), "path-byte"),
        )
        for constant, limit, files, directories, diagnostic in cases:
            with self.subTest(constant=constant), tempfile.TemporaryDirectory(
                prefix="atlas inventory structural ceiling "
            ) as temp_dir:
                atlas_module = self.load_atlas_subject()
                project = Path(temp_dir) / "project"
                project.mkdir()
                for relative in directories:
                    (project / relative).mkdir(parents=True)
                for relative in files:
                    path = project / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("print('safe')\n", encoding="utf-8")
                setattr(atlas_module, constant, limit)

                with self.assertRaisesRegex(atlas_module.AtlasError, diagnostic):
                    atlas_module.build_safe_inventory(project)

    def test_safe_inventory_stops_streaming_a_flat_directory_at_the_file_limit(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas streaming inventory ceiling ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            consumed = 0

            class FakeEntry:
                def __init__(self, name: str) -> None:
                    self.name = name
                    self.path = os.fspath(project / name)

                def is_dir(self, *, follow_symlinks: bool = True) -> bool:
                    return False

                def is_symlink(self) -> bool:
                    return False

            class FakeScandir:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback) -> None:
                    return None

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal consumed
                    if consumed >= 10:
                        raise StopIteration
                    consumed += 1
                    return FakeEntry(f"source-{consumed}.py")

                def close(self) -> None:
                    return None

            fake_scandir = FakeScandir()
            original_scandir = atlas_module.os.scandir
            atlas_module.os.scandir = lambda _path: fake_scandir
            atlas_module.MAX_INVENTORY_FILES = 2
            try:
                with self.assertRaisesRegex(atlas_module.AtlasError, "file-count"):
                    atlas_module.build_safe_inventory(project)
            finally:
                atlas_module.os.scandir = original_scandir

            self.assertEqual(consumed, 3, "traversal must stop before consuming the flat directory")

    def test_inventory_stdout_uses_the_same_bounded_json_serializer_as_file_output(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas bounded inventory stdout ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            (project / "runtime.py").write_text("print('safe')\n", encoding="utf-8")
            atlas_module.MAX_JSON_OUTPUT_BYTES = 32
            arguments = atlas_module.argparse.Namespace(
                project=project,
                project_path=None,
                output=None,
            )
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                atlas_module.AtlasError, "JSON output exceeds the byte limit"
            ):
                atlas_module.inventory_command(arguments)

    def test_replay_directory_targets_require_at_least_one_safe_inventory_member(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas empty replay target ") as temp_dir:
            project = Path(temp_dir) / "project"
            safe = project / "safe"
            safe.mkdir(parents=True)
            (safe / "source.py").write_text("print('safe')\n", encoding="utf-8")
            (project / "empty").mkdir()
            excluded = project / "excluded-only" / "__pycache__"
            excluded.mkdir(parents=True)
            (excluded / "private.pyc").write_bytes(b"PRIVATE-IGNORED-CONTENT")
            inventory = atlas_module.build_safe_inventory(project)
            members = set(inventory.members)

            self.assertTrue(
                atlas_module.replay_target_is_safe(project, project, "safe", members)
            )
            self.assertFalse(
                atlas_module.replay_target_is_safe(project, project, "empty", members)
            )
            self.assertFalse(
                atlas_module.replay_target_is_safe(
                    project, project, "excluded-only", members
                )
            )

    def test_replay_plan_requires_path_sort_for_directory_and_multiple_targets(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas deterministic replay plan ") as temp_dir:
            project = Path(temp_dir) / "project"
            source_dir = project / "src"
            source_dir.mkdir(parents=True)
            (source_dir / "first.py").write_text("needle = 1\n", encoding="utf-8")
            (project / "first.py").write_text("needle = 1\n", encoding="utf-8")
            (project / "second.py").write_text("needle = 2\n", encoding="utf-8")
            inventory = atlas_module.build_safe_inventory(project)

            def plan(source_ref):
                return atlas_module.replay_command_plan(
                    {
                        "source_ref": source_ref,
                        "notes": "cwd=.; exit=0; stdout_sha256=" + "0" * 64,
                    },
                    2,
                    inventory,
                )

            invalid_commands = (
                "rg --no-config --files src",
                "rg --no-config needle first.py second.py",
                "rg --no-config --sort modified --files src",
                "rg --no-config --sortr path --files src",
                "rg --no-config --sort=path --files src",
            )
            for command in invalid_commands:
                with self.subTest(command=command):
                    replay_plan, errors = plan(command)
                    self.assertIsNone(replay_plan)
                    self.assertTrue(any("--sort path" in error for error in errors))

            sorted_directory, directory_errors = plan(
                "rg --no-config --sort path --files src"
            )
            self.assertIsNotNone(sorted_directory, directory_errors)
            self.assertEqual(directory_errors, [])
            single_file, file_errors = plan("rg --no-config --files first.py")
            self.assertIsNotNone(single_file, file_errors)
            self.assertEqual(file_errors, [])

    def test_replay_target_safety_does_not_enumerate_excluded_siblings(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas replay no second traversal ") as temp_dir:
            project = Path(temp_dir) / "project"
            target = project / "source"
            target.mkdir(parents=True)
            (target / "runtime.py").write_text("print('safe')\n", encoding="utf-8")
            ignored = target / "__pycache__"
            ignored.mkdir()
            for index in range(10):
                (ignored / f"private-{index}.pyc").write_bytes(b"PRIVATE")
            inventory = atlas_module.build_safe_inventory(project)
            original_walk = atlas_module.os.walk

            def forbidden_walk(*_args, **_kwargs):
                raise AssertionError("replay safety must derive membership from SafeInventory")

            atlas_module.os.walk = forbidden_walk
            try:
                self.assertTrue(
                    atlas_module.replay_target_is_safe(
                        project,
                        project,
                        "source",
                        set(inventory.members),
                    )
                )
            finally:
                atlas_module.os.walk = original_walk

    def test_host_path_scan_is_generic_but_preserves_documented_api_routes(self) -> None:
        atlas_module = self.load_atlas_subject()
        self.assertTrue(
            atlas_module.contains_local_absolute_path(
                "/custom-mount/team/repository/config.yaml"
            )
        )
        self.assertTrue(
            atlas_module.contains_local_absolute_path(
                "endpoint: /" + "Users/alice/private/repository"
            )
        )
        self.assertTrue(
            atlas_module.contains_local_absolute_path(
                "GET /" + "home/alice/secret.txt"
            )
        )
        self.assertFalse(atlas_module.contains_local_absolute_path("GET /api/v1/parcels"))
        self.assertFalse(atlas_module.contains_local_absolute_path("stderr to /dev/null"))

    def test_symlink_ancestors_block_writes_and_atlas_reads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas symlink boundary ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            shutil.copytree(FIXTURES_ROOT / "quick_cli", project)
            outside = root / "outside"
            outside.mkdir()
            link = root / "link"
            link.symlink_to(outside, target_is_directory=True)

            inventory = run_atlas(
                "inventory", "--project", project, "--output", link / "inventory.json"
            )
            self.assertNotEqual(inventory.returncode, 0)
            self.assertFalse((outside / "inventory.json").exists())

            initialized = run_atlas(
                "init", "--project", project, "--mode", "QUICK", "--output", link / "atlas"
            )
            self.assertNotEqual(initialized.returncode, 0)
            self.assertFalse((outside / "atlas" / "PROJECT_ATLAS.md").exists())

            real_atlas = outside / "real-atlas"
            created = run_atlas(
                "init", "--project", project, "--mode", "QUICK", "--output", real_atlas
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            validated = run_atlas("validate", "--atlas", link / "real-atlas", "--mode", "QUICK")
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn("symbolic", f"{validated.stdout}\n{validated.stderr}".lower())

    def test_validate_rejects_escaping_source_ref_and_file_link(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas escaping refs ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init",
                "--project",
                FIXTURES_ROOT / "forensic_legacy",
                "--mode",
                "FORENSIC",
                "--output",
                atlas,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "F-1\tCONFIRMED\tescaping source\tFILE\t../../outside.py:1\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            architecture = atlas / "CURRENT_ARCHITECTURE.md"
            architecture.write_text(
                architecture.read_text(encoding="utf-8") + "\n[host file](file:///etc/passwd)\n",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate",
                "--atlas",
                atlas,
                "--project",
                FIXTURES_ROOT / "forensic_legacy",
                "--mode",
                "FORENSIC",
            )
            self.assertNotEqual(result.returncode, 0)
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
            self.assertRegex(diagnostic, r"outside|escape|absolute")
            self.assertIn("file", diagnostic)

    def test_validate_rejects_mixed_mode_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas mixed mode ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init",
                "--project",
                FIXTURES_ROOT / "forensic_legacy",
                "--mode",
                "FORENSIC",
                "--output",
                atlas,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            quick = run_atlas(
                "init",
                "--project",
                FIXTURES_ROOT / "quick_cli",
                "--mode",
                "QUICK",
                "--output",
                atlas,
            )
            self.assertEqual(quick.returncode, 0, quick.stderr)
            result = run_atlas("validate", "--atlas", atlas)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mixed", f"{result.stdout}\n{result.stderr}".lower())

    def test_validate_rejects_external_kind_with_local_absolute_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas external local path ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init",
                "--project",
                FIXTURES_ROOT / "forensic_legacy",
                "--mode",
                "FORENSIC",
                "--output",
                atlas,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            local_absolute_source = "/" + "Users/example/private.txt"
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "F-1\tCONFIRMED\tlocal private file\tEXTERNAL\t"
                + local_absolute_source
                + "\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate",
                "--atlas",
                atlas,
                "--project",
                FIXTURES_ROOT / "forensic_legacy",
                "--mode",
                "FORENSIC",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside", f"{result.stdout}\n{result.stderr}".lower())

    def test_snapshot_resolves_line_prefixed_project_source(self) -> None:
        project = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas line reference ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            source_ref = "legacy_system/gateway.py"
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + f"F-1\tCONFIRMED\tgateway entry\tFILE\t{source_ref}:L1\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            snapshot = Path(temp_dir) / "snapshot.json"
            result = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = load_json(self, snapshot)
            entries = {entry["path"]: entry["sha256"] for entry in payload["files"]}
            expected = hashlib.sha256((project / source_ref).read_bytes()).hexdigest()
            self.assertEqual(entries.get(source_ref), expected)

    def test_snapshot_includes_bare_root_dotfile_reference(self) -> None:
        project = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas root dotfile reference ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "F-1\tCONFIRMED\texclusion policy\tCONFIG\t.gitignore:L1\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            snapshot = Path(temp_dir) / "snapshot.json"
            result = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            entries = {entry["path"]: entry["sha256"] for entry in load_json(self, snapshot)["files"]}
            expected = hashlib.sha256((project / ".gitignore").read_bytes()).hexdigest()
            self.assertEqual(entries.get(".gitignore"), expected)

    def test_snapshot_fails_closed_on_malformed_traceability(self) -> None:
        project = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas malformed snapshot ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                "bad\theader\nlegacy_system/gateway.py\n", encoding="utf-8"
            )
            snapshot = Path(temp_dir) / "snapshot.json"
            result = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(snapshot.exists())
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
            self.assertIn("traceability", diagnostic)
            self.assertNotIn(str(project).lower(), diagnostic)

    def test_project_local_file_reference_cannot_be_mislabeled_as_external(self) -> None:
        project = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas typed snapshot ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "F-1\tCONFIRMED\texternal note\tEXTERNAL\tlegacy_system/authority.py:L1-L4\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            architecture = atlas / "CURRENT_ARCHITECTURE.md"
            architecture.write_text(
                architecture.read_text(encoding="utf-8")
                + "\nMarkdown-only token `legacy_system/gateway.py:L1`.\n",
                encoding="utf-8",
            )
            validated = run_atlas(
                "validate",
                "--atlas",
                atlas,
                "--project",
                project,
                "--mode",
                "FORENSIC",
                "--draft",
            )
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn(
                "file-like source_type",
                f"{validated.stdout}\n{validated.stderr}".lower(),
            )
            snapshot = Path(temp_dir) / "snapshot.json"
            result = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(snapshot.exists())

    def test_snapshot_hashes_only_explicit_file_and_command_targets(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas bounded snapshot population ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            for name in ("referenced.py", "unreferenced.py", "also_unreferenced.py"):
                (project / name).write_text(f"# {name}\n", encoding="utf-8")
            command_target = project / "command-target"
            command_target.mkdir()
            (command_target / "first.py").write_text("FIRST = True\n", encoding="utf-8")
            (command_target / "second.py").write_text("SECOND = True\n", encoding="utf-8")
            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "EV-1\tCONFIRMED\tReferenced source\tFILE\treferenced.py:L1\t"
                + "2026-07-22\tACTIVE\t-\t\n"
                + "EV-2\tCONFIRMED\tBounded command source\tCOMMAND\t"
                + "rg --no-config --sort path --files command-target\t2026-07-22\tACTIVE\t-\t"
                + "cwd=.; exit=0; stdout_sha256="
                + ("0" * 64)
                + "\n",
                encoding="utf-8",
            )

            artifacts = atlas_module.build_artifact_inventory(atlas)
            inventory = atlas_module.build_safe_inventory(project)
            rows = atlas_module.read_traceability_rows(artifacts)
            observed: list[str] = []
            original_hash = atlas_module.hash_inventory_file

            def recording_hash(safe_inventory, relative):
                observed.append(relative.as_posix())
                return original_hash(safe_inventory, relative)

            atlas_module.hash_inventory_file = recording_hash
            self.addCleanup(setattr, atlas_module, "hash_inventory_file", original_hash)
            payload = atlas_module.build_source_snapshot_payload(artifacts, inventory, rows)

            self.assertEqual(
                observed,
                [
                    "command-target/first.py",
                    "command-target/second.py",
                    "referenced.py",
                ],
            )
            self.assertEqual(payload["safe_inventory"]["member_count"], 5)
            self.assertRegex(payload["safe_inventory"]["path_manifest_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(payload["evidence_scope"]["unique_evidence_files"], 3)
            self.assertEqual(payload["evidence_scope"]["hashed_files"], 3)

    def test_snapshot_validation_rejects_extra_unreferenced_safe_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas exact snapshot population ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            (project / "referenced.py").write_text("print('referenced')\n", encoding="utf-8")
            unreferenced = project / "unreferenced.py"
            unreferenced.write_text("print('unreferenced')\n", encoding="utf-8")
            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "EV-1\tCONFIRMED\tReferenced source\tFILE\treferenced.py:L1\t"
                + "2026-07-22\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            snapshot = atlas / "SOURCE_SNAPSHOT.json"
            snapshotted = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
            payload = load_json(self, snapshot)
            payload["files"].append(
                {
                    "path": "unreferenced.py",
                    "sha256": hashlib.sha256(unreferenced.read_bytes()).hexdigest(),
                }
            )
            snapshot.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

            validated = run_atlas(
                "validate",
                "--atlas",
                atlas,
                "--project",
                project,
                "--mode",
                "FORENSIC",
                "--draft",
            )
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn("exact active evidence source population", f"{validated.stdout}\n{validated.stderr}".lower())

    def test_snapshot_validation_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas strict snapshot json ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            (project / "referenced.py").write_text("print('referenced')\n", encoding="utf-8")
            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "EV-1\tCONFIRMED\tReferenced source\tFILE\treferenced.py:L1\t"
                + "2026-07-22\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            snapshot = atlas / "SOURCE_SNAPSHOT.json"
            snapshotted = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
            serialized = snapshot.read_text(encoding="utf-8")
            marker = '"schema_version": "0.2"'
            self.assertIn(marker, serialized)
            snapshot.write_text(
                serialized.replace(marker, f"{marker},\n  {marker}", 1), encoding="utf-8"
            )

            validated = run_atlas(
                "validate",
                "--atlas",
                atlas,
                "--project",
                project,
                "--mode",
                "FORENSIC",
                "--draft",
            )
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn("strict json", f"{validated.stdout}\n{validated.stderr}".lower())

    def test_snapshot_validation_detects_safe_path_manifest_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas stale path manifest ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            (project / "referenced.py").write_text("print('referenced')\n", encoding="utf-8")
            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "EV-1\tCONFIRMED\tReferenced source\tFILE\treferenced.py:L1\t"
                + "2026-07-22\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            snapshot = atlas / "SOURCE_SNAPSHOT.json"
            snapshotted = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
            (project / "unreferenced-new.py").write_text(
                "print('unreferenced')\n", encoding="utf-8"
            )

            validated = run_atlas(
                "validate",
                "--atlas",
                atlas,
                "--project",
                project,
                "--mode",
                "FORENSIC",
                "--draft",
            )
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn("path manifest is stale", f"{validated.stdout}\n{validated.stderr}".lower())

    def test_project_source_reference_must_belong_to_safe_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas allowlist source ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            (project / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
            (project / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")
            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "F-1\tCONFIRMED\tignored source\tFILE\tignored.py:L1\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )

            validated = run_atlas(
                "validate", "--atlas", atlas, "--project", project, "--mode", "FORENSIC"
            )
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn("safe inventory", f"{validated.stdout}\n{validated.stderr}".lower())
            snapshot = root / "snapshot.json"
            snapshotted = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertNotEqual(snapshotted.returncode, 0)
            self.assertFalse(snapshot.exists())

    def test_source_reference_line_validation_has_a_bounded_read_limit(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas bounded source reference ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            oversized = project / "oversized.py"
            with oversized.open("wb") as stream:
                stream.truncate(atlas_module.MAX_EVIDENCE_SOURCE_BYTES + 1)
            inventory = atlas_module.build_safe_inventory(project)

            atlas = root / "atlas"
            atlas.mkdir()
            (atlas / "PROJECT_ATLAS.md").write_text(
                "# Project Atlas\n\nEvidence: `oversized.py:L1`\n",
                encoding="utf-8",
            )
            artifacts = atlas_module.build_artifact_inventory(atlas)

            errors = atlas_module.validate_project_source_references(
                artifacts,
                inventory,
                ("PROJECT_ATLAS.md",),
            )

            self.assertTrue(errors)
            self.assertIn("non-text project source lines", "\n".join(errors))

    def test_validate_rejects_portable_host_paths_and_secret_material_without_echoing_them(self) -> None:
        fixture = FIXTURES_ROOT / "standard_service"
        path_samples = (
            "/opt/acme/private/project.yaml",
            "D:\\Work\\private\\project.yaml",
            "\\\\fileserver\\share\\private.yaml",
        )
        secret = "gh" + "p_" + "A" * 32
        for hostile_value in (*path_samples, secret):
            with self.subTest(value_type="secret" if hostile_value == secret else "path"), tempfile.TemporaryDirectory(
                prefix="atlas artifact scan "
            ) as temp_dir:
                atlas = Path(temp_dir) / "atlas"
                initialized = run_atlas(
                    "init", "--project", fixture, "--mode", "STANDARD", "--output", atlas
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                artifact = atlas / "CURRENT_ARCHITECTURE.md"
                artifact.write_text(
                    artifact.read_text(encoding="utf-8") + f"\nEvidence: {hostile_value}\n",
                    encoding="utf-8",
                )
                result = run_atlas(
                    "validate", "--atlas", atlas, "--project", fixture, "--mode", "STANDARD"
                )
                self.assertNotEqual(result.returncode, 0)
                diagnostic = f"{result.stdout}\n{result.stderr}"
                self.assertNotIn(hostile_value, diagnostic)
                expected = "secret material" if hostile_value == secret else "local absolute path"
                self.assertIn(expected, diagnostic.lower())

    def test_leakage_scanner_decodes_file_uris_and_redacts_extended_credentials(self) -> None:
        atlas_module = self.load_atlas_subject()
        encoded_file_uri = "file" + "%3A%2F%2F%2F" + "Users%2Fperson%2Fprivate.txt"
        credential_url = "https://" + "operator:" + "S" * 24 + "@example.invalid/private"
        authorization = "Author" + "ization: Bearer " + "T" * 40
        jwt = "eyJ" + "A" * 20 + "." + "B" * 20 + "." + "C" * 20
        private_key = "-----BEGIN " + "PGP PRIVATE KEY BLOCK-----"
        password_assignment = "pass" + 'word = "correct horse battery staple"'

        self.assertTrue(atlas_module.contains_local_absolute_path(encoded_file_uri))
        for secret in (credential_url, authorization, jwt, private_key, password_assignment):
            with self.subTest(kind=secret.split(" ", 1)[0]):
                self.assertTrue(atlas_module.contains_secret_material(secret))
                self.assertNotIn(secret, atlas_module.sanitize_diagnostic(secret))

        benign = (
            "Use the Authorization header with Bearer authentication.",
            "JWT validation is performed by the provider.",
            "The file URI scheme is rejected at the boundary.",
            "https://example.invalid/public/path",
        )
        for value in benign:
            with self.subTest(benign=value):
                self.assertFalse(atlas_module.contains_secret_material(value))
                self.assertFalse(atlas_module.contains_local_absolute_path(value))

    def test_completion_and_replay_enforce_small_configured_resource_limits(self) -> None:
        atlas_module = self.load_atlas_subject()
        with tempfile.TemporaryDirectory(prefix="atlas bounded resources ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            source_dir = project / "src"
            source_dir.mkdir(parents=True)
            (source_dir / "source.py").write_text("print('bounded')\n", encoding="utf-8")
            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)

            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "EV-1\tCONFIRMED\tOne\tFILE\tsrc/source.py:L1\t2026-07-22\tACTIVE\t-\t\n"
                + "EV-2\tCONFIRMED\tTwo\tFILE\tsrc/source.py:L1\t2026-07-22\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            artifacts = atlas_module.build_artifact_inventory(atlas)
            atlas_module.MAX_TRACEABILITY_ROWS = 1
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.read_traceability_rows(artifacts)

            inventory = atlas_module.build_safe_inventory(project)
            atlas_module.MAX_REPLAY_FILE_BYTES = 4
            mirror = root / "mirror"
            mirror.mkdir()
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.build_replay_mirror(
                    inventory, atlas_module.PurePosixPath("."), ["src"], mirror
                )

            atlas_module.MAX_REPLAY_FILE_BYTES = 1024
            atlas_module.MAX_REPLAY_TOTAL_BYTES = 4
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.build_replay_mirror(
                    inventory, atlas_module.PurePosixPath("."), ["src"], mirror
                )

            atlas_module.MAX_EVIDENCE_SOURCE_BYTES = 4
            with self.assertRaises(atlas_module.AtlasError):
                atlas_module.hash_inventory_file(
                    inventory, atlas_module.PurePosixPath("src/source.py")
                )

            atlas_module.MAX_EVIDENCE_SOURCE_BYTES = 32
            growing = source_dir / "growing.py"
            growing.write_bytes(b"a" * 16)
            growing_inventory = atlas_module.build_safe_inventory(project)
            original_read = atlas_module.os.read
            expanded = False

            def grow_during_hash(descriptor, count):
                nonlocal expanded
                chunk = original_read(descriptor, count)
                if chunk and not expanded:
                    with growing.open("ab") as stream:
                        stream.write(b"b" * 64)
                    expanded = True
                return chunk

            atlas_module.os.read = grow_during_hash
            self.addCleanup(setattr, atlas_module.os, "read", original_read)
            with self.assertRaisesRegex(atlas_module.AtlasError, "hash limit"):
                atlas_module.hash_inventory_file(
                    growing_inventory, atlas_module.PurePosixPath("src/growing.py")
                )
            atlas_module.os.read = original_read

            command = ["rg", "--no-config", "--files", "--sort", "path", "src"]
            observed = run_command(command, cwd=project)
            self.assertEqual(observed.returncode, 0, observed.stderr)
            record = {
                "source_ref": "rg --no-config --files --sort path src",
                "notes": "cwd=.; exit=0; stdout_sha256="
                + hashlib.sha256(observed.stdout.encode("utf-8")).hexdigest(),
            }
            atlas_module.MAX_REPLAY_FILE_BYTES = 1024
            atlas_module.MAX_REPLAY_TOTAL_BYTES = 1024
            atlas_module.MAX_REPLAY_STDOUT_BYTES = 2
            replay_errors = atlas_module.replay_command_evidence(record, 2, inventory)
            self.assertTrue(any("stdout" in error.lower() and "limit" in error.lower() for error in replay_errors))

            atlas_module.MAX_ARTIFACT_BYTES = 16
            arguments = atlas_module.argparse.Namespace(
                atlas=atlas,
                project=project,
                mode="FORENSIC",
                draft=True,
                replay_command_evidence=False,
            )
            self.assertEqual(atlas_module.validate_command(arguments), 1)

            atlas_module.MAX_ARTIFACT_BYTES = 1024 * 1024
            atlas_module.MAX_ATLAS_TOTAL_BYTES = 32
            self.assertEqual(atlas_module.validate_command(arguments), 1)

    def test_snapshot_validation_rejects_excessive_json_depth(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas snapshot json depth ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            (project / "source.py").write_text("print('safe')\n", encoding="utf-8")
            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "EV-1\tCONFIRMED\tSafe source\tFILE\tsource.py:L1\t"
                + "2026-07-22\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            snapshot = atlas / "SOURCE_SNAPSHOT.json"
            snapshotted = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertEqual(snapshotted.returncode, 0, snapshotted.stderr)
            nested: object = "leaf"
            for _ in range(12):
                nested = {"nested": nested}
            snapshot.write_text(json.dumps(nested), encoding="utf-8")
            validated = run_atlas(
                "validate",
                "--atlas",
                atlas,
                "--project",
                project,
                "--mode",
                "FORENSIC",
                "--draft",
            )
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn("json depth limit", f"{validated.stdout}\n{validated.stderr}".lower())

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required for command replay")
    def test_replay_ignores_generated_children_without_reading_them(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas ignored replay child ") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            shutil.copytree(
                FIXTURES_ROOT / "forensic_legacy",
                project,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            command = ["rg", "--no-config", "--files", "--sort", "path", "legacy_system"]
            observed = run_command(command, cwd=project)
            self.assertEqual(observed.returncode, 0, observed.stderr)
            digest = hashlib.sha256(observed.stdout.encode("utf-8")).hexdigest()
            ignored = project / "legacy_system" / "__pycache__"
            ignored.mkdir()
            (ignored / "private.pyc").write_bytes(b"IGNORED-CONTENT-MUST-NOT-BE-READ")

            atlas = root / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "EV-CMD\tCONFIRMED\tSafe files enumerated\tCOMMAND\t"
                + "rg --no-config --files --sort path legacy_system\t2026-07-22\tACTIVE\t-\t"
                + f"cwd=.; exit=0; stdout_sha256={digest}\n",
                encoding="utf-8",
            )
            validated = run_atlas(
                "validate",
                "--atlas",
                atlas,
                "--project",
                project,
                "--mode",
                "FORENSIC",
                "--draft",
                "--replay-command-evidence",
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_validate_rejects_non_reproducible_command_evidence(self) -> None:
        project = FIXTURES_ROOT / "forensic_legacy"
        with tempfile.TemporaryDirectory(prefix="atlas invalid command evidence ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + "F-1\tCONFIRMED\tall writers found\tCOMMAND\trg -n --glob *.py write legacy_system\t2026-07-21\tACTIVE\t-\tcommand was run\n",
                encoding="utf-8",
            )
            result = run_atlas(
                "validate", "--atlas", atlas, "--project", project, "--mode", "FORENSIC"
            )
            self.assertNotEqual(result.returncode, 0)
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
            self.assertIn("command", diagnostic)
            self.assertRegex(diagnostic, r"glob|stdout_sha256|exit=")

    def test_explicit_mode_without_project_retains_risk_signals(self) -> None:
        result = run_atlas(
            "select-mode", "--mode", "QUICK", "--critical", "--financial-data"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "QUICK")
        self.assertEqual(payload["recommended_mode"], "FORENSIC")
        self.assertTrue(payload["signals"]["critical"])
        self.assertIn("coverage_warning", payload)

    def test_snapshot_project_source_cannot_be_shadowed_by_atlas_file(self) -> None:
        project = FIXTURES_ROOT / "quick_cli"
        with tempfile.TemporaryDirectory(prefix="atlas snapshot shadow ") as temp_dir:
            atlas = Path(temp_dir) / "atlas"
            initialized = run_atlas(
                "init", "--project", project, "--mode", "FORENSIC", "--output", atlas
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            source_ref = "quick_cli/runtime.py"
            shadow = atlas / source_ref
            shadow.parent.mkdir(parents=True)
            shadow.write_text("atlas shadow\n", encoding="utf-8")
            (atlas / "TRACEABILITY.tsv").write_text(
                TRACE_HEADER
                + f"F-1\tCONFIRMED\tstate writer\tFILE\t{source_ref}:1\t2026-07-21\tACTIVE\t-\t\n",
                encoding="utf-8",
            )
            snapshot = Path(temp_dir) / "snapshot.json"
            result = run_atlas(
                "snapshot", "--atlas", atlas, "--project", project, "--output", snapshot
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            entries = {entry["path"]: entry["sha256"] for entry in load_json(self, snapshot)["files"]}
            expected = hashlib.sha256((project / source_ref).read_bytes()).hexdigest()
            self.assertEqual(entries.get(source_ref), expected)

    def test_legitimate_generated_basename_remains_inventory_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas basename evidence ") as temp_dir:
            project = Path(temp_dir) / "project"
            evidence = project / "docs" / "CURRENT_ARCHITECTURE.md"
            evidence.parent.mkdir(parents=True)
            evidence.write_text(
                "# Current Architecture\n\nA production-critical financial settlement service.\n",
                encoding="utf-8",
            )
            output = Path(temp_dir) / "inventory.json"
            result = run_atlas("inventory", "--project", project, "--output", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = load_json(self, output)
            self.assertIn("docs/CURRENT_ARCHITECTURE.md", payload["files"])
            self.assertEqual(payload["mode"], "QUICK")
            self.assertFalse(payload["signals"]["critical"])
            self.assertFalse(payload["signals"]["financial_data"])

    def test_detect_mode_rejects_symlinked_index_before_reading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas symlink artifact ") as temp_dir:
            root = Path(temp_dir)
            atlas = root / "atlas"
            atlas.mkdir()
            outside = root / "outside-index"
            outside.write_bytes(b"\xff\xfe\x00")
            (atlas / "ATLAS_INDEX.md").symlink_to(outside)
            result = run_atlas("validate", "--atlas", atlas)
            self.assertNotEqual(result.returncode, 0)
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
            self.assertIn("symbolic", diagnostic)
            self.assertNotIn("utf-8", diagnostic)
            self.assertNotIn(str(root).lower(), diagnostic)


if __name__ == "__main__":
    unittest.main()
