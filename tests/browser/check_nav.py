"""Check photo paging and Escape in a real browser.

Not part of the pytest suite: it needs Chrome. Run it by hand after touching
the navigation code in app.js.

    pip install websocket-client          # not a runtime dependency
    harelphotos serve --port 5090
    python tests/browser/check_nav.py http://127.0.0.1:5090/a/some-album/

Pick an album that actually has several photos with images generated.

It exists because the two things being asserted are properties of the
browser's history, which nothing else can observe:

* paging with the arrow keys must not add a history entry per photo, or Back
  walks backwards through every photo you looked at instead of returning to
  the grid;
* Escape must be a genuine history step where one exists, because that is what
  lets the browser restore the album from its back/forward cache -- already
  laid out, images already painted -- instead of rebuilding the page.

Both were wrong before, and both were wrong in ways that only showed up over a
real network.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import subprocess
import sys
import time
import urllib.request

import websocket

PORT = 9333
URL = sys.argv[1]


class Browser:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise SystemExit(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def goto(self, url):
        self.send("Page.navigate", url=url)
        self.settle()

    def eval(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")

    def key(self, key, code=None):
        for kind in ("keyDown", "keyUp"):
            self.send("Input.dispatchKeyEvent", type=kind, key=key,
                      code=code or key, windowsVirtualKeyCode=_vk(key),
                      nativeVirtualKeyCode=_vk(key))
        self.settle()

    def settle(self, seconds=1.2):
        time.sleep(seconds)


def _vk(key):
    return {"ArrowRight": 39, "ArrowLeft": 37, "Escape": 27}.get(key, 0)


def swipe(b, x0, y0, x1, y1, steps=8):
    """A real touch gesture. Needs Emulation.setTouchEmulationEnabled first.

    Swipe was broken for as long as it existed and nobody noticed, because
    nothing here could produce a touch: a mouse drag does not reproduce it,
    since the bug was the browser claiming the gesture and sending
    pointercancel instead of pointerup.
    """
    b.send("Input.dispatchTouchEvent", type="touchStart",
           touchPoints=[{"x": x0, "y": y0}])
    for i in range(1, steps + 1):
        b.send("Input.dispatchTouchEvent", type="touchMove",
               touchPoints=[{"x": x0 + (x1 - x0) * i / steps,
                             "y": y0 + (y1 - y0) * i / steps}])
        time.sleep(0.02)
    b.send("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
    b.settle()


def check(label, got, want):
    ok = got == want
    print(f"[{'  ok  ' if ok else ' FAIL '}] {label}: {got!r}" +
          ("" if ok else f"  (expected {want!r})"))
    return ok


def main():
    chrome = subprocess.Popen(
        ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={PORT}", "--window-size=1280,900",
         "--remote-allow-origins=*",
         "--user-data-dir=/tmp/cdp-profile-nav", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = 0
    try:
        for _ in range(60):
            try:
                tabs = json.load(
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                ws_url = [t for t in tabs
                          if t["type"] == "page"][0]["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("chrome did not start")

        b = Browser(websocket.create_connection(ws_url, timeout=30))
        b.send("Page.enable")
        b.send("Runtime.enable")

        b.goto(URL)
        album = b.eval("location.pathname")
        depth_at_album = b.eval("history.length")
        print(f"album: {album}  (history.length {depth_at_album})")

        # Armed here so it is part of the document that gets cached; if that
        # document is restored rather than rebuilt, the flag comes back with it.
        b.eval("window.__hpRestored = false;"
               "window.addEventListener('pageshow', function (e) {"
               "  if (e.persisted) window.__hpRestored = true; });")

        # Open the first photo the way a person does.
        opened = b.eval(
            "(function(){var a=document.querySelector('#grid a');"
            "if(!a)return null;a.click();return a.getAttribute('href');})()")
        if not opened:
            raise SystemExit("no photo link in the grid — pick an album with photos")
        b.settle()
        first = b.eval("location.pathname")
        failures += not check("opening a photo adds one history entry",
                              b.eval("history.length"), depth_at_album + 1)

        # Page forward three times.
        for _ in range(3):
            b.key("ArrowRight")
        third = b.eval("location.pathname")
        failures += not check("paging moved to another photo", third != first, True)
        failures += not check("paging added NO history entries",
                              b.eval("history.length"), depth_at_album + 1)

        # The on-screen arrows must behave exactly like the keys. They are
        # real links, so without interception each click pushed a history
        # entry and Back went one photo back instead of to the album.
        before = b.eval("history.length")
        b.eval("(function(){var a=document.querySelector('.stage a.nav.next');"
               "if(a)a.click();})()")
        b.settle()
        failures += not check("clicking the arrow added NO history entry",
                              b.eval("history.length"), before)

        # Escape must land on the album, in one step.
        b.key("Escape")
        failures += not check("Escape returns to the album",
                              b.eval("location.pathname"), album)
        # back() moves the pointer, it does not shorten the list, so the
        # length is unchanged -- what matters is that paging never grew it.
        failures += not check("...without having grown history",
                              b.eval("history.length"), depth_at_album + 1)

        # And the grid must have come from the back/forward cache rather than
        # being rebuilt.
        #
        # Measured with pageshow.persisted, not the navigation type. A genuine
        # bfcache restore reuses the very same document, so its navigation
        # entry still reads "navigate" from the original load -- reading
        # "back_forward" there actually means the page was rebuilt, which is
        # the opposite of what is wanted. The listener below survives into the
        # restored document precisely because it is the same document.
        # Advisory, not a failure. The browser decides whether to keep a page
        # in the bfcache and legitimately declines -- observed passing and
        # failing on consecutive runs of identical code -- so asserting it
        # produces false alarms. What the code controls is that Escape is a
        # history navigation at all, which is checked above; the restore is the
        # browser's to grant.
        restored = b.eval("window.__hpRestored === true")
        print(f"[{'  ok  ' if restored else ' note ' }] restored from the bfcache: "
              f"{restored}" + ("" if restored
                               else "  (opportunistic; the browser may decline)"))

        # The neighbouring photo must be fetched while this one is on screen,
        # or every arrow press waits a full round trip for an image that could
        # have been loaded during the seconds you spent looking at the last one.
        b.goto(URL)
        b.eval("(function(){document.querySelector('#grid a').click();})()")
        b.settle(2.5)
        nxt = b.eval("(function(){var d=document.getElementById('nav-data');"
                     "return d?JSON.parse(d.textContent).next:null;})()")
        if nxt:
            stem = nxt.rsplit("/", 1)[-1]
            fetched = b.eval(
                "performance.getEntriesByType('resource')"
                ".filter(function(e){return e.name.indexOf('/i/')>=0 &&"
                " e.name.indexOf(" + json.dumps(stem) + ")>=0;})"
                ".map(function(e){return e.name;})")
            failures += not check("the next photo was prefetched",
                                  bool(fetched), True)
            if fetched:
                tier = fetched[0].split("/i/")[1].split("/")[0]
                print(f"         prefetched tier {tier} "
                      f"(window is 1280 wide, so 1280 is right; 1600 would be waste)")

        # A link is not a Back. Restoring a remembered position on an ordinary
        # navigation dropped you into the middle of an album you had merely
        # linked to, which is not what following a link means.
        b.goto(URL)
        b.eval("window.scrollTo(0, 2000)")
        b.settle()
        b.goto("about:blank")
        b.goto(URL)
        failures += not check("following a link starts at the top",
                              b.eval("Math.round(window.scrollY)"), 0)

        # Swipe, with genuine touch events on an emulated phone.
        b.send("Emulation.setDeviceMetricsOverride", width=390, height=844,
               deviceScaleFactor=2, mobile=True)
        b.send("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
        b.goto(URL)
        b.eval("(function(){document.querySelector('#grid a').click();})()")
        b.settle()
        opened = b.eval("location.pathname")
        depth = b.eval("history.length")

        swipe(b, 320, 420, 60, 420)                    # left: forwards
        moved = b.eval("location.pathname")
        failures += not check("swipe left pages forward", moved != opened, True)

        swipe(b, 60, 420, 320, 420)                    # right: backwards
        failures += not check("swipe right pages back",
                              b.eval("location.pathname"), opened)
        failures += not check("swiping added no history entries",
                              b.eval("history.length"), depth)

        swipe(b, 200, 300, 200, 640)                   # down: to the album
        failures += not check("swipe down returns to the album",
                              b.eval("location.pathname"), album)

        b.send("Emulation.setTouchEmulationEnabled", enabled=False)
        b.send("Emulation.clearDeviceMetricsOverride")

        # A photo opened cold must not send you off the site.
        b.goto("about:blank")
        b.goto(URL.rstrip("/").rsplit("/a/", 1)[0] + opened)
        b.key("Escape")
        failures += not check("Escape from a deep link still reaches the album",
                              b.eval("location.pathname"), album)

    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()

    print()
    print("all checks passed" if not failures else f"{failures} check(s) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
