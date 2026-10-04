// The ticker on the home page (phase 11 C). htmx asks for its lines every
// 30 s; this file adds the two things htmx does not:
//
// Pause. Content that updates by itself needs a way to stop it (WCAG 2.2.2).
// The button is hidden until this file runs, since without it nothing
// updates. While pressed, every poll is cancelled at htmx:confirm.
//
// Fresh lines. After a swap, a line whose key was not on the page before
// gets `ticker-fresh`, which fades it in (not under reduced motion, see
// input.css).
(() => {
  "use strict";

  const box = document.querySelector("[data-ticker]");
  const button = box && box.querySelector("[data-ticker-pause]");
  if (!button) return;

  let paused = false;
  let seen = new Set();

  const keys = () =>
    new Set(Array.from(box.querySelectorAll("li[data-key]"), (li) => li.dataset.key));

  button.hidden = false;
  button.addEventListener("click", () => {
    paused = !paused;
    button.setAttribute("aria-pressed", String(paused));
    // Both words come from the page, in its language (phase 12).
    button.textContent = paused ? button.dataset.resume : button.dataset.pause;
  });

  box.addEventListener("htmx:confirm", (event) => {
    if (paused) event.preventDefault();
  });
  box.addEventListener("htmx:beforeSwap", () => {
    seen = keys();
  });
  box.addEventListener("htmx:afterSwap", () => {
    box.querySelectorAll("li[data-key]").forEach((li) => {
      if (!seen.has(li.dataset.key)) li.classList.add("ticker-fresh");
    });
  });
})();
