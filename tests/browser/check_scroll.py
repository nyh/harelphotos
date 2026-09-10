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
    print("album loaded, height =", js("document.body.scrollHeight"))
    js("window.scrollTo(0, 1500)")
    time.sleep(0.6)
    before = js("Math.round(window.scrollY)")
    print("scrolled to", before)
    stored = js("sessionStorage.getItem('hp:scroll:' + location.pathname)")
    print("sessionStorage says", stored)

    href = js("document.querySelector('#grid .tile').href")
    print("opening", href.split('/p/')[-1])
    goto(href)
    print("on photo page:", js("location.pathname"))

    # Press Escape exactly as a user would.
    cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape",
        windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
    cmd("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape",
        windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
    time.sleep(3.5)

    where = js("location.pathname")
    after = js("Math.round(window.scrollY)")
    print(f"after Escape:        at {where}, scrollY = {after}")
    results = [("Escape from one photo", where.endswith("/ancient/") and abs(after - before) < 100)]

    # ...and after paging through several photos with the arrow keys.
    js("window.scrollTo(0, 900)"); time.sleep(0.6)
    goto(js("document.querySelector('#grid .tile').href"))
    for _ in range(3):
        cmd("Input.dispatchKeyEvent", type="keyDown", key="ArrowRight",
            code="ArrowRight", windowsVirtualKeyCode=39, nativeVirtualKeyCode=39)
        cmd("Input.dispatchKeyEvent", type="keyUp", key="ArrowRight",
            code="ArrowRight", windowsVirtualKeyCode=39, nativeVirtualKeyCode=39)
        time.sleep(2.5)
    print("paged to:           ", js("location.pathname"))
    cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape",
        windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
    cmd("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape",
        windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
    time.sleep(3.5)
    where2, after2 = js("location.pathname"), js("Math.round(window.scrollY)")
    print(f"after paging+Escape: at {where2}, scrollY = {after2} (wanted ~900)")
    results.append(("Escape after paging 3 photos", where2.endswith("/ancient/") and abs(after2 - 900) < 100))

    # And the Back button must still work as it always did.
    js("window.scrollTo(0, 1800)"); time.sleep(0.6)
    goto(js("document.querySelector('#grid .tile').href"))
    cmd("Page.navigateToHistoryEntry", entryId=cmd("Page.getNavigationHistory")["entries"][-2]["id"])
    time.sleep(3.0)
    where3, after3 = js("location.pathname"), js("Math.round(window.scrollY)")
    print(f"after Back:          at {where3}, scrollY = {after3} (wanted ~1800)")
    results.append(("Back button", where3.endswith("/ancient/") and abs(after3 - 1800) < 200))

    print()
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    raise SystemExit(0 if all(ok for _, ok in results) else 1)
finally:
    chrome.terminate()
