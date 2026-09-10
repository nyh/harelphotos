"""Check the justified grid in a real browser.

    pip install websocket-client          # not a runtime dependency
    harelphotos serve --port 5090
    python tests/browser/check_layout.py http://127.0.0.1:5090/a/some-album/

The one invariant that matters: **a row is exactly as wide as the grid**. That
is the entire promise of a justified layout, and it is arithmetic done in
JavaScript against measurements only the browser has, so nothing in the pytest
suite can check it.

It exists because the arithmetic was once done with one aspect ratio and the
widths rendered with another: rows were measured with the aspect clamped to
3.0, then each tile was given `height * true aspect`. An album containing a
1916x320 panorama produced a 1784px row inside a 1248px grid and the photos ran
off the side of the page.

Point it at an album with a very wide photo in it. An album of ordinary
snapshots will pass whatever the code does.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import subprocess
import sys
import time
import urllib.request

import websocket

PORT = 9350
URL = sys.argv[1]
WIDTHS = (1280, 1024, 768, 390)


def main():
    chrome = subprocess.Popen(
        ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         "--user-data-dir=/tmp/cdp-layout", "about:blank"],
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

        ws = websocket.create_connection(ws_url, timeout=30)
        n = [0]

        def send(method, **params):
            n[0] += 1
            ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == n[0]:
                    return msg.get("result", {})

        send("Page.enable")
        for width in WIDTHS:
            send("Emulation.setDeviceMetricsOverride", width=width, height=900,
                 deviceScaleFactor=1, mobile=width < 500)
            send("Page.navigate", url=URL)
            time.sleep(2.5)
            r = send("Runtime.evaluate", returnByValue=True, expression="""
              (function(){
                var g = document.getElementById('grid');
                if (!g) return null;
                var gap = parseInt(g.dataset.gap, 10) || 4;
                var rows = [].map.call(g.querySelectorAll('.row'), function (rw) {
                  var tiles = [].map.call(rw.children, function (t) {
                    return {w: t.getBoundingClientRect().width,
                            ar: parseFloat(t.dataset.ar) || 1};
                  });
                  var total = tiles.reduce(function (a, t) { return a + t.w; }, 0)
                              + gap * (tiles.length - 1);
                  return {n: tiles.length, total: Math.round(total),
                          widest: Math.max.apply(null, tiles.map(function (t) {
                            return t.ar; }))};
                });
                return {gw: Math.round(g.clientWidth), rows: rows,
                        sideways: document.documentElement.scrollWidth
                                  > window.innerWidth + 1};
              })()""")
            v = r.get("result", {}).get("value")
            if not v:
                print(f"[ FAIL ] {width}px: no grid on the page")
                failures += 1
                continue
            if not v["rows"]:
                print(f"[ note ] {width}px: album has no photos, nothing to check")
                continue

            widest = max(r["widest"] for r in v["rows"])
            # The last row is deliberately not stretched to fill: a single
            # leftover photo would otherwise become absurdly large.
            over = [r for r in v["rows"][:-1] if abs(r["total"] - v["gw"]) > 2]
            ok = not over and not v["sideways"]
            failures += not ok
            print(f"[{'  ok  ' if ok else ' FAIL '}] {width:>4}px grid={v['gw']}px, "
                  f"{len(v['rows'])} rows, widest aspect {widest:.2f}"
                  + ("" if ok else f"  — {len(over)} row(s) not flush"
                                   + (", page scrolls sideways" if v["sideways"] else "")))
            for r in over:
                print(f"           {r['n']} tiles totalling {r['total']}px "
                      f"in a {v['gw']}px grid")
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
