# Live Handoff

## Scope and Mode

Mode is FORENSIC. Record exact included and excluded contours and their denominators.

## Completed

No contour has been marked complete yet.

## Evidence Freshness

No source observation or deterministic source snapshot has been recorded yet.

## Independent Reviews

Completion requires one canonical-review-input-bound correctness review and one canonical-review-input-bound security review. Each must record `PASS`, zero Critical, zero Important, a retained evidence summary, and remaining proof limits.

| ID | Review kind | Reviewer ref | Independence | Reviewed snapshot | Verdict | Critical | Important | Retained evidence summary | Remaining limits | Reviewed at | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Remaining Work and Blockers

Route every unresolved material boundary through `OPEN_UNKNOWNS.md` and `TRACEABILITY.tsv`.

## Continue From Here

Select the earliest unresolved high-consequence contour, verify its primary sources, update traceability, and refresh this handoff before switching contours.

## Reproducible Commands

Run from the project root. Keep this validator-owned fence exactly as initialized. For an explicit custom output, export `PROJECT_ATLAS_ROOT` to that directory before running the unchanged fence and record the routing above.

```sh
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P)"
if [ -n "${PROJECT_ATLAS_ROOT:-}" ]; then atlas_root="$PROJECT_ATLAS_ROOT"; elif [ -d "$project_root/docs/project-atlas" ]; then atlas_root="$project_root/docs/project-atlas"; else atlas_root="$project_root/project-atlas"; fi
# Project Atlas helper resolution v1
atlas_script="${PROJECT_ATLAS_SCRIPT:-}"
if [ -z "$atlas_script" ]; then
  atlas_default_roots="${PROJECT_ATLAS_DEFAULT_SEARCH_ROOTS:-}"
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
PYTHONDONTWRITEBYTECODE=1 python3 "$atlas_script" snapshot --atlas "$atlas_root" --project "$project_root" --output "$atlas_root/SOURCE_SNAPSHOT.json"
PYTHONDONTWRITEBYTECODE=1 python3 "$atlas_script" validate --atlas "$atlas_root" --project "$project_root" --mode FORENSIC --replay-command-evidence
```
