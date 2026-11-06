// The ticker on the home page (phase 11 C; Paket 4, PR 16). The page holds
// every line - up to forty events, a fun line after every seven - and the
// stylesheet shows the first eight. Without this file those eight stand, and
// htmx replaces them every 30 s. With it:
//
// The loop. Every five seconds the last line moves to the top: it comes in
// at the top and the eighth goes out at the bottom, round and round. Only
// with more lines than are shown, and not under reduced motion.
//
// The poll. htmx asks for the lines every 30 s. The answer is merged into
// the loop instead of starting it over: an event not seen before goes to the
// top, one no longer sent goes, fun lines stay where they are (each answer
// picks its own) unless one would come sooner than after seven events.
//
// Holding still. Content that moves by itself needs a way to stop it (WCAG
// 2.2.2): the Pause button stops the loop, the poll and the overlord; a
// pointer resting on the band or the focus inside it holds loop and poll.
//
// Arriving. A line that comes in fades in and its symbol moves once
// (`ticker-fresh`, `icon-now`; not under reduced motion, see input.css).
//
// merge is the rule, kept pure so tests/test_ticker.py can run it under Node.
(() => {
  "use strict";

  const STEP_MS = 5000;
  const SHOWN = 8;
  const FUN_EVERY = 8;
  const ARRIVE_MS = 1800; // longer than every arriving move in input.css

  // Lines as {key, fun}, top first. Returns the merged list in the same form.
  function merge(current, incoming, every = FUN_EVERY) {
    const sent = new Set(incoming.filter((line) => !line.fun).map((line) => line.key));
    const have = new Set(current.map((line) => line.key));
    const fresh = incoming.filter((line) => !line.fun && !have.has(line.key));
    let lines = fresh.concat(current.filter((line) => line.fun || sent.has(line.key)));
    // A fun line needs seven events before it, counted round the loop.
    let dropped = true;
    while (dropped) {
      dropped = false;
      const funs = lines.filter((line) => line.fun).length;
      for (let at = 0; at < lines.length && funs; at += 1) {
        if (!lines[at].fun) continue;
        let gap = 0;
        for (let back = 1; back < lines.length; back += 1) {
          const line = lines[(at - back + lines.length) % lines.length];
          if (line.fun) break;
          gap += 1;
        }
        if (gap < every - 1) {
          lines = lines.slice(0, at).concat(lines.slice(at + 1));
          dropped = true;
          break;
        }
      }
    }
    return lines;
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { merge, FUN_EVERY };
    return;
  }

  const box = document.querySelector("[data-ticker]");
  const button = box && box.querySelector("[data-ticker-pause]");
  if (!button) return;

  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  let paused = false;
  let held = false;

  const list = () => box.querySelector("#ticker-lines");
  const keyed = (ul) => Array.from(ul.querySelectorAll(":scope > li[data-key]"));
  const shape = (li) => ({ key: li.dataset.key, fun: li.hasAttribute("data-fun") });

  const arrive = (li) => {
    li.classList.add("ticker-fresh", "icon-now");
    window.setTimeout(() => li.classList.remove("ticker-fresh", "icon-now"), ARRIVE_MS);
  };

  button.hidden = false;
  button.addEventListener("click", () => {
    paused = !paused;
    button.setAttribute("aria-pressed", String(paused));
    box.classList.toggle("is-paused", paused);
    // Both words come from the page, in its language (phase 12).
    button.textContent = paused ? button.dataset.resume : button.dataset.pause;
  });

  const hold = (on) => () => {
    held = on;
  };
  box.addEventListener("mouseenter", hold(true));
  box.addEventListener("mouseleave", hold(false));
  box.addEventListener("focusin", hold(true));
  box.addEventListener("focusout", (event) => {
    if (!box.contains(event.relatedTarget)) held = false;
  });

  box.addEventListener("htmx:confirm", (event) => {
    if (paused || held) event.preventDefault();
  });

  box.addEventListener("htmx:beforeSwap", (event) => {
    const ul = list();
    const answer = document.createElement("template");
    answer.innerHTML = event.detail.serverResponse;
    const next = answer.content.querySelector("#ticker-lines");
    const mine = keyed(ul);
    // A quiet list on either side: let htmx swap it whole.
    if (!next || !mine.length || !keyed(next).length) return;
    event.detail.shouldSwap = false;
    const nodes = new Map(mine.map((li) => [li.dataset.key, li]));
    const sent = keyed(next);
    sent.forEach((li) => {
      if (!nodes.has(li.dataset.key)) nodes.set(li.dataset.key, li);
    });
    const order = merge(mine.map(shape), sent.map(shape));
    ul.replaceChildren(...order.map((line) => nodes.get(line.key)));
    sent.filter((li) => li.isConnected).forEach(arrive);
  });

  window.setInterval(() => {
    const ul = list();
    if (paused || held || still.matches || document.hidden || !ul) return;
    const lines = keyed(ul);
    if (lines.length <= SHOWN) return;
    const last = lines[lines.length - 1];
    ul.prepend(last);
    arrive(last);
  }, STEP_MS);
})();
