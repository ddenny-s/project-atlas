# Project Atlas

Mode: **QUICK**

## Scope and Depth Rationale

This case-study map covers only the blank parcel identifier invariant across the API, worker, and shared state writer. It deliberately omits the rest of the service architecture.

Selected by: The case-study operator explicitly selected QUICK for one bounded invariant.
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

Observed at: 2026-07-26T10:15:00Z
Source or worktree snapshot: git-5cffcb6a-standard-service-before-fixture

## Current Claims

- `CURRENT · CONFIRMED · CLAIM-API-001`: the API rejects a whitespace-only `parcel_id` before calling the shared writer. Source: `service/api.py:L8-L12`.
- `CURRENT · CONFIRMED · CLAIM-WORKER-002`: the worker calls the shared writer without an equivalent boundary check. Sources: `service/worker.py:L27-L41` and `service/state.py:L8-L21`.
- `CURRENT · UNKNOWN · UNKNOWN:PROVIDER-ORDERING`: provider event ordering after a timed-out request is not established. Source: `README.md:L8-L10`.

## Purpose

The API rejects a blank parcel identifier, while the worker reaches the shared writer without that boundary check. The map prepares one task to enforce the invariant at the shared owner layer.

## Entry Point

The request path enters through `service/api.py:L8-L12`; the background path enters through `service/worker.py:L27-L41`.

## Inputs and Outputs

Both paths accept a parcel identifier and persist parcel state through `service/state.py:L8-L21`; before the change, the worker path can persist whitespace as an identifier.

## Dependencies

The mapped paths use Python, SQLite from the standard library, and the shared status writer.

## Verification

Command: `rg --no-config --sort path --line-number --fixed-strings 'record_status' service/api.py service/state.py service/worker.py`
Proof boundary: The bounded search proves that both mapped entry paths call the same shared writer and locates that writer definition.

## Exact Validation Result

Exit code: 0
Observed result: The command found both caller sites and the shared record_status definition in the three cited source files.
Stdout SHA-256: 7ca745e486fd7e0894bfe755163cfee5667856e1e021c3c08c72ea58803414dd

## Risks

The bounded map does not establish provider ordering, retry side effects, deployment behavior, or production readiness.

## Exclusions

Provider delivery semantics, administrator override behavior, deployment, and production data are excluded from this one-invariant denominator.

## Evidence Legend

- **CONFIRMED**: directly supported by a project-relative source or captured command.
- **INFERENCE**: reasoned from confirmed evidence but not directly observed.
- **HYPOTHESIS**: testable explanation that still requires discriminating evidence.
- **TARGET**: proposed future state, never evidence of current behavior.
- **UNKNOWN**: not established within the declared scope and snapshot.

## Next Safe Action

Build a bounded context packet for ATLAS-001, re-read its three source ranges, and test the invariant at both entry paths before changing the shared writer.

## Source References

- `service/api.py:L8-L12` owns request-boundary validation.
- `service/worker.py:L27-L41` owns the background delivery write path.
- `service/state.py:L8-L21` owns the shared persistent writer.
- `README.md:L8-L10` records authority and provider-ordering boundaries.

## Unknowns

- UNKNOWN:PROVIDER-ORDERING remains open because provider ordering after a timed-out request is not established.

## Finish Alignment

| Question ID | Batch ID | Topic | Question | Option A | Option B | Option C | Option D | Selected | Free-form note | Answer state | Map effect | Provenance | Answered at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FA-Q001 | FA-B001 | Candidate map | Is the one-invariant map and ATLAS-001 backlog ready for handoff? | Finish the bounded map and keep provider ordering open | Expand to provider retries now | Stop without a future task | Другое — напишу сам | A | - | ANSWERED | The candidate map is accepted and the unrelated provider gap stays open | USER_INPUT:FA-Q001 | 2026-07-26T10:45:00Z |

| Batch ID | Sequence | Question IDs | Remaining material gaps | Decision | Decision provenance | Status |
| --- | --- | --- | --- | --- | --- | --- |
| FA-B001 | 1 | FA-Q001 | None | STOP_STABLE | PROTOCOL:SEMANTIC_STOP | COMPLETE |

## Run Economics

| Run ID | Block ID | Entry | Block | Unit | Min | Typical | Max | Basis | Model tier and effort | Input | Output | Reasoning | Total | Telemetry | Variance vs typical | Recorded at | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RUN-BEFORE | MAP-BEFORE | PRE | Bounded invariant map | MODEL_TOKENS | 1200 | 1800 | 2800 | Three source paths, one task, and one unresolved provider boundary | balanced capability medium effort | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | 2026-07-26T09:55:00Z | MODELLED |
| RUN-BEFORE | MAP-BEFORE | POST | Bounded invariant map | MODEL_TOKENS | UNMEASURED | UNMEASURED | UNMEASURED | PRE:MAP-BEFORE | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | UNMEASURED | 2026-07-26T10:50:00Z | UNMEASURED |

## Future Tasks

| Task ID | Claim kind | Priority | Outcome | Basis | Affected areas | Scope | Non-goals | Acceptance criteria | Dependencies and unknowns | Risks | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ATLAS-001 | TARGET | P1 | Make every status write reject a blank parcel identifier | USER_INPUT:SA-Q001; service/api.py:L8-L12; service/worker.py:L27-L41; service/state.py:L8-L21; MAP:PROJECT_ATLAS.md#purpose | API, worker, and shared writer | Move the invariant to the shared writer and cover both entry paths | Provider retries, administrator authority, status design, and deployment | API blank input is rejected; worker blank input is rejected; valid API and worker writes remain persisted | No blocking dependency; preserve UNKNOWN:PROVIDER-ORDERING | A misplaced check could leave one path inconsistent or reject valid identifiers | Run the case-study regression and QUICK completion validation on the changed snapshot | READY |
