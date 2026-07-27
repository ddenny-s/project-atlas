# Output Contract

Project Atlas creates the smallest document set that can preserve evidence, route readers, and support the selected depth. Output files belong to the user and are never silently replaced.

Substantive-content checks are Unicode-aware: non-ASCII letters and numbers count as substantive, while whitespace and punctuation alone do not.

## Output routing

An explicit output path always wins. When no path is supplied:

- QUICK writes `./PROJECT_ATLAS.md`.
- STANDARD and FORENSIC use `./docs/project-atlas/` when `docs/` already contains user-facing project documentation.
- STANDARD and FORENSIC otherwise use `./project-atlas/`.

The skill and `atlas.py init` apply this routing policy. An explicit `--output` overrides the default destination.

The helper's filesystem reads and writes require POSIX directory-descriptor and no-follow support. On a platform without those primitives, including native Windows Python, `atlas.py` exits with a clean fail-closed error instead of using a path-based fallback. An adapter may still execute the protocol with equally bounded host-native tools, but must record that the helper validation was unavailable.

Initialization refuses to overwrite an existing output. Refresh reads the current atlas first, preserves user-authored material, and writes only the changes supported by new evidence.

## Common conventions

Every atlas states:

- START alignment before deep work and FINISH alignment against the candidate map;
- project and investigated scope;
- selected depth and selection rationale;
- source snapshot and observation time;
- excluded and unavailable sources;
- evidence-class legend;
- current-state facts separately from target proposals;
- validation commands and their exact result status;
- open unknowns and proof limitations;
- per-block model-token forecasts and exact-or-unmeasured telemetry;
- a traceable future-task backlog that mapping does not implement;
- next safe action and continuation instructions.

Material facts use `CONFIRMED`, `INFERENCE`, `HYPOTHESIS`, `TARGET`, or `UNKNOWN` as defined in the [methodology](methodology.md).

## QUICK artifact

`PROJECT_ATLAS.md` contains:

1. Start Alignment with its question table and batch ledger.
2. Scope and depth rationale with exactly one `Selected by`, `Conflicting automatic signals`, `Intentionally omitted coverage`, and `Escalation condition` field.
3. Observation time or source/worktree snapshot.
4. Product purpose and user outcome.
5. Start or invocation path.
6. Main inputs, outputs, dependencies, and state.
7. Exclusions and evidence-class legend.
8. Verification command, proof limits, and exact result.
9. Risks, unknowns, and next safe action.
10. Project-relative source references.
11. Finish Alignment against the final candidate map.
12. Run Economics with PRE and POST rows.
13. Future Tasks with at least one READY or honest BLOCKED row.

QUICK stays in one file unless the user explicitly requests a different arrangement.

Completion requires substantive project evidence rather than generated scaffold language or `UNKNOWN`. The four depth-decision fields appear exactly once and contain substantive values rather than empty or bare sentinel answers. `Observed at` is a real UTC timestamp, the snapshot is concrete, the legend defines all five evidence classes, and verification records one exact bounded `rg --no-config` command against explicit safe-inventory targets, its proof boundary, integer exit code, observed result, and standard-output SHA-256. A directory target or multiple targets require exact `--sort path`. The validator replays that command from the project root and compares the real exit code and digest. At least one source reference resolves to a project-relative source location. This deterministic check is a lower bound; semantic review is still required to decide whether the prose and cited source support each other.

## Alignment, economics, and future-task records

START and FINISH questions are adaptive, one to three per batch, with no total cap. Every visible question has exactly four choices; D is exactly `Другое — напишу сам`. Unknown, skip, and user stop are separate controls. If a picker cannot preserve that shape, the adapter uses plain chat. FINISH runs after the candidate map; a map-changing answer requires an update and another FINISH pass.

The canonical question and batch schemas are:

```text
Question ID | Batch ID | Topic | Question | Option A | Option B | Option C | Option D | Selected | Free-form note | Answer state | Map effect | Provenance | Answered at
Batch ID | Sequence | Question IDs | Remaining material gaps | Decision | Decision provenance | Status
```

Run Economics uses:

```text
Run ID | Block ID | Entry | Block | Unit | Min | Typical | Max | Basis | Model tier and effort | Input | Output | Reasoning | Total | Telemetry | Variance vs typical | Recorded at | Status
```

PRE is an integer `MODEL_TOKENS` forecast made before each deep block. POST contains only exact host telemetry or `UNMEASURED`. The PRE maximum is a reforecast threshold, not a hard cap. Weekly usage appears only when the host supplies that exact signal; model tokens never become quota percentages.

Future Tasks uses:

```text
Task ID | Claim kind | Priority | Outcome | Basis | Affected areas | Scope | Non-goals | Acceptance criteria | Dependencies and unknowns | Risks | Verification | Status
```

Every task is `TARGET`, and mapping never implements it. Both `READY` and `BLOCKED` rows need substantive, non-draft `Outcome`, `Basis`, `Affected areas`, `Scope`, `Non-goals`, `Acceptance criteria`, `Dependencies and unknowns`, `Risks`, and `Verification` values. Both also need a technical `Basis`: a safe project-relative source or a visible, unique, non-interaction Atlas section with substantive content referenced as `MAP:<atlas-file>#<stable-anchor>`. `READY` additionally needs an active, non-dangling `USER_INPUT:<Question ID>` in `Basis`. `BLOCKED` may omit only that owner input; it still needs the same technical basis and canonical `UNKNOWN:<stable-id>` in `Dependencies and unknowns`. If it cites `USER_INPUT`, that reference must still be active and non-dangling. Any justified number of tasks is allowed, with at least one `READY` or `BLOCKED` row at completion. Detailed enums and provenance rules live in the [canonical interaction reference](../core/skill/map-project/references/user-interaction-and-budget.md).

## STANDARD and FORENSIC artifacts

| Artifact | STANDARD | FORENSIC | Purpose |
| --- | --- | --- | --- |
| `ATLAS_INDEX.md` | Required | Required | Canonical entrypoint, scope, depth, status, routing, and freshness |
| `PRODUCT_AND_REQUIREMENTS.md` | Required | Required | Start alignment, users, outcomes, scenarios, requirements, and conflicts |
| `CURRENT_ARCHITECTURE.md` | Required | Required | Current components, ownership, dependencies, and runtime relationships |
| `RUNTIME_AND_ENTRYPOINTS.md` | Required | Required | Runtime roots, startup, shutdown, triggers, configuration, and effects |
| `DATA_STATE_AND_AUTHORITY.md` | Required | Required | Stores, state objects, readers, writers, lifecycle, authority, and conflicts |
| `PRODUCT_FLOWS.md` | Required | Required | Priority end-to-end user and operational flows |
| `QUALITY_SECURITY_AND_OPERATIONS.md` | Required | Required | Test proof, security, privacy, reliability, observability, recovery, and cost |
| `FINDINGS_AND_DISPOSITIONS.md` | Required | Required | Findings, severity, evidence, and keep/rewrite/merge/delete decisions |
| `TARGET_ARCHITECTURE.md` | Required | Required | Proposed future architecture, rationale, alternatives, and constraints |
| `MIGRATION_PLAN.md` | Required | Required | Ordered changes, future tasks, compatibility, verification, rollback, and decision gates |
| `TRACEABILITY.tsv` | Recommended when claims are numerous | Required | Machine-checkable claim-to-source and claim-to-output ledger |
| `SOURCE_SNAPSHOT.json` | Not required | Required for completion | Strict digest of the safe path manifest, active file-like and command-target evidence population, and traceability state |
| `OPEN_UNKNOWNS.md` | Required | Required | Material unanswered questions and resolving checks |
| `LIVE_HANDOFF.md` | Required | Required | Finish alignment, run economics, resume state, last checks, active risks, and next bounded actions |

For STANDARD and FORENSIC, `ATLAS_INDEX.md` `Scope and Coverage` owns the same four single-value depth-decision fields. They are not duplicated in `LIVE_HANDOFF.md`.

STANDARD and FORENSIC use the artifact set above; do not merge required routed artifacts. Completion rejects reserved artifacts from another mode and rejects every required artifact that is still byte-identical to its generated scaffold. QUICK usually shares the product root, so unrelated product files are not treated as Atlas artifacts. FORENSIC keeps material registries modular enough to validate and update independently.

STANDARD completion also requires every canonical section heading. It permits one unambiguous descriptive extension such as `Security and Privacy`, while missing or competing headings fail. Static contract sections may retain canonical text; dynamic sections must replace draft prose and empty-table state. Current-material `CONFIRMED`, `INFERENCE`, and `HYPOTHESIS` rows in requirements and findings must cite a valid project-relative safe-inventory source.

## Artifact requirements

### `ATLAS_INDEX.md`

The index is the only required starting point. It records:

- atlas version, selected mode, and output root;
- project snapshot and drift indicators;
- scope, exclusions, and coverage summary;
- artifact table with status and last verification;
- highest-risk findings and unknowns;
- current versus target-state boundary;
- route to the live handoff.

FORENSIC records every material completeness claim under `## Coverage Claims` with this exact table shape:

```text
ID | Claim kind | Claim | Population | Discovery method | Numerator | Denominator | Exclusions | Status
```

Counts are non-negative integers, and the numerator cannot exceed the denominator. The claim is not covered merely because its ID appears elsewhere; it needs its exact traceability reference.

### `PRODUCT_AND_REQUIREMENTS.md`

Every current, inferred, proposed, or unresolved requirement uses this exact table shape:

```text
ID | Claim kind | Requirement | Source | Status
```

Future controls use `TARGET`; missing current evidence uses `UNKNOWN`.

The same artifact owns `Start Alignment`. Owner answers are `USER_INPUT` provenance and can support direction, scope, and TARGET claims, never technical current-state facts.

### `CURRENT_ARCHITECTURE.md`

Describe components by responsibility and effects, not directory order. Explain why each material component exists, what invokes it, what it owns, and what breaks if it changes or disappears.

### `RUNTIME_AND_ENTRYPOINTS.md`

For each runtime root, record the trigger, executable or handler, configuration, initialized dependencies, inputs, outputs, side effects, failure visibility, shutdown, and source evidence. FORENSIC includes a declared denominator and status for every discovered root.

### `DATA_STATE_AND_AUTHORITY.md`

For each material state object, record store, schema, lifecycle, readers, writers, consistency, retention, authority, conflict resolution, and recovery. Secret references may be named, but secret values must never be copied.

### `PRODUCT_FLOWS.md`

Trace a flow from user or system trigger to observable outcome. Include synchronous and asynchronous hops, state transitions, provider calls, authority decisions, errors, and verification evidence.

### `QUALITY_SECURITY_AND_OPERATIONS.md`

Separate configured intent, test evidence, and observed operation. State mock boundaries, missing failure cases, operational ownership, logging and alert gaps, backup and restore evidence, security boundaries, and cost-sensitive paths.

### `FINDINGS_AND_DISPOSITIONS.md`

Each finding includes identifier, severity, affected scope, evidence, impact, recommended disposition, prerequisites, verification, rollback, and status. A disposition is not implementation authorization.

Use this exact table shape:

```text
ID | Claim kind | Severity | Finding | Affected scope | Evidence | Impact | Disposition | Prerequisites | Verification | Rollback | Status
```

Severity is one of `P0` (critical), `P1` (important), `P2` (moderate), `P3` (minor), or `UNKNOWN`. Disposition is one of `KEEP`, `REWRITE`, `MERGE`, `DELETE`, or `UNKNOWN`.

### `TARGET_ARCHITECTURE.md`

Keep target proposals marked `TARGET`. Tie each proposal to a finding or explicit product objective. Include constraints, trade-offs, rejected options where material, and the source of decision authority.

### `MIGRATION_PLAN.md`

Use ordered, independently verifiable stages. Each stage defines preconditions, compatibility behavior, state or data handling, rollout gate, primary signal, secondary checks, rollback, and the person or system authorized to proceed.

Use this exact table shape:

```text
Stage | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status
```

FORENSIC uses the same table with an explicit claim kind:

```text
Stage | Claim kind | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status
```

`Future Tasks` is a separate registry in the same artifact, not a migration authorization:

```text
Task ID | Claim kind | Priority | Outcome | Basis | Affected areas | Scope | Non-goals | Acceptance criteria | Dependencies and unknowns | Risks | Verification | Status
```

The same `READY`/`BLOCKED` matrix above applies here. In particular, a `BLOCKED` row is not valid without a safe technical source or visible, unique, non-interaction, substantive map basis.

### `TRACEABILITY.tsv`

Use tab-separated rows with a stable header. The canonical fields are:

```text
fact_id	claim_kind	claim	source_type	source_ref	observed_at	status	atlas_refs	notes
```

Requirements:

- one material claim per row;
- stable identifiers across refreshes;
- repository-relative source paths;
- no secrets or large source excerpts;
- explicit stale, superseded, and unresolved status;
- parseable tabs and one header row;
- source references that another reviewer can open or reproduce.

`atlas_refs` is the exact machine link to material FORENSIC registries. Use `-` when a source fact has no material registry link. Otherwise list unique references in lexical order, separated by semicolons, using only these forms:

```text
PRODUCT_AND_REQUIREMENTS.md#requirements/<ID>
PRODUCT_AND_REQUIREMENTS.md#direction/<Question ID>
FINDINGS_AND_DISPOSITIONS.md#findings/<ID>/finding
FINDINGS_AND_DISPOSITIONS.md#findings/<ID>/disposition
MIGRATION_PLAN.md#migration/<Stage>
MIGRATION_PLAN.md#future-tasks/<Task ID>
ATLAS_INDEX.md#coverage/<ID>
OPEN_UNKNOWNS.md#unknowns/<ID>
LIVE_HANDOFF.md#direction/<Question ID>
LIVE_HANDOFF.md#reviews/<ID>
```

At FORENSIC completion, every canonical material claim has an `ACTIVE` or `CURRENT` trace row with an exact claim-kind and claim-text match. Matching `fact_id` values, stale rows, or prose links are insufficient. Each finding produces two claims: the finding text itself and `Disposition <ID>: <DISPOSITION>`; the disposition is `TARGET` unless it remains `UNKNOWN`. Active answered direction claims use exact text `<Question>: <Selected option>` and active future tasks use exact `Outcome`; both are `TARGET`.

Trace status is exactly `ACTIVE`, `CURRENT`, `STALE`, or `SUPERSEDED`. `UNRESOLVED` evidence is compatible only with `UNKNOWN`; it cannot support a current fact, inference, hypothesis, or target. `observed_at` is a real `YYYY-MM-DD` calendar date or `YYYY-MM-DDTHH:MM:SSZ` UTC timestamp. Review references and non-review references cannot be mixed in one ledger row. Only compatible completion-active rows count toward registry coverage.

For a `COMMAND` row, record one exact command in `source_ref`. Quote glob and search arguments. Use `rg --no-config` against explicit safe-inventory files or bounded directories, never the whole project. A directory target or multiple targets require exact `--sort path`; `--sortr` and other sort keys are rejected. In `notes`, record `cwd=<relative>; exit=<integer>; stdout_sha256=<64 hex>`. Do not use prose, pseudo-commands, aliases, or commands that were not actually run. FORENSIC completion requires at least one completion-active `COMMAND` row, replays every active supported row with `--replay-command-evidence`, and compares both the exit code and stdout digest.

### `OPEN_UNKNOWNS.md`

Each unknown states why it matters, what was already checked, the cheapest resolving experiment, required access or authority, severity if unresolved, and the decision it blocks.

FORENSIC keeps active material unknowns under `## Open Unknowns` with this exact table shape:

```text
ID | UNKNOWN | Consequence | Next evidence | Owner | Status
```

### `LIVE_HANDOFF.md`

The handoff includes:

- snapshot and worktree state observed at the last checkpoint;
- selected mode, scope, exclusions, and output root;
- final owner alignment and per-block run economics;
- completed contours and current coverage;
- exact last validation commands and outcomes;
- active hypotheses and unresolved high-risk unknowns;
- user-authored sections that must be preserved;
- next one to three bounded actions;
- stop conditions and authorization gates;
- files to read first when resuming.

FORENSIC keeps review evidence under `## Independent Reviews` with this exact table shape:

```text
ID | Review kind | Reviewer ref | Independence | Reviewed snapshot | Verdict | Critical | Important | Retained evidence summary | Remaining limits | Reviewed at | Status
```

Completion requires exactly one completion-active `CORRECTNESS` review and one completion-active `SECURITY` review from distinct reviewers. `Independence` is `FRESH_CONTEXT` or `EXTERNAL_REVIEWER`; each review binds to the current `review_input.sha256`, records `PASS`, `0` Critical, `0` Important, a substantive retained summary, substantive remaining limits, and a UTC timestamp in `YYYY-MM-DDTHH:MM:SSZ` form. The timestamp cannot precede the latest bound non-review evidence, cannot exceed it by more than seven days, and cannot be more than five minutes ahead of the validating host clock. Each review summary is also a material claim with exact traceability coverage. A bound review does not expire merely with wall-clock age; evidence refresh or any non-review content change creates a new digest and requires new reviews.

The review table is a retained attestation, not an authenticated identity system. Validation proves the record shape, distinct references, chronology, snapshot binding, counts, and ledger binding. The invoking host or release process must enforce actual fresh-context separation and must judge whether cited evidence semantically supports the claims; the deterministic helper cannot prove reviewer identity or natural-language entailment.

The one executable shell fence must match the validator-owned mode template exactly; adding, removing, or editing even a valid shell command invalidates the handoff. Prose outside the fence remains user-owned. For custom routing, export `PROJECT_ATLAS_ROOT` before running the unchanged fence and record that routing in the handoff prose. Canonical core has no AI-host install default: use an explicit `PROJECT_ATLAS_SCRIPT` or configure `PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS`; each native adapter supplies its own deterministic install/cache roots. The fence fails on zero or multiple candidates, runs Python with `PYTHONDONTWRITEBYTECODE=1`, and validates with both `--atlas` and `--project`. FORENSIC also reproduces the named source snapshot and validates with `--replay-command-evidence`.

Use `atlas.py validate --draft` for structural scaffold checks. Default validation is the completion gate and rejects every required artifact that remains its untouched scaffold, reserved artifacts from another mode, and empty canonical registries.

### `SOURCE_SNAPSHOT.json`

FORENSIC completion uses schema version `0.2` with exactly these top-level fields:

```text
schema_version | safe_inventory | evidence_scope | review_input | review_records_sha256 | traceability_sha256 | files | sha256
```

`safe_inventory` stores `member_count`, `excluded_count`, and `path_manifest_sha256`. The path manifest hashes only allowlisted relative path names; producing it never opens unrelated file contents. `evidence_scope` records `unique_evidence_files` and `hashed_files`. `files` is the non-empty ordered, exact union of distinct completion-active `FILE`, `SCHEMA`, `CONFIG`, and `TEST` traceability references plus every allowlisted file member resolved from completion-active `COMMAND` targets, each with its current content hash. Directory targets expand only under the replay count and byte ceilings. Extra safe files, missing evidence files, reordered entries, ignored paths, symlinks, hardlinks, and stale hashes are invalid.

`traceability_sha256` binds the complete current ledger. The top-level `sha256` binds schema version, safe path manifest, and the exact evidence-file population. `review_input` records required-mode-artifact count, non-review trace row count, latest bound evidence time, and a digest over the source-scope digest plus every required mode artifact with only review table rows and review-linked trace rows removed. `SOURCE_SNAPSHOT.json` is the digest carrier rather than a mode artifact. `review_records_sha256` binds the removed records separately. Review rows therefore cannot hash their own digest, while any bound source, non-review atlas, or trace change invalidates both reviews.

Non-draft FORENSIC validation fails unless at least one active command exists, every active supported command is replayed with `--replay-command-evidence`, the strict snapshot is current, all material registry claims have exact active ledger coverage, and both required review attestations pass against the current snapshot.

## Validator safety ceilings and v0.2 migration

The reference helper fails closed at these exact ceilings:

| Surface | Limit |
| --- | --- |
| Safe-inventory traversal | 100,000 files, 20,000 directories, depth 64, 16 MiB of UTF-8 relative-path bytes |
| Each ignore-metadata file | 1 MiB |
| Each non-trace atlas artifact, including the snapshot | 2 MiB |
| `TRACEABILITY.tsv` | 4 MiB and 10,000 substantive data rows |
| Required artifacts plus a present snapshot | 16 MiB aggregate |
| Each canonical registry | 5,000 substantive rows |
| Snapshot JSON | Depth 8 and 50,000 nodes |
| Each evidence source read or hash | 16 MiB |
| Serialized JSON output, file or stdout | 8 MiB |
| Inventory classification | 2,000 files, 512 KiB each, 32 MiB total |
| Replay mirror | 2,000 files, 4 MiB each, 32 MiB total |
| Replay process | 4 MiB stdout, 256 KiB stderr, 15 seconds |

Source reads, artifact reads, evidence hashes, and replay copies reject symbolic links, hardlinks, and any identity, size, modification-time, change-time, or link-count mutation observed across the operation. Leakage checks decode percent encoding before detecting local file URIs, credential-bearing URLs, authorization values, bearer/JWT material, private-key headers, and known token shapes. Diagnostics redact matching material.

JSON output creation is no-clobber when the target is absent and uses atomic name exchange with identity-checked quarantine and restoration when replacing an existing target. It fails closed where the operating system lacks the necessary atomic rename flags.

Traversal and serialization ceilings fail the whole operation; the helper never presents a truncated inventory or partial JSON document as complete evidence.

Version 0.2 is intentionally breaking. An atlas produced for v0.1 must restore the canonical handoff fence, migrate review rows to the twelve-column table, add substantive remaining limits and fresh UTC times, ensure compatible evidence statuses and a non-empty active evidence population, regenerate `SOURCE_SNAPSHOT.json`, and repeat both reviews against `review_input.sha256`. Oversized, hardlinked, encoded-leak-bearing, or semantically incomplete outputs that older validation accepted now fail.

## Diagrams

Use Mermaid when it makes runtime, data movement, authority, state transitions, product flow, or migration order easier to verify. A diagram supplements the evidence table; it does not replace source references or hide unknown edges.

## Refresh behavior

On refresh:

1. Validate the existing structure before writing.
2. Preserve stable identifiers and user annotations.
3. Compare source snapshots and cheap drift indicators.
4. Mark stale claims before recalculating dependent conclusions.
5. Update affected artifacts and traceability rows together.
6. Record added, changed, removed, reverified, and unresolved items.
7. Refresh the index and handoff last.

Do not delete an artifact merely because a current search no longer finds its subject. First determine whether the source moved, became generated, changed environment, or is now an unresolved unknown.

## Publication check

Before committing or sharing an atlas, check for credentials, local machine paths and encoded file URIs, credential-bearing URLs, authorization or bearer values, JWTs, private-key material, customer or employee identifiers, internal hostnames, private repository names, proprietary snippets, and environment-specific data. Replace sensitive evidence with a safe reference or keep the atlas private.
