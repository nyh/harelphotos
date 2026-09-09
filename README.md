# harelphotos

A self-hosted photo gallery for a personal collection — a Google-Photos-like
browsing experience over an ordinary directory tree of JPEGs, served from a
small Linux server to a handful of invited family members.

> **Status: in progress.** The indexing half works — see
> [`MANUAL.md`](MANUAL.md). There is no web interface yet.
> [`DESIGN.md`](DESIGN.md) is the plan being followed.

## The problem

~300 GB of photos (98,460 JPEGs in 739 directories, measured) live in a
hierarchical directory tree on a
CPU-weak, internet-connected Linux server. The goal is a fast, pretty, private
web gallery over exactly that tree — no import step, no library format, no
database that becomes the source of truth.

## Approach

**The filesystem stays authoritative.** Photos and per-directory `.album.toml`
files are the only real state. The SQLite index and the entire derived-image
tree are caches: delete either and a rescan rebuilds it.

**All the cost moves to scan time.** `harelphotos scan` walks the tree, detects
what changed, and pre-generates every image size. A page view is then a few
indexed SQLite reads plus a template render; the image bytes are static files
the web server sends directly. No image processing ever happens in a request.

## Design decisions

Chosen from measurements rather than taste — the numbers behind each are in
[`DESIGN.md`](DESIGN.md):

| | |
|---|---|
| Derivative format | **AVIF**, four tiers (256/512 px grid, 1280/1600 px viewing) delivered by `srcset`; WebP/JPEG served to older browsers by content negotiation. Measured 48% smaller than WebP at matched SSIM |
| Derived tree size | **~14 GB for 98,460 photos** — under 5% of the originals |
| Bulk encode | **~22 core-hours** (~1.9 h on 12 cores), run on a fast machine and rsynced to the server |
| Change detection | mtime decides whether to *look*; a content signature decides whether to *work*, so re-dating files doesn't trigger a mass re-encode |
| Photo grid | justified rows, true aspect ratios, **never cropped** |
| Album listing | separate section, uniform cards with the name captioned below |
| Metadata | per-directory `.album.toml` — title, cover photo, sort order, access list |
| Access control | local accounts and/or Google Sign-In, against an allowlist; restrictions inherit down the tree |

## Planned stack

Python 3.11+, and four dependencies: **Flask**, **Pillow** (which bundles the
AVIF encoder), **requests**, **gunicorn** — behind Apache on the server. No
build step, no npm, no ORM, no job queue, no database server. Targets Fedora and
Rocky Linux 9, installed identically on both from one virtualenv.

## Before implementation starts

A short list of things on the real machines that no code will do — see
[§0.1 of the design](DESIGN.md#01-manual-steps--things-no-code-will-do-for-you).
The one with real work behind it: **on the server, the photos must be moved out
of the home directory** (e.g. to `/srv/photos`), which removes the need for
POSIX ACLs, an SELinux boolean and a weakened `ProtectHome`. On the home
machine they stay exactly where they are.

## Documents

- [`MANUAL.md`](MANUAL.md) — **how to use what exists today.**
- [`PLAN`](PLAN) — the original statement of intent.
- [`DESIGN.md`](DESIGN.md) — the full design: data model, scanner, image
  pipeline, web app, authentication, deployment, testing, and a milestone
  breakdown.
