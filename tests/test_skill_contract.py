from __future__ import annotations

import re
import unittest

from tests.support import (
    CLAUDE_ADAPTER,
    CODEX_ADAPTER,
    CORE_SKILL,
    REPO_ROOT,
    assert_file,
    parse_frontmatter,
    read_text,
)


UNFINISHED_MARKER_PATTERN = re.compile(
    r"\b(?:" + "|".join(
        (
            "TO" + "DO",
            "T" + "BD",
            "FIX" + "ME",
            "PLACE" + "HOLDER",
            "CHANGE" + "ME",
        )
    ) + r")\b",
    re.IGNORECASE,
)


class CoreSkillContractTests(unittest.TestCase):
    def test_repository_has_public_project_layout(self) -> None:
        required_files = (
            "README.md",
            "README.ru.md",
            "LICENSE",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "docs/README.md",
            "docs/README.ru.md",
            "docs/methodology.md",
            "docs/methodology.ru.md",
            "docs/depth-levels.md",
            "docs/depth-levels.ru.md",
            "docs/outputs.md",
            "docs/outputs.ru.md",
            "docs/examples.md",
            "docs/examples.ru.md",
            "docs/adapters.md",
            "docs/adapters.ru.md",
            "docs/case-study.md",
            "docs/case-study.ru.md",
        )
        for relative_path in required_files:
            with self.subTest(path=relative_path):
                assert_file(self, REPO_ROOT / relative_path)

    def test_frontmatter_contains_only_required_fields(self) -> None:
        metadata, _ = parse_frontmatter(self, CORE_SKILL / "SKILL.md")
        self.assertEqual(set(metadata), {"name", "description"})
        self.assertEqual(metadata["name"], "map-project")
        self.assertGreaterEqual(len(metadata["description"]), 80)
        self.assertIsNone(UNFINISHED_MARKER_PATTERN.search(metadata["description"]))

    def test_skill_name_matches_its_folder(self) -> None:
        metadata, _ = parse_frontmatter(self, CORE_SKILL / "SKILL.md")
        self.assertEqual(CORE_SKILL.name, metadata["name"])

    def test_skill_body_has_no_scaffold_placeholders(self) -> None:
        _, body = parse_frontmatter(self, CORE_SKILL / "SKILL.md")
        self.assertIsNone(
            UNFINISHED_MARKER_PATTERN.search(body),
            "SKILL.md still contains generator scaffold text",
        )

    def test_description_routes_all_supported_mapping_intents(self) -> None:
        metadata, _ = parse_frontmatter(self, CORE_SKILL / "SKILL.md")
        description = metadata["description"].lower()
        semantic_groups = {
            "map": ("map", "atlas"),
            "project": ("project", "repository", "codebase"),
            "architecture": ("architecture",),
            "runtime": ("runtime", "entrypoint"),
            "state": ("state", "data"),
            "authority": ("authority", "source of truth"),
            "continuation": ("handoff", "refactor", "legacy", "audit", "refresh"),
        }
        for intent, alternatives in semantic_groups.items():
            with self.subTest(intent=intent):
                self.assertTrue(
                    any(term in description for term in alternatives),
                    f"description does not route the {intent!r} intent",
                )

    def test_skill_uses_progressive_disclosure_and_stays_compact(self) -> None:
        skill = read_text(self, CORE_SKILL / "SKILL.md")
        self.assertLessEqual(len(skill.splitlines()), 500)
        self.assertTrue((CORE_SKILL / "references").is_dir(), "detailed rules must live in references/")
        self.assertTrue((CORE_SKILL / "assets" / "templates").is_dir(), "outputs must live in templates/")

    def test_skill_requires_clean_probes_and_counted_claim_cross_checks(self) -> None:
        _, body = parse_frontmatter(self, CORE_SKILL / "SKILL.md")
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", body)
        self.assertRegex(body.lower(), r"enumerat(?:e|ed).{0,120}(?:count|denominator)")
        self.assertRegex(body.lower(), r"contradiction|disprove")

    def test_skill_forbids_broad_reads_when_building_baselines(self) -> None:
        _, body = parse_frontmatter(self, CORE_SKILL / "SKILL.md")
        normalized = " ".join(body.lower().split())
        self.assertIn("safe inventory", normalized)
        self.assertRegex(normalized, r"never (?:run|use).{0,120}broad.{0,80}(?:hash|checksum|find)")
        self.assertRegex(normalized, r"excluded.{0,120}(?:without opening|without reading)")

    def test_resource_and_collaboration_governance_is_host_independent(self) -> None:
        protocol = read_text(self, REPO_ROOT / "core" / "PROTOCOL.md")
        skill = read_text(self, CORE_SKILL / "SKILL.md")
        workflow = read_text(
            self, CORE_SKILL / "references" / "investigation-workflow.md"
        )
        normalized = " ".join(f"{protocol}\n{skill}\n{workflow}".lower().split())

        for required in (
            "memory pressure",
            "swap",
            "responsiveness",
            "free disk",
            "active model and terminal sessions",
            "one heavy process",
            "object count and byte limit",
            "storage headroom",
            "one writer",
            "owner-defined session limit",
            "capability tier",
            "exact frozen bytes",
        ):
            with self.subTest(required=required):
                self.assertTrue(
                    required in normalized,
                    f"resource governance is missing {required!r}",
                )

        self.assertIsNotNone(
            re.search(
                r"(?:production runtime|database|container runtime).{0,240}"
                r"explicit (?:owner )?approval",
                normalized,
            ),
            "runtime stop authority is not explicit",
        )
        self.assertIsNotNone(
            re.search(
                r"remote runner.{0,320}explicit (?:owner )?approval",
                normalized,
            ),
            "remote runner authority is not explicit",
        )
        self.assertIsNotNone(
            re.search(
                r"independent auditor.{0,180}(?:must not|is not).{0,100}author",
                normalized,
            ),
            "independent auditor authorship boundary is missing",
        )

    def test_core_protocol_defines_modes_and_evidence_classes(self) -> None:
        assert_file(self, CORE_SKILL / "SKILL.md")
        corpus = "\n".join(
            path.read_text(encoding="utf-8")
            for path in CORE_SKILL.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".py", ".json", ".yaml", ".yml", ".tsv"}
        )
        for required_term in ("quick", "standard", "forensic"):
            with self.subTest(term=required_term):
                self.assertIn(required_term, corpus.lower())
        for claim_kind in ("CONFIRMED", "INFERENCE", "HYPOTHESIS", "TARGET", "UNKNOWN"):
            with self.subTest(claim_kind=claim_kind):
                self.assertRegex(corpus, rf"\b{claim_kind}\b")

    def test_core_is_independent_of_codex_claude_and_openai(self) -> None:
        forbidden = re.compile(r"(?i)(?:\bcodex\b|\bclaude\b|\bopenai\b|\$CODEX_HOME|\.claude)")
        offenders: list[str] = []
        for path in CORE_SKILL.rglob("*"):
            relative = path.relative_to(CORE_SKILL).as_posix()
            if forbidden.search(relative):
                offenders.append(relative)
                continue
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                if forbidden.search(text):
                    offenders.append(relative)
        self.assertEqual(offenders, [], f"tool-specific branding leaked into core: {offenders}")

    def test_codex_adapter_owns_openai_metadata(self) -> None:
        metadata_path = CODEX_ADAPTER / "skills" / "map-project" / "agents" / "openai.yaml"
        text = read_text(self, metadata_path)
        self.assertRegex(text, r"(?m)^interface:\s*$")
        self.assertRegex(text, r"(?m)^\s+display_name:\s*[\"']?Project Atlas")
        self.assertRegex(text, r"(?m)^\s+short_description:\s*[\"']?.{20,}")
        self.assertIn("$project-atlas:map-project", text)
        self.assertIsNone(UNFINISHED_MARKER_PATTERN.search(text))

    def test_claude_adapter_does_not_ship_codex_ui_metadata(self) -> None:
        self.assertFalse(
            (CLAUDE_ADAPTER / "skills" / "map-project" / "agents" / "openai.yaml").exists(),
            "Claude adapter must not contain Codex-only openai.yaml metadata",
        )


if __name__ == "__main__":
    unittest.main()
