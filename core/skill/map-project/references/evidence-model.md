# Evidence Model

## Claim kinds

Use exactly one kind for every material claim:

- `CONFIRMED`: directly supported by current primary evidence.
- `INFERENCE`: reasoned from cited evidence but not directly observed.
- `HYPOTHESIS`: a testable explanation awaiting evidence.
- `TARGET`: a future proposal, never a current fact.
- `UNKNOWN`: a named gap with no established answer.

Do not use confidence language to blur these boundaries.

## Evidence priority

Prefer the strongest available source:

1. a fresh runtime observation of the exact path;
2. a reproducible command and its result;
3. a focused test and its asserted boundary;
4. a schema or configuration that owns the behavior;
5. a current file and line;
6. a primary external document;
7. an inference linked to the sources that support it.

Record what each source proves and what it does not prove. A passing test proves only its exercised inputs and assertions. Source code does not prove deployment, configuration, external behavior, or production data shape.

For a counted claim, list the members, denominator, discovery method, and exclusions before writing the total. Recount after editing the narrative; a number that disagrees with its enumerated set is not `CONFIRMED`.

## Source references

Use project-relative paths. Add a line or symbol when possible. Never store source contents, credentials, private identifiers, local absolute paths, or ignored paths in an atlas or snapshot.

Record observation time for volatile evidence. Mark evidence stale when its source hash, relevant configuration, runtime, or observation boundary changes.

## User-input provenance

An alignment answer uses `USER_INPUT:<Question ID>` provenance. It can support scope, authority, intended outcome, `TARGET` direction, output, acceptance language, priority, or backlog. It cannot support a technical current-state fact. Treat an owner statement about implementation as a lead until technical evidence confirms it; otherwise use `HYPOTHESIS` or `UNKNOWN`.

`USER_INPUT` is alignment provenance, not a substitute label for repository, command, or runtime evidence. A FORENSIC direction trace row uses source type `EXTERNAL` with source reference `USER_INPUT:<Question ID>`. A `READY` future task needs both active answered user provenance and a safe project-relative source or visible, unique, non-interaction, substantive `MAP:<atlas-file>#<stable-anchor>` basis. A `BLOCKED` task may omit only the user provenance: it still needs the same substantive task fields and technical basis, plus canonical `UNKNOWN:<stable-id>` in `Dependencies and unknowns`. Any cited `USER_INPUT` must resolve to an active answer.

## Traceability rows

Use the exact tab-separated columns:

```text
fact_id	claim_kind	claim	source_type	source_ref	observed_at	status	atlas_refs	notes
```

Keep `fact_id` stable and unique. Use a claim kind from the canonical enum. Use a source type from `FILE`, `SCHEMA`, `CONFIG`, `TEST`, `COMMAND`, `RUNTIME`, `EXTERNAL`, or `UNRESOLVED`. Keep `source_ref` relative for repository evidence. Use `notes` for proof boundaries, not copied source content.

A `source_ref` that resolves to a project-relative safe-inventory file or line range must use `FILE`, `SCHEMA`, `CONFIG`, or `TEST`. Do not label local repository evidence as `EXTERNAL`, `RUNTIME`, `COMMAND`, or `UNRESOLVED` to remove it from source validation or the snapshot population.

Use exactly `ACTIVE`, `CURRENT`, `STALE`, or `SUPERSEDED` for status. `UNRESOLVED` can support only `UNKNOWN`; it cannot support any other claim kind. Record a real calendar date as `YYYY-MM-DD` or a real UTC timestamp as `YYYY-MM-DDTHH:MM:SSZ`. A row with an invalid kind/source/status combination or timestamp never satisfies completion coverage.

`atlas_refs` is the only machine-recognized link from a source fact to material FORENSIC registries. Use `-` for no registry link. Otherwise use lexically sorted, unique, semicolon-separated references in one of these forms:

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

The referenced registry row, ledger `claim_kind`, and ledger `claim` must agree exactly. Only a compatible `ACTIVE` or `CURRENT` ledger row covers a material claim at completion. Matching `fact_id` text, prose links, stale rows, and approximate wording do not count. Review references cannot share a row with non-review references. A finding produces two material claims: its finding text with the row's claim kind, and `Disposition <ID>: <DISPOSITION>` with kind `TARGET` unless the disposition is `UNKNOWN`.

Each active answered START or FINISH direction is a `TARGET` claim with exact text `<Question>: <Selected option>`. START uses `PRODUCT_AND_REQUIREMENTS.md#direction/<Question ID>` and FINISH uses `LIVE_HANDOFF.md#direction/<Question ID>`. Each active READY or BLOCKED future task is a `TARGET` claim whose exact text is its `Outcome` and whose reference is `MIGRATION_PLAN.md#future-tasks/<Task ID>`. These direction and backlog claims do not establish current implementation.

For `COMMAND`, `source_ref` is one exact command that was actually run. Quote globs and search expressions, use a project-relative working directory, and do not substitute prose, aliases, pseudo-arguments, or an intended command. Use `rg --no-config` against explicit files or bounded directories that each resolve to at least one member of the safe inventory; do not target the whole project, an empty directory, or an excluded-only contour. A directory target or multiple targets require exact `--sort path`; `--sortr` and other sort keys are rejected because they do not satisfy the canonical ordering contract. The notes field records `cwd=<relative>; exit=<integer>; stdout_sha256=<64 hex>`. A command that was not executed and captured cannot support `CONFIRMED`. FORENSIC completion requires at least one completion-active command and uses `--replay-command-evidence` to rerun every completion-active supported row without a shell and compare the recorded exit code and stdout digest; `STALE` and `SUPERSEDED` commands remain historical records and are not replayed for completion.

The source snapshot hashes the exact distinct completion-active `FILE`, `SCHEMA`, `CONFIG`, and `TEST` references plus every allowlisted file member resolved from completion-active `COMMAND` targets. Directory targets expand only under the replay count and byte ceilings. `evidence_scope.unique_evidence_files` and `hashed_files` record this exact evidence population. At least one active evidence source is required for FORENSIC completion. The whole safe inventory is otherwise recorded only as counts and a digest of relative path names, so unrelated allowlisted files are never opened merely to build a baseline. Snapshot v0.2 additionally binds that source scope plus canonical non-review atlas and ledger content in `review_input.sha256`, while `review_records_sha256` binds the excluded review records. Any bound source, non-review artifact, or trace change invalidates both reviews without creating a self-referential digest.

## Drift

On refresh, compare cheap indicators first: referenced file hashes, manifests, schemas, runtime roots, configuration names, test entry points, and prior open unknowns. Recheck affected claims and mark stale or superseded rows explicitly. Preserve the old reasoning trail when it remains useful.
