# Migration Plan

## Preconditions

Current-state coverage, target ownership, data safety, and rollback authority must be accepted first.

## Sequence

| Stage | Claim kind | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Future Tasks

Record every justified task without implementing it. Both `READY` and `BLOCKED` need substantive, non-draft values in `Outcome`, `Basis`, `Affected areas`, `Scope`, `Non-goals`, `Acceptance criteria`, `Dependencies and unknowns`, `Risks`, and `Verification`, plus a technical `Basis`: a safe project-relative source or a visible, unique, non-interaction Atlas section with substantive content referenced as `MAP:<atlas-file>#<stable-anchor>`. `READY` also needs an active `USER_INPUT:<Question ID>` in `Basis`. `BLOCKED` may omit only this owner input; it still needs the technical basis and canonical `UNKNOWN:<stable-id>` in `Dependencies and unknowns`. Any cited `USER_INPUT` must be active and non-dangling.

| Task ID | Claim kind | Priority | Outcome | Basis | Affected areas | Scope | Non-goals | Acceptance criteria | Dependencies and unknowns | Risks | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Mixed-Version and Data Safety

Backward compatibility, ordering, backfill, replay, and concurrent-writer behavior remain UNKNOWN.

## Rollback

Define a tested rollback action, data restoration path, decision owner, and point of no return for each step.

## Completion Gate

Require observable behavior at every changed boundary and record any unavailable primary signal.
