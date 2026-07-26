# Usage Examples

The examples below use explicit invocation so the intended adapter and workflow are unambiguous. Codex is the primary adapter; equivalent Claude Code invocations follow each core scenario.

## 1. QUICK map of a small CLI

Codex:

```text
Use $project-atlas:map-project to create a QUICK map of this small CLI.
```

Claude Code:

```text
/project-atlas:map-project Create a QUICK map of this small CLI.
```

Expected shape: one `PROJECT_ATLAS.md` with scope and depth rationale, observation time or snapshot, purpose, entrypoint, inputs, outputs, dependencies, exclusions, an evidence legend, verification and its exact result, risks, unknowns, project-relative source references, and the next safe action. The workflow should not create a large document tree.

## 2. STANDARD atlas before refactoring

Codex:

```text
Use $project-atlas:map-project to build a STANDARD current-state and target-state atlas before we refactor this service.
```

Claude Code:

```text
/project-atlas:map-project Build a STANDARD current-state and target-state atlas before we refactor this service.
```

Expected shape: routed current architecture, runtime, data and authority, priority flows, quality and operations, findings, target architecture, migration plan, open unknowns, and handoff. Mapping alone does not authorize the refactor.

## 3. FORENSIC investigation

Codex:

```text
Use $project-atlas:map-project in FORENSIC mode. Map every runtime root, data store, state writer, authority boundary, recovery path and test gap. Do not implement changes until I approve the atlas.
```

Claude Code:

```text
/project-atlas:map-project Use FORENSIC mode. Map every runtime root, data store, state writer, authority boundary, recovery path and test gap. Do not implement changes until I approve the atlas.
```

Expected shape: explicit denominators, complete registries for the declared scope, traceability, quantitative coverage, open unknowns, source snapshot, reproducible checks, independent review, and a safe migration plan. The implementation gate is explicit.

## 4. Incremental refresh and drift

Codex:

```text
Use $project-atlas:map-project to refresh the existing atlas incrementally and report drift.
```

Claude Code:

```text
/project-atlas:map-project Refresh the existing atlas incrementally and report drift.
```

Expected behavior: read the existing index and handoff, preserve user additions, compare cheap drift indicators, revalidate affected claims, mark stale evidence, and report added, changed, removed, reverified, and unresolved items.

## 5. Continuation handoff

Codex:

```text
Use $project-atlas:map-project to prepare a continuation handoff for another Codex session.
```

Claude Code:

```text
/project-atlas:map-project Prepare a continuation handoff for another Claude Code session.
```

Expected behavior: update `LIVE_HANDOFF.md` with the source snapshot, current worktree evidence, completed scope, last validations, unresolved risks, preservation rules, and the next bounded actions.

## Specify an output path

```text
Use $project-atlas:map-project in STANDARD mode. Write the atlas under architecture/evidence, preserve any existing documents there, and report every file changed.
```

An explicit location overrides default routing. Initialization must refuse a silent overwrite; refresh must inspect and preserve existing content.

## Define exclusions and privacy boundaries

```text
Use $project-atlas:map-project in STANDARD mode. Do not open .env files, credential stores, private keys, production exports, vendor trees, or generated binaries. Record excluded paths and resulting unknowns. Keep all evidence repository-relative.
```

Exclusions are part of the atlas scope. The workflow should not infer the contents of excluded sources.

## Map only, with an implementation gate

```text
Use $project-atlas:map-project to map the current authorization flow and propose a target design. Do not modify application code, configuration, data, infrastructure, or deployments. Stop at the reviewed migration plan.
```

This distinguishes investigation permission from implementation permission.

## Focus on state and authority

```text
Use $project-atlas:map-project in FORENSIC mode. For every material state object, list its store, schema, readers, writers, lifecycle, authority, conflict resolution, retry, idempotency, rollback and recovery evidence. Mark every unsupported edge UNKNOWN.
```

This is appropriate when automatic decisions or competing writers make a general architecture summary insufficient.

## Audit what tests prove

```text
Use $project-atlas:map-project in STANDARD mode. Map each critical product claim to tests or runtime evidence, state the mock boundary, and list what remains unproved. Do not use green test counts as a production-readiness conclusion.
```

## Start with automatic depth selection

```text
Use $project-atlas:map-project to assess this repository, choose QUICK, STANDARD or FORENSIC, explain the decisive signals, then create the corresponding atlas. Do not read excluded or high-volume trees merely to estimate size.
```

The selection explanation becomes part of the atlas index or QUICK document.

## Standalone Codex installation

When Project Atlas is installed as a standalone skill rather than a plugin, use the unnamespaced invocation:

```text
Use $map-project to create a QUICK map of this small CLI.
```

The protocol and outputs remain the same; only discovery and invocation differ.
