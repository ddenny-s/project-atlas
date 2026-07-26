# Pocket Counter

Pocket Counter is a small, single-process command-line utility. It accepts an
integer, retries one transient calculation, and writes one local JSON state
file. It has no network service, database, worker, queue, or production data.

The runtime entry point is `python3 -m quick_cli`. An explicit operator
`--force` flag has the last word when an existing state file would be replaced.

Known unknown: atomic replacement has not been observed on a full filesystem.
