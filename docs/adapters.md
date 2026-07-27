# Adapter Architecture

Project Atlas separates a host-independent protocol from host-specific distribution and invocation. This lets the evidence model evolve once while Codex, Claude Code, and future AI tools integrate through small adapters.

## Layer model

```text
User request
    ↓
Host adapter: invocation, manifests, discovery, permissions, packaging
    ↓
Core protocol: scope, depth, evidence, workflow, outputs, safety
    ↓
Repository and approved runtime evidence
    ↓
Atlas artifacts
```

The core owns meaning. An adapter owns translation into a host's supported surfaces.

## Repository layout

```text
core/skill/map-project/              Canonical workflow and resources
adapters/codex/                      Primary Codex plugin package
adapters/claude-code/                Secondary Claude Code plugin package
.agents/plugins/marketplace.json     Codex marketplace catalog
.claude-plugin/marketplace.json      Claude Code marketplace catalog
scripts/                             Packaging and installation mechanics
tests/                               Core and adapter conformance checks
```

Public documentation stays outside plugin packages. Runtime adapter packages contain only the files needed by that host.

## Core responsibilities

The core defines:

- evidence classes and traceability;
- QUICK, STANDARD, and FORENSIC selection and meaning;
- safe progressive discovery;
- runtime, data, state, authority, flow, recovery, and test analysis;
- current-state versus target-state separation;
- output routing and artifact contracts;
- incremental refresh, drift, and handoff semantics;
- privacy and non-destructive behavior.

The core must not depend on a `$` mention, slash command, host-specific home directory, plugin cache, manifest field, or proprietary tool name.

## Adapter responsibilities

An adapter may define:

- plugin and marketplace manifests;
- namespaced invocation syntax;
- host-specific skill metadata;
- capability mapping for search, reads, commands, writes, and delegation;
- permission and approval guidance;
- installation, update, removal, and discovery checks;
- packaging required to make the plugin self-contained.

An adapter must not:

- rename evidence classes or change their proof requirements;
- reinterpret a depth level;
- weaken exclusions, privacy, or non-destructive defaults;
- claim evidence unavailable to the host;
- change the output contract without a corresponding core change;
- rely on paths outside its installed package at runtime.

Plugin hosts can copy installed packages into a cache. Release packaging therefore includes the required core files inside each adapter rather than referencing `../core` at runtime. Conformance tests detect drift between canonical core content and packaged adapters.

## Generated host guidance

`MODEL_GUIDANCE.md` is the adapter-owned source for dated model, reasoning, question UI, and quota-display guidance. `scripts/sync_adapters.py` copies each source byte-for-byte to the installed skill as `references/host-guidance.md`; the generated copy must not be edited by hand. That file is the only intentionally different file at a common payload path between Codex and Claude Code. Codex additionally owns `agents/openai.yaml`, which is Codex-only metadata rather than a common path. Every other shared payload file remains byte-identical across the two adapters, and the exact-tree sync still rejects missing, changed, or orphan files. Core stays vendor-neutral.

Each source is marked `checked_at: 2026-07-26`, `PROVIDER_BASED_STARTING_POINT`, and `NOT_YET_ATLAS_BENCHMARKED`. The matrix is therefore a dated provider-based default to evaluate, not a claim that Atlas has measured an optimum. Run Economics records both the selected label or alias and the effective resolved model because host aliases, provider routing, organization access, and availability move.

The host interaction rules preserve one user-visible contract:

- ask 1-3 questions per interaction, with an unlimited total count governed by a semantic stop rather than a numeric cap;
- show exactly four visible choices for every question;
- use plain-chat A-D unless a native picker can preserve the exact four-choice contract without adding another control; D is exactly `Другое — напишу сам`;
- the Claude Code adapter makes no native-picker claim and always documents the plain-chat A-D fallback;
- accept `не знаю` without guessing;
- show an exact weekly remaining percentage only when the host supplies the exact weekly bucket, timestamp, and source; otherwise omit the quota line entirely.

The adapter never converts tokens into quota. A token forecast and a host usage bucket are different evidence.

## Codex adapter

Status: **primary and full adapter**.

The Codex package uses a `.codex-plugin/plugin.json` manifest and exposes the skill through the repository's Codex marketplace catalog. Its explicit invocation is:

```text
$project-atlas:map-project
```

The plugin manifest and generated `agents/openai.yaml` use the same mode-neutral default prompt. QUICK, STANDARD, or FORENSIC is selected by the skill from scope and owner answers rather than forced by packaging.

The [dated Codex source](../adapters/codex/MODEL_GUIDANCE.md) starts QUICK/read-heavy work on GPT-5.6 Terra with `medium` reasoning, STANDARD on GPT-5.6 Sol with `high`, and FORENSIC/adversarial work on GPT-5.6 Sol with `xhigh`. `max` is reserved for the hardest quality-first block only after a representative eval shows a material gain and the user has seen the token budget; neither `max` nor `ultra` is a global default. The cited provider sources are the [Codex usage guide](https://learn.chatgpt.com/docs/pricing#what-are-the-usage-limits-for-my-plan) and [current GPT-5.6 guidance](https://developers.openai.com/api/docs/guides/latest-model#using-gpt-5-6).

The adapter should map the full core contract, including adaptive depth, scripts, output templates, incremental refresh, traceability validation, and handoff. Codex-specific metadata and model guidance belong in the adapter package, not in the protocol.

Installation and lifecycle commands are documented in the [main README](../README.md).

## Claude Code adapter

Status: **secondary adapter and first portability target**.

The Claude Code package uses a `.claude-plugin/plugin.json` manifest and the compatible root marketplace catalog. Its explicit invocation is:

```text
/project-atlas:map-project
```

It must produce atlas artifacts with the same evidence labels, mode semantics, filenames, traceability, and safety behavior as the Codex adapter. Host-specific differences in discovery, permissions, context management, or subagents are recorded as adapter limitations, not hidden behind equivalent-looking claims.

The [dated Claude Code source](../adapters/claude-code/MODEL_GUIDANCE.md) starts QUICK on `sonnet` with `high` effort, STANDARD on `opus` with `high`, and FORENSIC/adversarial work on `best` with `xhigh`. At the recorded date, `best` uses Fable 5 when the organization has access to it and otherwise uses the latest Opus model. On the Anthropic API and Claude Platform on AWS, `opus` resolves to Opus 5. Alias resolution varies by provider and updates over time; the provider table differs elsewhere. Opus 5 requires Claude Code v2.1.219 or later. A recorded Claude Code v2.1.207 run is historical evidence rather than the current alias contract: at that version, `opus` resolved to Opus 4.8 on the Anthropic API and Claude Platform on AWS. Run Economics therefore records the selected alias, provider, effective resolved model, and Claude Code version.

Anthropic recommends each model's default effort as the general starting point: use a larger model for a genuinely harder or more ambiguous problem, and raise effort when the model skipped files, tests, or verification. `max` remains an exceptional evaluated choice rather than a default. `ultracode` is session-only and runs `xhigh` plus dynamic workflows for substantive tasks. It is optional for a hardest substantive block only when workflows are available and the extra usage is explicitly budgeted; it is neither an Atlas default nor a global setting. The `--effort ultracode` form is supported in Claude Code v2.1.203 and later. The exact `ultrathink` keyword requests one-off deeper reasoning with API effort unchanged. `haiku` is limited to bounded extraction or classification and never owns a whole Atlas run. Codex remains the primary adapter. See Anthropic's current [model configuration](https://code.claude.com/docs/en/model-config), [model and effort guidance](https://claude.com/blog/claude-model-and-effort-level-in-claude-code), and [dynamic workflows guidance](https://claude.com/blog/introducing-dynamic-workflows-in-claude-code).

### Claude Code capability contract

| Host surface | Claude Code mapping | Proof boundary or limitation |
| --- | --- | --- |
| Repository instructions | Claude Code discovers the installed `map-project` skill and applies repository instruction files through its native instruction hierarchy. | Project Atlas does not bypass or independently authenticate host instruction discovery; record missing or conflicting instructions as a scope limit. |
| Search and bounded reads | The skill directs Claude Code to use its repository search and read tools while the bundled helper supplies the same bounded inventory, validation, and replay contract as core. | Host tool permissions still decide which paths are readable. Inaccessible evidence stays `UNKNOWN`; the adapter grants no additional access. |
| Permission boundary | Commands, writes, network use, and runtime observation remain subject to Claude Code's active permission settings and user approvals. | The plugin manifest does not grant shell, network, production, or secret access. A map-only request never authorizes product mutation. |
| Context and handoff | Routed Atlas files and `LIVE_HANDOFF.md` are the durable context boundary between sessions; hidden conversation state is not evidence. | Context compaction and session limits are host behavior. Continue from artifacts and re-open cited sources when freshness matters. |
| Independent review | Use a fresh Claude Code context or an external reviewer and record the declared separation in the FORENSIC review table. | Project Atlas validates the record and digest, not reviewer identity or actual subagent independence. |
| Installation and discovery | The Claude marketplace manifest and plugin package expose `/project-atlas:map-project`; the standalone installer exposes `/map-project`. | Native Windows standalone installation is unsupported because the transactional helper requires POSIX descriptor primitives. |

The release tests byte-equivalence of the shared protocol, Claude manifest and marketplace shape, strict Claude CLI validation when available, isolated plugin/standalone installation, and the common output contracts. No automated end-to-end Claude task execution is claimed by the repository CI; semantic parity is established through the shared contract plus independent synthetic forward tests and must be rechecked when Claude Code host behavior changes.

### Claude Code standalone lifecycle

Run `./scripts/install-claude.sh` to install the standalone skill at `$CLAUDE_CONFIG_DIR/skills/map-project`, or at `$HOME/.claude/skills/map-project` when `CLAUDE_CONFIG_DIR` is unset. A later run refuses to overwrite that directory. Use `./scripts/install-claude.sh --force` for an explicit update; the installer preserves the previous tree under `$CLAUDE_CONFIG_DIR/.skill-backups/project-atlas/` (or the corresponding `$HOME/.claude` directory) before publishing the replacement.

To remove the standalone skill, delete only the `skills/map-project` directory after confirming its location. Backups are intentionally separate and remain until inspected and removed explicitly. The installer does not require `sudo`, edit shell profiles, or change the Claude Code plugin marketplace installation.

Both standalone installers require Python 3.10 or newer, Bash, `diff`, and the POSIX descriptor primitives available on macOS and Linux. Native Windows standalone installation is unsupported. Each packaged tree is bounded to 2,048 files, 512 directories, depth 32, 4 MiB per file, and 64 MiB total. Every external `diff` verification has a 30-second ceiling.

## Capability mapping

| Protocol capability | Adapter obligation |
| --- | --- |
| Project instructions | Discover and apply the host's repository instruction hierarchy before analysis |
| Structural index | Use safe file enumeration and metadata without opening excluded content |
| Search | Prefer the host's efficient repository search; preserve path exclusions |
| Bounded read | Read only the context needed to support a claim |
| Commands | Use non-destructive commands within host approvals and report exact outcomes |
| Runtime observation | Require explicit access and label environment, time, and proof boundary |
| Artifact writes | Preserve worktree changes and refuse silent overwrite |
| Independent review | Use fresh review context when the host supports it; otherwise record the gap |
| Citations | Emit stable repository-relative sources and reproducible command references |
| Handoff | Produce the same continuation contract across hosts |

Canonical core has no AI-host-specific discovery default. The Codex and Claude Code adapter packages inject bounded searches for the standalone Codex roots, the legacy Codex skill root, Codex plugin cache, standalone Claude Code root, and Claude Code plugin cache. Set `PROJECT_ATLAS_SCRIPT` to one exact helper when several installed copies exist, or set `PROJECT_ATLAS_SEARCH_ROOTS` to a colon-separated bounded root list. Zero or multiple matches fail closed.

## Adding a future adapter

A new adapter is acceptable when it:

1. Documents its invocation, discovery, permissions, packaging, and lifecycle.
2. Packages every runtime dependency inside the host's install boundary.
3. Passes the shared core and output-contract tests.
4. Passes synthetic QUICK, STANDARD, and FORENSIC scenarios.
5. Demonstrates incremental refresh without destroying user additions.
6. Preserves exclusions and does not disclose fixture secrets.
7. Labels unsupported capabilities and resulting unknowns.
8. Produces a handoff another supported host can understand.
9. Receives an independent correctness and security review.

Adapter maturity should be stated plainly as experimental, secondary, or full. Compatibility means semantic conformance, not identical internal tool calls.

Standalone installers serialize writes with an atomic per-target lock and keep forced-update backups outside auto-discovered `skills/` directories. One run-specific nonce marks the lock, staging root, and promoted target. The prior installation is not marked or rewritten: a descriptor-relative manifest records its exact payload before the no-replace move to backup. The installer checks identity plus the applicable nonce marker or manifest before and after atomic moves and cleanup quarantine. Its public target keeps the marker through payload verification, and marker removal is the commit cutpoint. If any proof changes or cannot be re-established, cleanup or restore fails closed and leaves the ambiguous state for manual inspection.

A verifier timeout or trappable error/signal terminates the active verifier process group and attempts rollback. An untrappable `SIGKILL`, power loss, or filesystem durability failure can instead leave a stale lock or staging tree, a missing target with the previous copy in `.skill-backups/project-atlas/`, or an ambiguous target/backup state; there is no installer recovery journal. Confirm no installer is active, inspect all three locations, remove only a verified empty stale lock with `rmdir`, and restore the identified backup or retry deliberately.

FORENSIC command replay materializes only allowlisted evidence files in an operating-system temporary directory before running bounded `rg`. It therefore requires a writable temporary area even though product sources remain read-only; a fully read-only sandbox fails closed before replay.

`sync_adapters.py` takes a repository-anchored exclusive lock, prepares and verifies both staged bundles, then commits them as one journaled transaction. The tree ceiling is depth 32, 2,048 directories, 4,096 files, 8,192 total entries, 8 MiB per file, and 64 MiB total. The v2 journal records transaction identity, per-adapter ownership nonces, `prepared_tree_sha256`, and original-tree digests; a separate commit receipt proves when finalization may proceed. Before cleanup or quarantine, the new prepared tree must match both its journal-bound role marker and `prepared_tree_sha256`; the prior tree must match the corresponding marker and recorded original-tree digest. Proof is checked again after atomic quarantine. A missing or changed marker or payload therefore preserves the state for manual inspection rather than authorizing deletion. Recovery also verifies the journal-bound original bytes before rollback can restore an old adapter and uses private marker quarantine before deleting retired markers. A markerless cleanup quarantine, damaged state file, v1 journal, orphan receipt, or missing journal after power loss requires manual inspection rather than automatic deletion. Write-mode synchronization uses descriptor-relative, no-replace primitives on macOS and Linux and fails closed on native Windows. `scripts/sync_adapters.py --check` is a state-free, read-only comparison that does not create `.scratch`, locks, staging paths, or a journal; it fails closed when unfinished state exists. Require it to pass before committing or publishing. CI enforces the check on Python 3.10 and 3.13 across macOS, Ubuntu, and the portable Windows contract job.

## External interface references

Packaging follows the public host interfaces documented by [OpenAI Build plugins](https://learn.chatgpt.com/docs/build-plugins), [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills), [Anthropic Create plugins](https://code.claude.com/docs/en/plugins), and [Anthropic plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces). Project Atlas remains a community project and is not endorsed by either vendor.
