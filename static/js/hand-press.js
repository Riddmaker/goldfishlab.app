// A card in hand on a touch screen (B-09): a finger that rests on it shows
// the card big, and lifting the finger puts it back. Only a short tap plays
// the card: the click that follows a long press is swallowed. A finger that
// moves is scrolling the hand, so it shows nothing. Mouse and pen are left
// alone; with a mouse the raised card already grows (assets/css/input.css).
//
// pressKind is the rule, kept pure so tests/test_hand_press.py runs it under Node.
(() => {
  "use strict";

  const HOLD_MS = 350;
  const SLOP_PX = 10;

  // {held, moved} in ms and px -> "peek" | "tap" | "scroll"
  function pressKind(press) {
    if (press.moved > SLOP_PX) return "scroll";
    return press.held >= HOLD_MS ? "peek" : "tap";
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { pressKind, HOLD_MS, SLOP_PX };
    return;
  }

  let press = null;
  let peek = null;
  let swallow = false;

  function show(img) {
    peek = document.createElement("div");
    peek.className = "hand-peek";
    peek.setAttribute("aria-hidden", "true");
    const big = document.createElement("img");
    big.src = img.currentSrc || img.src;
    big.alt = "";
    peek.append(big);
    document.body.append(peek);
  }

  function end() {
    if (press) clearTimeout(press.timer);
    press = null;
    if (peek) peek.remove();
    peek = null;
  }

  document.addEventListener("pointerdown", (event) => {
    end();
    swallow = false;
    if (event.pointerType !== "touch") return;
    const card = event.target.closest(".hand-card");
    const img = card && card.querySelector("img.hand-card-img");
    if (!img) return;
    press = { x: event.clientX, y: event.clientY, start: event.timeStamp };
    press.timer = setTimeout(() => {
      if (!press) return;
      show(img);
      swallow = true;
    }, HOLD_MS);
  }, { passive: true });

  document.addEventListener("pointermove", (event) => {
    if (!press || peek) return;
    const moved = Math.hypot(event.clientX - press.x, event.clientY - press.y);
    if (pressKind({ held: event.timeStamp - press.start, moved }) === "scroll") end();
  }, { passive: true });

  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);

  document.addEventListener("click", (event) => {
    if (!swallow) return;
    swallow = false;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  // A long press would otherwise open the phone's own menu for the picture.
  document.addEventListener("contextmenu", (event) => {
    if (event.target.closest && event.target.closest(".hand-card")) event.preventDefault();
  });
})();
