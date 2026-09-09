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

    var rows = [];
    var row = [];
    var sum = 0;
    items.forEach(function (item) {
      row.push(item);
      // Clamp when accumulating, or one 3:1 panorama drags its whole row down
      // to a sliver.
      sum += Math.min(3.0, Math.max(0.4, item.ar));
      var h = (width - gap * (row.length - 1)) / sum;
      if (h <= target) {
        rows.push({ items: row, height: h });
        row = [];
        sum = 0;
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
        item.el.style.width = Math.round(r.height * item.ar) + "px";
        item.el.style.height = Math.round(r.height) + "px";
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
        case "Escape":     go(nav.album); break;
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
        go(nav.album);
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
