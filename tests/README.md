# The tests

Three suites, with different costs and different reasons to exist. Only the
first runs by default, which is why this file exists: the other two have each
gone months without being run, and both were quietly asserting things that had
stopped being true.

| suite | command | time | needs |
|---|---|---|---|
| **pytest** | `.venv/bin/python -m pytest tests/ -q` | **~60 s** | nothing |
| **browser** | `.venv/bin/python tests/browser/run_all.py` | **~4 min** | Chrome, `websocket-client` |
| **live place names** | `HARELPHOTOS_GEONAMES_LIVE=1 .venv/bin/python -m pytest tests/test_geonames_live.py` | **~1 min**, plus a first-run download | network, ~80 MB (cached) |

**Always run pytest through `.venv/bin/python -m pytest`**, never a bare
`pytest` or the system Python. The system Pillow has no AVIF support, so the
`scanned` fixture silently generates no images and about eighteen tests in
`test_web.py` fail with 404s and an `AttributeError` — looking exactly like a
regression you just caused.

---

## 1. pytest — the everyday suite

433 tests, about a minute. Everything that can be checked without a browser or
a network: the scanner, the index, the derivative pipeline, access control,
authentication, the CLI, config parsing, place-name rules, and the web
application through Flask's test client.

```sh
.venv/bin/python -m pytest tests/ -q              # all of it
.venv/bin/python -m pytest tests/test_web.py -q   # one file, while working
.venv/bin/python -m pytest tests/test_web.py -q -k album      # one subject
.venv/bin/python -m pytest tests/test_web.py::test_photo_page # one test
```

Run the affected files while working, and the whole suite every few commits or
when a change is cross-cutting. The whole suite is also memory-hungry enough to
matter: it encodes real AVIF images in parallel workers, and a full run has
coincided with an out-of-memory kill on a loaded development machine. Do not
run it twice in one sitting for no reason.

The heavy files, if you are wondering where the minute goes:

| file | tests | time |
|---|---|---|
| `test_auth.py` | 50 | 23 s |
| `test_web.py` | 66 | 14 s |
| `test_scanner.py` | 35 | 5 s |
| `test_derive.py` | 24 | 3 s |

Almost all of it is fixtures generating real JPEGs and encoding real AVIF. The
assertions themselves are instant.

**Tests may not listen on a TCP port.** `conftest.py` makes `listen()` raise on
an AF_INET socket, after a test invoked `serve`, started a real Flask server and
blocked forever holding port 5000. A test that genuinely needs one requests the
`local_server_allowed` fixture — currently only `test_envcheck_live.py`, which
checks redirect handling and so cannot use a mocked response.

`fixtures.py` has the shared builders: `make_jpeg` (a real JPEG, optionally with
EXIF, GPS and orientation), `make_tree` (a small album tree with `.album.toml`
files), `make_config`, `fresh_index`, `add_user`.

## 2. Browser checks — what only a browser can see

```sh
.venv/bin/python tests/browser/run_all.py         # all four, ~4 minutes
.venv/bin/python tests/browser/run_all.py --only nav
.venv/bin/python tests/browser/run_all.py --keep  # leave the album up to poke at
```

`run_all.py` builds a throwaway photo tree, indexes it, serves it on a free
port, runs each check against it and takes it all down. It touches no real
collection, config or network.

| check | what it is for |
|---|---|
| `check_layout.py` | justified rows: a row must be exactly as wide as the grid |
| `check_nav.py` | history — paging must add no entries, Escape and the back arrow must be a step back |
| `check_scroll.py` | coming back to an album lands where you were |
| `check_dark.py` | both palettes actually paint, `color-scheme` and `theme-color` follow |

**Run these after touching `app.js`, `app.css`, or anything about how a page is
laid out or navigated.** They are the only tests that can observe history
entries, computed row widths, resolved colours or scroll restoration — the
pytest suite cannot see any of it, and neither can a reading of the code.

They are named `check_*.py` rather than `test_*.py` deliberately, so pytest does
not collect them: they need Chrome and are far too slow for every run. The price
of that is the reason this README exists. `check_nav.py` spent months asserting
that swiping down returned to the album, long after that gesture was removed on
purpose, and `check_scroll.py` had three independent staleness bugs at once — a
hardcoded fixture album name, navigating instead of clicking, and scroll targets
larger than the page. Nothing failed, because nothing ran them.

## 3. Live place names — the real GeoNames data

```sh
HARELPHOTOS_GEONAMES_LIVE=1 .venv/bin/python -m pytest tests/test_geonames_live.py -q
HARELPHOTOS_GEONAMES_CACHE=~/.cache/harelphotos-geonames \
  HARELPHOTOS_GEONAMES_LIVE=1 .venv/bin/python -m pytest tests/test_geonames_live.py -q
```

Fifteen real photographs against the real GeoNames dumps, skipped unless that
variable is set — which is why a normal run reports "15 skipped". It downloads
about 80 MB the first time; set `HARELPHOTOS_GEONAMES_CACHE` to keep it
somewhere permanent, or it lands under the system temporary directory and is
fetched again after a reboot.

It exists because the naming rules are tuned against real data, and a fixture
with four invented towns cannot tell you that a rule change has started calling
a theme park by the name of the village beside it. Run it after touching
`geonames.py` — the filters, the radii, the dominance rules — where a change
that looks obviously right locally is routinely wrong against two million
places.

`test_geonames.py`, by contrast, uses a handful of invented places and runs in
under a second. Unit-test the mechanics there; verify the judgement here.
