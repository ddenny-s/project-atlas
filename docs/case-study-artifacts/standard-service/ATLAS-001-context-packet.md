# Task Context Packet: ATLAS-001

Source map: `before/PROJECT_ATLAS.md`
Source snapshot: `git-5cffcb6a-standard-service-before-fixture`
Prepared at: `2026-07-26T11:00:00Z`

## Task

Claim kind: `TARGET`
Status: `READY`
Priority: `P1`
Outcome: Make every status write reject a blank parcel identifier.

## Basis

- Owner direction: `USER_INPUT:SA-Q001`.
- API boundary: `service/api.py:L8-L12`.
- Worker write path: `service/worker.py:L27-L41`.
- Shared writer: `service/state.py:L8-L21`.
- Map anchor: `MAP:PROJECT_ATLAS.md#purpose`.

## CURRENT Claims

- `CLAIM-API-001` · `CONFIRMED`: the API rejects a whitespace-only `parcel_id` before calling the shared writer.
- `CLAIM-WORKER-002` · `CONFIRMED`: the worker calls the shared writer without an equivalent boundary check.

## Owning Layer

`service/state.py:L8-L21` is the shared persistence boundary used by both mapped entry paths. The invariant belongs there so the API and worker cannot diverge.

## Authority Boundary

The administrator override in `service/authority.py:L4-L10` is unchanged. ATLAS-001 validates identifiers only and does not alter who may override automatic status.

## Related Unknown

`UNKNOWN:PROVIDER-ORDERING` remains open. Provider event ordering after a timed-out request is not established by this task.

## Scope

- Add the whitespace-only identifier guard to the shared writer.
- Verify rejection through both API and worker paths.
- Verify valid API and worker writes remain persisted.

## Non-goals

- Provider retry behavior.
- Provider event ordering.
- Administrator authority.
- Status model redesign.
- Deployment or production readiness.

## Acceptance Criteria

1. A whitespace-only `parcel_id` is rejected through the API path.
2. A whitespace-only `parcel_id` is rejected through the worker path.
3. A valid API parcel remains persisted with writer `api`.
4. A valid worker parcel remains persisted with writer `worker`.
5. `UNKNOWN:PROVIDER-ORDERING` remains visible and unresolved.
6. The refreshed QUICK map passes completion validation against the changed snapshot.

## Required Checks

- Reproduce the before worker write of a whitespace-only identifier.
- Apply the published `ATLAS-001.patch` only to the temporary case-study copy.
- Exercise both blank entry paths.
- Exercise one valid write through each entry path.
- Run the after-map bounded `rg --no-config` evidence command.
- Run Atlas completion validation with the patched temporary copy as `--project`.

## Freshness

- `service/api.py:L8-L12` reread on `git-5cffcb6a-standard-service-before-fixture`.
- `service/worker.py:L27-L41` reread on the same snapshot.
- `service/state.py:L8-L21` reread on the same snapshot.
- `service/authority.py:L4-L10` reread on the same snapshot.
- The source references resolve inside `tests/fixtures/standard_service`.

## Excluded Context

Provider implementation details, deployment files, production data, unrelated Atlas fixtures, and the full STANDARD architecture map are intentionally excluded.
