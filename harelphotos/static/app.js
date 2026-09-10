/* harelphotos (DESIGN.md 11.1c, 11.2).
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
          depth: window.history.length
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
  function limitLoading(grid) {
    if (!("IntersectionObserver" in window)) return;   // native lazy only
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var img = e.target.firstElementChild;
        if (!img || img.tagName !== "IMG") return;     // a pending placeholder
        if (e.isIntersecting) {
          if (img.dataset.srcset) {
            img.setAttribute("srcset", img.dataset.srcset);
            delete img.dataset.srcset;
          }
          if (img.dataset.src) {
            img.setAttribute("src", img.dataset.src);
            delete img.dataset.src;
          }
          if ("fetchPriority" in img) img.fetchPriority = "high";
        } else if (!img.complete) {
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
      });
    // Two viewports of slack: far enough that scrolling normally never waits,
    // near enough that the queue stays short.
    }, { rootMargin: "200% 0px" });
    Array.prototype.forEach.call(grid.querySelectorAll(".tile"), function (t) {
      io.observe(t);
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
    function go(url) { if (url) window.location.replace(url); }

    // Opening a photo from the grid is a normal link, so it does add one
    // entry -- which is the one Back consumes.
    function openedFromAlbum() {
      var raw;
      try { raw = sessionStorage.getItem(FROM_ALBUM_KEY); } catch (e) { return false; }
      if (!raw) return false;
      var mark;
      try { mark = JSON.parse(raw); } catch (e) { return false; }
      // Same album, and exactly one entry deeper than when the tile was
      // clicked. Both must hold: a stale mark from earlier in the session
      // would otherwise send a deep link (a bookmark, a pasted URL) backwards
      // out of the site entirely.
      return mark && mark.album === nav.album &&
             window.history.length === mark.depth + 1;
    }

    // Escape and a downward swipe return to the album.
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
      if (openedFromAlbum()) { window.history.back(); return; }
      window.location.href = nav.album;
    }

    // Fetch the neighbouring photos while this one is being looked at.
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

    var info = document.getElementById("info");
    var toggle = document.getElementById("info-toggle");
    function toggleInfo() {
      if (!info) return;
      var showing = !info.hidden;
      info.hidden = showing;
      if (toggle) toggle.setAttribute("aria-expanded", String(!showing));
    }
    if (toggle) toggle.addEventListener("click", toggleInfo);

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

    // Gestures on the photo. Unchanged: swipe sideways to page, swipe down to
    // return to the album. Added: pinch to zoom, drag to pan while zoomed,
    // double-tap to zoom in and out.
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
    if (!stage || !window.PointerEvent) return;

    var MAX_SCALE = 6;
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
    var panning = false;
    var startX = 0, startY = 0, movedX = 0, movedY = 0;
    var lastTap = 0;

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
      // nothing to spare the picture stays centred, which is why this is
      // max(0, ...) rather than an absolute value.
      var mx = Math.max(0, (box.w * scale - img.clientWidth) / 2);
      var my = Math.max(0, (box.h * scale - img.clientHeight) / 2);
      tx = Math.min(mx, Math.max(-mx, tx));
      ty = Math.min(my, Math.max(-my, ty));
    }

    function apply(animate) {
      clamp();
      img.style.transition = animate ? "transform 0.18s ease-out" : "";
      img.style.transform = scale === 1 && !tx && !ty
        ? "" : "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
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
    function zoomTo(next, cx, cy) {
      next = Math.min(MAX_SCALE, Math.max(1, next));
      var r = img.getBoundingClientRect();
      // Where the anchor sits relative to the element's centre, in the
      // untransformed coordinate space.
      var ox = (cx - (r.left + r.width / 2) - tx) / scale;
      var oy = (cy - (r.top + r.height / 2) - ty) / scale;
      tx += ox * (scale - next);
      ty += oy * (scale - next);
      scale = next;
      if (scale === 1) { tx = 0; ty = 0; }
    }

    function reset(animate) { scale = 1; tx = 0; ty = 0; apply(animate); }

    function centre(a, b) {
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
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      var two = twoPointers();
      if (two) {
        // A second finger: stop whatever the first was doing and start a
        // pinch from wherever the picture currently sits.
        panning = false;
        var c = centre(two[0], two[1]);
        pinch = { dist: spread(two[0], two[1]) || 1, cx: c.x, cy: c.y,
                  scale: scale, tx: tx, ty: ty };
        return;
      }
      if (e.pointerType === "mouse" && !zoomed()) return;
      startX = movedX = e.clientX;
      startY = movedY = e.clientY;
      panning = true;
      try { stage.setPointerCapture(e.pointerId); } catch (err) {}
    }, { passive: true });

    stage.addEventListener("pointermove", function (e) {
      if (!pointers[e.pointerId]) return;
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      var two = twoPointers();
      if (two && pinch) {
        var c = centre(two[0], two[1]);
        scale = pinch.scale;
        tx = pinch.tx;
        ty = pinch.ty;
        zoomTo(pinch.scale * (spread(two[0], two[1]) / pinch.dist),
               pinch.cx, pinch.cy);
        // Following the midpoint means the picture moves with the hand as
        // well as growing under it, which is what makes it feel like paper.
        tx += c.x - pinch.cx;
        ty += c.y - pinch.cy;
        apply(false);
        return;
      }
      if (!panning) return;
      if (zoomed()) {
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

      var moved = Math.abs(dx) > 10 || Math.abs(dy) > 10;
      if (!moved) {
        var now = Date.now();
        if (now - lastTap < 300) {
          lastTap = 0;
          if (zoomed()) reset(true);
          else { zoomTo(DOUBLE_TAP_SCALE, e.clientX, e.clientY); apply(true); }
        } else {
          lastTap = now;
        }
        return;
      }
      // Paging gestures belong to an un-zoomed photograph. Zoomed in, the
      // same movement is a pan, and stealing it would make the photo jump to
      // the next one whenever somebody looked at its right-hand edge.
      if (zoomed()) return;
      if (e.pointerType === "mouse") return;
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) {
        go(dx < 0 ? nav.next : nav.prev);
      } else if (dy > 90 && Math.abs(dy) > Math.abs(dx)) {
        backToAlbum();
      }
    }, { passive: true });

    // A mouse and a trackpad. Ctrl+wheel is what a trackpad pinch arrives as,
    // and what every other image viewer uses; a bare wheel is left alone.
    stage.addEventListener("wheel", function (e) {
      if (!e.ctrlKey) return;
      e.preventDefault();
      zoomTo(scale * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX, e.clientY);
      apply(false);
    }, { passive: false });

    stage.addEventListener("dblclick", function (e) {
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { initGrid(); initViewer(); });
  } else {
    initGrid();
    initViewer();
  }
})();
