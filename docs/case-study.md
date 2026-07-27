# End-to-end example: from map to verified change

[English](./case-study.md) · [Русский](./case-study.ru.md)

This small, fully public example shows the complete Atlas loop:

```text
CURRENT → map → finding → TARGET task → change → verification → new CURRENT
```

It uses the synthetic parcel service in
[`tests/fixtures/standard_service`](../tests/fixtures/standard_service). The
fixture contains no production data, credentials, or network dependency.

## 1. Before the map

The service has two entry paths:

- the API accepts a parcel and rejects a blank `parcel_id` in
  [`api.py`](../tests/fixtures/standard_service/service/api.py#L8-L12);
- a background worker records delivery results through
  [`worker.py`](../tests/fixtures/standard_service/service/worker.py#L25-L41);
- both paths use the shared SQLite writer in
  [`state.py`](../tests/fixtures/standard_service/service/state.py#L8-L21).

The problem is easy to miss when reading only the API. The worker does not
repeat the check and sends a blank identifier to the shared store.

Run this from the Project Atlas repository root:

```bash
PYTHONPATH=tests/fixtures/standard_service \
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import sqlite3
import tempfile
from pathlib import Path

from service.api import accept_parcel
from service.worker import process_delivery

database = Path(tempfile.mkdtemp()) / "probe.sqlite"
try:
    accept_parcel(database, "   ")
except ValueError as error:
    print(f"api blank: rejected ({error})")
else:
    raise AssertionError("API accepted a blank parcel_id")

process_delivery(database, "   ", lambda: "delivered")
row = sqlite3.connect(database).execute(
    "SELECT parcel_id, status, writer FROM parcel_state"
).fetchone()
print(f"worker blank: {row}")
PY
```

Observed result:

```text
api blank: rejected (parcel_id is required)
worker blank: ('   ', 'delivered', 'worker')
```

## 2. What the map records

The complete frozen
[`before/PROJECT_ATLAS.md`](./case-study-artifacts/standard-service/before/PROJECT_ATLAS.md)
passes Atlas QUICK completion validation. Its central claims are:

```text
CURRENT · CONFIRMED · CLAIM-API-001
The API rejects a blank parcel_id.
Source: service/api.py:8-12

CURRENT · CONFIRMED · CLAIM-WORKER-002
The worker reaches the shared record_status without that check.
Sources: service/worker.py:25-41, service/state.py:8-21

CURRENT · UNKNOWN · UNKNOWN-PROVIDER-ORDERING
Provider event ordering after a timeout is not established.
Source: README.md:8-10
```

The map does not claim that the whole service is broken. It records one
evidence-backed inconsistency and keeps the separate provider unknown visible.

## 3. The finding becomes a ready task

The before-map turns that inconsistency into this bounded backlog item:

```text
ATLAS-001 · TARGET · READY

Outcome:
  Every write path rejects a blank parcel_id.

Why:
  The API validates the identifier; the worker does not.

Scope:
  The shared service/state.py writer and checks for both entry paths.

Non-goals:
  Provider retries, administrator authority, and status design.

Acceptance:
  1. A blank parcel_id is rejected from both API and worker paths.
  2. A valid delivery is still persisted.
  3. UNKNOWN-PROVIDER-ORDERING remains UNKNOWN.
```

This card is why the map remains useful after it is created. The next session
gets a reason, boundary, acceptance criteria, and source links instead of
starting the investigation again.

Before editing, the agent builds a bounded
[`Task Context Packet`](./case-study-artifacts/standard-service/ATLAS-001-context-packet.md):

```text
Task: ATLAS-001
CURRENT claims: CLAIM-API-001, CLAIM-WORKER-002
Sources: service/api.py, service/worker.py, service/state.py
Authority boundary: administrator override is unchanged
Related unknown: UNKNOWN-PROVIDER-ORDERING
Required checks: blank rejected; valid worker write preserved
Freshness: all three sources reread at the current snapshot
Excluded: provider retries, status design, deployment
```

The packet names excluded as well as included context, so selection from a
large map can be reproduced and challenged.

## 4. Minimal change at the owning layer

Move the invariant to the shared writer used by both paths:

The test applies the exact versioned
[`ATLAS-001.patch`](./case-study-artifacts/standard-service/ATLAS-001.patch)
published with this case study:

```diff
 def record_status(database: Path, parcel_id: str, status: str, *, writer: str) -> None:
     """Persistent state writer shared by the API and worker runtimes."""
+    if not parcel_id.strip():
+        raise ValueError("parcel_id is required")
     database.parent.mkdir(parents=True, exist_ok=True)
```

This is an illustrative patch. The source fixture deliberately remains in its
**before** state so anyone can reproduce the finding.

## 5. Revalidation

The public test copies the fixture to a temporary directory, confirms the
before state, applies the documented change only to the temporary copy, and
then checks both entry paths and their valid writes:

- a blank identifier is rejected by the API and worker;
- a valid API parcel is stored as `accepted`;
- a valid worker parcel is stored as `delivered`.

Observed post-patch output:

```text
api blank: rejected (parcel_id is required)
worker blank: rejected (parcel_id is required)
valid: [('parcel-7', 'delivered', 'worker'),
        ('parcel-api', 'accepted', 'api')]
```

Run:

```bash
python3 -m unittest tests.test_documentation_case_study -v
```

Verification source:
[`tests/test_documentation_case_study.py`](../tests/test_documentation_case_study.py).

## 6. Map after the change

After the check passes, the
[`after/PROJECT_ATLAS.md`](./case-study-artifacts/standard-service/after/PROJECT_ATLAS.md)
is refreshed and passes the same QUICK completion validator against the
temporary patched copy:

```text
CURRENT · CONFIRMED · CLAIM-STATE-003
Shared record_status rejects a blank parcel_id for API and worker paths.
Evidence: tests/test_documentation_case_study.py

Task receipt: ATLAS-001 · VERIFIED
Future-task lineage: ATLAS-001 · SUPERSEDED

CURRENT · UNKNOWN · UNKNOWN-PROVIDER-ORDERING
Unchanged: provider event ordering after a timeout is still unproved.

ATLAS-002 · TARGET · BLOCKED
Establish provider ordering before proposing a retry change.
```

`VERIFIED` is recorded as a task receipt. The canonical future-task row becomes
`SUPERSEDED`, while the unrelated unknown remains open and blocks `ATLAS-002`.
This prevents a successful identifier fix from pretending to answer a provider
question that it never tested.

Run the frozen lineage check directly:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_documentation_case_study.DocumentationCaseStudyTests.test_frozen_atlas_lineage \
  -v
```

Atlas preserves a chain, not just a document:

```text
claim → source → task → change → verification → updated claim
```

This example proves that workflow on one synthetic project. It does not prove
an Atlas efficiency percentage for real repositories, and it does not replace
human review of a specific project.
