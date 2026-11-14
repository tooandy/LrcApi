# Home LrcAPI — household read-only lyrics gateway

This private fork runs natively on the Mac mini with Python **3.12** and
**uv**. The old upstream has been discontinued and the external lyrics
providers are unreliable. Keep the architecture as simple as possible.

## Architecture

```text
Clients → Debian Nginx HTTPS :7193 / legacy HTTP :28883
             ↳ Mac mini :28883 [read-only lyrics API]
                      ├─ Mac APFS SQLite: manual corrections only
                      ├─ NAS NFS: existing .lrc / embedded lyrics
                      └─ NetEase, Kugou, LRCLIB, QQ Music (bounded providers)

Mac mini 127.0.0.1:28886 + 192.168.3.7:28886 [LAN-restricted editor;
                                                  NOT reverse proxied]
                      ├─ browse folders on demand, never crawl entire NAS
                      ├─ inspect, edit, save and undo SQLite corrections
                      └─ optional one-track .lrc export with Mac backup
```

**No need to verify every local lyric.** The default is to trust existing
`.lrc` and embedded lyrics. Only the rare incorrect track goes into the
SQLite correction database.

## Storage rules

- SQLite correction history:
  `~/Library/Application Support/HomeServer/LrcAPI/overrides.sqlite3` on
  **Mac local APFS**, not NFS. Files and their WAL/SHM are backed up together,
  or use the SQLite Online Backup API for a consistent online backup.
  The editor automatically creates an APFS snapshot after each manual
  correction and revocation, retaining the 20 newest snapshots. These are
  local safety copies, not independent disaster-recovery backups.
- Sidecar files: the physical `Artist/Album/Track.lrc` files on NAS remain
  fully compatible with Navidrome and are used by default.
- Audio embedded tags: read only, never rewritten by this service.
- A manual SQLite correction supersedes the old file for requests to the
  Home LrcAPI API. It does not automatically modify NAS files.
- When a user explicitly wants Navidrome to see that change, the editor
  can write **one** sidecar after a second confirmation. Original bytes are
  saved under the Mac-local `sidecar-backups/` directory. The destination
  is validated as a real NFS mount; all writes are per-track and atomic.
  Restore checks the current sidecar SHA-256 and refuses to overwrite
  someone else's later changes.
- Never export wholesale while Synology v2 RAID is degraded. Importing
  an entire library to SQLite is not required.

A few dozen manual corrections should be well under 1 MB of lyric payload;
SQLite pages and audit history add some overhead. Even 9000 tracks × 10 KB
would be only about 90 MB of raw lyric content, but we intentionally do not
copy all tracks.

## Core matching correction (2026-11-14)

The search aggregator now bounds its public `score` to **0.00–1.00**.
Its metadata weights already summed to 1.00; the previous extra 0.08
sync bonus produced a misleading 1.08 / "108%".
Now metadata receives 92% and synchronized timestamps 8%. A perfect
metadata score is **not human verification** of lyric words or timing:
remote candidates still have `verified=false`.

Explicitly conflicting versions (studio vs Live / Acoustic / Remix /
Demo / Instrumental / Remastered) are rejected in both directions;
ordinary song titles like "I Want to Live" are not treated as versions.
Malformed provider metadata or non-finite durations are dropped.
The existing artist, album and duration matching guards remain active.
Legacy Kugou and NetEase adapters now add trusted `source` labels in the
aggregator, so two distinct candidate lyrics with the same metadata can
be compared by provider instead of both appearing as anonymous "network"
results. Distinct lyric texts/timings are deliberately not collapsed.


HTTP contract tests cover `/lyrics`, `/jsonapi`,
`/api/v1/lyrics/single` and `/api/v1/lyrics/advance`.
No routes, auth secrets, SQLite schema, or NAS data were changed.

**Old cover gap resolved (2026-11-14):** `/cover` and the
`/api/v1/cover/{album,artist,music}` endpoints now use a small authenticated
read-only QQ Music JPEG handler. This is **not** the old unsafe HTTP proxy.
See the next section for its strict HTTPS allowlist and size limits.

## QQ Music lyrics and artwork integration (2026-11-14)

The codebase reuses two existing references without bringing a new runtime:
the user's `tooandy/navidrome-qqmusic-plugin` (Go/TinyGo WASM, **artwork
only**) and the MIT-licensed `Rain120/qq-music-api` lyric request logic.
The Go Navidrome plugin is a **separate project**, not a dependency of
this Python LrcAPI. Existing music files and SQLite data are unchanged.

- Lyrics: anonymous QQ Music search
  `music.search.SearchCgiService/DoSearchForQQMusicDesktop` at
  `https://u.y.qq.com/cgi-bin/musicu.fcg`, then base64 synced LRC from
  `https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg` using a
  verified 14-character song MID. Search result title, artist, album, and
  recording duration must match conservatively. Studio tracks do not
  silently use concert album results. Maximum two QQ candidates per query,
  added to the existing bounded aggregation pool; other providers remain
  available and retain priority when scores tie.
- Artwork: `GET /cover?artist=...&album=...` plus
  `/api/v1/cover/album?artist=...&album=...`,
  `/api/v1/cover/artist?artist=...`, and
  `/api/v1/cover/music?artist=...&title=...&album=...`.
  Every route needs the same **read-only API_AUTH** as `/lyrics`.
  Only exact matching album or artist MIDs are accepted. The handler
  builds fixed `https://y.gtimg.cn/music/photo_new/...` URLs (no caller
  supplied URLs), rejects redirects, enforces JPEG content and a 2 MB
  download bound, and returns image/jpeg. No audio or QQ account token.
- A single opt-out flag, `LRCAPI_QQMUSIC_ENABLED=0`, disables both QQ
  lyric aggregation and its artwork route, without disabling other
  sources. For persistent production opt-out, add that environment
  variable to the private Mac configuration files
  `~/.config/home-server/lrcapi.env` **and**
  `~/.config/home-server/lrcapi-editor.env`, then restart their respective
  launchd user services. Do not commit or reveal either secret-bearing file.
- The former `client_search_cp` request with `new_json=1` silently returned
  zero songs. The integration **does not use** this broken form. No
  extra Node.js service, dependency installation, database migration,
  public proxy change, or NAS write is needed.
- Test with `uv run --locked pytest -q` and
  `node --test tests/*.test.mjs`. The QQ-specific Python suite mocks
  network access and tests malformed responses, image size/content,
  strict identity matching, bounded score and recovery with QQ disabled.

## Using the correction page

On **Mac mini itself**, open (no new password prompt):

    http://127.0.0.1:28886/

From another device connected to the **home LAN 192.168.3.0/24**, open:

    http://192.168.3.7:28886/

The browser prompts for HTTP Basic credentials: username `lyrics`, with a
separate strong generated password kept only in the Mac's private file
`~/.config/home-server/lrcapi-editor.env` (mode 0600). To view it locally:

    sed -n 's/^LRCAPI_EDITOR_PASSWORD=//p' ~/.config/home-server/lrcapi-editor.env

Do not commit, paste, or send this password to a group. LAN clients always
require authentication; even browsing the library and fetching the JS are
protected. The Mac loopback entry retains its earlier behavior.

1. Browse artist → album → track; use the native HTML5 audio player to
   listen while the active sidecar or embedded LRC highlights each timed line.
   Select a line to calibrate it, or click its timestamp to seek.
2. Press **寻找网络候选** on demand to load independent alternative lyrics.
   Swipe horizontally in the lyrics panel or use its arrow controls to
   switch versions at **the same audio playback position**. None are
   automatically accepted.
3. Set a shift step between 0.01 and 10 seconds (e.g. 0.1 / 0.5 / 1).
   Press **−** to advance the lyrics or **+** to delay them, on any
   candidate including the default lyrics. Shifts are reversible even when
   an early timestamp is temporarily clamped to 0.
4. If a particular line drifts, select the lyric and press **把选中行对齐播放时刻**
   to timestamp that line at the current audio position. Untimed plain-text
   lyrics can be timestamped one line at a time. Expand **精细编辑歌词文本 /
   时间标签** for spelling fixes and detailed raw LRC editing.
5. Every selection, swipe, preview and timing change is **browser-only**
   until **保存纠错到 SQLite** is clicked. Saving writes one manual correction
   and a backup on the Mac. Existing NAS sidecar and audio remain unchanged.
6. **同步到同名 .lrc** is a *separate*, explicit per-song operation (for
   Navidrome), with an original sidecar backup and confirmation; rollback
   and SQLite correction revocation are available independently.

### Playback protocol and supported formats

The editor's private `GET /api/audio?path=...` streams the one selected audio
file read-only from the existing NAS NFS mount with HTTP Range support (206).
It reuses the editor's trusted LAN IP, Host, password and path checks;
**the public LrcAPI / reverse proxy do not serve audio**. Native browser
playback works with common MP3/M4A/AAC/FLAC/WAV/OGG/Opus formats depending
on the browser. APE/DSF/WMA require transcoding and are not offered as
playback formats; lyric editing still works. No bulk file import or NAS
writeback is involved. HTTP Basic on home LAN is not TLS-encrypted, so
do not port-forward this private editor to the Internet.

### Theme

The upper-right **外观** selector offers **跟随系统** (the default), **浅色**
and **深色**. All lyric and browsing surfaces are styled for both color
schemes, including the mobile layout. The choice is saved in **this browser's
localStorage** (`home-lrcapi-theme-v1`), not in SQLite, audio files, or user
credentials. Automatic mode follows system color-scheme changes without a
reload. No CDN or third-party JavaScript is required. Modern browsers
supporting the CSS `light-dark()` color function are expected.

Run the isolated UI logic regression with `node --test tests/theme.test.mjs`.

The editor binds only to `127.0.0.1` and `192.168.3.7` (not `0.0.0.0`).
Source IPs must be loopback or within `192.168.3.0/24`; untrusted Host and
Origin headers are blocked. LAN requests need separate password authentication
with rate-limited failures, and writes still need a random CSRF token.

**Security limit:** This LAN address is HTTP, not HTTPS. HTTP Basic encodes
credentials but does not encrypt them on the network. Use only a trusted home
Wi-Fi/LAN, and **do not forward TCP 28886 from the router to the Internet**.
For secure access from other networks, add a separate HTTPS/Tailscale
authenticated route instead of widening the bind address. Debian Nginx and
the existing public lyrics API remain unchanged.

## Running and managing Python with uv

```sh
cd ~/Documents/Code/LrcApi-next
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check app.py api mod admin tests --select E9,F63,F7,F82
uv run --locked pip-audit --path .venv/lib/python3.12/site-packages
```

`pyproject.toml` and `uv.lock` are the only dependency sources. No
`requirements.txt` or frozen pip list. The `.venv` directory is not
tracked by Git. Production services run the interpreter generated by
`uv sync`; do not run unconstrained `pip install -U` in production.

Python 3.12 runs in GitHub Actions using `astral-sh/setup-uv@v7`, with
locked installs, pytest, critical ruff checks and `pip-audit`. Old Docker
release/build workflows have been removed from this feature branch.

## Backups, service controls and rollback

- Public Mac API: `launchctl list | grep local.home-server.lrcapi`.
- Editor service: `launchctl list | grep local.home-server.lrcapi-editor`.
- Worktree canary: `local.home-server.lrcapi-canary`, port 28884;
  stop once rollout is complete.
- Secrets remain in `~/.config/home-server/lrcapi.env` (0600), never Git.
- Editor has no access through Debian Nginx.
- Before rolling out the new API, save the old LaunchAgent plist and
  keep the old checkout `~/Documents/Code/LrcApi` unchanged. Reverting
  the Mac API process to the old checkout preserves original HTTPS and
  legacy HTTP addresses, because Nginx upstream remains `:28883`.
- The Docker 1.5.6 container on Debian remains `exited/restart=no`
  for emergency rollback; never start it while Debian Nginx is listening
  on the same port.
- SQLite corrections survive switching back/forth between the new and
  old binaries; the old binary simply ignores them. Back up the database
  before schema changes.

## Operational caveats

- GitHub feature branch changes do **not** modify the fork's `main`
  until reviewed/merged.
- The user-level launchd services require a logged-in user session.
- The NAS v2 degraded RAID requires priority backup/repair; the lyrics
  editor deliberately never mass-writes tags or sidecar files.
- Upstream rate limits remain relevant. LRCLIB is one provider among
  several, and lyrics returned by the Internet are candidates rather
  than automatically human-verified copies.
