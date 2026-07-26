<h1 align="center">Project Atlas</h1>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Project Atlas turns bounded repository evidence into a reviewable software project map and continuation handoff">
</p>

<p align="center">
  <a href="./docs/README.ru.md">Русская версия</a>
  ·
  <a href="#start-with-codex">Quick start</a>
  ·
  <a href="./core/PROTOCOL.md">Protocol</a>
  ·
  <a href="./docs/methodology.md">Methodology</a>
  ·
  <a href="./SECURITY.md">Security</a>
  ·
  <a href="./LICENSE">MIT</a>
</p>

Project Atlas turns an unfamiliar repository into a source-linked map an engineer can audit and continue. It traces product behavior, runtimes, data, state, authority, risks, and recovery while keeping facts, inferences, targets, and unknowns distinct.

**Codex is the primary, full adapter. Claude Code is the first secondary adapter.** Both package the same AI-tool-independent core protocol.

<p align="center">
  <strong><a href="#start-with-codex">Map your first repository with Codex →</a></strong>
</p>

> [!IMPORTANT]
> Project Atlas is a community project, not an official OpenAI or Anthropic product. Review every generated atlas before making important changes. An atlas complements runtime tests, monitoring, backups, incident procedures, security review, and human judgment; it does not replace them.

## Why trust the output

- Material claims are source-linked and explicitly classified as `CONFIRMED`, `INFERENCE`, `HYPOTHESIS`, `TARGET`, or `UNKNOWN`.
- Completion validation checks the artifact structure, safe-inventory source membership, and supported bounded command replay. It does not claim that natural-language conclusions are true.

### Reproducible effectiveness

For v0.1.0, “effective” means the protocol can produce a contractually complete, source-bounded, adapter-stable, and safely installable result. The [release suite](./tests/) tests those mechanics across the configured [CI matrix](./.github/workflows/ci.yml):

- **Completion contracts:** QUICK, STANDARD, and FORENSIC accept substantive fixture atlases and reject incomplete or structurally spoofed output. Evidence: [CLI contract tests](./tests/test_atlas_cli.py) and [public fixture oracles](./tests/oracles/).
- **Source boundaries:** unsafe, ignored, symlinked, out-of-bound, and CommonMark-hidden references are rejected instead of treated as evidence. Evidence: [security regression tests](./tests/test_atlas_security.py).
- **Adapter stability:** Codex and Claude Code carry byte-identical canonical skill payloads, with drift detected before release. Evidence: [adapter packaging tests](./tests/test_adapter_packaging.py).
- **Installer transactions:** isolated-path tests cover overwrite refusal, bounded preflight, backup, rollback, and interruption behavior for both standalone installers. Evidence: [installer transaction tests](./tests/test_installers.py).

<details>
<summary><strong>Run the proof checks</strong></summary>

```bash
python3 -m unittest tests.test_atlas_cli
python3 -m unittest tests.test_atlas_security
python3 scripts/sync_adapters.py --check
python3 -m unittest tests.test_installers
```

</details>

> [!NOTE]
> The repository does not yet contain a controlled benchmark proving faster onboarding, fewer incidents, or lower maintenance cost. Those are measurable product hypotheses, not v0.1.0 facts.

## How Atlas works

<p align="center">
  <img src="./assets/readme/workflow.svg" width="100%" alt="Project Atlas workflow: bound discovery, classify evidence, select depth, and deliver a validated atlas with a handoff">
</p>

The workflow is deliberately stricter than “ask an agent to explain the repo”:

1. **Bound discovery** before reading deeply: apply repository instructions, exclusions, privacy limits, and resource ceilings.
2. **Classify every material claim** as `CONFIRMED`, `INFERENCE`, `HYPOTHESIS`, `TARGET`, or `UNKNOWN`.
3. **Choose depth by cost of error**, not file count alone.
4. **Validate the output contract** and leave a continuation handoff another engineer or supported agent can reopen.

| Depth | Use it for | Output |
| --- | --- | --- |
| **QUICK** | Small, low-risk, short-lived projects | One `PROJECT_ATLAS.md` |
| **STANDARD** | Active applications, services, and libraries | 12 routed current-state, flow, risk, target, migration, and handoff documents |
| **FORENSIC** | Critical, legacy, multi-service, authority-heavy, or high-risk systems | 13 routed mode artifacts plus generated `SOURCE_SNAPSHOT.json` at completion |

See the full [depth rules](./docs/depth-levels.md) and [output contract](./docs/outputs.md).

## Start with Codex

Add the marketplace and install the primary adapter:

```bash
codex plugin marketplace add ddenny-s/project-atlas
codex plugin add project-atlas@project-atlas
```

Start a fresh Codex session if the skill is not yet visible. Open the repository you want to map in Codex, or start Codex from that repository's root. Then run:

```text
Use $project-atlas:map-project to create and validate a QUICK atlas of this repository.
```

That first successful run should leave one source-linked `PROJECT_ATLAS.md` without changing product code. Its completion validator returns:

```json
{"artifacts": 1, "mode": "QUICK", "status": "valid", "validation": "completion"}
```

The document records the inspected boundary, evidence snapshot, exact replayable check, project-relative citations, risks, unknowns, and next safe action. Move to STANDARD when you need routed current/target architecture and migration artifacts; use FORENSIC when missing a relationship could cause material harm.

### Claude Code

Install the secondary adapter from the same repository:

```bash
claude plugin marketplace add ddenny-s/project-atlas
claude plugin install project-atlas@project-atlas
```

Invoke its namespaced skill:

```text
/project-atlas:map-project
```

The wording, evidence labels, depth semantics, filenames, validation rules, and handoff contract remain the same across both adapters. Host permissions and available tools may differ; those gaps stay explicit.

## What an atlas answers

An atlas is useful when you need to establish:

- what the product does and which UI, API, CLI, worker, queue, cron, webhook, or provider runtimes produce observable outcomes;
- where data enters, leaves, persists, changes state, gains an authoritative writer, or crosses a human, automated, administrative, or provider boundary;
- how product flows handle retries, idempotency, rollback, recovery, and partial states;
- what tests and runtime observations prove, what remains unknown, and which implementations conflict or are obsolete;
- how current architecture can move toward a safer target, in what sequence, and what the next engineer should verify first.

It is usually excessive for a tiny, obvious script when one README and one verification command already answer the important questions.

## Trust model

Project Atlas keeps five claim kinds distinct:

| Claim kind | Meaning |
| --- | --- |
| `CONFIRMED` | Directly supported by current primary evidence |
| `INFERENCE` | Reasoned from named evidence, but not directly observed |
| `HYPOTHESIS` | A testable explanation still awaiting evidence |
| `TARGET` | A proposed future state, never a current fact |
| `UNKNOWN` | A named gap whose answer is not established |

Material current-state claims point to the strongest available project-relative source or reproducible command evidence. Green tests prove only the exercised behavior. Current and target architecture live in separate artifacts.

Read the [evidence model](./core/skill/map-project/references/evidence-model.md), [limits and safety](#limits-and-safety), and [SECURITY.md](./SECURITY.md) before using an atlas for high-impact decisions.

## Installation and lifecycle

Marketplace installation is the primary distribution path. Standalone installers are available for local skill directories on macOS and Linux.

<details>
<summary><strong>Codex marketplace: inspect, update, and remove</strong></summary>

Inspect the installed plugin:

```bash
codex plugin list --marketplace project-atlas --json
```

Refresh and reinstall:

```bash
codex plugin marketplace upgrade project-atlas
codex plugin remove project-atlas@project-atlas
codex plugin add project-atlas@project-atlas
```

Remove the plugin and marketplace:

```bash
codex plugin remove project-atlas@project-atlas
codex plugin marketplace remove project-atlas
```

</details>

<details>
<summary><strong>Claude Code marketplace: inspect, update, and remove</strong></summary>

```bash
claude plugin details project-atlas@project-atlas
claude plugin marketplace update project-atlas
claude plugin update project-atlas@project-atlas
```

Restart Claude Code after an update. To remove it:

```bash
claude plugin uninstall project-atlas@project-atlas
claude plugin marketplace remove project-atlas
```

</details>

<details>
<summary><strong>Standalone Codex and Claude Code skills</strong></summary>

Clone once:

```bash
git clone https://github.com/ddenny-s/project-atlas.git
cd project-atlas
```

Install the standalone Codex skill into `$HOME/.agents/skills/map-project`:

```bash
./scripts/install.sh --user-scope
```

Invoke it as `$map-project`. Update explicitly:

```bash
git pull --ff-only
./scripts/install.sh --user-scope --force
```

Install the standalone Claude Code skill into `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/map-project`:

```bash
./scripts/install-claude.sh
```

Invoke it as `/map-project`. Update explicitly:

```bash
git pull --ff-only
./scripts/install-claude.sh --force
```

Both installers refuse silent overwrite. Forced updates preserve the previous tree under the corresponding `.skill-backups/project-atlas/` directory. See [adapter architecture and standalone lifecycle](./docs/adapters.md) before manual recovery or removal.

To remove only verified standalone Project Atlas copies, run this interactive check. It prints the exact targets, verifies Project Atlas markers, and requires a typed `REMOVE` before deleting anything:

```bash
codex_skill="${HOME:?}/.agents/skills/map-project"
claude_root="${CLAUDE_CONFIG_DIR:-${HOME:?}/.claude}"
claude_skill="$claude_root/skills/map-project"
printf 'Codex: %s\nClaude Code: %s\n' "$codex_skill" "$claude_skill"

verified_skills=()
for skill in "$codex_skill" "$claude_skill"; do
  if test -d "$skill" &&
     test ! -L "$skill" &&
     test -f "$skill/SKILL.md" &&
     test ! -L "$skill/SKILL.md" &&
     grep -q '^name: map-project$' "$skill/SKILL.md" &&
     grep -q 'Project Atlas' "$skill/SKILL.md"; then
    printf 'Verified Project Atlas skill: %s\n' "$skill"
    verified_skills+=("$skill")
  else
    printf 'Skipped unverified or absent path: %s\n' "$skill"
  fi
done

test "${#verified_skills[@]}" -gt 0 || exit 1
printf '%s' 'Type REMOVE to delete the verified paths: ' >&2
IFS= read -r confirmation
test "$confirmation" = 'REMOVE' || exit 1
for skill in "${verified_skills[@]}"; do
  rm -r -- "$skill"
done
```

Backups remain separate under the corresponding `.skill-backups/project-atlas/` directory for explicit inspection and cleanup.

</details>

## Useful prompts

### Map before a refactor

```text
Use $project-atlas:map-project to build a STANDARD current-state and target-state atlas before we refactor this service.
```

### Investigate a critical legacy system

```text
Use $project-atlas:map-project in FORENSIC mode. Map every runtime root, data store, state writer, authority boundary, recovery path, and test gap. Do not implement changes until I approve the atlas.
```

### Refresh and hand off

```text
Use $project-atlas:map-project to refresh the existing atlas incrementally, report drift, and prepare a continuation handoff for another session.
```

Claude Code uses the same intent with `/project-atlas:map-project`. More examples are in [docs/examples.md](./docs/examples.md).

## Limits and safety

- Excluded paths, secrets, credentials, private dumps, and forbidden directories stay outside discovery. Mapping does not authorize code changes, production operations, deployments, or data mutation.
- Metadata and bounded reads come before broad content access. Memory pressure, disk headroom, one-heavy-process execution, session ownership, and media retention are governed explicitly.
- Source code, green tests, and deterministic validation do not prove production behavior, natural-language entailment, complete coverage, or reviewer identity.
- Dynamic dispatch, generated code, runtime configuration, external services, and later project changes can hide paths or make an atlas stale.
- The filesystem helper and standalone installers require safe POSIX descriptor and no-follow support: they run on macOS and Linux and fail closed on native Windows. The protocol can still be followed there with bounded host-native tools when the gap is recorded.
- FORENSIC replay needs a writable temporary directory for its allowlisted mirror. Host permissions still define what can be inspected; inaccessible evidence remains `UNKNOWN`.

<details>
<summary><strong>Operational ceilings and recovery boundaries</strong></summary>

Safe-inventory traversal is bounded to 100,000 files, 20,000 directories, depth 64, and 16 MiB of UTF-8 relative-path bytes. Serialized JSON is bounded to 8 MiB. Git ignore classification runs against an isolated temporary worktree with bounded output and a 15-second deadline; source `.git` metadata and user-level excludes are not evidence inputs.

Standalone installation is bounded to 2,048 files, 512 directories, depth 32, 4 MiB per file, and 64 MiB total. Adapter synchronization is bounded to depth 32, 2,048 directories, 4,096 files, 8,192 entries, 8 MiB per file, and 64 MiB total.

Trappable installer and synchronization failures attempt rollback. `SIGKILL`, power loss, or filesystem durability failure can still leave a stale lock, staging tree, backup, or ambiguous target. Inspect the target, backup, staging, lock, and journal state before recovery; never infer authority from modification time alone.

These mechanisms protect public paths from ordinary concurrent writers. They cannot sandbox a malicious same-account process with direct filesystem access.

</details>

## Development

The canonical workflow lives under [`core/`](./core/). Host adapters package it without changing semantics. After editing the core, synchronize adapters and run the release checks:

```bash
python3 scripts/sync_adapters.py
python3 scripts/sync_adapters.py --check
python3 -m unittest discover -s tests -v
git diff --check
```

Before publishing, maintainers separately verify disposable clean profiles, path-with-spaces behavior, manifests, output contracts, all three forward scenarios, and independent correctness and security review. This manual clean-profile gate is required before creating the version tag or GitHub Release and is not claimed as a GitHub Actions check. CI checks are necessary evidence, not the whole release decision.

Read [CONTRIBUTING.md](./CONTRIBUTING.md), [adapter architecture](./docs/adapters.md), and [ACKNOWLEDGEMENTS.md](./ACKNOWLEDGEMENTS.md) before proposing protocol or packaging changes.

## License

Project Atlas is available under the [MIT License](./LICENSE).
