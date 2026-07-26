# Output Contract

Substantive-content checks are Unicode-aware: non-ASCII letters and numbers count as substantive, while whitespace and punctuation alone do not.

## QUICK

Create only `PROJECT_ATLAS.md`. Completion requires substantive content in every required section, one structured depth-decision record in `Scope and Depth Rationale`, a real observation timestamp and concrete source snapshot, the complete five-kind evidence legend, one bounded deterministic `rg --no-config` command against explicit safe-inventory targets and proof boundary, an integer exit code, observed result, standard-output SHA-256, and at least one project-relative source location. A directory target or multiple targets require exact `--sort path`. Validation replays the command from the project root and compares the exit code and digest. Generic prose, fabricated command evidence, and unchanged scaffold content do not pass.

## STANDARD

Create:

- `ATLAS_INDEX.md`
- `PRODUCT_AND_REQUIREMENTS.md`
- `CURRENT_ARCHITECTURE.md`
- `RUNTIME_AND_ENTRYPOINTS.md`
- `DATA_STATE_AND_AUTHORITY.md`
- `PRODUCT_FLOWS.md`
- `QUALITY_SECURITY_AND_OPERATIONS.md`
- `FINDINGS_AND_DISPOSITIONS.md`
- `TARGET_ARCHITECTURE.md`
- `MIGRATION_PLAN.md`
- `OPEN_UNKNOWNS.md`
- `LIVE_HANDOFF.md`

Use `ATLAS_INDEX.md` as the router. Its `Scope and Coverage` section contains the structured depth-decision record. Keep current state, target state, migration, and unknowns distinct. Link documents using relative paths.

Completion requires every canonical section heading. A heading may have one unambiguous descriptive extension, but it may not disappear or compete with a duplicate. Static contract text may remain canonical; dynamic sections must replace initialized draft prose and empty tables. Every current-material `CONFIRMED`, `INFERENCE`, or `HYPOTHESIS` requirement/finding row has a valid project-relative source in its `Source` or `Evidence` cell.

## FORENSIC

Create the STANDARD set plus `TRACEABILITY.tsv` and the generated `SOURCE_SNAPSHOT.json`. Add complete entry-point, state-object, reader/writer, authority, effect, recovery, and disposition registries. Record every material completeness claim in the canonical `ATLAS_INDEX.md` coverage table with a numerator, denominator, exclusions, and exact ledger link. Retain at least one completion-active replayable `COMMAND` row; a directory target or multiple targets require exact `--sort path`. Create and validate a deterministic source snapshot after evidence references are populated, then retain two independent reviews bound to its scope digest.

## Rerun behavior

Treat every existing file as user-owned. Initialization may add a missing required file but must not replace or merge into an existing file. Refresh work must read the existing document, recheck affected evidence, and make a deliberate narrow edit.

## Required registries

Every requirement is a row with `ID`, `Claim kind`, `Requirement`, `Source`, and `Status`. A future control uses `TARGET`; an unsupported current requirement uses `UNKNOWN`.

Every finding is a row with `ID`, `Claim kind`, `Severity`, `Finding`, `Affected scope`, `Evidence`, `Impact`, `Disposition`, `Prerequisites`, `Verification`, `Rollback`, and `Status`. Empty required cells are invalid.

Use only `P0` (critical), `P1` (important), `P2` (moderate), `P3` (minor), or `UNKNOWN` for severity. Use only `KEEP`, `REWRITE`, `MERGE`, `DELETE`, or `UNKNOWN` for disposition.

Every STANDARD migration stage is a row with `Stage`, `Change`, `Preconditions`, `Compatibility and state/data handling`, `Primary signal`, `Secondary signals`, `Decision authority`, `Rollback`, and `Status`. FORENSIC inserts `Claim kind` after `Stage`. Use an explicit `None` or `UNKNOWN` when a field does not apply or is unresolved; do not leave it blank.

Every FORENSIC coverage claim is a row under `## Coverage Claims` in `ATLAS_INDEX.md` with `ID`, `Claim kind`, `Claim`, `Population`, `Discovery method`, `Numerator`, `Denominator`, `Exclusions`, and `Status`.

Every FORENSIC unknown is a row under `## Open Unknowns` with `ID`, `UNKNOWN`, `Consequence`, `Next evidence`, `Owner`, and `Status`.

Every FORENSIC independent review is a row under `## Independent Reviews` in `LIVE_HANDOFF.md` with `ID`, `Review kind`, `Reviewer ref`, `Independence`, `Reviewed snapshot`, `Verdict`, `Critical`, `Important`, `Retained evidence summary`, `Remaining limits`, `Reviewed at`, and `Status`. Completion requires exactly one completion-active `CORRECTNESS` and one completion-active `SECURITY` row with distinct reviewers; both use `PASS`, `0` Critical, `0` Important, substantive summaries and limits, and the current `review_input.sha256`. UTC review times cannot predate the latest bound evidence, exceed its seven-day freshness window, or be more than five minutes ahead of the validating host clock. A bound review remains valid while its content-addressed input remains unchanged; wall-clock age alone does not invalidate it. These are retained attestations; the host, not the deterministic validator, authenticates actual reviewer separation and semantic entailment.

All material FORENSIC registry claims require exact `ACTIVE` or `CURRENT` coverage from the canonical `atlas_refs` field in `TRACEABILITY.tsv`. Registry IDs do not implicitly link rows. Requirements, migration stages, coverage claims, unknowns, review summaries, and both the finding and its disposition have separately addressable references.

## Handoff

Record:

- declared mode and scope;
- completed and excluded contours;
- evidence freshness and snapshot digest;
- unresolved unknowns and risks;
- the exact next bounded action;
- commands needed to reproduce validation;
- blockers that require new authority or external input.

Do not claim completion when the handoff cannot route a fresh investigator to the next action.

Commands contain no substitution markers. The executable shell fence exactly matches the validator-owned mode template and is never edited; an explicit custom output is supplied at runtime through `PROJECT_ATLAS_ROOT`. Canonical core resolves an explicit helper or explicitly configured search roots; native adapters inject their deterministic install/cache roots. Resolution rejects zero or multiple candidates, sets project and atlas roots, uses `PYTHONDONTWRITEBYTECODE=1`, and passes both `--atlas` and `--project` to validation. FORENSIC records the full snapshot command and validates with `--replay-command-evidence`.

The FORENSIC `SOURCE_SNAPSHOT.json` v0.2 object has exactly `schema_version`, `safe_inventory`, `evidence_scope`, `review_input`, `review_records_sha256`, `traceability_sha256`, `files`, and `sha256`. The safe-inventory manifest hashes path names only. `files` must exactly equal the non-empty ordered union of distinct completion-active `FILE`, `SCHEMA`, `CONFIG`, and `TEST` references and all allowlisted file members resolved from completion-active `COMMAND` targets. Directory targets expand under the replay ceilings; unrelated allowlisted content is not opened or admitted. `evidence_scope.unique_evidence_files` and `hashed_files` record that exact union. `review_input.sha256` binds the source-scope digest, every required mode artifact, and canonical non-review trace rows; the snapshot object is the digest carrier and excluded by definition. `review_records_sha256` binds review table and review-only trace rows separately. Final validation recomputes every digest and population.

The helper rejects hardlinked sources and artifacts and mutation during reads or hashes. Exact ceilings are: safe-inventory traversal at 100,000 files, 20,000 directories, depth 64, and 16 MiB of UTF-8 relative-path bytes; 2 MiB per non-trace artifact including the snapshot; 4 MiB and 10,000 rows for traceability; 16 MiB aggregate atlas size; 5,000 rows per registry; snapshot JSON depth 8 and 50,000 nodes; 16 MiB per evidence source; 8 MiB serialized JSON output for both files and stdout; and replay limits of 2,000 files, 4 MiB per file, 32 MiB total, 4 MiB stdout, 256 KiB stderr, and 15 seconds. Crossing a traversal or serialization ceiling fails closed without truncation.

Use `atlas.py validate --draft` only for structural work-in-progress checks. Default validation applies completion gates and rejects every required artifact that remains its untouched scaffold, reserved artifacts from another mode, and empty canonical registries. FORENSIC completion also requires at least one active replayed command, `--replay-command-evidence`, the current strict snapshot, exact registry-ledger coverage, and both canonical-review-input-bound reviews.
