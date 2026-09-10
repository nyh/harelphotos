# Ideas for later

Not a plan, and not a promise. Things worth considering once the first full
scan has finished and the album has been lived in for a while, roughly in the
order I would argue for them. Each says what it costs and, where it matters,
why it might be a bad idea.

Written at the end of the M1–M9 work, so it reflects what actually turned out
to be true rather than what the design predicted.

---

## The gaps that would bite a family album

### 1. HEIC, and probably video

`PHOTO_EXTENSIONS = {".jpg", ".jpeg"}`. Every iPhone since 2017 writes HEIC by
default, so the first relative with an iPhone contributes photographs the
album cannot see at all — silently, since an unindexed file is simply absent.
The same for the `.mp4` clips a phone drops beside them.

HEIC needs `pillow-heif`, a fifth dependency but a well-known one, and the
derivative pipeline then works unchanged. Video is a much bigger question — a
poster frame is easy, playback and transcoding are not — and might reasonably
be answered with "the album shows a thumbnail and offers the original".

This is the largest functional gap in the project. Worth checking what the
family actually has before doing anything.

### 2. Search

There is none. Now that photos carry a place name, a date and camera details,
the useful queries are cheap to answer from the index: everything from Boston,
everything in August 2024, everything from a particular camera. A single search
box over `place`, `taken` and directory titles would be a few hundred lines and
would change how a 98,000-photo collection feels.

Full-text search over place names via SQLite FTS5 is built into the standard
library's SQLite; no new dependency.

### 3. A map

Coordinates are already in the index for every photo that has them. A map of
where a trip went, or of the whole collection, is the single most tourist-shaped
feature missing — and it is the one place where "we do not send your
coordinates anywhere" becomes awkward, since map tiles come from somebody.
Options: link out per photo as today, ship a static world outline and plot dots
on it, or accept a tile server for logged-in viewers only. The first is honest
and free; the second is a nice compromise; the third needs a decision about
what leaves the machine.

### 4. A link that needs no account

The whole design is "by invitation, everyone has an account". That is right for
the collection, and wrong for the case of sending one album to somebody who
will never make an account. A signed, expiring, revocable link to a single
album — read-only, no login — would fit the ACL model as another kind of
viewer, and is the feature most likely to be asked for by someone who is not
you.

Needs care: it is the first thing in the project that gives access without an
account, so it wants its own tests and a clear way to list and revoke what has
been shared.

---

## Operational, now that the numbers are real

### 5. Back up the derived tree, not just the config

The design says the index and the derivatives are a rebuildable cache, which is
true and was the right call. But the first full scan takes about two weeks on
this machine, which changes what "rebuildable" is worth. Losing
`/var/lib/harelphotos/derived` costs a fortnight of CPU, not an afternoon.

So it belongs on the backup list after all — not because the data is precious
but because the *time* is. A `harelphotos backup` that tars `users.toml`,
`secret_key`, `album-overrides.toml` and optionally the derived tree would make
that a decision rather than an oversight.

### 6. Scan the newest photographs first

`ORDER BY d.path, p.name`. A collection organised by year therefore fills in
from 2001 forwards, and the photographs taken last month — the ones anyone
actually wants to look at — arrive last, a fortnight later. Scanning by
descending EXIF date instead would make the album useful within hours.

Cheap to do, and it only matters during a long first scan, which is exactly
when it matters most.

### 7. A periodic rescan, and something that notices when it fails

A systemd timer running `scan` nightly, and `check --env --url` weekly with its
output mailed somewhere. The site can be broken in ways nobody notices for
months: a certificate that stopped renewing, a scan that has been failing since
a disk filled, an album that quietly shows nothing because a photo directory
was moved.

### 8. Retire or exercise `harelphotos sync`

It is written, tested against a local temporary directory, and has never run
against a real remote — because the decision was made to scan on the server
instead. Code that is never run does not stay working. Either exercise it once
properly and keep it, or delete it and let the git history hold it.

---

## Places and names

### 9. `geocode --explain LAT LON`

Every landmark bug this week was diagnosed the same way: dump the features
around a coordinate with distances, codes and populations, and see which rule
fired. That was ad-hoc `awk` over a downloaded country file each time. As a
command it would take an hour and would make the next report a two-minute
conversation instead of an afternoon.

### 10. A place name for an album

`[ui] location` in `.album.toml` already names a place for photographs that
have no GPS. The reverse is missing: no way to say "everything in this album is
the Grand Canyon" and have it beat a viewpoint 200 m away. The overrides file
is the natural home, and it would answer the one landmark limitation that
cannot be fixed with better rules — a feature so large that its recorded point
is tens of kilometres from where anyone stands.

### 11. Fewer surprises in dense cities

Known and deliberately unfixed: in a historic quarter something is always
within a couple of hundred metres, and nothing distinguishes a garden you are
sitting in from one you walked past. If it becomes annoying, the lever is to
name a landmark alongside a city only for categories that are destinations —
museums, monuments, historic sites — and never for a generic park. It costs the
good cases too, which is why it was not done.

---

## Smaller things

### 12. Duplicate detection

`content_sig` already identifies identical files, and a collection assembled
over twenty years from several cameras and backups certainly contains the same
photograph in several places. `harelphotos check --duplicates` would be a
report, not a delete button — the deleting should stay manual.

### 13. Zoom on a photo

Pinch and double-tap to zoom, drag to pan. The obvious thing to want from a
photo viewer on a phone, and currently absent: the image is fitted and that is
that.

### 14. A keyboard help overlay

`i`, `d`, arrows, Escape — none of them are discoverable. `?` showing a small
panel would fix that, and would be twenty lines.

### 15. Multiple downloads

Selecting several photographs and getting a zip. Wanted the first time somebody
says "can you send me the ones from the wedding". Streams from the derived tree
or the originals; needs a cap so nobody asks for 98,000 of them.

### 16. Dark mode by choice, not only by system

It follows `prefers-color-scheme` today. A toggle would need a control, a
preference stored per browser, and a decision about where the control lives —
which is why it was not done for someone who said they would not use it.

---

## Things I would not do

- **A database other than SQLite.** Nothing here has come close to needing one,
  and the read-only-to-the-web-process arrangement is what keeps a scan from
  taking the site down.
- **A JavaScript framework.** The whole client is one file, no build step, and
  every bug this week was found and fixed in minutes because of it.
- **Face recognition.** Technically feasible offline, and the single fastest
  way to turn a family album into something with a privacy policy nobody wants
  to read. If it is ever wanted, it wants a conversation first, not a commit.
- **Uploading photographs through the web interface.** The photo tree is
  read-only to this software, which is a load-bearing property — it is enforced
  by the systemd unit, and it is why a compromise of the web process cannot
  touch the originals.
