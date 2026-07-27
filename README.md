# Project Atlas

<p align="right">
  <strong>English</strong> · <a href="./README.ru.md">Русский</a>
</p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Project Atlas turns current code into evidence, a ready task, and a verified new current state">
</p>

<p align="center">
  <strong>A verifiable software-product map for coding agents.</strong><br>
  Codex is the primary adapter (host integration). Claude Code is the first additional adapter.<br>
  The protocol — the shared rules for building and checking the map — does not depend on one AI tool.
</p>

<p align="center">
  <a href="#30-seconds">30 seconds</a>
  ·
  <a href="#five-minutes-to-your-first-run">Five-minute start</a>
  ·
  <a href="#end-to-end-map--task--change">End-to-end example</a>
  ·
  <a href="#what-to-do-with-the-map">What happens next</a>
  ·
  <a href="#technical-deep-dive">Technical deep dive</a>
</p>

## 30 seconds

Project Atlas helps a coding agent understand an unfamiliar or forgotten
project without turning confident guesses into facts.

Put simply: it shows the agent what is where, how the parts connect, what is
known, and where it is safe to start.

It finds runtime roots, data, authority, background work, external boundaries,
tests, and risks. Every material claim points to source evidence and carries
one label: `CONFIRMED`, `INFERENCE`, `HYPOTHESIS`, `TARGET`, or `UNKNOWN`.

The result is not another document for its own sake. The map becomes a
verifiable starting point for the next piece of work:

```text
CURRENT map → TARGET task → source recheck → change
→ tests → refreshed CURRENT map
```

| Without a map | With Atlas |
| --- | --- |
| A new session rediscovers the project | It starts from named files, relationships, and risks |
| A guess can look like a fact | Facts, inferences, targets, and unknowns stay separate |
| “Add a feature” has no boundary | A task carries an outcome, scope, non-goals, and acceptance |
| Old context disappears after a change | The map is refreshed and checked again |

Atlas **does not change product code while mapping** and does not start future
tasks on its own. The user chooses a task, and the next agent rechecks the
sources before implementation.

> [!IMPORTANT]
> Project Atlas is a community project, not an official OpenAI or Anthropic
> product. Project content is processed under the rules of the selected
> service. A structurally valid map can still contain a wrong conclusion, so
> a person must review consequential decisions.

### What is proved and what is not

| Status | Honest answer |
| --- | --- |
| **Reproducible** | The validator checks map structure, allowed references, and replay commands. On three synthetic projects, depth selection matched the predeclared expected answer in `3/3` cases, and all `26/26` predefined correspondences were found. |
| **Modelled** | An open model ranges from **+40.0% token cost** to **−51.1% tokens** over ten follow-up tasks. These are scenarios, not observed user results. |
| **Not measured yet** | Real token and time savings, context-selection precision, and the success rate of follow-up tasks. |

The first public proof of the complete work loop is the
[end-to-end case study](./docs/case-study.md).

## Five minutes to your first run

The five-minute start begins after the required tools are installed and Codex
is signed in. It is not a promise that the completed map will arrive in exactly
five minutes. Investigation time depends on the project, depth, model, and
permissions.

> [!NOTE]
> On Windows, open WSL first and run this entire section inside it. The commands
> below target a macOS terminal or WSL; native PowerShell is not a verified
> v0.1.1 path.

### 1. Check the tools

Open a terminal:

```bash
codex --version
git --version
python3 --version
rg --version
```

You need Codex CLI, Git, Python 3.10+, and ripgrep (`rg`). All four commands
must print a version.

If a command is missing, use the official setup pages for
[Codex CLI](https://developers.openai.com/codex/cli),
[Git](https://git-scm.com/downloads/),
[Python](https://www.python.org/downloads/), or
[ripgrep](https://github.com/BurntSushi/ripgrep#installation).

Check that Codex is signed in:

```bash
codex login status
```

If it is not, run `codex login` and complete the browser flow.

### 2. Copy the safe demo project

```bash
git clone https://github.com/ddenny-s/project-atlas.git atlas-first-map
cp -R atlas-first-map/tests/fixtures/quick_cli atlas-quick-demo
cd atlas-quick-demo
```

`atlas-quick-demo` is a small program with no network, database, or production
data. Running `ls` in this folder should show `README.md` and `quick_cli`.

### 3. Install Atlas for Codex

```bash
codex plugin marketplace add ddenny-s/project-atlas
codex plugin add project-atlas@project-atlas
codex plugin list
```

The list should contain `project-atlas`.

### 4. Start Codex in that folder

```bash
codex
```

Send:

```text
Use $project-atlas:map-project.
Treat the current folder (`.`) as the project root and do not move above it.
Create and validate a QUICK map with `--project .`.
Do not change project code.
```

Atlas asks its initial product questions, shows a token forecast, inspects the
allowed files, presents a candidate map, and asks its final questions.

### 5. Check the result

The folder will contain:

```text
PROJECT_ATLAS.md
```

A successful structural check ends with a result shaped like this:

```json
{"artifacts": 1, "mode": "QUICK", "status": "valid", "validation": "completion"}
```

`valid` means the required sections and references passed automated checks. It
does not mean every sentence written by the model automatically became true.

### 6. Repeat this on your project

Exit the demo Codex session, open a terminal in the real project folder, and
start `codex` again:

```bash
cd "/replace/with/the/full/path/to/your/project"
codex
```

Send:

```text
Use $project-atlas:map-project.
Treat the current folder (`.`) as the project root and do not move above it.
First recommend QUICK, STANDARD, or FORENSIC, show the token forecast,
and wait for my confirmation. Then create and validate the map.
Do not change project code.
```

<details>
<summary><strong>Short glossary before you start</strong></summary>

- **Repository** — the project folder plus its change history.
- **Project root** — the top folder of the exact code you want mapped.
- **Plugin** — an installable extension for Codex or Claude Code.
- **QUICK** — the short Atlas depth for a small project or first orientation.
- **Token** — a small unit of text used to count model input and output; it is not a subscription percentage.
- **Future task** — a work card Atlas prepared but did not implement.
- **Worker** — code that does background work instead of answering a person directly.
- **Fixture** — a small test project or prepared test data.
- **Oracle** — the expected correct answer used to check a result.
- **Task Context Packet** — the small slice of the map needed for one task. By
  default, the agent shows it in chat; ask separately if you want a saved
  `.md` file.
- **Non-goals** — work that is deliberately outside that task.

</details>

## End-to-end: map → task → change

<p align="center">
  <img src="./assets/readme/workflow.svg" width="100%" alt="The Atlas loop: current state, evidence-backed finding, ready task, change, verification, and refreshed map">
</p>

A public demo service accepts parcels through an API and processes delivery in
a background worker. Both paths write to one SQLite table.

Before the map:

- the API rejects a blank `parcel_id`;
- the worker bypasses that check;
- the shared writer stores spaces as a real identifier.

Reproducible result:

```text
api blank: rejected (parcel_id is required)
worker blank: ('   ', 'delivered', 'worker')
```

The map separates three statements:

```text
CURRENT · CONFIRMED — the API validates parcel_id.
CURRENT · CONFIRMED — the worker reaches the shared writer without that check.
CURRENT · UNKNOWN   — provider ordering after a timeout is unproved.
```

It then creates `ATLAS-001 · TARGET · READY`: move the invariant to the shared
layer, leave retries and administrator authority alone, and check both blank
and valid identifiers.

A public test applies the minimal patch only to a temporary copy of the fixture
and proves:

```text
api blank: rejected (parcel_id is required)
worker blank: rejected (parcel_id is required)
valid: [('parcel-7', 'delivered', 'worker'),
        ('parcel-api', 'accepted', 'api')]
```

The task receipt becomes `VERIFIED`; its future-task row becomes `SUPERSEDED`.
Provider ordering remains `UNKNOWN`, so the map does not close a gap that the
change did not investigate.

[Open the complete example: sources → map → Context Packet → patch → test → refreshed map](./docs/case-study.md)

[Frozen map before](./docs/case-study-artifacts/standard-service/before/PROJECT_ATLAS.md)
· [Task Context Packet](./docs/case-study-artifacts/standard-service/ATLAS-001-context-packet.md)
· [Exact patch](./docs/case-study-artifacts/standard-service/ATLAS-001.patch)
· [Frozen map after](./docs/case-study-artifacts/standard-service/after/PROJECT_ATLAS.md)

## What to do with the map

### Choose one piece of work

In QUICK, open Future Tasks in `PROJECT_ATLAS.md`. In STANDARD or FORENSIC,
start with `ATLAS_INDEX.md`, `LIVE_HANDOFF.md`, and Future Tasks in
`MIGRATION_PLAN.md`.

Before editing, the agent builds a small **Task Context Packet**:

1. the selected task and acceptance criteria;
2. relevant `CURRENT` claims;
3. exact source files;
4. material `UNKNOWN` items and authority boundaries;
5. required checks;
6. the freshness result for every source link;
7. explicitly excluded context.

This keeps a large map from becoming a huge prompt. The next session receives
only the context needed for the selected task and can see what was left out.

By default, “show a Task Context Packet” means print it in chat, not create a
new file. To carry it into another session, add: “and save it as
`TASK_CONTEXT_<Task-ID>.md`.” The end-to-end case deliberately
[saves one as a separate artifact](./docs/case-study-artifacts/standard-service/ATLAS-001-context-packet.md)
so anyone can inspect it.

### Send one plain request

```text
Open the Atlas map and select task <Task ID>.
First check that its source links are still current.
Show a short Task Context Packet: task, related facts, files, unknowns,
checks, and exclusions.
Then implement only that task, run the checks, and refresh the map.
Do not close an UNKNOWN without new evidence.
```

### Where this helps

| Follow-up work | How the map helps |
| --- | --- |
| Fix a bug | Starts from the likely failure path, state, and related tests |
| Add a feature | Separates affected layers from explicit non-goals |
| Run a risky refactor | Preserves current relationships, authority, and rollback paths |
| Hand off a project | Gives source-backed navigation instead of an old chat summary |
| Start a new AI session | Reduces repeated orientation when the map is fresh |

A stale or shallow map can make work worse. That is why links are rechecked
before the change and the map is refreshed afterwards.

## What Atlas does internally

1. **BOUND** — define the project root, exclusions, product purpose, and cost of error.
2. **FORECAST** — show minimum, typical, and maximum model-token estimates before each deep block.
3. **DISCOVER** — find runtime roots, flows, state, authority, tests, and external boundaries.
4. **CLASSIFY** — separate facts, inferences, hypotheses, targets, and unknowns.
5. **ALIGN** — show the candidate map to the owner and ask only questions that can still change the map or backlog.
6. **DELIVER** — create the map, Future Tasks, handoff, and structural validation result.

There is no fixed total question count. Atlas asks small batches while another
answer can still change scope, risk, product direction, or a future task. Every
question shows four visible choices; the fourth is “Other — I will write it.”

Answer states remain distinct:

- `UNAVAILABLE` — the user does not know;
- `SKIPPED` — the user deliberately skipped;
- `STOP_USER` — the user stopped the survey;
- `UNKNOWN:<stable-id>` — a material gap remains open.

Owner answers have `USER_INPUT` provenance. They can confirm intent, but cannot
turn a technical guess about current code into `CONFIRMED`.

## Three depth levels

| Depth | Use it for | Result |
| --- | --- | --- |
| `QUICK` | A small project or first orientation | One `PROJECT_ATLAS.md` |
| `STANDARD` | A live application, service, or library | A routed current-state and target-state atlas |
| `FORENSIC` | A critical, old, confusing, or sensitive system | Complete registries, coverage denominators, source snapshot, and independent challenge |

Repository size does not select depth by itself. Cost of error, live data,
runtime count, state stores, automated decisions, and authority matter more.

[Exact selection rules](./docs/depth-levels.md) ·
[Output contract](./docs/outputs.md)

## Surveys, tokens, and model choice

Before every material block, Atlas shows:

| Estimate | Meaning |
| --- | --- |
| Minimum | Lower working bound when few unknowns are present |
| Typical | Main planning point, not an average across all users |
| Maximum | Checkpoint and reforecast boundary |

For the public `quick_cli` fixture, the current built-in model gives
**2,500 / 3,600 / 5,500 model tokens** for the complete QUICK loop. This is a
forecast based on the allowed-file inventory, not a usage promise.

If the host exposes an exact weekly remainder, Atlas may repeat it. If no exact
signal exists, no weekly line appears. Atlas never converts tokens into an
invented subscription-limit percentage.

### Starting model and effort

Checked against official documentation on **2026-07-27**. This is a starting
point, not an Atlas benchmark:

| Depth | Codex — primary adapter | Claude Code — additional adapter |
| --- | --- | --- |
| `QUICK` | GPT-5.6 Terra, `medium` | `sonnet`, `high` |
| `STANDARD` | GPT-5.6 Sol, `high` | `opus`, `high` |
| `FORENSIC` | GPT-5.6 Sol, `xhigh` | `best`, `xhigh` |

OpenAI describes GPT-5.6 Sol as the frontier-capability choice, Terra as the
quality-and-cost balance, and `medium` as a balanced starting point. Raise to
`high`, `xhigh`, or `max` only when evaluation shows a benefit.

In Claude Code, `best` uses Fable 5 when the organization has access and
otherwise selects the latest Opus. The `opus` alias depends on provider. It
currently selects Opus 5 on the Anthropic API and Claude Platform on AWS; Opus
5 requires Claude Code 2.1.219+. Claude Code 2.1.207 selected Opus 4.8, so Atlas
records the alias, provider, effective model, and Claude Code version.

`ultracode` is a session-only Claude Code setting, not an Atlas depth. It
combines `xhigh` with dynamic workflows. The `--effort ultracode` form requires
Claude Code 2.1.203+. Use it only for a hard bounded block inside an explicitly
accepted budget. `ultrathink` deepens one turn without changing API effort.
Codex remains the primary adapter.

Sources:
[OpenAI — Using GPT-5.6](https://developers.openai.com/api/docs/guides/latest-model),
[Claude Code — model configuration](https://code.claude.com/docs/en/model-config),
[Anthropic — model and effort](https://claude.com/blog/claude-model-and-effort-level-in-claude-code),
[Anthropic — dynamic workflows](https://claude.com/blog/introducing-dynamic-workflows-in-claude-code).

## Effectiveness without marketing math

Atlas does not yet have a published paired campaign on real downstream tasks.
No modelled percentage below is presented as an observed result.

### Open model for ten follow-up tasks

| Scenario | Tokens | Time | Acceptance pass | Break-even |
| --- | ---: | ---: | ---: | ---: |
| Pessimistic · `MODELLED_ASSUMPTION` | cost **+40.0%** | cost **+88.0%** | `70% → 68%` | 50 tasks by tokens, 120 by time |
| Illustrative middle · `MODELLED_ASSUMPTION` | saving **18.6%** | saving **6.7%** | `70% → 76%` | 6 tasks by tokens, 9 by time |
| Optimistic · `MODELLED_ASSUMPTION` | saving **51.1%** | saving **51.2%** | `70% → 82%` | 3 tasks by tokens, 3 by time |

Inputs are in
[`benchmarks/data/modelled/v0.1.0.json`](./benchmarks/data/modelled/v0.1.0.json);
formula output is in
[`modelled-v0.1.0.json`](./benchmarks/data/derived/modelled-v0.1.0.json).
Anyone can replace the assumptions with their own.

Break-even exists only when per-task savings are positive. At zero or negative
savings, the calculator returns `null`: break-even is not reached in that
model. With a zero baseline, relative improvement also returns `null`, not
infinity or an invented percentage.

Related research shows that preselected useful context can sometimes reduce
tokens and improve outcomes, while a repository graph can improve outcomes
with **higher** token cost. That is `EXTERNAL_EVIDENCE`, not an Atlas result.
Sources and arithmetic:
[`benchmarks/EXTERNAL_EVIDENCE.md`](./benchmarks/EXTERNAL_EVIDENCE.md).

## Benefits, costs, and boundaries

| Benefits | Costs and risks |
| --- | --- |
| Source-backed navigation instead of confident summaries | Initial mapping consumes tokens and time |
| Ready tasks with scope and acceptance | A stale or wrong map can misdirect later work |
| Portable context between agents and sessions | Dynamic code and external systems can hide relationships |
| Explicit UNKNOWN items instead of filled gaps | The validator checks structure, not absolute truth |

Atlas is most useful before a refactor, migration, handoff, authority/data
audit, or a series of future changes. A small clear script with a good README
often does not need it.

Safety rules:

- do not read or publish secrets, keys, or real production exports;
- mapping permission is not permission to change code, data, infrastructure, or production;
- expand reading only after a bounded safe inventory;
- independently challenge high-impact claims;
- refresh the map after material changes.

[SECURITY.md](./SECURITY.md) ·
[Evidence model](./core/skill/map-project/references/evidence-model.md)

## Technical deep dive

The normative contract is not duplicated in this README. It lives under
`core/`; these documents explain and index it:

- [English technical index](./docs/README.md)
- [Protocol](./core/PROTOCOL.md)
- [Methodology and evidence](./docs/methodology.md)
- [Depth levels](./docs/depth-levels.md)
- [Output contract](./docs/outputs.md)
- [Usage examples](./docs/examples.md)
- [Adapters, models, and installation](./docs/adapters.md)
- [End-to-end case study](./docs/case-study.md)

The canonical protocol stays host-independent:

```text
core/                 evidence, workflow, safety, and outputs
adapters/codex/       primary native package
adapters/claude-code/ first additional package
```

<details>
<summary><strong>Claude Code, direct install, update, and removal</strong></summary>

Claude Code:

```bash
claude plugin marketplace add ddenny-s/project-atlas
claude plugin install project-atlas@project-atlas
```

Invoke with `/project-atlas:map-project`.

Direct install on macOS or Linux:

```bash
git clone https://github.com/ddenny-s/project-atlas.git
cd project-atlas
./scripts/install.sh --user-scope
# or:
./scripts/install-claude.sh
```

Direct invocation: `$map-project` for Codex, `/map-project` for Claude Code.

Update a direct install:

```bash
git pull --ff-only
./scripts/install.sh --user-scope --force
# or:
./scripts/install-claude.sh --force
```

Plugins use the host's normal removal commands. Direct installation in v0.1.1
does not have a separately verified removal or manual-backup recovery command.
Do not improvise with recursive `mv`, `cp`, or `rm`; use the plugin route when
you need a simple lifecycle.

</details>

## Development and release

```bash
python3 scripts/sync_adapters.py --check
python3 -m unittest discover -s tests -v
git diff --check
```

Before a tag or GitHub Release, maintainers separately test disposable clean
profiles, paths with spaces, both adapters, every depth result, and independent
reviews. This manual gate is required before creating a version tag or GitHub
Release and is not a GitHub Actions check. Green CI is necessary, but not
sufficient for release.

[CONTRIBUTING.md](./CONTRIBUTING.md) · [MIT License](./LICENSE)
