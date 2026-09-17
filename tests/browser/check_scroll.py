"""Check the album/photo scroll behavior in a real browser.

Not part of the pytest suite: it needs Chrome and takes half a minute. Run it
by hand after touching app.js or the templates.

    harelphotos serve --port 5090 &
    python tests/browser/check_scroll.py http://127.0.0.1:5090/a/some-album/

It exists because this behavior cannot be checked any other way, and two
attempts to fix it by reasoning were both wrong: the first depended on
document.referrer, which is always empty here because the site sends
Referrer-Policy: no-referrer.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

import json, subprocess, time, urllib.request, sys
from urllib.parse import urlparse

import websocket

PORT = 9333
URL = sys.argv[1]

chrome = subprocess.Popen(
    ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
     f"--remote-debugging-port={PORT}", "--window-size=1280,900",
     "--remote-allow-origins=*",
     "--user-data-dir=/tmp/cdp-profile", "about:blank"],
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

    def cmd(method, **params):
        n[0] += 1
        ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == n[0]:
                if "error" in msg:
                    raise SystemExit(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(expr):
        r = cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def goto(u):
        cmd("Page.navigate", url=u)
        time.sleep(3.0)

    cmd("Page.enable")
    cmd("Runtime.enable")

    goto(URL)
    album_path = urlparse(URL).path
    height = js("document.body.scrollHeight")
    view = js("window.innerHeight")
    print(f"album loaded, height = {height}, window = {view}")

    # Targets as a fraction of what can actually be scrolled, not absolute
    # pixels. This asserted 1800 on an album 1830 tall, where the browser
    # clamps to about 1030 -- so the expectation was unreachable and the album
    # it was written against is the only one it could ever have passed on.
    span = max(0, height - view)
    if span < 300:
        raise SystemExit(f"album is only {height}px tall; too short to test "
                         "scrolling -- use one with more photographs")
    low, mid, high = round(span * 0.35), round(span * 0.55), round(span * 0.9)

    def open_first_photo():
        """Click a tile, the way a person does.

        Not `goto` of its href: the album page records "you left here by
        opening a photograph" on a *click*, and the photo page uses that to
        decide whether Escape is a history step. Navigating straight to the
        URL looks to the application exactly like a shared link, which
        deliberately does not restore anything -- so this used to measure a
        path no reader ever takes.
        """
        js("document.querySelector('#grid .tile').click()")
        time.sleep(3.0)

    def press(key, vk):
        for kind in ("keyDown", "keyUp"):
            cmd("Input.dispatchKeyEvent", type=kind, key=key, code=key,
                windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)

    js(f"window.scrollTo(0, {mid})")
    time.sleep(0.6)
    before = js("Math.round(window.scrollY)")
    print("scrolled to", before)
    print("sessionStorage says",
          js("sessionStorage.getItem('hp:scroll:' + location.pathname)"))

    open_first_photo()
    print("on photo page:", js("location.pathname"))

    press("Escape", 27)
    time.sleep(3.5)
    where = js("location.pathname")
    after = js("Math.round(window.scrollY)")
    print(f"after Escape:        at {where}, scrollY = {after} (wanted ~{before})")
    results = [("Escape from one photo",
                where == album_path and abs(after - before) < 100)]

    # ...and after paging through several photos with the arrow keys.
    js(f"window.scrollTo(0, {low})"); time.sleep(0.6)
    open_first_photo()
    for _ in range(3):
        press("ArrowRight", 39)
        time.sleep(2.5)
    print("paged to:           ", js("location.pathname"))
    press("Escape", 27)
    time.sleep(3.5)
    where2, after2 = js("location.pathname"), js("Math.round(window.scrollY)")
    print(f"after paging+Escape: at {where2}, scrollY = {after2} (wanted ~{low})")
    results.append(("Escape after paging 3 photos",
                    where2 == album_path and abs(after2 - low) < 100))

    # And the Back button must still work as it always did.
    js(f"window.scrollTo(0, {high})"); time.sleep(0.6)
    open_first_photo()
    cmd("Page.navigateToHistoryEntry", entryId=cmd("Page.getNavigationHistory")["entries"][-2]["id"])
    time.sleep(3.0)
    where3, after3 = js("location.pathname"), js("Math.round(window.scrollY)")
    print(f"after Back:          at {where3}, scrollY = {after3} (wanted ~{high})")
    results.append(("Back button",
                    where3 == album_path and abs(after3 - high) < 200))

    print()
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    raise SystemExit(0 if all(ok for _, ok in results) else 1)
finally:
    chrome.terminate()
