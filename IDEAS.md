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

### 15. A keyboard help overlay

`i`, `d`, arrows, Escape — none of them are discoverable. `?` showing a small
panel would fix that, and would be twenty lines.

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

### 18. Turn on HTTP/2

`curl -w '%{http_version}'` says **1.1**, and the vhost never mentions
otherwise. Over HTTP/1.1 a browser opens about six connections per origin, so
an album page of fifty thumbnails fetches them in nine sequential batches, each
costing a round trip — which is exactly why the pictures appear in waves rather
than together. HTTP/2 multiplexes them onto one connection.

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

The *cause* is item 18. Once the requests are multiplexed they largely arrive
together anyway, and this may stop being annoying without anything else being
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

### 22. Serve the album page's first screenful without waiting for the index

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
