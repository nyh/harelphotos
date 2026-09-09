# harelphotos — manual

What the software does **today**, and how to use it.

This describes only what is implemented and working. For why things are the way
they are — the measurements, the alternatives rejected, the plan for what isn't
built yet — see [DESIGN.md](DESIGN.md). Where the two disagree, this file is
right and DESIGN.md is out of date.

> **Implemented so far:** indexing a photo tree (`init`, `scan`, `check`,
> `config show`, `user`). There is **no web interface yet** — that is M4. What
> exists is the command-line half: it reads your photos and builds an index.

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
| `derived_root` | `<state>/derived` | generated images (not built yet — M3) |
| `users_file` | next to `config.toml` | accounts, mode 0600 |
| `secret_key_file` | next to `config.toml` | 32 random bytes, mode 0600 |

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
| `--dir SUBPATH` | index only this subdirectory (and prune only within it) |
| `--jobs N` | parallel header readers (default: all cores) |
| `--limit N` | stop after N photos, to chip away at a big backlog |
| `--full` | re-read every photo header |
| `--dry-run` | report what would happen, write nothing |
| `--force-unlock` | remove a lock left behind by a killed run |

Only one `scan` may run at a time; a second exits with a message naming the
first one's process. Photos that cannot be read are recorded and reported by
`check` rather than stopping the run.

### `harelphotos check`

Read-only report: how much was indexed, how much has EXIF dates and GPS, the
date range, the biggest directories, restricted directories, directories with
no photos, and anything that went wrong. Exits non-zero if there are problems.

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

Commands not yet implemented (`serve`, `geocode`, `gc`, `stats`, `cover`,
`acl`, `sync`) exist and will tell you which milestone they belong to.

---

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

`--full` forces every header to be re-read (useful after changing what metadata
is extracted); it still compares signatures, so it does not invalidate anything
that has not genuinely changed.

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

---

## Two machines

The intended arrangement is a fast machine at home doing the expensive work and
a small server doing the serving. The index stores **relative** paths, so
`index.sqlite` can be copied between machines even though `photo_root` differs
on each. This matters more from M3 onwards, when there are generated images to
ship.
