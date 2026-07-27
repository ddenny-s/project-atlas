# Project Atlas effectiveness model: possible savings and break-even

[English](./effectiveness.md) · [Русский](./effectiveness.ru.md) · [Technical docs](./README.md)

> Nothing magical is hiding in these percentages. The total spend comes first,
> followed by the formula and the inputs. Every Project Atlas number on this
> page comes from an open calculation model, not observed results from real
> users.

## The key result in 20 seconds

|  | Result from the calculation model |
| --- | --- |
| **−18.6% tokens** | illustrative scenario over 10 tasks: **700,000 → 570,000** |
| **task 6** | modelled token break-even in the illustrative scenario |
| **up to −51.1% tokens** | optimistic scenario over 10 tasks: **900,000 → 440,000** |
| **0 real campaigns** | no public paired "without Atlas / with Atlas" measurements yet |

The identifier `illustrative_mid` means the middle of three manually defined
examples. It is not the mean, median, or typical result for users.

This means:

- `+40% spend` is bad: Atlas used 40% more tokens;
- `−18.6% spend` is good: Atlas saved 18.6% of the tokens;
- `−51.1% spend` is good: Atlas saved 51.1% of the tokens.

The sign always applies to the **change in spend**, not to an abstract
"effectiveness" score:

```text
+ spend increased
− spend decreased
```

## Why `+40%` means overspending

In the unfavorable scenario over 10 tasks:

```text
without Atlas: 600,000 tokens
with Atlas:    840,000 tokens
difference:   +240,000 tokens

240,000 / 600,000 × 100 = +40%
```

Atlas therefore cost `1.4` times as much as working without it. This is not a
40% effectiveness gain. It is **40% overspending**.

Why can this happen at all? Building the map has an upfront cost. If that cost
is high, the task series is short, or each task is already cheap without the
map, the upfront cost does not have time to pay back.

## Why `−18.6%` and `−51.1%` mean savings

Illustrative scenario:

```text
without Atlas: 700,000 tokens
with Atlas:    570,000 tokens
difference:   −130,000 tokens

−130,000 / 700,000 × 100 = −18.6%
```

Optimistic scenario:

```text
without Atlas: 900,000 tokens
with Atlas:    440,000 tokens
difference:   −460,000 tokens

−460,000 / 900,000 × 100 = −51.1%
```

The map still costs tokens in both scenarios. Savings appear later because
subsequent tasks receive the context they need from the map instead of paying
the full cost of learning the project again.

## Three scenarios over 10 tasks

### Tokens

| Scenario | Without Atlas | Build the map | One task with Atlas and refresh | Total with Atlas | Change in spend | Break-even |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Unfavorable | 600,000 | 300,000 | 54,000 | 840,000 | **+40.0%** | task 50 |
| Illustrative | 700,000 | 170,000 | 40,000 | 570,000 | **−18.6%** | task 6 |
| Optimistic | 900,000 | 120,000 | 32,000 | 440,000 | **−51.1%** | task 3 |

The "Without Atlas" column already shows the total for 10 tasks. "One task with
Atlas and refresh" is multiplied by 10, then the one-time map cost is added.

### Time

| Scenario | Without Atlas | Build the map | One task with Atlas and refresh | Total with Atlas | Change in spend | Break-even |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Unfavorable | 250 min | 240 min | 23 min | 470 min | **+88.0%** | task 120 |
| Illustrative | 300 min | 120 min | 16 min | 280 min | **−6.7%** | task 9 |
| Optimistic | 400 min | 75 min | 12 min | 195 min | **−51.2%** | task 3 |

Tokens and time are calculated separately. Token savings do not guarantee the
same time savings.

## When Atlas breaks even

**Break-even** is the first task at which cumulative spend with Atlas becomes
no higher than spend without Atlas.

In the illustrative scenario:

- at 5 tasks, tokens have not broken even: `350,000` without Atlas versus
  `370,000` with Atlas;
- at task 6, tokens have broken even: `420,000` without Atlas versus `410,000`
  with Atlas;
- at task 9, time has broken even: `270` minutes without Atlas versus `264`
  minutes with Atlas.

A simple decision table for the illustrative model:

| Work horizon | What the model says |
| --- | --- |
| 1–5 tasks | Atlas still costs more in both tokens and time |
| 6–8 tasks | tokens have broken even; time has not |
| 9+ tasks | both tokens and time have broken even |

This is not a promise of results. It is guidance under the inputs recorded in
the model.

## When Atlas makes sense

Atlas can make economic sense when:

- the project will live beyond one task;
- different sessions or agents repeatedly study the same repository;
- important relationships are spread across several files, services, or data
  stores;
- an error caused by missing context costs more than building the map;
- the map will be refreshed after changes.

The clearest case is a planned series of related changes where each new coding
agent otherwise spends time asking, "Where is the entry point?", "What owns
this decision?", and "What else will this change break?"

## When Atlas does not make sense

Do not pay for a map when:

- there is one small, obvious task;
- the project will soon be discarded;
- the relevant code is in one clear file;
- nobody will reuse the map;
- the map will become stale and nobody will refresh it;
- the expected task series is shorter than the break-even point.

The unfavorable scenario exists to support this decision. It shows that Atlas
is not a free accelerator for every project.

## Formula in plain English

Without Atlas, every new task pays again to learn the project:

```text
Spend without Atlas =
spend for one ordinary task × number of tasks
```

With Atlas, the map is paid for once. Each task then pays for work with the
prepared context and for refreshing the map:

```text
Spend with Atlas =
building the map
+ (work from the map + refresh) × number of tasks
```

Change in spend:

```text
(spend with Atlas − spend without Atlas)
──────────────────────────────────────── × 100%
          spend without Atlas
```

Break-even point:

```text
cost of building the map
───────────────────────────────────
savings on one task after refresh
```

The result is rounded up to a whole task. If one task with Atlas and refresh is
not cheaper than an ordinary task, the map never breaks even in this model.

## Where the numbers come from

The input assumptions are stored in
[`benchmarks/data/modelled/v0.1.0.json`](../benchmarks/data/modelled/v0.1.0.json).
The calculated output is stored in
[`benchmarks/data/derived/modelled-v0.1.0.json`](../benchmarks/data/derived/modelled-v0.1.0.json).
The formulas are executed by
[`scripts/benchmark_atlas.py`](../scripts/benchmark_atlas.py), and the
experiment rules are documented in
[`benchmarks/README.md`](../benchmarks/README.md).

Recalculate the model from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/benchmark_atlas.py model \
  --input benchmarks/data/modelled/v0.1.0.json \
  --check benchmarks/data/derived/modelled-v0.1.0.json
```

The model inputs were frozen on `2026-07-26`. They are not Codex, Claude Code,
or other AI-tool telemetry, and they are not a percentage of a weekly limit.

## How much confidence to place in these numbers

The Project Atlas benchmark separates three classes of claims:
`MODELLED_ASSUMPTION`, `MEASURED`, and `EXTERNAL_EVIDENCE`.

### `MODELLED_ASSUMPTION` — calculation based on assumptions

All three scenarios, percentages, and break-even points on this page belong to
this class. The formula is exact, but the input values are assumed for now. The
model answers: **"What happens if the real costs look like this?"**

### Reproducibility is a property of the calculation

`REPRODUCIBLE` is not a fourth claim class here. It is a property of the open
calculation: the input JSON, formula code, and derived JSON are all in the
repository. Anyone can repeat the calculation and check the arithmetic. This
proves that the numbers are not hidden and are calculated consistently. It
does not prove that a scenario will match your project.

### `MEASURED` — measured in real paired runs

Project Atlas currently has **0** public paired campaigns.

A real measurement requires giving the same task to two fresh sessions: one
without Atlas and one with Atlas. They need the same project version, model,
settings, permissions, and definition of done. Provider-reported tokens, time,
and result quality are then compared. Until public receipts from such campaigns
exist, these percentages cannot be called actual user savings.

### `EXTERNAL_EVIDENCE` — research on other systems

These studies show that the context problem is real and that context,
retrieval, and planning can affect results. **They are not Project Atlas
results, and their percentages cannot be transferred to Atlas.**

- In a 2005 Microsoft study, developers identified understanding the rationale
  behind code (`66%`), frequent task switching (`62%`), and awareness of
  affecting changes elsewhere (`61%`) as leading problems:
  [Software Development at Microsoft Observed](https://www.microsoft.com/en-us/research/publication/software-development-at-microsoft-observed/).
- RepoCoder's iterative repository-context retrieval improved the baseline
  in-file completion score by more than `10%` in every studied setting:
  [RepoCoder](https://arxiv.org/abs/2303.12570).
- Repoformer reported up to a `70%` inference speedup in its online-serving
  setting through selective retrieval without reducing performance:
  [Repoformer](https://arxiv.org/abs/2403.10059).
- CodePlan passed tests in `5 of 7` repositories, while baselines without
  planning and with a comparable context type passed in `0 of 7`:
  [CodePlan](https://www.microsoft.com/en-us/research/publication/codeplan-repository-level-coding-using-llms-and-planning-2/).
- The counterexample also matters: in a randomized METR study, `16` experienced
  open-source developers completed `246` tasks and took `19%` more time on
  average with AI:
  [METR, Early-2025 AI and experienced OSS developers](https://metr.org/Early_2025_AI_Experienced_OS_Devs_Study-paper.pdf).

The last study explains why Atlas models more than an attractive scenario. AI
by itself does not guarantee a speedup. Value has to be tested on matching
tasks, including the cost of preparation, waiting, and verification.

## An honest bottom line

- **Illustrative model forecast:** over 10 tasks, `700,000 → 570,000` tokens
  (`−18.6%`), with token break-even at task 6.
- **Best open scenario:** `900,000 → 440,000` tokens (`−51.1%`).
- **Bad scenario:** `600,000 → 840,000` tokens (`+40.0%`), so Atlas is
  uneconomical for a short series of tasks like these.
- **Actual user result:** not yet measured in a public paired campaign.

Atlas is worth using not because a large percentage is printed next to it, but
when the task series is long enough for project knowledge built once to be
reused.
