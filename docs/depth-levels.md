# Depth Levels

Project Atlas scales the investigation to the decision and risk. QUICK, STANDARD, and FORENSIC are different evidence contracts, not cosmetic document presets.

## Selection factors

Consider all relevant factors before deep discovery:

| Factor | Lower-depth signal | Higher-depth signal |
| --- | --- | --- |
| Cost of error | Easy to detect and reverse | Financial, safety, legal, privacy, or data-loss impact |
| Production exposure | Local or disposable | Active users, critical operations, or irreversible effects |
| Runtime topology | One obvious process | Multiple services, workers, schedulers, queues, or hidden triggers |
| State | No durable state | Multiple stores, migrations, caches, replicas, or partial states |
| Authority | One human-controlled path | Automated decisions, overrides, conflicting writers, or external providers |
| Data sensitivity | Public or synthetic | Personal, financial, regulated, credential, or customer data |
| Implementation history | One current path | Legacy overlap, forks, flags, duplicate or conflicting implementations |
| Team and lifespan | One maintainer, short-lived | Multiple teams or agents, long-lived product |
| Decision scope | Orientation only | Refactor, migration, incident prevention, or audit evidence |

Repository size can increase cost, but it does not determine depth by itself. A small payment worker may need FORENSIC; a large generated codebase may need only a routed STANDARD map.

Automatic selection keeps tests, fixtures, templates, examples, and nested documentation in the safe inventory, but does not treat their vocabulary as product topology or high-impact product risk. Semantic risk signals come from bounded root project-declaration paragraphs or explicit operator inputs. Compound FORENSIC reasons require their evidence to occur in the same declaration unit; a storage transaction mentioned in one paragraph cannot combine with unrelated legacy compatibility text in another. Byte-identical packaged adapter Skill copies may collapse into their canonical core signal, while unrelated identical services remain separate runtimes.

Depth changes investigation coverage, not the owner-question cap. Every mode completes START alignment before deep work and FINISH alignment after its candidate map. Questions remain adaptive in one-to-three-question batches until the next answer cannot materially change scope, authority, mode, claim interpretation, output, or backlog. Every deep block also receives a PRE model-token forecast and an exact-or-`UNMEASURED` POST record.

## QUICK

Use QUICK for a small, low-risk, one-purpose project or for initial orientation when no consequential decision depends on complete coverage.

Required result:

- one `PROJECT_ATLAS.md`;
- START and FINISH alignment records;
- purpose, user, and observable outcome;
- launch or invocation path;
- primary inputs, outputs, state, and dependencies;
- exactly one bounded deterministic `rg --no-config` verification command;
- scope and depth rationale, observation time or snapshot, exclusions, and an evidence legend;
- known risks, explicit unknowns, project-relative references, and the next safe action;
- the exact result of the documented validation command;
- source references for material claims;
- run economics and at least one traceable READY or honest BLOCKED future task.

QUICK deliberately avoids a large document tree, exhaustive registries, speculative target architecture, and broad full-repository reading. It can still identify a trigger that warrants STANDARD or FORENSIC.

QUICK passes when a new reader can locate the main path, understand the evidence boundary, run the documented check, and see what remains unknown.

## STANDARD

Use STANDARD for an active application, service, or library where maintainers need a reliable current-state map and a reasoned target state before making changes.

Required coverage:

- product purpose and major requirements;
- current architecture and principal runtime roots;
- important user and operational flows;
- configuration and environment behavior;
- material data, state, readers, writers, and authority;
- failure handling and recovery on important paths;
- test coverage and proof boundaries;
- security, reliability, observability, and operational findings;
- current findings and dispositions;
- target architecture and staged migration plan;
- traceable future tasks that are not automatically implemented;
- START alignment in product requirements and FINISH alignment plus run economics in handoff;
- open unknowns, routed index, and live handoff.

STANDARD uses multiple documents when that improves routing. It does not require exhaustive enumeration of every file or low-impact helper.

STANDARD passes when the major product paths and decisions are traceable, current and target states are distinct, coupled layers have been inspected, and another session can continue from the handoff.

## FORENSIC

Use FORENSIC for critical, old, confusing, multi-service, production-sensitive, data-sensitive, or authority-heavy systems. Choose it when false confidence is more expensive than the investigation.

FORENSIC includes all STANDARD requirements plus:

- reproducible structural denominators and scope exclusions;
- complete runtime-root and entrypoint registries for the declared scope;
- call, data, state, and effect relationships for material contours;
- state-object reader and writer registries;
- explicit authority and conflict-resolution maps;
- retry, idempotency, rollback, replay, and recovery coverage;
- a machine-checkable traceability ledger;
- quantitative coverage with defined populations;
- source snapshots and reproducible commands;
- disposition for every material contour;
- independent challenge of high-impact claims;
- final owner direction and future-task claims bound before snapshot and review;
- safe multi-session continuation.

FORENSIC passes only when every material boundary in the declared population is confirmed or explicitly listed as `UNKNOWN`, coverage calculations name their denominator, and independent review finds no unresolved critical or important correctness gap in the atlas itself.

## Explicit mode and escalation

The user's explicit mode wins. The agent must not silently turn QUICK into a multi-day audit or silently reduce FORENSIC because the repository is large.

Every completed mode retains the depth decision as four single-value fields in its canonical scope section:

- `Selected by`;
- `Conflicting automatic signals`;
- `Intentionally omitted coverage`;
- `Escalation condition`.

When the automatic recommendation matched, say so; a bare `NONE`, `N/A`, empty value, or `UNKNOWN` is not a completed decision record.

If evidence reveals higher risk than the chosen mode:

1. finish the safe, bounded work already in scope;
2. record the trigger and the missing coverage;
3. recommend the next depth with a concrete reason;
4. expand only when authorized or when the original request already covers that work.

If the selected mode is deeper than useful, explain the lower-cost option, but preserve the requested mode unless the user changes it.

## Switching modes during refresh

Mode changes do not erase prior artifacts:

- QUICK → STANDARD imports supported facts, assigns stable fact identifiers, and adds routed documents.
- STANDARD → FORENSIC establishes denominators, expands registries, and independently challenges material claims.
- FORENSIC → STANDARD or QUICK produces a maintained summary while retaining the deeper evidence set unless deletion is explicitly requested.

Record the previous mode, new mode, reason, preserved artifacts, and changed proof boundary in the index and handoff.
