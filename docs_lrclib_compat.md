# LRCLIB read-only protocol adapter (staged, not deployed)

Routes: `GET /api/get`, `GET /api/get-cached`, `GET /api/get/<id>`,
and `GET /api/search`. Responses preserve LRCLIB's JSON schema and IDs.
The official LRCLIB API is the source; this is a bounded, authenticated
proxy with a 256-record in-memory LRU cache, request timeouts, and 429/503
Retry-After cooldown, not a self-hosted LRCLIB database.

**Authentication:** The existing LrcAPI API_AUTH/Authorization policy
applies. LRCLIB clients unable to send Authorization headers will receive
403 (or 401 depending on the auth configuration). Do not turn off
authentication while port 28883 is bound on all interfaces.

Not supported: /api/publish, /api/request-challenge, /api/flag, v2 API,
offline lookups after restart, persistent cache, or fallback matching.
These must not be advertised as fully protocol compatible.

Production remains on tooandy/lrcapi 1.5.6 until integration/staging,
music read-only access, performance and authentication are validated.
