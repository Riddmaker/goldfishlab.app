// The goldfish in the big flask on the home page (phase 10 T1.4, T1.6; in the
// header until Paket 4, PR 17). An enhancement only: without this file the
// flask is a still picture.
//
// The button. The script makes the flask a button named "Poke the goldfish"
// (the name comes from the page, in its language), so a keyboard can poke it
// too: Enter and Space are clicks.
//
// Idle. After 20 to 30 seconds without input the fish makes one short move,
// then another every 25 to 60 seconds; mouse, scroll, keys and touch start the
// wait again. It glances about (head turn, eye wide) about 60 % of the time,
// turns round about 30 %, jumps out of the flask about 10 %. The first move
// is always the glance, two jumps never follow each other, and a page view
// gets at most six. A hidden tab waits, and starts afresh when it is back.
//
// Hello. Two seconds after the page loads the fish jumps out of the flask
// once, so the page greets with some life. Not an idle move: it does not
// count towards the six, and the first idle move is still the glance.
//
// Hover. A mouse coming over the flask makes the fish jump, unless a move is
// already playing. Touch has no hover; it has the poke.
//
// Poke. The flask tilts, the water sloshes and the fish looks about. Three
// pokes within 1.5 seconds and the fish jumps out and back.
//
// Reduced motion. No hello jump, no idle moves, no hover jump; a poke only
// widens the eye for a moment.
//
// nextWait, nextMove and poke are the rules, kept pure so tests/test_fish.py
// can check them under Node without a browser.
(() => {
  "use strict";

  const MAX_IDLE_MOVES = 6;
  const MOVE_MS = 1800; // longer than every move in input.css (all under 2 s)
  const TRIPLE_MS = 1500;
  const HELLO_MS = 2000;
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

  // A poke at `now` after the earlier ones in `pokes` (times in ms): the
  // pokes still counting, and whether this one made three within TRIPLE_MS.
  // After a triple the count starts again.
  function poke(pokes, now) {
    const counting = pokes.filter((time) => now - time < TRIPLE_MS).concat(now);
    return counting.length >= 3 ? { pokes: [], triple: true } : { pokes: counting, triple: false };
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { nextWait, nextMove, poke, MAX_IDLE_MOVES, TRIPLE_MS };
  }
  if (typeof document === "undefined") return;

  const still = window.matchMedia("(prefers-reduced-motion: reduce)");

  function start() {
    const tank = document.querySelector("[data-fish]");
    const logo = tank && tank.querySelector(".logo");
    if (!logo) return;

    const flask = document.createElement("button");
    flask.type = "button";
    flask.className = tank.className;
    flask.setAttribute("data-fish", "");
    flask.setAttribute("aria-label", tank.dataset.label);
    flask.append(...tank.childNodes);
    tank.replaceWith(flask);

    let playing = null; // the timer that ends the move on screen
    let idleTimer = null;
    let idleMoves = 0;
    let previous;
    let pokes = [];

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

    flask.addEventListener("click", () => {
      if (still.matches) {
        play("gaze");
        return;
      }
      const result = poke(pokes, performance.now());
      pokes = result.pokes;
      if (result.triple) play("jump");
      else play("tilt", "gaze");
    });

    flask.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "mouse" && !still.matches && playing === null) play("jump");
    });

    for (const type of INPUT) {
      window.addEventListener(type, wait, { passive: true });
    }
    document.addEventListener("visibilitychange", wait);
    wait();

    if (!still.matches) {
      window.setTimeout(() => {
        if (playing === null && !document.hidden) play("jump");
      }, HELLO_MS);
    }
  }

  document.addEventListener("DOMContentLoaded", start);
})();
