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

### ~~13. Zoom on a photo~~ — done

Pinch, double-tap and drag to pan, and the sharper copy fetched behind the
gesture. Kept here only because the item below refers to it.

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

### ~~18. Turn on HTTP/2~~ — done, 2026-09-13

Was: `curl -w '%{http_version}'` said **1.1** and the vhost never mentioned
otherwise, so a browser opened about six connections per origin and fetched an
album's thumbnails six at a time, each batch costing a round trip.

Done. `Protocols h2 http/1.1` is in the shipped vhost behind an `IfModule`,
`mod_http2` is in INSTALL.md's package list and in `check --env`, and the live
server negotiates `h2` with HTTP/1.1 still working as a fallback.

**Reported immediately as feeling much better**, which is the result that
counts. The mechanism: the server costs about ten milliseconds per request and
the distance — it is in another country — costs about 105, so an album's
hundred-odd thumbnails over six connections was something like seventeen
serialized round trips of pure waiting. One connection and one handshake
removes nearly all of it.

Benchmarking it from a third machine was less tidy than that, and is recorded
because the tidy number would have been wrong. Forty thumbnails, twice: HTTP/2
took 12.63 s and 12.65 s, HTTP/1.1 took 149.8 s and then 16.7 s. The honest
reading is not "twelve times faster" but **"consistent where six connections
are not"** — one bad moment costs a multiplexed connection little and costs six
separate ones a great deal. The absolute figures are a slow path between two
countries and say nothing about what anyone else sees.

Everything below was written against the old behaviour and should be re-judged
against the new one before any of it is attempted.

`Protocols h2 http/1.1` in the TLS vhost, with `mod_http2` loaded. It is the
cheapest item on this list by a wide margin and probably the largest single
improvement, and it should be measured before anything else here is attempted,
because it changes what the rest are worth.

**HTTP/3 would be better still, and is not available to us.** It is a real
standard, and phones support it well — Chrome on Android and Safari on iOS
both. It would help more than HTTP/2 here for two reasons that are exactly this
site's problems. QUIC folds the transport and crypto handshakes together, which
is worth about the 120 ms that TLS costs on top of TCP in the measurements
above; and its streams are independent, so a single lost packet does not stall
every other one. HTTP/2 over TCP has that flaw, and fifty thumbnails all
waiting on one retransmit is what a lossy mobile link does to an album page.

The blocker is the web server. Apache 2.4 ships no HTTP/3 — `mod_http2` and
nothing beyond it — and Rocky 9 ships Apache. nginx has had it since 1.25 and
Caddy does it by default, so this means replacing or fronting the web server,
against two deliberate choices: INSTALL.md assumes an Apache already serving
other sites and takes care not to disturb it, and the X-Sendfile handoff is
Apache's (`sendfile_header` is an enum of `auto`, `X-Sendfile`, `none`, and
knows nothing of nginx's `X-Accel-Redirect`).

Worth revisiting if Apache ever ships it, or if this site ever moves off a
shared httpd for other reasons. Not worth moving *for*.

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

### 20. Stop the thumbnails appearing one at a time

Nadav's idea, and the honest answer is that it is two ideas.

The *appearance* is easy: hold a row until its images have all decoded, or fade
them in together, so a grid arrives as a grid rather than as popcorn. Tens of
lines, no new files, no invalidation.

The *cause* was item 18, which is now done — so the first thing to do here is
look again and see whether it still happens. Once the requests are multiplexed
they largely arrive together anyway, and this may have stopped being annoying
without anything else being
done.

The larger version — pack many thumbnails into one file and slice them out with
`background-position` or `object-view-box` — is genuinely attractive and
genuinely expensive. It buys one request instead of fifty and a grid that
appears at once. It costs:

Two objections raised against it here were wrong, and are recorded as wrong so
that nobody re-derives them:

- **Lazy loading is not lost.** Packs of about fifty make a 5000-photo album a
  hundred packs; you fetch the one or two on screen and the rest as they are
  scrolled to. That is the same model we already have, observing packs instead
  of images — *fewer* things to watch, not more.
- **Invalidation is only fatal if packs are cut by page position**, where
  inserting one photograph at the front shifts every pack after it. Cut them by
  *directory* and an album page's photographs are exactly one directory's own,
  in sort order, so adding a photograph rebuilds that directory's packs and
  nothing else. Comparable to what a rescan already does.

What remains genuinely awkward:

- **The geometry.** The grid is justified with true aspect ratios and never
  crops, so a pack holds fifty rectangles of differing shapes. That needs a
  packing pass at scan time, per-tile coordinates in the index, and
  `object-view-box` or a `background-position` trick to slice them out. Stacked
  at a common height it is simpler and wastes width on a panorama.
- **Cache sharing.** A thumbnail fetched once is reused wherever it appears,
  including as a cover on a parent page. Packs break that.
- **Two ladders.** `srcset` offers 256 and 512; packs would need both.

So: worth doing only if 18 and the cheap version of 20 are done and the grid
still arrives badly — which it may well not, because the fifty requests really
are the problem and HTTP/2 fixes them for one line of configuration. Recorded
in full because the instinct was right about the cause, and because the reason
to skip it should be "the cheap fix worked", not a list of difficulties that
turned out to be softer than they first looked.

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

### 22. The album page, measured

Reported as slow: `/a/2021/08/`, 312 photographs. Measured against the live
server while signed in, rather than guessed at.

**The image sizes are right, and that was the worry.** A browser really does
get AVIF -- checked by sending Chrome's, Firefox's and Safari's own `Accept`
headers, which is all the negotiation looks at. A typical grid thumbnail is
**15.8 KB**. The tiers, for one real photograph:

    256   5.8 KB     512   15.8 KB     1280   87 KB     1600   133 KB

(An earlier measurement of this said 34 KB, because `curl` sends `Accept: */*`
and gets the JPEG fallback. Worth knowing before measuring this again.)

**What costs the time is per-image latency, not bytes** -- and specifically the
distance, since the server is in another country. On a connection that is
already open, a thumbnail's first byte arrives 117 ms after the request against
a round trip of about 105, so the server costs roughly ten milliseconds. (An
earlier version of this entry said 250 ms of server time, arrived at by
subtracting the round trip from a figure that still had the TLS handshake
inside it. There was never a server-side problem.)

Over HTTP/1.1 and six connections that meant an album's hundred-odd thumbnails
in serialized batches, which was the wait. **Item 18 fixed it and is done.**
Everything remaining here was written against the old behaviour.

Three further things, found while looking:

**The ladder has a hole between 512 and 1280, and at today's sizes nothing
falls into it.** Measured on a real 412px phone at 2.625 device pixels per
point: a portrait tile is 95 CSS px and wants 250 device px, a landscape one is
191 and wants 500 — and both take the 512 copy, 16 KB. So 512 is the
interesting size and the hole is theoretical.

It stops being theoretical in two cases, neither of them present: a phone at a
full 3x density, where a landscape tile wants 585 and jumps to the 1280 file at
87 KB; or the grid thumbnails being made larger, which puts a landscape tile
past 674. If either ever happens, a 768 tier is the answer and is cheap — the
tier list is deliberately not part of the recipe fingerprint, so adding one
costs a single extra encode per photograph and re-encodes nothing.

Recorded because it was nearly acted on twice. Both times the reasoning was
about a device nobody here owns.

**`sendfile_header = "auto"` does nothing.** It is what `init` writes, and
`images.py` acts on the literal `"X-Sendfile"` and treats everything else as
`"none"` -- so the default configuration sends every thumbnail through Python
even where `mod_xsendfile` is loaded and ready. The name promises detection and
there is none. `check --env` now says so when the module is present; whether
the setting should instead *mean* something is a separate question.

**The lazy-load window may be too wide for a 312-photo page.** `limitLoading`
uses `rootMargin: "200% 0px"` -- two viewports of slack in each direction --
which on a tall album is a great many thumbnails requested before anybody has
scrolled. It was chosen so scrolling never waits, which is right, but it was
not chosen against a page this long. Worth measuring how many images a first
paint actually asks for before changing it.

### 23. Serve the album page's first screenful without waiting for the index

Not investigated, and listed so it is not forgotten: a page of five thousand
photographs does five thousand rows of work before the first byte, and only the
first thirty are looked at. Whether that actually costs anything is a question
for a measurement, not for an opinion.

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
