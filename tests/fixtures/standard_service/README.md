# Parcel Status Service

This medium-lived internal service has two runtime roots: a request handler and
a background delivery worker. Both share a SQLite state store. The request
handler records new parcels; the worker retries provider delivery and records
the resulting state transition.

An administrator's explicit status override outranks automatic provider state.
The provider's ordering guarantee after a retry is currently UNKNOWN. The
service is exercised locally and does not contain production credentials.
