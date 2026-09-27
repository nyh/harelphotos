"""Check that "Share original" appears only where it would work.

Not part of the pytest suite: it needs Chrome. Run it with the others through
`run_all.py`, or by hand:

    harelphotos serve --port 5090 &
    python tests/browser/check_share.py http://127.0.0.1:5090/p/album/p000.jpg

Sharing a file is the half of the Web Share API that many browsers do not
have: Firefox has no share sheet, a desktop Linux browser none either, and
`navigator.share` exists in places that take text and refuse files. The item
therefore ships hidden and app.js reveals it only after `canShare` has agreed
to a file. Both halves of that are worth holding still, because each fails
quietly: left hidden it is a feature nobody can find, and revealed wrongly it
is a control that does nothing when tapped.

So this runs the page twice against an injected `navigator`: once refusing
files, once accepting them. The real one is whatever this machine happens to
have, which is why it is not used for either case.
"""

# Copyright (C) 2026 Nadav Har'El
# SPDX-License-Identifier: AGPL-3.0-or-later

import json, shutil, subprocess, time, urllib.request, sys
from pathlib import Path

import websocket

PORT = 9340
PROFILE = "/tmp/cdp-share"
DOWNLOADS = "/tmp/cdp-share-downloads"
URL = sys.argv[1]

shutil.rmtree(DOWNLOADS, ignore_errors=True)
Path(DOWNLOADS).mkdir(parents=True)

# Replaces `share`/`canShare` before any of the page's own script runs, and
# records what the page asked about so the probe itself can be checked.
STUB = """
  window.__asked = [];
  window.__shared = [];
  navigator.share = function (data) {
    var files = (data && data.files) || [];
    // Kept whole, so the bytes themselves can be looked at afterwards: a
    // file's declared type is what the page put there, not what it holds.
    window.__lastFile = files[0] || null;
    window.__shared.push(files.map(function (f) {
      return { name: f.name, type: f.type, size: f.size };
    }));
    return Promise.resolve();
  };
  Object.defineProperty(navigator, 'canShare', {
    configurable: true,
    value: function (data) {
      window.__asked.push({
        files: (data && data.files || []).map(function (f) {
          return { name: f.name, type: f.type, size: f.size };
        })
      });
      return %s;
    }
  });
"""

chrome = subprocess.Popen(
    ["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
     f"--remote-debugging-port={PORT}", "--window-size=1280,800",
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

    def js(expr):
        r = cmd("Runtime.evaluate", expression=expr, returnByValue=True,
                awaitPromise=True)
        return r.get("result", {}).get("value")

    cmd("Page.enable")
    cmd("Runtime.enable")

    def load(answer):
        """Open the photo with a `canShare` that gives this answer."""
        cmd("Page.removeScriptToEvaluateOnNewDocument",
            identifier=load.prev) if getattr(load, "prev", None) else None
        r = cmd("Page.addScriptToEvaluateOnNewDocument", source=STUB % answer)
        load.prev = r.get("identifier")
        cmd("Page.navigate", url=URL)
        time.sleep(2.0)

    results = []

    # 1. A browser whose share sheet refuses files: no control at all.
    load("false")
    hidden = js("document.getElementById('share-original').hidden")
    small = js("document.getElementById('share-smaller').hidden")
    asked = js("window.__asked")
    print(f"refusing files: hidden={hidden}/{small}, "
          f"canShare asked {len(asked)} time(s)")
    results.append(("hidden where files cannot be shared", hidden is True))
    results.append(("smaller copy hidden there too", small is True))
    results.append(("asked canShare before deciding", len(asked) == 1))

    # But saving one is something every browser can do, and is the whole
    # reason the conversion does not live behind the share test: a reader on
    # Firefox has no share sheet and must still be able to get the small JPEG.
    saveable = js("document.getElementById('save-smaller').hidden")
    print(f"                download offered anyway: {saveable is False}")
    results.append(("smaller copy still downloadable", saveable is False))

    # The question has to be about a file that a share sheet would entertain:
    # named like the photograph, typed as an image, and not empty.
    probe = asked[0]["files"][0] if asked and asked[0]["files"] else {}
    print(f"probe: {probe}")
    results.append(("probe carries the photo's name",
                    str(probe.get("name", "")).endswith(".jpg")))
    results.append(("probe is a non-empty image", probe.get("size", 0) > 0
                    and str(probe.get("type", "")).startswith("image/")))

    # 2. A browser that accepts them: the controls are there, and say so.
    load("true")
    hidden = js("document.getElementById('share-original').hidden")
    text = js("document.querySelector('#share-original .label').textContent")
    small = js("document.getElementById('share-smaller').hidden")
    stext = js("document.querySelector('#share-smaller .label').textContent")
    print(f"accepting files: hidden={hidden}/{small}, "
          f"labels={text!r}, {stext!r}")
    results.append(("shown where files can be shared", hidden is False))
    results.append(("labelled", text == "Share original"))
    results.append(("smaller copy offered too", small is False))
    results.append(("smaller copy labelled", stext == "Share a smaller copy"))

    # And the label goes back to itself after a share, rather than being left
    # saying "Preparing" for the rest of the page's life.
    js("document.getElementById('share-original').click()")
    time.sleep(2.0)
    after = js("document.querySelector('#share-original .label').textContent")
    still = js("document.getElementById('share-original').hidden")
    sent = js("window.__shared")
    got = sent[0][0] if sent and sent[0] else {}
    print(f"after sharing:   label={after!r}, handed over {got}")
    results.append(("label restored after sharing", after == "Share original"))
    results.append(("still offered afterwards", still is False))
    # The whole point: what reaches the sheet is the original file, fetched
    # from the server -- not the one-byte stand-in used to ask the question,
    # and not a failed fetch quietly turning into nothing.
    results.append(("shared the original itself",
                    got.get("name") == "p000.jpg"
                    and str(got.get("type", "")).startswith("image/")
                    and got.get("size", 0) > 1000))

    # 3. The smaller copy. What leaves here has to be a JPEG whatever the
    #    server stores, because it is going to somebody else's mail client --
    #    and it has to be smaller than the original, or it is pure loss. The
    #    first two bytes settle the format: no AVIF starts 0xFF 0xD8.
    js("window.__shared = []; document.getElementById('share-smaller').click()")
    time.sleep(3.0)
    sent = js("window.__shared")
    copy = sent[0][0] if sent and sent[0] else {}
    magic = js("""(async () => {
        const f = window.__lastFile;
        if (!f) return null;
        const b = new Uint8Array(await f.slice(0, 2).arrayBuffer());
        return [b[0], b[1]];
    })()""")
    label = js("document.querySelector('#share-smaller .label').textContent")
    print(f"smaller copy:    {copy}, first bytes {magic}")
    results.append(("smaller copy is a JPEG",
                    copy.get("type") == "image/jpeg" and magic == [255, 216]))
    results.append(("smaller copy is smaller than the original",
                    0 < copy.get("size", 0) < got.get("size", 1)))
    results.append(("smaller copy label restored",
                    label == "Share a smaller copy"))

    # 4. The same copy, saved instead of shared. It has to arrive under a name
    #    of its own: dropped beside the original in a downloads folder, two
    #    files called the same thing are told apart only by the "(1)" the
    #    browser adds, and by then you cannot tell which is which.
    cmd("Page.setDownloadBehavior", behavior="allow", downloadPath=DOWNLOADS)
    tier = js("JSON.parse(document.getElementById('nav-data').textContent).largeTier")
    js("document.getElementById('save-smaller').click()")
    time.sleep(3.0)
    files = sorted(p.name for p in Path(DOWNLOADS).iterdir()) if \
        Path(DOWNLOADS).is_dir() else []
    want = f"p000-{tier}.jpg"
    sizes = {p.name: p.stat().st_size for p in Path(DOWNLOADS).iterdir()} if \
        Path(DOWNLOADS).is_dir() else {}
    print(f"downloaded:      {files} (wanted {want!r}), sizes {sizes}")
    results.append(("smaller copy saved under its own name", want in files))
    results.append(("saved copy has the original's bytes in it",
                    sizes.get(want, 0) > 1000))

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
    shutil.rmtree(DOWNLOADS, ignore_errors=True)
