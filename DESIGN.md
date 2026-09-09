# harelphotos — design & implementation plan

Expansion of [PLAN](PLAN) into something implementable. `PLAN` stays as the
original statement of intent; this file is the working design.

---

## 0. Decisions already made

From the original PLAN plus follow-up answers:

| Question | Decision |
|---|---|
| Front-end web server | **Apache httpd** (reverse proxy / static handoff) |
| Media types in v1 | **JPEG only.** Video/HEIC/RAW/PNG deliberately out of scope, but the code must not make them impossible to add |
| Derivative image format | **AVIF**, with WebP→JPEG fallback by `Accept` negotiation (§9.4) |
| Bulk encoding | Runs on the **fast home machine**; only the small derived tree is shipped to the weak server |
| Login methods | **Local accounts + Google Sign-In**, both in v1 |
| Per-directory config format | **TOML** — `.album.toml` |
| Derivative storage | **Separate mirror tree**, originals stay pristine |
| Extra v1 features | Keyboard + swipe navigation; EXIF info panel |
| Target OSes | **Fedora** (current) and **Rocky Linux 9.8** |
| Package sources | Distro packages *or* PyPI — both fine, just nothing enormous or obscure |

Decided during this design pass, from measurements (§2, §3) rather than taste:

| Question | Decision | Why |
|---|---|---|
| Python version | **3.11+** (Rocky: `dnf install python3.12`, confirmed available) | Rocky's stock 3.9 cannot install a maintained Pillow — §3.1 |
| Image library | **Pillow**, backend-pluggable | pyvips measured at only ~1.4× faster here, not 3–5× — §3.3 |
| Size ladder | **thumb [256, 512], view [1280, 2048]**, globally configurable | measured table in §2.1, reasoning in §2.3 |
| Derived tree size | ~17 GB for 80k photos (5.6% of originals) | §2.2 |
| Python dependencies | **4**: Flask, Pillow, requests, gunicorn | AVIF ships inside the Pillow wheel — §3.2 |
| WSGI hosting | **gunicorn + Apache `ProxyPass`**, not mod_wsgi | mod_wsgi is compiled against one interpreter and the distro's is built for Rocky's 3.9, not the 3.12 we need — §13.2 |
| Install docs | **`INSTALL.md`**, a deliverable of M8 | §13.0 drafts its content |
| TLS | **Let's Encrypt via certbot** (free, auto-renewing), keyed to the hostname | §13.3 — mandatory: Google OAuth and secure cookies both require HTTPS |
| Photo grid | **justified rows, never cropped**, from M4 | §11.1c — what Google Photos actually does |
| Album listing | separate section, **uniform cards with the name captioned below**, subdirs first | §11.1a — the hierarchy Google Photos doesn't have |
| Subdir order | natural sort by name; `dirsort`, parent `order`, child `sort_key` to override | §11.1b |
| Default sort | **EXIF date, else mtime, tie-break filename** | §5.3 |
| UI language | **English only**, no i18n machinery; CSS logical properties so a future Hebrew/RTL pass stays cheap | §11.4 |
| Filenames | **assumed UTF-8**; anything else is skipped and reported, never crashes a scan | §8 phase 1 |
| `photo_root` | **`/srv/photos`** on the server, **`~/pictures`** on the home machine | §13.1a — moving it out of `/home` avoids ACLs and SELinux special-casing entirely; relative paths in the DB make the difference free |
| Change detection | `mtime` decides whether to *look*; **`content_sig` decides whether to *work*** | §8 phase 2 — stops `jhead -ft` triggering a 22 core-hour re-encode |

---

## 0.1 Manual steps — things no code will do for you

⚠️ **Read this before starting implementation.** These are actions on the real
machines, not programming tasks. Each one blocks a specific milestone, and
forgetting them looks like a bug rather than a missing step.

| # | Action | Machine | Blocks | Detail |
|---|---|---|---|---|
| 1 | **Move the photos out of your home directory** — e.g. `/home/nyh/pictures` → `/srv/photos`, then `chown -R nyh:nyh /srv/photos` | **server only** | M8 | §13.1a |
| 2 | Point DNS at the server and run `certbot --apache -d photos.harel.org.il` | server | M6 | §13.3 |
| 3 | Open ports 80 and 443; `setsebool -P httpd_can_network_connect 1` | server | M8 | §13.4 |
| 4 | Copy the hero image (`newsign2.jpg`) to `/etc/harelphotos/` | both | M5 | §11.5 |
| 5 | Create the accounts: `harelphotos user add …` for each family member | both | M5 | §15 |
| 6 | *(optional)* Register the Google OAuth client and publish the consent screen | — | M6 | §12.2 |

**On step 1 specifically**, since it is the one with real work behind it: on the
**server** the photos must end up somewhere outside `/home`. Leaving them under
your home directory works, but only at the price of a POSIX ACL, an SELinux
boolean and a weakened `ProtectHome` — three workarounds that all vanish if the
directory simply lives at `/srv/photos` instead (§13.1a).

On the **home machine, change nothing**: `~/pictures` is fine exactly where it
is, because `harelphotos serve` runs as you, with no service account, no Apache
and no SELinux confinement. The two machines legitimately have different
`photo_root` values; the index stores relative paths, so this costs nothing.

Before moving 300 GB, check `df /home /srv` — same filesystem makes `mv` an
instant rename, different filesystems make it a real copy worth running under
`screen`.

Nothing here blocks M1–M5, which run entirely on the home machine.

---

## 1. Guiding principles

1. **The filesystem is the database.** Photos and `.album.toml` files are the
   only authoritative state (plus `users.toml`). The SQLite index and the whole
   derived tree are *caches*: `rm -rf` either one and a rescan rebuilds it. This
   is what makes the system comprehensible years later, and it is what the PLAN
   asks for when it says metadata lives in files, not a database.
2. **Move all cost to scan time.** A page view should do: a few indexed SQLite
   reads, one Jinja render, then Apache serves pre-encoded bytes. No image
   processing ever happens in a request.
3. **Small, boring, readable stack.** One Flask app, one SQLite file, one CSS
   file, one JS file, no build step, no npm, no bundler, no ORM, no framework
   you have to relearn.
4. **Identical on both machines.** One venv, one command, same code path on the
   Fedora laptop and the Rocky server. Distro differences are absorbed by
   using PyPI wheels for everything Python.
5. **Fail closed.** Anything not explicitly permitted is 404 (not 403 — don't
   confirm that a private album exists).

### On generating static HTML

The PLAN floats pre-generating static HTML. Recommendation: **don't** — and the
per-user footer (§11.3) is not what rules it out. Measured Jinja render times
for a realistic album template on this machine:

| album size | render | HTML size |
|---:|---:|---:|
| 100 photos | 1.2 ms | 38 KB |
| 500 photos | 4.2 ms | 182 KB |
| 3000 photos | 22 ms | 1.1 MB |

Even allowing the weak server to be 3–5× slower, a 500-photo album costs it
~15–20 ms of CPU. That is not a bottleneck for a family site; the expensive part
of this site is *bytes*, and those are already static files Apache serves with
no Python involvement.

**Pages were never shareable between users anyway.** ACLs (§6) mean the set of
subdirectories a given person may see differs per person, so any static album
page would have to be materialised once per access-control class and
regenerated on every metadata edit. Printing the viewer's name in the footer is
a second, much smaller instance of the same constraint — it costs nothing extra
because the page is already per-user.

If render cost ever *did* matter, the fix is cheap and doesn't need static
files:

- **`ETag` + 304** (already in §10.3) — a returning visitor's reload costs one
  indexed row read and sends no body at all. This is most of the benefit of
  static HTML for near-zero complexity, and it is in the plan.
- **An in-process LRU cache** of rendered bodies keyed by `(dir_id, acl_class)`,
  with the username punched in client-side. Standard technique, deliberately
  deferred (§18) — it trades a JS dependency and cache-invalidation logic for
  milliseconds we don't need.

The 3000-photo row above looks like a scaling problem — **1.1 MB of HTML** — but
album markup compresses about 31×, so it is ~35 KB on the wire and we send it in
one piece (§10.3). Static generation would not have helped with that either.

---

## 2. Measured numbers (not estimates)

Everything below was benchmarked on this machine with Pillow 12.3 (PyPI wheel,
bundled libavif), averaged over six real 12 MP phone JPEGs (2.8–6.2 MB each),
single core, AVIF `speed=6`, 4:2:0.

### 2.1 The size ladder

| Longest side | AVIF q | avg size | encode | GB @ 80k photos | what it's for |
|---:|---:|---:|---:|---:|---|
| 200 | 55 | 4.1 KB | 31 ms | 0.34 | too soft for DPR ≥ 2 |
| **256** | **52** | **5.1 KB** | **35 ms** | **0.42** | **grid tile, DPR 1** |
| 320 | 50 | 6.9 KB | 49 ms | 0.56 | |
| 400 | 50 | 9.7 KB | 61 ms | 0.79 | |
| **512** | **48** | **12.5 KB** | **69 ms** | **1.03** | **grid tile, DPR ≥ 1.5** |
| **1280** | **46** | **59.3 KB** | **132 ms** | **4.85** | **lightbox on phones** |
| 1600 | 45 | 85.5 KB | 177 ms | 7.01 | |
| 1920 | 45 | 114.8 KB | 232 ms | 9.41 | |
| **2048** | **45** | **127.5 KB** | **255 ms** | **10.45** | **lightbox on desktops** |
| 2560 | 44 | 158.9 KB | 355 ms | 13.02 | optional hi-DPI tier |
| 3200 | 43 | 209.4 KB | 542 ms | 17.15 | optional hi-DPI tier |

### 2.2 The chosen default ladder: 256 / 512 / 1280 / 2048

Measured end-to-end with the cascade pipeline of §9.1 (one decode, each tier
resized from the tier above):

```
decode w/ draft()     93 ms      ← libjpeg shrink-on-load
resizes (4 tiers)    400 ms
encodes (4 tiers)    498 ms
--------------------------------
TOTAL                991 ms/photo/core        205 KB/photo
```

Projected for a 300 GB collection (~80,000 photos at ~4 MB):

- **Bulk pass: ~22 core-hours** → **~1.8 hours wall clock** on 12 cores.
- **Derived tree: ~16.8 GB** — 5.6% of the originals, i.e. **18× smaller**,
  satisfying the PLAN's "order of magnitude smaller" requirement with margin.
- For comparison, the same four tiers as JPEG q80 would be ~520 KB/photo ≈
  **42 GB**. AVIF saves ~25 GB of disk and ~2.5× the bandwidth per photo viewed.

### 2.3 Why four tiers rather than two

Two tiers each for the grid and the lightbox, delivered by plain `srcset`
(§10.3), which is exactly how Google Photos behaves — it requests a different
`=w…` size per device pixel ratio. The extra tiers cost disk but buy the two
things the PLAN cares most about:

- **Album pages are the bandwidth-critical page.** A 500-photo album costs
  2.5 MB of thumbnails at the 256 tier versus 6.3 MB at the 512 tier. A DPR-1
  desktop should not pay retina prices, and on a weak uplink this is the
  difference between "very speedy" and not.
- **Phones should not download desktop-sized photos.** A phone at 400 CSS px
  wide and DPR 3 wants ~1200 px; serving it 1280 (59 KB) instead of 2048
  (128 KB) more than halves the cost of every swipe — and the lightbox
  prefetches neighbours, so it's really 2–3 images per swipe.

If disk gets tight, `sizes.thumb = [400]` and `sizes.view = [1600]` cuts the
derived tree to ~7.8 GB with a modest quality/bandwidth cost. It's one config
line and a rescan (§7, `deriv_key`).

### 2.4 Encoder speed setting

At the 2048 tier, `speed=6` gives 128 KB in 255 ms; `speed=8` gives ~137 KB in
~85 ms. Since encoding happens once, offline, on the fast machine, **speed=6 is
the right default** — pay 3× the time once to save ~7% of disk and bandwidth
forever. It stays configurable so a regenerate-on-the-server scenario can drop
to 8.

---

## 3. Technology choices

### 3.1 Python version: 3.11+, and why not Rocky's stock 3.9

Rocky 9.8 ships Python **3.9** as `python3`. That is a dead end, verified
against PyPI rather than assumed:

- **Pillow 12.x requires Python ≥ 3.10** — there are no `cp39` wheels for
  12.0.0 or later. Staying on 3.9 pins us to Pillow 11.3.0 permanently, with no
  future security fixes, in the one library that parses untrusted image files.
  (11.3.0 is also the *first* release with AVIF support, so we'd be locked to a
  single usable version.)
- **gunicorn 26 requires Python ≥ 3.10** as well.

Rocky 9 carries newer interpreters in AppStream as ordinary packages, so the fix
is one `dnf install python3.12`. Requiring **Python 3.11+** additionally gets us
`tomllib` in the standard library, removing the `tomli` dependency entirely.

So: `requires-python = ">=3.11"`. Fedora satisfies this out of the box; Rocky
needs one package (§13.1).

### 3.2 Dependencies — the complete list

```
Flask          # web framework + Jinja2 + Werkzeug (password hashing, WSGI)
Pillow>=11.3   # decode, resize, AVIF encode, EXIF   ← 11.3 is the first with AVIF
requests       # Google OAuth token exchange
gunicorn       # WSGI server behind Apache
```

Four packages, all household names, all pure-pip with wheels — **identical on
Fedora and Rocky**, no EPEL, no compiler, no `libavif-tools`, no system
`python3-*` packages. The deciding factor: verified that the PyPI Pillow wheel
bundles AVIF encode support (`PIL.features.check('avif')` → True), so we avoid
distro-specific AVIF packaging, which is exactly where Fedora and RHEL 9
diverge. Note Fedora's *own* `python3-pillow` package does **not** have AVIF
enabled — another reason to use the venv on both machines rather than distro
packages.

Apache modules: `mod_proxy` + `mod_proxy_http` (both stock). `mod_xsendfile` is
*optional* and auto-detected — see §10.4.

### 3.3 pyvips — measured, then rejected as the default

Worth taking seriously, so it was benchmarked rather than hand-waved. Same six
photos, same four-tier ladder, single-decode cascade in both, output sizes
matched, metadata stripped:

| | time/photo | 80k photos |
|---|---:|---:|
| Pillow (speed=6) | 991 ms | 22.0 core-h |
| pyvips (effort=3) | 718 ms | 16.0 core-h |

**pyvips is ~1.4× faster here — not the 3–5× it gets credited with.** The reason
is structural: roughly half the wall time is AVIF encoding inside libaom, which
is *the same library* under both. vips only wins on decode-and-resize, which is
the other half. Correcting for that, the honest speedup on this workload is
modest, and it buys ~30 minutes on a one-time job.

Against that: Pillow does decode, resize, AVIF encode *and* EXIF extraction with
one familiar API, where vips would need a second library for metadata; and vips
has sharp edges here — with default settings it wrote **28 KB** 256px
thumbnails (embedded metadata) until `keep=ForeignKeep.NONE` was passed, a
5× bloat that is invisible until you check. Exactly the kind of trap that
argues for the library you already know.

Decision: **Pillow is the default**, and §9 defines the derive step behind a
small backend interface so a pyvips backend can be dropped in. Note that the far
bigger lever is the encoder `speed`/`effort` setting (§2.4): Pillow at speed=8
is ~3× faster than at speed=6, which dwarfs the library choice.

Also rejected:

- *Authlib / python-jose* — Google OIDC needs ~60 lines with `requests` (§12.2).
- *SQLAlchemy* — the schema is 3 tables; raw `sqlite3` is clearer.
- *Celery / Redis / Postgres* — nothing here needs a job queue or a server DB.

---

## 4. On-disk layout

```
$PHOTO_ROOT/                       server: /srv/photos   home machine: ~/pictures  (read-only to the app)
├── 2019/
│   ├── .album.toml
│   ├── summer/
│   │   ├── .album.toml
│   │   ├── IMG_1234.jpg
│   │   └── IMG_1235.jpg
│   └── ...
└── ancient/
    └── ...

$DERIVED_ROOT/                     e.g. /var/lib/harelphotos/derived   (cache, rsync'able)
├── 256/2019/summer/IMG_1234.jpg.avif     grid tile, DPR 1
├── 512/2019/summer/IMG_1234.jpg.avif     grid tile, DPR ≥ 1.5
├── 1280/2019/summer/IMG_1234.jpg.avif    lightbox, phones
├── 2048/2019/summer/IMG_1234.jpg.avif    lightbox, desktops
├── webp/2048/…                           fallback for Safari 14–16.3 etc. } made on
├── jpeg/2048/…                           fallback for anything older      } demand (§9.4)
└── public/landing-{640,1280}.avif        landing-page hero, served without a login (§11.5)

$STATE/                            e.g. /var/lib/harelphotos/
├── index.sqlite                   rebuildable index (+ -wal, -shm)
└── geonames.sqlite                place-name dataset; re-downloadable (§9.5)

$CONFIG/                           e.g. /etc/harelphotos/
├── config.toml
├── users.toml
└── secret_key                     32 random bytes, mode 0600
```

Two naming decisions:

- **Tier directories are named by their pixel size**, not by a role like
  `thumb`/`view`. The ladder is configurable (§5.1), so role names would go
  stale the moment you retune it; with size names, changing
  `sizes.view = [1280, 2048]` to `[1280, 2560]` simply makes `2560/` appear and
  `harelphotos gc` remove `2048/`. Self-describing, and no ambiguity about what
  a file on disk actually contains.
- **The derived path keeps the original extension** before adding `.avif`
  (`IMG_1234.jpg.avif`), so `IMG_1234.jpg` and `IMG_1234.png` in one directory
  stay unambiguous — which matters the day we add PNG support.

`$PHOTO_ROOT` is mounted read-only for the web process; only the `scan` CLI and
the (optional, admin-only) cover-picker write to it.

---

## 5. Configuration files

### 5.1 Global `config.toml`

```toml
photo_root   = "/srv/photos"                 # home machine: "/home/nyh/pictures"
derived_root = "/var/lib/harelphotos/derived"
index_db     = "/var/lib/harelphotos/index.sqlite"
users_file   = "/etc/harelphotos/users.toml"
secret_key_file = "/etc/harelphotos/secret_key"

base_url   = "https://photos.harel.org.il"   # needed for OAuth redirect URI
sendfile_header = "auto"                     # "auto" | "X-Sendfile" | "none"  (§10.4)
log_file        = "/var/lib/harelphotos/harelphotos.log"   # unset = stderr (§13.7)

[ui]
site_title      = "The Har'El Family Photo Album"   # browser title
heading         = "Photo Album"                     # landing page (§11.5)
tagline         = "By invitation only. Please login to continue."
footer_text     = "You can view this private album because you are logged in as {user}. If you wish, you can {logout}."
landing_image   = "/etc/harelphotos/newsign2.jpg"
show_gps        = true        # EXIF panel: show location at all (§11.2)
map_link        = "osm"       # "osm" | "google" | "none" — outbound map link
album_page_size = 5000        # safety valve only (§10.3); below this, one page
dirsort         = "name"      # default subdirectory order (§11.1b); "-name" = newest first
dir_card_aspect = "4/3"       # subdirectory card shape; "native" = don't crop covers

[sizes]                       # px, longest side; lists become srcset tiers
thumb = [256, 512]            # album grid    (§2.3)
view  = [1280, 2048]          # lightbox
# Smaller disk budget?  thumb = [400], view = [1600]      → ~7.8 GB @ 80k
# Retina desktops?      view  = [1280, 2048, 3200]        → ~34 GB @ 80k

[encode]
format         = "avif"
fallback       = "auto"       # "auto" = Accept negotiation (§9.4); "none" = AVIF for all
speed          = 6            # 0 slowest/smallest … 10 fastest/largest
subsampling    = "4:2:0"
recipe_version = 1            # bump to force a full re-encode
# per-tier quality; the default curve gives smaller images a higher Q,
# because downscaling concentrates detail and low-Q artefacts show more.
quality = { 256 = 52, 512 = 48, 1280 = 46, 2048 = 45 }
quality_default = 46          # used for any tier not listed above

[scan]
jobs = 0                      # 0 = os.cpu_count()
exclude = [".*", "@eaDir", "Thumbs.db"]

[groups]
family  = ["nyh", "dad@gmail.com", "sis"]
cousins = ["@family", "cousin1@gmail.com"]     # groups may reference groups

[google]
enabled       = true
client_id     = "…apps.googleusercontent.com"
client_secret = "…"           # or client_secret_file = "…"
```

Search order: `$HARELPHOTOS_CONFIG`, `./config.toml`,
`~/.config/harelphotos/config.toml`, `/etc/harelphotos/config.toml`.

### 5.2 `users.toml`

Not a database — a hand-editable file, per the PLAN's philosophy. Managed by
`harelphotos user …` but perfectly editable by hand.

```toml
[users.nyh]
name     = "Nadav"
password = "scrypt:32768:8:1$…"      # werkzeug hash; omit for Google-only users
google   = "somebody@gmail.com"       # this account may also log in via Google
admin    = true

[users."dad@gmail.com"]              # a pure Google user: key IS the email
name   = "Dad"
google = "dad@gmail.com"

[users.grandma]
name     = "Grandma"
password = "scrypt:…"
epoch    = 2                         # bump to invalidate that user's sessions
```

The table key is the user's **identity token** — the thing that appears in ACLs
and in the session cookie. For local accounts it's a username; for Google-only
accounts, use the email as the key for clarity.

### 5.3 `.album.toml` — per-directory metadata

Every key optional; a directory with no `.album.toml` behaves sensibly.

```toml
title       = "Summer in Greece"      # default: prettified directory name
description = "Two weeks on Naxos."   # optional blurb at the top of the album
cover       = "IMG_1234.jpg"          # representative photo; may be "sub/dir/IMG_9.jpg"
sort        = "date"                  # PHOTOS in this dir. date|exif|mtime|name; "-" reverses
dirsort     = "name"                  # SUBDIRECTORIES of this dir: name|-name|date|-date (§11.1b)
order       = ["passover", "summer"]  # subdirs pinned first, in this order; rest follow dirsort
sort_key    = "1975"                  # how THIS dir sorts within its parent (overrides its name)
group_by    = "none"                  # none | day | month — date headers within the album
hidden      = false                   # omit from parent's listing (URL still works)
allow       = ["dad@gmail.com", "@family"]    # ACL — see §6
allow_replace = false                 # true = ignore ancestors' restrictions
location    = "Naxos, Greece"          # fallback place name for photos with no GPS (§9.5);
                                       # inherited by subdirectories

[photos."IMG_1234.jpg"]               # optional per-photo overrides
title  = "Nadav on the beach"
hidden = true
```

**Sort order — default `date`.** Filenames carry no meaningful order across a
collection assembled from different phones and cameras, so they are not the
default. `date` means:

```sql
ORDER BY COALESCE(taken, mtime_ns/1000000000) ASC, name ASC
```

i.e. **EXIF `DateTimeOriginal` when present, filesystem mtime otherwise**, with
filename as a stable tie-break so equal timestamps never shuffle between scans.
That directly supports the workflow of forcing mtimes to match EXIF dates
(`jhead -ft`) for the scans and hand-offs that have no EXIF at all: fix the
filesystem date and the photo sorts correctly, with nothing to configure. The
explicit `exif` and `mtime` values exist for the rare album where you want one
source only — `exif` sorts photos with no EXIF date last rather than mixing them
in.

This choice is what motivates the content-signature design in §8 phase 2: mtime
becomes something you *edit as metadata*, so it must not be what decides whether
to re-encode.

Design notes:

- `cover` resolution order: explicit `cover` → first photo in this directory in
  the effective sort order → cover of the first non-empty subdirectory →
  a generic folder icon. Deterministic, so covers don't shuffle between scans.
  (`cover = "auto:middle"` and `"auto:hash"` are accepted as alternatives to
  "first" for people who find first-photo covers boring.)
- Parse errors are **non-fatal**: log a warning, record it in the DB, show it in
  `harelphotos check`, and fall back to defaults. A typo in one TOML file must
  never take the site down.
- `.album.toml` is re-read only when its `(mtime, size)` changes, so it costs
  nothing on a rescan.
- The file is excluded from the photo listing, obviously — as is anything
  matching `scan.exclude`.

---

## 6. Access control model

Two things are protected: **directories** (and everything under them) and, by
extension, the image bytes within them.

**Rule: restrictions accumulate.** A user may view directory `D` if, for *every*
`.album.toml` on the path from the root to `D` that specifies `allow`, the user
matches that list. So marking `/2019/private` as `allow = ["nyh"]` locks that
subtree for everyone else, and nothing deeper down can accidentally re-open it.
This is exactly what the PLAN asks for ("allow only specific authenticated users
to look into a certain directory *and its subdirectories*").

**Escape hatch:** `allow_replace = true` on a directory means "this list is
absolute; ignore ancestors". That covers the rare "one album inside my private
tree that I want to share with the cousins" case, and it's explicit enough that
you can't do it by accident.

Implementation:

- The scanner stores, per directory, the **chain** of allow-lists inherited from
  its ancestors plus its own, as a JSON array-of-arrays (usually `[]`, i.e. no
  restriction at all). `allow_replace` truncates the chain.
- At request time: `all(user_matches(u, lst) for lst in chain)`.
- `user_matches` expands `@group` references from `config.toml` **at request
  time**, so editing a group in the config takes effect immediately without a
  rescan. Group expansion is recursive with a cycle guard.
- `admin = true` users bypass all ACLs.
- Denials return **404**, not 403, and hidden/denied directories are omitted
  from parent listings and from ancestor breadcrumbs.

**Critical invariant, to be covered by a test:** the ACL check happens on the
`/i/…` image routes and the `/orig/…` download route, not only on the HTML
pages. A private album's thumbnails must not be fetchable by guessing URLs.

---

## 7. Index database (`index.sqlite`)

Pure cache. `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`. A `schema_version`
row drives migrations; on mismatch and no migration path, just rebuild.

```sql
CREATE TABLE dirs (
  id            INTEGER PRIMARY KEY,
  parent_id     INTEGER REFERENCES dirs(id),
  path          TEXT NOT NULL UNIQUE,    -- relative to photo_root; '' for root
  name          TEXT NOT NULL,
  title         TEXT,
  description   TEXT,
  sort          TEXT,                       -- photo order within this dir
  dirsort       TEXT,                       -- child order (§11.1b)
  order_json    TEXT,                       -- parent's explicit `order` list, JSON
  sort_key      TEXT,                       -- this dir's key within its parent; NULL = use name
  natkey        TEXT,                       -- natural-sort collation key of sort_key or name
  hidden        INTEGER NOT NULL DEFAULT 0,
  acl_chain     TEXT NOT NULL DEFAULT '[]',   -- JSON [[tok,…],…]
  cover_photo   INTEGER REFERENCES photos(id),
  cfg_mtime     INTEGER, cfg_size INTEGER,    -- .album.toml change detection
  cfg_error     TEXT,
  n_photos      INTEGER NOT NULL DEFAULT 0,   -- direct
  n_photos_rec  INTEGER NOT NULL DEFAULT 0,   -- including subdirectories
  n_subdirs     INTEGER NOT NULL DEFAULT 0,
  date_min      INTEGER, date_max INTEGER,    -- recursive EXIF date span
  seen          INTEGER NOT NULL              -- scan generation
);
CREATE INDEX dirs_parent ON dirs(parent_id, natkey);

CREATE TABLE photos (
  id          INTEGER PRIMARY KEY,
  dir_id      INTEGER NOT NULL REFERENCES dirs(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  size        INTEGER NOT NULL,        -- original bytes  ┐ cheap "should we look?"
  mtime_ns    INTEGER NOT NULL,        -- original mtime  ┘ check (phase 1)
  content_sig BLOB,                    -- blake2b(size‖head‖tail); "did it really change?" (phase 2)
  width       INTEGER, height INTEGER, -- AFTER exif orientation applied
  taken       INTEGER,                 -- EXIF DateTimeOriginal, unix epoch, or NULL
  title       TEXT,
  hidden      INTEGER NOT NULL DEFAULT 0,
  color       TEXT,                    -- '#rrggbb' dominant colour placeholder
  exif_json   TEXT,                    -- camera, lens, exposure, iso, focal, gps
  place       TEXT,                    -- resolved city, or NULL (§9.5)
  place_dist  INTEGER,                 -- metres to it; large renders as "near X"
  landmark    TEXT,                    -- optional landmark qualifier, or NULL
  deriv_key   TEXT,                    -- fingerprint of what the derivatives were made from
  deriv_error TEXT,
  seen        INTEGER NOT NULL,
  UNIQUE(dir_id, name)
);
CREATE INDEX photos_dir  ON photos(dir_id, name);
CREATE INDEX photos_date ON photos(taken);
```

`natkey` exists because SQLite cannot natural-sort. It is computed once at scan
time from `COALESCE(sort_key, name)` by lower-casing and zero-padding every run
of digits to a fixed width — `Day 10` → `day 0000000010`, which then sorts
correctly under ordinary `ORDER BY`. Recomputed whenever a `.album.toml`
changes, and indexed with `parent_id` so listing a directory's children in order
is a single index scan.

`deriv_key = hash(content_sig, recipe_version, tiers, qualities, speed)`, where
`tiers` is the sorted configured ladder. A photo needs (re)processing iff its
computed key differs from the stored one, so adding or removing a tier, retuning
quality, or bumping `recipe_version` invalidates exactly what it should — no
separate "force" bookkeeping.

**`deriv_key` deliberately does not include `mtime_ns`.** Timestamps change
without pixels changing (§8, phase 2); making derivatives depend on them would
turn a metadata tidy-up into a 22 core-hour re-encode.

All paths stored are **relative**, so the same `index.sqlite` works on the home
machine and the server and can simply be copied between them.

---

## 8. The scanner

`harelphotos scan` — the program the PLAN asks for: "figure out what changed and
create/delete what needs changing", efficiently.

### Phase 1 — walk (single-threaded, fast, ~seconds for 80k files)

Recursive `os.scandir` from `photo_root`. `scandir` gives file type and `stat`
results from the directory read itself, so this is essentially the cost of
`find`. For each directory:

1. Upsert the `dirs` row, stamp `seen = generation`.
2. If `.album.toml`'s `(mtime, size)` changed, re-parse it and recompute
   `acl_chain` for this directory and (because the chain is inherited) mark its
   subtree for chain recomputation.
3. For each file with a known photo extension, upsert the `photos` row with
   `(size, mtime_ns)` and stamp `seen`. If `(size, mtime_ns)` differs from the
   stored values, flag the photo **stat-dirty**.

**Filenames are assumed UTF-8** (§19). No transcoding, no encoding guessing.
The one thing the scanner owes you is not dying on a surprise: a name that
`os.scandir` returns with surrogate escapes is skipped, counted, and listed by
`harelphotos check` — not raised halfway through a two-hour run, and not
silently swallowed either. One `if` and a warning; the alternative is a scan
that crashes on its 80,000th file after two hours of work.

Then delete every row whose `seen != generation` — those are the files and
directories that disappeared. Their derivative files go on a deletion list.

### Phase 2 — header read: content signature + EXIF (parallel, I/O-bound)

For every stat-dirty photo, read the **first 128 KB and last 64 KB** of the file
and from that single read produce two things:

- **`content_sig`** = `blake2b(size ‖ head ‖ tail)`, 16 bytes.
- **EXIF and dimensions** — `Image.open()` is lazy and parses only the header,
  so `DateTimeOriginal`, orientation, camera/lens/exposure/ISO/GPS and the SOF
  dimensions all come out of bytes we have already read, with no pixel decoding.

Then: if `content_sig` is unchanged, just store the new `mtime_ns` and stop —
**the file's bytes did not change, so its derivatives are still valid.** Only a
changed signature marks the photo for phase 3.

#### Why this phase exists — the `jhead -ft` trap

This falls directly out of choosing date-based sorting (§5.3). You said you like
forcing the filesystem date to match the EXIF date, which is what
`jhead -ft`, `exiftool "-FileModifyDate<DateTimeOriginal"` or `touch` do. Every
one of those **rewrites mtime without changing a single pixel**. Under a naive
`(size, mtime)` scheme that is indistinguishable from "the photo changed", so
one tidy-up pass across the collection would trigger a full **22 core-hour**
re-encode of all 80,000 photos, for nothing.

With the signature check, that same pass costs one ~200 KB read per touched
file — a few minutes of I/O — and re-encodes nothing. The rule is:

> `mtime` decides *whether to look*. `content_sig` decides *whether to work*.

Hence `deriv_key` is built from `content_sig`, never from `mtime` (§7). The same
property makes the two-machine rsync flow (§14) robust against any mtime drift
between the machines.

Phase 2 also means dates and dimensions are known **before** any derivative is
built, so albums sort correctly even while a first scan is still running.

### Phase 3 — derive (parallel, resumable, the only expensive part)

Select all photos whose `deriv_key` is stale. Feed them to a
`multiprocessing.Pool(jobs)` — worker processes, not threads, because Pillow
work is CPU-bound. Each task returns its result; the parent commits to SQLite in
batches of ~200 (workers never touch the DB, which sidesteps all SQLite
concurrency questions).

Resumability and crash-safety:

- Each derivative is written to `NAME.tmp.<pid>` and `os.replace()`d into
  place — an interrupted run never leaves a truncated AVIF.
- `deriv_key` is stored only after *every* configured tier is on disk.
- So `^C` and re-run simply continues; already-done work is skipped.

Politeness on the weak server: `--jobs N`, `--nice N` (default 10), and
`--limit N` (stop after N photos, for chipping away at a big backlog).

Failures (corrupt JPEG, truncated file) are recorded in `deriv_error` and do not
abort the run; `harelphotos check` lists them.

### Phase 4 — rollup and prune

- Bottom-up pass computing `n_photos_rec`, `date_min`/`date_max`, and resolving
  `cover_photo` per directory.
- Delete derivative files for removed photos, then remove now-empty directories
  from the derived tree.
- A separate `harelphotos gc --deep` walks the whole derived tree looking for
  files with no matching DB row (catches derivatives orphaned by a crash).

### Only one scan at a time

`scan`, `geocode` and `gc` take an exclusive lock — an `flock()` on
`$STATE/scan.lock` — held for the whole run, and refuse to start (with a clear
message naming the holder's PID and start time) if another holds it. Without
this, an impatient second `harelphotos scan` in another terminal, or a cron job
firing while you are running one by hand, gives two processes writing the same
derived files and the same SQLite rows. SQLite's own locking would prevent
corruption but not duplicated work, half-applied scan generations, or the
deletion pass in phase 4 removing files the other process just wrote.

`--force-unlock` exists for the case where a machine was hard-reset mid-scan.
The web application never takes this lock: it only reads, WAL mode lets it read
while a scan writes, and the whole point of the design is that a scan does not
take the site down.

### Expected timings

| Scenario | Time |
|---|---|
| First run, 80k photos, 12 cores | ~1.8 h (§2.2) |
| Rescan, nothing changed | seconds (pure `scandir` + SQLite) |
| Rescan after adding 500 photos | ~30 s |
| **Rescan after `jhead -ft` over the whole collection** | **~minutes** (phase 2 only), not 22 core-hours |

---

## 9. Derivative pipeline

### 9.1 The recipe (per photo, one decode)

**One decode, then a descending cascade** — each tier is resized from the tier
above it, not from the original:

```python
tiers = sorted(config.sizes.thumb + config.sizes.view, reverse=True)   # 2048,1280,512,256

im = Image.open(path)
im.draft("RGB", (tiers[0], tiers[0]))     # libjpeg shrink-on-load: the big win
im = ImageOps.exif_transpose(im)          # honour EXIF orientation
im = to_srgb(im)                          # ImageCms convert if an ICC profile is present
im = im.convert("RGB")

cur = im
for px in tiers:                          # descending
    cur = fit(cur, px)                    # LANCZOS, reducing_gap=2.0; never upscales
    out = cur.filter(UnsharpMask(0.6, 60, 3) if px > 512 else UnsharpMask(0.5, 50, 3))
    save_atomic(out, derived/f"{px}"/relpath + ".avif",
                "AVIF", quality=config.quality[px], speed=config.speed,
                subsampling="4:2:0")
```

Four details that matter, each verified by benchmark:

- **`draft()`** decodes the JPEG at 1/2, 1/4 or 1/8 scale inside libjpeg,
  cutting decode from ~450 ms to 93 ms.
- **Cascading** rather than resizing each tier from the full-size original.
  Going 2048→1280→512→256 makes the three smaller tiers nearly free (the whole
  four-tier resize cost is 400 ms, versus ~1.2 s if each came from the
  original), with no visible quality difference — every step is still a
  downscale through Lanczos.
- **UnsharpMask** after downscaling — downscaled photos always look soft; this
  is what makes the grid crisp rather than mushy. Gentler on the small tiers,
  where over-sharpening reads as noise and costs bytes.
- **Atomic writes** — encode to `NAME.tmp.<pid>`, then `os.replace()` (§8).

Derivatives carry **no EXIF** (smaller, and it avoids leaking GPS to anyone who
saves a thumbnail). Originals keep everything. This is not automatic: it is
precisely the trap that produced 28 KB thumbnails under vips (§3.3), so the test
suite asserts that derivatives contain no metadata.

Never upscale: a tier larger than the source is simply skipped, and the DB
records which tiers actually exist so `srcset` only advertises real files.

A photo smaller than the smallest tier therefore has **no derivatives at all**,
which the page must still be able to display. `/orig/…` cannot serve that need —
it sets `Content-Disposition: attachment`, so pointing an `<img>` at it would
offer a download instead of showing the picture. So the top tier degrades to an
*inline* original: the same bytes, served with the image's real content type and
no `Content-Disposition`, from `/i/orig/<path>`. It shares the ACL check and the
caching rules of the other image routes, and `/orig/…` stays the
deliberate-download route. Worth getting right early — small scanned images are
exactly the case, and it is the sort of thing that surfaces as a mysteriously
undisplayable photo months later.

### 9.2 Size ladder

Default **`thumb = [256, 512]`, `view = [1280, 2048]`** — chosen from the
measured table in §2.1, with the reasoning in §2.3. Globally configurable, not
per-directory (per-directory ladders would fragment the derived tree for no real
benefit). Changing the ladder changes `deriv_key`, so a rescan regenerates what
is needed and `harelphotos gc` removes tier directories that are no longer
configured.

Why these numbers, concretely:

- **256 / 512 for the grid.** Justified rows target ~180 px tall on a desktop
  and ~130 on a phone (§11.1c). At DPR 1 that needs ~180 px on the long edge
  (→ 256 tier); at DPR 2–3 it needs ~360–400 px (→ 512 tier, which for a 4:3
  photo is 512×384). Derivatives are **never cropped** — they keep the original
  aspect ratio, which is precisely what the justified-row layout consumes.
- **1280 / 2048 for the lightbox.** A phone at 400 CSS px × DPR 3 wants
  ~1200 px; a 1080p desktop wants ~1900. 2048 is a round number that covers
  1080p with headroom and a DPR-2 tablet fully. Beyond that, "download
  original" is the honest answer — and a `3200` tier can be added by editing one
  config line if retina laptops turn out to bother you (§2.1 has its cost).

### 9.3 EXIF extraction

Pillow's `Image.getexif()` and `get_ifd(IFD.Exif)` / `get_ifd(IFD.GPSInfo)` —
no external `exiftool` process per photo. Extract and store as JSON:
`DateTimeOriginal` (→ the `taken` column), camera make/model, lens, exposure
time, f-number, ISO, focal length (and 35 mm equivalent), and GPS lat/lon.
Everything is best-effort — malformed EXIF is extremely common and must never
raise.

Dominant colour for the placeholder: `im.resize((1,1))` on the thumb — free,
and it makes the grid look intentional while images stream in. (Considered
blurhash; a solid colour behind `loading="lazy"` images is 95% as good for none
of the dependency.)

### 9.4 Why AVIF, and what non-AVIF browsers get

AVIF is much less common on the web than WebP, and that's a fair reason to
question it. Two separate questions hide inside "is AVIF a good choice?" — what
it costs, and who can read it.

#### What it costs: measured against WebP and JPEG

Same six photos, same four tiers. To compare fairly, each tier was encoded to
AVIF at our chosen quality, then WebP was searched at **1-point quality
increments** for the lowest setting whose SSIM (against the same reference
image) met or beat AVIF's. So WebP here is at *equal or slightly better*
measured quality, not merely at a similar-sounding quality number:

| tier | AVIF | SSIM | WebP (matched) | SSIM | WebP vs AVIF | JPEG q82 |
|---:|---:|---:|---:|---:|---:|---:|
| 2048 | 127.0 KB | 0.9469 | 192.7 KB (q60) | 0.9519 | **+52%** | 434.7 KB |
| 1280 | 59.5 KB | 0.9391 | 87.5 KB (q52) | 0.9466 | **+47%** | 196.9 KB |
| 512 | 12.8 KB | 0.9358 | 17.0 KB (q48) | 0.9418 | **+33%** | 38.4 KB |
| 256 | 5.2 KB | 0.9500 | 5.8 KB (q52) | 0.9506 | **+11%** | 11.8 KB |
| **per photo** | **205 KB** | | **303 KB** | | **+48%** | 682 KB |
| **80k photos** | **16.8 GB** | | **24.8 GB** | | **+8 GB** | 55.9 GB |

So the choice is worth about **8 GB of disk** and ~48% of the bandwidth on every
photo viewed, on a server where the PLAN says space is tight and CPU is weak.
The advantage grows with image size, which is exactly backwards from what you'd
want if you were hoping to dismiss it: it's largest on the lightbox images that
dominate bandwidth, and smallest on the 256 px thumbnails where it barely
matters.

(Caveat on method: SSIM is a decent relative metric, not a perceptual verdict,
and it treats codecs somewhat differently. Treat "+48%" as solid to within a few
points, not to the decimal.)

#### Who can read it

- **Chrome 85+** (Aug 2020), **Firefox 93+** (Oct 2021), **Safari 16.4+**
  (Mar 2023), and the Android and iOS browsers of those vintages.
- The real gap is **iOS before 16.4** — which in practice means iPhones and
  iPads that cannot upgrade past iOS 15, i.e. 2015–2016 hardware such as the
  iPhone 6s/7 and older iPads. For a *family* site this is not a hypothetical:
  it is exactly the device an older relative still uses.

On adoption: WebP is far more widely deployed than AVIF, and Google — which
created WebP — uses it heavily across its products. I can't verify from here
what Google Photos serves to a given browser, and I'd rather say that than
guess; if it matters to you, open a photo there and check the response's
`Content-Type`. AVIF adoption is real but narrower (Netflix, Cloudflare's image
resizing, Next.js/Vercel all support it).

#### Why the adoption argument doesn't decide this

**AVIF *is* our default** — it is the only format we pre-generate, and on modern
family hardware it is the only one that will ever be served. The fallback below
is not a hedge that costs something; it is a safety net that costs nothing when
unused.

The mechanism is ordinary HTTP content negotiation, which is worth spelling out
because it's easy to have never noticed. Whenever a browser fetches an `<img>`,
it *already* tells the server which image formats it can decode, in the `Accept`
request header. It has always done this; AVIF was simply added to the list as
browsers gained support. Verified against MDN's documentation, the default a
browser sends for an image subresource looks like:

```
Accept: image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5
```

Nothing needs enabling and no JavaScript is involved — the header arrives on
every image request. Reading it is one line:

```python
avif_ok = "image/avif" in request.headers.get("Accept", "")
```

This is the same mechanism CDNs use to serve AVIF or WebP from a single URL.

**Why not simply serve AVIF to everyone and skip it?** That is very nearly what
happens — but "everyone has modern hardware" is a statement about today's
devices, and the failure mode if it's ever wrong is bad out of proportion to the
five lines saved: a relative sees an empty grey page, with no error message and
no obvious way to tell you what went wrong. They just stop using the site. The
cases are real: an iPad stuck on iOS 15, a new family member with an old phone,
a work laptop with a locked-down browser. Against that, when everyone *is*
modern, the fallback code never runs and not a single WebP or JPEG file is
created.

So: one negotiated ladder, best first:

1. `Accept` contains `image/avif` → serve the pre-generated AVIF. This is
   every current browser, and will be ~all of your traffic.
2. Else `image/webp` → serve WebP. This covers Safari 14–16.3 and older
   Chrome/Firefox — the band that lacks AVIF but is far from ancient.
3. Else → JPEG. Universal, and by then we're talking about genuinely old
   software.

Tiers 2 and 3 are **transcoded on demand from the AVIF and cached** under
`$DERIVED_ROOT/{webp,jpeg}/<tier>/…`, so they cost nothing until some device
actually asks. The first such request is slow; every one after it is a static
file. A rate limit caps the damage from a crawler.

`harelphotos scan --fallback webp` pre-generates the whole WebP tree if you'd
rather spend the disk than have anyone wait — worth doing if you know a family
member is on an old iPad.

#### If you'd rather not

Two escape hatches, both one line.

To drop the negotiation entirely and serve AVIF unconditionally, set
`[encode] fallback = "none"`. The `/i/…` routes then always return AVIF and the
`Vary: Accept` requirement below disappears with it. Simplest possible
behaviour; the cost is that a device without AVIF gets broken images rather than
slower ones.

To change the format everyone gets, set `[encode] format = "webp"`: WebP becomes
the primary pre-generated format, with JPEG as the only fallback. You pay the
8 GB and gain the reassurance of a format everyone has shipped for a decade. The
pipeline, the tier ladder, the `srcset` markup and the negotiation logic are all
format-agnostic, so nothing else changes — and `recipe_version` (§7) means
switching later just triggers a rescan rather than a rebuild of anything by
hand.

My recommendation stays AVIF, on the grounds that the fallback makes the
downside a non-event while the upside is 8 GB and half the bandwidth. But it's
a reversible decision either way, which is the main thing.

(Decode speed is the other fair objection to AVIF, and it gets its own
measurements in §9.6.)

### 9.5 Place names from coordinates

Turning `37.1036, 25.3766` into "Naxos, Greece" is much nicer than a coordinate
pair. This was originally deferred to M9 on the assumption it was expensive;
prototyping showed otherwise, so **the city layer moves up to M3**.

**Correcting an earlier claim in this document: geocoding does *not* require a
rescan.** It never opens a photo file. The GPS coordinates are already in the
database after the header-read pass (§8, phase 2), so geocoding is a pure
database operation — read coordinates, resolve names, write `photos.place`.
Running it later costs seconds, not a re-read of 300 GB. It is exposed both as
part of `scan` and as a standalone `harelphotos geocode` for re-running after a
dataset update.

**It must happen at scan time, not request time.** A page view must never make
an outbound network call: the server is weak, the call would block a request,
and an unreachable geocoder would break the info panel.

**Do it offline, from a local dataset.** The obvious approach is an online
reverse-geocoder such as Nominatim, and it's the wrong one here:

- Its usage policy caps bulk work at ~1 request/second, so 80,000 photos would
  take about a day and would abuse a volunteer-run service.
- More importantly, it means **transmitting the GPS coordinates of every photo
  your family has ever taken to a third party** — including your house. For a
  site whose entire premise is that these photos are private, that's the wrong
  default, and it would happen silently at scan time rather than on a
  deliberate click.

#### Layer 1 — cities (M3, the reliable backbone)

**GeoNames `cities500`**: every populated place worldwide with 500+
inhabitants. Measured end to end with a working prototype:

| | |
|---|---|
| Download | **13.6 MB** zipped, one-off (`harelphotos init --geonames`) |
| Contents | **235,694 places**, worldwide |
| Build | parse 0.6 s + index 0.3 s → a **16 MB** SQLite table |
| Lookup | **0.16 ms** per distinct coordinate |
| **Whole collection** | **0.7 s for 80,000 photos** (≈4,000 distinct locations after rounding to ~100 m and memoising) |
| Dependencies | **none** — stdlib `sqlite3` and `math`, ~80 lines |

Spot-checked worldwide: Náxos 0.2 km, Tel Aviv 0.5 km, Reykjavík 2.6 km,
Lobuche (Everest) 12.1 km. You were right that this is simple; it is also fast
enough to be unconditional.

The honest limitation is that nearest-populated-place is approximate — a photo
on a mountainside gets the nearest village, possibly 20 km away — so render the
distance when it's large ("near Filoti, Greece (18 km)") rather than asserting
precision we don't have. Note also that the longitude box does not wrap at
±180°, which costs nothing outside the Pacific.

#### Layer 2 — landmarks (M9, opt-in, and genuinely imperfect)

You asked for famous places that aren't cities — Disney World, the Grand
Canyon. GeoNames does carry these, in the full `allCountries` dump, under
feature codes like `AMUS` (amusement park), `PRK` (park), `MNMT` (monument),
`MT`, `CNYN`, `VLC`, `HSTS`. I tested it worldwide rather than guessing.

Filtering 13,464,089 worldwide rows to a curated allowlist of ~30 landmark codes
keeps **1,342,811 rows** (10%) — excluding the enormous noise categories
(streams, churches, schools, buildings, hotels, wells) that would otherwise
label a family photo "Saint Mary Church".

What a *coordinate* lookup actually returns, tested at ten famous sites:

| site | nearest curated landmark | |
|---|---|---|
| Eiffel Tower | **Tour Eiffel** (0.01 km) | ✅ |
| Mount Fuji | **Fujiyama** (0.03 km) | ✅ |
| Machu Picchu | **Ruinas Machupicchu** (0.10 km) | ✅ |
| Colosseum | **Colosse** (0.12 km) | ✅ |
| Uluru | **Uluru National Park** (0.15 km) | ✅ |
| Masada | **Har Metsada** (0.24 km) | ✅ |
| Walt Disney World | **Walt Disney World Resort** (2.03 km) | ✅ |
| Taj Mahal | Anguri Bagh (1.92 km) — a garden inside the complex | ⚠️ |
| Grand Canyon south rim | Grandeur Point (1.88 km) — a viewpoint | ⚠️ |
| Western Wall | Muristan (0.44 km) — a different quarter | ❌ |

So: **roughly seven in ten give a recognisable answer**, and names come in the
local language or transliteration ("Tour Eiffel", "Har Metsada", "Fujiyama").
The failure mode is that GeoNames has no notion of significance, so in dense
historic areas the *nearest* feature is some minor sub-feature rather than the
famous thing you meant. I tried using the count of alternate-language names as a
fame proxy and it doesn't work — "Walt Disney World" scores 3 while "Mud Lake"
scores 18.

Consequences for the design:

- **Opt-in**, via `harelphotos init --landmarks`: a 421 MB download filtered
  down to a ~100 MB table. Too big and too approximate to be the default.
- **Always show the city as well**, never the landmark alone — "Tour Eiffel ·
  Paris, France". A wrong landmark then reads as a curiosity rather than the
  entire answer.
- **Only label within a tight, code-specific radius** (a few hundred metres for
  monuments and museums, a few km for parks and resorts), so a photo gets a
  landmark only when genuinely at one. This also disposes of name ambiguity
  without any cleverness: there are twelve "Grand Canyon"s in the US alone, but
  only one is within 5 km of you.

#### Implementation spec

Everything needed to build this without rediscovering it.

**Downloads** (all from `https://download.geonames.org/export/dump/`, licensed
CC BY 4.0 — attribute GeoNames in the info panel or the README):

| file | size | when | purpose |
|---|---:|---|---|
| `cities500.zip` | 13.6 MB | `init --geonames` | 235,694 populated places ≥500 people |
| `countryInfo.txt` | 31 KB | `init --geonames` | country code → country name |
| `admin1CodesASCII.txt` | 152 KB | `init --geonames` | region code → region name ("US.FL" → Florida) |
| `allCountries.zip` | 421 MB | `init --landmarks` | 13.46 M rows, filtered to landmarks |
| `featureCodes_en.txt` | 59 KB | reference only | human-readable feature-code meanings |

**File format.** Both dumps are UTF-8, tab-separated, no header, 19 columns.
The ones we use, 0-indexed:

```
 1  name              display name (often the LOCAL language: "Tour Eiffel")
 4  latitude          float
 5  longitude         float
 6  feature class     one letter: A admin, H water, L area, P populated, S spot, T terrain
 7  feature code      e.g. PPL, AMUS, MNMT, CNYN
 8  country code      ISO-3166 alpha-2
10  admin1 code       joins to admin1CodesASCII as "<cc>.<admin1>"
14  population        integer, often 0 outside class P
```

Rows with a malformed lat/lon are skipped rather than fatal — the dump has some.

**Landmark feature codes.** The curated allowlist actually used in the
measurements above; everything else in `allCountries` is discarded. The
exclusions matter as much as the inclusions — `STM` streams, `CH` churches,
`SCH` schools, `BLDG`, `HTL`, `PO` and `WLL` alone are over a million rows of
noise that would label a family photo "Saint Mary Church":

```python
LANDMARK_CODES = {
  # built                                                       radius 800 m
  'MNMT','MUS','CSTL','ANS','HSTS','PAL','RUIN','PYR','TOWR','BDG','OBS','ZOO','THTR',
  # natural points                                              radius 1.5 km
  'MT','PK','VLC','FLLS','CAPE',
  # areas                                                       radius 5 km
  'PRK','AMUS','RESN','RESV','ISL','LK','LGN','BCH','GLCR','FRST','CNYN','DSRT','PLAT',
}
```

**Local storage** — `$STATE/geonames.sqlite`, separate from `index.sqlite` so
that deleting the rebuildable index doesn't force a 421 MB re-download:

```sql
CREATE TABLE places   (name TEXT, cc TEXT, admin1 TEXT, lat REAL, lon REAL, pop INTEGER);
CREATE TABLE landmarks(name TEXT, cc TEXT, code TEXT,   lat REAL, lon REAL);
CREATE INDEX places_ll    ON places(lat, lon);
CREATE INDEX landmarks_ll ON landmarks(lat, lon);
CREATE TABLE countries(cc TEXT PRIMARY KEY, name TEXT);
CREATE TABLE admin1(key TEXT PRIMARY KEY, name TEXT);   -- key = "US.FL"
```

**Nearest-neighbour lookup.** No k-d tree and no numpy: an indexed bounding-box
query with geometric expansion, then exact great-circle distance over the
handful of candidates. This is the prototype that measured 0.16 ms:

```python
def nearest(db, table, lat, lon):
    for d in (0.15, 0.6, 2.5, 10.0):              # degrees of latitude
        dlon = d / max(0.05, cos(radians(lat)))   # widen with latitude
        rows = db.execute(
            f"SELECT * FROM {table} WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat-d, lat+d, lon-dlon, lon+dlon)).fetchall()
        if rows:
            r = min(rows, key=lambda r: haversine(lat, lon, r.lat, r.lon))
            return r, haversine(lat, lon, r.lat, r.lon)
    return None, None                              # mid-ocean; leave place NULL
```

Two known corners, both cheap to live with: the longitude box does not wrap at
±180°, and the expansion returns the nearest row *inside* the first non-empty
box, which can differ from the true nearest when a closer point sits just
outside it. Neither is reachable in practice for photographs of people.

**Memoisation** is what makes the pass sub-second: key the cache on
`(round(lat, 3), round(lon, 3))` — about 100 m — which collapsed 80,000 photos
to ~4,000 real lookups in the prototype.

**Resolution order**, evaluated per photo, first hit wins:

1. **Landmark**, if the landmarks table is installed and one lies within its
   code's radius (800 m / 1.5 km / 5 km per the groups above). Rendered as a
   qualifier in front of the city, never alone.
2. **City** from `places`, always resolved when the photo has GPS. Stored with
   `place_dist`; rendered as `"{name}, {country}"`, or `"near {name}, {country}"`
   when `place_dist > 5 km`.
3. **Album `location`** from `.album.toml`, inherited down the tree — the only
   one of these that works for photos with no GPS at all.
4. **Raw coordinates**, formatted to 5 decimal places.

Worked examples of the resulting strings:

```
  Tour Eiffel · Paris, France          landmark 0.01 km + city
  Walt Disney World Resort · Bay Lake, Florida, United States
  near Filoti, Greece (18 km)          city only, far
  Náxos, Greece                        city only, close
  Walt Disney World                    album location; photo had no GPS
  31.77667, 35.23417                   GPS present, nothing resolved
```

Honest note on the thresholds: they bound the *damage*, not the error rate. The
Western Wall miss in the table above ("Muristan", an `HSTS` at 0.44 km) sits
comfortably inside the 800 m built-feature radius and would still be shown. That
is the seven-in-ten, and it is why the city is always printed alongside and why
`.album.toml` exists.

#### The better answer for trip albums

For your actual examples, `.album.toml`'s `location` key beats any geocoder and
ships in **M2**:

```toml
location = "Walt Disney World"
```

Typed once for the album, it is exactly right, in your words and your language,
for every photo in it — no 421 MB download, no seven-in-ten. Use the geocoder
for the long tail of photos nobody will ever label by hand.

**And most of your collection has no GPS at all.** Scans, film and pre-2010
cameras record nothing, and your "ancient" directory is precisely that case;
coordinates cannot help there at any accuracy. The same `location` key covers
it:

```toml
location = "Naxos, Greece"     # applies to this album and its subdirectories
```

Precedence: a confident landmark match, then the photo's own resolved city, then
the album `location`, then raw coordinates. The album location is inherited down
the tree, and is what makes the info panel useful for the pre-GPS half of the
collection.

### 9.6 Decode cost on the client

AVIF is genuinely more expensive to *decode* than JPEG or WebP, so "100
thumbnails must appear instantly" is the right thing to worry about. Measured
per image, decoding the same pictures from each format:

| tier | format | KB | wall | **CPU** | cores used |
|---:|---|---:|---:|---:|---:|
| 256 | AVIF | 5.2 | 1.05 ms | **1.43 ms** | 1.37 |
| 256 | WebP | 5.9 | 0.62 ms | **0.61 ms** | 1.00 |
| 256 | JPEG | 11.8 | 0.57 ms | **0.56 ms** | 0.99 |
| 512 | AVIF | 12.8 | 2.37 ms | **3.12 ms** | 1.32 |
| 512 | WebP | 17.3 | 1.82 ms | **1.80 ms** | 1.00 |
| 512 | JPEG | 38.4 | 1.78 ms | **1.77 ms** | 1.00 |

So yes: **AVIF costs about 1.7–2.3× the CPU of WebP or JPEG to decode.** The
"cores used" column is why CPU time is the honest measure here — the AVIF
decoder is multithreaded, so wall-clock flatters it. In a grid the browser is
already decoding many images in parallel across all cores, so the extra threads
have nowhere to go and CPU time is what you actually pay.

#### Does it matter in practice? No, for three reasons

**1. The absolute numbers are small.** 100 thumbnails at the 512 tier is 312 ms
of CPU for AVIF versus 180 ms for WebP — a 132 ms difference, spread across
cores, on work the browser does off the main thread (`decoding="async"`), so it
never blocks scrolling.

**2. We never decode 100 at once.** `loading="lazy"` plus `content-visibility:
auto` on each row (§11.1c) means only the images actually on screen are
fetched and decoded — 15–25, not 100. That cuts the real difference to roughly
**30 ms of CPU spread over several cores**, which is imperceptible.

**3. Network dominates, and there the smaller file wins.** For those same 100
thumbnails at the 512 tier, AVIF sends 1.25 MB against WebP's 1.69 MB:

| link | AVIF transfer | WebP transfer | AVIF's net advantage |
|---|---:|---:|---|
| 10 Mbit/s | 1048 ms | 1421 ms | **AVIF ~340 ms ahead** |
| 50 Mbit/s | 210 ms | 284 ms | **AVIF ~40 ms ahead** |
| 100+ Mbit/s | 105 ms | 142 ms | roughly a wash |

Break-even is around **110 Mbit/s** of *end-to-end* throughput on a 4-core
client. And the ceiling here is your server's **upload** bandwidth on a
residential connection, not the visitor's download speed — so in practice this
site will live permanently on the side of the table where the smaller file wins.

#### The asymmetry worth knowing

The multithreading that doesn't help a grid *does* help the single-photo view,
where one large image is decoded alone and the spare cores are free. At the 2048
tier: AVIF **10.8 ms** wall versus WebP **22.6 ms** and JPEG **21.8 ms** — AVIF
is about twice as fast in wall-clock terms, on top of arriving in half the
bytes. So the format is at its worst exactly where it matters least (many tiny
thumbnails, where every format is sub-millisecond) and at its best exactly where
it matters most (swiping through full-screen photos, §11.2).

#### Caveat on method

These are libavif, libwebp and libjpeg-turbo measured through Pillow, not the
browsers' own decoders — Chrome and Firefox decode AVIF with dav1d, Safari uses
its own. The relative magnitudes should carry over; the exact milliseconds will
not. If the grid ever does feel sluggish on a specific old phone, the cheap
first move is dropping that device to the 256 tier rather than changing format,
and the config-level escape hatch (`format = "webp"`, §9.4) remains one line.

---

## 10. Web application

Flask, one module of routes, Jinja templates, no blueprints (the app is too
small to earn them).

### 10.1 URL structure

Directly satisfies "URLs that point to the top album and to individual
subdirectories" — the URL *is* the directory path.

| URL | Purpose |
|---|---|
| `/` | **public** — landing page when logged out (§11.5); redirect to `/a/` when logged in |
| `/public/landing-640.avif` etc. | **public** — the landing hero image, fixed literal names, no photo data |
| `/a/` | root album |
| `/a/2019/summer/` | that directory's album page |
| `/p/2019/summer/IMG_1234.jpg` | single-photo page (shareable, works on reload) |
| `/i/256/2019/summer/IMG_1234.jpg?v=KEY` | one derivative tier; the tier must be one that's configured |
| `/orig/2019/summer/IMG_1234.jpg` | original download (`Content-Disposition: attachment`) |
| `/api/a/2019/summer/?after=<id>` | JSON, for the >5000-photo safety valve (§10.3) and lightbox prefetch |
| `/login` | local login form (GET) and submission (POST) |
| `/logout` | **POST only**, CSRF-protected — never a GET link (§11.3) |
| `/privacy`, `/terms` | **public** (no login) — required by Google's consent screen (§12.2); static text, no photo data |
| `/auth/google/start`, `/auth/google/callback` | OIDC |
| `/static/…` | CSS/JS, served by Apache directly |

The lightbox uses `history.pushState` to swap the address bar to `/p/…` as you
navigate, so copying the URL or hitting reload lands on the same photo. That's
the behaviour that makes Google Photos feel right.

### 10.2 Path resolution — and why traversal is impossible

Request paths are **never** concatenated onto `photo_root`. The path string is
looked up verbatim in `dirs.path` / `photos.name`; a miss is a 404. Filesystem
paths are constructed only from values that came *out* of the database, which
were produced by `os.scandir`. `..`, symlink games, and NUL bytes therefore
cannot reach the filesystem layer at all — the class of bug is designed out
rather than filtered against. (Belt and braces: reject any path segment equal to
`.`/`..` or containing `/` or NUL at the routing layer as well.)

### 10.3 Album page rendering

One query for subdirectories (with their cover photos), one for photos. Both are
covered by indexes.

Every `<img>` gets explicit `width`/`height` (from the DB) so the layout never
shifts, `loading="lazy"`, `decoding="async"`, and the dominant colour as the
background.

#### Large albums: send the whole page

A 1000–2000 photo trip directory is the case to get right, and you're correct
that classic paging ("page 3 of 14") is a bad answer. But the *right* answer
turns out to be simpler than infinite scroll, because the markup is tiny once
compressed. Measured on the real album template:

| photos | raw HTML | gzip | brotli | gzip bytes/photo |
|---:|---:|---:|---:|---:|
| 500 | 182 KB | **6.4 KB** | 2.8 KB | 13 |
| 1000 | 361 KB | **12.0 KB** | 4.7 KB | 12 |
| 2000 | 719 KB | **23.2 KB** | 8.8 KB | 12 |
| 5000 | 1794 KB | **56.5 KB** | 24.1 KB | 12 |

Album markup is intensely repetitive, so it compresses about **31×**. The entire
HTML for a 2000-photo album costs **23 KB — about the same as two thumbnails.**
Chunking that over the network would be optimising the one part of the page that
is already free.

**And you already get the experience you're describing.** The "transparent
scrolling" you want — content appearing seamlessly as you scroll, as if it had
always been there — comes from `loading="lazy"` on the images, not from chunking
the HTML. The browser fetches each thumbnail as it approaches the viewport,
natively, with no JavaScript. Sending complete markup up front doesn't weaken
that; it makes it better, because there are no chunk boundaries to stutter at.

What sending everything additionally buys, all of which infinite scroll breaks:

- **An honest scrollbar.** Its size tells you how big the album is, and it
  doesn't shrink as you scroll — the single most common infinite-scroll
  annoyance.
- **The back button works.** Open a photo, press Back, and the browser restores
  your exact scroll position by itself. With appended content you must
  reimplement that, and it is fiddly.
- **Ctrl-F finds every photo**, and so does the browser's "find on page" on
  mobile.
- **The footer is reachable** — including the logout link (§11.3). A footer
  below infinitely-appending content can never be reached, which is a
  well-known and genuinely irritating failure.
- **No JavaScript required** for the page to be complete and navigable.

Rendering cost is bounded by `content-visibility: auto` on each row (§11.1c):
the browser skips layout and paint for off-screen rows entirely, so 2000 photos
of DOM cost about what the visible screenful costs. Server-side, 2000 photos
render in ~14 ms (§1).

**This makes `mod_deflate` a requirement, not a nicety** — without compression
that 23 KB is 719 KB. It's enabled by default on both Fedora and Rocky, and
§13.2 asserts it explicitly.

#### The safety valve

Beyond `album_page_size` (default **5000**) the page switches to appending
further batches from `/api/a/…?after=<id>` as you scroll. At that size the
concern is no longer bytes but DOM node count. This exists so a pathological
directory degrades gracefully rather than well; it is not the normal path, and
if you have no directory that large it will never run.

**Responsive images.** The tiers of §9.2 are delivered by plain `srcset`/`sizes`
with `w` descriptors — no JavaScript, the browser picks by viewport *and* device
pixel ratio:

```html
<img src="/i/256/2019/summer/IMG_1234.jpg?v=a1b2c3"
     srcset="/i/256/2019/summer/IMG_1234.jpg?v=a1b2c3 256w,
             /i/512/2019/summer/IMG_1234.jpg?v=a1b2c3 512w"
     sizes="(max-width: 600px) 33vw, 180px"
     width="256" height="192" loading="lazy" decoding="async"
     style="background:#4a5f7a" alt="">
```

Two things the design must get right for this to work:

- The `w` descriptor is the image's **intrinsic width**, which differs between
  landscape and portrait (a portrait photo in the 512 tier is 384×512). The
  templates emit the real per-photo, per-tier width from the DB rather than the
  tier number, otherwise the browser's DPR arithmetic is wrong for half the
  collection.
- Only tiers that actually exist on disk are listed — §9.1 skips tiers larger
  than the source, and advertising a missing file would produce broken images.

The lightbox uses the same mechanism with `sizes="100vw"` over the `view` tiers,
so a phone pulls 1280 and a desktop pulls 2048 without any device sniffing.

Caching: album pages get `Cache-Control: private, no-cache` + an `ETag` derived
from the directory's max child mtime and the viewing user, so a reload is a
cheap 304. Image URLs carry `?v=<deriv_key hash>` and get
`Cache-Control: private, max-age=31536000, immutable` — the browser fetches each
thumbnail exactly once, ever, and a re-encoded photo gets a new URL. This is
what makes the "very speedy UI" of the PLAN actually feel instant on a revisit.

**`Vary: Accept` is mandatory on every image response** whenever
`[encode] fallback` is not `"none"`. One URL —
`/i/256/…/IMG_1234.jpg` — deliberately returns AVIF, WebP or JPEG depending on
the request's `Accept` header (§9.4). Combined with a year-long `immutable`
cache lifetime, omitting `Vary` means any cache that saw one format can hand it
to a client that cannot read it, and the failure is both silent and extremely
long-lived. `private` keeps shared proxies out of it, but the browser's own
cache is enough to cause this on a device whose `Accept` changes after a browser
upgrade. This is a one-line header and a nasty bug if forgotten, so it gets an
explicit test.

### 10.4 Serving image bytes

Two modes, auto-detected at startup:

1. **`mod_xsendfile` present** (`sendfile_header = "X-Sendfile"` in config): the
   Flask handler does the ACL check and returns an empty body with
   `X-Sendfile: /abs/path`. Apache serves the file with `sendfile(2)` — zero
   Python CPU, zero Python memory per image. Best for the weak server.
2. **Not present** (default): Flask `send_file()`. For 7 KB thumbnails and a
   handful of family members this is entirely adequate; it only becomes
   interesting when someone downloads a 6 MB original.

Auto-detection keeps `mod_xsendfile` — which needs EPEL on Rocky 9 — strictly
optional, which is why it isn't in the dependency list in §3.

---

## 11. Frontend

Plain HTML + one CSS file + one vanilla JS file (~400 lines). No build step, no
framework: this is the part most likely to need understanding in five years.

### 11.1 Album view

An album page has **two distinct sections**, because a directory routinely holds
both subdirectories and loose photos, and they are different kinds of thing:

```
  ┌─ breadcrumb: Home / 2012 ──────────────────  [user ▾] ─┐

     Albums                                    (§11.1a)
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │  cover   │ │  cover   │ │  cover   │      uniform cards,
     │  photo   │ │  photo   │ │  photo   │      caption *below*
     └──────────┘ └──────────┘ └──────────┘      the image
      January      February     Passover
      214 photos   88 photos    340 photos

     Photos                                     (§11.1c)
     ┌────────┐ ┌─────┐ ┌───────────┐ ┌──────┐
     │        │ │     │ │           │ │      │   justified rows,
     └────────┘ └─────┘ └───────────┘ └──────┘   true aspect ratios,
     ┌─────┐ ┌──────────┐ ┌────┐ ┌─────────┐     never cropped
     └─────┘ └──────────┘ └────┘ └─────────┘
```

- Sticky header: breadcrumb of ancestor albums, album title, recursive photo
  count and date range, and the user menu (§11.3).
- **Subdirectories always come first**, in their own section with its own
  heading, then the loose photos. Section headings are omitted when a directory
  has only one kind — a leaf album shows no "Photos" heading, and a pure
  container directory shows no empty "Photos" section.
- The two grids are deliberately different: labelled uniform cards for albums,
  unlabelled justified rows for photos. Mixing them into one grid is what makes
  most gallery software confusing to navigate — and keeping them apart is also
  what lets album captions sit *below* their covers instead of being overlaid
  on them (§11.1a).

### 11.1a Subdirectory cards

This is the main structural departure from Google Photos, whose grid is a flat
timeline. Our primary organisation is your directory hierarchy, so a
subdirectory has to be *identifiable*, not merely pretty: in a top-level listing
of years, seeing twelve attractive cover photos with no labels tells you
nothing.

Each card is one link, stacking:

- the directory's **cover photo** (§5.3), from the 512 tier;
- below it, in the page background rather than on the image, the **title** —
  `title` from `.album.toml`, else the directory name with underscores turned
  into spaces;
- under that, in smaller, dimmer text, the **recursive photo count** and, where
  meaningful, the date span.

**The caption sits below the image, not overlaid on it.** An overlay needs a
dark scrim and a text-shadow to stay readable, because white text vanishes on a
snow photo — and even then it dims the cover it's sitting on. Since albums
already occupy their own section (§11.1), nothing about the layout requires the
label to be *inside* the tile, so putting it underneath is strictly better:
always legible whatever the cover looks like, no scrim darkening the photo, room
for a second line of detail, and it reads instantly as "these are folders" —
the familiar file-manager idiom.

To keep rows aligned, the caption block is a **fixed height**: the title is
clamped to two lines with an ellipsis (`-webkit-line-clamp: 2`) and carries a
`title` attribute with the full text, so one long album name can't shove its
neighbours out of alignment. Cards lift slightly on hover/focus and carry a
visible focus ring.

**Cover images are a uniform shape, and here we do crop** — `aspect-ratio: 4/3`
with `object-fit: cover`. A deliberate exception to §11.1c's no-cropping rule,
because the two cases genuinely differ: a photo is the content, so cropping it
destroys the thing you came to see; a cover is *packaging* for a directory. With
captions underneath, uniformity matters more than before — native aspect ratios
would leave every caption at a different height and the grid would look broken.
`[ui] dir_card_aspect = "native"` opts out if you'd rather see whole covers.

An album with no photos anywhere beneath it gets a neutral folder placeholder
rather than a broken image.

Layout is a plain CSS grid — `repeat(auto-fill, minmax(220px, 1fr))` — with no
JavaScript at all. The justified-row machinery is unnecessary here precisely
because the cards are uniform.

### 11.1b Ordering subdirectories

Default: **by name, naturally sorted**. Natural means digit runs compare
numerically, so `Day 2` precedes `Day 10`, and `2009` precedes `2010` — plain
lexicographic sorting gets this wrong in exactly the directory-naming patterns
people actually use.

Set `dirsort` in a directory's `.album.toml` to control how *its children* are
ordered:

| `dirsort` | meaning |
|---|---|
| `"name"` | natural sort by name — **default** |
| `"-name"` | reverse, e.g. newest year first at the top level |
| `"date"` | by the album's own date span (`date_max`), so albums sort chronologically regardless of naming |
| `"-date"` | most recent album first |

Two independent override mechanisms, because they suit different situations:

**1. The parent pins an explicit order.** Best for a handful of albums you want
in a specific sequence:

```toml
# in 2012/.album.toml
dirsort = "name"
order   = ["passover", "summer-trip", "chanukah"]   # these first, in this order
```

Anything not listed follows, in `dirsort` order. Unknown names in `order` are
ignored (with a warning from `harelphotos check`) so deleting a directory never
breaks its parent.

**2. The child declares its own sort key.** Best when you'd rather not edit the
parent every time you add a directory — and it fits the PLAN's principle that a
directory's metadata lives *in* that directory:

```toml
# in 2012/ancient-scans/.album.toml
title    = "Scans from the 1970s"
sort_key = "1975"        # sorts as if named "1975", wherever it actually sits
```

When set, `sort_key` substitutes for the directory name in the comparison. This
is the clean answer to a directory whose name can't be made to sort correctly —
`ancient` in a list of years, for instance — without renaming it or prefixing
numbers onto directory names.

Precedence is: explicit parent `order` first, then everything else by `dirsort`,
using `sort_key` where present and the natural-sorted name otherwise. Ties break
on the real directory name so the result is always deterministic.

`[ui] dirsort` in the global config sets the default for directories that don't
specify one — worth setting to `"-name"` if you want the newest year at the top
of the front page.

### 11.1c The grid: justified rows, no cropping

**Decided: justified rows from the start**, not square crops. This moves up from
the polish phase to M4, because retrofitting a layout is more work than building
the right one.

**What Google Photos actually does.** Its main grid is a *justified row* layout,
the same family as Flickr's and 500px's: photos keep their true aspect ratio and
are never cropped; each row is filled left-to-right and every photo in that row
is scaled to one common height; that height is chosen so the row exactly fills
the container width. Rows therefore differ slightly in height from one another
(typically ~180–220 px on a desktop), which is what produces the characteristic
even-margins, ragged-height look. Gaps are small — around 4 px. (Google Photos
*also* has a zoomed-out month/year view that does use square crops, and a
date-grouped timeline. We're taking the row layout, not those.)

**The algorithm** — greedy row filling, O(n), and we can run it because the
scanner already stores every photo's exact dimensions (§7):

```js
// aspects[] = width/height for each photo, in sort order
const GAP = 4, target = targetRowHeight(containerWidth);  // 180 desktop, 130 phone
let row = [], sumAspect = 0;
for (const a of aspects) {
    row.push(a); sumAspect += clamp(a, 0.4, 3.0);   // clamp panoramas & tall portraits
    const h = (containerWidth - GAP * (row.length - 1)) / sumAspect;
    if (h <= target) { emitRow(row, h); row = []; sumAspect = 0; }
}
if (row.length) emitRow(row, Math.min(target, heightThatFills(row)));  // last row: never stretch
```

Four details that separate a good implementation from a bad one:

- **The last row is not stretched to fill.** Otherwise a single leftover photo
  becomes absurdly large. Render it at the target height, left-aligned.
- **Clamp extreme aspect ratios** when accumulating. One 3:1 panorama otherwise
  drags its whole row down to a sliver.
- **Layout is pure arithmetic, not DOM measurement.** Aspect ratios come from
  `data-` attributes the server emits, so the whole layout is computed in one
  pass with zero forced reflows — this is what keeps a 3000-photo album fast.
- **Emit each row as a `<div>` with `content-visibility: auto` and
  `contain-intrinsic-size: <rowHeight>px`.** The browser then skips rendering
  off-screen rows entirely — native virtualisation, no custom virtual scroller,
  and it drops out for free from laying out in rows rather than absolutely
  positioning every tile.

On resize, recompute on `requestAnimationFrame` (debounced); it is pure maths
over an array, so it is cheap even for large albums.

**No-JS fallback**, and the first-paint markup before JS upgrades it — the
well-known flexbox approximation, which yields the same look to within a few
percent:

```css
.grid       { display: flex; flex-wrap: wrap; gap: 4px; }
.grid > a   { flex: 1 1 calc(var(--ar) * 180px); }  /* --ar set per photo */
.grid > a img { width: 100%; height: 100%; object-fit: cover; display: block; }
```

**Date headers.** `group_by = "day" | "month"` in `.album.toml` (default
`"none"`) inserts sticky date headers between row groups, Google-Photos style,
for albums that span a long period. Off by default because our primary
organisation is directories, not time.

### 11.2 Single-photo view

A full-viewport overlay over the album (so closing it returns you to your exact
scroll position), plus a standalone server-rendered page at the same URL for
direct links and sharing.

- **Keyboard:** `←`/`→` prev/next, `Esc` close, `i` toggle info, `d` download,
  `f` fullscreen, `Home`/`End` first/last.
- **Touch:** horizontal swipe to page, vertical swipe down to dismiss, both
  tracking the finger; pinch-zoom left to the browser.
- **Prefetch:** on showing photo *n*, kick off `Image()` loads for *n±1* (and
  *n+2* in the direction of travel). Combined with 59 KB (phone) / 128 KB
  (desktop) view images, paging feels instantaneous — this single detail is most
  of what "responsive" means in the PLAN. It also triples the bytes per swipe,
  which is exactly why the phone gets its own 1280 tier (§2.3).
- Progressive display: show the already-cached thumbnail, upscaled and blurred,
  under the view image until it decodes.

#### The two actions on a photo

**1. Download the original.** A clearly-labelled button (and the `d` key) on
`/orig/<path>`, which serves the untouched file straight from `photo_root` with
`Content-Disposition: attachment` and the original filename. Full resolution,
full EXIF, byte-for-byte what the camera wrote — none of the derivative
processing (§9.1 strips metadata from derivatives; originals keep everything).
On the server this is handed to Apache via `X-Sendfile` where available (§10.4),
so a 6 MB download costs the Python process nothing.

**2. The info panel** (`i`, or a visible ⓘ button — not keyboard-only, since
most viewing is on phones). Deliberately curated rather than a raw EXIF dump;
the interesting fields, in this order:

```
    IMG_1234.jpg                        4624 × 3472  ·  6.2 MB

    Sunday, 14 August 2025, 16:50
    Naxos, Greece                                  [ view on map ↗ ]

    Google Pixel 8 Pro
    1/250 s · f/1.7 · ISO 55 · 24 mm

                                        [ Download original ]
```

- **Date and time first** — the field people actually want. Rendered from EXIF
  `DateTimeOriginal`, which carries **no timezone**: it is the camera's local
  wall-clock time. Display it exactly as recorded and never "helpfully" convert
  through UTC, which is the classic way photo software shifts everyone's
  holiday snaps by three hours. If the tag is absent (scans, old cameras), fall
  back to the file's mtime and label it as such, so the two are never confused.
- **Location** — place name where known (§9.5), otherwise the raw coordinates,
  with a link out to a map. Suppressed entirely when `[ui] show_gps = false`.
- **Camera and exposure** on one line in the conventional photographic form.
  Omit any component that isn't present rather than printing "unknown".

Everything is best-effort: malformed EXIF is extremely common and a missing
field just drops its row.

**The map link** is configurable, since you may not want to hand coordinates to
a third party even on a deliberate click:

```toml
[ui]
map_link = "osm"     # "osm" (default) | "google" | "none"
```

`osm` → `openstreetmap.org/?mlat=…&mlon=…#map=15/…`, `google` →
`google.com/maps/search/?api=1&query=lat,lon`. Either way the outbound request
carries no referrer, because §12.3 sets `Referrer-Policy: no-referrer` — so the
map provider learns the coordinates but not which private album they came from.

### 11.3 Site chrome: who you are, and logging out

Every authenticated page carries the same two pieces of chrome, so there is
never a page from which you cannot leave.

**Footer**, at the bottom of every logged-in page, in small dimmed text:

> You can view this private album because you are logged in as **Dad**.
> If you wish, you can [log out].

The wording is yours; the only thing I've added is *who* you are logged in as,
which matters on a shared family computer and settles the "which of my two
Gmail accounts is this?" question that will otherwise come up. It's
configurable, with two placeholders substituted into the escaped text:

```toml
[ui]
footer_text = "You can view this private album because you are logged in as {user}. If you wish, you can {logout}."
```

`{user}` becomes the display `name` from `users.toml`, `{logout}` becomes the
link. Only those two placeholders are recognised; everything else is escaped.

**Header menu.** An album of 3000 photos is a long way to scroll to reach a
footer, so the sticky header's user menu carries the same thing in compact form —
"Signed in as Dad" and a "Log out" item. The footer explains, the header is
always to hand.

**Logout is a POST, not a link.** It *looks* like a link (a `<button>` styled as
one, so it stays keyboard- and screen-reader-correct), but it submits a tiny
form with a CSRF token. A plain `GET /logout` href is the tempting version and
it misbehaves in practice: link prefetchers, mail-scanning bots and "link
preview" fetchers follow GET links, and each one silently logs you out. It is
also trivially CSRF-able from any other page. Cost of doing it properly: about
four lines of HTML.

After logging out you land on the landing page (§11.5) with a "You have been
logged out." notice, rather than being bounced to a login form with no
explanation.

Note this ends the session on **this** browser only. To end a user's sessions
everywhere — a lost phone, a relative you no longer want to have access — bump
their `epoch` in `users.toml` or run `harelphotos user revoke NAME` (§12.1);
that invalidates every existing cookie for that user immediately.

The landing page shows none of this — it has no session to describe.

### 11.4 Responsiveness and theming

Single stylesheet, mobile-first, CSS custom properties, `prefers-color-scheme`
dark mode (a photo grid on a dark ground looks much better anyway), `safe-area-inset`
padding for notched phones, everything touch-target-sized ≥ 44 px. Tested on
Firefox and Chrome, desktop and Android/iOS widths.

**English-only** (§19) — no i18n framework, no translation files, no locale
machinery. The user-visible wording that does vary is already in `config.toml`
(`site_title`, `heading`, `tagline`, `footer_text`); the rest is in the
templates, in English.

The one concession to a possible Hebrew interface later costs nothing now: use
**CSS logical properties** throughout — `margin-inline-start` rather than
`margin-left`, `padding-inline`, `inset-inline`, `text-align: start`, `border-
inline-end`. It is the same amount of CSS, it is the modern idiom regardless,
and it is the difference between a future `dir="rtl"` being a one-line change
and being a stylesheet rewrite. No RTL work is done now, and no RTL testing is
in scope.

### 11.5 The landing page

`/` is the only page an unauthenticated visitor ever sees, so it carries the
whole first impression. It keeps the structure and the wording of the existing
site at `photos.harel.org.il`:

```
        ┌────────────────────────────────┐
        │   [ the Har'El family sign ]   │
        └────────────────────────────────┘

                 Photo Album

     By invitation only. Please login to continue.

        Username  [________________]
        Password  [________________]
                  [    Log in     ]

              ──────  or  ──────

           [  Sign in with Google  ]
```

Behaviour:

- Logged in already → `/` redirects straight to `/a/`. The landing page is only
  rendered for anonymous visitors.
- **Deep links survive login.** Hitting `/a/2019/summer/` while logged out shows
  this page with `?next=/a/2019/summer/`, and login lands you there rather than
  at the root. This is the shareable-URL requirement from the PLAN — a link you
  send to family has to work even when they're logged out. The current site does
  the same thing with its `httpd_location` hidden field; ours validates `next`
  as a **relative** path (starts with `/`, not `//`, no scheme) before
  redirecting, so it can't be turned into an open redirect to another site.
- The Google button appears only when `[google] enabled = true`, so the page is
  correct before M6 exists and if you skip Google entirely (§12.2).
- Error text is deliberate: a bad local login says "Incorrect username or
  password" without revealing which was wrong; a Google account that
  authenticated but isn't in `users.toml` gets a plain-language "That Google
  account isn't on the invitation list" rather than a bare 403, because that one
  *will* happen to a relative with two Gmail addresses.

**The hero image needs care, and the current site shows why.** Fetching
`newsign2.jpg` while logged out returns a 302 to the login page — the auth gate
catches the image too, so a logged-out visitor gets a broken image on the very
page meant to welcome them. We must not reproduce that, and we equally must not
"fix" it by punching an ACL hole into the photo routes.

Instead the landing image is a **dedicated public asset**, entirely outside the
photo tree and its ACL logic:

```toml
[ui]
landing_image = "/etc/harelphotos/newsign2.jpg"   # any file; copy the existing one
site_title    = "The Har'El Family Photo Album"   # browser title
heading       = "Photo Album"
tagline       = "By invitation only. Please login to continue."
```

`harelphotos init` derives it once into
`$DERIVED_ROOT/public/landing-{640,1280}.avif` plus a JPEG fallback, served at
those **fixed literal URLs** under `/public/`. No path component is ever taken
from the request, so there is nothing to traverse, and the auth exemption list
(§12.3) grows by one fixed prefix serving files that contain no photo data.

Presentation-wise this replaces `<CENTER>`/`BGCOLOR` markup with the same
stylesheet as the rest of the site: the image responsive via `srcset` rather
than hard-coded `WIDTH=500 HEIGHT=280`, the form usable on a phone, and dark
mode honoured. The words stay as they are — they're good.

---

## 12. Authentication

### 12.1 Sessions

Flask's signed-cookie session (itsdangerous, already a Flask dependency) — no
server-side session store to build, back up, or expire. Cookie holds
`{user: token, epoch: N, via: "local"|"google"}`.

- `SESSION_COOKIE_SECURE`, `HTTPONLY`, `SAMESITE="Lax"`; 30-day lifetime,
  sliding refresh. `SECURE` is switched off only in home-machine mode (§13.5),
  derived from the bind address rather than from a config setting, so it cannot
  be left off by accident in production.
- `epoch` is compared against `users.toml`; bumping a user's `epoch` (or
  deleting the user) invalidates their sessions immediately.
- `secret_key` read from `secret_key_file`; `harelphotos init` generates it.
  Changing it logs everyone out.

Local login: `werkzeug.security.check_password_hash` (scrypt by default).
Failed logins are rate-limited per (IP, username) with a small SQLite table and
exponential backoff, plus a constant-time dummy hash check for unknown users so
the response time doesn't reveal which usernames exist.

### 12.2 Google Sign-In

Standard OIDC authorization-code flow with PKCE, ~60 lines using `requests`:

1. `/auth/google/start` → store `state` + `nonce` + PKCE verifier in the
   session, redirect to `https://accounts.google.com/o/oauth2/v2/auth` with
   `scope=openid email`, `redirect_uri = base_url + "/auth/google/callback"`.
2. `/auth/google/callback` → check `state`, POST the code to Google's token
   endpoint over TLS, receive the ID token.
3. Decode the ID token's payload and check `nonce`, `aud`, `iss`, `exp`, and
   crucially `email_verified == true`.
4. Map `email` → a user in `users.toml` (matching either a table key or a
   `google = "…"` field). **No match → refuse.** Exactly the PLAN's model:
   Google merely asserts identity; the allowlist decides access. No
   self-registration, ever.

We can skip JWT *signature* verification (and thus any crypto dependency)
specifically because the token is fetched by us directly from Google's token
endpoint over a verified TLS connection — this is the case Google's own
documentation explicitly permits. That reasoning goes in a comment next to the
code, since it would otherwise look like a bug.

Endpoints come from Google's discovery document, fetched once and cached on disk
with a TTL.

#### What "Google Cloud Console" means here — you are not hosting anything on Google

The name is misleading. The Google Cloud Console is the single admin web UI for
*all* Google developer APIs, not just their hosting products. To let people sign
in with a Google account, someone has to tell Google "this application exists,
here is where it lives, please issue it a client ID" — and that registration
form happens to live in the Cloud Console. **Nothing about your server moves to
Google, nothing runs on Google's infrastructure, and there is no cost.**

Concretely, all you get out of this is two strings — a `client_id` and a
`client_secret` — that go into `config.toml` (§5.1). Your server keeps serving
your photos from your own machine exactly as before; Google's only role is
answering "yes, this browser really is somebody@gmail.com" when someone logs in.

You do need a Google account to do the registering, which you have. There is
**no billing account and no credit card involved** — OAuth client registration
is free and does not require enabling billing.

#### The setup, once

Google renames and rearranges these screens regularly, so treat this as the
shape of the process rather than an exact click path:

1. At `console.cloud.google.com`, create a **project** — just a free container
   to hold the registration. Name it anything ("harelphotos").
2. Configure the **OAuth consent screen**. Choose user type **External** (that
   simply means "not restricted to a Google Workspace organisation", which
   applies since your family use ordinary gmail.com accounts). Fill in an app
   name and your email. Request only the **`openid` and `email` scopes** —
   nothing else; we never read anyone's Gmail, Drive, contacts or profile.
3. Create an **OAuth 2.0 Client ID**, type **Web application**, and list the
   authorized redirect URIs (below).
4. Copy the client ID and secret into `config.toml`.

**Register two redirect URIs**: the production
`https://photos.harel.org.il/auth/google/callback`, and
`http://localhost:5000/auth/google/callback` for the home machine — Google
permits plain HTTP specifically for loopback addresses (§13.5).

#### Testing mode vs. publishing

A new consent screen starts in **Testing** mode, where only Google accounts you
explicitly list as "test users" (up to 100) can complete a login. Two annoyances
follow: you'd list each family member's email **twice** (once as a Google test
user, once in `users.toml`) with only the second actually controlling access;
and test users see a "Google hasn't verified this app" interstitial that looks
alarming to relatives.

**Publishing is free, and for our scopes it is immediate — no review queue.**
Google's verification review is required only for *sensitive* or *restricted*
scopes. We request `openid` and `email`, which are neither, so the app can go to
production without being reviewed by a human and without paying anything.

##### How to publish

In the console, under the OAuth consent screen / "Audience" settings, there is a
**Publish app** button that moves the publishing status from *Testing* to *In
production*. Before it will let you, fill in the consent screen fields it marks
required — typically:

| Field | What to use |
|---|---|
| App name | e.g. "Harel Photos" — shown on the consent screen |
| User support email | your own address |
| Developer contact email | your own address |
| Authorized domain | `harel.org.il` — the registrable domain, **not** the full `photos.harel.org.il` |
| Application home page | `https://photos.harel.org.il/` |
| Privacy policy link | `https://photos.harel.org.il/privacy` |
| Terms of service link | `https://photos.harel.org.il/terms` |

**The one trap: do not upload an app logo.** Uploading a logo triggers Google's
*brand verification*, which is exactly the slow human review that publishing
without a logo avoids. A logo buys you a small picture on the consent screen and
costs you weeks. Leave it empty.

The privacy-policy and terms URLs must be **publicly reachable** — Google may
fetch them, and so may your relatives. Since our app requires a login for
everything (§12.3), this is a design implication rather than a config task: the
app serves `/privacy` and `/terms` as two small static pages that are exempt
from the login requirement (§10.1). For a private family site the honest text is
short — no third-party sharing, no analytics, no tracking, data stays on a
personal server, contact address for deletion requests. `harelphotos init`
generates a reasonable default that you edit.

Google rearranges and renames these screens frequently (the OAuth settings were
recently rebranded "Google Auth Platform"), so expect the field names above to
be approximately, not exactly, what you see. If it does demand verification
despite the non-sensitive scopes, don't fight it — stay in Testing mode, which
works indefinitely for us. (The often-cited 7-day expiry for apps in testing
applies to *refresh tokens*; we never request one. We use the ID token once at
login and then maintain our own session cookie, so nothing expires at 7 days.)

After publishing, any Google account can *authenticate* — but authentication is
not authorization here: §12.2 step 4 still refuses anyone absent from
`users.toml`, so the allowlist remains the only thing that grants access.

#### This whole section is optional

Google Sign-In is a convenience, not a dependency. Local accounts (§12.1) are
fully functional on their own, so if this registration turns out to be more
bother than it's worth, skip M6 entirely and the site works — that is exactly
why §0 chose "both local and Google" rather than Google alone.

### 12.3 Other hardening

- `Content-Security-Policy: default-src 'self'; img-src 'self' data:;` — the app
  loads no third-party assets at all, so this is easy to keep strict.
- `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`.
- CSRF token on the login form and on any state-changing POST.
- Nothing is public: a single `@app.before_request` hook requires a session for
  everything except a short, hard-coded exemption list — `/` (the landing page,
  §11.5), `/login`, `/auth/*`, `/static/*`, `/public/*`, `/privacy`, `/terms`,
  and the health check. Opt-in-public would be a footgun; the PLAN says
  unauthorised people see nothing.
- That exemption list is the highest-risk few lines in the app, so it is
  literal, central, and covered by a test that walks every registered route and
  asserts each one either requires a session or appears on the list. Every
  exempt route serves fixed content and touches no photo data: `/privacy` and
  `/terms` exist only because Google's consent screen demands publicly fetchable
  URLs (§12.2), and `/public/*` serves the landing hero image under fixed
  literal filenames, so no request path ever reaches the filesystem (§11.5).

---

## 13. Deployment

### 13.0 Package requirements — deliverable: `INSTALL.md`

A standalone `INSTALL.md` is an explicit deliverable of milestone M8, covering
both distros, both roles (home workstation / server), the manual prerequisites
of §0.1 — **starting with moving the photos out of `/home` on the server** — and
the post-install verification. Its substance:

**System packages — Fedora** (workstation or server):

```
sudo dnf install python3 python3-pip httpd
```

**System packages — Rocky Linux 9.8:**

```
sudo dnf install python3.12 python3.12-pip httpd
```

The alternate interpreter is required, not cosmetic — Rocky's stock `python3` is
3.9, which cannot install a maintained Pillow (§3.1). `python3.12` is a regular
RHEL 9 AppStream package installing alongside the system Python, touching
nothing that depends on `python3`. **Confirmed available on the target Rocky 9.8
machine.**

On the **home machine**, `httpd` is unnecessary — `harelphotos serve` is enough.

**Server only, required — TLS:** `certbot python3-certbot-apache` (Fedora: stock;
Rocky: needs `epel-release` first). Full procedure in §13.3.

**Server only, optional:** `mod_xsendfile`, which lets Apache serve image bytes
directly (§10.4). It's stock in Fedora; on Rocky it requires EPEL
(`dnf install epel-release mod_xsendfile`) — which you'll likely have enabled
anyway for certbot. Skip it if you prefer; the app auto-detects and falls back,
and the difference only shows up under load we don't expect.

**Not needed, despite what you might assume:** `libavif-tools`, `python3-pillow`,
`python3-flask`, `mod_wsgi`, `vips`, ImageMagick, ffmpeg, exiftool, any database
server, any Node.js. The AVIF encoder arrives inside the Pillow wheel (§3.2).

**Python packages** — one venv, identical on both distros:

```
python3.12 -m venv /opt/harelphotos/venv          # 'python3' on Fedora
/opt/harelphotos/venv/bin/pip install -e /opt/harelphotos/src
```

which pulls in exactly: Flask (+ Jinja2, Werkzeug, click, itsdangerous,
blinker, markupsafe), Pillow, requests (+ urllib3, certifi, idna,
charset-normalizer), gunicorn (+ packaging).

**Verification step**, which `INSTALL.md` must include, because a Pillow without
AVIF fails late and confusingly (at the first encode, hours into a bulk scan)
rather than at install time:

```
/opt/harelphotos/venv/bin/python -c \
  "from PIL import features; assert features.check('avif'), 'no AVIF'; print('ok')"
```

`harelphotos check --env` performs this and the other environment assertions
(Python version, writable state dir, readable photo root, Apache modules —
including that `mod_deflate` is actually compressing `text/html`, which §10.3
depends on and which fails silently if misconfigured).

### 13.1 Layout on disk

```
/opt/harelphotos/src         the checkout
/opt/harelphotos/venv        the virtualenv
/etc/harelphotos/            config.toml, users.toml, secret_key, newsign2.jpg
/var/lib/harelphotos/        index.sqlite, geonames.sqlite, derived/
/run/harelphotos/            gunicorn.sock (systemd RuntimeDirectory)
/srv/photos/                 the originals
```

### 13.1a Where `photo_root` lives — and why it differs per machine

`photo_root` is configuration, not a constant, and the two machines settle it
differently:

| | `photo_root` | why |
|---|---|---|
| **Server** | `/srv/photos` | outside `/home`, so no ACLs and no SELinux special-casing |
| **Home machine** | `/home/nyh/pictures` | stays where it is; none of the server's constraints apply |

**Why `/srv` on the server.** Photos under `/home` cost three separate
workarounds, each of which fails confusingly: home directories are `HOME_MODE
0700` on both distros, so a dedicated service user cannot even traverse into
them (needing a POSIX ACL, including a *default* ACL or albums added later
silently become unreadable); SELinux forbids Apache from reading home content
(`httpd_read_user_content` is off by default — verified on this machine — which
bites the moment you enable `mod_xsendfile`); and `ProtectHome=` in the systemd
unit has to be weakened. Moving the directory removes all three at once, which
is a much better trade than configuring around them. `/srv` is also the
conventional FHS location for data served by the system.

Setup is then unremarkable — the photos keep belonging to you, and the service
user reads them like any other file:

```
sudo mkdir -p /srv/photos
sudo chown -R nyh:nyh /srv/photos      # you still own and manage them
                                       # dirs 0755, files 0644 under the usual umask
```

One thing to check before moving 300 GB: `df /home /srv`. If both sit on the
same filesystem, `mv` is an instant rename; if they don't, it's a genuine
300 GB copy and worth running under `screen`.

**Why the home machine needs nothing.** There, `harelphotos serve` runs as *you*
(§13.5) — no service user, no Apache, no SELinux confinement — so photos in
`~/pictures` are simply readable by the process that wants them. All three
problems above are artefacts of the privilege separation that only exists on the
server.

**What this relies on**, and the reason it costs nothing: the index stores
**relative** paths only (§7). `2019/summer/IMG_1234.jpg` means the same thing
under either root, so `index.sqlite` copies between the machines unchanged even
though `photo_root` differs — which is what makes the sync in §14 work at all.
Each machine's `config.toml` carries its own `photo_root`, `derived_root` and
`base_url`; nothing else differs.

**On your Apache question** — correct, and structural rather than configured:
there is no `Alias` or `<Directory>` anywhere exposing `photo_root`, so Apache
has no route to those files. Every byte goes through the Flask handler, which
performs the ACL check (§6) first and only then hands the path over.
`XSendFilePath` grants Apache permission to serve a file *when the application
asks it to*; it is not a way for a request to reach one. The same check guards
`/orig/…`, so downloads are authenticated exactly like thumbnails.

### 13.2 Why three pieces? Flask, gunicorn and Apache

Reasonable confusion, because Flask does ship *a* server. The three do different
jobs:

```
   browser ──HTTPS──▶  Apache  ──HTTP over a unix socket──▶  gunicorn  ──calls──▶  Flask app
                       │                                     │                     │
                       TLS, vhosts, static files,             process management,   your code:
                       sendfile(), logs, slow-client          worker pool,          routes, ACLs,
                       buffering, compression                 restarts, timeouts    SQL, templates
```

| | what it is | what it does here |
|---|---|---|
| **Flask** | a *library* (web framework) | turns an HTTP request into a Python function call and the return value into a response. Defines a WSGI application object. **It is not a server** — it needs something to run it |
| **gunicorn** | a WSGI *server* | actually runs the Python code: keeps a pool of worker processes, restarts one if it crashes, enforces timeouts, hands requests to the Flask object |
| **Apache** | the edge/reverse proxy | terminates TLS, hosts the vhost, serves `/static/` (and image bytes via `mod_xsendfile`, §10.4) with `sendfile()`, writes access logs, buffers slow clients |

**But doesn't Flask have a built-in server?** It does — Werkzeug's development
server, which is what `harelphotos serve` uses (§13.5). Its own documentation
says not to use it in production: it is single-threaded by default, has no
process supervision, no TLS, and no protection against a client that dribbles
out a request over five minutes. It's excellent for development and genuinely
fine on your home machine; it is not what you want facing the internet.

**Could we drop one?**

- *Drop Apache, let gunicorn face the internet?* Possible — gunicorn speaks
  HTTP. But you'd then terminate TLS inside gunicorn (certbot's Apache plugin
  automates this for Apache and not for gunicorn), serve static files from
  Python, and expose the worker pool directly to slow clients, which is exactly
  what gunicorn's sync workers handle worst. Since the server already runs
  Apache, adding three proxy lines is far less work than replacing what Apache
  already does well.
- *Drop gunicorn, use Apache's `mod_wsgi`?* This is the real alternative, and it
  would be one fewer moving part. The reason not to is concrete rather than
  aesthetic: **mod_wsgi is compiled against one specific Python interpreter**,
  and we need Python 3.12 on Rocky (§3.1) while the distro's `python3-mod_wsgi`
  is built against the system 3.9. Matching those up means either building
  mod_wsgi yourself or abandoning the newer Python. gunicorn is just a Python
  program inside our own venv, so it works with whatever interpreter that venv
  has — identically on Fedora and Rocky. It also gives clean `systemctl restart
  harelphotos` semantics without touching Apache.

**On the home machine you use one of the three, not all of them**: `harelphotos
serve` runs the Flask app on Werkzeug's server directly, with no Apache and no
gunicorn (§13.5). The stack below is only for the server.

#### The gunicorn side

`harelphotos.service` — a systemd unit, so gunicorn starts at boot and is
restarted if it dies:

```ini
[Service]
User=harelphotos
ExecStart=/opt/harelphotos/venv/bin/gunicorn --workers 3 --threads 4 \
          --bind unix:/run/harelphotos/gunicorn.sock harelphotos.web:app
Environment=HARELPHOTOS_CONFIG=/etc/harelphotos/config.toml
RuntimeDirectory=harelphotos
ProtectSystem=strict
ReadWritePaths=/var/lib/harelphotos
ProtectHome=yes                # photos are at /srv/photos, not under /home (§13.1a)
ReadOnlyPaths=/srv/photos
# photo_root appears only under ReadOnlyPaths → the web process can never
# write to your photos, even if it is compromised.
```

Note it binds a **unix socket**, not a TCP port: the Python workers are then not
reachable from the network at all, only through Apache.

#### The Apache side

```apache
Alias /static/ /opt/harelphotos/src/harelphotos/static/
<Directory /opt/harelphotos/src/harelphotos/static>
    Require all granted
    Header set Cache-Control "public, max-age=604800"
</Directory>
ProxyPass        /static/ !
ProxyPass        / unix:/run/harelphotos/gunicorn.sock|http://localhost/
ProxyPassReverse / unix:/run/harelphotos/gunicorn.sock|http://localhost/
RequestHeader set X-Forwarded-Proto https

# REQUIRED: album HTML is highly repetitive and compresses ~31x (§10.3).
# Without this a 2000-photo album is 719 KB instead of 23 KB.
AddOutputFilterByType DEFLATE text/html text/css application/javascript application/json
# Do NOT add image/avif, image/webp or image/jpeg — already compressed, pure waste.
# Optional, if mod_xsendfile is installed:
#   XSendFile On
#   XSendFilePath /var/lib/harelphotos/derived
#   XSendFilePath /srv/photos
```

Flask sits behind `ProxyFix` so it sees the real scheme and client IP.

On the home machine, none of this is needed — see §13.5.

### 13.3 TLS with Let's Encrypt — free, and required

TLS is **not optional here**, for three independent reasons: Google's OAuth will
not redirect to a plain-HTTP `redirect_uri`; `SESSION_COOKIE_SECURE` (§12.1)
means the login cookie is never sent over HTTP, so the site simply won't work;
and without it every family member's password crosses the network in clear text.

**Let's Encrypt** issues certificates free, automatically, with 90-day lifetimes
and unattended renewal. Certificates are per-**hostname**, which is why the
hostname matters and the IP address does not — Let's Encrypt will not issue a
certificate for a bare IP at all, so using the hostname isn't merely your
preference, it's a requirement.

#### Prerequisites

1. **DNS**: the hostname must resolve publicly to this server.
   Check from *elsewhere*, not from the server itself:
   `dig +short photos.harel.org.il` should print the server's public IP.
2. **Port 80 and 443 reachable from the internet.** Port 80 is needed even
   though the site will be HTTPS-only: it's how the HTTP-01 challenge and the
   redirect work. If your ISP blocks port 80, skip to the DNS-01 note below.
3. `httpd` installed and serving that hostname (§13.2).

If the server's public IP is **dynamic**, add a dynamic-DNS updater so the name
keeps pointing at it; renewals fail silently otherwise, and you'd discover it 90
days later when the site breaks.

#### Install certbot

Fedora:

```
sudo dnf install certbot python3-certbot-apache
```

Rocky Linux 9 — certbot lives in EPEL:

```
sudo dnf install epel-release
sudo dnf install certbot python3-certbot-apache
```

*(EPEL is a well-established Fedora-project repository, not a third-party one.
If you'd rather not enable it, `acme.sh` is a single self-contained shell script
that does the same job with no packages at all —
`curl https://get.acme.sh | sh` — but certbot is the better-trodden path and
`INSTALL.md` will document certbot as the primary route.)*

Note this certbot venv is **entirely separate** from the application's venv
(§13.0) — it uses the system Python 3.9 on Rocky, which is fine because it's
distro-maintained. They don't interact.

#### Obtain the certificate

```
sudo certbot --apache -d photos.harel.org.il
```

This edits your Apache config in place: it proves control of the hostname over
port 80, installs the certificate, and — when it offers — sets up the
HTTP→HTTPS redirect. **Say yes to the redirect.**

Afterwards the vhost from §13.2 should live inside the `<VirtualHost *:443>`
block certbot created, with port 80 reduced to a redirect. Two settings to add
by hand:

```apache
# in the :443 vhost
Header always set Strict-Transport-Security "max-age=15768000"
```

and in `config.toml`, `base_url = "https://photos.harel.org.il"` — this is what
the OAuth `redirect_uri` is built from (§12.2), and it must match the value
registered with Google exactly, including the scheme. (That registration is just
a web form; nothing is hosted on Google — see §12.2.)

#### Renewal

The certbot package installs a systemd timer that renews automatically; nothing
to configure. Verify it is armed and that renewal actually works:

```
systemctl status certbot-renew.timer
sudo certbot renew --dry-run
```

Run the dry-run once at install time. It exercises the whole path, so a
misconfiguration surfaces now rather than in 90 days.

#### If port 80 is blocked by your ISP

Use the DNS-01 challenge instead, which proves control by publishing a TXT
record and needs no inbound ports at all. If your DNS provider has a certbot
plugin (Cloudflare, Route 53, DigitalOcean, and others), it's fully automatic:

```
sudo dnf install python3-certbot-dns-cloudflare      # or your provider
sudo certbot certonly --dns-cloudflare -d photos.harel.org.il
```

Otherwise `certbot certonly --manual --preferred-challenges dns` works but
requires editing a DNS record by hand every 90 days — workable, but arrange the
plugin if you can.

### 13.4 Firewall and SELinux

Both matter on Rocky, which ships firewalld and SELinux enforcing by default;
Fedora is the same. These are the things that make a correct configuration
appear broken.

```
sudo firewall-cmd --permanent --add-service=http --add-service=https
sudo firewall-cmd --reload
```

SELinux, for the reverse proxy in §13.2 — without this, Apache gets
*Permission denied* connecting to the gunicorn socket and logs a 503 while
everything looks correctly configured:

```
sudo setsebool -P httpd_can_network_connect 1
```

And if you enable `mod_xsendfile` (§10.4), Apache must be allowed to read the
files it hands out. Both trees are ordinary system data, so a label is all it
takes — this is precisely the simplicity bought by keeping photos out of `/home`
(§13.1a):

```
sudo semanage fcontext -a -t httpd_sys_content_t "/var/lib/harelphotos/derived(/.*)?"
sudo semanage fcontext -a -t httpd_sys_content_t "/srv/photos(/.*)?"
sudo restorecon -R /var/lib/harelphotos/derived /srv/photos
```

Neither is needed at all with `sendfile_header = "none"`, which keeps Apache out
of the file-reading business entirely.

`harelphotos check --env` tests these conditions directly rather than making you
infer them from a 503: the socket is reachable, the derived tree is readable by
the Apache user, and the service user can actually `stat()` and open a sample
photo under `photo_root`, traversing every parent directory on the way. That
last check is cheap and stays worth having even now that `/srv/photos` makes it
unlikely to fail — permissions drift, and a 403 on every thumbnail is a poor way
to find out.

### 13.5 Home-machine mode: no domain, no TLS, no Apache

Everything in §13.2–13.4 is *server* deployment. The home machine — where you'll
do the bulk encoding (§14) and most of the UI work — needs none of it:

```
harelphotos serve                 # http://127.0.0.1:5000, no Apache, no TLS
```

Flask's dev server, serving its own static files and image bytes. This is a
first-class supported mode, not a degraded one, and `INSTALL.md` documents it as
the "workstation" role.

**What changes automatically in this mode**, keyed off the bind address being
loopback or the `--dev` flag — never off a config value that could be wrong in
production:

| | server | `harelphotos serve` |
|---|---|---|
| `SESSION_COOKIE_SECURE` | on | **off** — otherwise the cookie is never sent over HTTP and login silently fails |
| HSTS header | on | off |
| `base_url` | `https://host` | `http://127.0.0.1:5000` |
| Static files | Apache | Flask |
| Template auto-reload | off | on |

The `SESSION_COOKIE_SECURE` interaction is the one that would otherwise cost an
afternoon: with it on and no TLS, you log in, get redirected, and land back at
the login page with no error anywhere.

**Local accounts work normally** over plain HTTP on localhost — create one with
`harelphotos user add nyh` and log in. That's the honest way to exercise the
real login path.

**Google Sign-In also works here**, which is worth knowing because it looks like
it shouldn't. Google's OAuth policy makes an explicit exception for loopback
addresses: `http://localhost:5000/auth/google/callback` is accepted as an
authorized redirect URI even though it's plain HTTP. Register it as a *second*
redirect URI alongside the production one (§12.2) and both work. (The exception
is loopback only — a LAN IP or a `.local` name will be rejected.)

**Skipping auth entirely for UI work.** Since you said this doesn't need to
work with authentication, there's a shortcut:

```
harelphotos serve --no-auth          # every request is a synthetic admin user
harelphotos serve --as grandma       # browse as a real user, to test ACLs
```

`--no-auth` renders a persistent banner across the top of every page, so a
window left open for a week can't be mistaken for the real thing. `--as USER` is
the more useful one day-to-day: it's how you check that §6's ACL rules actually
hide what they should, without logging in and out repeatedly.

**Testing on a phone**, which you'll want for the swipe gestures and the
justified grid (§11.1c):

```
harelphotos serve --bind 0.0.0.0     # then browse http://192.168.1.x:5000 on the phone
```

Plain HTTP over your LAN is fine for this — nothing in the UI requires a secure
context (no service workers, and the Fullscreen API works over HTTP). Combining
`--bind 0.0.0.0` with `--no-auth` additionally requires `--insecure`, so that
exposing an unauthenticated view of your photo collection to the local network
is always a deliberate act. `--as USER` has no such restriction.

The dev server is single-threaded by default; pass `--threads 4` when testing
a large album, or the browser's parallel image fetches will queue behind each
other and give a misleading impression of how the grid performs.

### 13.6 Logging

Not glamorous, but the difference between "the site is broken" and knowing why.
Python's stdlib `logging`, one configuration, two destinations:

- **The web app** logs to `log_file` from `config.toml`, or to stderr when unset
  — under systemd, stderr goes to the journal, so `journalctl -u harelphotos`
  works with no configuration at all. That's the recommended setup on the
  server; `log_file` exists for the case where you'd rather have a plain file.
- **The CLI** logs to stderr at `WARNING`, `-v` for `INFO`, `-q` for errors
  only. Progress output (§15) goes to stdout and is separate from logging, so
  `harelphotos scan >/dev/null` still shows you the problems.

What must always be logged, because each is otherwise invisible: `.album.toml`
parse errors (§5.3), per-photo derive failures (§8 phase 3), authentication
failures with the source IP (§12.1), and any request that 500s. Apache keeps its
own access log; there's no reason to duplicate it in the app.

Deliberately *not* logged: photo paths on successful requests. A log of who
viewed which family photos when is a privacy liability that serves no purpose
here, and it is the kind of thing that accumulates silently for years.

### 13.7 Backups

Back up: `$PHOTO_ROOT` (the photos and their `.album.toml` files) and
`$CONFIG` (`users.toml`, `secret_key`). Explicitly **do not** back up
`index.sqlite` or the derived tree — both are reproducible, and saying so
plainly in the README is worth more than any amount of clever backup tooling.

---

## 14. Two-machine workflow

The PLAN's constraint — weak server, fast home machine — with the answer that
bulk encoding happens at home. Concretely:

```
 1. Upload new photos to the server              (however you do it today)
 2. server → home   rsync -a --delete originals  (mtimes preserved: -a does)
 3. home            harelphotos scan             (~1.8 h first time, ~seconds later)
 4. home → server   rsync -a --delete derived/   (~17 GB first time, small deltas after)
 5. home → server   copy index.sqlite            (VACUUM INTO for a consistent snapshot)
 6. server          systemctl reload harelphotos
```

Steps 2–6 wrapped in `harelphotos sync --to server` (a thin, readable wrapper
around two rsyncs and an ssh command; `--dry-run` prints them).

Two correctness requirements this imposes, both already satisfied by the design:

- **Relative paths everywhere in the DB**, so `index.sqlite` is portable between
  machines (§7).
- **`deriv_key` is derived from `content_sig`, not from `mtime_ns`** (§7). This
  is what makes the sync robust: the signature is computed from the file's own
  bytes, which `rsync` reproduces exactly, so the server agrees with the home
  machine about what is up to date and a `harelphotos scan` run *on the server*
  after a sync finds nothing to do rather than re-encoding 80,000 photos. Had
  the key depended on mtime, any timestamp drift between the two machines — a
  filesystem with coarser timestamp granularity, an rsync without `-a`, a
  restore from backup — would have triggered exactly that mass re-encode.

If the home machine ever lacks a full copy of the photos, `harelphotos scan` on
the server still works — it's just slower (and `--nice`, `--jobs 1`, `--limit`
exist for exactly that). The two-machine flow is an optimisation, not a
requirement.

---

## 15. Command-line interface

```
harelphotos init                     create config, state dirs, secret key,
                                     and default privacy/terms text (§12.2)
harelphotos init --geonames          download the offline city dataset, 13.6 MB (§9.5)
harelphotos init --landmarks         also download the landmark dataset, 421 MB (§9.5)
harelphotos geocode [--force]        resolve place names from GPS already in the DB;
                                     no photo files are read, ~1 s for 80k photos
harelphotos scan [--full] [--jobs N] [--nice N] [--limit N] [--dir PATH]
                 [--dry-run] [--fallback webp|jpeg]
harelphotos check                    config errors, missing/failed derivatives,
                                     orphans, broken cover references, ACL summary
harelphotos gc [--deep]              remove orphaned derivatives
harelphotos serve [--bind ADDR] [--port N] [--threads N]
                  [--no-auth [--insecure]] [--as USER]
                                     home-machine server: plain HTTP, no Apache,
                                     no TLS; see §13.5
harelphotos user add NAME [--google EMAIL] [--admin]
harelphotos user passwd NAME
harelphotos user list | del NAME | revoke NAME
harelphotos cover DIR PHOTO          write cover = "…" into DIR/.album.toml
harelphotos acl DIR [--allow …]      inspect / set a directory's ACL
harelphotos stats                    photo/dir counts, derived size, biggest albums
```

`scan` prints a live progress line (`12,431/80,102 · 18 photos/s · ETA 1:02:14`)
and a summary of added/removed/failed.

---

## 16. Testing

Not exhaustive TDD, but enough that a rescan can't silently eat data:

- **Fixture tree generator** — builds a temp tree of tiny synthetic JPEGs with
  controlled EXIF (dates, orientations, GPS), nested dirs, `.album.toml` files,
  deliberate edge cases: unicode and spaces in names, a corrupt JPEG, an empty
  directory, a broken symlink, a malformed TOML file, and one deliberately
  non-UTF-8 filename — asserted to be skipped and reported rather than to raise
  (§8 phase 1).
- **Scanner diff tests** — the highest-value tests in the project. Scan, mutate
  the tree (add / delete / rename / touch / move a subtree / edit a
  `.album.toml`), rescan, assert the DB and the derived tree match a full
  from-scratch scan exactly. Also: interrupt mid-derive and assert that a resume
  converges.
- **The `touch` test specifically** (§8 phase 2) — `os.utime()` every photo in a
  fixture tree, rescan, and assert that **zero** derivatives were regenerated
  while the stored `mtime_ns` values were all updated. Then modify one photo's
  bytes and assert exactly one regeneration. This guards the property that keeps
  an EXIF-date tidy-up from costing 22 core-hours, and it is the kind of thing
  that silently regresses.
- **Sort-order tests** — EXIF date wins over mtime; photos lacking EXIF fall
  back to mtime and interleave correctly; equal timestamps tie-break by name
  deterministically across repeated scans.
- **Subdirectory ordering tests** (§11.1b) — natural sort puts `Day 2` before
  `Day 10` and `2009` before `2010`; `dirsort = "-name"` reverses; a parent's
  `order` list pins its named children first with the rest following; a child's
  `sort_key` displaces its name in the comparison; a name in `order` that no
  longer exists is ignored rather than raising; and the whole ordering is stable
  across repeated scans.
- **ACL tests** — a matrix of users × directories over a fixture tree with
  nested `allow` and `allow_replace`, asserted against both HTML routes *and*
  `/i/…` and `/orig/…` (§6's invariant), plus listing suppression and 404-not-403.
- **Auth tests** — login/logout, bad password, epoch revocation, session cookie
  tampering, CSRF, and a mocked Google callback (valid, wrong `state`, wrong
  `nonce`, unverified email, unknown email).
- **Route exemption test** — enumerate `app.url_map` and assert every route
  either rejects an anonymous request or is on the literal public list (§12.3).
  This catches the failure mode where a new route is added and quietly forgets
  the login requirement.
- **Logout tests** (§11.3) — `GET /logout` does *not* end the session (a
  prefetcher must not be able to log you out); `POST /logout` without a valid
  CSRF token is rejected; with one, the session cookie is cleared and the old
  cookie no longer authenticates if replayed. Plus: every logged-in page renders
  a logout control, asserted by walking the authenticated routes.
- **Landing page tests** — anonymous `/` renders and its hero image loads with
  no session (the bug the current site has); logged-in `/` redirects to `/a/`;
  `?next=` round-trips a deep link; and `next=https://evil.example/`,
  `next=//evil.example`, `next=javascript:…` are all rejected rather than
  followed.
- **EXIF/info-panel tests** — `DateTimeOriginal` is displayed as the camera's
  local wall-clock time with **no timezone conversion** (assert the rendered
  string matches the tag under several `TZ` settings — the shifted-holiday-snaps
  bug, §11.2); a photo with no EXIF date falls back to mtime and is labelled
  differently; GPS renders when present and the whole row disappears under
  `show_gps = false`; a photo with no GPS inherits its album's `location`, and
  its own coordinates win when it has them.
- **Content-negotiation tests** (§9.4) — a request whose `Accept` includes
  `image/avif` gets AVIF; one with only `image/webp` gets WebP; one with
  neither gets JPEG; and **every image response carries `Vary: Accept`**, which
  is the silent-and-long-lived bug of §10.3. Also: `fallback = "none"` returns
  AVIF regardless of `Accept`.
- **Scan locking test** (§8) — a second `scan` while one holds the lock exits
  with a clear message rather than running concurrently.
- **Tiny-image test** — a photo smaller than the smallest tier gets no
  derivatives and still renders, via the inline-original route, without
  `Content-Disposition` (§9.1).
- **Derivative tests** — orientation applied correctly (all 8 EXIF values),
  no upscaling, no EXIF in output, `deriv_key` invalidation on each config
  change, atomic-write behaviour.
- **Route smoke tests** via Flask's test client; a tiny scan-performance test
  over ~5k synthetic files to catch accidental O(n²).

`pytest`, no other test dependency.

---

## 17. Milestones

Each milestone is independently useful and independently testable.

| # | Deliverable | Notes |
|---|---|---|
| **M1** | Config loading, `.album.toml` parsing, DB schema, `init` | foundation |
| **M2** | `scan` phases 1–2 + `check`: tree indexed with dates and dimensions, no images yet | verify the diff engine and the mtime-vs-signature logic on the real 300 GB tree — fast and safe |
| **M3** | Derivative pipeline + phases 3–4; `gc`, `stats`; **offline city geocoding** (§9.5) | the first long run; validate the §2 projections against reality |
| **M4** | Flask app: album browsing, **subdirectory cards + ordering** (§11.1a–b), **justified-row photo grid** (§11.1c), photo pages, **no auth** | bind to localhost only |
| **M5** | Landing page (§11.5), local accounts, sessions, `?next=` deep links, ACL enforcement | the security-critical milestone; write these tests first |
| **M6** | TLS (§13.3), firewall/SELinux (§13.4), then Google Sign-In | TLS comes first. The Google half is optional (§12.2) — local accounts already work, so M6 can be dropped or deferred without affecting anything else |
| **M7** | Lightbox: keyboard, swipe, prefetch, **info panel + download original** (§11.2), album `location` | the "feels like Google Photos" milestone |
| **M8** | Deployment: **`INSTALL.md`** (§13.0), `check --env`, gunicorn unit, Apache vhost, `sync`, README | ⚠️ needs the §0.1 manual steps done first — above all, photos moved out of `/home` on the server |
| **M9** | Polish: date-group headers, cover-picker UI, dark mode, **opt-in landmark geocoding** (§9.5), >5000-photo safety valve | |

M4 is usable on the home machine from day one via `harelphotos serve` (§13.5) —
plain HTTP on localhost, `--no-auth` for pure UI work, `--bind 0.0.0.0` to try
it on a phone. Nothing in M4–M7 requires the server, a domain, or TLS; those
first appear in M6 only for the Google Sign-In half.

Suggested order deviation worth considering: do **M5 before M4 is exposed** —
never run an unauthenticated version on a machine reachable from the internet,
even briefly.

### How the work proceeds

For whoever picks this up later, including a future session reading this cold:

- **One milestone per working session**, then stop and report. A milestone is
  the natural review unit: large enough to be worth reviewing, small enough to
  review honestly. M3 and M4 are big enough to split.
- **Tests are written with the code, not after**, and must pass before a
  milestone is called done. §16 lists what matters; the scanner-diff and ACL
  tests are the ones that earn their keep.
- **This document is the spec.** Where it settles a question, follow it. Where
  reality contradicts it — a measurement that doesn't reproduce, a library that
  behaves differently — say so and update the document rather than quietly
  improvising around it. Several sections exist only because a measurement
  contradicted an assumption.
- **Expensive and irreversible operations belong to the owner, not the
  implementer.** The 1.8-hour bulk scan over the real collection, the 17 GB
  rsync, moving 300 GB of photos, anything at all on the server. Prepare them,
  print the exact command, and let a human run it.
- **Synthetic fixtures cannot substitute for the real tree.** A 20-year
  collection has odd filenames, broken EXIF, empty directories and files no
  test author would think to invent. The first run of `scan` against the real
  `photo_root` (M2, cheap and read-only) is the most informative test in the
  project.

---

## 18. Deliberately deferred

Recorded so they're not accidentally designed out:

- **Video** (MP4/MOV) — poster-frame thumbnails via ffmpeg, `<video>` playback,
  probably serve originals rather than transcode. The `photos` table and the
  derived-path scheme (`NAME.ext.avif`) already accommodate a `kind` column.
- **HEIC / RAW / PNG** — `pillow-heif` for HEIC; RAW via `rawpy` or by using the
  embedded preview JPEG. Only the "which extensions count as photos" list and
  the decode step need to change.
- **Search and timeline view** — `taken` is already indexed, and titles/filenames
  would go in an FTS5 table. A flat chronological scroll across all albums is
  the biggest single chunk of remaining work.
- **A larger hi-DPI tier** (3200 px, ~+17 GB) — one config line plus a rescan,
  if retina laptops turn out to look soft in practice. §2.1 has the cost.
- **Set-cover-from-the-browser** for admins — writes `cover = …` into
  `.album.toml`; needs the web process to have write access to `photo_root`,
  which §13.2 currently forbids, so it needs a deliberate decision.
- **Album ZIP download** — decided against for v1: a sustained I/O and bandwidth
  hit on a weak server, needing rate limiting to be safe.
- **A pyvips derive backend** — measured at ~1.4× faster (§3.3), behind the same
  backend interface. Worth doing only if the collection grows enough that
  ~2 hours per full regeneration becomes annoying.
- **Cached rendered album bodies** — an in-process LRU keyed by
  `(dir_id, acl_class)`, with the viewer's name filled in client-side, if the
  4 ms render of a 500-photo album ever turns out to matter (§1). The `ETag`/304
  path in §10.3 is the cheap 90% of this and is already in the plan.
- **Face/object recognition, sharing links, uploads via the web UI** — out of
  scope; this is a viewer for a collection you curate with a filesystem.

---

## 19. Open questions

Nothing here blocks starting on M1 — these can be answered as we reach them.

~~1. **Grid style**~~ — *resolved: justified rows, never cropped, built in M4
   rather than deferred to polish. §11.1c has the algorithm.*
~~2. **Sort order default**~~ — *resolved: `date` = EXIF `DateTimeOriginal`,
   falling back to filesystem mtime, tie-broken by filename (§5.3). This is what
   motivated the content-signature scanner phase (§8) so that fixing up mtimes
   doesn't trigger a mass re-encode.*
~~3. **`photo_root` on the server**~~ — *resolved: **`/srv/photos`** on the
   server (moved out of `/home`, which removes the ACL, SELinux-boolean and
   `ProtectHome` workarounds outright), and **`~/pictures`** on the home
   machine, where `serve` runs as you and none of those constraints exist.
   §13.1a. Check `df /home /srv` before moving — same filesystem means `mv` is
   instant, different means a real 300 GB copy.*
~~4. **Domain and TLS**~~ — *resolved: hostname exists, TLS not yet set up.
   §13.3 is the step-by-step (Let's Encrypt, free, auto-renewing), and §13.4
   covers the firewall and SELinux settings that make a correct setup look
   broken. Still needed from you: the actual hostname, so `base_url` and the
   Google OAuth `redirect_uri` can be filled in at M6.*
5. **Rough photo count** — my sizing assumed ~80,000 photos from 300 GB. If it's
   really 250,000 smaller photos, the numbers in §2 shift (more scan time, more
   thumbnails, but *less* total derived size). Worth checking early with
   `find $PHOTO_ROOT -iname '*.jpg' | wc -l`, since it's the input to every
   estimate in §2.
6. **Disk budget for the derived tree** — the default ladder wants ~17 GB for
   80k photos. If that's more than you want to spend, say so and the default
   drops to `thumb = [400]`, `view = [1600]` (~7.8 GB) at some cost in
   sharpness on retina screens and in phone bandwidth.
~~7. **Python 3.12 on Rocky**~~ — *resolved: confirmed available. `requires-python
   = ">=3.11"` stands, and the `tomli` shim is gone for good.*

~~8. **Language and date formatting**~~ — *resolved: **English-only**, no i18n
   machinery, no translation files, no locale configuration. Hebrew is a
   possible future want but explicitly not now. The one free precaution is in
   §11.4: write the stylesheet with CSS **logical properties** from the start,
   which costs nothing today and is what makes a future `dir="rtl"` mostly a
   one-line change instead of a rewrite.*
~~9. **Filename encoding**~~ — *resolved: **filenames are UTF-8**; the scanner
   assumes it and builds no transcoding machinery. It still refuses a
   non-UTF-8 name cleanly rather than crashing on it (§8, phase 1) — the
   assumption is cheap to hold, but a two-hour scan should not die on its
   80,000th file if one turns out to be odd.*
