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
      return { el: el, ar: isFinite(ar) && ar > 0 ? ar : 1 };
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
      // Clamp when accumulating, or one 3:1 panorama drags its whole row down
      // to a sliver.
      var ar = Math.min(3.0, Math.max(0.4, item.ar));
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

    // Only on a fresh navigation: for Back and Forward the browser restores
    // the position itself, and doing it twice fights with it.
    var entries = window.performance && window.performance.getEntriesByType
      ? window.performance.getEntriesByType("navigation") : [];
    var kind = entries.length ? entries[0].type : "navigate";
    if (kind !== "navigate") return;

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
    rememberScroll(grid);
    window.addEventListener("resize", relayout, { passive: true });
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

    // Horizontal swipe pages; a downward swipe returns to the album. Vertical
    // scrolling is left alone.
    var img = document.getElementById("main");
    if (!img || !window.PointerEvent) return;
    var startX = 0, startY = 0, tracking = false;
    img.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "mouse") return;
      tracking = true; startX = e.clientX; startY = e.clientY;
    }, { passive: true });
    img.addEventListener("pointerup", function (e) {
      if (!tracking) return;
      tracking = false;
      var dx = e.clientX - startX, dy = e.clientY - startY;
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) {
        go(dx < 0 ? nav.next : nav.prev);
      } else if (dy > 90 && Math.abs(dy) > Math.abs(dx)) {
        backToAlbum();
      }
    }, { passive: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { initGrid(); initViewer(); });
  } else {
    initGrid();
    initViewer();
  }
})();
