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
family actually has before doing anything. PNG is a separate and much smaller
question — see item 28.

### 28. PNG, and whether pictures that are not photographs belong here

`PHOTO_EXTENSIONS = {".jpg", ".jpeg"}`, so a PNG is not indexed and simply does
not appear. Nadav has `2026/AI` full of them — images made by an AI rather than
a camera — and the question is really two questions.

**Reading them is nearly free, unlike HEIC.** Pillow decodes PNG out of the
box, so there is no new dependency and nothing to install; the derivative
pipeline would work by adding one extension to that set. Three things behave
differently from JPEG and are worth knowing before flipping it on:

- **`im.draft()` does nothing.** The 1/2, 1/4, 1/8 decode trick in `derive` is
  a libjpeg feature, and the comment beside it — decode from ~450 ms to ~93 ms,
  "the single biggest win here" — applies to JPEG alone. On PNG the call
  returns `None` and the whole image is decoded. Measured on one of Nadav's at
  1408x768: about 30 ms, so it does not matter at these sizes, and it would at
  much larger ones.
- **Transparency is silently flattened.** `derive` does `im.convert("RGB")`,
  and a fully transparent pixel keeps whatever colour is under the alpha rather
  than being composited onto anything — a logo on transparency comes out with
  garbage behind it instead of white. AVIF supports alpha, so this is a choice
  to make rather than a limit to accept.
- **The quality ladder is tuned for photographs.** Lossy encoding of flat
  colour, text and hard edges is its weak case, and a screenshot is exactly
  that. AI art is usually photographic enough not to care; a screenshot of a
  conversation would not be.

Storage is not a concern either way. One of these PNGs is 1951 KB, and its
AVIF derivatives come to 6 KB, 16 KB and 63 KB at the 256, 512 and 1280 tiers.

**Dates are the real wrinkle, and it is the WhatsApp problem again.** These
files have no EXIF date. Pillow reports zero tags, and although there *is* a
`Raw profile type APP1` text chunk holding a genuine 6762-byte TIFF block —
complete with a "Picasa" software tag — there is no date in that either. The
file dates are all 29 August, the day the folder was copied.

The names carry it, though: `1784536337126.png` is a millisecond epoch, 20 July
2026 at 11:32. Four of the five are named that way and one is
`file_00000000230c820b86a55c3e92e8067b.png`, which is not. So
`contrib/fixup_whatsapp_dates.py` has an obvious sibling, or an obvious second
pattern — the same trick of putting the date where the scanner already looks,
with no change to the application at all.

**The second question is whether they belong in the album**, and it is not a
technical one. Nadav: "I don't know if I really want to save these AI creations
as photos, but maybe I will want to show art together with the photos." If the
answer is yes, the machinery already exists to keep them apart without keeping
them out — a directory of their own, with `hidden` if they should not appear in
listings, or an `allow` list, or simply a title that says what they are. Worth
deciding before turning the extension on, because afterwards they arrive
wherever they happen to sit in the tree.

### 2. Search

There is none. Now that photos carry a place name, a date and camera details,
the useful queries are cheap to answer from the index: everything from Boston,
everything in August 2024, everything from a particular camera. A single search
box over `place`, `taken` and directory titles would be a few hundred lines and
would change how a collection of a hundred thousand photographs feels.

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

### 24. Starred photographs, per person

Nadav's idea. A star on a photograph in the single-photo view, one's own and
nobody else's; a small star over the corner of a starred tile in the grid and
in the photo view; and a way to see the starred ones under any node of the
tree — at the root, everything starred anywhere; inside `2019/08`, what is
starred beneath `2019/08`. Ordered by date taken, not by where they sit in the
tree, because a list of favorites is a timeline and not a directory.

Numbered 24 and filed here rather than renumbering: the numbers are referred to
from elsewhere in this file, so they are identifiers and not an ordering.

**Where it is stored, and why not in the index.** Not `index.sqlite`. That
database is read-only to the web process and is locked by a running scan, which
is exactly the collision that drove login throttling out of it (DESIGN.md 12.1:
"database is locked" while a scan ran, and logging in failed). There is already
a second database for precisely this, `auth.sqlite` in the state directory,
and it is already the one thing the web process writes. Stars either join it or
get a third file beside it; joining it is less machinery, a separate file is
tidier if stars ever grow rows in bulk. Either way the rule stands: one writer
at a time, and never the scanner's database.

**Naming a photograph so a star survives a rename.** The index already computes
`content_sig` — `blake2b(size ‖ first bytes ‖ last bytes)`, in
`scanner.content_signature` — which is stable across renames and moves because
it never looks at the path. It is the obvious key, and it comes with three
consequences worth deciding on deliberately rather than discovering:

- **Two identical copies of a photograph share a signature**, so starring one
  in `2019/trip` stars the one in `chosen-pictures` too. That is arguably
  correct — it is the same photograph — but it is a decision, not an accident,
  and item 12 (duplicate detection) is the same fact viewed from the other
  side.
- **It is a cheap fingerprint, not a full hash.** Two different files could
  collide in principle. For a star the cost of a collision is one wrongly
  marked photograph, which is tolerable; it would not be tolerable for
  anything that deleted data.
- **It is NULL until phase 2 of a scan has read the file.** A star cannot be
  placed on a photograph the scanner has only walked past, so the UI needs to
  not offer it, rather than fail.

**Whose star.** `Viewer` carries `token` and `name`. The token is the session's,
so it is the wrong key — a new login would orphan every star. The account name
from `users.toml` is the stable identity, with the wrinkle that renaming an
account would then lose that person's stars, and that Google sign-in identifies
people by a different string again.

**What `gc` must do.** Delete stars whose photographs are gone, as Nadav says.
With one caution loud enough to survive into the implementation: *gone from the
index* is not the same as *gone*. An unmounted drive, or a scan that has not run
since a reorganization, makes photographs vanish from the index temporarily, and
a `gc` that prunes on absence would quietly delete people's favorites. This
wants either a grace period, or to run only after a full successful scan, or a
`--dry-run` that says how many stars it is about to drop — `gc` already has the
flag.

**The rest of the awkwardness.** The starred view is a query across the whole
tree, so it must filter by `dirs.acl_chain` like every other listing, or it
becomes a way to enumerate photographs one cannot otherwise see. Sorting by
date wants `photos.taken`, which is already indexed (`photos_date`), and a
decision about photographs with no EXIF date.

**It is the first per-person state, but not the first mutable state**, and the
difference matters. Choosing an album's cover from the UI already writes: the
`/cover` route calls `overrides.set_many`, which serializes the entire override
table and replaces the file. That is fine for a handful of album covers chosen
occasionally by one admin, and it is the wrong shape for stars — thousands of
rows, toggled constantly, by everybody, where rewriting the whole file per
click is both slow and a lost update waiting for two people clicking at once.

So stars would be the *third* kind of state the web process writes, after the
login throttle and the overrides file, and each has picked its own mechanism.
That is the argument for deciding once where UI-written state belongs rather
than inventing a third answer — and quite possibly for moving the cover picks
there too, which was always the plan for them. Worth doing as part of this
rather than after it: two stores with one design beats three with three.

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

### 25. Renaming a directory should not re-encode it

Nadav's question, and the answer is worse than it sounds: a rename is not
recognized at all. There is no move detection anywhere in the scanner —
`content_sig` is used only to ask "did the bytes *at this path* change?".

So renaming `2019/trip` to `2019/italy` is a delete plus an add. `prune()`
drops every old row; the new names are fresh rows with `deriv_key` NULL; phase
2 reads each file and computes a `content_sig` that is *identical*, since the
bytes never moved; and phase 3 hits `if r["deriv_key"] != want_key` — NULL
against the key — and re-encodes every tier. Three hundred photographs is
twelve hundred AVIF encodes, on a one-core server, to produce files
byte-for-byte identical to the ones the same scan is about to delete.

**It is avoidable because of a property the code already has.** `deriv_key` is
computed from `content_sig` and the encode settings only, and never from the
path (`derive.deriv_key`). The derivatives of a renamed photograph are
therefore already correct; the only thing wrong about them is where they sit.
The work is a `rename(2)` per file, not an encode.

Two accidents of ordering make it tractable rather than a rewrite:

- `prune()` already collects the vanished rows before deleting them, and
  returns their paths. It would need to return their `content_sig` and
  `deriv_tiers` as well, which is two more columns in a `SELECT` it already
  runs.
- `prune_derivatives(orphans)` is called *after* `derive_all()`, not before —
  so throughout phase 3 the old derivative files are still on disk, waiting to
  be moved instead of deleted.

So: after phase 2, match the signatures of newly-seen photographs against those
of pruned ones; for each match, rename the derived files into place and write
`deriv_key` and `deriv_tiers` so phase 3 skips them entirely. Nothing about
correctness changes — a missed match costs exactly what happens today.

What is genuinely awkward:

- **Identical duplicates share a signature**, so it is a many-to-many match and
  not a lookup. Item 12 again, from a third direction.
- **A rename plus an edit is not a rename**, which the signature comparison
  handles correctly by simply not matching — worth stating so nobody adds a
  filename heuristic on top and reintroduces the bug `content_sig` exists to
  prevent.
- **`scan --dir X` cannot see a move across its own boundary.** `prune()` is
  deliberately scoped to the subtree, so a photograph moved out of `X` is
  pruned with nothing to match it against, and one moved *in* is new with no
  pruned partner. Detection would work on a full scan and quietly not on a
  scoped one, which is acceptable but must not be surprising.

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

### 10a. Landmarks of your own

GeoNames does not know the Haifa Zoo. It is not filed under the wrong code or
sitting 3 km away — the only `ZOO` in the entire country is the Dolphin Reef in
Eilat, and there is no park record within 5 km of the zoo either. A photograph
taken inside it says "Haifa", which is correct and is the best the data allows.

No rule can fix a missing row, and this will keep happening: a zoo, a favourite
beach, a grandparent's village, anything local enough that nobody added it. So
a file of one's own, merged into the landmark table when it is built:

    [[landmark]]
    name   = "Haifa Zoo"
    lat    = 32.8062
    lon    = 34.9865
    radius = 250

Loaded at `init --landmarks` beside the downloaded rows so it takes part in the
same rules rather than bypassing them — joining a city rather than replacing
it, losing to an airport, shrinking near a town if given an area code. A code
could be optional, defaulting to something small and built.

Worth doing because it is the escape hatch for a whole class rather than one
zoo, and because the alternative — submitting corrections upstream to GeoNames
— is right, public-spirited, and takes months.

Related: item 10 names a whole album, which is the answer when a feature is so
large its recorded point is nowhere near you. This is the answer when the
feature is missing altogether.

### 10b. Boundaries, not just centroids — *less needed than it looked*

**The case that prompted this was fixed by something else.** Luna Park now says
"Tel Aviv Luna Park, Tel Aviv" rather than Ramat Gan, because the places table
took in the villages and neighborhoods. Those rows carry no population, which
hands the decision to the dominance rule, and that picks the largest city whose
extent reaches the camera. What remains below is still true of the general
problem and is kept for it — but the concrete example is gone, so anybody
picking this up should first find a case that still hurts.



Every place in the dataset is one point and a population. Where towns abut —
common in Israel, and anywhere a conurbation is many municipalities — nothing
says which side of a border a photograph is on. Tel Aviv's Luna Park is named
"Ramat Gan" because Ramat Gan's recorded centre is 2.7 km away and Tel Aviv's
is 4.2, and `admin2`, `admin3` and `admin4` are empty for every Israeli row.

**The obvious heuristic does not work, and the reason is worth keeping.** "When
two towns both plausibly contain the camera, prefer the bigger" was tested
against the real dumps:

    Luna Park            nearest: Ramat Gan          bigger: Tel Aviv   <- want bigger
    Newton Upper Falls   nearest: Newton Upper Falls bigger: Newton     <- want nearest

Structurally identical, opposite right answers. No rule over centroids and
populations can separate them, which is what makes this a data problem rather
than a tuning problem.

**Boundaries would fix one of those two and break the other.** Asked of
OpenStreetMap, `admin_level=8` at Luna Park is *Tel-Aviv*, exactly right. At
Newton Upper Falls it is *Newton*, and there is no boundary for the village at
all — it is a named locality inside a city, not a municipality, so it has a
centroid and a population and nothing else. Answering purely by boundary would
therefore reintroduce the regression this project has been told twice not to
have.

So boundaries belong as a **tie-breaker, not a lookup**: keep choosing by
centroid as now, and consult a boundary only when two towns are both plausible.
That changes the ambiguous cases and leaves everything else exactly as it is.

**Data.** Checked rather than remembered:

- **geoBoundaries** (CC BY 4.0) stops too coarse. Israel's ADM2 is
  *Subdistrict* and there is no ADM3; the United States' ADM2 is *Counties*,
  and both Newtons are in Middlesex. Ruled out.
- **OpenStreetMap** has what is wanted, at `admin_level=8`. Licence is ODbL,
  whose share-alike applies to derived databases and wants reading properly
  before shipping one. Getting it in bulk is the awkward part: the
  distributions are `.osm.pbf`, which needs a protobuf parser — a fifth
  dependency — and Overpass is for queries, not for downloading a planet.
- **Who's On First** publishes per-country SQLite bundles of locality
  polygons, mostly CC-0. Worth checking first if this is ever attempted; the
  format would suit this project better than anything requiring a parser.

**Implementation**, if the data question is solved: SQLite's R-Tree module is
compiled into the standard library here — verified — so a bounding-box index
plus ray casting in plain Python is enough, with no new dependency and no
geometry library. Simplified polygons would shrink it, at the cost of accuracy
exactly at the borders, which is the only place it is needed.

Not small, and not obviously worth it for a family album: it buys the right
municipality in dense conurbations and nothing else. Recorded because the
question was asked and the answer took real digging, and because the two-case
proof above is the thing to re-read before anyone tries a cheaper fix.

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

### 14. A thumbnail size control, like Picasa's

Picasa's desktop window had a slider that ran from a great many tiny thumbnails
to a few large ones, and it was the thing that made a big collection feel
navigable: wide for skimming a year, narrow for actually looking at a trip.
Google Photos and macOS Photos both have a version of it. Nothing here does —
the grid is one size and that is that.

The machinery is already in place, which is what makes this attractive. The
justified layout is driven by a single target row height (`var target = width <
600 ? 130 : 180` in `app.js`); a control that sets that number and re-runs the
layout is most of the work, and `+`/`-` should do it too. The size wants
remembering per browser, and the right file is chosen automatically afterwards,
because the layout already tells each image how wide it will actually be.

Desktop only, most likely. On a phone the width decides the answer and there is
little to choose.

### 15. A keyboard help overlay — *partly done*

`i`, `d`, arrows, Escape — none of them were discoverable. Two of them now are:
the overflow menu prints the letter beside "Photo information" and "Download
original", hidden where the pointer is coarse and there is nothing to press
them with, and MANUAL.md has a table of every key and gesture.

What is still missing is the arrows, Escape and `0`, which have no menu item to
sit beside. `?` showing a small panel would cover those, and would be twenty
lines.

### 16. Multiple downloads

Selecting several photographs and getting a zip. Wanted the first time somebody
says "can you send me the ones from the wedding". Streams from the derived tree
or the originals; needs a cap so nobody asks for 98,000 of them.

### 17. Dark mode by choice, not only by system

It follows `prefers-color-scheme` today. A toggle would need a control, a
preference stored per browser, and a decision about where the control lives —
which is why it was not done for someone who said they would not use it.

---

## Responsiveness

The album now feels close to an application on a phone, and every remaining
complaint about it is about waiting. These are collected in the order they are
worth doing, which is *not* the order they occur to you: the first is a line of
configuration and the last is a rewrite.

Measured against photos.harel.org.il, from a laptop on a good connection, so a
phone on mobile data is two to four times worse:

    one TCP round trip        ~105 ms
    TLS established           ~226 ms
    first byte of a tiny file ~332 ms

Everything below follows from that number being large and the files being
small. A 1280px AVIF of a real 12-megapixel photograph is **34 KB**. The
sizes are not the problem and have not been for a while.

### 19. Prefetch the neighbouring *pages*, not only their images

The next and previous photographs' images are already fetched ahead of time,
but not their HTML — so every swipe still pays a full round trip for the page
before the cached image can even be referenced. Paging is a page load, and a
page load is a round trip the reader watches.

`<link rel="prefetch">`, or the speculation-rules API, on the neighbour URLs.

Cheaper than it first looks. Prefetching *both* neighbours would double the
server's work, but paging in a direction only ever needs the one ahead: the one
behind is where you just came from and is already in the browser's cache. So in
steady use this costs one page render per photograph, exactly as now, and
simply moves it off the path the reader waits on. Only a cold start and a
change of direction waste anything, one page each.

Widening the image prefetch from one neighbour to two belongs here as well —
swiping quickly outruns a one-deep window, which is precisely when the wait is
noticed.

Do this before item 21, which addresses the same waiting far more elaborately.
This is a few lines; if it is enough, the argument about 21 never has to be
had — and if it is not, 21's case rests on how paging *feels* rather than on
what it saves.

### 21. Page between photographs without loading a page

Nadav's, and the one with the largest effect on how the viewer feels.

**The idea.** Arrow or swipe swaps the image and the caption in place. Nothing
navigates, no HTML is fetched, and the address bar is kept honest with
`history.replaceState` so that the URL still names the photograph on screen.

**What has to come from somewhere.** Two things are on the page: the image, and
the words around it -- the filename, the date, the place, the camera, the
exposure, and which photographs are on either side. Today a page load brings
both, the words baked into the HTML. Stop loading pages and the image is
already handled (it is fetched, and prefetched, exactly as now), but the words
need a source. That source is a small JSON document, and everything below about
"the JSON" means that and nothing more.

**What it costs, measured.** The worry was server load and traffic, and neither
is the objection -- but nor are they much of an argument *for* this, once the
comparison is made against item 19 rather than against today.

    per photograph, paging steadily   today       with 19    this
    round trip in the way             1 (~330ms)  0          0
    bytes fetched                     3.3 KB      3.3 KB     ~0.7 KB
    server renders                    1           1          ~1/50

Item 19 fetches one page per photograph, not two: paging in a direction only
ever needs the one ahead, because the one behind is where you just came from
and is already in the browser's cache. So it costs the same work as today and
merely moves it off the path the reader is waiting on. Something is wasted only
when a session starts cold, or when somebody reverses direction -- one page
each time, and not once in between.

So the efficiency case for this over item 19 is thin: four times less traffic
and fifty times less rendering, of a page that renders in a millisecond and a
half. On a family album that is not a reason to do anything.

The real argument is what it makes possible rather than what it saves. Nothing
is torn down between photographs, so a photograph can slide out while the next
slides in, the zoom and the info panel keep their state, and the swipe that
already follows the thumb becomes a carousel rather than a gesture that ends in
a page load. Item 19 makes the waiting disappear; this makes the paging feel
like one continuous thing. Those are different goals and it is worth being
honest about which is being bought.

**How it should be built.** Three things:

- **As progressive enhancement.** The server-rendered page stays exactly as it
  is: it is what a bookmark, a shared link and a browser without JavaScript
  get. The swapping layers on top. That keeps the change additive and
  reversible, which matters for the view everything else hangs off.
- **With the metadata formatted server-side.** Send `"1/200 s · f/1.9 · ISO
  44"`, not raw EXIF -- the string the template would have printed. Otherwise
  `exposure`, `photo_date`, `day_date` and `maplink` all have to exist a second
  time in JavaScript and stay in step with the Python. About 730 bytes of
  finished text per photograph.
- **Windowed, and the first window inlined.** Fifty photographs' metadata around
  the current one is 36 KB and buys fifty moves with no request at all; a
  5000-photo album entire would be 3.6 MB. The first window costs nothing extra
  at all if it is written into the photo page that is being loaded anyway --
  which it already does for its own photo, in `nav-data`. Later windows fetch
  in the background, exactly as the images already do. So paging really does
  cost what the image costs, for as long as anyone pages in one sitting.

**Considered and rejected: putting the words inside the images**, so that no
JSON exists at all. AVIF is a HEIF container with boxes for EXIF and XMP, so it
can physically be written. Two problems, and the second decides it.

JavaScript cannot read those boxes from an `<img>`, which hands back decoded
pixels and not file bytes. It would mean fetching each image a second time (a
cache hit, at least) and parsing ISOBMFF structure by hand, in a project with
one script and no build step.

Worse, it couples metadata to pixels, which is precisely what this codebase is
built not to do. `place` arrives from `geocode`, a pass run after the scan;
prev and next depend on the album's sort order rather than on the photograph;
`is_cover` lives in the overrides file and is changed by a button in the
browser. None of them are properties of the file. `deriv_key` is deliberately
built from the content signature and the encode recipe and nothing else, so
that a metadata tidy-up never becomes a re-encode -- and embedding would make
re-encoding the whole collection the price of a better landmark rule.

**What it really costs.** Two renderers for one view, which have to agree.
Formatting server-side shrinks that a great deal but does not remove it, and it
is the largest departure this project would have made from "a page view is a
few indexed SQLite reads and a template render". The admin cover button is the
one part of the page that is not display -- a POST form with a CSRF token --
and wants thinking about separately.

**What already exists.** `api_album` returns name, URL and aspect for
prefetching; this is the same endpoint with enough in it to draw a page. The
photo page already carries a `nav-data` block, which is the inlined first
window in miniature.

**What it composes with.** The swipe that follows the thumb becomes a real
carousel, prefetching becomes trivial, and the zoom resets naturally between
photographs.

### 22. How wide should the lazy-load window be?

`limitLoading` observes each tile with `rootMargin: "200% 0px"` — two viewports
of slack above and below — so that scrolling never waits for an image. That was
chosen against an album of a few hundred photographs, not one of three and a
half thousand.

On a phone, two viewports either side of a 700 px screen is a 3500 px band,
which at a 130 px row height is around 27 rows, or roughly 90 tiles requested
before anybody has scrolled at all. At a median thumbnail of about 12 KB that
is on the order of a megabyte fetched up front. A desktop's wider rows make the
tile count higher still.

Against that: `loading="lazy"` is on every tile as well, so the browser applies
its own heuristics too, and the rows carry `content-visibility: auto`, so
nothing off-screen is laid out or painted regardless. The cost is bandwidth and
request count, not rendering.

Worth measuring before changing: count what a first paint actually asks for, on
a phone and on a desktop, rather than reasoning from the margin. A narrower
window trades a little scrolling latency for a lot of unrequested data, and
which way that trade falls depends on a number nobody has yet looked at.

### 27. A 768 tier, to close the gap between 512 and 1280

The ladder is `256, 512, 1280, 1600`. Every step is 2× except one, which is
2.5×, and the album grid asks for widths that land inside it.

A tier is the **longest edge** of the stored file, while the justified grid
fixes the row *height* — 130 px on a phone, 180 on a desktop. So a tile's width
is its aspect ratio times that height, and what it needs is that times the
screen's pixel ratio. A 16:9 photograph on a 2.625× phone wants 231 × 2.625 =
607 px of width: past the 512, so it fetches the 1280 and gets four times the
pixels it can show.

**Who benefits, and who does not.** The requirement is `aspect × row × dpr`
falling between 512 and 1280, which is:

- **Wide photographs only.** On a 2× screen the threshold is `aspect > 1.42`,
  so 3:2 and 16:9 qualify and 4:3 does not — it asks for 478 px and the 512
  covers it. Nothing narrower than 3:2 is ever affected.
- **On 2× screens and above.** At 1× a 16:9 tile is 320 px wide and picks the
  512 anyway; it would take `aspect > 2.84` for a 1× screen to reach the 1280,
  and nothing in the collection is that wide.

On the 3505-photo Thailand album that is **228 photographs, 6.5%** — 177 at
16:9 and 47 at 3:2. Measured medians over 15 of each, fetched from the live
site:

| shape | 512 tier | 1280 tier | with a 768 (estimated) |
|---|---|---|---|
| 16:9 | 7 KB | 24 KB | ~12 KB |
| 3:2 | 22 KB | 92 KB | ~41 KB |

So roughly **6 MB saved across that whole album** — and only for a reader who
scrolls all of it on a high-density screen. Per screenful, where it is actually
felt, it is about one wide tile in twenty and some 20 KB.

**It would do nothing for the single-photo view.** There `view_sizes` claims
`min(100vw, calc(100dvh * aspect))`, which on a 390 px phone at 2.625× is 1024
device px — past the 768, so it still fetches the 1280. The 768 only helps when
the rendered width lands between 512 and 768, which needs a window narrower than
about 384 CSS px at 2×. Nearly nothing lands there.

**The cost is collection-wide and the benefit is not.** `expected_tiers`
generates every configured tier for every photograph, so a 768 tier is one more
file for all ~98,000 of them, including the 93.5% whose shape can never use it.
From the two measured points, file size grows roughly as pixel count to the
power 0.7, so a 768 file is about **1.8× its 512** — meaning the 768 tier would
occupy a little under twice whatever the 512 tier occupies today.
`harelphotos stats` prints that per tier, so the real figure is one command
away; a rough guess is 2–4 GB.

Encoding it is the cheap part, by design: `deriv_key` excludes the tier list,
so adding a size re-encodes nothing and generates only the new size, and 768 has
no `DEFAULT_QUALITY` entry so it would use `quality_default` — which is exactly
the arrangement that makes trying a size and discarding it inexpensive.

**The honest summary:** this buys a few megabytes for 6.5% of photographs on
high-density screens, and costs a few gigabytes of storage for all of them, at a
point where nothing is visibly slow — the thumbnails are `loading="lazy"` inside
rows with `content-visibility: auto`, so a reader only ever fetches a screenful
at a time. Worth doing if wide tiles ever look slow to arrive on a phone, and
not before.

*(If it is ever wanted and the storage is the objection, the sharper version is
to generate 768 only for photographs wide enough to use it — `expected_tiers`
already decides per photo which tiers to make, and it has the dimensions to hand.
That confines the cost to the same 6.5% that gets the benefit, at the price of a
tier whose presence varies by photograph, which nothing else in the design
currently assumes.)*

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
