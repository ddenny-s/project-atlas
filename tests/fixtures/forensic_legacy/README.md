# Legacy Settlement Mesh

This production-critical legacy fixture coordinates synthetic financial
settlements across an HTTP gateway, webhook receiver, queue worker, and cron
reconciler. Multiple writers share one ledger, an old migration path is still
present, and external provider retries can arrive out of order.

Manual compliance decisions have final authority over automatic settlement
state. The owner of the deprecated migration writer is UNKNOWN. Recovery after
a partial provider acknowledgement is also UNKNOWN and must be traced before
any rewrite. All credentials in ignored fixture paths are inert canary text.
