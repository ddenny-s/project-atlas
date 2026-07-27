# Live Handoff

## Scope and Mode

Mode is STANDARD. Record included and excluded contours before investigation continues.

## Completed

No contour has been marked complete yet.

## Evidence Freshness

No source observation or snapshot has been recorded yet.

## Finish Alignment

Complete against the candidate map and backlog. If an answer changes either, update them, supersede affected records, and repeat this section before completion.

### Question table

| Question ID | Batch ID | Topic | Question | Option A | Option B | Option C | Option D | Selected | Free-form note | Answer state | Map effect | Provenance | Answered at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Batch ledger

| Batch ID | Sequence | Question IDs | Remaining material gaps | Decision | Decision provenance | Status |
| --- | --- | --- | --- | --- | --- | --- |

## Run Economics

Record PRE before every deep-work block and POST after it. PRE uses integer model-token ranges; POST uses exact host telemetry or `UNMEASURED`.

| Run ID | Block ID | Entry | Block | Unit | Min | Typical | Max | Basis | Model tier and effort | Input | Output | Reasoning | Total | Telemetry | Variance vs typical | Recorded at | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Remaining Work and Blockers

Route unresolved evidence through `OPEN_UNKNOWNS.md`.

## Continue From Here

Run the safe structural inventory, select one bounded runtime contour, and update this handoff before switching contours.

## Reproducible Commands

Run from the project root. Keep this validator-owned fence exactly as initialized. For an explicit custom output, export `PROJECT_ATLAS_ROOT` to that directory before running the unchanged fence and record the routing above.

```sh
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P)"
if [ -n "${PROJECT_ATLAS_ROOT:-}" ]; then atlas_root="$PROJECT_ATLAS_ROOT"; elif [ -d "$project_root/docs/project-atlas" ]; then atlas_root="$project_root/docs/project-atlas"; else atlas_root="$project_root/project-atlas"; fi
# Project Atlas helper resolution v1
atlas_script="${PROJECT_ATLAS_SCRIPT:-}"
if [ -z "$atlas_script" ]; then
  atlas_default_roots="${PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS:-$HOME/.agents/skills:${CODEX_HOME:-$HOME/.codex}/skills:${CODEX_HOME:-$HOME/.codex}/plugins/cache:${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills:${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache}"
  atlas_search_roots="${PROJECT_ATLAS_SEARCH_ROOTS:-$atlas_default_roots}"
  atlas_candidates="$(
    printf '%s\n' "$atlas_search_roots" |
      tr ':' '\n' |
      while IFS= read -r atlas_search_root; do
        if [ -d "$atlas_search_root" ]; then
          find "$atlas_search_root" -type f -path '*/map-project/scripts/atlas.py' -print 2>/dev/null
        fi
      done |
      LC_ALL=C sort -u
  )"
  atlas_candidate_count="$(printf '%s\n' "$atlas_candidates" | awk 'NF { count++ } END { print count + 0 }')"
  test "$atlas_candidate_count" -eq 1
  atlas_script="$atlas_candidates"
fi
test -f "$atlas_script"
PYTHONDONTWRITEBYTECODE=1 python3 "$atlas_script" validate --atlas "$atlas_root" --project "$project_root" --mode STANDARD
```
