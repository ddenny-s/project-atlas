# Project Atlas technical documentation

[English](./README.md) · [Русский](./README.ru.md) · [Product overview](../README.md)

This is the third layer of the public documentation. Start with the
[30-second explanation](../README.md#30-seconds) or
[five-minute first run](../README.md#five-minutes-to-your-first-run) if you do
not need the protocol details yet.

## Source-of-truth map

| Question | Authoritative source |
| --- | --- |
| What evidence labels and workflow mean | [`core/PROTOCOL.md`](../core/PROTOCOL.md) |
| How claims, traces, coverage, and review work | [`methodology.md`](./methodology.md) |
| How QUICK, STANDARD, and FORENSIC are selected | [`depth-levels.md`](./depth-levels.md) |
| What every artifact must contain | [`outputs.md`](./outputs.md) |
| How Codex and Claude Code package the protocol | [`adapters.md`](./adapters.md) |
| How to invoke representative workflows | [`examples.md`](./examples.md) |
| How a map becomes a verified code change | [`case-study.md`](./case-study.md) |
| What may be read, written, or published | [`SECURITY.md`](../SECURITY.md) |
| What the benchmark can and cannot claim | [`benchmarks/`](../benchmarks/) |

`core/PROTOCOL.md` is normative. Documentation explains it; adapters translate
host discovery and permissions. Neither adapter may redefine evidence labels,
depth semantics, output contracts, or safety boundaries.

## The protocol in one route

```text
BOUND
  project root · exclusions · product purpose · cost of error
    ↓
FORECAST
  min · typical · max model tokens for the next material block
    ↓
DISCOVER + CLASSIFY
  runtime · data · state · authority · tests · risks
  CONFIRMED · INFERENCE · HYPOTHESIS · TARGET · UNKNOWN
    ↓
ALIGN
  owner review · adaptive questions · corrected scope and direction
    ↓
DELIVER
  atlas · Future Tasks · handoff · validation
    ↓
USE
  Task Context Packet · source recheck · implementation · tests · refresh
```

## Design boundaries

- Mapping permission does not authorize implementation, infrastructure changes,
  production access, or destructive commands.
- `TARGET` describes intended future state. It is never presented as current.
- `USER_INPUT` can establish owner intent but cannot confirm current code
  behavior.
- Structural validation does not prove semantic truth, completeness, or
  production readiness.
- A downstream task rechecks source freshness and carries only a bounded Task
  Context Packet, not the entire atlas by default.
- Unknowns remain open until new evidence closes them.

## Reproducible checks

From the repository root:

```bash
python3 scripts/sync_adapters.py --check
python3 -m unittest discover -s tests -v
git diff --check
```

Run the public map-to-change example only:

```bash
python3 -m unittest tests.test_documentation_case_study -v
```

Modelled effectiveness inputs remain versioned as `v0.1.0` benchmark data
because v0.1.1 changes documentation and package metadata, not the benchmark
dataset or protocol behavior.

## Reading order by job

| Job | Read |
| --- | --- |
| First small map | [Examples](./examples.md) → [QUICK](./depth-levels.md#quick) |
| Refactor or migration | [STANDARD](./depth-levels.md#standard) → [Outputs](./outputs.md) |
| High-consequence audit | [FORENSIC](./depth-levels.md#forensic) → [Methodology](./methodology.md) |
| Build another native adapter | [Protocol](../core/PROTOCOL.md) → [Adapters](./adapters.md) |
| Evaluate the value claim | [Case study](./case-study.md) → [Benchmarks](../benchmarks/) |

Project Atlas is a community project, not an official OpenAI or Anthropic
product.
