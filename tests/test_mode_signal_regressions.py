from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import run_atlas


class ModeSignalRegressionTests(unittest.TestCase):
    def test_support_only_inventory_size_does_not_raise_mode(self) -> None:
        for support_file_count in (30, 750):
            with self.subTest(support_file_count=support_file_count), tempfile.TemporaryDirectory(
                prefix=f"atlas support-only size {support_file_count} "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                support = project / "tests"
                support.mkdir(parents=True)
                for index in range(support_file_count):
                    (support / f"support_{index:04d}.txt").write_text(
                        "support evidence only\n",
                        encoding="utf-8",
                    )

                result = run_atlas("inventory", "--project", project)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["mode"], "QUICK")
                self.assertEqual(payload["file_count"], support_file_count)
                self.assertEqual(len(payload["files"]), support_file_count)
                self.assertEqual(payload["signals"]["file_count"], support_file_count)
                self.assertEqual(payload["signals"]["structural_file_count"], 0)
                self.assertEqual(payload["signals"]["source_file_count"], 0)
                self.assertEqual(
                    payload["reasons"],
                    ["small, single-runtime, low-risk structural surface"],
                )

    def test_root_support_filenames_do_not_raise_mode(self) -> None:
        for support_file_count in (30, 750):
            with self.subTest(support_file_count=support_file_count), tempfile.TemporaryDirectory(
                prefix=f"atlas root support size {support_file_count} "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                project.mkdir()
                for index in range(support_file_count):
                    (project / f"test_support_{index:04d}.py").write_text(
                        "VALUE = 'support-only'\n",
                        encoding="utf-8",
                    )

                result = run_atlas("inventory", "--project", project)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["mode"], "QUICK")
                self.assertEqual(payload["file_count"], support_file_count)
                self.assertEqual(payload["signals"]["structural_file_count"], 0)
                self.assertEqual(payload["signals"]["source_file_count"], 0)

        with tempfile.TemporaryDirectory(prefix="atlas root support conventions ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            for filename in (
                "conftest.py",
                "test_gateway.js",
                "worker_test.py",
                "worker_test.go",
                "worker_spec.rb",
                "worker.spec.ts",
            ):
                (project / filename).write_text("VALUE = 'support-only'\n", encoding="utf-8")
            result = run_atlas("inventory", "--project", project)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "QUICK")
            self.assertEqual(payload["signals"]["structural_file_count"], 0)

    def test_automatic_decision_requires_action_and_authority_in_one_unit(self) -> None:
        negative_cases = {
            "automatic_formatting": "# Tool\n\nAutomatic formatting is available.\n",
            "documented_authority": "# Tool\n\nThe authority boundary is documented.\n",
            "split_units": (
                "# Tool\n\nAn automatic approval decision is recorded.\n\n"
                "The operator override governs final authority.\n"
            ),
            "filename_only": "# Tool\n\nA local utility processes plain text.\n",
        }
        for case_name, readme in negative_cases.items():
            with self.subTest(case=case_name), tempfile.TemporaryDirectory(
                prefix=f"atlas automatic evidence {case_name} "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                source = project / "src"
                source.mkdir(parents=True)
                (project / "README.md").write_text(readme, encoding="utf-8")
                (source / "authority.py").write_text(
                    "POLICY = 'human-controlled'\n",
                    encoding="utf-8",
                )
                if case_name == "filename_only":
                    (source / "automatic_decision.py").write_text(
                        "FORMAT = 'plain'\n",
                        encoding="utf-8",
                    )

                result = run_atlas("select-mode", "--project", project)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["mode"], "QUICK")
                self.assertFalse(payload["signals"]["automatic_decisions"])

        positive_units = (
            "The operator override governs an automatic approval decision.",
            "The service automatically makes approval decisions. Operators can override them.",
        )
        for unit in positive_units:
            with self.subTest(positive_unit=unit), tempfile.TemporaryDirectory(
                prefix="atlas automatic same unit "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                source = project / "src"
                source.mkdir(parents=True)
                (project / "README.md").write_text(
                    f"# Approval service\n\n{unit}\n",
                    encoding="utf-8",
                )
                (source / "authority.py").write_text(
                    "POLICY = 'operator-override'\n",
                    encoding="utf-8",
                )

                result = run_atlas("select-mode", "--project", project)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["mode"], "STANDARD")
                self.assertTrue(payload["signals"]["automatic_decisions"])

    def test_financial_signal_requires_product_context_not_cost_metaphor(self) -> None:
        cases = {
            "cost_metaphor": (
                "# Atlas\n\n"
                "The map has an expensive first payment, then later tasks reuse it.\n",
                False,
            ),
            "financial_data": (
                "# Records\n\nThe service stores financial data for customers.\n",
                True,
            ),
            "payment_action": (
                "# Checkout\n\nThe service processes customer payments.\n",
                True,
            ),
            "payment_product": (
                "# Checkout\n\nThis payment processor exposes a local API.\n",
                True,
            ),
            "settling_action": (
                "# Settlement\n\nThe worker is settling customer payments.\n",
                True,
            ),
            "payment_processing_service": (
                "# Checkout\n\nThe app is a payment processing service.\n",
                True,
            ),
            "passive_payment_action": (
                "# Checkout\n\nCustomer payments are processed by the worker.\n",
                True,
            ),
            "progressive_passive_payment_action": (
                "# Checkout\n\nCustomer payments are being processed by the worker.\n",
                True,
            ),
            "modal_passive_payment_action": (
                "# Checkout\n\nCustomer payments can be processed by the worker.\n",
                True,
            ),
            "future_passive_payment_action": (
                "# Checkout\n\nCustomer payments will be processed by the worker.\n",
                True,
            ),
            "required_passive_payment_action": (
                "# Checkout\n\nCustomer payments must be processed by the worker.\n",
                True,
            ),
            "get_passive_payment_action": (
                "# Checkout\n\nCustomer payments get processed by the worker.\n",
                True,
            ),
            "payments_platform": (
                "# Checkout\n\nThe payments platform exposes a local API.\n",
                True,
            ),
            "payment_orchestration_platform": (
                "# Checkout\n\nThis payment orchestration platform routes transactions.\n",
                True,
            ),
            "settlement_engine": (
                "# Settlement\n\nThe settlement engine reconciles ledger entries.\n",
                True,
            ),
            "hyphenated_payment_processing_service": (
                "# Checkout\n\nThe app is a payment-processing service.\n",
                True,
            ),
            "hyphenated_payment_orchestration_platform": (
                "# Checkout\n\nThis payment-orchestration platform routes transactions.\n",
                True,
            ),
            "hyphenated_settlement_engine": (
                "# Settlement\n\nThe settlement-engine reconciles ledger entries.\n",
                True,
            ),
        }
        for case_name, (readme, expected) in cases.items():
            with self.subTest(case=case_name), tempfile.TemporaryDirectory(
                prefix=f"atlas financial evidence {case_name} "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                project.mkdir()
                (project / "README.md").write_text(readme, encoding="utf-8")

                result = run_atlas("select-mode", "--project", project)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["signals"]["financial_data"], expected)

    def test_no_readme_source_declarations_supply_bounded_risk_evidence(self) -> None:
        for prefix in ("", "\ufeff"):
            with self.subTest(utf8_bom=bool(prefix)), tempfile.TemporaryDirectory(
                prefix="atlas source declaration "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                project.mkdir()
                (project / "app.py").write_text(
                    prefix
                    + '"""This production service handles financial payments.\n\n'
                    "The service automatically makes approval decisions. "
                    'Operators can override them.\n"""\n'
                    "def main():\n    return 0\n",
                    encoding="utf-8",
                )

                result = run_atlas("select-mode", "--project", project)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["mode"], "FORENSIC")
                self.assertTrue(payload["signals"]["production"])
                self.assertTrue(payload["signals"]["financial_data"])
                self.assertTrue(payload["signals"]["automatic_decisions"])

        with tempfile.TemporaryDirectory(prefix="atlas regex constants ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            (project / "signals.py").write_text(
                "import re\n"
                "PRODUCTION = re.compile(r'production service')\n"
                "FINANCIAL = re.compile(r'financial payments')\n"
                "AUTOMATIC = re.compile(r'automatically makes approval decisions')\n"
                "AUTHORITY = re.compile(r'operators can override them')\n",
                encoding="utf-8",
            )

            result = run_atlas("select-mode", "--project", project)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "QUICK")
            self.assertFalse(payload["signals"]["production"])
            self.assertFalse(payload["signals"]["financial_data"])
            self.assertFalse(payload["signals"]["automatic_decisions"])

    def test_no_readme_c_style_block_declarations_supply_bounded_risk_evidence(self) -> None:
        declarations = {
            "Service.java": (
                "/**\n"
                " * This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them.\n"
                " */\n"
                "final class Service {}\n"
            ),
            "PackagedService.java": (
                "/* Copyright 2026 */\n"
                "package com.example.payments;\n"
                "import static com.example.Policy.APPROVAL;\n"
                "/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "final class PackagedService {}\n"
            ),
            "Service.kt": (
                "package com.example.payments\n"
                "import com.example.Policy as ApprovalPolicy\n"
                "/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "class Service\n"
            ),
            "Service.cs": (
                "using System;\n"
                "namespace Example.Payments;\n"
                "/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "internal sealed class Service {}\n"
            ),
            "service.ts": (
                "/* This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "export const service = {};\n"
            ),
            "service.js": (
                "#!/usr/bin/env node\n"
                "/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "export const service = {};\n"
            ),
            "worker.ts": (
                "\ufeff/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "export const worker = {};\n"
            ),
            "service.mjs": (
                "#!/usr/bin/env node\n"
                "/* This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "export const service = {};\n"
            ),
            "service.cjs": (
                "/* This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "module.exports = {};\n"
            ),
            "service.mts": (
                "/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "export const service = {};\n"
            ),
            "service.cts": (
                "/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "export = {};\n"
            ),
            "service.h": (
                "/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "struct service;\n"
            ),
            "service.hpp": (
                "#pragma once\n"
                "#include <string>\n"
                "/** This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "class service {};\n"
            ),
            "service.go": (
                "package payments\n"
                "import (\n\t\"context\"\n)\n"
                "/* This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "type service struct{}\n"
            ),
            "service.php": (
                "<?php\n"
                "namespace Example\\Payments;\n"
                "/* This production service handles financial payments.\n"
                " * The service automatically makes approval decisions.\n"
                " * Operators can override them. */\n"
                "final class Service {}\n"
            ),
        }
        for filename, source in declarations.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory(
                prefix="atlas block declaration "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                project.mkdir()
                (project / filename).write_text(source, encoding="utf-8")

                result = run_atlas("select-mode", "--project", project)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["mode"], "FORENSIC")
                self.assertTrue(payload["signals"]["production"])
                self.assertTrue(payload["signals"]["financial_data"])
                self.assertTrue(payload["signals"]["automatic_decisions"])

        with tempfile.TemporaryDirectory(prefix="atlas block literal decoy ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            (project / "service.ts").write_text(
                "export const text = "
                "'/* production service handles financial payments; operators override automatic decisions */';\n",
                encoding="utf-8",
            )
            result = run_atlas("select-mode", "--project", project)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "QUICK")
            self.assertFalse(payload["signals"]["production"])
            self.assertFalse(payload["signals"]["financial_data"])
            self.assertFalse(payload["signals"]["automatic_decisions"])

        with tempfile.TemporaryDirectory(prefix="atlas post-declaration comment decoy ") as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            (project / "Service.java").write_text(
                "package com.example;\n"
                "final class Service {}\n"
                "/** production service handles financial payments; "
                "operators override automatic approval decisions */\n",
                encoding="utf-8",
            )
            result = run_atlas("select-mode", "--project", project)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "QUICK")
            self.assertFalse(payload["signals"]["production"])
            self.assertFalse(payload["signals"]["financial_data"])
            self.assertFalse(payload["signals"]["automatic_decisions"])

    def test_no_readme_explicit_config_keys_supply_bounded_risk_evidence(self) -> None:
        for prefix in ("", "\ufeff"):
            with self.subTest(utf8_bom=bool(prefix)), tempfile.TemporaryDirectory(
                prefix="atlas config declaration "
            ) as temp_dir:
                project = Path(temp_dir) / "project"
                project.mkdir()
                (project / "service.yaml").write_text(
                    prefix
                    + "production: true\n"
                    "financial_data: true\n"
                    "automatic_decisions: true\n"
                    "operator_override: true\n",
                    encoding="utf-8",
                )

                result = run_atlas("select-mode", "--project", project)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["mode"], "FORENSIC")
                self.assertTrue(payload["signals"]["production"])
                self.assertTrue(payload["signals"]["financial_data"])
                self.assertTrue(payload["signals"]["automatic_decisions"])


if __name__ == "__main__":
    unittest.main()
