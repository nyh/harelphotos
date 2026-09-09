# harelphotos

A self-hosted photo gallery for a personal collection — a Google-Photos-like
browsing experience over an ordinary directory tree of JPEGs, served from a
small Linux server to a handful of invited family members.

> **Status: design stage.** There is no code yet. This repository currently
> holds the plan; [`DESIGN.md`](DESIGN.md) is written to be followed.

## The problem

~300 GB of photos (~80,000 files) live in a hierarchical directory tree on a
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
| Derivative format | **AVIF**, four tiers (256/512 px grid, 1280/2048 px lightbox) delivered by `srcset`; WebP/JPEG served to older browsers by content negotiation. Measured 48% smaller than WebP at matched SSIM |
| Derived tree size | **~17 GB for ~80,000 photos** — 5.6% of the originals |
| Bulk encode | **~22 core-hours** (~1.8 h on 12 cores), run on a fast machine and rsynced to the server |
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

## Documents

- [`PLAN`](PLAN) — the original statement of intent.
- [`DESIGN.md`](DESIGN.md) — the full design: data model, scanner, image
  pipeline, web app, authentication, deployment, testing, and a milestone
  breakdown.
