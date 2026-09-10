# harelphotos

A private, self-hosted photo gallery over a directory tree you already have.

You point it at your photos, it builds an index and a set of resized copies
somewhere else, and it serves a fast, Google-Photos-shaped web gallery to the
handful of people you invite. It runs comfortably on a small, slow machine, and
a collection of a hundred thousand photographs is unremarkable for it.

## Who it is for

**People with a collection already organised into directories** — by year, by
month, by trip, by event, however you have kept it for the last twenty years.
There is no import step and no library format. Your folders *are* the albums,
your folder names are the album titles, and a subdirectory is a sub-album. If
you rename a directory, the album is renamed; if you move photographs between
them, the gallery follows on the next scan.

**People who want that collection left exactly as it is.** Nothing here ever
writes to your photo tree — not the scanner, not the web interface, not the
commands that set covers or access rules. Every piece of state this software
creates lives in a separate directory you nominate: the index, the resized
copies, the place-name database, the settings you change from the browser. The
deployment enforces it rather than trusting it, since the server process runs
with your photographs mounted read-only.

That combination is the point. The collection stays yours, in the shape you
made it, readable by any other program, and a backup of it is still just a copy
of your photographs.

## What it does

- **Browses the tree.** Subdirectories appear as album cards with a cover and a
  photo count; photographs appear below them in a justified grid that never
  crops. Sorted by the date the photograph was taken, not by filename.
- **Serves the right image size.** Four sizes of every photo are generated
  ahead of time in AVIF, and the browser picks from them — a phone fetches a
  phone-sized file, a desktop does not. No image processing happens during a
  request.
- **Names where photographs were taken**, from a local dataset: "Náxos,
  Greece", and optionally the airport, park, museum or monument you were
  standing in. Nothing is sent to a geocoding service; the coordinates of every
  photograph your family has taken never leave the machine.
- **Keeps it private.** Invited accounts only, with passwords or Google
  sign-in. Any directory can be restricted to particular people, and
  restrictions accumulate down the tree.
- **Behaves like a photo viewer.** Arrow keys and swipes move between
  photographs, Escape returns to the grid where you left it, the next and
  previous images are fetched before you ask for them, and there is a panel
  with the date, camera, exposure and place.
- **Installs on a phone**, so it opens from the home screen without a browser
  address bar.

## Getting started

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
harelphotos init --photo-root ~/pictures --state-dir ~/.local/share/harelphotos
harelphotos scan
harelphotos serve
```

That gives you the whole gallery on `http://localhost:5000`, with no login and
nothing exposed. The first scan is the slow part — it reads every photograph
and encodes four sizes of each — but it is interruptible, resumable, and does
not repeat work on later runs.

`serve` is a development server, bound to the loopback address and with the
login gate off, which is right for looking at your own photographs on your own
machine and wrong for anything else. To run it properly — behind Apache with
TLS, as a systemd service, with accounts and invitations — see
[`INSTALL.md`](INSTALL.md).

Place names are optional, and separate because they mean downloading a dataset:

```sh
harelphotos init --geonames --landmarks
harelphotos geocode
```

The first downloads the place-name data — a small file of towns worldwide, and
a much larger one, several hundred megabytes, of airports, parks, museums and
monuments. The second turns each photograph's coordinates into a name like
"Náxos, Greece", or the airport you were standing in. Both are offline
afterwards, and no coordinates are ever sent anywhere.

## Keeping it up to date

Add, delete, rename and rearrange photographs and directories as you like; the
gallery follows. Re-run the two commands afterwards:

```sh
harelphotos scan
harelphotos geocode
```

A rescan only looks at what the filesystem says has changed and only re-encodes
photographs whose pixels actually differ, so it costs a fraction of the first
one. `harelphotos gc` reclaims the space left by photographs you deleted.

[`MANUAL.md`](MANUAL.md) is the guide to everything else: the commands, the
`.album.toml` settings, access control, covers, hiding a directory, accounts.

## How it works, briefly

**The filesystem is authoritative.** Your photographs and any `.album.toml`
files you write beside them are the only inputs. The SQLite index and the whole
tree of resized images are caches: delete either and a rescan rebuilds it.

**All the cost moves to scan time.** A page view is a few indexed SQLite reads
and a template render; the image bytes are static files the web server sends
directly. That is what lets it be quick on a slow machine.

**A rescan does as little as possible.** It compares modification times to
decide what to look at, and content fingerprints to decide what to re-encode,
so touching a file's timestamp costs nothing and re-encoding only happens when
the pixels actually changed.

Four dependencies — Flask, Pillow, requests, gunicorn — and no build step, no
JavaScript framework, and no database server.

## Documents

- [`MANUAL.md`](MANUAL.md) — **how to use it.** Start here.
- [`INSTALL.md`](INSTALL.md) — putting it on a server with TLS.

Of secondary interest, kept for the record:

- [`IDEAS.md`](IDEAS.md) — what might come next, and what deliberately should not.
- [`DESIGN.md`](DESIGN.md) — the design it was built from, including the
  measurements behind the decisions.
- [`PLAN`](PLAN) — the original statement of intent, before any of it existed.

## Licence

GNU Affero General Public License, version 3 or later. The full text is in
[`LICENSE`](LICENSE); every source file carries an SPDX identifier.

    Copyright (C) 2026 Nadav Har'El

    This program is free software: you can redistribute it and/or modify it
    under the terms of the GNU Affero General Public License as published by
    the Free Software Foundation, either version 3 of the License, or (at your
    option) any later version.

    This program is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
    FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License
    for more details.

    You should have received a copy of the GNU Affero General Public License
    along with this program. If not, see <https://www.gnu.org/licenses/>.

The Affero variant is deliberate: this is software people reach through a
browser, and section 13 asks that anyone running a *modified* version offer its
source to the people using it. If you do, point `[ui] source_url` in your
configuration at your own repository — the page footer already shows that link.

The place-name data is not covered by the above. It comes from
[GeoNames](https://www.geonames.org/) under CC BY 4.0 and is downloaded at run
time rather than distributed here.
