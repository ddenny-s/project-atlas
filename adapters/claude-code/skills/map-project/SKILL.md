---
name: map-project
description: Investigate an unfamiliar, inherited, legacy, or changing software project and create or refresh an evidence-backed Project Atlas. Use for project mapping, architecture audits, refactor preparation, source-of-truth discovery, runtime/data/state/authority documentation, risk and migration analysis, continuation handoffs, and incremental atlas drift checks.
---

# Map Project

Create a bounded, evidence-backed map that another investigator can navigate and continue. Map the product and its behavior; do not merely summarize files.

## Establish scope and authority

1. Read the user's request and every applicable project instruction before discovery.
2. State the investigation boundary, forbidden paths, allowed runtime access, intended output location, and observable completion criteria.
3. Treat the request as read-only for product code, configuration, dependencies, data, and production unless the user explicitly authorizes more.
4. Inspect an existing atlas before writing. Preserve user text and update it incrementally.
5. Keep unrelated dirty-worktree changes untouched. Check for adjacent writers before updating shared atlas files.

## Govern host resources and collaboration

Before a heavy scan, test suite, index, media pass, or model pass, check memory pressure, swap trend, responsiveness, free disk, and active model and terminal sessions. Do not use free memory as the only signal. On a constrained host, run one heavy process at a time and keep other passes short and bounded. If pressure rises, stop launching work, stop only your own heavy processes, release idle sessions, wait for recovery, and resume in smaller chunks.

Never stop a production runtime, database, container runtime, or virtualization layer without explicit owner approval, proven ownership, and a restart plus health-check plan. Never delete volumes or authoritative originals as cleanup. Do not introduce a remote runner or transfer project material off-host without an explicit approval envelope covering the host, files, restricted data, retention, result, cleanup, and cost.

Give each parallel worker an isolated scope, permitted paths, role, exact output, forbidden actions, lifetime, and handoff target. Keep one writer for each canonical document, obey the owner-defined session limit, reuse sessions, and release them when their scope ends. Choose the capability tier by semantic and security risk. Give independent auditors exact frozen bytes they did not author.

Read [investigation-workflow.md](references/investigation-workflow.md) for the detailed resource, storage, retention, and collaboration procedure before any non-trivial investigation.

## Select depth

Honor an explicit `QUICK`, `STANDARD`, or `FORENSIC` request. Otherwise run:

```text
PYTHONDONTWRITEBYTECODE=1 python3 <skill-dir>/scripts/atlas.py select-mode --project <project>
```

Explain the selected mode using risk signals, not file count alone. Keep support contours in the safe inventory, but do not treat vocabulary in tests, fixtures, templates, examples, or nested documentation as product topology or high-impact risk by itself. Read [depth-selection.md](references/depth-selection.md) when signals conflict or the project sits near a mode boundary.

Every completed atlas records exactly one `Selected by`, `Conflicting automatic signals`, `Intentionally omitted coverage`, and `Escalation condition` field. Put them in QUICK's `Scope and Depth Rationale` section or the routed `ATLAS_INDEX.md` `Scope and Coverage` section. Explain an absence such as a matching automatic recommendation; do not use a bare sentinel.

Use these output shapes:

- `QUICK`: create exactly one `PROJECT_ATLAS.md`; its one bounded `rg --no-config` verification command is replayed at completion.
- `STANDARD`: create the routed current-state, flow, quality, target-state, migration, unknowns, and handoff set.
- `FORENSIC`: create the full routed set plus traceability, quantitative coverage, source hashes, and independent review.

## Build a safe structural index

Run the local inventory before broad content reads:

```text
PYTHONDONTWRITEBYTECODE=1 python3 <skill-dir>/scripts/atlas.py inventory --project <project> --output <scratch>/inventory.json
```

Treat this safe inventory as the only readable and hashable baseline list. Never run a broad `find`, recursive glob, checksum, or content hash across the project to establish drift: that can open excluded material. Compare excluded contours by relative path metadata only, without opening or reading their contents.

The helper rejects symbolic and hard links, oversized sources, and any identity or metadata mutation observed across a read or hash. Ignored generated replay descendants are pruned without reading their contents. Do not replace these checks with path-only reads.

Classify project-local `.gitignore` matches with exact `git check-ignore --no-index` only inside the helper's isolated mirror of stable ignore-file copies and candidate path metadata. Do not let Git discover or read source `.git` metadata, `info/exclude`, local/worktree/global/system config, or an external `core.excludesFile`; those are outside the authorized inventory boundary. For custom `.ignore` metadata, preserve Git-style escaped pattern characters and trailing-space semantics; if in-scope ignore metadata is unreadable, malformed, or uses an unsupported escape, fail closed before reading or hashing affected paths.

Use `rg --files` and `rg` for follow-up routing when available. Restrict reads to the contour under investigation. Exclude ignored and forbidden paths, secrets, credentials, private keys, dumps, local databases, dependency/vendor trees, generated outputs, build artifacts, and version-control internals. Do not follow symbolic links outside the project. Do not use the network without explicit authority.

Read [investigation-workflow.md](references/investigation-workflow.md) before a STANDARD or FORENSIC investigation, whenever runtime, state, or authority spans multiple contours, or whenever a pass is resource-sensitive.

## Initialize without overwriting

Create only missing atlas files:

```text
PYTHONDONTWRITEBYTECODE=1 python3 <skill-dir>/scripts/atlas.py init --project <project> --mode <mode> --output <atlas-dir>
```

For QUICK, use the project root as the output directory unless the user chooses another directory. For STANDARD and FORENSIC, prefer `docs/project-atlas/` when `docs/` already contains project documentation; otherwise use `project-atlas/`.

Never replace an existing file during initialization. Read [output-contract.md](references/output-contract.md) before changing the routed document set or traceability schema.

## Investigate one contour at a time

For each contour:

1. Identify the user or system trigger and the observable outcome.
2. Trace the runtime boundary, validation, authority decision, domain behavior, state reads and writes, external effects, response, retry, partial state, rollback, and recovery.
3. Check sibling runtimes, shared writers, alternative configurations, legacy paths, and tests of the same contract.
4. Record the result immediately with source references.
5. Verify the cited sources and mark gaps `UNKNOWN`.
6. Refresh `LIVE_HANDOFF.md` before moving to the next contour.

Map at least:

- product purpose, users, requirements, and outcomes;
- UI, API, CLI, worker, queue, cron, webhook, and trigger entry points;
- data sources, sinks, stores, state objects, readers, writers, and transitions;
- human, automated, administrative, and provider authority;
- configuration, feature flags, environments, and external dependencies;
- tests, runtime observations, and exact proof boundaries;
- security, reliability, privacy, cost, observability, and data-loss risks;
- duplicate, obsolete, conflicting, and unused implementations;
- keep, merge, rewrite, or delete dispositions;
- target architecture, migration sequence, verification gates, and rollback.

Explain why each material component exists and what changes if it is removed. Use diagrams only when they materially clarify runtime, data, state, authority, flows, or migration.

Run Python probes and tests with `PYTHONDONTWRITEBYTECODE=1`. Prefer an excluded scratch directory for all probe outputs. Compare the product tree before and after runtime inspection; remove only artifacts proven to have been created by the current probe, and never delete pre-existing user files.

## Keep claims honest

Read [evidence-model.md](references/evidence-model.md) before recording material findings or editing `TRACEABILITY.tsv`.

Classify every material claim as `CONFIRMED`, `INFERENCE`, `HYPOTHESIS`, `TARGET`, or `UNKNOWN`. Cite the strongest current primary source with a relative path and line, schema, configuration, test, command, runtime observation, or primary external document. Record evidence freshness.

Use only `ACTIVE`, `CURRENT`, `STALE`, or `SUPERSEDED` for trace status. `UNRESOLVED` supports only `UNKNOWN`, and dates must be real `YYYY-MM-DD` values or UTC `YYYY-MM-DDTHH:MM:SSZ` timestamps. Invalid source/kind/status combinations cannot satisfy completion.

Keep current architecture separate from target architecture. Do not treat green tests as proof of production behavior or complete coverage. Do not convert missing evidence into a plausible narrative.

For every quantitative claim, enumerate the members first, state the denominator and exclusions, then recount the narrative number against that list. Before completion, run a contradiction pass: try to disprove each P0/P1 finding, authority claim, and counted claim at its cited sources. Put every requirement in the canonical requirements table with an explicit claim kind; future controls are `TARGET`, not current requirements. In FORENSIC mode, put each completeness claim in `ATLAS_INDEX.md`, each unknown in `OPEN_UNKNOWNS.md`, and use the claim-kind-bearing migration table.

For every material FORENSIC registry claim, add an exact `atlas_refs` link from `TRACEABILITY.tsv`; a reused `fact_id` is not a link. Keep references unique and lexically sorted, separated by semicolons, and use `-` only when the fact supports no material registry row. The ledger `claim_kind` and `claim` must exactly match the referenced canonical claim. Findings require separate links for `/finding` and `/disposition`.

For QUICK verification and `COMMAND` traceability, record a bounded `rg --no-config` command that was actually run against explicit safe-inventory targets, quote shell globs, avoid prose or pseudo-commands, and keep the working directory project-relative. A directory target or multiple targets require exact `--sort path`; reverse or metadata-based sort modes are not completion evidence. Record notes as `cwd=<relative>; exit=<integer>; stdout_sha256=<64 hex>`. If the command was not executed or its result was not captured, use `UNKNOWN` instead of `CONFIRMED`. FORENSIC completion requires at least one active `COMMAND` row.

## Validate and hand off

The bundled helper requires Python 3.10 or newer and safe POSIX directory-descriptor primitives. It runs on macOS and Linux and fails closed on native Windows; record the gap and use equally bounded host-native tools there.

Validate the declared output contract:

```text
PYTHONDONTWRITEBYTECODE=1 python3 <skill-dir>/scripts/atlas.py validate --atlas <atlas-dir> --project <project> --mode <mode>
```

This default command applies completion gates. During initial scaffolding only, add `--draft`; never report a draft validation as completion.

For FORENSIC, replay bounded ripgrep evidence and compare its captured exit code and standard-output digest:

```text
PYTHONDONTWRITEBYTECODE=1 python3 <skill-dir>/scripts/atlas.py validate --atlas <atlas-dir> --project <project> --mode FORENSIC --replay-command-evidence
```

For FORENSIC, snapshot every validated active evidence source: `FILE`, `SCHEMA`, `CONFIG`, and `TEST` traceability references plus the allowlisted file members resolved from active `COMMAND` targets:

```text
PYTHONDONTWRITEBYTECODE=1 python3 <skill-dir>/scripts/atlas.py snapshot --atlas <atlas-dir> --project <project> --output <atlas-dir>/SOURCE_SNAPSHOT.json
```

The snapshot's `files` population is exactly the non-empty union of distinct completion-active file-like references and explicit allowlisted `COMMAND` target members. A directory target expands only to its safe-inventory members under the replay count and byte ceilings; unrelated allowlisted contents are not opened. Its safe-inventory manifest hashes relative path names only. `evidence_scope.unique_evidence_files` and `hashed_files` record this exact population. Snapshot v0.2 binds this source scope and all canonical non-review atlas content in `review_input.sha256`, while binding review records separately. After the evidence scope is stable, retain one fresh-context or external `CORRECTNESS` review and one `SECURITY` review from distinct reviewers. Both bind to `review_input.sha256`, record `PASS`, `0` Critical, `0` Important, concrete retained evidence, remaining limits, a UTC time no earlier than the evidence boundary, no more than seven days later, and no more than five minutes ahead of the validating host clock, plus exact ledger coverage. Refresh the snapshot after adding review ledger rows; any non-review content change requires both reviews again. Wall-clock age alone does not invalidate an unchanged content-addressed attestation. These rows are retained attestations: the host must enforce real reviewer separation and semantic challenge because the helper cannot authenticate reviewer identity or natural-language entailment.

Recheck that outputs contain no secrets, private content, or local absolute paths. Record denominators, exclusions, stale evidence, and unresolved boundaries. For STANDARD, request independent human review before consequential changes when the host or delivery process supports it; this is a workflow recommendation, not a machine-validated completion record. FORENSIC uses the two machine-validated review records above.

Finish with a handoff that states completed scope, primary evidence, freshness, remaining unknowns, the next bounded action, and exact reproducible commands. Keep its validator-owned executable fence byte-for-byte unchanged; use `PROJECT_ATLAS_ROOT` at runtime for a custom output. Canonical core has no AI-host install default, so use `PROJECT_ATLAS_SCRIPT` or configured search roots; native adapters supply their roots. FORENSIC completion must use non-draft validation with `--replay-command-evidence`; it also requires at least one replayed command, the strict current snapshot, exact registry-ledger coverage, and both canonical-review-input-bound reviews. If the completion gate is not met, report the atlas as partial and name the earliest unresolved boundary.
