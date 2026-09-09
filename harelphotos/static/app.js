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

  function initGrid() {
    var grid = document.getElementById("grid");
    if (!grid) return;
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
    window.addEventListener("resize", relayout, { passive: true });
  }

  /* ---------------------------------------------------- single photo view */

  function initViewer() {
    var el = document.getElementById("nav-data");
    if (!el) return;
    var nav;
    try { nav = JSON.parse(el.textContent); } catch (e) { return; }

    function go(url) { if (url) window.location.href = url; }

    // Returning to the album must restore the scroll position, which only the
    // browser's own history can do — navigating to the album URL afresh lands
    // you back at the top. So Escape (and a downward swipe) step *back*
    // through history rather than navigating.
    //
    // Paging with the arrow keys pushes a history entry per photo, so after
    // three photos the album is three steps back, not one. That depth is
    // tracked in sessionStorage, keyed by album, and reset whenever we arrive
    // from somewhere that is not another photo in the same album.
    var DEPTH_KEY = "hp:depth:" + nav.album;

    function sameOrigin(url) {
      return !!url && url.indexOf(window.location.origin + "/") === 0;
    }

    function trackDepth() {
      var ref = document.referrer;
      var depth = 1;
      if (sameOrigin(ref)) {
        var path = ref.slice(window.location.origin.length);
        if (path.indexOf("/p/") === 0) {
          // Arrived from another photo page: one step deeper.
          try {
            depth = (parseInt(sessionStorage.getItem(DEPTH_KEY), 10) || 1) + 1;
          } catch (e) { depth = 2; }
        }
      }
      try { sessionStorage.setItem(DEPTH_KEY, String(depth)); } catch (e) {}
      return depth;
    }

    var depth = trackDepth();

    function backToAlbum() {
      // Only trust history if we actually came from within the site: someone
      // opening a shared link directly has nothing to go back to, and
      // history.back() would take them off the site entirely.
      if (sameOrigin(document.referrer) && window.history.length > 1) {
        try { sessionStorage.removeItem(DEPTH_KEY); } catch (e) {}
        window.history.go(-depth);
        return;
      }
      go(nav.album);
    }

    // Fetch the neighbours now, so paging feels instant rather than like a
    // page load.
    (nav.preload || []).forEach(function (src) {
      if (src) { var i = new Image(); i.src = src; }
    });

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
