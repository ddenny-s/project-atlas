# Project Atlas

Mode: **QUICK**

## Scope and Depth Rationale

This refreshed case-study map covers only the blank parcel identifier invariant across the API, worker, and shared state writer. It deliberately omits the rest of the service architecture.

Selected by: The case-study operator explicitly retained QUICK for the refreshed one-invariant map.
Conflicting automatic signals: Automatic selection recommends STANDARD because the fixture has two runtime roots and shared state.
Intentionally omitted coverage: Full runtime routing, retry semantics, administrator authority analysis, and target architecture remain outside this teaching contour.
Escalation condition: Use STANDARD before relying on the map for service-wide change or production decisions.

## Start Alignment

| Question ID | Batch ID | Topic | Question | Option A | Option B | Option C | Option D | Selected | Free-form note | Answer state | Map effect | Provenance | Answered at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SA-Q001 | SA-B001 | Change boundary | Which invariant should this teaching map prepare for change? | Reject blank parcel identifiers on every write path | Document only the API path | Expand to all provider behavior | Другое — напишу сам | A | - | ANSWERED | The map and backlog are bounded to one shared write invariant | USER_INPUT:SA-Q001 | 2026-07-26T10:00:00Z |

| Batch ID | Sequence | Question IDs | Remaining material gaps | Decision | Decision provenance | Status |
| --- | --- | --- | --- | --- | --- | --- |
| SA-B001 | 1 | SA-Q001 | None | STOP_STABLE | PROTOCOL:SEMANTIC_STOP | COMPLETE |

## Evidence Snapshot

Observed at: 2026-07-26T12:15:00Z
Source or worktree snapshot: case-study-standard-service-after-atlas-001-v1

## Current Claims

- `CURRENT · CONFIRMED · CLAIM-STATE-003`: the shared writer rejects a whitespace-only `parcel_id` before either entry path can persist it. Source: `service/state.py:L8-L23`.
- `CLAIM-API-001` and `CLAIM-WORKER-002` are retained as the superseded before-map lineage inputs closed by ATLAS-001.
- `CURRENT · UNKNOWN · UNKNOWN:PROVIDER-ORDERING`: provider event ordering after a timed-out request remains unestablished. Source: `README.md:L8-L10`.

## Purpose

The shared writer now rejects a blank parcel identifier for both API and worker callers. The refreshed map records that invariant as current while preserving the unrelated provider-ordering gap.

## Entry Point

The request path enters through `service/api.py:L8-L12`; the background path enters through `service/worker.py:L27-L41`.

## Inputs and Outputs

Both paths accept a parcel identifier and persist parcel state through the validated shared writer in `service/state.py:L8-L23`; whitespace-only identifiers now fail before SQLite writes.

## Dependencies

The mapped paths use Python, SQLite from the standard library, and the shared status writer.

## Verification

Command: `rg --no-config --sort path --line-number --fixed-strings 'parcel_id' service/api.py service/state.py service/worker.py`
Proof boundary: The bounded search locates both callers and the shared parcel identifier validation on the exact refreshed source snapshot.

## Exact Validation Result

Exit code: 0
Observed result: The command found both caller sites and the shared blank-identifier guard in the three cited source files.
Stdout SHA-256: 497499eef9342b1edb3ad0e3b15c0b468dc4d89a7f142981167fadc46b4cd412

## Risks

The refreshed map does not establish provider ordering, retry side effects, deployment behavior, or production readiness.

## Exclusions

Provider delivery semantics, administrator override behavior, deployment, and production data are excluded from this one-invariant denominator.

## Evidence Legend

- **CONFIRMED**: directly supported by a project-relative source or captured command.
- **INFERENCE**: reasoned from confirmed evidence but not directly observed.
- **HYPOTHESIS**: testable explanation that still requires discriminating evidence.
- **TARGET**: proposed future state, never evidence of current behavior.
- **UNKNOWN**: not established within the declared scope and snapshot.

## Next Safe Action

Investigate provider ordering with a controlled timeout sequence before proposing any retry or reconciliation change.

## Source References

- `service/api.py:L8-L12` owns request-boundary validation.
- `service/worker.py:L27-L41` owns the background delivery write path.
- `service/state.py:L8-L23` owns the shared blank-identifier invariant and persistent writer.
- `README.md:L8-L10` records authority and provider-ordering boundaries.

## Unknowns

- UNKNOWN:PROVIDER-ORDERING remains open because provider ordering after a timed-out request is not established.

## Task Receipts

| Task ID | Status | Result | Evidence |
| --- | --- | --- | --- |
| ATLAS-001 | VERIFIED | Both blank-input checks and both valid write checks passed on the refreshed snapshot | ATLAS-001.patch; service/state.py:L8-L23; case-study regression `tests/test_documentation_case_study.py:test_public_case_study_reproduces_before_and_after_states` |

## Finish Alignment

| Question ID | Batch ID | Topic | Question | Option A | Option B | Option C | Option D | Selected | Free-form note | Answer state | Map effect | Provenance | Answered at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FA-Q002 | FA-B002 | Refreshed map | Does the refreshed map preserve the completed invariant and unrelated gap? | Accept the refreshed map and keep provider ordering open | Reopen the completed invariant | Claim provider ordering is resolved | Другое — напишу сам | A | - | ANSWERED | ATLAS-001 is retained as a receipt and the provider gap remains explicit | USER_INPUT:FA-Q002 | 2026-07-26T12:35:00Z |

| Batch ID | Sequence | Question IDs | Remaining material gaps | Decision | Decision provenance | Status |
| --- | --- | --- | --- | --- | --- | --- |
| FA-B002 | 1 | FA-Q002 | None | STOP_STABLE | PROTOCOL:SEMANTIC_STOP | COMPLETE |

## Run Economics

| Run ID | Block ID | Entry | Block | Unit | Min | Typical | Max | Basis | Model tier and effort | Input | Output | Reasoning | Total | Telemetry | Variance vs typical | Recorded at | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RUN-AFTER | MAP-AFTER | PRE | Refresh bounded invariant map | MODEL_TOKENS | 700 | 1100 | 1800 | Three refreshed source paths, one receipt, and one preserved unknown | balanced capability medium effort | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | 2026-07-26T11:55:00Z | MODELLED |
| RUN-AFTER | MAP-AFTER | POST | Refresh bounded invariant map | MODEL_TOKENS | UNMEASURED | UNMEASURED | UNMEASURED | PRE:MAP-AFTER | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | 2026-07-26T12:40:00Z | UNMEASURED |

## Future Tasks

| Task ID | Claim kind | Priority | Outcome | Basis | Affected areas | Scope | Non-goals | Acceptance criteria | Dependencies and unknowns | Risks | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ATLAS-001 | TARGET | P1 | Make every status write reject a blank parcel identifier | USER_INPUT:SA-Q001; service/api.py:L8-L12; service/worker.py:L27-L41; service/state.py:L8-L23; MAP:PROJECT_ATLAS.md#purpose | API, worker, and shared writer | Move the invariant to the shared writer and cover both entry paths | Provider retries, administrator authority, status design, and deployment | API blank input is rejected; worker blank input is rejected; valid API and worker writes remain persisted | No blocking dependency; preserve UNKNOWN:PROVIDER-ORDERING | A misplaced check could leave one path inconsistent or reject valid identifiers | The refreshed regression and completion validation captured the completed outcome | SUPERSEDED |
| ATLAS-002 | TARGET | P2 | Establish provider event ordering after a timed-out delivery attempt | README.md:L8-L10; MAP:PROJECT_ATLAS.md#unknowns | Provider retry and reconciliation boundary | Design a controlled ordering probe and record the observed sequence | Identifier validation, administrator authority, deployment, and status redesign | A reproducible timeout sequence identifies ordering or retains a narrower explicit gap | UNKNOWN:PROVIDER-ORDERING blocks a ready implementation task | A synthetic provider may not reproduce the real ordering contract | Run a bounded provider double and preserve exact event evidence | BLOCKED |
