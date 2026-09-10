"""Check both colour schemes in a real browser.

    pip install websocket-client pillow      # neither is a runtime dependency
    harelphotos serve --port 5090
    python tests/browser/check_dark.py http://127.0.0.1:5090 /a/some-album/

The second argument is an album holding photographs; without one the photo page
cannot be reached and is skipped.

Dark mode is a palette of CSS variables, so nothing in the pytest suite can
tell whether the page actually paints dark -- only a browser resolving
`prefers-color-scheme` can. This renders the login page, an album and a photo
in both schemes and samples the pixels.

It also checks the two things that a tokenised palette leaves behind: whether
`color-scheme` is declared, without which form controls and scrollbars stay
light while everything else darkens, and whether the `theme-color` meta follows
the scheme, without which a phone's status bar stays white above a dark page.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

import base64
import io
import json
import subprocess
import sys
import time
import urllib.request

import websocket
from PIL import Image

PORT = 9360
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5090"
ALBUM = sys.argv[2] if len(sys.argv) > 2 else "/a/"
PAGES = [("login", "/login"),
         ("album", ALBUM),
         ("photo", None)]          # a photo from that album


def luminance(rgb):
    c = [v / 255 for v in rgb]
    c = [(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4) for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def main():
    chrome = subprocess.Popen(
        ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         "--window-size=1100,800", "--user-data-dir=/tmp/cdp-dark", "about:blank"],
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

        def value(expr):
            return send("Runtime.evaluate", returnByValue=True,
                        expression=expr)["result"].get("value")

        send("Page.enable")
        for scheme in ("light", "dark"):
            send("Emulation.setEmulatedMedia",
                 features=[{"name": "prefers-color-scheme", "value": scheme}])
            for label, path in PAGES:
                if path is None:
                    send("Page.navigate", url=BASE + ALBUM); time.sleep(1.5)
                    path = value("(document.querySelector('#grid a')||{})"
                                 ".getAttribute?"
                                 "document.querySelector('#grid a')"
                                 ".getAttribute('href'):null")
                    if not path:
                        print(f"[ note ] {scheme:<5} no photo to check")
                        continue
                send("Page.navigate", url=BASE + path)
                time.sleep(2.2)
                shot = send("Page.captureScreenshot", format="png")
                im = Image.open(io.BytesIO(
                    base64.b64decode(shot["data"]))).convert("RGB")
                # A corner, well away from any photograph.
                corner = im.getpixel((im.width - 6, 6))
                declared = value("getComputedStyle(document.documentElement)"
                                 ".colorScheme")
                theme = value(
                    "(document.querySelector('meta[name=theme-color]"
                    f"[media*=\\\"{scheme}\\\"]')||{{}}).content || ''")
                is_dark = luminance(corner) < 0.2
                ok = (is_dark if scheme == "dark" else not is_dark) and bool(theme)
                failures += not ok
                print(f"[{'  ok  ' if ok else ' FAIL '}] {scheme:<5} {label:<6} "
                      f"corner {corner}  color-scheme {declared!r}  "
                      f"theme-color {theme or '(missing)'}")
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()

    print()
    print("both schemes paint correctly" if not failures
          else f"{failures} check(s) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
