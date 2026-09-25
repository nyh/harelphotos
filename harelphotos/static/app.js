/* harelphotos — the browser side.
 *
 * Plain JavaScript, no build step, no framework. Two jobs: lay the photo grid
 * out in justified rows, and make the single-photo page navigable by keyboard
 * and swipe.
 *
 * The page is complete and usable without any of this — the CSS flexbox rule
 * already produces very nearly the same grid, and the prev/next links are real
 * links. This only makes it exact and quick.
 */

/*
 * Copyright (C) 2026 Nadav Har'El
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
(function () {
  "use strict";

  var FROM_ALBUM_KEY = "hp:openedFrom";

  /* ------------------------------------------------ justified row layout */

  // Photos keep their true aspect ratio and are never cropped: each row is
  // filled left to right, then every tile in it is scaled to one common height
  // chosen so the row exactly fills the container.
  function layoutGrid(grid) {
    var tiles = Array.prototype.slice.call(grid.querySelectorAll(".tile"));
    if (!tiles.length) return;

    // Read every aspect ratio from the markup rather than measuring the DOM,
    // so the whole layout is one pass of arithmetic with no forced reflows.
    // This is what keeps a 3000-photo album fast.
    var items = tiles.map(function (el) {
      var ar = parseFloat(el.dataset.ar);
      if (!isFinite(ar) || ar <= 0) ar = 1;
      // Bounded only against nonsense in the data, never for layout: the row
      // arithmetic and the width it produces have to use the SAME number. They
      // did not -- a row was measured with the aspect clamped to 3 and then
      // rendered at the true one, so a 6:1 panorama made its row about twice
      // the width of the page and everything spilled off the side.
      return { el: el, ar: Math.min(20, Math.max(0.05, ar)) };
    });

    var width = grid.clientWidth;
    if (!width) return;
    var gap = parseInt(grid.dataset.gap, 10) || 4;
    var target = width < 600 ? 130 : 180;

    function heightFor(n, sum) {
      return (width - gap * (n - 1)) / sum;
    }

    var rows = [];
    var row = [];
    var sum = 0;
    items.forEach(function (item) {
      var ar = item.ar;
      var withoutIt = row.length ? heightFor(row.length, sum) : Infinity;
      row.push(item);
      sum += ar;
      var withIt = heightFor(row.length, sum);
      if (withIt <= target) {
        // Adding this one took the row past the target. Whether to keep it
        // depends on which side lands closer: breaking as soon as the height
        // dips below target systematically overshoots, and with portrait
        // photos (whose narrow tiles accumulate slowly) rows came out at
        // ~150px against a 180px target — visibly smaller thumbnails than
        // intended.
        if (Math.abs(withoutIt - target) < Math.abs(withIt - target)) {
          row.pop();
          sum -= ar;
          rows.push({ items: row, height: withoutIt });
          row = [item];
          sum = ar;
        } else {
          rows.push({ items: row, height: withIt });
          row = [];
          sum = 0;
        }
      }
    });
    if (row.length) {
      // The last row is never stretched to fill: a single leftover photo would
      // become absurdly large.
      var h = (width - gap * (row.length - 1)) / sum;
      rows.push({ items: row, height: Math.min(target, h) });
    }

    var frag = document.createDocumentFragment();
    rows.forEach(function (r) {
      var div = document.createElement("div");
      div.className = "row";
      div.style.height = Math.round(r.height) + "px";
      // Lets the browser skip layout and paint for off-screen rows: native
      // virtualisation, for free, because we laid out in rows.
      div.style.containIntrinsicSize = Math.round(r.height) + "px";
      r.items.forEach(function (item) {
        var w = Math.round(r.height * item.ar);
        item.el.style.width = w + "px";
        item.el.style.height = Math.round(r.height) + "px";
        // Now that the real width is known, tell the browser: otherwise it
        // picks from srcset using an estimate and visibly upscales the wider
        // photos. Set before most lazy images have loaded, so in practice the
        // right file is the only one ever fetched.
        var img = item.el.firstElementChild;
        if (img && img.srcset) img.sizes = w + "px";
        div.appendChild(item.el);
      });
      frag.appendChild(div);
    });
    grid.textContent = "";
    grid.appendChild(frag);
    grid.classList.add("justified");
  }

  // Remember where you were in an album, and come back to it. The browser
  // does this by itself for Back, but not for a fresh navigation, which is
  // what Escape from a photo performs.
  function rememberScroll(grid) {
    var key = "hp:scroll:" + window.location.pathname;
    var pending = false;

    window.addEventListener("scroll", function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        pending = false;
        try { sessionStorage.setItem(key, String(Math.round(window.scrollY))); } catch (e) {}
      });
    }, { passive: true });

    // Only for Back and Forward, never for a fresh navigation.
    //
    // This was the other way round, from when Escape left a photo by
    // navigating to the album rather than stepping back through history. The
    // effect was that following an ordinary *link* to an album you had visited
    // earlier dropped you somewhere in its middle, which is not what a link
    // means -- and Back, the one case where restoring is wanted, was skipped.
    //
    // It is still needed for Back: the browser restores a position of its own,
    // but the justified rows are laid out afterwards and change every height,
    // so its guess lands in the wrong place. When the page comes back from the
    // bfcache instead, none of this runs at all -- the document was never torn
    // down, and the position was never lost.
    var entries = window.performance && window.performance.getEntriesByType
      ? window.performance.getEntriesByType("navigation") : [];
    var kind = entries.length ? entries[0].type : "navigate";
    if (kind !== "back_forward") return;

    var y = 0;
    try { y = parseInt(sessionStorage.getItem(key), 10) || 0; } catch (e) { y = 0; }
    if (y > 0) {
      // After the rows have been laid out, so the page is already its final
      // height; otherwise the scroll is clamped to a shorter document.
      window.scrollTo(0, y);
    }
  }

  // Records that we are leaving this album by opening one of its photos, so
  // the photo page can tell whether Back leads to the grid (see backToAlbum).
  function markOpenedFromAlbum(grid) {
    grid.addEventListener("click", function (e) {
      var a = e.target.closest ? e.target.closest("a") : null;
      if (!a || !a.href) return;
      try {
        sessionStorage.setItem(FROM_ALBUM_KEY, JSON.stringify({
          album: window.location.pathname,
          t: Date.now()
        }));
      } catch (err) {}
    });
  }

  // Keep the browser's request queue short, so what you are looking at is not
  // stuck behind what you have already scrolled past.
  //
  // `loading="lazy"` starts images well before they are needed, and once a
  // request is queued it stays queued -- scroll through a 400-photo album and
  // hundreds of requests for photos now far off-screen sit ahead of the ones
  // filling the screen. Abandoning those frees the slots immediately.
  //
  // Only ever abandons an image that has NOT finished loading: a completed one
  // keeps its srcset, so nothing is fetched twice.
  //
  // It also retries images that failed to load. The browser never refetches
  // a failed image by itself, so without this a tile whose request failed
  // (say, a server timeout) stays blank until the page is reloaded. On
  // failure we park the image's URLs, as for a tile that left the range, and
  // restore them, which fetches the image again:
  //  * whenever the tile comes back into range, however many times it failed;
  //  * while it stays in range, after 1, 2, 4, ... seconds, up to RETRIES times
  //    in a row -- the count and the delay start over once one of them works;
  //  * when the browser reports that the network is back ("online").
  function limitLoading(grid) {
    if (!("IntersectionObserver" in window)) return;   // native lazy only
    var RETRIES = 5;

    function park(img) {
      // Park the URLs rather than dropping them, and clear the attributes,
      // which is what actually cancels an in-flight request.
      if (img.getAttribute("srcset")) {
        img.dataset.srcset = img.getAttribute("srcset");
        img.removeAttribute("srcset");
      }
      if (img.getAttribute("src")) {
        img.dataset.src = img.getAttribute("src");
        img.removeAttribute("src");
      }
    }

    function restore(img) {
      if (img.dataset.srcset) {
        img.setAttribute("srcset", img.dataset.srcset);
        delete img.dataset.srcset;
      }
      if (img.dataset.src) {
        img.setAttribute("src", img.dataset.src);
        delete img.dataset.src;
      }
      if ("fetchPriority" in img) img.fetchPriority = "high";
    }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var img = e.target.firstElementChild;
        if (!img || img.tagName !== "IMG") return;     // a pending placeholder
        img.hpNear = e.isIntersecting;
        if (e.isIntersecting) restore(img);
        else if (!img.complete) park(img);
      });
    // Two viewports of slack: far enough that scrolling normally never waits,
    // near enough that the queue stays short.
    }, { rootMargin: "200% 0px" });
    Array.prototype.forEach.call(grid.querySelectorAll(".tile"), function (t) {
      io.observe(t);
    });

    // Capturing, because `error` does not bubble.
    grid.addEventListener("error", function (e) {
      var img = e.target;
      if (img.tagName !== "IMG") return;
      park(img);
      var tries = (img.hpTries || 0) + 1;
      img.hpTries = tries;
      if (tries > RETRIES) return;
      setTimeout(function () {
        if (img.hpNear) restore(img);
      }, 1000 * Math.pow(2, tries - 1));
    }, true);

    // A retry that worked ends that failure: count from zero, so RETRIES is
    // five attempts at one failure and not five for the life of the page. A
    // tile is fetched again long after it loaded when `sizes` changes and the
    // browser picks a different file out of the srcset -- a rotated phone, a
    // resized window -- and that fetch deserves its own five. Capturing,
    // because `load` does not bubble either.
    grid.addEventListener("load", function (e) {
      if (e.target.tagName === "IMG") e.target.hpTries = 0;
    }, true);

    // Back from a dead connection: whatever failed on the way down can come
    // now, rather than at the next pause or the next scroll.
    window.addEventListener("online", function () {
      Array.prototype.forEach.call(grid.querySelectorAll(".tile > img"), function (img) {
        if (img.hpNear) restore(img);
      });
    });
  }

  function initGrid() {
    var grid = document.getElementById("grid");
    if (!grid) return;
    markOpenedFromAlbum(grid);
    var lastWidth = 0;
    var pending = false;
    function relayout() {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        pending = false;
        if (grid.clientWidth === lastWidth) return;
        lastWidth = grid.clientWidth;
        layoutGrid(grid);
      });
    }
    lastWidth = grid.clientWidth;
    layoutGrid(grid);
    limitLoading(grid);
    rememberScroll(grid);
    window.addEventListener("resize", relayout, { passive: true });

    // The window is not the only thing that changes the grid's width. Laying
    // the rows out makes the page shorter, which can remove the vertical
    // scrollbar, which widens the grid by about 15px -- and `resize` does not
    // fire for that, so the rows stayed a scrollbar short of the edge. The
    // guard inside relayout() stops this chasing its own tail.
    if (window.ResizeObserver) {
      new ResizeObserver(relayout).observe(grid);
    }
  }

  /* ---------------------------------------------------- single photo view */

  function initViewer() {
    var el = document.getElementById("nav-data");
    if (!el) return;
    var nav;
    try { nav = JSON.parse(el.textContent); } catch (e) { return; }

    // Paging replaces the current history entry instead of adding one.
    //
    // With a push per photo, Back walked backwards through every photo you had
    // looked at -- ten arrow presses meant ten Backs to reach the album again,
    // which is not what Back means here. Replacing keeps history at
    // [album, the photo you are on], so Back always means "return to the
    // grid", however far along you have paged.
    function go(url) {
      if (!url) return;
      keepTicket();
      window.location.replace(url);
    }

    // Opening a photo from the grid is a normal link, so it does add one
    // entry -- which is the one Back consumes.
    /* Is there an album behind us to step back to?
     *
     * Decided once, here, because the answer cannot change while this page is
     * open -- and because the test is about how we *arrived*, which is only
     * knowable on arrival.
     *
     * The guard is recency, not history depth. Depth was the obvious test and
     * was wrong: `history.length === mark.depth + 1` assumes opening a
     * photograph appends an entry, and it does not when a forward entry
     * exists. Go album -> photo -> back -> photo and the second click
     * *replaces* the forward entry, so the length never changes. Measured:
     * 2 to 3 on the first click, 3 to 3 on the second. So every photograph
     * after the first in one visit lost its way back.
     *
     * What actually distinguishes a click from a pasted URL is that a click
     * writes this mark microseconds before the page loads. The window is
     * short, and refreshing the mark below keeps paging alive indefinitely
     * without widening it -- paging is a page load too, so it renews the
     * lease each time.
     *
     * The case this still gets wrong is a deep link pasted within seconds of
     * leaving a photograph of the same album, where Back leads somewhere
     * else. It is a narrow window, and both outcomes put you on that album.
     */
    var FROM_ALBUM_TTL = 30000;

    var cameFromAlbum = (function () {
      var raw;
      try { raw = sessionStorage.getItem(FROM_ALBUM_KEY); } catch (e) { return false; }
      // Consumed, always, whatever it says. That single line is what makes
      // this exact: the mark is a ticket for *one* page load, written by the
      // click that caused it, and spent on arrival. A photograph opened later
      // from a pasted link finds nothing, because the previous load took it.
      try { sessionStorage.removeItem(FROM_ALBUM_KEY); } catch (e) {}
      if (!raw) return false;
      var mark;
      try { mark = JSON.parse(raw); } catch (e) { return false; }
      if (!mark || mark.album !== nav.album) return false;
      // Belt and braces for a ticket written by a click that never arrived --
      // the reader changed their mind, or the navigation failed.
      return Date.now() - mark.t < FROM_ALBUM_TTL;
    })();

    // Paging replaces this page, so hand the ticket on: the next photograph is
    // the same visit to the same album, and it reaches that page the same way
    // the album's click reaches this one -- written microseconds before the
    // navigation that consumes it.
    function keepTicket() {
      if (!cameFromAlbum) return;
      try {
        sessionStorage.setItem(FROM_ALBUM_KEY, JSON.stringify({
          album: nav.album, t: Date.now()
        }));
      } catch (e) {}
    }

    // Escape and the Back button return to the album.
    //
    // history.back() where it is genuinely a step back, because that restores
    // the grid from the browser's back/forward cache: already laid out, images
    // already painted, scroll position kept. A fresh navigation cannot use
    // that cache and rebuilds the whole page instead.
    //
    // The fallback matters: arriving straight at a photo means there is no
    // album behind us to go back to. An earlier attempt used document.referrer
    // to tell the two apart, which could never work -- this site sends
    // `Referrer-Policy: no-referrer`, so the referrer is always empty.
    function backToAlbum() {
      if (cameFromAlbum) { window.history.back(); return; }
      window.location.href = nav.album;
    }

    /* Show the back arrow, but only when it has something to pop.
     *
     * Escape does this on a keyboard and there is no equivalent on a phone. An
     * installed site on iOS has no back button and no back gesture either --
     * `touch-action: none` on the stage suppresses the edge swipe, and a
     * sideways swipe is paging here anyway -- so a photograph was a dead end
     * but for the breadcrumb, which leads to the top of the album rather than
     * back to the place you were looking at.
     *
     * Conditional on `cameFromAlbum` for the same reason the button is
     * hidden in the markup: on a photograph opened from a shared link there is
     * nothing behind us, `backToAlbum` would fall through to a fresh
     * navigation, and an arrow that means "back" would be doing something
     * else. The breadcrumb is the honest way out of that case.
     *
     * Revealed from script rather than rendered visible, because without
     * script it could not work at all. */
    var backBtn = document.getElementById("back-to-album");
    if (backBtn) {
      // Wired unconditionally, and only its visibility is conditional. The
      // two were tied together at first, which made a visible button with no
      // handler possible -- and it happened: `.iconbutton` sets `display`,
      // which beats the `hidden` attribute, so the button showed on every
      // photograph while only some of them could act on a tap. It highlighted
      // under the finger and did nothing. Behavior that cannot disagree with
      // what is on screen is worth more than the saved listener.
      backBtn.addEventListener("click", function (e) {
        e.preventDefault();
        backToAlbum();
      });
      // The stage turns a pointerdown into a pan or a swipe. Tapping a control
      // that sits on top of it is neither.
      ["pointerdown", "pointerup", "touchstart"].forEach(function (t) {
        backBtn.addEventListener(t, function (e) { e.stopPropagation(); });
      });
      backBtn.hidden = !cameFromAlbum;
    }

    // Fetch the neighboring photos while this one is being looked at.
    //
    // Paging is a page load, so without this every arrow press waits a full
    // round trip for an image that could have been fetched during the seconds
    // the previous photo was on screen. The browser chooses the size from the
    // same srcset/sizes the visible image uses -- a phone must not pull the
    // 1600px file just because it is next.
    //
    // Deliberately after load: a prefetch that competes with the photo you are
    // actually looking at has made things worse, not better.
    function prefetchNeighbours() {
      var list = nav.prefetch || [];
      for (var i = 0; i < list.length; i++) {
        if (!list[i]) continue;
        var img = new Image();
        if (nav.sizes) img.sizes = nav.sizes;
        img.srcset = list[i];
        // Lowest priority: this is speculative work for a photo nobody has
        // asked for yet.
        if ("fetchPriority" in img) img.fetchPriority = "low";
      }
    }
    if (document.readyState === "complete") {
      prefetchNeighbours();
    } else {
      window.addEventListener("load", prefetchNeighbours);
    }

    // The on-screen arrows must page the same way the keys do.
    //
    // They are real links, so clicking one pushed a history entry and Back
    // went to the previous photo instead of the album -- the very thing
    // location.replace() avoids for the arrow keys. Left as links on purpose:
    // middle-click, ctrl-click and "open in new tab" keep working, and they
    // still function with no JavaScript at all.
    Array.prototype.forEach.call(
      document.querySelectorAll(".stage a.nav"),
      function (a) {
        a.addEventListener("click", function (e) {
          if (e.defaultPrevented || e.button !== 0) return;
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
          e.preventDefault();
          go(a.getAttribute("href"));
        });
      }
    );

    /* The information panel, and whether it stays open as you page.
     *
     * Paging is a whole page load, so without remembering it the panel closed
     * on every photograph and had to be opened again from the menu -- which is
     * not what "show me the details" means when you are comparing a series.
     *
     * Kept in sessionStorage, so it lasts as long as the tab and no longer.
     * localStorage would remember it next week too, which is the behaviour of
     * a *setting*; this is a mode you turn on while looking at something, and
     * having it reappear a month later would be a surprise rather than a
     * convenience.
     */
    var INFO_KEY = "hp:info";
    var info = document.getElementById("info");
    var toggle = document.getElementById("info-toggle");
    var closer = document.getElementById("info-close");

    function showInfo(open) {
      if (!info) return;
      info.hidden = !open;
      if (toggle) toggle.setAttribute("aria-expanded", String(open));
      try {
        if (open) sessionStorage.setItem(INFO_KEY, "1");
        else sessionStorage.removeItem(INFO_KEY);
      } catch (e) {}
    }
    function toggleInfo() { if (info) showInfo(info.hidden); }

    if (toggle) toggle.addEventListener("click", toggleInfo);
    if (closer) closer.addEventListener("click", function () { showInfo(false); });

    if (info) {
      var wanted = false;
      try { wanted = sessionStorage.getItem(INFO_KEY) === "1"; } catch (e) {}
      if (wanted) showInfo(true);
    }

    document.addEventListener("keydown", function (e) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      var tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      switch (e.key) {
        case "ArrowLeft":  go(nav.prev); break;
        case "ArrowRight": go(nav.next); break;
        case "Escape":     backToAlbum(); break;
        case "i": case "I": toggleInfo(); break;
        case "d": case "D": go(nav.download); break;
        default: return;
      }
      e.preventDefault();
    });

    // Gestures on the photo: swipe sideways to page, pinch to zoom, drag to
    // pan while zoomed, double-tap to zoom in and out.
    //
    // The paging swipes belong to an un-zoomed photograph only. Once it is
    // magnified the same movement is a pan, and taking it would send the
    // reader to the next photograph every time they looked at a right-hand
    // edge.
    //
    // Bound to the stage, not the image, so the letterboxed margins either
    // side of a photo count too -- on a wide screen with a tall photo those
    // are most of what your thumb can reach.
    //
    // None of it worked at all until `touch-action: none` was set on the stage
    // (see app.css): without it the browser claims a drag as a pan of its own,
    // sends pointercancel, and the pointerup being listened for never arrives.
    // That same line is why pinching did nothing either -- it turns off the
    // browser's built-in zoom along with everything else, and an installed app
    // has no page zoom to fall back on. Having taken the gesture we owe the
    // reader an implementation of it.
    var stage = document.querySelector(".stage");
    var img = document.getElementById("main");

    // The stylesheet hides this image's alt text, so that the filename does
    // not flash in the corner while the photograph loads. If it never loads,
    // put it back: an empty frame that does not say what is missing is worse
    // than a moment of text. Registered before the guard below, because it has
    // nothing to do with zooming and should work without PointerEvent.
    if (img) {
      img.addEventListener("error", function () {
        img.classList.add("load-failed");
      });
    }

    if (!stage || !window.PointerEvent) return;

    /* How far in a zoom may go.
     *
     * The number that means something is one pixel of the original per
     * *physical* screen pixel: past that there is no more detail in the file,
     * only larger pixels. For a 4000px photograph on a 390pt phone at three
     * device pixels per point that lands near 3.4x, and on a desktop showing
     * it 1200px wide, near 3.3x -- so a single fixed limit is generous for a
     * big photograph and mean to a huge one.
     *
     * Overshoot past that is wanted, and three times it is the figure chosen.
     * Every photo application allows a good deal -- "make this small thing big
     * enough to read" does not stop being useful at the pixel grid, and nobody
     * clamps at 1:1. What ends up limiting the reader is not blur but
     * navigation: panning moves the picture in proportion to the zoom, so past
     * about ten times a thumb's width throws you across the photograph and you
     * lose track of where you were looking. Hence the absolute ceiling, which
     * only bites on very large files.
     *
     * The floor is for the other end: a small scan would otherwise refuse to
     * enlarge at all, when peering at a face in one is exactly the reason to
     * zoom. */
    var OVERZOOM = 3;
    var MIN_MAX_SCALE = 3;
    var MAX_SCALE = 12;
    var DOUBLE_TAP_SCALE = 2.5;
    // Two rungs, because they cost wildly different amounts. `sizes` will have
    // fetched something screen-sized -- often 1280 or less on a phone -- so the
    // largest generated copy is a few hundred kilobytes that sharpens the
    // picture straight away, and is worth taking almost as soon as anyone
    // zooms. The original is the only thing sharper and costs megabytes, so it
    // waits until somebody is clearly looking closely.
    var LARGE_AT = 1.3;
    var FULL_AT = 2.5;
    // Not worth pulling a raw file this big down a phone connection to sharpen
    // a photograph somebody is looking at for a few seconds.
    var FULL_MAX_BYTES = 24 * 1024 * 1024;

    var scale = 1, tx = 0, ty = 0;
    var pointers = {};        // active pointers by id
    var pinch = null;         // {dist, cx, cy, scale, tx, ty} at gesture start
    var panning = false, panActive = false, lastPointerType = "";

    /* Dragging a photograph sideways to page.
     *
     * Not decoration. The swipe used to do nothing whatever until the finger
     * lifted, and then the photograph was simply replaced -- so there was no
     * moment where anything said "this gesture is working, and this is what it
     * will do". Moving the picture with the thumb says both, before the reader
     * has committed to anything, and lets them change their mind by dragging
     * back. Every gallery on a phone does this.
     *
     * `swipeX` is kept apart from `tx`, which belongs to panning a zoomed
     * photograph, so the two never have to reason about each other: paging is
     * for a photograph at rest and panning for one magnified, and each writes
     * its own number. */
    var swipeX = 0, swiping = false;
    var SWIPE_START = 12;      // before this, it might still be a tap
    var SWIPE_COMMIT = 60;     // past this, lifting off pages
    var EDGE_RESISTANCE = 0.3; // drag towards a photograph that is not there
    var startX = 0, startY = 0, movedX = 0, movedY = 0;
    var lastTap = 0, lastTapX = 0, lastTapY = 0;

    /* What separates a tap from a drag, in pixels and milliseconds.
     *
     * These began at 10px and 300ms, borrowed from a mouse, and double-tapping
     * was reported as nearly impossible: a fingertip is a centimetre across and
     * rolls as it lifts, so tap after tap was being judged a tiny drag. Worse,
     * a "drag" did not even record the attempt, so one sloppy tap discarded the
     * pair and the next tap started again from nothing.
     *
     * 18px is about a finger's wobble; 400ms is comfortably slower than anyone
     * taps twice on purpose. The taps must also land near each other, or two
     * unrelated taps at opposite corners would count. */
    var TAP_SLOP = 18;
    var TAP_MS = 400;
    var TAP_NEAR = 60;

    /* A dead zone before a pan begins, for the same reason: without it the
     * wobble of a tap dragged the photograph a few pixels, which both looked
     * like the picture twitching and guaranteed the tap was scored a drag. */
    var PAN_SLOP = 8;

    function zoomed() { return scale > 1.01; }

    /* The rectangle the photograph itself occupies inside the element.
     *
     * `object-fit: contain` letterboxes it, so the element is not the picture:
     * on a wide screen showing a tall photo most of the element is empty. Pan
     * limits computed against the element would let the picture be dragged
     * entirely off the screen and leave the reader looking at nothing. */
    function pictureBox() {
      var w = img.clientWidth, h = img.clientHeight;
      var nw = img.naturalWidth, nh = img.naturalHeight;
      if (!nw || !nh) return { w: w, h: h };
      var s = Math.min(w / nw, h / nh);
      return { w: nw * s, h: nh * s };
    }

    function clamp() {
      var box = pictureBox();
      // How far the scaled picture overflows the element, each way. With
      // nothing to spare the picture stays centered, which is why this is
      // max(0, ...) rather than an absolute value.
      var mx = Math.max(0, (box.w * scale - img.clientWidth) / 2);
      var my = Math.max(0, (box.h * scale - img.clientHeight) / 2);
      tx = Math.min(mx, Math.max(-mx, tx));
      ty = Math.min(my, Math.max(-my, ty));
    }

    function apply(animate) {
      clamp();
      img.style.transition = animate ? "transform 0.18s ease-out" : "";
      var x = tx + swipeX;
      img.style.transform = scale === 1 && !x && !ty
        ? "" : "translate(" + x + "px," + ty + "px) scale(" + scale + ")";
      stage.classList.toggle("zoomed", zoomed());
      if (zoomed()) upgrade();
    }

    /* Fetch a sharper copy once the one on screen is being magnified.
     *
     * Loaded in the background and put in place only when it has arrived, so
     * the photograph never blanks out in the middle of a gesture. Each rung is
     * taken at most once, and a failure is not retried -- a zoom that is
     * merely soft is a great deal better than one that stutters.
     *
     * `srcset` has to be removed along the way. Setting `src` alone changes
     * nothing while a srcset is present: the browser picks from its
     * candidates, and neither of these rungs is one of them. */
    var rung = 0;                 // 0 = as delivered, 1 = largest copy, 2 = original
    var fetching = false;

    function swapTo(url, level) {
      if (fetching || rung >= level || !url) return;
      // Already showing it: `sizes` may well have chosen the largest copy by
      // itself on a wide screen, and re-fetching it would be pure waste.
      if (img.currentSrc && img.currentSrc.indexOf(url) >= 0) {
        rung = level;
        return;
      }
      fetching = true;
      var next = new Image();
      next.onload = function () {
        fetching = false;
        rung = level;
        img.removeAttribute("srcset");
        img.removeAttribute("sizes");
        img.src = url;
      };
      next.onerror = function () { fetching = false; rung = level; };
      if ("fetchPriority" in next) next.fetchPriority = "high";
      next.src = url;
    }

    function upgrade() {
      if (scale >= FULL_AT && nav.full) {
        if (nav.fullBytes && nav.fullBytes > FULL_MAX_BYTES) return;
        var conn = navigator.connection;
        if (conn && conn.saveData) return;    // the reader asked us not to
        swapTo(nav.full, 2);
      } else if (scale >= LARGE_AT) {
        swapTo(nav.large, 1);
      }
    }

    /* Zoom about a point, so the pixel under the fingers stays under them. */
    /* Where the middle of the photo's box is when nothing is transformed.
     *
     * Deliberately not `img.getBoundingClientRect()`, which reports the element
     * as currently *painted*. That is a different thing from the state this
     * code is holding in `scale`, `tx` and `ty`, and during a pinch the two are
     * never the same: each move event resets those variables to the values the
     * gesture started with, while the screen still shows the result of the
     * previous frame. Anchoring against the painted position therefore used a
     * frame-old reference that changed on every event, and the error came out
     * as the photograph sliding continuously under the fingers -- worse the
     * more events the gesture generated, which is exactly what moving one
     * finger does.
     *
     * `offsetLeft` and friends ignore transforms, and the stage is never
     * transformed, so this is a fixed reference that does not depend on what
     * has been drawn. */
    function untransformedCenter() {
      var s = stage.getBoundingClientRect();
      return { x: s.left + img.offsetLeft + img.offsetWidth / 2,
               y: s.top + img.offsetTop + img.offsetHeight / 2 };
    }

    /* Zoom to `next` about the screen point (cx, cy), starting from an
     * explicit state rather than from whatever is on screen.
     *
     * Taking the base as an argument is what makes a pinch a pure function of
     * where it started and how far apart the fingers are now. Nothing
     * accumulates from frame to frame, so there is no drift to build up. */
    /* The limit for this photograph on this screen, from its real size. */
    function maxScale() {
      var box = pictureBox();
      var dpr = window.devicePixelRatio || 1;
      if (!nav.fullW || !box.w) return MAX_SCALE;
      var oneToOne = nav.fullW / (box.w * dpr);
      return Math.max(MIN_MAX_SCALE,
                      Math.min(MAX_SCALE, oneToOne * OVERZOOM));
    }

    function zoomFrom(base, next, cx, cy) {
      next = Math.min(maxScale(), Math.max(1, next));
      var c = untransformedCenter();
      // The anchor, in the picture's own coordinates, measured from the
      // center. Here the translation *is* subtracted, because `c` is the
      // untransformed center and `base.tx` is where the picture sits relative
      // to it.
      var ox = (cx - c.x - base.tx) / base.scale;
      var oy = (cy - c.y - base.ty) / base.scale;
      scale = next;
      tx = base.tx + ox * (base.scale - next);
      ty = base.ty + oy * (base.scale - next);
      if (scale === 1) { tx = 0; ty = 0; }
    }

    function zoomTo(next, cx, cy) {
      zoomFrom({ scale: scale, tx: tx, ty: ty }, next, cx, cy);
    }

    function reset(animate) { scale = 1; tx = 0; ty = 0; apply(animate); }

    function center(a, b) {
      return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }
    function spread(a, b) {
      return Math.hypot(a.x - b.x, a.y - b.y);
    }
    function twoPointers() {
      var ids = Object.keys(pointers);
      return ids.length === 2 ? [pointers[ids[0]], pointers[ids[1]]] : null;
    }

    stage.addEventListener("pointerdown", function (e) {
      lastPointerType = e.pointerType;
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      var two = twoPointers();
      if (two) {
        // A second finger: stop whatever the first was doing and start a
        // pinch from wherever the picture currently sits. A half-finished
        // page-swipe is put back first, so that a pinch begun during one does
        // not zoom a photograph that is sitting an inch off-centre.
        panning = false;
        if (swiping) { swiping = false; swipeX = 0; }
        var c = center(two[0], two[1]);
        pinch = { dist: spread(two[0], two[1]) || 1, cx: c.x, cy: c.y,
                  scale: scale, tx: tx, ty: ty };
        return;
      }
      if (e.pointerType === "mouse" && !zoomed()) return;
      startX = movedX = e.clientX;
      startY = movedY = e.clientY;
      panning = true;
      panActive = false;
      swiping = false;
      swipeX = 0;
      try { stage.setPointerCapture(e.pointerId); } catch (err) {}
    }, { passive: true });

    stage.addEventListener("pointermove", function (e) {
      if (!pointers[e.pointerId]) return;
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      var two = twoPointers();
      if (two && pinch) {
        // Zoom only, about the point the fingers started from, and no pan.
        //
        // Following the current midpoint instead -- so the picture travels
        // with the hand as well as growing under it, the way a sheet of paper
        // would -- was tried and is wrong here. Two fingers never move by
        // exactly the same amount, so every pinch also slid the photograph
        // sideways by however much the midpoint had drifted, and it read as
        // the picture wandering off on its own rather than as panning. Pan is
        // its own gesture, with one finger, once the zoom is finished.
        //
        // The anchor is the *starting* midpoint rather than the current one
        // for the same reason: a moving anchor reintroduces the drift in a
        // subtler form.
        //
        // Computed from `pinch`, the state the gesture began in, and not from
        // the state left by the previous move event. The whole pinch is
        // therefore a pure function of one number -- how far apart the fingers
        // are now, against how far apart they started -- so there is nothing
        // for an error to accumulate in, and putting the fingers back where
        // they began puts the photograph back exactly where it began.
        zoomFrom(pinch, pinch.scale * (spread(two[0], two[1]) / pinch.dist),
                 pinch.cx, pinch.cy);
        apply(false);
        return;
      }
      if (!panning) return;

      // A photograph at rest: a sideways drag is paging, and the picture
      // follows the thumb so that the gesture is visibly doing something.
      if (!zoomed() && e.pointerType !== "mouse") {
        var sdx = e.clientX - startX, sdy = e.clientY - startY;
        if (!swiping) {
          // Committed to sideways, and far enough not to be a tap. Downward
          // is the album gesture and is left alone.
          if (Math.abs(sdx) < SWIPE_START || Math.abs(sdx) <= Math.abs(sdy)) return;
          swiping = true;
        }
        // Dragging towards a photograph that does not exist still moves, but
        // grudgingly: the picture pulling back against the thumb is how the
        // end of an album announces itself without a message.
        var wanted = sdx < 0 ? nav.next : nav.prev;
        swipeX = wanted ? sdx : sdx * EDGE_RESISTANCE;
        apply(false);
        return;
      }

      if (zoomed()) {
        // Nothing moves until the finger has clearly committed to a drag.
        if (!panActive) {
          if (Math.abs(e.clientX - startX) < PAN_SLOP &&
              Math.abs(e.clientY - startY) < PAN_SLOP) {
            // Keep the origin current, so the pan starts from where the finger
            // is now rather than jumping by the slop it has already used up.
            movedX = e.clientX;
            movedY = e.clientY;
            return;
          }
          panActive = true;
        }
        tx += e.clientX - movedX;
        ty += e.clientY - movedY;
        apply(false);
      }
      movedX = e.clientX;
      movedY = e.clientY;
    }, { passive: true });

    function endPointer(e) {
      delete pointers[e.pointerId];
      if (Object.keys(pointers).length < 2) pinch = null;
      try { stage.releasePointerCapture(e.pointerId); } catch (err) {}
    }

    stage.addEventListener("pointercancel", function (e) {
      panning = false;
      // Whatever took the gesture away -- a phone call, a notification, the
      // browser deciding it wants it -- the photograph must not be left
      // stranded halfway off the screen.
      if (swiping) { swiping = false; swipeX = 0; apply(true); }
      endPointer(e);
    }, { passive: true });

    stage.addEventListener("pointerup", function (e) {
      var wasPanning = panning;
      var dx = e.clientX - startX, dy = e.clientY - startY;
      panning = false;
      var hadTwo = !!twoPointers();
      endPointer(e);
      if (hadTwo) {
        // Lifting one finger of a pinch: settle, and snap back rather than
        // leaving the photograph at 1.02 with the pan handler still armed.
        if (scale < 1.05) reset(true); else apply(true);
        return;
      }
      if (!wasPanning) return;

      var moved = Math.abs(dx) > TAP_SLOP || Math.abs(dy) > TAP_SLOP;
      if (!moved) {
        var now = Date.now();
        var near = Math.abs(e.clientX - lastTapX) < TAP_NEAR &&
                   Math.abs(e.clientY - lastTapY) < TAP_NEAR;
        if (now - lastTap < TAP_MS && near) {
          lastTap = 0;
          if (zoomed()) reset(true);
          else { zoomTo(DOUBLE_TAP_SCALE, e.clientX, e.clientY); apply(true); }
        } else {
          lastTap = now;
          lastTapX = e.clientX;
          lastTapY = e.clientY;
        }
        return;
      }
      // A real drag ends any half-finished double tap, so that a tap, a pan
      // and a tap are not mistaken for one.
      lastTap = 0;
      // Paging gestures belong to an un-zoomed photograph. Zoomed in, the
      // same movement is a pan, and stealing it would make the photo jump to
      // the next one whenever somebody looked at its right-hand edge.
      if (zoomed()) { swiping = false; return; }
      if (e.pointerType === "mouse") return;

      if (swiping) {
        swiping = false;
        var target = dx < 0 ? nav.next : nav.prev;
        if (Math.abs(dx) > SWIPE_COMMIT && target) {
          // Send the photograph the rest of the way out, and start the
          // navigation in the same breath rather than after it. The browser
          // keeps showing this document until the next one is ready to paint,
          // so the animation plays during the load instead of delaying it --
          // free where it is quick, and useful feedback where it is not.
          swipeX = dx < 0 ? -stage.clientWidth : stage.clientWidth;
          apply(true);
          go(target);
        } else {
          // Not far enough, or nothing to go to. Back where it came from,
          // which is also how the reader takes the gesture back.
          swipeX = 0;
          apply(true);
        }
        return;
      }

      // A downward swipe used to return to the album, and does not any more.
      // It is not a gesture anyone expects a photograph to have, and it was
      // taking one that people do expect: pulling a page down to reload it.
      // Escape and the Back button both still lead back, and both are things
      // a reader reaches for deliberately.
    }, { passive: true });

    // A mouse and a trackpad. Ctrl+wheel is what a trackpad pinch arrives as,
    // and what every other image viewer uses; a bare wheel is left alone.
    stage.addEventListener("wheel", function (e) {
      if (!e.ctrlKey) return;
      e.preventDefault();
      zoomTo(scale * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX, e.clientY);
      apply(false);
    }, { passive: false });

    // A mouse only, and this guard is the whole reason double-tap on a phone
    // hardly ever worked.
    //
    // Chrome for Android synthesizes `dblclick` from a double tap, so both this
    // and the tap detection above were firing for the same two taps: the taps
    // zoomed in, and the synthetic dblclick arrived immediately afterwards,
    // found `zoomed()` true, and zoomed straight back out. The gesture worked
    // only when the timing happened to let one of them through alone -- which
    // is exactly "I can only do it rarely".
    stage.addEventListener("dblclick", function (e) {
      if (lastPointerType !== "mouse") return;
      e.preventDefault();
      if (zoomed()) reset(true);
      else { zoomTo(DOUBLE_TAP_SCALE, e.clientX, e.clientY); apply(true); }
    });

    // Escape closes the zoom before it leaves the photograph, which is what
    // the key means everywhere else: undo the last thing you opened.
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && zoomed()) {
        e.preventDefault();
        e.stopImmediatePropagation();
        reset(true);
      } else if (e.key === "0" && zoomed()) {
        reset(true);
      }
    }, true);
  }

  /* Sharing a link to the page you are looking at.
   *
   * Installed to a home screen the site runs with no address bar -- which is
   * most of the point of installing it, and which takes away the only way
   * anyone had to copy a link and send it to somebody. This puts that back.
   *
   * `navigator.share` hands the phone's own share sheet the current URL, which
   * is what every other application on the device does and so needs no
   * explaining. Where there is no share sheet a desktop browser almost always
   * has a clipboard, and a link on the clipboard is the same job done quietly.
   *
   * The button ships hidden and is revealed here, so a browser with neither
   * capability shows no control rather than one that does nothing. Both APIs
   * want a secure context, so over plain HTTP on a development machine there
   * is legitimately nothing to offer.
   */
  function initShare() {
    var button = document.getElementById("share");
    if (!button) return;
    var canShare = typeof navigator.share === "function";
    var canCopy = !!(navigator.clipboard && navigator.clipboard.writeText);
    if (!canShare && !canCopy) return;
    button.hidden = false;

    function toast(text) {
      var el = document.createElement("div");
      el.className = "toast";
      el.setAttribute("role", "status");
      el.textContent = text;
      document.body.appendChild(el);
      // Two frames: the element has to be in the document at opacity 0 before
      // the class that raises it, or there is no transition to watch.
      requestAnimationFrame(function () {
        requestAnimationFrame(function () { el.classList.add("show"); });
      });
      setTimeout(function () {
        el.classList.remove("show");
        setTimeout(function () { el.remove(); }, 300);
      }, 1600);
    }

    button.addEventListener("click", function () {
      var url = window.location.href;
      if (canShare) {
        // A rejected promise is the reader dismissing the sheet. That is not
        // an error, and must not fall through to copying instead.
        navigator.share({ title: document.title, url: url }).catch(function () {});
        return;
      }
      navigator.clipboard.writeText(url).then(
        function () { toast("Link copied"); },
        function () { toast("Could not copy the link"); }
      );
    });
  }

  /* The overflow menu opens and closes by itself -- it is a `<details>`, and
   * that is the whole reason for using one. These are the two things the
   * element does not do, and which every reader expects of a menu: close when
   * you press Escape, and close when you touch something else.
   *
   * The Escape listener runs in the capture phase and stops there, so that
   * closing an open menu does not also reset a zoom or leave the photograph.
   * Escape means "undo the last thing you opened", and the menu is it. */
  function initMenu() {
    var menu = document.querySelector("details.menu");
    if (!menu) return;
    document.addEventListener("click", function (e) {
      if (!menu.open) return;
      // Outside: dismissed. Inside and on an item: used, which also means
      // done -- "Photo information" opens a panel this menu would otherwise
      // be sitting on top of. The summary is excluded, being the thing that
      // toggles the menu in the first place.
      var item = e.target.closest ? e.target.closest(".menu-item") : null;
      if (!menu.contains(e.target) || item) menu.open = false;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.open) {
        e.preventDefault();
        e.stopImmediatePropagation();
        menu.open = false;
        var summary = menu.querySelector("summary");
        if (summary) summary.focus();
      }
    }, true);
  }

  /* initMenu before initViewer, and the order is load-bearing: both listen for
   * Escape in the capture phase, and capture listeners run in the order they
   * were registered. Registered the other way round, Escape with a menu open
   * over a zoomed photograph would reset the zoom and leave the menu up. */
  function start() { initGrid(); initMenu(); initViewer(); initShare(); }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
