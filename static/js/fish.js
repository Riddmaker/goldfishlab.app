// The goldfish in the header logo, on the home page (phase 10 T1.4, T1.6).
// An enhancement only: without this file the logo is a still picture and
// a plain link home.
//
// Idle. After 20 to 30 seconds without input the fish makes one short move,
// then another every 25 to 60 seconds; mouse, scroll, keys and touch start the
// wait again. It glances about (head turn, eye wide) about 60 % of the time,
// turns round about 30 %, jumps out of the flask about 10 %. The first move
// is always the glance, two jumps never follow each other, and a page view
// gets at most six. A hidden tab waits, and starts afresh when it is back.
//
// Click. The link points at the page it is on, so a click does not reload:
// the flask tilts, the water sloshes and the fish looks about. Three clicks
// within 1.5 seconds and the fish jumps out and back.
//
// Reduced motion. No idle moves; a click only widens the eye for a moment.
//
// nextWait and nextMove are the rules, kept pure so tests/test_fish.py can
// check them under Node without a browser.
(() => {
  "use strict";

  const MAX_IDLE_MOVES = 6;
  const MOVE_MS = 1800; // longer than every move in input.css (all under 2 s)
  const TRIPLE_MS = 1500;
  const INPUT = ["pointermove", "pointerdown", "scroll", "keydown", "touchstart", "wheel"];

  // Milliseconds until the next idle move.
  function nextWait(random, first) {
    return first ? 20000 + random() * 10000 : 25000 + random() * 35000;
  }

  // The next idle move after `previous` (undefined before the first).
  function nextMove(random, previous) {
    if (!previous) return "gaze";
    // After a jump the roll is drawn from gaze and turn only, same odds.
    const roll = random() * (previous === "jump" ? 0.9 : 1);
    if (roll < 0.6) return "gaze";
    if (roll < 0.9) return "turn";
    return "jump";
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { nextWait, nextMove, MAX_IDLE_MOVES };
  }
  if (typeof document === "undefined") return;

  const still = window.matchMedia("(prefers-reduced-motion: reduce)");

  function start() {
    const link = document.querySelector("[data-fish-link]");
    const logo = link && link.querySelector(".logo");
    if (!logo) return;

    let playing = null; // the timer that ends the move on screen
    let idleTimer = null;
    let idleMoves = 0;
    let previous;
    let clicks = [];

    function play(...moves) {
      window.clearTimeout(playing);
      logo.classList.remove("is-gaze", "is-turn", "is-jump", "is-tilt");
      void logo.getBoundingClientRect(); // restart a move that is replayed
      logo.classList.add(...moves.map((move) => `is-${move}`));
      playing = window.setTimeout(() => {
        logo.classList.remove(...moves.map((move) => `is-${move}`));
        playing = null;
      }, MOVE_MS);
    }

    function wait() {
      window.clearTimeout(idleTimer);
      idleTimer = null;
      if (still.matches || document.hidden || idleMoves >= MAX_IDLE_MOVES) return;
      idleTimer = window.setTimeout(idle, nextWait(Math.random, idleMoves === 0));
    }

    function idle() {
      if (playing === null) {
        previous = nextMove(Math.random, previous);
        idleMoves += 1;
        play(previous);
      }
      wait();
    }

    link.addEventListener("click", (event) => {
      // A new tab or window is still the link's to open.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      event.preventDefault();
      if (still.matches) {
        play("gaze");
        return;
      }
      const now = performance.now();
      clicks = clicks.filter((time) => now - time < TRIPLE_MS).concat(now);
      if (clicks.length >= 3) {
        clicks = [];
        play("jump");
      } else {
        play("tilt", "gaze");
      }
    });

    for (const type of INPUT) {
      window.addEventListener(type, wait, { passive: true });
    }
    document.addEventListener("visibilitychange", wait);
    wait();
  }

  document.addEventListener("DOMContentLoaded", start);
})();
