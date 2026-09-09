# harelphotos — manual

What the software does **today**, and how to use it.

This describes only what is implemented and working. For why things are the way
they are — the measurements, the alternatives rejected, the plan for what isn't
built yet — see [DESIGN.md](DESIGN.md). Where the two disagree, this file is
right and DESIGN.md is out of date.

> **Implemented so far:** indexing a photo tree, generating its images, and
> browsing them in a web interface. There is **no login yet** — anyone who can
> reach the server sees everything, so keep it on `localhost` until M5.

---

## Installing

Python 3.11 or newer, and four packages. On Fedora the system Python is fine;
on Rocky Linux 9 install `python3.12` first, because the stock `python3` is 3.9
and too old.

```
git clone https://github.com/nyh/harelphotos
cd harelphotos
python3 -m venv .venv               # python3.12 -m venv .venv  on Rocky 9
./.venv/bin/pip install -e .
```

Check that Pillow can encode AVIF — it will be needed later, and it is better
to find out now than an hour into a scan:

```
./.venv/bin/python -c "from PIL import features; print(features.check('avif'))"
```

That must print `True`. The PyPI Pillow wheel bundles AVIF support; a distro
`python3-pillow` package generally does not, which is why the venv exists.

---

## Running the commands

**From which directory?** Any. The working directory does not matter, provided
you use the full path to the program and give the config path absolutely.

The program lives in the virtualenv, at `<checkout>/.venv/bin/harelphotos`. It
was installed with `pip install -e .`, so it runs from anywhere without
activating anything — the two lines below are only so you can type
`harelphotos` instead of the full path, and so every command finds the same
config:

```sh
export PATH="$HOME/harelphotos/.venv/bin:$PATH"
export HARELPHOTOS_CONFIG="$HOME/.config/harelphotos/config.toml"
```

(`source ~/harelphotos/.venv/bin/activate` does the same as the first line.
Put both in `~/.bashrc` and you never think about it again.)

**Setting `HARELPHOTOS_CONFIG` is worth doing.** The config search order ends
with `./config.toml`, so without it, running from a directory that happens to
contain a `config.toml` would silently use the wrong one. If you are ever
unsure, `harelphotos config show` prints which file it actually read.

### First time

`init` needs `-c` spelled out, because the config does not exist yet for
`$HARELPHOTOS_CONFIG` to point at:

```sh
harelphotos -c ~/.config/harelphotos/config.toml init --photo-root ~/pictures
```

Then set the two variables above, and:

```sh
harelphotos user add nyh --display-name Nadav --admin
harelphotos scan
harelphotos check
```

### Every time after that

With the variables set, that is the whole of it:

```sh
harelphotos scan            # after adding or changing photos
harelphotos check           # what is indexed, and anything wrong
```

### Trying it out without committing to anything

Nothing is written outside the state directory you name, so a scratch location
is a fine way to experiment, and `rm -rf` undoes all of it:

```sh
harelphotos -c /tmp/hp/config.toml init \
    --photo-root /home/nyh/mnt/pictures --state-dir /tmp/hp/state
export HARELPHOTOS_CONFIG=/tmp/hp/config.toml
harelphotos scan --dir 2019       # just one year, rather than the whole tree
harelphotos check
```

`init` creates the config file, the state directory, an empty accounts file, a
random secret key, and starter privacy/terms text. It is safe to run twice: it
never overwrites a config you have edited, and it never regenerates the secret
key (doing so would log everyone out).

**Your photos are never written to.** The only thing that ever appears in the
photo tree is `.album.toml` files, which you create yourself.

---

## Where things live

`init` sets these up; `config show` prints where they ended up.

| | default | what it is |
|---|---|---|
| `photo_root` | you choose | your photos. Read-only to this software |
| `index_db` | `<state>/index.sqlite` | the index. **A cache** — delete it and rescan |
| `derived_root` | `<state>/derived` | generated images. **A cache** — rebuildable |
| `users_file` | next to `config.toml` | accounts, mode 0600 |
| `secret_key_file` | next to `config.toml` | 32 random bytes, mode 0600 |
| `geonames.sqlite` | `<state>/` | place-name dataset, if installed. Re-downloadable |

Back up `photo_root` and the config directory. Do **not** bother backing up the
index or the derived tree; both are reproducible from your photos.

---

## Commands

### `harelphotos init --photo-root DIR [--state-dir DIR]`

Create configuration and state. Idempotent.

### `harelphotos scan`

Index the photo tree. Safe to interrupt and re-run.

| option | |
|---|---|
| `--dir SUBPATH` | work only within this subdirectory, in every phase |
| `--jobs N` | parallel workers (default: all cores) |
| `--limit N` | stop after N photos, to chip away at a big backlog |
| `--full` | re-read all metadata and regenerate all images |
| `--repair` | regenerate images whose files have gone missing |
| `--no-images` | index metadata only, generate no images |
| `--dry-run` | report what would happen, write nothing |
| `--force-unlock` | remove a lock left behind by a killed run |

A scan runs in three visible phases, each with its own progress line:

```
scanning /srv/photos/2024/08
  looking for photos: 12 directories, 4,318 photos · 287/s
  reading metadata: 146/4,318 · 17.2/s · ETA 4:02
  generating images: 92/4,318 · 4.9/s · ETA 14:11
```

The first phase has no total to count towards — it is still finding out how
much there is — so it shows what it has found so far. On a network mount this
phase can take a while on its own, because every file needs a round trip.

"Reading metadata" means reading just the *start* of each photo file — the part
holding the date, camera, GPS and dimensions — without decoding the picture
itself. It is quick, which is why it is a separate phase from the slow one.

**Generating images is much slower than reading metadata** — a few photos per
second per core, against tens or hundreds per second — so most of the wait is
the second line. The rate shown settles after the first few seconds; ignore the
wild ETA at the very start.

Only one `scan` may run at a time; a second exits with a message naming the
first one's process. Photos that cannot be read are recorded and reported by
`check` rather than stopping the run.

**Interrupting a scan is safe.** Ctrl-C and re-run: metadata and images are
committed as it goes, and a photo is only recorded as done once every one of
its images is safely on disk. You lose at most the last batch of image work,
which gets redone. A killed encode can leave a stray temporary file behind;
`harelphotos gc` clears those, and nothing ever serves them.

### `harelphotos check [--verify-files]`

Read-only report: how much was indexed, how much has EXIF dates and GPS, the
date range, the biggest directories, restricted directories, directories with
no photos, and anything that went wrong. Exits non-zero if there are problems.

`--verify-files` additionally checks that every image the database says it
generated is actually on disk. Worth running if you have deleted part of the
derived tree, or after an interrupted copy: because the recipe fingerprint
still matches, an ordinary rescan would skip those photos forever. The fix it
suggests is `scan --repair`.

### `harelphotos stats`

Counts, the size of the derived tree broken down per tier with average file
sizes, and the biggest directories. The quickest way to see what the images are
costing you in disk.

### `harelphotos gc [--deep] [--dry-run]`

Remove generated images that are no longer wanted: tier directories left behind
after changing the size ladder, and temporary files from an interrupted encode.
`--deep` walks the whole derived tree looking for files with no matching photo,
which is the only way to find files orphaned by a crash — by definition the
database never learned about those. `--dry-run` reports without deleting.

### `harelphotos geocode [--force]`

Turn GPS coordinates into place names like `Náxos, South Aegean, Greece`.

This never opens a photo: the coordinates are already in the index from the
scan, so it is a database operation taking seconds, not a re-read of your
collection. It needs the place-name dataset (below). `--force` re-resolves
photos that already have a place, after a dataset update.

`scan` does not geocode automatically; run this once after the dataset is
installed, and again when you add photos with GPS.

### `harelphotos serve [--bind ADDR] [--port N]`

Run the web interface. Listens on `127.0.0.1:5000` by default, so only your own
machine can reach it:

```sh
harelphotos serve
```

Then open <http://127.0.0.1:5000/>.

If it says *Address already in use*, something else holds port 5000. Find it
with `ss -ltnp | grep :5000`, or just pick another port with
`--port 5001`.

**`serve` never scans.** It only reads what is already in the index, so run
`harelphotos scan` first — and again whenever you add photos. If some photos
have no images generated yet, `serve` says so on startup rather than leaving
you with a page full of broken thumbnails and no explanation.

After upgrading the software, run `scan` once: if the way images are produced
has changed, it regenerates them by itself, without your having to know that
anything changed.

**There is no authentication yet.** Binding anywhere other than localhost hands
your whole collection to anyone on the network; the command warns you if you
do. Logging in arrives in M5, and the server deployment (Apache, TLS) in M8 —
until then this is the development server and should stay on localhost.

What you can do: browse albums, follow subdirectories, click a photo to see it
large, move between photos with the arrow keys or by swiping, press `i` for
date/camera/location details, `d` to download the original, and `Esc` to go
back to the album.

`Esc` and a downward swipe return you to the album **exactly where you left
it**, including after paging through several photos with the arrow keys. The
album remembers your position for the rest of the browser session, so the Back
button and `Esc` behave the same way.

### `harelphotos init --geonames`

Download and build the offline place-name dataset that `geocode` uses. Run it
once:

```sh
harelphotos init --geonames
```

It fetches about 14 MB from [geonames.org](https://www.geonames.org/) —
`cities500.zip` plus the country and region name tables — and builds
`geonames.sqlite` (~18 MB, 235,694 populated places worldwide) next to your
index. The downloads are cached, so re-running it does not re-fetch.

**This is the only time the software makes a network connection**, and it is
deliberate that it happens here rather than during a scan. The alternative —
asking an online geocoding service about each photo — would mean sending the
coordinates of every photo your family has taken, including your house, to a
third party. Doing it from a local dataset means the coordinates never leave
your machine.

The data is from GeoNames under CC BY 4.0; attribute it if you publish
anything derived from it.

### `harelphotos config show`

Print the effective configuration — the quickest way to confirm the file you
edited is the file being read.

### `harelphotos user ...`

```
harelphotos user add NAME [--display-name X] [--admin]
                         [--google EMAIL] [--google-only]
harelphotos user list
harelphotos user passwd NAME
harelphotos user del NAME
harelphotos user revoke NAME     # invalidate that user's existing sessions
```

`NAME` is the identity used in `.album.toml` access lists. For a Google-only
account, use the email address as the name. (Nothing uses these accounts yet —
logging in arrives with the web interface in M5.)

Three commands are still stubs and will say so: `cover` (set an album's cover
photo from the command line), `acl` (inspect or set who may see a directory),
and `sync` (copy the generated images to the server).

---

## The generated images

`scan` produces, for every photo, a set of downsized copies under
`derived_root`. Those are what a browser will actually load; the originals are
only ever sent when someone asks to download one.

Four sizes by default — 256 and 512 px for grid thumbnails, 1280 and 1600 px
for viewing a single photo — encoded as AVIF. They are laid out by pixel size:

```
derived/256/2019/summer/IMG_1234.jpg.avif
derived/512/2019/summer/IMG_1234.jpg.avif
```

Things worth knowing:

- **Nothing is ever upscaled.** A photo smaller than a tier simply has no file
  for it, and the database records which tiers really exist. A 400 px scan gets
  one 256 px copy and nothing else.
- **Aspect ratios are preserved**; no image is cropped.
- **Metadata is stripped** from the generated images — smaller files, and no
  GPS coordinates travelling with a thumbnail somebody saves.
- **Writes are atomic**, so interrupting a scan never leaves a half-written
  image. Re-running continues where it stopped.
- Regenerating everything is a matter of changing `recipe_version`, a size, or
  a quality in `config.toml`: the fingerprint changes and the next scan rebuilds
  what it must.

To see what they cost, `harelphotos stats`.

## How the browser picks an image size

Each photo is generated at several sizes (256, 512, 1280, 1600 px by default),
and **the server does not choose between them** — the page offers all of them
and the browser picks:

```html
<img src="/i/256/2019/summer/IMG_1234.jpg?v=a1b2c3"
     srcset="/i/256/2019/summer/IMG_1234.jpg?v=a1b2c3 256w,
             /i/512/2019/summer/IMG_1234.jpg?v=a1b2c3 512w"
     sizes="(max-width: 600px) 33vw, 180px">
```

`srcset` lists the available files with their real pixel widths; `sizes` says
how large the picture will actually appear on the page. The browser combines
those with the one thing the server cannot know — the device's pixel ratio —
and downloads exactly one of them.

That is why there are two sizes for each purpose rather than one:

- **256 and 512 for the grid**, plus the bigger ones when a tile is large
  enough to need them. An ordinary screen takes the 256 for most thumbnails; a
  retina screen takes the 512, and for a wide photo — which a justified row
  makes much wider than it is tall — one of the larger sizes. Album pages are
  where the bandwidth goes (500 thumbnails is 2.5 MB at 256 against 6.3 MB at
  512), so it matters that a non-retina screen is not made to pay retina
  prices.
- **1280 and 1600 for viewing one photo.** The photo is letterboxed to fit
  inside the window, so the limit is the available *height*, not the window
  width: measured in a browser, a 4:3 photo in a 1920x1080 window renders only
  1079 px wide, and the most demanding case — a retina laptop — needs 1676. So
  1600 is the top size; 2048 was measured to buy nothing and cost more than
  half the derived tree.

Sizes larger than the original are never generated. A photo that falls between
two tiers — 1174 px, say — gets a copy at *its own* size rather than being
dropped to the next tier down, so nothing loses resolution it had.

### What the photo count and dates at the top of an album mean

Both are **recursive**: they cover the album and everything beneath it, so a
directory holding only subdirectories still shows a total. The dates are the
span of the oldest to newest photo in that whole subtree.

They describe **what is in the index**, not what is on disk. If a scan is still
running, or you have only scanned part of the tree with `--dir`, the number is
the count of what has been indexed so far and will look too small. It is
brought up to date at the end of every scan, so a completed `harelphotos scan`
makes it right.

A date that looks wrong usually is: a photo with no date recorded falls back to
the file's timestamp, which for a scan is when it was scanned rather than when
it was taken. `harelphotos check` reports what fraction of your photos have a
real date.

### If thumbnails look soft

They should not, but if they do, in order of likelihood:

1. **Check the tier being fetched.** Watch the server output while loading an
   album: the URLs contain the size, as in `GET /i/512/...`. If a thumbnail
   renders wider on screen than the number in its URL, it is being upscaled —
   that is a bug, please report it.
2. **Raise the quality.** The defaults are deliberately frugal: `quality` in
   `[encode]` is 52 at 256 px and 48 at 512 px. Raising those to, say, 62 and
   58 costs roughly a third more disk for the grid. Then run `harelphotos
   scan`, which notices the change and regenerates. Note that changing quality
   re-encodes **everything**, unlike changing `view`, which only makes the
   sizes that changed.
3. **The photo may simply be small.** An old scan or a 2003 camera photo has
   less detail than a modern phone image, and nothing can add it back. The
   info panel on the photo page shows the original's dimensions.

## How a rescan decides what changed

This is the part worth understanding, because it is what makes rescanning a
300 GB collection take seconds instead of hours.

**A file with the same size and modification time is considered unchanged, and
is not read at all** — the same rule `rsync` and `make` use. On a rescan where
nothing has changed, no photo is opened.

When size or mtime *does* differ, the file is not immediately treated as new.
The scanner reads its first 128 KB and last 64 KB and compares a signature of
those bytes against the stored one:

- **signature identical** → only the timestamp changed. The stored data stays
  valid and nothing downstream is regenerated.
- **signature differs** → the photo really changed; re-read and re-derive it.

That second check exists for one specific reason. Tools that make the file date
match the EXIF date — `jhead -ft`, `exiftool "-FileModifyDate<DateTimeOriginal"`,
plain `touch` — rewrite every mtime without altering a single pixel. Under the
size-and-mtime rule alone, that is indistinguishable from "all 98,000 photos
changed", and would trigger a full re-encode. With the signature check, the same
operation costs a few minutes of reading and regenerates nothing.

So: **mtime decides whether to look. The signature decides whether to work.**

`--full` forces all metadata to be re-read and all images regenerated — useful
after upgrading, or when you want to be certain everything is current.

---

## Per-directory settings: `.album.toml`

Album metadata lives in the photo tree, not in a database, so it moves with the
photos and is editable with any text editor. Every key is optional, and a
directory with no `.album.toml` behaves sensibly.

```toml
title       = "Summer in Greece"      # default: the directory name
description = "Two weeks on Naxos."
cover       = "IMG_1234.jpg"          # or "subdir/IMG_9.jpg"; default: first photo
sort        = "date"                  # date | exif | mtime | name; "-" reverses
dirsort     = "name"                  # order of SUBDIRECTORIES; "-name" = newest first
order       = ["passover", "summer"]  # pin these subdirectories first, in this order
sort_key    = "1975"                  # how THIS directory sorts inside its parent
hidden      = false                   # omit from the parent's listing
allow       = ["nyh", "@family"]      # who may see this directory and everything under it
allow_replace = false                 # true = ignore restrictions inherited from above
location    = "Naxos, Greece"         # place name for photos with no GPS

[photos."IMG_1234.jpg"]
title  = "Nadav on the beach"
hidden = true
```

**A mistake in one of these files never breaks anything.** A malformed file, an
unknown value, a wrong type — each is reported by `check`, the affected setting
falls back to its default, and the directory is scanned normally.

Two ordering details worth knowing:

- Sorting is **natural**, so `Day 2` comes before `Day 10` and `2009` before
  `2010`.
- `sort_key` is how you place a directory whose name will not sort where you
  want. An `ancient` directory among year directories can carry
  `sort_key = "1975"` and land in the right place without being renamed.

### Access control

`allow` lists who may see a directory **and everything beneath it**.
Restrictions accumulate downwards: a user must satisfy every `allow` list on
the path from the top, so putting one on a directory locks its whole subtree
and nothing deeper can accidentally re-open it. `allow_replace = true` is the
deliberate exception, discarding what was inherited.

Names come from `users.toml`. `@name` refers to a group defined under
`[groups]` in `config.toml`; groups may contain other groups.

---

## Accounts: `users.toml`

Written by `harelphotos user`, and equally editable by hand.

```toml
[users.nyh]
name     = "Nadav"
password = "scrypt:32768:8:1$..."     # omit for a Google-only account
google   = "someone@gmail.com"        # may also sign in with this Google account
admin    = true                       # bypasses all access lists

[users."dad@gmail.com"]
name   = "Dad"
google = "dad@gmail.com"
```

Bump a user's `epoch` (or run `user revoke`) to invalidate their existing
sessions everywhere — for a lost phone, or someone who should no longer have
access.

---

## Global settings: `config.toml`

`init` writes a commented file with sensible defaults. The settings that
matter today:

```toml
photo_root      = "/srv/photos"       # home machine: "/home/nyh/pictures"
derived_root    = "/var/lib/harelphotos/derived"
index_db        = "/var/lib/harelphotos/index.sqlite"
users_file      = "/etc/harelphotos/users.toml"
secret_key_file = "/etc/harelphotos/secret_key"

[scan]
jobs    = 0                           # 0 = all cores
nice    = 10
exclude = [".*", "@eaDir", "Thumbs.db"]

[groups]
family = ["nyh", "dad@gmail.com"]
```

Settings for image sizes, encoding and the web interface are written by `init`
but are not used yet.

The config file is found via `-c`, then `$HARELPHOTOS_CONFIG`, then
`./config.toml`, `~/.config/harelphotos/config.toml`, `/etc/harelphotos/config.toml`
— see [Running the commands](#running-the-commands) for why it is worth setting
the environment variable rather than relying on the search.

---

## What is not indexed

Only `.jpg` and `.jpeg` files (either case) are indexed. Everything else in the
photo tree is ignored, including **HEIC images and videos** — so a directory
holding only those will appear empty. Support for them is planned but not
built.

Also skipped: anything matching `scan.exclude` (dotfiles, `Thumbs.db` and
`@eaDir` by default), and any filename that is not valid UTF-8 — those are
counted and listed rather than stopping the scan.

For reference, in the collection this was built for: 98,460 JPEGs, and also
4,069 HEIC images and 779 videos that are currently invisible.

---

## Two machines

The intended arrangement is a fast machine at home doing the expensive work and
a small server doing the serving. The index stores **relative** paths, so
`index.sqlite` can be copied between machines even though `photo_root` differs
on each. This matters more from M3 onwards, when there are generated images to
ship.
