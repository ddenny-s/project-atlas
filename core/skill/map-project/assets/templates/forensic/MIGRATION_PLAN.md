# Migration Plan

## Preconditions

Current-state coverage, target ownership, data safety, and rollback authority must be accepted first.

## Sequence

| Stage | Claim kind | Change | Preconditions | Compatibility and state/data handling | Primary signal | Secondary signals | Decision authority | Rollback | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Mixed-Version and Data Safety

Backward compatibility, ordering, backfill, replay, and concurrent-writer behavior remain UNKNOWN.

## Rollback

Define a tested rollback action, data restoration path, decision owner, and point of no return for each step.

## Completion Gate

Require observable behavior at every changed boundary and record any unavailable primary signal.
