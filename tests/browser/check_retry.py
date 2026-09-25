"""Check that a thumbnail which failed to load is fetched again.

Not part of the pytest suite: it needs Chrome. Run it with the others through
`run_all.py`, or by hand:

    harelphotos serve --port 5090 &
    python tests/browser/check_retry.py http://127.0.0.1:5090/a/some-album/

A browser never retries a broken image by itself, and a broken image is
`complete`, which is what app.js's loading limiter looks at -- so a burst of
failures on a fast scroll used to leave tiles blank for the life of the page.
This intercepts the image requests with the DevTools Fetch domain and makes
chosen ones fail, then looks at whether the tiles on screen fill in anyway.

The window is short on purpose: the limiter's range is two windows either
side, and the album has to be tall enough, measured in windows, for a tile to
leave that range and come back.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

import json, subprocess, time, urllib.request, sys

import websocket

PORT = 9337
PROFILE = "/tmp/cdp-retry"
URL = sys.argv[1]

chrome = subprocess.Popen(
    ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
     f"--remote-debugging-port={PORT}", "--window-size=1280,500",
     "--remote-allow-origins=*",
     f"--user-data-dir={PROFILE}", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
            ws_url = [t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("chrome did not start")

    ws = websocket.create_connection(ws_url, timeout=30)
    n = [0]

    # How many more times each image URL is to fail. A URL not in here is
    # looked up in `policy`, which says how often a first-seen URL fails.
    failures_left: dict[str, int] = {}
    policy = [lambda url: 0]
    stats = {"failed": 0, "passed": 0}

    def handle(msg):
        """Answer a paused image request: fail it or let it through."""
        if msg.get("method") != "Fetch.requestPaused":
            return
        p = msg["params"]
        url = p["request"]["url"]
        if url not in failures_left:
            failures_left[url] = policy[0](url)
        n[0] += 1
        if failures_left[url] > 0:
            failures_left[url] -= 1
            stats["failed"] += 1
            ws.send(json.dumps({"id": n[0], "method": "Fetch.failRequest",
                                "params": {"requestId": p["requestId"],
                                           "errorReason": "ConnectionReset"}}))
        else:
            stats["passed"] += 1
            ws.send(json.dumps({"id": n[0], "method": "Fetch.continueRequest",
                                "params": {"requestId": p["requestId"]}}))

    def cmd(method, **params):
        n[0] += 1
        mine = n[0]
        ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == mine:
                if "error" in msg:
                    raise SystemExit(f"{method}: {msg['error']}")
                return msg.get("result", {})
            handle(msg)

    def pump(seconds):
        """Sleep, but keep answering paused requests -- they hang otherwise."""
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            ws.settimeout(left)
            try:
                handle(json.loads(ws.recv()))
            except websocket.WebSocketTimeoutException:
                break
        ws.settimeout(30)

    def js(expr):
        r = cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    ON_SCREEN = """(() => {
        let n = 0, blank = 0;
        for (const img of document.querySelectorAll('#grid .tile > img')) {
            const r = img.getBoundingClientRect();
            if (r.bottom <= 0 || r.top >= innerHeight) continue;
            n++;
            if (!(img.complete && img.naturalWidth > 0)) blank++;
        }
        return [n, blank];
    })()"""

    # The highest failure count left on a tile that is on screen.
    TRIES = """(() => {
        let n = 0, most = 0;
        for (const img of document.querySelectorAll('#grid .tile > img')) {
            const r = img.getBoundingClientRect();
            if (r.bottom <= 0 || r.top >= innerHeight) continue;
            n++;
            most = Math.max(most, img.hpTries || 0);
        }
        return [n, most];
    })()"""

    def wait_filled(seconds):
        """(tiles on screen, how many are blank) once none are, or at the end."""
        deadline = time.monotonic() + seconds
        while True:
            shown, blank = js(ON_SCREEN)
            if blank == 0 or time.monotonic() > deadline:
                return shown, blank
            pump(0.2)

    cmd("Page.enable")
    cmd("Runtime.enable")
    cmd("Network.enable")
    cmd("Network.setCacheDisabled", cacheDisabled=True)
    cmd("Fetch.enable", patterns=[{"urlPattern": "*/i/*", "requestStage": "Request"}])

    results = []

    # 1. Every image fails at least once and a quarter of them three times;
    #    the page is not scrolled at all, so only the timed retries can fill
    #    them. Three failures is retries after 1 s, 2 s and 4 s: 7 s and
    #    change. Every one of them fails so that check 2 has something to
    #    look at whichever tiles happen to be on screen.
    policy[0] = lambda url: [1, 2, 3, 1][hash(url) % 4]
    cmd("Page.navigate", url=URL)
    pump(1.0)
    height = js("document.body.scrollHeight")
    view = js("window.innerHeight")
    print(f"album loaded, height = {height}, window = {view}")
    if height < 8 * view:
        raise SystemExit(f"album is only {height}px tall, {height / view:.1f} windows; "
                         "too short for a tile to leave the loading range and "
                         "come back -- use one with more photographs")
    shown, blank = wait_filled(12)
    print(f"sitting still:  {shown} tiles on screen, {blank} blank "
          f"({stats['failed']} requests failed on purpose)")
    results.append(("failed tiles retried while on screen", shown > 0 and blank == 0))

    # 2. Those tiles all failed and then loaded, so none of them is owed any
    #    more retries: the count has to be back at zero. If it were not, the
    #    five allowed attempts would be five for the life of the page rather
    #    than five at one failure -- and a tile that is fetched again later,
    #    which happens when a rotated phone changes `sizes` and the browser
    #    picks a different file out of the srcset, would start out having
    #    spent them.
    shown, most = js(TRIES)
    print(f"after loading:  {shown} tiles on screen, highest failure count {most}")
    results.append(("failure count starts over after a success",
                    shown > 0 and most == 0))

    # 3. Everything at the bottom fails for a while. Then the failures stop,
    #    and the reader scrolls away and straight back: the tiles must come
    #    now, not at the next timed retry. The first failure is at t, its
    #    retry at t+1 s fails too, and the one after that is due at t+3 s --
    #    so anything filled before then was filled by coming back.
    policy[0] = lambda url: 10**6
    failures_left.clear()
    js("window.scrollTo(0, document.body.scrollHeight)")
    pump(2.0)
    shown, failing = js(ON_SCREEN)
    print(f"failing:        {shown} tiles on screen, {failing} blank")
    policy[0] = lambda url: 0
    failures_left.clear()
    js("window.scrollTo(0, 0)")
    pump(0.3)
    js("window.scrollTo(0, document.body.scrollHeight)")
    shown, blank = wait_filled(0.6)
    print(f"scrolled back:  {shown} tiles on screen, {blank} blank")
    results.append(("failed tiles retried on scrolling back",
                    shown > 0 and failing > 0 and blank == 0))

    print()
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    raise SystemExit(0 if all(ok for _, ok in results) else 1)
finally:
    chrome.terminate()
    try:
        chrome.wait(timeout=10)
    except subprocess.TimeoutExpired:
        chrome.kill()
