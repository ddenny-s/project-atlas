# User Interaction and Budget

This reference owns the host-independent rules for owner alignment, investigation forecasts, measured usage, and future-task routing.

## Alignment phases

`START_ALIGNMENT` is mandatory before deep work. Read applicable instructions, establish the read-only safety boundary, and perform only the cheap preflight needed to ask useful questions before starting it. Use it to settle scope, authority, mode intent, claim interpretation, output routing, and the decision the atlas must support.

`FINISH_ALIGNMENT` is mandatory after the candidate map and candidate future-task backlog exist. Run it before completion validation and handoff. In FORENSIC mode, run it before creating the final source snapshot or requesting independent reviews. If an answer changes the map or backlog, update the affected artifacts, mark superseded answer and batch records, and repeat `FINISH_ALIGNMENT` against the new candidate until it reaches a valid stop.

## Adaptive question loop

There is no total question cap. Each batch contains one to three questions. Continue only when the next answer can materially change at least one of:

- scope;
- authority;
- depth mode;
- interpretation of a material claim;
- output routing or contract;
- the future-task backlog.

Otherwise stop semantically with `STOP_STABLE`. Do not ask preference, confirmation, or curiosity questions that cannot change one of those decisions.

Every visible question has exactly four choices labelled A through D. A, B, and C are contextual, materially distinct choices. D is always exactly:

```text
Другое — напишу сам
```

Unknown, skip, and stop are separate controls, not extra choices:

- unknown records `Selected` as `-` and `Answer state` as `UNAVAILABLE`;
- skip records `Selected` as `-` and `Answer state` as `SKIPPED`;
- user stop ends the batch ledger with `STOP_USER`;
- a question invalidated by later context is retained as `SUPERSEDED`.

An answered custom response selects D and stores the response in `Free-form note`. Use stable question IDs such as `SA-Q001` and `FA-Q001`, stable batch IDs such as `SA-B001`, UTC timestamps, and semicolon-separated Question IDs in the batch ledger.

If a host picker cannot display exactly four choices or cannot preserve the exact D label, render A through D in plain chat and accept the answer there. Host limitations never change the four-choice contract.

### Question table

| Question ID | Batch ID | Topic | Question | Option A | Option B | Option C | Option D | Selected | Free-form note | Answer state | Map effect | Provenance | Answered at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

`Selected` is A, B, C, or D for `ANSWERED`, and `-` for `SKIPPED`, `UNAVAILABLE`, or `SUPERSEDED`. `Answer state` is exactly `ANSWERED`, `SKIPPED`, `UNAVAILABLE`, or `SUPERSEDED`. An active answered row uses `USER_INPUT:<Question ID>` provenance. `Map effect` names the affected map fields or records that the answer confirmed the candidate without changing it. A superseded row uses `-` for its note and map effect while retaining its original user-input provenance and UTC answer time.

### Batch ledger

| Batch ID | Sequence | Question IDs | Remaining material gaps | Decision | Decision provenance | Status |
| --- | --- | --- | --- | --- | --- | --- |

`Decision` is exactly `CONTINUE`, `STOP_STABLE`, `STOP_USER`, or `STOP_UNAVAILABLE`. `STOP_STABLE` is valid only when `Remaining material gaps` is exactly `None`; its provenance is `PROTOCOL:SEMANTIC_STOP`. User stop names each unresolved gap as `UNKNOWN:<stable-id>` and uses `USER_INPUT:<stable-id>` provenance. An unavailable stop names each unresolved gap as `UNKNOWN:<stable-id>` and uses `UNAVAILABLE:<stable-id>` provenance. A continued batch names the remaining gap as `UNKNOWN:<stable-id>` and cites the relevant `USER_INPUT:<stable-id>` or `PROTOCOL:MATERIAL_GAP:<stable-id>`.

`Status` is `ACTIVE`, `COMPLETE`, or `SUPERSEDED`. `ACTIVE` is allowed only for an unresolved draft continuation. Completion has no `ACTIVE` batch, retains superseded history, and has one final `COMPLETE` stop decision.

## Provenance boundary

Owner answers are `USER_INPUT` provenance. They may establish requested scope, authority, intended outcomes, TARGET direction, output preferences, prioritization, acceptance wording, and backlog decisions. They never establish a technical current-state fact. A statement about current implementation supplied by the owner remains a lead and must be verified from technical evidence or classified as `HYPOTHESIS` or `UNKNOWN`.

In FORENSIC traceability, each active answered direction row is a `TARGET` claim:

```text
PRODUCT_AND_REQUIREMENTS.md#direction/<Question ID>
LIVE_HANDOFF.md#direction/<Question ID>
```

START uses the first form and FINISH uses the second. The exact claim is `<Question>: <Selected option>`. The trace row uses source type `EXTERNAL` and source reference `USER_INPUT:<Question ID>`. These direction links do not replace technical evidence for current-state claims.

## Run forecast and telemetry

Use the deterministic host-neutral planning helper when available:

```text
PYTHONDONTWRITEBYTECODE=1 python3 <skill-dir>/scripts/atlas.py estimate-budget --project <project> --mode <mode>
```

Its output is `MODELLED` with basis `ASSUMPTION` and unit `MODEL_TOKENS`. It uses safe-inventory metadata rather than opening excluded content, and it explicitly excludes pre-existing conversation and any quota conversion. Its maximum means `REFORECAST_THRESHOLD_NOT_HARD_CAP`. This planning output never becomes POST telemetry and does not replace a block-specific PRE row with current assumptions.

Before the initial deep-work block, show the block forecast and record a PRE row. Do the same before every later deep-work block. A PRE forecast uses:

- `Unit` exactly `MODEL_TOKENS`;
- positive integer `Min`, `Typical`, and `Max`, ordered `Min <= Typical <= Max`;
- assumptions in `Basis`, including bounded input size, expected output, uncertainty, and reasoning complexity;
- a host-neutral capability tier and effort setting in `Model tier and effort`;
- `UNMEASURED` in `Input`, `Output`, `Reasoning`, `Total`, `Telemetry`, and `Variance vs typical`;
- `MODELLED` status.

`Max` is a reforecast threshold, not a hard cap and not authorization to overrun a safety boundary. When the block reaches or is expected to cross it, stop at a safe checkpoint, record the available POST telemetry, narrow or split the next block as needed, and write a new PRE row before continuing.

After each deep block, record a POST row. `Input`, `Output`, `Reasoning`, and `Total` contain only exact host-reported model-token integers or `UNMEASURED`; never estimate missing telemetry. If all three components are reported, their sum must equal the reported total. POST `Min`, `Typical`, and `Max` are `UNMEASURED`, `Basis` points to `PRE:<Block ID>`, and the capability/effort cell records the generic setting actually used or `UNMEASURED`. `Telemetry` identifies the exact host signal or is `UNMEASURED`. `Variance vs typical` is the exact signed difference between a host-reported total and the matching PRE typical value, otherwise `UNMEASURED`. POST status is `MEASURED` when exact host telemetry is present and `UNMEASURED` otherwise.

### Run Economics

| Run ID | Block ID | Entry | Block | Unit | Min | Typical | Max | Basis | Model tier and effort | Input | Output | Reasoning | Total | Telemetry | Variance vs typical | Recorded at | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

`Entry` is exactly `PRE` or `POST`. Do not convert model tokens into context-window, daily, weekly, monetary, or quota percentages. Show a weekly-usage line only when the host supplies that exact weekly signal; otherwise omit the line entirely.

## Future tasks

Create every task justified by the accepted map; there is no fixed task count. Mapping does not authorize implementation.

| Task ID | Claim kind | Priority | Outcome | Basis | Affected areas | Scope | Non-goals | Acceptance criteria | Dependencies and unknowns | Risks | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Every row has `Claim kind` `TARGET` and status `READY`, `BLOCKED`, `SUPERSEDED`, or `REJECTED`. Every active `READY` or `BLOCKED` row has substantive, non-draft values for `Outcome`, `Basis`, `Affected areas`, `Scope`, `Non-goals`, `Acceptance criteria`, `Dependencies and unknowns`, `Risks`, and `Verification`.

Both active statuses need a technical `Basis`: either a safe project-relative source or `MAP:<atlas-file>#<stable-anchor>`. A map reference must resolve to a visible, unique, non-interaction level-two section with substantive content in an Atlas artifact for the selected mode. A `READY` task additionally cites an active answered `USER_INPUT:<Question ID>` in `Basis`; user input may confirm direction or priority but cannot replace the technical basis. A `BLOCKED` task may omit only that owner input. It still needs the same substantive fields and technical basis, and names the unresolved dependency as canonical `UNKNOWN:<stable-id>` in `Dependencies and unknowns`. If any task cites `USER_INPUT`, every such reference must resolve to an active answer and must not dangle. At completion, the table contains at least one `READY` task or one honest `BLOCKED` task; it may contain any justified number of additional rows.

In FORENSIC traceability, each active READY or BLOCKED task uses:

```text
MIGRATION_PLAN.md#future-tasks/<Task ID>
```

The ledger claim kind is `TARGET` and the exact claim is the task's `Outcome`.
