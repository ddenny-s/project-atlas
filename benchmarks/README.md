# Project Atlas effectiveness benchmark

This directory keeps three different statements separate:

1. **MEASURED** — paired fresh-session runs with raw, privacy-safe receipts.
2. **MODELLED_ASSUMPTION** — transparent scenarios calculated from visible inputs.
3. **EXTERNAL_EVIDENCE** — results from other systems or papers. They support the
   approach but are not Project Atlas measurements.

No percentage may move into the public README or an SVG without one of those
labels, its denominator, sample size or assumptions, calculation date, and a
reproducible source file.

## What a measured campaign compares

For every downstream task, run one `BASELINE` and one `ATLAS_USE` session with
the same:

- public fixture and task bytes;
- starting revision and permissions;
- adapter version, effective model, and reasoning effort;
- exact acceptance contract bytes and scoring oracle;
- fresh-session boundary.

Counterbalance the order of the two conditions. Keep failures and repair turns.
Build the frozen atlas in a separate `ATLAS_BUILD` receipt. Record refresh work
as `ATLAS_REFRESH`.

Receipts store hashes and metrics, not prompts, transcripts, source contents,
secrets, local absolute paths, or human-readable oracle labels. Two digests
serve different purposes:

- `acceptance_contract_sha256` binds the exact acceptance-criteria bytes given
  to both sessions in one `BASELINE` / `ATLAS_USE` pair. The validator requires
  the digest on both receipts and rejects a pair when the values differ.
  Different downstream tasks may have different acceptance contracts, so this
  digest is pair-locked rather than campaign-wide.
- `oracle_sha256` binds the evaluator's expected-item set. The validator
  recomputes it from the sorted expected-item hashes. It is not a substitute
  for the acceptance contract and does not prove what instructions the agent
  saw.

Expected and observed oracle items are SHA-256 identifiers. A public campaign
also publishes the corresponding acceptance-contract and oracle manifests so
reviewers can verify their semantic content outside the privacy-safe receipt.
Token totals count only when the provider reports them. Benchmark
receipts do not accept weekly-quota fields at all; Atlas may show an exact host
signal in the run map, but token counts are never converted into subscription
quota.

## Metrics

- contract pass rate;
- contract-pass percentage-point and relative change;
- exact-set precision, recall, and F1;
- unsafe, dangling, and unclassified reference counts;
- paired wall time;
- provider-reported paired token totals;
- build and refresh cost;
- gross per-task saving;
- net per-task saving after amortized refresh cost;
- break-even task count from the **net** saving.

Time and tokens are calculated separately. They are not converted into money
unless a later campaign supplies an explicit hourly rate and provider prices.
Gross token savings are calculated over the subset of complete pairs where
both receipts contain provider-reported totals; `sample_n` and
`exact_token_pairs` expose that subset. Net token saving and token break-even
are emitted only when every complete pair has exact token totals and the build
and every refresh have provider-reported totals. Otherwise those net fields are
`null`; a partial exact subset must not be extrapolated across missing costs.
Contract-pass rate is
`contract_pass_receipts / complete_pairs × 100`. For each reference-integrity
metric, the paired delta is
`Σ(ATLAS_USE metric − BASELINE metric)` and the per-task mean divides the
condition total by the number of complete pairs. The derived JSON carries these
formula definitions beside the calculated values.
One measured campaign represents one map lineage, one fixture/revision, and a
counterbalanced set of byte-unique downstream tasks. Each pair's real,
non-overlapping timestamps must agree with its declared order. The whole
campaign is sequential: no two measured receipt intervals may overlap across
pairs or among `ATLAS_BUILD`, `ATLAS_USE`, and `ATLAS_REFRESH`; intervals may
touch at an endpoint. This prevents competing runs from contaminating wall
time. The campaign starts with exactly one
`ATLAS_BUILD`; every `ATLAS_USE` and `ATLAS_REFRESH` is linked to the map hash
available at its timestamp. Wall time must come from a host monotonic clock and
both measurements must be at least 100 ms. Wall time must agree with receipt
timestamps within `max(5 ms, 2% of timestamp duration)`.
This keeps the break-even denominator meaningful instead of mixing unrelated
projects, repeated tasks, or model settings.

## Reproduce the modelled scenarios

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/benchmark_atlas.py model \
  --input benchmarks/data/modelled/v0.1.0.json \
  --check benchmarks/data/derived/modelled-v0.1.0.json
```

## Derive a measured campaign

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/benchmark_atlas.py derive \
  --input-dir benchmarks/data/raw/<campaign-id> \
  --check benchmarks/data/derived/<campaign-id>.json
```

The repository does not contain private transcripts. A measured public
campaign is releasable only after receipt schema, hash, privacy, reproducibility,
and independent-review checks pass.
