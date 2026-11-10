# LRCLIB read-only API compatibility (Home LrcAPI 2.0)

Existing clients use the same `Authorization` or `Authentication` header
and existing domains as before. This is **not** a public LRCLIB mirror and
does not support publishing, challenges, flagging or arbitrary file writes.

## Supported endpoints

- `GET /api/search` — search by `q` or track metadata; missing query returns `[]`.
- `GET /api/get` — `track_name` and `artist_name` required; `album_name` and
  `duration` optional, to match current LRCLIB v1 semantics.
- `GET /api/get/<id>` — fetch an existing LRCLIB record by ID.
- `GET /api/get-cached` — returns only previously cached results from **this
  instance**; unlike full LRCLIB, it does not query a replicated remote DB.
- `GET /api/v2/search` — read-only beta proxy; may change with upstream.

There is one shared LRCLIB client for these routes and the legacy /lyrics
fallback. It centralizes 429/503 cooldowns, Retry-After, 256-result bounded
in-memory cache, upstream timeouts, User-Agent and response size limits.

Clients that cannot set the household's authorization header will still
receive HTTP 401. Never remove authentication to make such clients work over
a public endpoint; use a trusted internal proxy if needed.

## Song source priority

1. Explicit manual correction from Mac-local SQLite.
2. Existing same-name `.lrc` file (**accepted as correct by default**).
3. Audio-embedded lyrics if there is no `.lrc`.
4. Online providers after local lookup misses.

Only the correction UI or owner-only CLI writes SQLite. The normal lyrics API
does not mutate the music directory. Navidrome still reads the physical
`.lrc` files, so an optional explicit sidecar export is available through
the local-only editor. An APFS backup is taken first and the export can be
reversed, subject to checksum verification.

To run tests and dependency audit:

```sh
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check app.py api mod admin tests --select E9,F63,F7,F82
uv run --locked pip-audit --path .venv/lib/python3.12/site-packages
```
