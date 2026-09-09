# harelphotos — manual

What the software does **today**, and how to use it.

This describes only what is implemented and working. For why things are the way
they are — the measurements, the alternatives rejected, the plan for what isn't
built yet — see [DESIGN.md](DESIGN.md). Where the two disagree, this file is
right and DESIGN.md is out of date.

> **Implemented so far:** indexing a photo tree, generating its images,
> browsing them in a web interface, and logging in with a local account. Not
> yet: Google sign-in, and the real server deployment behind Apache with TLS.

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

The account is only needed once you want the login page (`serve --login`); the
local server does not require one by default.

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
| `auth.sqlite` | `<state>/` | failed-login counts. Safe to delete; resets the backoff |

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

### `harelphotos serve [--login] [--bind ADDR] [--port N]`

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

**No login is required by default.** This is the development server on your own
machine, and typing a password to look at your own photos is friction for
nothing. To exercise the real login page instead:

```sh
harelphotos serve --login
```

You will need an account first — see [`user add`](#harelphotos-user--creating-accounts).
Pages served without a login carry a banner saying so, so a window left open
for a week cannot be mistaken for the real thing.

Binding anywhere other than `127.0.0.1` **without** `--login` is refused
outright, because it would hand the whole collection to anyone who can reach
the machine. Use `--login` (recommended), or `--insecure` if you really mean
it — testing on a phone over your own LAN, say.

What you can do: browse albums, follow subdirectories, click a photo to see it
large, move between photos with the arrow keys or by swiping, press `i` for
camera and exposure details, `d` to download the original, and `Esc` to go
back to the album.

A photo's date and place are shown next to its filename, without opening
anything: they are what you want to know while looking at a photo, whereas the
camera settings are for when you go looking. On a narrow screen they wrap onto
their own line rather than squeezing the filename. The place comes from the
photo's own GPS, turned into a place name by [`geocode`](#harelphotos-geocode);
for photos with no GPS it falls back to `location` in the album's `.album.toml`,
and `show_gps = false` in `config.toml` suppresses both.

`Esc`, a downward swipe and the browser's Back button all return you to the
album **exactly where you left it**, in one step, however many photos you
paged through first.

That last part is deliberate. Paging with the arrow keys replaces the current
page rather than stacking a new one, so viewing twenty photos does not put
twenty entries in your history for Back to walk back through — Back means
"return to the grid", not "the previous photo". Use the left arrow for that.

Returning is also a real history step, which lets the browser restore the
album from its back/forward cache: the grid is already laid out and the
thumbnails already loaded, so it appears instantly instead of being rebuilt.
Arriving straight at a photo — a bookmark or a pasted link — has no album
behind it, so `Esc` navigates to the album normally instead.

The photo is sized to the space actually available, measured after the page is
laid out rather than assumed, and the whole page is one screenful — bar, photo,
nothing to scroll. When vertical space is scarce, a phone held sideways or a
short window, the top bar shrinks to about a third of its height rather than
floating over the photo: covering part of the picture to make the rest slightly
bigger is the wrong trade on a page whose entire purpose is looking at it.

While you are looking at a photo, the ones on either side of it are fetched in
the background, so paging with the arrow keys does not wait for the network.
The browser picks the size for those the same way it does for the visible
image, from `srcset` — a phone does not pull the 1600px file just because it is
next. The prefetch starts only after the photo you are actually looking at has
loaded, so it never competes with it.

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

### `harelphotos user ...` — creating accounts

```sh
harelphotos user add nyh --display-name Nadav --admin
```

That prompts for a password twice and writes the account to `users.toml`. To
set one non-interactively — handy for a throwaway test account, but it lands in
your shell history, so don't do it for a real password:

```sh
harelphotos user add nyh --password nyh --display-name Nadav --admin
```

Then log in at <http://127.0.0.1:5000/> with `nyh` / `nyh`, remembering that
`serve` needs `--login` for the login page to appear at all.

The rest:

```sh
harelphotos user list                        # who exists, and how they log in
harelphotos user passwd NAME                 # change a password
harelphotos user del NAME                    # remove an account
harelphotos user revoke NAME                 # invalidate their live sessions
harelphotos user add NAME --google EMAIL     # may also use Google sign-in
harelphotos user add EMAIL --google-only     # Google only, no password
```

`NAME` is the identity that appears in `.album.toml` access lists, so keep it
short and stable. For a Google-only account, use the email address as the name.

`--admin` bypasses every access list — useful for yourself, and for nobody
else. `revoke` is what to reach for if a phone is lost or a relative should
stop having access; it invalidates their existing sessions everywhere without
deleting the account.

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
location    = "Naxos, Greece"         # shown on the album; also the place for
                                      # photos in it that have no GPS

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

## Links you can write by hand

The URLs mirror your directory tree, so you can construct one for any album or
photo without clicking your way there — useful for emailing someone a specific
album, or bookmarking one.

| what | URL | example |
|---|---|---|
| an album | `/a/<directory path>/` | `/a/2024/08/usa/helicopter/` |
| the top album | `/a/` | |
| one photo | `/p/<directory path>/<filename>` | `/p/2024/08/usa/helicopter/IMG_0004.JPG` |
| the original file | `/orig/<directory path>/<filename>` | `/orig/2024/08/usa/helicopter/IMG_0004.JPG` |

So with the site at `https://photos.example.com`, the album living in
`2024/08/usa/helicopter` is at
`https://photos.example.com/a/2024/08/usa/helicopter/`.

Details that save guessing:

- **The path is exactly the directory path** under `photo_root`, and the
  filename exactly as on disk — including its capitalisation, so `.JPG` and
  `.jpg` are not interchangeable here even though the scanner accepts both.
- **A missing trailing slash on an album redirects**, so `/a/2024/08` works.
- **Spaces and other odd characters** are best percent-encoded (`%20` for a
  space), though pasting the plain form into a browser works too, because the
  browser encodes it for you.
- **A path that does not exist gives "Not found"** — as does one you are not
  allowed to see, deliberately, so that a private album cannot be detected by
  probing for it.

**Sharing a link with someone who is not logged in works.** They get the login
page, and land on the album or photo you sent them once they have logged in
(see [Logging in](#logging-in)) — they do not end up at the top level having to
find it again.

The `/i/512/...` URLs you may notice in the page source are for the resized
copies, and are not meant to be shared: they carry a fingerprint that changes
whenever the image is regenerated. Use `/p/...` for a photo, or `/orig/...` if
you specifically want to hand someone the original file.

## Why the URLs start with `/a/`

Album paths come from your filesystem, so a directory could be called anything
— including `login` or `static`. Keeping albums under `/a/` (and photos under
`/p/`, images under `/i/`) gives them a space of their own where any name is
safe, instead of having to forbid names that collide with a page of the site.

## Logging in

With `serve --login`, everything requires an account: the only pages a stranger
can reach are the front page, the login form, and the privacy and terms text.
Requests for images or JSON are refused outright rather than answered with a
login page, so a stale tab does not fill up with HTML where pictures should be.

A few details that are deliberate:

- **A link you send to family works when they are logged out.** Opening
  `/a/2019/summer/` shows the login page and returns you to that album
  afterwards, rather than dumping you at the top level.
- **Logging out is a button, not a link.** A plain link would be followed by
  link prefetchers, mail scanners and preview bots, each of which would
  silently log you out.
- **A wrong password never says which part was wrong**, and an unknown username
  takes just as long as a real one, so the site does not reveal who has an
  account.
- **Repeated failures are slowed down.** After three wrong guesses from the
  same address for the same username, each further attempt has to wait — two
  seconds, then four, then eight, capped at thirty. A correct password clears
  the count, so a relative mistyping theirs twice notices nothing. The point is
  that the site is on the open internet: without it, someone could try
  passwords as fast as the server can check them.

  It is a precaution, not a gate. If the counter cannot be written for any
  reason, the login still proceeds and a warning goes to the log — refusing a
  correct password because a counter failed would be the worse outcome by far.
  The counts live in `auth.sqlite`, separate from the index, so that a running
  scan and someone logging in never contend.
- **Sessions last 30 days** and survive a restart. They stop working
  immediately if you delete the account or run `user revoke`.

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

base_url        = "https://photos.example.org"
session_days    = 30                  # how long a login lasts
behind_proxy    = false               # true when Apache is in front
sendfile_header = "auto"              # "X-Sendfile" with mod_xsendfile

[scan]
jobs    = 0                           # 0 = all cores
nice    = 10
exclude = [".*", "@eaDir", "Thumbs.db"]

[groups]
family = ["nyh", "dad@gmail.com"]
```

The three that only matter on a server:

- **`base_url`** must be exactly what the browser asks for, with no trailing
  slash. It decides whether the session cookie is marked `Secure`, so getting
  it wrong makes logging in fail by silently returning you to the login page.
- **`behind_proxy`** makes the application believe Apache's `X-Forwarded-For`
  and `X-Forwarded-Proto`. Off by default because trusting those headers on a
  directly-reachable server would let anyone claim any address. Behind Apache
  without it, every request looks like `127.0.0.1`, so one person mistyping a
  password throttles everybody.
- **`sendfile_header = "X-Sendfile"`** lets Apache send image bytes itself once
  the access check has passed, instead of copying them through Python. Needs
  `mod_xsendfile` and a matching `XSendFilePath`.

**`session_days`** is counted from when you log in, not from last use. The
session cookie is deliberately not re-sent on every response: its value is part
of the browser's cache key for images, so a cookie that changed each time threw
away every cached thumbnail and made each page load re-download the whole grid.

Every key has a default, so a `config.toml` written by an older `init` keeps
working and picks up the default for anything it does not mention. That cuts
both ways: because `init` bakes the current defaults into the file it writes, a
later change to a *code* default will not reach a config that already names the
setting. If a new default does not seem to be taking effect, look for the old
value still sitting in your file.

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
