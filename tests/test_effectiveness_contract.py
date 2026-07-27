from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.support import REPO_ROOT, run_command


BENCHMARK_SCRIPT = REPO_ROOT / "scripts" / "benchmark_atlas.py"
MODELLED_INPUT = REPO_ROOT / "benchmarks" / "data" / "modelled" / "v0.1.0.json"
MODELLED_DERIVED = (
    REPO_ROOT / "benchmarks" / "data" / "derived" / "modelled-v0.1.0.json"
)
EXTERNAL_EVIDENCE = REPO_ROOT / "benchmarks" / "EXTERNAL_EVIDENCE.md"


def oracle_item(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def oracle_manifest_hash(items: list[str]) -> str:
    payload = json.dumps(
        {"expected_items": sorted(items)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256((payload + "\n").encode("utf-8")).hexdigest()


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location("benchmark_atlas", BENCHMARK_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load benchmark helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EffectivenessContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.benchmark = load_benchmark_module()

    def test_modelled_scenarios_are_reproducible_and_labelled(self) -> None:
        result = run_command(
            [
                "python3",
                BENCHMARK_SCRIPT,
                "model",
                "--input",
                MODELLED_INPUT,
                "--check",
                MODELLED_DERIVED,
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        derived = json.loads(MODELLED_DERIVED.read_text(encoding="utf-8"))
        self.assertEqual(derived["classification"], "MODELLED_ASSUMPTION")
        scenarios = {item["id"]: item for item in derived["scenarios"]}
        self.assertNotIn("typical", scenarios)
        self.assertEqual(
            scenarios["illustrative_mid"]["tokens"]["break_even_tasks"],
            6,
        )
        self.assertEqual(
            scenarios["illustrative_mid"]["time"]["break_even_tasks"],
            9,
        )
        illustrative_mid_ten = next(
            item
            for item in scenarios["illustrative_mid"]["tokens"]["horizons"]
            if item["horizon_tasks"] == 10
        )
        self.assertEqual(illustrative_mid_ten["net_saving"], 130000)
        self.assertEqual(
            illustrative_mid_ten["net_saving_percent_of_baseline"],
            18.6,
        )
        self.assertEqual(
            scenarios["illustrative_mid"]["quality"][
                "contract_pass_delta_points"
            ],
            6.0,
        )
        self.assertEqual(
            scenarios["illustrative_mid"]["quality"][
                "contract_pass_relative_improvement_percent"
            ],
            8.6,
        )

    def test_modelled_decimal_costs_have_exact_break_even_and_horizons(self) -> None:
        payload = json.loads(MODELLED_INPUT.read_text(encoding="utf-8"))
        payload["horizons"] = [3]
        scenario = payload["scenarios"][0]
        scenario.update(
            {
                "atlas_build_minutes": 0.3,
                "atlas_build_tokens": 0.3,
                "atlas_use_minutes_per_task": 0.2,
                "atlas_use_tokens_per_task": 0.2,
                "baseline_minutes_per_task": 0.3,
                "baseline_tokens_per_task": 0.3,
                "refresh_minutes_per_task": 0,
                "refresh_tokens_per_task": 0,
            }
        )

        with tempfile.TemporaryDirectory() as raw_temp:
            input_path = Path(raw_temp) / "decimal-model.json"
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.benchmark.modelled_summary(
                self.benchmark.read_json_file(input_path)
            )
        output = next(
            item for item in result["scenarios"] if item["id"] == "pessimistic"
        )

        for metric, saving_field in (
            ("time", "per_task_saving_minutes"),
            ("tokens", "per_task_saving_tokens"),
        ):
            with self.subTest(metric=metric):
                self.assertEqual(output[metric]["break_even_tasks"], 3)
                self.assertEqual(output[metric][saving_field], 0.1)
                self.assertEqual(output[metric]["horizons"][0]["net_saving"], 0)

    def test_russian_homepages_define_formula_domains_and_alignment_states(
        self,
    ) -> None:
        for path in (
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "README.ru.md",
        ):
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                for marker in (
                    "`UNAVAILABLE`",
                    "`SKIPPED`",
                    "`STOP_USER`",
                    "`UNKNOWN:<stable-id>`",
                    "нулевой или отрицательной экономии",
                    "в этой модели окупаемость не достигается",
                    "при нулевом baseline",
                    "а не бесконечность или выдуманный процент",
                ):
                    self.assertIn(marker, content)
                self.assertNotIn(
                    "Если ответ неизвестен или вопрос пропущен, Atlas записывает "
                    "`UNKNOWN`",
                    content,
                )

        payload = json.loads(MODELLED_INPUT.read_text(encoding="utf-8"))
        scenario = payload["scenarios"][0]
        scenario["baseline_contract_pass_percent"] = 0
        scenario["atlas_contract_pass_percent"] = 0
        scenario["baseline_tokens_per_task"] = (
            scenario["atlas_use_tokens_per_task"]
            + scenario["refresh_tokens_per_task"]
        )
        scenario["baseline_minutes_per_task"] = (
            scenario["atlas_use_minutes_per_task"]
            + scenario["refresh_minutes_per_task"]
        )
        output = self.benchmark.modelled_summary(payload)["scenarios"][0]
        self.assertIsNone(output["tokens"]["break_even_tasks"])
        self.assertIsNone(output["time"]["break_even_tasks"])
        self.assertIsNone(
            output["quality"]["contract_pass_relative_improvement_percent"]
        )

    def test_external_evidence_names_average_tokens_and_oracle_limit(self) -> None:
        evidence = EXTERNAL_EVIDENCE.read_text(encoding="utf-8")
        self.assertIn("| Agent | Average tokens per evaluated task |", evidence)
        self.assertNotIn("| Agent | Total tokens |", evidence)
        self.assertIn("n=376", evidence)
        self.assertIn("v3", evidence)
        self.assertIn("2026-05-06", evidence)
        self.assertIn("not a deployable retrieval result", evidence)
        self.assertIn("upper-bound", evidence)

    def test_modelled_input_cannot_masquerade_as_measured(self) -> None:
        payload = json.loads(MODELLED_INPUT.read_text(encoding="utf-8"))
        payload["classification"] = "MEASURED"
        with self.assertRaisesRegex(ValueError, "MODELLED_ASSUMPTION"):
            self.benchmark.modelled_summary(payload)

    def test_modelled_input_rejects_unknown_root_and_scenario_fields(self) -> None:
        payload = json.loads(MODELLED_INPUT.read_text(encoding="utf-8"))
        payload["projection_method"] = "undocumented"
        with self.assertRaisesRegex(ValueError, "modelled input.*unknown fields"):
            self.benchmark.modelled_summary(payload)

        payload = json.loads(MODELLED_INPUT.read_text(encoding="utf-8"))
        payload["scenarios"][0]["hidden_adjustment"] = 1
        with self.assertRaisesRegex(ValueError, r"scenarios\[0\].*unknown fields"):
            self.benchmark.modelled_summary(payload)

    def test_receipt_rejects_raw_prompt_and_absolute_path(self) -> None:
        receipt = self.valid_receipt("BASELINE", "pair-1", "run-base")
        receipt["prompt"] = "raw prompt must not be stored"
        with self.assertRaisesRegex(ValueError, "forbidden field"):
            self.benchmark.validate_receipt(receipt)

        receipt = self.valid_receipt("BASELINE", "pair-1", "run-base")
        receipt["adapter_version"] = "/" + "Users" + "/example/private"
        with self.assertRaisesRegex(ValueError, "absolute path"):
            self.benchmark.validate_receipt(receipt)

    def test_receipt_rejects_unknown_fields_and_unbounded_oracle_items(self) -> None:
        receipt = self.valid_receipt("BASELINE", "pair-1", "run-base")
        receipt["notes"] = "a raw transcript could otherwise hide here"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.benchmark.validate_receipt(receipt)

        receipt = self.valid_receipt("BASELINE", "pair-1", "run-base")
        receipt["result"]["observed_items"] = ["raw-prompt-must-not-be-stored"]
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.benchmark.validate_receipt(receipt)

        receipt = self.valid_receipt("BASELINE", "pair-1", "run-base")
        receipt["result"]["observed_items"] = [
            "artifact:" + "/" + "Users" + "/example/private.py"
        ]
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.benchmark.validate_receipt(receipt)

    def test_receipt_rejects_baseline_contamination_and_quota_claims(self) -> None:
        receipt = self.valid_receipt("BASELINE", "pair-1", "run-base")
        receipt["input_atlas_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "BASELINE.*map hashes"):
            self.benchmark.validate_receipt(receipt)

        receipt = self.valid_receipt("BASELINE", "pair-1", "run-base")
        receipt["weekly_quota"] = {
            "exact_host_signal": True,
            "remaining_percent": 42,
            "source": "DERIVED_FROM_TOKENS",
        }
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.benchmark.validate_receipt(receipt)

    def test_receipt_requires_and_pair_locks_acceptance_contract_digest(self) -> None:
        for condition in ("BASELINE", "ATLAS_USE"):
            with self.subTest(condition=condition):
                receipt = self.valid_receipt(condition, "pair-1", "run-base")
                del receipt["acceptance_contract_sha256"]
                with self.assertRaisesRegex(
                    ValueError, "acceptance_contract_sha256"
                ):
                    self.benchmark.validate_receipt(receipt)

        build = self.valid_receipt("ATLAS_BUILD", None, "build-1")
        baseline = self.valid_receipt("BASELINE", "pair-1", "base-1")
        atlas = self.valid_receipt(
            "ATLAS_USE", "pair-1", "atlas-1", counterbalance=2
        )
        atlas["acceptance_contract_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            ValueError, "pair pair-1 differs in acceptance_contract_sha256"
        ):
            self.benchmark.measured_summary(
                [
                    self.benchmark.validate_receipt(build),
                    self.benchmark.validate_receipt(baseline),
                    self.benchmark.validate_receipt(atlas),
                ]
            )

    def test_receipt_rejects_wall_time_inconsistent_with_timestamps(self) -> None:
        receipt = self.valid_receipt("BASELINE", "pair-1", "run-base")
        receipt["ended_at"] = "2026-07-27T10:00:00Z"
        receipt["wall_time_ms"] = 100
        with self.assertRaisesRegex(ValueError, "wall_time_ms.*timestamps"):
            self.benchmark.validate_receipt(receipt)

    def test_receipt_rejects_contradictory_status_and_counterbalance_order(
        self,
    ) -> None:
        receipt = self.valid_receipt("ATLAS_USE", "pair-1", "atlas-1")
        receipt["result"]["status"] = "ERROR"
        receipt["result"]["contract_pass"] = True
        with self.assertRaisesRegex(ValueError, "ERROR.*contract_pass"):
            self.benchmark.validate_receipt(receipt)

        build = self.valid_receipt("ATLAS_BUILD", None, "build-1")
        baseline = self.valid_receipt("BASELINE", "pair-1", "base-1")
        atlas = self.valid_receipt(
            "ATLAS_USE", "pair-1", "atlas-1", counterbalance=2
        )
        baseline["started_at"] = "2026-07-26T10:00:04.000Z"
        baseline["ended_at"] = "2026-07-26T10:00:05.000Z"
        atlas["started_at"] = "2026-07-26T10:00:02.000Z"
        atlas["ended_at"] = "2026-07-26T10:00:03.000Z"
        with self.assertRaisesRegex(ValueError, "counterbalance.*timestamps"):
            self.benchmark.measured_summary(
                [
                    self.benchmark.validate_receipt(build),
                    self.benchmark.validate_receipt(baseline),
                    self.benchmark.validate_receipt(atlas),
                ]
            )

    def test_measured_campaign_rejects_oracle_swap_duplicate_task_and_bad_lineage(
        self,
    ) -> None:
        baseline = self.valid_receipt("BASELINE", "pair-1", "base-1")
        atlas = self.valid_receipt(
            "ATLAS_USE", "pair-1", "atlas-1", counterbalance=2
        )
        atlas["oracle_sha256"] = "9" * 64
        with self.assertRaisesRegex(ValueError, "oracle_sha256"):
            self.benchmark.measured_summary(
                [
                    self.benchmark.validate_receipt(baseline),
                    self.benchmark.validate_receipt(atlas),
                ]
            )

        pairs = [
            self.valid_receipt("BASELINE", "pair-1", "base-1"),
            self.valid_receipt(
                "ATLAS_USE", "pair-1", "atlas-1", counterbalance=2
            ),
            self.valid_receipt(
                "BASELINE",
                "pair-2",
                "base-2",
                counterbalance=2,
                task_id="task-b",
            ),
            self.valid_receipt(
                "ATLAS_USE",
                "pair-2",
                "atlas-2",
                counterbalance=1,
                task_id="task-b",
            ),
        ]
        pairs[2]["task_sha256"] = pairs[0]["task_sha256"]
        pairs[3]["task_sha256"] = pairs[1]["task_sha256"]
        with self.assertRaisesRegex(ValueError, "duplicate task identity"):
            self.benchmark.measured_summary(
                [self.benchmark.validate_receipt(item) for item in pairs]
            )

        build = self.valid_receipt("ATLAS_BUILD", None, "build-1")
        baseline = self.valid_receipt("BASELINE", "pair-1", "base-1")
        atlas = self.valid_receipt(
            "ATLAS_USE", "pair-1", "atlas-1", counterbalance=2
        )
        atlas["input_atlas_sha256"] = "9" * 64
        with self.assertRaisesRegex(ValueError, "lineage"):
            self.benchmark.measured_summary(
                [
                    self.benchmark.validate_receipt(build),
                    self.benchmark.validate_receipt(baseline),
                    self.benchmark.validate_receipt(atlas),
                ]
            )

    def test_measured_pairs_compute_structure_tokens_time_and_break_even(self) -> None:
        receipts = [
            self.valid_receipt(
                "ATLAS_BUILD",
                None,
                "build-1",
                wall_time_ms=1000,
                total_tokens=100,
            ),
            self.valid_receipt(
                "BASELINE",
                "pair-1",
                "base-1",
                wall_time_ms=1000,
                total_tokens=100,
                observed=["owner", "entry"],
                dangling_refs=1,
                unclassified_claims=3,
                unsafe_refs=2,
            ),
            self.valid_receipt(
                "ATLAS_USE",
                "pair-1",
                "atlas-1",
                wall_time_ms=600,
                total_tokens=60,
                observed=["owner", "entry", "extra"],
                counterbalance=2,
                unclassified_claims=1,
            ),
            self.valid_receipt(
                "BASELINE",
                "pair-2",
                "base-2",
                task_id="task-b",
                wall_time_ms=1200,
                total_tokens=120,
                observed=["owner"],
                counterbalance=2,
                contract_pass=False,
            ),
            self.valid_receipt(
                "ATLAS_USE",
                "pair-2",
                "atlas-2",
                task_id="task-b",
                wall_time_ms=700,
                total_tokens=70,
                observed=["owner", "entry"],
                counterbalance=1,
            ),
            self.valid_receipt(
                "ATLAS_REFRESH",
                None,
                "refresh-1",
                wall_time_ms=600,
                total_tokens=50,
            ),
        ]
        receipts[2]["result"]["expected_items"].reverse()
        derived = self.benchmark.measured_summary(
            [self.benchmark.validate_receipt(item) for item in receipts]
        )
        self.assertEqual(derived["classification"], "MEASURED")
        self.assertEqual(derived["pairs"]["complete"], 2)
        self.assertEqual(derived["tokens"]["sample_n"], 2)
        self.assertEqual(derived["tokens"]["median_paired_saving"], 45)
        self.assertEqual(derived["tokens"]["amortized_refresh_per_task"], 25)
        self.assertEqual(derived["tokens"]["median_net_saving_after_refresh"], 20)
        self.assertEqual(derived["tokens"]["break_even_tasks"], 5)
        self.assertEqual(derived["time"]["median_paired_saving_ms"], 450)
        self.assertEqual(derived["time"]["amortized_refresh_per_task_ms"], 300)
        self.assertEqual(derived["time"]["median_net_saving_after_refresh_ms"], 150)
        self.assertEqual(derived["time"]["break_even_tasks"], 7)
        self.assertEqual(derived["quality"]["contract_pass_baseline_percent"], 50.0)
        self.assertEqual(derived["quality"]["contract_pass_with_atlas_percent"], 100.0)
        self.assertEqual(derived["quality"]["contract_pass_delta_points"], 50.0)
        self.assertEqual(
            derived["quality"]["contract_pass_relative_improvement_percent"],
            100.0,
        )
        self.assertEqual(
            derived["reference_integrity"]["unsafe_refs"]["baseline_total"],
            2,
        )
        self.assertEqual(
            derived["reference_integrity"]["unsafe_refs"]["with_atlas_total"],
            0,
        )
        self.assertEqual(
            derived["reference_integrity"]["unsafe_refs"]["paired_delta_total"],
            -2,
        )
        self.assertEqual(
            derived["reference_integrity"]["unclassified_claims"][
                "paired_delta_total"
            ],
            -2,
        )
        self.assertEqual(
            derived["formulae"]["contract_pass_rate"],
            "contract_pass_receipts / complete_pairs * 100",
        )
        self.assertEqual(
            derived["formulae"]["reference_integrity_paired_delta_total"],
            "sum(atlas_use_result_metric - baseline_result_metric)",
        )
        self.assertEqual(
            derived["formulae"]["reference_integrity_mean_per_task"],
            "condition_total / complete_pairs",
        )
        self.assertEqual(derived["structure"]["with_atlas"]["precision_percent"], 80.0)
        self.assertEqual(derived["structure"]["with_atlas"]["recall_percent"], 100.0)

    def test_unmeasured_tokens_remain_null_instead_of_becoming_an_estimate(self) -> None:
        baseline = self.valid_receipt(
            "BASELINE", "pair-1", "base-1", total_tokens=None
        )
        atlas = self.valid_receipt(
            "ATLAS_USE",
            "pair-1",
            "atlas-1",
            total_tokens=None,
            counterbalance=2,
        )
        derived = self.benchmark.measured_summary(
            [
                self.benchmark.validate_receipt(
                    self.valid_receipt("ATLAS_BUILD", None, "build-1")
                ),
                self.benchmark.validate_receipt(baseline),
                self.benchmark.validate_receipt(atlas),
            ]
        )
        self.assertEqual(derived["tokens"]["sample_n"], 0)
        self.assertIsNone(derived["tokens"]["median_paired_saving"])
        self.assertIsNone(derived["tokens"]["median_paired_saving_percent"])
        self.assertIsNone(derived["tokens"]["break_even_tasks"])

    def test_partial_pair_token_telemetry_keeps_only_gross_exact_subset(self) -> None:
        receipts = [
            self.valid_receipt(
                "ATLAS_BUILD",
                None,
                "build-1",
                total_tokens=100,
            ),
            self.valid_receipt(
                "BASELINE",
                "pair-1",
                "base-1",
                total_tokens=100,
            ),
            self.valid_receipt(
                "ATLAS_USE",
                "pair-1",
                "atlas-1",
                total_tokens=60,
                counterbalance=2,
            ),
            self.valid_receipt(
                "BASELINE",
                "pair-2",
                "base-2",
                total_tokens=120,
                counterbalance=2,
                task_id="task-b",
            ),
            self.valid_receipt(
                "ATLAS_USE",
                "pair-2",
                "atlas-2",
                total_tokens=None,
                counterbalance=1,
                task_id="task-b",
            ),
            self.valid_receipt(
                "ATLAS_REFRESH",
                None,
                "refresh-1",
                total_tokens=50,
            ),
        ]
        derived = self.benchmark.measured_summary(
            [self.benchmark.validate_receipt(item) for item in receipts]
        )
        self.assertEqual(derived["pairs"]["complete"], 2)
        self.assertEqual(derived["pairs"]["exact_token_pairs"], 1)
        self.assertEqual(derived["tokens"]["sample_n"], 1)
        self.assertEqual(derived["tokens"]["median_paired_saving"], 40)
        self.assertEqual(derived["tokens"]["median_paired_saving_percent"], 40.0)
        self.assertEqual(derived["tokens"]["range_paired_saving"], [40, 40])
        self.assertEqual(derived["tokens"]["amortized_refresh_per_task"], 25)
        self.assertIsNone(
            derived["tokens"]["median_net_saving_after_refresh"]
        )
        self.assertIsNone(derived["tokens"]["break_even_tasks"])

    def test_net_token_metrics_require_exact_build_and_refresh_costs(self) -> None:
        for unmeasured_condition in ("ATLAS_BUILD", "ATLAS_REFRESH"):
            with self.subTest(unmeasured_condition=unmeasured_condition):
                receipts = [
                    self.valid_receipt(
                        "ATLAS_BUILD",
                        None,
                        "build-1",
                        total_tokens=(
                            None
                            if unmeasured_condition == "ATLAS_BUILD"
                            else 100
                        ),
                    ),
                    self.valid_receipt(
                        "BASELINE",
                        "pair-1",
                        "base-1",
                        total_tokens=100,
                    ),
                    self.valid_receipt(
                        "ATLAS_USE",
                        "pair-1",
                        "atlas-1",
                        total_tokens=60,
                        counterbalance=2,
                    ),
                    self.valid_receipt(
                        "ATLAS_REFRESH",
                        None,
                        "refresh-1",
                        total_tokens=(
                            None
                            if unmeasured_condition == "ATLAS_REFRESH"
                            else 10
                        ),
                    ),
                ]
                derived = self.benchmark.measured_summary(
                    [
                        self.benchmark.validate_receipt(item)
                        for item in receipts
                    ]
                )
                self.assertEqual(derived["tokens"]["sample_n"], 1)
                self.assertEqual(
                    derived["tokens"]["median_paired_saving"],
                    40,
                )
                self.assertIsNone(
                    derived["tokens"]["median_net_saving_after_refresh"]
                )
                self.assertIsNone(derived["tokens"]["break_even_tasks"])

    def test_measured_campaign_rejects_overlapping_pair_intervals(self) -> None:
        receipts = [
            self.valid_receipt("ATLAS_BUILD", None, "build-1"),
            self.valid_receipt("BASELINE", "pair-1", "base-1"),
            self.valid_receipt(
                "ATLAS_USE",
                "pair-1",
                "atlas-1",
                counterbalance=2,
            ),
            self.valid_receipt(
                "BASELINE",
                "pair-2",
                "base-2",
                counterbalance=2,
                task_id="task-b",
            ),
            self.valid_receipt(
                "ATLAS_USE",
                "pair-2",
                "atlas-2",
                counterbalance=1,
                task_id="task-b",
            ),
        ]
        self.set_interval(
            receipts[4],
            "2026-07-26T10:00:02.500Z",
            wall_time_ms=1000,
        )
        with self.assertRaisesRegex(ValueError, "overlapping measured intervals"):
            self.benchmark.measured_summary(
                [self.benchmark.validate_receipt(item) for item in receipts]
            )

    def test_measured_campaign_rejects_use_overlap_with_map_work(self) -> None:
        for producer_condition, producer_start in (
            ("ATLAS_BUILD", "2026-07-26T10:00:01.500Z"),
            ("ATLAS_REFRESH", "2026-07-26T10:00:02.500Z"),
        ):
            with self.subTest(producer_condition=producer_condition):
                receipts = [
                    self.valid_receipt(
                        "ATLAS_BUILD",
                        None,
                        "build-1",
                    ),
                    self.valid_receipt(
                        "BASELINE",
                        "pair-1",
                        "base-1",
                    ),
                    self.valid_receipt(
                        "ATLAS_USE",
                        "pair-1",
                        "atlas-1",
                        counterbalance=2,
                    ),
                ]
                producer = receipts[0]
                if producer_condition == "ATLAS_REFRESH":
                    producer = self.valid_receipt(
                        "ATLAS_REFRESH",
                        None,
                        "refresh-1",
                    )
                    receipts.append(producer)
                self.set_interval(
                    producer,
                    producer_start,
                    wall_time_ms=1000,
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "overlapping measured intervals",
                ):
                    self.benchmark.measured_summary(
                        [
                            self.benchmark.validate_receipt(item)
                            for item in receipts
                        ]
                    )

    def test_cli_derivation_is_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas benchmark receipts ") as temp_dir:
            root = Path(temp_dir)
            receipts = [
                self.valid_receipt("ATLAS_BUILD", None, "build-1"),
                self.valid_receipt("BASELINE", "pair-1", "base-1"),
                self.valid_receipt(
                    "ATLAS_USE", "pair-1", "atlas-1", counterbalance=2
                ),
            ]
            for index, receipt in enumerate(receipts):
                (root / f"{index}.json").write_text(
                    json.dumps(receipt, ensure_ascii=False),
                    encoding="utf-8",
                )
            first = run_command(
                [
                    "python3",
                    BENCHMARK_SCRIPT,
                    "derive",
                    "--input-dir",
                    root,
                ]
            )
            second = run_command(
                [
                    "python3",
                    BENCHMARK_SCRIPT,
                    "derive",
                    "--input-dir",
                    root,
                ]
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)

    def valid_receipt(
        self,
        condition: str,
        pair_id: str | None,
        run_id: str,
        *,
        wall_time_ms: int = 1000,
        total_tokens: int | None = 100,
        observed: list[str] | None = None,
        counterbalance: int = 1,
        contract_pass: bool = True,
        task_id: str = "task-a",
        dangling_refs: int = 0,
        unclassified_claims: int = 0,
        unsafe_refs: int = 0,
    ) -> dict[str, object]:
        input_atlas_hash = (
            "c" * 64 if condition in {"ATLAS_USE", "ATLAS_REFRESH"} else None
        )
        output_atlas_hash = (
            "c" * 64
            if condition == "ATLAS_BUILD"
            else "f" * 64
            if condition == "ATLAS_REFRESH"
            else None
        )
        output_hash = output_atlas_hash or "d" * 64
        base_time = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
        offset_seconds = 0
        if condition == "ATLAS_BUILD":
            offset_seconds = -2
        elif condition == "ATLAS_REFRESH":
            offset_seconds = 8
        elif task_id == "task-a":
            offset_seconds = 0 if counterbalance == 1 else 2
        elif task_id == "task-b":
            offset_seconds = 4 if counterbalance == 1 else 6
        started = base_time + timedelta(seconds=offset_seconds)
        ended = started + timedelta(milliseconds=wall_time_ms)
        started_at = started.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        ended_at = ended.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expected_items = [oracle_item("owner"), oracle_item("entry")]
        observed_items = [
            oracle_item(item)
            for item in (observed if observed is not None else ["owner", "entry"])
        ]
        return {
            "acceptance_contract_sha256": (
                "b" * 64
                if condition in {"BASELINE", "ATLAS_USE"}
                else None
            ),
            "adapter": "codex",
            "adapter_version": "0.1.0",
            "campaign_id": "pilot",
            "classification": "MEASURED",
            "condition": condition,
            "counterbalance_position": counterbalance,
            "effective_model": "model-a",
            "ended_at": ended_at,
            "fixture": "fixture-a",
            "fixture_sha256": "a" * 64,
            "fresh_session": True,
            "input_atlas_sha256": input_atlas_hash,
            "oracle_sha256": oracle_manifest_hash(expected_items),
            "output_atlas_sha256": output_atlas_hash,
            "pair_id": pair_id,
            "permission_profile_sha256": "e" * 64,
            "reasoning_effort": "high",
            "requested_model": "model-a",
            "result": {
                "contract_pass": contract_pass,
                "dangling_refs": dangling_refs,
                "expected_items": expected_items,
                "observed_items": observed_items,
                "output_sha256": output_hash,
                "status": "PASS",
                "unclassified_claims": unclassified_claims,
                "unsafe_refs": unsafe_refs,
            },
            "revision": "rev-1",
            "run_id": run_id,
            "schema_version": "0.1",
            "started_at": started_at,
            "task_id": task_id,
            "task_sha256": hashlib.sha256(task_id.encode("utf-8")).hexdigest(),
            "tokens": {
                "cached": None,
                "input": None,
                "output": None,
                "reasoning": None,
                "source": (
                    "PROVIDER_REPORTED" if total_tokens is not None else "UNMEASURED"
                ),
                "total": total_tokens,
            },
            "wall_time_ms": wall_time_ms,
            "wall_time_source": "HOST_MONOTONIC",
        }

    @staticmethod
    def set_interval(
        receipt: dict[str, object],
        started_at: str,
        *,
        wall_time_ms: int,
    ) -> None:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ended = started + timedelta(milliseconds=wall_time_ms)
        receipt["started_at"] = started_at
        receipt["ended_at"] = (
            ended.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        receipt["wall_time_ms"] = wall_time_ms


if __name__ == "__main__":
    unittest.main()
