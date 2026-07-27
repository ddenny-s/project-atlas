from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from tests.support import (
    CODEX_ADAPTER,
    CLAUDE_ADAPTER,
    CORE_SKILL,
    REPO_ROOT,
    assert_directory,
    assert_file,
    load_json,
    parse_frontmatter,
    run_command,
    tree_digest,
)

HOST_GUIDANCE_RELATIVE = Path("references/host-guidance.md")
HOST_GUIDANCE_SOURCES = {
    "codex": CODEX_ADAPTER / "MODEL_GUIDANCE.md",
    "claude-code": CLAUDE_ADAPTER / "MODEL_GUIDANCE.md",
}


class AdapterPackagingTests(unittest.TestCase):
    def test_ci_declares_python_310_floor_and_python_313_coverage(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('python-version: "3.10"', workflow)
        self.assertGreaterEqual(
            workflow.count('python-version: "3.13"'),
            2,
            "CI must retain supported Python 3.13 coverage on POSIX and Windows",
        )
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn("windows-latest", workflow)

    def test_ci_whitespace_gate_handles_root_and_later_commits_without_masking_errors(
        self,
    ) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        step_marker = "      - name: Check all tracked files for whitespace errors\n"
        self.assertIn(step_marker, workflow)
        step = workflow.split(step_marker, 1)[1]
        run_marker = "        run: |\n"
        self.assertTrue(step.startswith(run_marker))
        commands: list[str] = []
        for line in step[len(run_marker) :].splitlines():
            if not line.startswith("          "):
                break
            commands.append(line.removeprefix("          "))
        self.assertTrue(commands, "CI whitespace step has no shell commands")
        gate = "\n".join(commands)

        with tempfile.TemporaryDirectory(prefix="atlas whitespace gate ") as temp_dir:
            repository = Path(temp_dir) / "repository"
            repository.mkdir()
            for command in (
                ["git", "init", "--quiet"],
                ["git", "config", "user.name", "CI Contract"],
                ["git", "config", "user.email", "ci-contract@example.invalid"],
            ):
                configured = run_command(command, cwd=repository)
                self.assertEqual(configured.returncode, 0, configured.stderr)

            tracked = repository / "tracked.txt"
            tracked.write_text("root commit\n\n", encoding="utf-8")
            for command in (
                ["git", "add", "tracked.txt"],
                ["git", "commit", "--quiet", "-m", "root"],
            ):
                committed = run_command(command, cwd=repository)
                self.assertEqual(committed.returncode, 0, committed.stderr)
            root_result = run_command(["sh", "-eu", "-c", gate], cwd=repository)
            self.assertEqual(root_result.returncode, 0, root_result.stderr)

            tracked.write_text("root commit\nlater commit\n\n", encoding="utf-8")
            for command in (
                ["git", "add", "tracked.txt"],
                ["git", "commit", "--quiet", "-m", "later"],
            ):
                committed = run_command(command, cwd=repository)
                self.assertEqual(committed.returncode, 0, committed.stderr)
            later_result = run_command(["sh", "-eu", "-c", gate], cwd=repository)
            self.assertEqual(later_result.returncode, 0, later_result.stderr)

            tracked.write_text("trailing whitespace \n", encoding="utf-8")
            for command in (
                ["git", "add", "tracked.txt"],
                ["git", "commit", "--quiet", "-m", "whitespace error"],
            ):
                committed = run_command(command, cwd=repository)
                self.assertEqual(committed.returncode, 0, committed.stderr)
            invalid_result = run_command(["sh", "-eu", "-c", gate], cwd=repository)
            self.assertNotEqual(invalid_result.returncode, 0)
            self.assertIn(
                "trailing whitespace",
                f"{invalid_result.stdout}\n{invalid_result.stderr}",
            )

    def test_codex_and_claude_plugin_manifests_exist(self) -> None:
        manifests = {
            "codex": CODEX_ADAPTER / ".codex-plugin" / "plugin.json",
            "claude-code": CLAUDE_ADAPTER / ".claude-plugin" / "plugin.json",
        }
        for adapter, path in manifests.items():
            with self.subTest(adapter=adapter):
                payload = load_json(self, path)
                self.assertEqual(payload.get("name"), "project-atlas")
                self.assertRegex(str(payload.get("version", "")), r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
                self.assertGreaterEqual(len(str(payload.get("description", ""))), 20)
                self.assertEqual(payload.get("license"), "MIT")
                self.assertEqual(payload.get("skills"), "./skills/")

        codex = load_json(self, manifests["codex"])
        claude = load_json(self, manifests["claude-code"])
        codex_marketplace = load_json(
            self, REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
        )["plugins"][0]
        claude_marketplace = load_json(
            self, REPO_ROOT / ".claude-plugin" / "marketplace.json"
        )["plugins"][0]
        versions = {
            "codex plugin": codex.get("version"),
            "claude plugin": claude.get("version"),
            "codex marketplace": codex_marketplace.get("version"),
            "claude marketplace": claude_marketplace.get("version"),
        }
        for owner, version in versions.items():
            with self.subTest(version_owner=owner):
                self.assertIsInstance(version, str, f"{owner} must own an explicit version")
                self.assertRegex(str(version), r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
        self.assertEqual(
            len(set(versions.values())),
            1,
            f"plugin and marketplace versions differ: {versions}",
        )
        self.assertEqual(codex.get("capabilities"), None)
        interface = codex.get("interface")
        self.assertIsInstance(interface, dict)
        self.assertEqual(interface.get("capabilities"), ["Read", "Write"])

    def test_codex_default_prompts_are_mode_neutral_and_aligned(self) -> None:
        expected = (
            "Use $project-atlas:map-project to create a verifiable atlas "
            "of this project."
        )
        manifest = load_json(
            self,
            CODEX_ADAPTER / ".codex-plugin" / "plugin.json",
        )
        self.assertEqual(manifest["interface"].get("defaultPrompt"), [expected])

        metadata = (
            CODEX_ADAPTER
            / "skills"
            / "map-project"
            / "agents"
            / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn(f'default_prompt: "{expected}"', metadata)
        self.assertNotIn("STANDARD", manifest["interface"]["defaultPrompt"][0])
        self.assertNotIn("STANDARD", metadata)

    def test_host_guidance_sources_are_dated_provider_starting_points(self) -> None:
        common_markers = (
            "checked_at: 2026-07-26",
            "status: PROVIDER_BASED_STARTING_POINT",
            "atlas_benchmark_status: NOT_YET_ATLAS_BENCHMARKED",
            "exactly four visible choices",
            "1-3 questions per interaction",
            "unlimited total questions",
            "semantic stop",
            "effective resolved model",
            "exact weekly remaining percentage",
            "timestamp",
            "source",
            "omit the quota line entirely",
            "never infer quota from token",
        )
        host_markers = {
            "codex": (
                "QUICK / read-heavy | GPT-5.6 Terra | `medium`",
                "STANDARD | GPT-5.6 Sol | `high`",
                "FORENSIC / adversarial | GPT-5.6 Sol | `xhigh`",
                "plain chat with A-D",
                "D is exactly `Другое — напишу сам`",
                "Never set `max` or `ultra` globally",
                "https://learn.chatgpt.com/docs/pricing#what-are-the-usage-limits-for-my-plan",
                "https://developers.openai.com/api/docs/guides/latest-model#using-gpt-5-6",
            ),
            "claude-code": (
                "QUICK | `sonnet` | `high`",
                "STANDARD | `opus` | `high`",
                "FORENSIC / adversarial | `best` | `xhigh`",
                "`best` uses Fable 5 when the organization has access to it",
                "otherwise the latest Opus model",
                "On the Anthropic API and Claude Platform on AWS, `opus` currently "
                "resolves to Opus 5",
                "Opus 5 requires Claude Code v2.1.219 or later",
                "Claude Code version",
                "Claude Code v2.1.207",
                "Opus 4.8",
                "Aliases vary by provider and update over time",
                "`ultracode` is a session-only Claude Code setting, not an effort level",
                "`xhigh` plus dynamic workflows",
                "`--effort ultracode` form requires Claude Code v2.1.203 or later",
                "explicitly budgeted",
                "not a default for Atlas or a global setting",
                "Codex remains the primary adapter",
                "`ultrathink`",
                "one turn",
                "does not change API effort",
                "`haiku` is allowed only for bounded extraction or classification",
                "never for a whole Atlas run",
                "Do not claim a native question picker",
                "plain-chat A-D",
                "https://code.claude.com/docs/en/model-config#model-aliases",
                "https://code.claude.com/docs/en/model-config#choose-an-effort-level",
            ),
        }

        for adapter, source in HOST_GUIDANCE_SOURCES.items():
            content = " ".join(source.read_text(encoding="utf-8").split())
            with self.subTest(adapter=adapter):
                for marker in (*common_markers, *host_markers[adapter]):
                    self.assertIn(marker, content)
                entrypoint = (
                    REPO_ROOT
                    / "adapters"
                    / adapter
                    / "skills"
                    / "map-project"
                    / "SKILL.md"
                ).read_text(encoding="utf-8")
                self.assertIn(
                    "When `references/host-guidance.md` is present, read it",
                    entrypoint,
                )

        claude_surfaces = (
            HOST_GUIDANCE_SOURCES["claude-code"],
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "README.ru.md",
            REPO_ROOT / "docs" / "adapters.md",
        )
        for surface in claude_surfaces:
            content = surface.read_text(encoding="utf-8")
            with self.subTest(surface=surface):
                for current_marker in (
                    "Fable 5",
                    "Opus 5",
                    "2.1.219",
                    "2.1.207",
                    "Opus 4.8",
                    "ultracode",
                    "ultrathink",
                ):
                    self.assertIn(current_marker, content)
                for stale_marker in (
                    "`best` is currently equivalent to the dynamic `opus` alias",
                    "`best` as equivalent to the dynamic `opus` alias",
                    "`best` сейчас равен динамическому псевдониму `opus`",
                    "`opus` currently resolves to Opus 4.7",
                    "`opus` resolves to Opus 4.7",
                    "`opus` сейчас выбирает Opus 4.7",
                ):
                    self.assertNotIn(stale_marker, content)

        russian_homepage = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for marker in (
            "`best` использует Fable 5",
            "Anthropic API и Claude Platform on AWS",
            "`opus` сейчас выбирает Opus 5",
            "Claude Code 2.1.219",
            "Claude Code 2.1.207",
            "Opus 4.8",
            "`ultracode`",
            "только на текущую сессию",
            "`xhigh` и динамические рабочие процессы",
            "`--effort ultracode`",
            "Claude Code 2.1.203",
            "явно согласованного бюджета",
            "`ultrathink`",
            "не меняет effort, передаваемый API",
            "Codex остаётся основным адаптером",
        ):
            with self.subTest(russian_homepage_marker=marker):
                self.assertIn(marker, russian_homepage)

    def test_adapter_docs_explain_host_guidance_and_interaction_fallbacks(self) -> None:
        documentation = " ".join(
            (REPO_ROOT / "docs" / "adapters.md").read_text(encoding="utf-8").split()
        )
        markers = (
            "`MODEL_GUIDANCE.md` is the adapter-owned source",
            "`references/host-guidance.md`",
            "only intentionally different file at a common payload path",
            "Core stays vendor-neutral",
            "`checked_at: 2026-07-26`",
            "`PROVIDER_BASED_STARTING_POINT`",
            "`NOT_YET_ATLAS_BENCHMARKED`",
            "exactly four visible choices",
            "D is exactly `Другое — напишу сам`",
            "Claude Code adapter makes no native-picker claim",
            "plain-chat A-D",
            "1-3 questions per interaction",
            "unlimited total count",
            "semantic stop",
            "exact weekly remaining percentage",
            "omit the quota line",
            "never converts tokens into quota",
            "effective resolved model",
            "Claude Code version",
            "`best` uses Fable 5 when the organization has access to it",
            "Anthropic API and Claude Platform on AWS",
            "`opus` resolves to Opus 5",
            "Opus 5 requires Claude Code v2.1.219 or later",
            "Claude Code v2.1.207",
            "Opus 4.8",
            "Alias resolution varies by provider and updates over time",
            "`ultracode` is session-only",
            "`xhigh` plus dynamic workflows",
            "`--effort ultracode`",
            "v2.1.203",
            "explicitly budgeted",
            "`ultrathink` keyword requests one-off deeper reasoning",
            "API effort unchanged",
            "Codex remains the primary adapter",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, documentation)

    def test_packaged_host_guidance_is_the_only_common_payload_difference(self) -> None:
        payloads: dict[str, dict[Path, bytes]] = {}
        for adapter, adapter_root in (
            ("codex", CODEX_ADAPTER),
            ("claude-code", CLAUDE_ADAPTER),
        ):
            bundle = adapter_root / "skills" / "map-project"
            payloads[adapter] = {
                path.relative_to(bundle): path.read_bytes()
                for path in bundle.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.relative_to(bundle).parts
                and path.suffix not in {".pyc", ".pyo"}
                and path.name != ".DS_Store"
            }
            packaged = bundle / HOST_GUIDANCE_RELATIVE
            with self.subTest(adapter=adapter, path=HOST_GUIDANCE_RELATIVE):
                assert_file(self, packaged)
                self.assertEqual(
                    packaged.read_bytes(),
                    HOST_GUIDANCE_SOURCES[adapter].read_bytes(),
                )

        codex_payload = dict(payloads["codex"])
        claude_payload = dict(payloads["claude-code"])
        codex_payload.pop(Path("agents/openai.yaml"))
        codex_guidance = codex_payload.pop(HOST_GUIDANCE_RELATIVE)
        claude_guidance = claude_payload.pop(HOST_GUIDANCE_RELATIVE)
        self.assertNotEqual(
            codex_guidance,
            claude_guidance,
            "host guidance must describe each host rather than silently converge",
        )
        self.assertEqual(set(codex_payload), set(claude_payload))
        for relative in sorted(codex_payload):
            with self.subTest(path=relative):
                self.assertEqual(codex_payload[relative], claude_payload[relative])

    def test_claude_adapter_capability_contract_is_explicit(self) -> None:
        documentation = (REPO_ROOT / "docs" / "adapters.md").read_text(encoding="utf-8")
        section = documentation.split("## Claude Code adapter", 1)[1].split(
            "## Capability mapping", 1
        )[0]
        expected = {
            "Repository instructions": (
                ("instruction hierarchy",),
                ("does not bypass", "conflicting instructions"),
            ),
            "Search and bounded reads": (
                ("bounded inventory", "replay contract"),
                ("permissions", "unknown", "no additional access"),
            ),
            "Permission boundary": (
                ("active permission settings", "user approvals"),
                ("does not grant shell", "map-only request"),
            ),
            "Context and handoff": (
                ("live_handoff.md", "durable context boundary"),
                ("context compaction", "re-open cited sources"),
            ),
            "Independent review": (
                ("fresh claude code context", "external reviewer"),
                ("not reviewer identity", "actual subagent independence"),
            ),
            "Installation and discovery": (
                ("/project-atlas:map-project", "/map-project"),
                ("native windows", "posix descriptor primitives"),
            ),
        }

        def capability_errors(content: str) -> list[str]:
            table_lines = [
                line
                for line in content.splitlines()
                if line.startswith("|") and line.count("|") >= 4
            ]
            rows: dict[str, tuple[str, str]] = {}
            for line in table_lines:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) != 3 or cells[0] in {"Host surface", "---"}:
                    continue
                rows[cells[0]] = (cells[1].lower(), cells[2].lower())
            errors: list[str] = []
            if set(rows) != set(expected):
                errors.append("capability row set differs from the six-row contract")
            for surface, (mapping_markers, limitation_markers) in expected.items():
                mapping, limitation = rows.get(surface, ("", ""))
                for marker in mapping_markers:
                    if marker not in mapping:
                        errors.append(f"{surface} mapping omits {marker}")
                for marker in limitation_markers:
                    if marker not in limitation:
                        errors.append(f"{surface} limitation omits {marker}")
            return errors

        self.assertIn("### Claude Code capability contract", section)
        self.assertEqual(capability_errors(section), [])
        self.assertIn("No automated end-to-end Claude task execution", section)

        weakened = section.replace("the adapter grants no additional access", "")
        self.assertTrue(
            any("no additional access" in error for error in capability_errors(weakened)),
            "removing a Claude limitation did not break the capability contract test",
        )

    def test_release_docs_distinguish_ci_from_manual_clean_profile_gate(self) -> None:
        readmes = {
            "Russian homepage": (
                REPO_ROOT / "README.md",
                (
                    "одноразовые чистые профили",
                    "создания тега версии или github release",
                    "ручной гейт",
                    "не считается проверкой github actions",
                ),
            ),
            "Russian documentation": (
                REPO_ROOT / "docs" / "README.ru.md",
                (
                    "одноразовые чистые профили",
                    "создания тега версии или github release",
                    "ручной гейт",
                    "не считается проверкой github actions",
                ),
            ),
        }
        for language, (path, markers) in readmes.items():
            readme = path.read_text(encoding="utf-8").lower()
            for marker in markers:
                with self.subTest(language=language, marker=marker):
                    self.assertIn(marker, readme)

    def test_russian_documentation_mirrors_the_public_homepage(self) -> None:
        homepage = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        documentation = (REPO_ROOT / "docs" / "README.ru.md").read_text(
            encoding="utf-8"
        )
        expected_documentation = homepage
        for homepage_prefix, documentation_prefix in (
            ('src="./assets/', 'src="../assets/'),
            ("](./tests/", "](../tests/"),
            ("](./core/", "](../core/"),
            ("](./scripts/", "](../scripts/"),
            ("](./benchmarks/", "](../benchmarks/"),
            ('href="./benchmarks/', 'href="../benchmarks/'),
            ("](./SECURITY", "](../SECURITY"),
            ("](./CONTRIBUTING", "](../CONTRIBUTING"),
            ("](./LICENSE", "](../LICENSE"),
            ("](./docs/", "](./"),
        ):
            expected_documentation = expected_documentation.replace(
                homepage_prefix,
                documentation_prefix,
            )
        self.assertEqual(documentation, expected_documentation)
        for root_cwd_command in (
            "./scripts/install.sh --user-scope",
            "./scripts/install-claude.sh",
            "./scripts/install.sh --user-scope --force",
            "./scripts/install-claude.sh --force",
        ):
            with self.subTest(root_cwd_command=root_cwd_command):
                self.assertEqual(
                    documentation.count(root_cwd_command),
                    homepage.count(root_cwd_command),
                    "documentation commands must still run from the repository root",
                )
        self.assertIn("](../scripts/benchmark_atlas.py)", documentation)
        self.assertNotIn("](./scripts/benchmark_atlas.py)", documentation)

    def test_marketplace_manifests_exist(self) -> None:
        manifests = {
            REPO_ROOT / ".agents" / "plugins" / "marketplace.json": (
                {"source": "local", "path": "./adapters/codex"},
                CODEX_ADAPTER / ".codex-plugin" / "plugin.json",
            ),
            REPO_ROOT / ".claude-plugin" / "marketplace.json": (
                "./adapters/claude-code",
                CLAUDE_ADAPTER / ".claude-plugin" / "plugin.json",
            ),
        }
        for path, (expected_source, plugin_manifest) in manifests.items():
            with self.subTest(path=path):
                payload = load_json(self, path)
                self.assertIsInstance(payload.get("plugins"), list)
                matches = [
                    plugin
                    for plugin in payload["plugins"]
                    if isinstance(plugin, dict) and plugin.get("name") == "project-atlas"
                ]
                self.assertEqual(len(matches), 1, "marketplace must expose project-atlas exactly once")
                entry = matches[0]
                self.assertEqual(entry.get("source"), expected_source)
                source_path = expected_source["path"] if isinstance(expected_source, dict) else expected_source
                normalized = PurePosixPath(source_path)
                self.assertFalse(normalized.is_absolute())
                self.assertNotIn("..", normalized.parts)
                plugin = load_json(self, plugin_manifest)
                if "version" in entry:
                    self.assertEqual(entry["version"], plugin["version"])

        codex_entry = load_json(self, REPO_ROOT / ".agents" / "plugins" / "marketplace.json")[
            "plugins"
        ][0]
        self.assertEqual(
            codex_entry.get("policy"),
            {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        )
        self.assertEqual(codex_entry.get("category"), "Developer Tools")

    def test_bundled_skills_match_canonical_core_byte_for_byte(self) -> None:
        assert_directory(self, CORE_SKILL)
        canonical_files = sorted(
            path
            for path in CORE_SKILL.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.relative_to(CORE_SKILL).parts
            and path.suffix not in {".pyc", ".pyo"}
            and path.name != ".DS_Store"
        )
        self.assertTrue(canonical_files, "canonical core skill is empty")
        for adapter_root in (CODEX_ADAPTER, CLAUDE_ADAPTER):
            bundle = adapter_root / "skills" / "map-project"
            assert_directory(self, bundle)
            for canonical in canonical_files:
                relative = canonical.relative_to(CORE_SKILL)
                bundled = bundle / relative
                with self.subTest(adapter=adapter_root.name, path=relative):
                    assert_file(self, bundled)
                    expected = canonical.read_bytes()
                    if relative in {
                        Path("assets/templates/standard/LIVE_HANDOFF.md"),
                        Path("assets/templates/forensic/LIVE_HANDOFF.md"),
                    }:
                        expected = expected.replace(
                            b'atlas_default_roots="${PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS:-}"',
                            b'atlas_default_roots="${PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS:-$HOME/.agents/skills:'
                            b'${CODEX_HOME:-$HOME/.codex}/skills:${CODEX_HOME:-$HOME/.codex}/plugins/cache:'
                            b'${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills:'
                            b'${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache}"',
                        )
                    self.assertEqual(bundled.read_bytes(), expected)
                    self.assertFalse(bundled.is_symlink(), "adapter bundles must be self-contained copies")

            canonical_metadata, _ = parse_frontmatter(self, CORE_SKILL / "SKILL.md")
            bundled_metadata, _ = parse_frontmatter(self, bundle / "SKILL.md")
            self.assertEqual(bundled_metadata, canonical_metadata)

    def test_adapter_handoffs_cover_supported_install_roots_without_branding_core(self) -> None:
        modes = ("standard", "forensic")
        for mode in modes:
            core_handoff = (
                CORE_SKILL / "assets" / "templates" / mode / "LIVE_HANDOFF.md"
            ).read_text(encoding="utf-8")
            self.assertIn("PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS", core_handoff)
            self.assertIn('PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS:-}', core_handoff)
            self.assertNotIn("$HOME/.agents/skills", core_handoff)
            self.assertNotIn("CODEX_HOME", core_handoff)
            self.assertNotIn(".claude", core_handoff.lower())
            for adapter in (CODEX_ADAPTER, CLAUDE_ADAPTER):
                bundled = (
                    adapter
                    / "skills"
                    / "map-project"
                    / "assets"
                    / "templates"
                    / mode
                    / "LIVE_HANDOFF.md"
                ).read_text(encoding="utf-8")
                with self.subTest(mode=mode, adapter=adapter.name):
                    self.assertIn("$HOME/.agents/skills", bundled)
                    self.assertIn("CODEX_HOME", bundled)
                    self.assertIn("/plugins/cache", bundled)
                    self.assertIn("CLAUDE_CONFIG_DIR", bundled)

    def test_adapter_sync_check_reports_no_drift(self) -> None:
        script = REPO_ROOT / "scripts" / "sync_adapters.py"
        assert_file(self, script)
        result = run_command(["python3", script, "--check"])
        self.assertEqual(
            result.returncode,
            0,
            f"adapter bundles drifted\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_sync_detects_repairs_and_deterministically_stabilizes_drift(self) -> None:
        script = REPO_ROOT / "scripts" / "sync_adapters.py"
        assert_file(self, script)
        assert_directory(self, REPO_ROOT / "core")
        assert_directory(self, REPO_ROOT / "adapters")
        with tempfile.TemporaryDirectory(prefix="atlas sync repo with spaces ") as temp_dir:
            clone = Path(temp_dir) / "project atlas"
            clone.mkdir()
            shutil.copytree(REPO_ROOT / "core", clone / "core")
            shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
            shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")

            bootstrap = run_command(
                [sys.executable, clone / "scripts" / "sync_adapters.py"],
                cwd=clone,
            )
            self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)

            drifted = clone / "adapters" / "codex" / "skills" / "map-project" / "SKILL.md"
            assert_file(self, drifted)
            drifted.write_text(drifted.read_text(encoding="utf-8") + "\nDRIFT-CANARY\n", encoding="utf-8")
            guidance = (
                clone
                / "adapters"
                / "codex"
                / "skills"
                / "map-project"
                / HOST_GUIDANCE_RELATIVE
            )
            assert_file(self, guidance)
            guidance.write_text(
                guidance.read_text(encoding="utf-8") + "\nHOST-DRIFT-CANARY\n",
                encoding="utf-8",
            )
            orphan = guidance.with_name("unexpected-host-guidance.md")
            orphan.write_text("ORPHAN-CANARY\n", encoding="utf-8")

            check = run_command([sys.executable, clone / "scripts" / "sync_adapters.py", "--check"], cwd=clone)
            self.assertNotEqual(check.returncode, 0, "--check must reject adapter drift")
            self.assertIn(
                "content differs: references/host-guidance.md",
                check.stderr,
            )
            self.assertIn(
                "unexpected file: references/unexpected-host-guidance.md",
                check.stderr,
            )

            repair = run_command([sys.executable, clone / "scripts" / "sync_adapters.py"], cwd=clone)
            self.assertEqual(repair.returncode, 0, repair.stderr)
            self.assertEqual(
                drifted.read_bytes(),
                (clone / "core" / "skill" / "map-project" / "SKILL.md").read_bytes(),
            )
            self.assertEqual(
                guidance.read_bytes(),
                (clone / "adapters" / "codex" / "MODEL_GUIDANCE.md").read_bytes(),
            )
            self.assertFalse(orphan.exists())

            first_digest = tree_digest(clone / "adapters", excluded_names={"__pycache__"})
            second_sync = run_command([sys.executable, clone / "scripts" / "sync_adapters.py"], cwd=clone)
            self.assertEqual(second_sync.returncode, 0, second_sync.stderr)
            self.assertEqual(
                tree_digest(clone / "adapters", excluded_names={"__pycache__"}),
                first_digest,
                "a second sync changed already-synchronized adapter output",
            )

    @unittest.skipIf(os.name == "nt", "symlink creation is not portable on Windows CI")
    def test_sync_rejects_a_symlinked_host_guidance_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas guidance source symlink ") as temp_dir:
            clone = Path(temp_dir) / "project atlas"
            clone.mkdir()
            shutil.copytree(REPO_ROOT / "core", clone / "core")
            shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
            shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")

            source = clone / "adapters" / "codex" / "MODEL_GUIDANCE.md"
            source.unlink()
            outside = clone / "foreign-guidance.md"
            outside.write_text("must-not-be-packaged\n", encoding="utf-8")
            source.symlink_to(outside)

            result = run_command(
                [sys.executable, clone / "scripts" / "sync_adapters.py", "--check"],
                cwd=clone,
                timeout=5,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "host guidance source contains a symlinked path component",
                result.stderr,
            )
            self.assertNotIn("must-not-be-packaged", result.stderr)
            self.assertTrue(source.is_symlink())

    def test_sync_detects_and_repairs_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas fifo sync repo ") as temp_dir:
            clone = Path(temp_dir) / "project atlas"
            clone.mkdir()
            shutil.copytree(REPO_ROOT / "core", clone / "core")
            shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
            shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
            fifo = clone / "adapters" / "codex" / "skills" / "map-project" / "unexpected.pipe"
            os.mkfifo(fifo)

            check = run_command(
                [sys.executable, clone / "scripts" / "sync_adapters.py", "--check"],
                cwd=clone,
                timeout=5,
            )
            self.assertNotEqual(check.returncode, 0, "--check must reject special filesystem nodes")
            self.assertIn("unexpected special node", check.stderr)

            repair = run_command(
                [sys.executable, clone / "scripts" / "sync_adapters.py"],
                cwd=clone,
                timeout=5,
            )
            self.assertEqual(repair.returncode, 0, repair.stderr)
            self.assertFalse(fifo.exists(), "repair left the unexpected FIFO in the bundle")

            stable = run_command(
                [sys.executable, clone / "scripts" / "sync_adapters.py", "--check"],
                cwd=clone,
                timeout=5,
            )
            self.assertEqual(stable.returncode, 0, stable.stderr)

    def test_sync_rejects_a_fifo_adapter_target_before_transaction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas fifo target repo ") as temp_dir:
            clone = Path(temp_dir) / "project atlas"
            clone.mkdir()
            shutil.copytree(REPO_ROOT / "core", clone / "core")
            shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
            shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
            target = clone / "adapters" / "codex" / "skills" / "map-project"
            shutil.rmtree(target)
            os.mkfifo(target)

            check = run_command(
                [sys.executable, clone / "scripts" / "sync_adapters.py", "--check"],
                cwd=clone,
                timeout=5,
            )
            self.assertEqual(check.returncode, 1, check.stderr)
            self.assertIn("is missing or is not a directory", check.stderr)

            repair = run_command(
                [sys.executable, clone / "scripts" / "sync_adapters.py"],
                cwd=clone,
                timeout=5,
            )
            self.assertEqual(repair.returncode, 2, repair.stderr)
            self.assertIn(
                "codex adapter target must be a directory or absent",
                repair.stderr,
            )
            self.assertTrue(stat.S_ISFIFO(os.lstat(target).st_mode))
            self.assertFalse((clone / ".scratch" / "sync-adapters.journal.json").exists())
            self.assertEqual(
                list(target.parent.glob(f".{target.name}.sync-*")),
                [],
            )

    @unittest.skipIf(os.name == "nt", "POSIX modes model a read-only checkout")
    def test_sync_check_passes_without_state_on_a_read_only_checkout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas readonly check repo ") as temp_dir:
            clone = Path(temp_dir) / "project atlas"
            clone.mkdir()
            shutil.copytree(REPO_ROOT / "core", clone / "core")
            shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
            shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")
            script = clone / "scripts" / "sync_adapters.py"
            spec = importlib.util.spec_from_file_location("atlas_readonly_sync", script)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            expected = module.load_expected_adapters()
            for adapter, target in module.ADAPTER_SKILLS.items():
                shutil.rmtree(target)
                target.mkdir()
                target_identity = module.object_identity(target)
                assert target_identity is not None
                module.build_tree(
                    target,
                    *expected[adapter],
                    expected_root=target_identity,
                )

            self.assertFalse(module.SYNC_STATE_DIR.exists())
            try:
                for path in sorted(
                    (path for path in clone.rglob("*") if path.is_dir()),
                    key=lambda item: len(item.parts),
                    reverse=True,
                ):
                    path.chmod(0o555)
                clone.chmod(0o555)

                check = run_command(
                    [sys.executable, script, "--check"],
                    cwd=clone,
                    timeout=10,
                )
                self.assertEqual(check.returncode, 0, check.stderr)
                self.assertEqual(check.stdout, "adapter bundles are synchronized\n")
                self.assertFalse(module.SYNC_STATE_DIR.exists())
            finally:
                clone.chmod(0o755)
                for path in clone.rglob("*"):
                    if path.is_dir():
                        path.chmod(0o755)

    def test_sync_refuses_adapter_parent_symlink_that_escapes_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atlas symlink escape sync ") as temp_dir:
            root = Path(temp_dir)
            clone = root / "project atlas"
            clone.mkdir()
            shutil.copytree(REPO_ROOT / "core", clone / "core")
            shutil.copytree(REPO_ROOT / "adapters", clone / "adapters")
            shutil.copytree(REPO_ROOT / "scripts", clone / "scripts")

            external_skills = root / "external skills"
            external_target = external_skills / "map-project"
            external_target.mkdir(parents=True)
            sentinel = external_target / "outside-repository.txt"
            sentinel.write_text("preserve\n", encoding="utf-8")

            skills_parent = clone / "adapters" / "codex" / "skills"
            shutil.rmtree(skills_parent)
            skills_parent.symlink_to(external_skills, target_is_directory=True)

            result = run_command(
                [sys.executable, clone / "scripts" / "sync_adapters.py"],
                cwd=clone,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0, "sync followed an adapter parent symlink")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
            self.assertFalse((external_target / "SKILL.md").exists())

if __name__ == "__main__":
    unittest.main()
