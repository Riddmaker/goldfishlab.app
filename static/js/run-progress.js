// The run page while a run plays (phase 10 T3.1, T3.2). An enhancement only:
// without this file the bar shows the real share from the server, and the
// first loading line stays.
//
// The bar. A real batch can take half a minute to land, and a bar that sits
// still that long reads as broken. So it shows the largest of three values:
// the one it showed last, the real one, and a curve 95 * (1 - e^(-t/tau))
// with tau half the expected duration - quick at first, slower as it nears
// 95, never there. It never moves backwards and never shows 100 before the
// run is done (the page then reloads into the report). htmx replaces the
// fragment every two seconds; the value is kept per run across the swaps.
// While the run waits in the queue the curve stops at 10: a queue can be
// long, and a bar at 90 above the word "Queued" would promise what nobody is
// doing yet. Once a worker starts it, the curve counts from that moment.
//
// The lines. One at a time, a new one every three to five seconds, in random
// order and none twice until all have been shown. Still under reduced motion.
(() => {
  "use strict";

  const REACH = 95;
  const CEILING = 99;
  const QUEUED_CEILING = 10;
  const TICK_MS = 500;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  // Run id -> {origin, queued, shown}: origin is when the run was asked for,
  // or started once it plays, on this page's clock (the server counts the
  // seconds, so a wrong local clock does not matter); shown is on screen.
  const runs = new Map();

  const curve = (seconds, expected) =>
    REACH * (1 - Math.exp(-seconds / Math.max(1, expected / 2)));

  function stateFor(root) {
    const id = root.dataset.runProgress;
    const queued = "queued" in root.dataset;
    const state = runs.get(id) || { shown: 0 };
    if (!runs.has(id) || state.queued !== queued) {
      // First sight, or the run just left the queue: the clock restarts,
      // and the bar carries on from where it stands.
      const elapsed = Number(root.dataset.elapsed) || 0;
      state.origin = performance.now() - elapsed * 1000;
      state.queued = queued;
      runs.set(id, state);
    }
    return state;
  }

  function show(root, instant) {
    // Only while it plays: a finished fragment carries no pace, and its real
    // number is the last word.
    if (!root.dataset.expected) return;
    const state = stateFor(root);
    const seconds = (performance.now() - state.origin) / 1000;
    const real = Number(root.dataset.pct) || 0;
    const expected = Number(root.dataset.expected);
    const guess = Math.min(state.queued ? QUEUED_CEILING : CEILING, curve(seconds, expected));
    state.shown = Math.min(CEILING, Math.max(state.shown, real, guess));

    const fill = root.querySelector("[data-run-fill]");
    const label = root.querySelector("[data-run-label]");
    if (instant) {
      // A fresh fragment arrives drawn at the real share. Jump it to the
      // shown value before it is painted, without the glide, or it would
      // visibly slide back and forth on every swap.
      fill.classList.add("is-instant");
      fill.style.width = `${state.shown.toFixed(1)}%`;
      void fill.offsetWidth;
      fill.classList.remove("is-instant");
    } else {
      fill.style.width = `${state.shown.toFixed(1)}%`;
    }
    // The page's language places the sign: "42%" in English, "42 %" in German.
    label.textContent = new Intl.NumberFormat(document.documentElement.lang, {
      style: "percent", maximumFractionDigits: 0,
    }).format(Math.floor(state.shown) / 100);
  }

  function adopt(scope) {
    for (const root of scope.querySelectorAll("[data-run-progress]")) {
      show(root, true);
    }
  }

  function rotate(list) {
    const items = Array.from(list.children);
    let current = items.findIndex((item) => item.classList.contains("is-current"));
    let bag = [];
    const next = () => {
      if (!bag.length) {
        // Every line but the one on screen, shuffled (Fisher-Yates).
        bag = items.map((_, index) => index).filter((index) => index !== current);
        for (let i = bag.length - 1; i > 0; i -= 1) {
          const j = Math.floor(Math.random() * (i + 1));
          [bag[i], bag[j]] = [bag[j], bag[i]];
        }
      }
      items[current].classList.remove("is-current");
      current = bag.pop();
      items[current].classList.add("is-current");
      window.setTimeout(next, 3000 + Math.random() * 2000);
    };
    window.setTimeout(next, 3000 + Math.random() * 2000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    adopt(document);
    // Fired by the new fragment itself, before the browser paints it.
    document.body.addEventListener("htmx:afterSwap", () => adopt(document));
    window.setInterval(() => {
      for (const root of document.querySelectorAll("[data-run-progress]")) {
        show(root, false);
      }
    }, TICK_MS);

    const list = document.querySelector("[data-run-lines]");
    if (list && list.children.length > 1 && !still.matches) rotate(list);
  });
})();
